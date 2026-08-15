#!/usr/bin/env python3
"""C2.5 read-only management composition over existing authoritative state."""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from c2_2_runtime import pid_is_running
from c2_5_rule_governance import RuleGovernanceStore
from c2_5_rules import build_dual_replay_evidence, build_rule_transparency, iso_time, utc_now


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_INVENTORY_PATH = PROJECT_ROOT / "docs" / "C2.5_TASK_INVENTORY.json"
TASK_SUPPLEMENT_PATH = PROJECT_ROOT / "docs" / "C2.5_TASK_INVENTORY_SUPPLEMENT.json"
INHERITANCE_PATH = PROJECT_ROOT / "docs" / "C2.5_INHERITANCE_MANIFEST.json"
AUDIT_PATH = PROJECT_ROOT / "runtime" / "c2.5" / "management-audit.jsonl"
CHAIN_ORDER = (
    "ethereum-mainnet",
    "solana-mainnet",
    "base-mainnet",
    "arbitrum-mainnet",
    "bnb-mainnet",
    "robinhood-mainnet",
)
CHAIN_LABELS = {
    "ethereum-mainnet": "Ethereum",
    "solana-mainnet": "Solana",
    "base-mainnet": "Base",
    "arbitrum-mainnet": "Arbitrum One",
    "bnb-mainnet": "BNB Smart Chain",
    "robinhood-mainnet": "Robinhood Chain",
}
LIVE_STATES = {
    "not_started",
    "waiting",
    "launching",
    "running",
    "pause_requested",
    "safe_paused",
    "partial",
    "completed",
    "failed",
    "blocked",
    "stale",
    "disabled",
    "unknown",
}
SUCCESS_STATES = {"success", "completed", "complete", "no_change", "ready"}
FAILURE_STATES = {"failed", "program_failure", "source_failure", "blocked"}
SOURCE_STATES = {
    "success",
    "no_data",
    "quota_limited",
    "source_failure",
    "unsupported",
    "configuration_missing",
    "program_failure",
}


def load_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_sha256(path: Path) -> str:
    payload = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest()


def stable_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_js_payload(path: Path) -> dict[str, Any]:
    source = Path(path).read_text(encoding="utf-8").strip()
    if "=" not in source or not source.endswith(";"):
        raise ValueError(f"快照格式无效：{path.name}")
    value = json.loads(source.split("=", 1)[1][:-1].strip())
    if not isinstance(value, dict):
        raise ValueError(f"快照不是对象：{path.name}")
    return value


def normalize_source_state(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "healthy": "success",
        "ok": "success",
        "failed": "source_failure",
        "restricted": "quota_limited",
        "not_configured": "configuration_missing",
        "not_supported": "unsupported",
        "never_run": "no_data",
        "partial": "source_failure",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in SOURCE_STATES else "no_data"


def normalize_live_state(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "never_run": "not_started",
        "not_migrated": "blocked",
        "ready": "waiting",
        "idle": "waiting",
        "success": "completed",
        "complete": "completed",
        "paused": "safe_paused",
        "partial_success": "partial",
        "program_failure": "failed",
        "already_running": "running",
    }
    result = aliases.get(value, value)
    return result if result in LIVE_STATES else "unknown"


def progress_payload(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    completed_raw = value.get("completed", value.get("completedUnits"))
    total_raw = value.get("total", value.get("totalUnits"))
    try:
        completed = max(0, int(completed_raw)) if completed_raw is not None else None
    except (TypeError, ValueError):
        completed = None
    try:
        total = int(total_raw) if total_raw is not None else None
    except (TypeError, ValueError):
        total = None
    if total is not None and total > 0 and completed is not None:
        percent = min(100, max(0, round(completed * 100 / total, 2)))
        kind = "determinate"
    elif total == 0 and completed == 0 and not value.get("stage") and not value.get("message"):
        completed = None
        total = None
        percent = None
        kind = "not_applicable"
    elif value and any(item is not None for item in (completed_raw, value.get("stage"), value.get("message"))):
        total = None
        percent = None
        kind = "indeterminate"
    else:
        completed = None
        total = None
        percent = None
        kind = "not_applicable"
    return {
        "kind": kind,
        "completed": completed,
        "total": total,
        "percent": percent,
        "stage": value.get("stage"),
        "message": value.get("message") or ("没有可用进度" if kind == "not_applicable" else "总量未知，显示当前阶段与已处理量"),
    }


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        pid = int(Path(path).read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        return {"exists": Path(path).exists(), "pid": None, "pidLive": False}
    return {"exists": True, "pid": pid, "pidLive": pid_is_running(pid)}


def compose_authoritative_job_state(
    raw: dict[str, Any],
    *,
    lock: dict[str, Any],
    pause_requested: bool,
    now: datetime,
    heartbeat_timeout: timedelta = timedelta(minutes=20),
) -> tuple[str, list[dict[str, Any]]]:
    raw_state = normalize_live_state(raw.get("state"))
    heartbeat = parse_time(raw.get("lastHeartbeatAt") or raw.get("updatedAt"))
    heartbeat_fresh = bool(heartbeat and now - heartbeat <= heartbeat_timeout)
    live_basis = bool(lock.get("exists") and lock.get("pidLive") and heartbeat_fresh)
    basis = [
        {"kind": "status_file", "value": raw.get("state") or "missing", "authoritative": True},
        {"kind": "process", "value": lock.get("pid"), "live": bool(lock.get("pidLive")), "authoritative": True},
        {"kind": "lock", "value": bool(lock.get("exists")), "authoritative": True},
        {"kind": "heartbeat", "value": raw.get("lastHeartbeatAt") or raw.get("updatedAt"), "fresh": heartbeat_fresh, "authoritative": True},
    ]
    if raw_state in {"running", "launching"}:
        live_state = "running" if live_basis else "stale"
    else:
        live_state = raw_state
    if pause_requested:
        if live_state == "running":
            live_state = "pause_requested"
        elif raw_state == "safe_paused" or (not lock.get("pidLive") and raw.get("checkpoint")):
            live_state = "safe_paused"
    return live_state, basis


def _extract_post_paths(source_path: Path) -> set[str]:
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    paths: set[str] = set()
    handler = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "QuietHandler"), None)
    method = next((node for node in (handler.body if handler else []) if isinstance(node, ast.FunctionDef) and node.name == "do_POST"), None)
    for node in ast.walk(method) if method else []:
        if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
            values = [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            if values and all(value.startswith("/api/") for value in values):
                paths.update(values)
    return paths


def _read_windows_tasks() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    command = (
        "$ErrorActionPreference='Stop'; "
        "Get-ScheduledTask | Where-Object {$_.TaskName -like 'Penguin*'} | ForEach-Object {"
        "$t=$_;$i=Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath;"
        "[pscustomobject]@{taskName=$t.TaskName;taskPath=$t.TaskPath;enabled=($t.State -ne 'Disabled');"
        "state=[string]$t.State;lastTaskResult=$i.LastTaskResult;lastRunTime=$i.LastRunTime.ToUniversalTime().ToString('o');"
        "nextRunTime=$i.NextRunTime.ToUniversalTime().ToString('o');"
        "actions=@($t.Actions | ForEach-Object {([string]$_.Execute+' '+[string]$_.Arguments).Trim()});"
        "triggers=@($t.Triggers | ForEach-Object {[string]$_})}} | ConvertTo-Json -Depth 5 -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    rows = payload if isinstance(payload, list) else [payload]
    return [row for row in rows if isinstance(row, dict)]


def _hidden_downstream(project_root: Path) -> list[str]:
    path = project_root / "scripts" / "run-c2-1-update-hidden.vbs"
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    downstream = []
    if "temp_artifact_retention.py" in source:
        downstream.append("temp_artifact_retention.py sweep --min-interval-hours 24")
    if "run_c2_2_update.py" in source:
        downstream.append("run_c2_2_update.py --job due --trigger automatic")
    return downstream


def _task_control_specs(task_id: str, live_state: str, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    labels = {
        "run_now": "立即运行",
        "resume_checkpoint": "从检查点恢复",
        "safe_pause": "安全暂停",
        "cancel_pause_request": "取消暂停请求",
        "pause_future_cycles": "暂停后续周期",
        "resume_future_cycles": "恢复后续周期",
        "set_interval_1_3_6_12_24": "修改运行频率",
        "retry_registered_source": "重试所选来源",
        "retry_existing_failed_partition": "重试失败分片",
        "run_retention_sweep": "运行一次保留检查",
    }
    allowed = list(entry.get("allowedControls") or [])
    if task_id == "maintenance.temp_artifact_retention":
        allowed = ["run_retention_sweep"]
    controls: list[dict[str, Any]] = []
    disabled: list[dict[str, Any]] = []
    for action in allowed:
        reason = ""
        normalized = action
        if action == "set_interval_1_3_6_12_24":
            normalized = "set_interval"
        if action == "retry_existing_failed_partition":
            normalized = "retry_partition"
        if live_state == "stale":
            reason = "真实状态陈旧，先恢复状态一致性。"
        elif live_state == "unknown":
            reason = "权威状态不可用，不能安全执行高影响操作。"
        elif action == "run_now" and live_state in {"running", "pause_requested"}:
            reason = "已有作业正在运行。"
        elif action == "safe_pause" and live_state != "running":
            reason = "当前没有可安全暂停的运行。"
        elif action == "cancel_pause_request" and live_state != "pause_requested":
            reason = "当前没有待取消的暂停请求。"
        spec = {"action": normalized, "label": labels.get(action, action), "requiresPreview": True}
        if action == "set_interval_1_3_6_12_24":
            spec["allowedValues"] = [1, 3, 6, 12, 24]
        (disabled if reason else controls).append({**spec, **({"reason": reason} if reason else {})})
    return controls, disabled


class C25ControlPlane:
    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        windows_reader: Callable[[], list[dict[str, Any]]] = _read_windows_tasks,
        clock: Callable[[], datetime] = utc_now,
        startup_state_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.app_root = self.project_root / "app"
        self.runtime_root = self.project_root / "runtime"
        self.data_root = self.project_root / "data"
        self.windows_reader = windows_reader
        self.clock = clock
        self.startup_state_provider = startup_state_provider or (lambda: {})
        self.inventory = load_json(self.project_root / "docs" / "C2.5_TASK_INVENTORY.json", {})
        self.supplement = load_json(self.project_root / "docs" / "C2.5_TASK_INVENTORY_SUPPLEMENT.json", {})
        self.inheritance = load_json(self.project_root / "docs" / "C2.5_INHERITANCE_MANIFEST.json", {})
        self.rule_governance = RuleGovernanceStore(
            self.runtime_root / "c2.5" / "rule-governance",
            rule_path=self.project_root / "docs" / "C2.4_RULE_CONFIG.json",
            trial_path=self.project_root / "docs" / "C2.4_RULE_RELAXATION_TRIAL_20260813.json",
            clock=clock,
        )

    def _latest_rollback_candidate(self, task_id: str) -> dict[str, Any] | None:
        rows = self._read_audit()
        already_rolled_back = {
            str(row.get("rollbackOf"))
            for row in rows
            if row.get("auditStage") == "result" and row.get("backendAccepted") and row.get("rollbackOf")
        }
        allowed = {"set_interval", "pause_future_cycles", "resume_future_cycles", "safe_pause", "cancel_pause_request"}
        for row in reversed(rows):
            if (
                row.get("auditStage") == "result"
                and row.get("backendAccepted")
                and row.get("taskId") == task_id
                and row.get("action") in allowed
                and row.get("auditId") not in already_rolled_back
            ):
                return {"auditId": row["auditId"], "action": row["action"], "requestedAt": row.get("requestedAt")}
        return None

    def _runtime_bundle(self) -> dict[str, Any]:
        c22_root = self.runtime_root / "c2.2"
        config = load_json(c22_root / "update-config.json", {})
        scheduler = load_json(c22_root / "scheduler-state.json", {})
        pause = load_json(c22_root / "pause-current.json", {})
        jobs = {code: load_json(c22_root / "jobs" / f"{code}.json", {}) for code in ("screening", "convexity_tracking")}
        candidate_root = c22_root / "candidate-production"
        candidate = load_json(candidate_root / "status.json", {})
        candidate_config = load_json(candidate_root / "config.json", {})
        candidate_pause = load_json(candidate_root / "pause.json", {})
        return {
            "config": config,
            "scheduler": scheduler,
            "pause": pause,
            "jobs": jobs,
            "pipelineLock": _read_lock(c22_root / "pipeline.lock"),
            "candidate": candidate,
            "candidateConfig": candidate_config,
            "candidatePause": candidate_pause,
            "candidateLock": _read_lock(candidate_root / "worker.lock"),
        }

    def _windows_bundle(self) -> list[dict[str, Any]]:
        rows = self.windows_reader()
        normalized = []
        for row in rows:
            task_name = str(row.get("taskName") or row.get("TaskName") or "")
            result = row.get("lastTaskResult", row.get("LastTaskResult"))
            try:
                result = int(result)
            except (TypeError, ValueError):
                result = None
            actions = row.get("actions") or row.get("Actions") or []
            if isinstance(actions, str):
                actions = [actions]
            normalized.append(
                {
                    "taskName": task_name,
                    "taskPath": row.get("taskPath") or row.get("TaskPath"),
                    "enabled": bool(row.get("enabled", row.get("Enabled", True))),
                    "state": str(row.get("state") or row.get("State") or "Unknown"),
                    "lastTaskResult": result,
                    "lastRunTime": row.get("lastRunTime") or row.get("LastRunTime"),
                    "nextRunTime": row.get("nextRunTime") or row.get("NextRunTime"),
                    "actions": list(actions),
                    "triggers": row.get("triggers") or row.get("Triggers") or [],
                }
            )
        return normalized

    def _job_task(self, entry: dict[str, Any], bundle: dict[str, Any], now: datetime) -> dict[str, Any]:
        job_code = "screening" if entry["taskId"] == "c22.screening" else "convexity_tracking"
        raw = bundle["jobs"].get(job_code) or {}
        pause = bundle["pause"]
        requested = bool(pause.get("requested")) and pause.get("jobCode") in {None, "", job_code}
        live_state, basis = compose_authoritative_job_state(
            raw,
            lock=bundle["pipelineLock"],
            pause_requested=requested,
            now=now,
        )
        config = (bundle["config"].get("jobs") or {}).get(job_code, {})
        progress = progress_payload({**(raw.get("progress") or {}), "stage": raw.get("stage"), "message": raw.get("message")})
        failure = {
            "code": raw.get("errorCode") or raw.get("error_code"),
            "summary": raw.get("message") if live_state in {"failed", "partial", "stale"} else None,
            "detail": raw.get("errorDetail") or raw.get("error_detail"),
            "affectedObjectCount": raw.get("affectedObjectCount"),
            "staleRisk": live_state == "stale",
        }
        return {
            "liveState": live_state,
            "stateBasis": basis,
            "schedule": {
                "mode": config.get("mode", "automatic"),
                "intervalHours": config.get("intervalHours", 24),
                "paused": bool(config.get("paused")),
                "timezone": bundle["config"].get("timezone", "Asia/Shanghai"),
            },
            "lastStartedAt": raw.get("lastStartedAt") or bundle["scheduler"].get("lastStartedAt"),
            "lastFinishedAt": raw.get("lastFinishedAt") or raw.get("lastCompletedAt"),
            "nextDueAt": raw.get("nextDueAt"),
            "progress": progress,
            "lastHeartbeatAt": raw.get("lastHeartbeatAt") or raw.get("updatedAt"),
            "checkpoint": raw.get("checkpoint") or raw.get("currentItem"),
            "sources": raw.get("sources") or raw.get("sourceIds") or [],
            "failure": failure,
        }

    def _candidate_task(self, entry: dict[str, Any], bundle: dict[str, Any], now: datetime) -> dict[str, Any]:
        task_id = entry["taskId"]
        queue = "daily_incremental" if task_id == "candidate.daily_incremental" else "historical_backlog"
        raw = bundle["candidate"]
        partitions = [row for row in raw.get("partitions", []) if row.get("queue_name") == queue]
        recent = [row for row in raw.get("recentPartitions", []) if row.get("queue_name") == queue]
        current = raw.get("currentPartition") if isinstance(raw.get("currentPartition"), dict) else None
        if current and current.get("queue_name") != queue:
            current = None
        pause_requested = bool(bundle["candidatePause"].get("requested"))
        if current:
            raw_state = "running"
        elif queue == "historical_backlog":
            incomplete_states = {str(row.get("state") or "unknown") for row in partitions} - {"completed"}
            raw_state = "waiting" if incomplete_states else "completed"
        else:
            raw_state = raw.get("state") or "waiting"
        status_input = {
            "state": raw_state,
            "lastHeartbeatAt": raw.get("lastHeartbeatAt") or (current or {}).get("updated_at"),
            "checkpoint": current or (recent[0] if recent else None),
        }
        live_state, basis = compose_authoritative_job_state(
            status_input,
            lock=bundle["candidateLock"],
            pause_requested=pause_requested,
            now=now,
        )
        if queue == "historical_backlog" and all(row.get("state") == "completed" for row in partitions):
            live_state = "completed"
        totals = {
            "completed": sum(int(row.get("candidates") or 0) for row in partitions if row.get("state") == "completed"),
            "total": sum(int(row.get("candidates") or 0) for row in partitions) or None,
            "stage": (current or {}).get("partition_id"),
            "message": "按既有分片断点处理候选。" if partitions else "尚无可用分片。",
        }
        failures = [row for row in recent if row.get("state") == "failed"]
        return {
            "liveState": live_state,
            "stateBasis": [*basis, {"kind": "pipeline_db_partitions", "value": len(partitions), "authoritative": True}],
            "schedule": {"mode": "component", "paused": pause_requested},
            "lastStartedAt": (raw.get("currentRun") or {}).get("started_at"),
            "lastFinishedAt": (raw.get("currentRun") or {}).get("finished_at"),
            "nextDueAt": None,
            "progress": progress_payload(totals),
            "lastHeartbeatAt": status_input["lastHeartbeatAt"],
            "checkpoint": status_input["checkpoint"],
            "sources": [],
            "failure": {
                "code": "failed_partition" if failures else None,
                "summary": f"{len(failures)}个失败分片" if failures else None,
                "detail": None,
                "affectedObjectCount": sum(int(row.get("total_count") or 0) for row in failures),
                "staleRisk": live_state == "stale",
            },
            "partitions": recent,
            "formalHistoricalScanAuthorized": bool(bundle["candidateConfig"].get("formalHistoricalScanAuthorized")),
        }

    def _base_task(self, entry: dict[str, Any], now: datetime) -> dict[str, Any]:
        lifecycle = entry.get("lifecycleClass", "unknown")
        live_state = "disabled" if lifecycle == "disabled" else "completed" if lifecycle == "completed_one_off" else "unknown"
        return {
            "liveState": live_state,
            "stateBasis": [{"kind": "frozen_inventory", "value": lifecycle, "authoritative": False}],
            "schedule": {"mode": "not_applicable"},
            "lastStartedAt": None,
            "lastFinishedAt": None,
            "nextDueAt": None,
            "progress": progress_payload({}),
            "lastHeartbeatAt": None,
            "checkpoint": None,
            "sources": [],
            "failure": {"code": None, "summary": None, "detail": None, "affectedObjectCount": None, "staleRisk": False},
        }

    def _manager_task(
        self,
        entry: dict[str, Any],
        *,
        bundle: dict[str, Any],
        windows: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        task_id = entry["taskId"]
        overlay = self._base_task(entry, now)
        scheduler_next = None
        if task_id in {"c22.screening", "c22.convexity_tracking"}:
            overlay.update(self._job_task(entry, bundle, now))
        elif task_id in {"candidate.daily_incremental", "candidate.history_backlog"}:
            overlay.update(self._candidate_task(entry, bundle, now))
        elif task_id == "windows.scheduler.primary":
            row = next((item for item in windows if item["taskName"] == "PenguinConvexity-C1.8-Scheduler"), None)
            if row:
                running_code = row.get("lastTaskResult") in {267009, 0x41301}
                overlay.update(
                    {
                        "liveState": "running" if running_code or row["state"].lower() == "running" else "waiting" if row["enabled"] else "disabled",
                        "stateBasis": [{"kind": "windows_scheduled_task", "value": row, "authoritative": True}],
                        "lastStartedAt": row.get("lastRunTime"),
                        "lastFinishedAt": None if running_code else row.get("lastRunTime"),
                        "schedulerNextTriggerAt": row.get("nextRunTime"),
                        "schedule": {"mode": "windows_check", "enabled": row["enabled"], "trigger": row.get("triggers"), "everyMinutes": 15},
                        "machineAction": row.get("actions"),
                        "lastTaskResult": row.get("lastTaskResult"),
                    }
                )
                scheduler_next = row.get("nextRunTime")
            else:
                overlay["liveState"] = "unknown"
                overlay["stateBasis"] = [{"kind": "windows_scheduled_task", "value": "not_observed", "authoritative": True}]
        elif task_id == "windows.hidden_runner":
            overlay.update(
                {
                    "liveState": "waiting",
                    "stateBasis": [{"kind": "script_parse", "value": _hidden_downstream(self.project_root), "authoritative": True}],
                    "downstreamResolved": _hidden_downstream(self.project_root),
                }
            )
        elif task_id == "maintenance.temp_artifact_retention":
            state = load_json(self.runtime_root / "maintenance" / "temp-artifact-sweep.json", {})
            last = state.get("lastResult") or {}
            overlay.update(
                {
                    "liveState": "failed" if last.get("blockedArtifacts") else "completed" if last else "not_started",
                    "stateBasis": [{"kind": "maintenance_state", "value": state.get("lastSweepAt"), "authoritative": True}],
                    "lastFinishedAt": state.get("lastSweepAt"),
                    "failure": {
                        "code": "blocked_artifacts" if last.get("blockedArtifacts") else None,
                        "summary": f"{last.get('blockedArtifacts')}个产物被阻断" if last.get("blockedArtifacts") else None,
                        "detail": last.get("blocked"),
                        "affectedObjectCount": last.get("blockedArtifacts", 0),
                        "staleRisk": False,
                    },
                }
            )
        elif task_id == "service.startup_snapshot_validation":
            state = self.startup_state_provider() or {}
            overlay.update(
                {
                    "liveState": normalize_live_state(state.get("state")),
                    "stateBasis": [{"kind": "in_process_startup_state", "value": state, "authoritative": True}],
                    "lastStartedAt": state.get("startedAt"),
                    "lastFinishedAt": state.get("finishedAt"),
                    "failure": {
                        "code": "startup_validation_failed" if state.get("state") == "failed" else None,
                        "summary": state.get("error") or None,
                        "detail": state.get("error") or None,
                        "affectedObjectCount": None,
                        "staleRisk": False,
                    },
                }
            )
        elif task_id == "service.local_http":
            overlay.update({"liveState": "running", "stateBasis": [{"kind": "current_http_process", "value": os.getpid(), "authoritative": True}]})
        elif task_id == "desktop.wpf_host":
            overlay.update({"liveState": "unknown", "stateBasis": [{"kind": "desktop_lifecycle", "value": "owned_by_wpf_host", "authoritative": True}]})
        elif task_id == "gate0.backfill.disabled":
            row = next((item for item in windows if item["taskName"] == "PenguinConvexity-Gate0-Backfill"), None)
            overlay.update(
                {
                    "liveState": "disabled",
                    "stateBasis": [{"kind": "windows_scheduled_task", "value": row or "not_observed", "authoritative": True}, {"kind": "frozen_policy", "value": "read_only_no_enable_no_run", "authoritative": True}],
                }
            )

        controls, disabled_controls = _task_control_specs(task_id, overlay["liveState"], entry)
        if task_id == "candidate.daily_incremental":
            partitions = list(overlay.get("partitions") or [])
            incomplete = [row for row in partitions if row.get("state") != "completed"]
            retriable = [row for row in incomplete if row.get("state") in {"failed", "paused", "retrying"}]
            kept_controls = []
            for control in controls:
                if control["action"] == "resume_checkpoint" and not incomplete:
                    disabled_controls.append({**control, "reason": "当前没有未完成的候选分片检查点。"})
                elif control["action"] == "retry_partition" and not retriable:
                    disabled_controls.append({**control, "reason": "当前没有可重试的失败或暂停分片。"})
                else:
                    kept_controls.append(control)
            controls = kept_controls
        if task_id == "candidate.history_backlog":
            partitions = list(overlay.get("partitions") or [])
            incomplete = [row for row in partitions if row.get("state") != "completed"]
            failed = [row for row in incomplete if row.get("state") in {"failed", "paused", "retrying"}]
            authorized = bool(overlay.get("formalHistoricalScanAuthorized"))
            if authorized and incomplete:
                controls.append({"action": "resume_checkpoint", "label": "从检查点恢复", "requiresPreview": True})
                if failed:
                    controls.append({"action": "retry_partition", "label": "重试失败分片", "requiresPreview": True})
            else:
                reason = "历史候选已完成，没有可恢复分片。" if not incomplete else "原历史扫描授权无效，不能恢复。"
                disabled_controls.extend(
                    [
                        {"action": "resume_checkpoint", "label": "从检查点恢复", "requiresPreview": True, "reason": reason},
                        {"action": "retry_partition", "label": "重试失败分片", "requiresPreview": True, "reason": reason},
                    ]
                )
        if task_id in {"c22.screening", "c22.convexity_tracking"} and overlay["liveState"] not in {"stale", "unknown"}:
            rollback = self._latest_rollback_candidate(task_id)
            if rollback:
                controls.append(
                    {
                        "action": "rollback_control_change",
                        "label": "回滚上次频率 / 暂停变更",
                        "requiresPreview": True,
                        "parameters": {"auditId": rollback["auditId"]},
                        "rollbackTarget": rollback,
                    }
                )
        if not controls and not disabled_controls and entry.get("controlPolicy") not in {None, ""}:
            disabled_controls.append({"action": "direct_control", "label": "直接控制", "reason": str(entry.get("controlPolicy"))})
        affected_pages = {
            "c22.screening": ["new-token-update.html", "candidate-production.html", "candidate-pool.html"],
            "c22.convexity_tracking": ["update-center.html", "candidate-pool.html", "change-explanations.html"],
            "candidate.daily_incremental": ["candidate-production.html", "new-token-update.html"],
            "candidate.history_backlog": ["candidate-production.html"],
            "maintenance.temp_artifact_retention": ["maintenance-jobs.html"],
        }.get(task_id, ["task-detail.html"])
        task = {
            "taskId": task_id,
            "displayName": entry.get("displayName", task_id),
            "machineNames": list(entry.get("machineNames") or []),
            "entryKind": entry.get("entryKind", "unknown"),
            "lifecycleClass": entry.get("lifecycleClass", "unknown"),
            "liveState": overlay["liveState"],
            "stateBasis": overlay["stateBasis"],
            "capabilityBoundary": entry.get("controlPolicy", "details_only"),
            "triggerModes": list(entry.get("entryPoints") or []),
            "schedule": overlay.get("schedule") or {"mode": "not_applicable"},
            "lastStartedAt": overlay.get("lastStartedAt"),
            "lastFinishedAt": overlay.get("lastFinishedAt"),
            "nextDueAt": overlay.get("nextDueAt"),
            "schedulerNextTriggerAt": overlay.get("schedulerNextTriggerAt") or scheduler_next,
            "progress": overlay["progress"],
            "lastHeartbeatAt": overlay.get("lastHeartbeatAt"),
            "checkpoint": overlay.get("checkpoint"),
            "chains": list(entry.get("chains") or []),
            "sources": list(overlay.get("sources") or []),
            "inputs": list(entry.get("inputs") or []),
            "outputs": list(entry.get("outputs") or []),
            "upstreamTaskIds": list(entry.get("upstreamTaskIds") or []),
            "downstreamTaskIds": list(entry.get("downstreamTaskIds") or []),
            "affectedPages": affected_pages,
            "failure": overlay["failure"],
            "controls": controls,
            "disabledControls": disabled_controls,
            "logs": [entry.get("statusPath")] if entry.get("statusPath") else [],
            "auditUrl": f"run-audit.html?taskId={task_id}",
            "observedAt": iso_time(now),
        }
        for key in ("partitions", "formalHistoricalScanAuthorized", "downstreamResolved", "machineAction", "lastTaskResult"):
            if key in overlay:
                task[key] = overlay[key]
        task["stateVersion"] = stable_digest(
            {
                "taskId": task_id,
                "liveState": task["liveState"],
                "schedule": task["schedule"],
                "heartbeat": task["lastHeartbeatAt"],
                "checkpoint": task["checkpoint"],
                "failure": task["failure"],
            }
        )
        return task

    def tasks_payload(self) -> dict[str, Any]:
        now = self.clock()
        bundle = self._runtime_bundle()
        windows = self._windows_bundle()
        entries = list(self.inventory.get("entries") or [])
        tasks = [self._manager_task(entry, bundle=bundle, windows=windows, now=now) for entry in entries]
        for definition in self.inventory.get("updateTaskCatalog") or []:
            child_entry = {
                "taskId": definition["taskId"],
                "displayName": definition.get("displayName", definition["taskId"]),
                "entryKind": "update_task_catalog_child",
                "lifecycleClass": definition.get("classification", "manual_on_demand"),
                "controlPolicy": definition.get("controlPolicy", "details_only"),
                "machineNames": [f"TASK_DEFINITIONS[{definition['taskId']}]"],
                "entryPoints": ["/api/update-task", "scripts/run_update_task.py"],
            }
            tasks.append(self._manager_task(child_entry, bundle=bundle, windows=windows, now=now))
        task_ids = [row["taskId"] for row in tasks]
        registered_post = {row["path"] for row in self.supplement.get("postEndpointMappings") or []}
        observed_post = _extract_post_paths(self.project_root / "scripts" / "serve_local.py")
        inherited_observed = {path for path in observed_post if not path.startswith("/api/c2.5/")}
        expected_windows = {
            name
            for entry in entries
            if entry.get("entryKind") == "windows_scheduled_task"
            for name in entry.get("machineNames") or []
            if name.startswith("Penguin")
        }
        observed_windows = {row["taskName"] for row in windows}
        counts = Counter(task_ids)
        reconciliation = {
            "registeredTopLevelEntryCount": len(entries),
            "registeredUpdateTaskCatalogCount": len(self.inventory.get("updateTaskCatalog") or []),
            "observedInheritedPostEndpointCount": len(inherited_observed),
            "registeredInheritedPostEndpointCount": len(registered_post),
            "managedControlPostEndpoints": sorted(path for path in observed_post if path.startswith("/api/c2.5/")),
            "missingPostEndpoints": sorted(registered_post - inherited_observed),
            "unregisteredPostEndpoints": sorted(inherited_observed - registered_post),
            "duplicateTaskIds": sorted(task_id for task_id, count in counts.items() if count > 1),
            "missingWindowsEntries": sorted(observed_windows - expected_windows),
            "notInstalledExpectedWindowsEntries": sorted(expected_windows - observed_windows),
            "approvedDeletionCount": int(self.inheritance.get("approvedDeletionCount") or 0),
        }
        reconciliation["blocking"] = bool(
            reconciliation["missingPostEndpoints"]
            or reconciliation["unregisteredPostEndpoints"]
            or reconciliation["duplicateTaskIds"]
            or reconciliation["missingWindowsEntries"]
        )
        return {
            "schemaVersion": "c2.5-task-ledger-v1",
            "tasks": tasks,
            "reconciliation": reconciliation,
            "filters": {
                "lifecycleClasses": sorted({row["lifecycleClass"] for row in tasks}),
                "liveStates": sorted({row["liveState"] for row in tasks}),
                "entryKinds": sorted({row["entryKind"] for row in tasks}),
            },
            "observedAt": iso_time(now),
        }

    def task_payload(self, task_id: str) -> dict[str, Any]:
        ledger = self.tasks_payload()
        task = next((row for row in ledger["tasks"] if row["taskId"] == task_id), None)
        if task is None:
            return {"schemaVersion": "c2.5-task-detail-v1", "status": "not_found", "taskId": task_id, "observedAt": ledger["observedAt"]}
        runs = [row for row in self.runs_audit_payload()["runs"] if row.get("taskId") == task_id]
        return {
            "schemaVersion": "c2.5-task-detail-v1",
            "status": "ready",
            "task": task,
            "runHistory": runs,
            "sourceBreakdown": task["sources"],
            "chainBreakdown": task["chains"],
            "controlPreviewUrl": "/api/c2.5/control/preview",
            "observedAt": ledger["observedAt"],
        }

    def _load_c24_admin(self) -> dict[str, Any]:
        path = self.app_root / "c2-4-admin-snapshot.js"
        try:
            return load_js_payload(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def chains_sources_payload(self) -> dict[str, Any]:
        now = self.clock()
        data = self._load_c24_admin()
        chain_funnel = data.get("chainFunnel") or {}
        rows = []
        for chain_id in CHAIN_ORDER:
            counts = chain_funnel.get(chain_id) if isinstance(chain_funnel.get(chain_id), dict) else {}
            object_count = int(counts.get("tracking") or counts.get("public") or 0)
            rows.append(
                {
                    "chainId": chain_id,
                    "chainLabel": CHAIN_LABELS[chain_id],
                    "sourceId": "chain-aggregate",
                    "applicable": True,
                    "status": "success" if object_count > 0 else "no_data",
                    "lastAttemptAt": data.get("generatedAt"),
                    "lastSuccessAt": data.get("generatedAt") if object_count > 0 else None,
                    "dataAsOf": data.get("dataCutoffAt"),
                    "lagSeconds": max(0, int((now - parse_time(data.get("dataCutoffAt"))).total_seconds())) if parse_time(data.get("dataCutoffAt")) else None,
                    "cursor": None,
                    "checkpoint": None,
                    "discovered": counts.get("firstGateQueue", 0),
                    "accepted": counts.get("tracking", 0),
                    "rejected": None,
                    "deduplicated": None,
                    "failed": None,
                    "affectedObjects": object_count,
                    "nextRetryAt": None,
                    "retryCapability": "task_registered_sources_only",
                    "reasonCode": "real_zero" if object_count == 0 else "healthy",
                    "plainReason": "当前完整快照中该链没有结果。" if object_count == 0 else "当前完整快照中存在真实对象。",
                }
            )
        for source in data.get("sourceHealth") or []:
            source_id = str(source.get("source_id") or source.get("sourceId") or "unknown")
            chain_id = source.get("chain_id") or source.get("chainId") or "all_supported"
            affected = source.get("affected_count", source.get("affectedObjects", 0))
            rows.append(
                {
                    "chainId": chain_id,
                    "chainLabel": CHAIN_LABELS.get(chain_id, "全部适用链" if chain_id == "all_supported" else chain_id),
                    "sourceId": source_id,
                    "applicable": True,
                    "status": normalize_source_state(source.get("status")),
                    "lastAttemptAt": source.get("updated_at"),
                    "lastSuccessAt": source.get("last_success_at"),
                    "dataAsOf": data.get("dataCutoffAt"),
                    "lagSeconds": None,
                    "cursor": source.get("cursor"),
                    "checkpoint": source.get("checkpoint"),
                    "discovered": None,
                    "accepted": None,
                    "rejected": None,
                    "deduplicated": None,
                    "failed": affected if normalize_source_state(source.get("status")) in FAILURE_STATES else 0,
                    "affectedObjects": affected,
                    "nextRetryAt": source.get("next_retry_at"),
                    "retryCapability": "through_registered_task_only",
                    "reasonCode": source.get("reason_code"),
                    "plainReason": source.get("plain_reason") or "来源没有提供进一步说明。",
                }
            )
        return {
            "schemaVersion": "c2.5-chain-source-health-v1",
            "chainOrder": list(CHAIN_ORDER),
            "chainLabels": CHAIN_LABELS,
            "rows": rows,
            "summary": dict(Counter(row["status"] for row in rows)),
            "scopePolicy": "只按记录中的真实适用范围聚合；全局来源单列，不把失败自动复制到无适用对象的链。",
            "observedAt": iso_time(now),
            "dataAsOf": data.get("dataCutoffAt"),
        }

    def _tracking_items(self) -> list[dict[str, Any]]:
        return self._tracking_rule_sample()["items"]

    def _tracking_rule_sample(self) -> dict[str, Any]:
        for name in ("c2-4-tracking-snapshot.js", "c2-4-front-snapshot.js"):
            path = self.app_root / name
            try:
                data = load_js_payload(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            items = data.get("items")
            if isinstance(items, list):
                return {
                    "sampleKind": "current_read_only",
                    "sourcePath": f"app/{name}",
                    "sourceSha256": canonical_sha256(path),
                    "snapshotId": data.get("buildId") or name,
                    "dataAsOf": data.get("dataCutoffAt") or data.get("sourceCutoffAt") or data.get("generatedAt"),
                    "readOnly": True,
                    "items": [item for item in items if isinstance(item, dict)],
                }
        return {
            "sampleKind": "current_read_only",
            "sourcePath": "app/c2-4-tracking-snapshot.js",
            "sourceSha256": None,
            "snapshotId": None,
            "dataAsOf": None,
            "readOnly": True,
            "items": [],
            "unavailableReason": "当前完整跟踪快照不可用",
        }

    def _fixed_rule_sample(self) -> dict[str, Any]:
        path = self.project_root / "fixtures" / "c2.5" / "rule-transparency-matrix.json"
        if not path.is_file():
            path = PROJECT_ROOT / "fixtures" / "c2.5" / "rule-transparency-matrix.json"
        fixture = load_json(path, {})
        items = fixture.get("items") if isinstance(fixture.get("items"), list) else []
        return {
            "sampleKind": "fixed_historical",
            "sourcePath": "fixtures/c2.5/rule-transparency-matrix.json",
            "sourceSha256": canonical_sha256(path) if path.is_file() else None,
            "snapshotId": fixture.get("schemaVersion"),
            "readOnly": True,
            "items": [item for item in items if isinstance(item, dict)],
            **({"unavailableReason": "固定历史规则样本不可用"} if not items else {}),
        }

    def _rule_affected_snapshots(self) -> list[dict[str, Any]]:
        rows = []
        for logical_id, name, producer in (
            ("c24-tracking", "c2-4-tracking-snapshot.js", "c22.convexity_tracking"),
            ("c24-front", "c2-4-front-snapshot.js", "c22.convexity_tracking"),
            ("c24-admin", "c2-4-admin-snapshot.js", "c22.convexity_tracking"),
        ):
            path = self.app_root / name
            try:
                data = load_js_payload(path)
                rows.append(
                    {
                        "logicalSnapshotId": logical_id,
                        "snapshotId": data.get("buildId") or logical_id,
                        "path": f"app/{name}",
                        "producerTaskId": producer,
                        "builtAt": data.get("generatedAt") or data.get("builtAt"),
                        "complete": bool(data.get("isComplete", True)),
                    }
                )
            except (OSError, ValueError, json.JSONDecodeError) as error:
                rows.append(
                    {
                        "logicalSnapshotId": logical_id,
                        "snapshotId": logical_id,
                        "path": f"app/{name}",
                        "producerTaskId": producer,
                        "builtAt": None,
                        "complete": False,
                        "unavailableReason": str(error),
                    }
                )
        return rows

    def rule_change_preview(self, target_version: str) -> dict[str, Any]:
        governance = self.rule_governance.state()
        current = self._tracking_rule_sample()
        fixed = self._fixed_rule_sample()
        replay = build_dual_replay_evidence(
            current["items"],
            source_version=governance["activeVersion"],
            target_version=target_version,
            current_source={key: value for key, value in current.items() if key != "items"},
            fixed_history=fixed,
        )
        replay["affectedTaskIds"] = ["c22.screening", "c22.convexity_tracking"]
        replay["affectedSnapshots"] = self._rule_affected_snapshots()
        return replay

    def rules_payload(self) -> dict[str, Any]:
        governance = self.rule_governance.state()
        current = self._tracking_rule_sample()
        return build_rule_transparency(
            current["items"],
            rule_path=self.project_root / "docs" / "C2.4_RULE_CONFIG.json",
            trial_path=self.project_root / "docs" / "C2.4_RULE_RELAXATION_TRIAL_20260813.json",
            active_version=governance["activeVersion"],
            governance=governance,
            current_source={key: value for key, value in current.items() if key != "items"},
            fixed_history=self._fixed_rule_sample(),
            now=self.clock(),
        )

    def decision_trace_payload(self, asset_id: str) -> dict[str, Any]:
        now = self.clock()
        asset_id = str(asset_id or "").strip()
        if not asset_id:
            return {"schemaVersion": "c2.5-decision-trace-v1", "status": "invalid_request", "error": "assetId不能为空", "observedAt": iso_time(now)}
        records = []
        snapshot_refs = []
        for name in ("c2-4-candidate-snapshot.js", "c2-4-tracking-snapshot.js", "c2-4-front-snapshot.js"):
            path = self.app_root / name
            try:
                data = load_js_payload(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            matches = [row for row in data.get("items", []) if isinstance(row, dict) and str(row.get("assetId") or row.get("asset_id") or "") == asset_id]
            if matches:
                records.extend({"snapshot": name, "record": row} for row in matches)
                snapshot_refs.append({"snapshotId": data.get("buildId") or name, "path": f"app/{name}", "builtAt": data.get("generatedAt"), "dataAsOf": data.get("dataCutoffAt") or data.get("sourceCutoffAt")})
        if not records:
            return {"schemaVersion": "c2.5-decision-trace-v1", "status": "not_found", "assetId": asset_id, "records": [], "observedAt": iso_time(now)}
        merged: dict[str, Any] = {}
        for item in records:
            merged.update(item["record"])
        governance = self.rule_governance.state()
        rule_payload = build_rule_transparency([merged], rule_path=self.project_root / "docs" / "C2.4_RULE_CONFIG.json", trial_path=self.project_root / "docs" / "C2.4_RULE_RELAXATION_TRIAL_20260813.json", active_version=governance["activeVersion"], governance=governance, now=now)
        replay_row = (rule_payload.get("replay") or {}).get("rows", [{}])[0]
        evidence = merged.get("evidence") or merged.get("evidenceIds") or merged.get("keyEvidence") or []
        if not isinstance(evidence, list):
            evidence = [evidence]
        return {
            "schemaVersion": "c2.5-decision-trace-v1",
            "status": "ready",
            "assetId": asset_id,
            "identity": {key: merged.get(key) for key in ("canonicalName", "symbol", "chainId", "contractAddress", "pairAddress", "relationshipClass")},
            "t0": {key: merged.get(key) for key in ("t0", "t0Status", "ageDays", "lifecyclePool")},
            "evidence": evidence,
            "dataTimes": {"dataAsOf": merged.get("dataCutoffAt") or merged.get("evaluationCompletedAt"), "evaluationWindowId": merged.get("evaluationWindowId"), "pageReadAt": iso_time(now)},
            "ruleInputs": merged,
            "ruleResults": replay_row,
            "waitOrFailureReasons": merged.get("blockers") or merged.get("whyNot") or [],
            "activeOverride": rule_payload.get("activeOverride"),
            "businessState": {"trackingState": merged.get("trackingState"), "publicState": merged.get("publicState"), "publicEligible": merged.get("publicEligible")},
            "snapshotRefs": snapshot_refs,
            "frontendVisibility": bool(merged.get("publicEligible")),
            "ranking": {key: merged.get(key) for key in ("bayesPosterior", "bayesRankWithinChain", "rankingAvailable", "independentConfidence", "observedMetricCount")},
            "sourceLinks": merged.get("sourceLinks") or [],
            "path": ["输入证据", "数据时间", "规则检查", "结果理由", "当前状态", "下游快照", "前台显示"],
            "observedAt": iso_time(now),
        }

    def _database_integrity(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"path": str(path), "available": False, "quickCheck": None, "foreignKeyViolations": None, "readOnly": True}
        try:
            connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
            connection.execute("PRAGMA query_only=ON")
            try:
                quick = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
                foreign = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            finally:
                connection.close()
            return {"path": str(path), "available": True, "quickCheck": quick, "foreignKeyViolations": foreign, "readOnly": True}
        except sqlite3.Error as error:
            return {"path": str(path), "available": True, "quickCheck": "error", "foreignKeyViolations": None, "readOnly": True, "error": str(error)}

    def snapshots_payload(self) -> dict[str, Any]:
        now = self.clock()
        specs = [
            ("c24-candidates", "c2-4-candidate-snapshot.js", "c22.screening", ["c22.convexity_tracking"], ["new-token-update.html"]),
            ("c24-tracking", "c2-4-tracking-snapshot.js", "c22.convexity_tracking", ["c24-front", "c24-admin"], ["update-center.html", "decision-trace.html"]),
            ("c24-front", "c2-4-front-snapshot.js", "c22.convexity_tracking", [], ["candidate-pool.html", "change-explanations.html", "project-detail.html"]),
            ("c24-admin", "c2-4-admin-snapshot.js", "c22.convexity_tracking", ["c25-control-plane"], ["workbench.html", "chain-source-health.html"]),
            ("c22-admin", "c2-2-admin-snapshot.js", "c22.convexity_tracking", ["c24-admin"], ["update-center.html"]),
        ]
        snapshots = []
        for snapshot_id, name, producer, consumers, pages in specs:
            path = self.app_root / name
            try:
                data = load_js_payload(path)
                file_hash = canonical_sha256(path)
                items = data.get("items") if isinstance(data.get("items"), list) else []
                asset_ids = sorted({str(row.get("assetId") or row.get("asset_id")) for row in items if isinstance(row, dict) and (row.get("assetId") or row.get("asset_id"))})
                built = data.get("generatedAt") or data.get("builtAt")
                parsed = parse_time(built)
                stale = bool(parsed and now - parsed > timedelta(hours=48))
                complete = bool(data.get("isComplete", True))
                snapshots.append(
                    {
                        "snapshotId": data.get("buildId") or snapshot_id,
                        "schemaVersion": data.get("schemaVersion"),
                        "producerTaskId": producer,
                        "builtAt": built,
                        "dataAsOf": data.get("dataCutoffAt") or data.get("sourceCutoffAt"),
                        "atomic": True,
                        "complete": complete,
                        "stale": stale,
                        "objectCount": len(items),
                        "assetIdDigest": stable_digest(asset_ids),
                        "validation": {"format": "passed", "contentSha256": file_hash, "assetIdUnique": len(asset_ids) == len(items) if items else True},
                        "consumerTaskIds": consumers,
                        "consumerPages": pages,
                        "lastSuccessfulHandoff": built if complete else None,
                        "lastFailedHandoff": None,
                        "lifecycleStateField": "lifecyclePool" if items else None,
                        "convexityTrackingStateField": "trackingState" if items else None,
                        "path": f"app/{name}",
                    }
                )
            except (OSError, ValueError, json.JSONDecodeError) as error:
                snapshots.append(
                    {
                        "snapshotId": snapshot_id,
                        "schemaVersion": None,
                        "producerTaskId": producer,
                        "builtAt": None,
                        "dataAsOf": None,
                        "atomic": True,
                        "complete": False,
                        "stale": True,
                        "objectCount": None,
                        "assetIdDigest": None,
                        "validation": {"format": "failed", "error": str(error)},
                        "consumerTaskIds": consumers,
                        "consumerPages": pages,
                        "lastSuccessfulHandoff": None,
                        "lastFailedHandoff": iso_time(now),
                        "path": f"app/{name}",
                        "preservePreviousCompleteSnapshot": True,
                    }
                )
        return {
            "schemaVersion": "c2.5-snapshot-handoffs-v1",
            "snapshots": snapshots,
            "databases": [self._database_integrity(self.data_root / "convexity.db"), self._database_integrity(self.data_root / "c2.1-pipeline.db")],
            "stateBoundary": {"lifecycle": "lifecyclePool/lifecycleState", "convexityTracking": "trackingState", "separate": True},
            "managerCompositionWritesBusinessDatabases": False,
            "observedAt": iso_time(now),
        }

    def _read_audit(self) -> list[dict[str, Any]]:
        path = self.runtime_root / "c2.5" / "management-audit.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-200:]
        except OSError:
            return []
        rows = []
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def runs_audit_payload(self) -> dict[str, Any]:
        now = self.clock()
        data = self._load_c24_admin()
        stage_task = {"screening": "c22.screening", "convexity_tracking": "c22.convexity_tracking"}
        runs = []
        for row in data.get("recentRuns") or []:
            runs.append(
                {
                    "runId": row.get("run_id"),
                    "taskId": stage_task.get(row.get("stage"), row.get("stage")),
                    "trigger": row.get("trigger") or "unknown",
                    "startedAt": row.get("started_at"),
                    "finishedAt": row.get("finished_at"),
                    "finalState": row.get("state"),
                    "processed": row.get("completed_units"),
                    "checkpoint": row.get("current_item"),
                    "artifacts": [],
                    "error": {"code": row.get("error_code"), "detail": row.get("error_detail")},
                    "recoveryOf": row.get("recovery_of"),
                    "legacy": False,
                    "sourceVersion": "C2.4",
                    "stale": False,
                }
            )
        for version, path in (("C1.8", self.runtime_root / "c1.8" / "scheduler-state.json"), ("C2.1", self.runtime_root / "c2.1" / "scheduler-state.json")):
            row = load_json(path, {})
            if row:
                runs.append(
                    {
                        "runId": f"legacy-{version.lower()}-{row.get('lastStartedAt') or 'unknown'}",
                        "taskId": "c18.scheduler.legacy" if version == "C1.8" else "c21.pipeline.legacy",
                        "trigger": row.get("lastTrigger") or "legacy",
                        "startedAt": row.get("lastStartedAt"),
                        "finishedAt": row.get("lastFinishedAt"),
                        "finalState": row.get("lastStatus") or "unknown",
                        "processed": None,
                        "checkpoint": None,
                        "artifacts": [],
                        "error": {"code": None, "detail": row.get("lastError")},
                        "recoveryOf": None,
                        "legacy": True,
                        "sourceVersion": version,
                        "stale": True,
                    }
                )
        return {
            "schemaVersion": "c2.5-runs-audit-v1",
            "runs": runs,
            "managementAudit": self._read_audit(),
            "separation": "运行记录来自既有作业事实；管理审计只记录操作，不参与liveState组合。",
            "observedAt": iso_time(now),
        }

    def control_plane_payload(self) -> dict[str, Any]:
        ledger = self.tasks_payload()
        tasks = ledger["tasks"]
        rules = self.rules_payload()
        chain_sources = self.chains_sources_payload()
        snapshots = self.snapshots_payload()
        runs_audit = self.runs_audit_payload()
        by_lifecycle = Counter(row["lifecycleClass"] for row in tasks)
        by_state = Counter(row["liveState"] for row in tasks)
        scheduler = next((row for row in tasks if row["taskId"] == "windows.scheduler.primary"), None)
        selected = {
            task_id: next((row for row in tasks if row["taskId"] == task_id), None)
            for task_id in ("c22.screening", "c22.convexity_tracking", "candidate.daily_incremental", "maintenance.temp_artifact_retention")
        }
        complete_snapshots = [row for row in snapshots["snapshots"] if row["complete"]]
        incidents = [
            {"taskId": row["taskId"], "state": row["liveState"], "failure": row["failure"]}
            for row in tasks
            if row["liveState"] in {"failed", "stale", "blocked", "partial"}
        ]
        decision_items = [
            {"kind": "state_reconciliation", "message": "任务登记或现场入口存在差异。", "severity": "blocking"}
            for _ in [0]
            if ledger["reconciliation"]["blocking"]
        ]
        if rules.get("status") != "ready":
            decision_items.append({"kind": "rule_reconciliation", "message": "规则基线、活动覆盖或代码有效值需要对账。", "severity": "attention"})
        return {
            "schemaVersion": "c2.5-control-plane-v1",
            "product": "企鹅投研-凸性",
            "managerPriority": "管理者知情权第一优先",
            "systemStatus": "blocked" if ledger["reconciliation"]["blocking"] else "attention" if incidents or decision_items else "healthy",
            "taskCountsByLifecycleClass": dict(by_lifecycle),
            "taskCountsByLiveState": dict(by_state),
            "windowsScheduler": scheduler,
            "summaries": selected,
            "latestCompleteSuccess": max((row["lastFinishedAt"] for row in tasks if row["liveState"] == "completed" and row.get("lastFinishedAt")), default=None),
            "latestBusinessSnapshot": max((row["builtAt"] for row in complete_snapshots if row.get("builtAt")), default=None),
            "pageReadAt": ledger["observedAt"],
            "chainSourceSummary": chain_sources["summary"],
            "currentRuleVersion": (rules.get("effective") or {}).get("ruleVersion"),
            "activeOverrideCount": 1 if (rules.get("activeOverride") or {}).get("active") else 0,
            "expiringOverrideCount": 0,
            "recentIncidents": incidents[:12],
            "decisionItems": decision_items,
            "recentManagementActions": runs_audit["managementAudit"][-10:],
            "nextPlannedRuns": [
                {"taskId": row["taskId"], "nextDueAt": row["nextDueAt"], "schedulerNextTriggerAt": row["schedulerNextTriggerAt"]}
                for row in tasks
                if row.get("nextDueAt") or row.get("schedulerNextTriggerAt")
            ],
            "reconciliation": ledger["reconciliation"],
            "links": {
                "tasks": "task-ledger.html",
                "chainsSources": "chain-source-health.html",
                "rules": "rule-transparency.html",
                "snapshots": "snapshot-handoffs.html",
                "audit": "run-audit.html",
            },
        }


__all__ = [
    "C25ControlPlane",
    "compose_authoritative_job_state",
    "normalize_live_state",
    "progress_payload",
]
