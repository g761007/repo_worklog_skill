"""Integration test: the whole CLI pipeline, over a real multi-day history.

    analyze prepare -> (the agent's LLM, stubbed) -> analyze collect
    -> preview -> apply -> validate

The stub between prepare and collect is the point of the architecture, not a
shortcut: reading patches and writing prose is the hosting agent's job (§6.1),
and everything on either side of it is deterministic and therefore testable.
What this test holds is that the hand-offs line up — a manifest's required files
are the ones a result must account for, a collected run is what a preview will
build from, and a preview is what apply writes.

test_preview.py covers the refusals in depth on a one-day fixture. This one is
here for breadth: several days, a revert, a rename, a binary file, and days with
no commits at all, all the way to a validated worklog on disk.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from helpers import make_history_repo, run_cli, run_script, rmtree, wm

# make_history_repo's timeline. 07-10 carries three commits including a revert;
# 07-12 carries a rename plus a binary file; the days between carry nothing.
_COMMIT_DAYS = {"2026-07-01", "2026-07-10", "2026-07-12"}


def _empty_result(manifest: dict) -> dict:
    """What a Day Subagent returns for a day with no commits.

    A quiet day still gets dispatched and still answers, because "nothing
    happened" and "the subagent never came back" are different facts and only
    the day itself can tell them apart. Skipping the result here would make
    `collect` report the day as `missing` — correctly.
    """
    return {
        "date": manifest["date"], "timezone": manifest["timezone"],
        "language": "zh-TW", "status": "complete", "confidence": "verified",
        "escalation_recommended": False, "escalation_reasons": [],
        "has_changes": False, "commits": [], "work_items": [],
        "fixes": [], "refactors": [], "tests": [], "database_changes": [],
        "configuration_changes": [], "deployment_changes": [],
        "uncommitted_changes": [], "handoff_notes": [], "uncertainties": [],
        "evidence": [],
    }


def _result_for(manifest: dict) -> dict:
    """A result a Day Subagent could plausibly have returned for this manifest.

    Built *from* the manifest rather than hard-coded, so the test cannot drift
    into asserting against files the run never asked about. Every required file
    is accounted for in ``files[]`` and every cited commit/file pair is one Git
    really has — which is what `collect` checks.
    """
    date = manifest["date"]
    all_pairs = manifest.get("required_commit_file_pairs") or []
    # `required` is False for pairs the day does not have to account for — a
    # file the day's own revert deleted again, for instance. Cover the ones that
    # are required and cite the rest anyway, which is what a thorough day looks
    # like.
    required = [p for p in all_pairs if p.get("required")]
    commits = [c["short_hash"] for c in manifest["commits"]]
    return {
        "date": date, "timezone": manifest["timezone"], "language": "zh-TW",
        "status": "complete", "confidence": "verified",
        "escalation_recommended": False, "escalation_reasons": [],
        "has_changes": True, "commits": commits,
        "work_items": [{
            "title": f"{date} 的變更", "summary": "s", "behavior_change": "b",
            "implementation": "i", "impact": "im",
            "files": sorted({p["file"] for p in all_pairs}),
            "commits": commits, "tests": [], "risks": [],
            "maintenance_notes": [], "follow_ups": [], "confidence": "verified",
            "evidence": [{"commit": p["commit"], "file": p["file"],
                          "note": "changed here"} for p in required],
        }],
        "fixes": [], "refactors": [], "tests": [], "database_changes": [],
        "configuration_changes": [], "deployment_changes": [],
        "uncommitted_changes": [], "handoff_notes": [], "uncertainties": [],
        "evidence": [],
    }


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.repo = make_history_repo()
        self.home = tempfile.mkdtemp(prefix="rw_pl_home_")
        self.work = tempfile.mkdtemp(prefix="rw_pl_")
        self.wdir = os.path.join(self.work, wm.WORKLOG_DIRNAME)
        self.env = {"GIT_WORKLOG_HOME": self.home}

    def tearDown(self):
        for path in (self.repo, self.home, self.work):
            rmtree(path)

    def _preview(self, run_id: str, entries: dict):
        env = os.environ.copy()
        env.update(self.env)
        env["PYTHONPATH"] = __import__("helpers").SKILL_ROOT
        p = subprocess.run(
            ["python3", "-m", "git_worklog", "preview", "--run-id", run_id,
             "--repo", self.repo, "--dir", self.wdir],
            input=json.dumps({"entries": entries}), capture_output=True,
            text=True, env=env)
        return json.loads(p.stdout), p.returncode, p.stderr

    def test_prepare_to_applied_worklog(self):
        prep, rc, err = run_cli("analyze", "prepare", "--repo", self.repo,
                                "--from", "2026-07-01", "--to", "2026-07-12",
                                "--timezone", "Asia/Taipei", "--language", "zh-TW",
                                "--language-source", "user-request", env=self.env)
        self.assertTrue(prep["ok"], err)
        self.assertEqual(len(prep["tasks"]), 12)
        with_changes = {t["date"] for t in prep["tasks"] if t["has_changes"]}
        self.assertEqual(with_changes, _COMMIT_DAYS)
        self.assertEqual(
            next(t["commit_count"] for t in prep["tasks"] if t["date"] == "2026-07-10"),
            3, "the revert day's three commits must all reach the manifest")

        # The stub stands in for the agent's LLM. Every dispatched day answers,
        # including the quiet ones; only the days with commits get a file.
        entries = {}
        for task in prep["tasks"]:
            with open(task["manifest_path"], encoding="utf-8") as fh:
                manifest = json.load(fh)
            result = (_result_for(manifest) if task["has_changes"]
                      else _empty_result(manifest))
            with open(task["result_path"], "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False)
            if not task["has_changes"]:
                continue
            entries[task["date"]] = {"generated_markdown": (
                "## 當日摘要\n"
                "<!-- GIT_WORKLOG:SUMMARY:START -->\n"
                f"{task['date']}：{task['commit_count']} 個 commit。\n"
                "<!-- GIT_WORKLOG:SUMMARY:END -->\n")}

        coll, rc, err = run_cli("analyze", "collect", "--run-id", prep["run_id"],
                                "--repo", self.repo, env=self.env)
        self.assertTrue(coll["ok"], err)
        self.assertFalse(coll["partial_run"],
                         f"missing={coll['missing']} invalid={coll['invalid']}")
        # Every dispatched day came back, quiet ones included: `complete` is
        # about the answer arriving, not about the day having had commits.
        self.assertEqual(len(coll["complete"]), 12)
        self.assertEqual(coll["missing"], [])
        self.assertEqual(rc, 0)

        pv, rc, err = self._preview(prep["run_id"], entries)
        self.assertTrue(pv["ok"], err)
        self.assertEqual({f["action"] for f in pv["files"]}, {"create"})
        self.assertFalse(os.path.isdir(self.wdir), "the preview created files")
        # The nine commitless days were analysed and deliberately not written.
        self.assertEqual(len(pv["not_written"]), 9)

        ap, rc, err = run_cli("apply", "--preview-id", pv["preview_id"], env=self.env)
        self.assertTrue(ap["ok"], err)
        self.assertEqual(sorted(ap["written_dates"], reverse=True),
                         ["2026-07-12", "2026-07-10", "2026-07-01"])

        vd, _, _ = run_script("validate_daily_worklog.py", ["--dir", self.wdir])
        self.assertTrue(vd["ok"], vd)
        self.assertEqual(vd["file_count"], 3)
        vi, _, _ = run_script("validate_worklog_index.py", ["--dir", self.wdir])
        self.assertTrue(vi["ok"], vi)
        self.assertEqual(vi["errors"], [])

        # The index is navigation over exactly the days that were written.
        with open(os.path.join(self.wdir, "index.md"), encoding="utf-8") as fh:
            index = fh.read()
        for date in sorted(_COMMIT_DAYS):
            self.assertIn(f"[{date}](./days/{date}.md)", index)


if __name__ == "__main__":
    unittest.main()
