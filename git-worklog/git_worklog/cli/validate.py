"""``git-worklog validate`` — is what is on disk actually well-formed?

This is the read-only integrity check: day files, their GENERATED/MANUAL
markers, the index, and config. It reuses ``git_worklog.markers`` — the same
parser the writers use — rather than re-deriving the rules, so validate can
never disagree with what update/rebuild would produce.

Roadmap §12.1 also lists preview records, analysis results, evidence links and
language fields. Those are reported as ``skipped`` rather than quietly dropped:
previews and analysis are transient user-level state (their own validators live
in preview_state.py / collect_day_results.py, which run inside a pipeline where
the run id is known), and the language contract does not exist yet (PR 4).
"""

from __future__ import annotations

import json
import os

from git_worklog import config, language
from git_worklog import markers as wm


def _issue(code: str, message: str, **extra) -> dict:
    return {"code": code, "message": message, **extra}


def _validate_day(path: str, date: str) -> "tuple[list[dict], list[dict]]":
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return [_issue("UNREADABLE", f"{path}: {exc}", date=date)], []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [_issue("NON_UTF8", f"{path} is not valid UTF-8: {exc}", date=date)], []

    _, issues = wm.scan_day(text, date)
    fatal = [dict(i, date=date, target=path)
             for i in issues if i["code"] in wm.FATAL_CODES]
    warn = [dict(i, date=date, target=path)
            for i in issues if i["code"] not in wm.FATAL_CODES]
    return fatal, warn


def _validate_index(worklog_dir: str, layout: str,
                    disk_dates: "list[str]") -> "tuple[list[dict], list[dict]]":
    path = wm.index_path(worklog_dir)
    if not os.path.exists(path):
        if disk_dates:
            return [_issue("INDEX_MISSING",
                           f"{len(disk_dates)} day file(s) exist but there is no "
                           f"{path}. Rebuild it with rebuild_worklog_index.py.",
                           target=path)], []
        return [], []
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [_issue("INDEX_UNREADABLE", f"{path}: {exc}", target=path)], []

    doc, issues = wm.scan_index(text)
    fatal = [dict(i, target=path) for i in issues if i["code"] in wm.FATAL_CODES]
    warn = [dict(i, target=path) for i in issues if i["code"] not in wm.FATAL_CODES]
    if doc is None:
        return fatal, warn

    listed = {d for d, _ in doc.rows}
    on_disk = set(disk_dates)
    # A row pointing at a day file that is gone is a broken link in the one
    # document whose whole job is navigation.
    for d in sorted(listed - on_disk):
        fatal.append(_issue("INDEX_ROW_WITHOUT_FILE",
                            f"The index lists {d} but {wm.day_path(worklog_dir, d, layout)} "
                            "does not exist.", date=d, target=path))
    for d in sorted(on_disk - listed):
        warn.append(_issue("DAY_FILE_NOT_INDEXED",
                           f"{d} has a day file but no index row; rebuild the index.",
                           date=d, target=path))
    return fatal, warn


def _validate_config(worklog_dir: str) -> "tuple[list[dict], list[dict]]":
    path = wm.config_path(worklog_dir)
    if not os.path.exists(path):
        return [], []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [_issue("CONFIG_INVALID", f"{path}: {exc}", target=path)], []
    if not isinstance(cfg, dict):
        return [_issue("CONFIG_INVALID", f"{path} must contain an object.",
                       target=path)], []
    got = cfg.get("schema_version")
    if got is None:
        return [], [_issue("CONFIG_NO_SCHEMA_VERSION",
                           f"{path} has no schema_version.", target=path)]
    if not isinstance(got, int):
        return [_issue("CONFIG_INVALID",
                       f"{path}: schema_version must be an integer (got {got!r}).",
                       target=path)], []
    if got > wm.LAYOUT_VERSION:
        return [_issue("CONFIG_TOO_NEW",
                       f"{path} declares schema_version {got}, newer than this build "
                       f"understands ({wm.LAYOUT_VERSION}).", target=path)], []
    return [], []


def _validate_language(worklog_dir: str, layout: str,
                       dates: "list[str]") -> "tuple[list, list]":
    """Language settings and stamps on disk (§6.2.7, §6.2.12).

    What is checkable from a worklog alone is that its declared languages are
    tags this build can honour, and that day files carry a summary marker so the
    index can find their summary whatever language they are in. What is NOT
    checkable is whether the prose is *in* that language: engineering writing is
    full of English identifiers by contract (§6.2.9), so a detector would flag
    correct zh-TW work items. This deliberately does not try.
    """
    errors: "list[dict]" = []
    warnings: "list[dict]" = []
    cfg = config.load(worklog_dir)

    for key in ("language", "index_language"):
        raw = cfg.get(key)
        if not isinstance(raw, str) or raw.strip().lower() == language.AUTO:
            continue
        try:
            language.normalize(raw)
        except language.LanguageError as exc:
            errors.append(_issue("CONFIG_LANGUAGE_INVALID",
                                 f"config.json {key}: {exc.message}",
                                 target=wm.config_path(worklog_dir)))

    index_path = wm.index_path(worklog_dir)
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                stamped = wm.index_language_of(fh.read())
        except (OSError, UnicodeDecodeError):
            stamped = None      # the index's own validator reports this
        else:
            if stamped is not None:
                try:
                    language.normalize(stamped)
                except language.LanguageError as exc:
                    errors.append(_issue("INDEX_LANGUAGE_INVALID",
                                         f"index.md is stamped lang={stamped!r}, "
                                         f"which is not usable: {exc.message}",
                                         target=index_path))

    for d in dates:
        path = wm.day_path(worklog_dir, d, layout)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                day = wm.parse_day(fh.read(), d)
        except (OSError, UnicodeDecodeError, wm.WorklogFormatError):
            continue            # _validate_day already reported it
        if wm.summarise_generated(day.generated) and not wm.has_summary_marker(day.generated):
            # Not an error: the zh-TW heading fallback still finds it, which is
            # exactly why this is worth saying. The day works today and silently
            # loses its index summary the moment it is regenerated in another
            # language.
            warnings.append(_issue(
                "DAY_SUMMARY_UNMARKED",
                f"{d}.md has no summary marker; its index row relies on the "
                f"zh-TW heading fallback and would go blank if the day were "
                f"rewritten in another language.", target=path))

    return errors, warnings


def run(args) -> "tuple[dict, int]":
    repo = args.repo or "."
    worklog_dir = args.dir or os.path.join(repo, wm.WORKLOG_DIRNAME)

    if not os.path.isdir(worklog_dir):
        return {
            "ok": False,
            "worklog_dir": os.path.abspath(worklog_dir),
            "errors": [_issue("NOT_FOUND",
                              f"{os.path.abspath(worklog_dir)} does not exist.",
                              target=os.path.abspath(worklog_dir))],
            "warnings": [], "day_count": 0,
        }, 2

    layout = wm.detect_layout(worklog_dir)
    dates = wm.list_day_dates(worklog_dir, layout)

    errors: "list[dict]" = []
    warnings: "list[dict]" = []
    for d in dates:
        f, w = _validate_day(wm.day_path(worklog_dir, d, layout), d)
        errors.extend(f)
        warnings.extend(w)

    f, w = _validate_index(worklog_dir, layout, dates)
    errors.extend(f)
    warnings.extend(w)
    f, w = _validate_config(worklog_dir)
    errors.extend(f)
    warnings.extend(w)
    f, w = _validate_language(worklog_dir, layout, dates)
    errors.extend(f)
    warnings.extend(w)

    if layout == wm.LAYOUT_LEGACY:
        warnings.append(_issue("LEGACY_LAYOUT",
                               f"{worklog_dir} uses the pre-v0.6 flat layout. It is "
                               "valid and readable, but not writable; migrate it with "
                               "migrate_legacy_worklog.py --from-dir."))

    return {
        "ok": not errors,
        "worklog_dir": os.path.abspath(worklog_dir),
        "layout": layout,
        "day_count": len(dates),
        "dates": dates,
        "errors": errors,
        "warnings": warnings,
        "skipped": [
            {"check": "preview_records",
             "reason": "Transient user-level state; validated by preview_state.py "
                       "at apply time, where the run's preview_id is known."},
            {"check": "analysis_results",
             "reason": "Transient user-level state; validated by collect_day_results.py "
                       "within a run, where the run_dir is known."},
            {"check": "result_language",
             "reason": "Analysis results are transient user-level state; their "
                       "language is checked by collect_day_results.py against the "
                       "run's manifest, where the expected tag is known."},
        ],
    }, (1 if errors else 0)


def render_text(p: dict) -> str:
    lines = [f"git-worklog validate — {p['worklog_dir']}"]
    if p.get("layout"):
        lines.append(f"  layout: {p['layout']}, {p['day_count']} day file(s)")
    lines.append("")
    for e in p.get("errors", []):
        lines.append(f"  ✗ {e['code']}: {e['message']}")
    for w in p.get("warnings", []):
        lines.append(f"  ! {w['code']}: {w['message']}")
    if not p.get("errors") and not p.get("warnings"):
        lines.append("  ✓ No problems found.")
    lines.append("")
    lines.append("OK." if p["ok"] else f"FAILED: {len(p.get('errors', []))} error(s).")
    return "\n".join(lines) + "\n"
