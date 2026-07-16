#!/usr/bin/env python3
"""Resolve and validate Git Worklog date parameters into a canonical range.

This script is the deterministic date contract for the ``git-worklog`` skill.
Natural language is normalised into standard parameters by the model *before*
this script runs; the script itself never interprets free text. It only accepts
the canonical parameters (``date`` / ``days`` / ``from`` / ``to``) plus the
``7d`` / ``30d`` / bare-date shortcuts.

Output is a single JSON object on stdout. On success ``ok`` is ``true`` and the
resolved per-day boundaries are returned. On any validation failure ``ok`` is
``false``, ``errors`` describes what went wrong, and the process exits 2.

The per-day boundary uses a half-open interval ``[local 00:00, next 00:00)`` so
that millisecond precision and DST transitions never create gaps or overlaps.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Default span cap. It exists to bound per-day subagent cost, so it governs
# worklog generation and backfill. Report mode only reads day files already on
# disk and spawns no subagents, so it overrides this with --max-days.
MAX_DAYS = 30
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHORTCUT_DAYS_RE = re.compile(r"^(\d+)d$")


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(errors: list[dict], **extra) -> None:
    _emit({"ok": False, "errors": errors, **extra})
    sys.exit(2)


def detect_timezone(override: str | None) -> tuple[ZoneInfo, str, str]:
    """Resolve the timezone following the documented priority order.

    Returns ``(tzinfo, iana_name, source)``. ``source`` records where the value
    came from so the dry-run summary can be transparent about it.
    """
    if override:
        try:
            return ZoneInfo(override), override, "explicit"
        except ZoneInfoNotFoundError:
            _fail([{
                "code": "INVALID_TIMEZONE",
                "message": f"Unknown IANA timezone: {override!r}.",
            }])

    tz_env = os.environ.get("TZ")
    if tz_env:
        try:
            return ZoneInfo(tz_env), tz_env, "env:TZ"
        except ZoneInfoNotFoundError:
            pass

    localtime = "/etc/localtime"
    if os.path.islink(localtime):
        target = os.path.realpath(localtime)
        marker = "/zoneinfo/"
        if marker in target:
            name = target.split(marker, 1)[1]
            try:
                return ZoneInfo(name), name, "system:/etc/localtime"
            except ZoneInfoNotFoundError:
                pass

    # Last resort: a fixed offset with no IANA name. The orchestrator should
    # surface this and, per the contract, may ask the user to specify one.
    local = datetime.now().astimezone().tzinfo
    name = getattr(local, "key", None) or (local.tzname(None) if local else "UTC") or "UTC"
    return local, name, "system-offset"


def _parse_iso_date(value: str, field: str) -> date:
    if not _DATE_RE.match(value):
        _fail([{
            "code": "INVALID_DATE",
            "field": field,
            "value": value,
            "message": f"{field} must be formatted as YYYY-MM-DD (got {value!r}).",
        }])
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail([{
            "code": "INVALID_DATE",
            "field": field,
            "value": value,
            "message": f"{field} is not a real calendar date: {value!r}.",
        }])


def _day_bounds(d: date, tz: ZoneInfo) -> dict:
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    # timedelta arithmetic operates on wall-clock time and keeps the tzinfo, so
    # the offset is recomputed for the next local midnight even across DST.
    end = start + timedelta(days=1)
    return {
        "date": d.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def _absorb_shortcut(args: argparse.Namespace) -> None:
    """Fold a positional shortcut token into the canonical flags."""
    token = args.shortcut
    if token is None:
        return
    m = _SHORTCUT_DAYS_RE.match(token)
    if m:
        if args.days is not None:
            _fail([{"code": "ARG_CONFLICT",
                    "message": f"Shortcut {token!r} conflicts with --days."}])
        args.days = int(m.group(1))
        return
    if _DATE_RE.match(token):
        if args.date is not None:
            _fail([{"code": "ARG_CONFLICT",
                    "message": f"Shortcut {token!r} conflicts with --date."}])
        args.date = token
        return
    _fail([{
        "code": "UNKNOWN_SHORTCUT",
        "value": token,
        "message": f"Unrecognised shortcut {token!r}; expected NNd or YYYY-MM-DD.",
    }])


def resolve(args: argparse.Namespace) -> dict:
    tz, tz_name, tz_source = detect_timezone(args.timezone)
    max_days = getattr(args, "max_days", None)
    # Explicit None check: `or MAX_DAYS` would treat --max-days 0 as unset and
    # silently restore the default instead of rejecting it.
    if max_days is None:
        max_days = MAX_DAYS
    if max_days < 1:
        _fail([{
            "code": "BAD_MAX_DAYS",
            "max_days": max_days,
            "message": f"max-days must be at least 1 (got {max_days}).",
        }])

    today = (
        _parse_iso_date(args.today, "today")
        if args.today
        else datetime.now(tz).date()
    )

    has_date = args.date is not None
    has_days = args.days is not None
    has_from = getattr(args, "from_") is not None
    has_to = args.to is not None
    has_range = has_from or has_to

    # Mutual exclusivity: date / days / (from+to) are three separate modes.
    active = sum([has_date, has_days, has_range])
    if active == 0:
        _fail([{
            "code": "NO_DATE_SPEC",
            "message": "No date parameter given. Provide date, days, or from+to.",
        }])
    if active > 1:
        _fail([{
            "code": "ARG_CONFLICT",
            "message": "date, days and from/to are mutually exclusive; supply exactly one mode.",
            "provided": {"date": args.date, "days": args.days,
                         "from": args.from_, "to": args.to},
        }])

    include_uncommitted = bool(args.include_uncommitted)
    common = {
        "ok": True,
        "timezone": tz_name,
        "timezone_source": tz_source,
        "include_uncommitted": include_uncommitted,
        "today": today.isoformat(),
        "max_days": max_days,
        "errors": [],
    }

    if has_date:
        d = _parse_iso_date(args.date, "date")
        days = [_day_bounds(d, tz)]
        return {**common, "mode": "date", "days_count": 1, "dates": days}

    if has_days:
        if not isinstance(args.days, int) or args.days < 1 or args.days > max_days:
            _fail([{
                "code": "DAYS_OUT_OF_RANGE",
                "requested_days": args.days,
                "max_days": max_days,
                "message": f"days must be an integer between 1 and {max_days} (got {args.days}).",
            }])
        start_day = today - timedelta(days=args.days - 1)
        days = [_day_bounds(start_day + timedelta(days=i), tz)
                for i in range(args.days)]
        return {**common, "mode": "days", "days_count": args.days, "dates": days}

    # range mode
    if has_from and not has_to:
        _fail([{"code": "FROM_WITHOUT_TO",
                "message": "from must be accompanied by to."}])
    if has_to and not has_from:
        _fail([{"code": "TO_WITHOUT_FROM",
                "message": "to must not be used on its own; provide from as well."}])

    d_from = _parse_iso_date(args.from_, "from")
    d_to = _parse_iso_date(args.to, "to")
    if d_from > d_to:
        _fail([{
            "code": "FROM_AFTER_TO",
            "message": f"from ({d_from}) must not be later than to ({d_to}).",
        }])
    total = (d_to - d_from).days + 1
    if total > max_days:
        _fail([{
            "code": "TOO_MANY_DAYS",
            "requested_days": total,
            "max_days": max_days,
            "message": (f"The requested range spans {total} days, exceeding the "
                        f"{max_days}-day limit. Narrow the range to {max_days} days or fewer."),
        }])
    days = [_day_bounds(d_from + timedelta(days=i), tz) for i in range(total)]
    return {**common, "mode": "range", "days_count": total, "dates": days}


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
    _absorb_shortcut(args)
    _emit(resolve(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
