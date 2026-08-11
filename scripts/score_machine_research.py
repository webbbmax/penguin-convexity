#!/usr/bin/env python3
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone

from build_project_master_pool import lifecycle_context, project_lifecycle


RULE_VERSION = "machine-research-scoring-c1.5.0"
SOURCE_DEFINITION = {
    "source_id": "machine-research-scoring",
    "name": "机器证据与凸性评分",
    "source_type": "derived_analysis",
    "url": "",
    "access_method": "Local rules",
}

ROUTE_CONFIG = {
    "early": {
        "label": "早期项目",
        "foundationWeight": 60,
        "signalWeight": 20,
        "marketWeight": 10,
        "sourceWeight": 10,
        "ignition": (
            "先核验产品、合约、代币经济、团队和初始采用，再确认是否存在"
            "可被事件点燃的非线性路径。"
        ),
    },
    "og": {
        "label": "OG项目",
        "foundationWeight": 25,
        "signalWeight": 50,
        "marketWeight": 15,
        "sourceWeight": 10,
        "ignition": (
            "等待治理提案、关键代码发布、新部署、产品升级、监管或机构动作"
            "形成可验证的新增事实。"
        ),
    },
    "other": {
        "label": "潜力项目",
        "foundationWeight": 40,
        "signalWeight": 35,
        "marketWeight": 15,
        "sourceWeight": 10,
        "ignition": (
            "同时补齐基础档案与治理、代码、部署、产品和采用信号，"
            "证据形成交叉验证后再判断点火条件。"
        ),
    },
}

FOUNDATION_FIELDS = (
    "officialWebsite",
    "officialX",
    "github",
    "productDocs",
    "tokenomics",
    "asset",
    "contract",
    "market",
    "team",
    "audit",
)
SIGNAL_FIELDS = (
    "governance",
    "codeActivity",
    "contractDeployment",
    "productUpgrade",
    "onchainAdoption",
    "regulatory",
    "institutional",
    "tokenomicsAdjustment",
)
EVENT_EVIDENCE_TYPES = {
    "governance_proposal",
    "onchain_governance",
    "official_product_release",
    "official_contract_deployment",
    "official_regulatory_event",
    "official_institutional_event",
    "official_tokenomics_adjustment",
}


def parse_json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback
    return parsed


def parse_time(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return parse_time(int(text))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value, now):
    parsed = parse_time(value)
    current = parse_time(now)
    if not parsed or not current:
        return None
    return max(0, (current - parsed).days)


def http_url(value):
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


def group_rows(connection, sql, key):
    grouped = defaultdict(list)
    for row in connection.execute(sql):
        item = dict(row)
        grouped[item[key]].append(item)
    return grouped


def latest_by_key(rows, key):
    latest = {}
    for row in rows:
        latest.setdefault(row[key], row)
    return latest


def load_inputs(connection):
    cases = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
              candidate.*,
              project.canonical_name,
              project.website_domain,
              project.official_repo,
              project.identity_status AS project_identity_status,
              asset.symbol,
              asset.identity_status AS asset_identity_status
            FROM candidate_cases candidate
            JOIN projects project ON project.project_id = candidate.project_id
            LEFT JOIN assets asset ON asset.asset_id = candidate.asset_id
            WHERE project.identity_status != 'rejected'
            ORDER BY project.canonical_name, candidate.case_id
            """
        )
    ]
    sources = group_rows(
        connection,
        """
        SELECT *
        FROM source_discoveries
        WHERE matched_project_id IS NOT NULL
          AND status = 'active'
        ORDER BY last_seen_at DESC, source_discovery_id
        """,
        "matched_project_id",
    )
    evidence = group_rows(
        connection,
        """
        SELECT evidence.*, source.name AS source_name
        FROM evidence_items evidence
        LEFT JOIN sources source ON source.source_id = evidence.source_id
        WHERE evidence.project_id IS NOT NULL
        ORDER BY observed_at DESC, evidence_id
        """,
        "project_id",
    )
    contracts = group_rows(
        connection,
        """
        SELECT asset.project_id, contract.*
        FROM asset_contracts contract
        JOIN assets asset ON asset.asset_id = contract.asset_id
        ORDER BY contract.updated_at DESC, contract.asset_contract_id
        """,
        "project_id",
    )
    markets = latest_by_key(
        [
            dict(row)
            for row in connection.execute(
                """
                SELECT asset.project_id, market.*
                FROM market_snapshots market
                JOIN assets asset ON asset.asset_id = market.asset_id
                ORDER BY market.observed_at DESC, market.snapshot_id DESC
                """
            )
        ],
        "project_id",
    )
    risks = latest_by_key(
        [
            dict(row)
            for row in connection.execute(
                """
                SELECT asset.project_id, risk.*
                FROM contract_risks risk
                JOIN assets asset ON asset.asset_id = risk.asset_id
                ORDER BY risk.assessed_at DESC, risk.contract_risk_id DESC
                """
            )
        ],
        "project_id",
    )
    tradeability = latest_by_key(
        [
            dict(row)
            for row in connection.execute(
                """
                SELECT asset.project_id, check_result.*
                FROM tradeability_checks check_result
                JOIN asset_contracts contract
                  ON contract.asset_contract_id = check_result.asset_contract_id
                JOIN assets asset ON asset.asset_id = contract.asset_id
                ORDER BY check_result.checked_at DESC, check_result.check_id DESC
                """
            )
        ],
        "project_id",
    )
    asset_reviews = latest_by_key(
        [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM project_asset_identity_reviews
                ORDER BY reviewed_at DESC, project_asset_review_id DESC
                """
            )
        ],
        "project_id",
    )
    return {
        "cases": cases,
        "sources": sources,
        "evidence": evidence,
        "contracts": contracts,
        "markets": markets,
        "risks": risks,
        "tradeability": tradeability,
        "assetReviews": asset_reviews,
    }


def source_metrics(source_rows):
    metrics = {
        "sourceIds": set(),
        "highConfidenceSources": 0,
        "maxTvlUsd": None,
        "chains": set(),
        "proposalCount": 0,
        "latestProposalAt": None,
        "valueCaptureStatuses": set(),
        "sourceUrls": [],
    }
    for row in source_rows:
        metrics["sourceIds"].add(row["source_id"])
        if row["attribution_confidence"] == "high":
            metrics["highConfidenceSources"] += 1
        payload = parse_json(row.get("evidence_json"), {})
        tvl = payload.get("tvlUsd")
        if isinstance(tvl, (int, float)):
            metrics["maxTvlUsd"] = max(metrics["maxTvlUsd"] or 0, float(tvl))
        metrics["chains"].update(payload.get("chains") or [])
        count = int(payload.get("proposalCountInWindow") or 0)
        metrics["proposalCount"] += count
        proposal_time = parse_time(payload.get("latestProposalAt"))
        if proposal_time and (
            metrics["latestProposalAt"] is None
            or proposal_time > metrics["latestProposalAt"]
        ):
            metrics["latestProposalAt"] = proposal_time
        metrics["valueCaptureStatuses"].add(row["value_capture_status"])
        url = http_url(row.get("source_url"))
        if url and url not in metrics["sourceUrls"]:
            metrics["sourceUrls"].append(url)
    return metrics


def evidence_metrics(evidence_rows, now):
    types = {row["evidence_type"] for row in evidence_rows}
    confirmed = sum(
        row["fact_boundary"] in {"confirmed_fact", "high_confidence_inference"}
        for row in evidence_rows
    )
    recent_events = []
    for row in evidence_rows:
        if row["evidence_type"] not in EVENT_EVIDENCE_TYPES:
            continue
        age = days_since(row["observed_at"], now)
        if age is not None:
            recent_events.append(age)
    urls = []
    for row in evidence_rows:
        url = http_url(row.get("source_url"))
        if url and url not in urls:
            urls.append(url)
    return {
        "types": types,
        "confirmedCount": confirmed,
        "recentEventAges": recent_events,
        "evidenceIds": [row["evidence_id"] for row in evidence_rows[:30]],
        "sourceUrls": urls,
    }


def foundation_status(case, source_rows, evidence_types, contracts, market):
    return {
        "officialWebsite": bool(
            case.get("website_domain")
            or any(http_url(row.get("website_url")) for row in source_rows)
        ),
        "officialX": any(http_url(row.get("social_url")) for row in source_rows),
        "github": bool(
            case.get("official_repo")
            or any(http_url(row.get("repository_url")) for row in source_rows)
        ),
        "productDocs": "official_product_documentation" in evidence_types,
        "tokenomics": "official_tokenomics" in evidence_types,
        "asset": bool(case.get("asset_id")),
        "contract": bool(contracts),
        "market": bool(market),
        "team": "official_team_or_organization" in evidence_types,
        "audit": "official_audit_or_security" in evidence_types,
    }


def signal_status(source_info, evidence_types):
    return {
        "governance": bool(
            source_info["proposalCount"]
            or evidence_types
            & {"governance_proposal", "onchain_governance"}
        ),
        "codeActivity": "official_code_activity" in evidence_types,
        "contractDeployment": "official_contract_deployment" in evidence_types,
        "productUpgrade": "official_product_release" in evidence_types,
        "onchainAdoption": bool(
            source_info["maxTvlUsd"]
            or "protocol_adoption_metric" in evidence_types
        ),
        "regulatory": "official_regulatory_event" in evidence_types,
        "institutional": "official_institutional_event" in evidence_types,
        "tokenomicsAdjustment": (
            "official_tokenomics_adjustment" in evidence_types
        ),
    }


def weighted_coverage(statuses, maximum):
    available = sum(bool(value) for value in statuses.values())
    score = round(available / max(1, len(statuses)) * maximum)
    return available, score


def evidence_quality_score(
    lifecycle_bucket,
    foundation,
    signals,
    source_info,
    market,
    tradeability,
    project_status,
):
    config = ROUTE_CONFIG[lifecycle_bucket]
    foundation_count, foundation_score = weighted_coverage(
        foundation, config["foundationWeight"]
    )
    signal_count, signal_score = weighted_coverage(
        signals, config["signalWeight"]
    )
    source_count = len(source_info["sourceIds"])
    source_score = min(
        config["sourceWeight"],
        (
            (4 if project_status == "verified" else 2 if project_status == "pending" else 3)
            + min(4, source_count)
            + min(2, source_info["highConfidenceSources"])
        ),
    )
    market_points = 0
    if market:
        market_points += round(config["marketWeight"] * 0.55)
    if tradeability and tradeability.get("overall_status") == "pass":
        market_points += config["marketWeight"] - market_points
    market_points = min(config["marketWeight"], market_points)
    total = min(
        100,
        foundation_score + signal_score + source_score + market_points,
    )
    return total, {
        "categoryWeights": {
            "foundation": config["foundationWeight"],
            "preSignals": config["signalWeight"],
            "marketExit": config["marketWeight"],
            "sourceConfidence": config["sourceWeight"],
        },
        "foundation": {
            "score": foundation_score,
            "maximum": config["foundationWeight"],
            "available": foundation_count,
            "total": len(foundation),
            "fields": foundation,
        },
        "preSignals": {
            "score": signal_score,
            "maximum": config["signalWeight"],
            "available": signal_count,
            "total": len(signals),
            "fields": signals,
        },
        "sourceConfidence": {
            "score": source_score,
            "maximum": config["sourceWeight"],
            "distinctSources": source_count,
            "highConfidenceRecords": source_info["highConfidenceSources"],
        },
        "marketExit": {
            "score": market_points,
            "maximum": config["marketWeight"],
            "marketAvailable": bool(market),
            "tradeabilityStatus": (
                tradeability.get("overall_status") if tradeability else "unknown"
            ),
        },
    }


def tvl_score(value):
    if value is None or value <= 0:
        return 0
    if value >= 1_000_000_000:
        return 10
    if value >= 100_000_000:
        return 8
    if value >= 10_000_000:
        return 6
    if value >= 1_000_000:
        return 4
    return 2


def mismatch_components(
    case,
    source_info,
    evidence_info,
    foundation,
    signals,
    asset_review,
    market,
    risk,
    tradeability,
    now,
):
    project_status = case["project_identity_status"]
    asset_status = case.get("asset_identity_status") or "unknown"
    fact_certainty = (
        (6 if project_status == "verified" else 4 if project_status == "pending" else 5)
        + (5 if asset_status == "verified" else 3 if asset_status == "pending" else 0)
        + min(4, len(source_info["sourceIds"]))
        + min(3, evidence_info["confirmedCount"])
        + (2 if foundation["officialWebsite"] else 0)
    )
    fact_certainty = min(20, fact_certainty)

    economic_increment = tvl_score(source_info["maxTvlUsd"])
    economic_increment += min(3, len(source_info["chains"]))
    if "protocol_adoption_metric" in evidence_info["types"]:
        economic_increment += 2
    economic_increment = min(20, economic_increment)

    value_capture = 0
    if asset_status == "verified":
        value_capture += 6
    elif asset_status == "pending":
        value_capture += 3
    if asset_review and asset_review["resolution_status"] == "verified":
        value_capture += 4
    elif asset_review and asset_review["resolution_status"] == "corroborated":
        value_capture += 2
    if "verified" in source_info["valueCaptureStatuses"]:
        value_capture += 10
    elif "claimed" in source_info["valueCaptureStatuses"]:
        value_capture += 4
    if market:
        value_capture += 3
    if case["value_capture_grade"] in {"A", "B"}:
        value_capture += 2
    value_capture = min(25, value_capture)

    proposal_age = (
        days_since(source_info["latestProposalAt"], now)
        if source_info["latestProposalAt"]
        else None
    )
    event_proximity = 0
    if proposal_age is not None:
        event_proximity = 10 if proposal_age <= 7 else 8 if proposal_age <= 30 else 5 if proposal_age <= 90 else 2
    if evidence_info["recentEventAges"]:
        age = min(evidence_info["recentEventAges"])
        event_proximity = max(
            event_proximity,
            12 if age <= 7 else 9 if age <= 30 else 5 if age <= 90 else 2,
        )
    event_proximity = min(20, event_proximity)

    price_unreacted = 0
    price_change = market.get("price_change_24h_pct") if market else None
    if event_proximity and isinstance(price_change, (int, float)):
        movement = abs(float(price_change))
        price_unreacted = 10 if movement <= 3 else 7 if movement <= 10 else 3 if movement <= 20 else 0

    deductions = []
    risk_deduction = 0
    if risk:
        risk_level = risk.get("overall_risk")
        risk_points = {"medium": 3, "high": 8, "blocked": 20}.get(
            risk_level, 0
        )
        if risk_points:
            risk_deduction += risk_points
            deductions.append(f"合约风险 {risk_level}：-{risk_points}")
    if tradeability and tradeability.get("overall_status") == "fail":
        risk_deduction += 8
        deductions.append("交易性核验失败：-8")
    if project_status == "conflict":
        risk_deduction += 15
        deductions.append("项目身份冲突：-15")
    if asset_review and asset_review["resolution_status"] == "conflict":
        risk_deduction += 10
        deductions.append("资产身份冲突：-10")
    risk_deduction = min(30, risk_deduction)
    components = {
        "fact_certainty": fact_certainty,
        "economic_increment": economic_increment,
        "value_capture": value_capture,
        "event_proximity": event_proximity,
        "price_unreacted": price_unreacted,
    }
    return components, risk_deduction, deductions


def maturity_for_case(case, source_info, evidence_info, now):
    project_verified = case["project_identity_status"] == "verified"
    asset_verified = case.get("asset_identity_status") == "verified"
    proposal_age = (
        days_since(source_info["latestProposalAt"], now)
        if source_info["latestProposalAt"]
        else None
    )
    recent_event = (
        proposal_age is not None
        and proposal_age <= 30
        or any(age <= 30 for age in evidence_info["recentEventAges"])
    )
    if project_verified and asset_verified and recent_event:
        return "L2"
    if project_verified and (
        source_info["maxTvlUsd"]
        or evidence_info["types"]
        & {"protocol_adoption_metric", "official_code_activity"}
    ):
        return "L1"
    return "L0"


def blockers_for_case(
    case,
    foundation,
    signals,
    market,
    risk,
    tradeability,
    source_info,
):
    blockers = []
    if case["project_identity_status"] != "verified":
        blockers.append("项目主体身份尚未完成严格核验")
    if case.get("asset_identity_status") != "verified":
        blockers.append("尚未核验项目自身可交易资产")
    if "verified" not in source_info["valueCaptureStatuses"]:
        blockers.append("代币价值捕获路径尚未核验")
    if not foundation["contract"]:
        blockers.append("代币合约与所在链资料不足")
    if not market:
        blockers.append("缺少价格、市值、流动性和成交快照")
    if not tradeability or tradeability.get("overall_status") != "pass":
        blockers.append("卖出路径、滑点与交易性尚未通过")
    if not risk or risk.get("overall_risk") in {None, "", "unknown"}:
        blockers.append("合约权限与安全风险尚未形成结论")
    if not any(signals.values()):
        blockers.append("尚未发现可进入点火判断的前置信号")
    blockers.append("尚未形成可核验的非线性上行路径与最大亏损边界")
    return blockers


def convexity_readiness_score(
    case,
    components,
    foundation,
    market,
    risk,
    tradeability,
):
    identity_score = 0
    if case["project_identity_status"] == "verified":
        identity_score += 7
    if case.get("asset_identity_status") == "verified":
        identity_score += 7
    if foundation["contract"]:
        identity_score += 5
    if market:
        identity_score += 5
    if tradeability and tradeability.get("overall_status") == "pass":
        identity_score += 6
    if risk and risk.get("overall_risk") in {"low", "medium"}:
        identity_score += 5
    identity_score = min(35, identity_score)

    hard_evidence_score = round(
        components["fact_certainty"] / 20 * 12
        + components["economic_increment"] / 20 * 13
    )
    value_score = round(components["value_capture"] / 25 * 20)
    structure_score = 0
    total = min(
        100,
        identity_score
        + hard_evidence_score
        + value_score
        + structure_score,
    )
    return total, {
        "identityAndTradeability": {
            "score": identity_score,
            "maximum": 35,
        },
        "hardEvidence": {
            "score": hard_evidence_score,
            "maximum": 25,
        },
        "valueCapture": {
            "score": value_score,
            "maximum": 20,
        },
        "convexityStructure": {
            "score": structure_score,
            "maximum": 20,
            "reason": "尚未形成最大亏损、非线性上行、点火、衰减和失效闭环",
        },
    }


def confidence_label(score, case, source_info):
    if (
        score >= 65
        and case["project_identity_status"] == "verified"
        and case.get("asset_identity_status") == "verified"
        and len(source_info["sourceIds"]) >= 2
    ):
        return "high"
    if score >= 35 and case["project_identity_status"] == "verified":
        return "medium"
    if score >= 15:
        return "low"
    return "insufficient"


def score_record(case, inputs, lifecycle, now):
    project_id = case["project_id"]
    source_rows = inputs["sources"].get(project_id, [])
    evidence_rows = inputs["evidence"].get(project_id, [])
    contracts = inputs["contracts"].get(project_id, [])
    market = inputs["markets"].get(project_id)
    risk = inputs["risks"].get(project_id)
    tradeability = inputs["tradeability"].get(project_id)
    asset_review = inputs["assetReviews"].get(project_id)
    source_info = source_metrics(source_rows)
    evidence_info = evidence_metrics(evidence_rows, now)
    lifecycle_data = project_lifecycle(project_id, lifecycle)
    bucket = lifecycle_data.get("lifecycleBucket") or "other"
    if bucket not in ROUTE_CONFIG:
        bucket = "other"
    foundation = foundation_status(
        case,
        source_rows,
        evidence_info["types"],
        contracts,
        market,
    )
    signals = signal_status(source_info, evidence_info["types"])
    evidence_score, evidence_dimensions = evidence_quality_score(
        bucket,
        foundation,
        signals,
        source_info,
        market,
        tradeability,
        case["project_identity_status"],
    )
    components, risk_deduction, deductions = mismatch_components(
        case,
        source_info,
        evidence_info,
        foundation,
        signals,
        asset_review,
        market,
        risk,
        tradeability,
        now,
    )
    mismatch_total = max(0, min(100, sum(components.values()) - risk_deduction))
    readiness_score, readiness_dimensions = convexity_readiness_score(
        case,
        components,
        foundation,
        market,
        risk,
        tradeability,
    )
    blockers = blockers_for_case(
        case,
        foundation,
        signals,
        market,
        risk,
        tradeability,
        source_info,
    )
    source_urls = [
        *evidence_info["sourceUrls"],
        *source_info["sourceUrls"],
    ]
    source_url = next((url for url in source_urls if url), "")
    maturity = maturity_for_case(case, source_info, evidence_info, now)
    label = ROUTE_CONFIG[bucket]["label"]
    confidence = confidence_label(evidence_score, case, source_info)
    return {
        "caseId": case["case_id"],
        "projectId": project_id,
        "projectName": case["canonical_name"],
        "assetId": case.get("asset_id"),
        "symbol": case.get("symbol") or "",
        "lifecycleBucket": bucket,
        "lifecycleLabel": label,
        "maturity": maturity,
        "previousMaturity": case["maturity_level"],
        "currentAction": case["action_stage"],
        "evidenceQualityScore": evidence_score,
        "mismatchComponents": components,
        "mismatchScore": mismatch_total,
        "riskDeduction": risk_deduction,
        "deductions": deductions,
        "convexityReadinessScore": readiness_score,
        "confidence": confidence,
        "blockers": blockers,
        "evidenceDimensions": evidence_dimensions,
        "readinessDimensions": readiness_dimensions,
        "sourceEvidenceIds": evidence_info["evidenceIds"],
        "sourceUrl": source_url,
        "ignitionCondition": ROUTE_CONFIG[bucket]["ignition"],
        "supportingEvidence": evidence_info["evidenceIds"][:12],
        "counterEvidence": [],
        "openQuestions": blockers,
    }


def register_source(connection, now):
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope,
          confidence, conflict_risk, status, schedule_text,
          last_checked_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'convexity_scoring', '中', '低', 'active',
                '凸性更新中心单项更新', ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          status = 'active',
          last_checked_at = excluded.last_checked_at,
          updated_at = excluded.updated_at
        """,
        (
            SOURCE_DEFINITION["source_id"],
            SOURCE_DEFINITION["name"],
            SOURCE_DEFINITION["source_type"],
            SOURCE_DEFINITION["url"],
            SOURCE_DEFINITION["access_method"],
            now,
            now,
            now,
        ),
    )


def latest_machine_score(connection, case_id):
    row = connection.execute(
        """
        SELECT *
        FROM machine_research_scores
        WHERE case_id = ?
        ORDER BY scored_at DESC, machine_score_id DESC
        LIMIT 1
        """,
        (case_id,),
    ).fetchone()
    return dict(row) if row else None


def persist_machine_research_scores(connection, run_id, now, stable_id):
    register_source(connection, now)
    inputs = load_inputs(connection)
    lifecycle = lifecycle_context(connection)
    records = [
        score_record(case, inputs, lifecycle, now)
        for case in inputs["cases"]
    ]
    summary = {
        "projectsScored": len(records),
        "highConfidence": 0,
        "mediumConfidence": 0,
        "lowConfidence": 0,
        "insufficient": 0,
        "mismatchAbove65": 0,
        "readinessAbove65": 0,
        "changedProjects": 0,
        "lifecycleCounts": {"early": 0, "og": 0, "other": 0},
        "errors": [],
    }
    for record in records:
        previous = latest_machine_score(connection, record["caseId"])
        mismatch_id = stable_id(
            "machine-mismatch-score", run_id, record["caseId"]
        )
        review_id = stable_id(
            "machine-convexity-review", run_id, record["caseId"]
        )
        machine_score_id = stable_id(
            "machine-research-score", run_id, record["caseId"]
        )
        connection.execute(
            """
            INSERT INTO mismatch_scores (
              mismatch_score_id, case_id, scored_at, fact_certainty,
              economic_increment, value_capture, event_proximity,
              price_unreacted, risk_deduction, total_score,
              deduction_detail_json, rule_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mismatch_id,
                record["caseId"],
                now,
                record["mismatchComponents"]["fact_certainty"],
                record["mismatchComponents"]["economic_increment"],
                record["mismatchComponents"]["value_capture"],
                record["mismatchComponents"]["event_proximity"],
                record["mismatchComponents"]["price_unreacted"],
                record["riskDeduction"],
                record["mismatchScore"],
                json.dumps(record["deductions"], ensure_ascii=False),
                RULE_VERSION,
            ),
        )
        connection.execute(
            """
            INSERT INTO convexity_reviews (
              review_id, case_id, reviewed_at, primary_convexity_source,
              maximum_controllable_loss, nonlinear_upside_path,
              ignition_conditions, odds_decay_conditions,
              remaining_convexity, invalidation_window,
              supporting_evidence_json, counter_evidence_json,
              open_questions_json, reviewer_type, conclusion_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?, ?,
                    'rule_engine', ?)
            """,
            (
                review_id,
                record["caseId"],
                now,
                "尚未形成可核验主凸性来源",
                "尚未形成可执行结构，最大可控亏损暂无法计算",
                "尚未形成由事实到订单、资金或价格的非线性传导路径",
                record["ignitionCondition"],
                "缺少事件后价格反应与事实升级数据，暂不能判断赔率衰减",
                "关键身份、价值捕获、风险或交易性出现冲突时立即失效",
                json.dumps(record["supportingEvidence"], ensure_ascii=False),
                json.dumps(record["counterEvidence"], ensure_ascii=False),
                json.dumps(record["openQuestions"], ensure_ascii=False),
                RULE_VERSION,
            ),
        )
        dimension_payload = {
            "evidenceQuality": record["evidenceDimensions"],
            "convexityReadiness": record["readinessDimensions"],
            "mismatch": record["mismatchComponents"],
        }
        connection.execute(
            """
            INSERT INTO machine_research_scores (
              machine_score_id, case_id, run_id, mismatch_score_id,
              convexity_review_id, scored_at, lifecycle_bucket,
              lifecycle_label, evidence_quality_score, mismatch_score,
              convexity_readiness_score, confidence, dimension_scores_json,
              blockers_json, source_evidence_ids_json, source_url,
              scoring_boundary, rule_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                machine_score_id,
                record["caseId"],
                run_id,
                mismatch_id,
                review_id,
                now,
                record["lifecycleBucket"],
                record["lifecycleLabel"],
                record["evidenceQualityScore"],
                record["mismatchScore"],
                record["convexityReadinessScore"],
                record["confidence"],
                json.dumps(dimension_payload, ensure_ascii=False),
                json.dumps(record["blockers"], ensure_ascii=False),
                json.dumps(record["sourceEvidenceIds"], ensure_ascii=False),
                record["sourceUrl"],
                (
                    "三个分数只用于资料质量、研究排序和凸性闭环准备度；"
                    "不直接改变当前动作，也不构成买卖建议。"
                ),
                RULE_VERSION,
            ),
        )

        changes = []
        comparisons = (
            (
                "证据质量分",
                previous["evidence_quality_score"] if previous else None,
                record["evidenceQualityScore"],
            ),
            (
                "事实新闻错配分",
                previous["mismatch_score"] if previous else None,
                record["mismatchScore"],
            ),
            (
                "凸性准备度",
                previous["convexity_readiness_score"] if previous else None,
                record["convexityReadinessScore"],
            ),
            (
                "事实成熟度",
                record["previousMaturity"],
                record["maturity"],
            ),
        )
        for field, before, after in comparisons:
            if previous is None or before != after:
                changes.append(
                    {
                        "field": field,
                        "before": before,
                        "after": after,
                    }
                )
        if changes:
            summary["changedProjects"] += 1
        connection.execute(
            """
            UPDATE candidate_cases
            SET maturity_level = ?,
                updated_at = ?
            WHERE case_id = ?
            """,
            (record["maturity"], now, record["caseId"]),
        )
        payload = {
            "summary": (
                f"{record['lifecycleLabel']}机器评分：证据质量 "
                f"{record['evidenceQualityScore']}，错配 "
                f"{record['mismatchScore']}，凸性准备度 "
                f"{record['convexityReadinessScore']}；当前动作保持"
                f"{record['currentAction']}。"
            ),
            "evidenceQualityScore": record["evidenceQualityScore"],
            "mismatchScore": record["mismatchScore"],
            "convexityReadinessScore": record["convexityReadinessScore"],
            "confidence": record["confidence"],
            "blockers": record["blockers"],
            "changes": changes,
        }
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url,
              excerpt, project_hint, asset_hint, chain_hint, event_type,
              raw_payload_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                    'machine_research_scoring_refresh', ?, 'normalized')
            """,
            (
                stable_id("machine-score-event", run_id, record["caseId"]),
                SOURCE_DEFINITION["source_id"],
                run_id,
                f"{run_id}:{record['caseId']}:machine-score",
                now,
                now,
                hashlib.sha256(
                    json.dumps(
                        payload, ensure_ascii=False, sort_keys=True
                    ).encode("utf-8")
                ).hexdigest(),
                record["sourceUrl"],
                payload["summary"],
                record["projectName"],
                record["symbol"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        summary["lifecycleCounts"][record["lifecycleBucket"]] += 1
        summary[
            {
                "high": "highConfidence",
                "medium": "mediumConfidence",
                "low": "lowConfidence",
                "insufficient": "insufficient",
            }[record["confidence"]]
        ] += 1
        summary["mismatchAbove65"] += int(record["mismatchScore"] >= 65)
        summary["readinessAbove65"] += int(
            record["convexityReadinessScore"] >= 65
        )
    return summary
