"""Resolve a tag/ref range into its authoritative commit set for report mode.

Used when the user asks about a *version* ("整理 v1.0.1 CHANGELOG") rather than
a *date range*.

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

So this emits the commit set as the authority, and the ``dates`` it derives are
only an index for locating the day files worth reading. The caller reconciles the
two by matching the short hashes the day files already carry in their
``相關 commits`` bullets.

Day attribution uses the same rule as :mod:`~git_worklog.analysis.history`: the
committer date by default, in the resolved local timezone, so a commit lands on
the same calendar day both would file it under.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from git_worklog.analysis import AnalysisError
# The Git plumbing comes from history rather than being copied a third time.
# worktree.py holds its own copy of these plus the diff parser; merging those is
# issue #20's job, and whatever shared home it lands on, this import moves with
# the others.
from git_worklog.analysis.history import UNIT_SEP, GitError, _git, _git_ok

_COMMIT_FORMAT = UNIT_SEP.join(["%H", "%h", "%an", "%cI", "%aI", "%s"])


def list_tags(repo: str) -> "list[str]":
    """Tags newest-first by the date the tag itself was created.

    ``creatordate`` reads an annotated tag's own timestamp and falls back to the
    commit date for a lightweight tag, so both kinds sort by when the release was
    actually cut. Sorting by version number instead would misorder any project
    that backports (v1.0.2 cut after v1.1.0).
    """
    out = _git(repo, ["tag", "--sort=-creatordate", "--format=%(refname:short)"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _resolve_previous_tag(repo: str, tag: str) -> "str | None":
    """The tag cut immediately before ``tag``, or None if it is the first."""
    tags = list_tags(repo)
    if tag not in tags:
        return None
    idx = tags.index(tag)
    return tags[idx + 1] if idx + 1 < len(tags) else None


def _local_date(iso_ts: str, tz: ZoneInfo) -> str:
    return datetime.fromisoformat(iso_ts).astimezone(tz).date().isoformat()


def collect_range(repo: str, from_ref: "str | None", to_ref: str,
                  tz: ZoneInfo, date_field: str) -> "list[dict]":
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


def validate_spec(tag: "str | None", to_ref: "str | None",
                  from_ref: "str | None", list_tags_only: bool) -> None:
    """Check the ref arguments make a coherent request. Raises AnalysisError.

    Separate from :func:`resolve` so it runs before any Git call: "you asked for
    nothing" should not depend on whether the directory is a repository.
    """
    if not list_tags_only and not tag and not to_ref:
        raise AnalysisError("NO_REF_SPEC",
                            "Provide --tag, or --to-ref (optionally with "
                            "--from-ref), or --list-tags.")
    if tag and to_ref:
        raise AnalysisError("ARG_CONFLICT", "--tag and --to-ref are mutually exclusive.")
    if from_ref and not to_ref:
        raise AnalysisError("FROM_REF_WITHOUT_TO_REF", "--from-ref requires --to-ref.")


def resolve(repo: str = ".", tag: "str | None" = None,
            from_ref: "str | None" = None, to_ref: "str | None" = None,
            list_tags_only: bool = False, timezone: str = "UTC",
            date_field: str = "committer") -> dict:
    """Resolve the requested range. Raises AnalysisError or GitError."""
    validate_spec(tag, to_ref, from_ref, list_tags_only)

    if not _git_ok(repo, ["rev-parse", "--is-inside-work-tree"]):
        raise AnalysisError("NOT_A_GIT_REPO",
                            "The target directory is not inside a Git repository.",
                            path=repo)

    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise AnalysisError("INVALID_TIMEZONE", f"Unknown IANA timezone: {timezone}.")

    if list_tags_only:
        return {"ok": True, "tags": list_tags(repo)}

    if to_ref:
        for ref in (r for r in (to_ref, from_ref) if r):
            if not _git_ok(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]):
                raise AnalysisError("UNKNOWN_REF",
                                    f"Ref not found in this repository: {ref}.", ref=ref)
        resolved_tag, prev_tag, first_release = to_ref, from_ref, from_ref is None
    else:
        tags = list_tags(repo)
        if not tags:
            raise AnalysisError("NO_TAGS",
                                "This repository has no tags, so no version range "
                                "can be resolved. Ask the user for an explicit date "
                                "range instead.")
        if tag not in tags:
            raise AnalysisError("UNKNOWN_TAG", f"Tag not found: {tag}.",
                                tag=tag, available_tags=tags)
        resolved_tag = tag
        prev_tag = _resolve_previous_tag(repo, tag)
        # The oldest tag has no predecessor: the range runs from the root commit.
        # Reported rather than silently assumed -- "everything ever" is a very
        # different answer from "changes since the last release".
        first_release = prev_tag is None

    commits = collect_range(repo, prev_tag, resolved_tag, tz, date_field)
    dates = sorted({c["date"] for c in commits})

    return {
        "ok": True,
        "tag": resolved_tag,
        "prev_tag": prev_tag,
        "first_release": first_release,
        "commit_range": f"{prev_tag}..{resolved_tag}" if prev_tag else resolved_tag,
        "date_field": date_field,
        "timezone": timezone,
        "commit_count": len(commits),
        "commits": commits,
        "dates": dates,
        "date_span": {"from": dates[0], "to": dates[-1]} if dates else None,
        "note": "commits[] is authoritative; dates[] only locates the day files "
                "worth reading. A day file may describe commits outside this "
                "range -- reconcile by short_hash.",
    }


__all__ = ["GitError", "collect_range", "list_tags", "resolve", "validate_spec"]
