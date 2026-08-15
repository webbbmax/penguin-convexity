#!/usr/bin/env python3
"""C2.4 frozen deterministic screening, publication and ranking rules."""

from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULE_PATH = PROJECT_ROOT / "docs" / "C2.4_RULE_CONFIG.json"
DEFAULT_TRIAL_PATH = PROJECT_ROOT / "docs" / "C2.4_RULE_RELAXATION_TRIAL_20260813.json"
DEFAULT_ACTIVE_RULE_PATH = PROJECT_ROOT / "runtime" / "c2.5" / "rule-governance" / "current.json"
FROZEN_PUBLIC_RULE_VERSION = "c2.4-rules-v1"
TRIAL_PUBLIC_RULE_VERSION = "c2.4-public-baseline-quote-success-trial-v1"
EXPECTED_RULE_SHA256 = "775f9fad44e5f0db3b036e797643104a5ff9f075afbc4e1c16835606c8a88988"
EXPECTED_TRIAL_SHA256 = "7f6ccc9e35ab6ba7b5212911116facd9698489c0f7d0f27b9dbcf16dc0c7e202"
PUBLIC_STATES = ("convexity_clue", "active_project", "observing")
DATA_STATES = {
    "success",
    "no_data",
    "quota_limited",
    "source_failure",
    "unsupported",
    "configuration_missing",
    "program_failure",
}


def load_config(path: Path = DEFAULT_RULE_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def age_band(age_days: Any) -> str | None:
    value = number(age_days)
    if value is None or value < 0:
        return None
    if value <= 2:
        return "age_0_2"
    if value <= 6:
        return "age_3_6"
    if value <= 13:
        return "age_7_13"
    if value <= 30:
        return "age_14_30"
    return "age_31_90"


def _check(code: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), "reason": reason}


def evaluate_first_gate(item: dict[str, Any]) -> dict[str, Any]:
    """Apply only the four frozen first-gate checks; unknown risk is not failure."""

    age = number(item.get("ageDays"))
    t0_ok = item.get("t0Status") == "verified_in_supported_scope" and age is not None and 0 <= age <= 90
    identity_ok = all(
        bool(item.get(key))
        for key in ("chainId", "contractAddress", "pairAddress", "assetId")
    ) and item.get("tokenSide") in {"base", "quote"}
    buy_sell_ok = (number(item.get("observedBuys")) or 0) >= 1 and (number(item.get("observedSells")) or 0) >= 1
    hard_block = bool(
        item.get("confirmedHardBlock")
        or item.get("confirmedFreeze")
        or item.get("confirmedBlacklist")
        or item.get("confirmedSellBlock")
    )
    checks = [
        _check("t0_age", t0_ok, "T0已核验且当前处于0—90天。" if t0_ok else "T0未核验或已不在0—90天当前候选窗口。"),
        _check("stable_asset_identity", identity_ok, "链、合约、交易池、资产方向和稳定assetId完整。" if identity_ok else "链、合约、交易池、资产方向或稳定assetId仍有缺口。"),
        _check("public_buy_and_sell", buy_sell_ok, "公开市场已观察到至少1笔买入和1笔卖出。" if buy_sell_ok else "尚未同时观察到公开买入和卖出。"),
        _check("no_confirmed_trade_block", not hard_block, "没有已确认的冻结、黑名单或卖出阻断；未知风险进入第二关继续核验。" if not hard_block else "已确认冻结、黑名单或卖出阻断。"),
    ]
    return {
        "passed": all(row["passed"] for row in checks),
        "state": "passed" if all(row["passed"] for row in checks) else "not_passed",
        "checks": checks,
        "ruleVersion": "c2.4-first-gate-v1",
    }


def _canonical_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_active_rule_version(selector_path: Path | None = None) -> str:
    """Read the C2.5-approved selector; absence preserves the released trial."""

    path = Path(selector_path or DEFAULT_ACTIVE_RULE_PATH)
    if not path.exists():
        return TRIAL_PUBLIC_RULE_VERSION
    value = json.loads(path.read_text(encoding="utf-8"))
    version = value.get("activeVersion") if isinstance(value, dict) else None
    if version not in {FROZEN_PUBLIC_RULE_VERSION, TRIAL_PUBLIC_RULE_VERSION}:
        raise ValueError("C2.5规则选择器包含未知版本，拒绝生成新快照。")
    hashes = value.get("sourceHashes") if isinstance(value.get("sourceHashes"), dict) else {}
    if (
        hashes.get("ruleConfig") != EXPECTED_RULE_SHA256
        or hashes.get("trial") != EXPECTED_TRIAL_SHA256
        or _canonical_sha256(DEFAULT_RULE_PATH) != EXPECTED_RULE_SHA256
        or _canonical_sha256(DEFAULT_TRIAL_PATH) != EXPECTED_TRIAL_SHA256
    ):
        raise ValueError("C2.5规则选择器与冻结来源哈希不一致，拒绝生成新快照。")
    return str(version)


def _evaluate_trial_public_baseline(item: dict[str, Any]) -> dict[str, Any]:
    """Apply the user-authorized post-release quote-success-only baseline."""

    complete = item.get("deepTrackingState") == "completed" and bool(item.get("evaluationWindowId")) and bool(item.get("evaluationCompletedAt"))
    risk_ok = not any(
        bool(item.get(key))
        for key in ("confirmedHardBlock", "confirmedFreeze", "confirmedBlacklist", "confirmedSellBlock")
    )
    quote_ok = item.get("sellQuoteState") == "success"
    evidence_ok = bool(item.get("projectEvidenceQualified")) and bool(item.get("projectEvidenceAttributable"))
    identity_ok = all(bool(item.get(key)) for key in ("assetId", "chainId", "contractAddress", "pairAddress")) and item.get("tokenSide") in {"base", "quote"} and item.get("t0Status") == "verified_in_supported_scope"
    class_ok = item.get("relationshipClass") in {"A", "B", "C"}
    checks = [
        _check("complete_deep_result", complete, "本轮首轮基础跟踪已完整结束。" if complete else "还没有新的完整首轮基础跟踪结果。"),
        _check("risk_complete", risk_ok, "没有已确认的冻结、黑名单或卖出阻断。" if risk_ok else "发现已确认的冻结、黑名单或卖出阻断。"),
        _check("sell_quote", quote_ok, "100美元标准卖出报价成功；损失比例只记录，不作为当前门槛。" if quote_ok else "100美元标准卖出报价未成功。"),
        _check("project_evidence", evidence_ok, "至少一项项目证据可程序归属。" if evidence_ok else "缺少可程序归属的项目证据。"),
        _check("stable_identity", identity_ok, "T0、链、合约、交易池、方向和assetId仍然有效。" if identity_ok else "T0或稳定资产身份已经不完整。"),
        _check("public_relationship", class_ok, "项目关系属于A/B/C，可进入公开判断。" if class_ok else "D类只在后台补证，不能公开。"),
    ]
    passed = all(row["passed"] for row in checks)
    if not risk_ok:
        backend_state = "stopped_active_tracking"
    elif item.get("deepTrackingState") == "partial":
        backend_state = "waiting_source_retry"
    elif passed:
        backend_state = "complete_tracking"
    else:
        backend_state = "waiting_public_baseline"
    return {
        "passed": passed,
        "publicEligible": passed,
        "checks": checks,
        "trackingState": backend_state,
        "ruleVersion": TRIAL_PUBLIC_RULE_VERSION,
    }


def _evaluate_frozen_public_baseline(item: dict[str, Any]) -> dict[str, Any]:
    trial = _evaluate_trial_public_baseline(item)
    risk_state = item.get("riskSourceState") or item.get("riskState")
    risk_source_success = risk_state in {"success", "complete", "completed"}
    loss = number(item.get("sellQuoteLossPct"))
    quote_threshold_passed = item.get("sellQuoteState") == "success" and loss is not None and loss <= 15
    severe = any(
        bool(item.get(key))
        for key in (
            "confirmedHardBlock",
            "confirmedFreeze",
            "confirmedBlacklist",
            "confirmedSellBlock",
            "confirmedSevereAnomaly",
        )
    ) or (loss is not None and loss >= 20) or ((number(item.get("sellTaxPct")) or 0) >= 20)
    checks = [
        *trial["checks"],
        _check("risk_source_success", risk_source_success, "风险来源成功返回。" if risk_source_success else "风险来源没有成功返回，不能把未知当安全。"),
        _check("sell_quote_loss_lte_15", quote_threshold_passed, "100美元标准卖出报价损失不高于15%。" if quote_threshold_passed else "卖出报价损失未知或高于15%。"),
        _check("no_severe_anomaly", not severe, "没有已确认严重异常。" if not severe else "存在已确认严重异常。"),
    ]
    passed = all(row["passed"] for row in checks)
    return {
        "passed": passed,
        "publicEligible": passed,
        "checks": checks,
        "trackingState": "complete_tracking" if passed else "waiting_public_baseline",
        "ruleVersion": FROZEN_PUBLIC_RULE_VERSION,
    }


def evaluate_public_baseline_version(item: dict[str, Any], version: str) -> dict[str, Any]:
    if version == TRIAL_PUBLIC_RULE_VERSION:
        return _evaluate_trial_public_baseline(item)
    if version == FROZEN_PUBLIC_RULE_VERSION:
        return _evaluate_frozen_public_baseline(item)
    raise ValueError(f"未知规则版本：{version}")


def evaluate_public_baseline(
    item: dict[str, Any],
    *,
    selector_path: Path | None = None,
    active_version: str | None = None,
) -> dict[str, Any]:
    """Apply the explicitly selected approved version on the next legal run."""

    return evaluate_public_baseline_version(item, active_version or load_active_rule_version(selector_path))


def effective_rule_manifest(version: str) -> dict[str, Any]:
    if version not in {FROZEN_PUBLIC_RULE_VERSION, TRIAL_PUBLIC_RULE_VERSION}:
        raise ValueError(f"未知规则版本：{version}")
    if version == FROZEN_PUBLIC_RULE_VERSION:
        return {
            "public_eligibility_result": "all_frozen_public_baseline_checks",
            "public_risk_source_success": "required",
            "public_no_confirmed_hard_block": "required",
            "public_no_confirmed_severe_anomaly": "required",
            "strong_path_trade_demand_state": "frozen_path_conditions",
            "strong_path_liquidity_exit_state": "frozen_path_conditions",
            "strong_path_supply_holder_state": "frozen_path_conditions",
            "strong_path_indexed_pool_state": "frozen_path_conditions",
            "immediate_exit_state": "hard_block_or_loss_gte_20_or_sell_tax_gte_20",
            "public_sell_quote_loss": 15,
            "strong_path_sell_quote_loss": 10,
            "severe_immediate_exit_loss": 20,
            "sell_quote_loss_pct_lte_10_or_15": "enabled",
            "sell_quote_loss_pct_gte_20_immediate_exit": "enabled",
            "liquidity_drop_pct_gte_80_path_invalidation": "enabled",
            "supply_decimals_or_unit_change_path_invalidation": "enabled",
            "cross_source_price_deviation_pct_gte_25_path_pause": "enabled",
            "sell_tax_pct_gte_20_as_hard_block": "enabled",
        }
    return {
        "public_eligibility_result": "approved_trial_public_baseline_checks",
        "public_risk_source_success": "not_required_raw_state_preserved",
        "public_no_confirmed_hard_block": "required",
        "public_no_confirmed_severe_anomaly": "not_required_raw_state_preserved",
        "strong_path_trade_demand_state": "approved_trial_path_conditions",
        "strong_path_liquidity_exit_state": "approved_trial_path_conditions",
        "strong_path_supply_holder_state": "approved_trial_path_conditions",
        "strong_path_indexed_pool_state": "approved_trial_path_conditions",
        "immediate_exit_state": "confirmed_trade_block_only",
        "public_sell_quote_loss": "quote_success_loss_recorded",
        "strong_path_sell_quote_loss": "quote_success_no_confirmed_trade_block",
        "severe_immediate_exit_loss": "record_only_no_immediate_exit_gate",
        "sell_quote_loss_pct_lte_10_or_15": "disabled_as_gate",
        "sell_quote_loss_pct_gte_20_immediate_exit": "disabled_as_gate",
        "liquidity_drop_pct_gte_80_path_invalidation": "disabled_as_gate",
        "supply_decimals_or_unit_change_path_invalidation": "disabled_as_gate",
        "cross_source_price_deviation_pct_gte_25_path_pause": "disabled_as_gate",
        "sell_tax_pct_gte_20_as_hard_block": "disabled_as_gate",
    }


def evaluate_rule_condition(rule_id: str, item: dict[str, Any], version: str) -> dict[str, Any]:
    """Evaluate one rule independently so rule-level impact is not copied globally."""

    trial = version == TRIAL_PUBLIC_RULE_VERSION
    loss = number(item.get("sellQuoteLossPct"))
    quote_known = item.get("sellQuoteState") is not None
    hard_block = any(bool(item.get(key)) for key in ("confirmedHardBlock", "confirmedFreeze", "confirmedBlacklist", "confirmedSellBlock"))
    if rule_id == "public_sell_quote_loss":
        return {"applicable": quote_known, "passed": item.get("sellQuoteState") == "success" and (trial or (loss is not None and loss <= 15))}
    if rule_id == "strong_path_sell_quote_loss":
        return {"applicable": quote_known, "passed": item.get("sellQuoteState") == "success" and not hard_block and (trial or (loss is not None and loss <= 10))}
    if rule_id in {"severe_immediate_exit_loss", "sell_quote_loss_pct_gte_20_immediate_exit"}:
        return {"applicable": loss is not None, "passed": bool(trial or (loss is not None and loss < 20))}
    if rule_id == "sell_quote_loss_pct_lte_10_or_15":
        return {"applicable": quote_known, "passed": item.get("sellQuoteState") == "success" and (trial or (loss is not None and loss <= 15))}
    if rule_id == "liquidity_drop_pct_gte_80_path_invalidation":
        value = number(item.get("liquidityDropPct"))
        return {"applicable": value is not None, "passed": bool(trial or (value is not None and value < 80))}
    if rule_id == "supply_decimals_or_unit_change_path_invalidation":
        keys = ("supplyUnitScaleChanged", "supplyDecimalsChanged", "supplyUnitChanged")
        applicable = any(key in item for key in keys) or item.get("supplyUnitScaleStable") is not None
        changed = any(bool(item.get(key)) for key in keys) or item.get("supplyUnitScaleStable") is False
        return {"applicable": applicable, "passed": bool(trial or not changed)}
    if rule_id == "cross_source_price_deviation_pct_gte_25_path_pause":
        value = number(item.get("crossSourcePriceDeviationPct"))
        return {"applicable": value is not None, "passed": bool(trial or (value is not None and value < 25))}
    if rule_id == "sell_tax_pct_gte_20_as_hard_block":
        value = number(item.get("sellTaxPct"))
        return {"applicable": value is not None, "passed": bool(trial or (value is not None and value < 20))}
    raise ValueError(f"未知规则：{rule_id}")


def _thresholds(item: dict[str, Any], config: dict[str, Any]) -> dict[str, float]:
    band = age_band(item.get("ageDays")) or "age_31_90"
    frozen = config["ageBands"][band]
    actual = item.get("cohortThresholds") or {}
    p40 = frozen["fallbackP40"]
    p50 = frozen["fallbackP50"]
    def selected(name: str, fallback: Any) -> float:
        value = number(actual.get(name))
        return number(fallback) or 0 if value is None else value

    return {
        "liquidityFloor": number(frozen.get("liquidityFloorUsd")) or 0,
        "liquidityP50": selected("liquidityP50", p50.get("liquidityUsd")),
        "volumeP40": selected("volumeP40", p40.get("volumeUsd")),
        "volumeP50": selected("volumeP50", p50.get("volumeUsd")),
        "transactionsP50": selected("transactionsP50", p50.get("transactions")),
        "volumeLiquidityP50": selected("volumeLiquidityRatioP50", p50.get("volumeLiquidityRatio")),
        "relativeExpansionP50": selected("relativeExpansionP50", p50.get("relativeExpansion")),
    }


def _path(code: str, status: str, reason: str, source_types: list[str], metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "pathCode": code,
        "status": status,
        "plainReason": reason,
        "independentSourceTypes": sorted({value for value in source_types if value}),
        "metrics": metrics,
    }


def evaluate_strong_paths(
    item: dict[str, Any],
    config: dict[str, Any] | None = None,
    *,
    active_version: str | None = None,
    selector_path: Path | None = None,
) -> list[dict[str, Any]]:
    config = config or load_config()
    version = active_version or load_active_rule_version(selector_path)
    trial = version == TRIAL_PUBLIC_RULE_VERSION
    threshold = _thresholds(item, config)
    buys = number(item.get("observedBuys"))
    sells = number(item.get("observedSells"))
    volume = number(item.get("volumeUsd"))
    transactions = number(item.get("transactionCount"))
    ratio = number(item.get("volumeLiquidityRatio"))
    demand_ready = buys is not None and sells is not None and any(value is not None for value in (volume, transactions, ratio))
    demand_formed = (
        demand_ready
        and buys >= 1
        and sells >= 1
        and any((value is not None and value >= minimum) for value, minimum in (
            (volume, threshold["volumeP50"]),
            (transactions, threshold["transactionsP50"]),
            (ratio, threshold["volumeLiquidityP50"]),
        ))
        and (trial or not item.get("materialCrossSourceConflict"))
        and (trial or (number(item.get("crossSourcePriceDeviationPct")) or 0) < 25)
    )
    demand = _path(
        "trade_demand_formation",
        "formed" if demand_formed else "not_formed" if demand_ready else "unavailable",
        "公开买卖已形成，且至少一项交易指标达到同组P50。" if demand_formed else "交易历史完整但尚未达到同组P50。" if demand_ready else "交易指标不足，暂不能判断。",
        ["market_pool_data"],
        {"buys": buys, "sells": sells, "volumeUsd": volume, "transactionCount": transactions, "volumeLiquidityRatio": ratio, "volumeP50": threshold["volumeP50"], "transactionsP50": threshold["transactionsP50"], "volumeLiquidityRatioP50": threshold["volumeLiquidityP50"]},
    )

    liquidity = number(item.get("liquidityUsd"))
    quote_loss = number(item.get("sellQuoteLossPct"))
    liquidity_drop = number(item.get("liquidityDropPct"))
    exit_ready = item.get("sellQuoteState") == "success"
    strict_exit_inputs_ready = all(value is not None for value in (liquidity, quote_loss, liquidity_drop))
    exit_formed = (
        exit_ready
        and not item.get("confirmedHardBlock")
        and not item.get("confirmedSellBlock")
        and (trial or strict_exit_inputs_ready)
        and (trial or liquidity >= max(threshold["liquidityP50"], threshold["liquidityFloor"]))
        and (trial or quote_loss <= 10)
        and (trial or liquidity_drop < 80)
        and (trial or (number(item.get("crossSourcePriceDeviationPct")) or 0) < 25)
    )
    exit_observable = exit_ready and (trial or strict_exit_inputs_ready)
    exit_path = _path(
        "liquidity_exit_quality",
        "formed" if exit_formed else "not_formed" if exit_observable else "unavailable",
        "100美元标准卖出报价成功，且当前有效版本要求的流动性、损失与异常条件均通过。" if exit_formed else "当前有效版本要求的退出质量条件未全部通过。" if exit_observable else "当前有效版本所需的卖出报价或比较窗口尚不可用。",
        ["market_pool_data", "sell_quote_or_verified_route" if item.get("sellQuoteIndependent") else ""],
        {"liquidityUsd": liquidity, "liquidityP50": threshold["liquidityP50"], "liquidityFloorUsd": threshold["liquidityFloor"], "sellQuoteLossPct": quote_loss, "liquidityDropPct": liquidity_drop},
    )

    top10_change = number(item.get("top10ShareChangePercentagePoints"))
    hhi_change = number(item.get("holderHhiChangePct"))
    supply_change = number(item.get("supplyChangePct"))
    unit_stable = item.get("supplyUnitScaleStable")
    supply_ready = item.get("supplyHistoryState") == "success" and volume is not None and (trial or unit_stable is not None)
    supply_formed = supply_ready and volume >= threshold["volumeP40"] and any((
        top10_change is not None and top10_change <= -2,
        hhi_change is not None and hhi_change <= -5,
        supply_change is not None and supply_change <= -0.25,
    )) and (trial or unit_stable is True)
    supply_path = _path(
        "supply_holder_improvement",
        "formed" if supply_formed else "not_formed" if supply_ready else "unavailable",
        "供应或持币结构达到改善阈值，且当前成交额不低于同组P40。" if supply_formed else "供应历史可比，但尚未达到改善阈值。" if supply_ready else "当前还没有两个可换算的真实供应窗口。",
        ["direct_chain_historical_supply"],
        {"top10ShareChangePercentagePoints": top10_change, "holderHhiChangePct": hhi_change, "supplyChangePct": supply_change, "volumeUsd": volume, "volumeP40": threshold["volumeP40"]},
    )

    relative = number(item.get("relativeExpansion"))
    surplus = number(item.get("riskAdjustedSurplus"))
    indexed = number(item.get("indexedPoolCount"))
    ohlcv = number(item.get("ohlcvSuccessCount"))
    pool_ready = (
        item.get("poolHistoryState") == "success"
        and indexed is not None
        and ohlcv is not None
        and indexed == ohlcv
        and item.get("supplyHistoryState") == "success"
        and relative is not None
        and surplus is not None
        and (trial or unit_stable is not None)
    )
    pool_formed = pool_ready and relative >= threshold["relativeExpansionP50"] and surplus > 0 and (trial or unit_stable is True) and (trial or not item.get("severeAnomaly"))
    pool_path = _path(
        "indexed_pool_activity_vs_supply_adjusted_valuation",
        "formed" if pool_formed else "not_formed" if pool_ready else "unavailable",
        "提供方已索引池活动相对扩张达到同组P50，且风险调整剩余为正。" if pool_formed else "池与供应历史可比，但相对扩张尚未达到要求。" if pool_ready else "已索引池或供应历史尚不完整。",
        ["indexed_pool_history", "direct_chain_historical_supply"],
        {"relativeExpansion": relative, "relativeExpansionP50": threshold["relativeExpansionP50"], "riskAdjustedSurplus": surplus, "indexedPoolCount": indexed, "unindexedDiscoveredPoolCount": number(item.get("unindexedDiscoveredPoolCount"))},
    )
    return [demand, exit_path, supply_path, pool_path]


def determine_public_state(item: dict[str, Any], paths: list[dict[str, Any]]) -> dict[str, Any]:
    if not item.get("publicEligible"):
        return {"publicState": None, "convexityClue": False, "formedPathCount": 0}
    formed = [row for row in paths if row.get("status") == "formed"]
    codes = {row["pathCode"] for row in formed}
    source_types = {source for row in formed for source in row.get("independentSourceTypes", [])}
    market_pair_only = codes == {"trade_demand_formation", "liquidity_exit_quality"}
    independent_market_exit = "sell_quote_or_verified_route" in source_types
    clue = (
        len(formed) >= 2
        and bool(codes & {"trade_demand_formation", "liquidity_exit_quality"})
        and len(source_types) >= 2
        and (not market_pair_only or independent_market_exit)
    )
    active = bool(formed) or bool(item.get("recentQualifyingRepositoryActivity")) or bool(item.get("newVerifiedProductUsage"))
    state = "convexity_clue" if clue else "active_project" if active else "observing"
    return {
        "publicState": state,
        "convexityClue": clue,
        "formedPathCount": len(formed),
        "formedPathCodes": sorted(codes),
        "independentSourceTypes": sorted(source_types),
    }


def lifecycle_pool(item: dict[str, Any]) -> dict[str, Any]:
    age = number(item.get("ageDays"))
    if age is not None and 0 <= age <= 90:
        return {"eligible": True, "lifecyclePool": "new_0_90", "reason": "当前仍处于0—90天候选窗口。"}
    required = all((
        item.get("firstGatePassedWhileNew"),
        item.get("completeTrackingWhileNew"),
        item.get("publicBaselinePassedWhileNew"),
        item.get("stableIdentityStillValid"),
        not item.get("confirmedHardBlock"),
    ))
    return {
        "eligible": required,
        "lifecyclePool": "continued_91_plus" if required else None,
        "reason": "同一资产已从90天候选转入持续跟踪。" if required else "没有0—90天期间完成两关并达到公开底线的历史，不能补入老项目。",
    }


def _bayes_complete(item: dict[str, Any]) -> bool:
    factors = item.get("bayesFactors") or []
    measured_counts = [
        int(number(row.get("measuredIndicatorCount")) or 0)
        for row in factors
    ]
    return (
        number(item.get("bayesPosterior")) is not None
        and len(factors) == 5
        and all(number(row.get("score")) is not None for row in factors)
        and sum(measured_counts) > 0
        and int(number(item.get("observedMetricCount")) or 0) > 0
    )


def rank_home_by_chain(items: list[dict[str, Any]], maximum: int = 10) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        item["rankingAvailable"] = bool(item.get("publicEligible") and _bayes_complete(item))
        if item["rankingAvailable"]:
            groups.setdefault(str(item.get("chainId") or ""), []).append(item)
    output: dict[str, list[dict[str, Any]]] = {}
    for chain, rows in groups.items():
        rows.sort(key=lambda row: (
            -(number(row.get("bayesPosterior")) or 0),
            -(number(row.get("independentConfidence")) or 0),
            -int(number(row.get("observedMetricCount")) or 0),
            str(row.get("latestCompleteTrackingAt") or ""),
            str(row.get("assetId") or ""),
        ))
        # ISO times sort ascending above; invert without parsing by applying a stable
        # second pass before the numeric keys.
        rows.sort(key=lambda row: str(row.get("latestCompleteTrackingAt") or ""), reverse=True)
        rows.sort(key=lambda row: (
            -(number(row.get("bayesPosterior")) or 0),
            -(number(row.get("independentConfidence")) or 0),
            -int(number(row.get("observedMetricCount")) or 0),
        ))
        for rank, row in enumerate(rows, 1):
            row["bayesRankWithinChain"] = rank
        output[chain] = rows[:maximum]
    return output


def normal_exit_decision(
    item: dict[str, Any],
    new_window_id: str,
    below_exit_rule: bool,
    *,
    active_version: str | None = None,
    selector_path: Path | None = None,
) -> dict[str, Any]:
    version = active_version or load_active_rule_version(selector_path)
    loss = number(item.get("sellQuoteLossPct"))
    sell_tax = number(item.get("sellTaxPct"))
    immediate = any(
        bool(item.get(key))
        for key in ("confirmedHardBlock", "confirmedFreeze", "confirmedBlacklist", "confirmedSellBlock")
    ) or (version == FROZEN_PUBLIC_RULE_VERSION and ((loss is not None and loss >= 20) or (sell_tax is not None and sell_tax >= 20)))
    if immediate:
        return {"exit": True, "immediate": True, "consecutiveMisses": 0}
    previous_window = str(item.get("lastExitWindowId") or "")
    misses = int(item.get("consecutiveCompletedMisses") or 0)
    if below_exit_rule and new_window_id and new_window_id != previous_window:
        misses += 1
    elif not below_exit_rule and new_window_id and new_window_id != previous_window:
        misses = 0
    return {"exit": misses >= 2, "immediate": False, "consecutiveMisses": misses}
