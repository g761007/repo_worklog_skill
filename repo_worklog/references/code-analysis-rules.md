# Code Analysis Rules

Rules for a Day Subagent (or a Code Analysis Subagent it spawns) on **how to read
code** for a single day. Your job is to describe what the code actually does after
the day's changes — not to paraphrase commit messages. Follow every rule below.

**Prime directive: read the real diffs and enough surrounding code to be sure.**
A commit message tells you what the author *claims*; the patch and the code around
it tell you what actually changed. When they disagree, the code wins.

---

## 1. What you already have (from `collect_git_history.py`)

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
- the state **after the day's last commit**,
- and, when needed to confirm what actually survived, the **current version of
  that code in the repository**.

Describe what was introduced and what was later undone, and state clearly what
the code does at day's end.

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
