#!/usr/bin/env python3
"""Build a per-day analysis manifest for a Git Worklog Day Subagent.

Consumes the JSON produced by ``collect_git_history.py`` for a single day (and,
optionally, ``inspect_worktree.py`` output for today).

The engine is :mod:`git_worklog.analysis.manifest`; this is the command-line
shell around it. The logic moved into the package because only ``git_worklog*``
is packaged -- an installed ``git-worklog analyze prepare`` has no ``scripts/``
directory to reach for -- and the two front ends must not drift apart.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog import language
from git_worklog import markers as wm
from git_worklog.analysis import AnalysisError
from git_worklog.analysis import manifest as am

# Re-exported for anything importing this script as a module.
classify = am.classify
LARGE_DAY_FILE_THRESHOLD = am.LARGE_DAY_FILE_THRESHOLD


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _load_json(path: "str | None") -> dict:
    if path and path != "-":
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    data = sys.stdin.read()
    if not data.strip():
        return {}
    return json.loads(data)


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


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        model = am.parse_model(args.model_json)
        lang = am.resolve_language(args.language, args.language_source, args.dir)
        history = _load_json(args.history)
        worktree = _load_json(args.worktree) if args.worktree else {}
        _emit(am.build(
            date=args.date, timezone=args.timezone, history=history,
            worktree=worktree, include_uncommitted=args.include_uncommitted,
            provider=args.provider, model=model, lang=lang,
        ))
        return 0
    except language.LanguageError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except AnalysisError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except (json.JSONDecodeError, OSError) as exc:
        _fail("INPUT_ERROR", f"Could not read history/worktree input: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
