#!/usr/bin/env python3
"""Single C2.1 update entry used by manual and automatic hidden launches."""

import argparse
import json

from c2_1_pipeline import run_pipeline
from c2_1_runtime import (
    due_source_resume, interrupted_run_requires_resume, is_due, iso_time, load_config,
    load_state, next_run_at, pause_current_requested, pipeline_status, save_state, utc_now,
)


def run(action="all", trigger="automatic", force=False):
    state = load_state()
    config = load_config()
    if trigger == "automatic" and not force:
        status = pipeline_status()
        resume = status.get("resumeAvailable") is True or interrupted_run_requires_resume(status) or due_source_resume()
        if resume and pause_current_requested():
            return {"status": "paused", "message": "当前任务由用户暂停；断点已保留，不会自动恢复。"}
        if not resume and not is_due():
            return {"status": "not_due", "message": "当前没有到期的新一轮或待恢复任务。"}
        if resume:
            trigger = "resume"
    state.update(lastStartedAt=iso_time(utc_now()), lastStatus="running", lastTrigger=trigger, lastError="")
    save_state(state)
    result = run_pipeline(action=action, trigger_kind=trigger)
    finished = utc_now()
    state.update(
        lastFinishedAt=iso_time(finished), lastStatus=result.get("status", "failed"),
        lastError=result.get("error", ""), nextRunAt=iso_time(next_run_at(load_config(), finished)),
    )
    save_state(state)
    return result


def main():
    parser = argparse.ArgumentParser(description="企鹅投研-凸性 C2.1统一增量更新器")
    parser.add_argument("--action", choices=("all", "import", "sync", "enrich", "evaluate", "snapshot"), default="all")
    parser.add_argument("--trigger", choices=("manual", "automatic", "resume", "development"), default="automatic")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = run(args.action, args.trigger, args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("status") in {"completed", "already_running", "not_due", "paused"} else 1)


if __name__ == "__main__":
    main()
