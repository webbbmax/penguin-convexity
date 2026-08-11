#!/usr/bin/env python3
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PRESETS_PATH = PROJECT_ROOT / "storage" / "gate-screening-presets-v1.json"
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "gate-screening-state.json"

MATURITIES = {"L0", "L1", "L2", "L3", "L4", "L5"}
RISK_LEVELS = {"low", "medium", "high", "any"}
VALUE_CAPTURE_LEVELS = {"A", "B", "C", "any"}
REMAINING_LEVELS = {"high", "medium", "low", "any"}
ASSET_POLICIES = {"mapped", "any"}
TRADEABILITY_POLICIES = {"verified", "limited_or_verified", "any"}
IDENTITY_POLICIES = {"verified", "allow_pending", "any"}
SELL_PATH_POLICIES = {"verified", "allow_unknown", "any"}
CONTRACT_RISK_POLICIES = {
    "low_or_medium",
    "known_non_blocked",
    "allow_unknown",
    "any",
}
UNKNOWN_POLICIES = {"keep", "exclude"}

RISK_RANK = {"low": 1, "medium": 2, "high": 3}
VALUE_CAPTURE_RANK = {"C": 1, "B": 2, "A": 3}
REMAINING_RANK = {"low": 1, "medium": 2, "high": 3}
REMAINING_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "none": "无",
    "unknown": "待核验",
}
RISK_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "blocked": "阻断",
    "unknown": "待核验",
}
IDENTITY_LABELS = {
    "verified": "已核验",
    "pending": "待核验",
    "conflict": "身份冲突",
    "rejected": "已驳回",
    "unknown": "待核验",
}
TRADEABILITY_LABELS = {
    "verified": "已核验",
    "limited": "受限",
    "blocked": "阻断",
    "unknown": "待核验",
}
TRADEABILITY_POLICY_LABELS = {
    "verified": "仅已核验",
    "limited_or_verified": "已核验或受限",
    "any": "不限制",
}
SELL_PATH_LABELS = {
    "verified": "已核验",
    "blocked": "阻断",
    "unknown": "待核验",
}
SELL_PATH_POLICY_LABELS = {
    "verified": "必须已核验",
    "allow_unknown": "允许待核验",
    "any": "不参与筛选",
}
CONTRACT_RISK_POLICY_LABELS = {
    "low_or_medium": "仅低或中风险",
    "known_non_blocked": "已知且非阻断",
    "allow_unknown": "允许待核验",
    "any": "不参与筛选",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_presets(path=DEFAULT_PRESETS_PATH):
    payload = load_json(path)
    ids = [preset["id"] for preset in payload["presets"]]
    if len(ids) != len(set(ids)):
        raise ValueError("硬门槛预设 ID 重复")
    if payload["defaultPresetId"] not in ids:
        raise ValueError("默认硬门槛预设不存在")
    return payload


def default_state(presets=None):
    presets = presets or load_presets()
    preset = next(
        item
        for item in presets["presets"]
        if item["id"] == presets["defaultPresetId"]
    )
    return {
        "version": presets["version"],
        "activePresetId": preset["id"],
        "showOnlyPassing": True,
        "updatedAt": None,
        "settings": deepcopy(preset["settings"]),
    }


def _number(value, label, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label}必须是数值") from error
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return int(number) if number.is_integer() else number


def validate_state(payload, presets=None):
    presets = presets or load_presets()
    if not isinstance(payload, dict):
        raise ValueError("硬门槛设置格式错误")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("缺少硬门槛设置")

    maturities = settings.get("allowedMaturities")
    if (
        not isinstance(maturities, list)
        or not maturities
        or any(item not in MATURITIES for item in maturities)
    ):
        raise ValueError("至少选择一个有效成熟度")

    normalized = {
        "marketGateEnabled": bool(settings.get("marketGateEnabled")),
        "minimumLiquidityUsd": _number(
            settings.get("minimumLiquidityUsd"), "最低流动性", 0, 1_000_000_000_000
        ),
        "minimumVolume24hUsd": _number(
            settings.get("minimumVolume24hUsd"), "最低24小时成交额", 0, 1_000_000_000_000
        ),
        "maximumExitSlippagePct": _number(
            settings.get("maximumExitSlippagePct"), "最高退出滑点", 0, 100
        ),
        "minimumMismatchScore": _number(
            settings.get("minimumMismatchScore"), "最低错配分", 0, 100
        ),
        "allowedMaturities": [
            item for item in ("L0", "L1", "L2", "L3", "L4", "L5") if item in maturities
        ],
        "maximumRiskLevel": settings.get("maximumRiskLevel"),
        "minimumValueCaptureGrade": settings.get("minimumValueCaptureGrade"),
        "minimumRemainingConvexity": settings.get("minimumRemainingConvexity"),
        "assetPolicy": settings.get("assetPolicy"),
        "tradeabilityPolicy": settings.get("tradeabilityPolicy"),
        "identityPolicy": settings.get("identityPolicy"),
        "sellPathPolicy": settings.get("sellPathPolicy"),
        "contractRiskPolicy": settings.get("contractRiskPolicy"),
        "requireHardTrace": bool(settings.get("requireHardTrace")),
        "requireCompleteConvexity": bool(settings.get("requireCompleteConvexity")),
        "unknownDataPolicy": settings.get("unknownDataPolicy"),
    }
    enum_checks = (
        ("最高风险", normalized["maximumRiskLevel"], RISK_LEVELS),
        ("价值捕获", normalized["minimumValueCaptureGrade"], VALUE_CAPTURE_LEVELS),
        ("剩余凸性", normalized["minimumRemainingConvexity"], REMAINING_LEVELS),
        ("资产要求", normalized["assetPolicy"], ASSET_POLICIES),
        ("交易性要求", normalized["tradeabilityPolicy"], TRADEABILITY_POLICIES),
        ("身份要求", normalized["identityPolicy"], IDENTITY_POLICIES),
        ("卖出路径要求", normalized["sellPathPolicy"], SELL_PATH_POLICIES),
        ("合约风险要求", normalized["contractRiskPolicy"], CONTRACT_RISK_POLICIES),
        ("未知数据处理", normalized["unknownDataPolicy"], UNKNOWN_POLICIES),
    )
    for label, value, allowed in enum_checks:
        if value not in allowed:
            raise ValueError(f"{label}选项无效")

    known_presets = {preset["id"] for preset in presets["presets"]}
    active_preset_id = payload.get("activePresetId", "custom")
    if active_preset_id not in known_presets | {"custom"}:
        active_preset_id = "custom"
    return {
        "version": presets["version"],
        "activePresetId": active_preset_id,
        "showOnlyPassing": bool(payload.get("showOnlyPassing", True)),
        "updatedAt": payload.get("updatedAt") or utc_now(),
        "settings": normalized,
    }


def load_state(state_path=DEFAULT_STATE_PATH, presets_path=DEFAULT_PRESETS_PATH):
    presets = load_presets(presets_path)
    path = Path(state_path)
    if not path.exists():
        return default_state(presets)
    try:
        return validate_state(load_json(path), presets)
    except (OSError, ValueError, json.JSONDecodeError):
        return default_state(presets)


def save_state(payload, state_path=DEFAULT_STATE_PATH, presets_path=DEFAULT_PRESETS_PATH):
    presets = load_presets(presets_path)
    state = validate_state({**payload, "updatedAt": utc_now()}, presets)
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return state


def _gate(key, label, status, detail):
    return {"key": key, "label": label, "status": status, "detail": detail}


def evaluate_case(case, settings):
    gates = []
    hard_blocked = (
        case.get("riskLevel") == "blocked"
        or case.get("state") in {"invalidated", "archived"}
        or case.get("normalizedAction") == "已失去凸性"
    )
    gates.append(
        _gate(
            "hard_invalidation",
            "硬失效",
            "fail" if hard_blocked else "pass",
            "项目已失效、归档或风险阻断" if hard_blocked else "未触发硬失效",
        )
    )

    maturity = case.get("maturity")
    maturity_pass = maturity in settings["allowedMaturities"]
    gates.append(
        _gate(
            "maturity",
            "成熟度",
            "pass" if maturity_pass else "fail",
            f"{maturity or '未知'}；允许 {', '.join(settings['allowedMaturities'])}",
        )
    )

    risk = case.get("riskLevel")
    if risk in (None, "", "unknown"):
        gates.append(_gate("risk", "风险", "pending", "风险等级尚未核验"))
    elif settings["maximumRiskLevel"] == "any":
        gates.append(
            _gate(
                "risk",
                "风险",
                "pass",
                f"当前 {RISK_LABELS.get(risk, risk)}；未限制最高风险",
            )
        )
    else:
        risk_pass = (
            risk in RISK_RANK
            and RISK_RANK[risk] <= RISK_RANK[settings["maximumRiskLevel"]]
        )
        gates.append(
            _gate(
                "risk",
                "风险",
                "pass" if risk_pass else "fail",
                f"当前 {RISK_LABELS.get(risk, risk)}；"
                f"最高允许 {RISK_LABELS[settings['maximumRiskLevel']]}",
            )
        )

    value_capture = case.get("valueCaptureGrade")
    if settings["minimumValueCaptureGrade"] == "any":
        gates.append(
            _gate("value_capture", "价值捕获", "pass", f"当前 {value_capture or '未知'}；未限制")
        )
    elif value_capture not in VALUE_CAPTURE_RANK:
        gates.append(_gate("value_capture", "价值捕获", "pending", "价值捕获尚未核验"))
    else:
        capture_pass = (
            VALUE_CAPTURE_RANK[value_capture]
            >= VALUE_CAPTURE_RANK[settings["minimumValueCaptureGrade"]]
        )
        gates.append(
            _gate(
                "value_capture",
                "价值捕获",
                "pass" if capture_pass else "fail",
                f"当前 {value_capture}；最低 {settings['minimumValueCaptureGrade']}",
            )
        )

    remaining = case.get("remainingConvexity")
    if settings["minimumRemainingConvexity"] == "any":
        gates.append(
            _gate(
                "remaining_convexity",
                "剩余凸性",
                "pass",
                f"当前 {REMAINING_LABELS.get(remaining, '待核验')}；未限制",
            )
        )
    elif remaining not in REMAINING_RANK:
        gates.append(_gate("remaining_convexity", "剩余凸性", "pending", "剩余凸性尚未核验"))
    else:
        remaining_pass = (
            REMAINING_RANK[remaining]
            >= REMAINING_RANK[settings["minimumRemainingConvexity"]]
        )
        gates.append(
            _gate(
                "remaining_convexity",
                "剩余凸性",
                "pass" if remaining_pass else "fail",
                f"当前 {REMAINING_LABELS.get(remaining, remaining)}；"
                f"最低 {REMAINING_LABELS[settings['minimumRemainingConvexity']]}",
            )
        )

    score = case.get("mismatchScore")
    if score is None:
        gates.append(_gate("mismatch_score", "错配分", "pending", "错配分尚未形成"))
    else:
        score_pass = score >= settings["minimumMismatchScore"]
        gates.append(
            _gate(
                "mismatch_score",
                "错配分",
                "pass" if score_pass else "fail",
                f"当前 {score}；最低 {settings['minimumMismatchScore']}",
            )
        )

    asset_mapped = bool(case.get("assetMapped"))
    asset_pass = settings["assetPolicy"] == "any" or asset_mapped
    gates.append(
        _gate(
            "asset_mapping",
            "资产映射",
            "pass" if asset_pass else "fail",
            "已有可跟踪资产"
            if asset_mapped
            else ("允许无资产项目" if settings["assetPolicy"] == "any" else "尚无已核验资产"),
        )
    )

    project_identity = case.get("projectIdentityStatus")
    asset_identity = case.get("assetIdentityStatus")
    identity_verified = project_identity == "verified" and (
        settings["assetPolicy"] == "any" or asset_identity == "verified"
    )
    identity_pending = (
        project_identity in (None, "", "pending")
        or (
            settings["assetPolicy"] != "any"
            and asset_identity in (None, "", "pending")
        )
    )
    if settings["identityPolicy"] == "any":
        identity_status = "pass"
    elif identity_verified:
        identity_status = "pass"
    elif identity_pending:
        identity_status = "pending"
    else:
        identity_status = "fail"
    gates.append(
        _gate(
            "identity",
            "身份核验",
            identity_status,
            f"项目 {IDENTITY_LABELS.get(project_identity, '待核验')}；"
            f"资产 {IDENTITY_LABELS.get(asset_identity, '无')}",
        )
    )

    hard_trace = bool(case.get("hardTracePresent"))
    gates.append(
        _gate(
            "hard_trace",
            "可信硬痕迹",
            "pass"
            if hard_trace or not settings["requireHardTrace"]
            else "fail",
            "已有事实材料"
            if hard_trace
            else (
                "当前方案不要求硬痕迹"
                if not settings["requireHardTrace"]
                else "尚无可信事实材料"
            ),
        )
    )

    complete_convexity = bool(case.get("convexityFieldsComplete"))
    gates.append(
        _gate(
            "convexity_fields",
            "凸性字段",
            "pass"
            if complete_convexity or not settings["requireCompleteConvexity"]
            else "fail",
            "凸性来源、亏损、上行、点火和失效字段完整"
            if complete_convexity
            else (
                "当前方案不要求字段完整"
                if not settings["requireCompleteConvexity"]
                else "凸性核心字段不完整"
            ),
        )
    )

    sell_path = case.get("sellPathStatus") or "unknown"
    if settings["sellPathPolicy"] == "any":
        sell_status = "pass"
    elif sell_path == "verified":
        sell_status = "pass"
    elif sell_path == "blocked":
        sell_status = "fail"
    else:
        sell_status = "pending"
    gates.append(
        _gate(
            "sell_path",
            "卖出路径",
            sell_status,
            f"当前 {SELL_PATH_LABELS.get(sell_path, sell_path)}；"
            f"要求 {SELL_PATH_POLICY_LABELS[settings['sellPathPolicy']]}",
        )
    )

    contract_risk = case.get("contractRisk") or "unknown"
    contract_policy = settings["contractRiskPolicy"]
    if contract_policy == "any":
        contract_status = "pass"
    elif contract_risk == "blocked":
        contract_status = "fail"
    elif contract_risk == "unknown":
        contract_status = "pending"
    elif contract_policy == "low_or_medium":
        contract_status = "pass" if contract_risk in {"low", "medium"} else "fail"
    else:
        contract_status = "pass"
    gates.append(
        _gate(
            "contract_risk",
            "合约风险",
            contract_status,
            f"当前 {RISK_LABELS.get(contract_risk, contract_risk)}；"
            f"要求 {CONTRACT_RISK_POLICY_LABELS[contract_policy]}",
        )
    )

    tradeability = case.get("tradeabilityStatus")
    if settings["tradeabilityPolicy"] == "any":
        tradeability_status = "pass"
    elif tradeability in (None, "", "unknown"):
        tradeability_status = "pending"
    elif settings["tradeabilityPolicy"] == "verified":
        tradeability_status = "pass" if tradeability == "verified" else "fail"
    else:
        tradeability_status = (
            "pass" if tradeability in {"verified", "limited"} else "fail"
        )
    gates.append(
        _gate(
            "tradeability",
            "交易性",
            tradeability_status,
            f"当前 {TRADEABILITY_LABELS.get(tradeability, '待核验')}；"
            f"要求 {TRADEABILITY_POLICY_LABELS[settings['tradeabilityPolicy']]}",
        )
    )

    if settings["marketGateEnabled"]:
        market = case.get("latestMarket") or {}
        market_rules = (
            (
                "liquidity",
                "流动性",
                market.get("liquidityUsd"),
                settings["minimumLiquidityUsd"],
                ">=",
            ),
            (
                "volume_24h",
                "24小时成交",
                market.get("volume24hUsd"),
                settings["minimumVolume24hUsd"],
                ">=",
            ),
            (
                "exit_slippage",
                "退出滑点",
                market.get("estimatedExitSlippagePct"),
                settings["maximumExitSlippagePct"],
                "<=",
            ),
        )
        for key, label, value, threshold, operator in market_rules:
            if value is None:
                gates.append(_gate(key, label, "pending", f"{label}数据缺失"))
                continue
            passed = value >= threshold if operator == ">=" else value <= threshold
            unit = "%" if key == "exit_slippage" else " 美元"
            threshold_text = "不少于" if operator == ">=" else "不高于"
            gates.append(
                _gate(
                    key,
                    label,
                    "pass" if passed else "fail",
                    f"当前 {value:g}{unit}；门槛{threshold_text} {threshold:g}{unit}",
                )
            )

    failed = [gate for gate in gates if gate["status"] == "fail"]
    pending = [gate for gate in gates if gate["status"] == "pending"]
    included = not failed and (
        settings["unknownDataPolicy"] == "keep" or not pending
    )
    status = "fail" if failed else ("pending" if pending else "pass")
    return {
        "status": status,
        "included": included,
        "passedCount": sum(gate["status"] == "pass" for gate in gates),
        "totalCount": len(gates),
        "failedReasons": [gate["detail"] for gate in failed],
        "pendingReasons": [gate["detail"] for gate in pending],
        "gates": gates,
    }


def build_screening_snapshot(cases, state_path=DEFAULT_STATE_PATH, presets_path=DEFAULT_PRESETS_PATH):
    presets = load_presets(presets_path)
    active = load_state(state_path, presets_path)
    for case in cases:
        case["screening"] = evaluate_case(case, active["settings"])
    return {
        "version": presets["version"],
        "defaultPresetId": presets["defaultPresetId"],
        "presets": presets["presets"],
        "active": active,
        "summary": {
            "total": len(cases),
            "included": sum(case["screening"]["included"] for case in cases),
            "passed": sum(case["screening"]["status"] == "pass" for case in cases),
            "pending": sum(case["screening"]["status"] == "pending" for case in cases),
            "excluded": sum(not case["screening"]["included"] for case in cases),
        },
    }
