#!/usr/bin/env python3
"""Simulate or apply Git Worklog updates to per-day files under PROJECT_WORKLOG/.

Given ``{date: generated_markdown}`` entries, this script computes each target
day file's new content — creating a fresh file or overwriting only the
GENERATED region of an existing one, always preserving that day's MANUAL region.
Each day lives in its own ``PROJECT_WORKLOG/<date>.md`` file; no other date file
is ever read or rewritten.

Dry-run is the default: nothing is written, the directory is not created, and a
full per-file preview plus a planned-change list and the per-day index summaries
are emitted. ``--apply`` writes all target day files as one transaction — every
file is staged and validated, then swapped in atomically, with rollback so a
mid-run failure never leaves some days updated and others not. The index is
maintained separately by ``rebuild_worklog_index.py``.

Input (``--input FILE`` or stdin):
    {"meta": {"timezone": "...", "branch": "...", "head": "..."},
     "entries": {"2026-07-15": {"generated_markdown": "..."}, ...}}

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile

import worklog_markers as wm

DEFAULT_DIR = wm.WORKLOG_DIRNAME


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
        return {}
    return json.loads(raw)


def _read_existing(path: str) -> str | None:
    """Return the file's text, or None if absent. Fail on non-UTF-8."""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("NON_UTF8", f"Existing day file is not valid UTF-8: {exc}", target=path)


def _plan(worklog_dir: str, entries: dict, meta: dict) -> list[dict]:
    """Compute the intended write for every target date. Fail on corruption."""
    tz = meta.get("timezone")
    branch = meta.get("branch")
    head = meta.get("head")
    writes: list[dict] = []
    for date in sorted(entries.keys(), reverse=True):
        if not wm.is_valid_date(date):
            _fail("INVALID_DATE", f"Entry key {date!r} is not a YYYY-MM-DD date.")
        if not isinstance(entries[date], dict):
            _fail("INVALID_ENTRY",
                  f"Entry for {date} must be an object with 'generated_markdown'.")
        gen_md = entries[date].get("generated_markdown", "")
        if wm.contains_marker_line(gen_md):
            _fail("GENERATED_CONTAINS_MARKER",
                  f"generated_markdown for {date} contains a {wm.PREFIX} marker line, "
                  "which would corrupt the file. Rephrase or escape it.", date=date)
        path = wm.day_path(worklog_dir, date, wm.LAYOUT_CURRENT)
        original = _read_existing(path)
        summary = wm.summarise_generated(gen_md)

        if original is None:
            content = wm.render_new_day_file(date, gen_md, timezone=tz, branch=branch, head=head)
            action, manual_preserved = "create", False
        else:
            try:
                existing_day = wm.parse_day(original, date)
            except wm.WorklogFormatError as exc:
                _fail("CORRUPT_MARKERS",
                      f"Day file {date}.md has corrupted/missing markers; refusing to guess a repair.",
                      target=path, issues=exc.issues)
            content = wm.overwrite_day_generated(original, date, gen_md,
                                                 timezone=tz, branch=branch, head=head)
            # MANUAL must survive byte-for-byte.
            new_day = wm.parse_day(content, date)
            if new_day.manual != existing_day.manual:
                _fail("MANUAL_MUTATED",
                      f"Refusing to write: MANUAL content for {date} would change.", date=date)
            manual_preserved = bool(existing_day.manual.strip())
            action = "no_change" if content == original else "overwrite"

        writes.append({
            "date": date, "path": path, "action": action,
            "manual_preserved": manual_preserved, "summary": summary,
            "original": original, "content": content,
        })
    return writes


def _transactional_apply(worklog_dir: str, writes: list[dict], timezone: str | None = None) -> None:
    """Stage, validate, then atomically swap every changed file, with rollback."""
    changed = [w for w in writes if w["action"] != "no_change"]
    if not changed:
        return
    day_dir = wm.days_dir(worklog_dir, wm.LAYOUT_CURRENT)
    os.makedirs(day_dir, exist_ok=True)
    wm.ensure_data_dir(worklog_dir, timezone)

    # Stage each file to a same-directory temp and validate it before it goes live.
    staged: list[tuple[str, dict]] = []
    try:
        for w in changed:
            fd, tmp = tempfile.mkstemp(dir=day_dir, prefix=".rw-", suffix=".tmp")
            staged.append((tmp, w))   # track immediately so cleanup can't miss it
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(w["content"])
                fh.flush()
                os.fsync(fh.fileno())
            _, issues = wm.scan_day(w["content"], w["date"])
            if [i for i in issues if i["code"] in wm.FATAL_CODES]:
                raise RuntimeError(f"staged {w['date']}.md failed validation")
    except Exception:
        for tmp, _ in staged:
            _safe_unlink(tmp)
        raise

    # Swap them in; on any failure restore everything already swapped.
    swapped: list[dict] = []
    try:
        for tmp, w in staged:
            os.replace(tmp, w["path"])
            swapped.append(w)
    except Exception:
        _rollback(swapped)
        for tmp, _ in staged:
            _safe_unlink(tmp)
        raise

    # Confirm the live files parse; roll back the whole batch if not.
    for w in swapped:
        with open(w["path"], "r", encoding="utf-8") as fh:
            live = fh.read()
        _, issues = wm.scan_day(live, w["date"])
        if [i for i in issues if i["code"] in wm.FATAL_CODES]:
            _rollback(swapped)
            raise RuntimeError(f"post-write validation failed for {w['date']}.md")


def _rollback(swapped: list[dict]) -> None:
    for w in reversed(swapped):
        if w["original"] is not None:
            # Restore atomically so a failed restore can't truncate the original.
            target_dir = os.path.dirname(os.path.abspath(w["path"]))
            fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".rw-rb-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(w["original"])
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, w["path"])
            finally:
                _safe_unlink(tmp)
        else:
            _safe_unlink(w["path"])


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def run(args: argparse.Namespace) -> int:
    payload = _load_input(args.input)
    entries = payload.get("entries", {})
    if not isinstance(entries, dict) or not entries:
        _fail("NO_ENTRIES", "No entries provided to write.")
    meta = payload.get("meta", {}) if isinstance(payload.get("meta"), dict) else {}
    worklog_dir = args.dir or payload.get("worklog_dir") or DEFAULT_DIR

    # Writing day files into a pre-v0.6 flat directory would leave the worklog
    # half in each layout, so refuse and point at the migration instead. Reads
    # of a legacy directory still work; only writes are gated.
    if wm.detect_layout(worklog_dir) == wm.LAYOUT_LEGACY:
        _fail("LEGACY_LAYOUT",
              f"{worklog_dir} still uses the pre-v0.6 flat layout (day files at its "
              f"root, not under {wm.DAYS_SUBDIR}/). Run migrate_legacy_worklog.py "
              "first; this script will not write a mixed-layout directory.",
              worklog_dir=worklog_dir)

    writes = _plan(worklog_dir, entries, meta)

    planned = [{"date": w["date"], "path": w["path"], "action": w["action"],
                "manual_preserved": w["manual_preserved"]} for w in writes]
    summaries = {w["date"]: w["summary"] for w in writes}
    previews = {w["date"]: w["content"] for w in writes}
    file_hashes = {w["date"]: {
        "original": _sha256(w["original"]) if w["original"] is not None else None,
        "preview": _sha256(w["content"]),
    } for w in writes}
    preserved = sorted(w["date"] for w in writes
                       if w["action"] == "overwrite" and w["manual_preserved"])

    common = {
        "worklog_dir": worklog_dir,
        "dir_exists": os.path.isdir(worklog_dir),
        "planned_changes": planned,
        "summaries": summaries,
        "preserved_manual_dates": preserved,
        "file_hashes": file_hashes,
    }

    if not args.apply:
        _emit({
            "ok": True, "mode": "dry-run",
            **common,
            "previews": previews,
            "note": (f"No files have been modified. {worklog_dir}/ is not created "
                     "during dry-run."),
        })
        return 0

    try:
        _transactional_apply(worklog_dir, writes, meta.get("timezone"))
    except Exception as exc:  # noqa: BLE001 — surface any staging/swap failure as JSON
        _fail("WRITE_FAILED", f"Transactional write failed and was rolled back: {exc}",
              worklog_dir=worklog_dir)

    written = [w["date"] for w in writes if w["action"] != "no_change"]
    _emit({
        "ok": True, "mode": "apply",
        **common,
        "written_dates": written,
        "note": ("Day files written atomically as one transaction. "
                 "Run rebuild_worklog_index.py next to refresh index.md. "
                 "No git add / commit / push was performed."),
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Create/overwrite per-day Git Worklog files.")
    p.add_argument("--input", help="Path to entries JSON, or '-' / omit for stdin.")
    p.add_argument("--dir", help=f"Worklog directory (default: {DEFAULT_DIR}).")
    p.add_argument("--apply", action="store_true",
                   help="Write the files. Without this flag the run is a dry-run.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (json.JSONDecodeError, OSError) as exc:
        _fail("IO_ERROR", f"{exc}")
    except Exception as exc:  # never let a traceback replace the single JSON object
        _fail("UNEXPECTED_ERROR", f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
