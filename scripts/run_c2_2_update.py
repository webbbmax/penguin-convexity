#!/usr/bin/env python3
"""Single-process C2.2 orchestrator for screening and convexity tracking."""

from __future__ import annotations

import argparse
import json
import time
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from build_c2_2_snapshots import build_snapshots
from c2_2_runtime import (
    JOB_CODES,
    RUNTIME_ROOT,
    atomic_json,
    iso_time,
    is_due,
    load_json,
    load_state,
    next_run_at,
    pipeline_lock,
    pause_current_requested,
    save_job_status,
    save_state,
    status_payload,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
C21_FRONT_PATH = PROJECT_ROOT / "app" / "c2-1-front-snapshot.js"
C21_ADMIN_PATH = PROJECT_ROOT / "app" / "c2-1-admin-snapshot.js"
MAIN_DB_PATH = PROJECT_ROOT / "data" / "convexity.db"
C22_TRACKING_TASK_ID = "c2_2_convexity_tracking_refresh"
SCREENING_EVALUATION_BATCH_SIZE = 500
FIRST_GATE_RECHECK_BATCH_SIZE = 500
TRACKING_HANDOFF_BATCH_SIZE = 25
TRACKING_HANDOFF_MAX_BATCHES = 36
TRACKING_HANDOFF_TIME_BUDGET_SECONDS = 10 * 60
POST_BASELINE_STATE_PATH = RUNTIME_ROOT / "post-baseline-state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def set_status(job_code: str, *, state: str, stage: str, message: str, trigger: str, run_id: str, completed: int = 0, total: int = 0, error_code: str = "", error_detail: str = "", **extra) -> dict:
    payload = {
        "schemaVersion": "c2.2-job-status-v1",
        "jobCode": job_code,
        "runId": run_id,
        "trigger": trigger,
        "state": state,
        "stage": stage,
        "message": message,
        "progress": {"completed": int(completed), "total": int(total)},
        "lastHeartbeatAt": now_iso(),
        "lastCompletedAt": extra.pop("lastCompletedAt", None),
        "nextDueAt": extra.pop("nextDueAt", None),
        "checkpoint": extra.pop("checkpoint", None),
        "sourceFailures": extra.pop("sourceFailures", []),
        "objectFailures": extra.pop("objectFailures", []),
        "errorCode": error_code,
        "errorDetail": error_detail,
        **extra,
    }
    return save_job_status(payload)


def build_c22_snapshots() -> dict:
    # C2.2 consumes the C2.1 public contract as its screening input.  Candidate
    # tracking updates the database directly, so refresh that contract before
    # composing C2.2; otherwise newly evaluated projects stay invisible until
    # an unrelated screening job happens to run.
    from c2_1_db import open_pipeline_db
    from c2_1_pipeline import build_snapshots as build_c21_snapshots

    with closing(open_pipeline_db()) as connection:
        build_c21_snapshots(connection)
    payloads = build_snapshots(c21_front_path=C21_FRONT_PATH, c21_admin_path=C21_ADMIN_PATH, write=True)
    from build_c2_4_snapshots import build_snapshots as build_c24_snapshots

    payloads["c24"] = build_c24_snapshots(write=True)
    return payloads


def reconcile_c24_history() -> dict:
    """Make existing deep-tracking results eligible for durable C2.4 lifecycle migration."""

    from c2_1_db import open_pipeline_db
    from c2_4_tracking import reconcile_existing_tracking_history

    with closing(open_pipeline_db()) as connection:
        return reconcile_existing_tracking_history(connection)


def recheck_changed_first_gate_contracts() -> dict:
    """Refresh and complete changed first-gate contracts in the same cycle."""

    from c2_1_db import open_pipeline_db
    from candidate_production import (
        changed_first_gate_contract_candidate_ids,
        process_first_gate_candidates,
        refresh_production_contracts,
    )

    with closing(open_pipeline_db()) as connection:
        changed_ids = changed_first_gate_contract_candidate_ids(
            connection, limit=SCREENING_EVALUATION_BATCH_SIZE
        )
        refreshed = refresh_production_contracts(connection, changed_ids)
        rechecked = process_first_gate_candidates(
            connection, candidate_ids=changed_ids, refresh_market=False
        )
    return {
        "changed": len(changed_ids),
        "refreshed": len(refreshed),
        "firstGateRechecked": int(rechecked.get("evaluated") or 0),
    }


def drain_first_gate_backlog() -> dict:
    """Finish the materialized local first-gate queue before C2.4 publication."""

    from c2_1_db import open_pipeline_db
    from candidate_production import (
        pending_first_gate_candidate_ids,
        process_first_gate_candidates,
    )

    batches = processed = 0
    with closing(open_pipeline_db()) as connection:
        while True:
            candidate_ids = pending_first_gate_candidate_ids(
                connection, limit=FIRST_GATE_RECHECK_BATCH_SIZE
            )
            if not candidate_ids:
                break
            result = process_first_gate_candidates(
                connection, candidate_ids=candidate_ids, refresh_market=False
            )
            evaluated = int(result.get("evaluated") or 0)
            if evaluated != len(candidate_ids):
                raise RuntimeError(
                    f"第一关本地复核未完整推进：{evaluated}/{len(candidate_ids)}"
                )
            batches += 1
            processed += evaluated
    return {"batches": batches, "processed": processed, "remaining": 0}


def run_screening(trigger: str, run_id: str, dry_run: bool, source_id: str | None = None) -> dict:
    set_status("screening", state="running", stage="screening", message="正在单独更新筛选来源。" if source_id else "正在运行90天新币筛选。", trigger=trigger, run_id=run_id, total=6)
    if dry_run:
        snapshots = build_c22_snapshots()
        set_status("screening", state="completed", stage="snapshot_published", message="隔离自测完成，未写入筛选数据库。", trigger=trigger, run_id=run_id, completed=4, total=4, lastCompletedAt=now_iso(), nextDueAt=next_run_at("screening"))
        return {"status": "completed", "dryRun": True, "snapshot": snapshots["front"]["buildId"]}
    from candidate_production_runtime import pause_for_screening, resume_after_screening

    handoff = pause_for_screening()
    if handoff.get("status") == "timeout":
        set_status(
            "screening", state="paused", stage="waiting_for_candidate_checkpoint",
            message="历史候选扫描尚未到达安全断点；本轮未并发写数据库。",
            trigger=trigger, run_id=run_id, nextDueAt=next_run_at("screening"),
        )
        return {"status": "already_running", "candidateProductionHandoff": handoff}
    try:
        return _run_screening_with_exclusive_database(trigger, run_id, source_id)
    finally:
        resume_after_screening(handoff)


def _run_screening_with_exclusive_database(trigger: str, run_id: str, source_id: str | None = None) -> dict:
    from c2_1_pipeline import run_pipeline

    result = run_pipeline(
        action="retry_source" if source_id else "screening",
        trigger_kind=trigger,
        retry_source_id=source_id,
    )
    if result.get("status") == "paused":
        set_status("screening", state="paused", stage="paused", message="新币筛选已在安全点暂停，断点和上一份完整快照保留。", trigger=trigger, run_id=run_id, nextDueAt=None)
        return result
    if result.get("status") not in {"completed", "already_running"}:
        set_status("screening", state="failed", stage="screening", message="新币筛选失败，上一份完整候选快照保留。", trigger=trigger, run_id=run_id, error_code="program_failure", error_detail=str(result), nextDueAt=None)
        return result
    contract_reconciliation = recheck_changed_first_gate_contracts()
    set_status("screening", state="running", stage="candidate_production", message="正在处理本轮新增候选与到期复查对象。", trigger=trigger, run_id=run_id, completed=3, total=6)
    from candidate_production import run_worker as run_candidate_production

    production = run_candidate_production(
        queue_only="daily_incremental",
        max_partitions=1,
        partition_size=300,
        pause_requested=lambda: pause_current_requested("screening"),
    )
    if production.get("status") == "paused":
        set_status("screening", state="paused", stage="candidate_production", message="新增候选处理已在断点暂停。", trigger=trigger, run_id=run_id, completed=3, total=6, nextDueAt=None)
        return {"status": "paused", "screening": result, "candidateProduction": production}
    if production.get("status") not in {"completed", "already_running"}:
        set_status("screening", state="failed", stage="candidate_production", message="新增候选处理失败，上一份完整快照保留。", trigger=trigger, run_id=run_id, completed=3, total=6, error_code="program_failure", error_detail=str(production), nextDueAt=None)
        return {"status": "failed", "screening": result, "candidateProduction": production}
    first_gate_backlog = drain_first_gate_backlog()
    set_status("screening", state="running", stage="candidate_evaluation", message="正在把已完成资格批次交给硬门槛评估。", trigger=trigger, run_id=run_id, completed=4, total=6)
    publication = run_pipeline(
        action="evaluate_snapshot",
        trigger_kind=trigger,
        evaluation_limit=SCREENING_EVALUATION_BATCH_SIZE,
    )
    if publication.get("status") not in {"completed", "already_running"}:
        set_status("screening", state="failed", stage="candidate_evaluation", message="候选资格评估或快照发布失败，上一份完整快照保留。", trigger=trigger, run_id=run_id, completed=4, total=6, error_code="program_failure", error_detail=str(publication), nextDueAt=None)
        return {"status": "failed", "screening": result, "candidateProduction": production, "publication": publication}
    set_status("screening", state="running", stage="snapshot", message="正在发布筛选与组合快照。", trigger=trigger, run_id=run_id, completed=5, total=6)
    c24_history = reconcile_c24_history()
    snapshots = build_c22_snapshots()
    remaining = (result.get("retrySource") or {}).get("remainingRecoverableScopes")
    completion_message = (
        f"单项更新已完成；仍有{remaining}个范围连接失败，旧的成功数据已保留。"
        if source_id and remaining
        else "单项更新已完成；当前没有可恢复失败。"
        if source_id
        else "新币筛选和候选快照已完成。"
    )
    set_status("screening", state="completed", stage="snapshot_published", message=completion_message, trigger=trigger, run_id=run_id, completed=6, total=6, lastCompletedAt=now_iso(), nextDueAt=next_run_at("screening"))
    return {"status": "completed", "screening": result, "contractReconciliation": contract_reconciliation, "firstGateBacklog": first_gate_backlog, "candidateProduction": production, "publication": publication, "c24History": c24_history, "snapshot": snapshots["front"]["buildId"]}


def run_tracking(trigger: str, run_id: str, dry_run: bool, source_id: str | None = None) -> dict:
    from update_tasks import task_definition

    tracking_task = task_definition(C22_TRACKING_TASK_ID)
    tracking_total = 3 if source_id or dry_run else len(tracking_task["components"]) + 2
    set_status("convexity_tracking", state="running", stage="tracking", message="正在单独更新跟踪来源。" if source_id else "正在运行凸性跟踪。", trigger=trigger, run_id=run_id, total=tracking_total)
    if dry_run:
        snapshots = build_c22_snapshots()
        set_status("convexity_tracking", state="completed", stage="snapshot_published", message="隔离自测完成，未写入主数据库。", trigger=trigger, run_id=run_id, completed=3, total=3, lastCompletedAt=now_iso(), nextDueAt=next_run_at("convexity_tracking"))
        return {"status": "completed", "dryRun": True, "snapshot": snapshots["tracking"]["buildId"]}
    if source_id:
        from candidate_production_runtime import pause_for_screening, resume_after_screening
        from c2_2_candidate_tracking import SOURCE_TO_STAGE, run_candidate_tracking_batch

        if source_id not in SOURCE_TO_STAGE:
            result = {"status": "failed", "error": "这个来源不属于候选凸性跟踪。"}
            set_status("convexity_tracking", state="failed", stage="tracking", message="跟踪来源单项更新失败，上一份完整跟踪快照保留。", trigger=trigger, run_id=run_id, error_code="program_failure", error_detail=str(result), nextDueAt=None)
            return result
        handoff = pause_for_screening()
        if handoff.get("status") == "timeout":
            set_status("convexity_tracking", state="paused", stage="waiting_for_candidate_checkpoint", message="历史候选扫描尚未到达安全断点；本轮没有并发写入候选数据库。", trigger=trigger, run_id=run_id, nextDueAt=next_run_at("convexity_tracking"))
            return {"status": "already_running", "candidateProductionHandoff": handoff}
        try:
            result = run_candidate_tracking_batch(
                limit=TRACKING_HANDOFF_BATCH_SIZE,
                only_source_id=source_id,
            )
        finally:
            resume_after_screening(handoff)
        if result.get("status") not in {"completed", "partial_success"}:
            set_status("convexity_tracking", state="failed", stage="tracking", message="跟踪来源单项更新失败，上一份完整跟踪快照保留。", trigger=trigger, run_id=run_id, error_code="program_failure", error_detail=str(result), nextDueAt=None)
            return result
        set_status("convexity_tracking", state="running", stage="snapshot", message="正在发布单项来源更新后的跟踪快照。", trigger=trigger, run_id=run_id, completed=2, total=3)
        reconcile_c24_history()
        snapshots = build_c22_snapshots()
        completion_message = "单项来源已尝试；未完成的项目保留为可重试状态。" if result.get("partial") else "单项来源更新已完成。"
        set_status("convexity_tracking", state="partial" if result.get("partial") else "completed", stage="snapshot_published", message=completion_message, trigger=trigger, run_id=run_id, completed=3, total=3, lastCompletedAt=now_iso(), nextDueAt=next_run_at("convexity_tracking"))
        return {"status": "completed", "trackingSource": result, "snapshot": snapshots["tracking"]["buildId"]}

    from candidate_production_runtime import pause_for_screening, resume_after_screening
    from c2_2_candidate_tracking import run_candidate_tracking_batch

    set_status(
        "convexity_tracking",
        state="running",
        stage="candidate_handoff",
        message=f"正在把第一关清单按 {TRACKING_HANDOFF_BATCH_SIZE} 条一个断点交给首轮基础跟踪。",
        trigger=trigger,
        run_id=run_id,
        completed=0,
        total=tracking_total,
    )
    handoff = pause_for_screening()
    if handoff.get("status") == "timeout":
        set_status(
            "convexity_tracking",
            state="paused",
            stage="waiting_for_candidate_checkpoint",
            message="历史候选扫描尚未到达安全断点；本轮没有并发写入候选数据库。",
            trigger=trigger,
            run_id=run_id,
            nextDueAt=next_run_at("convexity_tracking", continuation=True),
        )
        return {"status": "already_running", "candidateProductionHandoff": handoff}
    batches = []
    processed_candidate_ids: set[int] = set()
    paused_during_handoff = False
    started_handoff = time.monotonic()
    try:
        while len(batches) < TRACKING_HANDOFF_MAX_BATCHES:
            if pause_current_requested("convexity_tracking"):
                paused_during_handoff = True
                break
            batch = run_candidate_tracking_batch(
                limit=TRACKING_HANDOFF_BATCH_SIZE,
                refresh_completed=True,
                exclude_candidate_ids=processed_candidate_ids,
            )
            batches.append(batch)
            processed_candidate_ids.update(int(value) for value in batch.get("candidateIds") or [])
            queue = batch.get("queue") or {}
            set_status(
                "convexity_tracking",
                state="running",
                stage="candidate_handoff",
                message=(
                    f"本轮已完成 {sum(int(item.get('completed') or 0) for item in batches)} 条首轮基础跟踪断点；"
                    f"当前队列仍有 {int(queue.get('remaining') or 0)} 条待继续。"
                ),
                trigger=trigger,
                run_id=run_id,
                completed=int(queue.get("completed") or 0),
                total=int(queue.get("total") or 0),
                checkpoint={
                    "batchesThisRun": len(batches),
                    "batchSize": TRACKING_HANDOFF_BATCH_SIZE,
                    "queue": queue,
                },
            )
            if batch.get("status") not in {"completed", "partial_success"}:
                break
            if not int(batch.get("selected") or 0):
                break
            # Keep isolated source failures on their own recoverable retry
            # records, but continue advancing fresh candidates in this run.
            # Stop only when the whole batch failed, which is the useful signal
            # that an upstream source is broadly unavailable and should not be
            # hammered for the rest of the ten-minute budget.
            if (
                int(batch.get("selected") or 0) > 0
                and int(batch.get("completed") or 0) == 0
                and int(batch.get("partial") or 0) >= int(batch.get("selected") or 0)
            ):
                break
            if not int(queue.get("remaining") or 0):
                break
            if time.monotonic() - started_handoff >= TRACKING_HANDOFF_TIME_BUDGET_SECONDS:
                break
    finally:
        resume_after_screening(handoff)
    failed_batch = next(
        (item for item in batches if item.get("status") not in {"completed", "partial_success"}),
        None,
    )
    final_queue = batches[-1].get("queue") if batches else {}
    queue_incomplete = int((final_queue or {}).get("remaining") or 0) > 0
    candidate_tracking = {
        "status": "failed" if failed_batch else "partial_success" if any(int(item.get("partial") or 0) for item in batches) or queue_incomplete else "completed",
        "selected": sum(int(item.get("selected") or 0) for item in batches),
        "completed": sum(int(item.get("completed") or 0) for item in batches),
        "partial": sum(int(item.get("partial") or 0) for item in batches),
        "batches": len(batches),
        "queue": final_queue,
        "lastBatch": batches[-1] if batches else {},
        "timeBudgetReached": bool(
            batches
            and time.monotonic() - started_handoff >= TRACKING_HANDOFF_TIME_BUDGET_SECONDS
            and int((batches[-1].get("queue") or {}).get("remaining") or 0) > 0
        ),
        "batchLimitReached": bool(
            len(batches) >= TRACKING_HANDOFF_MAX_BATCHES and queue_incomplete
        ),
    }
    if paused_during_handoff:
        snapshots = build_c22_snapshots()
        set_status(
            "convexity_tracking",
            state="paused",
            stage="paused",
            message="首轮基础跟踪已在候选断点安全暂停，已完成结果和上一份快照保留。",
            trigger=trigger,
            run_id=run_id,
            checkpoint=candidate_tracking.get("queue"),
            nextDueAt=None,
        )
        return {
            "status": "paused",
            "candidateTracking": candidate_tracking,
            "snapshot": snapshots["tracking"]["buildId"],
        }
    if candidate_tracking.get("status") not in {"completed", "partial_success"}:
        set_status(
            "convexity_tracking",
            state="failed",
            stage="candidate_handoff",
            message="候选交接失败；旧的完整跟踪快照继续保留。",
            trigger=trigger,
            run_id=run_id,
            error_code="program_failure",
            error_detail=str(candidate_tracking),
            nextDueAt=None,
        )
        return {"status": "failed", "candidateTracking": candidate_tracking}

    if queue_incomplete:
        reconcile_c24_history()
        snapshots = build_c22_snapshots()
        set_status(
            "convexity_tracking",
            state="partial",
            stage="snapshot_published",
            message=(
                f"本轮已停在安全断点；已完成 {int((final_queue or {}).get('completed') or 0)} / "
                f"{int((final_queue or {}).get('total') or 0)} 个跟踪对象，剩余对象将从当前断点继续。"
            ),
            trigger=trigger,
            run_id=run_id,
            completed=int((final_queue or {}).get("completed") or 0),
            total=int((final_queue or {}).get("total") or 0),
            checkpoint={"queue": final_queue, "batchesThisRun": candidate_tracking.get("batches")},
            lastCompletedAt=now_iso(),
            nextDueAt=next_run_at("convexity_tracking", continuation=True),
        )
        return {
            "status": "partial",
            "candidateTracking": candidate_tracking,
            "tracking": {"status": "deferred_until_candidate_handoff_complete"},
            "snapshot": snapshots["tracking"]["buildId"],
        }

    from run_update_task import run_update_task

    def tracking_progress(component: str, current_item: str, component_index: int, component_total: int) -> None:
        set_status(
            "convexity_tracking",
            state="running",
            stage=component,
            message=current_item,
            trigger=trigger,
            run_id=run_id,
            completed=max(1, component_index),
            total=component_total + 2,
        )

    cycle_key = datetime.now().astimezone().date().isoformat()
    post_baseline_state = load_json(
        POST_BASELINE_STATE_PATH,
        {"schemaVersion": "c2.2-post-baseline-state-v1"},
    )
    if post_baseline_state.get("legacyMainlineCycleKey") == cycle_key:
        result = {
            "status": "completed",
            "skipped": True,
            "reason": "legacy_mainline_already_completed_for_current_cycle",
        }
    else:
        result = run_update_task(
            task_id=C22_TRACKING_TASK_ID,
            db_path=MAIN_DB_PATH,
            pause_requested=lambda: pause_current_requested("convexity_tracking"),
            status_callback=tracking_progress,
            legacy_status=False,
        )
    if result.get("status") == "paused":
        set_status("convexity_tracking", state="paused", stage="paused", message="凸性跟踪已在组件安全点暂停，已有结果和上一份完整快照保留。", trigger=trigger, run_id=run_id, nextDueAt=None)
        return result
    if result.get("status") not in {"success", "completed", "partial_success"}:
        set_status("convexity_tracking", state="failed", stage="tracking", message="凸性跟踪失败，上一份完整跟踪快照保留。", trigger=trigger, run_id=run_id, error_code="program_failure", error_detail=str(result), nextDueAt=None)
        return result
    if not result.get("skipped"):
        post_baseline_state = {
            **post_baseline_state,
            "schemaVersion": "c2.2-post-baseline-state-v1",
            "legacyMainlineCycleKey": cycle_key,
            "legacyMainlineCompletedAt": now_iso(),
        }
        atomic_json(POST_BASELINE_STATE_PATH, post_baseline_state)

    set_status(
        "convexity_tracking",
        state="running",
        stage="deep_structure_enrichment",
        message="首轮队列已完成；正在单独补充一条全池结构与历史供应证据，不阻塞公开结果。",
        trigger=trigger,
        run_id=run_id,
        completed=tracking_total - 1,
        total=tracking_total,
    )
    from c2_2_candidate_tracking import run_deep_structure_batch

    deep_structure = run_deep_structure_batch(limit=1)
    set_status("convexity_tracking", state="running", stage="snapshot", message="正在发布跟踪与组合快照。", trigger=trigger, run_id=run_id, completed=tracking_total - 1, total=tracking_total)
    reconcile_c24_history()
    snapshots = build_c22_snapshots()
    queue = candidate_tracking.get("queue") or {}
    status = "partial" if (
        result.get("status") == "partial_success"
        or candidate_tracking.get("status") == "partial_success"
        or deep_structure.get("status") == "partial_success"
        or deep_structure.get("hasMore")
    ) else "completed"
    if int(candidate_tracking.get("partial") or 0):
        completion_message = "本轮已停在安全断点；部分来源需要后续单项重试，成功结果已保留。"
    elif int(queue.get("remaining") or 0):
        completion_message = f"本轮已停在安全断点；已完成 {int(queue.get('completed') or 0)} / {int(queue.get('total') or 0)} 个跟踪对象，余下对象将从当前断点继续。"
    elif status == "partial":
        completion_message = (
            "首轮队列和继承更新已完成；全池结构增强仍有对象，将从独立断点继续。"
            if deep_structure.get("hasMore")
            else "本轮跟踪组件部分完成；成功结果和来源状态已保留。"
        )
    else:
        completion_message = "凸性跟踪和组合快照已完成。"
    set_status(
        "convexity_tracking", state=status, stage="snapshot_published", message=completion_message,
        trigger=trigger, run_id=run_id,
        completed=int(queue.get("completed") or tracking_total),
        total=int(queue.get("total") or tracking_total),
        checkpoint={"queue": queue, "batchesThisRun": candidate_tracking.get("batches")},
        lastCompletedAt=now_iso(),
        nextDueAt=next_run_at("convexity_tracking", continuation=status == "partial"),
    )
    return {
        "status": status,
        "candidateTracking": candidate_tracking,
        "tracking": result,
        "deepStructure": deep_structure,
        "snapshot": snapshots["tracking"]["buildId"],
    }


def run(job_code: str, trigger: str = "manual", dry_run: bool = False, source_id: str | None = None) -> dict:
    if job_code not in {*JOB_CODES, "all", "due"}:
        raise ValueError("没有找到这个更新作业。")
    if source_id and job_code not in JOB_CODES:
        raise ValueError("单项来源更新必须指定一个作业。")
    selected_jobs = list(JOB_CODES) if job_code in {"all"} else [job_code] if job_code in JOB_CODES else [code for code in JOB_CODES if is_due(code)]
    if not selected_jobs:
        return {"status": "not_due", "message": "当前没有到期的更新作业。", "runtime": status_payload()}
    run_id = f"c22-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{'-'.join(selected_jobs)}"
    with pipeline_lock() as acquired:
        if not acquired:
            return {"status": "already_running", "message": "已有更新作业正在运行，未启动第二个写入者。", "runtime": status_payload()}
        state = load_state()
        save_state({**state, "lastStatus": "running", "lastStartedAt": now_iso(), "lastTrigger": trigger, "lastError": ""})
        active_job = None
        try:
            result = {}
            if "screening" in selected_jobs:
                active_job = "screening"
                if pause_current_requested("screening"):
                    return {"status": "paused", "message": "新币筛选已被暂停请求拦截。"}
                result["screening"] = run_screening(trigger, run_id, dry_run, source_id)
                if result["screening"].get("status") == "paused":
                    save_state({**load_state(), "lastStatus": "paused", "lastFinishedAt": now_iso(), "lastError": ""})
                    return {"status": "paused", "runId": run_id, **result}
                if result["screening"].get("status") not in {"completed", "already_running"}:
                    save_state({**load_state(), "lastStatus": "failed", "lastFinishedAt": now_iso(), "lastError": "screening_failed"})
                    return {"status": "failed", "runId": run_id, **result}
                active_job = None
            if "convexity_tracking" in selected_jobs:
                active_job = "convexity_tracking"
                if pause_current_requested("convexity_tracking"):
                    return {"status": "paused", "message": "凸性跟踪已被暂停请求拦截。"}
                result["tracking"] = run_tracking(trigger, run_id, dry_run, source_id)
                active_job = None
            final_status = "paused" if any(value.get("status") == "paused" for value in result.values()) else "completed" if all(value.get("status") in {"completed", "partial", "already_running"} for value in result.values()) else "failed"
            save_state({**load_state(), "lastStatus": final_status, "lastFinishedAt": now_iso(), "lastError": "" if final_status == "completed" else "job_failed"})
            return {"status": final_status, "runId": run_id, **result}
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            if active_job:
                set_status(active_job, state="failed", stage="failed", message="更新作业失败，上一份完整快照保留。", trigger=trigger, run_id=run_id, error_code="program_failure", error_detail=detail)
            save_state({**load_state(), "lastStatus": "failed", "lastFinishedAt": now_iso(), "lastError": detail})
            return {"status": "failed", "runId": run_id, "errorCode": "program_failure", "error": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description="C2.2单一隐藏更新入口")
    parser.add_argument("--job", choices=("screening", "convexity_tracking", "all", "due"), default="due")
    parser.add_argument("--trigger", choices=("manual", "automatic", "resume", "development"), default="manual")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source-id")
    args = parser.parse_args()
    result = run(args.job, args.trigger, args.dry_run, args.source_id)
    if args.job == "due" and args.trigger in {"automatic", "resume"} and not args.dry_run:
        from candidate_production_runtime import resume_authorized_history

        result["candidateProductionRecovery"] = resume_authorized_history()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"completed", "partial", "already_running", "paused"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
