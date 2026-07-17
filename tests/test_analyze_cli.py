"""Tests for `git-worklog analyze prepare` / `analyze collect` (roadmap §7).

These two commands bracket the part of the pipeline the CLI deliberately does
*not* do: `prepare` decides what must be analysed and in which language,
`collect` decides whether to believe what came back. The analysis in between is
the hosting agent's LLM's job, so what is testable here — and what these tests
hold — is that a day cannot go missing, drift language, or arrive uncited
without the CLI saying so.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from helpers import _git, _write, rmtree, run_cli

_REPO = None
_COMMIT = None
_DATE = "2026-07-15"
_DAY_ARGS = ["--from", _DATE, "--to", _DATE, "--timezone", "Asia/Taipei"]
_TZ = "Asia/Taipei"


def _today() -> str:
    """Today in the fixture's timezone — the only day uncommitted work can join."""
    return datetime.now(ZoneInfo(_TZ)).date().isoformat()


def setUpModule():
    """A real one-day repo, so evidence cites something that actually exists.

    `src/cache.py` and `src/util.py` are both source; `README.md` and
    `tests/test_cache.py` are not. That mix is the point: the coverage rule only
    holds the day to its source files, and a fixture of one file could not tell
    a working rule from one that requires nothing.
    """
    global _REPO, _COMMIT
    _REPO = tempfile.mkdtemp(prefix="rw_an_repo_")
    _git(_REPO, "init", "-q", "-b", "main")
    _git(_REPO, "config", "user.email", "t@example.com")
    _git(_REPO, "config", "user.name", "Tester")
    _git(_REPO, "config", "commit.gpgsign", "false")
    _write(_REPO, "src/cache.py",
           "class CacheLayer:\n    def get(self, key):\n        return key\n")
    _write(_REPO, "src/util.py", "def helper():\n    return 1\n")
    _write(_REPO, "README.md", "# demo\n")
    _write(_REPO, "tests/test_cache.py", "def test_get():\n    assert True\n")
    _git(_REPO, "add", "-A")
    _git(_REPO, "commit", "-q", "-m", "add cache",
         env={"GIT_AUTHOR_DATE": f"{_DATE}T10:00:00+08:00",
              "GIT_COMMITTER_DATE": f"{_DATE}T10:00:00+08:00"})
    _COMMIT = subprocess.run(["git", "-C", _REPO, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()


def tearDownModule():
    if _REPO:
        rmtree(_REPO)


def _result(date: str, **overrides) -> dict:
    """A result that covers the day: both source files accounted for.

    `src/util.py` is only listed in `files[]`, never cited in `evidence[]` — on
    purpose. That is the normal shape of good analysis (a real run cited 31% of
    its files while listing 88%), so the default fixture has to look like it, or
    the tests would only ever prove the rule against exhaustively-cited days.
    """
    obj = {
        "date": date, "timezone": "Asia/Taipei", "language": "zh-TW",
        "status": "complete", "confidence": "verified",
        "escalation_recommended": False, "escalation_reasons": [],
        "has_changes": True, "commits": [_COMMIT],
        "work_items": [{
            "title": "t", "summary": "s", "behavior_change": "b",
            "implementation": "i", "impact": "im",
            "files": ["src/cache.py", "src/util.py"],
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
    obj.update(overrides)
    return obj


class _Run(unittest.TestCase):
    """Base: each test gets its own GIT_WORKLOG_HOME so runs never collide."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="rw_an_home_")
        self.env = {"GIT_WORKLOG_HOME": self.home}

    def tearDown(self):
        rmtree(self.home)

    def prepare(self, *extra: str):
        # Language is stated rather than left to `auto`, which resolves to the
        # `en` fallback here: this repo has no config.json and an agent-hosted
        # run must not read the host locale (§6.2.5). A later --language in
        # `extra` overrides this one.
        d, rc, err = run_cli("analyze", "prepare", "--repo", _REPO, *_DAY_ARGS,
                             "--language", "zh-TW", "--language-source",
                             "user-request", *extra, env=self.env)
        return d, rc, err

    def collect(self, run_id: str):
        return run_cli("analyze", "collect", "--run-id", run_id,
                       "--repo", _REPO, env=self.env)

    def write_result(self, run_id: str, date: str, obj: dict) -> str:
        path = os.path.join(self.home, "analysis", run_id, "results", f"{date}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        return path


class TestPrepare(_Run):
    def test_writes_one_task_per_day_with_its_own_result_path(self):
        d, rc, err = run_cli("analyze", "prepare", "--repo", _REPO,
                             "--from", "2026-07-15", "--to", "2026-07-17",
                             "--timezone", "Asia/Taipei", env=self.env)
        self.assertTrue(d["ok"], err)
        self.assertEqual([t["date"] for t in d["tasks"]],
                         ["2026-07-15", "2026-07-16", "2026-07-17"])
        # Distinct output paths are what stop two Day Subagents racing on one file.
        paths = {t["result_path"] for t in d["tasks"]}
        self.assertEqual(len(paths), 3)
        for t in d["tasks"]:
            self.assertTrue(os.path.isfile(t["manifest_path"]))
            self.assertFalse(os.path.exists(t["result_path"]),
                             "prepare must not invent a result")

    def test_manifest_carries_the_roadmap_fields(self):
        d, _, err = self.prepare()
        self.assertTrue(d["ok"], err)
        with open(d["tasks"][0]["manifest_path"], encoding="utf-8") as fh:
            m = json.load(fh)
        self.assertEqual(m["schema_version"], 1)
        self.assertEqual(m["run_id"], d["run_id"])
        self.assertEqual(m["date"], _DATE)
        self.assertEqual(m["result_path"], d["tasks"][0]["result_path"])
        self.assertEqual(m["repository"]["root"], os.path.realpath(_REPO))
        self.assertIn("Read the actual patch.", m["analysis_rules"])

    def test_analysis_rules_travel_on_the_manifest_not_only_in_prose(self):
        # A rule the subagent is never shown is a rule that is not enforced: the
        # manifest is what reaches the model, the skill's prose may not.
        d, _, _ = self.prepare()
        with open(d["tasks"][0]["manifest_path"], encoding="utf-8") as fh:
            rules = " ".join(json.load(fh)["analysis_rules"]).lower()
        self.assertIn("do not rely only on commit messages", rules)
        self.assertIn("resolved language", rules)

    def test_the_day_carries_its_real_commits(self):
        d, _, _ = self.prepare()
        with open(d["tasks"][0]["manifest_path"], encoding="utf-8") as fh:
            m = json.load(fh)
        self.assertTrue(m["has_changes"])
        self.assertEqual([c["short_hash"] for c in m["commits"]], [_COMMIT])
        self.assertIn("src/cache.py", [f["path"] for f in m["changed_files"]])

    def test_language_is_resolved_once_for_the_whole_run(self):
        # §6.2.8: a run that decided per day could ask for two languages and
        # then reject itself for having got them.
        d, _, err = run_cli("analyze", "prepare", "--repo", _REPO,
                            "--from", "2026-07-15", "--to", "2026-07-17",
                            "--timezone", "Asia/Taipei",
                            "--language", "ja", "--language-source", "user-request",
                            env=self.env)
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["language"]["resolved"], "ja")
        for t in d["tasks"]:
            with open(t["manifest_path"], encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["language"]["resolved"], "ja")

    def test_reversed_range_refused(self):
        d, rc, _ = run_cli("analyze", "prepare", "--repo", _REPO,
                           "--from", "2026-07-17", "--to", "2026-07-15",
                           "--timezone", "Asia/Taipei", env=self.env)
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "BAD_RANGE")

    def test_unknown_timezone_refused(self):
        d, rc, _ = run_cli("analyze", "prepare", "--repo", _REPO,
                           "--from", _DATE, "--to", _DATE,
                           "--timezone", "Mars/Olympus", env=self.env)
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "INVALID_TIMEZONE")

    def test_bad_language_refused_before_any_task_is_written(self):
        d, rc, _ = self.prepare("--language", "not a tag")
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.isdir(os.path.join(self.home, "analysis")),
                         "a run that cannot be language-stamped must not exist")

    def test_not_a_repo_refused(self):
        tmp = tempfile.mkdtemp(prefix="rw_an_norepo_")
        try:
            d, rc, _ = run_cli("analyze", "prepare", "--repo", tmp,
                               *_DAY_ARGS, env=self.env)
            self.assertEqual(d["errors"][0]["code"], "NOT_A_GIT_REPO")
            self.assertEqual(rc, 2)
        finally:
            rmtree(tmp)


class TestCollect(_Run):
    def test_a_delivered_run_is_complete(self):
        d, _, _ = self.prepare()
        self.write_result(d["run_id"], _DATE, _result(_DATE))
        c, rc, err = self.collect(d["run_id"])
        self.assertTrue(c["ok"], err)
        self.assertEqual(c["complete"], [_DATE])
        self.assertFalse(c["partial_run"])
        self.assertEqual(rc, 0)

    def test_an_undelivered_day_is_missing_not_empty(self):
        # The failure this whole file-exchange design exists to catch: a day
        # whose subagent never wrote must never read as "nothing happened".
        d, _, _ = self.prepare()
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["missing"], [_DATE])
        self.assertTrue(c["partial_run"])
        self.assertEqual(rc, 1)

    def test_dates_come_from_the_tasks_not_from_the_caller(self):
        # Nothing on the collect command line names a date. If it did, a day
        # could be dropped from the run just by omitting it from the second
        # command -- which is the exact failure `missing` exists to report.
        d, _, _ = run_cli("analyze", "prepare", "--repo", _REPO,
                          "--from", "2026-07-15", "--to", "2026-07-16",
                          "--timezone", "Asia/Taipei", "--language", "zh-TW",
                          "--language-source", "user-request", env=self.env)
        self.write_result(d["run_id"], "2026-07-15", _result("2026-07-15"))
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["dates"], ["2026-07-15", "2026-07-16"])
        # The delivered day is genuinely complete, so `missing` is the only
        # reason this run is partial -- not a language mismatch standing in.
        self.assertEqual(c["complete"], ["2026-07-15"])
        self.assertEqual(c["missing"], ["2026-07-16"])
        self.assertEqual(rc, 1)

    def test_a_result_nobody_asked_for_is_reported(self):
        # A stray result means the directory holds analysis of a day this run
        # never prepared or language-checked. Merging it would smuggle it in.
        d, _, _ = self.prepare()
        self.write_result(d["run_id"], _DATE, _result(_DATE))
        self.write_result(d["run_id"], "2026-07-14", _result("2026-07-14"))
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["unknown"], ["2026-07-14"])
        self.assertTrue(c["partial_run"])
        self.assertEqual(rc, 1)

    def test_language_mismatch_against_the_manifest_is_refused(self):
        # Acceptance (issue #5): a run whose language drifts cannot be collected.
        d, _, _ = self.prepare("--language", "en", "--language-source", "user-request")
        self.write_result(d["run_id"], _DATE, _result(_DATE, language="zh-TW"))
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["expected_language"], "en")
        self.assertEqual(c["invalid"][0]["code"], "RESULT_LANGUAGE_MISMATCH")
        self.assertTrue(c["partial_run"])
        self.assertEqual(rc, 1)

    def test_fabricated_evidence_fails_the_day_through_the_cli(self):
        # The #15 fix has to be wired into `collect`, not merely present in the
        # library: a plausible-but-absent symbol is what reading cannot catch.
        d, _, _ = self.prepare()
        obj = _result(_DATE)
        obj["work_items"][0]["evidence"][0]["symbol"] = "MigrateDirectory"
        self.write_result(d["run_id"], _DATE, obj)
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["invalid"][0]["issues"][0]["code"],
                         "EVIDENCE_SYMBOL_NOT_FOUND")
        self.assertTrue(c["partial_run"])
        self.assertEqual(rc, 1)

    def test_a_degraded_day_blocks_the_run(self):
        d, _, _ = self.prepare()
        self.write_result(d["run_id"], _DATE, _result(_DATE, status="partial"))
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["degraded"], [_DATE])
        self.assertTrue(c["partial_run"])
        self.assertEqual(rc, 1)

    def test_malformed_result_is_invalid_not_skipped(self):
        d, _, _ = self.prepare()
        path = os.path.join(self.home, "analysis", d["run_id"], "results",
                            f"{_DATE}.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["invalid"][0]["code"], "RESULT_NOT_JSON")
        self.assertEqual(rc, 1)

    def test_unprepared_run_dir_refused(self):
        tmp = tempfile.mkdtemp(prefix="rw_an_bare_")
        try:
            d, rc, _ = run_cli("analyze", "collect", "--run-dir", tmp,
                               "--repo", _REPO, env=self.env)
            self.assertEqual(d["errors"][0]["code"], "RUN_NOT_PREPARED")
            self.assertEqual(rc, 2)
        finally:
            rmtree(tmp)

    def test_collect_without_a_run_refused(self):
        d, rc, _ = run_cli("analyze", "collect", "--repo", _REPO, env=self.env)
        self.assertEqual(d["errors"][0]["code"], "NO_RUN")
        self.assertEqual(rc, 2)

    def test_unknown_run_id_refused(self):
        d, rc, _ = self.collect("rw-19700101-000000")
        self.assertEqual(d["errors"][0]["code"], "RUN_NOT_PREPARED")
        self.assertEqual(rc, 2)


class TestRequiredPairs(_Run):
    """The manifest's answer to "what must this day's analysis account for?"."""

    def _pairs(self) -> "list[dict]":
        d, _, err = self.prepare()
        self.assertTrue(d["ok"], err)
        with open(d["tasks"][0]["manifest_path"], encoding="utf-8") as fh:
            return json.load(fh)["required_commit_file_pairs"]

    def test_every_changed_pair_is_listed_with_a_verdict(self):
        # All pairs, not just the required ones: a reader must be able to tell
        # "excluded" from "overlooked".
        pairs = self._pairs()
        self.assertEqual({p["file"] for p in pairs},
                         {"src/cache.py", "src/util.py", "README.md",
                          "tests/test_cache.py"})
        for p in pairs:
            self.assertIn(p["required"], (True, False))
            self.assertEqual(p["commit"], _COMMIT)

    def test_only_source_files_are_required(self):
        required = {p["file"] for p in self._pairs() if p["required"]}
        self.assertEqual(required, {"src/cache.py", "src/util.py"})

    def test_docs_and_tests_are_listed_but_not_required(self):
        # Real work, but a day may fairly cover them in a sentence. Requiring a
        # citation each would fail honest days rather than catch dishonest ones.
        excused = {p["file"]: p["category"] for p in self._pairs()
                   if not p["required"]}
        self.assertEqual(excused,
                         {"README.md": "documentation",
                          "tests/test_cache.py": "tests"})


class TestDeletionsAndBinaries(_Run):
    """Pairs that cannot be cited must not be required."""

    def setUp(self):
        super().setUp()
        self.repo = tempfile.mkdtemp(prefix="rw_an_del_")
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "Tester")
        _git(self.repo, "config", "commit.gpgsign", "false")
        _write(self.repo, "src/gone.py", "def gone():\n    return 1\n")
        _write(self.repo, "src/kept.py", "def kept():\n    return 2\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "seed",
             env={"GIT_AUTHOR_DATE": "2026-07-14T10:00:00+08:00",
                  "GIT_COMMITTER_DATE": "2026-07-14T10:00:00+08:00"})
        os.remove(os.path.join(self.repo, "src", "gone.py"))
        with open(os.path.join(self.repo, "src", "blob.py"), "wb") as fh:
            fh.write(b"\x00\x01\x02BINARY\xff\xfe")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "delete gone, add binary",
             env={"GIT_AUTHOR_DATE": f"{_DATE}T10:00:00+08:00",
                  "GIT_COMMITTER_DATE": f"{_DATE}T10:00:00+08:00"})

    def tearDown(self):
        rmtree(self.repo)
        super().tearDown()

    def test_a_deleted_file_is_not_required(self):
        # It is gone from that commit's tree, so any citation of it there would
        # be rejected by the evidence check. Requiring one requires the
        # impossible, and the day could never pass.
        d, _, err = run_cli("analyze", "prepare", "--repo", self.repo, *_DAY_ARGS,
                            "--language", "zh-TW", "--language-source",
                            "user-request", env=self.env)
        self.assertTrue(d["ok"], err)
        with open(d["tasks"][0]["manifest_path"], encoding="utf-8") as fh:
            pairs = json.load(fh)["required_commit_file_pairs"]
        by_file = {p["file"]: p for p in pairs}
        self.assertIn("src/gone.py", by_file)
        self.assertFalse(by_file["src/gone.py"]["required"])

    def test_a_binary_file_is_not_required(self):
        # No source to read, no symbol to cite.
        d, _, _ = run_cli("analyze", "prepare", "--repo", self.repo, *_DAY_ARGS,
                          "--language", "zh-TW", "--language-source",
                          "user-request", env=self.env)
        with open(d["tasks"][0]["manifest_path"], encoding="utf-8") as fh:
            pairs = json.load(fh)["required_commit_file_pairs"]
        by_file = {p["file"]: p for p in pairs}
        self.assertFalse(by_file["src/blob.py"]["required"])


class TestCoverage(_Run):
    """#15 made citations accurate. Accuracy says nothing about completeness."""

    def test_a_covered_day_passes(self):
        d, _, _ = self.prepare()
        self.write_result(d["run_id"], _DATE, _result(_DATE))
        c, rc, err = self.collect(d["run_id"])
        self.assertEqual(c["complete"], [_DATE], err)
        self.assertEqual(rc, 0)

    def test_source_the_analysis_never_mentions_fails_the_day(self):
        # The whole point: three real files cited perfectly, twenty unmentioned,
        # and it still reads as confident verified work.
        d, _, _ = self.prepare()
        obj = _result(_DATE)
        obj["work_items"][0]["files"] = ["src/cache.py"]  # src/util.py dropped
        self.write_result(d["run_id"], _DATE, obj)
        c, rc, _ = self.collect(d["run_id"])
        issue = c["invalid"][0]["issues"][0]
        self.assertEqual(issue["code"], "COVERAGE_INCOMPLETE")
        self.assertEqual(issue["missing_files"], ["src/util.py"])
        self.assertEqual((issue["covered_count"], issue["required_count"]), (1, 2))
        self.assertTrue(c["partial_run"])
        self.assertEqual(rc, 1)

    def test_files_alone_satisfies_coverage_without_a_citation(self):
        # Coverage asks the weaker question. Requiring evidence[] for every file
        # was measured against a real good run and would have rejected it.
        d, _, _ = self.prepare()
        obj = _result(_DATE)
        obj["work_items"][0]["evidence"] = [
            {"commit": _COMMIT, "file": "src/cache.py", "note": "x"}]
        self.assertNotIn("src/util.py",
                         [e["file"] for e in obj["work_items"][0]["evidence"]])
        self.write_result(d["run_id"], _DATE, obj)
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["complete"], [_DATE])
        self.assertEqual(rc, 0)

    def test_evidence_alone_satisfies_coverage_without_a_files_entry(self):
        d, _, _ = self.prepare()
        obj = _result(_DATE)
        obj["work_items"][0]["files"] = ["src/cache.py"]
        obj["work_items"][0]["evidence"].append(
            {"commit": _COMMIT, "file": "src/util.py", "symbol": "helper",
             "note": "y"})
        self.write_result(d["run_id"], _DATE, obj)
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["complete"], [_DATE])
        self.assertEqual(rc, 0)

    def test_files_recorded_in_an_unspecified_section_still_count(self):
        # The contract pins files[] on work_items[] only, but leaves fixes[] /
        # refactors[] / configuration_changes[] open — and a real run put files[]
        # on those too. Reading only the specified place would call an honestly
        # recorded change an omission.
        d, _, _ = self.prepare()
        obj = _result(_DATE)
        obj["work_items"][0]["files"] = ["src/cache.py"]
        obj["refactors"] = [{"title": "tidy helper", "files": ["src/util.py"]}]
        self.write_result(d["run_id"], _DATE, obj)
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["complete"], [_DATE])
        self.assertEqual(rc, 0)

    def test_unmentioned_docs_do_not_fail_the_day(self):
        # README.md changed that day and appears nowhere in the result.
        d, _, _ = self.prepare()
        obj = _result(_DATE)
        self.assertNotIn("README.md", json.dumps(obj))
        self.write_result(d["run_id"], _DATE, obj)
        c, rc, _ = self.collect(d["run_id"])
        self.assertEqual(c["complete"], [_DATE])
        self.assertEqual(rc, 0)

    def test_the_script_path_does_not_pretend_to_check_coverage(self):
        # `collect_day_results.py read` is handed a run directory and nothing
        # else, so it genuinely cannot know what was required. It must check what
        # it can rather than invent a verdict.
        from helpers import run_script
        run_dir = os.path.join(self.home, "flat")
        os.makedirs(run_dir)
        obj = _result(_DATE)
        obj["work_items"][0]["files"] = []  # would fail coverage, if it applied
        with open(os.path.join(run_dir, f"{_DATE}.json"), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        d, rc, err = run_script("collect_day_results.py",
                                ["read", "--run-dir", run_dir, "--dates", _DATE,
                                 "--repo", _REPO], env=self.env)
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["complete"], [_DATE])
        self.assertEqual(rc, 0)


class TestIncludeUncommitted(_Run):
    """Uncommitted work belongs to today and to no other day."""

    def test_todays_task_carries_the_working_tree(self):
        today = _today()
        _write(_REPO, "src/scratch.py", "def scratch():\n    return 0\n")
        try:
            d, _, err = run_cli("analyze", "prepare", "--repo", _REPO,
                                "--from", today, "--to", today,
                                "--timezone", _TZ, "--language", "zh-TW",
                                "--language-source", "user-request",
                                "--include-uncommitted", env=self.env)
            self.assertTrue(d["ok"], err)
            self.assertTrue(d["tasks"][0]["include_uncommitted"])
            self.assertIn("worktree_fingerprint", d)
            with open(d["tasks"][0]["manifest_path"], encoding="utf-8") as fh:
                m = json.load(fh)
            self.assertIn("src/scratch.py",
                          [u["path"] for u in m["uncommitted_changes"]])
            self.assertTrue(m["has_changes"],
                            "a dirty tree is a change even with no commits")
        finally:
            os.remove(os.path.join(_REPO, "src", "scratch.py"))

    def test_a_past_day_never_carries_the_working_tree(self):
        # A file's mtime says when it was last written, not when the work
        # happened, so there is nothing to attribute a dirty tree to on a past
        # date. Guessing would put today's half-done work into a shipped day.
        _write(_REPO, "src/scratch.py", "def scratch():\n    return 0\n")
        try:
            d, _, err = run_cli("analyze", "prepare", "--repo", _REPO, *_DAY_ARGS,
                                "--language", "zh-TW", "--language-source",
                                "user-request", "--include-uncommitted",
                                env=self.env)
            self.assertTrue(d["ok"], err)
            self.assertFalse(d["tasks"][0]["include_uncommitted"])
            with open(d["tasks"][0]["manifest_path"], encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["uncommitted_changes"], [])
        finally:
            os.remove(os.path.join(_REPO, "src", "scratch.py"))

    def test_uncommitted_outside_the_range_is_reported_not_dropped(self):
        # Silence here would read as "the worktree was clean", which is a
        # different and wrong statement.
        _write(_REPO, "src/scratch.py", "x = 1\n")
        try:
            d, _, _ = run_cli("analyze", "prepare", "--repo", _REPO, *_DAY_ARGS,
                              "--language", "zh-TW", "--language-source",
                              "user-request", "--include-uncommitted",
                              env=self.env)
            codes = [w["code"] for w in d.get("warnings", [])]
            self.assertIn("UNCOMMITTED_NOT_IN_RANGE", codes)
        finally:
            os.remove(os.path.join(_REPO, "src", "scratch.py"))

    def test_without_the_flag_the_working_tree_is_ignored(self):
        _write(_REPO, "src/scratch.py", "x = 1\n")
        try:
            today = _today()
            d, _, _ = run_cli("analyze", "prepare", "--repo", _REPO,
                              "--from", today, "--to", today, "--timezone", _TZ,
                              "--language", "zh-TW", "--language-source",
                              "user-request", env=self.env)
            self.assertFalse(d["tasks"][0]["include_uncommitted"])
            self.assertNotIn("worktree_fingerprint", d)
        finally:
            os.remove(os.path.join(_REPO, "src", "scratch.py"))


if __name__ == "__main__":
    unittest.main()
