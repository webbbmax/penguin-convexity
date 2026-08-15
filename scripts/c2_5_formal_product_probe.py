#!/usr/bin/env python3
"""Summarize C2.5 read-only rule, source, trace, and snapshot behavior on formal assets."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from c2_5_control_plane import C25ControlPlane


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
            "passExample",
            "nonPassExample",
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="C2.5 formal product read-only probe")
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    plane = C25ControlPlane(project_root=root, windows_reader=lambda: [])
    rules = plane.rules_payload()
    governance = rules.get("governance") or {}
    rollback_target = next(
        (row.get("version") for row in governance.get("knownVersions") or [] if row.get("version") != governance.get("activeVersion")),
        None,
    )
    rollback_preview = plane.rule_change_preview(rollback_target) if rollback_target else {}
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
        and rollback_preview.get("fixedHistorical", {}).get("sampleKind") == "fixed_historical"
        and rollback_preview.get("currentReadOnly", {}).get("sampleKind") == "current_read_only"
        and rollback_preview.get("currentReadOnly", {}).get("replay", {}).get("unionMatchesPerRule") is True
        and rollback_preview.get("affectedAssetIds") == rollback_rule_union
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
                    "sameInput",
                    "assetIdSetRecomputed",
                )
            },
            "replaySets": replay_sets,
            "rollbackPreview": rollback_preview,
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
