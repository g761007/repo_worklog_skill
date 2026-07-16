#!/usr/bin/env python3
"""One-time migration of a legacy worklog into the ``.git-worklog/`` layout.

Two legacy shapes are migrated, because the worklog has moved twice:

**Single file** (``--from-file``, pre-v0.2) — every day lived in one
``docs/PROJECT_WORKLOG.md`` delimited by ``REPO_WORKLOG:ENTRIES`` and per-date
``START/END`` markers. Each date is split into its own day file, preserving that
date's GENERATED and MANUAL text.

**Flat directory** (``--from-dir``, v0.2–v0.5) — ``PROJECT_WORKLOG/<date>.md``
with the index alongside. Day files move to ``.git-worklog/days/<date>.md`` and
their markers are re-tagged ``REPO_WORKLOG`` → ``GIT_WORKLOG``. Nothing else in
a day file changes: the title, meta blockquote, GENERATED prose and MANUAL notes
are copied byte for byte, so a migration never rewrites a worklog's content or
its language. The index is rebuilt (its links must now point into ``days/``)
with its MANUAL region preserved.

With neither flag the source is auto-detected, directory first.

It is **never** invoked by normal runs — only explicitly, via ``/git-worklog
migrate`` or by running this script. Dry-run is the default. It never deletes the
source, and never overwrites a day file that already exists (those are left for
the user to reconcile). If the legacy markers are corrupt, it refuses to migrate
rather than guess.

Usage:
    python3 scripts/migrate_legacy_worklog.py [--from-file docs/PROJECT_WORKLOG.md]
        [--from-dir PROJECT_WORKLOG] [--dir .git-worklog] [--timezone Asia/Taipei]
        [--apply]

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
DEFAULT_LEGACY_DIR = wm.LEGACY_WORKLOG_DIRNAME
DEFAULT_DIR = wm.WORKLOG_DIRNAME

_LEGACY_MARKER_RE = re.compile(
    rf"^<!--\s*{wm.LEGACY_PREFIX}:(\d{{4}}-\d{{2}}-\d{{2}}):(GENERATED|MANUAL):(START|END)\s*-->$"
)
_ENTRIES_RE = re.compile(rf"^<!--\s*{wm.LEGACY_PREFIX}:ENTRIES:(START|END)\s*-->$")


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


def _read_utf8(path: str, what: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("NON_UTF8", f"{what} is not valid UTF-8: {exc}", target=path)


def _resolve_source(args: argparse.Namespace) -> tuple[str, str]:
    """Decide what we are migrating from. Returns ``(kind, path)``."""
    if args.from_dir and args.from_file:
        _fail("AMBIGUOUS_SOURCE",
              "Pass only one of --from-dir / --from-file; they are different legacy shapes.")
    if args.from_dir:
        return "dir", args.from_dir
    if args.from_file:
        return "file", args.from_file
    # Auto-detect: the flat directory is the newer legacy shape, so prefer it.
    if wm.detect_layout(DEFAULT_LEGACY_DIR) == wm.LAYOUT_LEGACY:
        return "dir", DEFAULT_LEGACY_DIR
    if os.path.exists(DEFAULT_LEGACY):
        return "file", DEFAULT_LEGACY
    _fail("LEGACY_NOT_FOUND",
          f"No legacy worklog found: neither {DEFAULT_LEGACY_DIR}/ (flat directory) "
          f"nor {DEFAULT_LEGACY} (single file). Pass --from-dir or --from-file.")


def _plan_from_file(legacy_path: str, worklog_dir: str, tz: str | None) -> tuple[list, list, dict]:
    text = _read_utf8(legacy_path, "Legacy worklog")
    try:
        parsed = parse_legacy(text)
    except ValueError as exc:
        _fail("LEGACY_CORRUPT",
              f"Legacy worklog markers are corrupt; refusing to migrate: {exc}",
              target=legacy_path)
    if not parsed:
        _fail("LEGACY_EMPTY", "Legacy worklog has no date blocks to migrate.", target=legacy_path)

    planned: list[dict] = []
    to_write: list[tuple[str, str]] = []
    summaries: dict[str, str] = {}
    for date in sorted(parsed, reverse=True):
        gen, manual = parsed[date]["generated"], parsed[date]["manual"]
        if wm.contains_marker_line(gen):
            _fail("LEGACY_CONTAINS_MARKER",
                  f"Legacy GENERATED content for {date} contains a marker line; "
                  "refusing to migrate rather than produce a corrupt day file.",
                  date=date)
        summaries[date] = wm.summarise_generated(gen)
        path = wm.day_path(worklog_dir, date, wm.LAYOUT_CURRENT)
        if os.path.exists(path):
            planned.append({"date": date, "path": path, "action": "skip-exists"})
            continue
        planned.append({"date": date, "path": path, "action": "create"})
        to_write.append((path, wm.build_day_file(date, gen, manual, timezone=tz)))
    return planned, to_write, summaries


def _plan_from_dir(src_dir: str, worklog_dir: str) -> tuple[list, list, dict]:
    """Plan a flat-directory migration: move day files and re-tag their markers.

    Day content is copied verbatim apart from the marker prefix, so the original
    header metadata (branch, HEAD, timezone) and the prose survive untouched.
    ``--timezone`` is deliberately ignored here: each day file already records
    the timezone it was written under, and rewriting it would falsify history.
    """
    if os.path.abspath(src_dir) == os.path.abspath(worklog_dir):
        _fail("SOURCE_IS_TARGET",
              f"--from-dir and --dir are the same directory ({src_dir}); nothing to migrate.")
    layout = wm.detect_layout(src_dir)
    if layout != wm.LAYOUT_LEGACY:
        _fail("SOURCE_NOT_LEGACY",
              f"{src_dir} does not hold a flat legacy worklog (no <date>.md files at "
              f"its root). Detected layout: {layout}.", target=src_dir)

    planned: list[dict] = []
    to_write: list[tuple[str, str]] = []
    summaries: dict[str, str] = {}
    for date in sorted(wm.list_day_dates(src_dir, layout), reverse=True):
        src = wm.day_path(src_dir, date, layout)
        text = _read_utf8(src, f"Day file {date}.md")
        retagged, _ = wm.retag_markers(text)
        try:
            day = wm.parse_day(retagged, date)
        except wm.WorklogFormatError as exc:
            _fail("DAY_FILE_CORRUPT",
                  f"Day file {date}.md has corrupt/missing markers; refusing to migrate "
                  "rather than guess a repair.", target=src, issues=exc.issues)
        summaries[date] = wm.summarise_generated(day.generated)
        dst = wm.day_path(worklog_dir, date, wm.LAYOUT_CURRENT)
        if os.path.exists(dst):
            planned.append({"date": date, "path": dst, "action": "skip-exists"})
            continue
        planned.append({"date": date, "path": dst, "action": "create", "source": src})
        to_write.append((dst, retagged))
    if not to_write and not planned:
        _fail("LEGACY_EMPTY", f"{src_dir} has no day files to migrate.", target=src_dir)
    return planned, to_write, summaries


def _source_index_manual(kind: str, src: str, worklog_dir: str) -> str | None:
    """The MANUAL region to carry into the new index.

    The target's own index wins if it exists; otherwise a flat source's index
    donates its MANUAL so hand-written navigation notes survive the move.
    """
    target_index = wm.index_path(worklog_dir)
    for path, why in ((target_index, "An existing index.md"),
                      (wm.index_path(src) if kind == "dir" else None, "The source index.md")):
        if not path or not os.path.exists(path):
            continue
        try:
            return wm.parse_index(_read_utf8(path, "index.md")).manual
        except wm.WorklogFormatError as exc:
            _fail("INDEX_CORRUPT_MARKERS",
                  f"{why} has corrupt markers; refusing to migrate rather than "
                  f"discard its MANUAL region: {exc}", target=path)
    return None


def run(args: argparse.Namespace) -> int:
    kind, source = _resolve_source(args)
    worklog_dir = args.dir or DEFAULT_DIR
    if not os.path.exists(source):
        _fail("LEGACY_NOT_FOUND", f"Legacy worklog not found: {source}", target=source)

    if kind == "dir":
        planned, to_write, summaries = _plan_from_dir(source, worklog_dir)
    else:
        planned, to_write, summaries = _plan_from_file(source, worklog_dir, args.timezone)

    # Preview the rebuilt index over all migrated dates (existing + to-create).
    index_path = wm.index_path(worklog_dir)
    existing_manual = _source_index_manual(kind, source, worklog_dir)
    rows = [(d, summaries[d]) for d in sorted(summaries, reverse=True)]
    index_content = wm.render_index(rows, existing_manual, wm.LAYOUT_CURRENT)

    common = {
        "source": source,
        "source_kind": kind,
        "legacy_path": source,   # retained for callers written against the old key
        "worklog_dir": worklog_dir,
        "index_path": index_path,
        "planned_changes": planned,
        "dates": [d for d, _ in rows],
        "index_preview_sha256": _sha256(index_content),
    }

    if not args.apply:
        _emit({"ok": True, "mode": "dry-run", **common,
               "index_preview": index_content,
               "config_preview": wm.render_config(args.timezone),
               "note": ("No files have been modified. The source is never deleted; "
                        "existing day files are never overwritten.")})
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
        created.extend(wm.ensure_data_dir(worklog_dir, args.timezone))
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
           "note": (f"Migration written to {worklog_dir}/. The source ({source}) was NOT "
                    "deleted — review the result, then remove it yourself if you are "
                    "satisfied. No git add / commit / push was performed.")})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=f"Migrate a legacy worklog into {DEFAULT_DIR}/.")
    p.add_argument("--from-dir", dest="from_dir",
                   help=f"Flat legacy worklog directory, v0.2-v0.5 (default: {DEFAULT_LEGACY_DIR}).")
    p.add_argument("--from-file", dest="from_file",
                   help=f"Single-file legacy worklog, pre-v0.2 (default: {DEFAULT_LEGACY}).")
    p.add_argument("--legacy", dest="from_file",
                   help="Deprecated alias for --from-file.")
    p.add_argument("--dir", help=f"Target worklog directory (default: {DEFAULT_DIR}).")
    p.add_argument("--timezone",
                   help="Timezone recorded in config.json, and in each day file's header "
                        "when migrating from a single file. Ignored for --from-dir, whose "
                        "day files already record their own.")
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
