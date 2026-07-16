#!/usr/bin/env python3
"""Collect Git repository metadata and per-day commit facts for Git Worklog.

This script gathers the *raw material* a Day Subagent needs. It never writes
summaries and never filters by author identity -- the worklog is a project log,
not a personal report. It reports repository state and, for a given day window,
the commits whose chosen date field falls inside the half-open interval, each
with its changed-file list (rename/copy aware), merge status, and a preliminary
revert flag.

Actual patch reading is intentionally left to the subagent (``git show``); this
keeps the JSON bounded while still giving the analyst an exact file/commit index.

Commits whose changed files fall entirely inside the worklog output directory
(``PROJECT_WORKLOG/`` by default, ``--worklog-dir`` to override) are the
worklog's own self-referential output and are dropped from ``commits[]``
entirely -- not counted, not reported. A commit that touches both the worklog
directory and real files is kept, with only its worklog-directory files
removed.

Modes:
  --info-only            Emit only repository metadata (root, branch, HEAD, ...).
  --since ISO --until ISO Emit metadata + commits inside [since, until).

Output is a single JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta

import worklog_markers as wm

RECORD_SEP = "\x1e"
UNIT_SEP = "\x1f"

_COMMIT_FORMAT = UNIT_SEP.join([
    "%H", "%h", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%P", "%s", "%b",
])

# Commits whose changed files fall entirely inside this directory are the
# worklog's own output (e.g. "chore(docs): 補充 XX 專案工作日誌") and are
# excluded as self-referential -- see collect_commits().
DEFAULT_WORKLOG_DIR = wm.WORKLOG_DIRNAME


class GitError(RuntimeError):
    pass


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _fail(code: str, message: str, **extra) -> None:
    _emit({"ok": False, "errors": [{"code": code, "message": message, **extra}]})
    sys.exit(2)


def _git(repo: str, args: list[str], binary: bool = False):
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def _git_ok(repo: str, args: list[str]) -> bool:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def repo_info(repo: str) -> dict:
    if not _git_ok(repo, ["rev-parse", "--is-inside-work-tree"]):
        _fail("NOT_A_GIT_REPO",
              "The target directory is not inside a Git repository.",
              path=repo)
    root = _git(repo, ["rev-parse", "--show-toplevel"]).strip()
    has_commits = _git_ok(repo, ["rev-parse", "--verify", "--quiet", "HEAD"])
    # symbolic-ref resolves the branch even on an unborn branch (empty repo);
    # it fails only on a detached HEAD, which implies there are commits.
    sym = subprocess.run(
        ["git", "-C", repo, "symbolic-ref", "--quiet", "--short", "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    if sym.returncode == 0:
        branch = sym.stdout.decode("utf-8", "replace").strip()
        detached = False
    else:
        branch = None
        detached = True
    head = _git(repo, ["rev-parse", "HEAD"]).strip() if has_commits else None
    short_head = _git(repo, ["rev-parse", "--short", "HEAD"]).strip() if has_commits else None
    dirty = bool(_git(repo, ["status", "--porcelain"]).strip())
    return {
        "root": root,
        "branch": branch,
        "detached_head": detached,
        "head": head,
        "short_head": short_head,
        "has_commits": has_commits,
        "dirty_worktree": dirty,
    }


def _parse_raw(blob: str) -> list[dict]:
    """Parse ``git diff-tree --raw -z`` output into ordered file entries."""
    tokens = blob.split("\x00")
    tokens = [t for t in tokens if t != ""]
    entries: list[dict] = []
    i = 0
    while i < len(tokens):
        meta = tokens[i]
        if not meta.startswith(":"):
            # Defensive: unexpected token, stop to avoid mis-parsing.
            break
        fields = meta[1:].split(" ")
        # :<oldmode> <newmode> <oldsha> <newsha> <status>
        oldmode, newmode, oldsha, newsha, status = fields[:5]
        code = status[0]
        similarity = status[1:] if len(status) > 1 else None
        if code in ("R", "C"):
            old_path = tokens[i + 1]
            new_path = tokens[i + 2]
            i += 3
        else:
            old_path = None
            new_path = tokens[i + 1]
            i += 2
        is_submodule = "160000" in (oldmode, newmode)
        entries.append({
            "status": code,
            "similarity": int(similarity) if similarity and similarity.isdigit() else None,
            "path": new_path,
            "old_path": old_path,
            "old_mode": oldmode,
            "new_mode": newmode,
            "old_sha": oldsha,
            "new_sha": newsha,
            "is_submodule": is_submodule,
        })
    return entries


def _parse_numstat(blob: str) -> list[tuple[str, str]]:
    """Parse ``git diff-tree --numstat -z`` output into ordered (add, del)."""
    tokens = blob.split("\x00")
    counts: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        head = tokens[i]
        if head == "":
            i += 1
            continue
        parts = head.split("\t")
        if len(parts) < 3:
            i += 1
            continue
        add, dele, first_path = parts[0], parts[1], parts[2]
        if first_path == "":
            # rename/copy: the two paths follow as separate NUL tokens.
            i += 3
        else:
            i += 1
        counts.append((add, dele))
    return counts


def commit_files(repo: str, sha: str) -> list[dict]:
    """Changed files for a non-merge commit, rename/copy aware."""
    base = ["diff-tree", "--no-commit-id", "-r", "--root", "-M", "-C", "-z"]
    raw = _parse_raw(_git(repo, [*base, "--raw", sha]))
    nums = _parse_numstat(_git(repo, [*base, "--numstat", sha]))
    files = []
    for idx, entry in enumerate(raw):
        add, dele = (nums[idx] if idx < len(nums) else ("-", "-"))
        is_binary = add == "-" and dele == "-"
        files.append({
            "status": entry["status"],
            "path": entry["path"],
            "old_path": entry["old_path"],
            "similarity": entry["similarity"],
            "additions": None if is_binary else int(add or 0),
            "deletions": None if is_binary else int(dele or 0),
            "is_binary": is_binary,
            "is_submodule": entry["is_submodule"],
            "old_sha": entry["old_sha"] if entry["is_submodule"] else None,
            "new_sha": entry["new_sha"] if entry["is_submodule"] else None,
        })
    return files


def _is_revert(subject: str, body: str) -> bool:
    return subject.startswith("Revert ") or "This reverts commit" in body


def _in_worklog_dir(path: str, worklog_dir: str) -> bool:
    """True if ``path`` lives inside the worklog output directory.

    The pre-v0.6 directory counts too, whatever ``worklog_dir`` is now. History
    predating the migration still touches it, and a commit that only ever wrote
    the worklog does not become real project work because the directory was
    later renamed.
    """
    for d in {worklog_dir, wm.LEGACY_WORKLOG_DIRNAME}:
        prefix = d.rstrip("/") + "/"
        if path == d or path.startswith(prefix):
            return True
    return False


def collect_commits(repo: str, since: datetime, until: datetime,
                    date_field: str, worklog_dir: str | None = None) -> list[dict]:
    # Coarse git-side window padded by 2 days; precise half-open filtering is
    # done in Python so the chosen date field and DST boundaries are exact.
    git_since = (since - timedelta(days=2)).isoformat()
    git_until = (until + timedelta(days=2)).isoformat()
    log = _git(repo, [
        "log",
        f"--since-as-filter={git_since}",
        f"--until={git_until}",
        "--date=iso-strict",
        f"--pretty=format:{RECORD_SEP}{_COMMIT_FORMAT}",
    ])
    commits = []
    for record in log.split(RECORD_SEP):
        if not record.strip("\n"):
            continue
        fields = record.lstrip("\n").split(UNIT_SEP)
        if len(fields) < 11:
            continue
        (full, short, an, ae, ad, cn, ce, cd, parents, subject, body) = fields[:11]
        chosen = ad if date_field == "author" else cd
        chosen_dt = datetime.fromisoformat(chosen)
        if not (since <= chosen_dt < until):
            continue
        parent_list = parents.split() if parents.strip() else []
        is_merge = len(parent_list) > 1
        files = [] if is_merge else commit_files(repo, full)
        if worklog_dir is not None and files:
            non_worklog_files = [f for f in files
                                  if not _in_worklog_dir(f["path"], worklog_dir)]
            if not non_worklog_files:
                # Self-referential commit (touches only the worklog's own
                # output) -- excluded entirely: not counted, not reported.
                continue
            files = non_worklog_files
        additions = sum(f["additions"] or 0 for f in files)
        deletions = sum(f["deletions"] or 0 for f in files)
        commits.append({
            "full_hash": full,
            "short_hash": short,
            "author_name": an,
            "author_email": ae,
            "author_date": ad,
            "committer_name": cn,
            "committer_email": ce,
            "committer_date": cd,
            "subject": subject,
            "body": body.rstrip("\n"),
            "parents": parent_list,
            "is_merge": is_merge,
            "is_revert_candidate": _is_revert(subject, body),
            "files": files,
            "diffstat": {
                "files_changed": len(files),
                "additions": additions,
                "deletions": deletions,
                "has_binary": any(f["is_binary"] for f in files),
                "has_submodule": any(f["is_submodule"] for f in files),
                "note": "combined merge diff omitted to avoid double counting"
                        if is_merge else None,
            },
        })
    # committer/author dates ascending within the day for readability.
    commits.sort(key=lambda c: c["committer_date" if date_field != "author" else "author_date"])
    return commits


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Collect Git history facts for Git Worklog.")
    p.add_argument("--repo", default=".", help="Repository path (default: current directory).")
    p.add_argument("--since", help="Day window start, ISO 8601 with offset (inclusive).")
    p.add_argument("--until", help="Day window end, ISO 8601 with offset (exclusive).")
    p.add_argument("--date-field", choices=["committer", "author"], default="committer",
                   help="Which date decides day attribution (default: committer).")
    p.add_argument("--info-only", action="store_true",
                   help="Emit repository metadata only; skip commit collection.")
    p.add_argument("--worklog-dir", default=DEFAULT_WORKLOG_DIR,
                   help="Worklog output directory; commits touching only this "
                        "directory are excluded as self-referential "
                        f"(default: {DEFAULT_WORKLOG_DIR}).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        info = repo_info(args.repo)
        payload = {"ok": True, "repository": info, "date_field": args.date_field}
        if args.info_only:
            _emit(payload)
            return 0
        if not args.since or not args.until:
            _fail("MISSING_WINDOW",
                  "Both --since and --until are required unless --info-only is set.")
        if not info["has_commits"]:
            payload.update({
                "window": {"since": args.since, "until": args.until},
                "commits": [],
                "note": "Repository has no commits yet.",
            })
            _emit(payload)
            return 0
        since = datetime.fromisoformat(args.since)
        until = datetime.fromisoformat(args.until)
        commits = collect_commits(args.repo, since, until, args.date_field, args.worklog_dir)
        payload.update({
            "window": {"since": args.since, "until": args.until},
            "commit_count": len(commits),
            "commits": commits,
        })
        _emit(payload)
        return 0
    except GitError as exc:
        _fail("GIT_ERROR", str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
