#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import run_update_task as update_runner
from build_update_center_snapshot import build_update_center_snapshot
from init_db import initialize_database
from run_update_task import update_retry_status
from serve_local import (
    begin_update_status,
    finish_update_status,
    get_update_status,
    initialize_update_recovery,
)
from update_watchdog import (
    default_update_status,
    load_update_status,
    recover_interrupted_updates,
    save_update_status,
)


def test_interrupted_status_becomes_retryable_run():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        snapshot_path = root / "runtime-snapshot.js"
        status_path = root / "update-runtime-status.json"
        initialize_database(
            db_path=db_path,
            snapshot_path=snapshot_path,
            backup=False,
        )
        saved = default_update_status()
        saved.update(
            state="running",
            active=True,
            taskId="market_refresh",
            taskLabel="行情与流动性",
            runToken="interrupted-token",
            startedAt="2026-07-30T00:00:00Z",
            message="任务正在后台运行。",
        )
        save_update_status(saved, status_path)

        recovery = recover_interrupted_updates(
            db_path=db_path,
            status_path=status_path,
        )
        assert len(recovery["recoveredRuns"]) == 1
        status = load_update_status(status_path)
        assert status["state"] == "failed"
        assert status["active"] is False
        assert status["recoveryAvailable"] is True
        assert status["recoveryTaskId"] == "market_refresh"
        assert status["recoveryRunId"] == recovery["recoveredRuns"][0]

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            run = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (status["recoveryRunId"],),
            ).fetchone()
            error = connection.execute(
                "SELECT * FROM run_errors WHERE run_id = ?",
                (status["recoveryRunId"],),
            ).fetchone()
            assert run["status"] == "failed"
            assert run["error_count"] == 1
            assert error["task_name"] == "market_refresh"
            assert error["retryable"] == 1
            snapshot = build_update_center_snapshot(connection)
        finally:
            connection.close()
        latest = snapshot["runs"][0]
        assert latest["taskId"] == "market_refresh"
        assert latest["errors"][0]["retryTaskId"] == "market_refresh"
        assert latest["errors"][0]["taskNameLabel"] == "行情与流动性"

        changed = update_retry_status(
            db_path,
            status["recoveryRunId"],
            "market_refresh",
            "running",
        )
        assert changed == 1
        changed = update_retry_status(
            db_path,
            status["recoveryRunId"],
            "market_refresh",
            "succeeded",
        )
        assert changed == 1

        repeated = recover_interrupted_updates(
            db_path=db_path,
            status_path=status_path,
        )
        assert repeated["recoveredRuns"] == []
        connection = sqlite3.connect(db_path)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM runs WHERE triggered_by = '自动守护'"
            ).fetchone()[0] == 1
        finally:
            connection.close()


def test_status_is_persistent_and_exposes_guard():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        status_path = root / "update-runtime-status.json"
        initialize_database(
            db_path=db_path,
            snapshot_path=root / "runtime-snapshot.js",
            backup=False,
        )
        initialize_update_recovery(
            db_path=db_path,
            status_path=status_path,
        )
        running = begin_update_status(
            "machine_conclusion_refresh",
            status_path=status_path,
        )
        assert running["state"] == "running"
        assert running["watchdog"]["state"] == "monitoring"
        assert json.loads(status_path.read_text(encoding="utf-8"))["active"] is True

        partial = finish_update_status(
            {
                "status": "partial_success",
                "taskId": "machine_conclusion_refresh",
                "taskLabel": "机器状态与结论发布",
                "runId": "partial-run",
                "message": "部分来源超时。",
            },
            status_path=status_path,
        )
        assert partial["recoveryAvailable"] is True
        assert partial["recoveryTaskId"] == "machine_conclusion_refresh"
        assert partial["watchdog"]["state"] == "recovery_required"

        success = finish_update_status(
            {
                "status": "success",
                "taskId": "machine_conclusion_refresh",
                "taskLabel": "机器状态与结论发布",
                "runId": "success-run",
                "message": "更新完成。",
            },
            status_path=status_path,
        )
        assert success["recoveryAvailable"] is False
        assert success["watchdog"]["state"] == "healthy"
        assert get_update_status()["watchdog"]["version"] == "C1.5-05"


def test_timeout_before_normal_write_is_recorded():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        initialize_database(
            db_path=db_path,
            snapshot_path=root / "runtime-snapshot.js",
            backup=False,
        )
        original = update_runner.refresh_candidates
        original_rebuild = update_runner.rebuild_update_snapshots

        def raise_timeout(**_kwargs):
            raise TimeoutError("模拟外部信源超时")

        def rebuild_temporary_snapshots(db_path):
            return original_rebuild(
                db_path=db_path,
                update_path=root / "update-center-snapshot.js",
                source_path=root / "source-registry-snapshot.js",
            )

        update_runner.refresh_candidates = raise_timeout
        update_runner.rebuild_update_snapshots = rebuild_temporary_snapshots
        try:
            with (
                patch.object(update_runner, "begin_progress"),
                patch.object(update_runner, "finish_progress"),
                patch.object(update_runner, "update_progress"),
                patch.object(update_runner, "heartbeat_progress"),
            ):
                try:
                    update_runner.run_update_task(
                        task_id="machine_conclusion_refresh",
                        db_path=db_path,
                    )
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("超时异常应继续返回给接口层")
        finally:
            update_runner.refresh_candidates = original
            update_runner.rebuild_update_snapshots = original_rebuild

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            run = connection.execute(
                """
                SELECT *
                FROM runs
                WHERE job_name = '凸性机器状态与结论发布'
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            error = connection.execute(
                "SELECT * FROM run_errors WHERE run_id = ?",
                (run["run_id"],),
            ).fetchone()
            assert run["status"] == "failed"
            assert error["error_type"] == "timeout"
            assert error["task_name"] == "machine_conclusion_refresh"
            assert error["retryable"] == 1
        finally:
            connection.close()


def main():
    test_interrupted_status_becomes_retryable_run()
    print("PASS 中断任务自动转为可追溯、可单项重试记录")
    test_status_is_persistent_and_exposes_guard()
    print("PASS 运行状态持久化并向页面提供守护与恢复信息")
    test_timeout_before_normal_write_is_recorded()
    print("PASS 写库前超时也会形成可单项恢复的失败记录")


if __name__ == "__main__":
    main()
