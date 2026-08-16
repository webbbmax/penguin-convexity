#!/usr/bin/env python3
"""Build the three atomic C2.4 business snapshots and a compact admin view."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "c2.1-pipeline.db"
DEFAULT_CANDIDATE = APP_ROOT / "c2-4-candidate-snapshot.js"
DEFAULT_TRACKING = APP_ROOT / "c2-4-tracking-snapshot.js"
DEFAULT_FRONT = APP_ROOT / "c2-4-front-snapshot.js"
DEFAULT_ADMIN = APP_ROOT / "c2-4-admin-snapshot.js"
CANDIDATE_PREFIX = "window.PENGUIN_CONVEXITY_C24_CANDIDATES = "
TRACKING_PREFIX = "window.PENGUIN_CONVEXITY_C24_TRACKING = "
FRONT_PREFIX = "window.PENGUIN_CONVEXITY_C24 = "
ADMIN_PREFIX = "window.PENGUIN_CONVEXITY_C24_ADMIN = "
CHAIN_ORDER = (
    "ethereum-mainnet",
    "solana-mainnet",
    "base-mainnet",
    "arbitrum-mainnet",
    "bnb-mainnet",
    "robinhood-mainnet",
)
CHAIN_LABELS = {
    "ethereum-mainnet": "Ethereum",
    "solana-mainnet": "Solana",
    "base-mainnet": "Base",
    "arbitrum-mainnet": "Arbitrum One",
    "bnb-mainnet": "BNB Smart Chain",
    "robinhood-mainnet": "Robinhood Chain",
}
PATH_LABELS = {
    "trade_demand_formation": "交易需求形成",
    "liquidity_exit_quality": "流动性与退出质量",
    "supply_holder_improvement": "供应与持币结构改善",
    "indexed_pool_activity_vs_supply_adjusted_valuation": "已索引池活动跑赢供应调整估值",
}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c2_2_tracking import build_bayes_evidence, build_tracking_cohort_index, load_tracking_catalog  # noqa: E402
from c2_1_observation_state import confirmed_trade_block, latest_effective_market_rows  # noqa: E402
from c2_4_rule_replay import build_rule_replay_inputs  # noqa: E402
from c2_4_rules import (  # noqa: E402
    age_band,
    determine_public_state,
    evaluate_first_gate,
    evaluate_public_baseline,
    evaluate_strong_paths,
    lifecycle_pool,
    load_active_rule_version,
    load_config,
    number,
    rank_home_by_chain,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value if value is not None else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _read_snapshot(path: Path, prefix: str, schema_version: str) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith(prefix):
            return None
        payload = json.loads(text[len(prefix) :].rstrip(" ;\r\n"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if payload.get("schemaVersion") != schema_version or not payload.get("isComplete"):
        return None
    return payload


def _important_changes(
    previous_front: dict[str, Any] | None,
    public_items: list[dict[str, Any]],
    data_cutoff: str,
    tracking_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not previous_front:
        return []
    current = {item["assetId"]: item for item in public_items}
    previous = {item["assetId"]: item for item in previous_front.get("items") or []}
    tracked = {item["assetId"]: item for item in tracking_items or []}
    changes = [
        row for row in previous_front.get("changes") or []
        if str(row.get("changeId") or "").startswith("c24-change-")
    ]
    state_labels = {"convexity_clue": "凸性线索", "active_project": "活跃项目", "observing": "观察中"}
    lifecycle_labels = {"new_0_90": "90 天内", "continued_91_plus": "90 天后持续跟踪"}

    def add(asset_id: str, change_type: str, old: Any, new: Any, item: dict[str, Any], what: str, why: str, watch: str) -> None:
        changed_at = item.get("latestCompleteTrackingAt") or item.get("evaluationCompletedAt") or data_cutoff
        change_id = "c24-change-" + hashlib.sha256(
            f"{asset_id}|{change_type}|{old}|{new}|{changed_at}".encode("utf-8")
        ).hexdigest()[:24]
        if any(row.get("changeId") == change_id for row in changes):
            return
        changes.append({
            "changeId": change_id,
            "assetId": asset_id,
            "chainId": item.get("chainId"),
            "lifecyclePool": item.get("lifecyclePool"),
            "changedAt": changed_at,
            "whatChanged": what,
            "whyItMatters": why,
            "nextWatch": watch,
            "detailHref": f"project-detail.html?assetId={asset_id}",
        })

    for asset_id, item in current.items():
        old = previous.get(asset_id)
        if old is None:
            add(
                asset_id, "public_entry", "not_public", item.get("publicState"), item,
                f"首次达到公开底线，当前为{state_labels.get(item.get('publicState'), '观察中')}",
                "风险、退出、项目证据和稳定身份已经形成新的完整公开结果。",
                item.get("nextWatch") or "继续观察下一份完整窗口。",
            )
            continue
        if old.get("publicState") != item.get("publicState"):
            add(
                asset_id, "public_state", old.get("publicState"), item.get("publicState"), item,
                f"公开状态由{state_labels.get(old.get('publicState'), '原状态')}变为{state_labels.get(item.get('publicState'), '观察中')}",
                "新的完整窗口改变了强证据路径组合或可核验活动。",
                item.get("nextWatch") or "继续观察下一份完整窗口。",
            )
        old_paths = sorted(old.get("formedPathCodes") or [])
        new_paths = sorted(item.get("formedPathCodes") or [])
        if old_paths != new_paths:
            add(
                asset_id, "strong_paths", old_paths, new_paths, item,
                f"已形成的强证据路径由 {len(old_paths)} 条变为 {len(new_paths)} 条",
                "强证据路径数量或构成发生变化，会影响当前公开状态的依据。",
                item.get("nextWatch") or "继续核对下一份完整路径结果。",
            )
        if old.get("lifecyclePool") != item.get("lifecyclePool"):
            add(
                asset_id, "lifecycle", old.get("lifecyclePool"), item.get("lifecyclePool"), item,
                f"生命周期由{lifecycle_labels.get(old.get('lifecyclePool'), '原池')}转为{lifecycle_labels.get(item.get('lifecyclePool'), '当前池')}",
                "这是同一资产的跟踪池迁移，不代表项目质量提高或降低。",
                "继续按真实新窗口复查风险、退出和强路径。",
            )

    for asset_id, old in previous.items():
        if asset_id in current:
            continue
        current_tracking = tracked.get(asset_id, old)
        add(
            asset_id, "public_exit", old.get("publicState"), "not_public", current_tracking,
            "已撤下公开展示",
            "新的完整结果触发正常退出防抖，或确认出现冻结、黑名单、无法卖出等硬交易阻断。",
            "历史记录仍保留；只有新的完整证据重新满足公开底线才会恢复。",
        )
    changes.sort(key=lambda row: (str(row.get("changedAt") or ""), str(row.get("changeId") or "")), reverse=True)
    return changes[:500]


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finalize(payload: dict[str, Any], prefix: str) -> dict[str, Any]:
    content = {**payload, "isComplete": True}
    content_hash = _hash(content)
    return {**content, "buildId": f"{prefix}-{content_hash[:16]}", "contentSha256": content_hash}


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _chunks(values: list[int], size: int = 5000):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _select_for_ids(connection: sqlite3.Connection, table: str, ids: list[int], order: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in _chunks(ids):
        placeholders = ",".join("?" for _ in batch)
        rows.extend(dict(row) for row in connection.execute(
            f"SELECT * FROM {table} WHERE candidate_id IN ({placeholders}) ORDER BY candidate_id,{order}",
            tuple(batch),
        ))
    return rows


def _latest_and_previous(rows: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["candidate_id"])].append(row)
    latest = {}
    previous = {}
    for candidate_id, values in grouped.items():
        values.sort(key=lambda row: (str(row.get("observed_at") or ""), str(row.get("observation_id") or "")), reverse=True)
        latest[candidate_id] = values[0]
        if len(values) > 1:
            previous[candidate_id] = values[1]
    return latest, previous


def _completed_window_rows(
    rows: list[dict[str, Any]],
    tracking_records: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep public inputs inside the latest completed deep-tracking window."""

    result = []
    for row in rows:
        tracking = tracking_records.get(int(row["candidate_id"]), {})
        cutoff = tracking.get("completed_at") if tracking.get("state") == "completed" else None
        observed_at = row.get("observed_at")
        if not cutoff or not observed_at or str(observed_at) <= str(cutoff):
            result.append(row)
    return result


def _evaluation_project_evidence_ids(evaluation: dict[str, Any]) -> set[str]:
    hard_gate = _json(evaluation.get("hard_gate_json"), {}) or {}
    for check in hard_gate.get("checks") or []:
        if check.get("code") == "product_evidence_present" and check.get("status") == "pass":
            return {str(value) for value in check.get("evidenceIds") or [] if value}
    return set()


def _completed_window_evidence_rows(
    rows: list[dict[str, Any]],
    tracking_records: dict[int, dict[str, Any]],
    evaluations: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    referenced = {
        candidate_id: _evaluation_project_evidence_ids(evaluation)
        for candidate_id, evaluation in evaluations.items()
    }
    for row in rows:
        candidate_id = int(row["candidate_id"])
        tracking = tracking_records.get(candidate_id, {})
        cutoff = tracking.get("completed_at") if tracking.get("state") == "completed" else None
        observed_at = row.get("observed_at")
        evidence_id = str(row.get("evidence_id") or "")
        if (
            not cutoff
            or not observed_at
            or str(observed_at) <= str(cutoff)
            or evidence_id in referenced.get(candidate_id, set())
        ):
            result.append(row)
    return result


def _candidate_input(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "assetId": row.get("asset_id"),
        "chainId": row.get("network_id"),
        "contractAddress": row.get("token_address"),
        "pairAddress": row.get("pair_address"),
        "tokenSide": row.get("token_side"),
        "t0Status": row.get("t0_status"),
        "ageDays": row.get("age_days"),
        "observedBuys": row.get("observed_buys"),
        "observedSells": row.get("observed_sells"),
        "confirmedHardBlock": bool(row.get("confirmed_hard_block")),
    }


def _candidate_item(row: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidateId": int(row["candidate_id"]),
        "assetId": row.get("asset_id"),
        "projectId": row.get("project_id"),
        "canonicalName": row.get("canonical_name") or row.get("symbol") or "未命名候选",
        "symbol": row.get("symbol") or "",
        "chainId": row.get("network_id") or "",
        "chainLabel": CHAIN_LABELS.get(row.get("network_id"), row.get("network_id") or "未知链"),
        "tokenAddress": row.get("token_address") or "",
        "poolId": row.get("pair_address") or "",
        "assetDirection": row.get("token_side") or "",
        "t0": row.get("effective_t0"),
        "t0Status": row.get("t0_status"),
        "t0EvidenceIds": [value for value in (row.get("gate0_pool_id"),) if value],
        "ageDays": row.get("age_days"),
        "firstGateState": gate["state"],
        "firstGateChecks": gate["checks"],
        "relationshipClass": row.get("relationship_class") or "D",
        "firstGateCompletedAt": row.get("first_gate_completed_at"),
        "handoffBatchId": row.get("qualification_batch_id"),
        "sourceQueue": row.get("source_queue"),
    }


def _evidence_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    qualifying = [row for row in rows if row.get("status") == "qualifying"]
    types = sorted({str(row.get("evidence_type") or "") for row in qualifying if row.get("evidence_type")})
    sources = sorted({str(row.get("source_name") or "") for row in qualifying if row.get("source_name")})
    boundaries = [str(row.get("boundary_note") or "") for row in qualifying if row.get("boundary_note")]
    github_only = bool(types) and set(types) == {"github"}
    return {
        "qualified": bool(qualifying),
        "attributable": any(row.get("identity_status") in {"verified", "market_matched"} for row in qualifying),
        "state": "qualifying" if qualifying else "no_data",
        "types": types,
        "sources": sources,
        "boundary": "仅代码证据；不证明产品部署、用户采用或投资价值。" if github_only else boundaries[0] if boundaries else "目前没有找到可核验并能归属到该资产的项目证据，暂不公开。",
        "evidenceIds": [row.get("evidence_id") for row in qualifying if row.get("evidence_id")],
        "links": [{"label": row.get("source_name"), "url": row.get("source_url")} for row in qualifying if row.get("source_url")],
        "recentRepositoryActivity": any(row.get("evidence_type") == "github" and bool((_json(row.get("payload_json"), {}) or {}).get("recentNonDocumentationCommit")) for row in qualifying),
        "newProductUsage": any(row.get("evidence_type") == "product_usage" for row in qualifying),
    }


def _delta_pct(previous: Any, current: Any) -> float | None:
    old = number(previous)
    new = number(current)
    return (new / old - 1) * 100 if old not in {None, 0} and new is not None else None


def _normalized_supply(row: dict[str, Any]) -> float | None:
    raw = number(row.get("supply_raw"))
    decimals = number(row.get("decimals"))
    if raw is None:
        return None
    return raw / (10 ** int(decimals)) if decimals is not None else raw


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _quantile(values: list[float], probability: float) -> float | None:
    ordered = sorted(value for value in values if number(value) is not None)
    if not ordered:
        return None
    if len(ordered) == 1:
        return float(ordered[0])
    point = (len(ordered) - 1) * probability
    lower = int(point)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = point - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _build_path_cohort_index(
    base_by_id: dict[int, dict[str, Any]],
    tracking_records: dict[int, dict[str, Any]],
    continued_ids: set[int],
    market: dict[int, dict[str, Any]],
    supply: dict[int, dict[str, Any]],
    previous_supply: dict[int, dict[str, Any]],
    pool: dict[int, dict[str, Any]],
    data_cutoff: str,
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Prepare real rolling-30-day comparison objects for the four paths."""

    cutoff = _parse_time(data_cutoff) or datetime.now(timezone.utc)
    oldest = cutoff - timedelta(days=30)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate_id, tracking in tracking_records.items():
        if tracking.get("state") != "completed":
            continue
        completed_at = _parse_time(tracking.get("completed_at"))
        if completed_at is None or completed_at < oldest or completed_at > cutoff:
            continue
        base = base_by_id.get(candidate_id)
        if not base:
            continue
        chain_id = str(base.get("network_id") or "")
        age = number(base.get("age_days"))
        is_continued = candidate_id in continued_ids
        band = "continued_91_plus" if is_continued else age_band(age)
        if not chain_id or band is None:
            continue

        market_row = market.get(candidate_id, {})
        market_observed = _parse_time(market_row.get("observed_at"))
        market_usable = (
            market_row.get("source_status") == "success"
            and market_observed is not None
            and oldest <= market_observed <= cutoff
        )
        metrics = {
            "liquidityUsd": number(market_row.get("liquidity_usd")) if market_usable else None,
            "volumeUsd": number(market_row.get("volume_usd")) if market_usable else None,
            "transactions": number(market_row.get("transaction_count")) if market_usable else None,
            "volumeLiquidityRatio": number(market_row.get("volume_liquidity_ratio")) if market_usable else None,
            "relativeExpansion": None,
        }

        pool_row = pool.get(candidate_id, {})
        current_supply = supply.get(candidate_id, {})
        old_supply = previous_supply.get(candidate_id, {})
        pool_observed = _parse_time(pool_row.get("observed_at"))
        pool_payload = _json(pool_row.get("payload_json"), {}) or {}
        pool_usable = (
            pool_row.get("source_status") == "success"
            and pool_observed is not None
            and oldest <= pool_observed <= cutoff
            and number(pool_row.get("indexed_pool_count")) is not None
            and number(pool_row.get("indexed_pool_count")) == number(pool_row.get("ohlcv_success_count"))
            and current_supply.get("source_status") == "success"
            and old_supply.get("source_status") == "success"
        )
        if pool_usable:
            metrics["relativeExpansion"] = number(pool_row.get("relative_expansion"))
        if not any(value is not None for value in metrics.values()):
            continue

        if is_continued:
            groups[("continued_chain", chain_id, band)].append(metrics)
            groups[("continued_all", "", band)].append(metrics)
        else:
            groups[("new_chain", chain_id, band)].append(metrics)
            groups[("new_all", "", band)].append(metrics)
    return dict(groups)


def _path_cohort_thresholds(
    candidate: dict[str, Any],
    candidate_id: int,
    continued_ids: set[int],
    cohort_index: dict[tuple[str, str, str], list[dict[str, Any]]],
    config: dict[str, Any],
) -> dict[str, Any]:
    chain_id = str(candidate.get("chainId") or "")
    band = "continued_91_plus" if candidate_id in continued_ids else age_band(candidate.get("ageDays"))
    cohort_rules = config["cohortPercentiles"]
    if candidate_id in continued_ids:
        attempts = [
            (("continued_chain", chain_id, "continued_91_plus"), "same_chain_continued_91_plus_rolling_30_days", int(cohort_rules["continuedPrimary"]["minimumValidObjects"])),
            (("continued_all", "", "continued_91_plus"), "all_supported_chains_continued_91_plus_rolling_30_days", int(cohort_rules["continuedSecondary"]["minimumValidObjects"])),
            (("new_chain", chain_id, "age_31_90"), "same_chain_age_31_90_rolling_30_days", 20),
            (("new_all", "", "age_31_90"), "all_supported_chains_age_31_90_rolling_30_days", 50),
        ]
    else:
        attempts = [
            (("new_chain", chain_id, str(band or "")), "same_chain_same_age_band_rolling_30_days", int(cohort_rules["newPrimary"]["minimumValidObjects"])),
            (("new_all", "", str(band or "")), "all_supported_chains_same_age_band_rolling_30_days", int(cohort_rules["newSecondary"]["minimumValidObjects"])),
        ]

    selected: list[dict[str, Any]] = []
    scope = "frozen_age_band_fallback"
    largest_attempt = 0
    for key, candidate_scope, minimum in attempts:
        rows = cohort_index.get(key, [])
        largest_attempt = max(largest_attempt, len(rows))
        if len(rows) >= minimum:
            selected = rows
            scope = candidate_scope
            break
    values = lambda name: [float(row[name]) for row in selected if number(row.get(name)) is not None]
    thresholds = {}
    for output, metric, probability in (
        ("liquidityP50", "liquidityUsd", 0.50),
        ("volumeP40", "volumeUsd", 0.40),
        ("volumeP50", "volumeUsd", 0.50),
        ("transactionsP50", "transactions", 0.50),
        ("volumeLiquidityRatioP50", "volumeLiquidityRatio", 0.50),
        ("relativeExpansionP50", "relativeExpansion", 0.50),
    ):
        value = _quantile(values(metric), probability)
        if value is not None:
            thresholds[output] = value
    return {
        "scope": scope,
        "sampleSize": len(selected) if selected else largest_attempt,
        "thresholds": thresholds,
        "metricSampleSizes": {
            metric: len(values(metric))
            for metric in ("liquidityUsd", "volumeUsd", "transactions", "volumeLiquidityRatio", "relativeExpansion")
        },
    }


def _path_input(base: dict[str, Any], market: dict[str, Any], previous_market: dict[str, Any], risk: dict[str, Any], supply: dict[str, Any], previous_supply: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    market_payload = _json(market.get("payload_json"), {}) or {}
    risk_payload = _json(risk.get("payload_json"), {}) or {}
    pool_payload = _json(pool.get("payload_json"), {}) or {}
    previous_liquidity = number(previous_market.get("liquidity_usd"))
    current_liquidity = number(market.get("liquidity_usd"))
    liquidity_drop = None
    if previous_liquidity and current_liquidity is not None:
        liquidity_drop = max(0.0, (previous_liquidity - current_liquidity) / previous_liquidity * 100)
    previous_top10 = number(previous_supply.get("top10_share_pct"))
    current_top10 = number(supply.get("top10_share_pct"))
    previous_hhi = number(previous_supply.get("holder_hhi"))
    current_hhi = number(supply.get("holder_hhi"))
    return {
        **base,
        "observedBuys": market.get("observed_buys"),
        "observedSells": market.get("observed_sells"),
        "volumeUsd": market.get("volume_usd"),
        "transactionCount": market.get("transaction_count"),
        "volumeLiquidityRatio": market.get("volume_liquidity_ratio"),
        "liquidityUsd": market.get("liquidity_usd"),
        "sellQuoteState": market.get("standard_sell_quote_state") or "no_data",
        "sellQuoteLossPct": market.get("standard_sell_quote_loss_pct"),
        "sellQuoteIndependent": bool(market_payload.get("quoteProvider")),
        "liquidityDropPct": liquidity_drop,
        "confirmedHardBlock": confirmed_trade_block(risk),
        "severeAnomaly": confirmed_trade_block(risk),
        "materialCrossSourceConflict": bool(market_payload.get("materialCrossSourceConflict")),
        "crossSourcePriceDeviationPct": market_payload.get("crossSourcePriceDeviationPct", market_payload.get("cross_source_price_deviation_pct")),
        "sellTaxPct": risk.get("sell_tax_pct", risk_payload.get("sellTaxPct", risk_payload.get("sell_tax_pct"))),
        "supplyHistoryState": "success" if previous_supply and supply.get("source_status") == "success" and previous_supply.get("source_status") == "success" else "no_data",
        "supplyUnitScaleStable": pool_payload.get("unitScaleStable") if pool_payload.get("unitScaleStable") is not None else (supply.get("decimals") == previous_supply.get("decimals") if previous_supply else None),
        "top10ShareChangePercentagePoints": current_top10 - previous_top10 if current_top10 is not None and previous_top10 is not None else None,
        "holderHhiChangePct": _delta_pct(previous_hhi, current_hhi),
        "supplyChangePct": _delta_pct(
            _normalized_supply(previous_supply), _normalized_supply(supply)
        ),
        "poolHistoryState": pool.get("source_status") or "no_data",
        "indexedPoolCount": pool.get("indexed_pool_count"),
        "ohlcvSuccessCount": pool.get("ohlcv_success_count"),
        "unindexedDiscoveredPoolCount": pool.get("unindexed_discovered_pool_count"),
        "relativeExpansion": pool.get("relative_expansion"),
        "riskAdjustedSurplus": pool.get("risk_adjusted_surplus"),
    }


def _plain_public_fields(item: dict[str, Any]) -> dict[str, str]:
    formed = [PATH_LABELS.get(row["pathCode"], row["pathCode"]) for row in item.get("strongPaths", []) if row.get("status") == "formed"]
    state = item.get("publicState")
    why = "已完成首轮基础跟踪并达到公开底线。"
    if state == "convexity_clue":
        why = f"已形成{len(formed)}条相互独立的强证据路径。"
        state_reason = f"{len(formed)}条强证据路径已经形成，并满足来源独立要求。"
    elif state == "active_project":
        why = "已达到公开底线，并出现强路径或可核验的近期活动。"
        state_reason = f"已有{len(formed)}条强路径或可核验活动，但尚未同时满足凸性线索的路径数量和来源独立条件。"
    else:
        state_reason = "已经完成公开底线检查，但当前四条强证据路径都尚未形成。"
    evidence = "；".join(formed[:2]) if formed else "100美元卖出报价和项目证据均已完成核验，且没有已确认的硬交易阻断。"
    loss = number(item.get("sellQuoteLossPct"))
    risk = f"100美元标准卖出报价当前损失约{loss:.2f}%；该报价不代表其他金额或未来一定可退出。" if loss is not None else "卖出报价会随市场变化，当前结论不能代表未来退出条件。"
    unavailable = [PATH_LABELS.get(row["pathCode"], row["pathCode"]) for row in item.get("strongPaths", []) if row.get("status") != "formed"]
    watch = f"继续观察{unavailable[0]}能否形成。" if unavailable else "继续观察已形成路径能否在新的完整窗口保持。"
    return {"whyNow": why, "whyState": state_reason, "keyEvidence": evidence, "largestRisk": risk, "nextWatch": watch}


def _source_summary(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(connection, "source_health"):
        return []
    rows = connection.execute(
        """SELECT source_id,
        CASE WHEN source_id='project_website_identity'
               AND status='configuration_missing' AND http_status IN (401,403)
             THEN 'unsupported' ELSE status END status,
        COUNT(*) object_count,SUM(COALESCE(affected_object_count,0)) affected_count,
        MAX(updated_at) updated_at,MAX(last_success_at) last_success_at,
        MAX(CASE WHEN source_id='project_website_identity'
                   AND status='configuration_missing' AND http_status IN (401,403)
                 THEN '项目网站拒绝自动访问，属于来源能力边界；重复更新不会改变。'
                 ELSE plain_reason END) plain_reason
        FROM source_health GROUP BY source_id,
        CASE WHEN source_id='project_website_identity'
               AND status='configuration_missing' AND http_status IN (401,403)
             THEN 'unsupported' ELSE status END
        ORDER BY source_id,status"""
    ).fetchall()
    return [dict(row) for row in rows]


def _job_status(connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    try:
        from c2_2_runtime import status_payload

        payload = status_payload()
        if connection is not None:
            from candidate_production import funnel_status

            cached = payload.get("candidateProduction") or {}
            production = funnel_status(connection)
            for key in (
                "state",
                "workerPid",
                "paused",
                "formalHistoricalScanAuthorized",
                "formalHistoricalScanStarted",
                "gate0Rerun",
                "runtimeBoundary",
            ):
                if key in cached:
                    production[key] = cached[key]
            payload["candidateProduction"] = production
        return payload
    except Exception as error:
        return {"state": "program_failure", "message": f"当前无法读取作业状态：{type(error).__name__}: {error}"}


def _build(
    connection: sqlite3.Connection,
    db_path: Path,
    previous_front: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    generated_at = _now()
    config = load_config()
    active_rule_version = load_active_rule_version(PROJECT_ROOT / "runtime" / "c2.5" / "rule-governance" / "current.json")
    base_rows = [dict(row) for row in connection.execute(
        """SELECT p.*,c.network_id,c.token_address,c.gate0_pool_id,c.canonical_name,c.symbol,
        c.website_domain,c.official_repo,q.source_queue,q.completed_at first_gate_completed_at,
        q.updated_at first_gate_updated_at,q.state first_gate_queue_state
        FROM candidate_production_records p
        JOIN candidates c ON c.candidate_id=p.candidate_id
        LEFT JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id
        WHERE p.market_state='market_confirmed' AND COALESCE(p.asset_id,'')!=''
        ORDER BY p.candidate_id"""
    )]
    data_cutoff = max((str(row.get("updated_at") or "") for row in base_rows), default=generated_at)
    first_gate_history: dict[int, dict[str, Any]] = {}
    current_queue: dict[int, dict[str, Any]] = {}
    for row in base_rows:
        gate = evaluate_first_gate(_candidate_input(row))
        if gate["passed"] and row.get("first_gate_queue_state") == "completed":
            item = _candidate_item(row, gate)
            first_gate_history[int(row["candidate_id"])] = item
            current_queue[int(row["candidate_id"])] = item

    if _table_exists(connection, "c2_4_first_gate_history"):
        for history in connection.execute("SELECT * FROM c2_4_first_gate_history"):
            candidate_id = int(history["candidate_id"])
            if candidate_id in first_gate_history:
                continue
            source = next((row for row in base_rows if int(row["candidate_id"]) == candidate_id), None)
            if source:
                gate = {"state": "passed", "checks": _json(history["checks_json"], []), "ruleVersion": history["rule_version"]}
                first_gate_history[candidate_id] = _candidate_item(source, gate)

    continued_ids: set[int] = set()
    public_history: dict[int, dict[str, Any]] = {}
    lifecycle_history: dict[int, dict[str, Any]] = {}
    if _table_exists(connection, "c2_4_public_history"):
        public_history = {int(row["candidate_id"]): dict(row) for row in connection.execute("SELECT * FROM c2_4_public_history")}
    if _table_exists(connection, "c2_4_lifecycle_state"):
        lifecycle_history = {int(row["candidate_id"]): dict(row) for row in connection.execute("SELECT * FROM c2_4_lifecycle_state")}
        continued_ids = {candidate_id for candidate_id, row in lifecycle_history.items() if row.get("lifecycle_pool") == "continued_91_plus" and not row.get("stopped_at")}
    age_by_candidate = {
        int(row["candidate_id"]): number(row.get("age_days")) for row in base_rows
    }
    new_history_ids = {
        candidate_id
        for candidate_id in first_gate_history
        if age_by_candidate.get(candidate_id) is not None
        and age_by_candidate[candidate_id] <= 90
        and not lifecycle_history.get(candidate_id, {}).get("stopped_at")
    }
    active_public_ids = {
        candidate_id
        for candidate_id, row in public_history.items()
        if bool(int(row.get("public_active", 1) or 0))
    }
    tracked_ids = sorted(
        set(current_queue) | new_history_ids | continued_ids | active_public_ids
    )

    tracking_records = {}
    if _table_exists(connection, "candidate_tracking_records") and tracked_ids:
        for batch in _chunks(tracked_ids):
            placeholders = ",".join("?" for _ in batch)
            tracking_records.update({int(row["candidate_id"]): dict(row) for row in connection.execute(f"SELECT * FROM candidate_tracking_records WHERE candidate_id IN ({placeholders})", tuple(batch))})
    cohort_tracking_records = {}
    if _table_exists(connection, "candidate_tracking_records"):
        cohort_tracking_records = {
            int(row["candidate_id"]): dict(row)
            for row in connection.execute("SELECT * FROM candidate_tracking_records WHERE state='completed'")
        }
    evaluations = {}
    if tracked_ids:
        for batch in _chunks(tracked_ids):
            placeholders = ",".join("?" for _ in batch)
            evaluations.update({
                int(row["candidate_id"]): dict(row)
                for row in connection.execute(
                    f"""SELECT e.* FROM evaluations e
                    JOIN candidate_tracking_records t
                      ON t.candidate_id=e.candidate_id AND t.evaluated_at=e.evaluated_at
                    WHERE e.candidate_id IN ({placeholders})""",
                    tuple(batch),
                )
            })
            for row in connection.execute(
                f"SELECT * FROM evaluations WHERE is_current=1 AND candidate_id IN ({placeholders})",
                tuple(batch),
            ):
                evaluations.setdefault(int(row["candidate_id"]), dict(row))
    observation_ids = sorted(set(tracked_ids) | set(cohort_tracking_records))
    market_rows = _select_for_ids(connection, "market_observations", observation_ids, "observed_at") if observation_ids else []
    risk_rows = _select_for_ids(connection, "risk_observations", observation_ids, "observed_at") if observation_ids else []
    supply_rows = _select_for_ids(connection, "supply_observations", observation_ids, "observed_at") if observation_ids else []
    pool_rows = _select_for_ids(connection, "pool_window_observations", observation_ids, "observed_at") if observation_ids else []
    market_rows = _completed_window_rows(market_rows, cohort_tracking_records)
    risk_rows = _completed_window_rows(risk_rows, cohort_tracking_records)
    supply_rows = _completed_window_rows(supply_rows, cohort_tracking_records)
    pool_rows = _completed_window_rows(pool_rows, cohort_tracking_records)
    market, previous_market = latest_effective_market_rows(market_rows)
    risk, _ = _latest_and_previous(risk_rows)
    supply, previous_supply = _latest_and_previous(supply_rows)
    pool, _ = _latest_and_previous(pool_rows)
    evidence_by_candidate: dict[int, list[dict[str, Any]]] = defaultdict(list)
    evidence_rows: list[dict[str, Any]] = []
    if tracked_ids:
        evidence_rows = _select_for_ids(connection, "product_evidence", tracked_ids, "observed_at")
        evidence_rows = _completed_window_evidence_rows(
            evidence_rows, tracking_records, evaluations
        )
        for row in evidence_rows:
            if row.get("status") == "qualifying":
                evidence_by_candidate[int(row["candidate_id"])].append(row)

    cutoff_values = [
        str(value or "")
        for row in base_rows
        for value in (row.get("updated_at"), row.get("first_gate_completed_at"), row.get("first_gate_updated_at"))
    ]
    cutoff_values.extend(str(row.get("updated_at") or row.get("completed_at") or "") for row in cohort_tracking_records.values())
    cutoff_values.extend(str(row.get("evaluated_at") or "") for row in evaluations.values())
    for rows in (market_rows, risk_rows, supply_rows, pool_rows, evidence_rows):
        cutoff_values.extend(str(row.get("observed_at") or "") for row in rows)
    data_cutoff = max((value for value in cutoff_values if value), default=generated_at)

    base_by_id = {int(row["candidate_id"]): row for row in base_rows}
    path_cohort_index = _build_path_cohort_index(
        base_by_id,
        cohort_tracking_records,
        continued_ids,
        market,
        supply,
        previous_supply,
        pool,
        data_cutoff,
    )
    completed_ids = sorted(cohort_tracking_records)
    bayes_catalog = load_tracking_catalog(db_path, completed_ids) if completed_ids else {}
    cohort_index = build_tracking_cohort_index(bayes_catalog)
    tracking_items = []
    for candidate_id in tracked_ids:
        row = base_by_id.get(candidate_id)
        if not row:
            continue
        candidate = first_gate_history.get(candidate_id) or _candidate_item(row, {"state": "passed", "checks": [], "ruleVersion": "c2.4-first-gate-v1"})
        tracking_record = tracking_records.get(candidate_id, {})
        evaluation = evaluations.get(candidate_id, {})
        source_states = _json(tracking_record.get("source_states_json"), {}) or {}
        evidence = _evidence_summary(evidence_by_candidate.get(candidate_id, []))
        market_row = market.get(candidate_id, {})
        risk_row = risk.get(candidate_id, {})
        risk_reason_codes = _json(risk_row.get("reason_codes_json"), []) or []
        confirmed_block = confirmed_trade_block(risk_row)
        risk_state = risk_row.get("source_status") or source_states.get("risk") or "no_data"
        path_cohort = _path_cohort_thresholds(
            candidate,
            candidate_id,
            continued_ids,
            path_cohort_index,
            config,
        )
        path_input = _path_input(
            {
                "ageDays": candidate.get("ageDays"),
                "cohortThresholds": path_cohort["thresholds"],
            },
            market_row,
            previous_market.get(candidate_id, {}),
            risk_row,
            supply.get(candidate_id, {}),
            previous_supply.get(candidate_id, {}),
            pool.get(candidate_id, {}),
        )
        evaluation_window = evaluation.get("evaluation_window_id") if tracking_record.get("state") == "completed" else None
        deep_state = tracking_record.get("state") or "pending"
        baseline_input = {
            **_candidate_input(row),
            **path_input,
            "relationshipClass": candidate.get("relationshipClass"),
            "deepTrackingState": deep_state,
            "evaluationWindowId": evaluation_window,
            "evaluationCompletedAt": tracking_record.get("completed_at"),
            "riskState": risk_state,
            "riskSourceState": risk_state,
            "projectEvidenceQualified": evidence["qualified"],
            "projectEvidenceAttributable": evidence["attributable"],
            "confirmedFreeze": confirmed_block and any("freeze" in str(code) for code in risk_reason_codes),
            "confirmedBlacklist": confirmed_block and any("blacklist" in str(code) for code in risk_reason_codes),
            "confirmedSellBlock": confirmed_block and any(
                marker in str(code) for code in risk_reason_codes for marker in ("sell", "honeypot")
            ),
            "confirmedSevereAnomaly": bool(path_input.get("severeAnomaly")),
            "riskReasonCodes": [str(code) for code in risk_reason_codes],
            "supplyUnitScaleChanged": path_input.get("supplyUnitScaleStable") is False,
            "supplyDecimalsChanged": path_input.get("supplyUnitScaleStable") is False,
            "supplyUnitChanged": path_input.get("supplyUnitScaleStable") is False,
        }
        baseline = evaluate_public_baseline(baseline_input, active_version=active_rule_version)
        history = public_history.get(candidate_id, {})
        lifecycle_state = lifecycle_history.get(candidate_id, {})
        history_active = bool(history) and bool(int(history.get("public_active", 1) or 0))
        effective_public = bool(baseline["passed"] or (history_active and not lifecycle_state.get("stopped_at")))
        rule_replay_values = {
            **baseline_input,
            "publicEligible": effective_public,
            "strongPathEvaluationEligible": deep_state == "completed",
        }
        paths = evaluate_strong_paths(rule_replay_values, config, active_version=active_rule_version) if deep_state == "completed" else [
            {"pathCode": code, "status": "unavailable", "plainReason": "等待新的完整首轮基础跟踪结果。", "independentSourceTypes": [], "metrics": {}}
            for code in PATH_LABELS
        ]
        bayes = None
        catalog_record = bayes_catalog.get(candidate_id)
        if catalog_record:
            bayes = build_bayes_evidence({"_candidateId": candidate_id, "assetId": candidate["assetId"], "chainId": candidate["chainId"], "ageBand": catalog_record.get("ageBand"), "confidenceSummary": catalog_record.get("evaluation", {}).get("dataConfidence") or {}}, bayes_catalog, cohort_index=cohort_index)
        life = lifecycle_pool({
            "ageDays": candidate.get("ageDays"),
            "firstGatePassedWhileNew": candidate_id in first_gate_history,
            "completeTrackingWhileNew": bool(history),
            "publicBaselinePassedWhileNew": bool(history),
            "stableIdentityStillValid": bool(candidate.get("assetId") and candidate.get("chainId") and candidate.get("tokenAddress") and candidate.get("poolId") and candidate.get("assetDirection") in {"base", "quote"}),
            "confirmedHardBlock": baseline_input.get("confirmedHardBlock"),
            "severeAnomaly": baseline_input.get("severeAnomaly"),
        })
        if lifecycle_state.get("lifecycle_pool"):
            life = {"eligible": not lifecycle_state.get("stopped_at"), "lifecyclePool": lifecycle_state.get("lifecycle_pool"), "reason": "读取已保存的生命周期迁移记录。"}
        total = (bayes or {}).get("total") or {}
        item = {
            **candidate,
            "lifecyclePool": life.get("lifecyclePool") or "new_0_90",
            "continuedTrackingSince": lifecycle_state.get("continued_tracking_since"),
            "trackingState": baseline["trackingState"],
            "deepTrackingState": deep_state,
            "evaluationWindowId": evaluation_window,
            "evaluationCompletedAt": tracking_record.get("completed_at"),
            "latestCompleteTrackingAt": tracking_record.get("completed_at"),
            "riskState": baseline_input["riskState"],
            "sellQuoteState": baseline_input.get("sellQuoteState") or "no_data",
            "sellQuoteLossPct": baseline_input.get("sellQuoteLossPct"),
            "projectEvidenceState": evidence["state"],
            "projectEvidenceBoundary": evidence["boundary"],
            "projectEvidenceTypes": evidence["types"],
            "projectEvidenceSources": evidence["sources"],
            "projectEvidenceLinks": evidence["links"],
            "strongPaths": paths,
            "independentSourceTypes": sorted({source for path in paths for source in path.get("independentSourceTypes", [])}),
            "bayesFactors": (bayes or {}).get("factors") or [],
            "bayesPosterior": total.get("score"),
            "bayesInterval80": total.get("interval80"),
            "independentConfidence": (bayes or {}).get("confidenceScore"),
            "observedMetricCount": total.get("measuredIndicatorCount") or 0,
            "cohortScope": path_cohort["scope"],
            "cohortSampleSize": path_cohort["sampleSize"],
            "cohortMetricSampleSizes": path_cohort["metricSampleSizes"],
            "cohortThresholds": path_cohort["thresholds"],
            "bayesCohortScope": next((value.get("cohortScope") for value in ((bayes or {}).get("indicators") or {}).values() if value.get("cohortScope")), "fallback"),
            "bayesCohortSampleSize": max([int(value.get("cohortSampleSize") or 0) for value in ((bayes or {}).get("indicators") or {}).values()] or [0]),
            "severeAnomaly": bool(baseline_input.get("severeAnomaly")),
            "consecutiveCompletedMisses": int(lifecycle_state.get("consecutive_completed_misses") or 0),
            "sourceStates": source_states,
            "evidenceIds": sorted(set(evidence["evidenceIds"] + [value for value in (market_row.get("observation_id"), risk_row.get("observation_id"), supply.get(candidate_id, {}).get("observation_id"), pool.get(candidate_id, {}).get("observation_id")) if value])),
            "publicEligible": effective_public,
            "publicBaselinePassedThisWindow": baseline["passed"],
            "publicRetentionState": "current_window_passed" if baseline["passed"] else "retained_by_hysteresis" if effective_public else "not_public",
            "publicBaseline": baseline,
            "ruleReplayInputs": build_rule_replay_inputs(
                rule_replay_values,
                active_rule_version=active_rule_version,
            ),
            "market": {key: path_input.get(key) for key in ("liquidityUsd", "volumeUsd", "transactionCount", "observedBuys", "observedSells", "volumeLiquidityRatio", "sellQuoteState", "sellQuoteLossPct", "liquidityDropPct")},
            "recentQualifyingRepositoryActivity": evidence["recentRepositoryActivity"],
            "newVerifiedProductUsage": evidence["newProductUsage"],
        }
        state = determine_public_state(item, paths)
        non_project_states = {"quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure"}
        if effective_public and not baseline["passed"] and (
            deep_state != "completed" or any(value in non_project_states for value in source_states.values())
        ):
            saved_state = history.get("last_public_state") or "observing"
            state = {
                "publicState": saved_state,
                "convexityClue": saved_state == "convexity_clue",
                "formedPathCount": sum(path.get("status") == "formed" for path in paths),
                "formedPathCodes": sorted(path["pathCode"] for path in paths if path.get("status") == "formed"),
                "independentSourceTypes": sorted({source for path in paths for source in path.get("independentSourceTypes", [])}),
            }
        item.update(state)
        tracking_items.append(item)

    public_items = [
        {key: value for key, value in item.items() if key != "ruleReplayInputs"}
        for item in tracking_items
        if item.get("publicEligible") and item.get("publicState")
    ]
    home_by_chain = rank_home_by_chain(public_items, maximum=10)
    for item in public_items:
        item.update(_plain_public_fields(item))
        item["dataCutoffAt"] = data_cutoff
        item["detailHref"] = f"project-detail.html?assetId={item['assetId']}"
    changes = _important_changes(previous_front, public_items, data_cutoff, tracking_items)

    history_items = sorted(first_gate_history.values(), key=lambda row: row["assetId"])
    queue_items = sorted(current_queue.values(), key=lambda row: row["assetId"])
    candidate_payload = _finalize({
        "schemaVersion": "c2.4-candidate-snapshot-v1", "generatedAt": generated_at,
        "dataCutoffAt": data_cutoff, "producer": "90_day_candidate_initial_screen",
        "ruleVersion": "c2.4-first-gate-v1", "firstGatePassedHistory": history_items,
        "firstGateQueue": queue_items,
        "counts": {"firstGatePassedHistory": len(history_items), "firstGateQueue": len(queue_items)},
    }, "c24-candidate")
    tracking_payload = _finalize({
        "schemaVersion": "c2.4-tracking-snapshot-v1", "generatedAt": generated_at,
        "dataCutoffAt": data_cutoff, "producer": "convexity_deep_tracking",
        "ruleVersion": active_rule_version, "items": tracking_items,
        "stateCounts": dict(Counter(item["trackingState"] for item in tracking_items)),
        "lifecycleCounts": dict(Counter(item["lifecyclePool"] for item in tracking_items)),
    }, "c24-tracking")
    public_counts = dict(Counter(item["publicState"] for item in public_items))
    lifecycle_counts = dict(Counter(item["lifecyclePool"] for item in public_items))
    chain_counts = {chain: sum(item["chainId"] == chain for item in public_items) for chain in CHAIN_ORDER}
    home_assets = {chain: [item["assetId"] for item in rows] for chain, rows in home_by_chain.items()}
    front_payload = _finalize({
        "schemaVersion": "c2.4-public-snapshot-v1", "generatedAt": generated_at,
        "dataCutoffAt": data_cutoff, "producer": "convexity_tracking_publication",
        "items": public_items, "allOpportunities": [item["assetId"] for item in public_items],
        "homeTop10": home_assets, "changes": changes, "chainOrder": list(CHAIN_ORDER),
        "chainLabels": CHAIN_LABELS, "publicStateCounts": public_counts,
        "lifecycleCounts": lifecycle_counts, "chainCounts": chain_counts,
    }, "c24-public")

    history_assets = {item["assetId"] for item in history_items}
    queue_assets = {item["assetId"] for item in queue_items}
    tracking_assets = {item["assetId"] for item in tracking_items}
    public_assets = {item["assetId"] for item in public_items}
    continued_missing_history = sorted(item["assetId"] for item in tracking_items if item["lifecyclePool"] == "continued_91_plus" and item["assetId"] not in history_assets)
    reconciliation = {
        "inputCandidateCount": connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],
        "firstGatePassedHistoryCount": len(history_assets), "firstGateQueueCount": len(queue_assets),
        "trackingCount": len(tracking_assets), "publicCount": len(public_assets),
        "chainPublicCounts": chain_counts, "lifecyclePublicCounts": lifecycle_counts,
        "publicStateCounts": public_counts, "homeDerivedCount": sum(len(rows) for rows in home_assets.values()),
        "unrankedPublicCount": sum(not item.get("rankingAvailable") for item in public_items),
        "differences": {
            "publicNotTracked": sorted(public_assets - tracking_assets),
            "newTrackedNotQueued": sorted(item["assetId"] for item in tracking_items if item["lifecyclePool"] == "new_0_90" and item["assetId"] not in queue_assets),
            "trackedNotFirstGateHistory": sorted(tracking_assets - history_assets),
            "continuedMissingHistory": continued_missing_history,
        },
    }
    if any(reconciliation["differences"].values()):
        raise ValueError(f"C2.4集合对账失败：{reconciliation['differences']}")
    if len(public_assets) != len(public_items) or len(tracking_assets) != len(tracking_items) or len(queue_assets) != len(queue_items):
        raise ValueError("C2.4集合存在重复assetId，拒绝发布快照。")
    if public_assets != set(front_payload["allOpportunities"]):
        raise ValueError("全部机会不等于公开快照，拒绝发布。")

    production = connection.execute(
        """SELECT COUNT(*) production_records,
        SUM(CASE WHEN market_state='market_confirmed' THEN 1 ELSE 0 END) market_confirmed,
        SUM(CASE WHEN tracking_eligible=1 THEN 1 ELSE 0 END) tracking_eligible,
        SUM(CASE WHEN local_state<>'local_pass'
                  AND NOT (market_state='market_confirmed' AND COALESCE(asset_id,'')!='')
                 THEN 1 ELSE 0 END) local_not_passed,
        SUM(CASE WHEN local_state='local_pass' AND market_state='market_not_indexed' THEN 1 ELSE 0 END) market_not_indexed,
        SUM(CASE WHEN local_state='local_pass' AND market_state='waiting_for_trades' THEN 1 ELSE 0 END) waiting_for_trades,
        SUM(CASE WHEN local_state='local_pass' AND market_state='source_pending' THEN 1 ELSE 0 END) source_pending,
        SUM(CASE WHEN local_state='local_pass' AND market_state IS NULL THEN 1 ELSE 0 END) market_not_checked
        FROM candidate_production_records"""
    ).fetchone()
    total_candidates = int(reconciliation["inputCandidateCount"])
    market_confirmed_count = len(base_rows)
    pre_market_reasons = [
        {
            "code": "local_scope_not_passed",
            "label": "不在 0—90 天范围，或属于已知延续、报价/封装资产",
            "count": int(production["local_not_passed"] or 0),
            "kind": "not_passed",
        },
        {
            "code": "public_market_not_indexed",
            "label": "没有找到可核验的公开交易池",
            "count": int(production["market_not_indexed"] or 0),
            "kind": "not_passed",
        },
        {
            "code": "public_buy_or_sell_missing",
            "label": "公开池尚未同时观察到买入和卖出",
            "count": int(production["waiting_for_trades"] or 0),
            "kind": "not_passed",
        },
        {
            "code": "market_source_retry",
            "label": "市场来源失败，保留断点等待重试",
            "count": int(production["source_pending"] or 0),
            "kind": "waiting",
        },
    ]
    classified_before_market = market_confirmed_count + sum(row["count"] for row in pre_market_reasons)
    not_yet_processed = max(0, total_candidates - classified_before_market)
    pre_market_reasons.append({
        "code": "not_yet_processed",
        "label": "尚未形成完整处理记录",
        "count": not_yet_processed,
        "kind": "waiting",
    })
    pre_market_not_passed = sum(row["count"] for row in pre_market_reasons if row["kind"] == "not_passed")
    pre_market_waiting = sum(row["count"] for row in pre_market_reasons if row["kind"] == "waiting")

    first_gate_failure_labels = {
        "t0_age": "T0 未核验或已不在 0—90 天范围",
        "stable_asset_identity": "链、合约、交易池、资产方向或 assetId 不完整",
        "public_buy_and_sell": "未同时观察到至少 1 笔买入和 1 笔卖出",
        "no_confirmed_trade_block": "已确认冻结、黑名单或卖出阻断",
    }
    first_gate_failure_order = tuple(first_gate_failure_labels)
    first_gate_failure_counts: Counter[str] = Counter()
    first_gate_waiting_counts: Counter[str] = Counter()
    for row in base_rows:
        gate = evaluate_first_gate(_candidate_input(row))
        failed_codes = {check["code"] for check in gate["checks"] if not check["passed"]}
        if "t0_age" in failed_codes:
            first_gate_failure_counts["t0_age"] += 1
            continue
        if row.get("first_gate_queue_state") == "completed":
            if not gate["passed"]:
                primary = next((code for code in first_gate_failure_order if code in failed_codes), "other")
                first_gate_failure_counts[primary] += 1
            continue
        reason_code = str(row.get("tracking_reason_code") or "")
        if reason_code == "first_gate_not_passed":
            first_gate_waiting_counts["scheduled_recheck"] += 1
        elif reason_code == "public_pool_with_buy_and_sell":
            first_gate_waiting_counts["first_check_pending"] += 1
        else:
            first_gate_waiting_counts["completion_record_missing"] += 1
    first_gate_not_passed = sum(first_gate_failure_counts.values())
    first_gate_waiting = max(0, market_confirmed_count - len(queue_assets) - first_gate_not_passed)
    waiting_difference = first_gate_waiting - sum(first_gate_waiting_counts.values())
    if waiting_difference > 0:
        first_gate_waiting_counts["completion_record_missing"] += waiting_difference
    first_gate_primary_reasons = [
        {
            "code": code,
            "label": first_gate_failure_labels.get(code, "其他第一关规则未通过"),
            "count": count,
            "kind": "not_passed",
        }
        for code, count in first_gate_failure_counts.items()
        if count
    ] + [
        {
            "code": code,
            "label": {
                "first_check_pending": "已确认公开买卖，尚待执行四项第一关",
                "scheduled_recheck": "旧结果已排入重新核验队列",
                "completion_record_missing": "尚未形成第一关完成记录",
            }[code],
            "count": count,
            "kind": "waiting",
        }
        for code, count in first_gate_waiting_counts.items()
        if count
    ]

    completed_tracking_items = [item for item in tracking_items if item["deepTrackingState"] == "completed"]
    structure_eligible_items = [
        item for item in completed_tracking_items
        if item.get("relationshipClass") in {"A", "B", "C"}
        and item.get("projectEvidenceState") == "qualifying"
        and item.get("sellQuoteState") == "success"
        and not item.get("severeAnomaly")
    ]
    structure_attempted_items = [
        item for item in structure_eligible_items if int(item["candidateId"]) in pool
    ]
    structure_usable_items = [
        item for item in structure_attempted_items
        if pool.get(int(item["candidateId"]), {}).get("source_status") == "success"
    ]
    deep_waiting_counts: Counter[str] = Counter(
        str(item.get("deepTrackingState") or "pending")
        for item in tracking_items
        if item.get("deepTrackingState") != "completed"
    )
    deep_waiting_labels = {
        "pending": "尚未完成首轮基础跟踪",
        "partial": "来源未完成，保留断点等待重试",
        "failed": "本轮程序未完成，等待修复后重试",
    }
    deep_waiting_reasons = [
        {
            "code": f"deep_{code}",
            "label": deep_waiting_labels.get(code, "尚未形成完整首轮基础跟踪结果"),
            "count": count,
            "kind": "waiting",
        }
        for code, count in deep_waiting_counts.items()
        if count
    ]
    completed_not_public = [item for item in completed_tracking_items if item["assetId"] not in public_assets]
    public_failure_labels = {
        "risk_complete": "发现已确认的冻结、黑名单或卖出阻断",
        "sell_quote": "100 美元标准卖出报价未成功",
        "project_evidence": "缺少可程序归属的项目证据",
        "stable_identity": "T0 或稳定资产身份不完整",
        "public_relationship": "项目关系属于不公开的 D 类",
        "complete_deep_result": "首轮基础跟踪结果不完整",
    }
    public_failure_order = tuple(public_failure_labels)
    public_failure_counts: Counter[str] = Counter()
    for item in completed_not_public:
        failed_codes = {check["code"] for check in item["publicBaseline"]["checks"] if not check["passed"]}
        primary = next((code for code in public_failure_order if code in failed_codes), "other")
        public_failure_counts[primary] += 1
    public_primary_reasons = [
        {
            "code": code,
            "label": public_failure_labels.get(code, "其他公开底线未通过"),
            "count": count,
            "kind": "not_passed",
        }
        for code, count in public_failure_counts.items()
        if count
    ]
    continued_public_count = sum(item["lifecyclePool"] == "continued_91_plus" for item in public_items)
    new_public_count = max(0, len(public_items) - continued_public_count)
    partitions = [dict(row) for row in connection.execute(
        """SELECT queue_name,state,COUNT(*) partition_count,SUM(total_count) candidate_count,
        SUM(processed_count) processed_count,MAX(last_heartbeat_at) last_heartbeat_at
        FROM candidate_scan_partitions GROUP BY queue_name,state ORDER BY queue_name,state"""
    )] if _table_exists(connection, "candidate_scan_partitions") else []
    evidence_counts = [dict(row) for row in connection.execute(
        "SELECT evidence_type,status,COUNT(*) count FROM product_evidence GROUP BY evidence_type,status ORDER BY evidence_type,status"
    )] if _table_exists(connection, "product_evidence") else []
    recent_evidence = [dict(row) for row in connection.execute(
        """SELECT pe.evidence_id,pe.candidate_id,pe.evidence_type,pe.status,pe.source_name,
        pe.source_url,pe.observed_at,pe.boundary_note,p.asset_id,c.canonical_name,c.network_id
        FROM product_evidence pe JOIN candidates c ON c.candidate_id=pe.candidate_id
        LEFT JOIN candidate_production_records p ON p.candidate_id=pe.candidate_id
        WHERE pe.status='qualifying' ORDER BY pe.observed_at DESC LIMIT 50"""
    )] if _table_exists(connection, "product_evidence") else []
    recent_runs = [dict(row) for row in connection.execute(
        "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 10"
    )] if _table_exists(connection, "pipeline_runs") else []
    chain_funnel = {
        chain: {
            "firstGateQueue": sum(item["chainId"] == chain for item in queue_items),
            "tracking": sum(item["chainId"] == chain for item in tracking_items),
            "public": sum(item["chainId"] == chain for item in public_items),
        }
        for chain in CHAIN_ORDER
    }
    admin_payload = _finalize({
        "schemaVersion": "c2.4-admin-snapshot-v1", "generatedAt": generated_at,
        "dataCutoffAt": data_cutoff, "producer": "c2_4_admin_projection",
        "reconciliation": reconciliation,
        "candidateFunnel": {
            "foundCandidates": total_candidates,
            "completedBaseChecks": market_confirmed_count,
            "enteredDeepTracking": len(queue_assets),
            "waitingOrRetrying": first_gate_waiting + first_gate_not_passed,
            "partitions": partitions,
            "stages": [
                {"code": "all_candidates", "label": "已找到候选", "count": total_candidates},
                {"code": "market_confirmed", "label": "市场与交易已确认", "count": market_confirmed_count},
                {"code": "first_gate_passed", "label": "通过第一关，进入凸性跟踪", "count": len(queue_assets)},
            ],
            "transitions": [
                {
                    "from": "all_candidates", "to": "market_confirmed", "kind": "screening",
                    "title": "第 1 步：90 天范围与公开市场基础检查",
                    "rules": [
                        "只保留 T0 可核验且当前处于 0—90 天的资产",
                        "排除已知延续资产、报价资产和封装资产",
                        "必须找到可核验公开交易池，并观察到买入与卖出",
                    ],
                    "passed": market_confirmed_count,
                    "notPassed": pre_market_not_passed,
                    "waiting": pre_market_waiting,
                    "primaryReasons": [row for row in pre_market_reasons if row["count"]],
                    "manualAction": "等待项由历史底座和来源重试自动继续；手动更新不能跳过 T0 或公开市场规则。",
                },
                {
                    "from": "market_confirmed", "to": "first_gate_passed", "kind": "screening",
                    "title": "第 2 步：四项第一关核验",
                    "rules": [
                        "T0 已核验且年龄仍在 0—90 天",
                        "链、合约、交易池、资产方向和 assetId 完整",
                        "至少观察到 1 笔买入和 1 笔卖出",
                        "没有已确认的冻结、黑名单或卖出阻断",
                    ],
                    "passed": len(queue_assets),
                    "notPassed": first_gate_not_passed,
                    "waiting": first_gate_waiting,
                    "primaryReasons": first_gate_primary_reasons,
                    "manualAction": "等待项可由下方“立即更新”或“继续上次未完成”推动，但仍必须完成四项核验。",
                },
            ],
            "outsideFunnel": [
                {
                    "code": "first_gate_waiting", "label": "等待第一关核验",
                    "count": first_gate_waiting, "kind": "waiting",
                    "detail": "这部分尚未形成第一关完成结果，不是被规则淘汰。",
                    "manualAction": "历史底座运行时会自动处理；也可使用下方现有更新按钮推动同一断点，不能跳过核验。",
                },
                {
                    "code": "first_gate_not_passed", "label": "第一关本轮未通过",
                    "count": first_gate_not_passed, "kind": "not_passed",
                    "detail": "这部分已经完成第一关，但至少一项规则未通过。每个项目只归入最先未通过的一项，避免重复计数。",
                    "manualAction": "不是等待任务；后续真实数据变化时会在新的增量周期重新核验。",
                },
            ],
        },
        "trackingFunnel": {
            "received": len(tracking_items),
            "publicBaselineChecked": len(completed_tracking_items),
            "structureTracked": len(structure_attempted_items),
            "structureUsable": len(structure_usable_items),
            "published": len(public_items),
            "continued91Plus": continued_public_count,
            "backendObservation": sum(item["trackingState"] == "backend_observation" for item in tracking_items),
            "waitingSourceRetry": sum(item["trackingState"] == "waiting_source_retry" for item in tracking_items),
            "waitingPublicBaseline": sum(item["trackingState"] == "waiting_public_baseline" for item in tracking_items),
            "stages": [
                {"code": "received", "label": "收到第一关候选", "count": len(tracking_items)},
                {"code": "deep_tracking_completed", "label": "完成首轮基础跟踪", "count": len(completed_tracking_items)},
                {"code": "published", "label": "达到公开底线并发布", "count": len(public_items)},
                {"code": "continued_91_plus", "label": "90 天后持续跟踪", "count": continued_public_count, "kind": "lifecycle"},
            ],
            "transitions": [
                {
                    "from": "received", "to": "deep_tracking_completed", "kind": "processing",
                    "title": "第 1 步：完成公开所需的首轮基础跟踪",
                    "rules": [
                        "完成市场、100 美元卖出报价、项目证据和显性硬交易阻断检查",
                        "全池结构与历史供应属于后续增强证据，不阻塞首轮队列和公开结果",
                    ],
                    "passed": len(completed_tracking_items),
                    "notPassed": 0,
                    "waiting": len(tracking_items) - len(completed_tracking_items),
                    "primaryReasons": deep_waiting_reasons,
                    "manualAction": "这些是尚未完成的处理队列，不是被凸性规则淘汰；可用下方更新按钮从同一断点继续。",
                },
                {
                    "from": "deep_tracking_completed", "to": "published", "kind": "publication",
                    "title": "第 2 步：公开底线与凸性结构判断",
                    "rules": [
                        "没有已确认的冻结、黑名单或卖出阻断；风险来源缺失不冒充安全，也不直接淘汰",
                        "100 美元标准卖出报价成功；损失比例只记录，不作为当前门槛",
                        "至少一项项目证据可程序归属，身份仍有效且关系属于 A/B/C",
                        "四条强路径与贝叶斯结果只决定分类和排序，不替代公开底线",
                    ],
                    "passed": len(public_items),
                    "notPassed": len(completed_not_public),
                    "waiting": 0,
                    "primaryReasons": public_primary_reasons,
                    "manualAction": "每个未公开项目只归入最先未通过的一项；后续完整窗口会按真实新数据重新判断。",
                },
                {
                    "from": "published", "to": "continued_91_plus", "kind": "lifecycle",
                    "title": "第 3 步：第 91 天生命周期迁移",
                    "rules": [
                        "项目曾在 0—90 天内通过两关并达到公开底线",
                        "同一 assetId 到第 91 天后自动转入持续跟踪",
                    ],
                    "passed": continued_public_count,
                    "notPassed": 0,
                    "waiting": new_public_count,
                    "primaryReasons": ([{
                        "code": "not_reached_day_91", "label": "当前仍处于 0—90 天，不是被淘汰",
                        "count": new_public_count, "kind": "waiting",
                    }] if new_public_count else []),
                    "manualAction": "不能手动提前到第 91 天；达到真实时间后由系统自动迁移，不需要重新筛选。",
                },
            ],
            "outsideFunnel": [
                {
                    "code": "deep_tracking_waiting", "label": "尚未完成首轮基础跟踪",
                    "count": len(tracking_items) - len(completed_tracking_items), "kind": "waiting",
                    "detail": "仍在第二关首轮基础处理队列，不代表项目已被排除。",
                    "manualAction": "可使用下方“立即更新”或“继续上次未完成”推动同一断点。",
                },
                {
                    "code": "completed_not_public", "label": "已完成但本轮未公开",
                    "count": len(completed_not_public), "kind": "not_passed",
                    "detail": "至少一项公开底线未通过，但项目仍保留在跟踪集合中。",
                    "manualAction": "后续完整窗口有新数据时自动重新判断，不需要人工放行。",
                },
                {
                    "code": "post_baseline_structure_enrichment",
                    "label": "公开后继续补充全池结构与历史供应",
                    "count": max(0, len(structure_eligible_items) - len(structure_attempted_items)),
                    "kind": "enhancement",
                    "detail": "这是独立增强证据，不属于公开漏斗，也不会把已公开项目退回等待。",
                    "manualAction": "首轮队列完成后由同一可恢复作业从独立断点继续；无需重复运行旧主干。",
                },
            ],
        },
        "chainFunnel": chain_funnel,
        "trackingStateCounts": dict(Counter(item["trackingState"] for item in tracking_items)),
        "publicStateCounts": public_counts,
        "evidenceSummary": {"counts": evidence_counts, "recentQualifying": recent_evidence},
        "ruleSummary": {
            "ruleVersion": "c2.4-public-baseline-quote-success-trial-v1",
            "factorWeights": config["bayes"]["factorWeights"],
            "sellQuote": {
                "notionalUsd": 100,
                "requiredState": "success",
                "lossPercentageUse": "informational_only_not_a_current_gate",
            },
            "firstGateChecks": config["firstGate"]["requiredChecks"],
            "publicBaselineChecks": [
                "completed_baseline_tracking_result",
                "no_confirmed_freeze_blacklist_or_sell_block",
                "standard_sell_quote_100_usd_success",
                "attributable_project_evidence_and_stable_identity",
                "relationship_class_A_B_or_C",
            ],
            "activeTrialRecord": "docs/C2.4_RULE_RELAXATION_TRIAL_20260813.json",
        },
        "recentRuns": recent_runs,
        "sourceHealth": _source_summary(connection), "jobs": _job_status(connection),
        "database": {"candidatePath": "data/c2.1-pipeline.db", "candidateBytes": db_path.stat().st_size, "mainPath": "data/convexity.db", "mainBytes": (PROJECT_ROOT / "data" / "convexity.db").stat().st_size},
        "routeInventory": _json((PROJECT_ROOT / "docs" / "C2.4_INHERITANCE_MANIFEST.json").read_text(encoding="utf-8"), {}).get("routes", []),
    }, "c24-admin")
    return {"candidate": candidate_payload, "tracking": tracking_payload, "front": front_payload, "admin": admin_payload}


def validate_payloads(payloads: dict[str, dict[str, Any]]) -> None:
    for name, payload in payloads.items():
        for field in ("schemaVersion", "buildId", "generatedAt", "dataCutoffAt", "producer", "isComplete", "contentSha256"):
            if not payload.get(field):
                raise ValueError(f"{name}快照缺少{field}")
        content = {key: value for key, value in payload.items() if key not in {"buildId", "contentSha256"}}
        if _hash(content) != payload["contentSha256"]:
            raise ValueError(f"{name}快照内容哈希不一致")
    if payloads["front"]["dataCutoffAt"] != payloads["tracking"]["dataCutoffAt"] or payloads["tracking"]["dataCutoffAt"] != payloads["candidate"]["dataCutoffAt"]:
        raise ValueError("C2.4三份业务快照截止时间不一致")


def _write_group(output_dir: Path, payloads: dict[str, dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    specs = (
        (DEFAULT_CANDIDATE.name, CANDIDATE_PREFIX, payloads["candidate"]),
        (DEFAULT_TRACKING.name, TRACKING_PREFIX, payloads["tracking"]),
        (DEFAULT_FRONT.name, FRONT_PREFIX, payloads["front"]),
        (DEFAULT_ADMIN.name, ADMIN_PREFIX, payloads["admin"]),
    )
    backup_dir = PROJECT_ROOT / "runtime" / "c2.4" / "snapshot-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    temporary = []
    backups = []
    committed = []
    try:
        for name, prefix, payload in specs:
            target = output_dir / name
            temp = target.with_name(target.name + ".tmp")
            text = prefix + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + ";\n"
            json.loads(text[len(prefix) : -2])
            temp.write_text(text, encoding="utf-8")
            temporary.append((temp, target))
        for _temp, target in temporary:
            backup = backup_dir / f"{target.name}.previous"
            if backup.exists():
                backup.unlink()
            if target.exists():
                os.replace(target, backup)
                backups.append((target, backup))
        for temp, target in temporary:
            os.replace(temp, target)
            committed.append(target)
    except Exception:
        for temp, _target in temporary:
            if temp.exists():
                temp.unlink()
        for target in committed:
            if target.exists():
                target.unlink()
        for target, backup in reversed(backups):
            if backup.exists():
                os.replace(backup, target)
        raise


def build_snapshots(db_path: Path = DEFAULT_DB_PATH, output_dir: Path = APP_ROOT, write: bool = True) -> dict[str, dict[str, Any]]:
    db_path = Path(db_path)
    output_dir = Path(output_dir)
    previous_front = _read_snapshot(
        output_dir / DEFAULT_FRONT.name,
        FRONT_PREFIX,
        "c2.4-public-snapshot-v1",
    )
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        payloads = _build(connection, db_path, previous_front)
        connection.rollback()
    finally:
        connection.close()
    validate_payloads(payloads)
    if write:
        _write_group(Path(output_dir), payloads)
    return payloads


def main() -> int:
    parser = argparse.ArgumentParser(description="构建C2.4候选、跟踪、公开和后台原子快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=APP_ROOT)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    payloads = build_snapshots(args.db, args.output_dir, write=not args.check_only)
    print(json.dumps({
        "candidateBuildId": payloads["candidate"]["buildId"],
        "trackingBuildId": payloads["tracking"]["buildId"],
        "publicBuildId": payloads["front"]["buildId"],
        "firstGateQueue": len(payloads["candidate"]["firstGateQueue"]),
        "tracking": len(payloads["tracking"]["items"]),
        "public": len(payloads["front"]["items"]),
        "reconciliation": payloads["admin"]["reconciliation"],
        "wrote": not args.check_only,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
