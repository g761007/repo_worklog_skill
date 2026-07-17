#!/usr/bin/env python3
"""Inspect the Git working tree for uncommitted changes.

Only invoked when ``include_uncommitted=true``.

The engine is :mod:`git_worklog.analysis.worktree`; this is the command-line
shell around it. The logic moved into the package because only ``git_worklog*``
is packaged -- an installed ``git-worklog analyze prepare --include-uncommitted``
has no ``scripts/`` directory to reach for -- and the two front ends must not
drift apart.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog.analysis import AnalysisError
from git_worklog.analysis import worktree


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Inspect the Git working tree for Git Worklog.")
    p.add_argument("--repo", default=".", help="Repository path (default: current directory).")
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _emit(worktree.inspect(args.repo))
        return 0
    except AnalysisError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except worktree.GitError as exc:
        _fail("GIT_ERROR", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
