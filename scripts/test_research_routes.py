#!/usr/bin/env python3
from pathlib import Path

from build_research_route_snapshot import (
    DEFAULT_DETAIL_PATH,
    DEFAULT_MANUAL_PATH,
    DEFAULT_MASTER_PATH,
    DEFAULT_OPPORTUNITY_PATH,
    ROUTES,
    automatic_route,
    build_route_record,
    build_snapshot,
)


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
ALLOWED_ROUTES = {"startup", "mature", "hybrid"}


def record(**updates):
    value = {
        "masterId": "project:test",
        "projectId": "test",
        "caseId": "case:test",
        "name": "Test",
        "symbol": "TEST",
        "recordType": "project",
        "maturityLevel": "L1",
        "marketCapUsd": 10_000_000,
        "identityStatus": "verified",
        "contractAddress": "0x1111111111111111111111111111111111111111",
        "networkName": "Base",
        "liquidityUsd": 100_000,
        "lifecycleBucket": "early",
        "lifecycleLabel": "早期项目",
        "lifecycleDate": "2026-07-01",
        "lifecycleDateStatus": "verified",
        "lifecycleAgeLabel": "不足1个月",
        "lifecycleReason": "公开启动未满6个月。",
    }
    value.update(updates)
    return value


def test_automatic_routes():
    assert automatic_route(record())["routeId"] == "startup"
    assert automatic_route(
        record(
            lifecycleBucket="og",
            lifecycleLabel="OG项目",
            lifecycleDate="2014-01-10",
            lifecycleAgeLabel="12年6个月",
        )
    )["routeId"] == "mature"
    assert automatic_route(
        record(
            lifecycleBucket="other",
            lifecycleLabel="潜力项目",
            lifecycleDate="2024-01-01",
            lifecycleAgeLabel="2年6个月",
        )
    )["routeId"] == "hybrid"
    assert automatic_route(
        record(
            recordType="discovery",
            projectId="",
            caseId="",
            maturityLevel="",
            marketCapUsd=None,
            lifecycleBucket="early",
            lifecycleDateStatus="provisional",
        )
    )["routeId"] == "startup"


def test_manual_override():
    manual = {
        "manualReview": {
            "annotationId": "annotation-test",
            "updatedAt": "2026-07-29T00:00:00Z",
            "values": {
                "researchRouteOverride": "mature",
                "researchRouteReason": "治理提案已经成为主要研究入口。",
            },
        }
    }
    result = build_route_record(record(), {}, {}, manual)
    assert result["routeId"] == "startup"
    assert result["routeSource"] == "automatic"
    assert result["researchFocusId"] == "mature"
    assert result["researchFocusSource"] == "manual_override"
    assert result["researchFocusReason"] == "治理提案已经成为主要研究入口。"
    assert result["currentAction"] == "只观察"


def test_live_snapshot():
    snapshot = build_snapshot(
        DEFAULT_MASTER_PATH,
        DEFAULT_DETAIL_PATH,
        DEFAULT_MANUAL_PATH,
        DEFAULT_OPPORTUNITY_PATH,
    )
    assert snapshot["version"] == "C1.2-05"
    assert {item["id"] for item in ROUTES} == ALLOWED_ROUTES
    assert snapshot["counts"]["total"] == len(snapshot["records"])
    assert sum(snapshot["counts"][key] for key in ALLOWED_ROUTES) == len(
        snapshot["records"]
    )
    assert len({item["masterId"] for item in snapshot["records"]}) == len(
        snapshot["records"]
    )
    assert all(item["routeId"] in ALLOWED_ROUTES for item in snapshot["records"])
    assert all(item["routeReason"] for item in snapshot["records"])
    assert all(item["primaryFocus"] for item in snapshot["records"])
    assert all(item["totalChecks"] == len(item["checklist"]) for item in snapshot["records"])
    assert all(item["currentAction"] for item in snapshot["records"])
    assert all(len(item["foundationProfile"]) == 9 for item in snapshot["records"])
    assert all(len(item["preSignals"]) == 8 for item in snapshot["records"])
    assert all(
        item["layoutPriority"]
        == {
            "startup": "foundation_first",
            "mature": "signals_first",
            "hybrid": "balanced",
        }[item["researchFocusId"]]
        for item in snapshot["records"]
    )
    assert all(
        not field["sourceUrl"]
        or field["sourceUrl"].startswith(("http://", "https://"))
        for item in snapshot["records"]
        for field in item["foundationProfile"] + item["preSignals"]
    )
    by_master = {item["masterId"]: item for item in snapshot["records"]}
    assert all(
        not item["caseId"].startswith("thread-")
        for item in snapshot["records"]
        if item["caseId"]
    )
    case_records = [item for item in snapshot["records"] if item["caseId"]]
    assert len(case_records) == snapshot["counts"]["caseRecords"]
    assert (
        snapshot["counts"]["caseRecords"]
        + snapshot["counts"]["discoveryRecords"]
        == snapshot["counts"]["total"]
    )


def test_page_contract():
    consumers = (
        ("candidate-pool.html", "candidate-pool.js"),
        ("project-detail.html", "project-detail.js"),
        ("manual-review.html", "manual-review.js"),
        ("project-master-pool.html", "project-master-pool.js"),
        ("screening-console.html", "screening-console.js"),
    )
    for html_name, script_name in consumers:
        html = (APP_ROOT / html_name).read_text(encoding="utf-8")
        script = (APP_ROOT / script_name).read_text(encoding="utf-8")
        assert "research-route-snapshot.js" in html
        assert "routeLabel" in script
        assert "routeReason" in script
    manual_html = (APP_ROOT / "manual-review.html").read_text(encoding="utf-8")
    manual_script = (APP_ROOT / "manual-review.js").read_text(encoding="utf-8")
    assert 'id="reviewRouteFilter"' in manual_html
    assert 'name="researchRouteOverride"' in manual_script
    assert "人工调整研究重点的原因" in manual_script
    assert 'id="opportunityRouteFilter"' in (
        APP_ROOT / "candidate-pool.html"
    ).read_text(encoding="utf-8")
    assert 'id="masterRouteFilter"' in (
        APP_ROOT / "project-master-pool.html"
    ).read_text(encoding="utf-8")
    desktop_server = (ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
    startup_source = desktop_server[
        desktop_server.index("def rebuild_startup_snapshots():"):
        desktop_server.index("def main():")
    ]
    assert "rebuild_research_route_snapshot()" not in startup_source
    assert "C21_STARTUP_SNAPSHOTS" in startup_source


def main():
    test_automatic_routes()
    test_manual_override()
    test_live_snapshot()
    test_page_contract()
    print("C1.2-05 lifecycle category and research priority checks passed")


if __name__ == "__main__":
    main()
