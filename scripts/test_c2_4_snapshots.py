#!/usr/bin/env python3
"""Tier 0 C2.4 snapshot and frozen-scope reconciliation tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))

from build_c2_4_snapshots import CHAIN_ORDER, _important_changes, _source_summary, build_snapshots  # noqa: E402
from repair_c2_4_website_source_states import repair as repair_website_states  # noqa: E402


class C24FrozenScopeTests(unittest.TestCase):
    def test_formal_requirement_hashes_are_unchanged(self):
        lock = json.loads((PROJECT_ROOT / "docs" / "C2.4_REQUIREMENTS_LOCK.json").read_text(encoding="utf-8"))
        canonical = []
        for item in lock["documents"]:
            path = PROJECT_ROOT / item["path"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, item["sha256"], item["path"])
            self.assertEqual(path.stat().st_size, item["bytes"], item["path"])
            canonical.append(f"{item['path']}:{digest}")
        requirement_set = hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()
        self.assertEqual(requirement_set, lock["requirementSetSha256"])


class C24SnapshotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payloads = build_snapshots(write=False)

    def test_atomic_metadata_and_cutoff_match(self):
        cutoffs = set()
        for payload in self.payloads.values():
            self.assertTrue(payload["isComplete"])
            self.assertTrue(payload["buildId"])
            self.assertEqual(len(payload["contentSha256"]), 64)
            cutoffs.add(payload["dataCutoffAt"])
        self.assertEqual(len(cutoffs), 1)

    def test_first_gate_tracking_public_and_all_opportunities_reconcile(self):
        candidate = self.payloads["candidate"]
        tracking = self.payloads["tracking"]
        front = self.payloads["front"]
        admin = self.payloads["admin"]
        history = {item["assetId"] for item in candidate["firstGatePassedHistory"]}
        queue = {item["assetId"] for item in candidate["firstGateQueue"]}
        tracked = {item["assetId"] for item in tracking["items"]}
        public = {item["assetId"] for item in front["items"]}
        self.assertTrue(queue)
        self.assertTrue(queue <= history)
        self.assertTrue(public <= tracked)
        self.assertEqual(public, set(front["allOpportunities"]))
        self.assertEqual(admin["reconciliation"]["differences"], {
            "publicNotTracked": [],
            "newTrackedNotQueued": [],
            "trackedNotFirstGateHistory": [],
            "continuedMissingHistory": [],
        })

    def test_first_gate_has_only_the_four_frozen_checks(self):
        for item in self.payloads["candidate"]["firstGateQueue"]:
            self.assertEqual(len(item["firstGateChecks"]), 4)
            self.assertTrue(all(check["passed"] for check in item["firstGateChecks"]))

    def test_candidate_funnel_separates_rule_failures_from_waiting_work(self):
        funnel = self.payloads["admin"]["candidateFunnel"]
        self.assertEqual(
            [stage["code"] for stage in funnel["stages"]],
            ["all_candidates", "market_confirmed", "first_gate_passed"],
        )
        self.assertEqual(len(funnel["transitions"]), 2)
        for transition, stage in zip(funnel["transitions"], funnel["stages"]):
            self.assertEqual(
                stage["count"],
                transition["passed"] + transition["notPassed"] + transition["waiting"],
            )
            self.assertEqual(
                transition["notPassed"] + transition["waiting"],
                sum(reason["count"] for reason in transition["primaryReasons"]),
            )
        outside = {row["code"]: row for row in funnel["outsideFunnel"]}
        self.assertEqual(
            funnel["stages"][1]["count"] - funnel["stages"][2]["count"],
            outside["first_gate_waiting"]["count"] + outside["first_gate_not_passed"]["count"],
        )
        self.assertEqual(outside["first_gate_waiting"]["kind"], "waiting")
        self.assertEqual(outside["first_gate_not_passed"]["kind"], "not_passed")

    def test_tracking_funnel_reconciles_processing_publication_and_lifecycle(self):
        funnel = self.payloads["admin"]["trackingFunnel"]
        self.assertEqual(
            [stage["code"] for stage in funnel["stages"]],
            ["received", "deep_tracking_completed", "published", "continued_91_plus"],
        )
        self.assertEqual(len(funnel["transitions"]), 3)
        for transition, stage in zip(funnel["transitions"], funnel["stages"]):
            self.assertEqual(
                stage["count"],
                transition["passed"] + transition["notPassed"] + transition["waiting"],
            )
            self.assertEqual(
                transition["notPassed"] + transition["waiting"],
                sum(reason["count"] for reason in transition["primaryReasons"]),
            )
        lifecycle = funnel["transitions"][-1]
        self.assertEqual(lifecycle["kind"], "lifecycle")
        self.assertEqual(lifecycle["notPassed"], 0)
        self.assertIn("不能手动提前", lifecycle["manualAction"])

    def test_public_items_are_complete_second_gate_results(self):
        allowed_states = {"convexity_clue", "active_project", "observing"}
        for item in self.payloads["front"]["items"]:
            self.assertTrue(item["publicEligible"])
            self.assertIn(item["publicState"], allowed_states)
            self.assertEqual(item["deepTrackingState"], "completed")
            self.assertEqual(item["sellQuoteState"], "success")
            self.assertTrue(all(check["passed"] for check in item["publicBaseline"]["checks"]))
            self.assertNotEqual(item["relationshipClass"], "D")
            self.assertEqual(len(item["strongPaths"]), 4)

    def test_complete_results_publish_real_path_cohort_or_explicit_fallback(self):
        allowed = {
            "same_chain_same_age_band_rolling_30_days",
            "all_supported_chains_same_age_band_rolling_30_days",
            "same_chain_continued_91_plus_rolling_30_days",
            "all_supported_chains_continued_91_plus_rolling_30_days",
            "same_chain_age_31_90_rolling_30_days",
            "all_supported_chains_age_31_90_rolling_30_days",
            "frozen_age_band_fallback",
        }
        completed = [row for row in self.payloads["tracking"]["items"] if row["deepTrackingState"] == "completed"]
        self.assertTrue(completed)
        self.assertTrue(any(row["cohortScope"] != "frozen_age_band_fallback" for row in completed))
        for row in completed:
            self.assertIn(row["cohortScope"], allowed)
            self.assertGreaterEqual(row["cohortSampleSize"], 0)
            self.assertIsInstance(row["cohortMetricSampleSizes"], dict)
            self.assertIsInstance(row["cohortThresholds"], dict)
            self.assertEqual(len(row["strongPaths"]), 4)

    def test_home_is_only_a_per_chain_top_ten_projection(self):
        front = self.payloads["front"]
        public = {item["assetId"] for item in front["items"]}
        self.assertEqual(tuple(front["chainOrder"]), CHAIN_ORDER)
        for chain, asset_ids in front["homeTop10"].items():
            self.assertLessEqual(len(asset_ids), 10, chain)
            self.assertTrue(set(asset_ids) <= public, chain)
            for asset_id in asset_ids:
                item = next(row for row in front["items"] if row["assetId"] == asset_id)
                self.assertEqual(item["chainId"], chain)
                self.assertTrue(item["rankingAvailable"])

    def test_all_29_inherited_routes_have_current_acceptance_ownership(self):
        routes = self.payloads["admin"]["routeInventory"]
        self.assertEqual(len(routes), 29)
        self.assertEqual(len({item["path"] for item in routes}), 29)
        self.assertTrue(all(item.get("c2_4Location") and item.get("acceptance") for item in routes))

    def test_important_changes_use_only_adjacent_c24_public_snapshots(self):
        previous_item = {
            "assetId": "asset-1", "chainId": "base-mainnet", "lifecyclePool": "new_0_90",
            "publicState": "active_project", "formedPathCodes": ["trade_demand_formation"],
            "nextWatch": "old",
        }
        previous = {
            "items": [previous_item],
            "changes": [{"changeId": "c21-change-old", "whatChanged": "data_limited"}],
        }
        current = [{
            **previous_item,
            "publicState": "convexity_clue",
            "formedPathCodes": ["trade_demand_formation", "supply_holder_improvement"],
            "lifecyclePool": "continued_91_plus",
            "nextWatch": "next",
        }]
        changes = _important_changes(previous, current, "2026-08-13T00:00:00Z")
        self.assertEqual(len(changes), 3)
        self.assertTrue(all(row["changeId"].startswith("c24-change-") for row in changes))
        self.assertFalse(any("data_limited" in row["whatChanged"] for row in changes))
        self.assertTrue(any("凸性线索" in row["whatChanged"] for row in changes))
        self.assertTrue(any("90 天后持续跟踪" in row["whatChanged"] for row in changes))

    def test_public_exit_is_kept_as_an_explained_c24_change(self):
        previous = {"items": [{
            "assetId": "asset-1", "chainId": "base-mainnet", "lifecyclePool": "new_0_90",
            "publicState": "observing", "formedPathCodes": [], "nextWatch": "old",
        }], "changes": []}
        changes = _important_changes(previous, [], "2026-08-13T00:00:00Z")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["whatChanged"], "已撤下公开展示")
        self.assertIn("重新满足公开底线", changes[0]["nextWatch"])

    def test_project_website_403_is_a_capability_boundary_not_missing_configuration(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """CREATE TABLE source_health(
            source_id TEXT,status TEXT,http_status INTEGER,affected_object_count INTEGER,
            updated_at TEXT,last_success_at TEXT,plain_reason TEXT)"""
        )
        connection.execute(
            """INSERT INTO source_health VALUES(
            'project_website_identity','configuration_missing',403,1,
            '2026-08-13T00:00:00Z',NULL,'旧错误说明')"""
        )
        rows = _source_summary(connection)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "unsupported")
        self.assertIn("拒绝自动访问", rows[0]["plain_reason"])
        connection.close()

    def test_website_state_repair_reclassifies_access_denial_and_resets_shared_circuit_failures(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """CREATE TABLE source_health(
            source_id TEXT,scope_key TEXT,status TEXT,reason_code TEXT,plain_reason TEXT,
            http_status INTEGER,quota_remaining REAL,quota_reset_at TEXT,
            affected_object_count INTEGER,last_success_at TEXT,updated_at TEXT,
            PRIMARY KEY(source_id,scope_key));
            CREATE TABLE source_cursors(
            source_id TEXT,scope_key TEXT,stage TEXT,cursor_json TEXT,status TEXT,
            consecutive_failures INTEGER,next_retry_at TEXT,last_success_at TEXT,updated_at TEXT,
            PRIMARY KEY(source_id,scope_key,stage));
            INSERT INTO source_health VALUES
            ('project_website_identity','1','configuration_missing','','old',403,NULL,NULL,1,NULL,'old'),
            ('project_website_identity','2','source_failure','','old',NULL,NULL,NULL,1,NULL,'old');
            INSERT INTO source_cursors VALUES
            ('project_website_identity','1','identity','{}','configuration_missing',1,NULL,NULL,'old'),
            ('project_website_identity','2','identity','{}','source_failure',4,'later',NULL,'old');"""
        )
        result = repair_website_states(connection)
        self.assertEqual(result["reclassifiedAccessDenied"], 1)
        self.assertEqual(result["resetSharedCircuitFailures"], 1)
        self.assertEqual(connection.execute("SELECT status FROM source_health WHERE scope_key='1'").fetchone()[0], "unsupported")
        self.assertIsNone(connection.execute("SELECT status FROM source_health WHERE scope_key='2'").fetchone())
        connection.close()


if __name__ == "__main__":
    unittest.main()
