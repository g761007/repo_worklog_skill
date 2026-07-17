"""The ``git-worklog`` command.

``version``, ``doctor`` and ``validate`` came first (roadmap §12.1): the things
you want *before* trusting anything else, none of which need the analysis
pipeline. ``analyze prepare``/``collect`` (§7) then bracket the pipeline — they
decide what must be analysed and check what came back, while the analysis itself
stays with the hosting agent's LLM. The CLI does not replace it and needs no
model API key (§6.1). ``preview`` and ``apply`` (§10) close the loop: the first
turns a collected run plus the agent's prose into a frozen payload, the second
writes that payload and takes no other input.

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
from git_worklog import markers as wm


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

    a = sub.add_parser("analyze", help="Prepare per-day analysis tasks, and collect them back.")
    asub = a.add_subparsers(dest="analyze_command", metavar="<prepare|collect>",
                            required=True)

    prep = asub.add_parser("prepare", help="Mint a run and write one manifest per day.")
    prep.add_argument("--from", required=True, metavar="DATE",
                      help="First day to analyse (YYYY-MM-DD, inclusive).")
    prep.add_argument("--to", required=True, metavar="DATE",
                      help="Last day to analyse (YYYY-MM-DD, inclusive).")
    prep.add_argument("--timezone", required=True, metavar="TZ",
                      help="IANA timezone deciding where each day starts.")
    prep.add_argument("--repo", default=".", help="Repository to read (default: cwd).")
    prep.add_argument("--dir", help="Worklog directory, read for its config.json "
                                    f"language setting (default: ./{wm.WORKLOG_DIRNAME}).")
    prep.add_argument("--run-dir", help="Override the run directory (default: "
                                        "~/.git-worklog/analysis/<run_id>).")
    prep.add_argument("--date-field", choices=["committer", "author"],
                      default="committer",
                      help="Which date decides day attribution (default: committer).")
    prep.add_argument("--worklog-dir", default=wm.WORKLOG_DIRNAME,
                      help="Worklog output directory; commits touching only this "
                           "directory are excluded as self-referential.")
    prep.add_argument("--include-uncommitted", action="store_true",
                      help="Also hand the subagent the working tree's uncommitted "
                           "changes. They are attributed to today and to no other "
                           "day; if today is outside --from/--to they are left out "
                           "and the run says so.")
    prep.add_argument("--provider", default="anthropic",
                      help="Subagent provider key (anthropic / openai / google).")
    prep.add_argument("--model-json", default="",
                      help="Structured model object (JSON: {display_name, "
                           "model_id[, reasoning_effort]}).")
    prep.add_argument("--language", default="auto",
                      help="Content language for the worklog as a BCP 47 tag "
                           "(zh-TW, en, ja), or 'auto' to fall through to project "
                           "config and GIT_WORKLOG_LANGUAGE. Resolved once and "
                           "stamped on every manifest in the run.")
    prep.add_argument("--language-source", default=None,
                      help="Where --language came from, so the manifest records "
                           "why and not just what: user-request, agent-host, "
                           "conversation, cli-argument, project-config, "
                           "environment, system-locale, fallback.")

    coll = asub.add_parser("collect", help="Read and validate a prepared run's results.")
    coll.add_argument("--run-id", help="The run to collect (from `analyze prepare`).")
    coll.add_argument("--run-dir", help="The run directory, if it is not "
                                        "~/.git-worklog/analysis/<run-id>.")
    coll.add_argument("--repo", default=".",
                      help="Repository the evidence cites. Every entry is checked "
                           "against the tree of the commit it names — not the "
                           "checkout, which holds everything changed since.")

    pv = sub.add_parser("preview", help="Freeze what an apply would write.")
    pv.add_argument("--run-id", help="The collected run to preview (from "
                                     "`analyze prepare`).")
    pv.add_argument("--run-dir", help="The run directory, if it is not "
                                      "~/.git-worklog/analysis/<run-id>.")
    pv.add_argument("--repo", default=".", help="Repository to read (default: cwd).")
    pv.add_argument("--dir", help=f"Worklog directory (default: <repo>/{wm.WORKLOG_DIRNAME}).")
    pv.add_argument("--input", help="Rendered day files as JSON, or '-' / omit "
                                    "for stdin: {\"entries\": {\"<date>\": "
                                    "{\"generated_markdown\": \"...\"}}}.")
    # Defaulted by the engine, not here: reading the real default would mean
    # importing git_worklog.preview (and the writer, and Git) on every run,
    # including `version`, which this parser is built for too.
    pv.add_argument("--ttl-seconds", type=int, default=None,
                    help="How long the preview stays applicable (default: 24h).")
    pv.add_argument("--show", metavar="PREVIEW_ID",
                    help="Print a stored preview instead of creating one.")
    pv.add_argument("--check", action="store_true",
                    help="With --show, also re-read the repository and worklog "
                         "to report whether the preview is still applicable.")
    pv.add_argument("--cancel", metavar="PREVIEW_ID",
                    help="Retire a preview instead of creating one.")
    pv.add_argument("--now", help="Override current time (ISO 8601) for "
                                  "deterministic runs.")

    ap = sub.add_parser("apply", help="Write a preview's stored payload.")
    ap.add_argument("--preview-id", required=True,
                    help="The preview to apply. This is the only input: the "
                         "content comes from the record, never from the caller.")
    ap.add_argument("--now", help="Override current time (ISO 8601) for "
                                  "deterministic runs.")
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
    elif args.command == "analyze":
        from git_worklog.cli import analyze as cmd
    elif args.command == "preview":
        from git_worklog.cli import preview as cmd
    elif args.command == "apply":
        from git_worklog.cli import apply as cmd
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
