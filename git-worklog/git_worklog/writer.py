"""Turning generated prose into the bytes that land in ``.git-worklog/``.

:mod:`git_worklog.markers` knows the *format* — where a marker goes, what a day
file looks like. This module knows the *write*: which files a run would touch,
what each one's final text is, and how to get all of them onto disk without ever
leaving half a worklog behind.

Two shapes, one rule each:

* **Day files** — one per date, planned independently, applied as a single
  transaction. A day's MANUAL region survives byte-for-byte or the write is
  refused; there is no repair path for corrupt markers because guessing which
  text a human wrote is exactly the mistake that loses it.
* **index.md** — a pure function of the day files on disk, so it is rebuilt
  rather than edited, and its own MANUAL region is likewise preserved.

Planning is separated from applying because the plan *is* the preview: the same
bytes are shown to the user, stored on the preview record, and later written
(roadmap §10.1). Recomputing them at apply time would mean applying something
nobody saw.

This lives in the package, not in ``scripts/``, because ``git-worklog preview``
and ``git-worklog apply`` need it and an installed CLI has no ``scripts/``
directory to shell out to.
"""

from __future__ import annotations

import hashlib
import os
import tempfile

from git_worklog import config, language
from git_worklog import markers as wm

DEFAULT_DIR = wm.WORKLOG_DIRNAME


class WriterError(ValueError):
    """A refused write, carrying the wire code its caller reports.

    Mirrors :class:`git_worklog.analysis.AnalysisError`: the callers are thin
    shells (scripts and CLI subcommands) that each owe the user one JSON object
    with a stable ``code``, so the code rides on the exception rather than being
    re-derived from the message at every call site.
    """

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Day files
# --------------------------------------------------------------------------

def check_layout(worklog_dir: str) -> None:
    """Refuse to write day files into a pre-v0.6 flat directory.

    Writing would leave the worklog half in each layout. Reads of a legacy
    directory still work; only writes are gated.
    """
    if wm.detect_layout(worklog_dir) == wm.LAYOUT_LEGACY:
        raise WriterError(
            "LEGACY_LAYOUT",
            f"{worklog_dir} still uses the pre-v0.6 flat layout (day files at its "
            f"root, not under {wm.DAYS_SUBDIR}/). Run migrate_legacy_worklog.py "
            "first; this script will not write a mixed-layout directory.",
            worklog_dir=worklog_dir)


def _read_existing(path: str) -> "str | None":
    """Return the file's text, or None if absent. Fail on non-UTF-8."""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WriterError("NON_UTF8",
                          f"Existing day file is not valid UTF-8: {exc}", target=path)


def plan_days(worklog_dir: str, entries: dict, meta: dict) -> "list[dict]":
    """Compute the intended write for every target date. Fail on corruption.

    Each returned write carries both ``original`` and ``content`` — the whole
    before/after — because that is what a rollback and a preview each need.
    """
    tz = meta.get("timezone")
    branch = meta.get("branch")
    head = meta.get("head")
    writes: "list[dict]" = []
    for date in sorted(entries.keys(), reverse=True):
        if not wm.is_valid_date(date):
            raise WriterError("INVALID_DATE",
                              f"Entry key {date!r} is not a YYYY-MM-DD date.")
        if not isinstance(entries[date], dict):
            raise WriterError(
                "INVALID_ENTRY",
                f"Entry for {date} must be an object with 'generated_markdown'.")
        gen_md = entries[date].get("generated_markdown", "")
        if wm.contains_marker_line(gen_md):
            raise WriterError(
                "GENERATED_CONTAINS_MARKER",
                f"generated_markdown for {date} contains a {wm.PREFIX} marker line, "
                "which would corrupt the file. Rephrase or escape it.", date=date)
        path = wm.day_path(worklog_dir, date, wm.LAYOUT_CURRENT)
        original = _read_existing(path)
        summary = wm.summarise_generated(gen_md)

        if original is None:
            content = wm.render_new_day_file(date, gen_md, timezone=tz, branch=branch,
                                             head=head)
            action, manual_preserved = "create", False
        else:
            try:
                existing_day = wm.parse_day(original, date)
            except wm.WorklogFormatError as exc:
                raise WriterError(
                    "CORRUPT_MARKERS",
                    f"Day file {date}.md has corrupted/missing markers; refusing to "
                    "guess a repair.", target=path, issues=exc.issues)
            content = wm.overwrite_day_generated(original, date, gen_md,
                                                 timezone=tz, branch=branch, head=head)
            # MANUAL must survive byte-for-byte.
            new_day = wm.parse_day(content, date)
            if new_day.manual != existing_day.manual:
                raise WriterError(
                    "MANUAL_MUTATED",
                    f"Refusing to write: MANUAL content for {date} would change.",
                    date=date)
            manual_preserved = bool(existing_day.manual.strip())
            action = "no_change" if content == original else "overwrite"

        writes.append({
            "date": date, "path": path, "action": action,
            "manual_preserved": manual_preserved, "summary": summary,
            "original": original, "content": content,
        })
    return writes


def day_report(writes: "list[dict]") -> dict:
    """The parts of a plan that are safe to hand back as JSON.

    ``original``/``content`` are the whole file text and stay out of this: the
    callers that want them (dry-run previews, preview records) ask for them by
    name, and the ones that do not should not carry two copies of every day file
    through their output.
    """
    return {
        "planned_changes": [{"date": w["date"], "path": w["path"],
                             "action": w["action"],
                             "manual_preserved": w["manual_preserved"]}
                            for w in writes],
        "summaries": {w["date"]: w["summary"] for w in writes},
        "file_hashes": {w["date"]: {
            "original": sha256(w["original"]) if w["original"] is not None else None,
            "preview": sha256(w["content"]),
        } for w in writes},
        "preserved_manual_dates": sorted(w["date"] for w in writes
                                         if w["action"] == "overwrite"
                                         and w["manual_preserved"]),
    }


def apply_days(worklog_dir: str, writes: "list[dict]",
               timezone: "str | None" = None) -> None:
    """Stage, validate, then atomically swap every changed file, with rollback."""
    changed = [w for w in writes if w["action"] != "no_change"]
    if not changed:
        return
    day_dir = wm.days_dir(worklog_dir, wm.LAYOUT_CURRENT)
    os.makedirs(day_dir, exist_ok=True)
    wm.ensure_data_dir(worklog_dir, timezone)

    # Stage each file to a same-directory temp and validate it before it goes live.
    staged: "list[tuple[str, dict]]" = []
    try:
        for w in changed:
            fd, tmp = tempfile.mkstemp(dir=day_dir, prefix=".rw-", suffix=".tmp")
            staged.append((tmp, w))   # track immediately so cleanup can't miss it
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(w["content"])
                fh.flush()
                os.fsync(fh.fileno())
            _, issues = wm.scan_day(w["content"], w["date"])
            if [i for i in issues if i["code"] in wm.FATAL_CODES]:
                raise RuntimeError(f"staged {w['date']}.md failed validation")
    except Exception:
        for tmp, _ in staged:
            _safe_unlink(tmp)
        raise

    # Swap them in; on any failure restore everything already swapped.
    swapped: "list[dict]" = []
    try:
        for tmp, w in staged:
            os.replace(tmp, w["path"])
            swapped.append(w)
    except Exception:
        _rollback(swapped)
        for tmp, _ in staged:
            _safe_unlink(tmp)
        raise

    # Confirm the live files parse; roll back the whole batch if not.
    for w in swapped:
        with open(w["path"], "r", encoding="utf-8") as fh:
            live = fh.read()
        _, issues = wm.scan_day(live, w["date"])
        if [i for i in issues if i["code"] in wm.FATAL_CODES]:
            _rollback(swapped)
            raise RuntimeError(f"post-write validation failed for {w['date']}.md")


def _rollback(swapped: "list[dict]") -> None:
    for w in reversed(swapped):
        if w["original"] is not None:
            # Restore atomically so a failed restore can't truncate the original.
            target_dir = os.path.dirname(os.path.abspath(w["path"]))
            fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".rw-rb-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(w["original"])
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, w["path"])
            finally:
                _safe_unlink(tmp)
        else:
            _safe_unlink(w["path"])


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# index.md
# --------------------------------------------------------------------------

def _scan_day_summaries(worklog_dir: str,
                        layout: str) -> "tuple[dict[str, str], list[dict]]":
    """Map each on-disk ``<date>.md`` to its summary; warn on unreadable files."""
    summaries: "dict[str, str]" = {}
    warnings: "list[dict]" = []
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


def _read_index_manual(index_path: str) -> "str | None":
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
        raise WriterError("NON_UTF8", f"index.md is not valid UTF-8: {exc}",
                          target=index_path)
    try:
        doc = wm.parse_index(text)
    except wm.WorklogFormatError as exc:
        raise WriterError(
            "INDEX_CORRUPT_MARKERS",
            "index.md has corrupted/missing markers; refusing to guess a repair.",
            target=index_path, issues=exc.issues)
    return doc.manual


def resolve_index_language(worklog_dir: str, original: "str | None",
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


def plan_index(worklog_dir: str, overrides: "dict | None" = None,
               requested_language: "str | None" = None) -> dict:
    """Compute index.md's next text from the day files, plus pending overrides.

    ``overrides`` carry already-extracted one-line summaries for days that are
    about to be written but are not on disk yet, so a preview reflects the state
    after the day-file apply rather than the state before it.
    """
    index_path = wm.index_path(worklog_dir)
    # The index links to wherever the day files actually are, so a legacy
    # worklog rebuilt before migration still gets working links.
    layout = wm.detect_layout(worklog_dir)

    summaries, warnings = _scan_day_summaries(worklog_dir, layout)
    for date, summary in (overrides or {}).items():
        if not wm.is_valid_date(date):
            raise WriterError("INVALID_DATE",
                              f"Override key {date!r} is not a YYYY-MM-DD date.")
        summaries[date] = wm.clean_summary(summary) if summary else ""

    rows = [(d, summaries[d]) for d in sorted(summaries, reverse=True)]
    existing_manual = _read_index_manual(index_path)

    original = None
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as fh:
            original = fh.read()

    index_language, language_source = resolve_index_language(
        worklog_dir, original, requested_language)
    content = wm.render_index(rows, existing_manual, layout, index_language)
    action = ("no_change" if original == content
              else "create" if original is None else "rebuild")

    return {
        "worklog_dir": worklog_dir,
        "index_path": index_path,
        "action": action,
        "dates": [d for d, _ in rows],
        "preserved_index_manual": existing_manual is not None,
        "index_language": index_language,
        "index_language_source": language_source,
        "index_hash": {"original": sha256(original) if original is not None else None,
                       "preview": sha256(content)},
        "warnings": warnings,
        "original": original,
        "content": content,
    }


def apply_index(target: str, content: str) -> None:
    """Write index.md atomically, re-parsing the staged text before it goes live."""
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
