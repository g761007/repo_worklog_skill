"""Resolve the subagent provider/model for the host the skill runs under.

The Git Worklog orchestrator runs under exactly one host — Claude Code
(``anthropic``), Codex (``openai``), or Gemini (``google``) — and every Day
Subagent and Code Analysis Subagent for a run executes on that host's single
model. This is the deterministic, single-source resolver: it reads
``config/provider_models.json`` (the only machine-readable model source), selects
the entry for the given host, and applies the override precedence.

Override precedence for the model id (highest first):

  1. ``model`` (explicit runtime/CLI model id)
  2. environment variable ``GIT_WORKLOG_<HOST>_MODEL`` (or the deprecated
     ``REPO_WORKLOG_<HOST>_MODEL``)
  3. the provider default in ``config/provider_models.json``

The host is NEVER guessed: a missing or unknown host is a configuration error,
never a silent pick of the first provider. A model that cannot be resolved (empty
id) raises with the selectable candidate list — the skill must never silently
fall back to a pricier or arbitrary host default.

Escalation is opt-in only: automatic escalation is disabled and the flag is used
solely after the user explicitly approves an escalation re-run.
"""

from __future__ import annotations

import json
import os

VALID_HOSTS = ("anthropic", "openai", "google")


class ProviderError(ValueError):
    """A provider/model that cannot be resolved, carrying the wire code.

    Mirrors :class:`git_worklog.dates.DateError` and
    :class:`git_worklog.analysis.AnalysisError`: the callers are thin shells that
    owe the user one JSON object with a stable ``code``.
    """

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)

    def as_error(self) -> dict:
        """The error dict as it goes on the wire."""
        return {"code": self.code, "message": self.message, **self.extra}


def default_config_path() -> str:
    """``config/provider_models.json`` as shipped beside the package.

    The package sits inside the skill directory, so this resolves to the same
    file whether it is reached from ``scripts/`` or from the package -- there is
    one config, not one per front end.

    Note: ``config/`` is not part of the wheel (only ``git_worklog*`` is), so an
    installed CLI will not find it here and gets a loud CONFIG_NOT_FOUND rather
    than a wrong model. Relocating the file into the package is PR 7b's call, on
    the PR that actually gives the CLI a provider subcommand.
    """
    return os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "config", "provider_models.json"))


def env_var(host: str) -> str:
    return f"GIT_WORKLOG_{host.upper()}_MODEL"


def _legacy_env_var(host: str) -> str:
    """The pre-v0.7 name. Shipped in v0.3.0-v0.4.0, so it is still honoured."""
    return f"REPO_WORKLOG_{host.upper()}_MODEL"


def _resolve_env(host: str) -> "tuple[str | None, str | None]":
    """Read the model override from the environment.

    Returns ``(value, deprecated_var_used)``. The current name wins; the legacy
    one is honoured because it was publicly released and someone's shell profile
    still exports it. Silently ignoring it would swap their model without a word
    -- the one thing this module exists to never do.
    """
    val = os.environ.get(env_var(host)) or None
    if val:
        return val, None
    legacy = os.environ.get(_legacy_env_var(host)) or None
    if legacy:
        return legacy, _legacy_env_var(host)
    return None, None


def _candidates(providers: dict) -> list:
    """Every configured provider's human label + id, for halt-and-ask lists."""
    out = []
    for key in VALID_HOSTS:
        entry = providers.get(key)
        if not entry:
            continue
        out.append({
            "provider": key,
            "display_name": entry.get("display_name"),
            "model_id": entry.get("model_id"),
        })
    return out


def load_config(path: "str | None" = None) -> dict:
    """Read the single source of truth. Raises ProviderError if unusable."""
    path = path or default_config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ProviderError("CONFIG_NOT_FOUND",
                            f"Provider model config not found: {path}")
    except (json.JSONDecodeError, OSError) as exc:
        raise ProviderError("CONFIG_ERROR",
                            f"Could not read provider model config: {exc}")


def resolve(host: "str | None", model: "str | None" = None,
            reasoning_effort: "str | None" = None, escalate: bool = False,
            config_path: "str | None" = None) -> dict:
    """Resolve ``host`` to its model. Raises ProviderError on any config fault."""
    config = load_config(config_path)
    providers = config.get("providers") or {}

    # The host is never guessed. Missing / unknown host is a config error.
    if not host:
        raise ProviderError(
            "UNKNOWN_HOST",
            "No host was provided. The skill must detect its host (Claude Code -> "
            "anthropic, Codex -> openai, Gemini -> google) and pass --host; it must "
            "not guess or default to the first provider.",
            valid_hosts=list(VALID_HOSTS))
    if host not in VALID_HOSTS or host not in providers:
        raise ProviderError(
            "UNKNOWN_HOST",
            f"Unknown or unconfigured host '{host}'.",
            requested_host=host, valid_hosts=list(VALID_HOSTS))

    entry = providers[host]
    escalating = bool(escalate)

    # Pick the base id for this mode, then apply the override precedence on top.
    if escalating:
        base_id = entry.get("escalation_model_id")
        if not base_id:
            raise ProviderError(
                "NO_ESCALATION_MODEL",
                f"Provider '{host}' has no escalation_model_id configured; "
                "escalation is unavailable for this host.",
                provider=host)
        base_effort = entry.get("escalation_reasoning_effort")
    else:
        base_id = entry.get("model_id")
        base_effort = entry.get("reasoning_effort")

    env_val, deprecated_var = _resolve_env(host)
    cli_val = model or None
    if cli_val:
        model_id, source = cli_val, "cli"
    elif env_val:
        model_id, source = env_val, "env"
    else:
        model_id, source = base_id, "config"

    if not model_id or not str(model_id).strip():
        # Never silently fall back to a host default or a pricier model.
        raise ProviderError(
            "MODEL_UNAVAILABLE",
            f"No model id could be resolved for provider '{host}'. No fallback "
            "model was selected automatically. Configure an available model id "
            f"(config default, {env_var(host)}, or --model) and re-run the dry-run.",
            provider=host,
            requested_model_id=model_id or "",
            candidates=_candidates(providers))

    # reasoning_effort: CLI override wins; otherwise the mode's configured value.
    # Absent (e.g. anthropic / google) -> the key is omitted entirely, never "".
    effort = reasoning_effort or base_effort
    resolved_model = {
        "display_name": entry.get("display_name") if not escalating else str(model_id),
        "model_id": str(model_id),
    }
    if effort:
        resolved_model["reasoning_effort"] = effort

    escalation_id = entry.get("escalation_model_id")
    result = {
        "ok": True,
        "provider": host,
        "escalated": escalating,
        "model": resolved_model,
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

    # An honoured-but-renamed variable is reported, never silently obeyed: the
    # user is picking a model, and they should know which knob actually did it.
    if deprecated_var and source == "env":
        result["warnings"] = [{
            "code": "DEPRECATED_ENV_VAR",
            "message": f"{deprecated_var} is deprecated and will be removed in "
                       f"v2.0; rename it to {env_var(host)}. It was honoured "
                       "for this run.",
            "deprecated": deprecated_var,
            "replacement": env_var(host),
        }]
    esc_effort = entry.get("escalation_reasoning_effort")
    if esc_effort:
        result["escalation"]["reasoning_effort"] = esc_effort
    return result
