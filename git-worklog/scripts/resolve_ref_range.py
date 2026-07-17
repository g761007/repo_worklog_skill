#!/usr/bin/env python3
"""Resolve a tag/ref range into its authoritative commit set for report mode.

The engine is :mod:`git_worklog.analysis.refs`; this is the command-line shell
around it. The logic moved into the package because only ``git_worklog*`` is
packaged -- an installed CLI has no ``scripts/`` directory to reach for -- and
the two front ends must not drift apart.

Used by the ``git-worklog`` skill's report mode when the user asks about a
*version* ("整理 v1.0.1 CHANGELOG") rather than a *date range*. The commit set it
emits is the authority; the ``dates`` it derives only locate the day files worth
reading. See the module docstring for why the two do not map onto each other.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog.analysis import AnalysisError
from git_worklog.analysis import refs


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve a tag/ref range into its commit set for Git Worklog "
                    "report mode.")
    p.add_argument("--repo", default=".", help="Repository path (default: current directory).")
    p.add_argument("--tag", help="Tag to report on; the previous tag is found automatically.")
    p.add_argument("--from-ref", help="Explicit range start, exclusive (any ref).")
    p.add_argument("--to-ref", help="Explicit range end, inclusive (any ref).")
    p.add_argument("--list-tags", action="store_true",
                   help="List the repository's tags, newest-first.")
    p.add_argument("--timezone", default="UTC",
                   help="IANA timezone deciding each commit's calendar day (default: UTC).")
    p.add_argument("--date-field", choices=["committer", "author"], default="committer",
                   help="Which date decides day attribution (default: committer, "
                        "matching collect_git_history.py).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _emit(refs.resolve(
            repo=args.repo, tag=args.tag, from_ref=args.from_ref,
            to_ref=args.to_ref, list_tags_only=args.list_tags,
            timezone=args.timezone, date_field=args.date_field,
        ))
        return 0
    except AnalysisError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except refs.GitError as exc:
        _fail("GIT_ERROR", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
