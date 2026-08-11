#!/usr/bin/env python3
"""Runtime-only progress telemetry for C1.9.

This module writes only update status metadata. It never changes the database,
candidate records, scoring, action labels, or any research rule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from threading import RLock

from update_watchdog import DEFAULT_STATUS_PATH, load_update_status, save_update_status


_PROGRESS_LOCK = RLock()


def locked_progress(operation):
    @wraps(operation)
    def wrapper(*args, **kwargs):
        with _PROGRESS_LOCK:
            return operation(*args, **kwargs)

    return wrapper


STAGES = [
    (1, "市场与退出数据", {"market", "formal_market_exit"}),
    (2, "项目与证据发现", {"high_value_evidence", "source_discovery", "evidence", "discovery"}),
    (3, "合约与身份归属", {"contracts", "identity", "project_asset_identity"}),
    (4, "项目档案与研究材料", {"profile_enrichment", "formal_research_materials"}),
    (5, "监控与数据健康", {"monitoring_infrastructure", "weak_signals", "data_backbone"}),
    (6, "研究结论与催化", {"machine_research_scoring", "machine_conclusion", "catalyst_trade_path"}),
    (7, "跟踪与页面发布", {"tracking", "page_snapshot_rebuild"}),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stage_for(component: str) -> tuple[int, str]:
    for index, label, components in STAGES:
        if component in components:
            return index, label
    return 1, "正在准备"


def _elapsed_seconds(started_at: str | None) -> int | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        return max(0, round((datetime.now(timezone.utc) - started).total_seconds()))
    except (TypeError, ValueError):
        return None


@locked_progress
def begin_progress(task_id: str, task_label: str, component_total: int, status_path=DEFAULT_STATUS_PATH):
    status = load_update_status(status_path)
    already_active = (
        status.get("state") == "running"
        and bool(status.get("active"))
        and status.get("taskId") == task_id
    )
    started_at = status.get("startedAt") if already_active else utc_now()
    run_token = status.get("runToken") if already_active else started_at
    heartbeat_at = utc_now()
    total = max(0, int(component_total or 0))
    elapsed = _elapsed_seconds(started_at)
    status.update(
        state="running",
        active=True,
        taskId=task_id,
        taskLabel=task_label,
        runToken=run_token,
        runId=status.get("runId", "") if already_active else "",
        message="任务正在后台运行，离开页面不会中断。",
        startedAt=started_at,
        finishedAt=None,
        recoveryAvailable=False,
        recoveryTaskId="",
        recoveryRunId="",
        progressState="running",
        progressStageIndex=1,
        progressStageTotal=len(STAGES),
        progressStageLabel="准备开始",
        progressComponentIndex=0,
        progressComponentTotal=total,
        progressCompletedComponents=0,
        progressCurrentItem="正在准备任务",
        progressStartedAt=started_at,
        progressHeartbeatAt=heartbeat_at,
        progressElapsedSeconds=elapsed,
        progressEtaSeconds=None,
        progressTaskId=task_id,
        progressTaskLabel=task_label,
        stage="准备开始",
        stageIndex=1,
        stageTotal=len(STAGES),
        completedItems=0,
        totalItems=total,
        successCount=0,
        failedCount=0,
        waitingCount=total,
        currentItem="正在准备任务",
        lastHeartbeatAt=heartbeat_at,
        elapsed=elapsed,
        estimatedRemaining=None,
    )
    return save_update_status(status, status_path)


@locked_progress
def update_progress(component: str, current_item: str, component_index: int, component_total: int, status_path=DEFAULT_STATUS_PATH):
    status = load_update_status(status_path)
    incoming_stage_index, incoming_stage_label = stage_for(component)
    previous_stage_index = int(status.get("progressStageIndex") or 1)
    stage_index = max(previous_stage_index, incoming_stage_index)
    stage_label = (
        incoming_stage_label
        if incoming_stage_index >= previous_stage_index
        else status.get("progressStageLabel") or incoming_stage_label
    )
    incoming_completed = max(0, min(int(component_index) - 1, int(component_total or 0)))
    completed = max(
        int(status.get("progressCompletedComponents") or 0),
        incoming_completed,
    )
    total = max(
        int(status.get("progressComponentTotal") or 0),
        int(component_total or 0),
    )
    heartbeat_at = utc_now()
    elapsed = _elapsed_seconds(status.get("progressStartedAt") or status.get("startedAt"))
    status.update(
        progressState="running",
        progressStageIndex=stage_index,
        progressStageTotal=len(STAGES),
        progressStageLabel=stage_label,
        progressComponentIndex=max(
            int(status.get("progressComponentIndex") or 0),
            int(component_index),
        ),
        progressComponentTotal=total,
        progressCompletedComponents=completed,
        progressCurrentItem=current_item or stage_label,
        progressHeartbeatAt=heartbeat_at,
        progressElapsedSeconds=elapsed,
        progressEtaSeconds=None,
        stage=stage_label,
        stageIndex=stage_index,
        stageTotal=len(STAGES),
        completedItems=completed,
        totalItems=total,
        successCount=completed,
        failedCount=0,
        waitingCount=max(0, total - completed),
        currentItem=current_item or stage_label,
        lastHeartbeatAt=heartbeat_at,
        elapsed=elapsed,
        estimatedRemaining=None,
    )
    return save_update_status(status, status_path)


@locked_progress
def heartbeat_progress(status_path=DEFAULT_STATUS_PATH):
    status = load_update_status(status_path)
    if status.get("state") != "running" or not status.get("active"):
        return status
    heartbeat_at = utc_now()
    elapsed = _elapsed_seconds(status.get("progressStartedAt") or status.get("startedAt"))
    status.update(
        progressHeartbeatAt=heartbeat_at,
        progressElapsedSeconds=elapsed,
        lastHeartbeatAt=heartbeat_at,
        elapsed=elapsed,
    )
    return save_update_status(status, status_path)


@locked_progress
def finish_progress(
    state: str,
    message: str,
    status_path=DEFAULT_STATUS_PATH,
    *,
    success_count: int | None = None,
    failed_count: int | None = None,
    waiting_count: int = 0,
    total_items: int | None = None,
):
    status = load_update_status(status_path)
    total = int(total_items if total_items is not None else status.get("progressComponentTotal") or 0)
    completed = total if state == "success" else int(status.get("progressCompletedComponents") or 0)
    successes = completed if success_count is None else max(0, int(success_count))
    failures = (0 if state == "success" else (1 if state == "failed" else 0)) if failed_count is None else max(0, int(failed_count))
    waiting = max(0, int(waiting_count or 0))
    heartbeat_at = utc_now()
    elapsed = _elapsed_seconds(status.get("progressStartedAt") or status.get("startedAt"))
    stage_index = len(STAGES) if state == "success" else int(status.get("progressStageIndex") or 1)
    stage_label = "已完成" if state == "success" else status.get("progressStageLabel") or "任务结束"
    status.update(
        state=state,
        active=False,
        message=message or ("更新完成" if state == "success" else "任务已停止"),
        finishedAt=heartbeat_at,
        recoveryAvailable=state in {"partial_success", "failed"},
        recoveryTaskId=status.get("taskId", "") if state in {"partial_success", "failed"} else "",
        recoveryRunId=status.get("runId", "") if state in {"partial_success", "failed"} else "",
        progressState=state,
        progressStageIndex=stage_index,
        progressStageTotal=len(STAGES),
        progressStageLabel=stage_label,
        progressCompletedComponents=completed,
        progressCurrentItem=message or ("更新完成" if state == "success" else "任务已停止"),
        progressHeartbeatAt=heartbeat_at,
        progressElapsedSeconds=elapsed,
        progressEtaSeconds=None,
        stage=stage_label,
        stageIndex=stage_index,
        stageTotal=len(STAGES),
        completedItems=successes + failures,
        totalItems=total,
        successCount=successes,
        failedCount=failures,
        waitingCount=waiting,
        currentItem=message or ("更新完成" if state == "success" else "任务已停止"),
        lastHeartbeatAt=heartbeat_at,
        elapsed=elapsed,
        estimatedRemaining=None,
    )
    return save_update_status(status, status_path)
