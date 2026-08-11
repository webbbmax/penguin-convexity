#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_tracking_tasks_snapshot import (
    DEFAULT_OUTPUT_PATH,
    OUTPUT_PREFIX,
    load_js_payload,
)
from init_db import DEFAULT_DB_PATH


TASK_SOURCE_IDS = {
    "foundation": [
        "evidence-github-official",
        "evidence-link-health",
        "identity-coingecko-registry",
    ],
    "pre_signal": [
        "evidence-github-official",
        "evidence-defillama-protocols",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
    ],
    "balanced_research": [
        "evidence-github-official",
        "evidence-defillama-protocols",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
        "evidence-link-health",
    ],
    "tradeability": [
        "market-coingecko",
        "market-dexscreener",
        "contract-identity-mapping",
        "security-goplus",
        "chain-robinhood-blockscout",
    ],
    "identity": [
        "identity-coingecko-registry",
        "contract-identity-mapping",
        "evidence-link-health",
    ],
    "risk_review": [
        "security-goplus",
        "contract-identity-mapping",
        "market-dexscreener",
    ],
    "mismatch_scoring": [
        "market-coingecko",
        "market-dexscreener",
        "evidence-github-official",
        "evidence-defillama-protocols",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
    ],
    "value_capture": [
        "evidence-defillama-protocols",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
        "evidence-github-official",
    ],
    "model_refresh": [
        "market-coingecko",
        "market-dexscreener",
        "evidence-github-official",
        "evidence-defillama-protocols",
    ],
    "odds_review": [
        "market-coingecko",
        "market-dexscreener",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
    ],
    "execution_monitor": [
        "market-coingecko",
        "market-dexscreener",
        "evidence-defillama-protocols",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
    ],
    "reflexive_management": [
        "market-coingecko",
        "market-dexscreener",
        "evidence-defillama-protocols",
    ],
    "closed_review": [
        "evidence-github-official",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
    ],
    "verify_upgrade": [
        "market-coingecko",
        "market-dexscreener",
        "contract-identity-mapping",
        "security-goplus",
        "evidence-defillama-protocols",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
    ],
    "verify_stop": [
        "evidence-github-official",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
        "evidence-defillama-protocols",
        "market-coingecko",
    ],
    "rejected_recheck": [
        "evidence-github-official",
        "evidence-defillama-protocols",
        "evidence-snapshot-governance",
        "evidence-cactus-governance",
        "evidence-link-health",
        "market-coingecko",
        "market-dexscreener",
        "contract-identity-mapping",
    ],
}

FORMAL_MARKET_EXIT_SOURCE_ID = "formal-project-market-exit-enrichment"


def utc_now():
    return datetime.now(timezone.utc)


def iso_time(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_json(value, fallback=None):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return [] if fallback is None else fallback


def stable_id(*parts):
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"tracking-result-{digest}"


def latest_execution(connection, tracking_task_id, exclude_run_id=""):
    parameters = [tracking_task_id]
    exclusion = ""
    if exclude_run_id:
        exclusion = "AND run_id <> ?"
        parameters.append(exclude_run_id)
    return connection.execute(
        f"""
        SELECT *
        FROM tracking_task_runs
        WHERE tracking_task_id = ?
          {exclusion}
        ORDER BY finished_at DESC, tracking_result_id DESC
        LIMIT 1
        """,
        parameters,
    ).fetchone()


def _project_event_rows(connection, run_id, task):
    project_hints = list(
        dict.fromkeys(
            value
            for value in (
                task.get("caseId"),
                task.get("projectId"),
                task.get("projectName"),
            )
            if value
        )
    )
    if not project_hints:
        return []
    placeholders = ",".join("?" for _ in project_hints)
    return connection.execute(
        f"""
        SELECT source_id, status, raw_payload_json
        FROM raw_events
        WHERE ingestion_run_id = ?
          AND project_hint IN ({placeholders})
        """,
        (run_id, *project_hints),
    ).fetchall()


def _project_source_state(source_id, event_rows):
    parsed_rows = [
        (row, parse_json(row["raw_payload_json"], {}))
        for row in event_rows
    ]
    if source_id in {"market-coingecko", "market-dexscreener"}:
        for row, payload in parsed_rows:
            if (
                row["source_id"] == FORMAL_MARKET_EXIT_SOURCE_ID
                and source_id in (payload.get("sourceIds") or [])
            ):
                return "success", 1, 0, ""

    direct = [
        (row, payload)
        for row, payload in parsed_rows
        if row["source_id"] == source_id
    ]
    if direct:
        failures = []
        for row, payload in direct:
            payload_status = str(payload.get("status") or "").lower()
            if row["status"] == "failed" or payload_status in {
                "failed",
                "error",
                "unavailable",
            }:
                failures.append(
                    str(payload.get("error") or payload.get("detail") or "project source failed")
                )
            elif source_id == "security-goplus" and payload_status == "pending":
                failures.append("GoPlus project result pending")
        if failures:
            return "partial_success", len(direct), len(failures), failures[0]
        return "success", len(direct), 0, ""

    if source_id == "security-goplus":
        failures = []
        for _row, payload in parsed_rows:
            for evidence in payload.get("evidence") or []:
                if not isinstance(evidence, dict):
                    continue
                marker = " ".join(
                    str(evidence.get(key) or "")
                    for key in ("label", "detail")
                ).lower()
                if (
                    "goplus" in marker
                    and evidence.get("status") in {
                        "pending",
                        "failed",
                        "error",
                        "unavailable",
                    }
                ):
                    failures.append(str(evidence.get("detail") or "GoPlus project result pending"))
        if failures:
            return "partial_success", 0, 1, failures[0]

    if source_id == "contract-identity-mapping" and any(
        row["source_id"]
        in {
            "contract-identity-mapping",
            "security-goplus",
            "chain-robinhood-blockscout",
        }
        for row, _payload in parsed_rows
    ):
        return "success", 1, 0, ""
    return "no_data", 0, 0, ""


def source_results(connection, run_id, source_ids, task=None):
    rows = {
        row["source_id"]: row
        for row in connection.execute(
            """
            SELECT stat.*, source.name AS source_name
            FROM run_source_stats stat
            LEFT JOIN sources source ON source.source_id = stat.source_id
            WHERE stat.run_id = ?
            """,
            (run_id,),
        )
        if row["source_id"]
    }
    event_rows = _project_event_rows(connection, run_id, task) if task else []
    results = []
    for source_id in source_ids:
        row = rows.get(source_id)
        project_status = None
        project_matched = 0
        project_failed = 0
        project_error = ""
        if task and row:
            (
                project_status,
                project_matched,
                project_failed,
                project_error,
            ) = _project_source_state(source_id, event_rows)
            if project_status == "no_data" and row["status"] == "failed":
                project_status = "failed"
                project_failed = max(1, int(row["failed_count"] or 0))
                project_error = row["error_message"] or "source failed"
        results.append(
            {
                "sourceId": source_id,
                "sourceName": (
                    row["source_name"]
                    if row and row["source_name"]
                    else source_id
                ),
                "status": project_status or (row["status"] if row else "not_run"),
                "collectedCount": (
                    project_matched
                    if task and row
                    else int(row["collected_count"] or 0) if row else 0
                ),
                "matchedCount": (
                    project_matched
                    if task and row
                    else int(row["matched_count"] or 0) if row else 0
                ),
                "failedCount": (
                    project_failed
                    if task and row
                    else int(row["failed_count"] or 0) if row else 0
                ),
                "error": (
                    project_error
                    if task and row
                    else row["error_message"] if row else ""
                ),
            }
        )
    return results


def task_findings(connection, run_id, task, source_ids):
    project_hints = list(
        dict.fromkeys(
            value
            for value in (
                task.get("caseId"),
                task.get("projectId"),
                task.get("projectName"),
            )
            if value
        )
    )
    event_source_ids = list(
        dict.fromkeys([*source_ids, FORMAL_MARKET_EXIT_SOURCE_ID])
    )
    project_placeholders = ",".join("?" for _ in project_hints)
    source_placeholders = ",".join("?" for _ in event_source_ids)
    rows = connection.execute(
        f"""
        SELECT event.*, source.name AS source_name
        FROM raw_events event
        LEFT JOIN sources source ON source.source_id = event.source_id
        WHERE event.ingestion_run_id = ?
          AND event.project_hint IN ({project_placeholders})
          AND event.source_id IN ({source_placeholders})
        ORDER BY event.collected_at DESC, event.raw_event_id DESC
        """,
        (run_id, *project_hints, *event_source_ids),
    ).fetchall()
    findings = []
    for row in rows:
        payload = parse_json(row["raw_payload_json"], {})
        payload_source_ids = payload.get("sourceIds")
        if not isinstance(payload_source_ids, list):
            payload_source_ids = []
        if (
            row["source_id"] == FORMAL_MARKET_EXIT_SOURCE_ID
            and not set(payload_source_ids).intersection(source_ids)
        ):
            continue
        changes = payload.get("changes")
        if not isinstance(changes, list):
            changes = []
        existed = connection.execute(
            """
            SELECT 1
            FROM raw_events
            WHERE source_id = ?
              AND project_hint = ?
              AND content_hash = ?
              AND ingestion_run_id <> ?
            LIMIT 1
            """,
            (
                row["source_id"],
                row["project_hint"],
                row["content_hash"],
                run_id,
            ),
        ).fetchone()
        is_new = bool(changes) or not existed
        findings.append(
            {
                "evidenceId": row["raw_event_id"],
                "sourceId": row["source_id"],
                "sourceName": row["source_name"] or row["source_id"],
                "eventType": row["event_type"],
                "summary": row["excerpt"] or payload.get("summary") or "已完成核验。",
                "sourceUrl": row["source_url"],
                "observedAt": row["published_at"] or row["collected_at"],
                "collectedAt": row["collected_at"],
                "isNew": is_new,
                "changes": changes,
            }
        )
    return findings


def execution_status_for(results, findings):
    ran = [item for item in results if item["status"] != "not_run"]
    failed = [
        item
        for item in ran
        if item["status"] == "failed" or item["failedCount"] > 0
    ]
    if not ran or (failed and len(failed) == len(ran)):
        return "failed"
    if failed or any(item["status"] == "partial_success" for item in ran):
        return "partial_success"
    return "success" if findings else "no_change"


def decision_for(task, previous, execution_status, new_count):
    current_action = task.get("currentAction") or "只观察"
    previous_conclusion = previous["conclusion_after"] if previous else ""
    if execution_status == "failed":
        return (
            "undetermined",
            "映射信源本轮未完成，不能据此升级、继续或停止项目。",
        )
    follow_up = task.get("decisionFollowUp") or {}
    if (
        follow_up.get("required")
        and follow_up.get("status") in {"pending", "failed"}
    ):
        if follow_up.get("type") == "rejected_recheck":
            if new_count:
                return (
                    "continue",
                    f"围绕驳回原因发现{new_count}条新增或变化证据，"
                    "本轮只完成重新取证，不复用已驳回结论。",
                )
            return (
                "continue",
                "没有发现足以推翻驳回理由的新增独立证据，保持上一结论。",
            )
        if follow_up.get("type") == "verify_upgrade":
            if current_action in {"普通建仓", "极限试仓", "反身性管理"}:
                return (
                    "monitor",
                    f"二次验证后仍维持{current_action}，继续监测确认信号、"
                    "交易性和失效条件。",
                )
            return (
                "stop",
                "二次验证时上调后的行动条件已经不再成立，需要重新复核停止。",
            )
        if current_action == "失效/排除" or task.get("status") == "closed":
            return (
                "monitor",
                "二次验证确认停止条件仍然成立，保留失效证据并继续低频监测。",
            )
        if current_action in {"普通建仓", "极限试仓", "反身性管理"}:
            return (
                "upgrade",
                f"停止结论二次验证时发现行动状态已恢复为{current_action}，"
                "需要重新进入上调复核。",
            )
        return (
            "continue",
            "停止结论二次验证发现了重新研究线索，但尚未恢复到行动级。",
        )
    if current_action == "失效/排除" or task.get("status") == "closed":
        return "stop", "当前行动结论已经进入失效/排除，停止常规跟踪。"
    if (
        previous_conclusion.startswith("只观察")
        and current_action in {"普通建仓", "极限试仓", "反身性管理"}
    ):
        return (
            "upgrade",
            f"项目行动结论由只观察变为{current_action}，需要进入对应行动复核。",
        )
    if current_action in {"普通建仓", "极限试仓", "反身性管理"}:
        return (
            "monitor",
            f"维持{current_action}，继续检查确认信号、流动性与失效条件。",
        )
    if new_count:
        return (
            "continue",
            f"本轮发现{new_count}条新增或变化证据，但尚未满足自动升级条件，继续跟踪。",
        )
    return "continue", "已完成本轮检查，未发现足以改变当前行动结论的新证据。"


def persist_result(connection, run_id, task, now, forced=False):
    source_ids = TASK_SOURCE_IDS.get(
        task.get("taskType"),
        TASK_SOURCE_IDS["balanced_research"],
    )
    results = source_results(connection, run_id, source_ids, task)
    findings = task_findings(connection, run_id, task, source_ids)
    new_count = sum(item["isNew"] for item in findings)
    execution_status = execution_status_for(results, findings)
    previous = latest_execution(
        connection,
        task["taskId"],
        exclude_run_id=run_id,
    )
    decision, reason = decision_for(
        task,
        previous,
        execution_status,
        new_count,
    )
    next_review = (
        now
        if execution_status == "failed"
        else now + timedelta(days=int(task.get("reviewCadenceDays") or 1))
    )
    attempts = int(previous["attempts"] or 1) + 1 if forced and previous else 1
    retryable = execution_status in {"failed", "partial_success"}
    error_message = ""
    if execution_status == "failed":
        failed_names = [
            item["sourceName"]
            for item in results
            if item["status"] in {"failed", "not_run"} or item["failedCount"]
        ]
        error_message = (
            "没有映射信源完成本轮检查。"
            if not failed_names
            else f"未完成信源：{'、'.join(failed_names)}"
        )
    result_id = stable_id(task["taskId"], run_id)
    conclusion_after = task.get("currentConclusion", "")
    follow_up = task.get("decisionFollowUp") or {}
    if (
        follow_up.get("type") == "rejected_recheck"
        and follow_up.get("status") in {"pending", "failed"}
    ):
        conclusion_after = (
            follow_up.get("conclusionBefore")
            or task.get("currentConclusion", "")
        )
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?)
        """,
        (
            result_id,
            task["taskId"],
            task["caseId"],
            task.get("projectId") or None,
            run_id,
            task.get("projectCategory") or "hybrid",
            task.get("taskType") or "balanced_research",
            task.get("priority") or "P2",
            execution_status,
            decision,
            previous["conclusion_after"] if previous else task.get("currentConclusion", ""),
            conclusion_after,
            reason,
            json.dumps(source_ids, ensure_ascii=False),
            json.dumps(results, ensure_ascii=False),
            json.dumps(findings, ensure_ascii=False),
            len(findings),
            new_count,
            iso_time(now),
            iso_time(now),
            iso_time(next_review),
            int(retryable),
            "pending" if retryable else "not_requested",
            attempts,
            error_message,
            "C1.3-08",
        ),
    )
    if execution_status != "failed":
        connection.execute(
            """
            UPDATE candidate_cases
            SET next_review_at = ?, updated_at = ?
            WHERE case_id = ?
            """,
            (iso_time(next_review), iso_time(now), task["caseId"]),
        )
    if previous and previous["retryable"]:
        connection.execute(
            """
            UPDATE tracking_task_runs
            SET retry_status = ?
            WHERE tracking_result_id = ?
            """,
            (
                "succeeded" if execution_status != "failed" else "failed",
                previous["tracking_result_id"],
            ),
        )
    return {
        "trackingResultId": result_id,
        "trackingTaskId": task["taskId"],
        "caseId": task["caseId"],
        "projectId": task.get("projectId") or "",
        "projectName": task.get("projectName") or task["caseId"],
        "status": execution_status,
        "decision": decision,
        "reason": reason,
        "findings": len(findings),
        "newFindings": new_count,
        "nextReviewAt": iso_time(next_review),
        "retryable": retryable,
    }


def execute_tracking_tasks(
    db_path=DEFAULT_DB_PATH,
    run_id="",
    snapshot_path=DEFAULT_OUTPUT_PATH,
    tracking_task_id="",
    force=False,
    now=None,
):
    if not run_id:
        raise ValueError("缺少本次更新运行ID。")
    snapshot = load_js_payload(snapshot_path, OUTPUT_PREFIX)
    tasks = snapshot.get("tasks") or []
    if tracking_task_id:
        tasks = [item for item in tasks if item["taskId"] == tracking_task_id]
        if not tasks:
            raise ValueError("没有找到指定的项目跟踪任务。")
    selected = [
        item
        for item in tasks
        if force or tracking_task_id or item.get("status") == "due"
    ]
    now = now or utc_now()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        results = [
            persist_result(
                connection,
                run_id,
                task,
                now,
                forced=bool(force or tracking_task_id),
            )
            for task in selected
        ]
        failed = sum(item["status"] == "failed" for item in results)
        partial = sum(item["status"] == "partial_success" for item in results)
        if failed or partial:
            row = connection.execute(
                "SELECT status, error_count FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row:
                connection.execute(
                    """
                    UPDATE runs
                    SET status = CASE
                          WHEN status = 'failed' THEN status
                          ELSE 'partial_success'
                        END,
                        error_count = error_count + ?,
                        error_summary = CASE
                          WHEN error_summary = '' THEN ?
                          ELSE error_summary || '；' || ?
                        END
                    WHERE run_id = ?
                    """,
                    (
                        failed + partial,
                        f"项目跟踪未完整完成 {failed + partial} 项",
                        f"项目跟踪未完整完成 {failed + partial} 项",
                        run_id,
                    ),
                )
        connection.commit()
    finally:
        connection.close()
    return {
        "version": "C1.3-08",
        "runId": run_id,
        "eligible": len(selected),
        "notDue": len(tasks) - len(selected),
        "completed": sum(
            item["status"] in {"success", "no_change"} for item in results
        ),
        "partial": sum(item["status"] == "partial_success" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "upgraded": sum(item["decision"] == "upgrade" for item in results),
        "continued": sum(item["decision"] == "continue" for item in results),
        "stopped": sum(item["decision"] == "stop" for item in results),
        "monitored": sum(item["decision"] == "monitor" for item in results),
        "results": results,
        "explanation": (
            "没有项目到达复查时间，本轮未重复检查。"
            if not selected
            else (
                f"项目跟踪执行 {len(selected)} 项：完整完成"
                f"{sum(item['status'] in {'success', 'no_change'} for item in results)}项，"
                f"部分完成{sum(item['status'] == 'partial_success' for item in results)}项，"
                f"失败{sum(item['status'] == 'failed' for item in results)}项。"
            )
        ),
    }


def main():
    parser = argparse.ArgumentParser(description="执行C1.3-08项目跟踪任务")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--tracking-task", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            execute_tracking_tasks(
                db_path=args.db,
                run_id=args.run_id,
                snapshot_path=args.snapshot,
                tracking_task_id=args.tracking_task,
                force=args.force,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
