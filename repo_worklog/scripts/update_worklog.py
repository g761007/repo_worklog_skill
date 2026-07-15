#!/usr/bin/env python3
"""Simulate or apply repo_worklog updates to the Markdown worklog file.

Given a set of ``{date: generated_markdown}`` entries, this script computes the
resulting document by inserting new date blocks (descending order) and
overwriting the GENERATED region of existing ones, always preserving MANUAL
regions and any content outside the ENTRIES area.

By default it runs a dry-run: nothing is written, no directory is created, and
the full preview plus a planned-change list is emitted. With ``--apply`` it
writes safely via a same-directory temp file and an atomic replace, re-parsing
and re-validating before and after the swap.

Input (``--input FILE`` or stdin):
    {"entries": {"2026-07-15": {"generated_markdown": "..."}, ...}}

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import sys
import tempfile

import worklog_markers as wm

DEFAULT_TARGET = "docs/PROJECT_WORKLOG.md"


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_input(path: str | None) -> dict:
    raw = open(path, "r", encoding="utf-8").read() if path and path != "-" else sys.stdin.read()
    if not raw.strip():
        return {"entries": {}}
    return json.loads(raw)


def _read_target(target: str) -> tuple[str | None, wm.WorklogDoc, bool]:
    """Return (original_text, doc, existed). Raise on corruption."""
    if os.path.exists(target):
        with open(target, "rb") as fh:
            raw_bytes = fh.read()
        try:
            original = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail("NON_UTF8", f"Existing worklog is not valid UTF-8: {exc}", target=target)
        try:
            doc = wm.parse(original)
        except wm.WorklogFormatError as exc:
            _fail("CORRUPT_MARKERS",
                  "Existing worklog has corrupted markers; refusing to guess a repair.",
                  target=target, issues=exc.issues)
        return original, doc, True
    return None, wm.new_document(), False


def plan_update(doc: wm.WorklogDoc, entries: dict) -> tuple[wm.WorklogDoc, list[dict]]:
    existing = doc.by_date()
    planned: list[dict] = []
    for date in sorted(entries.keys(), reverse=True):
        gen_md = entries[date].get("generated_markdown", "")
        if date in existing:
            entry = existing[date]
            entry.block = wm.replace_generated(entry, gen_md)
            entry.generated = gen_md
            planned.append({"date": date, "action": "overwrite",
                            "manual_preserved": bool(entry.manual.strip())})
        else:
            block = wm.render_generated_block(date, gen_md)
            doc.entries.append(wm.Entry(date, block, gen_md, "", f"## {date}"))
            planned.append({"date": date, "action": "insert", "manual_preserved": False})
    planned.sort(key=lambda p: p["date"], reverse=True)
    return doc, planned


def _atomic_write(target: str, content: str) -> None:
    target_dir = os.path.dirname(os.path.abspath(target))
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".worklog-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        # Re-parse the staged file before it becomes the real one.
        with open(tmp_path, "r", encoding="utf-8") as fh:
            staged = fh.read()
        wm.parse(staged)
        os.replace(tmp_path, target)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def run(args: argparse.Namespace) -> int:
    payload = _load_input(args.input)
    entries = payload.get("entries", {})
    if not isinstance(entries, dict) or not entries:
        _fail("NO_ENTRIES", "No entries provided to write.")

    target = args.target or payload.get("target") or DEFAULT_TARGET
    original, doc, existed = _read_target(target)
    manual_before = {d: e.manual for d, e in doc.by_date().items()}

    updated_doc, planned = plan_update(doc, entries)
    preview = wm.serialise(updated_doc)

    original_text = original if existed else ""
    diff = list(difflib.unified_diff(
        original_text.splitlines(keepends=True),
        preview.splitlines(keepends=True),
        fromfile=(target if existed else "/dev/null"),
        tofile=target,
    ))

    common = {
        "target": target,
        "target_exists": existed,
        "target_dir_exists": os.path.isdir(os.path.dirname(os.path.abspath(target))),
        "planned_changes": planned,
        "preserved_manual_dates": sorted(d for d, m in manual_before.items() if m.strip()),
        "original_sha256": _sha256(original_text) if existed else None,
        "preview_sha256": _sha256(preview),
    }

    if not args.apply:
        _emit({
            "ok": True, "mode": "dry-run",
            **common,
            "preview_content": preview,
            "diff": "".join(diff),
            "note": "No files have been modified. docs/ is not created during dry-run.",
        })
        return 0

    # Apply path.
    manual_after = {d: e.manual for d, e in updated_doc.by_date().items()}
    for date, before in manual_before.items():
        if date in manual_after and manual_after[date] != before:
            _fail("MANUAL_MUTATED",
                  f"Refusing to write: MANUAL content for {date} would change.", date=date)

    _atomic_write(target, preview)

    with open(target, "r", encoding="utf-8") as fh:
        written = fh.read()
    final_doc, issues = wm.scan(written)
    fatal = [i for i in issues if i["code"] in wm.FATAL_CODES]
    if fatal:
        _fail("POST_WRITE_INVALID",
              "Worklog failed validation after writing.", issues=fatal)

    _emit({
        "ok": True, "mode": "apply",
        **common,
        "written_sha256": _sha256(written),
        "final_dates": final_doc.dates() if final_doc else [],
        "note": "Worklog written atomically. No git add / commit / push was performed.",
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Insert/overwrite repo_worklog entries.")
    p.add_argument("--input", help="Path to entries JSON, or '-' / omit for stdin.")
    p.add_argument("--target", help=f"Worklog path (default: {DEFAULT_TARGET}).")
    p.add_argument("--apply", action="store_true",
                   help="Write the file. Without this flag the run is a dry-run.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (json.JSONDecodeError, OSError) as exc:
        _fail("IO_ERROR", f"{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
