#!/usr/bin/env python3
"""Regression checks over the frozen real-data evidence used by C2.4."""

from __future__ import annotations

import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "C2.4_RULE_REGRESSION_MANIFEST.json"
CONFIG_PATH = ROOT / "docs" / "C2.4_RULE_CONFIG.json"


def read_jsonl(relative_path: str) -> list[dict]:
    path = ROOT / relative_path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values if isinstance(value, (int, float)))
    point = (len(ordered) - 1) * probability
    lower = int(point)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = point - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


class C24FrozenRealSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.assertions = cls.manifest["observedAssertions"]

    def test_frozen_evidence_files_have_not_changed(self):
        for item in self.manifest["realEvidenceFiles"]:
            path = ROOT / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            payload = path.read_bytes()
            self.assertEqual(len(payload), item["bytes"], item["path"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), item["sha256"], item["path"])
            if "physicalRows" in item:
                rows = sum(1 for line in payload.splitlines() if line.strip())
                self.assertEqual(rows, item["physicalRows"], item["path"])

    def test_market_age_counts_are_reproducible_from_last_physical_records(self):
        rows = read_jsonl("reports/c2.1-age-threshold-analysis/market-observations.jsonl")
        latest = {}
        for row in rows:
            latest[(row.get("networkId"), row.get("tokenAddress"))] = row
        eligible = [
            row
            for row in latest.values()
            if row.get("state") == "success"
            and row.get("effectiveAgeBand") in self.assertions["effectiveMarketSuccessAgeCounts"]
        ]
        self.assertEqual(
            dict(Counter(row["effectiveAgeBand"] for row in eligible)),
            self.assertions["effectiveMarketSuccessAgeCounts"],
        )

    def test_frozen_p40_p50_values_recompute_from_real_evidence(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = read_jsonl("reports/c2.1-age-threshold-analysis/market-observations.jsonl")
        latest = {}
        for row in rows:
            latest[(row.get("networkId"), row.get("tokenAddress"))] = row
        eligible = [row for row in latest.values() if row.get("state") == "success"]
        for band, frozen in config["ageBands"].items():
            pairs = [row["bestPair"] for row in eligible if row.get("effectiveAgeBand") == band]
            metrics = {
                "liquidityUsd": [row["liquidityUsd"] for row in pairs],
                "volumeUsd": [row["volumeH24Usd"] for row in pairs],
                "transactions": [(row.get("buysH24") or 0) + (row.get("sellsH24") or 0) for row in pairs],
                "volumeLiquidityRatio": [row["volumeH24Usd"] / row["liquidityUsd"] for row in pairs if row.get("liquidityUsd")],
            }
            for metric, values in metrics.items():
                self.assertAlmostEqual(quantile(values, 0.40), frozen["fallbackP40"][metric], places=8)
                self.assertAlmostEqual(quantile(values, 0.50), frozen["fallbackP50"][metric], places=8)

        path4 = read_jsonl("reports/c2.1-path4-full-pool-supply-probe/path4-inputs.jsonl")
        for band, frozen in config["ageBands"].items():
            values = [
                row["relativeExpansion"]
                for row in path4
                if row.get("effectiveAgeBand") == band
                and row.get("indexedPoolPath4InputUsable") is True
                and isinstance(row.get("relativeExpansion"), (int, float))
            ]
            self.assertAlmostEqual(quantile(values, 0.50), frozen["fallbackP50"]["relativeExpansion"], places=8)

    def test_fixed_sample_network_mix_is_preserved(self):
        rows = read_jsonl("reports/c2.1-strong-path-input-probe/sample-selection.jsonl")
        self.assertEqual(len(rows), self.assertions["fixedRealStrongPathProjects"])
        self.assertEqual(dict(Counter(row["networkId"] for row in rows)), self.assertions["networkCounts"])
        self.assertTrue(all(row.get("state") == "success" for row in rows))

    def test_standard_sell_quote_boundaries_are_reproducible(self):
        rows = read_jsonl("reports/c2.1-strong-path-input-probe/quote-observations.jsonl")
        expected = self.assertions["standardSellQuote"]
        states = Counter(row.get("state") for row in rows)
        self.assertEqual(states["success"], expected["success"])
        self.assertEqual(states["no_data"], expected["no_data"])
        self.assertEqual(states["unsupported"], expected["unsupported"])
        losses = [
            row["quoteLossPct"]
            for row in rows
            if row.get("state") == "success" and isinstance(row.get("quoteLossPct"), (int, float))
        ]
        self.assertEqual(sum(loss <= 10 for loss in losses), expected["successAndLossPctLte10"])
        self.assertEqual(sum(loss >= 20 for loss in losses), expected["successAndLossPctGte20"])

    def test_product_and_supply_boundaries_remain_honest(self):
        product_rows = read_jsonl("reports/c2.1-strong-path-input-probe/product-inputs.jsonl")
        verified_product_series = sum(
            row.get("state") == "success" and bool(row.get("localProjectMappings"))
            for row in product_rows
        )
        self.assertEqual(verified_product_series, self.assertions["verifiedProductUsageSeries"])

        path4_rows = read_jsonl("reports/c2.1-path4-full-pool-supply-probe/path4-inputs.jsonl")
        self.assertEqual(
            sum(row.get("indexedCoverageState") == "complete_for_indexed_set" for row in path4_rows),
            self.assertions["indexedPoolCoverageCompleteProjects"],
        )
        self.assertEqual(
            sum(row.get("state") == "success" for row in path4_rows),
            self.assertions["historicalSupplySuccessProjects"],
        )
        self.assertEqual(
            sum(
                row.get("state") == "success"
                and isinstance(row.get("supplyChangePct"), (int, float))
                and abs(row["supplyChangePct"]) <= 1
                for row in path4_rows
            ),
            self.assertions["historicalSupplyUnitScaleStableProjects"],
        )


if __name__ == "__main__":
    unittest.main()
