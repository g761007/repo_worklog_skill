#!/usr/bin/env python3
"""Report which dates have worklog coverage, for Git Worklog report mode.

Report mode answers questions from the day files already on disk. Before it can
do that honestly it has to know which of the requested dates actually have an
analysis behind them -- otherwise it quietly degrades into summarising commit
messages, which is the one thing this skill exists not to do.

The distinction that matters
----------------------------
A date with no worklog file is **not** automatically a gap. Per
``references/worklog-format.md`` §6, a day with no commits is deliberately given
no file -- the directory is not padded with empty days. So each date is one of:

* ``covered``    -- has commits and has a day file. Usable material.
* ``gap``        -- has commits but no day file. **A real gap**: real work exists
                    that nothing has analysed.
* ``no-commits`` -- no commits, so no file is expected. Nothing is missing.

Conflating ``no-commits`` with ``gap`` would send the user off to backfill days
that can never produce a file.

Commit counts come from ``collect_git_history.collect_commits``, so the
self-referential worklog-commit exclusion is inherited: a day whose only commits
edited ``PROJECT_WORKLOG/`` counts as ``no-commits``, not as a gap.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import collect_git_history as cgh
import worklog_markers as wm


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _day_bounds(date_str: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Half-open [local 00:00, next 00:00), matching resolve_date_range.py."""
    d = datetime.fromisoformat(date_str).date()
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start, end


def _has_day_file(worklog_dir: str, date_str: str) -> bool:
    return os.path.isfile(os.path.join(worklog_dir, f"{date_str}.md"))


def check(args: argparse.Namespace) -> dict:
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    if not dates:
        _fail("NO_DATES", "Provide at least one date via --dates.")
    for d in dates:
        # wm.is_valid_date only checks the YYYY-MM-DD shape, so it accepts
        # impossible dates like 2026-13-99; parse to reject those here rather
        # than let them raise out of _day_bounds as a bare traceback.
        if not wm.is_valid_date(d):
            _fail("INVALID_DATE", f"Not an ISO YYYY-MM-DD date: {d}.", date=d)
        try:
            date_cls.fromisoformat(d)
        except ValueError:
            _fail("INVALID_DATE", f"Not a real calendar date: {d}.", date=d)

    try:
        tz = ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        _fail("INVALID_TIMEZONE", f"Unknown IANA timezone: {args.timezone}.")

    info = cgh.repo_info(args.repo)  # exits with NOT_A_GIT_REPO when applicable
    worklog_dir = args.dir if os.path.isabs(args.dir) else os.path.join(
        info["root"], args.dir)
    dir_exists = os.path.isdir(worklog_dir)

    results = []
    for date_str in sorted(set(dates)):
        if info["has_commits"]:
            start, end = _day_bounds(date_str, tz)
            commits = cgh.collect_commits(args.repo, start, end, args.date_field,
                                          args.worklog_dir)
        else:
            commits = []
        has_worklog = dir_exists and _has_day_file(worklog_dir, date_str)
        if not commits:
            status = "no-commits"
        elif has_worklog:
            status = "covered"
        else:
            status = "gap"
        results.append({
            "date": date_str,
            "commit_count": len(commits),
            "has_worklog": has_worklog,
            "status": status,
        })

    gaps = [r["date"] for r in results if r["status"] == "gap"]
    return {
        "ok": True,
        "repository": {"root": info["root"], "has_commits": info["has_commits"]},
        "worklog_dir": worklog_dir,
        "dir_exists": dir_exists,
        "timezone": args.timezone,
        "date_field": args.date_field,
        "dates": results,
        "covered": [r["date"] for r in results if r["status"] == "covered"],
        "gaps": gaps,
        "no_commit_dates": [r["date"] for r in results if r["status"] == "no-commits"],
        "gap_commit_count": sum(r["commit_count"] for r in results
                                if r["status"] == "gap"),
        "fully_covered": not gaps,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Report per-date worklog coverage for Git Worklog report mode.")
    p.add_argument("--repo", default=".", help="Repository path (default: current directory).")
    p.add_argument("--dir", default=wm.WORKLOG_DIRNAME,
                   help=f"Worklog directory, absolute or relative to the repo root "
                        f"(default: {wm.WORKLOG_DIRNAME}).")
    p.add_argument("--dates", required=True,
                   help="Comma-separated ISO dates to check, e.g. 2026-07-01,2026-07-02.")
    p.add_argument("--timezone", default="UTC",
                   help="IANA timezone deciding each day's bounds (default: UTC).")
    p.add_argument("--date-field", choices=["committer", "author"], default="committer",
                   help="Which date decides day attribution (default: committer).")
    p.add_argument("--worklog-dir", default=cgh.DEFAULT_WORKLOG_DIR,
                   help="Repo-relative worklog path whose commits are treated as "
                        "self-referential and excluded from the counts "
                        f"(default: {cgh.DEFAULT_WORKLOG_DIR}).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _emit(check(args))
        return 0
    except cgh.GitError as exc:
        _fail("GIT_ERROR", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
