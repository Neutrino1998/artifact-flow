"""Import/export helpers for external tool seed bundles.

The live registry stores normalized DB rows, while config seeds live as
frontmatter Markdown. This module converts between those two shapes without
owning persistence. Import still flows through ``ToolRegistryManager.create``
so seeded-vs-dynamic ownership and collision checks stay in one place.
"""

from __future__ import annotations

import io
import posixpath
import re
import shutil
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List

import yaml

from db.models import ToolMember, ToolUnit
from reconcile.seeds import (
    SeedError,
    ToolUnitSeed,
    parse_mcp_seeds,
    parse_tool_seeds,
)
from utils.frontmatter import FrontmatterError, parse_frontmatter_text

MAX_SEED_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_SEED_ZIP_FILES = 64
MAX_SEED_FILE_BYTES = 256 * 1024

_ZIP_FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_ZIP_FIXED_MODE = 0o644
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ToolSeedBundleError(ValueError):
    """Bad seed upload/export shape."""


def export_unit_seed_bundle(unit: ToolUnit, members: Iterable[ToolMember]) -> bytes:
    """Render a tool unit as a deterministic config-seed zip bundle."""
    member_list = sorted(members, key=lambda m: m.full_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        if unit.kind == "mcp":
            cfg = unit.provider_config or {}
            fm = {
                "name": unit.name,
                "description": unit.description,
                "type": "mcp",
                "visibility": unit.visibility,
                "defer": unit.defer,
                "transport": cfg.get("transport", "streamable_http"),
                "url": cfg.get("url", ""),
                "headers": cfg.get("headers", {}) or {},
                "timeout": cfg.get("timeout", 60),
                "default_permission": cfg.get("default_permission", "confirm"),
            }
            _writestr(zf, f"mcp/{_safe_md_name(unit.name)}", _frontmatter_md(fm))
        elif unit.kind == "tool":
            if len(member_list) != 1:
                raise ToolSeedBundleError(f"singleton unit '{unit.name}' must have one member")
            member = member_list[0]
            fm = _member_frontmatter(member, fallback_name=unit.name)
            fm["visibility"] = unit.visibility
            fm["defer"] = unit.defer
            _writestr(zf, f"tools/{_safe_md_name(unit.name)}", _frontmatter_md(fm))
        elif unit.kind == "toolset":
            dirname = _safe_name(unit.name, fallback="toolset")
            set_fm = {
                "name": unit.name,
                "description": unit.description,
                "visibility": unit.visibility,
                "defer": unit.defer,
            }
            _writestr(zf, f"tools/{dirname}/_set.md", _frontmatter_md(set_fm))

            used_names: set[str] = {"_set.md"}
            for member in member_list:
                filename = _unique_member_filename(member.member_name, used_names)
                _writestr(
                    zf,
                    f"tools/{dirname}/{filename}",
                    _frontmatter_md(_member_frontmatter(member, fallback_name=member.member_name)),
                )
        else:
            raise ToolSeedBundleError(f"unsupported tool unit kind '{unit.kind}'")
    return buf.getvalue()


def parse_uploaded_seed_bundle(blob: bytes, filename: str) -> ToolUnitSeed:
    """Parse an uploaded seed bundle and require it to define exactly one unit."""
    if not blob:
        raise ToolSeedBundleError("uploaded seed file is empty")
    if len(blob) > MAX_SEED_UPLOAD_BYTES:
        raise ToolSeedBundleError(
            f"seed bundle must be <= {MAX_SEED_UPLOAD_BYTES // (1024 * 1024)}MB"
        )

    lower = filename.lower()
    with tempfile.TemporaryDirectory(prefix="artifactflow-tool-seed-") as tmp:
        root = Path(tmp)
        try:
            if lower.endswith(".md"):
                _stage_single_md(root, filename or "tool.md", blob)
            else:
                _extract_seed_zip(root, blob)

            seeds = _parse_staged_seeds(root)
        except UnicodeDecodeError as e:
            raise ToolSeedBundleError("seed Markdown files must be UTF-8") from e
        except OSError as e:
            raise ToolSeedBundleError(f"failed to read seed bundle: {e}") from e
    if len(seeds) != 1:
        raise ToolSeedBundleError(
            f"seed upload must contain exactly one tool unit; found {len(seeds)}"
        )
    return seeds[0]


def seed_to_create_spec(seed: ToolUnitSeed) -> Dict[str, Any]:
    """Convert a parsed config seed into the admin dynamic-create request shape."""
    if seed.kind == "mcp":
        cfg = seed.provider_config or {}
        return {
            "name": seed.name,
            "kind": "mcp",
            "description": seed.description,
            "visibility": seed.visibility,
            "defer": seed.defer,
            "members": [],
            "provider_config": {
                "transport": cfg["transport"],
                "url": cfg["url"],
                "headers": cfg.get("headers", {}) or {},
                "timeout": cfg.get("timeout", 60),
                "default_permission": cfg["default_permission"],
            },
        }

    return {
        "name": seed.name,
        "kind": seed.kind,
        "description": seed.description,
        "visibility": seed.visibility,
        "defer": seed.defer,
        "members": [
            {
                "member_name": m.member_name,
                "permission": m.permission,
                "description": m.definition.get("description", ""),
                "endpoint": m.definition.get("endpoint", ""),
                "method": m.definition.get("method", "GET"),
                "headers": m.definition.get("headers", {}) or {},
                "parameters": m.definition.get("parameters", []) or [],
                "response_extract": m.definition.get("response_extract"),
                "artifact_output": m.definition.get("artifact_output"),
                "timeout": m.definition.get("timeout", 60),
            }
            for m in seed.members
        ],
        "provider_config": None,
    }


def seed_bundle_filename(unit_name: str) -> str:
    return f"{_safe_name(unit_name, fallback='tool-unit')}-tool-seed.zip"


def _member_frontmatter(member: ToolMember, *, fallback_name: str) -> Dict[str, Any]:
    definition = member.definition or {}
    fm: Dict[str, Any] = {
        "name": member.member_name or fallback_name,
        "description": definition.get("description", ""),
        "type": "http",
        "permission": member.permission,
        "endpoint": definition.get("endpoint", ""),
        "method": definition.get("method", "GET"),
        "headers": definition.get("headers", {}) or {},
        "timeout": definition.get("timeout", 60),
    }
    if definition.get("response_extract"):
        fm["response_extract"] = definition["response_extract"]
    if definition.get("artifact_output"):
        fm["artifact_output"] = definition["artifact_output"]
    if definition.get("parameters"):
        fm["parameters"] = definition["parameters"]
    return fm


def _frontmatter_md(frontmatter: Dict[str, Any]) -> str:
    dumped = yaml.safe_dump(
        _drop_empty(frontmatter),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{dumped}\n---\n"


def _drop_empty(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _drop_empty(v)
            for k, v in obj.items()
            if v is not None and v != {} and v != []
        }
    if isinstance(obj, list):
        return [_drop_empty(v) for v in obj]
    return obj


def _writestr(zf: zipfile.ZipFile, arcname: str, content: str) -> None:
    zi = zipfile.ZipInfo(arcname, date_time=_ZIP_FIXED_DATE)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = _ZIP_FIXED_MODE << 16
    zf.writestr(zi, content.encode("utf-8"), compresslevel=9)


def _safe_name(name: str, *, fallback: str) -> str:
    safe = _SAFE_FILENAME_RE.sub("_", name.strip()).strip("._")
    if not safe:
        safe = fallback
    if safe.startswith("_"):
        safe = f"{fallback}_{safe.lstrip('_') or 'unit'}"
    return safe[:80]


def _safe_md_name(name: str) -> str:
    return f"{_safe_name(name, fallback='tool')}.md"


def _unique_member_filename(member_name: str, used: set[str]) -> str:
    stem = _safe_name(member_name, fallback="member")
    candidate = f"{stem}.md"
    idx = 2
    while candidate in used:
        candidate = f"{stem}_{idx}.md"
        idx += 1
    used.add(candidate)
    return candidate


def _stage_single_md(root: Path, filename: str, blob: bytes) -> None:
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ToolSeedBundleError("seed Markdown must be UTF-8") from e
    try:
        frontmatter, _ = parse_frontmatter_text(text, filename)
    except FrontmatterError as e:
        raise ToolSeedBundleError(str(e)) from e

    tool_type = frontmatter.get("type")
    if tool_type is None:
        if _looks_like_mcp_seed(frontmatter):
            raise ToolSeedBundleError(
                "single-file MCP seed upload must explicitly declare type: mcp"
            )
        tool_type = "http"
    if tool_type not in {"http", "mcp"}:
        raise ToolSeedBundleError(
            "single-file seed upload type must be http or mcp"
        )
    target_dir = root / ("mcp" if tool_type == "mcp" else "tools")
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "upload.md").write_bytes(blob)


def _looks_like_mcp_seed(frontmatter: Dict[str, Any]) -> bool:
    return any(key in frontmatter for key in ("url", "transport", "default_permission"))


def _extract_seed_zip(root: Path, blob: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        raise ToolSeedBundleError("seed upload must be a .zip bundle or a .md seed file") from e

    with zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if len(infos) > MAX_SEED_ZIP_FILES:
            raise ToolSeedBundleError(f"seed bundle contains too many files (max {MAX_SEED_ZIP_FILES})")
        for info in infos:
            if info.file_size > MAX_SEED_FILE_BYTES:
                raise ToolSeedBundleError(
                    f"seed bundle file '{info.filename}' is too large "
                    f"(max {MAX_SEED_FILE_BYTES // 1024}KB)"
                )
            rel = _safe_zip_member(info.filename)
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=64 * 1024)
            except (zipfile.BadZipFile, zlib.error, RuntimeError, OSError) as e:
                raise ToolSeedBundleError(
                    f"failed to extract seed bundle file '{info.filename}'"
                ) from e


def _safe_zip_member(filename: str) -> PurePosixPath:
    normalized = posixpath.normpath(filename.replace("\\", "/"))
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or normalized in {"", "."}:
        raise ToolSeedBundleError(f"unsafe path in seed bundle: {filename}")
    return path


def _parse_staged_seeds(root: Path) -> List[ToolUnitSeed]:
    roots = [
        (root / "tools", root / "mcp"),
        (root / "config" / "tools", root / "config" / "mcp"),
    ]
    for tools_dir, mcp_dir in roots:
        seeds = parse_tool_seeds(str(tools_dir)) + parse_mcp_seeds(str(mcp_dir))
        if seeds:
            return seeds

    # Convenience for a zip that contains one singleton .md at its root.
    root_mds = [
        p for p in root.iterdir()
        if p.is_file() and p.suffix == ".md" and not p.name.startswith(("_", "."))
    ]
    if len(root_mds) == 1:
        with open(root_mds[0], "rb") as f:
            _stage_single_md(root / "__single__", root_mds[0].name, f.read())
        return (
            parse_tool_seeds(str(root / "__single__" / "tools"))
            + parse_mcp_seeds(str(root / "__single__" / "mcp"))
        )

    return []
