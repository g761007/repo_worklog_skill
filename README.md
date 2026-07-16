# Git Worklog

A portable agent **skill** that turns a Git repository's real code history into a
human-readable, per-day **project worklog** under `PROJECT_WORKLOG/` — one
Markdown file per day plus an `index.md` that links them newest-first.

It reads the actual diffs and surrounding code — never just commit messages —
analyzes each day with its own subagent, previews every change as a dry-run, and
only writes after you explicitly confirm. It logs the whole project's history
(every author), and it never runs `git add/commit/push`.

**Languages:** [English](#english) · [繁體中文](#繁體中文說明)

---

## English

### Layout

```
git-worklog/                  # the skill (this whole directory is the skill)
├── SKILL.md                  # control layer: triggers, flow, script/reference map
├── agents/
│   └── openai.yaml           # host manifest: display name, UI metadata, model_config pointer
├── config/
│   └── provider_models.json  # single source of truth for per-host subagent models
├── scripts/                  # deterministic Python helpers (stdlib only)
│   ├── resolve_provider_model.py    # resolve per-host provider/model (overrides, escalation, halt-and-ask)
│   ├── resolve_date_range.py        # date/timezone parsing, day-span cap, per-day bounds
│   ├── resolve_ref_range.py         # report mode: tag/ref -> authoritative commit set
│   ├── check_worklog_coverage.py    # report mode: per-date covered / gap / no-commits
│   ├── collect_git_history.py      # repo metadata + per-day commit facts (no summaries)
│   ├── inspect_worktree.py         # staged/unstaged/untracked + worktree fingerprint
│   ├── build_analysis_manifest.py  # group changed files, propose reading, flag big days
│   ├── collect_day_results.py      # file-based subagent result exchange + schema validation
│   ├── update_daily_worklog.py     # create/overwrite per-day files (transactional); preserve MANUAL
│   ├── rebuild_worklog_index.py    # rebuild index.md from day files; preserve index MANUAL
│   ├── validate_daily_worklog.py   # per-day file marker/title/UTF-8 validation
│   ├── validate_worklog_index.py   # index marker/order/link/UTF-8 validation
│   ├── preview_state.py            # multi-file preview fingerprint, apply-time consistency
│   ├── migrate_legacy_worklog.py   # one-time split of the legacy single file
│   └── worklog_markers.py          # shared day/index parser/serialiser
└── references/               # detailed specs the skill loads on demand
    ├── report-mode.md
    ├── interaction-flow.md
    ├── date-parameter-contract.md
    ├── code-analysis-rules.md
    ├── subagent-contract.md
    ├── worklog-format.md
    └── provider-models.md

docs/
├── naming-conventions.md     # canonical names: brand, skill, CLI, package, directories
└── plans/                    # design plans, newest last (yyyy-MM-dd-<topic>.md)
    ├── 2026-07-15-repo-worklog-skill-design.md   # the original spec (single-file era)
    ├── 2026-07-16-commit-author-and-report-mode.md
    └── 2026-07-16-git-worklog-v1-roadmap.md      # the v1.0 rebrand + refactor roadmap
```

The worklog itself is written to `PROJECT_WORKLOG/` at the repository root:

```
PROJECT_WORKLOG/
├── index.md          # navigation: a date-descending table linking every day
├── 2026-07-15.md     # exactly one day per file
├── 2026-07-14.md
└── ...
```

### Requirements

- **Python 3.9+** (uses `zoneinfo`; developed and tested on 3.14). Standard
  library only — no third-party packages.
- **Git 2.37+** (uses `git log --since-as-filter`; developed on 2.54).
- A host that supports agent skills (Claude Code, and — via `agents/openai.yaml`
  — Codex / Gemini).

### Installation

This directory *is* the skill. Install it by placing the `git-worklog/` folder
where your host discovers skills, for example for Claude Code:

```bash
# user-level
cp -r git-worklog ~/.claude/skills/git-worklog
# or project-level, inside a target repo
cp -r git-worklog <your-project>/.claude/skills/git-worklog
```

Prefer a symlink while developing, so edits take effect immediately:

```bash
ln -s "$(pwd)/git-worklog" ~/.claude/skills/git-worklog
```

Then invoke it with `/git-worklog` (or natural language like “整理最近 7 天”).

### Usage

Run with no arguments to get the range menu; the skill does nothing until you
pick a range:

```
/git-worklog
```

Or drive it directly / in natural language:

```
/git-worklog days=7
/git-worklog date=2026-07-01
/git-worklog from=2026-07-01 to=2026-07-10
/git-worklog date=2026-07-15 include_uncommitted=true
整理最近 7 天，包含目前還沒有 commit 的修改
```

Every valid request produces a **dry-run preview** with a `preview_id`. Confirm
with “寫入” / “確認更新” / `apply <preview_id>` to write. See
`git-worklog/references/interaction-flow.md` for the full flow.

### Reporting from the worklog

Once days are logged, ask questions instead of building files. Reporting is
**read-only** — the answer comes back in the conversation, nothing is written, so
there is no dry-run to confirm:

```
整理上一週工作摘要
整理 v1.0.1 CHANGELOG
我要交接，整理最近一個月的重點與待辦
Daniel 上個月做了什麼
會員搜尋這功能是怎麼演進的
目前累積哪些技術債與待追蹤事項
```

Reports are built from the day files, so they inherit their analysis rather than
re-deriving it. If a date in the range has commits but no worklog, the skill says
so and offers to fill it in first — it never quietly downgrades to summarising
commit messages. (A date with no commits has no file by design, and is not
treated as missing.)

Version scopes are resolved by commit set, not by date: `v1.0.1` means
`git log v1.0.0..v1.0.1`, because a day file can cover commits outside a release
and a cherry-pick can land outside its dates. See
`git-worklog/references/report-mode.md`.

Already have a legacy single-file `docs/PROJECT_WORKLOG.md`? Migrate it once with
`/git-worklog migrate` (or `python3 scripts/migrate_legacy_worklog.py`). It
splits each date into `PROJECT_WORKLOG/<date>.md`, previews first, never deletes
the old file, and refuses if the legacy markers are corrupt.

### Configuration

- **Target directory:** defaults to `PROJECT_WORKLOG/` at the repo root
  (`update_daily_worklog.py --dir` / `rebuild_worklog_index.py --dir` to override).
- **Timezone:** auto-detected (`$TZ` → `/etc/localtime` → offset); override with
  `resolve_date_range.py --timezone Asia/Taipei`.
- **Subagent models:** defined once in `git-worklog/config/provider_models.json`
  (cost-first defaults — Claude Haiku 4.5 / GPT-5.6 Luna / Gemini 3.5 Flash) and
  resolved per host by `resolve_provider_model.py`. Override with
  `REPO_WORKLOG_{ANTHROPIC,OPENAI,GOOGLE}_MODEL` or an explicit `--model`. See
  `references/provider-models.md`.
- **Preview state:** stored outside the repo in `~/.repo_worklog/previews/`.
- **Subagent results:** each Day Subagent writes its analysis to
  `~/.repo_worklog/analysis/<run_id>/<date>.json` rather than returning it as
  reply text, which drops and truncates. Files are kept after the run, so a
  surprising worklog entry can be traced to the analysis behind it.

### Development commands

Each script is standalone and prints one JSON object to stdout:

```bash
cd git-worklog
python3 scripts/resolve_date_range.py --days 7 --timezone Asia/Taipei --today 2026-07-15
python3 scripts/collect_git_history.py --repo /path/to/repo --info-only
python3 scripts/update_daily_worklog.py --dir /tmp/PROJECT_WORKLOG <<'JSON'
{"meta": {"timezone": "Asia/Taipei", "branch": "main", "head": "abc1234"},
 "entries": {"2026-07-15": {"generated_markdown": "## 當日摘要\n\n..."}}}
JSON
python3 scripts/rebuild_worklog_index.py --dir /tmp/PROJECT_WORKLOG
python3 scripts/validate_daily_worklog.py --dir /tmp/PROJECT_WORKLOG
python3 scripts/validate_worklog_index.py --dir /tmp/PROJECT_WORKLOG
```

### Tests

A stdlib-only `unittest` suite lives in `tests/` (no third-party dependencies).
Run it from the project root:

```bash
python3 -m unittest discover -s tests -v
```

It builds a controlled Git fixture (single/multi-commit days, revert, rename,
binary, empty repo) and covers the date contract, Git collection, worktree
inspection, the analysis manifest, the day-file engine (create, overwrite,
MANUAL preservation, no-change, corruption refusal), the index rebuild (summaries,
ordering, MANUAL preservation), both validators, multi-file preview consistency,
legacy migration, and the full deterministic pipeline. CI runs the same suite
(see `.github/workflows/ci.yml`).
`docs/plans/2026-07-15-repo-worklog-skill-design.md` section 27 lists the broader
acceptance-test matrix.

### Safety model

- Dry-run first, always; nothing is written without explicit confirmation.
- One file per day: re-analysing a day rewrites only that day's file; every other
  day file is left byte-for-byte untouched.
- Each day's `MANUAL` region and the index's `MANUAL` region are preserved
  verbatim; only the generated regions are overwritten.
- Day-file writes are transactional (all target days stage, validate, and swap in
  atomically, with rollback), so a failed run never leaves some days updated and
  others not. The index is written atomically and is always reconstructable from
  the day files.
- Applies are gated by a multi-file preview fingerprint (repo/branch/HEAD/worktree
  + index hash + each day-file hash + the directory listing); a stale or
  already-used preview is refused.
- The skill never runs `git add/commit/push/fetch/pull/checkout/switch/merge/rebase`.

### License

Released under the [MIT License](LICENSE).

---

## 繁體中文說明

**Git Worklog** 是一個可攜的 agent **skill**，把 Git repository 的**實際程式碼歷史**
整理成方便人閱讀、**逐日**的**專案工作日誌**，寫在 `PROJECT_WORKLOG/` 目錄下——
每天一個 Markdown 檔，另有一份 `index.md` 依日期由新到舊連結各日。

它會閱讀**真正的 diff 與周邊程式碼**——不是只看 commit message——每一天各由一個
subagent 分析，所有變更都先以 dry-run 預覽，**經你明確確認後才寫入**。它記錄整個專案的
歷史（不分作者），而且**絕不執行** `git add/commit/push`。

### 目錄結構

- `git-worklog/`：整個目錄就是 skill 本體。
  - `SKILL.md`：控制層——觸發條件、流程、腳本與 references 對照。
  - `agents/openai.yaml`：宿主 manifest——顯示名稱、UI metadata、model_config 指標。
  - `config/provider_models.json`：逐宿主 subagent 模型的**單一設定來源**。
  - `scripts/`：確定性 Python 腳本（僅用標準庫，各自輸出單一 JSON）。
    - `resolve_provider_model.py`：依宿主解析 provider／模型（覆寫、escalation、halt-and-ask）。
    - `resolve_date_range.py`：日期／時區解析、日數上限（`--max-days`，預設 30）、逐日半開區間。
    - `resolve_ref_range.py`：報告模式——把 tag／ref 解析成權威的 commit 集合與對應日期。
    - `check_worklog_coverage.py`：報告模式——逐日覆蓋狀態（covered／gap／no-commits）。
    - `collect_git_history.py`：repo 中繼資料與逐日 commit 事實（不摘要、不依作者過濾）。
    - `inspect_worktree.py`：staged／unstaged／untracked 與 worktree 指紋。
    - `build_analysis_manifest.py`：檔案分組、所需上下文建議、大日標記。
    - `collect_day_results.py`：以檔案交換 subagent 分析結果並驗證 schema（缺檔／格式錯 → 該日視為失敗）。
    - `update_daily_worklog.py`：建立／覆蓋每日檔案（交易式）、保留 MANUAL。
    - `rebuild_worklog_index.py`：由日期檔重建 index.md、保留索引 MANUAL。
    - `validate_daily_worklog.py`：每日檔案的標記／標題／UTF-8 驗證。
    - `validate_worklog_index.py`：索引標記／排序／連結／UTF-8 驗證。
    - `preview_state.py`：多檔 preview 指紋、apply 前一致性、防重複套用。
    - `migrate_legacy_worklog.py`：一次性把舊單檔拆成目錄式。
    - `worklog_markers.py`：共用的日期檔／索引解析／序列化模組。
  - `references/`：skill 依需求載入的詳細規格（報告模式、互動流程、日期契約、
    程式碼分析規則、subagent 契約、工作日誌格式、模型設定）。
- `docs/naming-conventions.md`：品牌、skill、CLI、package 與目錄的正式命名對照。
- `docs/plans/`：設計計畫，檔名格式 `yyyy-MM-dd-<主題>.md`。
  - `2026-07-15-repo-worklog-skill-design.md`：原始設計規格（單檔時代）。
  - `2026-07-16-commit-author-and-report-mode.md`：commit 作者與報告模式。
  - `2026-07-16-git-worklog-v1-roadmap.md`：v1.0 更名與重構路線圖。

工作日誌本身寫在 repository 根目錄的 `PROJECT_WORKLOG/`：

```
PROJECT_WORKLOG/
├── index.md          # 導航：依日期由新到舊連結各日
├── 2026-07-15.md     # 一天一個檔
├── 2026-07-14.md
└── ...
```

### 需求環境

- **Python 3.9+**（使用 `zoneinfo`；於 3.14 開發測試）。僅標準庫，無第三方套件。
- **Git 2.37+**（使用 `git log --since-as-filter`；於 2.54 開發）。
- 支援 agent skill 的宿主（Claude Code；透過 `agents/openai.yaml` 也適用 Codex／Gemini）。

### 安裝

這個目錄本身就是 skill，把 `git-worklog/` 放到宿主會探索 skill 的位置即可。以 Claude Code 為例：

```bash
# 使用者層級
cp -r git-worklog ~/.claude/skills/git-worklog
# 或專案層級（放進目標 repo）
cp -r git-worklog <your-project>/.claude/skills/git-worklog
```

開發時建議改用**符號連結**，修改能即時生效：

```bash
ln -s "$(pwd)/git-worklog" ~/.claude/skills/git-worklog
```

之後以 `/git-worklog` 或自然語言（例如「整理最近 7 天」）呼叫。

### 使用方式

無參數呼叫時只會顯示範圍選單，**在你選擇範圍前不做任何分析**：

```
/git-worklog
```

也可直接帶參數或用自然語言：

```
/git-worklog days=7
/git-worklog date=2026-07-01
/git-worklog from=2026-07-01 to=2026-07-10
/git-worklog date=2026-07-15 include_uncommitted=true
整理最近 7 天，包含目前還沒有 commit 的修改
```

任何有效請求都會先產生 **dry-run 預覽**與一個 `preview_id`，以「寫入」／「確認更新」／
`apply <preview_id>` 確認後才會寫入。完整流程見 `git-worklog/references/interaction-flow.md`。

### 從工作日誌產生報告

日誌累積之後，可以直接提問，而不是再產生檔案。報告模式是**唯讀**的——答案直接回在對話裡，
不寫任何檔案，因此沒有 dry-run 需要確認：

```
整理上一週工作摘要
整理 v1.0.1 CHANGELOG
我要交接，整理最近一個月的重點與待辦
Daniel 上個月做了什麼
會員搜尋這功能是怎麼演進的
目前累積哪些技術債與待追蹤事項
```

報告是從每日檔案產生的，直接沿用既有分析，不重新推導。若範圍內某天有 commit 卻沒有日誌，
skill 會明講並詢問是否先補齊——**絕不默默降級成摘要 commit message**。（沒有 commit 的日期
本來就不該有檔案，不會被當成缺漏。）

版本範圍以 **commit 集合**界定，不是日期：`v1.0.1` 指的是 `git log v1.0.0..v1.0.1`——
因為某天的日誌可能涵蓋不屬於該版本的 commit，而 cherry-pick 的 commit 也可能落在日期區間外。
詳見 `git-worklog/references/report-mode.md`。

若專案已有舊的單檔 `docs/PROJECT_WORKLOG.md`，可用 `/git-worklog migrate`
（或 `python3 scripts/migrate_legacy_worklog.py`）一次性遷移：它會把每個日期拆成
`PROJECT_WORKLOG/<date>.md`，先預覽、絕不刪除舊檔，舊標記損壞時則拒絕遷移。

### 設定

- **輸出目錄**：預設為 repo 根目錄的 `PROJECT_WORKLOG/`（以
  `update_daily_worklog.py --dir` ／ `rebuild_worklog_index.py --dir` 覆寫）。
- **時區**：自動偵測（`$TZ` → `/etc/localtime` → 系統偏移）；可用
  `resolve_date_range.py --timezone Asia/Taipei` 指定。
- **Subagent 模型**：於 `git-worklog/config/provider_models.json` 統一設定
  （成本優先預設——Claude Haiku 4.5 ／ GPT-5.6 Luna ／ Gemini 3.5 Flash），由
  `resolve_provider_model.py` 依宿主解析；可用
  `REPO_WORKLOG_{ANTHROPIC,OPENAI,GOOGLE}_MODEL` 或 `--model` 覆寫，詳見
  `references/provider-models.md`。
- **Preview 狀態**：存放在 repo 之外的 `~/.repo_worklog/previews/`。
- **Subagent 分析結果**：每個 Day Subagent 把分析**寫進**
  `~/.repo_worklog/analysis/<run_id>/<date>.json`，而不是用回傳值交付——回傳通道會掉內容也會截斷。
  結果檔在執行後保留，方便回溯某段日誌是根據什麼分析寫出來的。

### 安全模型

- 一律先 dry-run；未經明確確認絕不寫入。
- 一天一個檔：重新分析某天只會覆寫該天的檔案，其他日期檔逐位元組保持不動。
- 每天的 `MANUAL` 區段與索引的 `MANUAL` 區段都逐字保留；只有自動產生區段會被覆蓋。
- 每日檔案採交易式寫入（所有目標日期一起暫存、驗證、原子替換，失敗即 rollback），
  失敗不會留下「部分日期已更新」的狀態；索引原子寫入，且永遠可由日期檔重建。
- Apply 前以多檔 preview 指紋把關（repo／branch／HEAD／worktree＋索引雜湊＋各日期檔雜湊＋
  目錄清單）；過期或已套用的 preview 會被拒絕。
- Skill 絕不執行 `git add/commit/push/fetch/pull/checkout/switch/merge/rebase`。

### 測試

`tests/` 內含只用標準庫的 `unittest` 測試（零第三方相依），於專案根目錄執行：

```bash
python3 -m unittest discover -s tests -v
```

涵蓋日期契約、Git 收集、工作區檢查、分析 manifest、日期檔引擎（建立／覆蓋／
MANUAL 保留／no-change／損壞拒絕）、索引重建（摘要／排序／MANUAL 保留）、兩支驗證器、
多檔 preview 一致性、舊檔遷移與完整確定性管線；CI 也會跑同一套。

### 授權

以 [MIT License](LICENSE) 釋出。
