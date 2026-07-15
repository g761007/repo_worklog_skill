#!/usr/bin/env python3
"""Shared parser / serialiser for the repo_worklog Markdown marker format.

The worklog is a human-first Markdown document whose machine-updatable regions
are delimited by stable HTML comments:

    <!-- REPO_WORKLOG:ENTRIES:START -->
    <!-- REPO_WORKLOG:2026-07-15:START -->
    ## 2026-07-15
    <!-- REPO_WORKLOG:2026-07-15:GENERATED:START -->
    ...auto-generated...
    <!-- REPO_WORKLOG:2026-07-15:GENERATED:END -->
    <!-- REPO_WORKLOG:2026-07-15:MANUAL:START -->
    ...human notes...
    <!-- REPO_WORKLOG:2026-07-15:MANUAL:END -->
    <!-- REPO_WORKLOG:2026-07-15:END -->
    <!-- REPO_WORKLOG:ENTRIES:END -->

Only GENERATED regions are ever overwritten. MANUAL regions, the document
header (before ENTRIES:START) and footer (after ENTRIES:END) are preserved
verbatim. This module is deliberately conservative: when a date block is *not*
being rewritten it is copied byte-for-byte, and even a rewritten block only has
the text between its GENERATED markers replaced.

update_worklog.py, validate_worklog.py and preview_state.py all build on this
module so the three agree on exactly one definition of the format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PREFIX = "REPO_WORKLOG"
ENTRIES_START = f"<!-- {PREFIX}:ENTRIES:START -->"
ENTRIES_END = f"<!-- {PREFIX}:ENTRIES:END -->"

_ENTRIES_RE = re.compile(rf"^<!--\s*{PREFIX}:ENTRIES:(START|END)\s*-->$")
_DATE_RE = re.compile(
    rf"^<!--\s*{PREFIX}:(\d{{4}}-\d{{2}}-\d{{2}}):"
    r"(START|END|GENERATED:START|GENERATED:END|MANUAL:START|MANUAL:END)\s*-->$"
)
_HEADING_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")

FATAL_CODES = {
    "ENTRIES_MISSING", "ENTRIES_UNBALANCED", "DATE_START_WITHOUT_END",
    "DATE_END_WITHOUT_START", "DUPLICATE_DATE", "DUPLICATE_GENERATED",
    "DUPLICATE_MANUAL", "MISSING_GENERATED", "MISSING_MANUAL",
    "HEADING_MISMATCH", "INTERLEAVED_BLOCKS", "STRAY_MARKER",
}


class WorklogFormatError(Exception):
    def __init__(self, issues: list[dict]):
        self.issues = issues
        super().__init__("; ".join(i["message"] for i in issues))


@dataclass
class Entry:
    date: str
    block: str            # full verbatim block, DATE:START line .. DATE:END line
    generated: str        # inner text between GENERATED markers (verbatim)
    manual: str           # inner text between MANUAL markers (verbatim)
    heading: str          # the "## DATE" heading line text (stripped)


@dataclass
class WorklogDoc:
    header: str = ""              # text before ENTRIES:START (verbatim)
    footer: str = ""             # text after ENTRIES:END (verbatim)
    entries: list[Entry] = field(default_factory=list)

    def dates(self) -> list[str]:
        return [e.date for e in self.entries]

    def by_date(self) -> dict[str, Entry]:
        return {e.date: e for e in self.entries}


def _classify(line: str):
    s = line.strip()
    m = _ENTRIES_RE.match(s)
    if m:
        return ("ENTRIES_" + m.group(1), None)
    m = _DATE_RE.match(s)
    if m:
        return (m.group(2).replace(":", "_"), m.group(1))
    return (None, None)


def scan(text: str) -> tuple[WorklogDoc | None, list[dict]]:
    """Parse ``text`` and collect every structural issue.

    Returns ``(doc, issues)``. ``doc`` is ``None`` only when the ENTRIES region
    itself cannot be located. Non-fatal issues (e.g. NOT_SORTED) may accompany a
    usable ``doc``; callers decide what is tolerable.
    """
    issues: list[dict] = []
    lines = text.splitlines(keepends=True)

    entries_start = entries_end = None
    for idx, line in enumerate(lines):
        kind, _ = _classify(line)
        if kind == "ENTRIES_START":
            if entries_start is not None:
                issues.append({"code": "ENTRIES_UNBALANCED", "line": idx + 1,
                               "message": "Duplicate ENTRIES:START marker."})
            elif entries_end is not None:
                issues.append({"code": "ENTRIES_UNBALANCED", "line": idx + 1,
                               "message": "ENTRIES:START appears after ENTRIES:END."})
            else:
                entries_start = idx
        elif kind == "ENTRIES_END":
            if entries_end is not None:
                issues.append({"code": "ENTRIES_UNBALANCED", "line": idx + 1,
                               "message": "Duplicate ENTRIES:END marker."})
            else:
                entries_end = idx

    if entries_start is None or entries_end is None or entries_start > entries_end:
        issues.append({"code": "ENTRIES_MISSING", "line": None,
                       "message": "A single well-formed ENTRIES:START/END pair is required."})
        return None, issues

    header = "".join(lines[:entries_start])
    footer = "".join(lines[entries_end + 1:])
    region = lines[entries_start + 1:entries_end]
    region_offset = entries_start + 1  # for human-facing line numbers

    doc = WorklogDoc(header=header, footer=footer)
    seen_dates: set[str] = set()

    state = "SEEK"          # SEEK, HEAD, GEN, POST_GEN, MAN, POST_MAN
    cur_date = None
    cur_start = None        # region-relative index of DATE:START
    gen_start = gen_end = man_start = man_end = None
    heading = None
    saw_gen = saw_man = False

    def line_no(region_idx: int) -> int:
        return region_offset + region_idx + 1

    for ridx, line in enumerate(region):
        kind, mdate = _classify(line)

        if kind is None:
            if state == "HEAD":
                hm = _HEADING_RE.match(line.strip())
                if hm and heading is None:
                    heading = line.strip()
            continue

        if kind == "START":
            if state != "SEEK":
                issues.append({"code": "INTERLEAVED_BLOCKS", "line": line_no(ridx),
                               "message": f"{mdate}:START opened before the previous block closed."})
                # Recover: treat as a fresh block start.
            if mdate in seen_dates:
                issues.append({"code": "DUPLICATE_DATE", "line": line_no(ridx),
                               "message": f"Date {mdate} appears more than once."})
            cur_date, cur_start = mdate, ridx
            gen_start = gen_end = man_start = man_end = None
            heading = None
            saw_gen = saw_man = False
            state = "HEAD"

        elif kind == "GENERATED_START":
            if state not in ("HEAD",):
                issues.append({"code": "STRAY_MARKER", "line": line_no(ridx),
                               "message": f"Unexpected GENERATED:START for {mdate}."})
            if saw_gen:
                issues.append({"code": "DUPLICATE_GENERATED", "line": line_no(ridx),
                               "message": f"Duplicate GENERATED block for {mdate}."})
            gen_start = ridx
            saw_gen = True
            state = "GEN"

        elif kind == "GENERATED_END":
            gen_end = ridx
            state = "POST_GEN"

        elif kind == "MANUAL_START":
            if state not in ("POST_GEN",):
                issues.append({"code": "STRAY_MARKER", "line": line_no(ridx),
                               "message": f"Unexpected MANUAL:START for {mdate}."})
            if saw_man:
                issues.append({"code": "DUPLICATE_MANUAL", "line": line_no(ridx),
                               "message": f"Duplicate MANUAL block for {mdate}."})
            man_start = ridx
            saw_man = True
            state = "MAN"

        elif kind == "MANUAL_END":
            man_end = ridx
            state = "POST_MAN"

        elif kind == "END":
            if cur_date is None:
                issues.append({"code": "DATE_END_WITHOUT_START", "line": line_no(ridx),
                               "message": f"{mdate}:END without a matching START."})
                state = "SEEK"
                continue
            if mdate != cur_date:
                issues.append({"code": "HEADING_MISMATCH", "line": line_no(ridx),
                               "message": f"{mdate}:END does not match open block {cur_date}."})
            if not saw_gen:
                issues.append({"code": "MISSING_GENERATED", "line": line_no(ridx),
                               "message": f"Block {cur_date} has no GENERATED region."})
            if not saw_man:
                issues.append({"code": "MISSING_MANUAL", "line": line_no(ridx),
                               "message": f"Block {cur_date} has no MANUAL region."})
            if heading is not None:
                hm = _HEADING_RE.match(heading)
                if hm and hm.group(1) != cur_date:
                    issues.append({"code": "HEADING_MISMATCH", "line": line_no(ridx),
                                   "message": f"Heading {heading!r} does not match block date {cur_date}."})

            block = "".join(region[cur_start:ridx + 1])
            generated = ("".join(region[gen_start + 1:gen_end])
                         if gen_start is not None and gen_end is not None else "")
            manual = ("".join(region[man_start + 1:man_end])
                      if man_start is not None and man_end is not None else "")
            doc.entries.append(Entry(cur_date, block, generated, manual, heading or f"## {cur_date}"))
            seen_dates.add(cur_date)
            state = "SEEK"
            cur_date = None

    if state != "SEEK":
        issues.append({"code": "DATE_START_WITHOUT_END", "line": None,
                       "message": f"Block {cur_date} was never closed with an END marker."})

    # Non-fatal: descending order is what update enforces; validate reports drift.
    ordered = doc.dates()
    if ordered != sorted(ordered, reverse=True):
        issues.append({"code": "NOT_SORTED", "line": None,
                       "message": "Date blocks are not in descending order."})

    return doc, issues


def parse(text: str) -> WorklogDoc:
    """Parse strictly: raise WorklogFormatError on any fatal structural issue."""
    doc, issues = scan(text)
    fatal = [i for i in issues if i["code"] in FATAL_CODES]
    if fatal or doc is None:
        raise WorklogFormatError(fatal or issues)
    return doc


def _normalise_block(block: str) -> str:
    stripped = block.strip("\n")
    return stripped + "\n" if stripped else ""


def render_generated_block(date: str, generated_md: str) -> str:
    """Build a brand-new date block with empty MANUAL region."""
    body = generated_md.strip("\n")
    return (
        f"<!-- {PREFIX}:{date}:START -->\n"
        f"## {date}\n\n"
        f"<!-- {PREFIX}:{date}:GENERATED:START -->\n"
        f"{body}\n"
        f"<!-- {PREFIX}:{date}:GENERATED:END -->\n\n"
        f"<!-- {PREFIX}:{date}:MANUAL:START -->\n\n"
        f"<!-- {PREFIX}:{date}:MANUAL:END -->\n\n"
        f"<!-- {PREFIX}:{date}:END -->\n"
    )


def replace_generated(entry: Entry, generated_md: str) -> str:
    """Return ``entry.block`` with only the GENERATED inner text replaced."""
    lines = entry.block.splitlines(keepends=True)
    gen_s = gen_e = None
    for idx, line in enumerate(lines):
        kind, _ = _classify(line)
        if kind == "GENERATED_START":
            gen_s = idx
        elif kind == "GENERATED_END":
            gen_e = idx
            break
    if gen_s is None or gen_e is None:
        # Fall back to a full re-render preserving the manual text.
        rebuilt = render_generated_block(entry.date, generated_md)
        if entry.manual.strip():
            return _inject_manual(rebuilt, entry.manual)
        return rebuilt
    body = generated_md.strip("\n")
    head = "".join(lines[:gen_s + 1])
    tail = "".join(lines[gen_e:])
    return _normalise_block(head + body + "\n" + tail)


def _inject_manual(block: str, manual: str) -> str:
    lines = block.splitlines(keepends=True)
    man_s = man_e = None
    for idx, line in enumerate(lines):
        kind, _ = _classify(line)
        if kind == "MANUAL_START":
            man_s = idx
        elif kind == "MANUAL_END":
            man_e = idx
            break
    if man_s is None or man_e is None:
        return block
    body = manual.strip("\n")
    head = "".join(lines[:man_s + 1])
    tail = "".join(lines[man_e:])
    return _normalise_block(head + "\n" + body + "\n\n" + tail)


DEFAULT_HEADER = (
    "# Project Worklog\n\n"
    "> 本文件依據 Git commit、實際程式碼 diff 與相關程式碼上下文產生。\n"
    "> 用於專案維護、交接與異動追蹤。\n"
    "> 日期依執行環境的本地時區判定。\n\n"
)


def new_document() -> WorklogDoc:
    return WorklogDoc(header=DEFAULT_HEADER, footer="")


def serialise(doc: WorklogDoc) -> str:
    """Render a WorklogDoc back to Markdown, entries sorted date-descending."""
    ordered = sorted(doc.entries, key=lambda e: e.date, reverse=True)
    parts = [doc.header]
    if not doc.header.endswith("\n\n") and doc.header:
        parts.append("\n" if doc.header.endswith("\n") else "\n\n")
    parts.append(ENTRIES_START + "\n\n")
    blocks = [_normalise_block(e.block) for e in ordered]
    parts.append("\n".join(blocks))
    if blocks:
        parts.append("\n")
    parts.append(ENTRIES_END + "\n")
    footer = doc.footer
    if footer and not footer.startswith("\n"):
        parts.append("\n")
    parts.append(footer)
    return "".join(parts)
