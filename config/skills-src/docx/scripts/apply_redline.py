#!/usr/bin/env python3
"""Apply simple tracked changes to a .docx file.

Usage:
    python apply_redline.py input.docx output.docx --replace OLD --with NEW --author Reviewer
    python apply_redline.py input.docx output.docx --delete OLD --author Reviewer
    python apply_redline.py input.docx output.docx --insert-after ANCHOR --text NEW --author Reviewer
    python apply_redline.py input.docx output.docx --insert-before ANCHOR --text NEW --author Reviewer

Scope is intentionally narrow: the match must be visible text inside one
paragraph and plain direct runs. Existing tracked changes, hyperlinks, fields,
text boxes, headers, and footers are not edited. For structural edits, unpack
and follow references/redlines.md manually.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_XML = "http://www.w3.org/XML/1998/namespace"
W = "{%s}" % NS_W
XML_SPACE = "{%s}space" % NS_XML


class RedlineError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_id(root) -> int:
    ids = []
    for el in root.iter():
        if el.tag in (W + "ins", W + "del") and el.get(W + "id", "").isdigit():
            ids.append(int(el.get(W + "id")))
    return max(ids, default=0) + 1


def _inside_tracked_change(run, paragraph) -> bool:
    cur = run.getparent()
    while cur is not None and cur is not paragraph:
        if cur.tag in (W + "ins", W + "del", W + "moveFrom", W + "moveTo"):
            return True
        cur = cur.getparent()
    return False


def _run_text(run) -> str:
    return "".join(t.text or "" for t in run.findall(W + "t"))


def _direct_text_runs(paragraph):
    runs = []
    offset = 0
    for child in paragraph:
        if child.tag != W + "r" or _inside_tracked_change(child, paragraph):
            continue
        text = _run_text(child)
        if not text:
            continue
        runs.append({
            "run": child,
            "start": offset,
            "end": offset + len(text),
            "text": text,
        })
        offset += len(text)
    return runs, "".join(item["text"] for item in runs)


def _text_el(tag: str, text: str):
    el = etree.Element(W + tag)
    el.text = text
    if text[:1].isspace() or text[-1:].isspace():
        el.set(XML_SPACE, "preserve")
    return el


def _clone_run_with_text(run, text: str, *, deleted: bool = False):
    if not text:
        return None
    new_run = etree.Element(W + "r")
    rpr = run.find(W + "rPr")
    if rpr is not None:
        new_run.append(copy.deepcopy(rpr))
    new_run.append(_text_el("delText" if deleted else "t", text))
    return new_run


def _change_wrapper(tag: str, change_id: int, author: str, date: str):
    el = etree.Element(W + tag)
    el.set(W + "id", str(change_id))
    el.set(W + "author", author)
    el.set(W + "date", date)
    return el


def _insert_change_after(anchor, wrapper) -> None:
    anchor.addnext(wrapper)


def _insert_change_before(anchor, wrapper) -> None:
    anchor.addprevious(wrapper)


def _insert_at_text_offset(runs, offset: int, wrapper) -> None:
    for item in runs:
        if item["start"] <= offset <= item["end"]:
            run = item["run"]
            if offset == item["start"]:
                _insert_change_before(run, wrapper)
                return
            if offset == item["end"]:
                _insert_change_after(run, wrapper)
                return

            split_at = offset - item["start"]
            before = _clone_run_with_text(run, item["text"][:split_at])
            after = _clone_run_with_text(run, item["text"][split_at:])
            if before is not None:
                run.addprevious(before)
            run.addprevious(wrapper)
            if after is not None:
                run.addprevious(after)
            parent = run.getparent()
            if parent is not None:
                parent.remove(run)
            return
    raise RedlineError("insert position is not in editable direct runs")


def _apply_inline_change(
    paragraph,
    runs,
    start: int,
    end: int,
    *,
    mode: str,
    new_text: str,
    author: str,
    change_id: int,
    date: str,
) -> None:
    affected = [item for item in runs if item["start"] < end and item["end"] > start]
    if not affected:
        raise RedlineError("match is not in editable direct runs")

    first_run = affected[0]["run"]
    last_run = affected[-1]["run"]
    base_run = first_run

    if mode in ("insert-before", "insert-after"):
        inserted = _change_wrapper("ins", change_id, author, date)
        inserted_run = _clone_run_with_text(base_run, new_text)
        if inserted_run is not None:
            inserted.append(inserted_run)
        insert_at = end if mode == "insert-after" else start
        _insert_at_text_offset(runs, insert_at, inserted)
        return

    left_text = affected[0]["text"][:max(start - affected[0]["start"], 0)]
    right_text = affected[-1]["text"][max(end - affected[-1]["start"], 0):]

    before = _clone_run_with_text(base_run, left_text)
    after = _clone_run_with_text(last_run, right_text)

    if before is not None:
        first_run.addprevious(before)

    if mode in ("replace", "delete"):
        deleted = _change_wrapper("del", change_id, author, date)
        for item in affected:
            frag_start = max(start, item["start"]) - item["start"]
            frag_end = min(end, item["end"]) - item["start"]
            deleted_run = _clone_run_with_text(
                item["run"], item["text"][frag_start:frag_end], deleted=True
            )
            if deleted_run is not None:
                deleted.append(deleted_run)
        first_run.addprevious(deleted)
        change_id += 1

    if mode == "replace" and new_text:
        inserted = _change_wrapper("ins", change_id, author, date)
        inserted_run = _clone_run_with_text(base_run, new_text)
        if inserted_run is not None:
            inserted.append(inserted_run)
        first_run.addprevious(inserted)

    if after is not None:
        last_run.addnext(after)

    if mode in ("replace", "delete"):
        for item in affected:
            parent = item["run"].getparent()
            if parent is not None:
                parent.remove(item["run"])


def _find_and_apply(root, *, needle: str, mode: str, new_text: str, author: str, all_matches: bool):
    if "\n" in needle or "\n" in new_text:
        raise RedlineError("multi-line matches/new text are not supported")
    date = _now()
    change_id = _next_id(root)
    applied = 0

    for paragraph in root.iter(W + "p"):
        # Recompute after each edit because the paragraph tree changes.
        while True:
            runs, full_text = _direct_text_runs(paragraph)
            pos = full_text.find(needle)
            if pos < 0:
                break
            start, end = pos, pos + len(needle)
            _apply_inline_change(
                paragraph,
                runs,
                start,
                end,
                mode=mode,
                new_text=new_text,
                author=author,
                change_id=change_id,
                date=date,
            )
            applied += 1
            change_id += 2
            if not all_matches:
                return applied
    return applied


def apply_redline(src: Path, out: Path, *, needle: str, mode: str, new_text: str, author: str, all_matches: bool):
    with zipfile.ZipFile(src) as zin:
        members = [(info, zin.read(info.filename)) for info in zin.infolist()]

    updated = []
    applied = 0
    for info, blob in members:
        if info.filename == "word/document.xml":
            root = etree.fromstring(blob)
            applied = _find_and_apply(
                root,
                needle=needle,
                mode=mode,
                new_text=new_text,
                author=author,
                all_matches=all_matches,
            )
            if applied == 0:
                raise RedlineError(f"anchor text not found in editable document body: {needle!r}")
            blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        updated.append((info, blob))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, blob in updated:
            zout.writestr(info.filename, blob)
    return {"mode": mode, "anchor": needle, "author": author, "changes": applied, "out": str(out)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--replace", dest="replace_text")
    group.add_argument("--delete")
    group.add_argument("--insert-before")
    group.add_argument("--insert-after")
    parser.add_argument("--with", dest="with_text", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--author", default="ArtifactFlow")
    parser.add_argument("--all", action="store_true", help="apply to all matching paragraphs")
    args = parser.parse_args()

    if args.replace_text is not None:
        mode, needle, new_text = "replace", args.replace_text, args.with_text
        if not new_text:
            raise SystemExit("error: --replace requires --with NEW")
    elif args.delete is not None:
        mode, needle, new_text = "delete", args.delete, ""
    elif args.insert_before is not None:
        mode, needle, new_text = "insert-before", args.insert_before, args.text
        if not new_text:
            raise SystemExit("error: --insert-before requires --text NEW")
    else:
        mode, needle, new_text = "insert-after", args.insert_after, args.text
        if not new_text:
            raise SystemExit("error: --insert-after requires --text NEW")
    if args.all and mode in ("insert-before", "insert-after"):
        raise SystemExit("error: --all is only supported for --replace and --delete")

    try:
        summary = apply_redline(
            Path(args.input),
            Path(args.output),
            needle=needle,
            mode=mode,
            new_text=new_text,
            author=args.author,
            all_matches=args.all,
        )
    except (OSError, zipfile.BadZipFile, etree.XMLSyntaxError, RedlineError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
