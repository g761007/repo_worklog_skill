#!/usr/bin/env python3
"""Rebuild PROJECT_WORKLOG/index.md from the per-day files.

The index is navigation only: a date-descending table linking every
``PROJECT_WORKLOG/<date>.md`` file, each row carrying that day's one-line
summary (its 當日摘要). This script scans the directory for valid date files,
derives each summary, rebuilds the GENERATED table, and preserves the index's
MANUAL region byte-for-byte. Files that are not ``<date>.md`` (including
``index.md`` itself) are ignored.

Dry-run is the default. For a dry-run that reflects pending day-file writes not
yet on disk, pass ``{"overrides": {"<date>": "summary", ...}}`` — an override
supplies (or replaces) a date's summary and adds dates that will exist after the
day-file apply. ``--apply`` writes index.md atomically.

Input (``--input FILE`` or stdin, optional):
    {"overrides": {"2026-07-15": "新增會員搜尋快取", ...}}

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile

import _bootstrap  # noqa: F401 — must precede any git_worklog import
import worklog_markers as wm

from git_worklog import config, language

DEFAULT_DIR = wm.WORKLOG_DIRNAME


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_input(path: str | None, apply_mode: bool = False) -> dict:
    # In apply mode the day files are already on disk and the index is a pure
    # function of them, so there is nothing to read. Reading stdin here would
    # hang forever in any non-interactive environment -- an agent harness, CI,
    # or cron -- where stdin is neither a TTY nor closed. isatty() alone is not
    # a sufficient guard, and SKILL.md §8 documents `--apply` with no stdin.
    if apply_mode and path is None:
        return {}
    if path is None and sys.stdin.isatty():
        return {}
    raw = open(path, "r", encoding="utf-8").read() if path and path != "-" else sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def _scan_day_summaries(worklog_dir: str, layout: str) -> tuple[dict[str, str], list[dict]]:
    """Map each on-disk ``<date>.md`` to its summary; warn on unreadable files."""
    summaries: dict[str, str] = {}
    warnings: list[dict] = []
    if not os.path.isdir(worklog_dir):
        return summaries, warnings
    for date in wm.list_day_dates(worklog_dir, layout):
        path = wm.day_path(worklog_dir, date, layout)
        name = os.path.basename(path)
        try:
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8")
            day = wm.parse_day(text, date)
            summaries[date] = wm.summarise_generated(day.generated)
        except (UnicodeDecodeError, wm.WorklogFormatError, OSError) as exc:
            summaries[date] = ""
            warnings.append({"code": "DAY_FILE_UNREADABLE", "date": date,
                             "message": f"Could not read summary from {name}: {exc}"})
    return summaries, warnings


def _read_index_manual(index_path: str) -> str | None:
    """Return the existing index MANUAL inner text, or None if index is absent.

    Fail on a corrupt existing index rather than discard its MANUAL region.
    """
    if not os.path.exists(index_path):
        return None
    with open(index_path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("NON_UTF8", f"index.md is not valid UTF-8: {exc}", target=index_path)
    try:
        doc = wm.parse_index(text)
    except wm.WorklogFormatError as exc:
        _fail("INDEX_CORRUPT_MARKERS",
              "index.md has corrupted/missing markers; refusing to guess a repair.",
              target=index_path, issues=exc.issues)
    return doc.manual


def _atomic_write(target: str, content: str) -> None:
    target_dir = os.path.dirname(os.path.abspath(target))
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".rw-index-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        with open(tmp, "r", encoding="utf-8") as fh:
            wm.parse_index(fh.read())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _resolve_index_language(worklog_dir: str, original: "str | None",
                            requested: "str | None") -> "tuple[str, str]":
    """Decide the index's language, and say what decided it (§6.2.12).

    The index is navigation, not content: it is one file that every run
    rewrites, and teams commit it. If each run rendered it in whatever language
    that run happened to use, a repository with a zh-TW developer and an English
    one would rewrite its headings back and forth forever and put that churn in
    every diff. So the language is decided once and then left alone:

      1. config's index_language, when the project pinned one -- an explicit
         team decision outranks anything a single run wants.
      2. the lang= already stamped on the index -- "first build wins", which is
         the recommended default (§6.2.12).
      3. an index that exists but carries no stamp is zh-TW, because that is the
         only language anything could have written before this contract. Reading
         it as unstamped-therefore-undecided would silently retitle every
         existing index on upgrade.
      4. otherwise this run's language: the index does not exist yet, so this
         run is the first build and gets to choose.
    """
    pinned = config.index_language(config.load(worklog_dir))
    if pinned:
        return language.normalize(pinned), "project-config"

    if original is not None:
        stamped = wm.index_language_of(original)
        if stamped:
            return language.normalize(stamped), "existing-index"
        return wm.DEFAULT_INDEX_LANGUAGE, "existing-index-unstamped"

    if requested:
        return language.normalize(requested), "run"
    return wm.DEFAULT_INDEX_LANGUAGE, "default"


def run(args: argparse.Namespace) -> int:
    worklog_dir = args.dir or DEFAULT_DIR
    index_path = wm.index_path(worklog_dir)
    # The index links to wherever the day files actually are, so a legacy
    # worklog rebuilt before migration still gets working links.
    layout = wm.detect_layout(worklog_dir)
    payload = _load_input(args.input, apply_mode=bool(args.apply))
    overrides = payload.get("overrides", {}) if isinstance(payload.get("overrides"), dict) else {}

    summaries, warnings = _scan_day_summaries(worklog_dir, layout)
    # Overrides carry already-extracted one-line summaries (from
    # update_daily_worklog.py's `summaries`), used to preview pending writes.
    for date, summary in overrides.items():
        if not wm.is_valid_date(date):
            _fail("INVALID_DATE", f"Override key {date!r} is not a YYYY-MM-DD date.")
        summaries[date] = wm.clean_summary(summary) if summary else ""

    rows = [(d, summaries[d]) for d in sorted(summaries, reverse=True)]
    existing_manual = _read_index_manual(index_path)

    original = None
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as fh:
            original = fh.read()

    index_language, language_source = _resolve_index_language(
        worklog_dir, original, args.language)
    content = wm.render_index(rows, existing_manual, layout, index_language)
    action = ("no_change" if original == content
              else "create" if original is None else "rebuild")

    common = {
        "worklog_dir": worklog_dir,
        "index_path": index_path,
        "action": action,
        "dates": [d for d, _ in rows],
        "preserved_index_manual": existing_manual is not None,
        "index_language": index_language,
        "index_language_source": language_source,
        "index_hash": {"original": _sha256(original) if original is not None else None,
                       "preview": _sha256(content)},
        "warnings": warnings,
    }

    if not args.apply:
        _emit({"ok": True, "mode": "dry-run", **common, "preview": content,
               "note": "No files have been modified."})
        return 0

    _atomic_write(index_path, content)
    with open(index_path, "r", encoding="utf-8") as fh:
        written = fh.read()
    _emit({"ok": True, "mode": "apply", **common,
           "written_sha256": _sha256(written),
           "note": "index.md written atomically. No git add / commit / push was performed."})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Rebuild PROJECT_WORKLOG/index.md from day files.")
    p.add_argument("--input", help="Optional overrides JSON, or '-' for stdin.")
    p.add_argument("--dir", help=f"Worklog directory (default: {DEFAULT_DIR}).")
    p.add_argument("--language", default=None,
                   help="This run's language, used only when the index does "
                        "not exist yet. An index that already has a language "
                        "keeps it (§6.2.12).")
    p.add_argument("--apply", action="store_true",
                   help="Write index.md. Without this flag the run is a dry-run.")
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
