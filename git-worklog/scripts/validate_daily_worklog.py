#!/usr/bin/env python3
"""Validate the structural integrity of per-day Git Worklog files.

For one file (``--target PROJECT_WORKLOG/2026-07-15.md``) or every date file in a
directory (``--dir PROJECT_WORKLOG``), this checks: the filename is a valid
``<date>.md``, the ``# Project Worklog — <date>`` title matches, the GENERATED
and MANUAL regions are present, unique and correctly ordered, every marker's date
matches the file's date, and the file is valid UTF-8.

Reports *every* issue it finds (it does not stop at the first). Fatal issues set
``ok=false`` and exit 2. Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import worklog_markers as wm


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _validate_file(path: str) -> dict:
    name = os.path.basename(path)
    date = wm.parse_date_filename(name)
    if date is None:
        return {"target": path, "ok": False,
                "errors": [{"code": "INVALID_FILENAME", "line": None,
                            "message": f"{name!r} is not a valid <YYYY-MM-DD>.md day file."}],
                "warnings": []}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return {"target": path, "ok": False,
                "errors": [{"code": "NOT_FOUND", "line": None,
                            "message": f"Day file not found: {path}"}], "warnings": []}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {"target": path, "ok": False,
                "errors": [{"code": "NON_UTF8", "line": None,
                            "message": f"File is not valid UTF-8: {exc}"}], "warnings": []}

    _, issues = wm.scan_day(text, date)
    fatal = [i for i in issues if i["code"] in wm.FATAL_CODES]
    warnings = [i for i in issues if i["code"] not in wm.FATAL_CODES]
    return {"target": path, "date": date, "ok": not fatal,
            "errors": fatal, "warnings": warnings}


def _day_files(worklog_dir: str) -> list[str]:
    if not os.path.isdir(worklog_dir):
        return []
    layout = wm.detect_layout(worklog_dir)
    return [wm.day_path(worklog_dir, d, layout)
            for d in sorted(wm.list_day_dates(worklog_dir, layout), reverse=True)]


def run(args: argparse.Namespace) -> tuple[dict, bool]:
    if args.target:
        result = _validate_file(args.target)
        return result, not result["ok"]

    files = _day_files(args.dir)
    results = [_validate_file(p) for p in files]
    any_fatal = any(not r["ok"] for r in results)
    return ({"ok": not any_fatal, "worklog_dir": args.dir,
             "file_count": len(results), "files": results}, any_fatal)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Validate per-day Git Worklog files.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", help="Path to a single <date>.md day file.")
    g.add_argument("--dir", help="Worklog directory to validate every day file in.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result, is_fatal = run(args)
    _emit(result)
    return 2 if is_fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
