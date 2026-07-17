#!/usr/bin/env python3
"""Build a per-day analysis manifest for a Git Worklog Day Subagent.

Consumes the JSON produced by ``collect_git_history.py`` for a single day (and,
optionally, ``inspect_worktree.py`` output for today) and groups the changed
files by real work area using the documented priority order. It also proposes
the context a subagent should read (full symbols, direct callers/deps, tests),
flags days large enough to warrant splitting into Code Analysis Subagents, and
carries each commit's author plus the day's distinct author list.

The manifest is a planning aid: it never summarises code and never decides the
final worklog wording. Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog import config, language
from git_worklog import markers as wm

# Number of changed files above which a day is flagged as "large" and the Day
# Subagent is advised to fan out into Code Analysis Subagents.
LARGE_DAY_FILE_THRESHOLD = 25

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


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _load_json(path: str | None) -> dict:
    if path and path != "-":
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    data = sys.stdin.read()
    if not data.strip():
        return {}
    return json.loads(data)


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


def _collect_authors(commits: list[dict]) -> list[str]:
    """Distinct author names for the day, ordered by first appearance.

    Deterministic fact, so it is computed here rather than inferred by a
    subagent from the commit list -- a day with many commits is exactly where a
    model would drop a contributor.
    """
    authors: list[str] = []
    for commit in commits:
        name = commit.get("author_name")
        if name and name not in authors:
            authors.append(name)
    return authors


def _top_module(path: str) -> str:
    parts = path.split("/")
    return parts[0] if len(parts) == 1 else "/".join(parts[:2])


def _collect_changed_files(commits: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
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


def _build_groups(files: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str], list[dict]] = {}
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


def _required_context(groups: list[dict]) -> list[dict]:
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


def _parse_model(model_json: str) -> dict | None:
    """Parse the structured model object threaded in from resolve_provider_model.

    The object is emitted verbatim onto the manifest so every subagent runs on
    the same provider/model. ``reasoning_effort`` is present only when it applies
    (e.g. openai) — never an empty string. Absent input -> ``model: null``.
    """
    if not model_json:
        return None
    try:
        model = json.loads(model_json)
    except json.JSONDecodeError as exc:
        _fail("BAD_MODEL_JSON", f"--model-json is not valid JSON: {exc}")
    if not isinstance(model, dict):
        _fail("BAD_MODEL_JSON", "--model-json must be a JSON object.")
    return model


def _resolve_language(args: argparse.Namespace) -> language.Resolution:
    """Decide this run's output language (§6.2.1).

    ``allow_locale`` is off: a manifest exists to be handed to an agent's LLM,
    which makes this an agent-hosted run by definition, and §6.2.5 is explicit
    that the host OS locale must not decide there — CI and dev containers are
    pinned to C or en_US and say nothing about what the user wants. The tiers
    above config live in the agent, which passes them down with --language.
    """
    worklog_dir = args.dir or os.path.join(".", wm.WORKLOG_DIRNAME)
    return language.resolve(
        explicit=args.language,
        source=args.language_source,
        config_value=config.language(config.load(worklog_dir)),
        allow_locale=False,
    )


def build_manifest(args: argparse.Namespace) -> dict:
    model = _parse_model(args.model_json)
    try:
        lang = _resolve_language(args)
    except language.LanguageError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    history = _load_json(args.history)
    if history and not history.get("ok", True):
        _fail("BAD_HISTORY_INPUT", "collect_git_history reported an error.",
              upstream=history.get("errors"))
    commits = history.get("commits", []) if history else []

    worktree = _load_json(args.worktree) if args.worktree else {}
    uncommitted = []
    if args.include_uncommitted and worktree:
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
        "date": args.date,
        "timezone": args.timezone,
        "language": lang.as_manifest(),
        "warnings": lang.warnings,
        "include_uncommitted": bool(args.include_uncommitted),
        "provider": args.provider,
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
        "uncommitted_changes": uncommitted,
        "large_day": is_large,
        "recommended_code_analysis_subagents": len(groups) if is_large else 0,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build a per-day analysis manifest for Git Worklog.")
    p.add_argument("--date", required=True, help="The day this manifest describes (YYYY-MM-DD).")
    p.add_argument("--timezone", required=True, help="Resolved IANA timezone.")
    p.add_argument("--history", help="Path to collect_git_history JSON, or '-' / omit for stdin.")
    p.add_argument("--worktree", help="Path to inspect_worktree JSON (today only).")
    p.add_argument("--include-uncommitted", action="store_true")
    p.add_argument("--provider", default="anthropic",
                   help="Subagent provider key (anthropic / openai / google).")
    p.add_argument("--model-json", default="",
                   help="Structured model object from resolve_provider_model.py "
                        "(JSON: {display_name, model_id[, reasoning_effort]}).")
    p.add_argument("--language", default="auto",
                   help="Output language as a BCP 47 tag (zh-TW, en, ja), or "
                        "'auto' to fall through to project config and "
                        "GIT_WORKLOG_LANGUAGE.")
    p.add_argument("--language-source", default=None,
                   help="Where --language came from, so the manifest records "
                        "why and not just what: user-request, agent-host, "
                        "conversation, cli-argument, project-config, "
                        "environment, system-locale, fallback.")
    p.add_argument("--dir", help="Worklog directory, read for its config.json "
                                 f"language setting (default: ./{wm.WORKLOG_DIRNAME}).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _emit(build_manifest(args))
        return 0
    except (json.JSONDecodeError, OSError) as exc:
        _fail("INPUT_ERROR", f"Could not read history/worktree input: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
