"""Tests for migrate_legacy_worklog.py.

Two legacy shapes: the pre-v0.2 single file (TestMigrate) and the v0.2-v0.5 flat
``PROJECT_WORKLOG/`` directory (TestMigrateFromDir).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from helpers import run_cli, run_script, rmtree, day_file, legacy_day_file, wm

LEGACY = """\
# Project Worklog

<!-- REPO_WORKLOG:ENTRIES:START -->

<!-- REPO_WORKLOG:2026-07-15:START -->
## 2026-07-15

<!-- REPO_WORKLOG:2026-07-15:GENERATED:START -->
### 當日摘要

新增會員搜尋快取。
<!-- REPO_WORKLOG:2026-07-15:GENERATED:END -->

<!-- REPO_WORKLOG:2026-07-15:MANUAL:START -->
issue #42：JWT 決策
<!-- REPO_WORKLOG:2026-07-15:MANUAL:END -->

<!-- REPO_WORKLOG:2026-07-15:END -->

<!-- REPO_WORKLOG:2026-07-14:START -->
## 2026-07-14

<!-- REPO_WORKLOG:2026-07-14:GENERATED:START -->
### 當日摘要

重構訂單狀態流程。
<!-- REPO_WORKLOG:2026-07-14:GENERATED:END -->

<!-- REPO_WORKLOG:2026-07-14:MANUAL:START -->

<!-- REPO_WORKLOG:2026-07-14:MANUAL:END -->

<!-- REPO_WORKLOG:2026-07-14:END -->

<!-- REPO_WORKLOG:ENTRIES:END -->
"""


class TestMigrate(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="rw_mig_")
        self.legacy = os.path.join(self.work, "docs", "PROJECT_WORKLOG.md")
        os.makedirs(os.path.dirname(self.legacy))
        Path(self.legacy).write_text(LEGACY, encoding="utf-8")
        self.dir = os.path.join(self.work, wm.WORKLOG_DIRNAME)

    def tearDown(self):
        rmtree(self.work)

    def _run(self, *extra):
        return run_script("migrate_legacy_worklog.py",
                          ["--legacy", self.legacy, "--dir", self.dir,
                           "--timezone", "Asia/Taipei", *extra])

    def test_dry_run_writes_nothing(self):
        d, _, _ = self._run()
        self.assertEqual(d["mode"], "dry-run")
        self.assertFalse(os.path.isdir(self.dir))
        self.assertEqual([p["date"] for p in d["planned_changes"]], ["2026-07-15", "2026-07-14"])

    def test_apply_splits_and_preserves_manual_and_legacy(self):
        d, _, _ = self._run("--apply")
        self.assertEqual(sorted(d["created_dates"], reverse=True), ["2026-07-15", "2026-07-14"])
        day15 = Path(day_file(self.dir, "2026-07-15")).read_text(encoding="utf-8")
        self.assertIn("新增會員搜尋快取", day15)
        self.assertIn("issue #42：JWT 決策", day15)          # MANUAL carried over
        self.assertTrue(os.path.exists(self.legacy))         # legacy never deleted
        # Result validates under the new engine.
        vd, _, _ = run_script("validate_daily_worklog.py", ["--dir", self.dir])
        self.assertTrue(vd["ok"])
        vi, _, _ = run_script("validate_worklog_index.py", ["--dir", self.dir])
        self.assertTrue(vi["ok"])

    def test_existing_day_file_is_skipped_not_clobbered(self):
        os.makedirs(self.dir)
        run_script("update_daily_worklog.py", ["--dir", self.dir, "--apply"],
                   stdin='{"meta":{"timezone":"Asia/Taipei"},'
                         '"entries":{"2026-07-15":{"generated_markdown":"## 當日摘要\\n\\nkeep mine"}}}')
        d, _, _ = self._run("--apply")
        actions = {p["date"]: p["action"] for p in d["planned_changes"]}
        self.assertEqual(actions["2026-07-15"], "skip-exists")
        self.assertIn("keep mine",
                      Path(day_file(self.dir, "2026-07-15")).read_text(encoding="utf-8"))

    def test_corrupt_legacy_refused(self):
        Path(self.legacy).write_text(
            "<!-- REPO_WORKLOG:ENTRIES:START -->\n"
            "<!-- REPO_WORKLOG:2026-07-15:GENERATED:START -->\nx\n", encoding="utf-8")
        d, _, _ = self._run()
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "LEGACY_CORRUPT")

    def test_marker_in_legacy_generated_refused(self):
        Path(self.legacy).write_text(
            "<!-- REPO_WORKLOG:ENTRIES:START -->\n"
            "<!-- REPO_WORKLOG:2026-07-15:START -->\n## 2026-07-15\n"
            "<!-- REPO_WORKLOG:2026-07-15:GENERATED:START -->\n"
            "<!-- REPO_WORKLOG:INDEX:GENERATED:START -->\n"     # bare marker inside generated
            "<!-- REPO_WORKLOG:2026-07-15:GENERATED:END -->\n"
            "<!-- REPO_WORKLOG:2026-07-15:MANUAL:START -->\n"
            "<!-- REPO_WORKLOG:2026-07-15:MANUAL:END -->\n"
            "<!-- REPO_WORKLOG:2026-07-15:END -->\n"
            "<!-- REPO_WORKLOG:ENTRIES:END -->\n", encoding="utf-8")
        d, _, _ = self._run()
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "LEGACY_CONTAINS_MARKER")

    def test_corrupt_existing_index_refused_preserving_manual(self):
        os.makedirs(self.dir)
        Path(os.path.join(self.dir, "index.md")).write_text("corrupt, no markers\n",
                                                            encoding="utf-8")
        d, _, _ = self._run("--apply")
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "INDEX_CORRUPT_MARKERS")


LEGACY_DAY = """\
# Project Worklog — 2026-07-15

> 時區：Asia/Taipei
> Branch：feature/login
> HEAD：deadbee

<!-- REPO_WORKLOG:2026-07-15:GENERATED:START -->
## 當日摘要

新增會員搜尋快取。

## 主要異動

- `src/cache.py`：加入 TTL。
<!-- REPO_WORKLOG:2026-07-15:GENERATED:END -->

<!-- REPO_WORKLOG:2026-07-15:MANUAL:START -->
issue #42：JWT 決策
<!-- REPO_WORKLOG:2026-07-15:MANUAL:END -->
"""

LEGACY_INDEX = """\
# Project Worklog

## 工作日誌

<!-- REPO_WORKLOG:INDEX:GENERATED:START -->
| 日期 | 摘要 |
|---|---|
| [2026-07-15](./2026-07-15.md) | 新增會員搜尋快取。 |
<!-- REPO_WORKLOG:INDEX:GENERATED:END -->

## 人工說明

<!-- REPO_WORKLOG:INDEX:MANUAL:START -->
交接請先讀 2026-07-15。
<!-- REPO_WORKLOG:INDEX:MANUAL:END -->
"""


class TestMigrateFromDir(unittest.TestCase):
    """The v0.2-v0.5 flat PROJECT_WORKLOG/ directory -> .git-worklog/."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="rw_migdir_")
        self.src = os.path.join(self.work, wm.LEGACY_WORKLOG_DIRNAME)
        os.makedirs(self.src)
        Path(legacy_day_file(self.src, "2026-07-15")).write_text(
            LEGACY_DAY, encoding="utf-8")
        Path(wm.index_path(self.src)).write_text(LEGACY_INDEX, encoding="utf-8")
        self.dst = os.path.join(self.work, wm.WORKLOG_DIRNAME)

    def tearDown(self):
        rmtree(self.work)

    def _run(self, *extra):
        return run_script("migrate_legacy_worklog.py",
                          ["--from-dir", self.src, "--dir", self.dst, *extra])

    def test_dry_run_writes_nothing(self):
        d, _, err = self._run()
        self.assertTrue(d["ok"], err)
        self.assertEqual(d["mode"], "dry-run")
        self.assertEqual(d["source_kind"], "dir")
        self.assertFalse(os.path.exists(self.dst))

    def test_day_content_is_copied_verbatim_apart_from_markers(self):
        # The acceptance criterion that matters: migration moves a worklog, it
        # does not rewrite one. Only the marker prefix may differ -- prose,
        # language, title and the original branch/HEAD metadata all survive.
        d, _, err = self._run("--apply")
        self.assertTrue(d["ok"], err)
        before = LEGACY_DAY.splitlines()
        after = Path(day_file(self.dst, "2026-07-15")).read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(before), len(after))
        for b, a in zip(before, after):
            if wm.LEGACY_PREFIX in b:
                self.assertEqual(a, b.replace(wm.LEGACY_PREFIX, wm.PREFIX))
            else:
                self.assertEqual(a, b)   # byte-identical, including 繁體中文 prose
        # Metadata a re-render would have destroyed:
        joined = "\n".join(after)
        self.assertIn("Branch：feature/login", joined)
        self.assertIn("HEAD：deadbee", joined)

    def test_source_is_never_deleted(self):
        self._run("--apply")
        self.assertTrue(os.path.isfile(legacy_day_file(self.src, "2026-07-15")))

    def test_creates_version_and_config(self):
        self._run("--apply", "--timezone", "Asia/Taipei")
        self.assertEqual(
            Path(wm.version_path(self.dst)).read_text(encoding="utf-8").strip(),
            str(wm.LAYOUT_VERSION))
        import json
        cfg = json.loads(Path(wm.config_path(self.dst)).read_text(encoding="utf-8"))
        self.assertEqual(cfg["schema_version"], wm.LAYOUT_VERSION)
        self.assertEqual(cfg["timezone"], "Asia/Taipei")

    def test_index_manual_carried_over_and_links_point_into_days(self):
        self._run("--apply")
        idx = Path(wm.index_path(self.dst)).read_text(encoding="utf-8")
        self.assertIn("交接請先讀 2026-07-15。", idx)          # MANUAL survives
        self.assertIn(wm.day_link("2026-07-15"), idx)          # ./days/<date>.md
        # The link resolves to a real file from the index's own directory.
        self.assertTrue(os.path.isfile(
            os.path.normpath(os.path.join(self.dst, wm.day_link("2026-07-15")))))

    def test_migrated_result_validates(self):
        self._run("--apply")
        vd, _, _ = run_script("validate_daily_worklog.py", ["--dir", self.dst])
        self.assertTrue(vd["ok"])
        vi, _, _ = run_script("validate_worklog_index.py", ["--dir", self.dst])
        self.assertTrue(vi["ok"])

    def test_rerun_skips_existing_and_never_clobbers(self):
        self._run("--apply")
        Path(day_file(self.dst, "2026-07-15")).write_text(
            LEGACY_DAY.replace(wm.LEGACY_PREFIX, wm.PREFIX).replace(
                "issue #42：JWT 決策", "hand-edited note"), encoding="utf-8")
        d, _, _ = self._run("--apply")
        self.assertEqual([p["action"] for p in d["planned_changes"]], ["skip-exists"])
        self.assertIn("hand-edited note",
                      Path(day_file(self.dst, "2026-07-15")).read_text(encoding="utf-8"))

    def test_corrupt_day_file_refused(self):
        Path(legacy_day_file(self.src, "2026-07-14")).write_text(
            "garbage, no markers\n", encoding="utf-8")
        d, _, _ = self._run()
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "DAY_FILE_CORRUPT")

    def test_source_equal_to_target_refused(self):
        d, _, _ = run_script("migrate_legacy_worklog.py",
                             ["--from-dir", self.src, "--dir", self.src])
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "SOURCE_IS_TARGET")

    def test_non_legacy_source_refused(self):
        d, _, _ = run_script("migrate_legacy_worklog.py",
                             ["--from-dir", self.work, "--dir", self.dst])
        self.assertFalse(d["ok"])
        self.assertEqual(d["errors"][0]["code"], "SOURCE_NOT_LEGACY")


class TestMigrateCli(unittest.TestCase):
    """`git-worklog migrate` — the same engine on the CLI surface (roadmap §2.4)."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="rw_migcli_")
        self.legacy = os.path.join(self.work, "docs", "PROJECT_WORKLOG.md")
        os.makedirs(os.path.dirname(self.legacy))
        Path(self.legacy).write_text(LEGACY, encoding="utf-8")
        self.dir = os.path.join(self.work, wm.WORKLOG_DIRNAME)

    def tearDown(self):
        rmtree(self.work)

    def _run(self, *extra):
        return run_cli("migrate", "--from-file", self.legacy, "--dir", self.dir,
                       "--timezone", "Asia/Taipei", *extra)

    def test_dry_run_is_what_you_get_for_not_asking(self):
        """No --apply, no writes. The safe direction is the default one."""
        d, rc, err = self._run()
        self.assertTrue(d["ok"], err)
        self.assertEqual(rc, 0)
        self.assertEqual(d["mode"], "dry-run")
        self.assertFalse(os.path.isdir(self.dir))

    def test_apply_writes_the_days_and_keeps_the_source(self):
        d, rc, err = self._run("--apply")
        self.assertTrue(d["ok"], err)
        self.assertEqual(rc, 0)
        self.assertEqual(d["mode"], "apply")
        self.assertTrue(os.path.isfile(day_file(self.dir, "2026-07-15")))
        # Never deletes the source: the user reviews, then removes it themselves.
        self.assertTrue(os.path.isfile(self.legacy))

    def test_missing_source_refused(self):
        d, rc, _ = run_cli("migrate", "--from-file",
                           os.path.join(self.work, "nope.md"), "--dir", self.dir)
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "LEGACY_NOT_FOUND")

    def test_both_sources_refused(self):
        d, rc, _ = run_cli("migrate", "--from-file", self.legacy,
                           "--from-dir", self.work, "--dir", self.dir)
        self.assertFalse(d["ok"])
        self.assertEqual(rc, 2)
        self.assertEqual(d["errors"][0]["code"], "AMBIGUOUS_SOURCE")


if __name__ == "__main__":
    unittest.main()
