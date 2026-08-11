#!/usr/bin/env python3
import argparse
import json
import logging
import sqlite3
from threading import Event, Thread
from pathlib import Path

from build_update_center_snapshot import rebuild_update_snapshots
from c1_9_progress import (
    begin_progress,
    finish_progress,
    heartbeat_progress,
    update_progress,
)
from init_db import DEFAULT_DB_PATH
from refresh_candidate_pool import refresh_candidates
from update_tasks import task_definition
from update_watchdog import record_failed_run


LOGGER = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 10


def safe_progress_call(operation, *args, **kwargs):
    """Keep optional experience telemetry from changing update semantics."""
    try:
        return operation(*args, **kwargs)
    except Exception as error:  # pragma: no cover - platform I/O failures vary
        LOGGER.warning("C1.9 progress telemetry failed: %s", error)
        return None


def progress_heartbeat_loop(stop_event, interval=HEARTBEAT_INTERVAL_SECONDS):
    while not stop_event.wait(interval):
        safe_progress_call(heartbeat_progress)


def update_retry_status(db_path, run_id, task_id, status):
    if not run_id:
        return 0
    task = task_definition(task_id)
    source_ids = task["sourceIds"]
    if not source_ids:
        return 0
    connection = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in source_ids)
        cursor = connection.execute(
            f"""
            UPDATE run_errors
            SET retry_status = ?,
                attempts = CASE
                  WHEN ? = 'running' THEN attempts + 1
                  ELSE attempts
                END,
                last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE run_id = ?
              AND (
                source_id IN ({placeholders})
                OR (source_id IS NULL AND task_name = ?)
              )
              AND retryable = 1
            """,
            (status, status, run_id, *source_ids, task_id),
        )
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()


def run_update_task(
    task_id,
    retry_run_id="",
    tracking_task_id="",
    db_path=DEFAULT_DB_PATH,
    timeout=20,
):
    task = task_definition(task_id)
    progress_components = [*task.get("components", []), "page_snapshot_rebuild"]
    component_order = {
        component: index
        for index, component in enumerate(progress_components, start=1)
    }
    safe_progress_call(
        begin_progress,
        task_id,
        task["label"],
        len(component_order),
    )

    def progress_callback(component, current_item):
        safe_progress_call(
            update_progress,
            component,
            current_item,
            component_order.get(component, 0),
            len(component_order),
        )

    retry_count = update_retry_status(
        db_path,
        retry_run_id,
        task_id,
        "running",
    )
    heartbeat_stop = Event()
    heartbeat_thread = Thread(
        target=progress_heartbeat_loop,
        args=(heartbeat_stop,),
        name="c1-9-progress-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        result = refresh_candidates(
            db_path=db_path,
            timeout=timeout,
            task_id=task_id,
            mode="retry" if retry_run_id else "manual",
            tracking_task_id=tracking_task_id,
            progress_callback=progress_callback,
        )
    except Exception as error:
        update_retry_status(
            db_path,
            retry_run_id,
            task_id,
            "failed",
        )
        record_failed_run(
            task_id=task_id,
            message=f"{task['label']}运行中断：{error}",
            error_type=(
                "timeout"
                if isinstance(error, TimeoutError)
                else "runtime_error"
            ),
            db_path=db_path,
            mode="retry" if retry_run_id else "manual",
        )
        rebuild_update_snapshots(db_path=db_path)
        safe_progress_call(
            finish_progress,
            "failed",
            str(error),
            failed_count=1,
            total_items=len(component_order),
        )
        raise
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)

    retry_status = "succeeded" if result["errors"] == 0 else "failed"
    update_retry_status(
        db_path,
        retry_run_id,
        task_id,
        retry_status,
    )
    rebuild_update_snapshots(db_path=db_path)
    if result["status"] == "success":
        if result.get("sourceDiscoveriesIncomplete"):
            message = (
                f"{task['label']}本轮已完成，分页位置已经保存。"
                f"可以继续扫描下一段；这不是失败。{result['explanation']}"
            )
        else:
            message = f"{task['label']}已完成。{result['explanation']}"
    elif result["status"] == "partial_success":
        message = (
            f"{task['label']}部分完成，请查看失败明细并单独重试。"
            f"{result['explanation']}"
        )
    else:
        message = (
            f"{task['label']}未能取得有效结果，请查看失败原因后重试。"
            f"{result['explanation']}"
        )
    tracking = result.get("tracking") or {}
    if tracking:
        success_count = int(tracking.get("completed") or 0)
        failed_count = int(tracking.get("partial") or 0) + int(tracking.get("failed") or 0)
        total_items = int(tracking.get("eligible") or success_count + failed_count)
        waiting_count = max(0, total_items - success_count - failed_count)
    else:
        total_items = len(component_order)
        failed_count = min(
            total_items,
            max(1 if result["status"] in {"partial_success", "failed"} else 0, int(result.get("errors") or 0)),
        )
        success_count = max(0, total_items - failed_count)
        waiting_count = 0
    safe_progress_call(
        finish_progress,
        result["status"],
        message,
        success_count=success_count,
        failed_count=failed_count,
        waiting_count=waiting_count,
        total_items=total_items,
    )
    return {
        "status": result["status"],
        "taskId": task_id,
        "taskLabel": task["label"],
        "runId": result["runId"],
        "retryRunId": retry_run_id,
        "trackingTaskId": tracking_task_id,
        "retryErrorsUpdated": retry_count,
        "message": message,
        "summary": result,
    }


def main():
    parser = argparse.ArgumentParser(description="运行凸性更新中心单项任务")
    parser.add_argument("--task", required=True)
    parser.add_argument("--retry-run", default="")
    parser.add_argument("--tracking-task", default="")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    print(
        json.dumps(
            run_update_task(
                task_id=args.task,
                retry_run_id=args.retry_run,
                tracking_task_id=args.tracking_task,
                db_path=args.db,
                timeout=args.timeout,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
