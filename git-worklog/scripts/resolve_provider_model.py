#!/usr/bin/env python3
"""Resolve the subagent provider/model for the host the skill runs under.

The engine is :mod:`git_worklog.providers`; this is the command-line shell
around it. The logic moved into the package because only ``git_worklog*`` is
packaged -- an installed CLI has no ``scripts/`` directory to reach for -- and
the two front ends must not drift apart.

It reads ``config/provider_models.json`` (the only machine-readable model
source), selects the entry for the given host, applies the override precedence,
and emits the resolved model as JSON for ``build_analysis_manifest.py`` and the
dry-run summary.

Override precedence for the model id (highest first):

  1. ``--model`` (explicit runtime/CLI model id)
  2. environment variable ``GIT_WORKLOG_<HOST>_MODEL`` (or the
     deprecated ``REPO_WORKLOG_<HOST>_MODEL``)
  3. the provider default in ``config/provider_models.json``

The host is NEVER guessed: a missing or unknown host is a configuration error,
never a silent pick of the first provider. A model that cannot be resolved (empty
id) halts with an explicit error and the selectable candidate list — the skill
must never silently fall back to a pricier or arbitrary host default.

Escalation (``--escalate``) selects the provider's ``escalation_model_id``. It is
opt-in only: automatic escalation is disabled and this flag is used solely after
the user explicitly approves an escalation re-run. Output is a single JSON object
on stdout; configuration errors exit 2.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog import providers


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve the per-host subagent provider/model for Git Worklog.")
    p.add_argument("--host", help="Detected host / provider key: anthropic | openai | google.")
    p.add_argument("--model", default="",
                   help="Explicit runtime model id override (highest precedence).")
    p.add_argument("--reasoning-effort", default="",
                   help="Override reasoning effort (openai; usually left to config).")
    p.add_argument("--escalate", action="store_true",
                   help="Select the provider's escalation_model_id. Opt-in only, "
                        "used after the user approves an escalation re-run.")
    p.add_argument("--config", default=providers.default_config_path(),
                   help="Path to provider_models.json (default: ../config/).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _emit(providers.resolve(
            host=args.host, model=args.model,
            reasoning_effort=args.reasoning_effort, escalate=args.escalate,
            config_path=args.config,
        ))
        return 0
    except providers.ProviderError as exc:
        _emit({"ok": False, "errors": [exc.as_error()]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
