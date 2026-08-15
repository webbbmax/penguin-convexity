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
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULE_PATH = PROJECT_ROOT / "docs" / "C2.4_RULE_CONFIG.json"
TRIAL_PATH = PROJECT_ROOT / "docs" / "C2.4_RULE_RELAXATION_TRIAL_20260813.json"
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


def replay_rules(items: list[dict[str, Any]], *, override_active: bool) -> dict[str, Any]:
    baseline_passed: list[str] = []
    effective_passed: list[str] = []
    rows: list[dict[str, Any]] = []
    effective_version = TRIAL_PUBLIC_RULE_VERSION if override_active else FROZEN_PUBLIC_RULE_VERSION
    for item in items:
        asset_id = str(item.get("assetId") or item.get("asset_id") or "").strip()
        if not asset_id:
            continue
        baseline = evaluate_public_baseline_version(item, FROZEN_PUBLIC_RULE_VERSION)
        effective = evaluate_public_baseline_version(item, effective_version)
        if baseline["passed"]:
            baseline_passed.append(asset_id)
        if effective["passed"]:
            effective_passed.append(asset_id)
        rows.append(
            {
                "assetId": asset_id,
                "baselinePassed": baseline["passed"],
                "effectivePassed": effective["passed"],
                "changed": baseline["passed"] != effective["passed"],
            }
        )
    baseline_set = set(baseline_passed)
    effective_set = set(effective_passed)
    return {
        "inputCount": len(rows),
        "baselinePassedCount": len(baseline_set),
        "effectivePassedCount": len(effective_set),
        "addedAssetIds": sorted(effective_set - baseline_set),
        "removedAssetIds": sorted(baseline_set - effective_set),
        "unchangedAssetIds": sorted(baseline_set & effective_set),
        "rows": rows,
        "sameInput": True,
        "assetIdSetRecomputed": True,
    }


def _unavailable(reason: str, source_path: str) -> dict[str, str]:
    return {"reason": reason, "sourcePath": source_path}


def replay_one_rule(rule_id: str, items: list[dict[str, Any]], effective_version: str) -> dict[str, Any]:
    rows = []
    for item in items:
        asset_id = str(item.get("assetId") or item.get("asset_id") or "").strip()
        if not asset_id:
            continue
        baseline = evaluate_rule_condition(rule_id, item, FROZEN_PUBLIC_RULE_VERSION)
        effective = evaluate_rule_condition(rule_id, item, effective_version)
        applicable = bool(baseline["applicable"] or effective["applicable"])
        rows.append(
            {
                "assetId": asset_id,
                "applicable": applicable,
                "baselinePassed": baseline["passed"] if applicable else None,
                "effectivePassed": effective["passed"] if applicable else None,
                "changed": applicable and baseline["passed"] != effective["passed"],
            }
        )
    applicable_rows = [row for row in rows if row["applicable"]]
    changed_rows = [row for row in applicable_rows if row["changed"]]
    baseline_passed = [row for row in applicable_rows if row["baselinePassed"]]
    effective_passed = [row for row in applicable_rows if row["effectivePassed"]]
    effective_failed = [row for row in applicable_rows if not row["effectivePassed"]]
    return {
        "counts": {
            "input": len(rows),
            "applicable": len(applicable_rows),
            "notApplicable": len(rows) - len(applicable_rows),
            "baselinePassed": len(baseline_passed),
            "effectivePassed": len(effective_passed),
            "changed": len(changed_rows),
        },
        "addedAssetIds": sorted(row["assetId"] for row in changed_rows if row["effectivePassed"]),
        "removedAssetIds": sorted(row["assetId"] for row in changed_rows if not row["effectivePassed"]),
        "passExample": effective_passed[0]["assetId"] if effective_passed else _unavailable("当前输入没有该规则的真实通过样本", "C2.4 tracking snapshot"),
        "nonPassExample": effective_failed[0]["assetId"] if effective_failed else _unavailable("当前输入没有该规则的真实未通过样本", "C2.4 tracking snapshot"),
        "differenceExample": changed_rows[0]["assetId"] if changed_rows else None,
        "rows": rows,
    }


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
        ("public_sell_quote_loss", "公开卖出报价损失", 15, "quote_success_loss_recorded", "%", "公开底线"),
        ("strong_path_sell_quote_loss", "流动性路径卖出报价损失", 10, "quote_success_no_confirmed_trade_block", "%", "强路径"),
        ("severe_immediate_exit_loss", "严重退出损失", 20, "record_only_no_immediate_exit_gate", "%", "立即退出"),
    ]
    rules: list[dict[str, Any]] = []
    for rule_id, name, baseline_value, trial_value, unit, scope in values:
        changed = override_active
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
                "approvedBy": active_override["approvedBy"] if override_active else "C2.4 requirements lock",
                "approvedAt": active_override["approvedAt"] if override_active else None,
                "effectiveFrom": active_override["effectiveFrom"] if override_active else None,
                "effectiveUntil": active_override["effectiveUntil"] if override_active else None,
                "scope": "C2.4活动试行",
                "downstreamImpact": "只影响本规则对应路径或阻断；保留原始证据，不把未知写成通过。",
            }
        )

    inputs = items or []
    for row in rules:
        rule_replay = replay_one_rule(row["ruleId"], inputs, selected_version)
        row.update({key: rule_replay[key] for key in ("counts", "addedAssetIds", "removedAssetIds", "passExample", "nonPassExample", "differenceExample")})
    code_manifest = effective_rule_manifest(selected_version)
    rules = reconcile_rule_values(rules, code_manifest)
    replay = replay_rules(inputs, override_active=override_active)
    reconciled = all(row["codeReconciliation"]["matched"] for row in rules) and len(rules) == len(code_manifest)
    history = [
        {"version": rule.get("ruleVersion"), "status": "active" if selected_version == FROZEN_PUBLIC_RULE_VERSION else "frozen_baseline", "sourceSha256": rule_hash},
        {"version": TRIAL_PUBLIC_RULE_VERSION, "status": "active" if override_active else active_override["status"], "sourceSha256": trial_hash or None},
    ]
    return {
        "schemaVersion": "c2.5-rule-transparency-v2",
        "status": "ready" if rule_hash == EXPECTED_RULE_SHA256 and reconciled else "attention",
        "frozenBaseline": {"ruleVersion": rule.get("ruleVersion"), "sourcePath": "docs/C2.4_RULE_CONFIG.json", "sourceSha256": rule_hash, "hashMatchesFrozen": rule_hash == EXPECTED_RULE_SHA256},
        "effective": {"ruleVersion": selected_version, "codeRuleVersion": selected_version, "reconciledWithCode": reconciled, "reconciledRuleCount": sum(row["codeReconciliation"]["matched"] for row in rules), "expectedRuleCount": len(code_manifest)},
        "activeOverride": active_override,
        "history": history,
        "governance": governance,
        "rules": rules,
        "replay": replay,
        "bayesBoundary": "贝叶斯只用于同链排序、变化和校准，不控制资格或凸性线索。",
        "observedAt": iso_time(observed_at),
    }


__all__ = [
    "build_rule_transparency",
    "evaluate_frozen_public_baseline",
    "reconcile_rule_values",
    "replay_one_rule",
    "replay_rules",
    "validate_active_override",
]
