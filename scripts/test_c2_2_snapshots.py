#!/usr/bin/env python3
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_c2_2_snapshots import (
    C22_ADMIN_PREFIX,
    C22_FRONT_PREFIX,
    C22_TRACKING_PREFIX,
    _write_snapshot_group,
    _source_health_with_ownership,
    build_payloads,
    build_snapshots,
)
from build_tracking_tasks_snapshot import load_js_payload
from c2_2_tracking import load_tracking_candidates


class C22SnapshotTests(unittest.TestCase):
    def _source(self):
        front = {
            "schemaVersion": "c2.1-front-v1",
            "buildId": "c21-build",
            "generatedAt": "2026-08-11T00:00:00Z",
            "sourceCutoffAt": "2026-08-10T23:59:00Z",
            "ruleVersion": "c2.1-rules-v1",
            "ruleConfigHash": "rules",
            "coverageSummary": {"frontVisibleCount": 2},
            "sourceImpactSummary": {"status": "healthy"},
            "materialChanges": [],
            "items": [
                self._item("asset-1", "A", "convexity_clue", 4),
                self._item("asset-2", "C", "data_limited", 0),
                self._item("asset-3", "D", "active_project", 2),
            ],
        }
        admin = {"generatedAt": front["generatedAt"], "sourceHealth": [], "cursors": [], "runs": [], "quality": {}}
        return front, admin

    @staticmethod
    def _item(asset, relationship, state, backfilled):
        return {
            "assetId": asset,
            "projectId": f"project-{asset}",
            "canonicalName": asset,
            "symbol": asset.upper(),
            "chainId": "ethereum-mainnet",
            "contractAddressMasked": "0xmask",
            "detailUrl": f"project-detail.html?id=project-{asset}",
            "t0": {"value": "2026-08-01T00:00:00Z"},
            "ageDays": 10,
            "relationshipClass": relationship,
            "hardGate": {"status": "pass", "checks": []},
            "productEvidence": {"hasAnyQualifyingEvidence": True, "qualifyingTypes": ["github"]},
            "observationHistory": {"backfilledDays": backfilled, "validHistoryDays": backfilled, "lastSuccessfulAt": "2026-08-11T00:00:00Z"},
            "displayState": {"code": state},
            "evidencePaths": ([
                {"pathCode": "trade_liquidity_formation", "status": "formed"},
                {"pathCode": "verified_product_usage_expansion", "status": "formed"},
            ] if state == "convexity_clue" else []),
            "factorDirections": [{"factor": "D", "direction": "stable"}],
            "dataConfidence": {"level": "sufficient"},
            "marketSnapshot": {"sourceStatus": "success", "liquidityUsd": 1000} if backfilled else None,
            "riskSummary": {"status": "no_confirmed_hard_block"},
            "latestMaterialChange": None,
        }

    def test_join_filters_d_and_removes_data_limited_from_public_states(self):
        front, tracking, admin = build_payloads(*self._source())
        self.assertEqual(len(front["items"]), 2)
        self.assertNotIn("data_limited", front["trackingStateCounts"])
        self.assertEqual(front["trackingStateCounts"]["convexity_clue"], 1)
        self.assertEqual(front["trackingStateCounts"]["awaiting_first_tracking"], 1)
        self.assertEqual(front["lifecycleCounts"]["A"], 1)
        self.assertEqual(front["lifecycleCounts"]["C"], 1)
        self.assertEqual(tracking["candidateBuildId"], front["candidateBuildId"])
        self.assertTrue(admin["inheritance"]["dataLimitedFrontStateRemoved"])
        self.assertEqual({row["assetId"] for row in front["items"]}, {"asset-1", "asset-2"})

    def test_completed_tracking_candidates_are_counted_without_exposing_backend_only_details(self):
        source_front, source_admin = self._source()
        tracking_candidates = [
            {
                "_candidateId": 4,
                "_qualificationBatchId": "batch-completed-1",
                "assetId": "asset-4",
                "projectId": None,
                "canonicalName": "后台候选",
                "symbol": "BACK",
                "chainId": "base-mainnet",
                "t0": {"value": "2026-08-10T00:00:00Z", "status": "verified_in_supported_scope"},
                "ageDays": 1,
                "relationshipClass": "D",
                "qualifiedAt": "2026-08-12T01:02:03Z",
            }
        ]

        front, tracking, admin = build_payloads(
            source_front,
            source_admin,
            tracking_candidates=tracking_candidates,
        )

        self.assertEqual({row["assetId"] for row in front["items"]}, {"asset-1", "asset-2"})
        self.assertEqual(
            {row["assetId"] for row in tracking["items"]},
            {"asset-1", "asset-2"},
        )
        self.assertEqual(tracking["inputSummary"]["candidateCount"], 3)
        self.assertEqual(tracking["inputSummary"]["detailedPublicItemCount"], 2)
        self.assertEqual(tracking["inputSummary"]["evaluatedCandidateCount"], 0)
        self.assertEqual(tracking["inputSummary"]["pendingFirstTrackingCount"], 1)
        self.assertEqual(tracking["inputSummary"]["backendIdentityPendingCount"], 1)
        self.assertEqual(tracking["inputSummary"]["completedQualificationBatchCount"], 1)
        self.assertEqual(front["trackingCandidateBuildId"], tracking["candidateBuildId"])
        self.assertEqual(admin["trackingQualification"]["candidateCount"], 3)
        self.assertEqual(front["trackingStateCounts"]["awaiting_first_tracking"], 1)
        self.assertEqual(admin["screening"]["generatedAt"], "2026-08-11T00:00:00Z")
        self.assertEqual(tracking["generatedAt"], "2026-08-12T01:02:03Z")
        self.assertEqual(front["generatedAt"], tracking["generatedAt"])

    def test_rule_evaluation_is_not_reported_as_completed_first_tracking(self):
        source_front, source_admin = self._source()
        tracking_candidates = [
            {
                "_candidateId": 1,
                "_qualificationBatchId": "batch-1",
                "assetId": "asset-1",
                "relationshipClass": "A",
                "qualifiedAt": "2026-08-12T01:00:00Z",
            },
            {
                "_candidateId": 2,
                "_qualificationBatchId": "batch-1",
                "assetId": "asset-2",
                "relationshipClass": "C",
                "qualifiedAt": "2026-08-12T01:00:00Z",
            },
            {
                "_candidateId": 4,
                "_qualificationBatchId": "batch-1",
                "assetId": "asset-4",
                "relationshipClass": "D",
                "qualifiedAt": "2026-08-12T01:00:00Z",
            },
        ]
        tracking_catalog = {
            1: {"candidateId": 1, "evaluatedAt": "2026-08-12T02:00:00Z", "evaluation": {}},
            2: {"candidateId": 2, "evaluatedAt": "2026-08-12T02:00:00Z", "evaluation": {}},
        }
        tracking_records = {
            1: {"state": "completed", "completedAt": "2026-08-12T02:01:00Z"},
            2: {"state": "partial", "completedAt": None},
        }

        front, tracking, admin = build_payloads(
            source_front,
            source_admin,
            tracking_candidates=tracking_candidates,
            tracking_catalog=tracking_catalog,
            tracking_records=tracking_records,
        )

        summary = tracking["inputSummary"]
        self.assertEqual(summary["ruleEvaluatedCandidateCount"], 2)
        self.assertEqual(summary["completedFirstTrackingCount"], 1)
        self.assertEqual(summary["pendingFirstTrackingCount"], 1)
        self.assertEqual(summary["backendIdentityPendingCount"], 1)
        self.assertNotEqual(
            next(item for item in front["items"] if item["assetId"] == "asset-1")["trackingState"],
            "awaiting_first_tracking",
        )
        self.assertEqual(
            next(item for item in front["items"] if item["assetId"] == "asset-2")["trackingState"],
            "awaiting_first_tracking",
        )
        self.assertEqual(admin["trackingQualification"]["completedFirstTrackingCount"], 1)

    def test_tracking_handoff_reads_all_classes_from_completed_batches_only(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "pipeline.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE candidates(
                  candidate_id INTEGER PRIMARY KEY,network_id TEXT,canonical_name TEXT,symbol TEXT
                );
                CREATE TABLE candidate_production_records(
                  candidate_id INTEGER PRIMARY KEY,asset_id TEXT,project_id TEXT,t0_status TEXT,
                  effective_t0 TEXT,age_days INTEGER,relationship_class TEXT,front_eligible INTEGER,
                  qualification_batch_id TEXT,qualified_at TEXT,tracking_eligible INTEGER
                );
                CREATE TABLE candidate_qualification_batches(
                  qualification_batch_id TEXT PRIMARY KEY,state TEXT
                );
                CREATE TABLE candidate_qualification_members(
                  qualification_batch_id TEXT,candidate_id INTEGER
                );
                CREATE TABLE candidate_first_gate_queue(
                  candidate_id INTEGER PRIMARY KEY,state TEXT
                );
                """
            )
            connection.executemany(
                "INSERT INTO candidates VALUES(?,?,?,?)",
                [
                    (1, "base-mainnet", "Market only", "MKT"),
                    (2, "base-mainnet", "Pending", "WAIT"),
                    (3, "base-mainnet", "Gate passed", "PASS"),
                ],
            )
            connection.executemany(
                "INSERT INTO candidate_qualification_batches VALUES(?,?)",
                [("batch-done", "completed"), ("batch-pending", "running")],
            )
            connection.executemany(
                "INSERT INTO candidate_production_records VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (1, "asset-1", "should-not-leak", "verified_in_supported_scope", "2026-08-10T00:00:00Z", 1, "D", 0, "batch-done", "2026-08-11T00:00:00Z", 1),
                    (2, "asset-2", None, "verified_in_supported_scope", "2026-08-10T00:00:00Z", 1, "D", 0, "batch-pending", "2026-08-11T00:00:00Z", 1),
                    (3, "asset-3", None, "verified_in_supported_scope", "2026-08-10T00:00:00Z", 1, "C", 1, "batch-done", "2026-08-11T00:00:00Z", 1),
                ],
            )
            connection.executemany(
                "INSERT INTO candidate_qualification_members VALUES(?,?)",
                [("batch-done", 1), ("batch-pending", 2), ("batch-done", 3)],
            )
            connection.executemany(
                "INSERT INTO candidate_first_gate_queue VALUES(?,?)",
                [(1, "completed"), (2, "pending"), (3, "completed")],
            )
            connection.commit()
            connection.close()

            rows = load_tracking_candidates(database)

            self.assertEqual([row["assetId"] for row in rows], ["asset-1", "asset-3"])
            self.assertTrue(all(row["_qualificationBatchId"] == "batch-done" for row in rows))
            self.assertTrue(all(row["projectId"] is None for row in rows))

    def test_source_health_has_explicit_job_ownership_and_honest_access_boundary(self):
        rows = _source_health_with_ownership([
            {"source_id": "github", "status": "success", "http_status": 200},
            {"source_id": "standard_sell_quote", "status": "no_data", "http_status": None},
            {"source_id": "dexscreener", "status": "source_failure", "http_status": 503},
            {"source_id": "project_website_identity", "status": "configuration_missing", "http_status": 403},
        ])
        by_source = {row["source_id"]: row for row in rows}
        self.assertEqual(by_source["github"]["owner"], "screening")
        self.assertEqual(by_source["standard_sell_quote"]["owner"], "convexity_tracking")
        self.assertEqual(by_source["dexscreener"]["owner"], "shared")
        self.assertEqual(by_source["dexscreener"]["affectedJobs"], ["screening", "convexity_tracking"])
        website = by_source["project_website_identity"]
        self.assertEqual(website["rawStatus"], "configuration_missing")
        self.assertEqual(website["status"], "unsupported")
        self.assertEqual(website["reason_code"], "website_access_restricted")

    def test_live_source_writes_three_atomic_files_while_background_inputs_can_advance(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp:
            first = build_snapshots(
                c21_front_path=root / "app" / "c2-1-front-snapshot.js",
                c21_admin_path=root / "app" / "c2-1-admin-snapshot.js",
                output_dir=Path(temp),
                write=True,
            )
            second = build_snapshots(
                c21_front_path=root / "app" / "c2-1-front-snapshot.js",
                c21_admin_path=root / "app" / "c2-1-admin-snapshot.js",
                output_dir=Path(temp),
                write=True,
            )
            self.assertEqual(first["front"]["schemaVersion"], second["front"]["schemaVersion"])
            self.assertEqual(first["tracking"]["schemaVersion"], second["tracking"]["schemaVersion"])
            self.assertTrue((Path(temp) / "c2-2-front-snapshot.js").exists())
            self.assertTrue((Path(temp) / "c2-2-tracking-snapshot.js").exists())
            self.assertTrue((Path(temp) / "c2-2-admin-snapshot.js").exists())
            parsed = load_js_payload(Path(temp) / "c2-2-front-snapshot.js", C22_FRONT_PREFIX)
            self.assertEqual(parsed["schemaVersion"], "c2.2-front-v1")
            self.assertEqual(len(parsed["items"]), first["front"]["coverageSummary"]["frontVisibleCount"])

    def test_snapshot_group_restores_previous_complete_set_on_partial_publish_failure(self):
        old_payloads = [
            ("c2-2-front-snapshot.js", {"schemaVersion": "old-front", "value": 1}, C22_FRONT_PREFIX),
            ("c2-2-tracking-snapshot.js", {"schemaVersion": "old-tracking", "value": 2}, C22_TRACKING_PREFIX),
            ("c2-2-admin-snapshot.js", {"schemaVersion": "old-admin", "value": 3}, C22_ADMIN_PREFIX),
        ]
        new_payloads = [
            (name, {"schemaVersion": payload["schemaVersion"].replace("old", "new"), "value": payload["value"] + 10}, prefix)
            for name, payload, prefix in old_payloads
        ]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            for name, payload, prefix in old_payloads:
                (target / name).write_text(
                    prefix + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + ";\n",
                    encoding="utf-8",
                )
            real_replace = os.replace
            failed_once = False

            def fail_during_second_commit(source, destination):
                nonlocal failed_once
                if not failed_once and str(source).endswith("c2-2-tracking-snapshot.js.tmp"):
                    failed_once = True
                    raise OSError("simulated partial publish failure")
                return real_replace(source, destination)

            with patch("build_c2_2_snapshots.os.replace", side_effect=fail_during_second_commit):
                with self.assertRaisesRegex(OSError, "simulated partial publish failure"):
                    _write_snapshot_group(target, new_payloads)

            for name, payload, prefix in old_payloads:
                restored = load_js_payload(target / name, prefix)
                self.assertEqual(restored, payload)
            self.assertFalse(any(target.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
