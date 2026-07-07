#!/usr/bin/env python3
"""Replace text in a .pptx while preserving formatting when possible.

Usage:
    python replace_text.py input.pptx output.pptx --find OLD --replace NEW
    python replace_text.py input.pptx output.pptx --map replacements.json

`--map` accepts either {"old": "new"} or
[{"find": "old", "replace": "new"}, ...].

The script first tries run-level replacement, preserving run formatting. If the
match spans multiple runs inside one paragraph, it rewrites that paragraph into
the first run and reports a `paragraph_rewrites` count. Missing find strings are
a failure unless `--allow-missing` is set. `--find` requires `--replace`; pass
`--replace ""` explicitly to delete matched text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pptx import Presentation


def _load_replacements(args) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if args.map:
        raw = json.loads(Path(args.map).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            pairs.extend((str(k), str(v)) for k, v in raw.items())
        elif isinstance(raw, list):
            for item in raw:
                pairs.append((str(item["find"]), str(item["replace"])))
        else:
            raise SystemExit("error: --map must be a JSON object or list")
    if args.find is not None:
        if args.replace is None:
            raise SystemExit("error: --find requires --replace")
        pairs.append((args.find, args.replace))
    elif args.replace is not None:
        raise SystemExit("error: --replace requires --find")
    if not pairs:
        raise SystemExit("error: provide --find/--replace or --map")
    empty = [old for old, _ in pairs if old == ""]
    if empty:
        raise SystemExit("error: find strings must be non-empty")
    return pairs


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
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    return out


def _text_frames(shape):
    if getattr(shape, "has_text_frame", False):
        yield shape.text_frame
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            for cell in row.cells:
                yield cell.text_frame


def _replace_in_paragraph(paragraph, old: str, new: str) -> tuple[int, int]:
    run_hits = 0
    for run in paragraph.runs:
        if old in run.text:
            run.text = run.text.replace(old, new)
            run_hits += 1
    if run_hits:
        return run_hits, 0

    full = "".join(run.text for run in paragraph.runs)
    if old not in full:
        return 0, 0
    rewritten = full.replace(old, new)
    if paragraph.runs:
        paragraph.runs[0].text = rewritten
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run().text = rewritten
    return 1, 1


def replace_text(
    src: Path,
    out: Path,
    replacements: list[tuple[str, str]],
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

    for old, new in replacements:
        hits = 0
        rewrites = 0
        for slide_idx, slide in enumerate(prs.slides, 1):
            if slides is not None and slide_idx not in slides:
                continue
            for shape in _iter_shapes(slide.shapes):
                for tf in _text_frames(shape):
                    for paragraph in tf.paragraphs:
                        h, r = _replace_in_paragraph(paragraph, old, new)
                        hits += h
                        rewrites += r
        summary["replacements"].append({
            "find": old,
            "replace": new,
            "hits": hits,
            "paragraph_rewrites": rewrites,
        })
        summary["total_hits"] += hits
        summary["paragraph_rewrites"] += rewrites

    missing = [r["find"] for r in summary["replacements"] if r["hits"] == 0]
    if missing and not allow_missing:
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(f"error: find string(s) not found: {missing}")

    prs.save(str(out))
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
    summary = replace_text(
        src,
        Path(args.output),
        replacements,
        slides=slide_set,
        allow_missing=args.allow_missing,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
