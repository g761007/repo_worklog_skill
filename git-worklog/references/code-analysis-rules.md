# Code Analysis Rules

Rules for a Day Subagent (or a Code Analysis Subagent it spawns) on **how to read
code** for a single day. Your job is to describe what the code actually does after
the day's changes — not to paraphrase commit messages. Follow every rule below.

**Prime directive: read the real diffs and enough surrounding code to be sure.**
A commit message tells you what the author *claims*; the patch and the code around
it tell you what actually changed. When they disagree, the code wins.

---

## 1. What you already have (from your manifest)

The collector has already run for your day and handed you a JSON object with the
day's commits. **You do not recompute any of this** — treat it as your index into
the day's work. Per commit you are given:

- `full_hash`, `short_hash`
- `author_name` / `author_email` / `author_date`
- `committer_name` / `committer_email` / `committer_date`
- `subject`, `body`
- `parents[]`, `is_merge`, `is_revert_candidate`
- `files[]`, where each file has: `status` (A/M/D/R/C/T), `path`,
  `old_path` (rename/copy source), `similarity`, `additions`, `deletions`,
  `is_binary`, `is_submodule`, and `old_sha` / `new_sha` (submodules)
- `diffstat`: `files_changed`, `additions`, `deletions`, `has_binary`,
  `has_submodule`

Rename/copy detection (`-M -C`) and binary detection are already applied, so
trust the `status`, `old_path`, `similarity`, and `is_binary` fields. Day
attribution is decided by the **committer date** by default.

**Patches are NOT in this JSON.** The collector deliberately omits the diff text
to keep the payload bounded. You read the patch yourself — see §2. Merge commits
arrive with `files: []` for the same reason (§6.1); read the merge with `git show`.

---

## 2. Mandatory diff reading

For every commit relevant to your day, read its **actual patch** — the equivalent
of:

```bash
git show --format=fuller --find-renames --find-copies <full_hash>
```

`--format=fuller` shows both author and committer identity/date;
`--find-renames --find-copies` make the patch agree with the rename/copy-aware
file list you were given. Read the added/removed lines, not just the headers.

The following are **background and index only**. None of them may substitute for
reading the diff:

- the commit `subject` or `body`
- the changed-file list (`files[]`)
- the `diffstat` (files changed, +/- line counts)

**Forbidden:** summarising a commit from `git log --oneline`, from the commit
message alone, or from the diffstat alone. If your description of a change could
have been written without opening the patch, you have not done the analysis.

---

## 3. Read the surrounding code, not just the hunk

A diff shows a few changed lines with a little context. That is rarely enough to
state what the behaviour now is. For every **important** change, read the full
enclosing semantic unit and the code that touches it:

- the complete enclosing **function / method**, **class**, **component**
- the **interface / type**, **route**, **controller**, **service**,
  **repository / data-access layer**
- the **schema**, **migration**, **feature flag**, **configuration** involved
- the change's **direct callers**, its **direct dependencies**, and the
  **related tests**

Do not conclude from only a handful of diff lines. If a function's signature,
return value, error path, or a shared constant changed, you must see who relies
on it before you can describe the impact.

---

## 4. Context-expansion strategy (ordered, bounded)

Start from the changed files and expand outward **in this order**, stopping as
soon as the evidence is sufficient:

1. **Full semantic unit** — read the whole function/class/component/etc. that
   contains each changed hunk.
2. **One layer of direct dependencies** — the code this unit calls into.
3. **One layer of direct callers** — the code that calls this unit.
4. **Corresponding tests** — unit/integration tests that exercise the change.
5. **A second layer — only when** the change touches a **public API**, a
   **schema/migration**, or a **core shared component**. Otherwise stop at layer one.
6. **If evidence is still insufficient**, stop and mark the uncertainty
   (`confidence: inferred` or `unknown`, with a note in `uncertainties`). Do not
   guess.

Two hard limits:

- **Never** scan the whole repository unboundedly. Expand along real edges
  (callers, dependencies, tests), not by reading everything.
- **Never** skip required reading to save model cost. Reading only the diff to be
  cheap is exactly the failure this skill exists to prevent.

---

## 5. Report the end-of-day final state, not each commit

When several commits touch the **same feature on the same day**, report the
**net state at end of day** — not each intermediate commit as if it were still a
live change.

Worked example. In one day:

```
Commit A: 加入快取 (adds a cache)
Commit B: 修正快取 key (fixes the cache key)
Commit C: 撤回快取 (reverts the cache)
```

The end-of-day code retains **no** caching behaviour, so the log must say so —
not list three separate still-effective changes:

```
當日曾導入快取機制，但後續已撤回；當日結束時的程式碼未保留該快取行為。
```

To determine the net state, compare:

- the state **before the day's first commit**,
- the **intermediate commits** in between,
- the state **after the day's last commit**.

Describe what was introduced and what was later undone, and state clearly what
the code does at day's end.

---

## 5a. Read the day's tree, not the checkout

"End of day" means **the repository as it stood at that day's last commit**. The
working tree in front of you is a different repository: it carries every change
made since, which for a historical day can be weeks of work.

This is the easiest rule in this document to break, because reading the checkout
is the most convenient thing available. A real run broke it: a subagent analysing
2026-07-15 ran the current test suite and recorded "Ran 132 tests" as that day's
result. The suite had **44** tests that day. The number was real — it was just
measuring the wrong repository.

**Read at the commit, always:**

| You want | Use | Never |
| --- | --- | --- |
| A commit's patch | `git show --format=fuller --find-renames --find-copies <hash>` | — |
| A file as it was that day | `git show <hash>:<path>` | opening the file from the working tree |
| What files existed that day | `git ls-tree -r <hash> --name-only -- <dir>` | `ls <dir>` |
| A count that day (tests, files, symbols) | `git grep -c <pattern> <hash> -- <path>` | grepping the working tree |
| Whether a symbol existed that day | `git grep -n <symbol> <hash> -- <path>` | grepping the working tree |

Use the day's **last** commit as `<hash>` for end-of-day questions, and the
specific commit for questions about that commit.

**Never run the project.** Do not run the test suite, a build, a linter, or any
command that executes the current checkout. They all measure today, and their
output is not evidence about a past day. Read test files to see what they cover
(§3); do not execute them. "The tests pass" is not a claim this analysis makes.

**Consulting today is allowed for exactly one question:** *does this change still
exist now?* That can be worth noting for a handoff. When you do, say so
explicitly — "當日新增的快取層目前仍在（截至分析時）" — and never let today's
state stand in for the day's. A statement without that marker is a claim about
the day, and must come from the day's tree.

If the day's tree cannot answer something, that is an `uncertainties[]` entry and
a lower `confidence` — not a licence to measure the checkout instead.

### Prefer not to count at all

The table above shows how to get a count honestly, but the better move is
usually not to state one. A re-run under this rule stopped measuring the wrong
repository and started **inventing numbers instead**: every per-file test count
it reported was wrong (13/12/10/8 against an actual 12/18/13/1), and it labelled
them `verified`. Closing one route to an unverified number simply opened another.

So: a worklog reader needs to know **what a test protects**, not how many tests
exist — and the first is provable from the diff while the second is exactly the
kind of plausible-looking detail that gets invented. Describe coverage; omit the
count. If a number is genuinely load-bearing, run the command, and cite the
commit and file in `evidence[]` so a reader can re-run it. Never write a number
you did not measure — being close is still fabrication.

---

## 6. Special situations

### 6.1 Merge commits

The collector returns `files: []` for merges (the combined diff is omitted to
avoid double-counting against the parents), so **read the merge yourself** with
`git show <merge_hash>`. Do not re-count changes already attributed to the parent
commits. Still analyze what only the merge introduces:

- **conflict resolution** — code that exists in neither parent,
- **squash-merge results** — the whole squashed change set,
- any code or behaviour that appears **only** in the merge result.

### 6.2 Revert

Never write just `Revert abc123`. Determine, from the reverted patch and the
current code:

- exactly **which behaviour** was undone,
- whether it was **fully** reverted or only partially,
- whether any **residual code** from the original change remains,
- the **end-of-day state** of that behaviour.

### 6.3 Rename / copy

Rename and copy detection is already on (`status` R/C, with `old_path` and
`similarity`). Describe file **moves**, module **reorganisation**, and
**renames** as such, and state whether **real behaviour also changed** alongside
the move. Do **not** misread a rename/copy as an unrelated delete + add.

### 6.4 Binary files

For a binary change record: the **path**, whether it was **added / modified /
deleted**, its **size or Git metadata**, and its **likely usage** in the project.
**Never claim to understand the binary's contents** — you cannot read them from
the diff.

### 6.5 Generated files

**Prefer analyzing the source** that produces the generated output. Detail the
generated output itself **only** when: it affects **runtime**, it affects
**deployment**, the **source is unavailable**, or the generated artifact **is
itself the deliverable**.

### 6.6 Lockfiles

Do **not** read lockfiles line by line. Summarise only:

- **direct** dependencies added or removed,
- direct-dependency **version bumps**, calling out **major** version changes,
- **large transitive** dependency churn,
- any **build or runtime risk** implied by the change.

### 6.7 Submodules

When a submodule revision changes, record: the submodule **path**, the **old
revision** and **new revision** (from `old_sha` / `new_sha`), whether the
submodule content is **available** to you, and whether you actually **analyzed**
it. If the submodule content is not available, **never guess** what changed
inside it — say it was not analyzed and lower confidence accordingly.

### 6.8 Self-referential worklog commits

The collector already drops commits that touch **only** the worklog output
directory (`.git-worklog/` by default — the `WORKLOG_DIRNAME` constant in
`git_worklog/markers.py`) before you ever see them: e.g. a
`chore(docs): 補充 XX 專案工作日誌` commit that only edits day files and
`index.md`. Such a commit never appears in `commits[]`, is never counted in
`commit_count`, and cannot make a day `has_changes:true` on its own. A commit
that touches the worklog directory **and** real files is kept, with only the
worklog-directory files stripped from its `files[]`. You will therefore never be
asked to summarise "today I wrote the worklog" — if a day's only commits were
worklog output, the manifest reports `has_changes:false` and, per §6 of
`references/worklog-format.md`, no file is created for it.
