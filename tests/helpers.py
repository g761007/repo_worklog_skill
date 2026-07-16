"""Shared test helpers: script runner and deterministic Git fixture builder.

The fixture reproduces a controlled history so tests can assert on real Git
behaviour (revert, rename, binary, multi-commit days) without depending on the
machine clock. Commit dates carry an explicit +08:00 offset so day-window
queries are reproducible regardless of the runner's timezone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "repo_worklog", "scripts")

sys.path.insert(0, SCRIPTS)
import worklog_markers as wm  # noqa: E402


def run_script(name: str, args: list[str], stdin: str | None = None,
               env: dict | None = None):
    """Run a skill script and return (parsed_json_or_None, returncode, stderr)."""
    full_env = os.environ.copy()
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if env:
        full_env.update(env)
    proc = subprocess.run(
        ["python3", os.path.join(SCRIPTS, name), *args],
        input=stdin, capture_output=True, text=True, env=full_env,
    )
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        parsed = None
    return parsed, proc.returncode, proc.stderr


def _git(repo: str, *args: str, env: dict | None = None) -> None:
    full_env = os.environ.copy()
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if env:
        full_env.update(env)
    subprocess.run(["git", "-C", repo, *args], check=True, env=full_env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _write(repo: str, rel: str, data, binary: bool = False) -> None:
    path = os.path.join(repo, rel)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb" if binary else "w") as fh:
        fh.write(data)


def _commit(repo: str, date: str, message: str, body: str | None = None,
            author: str | None = None) -> None:
    """Commit staged work. ``author`` overrides the author (not the committer).

    Passing ``author`` as ``"Name <email>"`` keeps the committer as the repo's
    configured identity, which mirrors real history (a reviewer landing someone
    else's patch) and lets tests prove the author -- not the committer -- is
    what reaches the manifest.
    """
    _git(repo, "add", "-A")
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    args = ["commit", "-q", "-m", message]
    if body:
        args += ["-m", body]
    if author:
        args += ["--author", author]
    _git(repo, *args, env=env)


def make_history_repo() -> str:
    """Build the standard fixture repo. Caller is responsible for cleanup.

    Timeline (committer/author dates, Asia/Taipei):
      2026-07-01  feat: add calc.add                    (A src/calc.py)
      2026-07-10  feat: add cache layer                 (A src/cache.py)
      2026-07-10  fix: correct cache key name           (M src/cache.py)
      2026-07-10  revert: drop cache layer              (D src/cache.py)
      2026-07-12  refactor: rename calc, add logo       (R calc->math_utils, A logo.png)
    """
    repo = tempfile.mkdtemp(prefix="rw_hist_")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    _git(repo, "config", "user.name", "Fixture Bot")
    _git(repo, "config", "user.email", "fixture@example.com")
    _git(repo, "config", "commit.gpgsign", "false")

    _write(repo, "src/calc.py", "def add(a, b):\n    return a + b\n")
    _commit(repo, "2026-07-01T10:00:00+08:00", "feat: add calc.add")

    _write(repo, "src/cache.py", "CACHE = {}\n\ndef get(k):\n    return CACHE.get(k)\n")
    _commit(repo, "2026-07-10T09:00:00+08:00", "feat: add cache layer")

    _write(repo, "src/cache.py", "CACHE = {}\n\ndef get(key):\n    return CACHE.get(key)\n")
    _commit(repo, "2026-07-10T11:00:00+08:00", "fix: correct cache key name")

    os.remove(os.path.join(repo, "src", "cache.py"))
    _commit(repo, "2026-07-10T15:00:00+08:00", "revert: drop cache layer",
            body="This reverts commit adding src/cache.py")

    _git(repo, "mv", "src/calc.py", "src/math_utils.py")
    _write(repo, "assets/logo.png", b"\x00\x01\x02BINARY\xff\xfe", binary=True)
    _commit(repo, "2026-07-12T14:00:00+08:00", "refactor: rename calc, add logo")

    return repo


def make_worklog_commit_repo() -> str:
    """Fixture mixing real work with self-referential worklog-output commits.

    Timeline (committer/author dates, Asia/Taipei):
      2026-07-20  feat: add greet.py                  (A src/greet.py)
      2026-07-20  chore(docs): worklog day20           (A PROJECT_WORKLOG/*.md; worklog-only)
      2026-07-21  chore(docs): worklog day21 only       (M PROJECT_WORKLOG/*.md; worklog-only, sole commit of the day)
      2026-07-22  chore(docs): mixed worklog + fix      (M PROJECT_WORKLOG/index.md, M src/greet.py)
    """
    repo = tempfile.mkdtemp(prefix="rw_wlog_")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    _git(repo, "config", "user.name", "Fixture Bot")
    _git(repo, "config", "user.email", "fixture@example.com")
    _git(repo, "config", "commit.gpgsign", "false")

    day_file = f"{wm.WORKLOG_DIRNAME}/2026-07-20.md"
    index_file = f"{wm.WORKLOG_DIRNAME}/{wm.INDEX_FILENAME}"

    _write(repo, "src/greet.py", "def greet(name):\n    return f'hi {name}'\n")
    _commit(repo, "2026-07-20T09:00:00+08:00", "feat: add greet.py")

    _write(repo, day_file, "# Project Worklog — 2026-07-20\n")
    _write(repo, index_file, "# Project Worklog\n")
    _commit(repo, "2026-07-20T18:00:00+08:00", "chore(docs): worklog day20")

    _write(repo, day_file, "# Project Worklog — 2026-07-20\n\nupdated\n")
    _commit(repo, "2026-07-21T09:00:00+08:00", "chore(docs): worklog day21 only")

    _write(repo, "src/greet.py", "def greet(name):\n    return f'hello {name}'\n")
    _write(repo, index_file, "# Project Worklog\n\nupdated\n")
    _commit(repo, "2026-07-22T09:00:00+08:00", "chore(docs): mixed worklog + fix")

    return repo


def make_multi_author_repo() -> str:
    """Fixture with a multi-author day and a single-author day.

    Every commit is authored by someone other than the configured committer
    ("Fixture Bot"), so a test that reads the committer instead of the author
    fails loudly rather than passing by coincidence.

    Timeline (committer/author dates, Asia/Taipei):
      2026-08-01  feat: add parser        (Alice Chen)
      2026-08-01  test: cover parser      (Bob Lin)
      2026-08-01  fix: parser edge case   (Alice Chen)  <- repeat author, must dedup
      2026-08-02  docs: document parser   (Carol Wu)    <- single-author day
    """
    repo = tempfile.mkdtemp(prefix="rw_authors_")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    _git(repo, "config", "user.name", "Fixture Bot")
    _git(repo, "config", "user.email", "fixture@example.com")
    _git(repo, "config", "commit.gpgsign", "false")

    alice = "Alice Chen <alice@example.com>"
    bob = "Bob Lin <bob@example.com>"
    carol = "Carol Wu <carol@example.com>"

    _write(repo, "src/parser.py", "def parse(text):\n    return text.split()\n")
    _commit(repo, "2026-08-01T09:00:00+08:00", "feat: add parser", author=alice)

    _write(repo, "tests/test_parser.py", "def test_parse():\n    assert True\n")
    _commit(repo, "2026-08-01T11:00:00+08:00", "test: cover parser", author=bob)

    _write(repo, "src/parser.py", "def parse(text):\n    return text.strip().split()\n")
    _commit(repo, "2026-08-01T15:00:00+08:00", "fix: parser edge case", author=alice)

    _write(repo, "docs/parser.md", "# Parser\n")
    _commit(repo, "2026-08-02T10:00:00+08:00", "docs: document parser", author=carol)

    return repo


def make_empty_repo() -> str:
    """An initialised repo with a committer identity but no commits."""
    repo = tempfile.mkdtemp(prefix="rw_empty_")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    _git(repo, "config", "user.name", "Fixture Bot")
    _git(repo, "config", "user.email", "fixture@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def rmtree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
