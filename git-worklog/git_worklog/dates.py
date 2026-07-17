"""Resolve Git Worklog date parameters into a canonical per-day range.

This is the deterministic date contract (roadmap §6.1): natural language is
normalised into standard parameters by the model *before* anything here runs, so
this module never interprets free text. It only accepts the canonical parameters
(``date`` / ``days`` / ``from_`` / ``to``) plus the ``NNd`` / bare-date
shortcuts.

The logic lives in the package rather than in ``scripts/`` because only
``git_worklog*`` is packaged: an installed CLI has no ``scripts/`` directory to
shell out to, so anything the CLI needs has to be importable, and the two front
ends must not drift apart.

The per-day boundary uses a half-open interval ``[local 00:00, next 00:00)`` so
that millisecond precision and DST transitions never create gaps or overlaps.
"""

from __future__ import annotations

import os
import re
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Default span cap. It exists to bound per-day subagent cost, so it governs
# worklog generation and backfill. Report mode only reads day files already on
# disk and spawns no subagents, so it overrides this with its own max_days.
MAX_DAYS = 30

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHORTCUT_DAYS_RE = re.compile(r"^(\d+)d$")


class DateError(ValueError):
    """A date parameter that cannot be honoured, carrying the wire code.

    Mirrors :class:`git_worklog.language.LanguageError` and
    :class:`git_worklog.analysis.AnalysisError`: the callers are thin shells (a
    script and, later, a CLI subcommand) that both owe the user one JSON object
    with a stable ``code``, so the code belongs on the exception rather than
    being re-derived from the message at each call site.
    """

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)

    def as_error(self) -> dict:
        """The error dict as it goes on the wire."""
        return {"code": self.code, "message": self.message, **self.extra}


def detect_timezone(override: "str | None") -> "tuple[ZoneInfo, str, str]":
    """Resolve the timezone following the documented priority order.

    Returns ``(tzinfo, iana_name, source)``. ``source`` records where the value
    came from so the dry-run summary can be transparent about it.
    """
    if override:
        try:
            return ZoneInfo(override), override, "explicit"
        except ZoneInfoNotFoundError:
            raise DateError("INVALID_TIMEZONE",
                            f"Unknown IANA timezone: {override!r}.")

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


def _parse_iso_date(value: str, field: str) -> date_cls:
    if not _DATE_RE.match(value):
        raise DateError("INVALID_DATE",
                        f"{field} must be formatted as YYYY-MM-DD (got {value!r}).",
                        field=field, value=value)
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        raise DateError("INVALID_DATE",
                        f"{field} is not a real calendar date: {value!r}.",
                        field=field, value=value)


def day_window(d: "date_cls | str", tz: ZoneInfo) -> "tuple[datetime, datetime]":
    """The half-open ``[local 00:00, next 00:00)`` window for one calendar day.

    This is the day boundary the whole tool agrees on: what a day file covers,
    what a Day Subagent is asked about, and what report mode counts commits in
    all have to be the same window, or a commit falls through the crack between
    two of them.
    """
    if isinstance(d, str):
        d = datetime.fromisoformat(d).date()
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    # timedelta arithmetic operates on wall-clock time and keeps the tzinfo, so
    # the offset is recomputed for the next local midnight even across DST.
    return start, start + timedelta(days=1)


def _day_bounds(d: date_cls, tz: ZoneInfo) -> dict:
    start, end = day_window(d, tz)
    return {
        "date": d.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def absorb_shortcut(token: "str | None", date: "str | None",
                    days: "int | None") -> "tuple[str | None, int | None]":
    """Fold a positional shortcut token into the canonical ``(date, days)``.

    Returns the pair unchanged when there is no token. A shortcut that collides
    with the flag it would set is an error rather than an override: the user
    said the same thing twice and the two may disagree.
    """
    if token is None:
        return date, days
    m = _SHORTCUT_DAYS_RE.match(token)
    if m:
        if days is not None:
            raise DateError("ARG_CONFLICT",
                            f"Shortcut {token!r} conflicts with --days.")
        return date, int(m.group(1))
    if _DATE_RE.match(token):
        if date is not None:
            raise DateError("ARG_CONFLICT",
                            f"Shortcut {token!r} conflicts with --date.")
        return token, days
    raise DateError("UNKNOWN_SHORTCUT",
                    f"Unrecognised shortcut {token!r}; expected NNd or YYYY-MM-DD.",
                    value=token)


def resolve(date: "str | None" = None, days: "int | None" = None,
            from_: "str | None" = None, to: "str | None" = None,
            include_uncommitted: bool = False, timezone: "str | None" = None,
            today: "str | None" = None, max_days: "int | None" = None) -> dict:
    """Resolve the canonical parameters into per-day boundaries.

    Raises :class:`DateError` on any validation failure.
    """
    tz, tz_name, tz_source = detect_timezone(timezone)
    # Explicit None check: `or MAX_DAYS` would treat max_days=0 as unset and
    # silently restore the default instead of rejecting it.
    if max_days is None:
        max_days = MAX_DAYS
    if max_days < 1:
        raise DateError("BAD_MAX_DAYS",
                        f"max-days must be at least 1 (got {max_days}).",
                        max_days=max_days)

    today_date = _parse_iso_date(today, "today") if today else datetime.now(tz).date()

    has_date = date is not None
    has_days = days is not None
    has_from = from_ is not None
    has_to = to is not None
    has_range = has_from or has_to

    # Mutual exclusivity: date / days / (from+to) are three separate modes.
    active = sum([has_date, has_days, has_range])
    if active == 0:
        raise DateError("NO_DATE_SPEC",
                        "No date parameter given. Provide date, days, or from+to.")
    if active > 1:
        raise DateError("ARG_CONFLICT",
                        "date, days and from/to are mutually exclusive; supply exactly one mode.",
                        provided={"date": date, "days": days,
                                  "from": from_, "to": to})

    common = {
        "ok": True,
        "timezone": tz_name,
        "timezone_source": tz_source,
        "include_uncommitted": bool(include_uncommitted),
        "today": today_date.isoformat(),
        "max_days": max_days,
        "errors": [],
    }

    if has_date:
        d = _parse_iso_date(date, "date")
        return {**common, "mode": "date", "days_count": 1,
                "dates": [_day_bounds(d, tz)]}

    if has_days:
        if not isinstance(days, int) or days < 1 or days > max_days:
            raise DateError("DAYS_OUT_OF_RANGE",
                            f"days must be an integer between 1 and {max_days} (got {days}).",
                            requested_days=days, max_days=max_days)
        start_day = today_date - timedelta(days=days - 1)
        dates = [_day_bounds(start_day + timedelta(days=i), tz) for i in range(days)]
        return {**common, "mode": "days", "days_count": days, "dates": dates}

    # range mode
    if has_from and not has_to:
        raise DateError("FROM_WITHOUT_TO", "from must be accompanied by to.")
    if has_to and not has_from:
        raise DateError("TO_WITHOUT_FROM",
                        "to must not be used on its own; provide from as well.")

    d_from = _parse_iso_date(from_, "from")
    d_to = _parse_iso_date(to, "to")
    if d_from > d_to:
        raise DateError("FROM_AFTER_TO",
                        f"from ({d_from}) must not be later than to ({d_to}).")
    total = (d_to - d_from).days + 1
    if total > max_days:
        raise DateError("TOO_MANY_DAYS",
                        f"The requested range spans {total} days, exceeding the "
                        f"{max_days}-day limit. Narrow the range to {max_days} days or fewer.",
                        requested_days=total, max_days=max_days)
    dates = [_day_bounds(d_from + timedelta(days=i), tz) for i in range(total)]
    return {**common, "mode": "range", "days_count": total, "dates": dates}
