# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **The worklog now lives in `.git-worklog/`, with day files under `days/`.**
  `PROJECT_WORKLOG/<date>.md` → `.git-worklog/days/<date>.md`;
  `PROJECT_WORKLOG/index.md` → `.git-worklog/index.md`. The directory also gains
  a `VERSION` (on-disk layout version) and a `config.json` (`schema_version`,
  `timezone`, and language fields that are inert until the language contract
  lands). Both are created on first write or migration and are never rewritten
  afterwards — `config.json` is yours to edit.

  Markers are re-tagged `REPO_WORKLOG:` → `GIT_WORKLOG:`. The old prefix still
  *parses*, so a legacy file can be read and migrated — and so it is still
  refused inside generated content, where it would corrupt a file either way.

  **Migrate with `migrate_legacy_worklog.py`** (or `/git-worklog migrate`), which
  now handles both legacy shapes: the flat `PROJECT_WORKLOG/` directory
  (`--from-dir`) and the pre-v0.2 single `docs/PROJECT_WORKLOG.md`
  (`--from-file`). With neither flag it auto-detects, directory first. Dry-run is
  still the default, the source is never deleted, and an existing day file is
  never overwritten.

  A directory migration **copies each day file verbatim apart from its marker
  lines** — it does not re-render them. Re-rendering would have refreshed the
  header and destroyed the original `Branch`/`HEAD` the day was analysed at, and
  a worklog's existing prose and language are not migration's business to rewrite.
  For the same reason `--timezone` is ignored for `--from-dir`: each day already
  records its own.

  Reading a not-yet-migrated worklog keeps working — `detect_layout()` probes for
  the flat shape, so validation, coverage and report mode are unaffected, and the
  index rebuilds with links to wherever the day files actually are. **Writing**
  to one is refused with `LEGACY_LAYOUT` rather than leaving the directory half
  in each layout.

  Commits touching the old `PROJECT_WORKLOG/` are still recognised as the tool's
  own output and excluded from analysis. Most of a migrated repo's worklog
  history is in that directory; without this, every one of those commits would
  come back as real project work and a Day Subagent would summarise "today I
  wrote the worklog" — the bug fixed in v0.3.1, reintroduced by a rename.

- **Renamed to Git Worklog.** The repository is now `git-worklog`, the skill
  directory is `git-worklog/`, and the skill is invoked with `/git-worklog`.
  Reinstall to `~/.claude/skills/git-worklog` (or re-point your symlink) — an
  install left at the old path keeps answering to the old command.

  The skill directory and the frontmatter `name` had to move together: in Claude
  Code the **directory name is the command name**, and frontmatter `name` is only
  the display name. Renaming just the frontmatter — which is what the v1.0
  roadmap's PR 1 originally scoped — would have left `/repo_worklog` as the real
  command while every doc told users to type `/git-worklog`. So this change also
  updates the two hard-coded `repo_worklog/scripts` paths in CI and the two in
  `tests/`.

  Not renamed yet, to keep this change to names only: the `PROJECT_WORKLOG/`
  output directory, the `~/.repo_worklog/` state directory, and the
  `REPO_WORKLOG_*_MODEL` environment variables. Those carry data and behaviour
  and move with their own migrations. See `docs/naming-conventions.md` for what
  is active versus planned.

- **Subagents no longer report counts; they report coverage.** `tests[]` now
  names each test file and the behaviour it pins — never how many tests exist
  (no `count` field, no "N 個測試"). More broadly: **never state a quantity you
  did not measure**. If a number is load-bearing, derive it from the day's tree
  with a command and cite it in `evidence[]`.
- `verified` is tightened: it means provable from code/diff/tests **you actually
  opened, at the day's commit**. A quantity you did not run a command to measure
  is never `verified`.

  Why: v0.4.0's §5a stopped subagents *measuring the wrong repository*, and a
  re-run showed they simply started *inventing* instead — every per-file test
  count was wrong (13/12/10/8 against an actual 12/18/13/1), all labelled
  `verified`, with `uncertainties: []`. Closing one route to an unverified number
  opened another, so the fix is to stop asking for the number: coverage is
  provable from the diff, a count is not, and a worklog reader needs to know what
  a test protects rather than how many there are. `tests[]` had never been
  specified at all, which is how it filled with invented counts in the first
  place.

  Note this is **not enforced by `collect_day_results.py`** — schema validation
  catches shape, not truth, and a fabricated number in a prose field is
  well-formed. That is a known limit, not an oversight.

## [0.4.0] - 2026-07-16

### Added

- **Report mode** — a read-only second mode that answers questions from the
  existing worklog instead of building it: 「整理上一週工作摘要」, 「整理 v1.0.1
  CHANGELOG」, handoff summaries, what a named person worked on, a feature's
  history, or accumulated tech debt and follow-ups. The answer is returned in the
  conversation; nothing is written, so there is no dry-run or confirmation gate.
  Reports are synthesised from the day files, reusing their analysis rather than
  re-deriving it. Specified in `references/report-mode.md`; `SKILL.md` §1a routes
  between the two modes.
- `resolve_ref_range.py` — resolve a tag/ref to its **authoritative commit set**
  (`--tag` finds the previous tag automatically, `--from-ref`/`--to-ref` set an
  explicit pair, `--list-tags` enumerates). A version is bounded by commits while
  the worklog is indexed by date, and converting between them is lossy both ways,
  so the commit set is the scope and the derived dates only locate the day files
  worth reading.
- `check_worklog_coverage.py` — classify each date as `covered` / `gap` /
  `no-commits`. A date with no day file is only a gap if it had real commits; a
  commitless day gets no file by design (`worklog-format.md` §6). When a report's
  range contains gaps, the skill asks and recommends backfilling — it never
  silently degrades to summarising commit messages.
- `resolve_date_range.py` gains `--max-days` (default unchanged at 30) and echoes
  the cap in effect as `max_days`.
- Commit **authorship in the daily worklog**: a `參與者` line under `當日摘要` and
  per-commit authors in `相關 commits`. The manifest now carries
  `commits[].author_name` and a deduplicated day-level `authors[]`; the
  orchestrator renders attribution from those directly, so the Day Subagent
  return schema is unchanged. Author emails are deliberately excluded.
- `collect_day_results.py` — Day Subagent results are now exchanged through
  **files** instead of return values. The orchestrator mints a run directory
  (`init`) and hands each subagent its own
  `~/.repo_worklog/analysis/<run_id>/<date>.json`; the subagent writes its JSON
  there and replies only `DONE`; the orchestrator collects and validates the lot
  (`read`). See `references/subagent-contract.md` §6a.
- `tests/test_report_scope.py` and `tests/test_day_results.py`, plus multi-author
  and tagged fixtures (suite grows 97 → 162).

### Changed

- **BREAKING:** `evidence[]` entries — at the top level and inside each
  `work_item` — are now **objects, not strings**:
  `{commit, file, symbol?, lines?, note?}`, with `commit` and `file` required.
  `collect_day_results.py read` rejects prose or a missing `commit`/`file`
  (`EVIDENCE_INVALID`) and marks the day failed. Free text reliably decayed into
  a restatement of the commit subject — a real run produced
  `"commit 4d08ee4: 完整改造，加 authors[] 與 author_name"`, which cites nothing a
  reader can open yet satisfies any prose-based rule. `symbol` and `lines` stay
  optional so a doc or config change is not pushed to invent them.
- **Subagents must read the day's tree, not the working checkout**
  (`code-analysis-rules.md` §5a, new). The checkout carries every change made
  since the day being analysed. Subagents now read at the commit
  (`git show <hash>:<path>`, `git ls-tree -r <hash>`,
  `git grep -c <pattern> <hash> -- <path>`) and **must not run the test suite, a
  build, or a linter** — those measure today. Consulting the present is allowed
  only to answer "does this still exist now?", and such statements must be
  labelled as today's. The old rule permitted checking "the current version of
  that code" without qualification, and a real run took that licence: analysing
  2026-07-15 it ran the current suite and recorded "Ran 132 tests" as that day's
  result, when the suite had 44 tests that day.
- **Day Subagents deliver results by writing a file, not by returning them.** The
  return channel was the pipeline's weakest link: it drops content (observed — a
  subagent that spent 63k tokens on correct analysis returned nothing, losing the
  day), it truncates (a day's object is routinely 15KB+), and its semantics and
  limits differ across the hosts this skill targets. A file has none of those
  properties and persists, so a later failure never costs the analysis twice and
  a human can read exactly what a subagent concluded.
- The orchestrator's completeness check is now deterministic:
  `collect_day_results.py read` validates every result against the §6 schema and
  reports `missing` (never arrived) and `invalid` (unparseable or off-schema)
  explicitly. **A missing result is a failed day, never an empty one** — it is
  never silently skipped and never back-filled from commit messages, and it sets
  `partial_run`, which blocks apply by default.
- Two golden rules are now scoped per mode rather than weakened:
  - The 30-day cap bounds per-day subagent cost, so it governs generation and
    backfill. Report mode reads day files already on disk and spawns no
    subagents, so it reads up to 90 days.
  - The worklog still *stores* every author and never filters by
    `git config user.name/email`. Report mode may filter by author **only** when
    the user names the person explicitly — it never infers who "我" is.
- `date-parameter-contract.md` now distinguishes 「上一週」 (the previous calendar
  week, resolved to explicit `from`/`to`, never including today) from 「最近一週」
  (a rolling `days=7` window that does).
- `docs/init_plan.md` moved to `docs/plans/2026-07-15-repo-worklog-skill-design.md`;
  design plans now live under `docs/plans/` as `yyyy-MM-dd-<topic>.md`.

## [0.3.1] - 2026-07-16

### Fixed

- `collect_git_history.py` now excludes self-referential worklog commits (e.g.
  a `chore(docs): 補充 XX 專案工作日誌` commit that only edits day files and
  `index.md`). A commit whose changed files fall entirely inside the worklog
  output directory (`PROJECT_WORKLOG/` by default, `--worklog-dir` to
  override) is dropped from `commits[]` entirely and never counted in
  `commit_count`; a commit that also touches real files keeps only its
  non-worklog files. A day whose only commits were worklog output now reports
  `has_changes:false`, the same as a day with no commits, instead of
  producing a worklog entry that describes itself.

## [0.3.0] - 2026-07-16

### Changed

- **BREAKING:** default per-host subagent models are now cost-first for bounded,
  per-day analysis — `anthropic` → Claude Haiku 4.5 (`claude-haiku-4-5`), `openai`
  → GPT-5.6 Luna (`gpt-5.6-luna`, reasoning effort `low`), `google` → Gemini 3.5
  Flash (`gemini-3.5-flash`). The previous defaults survive only as opt-in
  escalation models.
- **BREAKING:** subagent model selection has a single source of truth,
  `config/provider_models.json`. `agents/openai.yaml` no longer carries a
  `providers:` block — it points to the config via `model_config:`.
- **BREAKING:** `build_analysis_manifest.py` takes `--model-json` (a structured
  `{display_name, model_id[, reasoning_effort]}` object) instead of `--model`. The
  manifest's `model` field is now that object (or `null`), and `reasoning_effort`
  is omitted for providers that have none rather than emitted as an empty string.
- The Day Subagent return schema gains `status`, `confidence`,
  `escalation_recommended`, and `escalation_reasons`. These are advisory only and
  never trigger an automatic model switch.
- The dry-run summary now reports the resolved subagent configuration (provider,
  model, model id, reasoning effort, automatic escalation: disabled).

### Added

- `resolve_provider_model.py` — resolve the per-host provider/model. The host is
  never guessed; override precedence is `--model` > `REPO_WORKLOG_<PROVIDER>_MODEL`
  > config default. An unresolvable model halts and asks with a candidate list — it
  never silently falls back to a default or a pricier model.
- Opt-in escalation configuration (`escalation_model_id` per provider,
  `escalation_policy.automatic: false`). Escalation runs only after explicit user
  approval and mints a new preview id; subagents never escalate on their own.
- `tests/test_provider_models.py` — provider mapping, override precedence, manifest
  threading, no-silent-fallback, and escalation coverage (suite grows 65 → 92).

### Migrating from 0.2.0

The ids in `config/provider_models.json` are public model names. If your host
dispatches under a different id, override it with `REPO_WORKLOG_ANTHROPIC_MODEL` /
`REPO_WORKLOG_OPENAI_MODEL` / `REPO_WORKLOG_GOOGLE_MODEL` (or an explicit
`--model`) rather than editing multiple files. Anything that called
`build_analysis_manifest.py --model <id>` must switch to `--model-json '<object>'`.

## [0.2.0] - 2026-07-16

### Changed

- **BREAKING:** the worklog is now a directory, not a single file. It moves from
  `docs/PROJECT_WORKLOG.md` to `PROJECT_WORKLOG/` at the repository root — one file
  per day (`<date>.md`) plus a navigation `index.md`. Re-analysing a day rewrites
  only that day's file; every other day file is left byte-for-byte untouched, which
  keeps diffs small, avoids re-reading/rewriting one large file, and prevents
  multi-author conflicts on different dates.
- Each day file keeps a human-owned `MANUAL` region (preserved verbatim, including
  any content placed after `MANUAL:END`) and a tool-owned title/meta header plus
  `GENERATED` region. `index.md` has its own preserved `MANUAL` region.
- The apply-time preview fingerprint is now multi-file (index hash + per-day file
  hashes + the day-file directory listing), so a change to any target day file, to
  `index.md`, or to the set of day files invalidates a stale preview.

### Added

- `update_daily_worklog.py` — transactional multi-day writer (stage → validate →
  atomic swap → rollback); a failed run never leaves some days updated and others
  not.
- `rebuild_worklog_index.py` — rebuild `index.md` from the day files, preserving the
  index `MANUAL` region; an `overrides` input previews pending writes without
  touching disk.
- `validate_daily_worklog.py` / `validate_worklog_index.py` — per-day-file and index
  structural validation.
- `migrate_legacy_worklog.py` (`/repo_worklog migrate`) — one-time split of a legacy
  single-file worklog. Previews first, never deletes the legacy file, never
  overwrites an existing day file, and refuses corrupt or marker-colliding input.

### Removed

- `update_worklog.py` and `validate_worklog.py` (replaced by the per-day scripts
  above).

### Fixed

- Writer scripts emit a single JSON object on every path — an unexpected error can
  no longer surface as a bare Python traceback.
- Generated content containing a bare `REPO_WORKLOG` marker line is refused up front
  (`GENERATED_CONTAINS_MARKER`) instead of producing an unparseable day file; the
  same guard protects migration.
- Transactional writes no longer leak a staged temp file when validation fails, and
  rollback restores the original files atomically.
- Content after a day file's `MANUAL:END`, and duplicate end markers, are now handled
  correctly.

### Migrating from 0.1.0

Run `/repo_worklog migrate` (or `python3 scripts/migrate_legacy_worklog.py`) to
convert an existing `docs/PROJECT_WORKLOG.md` into `PROJECT_WORKLOG/`. It previews
the split first and never deletes the legacy file — remove it yourself once you are
satisfied.

## [0.1.0] - 2026-07-15

### Added

- Initial release of the `repo_worklog` agent skill: turns a Git repository's real
  code history into a per-day project worklog by reading actual diffs and the
  surrounding code (never just commit messages), with one day subagent per day and
  the whole project's history (every author).
- Deterministic, stdlib-only helper scripts — date-range resolution, Git history
  collection, worktree inspection, analysis-manifest building, and the worklog
  write/validate engine — each printing a single JSON object.
- Dry-run-first workflow: every request previews first, and a preview fingerprint
  (repo / branch / HEAD / worktree / worklog hash) gates each apply. MANUAL regions
  are preserved; the skill never runs `git add/commit/push`.
- Reference docs (interaction flow, date-parameter contract, code-analysis rules,
  subagent contract, worklog format, provider models), a bilingual README, and the
  MIT license.
- A stdlib-only `unittest` suite and GitHub Actions CI on Python 3.9 / 3.12 / 3.13,
  with a `skill.zip` release artifact.

[0.4.0]: https://github.com/g761007/git-worklog/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/g761007/git-worklog/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/g761007/git-worklog/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/g761007/git-worklog/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/g761007/git-worklog/releases/tag/v0.1.0
