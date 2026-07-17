---
name: git-worklog
description: >-
  Analyze this Git repository's actual code changes day by day, maintain a
  human-readable project worklog under .git-worklog/ (one file per day plus an
  index.md), and answer questions from it. Use when the user runs /git-worklog,
  or asks to 整理/產生/補 工作日誌, build a per-day work log, summarize what actually
  changed in the repo over a date range, or document daily commits for handoff —
  and also to report from that history: 整理上一週工作摘要, draft a release
  CHANGELOG for a tag or version, summarize a period for a status update or
  handoff, ask what a named person worked on, or collect outstanding tech debt
  and follow-ups. Reads real diffs and code — never just commit messages.
  Reporting is read-only; writing always previews (dry-run) first and only
  happens after explicit confirmation.
---

# Git Worklog

Produce a **project** worklog (not a personal report) by reading the real Git
diffs and surrounding code for each day in a requested range, then write one
Markdown file per day under `.git-worklog/` and refresh `.git-worklog/index.md`,
while preserving human notes.

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
  This holds in report mode too: where a day has no worklog, its commit messages
  are **not** a substitute — surface the gap and ask.
- **Whole project, every author.** The worklog always *stores* every author;
  never filter by `git config user.name/email`. Report mode may filter by author
  **only when the user names the person explicitly** — never infer who "我" is.
- **Max 30 calendar days for generation and backfill.** The cap bounds per-day
  subagent cost. Report mode only *reads* existing day files and spawns no
  subagents, so it reads up to 90 days (`--max-days 90`). Either way, over the
  limit → refuse, show the requested day count, ask the user to narrow. Never
  silently truncate.
- **Dry-run first, always.** Any valid request produces a preview only. Write
  only after the user explicitly confirms.
- **One file per day; the index is navigation.** Each day is
  `.git-worklog/days/<date>.md`; re-analysing one day never touches another day's
  file. `index.md` is rebuilt from the day files.
- **Preserve every day's MANUAL region and the index MANUAL region, forever.**
- **Never run** `git add/commit/push/fetch/pull/checkout/switch/merge/rebase`.

---

## 1. Trigger & no-argument menu

Triggered by `/git-worklog` or natural-language worklog requests.

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

## 1a. Route: generate or report

Two modes. Decide before doing anything else.

| The user wants | Mode | Goes to |
|---|---|---|
| The worklog itself built or filled in — 「整理今天」「整理最近 7 天」「補 7/1 的日誌」 | **Generate** (writes) | §2 onward |
| An **answer** drawn from the history — 「整理上一週工作摘要」「整理 v1.0.1 CHANGELOG」「Daniel 上個月做了什麼」「目前有哪些技術債」 | **Report** (read-only) | `references/report-mode.md` |

The tell: generate mode's product is **files**; report mode's product is **prose
in the conversation**. "整理今天" wants a day file. "整理上一週的工作摘要" wants
something to paste into a status update. When it is genuinely ambiguous, ask —
do not write files on a guess.

Report mode reads the existing day files plus Git, writes nothing, and therefore
has no dry-run or confirmation gate. Its one writing path is backfilling a gap,
which hands back to §§2–8 for just those dates, dry-run and confirmation
included.

**The menu is generate-only.** Report mode is reached by natural language or
explicit parameters, never by an option number — the menu asks "which range to
build", which is not the question report mode answers.

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

## 2a. Resolve the output language (roadmap §6.2)

**This is your job, not the scripts'.** The top of the priority order lives only
in this conversation — what the user asked for, what language you are speaking,
what the host is set to. A script cannot see any of it, and `--language` is how
you tell it. Resolve **once per run** and thread the same tag everywhere.

Priority, highest first:

1. **What the user asked for in this request** — "用英文整理" → `en`. Always wins.
2. **The project's `language` in `.git-worklog/config.json`**, if it is not
   `auto`. The scripts read this themselves; pass `--language auto` and let them.
3. **The language you are conversing in with this user, i.e. the host's
   language.** This is the normal case and it is a real answer — pass it, with
   `--language-source agent-host`.

Then also pass `--language-source` saying which of those it was, so the manifest
records *why* and not merely what:

| You resolved it from | `--language-source` |
|---|---|
| The user asking for a language in this request | `user-request` |
| The language of this conversation / your host | `agent-host` |
| Nothing to say — let config and env decide | omit, with `--language auto` |

**Do not let the repository choose.** English commit messages, English
identifiers, English comments and an English README decide nothing. A fully
English repo with a zh-TW conversation produces a zh-TW worklog. Do not "match
the codebase", and do not read this English SKILL.md as a hint.

**Do not infer from the OS locale.** You are an agent-hosted run, and
`--language` is exactly the mechanism that keeps a container pinned to `en_US`
from overriding a user speaking Chinese. The scripts will not consult the locale
for you (§6.2.5); that is deliberate, not a gap to fill.

If a run's manifest comes back with `source: "fallback"` and a
`LANGUAGE_NOT_RESOLVED` warning, you failed to pass a language and the run is
about to be written in English. **Re-run with an explicit `--language` rather
than accepting it** (§6.2.14) — unless English is genuinely right.

**Reports may differ from the worklog.** Day files are written in the language
of the run that produced them; a report is written in the language of the
request that asks for it (§6.2.11). Reading zh-TW day files and producing an
English release note is correct and needs no conversion of anything on disk.

**Never translate**: file paths, code symbols, commit hashes, API/class/package
names, branch and issue references, or anything in `evidence[]`. Explaining a
term in the output language is welcome; renaming it is not.

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

Split the range into one task **per calendar day**.

**5·0. Mint the run's result directory once, before dispatching anything:**

```
python3 scripts/collect_day_results.py init --dates <every date in the range>
```

Keep `run_dir` and `paths`. Each Day Subagent **writes** its JSON to its own
`paths[<date>]` and replies only `DONE` — results are never passed back as reply
text, which drops and truncates them. See `references/subagent-contract.md` §6a.

Then, for each day:

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
      --language <tag|auto> --language-source <source> \
      --provider <provider> --model-json '<model object>' [--include-uncommitted --worktree <file>]
```

`--language` and `--language-source` come from §2a and are **identical for every
day of the run** — a manifest's `language.resolved` is what each day's result is
checked against, and days that disagree block the whole run.

Resolve `<provider>` and `<model object>` **once for the whole run** with
`scripts/resolve_provider_model.py --host <anthropic|openai|google>` (see the
model table below), then thread its `provider` and `model` into every day's
manifest. `model` is an object — `{display_name, model_id}` plus
`reasoning_effort` for openai only.

Gives `file_groups` (grouped by real work area), `required_context`, a
`large_day` flag recommending Code Analysis Subagents when the day is big, and
the day's authorship — `authors[]` (distinct names, first-appearance order) plus
`commits[].author_name`. You render the `參與者` line and each `相關 commits`
entry's author from these directly; the subagent never returns attribution.

**5c. Spawn one Day Subagent** for that day, passing the manifest **and its
`paths[<date>]` output path**. The subagent must read the real diffs and enough
code context, determine the **end-of-day state** (a feature added then reverted
the same day is *not* a live change), and **write** the structured JSON in
`references/subagent-contract.md` to that path, replying only `DONE`. It must not
write to the worklog. Days with no commits still write `has_changes:false`.

**5d. Collect every day's result** (deterministic), once all subagents finish:

```
python3 scripts/collect_day_results.py read --run-dir <run_dir> \
    --dates <every dispatched date> --language <the manifest's language.resolved>
```

It validates each file against the return schema and gives `results` (date →
object), `complete`, `degraded`, `missing`, `invalid`, `failed_dates`,
`partial_run`, and `escalation_suggested_dates`. **A date in `missing` or
`invalid` is a failed day, not an empty one** — never treat it as "nothing
happened" and never fall back to its commit messages. `partial_run:true` blocks
apply by default (§9).

For large days, the Day Subagent may fan out into Code Analysis Subagents grouped
by feature/module (see `references/subagent-contract.md`).

Model per host — resolve the provider you are running under with
`resolve_provider_model.py --host <key>` and pass its `model_id` (cost-first
defaults; single source is `config/provider_models.json`):

| Host        | provider key | default display | default model_id      |
|-------------|--------------|-----------------|-----------------------|
| Claude Code | `anthropic`  | Claude Haiku 4.5 | `claude-haiku-4-5`   |
| Codex       | `openai`     | GPT-5.6 Luna (effort `low`) | `gpt-5.6-luna` |
| Gemini      | `google`     | Gemini 3.5 Flash | `gemini-3.5-flash`   |

Pick the host you actually run under — never guess the provider from a model
name, and never pass all three at once. If the host cannot be determined, stop
and report a configuration error (`UNKNOWN_HOST`). Overrides: an explicit
`--model` beats `GIT_WORKLOG_<PROVIDER>_MODEL`, which beats the config default.

If the chosen model is unavailable: **stop**, report the provider and requested
`model_id`, list candidates, and let the user decide. Never silently fall back to
a pricier model, never auto-switch to the escalation model, never degrade to
reading only commit messages. Escalation is opt-in and requires explicit user
approval (a new dry-run + new `preview_id`). Details:
`references/provider-models.md`.

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

1. Merge the per-day results from `collect_day_results.py read` (§5d). If it
   reports any `missing`, `invalid`, or `degraded` date — i.e. `partial_run` —
   mark the run **partial** and default to blocking apply (see error handling
   below).
2. Render each day's GENERATED Markdown from the day template in
   `references/worklog-format.md`, **in the run's resolved language** (§2a) —
   headings included. Omit empty sections — no walls of "無/N/A". Days with no
   changes get no file by default. Lead each day's summary with its single most
   useful sentence and **bracket it in SUMMARY markers**; that line becomes the
   index row, and without the markers a day written in anything but Traditional
   Chinese gets a blank one.
3. Simulate the day-file writes (dry-run is the default — no `--apply`). Pass
   `meta` (timezone/branch/short HEAD) and only dates that actually have content:

```
python3 scripts/update_daily_worklog.py --dir .git-worklog <<'JSON'
{"meta": {"timezone": "Asia/Taipei", "branch": "main", "head": "abc1234"},
 "entries": {"2026-07-15": {"generated_markdown": "..."}, "...": {...}}}
JSON
```

Per date it plans `create` / `overwrite` / `no_change`, preserves MANUAL, and
returns `planned_changes`, `previews` (full per-day file text), `summaries` (the
one-line index summary per date), `preserved_manual_dates`, and `file_hashes`
(`{original, preview}` per date). A corrupt existing day file aborts with
`CORRUPT_MARKERS` — never guess a repair.

4. Simulate the index rebuild, passing the pending day summaries as `overrides`
   so the preview reflects the about-to-be-written state without touching disk:

```
python3 scripts/rebuild_worklog_index.py --dir .git-worklog --language <tag> <<'JSON'
{"overrides": {"2026-07-15": "新增會員搜尋快取並補充 API 測試", "...": "..."}}
JSON
```

It returns the rebuilt `preview`, the descending `dates`, `index_hash`
(`{original, preview}`), and `preserved_index_manual`.

5. Create a preview record so a later apply can be integrity-checked. The
   `worklog` fingerprint is now multi-file: the current `index.md` hash, each
   target day file's hash (or `"missing"`), and a fingerprint of the day-file
   listing:

```
python3 scripts/preview_state.py create <<'JSON'
{"repository": {"root": "...", "branch": "...", "head": "...",
                "worktree_fingerprint": "<or omit if not include_uncommitted>"},
 "worklog": {"index_sha256": "<hash or 'missing'>",
             "day_files": {"2026-07-15": "<hash or 'missing'>"},
             "dir_fingerprint": "<hash of the sorted <date>.md listing>"},
 "params": {"mode": "days", "timezone": "...", "include_uncommitted": false,
            "language": "<the run's resolved language>"}}
JSON
```

`params.language` is compared at apply. A user who confirms a zh-TW preview and
then asks for English is asking for a **different worklog**, not the same one
rendered differently: the preview goes stale, and you build and confirm a new one
rather than applying the old payload under a new language (§6.2.10).

6. Show the user the dry-run summary described in
   `references/interaction-flow.md`: repository root, branch, HEAD, timezone,
   requested mode, resolved range, `include_uncommitted`, the **output
   language** (and, when it came from `fallback`, say so — the user may want to
   correct it before anything is written), the **subagent configuration** (provider, model, reasoning effort, automatic escalation:
   disabled), per-day commit counts and status, files analyzed, per-date planned
   action (create / overwrite / no-change), the index rebuild, preserved MANUAL
   dates, each day file's full preview, the index preview, the target directory
   `.git-worklog/`, the `preview_id`, and the line
   **"No files have been modified."**

---

## 8. Apply only after explicit confirmation

Natural-language confirmations ("寫入", "確認更新", "套用剛才的預覽", "把這份寫進去")
or `apply <preview_id>`.

1. Re-detect repository state and, if `include_uncommitted`, re-run
   `inspect_worktree.py`. Re-hash the current `index.md` and each target day
   file, and re-fingerprint the day-file listing.
2. Verify the preview is still valid (pass the same multi-file `worklog` block):

```
python3 scripts/preview_state.py verify --id <preview_id> --mark-applied <<'JSON'
{"repository": {"root": "...", "branch": "...", "head": "...",
                "worktree_fingerprint": "..."},
 "worklog": {"index_sha256": "<current>",
             "day_files": {"2026-07-15": "<current or 'missing'>"},
             "dir_fingerprint": "<current>"},
 "params": {"timezone": "...", "include_uncommitted": false}}
JSON
```

   Exit 3 / `consistent:false` → **do not write.** Report the reason
   (`already applied`, `expired`, or `state changed since dry-run` — including a
   changed target day file, changed `index.md`, or an added/removed day file) and
   re-run the dry-run for a fresh preview.

3. Write the day files as one transaction (staged, validated, atomically
   swapped, rolled back on any failure), then rebuild the index:

```
python3 scripts/update_daily_worklog.py --dir .git-worklog --apply <<'JSON'
{"meta": { ...same meta... }, "entries": { ...same entries as the dry-run... }}
JSON
python3 scripts/rebuild_worklog_index.py --dir .git-worklog --language <tag> --apply
```

   The day-file write is all-or-nothing. The index is a pure function of the day
   files, so if the index step ever fails after the day files succeed, re-run
   `rebuild_worklog_index.py --apply` to repair it — no day data is lost.

4. Confirm with `validate_daily_worklog.py --dir .git-worklog` and
   `validate_worklog_index.py --dir .git-worklog`, then report the actual
   update (dates created/overwritten, MANUAL preserved, index rebuilt, target
   directory). `.git-worklog/` is created now if it was missing. Do **not**
   git add/commit.

---

## 9. Error handling (summary)

- **Not a Git repo / >30 days / corrupt markers / non-UTF-8:** stop, report,
  never auto-repair. `update_daily_worklog.py` refuses a corrupt target day file
  (`CORRUPT_MARKERS`); `rebuild_worklog_index.py` refuses a corrupt `index.md`
  (`INDEX_CORRUPT_MARKERS`) so its MANUAL is never lost; the validators list every
  issue.
- **Unreadable code (permissions, missing submodule):** record what was not
  analyzed, lower `confidence`, note it in `uncertainties`; never fake analysis.
- **A day's subagent failed:** keep other days, mark the run partial, block apply
  by default. The user may choose to write only the successful days — if so,
  re-run the dry-run with just those dates and mint a new `preview_id`.
- **Date exists but re-analysis finds no commits:** do not auto-delete the day
  file; show the diff, keep MANUAL, and require explicit confirmation to clear
  GENERATED.
- **A legacy worklog is present:** never migrate automatically. Offer
  `migrate_legacy_worklog.py` (dry-run + confirm); it never deletes the source.
  Two shapes qualify — a flat `PROJECT_WORKLOG/` directory (`--from-dir`) and the
  single `docs/PROJECT_WORKLOG.md` (`--from-file`). With neither flag the script
  auto-detects, directory first.
- **Writing refused with `LEGACY_LAYOUT`:** the target directory still holds its
  day files at the root rather than under `days/`. Do not work around it by
  passing a different `--dir` — offer the migration. Reading a legacy directory
  (validate, coverage, report mode) keeps working untouched.

Full rules: `references/interaction-flow.md`, `references/code-analysis-rules.md`.

---

## Reference & script map

| Need | Read |
|------|------|
| Report mode: scope (dates vs refs), coverage, gaps, scenarios | `references/report-mode.md` |
| Menu, options, dry-run summary, confirmation, apply | `references/interaction-flow.md` |
| Date modes, timezone, 30-day limit, NL normalisation | `references/date-parameter-contract.md` |
| Diff reading, context expansion, final-state, merge/revert/rename/binary/lockfile/submodule | `references/code-analysis-rules.md` |
| Day/Code-Analysis subagent prompts, return schema, confidence, evidence | `references/subagent-contract.md` |
| Directory layout, day/index markers, create/overwrite, migration | `references/worklog-format.md` |
| Per-host models, overrides, unavailable-model handling, escalation | `references/provider-models.md` |

| Script | Role |
|--------|------|
| `resolve_provider_model.py` | Resolve the per-host subagent provider/model (single source `config/provider_models.json`; overrides, escalation, halt-and-ask) |
| `resolve_date_range.py` | Parse/validate dates, timezone, day-span limit (`--max-days`, default 30), per-day bounds |
| `resolve_ref_range.py` | Report mode: resolve a tag/ref to its authoritative commit set + derived dates |
| `check_worklog_coverage.py` | Report mode: per-date coverage — `covered` / `gap` / `no-commits` |
| `collect_git_history.py` | Repo metadata + per-day commit facts (no summaries, no author filter) |
| `inspect_worktree.py` | Staged/unstaged/untracked + worktree fingerprint (include_uncommitted only) |
| `build_analysis_manifest.py` | Group changed files, propose required context, flag large days |
| `collect_day_results.py` | Mint the run's result dir + per-date paths; read back and validate each Day Subagent's written JSON (missing/invalid → that day failed) |
| `update_daily_worklog.py` | Simulate/apply per-day files (create/overwrite/no-change); preserve MANUAL; transactional write |
| `rebuild_worklog_index.py` | Rebuild `index.md` from day files (descending, summaries); preserve index MANUAL; atomic write |
| `validate_daily_worklog.py` | Per-day file marker/title/UTF-8 validation |
| `validate_worklog_index.py` | Index marker/order/link/UTF-8 validation |
| `preview_state.py` | Multi-file preview fingerprint, id, apply-time consistency, anti-double-apply |
| `migrate_legacy_worklog.py` | One-time migration of a legacy worklog (flat `PROJECT_WORKLOG/`, or the single `docs/PROJECT_WORKLOG.md`) into `.git-worklog/` |
| `worklog_markers.py` | Shared day/index parser/serialiser (imported by the scripts above) |
