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
integrity) is done by the `git-worklog` CLI. The judgement work (reading code,
deciding what actually changed, writing the summary) is done by you and per-day
subagents. **Never let commit messages stand in for reading the diff.**

`python3` and `git` must be available; nothing needs installing. Run every
command as `python3 -m git_worklog <command>` from this directory. Each prints
one JSON object to stdout. Exit `0` means ok, `1` means it ran and found a
problem, `2` means it could not run.

**Use the CLI, never `scripts/`.** The `scripts/` directory holds thin
command-line shells over the same engine, kept for anyone who scripted against
them; they are not part of your flow and calling them will not give you a run id,
a preview, or anything you can apply.

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
which hands back to §§2–6 for just those dates, dry-run and confirmation
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
request that asks for it (§6.2.11) — pass it to `report --language`, resolved the
same way as §2a. Reading zh-TW day files and producing an English release note is
correct and needs no conversion of anything on disk.

**Never translate**: file paths, code symbols, commit hashes, API/class/package
names, branch and issue references, or anything in `evidence[]`. Explaining a
term in the output language is welcome; renaming it is not.

---

## 3. Per-day analysis — one Day Subagent per day

Two commands bracket this section. `analyze prepare` decides **what** must be
analysed and **in which language**; `analyze collect` decides whether to
**believe** what came back. Everything between them — reading the patches,
understanding the code, writing the prose — is yours and the subagents'. The CLI
never does it and needs no model API key.

The field-by-field detail of what these commands emit — every output key, the
manifest contents, the error codes, the check mechanics — is in
`references/analysis-pipeline.md`. What must reach you *inline* is here.

**3a. Prepare the run** (deterministic, once for the whole range):

```
python3 -m git_worklog analyze prepare <range> --repo <root> \
    --host <anthropic|openai|google> \
    --language <tag|auto> --language-source <source> \
    [--timezone <IANA>] [--include-uncommitted]
```

- **`<range>` is what the user said, verbatim** — a shortcut like `7d`, or
  `--date` / `--days` / `--from`+`--to`. Do **not** resolve the dates yourself;
  prepare answers, and its `range` block is what you show in the dry-run.
- **Do not compute a model.** `--host` is the host you run under (Claude Code →
  `anthropic`, Codex → `openai`, Gemini → `google`); prepare resolves the model
  from the packaged config. Never guess the host; if you cannot tell, ask. If the
  model is unavailable it returns `MODEL_UNAVAILABLE` — **halt and ask**, never
  silently pick another. Details: `references/provider-models.md`.
- **`--language` / `--language-source` come from §2a**, resolved once and stamped
  on every manifest — a day whose result disagrees blocks the whole run.
- Keep `run_id`, `run_dir`, and every `tasks[]` entry (each carries a
  `manifest_path` and a `result_path`).
- On `ok:false`, report the error and stop (code table:
  `references/analysis-pipeline.md` §3).

**A `LARGE_DAY` warning means stop and ask before dispatching that day.** It
carries the commit, file and group counts and the resolved model, because one
subagent on the cheap model may not hold a big day. Offer to fan it out into the
recommended Code Analysis Subagents, to escalate the model (`--host … --escalate`,
redo `prepare`), or to proceed as-is — the same surface-and-ask you use for a gap
or an over-30-day range. Do not decide silently: a large day that ran anyway on
the cheap model is exactly the run that came back confidently wrong and nothing
noticed. The counts are there so a 60-file day and a 26-file one are not the same
question; proceeding is a legitimate answer, an unasked question is not.

**3b. Coverage.** Each manifest's `required_commit_file_pairs` marks the source
files the day's analysis **must account for** — naming one in a work item's
`files[]` is enough. A required file the result never mentions fails the day at
collect: a file changed but never described may never have been read, and that is
invisible in a result that otherwise looks confident. Which files are required,
and why deletions and non-source files are excused:
`references/analysis-pipeline.md` §5.

**3c. Spawn one Day Subagent** per day, passing its `manifest_path` **and its
`result_path`**. The subagent reads the real diffs and enough code context,
determines the **end-of-day state** (a feature added then reverted the same day
is *not* a live change), and **writes** the structured JSON from
`references/subagent-contract.md` to that path, replying only `DONE` — results
are never passed back as reply text, which drops and truncates them (§6a). It
must not write to the worklog. Days with no commits still write
`has_changes:false`. A large day may fan out into Code Analysis Subagents grouped
by work area, each writing to the manifest's `parts_dir` — never beside
`result_path`, where `collect` would fail the run over it
(`references/subagent-contract.md`).

**3d. Collect every day's result** (deterministic), once all subagents finish:

```
python3 -m git_worklog analyze collect --run-id <run_id> --repo <root>
```

It reads the run's own manifests, so no day can be dropped by being left off a
command line. Every result is checked three ways — **language**, **evidence and
prose symbols against the day's tree**, and **required-file coverage** — and each
failure fails the day. `partial_run:true` (any `missing`, `invalid`, `degraded`
or `unknown` date) blocks apply and exits `1`.

A failed or missing day is **not** an empty one: never treat it as "nothing
happened", never fall back to commit messages. Fix a failure by **re-running that
day's subagent** against the same manifest, then collecting again — never
hand-edit a result, never paper over a gap. Output fields and the check
mechanics: `references/analysis-pipeline.md` §6.

Model per host is resolved by `--host` (cost-first defaults, single source
`git_worklog/data/provider_models.json`); read `model` back off prepare's output.
Overrides, the per-host table, unavailable-model handling and opt-in escalation:
`references/provider-models.md`.

---

## 4. Uncommitted changes (only when include_uncommitted=true)

Pass `--include-uncommitted` to `analyze prepare` (§3a). It classifies
`staged` / `unstaged` / `untracked` (binary-aware), puts them on **today's**
manifest as `uncommitted_changes[]`, and returns a `worktree_fingerprint` on the
run.

Uncommitted content is attributed to **today only** — never spread across
historical days, because a file's mtime says when it was last written, not when
the work happened. In a multi-day range only today's task carries it; if today
is outside the range, prepare warns `UNCOMMITTED_NOT_IN_RANGE` rather than
silently dropping it. Present it in its own `### 尚未提交的異動` section, split
into staged / unstaged / untracked, and never describe it as committed.

---

## 5. Merge results, generate Markdown, dry-run

1. Merge the per-day results from `analyze collect` (§3d). If it reports any
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

4. Show the dry-run summary (`references/interaction-flow.md` has the full
   layout). The load-bearing parts: the resolved range and timezone; the output
   **language** — and if it came from `fallback`, say so, because the user may
   want to correct it before anything is written; the subagent provider/model;
   each date's planned action (create / overwrite / no-change); the preserved
   MANUAL dates; every day file's full preview and the index preview; the
   `preview_id`; and the line **"No files have been modified."** If `not_written`
   is non-empty, say which dates and why — a day the user expected and does not
   get should not be discovered after the write.

---

## 6. Apply only after explicit confirmation

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
   | `INDEX_WRITE_FAILED` | The day files **were** written; only `index.md` was not. No data is lost — the index is a pure function of the day files, so repair it with `python3 -m git_worklog reindex --apply`. |

3. Confirm with `python3 -m git_worklog validate`, then report the actual
   update from apply's own output: `written_dates`, `preserved_manual_dates`,
   `index_action`, and the target directory.

---

## 7. Error handling (summary)

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
  `python3 -m git_worklog migrate` (dry-run + confirm); it never deletes the
  source. Two shapes qualify — a flat `PROJECT_WORKLOG/` directory
  (`--from-dir`) and the single `docs/PROJECT_WORKLOG.md` (`--from-file`). With
  neither flag it auto-detects, directory first.
- **Writing refused with `LEGACY_LAYOUT`:** the target directory still holds its
  day files at the root rather than under `days/`. Do not work around it by
  passing a different `--dir` — offer the migration. Reading a legacy directory
  (validate, coverage, report mode) keeps working untouched.

Full rules: `references/interaction-flow.md`, `references/code-analysis-rules.md`.

---

## Reference & command map

| Need | Read |
|------|------|
| `analyze prepare` / `collect` output fields, manifest contents, error codes, check mechanics | `references/analysis-pipeline.md` |
| Report mode: scope (dates vs refs), coverage, gaps, scenarios | `references/report-mode.md` |
| Menu, options, dry-run summary, confirmation, apply | `references/interaction-flow.md` |
| Date modes, timezone, 30-day limit, NL normalisation | `references/date-parameter-contract.md` |
| Diff reading, context expansion, final-state, merge/revert/rename/binary/lockfile/submodule | `references/code-analysis-rules.md` |
| Day/Code-Analysis subagent prompts, return schema, confidence, evidence | `references/subagent-contract.md` |
| Directory layout, day/index markers, create/overwrite, migration | `references/worklog-format.md` |
| Per-host models, overrides, unavailable-model handling, escalation | `references/provider-models.md` |

Every deterministic step is a CLI command (§3). Run each as
`python3 -m git_worklog <command>` from this directory — no install needed. This
is the whole surface you need; there is nothing in `scripts/` that is not here.

| Command | Role |
|---------|------|
| `analyze prepare` | Mint a run: resolve the range, timezone and model, then write one manifest per day (what to analyse, in which language, which files are required, where to write the result) |
| `analyze collect` | Read the run's results back and check them: schema, language, evidence accuracy, coverage, missing/unknown days |
| `preview` | Freeze the apply: store every target file's final text, the fingerprints, the language and a TTL. Returns a `preview_id`. Writes nothing |
| `apply` | Write that stored payload after re-checking the world. Takes a `preview_id` and nothing else |
| `report` | Report mode's entry point: resolve the scope (dates or a tag's commit set), check coverage, reconcile the tag against the day files, resolve the output language. Writes nothing — the prose is yours |
| `coverage` | Per-date `covered` / `gap` / `no-commits`. Exit `1` means a gap — real work nothing has analysed. A primitive `report` composes |
| `refs` | Resolve a tag/ref to its authoritative commit set + derived dates. A primitive `report` composes; `--list-tags` lists what exists |
| `migrate` | One-time migration of a legacy worklog (flat `PROJECT_WORKLOG/`, or the single `docs/PROJECT_WORKLOG.md`) into `.git-worklog/`. Dry-run unless `--apply` |
| `reindex` | Rebuild `index.md` from the day files. Normal runs never need it — `apply` does it — but it is the repair for `INDEX_WRITE_FAILED` |
| `doctor` | Is this environment able to run the tool? |
| `validate` | Is the worklog on disk well-formed? Day markers, index links, config, language stamps |
| `version` | CLI / layout / config-schema versions |
