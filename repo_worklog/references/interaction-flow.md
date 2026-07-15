# Interaction flow

How the skill turns a `/repo_worklog` invocation (menu pick, natural language, or
direct parameters) into a validated request, a dry-run preview, and finally an
apply. The deterministic work is done by `scripts/`; every script is run as
`python3 scripts/<name>.py` and prints one JSON object with `ok:true`/`ok:false`.

This file covers the menu, option numbers, natural-language and direct-parameter
entry, the dry-run summary, confirmation, apply-time re-verification, and partial
failure. Date normalisation detail lives in `references/date-parameter-contract.md`.

---

## 1. No-argument menu (hard stop)

When `/repo_worklog` is invoked with **no usable arguments**, print this menu
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
/repo_worklog date=2026-07-01
/repo_worklog days=7
/repo_worklog 7d
/repo_worklog 30d
/repo_worklog from=2026-07-01 to=2026-07-10
/repo_worklog date=2026-07-15 include_uncommitted=true
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
results and simulating the update with `update_worklog.py` (no `--apply`), show
the user a summary that includes at least all of the following fields:

- repository root
- current branch
- HEAD commit
- timezone
- requested date mode
- resolved date range
- `include_uncommitted` status
- per-day commit counts
- per-day analysis status
- number of files analyzed
- dates to insert
- dates to overwrite
- no-change days
- preserved MANUAL sections
- the full worklog preview (from `preview_content`)
- target file path (default `docs/PROJECT_WORKLOG.md`)
- the `preview_id`
- the line **`No files have been modified.`**

The `dry-run` output of `update_worklog.py` supplies the mechanical fields:
`mode`, `target`, `target_exists`, `target_dir_exists`, `planned_changes`
(each `{date, action:"insert"|"overwrite", manual_preserved}`),
`preserved_manual_dates`, `original_sha256`, `preview_sha256`, `preview_content`,
`diff`, and `note` (`"No files have been modified."`). The `preview_id` comes
from `preview_state.py create`, formatted `rw-YYYYMMDD-<6 hex>`.

A compact rendering (fuller field list above; per-day counts, files analyzed,
no-change days, preserved MANUAL, and the full preview are shown too):

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

Target:
docs/PROJECT_WORKLOG.md

Planned changes:
- 2026-07-14: overwrite generated section
- 2026-07-15: insert new entry

No files have been modified.

Preview ID:
rw-20260715-a81f2c
```

The dry-run never creates `docs/` and never writes anything. The skill never runs
`git add` / `commit` / `push`.

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
worklog, then verify the preview:

```
python3 scripts/preview_state.py verify --id <preview_id> --mark-applied <<'JSON'
{"repository": {"root": "...", "branch": "...", "head": "...",
                "worktree_fingerprint": "..."},
 "worklog": {"original_sha256": "<current hash>"},
 "params": {"include_uncommitted": false}}
JSON
```

`verify` returns `consistent` (bool), `mismatches[{field,expected,actual}]`,
`already_applied`, `expired`, `age_seconds`, and `reason`, and exits with code `3`
when inconsistent. Consistency is checked across: repository root, branch, HEAD,
working-tree fingerprint, the worklog's original content hash, and
`include_uncommitted`. The default TTL is 24 hours. Preview state lives in
`~/.repo_worklog/previews/`, outside the repo.

If `consistent:false` (exit 3) — including `already_applied`, `expired`, or any
state change since the dry-run — **do not write.** Explain the reason to the user
and re-run the dry-run to mint a fresh preview. Never apply a stale preview.

When `consistent:true`, apply the same entries with `--apply`:

```
python3 scripts/update_worklog.py --target docs/PROJECT_WORKLOG.md --apply <<'JSON'
{"entries": { ... same entries as the dry-run ... }}
JSON
```

Apply output adds `written_sha256` and `final_dates`. `docs/` is created now if it
was missing. Then validate and report the actual update.

---

## 9. Partial failure

If any day's subagent failed, mark the run **partial** and **block apply by
default** — do not substitute commit messages for the missing analysis, and keep
the days that did succeed.

The user may explicitly choose to write only the successful days. That is a new
request, not a resumed one: re-run the dry-run with only those dates, which
produces a **new `preview_id`**. Show the updated planned changes and the new
preview id, and require confirmation again before applying.
