#!/usr/bin/env python3
"""Exchange Day Subagent results through files, and validate them.

The engine is :mod:`git_worklog.analysis.results` -- including the rationale for
using files rather than reply text, and what ``read`` guarantees. This is the
command-line shell around it. The logic moved into the package because only
``git_worklog*`` is packaged -- an installed ``git-worklog analyze collect`` has
no ``scripts/`` directory to reach for -- and the two front ends must not drift
apart.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog import language
from git_worklog.analysis import AnalysisError
from git_worklog.analysis import results as ar


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def cmd_init(args: argparse.Namespace) -> int:
    _emit(ar.mint_run(ar.parse_dates(args.dates), args.run_dir))
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    dates = ar.parse_dates(args.dates)
    expected_language = (language.normalize(args.language)
                         if args.language else None)
    _emit(ar.read_run(args.run_dir, dates, args.repo, expected_language))
    return 0


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


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return cmd_init(args)
        return cmd_read(args)
    except language.LanguageError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except AnalysisError as exc:
        _fail(exc.code, exc.message, **exc.extra)
    except OSError as exc:
        _fail("IO_ERROR", f"Filesystem error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
