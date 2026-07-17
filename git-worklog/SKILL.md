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
integrity) is done by the `git-worklog` CLI and the scripts in `scripts/`. The
judgement work (reading code, deciding what actually changed, writing the
summary) is done by you and per-day subagents. **Never let commit messages stand
in for reading the diff.**

`python3` and `git` must be available; nothing needs installing. Run the CLI as
`python3 -m git_worklog <command>` and any script as `python3 scripts/<name>.py`,
both from this directory. Each prints one JSON object to stdout. Exit `0` means
ok, `1` means it ran and found a problem, `2` means it could not run.

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

Two commands bracket this section. `analyze prepare` decides **what** must be
analysed and **in which language**; `analyze collect` decides whether to
**believe** what came back. Everything between them — reading the patches,
understanding the code, writing the prose — is yours and the subagents'. The CLI
never does it and needs no model API key.

**5a. Prepare the run** (deterministic, once for the whole range):

```
python3 -m git_worklog analyze prepare --repo <root> \
    --from <first date> --to <last date> --timezone <tz> \
    --language <tag|auto> --language-source <source> \
    --provider <provider> --model-json '<model object>' \
    [--include-uncommitted]
```

Returns `run_id`, `run_dir`, and `tasks[]` — one entry per calendar day, each
with a `manifest_path` (what to analyse) and a `result_path` (where its analysis
must be written). Keep all of it.

`--language` and `--language-source` come from §2a. They are resolved **once**
here and stamped on every manifest in the run: a manifest's `language.resolved`
is what each day's result is checked against, and days that disagree block the
whole run.

Resolve `<provider>` and `<model object>` **once for the whole run** with
`scripts/resolve_provider_model.py --host <anthropic|openai|google>` (see the
model table below). `model` is an object — `{display_name, model_id}` plus
`reasoning_effort` for openai only.

Each manifest gives `file_groups` (grouped by real work area),
`required_context`, `analysis_rules`, a `large_day` flag recommending Code
Analysis Subagents when the day is big, `parts_dir` (where a fan-out's parts go
— never beside the day's result, see below), `required_commit_file_pairs` (§5b),
and the day's authorship — `authors[]` (distinct names, first-appearance order)
plus `commits[].author_name`. You render the `參與者` line and each
`相關 commits` entry's author from these directly; the subagent never returns
attribution.

A large day's fan-out writes its per-group parts to `parts_dir`, **not** beside
the day's `result_path`. `results/` holds the run's answers and `collect` fails
the run over any file there it did not ask for (`unknown`) — so a fan-out that
derives a sibling of `result_path` blocks the very day it was meant to make
tractable.

Patches are **not** in the manifest — subagents read them with `git show`.

**5b. What each day is held to.** Every manifest lists
`required_commit_file_pairs`: each (commit, file) the day touched, flagged
`required` or not. **Required means the day's analysis must account for that
file** — naming it in a work item's `files[]` is enough; an `evidence[]` citation
is stronger but not demanded. Only source files are required; docs, config, CI,
tests, binaries and deleted files are listed but excused (a deleted file is gone
from that commit's tree, so it *cannot* be cited).

A required file the analysis never mentions fails the day at §5d. This is not
pedantry: a file that was changed but never described may never have been read,
and that is invisible in a result that otherwise looks confident.

**5c. Spawn one Day Subagent** per day, passing its `manifest_path` **and its
`result_path`**. The subagent must read the real diffs and enough code context,
determine the **end-of-day state** (a feature added then reverted the same day is
*not* a live change), and **write** the structured JSON in
`references/subagent-contract.md` to that path, replying only `DONE` — results
are never passed back as reply text, which drops and truncates them
(`references/subagent-contract.md` §6a). It must not write to the worklog. Days
with no commits still write `has_changes:false`.

**5d. Collect every day's result** (deterministic), once all subagents finish:

```
python3 -m git_worklog analyze collect --run-id <run_id> --repo <root>
```

Nothing here names a date or a language: `collect` reads the run's own
manifests, so a day cannot be dropped from the check by being left off a command
line. It reports:

- `complete` / `degraded` / `missing` / `invalid` / `unknown` / `failed_dates`,
  `results` (date → object), `partial_run`, `escalation_suggested_dates`.
- **A date in `missing` or `invalid` is a failed day, not an empty one** — never
  treat it as "nothing happened" and never fall back to its commit messages.
- `unknown` is a result file the run never asked for. Do not merge it.
- `partial_run:true` blocks apply by default (§9). Exit code is `1`.

Three things every result is checked against, and each fails the day:

- **Language** — the tag must be the one its manifest asked for.
- **Evidence accuracy** — every entry is checked against the tree of the commit
  it cites: the commit exists, the file existed *at that commit*, the `symbol`
  appears in it, the `lines` range is inside it. A subagent that cites
  `migrate_directory` for a function called `parse_legacy` has told you nothing
  you can follow (#15). On a shallow clone unreachable commits report
  `EVIDENCE_UNVERIFIABLE` rather than failing the day — that is the runner's
  clone depth, not the subagent's fault.
- **Coverage** — every required file (§5b) is mentioned somewhere.
  `COVERAGE_INCOMPLETE` names exactly which were not.

If a day fails, fix the analysis — re-run that day's subagent against the same
manifest and let it write its `result_path` again, then collect once more. Do
**not** work around a failure by editing the result file by hand, and never
paper over a gap with commit messages.

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

Pass `--include-uncommitted` to `analyze prepare` (§5a). It classifies
`staged` / `unstaged` / `untracked` (binary-aware), puts them on **today's**
manifest as `uncommitted_changes[]`, and returns a `worktree_fingerprint` on the
run.

Uncommitted content is attributed to **today only** — never spread across
historical days, because a file's mtime says when it was last written, not when
the work happened. In a multi-day range only today's task carries it; if today
is outside the range, prepare warns `UNCOMMITTED_NOT_IN_RANGE` rather than
silently dropping it. Present it in its own `### 尚未提交的異動` section, split
into staged / unstaged / untracked, and never describe it as committed.

`scripts/inspect_worktree.py --repo <root>` still exists if you need the
worktree on its own.

---

## 7. Merge results, generate Markdown, dry-run

1. Merge the per-day results from `analyze collect` (§5d). If it reports any
   `missing`, `invalid`, `degraded` or `unknown` date — i.e. `partial_run` —
   mark the run **partial** and default to blocking apply (see error handling
   below).
2. Render each day's GENERATED Markdown from the day template in
   `references/worklog-format.md`, **in the run's resolved language** (§2a) —
   headings included. Omit empty sections — no walls of "無/N/A". Days with no
   changes get no file by default. Lead each day's summary with its single most
   useful sentence and **bracket it in SUMMARY markers**; that line becomes the
   index row, and without the markers a day written in anything but Traditional
   Chinese gets a blank one.
3. Hand the rendered days to `preview`, which freezes everything the apply will
   write. Pass only dates that actually have content:

```
git-worklog preview --run-id <run_id> --repo <root> <<'JSON'
{"entries": {"2026-07-15": {"generated_markdown": "..."}, "...": {...}}}
JSON
```

This is the **only** point at which your prose enters the tool. `preview`
re-runs `collect`'s verdict (a partial run is refused, `RUN_NOT_COLLECTED`),
plans each date as `create` / `overwrite` / `no_change` preserving MANUAL,
rebuilds the index over the pending summaries, and stores the complete final
text of every target file on the record. It returns `preview_id`, `files[]`
(path + action + sha256), `previews` (full per-day file text), `index_preview`,
`language`, `expires_at`, and `not_written` (dates the run analysed that get no
file). Nothing is written and `.git-worklog/` is not created.

A day the run never analysed is refused (`UNKNOWN_DATE`) — do not work around it
by rendering it anyway; prepare a run that covers it. A corrupt existing day file
aborts with `CORRUPT_MARKERS`, a corrupt `index.md` with `INDEX_CORRUPT_MARKERS`
— never guess a repair.

The record also fixes the **language**. A user who confirms a zh-TW preview and
then asks for English is asking for a **different worklog**, not the same one
rendered differently: build and confirm a new preview rather than applying the
old payload under a new language (§6.2.10).

Previews expire after 24h (`--ttl-seconds` to change it). `git-worklog preview
--show <id> --check` reports a stored preview's state; `--cancel <id>` retires
one the user decided against.

4. Show the user the dry-run summary described in
   `references/interaction-flow.md`: repository root, branch, HEAD, timezone,
   requested mode, resolved range, `include_uncommitted`, the **output
   language** (and, when it came from `fallback`, say so — the user may want to
   correct it before anything is written), the **subagent configuration** (provider, model, reasoning effort, automatic escalation:
   disabled), per-day commit counts and status, files analyzed, per-date planned
   action (create / overwrite / no-change), the index rebuild, preserved MANUAL
   dates, each day file's full preview, the index preview, the target directory
   `.git-worklog/`, the `preview_id`, and the line
   **"No files have been modified."**

   If `not_written` is non-empty, say which dates and why (no changes that day).
   A day the user expected and does not get is not something to discover after
   the write.

---

## 8. Apply only after explicit confirmation

Natural-language confirmations ("寫入", "確認更新", "套用剛才的預覽", "把這份寫進去")
or `apply <preview_id>`.

1. Apply the preview. This is the whole step:

```
git-worklog apply --preview-id <preview_id>
```

   **Do not pass the day content again — there is nowhere to pass it.** The
   record holds the exact bytes the user just approved, and apply writes those.
   Re-rendering, re-reading the results, or re-dispatching a subagent at this
   point would produce a worklog nobody previewed, which is precisely what the
   record exists to prevent.

   Apply re-checks the world first: repository identity, git dir, branch, HEAD,
   submodules, the working tree (when the run read it), every target day file,
   `index.md`, the day-file listing, the run's manifests and results, and the
   project's language settings. It writes the day files as one transaction
   (staged, validated, atomically swapped, rolled back on any failure), then
   rebuilds the index. `.git-worklog/` is created now if it was missing. No git
   add / commit / push.

2. On a refusal, **do not write.** Report the code and build a fresh preview:

   | Code | Meaning |
   |---|---|
   | `PREVIEW_STALE` | Something moved since the preview; `mismatches[]` names what. |
   | `PREVIEW_EXPIRED` | Past its TTL. |
   | `PREVIEW_ALREADY_APPLIED` | Spent. Never re-apply. |
   | `PREVIEW_CANCELLED` | The user retired it. |
   | `PREVIEW_FAILED` | An earlier apply failed and rolled back. Not retryable. |
   | `PREVIEW_INTERRUPTED` | An apply died mid-write; whether it wrote is unknown. Check `.git-worklog/` before doing anything else. |
   | `APPLY_LOCKED` | Another apply is writing to this worklog. Wait. |
   | `INDEX_WRITE_FAILED` | The day files **were** written; only `index.md` was not. No data is lost — repair with `rebuild_worklog_index.py --dir .git-worklog --apply`. |

3. Confirm with `validate_daily_worklog.py --dir .git-worklog` and
   `validate_worklog_index.py --dir .git-worklog`, then report the actual
   update from apply's own output: `written_dates`, `preserved_manual_dates`,
   `index_action`, and the target directory.

---

## 9. Error handling (summary)

- **Not a Git repo / >30 days / corrupt markers / non-UTF-8:** stop, report,
  never auto-repair. `preview` refuses a corrupt target day file
  (`CORRUPT_MARKERS`) and a corrupt `index.md` (`INDEX_CORRUPT_MARKERS`) so its
  MANUAL is never lost; the validators list every issue.
- **Unreadable code (permissions, missing submodule):** record what was not
  analyzed, lower `confidence`, note it in `uncertainties`; never fake analysis.
- **A day's subagent failed:** keep other days, mark the run partial, block apply
  by default — `preview` refuses a partial run outright (`RUN_NOT_COLLECTED`).
  The user may choose to write only the successful days; that means a run
  prepared for just those dates, not a preview that quietly leaves days out.
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

The analysis pipeline is driven by the CLI (§5). Run it as
`python3 -m git_worklog <command>` from this directory — no install needed, same
as the scripts:

| Command | Role |
|---------|------|
| `analyze prepare` | Mint a run; write one manifest per day (what to analyse, in which language, which files are required, where to write the result) |
| `analyze collect` | Read the run's results back and check them: schema, language, evidence accuracy, coverage, missing/unknown days |
| `preview` | Freeze the apply: store every target file's final text, the fingerprints, the language and a TTL. Returns a `preview_id`. Writes nothing |
| `apply` | Write that stored payload after re-checking the world. Takes a `preview_id` and nothing else |
| `doctor` | Is this environment able to run the tool? |
| `validate` | Is the worklog on disk well-formed? |
| `version` | CLI / layout / config-schema versions |

The rest of the deterministic work is still scripts:

| Script | Role |
|--------|------|
| `resolve_provider_model.py` | Resolve the per-host subagent provider/model (single source `config/provider_models.json`; overrides, escalation, halt-and-ask) |
| `resolve_date_range.py` | Parse/validate dates, timezone, day-span limit (`--max-days`, default 30), per-day bounds |
| `resolve_ref_range.py` | Report mode: resolve a tag/ref to its authoritative commit set + derived dates |
| `check_worklog_coverage.py` | Report mode: per-date coverage — `covered` / `gap` / `no-commits` |
| `collect_git_history.py` | Repo metadata + per-day commit facts (no summaries, no author filter); `--info-only` for §4 |
| `inspect_worktree.py` | Staged/unstaged/untracked + worktree fingerprint on its own (`analyze prepare --include-uncommitted` does this for a run) |
| `build_analysis_manifest.py` | One day's manifest from history JSON on stdin (`analyze prepare` does this for a whole range) |
| `collect_day_results.py` | Read back and validate results in a flat run dir. Cannot check coverage — it never sees a manifest, so it does not pretend to. Prefer `analyze collect` |
| `update_daily_worklog.py` | Simulate/apply per-day files outside a run. `preview`/`apply` do this for a real run, and only they freeze the payload — prefer them |
| `rebuild_worklog_index.py` | Rebuild `index.md` from day files (descending, summaries); preserve index MANUAL; atomic write. Also the repair for `INDEX_WRITE_FAILED` |
| `validate_daily_worklog.py` | Per-day file marker/title/UTF-8 validation |
| `validate_worklog_index.py` | Index marker/order/link/UTF-8 validation |
| `migrate_legacy_worklog.py` | One-time migration of a legacy worklog (flat `PROJECT_WORKLOG/`, or the single `docs/PROJECT_WORKLOG.md`) into `.git-worklog/` |
| `worklog_markers.py` | Shared day/index parser/serialiser (imported by the scripts above) |
