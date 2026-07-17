#!/usr/bin/env python3
"""Collect Git repository metadata and per-day commit facts for Git Worklog.

The engine is :mod:`git_worklog.analysis.history`; this is the command-line
shell around it. The logic moved into the package because only ``git_worklog*``
is packaged -- an installed ``git-worklog analyze`` has no ``scripts/``
directory to reach for -- and the two front ends must not drift apart.

Modes:
  --info-only            Emit only repository metadata (root, branch, HEAD, ...).
  --since ISO --until ISO Emit metadata + commits inside [since, until).

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog.analysis import AnalysisError
from git_worklog.analysis import history

DEFAULT_WORKLOG_DIR = history.DEFAULT_WORKLOG_DIR


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Collect Git history facts for Git Worklog.")
    p.add_argument("--repo", default=".", help="Repository path (default: current directory).")
    p.add_argument("--since", help="Day window start, ISO 8601 with offset (inclusive).")
    p.add_argument("--until", help="Day window end, ISO 8601 with offset (exclusive).")
    p.add_argument("--date-field", choices=["committer", "author"], default="committer",
                   help="Which date decides day attribution (default: committer).")
    p.add_argument("--info-only", action="store_true",
                   help="Emit repository metadata only; skip commit collection.")
    p.add_argument("--worklog-dir", default=DEFAULT_WORKLOG_DIR,
                   help="Worklog output directory; commits touching only this "
                        "directory are excluded as self-referential "
                        f"(default: {DEFAULT_WORKLOG_DIR}).")
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _emit(history.collect(
            repo=args.repo, since=args.since, until=args.until,
            date_field=args.date_field, info_only=args.info_only,
            worklog_dir=args.worklog_dir,
        ))
        return 0
    except AnalysisError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except history.GitError as exc:
        _fail("GIT_ERROR", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
