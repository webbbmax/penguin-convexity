#!/usr/bin/env python3
"""Deterministic C2.1 hard gate, evidence paths, states and confidence."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULE_PATH = PROJECT_ROOT / "docs" / "C2.1_RULE_CONFIG.json"
STATE_LABELS = {
    "data_limited": "数据受限",
    "convexity_clue": "凸性线索",
    "active_project": "活跃项目",
    "early_observation": "新发观察",
    "continuous_observation": "持续观察",
}
PATH_LABELS = {
    "trade_liquidity_formation": "交易与流动性形成",
    "verified_product_usage_expansion": "真实产品使用扩张",
    "supply_demand_structure_improvement": "供应与需求结构改善",
    "indexed_pool_activity_outpaces_supply_adjusted_valuation": "已索引池活动超过供应修正估值变化",
}
GITHUB_BOUNDARY = "目前只确认代码仓库，不证明产品部署、用户采用或投资价值。"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def load_rules(path=DEFAULT_RULE_PATH) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    rules = json.loads(raw.decode("utf-8-sig"))
    if rules.get("status") != "frozen" or rules.get("ruleVersion") != "c2.1-rules-v1":
        raise ValueError("C2.1正式规则配置未冻结或版本不匹配")
    return rules, hashlib.sha256(raw).hexdigest()


def age_days(t0, as_of=None) -> int | None:
    start = parse_utc(t0)
    current = parse_utc(as_of) if as_of else datetime.now(timezone.utc)
    if not start or not current:
        return None
    return math.floor((current - start).total_seconds() / 86400)


def age_band(days, rules) -> str | None:
    if days is None:
        return None
    for code, band in rules["ageBands"].items():
        if band["minAgeDays"] <= days <= band["maxAgeDays"]:
            return code
    return None


def history_stage(days) -> str:
    if days is None or days <= 2:
        return "launch_0_2"
    if days <= 6:
        return "early_3_6"
    if days <= 13:
        return "forming_7_13"
    return "full_14_90"


def evaluation_window_id(t0, as_of, band) -> str:
    current = parse_utc(as_of) or datetime.now(timezone.utc)
    if band in {"age_0_2", "age_3_6"}:
        return f"hour:{current.strftime('%Y-%m-%dT%H')}:completed"
    return f"day:{current.strftime('%Y-%m-%d')}:completed"


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def check(code, label, status, reason, evidence_ids=None, source_names=None, observed_at=None):
    return {
        "code": code,
        "label": label,
        "status": status,
        "reason": reason,
        "evidenceIds": list(evidence_ids or []),
        "sourceNames": list(source_names or []),
        "observedAt": observed_at,
    }


def product_evidence_summary(records) -> dict:
    qualifying = [row for row in records if row.get("status") == "qualifying"]
    types = sorted({row.get("evidenceType") for row in qualifying if row.get("evidenceType")})
    github_row = next((row for row in qualifying if row.get("evidenceType") == "github"), None)
    result = {
        "hasAnyQualifyingEvidence": bool(qualifying),
        "qualifyingTypes": types,
        "github": {
            "status": "qualifying" if github_row else "missing",
            "officialIdentityStatus": github_row.get("identityStatus") if github_row else "not_verified",
            "organization": github_row.get("organization", "") if github_row else "",
            "repository": github_row.get("repository", "") if github_row else "",
            "repositoryUrl": github_row.get("sourceUrl", "") if github_row else "",
            "isFork": bool(github_row.get("isFork")) if github_row else False,
            "hasOwnCommits": bool(github_row.get("hasOwnCommits")) if github_row else False,
            "hasNonDocumentOwnCommit": bool(github_row.get("hasNonDocumentOwnCommit")) if github_row else False,
            "isArchived": bool(github_row.get("isArchived")) if github_row else False,
            "isEmpty": bool(github_row.get("isEmpty")) if github_row else False,
            "primaryLanguage": github_row.get("primaryLanguage") if github_row else None,
            "commitCountObserved": github_row.get("commitCountObserved") if github_row else None,
            "contributorCountObserved": github_row.get("contributorCountObserved") if github_row else None,
            "lastCommitAt": github_row.get("lastCommitAt") if github_row else None,
            "collectedAt": github_row.get("observedAt") if github_row else None,
            "evidenceIds": [github_row.get("evidenceId")] if github_row and github_row.get("evidenceId") else [],
            "boundaryNote": GITHUB_BOUNDARY if github_row else "",
        },
        "deployedProduct": next((row for row in qualifying if row.get("evidenceType") == "deployed_product"), None),
        "structuredBusiness": next((row for row in qualifying if row.get("evidenceType") == "business"), None),
        "executedTokenUtility": next((row for row in qualifying if row.get("evidenceType") == "token_utility"), None),
        "productUsage": next((row for row in qualifying if row.get("evidenceType") == "product_usage"), None),
        "plainSummary": "、".join({
            "github": "仅代码证据",
            "deployed_product": "已部署产品",
            "business": "已有业务数据",
            "token_utility": "代币功能已执行",
            "product_usage": "已有链上使用",
        }.get(item, item) for item in types) or "尚无合格产品证据",
    }
    return result


def build_hard_gate(candidate, market, risks, product, days, rule_version, observed_at, previous=None):
    interrupted = bool(candidate.get("criticalDataInterrupted"))
    checks = []
    checks.append(check(
        "t0_verified", "T0可复现", "pass" if candidate.get("t0Status") == "verified_in_supported_scope" else "fail",
        "已保存接入范围内可复现的最早公开流通证据。" if candidate.get("t0Status") == "verified_in_supported_scope" else "T0证据尚未核验或存在冲突。",
        candidate.get("t0EvidenceIds"), candidate.get("t0SourceNames"), candidate.get("effectiveT0"),
    ))
    within = days is not None and 0 <= days <= 90
    checks.append(check(
        "within_90_days", "处于0—90天", "pass" if within else "fail",
        "第90天仍在范围内。" if within else "未来时间或已到第91天。",
        candidate.get("t0EvidenceIds"), ["已核验T0与当前时间的确定性计算"], observed_at,
    ))
    relationship = candidate.get("relationshipClass")
    checks.append(check(
        "relationship_front_eligible", "项目关系可前台表达", "pass" if relationship in {"A", "B", "C"} else "fail",
        "A/B/C可进入前台。" if relationship in {"A", "B", "C"} else "D类只有代币，留在后台。",
        candidate.get("identityEvidenceIds"), ["已核验身份与项目关系分类规则"], observed_at,
    ))
    identity_ok = candidate.get("identityStatus") in {"verified", "market_matched"} and candidate.get("continuityStatus") != "known_continuation"
    checks.append(check("asset_pool_identity_consistent", "资产与交易池一致", "pass" if identity_ok else "fail", "网络、合约、池和资产方向一致。" if identity_ok else candidate.get("continuityReason") or "资产身份或连续关系尚未闭环。", candidate.get("identityEvidenceIds"), candidate.get("identitySourceNames"), observed_at))

    market_state = (market or {}).get("sourceStatus")
    market_present = market_state == "success" and bool((market or {}).get("pairAddress"))
    if interrupted and previous and previous.get("hardGate", {}).get("status") in {"pass", "stale"}:
        market_status = "stale"
        market_reason = "当前市场来源中断，沿用上次完整事实并标记数据受限。"
    else:
        market_status = "pass" if market_present else "pending" if market_state in {"quota_limited", "source_failure", "configuration_missing", "program_failure"} else "fail"
        market_reason = "已观察到公开交易池。" if market_present else "当前没有完整的公开交易池事实。"
    checks.append(check("public_market_exists", "存在公开市场", market_status, market_reason, (market or {}).get("evidenceIds"), [(market or {}).get("sourceName")] if (market or {}).get("sourceName") else [], (market or {}).get("observedAt")))
    for code, label, field in (("observed_buy", "观察到公众买入", "observedBuys"), ("observed_sell", "观察到公众卖出", "observedSells")):
        value = number((market or {}).get(field))
        status = "pass" if value is not None and value >= 1 else "stale" if market_status == "stale" else "pending" if market_status == "pending" else "fail"
        reason = f"当前真实窗口观察到{int(value)}笔。" if status == "pass" else "尚未取得可用的真实买卖事实。"
        checks.append(check(code, label, status, reason, (market or {}).get("evidenceIds"), [(market or {}).get("sourceName")] if (market or {}).get("sourceName") else [], (market or {}).get("observedAt")))
    hard_block = any(bool(row.get("hardTradeBlock")) for row in risks)
    risk_pending = any(row.get("sourceStatus") in {"quota_limited", "source_failure", "configuration_missing", "program_failure"} for row in risks)
    checks.append(check("no_hard_trade_block", "没有已确认硬交易阻断", "fail" if hard_block else "pending" if not risks or risk_pending else "pass", "已确认卖出、冻结、黑名单或身份硬阻断。" if hard_block else "当前未发现已确认硬交易阻断。" if risks and not risk_pending else "风险来源尚未完成，不能写成安全。", [item for row in risks for item in row.get("evidenceIds", [])], sorted({row.get("sourceName") for row in risks if row.get("sourceName")}), observed_at))
    checks.append(check("product_evidence_present", "存在合格产品证据", "pass" if product["hasAnyQualifyingEvidence"] else "fail", product["plainSummary"], [item for row in candidate.get("productEvidenceRecords", []) for item in ([row.get("evidenceId")] if row.get("evidenceId") else [])], sorted({row.get("sourceName") for row in candidate.get("productEvidenceRecords", []) if row.get("sourceName")}), observed_at))

    statuses = {row["status"] for row in checks}
    if "fail" in statuses:
        status = "fail"
    elif statuses <= {"pass"}:
        status = "pass"
    elif "stale" in statuses and statuses <= {"pass", "stale"}:
        status = "stale"
    else:
        status = "pending"
    preserved = bool(status in {"pending", "stale"} and interrupted and previous and previous.get("frontEligible"))
    return {"status": status, "checkedAt": observed_at, "ruleVersion": rule_version, "checks": checks}, preserved


def path(code, status, reason, metrics=None, counter=None, unknowns=None, evidence_ids=None, formed_at=None, invalidated_at=None):
    return {
        "pathCode": code,
        "label": PATH_LABELS[code],
        "status": status,
        "plainReason": reason,
        "supportingMetrics": list(metrics or []),
        "counterEvidence": list(counter or []),
        "unknowns": list(unknowns or []),
        "evidenceIds": list(evidence_ids or []),
        "formedAt": formed_at,
        "invalidatedAt": invalidated_at,
    }


def evaluate_paths(candidate, market, risks, product_usage, supply, pool_window, thresholds, rules, observed_at):
    severe = any(bool(row.get("severeAnomaly")) for row in risks)
    liquidity = number((market or {}).get("liquidityUsd"))
    volume = number((market or {}).get("volumeUsd"))
    transactions = number((market or {}).get("transactionCount"))
    ratio = number((market or {}).get("volumeLiquidityRatio"))
    quote_state = (market or {}).get("standardSellQuoteState")
    quote_loss = number((market or {}).get("standardSellQuoteLossPct"))
    values = ((volume, thresholds["volumeP60"]), (transactions, thresholds["transactionsP60"]), (ratio, thresholds["ratioP60"]))
    passing = sum(value is not None and value >= threshold for value, threshold in values)
    quote_available = quote_state == "success"
    market_available = (market or {}).get("sourceStatus") == "success"
    path1_formed = market_available and quote_available and not severe
    if not market_available or quote_state in {"no_data", "unsupported", "quota_limited", "source_failure", "configuration_missing", "program_failure", None}:
        path1_status = "unavailable"
        path1_reason = "市场或100美元标准卖出报价暂不可用，不能补零或冒充通过。"
    else:
        path1_status = "formed" if path1_formed else "not_formed"
        path1_reason = "100美元标准卖出报价成功，且没有已确认的硬交易阻断；比例指标只记录。" if path1_formed else "卖出报价可用，但存在已确认的硬交易阻断。"
    path1 = path("trade_liquidity_formation", path1_status, path1_reason, [
        {"label": "流动性", "value": liquidity, "threshold": max(thresholds["liquidityFloor"], thresholds["liquidityP60"]), "unit": "USD"},
        {"label": "达到P60的需求指标", "value": passing, "threshold": 2, "unit": "项"},
        {"label": "100美元卖出估算损失", "value": quote_loss, "threshold": None, "unit": "%"},
    ], unknowns=[] if quote_available else ["100美元标准卖出报价不可用"], evidence_ids=(market or {}).get("evidenceIds"), formed_at=observed_at if path1_formed else None)

    usage = product_usage or {}
    usage_ready = usage.get("identityMappingStatus") == "verified" and usage.get("previousValue") is not None and usage.get("currentValue") is not None
    usage_formed = usage_ready and number(usage.get("currentValue")) >= 5 and number(usage.get("currentValue")) >= number(usage.get("previousValue")) + 3 and number(usage.get("currentValue")) >= number(usage.get("previousValue")) * 1.2
    path2 = path("verified_product_usage_expansion", "formed" if usage_formed else "not_formed" if usage_ready else "unavailable", "确定映射的产品使用在两个等长完整窗口达到绝对值与增幅门槛。" if usage_formed else "真实产品使用序列完整但未达到冻结门槛。" if usage_ready else "没有确定映射的真实产品使用时间序列。", usage.get("supportingMetrics"), unknowns=[] if usage_ready else ["真实产品使用历史不可用"], evidence_ids=usage.get("evidenceIds"), formed_at=observed_at if usage_formed else None)

    supply = supply or {}
    supply_ready = supply.get("historyState") == "success" and supply.get("marketActivityVsP50") is not None
    top10_change = (number(supply.get("previousTop10SharePct")), number(supply.get("currentTop10SharePct")))
    hhi_change = (number(supply.get("previousHolderHhi")), number(supply.get("currentHolderHhi")))
    supply_change = number(supply.get("supplyChangePct"))
    market_p50 = bool(supply.get("marketActivityVsP50") and supply.get("marketActivityVsP50") >= 1)
    supply_formed = supply_ready and market_p50 and (
        (all(v is not None for v in top10_change) and top10_change[0] - top10_change[1] >= 5)
        or (all(v is not None for v in hhi_change) and hhi_change[0] > 0 and (hhi_change[0] - hhi_change[1]) / hhi_change[0] >= 0.10)
        or (supply_change is not None and supply_change <= -0.5)
    )
    path3 = path("supply_demand_structure_improvement", "formed" if supply_formed else "not_formed" if supply_ready else "unavailable", "供应或持仓结构改善且市场活动不低于同组P50。" if supply_formed else "历史可比但没有达到供应需求改善门槛。" if supply_ready else "只有当前供应快照或历史计量不可比。", supply.get("supportingMetrics"), unknowns=[] if supply_ready else ["供应与持仓历史不可比"], evidence_ids=supply.get("evidenceIds"), formed_at=observed_at if supply_formed else None)

    pool = pool_window or {}
    pool_state = pool.get("sourceStatus")
    pool_ready = pool_state == "success" and pool.get("indexedPoolCount") == pool.get("ohlcvSuccessCount") and pool.get("supplyHistorySuccess") and number(pool.get("relativeExpansion")) is not None and number(pool.get("riskAdjustedSurplus")) is not None
    comparison_formed = pool.get("comparisonWindowComplete") is True
    path4_formed = pool_ready and comparison_formed and number(pool.get("relativeExpansion")) >= thresholds["relativeExpansionP60"] and number(pool.get("riskAdjustedSurplus")) > 0 and not severe
    if pool_state in {"quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure", "no_data", None}:
        path4_status = "unavailable"
        path4_reason = "已索引池OHLCV或历史供应暂不可用。"
    elif not comparison_formed:
        path4_status = "forming"
        path4_reason = "真实前后比较窗口尚未完整形成。"
    else:
        path4_status = "formed" if path4_formed else "not_formed"
        path4_reason = "已索引池活动相对供应修正估值达到同组P60，且风险修正剩余为正。" if path4_formed else "完整窗口未同时达到相对扩张和风险修正剩余门槛。"
    path4 = path("indexed_pool_activity_outpaces_supply_adjusted_valuation", path4_status, path4_reason, [
        {"label": "相对扩张", "value": number(pool.get("relativeExpansion")), "threshold": thresholds["relativeExpansionP60"]},
        {"label": "风险修正剩余", "value": number(pool.get("riskAdjustedSurplus")), "threshold": 0},
        {"label": "未索引已发现池", "value": pool.get("unindexedDiscoveredPoolCount"), "unit": "个"},
    ], unknowns=[] if pool_ready else ["部分已索引池OHLCV或历史供应不可用"], evidence_ids=pool.get("evidenceIds"), formed_at=observed_at if path4_formed else None)
    return [path1, path2, path3, path4]


def factor_and_confidence(market, supply, product_usage, pool_window, risks, product, history, source_impact, thresholds):
    observed = {
        "D": bool(market and market.get("sourceStatus") == "success" and (market.get("volumeUsd") is not None or market.get("transactionCount") is not None)),
        "L": bool(market and market.get("liquidityUsd") is not None and market.get("standardSellQuoteState") == "success"),
        "S": bool(supply and supply.get("historyState") == "success"),
        "G": bool((product_usage and product_usage.get("currentValue") is not None) or (pool_window and pool_window.get("sourceStatus") == "success")),
        "Q": bool(risks or (market and market.get("sourceStatus") == "success")),
    }
    def direction(code):
        if not observed[code]:
            return "unavailable"
        if code == "D":
            passing = sum(((number(market.get("volumeUsd")) or -1) >= thresholds["volumeP60"], (number(market.get("transactionCount")) or -1) >= thresholds["transactionsP60"], (number(market.get("volumeLiquidityRatio")) or -1) >= thresholds["ratioP60"]))
            return "improving" if passing >= 2 else "stable"
        if code == "L":
            liquidity = number(market.get("liquidityUsd"))
            return "improving" if market.get("standardSellQuoteState") == "success" else "weakening"
        if code == "S":
            return "stable"
        if code == "G":
            return "improving" if pool_window and number(pool_window.get("riskAdjustedSurplus")) is not None and number(pool_window.get("riskAdjustedSurplus")) > 0 else "stable"
        return "weakening" if any(row.get("severeAnomaly") for row in risks) else "stable"
    directions = [{"factor": code, "direction": direction(code)} for code in ("D", "L", "S", "G", "Q")]
    field_coverage = sum(observed.values()) / 5
    freshness = 0 if source_impact.get("status") == "interrupted" else 0.6 if source_impact.get("status") == "degraded" else 1
    history_coverage = max(0, min(1, number(history.get("coverageRatio")) or 0))
    cross_source = 0 if source_impact.get("reasonCode") == "cross_source_price_conflict" else 0.5 if source_impact.get("status") != "healthy" else 1
    confidence_value = 0.35 * field_coverage + 0.25 * freshness + 0.20 * history_coverage + 0.20 * cross_source
    level = "interrupted" if source_impact.get("status") == "interrupted" else "sufficient" if confidence_value >= 0.7 else "limited"
    confidence = {
        "level": level,
        "plainReason": "关键字段、时效、真实历史和跨来源一致性均达到当前判断需要。" if level == "sufficient" else "部分关键字段、历史或来源一致性仍受限。" if level == "limited" else "关键来源中断，保留上次完整判断。",
        "validThrough": source_impact.get("lastSuccessfulAt"),
        "components": {"fieldCoverage": field_coverage, "dataFreshness": freshness, "realHistoryCoverage": history_coverage, "crossSourceConsistency": cross_source},
    }
    factor_score = {"improving": 0.75, "stable": 0.5, "weakening": 0.25, "unavailable": 0.5}
    weights = {"D": 0.25, "L": 0.25, "S": 0.20, "G": 0.15, "Q": 0.15}
    score = sum(weights[row["factor"]] * factor_score[row["direction"]] for row in directions) * 100
    return directions, confidence, score, sum(observed.values())


def evaluate_candidate(candidate, *, market=None, risks=None, product_usage=None, supply=None, pool_window=None, cohort=None, previous=None, as_of=None, rules=None, rule_hash=None):
    rules, rule_hash = (rules, rule_hash) if rules is not None and rule_hash is not None else load_rules()
    as_of = as_of or utc_now()
    days = age_days(candidate.get("effectiveT0"), as_of)
    band_code = age_band(days, rules) or "outside_window"
    fallback = rules["ageBands"].get(band_code, rules["ageBands"]["age_31_90"])
    p60 = fallback["baselineP60"]
    p70 = fallback["baselineP70"]
    cohort = cohort or {}
    thresholds = {
        "liquidityFloor": fallback["liquidityFloorUsd"],
        "liquidityP60": number(cohort.get("liquidityP60")) or p60["liquidityUsd"],
        "volumeP60": number(cohort.get("volumeP60")) or p60["volumeUsd"],
        "transactionsP60": number(cohort.get("transactionsP60")) or p60["transactions"],
        "ratioP60": number(cohort.get("ratioP60")) or p60["volumeLiquidityRatio"],
        "volumeP70": number(cohort.get("volumeP70")) or p70["volumeUsd"],
        "transactionsP70": number(cohort.get("transactionsP70")) or p70["transactions"],
        "ratioP70": number(cohort.get("ratioP70")) or p70["volumeLiquidityRatio"],
        "relativeExpansionP60": number(cohort.get("relativeExpansionP60")) or 0,
    }
    records = candidate.get("productEvidenceRecords") or []
    product = product_evidence_summary(records)
    risks = risks or []
    hard_gate, preserved = build_hard_gate(candidate, market, risks, product, days, rules["ruleVersion"], as_of, previous)
    paths = evaluate_paths(candidate, market, risks, product_usage, supply, pool_window, thresholds, rules, as_of)
    expected_days = max(0, (days if days is not None else -1) + 1)
    valid_days = int(candidate.get("validHistoryDays") or min(expected_days, 1 if market else 0))
    backfilled_days = int(candidate.get("backfilledDays") or valid_days)
    history = {
        "t0": candidate.get("effectiveT0"),
        "ageDays": days,
        "expectedHistoryDays": expected_days,
        "backfilledDays": backfilled_days,
        "validHistoryDays": valid_days,
        "gapDays": max(0, expected_days - backfilled_days),
        "coverageRatio": valid_days / expected_days if expected_days else 0,
        "effectiveWindowDays": min(expected_days, 14),
        "historyStage": history_stage(days),
        "sources": candidate.get("historySources") or [],
        "lastSuccessfulAt": candidate.get("historyLastSuccessfulAt") or (market or {}).get("observedAt"),
    }
    source_impact = candidate.get("sourceImpact") or {"status": "healthy", "affectedProjectCount": 0, "affectedChains": [], "affectedFields": [], "lastSuccessfulAt": (market or {}).get("observedAt"), "reasonCode": "", "plainReason": "当前关键来源可用。", "expectedRecoveryAt": None}
    directions, confidence, sort_score, observed_factor_count = factor_and_confidence(market or {}, supply or {}, product_usage or {}, pool_window or {}, risks, product, history, source_impact, thresholds)
    formed_paths = [row["pathCode"] for row in paths if row["status"] == "formed"]
    source_types = set(candidate.get("independentSourceTypes") or [])
    clue = hard_gate["status"] == "pass" and observed_factor_count >= 3 and "trade_liquidity_formation" in formed_paths and len(formed_paths) >= 2 and len(source_types) >= 2 and not any(row.get("severeAnomaly") for row in risks)
    active_market = market and number(market.get("observedBuys")) is not None and number(market.get("observedBuys")) >= 1 and number(market.get("observedSells")) is not None and number(market.get("observedSells")) >= 1 and any(((number(market.get("volumeUsd")) or -1) >= thresholds["volumeP70"], (number(market.get("transactionCount")) or -1) >= thresholds["transactionsP70"], (number(market.get("volumeLiquidityRatio")) or -1) >= thresholds["ratioP70"]))
    github = product["github"]
    recent_commit = parse_utc(github.get("lastCommitAt"))
    current_time = parse_utc(as_of)
    active_repo = github.get("status") == "qualifying" and recent_commit and current_time and (current_time - recent_commit).total_seconds() <= 30 * 86400 and github.get("hasNonDocumentOwnCommit")
    active_usage = bool(product_usage and product_usage.get("newRealUsage"))
    active_liquidity = bool(market and market.get("twoCompletedWindowsAboveFloor") and number(market.get("liquidityDrawdownPct")) is not None and number(market.get("liquidityDrawdownPct")) < 50)
    active = hard_gate["status"] == "pass" and not clue and not candidate.get("criticalDataInterrupted") and any((active_market, active_repo, active_usage, active_liquidity))
    if hard_gate["status"] == "fail":
        display_state = "continuous_observation" if days is not None and days >= 14 else "early_observation"
        state_reason = "对象没有通过全部前台观察门槛，状态只保存在后台。"
    elif candidate.get("criticalDataInterrupted") or preserved:
        display_state = "data_limited"
        state_reason = source_impact.get("plainReason") or "关键数据暂时无法更新，保留上次完整记录。"
    elif clue:
        display_state = "convexity_clue"
        state_reason = "已形成至少两条强证据路径，其中包含交易与流动性；不是上涨预测。"
    elif active:
        display_state = "active_project"
        state_reason = "已出现可复算的真实活动，但尚未形成凸性线索。"
    elif days is not None and days < 14:
        display_state = "early_observation"
        state_reason = "真实历史不足14天且尚未形成强/弱线索；不是等待期或年龄扣分。"
    else:
        display_state = "continuous_observation"
        state_reason = "真实历史已满14天，仍符合观察范围但尚未形成强/弱线索。"

    window_id = evaluation_window_id(candidate.get("effectiveT0"), as_of, band_code)
    misses = 0
    if previous and previous.get("displayState", {}).get("code") in {"convexity_clue", "active_project"} and display_state not in {"data_limited", previous.get("displayState", {}).get("code")} and hard_gate["status"] == "pass" and not any(row.get("severeAnomaly") for row in risks):
        previous_window = previous.get("evaluationWindowId")
        misses = int(previous.get("consecutiveCompletedMisses") or 0) + (window_id != previous_window)
        if misses < 2:
            display_state = previous["displayState"]["code"]
            state_reason = "本次是第1个新完成窗口未达到退出规则，按防抖保留原状态。"
    if previous and window_id == previous.get("evaluationWindowId"):
        misses = int(previous.get("consecutiveCompletedMisses") or 0)

    front_eligible = hard_gate["status"] == "pass" or preserved
    sort_reason = f"{STATE_LABELS[display_state]}；{len(formed_paths)}条强路径形成；数据可信度{confidence['level']}。"
    return {
        "evaluationWindowId": window_id,
        "evaluatedAt": as_of,
        "ruleVersion": rules["ruleVersion"],
        "ruleConfigHash": rule_hash,
        "cohortSnapshotId": cohort.get("snapshotId") or f"fallback:{band_code}:{rules['ruleVersion']}",
        "cohortScope": cohort.get("scope") or "frozen_age_band_baseline",
        "cohortSampleSize": int(cohort.get("sampleSize") or 0),
        "ageDays": days,
        "ageBand": band_code,
        "frontEligible": front_eligible,
        "hardGate": hard_gate,
        "productEvidence": product,
        "observationHistory": history,
        "displayState": {"code": display_state, "label": STATE_LABELS[display_state], "reason": state_reason, "since": as_of, "triggerEvidenceIds": [item for row in paths if row["status"] == "formed" for item in row.get("evidenceIds", [])], "nextTransitionConditions": ["下一完整窗口按冻结规则重新计算"], "invalidationConditions": ["硬门槛失败、第91天或已确认严重异常立即退出"]},
        "evidencePaths": paths,
        "factorDirections": directions,
        "dataConfidence": confidence,
        "thresholdContext": {"cohortSnapshotId": cohort.get("snapshotId") or f"fallback:{band_code}:{rules['ruleVersion']}", "cohortScope": cohort.get("scope") or "frozen_age_band_baseline", "cohortSampleSize": int(cohort.get("sampleSize") or 0), "entryThresholds": thresholds, "exitThresholds": {"percentile": 0.50}, "cohortFallbackUsed": not bool(cohort)},
        "marketSnapshot": market or {},
        "sourceImpact": source_impact,
        "sortScore": round(sort_score, 6),
        "sortReason": sort_reason,
        "consecutiveCompletedMisses": misses,
        "formedAt": as_of if display_state in {"convexity_clue", "active_project"} else None,
        "invalidatedAt": as_of if hard_gate["status"] == "fail" else None,
    }
