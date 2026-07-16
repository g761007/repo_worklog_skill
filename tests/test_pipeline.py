"""Integration test: the deterministic script pipeline the skill runs end to end.

resolve_date_range -> collect_git_history -> build_analysis_manifest
-> update_daily_worklog (dry-run) -> rebuild_worklog_index (dry-run w/ overrides)
-> preview_state create/verify -> update_daily_worklog --apply
-> rebuild_worklog_index --apply -> validate_daily_worklog + validate_worklog_index.
Day summaries are synthesised (real runs get them from Day Subagents) to exercise
the JSON hand-offs between scripts.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import make_history_repo, run_script, rmtree, wm


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def worklog_fingerprint(worklog_dir: str, target_dates: list[str]) -> dict:
    """Mirror what the orchestrator records for a multi-file preview."""
    index_path = os.path.join(worklog_dir, "index.md")
    index_sha = _sha(Path(index_path).read_text(encoding="utf-8")) \
        if os.path.exists(index_path) else "missing"
    day_files = {}
    for date in target_dates:
        p = os.path.join(worklog_dir, f"{date}.md")
        day_files[date] = _sha(Path(p).read_text(encoding="utf-8")) if os.path.exists(p) else "missing"
    listing = sorted(n for n in (os.listdir(worklog_dir) if os.path.isdir(worklog_dir) else [])
                     if n.endswith(".md") and n != "index.md")
    return {"index_sha256": index_sha, "day_files": day_files,
            "dir_fingerprint": _sha("\n".join(listing))}


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_history_repo()

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.repo)

    def test_full_pipeline(self):
        resolved, _, _ = run_script(
            "resolve_date_range.py",
            ["--from", "2026-07-01", "--to", "2026-07-12", "--timezone", "Asia/Taipei"])
        self.assertEqual(resolved["days_count"], 12)

        info, _, _ = run_script("collect_git_history.py", ["--repo", self.repo, "--info-only"])
        self.assertTrue(info["repository"]["has_commits"])

        entries = {}
        commit_days = {}
        for day in resolved["dates"]:
            hist, _, _ = run_script("collect_git_history.py",
                                    ["--repo", self.repo,
                                     "--since", day["start"], "--until", day["end"]])
            man, rc, err = run_script(
                "build_analysis_manifest.py",
                ["--date", day["date"], "--timezone", "Asia/Taipei"],
                stdin=json.dumps(hist))
            self.assertTrue(man["ok"], err)
            if man["has_changes"]:
                commit_days[day["date"]] = man["commit_count"]
                groups = ", ".join(g["group"] for g in man["file_groups"])
                entries[day["date"]] = {
                    "generated_markdown": f"## 當日摘要\n\n{man['commit_count']} commit(s): {groups}."}

        self.assertEqual(set(commit_days), {"2026-07-01", "2026-07-10", "2026-07-12"})
        self.assertEqual(commit_days["2026-07-10"], 3)

        work = tempfile.mkdtemp(prefix="rw_pl_")
        home = tempfile.mkdtemp(prefix="rw_plhome_")
        target_dates = sorted(entries, reverse=True)
        try:
            wdir = os.path.join(work, wm.WORKLOG_DIRNAME)
            meta = {"timezone": "Asia/Taipei",
                    "branch": info["repository"]["branch"],
                    "head": info["repository"]["short_head"]}
            payload = json.dumps({"meta": meta,
                                  "entries": entries})

            dry, _, _ = run_script("update_daily_worklog.py", ["--dir", wdir], stdin=payload)
            self.assertEqual(dry["mode"], "dry-run")
            self.assertEqual({p["action"] for p in dry["planned_changes"]}, {"create"})
            self.assertFalse(os.path.isdir(wdir))

            # Index dry-run reflects the pending day files via overrides.
            idx_dry, _, _ = run_script("rebuild_worklog_index.py", ["--dir", wdir],
                                       stdin=json.dumps({"overrides": dry["summaries"]}))
            self.assertEqual(idx_dry["dates"], ["2026-07-12", "2026-07-10", "2026-07-01"])

            fp = {"repository": {"root": info["repository"]["root"],
                                 "branch": info["repository"]["branch"],
                                 "head": info["repository"]["head"],
                                 "worktree_fingerprint": None},
                  "worklog": worklog_fingerprint(wdir, target_dates),
                  "params": {"timezone": "Asia/Taipei", "include_uncommitted": False}}
            pv, _, _ = run_script("preview_state.py",
                                  ["create", "--now", "2026-07-15T12:00:00+08:00"],
                                  stdin=json.dumps(fp), env={"HOME": home})
            pid = pv["preview_id"]

            verify_state = {"repository": fp["repository"],
                            "worklog": worklog_fingerprint(wdir, target_dates),
                            "params": fp["params"]}
            vr, rc, _ = run_script(
                "preview_state.py",
                ["verify", "--id", pid, "--mark-applied", "--now", "2026-07-15T12:01:00+08:00"],
                stdin=json.dumps(verify_state), env={"HOME": home})
            self.assertTrue(vr["consistent"])

            ap, _, err = run_script("update_daily_worklog.py", ["--dir", wdir, "--apply"],
                                    stdin=payload)
            self.assertTrue(ap["ok"], err)
            self.assertEqual(sorted(ap["written_dates"], reverse=True),
                             ["2026-07-12", "2026-07-10", "2026-07-01"])

            idx_ap, _, err = run_script("rebuild_worklog_index.py", ["--dir", wdir, "--apply"],
                                        stdin="")
            self.assertTrue(idx_ap["ok"], err)
            self.assertEqual(idx_ap["dates"], ["2026-07-12", "2026-07-10", "2026-07-01"])

            vd, _, _ = run_script("validate_daily_worklog.py", ["--dir", wdir])
            self.assertTrue(vd["ok"])
            self.assertEqual(vd["file_count"], 3)
            vi, _, _ = run_script("validate_worklog_index.py", ["--dir", wdir])
            self.assertTrue(vi["ok"])
            self.assertEqual(vi["errors"], [])
        finally:
            rmtree(work)
            rmtree(home)


if __name__ == "__main__":
    unittest.main()
