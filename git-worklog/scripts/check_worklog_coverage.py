#!/usr/bin/env python3
"""Report which dates have worklog coverage, for Git Worklog report mode.

The engine is :mod:`git_worklog.analysis.coverage`; this is the command-line
shell around it. The logic moved into the package because only ``git_worklog*``
is packaged -- an installed CLI has no ``scripts/`` directory to reach for --
and the two front ends must not drift apart.

Report mode answers questions from the day files already on disk, so it has to
know which requested dates actually have an analysis behind them. A date with no
worklog file is **not** automatically a gap: a day with no commits is
deliberately given no file. See the module docstring for the covered / gap /
no-commits distinction.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

import worklog_markers as wm

from git_worklog.analysis import AnalysisError
from git_worklog.analysis import coverage
from git_worklog.analysis import history as cgh


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


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
        _emit(coverage.check(
            repo=args.repo, dir=args.dir, dates=args.dates,
            timezone=args.timezone, date_field=args.date_field,
            worklog_dir=args.worklog_dir,
        ))
        return 0
    except AnalysisError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except cgh.GitError as exc:
        _fail("GIT_ERROR", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
