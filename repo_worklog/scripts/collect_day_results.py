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
repository, under ``~/.repo_worklog/analysis/<run_id>/<date>.json``, alongside
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
import sys
from datetime import datetime

import worklog_markers as wm

ANALYSIS_DIR = os.path.join(os.path.expanduser("~"), ".repo_worklog", "analysis")

# Top-level keys required by the Day Subagent return schema
# (references/subagent-contract.md §6). All must be present even when empty.
REQUIRED_KEYS = [
    "date", "timezone", "status", "confidence", "escalation_recommended",
    "escalation_reasons", "has_changes", "commits", "work_items", "fixes",
    "refactors", "tests", "database_changes", "configuration_changes",
    "deployment_changes", "uncommitted_changes", "handoff_notes",
    "uncertainties", "evidence",
]

# Keys required on each work_items[] entry (§6).
REQUIRED_WORK_ITEM_KEYS = [
    "title", "summary", "behavior_change", "implementation", "impact", "files",
    "commits", "tests", "risks", "maintenance_notes", "follow_ups",
    "confidence", "evidence",
]

VALID_STATUS = {"complete", "partial", "failed"}
VALID_CONFIDENCE = {"verified", "inferred", "unknown"}


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
    os.makedirs(run_dir, exist_ok=True)
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


def _validate(obj, date: str) -> list[dict]:
    """Structural check against the §6 return schema. Returns issue dicts."""
    issues: list[dict] = []
    if not isinstance(obj, dict):
        return [{"code": "RESULT_NOT_OBJECT",
                 "message": "The result file must contain a JSON object."}]

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
    return issues


def cmd_read(args: argparse.Namespace) -> int:
    dates = _parse_dates(args.dates)
    run_dir = args.run_dir
    if not os.path.isdir(run_dir):
        _fail("RUN_DIR_MISSING", f"No such analysis run directory: {run_dir}.",
              run_dir=run_dir)

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

        issues = _validate(obj, date)
        if issues:
            invalid.append({"date": date, "path": path,
                            "code": issues[0]["code"],
                            "message": issues[0]["message"],
                            "issues": issues})
            continue
        results[date] = obj

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
        "results": results,
        "complete": complete,
        "degraded": degraded,
        "missing": missing,
        "invalid": invalid,
        "failed_dates": failed_dates,
        # A run is partial if any day failed to arrive, arrived malformed, or
        # reported its own status as partial/failed. Apply is blocked by default
        # for a partial run (subagent-contract.md §11).
        "partial_run": bool(failed_dates or degraded),
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
                        "~/.repo_worklog/analysis/<run_id>).")

    r = sub.add_parser("read", help="Read and validate the run's result files.")
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
