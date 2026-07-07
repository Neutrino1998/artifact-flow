#!/usr/bin/env python3
"""Inspect a .pptx file into a compact JSON summary.

Usage:
    python inspect_deck.py input.pptx [--max-text 600] [--include-notes]

The sandbox has no slide renderer. This script gives a structural view instead:
slide order, layout names, text blocks, placeholders, tables, images, charts,
group children, and shape boxes in inches. Use it before editing an existing deck
or template.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

EMU_PER_INCH = 914400


def _inch(value) -> float:
    return round(int(value) / EMU_PER_INCH, 3)


def _shape_type(shape) -> str:
    if getattr(shape, "has_table", False):
        return "table"
    if getattr(shape, "has_chart", False):
        return "chart"
    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        return "image"
    if getattr(shape, "has_text_frame", False):
        return "text"
    name = getattr(shape.shape_type, "name", None)
    return name.lower() if name else str(shape.shape_type)


def _text(shape, max_text: int) -> str:
    if not getattr(shape, "has_text_frame", False):
        return ""
    text = "\n".join(p.text for p in shape.text_frame.paragraphs).strip()
    if max_text > 0 and len(text) > max_text:
        return text[:max_text] + "...(truncated)"
    return text


def _placeholder(shape):
    if not getattr(shape, "is_placeholder", False):
        return None
    try:
        fmt = shape.placeholder_format
        return {
            "idx": fmt.idx,
            "type": getattr(fmt.type, "name", str(fmt.type)),
        }
    except Exception:  # noqa: BLE001 - malformed decks should still inspect best-effort.
        return {"idx": None, "type": "unknown"}


def _table(shape, max_text: int):
    if not getattr(shape, "has_table", False):
        return None
    rows = []
    table = shape.table
    for row in table.rows:
        rows.append([
            (cell.text or "")[:max_text] if max_text > 0 else (cell.text or "")
            for cell in row.cells
        ])
    return {
        "rows": len(table.rows),
        "cols": len(table.columns),
        "sample": rows[:5],
    }


def _shape_summary(shape, max_text: int) -> dict:
    item = {
        "name": getattr(shape, "name", ""),
        "type": _shape_type(shape),
        "box": {
            "x": _inch(shape.left),
            "y": _inch(shape.top),
            "w": _inch(shape.width),
            "h": _inch(shape.height),
        },
    }
    placeholder = _placeholder(shape)
    if placeholder is not None:
        item["placeholder"] = placeholder
    text = _text(shape, max_text)
    if text:
        item["text"] = text
    table = _table(shape, max_text)
    if table is not None:
        item["table"] = table
    child_shapes = getattr(shape, "shapes", None)
    if child_shapes is not None:
        children = [_shape_summary(child, max_text) for child in child_shapes]
        item["child_count"] = len(children)
        if children:
            item["children"] = children
    return item


def inspect(path: Path, *, max_text: int, include_notes: bool) -> dict:
    prs = Presentation(str(path))
    slides = []
    for idx, slide in enumerate(prs.slides, 1):
        slide_item = {
            "index": idx,
            "layout": getattr(slide.slide_layout, "name", ""),
            "shapes": [_shape_summary(shape, max_text) for shape in slide.shapes],
        }
        if include_notes and getattr(slide, "has_notes_slide", False):
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                slide_item["notes"] = notes[:max_text] if max_text > 0 else notes
        slides.append(slide_item)
    return {
        "file": str(path),
        "slide_count": len(slides),
        "page_size": {"w": _inch(prs.slide_width), "h": _inch(prs.slide_height)},
        "slides": slides,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx")
    parser.add_argument("--max-text", type=int, default=600)
    parser.add_argument("--include-notes", action="store_true")
    args = parser.parse_args()

    path = Path(args.pptx)
    if not path.is_file():
        raise SystemExit(f"error: not a file: {path}")
    data = inspect(path, max_text=args.max_text, include_notes=args.include_notes)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
