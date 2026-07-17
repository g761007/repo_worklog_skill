"""User-level state directory resolution.

Previews and analysis results are deliberately kept **outside** the target
repository — they are working state, not project content, and committing them
would pollute the very history the tool reads. They live under
``~/.git-worklog/`` (roadmap §5), overridable with ``GIT_WORKLOG_HOME`` so a
test, a sandbox, or a shared CI runner can point somewhere else.

``GIT_WORKLOG_HOME`` is a new variable, not a rename: there was never a
``REPO_WORKLOG_HOME``. The paths were hard-coded, which is why they moved here.
"""

from __future__ import annotations

import os

HOME_ENV = "GIT_WORKLOG_HOME"
HOME_DIRNAME = ".git-worklog"

# Where state lived before v0.7. Nothing is migrated automatically: previews
# expire in 24h and analysis files are diagnostic leftovers, so a stale copy is
# noise rather than loss. `doctor` points it out; deleting it is the user's call.
LEGACY_HOME_DIRNAME = ".repo_worklog"

PREVIEWS_SUBDIR = "previews"
ANALYSIS_SUBDIR = "analysis"
# Apply locks live here (roadmap §5). They are transient by nature -- a lock
# outliving its process is a bug to detect, not state to keep -- so they sit in
# tmp/ rather than earning a directory of their own.
TMP_SUBDIR = "tmp"

# Owner-only: these files quote source code and diffs from private repositories.
DIR_MODE = 0o700


def home() -> str:
    """The user-level state directory. ``GIT_WORKLOG_HOME`` wins when set."""
    override = os.environ.get(HOME_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.expanduser("~"), HOME_DIRNAME)


def legacy_home() -> str:
    """The pre-v0.7 state directory, whether or not it exists."""
    return os.path.join(os.path.expanduser("~"), LEGACY_HOME_DIRNAME)


def previews_dir() -> str:
    return os.path.join(home(), PREVIEWS_SUBDIR)


def analysis_dir() -> str:
    return os.path.join(home(), ANALYSIS_SUBDIR)


def tmp_dir() -> str:
    return os.path.join(home(), TMP_SUBDIR)


def ensure_dir(path: str) -> str:
    """Create ``path`` (and parents) owner-only, and return it.

    The mode is applied on creation only; an existing directory is left as the
    user set it. On filesystems without POSIX permissions the mode is ignored,
    which is why this is a best-effort hardening rather than a guarantee.
    """
    os.makedirs(path, mode=DIR_MODE, exist_ok=True)
    return path
