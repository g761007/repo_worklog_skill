"""Integration tests for the `git-worklog` CLI.

Driven as a subprocess (`python3 -m git_worklog`), the way the skill and a user
actually invoke it — so these cover argument parsing, the JSON contract and exit
codes, not just the functions underneath.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, make_history_repo, rmtree, wm

SKILL_ROOT = os.path.join(ROOT, "git-worklog")


def run_cli(*args: str, env: "dict | None" = None):
    """Invoke the CLI as a subprocess. Returns (parsed_json_or_None, rc, stderr)."""
    full_env = os.environ.copy()
    full_env["PYTHONPATH"] = SKILL_ROOT
    if env:
        full_env.update(env)
    p = subprocess.run([sys.executable, "-m", "git_worklog", *args],
                       capture_output=True, text=True, env=full_env)
    try:
        parsed = json.loads(p.stdout)
    except json.JSONDecodeError:
        parsed = None
    return parsed, p.returncode, p.stderr


def write(path: str, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


class TestInterfaceLanguage(unittest.TestCase):
    """§6.2.13: the CLI's own messages are a separate setting from content."""

    def test_defaults_to_english_quietly(self):
        d, rc, err = run_cli("version")
        self.assertEqual(rc, 0, err)
        self.assertEqual(d["interface_language"], "en")
        self.assertNotIn("warnings", d)

    def test_unsupported_language_is_answered_not_ignored(self):
        # English-only messages are allowed for now (§6.2.13). Accepting the flag
        # in silence would look like it worked.
        d, rc, err = run_cli("--interface-language", "zh-TW", "version")
        self.assertEqual(rc, 0, err)
        self.assertEqual(d["interface_language"], "en")
        self.assertEqual([w["code"] for w in d["warnings"]],
                         ["INTERFACE_LANGUAGE_NOT_SUPPORTED"])

    def test_an_unusable_tag_fails_rather_than_falling_back(self):
        d, rc, _ = run_cli("--interface-language", "nonsense", "version")
        self.assertEqual(rc, 2)
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "LANGUAGE_INVALID")

    def test_json_keys_do_not_change_with_the_interface_language(self):
        # Keys are API. Translating them would break every caller (§6.2.13).
        a, _, _ = run_cli("version")
        b, _, _ = run_cli("--interface-language", "zh-TW", "version")
        self.assertEqual(set(a), set(b) - {"warnings"})


class TestVersion(unittest.TestCase):
    def test_reports_every_version_separately(self):
        d, rc, err = run_cli("version")
        self.assertEqual(rc, 0, err)
        self.assertTrue(d["ok"])
        from git_worklog import __version__
        self.assertEqual(d["cli_version"], __version__)
        self.assertEqual(d["layout_version"], wm.LAYOUT_VERSION)

    def test_layout_version_is_not_the_product_version(self):
        # They answer different questions and move on different clocks; a
        # product release must not imply a data migration. See issue #12.
        d, _, _ = run_cli("version")
        self.assertIsInstance(d["layout_version"], int)
        self.assertIsInstance(d["cli_version"], str)

    def test_text_mode_is_not_json(self):
        p = subprocess.run([sys.executable, "-m", "git_worklog", "--text", "version"],
                           capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": SKILL_ROOT})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("git-worklog", p.stdout)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(p.stdout)

    def test_version_flag(self):
        p = subprocess.run([sys.executable, "-m", "git_worklog", "--version"],
                           capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": SKILL_ROOT})
        self.assertEqual(p.returncode, 0)
        from git_worklog import __version__
        self.assertIn(__version__, p.stdout)


class TestNoArgs(unittest.TestCase):
    def test_bare_invocation_prints_help_and_succeeds(self):
        p = subprocess.run([sys.executable, "-m", "git_worklog"],
                           capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": SKILL_ROOT})
        self.assertEqual(p.returncode, 0)
        self.assertIn("usage", p.stdout.lower())


class TestDoctor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_history_repo()
        cls.home = tempfile.mkdtemp(prefix="gw_home_")

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.repo)
        rmtree(cls.home)

    def _run(self, *args):
        return run_cli("doctor", "--repo", self.repo, *args,
                       env={"GIT_WORKLOG_HOME": os.path.join(self.home, "state")})

    def _status(self, d, name):
        return next(c["status"] for c in d["checks"] if c["check"] == name)

    def test_healthy_repo_passes(self):
        d, rc, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertTrue(d["ok"])
        self.assertEqual(d["failed"], [])
        self.assertEqual(self._status(d, "repository"), "ok")
        self.assertEqual(self._status(d, "python"), "ok")
        self.assertEqual(self._status(d, "git"), "ok")

    def test_outside_a_git_repo_fails(self):
        work = tempfile.mkdtemp(prefix="gw_nogit_")
        try:
            d, rc, _ = run_cli("doctor", "--repo", work)
            self.assertEqual(rc, 1)
            self.assertFalse(d["ok"])
            self.assertIn("repository", d["failed"])
        finally:
            rmtree(work)

    def test_language_settings_are_checked(self):
        # These were skipped until the language contract existed. Now they are
        # real: "auto" is a valid answer, not a missing one.
        d, _, _ = self._run()
        self.assertEqual(self._status(d, "language"), "ok")
        self.assertEqual(self._status(d, "index_language"), "ok")

    def test_an_unusable_config_language_fails_doctor(self):
        # The point of checking it here: config.json is hand-edited, and a bad
        # tag otherwise surfaces mid-analysis with the diff already collected.
        work = tempfile.mkdtemp(prefix="gw_lang_")
        try:
            wdir = os.path.join(work, wm.WORKLOG_DIRNAME)
            os.makedirs(wdir)
            write(wm.config_path(wdir),
                  json.dumps({"schema_version": 1, "language": "chinese"}))
            d, rc, _ = self._run("--dir", wdir)
            self.assertEqual(self._status(d, "language"), "fail")
            self.assertEqual(rc, 1)
        finally:
            rmtree(work)

    def test_bare_zh_fails_doctor_rather_than_being_guessed(self):
        work = tempfile.mkdtemp(prefix="gw_lang_")
        try:
            wdir = os.path.join(work, wm.WORKLOG_DIRNAME)
            os.makedirs(wdir)
            write(wm.config_path(wdir),
                  json.dumps({"schema_version": 1, "index_language": "zh"}))
            d, _, _ = self._run("--dir", wdir)
            self.assertEqual(self._status(d, "index_language"), "fail")
        finally:
            rmtree(work)

    def test_legacy_layout_warns_but_does_not_fail(self):
        work = tempfile.mkdtemp(prefix="gw_legacy_")
        try:
            wdir = os.path.join(work, wm.LEGACY_WORKLOG_DIRNAME)
            write(wm.day_path(wdir, "2026-07-15", wm.LAYOUT_LEGACY),
                  wm.render_new_day_file("2026-07-15", "## 當日摘要\n\nX"))
            d, rc, _ = self._run("--dir", wdir)
            self.assertEqual(rc, 0)          # readable: not a failure
            self.assertTrue(d["ok"])
            self.assertEqual(self._status(d, "worklog_dir"), "warn")
        finally:
            rmtree(work)

    def test_config_newer_than_this_build_fails(self):
        work = tempfile.mkdtemp(prefix="gw_newcfg_")
        try:
            wdir = os.path.join(work, wm.WORKLOG_DIRNAME)
            os.makedirs(wdir)
            write(wm.config_path(wdir),
                  json.dumps({"schema_version": wm.LAYOUT_VERSION + 1}))
            d, rc, _ = self._run("--dir", wdir)
            self.assertEqual(rc, 1)
            self.assertIn("config", d["failed"])
        finally:
            rmtree(work)

    def test_layout_version_newer_than_this_build_fails(self):
        work = tempfile.mkdtemp(prefix="gw_newver_")
        try:
            wdir = os.path.join(work, wm.WORKLOG_DIRNAME)
            os.makedirs(wdir)
            write(wm.version_path(wdir), f"{wm.LAYOUT_VERSION + 1}\n")
            d, rc, _ = self._run("--dir", wdir)
            self.assertEqual(rc, 1)
            self.assertIn("layout_version", d["failed"])
        finally:
            rmtree(work)

    def test_missing_worklog_dir_is_not_a_failure(self):
        # A repo that has never run the tool is healthy, not broken.
        d, rc, _ = self._run("--dir", os.path.join(self.repo, "nope"))
        self.assertEqual(rc, 0)
        self.assertTrue(d["ok"])


class TestDoctorStateDir(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="gw_state_")
        self.repo = make_history_repo()

    def tearDown(self):
        rmtree(self.work)
        rmtree(self.repo)

    def _run(self, home):
        return run_cli("doctor", "--repo", self.repo,
                       env={"GIT_WORKLOG_HOME": home})

    def test_honours_git_worklog_home(self):
        home = os.path.join(self.work, "custom")
        d, _, _ = self._run(home)
        check = next(c for c in d["checks"] if c["check"] == "state_dir")
        self.assertEqual(check["value"], os.path.abspath(home))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_world_readable_state_dir_warns(self):
        # These files quote source and diffs from private repositories.
        home = os.path.join(self.work, "loose")
        os.makedirs(home, mode=0o755)
        os.chmod(home, 0o755)
        d, rc, _ = self._run(home)
        self.assertEqual(rc, 0)          # a warning, not a failure
        check = next(c for c in d["checks"] if c["check"] == "state_dir")
        self.assertEqual(check["status"], "warn")

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_owner_only_state_dir_is_ok(self):
        home = os.path.join(self.work, "tight")
        os.makedirs(home, mode=0o700)
        os.chmod(home, 0o700)
        d, _, _ = self._run(home)
        check = next(c for c in d["checks"] if c["check"] == "state_dir")
        self.assertEqual(check["status"], "ok")


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="gw_val_")
        self.dir = os.path.join(self.work, wm.WORKLOG_DIRNAME)

    def tearDown(self):
        rmtree(self.work)

    def _seed(self, dates=("2026-07-15",)):
        for d in dates:
            write(wm.day_path(self.dir, d, wm.LAYOUT_CURRENT),
                  wm.render_new_day_file(d, f"## 當日摘要\n\n{d} 的工作"))
        rows = [(d, f"{d} 的工作") for d in sorted(dates, reverse=True)]
        write(wm.index_path(self.dir), wm.render_index(rows, None, wm.LAYOUT_CURRENT))
        wm.ensure_data_dir(self.dir, "Asia/Taipei")

    def _run(self, *args):
        return run_cli("validate", "--dir", self.dir, *args)

    def test_missing_directory_is_a_usage_error_not_a_validation_failure(self):
        d, rc, _ = self._run()
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "NOT_FOUND")

    def test_healthy_worklog_passes(self):
        self._seed(("2026-07-15", "2026-07-14"))
        d, rc, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertTrue(d["ok"])
        self.assertEqual(d["day_count"], 2)
        self.assertEqual(d["errors"], [])

    def test_corrupt_day_file_fails(self):
        self._seed()
        write(wm.day_path(self.dir, "2026-07-15", wm.LAYOUT_CURRENT),
              "garbage, no markers\n")
        d, rc, _ = self._run()
        self.assertEqual(rc, 1)
        self.assertFalse(d["ok"])
        codes = [e["code"] for e in d["errors"]]
        self.assertIn("MISSING_GENERATED", codes)

    def test_index_row_without_a_day_file_fails(self):
        # The index's one job is navigation; a row pointing nowhere is broken.
        self._seed()
        os.remove(wm.day_path(self.dir, "2026-07-15", wm.LAYOUT_CURRENT))
        d, rc, _ = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("INDEX_ROW_WITHOUT_FILE", [e["code"] for e in d["errors"]])

    def test_day_file_missing_from_index_warns(self):
        self._seed()
        write(wm.day_path(self.dir, "2026-07-13", wm.LAYOUT_CURRENT),
              wm.render_new_day_file("2026-07-13", "## 當日摘要\n\nX"))
        d, rc, _ = self._run()
        self.assertEqual(rc, 0)          # rebuildable, not broken
        self.assertIn("DAY_FILE_NOT_INDEXED", [w["code"] for w in d["warnings"]])

    def test_missing_index_with_day_files_fails(self):
        self._seed()
        os.remove(wm.index_path(self.dir))
        d, rc, _ = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("INDEX_MISSING", [e["code"] for e in d["errors"]])

    def test_invalid_config_fails(self):
        self._seed()
        write(wm.config_path(self.dir), "{not json\n")
        d, rc, _ = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("CONFIG_INVALID", [e["code"] for e in d["errors"]])

    def test_legacy_layout_validates_with_a_warning(self):
        # Readable, therefore valid; not writable, therefore worth saying.
        wdir = os.path.join(self.work, wm.LEGACY_WORKLOG_DIRNAME)
        write(wm.day_path(wdir, "2026-07-15", wm.LAYOUT_LEGACY),
              wm.render_new_day_file("2026-07-15", "## 當日摘要\n\n舊資料"))
        write(wm.index_path(wdir),
              wm.render_index([("2026-07-15", "舊資料")], None, wm.LAYOUT_LEGACY))
        d, rc, err = run_cli("validate", "--dir", wdir)
        self.assertEqual(rc, 0, err)
        self.assertTrue(d["ok"])
        self.assertEqual(d["layout"], wm.LAYOUT_LEGACY)
        self.assertIn("LEGACY_LAYOUT", [w["code"] for w in d["warnings"]])

    def test_unchecked_areas_are_declared(self):
        # language_fields is no longer skipped: config and index languages are
        # checkable from a worklog alone. What replaced it is narrower and still
        # honest -- a result's language can only be judged against the manifest
        # of the run that produced it, which validate cannot see.
        self._seed()
        d, _, _ = self._run()
        self.assertEqual(
            {s["check"] for s in d["skipped"]},
            {"preview_records", "analysis_results", "result_language"})

    def test_a_day_without_a_summary_marker_warns_but_stays_valid(self):
        # It reads correctly today via the zh-TW heading fallback, and loses its
        # index summary the moment it is rewritten in another language. That is
        # a warning, not an error: nothing is wrong with the file yet.
        self._seed()
        d, rc, _ = self._run()
        self.assertTrue(d["ok"])
        self.assertEqual(rc, 0)
        self.assertIn("DAY_SUMMARY_UNMARKED", {w["code"] for w in d["warnings"]})

    def test_a_marked_day_does_not_warn(self):
        wdir = os.path.join(self.work, wm.WORKLOG_DIRNAME)
        write(wm.day_path(wdir, "2026-07-15"),
              wm.render_new_day_file(
                  "2026-07-15",
                  "## Daily summary\n" + wm.render_summary("Did the thing.")))
        d, _, _ = self._run()
        self.assertNotIn("DAY_SUMMARY_UNMARKED", {w["code"] for w in d["warnings"]})


class TestStatePaths(unittest.TestCase):
    """GIT_WORKLOG_HOME decides where working state lives."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="gw_paths_")

    def tearDown(self):
        rmtree(self.work)

    def _preview_id(self, env):
        fp = {
            "repository": {"root": "/repo", "branch": "main", "head": "abc123",
                           "worktree_fingerprint": None},
            "worklog": {"index_sha256": "idx1", "day_files": {}, "dir_fingerprint": "df1"},
            "params": {"timezone": "Asia/Taipei", "include_uncommitted": False},
        }
        from helpers import run_script
        d, _, err = run_script("preview_state.py",
                               ["create", "--now", "2026-07-15T12:00:00+08:00"],
                               stdin=json.dumps(fp), env=env)
        self.assertIsNotNone(d, err)
        self.assertTrue(d["ok"], err)
        return d["preview_id"]

    def test_git_worklog_home_wins_over_home(self):
        # A user who points GIT_WORKLOG_HOME somewhere means it, even on a box
        # where HOME is also unusual (CI, a sandbox, a shared runner).
        home = os.path.join(self.work, "home")
        explicit = os.path.join(self.work, "explicit")
        os.makedirs(home)
        pid = self._preview_id({"HOME": home, "GIT_WORKLOG_HOME": explicit})
        self.assertTrue(os.path.isfile(
            os.path.join(explicit, "previews", f"{pid}.json")))
        self.assertFalse(os.path.exists(os.path.join(home, ".git-worklog")))

    def test_defaults_under_home_when_unset(self):
        home = os.path.join(self.work, "home2")
        os.makedirs(home)
        pid = self._preview_id({"HOME": home})
        self.assertTrue(os.path.isfile(
            os.path.join(home, ".git-worklog", "previews", f"{pid}.json")))

    def test_state_never_lands_in_the_repository(self):
        # Working state in the repo would pollute the very history the tool reads.
        home = os.path.join(self.work, "home3")
        os.makedirs(home)
        self._preview_id({"HOME": home})
        self.assertFalse(os.path.exists(
            os.path.join(ROOT, ".git-worklog", "previews")))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_created_state_dir_is_owner_only(self):
        home = os.path.join(self.work, "home4")
        os.makedirs(home)
        self._preview_id({"HOME": home})
        created = os.path.join(home, ".git-worklog", "previews")
        self.assertEqual(os.stat(created).st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
