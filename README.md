# repo_worklog skill

A portable agent **skill** that turns a Git repository's real code history into a
human-readable, per-day **project worklog** at `docs/PROJECT_WORKLOG.md`.

It reads the actual diffs and surrounding code — never just commit messages —
analyzes each day with its own subagent, previews every change as a dry-run, and
only writes after you explicitly confirm. It logs the whole project's history
(every author), and it never runs `git add/commit/push`.

**Languages:** [English](#english) · [繁體中文](#繁體中文說明)

---

## English

### Layout

```
repo_worklog/                 # the skill (this whole directory is the skill)
├── SKILL.md                  # control layer: triggers, flow, script/reference map
├── agents/
│   └── openai.yaml           # host manifest: display name, providers, UI metadata
├── scripts/                  # deterministic Python helpers (stdlib only)
│   ├── resolve_date_range.py     # date/timezone parsing, 30-day limit, per-day bounds
│   ├── collect_git_history.py    # repo metadata + per-day commit facts (no summaries)
│   ├── inspect_worktree.py       # staged/unstaged/untracked + worktree fingerprint
│   ├── build_analysis_manifest.py# group changed files, propose reading, flag big days
│   ├── update_worklog.py         # simulate/apply insert & overwrite; preserve MANUAL
│   ├── validate_worklog.py       # marker + UTF-8 structural validation
│   ├── preview_state.py          # preview fingerprint, id, apply-time consistency
│   └── worklog_markers.py        # shared marker parser/serialiser (imported by 3 above)
└── references/               # detailed specs the skill loads on demand
    ├── interaction-flow.md
    ├── date-parameter-contract.md
    ├── code-analysis-rules.md
    ├── subagent-contract.md
    ├── worklog-format.md
    └── provider-models.md

docs/init_plan.md             # the original design specification
```

### Requirements

- **Python 3.9+** (uses `zoneinfo`; developed and tested on 3.14). Standard
  library only — no third-party packages.
- **Git 2.37+** (uses `git log --since-as-filter`; developed on 2.54).
- A host that supports agent skills (Claude Code, and — via `agents/openai.yaml`
  — Codex / Gemini).

### Installation

This directory *is* the skill. Install it by placing the `repo_worklog/` folder
where your host discovers skills, for example for Claude Code:

```bash
# user-level
cp -r repo_worklog ~/.claude/skills/repo_worklog
# or project-level, inside a target repo
cp -r repo_worklog <your-project>/.claude/skills/repo_worklog
```

Prefer a symlink while developing, so edits take effect immediately:

```bash
ln -s "$(pwd)/repo_worklog" ~/.claude/skills/repo_worklog
```

Then invoke it with `/repo_worklog` (or natural language like “整理最近 7 天”).

### Usage

Run with no arguments to get the range menu; the skill does nothing until you
pick a range:

```
/repo_worklog
```

Or drive it directly / in natural language:

```
/repo_worklog days=7
/repo_worklog date=2026-07-01
/repo_worklog from=2026-07-01 to=2026-07-10
/repo_worklog date=2026-07-15 include_uncommitted=true
整理最近 7 天，包含目前還沒有 commit 的修改
```

Every valid request produces a **dry-run preview** with a `preview_id`. Confirm
with “寫入” / “確認更新” / `apply <preview_id>` to write. See
`repo_worklog/references/interaction-flow.md` for the full flow.

### Configuration

- **Target file:** defaults to `docs/PROJECT_WORKLOG.md`
  (`update_worklog.py --target` to override).
- **Timezone:** auto-detected (`$TZ` → `/etc/localtime` → offset); override with
  `resolve_date_range.py --timezone Asia/Taipei`.
- **Subagent models:** set per host in `repo_worklog/agents/openai.yaml`
  (`providers.*.model_id`). See `references/provider-models.md`.
- **Preview state:** stored outside the repo in `~/.repo_worklog/previews/`.

### Development commands

Each script is standalone and prints one JSON object to stdout:

```bash
cd repo_worklog
python3 scripts/resolve_date_range.py --days 7 --timezone Asia/Taipei --today 2026-07-15
python3 scripts/collect_git_history.py --repo /path/to/repo --info-only
python3 scripts/update_worklog.py --target /tmp/WL.md <<'JSON'
{"entries": {"2026-07-15": {"generated_markdown": "### 當日摘要\n\n..."}}}
JSON
python3 scripts/validate_worklog.py --target /tmp/WL.md
```

### Tests

There is no packaged test suite yet. The scripts were verified against a
controlled Git fixture (single/multi-commit days, revert, rename, binary,
uncommitted changes) and an end-to-end run of the worklog engine (insert,
overwrite, MANUAL preservation, corruption detection, preview consistency).
See `docs/init_plan.md` section 27 for the intended acceptance-test matrix.

### Safety model

- Dry-run first, always; nothing is written without explicit confirmation.
- `MANUAL` regions and all content outside the `ENTRIES` area are preserved
  verbatim; only `GENERATED` regions are overwritten.
- Writes are atomic (same-directory temp file + atomic replace, re-validated).
- Applies are gated by a preview fingerprint (repo/branch/HEAD/worktree/worklog
  hash); a stale or already-used preview is refused.
- The skill never runs `git add/commit/push/fetch/pull/checkout/switch/merge/rebase`.

### License

Released under the [MIT License](LICENSE).

---

## 繁體中文說明

`repo_worklog` 是一個可攜的 agent **skill**，把 Git repository 的**實際程式碼歷史**
整理成方便人閱讀、**逐日**的**專案工作日誌**，預設寫在 `docs/PROJECT_WORKLOG.md`。

它會閱讀**真正的 diff 與周邊程式碼**——不是只看 commit message——每一天各由一個
subagent 分析，所有變更都先以 dry-run 預覽，**經你明確確認後才寫入**。它記錄整個專案的
歷史（不分作者），而且**絕不執行** `git add/commit/push`。

### 目錄結構

- `repo_worklog/`：整個目錄就是 skill 本體。
  - `SKILL.md`：控制層——觸發條件、流程、腳本與 references 對照。
  - `agents/openai.yaml`：宿主 manifest——顯示名稱、providers 模型、UI metadata。
  - `scripts/`：確定性 Python 腳本（僅用標準庫，各自輸出單一 JSON）。
    - `resolve_date_range.py`：日期／時區解析、30 天上限、逐日半開區間。
    - `collect_git_history.py`：repo 中繼資料與逐日 commit 事實（不摘要、不依作者過濾）。
    - `inspect_worktree.py`：staged／unstaged／untracked 與 worktree 指紋。
    - `build_analysis_manifest.py`：檔案分組、所需上下文建議、大日標記。
    - `update_worklog.py`：模擬／套用插入與覆蓋、保留 MANUAL、原子寫入。
    - `validate_worklog.py`：標記與 UTF-8 結構驗證。
    - `preview_state.py`：preview 指紋／ID、apply 前一致性、防重複套用。
    - `worklog_markers.py`：共用標記解析／序列化模組（由上述三支匯入）。
  - `references/`：skill 依需求載入的詳細規格（互動流程、日期契約、程式碼分析規則、
    subagent 契約、工作日誌格式、模型設定）。
- `docs/init_plan.md`：原始設計規格。

### 需求環境

- **Python 3.9+**（使用 `zoneinfo`；於 3.14 開發測試）。僅標準庫，無第三方套件。
- **Git 2.37+**（使用 `git log --since-as-filter`；於 2.54 開發）。
- 支援 agent skill 的宿主（Claude Code；透過 `agents/openai.yaml` 也適用 Codex／Gemini）。

### 安裝

這個目錄本身就是 skill，把 `repo_worklog/` 放到宿主會探索 skill 的位置即可。以 Claude Code 為例：

```bash
# 使用者層級
cp -r repo_worklog ~/.claude/skills/repo_worklog
# 或專案層級（放進目標 repo）
cp -r repo_worklog <your-project>/.claude/skills/repo_worklog
```

開發時建議改用**符號連結**，修改能即時生效：

```bash
ln -s "$(pwd)/repo_worklog" ~/.claude/skills/repo_worklog
```

之後以 `/repo_worklog` 或自然語言（例如「整理最近 7 天」）呼叫。

### 使用方式

無參數呼叫時只會顯示範圍選單，**在你選擇範圍前不做任何分析**：

```
/repo_worklog
```

也可直接帶參數或用自然語言：

```
/repo_worklog days=7
/repo_worklog date=2026-07-01
/repo_worklog from=2026-07-01 to=2026-07-10
/repo_worklog date=2026-07-15 include_uncommitted=true
整理最近 7 天，包含目前還沒有 commit 的修改
```

任何有效請求都會先產生 **dry-run 預覽**與一個 `preview_id`，以「寫入」／「確認更新」／
`apply <preview_id>` 確認後才會寫入。完整流程見 `repo_worklog/references/interaction-flow.md`。

### 設定

- **輸出檔案**：預設 `docs/PROJECT_WORKLOG.md`（以 `update_worklog.py --target` 覆寫）。
- **時區**：自動偵測（`$TZ` → `/etc/localtime` → 系統偏移）；可用
  `resolve_date_range.py --timezone Asia/Taipei` 指定。
- **Subagent 模型**：在 `repo_worklog/agents/openai.yaml` 依宿主設定
  （`providers.*.model_id`），詳見 `references/provider-models.md`。
- **Preview 狀態**：存放在 repo 之外的 `~/.repo_worklog/previews/`。

### 安全模型

- 一律先 dry-run；未經明確確認絕不寫入。
- `MANUAL` 區段與 `ENTRIES` 區域外的所有內容都逐字保留；只有 `GENERATED` 會被覆蓋。
- 寫入採原子方式（同目錄暫存檔＋原子替換，寫入前後皆重新驗證）。
- Apply 前以 preview 指紋（repo／branch／HEAD／worktree／工作日誌雜湊）把關；
  過期或已套用的 preview 會被拒絕。
- Skill 絕不執行 `git add/commit/push/fetch/pull/checkout/switch/merge/rebase`。

### 授權

以 [MIT License](LICENSE) 釋出。
