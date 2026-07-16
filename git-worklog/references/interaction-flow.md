# Interaction flow

How the skill turns a `/git-worklog` invocation (menu pick, natural language, or
direct parameters) into a validated request, a dry-run preview, and finally an
apply. The deterministic work is done by `scripts/`; every script is run as
`python3 scripts/<name>.py` and prints one JSON object with `ok:true`/`ok:false`.

This file covers the menu, option numbers, natural-language and direct-parameter
entry, the dry-run summary, confirmation, apply-time re-verification, and partial
failure. Date normalisation detail lives in `references/date-parameter-contract.md`.

**Scope: this file is generation mode.** A request for an *answer* from the
history rather than for worklog files — 「整理上一週工作摘要」, 「整理 v1.0.1
CHANGELOG」 — is report mode: it is read-only, has no menu, no dry-run and no
`preview_id`, and is specified in `references/report-mode.md`. Route first
(`SKILL.md` §1a). The only place the two meet is §10 below, where report mode
hands a gap back here to be filled.

---

## 1. No-argument menu (hard stop)

When `/git-worklog` is invoked with **no usable arguments**, print this menu
**verbatim** and wait. Do nothing else.

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

With no arguments the skill **only prints the menu and waits**. It must NOT, at
this point:

- scan Git history (no `collect_git_history.py`, not even `--info-only`),
- spawn any Day Subagent,
- run `resolve_date_range.py` or any other script,
- generate a worklog preview,
- create or modify any file.

Analysis begins only after the user picks a range (option number, natural
language, or direct parameters).

---

## 2. Option-number handling

After the menu, a bare option number is a valid reply. Map it to canonical
parameters. Options 2, 5, and 7 need one follow-up question before you have a
complete request.

| Input | Meaning | Canonical parameters | Follow-up |
|-------|---------|----------------------|-----------|
| `1` | 今天 | `date=<local today>` | — |
| `2` | 指定日期 | `date=<asked>` | ask, then resolve |
| `3` | 最近 7 天 | `days=7` | — |
| `4` | 最近 30 天 | `days=30` | — |
| `5` | 自訂日期範圍 | `from=<asked> to=<asked>` | ask, then resolve |
| `6` | 今天，並包含尚未提交的異動 | `date=<local today> include_uncommitted=true` | — |
| `7` | 自訂日期或範圍，並包含尚未提交的異動 | `date=…` or `from=… to=…`, plus `include_uncommitted=true` | ask, then resolve |

For option `2`, ask verbatim:

```
請輸入要整理的日期，例如 2026-07-01。
```

For option `5`, ask verbatim:

```
請輸入起始與結束日期，例如 2026-07-01 到 2026-07-10。
```

For option `7`, ask for a date **or** a range (reuse the option-2 prompt for a
single day or the option-5 prompt for a range, depending on what the user is
providing), then set `include_uncommitted=true` on top of the resolved
parameters.

Once you have complete parameters, continue to validation (section 5).

---

## 3. Natural-language driving

The user may skip option numbers and describe the range in prose. You (the model)
convert prose to canonical parameters; the scripts never parse free text. Common
phrasings and their normalisation:

| User says | Canonical parameters |
|-----------|----------------------|
| 整理今天 | `date=<local today>`, `include_uncommitted=false` |
| 整理最近一週 | `days=7`, `include_uncommitted=false` |
| 幫我補 2026 年 7 月 1 日 | `date=2026-07-01`, `include_uncommitted=false` |
| 整理 7 月 1 日到 7 月 10 日 | `from=2026-07-01 to=2026-07-10`, `include_uncommitted=false` |
| 今天含未提交 | `date=<local today>`, `include_uncommitted=true` |
| 整理近 30 天但不要 working tree | `days=30`, `include_uncommitted=false` |

"近一個月" always resolves to `days=30`, never the actual month length.
`date` / `days` / `from`+`to` are mutually exclusive. Full case list:
`references/date-parameter-contract.md`.

---

## 4. Direct-parameter invocation (power users)

A user who already knows the interface can pass parameters on the invocation and
**skip the menu entirely**:

```
/git-worklog date=2026-07-01
/git-worklog days=7
/git-worklog 7d
/git-worklog 30d
/git-worklog from=2026-07-01 to=2026-07-10
/git-worklog date=2026-07-15 include_uncommitted=true
```

When valid parameters are present on the invocation, do not print the menu — go
straight to validation and analysis. `7d` / `30d` are positional shortcuts;
`date=`, `days=`, `from=`/`to=`, and `include_uncommitted=` are the keyed forms.

---

## 5. Validate, then analyse

Whatever the entry path, normalise to canonical parameters and validate the range
first:

```
python3 scripts/resolve_date_range.py <args> [--timezone <IANA>] [--today <YYYY-MM-DD>]
```

Pass the resolved parameters as flags — `--date`, `--days`, `--from`/`--to`, a
positional shortcut (`7d` | `30d` | `YYYY-MM-DD`), and `--include-uncommitted`
when requested. On `ok:false`, report the error and stop; do not start any
subagent. Error codes include `NO_DATE_SPEC`, `ARG_CONFLICT`,
`DAYS_OUT_OF_RANGE`, `INVALID_DATE`, `FROM_AFTER_TO`, `FROM_WITHOUT_TO`,
`TO_WITHOUT_FROM`, and `TOO_MANY_DAYS` (which reports `requested_days` and
`max_days` — show the requested count and ask the user to narrow). Detail:
`references/date-parameter-contract.md`.

On `ok:true`, proceed to repository detection and per-day analysis (SKILL.md
sections 4–7), which always ends in a dry-run.

---

## 6. Dry-run summary

Every valid request produces a **preview only**. After merging the per-day
results, simulate the day-file writes with `update_daily_worklog.py` and the
index rebuild with `rebuild_worklog_index.py` (both without `--apply`), then show
the user a summary that includes at least all of the following fields:

- repository root
- current branch
- HEAD commit
- timezone
- requested date mode
- resolved date range
- `include_uncommitted` status
- subagent configuration (provider, model, model id, reasoning effort, automatic
  escalation: disabled) — see below
- per-day commit counts
- per-day analysis status
- number of files analyzed
- per-date planned action (create / overwrite / no-change)
- the index rebuild
- preserved MANUAL sections (day files and the index)
- each day file's full preview (from `previews`) and the index preview
- target directory (default `PROJECT_WORKLOG/`)
- the `preview_id`
- the line **`No files have been modified.`**

**Subagent configuration block.** Resolved once by
`resolve_provider_model.py --host <key>`. When every day uses the same model,
show it once:

```
Subagent configuration:
- Provider: OpenAI
- Model: GPT-5.6 Luna
- Model ID: gpt-5.6-luna
- Reasoning effort: low
- Automatic escalation: disabled
```

Omit the `Reasoning effort` line for providers that have none (anthropic /
google). If the user approved escalation for some dates, list per date instead:

```
Subagent configuration:
- 2026-07-13: GPT-5.6 Luna
- 2026-07-14: GPT-5.6 Terra (user-approved escalation)
- 2026-07-15: GPT-5.6 Luna
```

`update_daily_worklog.py` (dry-run) supplies: `worklog_dir`, `dir_exists`,
`planned_changes` (each `{date, path, action:"create"|"overwrite"|"no_change",
manual_preserved}`), `summaries` (one-line index summary per date), `previews`
(full per-day file text), `preserved_manual_dates`, and `file_hashes`.
`rebuild_worklog_index.py` (dry-run, given `{"overrides": summaries}`) supplies
the index `preview`, the descending `dates`, `index_hash`, and
`preserved_index_manual`. The `preview_id` comes from `preview_state.py create`,
formatted `rw-YYYYMMDD-<6 hex>`.

A compact rendering (fuller field list above; per-day counts, files analyzed,
preserved MANUAL, and the full per-file previews are shown too):

```
Dry-run completed.

Repository:
<repository-root>

Branch:
main

HEAD:
abc1234

Timezone:
Asia/Taipei

Range:
2026-07-09 to 2026-07-15

Target directory:
PROJECT_WORKLOG/

Planned changes:
- PROJECT_WORKLOG/2026-07-13.md: no changes
- PROJECT_WORKLOG/2026-07-14.md: overwrite generated section
- PROJECT_WORKLOG/2026-07-15.md: create new file
- PROJECT_WORKLOG/index.md: rebuild generated index

Preserved manual sections:
- PROJECT_WORKLOG/2026-07-14.md
- PROJECT_WORKLOG/index.md

No files have been modified.

Preview ID:
rw-20260715-a81f2c
```

The dry-run never creates `PROJECT_WORKLOG/` and never writes anything. The skill
never runs `git add` / `commit` / `push`.

---

## 7. Confirmation to apply

An apply happens **only after explicit confirmation**. Accept either natural
language or the keyed form:

- natural language: `寫入`, `確認更新`, `套用剛才的預覽`, `把這份寫進去`
- keyed: `apply rw-YYYYMMDD-xxxxxx` (the `preview_id` from the dry-run)

Anything ambiguous is not a confirmation — re-show or clarify rather than write.

---

## 8. Apply-time re-verification

On confirmation, before writing, re-detect repository state, re-run
`inspect_worktree.py` if `include_uncommitted` is set, re-hash the current
`index.md` and each target day file, re-fingerprint the day-file listing, then
verify the preview:

```
python3 scripts/preview_state.py verify --id <preview_id> --mark-applied <<'JSON'
{"repository": {"root": "...", "branch": "...", "head": "...",
                "worktree_fingerprint": "..."},
 "worklog": {"index_sha256": "<current or 'missing'>",
             "day_files": {"2026-07-15": "<current or 'missing'>"},
             "dir_fingerprint": "<current>"},
 "params": {"timezone": "...", "include_uncommitted": false}}
JSON
```

`verify` returns `consistent` (bool), `mismatches[{field,expected,actual}]`,
`already_applied`, `expired`, `age_seconds`, and `reason`, and exits with code `3`
when inconsistent. Consistency is checked across: repository root, branch, HEAD,
working-tree fingerprint, `index.md` content, each target day file, the day-file
listing, timezone, and `include_uncommitted` — so a changed target day file, a
changed `index.md`, or an added/removed day file all invalidate the preview. The
default TTL is 24 hours. Preview state lives in `~/.repo_worklog/previews/`,
outside the repo.

If `consistent:false` (exit 3) — including `already_applied`, `expired`, or any
state change since the dry-run — **do not write.** Explain the reason to the user
and re-run the dry-run to mint a fresh preview. Never apply a stale preview.

When `consistent:true`, apply the same entries, then rebuild the index:

```
python3 scripts/update_daily_worklog.py --dir PROJECT_WORKLOG --apply <<'JSON'
{"meta": { ... same meta ... }, "entries": { ... same entries as the dry-run ... }}
JSON
python3 scripts/rebuild_worklog_index.py --dir PROJECT_WORKLOG --apply
```

The day-file apply is one all-or-nothing transaction (`written_dates` in its
output); `rebuild_worklog_index.py --apply` then writes `index.md` atomically.
`PROJECT_WORKLOG/` is created now if it was missing. Then run
`validate_daily_worklog.py --dir PROJECT_WORKLOG` and
`validate_worklog_index.py --dir PROJECT_WORKLOG` and report the actual update.

---

## 9. Partial failure

If any day's subagent failed, mark the run **partial** and **block apply by
default** — do not substitute commit messages for the missing analysis, and keep
the days that did succeed.

The user may explicitly choose to write only the successful days. That is a new
request, not a resumed one: re-run the dry-run with only those dates, which
produces a **new `preview_id`**. Show the updated planned changes and the new
preview id, and require confirmation again before applying.

---

## 10. Backfill requested by report mode

Report mode calls in here when the range it was asked to report on contains dates
that have commits but no day file, and the user chose to fill them
(`references/report-mode.md` §4). Nothing about this flow is special-cased:

1. The gap dates are the range. Run §§5–8 over **only those dates** — the same
   validation, per-day subagents, dry-run summary, `preview_id`, and explicit
   confirmation as any other generation run. A report request is **not** a
   confirmation to write.
2. The 30-day cap applies, because this spawns subagents. More than 30 gap dates
   cannot be filled in one pass: say so and offer batches. Never quietly start a
   45-day run because the report's reading cap was 90.
3. After the apply, control returns to report mode, which re-reads the freshly
   written day files and produces the report.

If the user declines the backfill, **do not write anything** and do not treat the
report as blocked — report mode proceeds and marks the shallow dates instead.
