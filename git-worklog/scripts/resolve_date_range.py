#!/usr/bin/env python3
"""Resolve and validate Git Worklog date parameters into a canonical range.

The engine is :mod:`git_worklog.dates`; this is the command-line shell around
it. The logic moved into the package because only ``git_worklog*`` is packaged
-- an installed CLI has no ``scripts/`` directory to reach for -- and the two
front ends must not drift apart.

Natural language is normalised into standard parameters by the model *before*
this script runs; it never interprets free text. It only accepts the canonical
parameters (``date`` / ``days`` / ``from`` / ``to``) plus the ``NNd`` /
bare-date shortcuts.

Output is a single JSON object on stdout. On success ``ok`` is ``true`` and the
resolved per-day boundaries are returned. On any validation failure ``ok`` is
``false``, ``errors`` describes what went wrong, and the process exits 2.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog import dates

MAX_DAYS = dates.MAX_DAYS


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(errors: list[dict]) -> None:
    _emit({"ok": False, "errors": errors})
    sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Resolve Git Worklog date parameters.")
    p.add_argument("shortcut", nargs="?", help="Shortcut token: NNd or YYYY-MM-DD.")
    p.add_argument("--date", help="Single calendar date (YYYY-MM-DD).")
    p.add_argument("--days", type=int, help="Most recent N calendar days including today (1-30).")
    p.add_argument("--from", dest="from_", help="Range start (inclusive, YYYY-MM-DD).")
    p.add_argument("--to", help="Range end (inclusive, YYYY-MM-DD).")
    p.add_argument("--include-uncommitted", action="store_true",
                   help="Include working tree changes (recorded, applied only to today).")
    p.add_argument("--timezone", help="Explicit IANA timezone override, e.g. Asia/Taipei.")
    p.add_argument("--today", help="Override today's date (YYYY-MM-DD) for deterministic runs.")
    p.add_argument("--max-days", type=int, default=MAX_DAYS,
                   help=f"Maximum span in calendar days (default: {MAX_DAYS}). The "
                        "default bounds per-day subagent cost and applies to worklog "
                        "generation and backfill. Report mode reads existing day files "
                        "and spawns no subagents, so it raises this cap.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        date, days = dates.absorb_shortcut(args.shortcut, args.date, args.days)
        _emit(dates.resolve(
            date=date, days=days, from_=args.from_, to=args.to,
            include_uncommitted=args.include_uncommitted,
            timezone=args.timezone, today=args.today, max_days=args.max_days,
        ))
        return 0
    except dates.DateError as exc:
        _fail([exc.as_error()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
