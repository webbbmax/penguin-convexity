#!/usr/bin/env python3
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULEBOOK_PATH = PROJECT_ROOT / "storage" / "rulebook-v1.json"
STATE_MACHINE_PATH = PROJECT_ROOT / "storage" / "state-machine-v1.json"


GATE_LABELS = {
    "identity_verified": "项目主体已核验",
    "asset_verified": "资产与合约已核验",
    "official_relation_verified": "官方关系已核验",
    "venue_verified": "真实交易场所已核验",
    "sell_path_verified": "卖出路径已核验",
    "hard_trace_present": "至少一个可信硬痕迹",
    "independent_diffusion_present": "至少一个独立扩散信号",
    "value_capture_a_or_b": "价值捕获达到 A 或 B",
    "convexity_fields_complete": "凸性必填字段完整",
    "contract_risk_known_and_not_blocked": "合约风险已知且非阻断",
    "tradeability_verified": "交易性达到标准或极限门槛",
}

ACTIVE_STATES = {
    "active_embryo",
    "priority_watch",
    "extreme_test",
    "trial_ready",
    "igniting",
    "odds_decay",
}

TRADEABILITY_LABELS = {
    "standard": "标准交易性",
    "extreme": "极限交易性",
    "untradeable": "不可交易",
    "unknown": "待核验",
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_rulebook():
    return load_json(RULEBOOK_PATH)


def load_state_machine():
    return load_json(STATE_MACHINE_PATH)


def calculate_score(data):
    components = {
        "fact_certainty": int(data["fact_certainty"]),
        "economic_increment": int(data["economic_increment"]),
        "value_capture": int(data["value_capture"]),
        "event_proximity": int(data["event_proximity"]),
        "price_unreacted": int(data["price_unreacted"]),
    }
    base_score = sum(components.values())
    risk_deduction = int(data["risk_deduction"])
    return {
        "components": components,
        "baseScore": base_score,
        "riskDeduction": risk_deduction,
        "totalScore": max(0, min(100, base_score - risk_deduction)),
    }


def classify_tradeability(data, rulebook):
    if data["contract_risk"] == "blocked" or not data["sell_path_verified"]:
        return "untradeable"

    required_values = (
        data["liquidity_usd"],
        data["volume_24h_usd"],
        data["exit_slippage_pct"],
    )
    if data["contract_risk"] == "unknown" or any(value is None for value in required_values):
        return "unknown"

    standard = rulebook["tradeability"]["standard"]
    if (
        data["liquidity_usd"] >= standard["minimum_liquidity_usd"]
        and data["volume_24h_usd"] >= standard["minimum_volume_24h_usd"]
        and data["exit_slippage_pct"] <= standard["maximum_exit_slippage_pct"]
    ):
        return "standard"

    extreme = rulebook["tradeability"]["extreme"]
    if (
        data["liquidity_usd"] >= extreme["minimum_liquidity_usd"]
        and data["volume_24h_usd"] >= extreme["minimum_volume_24h_usd"]
        and data["exit_slippage_pct"] <= extreme["maximum_exit_slippage_pct"]
    ):
        return "extreme"

    return "untradeable"


def build_gates(data, tradeability):
    raw_results = {
        "identity_verified": data["identity_verified"],
        "asset_verified": data["asset_verified"],
        "official_relation_verified": data["official_relation_verified"],
        "venue_verified": data["venue_verified"],
        "sell_path_verified": data["sell_path_verified"],
        "hard_trace_present": data["hard_trace_count"] >= 1,
        "independent_diffusion_present": data["independent_diffusion_count"] >= 1,
        "value_capture_a_or_b": data["value_capture_grade"] in ("A", "B"),
        "convexity_fields_complete": data["convexity_fields_complete"],
        "contract_risk_known_and_not_blocked": (
            None if data["contract_risk"] == "unknown" else data["contract_risk"] != "blocked"
        ),
        "tradeability_verified": (
            None if tradeability == "unknown" else tradeability in ("standard", "extreme")
        ),
    }
    return [
        {
            "key": key,
            "label": GATE_LABELS[key],
            "status": "pending" if raw_results[key] is None else ("pass" if raw_results[key] else "fail"),
        }
        for key in GATE_LABELS
    ]


def state_reason(state, data, score, tradeability):
    reasons = {
        "shadow_signal": "至少一个正式入库硬门槛未通过，只保留影子信号。",
        "identity_pending": "项目、资产或官方关系仍未完成核验。",
        "tradeability_pending": "流动性、滑点、卖出路径或合约权限仍有未知项。",
        "active_embryo": "通过正式库硬门槛，但尚未达到重点或行动条件。",
        "priority_watch": "点火临近且研究优先级达到重点复核阈值。",
        "extreme_test": "满足极限胚胎门槛，仅允许现货、无杠杆和目标仓位 5% 以内。",
        "trial_ready": "L2-L4 的证据、价值捕获、风险与标准交易性已显著降低不确定性。",
        "igniting": "点火条件正在转化为真实订单、资金或价格变化。",
        "odds_decay": "价格已经明显反应，且事实未同步升级或剩余凸性偏低。",
        "invalidated": "核心事实、安全或交易性出现硬失效。",
        "transferred_l5": "事实进入 L5 反身性阶段，移交趋势管理。",
        "archived": "连续复核无有效进展，保留历史后退出当前排序。",
    }
    detail = reasons[state]
    if state in ACTIVE_STATES:
        detail += (
            f" 当前错配分 {score['totalScore']}，"
            f"交易性为 {TRADEABILITY_LABELS[tradeability]}。"
        )
    return detail


def action_for_state(state):
    return {
        "shadow_signal": "只记录",
        "identity_pending": "只记录",
        "tradeability_pending": "只观察",
        "active_embryo": "只观察",
        "priority_watch": "等待点火",
        "extreme_test": "极限试仓",
        "trial_ready": "可试仓",
        "igniting": "提高至目标仓位阶段",
        "odds_decay": "赔率衰减，停止新增",
        "invalidated": "已失效",
        "transferred_l5": "转入L5趋势管理",
        "archived": "只记录",
    }[state]


def position_guidance(state, maturity, rulebook):
    if state == "extreme_test":
        return "现货、无杠杆，不超过目标仓位 5%"
    if state in ("trial_ready", "igniting"):
        return rulebook["positionGuidance"][maturity]
    if state == "transferred_l5":
        return rulebook["positionGuidance"]["L5"]
    return "不进入行动级仓位"


def decide_state(data, score, tradeability, previous_state, rulebook):
    if data["archive_requested"]:
        return "archived"
    if data["maturity"] == "L5":
        return "transferred_l5"
    if data["core_invalidated"] or data["contract_risk"] == "blocked":
        return "invalidated"
    if not (
        data["identity_verified"]
        and data["asset_verified"]
        and data["official_relation_verified"]
    ):
        return "identity_pending"
    if tradeability == "unknown":
        return "tradeability_pending"
    if previous_state in ACTIVE_STATES and (
        not data["sell_path_verified"] or tradeability == "untradeable"
    ):
        return "invalidated"

    hard_gate_failed = (
        not data["venue_verified"]
        or not data["sell_path_verified"]
        or data["hard_trace_count"] < 1
        or data["independent_diffusion_count"] < 1
        or data["value_capture_grade"] not in ("A", "B")
        or not data["convexity_fields_complete"]
        or tradeability == "untradeable"
    )
    if hard_gate_failed:
        return "shadow_signal"

    decay = rulebook["decayRules"]
    if (
        data["price_reaction_pct"] >= decay["minimum_price_reaction_pct"]
        and not data["facts_upgraded"]
        and data["remaining_convexity"] in decay["remaining_convexity_levels"]
    ):
        return "odds_decay"
    if data["ignition_active"]:
        return "igniting"

    thresholds = rulebook["scoreThresholds"]
    acceptable_risk = data["risk_level"] in ("low", "medium")
    if (
        tradeability == "extreme"
        and data["maturity"] in ("L0", "L1", "L2")
        and score["totalScore"] >= thresholds["extreme_test"]
        and acceptable_risk
        and data["remaining_convexity"] == "high"
    ):
        return "extreme_test"
    if (
        tradeability == "standard"
        and data["maturity"] in ("L2", "L3", "L4")
        and score["totalScore"] >= thresholds["trial_ready"]
        and acceptable_risk
        and data["remaining_convexity"] in ("high", "medium")
    ):
        return "trial_ready"
    if (
        data["ignition_proximity"] in ("near", "immediate")
        and score["totalScore"] >= thresholds["priority_watch"]
        and acceptable_risk
    ):
        return "priority_watch"
    return "active_embryo"


def evaluate_snapshot(data, previous_state=None, rulebook=None):
    rulebook = rulebook or load_rulebook()
    score = calculate_score(data)
    tradeability = classify_tradeability(data, rulebook)
    state = decide_state(data, score, tradeability, previous_state, rulebook)
    return {
        "maturity": data["maturity"],
        "state": state,
        "tradeability": tradeability,
        "riskLevel": data["risk_level"],
        "remainingConvexity": data["remaining_convexity"],
        "ignitionProximity": data["ignition_proximity"],
        "valueCaptureGrade": data["value_capture_grade"],
        "primaryConvexitySource": data["primary_convexity_source"],
        "action": action_for_state(state),
        "positionGuidance": position_guidance(state, data["maturity"], rulebook),
        "score": score,
        "gates": build_gates(data, tradeability),
        "reason": state_reason(state, data, score, tradeability),
        "maximumControllableLoss": data["maximum_controllable_loss"],
        "nonlinearUpsidePath": data["nonlinear_upside_path"],
        "ignitionConditions": data["ignition_conditions"],
        "oddsDecayConditions": data["odds_decay_conditions"],
        "invalidationWindow": data["invalidation_window"],
    }


def transition_is_legal(from_state, to_state, state_machine=None):
    if from_state is None or from_state == to_state:
        return True
    state_machine = state_machine or load_state_machine()
    return to_state in state_machine["allowedTransitions"].get(from_state, [])
