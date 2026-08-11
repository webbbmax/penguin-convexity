#!/usr/bin/env python3
"""Deterministic C2.2 implementation gate; no network or database writes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALID_SOURCE_STATES = {"success", "no_data", "quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure"}


def load_js(path: Path, prefix: str) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(prefix):
        raise AssertionError(f"{path} does not use the expected snapshot prefix")
    return json.loads(text[len(prefix):].strip().removesuffix(";"))


class C22AcceptanceTests(unittest.TestCase):
    def test_frozen_requirement_lock_and_inherited_dependencies_are_unchanged(self):
        lock = json.loads((ROOT / "docs" / "C2.2_REQUIREMENTS_LOCK.json").read_text(encoding="utf-8"))
        canonical = []
        for item in lock["documents"]:
            digest = hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest()
            self.assertEqual(digest, item["sha256"], item["path"])
            canonical.append(f"{item['path']}:{digest}")
        self.assertEqual(hashlib.sha256("\n".join(canonical).encode()).hexdigest(), lock["requirementSetSha256"])
        for item in lock.get("inheritedFrozenDependencies", []):
            self.assertEqual(hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest(), item["sha256"], item["path"])

    def test_three_snapshots_are_joined_by_asset_id_and_front_is_complete(self):
        front = load_js(ROOT / "app" / "c2-2-front-snapshot.js", "window.PENGUIN_CONVEXITY_C22 = ")
        tracking = load_js(ROOT / "app" / "c2-2-tracking-snapshot.js", "window.PENGUIN_CONVEXITY_C22_TRACKING = ")
        admin = load_js(ROOT / "app" / "c2-2-admin-snapshot.js", "window.PENGUIN_CONVEXITY_C22_ADMIN = ")
        front_ids = {item["assetId"] for item in front["items"]}
        tracking_ids = {item["assetId"] for item in tracking["items"]}
        self.assertEqual(front_ids, tracking_ids)
        self.assertEqual(front["candidateBuildId"], tracking["candidateBuildId"])
        self.assertEqual(admin["screening"]["buildId"], front["candidateBuildId"])
        self.assertEqual(admin["calibration"]["schemaVersion"], "c2.2-calibration-v1")
        self.assertEqual(admin["calibration"]["parameterMutation"], "none")
        self.assertTrue(all(admin["calibration"]["summary"][str(h)]["sampleStatus"] == "sample_insufficient" for h in (7, 14, 30)))
        calibration_path = ROOT / "runtime" / "c2.2" / "calibration" / "latest.json"
        self.assertTrue(calibration_path.exists())
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        self.assertEqual(calibration["buildId"], admin["calibration"]["buildId"])
        self.assertEqual(front["coverageSummary"]["frontVisibleCount"], len(front["items"]))
        self.assertNotIn("data_limited", front["trackingStateCounts"])
        self.assertNotIn("data_limited", json.dumps(front, ensure_ascii=False))
        self.assertTrue(all(item["relationshipClass"] in {"A", "B", "C"} for item in front["items"]))
        self.assertTrue(all(0 <= int(item["ageDays"]) <= 90 for item in front["items"]))

    def test_tracking_contract_has_history_lineage_and_bayes_fields(self):
        tracking = load_js(ROOT / "app" / "c2-2-tracking-snapshot.js", "window.PENGUIN_CONVEXITY_C22_TRACKING = ")
        self.assertEqual(tracking["database"]["mainDatabase"], "data/convexity.db")
        self.assertEqual(tracking["database"]["mainDatabaseMode"], "read_only_supplementary_lineage")
        for item in tracking["items"]:
            self.assertIn("marketHistory", item)
            self.assertIn("series", item["marketHistory"])
            self.assertIn("liquidityAndExit", item)
            self.assertIn("addressAndSupply", item)
            self.assertIn("factorPosteriors", item)
            self.assertIn("mainDatabaseFacts", item)
            self.assertIn("inputLineage", item)

    def test_runtime_and_single_hidden_entry_are_independent(self):
        config = json.loads((ROOT / "runtime" / "c2.2" / "update-config.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config["jobs"]), {"screening", "convexity_tracking"})
        self.assertTrue(all(job["mode"] in {"manual", "automatic"} for job in config["jobs"].values()))
        vbs = (ROOT / "scripts" / "run-c2-1-update-hidden.vbs").read_text(encoding="utf-8")
        self.assertIn("run_c2_2_update.py", vbs)
        self.assertIn("--job due", vbs)
        self.assertIn('shell.Run command, 0, True', vbs)

    def test_databases_integrity_and_source_states(self):
        for relative in ("data/convexity.db", "data/c2.1-pipeline.db"):
            connection = sqlite3.connect(f"file:{(ROOT / relative).as_posix()}?mode=ro", uri=True)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok", relative)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [], relative)
            if relative.endswith("c2.1-pipeline.db"):
                states = {row[0] for row in connection.execute("SELECT DISTINCT status FROM source_health") if row[0]}
                self.assertTrue(states <= VALID_SOURCE_STATES, states)
            connection.close()

    def test_all_c22_javascript_passes_syntax_check(self):
        for name in ("c2-2-front.js", "c2-2-admin.js", "c2-2-detail-history.js"):
            result = subprocess.run(["node", "--check", str(ROOT / "app" / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
