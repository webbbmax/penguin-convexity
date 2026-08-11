#!/usr/bin/env python3
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
SHELL_ROOT = PROJECT_ROOT / "desktop"


def load_snapshot(filename, prefix):
    text = (APP_ROOT / filename).read_text(encoding="utf-8").strip()
    assert text.startswith(prefix) and text.endswith(";")
    return json.loads(text[len(prefix):-1])


def find_route(routes, master_id):
    return next(
        item for item in routes["records"] if item["masterId"] == master_id
    )


def find_case(opportunity, project_id):
    return next(
        item for item in opportunity["cases"]
        if item["projectId"] == project_id
    )


def fields(profile):
    return [
        field
        for section in profile["sections"]
        for field in section["fields"]
    ]


def indexed(items):
    return {item["id"]: item for item in items}


def test_representative_project_routes():
    details = load_snapshot(
        "project-detail-snapshot.js",
        "window.PENGUIN_CONVEXITY_PROJECT_DETAILS = ",
    )
    routes = load_snapshot(
        "research-route-snapshot.js",
        "window.PENGUIN_CONVEXITY_RESEARCH_ROUTES = ",
    )
    if not details["records"]:
        assert routes["counts"]["total"] == 0
        return
    expected = {
        "project:cowl-protocol": ("startup", "foundation_first", "early"),
        "project:jito": ("hybrid", "balanced", "other"),
        "project:uniswap": ("mature", "signals_first", "og"),
    }
    if not set(expected).issubset(details["records"]):
        route_ids = {item["masterId"] for item in routes["records"]}
        assert route_ids == set(details["records"])
        assert routes["counts"]["total"] == details["counts"]["total"]
        for master_id, detail in list(details["records"].items())[:20]:
            assert detail["automaticProfile"]["version"] == "C1.4-05"
            route = find_route(routes, master_id)
            assert route["routeSource"] == "automatic"
        return
    for master_id, route_contract in expected.items():
        assert master_id in details["records"]
        profile = details["records"][master_id]["automaticProfile"]
        route = find_route(routes, master_id)
        assert profile["version"] == "C1.4-05"
        assert (
            route["routeId"],
            route["layoutPriority"],
            route["lifecycleBucket"],
        ) == route_contract
        assert route["foundationProfile"]
        assert route["preSignals"]


def test_action_authority_uses_current_opportunity_result():
    details = load_snapshot(
        "project-detail-snapshot.js",
        "window.PENGUIN_CONVEXITY_PROJECT_DETAILS = ",
    )
    routes = load_snapshot(
        "research-route-snapshot.js",
        "window.PENGUIN_CONVEXITY_RESEARCH_ROUTES = ",
    )
    opportunity = load_snapshot(
        "opportunity-center-snapshot.js",
        "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = ",
    )
    if not details["records"]:
        assert opportunity["counts"]["total"] == 0
        return
    if "project:jito" not in details["records"]:
        route_by_case = {
            item["caseId"]: item for item in routes["records"] if item["caseId"]
        }
        for case in opportunity["cases"][:50]:
            assert case["opportunityStage"]["finalActionLabel"] == "只观察"
            assert route_by_case[case["caseId"]]["currentAction"] == "只观察"
        return
    for project_id in ("jito", "uniswap"):
        master_id = f"project:{project_id}"
        route = find_route(routes, master_id)
        case = find_case(opportunity, project_id)
        legacy_action = details["records"][master_id]["cases"][0][
            "action_stage"
        ]
        current_action = case["opportunityStage"]["finalActionLabel"]
        assert legacy_action == "普通建仓"
        assert current_action == "只观察"
        assert route["currentAction"] == current_action


def test_profile_tasks_have_real_update_entries():
    details = load_snapshot(
        "project-detail-snapshot.js",
        "window.PENGUIN_CONVEXITY_PROJECT_DETAILS = ",
    )
    update_center = load_snapshot(
        "update-center-snapshot.js",
        "window.PENGUIN_CONVEXITY_UPDATE_CENTER = ",
    )
    task_ids = {item["taskId"] for item in update_center["tasks"]}
    if not details["records"]:
        assert update_center["latestRun"] is None
        return
    historical_targets = (
        "project:cowl-protocol",
        "project:jito",
        "project:uniswap",
    )
    targets = (
        historical_targets
        if set(historical_targets).issubset(details["records"])
        else list(details["records"])[:20]
    )
    for master_id in targets:
        profile = details["records"][master_id]["automaticProfile"]
        next_task = profile["nextAutoTask"]
        assert next_task["taskId"] in task_ids
        assert next_task["href"] == (
            f'update-center.html?task={next_task["taskId"]}'
        )
        for item in fields(profile):
            if item["status"] in {"missing", "pending", "conflict"}:
                assert item["nextTaskId"] in task_ids


def test_evidence_routing_and_lifecycle_focus():
    routes = load_snapshot(
        "research-route-snapshot.js",
        "window.PENGUIN_CONVEXITY_RESEARCH_ROUTES = ",
    )
    if not routes["records"]:
        return
    if "project:cowl-protocol" not in {
        item["masterId"] for item in routes["records"]
    }:
        details = load_snapshot(
            "project-detail-snapshot.js",
            "window.PENGUIN_CONVEXITY_PROJECT_DETAILS = ",
        )
        for route in routes["records"][:20]:
            assert route["checklist"]
            assert route["routeSource"] == "automatic"
        pending_project = next(
            detail
            for detail in details["records"].values()
            if (detail.get("project") or {}).get("identity_status") == "pending"
        )
        pending_route = find_route(routes, pending_project["master"]["masterId"])
        pending_website = indexed(pending_route["foundationProfile"])[
            "officialWebsite"
        ]
        assert pending_website["factBoundary"] == "unverified_identity"
        return
    cowl = find_route(routes, "project:cowl-protocol")
    cowl_foundation = indexed(cowl["foundationProfile"])
    assert cowl_foundation["github"]["sourceUrl"] == (
        "https://github.com/Cowl-Protocol/cli"
    )
    assert "官方映射仓库" in cowl_foundation["github"]["evidence"]
    assert cowl_foundation["productDocs"]["sourceUrl"].endswith("/README.md")
    assert "产品文档入口" in cowl_foundation["productDocs"]["evidence"]
    assert cowl_foundation["audit"]["sourceUrl"].endswith("/audits/README.md")

    jito = find_route(routes, "project:jito")
    assert jito["researchFocusId"] == "hybrid"
    assert jito["layoutPriority"] == "balanced"

    uniswap = find_route(routes, "project:uniswap")
    uniswap_signals = indexed(uniswap["preSignals"])
    assert uniswap_signals["governanceProposal"]["status"] == "available"
    assert uniswap_signals["onchainData"]["status"] == "available"


def test_clickable_tasks_and_release_shell():
    html = (APP_ROOT / "project-detail.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "project-detail.js").read_text(encoding="utf-8")
    styles = (APP_ROOT / "styles.css").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    shell = (SHELL_ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="detailContent"' in html
    assert "renderAutomaticProfile" in script
    assert "运行自动补齐" in script
    assert "profile.nextAutoTask?.href" in script
    assert ".automatic-profile-gaps a" in styles
    assert "CONVEXITY WORKSPACE · C1.7" in workbench
    assert 'data-page="workbench.html"' in shell
    assert "C1.7" in shell
    assert "shell.js?v=m1p0" in shell


def main():
    test_representative_project_routes()
    test_action_authority_uses_current_opportunity_result()
    test_profile_tasks_have_real_update_entries()
    test_evidence_routing_and_lifecycle_focus()
    test_clickable_tasks_and_release_shell()
    print("C1.4 功能回归与 C1.6-01 生产外壳测试通过。")


if __name__ == "__main__":
    main()
