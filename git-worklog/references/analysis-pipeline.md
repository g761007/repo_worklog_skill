# The analysis pipeline: `prepare` and `collect` in detail

`SKILL.md` §3 drives the per-day pipeline and carries the rules you must act on
inline. This file is the field-by-field reference behind it: what `analyze
prepare` returns, what a manifest carries, what `analyze collect` reports, and
how a result is checked. Load it when you need a specific field or error code;
the rules that decide what you *do* live in `SKILL.md`, not here.

---

## 1. `analyze prepare` — the full output

```
python3 -m git_worklog analyze prepare <range> --repo <root> \
    --host <anthropic|openai|google> \
    --language <tag|auto> --language-source <source> \
    [--timezone <IANA>] [--include-uncommitted]
```

Top-level keys:

- `run_id`, `run_dir` — the run's identity and directory. Keep both.
- `tasks[]` — one entry per calendar day, each with `manifest_path` (what to
  analyse), `result_path` (where its analysis must be written), `has_changes`,
  `commit_count`, `changed_file_count`, `group_count`, `large_day`,
  `include_uncommitted`.
- `range` — `{mode, from, to, days_count, today}`: the days it actually picked.
  If the user said `7d`, this is which seven.
- `timezone` — `{resolved, source}`. When `source` is `system-offset` (no IANA
  name), tell the user which offset is assumed and offer to set one.
- `repository` — `root`, `branch` (null if `detached_head`), `head` /
  `short_head`, `has_commits`, `dirty_worktree`.
- `provider`, `model` — what every subagent in this run will use.
- `warnings[]` — see §2.

Empty repo (`has_commits:false`): with no `--include-uncommitted` there is
nothing to log — say so. With it, only the working tree is analysed.

Timezone priority and the half-open `[00:00, next 00:00)` day rule:
`date-parameter-contract.md`.

---

## 2. `warnings[]`

Surface every warning; never swallow one.

| Code | Meaning | What SKILL.md tells you to do |
|---|---|---|
| `DEPRECATED_ENV_VAR` | An old variable name chose the model. | Tell the user which knob did it. |
| `UNCOMMITTED_NOT_IN_RANGE` | Their uncommitted work had nowhere to go (today is outside the range). | Say it was left out. |
| `LARGE_DAY` | A day is big enough that one subagent on the resolved model may not hold it. Carries the commit / file / group counts and the model. | **Stop and ask** before dispatching — see `SKILL.md` §3. |

---

## 3. `ok:false` — error codes

Report the error's message and stop. The date and range codes are specified in
`date-parameter-contract.md`; the model codes in `provider-models.md`.

| Code | Note |
|---|---|
| `TOO_MANY_DAYS` | Show `requested_days`; ask the user to narrow. |
| `NO_DATE_SPEC` / `ARG_CONFLICT` / `DAYS_OUT_OF_RANGE` / `INVALID_DATE` / `FROM_AFTER_TO` / `TO_WITHOUT_FROM` | Malformed date request. |
| `INVALID_TIMEZONE` | Not an IANA name. |
| `UNKNOWN_HOST` | `--host` was not one of anthropic / openai / google, or could not be determined. |
| `MODEL_UNAVAILABLE` | Halt and ask — never silently pick another model. |
| `NOT_A_GIT_REPO` | Tell the user this directory is not a Git repository and stop. |

---

## 4. What a manifest carries

Each per-day manifest (`manifest_path`) gives:

- `file_groups` — the day's changed files grouped by real work area.
- `required_context` — the files a subagent should read for context.
- `analysis_rules` — the code-analysis rules, on the manifest so they reach the
  subagent (`references/subagent-contract.md`).
- `large_day` — true when the day is big enough to warrant a fan-out.
- `parts_dir` — where a fan-out's per-group parts go. **Never** beside the day's
  `result_path`: `results/` holds the run's answers and `collect` fails the run
  over any file there it did not ask for (`unknown`), so a fan-out that derives a
  sibling of `result_path` blocks the very day it was meant to make tractable.
- `required_commit_file_pairs` — see §5.
- authorship — `authors[]` (distinct names, first-appearance order) and
  `commits[].author_name`. Render the `參與者` line and each `相關 commits`
  entry's author from these directly; the subagent never returns attribution.

Patches are **not** in the manifest — subagents read them with `git show`.

---

## 5. `required_commit_file_pairs` — what each day is held to

Every manifest lists each (commit, file) the day touched, flagged `required` or
not. **Required means the day's analysis must account for that file** — naming it
in a work item's `files[]` is enough; an `evidence[]` citation is stronger but
not demanded. Only source files are required; docs, config, CI, tests, binaries
and deleted files are listed but excused (a deleted file is gone from that
commit's tree, so it *cannot* be cited).

A required file the analysis never mentions fails the day at collect. This is not
pedantry: a file that was changed but never described may never have been read,
and that is invisible in a result that otherwise looks confident.

---

## 6. `analyze collect` — the full output

```
python3 -m git_worklog analyze collect --run-id <run_id> --repo <root>
```

Nothing here names a date or a language: `collect` reads the run's own manifests,
so a day cannot be dropped from the check by being left off a command line.

- `complete` / `degraded` / `missing` / `invalid` / `unknown` / `failed_dates`,
  `results` (date → object), `partial_run`, `escalation_suggested_dates`.
- A date in `missing` or `invalid` is a **failed day, not an empty one** — never
  treat it as "nothing happened" and never fall back to its commit messages.
- `unknown` is a result file the run never asked for. Do not merge it.
- `partial_run:true` blocks apply by default (`SKILL.md` §7). Exit code is `1`.

### The three checks, each of which fails the day

- **Language** — the tag must be the one its manifest asked for.
- **Evidence accuracy** — every `evidence[]` entry, and every `` `backtick` ``
  symbol in the prose, is checked against the tree of the commit it cites: the
  commit exists, the file existed *at that commit*, the symbol appears in it, the
  `lines` range is inside it. A subagent that cites `migrate_directory` for a
  function called `parse_legacy` has told you nothing you can follow (#15). On a
  shallow clone, unreachable commits report `EVIDENCE_UNVERIFIABLE` rather than
  failing the day — that is the runner's clone depth, not the subagent's fault.
  Full rules: `references/subagent-contract.md` §8.
- **Coverage** — every required file (§5) is mentioned somewhere.
  `COVERAGE_INCOMPLETE` names exactly which were not.

If a day fails, **fix the analysis** — re-run that day's subagent against the
same manifest and let it write its `result_path` again, then collect once more.
Do **not** hand-edit the result file, and never paper over a gap with commit
messages.
