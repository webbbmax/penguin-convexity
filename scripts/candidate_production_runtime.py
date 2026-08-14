#!/usr/bin/env python3
"""Hidden runtime controls for C2.2 candidate production."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

from c2_1_db import DEFAULT_DB_PATH, open_pipeline_db
from c2_2_runtime import atomic_json, iso_time, load_json, pid_is_running, utc_now
from candidate_production import LOCK_PATH, RUNTIME_ROOT, STATUS_PATH, funnel_status, schema_ready


CONFIG_PATH = RUNTIME_ROOT / "config.json"
PAUSE_PATH = RUNTIME_ROOT / "pause.json"
LOG_PATH = RUNTIME_ROOT / "worker.log"
RUNNER_PATH = Path(__file__).resolve().parent / "candidate_production.py"


def default_config() -> dict:
    return {
        "schemaVersion": "c2.2-candidate-production-config-v1",
        "formalHistoricalScanAuthorized": False,
        "paused": False,
        "updatedAt": None,
    }


def load_config(path: Path = CONFIG_PATH) -> dict:
    result = default_config()
    result.update(load_json(path, {}))
    # This flag is never enabled through the product API. It requires a separate
    # source-controlled change after explicit user authorization.
    result["formalHistoricalScanAuthorized"] = bool(result.get("formalHistoricalScanAuthorized"))
    result["paused"] = bool(result.get("paused"))
    return result


def request_pause(requested: bool) -> bool:
    atomic_json(PAUSE_PATH, {"requested": bool(requested), "updatedAt": iso_time(utc_now())})
    return bool(requested)


def worker_pid() -> int | None:
    try:
        value = int(LOCK_PATH.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if pid_is_running(value) else None


def _live_partition_overlay(connection: sqlite3.Connection, payload: dict) -> dict:
    result = dict(payload)
    result["partitions"] = [dict(item) for item in connection.execute(
        "SELECT queue_name,state,COUNT(*) count,SUM(total_count) candidates FROM candidate_scan_partitions GROUP BY queue_name,state"
    )]
    current = connection.execute(
        "SELECT * FROM candidate_scan_partitions WHERE state='running' ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    result["currentPartition"] = dict(current) if current else None
    result["recentPartitions"] = [dict(item) for item in connection.execute(
        """SELECT * FROM candidate_scan_partitions
        WHERE state IN ('running','paused','retrying','failed')
        ORDER BY updated_at DESC LIMIT 12"""
    )]
    current_run = connection.execute(
        "SELECT * FROM candidate_production_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    result["currentRun"] = dict(current_run) if current_run else None
    result["state"] = "running" if current else result.get("state", "ready")
    return result


def status_payload(db_path: Path = DEFAULT_DB_PATH, status_path: Path | None = None) -> dict:
    config = load_config()
    try:
        resolved = Path(db_path).resolve()
        effective_status_path = status_path
        if effective_status_path is None and resolved == DEFAULT_DB_PATH.resolve():
            effective_status_path = STATUS_PATH
        connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            cached = load_json(effective_status_path, {}) if effective_status_path else {}
            current_status_fields = {
                "firstGateDeferredCount",
                "firstGateOutsideWindowCount",
            }
            if (
                cached.get("schemaVersion") == "c2.2-candidate-production-status-v1"
                and current_status_fields.issubset(cached)
            ):
                payload = _live_partition_overlay(connection, cached)
            else:
                payload = funnel_status(connection) if schema_ready(connection) else {
                    "schemaVersion": "c2.2-candidate-production-status-v1",
                    "state": "not_migrated",
                }
                if effective_status_path and payload.get("schemaVersion") == "c2.2-candidate-production-status-v1":
                    atomic_json(effective_status_path, payload)
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as error:
        payload = {
            "schemaVersion": "c2.2-candidate-production-status-v1",
            "state": "program_failure",
            "errorCode": "program_failure",
            "errorDetail": f"{type(error).__name__}: {error}",
        }
    pid = worker_pid()
    payload.update({
        "state": "running" if pid else payload.get("state", "ready"),
        "workerPid": pid,
        "paused": bool(load_json(PAUSE_PATH, {}).get("requested")),
        "formalHistoricalScanAuthorized": config["formalHistoricalScanAuthorized"],
        "formalHistoricalScanStarted": any(
            item.get("queue_name") == "historical_backlog" for item in payload.get("partitions", [])
        ),
        "gate0Rerun": False,
        "runtimeBoundary": "隐藏后台、原子断点、关机后从已有分片继续；不重新扫描Gate 0区块历史。",
    })
    return payload


def pause_for_screening(timeout_seconds: float = 120, poll_seconds: float = 0.25) -> dict:
    pid = worker_pid()
    if not pid:
        return {"status": "idle", "resumeAfter": False}
    queue = "historical_backlog"
    try:
        with closing(open_pipeline_db(DEFAULT_DB_PATH)) as connection:
            row = connection.execute(
                """SELECT COALESCE(
                  (SELECT queue_name FROM candidate_scan_partitions WHERE state='running' ORDER BY updated_at DESC LIMIT 1),
                  (SELECT selected_queue FROM candidate_production_runs ORDER BY started_at DESC LIMIT 1)
                )"""
            ).fetchone()
            if row and row[0] in {"daily_incremental", "historical_backlog"}:
                queue = str(row[0])
    except sqlite3.Error:
        pass
    request_pause(True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not worker_pid():
            return {"status": "paused", "resumeAfter": True, "previousPid": pid, "queue": queue}
        time.sleep(poll_seconds)
    return {
        "status": "timeout",
        "resumeAfter": False,
        "pid": worker_pid(),
        "message": "历史候选扫描尚未到达安全断点；本轮筛选没有并发写数据库。",
    }


def resume_after_screening(handoff: dict) -> dict:
    if not handoff.get("resumeAfter"):
        return {"status": "not_needed"}
    queue = handoff.get("queue") or "daily_incremental"
    return launch_hidden(queue, authorized_resume=queue == "historical_backlog")


def launch_hidden(
    queue: str = "daily_incremental",
    max_partitions: int | None = None,
    *,
    authorized_resume: bool = False,
) -> dict:
    if queue not in {"daily_incremental", "historical_backlog"}:
        raise ValueError("没有找到这个候选生产队列。")
    config = load_config()
    if queue == "historical_backlog" and not config["formalHistoricalScanAuthorized"] and not authorized_resume:
        return {
            "status": "not_authorized",
            "message": "459万历史候选正式扫描尚未获得单独授权，未启动。",
        }
    pid = worker_pid()
    if pid:
        return {"status": "already_running", "pid": pid, "message": "历史候选基础扫描已在隐藏后台运行。"}
    request_pause(False)
    partition_size = 300 if queue == "daily_incremental" else 5000
    command = [sys.executable, str(RUNNER_PATH), "run", "--partition-size", str(partition_size)]
    if queue == "daily_incremental":
        command.extend(["--queue", queue])
    if queue == "historical_backlog":
        command.append("--history-authorized")
    if max_partitions is not None:
        command.extend(["--max-partitions", str(int(max_partitions))])
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("ab") as log:
        process = subprocess.Popen(
            command, cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, creationflags=creationflags, startupinfo=startupinfo, close_fds=True,
        )
    return {"status": "launched", "pid": process.pid, "message": "候选生产化已在隐藏后台启动。"}


def resume_authorized_history(db_path: Path = DEFAULT_DB_PATH) -> dict:
    config = load_config()
    if not config["formalHistoricalScanAuthorized"]:
        return {"status": "not_authorized", "message": "历史候选正式扫描未授权，调度器没有启动。"}
    pid = worker_pid()
    if pid:
        return {"status": "already_running", "pid": pid, "message": "历史候选扫描仍在运行。"}
    if bool(load_json(PAUSE_PATH, {}).get("requested")):
        return {"status": "paused", "message": "历史候选扫描已暂停，调度器保持暂停状态。"}
    with closing(open_pipeline_db(db_path)) as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM candidate_scan_partitions WHERE queue_name='historical_backlog' AND state<>'completed'"
        ).fetchone()[0]
        total = connection.execute(
            "SELECT COUNT(*) FROM candidate_scan_partitions WHERE queue_name='historical_backlog'"
        ).fetchone()[0]
    if total and not remaining:
        return {"status": "completed", "message": "历史候选扫描已经全部完成。"}
    return launch_hidden("historical_backlog")


def retry_partition(partition_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    if not partition_id:
        raise ValueError("缺少要重试的分片编号。")
    with closing(open_pipeline_db(db_path)) as connection:
        row = connection.execute(
            "SELECT queue_name,state FROM candidate_scan_partitions WHERE partition_id=?", (partition_id,)
        ).fetchone()
        if not row:
            raise ValueError("没有找到这个候选分片。")
        if row["queue_name"] == "historical_backlog" and not load_config()["formalHistoricalScanAuthorized"]:
            return {"status": "not_authorized", "message": "历史候选正式扫描尚未授权，未重试。"}
        connection.execute(
            """UPDATE candidate_scan_partitions SET state='retrying',next_retry_at=NULL,error_detail='',updated_at=?
            WHERE partition_id=?""",
            (iso_time(utc_now()), partition_id),
        )
        connection.commit()
        queue = row["queue_name"]
    return launch_hidden(queue)
