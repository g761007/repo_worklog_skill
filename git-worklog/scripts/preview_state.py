#!/usr/bin/env python3
"""Manage Git Worklog dry-run preview state and apply-time consistency.

A preview records a fingerprint of everything that must not have changed between
the dry-run and the apply: repository identity, branch, HEAD, working-tree
fingerprint, timezone, include_uncommitted, and — because the worklog is now a
directory, not one file — the index.md content hash, the per-date day-file
hashes (each ``"missing"`` when absent), and a fingerprint of the directory's
day-file listing. ``apply`` is only safe when a re-check finds all of these
identical, the preview has not already been applied, and it has not expired.

The ``worklog`` block passed to ``create`` / ``verify`` therefore looks like::

    "worklog": {
      "index_sha256": "<hash or 'missing'>",
      "day_files": {"2026-07-15": "<hash>", "2026-07-14": "missing"},
      "dir_fingerprint": "<hash of the sorted <date>.md listing>"
    }

A change to any target day file, to index.md, or to the set of day files (which
would change the rebuilt index) invalidates the preview.

State is stored outside the repository (``~/.git-worklog/previews/``, or
``$GIT_WORKLOG_HOME/previews/``) so a
dry-run never touches the working tree.

Subcommands:
  create   Persist a new preview and print its id.
  verify   Re-check a preview against the current state (optionally mark applied).
  show     Print a stored preview's metadata.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import _bootstrap  # noqa: F401 — must precede any git_worklog import

from git_worklog import paths

STATE_DIR = paths.previews_dir()
DEFAULT_TTL_SECONDS = 24 * 3600

# Fields compared between create-time and apply-time, with a stable label.
# Values may be scalars or dicts (e.g. worklog.day_files); ``!=`` compares both.
_CONSISTENCY_KEYS = [
    ("repository", "root", "repository"),
    ("repository", "branch", "branch"),
    ("repository", "head", "HEAD"),
    ("repository", "worktree_fingerprint", "working tree"),
    ("worklog", "index_sha256", "index.md content"),
    ("worklog", "day_files", "day files"),
    ("worklog", "dir_fingerprint", "worklog directory listing"),
    ("params", "timezone", "timezone"),
    ("params", "include_uncommitted", "include_uncommitted"),
    # §6.2.10: apply must not re-decide the language. A user who confirmed a
    # zh-TW preview and then asked for English is asking for a different
    # worklog, not the same one rendered differently -- so the preview goes
    # stale and a new one gets built and confirmed, rather than apply quietly
    # writing something nobody previewed.
    ("params", "language", "language"),
]


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _now(now_override: str | None) -> datetime:
    if now_override:
        dt = datetime.fromisoformat(now_override)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _load_input(path: str | None) -> dict:
    raw = open(path, "r", encoding="utf-8").read() if path and path != "-" else sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def _nested(data: dict, group: str, key: str):
    return (data.get(group) or {}).get(key)


def _preview_path(preview_id: str) -> str:
    return os.path.join(STATE_DIR, f"{preview_id}.json")


def cmd_create(args: argparse.Namespace) -> int:
    data = _load_input(args.input)
    preview_sha = _nested(data, "worklog", "preview_sha256") or ""
    now = _now(args.now)
    basis = preview_sha or hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    short = basis[:6]
    preview_id = f"rw-{now.strftime('%Y%m%d')}-{short}"

    metadata = {
        "preview_id": preview_id,
        "created_at": now.isoformat(),
        "used": False,
        "used_at": None,
        "ttl_seconds": args.ttl_seconds,
        "repository": data.get("repository", {}),
        "worklog": data.get("worklog", {}),
        "params": data.get("params", {}),
    }
    paths.ensure_dir(STATE_DIR)
    with open(_preview_path(preview_id), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2)

    _emit({"ok": True, "preview_id": preview_id, "created_at": metadata["created_at"],
           "state_path": _preview_path(preview_id)})
    return 0


def _load_preview(preview_id: str) -> dict:
    path = _preview_path(preview_id)
    if not os.path.exists(path):
        _fail("UNKNOWN_PREVIEW", f"No preview found for id {preview_id!r}.", preview_id=preview_id)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cmd_show(args: argparse.Namespace) -> int:
    _emit({"ok": True, "preview": _load_preview(args.id)})
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    meta = _load_preview(args.id)
    current = _load_input(args.input)
    now = _now(args.now)

    mismatches = []
    for group, key, label in _CONSISTENCY_KEYS:
        expected = _nested(meta, group, key)
        actual = _nested(current, group, key)
        if expected != actual:
            mismatches.append({"field": label, "expected": expected, "actual": actual})

    already_used = bool(meta.get("used"))
    created = datetime.fromisoformat(meta["created_at"])
    age = (now - created).total_seconds()
    expired = age > meta.get("ttl_seconds", DEFAULT_TTL_SECONDS)

    consistent = not mismatches and not already_used and not expired

    result = {
        "ok": consistent,
        "preview_id": args.id,
        "consistent": consistent,
        "mismatches": mismatches,
        "already_applied": already_used,
        "expired": expired,
        "age_seconds": int(age),
    }

    if not consistent:
        result["reason"] = (
            "already applied" if already_used else
            "expired" if expired else
            "state changed since dry-run"
        )
        result["instruction"] = "Re-run the dry-run to produce a fresh preview before applying."
        _emit(result)
        return 3

    if args.mark_applied:
        meta["used"] = True
        meta["used_at"] = now.isoformat()
        with open(_preview_path(args.id), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        result["marked_applied"] = True

    _emit(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage Git Worklog preview state.")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="Create and persist a preview.")
    c.add_argument("--input", help="Preview fingerprint JSON, or '-' / omit for stdin.")
    c.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    c.add_argument("--now", help="Override current time (ISO 8601) for deterministic runs.")
    c.set_defaults(func=cmd_create)

    v = sub.add_parser("verify", help="Verify a preview against current state.")
    v.add_argument("--id", required=True)
    v.add_argument("--input", help="Current-state JSON, or '-' / omit for stdin.")
    v.add_argument("--mark-applied", action="store_true",
                   help="Record the preview as applied when it verifies.")
    v.add_argument("--now", help="Override current time (ISO 8601) for deterministic runs.")
    v.set_defaults(func=cmd_verify)

    s = sub.add_parser("show", help="Show stored preview metadata.")
    s.add_argument("--id", required=True)
    s.set_defaults(func=cmd_show)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (json.JSONDecodeError, OSError) as exc:
        _fail("IO_ERROR", f"{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
