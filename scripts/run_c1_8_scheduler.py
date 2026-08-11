#!/usr/bin/env python3
"""Run one isolated C1.8 unattended scheduler tick."""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from c1_8_runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOCK_PATH,
    DEFAULT_STATE_PATH,
    load_config,
    load_json,
    mark_scheduler_run,
    scheduler_lock,
    scheduler_status,
    should_run,
)
from build_tracking_tasks_snapshot import load_js_payload
from run_update_task import run_update_task


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
OPPORTUNITY_PATH = APP_ROOT / "opportunity-center-snapshot.js"
TRACKING_PATH = APP_ROOT / "tracking-task-snapshot.js"
OPPORTUNITY_PREFIX = "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = "
TRACKING_PREFIX = "window.PENGUIN_CONVEXITY_TRACKING_TASKS = "


def utc_now():
    return datetime.now(timezone.utc)


def load_snapshot(path, prefix):
    try:
        return load_js_payload(path, prefix)
    except (OSError, ValueError):
        return {}


def due_task_count(db_path, now=None):
    """Count currently due tracking cases from the live project database."""
    now = now or utc_now()
    db_path = Path(db_path)
    if not db_path.exists():
        return 0
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM candidate_cases
            WHERE next_review_at IS NOT NULL
              AND next_review_at != ''
              AND datetime(next_review_at) <= datetime(?)
              AND COALESCE(action_stage, '') NOT IN ('失效/排除', '已失去凸性')
            """,
            (now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),),
        ).fetchone()
        return int(row[0] if row else 0)
    except sqlite3.OperationalError:
        return 0
    finally:
        connection.close()


def result_scheduler_status(result, kind):
    result_status = result.get("status")
    message = str(result.get("message") or "")
    lowered = message.lower()
    if any(marker in lowered for marker in ("quota", "rate limit", "429", "额度")):
        return "quota_delayed"
    if result_status == "partial_success":
        return "partial"
    if result_status != "success":
        return "failed"
    tracking = (result.get("summary") or {}).get("tracking") or {}
    tracking_results = tracking.get("results") or []
    if kind == "hourly" and tracking_results and all(
        item.get("status") == "no_change" for item in tracking_results
    ):
        return "no_change"
    return "completed"


def run_once(
    db_path,
    now=None,
    dry_run=False,
    force=False,
    config_path=DEFAULT_CONFIG_PATH,
    state_path=DEFAULT_STATE_PATH,
    lock_path=DEFAULT_LOCK_PATH,
):
    now = now or utc_now()
    config = load_config(config_path)
    opportunity = load_snapshot(OPPORTUNITY_PATH, OPPORTUNITY_PREFIX)
    tracking = load_snapshot(TRACKING_PATH, TRACKING_PREFIX)
    due_count = due_task_count(db_path, now)
    tracking.setdefault("counts", {})["due"] = due_count
    with scheduler_lock(lock_path) as acquired:
        if not acquired:
            return {"status": "queued", "kind": None, "message": "已有调度任务正在执行。"}
        if config.get("paused") or not config.get("enabled"):
            mark_scheduler_run("paused", "none", now=now, state_path=state_path, config_path=config_path)
            return {"status": "paused", "kind": None, "message": "自动调度已暂停。"}
        kind = "forced" if force else should_run(
            now=now,
            config_path=config_path,
            state_path=state_path,
            due_count=due_count,
        )
        if not kind:
            mark_scheduler_run("not_due", "none", now=now, state_path=state_path, config_path=config_path)
            status = scheduler_status(opportunity, tracking, now, config_path, state_path, due_count=due_count)
            return {"status": "not_due", "kind": None, "message": status["reason"], "scheduler": status}
        if dry_run:
            mark_scheduler_run("queued", kind, now=now, state_path=state_path, config_path=config_path)
            return {
                "status": "queued",
                "kind": kind,
                "taskId": "full_refresh" if kind in {"daily", "forced"} else "tracking_task_refresh",
                "dueCount": due_count,
                "message": "隔离自测：已选择应执行任务，未写入业务数据库。",
            }
        task_id = "full_refresh" if kind in {"daily", "forced"} else "tracking_task_refresh"
        mark_scheduler_run("running", kind, now=now, state_path=state_path, config_path=config_path)
        try:
            result = run_update_task(task_id=task_id, db_path=db_path)
            status = result_scheduler_status(result, kind)
            error = result.get("message", "") if status in {"partial", "failed", "quota_delayed"} else ""
            mark_scheduler_run(status, kind, now=utc_now(), error=error, state_path=state_path, config_path=config_path)
            return {**result, "schedulerStatus": status, "kind": kind}
        except Exception as error:
            mark_scheduler_run("failed", kind, now=utc_now(), error=str(error), state_path=state_path, config_path=config_path)
            return {"status": "failed", "kind": kind, "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description="C1.8 凸性无人值守调度器")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "convexity.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    args = parser.parse_args()
    print(json.dumps(run_once(args.db, dry_run=args.dry_run, force=args.force, config_path=args.config, state_path=args.state, lock_path=args.lock), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
