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

from git_worklog import dates as gwdates
from git_worklog import language
from git_worklog import providers
from git_worklog.analysis import (
    PARTS_SUBDIR, RESULTS_SUBDIR, SCHEMA_VERSION, TASKS_SUBDIR, AnalysisError,
)
from git_worklog.analysis import history as ah
from git_worklog.analysis import manifest as am
from git_worklog.analysis import results as ar
from git_worklog.analysis import worktree as aw


def _resolve_model(args) -> "tuple[str, dict, list]":
    """The provider and model for the whole run, plus any warnings to surface.

    ``--host`` asks the resolver; ``--provider``/``--model-json`` state the answer
    directly. Both stay: the skill knows its host, while a caller reconstructing a
    previous run has the model object and no host to re-derive it from.

    Returns ``(provider, model, warnings)``. The warnings are not decoration --
    an honoured-but-deprecated env var is reported here or nowhere, and the whole
    point of that warning is that a model must never change under the user in
    silence.
    """
    if not args.host:
        if args.model or args.escalate:
            raise AnalysisError(
                "ARG_CONFLICT",
                "--model and --escalate select a model from the host's config, so "
                "they need --host. With --provider, state the model in "
                "--model-json instead.")
        # Unstated provider keeps its historical default rather than becoming a
        # None on the manifest.
        return (args.provider or "anthropic"), am.parse_model(args.model_json), []
    if args.provider or args.model_json:
        raise AnalysisError(
            "ARG_CONFLICT",
            "--host resolves the provider and model; do not also pass "
            "--provider / --model-json.")
    resolved = providers.resolve(host=args.host, model=args.model,
                                 escalate=args.escalate)
    return resolved["provider"], resolved["model"], list(resolved.get("warnings", []))


def _model_label(model: "dict | None", provider: str) -> str:
    """Name the resolved model for a human-facing warning.

    ``model`` is null when no ``--host`` resolved one -- the run uses the
    provider's default, which is exactly the case a large-day warning most needs
    to name, so it says so rather than leaving a blank.
    """
    if isinstance(model, dict) and model.get("model_id"):
        return model["model_id"]
    return f"the default {provider} model"


def _large_day_warning(date: str, manifest: dict, label: str) -> "dict | None":
    """Surface a day too big for one subagent, before it is dispatched.

    ``large_day`` has been on the manifest all along, but as advice with no
    consequence: nothing sized the day against the model it was about to be given
    to, and ``escalation_recommended`` came back *from* the subagent -- the party
    least able to notice it was overwhelmed (#22). This moves the signal to
    prepare, which knows the commit count, the file count and the model before
    dispatch and needs no one's opinion to see the day is large.

    It surfaces the counts, not a verdict: a 60-file day and a 26-file day are
    both ``large_day`` yet not the same problem, and the fix for that is to show
    the numbers, not to invent a second threshold. The orchestrator turns this
    into a choice -- fan out, escalate, or proceed (SKILL.md) -- exactly as the
    over-30-day and gap prompts do. Proceeding is allowed; nothing is refused.
    """
    if not manifest["large_day"]:
        return None
    files = len(manifest["changed_files"])
    groups = len(manifest["file_groups"])
    fan = manifest["recommended_code_analysis_subagents"]
    return {
        "code": "LARGE_DAY",
        "date": date,
        "message": (
            f"{date} is a large day: {manifest['commit_count']} commit(s), "
            f"{files} changed file(s) in {groups} group(s), on {label}. One "
            f"subagent may not hold it — consider fanning out into the {fan} "
            f"recommended Code Analysis Subagents, or escalating the model, "
            f"before dispatching. Proceeding as-is is allowed."),
        "commit_count": manifest["commit_count"],
        "changed_file_count": files,
        "group_count": groups,
        "recommended_code_analysis_subagents": fan,
    }


def _prepare(args) -> "tuple[dict, int]":
    # One date contract, not a second one written out here. dates.resolve()
    # accepts every form the user can give (7d / --days / --date / --from+--to),
    # validates them, and hands back each day's half-open window already
    # computed -- so prepare cannot disagree with the rule about where a day
    # starts, because it no longer states it.
    date, days = gwdates.absorb_shortcut(args.shortcut, args.date, args.days)
    resolved = gwdates.resolve(
        date=date, days=days, from_=getattr(args, "from"), to=args.to,
        timezone=args.timezone, today=args.today,
    )
    dates = [d["date"] for d in resolved["dates"]]
    windows = {d["date"]: (d["start"], d["end"]) for d in resolved["dates"]}
    tz_name = resolved["timezone"]
    provider, model, model_warnings = _resolve_model(args)

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
    parts_dir = os.path.join(run_dir, PARTS_SUBDIR)
    for d in (tasks_dir, results_dir, parts_dir):
        os.makedirs(d, mode=0o700, exist_ok=True)

    # Uncommitted work belongs to today and to no other day: a file's mtime says
    # when it was last written, not when the work happened, so there is nothing
    # to attribute a dirty worktree to on any past date. "Today" is read in the
    # run's timezone, which is the same clock that decides where a day starts.
    today = resolved["today"]
    worktree = aw.inspect(args.repo) if args.include_uncommitted else None

    model_label = _model_label(model, provider)
    tasks = []
    large_day_warnings = []
    for date in dates:
        start, end = windows[date]
        history = ah.collect(
            repo=args.repo, since=start, until=end,
            date_field=args.date_field, worklog_dir=args.worklog_dir,
        )
        day_worktree = worktree if (worktree and date == today) else None
        result_path = os.path.join(results_dir, f"{date}.json")
        manifest = am.build(
            date=date, timezone=tz_name, history=history,
            worktree=day_worktree, include_uncommitted=bool(day_worktree),
            provider=provider, model=model,
            lang=lang, run_id=minted["run_id"], result_path=result_path,
            parts_dir=parts_dir,
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
            # The counts, not just the boolean: a 60-file day and a 26-file day
            # are both large_day and are not the same dispatch decision (#22).
            # The recommended fan-out count is a large-day mechanic, so it rides
            # the LARGE_DAY warning and the manifest, not this per-day summary.
            "changed_file_count": len(manifest["changed_files"]),
            "group_count": len(manifest["file_groups"]),
            "large_day": manifest["large_day"],
            "include_uncommitted": bool(day_worktree),
        })
        warning = _large_day_warning(date, manifest, model_label)
        if warning:
            large_day_warnings.append(warning)

    payload = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "run_id": minted["run_id"],
        "run_dir": run_dir,
        # The whole of repo_info, not a chosen subset: this is the only place
        # the caller learns the repository state now, and the previous
        # `collect_git_history --info-only` step it replaces reported all of it.
        "repository": info,
        # What the date spec actually resolved to. Reported rather than assumed:
        # the caller may have passed "7d" and is entitled to see which seven days
        # that turned out to be, and in which zone, before any of it is written.
        "range": {"mode": resolved["mode"], "from": dates[0], "to": dates[-1],
                  "days_count": resolved["days_count"], "today": today},
        "timezone": {"resolved": tz_name, "source": resolved["timezone_source"]},
        "provider": provider,
        "model": model,
        "language": lang.as_manifest(),
        "tasks": tasks,
    }
    if worktree:
        payload["worktree_fingerprint"] = worktree["worktree_fingerprint"]
    warnings = list(lang.warnings) + model_warnings + large_day_warnings
    if worktree and today not in dates:
        # Asked for uncommitted work on a range that does not contain today, so
        # there is nowhere to put it. Silence here would read as "the worktree
        # was clean", which is a different and wrong statement.
        warnings.append({
            "code": "UNCOMMITTED_NOT_IN_RANGE",
            "message": f"--include-uncommitted was requested but {today} is not "
                       f"in the range, and uncommitted work can only be "
                       f"attributed to today. It has been left out.",
        })
    if warnings:
        payload["warnings"] = warnings
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
    tasks = am.load_tasks(run_dir)
    dates, results_dir = tasks["dates"], tasks["results_dir"]
    expected_language = tasks["language"]

    payload = ar.read_run(results_dir, dates, args.repo, expected_language,
                          tasks["required_by_date"], tasks["commits_by_date"])

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
    except (AnalysisError, gwdates.DateError, providers.ProviderError,
            language.LanguageError) as exc:
        # Four engines, one wire shape. They each carry their own code because
        # the caller acts on the code, not on which module raised it.
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
        lines.append(f"  {p['run_dir']}\n")
        rng, tz = p["range"], p["timezone"]
        # A shortcut is only as good as the range it turned into, so say which
        # days those were before listing them.
        lines.append(f"  {rng['from']} .. {rng['to']}  ({rng['days_count']} day(s), "
                     f"{tz['resolved']} via {tz['source']})\n")
        lines.append(f"  model: {p['model']['model_id']}  ({p['provider']})\n\n")
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
