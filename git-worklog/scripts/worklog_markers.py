#!/usr/bin/env python3
"""Shared parser / serialiser for the Git Worklog directory-based worklog.

The worklog is no longer a single growing file. It is a directory:

    PROJECT_WORKLOG/
    ├── index.md
    ├── 2026-07-15.md
    ├── 2026-07-14.md
    └── ...

Two on-disk shapes are managed here, each with stable HTML-comment markers that
use the prefix ``REPO_WORKLOG``.

**A per-day file** (``PROJECT_WORKLOG/<date>.md``) holds exactly one day. Its
title and meta blockquote are tool-owned (regenerated on every write); only the
MANUAL region is human-owned and preserved byte-for-byte::

    # Project Worklog — 2026-07-15

    > 時區：Asia/Taipei
    > Branch：main
    > HEAD：abc1234

    <!-- REPO_WORKLOG:2026-07-15:GENERATED:START -->
    ...auto-generated...
    <!-- REPO_WORKLOG:2026-07-15:GENERATED:END -->

    <!-- REPO_WORKLOG:2026-07-15:MANUAL:START -->
    ...human notes...
    <!-- REPO_WORKLOG:2026-07-15:MANUAL:END -->

**The index** (``PROJECT_WORKLOG/index.md``) is navigation only. Its GENERATED
region is a date-descending table rebuilt from the day files; its MANUAL region
is preserved verbatim::

    <!-- REPO_WORKLOG:INDEX:GENERATED:START -->
    | 日期 | 摘要 |
    |---|---|
    | [2026-07-15](./2026-07-15.md) | ... |
    <!-- REPO_WORKLOG:INDEX:GENERATED:END -->

    <!-- REPO_WORKLOG:INDEX:MANUAL:START -->
    ...human notes...
    <!-- REPO_WORKLOG:INDEX:MANUAL:END -->

update_daily_worklog.py, rebuild_worklog_index.py, validate_daily_worklog.py,
validate_worklog_index.py and preview_state.py all build on this module so they
agree on exactly one definition of the format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = "REPO_WORKLOG"
WORKLOG_DIRNAME = "PROJECT_WORKLOG"
INDEX_FILENAME = "index.md"

# --- regexes -----------------------------------------------------------------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

_DAY_MARKER_RE = re.compile(
    rf"^<!--\s*{PREFIX}:(\d{{4}}-\d{{2}}-\d{{2}}):(GENERATED|MANUAL):(START|END)\s*-->$"
)
_INDEX_MARKER_RE = re.compile(
    rf"^<!--\s*{PREFIX}:INDEX:(GENERATED|MANUAL):(START|END)\s*-->$"
)
# Accept an em dash or a plain hyphen between the title and the date.
_DAY_TITLE_RE = re.compile(r"^#\s+Project Worklog\s+[—–-]\s+(\d{4}-\d{2}-\d{2})\s*$")
_SUMMARY_HEADING_RE = re.compile(r"^#{1,6}\s*當日摘要\s*$")
_INDEX_ROW_RE = re.compile(r"^\|\s*\[(\d{4}-\d{2}-\d{2})\]\([^)]*\)\s*\|(.*)\|\s*$")

# Fatal validation codes shared by both validators.
FATAL_CODES = {
    "MISSING_GENERATED", "MISSING_MANUAL", "DUPLICATE_GENERATED",
    "DUPLICATE_MANUAL", "GENERATED_UNCLOSED", "MANUAL_UNCLOSED",
    "ORDER_MANUAL_BEFORE_GENERATED", "MARKER_DATE_MISMATCH",
    "TITLE_MISSING", "TITLE_DATE_MISMATCH", "STRAY_DATE_MARKER",
    "INDEX_MISSING_GENERATED", "INDEX_MISSING_MANUAL",
    "INDEX_DUPLICATE_GENERATED", "INDEX_DUPLICATE_MANUAL",
    "INDEX_GENERATED_UNCLOSED", "INDEX_MANUAL_UNCLOSED",
    "INDEX_ORDER", "INDEX_DUPLICATE_DATE",
}

# Length cap for an index summary cell so the table stays skimmable.
SUMMARY_MAX_CHARS = 80


class WorklogFormatError(Exception):
    def __init__(self, issues: list[dict]):
        self.issues = issues
        super().__init__("; ".join(i["message"] for i in issues))


# --- day-file model ----------------------------------------------------------


@dataclass
class DayFile:
    date: str
    generated: str        # inner text between GENERATED markers (verbatim)
    manual: str           # inner text between MANUAL markers (verbatim)
    title_date: str | None = None
    trailing: str = ""    # any content after MANUAL:END (preserved on overwrite)


def is_valid_date(value: str) -> bool:
    return bool(DATE_RE.match(value))


def contains_marker_line(text: str) -> bool:
    """True if any line is a REPO_WORKLOG day/index marker.

    Generated content that carries such a line would corrupt the file's
    structure (parsing is line-based), so callers reject it up front rather
    than emit a file that fails to re-parse.
    """
    for line in text.splitlines():
        s = line.strip()
        if _DAY_MARKER_RE.match(s) or _INDEX_MARKER_RE.match(s):
            return True
    return False


def parse_date_filename(name: str) -> str | None:
    """Return the ISO date encoded by ``<date>.md`` or None (index/other names)."""
    m = DATE_FILE_RE.match(name)
    return m.group(1) if m else None


def scan_day(text: str, date: str) -> tuple[DayFile | None, list[dict]]:
    """Parse a single day file, collecting every structural issue.

    ``date`` is the date the file claims (its filename). Markers for any other
    date are reported as ``MARKER_DATE_MISMATCH``. Returns ``(day, issues)``;
    ``day`` is None when the GENERATED/MANUAL regions cannot be located.
    """
    issues: list[dict] = []
    lines = text.splitlines(keepends=True)

    title_date = None
    gen_s = gen_e = man_s = man_e = None
    dup_gen = dup_man = False

    for idx, raw in enumerate(lines):
        s = raw.strip()
        tm = _DAY_TITLE_RE.match(s)
        if tm and title_date is None:
            title_date = tm.group(1)
            continue
        m = _DAY_MARKER_RE.match(s)
        if not m:
            continue
        mdate, region, edge = m.group(1), m.group(2), m.group(3)
        if mdate != date:
            issues.append({"code": "MARKER_DATE_MISMATCH", "line": idx + 1,
                           "message": f"Marker date {mdate} does not match file date {date}."})
            continue
        if region == "GENERATED" and edge == "START":
            if gen_s is not None:
                dup_gen = True
            else:
                gen_s = idx
        elif region == "GENERATED" and edge == "END":
            if gen_e is not None:
                dup_gen = True   # keep the first END; a second is a duplicate
            else:
                gen_e = idx
        elif region == "MANUAL" and edge == "START":
            if man_s is not None:
                dup_man = True
            else:
                man_s = idx
        elif region == "MANUAL" and edge == "END":
            if man_e is not None:
                dup_man = True
            else:
                man_e = idx

    if title_date is None:
        issues.append({"code": "TITLE_MISSING", "line": None,
                       "message": "Missing '# Project Worklog — <date>' title line."})
    elif title_date != date:
        issues.append({"code": "TITLE_DATE_MISMATCH", "line": None,
                       "message": f"Title date {title_date} does not match file date {date}."})

    if dup_gen:
        issues.append({"code": "DUPLICATE_GENERATED", "line": None,
                       "message": f"Duplicate GENERATED region for {date}."})
    if dup_man:
        issues.append({"code": "DUPLICATE_MANUAL", "line": None,
                       "message": f"Duplicate MANUAL region for {date}."})
    if gen_s is None:
        issues.append({"code": "MISSING_GENERATED", "line": None,
                       "message": f"Missing GENERATED:START for {date}."})
    if gen_s is not None and gen_e is None:
        issues.append({"code": "GENERATED_UNCLOSED", "line": None,
                       "message": f"GENERATED region for {date} is never closed."})
    if man_s is None:
        issues.append({"code": "MISSING_MANUAL", "line": None,
                       "message": f"Missing MANUAL:START for {date}."})
    if man_s is not None and man_e is None:
        issues.append({"code": "MANUAL_UNCLOSED", "line": None,
                       "message": f"MANUAL region for {date} is never closed."})
    if (gen_s is not None and man_s is not None and man_s < gen_s):
        issues.append({"code": "ORDER_MANUAL_BEFORE_GENERATED", "line": None,
                       "message": f"MANUAL region precedes GENERATED region for {date}."})

    if None in (gen_s, gen_e, man_s, man_e):
        return None, issues

    generated = "".join(lines[gen_s + 1:gen_e])
    manual = "".join(lines[man_s + 1:man_e])
    trailing = "".join(lines[man_e + 1:])
    return DayFile(date, generated, manual, title_date, trailing), issues


def parse_day(text: str, date: str) -> DayFile:
    """Parse a day file strictly: raise on any fatal structural issue."""
    day, issues = scan_day(text, date)
    fatal = [i for i in issues if i["code"] in FATAL_CODES]
    if fatal or day is None:
        raise WorklogFormatError(fatal or issues)
    return day


def _meta_block(timezone: str | None, branch: str | None, head: str | None) -> str:
    meta: list[str] = []
    if timezone:
        meta.append(f"> 時區：{timezone}")
    if branch:
        meta.append(f"> Branch：{branch}")
    if head:
        meta.append(f"> HEAD：{head}")
    return ("\n" + "\n".join(meta) + "\n") if meta else ""


def _assemble_day(date: str, generated_md: str, manual_inner: str, *,
                  timezone: str | None, branch: str | None, head: str | None,
                  trailing: str = "") -> str:
    gen_body = generated_md.strip("\n")
    parts = [
        f"# Project Worklog — {date}\n",
        _meta_block(timezone, branch, head),
        "\n",
        f"<!-- {PREFIX}:{date}:GENERATED:START -->\n",
        (gen_body + "\n") if gen_body else "",
        f"<!-- {PREFIX}:{date}:GENERATED:END -->\n",
        "\n",
        f"<!-- {PREFIX}:{date}:MANUAL:START -->\n",
        manual_inner,
        f"<!-- {PREFIX}:{date}:MANUAL:END -->\n",
        trailing,
    ]
    return "".join(parts)


def build_day_file(date: str, generated_md: str, manual_inner: str = "\n", *,
                   timezone: str | None = None, branch: str | None = None,
                   head: str | None = None) -> str:
    """Build a day file with an explicit MANUAL inner block (used by migration)."""
    inner = manual_inner or "\n"
    if not inner.endswith("\n"):
        inner += "\n"
    return _assemble_day(date, generated_md, inner,
                         timezone=timezone, branch=branch, head=head)


def render_new_day_file(date: str, generated_md: str, *, timezone: str | None = None,
                        branch: str | None = None, head: str | None = None) -> str:
    """Build a brand-new day file with an empty MANUAL region."""
    return _assemble_day(date, generated_md, "\n",
                         timezone=timezone, branch=branch, head=head)


def overwrite_day_generated(text: str, date: str, generated_md: str, *,
                            timezone: str | None = None, branch: str | None = None,
                            head: str | None = None) -> str:
    """Return the day file with header + GENERATED refreshed, MANUAL preserved.

    The MANUAL inner text is copied byte-for-byte from ``text``; the title and
    meta blockquote are regenerated to reflect the current analysis run.
    """
    day = parse_day(text, date)
    return _assemble_day(date, generated_md, day.manual,
                         timezone=timezone, branch=branch, head=head,
                         trailing=day.trailing)


# --- index model -------------------------------------------------------------


DEFAULT_INDEX_MANUAL = "可在此補充專案工作日誌的閱讀方式、重要里程碑或交接說明。\n"

_INDEX_HEADER = (
    "# Project Worklog\n\n"
    "> 本目錄依據 Git commit、實際程式碼 diff 與相關程式碼上下文產生。\n"
    "> 用於專案維護、交接與異動追蹤。\n"
    "> 日期依執行環境的本地時區判定。\n\n"
    "## 工作日誌\n\n"
)


@dataclass
class IndexDoc:
    rows: list[tuple[str, str]]   # (date, summary), order as found
    manual: str                   # inner text between INDEX:MANUAL markers (verbatim)


def summarise_generated(generated_md: str) -> str:
    """Derive a one-line index summary from a day's 當日摘要 section.

    Returns the first non-empty, non-heading line under the 當日摘要 heading,
    collapsed to a single line, table-escaped, and length-capped. Empty string
    when no summary paragraph is present.
    """
    lines = generated_md.splitlines()
    in_summary = False
    for raw in lines:
        s = raw.strip()
        if _SUMMARY_HEADING_RE.match(s):
            in_summary = True
            continue
        if not in_summary:
            continue
        if not s:
            continue
        if s.startswith("#"):
            break  # next section began before any summary text
        return clean_summary(s)
    return ""


def clean_summary(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    collapsed = collapsed.replace("|", "\\|")
    if len(collapsed) > SUMMARY_MAX_CHARS:
        collapsed = collapsed[:SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return collapsed


def render_index(rows: list[tuple[str, str]], manual_inner: str | None = None) -> str:
    """Render index.md from ``rows`` (caller sorts them date-descending)."""
    if manual_inner is None:
        manual_inner = DEFAULT_INDEX_MANUAL
    table = ["| 日期 | 摘要 |", "|---|---|"]
    for date, summary in rows:
        table.append(f"| [{date}](./{date}.md) | {summary} |")
    gen_body = "\n".join(table)
    parts = [
        _INDEX_HEADER,
        f"<!-- {PREFIX}:INDEX:GENERATED:START -->\n",
        gen_body + "\n",
        f"<!-- {PREFIX}:INDEX:GENERATED:END -->\n",
        "\n## 人工說明\n\n",
        f"<!-- {PREFIX}:INDEX:MANUAL:START -->\n",
        manual_inner,
        f"<!-- {PREFIX}:INDEX:MANUAL:END -->\n",
    ]
    return "".join(parts)


def scan_index(text: str) -> tuple[IndexDoc | None, list[dict]]:
    """Parse index.md, collecting every structural issue."""
    issues: list[dict] = []
    lines = text.splitlines(keepends=True)

    gen_s = gen_e = man_s = man_e = None
    dup_gen = dup_man = False
    for idx, raw in enumerate(lines):
        m = _INDEX_MARKER_RE.match(raw.strip())
        if not m:
            continue
        region, edge = m.group(1), m.group(2)
        if region == "GENERATED" and edge == "START":
            if gen_s is not None:
                dup_gen = True
            else:
                gen_s = idx
        elif region == "GENERATED" and edge == "END":
            if gen_e is not None:
                dup_gen = True   # keep the first END; a second is a duplicate
            else:
                gen_e = idx
        elif region == "MANUAL" and edge == "START":
            if man_s is not None:
                dup_man = True
            else:
                man_s = idx
        elif region == "MANUAL" and edge == "END":
            if man_e is not None:
                dup_man = True
            else:
                man_e = idx

    if dup_gen:
        issues.append({"code": "INDEX_DUPLICATE_GENERATED", "line": None,
                       "message": "Duplicate INDEX GENERATED region."})
    if dup_man:
        issues.append({"code": "INDEX_DUPLICATE_MANUAL", "line": None,
                       "message": "Duplicate INDEX MANUAL region."})
    if gen_s is None:
        issues.append({"code": "INDEX_MISSING_GENERATED", "line": None,
                       "message": "Missing INDEX GENERATED:START."})
    if gen_s is not None and gen_e is None:
        issues.append({"code": "INDEX_GENERATED_UNCLOSED", "line": None,
                       "message": "INDEX GENERATED region is never closed."})
    if man_s is None:
        issues.append({"code": "INDEX_MISSING_MANUAL", "line": None,
                       "message": "Missing INDEX MANUAL:START."})
    if man_s is not None and man_e is None:
        issues.append({"code": "INDEX_MANUAL_UNCLOSED", "line": None,
                       "message": "INDEX MANUAL region is never closed."})

    if None in (gen_s, gen_e, man_s, man_e):
        return None, issues

    generated = "".join(lines[gen_s + 1:gen_e])
    manual = "".join(lines[man_s + 1:man_e])
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row_line in generated.splitlines():
        rm = _INDEX_ROW_RE.match(row_line.strip())
        if not rm:
            continue
        rdate, summary = rm.group(1), rm.group(2).strip()
        if rdate in seen:
            issues.append({"code": "INDEX_DUPLICATE_DATE", "line": None,
                           "message": f"Date {rdate} appears more than once in the index."})
        seen.add(rdate)
        rows.append((rdate, summary))

    ordered = [d for d, _ in rows]
    if ordered != sorted(ordered, reverse=True):
        issues.append({"code": "INDEX_ORDER", "line": None,
                       "message": "Index dates are not in descending order."})

    return IndexDoc(rows, manual), issues


def parse_index(text: str) -> IndexDoc:
    doc, issues = scan_index(text)
    fatal = [i for i in issues if i["code"] in FATAL_CODES]
    if fatal or doc is None:
        raise WorklogFormatError(fatal or issues)
    return doc
