"""Tests for collect_day_results.py — the file-based Day Subagent result exchange.

The point of this script is that a result which never arrives, or arrives
malformed, becomes an explicit failure instead of a silently empty day. These
tests exist mainly to hold that line.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import _git, _write, run_script, rmtree


# A real repository, so the default evidence cites something that exists.
# Until issue #15 these tests used {"commit": "abc1234", "file": "src/cache.py",
# "symbol": "CacheLayer.get"} — a wholly fabricated citation that the validator
# accepted, which is precisely the bug. A fixture that cannot be checked cannot
# test a checker.
_REPO = None
_COMMIT = None      # cache.py exists here
_COMMIT2 = None     # paths.py added here, so it is absent at _COMMIT


def setUpModule():
    global _REPO, _COMMIT, _COMMIT2
    _REPO = tempfile.mkdtemp(prefix="rw_dr_repo_")
    _git(_REPO, "init", "-q", "-b", "main")
    _git(_REPO, "config", "user.email", "t@example.com")
    _git(_REPO, "config", "user.name", "Tester")
    body = ["import os", ""]
    body += [f"# filler line {i}" for i in range(38)]
    body += ["", "class CacheLayer:", "    def get(self, key):",
             "        return self._store.get(key)", ""]
    _write(_REPO, "src/cache.py", "\n".join(body) + "\n")
    _write(_REPO, "README.md", "# demo\n\n## usage\n\nRun it.\n")
    _git(_REPO, "add", "-A")
    _git(_REPO, "commit", "-q", "-m", "add cache")
    _COMMIT = subprocess.run(["git", "-C", _REPO, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()

    _write(_REPO, "src/paths.py", "def previews_dir():\n    return '/tmp/previews'\n")
    _git(_REPO, "add", "-A")
    _git(_REPO, "commit", "-q", "-m", "add paths")
    _COMMIT2 = subprocess.run(["git", "-C", _REPO, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()


def tearDownModule():
    if _REPO:
        rmtree(_REPO)


def _evidence(**overrides) -> dict:
    e = {"commit": _COMMIT, "file": "src/cache.py",
         "symbol": "CacheLayer.get", "lines": "42-45", "note": "adds lookup"}
    e.update(overrides)
    return e


def _valid_result(date: str, **overrides) -> dict:
    obj = {
        "date": date, "timezone": "Asia/Taipei", "language": "zh-TW",
        "status": "complete", "confidence": "verified",
        "escalation_recommended": False, "escalation_reasons": [],
        "has_changes": True,
        "commits": ["abc1234"],
        "work_items": [{
            "title": "t", "summary": "s", "behavior_change": "b",
            "implementation": "i", "impact": "im", "files": ["a.py"],
            "commits": ["abc1234"], "tests": [], "risks": [],
            "maintenance_notes": [], "follow_ups": [],
            "confidence": "verified", "evidence": [_evidence()],
        }],
        "fixes": [], "refactors": [], "tests": [], "database_changes": [],
        "configuration_changes": [], "deployment_changes": [],
        "uncommitted_changes": [], "handoff_notes": [], "uncertainties": [],
        "evidence": [],
    }
    obj.update(overrides)
    return obj


class TestInit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rw_run_")

    def tearDown(self):
        rmtree(self.tmp)

    def test_init_mints_a_path_per_date(self):
        d, _, err = run_script("collect_day_results.py",
                               ["init", "--dates", "2026-07-15,2026-07-16",
                                "--run-dir", self.tmp])
        self.assertTrue(d["ok"], err)
        self.assertEqual(sorted(d["paths"]), ["2026-07-15", "2026-07-16"])
        # A distinct file per date is what stops two Day Subagents racing.
        self.assertEqual(d["paths"]["2026-07-15"],
                         os.path.join(self.tmp, "2026-07-15.json"))
        self.assertNotEqual(d["paths"]["2026-07-15"], d["paths"]["2026-07-16"])
        self.assertTrue(os.path.isdir(d["run_dir"]))

    def test_init_creates_the_directory(self):
        target = os.path.join(self.tmp, "nested", "run")
        d, _, err = run_script("collect_day_results.py",
                               ["init", "--dates", "2026-07-15", "--run-dir", target])
        self.assertTrue(d["ok"], err)
        self.assertTrue(os.path.isdir(target))

    def test_duplicate_dates_collapse(self):
        d, _, _ = run_script("collect_day_results.py",
                             ["init", "--dates", "2026-07-15,2026-07-15",
                              "--run-dir", self.tmp])
        self.assertEqual(d["dates"], ["2026-07-15"])

    def test_invalid_date_refused(self):
        d, rc, _ = run_script("collect_day_results.py",
                              ["init", "--dates", "nope", "--run-dir", self.tmp])
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "INVALID_DATE")


class TestRead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rw_run_")

    def tearDown(self):
        rmtree(self.tmp)

    def _write(self, date, payload):
        path = os.path.join(self.tmp, f"{date}.json")
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(payload, str):
                fh.write(payload)
            else:
                json.dump(payload, fh, ensure_ascii=False)

    def _read(self, dates="2026-07-15"):
        d, _, err = run_script("collect_day_results.py",
                               ["read", "--run-dir", self.tmp, "--dates", dates,
                                "--repo", _REPO])
        self.assertIsNotNone(d, err)
        return d

    def test_valid_result_round_trips(self):
        self._write("2026-07-15", _valid_result("2026-07-15"))
        d = self._read()
        self.assertTrue(d["ok"])
        self.assertEqual(d["complete"], ["2026-07-15"])
        self.assertEqual(d["missing"], [])
        self.assertFalse(d["partial_run"])
        self.assertEqual(d["results"]["2026-07-15"]["work_items"][0]["title"], "t")

    def test_missing_file_is_a_failed_day_not_an_empty_one(self):
        # The exact failure this script exists to catch: a subagent did the work
        # but its result never landed. Reporting "no changes" here would write a
        # confidently empty worklog over a real day's work.
        d = self._read("2026-07-15,2026-07-16")
        self.assertEqual(d["missing"], ["2026-07-15", "2026-07-16"])
        self.assertEqual(d["failed_dates"], ["2026-07-15", "2026-07-16"])
        self.assertTrue(d["partial_run"])
        self.assertEqual(d["results"], {})

    def test_one_missing_day_does_not_lose_the_others(self):
        self._write("2026-07-15", _valid_result("2026-07-15"))
        d = self._read("2026-07-15,2026-07-16")
        self.assertEqual(d["complete"], ["2026-07-15"])
        self.assertEqual(d["missing"], ["2026-07-16"])
        self.assertTrue(d["partial_run"])

    def test_unparseable_file_is_invalid_not_a_crash(self):
        self._write("2026-07-15", "{not json at all")
        d = self._read()
        self.assertTrue(d["ok"])
        self.assertEqual(d["invalid"][0]["code"], "RESULT_NOT_JSON")
        self.assertTrue(d["partial_run"])

    def test_truncated_json_is_caught(self):
        # A half-written file is the realistic corruption mode for a channel
        # that truncates.
        full = json.dumps(_valid_result("2026-07-15"))
        self._write("2026-07-15", full[:len(full) // 2])
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "RESULT_NOT_JSON")
        self.assertTrue(d["partial_run"])

    def test_missing_schema_keys_rejected(self):
        obj = _valid_result("2026-07-15")
        del obj["uncertainties"]
        del obj["evidence"]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "RESULT_SCHEMA_INVALID")
        self.assertEqual(sorted(d["invalid"][0]["issues"][0]["missing_keys"]),
                         ["evidence", "uncertainties"])

    def test_wrong_date_rejected(self):
        # A subagent writing another day's object into this day's file would
        # silently misattribute a whole day of work.
        self._write("2026-07-15", _valid_result("2026-07-14"))
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "RESULT_DATE_MISMATCH")

    def test_bad_status_rejected(self):
        self._write("2026-07-15", _valid_result("2026-07-15", status="done"))
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "RESULT_BAD_STATUS")

    def test_bad_confidence_rejected(self):
        self._write("2026-07-15", _valid_result("2026-07-15", confidence="high"))
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "RESULT_BAD_CONFIDENCE")

    def test_malformed_work_item_rejected(self):
        obj = _valid_result("2026-07-15")
        del obj["work_items"][0]["evidence"]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "WORK_ITEM_SCHEMA_INVALID")

    def test_json_array_rejected(self):
        self._write("2026-07-15", [1, 2, 3])
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "RESULT_NOT_OBJECT")

    def test_partial_status_marks_the_run_partial(self):
        # The file arrived and is well-formed, but the subagent itself said it
        # could not finish — still not a clean run.
        self._write("2026-07-15", _valid_result("2026-07-15", status="partial"))
        d = self._read()
        self.assertEqual(d["degraded"], ["2026-07-15"])
        self.assertEqual(d["complete"], [])
        self.assertTrue(d["partial_run"])

    def test_no_change_day_is_complete_not_failed(self):
        # has_changes:false is a valid answer, not an absence.
        self._write("2026-07-15", _valid_result("2026-07-15", has_changes=False,
                                                work_items=[]))
        d = self._read()
        self.assertEqual(d["complete"], ["2026-07-15"])
        self.assertFalse(d["partial_run"])

    def test_escalation_suggestions_surfaced(self):
        self._write("2026-07-15", _valid_result("2026-07-15",
                                                escalation_recommended=True))
        d = self._read()
        self.assertEqual(d["escalation_suggested_dates"], ["2026-07-15"])

    def test_prose_evidence_rejected(self):
        # The exact shape a real subagent produced. It reads like evidence and
        # would satisfy any prose-based rule, but cites nothing openable — no
        # file, no symbol, no lines.
        obj = _valid_result("2026-07-15")
        obj["work_items"][0]["evidence"] = [
            "commit 4d08ee4: 完整改造，加 authors[] 與 author_name"
        ]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "EVIDENCE_INVALID")
        self.assertTrue(d["partial_run"])

    def test_evidence_without_file_rejected(self):
        # A bare hash does not tell a reader where to look.
        obj = _valid_result("2026-07-15")
        obj["work_items"][0]["evidence"] = [{"commit": "abc1234"}]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "EVIDENCE_INVALID")
        self.assertEqual(d["invalid"][0]["issues"][0]["missing_keys"], ["file"])

    def test_evidence_without_commit_rejected(self):
        obj = _valid_result("2026-07-15")
        obj["work_items"][0]["evidence"] = [{"file": "src/cache.py"}]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["invalid"][0]["issues"][0]["missing_keys"], ["commit"])

    def test_blank_evidence_fields_rejected(self):
        # Present-but-empty is the obvious way to satisfy a key check without
        # citing anything.
        obj = _valid_result("2026-07-15")
        obj["work_items"][0]["evidence"] = [{"commit": "  ", "file": ""}]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(sorted(d["invalid"][0]["issues"][0]["missing_keys"]),
                         ["commit", "file"])

    def test_top_level_prose_evidence_rejected(self):
        obj = _valid_result("2026-07-15")
        obj["evidence"] = ["整體來說改動很完整"]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "EVIDENCE_INVALID")

    def test_evidence_without_symbol_or_lines_accepted(self):
        # A doc or config change has no symbol and no meaningful line range;
        # requiring them would push subagents to invent them.
        obj = _valid_result("2026-07-15")
        obj["work_items"][0]["evidence"] = [
            {"commit": _COMMIT, "file": "README.md", "note": "usage section"}
        ]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["complete"], ["2026-07-15"])
        self.assertFalse(d["partial_run"])

    def test_empty_evidence_list_accepted(self):
        # A no-change day cites nothing; that is not a violation.
        obj = _valid_result("2026-07-15", has_changes=False, work_items=[],
                            evidence=[])
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["complete"], ["2026-07-15"])

    def test_missing_run_dir_refused(self):
        d, rc, _ = run_script("collect_day_results.py",
                              ["read", "--run-dir",
                               os.path.join(self.tmp, "nope"),
                               "--dates", "2026-07-15", "--repo", _REPO])
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "RUN_DIR_MISSING")


class TestResultLanguage(unittest.TestCase):
    """The language half of the result contract (§6.2.9)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rw_run_")

    def tearDown(self):
        rmtree(self.tmp)

    def _write(self, date, payload):
        with open(os.path.join(self.tmp, f"{date}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def _read(self, dates="2026-07-15", language=None):
        args = ["read", "--run-dir", self.tmp, "--dates", dates,
                "--repo", _REPO]
        if language:
            args += ["--language", language]
        d, _, err = run_script("collect_day_results.py", args)
        self.assertIsNotNone(d, err)
        return d

    def test_result_without_a_language_is_rejected(self):
        obj = _valid_result("2026-07-15")
        del obj["language"]
        self._write("2026-07-15", obj)
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "RESULT_SCHEMA_INVALID")
        self.assertIn("language", d["invalid"][0]["issues"][0]["missing_keys"])

    def test_result_language_must_be_a_real_tag(self):
        self._write("2026-07-15", _valid_result("2026-07-15", language="chinese"))
        d = self._read()
        self.assertEqual(d["invalid"][0]["code"], "RESULT_BAD_LANGUAGE")

    def test_result_language_must_match_the_manifest(self):
        # The subagent was told zh-TW and answered in English. Accepting it
        # would put an English day inside a Traditional Chinese worklog.
        self._write("2026-07-15", _valid_result("2026-07-15", language="en"))
        d = self._read(language="zh-TW")
        self.assertEqual(d["invalid"][0]["code"], "RESULT_LANGUAGE_MISMATCH")
        self.assertTrue(d["partial_run"])
        self.assertEqual(d["results"], {})

    def test_matching_language_passes(self):
        self._write("2026-07-15", _valid_result("2026-07-15", language="zh-TW"))
        d = self._read(language="zh-TW")
        self.assertEqual(d["complete"], ["2026-07-15"])
        self.assertEqual(d["language"], "zh-TW")
        self.assertFalse(d["language_inconsistent"])

    def test_casing_difference_is_not_a_language_difference(self):
        self._write("2026-07-15", _valid_result("2026-07-15", language="zh-tw"))
        d = self._read(language="zh-TW")
        self.assertEqual(d["complete"], ["2026-07-15"])

    def test_zh_tw_result_does_not_satisfy_a_zh_cn_manifest(self):
        # §21.4: these are different languages, and treating them as one would
        # ship Traditional text to a Simplified reader.
        self._write("2026-07-15", _valid_result("2026-07-15", language="zh-TW"))
        d = self._read(language="zh-CN")
        self.assertEqual(d["invalid"][0]["code"], "RESULT_LANGUAGE_MISMATCH")

    def test_mixed_languages_across_days_make_the_run_partial(self):
        # §21.4: no manifest language was passed, so nothing checked the days
        # individually -- but a worklog whose days switch language mid-run must
        # not reach apply.
        self._write("2026-07-15", _valid_result("2026-07-15", language="zh-TW"))
        self._write("2026-07-16", _valid_result("2026-07-16", language="en"))
        d = self._read("2026-07-15,2026-07-16")
        self.assertTrue(d["language_inconsistent"])
        self.assertTrue(d["partial_run"])
        self.assertEqual(d["languages_seen"], ["en", "zh-TW"])
        self.assertIsNone(d["language"])

    def test_consistent_days_report_the_runs_language(self):
        self._write("2026-07-15", _valid_result("2026-07-15", language="ja"))
        self._write("2026-07-16", _valid_result("2026-07-16", language="ja"))
        d = self._read("2026-07-15,2026-07-16")
        self.assertEqual(d["language"], "ja")
        self.assertFalse(d["language_inconsistent"])
        self.assertFalse(d["partial_run"])

    def test_collect_rejects_an_unusable_expected_language_outright(self):
        self._write("2026-07-15", _valid_result("2026-07-15"))
        d, _, err = run_script("collect_day_results.py",
                               ["read", "--run-dir", self.tmp, "--repo", _REPO,
                                "--dates", "2026-07-15", "--language", "zh"])
        self.assertIsNotNone(d, err)
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "LANGUAGE_AMBIGUOUS")

    def test_english_identifiers_in_zh_tw_prose_are_not_a_language_error(self):
        # The reason validation is structural: correct zh-TW engineering prose
        # is full of English paths and symbols by contract. A detector would
        # flag this; the contract must not.
        obj = _valid_result("2026-07-15", language="zh-TW")
        obj["work_items"][0]["summary"] = (
            "重構 src/auth/token_manager.py 的 refresh_token()，"
            "避免 TokenRefreshError 在 retry 時被吞掉。")
        self._write("2026-07-15", obj)
        d = self._read(language="zh-TW")
        self.assertEqual(d["complete"], ["2026-07-15"])


class TestEvidenceVerifiedAgainstTheTree(unittest.TestCase):
    """Issue #15: symbol and lines must resolve, not merely be present.

    The fabrications this exists to catch were all *plausible* — a real run
    cited `migrate_directory` for a function called `parse_legacy`, and
    `preview_dir` for `previews_dir`. Fixtures here mirror that: the bad names
    are one plausible step from the real ones, because a fixture full of
    obviously-fake names would pass a checker that the real thing defeats.
    """

    def setUp(self):
        self.repo, self.c1, self.c2 = _REPO, _COMMIT, _COMMIT2
        self.tmp = tempfile.mkdtemp(prefix="rw_ev_run_")

    def tearDown(self):
        rmtree(self.tmp)

    def _read(self, evidence):
        obj = _valid_result("2026-07-15", evidence=evidence)
        with open(os.path.join(self.tmp, "2026-07-15.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        d, _, err = run_script("collect_day_results.py",
                               ["read", "--run-dir", self.tmp,
                                "--dates", "2026-07-15", "--repo", self.repo])
        self.assertIsNotNone(d, err)
        return d

    def _codes(self, d):
        return [i["code"] for i in d["invalid"][0]["issues"]] if d["invalid"] else []

    def test_real_citation_passes(self):
        d = self._read([{"commit": self.c1, "file": "src/cache.py",
                         "symbol": "CacheLayer.get", "lines": "1-3"}])
        self.assertEqual(d["complete"], ["2026-07-15"])
        self.assertFalse(d["partial_run"])

    def test_a_plausible_symbol_that_does_not_exist_fails_the_day(self):
        # `store_get` reads like this file's code and is not in it.
        d = self._read([{"commit": self.c1, "file": "src/cache.py",
                         "symbol": "store_get"}])
        self.assertIn("EVIDENCE_SYMBOL_NOT_FOUND", self._codes(d))
        self.assertTrue(d["partial_run"])
        self.assertEqual(d["complete"], [])

    def test_near_miss_symbol_is_caught(self):
        # The exact shape of the real fabrication: previews_dir -> preview_dir.
        d = self._read([{"commit": self.c2, "file": "src/paths.py",
                         "symbol": "preview_dir"}])
        self.assertIn("EVIDENCE_SYMBOL_NOT_FOUND", self._codes(d))

    def test_qualified_name_resolves_without_appearing_verbatim(self):
        # "CacheLayer.get" is nowhere in the file as a literal string; the file
        # holds `class CacheLayer:` and `def get`. Matching the whole field would
        # reject every correct qualified citation.
        d = self._read([{"commit": self.c1, "file": "src/cache.py",
                         "symbol": "CacheLayer.get"}])
        self.assertEqual(d["complete"], ["2026-07-15"])

    def test_two_real_symbols_in_one_field_are_not_a_fabrication(self):
        d = self._read([{"commit": self.c1, "file": "src/cache.py",
                         "symbol": "CacheLayer, get"}])
        self.assertEqual(d["complete"], ["2026-07-15"])

    def test_file_added_later_does_not_exist_at_an_earlier_commit(self):
        # It is in the checkout right now, which is exactly the trap: evidence
        # must be checked against the day's tree, not today's.
        d = self._read([{"commit": self.c1, "file": "src/paths.py",
                         "symbol": "previews_dir"}])
        self.assertIn("EVIDENCE_FILE_NOT_IN_COMMIT", self._codes(d))

    def test_the_same_file_passes_at_the_commit_that_added_it(self):
        d = self._read([{"commit": self.c2, "file": "src/paths.py",
                         "symbol": "previews_dir"}])
        self.assertEqual(d["complete"], ["2026-07-15"])

    def test_line_range_past_end_of_file_fails(self):
        d = self._read([{"commit": self.c1, "file": "src/cache.py",
                         "symbol": "CacheLayer", "lines": "1-500"}])
        self.assertIn("EVIDENCE_LINES_OUT_OF_RANGE", self._codes(d))

    def test_inverted_line_range_fails(self):
        d = self._read([{"commit": self.c1, "file": "src/cache.py",
                         "symbol": "CacheLayer", "lines": "3-1"}])
        self.assertIn("EVIDENCE_LINES_OUT_OF_RANGE", self._codes(d))

    def test_invented_commit_hash_fails_in_a_full_clone(self):
        d = self._read([{"commit": "deadbee", "file": "src/cache.py",
                         "symbol": "CacheLayer"}])
        self.assertIn("EVIDENCE_COMMIT_UNKNOWN", self._codes(d))

    def test_work_item_evidence_is_checked_too(self):
        # Not just top-level: the per-item citations are what a reader follows.
        obj = _valid_result("2026-07-15", evidence=[])
        obj["work_items"][0]["evidence"] = [
            {"commit": self.c1, "file": "src/cache.py", "symbol": "store_get"}]
        with open(os.path.join(self.tmp, "2026-07-15.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        d, _, err = run_script("collect_day_results.py",
                               ["read", "--run-dir", self.tmp,
                                "--dates", "2026-07-15", "--repo", self.repo])
        self.assertIsNotNone(d, err)
        self.assertIn("EVIDENCE_SYMBOL_NOT_FOUND", self._codes(d))

    def test_evidence_without_a_symbol_is_still_fine(self):
        # symbol is expected where code is touched, not universally required.
        d = self._read([{"commit": self.c1, "file": "src/cache.py"}])
        self.assertEqual(d["complete"], ["2026-07-15"])

    def test_a_non_repo_fails_the_command_rather_than_the_day(self):
        # Refusing to run beats reporting every citation as unverifiable, which
        # would read as the subagent's fault.
        empty = tempfile.mkdtemp(prefix="rw_ev_norepo_")
        try:
            with open(os.path.join(self.tmp, "2026-07-15.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(_valid_result("2026-07-15"), fh)
            d, rc, _ = run_script("collect_day_results.py",
                                  ["read", "--run-dir", self.tmp,
                                   "--dates", "2026-07-15", "--repo", empty])
            self.assertFalse(d["ok"])
            self.assertEqual(d["errors"][0]["code"], "NOT_A_GIT_REPO")
            self.assertEqual(rc, 2)
        finally:
            rmtree(empty)


class TestShallowCloneCannotVerify(unittest.TestCase):
    """A truncated clone is the environment's doing, not the subagent's."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rw_ev_shallow_")
        self.origin = tempfile.mkdtemp(prefix="rw_ev_origin_")
        _git(self.origin, "init", "-q", "-b", "main")
        _git(self.origin, "config", "user.email", "t@example.com")
        _git(self.origin, "config", "user.name", "Tester")
        for i in range(3):
            _write(self.origin, "src/cache.py", f"def get_{i}():\n    return {i}\n")
            _git(self.origin, "add", "-A")
            _git(self.origin, "commit", "-q", "-m", f"c{i}")
        self.old = subprocess.run(
            ["git", "-C", self.origin, "rev-parse", "--short", "HEAD~2"],
            capture_output=True, text=True).stdout.strip()
        self.shallow = tempfile.mkdtemp(prefix="rw_ev_clone_")
        subprocess.run(["git", "clone", "-q", "--depth", "1",
                        f"file://{self.origin}", self.shallow],
                       capture_output=True, text=True)

    def tearDown(self):
        for p in (self.tmp, self.origin, self.shallow):
            rmtree(p)

    def test_unreachable_commit_is_unverifiable_not_a_fabrication(self):
        # Failing the day here would blame the subagent for the runner's clone
        # depth — and CI clones shallow by default.
        obj = _valid_result("2026-07-15", evidence=[
            {"commit": self.old, "file": "src/cache.py", "symbol": "get_0"}])
        with open(os.path.join(self.tmp, "2026-07-15.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        d, _, err = run_script("collect_day_results.py",
                               ["read", "--run-dir", self.tmp,
                                "--dates", "2026-07-15", "--repo", self.shallow])
        self.assertIsNotNone(d, err)
        codes = [i["code"] for i in d["invalid"][0]["issues"]]
        self.assertIn("EVIDENCE_UNVERIFIABLE", codes)
        self.assertNotIn("EVIDENCE_COMMIT_UNKNOWN", codes)


class TestInitReadRoundTrip(unittest.TestCase):
    def test_paths_from_init_are_what_read_looks_for(self):
        # If init and read ever disagreed about the filename, every day would
        # read as missing while the results sat on disk.
        tmp = tempfile.mkdtemp(prefix="rw_run_")
        try:
            init, _, _ = run_script("collect_day_results.py",
                                    ["init", "--dates", "2026-07-15",
                                     "--run-dir", tmp])
            with open(init["paths"]["2026-07-15"], "w", encoding="utf-8") as fh:
                json.dump(_valid_result("2026-07-15"), fh)
            d, _, _ = run_script("collect_day_results.py",
                                 ["read", "--run-dir", init["run_dir"],
                                  "--dates", "2026-07-15", "--repo", _REPO])
            self.assertEqual(d["complete"], ["2026-07-15"])
        finally:
            rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
