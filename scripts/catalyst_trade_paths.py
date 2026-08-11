#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH, initialize_database


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GATE_STATE_PATH = PROJECT_ROOT / "data" / "gate-screening-state.json"
RULE_VERSION = "catalyst-trade-path-c1.6.03"
MODELED_EXIT_NOTIONAL_USD = 20_000.0
CATALYST_PATH_SOURCE_DEFINITION = {
    "source_id": "machine-catalyst-trade-path",
    "name": "机器催化交易路径",
    "source_type": "internal_model",
    "url": "local://catalyst-trade-path",
    "access_method": "Deterministic rules",
}

STAGE_LABELS = {
    "catalyst_pending": "催化事实待发现",
    "asset_pending": "受益资产待确认",
    "transmission_pending": "价值传导待闭环",
    "market_pending": "市场表达待补齐",
    "exit_pending": "2万美元退出待闭环",
    "research_ready": "研究路径已闭环",
    "action_ready": "行动路径已闭环",
    "invalidated": "路径已失效",
}

CATALYST_TYPES = {
    "governance_proposal": ("governance", 0),
}

CONFIRMATION_TYPES = {
    "protocol_adoption_metric",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(*parts):
    payload = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def parse_json(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def dedupe_text(items):
    output = []
    seen = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def modeled_slippage(liquidity_usd, notional_usd=MODELED_EXIT_NOTIONAL_USD):
    if not liquidity_usd or float(liquidity_usd) <= 0:
        return None
    return round(min(100.0, (200.0 * float(notional_usd)) / float(liquidity_usd)), 4)


def load_exit_threshold(path=DEFAULT_GATE_STATE_PATH):
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        return float(state["settings"]["maximumExitSlippagePct"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 8.0


def evidence_age_status(observed_at, generated_at):
    observed = parse_time(observed_at)
    generated = parse_time(generated_at)
    if not observed or not generated:
        return "stale"
    age_days = max(0, (generated - observed).days)
    if age_days <= 30:
        return "active"
    if age_days <= 90:
        return "stale"
    return "expired"


def latest_by_asset(connection, table, order_columns):
    rows = {}
    for row in connection.execute(
        f"""
        SELECT *
        FROM {table}
        ORDER BY {order_columns}
        """
    ):
        item = dict(row)
        rows.setdefault(item["asset_id"], item)
    return rows


def evidence_by_project(connection):
    output = {}
    for row in connection.execute(
        """
        SELECT evidence.*, source.name AS source_name
        FROM evidence_items evidence
        LEFT JOIN sources source ON source.source_id = evidence.source_id
        ORDER BY evidence.observed_at DESC, evidence.evidence_id DESC
        """
    ):
        output.setdefault(row["project_id"], []).append(dict(row))
    return output


def latest_machine_conclusions(connection):
    output = {}
    for row in connection.execute(
        """
        SELECT conclusion.*
        FROM machine_conclusions conclusion
        WHERE conclusion.publication_status = 'published'
        ORDER BY conclusion.generated_at DESC, conclusion.machine_conclusion_id DESC
        """
    ):
        output.setdefault(row["case_id"], dict(row))
    return output


def primary_contracts(connection):
    output = {}
    for row in connection.execute(
        """
        SELECT contract.*, network.name AS network_name
        FROM asset_contracts contract
        JOIN networks network ON network.network_id = contract.network_id
        ORDER BY contract.is_primary DESC, contract.updated_at DESC
        """
    ):
        output.setdefault(row["asset_id"], dict(row))
    return output


def latest_tradeability(connection):
    output = {}
    for row in connection.execute(
        """
        SELECT check_item.*, contract.asset_id
        FROM tradeability_checks check_item
        JOIN asset_contracts contract
          ON contract.asset_contract_id = check_item.asset_contract_id
        ORDER BY check_item.checked_at DESC, check_item.check_id DESC
        """
    ):
        output.setdefault(row["asset_id"], dict(row))
    return output


def latest_venues(connection):
    output = {}
    for row in connection.execute(
        """
        SELECT *
        FROM venues
        WHERE status IN ('active', 'unknown')
        ORDER BY COALESCE(checked_at, updated_at) DESC, venue_id DESC
        """
    ):
        output.setdefault(row["asset_id"], dict(row))
    return output


def latest_markets(connection):
    return latest_by_asset(
        connection,
        "market_snapshots",
        "observed_at DESC, snapshot_id DESC",
    )


def latest_contract_risks(connection):
    return latest_by_asset(
        connection,
        "contract_risks",
        "assessed_at DESC, contract_risk_id DESC",
    )


def choose_catalyst(records, generated_at):
    candidates = []
    for item in records:
        mapping = CATALYST_TYPES.get(item["evidence_type"])
        if not mapping:
            continue
        if (
            item["evidence_type"] == "governance_proposal"
            and "active" not in str(item["summary"]).lower()
        ):
            continue
        catalyst_type, priority = mapping
        status = evidence_age_status(item["observed_at"], generated_at)
        if status == "expired":
            continue
        boundary_rank = {
            "confirmed_fact": 0,
            "high_confidence_inference": 1,
            "project_claim": 2,
            "unverified_signal": 3,
        }.get(item["fact_boundary"], 4)
        candidates.append(
            (
                0 if status == "active" else 1,
                priority,
                boundary_rank,
                -(
                    parse_time(item["observed_at"]).timestamp()
                    if parse_time(item["observed_at"])
                    else 0
                ),
                catalyst_type,
                status,
                item,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda value: value[:4])
    _, _, _, _, catalyst_type, status, evidence = candidates[0]
    return {
        "type": catalyst_type,
        "status": status,
        "evidence": evidence,
    }


def confirmation_evidence(records):
    return [
        item
        for item in records
        if item["evidence_type"] in CONFIRMATION_TYPES
        and item["fact_boundary"] in ("confirmed_fact", "high_confidence_inference")
    ][:3]


def build_path_record(
    case,
    project,
    asset,
    records,
    conclusion,
    market,
    venue,
    contract,
    tradeability,
    contract_risk,
    run_id,
    generated_at,
    exit_threshold,
):
    catalyst = choose_catalyst(records, generated_at)
    confirmations = confirmation_evidence(records)
    value_capture = (
        case["value_capture_grade"]
        if case["value_capture_grade"] != "unknown"
        else asset["capture_grade"]
        if asset and asset["capture_grade"] != "unknown"
        else "unknown"
    )
    modeled_exit = modeled_slippage(
        market["liquidity_usd"] if market else None
    )
    catalyst_evidence = catalyst["evidence"] if catalyst else None
    catalyst_status = catalyst["status"] if catalyst else "missing"
    transmission_status = (
        "verified"
        if catalyst and asset and value_capture in ("A", "B", "C") and confirmations
        else "partial"
        if catalyst and asset
        else "missing"
    )
    sell_path_status = (
        tradeability["sell_path_status"] if tradeability else "unknown"
    )
    blocked_execution = (
        sell_path_status == "blocked"
        or (contract_risk and contract_risk["overall_risk"] == "blocked")
        or (modeled_exit is not None and modeled_exit > exit_threshold)
    )
    execution_status = (
        "blocked"
        if blocked_execution
        else "verified"
        if (
            sell_path_status == "read_only_verified"
            and market
            and market["liquidity_usd"]
            and venue
            and modeled_exit is not None
            and modeled_exit <= exit_threshold
        )
        else "limited"
        if asset and (market or venue or tradeability)
        else "unknown"
    )

    blockers = []
    if not catalyst:
        blockers.append("尚未发现90日内可溯源的治理、代码发布或安全变化候选催化")
    elif catalyst_status == "stale":
        blockers.append("候选催化已超过30日，需要确认是否仍处于有效窗口")
    if not asset:
        blockers.append("尚未确认能够承接上行的可购买资产")
    if asset and value_capture == "unknown":
        blockers.append("项目事实如何传导到该资产价值尚未核验")
    if not confirmations:
        blockers.append("尚无采用、订单或资金变化确认信号")
    if asset and not market:
        blockers.append("尚无该资产的最新市场快照")
    if asset and market and not market["liquidity_usd"]:
        blockers.append("尚无可用于退出估算的单池流动性")
    if asset and not venue:
        blockers.append("尚未确认交易场所或交易池")
    if asset and sell_path_status != "read_only_verified":
        blockers.append("只读卖出路径尚未通过")
    if modeled_exit is None and asset:
        blockers.append("无法计算2万美元理论退出滑点")
    elif modeled_exit is not None and modeled_exit > exit_threshold:
        blockers.append(
            f"2万美元理论退出滑点 {modeled_exit:.2f}% 超过当前 {exit_threshold:.2f}% 门槛"
        )
    if contract_risk and contract_risk["overall_risk"] == "blocked":
        blockers.append("合约风险已达到阻断级")

    conclusion_state = conclusion["conclusion_state"] if conclusion else ""
    opportunity_stage = conclusion["opportunity_stage"] if conclusion else ""
    if (
        conclusion_state == "invalidated"
        or opportunity_stage == "invalidated"
        or case["workflow_state"] == "invalidated"
    ):
        path_stage = "invalidated"
    elif not catalyst:
        path_stage = "catalyst_pending"
    elif not asset:
        path_stage = "asset_pending"
    elif transmission_status != "verified":
        path_stage = "transmission_pending"
    elif not market or not market["liquidity_usd"] or not venue:
        path_stage = "market_pending"
    elif execution_status != "verified":
        path_stage = "exit_pending"
    elif opportunity_stage == "actionable":
        path_stage = "action_ready"
    else:
        path_stage = "research_ready"

    next_task_map = {
        "catalyst_pending": (
            "high_value_evidence_refresh",
            "继续扫描治理提案、官方代码与安全变化，形成可溯源候选催化。",
        ),
        "asset_pending": (
            "machine_asset_identity_refresh",
            "核验项目自身可购买资产、网络和合约，不能用相关生态代币代替。",
        ),
        "transmission_pending": (
            "formal_research_materials_refresh",
            "补齐产品、代币经济与采用证据，核验事实如何传导到资产价值。",
        ),
        "market_pending": (
            "formal_market_exit_refresh",
            "补齐价格、流动性、最深交易池和市场表达路径。",
        ),
        "exit_pending": (
            "formal_market_exit_refresh",
            "复核只读卖出路径，并重算2万美元理论退出滑点。",
        ),
        "research_ready": (
            "machine_conclusion_refresh",
            "重新发布机器结论，确认是否满足当前行动硬门槛。",
        ),
        "action_ready": (
            "tracking_task_refresh",
            "持续跟踪催化窗口、确认信号、退出能力与失效条件。",
        ),
        "invalidated": (
            "high_value_evidence_refresh",
            "保留失效历史，只有出现新的独立事实链时才重新建案。",
        ),
    }
    next_task_id, next_step = next_task_map[path_stage]

    transmission_steps = [
        {
            "key": "catalyst",
            "label": "催化事实",
            "status": "verified" if catalyst else "missing",
            "detail": (
                catalyst_evidence["summary"]
                if catalyst_evidence
                else "尚未发现有效候选催化"
            ),
            "evidenceId": catalyst_evidence["evidence_id"] if catalyst_evidence else "",
        },
        {
            "key": "confirmation",
            "label": "订单与采用确认",
            "status": "verified" if confirmations else "missing",
            "detail": (
                confirmations[0]["summary"]
                if confirmations
                else "尚无采用、订单或资金变化确认"
            ),
            "evidenceId": confirmations[0]["evidence_id"] if confirmations else "",
        },
        {
            "key": "value_capture",
            "label": "资产价值捕获",
            "status": "verified" if value_capture in ("A", "B", "C") else "missing",
            "detail": (
                f"价值捕获等级 {value_capture}"
                if value_capture in ("A", "B", "C")
                else "价值传导关系尚未核验"
            ),
            "evidenceId": "",
        },
        {
            "key": "market_expression",
            "label": "市场表达",
            "status": "verified" if asset and market and venue else "missing",
            "detail": (
                f"{asset['symbol']} · {venue['venue_name']} {venue['pair_symbol']}".strip()
                if asset and venue
                else f"{asset['symbol']} 已识别，交易场所待补齐"
                if asset
                else "受益资产待确认"
            ),
            "evidenceId": "",
        },
        {
            "key": "exit",
            "label": "2万美元退出",
            "status": "verified" if execution_status == "verified" else "missing",
            "detail": (
                f"恒定乘积理论滑点 {modeled_exit:.2f}%，只读卖出路径已通过"
                if execution_status == "verified"
                else f"恒定乘积理论滑点 {modeled_exit:.2f}%，尚未通过全部退出门槛"
                if modeled_exit is not None
                else "流动性不足，暂无法估算"
            ),
            "evidenceId": "",
        },
    ]

    conclusion_invalidations = (
        parse_json(conclusion["invalidation_conditions_json"], [])
        if conclusion
        else []
    )
    invalidations = dedupe_text(
        [case["invalidation"], *conclusion_invalidations]
    )
    source_evidence_ids = dedupe_text(
        [
            catalyst_evidence["evidence_id"] if catalyst_evidence else "",
            *(item["evidence_id"] for item in confirmations),
            *(
                parse_json(conclusion["source_evidence_ids_json"], [])
                if conclusion
                else []
            ),
        ]
    )
    expression_asset = ""
    if asset:
        network_name = asset["chain"] or (
            contract["network_name"] if contract else ""
        )
        expression_asset = f"{asset['symbol']} · {network_name}".strip(" ·")
    venue_text = (
        f"{venue['venue_name']} · {venue['pair_symbol']}"
        if venue
        else ""
    )
    source_url = (
        catalyst_evidence["source_url"]
        if catalyst_evidence
        else conclusion["source_url"]
        if conclusion
        else (
            f"https://{project['website_domain']}"
            if project["website_domain"]
            else ""
        )
    )
    record = {
        "case_id": case["case_id"],
        "project_id": case["project_id"],
        "asset_id": asset["asset_id"] if asset else None,
        "run_id": run_id,
        "generated_at": generated_at,
        "path_stage": path_stage,
        "path_stage_label": STAGE_LABELS[path_stage],
        "catalyst_type": catalyst["type"] if catalyst else "unknown",
        "catalyst_status": catalyst_status,
        "catalyst_evidence_id": (
            catalyst_evidence["evidence_id"] if catalyst_evidence else None
        ),
        "catalyst_summary": (
            catalyst_evidence["summary"]
            if catalyst_evidence
            else "尚未发现90日内可溯源候选催化"
        ),
        "catalyst_source_url": (
            catalyst_evidence["source_url"] if catalyst_evidence else ""
        ),
        "catalyst_observed_at": (
            catalyst_evidence["observed_at"] if catalyst_evidence else None
        ),
        "confirmation_evidence_ids_json": json.dumps(
            [item["evidence_id"] for item in confirmations],
            ensure_ascii=False,
        ),
        "transmission_steps_json": json.dumps(
            transmission_steps,
            ensure_ascii=False,
        ),
        "transmission_status": transmission_status,
        "expression_asset_text": expression_asset.strip(" ·"),
        "contract_address": contract["contract_address"] if contract else "",
        "network_name": contract["network_name"] if contract else asset["chain"] if asset else "",
        "venue_text": venue_text,
        "sell_path_status": sell_path_status,
        "observed_exit_notional_usd": (
            tradeability["exit_notional_usd"] if tradeability else None
        ),
        "observed_exit_slippage_pct": (
            tradeability["estimated_exit_slippage_pct"] if tradeability else None
        ),
        "modeled_exit_notional_usd": MODELED_EXIT_NOTIONAL_USD,
        "modeled_exit_slippage_pct": modeled_exit,
        "modeled_exit_method": (
            "按最深单池流动性使用 200×退出金额/流动性 的恒定乘积近似；"
            "仅用于筛选，不代表真实成交、报价或滑点保证。"
        ),
        "execution_status": execution_status,
        "invalidation_conditions_json": json.dumps(
            invalidations,
            ensure_ascii=False,
        ),
        "blockers_json": json.dumps(dedupe_text(blockers), ensure_ascii=False),
        "next_task_id": next_task_id,
        "next_step": next_step,
        "source_evidence_ids_json": json.dumps(
            source_evidence_ids,
            ensure_ascii=False,
        ),
        "source_url": source_url or "",
        "publication_status": "published",
        "rule_version": RULE_VERSION,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in record.items()
                if key not in {"run_id", "generated_at", "publication_status"}
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    record["catalyst_trade_path_id"] = (
        f"catalyst-path-{stable_id(case['case_id'], fingerprint)}"
    )
    return record


def persist_catalyst_trade_paths(
    connection,
    run_id,
    generated_at=None,
    stable_id_fn=None,
):
    generated_at = generated_at or utc_now()
    exit_threshold = load_exit_threshold()
    evidence = evidence_by_project(connection)
    conclusions = latest_machine_conclusions(connection)
    markets = latest_markets(connection)
    venues = latest_venues(connection)
    contracts = primary_contracts(connection)
    tradeability = latest_tradeability(connection)
    contract_risks = latest_contract_risks(connection)
    assets = {
        row["asset_id"]: dict(row)
        for row in connection.execute("SELECT * FROM assets")
    }
    projects = {
        row["project_id"]: dict(row)
        for row in connection.execute("SELECT * FROM projects")
    }
    cases = [dict(row) for row in connection.execute(
        "SELECT * FROM candidate_cases ORDER BY case_id"
    )]
    stage_counts = {stage: 0 for stage in STAGE_LABELS}
    inserted = 0
    changed = 0
    with_catalyst = 0
    with_asset = 0
    exit_modeled = 0
    for case in cases:
        project = projects[case["project_id"]]
        asset = assets.get(case["asset_id"])
        record = build_path_record(
            case,
            project,
            asset,
            evidence.get(case["project_id"], []),
            conclusions.get(case["case_id"]),
            markets.get(case["asset_id"]),
            venues.get(case["asset_id"]),
            contracts.get(case["asset_id"]),
            tradeability.get(case["asset_id"]),
            contract_risks.get(case["asset_id"]),
            run_id,
            generated_at,
            exit_threshold,
        )
        previous = connection.execute(
            """
            SELECT catalyst_trade_path_id, path_stage
            FROM catalyst_trade_paths
            WHERE case_id = ? AND publication_status = 'published'
            ORDER BY generated_at DESC, catalyst_trade_path_id DESC
            LIMIT 1
            """,
            (case["case_id"],),
        ).fetchone()
        if not previous or previous["catalyst_trade_path_id"] != record["catalyst_trade_path_id"]:
            changed += 1
        connection.execute(
            """
            UPDATE catalyst_trade_paths
            SET publication_status = 'superseded'
            WHERE case_id = ?
              AND publication_status = 'published'
              AND catalyst_trade_path_id <> ?
            """,
            (case["case_id"], record["catalyst_trade_path_id"]),
        )
        columns = list(record)
        cursor = connection.execute(
            f"""
            INSERT OR IGNORE INTO catalyst_trade_paths (
              {", ".join(columns)}
            )
            VALUES ({", ".join("?" for _ in columns)})
            """,
            tuple(record[column] for column in columns),
        )
        inserted += int(cursor.rowcount > 0)
        connection.execute(
            """
            UPDATE catalyst_trade_paths
            SET publication_status = 'published'
            WHERE catalyst_trade_path_id = ?
            """,
            (record["catalyst_trade_path_id"],),
        )
        stage_counts[record["path_stage"]] += 1
        with_catalyst += int(record["catalyst_status"] in ("active", "stale"))
        with_asset += int(bool(record["asset_id"]))
        exit_modeled += int(record["modeled_exit_slippage_pct"] is not None)
    return {
        "projectsProcessed": len(cases),
        "recordsInserted": inserted,
        "changedProjects": changed,
        "withCatalyst": with_catalyst,
        "withAsset": with_asset,
        "exitModeled": exit_modeled,
        "stageCounts": stage_counts,
        "exitThresholdPct": exit_threshold,
        "modeledExitNotionalUsd": MODELED_EXIT_NOTIONAL_USD,
        "errors": [],
    }


def deserialize_path(row):
    item = dict(row)
    for source_field, output_field, fallback in (
        ("confirmation_evidence_ids_json", "confirmationEvidenceIds", []),
        ("transmission_steps_json", "transmissionSteps", []),
        ("invalidation_conditions_json", "invalidationConditions", []),
        ("blockers_json", "blockers", []),
        ("source_evidence_ids_json", "sourceEvidenceIds", []),
    ):
        item[output_field] = parse_json(item.pop(source_field), fallback)
    return item


def latest_paths(connection):
    output = {}
    for row in connection.execute(
        """
        SELECT path.*
        FROM catalyst_trade_paths path
        WHERE path.publication_status = 'published'
        ORDER BY path.generated_at DESC, path.catalyst_trade_path_id DESC
        """
    ):
        output.setdefault(row["case_id"], deserialize_path(row))
    return output


def main():
    parser = argparse.ArgumentParser(description="生成凸性项目催化交易路径")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    initialize_database(args.db, backup=True)
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        result = persist_catalyst_trade_paths(
            connection,
            run_id=f"catalyst-path-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
        )
        connection.commit()
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
