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

from git_worklog import dates as gwdates
from git_worklog import markers as wm
from git_worklog.analysis import AnalysisError
from git_worklog.analysis import history as cgh

# Report mode's day cap. Generation is bounded at 30 because each day costs a
# subagent; report mode only reads day files that already exist, so the thing
# that cap protects is not at stake and a quarter is a reasonable question to
# ask of a worklog.
REPORT_MAX_DAYS = 90


def _has_day_file(worklog_dir: str, date_str: str) -> bool:
    # Resolve against the layout actually on disk, so a not-yet-migrated
    # worklog reports its real coverage instead of a repo-wide false gap.
    return os.path.isfile(wm.day_path(worklog_dir, date_str))


def _parse_dates(raw: str) -> "list[str]":
    """An explicit ``a,b,c`` list. Ref scope's dates are a set, not a range."""
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
          timezone: "str | None" = None, date_field: str = "committer",
          worklog_dir: "str | None" = None, shortcut: "str | None" = None,
          date: "str | None" = None, days: "int | None" = None,
          from_: "str | None" = None, to: "str | None" = None,
          today: "str | None" = None,
          max_days: "int | None" = None) -> dict:
    """Coverage for the requested dates. Raises AnalysisError or GitError.

    Two ways to say which dates, because report mode has two scopes and they are
    shaped differently. A *date* scope is a range, so it is given the same way
    `analyze prepare` takes one and resolved by the same contract. A *ref* scope
    is whatever days a tag's commits happen to fall on — an arbitrary set, with
    gaps — so it arrives as an explicit ``dates`` list instead.
    """
    if dates and (shortcut or date or days is not None or from_ or to):
        raise AnalysisError(
            "ARG_CONFLICT",
            "--dates lists exact days; the range flags resolve a span. Pass one.")

    dir = dir or wm.WORKLOG_DIRNAME
    worklog_dir = worklog_dir or cgh.DEFAULT_WORKLOG_DIR

    if dates:
        requested = _parse_dates(dates)
        # An explicit list still needs a zone to know where its days start.
        # UTC rather than the machine's: the caller of this form is ref scope,
        # which already resolved these dates under a zone it chose, and guessing
        # a different one here would silently re-cut the day boundaries.
        tz, tz_name, tz_source = gwdates.detect_timezone(timezone or "UTC")
    else:
        # The same date contract generation mode uses, with report mode's own
        # cap: it reads day files already on disk and spawns no subagents, so
        # the 30-day bound on subagent cost does not apply to it.
        d, n = gwdates.absorb_shortcut(shortcut, date, days)
        resolved = gwdates.resolve(
            date=d, days=n, from_=from_, to=to, timezone=timezone, today=today,
            max_days=REPORT_MAX_DAYS if max_days is None else max_days)
        requested = [e["date"] for e in resolved["dates"]]
        tz, tz_name, tz_source = gwdates.detect_timezone(resolved["timezone"])

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
        "timezone": {"resolved": tz_name, "source": tz_source},
        "date_field": date_field,
        "dates": results,
        "covered": [r["date"] for r in results if r["status"] == "covered"],
        "gaps": gaps,
        "no_commit_dates": [r["date"] for r in results if r["status"] == "no-commits"],
        "gap_commit_count": sum(r["commit_count"] for r in results
                                if r["status"] == "gap"),
        "fully_covered": not gaps,
    }
