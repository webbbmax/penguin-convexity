#!/usr/bin/env python3
"""Runtime state and one hidden launcher for the two C2.2 jobs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import ctypes
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "c2.2"
DEFAULT_CONFIG_PATH = RUNTIME_ROOT / "update-config.json"
DEFAULT_STATE_PATH = RUNTIME_ROOT / "scheduler-state.json"
DEFAULT_PAUSE_PATH = RUNTIME_ROOT / "pause-current.json"
DEFAULT_LOCK_PATH = RUNTIME_ROOT / "pipeline.lock"
DEFAULT_LOG_PATH = RUNTIME_ROOT / "logs" / "update-runner.log"
DEFAULT_RUNNER = PROJECT_ROOT / "scripts" / "run_c2_2_update.py"
JOB_CODES = ("screening", "convexity_tracking")
ALLOWED_INTERVALS = {1, 3, 6, 12, 24}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_time(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def parse_time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def load_json(path: Path, fallback: dict) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(fallback)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return dict(fallback)


def atomic_json(path: Path, payload: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return path


def default_job_config() -> dict:
    return {"mode": "automatic", "intervalHours": 24, "paused": False}


def default_config() -> dict:
    return {
        "schemaVersion": "c2.2-update-config-v1",
        "timezone": "Asia/Shanghai",
        "updatedAt": None,
        "jobs": {code: default_job_config() for code in JOB_CODES},
    }


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    result = default_config()
    raw = load_json(path, {})
    for key in ("schemaVersion", "timezone", "updatedAt"):
        if key in raw:
            result[key] = raw[key]
    for code in JOB_CODES:
        incoming = raw.get("jobs", {}).get(code, {}) if isinstance(raw.get("jobs"), dict) else {}
        result["jobs"][code].update(incoming)
    validate_config(result)
    return result


def validate_config(config: dict) -> bool:
    if config.get("timezone") != "Asia/Shanghai":
        raise ValueError("C2.2当前只支持Asia/Shanghai时区。")
    for code in JOB_CODES:
        job = config.get("jobs", {}).get(code, {})
        if job.get("mode") not in {"manual", "automatic"}:
            raise ValueError(f"{code}更新方式只能是仅手动或自动。")
        if job.get("mode") == "automatic" and int(job.get("intervalHours") or 0) not in ALLOWED_INTERVALS:
            raise ValueError(f"{code}自动频率只能选择每1、3、6、12或24小时。")
    return True


def update_config(job_code: str, changes: dict, path: Path = DEFAULT_CONFIG_PATH) -> dict:
    if job_code not in JOB_CODES:
        raise ValueError("没有找到这个C2.2作业。")
    unknown = set(changes) - {"mode", "intervalHours", "paused"}
    if unknown:
        raise ValueError("包含不支持的更新设置。")
    config = load_config(path)
    job = config["jobs"][job_code]
    job.update(changes)
    if job.get("mode") == "manual":
        job["intervalHours"] = None
    elif job.get("intervalHours") is not None:
        job["intervalHours"] = int(job["intervalHours"])
    config["updatedAt"] = iso_time(utc_now())
    validate_config(config)
    atomic_json(path, config)
    return config


def default_state() -> dict:
    return {"schemaVersion": "c2.2-scheduler-state-v1", "updatedAt": None, "lastTrigger": None, "lastStartedAt": None, "lastFinishedAt": None, "lastStatus": "never_run", "nextRunAt": None, "lastError": "", "lastLaunchedPid": None}


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict:
    result = default_state()
    result.update(load_json(path, {}))
    return result


def save_state(state: dict, path: Path = DEFAULT_STATE_PATH) -> dict:
    state = {**default_state(), **state, "updatedAt": iso_time(utc_now())}
    atomic_json(path, state)
    return state


def job_status(job_code: str) -> dict:
    if job_code not in JOB_CODES:
        raise ValueError("没有找到这个C2.2作业。")
    path = RUNTIME_ROOT / "jobs" / f"{job_code}.json"
    return load_json(path, {"schemaVersion": "c2.2-job-status-v1", "jobCode": job_code, "state": "not_started", "stage": "not_started", "message": "尚未运行。", "progress": {"completed": 0, "total": 0}})


def save_job_status(payload: dict) -> dict:
    return atomic_json(RUNTIME_ROOT / "jobs" / f"{payload['jobCode']}.json", payload) and payload


def pause_current_requested() -> bool:
    return bool(load_json(DEFAULT_PAUSE_PATH, {}).get("requested"))


def request_pause_current(requested: bool) -> bool:
    atomic_json(DEFAULT_PAUSE_PATH, {"requested": bool(requested), "updatedAt": iso_time(utc_now())})
    return pause_current_requested()


def next_run_at(job_code: str, from_time: datetime | None = None) -> str | None:
    config = load_config()["jobs"][job_code]
    if config.get("mode") != "automatic" or config.get("paused"):
        return None
    return iso_time((from_time or utc_now()) + timedelta(hours=int(config["intervalHours"])))


def is_due(job_code: str, now: datetime | None = None) -> bool:
    config = load_config()["jobs"][job_code]
    if config.get("mode") != "automatic" or config.get("paused"):
        return False
    status = job_status(job_code)
    due = parse_time(status.get("nextDueAt"))
    if due is None:
        return True
    return (now or utc_now()) >= due


def pid_is_running(pid: int | None) -> bool:
    try:
        pid_value = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_value <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid_value)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid_value, 0)
        return True
    except OSError:
        return False


@contextmanager
def pipeline_lock(path: Path = DEFAULT_LOCK_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        acquired = True
    except FileExistsError:
        stale = False
        try:
            stale = not pid_is_running(int(path.read_text(encoding="ascii").strip()))
        except (OSError, ValueError):
            stale = True
        if stale:
            try:
                path.unlink()
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                acquired = True
            except (FileExistsError, OSError):
                acquired = False
    try:
        yield acquired
    finally:
        if acquired:
            path.unlink(missing_ok=True)


def launch_hidden(job_code: str, trigger: str = "manual") -> dict:
    if job_code not in JOB_CODES and job_code != "all":
        raise ValueError("没有找到这个C2.2作业。")
    statuses = [job_status(code) for code in JOB_CODES]
    if any(status.get("state") == "running" for status in statuses):
        return {"status": "already_running", "message": "已有C2.2作业在隐藏后台运行。", "jobs": statuses}
    request_pause_current(False)
    DEFAULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(DEFAULT_RUNNER), "--job", job_code, "--trigger", trigger]
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    with DEFAULT_LOG_PATH.open("ab") as log:
        process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=creationflags, startupinfo=startupinfo, close_fds=True)
    state = load_state()
    save_state({**state, "lastStartedAt": iso_time(utc_now()), "lastStatus": "launched", "lastTrigger": trigger, "lastError": "", "lastLaunchedPid": process.pid})
    return {"status": "launched", "pid": process.pid, "message": "C2.2更新已在隐藏后台启动；关闭产品窗口不会中断。"}


def status_payload() -> dict:
    config = load_config()
    return {
        "schemaVersion": "c2.2-update-status-v1",
        "config": config,
        "scheduler": load_state(),
        "jobs": {code: job_status(code) for code in JOB_CODES},
        "pauseCurrentRequested": pause_current_requested(),
        "normalDesktopConsoleWindows": 0,
        "runtimeBoundary": "关闭窗口或Codex后继续；关机/休眠时停止，下一次Windows登录后由同一任务恢复。",
        "singleWindowsTask": "PenguinConvexity-C1.8-Scheduler",
    }
