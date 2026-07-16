#!/usr/bin/env python3
"""Resolve a tag/ref range into its authoritative commit set for report mode.

Used by the ``git-worklog`` skill's report mode when the user asks about a
*version* ("整理 v1.0.1 CHANGELOG") rather than a *date range*.

Why this exists, and why the commit set is authoritative
-------------------------------------------------------
The worklog is indexed by calendar date; a version is bounded by a commit set.
The two do not map onto each other, so a report must not simply convert a tag
into a date span and read those day files -- that is wrong in both directions:

* **Over-inclusive:** a day file describes *everything* committed that day,
  including commits outside the tag range (work on another branch, or commits
  landed after the tag was cut that afternoon).
* **Under-inclusive:** a cherry-picked commit keeps its original author date, so
  it belongs to the range while sitting on a day outside the span.

So this script emits the commit set as the authority, and the ``dates`` it
derives are only an index for locating the day files worth reading. The caller
reconciles the two by matching the short hashes the day files already carry in
their ``相關 commits`` bullets.

Day attribution uses the same rule as ``collect_git_history.py``: the committer
date by default, in the resolved local timezone, so a commit lands on the same
calendar day both scripts would file it under.

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UNIT_SEP = "\x1f"
_COMMIT_FORMAT = UNIT_SEP.join(["%H", "%h", "%an", "%cI", "%aI", "%s"])


class GitError(RuntimeError):
    pass


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _git(repo: str, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout.decode("utf-8", "replace")


def _git_ok(repo: str, args: list[str]) -> bool:
    proc = subprocess.run(["git", "-C", repo, *args],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def list_tags(repo: str) -> list[str]:
    """Tags newest-first by the date the tag itself was created.

    ``creatordate`` reads an annotated tag's own timestamp and falls back to the
    commit date for a lightweight tag, so both kinds sort by when the release was
    actually cut. Sorting by version number instead would misorder any project
    that backports (v1.0.2 cut after v1.1.0).
    """
    out = _git(repo, ["tag", "--sort=-creatordate", "--format=%(refname:short)"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _resolve_previous_tag(repo: str, tag: str) -> str | None:
    """The tag cut immediately before ``tag``, or None if it is the first."""
    tags = list_tags(repo)
    if tag not in tags:
        return None
    idx = tags.index(tag)
    return tags[idx + 1] if idx + 1 < len(tags) else None


def _local_date(iso_ts: str, tz: ZoneInfo) -> str:
    return datetime.fromisoformat(iso_ts).astimezone(tz).date().isoformat()


def collect_range(repo: str, from_ref: str | None, to_ref: str,
                  tz: ZoneInfo, date_field: str) -> list[dict]:
    """Commits reachable from ``to_ref`` but not ``from_ref`` (exclusive start).

    ``from_ref`` of None means "since the root commit" -- the first release.
    """
    spec = f"{from_ref}..{to_ref}" if from_ref else to_ref
    out = _git(repo, ["log", spec, "--date=iso-strict",
                      f"--pretty=format:{_COMMIT_FORMAT}"])
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        fields = line.split(UNIT_SEP)
        if len(fields) < 6:
            continue
        full, short, author, cdate, adate, subject = fields[:6]
        chosen = adate if date_field == "author" else cdate
        commits.append({
            "full_hash": full,
            "short_hash": short,
            "author_name": author,
            "date": _local_date(chosen, tz),
            "subject": subject,
        })
    commits.reverse()  # oldest first, so a CHANGELOG reads chronologically
    return commits


def resolve(args: argparse.Namespace) -> dict:
    repo = args.repo
    if not _git_ok(repo, ["rev-parse", "--is-inside-work-tree"]):
        _fail("NOT_A_GIT_REPO",
              "The target directory is not inside a Git repository.", path=repo)

    try:
        tz = ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        _fail("INVALID_TIMEZONE", f"Unknown IANA timezone: {args.timezone}.")

    if args.list_tags:
        return {"ok": True, "tags": list_tags(repo)}

    if args.to_ref:
        to_ref, from_ref = args.to_ref, args.from_ref
        for ref in (r for r in (to_ref, from_ref) if r):
            if not _git_ok(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]):
                _fail("UNKNOWN_REF", f"Ref not found in this repository: {ref}.",
                      ref=ref)
        tag, prev_tag, first_release = to_ref, from_ref, from_ref is None
    else:
        tag = args.tag
        tags = list_tags(repo)
        if not tags:
            _fail("NO_TAGS", "This repository has no tags, so no version range "
                             "can be resolved. Ask the user for an explicit date "
                             "range instead.")
        if tag not in tags:
            _fail("UNKNOWN_TAG", f"Tag not found: {tag}.",
                  tag=tag, available_tags=tags)
        prev_tag = _resolve_previous_tag(repo, tag)
        # The oldest tag has no predecessor: the range runs from the root commit.
        # Reported rather than silently assumed -- "everything ever" is a very
        # different answer from "changes since the last release".
        first_release = prev_tag is None

    commits = collect_range(repo, prev_tag, tag, tz, args.date_field)
    dates = sorted({c["date"] for c in commits})

    return {
        "ok": True,
        "tag": tag,
        "prev_tag": prev_tag,
        "first_release": first_release,
        "commit_range": f"{prev_tag}..{tag}" if prev_tag else tag,
        "date_field": args.date_field,
        "timezone": args.timezone,
        "commit_count": len(commits),
        "commits": commits,
        "dates": dates,
        "date_span": {"from": dates[0], "to": dates[-1]} if dates else None,
        "note": "commits[] is authoritative; dates[] only locates the day files "
                "worth reading. A day file may describe commits outside this "
                "range -- reconcile by short_hash.",
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve a tag/ref range into its commit set for Git Worklog "
                    "report mode.")
    p.add_argument("--repo", default=".", help="Repository path (default: current directory).")
    p.add_argument("--tag", help="Tag to report on; the previous tag is found automatically.")
    p.add_argument("--from-ref", help="Explicit range start, exclusive (any ref).")
    p.add_argument("--to-ref", help="Explicit range end, inclusive (any ref).")
    p.add_argument("--list-tags", action="store_true",
                   help="List the repository's tags, newest-first.")
    p.add_argument("--timezone", default="UTC",
                   help="IANA timezone deciding each commit's calendar day (default: UTC).")
    p.add_argument("--date-field", choices=["committer", "author"], default="committer",
                   help="Which date decides day attribution (default: committer, "
                        "matching collect_git_history.py).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.list_tags and not args.tag and not args.to_ref:
        _fail("NO_REF_SPEC", "Provide --tag, or --to-ref (optionally with "
                             "--from-ref), or --list-tags.")
    if args.tag and args.to_ref:
        _fail("ARG_CONFLICT", "--tag and --to-ref are mutually exclusive.")
    if args.from_ref and not args.to_ref:
        _fail("FROM_REF_WITHOUT_TO_REF", "--from-ref requires --to-ref.")
    try:
        _emit(resolve(args))
        return 0
    except GitError as exc:
        _fail("GIT_ERROR", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
