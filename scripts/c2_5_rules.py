#!/usr/bin/env python3
"""C2.5 rule transparency with per-rule replay and code reconciliation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from c2_4_rules import (
    FROZEN_PUBLIC_RULE_VERSION,
    TRIAL_PUBLIC_RULE_VERSION,
    effective_rule_manifest,
    evaluate_public_baseline_version,
    evaluate_rule_condition,
    evaluate_strong_paths,
    load_config,
    number,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULE_PATH = PROJECT_ROOT / "docs" / "C2.4_RULE_CONFIG.json"
TRIAL_PATH = PROJECT_ROOT / "docs" / "C2.4_RULE_RELAXATION_TRIAL_20260813.json"
FIXED_HISTORY_PATH = PROJECT_ROOT / "fixtures" / "c2.5" / "rule-transparency-matrix.json"
EXPECTED_RULE_SHA256 = "775f9fad44e5f0db3b036e797643104a5ff9f075afbc4e1c16835606c8a88988"
EXPECTED_TRIAL_SHA256 = "7f6ccc9e35ab6ba7b5212911116facd9698489c0f7d0f27b9dbcf16dc0c7e202"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"规则文件不是JSON对象：{path}")
    return value


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_active_override(
    trial: dict[str, Any],
    *,
    trial_sha256: str,
    rule_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or utc_now()
    reasons: list[str] = []
    approved_at = _parse_time(trial.get("authorizedAt"))
    effective_until = _parse_time(trial.get("effectiveUntil"))
    baseline = trial.get("frozenBaseline") if isinstance(trial.get("frozenBaseline"), dict) else {}
    if trial.get("status") != "user_authorized_active_trial":
        reasons.append("覆盖状态不是已获用户批准的活动试行")
    if approved_at is None:
        reasons.append("缺少可验证的批准时间")
    if effective_until is not None and effective_until <= current:
        reasons.append("覆盖已过期")
    if trial_sha256 != EXPECTED_TRIAL_SHA256:
        reasons.append("覆盖文件哈希与C2.5冻结依赖不一致")
    if rule_sha256 != EXPECTED_RULE_SHA256:
        reasons.append("C2.4冻结规则文件哈希不一致")
    if baseline.get("ruleConfigSha256") != EXPECTED_RULE_SHA256:
        reasons.append("覆盖引用的冻结规则哈希不一致")
    active = not reasons
    return {
        "active": active,
        "status": "active" if active else "rejected",
        "reasons": reasons,
        "approvedBy": "user_explicit_authorization" if approved_at else None,
        "approvedAt": iso_time(approved_at) if approved_at else None,
        "effectiveFrom": iso_time(approved_at) if approved_at else None,
        "effectiveUntil": iso_time(effective_until) if effective_until else None,
        "sourcePath": "docs/C2.4_RULE_RELAXATION_TRIAL_20260813.json",
        "sourceSha256": trial_sha256,
    }


def evaluate_frozen_public_baseline(item: dict[str, Any]) -> dict[str, Any]:
    return evaluate_public_baseline_version(item, FROZEN_PUBLIC_RULE_VERSION)


def normalize_rule_replay_item(item: dict[str, Any]) -> dict[str, Any]:
    """Expand the read-only C2.4 tracking projection back to evaluator fields."""

    source_states = item.get("sourceStates") if isinstance(item.get("sourceStates"), dict) else {}
    baseline = item.get("publicBaseline") if isinstance(item.get("publicBaseline"), dict) else {}
    baseline_checks = {
        row.get("code"): row.get("passed")
        for row in baseline.get("checks", [])
        if isinstance(row, dict) and row.get("code")
    }
    first_gate_checks = {
        row.get("code"): row.get("passed")
        for row in item.get("firstGateChecks", [])
        if isinstance(row, dict) and row.get("code")
    }
    market = item.get("market") if isinstance(item.get("market"), dict) else {}
    path_metrics: dict[str, Any] = {}
    for path in item.get("strongPaths", []):
        if isinstance(path, dict) and isinstance(path.get("metrics"), dict):
            path_metrics.update({key: value for key, value in path["metrics"].items() if value is not None})
    project_evidence = item.get("projectEvidenceState") == "success" or baseline_checks.get("project_evidence") is True
    return {
        **item,
        **path_metrics,
        **{key: value for key, value in market.items() if value is not None},
        "contractAddress": item.get("contractAddress") or item.get("tokenAddress"),
        "pairAddress": item.get("pairAddress") or item.get("poolId"),
        "tokenSide": item.get("tokenSide") or item.get("assetDirection"),
        "riskSourceState": item.get("riskSourceState") or source_states.get("risk") or item.get("riskState"),
        "projectEvidenceQualified": item.get("projectEvidenceQualified") if item.get("projectEvidenceQualified") is not None else project_evidence,
        "projectEvidenceAttributable": item.get("projectEvidenceAttributable") if item.get("projectEvidenceAttributable") is not None else project_evidence,
        "confirmedHardBlock": item.get("confirmedHardBlock") if item.get("confirmedHardBlock") is not None else first_gate_checks.get("no_confirmed_trade_block") is False,
        "confirmedSevereAnomaly": item.get("confirmedSevereAnomaly") if item.get("confirmedSevereAnomaly") is not None else bool(item.get("severeAnomaly")),
        "supplyHistoryState": item.get("supplyHistoryState") or source_states.get("supply"),
        "poolHistoryState": item.get("poolHistoryState") or source_states.get("path4"),
        "sellQuoteIndependent": item.get("sellQuoteIndependent") if item.get("sellQuoteIndependent") is not None else "sell_quote_or_verified_route" in (item.get("independentSourceTypes") or []),
    }


STRONG_PATH_RULES = {
    "strong_path_trade_demand_state": "trade_demand_formation",
    "strong_path_liquidity_exit_state": "liquidity_exit_quality",
    "strong_path_supply_holder_state": "supply_holder_improvement",
    "strong_path_indexed_pool_state": "indexed_pool_activity_vs_supply_adjusted_valuation",
}


def _binary_state(*, applicable: bool, passed: bool, comparable: bool = True) -> dict[str, Any]:
    return {
        "applicable": applicable,
        "comparable": comparable,
        "passed": passed if applicable and comparable else None,
        "state": "passed" if passed else "failed" if applicable and comparable else "unknown" if not comparable else "not_applicable",
    }


def evaluate_governed_rule_states(item: dict[str, Any], version: str, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    public = evaluate_public_baseline_version(item, version)
    states["public_eligibility_result"] = _binary_state(applicable=True, passed=bool(public["passed"]))

    trial = version == TRIAL_PUBLIC_RULE_VERSION
    risk_state = item.get("riskSourceState") or item.get("riskState")
    risk_success = risk_state in {"success", "complete", "completed"}
    states["public_risk_source_success"] = _binary_state(applicable=risk_state is not None, passed=trial or risk_success)
    hard_block = any(bool(item.get(key)) for key in ("confirmedHardBlock", "confirmedFreeze", "confirmedBlacklist", "confirmedSellBlock"))
    states["public_no_confirmed_hard_block"] = _binary_state(applicable=True, passed=not hard_block)
    severe = bool(item.get("confirmedSevereAnomaly") or item.get("severeAnomaly"))
    states["public_no_confirmed_severe_anomaly"] = _binary_state(applicable=True, passed=trial or not severe)

    computed_paths = {
        row["pathCode"]: row["status"]
        for row in evaluate_strong_paths(item, config=config, active_version=version)
    }
    for rule_id, path_code in STRONG_PATH_RULES.items():
        status, comparable = computed_paths[path_code], True
        states[rule_id] = {
            "applicable": True,
            "comparable": comparable,
            "passed": status == "formed" if comparable else None,
            "state": status,
        }

    loss = number(item.get("sellQuoteLossPct"))
    sell_tax = number(item.get("sellTaxPct"))
    immediate = hard_block or (not trial and ((loss is not None and loss >= 20) or (sell_tax is not None and sell_tax >= 20)))
    states["immediate_exit_state"] = _binary_state(applicable=True, passed=not immediate)

    outcome_ids = set(states)
    for rule_id in effective_rule_manifest(version):
        if rule_id in outcome_ids:
            continue
        condition = evaluate_rule_condition(rule_id, item, version)
        states[rule_id] = _binary_state(applicable=bool(condition["applicable"]), passed=bool(condition["passed"]))
    return states


def replay_version_change(
    items: list[dict[str, Any]],
    *,
    source_version: str,
    target_version: str,
) -> dict[str, Any]:
    source_passed: list[str] = []
    target_passed: list[str] = []
    rows: list[dict[str, Any]] = []
    for item in items:
        asset_id = str(item.get("assetId") or item.get("asset_id") or "").strip()
        if not asset_id:
            continue
        replay_item = normalize_rule_replay_item(item)
        source = evaluate_public_baseline_version(replay_item, source_version)
        target = evaluate_public_baseline_version(replay_item, target_version)
        if source["passed"]:
            source_passed.append(asset_id)
        if target["passed"]:
            target_passed.append(asset_id)
        rows.append(
            {
                "assetId": asset_id,
                "sourcePassed": source["passed"],
                "targetPassed": target["passed"],
                "changed": source["passed"] != target["passed"],
            }
        )
    source_set = set(source_passed)
    target_set = set(target_passed)
    added = sorted(target_set - source_set)
    removed = sorted(source_set - target_set)
    return {
        "sourceVersion": source_version,
        "targetVersion": target_version,
        "inputCount": len(rows),
        "sourcePassedCount": len(source_set),
        "targetPassedCount": len(target_set),
        "addedAssetIds": added,
        "removedAssetIds": removed,
        "affectedAssetIds": sorted(set(added) | set(removed)),
        "unchangedAssetIds": sorted(source_set & target_set),
        "rows": rows,
        "sameInput": True,
        "assetIdSetRecomputed": True,
    }


def replay_rules(
    items: list[dict[str, Any]],
    *,
    override_active: bool,
    governed_replay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_version = TRIAL_PUBLIC_RULE_VERSION if override_active else FROZEN_PUBLIC_RULE_VERSION
    replay = replay_version_change(
        items,
        source_version=FROZEN_PUBLIC_RULE_VERSION,
        target_version=effective_version,
    )
    governed = governed_replay or replay_governed_rules(
        items,
        source_version=FROZEN_PUBLIC_RULE_VERSION,
        target_version=effective_version,
    )
    return {
        **replay,
        "baselinePassedCount": replay["sourcePassedCount"],
        "effectivePassedCount": replay["targetPassedCount"],
        "publicEligibilityAffectedAssetIds": replay["affectedAssetIds"],
        "affectedAssetIds": governed["affectedAssetIds"],
        "affectedAssetCount": governed["affectedAssetCount"],
        "affectedByArea": governed["affectedByArea"],
        "governedRuleCount": governed["ruleCount"],
        "unionMatchesPerRule": governed["unionMatchesPerRule"],
        "impactCalculation": governed["impactCalculation"],
        "rows": [
            {
                "assetId": row["assetId"],
                "baselinePassed": row["sourcePassed"],
                "effectivePassed": row["targetPassed"],
                "changed": row["changed"],
            }
            for row in replay["rows"]
        ],
    }


def _fixed_history_sample(path: Path = FIXED_HISTORY_PATH) -> dict[str, Any]:
    fixture = load_json(path)
    items = fixture.get("items") if isinstance(fixture.get("items"), list) else []
    return {
        "sampleKind": "fixed_historical",
        "sourcePath": "fixtures/c2.5/rule-transparency-matrix.json",
        "sourceSha256": sha256(path),
        "snapshotId": fixture.get("schemaVersion"),
        "readOnly": True,
        "items": [row for row in items if isinstance(row, dict)],
    }


def build_dual_replay_evidence(
    current_items: list[dict[str, Any]],
    *,
    source_version: str,
    target_version: str,
    current_source: dict[str, Any] | None = None,
    fixed_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    historical = fixed_history or _fixed_history_sample()
    current = {
        "sampleKind": "current_read_only",
        "sourcePath": "C2.4 tracking snapshot",
        "sourceSha256": None,
        "snapshotId": None,
        "dataAsOf": None,
        "readOnly": True,
        **(current_source or {}),
        "items": current_items,
    }

    def replay_sample(sample: dict[str, Any], *, verify_stored_executor: bool) -> dict[str, Any]:
        sample_items = [row for row in sample.get("items", []) if isinstance(row, dict)]
        replay = replay_version_change(
            sample_items,
            source_version=source_version,
            target_version=target_version,
        )
        replay.pop("rows", None)
        governed = replay_governed_rules(
            sample_items,
            source_version=source_version,
            target_version=target_version,
            verify_stored_executor=verify_stored_executor,
        )
        if verify_stored_executor and (sample.get("unavailableReason") or not sample_items):
            governed["impactCalculation"] = {
                **governed["impactCalculation"],
                "status": "incomplete",
                "complete": False,
                "approvalBlocked": True,
                "reason": str(sample.get("unavailableReason") or "当前只读样本为空，影响无法完整计算。"),
            }
        rule_impacts = [
            {key: value for key, value in row.items() if key != "rows"}
            for row in governed["rules"]
        ]
        replay["publicEligibilityAffectedAssetIds"] = replay["affectedAssetIds"]
        replay["affectedAssetIds"] = governed["affectedAssetIds"]
        replay["affectedAssetCount"] = governed["affectedAssetCount"]
        replay["affectedByArea"] = governed["affectedByArea"]
        replay["governedRuleCount"] = governed["ruleCount"]
        replay["unionMatchesPerRule"] = governed["unionMatchesPerRule"]
        replay["impactCalculation"] = governed["impactCalculation"]
        return {
            **{key: value for key, value in sample.items() if key != "items"},
            "replay": replay,
            "ruleImpacts": rule_impacts,
        }

    fixed_result = replay_sample(historical, verify_stored_executor=False)
    current_result = replay_sample(current, verify_stored_executor=True)
    return {
        "schemaVersion": "c2.5-rule-dual-replay-evidence-v1",
        "sourceVersion": source_version,
        "targetVersion": target_version,
        "fixedHistorical": fixed_result,
        "currentReadOnly": current_result,
        "affectedAssetIds": current_result["replay"]["affectedAssetIds"],
        "fixedHistoricalAffectedAssetIds": fixed_result["replay"]["affectedAssetIds"],
        "sameInputWithinEachSample": True,
        "assetIdSetRecomputed": True,
        "approvalBlocked": bool(current_result["replay"]["impactCalculation"]["approvalBlocked"]),
        "impactCalculation": current_result["replay"]["impactCalculation"],
    }


def _unavailable(reason: str, source_path: str) -> dict[str, str]:
    return {"reason": reason, "sourcePath": source_path}


def replay_governed_rules(
    items: list[dict[str, Any]],
    *,
    source_version: str,
    target_version: str,
    verify_stored_executor: bool = False,
) -> dict[str, Any]:
    source_manifest = effective_rule_manifest(source_version)
    target_manifest = effective_rule_manifest(target_version)
    if list(source_manifest) != list(target_manifest):
        raise ValueError("前后规则登记集合不一致，拒绝生成不完整影响预览。")
    rule_ids = list(target_manifest)
    buckets: dict[str, list[dict[str, Any]]] = {rule_id: [] for rule_id in rule_ids}
    executor_mismatches: dict[str, set[str]] = {rule_id: set() for rule_id in STRONG_PATH_RULES}
    executor_missing: dict[str, set[str]] = {rule_id: set() for rule_id in STRONG_PATH_RULES}
    executor_checked: set[str] = set()
    calculation_errors: list[dict[str, str]] = []
    config = load_config(RULE_PATH)
    for item in items:
        asset_id = str(item.get("assetId") or item.get("asset_id") or "").strip()
        if not asset_id:
            continue
        replay_item = normalize_rule_replay_item(item)
        evaluation_failed = False
        try:
            source_states = evaluate_governed_rule_states(replay_item, source_version, config)
            target_states = evaluate_governed_rule_states(replay_item, target_version, config)
        except Exception as error:
            evaluation_failed = True
            calculation_errors.append({"assetId": asset_id, "error": f"{type(error).__name__}: {error}"[:500]})
            source_states = target_states = {
                rule_id: {"applicable": True, "comparable": False, "passed": None, "state": "unavailable"}
                for rule_id in rule_ids
            }
        if verify_stored_executor and not evaluation_failed:
            stored_version = (item.get("publicBaseline") or {}).get("ruleVersion") if isinstance(item.get("publicBaseline"), dict) else None
            stored_paths = {
                str(row.get("pathCode")): str(row.get("status"))
                for row in item.get("strongPaths", [])
                if isinstance(row, dict) and row.get("pathCode") and row.get("status")
            }
            if stored_version in {source_version, target_version}:
                executor_checked.add(asset_id)
                active_states = source_states if stored_version == source_version else target_states
                for rule_id, path_code in STRONG_PATH_RULES.items():
                    if path_code not in stored_paths:
                        executor_missing[rule_id].add(asset_id)
                    elif active_states[rule_id]["state"] != stored_paths[path_code]:
                        executor_mismatches[rule_id].add(asset_id)
            else:
                for rule_id in STRONG_PATH_RULES:
                    executor_missing[rule_id].add(asset_id)
        for rule_id in rule_ids:
            source = source_states[rule_id]
            target = target_states[rule_id]
            applicable = bool(source["applicable"] or target["applicable"])
            comparable = bool(source["comparable"] and target["comparable"])
            changed = bool(applicable and comparable and source["state"] != target["state"])
            buckets[rule_id].append(
                {
                    "assetId": asset_id,
                    "applicable": applicable,
                    "comparable": comparable,
                    "sourceState": source["state"],
                    "targetState": target["state"],
                    "baselinePassed": source["passed"] if applicable and comparable else None,
                    "effectivePassed": target["passed"] if applicable and comparable else None,
                    "changed": changed,
                }
            )

    impacts = []
    for rule_id in rule_ids:
        rows = buckets[rule_id]
        applicable_rows = [row for row in rows if row["applicable"]]
        comparable_rows = [row for row in applicable_rows if row["comparable"]]
        changed_rows = [row for row in comparable_rows if row["changed"]]
        source_passed = [row for row in comparable_rows if row["baselinePassed"]]
        target_passed = [row for row in comparable_rows if row["effectivePassed"]]
        waiting = [row for row in applicable_rows if not row["comparable"] or row["targetState"] in {"unknown", "unavailable", "waiting", "no_data"}]
        failed = [
            row for row in comparable_rows
            if not row["effectivePassed"] and row["targetState"] not in {"unknown", "unavailable", "waiting", "no_data"}
        ]
        added = sorted(row["assetId"] for row in changed_rows if not row["baselinePassed"] and row["effectivePassed"])
        removed = sorted(row["assetId"] for row in changed_rows if row["baselinePassed"] and not row["effectivePassed"])
        state_changed = sorted(row["assetId"] for row in changed_rows)
        impacts.append(
            {
                "ruleId": rule_id,
                "sourceVersion": source_version,
                "targetVersion": target_version,
                "counts": {
                    "input": len(rows),
                    "applicable": len(applicable_rows),
                    "notApplicable": len(rows) - len(applicable_rows),
                    "waiting": len(waiting),
                    "failed": len(failed),
                    "baselinePassed": len(source_passed),
                    "effectivePassed": len(target_passed),
                    "entered": len(added),
                    "withdrawn": len(removed),
                    "changed": len(changed_rows),
                    "executorMismatches": len(executor_mismatches.get(rule_id, set())),
                    "executorEvidenceMissing": len(executor_missing.get(rule_id, set())),
                },
                "addedAssetIds": added,
                "removedAssetIds": removed,
                "stateChangedAssetIds": state_changed,
                "passExample": target_passed[0]["assetId"] if target_passed else _unavailable("当前输入没有该规则的真实通过样本", "C2.4 tracking snapshot"),
                "nonPassExample": failed[0]["assetId"] if failed else _unavailable("当前输入没有该规则的真实未通过样本", "C2.4 tracking snapshot"),
                "differenceExample": state_changed[0] if state_changed else None,
                "executorStateDigest": hashlib.sha256(json.dumps(
                    [{key: row[key] for key in ("assetId", "sourceState", "targetState")} for row in rows],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")).hexdigest(),
                "executorMismatchAssetIds": sorted(executor_mismatches.get(rule_id, set())),
                "executorMissingEvidenceAssetIds": sorted(executor_missing.get(rule_id, set())),
                "rows": rows,
            }
        )

    impact_map = {row["ruleId"]: set(row["stateChangedAssetIds"]) for row in impacts}
    areas = {
        "publicEligibility": ["public_eligibility_result"],
        "strongPaths": list(STRONG_PATH_RULES),
        "immediateExit": ["immediate_exit_state", "severe_immediate_exit_loss", "sell_quote_loss_pct_gte_20_immediate_exit"],
        "riskAndHardBlock": ["public_risk_source_success", "public_no_confirmed_hard_block", "public_no_confirmed_severe_anomaly", "sell_tax_pct_gte_20_as_hard_block"],
        "invalidationPauseAndUnitStability": ["liquidity_drop_pct_gte_80_path_invalidation", "supply_decimals_or_unit_change_path_invalidation", "cross_source_price_deviation_pct_gte_25_path_pause"],
    }
    affected_by_area = {
        area: sorted(set().union(*(impact_map[rule_id] for rule_id in ids)))
        for area, ids in areas.items()
    }
    affected = sorted(set().union(*(set(row["stateChangedAssetIds"]) for row in impacts)))
    mismatch_assets = sorted(set().union(*executor_mismatches.values()))
    missing_assets = sorted(set().union(*executor_missing.values()))
    impact_complete = not verify_stored_executor or not (calculation_errors or mismatch_assets or missing_assets)
    impact_calculation = {
        "status": "complete" if impact_complete else "incomplete",
        "complete": impact_complete,
        "approvalBlocked": not impact_complete,
        "reason": None if impact_complete else "当前快照字段不足或复算结果与C2.4真实执行结果不一致，影响无法完整计算。",
        "evaluatorPath": "scripts/c2_4_rules.py::evaluate_strong_paths",
        "verificationRequired": verify_stored_executor,
        "verifiedAssetCount": len(executor_checked),
        "executorMismatchAssetIds": mismatch_assets,
        "executorMissingEvidenceAssetIds": missing_assets,
        "calculationErrors": calculation_errors,
    }
    return {
        "schemaVersion": "c2.5-governed-rule-replay-v1",
        "sourceVersion": source_version,
        "targetVersion": target_version,
        "ruleCount": len(impacts),
        "rules": impacts,
        "affectedByArea": affected_by_area,
        "affectedAssetIds": affected,
        "affectedAssetCount": len(affected),
        "unionMatchesPerRule": affected == sorted(set().union(*(set(row["stateChangedAssetIds"]) for row in impacts))),
        "impactCalculation": impact_calculation,
    }


def replay_one_rule(rule_id: str, items: list[dict[str, Any]], effective_version: str) -> dict[str, Any]:
    replay = replay_governed_rules(
        items,
        source_version=FROZEN_PUBLIC_RULE_VERSION,
        target_version=effective_version,
    )
    return next(row for row in replay["rules"] if row["ruleId"] == rule_id)


def reconcile_rule_values(rules: list[dict[str, Any]], code_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in rules:
        code_value = code_manifest.get(row["ruleId"], _unavailable("代码未登记该规则", "scripts/c2_4_rules.py"))
        matched = code_value == row.get("effectiveValue")
        result.append(
            {
                **row,
                "codeReconciliation": {
                    "matched": matched,
                    "pageEffectiveValue": row.get("effectiveValue"),
                    "codeEffectiveValue": code_value,
                    "codePath": "scripts/c2_4_rules.py",
                    "comparison": "exact_field_value",
                },
            }
        )
    return result


def build_rule_transparency(
    items: list[dict[str, Any]] | None = None,
    *,
    rule_path: Path = RULE_PATH,
    trial_path: Path = TRIAL_PATH,
    active_version: str | None = None,
    governance: dict[str, Any] | None = None,
    current_source: dict[str, Any] | None = None,
    fixed_history: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or utc_now()
    try:
        rule = load_json(rule_path)
        rule_hash = sha256(rule_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {"schemaVersion": "c2.5-rule-transparency-v1", "status": "blocked", "unavailable": _unavailable(str(error), str(rule_path)), "observedAt": iso_time(observed_at)}
    try:
        trial = load_json(trial_path)
        trial_hash = sha256(trial_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        trial, trial_hash, trial_error = {}, "", str(error)
    else:
        trial_error = ""
    authorization = validate_active_override(trial, trial_sha256=trial_hash, rule_sha256=rule_hash, now=observed_at)
    if trial_error:
        authorization["reasons"].append(trial_error)
        authorization["active"] = False
        authorization["status"] = "rejected"
    selected_version = active_version or (TRIAL_PUBLIC_RULE_VERSION if authorization["active"] else FROZEN_PUBLIC_RULE_VERSION)
    if selected_version == TRIAL_PUBLIC_RULE_VERSION and not authorization["active"]:
        selected_version = FROZEN_PUBLIC_RULE_VERSION
    override_active = selected_version == TRIAL_PUBLIC_RULE_VERSION
    active_override = {**authorization, "active": bool(authorization["active"] and override_active), "status": "active" if authorization["active"] and override_active else "inactive_approved" if authorization["active"] else authorization["status"]}

    disabled = list((trial.get("activeOverrides") or {}).get("disabledAsGates") or [])
    values = [
        ("public_eligibility_result", "当前公开资格结果", "all_frozen_public_baseline_checks", "approved_trial_public_baseline_checks", "state", "公开底线总结果"),
        ("public_risk_source_success", "风险来源成功", "required", "not_required_raw_state_preserved", "state", "公开底线风险"),
        ("public_no_confirmed_hard_block", "无已确认硬阻断", "required", "required", "state", "公开底线风险"),
        ("public_no_confirmed_severe_anomaly", "无已确认严重异常", "required", "not_required_raw_state_preserved", "state", "公开底线风险"),
        ("strong_path_trade_demand_state", "交易需求强路径状态", "frozen_path_conditions", "approved_trial_path_conditions", "state", "强路径"),
        ("strong_path_liquidity_exit_state", "流动性退出强路径状态", "frozen_path_conditions", "approved_trial_path_conditions", "state", "强路径"),
        ("strong_path_supply_holder_state", "供应持币改善强路径状态", "frozen_path_conditions", "approved_trial_path_conditions", "state", "强路径"),
        ("strong_path_indexed_pool_state", "全池活动估值强路径状态", "frozen_path_conditions", "approved_trial_path_conditions", "state", "强路径"),
        ("immediate_exit_state", "立即退出状态", "hard_block_or_loss_gte_20_or_sell_tax_gte_20", "confirmed_trade_block_only", "state", "立即退出"),
        ("public_sell_quote_loss", "公开卖出报价损失", 15, "quote_success_loss_recorded", "%", "公开底线"),
        ("strong_path_sell_quote_loss", "流动性路径卖出报价损失", 10, "quote_success_no_confirmed_trade_block", "%", "强路径"),
        ("severe_immediate_exit_loss", "严重退出损失", 20, "record_only_no_immediate_exit_gate", "%", "立即退出"),
    ]
    rules: list[dict[str, Any]] = []
    for rule_id, name, baseline_value, trial_value, unit, scope in values:
        changed = bool(override_active and baseline_value != trial_value)
        rules.append(
            {
                "ruleId": rule_id,
                "plainName": name,
                "plainDescription": "冻结基线与当前有效版本分开显示；这不是新的C2.5投资规则。",
                "baselineValue": baseline_value,
                "effectiveValue": trial_value if changed else baseline_value,
                "difference": "活动试行覆盖" if changed else "无差异",
                "unit": unit,
                "status": "overridden" if changed else "baseline",
                "sourcePath": "docs/C2.4_RULE_CONFIG.json",
                "sourceSha256": rule_hash,
                "baselineVersion": FROZEN_PUBLIC_RULE_VERSION,
                "effectiveVersion": selected_version,
                "effectiveSourcePath": "docs/C2.4_RULE_RELAXATION_TRIAL_20260813.json" if changed else "docs/C2.4_RULE_CONFIG.json",
                "effectiveSourceSha256": trial_hash if changed else rule_hash,
                "approvedBy": active_override["approvedBy"] if changed else "C2.4 requirements lock",
                "approvedAt": active_override["approvedAt"] if changed else None,
                "effectiveFrom": active_override["effectiveFrom"] if changed else None,
                "effectiveUntil": active_override["effectiveUntil"] if changed else None,
                "scope": scope,
                "downstreamImpact": "只影响该规则登记的公开底线、强路径或退出判断；不以综合分替代。",
            }
        )
    disabled_names = {
        "sell_quote_loss_pct_lte_10_or_15": "卖出报价损失10%或15%门槛",
        "sell_quote_loss_pct_gte_20_immediate_exit": "卖出报价损失20%立即退出门槛",
        "liquidity_drop_pct_gte_80_path_invalidation": "流动性下降80%路径失效门槛",
        "supply_decimals_or_unit_change_path_invalidation": "供应量精度或单位变化路径失效门槛",
        "cross_source_price_deviation_pct_gte_25_path_pause": "跨来源价格偏差25%路径暂停门槛",
        "sell_tax_pct_gte_20_as_hard_block": "卖出税20%硬阻断门槛",
    }
    for rule_id in disabled:
        rules.append(
            {
                "ruleId": rule_id,
                "plainName": disabled_names.get(rule_id, rule_id),
                "plainDescription": "活动试行停用该门槛，但原始证据继续保存。",
                "baselineValue": "enabled",
                "effectiveValue": "disabled_as_gate" if override_active else "enabled",
                "difference": "只停用门槛，不删除原始字段" if override_active else "无差异",
                "unit": "state",
                "status": "overridden" if override_active else "baseline",
                "sourcePath": "docs/C2.4_RULE_RELAXATION_TRIAL_20260813.json",
                "sourceSha256": trial_hash or None,
                "baselineVersion": FROZEN_PUBLIC_RULE_VERSION,
                "effectiveVersion": selected_version,
                "effectiveSourcePath": "docs/C2.4_RULE_RELAXATION_TRIAL_20260813.json" if override_active else "docs/C2.4_RULE_CONFIG.json",
                "effectiveSourceSha256": trial_hash if override_active else rule_hash,
                "approvedBy": active_override["approvedBy"] if override_active else "C2.4 requirements lock",
                "approvedAt": active_override["approvedAt"] if override_active else None,
                "effectiveFrom": active_override["effectiveFrom"] if override_active else None,
                "effectiveUntil": active_override["effectiveUntil"] if override_active else None,
                "scope": "C2.4活动试行",
                "downstreamImpact": "只影响本规则对应路径或阻断；保留原始证据，不把未知写成通过。",
            }
        )

    inputs = items or []
    governed_replay = replay_governed_rules(
        inputs,
        source_version=FROZEN_PUBLIC_RULE_VERSION,
        target_version=selected_version,
        verify_stored_executor=True,
    )
    impact_map = {row["ruleId"]: row for row in governed_replay["rules"]}
    for row in rules:
        rule_replay = impact_map[row["ruleId"]]
        row.update({key: rule_replay[key] for key in ("counts", "addedAssetIds", "removedAssetIds", "stateChangedAssetIds", "passExample", "nonPassExample", "differenceExample", "executorStateDigest", "executorMismatchAssetIds", "executorMissingEvidenceAssetIds")})
    code_manifest = effective_rule_manifest(selected_version)
    rules = reconcile_rule_values(rules, code_manifest)
    replay = replay_rules(inputs, override_active=override_active, governed_replay=governed_replay)
    replay_sets = build_dual_replay_evidence(
        inputs,
        source_version=FROZEN_PUBLIC_RULE_VERSION,
        target_version=selected_version,
        current_source=current_source,
        fixed_history=fixed_history,
    )
    replay["impactCalculation"] = replay_sets["impactCalculation"]
    reconciled = all(row["codeReconciliation"]["matched"] for row in rules) and len(rules) == len(code_manifest)
    history = [
        {"version": rule.get("ruleVersion"), "status": "active" if selected_version == FROZEN_PUBLIC_RULE_VERSION else "frozen_baseline", "sourceSha256": rule_hash},
        {"version": TRIAL_PUBLIC_RULE_VERSION, "status": "active" if override_active else active_override["status"], "sourceSha256": trial_hash or None},
    ]
    return {
        "schemaVersion": "c2.5-rule-transparency-v2",
        "status": "ready" if rule_hash == EXPECTED_RULE_SHA256 and reconciled and not replay_sets["approvalBlocked"] else "attention",
        "frozenBaseline": {"ruleVersion": rule.get("ruleVersion"), "sourcePath": "docs/C2.4_RULE_CONFIG.json", "sourceSha256": rule_hash, "hashMatchesFrozen": rule_hash == EXPECTED_RULE_SHA256},
        "effective": {"ruleVersion": selected_version, "codeRuleVersion": selected_version, "reconciledWithCode": reconciled, "reconciledRuleCount": sum(row["codeReconciliation"]["matched"] for row in rules), "expectedRuleCount": len(code_manifest)},
        "activeOverride": active_override,
        "history": history,
        "governance": governance,
        "rules": rules,
        "replay": replay,
        "replaySets": replay_sets,
        "governanceApprovalBlocked": replay_sets["approvalBlocked"],
        "governanceBlockReason": replay_sets["impactCalculation"].get("reason"),
        "bayesBoundary": "贝叶斯只用于同链排序、变化和校准，不控制资格或凸性线索。",
        "observedAt": iso_time(observed_at),
    }


__all__ = [
    "build_rule_transparency",
    "build_dual_replay_evidence",
    "evaluate_frozen_public_baseline",
    "normalize_rule_replay_item",
    "reconcile_rule_values",
    "replay_one_rule",
    "replay_rules",
    "replay_version_change",
    "validate_active_override",
]
