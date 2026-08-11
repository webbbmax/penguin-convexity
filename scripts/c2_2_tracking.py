#!/usr/bin/env python3
"""Read-only C2.2 tracking inputs and connect them to the Bayes layer."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from c2_2_bayes import (
    FACTOR_INDICATORS,
    Prior,
    Posterior,
    WindowObservation,
    factor_posterior,
    independent_confidence,
    metric_z,
    observation_weight,
    posterior_update,
    posterior_difference_probability,
    robust_prior,
    total_evidence_score,
)


INDICATOR_CONFIG = {
    "volume": ("nonnegative", "positive"),
    "trade_count": ("nonnegative", "positive"),
    "volume_liquidity": ("nonnegative", "positive"),
    "real_buy_sell": ("nonnegative", "positive"),
    "relative_liquidity": ("proportion", "positive"),
    "standard_sell_loss_inverse": ("proportion", "negative"),
    "liquidity_retention": ("proportion", "positive"),
    "top10_concentration_inverse": ("proportion", "negative"),
    "hhi_inverse": ("proportion", "negative"),
    "net_supply_reduction": ("proportion", "positive"),
    "product_usage_growth": ("nonnegative", "positive"),
    "relative_expansion": ("raw", "positive"),
    "risk_adjusted_remaining": ("raw", "positive"),
    "severe_anomaly_inverse": ("proportion", "positive"),
    "cross_source_consistency": ("proportion", "positive"),
    "activity_concentration_inverse": ("proportion", "negative"),
}


def _json(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _json_value(value: str | None) -> Any:
    try:
        return json.loads(value or "null")
    except json.JSONDecodeError:
        return None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _iso(value: Any) -> str:
    return str(value or "")


def _latest(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        candidate_id = int(row["candidate_id"])
        current = result.get(candidate_id)
        if current is None or _iso(row.get("observed_at")) > _iso(current.get("observed_at")):
            result[candidate_id] = row
    return result


def _series(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Keep every real observation, ordered for the detail history contract."""

    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            candidate_id = int(row["candidate_id"])
        except (KeyError, TypeError, ValueError):
            continue
        result[candidate_id].append(row)
    for values in result.values():
        values.sort(key=lambda row: (_iso(row.get("observed_at")), _iso(row.get("observation_id"))))
    return dict(result)


def _public_market(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "observedAt": row.get("observed_at"),
        "sourceName": row.get("source_name"),
        "sourceStatus": row.get("source_status"),
        "pairAddress": row.get("pair_address"),
        "pairCreatedAt": row.get("pair_created_at"),
        "tokenSide": row.get("token_side"),
        "liquidityUsd": _finite(row.get("liquidity_usd")),
        "fdvUsd": _finite(row.get("fdv_usd")),
        "marketCapUsd": _finite(row.get("market_cap_usd")),
        "volumeUsd": _finite(row.get("volume_usd")),
        "transactionCount": _finite(row.get("transaction_count")),
        "observedBuys": _finite(row.get("observed_buys")),
        "observedSells": _finite(row.get("observed_sells")),
        "volumeLiquidityRatio": _finite(row.get("volume_liquidity_ratio")),
        "priceUsd": _finite(row.get("price_usd")),
        "standardSellNotionalUsd": _finite(row.get("standard_sell_notional_usd")),
        "standardSellQuoteState": row.get("standard_sell_quote_state"),
        "standardSellQuoteLossPct": _finite(row.get("standard_sell_quote_loss_pct")),
        "observationId": row.get("observation_id"),
    }


def _public_supply(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "observedAt": row.get("observed_at"),
        "sourceName": row.get("source_name"),
        "sourceStatus": row.get("source_status"),
        "supplyRaw": row.get("supply_raw"),
        "decimals": row.get("decimals"),
        "top10SharePct": _finite(row.get("top10_share_pct")),
        "holderHhi": _finite(row.get("holder_hhi")),
        "observationId": row.get("observation_id"),
    }


def _public_pool(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "observedAt": row.get("observed_at"),
        "sourceName": row.get("source_name"),
        "sourceStatus": row.get("source_status"),
        "indexedPoolCount": row.get("indexed_pool_count"),
        "ohlcvSuccessCount": row.get("ohlcv_success_count"),
        "unindexedDiscoveredPoolCount": row.get("unindexed_discovered_pool_count"),
        "relativeExpansion": _finite(row.get("relative_expansion")),
        "riskAdjustedSurplus": _finite(row.get("risk_adjusted_surplus")),
        "observationId": row.get("observation_id"),
    }


def _public_risk(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "observedAt": row.get("observed_at"),
        "sourceName": row.get("source_name"),
        "sourceStatus": row.get("source_status"),
        "hardTradeBlock": row.get("hard_trade_block"),
        "severeAnomaly": _finite(row.get("severe_anomaly")),
        "reasonCodes": _json_value(row.get("reason_codes_json")) or [],
        "observationId": row.get("observation_id"),
    }


def _candidate_id(project_id: str | None) -> int | None:
    text = str(project_id or "")
    if text.startswith("c21-"):
        try:
            return int(text.split("-", 1)[1])
        except ValueError:
            return None
    return None


def _supply_reduction(pool: dict[str, Any] | None) -> float | None:
    payload = _json((pool or {}).get("payload_json"))
    previous = _finite(payload.get("previousSupplyRaw"))
    current = _finite(payload.get("currentSupplyRaw"))
    if previous is None or current is None or previous <= 0:
        return None
    return (previous - current) / previous


def _row_metrics(market: dict[str, Any] | None, risk: dict[str, Any] | None, supply: dict[str, Any] | None, pool: dict[str, Any] | None, source_count: int) -> dict[str, float | None]:
    market = market or {}
    risk = risk or {}
    supply = supply or {}
    pool = pool or {}
    liquidity = _finite(market.get("liquidity_usd"))
    market_cap = _finite(market.get("market_cap_usd"))
    loss = _finite(market.get("standard_sell_quote_loss_pct"))
    top10 = _finite(supply.get("top10_share_pct"))
    hhi = _finite(supply.get("holder_hhi"))
    severe = _finite(risk.get("severe_anomaly"))
    return {
        "volume": _finite(market.get("volume_usd")),
        "trade_count": _finite(market.get("transaction_count")),
        "volume_liquidity": _finite(market.get("volume_liquidity_ratio")),
        "real_buy_sell": min(value for value in (_finite(market.get("observed_buys")), _finite(market.get("observed_sells"))) if value is not None) if _finite(market.get("observed_buys")) is not None and _finite(market.get("observed_sells")) is not None else None,
        "relative_liquidity": liquidity / market_cap if liquidity is not None and market_cap and market_cap > 0 else None,
        "standard_sell_loss_inverse": loss / 100.0 if loss is not None else None,
        "liquidity_retention": None,
        "top10_concentration_inverse": 1.0 - top10 / 100.0 if top10 is not None else None,
        "hhi_inverse": 1.0 - hhi if hhi is not None else None,
        "net_supply_reduction": _supply_reduction(pool),
        "product_usage_growth": None,
        "relative_expansion": _finite(pool.get("relative_expansion")),
        "risk_adjusted_remaining": _finite(pool.get("risk_adjusted_surplus")),
        "severe_anomaly_inverse": 1.0 - severe if severe is not None else None,
        "cross_source_consistency": min(1.0, source_count / 2.0) if source_count else None,
        "activity_concentration_inverse": None,
    }


def load_tracking_catalog(db_path: Path) -> dict[int, dict[str, Any]]:
    """Load only current C2.1 observations through a read-only SQLite URI."""

    db_path = Path(db_path)
    if not db_path.exists():
        return {}
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        eval_rows = [dict(row) for row in connection.execute("""
            SELECT e.candidate_id,e.age_band,e.evaluated_at,c.network_id,
                   c.token_address,c.mapped_asset_id,c.canonical_name,c.symbol
            FROM evaluations e JOIN candidates c ON c.candidate_id=e.candidate_id
            WHERE e.is_current=1
        """)]
        candidate_ids = {int(row["candidate_id"]) for row in eval_rows}
        if not candidate_ids:
            return {}
        placeholders = ",".join("?" for _ in candidate_ids)
        args = tuple(sorted(candidate_ids))
        market_rows = [dict(row) for row in connection.execute(f"SELECT * FROM market_observations WHERE candidate_id IN ({placeholders})", args)]
        risk_rows = [dict(row) for row in connection.execute(f"SELECT * FROM risk_observations WHERE candidate_id IN ({placeholders})", args)]
        supply_rows = [dict(row) for row in connection.execute(f"SELECT * FROM supply_observations WHERE candidate_id IN ({placeholders})", args)]
        pool_rows = [dict(row) for row in connection.execute(f"SELECT * FROM pool_window_observations WHERE candidate_id IN ({placeholders})", args)]
        market = _latest(market_rows)
        risk = _latest(risk_rows)
        supply = _latest(supply_rows)
        pool = _latest(pool_rows)
        market_series = _series(market_rows)
        risk_series = _series(risk_rows)
        supply_series = _series(supply_rows)
        pool_series = _series(pool_rows)
        catalog: dict[int, dict[str, Any]] = {}
        for evaluation in eval_rows:
            candidate_id = int(evaluation["candidate_id"])
            observations = [market.get(candidate_id), risk.get(candidate_id), supply.get(candidate_id), pool.get(candidate_id)]
            source_names = {str(row.get("source_name")) for row in observations if row and row.get("source_name")}
            metric_observations: list[dict[str, Any]] = []
            for row in market_series.get(candidate_id, []):
                metric_observations.append({
                    "observedAt": row.get("observed_at") or "",
                    "observationId": row.get("observation_id") or "",
                    "sourceName": row.get("source_name") or "",
                    "sourceStatus": row.get("source_status") or "no_data",
                    "sourceCount": 1,
                    "metrics": _row_metrics(row, None, None, None, 1),
                })
            for row in risk_series.get(candidate_id, []):
                metric_observations.append({
                    "observedAt": row.get("observed_at") or "",
                    "observationId": row.get("observation_id") or "",
                    "sourceName": row.get("source_name") or "",
                    "sourceStatus": row.get("source_status") or "no_data",
                    "sourceCount": 1,
                    "metrics": _row_metrics(None, row, None, None, 1),
                })
            for row in supply_series.get(candidate_id, []):
                metric_observations.append({
                    "observedAt": row.get("observed_at") or "",
                    "observationId": row.get("observation_id") or "",
                    "sourceName": row.get("source_name") or "",
                    "sourceStatus": row.get("source_status") or "no_data",
                    "sourceCount": 1,
                    "metrics": _row_metrics(None, None, row, None, 1),
                })
            for row in pool_series.get(candidate_id, []):
                metric_observations.append({
                    "observedAt": row.get("observed_at") or "",
                    "observationId": row.get("observation_id") or "",
                    "sourceName": row.get("source_name") or "",
                    "sourceStatus": row.get("source_status") or "no_data",
                    "sourceCount": 1,
                    "metrics": _row_metrics(None, None, None, row, 1),
                })
            metric_observations.sort(key=lambda row: (_iso(row.get("observedAt")), _iso(row.get("observationId"))))
            catalog[candidate_id] = {
                "candidateId": candidate_id,
                "networkId": evaluation.get("network_id") or "",
                "ageBand": evaluation.get("age_band") or "",
                "evaluatedAt": evaluation.get("evaluated_at") or "",
                "tokenAddress": evaluation.get("token_address") or "",
                "mainAssetId": evaluation.get("mapped_asset_id") or "",
                "canonicalName": evaluation.get("canonical_name") or "",
                "symbol": evaluation.get("symbol") or "",
                "sourceCount": len(source_names),
                "metrics": _row_metrics(market.get(candidate_id), risk.get(candidate_id), supply.get(candidate_id), pool.get(candidate_id), len(source_names)),
                "metricObservations": metric_observations,
                "observedAt": max((_iso(row.get("observed_at")) for row in observations if row), default=""),
                "series": {
                    "market": [_public_market(row) for row in market_series.get(candidate_id, [])],
                    "supply": [_public_supply(row) for row in supply_series.get(candidate_id, [])],
                    "pool": [_public_pool(row) for row in pool_series.get(candidate_id, [])],
                    "risk": [_public_risk(row) for row in risk_series.get(candidate_id, [])],
                },
            }
        return catalog
    finally:
        connection.close()


def load_main_database_facts(db_path: Path, asset_ids: set[str] | list[str]) -> dict[str, dict[str, Any]]:
    """Read supplementary legacy-main facts without changing their ownership.

    C2.2 tracking observations remain sourced from the candidate pipeline.  The
    main convexity database is read here only to attach an exact asset lineage
    and the latest existing market/risk/tradeability facts.  No name or symbol
    matching is attempted, and an absent asset is explicitly reported.
    """

    requested = {str(value).strip() for value in asset_ids if str(value).strip()}
    if not requested or not Path(db_path).exists():
        return {}
    connection = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in requested)
        args = tuple(sorted(requested))
        assets = {
            str(row["asset_id"]): dict(row)
            for row in connection.execute(f"SELECT * FROM assets WHERE asset_id IN ({placeholders})", args)
        }
        cases: dict[str, dict[str, Any]] = {}
        for row in connection.execute(f"""
            SELECT * FROM candidate_cases
            WHERE asset_id IN ({placeholders})
            ORDER BY updated_at DESC, case_id DESC
        """, args):
            cases.setdefault(str(row["asset_id"]), dict(row))
        markets: dict[str, dict[str, Any]] = {}
        for row in connection.execute(f"""
            SELECT * FROM market_snapshots
            WHERE asset_id IN ({placeholders})
            ORDER BY observed_at DESC, snapshot_id DESC
        """, args):
            asset_id = str(row["asset_id"])
            markets.setdefault(asset_id, dict(row))
        risks: dict[str, dict[str, Any]] = {}
        for row in connection.execute(f"""
            SELECT * FROM contract_risks
            WHERE asset_id IN ({placeholders})
            ORDER BY assessed_at DESC, contract_risk_id DESC
        """, args):
            asset_id = str(row["asset_id"])
            risks.setdefault(asset_id, dict(row))
        contracts: dict[str, dict[str, Any]] = {}
        for row in connection.execute(f"""
            SELECT * FROM asset_contracts
            WHERE asset_id IN ({placeholders})
            ORDER BY updated_at DESC, asset_contract_id DESC
        """, args):
            asset_id = str(row["asset_id"])
            contracts.setdefault(asset_id, dict(row))
        tradeability: dict[str, dict[str, Any]] = {}
        contract_ids = tuple(str(row["asset_contract_id"]) for row in contracts.values() if row.get("asset_contract_id") is not None)
        if contract_ids:
            tc_placeholders = ",".join("?" for _ in contract_ids)
            for row in connection.execute(f"""
                SELECT * FROM tradeability_checks
                WHERE asset_contract_id IN ({tc_placeholders})
                ORDER BY checked_at DESC, check_id DESC
            """, contract_ids):
                tradeability.setdefault(str(row["asset_contract_id"]), dict(row))
        result: dict[str, dict[str, Any]] = {}
        for asset_id in requested:
            asset = assets.get(asset_id)
            contract = contracts.get(asset_id)
            trade = tradeability.get(str(contract["asset_contract_id"])) if contract and contract.get("asset_contract_id") is not None else None
            matched = any((asset, cases.get(asset_id), markets.get(asset_id), risks.get(asset_id), contract, trade))
            result[asset_id] = {
                "matched": matched,
                "assetId": asset_id,
                "matchMethod": "asset_id" if matched else None,
                "asset": asset,
                "candidateCase": cases.get(asset_id),
                "market": markets.get(asset_id),
                "contractRisk": risks.get(asset_id),
                "assetContract": contract,
                "tradeability": trade,
                "reason": None if matched else "主库没有同 assetId 事实；不按名称、Symbol或数组顺序继承。",
            }
        return result
    finally:
        connection.close()


def _prior_and_z(value: float | None, item: dict[str, Any], indicator: str, catalog: dict[int, dict[str, Any]]) -> tuple[Prior, float | None, str, int]:
    kind, direction = INDICATOR_CONFIG[indicator]
    candidate_id = int(item.get("_candidateId") or 0)
    current = catalog.get(candidate_id) or {}
    network = current.get("networkId") or item.get("chainId") or ""
    age_band = current.get("ageBand") or item.get("ageBand") or ""
    same_chain = [row for row in catalog.values() if row.get("networkId") == network and row.get("ageBand") == age_band]
    same_age = [row for row in catalog.values() if row.get("ageBand") == age_band]
    cohort_rows = same_chain if len(same_chain) >= 20 else same_age if len(same_age) >= 50 else []
    fallback = not cohort_rows
    cohort_values = [row.get("metrics", {}).get(indicator) for row in cohort_rows]
    cohort_values = [row for row in cohort_values if _finite(row) is not None]
    current_values = [row.get("metrics", {}).get(indicator) for row in cohort_rows]
    z = metric_z(value, cohort_values + [value] if value is not None else cohort_values, kind=kind, direction=direction) if value is not None and cohort_values else None
    # A cohort percentile needs the current value and comparison values, but the
    # prior is estimated from the comparison z values only.
    cohort_z = [metric_z(candidate, cohort_values, kind=kind, direction=direction) for candidate in cohort_values] if cohort_values else []
    cohort_z = [float(candidate) for candidate in cohort_z if candidate is not None]
    prior = robust_prior(cohort_z, fallback=fallback)
    scope = "same_chain_same_age_band_30d" if same_chain and len(same_chain) >= 20 else "same_age_band_six_chains_30d" if same_age and len(same_age) >= 50 else "fallback"
    return prior, z, scope, len(cohort_rows)


def build_bayes_evidence(item: dict[str, Any], catalog: dict[int, dict[str, Any]], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate_id = _candidate_id(item.get("projectId"))
    item_for_calc = {**item, "_candidateId": candidate_id}
    record = catalog.get(candidate_id or -1, {})
    indicator_posteriors: dict[str, Posterior] = {}
    indicator_payload: dict[str, Any] = {}
    for factor in FACTOR_INDICATORS.values():
        for indicator in factor:
            value = (record.get("metrics") or {}).get(indicator)
            prior, z, scope, sample_size = _prior_and_z(value, item_for_calc, indicator, catalog)
            observations: list[WindowObservation] = []
            historical = list(record.get("metricObservations") or [])
            if historical:
                for index, observation in enumerate(historical):
                    observed_value = (observation.get("metrics") or {}).get(indicator)
                    if observed_value is None:
                        continue
                    _obs_prior, observation_z, _obs_scope, _obs_sample = _prior_and_z(observed_value, item_for_calc, indicator, catalog)
                    weight = observation_weight(
                        observation.get("sourceStatus") or "no_data",
                        independent_source_count=int(observation.get("sourceCount") or 0),
                        comparable=observation_z is not None,
                    )
                    if observation_z is None or weight <= 0:
                        continue
                    evidence_id = f"{item.get('assetId')}|{indicator}|{observation.get('observedAt') or index}|{observation.get('observationId') or index}|c2.2-bayes-v1"
                    observations.append(WindowObservation(evidence_id, observation_z, weight))
            elif z is not None:
                weight = observation_weight("healthy" if value is not None else "no_data", independent_source_count=int(record.get("sourceCount") or 0), comparable=value is not None)
                observed_at = record.get("observedAt") or item.get("dataCutoffAt") or ""
                if weight > 0:
                    observations.append(WindowObservation(f"{item.get('assetId')}|{indicator}|{observed_at}|c2.2-bayes-v1", z, weight))
            posterior = posterior_update(prior, observations)
            indicator_posteriors[indicator] = posterior
            indicator_payload[indicator] = {
                "mean": posterior.mean,
                "variance": posterior.variance,
                "score": posterior.score,
                "interval80": posterior.interval80,
                "priorSource": posterior.prior_source,
                "cohortScope": scope,
                "cohortSampleSize": sample_size,
                "measuredObservations": posterior.measured_observations,
                "effectiveWeight": posterior.effective_weight,
                "duplicateObservations": posterior.duplicate_observations,
            }
    previous_factors = {}
    for row in (previous or {}).get("factors", []):
        if row.get("factor"):
            previous_factors[row["factor"]] = Posterior(float(row.get("mean", 0)), float(row.get("variance", 1)), 0.0, "cohort", int(row.get("measuredIndicatorCount", 0)), 0.0, 0)
    factors = {}
    for factor in FACTOR_INDICATORS:
        aggregate = factor_posterior(factor, indicator_posteriors)
        previous = previous_factors.get(factor)
        factor_direction = aggregate.direction
        if aggregate.measured_indicator_count == 0:
            factor_direction = "no_measured"
        elif previous is not None:
            probability = posterior_difference_probability(aggregate.posterior, previous)
            factor_direction = "improving" if probability >= 0.80 else "weakening" if probability <= 0.20 else "stable"
        factors[factor] = {
            "factor": factor,
            "mean": aggregate.posterior.mean,
            "variance": aggregate.posterior.variance,
            "score": aggregate.posterior.score,
            "interval80": aggregate.posterior.interval80,
            "direction": factor_direction,
            "measuredIndicatorCount": aggregate.measured_indicator_count,
            "indicatorCount": aggregate.indicator_count,
        }
    total = total_evidence_score({factor: factor_posterior(factor, indicator_posteriors) for factor in FACTOR_INDICATORS})
    components = (item.get("confidenceSummary") or {}).get("components") or {}
    confidence_score = independent_confidence(*(float(components.get(key, 0) or 0) * 100 for key in ("fieldCoverage", "dataFreshness", "realHistoryCoverage", "crossSourceConsistency")))
    return {
        "indicators": indicator_payload,
        "factors": list(factors.values()),
        "total": {"mean": total.mean, "variance": total.variance, "score": total.score, "interval80": total.interval80, "priorSource": total.prior_source, "measuredIndicatorCount": total.measured_observations, "effectiveWeight": total.effective_weight},
        "confidenceScore": confidence_score,
        "modelVersion": "c2.2-deterministic-hierarchical-eb-v1",
        "ruleVersion": "c2.2-bayes-v1",
        "inputCandidateId": candidate_id,
        "inputSource": "data/c2.1-pipeline.db:current_and_historical_observations",
    }
