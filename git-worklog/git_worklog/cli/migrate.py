"""``git-worklog migrate`` — move a legacy worklog into ``.git-worklog/``.

On the CLI surface since roadmap §2.4, and never part of a normal run: it is
invoked explicitly or not at all. Dry-run is the default, which is why ``--apply``
exists rather than a ``--dry-run`` flag — the safe direction is the one you get
by not thinking about it.

It never deletes the source and never overwrites a day file that already exists.
Corrupt legacy markers end the run rather than produce a half-migrated worklog:
the engine (:mod:`git_worklog.migrate`) refuses instead of guessing a repair.
"""

from __future__ import annotations

from git_worklog import migrate as engine


def run(args) -> "tuple[dict, int]":
    try:
        return engine.run(from_dir=args.from_dir, from_file=args.from_file,
                          dir=args.dir, timezone=args.timezone,
                          apply=args.apply), 0
    except engine.MigrateError as exc:
        return {"ok": False, "errors": [exc.as_error()]}, 2


def render_text(p: dict) -> str:
    if not p.get("ok"):
        return "".join(f"error: {e['message']}\n" for e in p.get("errors", []))

    lines = [f"git-worklog migrate — {p['mode']}\n"]
    lines.append(f"  source : {p['source']}  ({p['source_kind']})\n")
    lines.append(f"  target : {p['worklog_dir']}\n\n")

    for change in p["planned_changes"]:
        mark = "+" if change["action"] == "create" else "="
        note = "" if change["action"] == "create" else "  (exists, left alone)"
        lines.append(f"  {mark} {change['date']}{note}\n")
    if not p["planned_changes"]:
        lines.append("  nothing to migrate\n")

    lines.append(f"\n{p['note']}\n")
    return "".join(lines)
