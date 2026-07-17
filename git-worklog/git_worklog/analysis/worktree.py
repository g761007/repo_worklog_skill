"""Inspect the Git working tree for uncommitted changes.

Only used when ``include_uncommitted`` is on. It classifies changes into
staged / unstaged / untracked, flags binary and submodule entries, and computes
a stable working-tree fingerprint the preview state uses to detect drift between
dry-run and apply.

Uncommitted content may only ever be attributed to *today*: this module does not
guess historical dates from filesystem mtimes, because a file's mtime says when
it was last written, not when the work happened.

The diff parsers here are near-duplicates of the ones in
:mod:`~git_worklog.analysis.history`. That duplication was deliberate when these
were two standalone scripts that had to stay individually portable; now that both
live in the package, the rationale is gone and they could be shared. Left alone
here on purpose -- merging them is a behaviour risk that belongs in its own change,
not smuggled into one about evidence coverage.
"""

from __future__ import annotations

import hashlib
import subprocess

from git_worklog.analysis import AnalysisError


class GitError(RuntimeError):
    pass


def _git(repo: str, args: "list[str]", binary: bool = False):
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def _git_ok(repo: str, args: "list[str]") -> bool:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def _parse_raw(blob: str) -> "list[dict]":
    tokens = [t for t in blob.split("\x00") if t != ""]
    entries: "list[dict]" = []
    i = 0
    while i < len(tokens):
        meta = tokens[i]
        if not meta.startswith(":"):
            break
        fields = meta[1:].split(" ")
        oldmode, newmode, _oldsha, _newsha, status = fields[:5]
        code = status[0]
        similarity = status[1:] if len(status) > 1 else None
        if code in ("R", "C"):
            old_path, new_path = tokens[i + 1], tokens[i + 2]
            i += 3
        else:
            old_path, new_path = None, tokens[i + 1]
            i += 2
        entries.append({
            "status": code,
            "similarity": int(similarity) if similarity and similarity.isdigit() else None,
            "path": new_path,
            "old_path": old_path,
            "is_submodule": "160000" in (oldmode, newmode),
        })
    return entries


def _parse_numstat(blob: str) -> "list[tuple[str, str]]":
    tokens = blob.split("\x00")
    counts: "list[tuple[str, str]]" = []
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
        i += 3 if first_path == "" else 1
        counts.append((add, dele))
    return counts


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
