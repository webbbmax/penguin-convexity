#!/usr/bin/env python3
from datetime import datetime, timezone
from pathlib import Path

from build_tracking_tasks_snapshot import (
    DEFAULT_OPPORTUNITY_PATH,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_ROUTE_PATH,
    OUTPUT_PREFIX,
    build_snapshot,
    write_snapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def test_live_tasks():
    fixed_now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    snapshot = build_snapshot(
        DEFAULT_OPPORTUNITY_PATH,
        DEFAULT_ROUTE_PATH,
        now=fixed_now,
    )
    tasks = snapshot["tasks"]
    assert snapshot["version"] == "C1.5-05"
    assert snapshot["counts"]["total"] == len(tasks)
    assert snapshot["counts"]["activeTracking"] == sum(
        item["currentAction"] == "只观察" for item in tasks
    )
    assert len({item["taskId"] for item in tasks}) == len(tasks)
    assert len({item["caseId"] for item in tasks}) == len(tasks)
    assert all(item["projectCategory"] in {"startup", "mature", "hybrid"} for item in tasks)
    assert all(item["priority"] in {"P0", "P1", "P2", "P3"} for item in tasks)
    assert all(item["status"] in {"due", "open", "monitoring", "closed"} for item in tasks)
    assert all(len(item["checklist"]) == 3 for item in tasks)
    assert all(item["nextStep"] for item in tasks)
    assert all(item["nextReviewAt"] for item in tasks)
    assert all(item["upgradeCondition"] for item in tasks)
    assert all(item["stopCondition"] for item in tasks)
    assert all(item["suggestedSources"] for item in tasks)
    assert all(
        item["status"] in {"due", "open"}
        for item in tasks
        if item["currentAction"] == "只观察"
    )
    assert all(not item["caseId"].startswith("thread-") for item in tasks)
    repeated = build_snapshot(
        DEFAULT_OPPORTUNITY_PATH,
        DEFAULT_ROUTE_PATH,
        now=fixed_now,
    )
    assert repeated["tasks"] == tasks


def test_snapshot_file():
    snapshot = build_snapshot(DEFAULT_OPPORTUNITY_PATH, DEFAULT_ROUTE_PATH)
    write_snapshot(snapshot, DEFAULT_OUTPUT_PATH)
    text = DEFAULT_OUTPUT_PATH.read_text(encoding="utf-8").strip()
    assert text.startswith(OUTPUT_PREFIX)
    assert text.endswith(";")
    assert '"version":"C1.5-05"' in text
    assert (
        '"taskVersion":"C1.5-05"' in text
        or '"tasks":[]' in text
    )


def test_page_contract():
    opportunity_html = (APP_ROOT / "candidate-pool.html").read_text(encoding="utf-8")
    opportunity_script = (APP_ROOT / "candidate-pool.js").read_text(encoding="utf-8")
    detail_html = (APP_ROOT / "project-detail.html").read_text(encoding="utf-8")
    detail_script = (APP_ROOT / "project-detail.js").read_text(encoding="utf-8")
    assert "tracking-task-snapshot.js" in opportunity_html
    assert "tracking-task-snapshot.js" in detail_html
    for item_id in (
        "trackingTasks",
        "trackingTaskBoard",
        "trackingRouteFilter",
        "trackingPriorityFilter",
        "trackingStatusFilter",
        "actionBlockers",
    ):
        assert f'id="{item_id}"' in opportunity_html
    assert opportunity_html.index('id="actionBlockers"') < opportunity_html.index(
        'id="recentChanges"'
    )
    assert opportunity_html.index('id="recentChanges"') < opportunity_html.index(
        'id="catalystPaths"'
    )
    assert opportunity_html.index('id="catalystPaths"') < opportunity_html.index(
        "LIFECYCLE CATEGORIES"
    )
    assert opportunity_html.index("LIFECYCLE CATEGORIES") < opportunity_html.index(
        'id="trackingTasks"'
    )
    assert "renderTrackingTasks" in opportunity_script
    assert "trackingByCase" in opportunity_script
    assert "下一步跟踪" in opportunity_script
    assert 'id="detailTrackingTask"' in detail_script
    assert "renderTrackingTask" in detail_script
    assert "升级条件" in detail_script
    assert "停止条件" in detail_script
    assert "本轮发现的依据" in detail_script
    assert "查看原始来源" in detail_script
    assert "detail-tracking-source-results" in detail_script
    assert "最近跟踪" in opportunity_script
    assert "为什么还不能行动" in opportunity_html


def test_pipeline_contract():
    for relative_path in (
        "scripts/refresh_candidate_pool.py",
        "scripts/serve_local.py",
        "scripts/manage_manual_review.py",
    ):
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "rebuild_tracking_tasks_snapshot" in text
    desktop_server = (PROJECT_ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
    startup_source = desktop_server[
        desktop_server.index("def rebuild_startup_snapshots():"):
        desktop_server.index("def main():")
    ]
    assert "rebuild_tracking_tasks_snapshot()" not in startup_source
    assert "C21_STARTUP_SNAPSHOTS" in startup_source


def main():
    test_live_tasks()
    test_snapshot_file()
    test_page_contract()
    test_pipeline_contract()
    print("C1.5-05 自动跟踪任务、页面顺序与更新接入测试通过。")


if __name__ == "__main__":
    main()
