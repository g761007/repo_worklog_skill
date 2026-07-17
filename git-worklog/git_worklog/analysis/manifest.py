"""Build a per-day analysis manifest for a Day Subagent.

A manifest is a *planning aid*: it groups the day's changed files by real work
area, proposes the context a subagent should read beyond the diff itself, flags
days large enough to warrant fanning out, and carries the day's authorship as a
deterministic fact rather than something a model infers from a commit list.

It never summarises code and never decides the final worklog wording. Those stay
with the hosting agent's LLM (roadmap §6.1).
"""

from __future__ import annotations

import json
import os

from git_worklog import config, language
from git_worklog import markers as wm
from git_worklog.analysis import (  # noqa: F401
    RESULTS_SUBDIR, SCHEMA_VERSION, TASKS_SUBDIR, AnalysisError,
)

# Number of changed files above which a day is flagged as "large" and the Day
# Subagent is advised to fan out into Code Analysis Subagents.
LARGE_DAY_FILE_THRESHOLD = 25

# Carried on every manifest (roadmap §8) so the contract travels with the task
# rather than living only in prose the subagent may never have been shown. The
# skill states all of this at length; a manifest is what actually reaches the
# model, and a rule that is not in the manifest is a rule that is not enforced.
ANALYSIS_RULES = [
    "Read the actual patch.",
    "Read historical source code from the target commit.",
    "Do not rely only on commit messages.",
    "Describe the final state after all commits on the same day.",
    "Write all user-facing analysis using the resolved language.",
    "Preserve code symbols, file paths, commit hashes, and identifiers.",
]

# The categories whose files carry the behaviour a worklog is *about*. A day's
# analysis may reasonably summarise its docs and config in one line, but source
# it never mentioned is source it may never have read -- so only these are
# required to be accounted for (see required_commit_file_pairs below).
SOURCE_CATEGORIES = ("backend", "frontend", "mobile", "database")

# Category priority mirrors references/code-analysis-rules.md (plan section 9.4).
# Earlier entries win when a path matches more than one rule.
_CATEGORY_ORDER = [
    "tests",
    "database",
    "configuration",
    "deployment",
    "documentation",
    "frontend",
    "mobile",
    "backend",
    "other",
]


def classify(path: str) -> str:
    """Assign a single work category to a file path (priority-ordered)."""
    p = path.lower()
    name = os.path.basename(p)

    def has(*needles: str) -> bool:
        return any(n in p for n in needles)

    if has("/test", "test_", "_test.", "/tests/", "__tests__", ".test.",
           ".spec.", "/spec/", "spec_"):
        return "tests"
    if has("migration", "migrations", "/schema", "alembic", "/db/") or p.endswith(".sql"):
        return "database"
    if has(".github/", ".gitlab-ci", "/ci/", "dockerfile", "docker-compose",
           "/config", "makefile", "justfile") or name in (
            "dockerfile", "makefile", "justfile") or p.endswith(
            (".yml", ".yaml", ".toml", ".ini", ".cfg", ".env")):
        return "configuration"
    if has("deploy", "/k8s/", "kubernetes", "helm", "terraform", "/infra",
           "ansible") or p.endswith((".tf", ".tfvars")):
        return "deployment"
    if has("/docs/", "readme", "changelog", "license") or p.endswith((".md", ".rst", ".adoc")):
        return "documentation"
    if has("android/", "ios/", "/mobile/") or p.endswith((".swift", ".kt", ".m", ".mm")):
        return "mobile"
    if has("frontend/", "/web/", "/ui/", "/components/", "/pages/") or p.endswith(
            (".tsx", ".jsx", ".vue", ".css", ".scss", ".sass", ".less", ".html", ".svelte")):
        return "frontend"
    if has("/api/", "controller", "/service", "/routes", "/handlers", "/models",
           "repository") or p.endswith(
            (".py", ".go", ".rb", ".java", ".rs", ".php", ".cs", ".ts", ".js", ".ex", ".exs")):
        return "backend"
    return "other"


def _collect_authors(commits: "list[dict]") -> "list[str]":
    """Distinct author names for the day, ordered by first appearance.

    Deterministic fact, so it is computed here rather than inferred by a
    subagent from the commit list -- a day with many commits is exactly where a
    model would drop a contributor.
    """
    authors: "list[str]" = []
    for commit in commits:
        name = commit.get("author_name")
        if name and name not in authors:
            authors.append(name)
    return authors


def _top_module(path: str) -> str:
    parts = path.split("/")
    return parts[0] if len(parts) == 1 else "/".join(parts[:2])


def _collect_changed_files(commits: "list[dict]") -> "list[dict]":
    seen: "dict[str, dict]" = {}
    for commit in commits:
        for f in commit.get("files", []):
            path = f.get("path")
            if not path:
                continue
            rec = seen.setdefault(path, {
                "path": path,
                "statuses": set(),
                "is_binary": f.get("is_binary", False),
                "is_submodule": f.get("is_submodule", False),
                "old_path": f.get("old_path"),
                "commits": [],
            })
            rec["statuses"].add(f.get("status"))
            rec["is_binary"] = rec["is_binary"] or f.get("is_binary", False)
            rec["is_submodule"] = rec["is_submodule"] or f.get("is_submodule", False)
            if f.get("old_path"):
                rec["old_path"] = f["old_path"]
            rec["commits"].append(commit.get("short_hash"))
    files = []
    for rec in seen.values():
        rec["statuses"] = sorted(x for x in rec["statuses"] if x)
        rec["category"] = classify(rec["path"])
        files.append(rec)
    files.sort(key=lambda r: r["path"])
    return files


def _build_groups(files: "list[dict]") -> "list[dict]":
    buckets: "dict[tuple[str, str], list[dict]]" = {}
    for f in files:
        key = (f["category"], _top_module(f["path"]))
        buckets.setdefault(key, []).append(f)
    groups = []
    for (category, module), members in buckets.items():
        commits = sorted({c for m in members for c in m["commits"] if c})
        groups.append({
            "group": f"{category}:{module}",
            "category": category,
            "module": module,
            "files": [m["path"] for m in members],
            "commits": commits,
            "has_binary": any(m["is_binary"] for m in members),
            "has_submodule": any(m["is_submodule"] for m in members),
        })
    # Order groups by the documented category priority, then by name.
    order = {c: i for i, c in enumerate(_CATEGORY_ORDER)}
    groups.sort(key=lambda g: (order.get(g["category"], len(order)), g["group"]))
    return groups


def _required_context(groups: "list[dict]") -> "list[dict]":
    """Suggest the reading a subagent must do beyond the diff itself."""
    context = []
    for g in groups:
        if g["category"] in ("documentation", "configuration", "deployment"):
            depth = "surface"
        elif g["category"] in ("backend", "frontend", "mobile", "database"):
            depth = "deep"
        else:
            depth = "standard"
        context.append({
            "group": g["group"],
            "category": g["category"],
            "read": [
                "full enclosing function / class / component for each changed hunk",
                "one layer of direct callers",
                "one layer of direct dependencies",
                "the corresponding tests",
            ],
            "expand_second_layer_if": "public API, schema, or shared core is touched",
            "depth": depth,
        })
    return context


def parse_model(model_json: str) -> "dict | None":
    """Parse the structured model object threaded in from resolve_provider_model.

    The object is emitted verbatim onto the manifest so every subagent runs on
    the same provider/model. ``reasoning_effort`` is present only when it applies
    (e.g. openai) — never an empty string. Absent input -> ``None``.
    """
    if not model_json:
        return None
    try:
        model = json.loads(model_json)
    except json.JSONDecodeError as exc:
        raise AnalysisError("BAD_MODEL_JSON", f"--model-json is not valid JSON: {exc}")
    if not isinstance(model, dict):
        raise AnalysisError("BAD_MODEL_JSON", "--model-json must be a JSON object.")
    return model


def resolve_language(explicit: "str | None", source: "str | None",
                     worklog_dir: "str | None" = None) -> language.Resolution:
    """Decide this run's output language (§6.2.1).

    ``allow_locale`` is off: a manifest exists to be handed to an agent's LLM,
    which makes this an agent-hosted run by definition, and §6.2.5 is explicit
    that the host OS locale must not decide there — CI and dev containers are
    pinned to C or en_US and say nothing about what the user wants. The tiers
    above config live in the agent, which passes them down explicitly.
    """
    worklog_dir = worklog_dir or os.path.join(".", wm.WORKLOG_DIRNAME)
    return language.resolve(
        explicit=explicit,
        source=source,
        config_value=config.language(config.load(worklog_dir)),
        allow_locale=False,
    )


def _pair_required(f: dict, path: str) -> bool:
    """Must this (commit, file) pair be accounted for by the day's analysis?

    Deliberately narrower than "everything that changed". Three exclusions, each
    for a different reason:

    * non-source categories -- docs, config, CI and tests are real work, but a
      day may fairly cover them in a sentence, and demanding a citation for each
      would fail honest days rather than catch dishonest ones.
    * binary and submodule files -- there is no source to read or symbol to cite.
    * deletions -- the file is *gone* from that commit's tree, so any citation of
      it at that commit would be rejected by the evidence check. Requiring one
      would be requiring the impossible.
    """
    if classify(path) not in SOURCE_CATEGORIES:
        return False
    if f.get("is_binary") or f.get("is_submodule"):
        return False
    if f.get("status") == "D":
        return False
    return True


def _required_commit_file_pairs(commits: "list[dict]") -> "list[dict]":
    """Every (commit, file) pair of the day, flagged for whether it is required.

    All pairs are listed, not only the required ones: the subagent should see
    what it is *not* being held to as well, and a reader of the manifest can
    tell "excluded" from "overlooked".
    """
    pairs = []
    for commit in commits:
        sha = commit.get("short_hash")
        for f in commit.get("files", []):
            path = f.get("path")
            if not path:
                continue
            pairs.append({
                "commit": sha,
                "file": path,
                "category": classify(path),
                "required": _pair_required(f, path),
            })
    pairs.sort(key=lambda p: (p["file"], p["commit"] or ""))
    return pairs


def required_files(manifest: dict) -> "set[str]":
    """The distinct files a manifest requires the day's analysis to account for.

    File-level, not pair-level, and that is forced rather than chosen: coverage
    is satisfied by `work_items[].files[]` as well as by `evidence[]`, and a
    `files[]` entry names a path without saying which commit it belongs to. Only
    `evidence[]` carries both, so a pair-level rule could be met by evidence
    alone -- which measurement showed rejects days that are actually fine.
    """
    return {p["file"] for p in manifest.get("required_commit_file_pairs") or []
            if p.get("required")}


def load_tasks(run_dir: str) -> dict:
    """Read back what a run asked for, from the manifests it dispatched.

    The tasks are the run's own record of its scope, which is why both `collect`
    and `preview` read them rather than taking a caller-supplied date list: a day
    could otherwise be dropped from a run simply by leaving it out of the second
    command -- the exact failure `missing` exists to catch.
    """
    tasks_dir = os.path.join(run_dir, TASKS_SUBDIR)
    if not os.path.isdir(tasks_dir):
        raise AnalysisError(
            "RUN_NOT_PREPARED",
            f"{run_dir} has no {TASKS_SUBDIR}/ directory, so there is nothing to "
            f"collect against. Was this run created by `analyze prepare`?",
            run_dir=run_dir)

    dates, resolved_language = [], None
    required_by_date, manifests = {}, {}
    for name in sorted(os.listdir(tasks_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(tasks_dir, name), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        date = manifest["date"]
        dates.append(date)
        manifests[date] = manifest
        required_by_date[date] = required_files(manifest)
        block = manifest.get("language") or {}
        if block.get("resolved"):
            resolved_language = block["resolved"]
    if not dates:
        raise AnalysisError("RUN_HAS_NO_TASKS",
                            f"{tasks_dir} contains no manifests.", run_dir=run_dir)

    return {
        "run_dir": run_dir,
        "tasks_dir": tasks_dir,
        "results_dir": os.path.join(run_dir, RESULTS_SUBDIR),
        "dates": dates,
        "language": resolved_language,
        "required_by_date": required_by_date,
        "manifests": manifests,
    }


def _repository_block(history: "dict | None") -> "dict | None":
    """The §8 repository block, taken from the history payload's own metadata."""
    info = (history or {}).get("repository")
    if not isinstance(info, dict):
        return None
    return {
        "root": info.get("root"),
        "git_dir": info.get("git_dir"),
        "head": info.get("head"),
        "branch": info.get("branch"),
    }


def build(date: str, timezone: str, history: "dict | None" = None,
          worktree: "dict | None" = None, include_uncommitted: bool = False,
          provider: str = "anthropic", model: "dict | None" = None,
          lang: "language.Resolution | None" = None,
          run_id: "str | None" = None,
          result_path: "str | None" = None,
          parts_dir: "str | None" = None) -> dict:
    """Assemble one day's manifest from that day's collected Git facts.

    ``run_id``, ``result_path`` and ``parts_dir`` are known only to a caller
    that minted a run (``analyze prepare``), so they are optional: a manifest
    built ad hoc for one day is still a valid manifest, it just does not belong
    to a run yet.
    """
    if history and not history.get("ok", True):
        raise AnalysisError("BAD_HISTORY_INPUT",
                            "collect_git_history reported an error.",
                            upstream=history.get("errors"))
    commits = history.get("commits", []) if history else []

    uncommitted = []
    if include_uncommitted and worktree:
        for bucket in ("staged", "unstaged", "untracked"):
            for f in worktree.get(bucket, []):
                uncommitted.append({
                    "path": f.get("path"),
                    "state": bucket,
                    "status": f.get("status"),
                    "is_binary": f.get("is_binary", False),
                    "category": classify(f.get("path", "")),
                })

    files = _collect_changed_files(commits)
    groups = _build_groups(files)
    is_large = len(files) > LARGE_DAY_FILE_THRESHOLD or len(groups) > 8

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "date": date,
        "timezone": timezone,
        "repository": _repository_block(history),
        "language": lang.as_manifest() if lang else None,
        "warnings": lang.warnings if lang else [],
        "include_uncommitted": bool(include_uncommitted),
        "provider": provider,
        "model": model,
        "has_changes": bool(commits) or bool(uncommitted),
        "commit_count": len(commits),
        "authors": _collect_authors(commits),
        "commits": [{
            "short_hash": c.get("short_hash"),
            "full_hash": c.get("full_hash"),
            "author_name": c.get("author_name"),
            "subject": c.get("subject"),
            "is_merge": c.get("is_merge"),
            "is_revert_candidate": c.get("is_revert_candidate"),
        } for c in commits],
        "changed_files": [{
            "path": f["path"], "statuses": f["statuses"], "category": f["category"],
            "is_binary": f["is_binary"], "is_submodule": f["is_submodule"],
            "old_path": f["old_path"], "commits": f["commits"],
        } for f in files],
        "file_groups": groups,
        "required_context": _required_context(groups),
        "required_commit_file_pairs": _required_commit_file_pairs(commits),
        "analysis_rules": list(ANALYSIS_RULES),
        "uncommitted_changes": uncommitted,
        "large_day": is_large,
        "recommended_code_analysis_subagents": len(groups) if is_large else 0,
        "result_path": result_path,
        # Where a fan-out's per-group parts go. It is on the manifest because a
        # Day Subagent is handed a manifest and a result_path and nothing else:
        # left to derive a sibling path from result_path, it lands in results/,
        # where every extra file is an `unknown` that fails the whole run. The
        # day the contract most wants split up is exactly the day that then
        # cannot be written -- observed on the first real large-day run.
        "parts_dir": parts_dir,
    }
