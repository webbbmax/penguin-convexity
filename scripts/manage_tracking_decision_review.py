#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from build_change_explanations_snapshot import rebuild_change_explanations_snapshot
from build_tracking_tasks_snapshot import rebuild_tracking_tasks_snapshot
from init_db import (
    DEFAULT_DB_PATH,
    DEFAULT_SNAPSHOT_PATH,
    initialize_database,
    write_runtime_snapshot,
)


RULE_VERSION = "C1.3-08"
ACTION_MAP = {
    "confirm": "confirmed",
    "reject": "rejected",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(tracking_result_id, reviewed_at):
    digest = hashlib.sha256(
        f"{tracking_result_id}:{reviewed_at}".encode("utf-8")
    ).hexdigest()[:20]
    return f"tracking-review-{digest}"


def parse_json(value, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def execute_tracking_decision_review(
    payload,
    db_path=DEFAULT_DB_PATH,
    rebuild_snapshots=True,
):
    tracking_result_id = str(payload.get("trackingResultId") or "").strip()
    requested_action = str(payload.get("action") or "").strip()
    note = str(payload.get("note") or "").strip()
    actor = str(payload.get("actor") or "user").strip() or "user"
    if not tracking_result_id:
        raise ValueError("请选择需要复核的结论变化。")
    if requested_action not in ACTION_MAP:
        raise ValueError("复核动作只能是确认采用或不采纳并重新复查。")
    if requested_action == "reject" and len(note) < 4:
        raise ValueError("不采纳时请填写简短原因，避免无法追溯。")

    db_path = Path(db_path).resolve()
    runtime_snapshot_path = (
        DEFAULT_SNAPSHOT_PATH
        if db_path == Path(DEFAULT_DB_PATH).resolve()
        else db_path.with_name("runtime-snapshot.js")
    )
    initialize_database(
        db_path=db_path,
        snapshot_path=runtime_snapshot_path,
        backup=False,
    )
    reviewed_at = utc_now()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        result = connection.execute(
            """
            SELECT tracking.*
            FROM tracking_task_runs tracking
            WHERE tracking.tracking_result_id = ?
            """,
            (tracking_result_id,),
        ).fetchone()
        if not result:
            raise ValueError("没有找到这条跟踪结论，请刷新页面后重试。")
        if result["decision"] not in {"upgrade", "stop"}:
            raise ValueError("继续跟踪和行动后监测由系统自动处理，不进入人工复核。")
        if result["execution_status"] == "failed":
            raise ValueError("本轮信源检查失败，应先单独重试，不能复核结论。")

        review_action = ACTION_MAP[requested_action]
        findings = parse_json(result["findings_json"], [])
        evidence_ids = [
            item.get("evidenceId")
            for item in findings
            if item.get("evidenceId")
        ]
        review_id = stable_id(tracking_result_id, reviewed_at)
        connection.execute(
            """
            INSERT INTO tracking_decision_reviews (
              tracking_review_id, tracking_result_id, tracking_task_id,
              case_id, decision, review_action, review_note,
              evidence_ids_json, actor, reviewed_at, rule_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                tracking_result_id,
                result["tracking_task_id"],
                result["case_id"],
                result["decision"],
                review_action,
                note,
                json.dumps(evidence_ids, ensure_ascii=False),
                actor,
                reviewed_at,
                RULE_VERSION,
            ),
        )
        transition_id = f"state-{review_id}"
        connection.execute(
            """
            INSERT INTO state_transitions (
              transition_id, case_id, from_state, to_state, reason,
              evidence_ids_json, rule_version, actor, transitioned_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition_id,
                result["case_id"],
                f"tracking_{result['decision']}_pending",
                (
                    f"tracking_{result['decision']}_confirmed"
                    if review_action == "confirmed"
                    else "tracking_decision_rejected_recheck"
                ),
                note or (
                    "确认采用自动跟踪形成的结论变化。"
                    if review_action == "confirmed"
                    else "不采纳本次自动结论，项目重新进入复查。"
                ),
                json.dumps(evidence_ids, ensure_ascii=False),
                RULE_VERSION,
                actor,
                reviewed_at,
            ),
        )
        connection.commit()
        write_runtime_snapshot(connection, runtime_snapshot_path)
    finally:
        connection.close()

    tracking_snapshot = None
    change_snapshot = None
    if rebuild_snapshots:
        tracking_snapshot = rebuild_tracking_tasks_snapshot(db_path=db_path)
        change_snapshot = rebuild_change_explanations_snapshot(db_path=db_path)
    follow_up = None
    if tracking_snapshot:
        follow_up = next(
            (
                item.get("decisionFollowUp")
                for item in tracking_snapshot.get("tasks", [])
                if item.get("caseId") == result["case_id"]
            ),
            None,
        )
    return {
        "status": "success",
        "message": (
            (
                "结论变化已确认采用，并安排次日二次验证。"
                if result["decision"] == "upgrade"
                else "停止结论已确认采用，并安排7天后复核失效是否持续。"
            )
            if requested_action == "confirm"
            else "本次结论未采用，驳回原因已转成检查清单并立即重新复查。"
        ),
        "review": {
            "reviewId": review_id,
            "trackingResultId": tracking_result_id,
            "caseId": result["case_id"],
            "decision": result["decision"],
            "status": review_action,
            "note": note,
            "actor": actor,
            "reviewedAt": reviewed_at,
        },
        "followUp": follow_up,
        "counts": {
            "trackingDue": (
                tracking_snapshot.get("counts", {}).get("due", 0)
                if tracking_snapshot
                else None
            ),
            "decisionReviewPending": (
                change_snapshot.get("counts", {}).get("decisionReviewPending", 0)
                if change_snapshot
                else None
            ),
            "decisionFollowUpDue": (
                tracking_snapshot.get("counts", {}).get("decisionFollowUpDue", 0)
                if tracking_snapshot
                else None
            ),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="处理C1.3-08跟踪结论复核")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--tracking-result-id", required=True)
    parser.add_argument("--action", choices=sorted(ACTION_MAP), required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--actor", default="user")
    parser.add_argument("--skip-rebuild", action="store_true")
    args = parser.parse_args()
    result = execute_tracking_decision_review(
        {
            "trackingResultId": args.tracking_result_id,
            "action": args.action,
            "note": args.note,
            "actor": args.actor,
        },
        db_path=args.db,
        rebuild_snapshots=not args.skip_rebuild,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
