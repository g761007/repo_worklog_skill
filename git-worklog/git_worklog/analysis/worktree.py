"""Inspect the Git working tree for uncommitted changes.

Only used when ``include_uncommitted`` is on. It classifies changes into
staged / unstaged / untracked, flags binary and submodule entries, and computes
a stable working-tree fingerprint the preview state uses to detect drift between
dry-run and apply.

Uncommitted content may only ever be attributed to *today*: this module does not
guess historical dates from filesystem mtimes, because a file's mtime says when
it was last written, not when the work happened.

The git helpers and diff parser come from :mod:`~git_worklog.analysis.history`.
They were duplicated here while these were two standalone scripts that had to stay
individually portable; now that both ship in the package that rationale is gone,
so this module imports them rather than carrying its own copy (#20).
"""

from __future__ import annotations

import hashlib

from git_worklog.analysis import AnalysisError
# The git helpers and diff parser live in history. They were duplicated here only
# while these two modules were standalone scripts that had to stay individually
# portable (#20); now that both ship in the package, that rationale is gone.
# history's `_parse_raw` returns a fuller entry — it also needs old/new mode and
# sha for submodule and mode-change detection — and `_diff_files` below reads the
# subset it needs, so sharing the parser leaves this module's output unchanged.
# GitError is now the one class, so a working-tree git failure surfaces as
# GIT_ERROR through the same CLI catch as every other Git call.
from git_worklog.analysis.history import (  # noqa: F401
    GitError, _git, _git_ok, _parse_numstat, _parse_raw,
)


def _diff_files(repo: str, cached: bool) -> "list[dict]":
    base = ["diff", "-M", "-C", "-z"]
    if cached:
        base.append("--cached")
    raw = _parse_raw(_git(repo, [*base, "--raw"]))
    nums = _parse_numstat(_git(repo, [*base, "--numstat"]))
    files = []
    for idx, entry in enumerate(raw):
        add, dele = nums[idx] if idx < len(nums) else ("-", "-")
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
        })
    return files


def _untracked(repo: str) -> "list[dict]":
    out = _git(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
    paths = [p for p in out.split("\x00") if p]
    result = []
    for path in paths:
        # Sniff for a NUL byte in the first 8 KB, the same heuristic Git uses.
        try:
            with open(f"{repo}/{path}", "rb") as fh:
                is_binary = b"\x00" in fh.read(8000)
        except OSError:
            is_binary = False
        result.append({"status": "A", "path": path, "is_binary": is_binary})
    return result


def _fingerprint(repo: str) -> str:
    h = hashlib.sha256()
    h.update(_git(repo, ["status", "--porcelain=v2", "-z"], binary=True))
    h.update(b"\x00STAGED\x00")
    h.update(_git(repo, ["diff", "--cached"], binary=True))
    h.update(b"\x00UNSTAGED\x00")
    h.update(_git(repo, ["diff"], binary=True))
    h.update(b"\x00UNTRACKED\x00")
    for entry in _untracked(repo):
        h.update(entry["path"].encode("utf-8", "replace"))
        try:
            with open(f"{repo}/{entry['path']}", "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
        h.update(b"\x00")
    return h.hexdigest()


def inspect(repo: str) -> dict:
    """Everything uncommitted in ``repo``, plus a fingerprint of that state."""
    if not _git_ok(repo, ["rev-parse", "--is-inside-work-tree"]):
        raise AnalysisError("NOT_A_GIT_REPO",
                            "The target directory is not inside a Git repository.",
                            path=repo)
    staged = _diff_files(repo, cached=True)
    unstaged = _diff_files(repo, cached=False)
    untracked = _untracked(repo)
    return {
        "ok": True,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "counts": {
            "staged": len(staged),
            "unstaged": len(unstaged),
            "untracked": len(untracked),
        },
        "has_uncommitted": bool(staged or unstaged or untracked),
        "worktree_fingerprint": _fingerprint(repo),
    }
