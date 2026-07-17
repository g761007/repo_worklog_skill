#!/usr/bin/env python3
"""Shared parser / serialiser for the Git Worklog directory-based worklog.

The worklog is no longer a single growing file. It is a directory:

    .git-worklog/
    ├── VERSION
    ├── config.json
    ├── index.md
    └── days/
        ├── 2026-07-15.md
        ├── 2026-07-14.md
        └── ...

Two on-disk shapes are managed here, each with stable HTML-comment markers that
use the prefix ``GIT_WORKLOG``.

**A per-day file** (``.git-worklog/days/<date>.md``) holds exactly one day. Its
title and meta blockquote are tool-owned (regenerated on every write); only the
MANUAL region is human-owned and preserved byte-for-byte::

    # Project Worklog — 2026-07-15

    > 時區：Asia/Taipei
    > Branch：main
    > HEAD：abc1234

    <!-- GIT_WORKLOG:2026-07-15:GENERATED:START -->
    ...auto-generated...
    <!-- GIT_WORKLOG:2026-07-15:GENERATED:END -->

    <!-- GIT_WORKLOG:2026-07-15:MANUAL:START -->
    ...human notes...
    <!-- GIT_WORKLOG:2026-07-15:MANUAL:END -->

**The index** (``.git-worklog/index.md``) is navigation only. Its GENERATED
region is a date-descending table rebuilt from the day files; its MANUAL region
is preserved verbatim::

    <!-- GIT_WORKLOG:INDEX:GENERATED:START -->
    | 日期 | 摘要 |
    |---|---|
    | [2026-07-15](./days/2026-07-15.md) | ... |
    <!-- GIT_WORKLOG:INDEX:GENERATED:END -->

    <!-- GIT_WORKLOG:INDEX:MANUAL:START -->
    ...human notes...
    <!-- GIT_WORKLOG:INDEX:MANUAL:END -->

**Legacy compatibility.** Before v0.6 the directory was ``PROJECT_WORKLOG/``,
day files sat at its root rather than under ``days/``, and markers used the
``REPO_WORKLOG`` prefix. Both marker prefixes parse; only ``GIT_WORKLOG`` is
ever written. ``detect_layout()`` recognises the flat legacy shape so a
not-yet-migrated worklog stays readable — writing to one is refused, and
migrate_legacy_worklog.py converts it.

update_daily_worklog.py, rebuild_worklog_index.py, validate_daily_worklog.py,
validate_worklog_index.py and preview_state.py all build on this module so they
agree on exactly one definition of the format.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

PREFIX = "GIT_WORKLOG"
# Markers written before v0.6. Parsed, never written. Migration rewrites them.
LEGACY_PREFIX = "REPO_WORKLOG"

WORKLOG_DIRNAME = ".git-worklog"
LEGACY_WORKLOG_DIRNAME = "PROJECT_WORKLOG"
DAYS_SUBDIR = "days"
INDEX_FILENAME = "index.md"
CONFIG_FILENAME = "config.json"
VERSION_FILENAME = "VERSION"

# On-disk layout version, written to .git-worklog/VERSION. Bump only when the
# directory shape changes in a way that needs another migration.
LAYOUT_VERSION = 1

# --- regexes -----------------------------------------------------------------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

_ANY_PREFIX = f"(?:{PREFIX}|{LEGACY_PREFIX})"
_DAY_MARKER_RE = re.compile(
    rf"^<!--\s*{_ANY_PREFIX}:(\d{{4}}-\d{{2}}-\d{{2}}):(GENERATED|MANUAL):(START|END)\s*-->$"
)
# The optional lang= attribute records the language the index was first built
# in (§6.2.12). It is optional because every index.md written before the
# language contract lacks it and must keep parsing: an index that suddenly fails
# to parse would read as INDEX_MISSING_GENERATED, i.e. a corrupt file, over a
# purely additive change.
_INDEX_MARKER_RE = re.compile(
    rf"^<!--\s*{_ANY_PREFIX}:INDEX:(GENERATED|MANUAL):(START|END)"
    rf"(?:\s+lang=([A-Za-z0-9-]+))?\s*-->$"
)
# Accept an em dash or a plain hyphen between the title and the date.
_DAY_TITLE_RE = re.compile(r"^#\s+Project Worklog\s+[—–-]\s+(\d{4}-\d{2}-\d{2})\s*$")

# Brackets the one-line summary inside a day's GENERATED region, so the index
# can find it without knowing what language the day was written in.
_SUMMARY_MARKER_RE = re.compile(
    rf"^<!--\s*{_ANY_PREFIX}:SUMMARY:(START|END)\s*-->$"
)

# How the summary was found before the marker existed: by its zh-TW heading.
# Day files written by earlier versions still carry that heading and no marker,
# and are never rewritten just to gain one — so this stays as the fallback for
# them. It is not a language rule; it is an artefact of the days when zh-TW was
# the only language the tool could produce.
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
    """True if any line is a day/index marker, in either prefix.

    Generated content that carries such a line would corrupt the file's
    structure (parsing is line-based), so callers reject it up front rather
    than emit a file that fails to re-parse. The legacy prefix counts too: it
    still parses, so it can still corrupt.

    SUMMARY markers are deliberately absent from this check: they nest *inside*
    a GENERATED region rather than delimiting one, so they cannot corrupt it,
    and generated content is required to carry them. Adding them here would
    reject every day the contract asks for.
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


_LEGACY_DAY_MARKER_RE = re.compile(
    rf"^(\s*)<!--\s*{LEGACY_PREFIX}:(\d{{4}}-\d{{2}}-\d{{2}}):(GENERATED|MANUAL):(START|END)\s*-->\s*$"
)
_LEGACY_INDEX_MARKER_RE = re.compile(
    rf"^(\s*)<!--\s*{LEGACY_PREFIX}:INDEX:(GENERATED|MANUAL):(START|END)\s*-->\s*$"
)


def retag_markers(text: str) -> tuple[str, int]:
    """Rewrite legacy-prefix marker lines to the current prefix.

    Only whole lines that parse as markers are touched, so prose that merely
    mentions the old prefix is left alone. Everything else -- the title, the
    meta blockquote, GENERATED prose, MANUAL notes -- is preserved byte for
    byte: migration moves a worklog, it does not rewrite one.

    Returns ``(new_text, markers_rewritten)``.
    """
    out: list[str] = []
    count = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n\r")
        eol = line[len(body):]
        dm = _LEGACY_DAY_MARKER_RE.match(body)
        if dm:
            indent, date, region, edge = dm.groups()
            out.append(f"{indent}<!-- {PREFIX}:{date}:{region}:{edge} -->{eol}")
            count += 1
            continue
        im = _LEGACY_INDEX_MARKER_RE.match(body)
        if im:
            indent, region, edge = im.groups()
            out.append(f"{indent}<!-- {PREFIX}:INDEX:{region}:{edge} -->{eol}")
            count += 1
            continue
        out.append(line)
    return "".join(out), count


# --- paths & layout ----------------------------------------------------------

# Where a worklog directory's day files live.
LAYOUT_CURRENT = "current"   # <dir>/days/<date>.md
LAYOUT_LEGACY = "legacy"     # <dir>/<date>.md   (pre-v0.6, read-only)
LAYOUT_EMPTY = "empty"       # no day files yet; treated as current


def detect_layout(worklog_dir: str) -> str:
    """Classify a worklog directory by where its day files actually sit.

    Probes the directory rather than trusting its name, so a legacy worklog
    stays readable wherever it lives and a caller that passes an explicit
    ``--dir`` is not second-guessed. ``days/`` wins if both shapes are present:
    a migration that was interrupted leaves the legacy files behind, and the
    migrated copies are the ones to trust.
    """
    if os.path.isdir(os.path.join(worklog_dir, DAYS_SUBDIR)):
        return LAYOUT_CURRENT
    try:
        names = os.listdir(worklog_dir)
    except OSError:
        return LAYOUT_EMPTY
    if any(parse_date_filename(n) for n in names):
        return LAYOUT_LEGACY
    return LAYOUT_EMPTY


def days_dir(worklog_dir: str, layout: str | None = None) -> str:
    """Directory holding the day files, for the given (or detected) layout."""
    if layout is None:
        layout = detect_layout(worklog_dir)
    if layout == LAYOUT_LEGACY:
        return worklog_dir
    return os.path.join(worklog_dir, DAYS_SUBDIR)


def day_path(worklog_dir: str, date: str, layout: str | None = None) -> str:
    return os.path.join(days_dir(worklog_dir, layout), f"{date}.md")


def index_path(worklog_dir: str) -> str:
    """The index sits at the worklog root in both layouts."""
    return os.path.join(worklog_dir, INDEX_FILENAME)


def config_path(worklog_dir: str) -> str:
    return os.path.join(worklog_dir, CONFIG_FILENAME)


def version_path(worklog_dir: str) -> str:
    return os.path.join(worklog_dir, VERSION_FILENAME)


def day_link(date: str, layout: str | None = None) -> str:
    """The index's relative link to a day file."""
    if layout == LAYOUT_LEGACY:
        return f"./{date}.md"
    return f"./{DAYS_SUBDIR}/{date}.md"


def render_config(timezone: str | None = None) -> str:
    """The initial ``config.json`` body (roadmap §4.4).

    ``language`` and ``index_language`` default to ``auto``, meaning "nobody has
    decided". Since this file is never rewritten once it exists, that default is
    also what every worklog created before the language contract carries -- so
    ``auto`` on disk is indistinguishable from a file the user never opened, and
    git_worklog.config reads it as exactly that rather than as a choice.
    """
    config = {
        "schema_version": LAYOUT_VERSION,
        "timezone": timezone or "auto",
        "language": "auto",
        "index_language": "auto",
        "authors": [],
        "ignore": [],
    }
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def ensure_data_dir(worklog_dir: str, timezone: str | None = None) -> list[str]:
    """Create VERSION and config.json if absent. Returns the paths created.

    Never rewrites either file: both are user-editable, and a worklog that
    already declares its layout is not ours to redeclare.
    """
    created: list[str] = []
    os.makedirs(worklog_dir, exist_ok=True)
    vp = version_path(worklog_dir)
    if not os.path.exists(vp):
        with open(vp, "w", encoding="utf-8") as fh:
            fh.write(f"{LAYOUT_VERSION}\n")
        created.append(vp)
    cp = config_path(worklog_dir)
    if not os.path.exists(cp):
        with open(cp, "w", encoding="utf-8") as fh:
            fh.write(render_config(timezone))
        created.append(cp)
    return created


def list_day_dates(worklog_dir: str, layout: str | None = None) -> list[str]:
    """Every date with a day file, ascending. Empty when the directory is absent."""
    if layout is None:
        layout = detect_layout(worklog_dir)
    d = days_dir(worklog_dir, layout)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(x for x in (parse_date_filename(n) for n in names) if x)


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


# The index's own furniture: everything on the page that is not a day's summary.
# A catalog is safe here in a way it would not be for day files, because this
# text is rendered by this function -- there is no LLM in the loop to reproduce
# it approximately, so a heading cannot drift and silently stop matching.
#
# Only the languages this project can actually vouch for are listed. An index
# resolved to any other language gets English furniture around day summaries
# written in that language, which is honest; inventing translations nobody here
# can proofread would not be.
_INDEX_CHROME = {
    "zh-TW": {
        "title": "Project Worklog",
        "intro": ("> 本目錄依據 Git commit、實際程式碼 diff 與相關程式碼上下文產生。\n"
                  "> 用於專案維護、交接與異動追蹤。\n"
                  "> 日期依執行環境的本地時區判定。"),
        "section": "工作日誌",
        "col_date": "日期",
        "col_summary": "摘要",
        "manual_section": "人工說明",
        "manual_default": "可在此補充專案工作日誌的閱讀方式、重要里程碑或交接說明。\n",
    },
    "en": {
        "title": "Project Worklog",
        "intro": ("> Generated from Git commits, the actual code diffs and the\n"
                  "> surrounding code context. Used for maintenance, handover and\n"
                  "> change tracking. Dates follow the local timezone of the run."),
        "section": "Worklog",
        "col_date": "Date",
        "col_summary": "Summary",
        "manual_section": "Notes",
        "manual_default": ("Add anything worth knowing about how to read this "
                           "worklog: milestones, handover notes, context.\n"),
    },
}

INDEX_CHROME_LANGUAGES = tuple(_INDEX_CHROME)
DEFAULT_INDEX_LANGUAGE = "zh-TW"


def index_chrome(language: "str | None") -> dict:
    """The index's furniture for ``language``, falling back to English."""
    if language in _INDEX_CHROME:
        return _INDEX_CHROME[language]
    return _INDEX_CHROME["en"]


# Kept as a module constant because callers outside this module reach for it.
DEFAULT_INDEX_MANUAL = _INDEX_CHROME[DEFAULT_INDEX_LANGUAGE]["manual_default"]


@dataclass
class IndexDoc:
    rows: list[tuple[str, str]]   # (date, summary), order as found
    manual: str                   # inner text between INDEX:MANUAL markers (verbatim)


def summarise_generated(generated_md: str) -> str:
    """Derive the one-line index summary from a day's GENERATED region.

    Prefers the SUMMARY marker, which says where the summary is without saying
    what language it is in. That matters because the index is built by scanning
    every day file, and days may be written in different languages: keying off a
    heading's text would silently yield an empty summary for any day not written
    in the language the reader happened to hardcode.

    Falls back to the zh-TW 當日摘要 heading for day files written before the
    marker existed. Those are never rewritten just to gain one, so the fallback
    is permanent, not a migration window.

    Returns a single line, table-escaped and length-capped; empty string when
    there is no summary to find.
    """
    marked = _between_summary_markers(generated_md)
    if marked is not None:
        return clean_summary(marked)

    in_summary = False
    for raw in generated_md.splitlines():
        s = raw.strip()
        if _SUMMARY_HEADING_RE.match(s):
            in_summary = True
            continue
        if not in_summary:
            continue
        if not s or _SUMMARY_MARKER_RE.match(s):
            continue  # a marker line is structure, not the summary's text
        if s.startswith("#"):
            break  # next section began before any summary text
        return clean_summary(s)
    return ""


def _between_summary_markers(generated_md: str) -> "str | None":
    """The first non-empty line between SUMMARY markers, or None if unmarked.

    An unclosed or empty marker pair returns None rather than an empty summary,
    so the caller falls back to the heading scan instead of writing a blank row
    for a day that does have a summary.
    """
    collecting = False
    for raw in generated_md.splitlines():
        s = raw.strip()
        m = _SUMMARY_MARKER_RE.match(s)
        if m:
            if m.group(1) == "END":
                break  # closed without content — treat as unmarked
            collecting = True
            continue
        if collecting and s and not s.startswith("#"):
            return s
    return None


def has_summary_marker(generated_md: str) -> bool:
    """True if the summary is bracketed rather than found by heading fallback.

    The distinction matters: a zh-TW day without the marker reads correctly
    today and loses its index summary the moment it is regenerated in another
    language, so callers want to tell "works" apart from "works for now".
    """
    return any(_SUMMARY_MARKER_RE.match(line.strip())
               for line in generated_md.splitlines())


def render_summary(summary: str) -> str:
    """The SUMMARY-marked block that a day's GENERATED region must carry."""
    return (f"<!-- {PREFIX}:SUMMARY:START -->\n"
            f"{summary.strip()}\n"
            f"<!-- {PREFIX}:SUMMARY:END -->\n")


def clean_summary(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    collapsed = collapsed.replace("|", "\\|")
    if len(collapsed) > SUMMARY_MAX_CHARS:
        collapsed = collapsed[:SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return collapsed


def render_index(rows: list[tuple[str, str]], manual_inner: str | None = None,
                 layout: str | None = None,
                 language: "str | None" = None) -> str:
    """Render index.md from ``rows`` (caller sorts them date-descending).

    ``language`` is stamped onto the GENERATED marker so the next rebuild uses
    the same one (§6.2.12). Without that, an index rebuilt by a zh-TW agent on
    Monday and an English one on Tuesday would flip its headings every run and
    churn the diff of a file teams commit.
    """
    if language is None:
        language = DEFAULT_INDEX_LANGUAGE
    chrome = index_chrome(language)
    if manual_inner is None:
        manual_inner = chrome["manual_default"]
    table = [f"| {chrome['col_date']} | {chrome['col_summary']} |", "|---|---|"]
    for date, summary in rows:
        table.append(f"| [{date}]({day_link(date, layout)}) | {summary} |")
    gen_body = "\n".join(table)
    parts = [
        f"# {chrome['title']}\n\n{chrome['intro']}\n\n## {chrome['section']}\n\n",
        f"<!-- {PREFIX}:INDEX:GENERATED:START lang={language} -->\n",
        gen_body + "\n",
        f"<!-- {PREFIX}:INDEX:GENERATED:END -->\n",
        f"\n## {chrome['manual_section']}\n\n",
        f"<!-- {PREFIX}:INDEX:MANUAL:START -->\n",
        manual_inner,
        f"<!-- {PREFIX}:INDEX:MANUAL:END -->\n",
    ]
    return "".join(parts)


def index_language_of(text: str) -> "str | None":
    """The language an existing index.md was built in, or None if unstamped.

    None means "written before the marker carried a language", not "English":
    those indexes are zh-TW, which is why callers treat an unstamped index as
    already fixed to the default rather than re-deciding it.
    """
    for raw in text.splitlines():
        m = _INDEX_MARKER_RE.match(raw.strip())
        if m and m.group(1) == "GENERATED" and m.group(2) == "START":
            return m.group(3)
    return None


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
