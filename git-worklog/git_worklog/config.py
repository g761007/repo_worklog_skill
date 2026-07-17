"""Reading ``.git-worklog/config.json`` (roadmap §4.4, §6.2.7).

Until the language contract there was no reader at all: ``markers.render_config``
wrote the file on first apply and only ``schema_version`` was ever read back, by
``doctor`` and ``validate``, to check the layout was not from the future. This
module is the first code to treat config as *settings* rather than as a version
stamp.

Two facts about the file shape everything here:

``ensure_data_dir`` never rewrites an existing config. Every worklog created
since the ``.git-worklog/`` layout landed already carries ``"language": "auto"``
and ``"index_language": "auto"``, written by builds that ignored both. So
``"auto"`` on disk cannot be read as a choice — it is indistinguishable from a
file the user has never opened, and means exactly "nobody has decided".

The file is the user's. A malformed config is reported by ``doctor`` and
``validate``, which exist for that; it does not entitle this module to rewrite
it, and an unreadable one degrades to "no preference" rather than stopping a
run — losing a config override is a smaller failure than refusing to write a
worklog at all.
"""

from __future__ import annotations

import json
import os

from git_worklog import markers as wm
from git_worklog.language import AUTO


def load(worklog_dir: str) -> dict:
    """The config object, or ``{}`` when absent, unreadable or malformed."""
    path = wm.config_path(worklog_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _setting(config: dict, key: str) -> "str | None":
    """A configured string, or None for absent/``auto``/wrong-typed."""
    value = config.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() == AUTO:
        return None
    return value


def language(config: dict) -> "str | None":
    """The project's content-language override, if it set one (§6.2.7)."""
    return _setting(config, "language")


def index_language(config: dict) -> "str | None":
    """The project's fixed index language, if it set one (§6.2.12).

    ``auto`` here does not mean "no index language" — it means the index keeps
    whichever language it was first built in, which is recorded in the index
    file itself rather than written back here.
    """
    return _setting(config, "index_language")


def timezone(config: dict) -> "str | None":
    """The project's timezone override, if it set one."""
    return _setting(config, "timezone")


def find(repo: str = ".", worklog_dir: "str | None" = None) -> dict:
    """Load the config for a repository, honouring an explicit ``--dir``."""
    if worklog_dir is None:
        worklog_dir = os.path.join(repo, wm.WORKLOG_DIRNAME)
    return load(worklog_dir)
