"""``git-worklog apply`` — write the preview, and nothing else (roadmap §10.3).

The whole command is one argument: a preview id. That is the point. There is no
``--entries``, no stdin, no way to hand it content — so there is no way for what
lands on disk to differ from what the user approved. Everything else it needs to
re-check the world is in the record.

It refuses rather than adapts. A moved HEAD, an edited day file, a changed
analysis result, an expired or already-applied preview: each ends the same way,
with a code, a reason, and an instruction to build a fresh preview. Applying
"close enough" is how a worklog nobody reviewed gets committed.
"""

from __future__ import annotations

from git_worklog import preview as pv
from git_worklog import writer
from git_worklog.analysis import AnalysisError
from git_worklog.analysis import history as ah


def run(args) -> "tuple[dict, int]":
    try:
        record = pv.load(args.preview_id)
        payload = pv.apply(record, pv.now_utc(args.now))
        payload.update({
            "ok": True,
            "note": ("Day files written atomically as one transaction, then "
                     "index.md rebuilt. No git add / commit / push was "
                     "performed."),
        })
        return payload, 0
    except (pv.PreviewError, writer.WriterError, AnalysisError) as exc:
        return {"ok": False, "errors": [
            {"code": exc.code, "message": exc.message, **exc.extra}]}, 2
    except ah.GitError as exc:
        return {"ok": False, "errors": [
            {"code": "GIT_ERROR", "message": str(exc)}]}, 2


def render_text(p: dict) -> str:
    if not p.get("ok"):
        lines = []
        for e in p.get("errors", []):
            lines.append(f"error: {e['message']}\n")
            for m in e.get("mismatches", []):
                lines.append(f"  changed: {m['field']}\n")
            if e.get("instruction"):
                lines.append(f"  {e['instruction']}\n")
        return "".join(lines)

    lines = [f"applied {p['preview_id']}\n"]
    if p.get("broke_stale_lock"):
        lines.append("  note     : broke a lock left behind by a dead process\n")
    written = p.get("written_dates") or []
    lines.append(f"  days     : {', '.join(written) if written else 'none changed'}\n")
    lines.append(f"  index    : {p['index_action']}\n")
    if p.get("preserved_manual_dates"):
        lines.append(f"  MANUAL kept: {', '.join(p['preserved_manual_dates'])}\n")
    lines.append(f"\n{p['note']}\n")
    return "".join(lines)
