#!/usr/bin/env python3
"""Single-process C2.2 orchestrator for screening and convexity tracking."""

from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from build_c2_2_snapshots import build_snapshots
from c2_2_runtime import (
    JOB_CODES,
    RUNTIME_ROOT,
    iso_time,
    is_due,
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
    return build_snapshots(c21_front_path=C21_FRONT_PATH, c21_admin_path=C21_ADMIN_PATH, write=True)


def run_screening(trigger: str, run_id: str, dry_run: bool) -> dict:
    set_status("screening", state="running", stage="screening", message="正在运行90天新币筛选。", trigger=trigger, run_id=run_id, total=4)
    if dry_run:
        snapshots = build_c22_snapshots()
        set_status("screening", state="completed", stage="snapshot_published", message="隔离自测完成，未写入筛选数据库。", trigger=trigger, run_id=run_id, completed=4, total=4, lastCompletedAt=now_iso(), nextDueAt=next_run_at("screening"))
        return {"status": "completed", "dryRun": True, "snapshot": snapshots["front"]["buildId"]}
    from c2_1_pipeline import run_pipeline

    result = run_pipeline(action="all", trigger_kind=trigger)
    if result.get("status") not in {"completed", "already_running"}:
        set_status("screening", state="failed", stage="screening", message="新币筛选失败，上一份完整候选快照保留。", trigger=trigger, run_id=run_id, error_code="program_failure", error_detail=str(result), nextDueAt=None)
        return result
    set_status("screening", state="running", stage="snapshot", message="正在发布C2.2筛选与组合快照。", trigger=trigger, run_id=run_id, completed=3, total=4)
    snapshots = build_c22_snapshots()
    set_status("screening", state="completed", stage="snapshot_published", message="新币筛选和候选快照已完成。", trigger=trigger, run_id=run_id, completed=4, total=4, lastCompletedAt=now_iso(), nextDueAt=next_run_at("screening"))
    return {"status": "completed", "screening": result, "snapshot": snapshots["front"]["buildId"]}


def run_tracking(trigger: str, run_id: str, dry_run: bool) -> dict:
    set_status("convexity_tracking", state="running", stage="tracking", message="正在运行凸性跟踪。", trigger=trigger, run_id=run_id, total=3)
    if dry_run:
        snapshots = build_c22_snapshots()
        set_status("convexity_tracking", state="completed", stage="snapshot_published", message="隔离自测完成，未写入主数据库。", trigger=trigger, run_id=run_id, completed=3, total=3, lastCompletedAt=now_iso(), nextDueAt=next_run_at("convexity_tracking"))
        return {"status": "completed", "dryRun": True, "snapshot": snapshots["tracking"]["buildId"]}
    from run_update_task import run_update_task

    result = run_update_task(task_id="full_refresh", db_path=MAIN_DB_PATH)
    if result.get("status") not in {"success", "completed", "partial_success"}:
        set_status("convexity_tracking", state="failed", stage="tracking", message="凸性跟踪失败，上一份完整跟踪快照保留。", trigger=trigger, run_id=run_id, error_code="program_failure", error_detail=str(result), nextDueAt=None)
        return result
    set_status("convexity_tracking", state="running", stage="snapshot", message="正在发布C2.2跟踪与组合快照。", trigger=trigger, run_id=run_id, completed=2, total=3)
    snapshots = build_c22_snapshots()
    status = "completed" if result.get("status") != "partial_success" else "partial"
    set_status("convexity_tracking", state=status, stage="snapshot_published", message="凸性跟踪已完成，部分来源结果按来源状态保留。" if status == "partial" else "凸性跟踪和组合快照已完成。", trigger=trigger, run_id=run_id, completed=3, total=3, lastCompletedAt=now_iso(), nextDueAt=next_run_at("convexity_tracking"))
    return {"status": status, "tracking": result, "snapshot": snapshots["tracking"]["buildId"]}


def run(job_code: str, trigger: str = "manual", dry_run: bool = False) -> dict:
    if job_code not in {*JOB_CODES, "all", "due"}:
        raise ValueError("没有找到这个C2.2作业。")
    selected_jobs = list(JOB_CODES) if job_code in {"all"} else [job_code] if job_code in JOB_CODES else [code for code in JOB_CODES if is_due(code)]
    if not selected_jobs:
        return {"status": "not_due", "message": "当前没有到期的C2.2作业。", "runtime": status_payload()}
    run_id = f"c22-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{'-'.join(selected_jobs)}"
    with pipeline_lock() as acquired:
        if not acquired:
            return {"status": "already_running", "message": "已有C2.2作业正在运行，未启动第二个写入者。", "runtime": status_payload()}
        if pause_current_requested():
            return {"status": "paused", "message": "当前任务已被暂停请求拦截。"}
        state = load_state()
        save_state({**state, "lastStatus": "running", "lastStartedAt": now_iso(), "lastTrigger": trigger, "lastError": ""})
        try:
            result = {}
            if "screening" in selected_jobs:
                result["screening"] = run_screening(trigger, run_id, dry_run)
                if result["screening"].get("status") not in {"completed", "already_running"}:
                    save_state({**load_state(), "lastStatus": "failed", "lastFinishedAt": now_iso(), "lastError": "screening_failed"})
                    return {"status": "failed", "runId": run_id, **result}
            if "convexity_tracking" in selected_jobs:
                result["tracking"] = run_tracking(trigger, run_id, dry_run)
            final_status = "completed" if all(value.get("status") in {"completed", "partial", "already_running"} for value in result.values()) else "failed"
            save_state({**load_state(), "lastStatus": final_status, "lastFinishedAt": now_iso(), "lastError": "" if final_status == "completed" else "job_failed"})
            return {"status": final_status, "runId": run_id, **result}
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            for code in selected_jobs:
                set_status(code, state="failed", stage="failed", message="C2.2作业失败，上一份完整快照保留。", trigger=trigger, run_id=run_id, error_code="program_failure", error_detail=detail)
            save_state({**load_state(), "lastStatus": "failed", "lastFinishedAt": now_iso(), "lastError": detail})
            return {"status": "failed", "runId": run_id, "errorCode": "program_failure", "error": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description="C2.2单一隐藏更新入口")
    parser.add_argument("--job", choices=("screening", "convexity_tracking", "all", "due"), default="due")
    parser.add_argument("--trigger", choices=("manual", "automatic", "resume", "development"), default="manual")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run(args.job, args.trigger, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"completed", "partial", "already_running", "paused"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
