"""``git-worklog preview`` — freeze what apply will write (roadmap §10.2).

Takes a collected run and the day prose the agent's LLM wrote for it, computes
every target file's final text, and stores the lot as one immutable record. What
comes back is a summary and a ``preview_id``; the bytes are on disk, not in the
conversation, and :mod:`git_worklog.cli.apply` writes them from there.

``--show`` and ``--cancel`` share this command because they act on the thing it
made. Creation is the bare form, matching §10.2, because it is the one on the
hot path — a skill runs it every time and reads the other two almost never.
"""

from __future__ import annotations

import json
import os
import sys

from git_worklog import preview as pv
from git_worklog import writer
from git_worklog.analysis import AnalysisError
from git_worklog.analysis import results as ar


def _load_entries(path: "str | None") -> dict:
    # Reading a TTY here would hang waiting for a human who was never asked to
    # type a worklog. The same guard rebuild_worklog_index.py needs.
    if path is None and sys.stdin.isatty():
        return {}
    raw = (open(path, "r", encoding="utf-8").read()
           if path and path != "-" else sys.stdin.read())
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise pv.PreviewError(
            "NO_ENTRIES",
            "Pass the rendered day files as {\"entries\": {\"<date>\": "
            "{\"generated_markdown\": \"...\"}}} on stdin or via --input.")
    return entries


def _run_dir_for(args) -> str:
    if args.run_dir:
        return args.run_dir
    if not args.run_id:
        raise AnalysisError("NO_RUN",
                            "Pass --run-id (from `analyze prepare`) or --run-dir.")
    return os.path.join(ar.analysis_dir(), args.run_id)


def _create(args) -> "tuple[dict, int]":
    entries = _load_entries(args.input)
    record = pv.build(
        run_dir=_run_dir_for(args), entries=entries, repo=args.repo,
        worklog_dir=args.dir, ttl_seconds=args.ttl_seconds,
        now=pv.now_utc(args.now),
    )
    state_path = pv.save(record)
    payload = pv.public(record)
    payload.update({"ok": True, "state_path": state_path})
    # The full text of every file, returned once so the caller can show the user
    # what they are approving. Apply never asks for it again.
    payload["previews"] = {d["date"]: d["content"] for d in record["payload"]["days"]}
    payload["index_preview"] = record["payload"]["index"]["content"]
    return payload, 0


def _show(args) -> "tuple[dict, int]":
    record = pv.load(args.show)
    verdict = pv.evaluate(record, pv.snapshot(record) if args.check else None,
                          pv.now_utc(args.now))
    payload = pv.public(record)
    payload.update({"ok": True, **verdict})
    return payload, (0 if verdict["applicable"] or not args.check else 1)


def _cancel(args) -> "tuple[dict, int]":
    record = pv.cancel(pv.load(args.cancel), pv.now_utc(args.now))
    payload = pv.public(record)
    payload.update({"ok": True})
    return payload, 0


def run(args) -> "tuple[dict, int]":
    try:
        if args.show:
            return _show(args)
        if args.cancel:
            return _cancel(args)
        return _create(args)
    except (pv.PreviewError, writer.WriterError, AnalysisError) as exc:
        return {"ok": False, "errors": [
            {"code": exc.code, "message": exc.message, **exc.extra}]}, 2
    except json.JSONDecodeError as exc:
        return {"ok": False, "errors": [
            {"code": "IO_ERROR", "message": f"Entries are not valid JSON: {exc}"}]}, 2


def render_text(p: dict) -> str:
    if not p.get("ok"):
        return "".join(f"error: {e['message']}\n" for e in p.get("errors", []))

    lines = [f"preview {p['preview_id']}  ({p.get('state', pv.PREVIEWED)})\n"]
    lang = p.get("language") or {}
    if lang.get("resolved"):
        lines.append(f"  language : {lang['resolved']} (via {lang.get('source')})\n")
    lines.append(f"  expires  : {p['expires_at']}\n")
    if p.get("reason"):
        lines.append(f"  reason   : {p['reason']}\n")
    lines.append("\n")
    for f in p.get("files", []):
        lines.append(f"  {f['action']:9} {f['path']}\n")
    if p.get("not_written"):
        lines.append(f"\n  analysed but not written: {', '.join(p['not_written'])}\n")
    for w in p.get("warnings", []):
        lines.append(f"  warning: {w.get('message', w)}\n")
    for m in p.get("mismatches", []):
        lines.append(f"  changed: {m['field']}\n")
    if p.get("state_path"):
        lines.append("\nNo files have been modified. Apply with "
                     f"`git-worklog apply --preview-id {p['preview_id']}`.\n")
    return "".join(lines)
