#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from c2_4_rules import (
    determine_public_state,
    evaluate_first_gate,
    evaluate_public_baseline,
    evaluate_strong_paths,
    lifecycle_pool,
    normal_exit_decision,
    rank_home_by_chain,
)


def first_gate_fixture(age=10, **changes):
    item = {
        "assetId": "asset-1",
        "chainId": "ethereum-mainnet",
        "contractAddress": "0xtoken",
        "pairAddress": "0xpool",
        "tokenSide": "base",
        "t0Status": "verified_in_supported_scope",
        "ageDays": age,
        "observedBuys": 1,
        "observedSells": 1,
        "confirmedHardBlock": False,
    }
    item.update(changes)
    return item


def public_fixture(**changes):
    item = first_gate_fixture()
    item.update({
        "deepTrackingState": "completed",
        "evaluationWindowId": "window-1",
        "evaluationCompletedAt": "2026-08-13T00:00:00Z",
        "riskState": "success",
        "severeAnomaly": False,
        "sellQuoteState": "success",
        "sellQuoteLossPct": 15,
        "projectEvidenceQualified": True,
        "projectEvidenceAttributable": True,
        "relationshipClass": "C",
    })
    item.update(changes)
    return item


class C24FirstGateTests(unittest.TestCase):
    def test_age_boundaries(self):
        self.assertTrue(evaluate_first_gate(first_gate_fixture(age=0))["passed"])
        self.assertTrue(evaluate_first_gate(first_gate_fixture(age=90))["passed"])
        self.assertFalse(evaluate_first_gate(first_gate_fixture(age=91))["passed"])

    def test_relationship_evidence_and_market_amount_do_not_control_first_gate(self):
        for relationship in ("A", "B", "C", "D"):
            item = first_gate_fixture(
                relationshipClass=relationship,
                projectEvidenceQualified=False,
                liquidityUsd=0,
                volumeUsd=0,
                sellQuoteState="no_data",
                riskState="no_data",
                bayesPosterior=None,
            )
            self.assertTrue(evaluate_first_gate(item)["passed"])

    def test_unknown_risk_passes_but_confirmed_block_fails(self):
        self.assertTrue(evaluate_first_gate(first_gate_fixture(riskState="no_data"))["passed"])
        self.assertFalse(evaluate_first_gate(first_gate_fixture(confirmedHardBlock=True))["passed"])

    def test_identity_or_two_way_trade_missing_fails(self):
        self.assertFalse(evaluate_first_gate(first_gate_fixture(pairAddress=""))["passed"])
        self.assertFalse(evaluate_first_gate(first_gate_fixture(observedSells=0))["passed"])


class C24PublicBaselineTests(unittest.TestCase):
    def test_successful_quote_is_public_regardless_of_loss_percentage(self):
        result = evaluate_public_baseline(public_fixture(sellQuoteLossPct=35))
        self.assertTrue(result["passed"])
        self.assertEqual(result["trackingState"], "complete_tracking")

    def test_unknown_risk_source_does_not_block_without_confirmed_trade_block(self):
        result = evaluate_public_baseline(public_fixture(riskState="no_data"))
        self.assertTrue(result["passed"])

    def test_missing_is_not_zero_or_public(self):
        result = evaluate_public_baseline(public_fixture(sellQuoteState="no_data", sellQuoteLossPct=None))
        self.assertFalse(result["passed"])
        self.assertEqual(result["trackingState"], "waiting_public_baseline")

    def test_d_is_backend_only(self):
        result = evaluate_public_baseline(public_fixture(relationshipClass="D"))
        self.assertFalse(result["passed"])


class C24PathAndStateTests(unittest.TestCase):
    def complete_path_fixture(self, **changes):
        item = public_fixture(
            ageDays=10,
            observedBuys=5,
            observedSells=3,
            volumeUsd=1000,
            transactionCount=30,
            volumeLiquidityRatio=0.2,
            liquidityUsd=20000,
            sellQuoteLossPct=5,
            sellQuoteIndependent=True,
            liquidityDropPct=10,
            supplyHistoryState="success",
            supplyUnitScaleStable=True,
            top10ShareChangePercentagePoints=-3,
            holderHhiChangePct=-1,
            supplyChangePct=0,
            poolHistoryState="success",
            indexedPoolCount=2,
            ohlcvSuccessCount=2,
            unindexedDiscoveredPoolCount=1,
            relativeExpansion=1,
            riskAdjustedSurplus=0.5,
        )
        item.update(changes)
        return item

    def test_four_paths_recompute_and_clue_requires_independent_sources(self):
        item = self.complete_path_fixture(publicEligible=True)
        paths = evaluate_strong_paths(item)
        self.assertEqual([row["status"] for row in paths], ["formed"] * 4)
        state = determine_public_state(item, paths)
        self.assertEqual(state["publicState"], "convexity_clue")

    def test_market_double_counting_guard(self):
        item = self.complete_path_fixture(
            publicEligible=True,
            sellQuoteIndependent=False,
            supplyHistoryState="no_data",
            poolHistoryState="no_data",
        )
        paths = evaluate_strong_paths(item)
        self.assertNotEqual(determine_public_state(item, paths)["publicState"], "convexity_clue")

    def test_one_path_is_active_and_zero_paths_is_observing(self):
        item = self.complete_path_fixture(publicEligible=True, sellQuoteState="no_data", liquidityDropPct=None, supplyHistoryState="no_data", poolHistoryState="no_data")
        paths = evaluate_strong_paths(item)
        self.assertEqual(determine_public_state(item, paths)["publicState"], "active_project")
        none = [{**row, "status": "not_formed"} for row in paths]
        self.assertEqual(determine_public_state(item, none)["publicState"], "observing")

    def test_dynamic_cohort_thresholds_override_fallback_and_keep_real_zero(self):
        item = self.complete_path_fixture(
            volumeUsd=0,
            transactionCount=0,
            volumeLiquidityRatio=0,
            cohortThresholds={
                "volumeP40": 0,
                "volumeP50": 0,
                "transactionsP50": 0,
                "volumeLiquidityRatioP50": 0,
                "liquidityP50": 25000,
                "relativeExpansionP50": 2,
            },
        )
        paths = evaluate_strong_paths(item)
        self.assertEqual(paths[0]["status"], "formed")
        self.assertEqual(paths[0]["metrics"]["volumeP50"], 0)
        self.assertEqual(paths[1]["status"], "formed")
        self.assertEqual(paths[3]["status"], "not_formed")

    def test_missing_supply_is_unavailable_but_unit_change_no_longer_invalidates(self):
        current_only = evaluate_strong_paths(self.complete_path_fixture(supplyHistoryState="no_data"))[2]
        unit_changed = evaluate_strong_paths(self.complete_path_fixture(supplyUnitScaleStable=False))[2]
        self.assertEqual(current_only["status"], "unavailable")
        self.assertEqual(unit_changed["status"], "formed")

    def test_successful_quote_forms_exit_path_without_percentage_thresholds(self):
        path = evaluate_strong_paths(self.complete_path_fixture(liquidityUsd=0, sellQuoteLossPct=99, liquidityDropPct=100))[1]
        self.assertEqual(path["status"], "formed")

    def test_explicit_frozen_version_restores_path_thresholds_and_unit_guard(self):
        paths = evaluate_strong_paths(
            self.complete_path_fixture(liquidityUsd=0, sellQuoteLossPct=99, liquidityDropPct=100, supplyUnitScaleStable=False),
            active_version="c2.4-rules-v1",
        )
        self.assertEqual(paths[1]["status"], "not_formed")
        self.assertEqual(paths[2]["status"], "not_formed")
        self.assertEqual(paths[3]["status"], "not_formed")

    def test_unindexed_pool_count_is_disclosed_but_does_not_reject_indexed_path(self):
        path = evaluate_strong_paths(self.complete_path_fixture(unindexedDiscoveredPoolCount=7))[3]
        self.assertEqual(path["status"], "formed")
        self.assertEqual(path["metrics"]["unindexedDiscoveredPoolCount"], 7)


class C24LifecycleRankingTests(unittest.TestCase):
    def test_day_91_requires_all_new_period_history(self):
        base = {
            "ageDays": 91,
            "firstGatePassedWhileNew": True,
            "completeTrackingWhileNew": True,
            "publicBaselinePassedWhileNew": True,
            "stableIdentityStillValid": True,
            "confirmedHardBlock": False,
            "severeAnomaly": False,
        }
        self.assertEqual(lifecycle_pool(base)["lifecyclePool"], "continued_91_plus")
        self.assertFalse(lifecycle_pool({**base, "publicBaselinePassedWhileNew": False})["eligible"])

    def test_home_top10_does_not_delete_rank_11_or_unranked_public(self):
        items = []
        for index in range(12):
            items.append({
                "assetId": f"asset-{index:02d}",
                "chainId": "base-mainnet",
                "publicEligible": True,
                "bayesPosterior": 100 - index,
                "independentConfidence": 80,
                "observedMetricCount": 10,
                "latestCompleteTrackingAt": f"2026-08-13T00:{index:02d}:00Z",
                "bayesFactors": [{"score": 50, "measuredIndicatorCount": 1}] * 5,
            })
        unranked = {"assetId": "asset-unranked", "chainId": "base-mainnet", "publicEligible": True, "bayesPosterior": None, "bayesFactors": []}
        all_items = items + [unranked]
        home = rank_home_by_chain(all_items)
        self.assertEqual(len(home["base-mainnet"]), 10)
        self.assertEqual(len(all_items), 13)
        self.assertEqual(items[10]["bayesRankWithinChain"], 11)
        self.assertFalse(unranked["rankingAvailable"])

    def test_ranking_never_compares_chains(self):
        rows = []
        for chain in ("base-mainnet", "ethereum-mainnet"):
            rows.append({"assetId": chain, "chainId": chain, "publicEligible": True, "bayesPosterior": 50, "independentConfidence": 50, "observedMetricCount": 5, "latestCompleteTrackingAt": "2026-08-13T00:00:00Z", "bayesFactors": [{"score": 50, "measuredIndicatorCount": 1}] * 5})
        home = rank_home_by_chain(rows)
        self.assertEqual(set(home), {"base-mainnet", "ethereum-mainnet"})
        self.assertTrue(all(group[0]["bayesRankWithinChain"] == 1 for group in home.values()))

    def test_ranking_final_tie_break_is_stable_asset_id(self):
        rows = [{
            "assetId": asset_id, "chainId": "base-mainnet", "publicEligible": True,
            "bayesPosterior": 50, "independentConfidence": 50, "observedMetricCount": 5,
            "latestCompleteTrackingAt": "2026-08-13T00:00:00Z",
            "bayesFactors": [{"score": 50, "measuredIndicatorCount": 1}] * 5,
        } for asset_id in ("asset-b", "asset-a")]
        ranked = rank_home_by_chain(rows)["base-mainnet"]
        self.assertEqual([row["assetId"] for row in ranked], ["asset-a", "asset-b"])

    def test_neutral_default_scores_without_measured_evidence_are_not_ranked(self):
        item = {
            "assetId": "asset-neutral", "chainId": "base-mainnet", "publicEligible": True,
            "bayesPosterior": 50, "independentConfidence": 50, "observedMetricCount": 0,
            "latestCompleteTrackingAt": "2026-08-13T00:00:00Z",
            "bayesFactors": [{"score": 50, "measuredIndicatorCount": 0}] * 5,
        }
        self.assertEqual(rank_home_by_chain([item]), {})
        self.assertFalse(item["rankingAvailable"])

    def test_complete_five_factor_posterior_with_partial_real_evidence_is_ranked(self):
        item = {
            "assetId": "asset-partial", "chainId": "solana-mainnet", "publicEligible": True,
            "bayesPosterior": 61, "independentConfidence": 45, "observedMetricCount": 8,
            "latestCompleteTrackingAt": "2026-08-13T00:00:00Z",
            "bayesFactors": [
                {"factor": "D", "score": 70, "measuredIndicatorCount": 4},
                {"factor": "L", "score": 58, "measuredIndicatorCount": 2},
                {"factor": "S", "score": 50, "measuredIndicatorCount": 0},
                {"factor": "G", "score": 50, "measuredIndicatorCount": 0},
                {"factor": "Q", "score": 55, "measuredIndicatorCount": 2},
            ],
        }
        home = rank_home_by_chain([item])
        self.assertEqual([row["assetId"] for row in home["solana-mainnet"]], ["asset-partial"])
        self.assertTrue(item["rankingAvailable"])

    def test_exit_hysteresis_counts_distinct_complete_windows_only(self):
        first = normal_exit_decision({}, "window-1", True)
        repeat = normal_exit_decision({"consecutiveCompletedMisses": 1, "lastExitWindowId": "window-1"}, "window-1", True)
        second = normal_exit_decision({"consecutiveCompletedMisses": 1, "lastExitWindowId": "window-1"}, "window-2", True)
        loss_only = normal_exit_decision({"sellQuoteLossPct": 20}, "window-1", False)
        immediate = normal_exit_decision({"confirmedHardBlock": True}, "window-1", False)
        self.assertEqual(first["consecutiveMisses"], 1)
        self.assertEqual(repeat["consecutiveMisses"], 1)
        self.assertTrue(second["exit"])
        self.assertFalse(loss_only["immediate"])
        self.assertTrue(immediate["immediate"])

    def test_explicit_frozen_version_restores_quote_loss_immediate_exit(self):
        trial = normal_exit_decision({"sellQuoteLossPct": 20}, "window-1", False)
        frozen = normal_exit_decision({"sellQuoteLossPct": 20}, "window-1", False, active_version="c2.4-rules-v1")
        self.assertFalse(trial["exit"])
        self.assertTrue(frozen["exit"])
        self.assertTrue(frozen["immediate"])


if __name__ == "__main__":
    unittest.main()
