#!/usr/bin/env python3
"""Build the C2.2 screening, tracking and joined front snapshots.

The first C2.2 build intentionally consumes the already published C2.1
candidate snapshot as the screening source.  This preserves the live C2.1
hard-gate and four-path implementation while giving C2.2 separate ownership
and a stable assetId join.  The C2.2 pipeline can later replace the source
producer without changing this page contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
DEFAULT_FRONT = APP_ROOT / "c2-2-front-snapshot.js"
DEFAULT_TRACKING = APP_ROOT / "c2-2-tracking-snapshot.js"
DEFAULT_ADMIN = APP_ROOT / "c2-2-admin-snapshot.js"
C21_FRONT_PREFIX = "window.PENGUIN_CONVEXITY_C21 = "
C21_ADMIN_PREFIX = "window.PENGUIN_CONVEXITY_C21_ADMIN = "
C22_FRONT_PREFIX = "window.PENGUIN_CONVEXITY_C22 = "
C22_TRACKING_PREFIX = "window.PENGUIN_CONVEXITY_C22_TRACKING = "
C22_ADMIN_PREFIX = "window.PENGUIN_CONVEXITY_C22_ADMIN = "

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tracking_tasks_snapshot import load_js_payload  # noqa: E402
from build_c2_2_calibration import build_calibration_payload, write_calibration_payload  # noqa: E402
from c2_2_tracking import build_bayes_evidence, load_main_database_facts, load_tracking_catalog  # noqa: E402


ALLOWED_RELATIONSHIPS = {"A", "B", "C"}
ALLOWED_CHANGE_TYPES = {
    "t0",
    "age_band",
    "relationship_class",
    "hard_gate",
    "product_evidence",
    "display_state",
    "strong_path",
    "factor_direction",
    "confidence",
    "risk",
    "exit_90_days",
}


def _sha(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _candidate_id(project_id: Any) -> int | None:
    text = str(project_id or "")
    if not text.startswith("c21-"):
        return None
    try:
        return int(text.split("-", 1)[1])
    except ValueError:
        return None


def _age_band(item: dict[str, Any]) -> str:
    age = item.get("ageDays")
    try:
        age_value = int(age)
    except (TypeError, ValueError):
        age_value = 91
    return "age_0_30" if 0 <= age_value <= 30 else "age_31_90"


def _tracking_state(item: dict[str, Any]) -> str:
    history = item.get("observationHistory") or {}
    market = item.get("marketSnapshot") or {}
    paths = item.get("evidencePaths") or []
    formed = [path for path in paths if (path or {}).get("status") == "formed"]
    if len(formed) >= 2 and any((path or {}).get("pathCode") == "trade_liquidity_formation" for path in formed):
        return "convexity_clue"
    if history.get("backfilledDays", 0) or market.get("sourceStatus") in {"success", "healthy", "ok"}:
        return "active_project" if formed else "observing"
    if history.get("validHistoryDays", 0):
        return "observing"
    return "awaiting_first_tracking"


def _hard_gate_summary(item: dict[str, Any]) -> dict[str, Any]:
    hard_gate = item.get("hardGate") or {}
    return {
        "status": hard_gate.get("status") or "unknown",
        "checks": hard_gate.get("checks") or [],
        "checkedAt": hard_gate.get("checkedAt"),
        "ruleVersion": hard_gate.get("ruleVersion"),
    }


def _product_evidence_summary(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("productEvidence") or {}
    return {
        "hasAnyQualifyingEvidence": bool(evidence.get("hasAnyQualifyingEvidence")),
        "qualifyingTypes": list(evidence.get("qualifyingTypes") or []),
        "plainSummary": evidence.get("plainSummary") or "",
        "github": evidence.get("github"),
        "deployedProduct": evidence.get("deployedProduct"),
        "structuredBusiness": evidence.get("structuredBusiness"),
        "executedTokenUtility": evidence.get("executedTokenUtility"),
        "productUsage": evidence.get("productUsage"),
    }


def _tracking_item(
    item: dict[str, Any],
    candidate_build_id: str,
    source_cutoff: str,
    bayes: dict[str, Any] | None = None,
    *,
    catalog_record: dict[str, Any] | None = None,
    main_database_fact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = _tracking_state(item)
    market = item.get("marketSnapshot")
    history = item.get("observationHistory") or {}
    confidence = item.get("dataConfidence")
    bayes = bayes or {}
    bayes_factors = bayes.get("factors") or []
    inherited_directions = item.get("factorDirections") or []
    factor_directions = [{"factor": row.get("factor"), "direction": row.get("direction")} for row in bayes_factors] or inherited_directions
    catalog_record = catalog_record or {}
    series = catalog_record.get("series") or {}
    market_series = list(series.get("market") or [])
    supply_series = list(series.get("supply") or [])
    pool_series = list(series.get("pool") or [])
    risk_series = list(series.get("risk") or [])
    main_database_fact = main_database_fact or {
        "matched": False,
        "assetId": catalog_record.get("mainAssetId") or item.get("assetId"),
        "matchMethod": None,
        "reason": "主库没有同 assetId 事实；不按名称、Symbol或数组顺序继承。",
    }
    main_database_fact = {
        "matched": bool(main_database_fact.get("matched")),
        "assetId": main_database_fact.get("assetId") or catalog_record.get("mainAssetId") or item.get("assetId"),
        "matchMethod": main_database_fact.get("matchMethod"),
        "reason": main_database_fact.get("reason"),
        "facts": {
            "asset": main_database_fact.get("asset"),
            "candidateCase": main_database_fact.get("candidateCase"),
            "market": main_database_fact.get("market"),
            "contractRisk": main_database_fact.get("contractRisk"),
            "assetContract": main_database_fact.get("assetContract"),
            "tradeability": main_database_fact.get("tradeability"),
        },
    }
    risk_summary = dict(item.get("riskSummary") or {})
    if risk_series:
        risk_summary["history"] = risk_series
    return {
        "assetId": item.get("assetId"),
        "projectId": item.get("projectId"),
        "trackingState": state,
        "marketHistory": {
            "latest": market,
            "series": market_series,
            "observationHistory": history,
        },
        "liquidityAndExit": {
            "liquidityUsd": (market or {}).get("liquidityUsd") if market else None,
            "standardSellQuoteState": (market or {}).get("standardSellQuoteState") if market else None,
            "standardSellQuoteLossPct": (market or {}).get("standardSellQuoteLossPct") if market else None,
            "standardSellNotionalUsd": (market or {}).get("standardSellNotionalUsd") if market else None,
            "series": [
                {
                    "observedAt": row.get("observedAt"),
                    "liquidityUsd": row.get("liquidityUsd"),
                    "standardSellNotionalUsd": row.get("standardSellNotionalUsd"),
                    "standardSellQuoteState": row.get("standardSellQuoteState"),
                    "standardSellQuoteLossPct": row.get("standardSellQuoteLossPct"),
                    "sourceName": row.get("sourceName"),
                }
                for row in market_series
            ],
        },
        "addressAndSupply": {
            "current": item.get("addressAndSupply") or item.get("supplySummary"),
            "history": supply_series,
            "poolHistory": pool_series,
        },
        "productUsage": (item.get("productEvidence") or {}).get("productUsage"),
        "riskAndAnomalies": risk_summary,
        "mainDatabaseFacts": main_database_fact,
        "inputLineage": {
            "candidateDatabase": "data/c2.1-pipeline.db",
            "mainDatabase": "data/convexity.db",
            "mainDatabaseMatch": bool(main_database_fact.get("matched")),
            "matchMethod": main_database_fact.get("matchMethod"),
        },
        "marketSeries": market_series,
        "liquiditySeries": [
            {
                "observedAt": row.get("observedAt"),
                "liquidityUsd": row.get("liquidityUsd"),
                "sourceName": row.get("sourceName"),
                "sourceStatus": row.get("sourceStatus"),
            }
            for row in market_series
        ],
        "standardSellQuoteHistory": [
            {
                "observedAt": row.get("observedAt"),
                "standardSellNotionalUsd": row.get("standardSellNotionalUsd"),
                "standardSellQuoteState": row.get("standardSellQuoteState"),
                "standardSellQuoteLossPct": row.get("standardSellQuoteLossPct"),
                "sourceName": row.get("sourceName"),
            }
            for row in market_series
        ],
        "addressAndSupplyHistory": supply_series,
        "productUsageHistory": [],
        "riskAndAnomaliesHistory": risk_series,
        "evidenceRefs": sorted({evidence_id for path in (item.get("evidencePaths") or []) for evidence_id in (path.get("evidenceIds") or [])}),
        "factorPosteriors": {
            "directions": factor_directions,
            "indicators": bayes.get("indicators") or {},
            "factors": bayes_factors,
            "total": bayes.get("total"),
            "confidenceScore": bayes.get("confidenceScore"),
            "modelVersion": bayes.get("modelVersion") or "c2.2-deterministic-hierarchical-eb-v1",
            "inputSource": bayes.get("inputSource") or "data/c2.1-pipeline.db:current_and_historical_observations",
            "priorSource": (item.get("thresholdContext") or {}).get("cohortScope"),
            "measuredIndicatorCount": (bayes.get("total") or {}).get("measuredIndicatorCount"),
            "effectiveWindowCount": history.get("validHistoryDays"),
        },
        "confidence": confidence,
        "strongPaths": item.get("evidencePaths") or [],
        "trackingChanges": [_public_change_record(item.get("latestMaterialChange"))] if item.get("latestMaterialChange") else [],
        "counterEvidence": [
            evidence
            for path in (item.get("evidencePaths") or [])
            for evidence in (path.get("counterEvidence") or [])
        ],
        "materialChanges": [_public_change_record(item.get("latestMaterialChange"))] if item.get("latestMaterialChange") else [],
        "confidenceComponents": (confidence or {}).get("components") if isinstance(confidence, dict) else None,
        "lastCompleteTrackingAt": history.get("lastSuccessfulAt") or source_cutoff,
        "candidateBuildId": candidate_build_id,
    }


def _screening_item(item: dict[str, Any], screening_build_id: str) -> dict[str, Any]:
    t0 = item.get("t0") or {}
    return {
        "assetId": item.get("assetId"),
        "projectId": item.get("projectId"),
        "canonicalName": item.get("canonicalName") or "",
        "symbol": item.get("symbol") or "",
        "chainId": item.get("chainId") or "",
        "contractAddress": item.get("contractAddressMasked"),
        "effectiveT0": t0.get("value"),
        "ageDays": item.get("ageDays"),
        "ageBand": _age_band(item),
        "relationshipClass": item.get("relationshipClass"),
        "hardGate": _hard_gate_summary(item),
        "productEvidence": _product_evidence_summary(item),
        "screeningChange": item.get("latestMaterialChange"),
        "detailHref": item.get("detailUrl") or "",
        "sourceProjectObject": item.get("projectId"),
        "screeningBuildId": screening_build_id,
    }


def _front_item(screening: dict[str, Any], tracking: dict[str, Any] | None) -> dict[str, Any]:
    source = screening.get("sourceItem") or {}
    tracking = tracking or {}
    state = tracking.get("trackingState") or "awaiting_first_tracking"
    paths = tracking.get("strongPaths") or []
    formed = sum(1 for path in paths if (path or {}).get("status") == "formed")
    market = (tracking.get("marketHistory") or {}).get("latest")
    confidence = tracking.get("confidence")
    return {
        "assetId": screening.get("assetId"),
        "projectId": screening.get("projectId"),
        "canonicalName": screening.get("canonicalName") or source.get("canonicalName") or "",
        "symbol": screening.get("symbol") or source.get("symbol") or "",
        "chainId": screening.get("chainId") or source.get("chainId") or "",
        "effectiveT0": screening.get("effectiveT0"),
        "ageDays": screening.get("ageDays"),
        "ageBand": screening.get("ageBand"),
        "relationshipClass": screening.get("relationshipClass"),
        "hardGateSummary": screening.get("hardGate"),
        "productEvidenceSummary": screening.get("productEvidence"),
        "trackingState": state,
        "factorDirections": ((tracking.get("factorPosteriors") or {}).get("directions") or []),
        "confidenceSummary": confidence,
        "strongPathSummary": {
            "formedCount": formed,
            "requiredCount": 2,
            "hasTradeAndLiquidity": any((path or {}).get("pathCode") == "trade_liquidity_formation" and (path or {}).get("status") == "formed" for path in paths),
        },
        "marketSummary": market,
        "riskSummary": tracking.get("riskAndAnomalies"),
        "latestMaterialChange": _public_change_record((tracking.get("trackingChanges") or [None])[0] or screening.get("screeningChange")),
        "dataCutoffAt": tracking.get("lastCompleteTrackingAt"),
        "sortReason": _sort_reason(state, formed, confidence),
        "detailHref": screening.get("detailHref") or source.get("detailUrl") or "",
        "_trackingInternal": {
            "missing": not bool(tracking),
            "source": "c2.1-inherited-tracking-fields",
        },
    }


def _sort_reason(state: str, formed: int, confidence: dict[str, Any] | None) -> str:
    confidence_level = (confidence or {}).get("level") or "未形成"
    if state == "convexity_clue":
        return f"凸性线索；{formed}条强证据路径形成；可信度{confidence_level}。"
    if state == "active_project":
        return f"活跃项目；{formed}条强证据路径形成；可信度{confidence_level}。"
    if state == "observing":
        return f"持续观察；已完成跟踪但强路径未满足；可信度{confidence_level}。"
    return "等待首轮跟踪；筛选已通过，暂无完整跟踪结果。"


def build_payloads(
    c21_front: dict[str, Any],
    c21_admin: dict[str, Any],
    *,
    tracking_catalog: dict[int, dict[str, Any]] | None = None,
    previous_tracking: dict[str, Any] | None = None,
    main_database_facts: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_items = list(c21_front.get("items") or [])
    generated_at = c21_front.get("generatedAt") or c21_admin.get("generatedAt") or ""
    source_cutoff = c21_front.get("sourceCutoffAt") or generated_at
    eligible = [
        item for item in source_items
        if (item.get("hardGate") or {}).get("status") == "pass"
        and item.get("relationshipClass") in ALLOWED_RELATIONSHIPS
        and 0 <= int(item.get("ageDays", 91)) <= 90
    ]
    screening_build_id = f"c22-screening-{_sha({'source': c21_front.get('buildId'), 'items': [item.get('assetId') for item in eligible]})[:16]}"
    screening_items = [_screening_item(item, screening_build_id) for item in sorted(eligible, key=lambda row: str(row.get("assetId") or ""))]
    by_asset = {item.get("assetId"): item for item in eligible if item.get("assetId")}
    previous_by_asset = {row.get("assetId"): row for row in (previous_tracking or {}).get("items", [])}
    tracking_items = []
    for item in sorted(eligible, key=lambda row: str(row.get("assetId") or "")):
        candidate_id = _candidate_id(item.get("projectId"))
        record = (tracking_catalog or {}).get(candidate_id or -1, {})
        bayes = build_bayes_evidence(item, tracking_catalog or {}, (previous_by_asset.get(item.get("assetId")) or {}).get("factorPosteriors")) if tracking_catalog else None
        main_asset_id = record.get("mainAssetId") or item.get("assetId")
        main_fact = (main_database_facts or {}).get(main_asset_id) or (main_database_facts or {}).get(item.get("assetId"))
        tracking_items.append(
            _tracking_item(
                item,
                screening_build_id,
                source_cutoff,
                bayes,
                catalog_record=record,
                main_database_fact=main_fact,
            )
        )
    tracking_by_asset = {item.get("assetId"): item for item in tracking_items}

    screening = {
        "schemaVersion": "c2.2-screening-v1",
        "buildId": screening_build_id,
        "generatedAt": generated_at,
        "sourceCutoffAt": source_cutoff,
        "ruleVersion": c21_front.get("ruleVersion"),
        "ruleConfigHash": c21_front.get("ruleConfigHash"),
        "candidateCount": len(screening_items),
        "items": screening_items,
        "coverageSummary": c21_front.get("coverageSummary") or {},
        "sourceImpactSummary": c21_front.get("sourceImpactSummary") or {},
        "database": {
            "candidateDatabase": "data/c2.1-pipeline.db",
            "mainDatabase": "data/convexity.db",
            "mainDatabaseMode": "read_only_supplementary_lineage",
        },
    }
    tracking_build_id = f"c22-tracking-{_sha({'candidateBuildId': screening_build_id, 'items': tracking_items})[:16]}"
    tracking = {
        "schemaVersion": "c2.2-tracking-v1",
        "buildId": tracking_build_id,
        "candidateBuildId": screening_build_id,
        "generatedAt": generated_at,
        "sourceCutoffAt": source_cutoff,
        "modelVersion": "c2.2-deterministic-hierarchical-eb-v1",
        "bayesSpecHash": _sha({"spec": "docs/C2.2_BAYES_SPEC.md", "version": "c2.2-bayes-v1"}),
        "database": {
            "candidateDatabase": "data/c2.1-pipeline.db",
            "mainDatabase": "data/convexity.db",
            "mainDatabaseMode": "read_only_supplementary_lineage",
        },
        "items": tracking_items,
        "stateCounts": _state_counts(tracking_items),
        "sourceImpactSummary": c21_front.get("sourceImpactSummary") or {},
    }
    front_items = []
    for screening_item in screening_items:
        original = by_asset.get(screening_item.get("assetId"), {})
        screening_item_for_front = {**screening_item, "sourceItem": original}
        front_items.append(_front_item(screening_item_for_front, tracking_by_asset.get(screening_item.get("assetId"))))
    changes = _changes(c21_front.get("materialChanges") or [], front_items)
    front = {
        "schemaVersion": "c2.2-front-v1",
        "buildId": f"c22-front-{_sha({'candidateBuildId': screening_build_id, 'trackingBuildId': tracking_build_id})[:16]}",
        "candidateBuildId": screening_build_id,
        "trackingBuildId": tracking_build_id,
        "generatedAt": generated_at,
        "sourceCutoffAt": source_cutoff,
        "coverageSummary": {
            **(c21_front.get("coverageSummary") or {}),
            "frontVisibleCount": len(front_items),
        },
        "lifecycleCounts": _lifecycle_counts(screening_items),
        "trackingStateCounts": _state_counts(tracking_items),
        "items": front_items,
        "changes": changes,
    }
    admin = {
        "schemaVersion": "c2.2-admin-v1",
        "buildId": f"c22-admin-{_sha({'front': front['buildId'], 'tracking': tracking_build_id})[:16]}",
        "generatedAt": generated_at,
        "sourceCutoffAt": source_cutoff,
        "screening": screening,
        "tracking": tracking,
        "jobs": _jobs(c21_admin, generated_at),
        "config": _runtime_config(),
        "sourceHealth": c21_admin.get("sourceHealth") or [],
        "cursors": c21_admin.get("cursors") or [],
        "runs": c21_admin.get("runs") or [],
        "quality": c21_admin.get("quality") or {},
        "inheritance": {
            "screeningSource": "c2.1-front-snapshot",
            "trackingSource": "c2.1-front-tracking-fields",
            "joinKey": "assetId",
            "dataLimitedFrontStateRemoved": True,
        },
    }
    return front, tracking, admin


def _state_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in ("convexity_clue", "active_project", "observing", "awaiting_first_tracking")}
    for item in items:
        state = item.get("trackingState")
        if state in counts:
            counts[state] += 1
    return counts


def _lifecycle_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"age_0_30": 0, "age_31_90": 0, "A": 0, "B": 0, "C": 0}
    for item in items:
        for key in (item.get("ageBand"), item.get("relationshipClass")):
            if key in counts:
                counts[key] += 1
    return counts


def _changes(changes: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known_assets = {item.get("assetId") for item in items}
    asset_by_project = {item.get("projectId"): item.get("assetId") for item in items if item.get("projectId") and item.get("assetId")}
    href_by_project = {item.get("projectId"): item.get("detailHref") for item in items if item.get("projectId")}
    output = []
    for change in changes:
        if change.get("changeType") not in ALLOWED_CHANGE_TYPES:
            continue
        asset_id = change.get("assetId") or asset_by_project.get(change.get("projectId"))
        if change.get("projectId") and not asset_id:
            continue
        if asset_id and asset_id not in known_assets:
            continue
        output.append({
            "changeId": change.get("changeId"),
            "assetId": asset_id,
            "projectId": change.get("projectId"),
            "changedAt": change.get("changedAt"),
            "changeType": _public_change_type(change.get("changeType")),
            "previousValue": _public_change_value(change.get("previousValue")),
            "currentValue": _public_change_value(change.get("currentValue")),
            "whyItMatters": change.get("whyItMatters") or "判断输入发生变化。",
            "sourceCutoffAt": change.get("sourceCutoffAt"),
            "detailHref": change.get("detailUrl") or href_by_project.get(change.get("projectId")) or "",
        })
    for item in items:
        latest = item.get("latestMaterialChange") or {}
        if latest.get("changeId") and not any(row.get("changeId") == latest.get("changeId") for row in output):
            output.append({
                "changeId": latest.get("changeId"),
                "assetId": item.get("assetId"),
                "projectId": item.get("projectId"),
                "changedAt": latest.get("changedAt"),
                "changeType": _public_change_type(latest.get("changeType") or "tracking_state"),
                "previousValue": _public_change_value(latest.get("previousValue")),
                "currentValue": _public_change_value(latest.get("currentValue")),
                "whyItMatters": latest.get("whyItMatters") or "跟踪判断发生变化。",
                "sourceCutoffAt": latest.get("sourceCutoffAt"),
                "detailHref": item.get("detailHref") or "",
            })
    return sorted(output, key=lambda row: (str(row.get("changedAt") or ""), str(row.get("changeId") or "")), reverse=True)[:500]


def _public_change_value(value: Any) -> Any:
    """Keep historical transitions understandable without reviving the old public state."""

    labels = {
        "data_limited": "旧版未形成完整跟踪",
        "early_observation": "等待首轮跟踪",
        "continuous_observation": "观察中",
        "convexity_clue": "凸性线索",
        "active_project": "活跃项目",
        "observing": "观察中",
        "awaiting_first_tracking": "等待首轮跟踪",
    }
    return labels.get(value, value)


def _public_change_type(value: Any) -> Any:
    return "跟踪状态" if value == "display_state" else value


def _public_change_record(change: dict[str, Any] | None) -> dict[str, Any] | None:
    if not change:
        return None
    return {
        **change,
        "changeType": _public_change_type(change.get("changeType")),
        "previousValue": _public_change_value(change.get("previousValue")),
        "currentValue": _public_change_value(change.get("currentValue")),
    }


def _jobs(admin: dict[str, Any], generated_at: str) -> dict[str, Any]:
    try:
        from c2_2_runtime import job_status as load_c22_job_status
        runtime_jobs = {code: load_c22_job_status(code) for code in ("screening", "convexity_tracking")}
    except Exception:
        runtime_jobs = {}
    runs = list(admin.get("runs") or [])
    latest = sorted(runs, key=lambda row: str(row.get("updated_at") or row.get("finished_at") or ""), reverse=True)
    last = latest[0] if latest else {}
    state = last.get("state") or "completed"
    fallback = {
        "screening": {
            "jobCode": "screening",
            "state": state,
            "trigger": "resume",
            "frequency": "24h",
            "paused": False,
            "runId": last.get("run_id"),
            "stage": last.get("stage") or "snapshot_published",
            "progress": {"completed": last.get("completed_units"), "total": last.get("total_units")},
            "lastHeartbeatAt": last.get("updated_at") or generated_at,
            "lastCompletedAt": last.get("finished_at") or generated_at,
            "checkpoint": None,
            "sourceFailures": [],
            "objectFailures": [],
            "nextDueAt": None,
        },
        "convexity_tracking": {
            "jobCode": "convexity_tracking",
            "state": "completed",
            "trigger": "resume",
            "frequency": "24h",
            "paused": False,
            "runId": None,
            "stage": "snapshot_published",
            "progress": {"completed": len(admin.get("runs") or []), "total": len(admin.get("runs") or [])},
            "lastHeartbeatAt": generated_at,
            "lastCompletedAt": generated_at,
            "checkpoint": None,
            "sourceFailures": [],
            "objectFailures": [],
            "nextDueAt": None,
        },
    }
    return {code: runtime_jobs.get(code) or fallback[code] for code in ("screening", "convexity_tracking")}


def _runtime_config() -> dict[str, Any]:
    try:
        from c2_2_runtime import load_config
        return load_config()
    except Exception:
        return {
            "schemaVersion": "c2.2-update-config-v1",
            "timezone": "Asia/Shanghai",
            "jobs": {
                "screening": {"mode": "automatic", "intervalHours": 24, "paused": False},
                "convexity_tracking": {"mode": "automatic", "intervalHours": 24, "paused": False},
            },
        }


def _validate_payloads(front: dict[str, Any], tracking: dict[str, Any], admin: dict[str, Any]) -> None:
    front_assets = {row.get("assetId") for row in front.get("items") or []}
    tracking_assets = {row.get("assetId") for row in tracking.get("items") or []}
    if None in front_assets or None in tracking_assets or front_assets != tracking_assets:
        raise ValueError("C2.2前台与跟踪项目集合不一致，拒绝发布")
    if tracking.get("candidateBuildId") != front.get("candidateBuildId"):
        raise ValueError("C2.2跟踪引用的candidateBuildId不一致，拒绝发布")
    if (admin.get("screening") or {}).get("buildId") != front.get("candidateBuildId"):
        raise ValueError("C2.2后台筛选快照与统一前台候选版本不一致，拒绝发布")
    if not all(payload.get("schemaVersion") for payload in (front, tracking, admin)):
        raise ValueError("C2.2快照缺少schemaVersion，拒绝发布")


def _write_snapshot_group(target: Path, payloads: list[tuple[str, dict[str, Any], str]]) -> None:
    """Publish three snapshots as one recoverable file group.

    All temporary files are prepared and parsed before the old files are moved
    aside.  If any replacement fails, the previous complete set is restored.
    The `.previous` files are deliberately retained as a local rollback point.
    """

    target.mkdir(parents=True, exist_ok=True)
    temp_paths: list[Path] = []
    backups: list[tuple[Path, Path]] = []
    committed: list[Path] = []
    backup_dir = PROJECT_ROOT / "runtime" / "c2.2" / "snapshot-backups" if target.resolve() == APP_ROOT.resolve() else target / ".c2.2-previous"
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        for name, payload, prefix in payloads:
            path = target / name
            temp = path.with_name(path.name + ".tmp")
            text = prefix + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + ";\n"
            # Parse the JSON body before any live file is touched.
            json.loads(text[len(prefix):-2])
            temp.write_text(text, encoding="utf-8")
            temp_paths.append(temp)
        for name, _payload, _prefix in payloads:
            path = target / name
            backup = backup_dir / f"{path.name}.previous"
            if backup.exists():
                backup.unlink()
            if path.exists():
                os.replace(path, backup)
                backups.append((path, backup))
        for temp in temp_paths:
            path = target / temp.name[:-4]
            os.replace(temp, path)
            committed.append(path)
    except Exception:
        for temp in temp_paths:
            if temp.exists():
                temp.unlink()
        for path in committed:
            if path.exists():
                path.unlink()
        for path, backup in reversed(backups):
            if backup.exists():
                os.replace(backup, path)
        raise


def build_snapshots(
    *,
    c21_front_path: Path,
    c21_admin_path: Path,
    output_dir: Path | None = None,
    write: bool = True,
    tracking_db_path: Path | None = None,
    main_db_path: Path | None = None,
) -> dict[str, Any]:
    c21_front = load_js_payload(c21_front_path, C21_FRONT_PREFIX)
    c21_admin = load_js_payload(c21_admin_path, C21_ADMIN_PREFIX)
    target = output_dir or APP_ROOT
    previous_tracking = None
    previous_path = target / DEFAULT_TRACKING.name
    if previous_path.exists():
        try:
            previous_tracking = load_js_payload(previous_path, C22_TRACKING_PREFIX)
        except (OSError, ValueError, json.JSONDecodeError):
            previous_tracking = None
    tracking_catalog = load_tracking_catalog(tracking_db_path or (PROJECT_ROOT / "data" / "c2.1-pipeline.db"))
    main_ids = {str(item.get("assetId")) for item in c21_front.get("items") or [] if item.get("assetId")}
    for record in tracking_catalog.values():
        if record.get("mainAssetId"):
            main_ids.add(str(record["mainAssetId"]))
    main_database_facts = load_main_database_facts(main_db_path or (PROJECT_ROOT / "data" / "convexity.db"), main_ids)
    front, tracking, admin = build_payloads(
        c21_front,
        c21_admin,
        tracking_catalog=tracking_catalog,
        previous_tracking=previous_tracking,
        main_database_facts=main_database_facts,
    )
    try:
        calibration = build_calibration_payload(front, tracking, tracking_db_path or (PROJECT_ROOT / "data" / "c2.1-pipeline.db"))
    except Exception as error:
        calibration = {
            "schemaVersion": "c2.2-calibration-v1",
            "modelVersion": "c2.2-deterministic-hierarchical-eb-v1",
            "ruleVersion": "c2.2-bayes-v1",
            "state": "program_failure",
            "reason": f"校准报告未生成：{type(error).__name__}: {error}",
            "parameterMutation": "none",
            "summary": {},
            "items": [],
        }
    admin["calibration"] = calibration
    admin["buildId"] = f"c22-admin-{_sha({'front': front['buildId'], 'tracking': tracking['buildId'], 'calibration': calibration})[:16]}"
    _validate_payloads(front, tracking, admin)
    if write:
        _write_snapshot_group(
            target,
            [
                (DEFAULT_FRONT.name, front, C22_FRONT_PREFIX),
                (DEFAULT_TRACKING.name, tracking, C22_TRACKING_PREFIX),
                (DEFAULT_ADMIN.name, admin, C22_ADMIN_PREFIX),
            ],
        )
        if target.resolve() == APP_ROOT.resolve() and calibration.get("state") != "program_failure":
            write_calibration_payload(calibration)
    return {"front": front, "tracking": tracking, "admin": admin}


def main() -> int:
    parser = argparse.ArgumentParser(description="构建C2.2筛选、跟踪和统一前台快照")
    parser.add_argument("--c21-front", type=Path, default=APP_ROOT / "c2-1-front-snapshot.js")
    parser.add_argument("--c21-admin", type=Path, default=APP_ROOT / "c2-1-admin-snapshot.js")
    parser.add_argument("--output-dir", type=Path, default=APP_ROOT)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    payloads = build_snapshots(c21_front_path=args.c21_front, c21_admin_path=args.c21_admin, output_dir=args.output_dir, write=not args.check_only)
    print(json.dumps({
        "schemaVersion": payloads["front"]["schemaVersion"],
        "frontBuildId": payloads["front"]["buildId"],
        "candidateBuildId": payloads["front"]["candidateBuildId"],
        "trackingBuildId": payloads["front"]["trackingBuildId"],
        "frontVisibleCount": len(payloads["front"]["items"]),
        "trackingStateCounts": payloads["front"]["trackingStateCounts"],
        "wrote": not args.check_only,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
