#!/usr/bin/env python3
import json
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
sys.path.insert(0, str(ROOT / "scripts"))

from build_change_explanations_snapshot import build_snapshot  # noqa: E402
from init_db import initialize_database  # noqa: E402


def opportunity_case(stage="observe", stage_order=3, risk="high", price=100):
    return {
        "caseId": "change-test-case",
        "projectName": "变化测试项目",
        "symbol": "CHANGE",
        "detailUrl": "project-detail.html?id=change-test-case",
        "maturity": "L2",
        "riskLevel": risk,
        "remainingConvexity": "high",
        "ignitionProximity": "near",
        "liquidityGrade": "extreme",
        "tradeabilityStatus": "limited",
        "mismatchScore": 60,
        "publicSignal": {"score": 65},
        "screening": {"status": "pending", "included": True},
        "latestMarket": {
            "priceUsd": price,
            "volume24hUsd": 100000,
            "liquidityUsd": 50000,
            "fdvUsd": 1000000,
        },
        "opportunityStage": {
            "stage": stage,
            "stageLabel": {
                "observe": "研究观察",
                "qualified_pending": "入选待补证",
            }[stage],
            "stageOrder": stage_order,
            "modelActionCategory": "observe",
            "modelActionLabel": "只观察",
        },
    }


def opportunity(case, run_id=""):
    return {
        "generatedAt": "2026-07-29T00:00:00Z",
        "latestRefresh": {"runId": run_id} if run_id else {},
        "cases": [case],
    }


def tracking_result(
    run_id,
    decision="continue",
    new_findings=1,
    result_id="tracking-result-change-test",
):
    return {
        "tracking_result_id": result_id,
        "tracking_task_id": "tracking-task-change-test",
        "case_id": "change-test-case",
        "run_id": run_id,
        "decision": decision,
        "decisionLabel": {
            "continue": "继续跟踪",
            "upgrade": "升级复核",
            "stop": "停止跟踪",
        }[decision],
        "execution_status": "success",
        "statusLabel": "已发现有效记录",
        "reason": f"自动跟踪发现{new_findings}条新增或变化证据。",
        "conclusion_before": "只观察：测试",
        "conclusion_after": "只观察：测试",
        "new_findings_count": new_findings,
        "findings_count": new_findings,
        "started_at": "2026-07-30T06:00:00Z",
        "finished_at": "2026-07-30T06:01:00Z",
        "findings": [
            {
                "evidenceId": "tracking-evidence-change-test",
                "sourceName": "治理论坛",
                "eventType": "governance_proposal",
                "summary": "发现新的治理提案。",
                "sourceUrl": "https://example.com/proposal",
                "observedAt": "2026-07-30T05:55:00Z",
                "collectedAt": "2026-07-30T06:00:00Z",
                "isNew": True,
                "changes": [],
            }
        ] if new_findings else [],
    }


def insert_run(connection, run_id):
    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at, finished_at,
          zero_result_class, zero_result_explanation, triggered_by
        )
        VALUES (?, '变化测试', 'manual', 'success',
                '2026-07-30T06:00:00Z', '2026-07-30T06:01:00Z',
                'none', '测试', '自动测试')
        """,
        (run_id,),
    )


def test_history_rules():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "change.db"
        initialize_database(db_path, root / "runtime.js", backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                INSERT INTO candidate_cases (
                  case_id, title, rule_version, created_at, updated_at
                )
                VALUES ('change-test-case', '变化测试项目', 'test', 'now', 'now')
                """
            )
            connection.commit()

            first = build_snapshot(
                connection,
                opportunity(opportunity_case()),
                {"changes": []},
            )
            assert first["counts"]["baseline"] == 1
            assert first["counts"]["upgrade"] == 0
            assert first["items"][0]["currentStatus"] == "baseline"

            second = build_snapshot(
                connection,
                opportunity(
                    opportunity_case(
                        stage="qualified_pending",
                        stage_order=2,
                        risk="medium",
                        price=115,
                    )
                ),
                {"changes": []},
            )
            assert second["counts"]["upgrade"] == 1
            assert second["counts"]["history"] == 2
            fields = {
                item["field"]
                for item in second["items"][0]["latestHistory"]["changedFields"]
            }
            assert {"stage", "riskLevel", "priceUsd"}.issubset(fields)
            assert "行动阶段从" in second["items"][0]["currentExplanation"]

            third = build_snapshot(
                connection,
                opportunity(
                    opportunity_case(
                        stage="qualified_pending",
                        stage_order=2,
                        risk="medium",
                        price=120,
                    )
                ),
                {"changes": []},
            )
            assert third["counts"]["stable"] == 1
            assert third["counts"]["history"] == 2
            assert third["items"][0]["currentStatus"] == "stable"
        finally:
            connection.close()


def test_tracking_results_merge_and_material_rules():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "tracking-change.db"
        initialize_database(db_path, root / "runtime.js", backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                INSERT INTO candidate_cases (
                  case_id, title, rule_version, created_at, updated_at
                )
                VALUES ('change-test-case', '变化测试项目', 'test', 'now', 'now')
                """
            )
            build_snapshot(
                connection,
                opportunity(opportunity_case()),
                {"changes": [], "trackingResults": []},
            )

            run_id = "tracking-stage-merge-run"
            insert_run(connection, run_id)
            connection.commit()
            merged = build_snapshot(
                connection,
                opportunity(
                    opportunity_case(
                        stage="qualified_pending",
                        stage_order=2,
                        risk="medium",
                    ),
                    run_id=run_id,
                ),
                {
                    "latestRun": {"run_id": run_id},
                    "changes": [],
                    "trackingResults": [
                        tracking_result(run_id, decision="upgrade"),
                    ],
                },
            )
            assert merged["version"] == "C1.3-08"
            assert merged["counts"]["trackingMaterial"] == 1
            assert merged["counts"]["history"] == 2
            assert merged["items"][0]["currentStatus"] == "upgrade"
            assert merged["items"][0]["latestHistory"]["changeSource"] == "stage_and_tracking"
            assert merged["items"][0]["latestHistory"]["trackingResult"]["decision"] == "upgrade"
            assert len(merged["items"][0]["latestHistory"]["evidence"]) == 1

            no_change = build_snapshot(
                connection,
                opportunity(
                    opportunity_case(
                        stage="qualified_pending",
                        stage_order=2,
                        risk="medium",
                    ),
                    run_id=run_id,
                ),
                {
                    "latestRun": {"run_id": run_id},
                    "changes": [],
                    "trackingResults": [
                        tracking_result(
                            run_id,
                            decision="continue",
                            new_findings=0,
                            result_id="tracking-result-no-change",
                        ),
                    ],
                },
            )
            assert no_change["counts"]["trackingMaterial"] == 0
            assert no_change["counts"]["history"] == 2
            assert no_change["items"][0]["currentStatus"] == "stable"
        finally:
            connection.close()


def test_live_snapshot_and_pages():
    prefix = "window.PENGUIN_CONVEXITY_CHANGE_EXPLANATIONS = "
    text = (APP_ROOT / "change-explanations-snapshot.js").read_text(encoding="utf-8").strip()
    snapshot = json.loads(text[len(prefix):-1])
    assert snapshot["version"] == "C1.3-08"
    assert snapshot["counts"]["total"] == len(snapshot["items"])
    assert snapshot["counts"]["history"] == len(snapshot["history"])
    assert snapshot["counts"]["recent24h"] == len(snapshot["recent24h"])
    assert snapshot["counts"]["recent7d"] == len(snapshot["recent7d"])
    assert all(item["currentExplanation"] for item in snapshot["items"])

    html = (APP_ROOT / "change-explanations.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "change-explanations.js").read_text(encoding="utf-8")
    opportunity_html = (APP_ROOT / "candidate-pool.html").read_text(encoding="utf-8")
    opportunity_script = (APP_ROOT / "candidate-pool.js").read_text(encoding="utf-8")
    assert "C1.3-08" in html
    assert 'id="changeDirectionFilter"' in html
    assert 'id="changeCategoryFilter"' in html
    assert "查看原始来源" in script
    assert ".slice(" not in script
    assert "change-explanations-snapshot.js" in opportunity_html
    assert 'id="opportunityChangeFeed"' in opportunity_html
    assert "尚无可比较的自动升降级" in opportunity_script
    assert "过去24小时无新增，展示近7天" in opportunity_script
    assert "changeState.recent24h" in opportunity_script
    assert "规则重算 + 自动跟踪" in json.dumps(snapshot, ensure_ascii=False) or snapshot["counts"]["trackingMaterial"] == 0
    assert "item.changeSourceLabel" in opportunity_script
    assert "最近跟踪" in opportunity_script


def main():
    test_history_rules()
    test_tracking_results_merge_and_material_rules()
    test_live_snapshot_and_pages()
    print("C1.3-08 change explanation and tracking-result checks passed")


if __name__ == "__main__":
    main()
