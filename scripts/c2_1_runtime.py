#!/usr/bin/env python3
"""C2.1 user-controlled update schedule and hidden process launcher."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from c2_1_enrichment import RETRYABLE_SOURCE_STAGES


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "c2.1"
DEFAULT_CONFIG_PATH = RUNTIME_ROOT / "update-config.json"
DEFAULT_STATE_PATH = RUNTIME_ROOT / "scheduler-state.json"
DEFAULT_PIPELINE_STATUS_PATH = RUNTIME_ROOT / "pipeline-status.json"
DEFAULT_PAUSE_REQUEST_PATH = RUNTIME_ROOT / "pause-current.json"
DEFAULT_MIGRATION_STATUS_PATH = RUNTIME_ROOT / "scheduler-migration.json"
DEFAULT_LOG_PATH = RUNTIME_ROOT / "logs" / "update-runner.log"
DEFAULT_RUNNER = PROJECT_ROOT / "scripts" / "run_c2_1_update.py"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "c2.1-pipeline.db"
ALLOWED_INTERVALS = {1, 3, 6, 12, 24}

DEFAULT_CONFIG = {
    "schemaVersion": "c2.1-update-config-v1",
    "mode": "manual",
    "intervalHours": None,
    "paused": False,
    "timezone": "Asia/Shanghai",
    "updatedAt": None,
}


def utc_now():
    return datetime.now(timezone.utc)


def iso_time(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def load_json(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return dict(fallback) if isinstance(fallback, dict) else fallback


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def load_config(path=DEFAULT_CONFIG_PATH):
    result = dict(DEFAULT_CONFIG)
    result.update(load_json(path, {}))
    validate_config(result)
    return result


def validate_config(config):
    mode = config.get("mode")
    interval = config.get("intervalHours")
    if mode not in {"manual", "automatic"}:
        raise ValueError("更新方式只能是仅手动或自动。")
    if mode == "automatic" and int(interval or 0) not in ALLOWED_INTERVALS:
        raise ValueError("自动频率只能选择每1、3、6、12或24小时。")
    if config.get("timezone") != "Asia/Shanghai":
        raise ValueError("C2.1当前只支持Asia/Shanghai时区。")
    return True


def update_config(changes, path=DEFAULT_CONFIG_PATH):
    config = load_config(path)
    allowed = {"mode", "intervalHours", "paused"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("包含不支持的更新设置。")
    config.update(changes)
    if config["mode"] == "manual":
        config["intervalHours"] = None
    elif config.get("intervalHours") is not None:
        config["intervalHours"] = int(config["intervalHours"])
    validate_config(config)
    config["updatedAt"] = iso_time(utc_now())
    atomic_json(path, config)
    return config


def load_state(path=DEFAULT_STATE_PATH):
    state = {
        "schemaVersion": "c2.1-scheduler-state-v1", "lastStartedAt": None, "lastFinishedAt": None,
        "lastStatus": "never_run", "lastTrigger": None, "lastError": "", "nextRunAt": None,
        "lastLaunchedPid": None, "updatedAt": None,
    }
    state.update(load_json(path, {}))
    return state


def save_state(state, path=DEFAULT_STATE_PATH):
    state = dict(state)
    state["updatedAt"] = iso_time(utc_now())
    atomic_json(path, state)
    return state


def next_run_at(config, from_time=None):
    if config.get("mode") != "automatic" or config.get("paused"):
        return None
    return (from_time or utc_now()) + timedelta(hours=int(config["intervalHours"]))


def pipeline_status():
    return load_json(DEFAULT_PIPELINE_STATUS_PATH, {"state": "not_started", "stage": "not_started", "message": "尚未运行C2.1更新。"})


def pid_is_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def interrupted_run_requires_resume(status=None):
    status = status or pipeline_status()
    if status.get("state") != "running":
        return False
    return not pid_is_running(status.get("processId"))


def due_source_resume(now=None, db_path=DEFAULT_DB_PATH):
    if not Path(db_path).exists():
        return False
    current = iso_time(now or utc_now())
    try:
        connection = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=5)
        due_rows = connection.execute(
            """
            SELECT source_id,stage,updated_at,cursor_json FROM source_cursors
            WHERE status IN ('source_failure','quota_limited')
              AND next_retry_at IS NOT NULL AND datetime(next_retry_at)<=datetime(?)
            """,
            (current,),
        ).fetchall()
        for source_id, stage, failed_at, cursor_json in due_rows:
            payload = json.loads(cursor_json or "{}")
            candidate_ids = payload.get("candidateIds")
            if candidate_ids is None and payload.get("candidateId") is not None:
                candidate_ids = [payload["candidateId"]]
            unresolved = {str(item) for item in (candidate_ids or [])}
            if not unresolved:
                connection.close()
                return True
            newer_rows = connection.execute(
                """
                SELECT cursor_json FROM source_cursors
                WHERE source_id=? AND stage=? AND updated_at>?
                  AND status NOT IN ('source_failure','quota_limited','program_failure')
                """,
                (source_id, stage, failed_at),
            ).fetchall()
            for (newer_json,) in newer_rows:
                newer = json.loads(newer_json or "{}")
                recovered = newer.get("candidateIds")
                if recovered is None and newer.get("candidateId") is not None:
                    recovered = [newer["candidateId"]]
                unresolved.difference_update(str(item) for item in (recovered or []))
                if not unresolved:
                    break
            if unresolved:
                connection.close()
                return True
        connection.close()
        return False
    except (json.JSONDecodeError, sqlite3.Error):
        return False


def pause_current_requested():
    return bool(load_json(DEFAULT_PAUSE_REQUEST_PATH, {}).get("requested"))


def request_pause_current(requested):
    atomic_json(DEFAULT_PAUSE_REQUEST_PATH, {"requested": bool(requested), "updatedAt": iso_time(utc_now())})
    return pause_current_requested()


def is_due(now=None, config_path=DEFAULT_CONFIG_PATH, state_path=DEFAULT_STATE_PATH):
    now = now or utc_now()
    config = load_config(config_path)
    if config["mode"] != "automatic" or config.get("paused"):
        return False
    state = load_state(state_path)
    due = parse_time(state.get("nextRunAt"))
    if not due:
        last = parse_time(state.get("lastStartedAt"))
        due = (last or now - timedelta(hours=int(config["intervalHours"]))) + timedelta(hours=int(config["intervalHours"]))
    return now >= due


def launch_hidden(trigger="manual", action="all", source_id=None):
    if action == "retry_source" and source_id not in RETRYABLE_SOURCE_STAGES:
        raise ValueError("单项更新缺少有效来源。")
    status = pipeline_status()
    if status.get("state") == "running":
        return {"status": "already_running", "message": "已有一条C2.1更新正在后台运行。", "pipeline": status}
    request_pause_current(False)
    DEFAULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(DEFAULT_RUNNER), "--trigger", trigger, "--action", action]
    if action == "retry_source":
        command.extend(["--source-id", source_id])
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    with DEFAULT_LOG_PATH.open("ab") as log:
        process = subprocess.Popen(
            command, cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=creationflags, startupinfo=startupinfo, close_fds=True,
        )
    state = load_state()
    state.update(lastStartedAt=iso_time(utc_now()), lastStatus="launched", lastTrigger=trigger, lastError="", lastLaunchedPid=process.pid)
    save_state(state)
    return {"status": "launched", "pid": process.pid, "message": "C2.1更新已在隐藏后台启动；关闭产品窗口不会中断。"}


def status_payload():
    config = load_config()
    state = load_state()
    pipeline = pipeline_status()
    return {
        "schemaVersion": "c2.1-update-status-v1", "config": config, "scheduler": state, "pipeline": pipeline,
        "pauseCurrentRequested": pause_current_requested(),
        "normalDesktopConsoleWindows": 0,
        "runtimeBoundary": "关闭窗口或Codex后继续；关机/休眠时停止，下一次Windows登录后由统一任务恢复。",
        "existingTaskMigration": load_json(DEFAULT_MIGRATION_STATUS_PATH, {}).get("status", "development_not_migrated"),
    }
