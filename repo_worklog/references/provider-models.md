# Provider Models

Per-host subagent model selection for the `repo_worklog` skill. This document
is the reference behind the `providers:` block in `agents/openai.yaml` and
mirrors plan section 7 (Subagent 模型設定).

The skill runs under one of three hosts. Each host has one provider key and one
model. Every Day Subagent and Code Analysis Subagent for a run is spawned on the
model belonging to the host the skill is currently running under.

## Per-host models

| Host        | provider key  | display_name       | model_id                       |
| ----------- | ------------- | ------------------ | ------------------------------ |
| Claude Code | `claude_code` | `claude-sonnet-5`  | `<runtime-specific-model-id>`  |
| Codex       | `codex`       | `gpt-5.6 Terra`    | `<runtime-specific-model-id>`  |
| Gemini      | `gemini`      | `gemini-flash-3.0` | `<runtime-specific-model-id>`  |

## display_name vs model_id

`display_name` and `model_id` are kept deliberately separate:

- `display_name` is the human-facing label shown in menus, dry-run summaries,
  and candidate lists. It never changes across hosts.
- `model_id` is the runtime dispatch identifier the host actually resolves when
  spawning a subagent. It is host-specific and must be set to whatever
  identifier the host genuinely supports before shipping.

The YAML shape (plan section 7.4, matching `agents/openai.yaml`):

```yaml
providers:
  claude_code:
    display_name: claude-sonnet-5
    model_id: <runtime-specific-model-id>
  codex:
    display_name: gpt-5.6 Terra
    model_id: <runtime-specific-model-id>
  gemini:
    display_name: gemini-flash-3.0
    model_id: <runtime-specific-model-id>
```

Do not treat the `display_name` as a routable identifier. Replace each
`model_id` with the host's exact model id during implementation; a wrong or
placeholder `model_id` must surface as an unavailable model (see below), never
as a silent fallback.

## How the skill uses it

The orchestrator (main coordinator) reads the `providers:` block, selects the
entry matching the host it is running under, and threads that provider's key and
`model_id` through the analysis pipeline:

```text
provider entry (by current host)
  -> build_analysis_manifest.py --provider <key> --model <model_id>
  -> spawn each Day / Code-Analysis subagent on <model_id>
```

The chosen `provider` and `model` also land in each day's analysis manifest
(see plan section 24.6), so every subagent for the run executes on the same
model.

## Unavailable-model policy

The manifest sets `model_unavailable_policy: halt-and-ask` (plan section 7.5).
If the selected `model_id` is not available, the skill MUST halt and ask rather
than proceed. It MUST NOT:

- silently switch to a more expensive model,
- auto-pick another model,
- degrade to reading only commit messages.

It MUST:

- stop any not-yet-started related subagent tasks,
- report that the specified model is unavailable,
- list the currently selectable candidate models (by `display_name`),
- let the user decide whether to substitute.

Example message:

```text
Model unavailable: gpt-5.6 Terra (codex) could not be selected on this host.

I have not started the day-by-day analysis, and I will not auto-switch to
another model. Currently selectable models:

  - claude-sonnet-5 (claude_code)
  - gemini-flash-3.0 (gemini)

Reply with one of the above to substitute, or tell me how to proceed.
```
