# Date Parameter Contract

This document describes the exact behaviour of `scripts/resolve_date_range.py`, the
deterministic date contract for the `git-worklog` skill. The skill's model layer
normalises free-form natural language into canonical parameters *before* calling
this script; the script itself never parses free text. It only accepts the
canonical parameters plus a small set of shortcuts, resolves them into concrete
per-day time boundaries in the local timezone, and prints exactly one JSON object.

Everything here is authoritative for the *implemented* script. Do not assume
behaviour beyond what is documented below.

---

## 1. Command-line interface

```text
python3 scripts/resolve_date_range.py [SHORTCUT] \
    [--date D] [--days N] [--from D] [--to D] \
    [--include-uncommitted] [--timezone IANA] [--today D]
```

| Argument | Meaning |
| --- | --- |
| `SHORTCUT` (positional, optional) | One of `7d`, `30d` (any `NNd`) or a bare `YYYY-MM-DD`. Folded into `--days` / `--date`. |
| `--date D` | Single calendar date `YYYY-MM-DD` (single-day mode). |
| `--days N` | Most recent `N` calendar days **including today** (recent-days mode). |
| `--from D` | Range start, inclusive, `YYYY-MM-DD` (custom-range mode). |
| `--to D` | Range end, inclusive, `YYYY-MM-DD` (custom-range mode). |
| `--include-uncommitted` | Records intent to include working-tree changes. Applied to **today only**. |
| `--timezone IANA` | Explicit IANA timezone override, e.g. `Asia/Taipei`. |
| `--today D` | Overrides "today" (`YYYY-MM-DD`) for deterministic runs. |
| `--max-days N` | Maximum span in calendar days. Default `30`. |

The script exits `0` on success and `2` on any validation failure.

Natural language is normalised by the **model** before the script is called; the
script never interprets free text.

### The day-span cap

`MAX_DAYS = 30` is the default and applies to `--days` and to `from`/`to`. It
exists to **bound per-day subagent cost**, so it governs worklog generation and
backfilling a gap.

Report mode (`references/report-mode.md`) only reads day files already on disk
and spawns no subagents, so it passes `--max-days 90`. That is a different
constraint — bounding how much day-file text is pulled into context — not a
loosening of the generation rule. Backfill is still capped at 30, because
backfill spawns subagents.

The successful response echoes the cap in effect as `max_days`. `--max-days` must
be at least 1 (`BAD_MAX_DAYS`); it raises the ceiling, it never removes it —
`TOO_MANY_DAYS` and `DAYS_OUT_OF_RANGE` still fire against whatever cap applies.

---

## 2. Supported canonical parameters and shortcuts

Canonical parameters:

```text
date
days
from
to
include_uncommitted
```

Shortcut forms (positional token):

```text
7d
30d
2026-07-01
```

Shortcut folding rules:

- `NNd` (e.g. `7d`, `30d`) is folded into `days`. If `--days` is also supplied,
  this is an `ARG_CONFLICT`.
- A bare `YYYY-MM-DD` is folded into `date`. If `--date` is also supplied, this is
  an `ARG_CONFLICT`.
- Any other token is an `UNKNOWN_SHORTCUT`.

---

## 3. Three modes (mutually exclusive)

Exactly one of the following modes must be active: **`date`**, **`days`**, or the
**`from` + `to`** range. See §7 for the mutual-exclusivity rule.

### 3.1 Single-day mode (`mode: "date"`)

`--date 2026-07-01` is exactly equivalent to the bare shortcut `2026-07-01`. Only
the specified day is processed; `days_count` is `1`.

```json
{
  "ok": true,
  "timezone": "Asia/Taipei",
  "timezone_source": "explicit",
  "include_uncommitted": false,
  "today": "2026-07-15",
  "errors": [],
  "mode": "date",
  "days_count": 1,
  "dates": [
    { "date": "2026-07-01", "start": "2026-07-01T00:00:00+08:00", "end": "2026-07-02T00:00:00+08:00" }
  ]
}
```

### 3.2 Recent-days mode (`mode: "days"`)

`--days N` covers the most recent `N` calendar days **including today**. Therefore
`days=1` means today, and the start day is `today - (N - 1)`.

`--days 7` is exactly equivalent to the shortcut `7d`.

Worked example — run on `2026-07-15` with `7d`:

```text
2026-07-09 至 2026-07-15   (7 days, today inclusive)
```

```json
{
  "ok": true,
  "mode": "days",
  "days_count": 7,
  "today": "2026-07-15",
  "dates": [
    { "date": "2026-07-09", "start": "2026-07-09T00:00:00+08:00", "end": "2026-07-10T00:00:00+08:00" },
    { "date": "2026-07-10", "start": "2026-07-10T00:00:00+08:00", "end": "2026-07-11T00:00:00+08:00" },
    "... 2026-07-11 through 2026-07-14 ...",
    { "date": "2026-07-15", "start": "2026-07-15T00:00:00+08:00", "end": "2026-07-16T00:00:00+08:00" }
  ]
}
```

### 3.3 Custom-range mode (`mode: "range"`)

`--from` and `--to` are both **inclusive on both ends**. The total number of days is:

```text
total = to - from + 1
```

So `from=2026-07-01 to=2026-07-10` yields `days_count = 10`.

---

## 4. Maximum 30 days

The maximum span is fixed at **30 days** (`MAX_DAYS = 30`).

| Input | Result |
| --- | --- |
| `days=30` | Valid (`days_count = 30`). |
| `days=31` | Invalid — `DAYS_OUT_OF_RANGE`. |
| `from=2026-06-01 to=2026-07-15` | Invalid — spans 45 days, `TOO_MANY_DAYS`. |

When a range exceeds 30 days the script:

- does **not** run the analysis;
- does **not** auto-truncate to 30 days;
- reports the actual requested day count (`requested_days`) and `max_days`;
- and the skill asks the user to narrow the range.

The `TOO_MANY_DAYS` failure for the 45-day example:

```json
{
  "ok": false,
  "errors": [
    {
      "code": "TOO_MANY_DAYS",
      "requested_days": 45,
      "max_days": 30,
      "message": "The requested range spans 45 days, exceeding the 30-day limit. Narrow the range to 30 days or fewer."
    }
  ]
}
```

User-facing message the skill surfaces (from the plan):

```text
指定範圍共 45 天，超過 Git Worklog 的 30 天上限。
請將日期範圍縮小至 30 天以內。
```

> Note: `days=31` is rejected as `DAYS_OUT_OF_RANGE` (the `--days` integer bound),
> whereas an over-long `from`/`to` span is rejected as `TOO_MANY_DAYS`. Both enforce
> the same 30-day ceiling.

---

## 5. Mode mutual exclusivity

`date`, `days`, and the `from`+`to` pair are three separate modes. Exactly one must
be active.

- Supplying **none** of them → `NO_DATE_SPEC`.
- Supplying **more than one** → `ARG_CONFLICT`. The script must **not** pick a
  winner; it reports the conflict and refuses to proceed.

Example — `date=2026-07-01 days=7` is a conflict:

```json
{
  "ok": false,
  "errors": [
    {
      "code": "ARG_CONFLICT",
      "message": "date, days and from/to are mutually exclusive; supply exactly one mode.",
      "provided": { "date": "2026-07-01", "days": 7, "from": null, "to": null }
    }
  ]
}
```

---

## 6. Other validation rules

- `days` must be an integer in `1..30`; otherwise `DAYS_OUT_OF_RANGE`.
- `from` must not be later than `to`; otherwise `FROM_AFTER_TO`.
- `to` must not be used on its own; `to` without `from` → `TO_WITHOUT_FROM`.
- `from` must be paired with `to`; `from` without `to` → `FROM_WITHOUT_TO`.
- Dates are normalised to `YYYY-MM-DD`. A value that does not match this pattern is
  an `INVALID_DATE`.
- Impossible calendar dates are rejected. `2026-02-30` → `INVALID_DATE`
  (`"date is not a real calendar date"`).
- "近一個月" (recent month) is **always 30 days** — never the actual calendar-month
  length. This normalisation happens in the model layer (`days=30`).
- Future dates parse successfully. If the corresponding Git history has no activity,
  the downstream analysis simply reports "no changes" for those days; the date
  resolver itself does not treat future dates as errors.

---

## 7. Timezone rules

### 7.1 Priority order

All date ranges are computed against the local timezone. The script resolves the
timezone in this order, recording where it came from in `timezone_source`:

| Priority | Source | `timezone_source` value |
| --- | --- | --- |
| 1 | Explicit `--timezone` override | `explicit` |
| 2 | `$TZ` environment variable (env-provided IANA) | `env:TZ` |
| 3 | OS local timezone via the `/etc/localtime` symlink | `system:/etc/localtime` |
| 4 | Fixed local UTC offset with no reliable IANA name | `system-offset` |

The broader skill contract layers further fallbacks around the script: an
env-provided IANA timezone, then the OS local timezone, then project config, then
skill config, and finally — only if the timezone still cannot be reliably
determined — it asks the user to specify one. The script itself directly honours the
`--timezone` override and `$TZ`, reads `/etc/localtime`, and otherwise falls back to
a fixed offset.

When `timezone_source` is `system-offset` there is **no reliable IANA name**
(the `timezone` field carries only a best-effort label / offset name). The skill
should surface the assumed offset and offer to set an explicit timezone.

An unknown `--timezone` value fails fast with `INVALID_TIMEZONE`.

Example canonical IANA name:

```text
Asia/Taipei
```

### 7.2 Per-day interval — half-open

Each day uses the **half-open interval** `[local 00:00:00, next day 00:00:00)`:

```text
[當日 00:00:00, 次日 00:00:00)
```

The `end` of a day is the **next day's local midnight**, never `23:59:59.999`. This
avoids millisecond-precision gaps and DST transition problems. The `end` offset is
recomputed at the next local midnight, so it stays correct across DST boundaries.

In the emitted JSON each day is:

```json
{ "date": "2026-07-15", "start": "2026-07-15T00:00:00+08:00", "end": "2026-07-16T00:00:00+08:00" }
```

### 7.3 Commit day attribution

Commit-to-day attribution uses the **committer date** by default. The **author
date** is also retained; if the difference between the two would affect
understanding, it can be flagged in the analysis evidence.

---

## 8. Natural-language normalisation (model layer)

The model maps user phrasing to canonical parameters before invoking the script.
The following table reproduces the plan's §26 cases.

| # | User input (literal) | Canonical output |
| --- | --- | --- |
| 26.1 | 整理今天 | `date=<local today>`, `include_uncommitted=false` |
| 26.2 | 幫我補 2026 年 7 月 1 日的日誌 | `date=2026-07-01`, `include_uncommitted=false` |
| 26.3 | 整理最近一週 | `days=7`, `include_uncommitted=false` |
| 26.4 | 整理近一個月 | `days=30`, `include_uncommitted=false` |
| 26.5 | 整理 2026 年 7 月 1 日到 7 月 10 日 | `from=2026-07-01`, `to=2026-07-10`, `include_uncommitted=false` |
| 26.6 | 整理今天，連還沒有 commit 的一起 | `date=<local today>`, `include_uncommitted=true` |
| 26.7 | 整理最近七天，並包含目前 working tree | `days=7`, `include_uncommitted=true` |
| — | 整理上一週的工作摘要 | `from`/`to` of the **previous calendar week** |
| — | 整理這個月 | `from=<1st of this month>`, `to=<local today>` |

Notes:

- "近一個月" always maps to `days=30`, not the calendar-month length.
- **"上一週" and "最近一週" are different requests.** "最近一週" is a rolling
  window — the last 7 days including today (`days=7`). "上一週" is the *previous
  calendar week*: it ends before this week starts and never includes today, so it
  resolves to explicit `from`/`to` (e.g. asked on Thursday 2026-07-16 with weeks
  starting Monday → `from=2026-07-06`, `to=2026-07-12`). This distinction matters
  most for the weekly-summary report, where silently including today's half-done
  work makes the summary wrong.
- Week boundaries follow the user's convention. When it is unclear whether their
  week starts Monday or Sunday and the answer changes the range, ask rather than
  assume — the script has no notion of weeks, so this is entirely a model-layer
  decision.
- Calendar-period phrasings ("上一週", "這個月", "上個月") all resolve to explicit
  `from`/`to`. Only rolling windows use `days=N`.
- For multi-day requests with `include_uncommitted=true` (e.g. 26.7), the
  uncommitted working-tree content is attributed to **today only** — never spread
  across the earlier days in the range.

---

## 9. Output schema

### 9.1 Success

`ok=true`, exit code `0`. Fields (in emitted order):

| Field | Type | Notes |
| --- | --- | --- |
| `ok` | boolean | `true` |
| `timezone` | string | Resolved IANA name (or best-effort label under `system-offset`). |
| `timezone_source` | string | `explicit` \| `env:TZ` \| `system:/etc/localtime` \| `system-offset`. |
| `include_uncommitted` | boolean | Echoes the flag. |
| `today` | string | `YYYY-MM-DD` (local today, or the `--today` override). |
| `max_days` | integer | The day-span cap in effect (default `30`, or `--max-days`). |
| `errors` | array | Empty (`[]`) on success. |
| `mode` | string | `date` \| `days` \| `range`. |
| `days_count` | integer | Number of days in `dates`. |
| `dates` | array | One object per day: `{ date, start, end }`. |

Each `dates[]` entry:

```json
{
  "date": "2026-07-01",
  "start": "2026-07-01T00:00:00+08:00",
  "end": "2026-07-02T00:00:00+08:00"
}
```

### 9.2 Failure

`ok=false`, exit code `2`:

```json
{
  "ok": false,
  "errors": [ { "code": "<CODE>", "message": "<human-readable>", "...": "..." } ]
}
```

---

## 10. Error-code reference

| Code | Trigger | Extra fields |
| --- | --- | --- |
| `NO_DATE_SPEC` | None of `date` / `days` / `from`+`to` supplied. | — |
| `ARG_CONFLICT` | More than one mode supplied, or a shortcut collides with its flag (`7d`+`--days`, bare date+`--date`). | `provided` (mode conflict) |
| `DAYS_OUT_OF_RANGE` | `days` not an integer in `1..30` (e.g. `31`). | `requested_days`, `max_days` |
| `INVALID_DATE` | A date value is not `YYYY-MM-DD` or is not a real calendar date (e.g. `2026-02-30`). | `field`, `value` |
| `FROM_AFTER_TO` | `from` is later than `to`. | — |
| `FROM_WITHOUT_TO` | `from` supplied without `to`. | — |
| `TO_WITHOUT_FROM` | `to` supplied without `from`. | — |
| `TOO_MANY_DAYS` | `from`/`to` span exceeds 30 days. | `requested_days`, `max_days` (=30) |
| `UNKNOWN_SHORTCUT` | Positional token is neither `NNd` nor `YYYY-MM-DD`. | `value` |
| `INVALID_TIMEZONE` | `--timezone` is not a known IANA timezone. | — |

All failures set `ok=false`, populate `errors`, and exit `2`.
