#!/usr/bin/env python3
"""Summarize C2.5 read-only rule, source, trace, and snapshot behavior on formal assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from c2_5_control_plane import C25ControlPlane
from c2_5_rules import normalize_rule_replay_item
from c2_4_rule_replay import RuleReplayInputError
from c2_4_rules import evaluate_strong_paths, load_config


STRONG_PATH_RULES = {
    "strong_path_trade_demand_state": "trade_demand_formation",
    "strong_path_liquidity_exit_state": "liquidity_exit_quality",
    "strong_path_supply_holder_state": "supply_holder_improvement",
    "strong_path_indexed_pool_state": "indexed_pool_activity_vs_supply_adjusted_valuation",
}


def impact_calculation_is_j05_ready(calculation: dict) -> bool:
    return bool(
        calculation.get("verificationRequired") is True
        and calculation.get("complete") is True
        and calculation.get("approvalBlocked") is False
        and not calculation.get("executorMismatchAssetIds")
        and not calculation.get("executorMissingEvidenceAssetIds")
        and not calculation.get("calculationErrors")
    )


def direct_strong_path_executor_truth(
    items: list[dict],
    *,
    source_version: str,
    target_version: str,
    rule_path: Path,
) -> dict:
    config = load_config(rule_path)
    rows = {rule_id: [] for rule_id in STRONG_PATH_RULES}
    errors = []
    for raw in items:
        asset_id = str(raw.get("assetId") or raw.get("asset_id") or "").strip()
        if not asset_id:
            continue
        try:
            item = normalize_rule_replay_item(raw, require_replay_inputs=True)
        except RuleReplayInputError as error:
            errors.append({"assetId": asset_id, "code": error.code, "error": str(error)})
            continue
        eligible = item.get("strongPathEvaluationEligible") is True
        source = {row["pathCode"]: row["status"] for row in evaluate_strong_paths(item, config=config, active_version=source_version)} if eligible else {
            path_code: "unavailable" for path_code in STRONG_PATH_RULES.values()
        }
        target = {row["pathCode"]: row["status"] for row in evaluate_strong_paths(item, config=config, active_version=target_version)} if eligible else {
            path_code: "unavailable" for path_code in STRONG_PATH_RULES.values()
        }
        for rule_id, path_code in STRONG_PATH_RULES.items():
            rows[rule_id].append({"assetId": asset_id, "sourceState": source[path_code], "targetState": target[path_code]})
    return {
        "rules": {rule_id: {
            "stateChangedAssetIds": sorted(row["assetId"] for row in values if row["sourceState"] != row["targetState"]),
            "executorStateDigest": hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        }
        for rule_id, values in rows.items()},
        "errors": errors,
    }


def candidate_rebuilt_tracking_sample(root: Path) -> dict:
    """Build the candidate tracking snapshot in memory from the formal DB without writing it."""

    import build_c2_4_snapshots as builder

    original_root = builder.PROJECT_ROOT
    try:
        builder.PROJECT_ROOT = root
        tracking = builder.build_snapshots(
            db_path=root / "data" / "c2.1-pipeline.db",
            output_dir=root / "app",
            write=False,
        )["tracking"]
    finally:
        builder.PROJECT_ROOT = original_root
    return {
        "sourcePath": "candidate-read-only-rebuild:data/c2.1-pipeline.db",
        "sourceSha256": tracking.get("contentSha256"),
        "snapshotId": tracking.get("buildId"),
        "dataAsOf": tracking.get("dataCutoffAt"),
        "readOnly": True,
        "sampleSourceKind": "candidate_read_only_rebuild_from_formal_db",
        "items": tracking.get("items") or [],
    }


def compact_rule(row: dict) -> dict:
    return {
        key: row.get(key)
        for key in (
            "ruleId",
            "plainName",
            "baselineValue",
            "effectiveValue",
            "difference",
            "unit",
            "status",
            "baselineVersion",
            "effectiveVersion",
            "sourcePath",
            "sourceSha256",
            "effectiveSourcePath",
            "effectiveSourceSha256",
            "approvedBy",
            "approvedAt",
            "counts",
            "addedAssetIds",
            "removedAssetIds",
            "stateChangedAssetIds",
            "executorStateDigest",
            "executorMismatchAssetIds",
            "executorMissingEvidenceAssetIds",
            "passExample",
            "nonPassExample",
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="C2.5 formal product read-only probe")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--candidate-rebuild", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    plane = C25ControlPlane(project_root=root, windows_reader=lambda: [])
    current_sample = candidate_rebuilt_tracking_sample(root) if args.candidate_rebuild else plane._tracking_rule_sample()
    rules = plane.rules_payload(current_sample=current_sample)
    governance = rules.get("governance") or {}
    rollback_target = next(
        (row.get("version") for row in governance.get("knownVersions") or [] if row.get("version") != governance.get("activeVersion")),
        None,
    )
    rollback_preview = plane.rule_change_preview(rollback_target, current_sample=current_sample) if rollback_target else {}
    snapshots = plane.snapshots_payload()
    chains = plane.chains_sources_payload()
    replay_rows = (rules.get("replay") or {}).get("rows") or []
    trace_asset_id = next((row.get("assetId") for row in replay_rows if row.get("assetId")), None)
    trace = plane.decision_trace_payload(trace_asset_id) if trace_asset_id else {"status": "unavailable", "reason": "没有可追溯的真实assetId"}
    trace_summary = {
        key: trace.get(key)
        for key in (
            "status",
            "assetId",
            "identity",
            "t0",
            "evidence",
            "dataTimes",
            "ruleResults",
            "waitOrFailureReasons",
            "businessState",
            "snapshotRefs",
            "frontendVisibility",
            "ranking",
            "path",
        )
    }
    compact_snapshots = [
        {
            key: row.get(key)
            for key in (
                "snapshotId",
                "schemaVersion",
                "producerTaskId",
                "builtAt",
                "dataAsOf",
                "atomic",
                "complete",
                "stale",
                "objectCount",
                "assetIdDigest",
                "validation",
                "consumerTaskIds",
                "consumerPages",
                "lastSuccessfulHandoff",
                "lastFailedHandoff",
                "lifecycleStateField",
                "convexityTrackingStateField",
                "path",
            )
        }
        for row in snapshots.get("snapshots") or []
    ]
    required_rules = {
        "public_eligibility_result",
        "public_risk_source_success",
        "public_no_confirmed_hard_block",
        "public_no_confirmed_severe_anomaly",
        "strong_path_trade_demand_state",
        "strong_path_liquidity_exit_state",
        "strong_path_supply_holder_state",
        "strong_path_indexed_pool_state",
        "immediate_exit_state",
        "public_sell_quote_loss",
        "strong_path_sell_quote_loss",
        "severe_immediate_exit_loss",
        "sell_quote_loss_pct_lte_10_or_15",
        "sell_quote_loss_pct_gte_20_immediate_exit",
        "liquidity_drop_pct_gte_80_path_invalidation",
        "supply_decimals_or_unit_change_path_invalidation",
        "cross_source_price_deviation_pct_gte_25_path_pause",
        "sell_tax_pct_gte_20_as_hard_block",
    }
    observed_rules = {row.get("ruleId") for row in rules.get("rules") or []}
    replay_sets = rules.get("replaySets") or {}
    fixed_replay = replay_sets.get("fixedHistorical") or {}
    current_replay = replay_sets.get("currentReadOnly") or {}
    current_rule_union = sorted({
        asset_id
        for row in current_replay.get("ruleImpacts") or []
        for asset_id in row.get("stateChangedAssetIds") or []
    })
    rollback_rule_union = sorted({
        asset_id
        for row in (rollback_preview.get("currentReadOnly") or {}).get("ruleImpacts") or []
        for asset_id in row.get("stateChangedAssetIds") or []
    })
    current_truth_result = direct_strong_path_executor_truth(
        current_sample["items"],
        source_version=replay_sets.get("sourceVersion"),
        target_version=replay_sets.get("targetVersion"),
        rule_path=root / "docs" / "C2.4_RULE_CONFIG.json",
    )
    rollback_truth_result = direct_strong_path_executor_truth(
        current_sample["items"],
        source_version=rollback_preview.get("sourceVersion"),
        target_version=rollback_preview.get("targetVersion"),
        rule_path=root / "docs" / "C2.4_RULE_CONFIG.json",
    ) if rollback_target else {"rules": {}, "errors": []}
    current_truth = current_truth_result["rules"]
    rollback_truth = rollback_truth_result["rules"]
    current_impacts = {row.get("ruleId"): row for row in current_replay.get("ruleImpacts") or []}
    rollback_impacts = {row.get("ruleId"): row for row in (rollback_preview.get("currentReadOnly") or {}).get("ruleImpacts") or []}
    current_executor_matches = all(
        current_impacts.get(rule_id, {}).get("stateChangedAssetIds") == truth["stateChangedAssetIds"]
        and current_impacts.get(rule_id, {}).get("executorStateDigest") == truth["executorStateDigest"]
        for rule_id, truth in current_truth.items()
    )
    rollback_executor_matches = all(
        rollback_impacts.get(rule_id, {}).get("stateChangedAssetIds") == truth["stateChangedAssetIds"]
        and rollback_impacts.get(rule_id, {}).get("executorStateDigest") == truth["executorStateDigest"]
        for rule_id, truth in rollback_truth.items()
    )
    current_calculation = (current_replay.get("replay") or {}).get("impactCalculation") or {}
    rollback_calculation = ((rollback_preview.get("currentReadOnly") or {}).get("replay") or {}).get("impactCalculation") or {}

    passed = bool(
        rules.get("status") == "ready"
        and required_rules == observed_rules
        and rules.get("effective", {}).get("reconciledRuleCount") == len(required_rules)
        and rules.get("effective", {}).get("expectedRuleCount") == len(required_rules)
        and sum(row.get("effectiveValue") == "disabled_as_gate" for row in rules.get("rules") or []) == 6
        and rules.get("replay", {}).get("sameInput")
        and rules.get("replay", {}).get("assetIdSetRecomputed")
        and fixed_replay.get("sampleKind") == "fixed_historical"
        and current_replay.get("sampleKind") == "current_read_only"
        and fixed_replay.get("readOnly") is True
        and current_replay.get("readOnly") is True
        and int((fixed_replay.get("replay") or {}).get("inputCount") or 0) > 0
        and fixed_replay.get("sourcePath") != current_replay.get("sourcePath")
        and current_replay.get("replay", {}).get("unionMatchesPerRule") is True
        and current_replay.get("replay", {}).get("affectedAssetIds") == current_rule_union
        and current_executor_matches
        and not current_truth_result["errors"]
        and impact_calculation_is_j05_ready(current_calculation)
        and rules.get("governanceApprovalBlocked") is False
        and rollback_preview.get("fixedHistorical", {}).get("sampleKind") == "fixed_historical"
        and rollback_preview.get("currentReadOnly", {}).get("sampleKind") == "current_read_only"
        and rollback_preview.get("currentReadOnly", {}).get("replay", {}).get("unionMatchesPerRule") is True
        and rollback_preview.get("affectedAssetIds") == rollback_rule_union
        and rollback_executor_matches
        and not rollback_truth_result["errors"]
        and impact_calculation_is_j05_ready(rollback_calculation)
        and rollback_preview.get("approvalBlocked") is False
        and set(rollback_preview.get("affectedTaskIds") or []) == {"c22.screening", "c22.convexity_tracking"}
        and len(rollback_preview.get("affectedSnapshots") or []) == 3
        and all(row.get("snapshotId") for row in rollback_preview.get("affectedSnapshots") or [])
        and trace.get("status") == "ready"
        and len(compact_snapshots) == 5
        and all(row.get("complete") for row in compact_snapshots)
        and all(row.get("validation", {}).get("format") == "passed" for row in compact_snapshots)
        and all(row.get("quickCheck") == "ok" and row.get("foreignKeyViolations") == 0 and row.get("readOnly") for row in snapshots.get("databases") or [])
    )
    payload = {
        "schemaVersion": "c2.5-formal-product-readonly-probe-v1",
        "status": "passed" if passed else "failed",
        "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "projectRoot": str(root),
        "sampleSourceKind": current_sample.get("sampleSourceKind") or "installed_formal_tracking_snapshot",
        "j05Ready": passed,
        "rules": {
            "status": rules.get("status"),
            "frozenBaseline": rules.get("frozenBaseline"),
            "effective": rules.get("effective"),
            "activeOverride": rules.get("activeOverride"),
            "rules": [compact_rule(row) for row in rules.get("rules") or []],
            "replay": {
                key: rules.get("replay", {}).get(key)
                for key in (
                    "inputCount",
                    "baselinePassedCount",
                    "effectivePassedCount",
                    "addedAssetIds",
                    "removedAssetIds",
                    "publicEligibilityAffectedAssetIds",
                    "affectedAssetIds",
                    "affectedAssetCount",
                    "affectedByArea",
                    "governedRuleCount",
                    "unionMatchesPerRule",
                    "impactCalculation",
                    "sameInput",
                    "assetIdSetRecomputed",
                )
            },
            "replaySets": replay_sets,
            "rollbackPreview": rollback_preview,
            "executorTruthReconciliation": {
                "snapshotId": current_sample.get("snapshotId"),
                "sourceSha256": current_sample.get("sourceSha256"),
                "currentPreviewMatchesC24Executor": current_executor_matches,
                "rollbackPreviewMatchesC24Executor": rollback_executor_matches,
                "currentImpactCalculation": current_calculation,
                "rollbackImpactCalculation": rollback_calculation,
                "strongPathChangedCounts": {rule_id: len(row["stateChangedAssetIds"]) for rule_id, row in current_truth.items()},
                "currentDirectExecutorErrors": current_truth_result["errors"],
                "rollbackDirectExecutorErrors": rollback_truth_result["errors"],
            },
            "bayesBoundary": rules.get("bayesBoundary"),
        },
        "decisionTrace": trace_summary,
        "snapshots": compact_snapshots,
        "databases": snapshots.get("databases"),
        "stateBoundary": snapshots.get("stateBoundary"),
        "managerCompositionWritesBusinessDatabases": snapshots.get("managerCompositionWritesBusinessDatabases"),
        "chainSource": {
            "chainOrder": chains.get("chainOrder"),
            "summary": chains.get("summary"),
            "rowCount": len(chains.get("rows") or []),
            "scopePolicy": chains.get("scopePolicy"),
            "dataAsOf": chains.get("dataAsOf"),
        },
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
