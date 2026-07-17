# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`git-worklog preview` / `git-worklog apply`** (#6) — a preview is now the
  artifact, not a receipt for one.

  It used to be a *fingerprint*: a hash of everything that must not change
  between the dry-run and the apply, while the content itself stayed in the
  agent's conversation and was handed back at apply time. That proves the world
  did not move. It cannot prove the bytes written are the bytes the user read —
  and anything that re-renders in between (a re-run subagent, a dropped message,
  a different model) produces a worklog nobody approved while every check still
  passes.

  So `preview --run-id <id>` now stores the complete final text of every file
  the apply will write, and `apply --preview-id <id>` writes exactly that. The
  command takes no other input, which is the actual guarantee: there is no
  argument through which a re-render could arrive.

  Apply re-checks everything the payload depends on before writing — repository
  identity, git dir, branch, HEAD, submodules, the working tree (only when the
  run read it), every target day file, `index.md`, the day-file listing, the
  run's manifests and results, and the project's language settings — and refuses
  rather than reconciles. `preview` also refuses a partial run outright
  (`RUN_NOT_COLLECTED`) and a day the run never analysed (`UNKNOWN_DATE`).

  States: `previewed → confirmed → applied`, plus `cancelled`, `failed`, and the
  computed `expired` / `stale`. `confirmed` is written to disk *before* the first
  byte, so a process that dies mid-apply leaves evidence rather than a record
  still claiming nothing happened. Concurrent applies to one worklog are locked
  out; a lock is broken only when its owner is provably dead.

- **A day's analysis must now account for the source it changed** (#5).
  Manifests carry `required_commit_file_pairs` — every (commit, file) the day
  touched, each flagged required or not — and `analyze collect` fails any day
  that leaves a required file unmentioned (`COVERAGE_INCOMPLETE`, naming them).

  This is the completeness half of the evidence problem. #15 made citations
  *accurate*: one now has to resolve against the tree it names. Accuracy says
  nothing about *coverage* — an analysis can cite three real files perfectly,
  never mention the other twenty it changed, and still read as
  `status: complete, confidence: verified`.

  Naming a file in a work item's `files[]` satisfies the requirement; an
  `evidence[]` citation is stronger but not demanded. That bar is what
  measurement supports rather than what a stricter-sounding rule would give:
  against a real, good run, requiring `evidence[]` for every file rejects it
  (31% cited), while `files[] ∪ evidence[]` passes it at 91% and still names the
  two files its analysis genuinely never reached.

  Only source files are required. Docs, config, CI and tests are listed but
  excused — real work, but a day may fairly cover them in a sentence. Binaries
  and submodules have no source to cite. Deletions are excused because the file
  is *gone* from that commit's tree: requiring a citation would require the
  impossible.

- **`analyze prepare --include-uncommitted`** — the working tree joins today's
  manifest and no other day's. Asking for it on a range that excludes today
  warns (`UNCOMMITTED_NOT_IN_RANGE`) rather than silently reading as "the tree
  was clean".

- **`git-worklog analyze prepare` / `analyze collect`** (#5) — the two commands
  that bracket an analysis without performing it. `prepare` mints a run and
  writes one Analysis Manifest per day under
  `~/.git-worklog/analysis/<run_id>/tasks/`, each naming the `result_path` its
  analysis must be written to; `collect` reads the results back and checks that
  every prepared day arrived, that none drifted language, and that every
  evidence citation resolves against the tree of the commit it names.

  Between them sits the hosting agent's LLM, which is the only part that reads
  code and writes prose — so the CLI still needs no model API key. `collect`
  takes its dates from the *tasks*, never from its command line: a
  caller-supplied date list would let a day be dropped from a run just by
  omitting it from the second command, which is the exact failure `missing`
  exists to report. A result nobody asked for is reported as `unknown` and
  blocks the run rather than being merged as a day that was never prepared.

  Manifests now carry the roadmap §8 fields: `schema_version`, `run_id`,
  `repository` (including `git_dir`, which is not `root/.git` in a worktree),
  `result_path`, and `analysis_rules` — the rules travel on the manifest
  because the manifest is what actually reaches the model, and a rule stated
  only in prose is a rule that is not enforced.

  The skill now drives the analysis pipeline through these two commands
  (`python3 -m git_worklog analyze ...`, no install needed) instead of
  `collect_git_history.py | build_analysis_manifest.py` and
  `collect_day_results.py`.

- **Evidence citations are now checked against the repository, not just for
  presence** (#15). `collect_day_results.py read` gains a required `--repo` and
  verifies every evidence entry against the tree of the commit it cites: the
  commit exists, the file existed *at that commit*, the `symbol` appears in it,
  and the `lines` range is inside it. A citation that does not resolve fails the
  day and blocks apply — the same outcome prose evidence already got, because a
  name that leads nowhere is worth the same.

  Found by dogfooding: a real run produced 7 fabrications in 32 entries and
  every one passed, because only `commit` and `file` were ever enforced and
  those were genuine. `symbol` and `lines` were decorative — they looked like
  citations and were not. Every fabrication was a *plausible* name
  (`migrate_directory` for `parse_legacy`, `preview_dir` for `previews_dir`, a
  line range past EOF), which is exactly why a reader cannot catch them and the
  check has to be mechanical.

  A shallow clone reports `EVIDENCE_UNVERIFIABLE` instead of failing the day:
  the commits are unreachable because of the runner's clone depth, which is not
  the subagent's fault. An invented hash in a full clone is
  `EVIDENCE_COMMIT_UNKNOWN` and does fail.


- **A BCP 47 language contract (roadmap §6.2).** The worklog is written in the
  language you are asking in, not the language the repository happens to be in.
  A repo full of English commit messages, English identifiers and an English
  README still produces a `zh-TW` worklog for a `zh-TW` conversation — source
  data never votes on output language.

  `--language` and `--language-source` on `build_analysis_manifest.py`, plus
  `language` and `index_language` in `.git-worklog/config.json` (both shipped as
  `auto` since the `.git-worklog/` layout and inert until now) and the new
  `GIT_WORKLOG_LANGUAGE` environment variable. Priority runs: what you asked for
  → CLI argument → project config → the agent host → environment → system locale
  → English.

  The tiers above "project config" are only visible to the agent running the
  skill, so it resolves those and passes the answer down; the scripts resolve the
  rest. An agent-hosted run **never** reads the OS locale (§6.2.5): a container
  pinned to `en_US` says nothing about what you want. When nothing resolves, the
  run says so — `source: "fallback"` plus a `LANGUAGE_NOT_RESOLVED` warning — so
  the agent can re-run with an explicit language rather than quietly writing
  English. A malformed `--language` fails the run instead of falling back, since
  falling back would write English for someone who asked for something else and
  mistyped it.

  Bare `zh` is refused: it does not say Hant or Hans. Language *names*
  (`chinese`, `traditional`) are not tags. `zh-TW` and `zh-CN` are never the same
  setting; `zh-tw` and `zh-TW` always are.

  Day results now declare their `language`, and it must match the manifest.
  Validation is structural, never a language detector — correct `zh-TW`
  engineering prose is full of English paths and symbols by contract, and a
  detector would flag it. A run whose days disagree is partial and cannot apply.

  Apply cannot change the language: `params.language` is part of the preview's
  consistency check, so confirming a `zh-TW` preview and then asking for English
  goes stale and asks for a fresh preview rather than writing prose nobody saw.

  `index.md` decides its language once and keeps it — a config pin, else the
  language stamped on the index when it was first built. Otherwise a `zh-TW`
  developer and an English one would flip its headings back and forth in every
  diff. Existing indexes carry no stamp and are read as `zh-TW`, so nothing is
  retitled on upgrade. Index furniture ships in `zh-TW` and `en`; any other
  language gets English furniture around day summaries written in that language,
  rather than machine translations nobody here can proofread.

  `doctor` and `validate` now check language settings for real, rather than
  reporting them as skipped.

  `--interface-language` on the CLI keeps the tool's own messages separate from
  the worklog's language (§6.2.13): `--language zh-TW --interface-language en` is
  a supported combination, and neither drags the other along. Messages ship in
  English only for now — the roadmap allows that — but asking for another
  language gets `INTERFACE_LANGUAGE_NOT_SUPPORTED` rather than silence that looks
  like it worked. JSON keys never translate; they are API.

- **A summary marker in day files** (`<!-- GIT_WORKLOG:SUMMARY:START -->`).
  `index.md` used to find each day's summary by looking for the literal `當日摘要`
  heading, which meant a day written in any other language got a blank index row
  — no error, no warning, just a missing summary. The markers say *where* the
  summary is without saying what language it is in. Day files written before them
  keep working through the heading fallback and are never rewritten to gain one;
  `validate` warns (`DAY_SUMMARY_UNMARKED`) that such a day would lose its
  summary if regenerated in another language. As a side effect the participants
  line can no longer hijack the index row, since it now sits outside the markers.

- **A `git-worklog` CLI, and the `git_worklog` package behind it.** First three
  commands (roadmap §12.1): `version` reports the CLI, layout and config-schema
  versions separately; `doctor` checks whether this environment can actually run
  the tool; `validate` checks whether the worklog on disk is well-formed. Each
  prints one JSON object like the scripts do, `--text` renders for humans, and
  exit codes separate "it ran and the answer is no" (1) from "it could not run"
  (2).

  The package lives **inside** the skill directory (`git-worklog/git_worklog/`),
  not beside it. Copying the skill folder still yields a working skill with
  nothing installed and no dependencies — `pyproject.toml` maps the package root
  there, so the same code is *also* `pip install`-able for anyone who wants the
  command on PATH. Roadmap §3 draws the two as siblings; that split is PR 7's,
  and it must not cost the copy-to-install property.

  `git_worklog/__init__.py`'s `__version__` is now the single source of the
  product version — `pyproject.toml` reads it dynamically, and CI asserts the
  installed console script agrees with it. See issue #12.

  `doctor` and `validate` report what they did **not** check (`skipped`) rather
  than omitting it: preview and analysis records are validated inside a run,
  where the run id is known, so a worklog directory is the wrong place to judge
  them. A green check should never imply more coverage than it has.

- **`GIT_WORKLOG_HOME`** overrides the user-level state directory, which is now
  `~/.git-worklog/` (was `~/.repo_worklog/`). Nothing is migrated: previews
  expire in 24h and analysis files are diagnostic leftovers, so a stale copy is
  noise, not loss — `doctor` points the old directory out and leaves deleting it
  to you. Newly created state directories are `0700`; they quote source and diffs
  from private repositories. This variable is new, not a rename — there was never
  a `REPO_WORKLOG_HOME`.

### Fixed

- **A large day's fan-out no longer blocks itself** (#6). Manifests gain
  `parts_dir`, and `analyze prepare` creates `<run_dir>/parts/` for it.

  A Day Subagent is handed a manifest and a `result_path` and nothing else, so
  when `large_day` led it to split the day into Code Analysis Subagents, it
  derived their paths from the only path it had — landing them in `results/`,
  where `collect` reads every file as some day's answer and fails the run over
  any it did not ask for. The day the contract most wants split up was exactly
  the day that then could not be written.

  The contract sent it there: §10 said the parts go "under the same `run_dir` …
  beside the day's result without colliding", which was true when a run was one
  flat directory and stopped being true when `tasks/` and `results/` were split
  out. It could not be fixed by rewording, either — `run_dir` was not on the
  manifest, so the sentence described a path the subagent had no way to build.

  Found by dogfooding this repo's own 28-commit day.

### Removed

- **`scripts/preview_state.py`** (#6) — superseded by `git-worklog preview` /
  `apply`. Its contract could not survive the immutable record: `create` now
  needs the payload, and `verify` compared parameters the caller re-supplied,
  which `apply` no longer accepts. Keeping it would have left two things called
  "preview" meaning different things, one of which cannot deliver the guarantee
  the other exists for.

### Changed

- **The last script-local engines moved into the package** (#7). Date resolution,
  ref-range resolution, provider/model resolution, coverage and legacy migration
  were business logic living in `scripts/`, where only a script could reach them
  — an installed `git-worklog` has no `scripts/` directory to shell out to. They
  are now `git_worklog/dates.py`, `analysis/refs.py`, `providers.py`,
  `analysis/coverage.py` and `migrate.py`; the scripts are thin shells over them
  and their JSON contracts are unchanged.

  Failures that were `sys.exit(2)` calls buried in the logic are now exceptions
  carrying the wire code (`DateError`, `ProviderError`, `MigrateError`,
  `AnalysisError`), so the same rules can serve a CLI subcommand that has to
  render them differently. `scripts/` drops from 2029 to 1189 lines.

  The half-open local-midnight day window was written out three times — in
  `resolve_date_range.py` and, twice, as copies commented "matching
  resolve_date_range.py". It is now `dates.day_window()` alone: what a day file
  covers, what a subagent is asked about, and what report mode counts commits in
  are the same window by construction rather than by three authors agreeing.

- **The worklog writer moved into the package** (`git_worklog/writer.py`) (#6).
  `preview` has to compute the exact bytes it stores and `apply` has to write
  them, but the planning and the transactional write lived in `scripts/`, which
  an installed CLI cannot reach. `update_daily_worklog.py` and
  `rebuild_worklog_index.py` are now thin shells over it; their JSON contracts
  are unchanged.

- **Model override variables are now `GIT_WORKLOG_{ANTHROPIC,OPENAI,GOOGLE}_MODEL`.**
  The `REPO_WORKLOG_*` names shipped publicly in v0.3.0–v0.4.0 and are **still
  honoured** — dropping them would silently swap a user's model back to the
  config default, which is precisely what the resolver exists to prevent. The
  current name wins when both are set, and a run that used an old name reports
  `DEPRECATED_ENV_VAR` in `warnings[]` rather than obeying it quietly. Removed in
  v2.0.

- `worklog_markers.py` moved to `git_worklog/markers.py` so the CLI and the
  scripts share one definition of the format. `scripts/worklog_markers.py`
  remains as a shim that re-exports it, so every script and every existing import
  keeps working unchanged.

- **The worklog now lives in `.git-worklog/`, with day files under `days/`.**
  `PROJECT_WORKLOG/<date>.md` → `.git-worklog/days/<date>.md`;
  `PROJECT_WORKLOG/index.md` → `.git-worklog/index.md`. The directory also gains
  a `VERSION` (on-disk layout version) and a `config.json` (`schema_version`,
  `timezone`, `language` and `index_language`). Both are created on first write
  or migration and are never rewritten
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

### Changed

- **The analysis pipeline moved into the `git_worklog` package**, behind the
  scripts that used to hold it: `git_worklog.analysis.history`, `.manifest`,
  `.results` and `.worktree`. Only `git_worklog*` is packaged, so an installed
  CLI has no `scripts/` directory to shell out to — the logic had to be
  importable before `analyze` could host any of it.

  The scripts keep their exact command-line contracts and are unchanged from a
  caller's point of view, with one addition: `collect_git_history.py` now
  reports `repository.git_dir`.

  `collect_day_results.py` still works, but cannot check coverage: it is handed
  a run directory and never sees a manifest, so it does not know what was
  required and does not pretend to. Prefer `analyze collect`.

  Internally, the package raises `AnalysisError` (carrying the wire code) where
  the scripts called `_fail()` and exited — a function that exits the process
  cannot be reused by a second front end. Each script converts it back to the
  exit code it always produced.

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
