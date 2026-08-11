#!/usr/bin/env python3
import unittest

from c2_1_rules import evaluate_candidate, load_rules


class C21RuleTests(unittest.TestCase):
    def setUp(self):
        self.rules, self.rule_hash = load_rules()
        self.product = [{
            "evidenceId": "business-1",
            "evidenceType": "business",
            "status": "qualifying",
            "identityStatus": "verified",
            "sourceName": "structured business",
            "observedAt": "2026-08-10T00:00:00Z",
        }]
        self.market = {
            "sourceStatus": "success",
            "sourceName": "market",
            "observedAt": "2026-08-10T00:00:00Z",
            "pairAddress": "pool-1",
            "liquidityUsd": 100000,
            "volumeUsd": 100000,
            "transactionCount": 1000,
            "observedBuys": 20,
            "observedSells": 10,
            "volumeLiquidityRatio": 1,
            "standardSellQuoteState": "success",
            "standardSellQuoteLossPct": 2,
            "evidenceIds": ["market-1"],
        }
        self.risks = [{"sourceName": "risk", "sourceStatus": "success", "hardTradeBlock": False, "severeAnomaly": False}]

    def candidate(self, t0="2026-08-08T00:00:00Z", relationship="C"):
        return {
            "effectiveT0": t0,
            "t0Status": "verified_in_supported_scope",
            "relationshipClass": relationship,
            "identityStatus": "verified",
            "continuityStatus": "candidate_asset",
            "productEvidenceRecords": self.product,
            "independentSourceTypes": ["market_pool_data", "direct_chain_historical_supply"],
            "validHistoryDays": 2,
            "backfilledDays": 2,
        }

    def test_day_90_visible_day_91_exits(self):
        day90 = evaluate_candidate(self.candidate("2026-05-12T12:00:00Z"), market=self.market, risks=self.risks, as_of="2026-08-10T12:00:00Z", rules=self.rules, rule_hash=self.rule_hash)
        day91 = evaluate_candidate(self.candidate("2026-05-11T12:00:00Z"), market=self.market, risks=self.risks, as_of="2026-08-10T12:00:00Z", rules=self.rules, rule_hash=self.rule_hash)
        self.assertTrue(day90["frontEligible"])
        self.assertEqual(day90["ageDays"], 90)
        self.assertFalse(day91["frontEligible"])
        self.assertEqual(day91["hardGate"]["status"], "fail")

    def test_class_d_is_backend_only(self):
        result = evaluate_candidate(self.candidate(relationship="D"), market=self.market, risks=self.risks, as_of="2026-08-10T12:00:00Z", rules=self.rules, rule_hash=self.rule_hash)
        self.assertFalse(result["frontEligible"])

    def test_quote_unsupported_makes_path_unavailable_not_zero(self):
        market = {**self.market, "standardSellQuoteState": "unsupported", "standardSellQuoteLossPct": None}
        result = evaluate_candidate(self.candidate(), market=market, risks=self.risks, as_of="2026-08-10T12:00:00Z", rules=self.rules, rule_hash=self.rule_hash)
        path = result["evidencePaths"][0]
        self.assertEqual(path["status"], "unavailable")
        self.assertIsNone(path["supportingMetrics"][2]["value"])
        self.assertTrue(result["frontEligible"])

    def test_two_real_paths_form_convexity_clue(self):
        pool = {
            "sourceStatus": "success",
            "indexedPoolCount": 1,
            "ohlcvSuccessCount": 1,
            "supplyHistorySuccess": True,
            "unitScaleStable": True,
            "relativeExpansion": 1,
            "riskAdjustedSurplus": 1,
            "comparisonWindowComplete": True,
            "unindexedDiscoveredPoolCount": 0,
            "evidenceIds": ["path4-1"],
        }
        result = evaluate_candidate(self.candidate(), market=self.market, risks=self.risks, pool_window=pool, as_of="2026-08-10T12:00:00Z", rules=self.rules, rule_hash=self.rule_hash)
        self.assertEqual(result["displayState"]["code"], "convexity_clue")
        self.assertEqual(sum(path["status"] == "formed" for path in result["evidencePaths"]), 2)

    def test_same_window_does_not_add_hysteresis_miss(self):
        previous = {
            "evaluationWindowId": "hour:2026-08-10T12:completed",
            "frontEligible": True,
            "hardGate": {"status": "pass"},
            "displayState": {"code": "convexity_clue"},
            "consecutiveCompletedMisses": 1,
        }
        result = evaluate_candidate(self.candidate(), market={**self.market, "volumeUsd": 0, "transactionCount": 0, "volumeLiquidityRatio": 0}, risks=self.risks, previous=previous, as_of="2026-08-10T12:30:00Z", rules=self.rules, rule_hash=self.rule_hash)
        self.assertEqual(result["displayState"]["code"], "convexity_clue")
        self.assertEqual(result["consecutiveCompletedMisses"], 1)

    def test_confirmed_hard_sell_block_removes_front_immediately(self):
        risks = [{"sourceName": "risk", "sourceStatus": "success", "hardTradeBlock": True, "severeAnomaly": True}]
        result = evaluate_candidate(self.candidate(), market=self.market, risks=risks, as_of="2026-08-10T12:00:00Z", rules=self.rules, rule_hash=self.rule_hash)
        self.assertFalse(result["frontEligible"])
        self.assertEqual(result["hardGate"]["status"], "fail")

    def test_supply_unit_change_bypasses_hysteresis(self):
        previous = {
            "evaluationWindowId": "hour:2026-08-10T11:completed", "frontEligible": True,
            "hardGate": {"status": "pass"}, "displayState": {"code": "convexity_clue"},
            "consecutiveCompletedMisses": 0,
        }
        result = evaluate_candidate(
            self.candidate(), market=self.market, risks=self.risks,
            supply={"historyState": "success", "unitScaleStable": False, "marketActivityVsP50": 1},
            previous=previous, as_of="2026-08-10T12:00:00Z", rules=self.rules, rule_hash=self.rule_hash,
        )
        self.assertNotEqual(result["displayState"]["code"], "convexity_clue")

    def test_provider_failure_is_data_limited_not_path_miss(self):
        candidate = self.candidate()
        candidate["criticalDataInterrupted"] = True
        candidate["sourceImpact"] = {"status": "interrupted", "plainReason": "来源中断", "affectedProjectCount": 1}
        previous = {
            "evaluationWindowId": "hour:2026-08-10T11:completed", "frontEligible": True,
            "hardGate": {"status": "pass"}, "displayState": {"code": "active_project"},
        }
        market = {**self.market, "sourceStatus": "source_failure", "standardSellQuoteState": "source_failure", "standardSellQuoteLossPct": None}
        result = evaluate_candidate(candidate, market=market, risks=self.risks, previous=previous, as_of="2026-08-10T12:00:00Z", rules=self.rules, rule_hash=self.rule_hash)
        self.assertTrue(result["frontEligible"])
        self.assertEqual(result["displayState"]["code"], "data_limited")
        self.assertEqual(result["evidencePaths"][0]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
