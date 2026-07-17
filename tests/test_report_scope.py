"""Tests for report mode's scope resolution: resolve_ref_range.py,
check_worklog_coverage.py, and resolve_date_range.py's --max-days cap."""

from __future__ import annotations

import os
import unittest

from helpers import (
    make_empty_repo, make_multi_author_repo, make_tagged_repo,
    make_worklog_commit_repo, run_cli, run_script, rmtree, day_file,
    legacy_day_file, wm,
)

TPE = ["--timezone", "Asia/Taipei"]


class TestResolveRefRange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_tagged_repo()

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.repo)

    def _run(self, args):
        return run_script("resolve_ref_range.py", ["--repo", self.repo, *TPE, *args])

    def test_lists_tags_newest_first(self):
        d, _, err = self._run(["--list-tags"])
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["tags"], ["v1.0.1", "v1.0.0"])

    def test_tag_resolves_against_its_predecessor(self):
        d, _, err = self._run(["--tag", "v1.0.1"])
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["prev_tag"], "v1.0.0")
        self.assertEqual(d["commit_range"], "v1.0.0..v1.0.1")
        self.assertFalse(d["first_release"])

    def test_range_excludes_start_tag_and_later_work(self):
        # The v1.0.0 commit must not be re-reported under v1.0.1, and the
        # untagged commit that landed after v1.0.1 must not leak in -- either
        # mistake would put the wrong changes in a release's CHANGELOG.
        d, _, _ = self._run(["--tag", "v1.0.1"])
        subjects = [c["subject"] for c in d["commits"]]
        self.assertEqual(subjects, ["feat: add search", "fix: search off-by-one"])

    def test_commits_are_chronological(self):
        # Oldest first, so a generated CHANGELOG reads in the order work happened.
        d, _, _ = self._run(["--tag", "v1.0.1"])
        self.assertEqual([c["date"] for c in d["commits"]],
                         ["2026-09-02", "2026-09-03"])

    def test_dates_derived_from_the_commit_set(self):
        d, _, _ = self._run(["--tag", "v1.0.1"])
        self.assertEqual(d["dates"], ["2026-09-02", "2026-09-03"])
        self.assertEqual(d["date_span"], {"from": "2026-09-02", "to": "2026-09-03"})

    def test_commits_carry_author(self):
        d, _, _ = self._run(["--tag", "v1.0.1"])
        by_subject = {c["subject"]: c["author_name"] for c in d["commits"]}
        self.assertEqual(by_subject, {"feat: add search": "Bob Lin",
                                      "fix: search off-by-one": "Alice Chen"})

    def test_first_tag_falls_back_to_root_and_says_so(self):
        # "Everything ever" is a very different answer from "since the last
        # release", so the caller must be able to tell them apart.
        d, _, err = self._run(["--tag", "v1.0.0"])
        self.assertTrue(d["ok"], err)
        self.assertIsNone(d["prev_tag"])
        self.assertTrue(d["first_release"])
        self.assertEqual(d["commit_range"], "v1.0.0")
        self.assertEqual([c["subject"] for c in d["commits"]], ["feat: add core"])

    def test_unknown_tag_lists_the_real_ones(self):
        d, rc, _ = self._run(["--tag", "v9.9.9"])
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "UNKNOWN_TAG")
        self.assertEqual(d["errors"][0]["available_tags"], ["v1.0.1", "v1.0.0"])

    def test_explicit_ref_pair(self):
        d, _, err = self._run(["--from-ref", "v1.0.0", "--to-ref", "v1.0.1"])
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["commit_count"], 2)

    def test_unknown_explicit_ref_is_refused(self):
        d, rc, _ = self._run(["--to-ref", "nope"])
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "UNKNOWN_REF")

    def test_tag_and_to_ref_conflict(self):
        d, rc, _ = self._run(["--tag", "v1.0.1", "--to-ref", "v1.0.0"])
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "ARG_CONFLICT")

    def test_no_ref_spec(self):
        d, rc, _ = self._run([])
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "NO_REF_SPEC")

    def test_timezone_decides_the_calendar_day(self):
        # "feat: add search" is 2026-09-02T05:00+08:00, i.e. 2026-09-01T21:00Z.
        # Under Asia/Taipei it belongs to 09-02, under UTC to 09-01. Getting
        # this wrong points the report at a day file that does not exist.
        d, _, _ = run_script("resolve_ref_range.py",
                             ["--repo", self.repo, "--tag", "v1.0.1",
                              "--timezone", "UTC"])
        self.assertEqual(d["dates"], ["2026-09-01", "2026-09-03"])


class TestResolveRefRangeNoTags(unittest.TestCase):
    def test_repo_without_tags(self):
        repo = make_multi_author_repo()
        try:
            d, rc, _ = run_script("resolve_ref_range.py",
                                  ["--repo", repo, "--tag", "v1.0.0", *TPE])
            self.assertFalse(d["ok"])
            self.assertEqual(rc, 2)
            self.assertEqual(d["errors"][0]["code"], "NO_TAGS")
        finally:
            rmtree(repo)

    def test_not_a_git_repo(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="rw_notgit_")
        try:
            d, rc, _ = run_script("resolve_ref_range.py",
                                  ["--repo", tmp, "--list-tags", *TPE])
            self.assertFalse(d["ok"])
            self.assertEqual(d["errors"][0]["code"], "NOT_A_GIT_REPO")
        finally:
            rmtree(tmp)


class TestCheckWorklogCoverage(unittest.TestCase):
    """A date with no worklog file is only a gap if that date had real commits."""

    @classmethod
    def setUpClass(cls):
        # 2026-08-01 has three commits, 2026-08-02 one, 2026-08-05 none.
        cls.repo = make_multi_author_repo()
        wdir = os.path.join(cls.repo, wm.WORKLOG_DIRNAME)
        os.makedirs(os.path.join(wdir, wm.DAYS_SUBDIR), exist_ok=True)
        with open(day_file(wdir, "2026-08-01"), "w", encoding="utf-8") as fh:
            fh.write("# Project Worklog — 2026-08-01\n")

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.repo)

    def _run(self, dates, extra=None):
        d, _, err = run_script("check_worklog_coverage.py",
                               ["--repo", self.repo, "--dates", dates, *TPE,
                                *(extra or [])])
        self.assertTrue(d["ok"], err)
        return d

    def test_date_with_commits_and_file_is_covered(self):
        d = self._run("2026-08-01")
        self.assertEqual(d["dates"][0]["status"], "covered")
        self.assertEqual(d["dates"][0]["commit_count"], 3)
        self.assertEqual(d["covered"], ["2026-08-01"])

    def test_date_with_commits_but_no_file_is_a_gap(self):
        d = self._run("2026-08-02")
        self.assertEqual(d["dates"][0]["status"], "gap")
        self.assertEqual(d["gaps"], ["2026-08-02"])
        self.assertFalse(d["fully_covered"])

    def test_date_without_commits_is_not_a_gap(self):
        # The rule this whole script exists for: worklog-format.md §6 says a day
        # with no commits gets no file on purpose. Calling it a gap would send
        # the user to backfill a day that can never produce one.
        d = self._run("2026-08-05")
        self.assertEqual(d["dates"][0]["status"], "no-commits")
        self.assertEqual(d["gaps"], [])
        self.assertTrue(d["fully_covered"])

    def test_mixed_range_classifies_each_date(self):
        d = self._run("2026-08-01,2026-08-02,2026-08-05")
        self.assertEqual({r["date"]: r["status"] for r in d["dates"]}, {
            "2026-08-01": "covered",
            "2026-08-02": "gap",
            "2026-08-05": "no-commits",
        })
        self.assertEqual(d["gap_commit_count"], 1)

    def test_invalid_date_is_refused(self):
        d, rc, _ = run_script("check_worklog_coverage.py",
                              ["--repo", self.repo, "--dates", "2026-13-99", *TPE])
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "INVALID_DATE")


class TestCoverageWithoutWorklogDir(unittest.TestCase):
    def test_missing_dir_reports_gaps_not_an_error(self):
        # A project that has never run the skill must get an actionable answer,
        # not a crash.
        repo = make_multi_author_repo()
        try:
            d, _, err = run_script("check_worklog_coverage.py",
                                   ["--repo", repo, "--dates", "2026-08-01", *TPE])
            self.assertTrue(d["ok"], err)
            self.assertFalse(d["dir_exists"])
            self.assertEqual(d["gaps"], ["2026-08-01"])
        finally:
            rmtree(repo)

    def test_empty_repo_has_no_gaps(self):
        repo = make_empty_repo()
        try:
            d, _, err = run_script("check_worklog_coverage.py",
                                   ["--repo", repo, "--dates", "2026-08-01", *TPE])
            self.assertTrue(d["ok"], err)
            self.assertEqual(d["dates"][0]["status"], "no-commits")
            self.assertTrue(d["fully_covered"])
        finally:
            rmtree(repo)


class TestCoverageExcludesSelfReferentialCommits(unittest.TestCase):
    def test_worklog_only_day_counts_as_no_commits(self):
        # 2026-07-21's only commit edited PROJECT_WORKLOG/. The collector drops
        # it, so the day must read as no-commits -- not as a gap the user is
        # asked to backfill with a worklog describing the worklog.
        repo = make_worklog_commit_repo()
        try:
            d, _, err = run_script("check_worklog_coverage.py",
                                   ["--repo", repo, "--dates",
                                    "2026-07-20,2026-07-21,2026-07-22", *TPE])
            self.assertTrue(d["ok"], err)
            statuses = {r["date"]: r["status"] for r in d["dates"]}
            self.assertEqual(statuses["2026-07-21"], "no-commits")
            self.assertEqual(statuses["2026-07-22"], "gap")
            self.assertNotIn("2026-07-21", d["gaps"])
        finally:
            rmtree(repo)


class TestMaxDaysCap(unittest.TestCase):
    """The 30-day cap bounds subagent cost; report mode reads files instead."""

    def test_default_cap_is_still_thirty(self):
        d, rc, _ = run_script("resolve_date_range.py",
                              ["--from", "2026-01-01", "--to", "2026-03-31", *TPE])
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "TOO_MANY_DAYS")
        self.assertEqual(d["errors"][0]["max_days"], 30)

    def test_raised_cap_admits_a_longer_span(self):
        d, _, err = run_script("resolve_date_range.py",
                               ["--from", "2026-01-01", "--to", "2026-03-31",
                                "--max-days", "90", *TPE])
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["days_count"], 90)
        self.assertEqual(d["max_days"], 90)

    def test_raised_cap_still_enforces_a_limit(self):
        # --max-days raises the ceiling, it does not remove it.
        d, rc, _ = run_script("resolve_date_range.py",
                              ["--from", "2026-01-01", "--to", "2026-04-01",
                               "--max-days", "90", *TPE])
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "TOO_MANY_DAYS")
        self.assertEqual(d["errors"][0]["requested_days"], 91)

    def test_days_mode_respects_the_cap(self):
        d, _, err = run_script("resolve_date_range.py",
                               ["--days", "60", "--max-days", "90",
                                "--today", "2026-07-16", *TPE])
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["days_count"], 60)

    def test_days_mode_default_still_rejects_sixty(self):
        d, rc, _ = run_script("resolve_date_range.py",
                              ["--days", "60", "--today", "2026-07-16", *TPE])
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "DAYS_OUT_OF_RANGE")

    def test_nonsense_cap_is_refused(self):
        d, rc, _ = run_script("resolve_date_range.py",
                              ["--days", "1", "--max-days", "0", *TPE])
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "BAD_MAX_DAYS")


class TestCoverageCli(unittest.TestCase):
    """`git-worklog coverage` — the same engine, reached the way the skill reaches it.

    The engine's covered/gap/no-commits rule is held by TestCheckWorklogCoverage
    above. What is only true of the CLI is its exit code: report mode asks "can I
    answer from what is on disk?", and a gap means no. Answering 0 would let a
    caller that checks the status and not the payload report on days nothing
    analysed.
    """

    @classmethod
    def setUpClass(cls):
        # 2026-08-01 has three commits and a day file; 2026-08-02 has one commit
        # and none; 2026-08-05 has neither.
        cls.repo = make_multi_author_repo()
        wdir = os.path.join(cls.repo, wm.WORKLOG_DIRNAME)
        os.makedirs(os.path.join(wdir, wm.DAYS_SUBDIR), exist_ok=True)
        with open(day_file(wdir, "2026-08-01"), "w", encoding="utf-8") as fh:
            fh.write("# Project Worklog — 2026-08-01\n")

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.repo)

    def _run(self, dates):
        return run_cli("coverage", "--repo", self.repo, "--dates", dates, *TPE)

    def test_full_coverage_exits_zero(self):
        d, rc, err = self._run("2026-08-01")
        self.assertTrue(d["ok"], err)
        self.assertTrue(d["fully_covered"])
        self.assertEqual(rc, 0)

    def test_a_gap_exits_one(self):
        d, rc, err = self._run("2026-08-02")
        self.assertTrue(d["ok"], err)      # the command ran; the answer is "no"
        self.assertEqual(d["gaps"], ["2026-08-02"])
        self.assertEqual(rc, 1)

    def test_a_day_without_commits_does_not_count_as_a_gap(self):
        d, rc, err = self._run("2026-08-05")
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["no_commit_dates"], ["2026-08-05"])
        self.assertEqual(d["gaps"], [])
        self.assertEqual(rc, 0)

    def test_impossible_date_refused(self):
        d, rc, _ = self._run("2026-13-99")
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "INVALID_DATE")


class TestRefsCli(unittest.TestCase):
    """`git-worklog refs` — the CLI over the same ref resolver."""

    @classmethod
    def setUpClass(cls):
        cls.repo = make_tagged_repo()

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.repo)

    def test_tag_resolves_to_its_commit_set(self):
        d, rc, err = run_cli("refs", "--repo", self.repo, "--tag", "v1.0.1", *TPE)
        self.assertTrue(d["ok"], err)
        self.assertEqual(rc, 0)
        self.assertEqual(d["commit_range"], "v1.0.0..v1.0.1")
        self.assertEqual(d["commit_count"], 2)
        self.assertFalse(d["first_release"])

    def test_list_tags(self):
        d, _, err = run_cli("refs", "--repo", self.repo, "--list-tags")
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["tags"], ["v1.0.1", "v1.0.0"])

    def test_unknown_tag_lists_what_there_is(self):
        d, rc, _ = run_cli("refs", "--repo", self.repo, "--tag", "v9.9.9", *TPE)
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "UNKNOWN_TAG")
        self.assertEqual(d["errors"][0]["available_tags"], ["v1.0.1", "v1.0.0"])

    def test_no_ref_spec_refused(self):
        d, rc, _ = run_cli("refs", "--repo", self.repo, *TPE)
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "NO_REF_SPEC")


if __name__ == "__main__":
    unittest.main()
