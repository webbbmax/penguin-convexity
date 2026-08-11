#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from build_update_center_snapshot import rebuild_update_snapshots
from init_db import initialize_database
from refresh_candidate_pool import persist_refresh
from serve_local import (
    begin_update_status,
    fail_update_status,
    finish_update_status,
    get_update_status,
)
from sync_thread_candidates import import_candidates, load_fixture
from update_tasks import TASK_DEFINITIONS


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def sample_market_result():
    return {
        "caseId": "thread-cowl-20260728",
        "provider": "dexscreener",
        "status": "success",
        "sourceUrl": "https://dexscreener.com/example",
        "observedAt": "2026-07-29T00:00:00Z",
        "priceUsd": 0.00005,
        "liquidityUsd": 21000,
        "volume24hUsd": 30000,
        "marketCapUsd": 50000,
        "fdvUsd": 50000,
        "circulatingSupply": None,
        "priceChange24hPct": 2,
        "exitNotionalUsd": 100,
        "estimatedExitSlippagePct": 0.95,
        "definitionNote": "更新中心自动测试快照",
        "venue": {
            "name": "test-dex",
            "pairSymbol": "COWL/WETH",
            "poolAddress": "0xtest",
        },
        "raw": {},
    }


def test_snapshots_and_task_attribution():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        initialize_database(
            db_path,
            root / "runtime-snapshot.js",
            backup=False,
        )
        fixture = load_fixture()
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            import_candidates(connection, fixture)
            result = persist_refresh(
                connection,
                fixture,
                [sample_market_result()],
                [],
                "update-center-test-run",
                task_id="market_refresh",
                duration_ms=321,
            )
            connection.commit()
        finally:
            connection.close()

        assert result["status"] == "success"
        assert result["taskId"] == "market_refresh"
        update, sources = rebuild_update_snapshots(
            db_path=db_path,
            update_path=root / "update-center-snapshot.js",
            source_path=root / "source-registry-snapshot.js",
        )
        assert update["counts"]["tasks"] == len(TASK_DEFINITIONS) - 1
        assert any(
            task["taskId"] == "cactus_discovery_continue"
            for task in update["tasks"]
        )
        assert any(
            task["taskId"] == "machine_research_scoring_refresh"
            for task in update["tasks"]
        )
        assert any(
            task["taskId"] == "machine_conclusion_refresh"
            for task in update["tasks"]
        )
        assert update["runs"][0]["taskId"] == "market_refresh"
        assert update["runs"][0]["duration_ms"] == 321
        assert update["changes"]
        assert all(
            item["taskId"] == "market_refresh"
            for item in update["changes"]
        )
        market_source = next(
            item
            for item in sources["sources"]
            if item["source_id"] == "market-dexscreener"
        )
        assert market_source["primaryTaskId"] == "market_refresh"
        assert market_source["proves"]
        assert market_source["doesNotProve"]
        assert market_source["recordCount"] >= 1


def test_static_entrypoints():
    update_html = (APP_ROOT / "update-center.html").read_text(encoding="utf-8")
    update_script = (APP_ROOT / "update-center.js").read_text(encoding="utf-8")
    source_html = (APP_ROOT / "source-registry.html").read_text(encoding="utf-8")
    source_script = (APP_ROOT / "source-registry.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    local_server = (PROJECT_ROOT / "scripts" / "serve_local.py").read_text(
        encoding="utf-8"
    )
    launcher = (
        PROJECT_ROOT / "scripts" / "launch-convexity.ps1"
    ).read_text(encoding="utf-8")

    assert 'id="runFullUpdate"' in update_html
    assert 'data-update-task="full_refresh"' in update_html
    assert 'id="updateActivity"' in update_html
    assert 'id="updateWatchdog"' in update_html
    assert 'id="watchdogRecoveryAction"' in update_html
    assert "convexity-update-scroll-y" in update_script
    assert "update-status" in update_script
    assert "effectiveActivityStatus" in update_script
    assert "statusTime >= latestTime" in update_script
    assert "正在更新：" in update_script
    assert "renderWatchdog" in update_script
    assert "recoveryTaskId" in update_script
    assert "等待当前任务" in update_script
    assert "target.scrollIntoView" not in update_script
    assert "const isHttpUrl" in update_script
    assert "isHttpUrl(item.sourceUrl)" in update_script
    assert 'id="changePreviousPage"' in update_html
    assert 'id="changeNextPage"' in update_html
    assert "const changePageSize = 80" in update_script
    assert "pageRecords = records.slice" in update_script
    assert "只重试这类任务" in update_script
    assert '"continue"' in update_script
    assert "逐条变化" in update_html
    assert "项目跟踪执行结果" in update_html
    assert "machine_research_scoring_refresh" in TASK_DEFINITIONS
    assert "machine_conclusion_refresh" in TASK_DEFINITIONS
    assert "applyLiveUpdateGuard" in (
        APP_ROOT / "workbench.js"
    ).read_text(encoding="utf-8")
    assert "只重试这个项目" in update_script
    assert "trackingTaskId" in update_script
    assert "这个来源能证明什么" in source_script
    assert "不能证明什么" in source_script
    assert "update-center.html" in workbench
    assert "source-registry.html" in workbench
    assert "high-value-sources.html" in workbench
    assert "source-discovery.html" in workbench
    assert '["update-center.html", "更新中心"]' in navigation
    assert '["source-registry.html", "信源状态"]' in navigation
    assert "/api/update-task" in local_server
    assert "/api/update-status" in local_server
    assert "tracking_task_id" in local_server
    assert 'CONVEXITY_RELEASE = "C1.7"' in local_server
    assert "initialize_update_recovery" in local_server
    assert '"taskIds": sorted(TASK_DEFINITIONS)' in local_server
    assert '$health.convexityRelease -eq "C1.7"' in launcher
    assert '$health.migrationRelease -eq "M1.0"' in launcher
    assert "凸性信源库" in source_html


def test_running_status_survives_page_navigation():
    with tempfile.TemporaryDirectory() as temporary:
        status_path = Path(temporary) / "update-runtime-status.json"
        running = begin_update_status(
            "market_refresh",
            status_path=status_path,
        )
        assert running["state"] == "running"
        assert running["active"] is True
        assert running["taskLabel"] == "行情与流动性"
        assert running["watchdog"]["state"] == "monitoring"
        assert get_update_status()["runToken"] == running["runToken"]

        completed = finish_update_status(
            {
                "status": "success",
                "taskId": "market_refresh",
                "taskLabel": "行情与流动性",
                "runId": "status-test-run",
                "message": "测试任务已完成。",
            },
            status_path=status_path,
        )
        assert completed["active"] is False
        assert completed["state"] == "success"
        assert completed["runId"] == "status-test-run"
        assert completed["finishedAt"]
        assert completed["watchdog"]["state"] == "healthy"

        begin_update_status(
            "evidence_refresh",
            status_path=status_path,
        )
        failed = fail_update_status(
            "测试失败",
            status_path=status_path,
        )
        assert failed["active"] is False
        assert failed["state"] == "failed"
        assert failed["message"] == "测试失败"
        assert failed["recoveryAvailable"] is True


def main():
    test_snapshots_and_task_attribution()
    print("PASS 单项更新运行、逐条变化和信源证据边界已写入快照")
    test_static_entrypoints()
    print("PASS 更新中心与凸性信源库已接入工作台和桌面软件")
    test_running_status_survives_page_navigation()
    print("PASS 更新运行状态跨页面保留，完成与失败状态可读取")


if __name__ == "__main__":
    main()
