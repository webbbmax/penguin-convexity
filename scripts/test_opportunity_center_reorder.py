#!/usr/bin/env python3
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
sys.path.insert(0, str(ROOT / "scripts"))

from build_opportunity_center_snapshot import (  # noqa: E402
    CONCLUSION_GROUPS,
    DEFAULT_CANDIDATE_PATH,
    DEFAULT_FOUR_LAYER_PATH,
    FINAL_ACTIONS,
    STAGES,
    build_snapshot,
    classify_case,
)


def base_case(active=True, qualified=False, actionable=False, tier="research"):
    return {
        "publicSignal": {
            "active": active,
            "qualified": qualified,
            "actionable": actionable,
            "tier": tier,
            "exitReasons": [],
        },
        "screening": {"failedReasons": [], "pendingReasons": []},
        "state": "priority_watch",
        "oddsDecayConditions": "",
    }


def test_stage_rules():
    machine_case = {
        **base_case(),
        "normalizedAction": "只观察",
        "machineConclusion": {
            "opportunity_stage": "observe",
            "action_category": "observe",
            "action_label": "只观察",
            "conclusion_state": "asset_pending",
            "conclusion_state_label": "可购买资产待核验",
            "headline": "只观察：尚未确认项目自身可购买资产",
            "why_not_actionable": "尚未确认项目自身可购买资产。",
            "next_step": "自动核验项目资产。",
            "next_task_id": "machine_asset_identity_refresh",
            "upgradeConditions": ["项目自身可购买资产达到 verified"],
            "invalidationConditions": ["项目主体身份冲突"],
        },
    }
    machine = classify_case(machine_case, {"actionCategory": "ordinary"})
    assert machine["stage"] == "observe"
    assert machine["finalActionCategory"] == "observe"
    assert machine["conclusionSource"] == "machine_conclusion"
    assert machine["nextTaskId"] == "machine_asset_identity_refresh"

    model_pending = classify_case(base_case(), None)
    assert model_pending["stage"] == "model_pending"
    assert model_pending["finalActionCategory"] == "observe"
    ordinary = classify_case(
        base_case(actionable=True, qualified=True),
        {"actionCategory": "ordinary"},
    )
    assert ordinary["stage"] == "actionable"
    assert ordinary["finalActionCategory"] == "ordinary"
    blocked_extreme = classify_case(
        base_case(),
        {"actionCategory": "extreme"},
    )
    assert blocked_extreme["stage"] == "action_pending"
    assert blocked_extreme["finalActionCategory"] == "observe"
    qualified_observe = classify_case(
        base_case(qualified=True),
        {"actionCategory": "observe"},
    )
    assert qualified_observe["stage"] == "qualified_pending"
    assert qualified_observe["finalActionCategory"] == "observe"
    assert classify_case(
        {**base_case(), "state": "odds_decay"},
        {"actionCategory": "observe"},
    )["stage"] == "decay"
    reflexive = classify_case(
        base_case(),
        {"actionCategory": "reflexive"},
    )
    assert reflexive["stage"] == "reflexive"
    assert reflexive["finalActionCategory"] == "reflexive"
    rejected = classify_case(
        base_case(),
        {"actionCategory": "reject"},
    )
    assert rejected["stage"] == "invalidated"
    assert rejected["finalActionCategory"] == "invalidated"


def test_live_snapshot():
    snapshot = build_snapshot(DEFAULT_CANDIDATE_PATH, DEFAULT_FOUR_LAYER_PATH)
    assert snapshot["version"] == "C1.5-05"
    assert snapshot["counts"]["total"] == len(snapshot["cases"])
    assert sum(snapshot["stageCounts"].values()) == snapshot["counts"]["total"]
    assert sum(snapshot["actionCounts"].values()) == snapshot["counts"]["total"]
    assert [stage["id"] for stage in STAGES] == snapshot["publicRanking"]["stageOrder"]
    assert [
        action["id"] for action in FINAL_ACTIONS
    ] == snapshot["publicRanking"]["finalActionOrder"]
    assert snapshot["consistency"]["checked"] == snapshot["counts"]["total"]
    assert snapshot["consistency"]["conflicts"] == []
    assert all("opportunityStage" in item for item in snapshot["cases"])
    assert all(
        item["opportunityStage"]["modelActionCategory"] != "pending"
        for item in snapshot["cases"]
    )
    orders = [item["opportunityStage"]["stageOrder"] for item in snapshot["cases"]]
    assert orders == sorted(orders)
    assert all(
        item["opportunityStage"]["stage"] != "actionable"
        for item in snapshot["cases"]
        if not item["publicSignal"]["actionable"]
    )
    allowed_actions = {item["id"] for item in FINAL_ACTIONS}
    assert all(
        item["opportunityStage"]["finalActionCategory"] in allowed_actions
        for item in snapshot["cases"]
    )
    assert all(
        item["publicSignal"]["actionable"]
        for item in snapshot["cases"]
        if item["opportunityStage"]["finalActionCategory"] in {"ordinary", "extreme"}
    )
    conclusion = snapshot["conclusionBoard"]
    assert [item["id"] for item in conclusion["groups"]] == [
        item["id"] for item in CONCLUSION_GROUPS
    ]
    assert sum(item["count"] for item in conclusion["groups"]) == snapshot["counts"]["total"]
    grouped_actions = [
        action_id
        for group in conclusion["groups"]
        for action_id in group["actionIds"]
    ]
    assert sorted(grouped_actions) == sorted(item["id"] for item in FINAL_ACTIONS)
    assert len(grouped_actions) == len(set(grouped_actions))
    assert sum(item["count"] for item in conclusion["blockers"]) == snapshot["actionCounts"]["observe"]
    if snapshot["counts"]["actionable"] == 0:
        assert conclusion["headline"] == "本期没有满足完整行动门槛的项目"


def test_page_contract():
    html = (APP_ROOT / "candidate-pool.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "candidate-pool.js").read_text(encoding="utf-8")
    assert "opportunity-center-snapshot.js" in html
    assert "change-explanations-snapshot.js" in html
    assert 'id="opportunityStageBoard"' in html
    assert 'id="opportunityBlockerBoard"' in html
    assert 'id="opportunityChangeFeed"' in html
    assert 'id="opportunityCatalystBoard"' in html
    assert 'id="opportunityResetFilters"' in html
    ordered_sections = [
        'id="currentConclusions"',
        'id="actionBlockers"',
        'id="recentChanges"',
        'id="catalystPaths"',
        'id="projectCategories"',
        'id="trackingTasks"',
        'id="opportunityDirectory"',
    ]
    positions = [html.index(section) for section in ordered_sections]
    assert positions == sorted(positions)
    assert html.index('id="opportunityDirectory"') < html.index("CONVEXITY MAP")
    assert "潜力项目" in html
    assert "其他项目" not in html
    for field_id in (
        "opportunityStageFilter",
        "opportunityRiskFilter",
        "opportunityRemainingFilter",
        "opportunityIgnitionFilter",
        "opportunityTradeabilityFilter",
        "opportunitySort",
    ):
        assert f'id="{field_id}"' in html
    assert "opportunityStage.finalActionOrder" in script
    assert "finalActionCategory" in script
    assert "conclusionGroupById" in script
    assert "renderBlockerBoard" in script
    assert "renderCatalystPathBoard" in script
    assert 'group.id === "transferred"' in script
    assert ".slice(" not in script


def main():
    test_stage_rules()
    test_live_snapshot()
    test_page_contract()
    print("C1.5-05 opportunity center conclusion checks passed")


if __name__ == "__main__":
    main()
