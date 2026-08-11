#!/usr/bin/env python3
import shutil
import sqlite3
import tempfile
from pathlib import Path

from build_catalyst_trade_path_snapshot import (
    build_catalyst_trade_path_snapshot,
)
from catalyst_trade_paths import (
    MODELED_EXIT_NOTIONAL_USD,
    latest_paths,
    modeled_slippage,
    persist_catalyst_trade_paths,
)
from init_db import DEFAULT_DB_PATH, initialize_database


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def test_rules_and_persistence():
    assert modeled_slippage(1_000_000, 20_000) == 4.0
    assert MODELED_EXIT_NOTIONAL_USD == 20_000

    with tempfile.TemporaryDirectory() as temporary_dir:
        db_path = Path(temporary_dir) / "convexity.db"
        snapshot_path = Path(temporary_dir) / "runtime.js"
        shutil.copy2(DEFAULT_DB_PATH, db_path)
        initialize_database(
            db_path,
            snapshot_path,
            backup=False,
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            first = persist_catalyst_trade_paths(
                connection,
                "test-catalyst-path-run-1",
                "2026-07-31T05:30:00Z",
            )
            connection.commit()
            second = persist_catalyst_trade_paths(
                connection,
                "test-catalyst-path-run-2",
                "2026-07-31T05:31:00Z",
            )
            connection.commit()
            paths = latest_paths(connection)
            case_total = connection.execute(
                "SELECT COUNT(*) FROM candidate_cases"
            ).fetchone()[0]
            published_total = connection.execute(
                """
                SELECT COUNT(*)
                FROM catalyst_trade_paths
                WHERE publication_status = 'published'
                """
            ).fetchone()[0]
            duplicate_current = connection.execute(
                """
                SELECT COUNT(*)
                FROM (
                  SELECT case_id
                  FROM catalyst_trade_paths
                  WHERE publication_status = 'published'
                  GROUP BY case_id
                  HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            schema_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            governance_paths = [
                item
                for item in paths.values()
                if item["catalyst_type"] == "governance"
            ]
            snapshot = build_catalyst_trade_path_snapshot(connection)
        finally:
            connection.close()

    assert first["projectsProcessed"] == case_total
    assert second["projectsProcessed"] == case_total
    assert published_total == case_total
    assert duplicate_current == 0
    assert schema_version == 17
    assert all(
        "active" in item["catalyst_summary"].lower()
        for item in governance_paths
    ), "已经关闭的治理提案不能冒充当前催化"
    assert all(
        item["modeled_exit_notional_usd"] == 20_000
        for item in paths.values()
    )
    assert all(
        item["modeled_exit_method"].startswith("按最深单池流动性")
        for item in paths.values()
    )
    assert snapshot["counts"]["total"] == case_total
    assert "不代表真实成交" in snapshot["boundary"]


def test_ui_and_update_integration():
    html = (APP_ROOT / "catalyst-paths.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "catalyst-paths.js").read_text(encoding="utf-8")
    detail = (APP_ROOT / "project-detail.js").read_text(encoding="utf-8")
    opportunity = (APP_ROOT / "candidate-pool.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    update_tasks = (
        PROJECT_ROOT / "scripts" / "update_tasks.py"
    ).read_text(encoding="utf-8")
    refresh = (
        PROJECT_ROOT / "scripts" / "refresh_candidate_pool.py"
    ).read_text(encoding="utf-8")
    engine = (
        PROJECT_ROOT / "scripts" / "catalyst_trade_paths.py"
    ).read_text(encoding="utf-8")

    assert "实际只读核验" in script and "2万美元理论估算" in script
    assert "运行这项更新" in script
    assert "renderCatalystTradePath" in detail
    assert 'id="detailCatalystPath"' in detail
    assert "opportunity-catalyst-path" in opportunity
    assert 'href="catalyst-paths.html"' in workbench
    assert '["catalyst-paths.html", "催化路径"]' in navigation
    assert '"catalyst_trade_path_refresh"' in update_tasks
    assert '"catalyst_trade_path"' in refresh
    assert "rwa" not in engine.lower()


def main():
    test_rules_and_persistence()
    test_ui_and_update_integration()
    print("C1.6-03 催化、资产、价值传导、退出、失效与机器任务闭环测试通过。")


if __name__ == "__main__":
    main()
