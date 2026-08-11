#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_c2_2_snapshots import (
    C22_ADMIN_PREFIX,
    C22_FRONT_PREFIX,
    C22_TRACKING_PREFIX,
    _write_snapshot_group,
    build_payloads,
    build_snapshots,
)
from build_tracking_tasks_snapshot import load_js_payload


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

    def test_real_source_is_deterministic_and_writes_three_atomic_files(self):
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
            self.assertEqual(first["front"]["buildId"], second["front"]["buildId"])
            self.assertEqual(first["tracking"]["buildId"], second["tracking"]["buildId"])
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
