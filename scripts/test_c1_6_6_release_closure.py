#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
PROJECT_ROOT = ROOT.parent


def load_js_payload(path, prefix):
    text = path.read_text(encoding="utf-8").strip()
    assert text.startswith(prefix)
    assert text.endswith(";")
    return json.loads(text[len(prefix):-1])


def test_frozen_page_order():
    html = (APP_ROOT / "candidate-pool.html").read_text(encoding="utf-8")
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
    for anchor in (
        "#currentConclusions",
        "#actionBlockers",
        "#recentChanges",
        "#catalystPaths",
        "#projectCategories",
        "#trackingTasks",
        "#opportunityDirectory",
    ):
        assert f'href="{anchor}"' in html
    assert "结论方法</a>" not in html


def test_project_trace_and_transfer_contract():
    script = (APP_ROOT / "candidate-pool.js").read_text(encoding="utf-8")
    for marker in (
        "renderCatalystPathBoard",
        'group.id === "transferred"',
        "反身性管理",
        "失效排除",
        "查看证据、变化与升级条件",
        "证据来源",
        "最近变化",
        "下一步任务",
        "升级条件",
        "失效条件",
        "const catalystPath = item.catalystTradePath;",
    ):
        assert marker in script


def test_live_result_consistency():
    state = load_js_payload(
        APP_ROOT / "opportunity-center-snapshot.js",
        "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = ",
    )
    cases = state["cases"]
    assert state["counts"]["total"] == len(cases)
    assert sum(state["actionCounts"].values()) == len(cases)
    transferred = next(
        group for group in state["conclusionBoard"]["groups"]
        if group["id"] == "transferred"
    )
    assert transferred["count"] == (
        state["actionCounts"]["reflexive"]
        + state["actionCounts"]["invalidated"]
    )
    assert len(transferred["caseIds"]) == transferred["count"]
    assert all(item.get("detailUrl") for item in cases)
    assert all(item.get("opportunityStage") for item in cases)
    assert all(
        item["opportunityStage"].get("invalidationConditions")
        for item in cases
    )


def test_release_and_product_boundary():
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    shell = (ROOT / "desktop" / "index.html").read_text(
        encoding="utf-8"
    )
    server = (
        ROOT / "scripts" / "serve_local.py"
    ).read_text(encoding="utf-8")
    assert "C1.7" in workbench
    assert "C1.7" in navigation
    assert "C1.7" in shell
    assert 'CONVEXITY_RELEASE = "C1.7"' in server
    assert "RWA" not in shell
    assert "RWA" not in (
        APP_ROOT / "candidate-pool.html"
    ).read_text(encoding="utf-8")


def main():
    test_frozen_page_order()
    test_project_trace_and_transfer_contract()
    test_live_result_consistency()
    test_release_and_product_boundary()
    print("C1.6-06 冻结顺序、转出拆分、项目溯源与发布边界测试通过。")


if __name__ == "__main__":
    main()
