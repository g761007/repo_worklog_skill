"""``git-worklog reindex`` — rebuild index.md from the day files.

The index is navigation only, and a pure function of the day files: a
date-descending table linking each one, each row carrying that day's 當日摘要.
That is what makes this safe to re-run and what makes it the repair for
``INDEX_WRITE_FAILED`` — when an apply writes the days and then fails to write
the index, nothing is lost, because the index can be derived again from what
landed.

Normal runs do not need it: ``apply`` rebuilds the index itself. Dry-run is the
default here too.

The index's MANUAL region is preserved byte-for-byte. Files that are not
``<date>.md`` (including ``index.md`` itself) are ignored.
"""

from __future__ import annotations

import os

from git_worklog import writer


def run(args) -> "tuple[dict, int]":
    try:
        worklog_dir = args.dir or writer.DEFAULT_DIR
        # plan_index is happy to plan an index for a directory that is not there
        # — correct for `apply`, which is allowed to create the worklog. Here it
        # is not: this command repairs a worklog that exists, so a --dir that
        # does not is a typo, and planning an empty index over it would answer a
        # question nobody asked. Same answer `validate` gives.
        if not os.path.isdir(worklog_dir):
            return {"ok": False, "errors": [{
                "code": "NOT_FOUND",
                "message": f"{os.path.abspath(worklog_dir)} does not exist.",
                "target": os.path.abspath(worklog_dir),
            }]}, 2
        plan = writer.plan_index(worklog_dir, {}, args.language)
        common = {k: v for k, v in plan.items() if k not in ("original", "content")}

        if not args.apply:
            return {"ok": True, "mode": "dry-run", **common,
                    "preview": plan["content"],
                    "note": "No files have been modified."}, 0

        writer.apply_index(plan["index_path"], plan["content"])
        with open(plan["index_path"], "r", encoding="utf-8") as fh:
            written = fh.read()
        return {"ok": True, "mode": "apply", **common,
                "written_sha256": writer.sha256(written),
                "note": ("index.md written atomically. No git add / commit / "
                         "push was performed.")}, 0
    except writer.WriterError as exc:
        return {"ok": False, "errors": [
            {"code": exc.code, "message": exc.message, **exc.extra}]}, 2
    except OSError as exc:
        return {"ok": False, "errors": [
            {"code": "IO_ERROR", "message": str(exc)}]}, 2


def render_text(p: dict) -> str:
    if not p.get("ok"):
        return "".join(f"error: {e['message']}\n" for e in p.get("errors", []))

    lines = [f"git-worklog reindex — {p['mode']}\n"]
    lines.append(f"  {p['index_path']}\n")
    lines.append(f"  {len(p['dates'])} day file(s) listed\n")
    lines.append(f"  action: {p['action']}\n")
    if p.get("preserved_index_manual"):
        lines.append("  the index's MANUAL region was kept\n")
    lines.append(f"\n{p['note']}\n")
    return "".join(lines)
