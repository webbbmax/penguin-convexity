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
        self.assertEqual(front["trackingCandidateBuildId"], tracking["candidateBuildId"])
        self.assertEqual(admin["screening"]["buildId"], front["candidateBuildId"])
        self.assertEqual(admin["trackingQualification"]["buildId"], tracking["candidateBuildId"])
        handoff = tracking["inputSummary"]
        self.assertGreaterEqual(handoff["candidateCount"], len(tracking["items"]))
        self.assertLessEqual(handoff["ruleEvaluatedCandidateCount"], handoff["candidateCount"])
        self.assertEqual(handoff["publicCandidateCount"], len(tracking["items"]))
        self.assertEqual(
            handoff["completedFirstTrackingCount"] + handoff["pendingFirstTrackingCount"],
            handoff["publicCandidateCount"],
        )
        self.assertLessEqual(handoff["backendIdentityPendingCount"], handoff["candidateCount"])
        self.assertEqual(handoff["detailedPublicItemCount"], len(tracking["items"]))
        self.assertEqual(admin["calibration"]["schemaVersion"], "c2.2-calibration-v1")
        self.assertEqual(admin["calibration"]["parameterMutation"], "none")
        self.assertTrue(all(admin["calibration"]["summary"][str(h)]["sampleStatus"] == "sample_insufficient" for h in (7, 14, 30)))
        calibration_path = ROOT / "runtime" / "c2.2" / "calibration" / "latest.json"
        self.assertTrue(calibration_path.exists())
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        self.assertEqual(calibration["buildId"], admin["calibration"]["buildId"])
        self.assertEqual(front["coverageSummary"]["frontVisibleCount"], len(front["items"]))
        coverage = front["coverageSummary"]
        self.assertEqual(coverage["t0HandoffCount"], coverage["firstGateQueuedCount"])
        self.assertEqual(
            coverage["firstGateQueuedCount"],
            coverage["firstGateCompletedCount"]
            + coverage["firstGatePendingCount"]
            + coverage.get("firstGateOutsideWindowCount", 0)
            + coverage["firstGateFailedCount"],
        )
        self.assertEqual(coverage["hardGatePassedCount"], len(front["items"]))
        self.assertEqual(tracking["inputSummary"]["publicCandidateCount"], coverage["hardGatePassedCount"])
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
            self.assertIn("firstTracking", item)

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

    def test_update_pages_keep_visible_manual_controls_and_live_progress(self):
        screening = (ROOT / "app" / "new-token-update.html").read_text(encoding="utf-8")
        tracking = (ROOT / "app" / "update-center.html").read_text(encoding="utf-8")
        admin = (ROOT / "app" / "c2-2-admin.js").read_text(encoding="utf-8")
        current_admin = (ROOT / "app" / "c2-4-admin.js").read_text(encoding="utf-8")
        self.assertNotIn("c2-1-admin.js", screening)
        self.assertIn("立即手动更新新币筛选", admin)
        self.assertIn("立即手动更新凸性跟踪", admin)
        self.assertIn("仅手动", admin)
        self.assertIn("暂停自动更新", admin)
        self.assertIn("实时任务进度", admin)
        self.assertIn("首轮跟踪完成", admin)
        self.assertIn("待确认项目身份", admin)
        self.assertNotIn("已有首轮结果", admin)
        self.assertIn('/api/c2.2/status', admin)
        self.assertIn('/api/c2.1/status', admin)
        self.assertIn('/api/update-status', admin)
        self.assertIn("历史主干维护（不影响现役前台）", admin)
        self.assertIn("历史任务保留失败记录", admin)
        self.assertIn("不会计入现役故障，也不会阻断两项现役作业", admin)
        self.assertIn("查看历史任务明细与手动维护", admin)
        self.assertNotIn("工作台待处理 1 项", admin)
        self.assertNotIn("运行或待处理时自动展开", admin)
        self.assertNotIn(".slice(0,12)", admin)
        self.assertIn("历史候选基础扫描", admin)
        self.assertIn("日常高优先级队列", admin)
        self.assertIn("只统计日常新增和到期复查，不包含459万历史积压", admin)
        self.assertIn("不重新扫描 Gate 0 区块链历史", admin)
        self.assertIn('/api/c2.2/candidate-production/run', admin)
        self.assertIn('/api/c2.2/candidate-production/pause', admin)
        self.assertIn('/api/c2.2/candidate-production/retry', admin)
        for element_id in (
            "c22TopT0Verified",
            "c22TopFirstGateQueued",
            "c22TopFirstGateCompleted",
            "c22TopFirstGatePending",
            "c22TopHardGatePassed",
            "c22TopFrontVisible",
            "c22ProductionHistoricalHandoff",
            "c22ProductionDailyHandoff",
        ):
            self.assertIn(element_id, admin)
        for status_field in (
            "t0VerifiedCount",
            "firstGateQueuedCount",
            "firstGateProcessedCount",
            "firstGatePendingCount",
            "historicalT0HandoffCount",
            "dailyT0HandoffCount",
            "convexityTrackingInputCount",
        ):
            self.assertIn(status_field, admin)
        self.assertIn("firstGateOutsideWindowCount", current_admin)
        self.assertIn("c2-2-admin.js?v=c22-5", screening)
        self.assertIn("c2-2-admin.js?v=c22-5", tracking)

    def test_admin_source_rows_have_reconciled_job_ownership(self):
        admin = load_js(ROOT / "app" / "c2-2-admin-snapshot.js", "window.PENGUIN_CONVEXITY_C22_ADMIN = ")
        rows = admin["sourceHealth"]
        self.assertTrue(rows)
        self.assertTrue(all(row.get("owner") in {"screening", "convexity_tracking", "shared"} for row in rows))
        self.assertTrue(all(row.get("affectedJobs") for row in rows))
        self.assertTrue(all("rawStatus" in row for row in rows))
        self.assertFalse(any(
            row.get("source_id") == "project_website_identity"
            and row.get("status") == "configuration_missing"
            and row.get("http_status") in {401, 403}
            for row in rows
        ))


if __name__ == "__main__":
    unittest.main()
