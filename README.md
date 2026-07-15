# repo_worklog skill

A portable agent **skill** that turns a Git repository's real code history into a
human-readable, per-day **project worklog** at `docs/PROJECT_WORKLOG.md`.

It reads the actual diffs and surrounding code — never just commit messages —
analyzes each day with its own subagent, previews every change as a dry-run, and
only writes after you explicitly confirm. It logs the whole project's history
(every author), and it never runs `git add/commit/push`.

## Layout

```
repo_worklog/                 # the skill (this whole directory is the skill)
├── SKILL.md                  # control layer: triggers, flow, script/reference map
├── agents/
│   └── openai.yaml           # host manifest: display name, providers, UI metadata
├── scripts/                  # deterministic Python helpers (stdlib only)
│   ├── resolve_date_range.py     # date/timezone parsing, 30-day limit, per-day bounds
│   ├── collect_git_history.py    # repo metadata + per-day commit facts (no summaries)
│   ├── inspect_worktree.py       # staged/unstaged/untracked + worktree fingerprint
│   ├── build_analysis_manifest.py# group changed files, propose reading, flag big days
│   ├── update_worklog.py         # simulate/apply insert & overwrite; preserve MANUAL
│   ├── validate_worklog.py       # marker + UTF-8 structural validation
│   ├── preview_state.py          # preview fingerprint, id, apply-time consistency
│   └── worklog_markers.py        # shared marker parser/serialiser (imported by 3 above)
└── references/               # detailed specs the skill loads on demand
    ├── interaction-flow.md
    ├── date-parameter-contract.md
    ├── code-analysis-rules.md
    ├── subagent-contract.md
    ├── worklog-format.md
    └── provider-models.md

docs/init_plan.md             # the original design specification
```

## Requirements

- **Python 3.9+** (uses `zoneinfo`; developed and tested on 3.14). Standard
  library only — no third-party packages.
- **Git 2.37+** (uses `git log --since-as-filter`; developed on 2.54).
- A host that supports agent skills (Claude Code, and — via `agents/openai.yaml`
  — Codex / Gemini).

## Installation

This directory *is* the skill. Install it by placing the `repo_worklog/` folder
where your host discovers skills, for example for Claude Code:

```bash
# user-level
cp -r repo_worklog ~/.claude/skills/repo_worklog
# or project-level, inside a target repo
cp -r repo_worklog <your-project>/.claude/skills/repo_worklog
```

Then invoke it with `/repo_worklog` (or natural language like “整理最近 7 天”).

## Usage

Run with no arguments to get the range menu; the skill does nothing until you
pick a range:

```
/repo_worklog
```

Or drive it directly / in natural language:

```
/repo_worklog days=7
/repo_worklog date=2026-07-01
/repo_worklog from=2026-07-01 to=2026-07-10
/repo_worklog date=2026-07-15 include_uncommitted=true
整理最近 7 天，包含目前還沒有 commit 的修改
```

Every valid request produces a **dry-run preview** with a `preview_id`. Confirm
with “寫入” / “確認更新” / `apply <preview_id>` to write. See
`repo_worklog/references/interaction-flow.md` for the full flow.

## Configuration

- **Target file:** defaults to `docs/PROJECT_WORKLOG.md`
  (`update_worklog.py --target` to override).
- **Timezone:** auto-detected (`$TZ` → `/etc/localtime` → offset); override with
  `resolve_date_range.py --timezone Asia/Taipei`.
- **Subagent models:** set per host in `repo_worklog/agents/openai.yaml`
  (`providers.*.model_id`). See `references/provider-models.md`.
- **Preview state:** stored outside the repo in `~/.repo_worklog/previews/`.

## Development commands

Each script is standalone and prints one JSON object to stdout:

```bash
cd repo_worklog
python3 scripts/resolve_date_range.py --days 7 --timezone Asia/Taipei --today 2026-07-15
python3 scripts/collect_git_history.py --repo /path/to/repo --info-only
python3 scripts/update_worklog.py --target /tmp/WL.md <<'JSON'
{"entries": {"2026-07-15": {"generated_markdown": "### 當日摘要\n\n..."}}}
JSON
python3 scripts/validate_worklog.py --target /tmp/WL.md
```

## Tests

There is no packaged test suite yet. The scripts were verified against a
controlled Git fixture (single/multi-commit days, revert, rename, binary,
uncommitted changes) and an end-to-end run of the worklog engine (insert,
overwrite, MANUAL preservation, corruption detection, preview consistency).
See `docs/init_plan.md` section 27 for the intended acceptance-test matrix.

## Safety model

- Dry-run first, always; nothing is written without explicit confirmation.
- `MANUAL` regions and all content outside the `ENTRIES` area are preserved
  verbatim; only `GENERATED` regions are overwritten.
- Writes are atomic (same-directory temp file + atomic replace, re-validated).
- Applies are gated by a preview fingerprint (repo/branch/HEAD/worktree/worklog
  hash); a stale or already-used preview is refused.
- The skill never runs `git add/commit/push/fetch/pull/checkout/switch/merge/rebase`.
```
