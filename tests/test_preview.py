"""Tests for `git-worklog preview` / `apply` (roadmap §10, §17 PR 6, §21.2).

The claim under test is narrow and load-bearing: what apply writes is what the
user approved, and nothing else can get in. Two halves to that.

*Nothing else gets in* — apply's only argument is a preview id, so these tests
mostly probe the refusals: every way the world can move between preview and
apply must stop it, because a check that silently passes is indistinguishable
from no check at all.

*What the user approved* — the payload is compared byte-for-byte against what
lands on disk. That assertion is the one that would have failed under the old
design, where the content was re-supplied at apply time and only its
surroundings were fingerprinted.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from helpers import _git, _write, rmtree, run_cli

_REPO = None
_COMMIT = None
_DATE = "2026-07-15"
_GENERATED = ("## 當日摘要\n"
              "<!-- GIT_WORKLOG:SUMMARY:START -->\n"
              "新增 CacheLayer 查詢。\n"
              "<!-- GIT_WORKLOG:SUMMARY:END -->\n")


def setUpModule():
    global _REPO, _COMMIT
    _REPO = tempfile.mkdtemp(prefix="rw_pv_repo_")
    _git(_REPO, "init", "-q", "-b", "main")
    _git(_REPO, "config", "user.email", "t@example.com")
    _git(_REPO, "config", "user.name", "Tester")
    _git(_REPO, "config", "commit.gpgsign", "false")
    _write(_REPO, "src/cache.py",
           "class CacheLayer:\n    def get(self, key):\n        return key\n")
    _git(_REPO, "add", "-A")
    _git(_REPO, "commit", "-q", "-m", "add cache",
         env={"GIT_AUTHOR_DATE": f"{_DATE}T10:00:00+08:00",
              "GIT_COMMITTER_DATE": f"{_DATE}T10:00:00+08:00"})
    _COMMIT = subprocess.run(["git", "-C", _REPO, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()


def tearDownModule():
    if _REPO:
        rmtree(_REPO)


def _result(date: str) -> dict:
    return {
        "date": date, "timezone": "Asia/Taipei", "language": "zh-TW",
        "status": "complete", "confidence": "verified",
        "escalation_recommended": False, "escalation_reasons": [],
        "has_changes": True, "commits": [_COMMIT],
        "work_items": [{
            "title": "t", "summary": "s", "behavior_change": "b",
            "implementation": "i", "impact": "im", "files": ["src/cache.py"],
            "commits": [_COMMIT], "tests": [], "risks": [],
            "maintenance_notes": [], "follow_ups": [], "confidence": "verified",
            "evidence": [{"commit": _COMMIT, "file": "src/cache.py",
                          "symbol": "CacheLayer", "note": "adds lookup"}],
        }],
        "fixes": [], "refactors": [], "tests": [], "database_changes": [],
        "configuration_changes": [], "deployment_changes": [],
        "uncommitted_changes": [], "handoff_notes": [], "uncertainties": [],
        "evidence": [],
    }


class _Base(unittest.TestCase):
    """Each test gets its own state home and its own worklog directory.

    The worklog lives outside the fixture repo on purpose: an apply writes it,
    and a repo whose working tree changed between tests would make every
    fingerprint assertion depend on execution order.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="rw_pv_home_")
        self.wdir = os.path.join(tempfile.mkdtemp(prefix="rw_pv_out_"), ".git-worklog")
        self.env = {"GIT_WORKLOG_HOME": self.home}
        self.run_id = self._prepare()

    def tearDown(self):
        rmtree(self.home)
        rmtree(os.path.dirname(self.wdir))

    def _prepare(self) -> str:
        d, _, err = run_cli("analyze", "prepare", "--repo", _REPO,
                            "--from", _DATE, "--to", _DATE,
                            "--timezone", "Asia/Taipei",
                            "--language", "zh-TW", "--language-source",
                            "user-request", env=self.env)
        self.assertTrue(d and d["ok"], err)
        path = os.path.join(self.home, "analysis", d["run_id"], "results",
                            f"{_DATE}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_result(_DATE), fh, ensure_ascii=False)
        return d["run_id"]

    def preview(self, *extra: str, entries: "dict | None" = None):
        payload = {"entries": entries if entries is not None
                   else {_DATE: {"generated_markdown": _GENERATED}}}
        env = os.environ.copy()
        env.update(self.env)
        env["PYTHONPATH"] = __import__("helpers").SKILL_ROOT
        p = subprocess.run(
            ["python3", "-m", "git_worklog", "preview", "--run-id", self.run_id,
             "--repo", _REPO, "--dir", self.wdir, *extra],
            input=json.dumps(payload), capture_output=True, text=True, env=env)
        try:
            return json.loads(p.stdout), p.returncode, p.stderr
        except json.JSONDecodeError:
            return None, p.returncode, p.stdout + p.stderr

    def apply(self, preview_id: str, *extra: str):
        return run_cli("apply", "--preview-id", preview_id, *extra, env=self.env)

    def record(self, preview_id: str) -> dict:
        with open(os.path.join(self.home, "previews", f"{preview_id}.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)


class TestPreviewIsTheArtifact(_Base):
    """§10.1: the record carries the payload, not a receipt for it."""

    def test_apply_writes_the_stored_payload_byte_for_byte(self):
        # The assertion the old fingerprint-only design could not make. If apply
        # ever re-renders, re-reads a result, or re-asks the agent, this fails.
        d, _, err = self.preview()
        self.assertTrue(d["ok"], err)
        stored = self.record(d["preview_id"])["payload"]

        a, rc, err = self.apply(d["preview_id"])
        self.assertTrue(a["ok"], err)
        for target in stored["days"] + [stored["index"]]:
            with open(target["path"], encoding="utf-8") as fh:
                self.assertEqual(fh.read(), target["content"],
                                 f"{target['path']} is not what was previewed")

    def test_the_record_holds_every_field_apply_needs_to_stand_alone(self):
        # Apply is given a preview id and nothing else, so anything it must
        # re-check has to be in here. A missing field would not fail loudly --
        # it would compare None to None and pass.
        d, _, _ = self.preview()
        rec = self.record(d["preview_id"])
        for group, key in [("repository", "identity"), ("repository", "head"),
                           ("repository", "branch"), ("repository", "git_dir"),
                           ("repository", "submodule_fingerprint"),
                           ("worklog", "index_sha256"), ("worklog", "day_files"),
                           ("worklog", "dir_fingerprint"),
                           ("run", "tasks_fingerprint"),
                           ("run", "results_fingerprint"),
                           ("params", "worklog_dir"), ("params", "timezone"),
                           ("language", "resolved"), ("language", "source")]:
            self.assertIn(key, rec[group], f"{group}.{key} is not on the record")
        self.assertEqual(rec["language"]["resolved"], "zh-TW")
        self.assertTrue(rec["payload"]["days"][0]["content"])
        self.assertTrue(rec["payload"]["index"]["content"])
        self.assertEqual(rec["state"], "previewed")

    def test_two_previews_of_different_content_get_different_ids(self):
        # An escalation re-run, or any second attempt at a day, must not land on
        # the first preview's id: overwriting a record in place would take an
        # applied one's "applied" with it.
        a, _, _ = self.preview()
        b, _, _ = self.preview(entries={
            _DATE: {"generated_markdown": _GENERATED + "\n改寫後的內容。\n"}})
        self.assertNotEqual(a["preview_id"], b["preview_id"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.home, "previews", f"{a['preview_id']}.json")))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_the_record_is_owner_only(self):
        # §5.1: a preview holds the day's prose, which quotes a private
        # repository's code back at whoever can read the file.
        d, _, _ = self.preview()
        path = os.path.join(self.home, "previews", f"{d['preview_id']}.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_preview_writes_nothing(self):
        d, _, err = self.preview()
        self.assertTrue(d["ok"], err)
        self.assertFalse(os.path.exists(self.wdir),
                         "a dry-run created the worklog directory")

    def test_a_day_the_run_never_analysed_is_refused(self):
        # Otherwise the payload could carry a day that was never language-checked
        # or evidence-checked -- `collect`'s `unknown`, arriving one step later.
        d, rc, _ = self.preview(entries={
            "2026-07-14": {"generated_markdown": _GENERATED}})
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "UNKNOWN_DATE")
        self.assertEqual(rc, 2)

    def test_a_partial_run_cannot_be_previewed(self):
        # previewed is only reachable from collected, and preview re-runs that
        # verdict rather than trusting that collect was ever called.
        os.remove(os.path.join(self.home, "analysis", self.run_id, "results",
                               f"{_DATE}.json"))
        d, rc, _ = self.preview()
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "RUN_NOT_COLLECTED")
        self.assertEqual(d["errors"][0]["missing"], [_DATE])
        self.assertEqual(rc, 2)


class TestPreviewReuse(_Base):
    """§21.2 Preview Reuse: an applied preview is spent."""

    def test_applying_twice_is_refused_as_already_applied(self):
        d, _, _ = self.preview()
        a, rc, err = self.apply(d["preview_id"])
        self.assertTrue(a["ok"], err)

        again, rc, _ = self.apply(d["preview_id"])
        self.assertFalse(again["ok"])
        self.assertEqual(again["errors"][0]["code"], "PREVIEW_ALREADY_APPLIED")
        self.assertEqual(rc, 2)

    def test_already_applied_is_not_reported_as_stale(self):
        # The files an apply wrote no longer match the originals the record
        # noted, so an applied preview also mismatches -- against itself.
        # Reporting that as drift would send the reader hunting for an editor
        # who does not exist.
        d, _, _ = self.preview()
        self.apply(d["preview_id"])
        again, _, _ = self.apply(d["preview_id"])
        err = again["errors"][0]
        self.assertEqual(err["state"], "applied")
        self.assertNotIn("mismatches", err)


class TestPreviewExpiration(_Base):
    """§21.2 Preview Expiration."""

    def test_an_expired_preview_is_refused(self):
        d, _, err = self.preview("--ttl-seconds", "60",
                                 "--now", "2026-07-15T00:00:00+00:00")
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["expires_at"], "2026-07-15T00:01:00+00:00")

        a, rc, _ = self.apply(d["preview_id"], "--now", "2026-07-15T02:00:00+00:00")
        self.assertFalse(a["ok"])
        self.assertEqual(a["errors"][0]["code"], "PREVIEW_EXPIRED")
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.wdir), "an expired apply wrote files")

    def test_a_preview_inside_its_ttl_still_applies(self):
        # The other half: a TTL that refused everything would pass the test above
        # while being useless.
        d, _, _ = self.preview("--ttl-seconds", "3600",
                               "--now", "2026-07-15T00:00:00+00:00")
        a, rc, err = self.apply(d["preview_id"], "--now", "2026-07-15T00:30:00+00:00")
        self.assertTrue(a["ok"], err)
        self.assertEqual(rc, 0)


class TestStaleness(_Base):
    """§10.3: every way the world can move must stop the apply."""

    def _assert_stale(self, preview_id: str, field: str):
        a, rc, _ = self.apply(preview_id)
        self.assertFalse(a["ok"])
        self.assertEqual(a["errors"][0]["code"], "PREVIEW_STALE")
        self.assertEqual(rc, 2)
        fields = [m["field"] for m in a["errors"][0]["mismatches"]]
        self.assertIn(field, fields)

    def test_a_moved_head_makes_it_stale(self):
        d, _, _ = self.preview()
        _write(_REPO, "src/drift.py", "x = 1\n")
        _git(_REPO, "add", "-A")
        try:
            _git(_REPO, "commit", "-q", "-m", "drift")
            self._assert_stale(d["preview_id"], "HEAD")
        finally:
            _git(_REPO, "reset", "-q", "--hard", "HEAD~1")

    def test_an_edited_target_day_file_makes_it_stale(self):
        # The day file the preview planned to overwrite gained content after the
        # user looked at the diff. Writing the stored payload now would silently
        # discard whatever arrived in between.
        os.makedirs(os.path.join(self.wdir, "days"))
        with open(os.path.join(self.wdir, "days", f"{_DATE}.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# Project Worklog — 2026-07-15\n\n"
                     "<!-- GIT_WORKLOG:2026-07-15:GENERATED:START -->\nold\n"
                     "<!-- GIT_WORKLOG:2026-07-15:GENERATED:END -->\n\n"
                     "<!-- GIT_WORKLOG:2026-07-15:MANUAL:START -->\n\n"
                     "<!-- GIT_WORKLOG:2026-07-15:MANUAL:END -->\n")
        d, _, err = self.preview()
        self.assertTrue(d["ok"], err)
        with open(os.path.join(self.wdir, "days", f"{_DATE}.md"), "a",
                  encoding="utf-8") as fh:
            fh.write("\nedited after the preview\n")
        self._assert_stale(d["preview_id"], "day files")

    def test_a_new_day_file_appearing_makes_it_stale(self):
        # Nothing this preview names changed -- but the index is a function of
        # the whole directory, so its stored payload is now wrong.
        d, _, err = self.preview()
        self.assertTrue(d["ok"], err)
        os.makedirs(os.path.join(self.wdir, "days"), exist_ok=True)
        with open(os.path.join(self.wdir, "days", "2026-07-01.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# Project Worklog — 2026-07-01\n")
        self._assert_stale(d["preview_id"], "worklog directory listing")

    def test_an_edited_index_makes_it_stale(self):
        # index.md carries a human MANUAL region. Writing a payload built before
        # someone edited it would rebuild the table against a file that has since
        # moved on.
        os.makedirs(self.wdir)
        with open(os.path.join(self.wdir, "index.md"), "w", encoding="utf-8") as fh:
            fh.write("# Project Worklog\n\n"
                     "<!-- GIT_WORKLOG:INDEX:GENERATED:START -->\n"
                     "| 日期 | 摘要 |\n|---|---|\n"
                     "<!-- GIT_WORKLOG:INDEX:GENERATED:END -->\n\n"
                     "<!-- GIT_WORKLOG:INDEX:MANUAL:START -->\n\n"
                     "<!-- GIT_WORKLOG:INDEX:MANUAL:END -->\n")
        d, _, err = self.preview()
        self.assertTrue(d["ok"], err)
        with open(os.path.join(self.wdir, "index.md"), "a", encoding="utf-8") as fh:
            fh.write("\nappended after the preview\n")
        self._assert_stale(d["preview_id"], "index.md content")

    def test_an_edited_analysis_result_makes_it_stale(self):
        d, _, _ = self.preview()
        path = os.path.join(self.home, "analysis", self.run_id, "results",
                            f"{_DATE}.json")
        obj = _result(_DATE)
        obj["work_items"][0]["title"] = "rewritten after the preview"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        self._assert_stale(d["preview_id"], "analysis results")

    def test_changing_the_project_language_forces_a_new_preview(self):
        # §6.2.10: a project that switched language is asking for a different
        # worklog, not the same one rendered differently.
        d, _, _ = self.preview()
        os.makedirs(self.wdir, exist_ok=True)
        with open(os.path.join(self.wdir, "config.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"schema_version": 1, "language": "ja"}, fh)
        self._assert_stale(d["preview_id"], "project language setting")


class TestStateMachine(_Base):
    """§17 PR 6: the states, and what each one refuses."""

    def test_cancelled_previews_cannot_be_applied(self):
        d, _, _ = self.preview()
        c, rc, err = run_cli("preview", "--cancel", d["preview_id"], env=self.env)
        self.assertTrue(c["ok"], err)
        self.assertEqual(c["state"], "cancelled")

        a, rc, _ = self.apply(d["preview_id"])
        self.assertFalse(a["ok"])
        self.assertEqual(a["errors"][0]["code"], "PREVIEW_CANCELLED")
        self.assertFalse(os.path.exists(self.wdir))

    def test_an_applied_preview_cannot_be_cancelled(self):
        d, _, _ = self.preview()
        self.apply(d["preview_id"])
        c, rc, _ = run_cli("preview", "--cancel", d["preview_id"], env=self.env)
        self.assertFalse(c["ok"])
        self.assertEqual(c["errors"][0]["code"], "PREVIEW_NOT_OPEN")

    def test_a_record_left_in_confirmed_is_refused_not_retried(self):
        # confirmed is written before the first byte, so finding one means a
        # process died mid-apply and whether it wrote is unknown. Retrying would
        # be guessing; the tool says so instead.
        d, _, _ = self.preview()
        rec = self.record(d["preview_id"])
        rec["state"] = "confirmed"
        with open(os.path.join(self.home, "previews", f"{d['preview_id']}.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        a, rc, _ = self.apply(d["preview_id"])
        self.assertFalse(a["ok"])
        self.assertEqual(a["errors"][0]["code"], "PREVIEW_INTERRUPTED")

    def test_apply_records_confirmed_then_applied(self):
        d, _, _ = self.preview()
        self.assertEqual(self.record(d["preview_id"])["state"], "previewed")
        self.apply(d["preview_id"])
        rec = self.record(d["preview_id"])
        self.assertEqual(rec["state"], "applied")
        self.assertIsNotNone(rec["confirmed_at"])
        self.assertIsNotNone(rec["applied_at"])

    def test_unknown_preview_is_named_not_guessed(self):
        a, rc, _ = self.apply("rw-20260715-nosuch")
        self.assertFalse(a["ok"])
        self.assertEqual(a["errors"][0]["code"], "UNKNOWN_PREVIEW")
        self.assertEqual(rc, 2)

    def test_a_record_from_another_schema_is_refused(self):
        d, _, _ = self.preview()
        rec = self.record(d["preview_id"])
        rec["schema_version"] = 99
        with open(os.path.join(self.home, "previews", f"{d['preview_id']}.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        a, _, _ = self.apply(d["preview_id"])
        self.assertFalse(a["ok"])
        self.assertEqual(a["errors"][0]["code"], "PREVIEW_SCHEMA_MISMATCH")

    def test_show_reports_state_without_touching_anything(self):
        d, _, _ = self.preview()
        s, rc, err = run_cli("preview", "--show", d["preview_id"], env=self.env)
        self.assertTrue(s["ok"], err)
        self.assertEqual(s["state"], "previewed")
        self.assertTrue(s["applicable"])
        # The payload text is the bulk of the record and is already on screen
        # from the dry-run; show lists the files instead.
        self.assertNotIn("payload", s)
        self.assertEqual(len(s["files"]), 2)


class TestConcurrentApply(_Base):
    """§21.2 Concurrent Apply."""

    def test_a_second_apply_is_locked_out_while_one_is_writing(self):
        import sys
        sys.path.insert(0, __import__("helpers").SKILL_ROOT)
        os.environ["GIT_WORKLOG_HOME"] = self.home
        try:
            from git_worklog import preview as pv
            d, _, _ = self.preview()
            with pv.ApplyLock(self.wdir):
                a, rc, _ = self.apply(d["preview_id"])
            self.assertFalse(a["ok"])
            self.assertEqual(a["errors"][0]["code"], "APPLY_LOCKED")
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(self.wdir),
                             "a locked-out apply wrote files anyway")
        finally:
            os.environ.pop("GIT_WORKLOG_HOME", None)

    def test_the_lock_is_released_so_the_next_apply_proceeds(self):
        import sys
        sys.path.insert(0, __import__("helpers").SKILL_ROOT)
        os.environ["GIT_WORKLOG_HOME"] = self.home
        try:
            from git_worklog import preview as pv
            d, _, _ = self.preview()
            with pv.ApplyLock(self.wdir):
                pass
            a, rc, err = self.apply(d["preview_id"])
            self.assertTrue(a["ok"], err)
        finally:
            os.environ.pop("GIT_WORKLOG_HOME", None)

    def test_a_lock_left_by_a_dead_process_is_broken(self):
        # A crashed apply must not wedge the worklog forever -- but the lock is
        # only broken when its owner is provably gone, never on a timer.
        import sys
        sys.path.insert(0, __import__("helpers").SKILL_ROOT)
        os.environ["GIT_WORKLOG_HOME"] = self.home
        try:
            from git_worklog import paths
            from git_worklog import preview as pv
            lock = pv.ApplyLock(self.wdir)
            paths.ensure_dir(paths.tmp_dir())
            dead = subprocess.run(["python3", "-c", "import os; print(os.getpid())"],
                                  capture_output=True, text=True)
            with open(lock.path, "w", encoding="utf-8") as fh:
                json.dump({"pid": int(dead.stdout.strip()),
                           "host": __import__("socket").gethostname(),
                           "acquired_at": "2026-07-15T00:00:00+00:00"}, fh)
            d, _, _ = self.preview()
            a, rc, err = self.apply(d["preview_id"])
            self.assertTrue(a["ok"], err)
            self.assertTrue(a["broke_stale_lock"])
        finally:
            os.environ.pop("GIT_WORKLOG_HOME", None)

    def test_a_lock_held_by_a_live_process_on_this_host_is_not_broken(self):
        import sys
        sys.path.insert(0, __import__("helpers").SKILL_ROOT)
        os.environ["GIT_WORKLOG_HOME"] = self.home
        try:
            from git_worklog import paths
            from git_worklog import preview as pv
            lock = pv.ApplyLock(self.wdir)
            paths.ensure_dir(paths.tmp_dir())
            with open(lock.path, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(),
                           "host": __import__("socket").gethostname(),
                           "acquired_at": "2026-07-15T00:00:00+00:00"}, fh)
            d, _, _ = self.preview()
            a, _, _ = self.apply(d["preview_id"])
            self.assertFalse(a["ok"])
            self.assertEqual(a["errors"][0]["code"], "APPLY_LOCKED")
        finally:
            os.environ.pop("GIT_WORKLOG_HOME", None)


class TestApplyFailure(_Base):
    """§21.2 Transaction Rollback, at the preview level."""

    def test_a_failed_write_is_rolled_back_and_recorded_as_failed(self):
        # The record must not be left claiming nothing happened, and must not be
        # retryable: a rolled-back apply is a dead preview, not a paused one.
        import sys
        sys.path.insert(0, __import__("helpers").SKILL_ROOT)
        os.environ["GIT_WORKLOG_HOME"] = self.home
        try:
            from git_worklog import preview as pv
            from git_worklog import writer
            d, _, _ = self.preview()
            record = pv.load(d["preview_id"])

            def boom(*a, **k):
                raise OSError("injected write failure")

            real = writer.apply_days
            writer.apply_days = boom
            try:
                with self.assertRaises(pv.PreviewError) as caught:
                    pv.apply(record)
            finally:
                writer.apply_days = real

            self.assertEqual(caught.exception.code, "WRITE_FAILED")
            self.assertEqual(self.record(d["preview_id"])["state"], "failed")
            self.assertFalse(os.path.exists(os.path.join(self.wdir, "index.md")),
                             "the index was written despite the day write failing")

            a, _, _ = self.apply(d["preview_id"])
            self.assertFalse(a["ok"])
            self.assertEqual(a["errors"][0]["code"], "PREVIEW_FAILED")
        finally:
            os.environ.pop("GIT_WORKLOG_HOME", None)


if __name__ == "__main__":
    unittest.main()
