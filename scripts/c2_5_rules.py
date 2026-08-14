#!/usr/bin/env python3
"""Read-only C2.5 rule transparency over the frozen C2.4 rule engine."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from c2_4_rules import evaluate_public_baseline


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
    # Frozen document hashes use the canonical Git blob (LF). Windows may
    # materialize the same text as CRLF, so normalize that one representation.
    payload = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest()


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


def _quote_loss(item: dict[str, Any]) -> float | None:
    try:
        return float(item.get("sellQuoteLossPct"))
    except (TypeError, ValueError):
        return None


def evaluate_frozen_public_baseline(item: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the frozen public gates without changing production logic."""

    effective = evaluate_public_baseline(item)
    risk_state = item.get("riskSourceState") or item.get("riskState")
    risk_source_success = risk_state in {"success", "complete", "completed"}
    loss = _quote_loss(item)
    quote_threshold_passed = item.get("sellQuoteState") == "success" and loss is not None and loss <= 15
    severe = any(
        bool(item.get(key))
        for key in (
            "confirmedHardBlock",
            "confirmedFreeze",
            "confirmedBlacklist",
            "confirmedSellBlock",
            "confirmedSevereAnomaly",
        )
    )
    extra_checks = [
        {
            "code": "risk_source_success",
            "passed": risk_source_success,
            "reason": "风险来源成功返回。" if risk_source_success else "风险来源没有成功返回，冻结基线不能把未知当安全。",
        },
        {
            "code": "sell_quote_loss_lte_15",
            "passed": quote_threshold_passed,
            "reason": "100美元标准卖出报价损失不高于15%。" if quote_threshold_passed else "卖出报价损失未知或高于15%。",
        },
        {
            "code": "no_severe_anomaly",
            "passed": not severe,
            "reason": "没有已确认严重异常。" if not severe else "存在已确认严重异常。",
        },
    ]
    checks = [*effective.get("checks", []), *extra_checks]
    passed = all(bool(row.get("passed")) for row in checks)
    return {
        "passed": passed,
        "publicEligible": passed,
        "checks": checks,
        "ruleVersion": "c2.4-rules-v1-frozen-baseline-replay",
    }


def replay_rules(items: list[dict[str, Any]], *, override_active: bool) -> dict[str, Any]:
    baseline_passed: list[str] = []
    effective_passed: list[str] = []
    rows: list[dict[str, Any]] = []
    for item in items:
        asset_id = str(item.get("assetId") or item.get("asset_id") or "").strip()
        if not asset_id:
            continue
        baseline = evaluate_frozen_public_baseline(item)
        effective = evaluate_public_baseline(item) if override_active else baseline
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


def build_rule_transparency(
    items: list[dict[str, Any]] | None = None,
    *,
    rule_path: Path = RULE_PATH,
    trial_path: Path = TRIAL_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or utc_now()
    try:
        rule = load_json(rule_path)
        rule_hash = sha256(rule_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {
            "schemaVersion": "c2.5-rule-transparency-v1",
            "status": "blocked",
            "unavailable": _unavailable(str(error), str(rule_path)),
            "observedAt": iso_time(observed_at),
        }
    try:
        trial = load_json(trial_path)
        trial_hash = sha256(trial_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        trial = {}
        trial_hash = ""
        trial_error = str(error)
    else:
        trial_error = ""
    override = validate_active_override(
        trial,
        trial_sha256=trial_hash,
        rule_sha256=rule_hash,
        now=observed_at,
    )
    if trial_error:
        override["reasons"].append(trial_error)
        override["active"] = False
        override["status"] = "rejected"

    disabled = list((trial.get("activeOverrides") or {}).get("disabledAsGates") or [])
    values = [
        ("public_sell_quote_loss", "公开卖出报价损失", 15, "报价成功即可；损失比例只记录", "%", override["active"]),
        ("strong_path_sell_quote_loss", "流动性路径卖出报价损失", 10, "报价成功即可形成路径，但仍需无已确认交易阻断", "%", override["active"]),
        ("severe_immediate_exit_loss", "严重退出损失", 20, "仅记录，不在活动试行中单独形成停止门槛", "%", override["active"]),
    ]
    rules: list[dict[str, Any]] = []
    for rule_id, name, baseline_value, active_value, unit, changed in values:
        effective_value: Any = active_value if changed else baseline_value
        rules.append(
            {
                "ruleId": rule_id,
                "plainName": name,
                "plainDescription": "冻结基线与当前活动试行分开显示；这不是新的C2.5规则。",
                "baselineValue": baseline_value,
                "effectiveValue": effective_value,
                "difference": "活动试行覆盖" if changed else "无差异",
                "unit": unit,
                "status": "overridden" if changed else "baseline",
                "sourcePath": "docs/C2.4_RULE_CONFIG.json",
                "sourceSha256": rule_hash,
                "approvedBy": override["approvedBy"] if changed else "C2.4 requirements lock",
                "approvedAt": override["approvedAt"] if changed else None,
                "effectiveFrom": override["effectiveFrom"] if changed else None,
                "effectiveUntil": override["effectiveUntil"] if changed else None,
                "scope": "C2.4公开底线与强路径",
                "counts": None,
                "passExample": None,
                "nonPassExample": None,
                "downstreamImpact": "只影响公开底线或路径重放；不改变第一关、贝叶斯或行动语言。",
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
                "effectiveValue": "disabled_as_gate" if override["active"] else "enabled",
                "difference": "只停用门槛，不删除原始字段" if override["active"] else "无差异",
                "unit": "state",
                "status": "overridden" if override["active"] else "baseline",
                "sourcePath": "docs/C2.4_RULE_RELAXATION_TRIAL_20260813.json",
                "sourceSha256": trial_hash or None,
                "approvedBy": override["approvedBy"],
                "approvedAt": override["approvedAt"],
                "effectiveFrom": override["effectiveFrom"],
                "effectiveUntil": override["effectiveUntil"],
                "scope": "C2.4活动试行",
                "counts": None,
                "passExample": None,
                "nonPassExample": None,
                "downstreamImpact": "保留原始证据；不把空值或未知写成通过。",
            }
        )

    replay = replay_rules(items or [], override_active=override["active"])
    for row in rules:
        row["counts"] = {
            "input": replay["inputCount"],
            "baselinePassed": replay["baselinePassedCount"],
            "effectivePassed": replay["effectivePassedCount"],
            "changed": len(replay["addedAssetIds"]) + len(replay["removedAssetIds"]),
        }
        changed_rows = [item for item in replay["rows"] if item["changed"]]
        passed_rows = [item for item in replay["rows"] if item["effectivePassed"]]
        failed_rows = [item for item in replay["rows"] if not item["effectivePassed"]]
        row["passExample"] = passed_rows[0]["assetId"] if passed_rows else _unavailable("当前输入没有真实通过样本", "C2.4 tracking snapshot")
        row["nonPassExample"] = failed_rows[0]["assetId"] if failed_rows else _unavailable("当前输入没有真实未通过样本", "C2.4 tracking snapshot")
        if changed_rows:
            row["differenceExample"] = changed_rows[0]["assetId"]

    return {
        "schemaVersion": "c2.5-rule-transparency-v1",
        "status": "ready" if rule_hash == EXPECTED_RULE_SHA256 and override["active"] else "attention",
        "frozenBaseline": {
            "ruleVersion": rule.get("ruleVersion"),
            "sourcePath": "docs/C2.4_RULE_CONFIG.json",
            "sourceSha256": rule_hash,
            "hashMatchesFrozen": rule_hash == EXPECTED_RULE_SHA256,
        },
        "effective": {
            "ruleVersion": "c2.4-public-baseline-quote-success-trial-v1" if override["active"] else rule.get("ruleVersion"),
            "codeRuleVersion": "c2.4-public-baseline-quote-success-trial-v1",
            "reconciledWithCode": override["active"],
        },
        "activeOverride": override,
        "history": [
            {"version": rule.get("ruleVersion"), "status": "frozen_baseline", "sourceSha256": rule_hash},
            {"version": "c2.4-public-baseline-quote-success-trial-v1", "status": override["status"], "sourceSha256": trial_hash or None},
        ],
        "rules": rules,
        "replay": replay,
        "bayesBoundary": "贝叶斯只用于同链排序、变化和校准，不控制资格或凸性线索。",
        "observedAt": iso_time(observed_at),
    }


__all__ = [
    "build_rule_transparency",
    "evaluate_frozen_public_baseline",
    "replay_rules",
    "validate_active_override",
]
