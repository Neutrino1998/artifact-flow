#!/usr/bin/env python3
"""Apply simple tracked changes to a .docx file.

Usage:
    python apply_redline.py input.docx output.docx --replace OLD --with NEW --author Reviewer
    python apply_redline.py input.docx output.docx --replace-file old.txt --with-file new.txt
    python apply_redline.py input.docx output.docx --delete OLD --author Reviewer
    python apply_redline.py input.docx output.docx --insert-after ANCHOR --text NEW --author Reviewer
    python apply_redline.py input.docx output.docx --insert-before ANCHOR --text NEW --author Reviewer
    python apply_redline.py input.docx output.docx --plan changes.json --author Reviewer

Plan format:
    {"changes": [
      {"op": "replace", "find_file": "old.txt", "replace_file": "new.txt", "expect": 1},
      {"op": "delete", "find": "obsolete text", "expect": 1},
      {"op": "insert_after", "find": "anchor", "text_file": "addition.txt", "expect": 1}
    ]}

Paths inside a plan are resolved relative to the plan file. Text files are
UTF-8; one final line ending is stripped so quoted heredocs are convenient.
The whole plan is atomic: all changes must match their expected counts before
the output file is replaced.

Scope is intentionally narrow: the match must be visible text inside one
paragraph and plain direct runs. Existing tracked changes, hyperlinks, fields,
text boxes, headers, and footers are not edited. For structural edits, unpack
and follow references/redlines.md manually.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
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


def _preview(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


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
    if not needle:
        raise RedlineError("anchor text must not be empty")
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


def _count_matches(root, needle: str) -> int:
    if not needle:
        raise RedlineError("anchor text must not be empty")
    if "\n" in needle:
        raise RedlineError("multi-line matches are not supported")
    return sum(_direct_text_runs(paragraph)[1].count(needle) for paragraph in root.iter(W + "p"))


def _load_docx(src: Path):
    with zipfile.ZipFile(src) as zin:
        members = [(info, zin.read(info.filename)) for info in zin.infolist()]
    for index, (info, blob) in enumerate(members):
        if info.filename == "word/document.xml":
            return members, index, etree.fromstring(blob)
    raise RedlineError("word/document.xml is missing")


def _write_docx(members, document_index: int, root, out: Path) -> None:
    blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    info, _ = members[document_index]
    members[document_index] = (info, blob)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for member_info, member_blob in members:
                zout.writestr(member_info, member_blob)
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, out)
    finally:
        temp_path.unlink(missing_ok=True)


def _apply_plan_changes(root, changes: list[dict], *, author: str) -> list[dict]:
    if not changes:
        raise RedlineError("plan changes must not be empty")
    summaries = []
    for index, change in enumerate(changes, 1):
        mode = str(change.get("op", "")).replace("_", "-")
        if mode not in {"replace", "delete", "insert-before", "insert-after"}:
            raise RedlineError(f"change {index}: unsupported op {mode!r}")
        needle = change.get("find")
        if not isinstance(needle, str):
            raise RedlineError(f"change {index}: find must be a string")
        if mode == "replace":
            new_text = change.get("replace")
        elif mode.startswith("insert-"):
            new_text = change.get("text")
        else:
            new_text = ""
        if not isinstance(new_text, str):
            raise RedlineError(f"change {index}: replacement text must be a string")
        if mode != "delete" and not new_text:
            raise RedlineError(f"change {index}: replacement text must not be empty")
        expect = change.get("expect", 1)
        if not isinstance(expect, int) or isinstance(expect, bool) or expect < 1:
            raise RedlineError(f"change {index}: expect must be a positive integer")
        if mode.startswith("insert-") and expect != 1:
            raise RedlineError(f"change {index}: insert operations require expect=1")

        found = _count_matches(root, needle)
        if found != expect:
            raise RedlineError(
                f"change {index}: expected {expect} editable match(es), found {found}: "
                f"{_preview(needle)!r}"
            )
        applied = _find_and_apply(
            root,
            needle=needle,
            mode=mode,
            new_text=new_text,
            author=author,
            all_matches=expect > 1,
        )
        if applied != expect:
            raise RedlineError(f"change {index}: applied {applied}, expected {expect}")
        summaries.append({
            "index": index,
            "op": mode,
            "find": _preview(needle),
            "find_chars": len(needle),
            "applied": applied,
        })
    return summaries


def apply_plan(src: Path, out: Path, *, changes: list[dict], author: str) -> dict:
    if src.resolve() == out.resolve():
        raise RedlineError("input and output must be different paths")
    members, document_index, root = _load_docx(src)
    summaries = _apply_plan_changes(root, changes, author=author)
    _write_docx(members, document_index, root, out)
    return {
        "author": author,
        "changes": summaries,
        "total_changes": sum(item["applied"] for item in summaries),
        "out": str(out),
    }


def apply_redline(src: Path, out: Path, *, needle: str, mode: str, new_text: str, author: str, all_matches: bool):
    expected = _count_matches(_load_docx(src)[2], needle) if all_matches else 1
    summary = apply_plan(
        src,
        out,
        changes=[{"op": mode, "find": needle, "replace" if mode == "replace" else "text": new_text,
                  "expect": expected}],
        author=author,
    )
    return {
        "mode": mode,
        "anchor": _preview(needle),
        "anchor_chars": len(needle),
        "author": author,
        "changes": summary["total_changes"],
        "out": str(out),
    }


def _read_text_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def _resolve_plan_text(change: dict, *, direct_key: str, file_key: str, base: Path, index: int) -> str:
    direct = change.get(direct_key)
    file_name = change.get(file_key)
    if direct is not None and file_name is not None:
        raise RedlineError(f"change {index}: use only {direct_key} or {file_key}")
    if file_name is not None:
        if not isinstance(file_name, str) or not file_name:
            raise RedlineError(f"change {index}: {file_key} must be a path string")
        return _read_text_file(base / file_name)
    if not isinstance(direct, str):
        raise RedlineError(f"change {index}: missing {direct_key}/{file_key}")
    return direct


def _load_plan(path: Path) -> tuple[list[dict], str | None]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("changes"), list):
        raise RedlineError("plan must be an object with a changes array")
    resolved = []
    for index, original in enumerate(raw["changes"], 1):
        if not isinstance(original, dict):
            raise RedlineError(f"change {index}: must be an object")
        change = dict(original)
        change["find"] = _resolve_plan_text(
            change, direct_key="find", file_key="find_file", base=path.parent, index=index
        )
        mode = str(change.get("op", "")).replace("_", "-")
        if mode == "replace":
            change["replace"] = _resolve_plan_text(
                change, direct_key="replace", file_key="replace_file", base=path.parent, index=index
            )
        elif mode.startswith("insert-"):
            change["text"] = _resolve_plan_text(
                change, direct_key="text", file_key="text_file", base=path.parent, index=index
            )
        resolved.append(change)
    plan_author = raw.get("author")
    if plan_author is not None and not isinstance(plan_author, str):
        raise RedlineError("plan author must be a string")
    return resolved, plan_author


def _choose_text(direct: str | None, file_name: str | None, label: str) -> str:
    if direct is not None and file_name is not None:
        raise RedlineError(f"use only --{label} or --{label}-file")
    if file_name is not None:
        return _read_text_file(Path(file_name))
    if direct is None:
        raise RedlineError(f"missing --{label} or --{label}-file")
    return direct


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", help="JSON batch plan; text may come from UTF-8 files")
    group.add_argument("--replace", dest="replace_text")
    group.add_argument("--replace-file")
    group.add_argument("--delete")
    group.add_argument("--delete-file")
    group.add_argument("--insert-before")
    group.add_argument("--insert-before-file")
    group.add_argument("--insert-after")
    group.add_argument("--insert-after-file")
    parser.add_argument("--with", dest="with_text")
    parser.add_argument("--with-file")
    parser.add_argument("--text")
    parser.add_argument("--text-file")
    parser.add_argument("--author")
    parser.add_argument("--all", action="store_true", help="apply to all matching paragraphs")
    args = parser.parse_args()

    try:
        if args.plan:
            if args.all:
                raise RedlineError("--all cannot be used with --plan; set expect per change")
            changes, plan_author = _load_plan(Path(args.plan))
            summary = apply_plan(
                Path(args.input), Path(args.output), changes=changes,
                author=args.author or plan_author or "ArtifactFlow",
            )
        else:
            if args.replace_text is not None or args.replace_file is not None:
                mode = "replace"
                needle = _choose_text(args.replace_text, args.replace_file, "replace")
                new_text = _choose_text(args.with_text, args.with_file, "with")
            elif args.delete is not None or args.delete_file is not None:
                mode = "delete"
                needle = _choose_text(args.delete, args.delete_file, "delete")
                new_text = ""
            elif args.insert_before is not None or args.insert_before_file is not None:
                mode = "insert-before"
                needle = _choose_text(args.insert_before, args.insert_before_file, "insert-before")
                new_text = _choose_text(args.text, args.text_file, "text")
            else:
                mode = "insert-after"
                needle = _choose_text(args.insert_after, args.insert_after_file, "insert-after")
                new_text = _choose_text(args.text, args.text_file, "text")
            if args.all and mode in ("insert-before", "insert-after"):
                raise RedlineError("--all is only supported for replace and delete")
            summary = apply_redline(
                Path(args.input), Path(args.output), needle=needle, mode=mode,
                new_text=new_text, author=args.author or "ArtifactFlow", all_matches=args.all,
            )
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile, etree.XMLSyntaxError, RedlineError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
