"""``git-worklog coverage`` — which of these dates have an analysis behind them.

Report mode answers from the day files already on disk, so before it answers it
has to know which requested dates actually have one. Without this it degrades
into summarising commit messages, which is the one thing this tool exists not to
do.

A date with no day file is **not** automatically a gap: a day with no commits is
deliberately given no file. The engine (:mod:`git_worklog.analysis.coverage`)
keeps ``gap`` and ``no-commits`` apart, because conflating them sends the user
off to backfill days that can never produce a file.
"""

from __future__ import annotations

from git_worklog.analysis import AnalysisError
from git_worklog.analysis import coverage as engine
from git_worklog.analysis import history as ah


def run(args) -> "tuple[dict, int]":
    try:
        payload = engine.check(repo=args.repo, dir=args.dir, dates=args.dates,
                               timezone=args.timezone, date_field=args.date_field,
                               worklog_dir=args.worklog_dir)
        # Exit 1, not 0: a gap means the answer to "can I report on this?" is no.
        # It ran fine, and it found a problem — which is what 1 means here.
        return payload, (0 if payload["fully_covered"] else 1)
    except AnalysisError as exc:
        return {"ok": False, "errors": [
            {"code": exc.code, "message": exc.message, **exc.extra}]}, 2
    except ah.GitError as exc:
        return {"ok": False, "errors": [
            {"code": "GIT_ERROR", "message": str(exc)}]}, 2


def render_text(p: dict) -> str:
    if not p.get("ok"):
        return "".join(f"error: {e['message']}\n" for e in p.get("errors", []))

    lines = [f"git-worklog coverage — {p['worklog_dir']}\n\n"]
    symbol = {"covered": "✓", "gap": "✗", "no-commits": "·"}
    for row in p["dates"]:
        note = (f"{row['commit_count']} commit(s)" if row["commit_count"]
                else "no commits, no file expected")
        lines.append(f"  {symbol[row['status']]} {row['date']}  "
                     f"{row['status']:11} {note}\n")
    lines.append("\n")
    if p["fully_covered"]:
        lines.append("Every date with commits has a worklog.\n")
    else:
        lines.append(f"{len(p['gaps'])} gap(s) covering {p['gap_commit_count']} "
                     f"commit(s): {', '.join(p['gaps'])}\n")
        lines.append("Real work exists on those days that nothing has analysed.\n")
    return "".join(lines)
