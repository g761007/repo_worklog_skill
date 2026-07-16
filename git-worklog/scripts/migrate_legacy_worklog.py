#!/usr/bin/env python3
"""One-time migration of the legacy single-file worklog to the directory layout.

The pre-directory skill wrote every day into one ``docs/PROJECT_WORKLOG.md`` file
delimited by ``REPO_WORKLOG:ENTRIES`` and per-date ``START/END`` markers. This
script reads that legacy file, splits each date into its own
``PROJECT_WORKLOG/<date>.md`` (preserving that date's GENERATED and MANUAL text),
and builds ``PROJECT_WORKLOG/index.md``.

It is **never** invoked by normal runs — only explicitly, via ``/git-worklog
migrate`` or by running this script. Dry-run is the default. It never deletes the
legacy file and never overwrites a day file that already exists (those are left
for the user to reconcile). If the legacy markers are corrupt, it refuses to
migrate rather than guess.

Usage:
    python3 scripts/migrate_legacy_worklog.py [--legacy docs/PROJECT_WORKLOG.md]
        [--dir PROJECT_WORKLOG] [--timezone Asia/Taipei] [--apply]

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

import worklog_markers as wm

DEFAULT_LEGACY = os.path.join("docs", "PROJECT_WORKLOG.md")
DEFAULT_DIR = wm.WORKLOG_DIRNAME

_LEGACY_MARKER_RE = re.compile(
    r"^<!--\s*REPO_WORKLOG:(\d{4}-\d{2}-\d{2}):(GENERATED|MANUAL):(START|END)\s*-->$"
)
_ENTRIES_RE = re.compile(r"^<!--\s*REPO_WORKLOG:ENTRIES:(START|END)\s*-->$")


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_legacy(text: str) -> dict[str, dict[str, str]]:
    """Extract ``{date: {"generated": ..., "manual": ...}}`` from the legacy file.

    Raises ValueError with a reason when the markers are unbalanced, so a corrupt
    legacy file is refused rather than half-migrated.
    """
    lines = text.splitlines(keepends=True)
    has_entries = any(_ENTRIES_RE.match(ln.strip()) for ln in lines)
    if not has_entries:
        raise ValueError("no REPO_WORKLOG:ENTRIES markers found")

    # Collect marker positions per date.
    marks: dict[str, dict[str, int]] = {}
    order: list[str] = []
    for idx, raw in enumerate(lines):
        m = _LEGACY_MARKER_RE.match(raw.strip())
        if not m:
            continue
        date, region, edge = m.group(1), m.group(2), m.group(3)
        key = f"{region}_{edge}"
        slot = marks.setdefault(date, {})
        if key in slot:
            raise ValueError(f"duplicate {region}:{edge} marker for {date}")
        slot[key] = idx
        if date not in order:
            order.append(date)

    result: dict[str, dict[str, str]] = {}
    for date in order:
        slot = marks[date]
        for need in ("GENERATED_START", "GENERATED_END", "MANUAL_START", "MANUAL_END"):
            if need not in slot:
                raise ValueError(f"missing {need} marker for {date}")
        gs, ge = slot["GENERATED_START"], slot["GENERATED_END"]
        ms, me = slot["MANUAL_START"], slot["MANUAL_END"]
        if not (gs < ge and ms < me):
            raise ValueError(f"markers out of order for {date}")
        result[date] = {
            "generated": "".join(lines[gs + 1:ge]),
            "manual": "".join(lines[ms + 1:me]),
        }
    return result


def _atomic_write(target: str, content: str, validate) -> None:
    target_dir = os.path.dirname(os.path.abspath(target))
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".rw-mig-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        with open(tmp, "r", encoding="utf-8") as fh:
            validate(fh.read())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run(args: argparse.Namespace) -> int:
    legacy_path = args.legacy or DEFAULT_LEGACY
    worklog_dir = args.dir or DEFAULT_DIR
    if not os.path.exists(legacy_path):
        _fail("LEGACY_NOT_FOUND", f"Legacy worklog not found: {legacy_path}", target=legacy_path)
    with open(legacy_path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("NON_UTF8", f"Legacy worklog is not valid UTF-8: {exc}", target=legacy_path)
    try:
        parsed = parse_legacy(text)
    except ValueError as exc:
        _fail("LEGACY_CORRUPT",
              f"Legacy worklog markers are corrupt; refusing to migrate: {exc}",
              target=legacy_path)

    if not parsed:
        _fail("LEGACY_EMPTY", "Legacy worklog has no date blocks to migrate.", target=legacy_path)

    tz = args.timezone
    planned: list[dict] = []
    to_write: list[tuple[str, str]] = []   # (path, content) for dates we will create
    summaries: dict[str, str] = {}
    for date in sorted(parsed, reverse=True):
        gen = parsed[date]["generated"]
        manual = parsed[date]["manual"]
        if wm.contains_marker_line(gen):
            _fail("LEGACY_CONTAINS_MARKER",
                  f"Legacy GENERATED content for {date} contains a REPO_WORKLOG marker "
                  "line; refusing to migrate rather than produce a corrupt day file.",
                  date=date)
        summaries[date] = wm.summarise_generated(gen)
        path = os.path.join(worklog_dir, f"{date}.md")
        if os.path.exists(path):
            planned.append({"date": date, "path": path, "action": "skip-exists"})
            continue
        content = wm.build_day_file(date, gen, manual, timezone=tz)
        planned.append({"date": date, "path": path, "action": "create"})
        to_write.append((path, content))

    # Preview the rebuilt index over all migrated dates (existing + to-create).
    index_path = os.path.join(worklog_dir, wm.INDEX_FILENAME)
    existing_manual = None
    if os.path.exists(index_path):
        try:
            existing_manual = wm.parse_index(
                open(index_path, encoding="utf-8").read()).manual
        except (wm.WorklogFormatError, UnicodeDecodeError) as exc:
            _fail("INDEX_CORRUPT_MARKERS",
                  "An existing index.md has corrupt markers; refusing to migrate rather "
                  f"than discard its MANUAL region: {exc}", target=index_path)
    rows = [(d, summaries[d]) for d in sorted(summaries, reverse=True)]
    index_content = wm.render_index(rows, existing_manual)

    common = {
        "legacy_path": legacy_path,
        "worklog_dir": worklog_dir,
        "index_path": index_path,
        "planned_changes": planned,
        "dates": [d for d, _ in rows],
        "index_preview_sha256": _sha256(index_content),
    }

    if not args.apply:
        _emit({"ok": True, "mode": "dry-run", **common,
               "index_preview": index_content,
               "note": ("No files have been modified. The legacy file is never "
                        "deleted; existing day files are never overwritten.")})
        return 0

    # Write the new day files (and index) atomically; on any failure remove the
    # files we created so a partial migration is never left behind.
    created: list[str] = []
    try:
        for path, content in to_write:
            _atomic_write(path, content,
                          lambda t, d=os.path.basename(path)[:-3]: wm.parse_day(t, d))
            created.append(path)
        _atomic_write(index_path, index_content, wm.parse_index)
    except Exception as exc:
        for path in created:
            try:
                os.unlink(path)
            except OSError:
                pass
        _fail("MIGRATION_FAILED",
              f"Migration failed and created files were rolled back: {exc}",
              worklog_dir=worklog_dir)

    _emit({"ok": True, "mode": "apply", **common,
           "created_dates": [os.path.basename(p)[:-3] for p, _ in to_write],
           "note": ("Migration written. The legacy file was NOT deleted — review the "
                    "new PROJECT_WORKLOG/ directory, then remove docs/PROJECT_WORKLOG.md "
                    "yourself if you are satisfied. No git add / commit / push was performed.")})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Migrate a legacy single-file worklog to PROJECT_WORKLOG/.")
    p.add_argument("--legacy", help=f"Legacy worklog path (default: {DEFAULT_LEGACY}).")
    p.add_argument("--dir", help=f"Target worklog directory (default: {DEFAULT_DIR}).")
    p.add_argument("--timezone", help="Timezone to record in each migrated day file's header.")
    p.add_argument("--apply", action="store_true",
                   help="Write the migration. Without this flag the run is a dry-run.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (json.JSONDecodeError, OSError) as exc:
        _fail("IO_ERROR", f"{exc}")
    except Exception as exc:  # never let a traceback replace the single JSON object
        _fail("UNEXPECTED_ERROR", f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
