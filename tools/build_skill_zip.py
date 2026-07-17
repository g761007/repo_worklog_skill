#!/usr/bin/env python3
"""Build skill.zip — the archive a user unzips into their skills folder.

Roadmap Appendix A8. Packaging used to be manual, which is how the last release
shipped a zip containing `preview_state.py` (deleted a version earlier) and a
`config/` directory that had moved.

What ships is decided by ``git ls-files``, not by an exclusion list kept here.
The repository already states what is content and what is junk — that is what
`.gitignore` is — and a second list would only drift from it. So `__pycache__`,
`.omc/` and `*.egg-info/` are excluded by never being tracked, and a new kind of
build litter is excluded the day someone gitignores it, without touching this
file.

The consequence worth naming: this packages what is **committed**. Uncommitted
edits do not ship, which is the correct behaviour for a release artifact and a
surprise if you are testing a local change.

The archive stages the skill under `git-worklog/`, so the directory a user ends
up with is the name Claude Code triggers on. That needs no special handling now
that the repository directory is itself `git-worklog/` — the split into `skill/`
was dropped in PR 7a precisely so these two could not disagree.

Usage:
    python3 tools/build_skill_zip.py [--output skill.zip] [--check]

    --check  verify an existing archive instead of building one.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = "git-worklog"
DEFAULT_OUTPUT = os.path.join(REPO_ROOT, "skill.zip")

# Things whose absence means the skill is not a skill. Checked after building
# rather than trusted: a zip that is missing SKILL.md still unzips fine and fails
# only when someone tries to use it.
REQUIRED = (
    f"{SKILL_DIR}/SKILL.md",
    f"{SKILL_DIR}/agents/openai.yaml",
    f"{SKILL_DIR}/git_worklog/__init__.py",
    f"{SKILL_DIR}/git_worklog/cli/__init__.py",
    # The engine's config is package data; it is exactly the kind of file a
    # packaging step drops silently, and the CLI cannot resolve a model without
    # it.
    f"{SKILL_DIR}/git_worklog/data/provider_models.json",
)

# Never content, whatever git thinks. A belt-and-braces check on the output, not
# a filter on the input: if one of these ever appears, the tracked file list is
# wrong and that is worth failing over rather than quietly shipping.
FORBIDDEN_PARTS = ("__pycache__", ".omc", ".egg-info", ".DS_Store", ".pytest_cache")


def tracked_files() -> "list[str]":
    """Every committed file under the skill directory, repo-relative."""
    out = subprocess.run(
        ["git", "-C", REPO_ROOT, "ls-files", "-z", SKILL_DIR],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.decode("utf-8")
    return sorted(p for p in out.split("\0") if p)


def build(output: str) -> "list[str]":
    files = tracked_files()
    if not files:
        raise SystemExit(f"error: git tracks no files under {SKILL_DIR}/")

    # Sorted entries and ZIP_DEFLATED: two builds of one commit should differ
    # only by timestamp, so a diff of `unzip -l` is readable.
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            zf.write(os.path.join(REPO_ROOT, rel), arcname=rel)
    return files


def check(output: str) -> "list[str]":
    """Return a list of problems with the archive. Empty means it is sound."""
    problems = []
    if not os.path.isfile(output):
        return [f"{output} does not exist"]

    with zipfile.ZipFile(output) as zf:
        names = zf.namelist()
        bad = zf.testzip()
        if bad is not None:
            problems.append(f"corrupt entry: {bad}")

    for required in REQUIRED:
        if required not in names:
            problems.append(f"missing: {required}")

    for name in names:
        parts = name.split("/")
        if parts[0] != SKILL_DIR:
            # The unzipped directory name is the command name, so a stray
            # top-level entry is not cosmetic.
            problems.append(f"outside {SKILL_DIR}/: {name}")
        if any(p in part for part in parts for p in FORBIDDEN_PARTS):
            problems.append(f"build litter: {name}")

    return problems


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(description="Build the skill.zip release archive.")
    p.add_argument("--output", default=DEFAULT_OUTPUT,
                   help="Archive path (default: <repo>/skill.zip).")
    p.add_argument("--check", action="store_true",
                   help="Verify an existing archive instead of building one.")
    args = p.parse_args(argv)

    if not args.check:
        files = build(args.output)
        print(f"built {args.output} — {len(files)} file(s) under {SKILL_DIR}/")

    problems = check(args.output)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(args.output) as zf:
        size = os.path.getsize(args.output)
        print(f"ok: {len(zf.namelist())} entries, {size // 1024} KB, "
              f"unzips to {SKILL_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
