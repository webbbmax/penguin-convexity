#!/usr/bin/env python3
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH
from update_tasks import TASK_DEFINITIONS, task_id_for_job


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATUS_PATH = PROJECT_ROOT / "data" / "update-runtime-status.json"
WATCHDOG_VERSION = "C1.5-05"
DEFAULT_SOURCE_TIMEOUT_SECONDS = 20


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_update_status():
    return {
        "state": "idle",
        "active": False,
        "taskId": "",
        "taskLabel": "",
        "retryRunId": "",
        "trackingTaskId": "",
        "runToken": "",
        "runId": "",
        "message": "当前没有更新任务运行。",
        "startedAt": None,
        "finishedAt": None,
        "workerThreadId": None,
        "recoveryAvailable": False,
        "recoveryTaskId": "",
        "recoveryRunId": "",
        "lastRecoveryAt": None,
        "recoveredRunCount": 0,
    }


def load_update_status(status_path=DEFAULT_STATUS_PATH):
    status = default_update_status()
    try:
        saved = json.loads(Path(status_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return status
    if isinstance(saved, dict):
        status.update(saved)
    return status


def save_update_status(status, status_path=DEFAULT_STATUS_PATH):
    path = Path(status_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return status


def watchdog_payload(status):
    state = status.get("state", "idle")
    recovery_available = bool(status.get("recoveryAvailable"))
    if state == "running":
        watchdog_state = "monitoring"
        label = "正在守护"
        message = "当前任务仍在运行；切换页面不会中断，软件重启后也会识别未完成任务。"
    elif recovery_available:
        watchdog_state = "recovery_required"
        label = "可以恢复"
        message = status.get("message") or "上次任务未完成，可以只重新运行这一项。"
    elif state in {"success", "partial_success"}:
        watchdog_state = "healthy"
        label = "运行正常"
        message = "最近任务已经结束，成功数据和上次有效快照均已保留。"
    elif state == "failed":
        watchdog_state = "attention"
        label = "需要处理"
        message = status.get("message") or "最近任务失败，请查看失败明细。"
    else:
        watchdog_state = "idle"
        label = "等待任务"
        message = "当前没有任务运行，启动后会自动记录状态和恢复入口。"
    return {
        "version": WATCHDOG_VERSION,
        "state": watchdog_state,
        "label": label,
        "message": message,
        "lastCheckedAt": utc_now(),
        "lastRecoveryAt": status.get("lastRecoveryAt"),
        "recoveredRunCount": int(status.get("recoveredRunCount") or 0),
        "sourceTimeoutSeconds": DEFAULT_SOURCE_TIMEOUT_SECONDS,
        "dataProtection": "单项失败不会清除其他成功结果，也不会覆盖上次有效页面快照。",
    }


def status_with_watchdog(status):
    return {**status, "watchdog": watchdog_payload(status)}


def duration_ms(started_at, finished_at):
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        return max(0, round((finished - started).total_seconds() * 1000))
    except (AttributeError, TypeError, ValueError):
        return 0


def recovery_run_id(task_id, finished_at):
    digest = hashlib.sha256(
        f"{task_id}|{finished_at}".encode("utf-8")
    ).hexdigest()[:16]
    return f"watchdog-recovery-{digest}"


def record_failed_run(
    task_id,
    message,
    error_type="interrupted",
    db_path=DEFAULT_DB_PATH,
    started_at=None,
    mode="manual",
):
    if task_id not in TASK_DEFINITIONS:
        return []
    now = utc_now()
    task = TASK_DEFINITIONS[task_id]
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    recovered = []
    try:
        running_rows = list(
            connection.execute(
                """
                SELECT run_id, started_at
                FROM runs
                WHERE job_name = ? AND status = 'running'
                ORDER BY started_at
                """,
                (task["jobName"],),
            )
        )
        if not running_rows:
            run_id = recovery_run_id(task_id, now)
            run_started_at = started_at or now
            connection.execute(
                """
                INSERT OR IGNORE INTO runs (
                  run_id, job_name, mode, status, started_at, finished_at,
                  duration_ms, error_count, zero_result_class,
                  zero_result_explanation, triggered_by, error_summary,
                  schema_version
                )
                VALUES (?, ?, ?, 'failed', ?, ?, ?, 1, 'source_failure',
                        ?, '自动守护', ?, 1)
                """,
                (
                    run_id,
                    task["jobName"],
                    mode if mode in {"manual", "retry"} else "manual",
                    run_started_at,
                    now,
                    duration_ms(run_started_at, now),
                    message,
                    message,
                ),
            )
            running_rows = [{"run_id": run_id, "started_at": run_started_at}]
        for row in running_rows:
            run_id = row["run_id"]
            connection.execute(
                """
                UPDATE runs
                SET status = 'failed',
                    finished_at = ?,
                    duration_ms = ?,
                    error_count = CASE WHEN error_count > 0 THEN error_count ELSE 1 END,
                    zero_result_class = 'source_failure',
                    zero_result_explanation = ?,
                    error_summary = ?
                WHERE run_id = ?
                """,
                (
                    now,
                    duration_ms(row["started_at"], now),
                    message,
                    message,
                    run_id,
                ),
            )
            error_id = hashlib.sha256(
                f"{run_id}|{task_id}|{error_type}".encode("utf-8")
            ).hexdigest()[:20]
            connection.execute(
                """
                INSERT OR REPLACE INTO run_errors (
                  error_id, run_id, source_id, task_name, error_type, message,
                  retryable, retry_status, attempts, first_seen_at, last_seen_at
                )
                VALUES (?, ?, NULL, ?, ?, ?, 1, 'not_requested', 1, ?, ?)
                """,
                (
                    f"watchdog-error-{error_id}",
                    run_id,
                    task_id,
                    error_type,
                    message,
                    now,
                    now,
                ),
            )
            recovered.append(run_id)
        connection.commit()
    finally:
        connection.close()
    return recovered


def recover_interrupted_updates(
    db_path=DEFAULT_DB_PATH,
    status_path=DEFAULT_STATUS_PATH,
):
    previous = load_update_status(status_path)
    previous_task_id = previous.get("taskId", "")
    recovered = []
    message = "软件上次关闭或后台任务意外中断；其他成功数据已经保留，可以只重新运行这一项。"

    connection = sqlite3.connect(db_path)
    try:
        running_jobs = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT job_name FROM runs WHERE status = 'running'"
            )
        ]
    finally:
        connection.close()

    task_ids = {
        task_id_for_job(job_name)
        for job_name in running_jobs
        if task_id_for_job(job_name)
    }
    if previous.get("state") == "running" and previous_task_id in TASK_DEFINITIONS:
        task_ids.add(previous_task_id)
    for task_id in sorted(task_ids):
        recovered.extend(
            record_failed_run(
                task_id=task_id,
                message=message,
                error_type="interrupted",
                db_path=db_path,
                started_at=(
                    previous.get("startedAt")
                    if task_id == previous_task_id
                    else None
                ),
                mode="retry" if previous.get("retryRunId") else "manual",
            )
        )

    if recovered:
        recovery_task_id = (
            previous_task_id
            if previous_task_id in task_ids
            else task_id_for_job(running_jobs[0])
        )
        previous.update(
            state="failed",
            active=False,
            taskId=recovery_task_id,
            taskLabel=TASK_DEFINITIONS[recovery_task_id]["label"],
            runId=recovered[0],
            message=message,
            finishedAt=utc_now(),
            workerThreadId=None,
            recoveryAvailable=True,
            recoveryTaskId=recovery_task_id,
            recoveryRunId=recovered[0],
            lastRecoveryAt=utc_now(),
            recoveredRunCount=int(previous.get("recoveredRunCount") or 0)
            + len(recovered),
        )
    save_update_status(previous, status_path)
    return {
        "status": previous,
        "recoveredRuns": recovered,
    }
