#!/usr/bin/env python3
"""Validate PROJECT_WORKLOG/index.md and its consistency with the day files.

Checks: the INDEX GENERATED and MANUAL regions are present and unique; index
dates are unique and sorted descending; every linked ``./<date>.md`` day file
exists; and the file is valid UTF-8. A day file present on disk but missing from
the index is reported as a warning (rebuild_worklog_index.py fixes it).

Fatal issues set ``ok=false`` and exit 2. Output is a single JSON object.
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


def _disk_dates(worklog_dir: str) -> set[str]:
    if not os.path.isdir(worklog_dir):
        return set()
    return {d for d in (wm.parse_date_filename(n) for n in os.listdir(worklog_dir)) if d}


def validate(worklog_dir: str) -> tuple[dict, bool]:
    index_path = os.path.join(worklog_dir, wm.INDEX_FILENAME)
    try:
        with open(index_path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return ({"ok": False, "target": index_path,
                 "errors": [{"code": "NOT_FOUND", "line": None,
                             "message": f"Index not found: {index_path}"}],
                 "warnings": []}, True)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ({"ok": False, "target": index_path,
                 "errors": [{"code": "NON_UTF8", "line": None,
                             "message": f"File is not valid UTF-8: {exc}"}],
                 "warnings": []}, True)

    doc, issues = wm.scan_index(text)
    fatal = [i for i in issues if i["code"] in wm.FATAL_CODES]
    warnings = [i for i in issues if i["code"] not in wm.FATAL_CODES]

    index_dates = [d for d, _ in doc.rows] if doc else []
    disk_dates = _disk_dates(worklog_dir)

    for date in index_dates:
        if date not in disk_dates:
            fatal.append({"code": "INDEX_LINK_MISSING", "line": None,
                          "message": f"Index links {date}.md but that day file does not exist."})
    for date in sorted(disk_dates - set(index_dates), reverse=True):
        warnings.append({"code": "INDEX_ROW_MISSING", "line": None,
                         "message": f"{date}.md exists on disk but is not listed in the index."})

    return ({
        "ok": not fatal,
        "target": index_path,
        "dates": index_dates,
        "date_count": len(index_dates),
        "sorted_descending": index_dates == sorted(index_dates, reverse=True),
        "errors": fatal,
        "warnings": warnings,
    }, bool(fatal))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Validate PROJECT_WORKLOG/index.md.")
    p.add_argument("--dir", required=True, help="Worklog directory containing index.md.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result, is_fatal = validate(args.dir)
    _emit(result)
    return 2 if is_fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
