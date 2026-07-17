"""The analysis pipeline: Git facts in, validated per-day results out.

This is the engine behind ``git-worklog analyze prepare`` and ``analyze
collect`` (roadmap §7). It lives in the package rather than in ``scripts/``
because only ``git_worklog*`` is packaged: an installed CLI has no ``scripts/``
directory to shell out to, so anything the CLI needs has to be importable.

The split mirrors the pipeline's three deterministic stages:

* :mod:`~git_worklog.analysis.history` — what Git says happened.
* :mod:`~git_worklog.analysis.manifest` — what a Day Subagent is asked to do.
* :mod:`~git_worklog.analysis.results` — what came back, and whether it holds up.

None of them summarise code or decide wording. That is the hosting agent's LLM's
job (§6.1), and the CLI deliberately needs no model API key.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

# A run's on-disk layout (§7.1). Two directories, not one: `collect` can then
# tell a task that was never answered from a result nobody asked for. They live
# here rather than in the CLI because `preview` reads the same run and must not
# guess at the layout the CLI wrote.
TASKS_SUBDIR = "tasks"
RESULTS_SUBDIR = "results"
# Scratch space for a large day's fan-out. Deliberately *not* inside results/:
# `collect` treats every file there as a day's answer and fails the run over any
# it did not ask for, so a Day Subagent's per-group parts need somewhere to land
# that is not policed. See manifest.build's `parts_dir`.
PARTS_SUBDIR = "parts"


class AnalysisError(ValueError):
    """A failure that carries the wire code the CLI and scripts report.

    Mirrors :class:`git_worklog.language.LanguageError`: the pipeline's callers
    are two thin shells (a script and a CLI subcommand) that both owe the user
    one JSON object with a stable ``code``, so the code belongs on the
    exception rather than being re-derived from the message at each call site.
    """

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)
