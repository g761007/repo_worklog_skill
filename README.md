# Git Worklog

A portable agent **skill** that turns a Git repository's real code history into a
human-readable, per-day **project worklog** under `.git-worklog/` — one
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
├── git_worklog/              # the engine + `git-worklog` CLI (stdlib only)
│   ├── __init__.py           # __version__ — the single source of the product version
│   ├── data/
│   │   └── provider_models.json  # single source of truth for per-host subagent models
│   ├── markers.py            # day/index parser/serialiser; the format's definition
│   ├── paths.py              # user-level state dir ($GIT_WORKLOG_HOME, ~/.git-worklog)
│   ├── language.py           # BCP 47 resolution; the run's one output language
│   ├── config.py             # project config.json reader
│   ├── dates.py              # date/timezone contract; the day window everything agrees on
│   ├── providers.py          # per-host provider/model resolution (overrides, escalation)
│   ├── writer.py             # planning and transactionally writing day files + index.md
│   ├── preview.py            # the immutable preview record, its state machine and apply lock
│   ├── migrate.py            # one-time migration of a legacy worklog into .git-worklog/
│   ├── analysis/             # the pipeline: history -> manifest -> results (+ worktree, refs, coverage)
│   └── cli/                  # version / doctor / validate / analyze / preview / apply
├── scripts/                  # thin command-line shells over the package (stdlib only)
│   ├── resolve_provider_model.py    # resolve per-host provider/model (overrides, escalation, halt-and-ask)
│   ├── resolve_date_range.py        # date/timezone parsing, day-span cap, per-day bounds
│   ├── resolve_ref_range.py         # report mode: tag/ref -> authoritative commit set
│   ├── check_worklog_coverage.py    # report mode: per-date covered / gap / no-commits
│   ├── collect_git_history.py      # repo metadata + per-day commit facts (no summaries)
│   ├── inspect_worktree.py         # staged/unstaged/untracked + worktree fingerprint
│   ├── build_analysis_manifest.py  # one day's manifest (analyze prepare does a range)
│   ├── collect_day_results.py      # flat-dir result validation (analyze collect also checks coverage)
│   ├── update_daily_worklog.py     # create/overwrite per-day files (transactional); preserve MANUAL
│   ├── rebuild_worklog_index.py    # rebuild index.md from day files; preserve index MANUAL
│   ├── validate_daily_worklog.py   # per-day file marker/title/UTF-8 validation
│   ├── validate_worklog_index.py   # index marker/order/link/UTF-8 validation
│   ├── migrate_legacy_worklog.py   # one-time split of the legacy single file
│   └── worklog_markers.py          # compatibility shim -> git_worklog.markers
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

The worklog itself is written to `.git-worklog/` at the repository root:

```
.git-worklog/
├── VERSION           # on-disk layout version
├── config.json       # project settings (timezone, …)
├── index.md          # navigation: a date-descending table linking every day
└── days/
    ├── 2026-07-15.md # exactly one day per file
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

Nothing is installed and nothing is compiled: the engine sits inside that folder
and runs on the standard library alone.

### The CLI (optional)

The same engine also ships a `git-worklog` command. **The skill does not need
it** — it is for driving the deterministic parts yourself, from a terminal or
from CI:

```bash
pip install .          # from a clone; puts `git-worklog` on PATH
git-worklog version    # CLI / layout / config-schema versions
git-worklog doctor     # is this environment able to run the tool?
git-worklog validate   # is the worklog on disk well-formed?
git-worklog analyze    # prepare per-day analysis tasks, and collect them back
git-worklog preview    # freeze what an apply would write
git-worklog apply      # write a frozen preview
```

Without installing, the same commands run straight from the skill folder:

```bash
PYTHONPATH=git-worklog python3 -m git_worklog doctor --text
```

Each prints one JSON object (`--text` for a human-readable rendering). Exit `0`
means ok, `1` means it ran and found a problem, `2` means it could not run.

`analyze` is the pair of commands that bracket an analysis without performing
it:

```bash
git-worklog analyze prepare --from 2026-07-01 --to 2026-07-07 \
    --timezone Asia/Taipei --language zh-TW --language-source user-request
# → one manifest per day under ~/.git-worklog/analysis/<run_id>/tasks/,
#   each naming the result_path its analysis must be written to.

git-worklog analyze collect --run-id <run_id>
# → reads the results back and checks them: every prepared day arrived, none
#   drifted language, every evidence citation resolves against the tree of the
#   commit it names, and no source file the day changed went undescribed.
```

Between the two, something has to actually read the patches and write the
prose — and that something is your agent's model, not the CLI. This is why no
model API key is needed: `prepare` only decides *what* must be analysed, and
`collect` only decides whether to believe the answer. A day whose analysis never
arrived is reported as `missing`, never as a day where nothing happened.

That last check is the one that catches quiet failures. An analysis can cite
three real files perfectly, never mention the other twenty the day changed, and
still call itself complete and verified — so `prepare` marks which files the day
is answerable for (source only; docs, config, tests, binaries and deletions are
excused) and `collect` fails any day that leaves one undescribed.

`preview` and `apply` then close the loop:

```bash
git-worklog preview --run-id <run_id> <<'JSON'
{"entries": {"2026-07-15": {"generated_markdown": "## 當日摘要\n..."}}}
JSON
# → a preview_id. Every target file's *final text* is stored under
#   ~/.git-worklog/previews/, along with the repository, worklog, run and
#   language fingerprints it depends on. Nothing is written.

git-worklog apply --preview-id <preview_id>
# → re-checks all of those, then writes exactly the stored bytes.
```

A preview id is the only thing `apply` accepts. That is deliberate: the day's
prose enters once, before the user reads it, and there is no argument through
which a re-render could reach the disk. If anything moved in between — HEAD, a
day file, `index.md`, the analysis results, the project's language — apply
refuses and says which, rather than writing something close enough. Previews
expire after 24 hours, apply exactly once, and take a per-worklog lock so two of
them cannot interleave.

More commands — reports, migration, cleanup — arrive as the CLI grows; today
those live in the skill.

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
splits each date into `.git-worklog/days/<date>.md`, previews first, never deletes
the old file, and refuses if the legacy markers are corrupt.

### Configuration

- **Target directory:** defaults to `.git-worklog/` at the repo root
  (`update_daily_worklog.py --dir` / `rebuild_worklog_index.py --dir` to override).
- **Timezone:** auto-detected (`$TZ` → `/etc/localtime` → offset); override with
  `resolve_date_range.py --timezone Asia/Taipei`.
- **Output language:** the worklog is written in the language you are asking in,
  not the language the repository is in. English commits, English identifiers and
  an English README do not make an English worklog. Priority: what you asked for
  → `--language` → `.git-worklog/config.json` → the agent host →
  `GIT_WORKLOG_LANGUAGE` → system locale → English. Tags are BCP 47 (`zh-TW`,
  `en`, `ja`); bare `zh` is refused as ambiguous, and `zh-TW`/`zh-CN` are never
  treated as the same setting. Paths, code symbols, commit hashes and API names
  are never translated in any language. `index.md` fixes its language on first
  build so it does not churn between contributors. See roadmap §6.2.
- **Subagent models:** defined once in `git-worklog/git_worklog/data/provider_models.json`
  (cost-first defaults — Claude Haiku 4.5 / GPT-5.6 Luna / Gemini 3.5 Flash) and
  resolved per host by `resolve_provider_model.py`. Override with
  `GIT_WORKLOG_{ANTHROPIC,OPENAI,GOOGLE}_MODEL` or an explicit `--model`. See
  `references/provider-models.md`.
- **Preview state:** stored outside the repo in `~/.git-worklog/previews/`.
- **Subagent results:** each Day Subagent writes its analysis to
  `~/.git-worklog/analysis/<run_id>/<date>.json` rather than returning it as
  reply text, which drops and truncates. Files are kept after the run, so a
  surprising worklog entry can be traced to the analysis behind it.

### Development commands

Each script is standalone and prints one JSON object to stdout:

```bash
cd git-worklog
python3 scripts/resolve_date_range.py --days 7 --timezone Asia/Taipei --today 2026-07-15
python3 scripts/collect_git_history.py --repo /path/to/repo --info-only
python3 scripts/update_daily_worklog.py --dir /tmp/.git-worklog <<'JSON'
{"meta": {"timezone": "Asia/Taipei", "branch": "main", "head": "abc1234"},
 "entries": {"2026-07-15": {"generated_markdown": "## 當日摘要\n\n..."}}}
JSON
python3 scripts/rebuild_worklog_index.py --dir /tmp/.git-worklog
python3 scripts/validate_daily_worklog.py --dir /tmp/.git-worklog
python3 scripts/validate_worklog_index.py --dir /tmp/.git-worklog
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
- **An apply writes the preview, not a reconstruction of it.** The preview record
  holds the final text of every target file, and `git-worklog apply` takes a
  preview id and nothing else — so what lands on disk is what was on screen, and
  no re-render can reach the filesystem.
- Applies are gated on everything that payload depends on: repository identity,
  git dir, branch, HEAD, submodules, the working tree (when the run read it),
  `index.md`, each day file, the directory listing, the analysis run, and the
  project's language settings. Anything moved, or a preview that is expired,
  cancelled, failed or already applied, is refused rather than reconciled.
- Concurrent applies to one worklog are locked out; a lock is broken only when
  its owner is provably dead.
- The skill never runs `git add/commit/push/fetch/pull/checkout/switch/merge/rebase`.

### License

Released under the [MIT License](LICENSE).

---

## 繁體中文說明

**Git Worklog** 是一個可攜的 agent **skill**，把 Git repository 的**實際程式碼歷史**
整理成方便人閱讀、**逐日**的**專案工作日誌**，寫在 `.git-worklog/` 目錄下——
每天一個 Markdown 檔，另有一份 `index.md` 依日期由新到舊連結各日。

它會閱讀**真正的 diff 與周邊程式碼**——不是只看 commit message——每一天各由一個
subagent 分析，所有變更都先以 dry-run 預覽，**經你明確確認後才寫入**。它記錄整個專案的
歷史（不分作者），而且**絕不執行** `git add/commit/push`。

### 目錄結構

- `git-worklog/`：整個目錄就是 skill 本體。
  - `SKILL.md`：控制層——觸發條件、流程、腳本與 references 對照。
  - `agents/openai.yaml`：宿主 manifest——顯示名稱、UI metadata、model_config 指標。
  - `scripts/`：套件的命令列薄殼（僅用標準庫，各自輸出單一 JSON）。
    - `resolve_provider_model.py`：依宿主解析 provider／模型（覆寫、escalation、halt-and-ask）。
    - `resolve_date_range.py`：日期／時區解析、日數上限（`--max-days`，預設 30）、逐日半開區間。
    - `resolve_ref_range.py`：報告模式——把 tag／ref 解析成權威的 commit 集合與對應日期。
    - `check_worklog_coverage.py`：報告模式——逐日覆蓋狀態（covered／gap／no-commits）。
    - `collect_git_history.py`：repo 中繼資料與逐日 commit 事實（不摘要、不依作者過濾）。
    - `inspect_worktree.py`：staged／unstaged／untracked 與 worktree 指紋。
    - `build_analysis_manifest.py`：單日 manifest（`analyze prepare` 一次處理整個範圍）。
    - `collect_day_results.py`：扁平目錄的結果驗證。它看不到 manifest，因此無法檢查覆蓋率，也不假裝檢查；請優先用 `analyze collect`。
    - `update_daily_worklog.py`：建立／覆蓋每日檔案（交易式）、保留 MANUAL。
    - `rebuild_worklog_index.py`：由日期檔重建 index.md、保留索引 MANUAL。
    - `validate_daily_worklog.py`：每日檔案的標記／標題／UTF-8 驗證。
    - `validate_worklog_index.py`：索引標記／排序／連結／UTF-8 驗證。
    - `migrate_legacy_worklog.py`：一次性把舊單檔拆成目錄式。
    - `worklog_markers.py`：相容轉接層，實際模組為 `git_worklog.markers`。
  - `git_worklog/`：引擎與 `git-worklog` CLI 本體（僅標準庫）。
    - `__init__.py`：`__version__`——產品版本的單一來源。
    - `data/provider_models.json`：逐宿主 subagent 模型的**單一設定來源**（放在
      套件內，安裝版 CLI 才讀得到）。
    - `markers.py`：日期檔／索引的解析與序列化，即格式的定義。
    - `paths.py`：使用者層級狀態目錄（`$GIT_WORKLOG_HOME`、`~/.git-worklog`）。
    - `language.py`：BCP 47 語言解析——一個 run 只有一種輸出語言。
    - `config.py`：專案 `config.json` 讀取。
    - `dates.py`：日期／時區契約，以及全工具共用的單日半開區間。
    - `providers.py`：依宿主解析 provider／模型（覆寫、escalation）。
    - `writer.py`：規劃並以交易方式寫入日期檔與 `index.md`。
    - `preview.py`：不可變的 preview record、其狀態機與 apply 鎖。
    - `migrate.py`：一次性把舊版工作日誌遷移進 `.git-worklog/`。
    - `analysis/`：分析流程（history → manifest → results，另有 worktree、refs、coverage）。
    - `cli/`：`version`／`doctor`／`validate`／`analyze`／`preview`／`apply`。
  - `references/`：skill 依需求載入的詳細規格（報告模式、互動流程、日期契約、
    程式碼分析規則、subagent 契約、工作日誌格式、模型設定）。
- `docs/naming-conventions.md`：品牌、skill、CLI、package 與目錄的正式命名對照。
- `docs/plans/`：設計計畫，檔名格式 `yyyy-MM-dd-<主題>.md`。
  - `2026-07-15-repo-worklog-skill-design.md`：原始設計規格（單檔時代）。
  - `2026-07-16-commit-author-and-report-mode.md`：commit 作者與報告模式。
  - `2026-07-16-git-worklog-v1-roadmap.md`：v1.0 更名與重構路線圖。

工作日誌本身寫在 repository 根目錄的 `.git-worklog/`：

```
.git-worklog/
├── VERSION           # 磁碟佈局版本
├── config.json       # 專案設定（時區等）
├── index.md          # 導航：依日期由新到舊連結各日
└── days/
    ├── 2026-07-15.md # 一天一個檔
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

不需要安裝任何東西，也不需要編譯：引擎就在那個資料夾裡，只用標準庫執行。

### CLI（選用）

同一套引擎另外提供 `git-worklog` 指令。**skill 不需要它**——它是給你在終端機或 CI
裡自行驅動確定性部分用的：

```bash
pip install .          # 從 clone 安裝，把 `git-worklog` 放進 PATH
git-worklog version    # CLI／佈局／設定 schema 版本
git-worklog doctor     # 這個環境跑得動嗎？
git-worklog validate   # 磁碟上的工作日誌格式正確嗎？
git-worklog analyze    # 建立每日分析任務，再把結果收回來驗證
git-worklog preview    # 凍結這次 apply 會寫入的全部內容
git-worklog apply      # 寫入某份已凍結的 preview
```

不安裝的話，同樣的指令可直接從 skill 資料夾執行：

```bash
PYTHONPATH=git-worklog python3 -m git_worklog doctor --text
```

每個指令輸出單一 JSON 物件（`--text` 可切成人類可讀格式）。離開碼 `0` 表示正常、
`1` 表示執行成功但發現問題、`2` 表示指令本身無法執行。

`analyze` 是一對指令，它們框住分析、但不執行分析：

```bash
git-worklog analyze prepare --from 2026-07-01 --to 2026-07-07 \
    --timezone Asia/Taipei --language zh-TW --language-source user-request
# → 在 ~/.git-worklog/analysis/<run_id>/tasks/ 下每天產生一份 manifest，
#   每份都指定該日分析結果必須寫入的 result_path。

git-worklog analyze collect --run-id <run_id>
# → 把結果讀回來檢查：每個準備過的日期都有交、沒有任何一天語言跑掉、
#   每條 evidence 引文都能在它所引用的那個 commit 的 tree 上對得上，
#   而且當天改到的原始碼沒有任何一個檔案沒被描述到。
```

在這兩步之間，總得有人真的去讀 patch、寫出敘述——那是你的 agent 的模型，不是 CLI。
這正是不需要模型 API key 的原因：`prepare` 只決定**該分析什麼**，`collect` 只決定
要不要相信答案。分析沒交回來的日子會被報成 `missing`，絕不會被當成「這天沒事發生」。

最後那項檢查抓的是最安靜的失敗：一份分析可以把三個檔案引用得完美無缺、卻對當天改動的
另外二十個檔案隻字未提，而它看起來依然「完整、已驗證」。所以 `prepare` 會標出當天該負責
的檔案（只限原始碼；文件、設定、測試、二進位檔與刪除的檔案都豁免），`collect` 則會讓
任何漏掉的日子失敗。

`preview` 與 `apply` 收尾：

```bash
git-worklog preview --run-id <run_id> <<'JSON'
{"entries": {"2026-07-15": {"generated_markdown": "## 當日摘要\n..."}}}
JSON
# → 回傳 preview_id。每個目標檔案的**最終內容**都存進 ~/.git-worklog/previews/，
#   連同它所依賴的 repository、worklog、run 與語言指紋。此時不寫入任何檔案。

git-worklog apply --preview-id <preview_id>
# → 重新驗證上述全部項目，然後寫入「存起來的那份位元組」。
```

`apply` 只收 preview id，這是刻意的：當天的敘述只在使用者過目之前進入工具一次，之後
沒有任何參數能讓重新產生的內容抵達磁碟。中間只要有東西動過——HEAD、某個日期檔、
`index.md`、分析結果、專案語言設定——apply 就會拒絕並指出是哪一項，而不是寫入一份
「差不多」的內容。Preview 24 小時後過期、只能套用一次，並且會取得 per-worklog 鎖，
避免兩次 apply 交錯。

報告、遷移、清理等指令會隨 CLI 成長陸續加入，目前仍在 skill 內。

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
`.git-worklog/days/<date>.md`，先預覽、絕不刪除舊檔，舊標記損壞時則拒絕遷移。

### 設定

- **輸出目錄**：預設為 repo 根目錄的 `.git-worklog/`（以
  `update_daily_worklog.py --dir` ／ `rebuild_worklog_index.py --dir` 覆寫）。
- **時區**：自動偵測（`$TZ` → `/etc/localtime` → 系統偏移）；可用
  `resolve_date_range.py --timezone Asia/Taipei` 指定。
- **輸出語言**：日誌用「你提問的語言」寫，不是用「repo 的語言」寫。commit 訊息、識別符、
  README 全是英文，也不會讓日誌變成英文。優先序：本次要求 → `--language` →
  `.git-worklog/config.json` → Agent Host → `GIT_WORKLOG_LANGUAGE` → 系統 locale
  → 英文。語言標籤採 BCP 47（`zh-TW`、`en`、`ja`）；單獨的 `zh` 因語意模糊會被拒絕，
  `zh-TW` 與 `zh-CN` 永不視為同一設定。任何語言下，檔案路徑、程式符號、commit hash
  與 API 名稱都不翻譯。`index.md` 於首次建立時固定語言，避免不同貢獻者反覆改寫。
  詳見 roadmap §6.2。
- **Subagent 模型**：於 `git-worklog/git_worklog/data/provider_models.json` 統一設定
  （成本優先預設——Claude Haiku 4.5 ／ GPT-5.6 Luna ／ Gemini 3.5 Flash），由
  `resolve_provider_model.py` 依宿主解析；可用
  `GIT_WORKLOG_{ANTHROPIC,OPENAI,GOOGLE}_MODEL` 或 `--model` 覆寫，詳見
  `references/provider-models.md`。
- **Preview 狀態**：存放在 repo 之外的 `~/.git-worklog/previews/`。
- **Subagent 分析結果**：每個 Day Subagent 把分析**寫進**
  `~/.git-worklog/analysis/<run_id>/<date>.json`，而不是用回傳值交付——回傳通道會掉內容也會截斷。
  結果檔在執行後保留，方便回溯某段日誌是根據什麼分析寫出來的。

### 安全模型

- 一律先 dry-run；未經明確確認絕不寫入。
- 一天一個檔：重新分析某天只會覆寫該天的檔案，其他日期檔逐位元組保持不動。
- 每天的 `MANUAL` 區段與索引的 `MANUAL` 區段都逐字保留；只有自動產生區段會被覆蓋。
- 每日檔案採交易式寫入（所有目標日期一起暫存、驗證、原子替換，失敗即 rollback），
  失敗不會留下「部分日期已更新」的狀態；索引原子寫入，且永遠可由日期檔重建。
- **Apply 寫入的就是 preview 本身，不是重新產生的版本。** Preview record 存有每個目標
  檔案的最終內容，而 `git-worklog apply` 只收一個 preview id——所以落到磁碟上的，就是
  當初顯示在畫面上的那份，任何重新產生的內容都沒有路徑可以進入檔案系統。
- Apply 前會重新驗證那份 payload 所依賴的一切：repository identity、git dir、branch、
  HEAD、submodule、working tree（僅當該 run 有讀取時）、`index.md`、各日期檔、目錄清單、
  分析 run，以及專案語言設定。只要有任一項變動，或 preview 已過期／已取消／已失敗／
  已套用，一律拒絕，而不是自行調和。
- 同一份工作日誌的並行 apply 會被鎖擋下；只有在持有者確定已死時才會破鎖。
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
