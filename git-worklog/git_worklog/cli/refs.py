"""``git-worklog refs`` — turn a tag into the commit set it actually contains.

For report mode's version questions ("整理 v1.0.1 CHANGELOG"). The worklog is
indexed by calendar date and a version is bounded by a commit set; the two do not
map onto each other, so this emits the commits as the authority and the dates
only as an index for locating day files worth reading. See
:mod:`git_worklog.analysis.refs` for why converting a tag to a date span is wrong
in both directions.
"""

from __future__ import annotations

from git_worklog.analysis import AnalysisError
from git_worklog.analysis import refs as engine


def run(args) -> "tuple[dict, int]":
    try:
        return engine.resolve(repo=args.repo, tag=args.tag, from_ref=args.from_ref,
                              to_ref=args.to_ref, list_tags_only=args.list_tags,
                              timezone=args.timezone,
                              date_field=args.date_field), 0
    except AnalysisError as exc:
        return {"ok": False, "errors": [
            {"code": exc.code, "message": exc.message, **exc.extra}]}, 2
    except engine.GitError as exc:
        return {"ok": False, "errors": [
            {"code": "GIT_ERROR", "message": str(exc)}]}, 2


def render_text(p: dict) -> str:
    if not p.get("ok"):
        lines = []
        for e in p.get("errors", []):
            lines.append(f"error: {e['message']}\n")
            for tag in e.get("available_tags", []):
                lines.append(f"  available: {tag}\n")
        return "".join(lines)

    if "tags" in p:
        if not p["tags"]:
            return "No tags in this repository.\n"
        return "".join(f"{t}\n" for t in p["tags"])

    lines = [f"git-worklog refs — {p['commit_range']}\n"]
    if p["first_release"]:
        lines.append("  first release: the range runs from the root commit\n")
    span = p["date_span"]
    lines.append(f"  {p['commit_count']} commit(s)"
                 + (f", {span['from']} .. {span['to']}\n" if span else "\n"))
    lines.append(f"  ({p['date_field']} date, {p['timezone']})\n\n")
    for c in p["commits"]:
        lines.append(f"  {c['short_hash']}  {c['date']}  {c['subject']}\n")
    lines.append(f"\n{p['note']}\n")
    return "".join(lines)
