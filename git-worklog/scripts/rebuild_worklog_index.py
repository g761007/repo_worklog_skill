#!/usr/bin/env python3
"""Rebuild .git-worklog/index.md from the per-day files.

A thin CLI shell over :mod:`git_worklog.writer`, which owns the rebuild. The
index is navigation only: a date-descending table linking every day file, each
row carrying that day's one-line summary (its 當日摘要). The rebuild scans the
directory for valid date files, derives each summary, rebuilds the GENERATED
table, and preserves the index's MANUAL region byte-for-byte. Files that are not
``<date>.md`` (including ``index.md`` itself) are ignored.

Dry-run is the default. For a dry-run that reflects pending day-file writes not
yet on disk, pass ``{"overrides": {"<date>": "summary", ...}}`` — an override
supplies (or replaces) a date's summary and adds dates that will exist after the
day-file apply. ``--apply`` writes index.md atomically.

Input (``--input FILE`` or stdin, optional):
    {"overrides": {"2026-07-15": "新增會員搜尋快取", ...}}

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
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


def _load_input(path: str | None, apply_mode: bool = False) -> dict:
    # In apply mode the day files are already on disk and the index is a pure
    # function of them, so there is nothing to read. Reading stdin here would
    # hang forever in any non-interactive environment -- an agent harness, CI,
    # or cron -- where stdin is neither a TTY nor closed. isatty() alone is not
    # a sufficient guard, and SKILL.md §8 documents `--apply` with no stdin.
    if apply_mode and path is None:
        return {}
    if path is None and sys.stdin.isatty():
        return {}
    raw = open(path, "r", encoding="utf-8").read() if path and path != "-" else sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def run(args: argparse.Namespace) -> int:
    worklog_dir = args.dir or DEFAULT_DIR
    payload = _load_input(args.input, apply_mode=bool(args.apply))
    overrides = payload.get("overrides", {}) if isinstance(payload.get("overrides"), dict) else {}

    plan = writer.plan_index(worklog_dir, overrides, args.language)
    content = plan["content"]
    common = {k: v for k, v in plan.items() if k not in ("original", "content")}

    if not args.apply:
        _emit({"ok": True, "mode": "dry-run", **common, "preview": content,
               "note": "No files have been modified."})
        return 0

    writer.apply_index(plan["index_path"], content)
    with open(plan["index_path"], "r", encoding="utf-8") as fh:
        written = fh.read()
    _emit({"ok": True, "mode": "apply", **common,
           "written_sha256": writer.sha256(written),
           "note": "index.md written atomically. No git add / commit / push was performed."})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Rebuild .git-worklog/index.md from day files.")
    p.add_argument("--input", help="Optional overrides JSON, or '-' for stdin.")
    p.add_argument("--dir", help=f"Worklog directory (default: {DEFAULT_DIR}).")
    p.add_argument("--language", default=None,
                   help="This run's language, used only when the index does "
                        "not exist yet. An index that already has a language "
                        "keeps it (§6.2.12).")
    p.add_argument("--apply", action="store_true",
                   help="Write index.md. Without this flag the run is a dry-run.")
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
