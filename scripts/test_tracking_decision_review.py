#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_change_explanations_snapshot import build_snapshot as build_change_snapshot
from build_tracking_tasks_snapshot import (
    OPPORTUNITY_PREFIX,
    ROUTE_PREFIX,
    build_snapshot as build_tracking_snapshot,
    decision_follow_up,
    parse_time,
    stable_id as tracking_task_id,
)
from init_db import initialize_database
from manage_tracking_decision_review import execute_tracking_decision_review


def opportunity_case():
    return {
        "caseId": "review-case",
        "projectId": "review-project",
        "projectName": "复核测试项目",
        "symbol": "REVIEW",
        "detailUrl": "project-detail.html?id=project%3Areview-project",
        "maturity": "L2",
        "riskLevel": "medium",
        "remainingConvexity": "high",
        "ignitionProximity": "near",
        "liquidityGrade": "standard",
        "tradeabilityStatus": "verified",
        "mismatchScore": 70,
        "publicSignal": {"score": 75},
        "screening": {"status": "pass", "included": True},
        "opportunityStage": {
            "stage": "actionable",
            "stageLabel": "当前可行动",
            "stageOrder": 0,
            "modelActionCategory": "extreme",
            "modelActionLabel": "极限试仓",
            "finalActionCategory": "extreme",
            "finalActionLabel": "极限试仓",
            "finalActionReason": "测试结论变化。",
        },
    }


def create_run(connection, run_id, finished_at):
    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at, finished_at,
          zero_result_class, zero_result_explanation, triggered_by
        )
        VALUES (?, '结论复核测试', 'manual', 'success', ?, ?,
                'none', '测试', '自动测试')
        """,
        (run_id, finished_at, finished_at),
    )


def insert_tracking_result(connection, result_id, run_id, decision, finished_at):
    task_id = tracking_task_id("review-case")
    connection.execute(
        """
        INSERT INTO tracking_task_runs (
          tracking_result_id, tracking_task_id, case_id, project_id, run_id,
          project_category, task_type, priority, execution_status, decision,
          conclusion_before, conclusion_after, reason, sources_checked_json,
          source_results_json, findings_json, findings_count, new_findings_count,
          started_at, finished_at, next_review_at, retryable, retry_status,
          attempts, error_message, task_version
        )
        VALUES (?, ?, 'review-case', 'review-project', ?,
                'startup', 'foundation', 'P0', 'success', ?,
                '只观察：测试前结论', '极限试仓：测试后结论',
                '自动跟踪形成高影响结论变化。',
                '["official-website"]', '[]',
                '[{"evidenceId":"evidence-review","sourceName":"项目官网","summary":"发现新产品页面","isNew":true}]',
                1, 1, ?, ?, '2026-08-01T00:00:00Z',
                0, 'not_requested', 1, '', 'C1.3-08')
        """,
        (
            result_id,
            task_id,
            run_id,
            decision,
            finished_at,
            finished_at,
        ),
    )


def update_result(result_id, run_id, decision, finished_at):
    return {
        "tracking_result_id": result_id,
        "tracking_task_id": tracking_task_id("review-case"),
        "case_id": "review-case",
        "project_id": "review-project",
        "projectName": "复核测试项目",
        "run_id": run_id,
        "decision": decision,
        "decisionLabel": "升级复核" if decision == "upgrade" else "停止跟踪",
        "execution_status": "success",
        "statusLabel": "已发现有效记录",
        "reason": "自动跟踪形成高影响结论变化。",
        "conclusion_before": "只观察：测试前结论",
        "conclusion_after": "极限试仓：测试后结论",
        "new_findings_count": 1,
        "findings_count": 1,
        "started_at": finished_at,
        "finished_at": finished_at,
        "findings": [
            {
                "evidenceId": "evidence-review",
                "sourceName": "项目官网",
                "summary": "发现新产品页面",
                "isNew": True,
            }
        ],
    }


def test_review_confirm_reject_and_recheck():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "review.db"
        initialize_database(db_path, root / "runtime.js", backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                INSERT INTO projects (
                  project_id, canonical_name, identity_status, first_seen_at,
                  created_at, updated_at
                )
                VALUES ('review-project', '复核测试项目', 'verified', 'now',
                        'now', 'now')
                """
            )
            connection.execute(
                """
                INSERT INTO candidate_cases (
                  case_id, project_id, title, rule_version, created_at, updated_at
                )
                VALUES ('review-case', 'review-project', '复核测试项目',
                        'test', 'now', 'now')
                """
            )
            create_run(connection, "review-run-upgrade", "2026-07-30T07:00:00Z")
            insert_tracking_result(
                connection,
                "tracking-result-upgrade",
                "review-run-upgrade",
                "upgrade",
                "2026-07-30T07:00:00Z",
            )
            connection.commit()

            opportunity = {
                "generatedAt": "2026-07-30T07:00:00Z",
                "latestRefresh": {"runId": "review-run-upgrade"},
                "cases": [opportunity_case()],
            }
            update_center = {
                "latestRun": {"run_id": "review-run-upgrade"},
                "changes": [],
                "trackingResults": [
                    update_result(
                        "tracking-result-upgrade",
                        "review-run-upgrade",
                        "upgrade",
                        "2026-07-30T07:00:00Z",
                    )
                ],
            }
            pending = build_change_snapshot(connection, opportunity, update_center)
            assert pending["counts"]["decisionReviewPending"] == 1
            assert pending["reviewQueue"][0]["tracking_result_id"] == "tracking-result-upgrade"
        finally:
            connection.close()

        confirmed = execute_tracking_decision_review(
            {
                "trackingResultId": "tracking-result-upgrade",
                "action": "confirm",
                "actor": "test",
            },
            db_path=db_path,
            rebuild_snapshots=False,
        )
        assert confirmed["review"]["status"] == "confirmed"

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            confirmed_snapshot = build_change_snapshot(
                connection,
                opportunity,
                update_center,
            )
            assert confirmed_snapshot["counts"]["decisionReviewPending"] == 0
            assert confirmed_snapshot["counts"]["decisionReviewConfirmed"] == 1
            create_run(connection, "review-run-stop", "2026-07-30T08:00:00Z")
            insert_tracking_result(
                connection,
                "tracking-result-stop",
                "review-run-stop",
                "stop",
                "2026-07-30T08:00:00Z",
            )
            connection.commit()
        finally:
            connection.close()

        rejected = execute_tracking_decision_review(
            {
                "trackingResultId": "tracking-result-stop",
                "action": "reject",
                "note": "证据不足，需要重新检查。",
                "actor": "test",
            },
            db_path=db_path,
            rebuild_snapshots=False,
        )
        assert rejected["review"]["status"] == "rejected"
        reviewed_at = parse_time(rejected["review"]["reviewedAt"])
        due_check_at = reviewed_at + timedelta(seconds=1)
        follow_up_at = reviewed_at + timedelta(hours=1)
        completed_check_at = reviewed_at + timedelta(hours=2)
        follow_up_at_text = follow_up_at.isoformat().replace("+00:00", "Z")

        opportunity_path = root / "opportunity.js"
        route_path = root / "routes.js"
        opportunity_path.write_text(
            OPPORTUNITY_PREFIX
            + json.dumps(
                {
                    "generatedAt": "2026-07-30T08:00:00Z",
                    "cases": [opportunity_case()],
                },
                ensure_ascii=False,
            )
            + ";\n",
            encoding="utf-8",
        )
        route_path.write_text(
            ROUTE_PREFIX
            + json.dumps(
                {
                    "records": [
                        {
                            "caseId": "review-case",
                            "masterId": "project:review-project",
                            "routeId": "startup",
                            "routeLabel": "早期项目",
                            "nextEvidence": "项目官网",
                            "completeCount": 1,
                            "totalChecks": 8,
                            "checklist": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
            + ";\n",
            encoding="utf-8",
        )
        tracking = build_tracking_snapshot(
            opportunity_path,
            route_path,
            now=due_check_at,
            db_path=db_path,
        )
        task = tracking["tasks"][0]
        assert task["decisionReview"]["status"] == "rejected"
        assert task["decisionFollowUp"]["type"] == "rejected_recheck"
        assert task["decisionFollowUp"]["status"] == "pending"
        assert "证据不足，需要重新检查" in task["whyNow"]
        assert "独立于原结论的新证据" in task["checklist"][1]
        assert task["taskType"] == "rejected_recheck"
        assert task["status"] == "due"
        assert tracking["counts"]["decisionReviewRejected"] == 1
        assert tracking["counts"]["decisionFollowUpDue"] == 1

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            create_run(connection, "review-run-follow-up", follow_up_at_text)
            insert_tracking_result(
                connection,
                "tracking-result-follow-up",
                "review-run-follow-up",
                "continue",
                follow_up_at_text,
            )
            connection.commit()
        finally:
            connection.close()
        completed_tracking = build_tracking_snapshot(
            opportunity_path,
            route_path,
            now=completed_check_at,
            db_path=db_path,
        )
        completed_task = completed_tracking["tasks"][0]
        assert completed_task["decisionFollowUp"]["status"] == "completed"
        assert completed_tracking["counts"]["decisionFollowUpDue"] == 0
        assert completed_tracking["counts"]["decisionFollowUpCompleted"] == 1

        connection = sqlite3.connect(db_path)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM tracking_decision_reviews"
            ).fetchone()[0] == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM state_transitions"
            ).fetchone()[0] == 2
        finally:
            connection.close()


def test_page_contract():
    root = Path(__file__).resolve().parent.parent
    html = (root / "app" / "change-explanations.html").read_text(encoding="utf-8")
    script = (root / "app" / "change-explanations.js").read_text(encoding="utf-8")
    workbench = (root / "app" / "workbench.js").read_text(encoding="utf-8")
    detail = (root / "app" / "project-detail.js").read_text(encoding="utf-8")
    update_html = (root / "app" / "update-center.html").read_text(encoding="utf-8")
    update_script = (root / "app" / "update-center.js").read_text(encoding="utf-8")
    assert "重要变化" in html
    assert 'id="changeReviewFilter"' in html
    assert "确认采用" in script
    assert "不采纳并重新复查" in script
    assert "/api/convexity/tracking-decision-review" in script
    assert "pendingDecisionReviewCount" in workbench
    assert "人工复核是可选纠错" in workbench
    assert "dueDecisionFollowUpCount" in workbench
    assert "执行二次验证" in workbench
    assert "前往复核" in detail
    assert "结论二次验证" in detail
    assert 'id="verificationQueue"' in update_html
    assert "tracking-task-snapshot.js" in update_html
    assert "renderVerificationQueue" in update_script


def test_confirmed_follow_up_cadence():
    reviewed_at = "2026-07-30T07:00:00Z"
    upgrade = decision_follow_up(
        {
            "tracking_result_id": "upgrade-result",
            "decision": "upgrade",
            "decisionLabel": "升级复核",
            "decisionReview": {
                "status": "confirmed",
                "reviewedAt": reviewed_at,
                "note": "",
            },
        },
        None,
    )
    stop = decision_follow_up(
        {
            "tracking_result_id": "stop-result",
            "decision": "stop",
            "decisionLabel": "停止跟踪",
            "decisionReview": {
                "status": "confirmed",
                "reviewedAt": reviewed_at,
                "note": "",
            },
        },
        None,
    )
    assert upgrade["type"] == "verify_upgrade"
    assert (parse_time(upgrade["dueAt"]) - parse_time(reviewed_at)).days == 1
    assert stop["type"] == "verify_stop"
    assert (parse_time(stop["dueAt"]) - parse_time(reviewed_at)).days == 7


def main():
    test_review_confirm_reject_and_recheck()
    test_page_contract()
    test_confirmed_follow_up_cadence()
    print("C1.3-08 结论复核、状态回写、驳回重查与页面入口测试通过。")


if __name__ == "__main__":
    main()
