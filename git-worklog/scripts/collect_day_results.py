#!/usr/bin/env python3
"""Exchange Day Subagent results through files, and validate them.

Why files rather than return values
-----------------------------------
A Day Subagent's result is the whole point of the expensive part of a run: it
represents real patches read and real code understood. Passing it back as the
subagent's *reply text* makes that result hostage to the host's return channel,
which is the weakest link in the pipeline:

* it can drop or truncate content (observed in practice — a subagent that did
  63k tokens of correct analysis returned nothing),
* a day's object is routinely 15KB+ and a large day is far bigger,
* the skill targets several hosts (Claude Code / Codex / Gemini) whose return
  semantics and size limits all differ,
* a dropped reply loses the analysis outright, forcing a full re-run.

A file has none of those properties. It also survives the run, so a failure
downstream (rendering, preview, apply) never costs the analysis again, and a
human can read exactly what a subagent concluded.

So: the orchestrator mints a run directory with ``init``, hands each subagent its
own output path, and collects the lot with ``read``. Results live outside the
repository, under ``~/.git-worklog/analysis/<run_id>/<date>.json``, alongside
the preview state — the worklog directory is for the worklog, not for scratch.

What ``read`` guarantees
------------------------
A missing or malformed file is reported as **that day failing**, explicitly. This
is the deterministic half of the orchestrator's completeness check
(`references/subagent-contract.md` §1): a day whose result never arrived must
never be silently skipped, and must never be back-filled from commit messages.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

import _bootstrap  # noqa: F401 — must precede any git_worklog import
import worklog_markers as wm  # noqa: F401

from git_worklog import language, paths

ANALYSIS_DIR = paths.analysis_dir()

# Top-level keys required by the Day Subagent return schema
# (references/subagent-contract.md §6). All must be present even when empty.
REQUIRED_KEYS = [
    "date", "timezone", "language", "status", "confidence",
    "escalation_recommended", "escalation_reasons", "has_changes", "commits",
    "work_items", "fixes", "refactors", "tests", "database_changes",
    "configuration_changes", "deployment_changes", "uncommitted_changes",
    "handoff_notes", "uncertainties", "evidence",
]

# Keys required on each work_items[] entry (§6).
REQUIRED_WORK_ITEM_KEYS = [
    "title", "summary", "behavior_change", "implementation", "impact", "files",
    "commits", "tests", "risks", "maintenance_notes", "follow_ups",
    "confidence", "evidence",
]

VALID_STATUS = {"complete", "partial", "failed"}
VALID_CONFIDENCE = {"verified", "inferred", "unknown"}

# Evidence entries are objects, not sentences (subagent-contract.md §8). commit
# and file are required because they are the two facts always knowable and always
# checkable against the repository. Left as free text, the field reliably decays
# into a restatement of the commit subject -- observed in a real run:
# "commit 4d08ee4: 完整改造" cites nothing a reader can open.
REQUIRED_EVIDENCE_KEYS = ["commit", "file"]


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _parse_dates(raw: str) -> list[str]:
    dates = [d.strip() for d in raw.split(",") if d.strip()]
    if not dates:
        _fail("NO_DATES", "Provide at least one date via --dates.")
    for d in dates:
        if not wm.is_valid_date(d):
            _fail("INVALID_DATE", f"Not an ISO YYYY-MM-DD date: {d}.", date=d)
    # Preserve caller order but drop duplicates.
    return list(dict.fromkeys(dates))


def _result_path(run_dir: str, date: str) -> str:
    return os.path.join(run_dir, f"{date}.json")


def cmd_init(args: argparse.Namespace) -> int:
    dates = _parse_dates(args.dates)
    now = datetime.now()
    basis = hashlib.sha256(
        f"{now.isoformat()}|{','.join(dates)}".encode("utf-8")).hexdigest()[:6]
    run_id = f"rw-{now.strftime('%Y%m%d')}-{basis}"
    run_dir = args.run_dir or os.path.join(ANALYSIS_DIR, run_id)
    paths.ensure_dir(run_dir)
    _emit({
        "ok": True,
        "run_id": run_id,
        "run_dir": run_dir,
        "dates": dates,
        # One path per date: hand each Day Subagent exactly its own, so two days
        # can never race on one file.
        "paths": {d: _result_path(run_dir, d) for d in dates},
    })
    return 0


# An identifier inside a `symbol` field. Short tokens are ignored: `get` in
# `CacheLayer.get` says nothing, and a qualified name never appears verbatim in
# source anyway (the file holds `class CacheLayer:` and `def get`), so the field
# is checked token by token rather than as one string.
_SYMBOL_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


class _Tree:
    """Reads files as they were at a commit, and remembers what it read.

    Evidence must be checked against the day's tree, never the checkout: the
    working tree holds every change made since, so a symbol that exists today
    proves nothing about the day being described. One subprocess per distinct
    (commit, file) — a day's evidence cites the same few files repeatedly.
    """

    def __init__(self, repo: str):
        self.repo = repo
        self._files: dict = {}
        self._commits: dict = {}
        self._shallow = None

    def _git(self, *args) -> "tuple[int, str]":
        p = subprocess.run(["git", "-C", self.repo, *args],
                           capture_output=True, text=True)
        return p.returncode, p.stdout

    def is_shallow(self) -> bool:
        if self._shallow is None:
            _, out = self._git("rev-parse", "--is-shallow-repository")
            self._shallow = out.strip() == "true"
        return self._shallow

    def has_commit(self, commit: str) -> bool:
        if commit not in self._commits:
            code, _ = self._git("cat-file", "-e", f"{commit}^{{commit}}")
            self._commits[commit] = code == 0
        return self._commits[commit]

    def file_at(self, commit: str, path: str) -> "str | None":
        key = (commit, path)
        if key not in self._files:
            code, out = self._git("show", f"{commit}:{path}")
            self._files[key] = out if code == 0 else None
        return self._files[key]


def _validate_evidence(entries, where: str, tree: "_Tree | None" = None) -> list[dict]:
    """Evidence must be checkable citations, not prose — and must check out.

    Presence of ``commit`` and ``file`` was all this ever enforced, which left
    ``symbol`` and ``lines`` decorative: a real run cited `migrate_directory`
    for a function actually called `parse_legacy`, `preview_dir` for
    `previews_dir`, and a line range past the end of the file — and all of it
    passed (issue #15). Every fabrication was a *plausible* name, which is
    precisely why reading cannot catch them and why this has to be mechanical.
    """
    if entries is None:
        return []
    if not isinstance(entries, list):
        return [{"code": "EVIDENCE_INVALID",
                 "message": f"{where} must be an array."}]
    issues: list[dict] = []
    for idx, e in enumerate(entries):
        at = f"{where}[{idx}]"
        if not isinstance(e, dict):
            issues.append({
                "code": "EVIDENCE_INVALID",
                "message": f"{at} must be an object with at least "
                           f"{REQUIRED_EVIDENCE_KEYS}; prose is not evidence "
                           f"(got {type(e).__name__}).",
                "path": at,
            })
            continue
        missing = [k for k in REQUIRED_EVIDENCE_KEYS
                   if not str(e.get(k) or "").strip()]
        if missing:
            issues.append({
                "code": "EVIDENCE_INVALID",
                "message": f"{at} is missing: {', '.join(missing)}.",
                "path": at,
                "missing_keys": missing,
            })
            continue
        if tree is not None:
            issues.extend(_verify_against_tree(e, at, tree))
    return issues


def _verify_against_tree(e: dict, at: str, tree: _Tree) -> list[dict]:
    """Check one evidence entry against the repository it cites."""
    commit = str(e["commit"]).strip()
    path = str(e["file"]).strip()

    if not tree.has_commit(commit):
        # A missing commit in a shallow clone is the environment's doing, not
        # the subagent's, and failing the day for it would punish the wrong
        # party. In a full clone there is no such excuse: the hash is invented.
        if tree.is_shallow():
            return [{
                "code": "EVIDENCE_UNVERIFIABLE",
                "message": f"{at} cites commit {commit}, which this shallow "
                           f"clone does not have, so its evidence could not be "
                           f"checked. `git fetch --unshallow` to verify it.",
                "path": at, "commit": commit,
            }]
        return [{
            "code": "EVIDENCE_COMMIT_UNKNOWN",
            "message": f"{at} cites commit {commit}, which is not in this "
                       f"repository.",
            "path": at, "commit": commit,
        }]

    src = tree.file_at(commit, path)
    if src is None:
        return [{
            "code": "EVIDENCE_FILE_NOT_IN_COMMIT",
            "message": f"{at} cites {path} at commit {commit}, but that commit's "
                       f"tree has no such file. A file that exists today may not "
                       f"have existed then.",
            "path": at, "commit": commit, "file": path,
        }]

    issues: list[dict] = []
    symbol = str(e.get("symbol") or "").strip()
    if symbol:
        absent = [t for t in _SYMBOL_TOKEN_RE.findall(symbol) if t not in src]
        if absent:
            issues.append({
                "code": "EVIDENCE_SYMBOL_NOT_FOUND",
                "message": f"{at} cites symbol {symbol!r} in {path} at {commit}, "
                           f"but {', '.join(repr(a) for a in absent)} does not "
                           f"appear there. Cite what the code is actually called.",
                "path": at, "commit": commit, "file": path,
                "symbol": symbol, "absent": absent,
            })

    lines = str(e.get("lines") or "").strip()
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", lines) if lines else None
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        total = len(src.splitlines())
        if lo < 1 or hi > total or lo > hi:
            issues.append({
                "code": "EVIDENCE_LINES_OUT_OF_RANGE",
                "message": f"{at} cites lines {lines} of {path} at {commit}, "
                           f"which has {total} line(s).",
                "path": at, "commit": commit, "file": path,
                "lines": lines, "file_lines": total,
            })
    return issues


def _validate_language(obj, expected: "str | None") -> list[dict]:
    """Check the result's language field against the manifest's (§6.2.9).

    Structural only, on purpose. The roadmap is explicit that a natural-language
    detector must not decide this: engineering prose is full of English
    identifiers, paths and API names by contract, so a detector reading a
    correct zh-TW work item would see enough English to doubt it. What is
    checkable is that the subagent declared a language, that the tag is
    well-formed, and that it is the one it was asked for.
    """
    issues: list[dict] = []
    raw = obj.get("language")
    if raw is None:
        return issues  # absence is already reported by the required-keys check

    try:
        tag = language.normalize(raw)
    except language.LanguageError as exc:
        return [{"code": "RESULT_BAD_LANGUAGE", "message": exc.message,
                 "language": raw}]

    if expected is not None and tag != expected:
        issues.append({
            "code": "RESULT_LANGUAGE_MISMATCH",
            "message": f"Result is in {tag!r} but the manifest asked for "
                       f"{expected!r}.",
            "language": tag,
            "expected_language": expected,
        })
    return issues


def _validate(obj, date: str, expected_language: "str | None" = None,
              tree: "_Tree | None" = None) -> list[dict]:
    """Structural check against the §6 return schema. Returns issue dicts."""
    issues: list[dict] = []
    if not isinstance(obj, dict):
        return [{"code": "RESULT_NOT_OBJECT",
                 "message": "The result file must contain a JSON object."}]

    issues.extend(_validate_language(obj, expected_language))

    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        issues.append({
            "code": "RESULT_SCHEMA_INVALID",
            "message": f"Missing required keys: {', '.join(missing)}.",
            "missing_keys": missing,
        })

    if obj.get("date") != date:
        issues.append({
            "code": "RESULT_DATE_MISMATCH",
            "message": f"Result says date={obj.get('date')!r} but it was "
                       f"produced for {date!r}.",
        })

    status = obj.get("status")
    if status is not None and status not in VALID_STATUS:
        issues.append({
            "code": "RESULT_BAD_STATUS",
            "message": f"status must be one of {sorted(VALID_STATUS)} (got {status!r}).",
        })

    confidence = obj.get("confidence")
    if confidence is not None and confidence not in VALID_CONFIDENCE:
        issues.append({
            "code": "RESULT_BAD_CONFIDENCE",
            "message": f"confidence must be one of {sorted(VALID_CONFIDENCE)} "
                       f"(got {confidence!r}).",
        })

    issues.extend(_validate_evidence(obj.get("evidence"), "evidence", tree))

    work_items = obj.get("work_items")
    if work_items is not None and not isinstance(work_items, list):
        issues.append({"code": "RESULT_SCHEMA_INVALID",
                       "message": "work_items must be an array."})
    elif isinstance(work_items, list):
        for idx, item in enumerate(work_items):
            if not isinstance(item, dict):
                issues.append({
                    "code": "WORK_ITEM_INVALID",
                    "message": f"work_items[{idx}] must be an object.",
                })
                continue
            item_missing = [k for k in REQUIRED_WORK_ITEM_KEYS if k not in item]
            if item_missing:
                issues.append({
                    "code": "WORK_ITEM_SCHEMA_INVALID",
                    "message": f"work_items[{idx}] is missing: "
                               f"{', '.join(item_missing)}.",
                    "index": idx,
                    "missing_keys": item_missing,
                })
            issues.extend(
                _validate_evidence(item.get("evidence"),
                                   f"work_items[{idx}].evidence", tree))
    return issues


def cmd_read(args: argparse.Namespace) -> int:
    dates = _parse_dates(args.dates)
    run_dir = args.run_dir
    if not os.path.isdir(run_dir):
        _fail("RUN_DIR_MISSING", f"No such analysis run directory: {run_dir}.",
              run_dir=run_dir)

    tree = _Tree(args.repo)
    if not tree.has_commit("HEAD"):
        _fail("NOT_A_GIT_REPO",
              f"{args.repo} is not a readable Git repository, so evidence "
              f"cannot be checked against the commits it cites.",
              repo=args.repo)

    expected_language = None
    if args.language:
        try:
            expected_language = language.normalize(args.language)
        except language.LanguageError as exc:
            _fail(exc.code, exc.message, **exc.extra)

    results: dict[str, dict] = {}
    missing: list[str] = []
    invalid: list[dict] = []

    for date in dates:
        path = _result_path(run_dir, date)
        if not os.path.isfile(path):
            # The subagent never delivered. This is a failed day, not an empty
            # one -- the caller must not treat it as "nothing happened".
            missing.append(date)
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
        except json.JSONDecodeError as exc:
            invalid.append({"date": date, "path": path,
                            "code": "RESULT_NOT_JSON",
                            "message": f"Result file is not valid JSON: {exc}"})
            continue
        except (OSError, UnicodeDecodeError) as exc:
            invalid.append({"date": date, "path": path,
                            "code": "RESULT_UNREADABLE",
                            "message": f"Could not read result file: {exc}"})
            continue

        issues = _validate(obj, date, expected_language, tree)
        if issues:
            invalid.append({"date": date, "path": path,
                            "code": issues[0]["code"],
                            "message": issues[0]["message"],
                            "issues": issues})
            continue
        results[date] = obj

    # Every day in a run must be written in one language (§6.2.8). When the
    # manifest's language was passed in, each day was already checked against
    # it, so this can only fire for a run collected without one -- but it fires
    # then, because a worklog whose days silently switch language mid-run is not
    # something to discover after apply.
    languages = sorted({results[d]["language"] for d in results
                        if isinstance(results[d].get("language"), str)})
    run_language = languages[0] if len(languages) == 1 else None
    language_inconsistent = len(languages) > 1

    complete = [d for d in dates if d in results
                and results[d].get("status") == "complete"]
    degraded = [d for d in dates if d in results
                and results[d].get("status") in ("partial", "failed")]
    failed_dates = missing + [i["date"] for i in invalid]

    return_code = 0
    _emit({
        "ok": True,
        "run_dir": run_dir,
        "dates": dates,
        "language": run_language,
        "language_inconsistent": language_inconsistent,
        "languages_seen": languages,
        "results": results,
        "complete": complete,
        "degraded": degraded,
        "missing": missing,
        "invalid": invalid,
        "failed_dates": failed_dates,
        # A run is partial if any day failed to arrive, arrived malformed, or
        # reported its own status as partial/failed. Apply is blocked by default
        # for a partial run (subagent-contract.md §11) -- which is also the lever
        # that stops a mixed-language run from reaching the worklog, since a day
        # in the wrong language is wrong in a way no amount of retrying the
        # other days fixes.
        "partial_run": bool(failed_dates or degraded or language_inconsistent),
        "escalation_suggested_dates": [
            d for d in dates
            if d in results and results[d].get("escalation_recommended")
        ],
    })
    return return_code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Exchange and validate Day Subagent results via files.")
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="Mint a run directory and per-date output paths.")
    i.add_argument("--dates", required=True,
                   help="Comma-separated ISO dates this run covers.")
    i.add_argument("--run-dir",
                   help="Override the run directory (default: "
                        "~/.git-worklog/analysis/<run_id>).")

    r = sub.add_parser("read", help="Read and validate the run's result files.")
    r.add_argument("--repo", required=True,
                   help="Repository the evidence cites. Every entry is checked "
                        "against the tree of the commit it names — not the "
                        "checkout, which holds everything changed since.")
    r.add_argument("--language", default=None,
                   help="The manifest's resolved language. Each result must "
                        "declare this exact tag; omit only when collecting a "
                        "run whose manifest language is unknown.")
    r.add_argument("--run-dir", required=True, help="The run directory from init.")
    r.add_argument("--dates", required=True,
                   help="Comma-separated ISO dates that were dispatched.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return cmd_init(args)
        return cmd_read(args)
    except OSError as exc:
        _fail("IO_ERROR", f"Filesystem error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
