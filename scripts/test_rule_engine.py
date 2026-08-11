#!/usr/bin/env python3
import json
import tempfile
from pathlib import Path

import run_rule_replay
from rule_engine import calculate_score, evaluate_snapshot, load_rulebook, load_state_machine


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_PATH = PROJECT_ROOT / "fixtures" / "replay-scenarios-v1.json"
RULE_PAGE_PATH = PROJECT_ROOT / "app" / "rules-replay.html"
RULE_SCRIPT_PATH = PROJECT_ROOT / "app" / "rules-replay.js"
STYLE_PATH = PROJECT_ROOT / "app" / "styles.css"
DATA_PAGE_PATH = PROJECT_ROOT / "app" / "data-dictionary.html"


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS {message}")


def case_by_id(snapshot, case_id):
    return next(item for item in snapshot["results"] if item["caseId"] == case_id)


def main():
    rulebook = load_rulebook()
    state_machine = load_state_machine()
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    snapshot = run_rule_replay.build_replay_snapshot(FIXTURES_PATH)

    check(sum(rulebook["scoreWeights"].values()) == 100, "错配五项基础分合计为 100")
    check(len(state_machine["states"]) == 12, "状态机包含 PRD 定义的 12 个工作流状态")
    check(len(fixtures["cases"]) >= 16, "首批回放覆盖至少 16 个正反边界场景")
    check(snapshot["summary"]["failedCount"] == 0, "全部规则回放符合预期")
    check(snapshot["summary"]["allTransitionsLegal"], "全部状态迁移合法")
    check(
        rulebook["tradeability"]["extreme"]["minimum_liquidity_usd"] == 20000,
        "极限交易性最低流动性为 2 万美元",
    )

    social = case_by_id(snapshot, "scenario-social-only")
    check(social["final"]["state"] == "shadow_signal", "社交热度不能替代硬痕迹")

    capture_c = case_by_id(snapshot, "scenario-capture-c")
    check(
        capture_c["final"]["score"]["totalScore"] >= 80
        and capture_c["final"]["state"] == "shadow_signal",
        "高错配分不能绕过 C 级价值捕获门槛",
    )

    extreme = case_by_id(snapshot, "scenario-native-extreme")
    check(
        extreme["final"]["state"] == "extreme_test"
        and "5%" in extreme["final"]["positionGuidance"]
        and "无杠杆" in extreme["final"]["positionGuidance"],
        "极限试仓执行 5% 上限和无杠杆约束",
    )

    at_extreme_floor = {**fixtures["defaults"], "liquidity_usd": 20000, "volume_24h_usd": 25000, "exit_slippage_pct": 5}
    below_extreme_floor = {**at_extreme_floor, "liquidity_usd": 19999}
    check(
        evaluate_snapshot(at_extreme_floor)["tradeability"] == "extreme",
        "达到 2 万美元且其他交易性指标合格时进入极限交易性",
    )
    check(
        evaluate_snapshot(below_extreme_floor)["tradeability"] == "untradeable",
        "低于 2 万美元时仍被交易性门槛拦截",
    )

    blocked = case_by_id(snapshot, "scenario-dangerous-contract")
    check(blocked["final"]["state"] == "invalidated", "阻断级合约风险触发失效")

    priced_in = case_by_id(snapshot, "scenario-priced-in")
    check(priced_in["final"]["state"] == "odds_decay", "价格充分反应触发赔率衰减")

    l5 = case_by_id(snapshot, "scenario-l5-transfer")
    check(l5["final"]["state"] == "transferred_l5", "L5 自动移交反身性管理")

    pending = case_by_id(snapshot, "scenario-tradeability-pending")
    check(pending["final"]["state"] == "tradeability_pending", "未知交易性保持待核验")

    high_risk = case_by_id(snapshot, "scenario-high-score-high-risk")
    check(
        high_risk["final"]["score"]["totalScore"] >= rulebook["scoreThresholds"]["trial_ready"]
        and high_risk["final"]["state"] == "active_embryo",
        "高分但高风险不会自动进入行动级",
    )

    score = calculate_score(fixtures["defaults"])
    check(score["totalScore"] == 58, "评分计算执行基础分减风险扣分")

    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "replay.js"
        run_rule_replay.write_replay_snapshot(snapshot, output)
        check(
            output.exists() and "PENGUIN_CONVEXITY_REPLAY" in output.read_text(encoding="utf-8"),
            "回放快照可以独立生成",
        )

    page_text = RULE_PAGE_PATH.read_text(encoding="utf-8")
    script_text = RULE_SCRIPT_PATH.read_text(encoding="utf-8")
    style_text = STYLE_PATH.read_text(encoding="utf-8")
    data_page_text = DATA_PAGE_PATH.read_text(encoding="utf-8")
    check("案例逐步回放" in page_text and "候选状态机" in page_text, "回放页面包含核心入口")
    check("data-state" in script_text and "data-case-id" in script_text, "页面支持状态与案例点击")
    check(".replay-workspace" in style_text and ".state-machine" in style_text, "回放页面具备桌面布局")
    check("rules-replay.html" in data_page_text, "数据底座页面可以进入规则回放")

    evaluated = evaluate_snapshot(fixtures["defaults"])
    check(evaluated["state"] == "active_embryo", "默认完整候选进入正式胚胎而非行动级")

    print("\n凸性规则与状态回放测试全部通过。")


if __name__ == "__main__":
    main()
