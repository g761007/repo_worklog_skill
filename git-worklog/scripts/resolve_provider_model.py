#!/usr/bin/env python3
"""Resolve the subagent provider/model for the host the skill runs under.

The Git Worklog orchestrator runs under exactly one host — Claude Code
(`anthropic`), Codex (`openai`), or Gemini (`google`) — and every Day Subagent
and Code Analysis Subagent for a run executes on that host's single model. This
script is the deterministic, single-source resolver: it reads
``config/provider_models.json`` (the only machine-readable model source), selects
the entry for the given host, applies the override precedence, and emits the
resolved model as JSON for ``build_analysis_manifest.py`` and the dry-run summary.

Override precedence for the model id (highest first):

  1. ``--model`` (explicit runtime/CLI model id)
  2. environment variable ``REPO_WORKLOG_<HOST>_MODEL``
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
import os
import sys

_VALID_HOSTS = ("anthropic", "openai", "google")

_DEFAULT_CONFIG = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "config", "provider_models.json"))


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> int:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    return 2


def _env_var(host: str) -> str:
    return f"REPO_WORKLOG_{host.upper()}_MODEL"


def _candidates(providers: dict) -> list:
    """Every configured provider's human label + id, for halt-and-ask lists."""
    out = []
    for key in _VALID_HOSTS:
        entry = providers.get(key)
        if not entry:
            continue
        out.append({
            "provider": key,
            "display_name": entry.get("display_name"),
            "model_id": entry.get("model_id"),
        })
    return out


def resolve(args: argparse.Namespace) -> tuple[dict, int]:
    # Load the single source of truth.
    try:
        with open(args.config, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError:
        return {}, _fail("CONFIG_NOT_FOUND",
                         f"Provider model config not found: {args.config}")
    except (json.JSONDecodeError, OSError) as exc:
        return {}, _fail("CONFIG_ERROR", f"Could not read provider model config: {exc}")

    providers = config.get("providers") or {}

    # The host is never guessed. Missing / unknown host is a config error.
    host = args.host
    if not host:
        return {}, _fail(
            "UNKNOWN_HOST",
            "No host was provided. The skill must detect its host (Claude Code -> "
            "anthropic, Codex -> openai, Gemini -> google) and pass --host; it must "
            "not guess or default to the first provider.",
            valid_hosts=list(_VALID_HOSTS))
    if host not in _VALID_HOSTS or host not in providers:
        return {}, _fail(
            "UNKNOWN_HOST",
            f"Unknown or unconfigured host '{host}'.",
            requested_host=host, valid_hosts=list(_VALID_HOSTS))

    entry = providers[host]
    escalating = bool(args.escalate)

    # Pick the base id for this mode, then apply the override precedence on top.
    if escalating:
        base_id = entry.get("escalation_model_id")
        if not base_id:
            return {}, _fail(
                "NO_ESCALATION_MODEL",
                f"Provider '{host}' has no escalation_model_id configured; "
                "escalation is unavailable for this host.",
                provider=host)
        base_effort = entry.get("escalation_reasoning_effort")
    else:
        base_id = entry.get("model_id")
        base_effort = entry.get("reasoning_effort")

    env_val = os.environ.get(_env_var(host)) or None
    cli_val = args.model or None
    if cli_val:
        model_id, source = cli_val, "cli"
    elif env_val:
        model_id, source = env_val, "env"
    else:
        model_id, source = base_id, "config"

    if not model_id or not str(model_id).strip():
        # Never silently fall back to a host default or a pricier model.
        return {}, _fail(
            "MODEL_UNAVAILABLE",
            f"No model id could be resolved for provider '{host}'. No fallback "
            "model was selected automatically. Configure an available model id "
            "(config default, {env}, or --model) and re-run the dry-run.".format(
                env=_env_var(host)),
            provider=host,
            requested_model_id=model_id or "",
            candidates=_candidates(providers))

    # reasoning_effort: CLI override wins; otherwise the mode's configured value.
    # Absent (e.g. anthropic / google) -> the key is omitted entirely, never "".
    effort = args.reasoning_effort or base_effort
    model = {
        "display_name": entry.get("display_name") if not escalating else str(model_id),
        "model_id": str(model_id),
    }
    if effort:
        model["reasoning_effort"] = effort

    escalation_id = entry.get("escalation_model_id")
    result = {
        "ok": True,
        "provider": host,
        "escalated": escalating,
        "model": model,
        "model_id_source": source,
        "escalation": {
            "available": bool(escalation_id),
            "model_id": escalation_id,
        },
        "escalation_policy": config.get("escalation_policy",
                                        {"automatic": False, "suggest_when": []}),
        "model_unavailable_policy": config.get("model_unavailable_policy",
                                               "halt-and-ask"),
    }
    esc_effort = entry.get("escalation_reasoning_effort")
    if esc_effort:
        result["escalation"]["reasoning_effort"] = esc_effort
    return result, 0


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
    p.add_argument("--config", default=_DEFAULT_CONFIG,
                   help="Path to provider_models.json (default: ../config/).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result, code = resolve(args)
    if code == 0:
        _emit(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
