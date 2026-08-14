#!/usr/bin/env python3
import unittest

from c2_2_bayes import metric_z
from c2_2_tracking import _PreparedMetricCohort, build_bayes_evidence


class C22TrackingTests(unittest.TestCase):
    def test_prepared_cohort_is_numerically_identical_to_frozen_metric_transform(self):
        cases = [
            ([0, 0, 1, 2, 2, 10, 100], "nonnegative", "positive"),
            ([0.01, 0.1, 0.1, 0.5, 0.9, 1.0], "proportion", "negative"),
            ([-3, -1, 0, 2, 8], "raw", "positive"),
        ]
        for values, kind, direction in cases:
            prepared = _PreparedMetricCohort(values, kind, direction)
            for value in [*values, 0.3, 50]:
                self.assertAlmostEqual(
                    prepared.z(value, include_current=False),
                    metric_z(value, values, kind=kind, direction=direction),
                    places=12,
                )
                self.assertAlmostEqual(
                    prepared.z(value, include_current=True),
                    metric_z(value, [*values, value], kind=kind, direction=direction),
                    places=12,
                )

    def test_catalog_metrics_feed_bayes_without_reallocating_missing_indicators(self):
        metrics = {
            "volume": 1000,
            "trade_count": 20,
            "volume_liquidity": 0.2,
            "real_buy_sell": 10,
            "relative_liquidity": 0.1,
            "standard_sell_loss_inverse": 0.02,
            "liquidity_retention": None,
            "top10_concentration_inverse": 0.6,
            "hhi_inverse": 0.8,
            "net_supply_reduction": 0.01,
            "product_usage_growth": None,
            "relative_expansion": 0.1,
            "risk_adjusted_remaining": 0.05,
            "severe_anomaly_inverse": 1.0,
            "cross_source_consistency": 1.0,
            "activity_concentration_inverse": None,
        }
        catalog = {
            index: {
                "candidateId": index,
                "networkId": "ethereum-mainnet",
                "ageBand": "age_31_90",
                "sourceCount": 2,
                "observedAt": "2026-08-11T00:00:00Z",
                "metrics": {**metrics, "volume": metrics["volume"] + index},
            }
            for index in range(1, 22)
        }
        item = {
            "projectId": "c21-1",
            "assetId": "asset-1",
            "chainId": "ethereum-mainnet",
            "ageBand": "age_31_90",
            "dataCutoffAt": "2026-08-11T00:00:00Z",
            "confidenceSummary": {"components": {"fieldCoverage": 1, "dataFreshness": 1, "realHistoryCoverage": 1, "crossSourceConsistency": 1}},
        }
        result = build_bayes_evidence(item, catalog)
        self.assertGreater(result["total"]["measuredIndicatorCount"], 0)
        self.assertGreater(result["confidenceScore"], 0)
        self.assertEqual(result["indicators"]["liquidity_retention"]["measuredObservations"], 0)
        self.assertIn(result["factors"][0]["direction"], {"stable", "improving", "weakening", "no_measured"})

    def test_historical_windows_accumulate_once_and_duplicate_keys_do_not_count_twice(self):
        metrics = {"volume": 1000, "trade_count": 20, "volume_liquidity": 0.2, "real_buy_sell": 10, "relative_liquidity": 0.1, "standard_sell_loss_inverse": 0.02, "liquidity_retention": None, "top10_concentration_inverse": 0.6, "hhi_inverse": 0.8, "net_supply_reduction": 0.01, "product_usage_growth": None, "relative_expansion": 0.1, "risk_adjusted_remaining": 0.05, "severe_anomaly_inverse": 1.0, "cross_source_consistency": 1.0, "activity_concentration_inverse": None}
        catalog = {
            index: {"candidateId": index, "networkId": "ethereum-mainnet", "ageBand": "age_31_90", "sourceCount": 2, "observedAt": "2026-08-11T00:00:00Z", "metrics": {**metrics, "volume": metrics["volume"] + index}}
            for index in range(1, 22)
        }
        catalog[1]["metricObservations"] = [
            {"observedAt": "2026-08-10T00:00:00Z", "observationId": "w1", "sourceStatus": "success", "sourceCount": 2, "metrics": metrics},
            {"observedAt": "2026-08-11T00:00:00Z", "observationId": "w2", "sourceStatus": "success", "sourceCount": 2, "metrics": {**metrics, "volume": 4000}},
            {"observedAt": "2026-08-11T00:00:00Z", "observationId": "w2", "sourceStatus": "success", "sourceCount": 2, "metrics": {**metrics, "volume": 4000}},
        ]
        item = {"projectId": "c21-1", "assetId": "asset-1", "chainId": "ethereum-mainnet", "ageBand": "age_31_90", "dataCutoffAt": "2026-08-11T00:00:00Z", "confidenceSummary": {"components": {"fieldCoverage": 1, "dataFreshness": 1, "realHistoryCoverage": 1, "crossSourceConsistency": 1}}}
        result = build_bayes_evidence(item, catalog)
        self.assertEqual(result["indicators"]["volume"]["measuredObservations"], 2)
        self.assertEqual(result["indicators"]["volume"]["duplicateObservations"], 1)


if __name__ == "__main__":
    unittest.main()
