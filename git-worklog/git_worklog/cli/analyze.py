"""``git-worklog analyze`` — hand the day's work to the agent, then check it back in.

Two halves of one contract (roadmap §7):

``prepare`` mints a run and writes one Analysis Manifest per day. It does not
analyse anything -- it decides *what must be analysed* and *in which language*,
both of which are deterministic, and hands each day an output path to write to.

``collect`` reads what came back and refuses to believe it on request. Between
the two sits the hosting agent's LLM, which is the only part that reads code and
writes prose. That division is why the CLI needs no model API key (§6.1).

Run layout (§7.1)::

    ~/.git-worklog/analysis/<run_id>/
        tasks/<date>.json      the manifest -- what to analyse
        results/<date>.json    the result -- what was found

The two directories mean ``collect`` can tell a task that was never answered
from a result nobody asked for, and ``results/`` is just a directory of
``<date>.json`` files -- which is exactly what ``results.read_run`` already
reads, so both front ends share one validator rather than one per layout.
"""

from __future__ import annotations

import json
import os
from datetime import date as date_cls, datetime, timedelta

from git_worklog import language
from git_worklog import markers as wm
from git_worklog.analysis import SCHEMA_VERSION, AnalysisError
from git_worklog.analysis import history as ah
from git_worklog.analysis import manifest as am
from git_worklog.analysis import results as ar

TASKS_SUBDIR = "tasks"
RESULTS_SUBDIR = "results"


def _day_bounds(date_str: str, tz) -> "tuple[datetime, datetime]":
    """Half-open [local 00:00, next 00:00), matching resolve_date_range.py."""
    d = datetime.fromisoformat(date_str).date()
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start, end


def _date_range(start: str, end: str) -> "list[str]":
    """Every calendar date in [start, end], inclusive at both ends."""
    for value in (start, end):
        if not wm.is_valid_date(value):
            raise AnalysisError("INVALID_DATE",
                                f"Not an ISO YYYY-MM-DD date: {value}.", date=value)
    try:
        first = date_cls.fromisoformat(start)
        last = date_cls.fromisoformat(end)
    except ValueError as exc:
        raise AnalysisError("INVALID_DATE", f"Not a real calendar date: {exc}.")
    if first > last:
        raise AnalysisError("BAD_RANGE",
                            f"--from {start} is after --to {end}.",
                            **{"from": start, "to": end})
    out = []
    while first <= last:
        out.append(first.isoformat())
        first += timedelta(days=1)
    return out


def _zone(name: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise AnalysisError("INVALID_TIMEZONE", f"Unknown IANA timezone: {name}.",
                            timezone=name)


def _prepare(args) -> "tuple[dict, int]":
    dates = _date_range(getattr(args, "from"), args.to)
    tz = _zone(args.timezone)
    model = am.parse_model(args.model_json)

    # Resolved once for the whole run, never per day: a manifest's resolved
    # language is what each day's result is checked against, and days that
    # disagree block the run (§6.2.8). Deciding it per day would let the same
    # run ask for two languages and then reject itself for having got them.
    lang = am.resolve_language(args.language, args.language_source, args.dir)

    info = ah.repo_info(args.repo)
    minted = ar.mint_run(dates, args.run_dir)
    run_dir = minted["run_dir"]
    tasks_dir = os.path.join(run_dir, TASKS_SUBDIR)
    results_dir = os.path.join(run_dir, RESULTS_SUBDIR)
    for d in (tasks_dir, results_dir):
        os.makedirs(d, mode=0o700, exist_ok=True)

    tasks = []
    for date in dates:
        start, end = _day_bounds(date, tz)
        history = ah.collect(
            repo=args.repo, since=start.isoformat(), until=end.isoformat(),
            date_field=args.date_field, worklog_dir=args.worklog_dir,
        )
        result_path = os.path.join(results_dir, f"{date}.json")
        manifest = am.build(
            date=date, timezone=args.timezone, history=history,
            include_uncommitted=False, provider=args.provider, model=model,
            lang=lang, run_id=minted["run_id"], result_path=result_path,
        )
        manifest_path = os.path.join(tasks_dir, f"{date}.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        tasks.append({
            "date": date,
            "manifest_path": manifest_path,
            "result_path": result_path,
            "has_changes": manifest["has_changes"],
            "commit_count": manifest["commit_count"],
            "large_day": manifest["large_day"],
        })

    payload = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "run_id": minted["run_id"],
        "run_dir": run_dir,
        "repository": {"root": info["root"], "git_dir": info["git_dir"],
                       "head": info["head"], "branch": info["branch"]},
        "language": lang.as_manifest(),
        "tasks": tasks,
    }
    if lang.warnings:
        payload["warnings"] = list(lang.warnings)
    return payload, 0


def _run_dir_for(args) -> str:
    if args.run_dir:
        return args.run_dir
    if not args.run_id:
        raise AnalysisError("NO_RUN",
                            "Pass --run-id (from `analyze prepare`) or --run-dir.")
    return os.path.join(ar.analysis_dir(), args.run_id)


def _collect(args) -> "tuple[dict, int]":
    run_dir = _run_dir_for(args)
    tasks_dir = os.path.join(run_dir, TASKS_SUBDIR)
    results_dir = os.path.join(run_dir, RESULTS_SUBDIR)
    if not os.path.isdir(tasks_dir):
        raise AnalysisError(
            "RUN_NOT_PREPARED",
            f"{run_dir} has no {TASKS_SUBDIR}/ directory, so there is nothing to "
            f"collect against. Was this run created by `analyze prepare`?",
            run_dir=run_dir)

    # The tasks decide what the run asked for. Trusting a caller-supplied date
    # list instead would let a day be dropped from the run simply by leaving it
    # out of the second command -- the exact failure `missing` exists to catch.
    dates, expected_language = [], None
    for name in sorted(os.listdir(tasks_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(tasks_dir, name), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        dates.append(manifest["date"])
        block = manifest.get("language") or {}
        if block.get("resolved"):
            expected_language = block["resolved"]
    if not dates:
        raise AnalysisError("RUN_HAS_NO_TASKS",
                            f"{tasks_dir} contains no manifests.", run_dir=run_dir)

    payload = ar.read_run(results_dir, dates, args.repo, expected_language)

    # A result nobody asked for is not a bonus: it means the run directory holds
    # analysis of a day this run never dispatched, and merging it would put a
    # day into the worklog that was never prepared or language-checked.
    unknown = sorted(
        name[:-len(".json")]
        for name in os.listdir(results_dir)
        if name.endswith(".json") and name[:-len(".json")] not in dates
    ) if os.path.isdir(results_dir) else []

    payload.update({
        "run_id": args.run_id,
        "run_dir": run_dir,
        "expected_language": expected_language,
        "unknown": unknown,
    })
    if unknown:
        payload["partial_run"] = True
    return payload, (1 if payload["partial_run"] else 0)


def run(args) -> "tuple[dict, int]":
    try:
        if args.analyze_command == "prepare":
            return _prepare(args)
        return _collect(args)
    except AnalysisError as exc:
        return {"ok": False, "errors": [
            {"code": exc.code, "message": exc.message, **exc.extra}]}, 2
    except language.LanguageError as exc:
        return {"ok": False, "errors": [
            {"code": exc.code, "message": exc.message, **exc.extra}]}, 2
    except ah.GitError as exc:
        return {"ok": False, "errors": [
            {"code": "GIT_ERROR", "message": str(exc)}]}, 2


def render_text(p: dict) -> str:
    if not p.get("ok"):
        return "".join(f"error: {e['message']}\n" for e in p.get("errors", []))

    lines = []
    if "tasks" in p:
        lang = p["language"]
        lines.append(f"run {p['run_id']}  ({lang['resolved']}, via {lang['source']})\n")
        lines.append(f"  {p['run_dir']}\n\n")
        for t in p["tasks"]:
            note = "no changes" if not t["has_changes"] else (
                f"{t['commit_count']} commit(s)"
                + (", large day" if t["large_day"] else ""))
            lines.append(f"  {t['date']}  {note}\n")
        lines.append(f"\n{len(p['tasks'])} task(s) written. "
                     f"Each day's subagent writes to its result_path.\n")
        return "".join(lines)

    lines.append(f"run {p.get('run_id')}\n")
    lines.append(f"  complete : {len(p['complete'])}\n")
    for label in ("degraded", "missing", "unknown"):
        if p.get(label):
            lines.append(f"  {label:9}: {', '.join(p[label])}\n")
    for item in p.get("invalid", []):
        lines.append(f"  invalid  : {item['date']} — {item['message']}\n")
    if p.get("language_inconsistent"):
        lines.append(f"  languages: {', '.join(p['languages_seen'])} — a run "
                     f"must use one\n")
    lines.append("\n" + ("partial run: this cannot go to preview as-is\n"
                         if p["partial_run"] else "run complete\n"))
    return "".join(lines)
