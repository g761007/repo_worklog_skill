"""Report which dates have worklog coverage, for report mode.

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

Commit counts come from :func:`git_worklog.analysis.history.collect_commits`, so
the self-referential worklog-commit exclusion is inherited: a day whose only
commits edited the worklog directory counts as ``no-commits``, not as a gap.
"""

from __future__ import annotations

import os
from datetime import date as date_cls
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from git_worklog import dates as gwdates
from git_worklog import markers as wm
from git_worklog.analysis import AnalysisError
from git_worklog.analysis import history as cgh


def _has_day_file(worklog_dir: str, date_str: str) -> bool:
    # Resolve against the layout actually on disk, so a not-yet-migrated
    # worklog reports its real coverage instead of a repo-wide false gap.
    return os.path.isfile(wm.day_path(worklog_dir, date_str))


def _parse_dates(raw: str) -> "list[str]":
    parsed = [d.strip() for d in raw.split(",") if d.strip()]
    if not parsed:
        raise AnalysisError("NO_DATES", "Provide at least one date via --dates.")
    for d in parsed:
        # wm.is_valid_date only checks the YYYY-MM-DD shape, so it accepts
        # impossible dates like 2026-13-99; parse to reject those here rather
        # than let them raise out of the day window as a bare traceback.
        if not wm.is_valid_date(d):
            raise AnalysisError("INVALID_DATE", f"Not an ISO YYYY-MM-DD date: {d}.", date=d)
        try:
            date_cls.fromisoformat(d)
        except ValueError:
            raise AnalysisError("INVALID_DATE", f"Not a real calendar date: {d}.", date=d)
    return parsed


def check(repo: str = ".", dir: "str | None" = None, dates: str = "",
          timezone: str = "UTC", date_field: str = "committer",
          worklog_dir: "str | None" = None) -> dict:
    """Coverage for each requested date. Raises AnalysisError or GitError."""
    requested = _parse_dates(dates)
    dir = dir or wm.WORKLOG_DIRNAME
    worklog_dir = worklog_dir or cgh.DEFAULT_WORKLOG_DIR

    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise AnalysisError("INVALID_TIMEZONE", f"Unknown IANA timezone: {timezone}.")

    info = cgh.repo_info(repo)  # raises NOT_A_GIT_REPO when applicable
    resolved_dir = dir if os.path.isabs(dir) else os.path.join(info["root"], dir)
    dir_exists = os.path.isdir(resolved_dir)

    results = []
    for date_str in sorted(set(requested)):
        if info["has_commits"]:
            start, end = gwdates.day_window(date_str, tz)
            commits = cgh.collect_commits(repo, start, end, date_field, worklog_dir)
        else:
            commits = []
        has_worklog = dir_exists and _has_day_file(resolved_dir, date_str)
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
        "worklog_dir": resolved_dir,
        "dir_exists": dir_exists,
        "timezone": timezone,
        "date_field": date_field,
        "dates": results,
        "covered": [r["date"] for r in results if r["status"] == "covered"],
        "gaps": gaps,
        "no_commit_dates": [r["date"] for r in results if r["status"] == "no-commits"],
        "gap_commit_count": sum(r["commit_count"] for r in results
                                if r["status"] == "gap"),
        "fully_covered": not gaps,
    }
