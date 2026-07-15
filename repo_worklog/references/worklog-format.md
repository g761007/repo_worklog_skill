# Worklog Format Reference

This document defines the on-disk format of the project worklog produced by the
`repo_worklog` skill. The worklog is a **directory**, not one growing file:

```text
PROJECT_WORKLOG/
├── index.md          # navigation: a date-descending table linking every day
├── 2026-07-15.md     # exactly one day per file
├── 2026-07-14.md
├── 2026-07-13.md
└── ...
```

Each day lives in its own `PROJECT_WORKLOG/<date>.md`. Re-analysing one day
rewrites only that day's file; no other date file is read or touched. `index.md`
is rebuilt from the day files and is pure navigation. This replaces the earlier
single `docs/PROJECT_WORKLOG.md`, which grew without bound, produced huge diffs,
and forced a full re-read on every update.

The format is enforced mechanically. `scripts/worklog_markers.py` is the single
source of truth for parsing and serialising both shapes;
`update_daily_worklog.py`, `rebuild_worklog_index.py`, `validate_daily_worklog.py`
and `validate_worklog_index.py` all build on it. When this document and the
scripts appear to disagree, the scripts win — they were written to match this
spec exactly, so any disagreement is a bug to report, not work around.

---

## 1. Format choice and location

The worklog is **human-facing Markdown**. Markdown is deliberately the primary
output — not YAML, not JSON — because it reads well for handoff, previews on
GitHub/GitLab/IDEs, carries paths, hashes and code comfortably, suits review,
and safely holds hand-written notes alongside generated text via stable HTML
comment markers. JSON is used **only** as the internal exchange format between
the coordinator and day subagents (see `subagent-contract.md`); it never appears
in the worklog files.

### Default location

```text
PROJECT_WORKLOG/            # at the repository root
PROJECT_WORKLOG/index.md
PROJECT_WORKLOG/<date>.md   # <date> is an ISO YYYY-MM-DD calendar date
```

Override the directory with `update_daily_worklog.py --dir` /
`rebuild_worklog_index.py --dir`. If `PROJECT_WORKLOG/` does not exist:

- **dry-run must NOT create it.** A preview never touches the filesystem — no
  directory, no file. The dry-run output reports `dir_exists` so the preview can
  say the directory is still missing.
- **apply creates the directory and the files.** Only when the user confirms and
  the tool runs with `--apply` is `PROJECT_WORKLOG/` created and written.

---

## 2. The per-day file

Below `<date>` is an ISO `YYYY-MM-DD` date that matches the filename. A day file
has a **tool-owned header** (title + meta blockquote), one **GENERATED** region,
and one **MANUAL** region. Marker strings are exact — spelling, casing, colons
and spacing are all significant. Do not paraphrase them.

```markdown
# Project Worklog — 2026-07-15

> 時區：Asia/Taipei
> Branch：main
> HEAD：abc1234

<!-- REPO_WORKLOG:2026-07-15:GENERATED:START -->
（自動產生內容）
<!-- REPO_WORKLOG:2026-07-15:GENERATED:END -->

<!-- REPO_WORKLOG:2026-07-15:MANUAL:START -->
（人工補充內容）
<!-- REPO_WORKLOG:2026-07-15:MANUAL:END -->
```

### Anatomy

| Part | Role |
| --- | --- |
| `# Project Worklog — <date>` | Title. The date must equal the filename's date. **Tool-owned.** |
| `> 時區：… / > Branch：… / > HEAD：…` | Meta blockquote recorded at analysis time. Lines are emitted only when a value is provided. **Tool-owned.** |
| `<!-- REPO_WORKLOG:<date>:GENERATED:START -->` / `:GENERATED:END` | Bound the auto-generated analysis. Overwritten on every re-analysis. |
| `<!-- REPO_WORKLOG:<date>:MANUAL:START -->` / `:MANUAL:END` | Bound the human notes. **Never modified by re-analysis.** |

There is no `ENTRIES` wrapper, no per-date `START`/`END` block markers, and no
`## <date>` heading inside the markers — a day file *is* the day. Every day file
has exactly one GENERATED region followed by exactly one MANUAL region, with the
markers carrying the file's own date.

### What is tool-owned vs. human-owned

Only the **MANUAL** region belongs to the human and is preserved byte-for-byte.
The title and meta blockquote are regenerated on every write, so `Branch`/`HEAD`
reflect the state at the most recent analysis. Put human context in MANUAL, not
in the header.

---

## 3. Per-day generated template

The text between the GENERATED markers is built from this template. Section
headings are Traditional Chinese and used verbatim. Because there is no `## <date>`
wrapper heading, sections sit at `##` and work items at `###`:

```markdown
## 當日摘要

簡述當日完成的主要工作與整體影響。

## 主要異動

### 工作主題

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

## 修正項目

## 重構與技術債

## 資料庫與 Migration

## 設定、CI 與部署

## 測試狀態

## 尚未提交的異動

## 接手者快速閱讀

- 建議優先閱讀的檔案
- 關鍵流程入口
- 容易踩雷的規則
- 尚未涵蓋的測試
- 未完成或需要追蹤的項目
```

### Content rules

- **`當日摘要`** — one short paragraph on the day's main work and overall impact.
  Its **first line** is what `index.md` shows for the day, so lead with the
  single most useful sentence.
- **`主要異動`** — one `### 工作主題` sub-block per work item, each filling the ten
  bullet fields (`異動內容`, `程式碼行為`, `實作方式`, `影響範圍`, `相關檔案`,
  `相關 commits`, `測試與驗證`, `相容性與風險`, `維護注意事項`, `後續事項`). Repeat
  the block per distinct work item.
- The remaining sections carry fixes, refactors/tech-debt, database/migration,
  configuration/CI/deploy, test status, uncommitted working-tree changes (today
  only — see `code-analysis-rules.md`), and a fast-reading handoff guide.

### Omit empty sections

**Any section with no real content is omitted entirely.** Do not emit walls of
`無` / `無異動` / `N/A`. A day with a couple of small commits produces a short
file — `當日摘要` plus one or two others — not the full skeleton padded with
placeholders. The template lists what is *available*, not what is *mandatory*.

### No marker lines in generated content

Because parsing is line-based, the generated body must **not** contain a line that
is itself a `REPO_WORKLOG` marker (e.g. a summary quoting the tool's own
`<!-- REPO_WORKLOG:…:MANUAL:START -->` on its own line). `update_daily_worklog.py`
refuses such input up front with `GENERATED_CONTAINS_MARKER`; rephrase or inline
the reference rather than leaving it as a bare marker line. (The same guard makes
`migrate_legacy_worklog.py` refuse a legacy block whose generated text carries a
bare marker line, rather than emit a corrupt day file.)

---

## 4. The index file

`index.md` is navigation only. Its header (title, intro blockquote, `## 工作日誌`
and `## 人工說明` headings) and its GENERATED table are tool-owned; only the
INDEX MANUAL region is human-owned.

```markdown
# Project Worklog

> 本目錄依據 Git commit、實際程式碼 diff 與相關程式碼上下文產生。
> 用於專案維護、交接與異動追蹤。
> 日期依執行環境的本地時區判定。

## 工作日誌

<!-- REPO_WORKLOG:INDEX:GENERATED:START -->
| 日期 | 摘要 |
|---|---|
| [2026-07-15](./2026-07-15.md) | 新增會員搜尋快取並補充 API 測試 |
| [2026-07-14](./2026-07-14.md) | 重構訂單狀態流程並修正退款判斷 |
| [2026-07-13](./2026-07-13.md) | 更新 CI 設定與相依套件 |
<!-- REPO_WORKLOG:INDEX:GENERATED:END -->

## 人工說明

<!-- REPO_WORKLOG:INDEX:MANUAL:START -->
可在此補充專案工作日誌的閱讀方式、重要里程碑或交接說明。
<!-- REPO_WORKLOG:INDEX:MANUAL:END -->
```

### Index rules

- **Newest first.** Rows are sorted date-descending; the most recent day is at
  the top.
- **One row per day file.** Each row links `./<date>.md` and shows that day's
  one-line summary, derived from the day's `當日摘要` (collapsed to one line,
  table-pipes escaped, length-capped).
- **Only `<date>.md` files are indexed.** `index.md` itself and any other
  Markdown (README, notes) are ignored.
- **INDEX MANUAL is preserved** verbatim across every rebuild; INDEX GENERATED is
  fully replaced.

---

## 5. Create, overwrite, and no-change

`update_daily_worklog.py` takes `{date: {generated_markdown}}` plus `meta`
(`timezone`, `branch`, `head`) and, for each target date, does one of:

### New date — create

The day file does not exist yet:

1. Build a fresh day file: title, meta blockquote, the GENERATED content, and an
   empty MANUAL region.
2. Do not read or modify any other date file.

### Existing date — overwrite

The day file exists:

1. **Keep the MANUAL region** byte-for-byte.
2. **Fully replace the GENERATED region** with the fresh analysis (never append,
   never keep the old summary).
3. Regenerate the title + meta header from the current run.
4. **Preserve any trailing content** after `MANUAL:END` verbatim (so notes a user
   appended below the MANUAL region are never silently dropped).
5. Do not read or modify any other date file.

Before writing, the tool re-parses the result and aborts with `MANUAL_MUTATED`
if the MANUAL region would change.

### No change

If the freshly-rendered day file is byte-identical to what is on disk, the action
is `no_change` and the file is left untouched (it is not rewritten).

### The index follows

After the day files are written, `rebuild_worklog_index.py` rebuilds `index.md`
from the day files on disk (preserving INDEX MANUAL). In a dry-run, pass the
pending day summaries as `{"overrides": {...}}` so the index preview reflects the
about-to-be-written state without touching disk.

---

## 6. No-commit and empty days

- **A day with no commits in a range** still gets its own day subagent, which
  returns `has_changes=false`. By default **no file is created** for it — the
  directory is not padded with empty days. The dry-run summary lists the date as
  a no-change day so the user sees it was covered.
- **A previously-recorded date that now has no commits** is **not** auto-deleted.
  Show the diff in the dry-run, always keep the MANUAL region, and only clear the
  GENERATED region after explicit user confirmation. Clearing generated content
  is never automatic.

---

## 7. Multi-file write safety

An apply must never leave some days updated and others not.

- `update_daily_worklog.py --apply` writes **all** target day files as one
  transaction: each new file is staged to a same-directory temp file and
  validated, then swapped in with `os.replace`; if any stage or swap fails, every
  already-swapped file is rolled back (originals restored, newly-created files
  removed). Either all target day files reach their new state or none do.
- `rebuild_worklog_index.py --apply` writes `index.md` atomically (temp file,
  re-parsed, `os.replace`). The index is a **pure function of the day files**, so
  if the index write ever fails after the day files succeed, the day files are
  already consistent and re-running the index rebuild repairs it with no data
  loss.
- Neither script ever runs git.

---

## 8. Validation

`validate_daily_worklog.py` checks each day file: filename is a valid
`<date>.md`, the `# Project Worklog — <date>` title matches, GENERATED and MANUAL
regions are present, unique and correctly ordered, every marker's date matches
the file's date, and the file is UTF-8. `validate_worklog_index.py` checks the
index: INDEX GENERATED/MANUAL present and unique, dates unique and descending,
every linked day file exists, and UTF-8. Both report **every** issue in one pass.

**Corrupt markers are refused, never auto-repaired.** If a target day file has
missing or corrupt markers, `update_daily_worklog.py` aborts with
`CORRUPT_MARKERS` rather than guess which text was human-written. A corrupt
existing `index.md` aborts `rebuild_worklog_index.py` with `INDEX_CORRUPT_MARKERS`
so its MANUAL region is never discarded.

### Fatal issues

Day file (`ok=false`, exit 2): `INVALID_FILENAME`, `TITLE_MISSING`,
`TITLE_DATE_MISMATCH`, `MARKER_DATE_MISMATCH`, `MISSING_GENERATED`,
`GENERATED_UNCLOSED`, `DUPLICATE_GENERATED`, `MISSING_MANUAL`, `MANUAL_UNCLOSED`,
`DUPLICATE_MANUAL`, `ORDER_MANUAL_BEFORE_GENERATED`, `NON_UTF8`.

Index (`ok=false`, exit 2): `INDEX_MISSING_GENERATED`, `INDEX_GENERATED_UNCLOSED`,
`INDEX_DUPLICATE_GENERATED`, `INDEX_MISSING_MANUAL`, `INDEX_MANUAL_UNCLOSED`,
`INDEX_DUPLICATE_MANUAL`, `INDEX_DUPLICATE_DATE`, `INDEX_LINK_MISSING`, `NON_UTF8`.

`INDEX_ORDER` (dates not descending) and `INDEX_ROW_MISSING` (a day file present
on disk but absent from the index) are **warnings** — a normal index rebuild
fixes both — so they are surfaced but do not block.

---

## 9. Migration from the legacy single file

A project that already has `docs/PROJECT_WORKLOG.md` is migrated with
`scripts/migrate_legacy_worklog.py` (or `/repo_worklog migrate`), never
automatically. It reads the legacy file, splits each date into
`PROJECT_WORKLOG/<date>.md` preserving that date's GENERATED and MANUAL text,
and builds `index.md`. It is dry-run by default, **never deletes** the legacy
file, **never overwrites** a day file that already exists (those are reported as
`skip-exists`), and **refuses** (`LEGACY_CORRUPT`) if the legacy markers are
broken. After a successful apply the user decides whether to remove the old
`docs/PROJECT_WORKLOG.md` themselves.
