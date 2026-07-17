"""The ``git-worklog`` command.

Phase one (roadmap §12.1) is deliberately small: ``version``, ``doctor`` and
``validate`` — the three things you want *before* trusting anything else, and
none of which need the analysis pipeline. Generation and reporting stay with the
skill for now; the CLI does not replace the agent's LLM (roadmap §6.1).

Every subcommand prints one JSON object to stdout, matching the scripts'
contract, so the same parsing works everywhere. ``--text`` switches to a
human-readable rendering for terminal use.

Exit codes:
    0  ok
    1  ran fine, but the answer is "no" (doctor found a problem, validate failed)
    2  the command itself could not run
"""

from __future__ import annotations

import argparse
import json
import sys

from git_worklog import __version__, language


def _emit(payload: dict, as_text: bool, render) -> None:
    if as_text:
        sys.stdout.write(render(payload))
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="git-worklog",
        description="Engineering worklogs from real Git history and code.",
    )
    p.add_argument("--version", action="version", version=f"git-worklog {__version__}")
    p.add_argument("--text", action="store_true",
                   help="Human-readable output instead of JSON.")
    p.add_argument("--interface-language", default=None, metavar="TAG",
                   help="Language for this command's own messages (BCP 47). "
                        "English only for now; anything else is reported and "
                        "falls back. Separate from the worklog's content "
                        "language, which these commands do not set.")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("version", help="Report the CLI, layout and schema versions.")

    d = sub.add_parser("doctor", help="Check that this environment can run the tool.")
    d.add_argument("--repo", default=".", help="Repository to check (default: cwd).")
    d.add_argument("--dir", help="Worklog directory (default: <repo>/.git-worklog).")

    v = sub.add_parser("validate", help="Validate a worklog directory.")
    v.add_argument("--repo", default=".", help="Repository to check (default: cwd).")
    v.add_argument("--dir", help="Worklog directory (default: <repo>/.git-worklog).")
    return p


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    # Imported lazily so `git-worklog version` stays fast and cannot be broken
    # by an unrelated subcommand's import.
    if args.command == "version":
        from git_worklog.cli import version as cmd
    elif args.command == "doctor":
        from git_worklog.cli import doctor as cmd
    else:
        from git_worklog.cli import validate as cmd

    # §6.2.13 keeps interface language and content language apart. Phase one
    # ships English messages only -- which the roadmap allows -- but an
    # unsupported request is answered rather than ignored: silence would look
    # like it worked.
    try:
        interface = language.resolve_interface(args.interface_language)
    except language.LanguageError as exc:
        _emit({"ok": False, "errors": [{"code": exc.code, "message": exc.message}]},
              args.text, lambda p: f"error: {exc.message}\n")
        return 2

    try:
        payload, code = cmd.run(args)
    except Exception as exc:  # never let a traceback replace the JSON contract
        _emit({"ok": False, "errors": [{
            "code": "UNEXPECTED_ERROR",
            "message": f"{type(exc).__name__}: {exc}",
        }]}, args.text, lambda p: f"error: {exc}\n")
        return 2

    if interface.warnings:
        payload.setdefault("warnings", []).extend(interface.warnings)
    payload.setdefault("interface_language", interface.resolved)

    _emit(payload, args.text, cmd.render_text)
    return code
