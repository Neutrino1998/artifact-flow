#!/usr/bin/env python3
"""Replace text in a .pptx while preserving formatting when possible.

Usage:
    python replace_text.py input.pptx output.pptx --find OLD --replace NEW
    python replace_text.py input.pptx output.pptx --map replacements.json

`--map` accepts either the legacy exact-all form {"old": "new"} or the checked
form [{"find": "old", "replace": "new", "match": "auto", "expect": 1}, ...].

The script first tries run-level replacement, preserving run formatting. If the
match spans multiple runs inside one paragraph, it rewrites that paragraph into
the first run and reports a `paragraph_rewrites` count. Missing find strings are
a failure unless `--allow-missing` is set. Checked single replacements require
one unique candidate across selected slides. `--find` requires `--replace`;
pass `--replace ""` explicitly to delete matched text.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from pptx import Presentation
try:
    from text_match import find_unique_in_segments
except ModuleNotFoundError as exc:
    if exc.name != "text_match":
        raise
    from utils.text_match import find_unique_in_segments


MAX_SELECTED_SLIDES = 1000


def _structured_replacement(item: dict, index: int) -> dict:
    if "find" not in item or "replace" not in item:
        raise ValueError(f"replacement {index} requires find and replace")
    old = str(item["find"])
    new = str(item["replace"])
    if not old:
        raise ValueError(f"replacement {index} find must not be empty")
    expect = item.get("expect", 1)
    if not isinstance(expect, int) or isinstance(expect, bool) or expect < 1:
        raise ValueError(f"replacement {index} expect must be a positive integer")
    match_mode = item.get("match", "auto" if expect == 1 else "exact")
    if match_mode not in {"exact", "normalized", "auto"}:
        raise ValueError(f"replacement {index} has unsupported match mode")
    if expect > 1 and match_mode != "exact":
        raise ValueError(f"replacement {index} expect > 1 requires exact matching")
    return {
        "find": old,
        "replace": new,
        "expect": expect,
        "match": match_mode,
    }


def _load_replacements(args) -> list[dict]:
    replacements: list[dict] = []
    if args.map:
        raw = json.loads(Path(args.map).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            replacements.extend({
                "find": str(old),
                "replace": str(new),
                "expect": None,
                "match": "exact",
            } for old, new in raw.items())
        elif isinstance(raw, list):
            for index, item in enumerate(raw, 1):
                if not isinstance(item, dict):
                    raise SystemExit(f"error: replacement {index} must be an object")
                try:
                    replacements.append(_structured_replacement(item, index))
                except ValueError as exc:
                    raise SystemExit(f"error: {exc}") from exc
        else:
            raise SystemExit("error: --map must be a JSON object or list")
    if args.find is not None:
        if args.replace is None:
            raise SystemExit("error: --find requires --replace")
        replacements.append({
            "find": args.find,
            "replace": args.replace,
            "expect": None,
            "match": "exact",
        })
    elif args.replace is not None:
        raise SystemExit("error: --replace requires --find")
    if not replacements:
        raise SystemExit("error: provide --find/--replace or --map")
    if any(item["find"] == "" for item in replacements):
        raise SystemExit("error: find strings must be non-empty")
    return replacements


def _iter_shapes(shapes):
    for shape in shapes:
        yield shape
        child_shapes = getattr(shape, "shapes", None)
        if child_shapes is not None:
            yield from _iter_shapes(child_shapes)


def _parse_slides(expr: str | None) -> set[int] | None:
    if not expr:
        return None
    out: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = [int(x) for x in part.split("-", 1)]
            if lo > hi:
                raise SystemExit(f"error: bad slide range {part!r}")
            span = hi - lo + 1
            if span > MAX_SELECTED_SLIDES or len(out) + span > MAX_SELECTED_SLIDES:
                raise SystemExit(
                    f"error: select at most {MAX_SELECTED_SLIDES} slides"
                )
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
            if len(out) > MAX_SELECTED_SLIDES:
                raise SystemExit(
                    f"error: select at most {MAX_SELECTED_SLIDES} slides"
                )
    return out


def _text_frames(shape):
    if getattr(shape, "has_text_frame", False):
        yield shape.text_frame
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            for cell in row.cells:
                yield cell.text_frame


def _paragraph_text(paragraph) -> str:
    return "".join(run.text for run in paragraph.runs)


def _replace_span(paragraph, start: int, end: int, new: str) -> int:
    runs = []
    offset = 0
    for run in paragraph.runs:
        text = run.text
        runs.append((run, offset, offset + len(text), text))
        offset += len(text)
    affected = [item for item in runs if item[1] < end and item[2] > start]
    if not affected:
        raise RuntimeError("matched span is not backed by paragraph runs")

    if len(affected) == 1:
        run, run_start, _, text = affected[0]
        run.text = text[:start - run_start] + new + text[end - run_start:]
        return 0

    full = "".join(item[3] for item in runs)
    rewritten = full[:start] + new + full[end:]
    runs[0][0].text = rewritten
    for run, _, _, _ in runs[1:]:
        run.text = ""
    return 1


def _exact_spans(text: str, old: str) -> list[tuple[int, int]]:
    spans = []
    offset = 0
    while True:
        start = text.find(old, offset)
        if start < 0:
            return spans
        spans.append((start, start + len(old)))
        offset = start + len(old)


def _paragraph_entries(prs, slides: set[int] | None):
    entries = []
    for slide_idx, slide in enumerate(prs.slides, 1):
        if slides is not None and slide_idx not in slides:
            continue
        for shape in _iter_shapes(slide.shapes):
            for text_frame in _text_frames(shape):
                for paragraph in text_frame.paragraphs:
                    text = _paragraph_text(paragraph)
                    if text:
                        entries.append((slide_idx, paragraph, text))
    return entries


def _normalize_replacements(replacements) -> list[dict]:
    normalized = []
    for index, item in enumerate(replacements, 1):
        if isinstance(item, dict):
            if item.get("expect") is None:
                old = str(item.get("find", ""))
                if not old or "replace" not in item:
                    raise ValueError(
                        f"replacement {index} requires non-empty find and replace"
                    )
                normalized.append({
                    "find": old,
                    "replace": str(item["replace"]),
                    "expect": None,
                    "match": "exact",
                })
            else:
                normalized.append(_structured_replacement(item, index))
        else:
            old, new = item
            normalized.append({
                "find": str(old),
                "replace": str(new),
                "expect": None,
                "match": "exact",
            })
    return normalized


def _save_atomic(prs, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{out.name}.", suffix=".pptx", dir=out.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        prs.save(str(temp_path))
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, out)
    finally:
        temp_path.unlink(missing_ok=True)


def replace_text(
    src: Path,
    out: Path,
    replacements,
    *,
    slides: set[int] | None,
    allow_missing: bool,
) -> dict:
    prs = Presentation(str(src))
    summary = {
        "file": str(src),
        "out": str(out),
        "replacements": [],
        "total_hits": 0,
        "paragraph_rewrites": 0,
    }

    for replacement in _normalize_replacements(replacements):
        old = replacement["find"]
        new = replacement["replace"]
        expect = replacement["expect"]
        match_mode = replacement["match"]
        entries = _paragraph_entries(prs, slides)
        matches = []
        match_type = "exact"
        similarity = 1.0
        matched_text = old

        if expect == 1:
            located = find_unique_in_segments(
                [entry[2] for entry in entries], old, mode=match_mode
            )
            if not located.success or located.segment_index is None:
                raise ValueError(
                    f"expected 1 editable match for {old!r}: {located.message}"
                )
            _, paragraph, _ = entries[located.segment_index]
            matches = [(paragraph, located.start, located.end)]
            match_type = located.match_type
            similarity = located.similarity
            matched_text = located.matched_text
        else:
            for _, paragraph, text in entries:
                matches.extend(
                    (paragraph, start, end)
                    for start, end in _exact_spans(text, old)
                )
            if expect is not None and len(matches) != expect:
                raise ValueError(
                    f"expected {expect} exact match(es) for {old!r}, found {len(matches)}"
                )

        hits = len(matches)
        rewrites = 0
        by_paragraph = {}
        for paragraph, start, end in matches:
            by_paragraph.setdefault(paragraph, []).append((start, end))
        for paragraph, spans in by_paragraph.items():
            for start, end in sorted(spans, reverse=True):
                rewrites += _replace_span(paragraph, start, end, new)
        summary["replacements"].append({
            "find": old,
            "replace": new,
            "hits": hits,
            "paragraph_rewrites": rewrites,
            "match_type": match_type,
            "similarity": similarity,
            **({"matched_text": matched_text} if match_type != "exact" else {}),
        })
        summary["total_hits"] += hits
        summary["paragraph_rewrites"] += rewrites

    missing = [r["find"] for r in summary["replacements"] if r["hits"] == 0]
    if missing and not allow_missing:
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(f"error: find string(s) not found: {missing}")

    _save_atomic(prs, out)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--find")
    parser.add_argument("--replace")
    parser.add_argument("--map", help="JSON replacements file")
    parser.add_argument("--slides", help='1-based slides, e.g. "1,3-5"')
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.is_file():
        raise SystemExit(f"error: not a file: {src}")
    replacements = _load_replacements(args)
    slide_set = _parse_slides(args.slides)
    try:
        summary = replace_text(
            src,
            Path(args.output),
            replacements,
            slides=slide_set,
            allow_missing=args.allow_missing,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
