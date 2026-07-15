# Worklog Format Reference

This document defines the on-disk format of the project worklog produced by the
`repo_worklog` skill: how the file is laid out, the exact marker strings that
delimit machine-updatable regions, the per-day content template, and the precise
rules for preserving human-written content while inserting or overwriting dates.

The format is not enforced by convention alone. `scripts/update_worklog.py`
applies every rule below mechanically, and `scripts/validate_worklog.py` checks
marker integrity. Both build on `scripts/worklog_markers.py`, which is the single
source of truth for parsing and serialising the format. When this document and
the scripts appear to disagree, the scripts win — but they were written to match
this specification exactly, so any disagreement is a bug to be reported, not
worked around.

---

## 1. Format choice and default location

The worklog is a **human-facing Markdown document**. Markdown is deliberately the
primary output format — not YAML, not JSON — because it:

- reads well for humans skimming for maintenance or handoff context;
- previews natively on GitHub, GitLab and IDEs;
- comfortably carries file paths, commit hashes, code and handoff notes;
- suits code review;
- can safely hold hand-written additions alongside generated text;
- supports stable HTML comments as invisible update markers.

JSON is used **only as an internal exchange format** between the coordinator and
the day subagents (see `subagent-contract.md`). It never appears in the worklog
file itself.

### Default file path

```text
docs/PROJECT_WORKLOG.md
```

If `docs/` does not exist:

- **dry-run must NOT create it.** A preview run never touches the filesystem — no
  directory, no file. `update_worklog.py` reports `target_dir_exists` so the
  preview can say the directory is still missing.
- **apply creates the directory and the file.** Only when the user confirms and
  the tool runs with `--apply` is `docs/` created (via `os.makedirs(..., exist_ok=True)`)
  and the worklog written.

---

## 2. File skeleton and exact markers

Machine-updatable regions are delimited by stable HTML comments. Every marker
uses the prefix `REPO_WORKLOG`. The marker strings are exact — spelling,
casing, colons and spacing are all significant. Do not paraphrase them.

Below `<date>` is always an ISO calendar date in `YYYY-MM-DD` form, and the
`## <date>` heading line must repeat that same date. The following is the full
skeleton for the entries region, reproduced precisely:

```markdown
<!-- REPO_WORKLOG:ENTRIES:START -->

<!-- REPO_WORKLOG:<date>:START -->
## <date>

<!-- REPO_WORKLOG:<date>:GENERATED:START -->
（自動產生內容）
<!-- REPO_WORKLOG:<date>:GENERATED:END -->

<!-- REPO_WORKLOG:<date>:MANUAL:START -->
（人工補充內容）
<!-- REPO_WORKLOG:<date>:MANUAL:END -->

<!-- REPO_WORKLOG:<date>:END -->

<!-- REPO_WORKLOG:ENTRIES:END -->
```

### Document header

When creating a new worklog, the script writes exactly this header (title plus a
three-line blockquote intro) before `ENTRIES:START`:

```markdown
# Project Worklog

> 本文件依據 Git commit、實際程式碼 diff 與相關程式碼上下文產生。
> 用於專案維護、交接與異動追蹤。
> 日期依執行環境的本地時區判定。
```

### A complete, fully-marked file

Putting the header and one populated date block together, a minimal worklog
looks like this. Dates are ordered **newest-first (descending)**, so the block
with the most recent date comes first inside the entries region:

```markdown
# Project Worklog

> 本文件依據 Git commit、實際程式碼 diff 與相關程式碼上下文產生。
> 用於專案維護、交接與異動追蹤。
> 日期依執行環境的本地時區判定。

<!-- REPO_WORKLOG:ENTRIES:START -->

<!-- REPO_WORKLOG:2026-07-15:START -->
## 2026-07-15

<!-- REPO_WORKLOG:2026-07-15:GENERATED:START -->
（自動產生內容）
<!-- REPO_WORKLOG:2026-07-15:GENERATED:END -->

<!-- REPO_WORKLOG:2026-07-15:MANUAL:START -->
（人工補充內容）
<!-- REPO_WORKLOG:2026-07-15:MANUAL:END -->

<!-- REPO_WORKLOG:2026-07-15:END -->

<!-- REPO_WORKLOG:ENTRIES:END -->
```

### Anatomy of the markers

| Marker | Role |
| --- | --- |
| `<!-- REPO_WORKLOG:ENTRIES:START -->` / `:ENTRIES:END -->` | Bound the region that holds all date blocks. Everything before START is the header; everything after END is the footer. |
| `<!-- REPO_WORKLOG:<date>:START -->` / `:<date>:END -->` | Open and close one day's block. The `## <date>` heading sits just inside START. |
| `<!-- REPO_WORKLOG:<date>:GENERATED:START -->` / `:GENERATED:END -->` | Bound the auto-generated text. **This is the only region the tool ever overwrites.** |
| `<!-- REPO_WORKLOG:<date>:MANUAL:START -->` / `:MANUAL:END -->` | Bound the human-written text. Never modified by re-analysis. |

Every date block must contain exactly one GENERATED region and exactly one
MANUAL region, in that order, with the `## <date>` heading matching the block's
date.

---

## 3. Per-day content template

The generated portion of each day (the text between the GENERATED markers) is
built from the template below. Section headings are in Traditional Chinese and
must be used verbatim. Reproduced from the plan (§16):

```markdown
## 2026-07-15

### 當日摘要

簡述當日完成的主要工作與整體影響。

### 主要異動

#### 工作主題

- **異動內容：**
- **程式碼行為：**
- **實作方式：**
- **影響範圍：**
- **相關檔案：**
- **相關 commits：**
- **測試與驗證：**
- **相容性與風險：**
- **維護注意事項：**
- **後續事項：**

### 修正項目

### 重構與技術債

### 資料庫與 Migration

### 設定、CI 與部署

### 測試狀態

### 尚未提交的異動

### 接手者快速閱讀

- 建議優先閱讀的檔案
- 關鍵流程入口
- 容易踩雷的規則
- 尚未涵蓋的測試
- 未完成或需要追蹤的項目
```

### Content rules

- **`當日摘要`** — one short paragraph on the day's main work and overall impact.
- **`主要異動`** — one `#### 工作主題` sub-block per work item. Each item fills
  the ten bullet fields: `異動內容` (what changed), `程式碼行為` (how behaviour
  changed), `實作方式` (implementation approach), `影響範圍` (scope of impact),
  `相關檔案` (related files), `相關 commits` (related commits), `測試與驗證`
  (tests and verification), `相容性與風險` (compatibility and risk),
  `維護注意事項` (maintenance notes), `後續事項` (follow-ups). Repeat the
  `#### 工作主題` block for each distinct work item.
- **`修正項目`** — bug fixes. **`重構與技術債`** — refactors and technical debt.
  **`資料庫與 Migration`** — schema and migration changes. **`設定、CI 與部署`**
  — configuration, CI and deployment. **`測試狀態`** — test status.
  **`尚未提交的異動`** — uncommitted working-tree changes (only ever for today;
  see `code-analysis-rules.md`). **`接手者快速閱讀`** — a fast-reading guide for
  whoever picks up the work.

### Omit empty sections

**Any section with no real content must be omitted entirely.** Do not emit walls
of placeholder text:

```text
無
無異動
N/A
```

A day with only a couple of small commits should produce a short block with only
the relevant sections — `當日摘要` plus one or two others — not the full skeleton
padded with `無`. The template lists what is *available*, not what is *mandatory*.

---

## 4. Manual-content preservation

Only the text between `GENERATED:START` and `GENERATED:END` is ever overwritten.
Everything else is preserved **verbatim** (byte-for-byte). Specifically, the tool
preserves:

- the **document header** — everything before `ENTRIES:START`, including the
  title, the intro blockquote, and any hand-written notes a user added there;
- **everything outside the ENTRIES region** — header and footer alike;
- **each day's MANUAL region** — the text between that day's `MANUAL:START` and
  `MANUAL:END`;
- the **document footer** — everything after `ENTRIES:END`.

Re-analysing a date **must not modify that date's MANUAL region**. The parser
copies a date block that is not being rewritten byte-for-byte, and even a
rewritten block only has its GENERATED inner text replaced — the surrounding
markers, heading and MANUAL region are left intact. On apply, `update_worklog.py`
additionally compares every MANUAL region before and after; if any would change
it aborts with `MANUAL_MUTATED` rather than write.

### What belongs in MANUAL

The MANUAL region is where humans record context that Git alone cannot express:

- Issue or ticket links.
- Decision background — why an approach was chosen.
- Deploy notes.
- Context not captured in Git.
- Handoff reminders for whoever continues the work.
- Outcomes of team discussions.

This is the user's space. The skill reads around it but never rewrites it.

---

## 5. Insert vs. overwrite

The tool takes a set of `{date: generated_markdown}` entries and, for each date,
either inserts a new block or overwrites an existing one. In both cases the date
ordering stays descending and no other date is touched.

### New date — insert

When a date does not yet exist:

1. Build a full date block (START, heading, GENERATED with the new content, an
   empty MANUAL region, END).
2. Insert it at the correct descending position.
3. Do not modify any other date.
4. Do not modify any manual content.

Worked example. Given an existing worklog with:

```text
2026-07-15
2026-07-10
```

inserting `2026-07-12` yields:

```text
2026-07-15
2026-07-12
2026-07-10
```

The new block lands between the two existing ones; both existing blocks —
including their MANUAL regions — are left exactly as they were.

### Existing date — overwrite

When a date already exists:

1. **Keep the MANUAL region** unchanged.
2. **Fully replace the GENERATED region** with the fresh analysis.
3. Do **not** append — the new content replaces the old.
4. Do **not** keep the old auto-generated summary.
5. Keep the descending date order.

Overwrite is a clean replacement of the generated text only. The surrounding
markers, the heading, and the human's MANUAL notes survive verbatim.

---

## 6. No-commit and empty days

### Days with no commits in a range

In a multi-day range, a day that has no commits is still analysed and reported by
its own day subagent (it returns `has_changes=false`). By default **no empty
section is created** for such a day — the worklog is not padded with blank
blocks. Instead, the dry-run summary lists that date as a no-change day so the
user can see it was covered.

### A previously-recorded date that now has no commits

If a date already had an entry, but re-analysis finds no visible commits for it,
the tool does **not** silently delete the block. Instead:

- Do **not** auto-delete the date block.
- Show the diff in the dry-run so the change is explicit.
- **Always keep the MANUAL region.**
- Only clear the GENERATED region after explicit user confirmation.

Deletion or clearing of previously-recorded generated content is never automatic;
it requires the user to confirm, and even then the manual notes are retained.

---

## 7. Mechanical enforcement and validation

`update_worklog.py` enforces every rule above mechanically. It:

- takes input of the form
  `{"entries": {"<date>": {"generated_markdown": "..."}}}` (via `--input FILE`
  or stdin);
- inserts new dates in descending order and overwrites only the GENERATED region
  of existing dates;
- preserves MANUAL regions, the header and the footer;
- writes atomically (same-directory temp file, re-parsed and re-validated, then
  `os.replace`), and is dry-run by default (`--apply` to actually write);
- **never runs git** — no `git add`, `commit` or `push`.

`validate_worklog.py` checks marker integrity and reports **every** issue it
finds in one pass (it does not stop at the first). **Corrupt markers are refused,
never auto-repaired** — the tool will not guess a fix. If the existing file has
corrupt markers, `update_worklog.py` aborts with `CORRUPT_MARKERS` rather than
write over ambiguous structure.

### Fatal marker problems

Any of the following makes the file invalid (`ok=false`, exit code 2). The tool
refuses to proceed and offers to point out the location, but never auto-repairs:

| Code | Meaning |
| --- | --- |
| `ENTRIES_MISSING` | No single well-formed `ENTRIES:START`/`END` pair. |
| `ENTRIES_UNBALANCED` | Duplicate or out-of-order ENTRIES markers. |
| `DATE_START_WITHOUT_END` | A date block was opened but never closed. |
| `DATE_END_WITHOUT_START` | A `:END` marker with no matching `:START`. |
| `DUPLICATE_DATE` | The same date appears in more than one block. |
| `DUPLICATE_GENERATED` | A block has more than one GENERATED region. |
| `DUPLICATE_MANUAL` | A block has more than one MANUAL region. |
| `MISSING_GENERATED` | A block has no GENERATED region. |
| `MISSING_MANUAL` | A block has no MANUAL region. |
| `HEADING_MISMATCH` | The `## <date>` heading or `:END` date disagrees with the block's date. |
| `INTERLEAVED_BLOCKS` | A new block opened before the previous one closed. |
| `STRAY_MARKER` | A GENERATED/MANUAL marker appears out of sequence. |
| `NON_UTF8` | The file is not valid UTF-8. |

`NOT_SORTED` (date blocks not in descending order) is a **warning**, not a fatal
error: the document still parses, and a normal write re-sorts entries descending.
It is surfaced so drift is visible, but it does not block the tool.
