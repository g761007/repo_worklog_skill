# Subagent contract

The division of labour between the main orchestrator and the per-day subagents,
the exact JSON a Day Subagent must return, and how a failed day is handled. Multi
-day ranges are split one Day Subagent per date; a large single day may fan out
further into Code Analysis Subagents. Subagents analyse and return structured
data — they never touch the worklog.

The reading discipline a subagent must follow (real diffs, context expansion,
merge/revert/rename handling, end-of-day state) lives in
`references/code-analysis-rules.md`. Manifest construction lives in
`scripts/build_analysis_manifest.py`. This file is the interface between them.

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
- result-completeness checks (every dispatched date returned a valid object),
- cross-day deduplication,
- Markdown generation,
- dry-run, preview management, and writing (`update_worklog.py`,
  `preview_state.py`).

The orchestrator **must not** use a commit `subject` in place of a subagent's
code analysis. If a day has no analysis, that day has no content — it is not
back-filled from commit messages.

**Day Subagent.** Handles **exactly one date** and **never writes the worklog**.
It reads Git and code, then returns one structured JSON object (section 6). A day
with no commits and no in-scope uncommitted changes still returns a valid object
with `has_changes:false` — it is never silently dropped.

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

Each Day Subagent is handed one manifest, produced by
`scripts/build_analysis_manifest.py`. The orchestrator has already confirmed the
builder returned `ok:true` before dispatch. The manifest is a **planning aid**:
it groups files and proposes what to read; it never contains patches and never
decides worklog wording.

Fields:

| Field | Type | Meaning |
|-------|------|---------|
| `date` | `"YYYY-MM-DD"` | The single day this manifest covers. |
| `timezone` | string | Resolved IANA timezone. |
| `include_uncommitted` | bool | Whether working-tree changes are in scope (today only). |
| `provider` | string | `claude_code` / `codex` / `gemini`. |
| `model` | string | Runtime model id for that provider (section 4). |
| `has_changes` | bool | True if there are commits or in-scope uncommitted changes. |
| `commit_count` | int | Number of commits on the day. |
| `commits[]` | array | `{short_hash, full_hash, subject, is_merge, is_revert_candidate}`. |
| `changed_files[]` | array | `{path, statuses, category, is_binary, is_submodule, old_path, commits}`. |
| `file_groups[]` | array | `{group, category, module, files, commits, has_binary, has_submodule}`. |
| `required_context[]` | array | `{group, category, read[], expand_second_layer_if, depth}`. |
| `uncommitted_changes[]` | array | Working-tree entries (present only when `include_uncommitted`). |
| `large_day` | bool | True when the day is big enough to warrant fan-out (section 5). |
| `recommended_code_analysis_subagents` | int | Suggested fan-out count (0 when not large). |

`commits[].subject` is an **index and background only**. The subagent must still
read each relevant patch and the surrounding code before drawing conclusions.

---

## 4. Provider / model per host

The orchestrator sets `provider` and `model` on the manifest according to the
host it is running under:

| Host | `provider` | `model` |
|------|-----------|---------|
| Claude Code | `claude_code` | `claude-sonnet-5` |
| Codex | `codex` | `gpt-5.6-terra` (5.6 Terra) |
| Gemini | `gemini` | `gemini-flash-3.0` (Flash 3.0) |

A Day Subagent runs on, and spawns any Code Analysis Subagents on, the same
provider/model it was given.

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

The Day Subagent returns exactly this object. All keys are present even when the
array is empty. No prose outside the JSON.

```json
{
  "date": "YYYY-MM-DD",
  "timezone": "Asia/Taipei",
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
  valid object).
- `uncommitted_changes[]` is populated only when `include_uncommitted` is set and
  is attributed to today only.
- Anything the subagent could not verify goes in `uncertainties[]`, not into a
  `work_item` phrased as fact.

---

## 7. Confidence

Every `work_item` carries a `confidence`. Allowed values (plan §13.1):

- `verified` — directly provable from the code, the diff, or the tests.
- `inferred` — a reasonable conclusion from context, with **no** direct proof.
- `unknown` — insufficient data to decide.

An `inferred` or `unknown` conclusion must **never** be written as an established
fact. When confidence is not `verified`, say so in the item and, where relevant,
record the gap in `uncertainties[]`.

---

## 8. Evidence

`evidence` appears both at the top level and inside each `work_item`. Every
important conclusion must cite, at minimum (plan §13.2):

- the commit hash,
- the file path,
- the symbol / function name,
- the relevant diff or code region,
- the test file (when a test backs the claim),
- line numbers or ranges where relevant.

A conclusion with no evidence is not `verified`. If evidence cannot be gathered
(unreadable file, missing submodule, permission error), lower the confidence and
explain it in `uncertainties[]` — do not pretend the analysis was completed.

---

## 9. Day Subagent — PROMPT TEMPLATE

Fill in the bracketed inputs and hand this to each per-date subagent. The
manifest JSON is pasted inline.

```text
You are a Day Subagent for the repo_worklog skill. Analyse exactly ONE day of a
Git repository and return structured JSON. You do NOT write the worklog.

INPUTS
- date:               [YYYY-MM-DD]
- timezone:           [IANA tz, e.g. Asia/Taipei]
- repository root:    [absolute path]
- include_uncommitted:[true|false]   (uncommitted content belongs to TODAY only)
- provider / model:   [claude_code|codex|gemini] / [claude-sonnet-5|gpt-5.6-terra|gemini-flash-3.0]
- analysis manifest (from build_analysis_manifest.py):
[PASTE THE MANIFEST JSON HERE]

WHAT TO DO
1. For every relevant commit in the manifest, read the ACTUAL patch:
   git show --format=fuller --find-renames --find-copies <full_hash>
   Never conclude from the subject, body, file list, or diffstat alone.
2. Follow references/code-analysis-rules.md: read the full enclosing
   function/class/component of each hunk, one layer of direct callers, one layer
   of direct dependencies, and the related tests. Expand a second layer when a
   public API, schema, or shared core is touched.
3. Handle merge commits, revert candidates, and rename/copy correctly; do not
   double-count merges or read a rename as delete+add.
4. Group commits by REAL work theme, not one-per-commit. If large_day is true,
   you MAY fan out into Code Analysis Subagents grouped by work area (see the
   large-day template) — never one-commit-one-subagent.
5. Determine the END-OF-DAY final state. If a change was introduced and then
   reverted the same day, report the net result, not each intermediate step as
   if it were live.
6. If include_uncommitted is true, incorporate the manifest's
   uncommitted_changes as today's work.

OUTPUT
- Return EXACTLY the Day Subagent return schema (section 6 of the subagent
  contract). All keys present; empty arrays where nothing applies. A day with no
  work returns has_changes:false.
- Every important conclusion cites evidence: commit hash, file path,
  symbol/function, diff/code region, test file, and line numbers/ranges where
  relevant.
- Mark each work_item's confidence honestly: verified (provable from
  code/diff/tests), inferred (reasonable, no direct proof), unknown (insufficient
  data). Never state an inference as fact.
- Put anything unverifiable in uncertainties[]; lower confidence rather than
  guess. Do NOT fabricate symbols, files, behaviours, or test results.
- Respond with the JSON object only — no surrounding prose.
```

---

## 10. Code Analysis Subagent — PROMPT TEMPLATE

Spawned by a Day Subagent on a `large_day` to analyse a **single file group**.
Its result is merged back by the Day Subagent; it does not produce the day's
final object.

```text
You are a Code Analysis Subagent for the repo_worklog skill. Analyse ONE file
group within a single day and return structured findings. You do NOT write the
worklog and you do NOT decide the day's final wording.

INPUTS
- date:            [YYYY-MM-DD]
- timezone:        [IANA tz]
- repository root: [absolute path]
- provider / model:[same as the parent Day Subagent]
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
2. Read the full enclosing function/class/component, one layer of direct
   callers, one layer of direct dependencies, and the related tests, per
   references/code-analysis-rules.md and the `depth` given. Expand a second
   layer when expand_second_layer_if applies.
3. Determine the end-of-day state for THIS group's files, accounting for changes
   later undone within the same day.

OUTPUT
- Return work_item objects (section 6 schema) plus, where they apply,
  fixes / refactors / tests / database_changes / configuration_changes /
  deployment_changes entries for THIS group only.
- Cite evidence for each conclusion: commit hash, file path, symbol/function,
  diff/code region, test file, line numbers/ranges.
- Set confidence honestly (verified / inferred / unknown); never present an
  inference as fact. Record anything unverifiable as an uncertainty.
- Respond with the JSON only. Do not deduplicate across other groups — the Day
  Subagent reconciles that.
```

---

## 11. Failure handling

If a day's subagent fails (plan §22.5):

- **Do not** substitute commit messages for the missing analysis. A failed day
  has no content, not a message-derived stand-in.
- **Other days may continue** — one failed date does not abort the run.
- **Mark the whole run partial.** The orchestrator's completeness check treats
  any missing or invalid day object as a failure of that day.
- **Apply is blocked by default** for a partial run.
- **Show the failed date(s) and the reason** in the dry-run summary.

The user may explicitly choose to write only the successful days. That is a
**new** request, not a resumed one: re-run the dry-run over only those dates,
which mints a **new `preview_id`**. Show the updated planned changes and the new
preview id, and require confirmation again before applying (see
`references/interaction-flow.md` §9).
