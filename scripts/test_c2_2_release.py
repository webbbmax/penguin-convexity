#!/usr/bin/env python3
"""Frozen C2.2 release-state regression checks."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_js(path: Path, prefix: str) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(prefix):
        raise AssertionError(f"unexpected snapshot prefix: {path}")
    return json.loads(text[len(prefix):].strip().removesuffix(";"))


class C22ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required_local_artifacts = (
            ROOT / "docs/C2.2_ACCEPTANCE_MANIFEST.json",
            ROOT / "data/convexity.db",
            ROOT / "data/c2.1-pipeline.db",
            ROOT / "backups/c2.2-release-20260811T1544/manifest.json",
        )
        missing = [path for path in required_local_artifacts if not path.exists()]
        if missing:
            raise unittest.SkipTest("C2.2 release checks require ignored local operational artifacts.")

    def test_phase_acceptance_and_release_manifests_are_released(self):
        phase = json.loads((ROOT / "docs/C2.2_PHASE.json").read_text(encoding="utf-8"))
        acceptance = json.loads((ROOT / "docs/C2.2_ACCEPTANCE_MANIFEST.json").read_text(encoding="utf-8"))
        release = json.loads((ROOT / "docs/C2.2_RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(phase["status"], "independent_full_acceptance_complete_released")
        self.assertTrue(phase["published"])
        self.assertEqual(acceptance["summary"], {"total": 39, "passed": 39, "failed": 0, "unexplainedDifferences": 0, "unapprovedDeletions": 0})
        self.assertEqual(release["status"], phase["status"])
        self.assertEqual(release["acceptance"]["passed"], 39)

    def test_release_file_hashes_are_exact(self):
        release = json.loads((ROOT / "docs/C2.2_RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(sha256(ROOT / release["acceptance"]["manifest"]), release["acceptance"]["manifestSha256"])
        self.assertEqual(sha256(ROOT / release["acceptance"]["report"]), release["acceptance"]["reportSha256"])
        for item in release["releaseFiles"]:
            self.assertEqual(sha256(ROOT / item["path"]), item["sha256"], item["path"])

    def test_current_snapshots_are_complete_and_do_not_publish_data_limited(self):
        front = load_js(ROOT / "app/c2-2-front-snapshot.js", "window.PENGUIN_CONVEXITY_C22 = ")
        tracking = load_js(ROOT / "app/c2-2-tracking-snapshot.js", "window.PENGUIN_CONVEXITY_C22_TRACKING = ")
        admin = load_js(ROOT / "app/c2-2-admin-snapshot.js", "window.PENGUIN_CONVEXITY_C22_ADMIN = ")
        self.assertEqual({row["assetId"] for row in front["items"]}, {row["assetId"] for row in tracking["items"]})
        self.assertEqual(front["candidateBuildId"], tracking["candidateBuildId"])
        self.assertEqual(admin["screening"]["buildId"], front["candidateBuildId"])
        self.assertEqual(len(front["items"]), 11)
        self.assertNotIn("data_limited", json.dumps(front, ensure_ascii=False))

    def test_c22_snapshots_load_before_legacy_renderers(self):
        for name in ("candidate-pool.html", "change-explanations.html", "project-detail.html"):
            text = (ROOT / "app" / name).read_text(encoding="utf-8")
            snapshot_position = text.index("c2-2-front-snapshot.js")
            legacy_positions = [text.index(script) for script in ("front-c19.js", "c2-front.js", "c2-1-front.js") if script in text]
            self.assertTrue(legacy_positions, name)
            self.assertLess(snapshot_position, min(legacy_positions), name)

    def test_launcher_and_visible_version_require_c22(self):
        launcher = (ROOT / "scripts/launch-convexity.ps1").read_text(encoding="utf-8")
        self.assertIn('$health.experienceRelease -eq "C2.2"', launcher)
        self.assertIn("/api/c2.2/status", launcher)
        self.assertIn("c2-2-front.js", launcher)
        nav = (ROOT / "app/workbench-nav.js").read_text(encoding="utf-8")
        self.assertEqual(nav.count("当前版本 C2.2"), 1)
        self.assertNotIn("当前版本 C2.1", nav)
        status = (ROOT / "docs/STATUS.md").read_text(encoding="utf-8")
        self.assertIn("当前已发布体验版本：C2.2", status)
        migration = json.loads((ROOT / "docs/MIGRATION_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(migration["experienceRelease"], "C2.2")
        self.assertTrue(migration["currentExperienceRelease"]["published"])

    def test_databases_and_release_backups_are_valid(self):
        baseline = json.loads((ROOT / "docs/C2.2_IMPLEMENTATION_BASELINE.json").read_text(encoding="utf-8"))
        baseline_by_path = {item["path"]: item for item in baseline["databases"]}
        backup = json.loads((ROOT / "backups/c2.2-release-20260811T1544/manifest.json").read_text(encoding="utf-8"))
        for item in backup["databases"]:
            source = ROOT / item["source"]
            copied = ROOT / item["backup"]
            self.assertEqual(sha256(source), item["sha256"])
            self.assertEqual(sha256(copied), item["sha256"])
            self.assertEqual(item["sha256"], baseline_by_path[item["source"]]["sha256"])
            connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            connection.close()


if __name__ == "__main__":
    unittest.main()
