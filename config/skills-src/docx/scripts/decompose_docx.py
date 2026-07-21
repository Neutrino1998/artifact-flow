#!/usr/bin/env python3
"""Decompose a DOCX into semantic text, visible figures, and table records.

This is intentionally a best-effort reader for common Word documents, not a
Word layout engine.  It materializes only image presentations whose visible
pixels can be recovered safely.  Ambiguous presentations are marked for a
page-render fallback in the manifest.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import posixpath
import shutil
import subprocess
import tempfile
import zipfile
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Any

from lxml import etree
from PIL import Image, ImageOps, UnidentifiedImageError


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
V_NS = "urn:schemas-microsoft-com:vml"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

NS = {
    "w": W_NS,
    "a": A_NS,
    "pic": PIC_NS,
    "r": R_NS,
    "wp": WP_NS,
    "v": V_NS,
}

MAX_FIGURES = 500
MAX_TABLES = 500
MAX_TABLE_CELLS = 100_000
MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
# A cropped RGBA image can briefly coexist as source, crop, and normalized buffers
# inside the 1 GiB no-swap sandbox.  Keep enough headroom for Pillow and Python.
MAX_IMAGE_PIXELS = 40_000_000
MAX_REACHABLE_XML_PARTS = 1_000
PANDOC_TIMEOUT_SECONDS = 120
VECTOR_TIMEOUT_SECONDS = 60

RASTER_SUFFIXES = {
    ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp",
}
VECTOR_SUFFIXES = {".emf", ".svg", ".wmf"}
RELATIONSHIP_PART_ROLES = {
    "header": "header",
    "footer": "footer",
    "footnotes": "footnote",
    "endnotes": "endnote",
    "comments": "comment",
}


class DecomposeError(RuntimeError):
    """A loud, user-actionable decomposition failure."""


class ResourceLimitError(DecomposeError):
    """A resource bound was exceeded and must not be silently downgraded."""


def _xml(data: bytes) -> etree._Element:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        remove_blank_text=False,
    )
    return etree.fromstring(data, parser=parser)


def _json_write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_package(zf: zipfile.ZipFile) -> None:
    total = 0
    media_count = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise DecomposeError(f"DOCX contains unsafe member path: {name}")
        total += info.file_size
        if total > MAX_PACKAGE_BYTES:
            raise DecomposeError(
                f"DOCX uncompressed size exceeds {MAX_PACKAGE_BYTES} bytes"
            )
        if name.startswith("word/media/"):
            media_count += 1
            if info.file_size > MAX_MEDIA_BYTES:
                raise DecomposeError(
                    f"embedded media exceeds {MAX_MEDIA_BYTES} bytes: {name}"
                )
    if media_count > MAX_FIGURES:
        raise DecomposeError(
            f"DOCX contains {media_count} media files; limit is {MAX_FIGURES}"
        )
    if "word/document.xml" not in zf.namelist():
        raise DecomposeError("DOCX is missing word/document.xml")


def _part_relationships(
    zf: zipfile.ZipFile, part: str
) -> dict[str, dict[str, str | bool]]:
    part_path = PurePosixPath(part)
    rels_path = str(
        part_path.parent / "_rels" / f"{part_path.name}.rels"
    )
    if rels_path not in zf.namelist():
        return {}
    root = _xml(zf.read(rels_path))
    relationships: dict[str, dict[str, str | bool]] = {}
    for rel in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        if not rel_id or not target:
            continue
        external = rel.get("TargetMode") == "External"
        resolved = target
        if not external:
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(part), target)
            )
            if resolved.startswith("../") or resolved.startswith("/"):
                resolved = ""
        relationships[rel_id] = {
            "target": resolved,
            "external": external,
            "type": rel.get("Type", ""),
        }
    return relationships


def _paragraph_text(paragraph: etree._Element) -> str:
    nodes = paragraph.xpath(
        ".//*[self::w:t or self::w:tab or self::w:br or self::w:cr]"
        "[not(ancestor::w:del) and not(ancestor::w:moveFrom)]",
        namespaces=NS,
    )
    values: list[str] = []
    for node in nodes:
        local_name = etree.QName(node).localname
        if local_name == "tab":
            values.append("\t")
        elif local_name in {"br", "cr"}:
            values.append("\n")
        else:
            values.append(node.text or "")
    return "".join(values).strip()


def _paragraph_style(paragraph: etree._Element) -> str | None:
    values = paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)
    return values[0] if values else None


def _heading_level(style: str | None) -> int | None:
    if not style:
        return None
    lowered = style.casefold()
    if not (lowered.startswith("heading") or lowered.startswith("标题")):
        return None
    digits = "".join(char for char in style if char.isdigit())
    return max(1, min(int(digits or "1"), 9))


def _paragraph_context(
    root: etree._Element,
) -> dict[etree._Element, dict[str, Any]]:
    paragraphs = root.xpath(".//w:p", namespaces=NS)
    texts = [_paragraph_text(paragraph) for paragraph in paragraphs]
    next_nonempty: list[str | None] = [None] * len(texts)
    next_text: str | None = None
    for index in range(len(texts) - 1, -1, -1):
        next_nonempty[index] = next_text
        if texts[index]:
            next_text = texts[index]
    previous_nonempty: str | None = None
    heading_stack: list[str | None] = [None] * 9
    records: dict[etree._Element, dict[str, Any]] = {}
    for index, paragraph in enumerate(paragraphs):
        text = texts[index]
        style = _paragraph_style(paragraph)
        level = _heading_level(style)
        if level and text:
            heading_stack[level - 1] = text
            heading_stack[level:] = [None] * (9 - level)
        records[paragraph] = {
            "text": text or None,
            "style": style,
            "heading_path": [item for item in heading_stack if item],
            "before": previous_nonempty,
            "after": next_nonempty[index],
        }
        if text:
            previous_nonempty = text
    return records


def _truthy(value: str | None) -> bool:
    return bool(value and value.casefold() not in {"0", "false", "off", "no"})


def _int_attr(element: etree._Element | None, name: str) -> int:
    if element is None:
        return 0
    try:
        return int(element.get(name, "0"))
    except ValueError:
        return 0


def _presentation(
    node: etree._Element,
) -> tuple[dict[str, int], list[str], dict[str, int] | None]:
    crop = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    reasons: list[str] = []
    extent: dict[str, int] | None = None

    inline = node.xpath("ancestor::wp:inline[1] | ancestor::wp:anchor[1]", namespaces=NS)
    if inline:
        extents = inline[0].xpath("./wp:extent[1]", namespaces=NS)
        if extents:
            extent = {
                "cx": _int_attr(extents[0], "cx"),
                "cy": _int_attr(extents[0], "cy"),
            }

    if etree.QName(node).namespace == V_NS:
        return crop, ["vml_image"], extent

    pictures = node.xpath("ancestor::pic:pic[1]", namespaces=NS)
    if not pictures:
        return crop, ["unsupported_drawing_container"], extent
    picture = pictures[0]

    source_rects = picture.xpath(".//a:srcRect[1]", namespaces=NS)
    if source_rects:
        source_rect = source_rects[0]
        crop = {
            "left": _int_attr(source_rect, "l"),
            "top": _int_attr(source_rect, "t"),
            "right": _int_attr(source_rect, "r"),
            "bottom": _int_attr(source_rect, "b"),
        }
    if any(value < 0 or value > 100_000 for value in crop.values()):
        reasons.append("unsupported_crop_range")
    if crop["left"] + crop["right"] >= 100_000:
        reasons.append("invalid_horizontal_crop")
    if crop["top"] + crop["bottom"] >= 100_000:
        reasons.append("invalid_vertical_crop")

    transforms = picture.xpath("./pic:spPr/a:xfrm[1]", namespaces=NS)
    if transforms:
        transform = transforms[0]
        if _int_attr(transform, "rot"):
            reasons.append("rotation")
        if _truthy(transform.get("flipH")) or _truthy(transform.get("flipV")):
            reasons.append("flip")

    if picture.xpath("./pic:spPr/a:custGeom", namespaces=NS):
        reasons.append("custom_geometry")
    geometries = picture.xpath("./pic:spPr/a:prstGeom[1]", namespaces=NS)
    if geometries and geometries[0].get("prst", "rect") != "rect":
        reasons.append("non_rectangular_geometry")
    if picture.xpath(".//a:tile", namespaces=NS):
        reasons.append("tiled_fill")
    fill_rects = picture.xpath(".//a:stretch/a:fillRect[1]", namespaces=NS)
    if fill_rects and any(_int_attr(fill_rects[0], name) for name in ("l", "t", "r", "b")):
        reasons.append("fill_rect_transform")

    for child in node:
        if etree.QName(child).localname != "extLst":
            reasons.append("image_effects")
            break
    if node.xpath("ancestor::*[local-name()='grpSp' or local-name()='wgp']"):
        reasons.append("group_transform")
    return crop, list(dict.fromkeys(reasons)), extent


def _check_image_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ResourceLimitError(
            f"decoded image dimensions exceed limit: {width}x{height}"
        )


def _open_vector(data: bytes, suffix: str) -> Image.Image:
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        raise DecomposeError("LibreOffice is unavailable for vector conversion")
    with tempfile.TemporaryDirectory(prefix="docx-vector-") as temp_name:
        temp_dir = Path(temp_name)
        source = temp_dir / f"source{suffix}"
        profile = temp_dir / "profile"
        source.write_bytes(data)
        command = [
            office,
            "--headless",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            "png",
            "--outdir",
            str(temp_dir),
            str(source),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=VECTOR_TIMEOUT_SECONDS,
            check=False,
        )
        converted = temp_dir / "source.png"
        if result.returncode or not converted.is_file():
            detail = (result.stderr or result.stdout).strip()[-300:]
            raise DecomposeError(detail or "LibreOffice did not produce a PNG")
        try:
            with Image.open(converted) as image:
                _check_image_dimensions(image)
                return image.copy()
        except Image.DecompressionBombError as exc:
            raise ResourceLimitError(
                "converted vector exceeds Pillow's pixel safety limit"
            ) from exc


def _open_media(data: bytes, suffix: str) -> Image.Image:
    if suffix in VECTOR_SUFFIXES:
        return _open_vector(data, suffix)
    if suffix in RASTER_SUFFIXES:
        try:
            with Image.open(io.BytesIO(data)) as opened:
                _check_image_dimensions(opened)
                opened.seek(0)
                return ImageOps.exif_transpose(opened)
        except Image.DecompressionBombError as exc:
            raise ResourceLimitError(
                "decoded image exceeds Pillow's pixel safety limit"
            ) from exc
    raise DecomposeError(f"unsupported embedded image format: {suffix or '(none)'}")


def _materialize_visible_image(
    data: bytes,
    suffix: str,
    crop: dict[str, int],
    output: Path,
) -> str:
    source_image = _open_media(data, suffix)
    visible_image = source_image
    try:
        width, height = source_image.size
        left = round(width * crop["left"] / 100_000)
        top = round(height * crop["top"] / 100_000)
        right = width - round(width * crop["right"] / 100_000)
        bottom = height - round(height * crop["bottom"] / 100_000)
        if left or top or right != width or bottom != height:
            visible_image = source_image.crop((left, top, right, bottom))
        if visible_image.mode in {"RGB", "RGBA"}:
            normalized = visible_image
        else:
            has_alpha = (
                visible_image.mode == "LA"
                or "transparency" in visible_image.info
            )
            normalized = visible_image.convert("RGBA" if has_alpha else "RGB")
        try:
            normalized.save(output, format="PNG", optimize=True)
        finally:
            if normalized is not visible_image:
                normalized.close()
    finally:
        if visible_image is not source_image:
            visible_image.close()
        source_image.close()
    if suffix in VECTOR_SUFFIXES:
        return "cropped_rasterized_vector" if any(crop.values()) else "rasterized_vector"
    return "cropped" if any(crop.values()) else "embedded"


def _inspect_figures(
    zf: zipfile.ZipFile,
    parts: list[tuple[str, str, etree._Element]],
    output_dir: Path,
    warnings: list[str],
) -> list[dict[str, Any]]:
    figures_dir = output_dir / "figures"
    figures_dir.mkdir()
    figures: list[dict[str, Any]] = []
    for part, role, root in parts:
        relationships = _part_relationships(zf, part)
        candidates = root.xpath(".//a:blip | .//v:imagedata", namespaces=NS)
        if not candidates:
            continue
        context = _paragraph_context(root)
        body_block_orders = {
            block: index
            for index, block in enumerate(
                root.xpath(
                    "./w:body/*[self::w:p or self::w:tbl]", namespaces=NS
                ),
                start=1,
            )
        }
        for node in candidates:
            if len(figures) >= MAX_FIGURES:
                raise DecomposeError(f"image occurrence limit is {MAX_FIGURES}")
            figure_id = f"figure-{len(figures) + 1:03d}"
            is_vml = etree.QName(node).namespace == V_NS
            rel_id = node.get(f"{{{R_NS}}}id") if is_vml else node.get(f"{{{R_NS}}}embed")
            linked_id = None if is_vml else node.get(f"{{{R_NS}}}link")
            crop, reasons, extent = _presentation(node)
            if role == "other":
                reasons.append("unsupported_content_part")
            relationship = relationships.get(rel_id or linked_id or "")
            source_part: str | None = None
            if linked_id:
                reasons.append("external_image")
            elif not rel_id:
                reasons.append("missing_image_relationship")
            elif relationship is None:
                reasons.append("unresolved_image_relationship")
            elif relationship["external"]:
                reasons.append("external_image")
            else:
                source_part = str(relationship["target"])
                if not source_part or source_part not in zf.namelist():
                    reasons.append("missing_embedded_media")

            paragraphs = node.xpath("ancestor::w:p[1]", namespaces=NS)
            paragraph_element = paragraphs[0] if paragraphs else None
            paragraph_path = (
                root.getroottree().getpath(paragraph_element)
                if paragraph_element is not None
                else None
            )
            body_blocks = node.xpath(
                "ancestor::*[parent::w:body and (self::w:p or self::w:tbl)][1]",
                namespaces=NS,
            )
            block_order = body_block_orders.get(body_blocks[0]) if body_blocks else None
            paragraph = context.get(paragraph_element, {})
            suffix = Path(source_part or "").suffix.casefold()
            if source_part and suffix not in RASTER_SUFFIXES | VECTOR_SUFFIXES:
                reasons.append("unsupported_media_format")
            reasons = list(dict.fromkeys(reasons))

            visible_path: str | None = None
            display_mode: str | None = None
            if source_part and not reasons:
                destination = figures_dir / f"{figure_id}.png"
                try:
                    display_mode = _materialize_visible_image(
                        zf.read(source_part), suffix, crop, destination
                    )
                    visible_path = destination.relative_to(output_dir).as_posix()
                except ResourceLimitError:
                    raise
                except (
                    DecomposeError,
                    OSError,
                    UnidentifiedImageError,
                    subprocess.SubprocessError,
                ) as exc:
                    reasons.append("media_conversion_failed")
                    warnings.append(f"{figure_id}: {str(exc)[:300]}")

            figure = {
                "id": figure_id,
                "part": part,
                "part_role": role,
                "source_part": source_part,
                "visible_path": visible_path,
                "vision_ready": bool(visible_path),
                "fallback": None if visible_path else "page_required",
                "fallback_reasons": reasons,
                "display_mode": display_mode,
                "crop": crop,
                "display_extent_emu": extent,
                "paragraph_path": paragraph_path,
                "block_order": block_order,
                "context": {
                    "heading_path": paragraph.get("heading_path", []),
                    "before": paragraph.get("before"),
                    "current": paragraph.get("text"),
                    "after": paragraph.get("after"),
                },
                "likely_decorative": role in {"header", "footer"},
            }
            figures.append(figure)
    return figures


def _table_fallback_reasons(table: etree._Element) -> list[str]:
    reasons: list[str] = []
    if table.xpath(".//w:tbl", namespaces=NS):
        reasons.append("nested_table")
    revision_elements = {
        "ins", "del", "moveFrom", "moveTo", "cellIns", "cellDel", "cellMerge",
    }
    for element in table.iter():
        qname = etree.QName(element)
        if (
            qname.namespace == W_NS
            and (
                qname.localname in revision_elements
                or qname.localname.endswith("Change")
            )
        ):
            reasons.append("tracked_changes")
            break
    if table.xpath(
        ".//w:drawing | .//w:pict | .//w:object | .//w:altChunk"
        " | .//*[local-name()='oMath' or local-name()='oMathPara']",
        namespaces=NS,
    ):
        reasons.append("non_text_cell_content")
    return reasons


def _cell_record(cell: etree._Element, column: int) -> dict[str, Any]:
    spans = cell.xpath("./w:tcPr/w:gridSpan/@w:val", namespaces=NS)
    try:
        grid_span = max(1, int(spans[0])) if spans else 1
    except ValueError:
        grid_span = 1
    merges = cell.xpath("./w:tcPr/w:vMerge", namespaces=NS)
    vertical_merge = None
    if merges:
        vertical_merge = merges[0].get(f"{{{W_NS}}}val") or "continue"
    paragraphs = cell.xpath(
        ".//w:p[count(ancestor::w:tbl) = 1]", namespaces=NS
    )
    text = "\n".join(filter(None, (_paragraph_text(p) for p in paragraphs)))
    return {
        "column": column,
        "grid_span": grid_span,
        "vertical_merge": vertical_merge,
        "text": text,
    }


def _table_markdown(rows: list[list[dict[str, Any]]], columns: int) -> str:
    def escaped(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", "<br>")

    header = [f"column_{index}" for index in range(1, columns + 1)]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows:
        values: list[str] = []
        for cell in row:
            values.append(escaped(cell["text"]))
            values.extend([""] * (cell["grid_span"] - 1))
        values.extend([""] * (columns - len(values)))
        lines.append("| " + " | ".join(values[:columns]) + " |")
    return "\n".join(lines) + "\n"


def _extract_blocks_and_tables(
    root: etree._Element,
    figures: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    body_nodes = root.xpath("./w:body/*", namespaces=NS)
    tables_dir = output_dir / "tables"
    tables_dir.mkdir()
    blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    total_cells = 0
    previous_text: str | None = None
    figure_ids_by_block: dict[int, list[str]] = {}
    for figure in figures:
        if figure["part"] != "word/document.xml":
            continue
        block_order = figure.get("block_order")
        if block_order is not None:
            figure_ids_by_block.setdefault(block_order, []).append(figure["id"])

    for node in body_nodes:
        local_name = etree.QName(node).localname
        if local_name not in {"p", "tbl"}:
            continue
        block_order = len(blocks) + 1
        figure_ids = figure_ids_by_block.get(block_order, [])
        if local_name == "p":
            text = _paragraph_text(node)
            blocks.append({
                "order": block_order,
                "type": "paragraph",
                "text": text,
                "style": _paragraph_style(node),
                "figure_ids": figure_ids,
            })
            if text:
                previous_text = text
            continue

        if len(tables) >= MAX_TABLES:
            raise DecomposeError(f"table limit is {MAX_TABLES}")
        table_id = f"table-{len(tables) + 1:03d}"
        fallback_reasons = _table_fallback_reasons(node)
        if fallback_reasons:
            needs_page = any(
                reason in {"nested_table", "non_text_cell_content"}
                for reason in fallback_reasons
            )
            table = {
                "id": table_id,
                "structure_ready": False,
                "json_path": None,
                "markdown_path": None,
                "rows": None,
                "columns": None,
                "complex": None,
                "fallback": "page_required" if needs_page else "unsupported",
                "fallback_reasons": fallback_reasons,
                "context_before": previous_text,
                "figure_ids": figure_ids,
            }
        else:
            row_records: list[list[dict[str, Any]]] = []
            column_counts: list[int] = []
            complex_table = False
            for row in node.xpath("./w:tr", namespaces=NS):
                row_cells: list[dict[str, Any]] = []
                column = 1
                for cell in row.xpath("./w:tc", namespaces=NS):
                    total_cells += 1
                    if total_cells > MAX_TABLE_CELLS:
                        raise DecomposeError(
                            f"table cell limit is {MAX_TABLE_CELLS}"
                        )
                    record = _cell_record(cell, column)
                    row_cells.append(record)
                    column += record["grid_span"]
                    complex_table = complex_table or bool(
                        record["grid_span"] != 1
                        or record["vertical_merge"]
                    )
                row_records.append(row_cells)
                column_counts.append(column - 1)
            columns = max(column_counts, default=0)
            if len(set(column_counts)) > 1:
                complex_table = True

            json_path = tables_dir / f"{table_id}.json"
            markdown_path = tables_dir / f"{table_id}.md"
            table_data = {
                "id": table_id,
                "structure_ready": True,
                "rows": row_records,
                "row_count": len(row_records),
                "column_count": columns,
                "complex": complex_table,
            }
            _json_write(json_path, table_data)
            markdown_path.write_text(
                _table_markdown(row_records, columns), encoding="utf-8"
            )
            table = {
                "id": table_id,
                "structure_ready": True,
                "json_path": json_path.relative_to(output_dir).as_posix(),
                "markdown_path": markdown_path.relative_to(output_dir).as_posix(),
                "rows": len(row_records),
                "columns": columns,
                "complex": complex_table,
                "fallback": None,
                "fallback_reasons": [],
                "context_before": previous_text,
                "figure_ids": figure_ids,
            }
        tables.append(table)
        blocks.append({
            "order": block_order,
            "type": "table",
            "table_id": table_id,
            "figure_ids": figure_ids,
        })
    return blocks, tables


def _relationship_part_role(relationship_type: str) -> str:
    suffix = relationship_type.rsplit("/", 1)[-1]
    return RELATIONSHIP_PART_ROLES.get(suffix, "other")


def _discover_parts(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    names = set(zf.namelist())
    queue: deque[tuple[str, str]] = deque([
        ("word/document.xml", "body"),
    ])
    discovered: list[tuple[str, str]] = []
    visited: set[str] = set()
    while queue:
        part, role = queue.popleft()
        if part in visited:
            continue
        visited.add(part)
        discovered.append((part, role))
        if len(discovered) > MAX_REACHABLE_XML_PARTS:
            raise ResourceLimitError(
                f"reachable XML part limit is {MAX_REACHABLE_XML_PARTS}"
            )
        relationships = sorted(
            _part_relationships(zf, part).values(),
            key=lambda relationship: (
                str(relationship["target"]),
                str(relationship["type"]),
            ),
        )
        for relationship in relationships:
            if relationship["external"]:
                continue
            target = str(relationship["target"])
            if (
                target in names
                and target.startswith("word/")
                and target.casefold().endswith(".xml")
                and target not in visited
            ):
                queue.append((
                    target,
                    _relationship_part_role(str(relationship["type"])),
                ))
    return discovered


def _unsupported_content_warnings(root: etree._Element) -> list[str]:
    warnings: list[str] = []
    checks = {
        "SmartArt/diagram content requires page rendering":
            ".//*[local-name()='relIds' or local-name()='diagram']",
        "OLE content requires page rendering": ".//*[local-name()='OLEObject']",
        "Word canvas content may require page rendering":
            ".//*[local-name()='wpc' or local-name()='canvas']",
    }
    for message, expression in checks.items():
        if root.xpath(expression):
            warnings.append(message)
    return warnings


def inspect_docx(
    source: Path,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Inspect OOXML and write figure/table artifacts without invoking Pandoc."""
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(source) as zf:
            _validate_package(zf)
            parsed_parts = [
                (part, role, _xml(zf.read(part)))
                for part, role in _discover_parts(zf)
            ]
            for part, _, root in parsed_parts:
                warnings.extend(
                    f"{part}: {warning}"
                    for warning in _unsupported_content_warnings(root)
                )
            figures = _inspect_figures(zf, parsed_parts, output_dir, warnings)
            blocks, tables = _extract_blocks_and_tables(
                parsed_parts[0][2], figures, output_dir
            )
    except zipfile.BadZipFile as exc:
        raise DecomposeError(f"invalid DOCX package: {exc}") from exc
    return figures, blocks, tables, warnings


def _run_pandoc(source: Path, output_dir: Path) -> None:
    markdown = output_dir / "document.md"
    command = [
        "pandoc",
        "--track-changes=all",
        str(source),
        "-t",
        "gfm",
        "-o",
        str(markdown),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PANDOC_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DecomposeError("pandoc is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise DecomposeError(
            f"pandoc exceeded {PANDOC_TIMEOUT_SECONDS} seconds"
        ) from exc
    if result.returncode or not markdown.is_file():
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise DecomposeError(detail or "pandoc did not produce document.md")


def decompose_docx(source: Path, destination: Path) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise DecomposeError(f"input DOCX not found: {source}")
    if source.suffix.casefold() != ".docx":
        raise DecomposeError("input must be a .docx file")
    if destination.exists():
        raise DecomposeError(f"output path already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}-", dir=destination.parent
    ))
    try:
        _run_pandoc(source, temp_dir)
        figures, blocks, tables, warnings = inspect_docx(source, temp_dir)
        blocks_path = temp_dir / "blocks.jsonl"
        blocks_path.write_text(
            "".join(
                json.dumps(block, ensure_ascii=False) + "\n"
                for block in blocks
            ),
            encoding="utf-8",
        )
        manifest = {
            "schema_version": 2,
            "source": source.name,
            "text_path": "document.md",
            "blocks_path": "blocks.jsonl",
            "figures": figures,
            "tables": tables,
            "warnings": warnings,
            "counts": {
                "blocks": len(blocks),
                "figures": len(figures),
                "vision_ready_figures": sum(
                    1 for figure in figures if figure["vision_ready"]
                ),
                "page_fallback_figures": sum(
                    1 for figure in figures if not figure["vision_ready"]
                ),
                "tables": len(tables),
                "structure_ready_tables": sum(
                    1 for table in tables if table["structure_ready"]
                ),
                "table_fallbacks": sum(
                    1 for table in tables if not table["structure_ready"]
                ),
            },
        }
        _json_write(temp_dir / "manifest.json", manifest)
        os.replace(temp_dir, destination)
        return manifest
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decompose a DOCX into text, visible figures, and tables."
    )
    parser.add_argument("input", type=Path, help="source .docx")
    parser.add_argument("output_dir", type=Path, help="new output directory")
    args = parser.parse_args()
    try:
        manifest = decompose_docx(args.input, args.output_dir)
    except DecomposeError as exc:
        parser.exit(2, f"decompose_docx: {exc}\n")
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "manifest": str(args.output_dir / "manifest.json"),
        "counts": manifest["counts"],
        "warnings": len(manifest["warnings"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
