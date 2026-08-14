#!/usr/bin/env python3
"""Independent, read-only C2.4 core-set and public-state audit.

The expected sets and public states are recomputed from SQLite records and the
frozen JSON rule configuration.  This module deliberately does not import the
C2.4 implementation, snapshot builder, or C2.2 qualification helpers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "data" / "c2.1-pipeline.db"
RULES = ROOT / "docs" / "C2.4_RULE_CONFIG.json"
SNAPSHOTS = {
    "candidate": ROOT / "app" / "c2-4-candidate-snapshot.js",
    "tracking": ROOT / "app" / "c2-4-tracking-snapshot.js",
    "public": ROOT / "app" / "c2-4-front-snapshot.js",
    "admin": ROOT / "app" / "c2-4-admin-snapshot.js",
}
DEFAULT_OUTPUT = ROOT / "reports" / "c2.4-independent-acceptance" / "core-audit.json"
PUBLIC_STATES = {"convexity_clue", "active_project", "observing"}


def load_snapshot(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if "=" not in text:
        raise ValueError(f"invalid snapshot wrapper: {path}")
    return json.loads(text.split("=", 1)[1].strip().rstrip(";"))


def content_hash(payload: dict[str, Any]) -> str:
    content = {key: value for key, value in payload.items() if key not in {"buildId", "contentSha256"}}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) and value else value if value is not None else default
    except (TypeError, json.JSONDecodeError):
        return default


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def age_band(age_days: int) -> str:
    if age_days <= 2:
        return "age_0_2"
    if age_days <= 6:
        return "age_3_6"
    if age_days <= 13:
        return "age_7_13"
    if age_days <= 30:
        return "age_14_30"
    return "age_31_90"


def parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def quantile(values: list[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values if number(value) is not None)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    point = (len(ordered) - 1) * probability
    lower = int(point)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = point - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def independent_path_cohorts(
    connection: sqlite3.Connection,
    completed_rows: list[dict[str, Any]],
    continued_candidate_ids: set[int],
    config: dict[str, Any],
    cutoff: str,
) -> dict[int, dict[str, Any]]:
    cutoff_time = parse_time(cutoff) or datetime.now(timezone.utc)
    oldest = cutoff_time - timedelta(days=30)
    groups: dict[tuple[str, str, str], list[dict[str, float | None]]] = {}
    for row in completed_rows:
        candidate_id = int(row["candidate_id"])
        completed_at = parse_time(row.get("completed_at"))
        if completed_at is None or completed_at < oldest or completed_at > cutoff_time:
            continue
        chain_id = str(row.get("network_id") or "")
        is_continued = candidate_id in continued_candidate_ids
        band = "continued_91_plus" if is_continued else age_band(int(row["age_days"]))
        market, _ = latest_two(connection, "market_observations", candidate_id, str(row["completed_at"]))
        supply, previous_supply = latest_two(connection, "supply_observations", candidate_id, str(row["completed_at"]))
        pool, _ = latest_two(connection, "pool_window_observations", candidate_id, str(row["completed_at"]))
        market_time = parse_time(market.get("observed_at"))
        market_usable = market.get("source_status") == "success" and market_time is not None and oldest <= market_time <= cutoff_time
        metrics: dict[str, float | None] = {
            "liquidityUsd": number(market.get("liquidity_usd")) if market_usable else None,
            "volumeUsd": number(market.get("volume_usd")) if market_usable else None,
            "transactions": number(market.get("transaction_count")) if market_usable else None,
            "volumeLiquidityRatio": number(market.get("volume_liquidity_ratio")) if market_usable else None,
            "relativeExpansion": None,
        }
        pool_time = parse_time(pool.get("observed_at"))
        pool_payload = parse_json(pool.get("payload_json"), {}) or {}
        scale_stable = pool_payload.get("unitScaleStable")
        if scale_stable is None and previous_supply:
            scale_stable = supply.get("decimals") == previous_supply.get("decimals")
        pool_usable = bool(
            pool.get("source_status") == "success"
            and pool_time is not None
            and oldest <= pool_time <= cutoff_time
            and number(pool.get("indexed_pool_count")) is not None
            and number(pool.get("indexed_pool_count")) == number(pool.get("ohlcv_success_count"))
            and supply.get("source_status") == "success"
            and previous_supply.get("source_status") == "success"
            and scale_stable is True
        )
        if pool_usable:
            metrics["relativeExpansion"] = number(pool.get("relative_expansion"))
        if not any(value is not None for value in metrics.values()):
            continue
        keys = (
            (("continued_chain", chain_id, band), ("continued_all", "", band))
            if is_continued
            else (("new_chain", chain_id, band), ("new_all", "", band))
        )
        for key in keys:
            groups.setdefault(key, []).append(metrics)

    result = {}
    cohort_rules = config["cohortPercentiles"]
    for row in completed_rows:
        candidate_id = int(row["candidate_id"])
        chain_id = str(row.get("network_id") or "")
        is_continued = candidate_id in continued_candidate_ids
        if is_continued:
            attempts = [
                (("continued_chain", chain_id, "continued_91_plus"), "same_chain_continued_91_plus_rolling_30_days", int(cohort_rules["continuedPrimary"]["minimumValidObjects"])),
                (("continued_all", "", "continued_91_plus"), "all_supported_chains_continued_91_plus_rolling_30_days", int(cohort_rules["continuedSecondary"]["minimumValidObjects"])),
                (("new_chain", chain_id, "age_31_90"), "same_chain_age_31_90_rolling_30_days", 20),
                (("new_all", "", "age_31_90"), "all_supported_chains_age_31_90_rolling_30_days", 50),
            ]
        else:
            band = age_band(int(row["age_days"]))
            attempts = [
                (("new_chain", chain_id, band), "same_chain_same_age_band_rolling_30_days", int(cohort_rules["newPrimary"]["minimumValidObjects"])),
                (("new_all", "", band), "all_supported_chains_same_age_band_rolling_30_days", int(cohort_rules["newSecondary"]["minimumValidObjects"])),
            ]
        selected: list[dict[str, float | None]] = []
        scope = "frozen_age_band_fallback"
        largest_attempt = 0
        for key, candidate_scope, minimum in attempts:
            rows = groups.get(key, [])
            largest_attempt = max(largest_attempt, len(rows))
            if len(rows) >= minimum:
                selected = rows
                scope = candidate_scope
                break
        values = lambda name: [float(item[name]) for item in selected if number(item.get(name)) is not None]
        thresholds = {}
        for output, metric, probability in (
            ("liquidityP50", "liquidityUsd", 0.50),
            ("volumeP40", "volumeUsd", 0.40),
            ("volumeP50", "volumeUsd", 0.50),
            ("transactionsP50", "transactions", 0.50),
            ("volumeLiquidityRatioP50", "volumeLiquidityRatio", 0.50),
            ("relativeExpansionP50", "relativeExpansion", 0.50),
        ):
            value = quantile(values(metric), probability)
            if value is not None:
                thresholds[output] = value
        result[candidate_id] = {
            "scope": scope,
            "sampleSize": len(selected) if selected else largest_attempt,
            "thresholds": thresholds,
        }
    return result


def latest_two(
    connection: sqlite3.Connection,
    table: str,
    candidate_id: int,
    cutoff: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = [
        dict(row)
        for row in connection.execute(
            f"""SELECT * FROM {table}
            WHERE candidate_id=? AND observed_at<=?
            ORDER BY observed_at DESC, observation_id DESC""",
            (candidate_id, cutoff),
        )
    ]
    return (rows[0] if rows else {}, rows[1] if len(rows) > 1 else {})


def selected_evaluation(
    connection: sqlite3.Connection,
    candidate_id: int,
    evaluated_at: str | None,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM evaluations WHERE candidate_id=? AND evaluated_at=?",
        (candidate_id, evaluated_at),
    ).fetchone()
    if row is None:
        row = connection.execute(
            "SELECT * FROM evaluations WHERE candidate_id=? AND is_current=1",
            (candidate_id,),
        ).fetchone()
    return dict(row) if row else {}


def referenced_evidence_ids(evaluation: dict[str, Any]) -> set[str]:
    hard_gate = parse_json(evaluation.get("hard_gate_json"), {}) or {}
    for check in hard_gate.get("checks") or []:
        if check.get("code") == "product_evidence_present" and check.get("status") == "pass":
            return {str(value) for value in check.get("evidenceIds") or [] if value}
    return set()


def public_assessment(
    connection: sqlite3.Connection,
    row: dict[str, Any],
    config: dict[str, Any],
    cohort: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = int(row["candidate_id"])
    completed_at = str(row.get("completed_at") or "")
    evaluation = selected_evaluation(connection, candidate_id, row.get("evaluated_at"))
    evidence_refs = referenced_evidence_ids(evaluation)
    market, previous_market = latest_two(connection, "market_observations", candidate_id, completed_at)
    risk, _ = latest_two(connection, "risk_observations", candidate_id, completed_at)
    supply, previous_supply = latest_two(connection, "supply_observations", candidate_id, completed_at)
    pool, _ = latest_two(connection, "pool_window_observations", candidate_id, completed_at)

    evidence_rows = []
    for evidence_row in connection.execute(
        "SELECT * FROM product_evidence WHERE candidate_id=?",
        (candidate_id,),
    ):
        evidence = dict(evidence_row)
        if str(evidence.get("observed_at") or "") <= completed_at or str(evidence.get("evidence_id") or "") in evidence_refs:
            evidence_rows.append(evidence)
    qualifying_evidence = [evidence for evidence in evidence_rows if evidence.get("status") == "qualifying"]
    evidence_ok = any(
        evidence.get("identity_status") in {"verified", "market_matched"}
        for evidence in qualifying_evidence
    )

    quote_loss = number(market.get("standard_sell_quote_loss_pct"))
    identity_ok = bool(
        row.get("asset_id")
        and row.get("network_id")
        and row.get("token_address")
        and row.get("pair_address")
        and row.get("token_side") in {"base", "quote"}
        and row.get("t0_status") == "verified_in_supported_scope"
    )
    baseline_pass = bool(
        row.get("state") == "completed"
        and evaluation.get("evaluation_window_id")
        and completed_at
        and risk.get("source_status") == "success"
        and not risk.get("hard_trade_block")
        and not risk.get("severe_anomaly")
        and market.get("standard_sell_quote_state") == "success"
        and quote_loss is not None
        and quote_loss <= config["sellQuote"]["publicMaximumLossPct"]
        and evidence_ok
        and identity_ok
        and row.get("relationship_class") in {"A", "B", "C"}
    )
    result = {
        "assetId": row.get("asset_id"),
        "baselinePass": baseline_pass,
        "quoteLossPct": quote_loss,
        "evidenceAttributable": evidence_ok,
        "publicState": None,
        "formedPaths": [],
        "independentSourceTypes": [],
    }
    if not baseline_pass:
        return result

    thresholds = config["ageBands"][age_band(int(row["age_days"]))]
    p40 = thresholds["fallbackP40"]
    p50 = thresholds["fallbackP50"]
    actual = cohort.get("thresholds") or {}
    liquidity_p50 = number(actual.get("liquidityP50"))
    volume_p40 = number(actual.get("volumeP40"))
    volume_p50 = number(actual.get("volumeP50"))
    transactions_p50 = number(actual.get("transactionsP50"))
    ratio_p50 = number(actual.get("volumeLiquidityRatioP50"))
    relative_p50 = number(actual.get("relativeExpansionP50"))
    liquidity_p50 = p50["liquidityUsd"] if liquidity_p50 is None else liquidity_p50
    volume_p40 = p40["volumeUsd"] if volume_p40 is None else volume_p40
    volume_p50 = p50["volumeUsd"] if volume_p50 is None else volume_p50
    transactions_p50 = p50["transactions"] if transactions_p50 is None else transactions_p50
    ratio_p50 = p50["volumeLiquidityRatio"] if ratio_p50 is None else ratio_p50
    relative_p50 = p50["relativeExpansion"] if relative_p50 is None else relative_p50
    market_payload = parse_json(market.get("payload_json"), {}) or {}
    pool_payload = parse_json(pool.get("payload_json"), {}) or {}
    volume = number(market.get("volume_usd"))
    transactions = number(market.get("transaction_count"))
    volume_liquidity = number(market.get("volume_liquidity_ratio"))
    buys = number(market.get("observed_buys"))
    sells = number(market.get("observed_sells"))
    liquidity = number(market.get("liquidity_usd"))
    previous_liquidity = number(previous_market.get("liquidity_usd"))
    liquidity_drop = (
        max(0.0, (previous_liquidity - liquidity) / previous_liquidity * 100)
        if previous_liquidity not in {None, 0} and liquidity is not None
        else None
    )
    severe = bool(risk.get("severe_anomaly"))
    cross_source_conflict = bool(market_payload.get("materialCrossSourceConflict"))

    demand_ready = buys is not None and sells is not None and any(
        value is not None for value in (volume, transactions, volume_liquidity)
    )
    demand_formed = bool(
        demand_ready
        and buys >= 1
        and sells >= 1
        and any(
            value is not None and value >= minimum
            for value, minimum in (
                (volume, volume_p50),
                (transactions, transactions_p50),
                (volume_liquidity, ratio_p50),
            )
        )
        and not severe
        and not cross_source_conflict
    )
    quote_independent = bool(market_payload.get("quoteProvider"))
    exit_ready = bool(
        liquidity is not None
        and quote_loss is not None
        and liquidity_drop is not None
        and market.get("standard_sell_quote_state") == "success"
    )
    exit_formed = bool(
        exit_ready
        and liquidity >= max(liquidity_p50, thresholds["liquidityFloorUsd"])
        and quote_loss <= 10
        and liquidity_drop < 80
        and not risk.get("hard_trade_block")
    )

    current_top10 = number(supply.get("top10_share_pct"))
    previous_top10 = number(previous_supply.get("top10_share_pct"))
    top10_change = (
        current_top10 - previous_top10
        if current_top10 is not None and previous_top10 is not None
        else None
    )
    current_hhi = number(supply.get("holder_hhi"))
    previous_hhi = number(previous_supply.get("holder_hhi"))
    hhi_change = (
        (current_hhi / previous_hhi - 1) * 100
        if previous_hhi not in {None, 0} and current_hhi is not None
        else None
    )
    current_supply = number(supply.get("supply_raw"))
    previous_supply_value = number(previous_supply.get("supply_raw"))
    supply_change = (
        (current_supply / previous_supply_value - 1) * 100
        if previous_supply_value not in {None, 0} and current_supply is not None
        else None
    )
    unit_scale_stable = pool_payload.get("unitScaleStable")
    if unit_scale_stable is None and previous_supply:
        unit_scale_stable = supply.get("decimals") == previous_supply.get("decimals")
    supply_ready = bool(
        previous_supply
        and supply.get("source_status") == "success"
        and previous_supply.get("source_status") == "success"
        and unit_scale_stable is True
        and volume is not None
    )
    supply_formed = bool(
        supply_ready
        and volume >= volume_p40
        and any(
            (
                top10_change is not None and top10_change <= -2,
                hhi_change is not None and hhi_change <= -5,
                supply_change is not None and supply_change <= -0.25,
            )
        )
    )

    relative_expansion = number(pool.get("relative_expansion"))
    risk_adjusted_surplus = number(pool.get("risk_adjusted_surplus"))
    indexed_pools = number(pool.get("indexed_pool_count"))
    comparable_pools = number(pool.get("ohlcv_success_count"))
    pool_ready = bool(
        pool.get("source_status") == "success"
        and indexed_pools is not None
        and comparable_pools is not None
        and indexed_pools == comparable_pools
        and previous_supply
        and supply.get("source_status") == "success"
        and previous_supply.get("source_status") == "success"
        and unit_scale_stable is True
        and relative_expansion is not None
        and risk_adjusted_surplus is not None
    )
    pool_formed = bool(
        pool_ready
        and relative_expansion >= relative_p50
        and risk_adjusted_surplus > 0
        and not severe
    )

    formed_paths = []
    source_types = set()
    if demand_formed:
        formed_paths.append("trade_demand_formation")
        source_types.add("market_pool_data")
    if exit_formed:
        formed_paths.append("liquidity_exit_quality")
        source_types.add("market_pool_data")
        if quote_independent:
            source_types.add("sell_quote_or_verified_route")
    if supply_formed:
        formed_paths.append("supply_holder_improvement")
        source_types.add("direct_chain_historical_supply")
    if pool_formed:
        formed_paths.append("indexed_pool_activity_vs_supply_adjusted_valuation")
        source_types.update({"indexed_pool_history", "direct_chain_historical_supply"})

    codes = set(formed_paths)
    market_pair_only = codes == {"trade_demand_formation", "liquidity_exit_quality"}
    clue = bool(
        len(formed_paths) >= 2
        and codes & {"trade_demand_formation", "liquidity_exit_quality"}
        and len(source_types) >= 2
        and (not market_pair_only or "sell_quote_or_verified_route" in source_types)
    )
    recent_repository_activity = any(
        evidence.get("evidence_type") == "github"
        and bool((parse_json(evidence.get("payload_json"), {}) or {}).get("recentNonDocumentationCommit"))
        for evidence in qualifying_evidence
    )
    verified_product_usage = any(
        evidence.get("evidence_type") == "product_usage"
        for evidence in qualifying_evidence
    )
    result.update(
        {
            "publicState": (
                "convexity_clue"
                if clue
                else "active_project"
                if formed_paths or recent_repository_activity or verified_product_usage
                else "observing"
            ),
            "formedPaths": formed_paths,
            "independentSourceTypes": sorted(source_types),
            "cohortScope": cohort.get("scope"),
            "cohortSampleSize": cohort.get("sampleSize"),
            "cohortThresholds": cohort.get("thresholds") or {},
        }
    )
    return result


def diff(left: set[str], right: set[str]) -> dict[str, Any]:
    values = sorted(left - right)
    return {"count": len(values), "sample": values[:20]}


def main() -> int:
    parser = argparse.ArgumentParser(description="C2.4 independent read-only audit")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    config = json.loads(RULES.read_text(encoding="utf-8"))
    snapshots = {name: load_snapshot(path) for name, path in SNAPSHOTS.items()}
    cutoffs = {payload.get("dataCutoffAt") for payload in snapshots.values()}
    if len(cutoffs) != 1:
        raise ValueError(f"snapshot cutoffs differ: {sorted(cutoffs)}")
    cutoff = str(next(iter(cutoffs)))

    connection = sqlite3.connect(f"file:{DATABASE.as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    try:
        base_rows = [
            dict(row)
            for row in connection.execute(
                """SELECT p.candidate_id,p.asset_id,p.t0_status,p.age_days,p.pair_address,p.token_side,
                p.observed_buys,p.observed_sells,p.confirmed_hard_block,p.updated_at,
                c.network_id,c.token_address,q.state,q.updated_at AS queue_updated_at
                FROM candidate_production_records p
                JOIN candidates c ON c.candidate_id=p.candidate_id
                LEFT JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id
                WHERE p.market_state='market_confirmed' AND COALESCE(p.asset_id,'')<>''
                  AND p.updated_at<=?""",
                (cutoff,),
            )
        ]
        expected_queue = {
            str(row["asset_id"])
            for row in base_rows
            if row.get("t0_status") == "verified_in_supported_scope"
            and row.get("age_days") is not None
            and 0 <= int(row["age_days"]) <= 90
            and row.get("network_id")
            and row.get("token_address")
            and row.get("pair_address")
            and row.get("token_side") in {"base", "quote"}
            and (number(row.get("observed_buys")) or 0) >= 1
            and (number(row.get("observed_sells")) or 0) >= 1
            and not row.get("confirmed_hard_block")
            and row.get("state") == "completed"
            and (not row.get("queue_updated_at") or str(row["queue_updated_at"]) <= cutoff)
        }
        persisted_history = {
            str(row[0])
            for row in connection.execute(
                "SELECT asset_id FROM c2_4_first_gate_history WHERE passed_at<=?",
                (cutoff,),
            )
        }
        expected_history = expected_queue | persisted_history
        continued_rows = [
            dict(row)
            for row in connection.execute(
                """SELECT candidate_id,asset_id FROM c2_4_lifecycle_state
                WHERE lifecycle_pool='continued_91_plus' AND stopped_at IS NULL AND updated_at<=?""",
                (cutoff,),
            )
        ]
        continued = {str(row["asset_id"]) for row in continued_rows}
        continued_candidate_ids = {int(row["candidate_id"]) for row in continued_rows}
        active_history = {
            str(row["asset_id"]): dict(row)
            for row in connection.execute(
                """SELECT * FROM c2_4_public_history
                WHERE public_active=1 AND last_public_at<=?""",
                (cutoff,),
            )
        }
        expected_tracking = expected_queue | continued | set(active_history)

        completed_rows = [
            dict(row)
            for row in connection.execute(
                """SELECT p.*,c.network_id,c.token_address,t.state,t.evaluated_at,t.completed_at
                FROM candidate_tracking_records t
                JOIN candidate_production_records p ON p.candidate_id=t.candidate_id
                JOIN candidates c ON c.candidate_id=t.candidate_id
                WHERE t.state='completed' AND t.completed_at<=?""",
                (cutoff,),
            )
        ]
        path_cohorts = independent_path_cohorts(
            connection,
            completed_rows,
            continued_candidate_ids,
            config,
            cutoff,
        )
        assessments = {
            str(row["asset_id"]): public_assessment(
                connection,
                row,
                config,
                path_cohorts.get(int(row["candidate_id"]), {}),
            )
            for row in completed_rows
        }
        baseline_public = {
            asset_id for asset_id, assessment in assessments.items() if assessment["baselinePass"]
        }
        expected_public = baseline_public | set(active_history)
    finally:
        connection.rollback()
        connection.close()

    candidate = snapshots["candidate"]
    tracking = snapshots["tracking"]
    public = snapshots["public"]
    admin = snapshots["admin"]
    snapshot_history = {str(item["assetId"]) for item in candidate["firstGatePassedHistory"]}
    snapshot_queue = {str(item["assetId"]) for item in candidate["firstGateQueue"]}
    snapshot_tracking = {str(item["assetId"]) for item in tracking["items"]}
    public_by_id = {str(item["assetId"]): item for item in public["items"]}
    snapshot_public = set(public_by_id)
    all_opportunities = {str(value) for value in public["allOpportunities"]}

    state_mismatches = []
    cohort_mismatches = []
    for asset_id in sorted(expected_public & snapshot_public):
        expected_state = (
            assessments[asset_id]["publicState"]
            if asset_id in baseline_public
            else active_history[asset_id]["last_public_state"]
        )
        actual_state = public_by_id[asset_id].get("publicState")
        if expected_state != actual_state:
            state_mismatches.append(
                {"assetId": asset_id, "expected": expected_state, "actual": actual_state}
            )
        if asset_id in baseline_public:
            expected_cohort = assessments[asset_id]
            actual_item = public_by_id[asset_id]
            if (
                expected_cohort.get("cohortScope") != actual_item.get("cohortScope")
                or int(expected_cohort.get("cohortSampleSize") or 0) != int(actual_item.get("cohortSampleSize") or 0)
                or expected_cohort.get("cohortThresholds") != (actual_item.get("cohortThresholds") or {})
            ):
                cohort_mismatches.append({
                    "assetId": asset_id,
                    "expectedScope": expected_cohort.get("cohortScope"),
                    "actualScope": actual_item.get("cohortScope"),
                    "expectedSampleSize": expected_cohort.get("cohortSampleSize"),
                    "actualSampleSize": actual_item.get("cohortSampleSize"),
                })

    snapshot_hashes = {
        name: {
            "path": str(SNAPSHOTS[name].relative_to(ROOT)).replace("\\", "/"),
            "fileSha256": hashlib.sha256(SNAPSHOTS[name].read_bytes()).hexdigest(),
            "contentHashExpected": payload.get("contentSha256"),
            "contentHashActual": content_hash(payload),
            "contentHashMatches": content_hash(payload) == payload.get("contentSha256"),
        }
        for name, payload in snapshots.items()
    }
    chain_counts = dict(Counter(item.get("chainId") for item in public["items"]))
    lifecycle_counts = dict(Counter(item.get("lifecyclePool") for item in public["items"]))
    public_state_counts = dict(Counter(item.get("publicState") for item in public["items"]))
    home_assets = [asset_id for values in public["homeTop10"].values() for asset_id in values]
    home_violations = []
    for chain, asset_ids in public["homeTop10"].items():
        if len(asset_ids) > 10:
            home_violations.append({"chain": chain, "reason": "more_than_10"})
        for asset_id in asset_ids:
            item = public_by_id.get(asset_id)
            if not item or item.get("chainId") != chain or not item.get("rankingAvailable"):
                home_violations.append({"chain": chain, "assetId": asset_id, "reason": "not_rankable_public_same_chain"})

    differences = {
        "expectedQueueNotSnapshot": diff(expected_queue, snapshot_queue),
        "snapshotQueueNotExpected": diff(snapshot_queue, expected_queue),
        "expectedHistoryNotSnapshot": diff(expected_history, snapshot_history),
        "snapshotHistoryNotExpected": diff(snapshot_history, expected_history),
        "expectedTrackingNotSnapshot": diff(expected_tracking, snapshot_tracking),
        "snapshotTrackingNotExpected": diff(snapshot_tracking, expected_tracking),
        "expectedPublicNotSnapshot": diff(expected_public, snapshot_public),
        "snapshotPublicNotExpected": diff(snapshot_public, expected_public),
        "publicNotTracked": diff(snapshot_public, snapshot_tracking),
        "allOpportunitiesNotPublic": diff(all_opportunities, snapshot_public),
        "publicNotAllOpportunities": diff(snapshot_public, all_opportunities),
    }
    duplicate_counts = {
        "history": len(candidate["firstGatePassedHistory"]) - len(snapshot_history),
        "queue": len(candidate["firstGateQueue"]) - len(snapshot_queue),
        "tracking": len(tracking["items"]) - len(snapshot_tracking),
        "public": len(public["items"]) - len(snapshot_public),
        "allOpportunities": len(public["allOpportunities"]) - len(all_opportunities),
    }
    checks = {
        "snapshotContentHashes": all(row["contentHashMatches"] for row in snapshot_hashes.values()),
        "sameCutoff": len(cutoffs) == 1,
        "coreSetDifferencesZero": all(value["count"] == 0 for value in differences.values()),
        "duplicatesZero": all(value == 0 for value in duplicate_counts.values()),
        "publicStatesMatchIndependentRecalculation": not state_mismatches,
        "pathCohortsMatchIndependentRecalculation": not cohort_mismatches,
        "publicStatesAllowed": all(item.get("publicState") in PUBLIC_STATES for item in public["items"]),
        "summaryCountsMatch": (
            chain_counts == {key: value for key, value in public["chainCounts"].items() if value}
            and lifecycle_counts == public["lifecycleCounts"]
            and public_state_counts == public["publicStateCounts"]
        ),
        "homeIsReadOnlyPublicProjection": not home_violations and set(home_assets) <= snapshot_public,
        "adminReconciliationMatches": (
            admin["reconciliation"]["firstGatePassedHistoryCount"] == len(snapshot_history)
            and admin["reconciliation"]["firstGateQueueCount"] == len(snapshot_queue)
            and admin["reconciliation"]["trackingCount"] == len(snapshot_tracking)
            and admin["reconciliation"]["publicCount"] == len(snapshot_public)
            and not any(admin["reconciliation"]["differences"].values())
        ),
    }
    passed = all(checks.values())
    report = {
        "schemaVersion": "c2.4-independent-core-audit-v1",
        "release": "C2.4",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "passed" if passed else "failed",
        "independence": {
            "databaseMode": "read_only_query_only",
            "implementationQualificationImports": 0,
            "expectedSetsRecomputedFromBottomLevelRecords": True,
            "expectedPublicStateRecomputedFromFrozenJsonAndRawObservations": True,
        },
        "dataCutoffAt": cutoff,
        "snapshotBuildIds": {name: payload["buildId"] for name, payload in snapshots.items()},
        "snapshotHashes": snapshot_hashes,
        "counts": {
            "inputCandidates": admin["reconciliation"]["inputCandidateCount"],
            "expectedFirstGateHistory": len(expected_history),
            "snapshotFirstGateHistory": len(snapshot_history),
            "expectedFirstGateQueue": len(expected_queue),
            "snapshotFirstGateQueue": len(snapshot_queue),
            "expectedTracking": len(expected_tracking),
            "snapshotTracking": len(snapshot_tracking),
            "completedTrackingAssessed": len(completed_rows),
            "independentPublicBaselinePass": len(baseline_public),
            "activePublicHistory": len(active_history),
            "expectedPublic": len(expected_public),
            "snapshotPublic": len(snapshot_public),
            "homeDerived": len(home_assets),
            "unrankedPublic": sum(not item.get("rankingAvailable") for item in public["items"]),
        },
        "chainPublicCounts": public["chainCounts"],
        "lifecyclePublicCounts": lifecycle_counts,
        "publicStateCounts": public_state_counts,
        "differences": differences,
        "duplicateCounts": duplicate_counts,
        "stateMismatches": state_mismatches,
        "cohortMismatches": cohort_mismatches,
        "homeViolations": home_violations,
        "publicAssessments": {
            asset_id: assessments.get(asset_id, {"retainedState": active_history[asset_id]["last_public_state"]})
            for asset_id in sorted(expected_public)
        },
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "dataCutoffAt": cutoff,
        "counts": report["counts"],
        "differenceCounts": {key: value["count"] for key, value in differences.items()},
        "stateMismatches": state_mismatches,
        "cohortMismatches": cohort_mismatches,
        "checks": checks,
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
