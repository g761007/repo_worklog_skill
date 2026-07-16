"""Tests for collect_day_results.py — the file-based Day Subagent result exchange.

The point of this script is that a result which never arrives, or arrives
malformed, becomes an explicit failure instead of a silently empty day. These
tests exist mainly to hold that line.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from helpers import run_script, rmtree


def _valid_result(date: str, **overrides) -> dict:
    obj = {
        "date": date, "timezone": "Asia/Taipei",
        "status": "complete", "confidence": "verified",
        "escalation_recommended": False, "escalation_reasons": [],
        "has_changes": True,
        "commits": ["abc1234"],
        "work_items": [{
            "title": "t", "summary": "s", "behavior_change": "b",
            "implementation": "i", "impact": "im", "files": ["a.py"],
            "commits": ["abc1234"], "tests": [], "risks": [],
            "maintenance_notes": [], "follow_ups": [],
            "confidence": "verified", "evidence": ["abc1234 a.py:1"],
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
                               ["read", "--run-dir", self.tmp, "--dates", dates])
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

    def test_missing_run_dir_refused(self):
        d, rc, _ = run_script("collect_day_results.py",
                              ["read", "--run-dir",
                               os.path.join(self.tmp, "nope"),
                               "--dates", "2026-07-15"])
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "RUN_DIR_MISSING")


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
                                  "--dates", "2026-07-15"])
            self.assertEqual(d["complete"], ["2026-07-15"])
        finally:
            rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
