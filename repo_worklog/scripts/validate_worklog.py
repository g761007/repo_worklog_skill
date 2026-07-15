#!/usr/bin/env python3
"""Validate the structural integrity of a repo_worklog Markdown file.

Checks: ENTRIES markers balanced, each date block has matching START/END,
GENERATED and MANUAL regions present and unique, dates unique, heading matches
its block date, dates sorted descending, and the file is valid UTF-8.

Reports *every* issue it finds (it does not stop at the first) so a human can
fix corruption in one pass. Fatal issues set ``ok=false`` and exit 2;
non-fatal issues (e.g. ordering) are listed as warnings but keep ``ok=true``.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

import worklog_markers as wm


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def validate(target: str) -> tuple[dict, bool]:
    try:
        with open(target, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return ({"ok": False, "target": target,
                 "errors": [{"code": "NOT_FOUND", "line": None,
                             "message": f"Worklog file not found: {target}"}]}, True)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ({"ok": False, "target": target,
                 "errors": [{"code": "NON_UTF8", "line": None,
                             "message": f"File is not valid UTF-8: {exc}"}]}, True)

    doc, issues = wm.scan(text)
    fatal = [i for i in issues if i["code"] in wm.FATAL_CODES]
    warnings = [i for i in issues if i["code"] not in wm.FATAL_CODES]
    dates = doc.dates() if doc else []

    return ({
        "ok": not fatal,
        "target": target,
        "dates": dates,
        "date_count": len(dates),
        "sorted_descending": dates == sorted(dates, reverse=True),
        "errors": fatal,
        "warnings": warnings,
    }, bool(fatal))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Validate a repo_worklog Markdown file.")
    p.add_argument("--target", required=True, help="Path to the worklog file.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result, is_fatal = validate(args.target)
    _emit(result)
    return 2 if is_fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
