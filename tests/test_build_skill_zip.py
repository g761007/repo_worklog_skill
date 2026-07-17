"""Tests for tools/build_skill_zip.py — the release archive.

CI builds the real zip, unzips it, and runs the CLI out of it, so the happy path
is covered there against the real artifact. What is tested here is the part CI
cannot catch: the checks that are supposed to *reject* a bad archive. If those
quietly stopped rejecting anything, every build would still pass and the guard
would be decoration — which is how the previous release shipped a zip containing
a file that had been deleted a version earlier.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_skill_zip as bz  # noqa: E402

# A minimally sound archive: everything REQUIRED, nothing else.
SOUND = {name: "x" for name in bz.REQUIRED}


def _zip(path: str, entries: dict) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return path


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="rw_zip_")
        self.path = os.path.join(self.work, "skill.zip")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.work, ignore_errors=True)

    def test_a_sound_archive_has_no_problems(self):
        _zip(self.path, SOUND)
        self.assertEqual(bz.check(self.path), [])

    def test_a_missing_skill_md_is_caught(self):
        entries = dict(SOUND)
        del entries[f"{bz.SKILL_DIR}/SKILL.md"]
        _zip(self.path, entries)
        problems = bz.check(self.path)
        self.assertTrue(any("SKILL.md" in p for p in problems), problems)

    def test_a_missing_provider_config_is_caught(self):
        """Package data is the thing that silently stops shipping.

        An archive without it unzips fine and fails only when someone finally
        asks the CLI for a model.
        """
        entries = dict(SOUND)
        del entries[f"{bz.SKILL_DIR}/git_worklog/data/provider_models.json"]
        _zip(self.path, entries)
        problems = bz.check(self.path)
        self.assertTrue(any("provider_models.json" in p for p in problems), problems)

    def test_build_litter_is_caught(self):
        _zip(self.path, dict(SOUND, **{
            f"{bz.SKILL_DIR}/git_worklog/__pycache__/markers.pyc": "x"}))
        problems = bz.check(self.path)
        self.assertTrue(any("build litter" in p for p in problems), problems)

    def test_the_wrong_top_level_directory_is_caught(self):
        """The unzipped directory name is the command name, not cosmetics.

        This is the shape the previous release actually shipped: staged under the
        old `repo_worklog/`, so unzipping it gave you a skill answering to a name
        the docs no longer used.
        """
        _zip(self.path, dict(SOUND, **{"repo_worklog/SKILL.md": "x"}))
        problems = bz.check(self.path)
        self.assertTrue(any("outside" in p for p in problems), problems)

    def test_a_missing_archive_is_reported_not_ignored(self):
        problems = bz.check(os.path.join(self.work, "nope.zip"))
        self.assertTrue(problems)


class TestTrackedFiles(unittest.TestCase):
    """git decides what ships, so there is no exclusion list to drift."""

    def test_the_file_list_is_what_git_tracks(self):
        files = bz.tracked_files()
        self.assertTrue(files)
        self.assertTrue(all(f.startswith(bz.SKILL_DIR + "/") for f in files))
        self.assertIn(f"{bz.SKILL_DIR}/SKILL.md", files)

    def test_untracked_litter_is_absent_by_construction(self):
        # Not filtered out — never listed, because it is not committed.
        files = bz.tracked_files()
        for f in files:
            for part in bz.FORBIDDEN_PARTS:
                self.assertNotIn(part, f)


if __name__ == "__main__":
    unittest.main()
