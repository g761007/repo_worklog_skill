# Provider Models

Per-host subagent model selection for the `repo_worklog` skill. The **single
source of truth** is `config/provider_models.json`; this document is its
human-facing mirror. Never add a second place that stores model ids — update the
JSON and this file only.

The skill runs under one of three hosts. Each host has one provider key and one
model. Every Day Subagent and Code Analysis Subagent for a run is spawned on the
model belonging to the host the skill is currently running under. Defaults are
chosen for **cost efficiency** on bounded, per-day analysis — do not silently
replace them with pricier models.

## Per-host models (defaults)

| Host        | provider key | display_name       | default model_id       | reasoning_effort |
| ----------- | ------------ | ------------------ | ---------------------- | ---------------- |
| Claude Code | `anthropic`  | Claude Haiku 4.5   | `claude-haiku-4-5`     | —                |
| Codex       | `openai`     | GPT-5.6 Luna       | `gpt-5.6-luna`         | `low`            |
| Gemini      | `google`     | Gemini 3.5 Flash   | `gemini-3.5-flash`     | —                |

`reasoning_effort` applies to `openai` only. For `anthropic` and `google` the
field is **omitted entirely** (never an empty string) from the resolver output
and the manifest.

## display_name vs model_id

- `display_name` is the human-facing label shown in menus, dry-run summaries, and
  candidate lists.
- `model_id` is the runtime dispatch identifier the host actually resolves when
  spawning a subagent. If the host's exact id differs from the default, override
  it (see below) rather than editing multiple files — a wrong `model_id` must
  surface as an unavailable model, never as a silent fallback.

## Resolving the model (single command)

The orchestrator resolves the model with one deterministic script and threads its
`model` object through the pipeline:

```text
scripts/resolve_provider_model.py --host <anthropic|openai|google>
  -> { provider, model:{display_name, model_id[, reasoning_effort]}, ... }
  -> build_analysis_manifest.py --provider <key> --model-json '<model>'
  -> spawn every Day / Code-Analysis subagent on that model
```

The resolved `provider` and `model` land in each day's analysis manifest, so
every subagent for the run executes on the same model.

## Host selection — never guessed

The host is one of `anthropic` / `openai` / `google`, decided by which runtime the
skill is executing under (Claude Code → `anthropic`, Codex → `openai`, Gemini →
`google`). The orchestrator passes it as `--host`.

- Only that one provider's entry is used. The three providers are **never** all
  passed to a subagent at once.
- The provider is **never** inferred from a model name.
- If the host cannot be determined, `resolve_provider_model.py` returns an
  `UNKNOWN_HOST` error and the skill **stops and reports a configuration error**.
  It never defaults to the first provider.

## Override precedence

For a given host, the model id is chosen in this order (highest first):

1. an explicit runtime id passed to the command (`--model`),
2. the environment variable for that provider,
3. the provider default in `config/provider_models.json`.

The skill never auto-substitutes an arbitrary host default beyond step 3.

| provider    | environment variable            |
| ----------- | ------------------------------- |
| `anthropic` | `REPO_WORKLOG_ANTHROPIC_MODEL`  |
| `openai`    | `REPO_WORKLOG_OPENAI_MODEL`     |
| `google`    | `REPO_WORKLOG_GOOGLE_MODEL`     |

## Unavailable-model policy

The config sets `model_unavailable_policy: halt-and-ask`. If the selected
`model_id` cannot be resolved or the host rejects it, the skill MUST halt and ask
rather than proceed. It MUST NOT:

- silently switch to a more expensive model,
- auto-pick another model,
- fall back to the previous defaults (Sonnet / Terra / Pro),
- degrade to reading only commit messages,
- ignore the error and keep generating a worklog.

It MUST report at least the `provider`, the requested `model_id`, the reason, and
the currently selectable candidate models, then let the user decide. The resolver
supplies the candidate list on `MODEL_UNAVAILABLE`.

Example message:

```text
Unable to start repo_worklog subagent.
Provider: openai
Requested model: gpt-5.6-luna
The requested model is not available in the current host.
No fallback model was selected automatically.

Currently selectable models:
  - Claude Haiku 4.5 (anthropic / claude-haiku-4-5)
  - Gemini 3.5 Flash (google / gemini-3.5-flash)

Please configure an available model ID and run the dry-run again.
```

## Escalation (opt-in, default OFF)

Each provider also carries an escalation model for the rare case where the
low-cost default cannot resolve a day confidently. Escalation is **never
automatic**.

| provider    | escalation_model_id       | escalation_reasoning_effort |
| ----------- | ------------------------- | --------------------------- |
| `anthropic` | `claude-sonnet-5`         | —                           |
| `openai`    | `gpt-5.6-terra`           | `medium`                    |
| `google`    | `gemini-3.1-pro-preview`  | —                           |

Rules (`escalation_policy.automatic` is fixed to `false`):

- Subagents never switch to the escalation model on their own.
- The orchestrator may only **suggest** escalation in a dry-run, when a day's
  return has `escalation_recommended: true` (see `subagent-contract.md` §7) — the
  `suggest_when` reasons are: analysis incomplete, confidence unknown, conflicting
  evidence, unresolved merge result, unclear revert final state, or unreadable
  required code context.
- Escalation runs only after the user explicitly approves. The approved day(s) are
  re-analysed with `resolve_provider_model.py --host <key> --escalate`, which
  mints a **new** dry-run and a **new `preview_id`**. The original preview is
  never edited in place.
