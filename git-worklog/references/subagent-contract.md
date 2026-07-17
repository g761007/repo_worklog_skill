# Subagent contract

The division of labour between the main orchestrator and the per-day subagents,
the exact JSON a Day Subagent must return, and how a failed day is handled. Multi
-day ranges are split one Day Subagent per date; a large single day may fan out
further into Code Analysis Subagents. Subagents analyse and return structured
data — they never touch the worklog.

The reading discipline a subagent must follow (real diffs, context expansion,
merge/revert/rename handling, end-of-day state) lives in
`references/code-analysis-rules.md`. Manifest construction lives in
`git_worklog.analysis.manifest`, driven by `analyze prepare`. This file is the
interface between them.

---

## 1. Roles split

Two levels of responsibility. They do not overlap.

**Main orchestrator** (the model running the skill). Owns everything the user
sees and every decision that spans more than one day:

- user interaction and confirmation,
- natural-language parsing into canonical parameters,
- date validation (`resolve_date_range.py`),
- per-day task splitting (one Day Subagent per date),
- subagent dispatch and provider/model selection,
- minting the run and handing each subagent its manifest and output path
  (`analyze prepare` — see §6a),
- result-completeness checks (`analyze collect`: every dispatched date produced a
  valid object on disk, in the right language, with accurate citations covering
  the day's required files),
- cross-day deduplication,
- Markdown generation (one GENERATED block per day, in the run's resolved
  language; the SUMMARY-marked line becomes that day's row in `index.md`),
- dry-run, preview management, and writing (`git-worklog preview` / `apply`).

The orchestrator **must not** use a commit `subject` in place of a subagent's
code analysis. If a day has no analysis, that day has no content — it is not
back-filled from commit messages.

**Day Subagent.** Handles **exactly one date** and **never writes the worklog**.
It reads Git and code, then **writes** one structured JSON object (section 6) to
the output path it was given (section 6a). A day with no commits and no in-scope
uncommitted changes still writes a valid object with `has_changes:false` — it is
never silently dropped.

---

## 2. Day Subagent responsibilities

For its one date, a Day Subagent must (plan §9.3):

- get the day's commits and their metadata,
- for each relevant commit, read the **actual patch** via `git show` — not the
  subject, body, file list, or diffstat alone,
- analyse the changed files,
- track rename and copy (with detection enabled),
- analyse merge commits and revert candidates,
- group commits by **real work theme**, not one-per-commit,
- read the modified full code plus its direct callers, direct dependencies, and
  related tests,
- determine the **end-of-day final state** (a feature added then reverted the
  same day is reported as reverted, not as a live feature),
- return structured results with evidence for every important conclusion.

The full reading rules — how far to expand context, how to handle
merge/revert/rename/binary/lockfile/submodule, and how to reason about final
state — are in `references/code-analysis-rules.md`. A summary derived only from
commit messages is **not acceptable** and must never be returned as if it were
code analysis.

Note: patches are **not** included in the manifest. The subagent obtains them
itself, e.g. `git show --format=fuller --find-renames --find-copies <hash>`.
Uncommitted content (when `include_uncommitted` is set) is attributed to **today
only**, never to a past date.

---

## 3. The analysis manifest (input)

Each Day Subagent is handed one manifest, written by `analyze prepare` to the
task's `manifest_path`. The orchestrator has already confirmed the run prepared
cleanly before dispatch. The manifest is a **planning aid**:
it groups files and proposes what to read; it never contains patches and never
decides worklog wording.

Fields:

| Field | Type | Meaning |
|-------|------|---------|
| `schema_version` | int | Manifest schema version (`1`). |
| `run_id` | string\|null | The run this task belongs to; null for a manifest built ad hoc. |
| `date` | `"YYYY-MM-DD"` | The single day this manifest covers. |
| `timezone` | string | Resolved IANA timezone. |
| `repository` | object\|null | `{root, git_dir, head, branch}` — which checkout this came from. |
| `result_path` | string\|null | Where this day's analysis must be written (section 6a). |
| `parts_dir` | string\|null | Where a fan-out's per-group parts go (section 10). Never a sibling of `result_path`: `results/` is read as the run's answers, so a stray file there fails the run. |
| `analysis_rules[]` | array | The rules this analysis is held to, carried on the task itself. |
| `required_commit_file_pairs[]` | array | `{commit, file, category, required}` — section 3a. |
| `include_uncommitted` | bool | Whether working-tree changes are in scope (today only). |
| `provider` | string | `anthropic` / `openai` / `google`. |
| `model` | object\|null | `{display_name, model_id}` (+ `reasoning_effort` for openai). Section 4. |
| `has_changes` | bool | True if there are commits or in-scope uncommitted changes. |
| `commit_count` | int | Number of commits on the day. |
| `authors[]` | array | The day's distinct commit author names, deduplicated, ordered by first appearance. |
| `commits[]` | array | `{short_hash, full_hash, author_name, subject, is_merge, is_revert_candidate}`. |
| `changed_files[]` | array | `{path, statuses, category, is_binary, is_submodule, old_path, commits}`. |
| `file_groups[]` | array | `{group, category, module, files, commits, has_binary, has_submodule}`. |
| `required_context[]` | array | `{group, category, read[], expand_second_layer_if, depth}`. |
| `uncommitted_changes[]` | array | Working-tree entries (present only when `include_uncommitted`). |
| `large_day` | bool | True when the day is big enough to warrant fan-out (section 5). |
| `recommended_code_analysis_subagents` | int | Suggested fan-out count (0 when not large). |

`commits[].subject` is an **index and background only**. The subagent must still
read each relevant patch and the surrounding code before drawing conclusions.

**Authorship is rendered by the orchestrator, not returned by the subagent.**
`authors[]` and `commits[].author_name` are deterministic facts the manifest
already carries, so the orchestrator renders the `參與者` line from `authors[]`
and resolves each `相關 commits` entry's author by `short_hash` lookup (see
`references/worklog-format.md` §3). Routing them through the subagent's return
schema would only add a step where a name can be dropped or misattributed, so
the return schema (§6) carries no author field. A subagent may still *use*
authorship as analysis context — e.g. noticing that one work theme was handed
between two people — but it never decides the rendered attribution.

Author **emails are deliberately absent** from the manifest. The worklog is
human-facing prose; emails add PII noise and no narrative value. Do not fetch
them separately.

**Self-referential worklog commits are already excluded.** `collect_git_history.py`
drops any commit whose changed files fall entirely inside the worklog output
directory (`.git-worklog/` by default) before the manifest is built — see
`references/code-analysis-rules.md` §6.8. A day whose only commits were worklog
output arrives with `commits: []`, `commit_count: 0`, and `has_changes: false`,
same as a day with no commits at all.

---

## 3a. `required_commit_file_pairs[]` — what the day is held to

Every (commit, file) the day touched is listed, each flagged `required` or not.
All of them appear, not only the required ones, so you can tell what you are
*not* being held to from what was overlooked.

**A file with `required:true` must be accounted for in the result.** Naming it in
a work item's `files[]` is enough; an `evidence[]` citation is stronger but not
demanded. `analyze collect` fails the day with `COVERAGE_INCOMPLETE` and names
every required file the result never mentions.

Only source categories (`backend`, `frontend`, `mobile`, `database`) are
required. Docs, config, CI and tests are listed but excused — real work, but a
day may fairly cover them in a sentence. Binaries and submodules are excused
(there is no source to read), and so are deletions: the file is gone from that
commit's tree, so a citation of it there would be rejected anyway.

This is the completeness half of the evidence rules. §8 makes each citation
*accurate*; this makes the set of them *complete*. Accuracy alone is not enough —
an analysis can cite three real files perfectly, never mention the other twenty
the day changed, and still look verified. **A file you changed but never
described is a file you may never have read.** If a required file genuinely
needed no comment, say so in one line rather than omitting it.

Do not satisfy this by listing paths you did not open. A `files[]` entry is a
claim that the file was part of the work you are describing.

---

## 4. Provider / model per host

The orchestrator resolves the model with `scripts/resolve_provider_model.py
--host <key>` and sets `provider` and `model` on the manifest from its output.
Defaults are cost-first; the host is never guessed. Full rules (overrides, env
vars, unavailable-model halt, escalation) are in `references/provider-models.md`.

| Host | `provider` | default `model` (`model_id`) |
|------|-----------|------------------------------|
| Claude Code | `anthropic` | Claude Haiku 4.5 (`claude-haiku-4-5`) |
| Codex | `openai` | GPT-5.6 Luna (`gpt-5.6-luna`, reasoning_effort `low`) |
| Gemini | `google` | Gemini 3.5 Flash (`gemini-3.5-flash`) |

`model` is an object — `{display_name, model_id}`, plus `reasoning_effort` for
`openai` only (omitted for anthropic/google, never an empty string). A Day
Subagent runs on, and spawns any Code Analysis Subagents on, the same
provider/model it was given. It never swaps to another or to the escalation
model on its own.

---

## 5. Large-day fan-out

When the manifest sets `large_day:true`, a Day Subagent **may** spawn Code
Analysis Subagents and merge their results. Group the work by these criteria, in
**priority order** (plan §9.4):

1. actual feature or module,
2. related file groups,
3. backend / frontend / mobile,
4. API,
5. database / migration,
6. tests,
7. configuration / CI,
8. deployment,
9. documentation.

The manifest's `file_groups[]` already reflects this ordering (`category:module`
keys, category-priority sorted), and `recommended_code_analysis_subagents`
suggests how many to spawn. Use them as the default partition.

**Forbidden:** the naive "one commit = one Code Analysis Subagent" split. One
work item routinely spans several commits (add feature → fix it → adjust it), and
one commit routinely touches several work areas. Splitting by commit fractures a
single theme across agents and destroys the end-of-day picture. Split by work
area, then let each subagent see **all** the commits touching its files.

Fan-out is optional even when `large_day:true`; a Day Subagent that can analyse
the day directly is free to do so. Every Code Analysis Subagent still returns
structured, evidence-backed data — never a commit-message paraphrase — and the
Day Subagent is responsible for reconciling them into one return object,
including cross-group deduplication within the day.

---

## 6. Day Subagent return schema (EXACT)

**A Day Subagent delivers its result by writing a file, never by returning it as
its reply.** The orchestrator mints a run directory and hands each subagent its
own output path (§6a); the subagent's final action is to write exactly this
object there, then reply only `DONE`.

The object below is what goes **in the file**. All keys are present even when the
array is empty. The file contains the JSON object and nothing else — no prose, no
markdown fence.

```json
{
  "date": "YYYY-MM-DD",
  "timezone": "Asia/Taipei",
  "language": "zh-TW",
  "status": "complete",
  "confidence": "verified",
  "escalation_recommended": false,
  "escalation_reasons": [],
  "has_changes": true,
  "commits": [],
  "work_items": [],
  "fixes": [],
  "refactors": [],
  "tests": [],
  "database_changes": [],
  "configuration_changes": [],
  "deployment_changes": [],
  "uncommitted_changes": [],
  "handoff_notes": [],
  "uncertainties": [],
  "evidence": []
}
```

Each entry in `work_items[]` is exactly:

```json
{
  "title": "",
  "summary": "",
  "behavior_change": "",
  "implementation": "",
  "impact": "",
  "files": [],
  "commits": [],
  "tests": [],
  "risks": [],
  "maintenance_notes": [],
  "follow_ups": [],
  "confidence": "verified",
  "evidence": []
}
```

Rules:

- A no-change day returns `has_changes:false` with the arrays empty (still a
  valid object) and `status:"complete"`, `confidence:"verified"`.
- `status` is one of `complete` / `partial` / `failed`; `confidence` is one of
  `verified` / `inferred` / `unknown` (the day-level aggregate — see §7).
- `escalation_recommended` (bool) and `escalation_reasons[]` are an **advisory
  signal only** to the orchestrator; they never trigger an automatic model switch
  (§7).
- `uncommitted_changes[]` is populated only when `include_uncommitted` is set and
  is attributed to today only.
- Anything the subagent could not verify goes in `uncertainties[]`, not into a
  `work_item` phrased as fact.
- **`tests[]` describes what the tests *cover*, not how many there are.** Each
  entry names the test file and the behaviour it pins, e.g.
  `"tests/test_git_collection.py — merge 偵測、revert 候選、rename/copy、二進位檔、空 repo"`.
  Coverage is provable from the diff; a count is not, and a worklog reader needs
  to know what is protected, not the size of the suite. **Do not emit a test
  count** — no `count` field, no "N 個測試", no "(N test cases)".
- **Never state a quantity you did not measure** (test counts, file counts, line
  counts, percentages). This is the rule most likely to be broken, because a
  plausible number is easy to produce and looks like analysis. If a number is
  genuinely necessary, derive it from the day's tree with a command
  (`git grep -c <pattern> <hash> -- <path>`, `git ls-tree -r <hash> | wc -l`) and
  cite that commit and file in `evidence[]`. Otherwise omit it and describe the
  thing instead. An unmeasured number is a fabrication even when it is close.

---

## 6a. Result exchange — files, not return values

The result of a Day Subagent is the expensive part of the whole run: real patches
read, real code understood. Handing it back as reply text makes it hostage to the
host's return channel, which is the pipeline's weakest link:

- **It drops content.** Observed in practice: a subagent that spent 63k tokens on
  correct analysis returned nothing at all, and the day's work was lost.
- **It truncates.** A day's object is routinely 15KB+; a large day is far bigger.
- **It differs per host.** This skill runs under Claude Code, Codex and Gemini,
  whose return semantics and size limits are not the same.
- **It is not recoverable.** A lost reply means re-running the entire analysis.

A file has none of those properties, and it persists — so a later failure in
rendering, preview or apply never costs the analysis a second time, and a human
can read exactly what a subagent concluded.

### The flow

1. **Prepare the run** (orchestrator, once per run, before dispatch):

```
python3 -m git_worklog analyze prepare --repo <root> \
    --from 2026-07-15 --to 2026-07-16 --timezone Asia/Taipei \
    --language <tag|auto> --language-source <source>
```

Returns `run_id`, `run_dir`, and `tasks[]` — one entry per date, each with a
`manifest_path` and a `result_path`. Both live outside the repository, under
`~/.git-worklog/analysis/<run_id>/{tasks,results}/<date>.json`, next to the
preview state. The worklog directory is for the worklog; it is never used for
scratch.

2. **Dispatch**, giving each Day Subagent **its own** `manifest_path` and
`result_path`. One path per subagent means two days can never race on one file.

3. **Collect** (orchestrator, after all subagents finish):

```
python3 -m git_worklog analyze collect --run-id <run_id> --repo <root>
```

Returns `results` (date → the validated object), `complete`, `degraded`
(status `partial`/`failed`), `missing` (no file arrived), `invalid` (unparseable,
off-schema, wrong language, inaccurate citations or incomplete coverage — with
the reason), `unknown` (a result the run never asked for), `failed_dates`,
`partial_run`, and `escalation_suggested_dates`.

Note what `collect` is *not* given: a date list or a language. It reads the run's
own manifests for both, so a day cannot be dropped from the check by being left
off a command line, and a result cannot be judged against a language nobody asked
for.

### What this guarantees

`collect` validates every result against §6 — required top-level keys, the date
matching the file it was produced for, `status` and `confidence` in their allowed
sets, each `work_items[]` entry's required keys — then against its manifest: the
declared language (§6.2.9), every citation's accuracy against the cited commit's
tree (§8), and coverage of the day's required files (§3a). It is the
deterministic half of the orchestrator's completeness check (§1).

**A missing or malformed file means that day failed.** It is never an empty day,
never silently skipped, and never back-filled from commit messages. `collect`
reports it in `missing` / `invalid` and sets `partial_run:true`, which blocks
apply by default (§11).

**When a day fails, fix the analysis, not the file.** Re-run that day's subagent
against the same manifest and let it write `result_path` again. Editing a result
by hand to get past a check defeats every guarantee above.

Result files are deliberately left in place after a run so a surprising worklog
entry can be traced back to the analysis that produced it.

---

## 6b. Language (roadmap §6.2)

**`language` is copied from the manifest's `language.resolved`, verbatim. It is
never a choice.** The subagent does not decide, detect, negotiate or improve on
it. Copy the tag exactly as given — `zh-TW` stays `zh-TW`, not `zh`, not
`zh-Hant`, not `Traditional Chinese`.

`analyze collect` rejects the day if `language` is missing, is not a BCP
47 tag, or is not the tag the manifest asked for. A rejected day blocks the whole
run from apply, so getting this wrong wastes the entire analysis, not just the
field.

**Write every word of prose in the resolved language.** That means `summary`,
`title`, `behavior_change`, `implementation`, `impact`, `risks`,
`maintenance_notes`, `follow_ups`, `handoff_notes`, `uncertainties` — all of it.

**The repository's language does not vote.** This is the rule most likely to be
broken, because the pull toward English is constant and every input is shouting
it: the commit messages are English, the identifiers are English, the comments
are English, this contract is English, and the code you just read is English.
None of that decides anything. A repository can be entirely English and the
manifest can say `zh-TW`, and then the worklog is Traditional Chinese. That is
not a conflict to resolve — it is the normal case.

```text
Commit message: Fix token refresh race condition
Manifest language.resolved: zh-TW

→ 修正 Token Refresh 的競態條件，避免多個更新請求同時覆寫憑證狀態。
```

**Never translate these, in any language:**

| Never translated | Example |
|---|---|
| File and directory paths | `src/auth/token_manager.py` |
| Code symbols | `refresh_token`, `TokenRefreshError` |
| Commit hashes | `4d08ee4` |
| API, class and package names | `AbortController`, `requests` |
| Branch, tag and issue references | `feat/cli-foundation`, `#42` |
| Everything in `evidence[]` | it is a citation, not prose |

Explaining a term in the resolved language is welcome; renaming the thing is not.
Write `TokenRefreshError（更新憑證時拋出的例外）`, never `更新憑證錯誤`. A reader
must be able to grep every identifier you name straight out of the worklog and
land in the code.

---

## 7. Confidence

Every `work_item` carries a `confidence`. Allowed values (plan §13.1):

- `verified` — directly provable from the code, the diff, or the tests **that you
  actually read, at the day's commit**. If you did not open it, it is not
  `verified`. A quantity you did not run a command to measure is never
  `verified` — plausibility is not proof, and a number recalled rather than
  counted has been observed to be wrong in every digit while carrying a
  `verified` label.
- `inferred` — a reasonable conclusion from context, with **no** direct proof.
- `unknown` — insufficient data to decide.

An `inferred` or `unknown` conclusion must **never** be written as an established
fact. When confidence is not `verified`, say so in the item and, where relevant,
record the gap in `uncertainties[]`.

The **day-level** `confidence` is the aggregate for the date: `verified` only when
every important conclusion is provable; drop to `inferred` or `unknown` otherwise.

**Escalation recommendation (advisory only).** Set `escalation_recommended:true`
and list machine-readable `escalation_reasons[]` when any of these hold:

- the end-of-day state after a merge cannot be confirmed,
- a revert's completeness cannot be confirmed,
- multiple commits or diffs give conflicting evidence,
- required code context could not be read,
- cross-module behaviour cannot be confirmed,
- `status` is `partial`,
- day-level `confidence` is `unknown`.

This flag is a **suggestion to the orchestrator only**. The subagent does not
switch models, and the orchestrator never escalates automatically — it may only
surface the suggestion in the dry-run and act on it after explicit user approval
(see `references/provider-models.md` → Escalation).

---

## 8. Evidence

`evidence` appears both at the top level and inside each `work_item`. **Every
entry is an object, not a sentence** — prose is not evidence, because prose
cannot be checked:

```json
{
  "commit": "abc1234",
  "file": "src/cache.py",
  "symbol": "CacheLayer.get",
  "lines": "42-58",
  "note": "新增快取查詢，miss 時回源"
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `commit` | **yes** | The commit hash this was observed in (short or full). |
| `file` | **yes** | The path, as it exists in that commit's tree. |
| `symbol` | no | Function / class / component. Omit when the change has no symbol (a config or doc file). |
| `lines` | no | Line or range within that file, e.g. `"42-58"`. |
| `note` | no | One short line on what this specific citation shows. |

`commit` and `file` are mandatory because they are the two facts that are always
knowable and always verifiable against the repository. `symbol` and `lines` are
expected wherever the change touches code — omit them only when they genuinely do
not apply, not to save effort.

This is enforced, not merely requested: `analyze collect` rejects a
result whose evidence entries are strings, or are missing `commit` or `file`
(`EVIDENCE_INVALID`), and that day is reported as failed. The reason is that a
free-text `evidence` field degrades into a restatement of the commit subject —
observed in a real run: `"commit 4d08ee4: 完整改造，加 authors[] 與 author_name"`
cites nothing a reader can open, and would satisfy any prose-based rule.

Evidence must come from **the day's tree**, read at the commit — never from the
current checkout, and never from running the project. See
`references/code-analysis-rules.md` §5a.

A conclusion with no evidence is not `verified`. If evidence cannot be gathered
(unreadable file, missing submodule, permission error), lower the confidence and
explain it in `uncertainties[]` — do not pretend the analysis was completed.

---

## 9. Day Subagent — PROMPT TEMPLATE

Fill in the bracketed inputs and hand this to each per-date subagent. The
manifest JSON is pasted inline.

```text
You are a Day Subagent for the Git Worklog skill. Analyse exactly ONE day of a
Git repository and write structured JSON to a file. You do NOT write the worklog.

HOW TO DELIVER YOUR RESULT (read this first)
Your final action MUST be a file write saving your JSON to exactly this path:
  [result_path from analyze prepare]
The file must contain ONLY the JSON object — valid parseable JSON, no markdown
fence, no prose. Do NOT put the JSON in your reply: the reply channel drops and
truncates content, and losing it would throw away your whole analysis. After
writing the file, reply with just: DONE

INPUTS
- date:               [YYYY-MM-DD]
- timezone:           [IANA tz, e.g. Asia/Taipei]
- repository root:    [absolute path]
- include_uncommitted:[true|false]   (uncommitted content belongs to TODAY only)
- provider / model:   [anthropic|openai|google] / [model_id from the manifest's model.model_id]
- output language:    [the manifest's language.resolved, e.g. zh-TW]
- output path:        [the manifest's result_path — where your JSON must be written]
- analysis manifest (from analyze prepare):
[PASTE THE MANIFEST JSON HERE, or give its file path if large]

WHAT TO DO
1. For every relevant commit in the manifest, read the ACTUAL patch:
   git show --format=fuller --find-renames --find-copies <full_hash>
   Never conclude from the subject, body, file list, or diffstat alone.
2. READ THE DAY'S TREE, NOT THE CHECKOUT. The working tree holds every change
   made since this date; it is a different repository. Read at the commit:
     git show <hash>:<path>                     (a file as it was that day)
     git ls-tree -r <hash> --name-only -- <dir> (what existed that day)
     git grep -c <pattern> <hash> -- <path>     (any count, as of that day)
   NEVER run the test suite, a build, or a linter — they measure today, and
   their output is not evidence about this day. Read test files; do not execute
   them. You may check today's state ONLY to answer "does this still exist
   now?", and you must label any such statement as today's, not the day's.
   Full rules: references/code-analysis-rules.md §5a.
3. Follow references/code-analysis-rules.md: read the full enclosing
   function/class/component of each hunk, one layer of direct callers, one layer
   of direct dependencies, and the related tests. Expand a second layer when a
   public API, schema, or shared core is touched.
4. Handle merge commits, revert candidates, and rename/copy correctly; do not
   double-count merges or read a rename as delete+add.
5. Group commits by REAL work theme, not one-per-commit. If large_day is true,
   you MAY fan out into Code Analysis Subagents grouped by work area (see the
   large-day template) — never one-commit-one-subagent.
   IF YOU FAN OUT, each Code Analysis Subagent writes to
   [the manifest's parts_dir]/[date].[group-slug].json — NOT to a path derived
   from your own output path. Your output path is inside a results directory
   that is read as the run's answers: any extra file there fails the entire run
   (`unknown`). Reconcile the parts yourself and write only your own result to
   your output path.
6. Determine the END-OF-DAY final state — the repository as it stood at this
   day's LAST commit. If a change was introduced and then reverted the same day,
   report the net result, not each intermediate step as if it were live.
7. If include_uncommitted is true, incorporate the manifest's
   uncommitted_changes as today's work.
8. COVER EVERY REQUIRED FILE. The manifest's required_commit_file_pairs marks
   each file required:true or required:false. Before you write your result, walk
   the required:true list and check that each path appears somewhere in it —
   a work_item's files[] is enough, an evidence[] citation is stronger. This is
   validated: any required file your result never mentions fails the day and
   blocks the whole run (COVERAGE_INCOMPLETE). If a required file genuinely
   needs no comment, say so in one line rather than leaving it out. Do NOT list
   a path you did not open — files[] is a claim that you read it.

OUTPUT
- WRITE to the output path above EXACTLY the Day Subagent return schema (section
  6 of the subagent contract). All keys present; empty arrays where nothing
  applies. A day with no work still writes a valid object with has_changes:false
  — never skip the write.
- LANGUAGE. Copy the output language above into the "language" field verbatim,
  and write EVERY field of prose in it — summary, title, behavior_change,
  implementation, impact, risks, maintenance_notes, follow_ups, handoff_notes,
  uncertainties. This is validated: a wrong or missing language fails the day and
  blocks the whole run. The repository's own language does NOT decide this. The
  commits, identifiers, comments and docs you are about to read may be entirely
  English while the output language is zh-TW — that is the normal case, not a
  conflict. Never translate file paths, code symbols, commit hashes, API names,
  branch/issue references, or anything in evidence[]; explaining a term in the
  output language is welcome, renaming it is not. Section 6b has the details.
- Set status (complete|partial|failed), the day-level confidence
  (verified|inferred|unknown), and escalation_recommended + escalation_reasons[]
  honestly per section 7. escalation_recommended is a SUGGESTION only — never
  switch models yourself.
- Every important conclusion cites evidence, and every evidence entry is an
  OBJECT, never a sentence:
    {"commit": "abc1234", "file": "src/cache.py", "symbol": "CacheLayer.get",
     "lines": "42-58", "note": "新增快取查詢，miss 時回源"}
  commit and file are REQUIRED; symbol and lines are expected wherever the
  change touches code. All four are CHECKED against the tree of the commit you
  cite: the commit must exist, the file must have existed at that commit, the
  symbol must appear in it, and the line range must be inside it. A citation
  that does not resolve fails the day and blocks the whole run — the same
  outcome as prose evidence, because a name that leads nowhere is worth exactly
  as much. Do not reach for a plausible name: `migrate_directory` for a function
  actually called `parse_legacy`, or `preview_dir` for `previews_dir`, reads
  perfectly and is a fabrication (#15). Read the name, then cite it. A file that
  exists in the checkout may not have existed at the commit you are citing.
- A DELETION CANNOT BE CITED. The file is gone from the tree of the commit that
  deleted it, so {"commit": <the deleting commit>, "file": <the deleted file>}
  can never resolve. To evidence what the file was, cite it at a commit where it
  still existed; to say that it was deleted, write that in prose and keep the
  path in the work item's files[] — coverage accepts that, and deletions are
  excused from coverage anyway (§5b).
- tests[] says what the tests COVER, not how many exist. Name the file and the
  behaviour it pins:
    "tests/test_git_collection.py — merge 偵測、revert 候選、rename/copy、空 repo"
  Emit NO test count: no count field, no "N 個測試", no "(N test cases)".
  Coverage is provable from the diff; a count is not.
- NEVER state a quantity you did not measure — test counts, file counts, line
  counts, percentages. A plausible number is easy to produce and looks like
  analysis; every fabricated count in a real run was wrong. If a number is truly
  needed, get it from the day's tree with a command
  (git grep -c <pattern> <hash> -- <path>) and cite it in evidence[]. Otherwise
  omit it and describe the thing instead.
- Mark each work_item's confidence honestly: verified (provable from
  code/diff/tests you actually opened at the day's commit), inferred
  (reasonable, no direct proof), unknown (insufficient data). Never state an
  inference as fact. A number you did not measure is never verified.
- Put anything unverifiable in uncertainties[]; lower confidence rather than
  guess. Do NOT fabricate symbols, files, behaviours, counts, or test results.
- Write the file, then reply with just: DONE
```

---

## 10. Code Analysis Subagent — PROMPT TEMPLATE

Spawned by a Day Subagent on a `large_day` to analyse a **single file group**.
Its result is merged back by the Day Subagent; it does not produce the day's
final object.

```text
You are a Code Analysis Subagent for the Git Worklog skill. Analyse ONE file
group within a single day and write structured findings to a file. You do NOT
write the worklog and you do NOT decide the day's final wording.

HOW TO DELIVER YOUR RESULT (read this first)
Your final action MUST be a file write saving your JSON to exactly this path:
  [the output path your parent Day Subagent gave you]
The file must contain ONLY the JSON object — no markdown fence, no prose. Do NOT
put the JSON in your reply: the reply channel drops and truncates content. After
writing the file, reply with just: DONE

INPUTS
- date:            [YYYY-MM-DD]
- timezone:        [IANA tz]
- repository root: [absolute path]
- provider / model:[same as the parent Day Subagent]
- output path:     [<parts_dir>/<date>.<group-slug>.json — given by the parent,
                    from the manifest's parts_dir. NOT next to the day's result.]
- file group (from the manifest's file_groups[]):
    group:    [e.g. backend:src/api]
    category: [feature|backend|frontend|mobile|api|database|tests|configuration|deployment|documentation]
    module:   [top-level module]
    files:    [list of paths in this group]
    commits:  [ALL commit hashes touching these files — you get every one]
- required_context for this group (read[], expand_second_layer_if, depth):
[PASTE THE MATCHING required_context ENTRY]

WHAT TO DO
1. For each commit above, read the actual patch for THIS group's files
   (git show <hash> -- <paths>).
2. READ THE DAY'S TREE, NOT THE CHECKOUT — it holds every change made since this
   date. Use `git show <hash>:<path>`, `git ls-tree -r <hash>`, and
   `git grep -c <pattern> <hash> -- <path>` rather than the working tree, and
   never run the test suite, a build, or a linter (they measure today). See
   references/code-analysis-rules.md §5a.
3. Read the full enclosing function/class/component, one layer of direct
   callers, one layer of direct dependencies, and the related tests, per
   references/code-analysis-rules.md and the `depth` given. Expand a second
   layer when expand_second_layer_if applies.
4. Determine the end-of-day state for THIS group's files, accounting for changes
   later undone within the same day.

OUTPUT
- Return work_item objects (section 6 schema) plus, where they apply,
  fixes / refactors / tests / database_changes / configuration_changes /
  deployment_changes entries for THIS group only.
- Every evidence entry is an OBJECT, never a sentence:
    {"commit": "abc1234", "file": "src/cache.py", "symbol": "CacheLayer.get",
     "lines": "42-58", "note": "..."}
  commit and file are REQUIRED; symbol and lines are expected for code changes.
- tests[] says what the tests COVER, not how many exist. Emit no test count.
- NEVER state a quantity you did not measure with a command at the day's commit.
  An unmeasured number is a fabrication even when it looks right.
- Set confidence honestly (verified / inferred / unknown); never present an
  inference as fact. Record anything unverifiable as an uncertainty.
- Do not deduplicate across other groups — the Day Subagent reconciles that.
- Write the file, then reply with just: DONE
```

A Day Subagent that fans out gives each Code Analysis Subagent a distinct path
**under the manifest's `parts_dir`**, named `<date>.<group-slug>.json`, then
reads them back and reconciles them into its own `result_path`.

**Never put a group file next to the day's result.** `parts_dir` is a separate
directory precisely because `results/` is not scratch space: `analyze collect`
reads every file there as some day's answer and fails the whole run over any it
did not ask for (`unknown`). Deriving a sibling of `result_path` puts the parts
in exactly that directory — which is what a real large-day run did, blocking the
day the fan-out existed to make tractable. Use `parts_dir` from the manifest; do
not construct a path from `result_path`.

A group file that is missing or unparseable makes the **day** `partial` (§11);
the Day Subagent must not quietly drop that group's work area.

---

## 11. Failure handling

A day fails when `analyze collect` reports it under `missing` (no file was
written), under `invalid` (unparseable, off-schema, wrong language, a citation
that does not resolve, or a required file left unmentioned), or when its own
`status` is `partial`/`failed`. A result the run never asked for lands in
`unknown` and is not merged. Then (plan §22.5):

- **Do not** substitute commit messages for the missing analysis. A failed day
  has no content, not a message-derived stand-in.
- **Other days may continue** — one failed date does not abort the run.
- **Mark the whole run partial.** `collect` sets `partial_run:true`; treat any
  missing or invalid day object as a failure of that day, never as an empty day.
- **Apply is blocked by default** for a partial run.
- **Show the failed date(s) and the reason** in the dry-run summary.
- **Fix the analysis, not the result file.** A day that failed on language,
  evidence or coverage can be re-run against the same manifest — dispatch that
  date's subagent again, let it overwrite its `result_path`, and collect once
  more. Hand-editing a result to get past a check discards the only guarantee
  these checks provide.

The user may explicitly choose to write only the successful days. That is a
**new** request, not a resumed one: re-run the dry-run over only those dates,
which mints a **new `preview_id`**. Show the updated planned changes and the new
preview id, and require confirmation again before applying (see
`references/interaction-flow.md` §9).
