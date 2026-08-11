#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from build_tracking_tasks_snapshot import OUTPUT_PREFIX
from build_update_center_snapshot import rebuild_update_snapshots
from execute_tracking_tasks import (
    TASK_SOURCE_IDS,
    decision_for,
    execute_tracking_tasks,
    execution_status_for,
    source_results,
)
from init_db import initialize_database
from refresh_candidate_pool import formal_market_dependency_stats
from sync_thread_candidates import import_candidates, load_fixture
from update_tasks import task_definition


def create_run(connection, run_id, source_status="success", source_ids=None):
    now = "2026-07-30T04:00:00Z"
    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at, finished_at,
          zero_result_class, zero_result_explanation, triggered_by
        )
        VALUES (?, '凸性项目跟踪任务更新', 'manual', 'success', ?, ?,
                'none', '测试运行', '自动测试')
        """,
        (run_id, now, now),
    )
    for source_id in source_ids or TASK_SOURCE_IDS["pre_signal"]:
        connection.execute(
            """
            INSERT OR IGNORE INTO sources (
              source_id, name, source_type, url, access_method, scope,
              confidence, conflict_risk, status, schedule_text,
              created_at, updated_at
            )
            VALUES (?, ?, 'test', '', 'fixture', 'convexity', '中', '低',
                    'active', 'test', ?, ?)
            """,
            (source_id, source_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              failed_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            (
                f"{run_id}:{source_id}",
                run_id,
                source_id,
                f"test-{source_id}",
                source_status,
                now,
                now,
                1 if source_status == "failed" else 0,
            ),
        )


def task(current_action="只观察", status="due"):
    return {
        "taskId": "tracking-task-uniswap-test",
        "caseId": "thread-uni-20260728",
        "projectId": "uniswap",
        "projectName": "Uniswap",
        "projectCategory": "mature",
        "taskType": "pre_signal",
        "priority": "P1",
        "status": status,
        "currentAction": current_action,
        "currentConclusion": f"{current_action}：自动执行测试",
        "reviewCadenceDays": 1,
    }


def write_tracking_snapshot(path, tasks):
    path.write_text(
        OUTPUT_PREFIX + json.dumps({"tasks": tasks}, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )


def test_execute_continue_failure_and_upgrade():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        runtime_path = root / "runtime.js"
        tracking_path = root / "tracking.js"
        initialize_database(db_path, runtime_path, backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            import_candidates(connection, load_fixture())
            create_run(connection, "tracking-run-continue")
            connection.execute(
                """
                INSERT INTO raw_events (
                  raw_event_id, source_id, ingestion_run_id, external_id,
                  published_at, collected_at, content_hash, source_url,
                  excerpt, project_hint, asset_hint, chain_hint, event_type,
                  raw_payload_json, status
                )
                VALUES (
                  'tracking-evidence-uniswap',
                  'evidence-cactus-governance',
                  'tracking-run-continue',
                  'proposal-1',
                  '2026-07-30T03:00:00Z',
                  '2026-07-30T04:00:00Z',
                  'new-proposal-hash',
                  'https://example.com/proposal',
                  '发现新的治理提案',
                  'thread-uni-20260728',
                  'UNI',
                  'Ethereum',
                  'onchain_governance_proposal',
                  '{"changes":[]}',
                  'normalized'
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        write_tracking_snapshot(tracking_path, [task()])
        continued = execute_tracking_tasks(
            db_path=db_path,
            run_id="tracking-run-continue",
            snapshot_path=tracking_path,
            now=datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc),
        )
        assert continued["completed"] == 1
        assert continued["continued"] == 1
        assert continued["results"][0]["newFindings"] == 1

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            create_run(connection, "tracking-run-failed", source_status="failed")
            connection.commit()
        finally:
            connection.close()
        failed = execute_tracking_tasks(
            db_path=db_path,
            run_id="tracking-run-failed",
            snapshot_path=tracking_path,
            tracking_task_id="tracking-task-uniswap-test",
            force=True,
            now=datetime(2026, 7, 30, 5, 0, tzinfo=timezone.utc),
        )
        assert failed["failed"] == 1
        assert failed["results"][0]["retryable"]

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            create_run(connection, "tracking-run-upgrade")
            connection.commit()
        finally:
            connection.close()
        write_tracking_snapshot(tracking_path, [task("极限试仓")])
        upgraded = execute_tracking_tasks(
            db_path=db_path,
            run_id="tracking-run-upgrade",
            snapshot_path=tracking_path,
            tracking_task_id="tracking-task-uniswap-test",
            force=True,
            now=datetime(2026, 7, 30, 6, 0, tzinfo=timezone.utc),
        )
        assert upgraded["upgraded"] == 1

        update, _sources = rebuild_update_snapshots(
            db_path=db_path,
            update_path=root / "update.js",
            source_path=root / "sources.js",
        )
        assert update["counts"]["trackingExecutions"] == 3
        assert update["trackingResults"][0]["decisionLabel"] == "升级复核"
        assert update["trackingResults"][1]["retry_status"] == "succeeded"


def test_not_due_is_explicit_skip():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        tracking_path = root / "tracking.js"
        initialize_database(db_path, root / "runtime.js", backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            import_candidates(connection, load_fixture())
            create_run(connection, "tracking-run-not-due")
            connection.commit()
        finally:
            connection.close()
        write_tracking_snapshot(tracking_path, [task(status="open")])
        result = execute_tracking_tasks(
            db_path=db_path,
            run_id="tracking-run-not-due",
            snapshot_path=tracking_path,
        )
        assert result["eligible"] == 0
        assert result["notDue"] == 1
        assert "没有项目到达复查时间" in result["explanation"]


def test_decision_follow_up_rules():
    rejected_task = {
        **task("极限试仓"),
        "decisionFollowUp": {
            "required": True,
            "status": "pending",
            "type": "rejected_recheck",
        },
    }
    decision, reason = decision_for(
        rejected_task,
        None,
        "success",
        0,
    )
    assert decision == "continue"
    assert "保持上一结论" in reason

    verified_upgrade = {
        **task("极限试仓"),
        "decisionFollowUp": {
            "required": True,
            "status": "pending",
            "type": "verify_upgrade",
        },
    }
    decision, reason = decision_for(
        verified_upgrade,
        None,
        "success",
        1,
    )
    assert decision == "monitor"
    assert "二次验证后仍维持极限试仓" in reason

    verified_stop = {
        **task("失效/排除"),
        "decisionFollowUp": {
            "required": True,
            "status": "pending",
            "type": "verify_stop",
        },
    }
    decision, reason = decision_for(
        verified_stop,
        None,
        "no_change",
        0,
    )
    assert decision == "monitor"
    assert "停止条件仍然成立" in reason


def test_production_tracking_runs_formal_market_dependencies():
    definition = task_definition("tracking_task_refresh")
    assert "formal_market_exit" in definition["components"]
    assert "market" not in definition["components"]
    assert "contracts" not in definition["components"]

    stats = {
        item["sourceId"]: item
        for item in formal_market_dependency_stats(
            {
                "records": [
                    {
                        "asset_id": "asset-test",
                        "contract_address": "0x1234",
                        "networkId": "ethereum-mainnet",
                        "coinGeckoId": "test-token",
                        "sourceIds": [
                            "market-coingecko",
                            "market-dexscreener",
                        ],
                    }
                ],
                "contractResults": [
                    {
                        "provider": "goplus",
                        "status": "success",
                        "networkId": "ethereum-mainnet",
                        "evidence": [],
                    }
                ],
                "errors": [],
            }
        )
    }
    assert set(stats) == set(TASK_SOURCE_IDS["tradeability"])
    assert stats["market-coingecko"]["status"] == "success"
    assert stats["market-dexscreener"]["status"] == "success"
    assert stats["contract-identity-mapping"]["status"] == "success"
    assert stats["security-goplus"]["status"] == "success"
    assert stats["chain-robinhood-blockscout"]["status"] == "no_data"

    failed = {
        item["sourceId"]: item
        for item in formal_market_dependency_stats(
            {
                "records": [
                    {
                        "asset_id": "asset-test",
                        "coinGeckoId": "test-token",
                        "sourceIds": [],
                    }
                ],
                "contractResults": [],
                "errors": [
                    {"provider": "coingecko", "error": "timeout"},
                ],
            }
        )
    }
    assert failed["market-coingecko"]["status"] == "failed"
    assert failed["market-coingecko"]["failedCount"] == 1


def test_project_source_results_do_not_inherit_unrelated_global_failures():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        initialize_database(db_path, root / "runtime.js", backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            import_candidates(connection, load_fixture())
            create_run(
                connection,
                "tracking-run-project-sources",
                source_ids=TASK_SOURCE_IDS["tradeability"],
            )
            connection.execute(
                """
                UPDATE run_source_stats
                SET status = 'partial_success', failed_count = 113,
                    error_message = 'GoPlus did not return this contract'
                WHERE run_id = 'tracking-run-project-sources'
                  AND source_id = 'security-goplus'
                """
            )
            connection.commit()

            unaffected = source_results(
                connection,
                "tracking-run-project-sources",
                TASK_SOURCE_IDS["tradeability"],
                task(),
            )
            by_source = {item["sourceId"]: item for item in unaffected}
            assert by_source["security-goplus"]["status"] == "no_data"
            assert by_source["security-goplus"]["failedCount"] == 0
            assert execution_status_for(unaffected, []) == "no_change"

            connection.execute(
                """
                INSERT INTO raw_events (
                  raw_event_id, source_id, ingestion_run_id, external_id,
                  published_at, collected_at, content_hash, source_url,
                  excerpt, project_hint, asset_hint, chain_hint, event_type,
                  raw_payload_json, status
                )
                VALUES (
                  'tracking-goplus-pending', 'contract-identity-mapping',
                  'tracking-run-project-sources', 'contract-check-1',
                  '2026-07-30T03:00:00Z', '2026-07-30T04:00:00Z',
                  'contract-check-pending', '', 'contract check pending',
                  'thread-uni-20260728', 'UNI', 'Ethereum',
                  'contract_tradeability_check',
                  '{"provider":"contract_mapping","status":"pending","evidence":[{"label":"contract security API","status":"pending","detail":"RuntimeError: GoPlus did not return this contract"}]}',
                  'normalized'
                )
                """
            )
            connection.commit()
            affected = source_results(
                connection,
                "tracking-run-project-sources",
                TASK_SOURCE_IDS["tradeability"],
                task(),
            )
            by_source = {item["sourceId"]: item for item in affected}
            assert by_source["contract-identity-mapping"]["status"] == "success"
            assert by_source["security-goplus"]["status"] == "partial_success"
            assert by_source["security-goplus"]["failedCount"] == 1
            assert execution_status_for(affected, []) == "partial_success"
        finally:
            connection.close()


def test_formal_market_event_is_a_tracking_finding():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        tracking_path = root / "tracking.js"
        initialize_database(db_path, root / "runtime.js", backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            import_candidates(connection, load_fixture())
            create_run(
                connection,
                "tracking-run-formal-market",
                source_ids=TASK_SOURCE_IDS["tradeability"],
            )
            create_run(
                connection,
                "tracking-run-formal-market-previous",
                source_ids=TASK_SOURCE_IDS["tradeability"],
            )
            connection.execute(
                """
                INSERT INTO sources (
                  source_id, name, source_type, url, access_method, scope,
                  confidence, conflict_risk, status, schedule_text,
                  created_at, updated_at
                )
                VALUES (
                  'formal-project-market-exit-enrichment',
                  '正式项目市场与退出资料', 'test', '', 'fixture',
                  'convexity', '中', '低', 'active', 'test',
                  '2026-07-30T04:00:00Z', '2026-07-30T04:00:00Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO raw_events (
                  raw_event_id, source_id, ingestion_run_id, external_id,
                  published_at, collected_at, content_hash, source_url,
                  excerpt, project_hint, asset_hint, chain_hint, event_type,
                  raw_payload_json, status
                )
                VALUES (
                  'formal-market-uniswap',
                  'formal-project-market-exit-enrichment',
                  'tracking-run-formal-market', 'formal-market-1',
                  '2026-07-30T03:00:00Z', '2026-07-30T04:00:00Z',
                  'formal-market-change', 'https://example.com/market',
                  '24小时成交额发生变化', 'Uniswap', 'UNI', 'Ethereum',
                  'formal_project_market_exit_enrichment',
                  '{"sourceIds":["market-coingecko"],"changes":[{"field":"24小时成交额"}]}',
                  'normalized'
                )
                """
            )
            for raw_event_id, ingestion_run_id, collected_at in (
                (
                    "formal-market-uniswap-stable-previous",
                    "tracking-run-formal-market-previous",
                    "2026-07-29T04:00:00Z",
                ),
                (
                    "formal-market-uniswap-stable-current",
                    "tracking-run-formal-market",
                    "2026-07-30T04:00:00Z",
                ),
            ):
                connection.execute(
                    """
                    INSERT INTO raw_events (
                      raw_event_id, source_id, ingestion_run_id, external_id,
                      published_at, collected_at, content_hash, source_url,
                      excerpt, project_hint, asset_hint, chain_hint, event_type,
                      raw_payload_json, status
                    )
                    VALUES (?, 'formal-project-market-exit-enrichment', ?, ?,
                            ?, ?, 'formal-market-stable', 'https://example.com/market',
                            'stable market record', 'Uniswap', 'UNI', 'Ethereum',
                            'formal_project_market_exit_enrichment',
                            '{"sourceIds":["market-coingecko"],"changes":[]}',
                            'normalized')
                    """,
                    (
                        raw_event_id,
                        ingestion_run_id,
                        raw_event_id,
                        collected_at,
                        collected_at,
                    ),
                )
            connection.commit()
        finally:
            connection.close()

        write_tracking_snapshot(
            tracking_path,
            [{**task(), "taskType": "tradeability"}],
        )
        result = execute_tracking_tasks(
            db_path=db_path,
            run_id="tracking-run-formal-market",
            snapshot_path=tracking_path,
            now=datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc),
        )
        assert result["completed"] == 1
        assert result["results"][0]["findings"] == 2
        assert result["results"][0]["newFindings"] == 1


def main():
    test_execute_continue_failure_and_upgrade()
    test_not_due_is_explicit_skip()
    test_decision_follow_up_rules()
    test_production_tracking_runs_formal_market_dependencies()
    test_project_source_results_do_not_inherit_unrelated_global_failures()
    test_formal_market_event_is_a_tracking_finding()
    print("C1.3-08 跟踪任务执行、回写、失败重试与升级判定测试通过。")


if __name__ == "__main__":
    main()
