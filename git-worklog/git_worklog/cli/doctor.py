"""``git-worklog doctor`` — can this environment actually run the tool?

Each check reports ``ok`` / ``warn`` / ``fail`` with the value it observed, so a
failure names the thing that is wrong rather than the thing that broke later.
``fail`` means a real run would not work; ``warn`` means it would work but
something is worth knowing (a shallow clone limits history, a legacy layout
needs migrating).

The roadmap's §12.1 list is covered in full, language and index_language
included. A green check must never imply coverage it does not have, so anything
this command cannot check says so rather than being silently omitted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from git_worklog import config, language
from git_worklog import markers as wm
from git_worklog import paths

# git log --since-as-filter, which the collector relies on, landed in 2.37.
MIN_GIT = (2, 37)
MIN_PYTHON = (3, 9)


def _check(name: str, status: str, detail: str, **extra) -> dict:
    return {"check": name, "status": status, "detail": detail, **extra}


def _git(repo: str, *args: str) -> "tuple[int, str]":
    try:
        p = subprocess.run(["git", "-C", repo, *args],
                           capture_output=True, text=True)
        return p.returncode, (p.stdout or p.stderr).strip()
    except OSError as exc:
        return 127, str(exc)


def _check_python() -> dict:
    v = sys.version_info[:3]
    got = ".".join(str(x) for x in v)
    if v[:2] < MIN_PYTHON:
        return _check("python", "fail",
                      f"Python {got} is below the {'.'.join(str(x) for x in MIN_PYTHON)} floor.",
                      value=got)
    return _check("python", "ok", f"Python {got}.", value=got)


def _check_git_version() -> dict:
    if not shutil.which("git"):
        return _check("git", "fail", "git was not found on PATH.", value=None)
    code, out = _git(".", "--version")
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out or "")
    if code != 0 or not m:
        return _check("git", "fail", f"Could not read the git version: {out}", value=None)
    got = tuple(int(g) for g in m.groups(default="0"))
    text = ".".join(str(x) for x in got)
    if got[:2] < MIN_GIT:
        return _check("git", "fail",
                      f"git {text} is below {'.'.join(str(x) for x in MIN_GIT)}; "
                      "`git log --since-as-filter` is unavailable.", value=text)
    return _check("git", "ok", f"git {text}.", value=text)


def _check_repo(repo: str) -> "tuple[dict, bool]":
    code, out = _git(repo, "rev-parse", "--is-inside-work-tree")
    if code != 0 or out != "true":
        return _check("repository", "fail",
                      f"{os.path.abspath(repo)} is not inside a Git work tree.",
                      value=os.path.abspath(repo)), False
    return _check("repository", "ok", f"{os.path.abspath(repo)} is a Git work tree.",
                  value=os.path.abspath(repo)), True


def _check_shallow(repo: str) -> dict:
    code, out = _git(repo, "rev-parse", "--is-shallow-repository")
    if code != 0:
        return _check("shallow_clone", "warn", "Could not determine clone depth.")
    if out == "true":
        # Not a failure: a shallow clone works, it just cannot see far back, and
        # a day whose commits were truncated away would look empty rather than
        # wrong. Better said out loud than discovered in a worklog.
        return _check("shallow_clone", "warn",
                      "Shallow clone: history is truncated, so older days may "
                      "appear to have no commits. `git fetch --unshallow` to fix.",
                      value=True)
    return _check("shallow_clone", "ok", "Full clone.", value=False)


def _check_worktree(repo: str) -> dict:
    code, out = _git(repo, "status", "--porcelain")
    if code != 0:
        return _check("worktree", "warn", "Could not read the working tree status.")
    n = len([ln for ln in out.splitlines() if ln.strip()])
    if n:
        return _check("worktree", "ok",
                      f"{n} uncommitted change(s). Analysis covers them only when "
                      "explicitly asked (include_uncommitted).", value=n)
    return _check("worktree", "ok", "Clean.", value=0)


def _check_worklog_dir(worklog_dir: str) -> dict:
    if not os.path.isdir(worklog_dir):
        return _check("worklog_dir", "ok",
                      f"{worklog_dir} does not exist yet; it is created on first apply.",
                      value=None)
    layout = wm.detect_layout(worklog_dir)
    days = len(wm.list_day_dates(worklog_dir, layout))
    if layout == wm.LAYOUT_LEGACY:
        return _check("worklog_dir", "warn",
                      f"{worklog_dir} uses the pre-v0.6 flat layout ({days} day file(s) "
                      "at its root). It can be read but not written; run "
                      "migrate_legacy_worklog.py --from-dir to convert it.",
                      value=layout, day_count=days)
    return _check("worklog_dir", "ok",
                  f"{worklog_dir}: {layout} layout, {days} day file(s).",
                  value=layout, day_count=days)


def _check_config(worklog_dir: str) -> dict:
    path = wm.config_path(worklog_dir)
    if not os.path.exists(path):
        return _check("config", "ok",
                      "No config.json yet; it is written on first apply.", value=None)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _check("config", "fail", f"config.json is unreadable: {exc}", value=path)
    if not isinstance(cfg, dict):
        return _check("config", "fail", "config.json must contain an object.", value=path)
    got = cfg.get("schema_version")
    if got is None:
        return _check("config", "warn", "config.json has no schema_version.", value=path)
    if got > wm.LAYOUT_VERSION:
        return _check("config", "fail",
                      f"config.json declares schema_version {got}, newer than this "
                      f"build understands ({wm.LAYOUT_VERSION}). Upgrade git-worklog.",
                      value=got)
    return _check("config", "ok", f"config.json: schema_version {got}.", value=got)


def _check_language(worklog_dir: str) -> dict:
    """Whether config's language setting is one this build can honour.

    A bad tag here is worth surfacing now rather than at the point a run dies
    on it: config.json is edited by hand, and the failure it causes otherwise
    appears in the middle of an analysis with the diff already collected.
    """
    cfg = config.load(worklog_dir)
    raw = cfg.get("language")
    if raw is None:
        return _check("language", "ok",
                      "No language set; it resolves per run.", value=None)
    if isinstance(raw, str) and raw.strip().lower() == language.AUTO:
        return _check("language", "ok",
                      "language: auto — resolved per run from the request, the "
                      "environment or English.", value="auto")
    try:
        tag = language.normalize(raw)
    except language.LanguageError as exc:
        return _check("language", "fail",
                      f"config.json language: {exc.message}", value=raw)
    return _check("language", "ok", f"language: {tag}.", value=tag)


def _check_index_language(worklog_dir: str) -> dict:
    """What the index is written in, and whether that is pinned or inherited."""
    cfg = config.load(worklog_dir)
    raw = cfg.get("index_language")
    pinned = None
    if isinstance(raw, str) and raw.strip() and raw.strip().lower() != language.AUTO:
        try:
            pinned = language.normalize(raw)
        except language.LanguageError as exc:
            return _check("index_language", "fail",
                          f"config.json index_language: {exc.message}", value=raw)

    index_path = wm.index_path(worklog_dir)
    stamped = None
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                stamped = wm.index_language_of(fh.read())
        except (OSError, UnicodeDecodeError):
            stamped = None  # the index's own check owns reporting this

    if pinned:
        detail = f"index_language: {pinned} (pinned by config)."
        if stamped and stamped != pinned:
            detail += (f" The index on disk is {stamped} and will be rebuilt in "
                       f"{pinned}.")
        return _check("index_language", "ok", detail, value=pinned)
    if stamped:
        return _check("index_language", "ok",
                      f"index_language: {stamped} (fixed when the index was "
                      f"first built).", value=stamped)
    return _check("index_language", "ok",
                  "index_language: auto — the first build decides.", value="auto")


def _check_version_file(worklog_dir: str) -> dict:
    path = wm.version_path(worklog_dir)
    if not os.path.exists(path):
        return _check("layout_version", "ok",
                      "No VERSION yet; it is written on first apply.", value=None)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        got = int(raw)
    except (OSError, ValueError) as exc:
        return _check("layout_version", "fail", f"VERSION is unreadable: {exc}", value=path)
    if got > wm.LAYOUT_VERSION:
        return _check("layout_version", "fail",
                      f"The worklog declares layout version {got}, newer than this "
                      f"build understands ({wm.LAYOUT_VERSION}). Upgrade git-worklog.",
                      value=got)
    return _check("layout_version", "ok", f"Layout version {got}.", value=got)


def _check_state_dir() -> dict:
    home = paths.home()
    if not os.path.isdir(home):
        return _check("state_dir", "ok",
                      f"{home} does not exist yet; it is created on first preview.",
                      value=home)
    if not os.access(home, os.W_OK | os.X_OK):
        return _check("state_dir", "fail",
                      f"{home} is not writable; previews cannot be recorded.", value=home)
    mode = os.stat(home).st_mode & 0o777
    if os.name == "posix" and mode & 0o077:
        # These files quote source and diffs from private repositories.
        return _check("state_dir", "warn",
                      f"{home} is mode {mode:03o}; it holds code excerpts, so 700 is "
                      "advisable.", value=home, mode=f"{mode:03o}")
    return _check("state_dir", "ok", f"{home} is writable.", value=home,
                  mode=f"{mode:03o}")


def _check_legacy_state_dir() -> dict:
    legacy = paths.legacy_home()
    if os.path.isdir(legacy) and os.path.abspath(legacy) != os.path.abspath(paths.home()):
        return _check("legacy_state_dir", "warn",
                      f"{legacy} is left over from before v0.7. Nothing reads it now — "
                      "previews there have long expired. Delete it when convenient.",
                      value=legacy)
    return _check("legacy_state_dir", "ok", "No leftover state directory.", value=None)


def _check_skill_compat() -> dict:
    from git_worklog.cli.version import SKILL_COMPAT_VERSION
    skill_md = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "SKILL.md")
    if not os.path.exists(skill_md):
        # A pip-only install has no skill body; that is a valid way to use the CLI.
        return _check("skill_compat", "ok",
                      "No SKILL.md alongside the package (CLI-only install).",
                      value=None)
    return _check("skill_compat", "ok",
                  f"Skill body found; compat version {SKILL_COMPAT_VERSION}.",
                  value=SKILL_COMPAT_VERSION)


def run(args) -> "tuple[dict, int]":
    repo = args.repo or "."
    worklog_dir = args.dir or os.path.join(repo, wm.WORKLOG_DIRNAME)

    checks = [_check_python(), _check_git_version()]
    repo_check, in_repo = _check_repo(repo)
    checks.append(repo_check)
    if in_repo:
        checks.append(_check_shallow(repo))
        checks.append(_check_worktree(repo))
    checks.append(_check_worklog_dir(worklog_dir))
    checks.append(_check_version_file(worklog_dir))
    checks.append(_check_config(worklog_dir))
    checks.append(_check_state_dir())
    checks.append(_check_legacy_state_dir())
    checks.append(_check_skill_compat())
    checks.append(_check_language(worklog_dir))
    checks.append(_check_index_language(worklog_dir))

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    return {
        "ok": not failed,
        "repo": os.path.abspath(repo),
        "worklog_dir": os.path.abspath(worklog_dir),
        "checks": checks,
        "failed": [c["check"] for c in failed],
        "warnings": [c["check"] for c in warned],
    }, (1 if failed else 0)


_GLYPH = {"ok": "✓", "warn": "!", "fail": "✗", "skipped": "-"}


def render_text(p: dict) -> str:
    lines = [f"git-worklog doctor — {p['repo']}", ""]
    for c in p["checks"]:
        lines.append(f"  {_GLYPH.get(c['status'], '?')} {c['check']:<18} {c['detail']}")
    lines.append("")
    if p["failed"]:
        lines.append(f"FAILED: {', '.join(p['failed'])}")
    elif p["warnings"]:
        lines.append(f"OK, with warnings: {', '.join(p['warnings'])}")
    else:
        lines.append("All checks passed.")
    return "\n".join(lines) + "\n"
