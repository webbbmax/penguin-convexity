#!/usr/bin/env python3
from collections import Counter


ACTION_LABELS = {
    "ordinary": "普通建仓",
    "extreme": "极限试仓",
    "observe": "只观察",
    "reflexive": "反身性管理",
    "reject": "排除",
}

ACTION_PRIORITY = {
    "ordinary": 1,
    "extreme": 2,
    "observe": 3,
    "reflexive": 4,
    "reject": 5,
}
RULE_VERSION = "four-layer-c1.1.1"

RISK_RANK = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}
PASSING_REMAINING = {"high", "medium_high", "medium"}


def _check(key, label, status, detail):
    return {"key": key, "label": label, "status": status, "detail": detail}


def _status(checks):
    if any(item["status"] == "fail" for item in checks):
        return "fail"
    if any(item["status"] == "pending" for item in checks):
        return "pending"
    return "pass"


def _layer(layer_id, label, purpose, checks):
    status = _status(checks)
    failed = [item["detail"] for item in checks if item["status"] == "fail"]
    pending = [item["detail"] for item in checks if item["status"] == "pending"]
    return {
        "id": layer_id,
        "label": label,
        "purpose": purpose,
        "status": status,
        "checks": checks,
        "failedReasons": failed,
        "pendingReasons": pending,
    }


def _identity_check(key, label, value):
    if value == "verified":
        return _check(key, label, "pass", f"{label}已核验")
    if value in {"conflict", "rejected"}:
        return _check(key, label, "fail", f"{label}冲突或已驳回")
    return _check(key, label, "pending", f"{label}仍待核验")


def evaluate_identity_and_tradeability(data):
    checks = [
        _identity_check("project_identity", "项目身份", data.get("projectIdentity")),
        _identity_check("asset_identity", "资产身份", data.get("assetIdentity")),
        _identity_check("official_relation", "官方关系", data.get("officialRelation")),
    ]

    contract_risk = data.get("contractRisk", "unknown")
    if data.get("securityBlocked") or contract_risk == "blocked":
        checks.append(_check("contract_risk", "合约与安全", "fail", "存在阻断级合约或安全风险"))
    elif contract_risk == "unknown":
        checks.append(_check("contract_risk", "合约与安全", "pending", "合约风险仍待核验"))
    else:
        checks.append(_check("contract_risk", "合约与安全", "pass", f"合约风险已知且非阻断：{contract_risk}"))

    sell_path = data.get("sellPath", "unknown")
    if sell_path == "verified":
        checks.append(_check("sell_path", "卖出路径", "pass", "只读卖出路径已核验"))
    elif sell_path == "blocked":
        checks.append(_check("sell_path", "卖出路径", "fail", "卖出路径被阻断"))
    else:
        checks.append(_check("sell_path", "卖出路径", "pending", "卖出路径仍待核验"))

    tradeability = data.get("tradeability", "unknown")
    if tradeability in {"standard", "extreme"}:
        label = "标准交易性" if tradeability == "standard" else "极限交易性"
        checks.append(_check("tradeability", "交易性", "pass", f"达到{label}"))
    elif tradeability == "untradeable":
        checks.append(_check("tradeability", "交易性", "fail", "当前不可形成可执行买卖"))
    else:
        checks.append(_check("tradeability", "交易性", "pending", "流动性、成交或滑点仍不完整"))

    return _layer(
        "identity_tradeability",
        "第一层：身份与交易性",
        "确认买的是什么、在哪里交易、能否退出，以及合约风险是否可解释。",
        checks,
    )


def evaluate_hard_evidence(data):
    checks = []
    hard_trace = bool(data.get("hardTrace"))
    checks.append(
        _check(
            "hard_trace",
            "可信硬痕迹",
            "pass" if hard_trace else "fail",
            "已有代码、链上、治理、产品或正式文件"
            if hard_trace
            else "没有可验证硬痕迹",
        )
    )

    evidence_grade = data.get("evidenceGrade", "none")
    if evidence_grade == "verified":
        checks.append(_check("evidence_grade", "证据质量", "pass", "至少一条事实证据得到独立或执行层确认"))
    elif evidence_grade == "conditional":
        checks.append(_check("evidence_grade", "证据质量", "pending", "已有一手痕迹，但仍缺外部验证或执行结果"))
    else:
        checks.append(_check("evidence_grade", "证据质量", "fail", "只有项目方表述、社交热度或空白证据"))

    economic_increment = data.get("economicIncrement", "unknown")
    if economic_increment == "verified":
        checks.append(_check("economic_increment", "经济增量", "pass", "事件或产品能够形成新增使用、收入或供给变化"))
    elif economic_increment == "none":
        checks.append(_check("economic_increment", "经济增量", "fail", "事件不创造可辨认的经济增量"))
    else:
        checks.append(_check("economic_increment", "经济增量", "pending", "经济增量尚未得到足够证据"))

    source = str(data.get("primaryConvexity") or "").strip()
    checks.append(
        _check(
            "convexity_source",
            "主凸性来源",
            "pass" if source else "fail",
            f"主来源：{source}" if source else "没有明确主凸性来源",
        )
    )
    return _layer(
        "hard_evidence",
        "第二层：硬事实",
        "区分事实、项目方陈述和市场热度，确认是否存在真实经济增量。",
        checks,
    )


def evaluate_convexity_structure(data):
    checks = []
    value_capture = data.get("valueCapture", "unknown")
    if value_capture in {"A", "B"}:
        checks.append(_check("value_capture", "资产价值捕获", "pass", f"价值捕获为 {value_capture}"))
    elif value_capture == "C":
        checks.append(_check("value_capture", "资产价值捕获", "fail", "项目可能成立，但指定资产缺少清晰承接"))
    else:
        checks.append(_check("value_capture", "资产价值捕获", "pending", "价值捕获关系尚未核验"))

    loss_bound = data.get("lossBound", "unknown")
    if loss_bound in {"bounded", "bounded_zero"}:
        detail = "最大亏损可通过仓位封顶并按归零处理" if loss_bound == "bounded_zero" else "最大亏损边界已定义"
        checks.append(_check("loss_bound", "可控亏损", "pass", detail))
    elif loss_bound == "unbounded":
        checks.append(_check("loss_bound", "可控亏损", "fail", "最大亏损无法通过仓位或结构封顶"))
    else:
        checks.append(_check("loss_bound", "可控亏损", "pending", "最大可控亏损尚未定义"))

    checks.append(
        _check(
            "nonlinear_upside",
            "非线性上行",
            "pass" if data.get("nonlinearUpside") else "fail",
            "存在可解释的1到10路径" if data.get("nonlinearUpside") else "没有非线性上行传导路径",
        )
    )
    checks.append(
        _check(
            "ignition",
            "点火条件",
            "pass" if data.get("ignitionDefined") else "pending",
            "点火条件已定义" if data.get("ignitionDefined") else "点火条件仍待定义",
        )
    )
    checks.append(
        _check(
            "invalidation",
            "失效条件",
            "pass" if data.get("invalidationDefined") else "pending",
            "失效窗口已定义" if data.get("invalidationDefined") else "失效窗口仍待定义",
        )
    )

    remaining = data.get("remainingConvexity", "unknown")
    if remaining in PASSING_REMAINING:
        checks.append(_check("remaining", "剩余凸性", "pass", f"剩余凸性：{remaining}"))
    elif remaining in {"low", "none"}:
        checks.append(_check("remaining", "剩余凸性", "fail", f"剩余凸性已经{remaining}"))
    else:
        checks.append(_check("remaining", "剩余凸性", "pending", "剩余凸性尚未核验"))

    if data.get("securityUnresolved"):
        checks.append(_check("security_unresolved", "安全缺口", "pending", "仍有安全、权限或运行稳定性缺口"))

    return _layer(
        "convexity_structure",
        "第三层：凸性结构",
        "验证可控亏损、非线性上行、资产承接、点火、衰减和失效是否形成闭环。",
        checks,
    )


def decide_action(data, layers):
    maturity = data.get("maturity", "L0")
    risk = data.get("risk", "unknown")
    remaining = data.get("remainingConvexity", "unknown")
    price_reaction = data.get("priceReaction", "low")
    score = data.get("mismatchScore")
    layer_one, layer_two, layer_three = layers

    if (
        data.get("coreInvalidated")
        or data.get("securityBlocked")
        or risk == "blocked"
        or data.get("economicIncrement") == "none"
    ):
        return "reject", "核心事实、安全边界或经济增量已经失效。", "排除并保留失效记录"

    if maturity == "L5" or (price_reaction == "full" and remaining in {"low", "none"}):
        return "reflexive", "事实与价格已进入L5或赔率充分反应，移交反身性管理。", "不按早期凸性新增仓位"

    if price_reaction == "full":
        return "observe", "价格已经充分反应，与仍有高剩余凸性的标签相互冲突。", "等待回撤或重新核验剩余赔率"

    ordinary_ready = (
        maturity in {"L2", "L3", "L4"}
        and risk in {"R1", "R2", "R3"}
        and layer_one["status"] == "pass"
        and layer_two["status"] == "pass"
        and layer_three["status"] == "pass"
        and data.get("tradeability") == "standard"
        and score is not None
        and score >= 60
    )
    if ordinary_ready:
        if price_reaction == "partial":
            return "ordinary", "基本闭环成立，但价格已部分反应，等待回撤或新增采用确认。", "回撤后普通小仓"
        return "ordinary", "L2-L4证据与交易性已降险，且仍保留未充分定价的上行。", "分阶段建立普通仓位"

    extreme_ready = (
        maturity in {"L0", "L1", "L2"}
        and risk in RISK_RANK
        and RISK_RANK[risk] <= RISK_RANK["R5"]
        and layer_one["status"] == "pass"
        and bool(data.get("hardTrace"))
        and data.get("evidenceGrade") in {"verified", "conditional"}
        and data.get("economicIncrement") == "verified"
        and data.get("valueCapture") in {"A", "B"}
        and data.get("lossBound") == "bounded_zero"
        and bool(data.get("nonlinearUpside"))
        and bool(data.get("ignitionDefined"))
        and bool(data.get("invalidationDefined"))
        and remaining in {"high", "medium_high"}
        and score is not None
        and score >= 35
        and not data.get("securityUnresolved")
    )
    if extreme_ready:
        return "extreme", "早期非线性成立且亏损可按归零封顶，只允许极限试仓。", "现货、无杠杆，不超过目标仓位5%"

    reasons = []
    for layer in layers:
        reasons.extend(layer["failedReasons"])
        reasons.extend(layer["pendingReasons"])
    return "observe", reasons[0] if reasons else "尚未达到普通建仓或极限试仓条件。", "继续补证，不进入行动级仓位"


def evaluate_case(data):
    layers = [
        evaluate_identity_and_tradeability(data),
        evaluate_hard_evidence(data),
        evaluate_convexity_structure(data),
    ]
    action_category, reason, position = decide_action(data, layers)
    action_label = ACTION_LABELS[action_category]
    fourth = {
        "id": "action_classification",
        "label": "第四层：行动分层",
        "purpose": "把通过情况转换为普通建仓、极限试仓、只观察、反身性管理或排除。",
        "status": "pass",
        "checks": [
            _check("action", "最终动作", "pass", action_label),
            _check("position", "仓位边界", "pass", position),
        ],
        "failedReasons": [],
        "pendingReasons": [],
    }
    layers.append(fourth)

    stopped_layer = 4
    for index, layer in enumerate(layers[:3], start=1):
        if layer["status"] != "pass":
            stopped_layer = index
            break

    return {
        **data,
        "actionCategory": action_category,
        "actionLabel": action_label,
        "actionReason": reason,
        "positionBoundary": position,
        "stoppedLayer": stopped_layer,
        "stoppedLayerLabel": layers[stopped_layer - 1]["label"],
        "layers": layers,
    }


def summarize(results):
    actions = Counter(item["actionCategory"] for item in results)
    holds = Counter(item["stoppedLayer"] for item in results)
    layer_statuses = {}
    for index in range(4):
        layer_statuses[str(index + 1)] = dict(
            Counter(item["layers"][index]["status"] for item in results)
        )
    return {
        "total": len(results),
        "actionCounts": dict(actions),
        "holdCounts": {str(key): value for key, value in holds.items()},
        "layerStatusCounts": layer_statuses,
    }


def sort_results(results):
    return sorted(
        results,
        key=lambda item: (
            ACTION_PRIORITY[item["actionCategory"]],
            -(item.get("mismatchScore") or -1),
            item.get("project", ""),
        ),
    )
