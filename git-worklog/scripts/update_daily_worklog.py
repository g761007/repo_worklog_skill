#!/usr/bin/env python3
"""Simulate or apply Git Worklog updates to per-day files under .git-worklog/.

A thin CLI shell over :mod:`git_worklog.writer`, which owns the planning and the
transactional write. Given ``{date: generated_markdown}`` entries, it computes
each target day file's new content — creating a fresh file or overwriting only
the GENERATED region of an existing one, always preserving that day's MANUAL
region. Each day lives in its own ``.git-worklog/days/<date>.md`` file; no other
date file is ever read or rewritten.

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
import json
import os
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog import writer

DEFAULT_DIR = writer.DEFAULT_DIR


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _load_input(path: str | None) -> dict:
    raw = open(path, "r", encoding="utf-8").read() if path and path != "-" else sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def run(args: argparse.Namespace) -> int:
    payload = _load_input(args.input)
    entries = payload.get("entries", {})
    if not isinstance(entries, dict) or not entries:
        _fail("NO_ENTRIES", "No entries provided to write.")
    meta = payload.get("meta", {}) if isinstance(payload.get("meta"), dict) else {}
    worklog_dir = args.dir or payload.get("worklog_dir") or DEFAULT_DIR

    writer.check_layout(worklog_dir)
    writes = writer.plan_days(worklog_dir, entries, meta)

    common = {
        "worklog_dir": worklog_dir,
        "dir_exists": os.path.isdir(worklog_dir),
        **writer.day_report(writes),
    }

    if not args.apply:
        _emit({
            "ok": True, "mode": "dry-run",
            **common,
            "previews": {w["date"]: w["content"] for w in writes},
            "note": (f"No files have been modified. {worklog_dir}/ is not created "
                     "during dry-run."),
        })
        return 0

    try:
        writer.apply_days(worklog_dir, writes, meta.get("timezone"))
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
    except writer.WriterError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except (json.JSONDecodeError, OSError) as exc:
        _fail("IO_ERROR", f"{exc}")
    except Exception as exc:  # never let a traceback replace the single JSON object
        _fail("UNEXPECTED_ERROR", f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
