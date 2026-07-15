---
name: repo_worklog
description: >-
  Analyze this Git repository's actual code changes day by day and maintain a
  human-readable project worklog at docs/PROJECT_WORKLOG.md. Use when the user
  runs /repo_worklog, or asks to 整理/產生/補 工作日誌, build a per-day work log or
  changelog, summarize what actually changed in the repo over a date range, or
  document daily commits for handoff. Reads real diffs and code — never just
  commit messages. Always previews (dry-run) before writing, and only writes
  after explicit confirmation.
---

# repo_worklog

Produce a **project** worklog (not a personal report) by reading the real Git
diffs and surrounding code for each day in a requested range, then insert or
overwrite dated entries in a Markdown worklog while preserving human notes.

The deterministic work (date math, Git collection, Markdown surgery, preview
integrity) is done by the scripts in `scripts/`. The judgement work (reading
code, deciding what actually changed, writing the summary) is done by you and
per-day subagents. **Never let commit messages stand in for reading the diff.**

`python3` and `git` must be available. Run every script with
`python3 scripts/<name>.py`; each prints one JSON object to stdout.

---

## 0. Golden rules

- **No parameters → show the menu and stop.** Do not scan Git, spawn subagents,
  or generate anything until the user picks a range.
- **Read code, not just messages.** Every relevant commit's actual patch and the
  surrounding code context must be read. See `references/code-analysis-rules.md`.
- **Whole project, every author.** Never filter by `git config user.name/email`.
- **Max 30 calendar days.** Over the limit → refuse, show the requested day
  count, ask the user to narrow. Never silently truncate.
- **Dry-run first, always.** Any valid request produces a preview only. Write
  only after the user explicitly confirms.
- **Preserve MANUAL regions and everything outside the ENTRIES area, forever.**
- **Never run** `git add/commit/push/fetch/pull/checkout/switch/merge/rebase`.

---

## 1. Trigger & no-argument menu

Triggered by `/repo_worklog` or natural-language worklog requests.

When invoked with **no usable arguments**, print this menu verbatim and wait —
do nothing else:

```
請選擇要整理的專案工作日誌範圍：

1. 今天
2. 指定日期
3. 最近 7 天
4. 最近 30 天
5. 自訂日期範圍
6. 今天，並包含尚未提交的異動
7. 自訂日期或範圍，並包含尚未提交的異動

日期範圍最多為 30 天。
所有操作都會先顯示 dry-run 預覽，不會直接修改專案檔案。

你可以直接輸入選項編號，或用自然語言回答，例如：
- 整理今天
- 整理 2026-07-01
- 整理最近 7 天
- 整理 2026-07-01 到 2026-07-10
- 整理今天並包含未提交異動
- 整理近 30 天
```

Option numbers map to: `1`→today, `2`→ask for a date, `3`→`days=7`,
`4`→`days=30`, `5`→ask for a from/to range, `6`→today + `include_uncommitted`,
`7`→ask for a date/range + `include_uncommitted`.

Full menu, option, and confirmation handling: `references/interaction-flow.md`.

---

## 2. Normalise input → canonical parameters

You (the model) convert natural language into canonical parameters; the scripts
never interpret free text. Canonical parameters:

`date` · `days` · `from` · `to` · `include_uncommitted`, plus the shortcuts
`7d`, `30d`, and a bare `YYYY-MM-DD`.

- "整理今天" → `date=<local today>`
- "最近一週" → `days=7`; "近一個月" → `days=30` (always 30, never the month length)
- "7 月 1 日到 7 月 10 日" → `from=2026-07-01 to=2026-07-10`
- "包含未提交 / 連 working tree 一起" → `include_uncommitted=true`

Modes `date` / `days` / `from`+`to` are mutually exclusive. Normalisation cases:
`references/date-parameter-contract.md`.

---

## 3. Validate the date range (always first)

```
python3 scripts/resolve_date_range.py <args> [--timezone <IANA>] [--today <YYYY-MM-DD>]
```

Pass the canonical parameters, e.g. `--days 7`, `--date 2026-07-01`,
`--from 2026-07-01 --to 2026-07-10`, or a shortcut like `7d`. Add
`--include-uncommitted` when requested.

- On `ok:false`, report the error to the user and stop. Common codes:
  `TOO_MANY_DAYS` (show `requested_days`, ask to narrow), `ARG_CONFLICT`,
  `DAYS_OUT_OF_RANGE`, `INVALID_DATE`, `FROM_AFTER_TO`, `TO_WITHOUT_FROM`.
- On `ok:true`, keep `dates[]` (each has `date`, `start`, `end` half-open
  bounds), `timezone`, and `timezone_source`. If `timezone_source` is
  `system-offset` (no IANA name), tell the user which offset is assumed and
  offer to set one.

Timezone priority and half-open `[00:00, next 00:00)` day rule:
`references/date-parameter-contract.md`.

---

## 4. Detect repository state

```
python3 scripts/collect_git_history.py --repo <root> --info-only
```

- `NOT_A_GIT_REPO` → tell the user this directory is not a Git repository and stop.
- Record `root`, `branch` (null if `detached_head`), `head`/`short_head`,
  `has_commits`, `dirty_worktree`. These appear in the dry-run summary.
- Empty repo (`has_commits:false`): with `include_uncommitted=false` there is
  nothing to log — say so. With `include_uncommitted=true`, analyze only the
  working tree.

---

## 5. Per-day analysis — one Day Subagent per day

Split the range into one task **per calendar day**. For each day:

**5a. Collect that day's Git facts** (deterministic):

```
python3 scripts/collect_git_history.py --repo <root> \
    --since <day.start> --until <day.end>
```

Returns the day's commits with metadata, parents, merge flag, revert
candidate flag, and each commit's changed files (rename/copy aware, binary and
submodule flags, add/del counts). Patches are **not** included — subagents read
them with `git show`.

**5b. Build the day's manifest** (deterministic):

```
python3 scripts/collect_git_history.py ... \
  | python3 scripts/build_analysis_manifest.py --date <day> --timezone <tz> \
      --provider <provider> --model <model_id> [--include-uncommitted --worktree <file>]
```

Gives `file_groups` (grouped by real work area), `required_context`, and a
`large_day` flag recommending Code Analysis Subagents when the day is big.

**5c. Spawn one Day Subagent** for that day, passing the manifest. The subagent
must read the real diffs and enough code context, determine the **end-of-day
state** (a feature added then reverted the same day is *not* a live change), and
return the structured JSON in `references/subagent-contract.md`. It must not
write to the worklog. Days with no commits still report `has_changes:false`.

For large days, the Day Subagent may fan out into Code Analysis Subagents grouped
by feature/module (see `references/subagent-contract.md`).

Model per host — pick the provider you are running under and pass its `model_id`:

| Host        | display        | provider key   |
|-------------|----------------|----------------|
| Claude Code | claude-sonnet-5 | `claude_code` |
| Codex       | gpt-5.6 Terra  | `codex`        |
| Gemini      | gemini-flash-3.0 | `gemini`     |

If the chosen model is unavailable: **stop**, report it, list candidates, and
let the user decide. Never silently fall back to a pricier model, never degrade
to reading only commit messages. Details: `references/provider-models.md`.

---

## 6. Uncommitted changes (only when include_uncommitted=true)

```
python3 scripts/inspect_worktree.py --repo <root>
```

Classifies `staged` / `unstaged` / `untracked` (binary-aware) and returns a
`worktree_fingerprint`. Uncommitted content is attributed to **today only** —
never spread across historical days. Present it in its own
`### 尚未提交的異動` section, split into staged / unstaged / untracked, and never
describe it as committed. In a multi-day range, only today gets a worktree pass.

---

## 7. Merge results, generate Markdown, dry-run

1. Merge the per-day subagent results. If any day's subagent failed, mark the
   run **partial** and default to blocking apply (see error handling below).
2. Render each day's Markdown from the day template in
   `references/worklog-format.md`. Omit empty sections — no walls of "無/N/A".
   Days with no changes get no entry by default.
3. Simulate the update (dry-run is the default — no `--apply`):

```
python3 scripts/update_worklog.py --target docs/PROJECT_WORKLOG.md <<'JSON'
{"entries": {"2026-07-15": {"generated_markdown": "..."}, "...": {...}}}
JSON
```

Only pass dates that actually have content. The script inserts new dates in
descending order, overwrites the GENERATED region of existing dates, preserves
MANUAL, and returns `preview_content`, `diff`, `planned_changes`,
`preserved_manual_dates`, `original_sha256`, and `preview_sha256`.

4. Create a preview record so a later apply can be integrity-checked:

```
python3 scripts/preview_state.py create <<'JSON'
{"repository": {"root": "...", "branch": "...", "head": "...",
                "worktree_fingerprint": "<or omit if not include_uncommitted>"},
 "worklog": {"original_sha256": "...", "preview_sha256": "..."},
 "params": {"mode": "days", "timezone": "...", "include_uncommitted": false}}
JSON
```

5. Show the user the dry-run summary described in
   `references/interaction-flow.md`: repository root, branch, HEAD, timezone,
   requested mode, resolved range, `include_uncommitted`, per-day commit counts
   and status, files analyzed, dates to insert vs overwrite, no-change days,
   preserved MANUAL dates, the full worklog preview, target path, the
   `preview_id`, and the line **"No files have been modified."**

---

## 8. Apply only after explicit confirmation

Natural-language confirmations ("寫入", "確認更新", "套用剛才的預覽", "把這份寫進去")
or `apply <preview_id>`.

1. Re-detect repository state and, if `include_uncommitted`, re-run
   `inspect_worktree.py`. Re-hash the current worklog.
2. Verify the preview is still valid:

```
python3 scripts/preview_state.py verify --id <preview_id> --mark-applied <<'JSON'
{"repository": {"root": "...", "branch": "...", "head": "...",
                "worktree_fingerprint": "..."},
 "worklog": {"original_sha256": "<current hash>"},
 "params": {"include_uncommitted": false}}
JSON
```

   Exit 3 / `consistent:false` → **do not write.** Report the reason
   (`already applied`, `expired`, or `state changed since dry-run`) and re-run
   the dry-run for a fresh preview.

3. Write atomically (same-directory temp file + atomic replace, re-validated
   before and after the swap):

```
python3 scripts/update_worklog.py --target docs/PROJECT_WORKLOG.md --apply <<'JSON'
{"entries": { ... same entries as the dry-run ... }}
JSON
```

4. Confirm with `python3 scripts/validate_worklog.py --target docs/PROJECT_WORKLOG.md`,
   then report the actual update (dates inserted/overwritten, MANUAL preserved,
   target path). `docs/` is created now if it was missing. Do **not** git add/commit.

---

## 9. Error handling (summary)

- **Not a Git repo / >30 days / corrupt markers / non-UTF-8:** stop, report,
  never auto-repair. `update_worklog.py` refuses corrupt files
  (`CORRUPT_MARKERS`); `validate_worklog.py` lists every issue with line numbers.
- **Unreadable code (permissions, missing submodule):** record what was not
  analyzed, lower `confidence`, note it in `uncertainties`; never fake analysis.
- **A day's subagent failed:** keep other days, mark the run partial, block apply
  by default. The user may choose to write only the successful days — if so,
  re-run the dry-run with just those dates and mint a new `preview_id`.
- **Date exists but re-analysis finds no commits:** do not auto-delete the block;
  show the diff, keep MANUAL, and require explicit confirmation to clear GENERATED.

Full rules: `references/interaction-flow.md`, `references/code-analysis-rules.md`.

---

## Reference & script map

| Need | Read |
|------|------|
| Menu, options, dry-run summary, confirmation, apply | `references/interaction-flow.md` |
| Date modes, timezone, 30-day limit, NL normalisation | `references/date-parameter-contract.md` |
| Diff reading, context expansion, final-state, merge/revert/rename/binary/lockfile/submodule | `references/code-analysis-rules.md` |
| Day/Code-Analysis subagent prompts, return schema, confidence, evidence | `references/subagent-contract.md` |
| Markdown template, markers, insert/overwrite, empty days | `references/worklog-format.md` |
| Per-host models and unavailable-model handling | `references/provider-models.md` |

| Script | Role |
|--------|------|
| `resolve_date_range.py` | Parse/validate dates, timezone, 30-day limit, per-day bounds |
| `collect_git_history.py` | Repo metadata + per-day commit facts (no summaries, no author filter) |
| `inspect_worktree.py` | Staged/unstaged/untracked + worktree fingerprint (include_uncommitted only) |
| `build_analysis_manifest.py` | Group changed files, propose required context, flag large days |
| `update_worklog.py` | Simulate/apply insert & overwrite; preserve MANUAL; atomic write |
| `validate_worklog.py` | Structural marker & UTF-8 validation |
| `preview_state.py` | Preview fingerprint, id, apply-time consistency, anti-double-apply |
| `worklog_markers.py` | Shared marker parser/serialiser (imported by the three above) |
