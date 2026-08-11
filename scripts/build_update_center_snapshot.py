#!/usr/bin/env python3
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH, ZERO_RESULT_LABELS
from update_tasks import (
    SOURCE_BOUNDARIES,
    TASK_DEFINITIONS,
    task_for_source,
    task_id_for_job,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UPDATE_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "update-center-snapshot.js"
DEFAULT_SOURCE_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "source-registry-snapshot.js"

STATUS_LABELS = {
    "running": "运行中",
    "success": "成功",
    "partial_success": "部分完成",
    "failed": "失败",
    "skipped": "已跳过",
    "no_data": "没有返回数据",
    "never_run": "尚未运行",
    "active": "正常",
    "paused": "已暂停",
    "planned": "待接入",
    "error": "异常",
    "continuing": "续扫中",
    "review": "待人工复核",
    "restricted": "访问受限",
    "completed_continuing": "已完成，可继续续扫",
    "completed_review": "已完成，存在待复核项",
}

EVENT_LABELS = {
    "market_snapshot_refresh": "市场快照",
    "market_mapping_skip": "市场映射跳过",
    "market_refresh_error": "市场更新失败",
    "evidence_link_check": "证据链接",
    "official_code_activity": "官方代码痕迹",
    "official_security_activity": "官方安全相关代码活动",
    "protocol_adoption_snapshot": "协议采用指标",
    "offchain_governance_proposal": "链下治理提案",
    "onchain_governance_proposal": "链上治理提案",
    "project_source_discovery": "项目级发现",
    "contract_tradeability_check": "合约与卖出路径",
    "network_discovery": "链上发现",
    "formal_project_profile_enrichment": "身份与官方入口",
    "project_asset_identity_refresh": "项目资产身份",
    "formal_project_market_exit_enrichment": "市场与退出资料",
    "formal_project_research_material": "正式项目研究资料",
    "machine_research_scoring_refresh": "机器研究评分",
    "machine_conclusion_publish": "机器状态与结论",
}

TRACKING_STATUS_LABELS = {
    "success": "已发现有效记录",
    "partial_success": "部分信源未完成",
    "no_change": "已检查，暂无新增",
    "failed": "检查失败",
}

TRACKING_DECISION_LABELS = {
    "upgrade": "升级复核",
    "continue": "继续跟踪",
    "stop": "停止跟踪",
    "monitor": "行动后监测",
    "undetermined": "暂无法判定",
}

TRACKING_TASK_TYPE_LABELS = {
    "foundation": "基础档案补齐",
    "pre_signal": "前置信号监测",
    "balanced_research": "基础与信号并行补齐",
    "tradeability": "交易性补证",
    "identity": "身份补证",
    "risk_review": "风险核验",
    "mismatch_scoring": "错配评分补证",
    "value_capture": "价值捕获核验",
    "model_refresh": "模型刷新",
    "odds_review": "赔率复核",
    "execution_monitor": "行动后监测",
    "reflexive_management": "反身性管理",
    "closed_review": "失效复核",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_json(value, fallback=None):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def update_job_names():
    return {
        name
        for definition in TASK_DEFINITIONS.values()
        for name in [definition["jobName"], *definition.get("legacyJobNames", [])]
    }


def source_stat_presentation(row):
    summary = parse_json(row["filter_reason_summary_json"])
    source_id = row["source_id"] or ""
    failed_count = int(row["failed_count"] or 0)
    identity_conflicts = int(summary.get("identityConflict") or 0)
    unmapped = int(summary.get("unmapped") or 0)
    restricted = int(summary.get("restricted") or 0)
    if row["status"] == "failed" or failed_count:
        task_id = task_for_source(source_id)
        return {
            "displayStatus": "failed",
            "displayStatusLabel": "失败",
            "actionKind": "retry",
            "actionTaskId": task_id,
            "actionHref": "",
            "actionLabel": "只重试这个来源",
        }
    if summary.get("incomplete"):
        return {
            "displayStatus": "continuing",
            "displayStatusLabel": "本轮完成，可继续扫描",
            "actionKind": "continue",
            "actionTaskId": task_for_source(source_id),
            "actionHref": "",
            "actionLabel": "继续扫描",
        }
    if identity_conflicts or unmapped:
        return {
            "displayStatus": "review",
            "displayStatusLabel": "待人工复核",
            "actionKind": "review",
            "actionTaskId": "",
            "actionHref": "manual-review.html?queue=must_handle",
            "actionLabel": "进入必须处理",
        }
    if restricted:
        return {
            "displayStatus": "restricted",
            "displayStatusLabel": "访问受限，已保留旧数据",
            "actionKind": "none",
            "actionTaskId": "",
            "actionHref": "",
            "actionLabel": "",
        }
    if row["status"] == "partial_success":
        task_id = task_for_source(source_id)
        return {
            "displayStatus": "partial_success",
            "displayStatusLabel": "部分完成",
            "actionKind": "retry",
            "actionTaskId": task_id,
            "actionHref": "",
            "actionLabel": "只重试这个来源",
        }
    return {
        "displayStatus": row["status"],
        "displayStatusLabel": STATUS_LABELS.get(row["status"], row["status"]),
        "actionKind": "none",
        "actionTaskId": "",
        "actionHref": "",
        "actionLabel": "",
    }


def run_source_stats(connection, run_id):
    stats = []
    for row in connection.execute(
        """
        SELECT stat.*, source.name AS source_name
        FROM run_source_stats stat
        LEFT JOIN sources source ON source.source_id = stat.source_id
        WHERE stat.run_id = ?
        ORDER BY source.name, stat.collector_id
        """,
        (run_id,),
    ):
        presentation = source_stat_presentation(row)
        stats.append(
            {
                **dict(row),
                "sourceName": row["source_name"] or row["collector_id"],
                "statusLabel": presentation["displayStatusLabel"],
                "filterSummary": parse_json(row["filter_reason_summary_json"]),
                **presentation,
            }
        )
    return stats


def run_errors(connection, run_id):
    errors = [
        {
            **dict(row),
            "sourceName": row["source_name"] or row["source_id"] or "内部任务",
            "taskNameLabel": (
                TASK_DEFINITIONS[row["task_name"]]["label"]
                if row["task_name"] in TASK_DEFINITIONS
                else row["task_name"]
            ),
            "retryTaskId": (
                row["task_name"]
                if row["task_name"] in TASK_DEFINITIONS
                else task_for_source(row["source_id"] or "")
            ),
        }
        for row in connection.execute(
            """
            SELECT error.*, source.name AS source_name
            FROM run_errors error
            LEFT JOIN sources source ON source.source_id = error.source_id
            WHERE error.run_id = ?
            ORDER BY error.last_seen_at DESC, error.task_name
            """,
            (run_id,),
        )
    ]
    tracking = connection.execute(
        """
        SELECT COUNT(*) AS affected_count,
               SUM(execution_status = 'failed') AS failed_count,
               SUM(execution_status = 'partial_success') AS partial_count,
               MIN(started_at) AS first_seen_at,
               MAX(finished_at) AS last_seen_at,
               MAX(attempts) AS attempts
        FROM tracking_task_runs
        WHERE run_id = ?
          AND execution_status IN ('failed', 'partial_success')
        """,
        (run_id,),
    ).fetchone()
    if tracking and int(tracking["affected_count"] or 0):
        affected = int(tracking["affected_count"] or 0)
        failed = int(tracking["failed_count"] or 0)
        partial = int(tracking["partial_count"] or 0)
        errors.append(
            {
                "error_id": f"tracking-aggregate:{run_id}",
                "run_id": run_id,
                "source_id": "",
                "task_name": "tracking_task_refresh",
                "error_type": "tracking_incomplete",
                "message": (
                    f"项目跟踪未完整完成 {affected} 项："
                    f"失败 {failed} 项，部分完成 {partial} 项。"
                ),
                "retryable": 1,
                "retry_status": "not_requested",
                "attempts": int(tracking["attempts"] or 1),
                "first_seen_at": tracking["first_seen_at"],
                "last_seen_at": tracking["last_seen_at"],
                "sourceName": "项目跟踪",
                "taskNameLabel": TASK_DEFINITIONS[
                    "tracking_task_refresh"
                ]["label"],
                "retryTaskId": "tracking_task_refresh",
            }
        )
    return errors


def change_items(connection, run_ids):
    if not run_ids:
        return []
    placeholders = ",".join("?" for _ in run_ids)
    items = []
    for row in connection.execute(
        f"""
        SELECT event.*, source.name AS source_name, run.job_name
        FROM raw_events event
        JOIN runs run ON run.run_id = event.ingestion_run_id
        LEFT JOIN sources source ON source.source_id = event.source_id
        WHERE event.ingestion_run_id IN ({placeholders})
        ORDER BY event.collected_at DESC, event.raw_event_id DESC
        """,
        run_ids,
    ):
        payload = parse_json(row["raw_payload_json"])
        changes = payload.get("changes") if isinstance(payload.get("changes"), list) else []
        items.append(
            {
                "changeId": row["raw_event_id"],
                "runId": row["ingestion_run_id"],
                "taskId": task_for_source(row["source_id"])
                or task_id_for_job(row["job_name"]),
                "sourceId": row["source_id"],
                "sourceName": row["source_name"] or row["source_id"],
                "eventType": row["event_type"],
                "eventLabel": EVENT_LABELS.get(
                    row["event_type"],
                    row["event_type"] or "数据记录",
                ),
                "projectKey": row["project_hint"],
                "assetKey": row["asset_hint"],
                "chain": row["chain_hint"],
                "summary": row["excerpt"] or payload.get("summary") or "数据已刷新。",
                "sourceUrl": row["source_url"],
                "status": row["status"],
                "changes": changes,
                "collectedAt": row["collected_at"],
            }
        )

    for row in connection.execute(
        f"""
        SELECT review.*, discovery.token_name, discovery.symbol,
               discovery.network_id, run.job_name
        FROM discovery_identity_reviews review
        JOIN network_discoveries discovery
          ON discovery.discovery_id = review.discovery_id
        JOIN runs run ON run.run_id = review.run_id
        WHERE review.run_id IN ({placeholders})
        ORDER BY review.reviewed_at DESC, review.identity_review_id DESC
        """,
        run_ids,
    ):
        items.append(
            {
                "changeId": row["identity_review_id"],
                "runId": row["run_id"],
                "taskId": "identity_refresh",
                "sourceId": "identity-coingecko-registry",
                "sourceName": "CoinGecko 资产身份注册",
                "eventType": "identity_review",
                "eventLabel": "身份复核",
                "projectKey": row["token_name"] or row["discovery_id"],
                "assetKey": row["symbol"],
                "chain": row["network_id"],
                "summary": row["reason"],
                "sourceUrl": row["website_url"],
                "status": row["resolution_status"],
                "changes": [],
                "collectedAt": row["reviewed_at"],
            }
        )
    items.sort(
        key=lambda item: (item["collectedAt"], item["changeId"]),
        reverse=True,
    )
    return items


def build_runs(connection):
    names = sorted(update_job_names())
    placeholders = ",".join("?" for _ in names)
    runs = []
    for row in connection.execute(
        f"""
        SELECT *
        FROM runs
        WHERE job_name IN ({placeholders})
        ORDER BY started_at DESC, run_id DESC
        """,
        names,
    ):
        task_id = task_id_for_job(row["job_name"])
        errors = run_errors(connection, row["run_id"])
        source_stats = run_source_stats(connection, row["run_id"])
        source_actions = {item["actionKind"] for item in source_stats}
        if row["status"] == "running":
            display_status = "running"
        elif row["status"] == "failed" or row["error_count"] or "retry" in source_actions:
            display_status = row["status"]
        elif "continue" in source_actions:
            display_status = "completed_continuing"
        elif "review" in source_actions:
            display_status = "completed_review"
        else:
            display_status = row["status"]
        runs.append(
            {
                **dict(row),
                "taskId": task_id,
                "taskLabel": TASK_DEFINITIONS[task_id]["label"],
                "displayStatus": display_status,
                "statusLabel": STATUS_LABELS.get(display_status, display_status),
                "zeroResultLabel": ZERO_RESULT_LABELS.get(
                    row["zero_result_class"],
                    row["zero_result_class"],
                ),
                "sourceStats": source_stats,
                "errors": errors,
                "canRetry": bool(errors)
                or bool(source_actions & {"retry", "continue"}),
                "hasContinuation": "continue" in source_actions,
                "hasReview": "review" in source_actions,
            }
        )
    return runs


def build_tasks(runs, changes):
    tasks = []
    for task_id, definition in sorted(
        TASK_DEFINITIONS.items(),
        key=lambda item: item[1]["order"],
    ):
        task_runs = [run for run in runs if run["taskId"] == task_id]
        latest = task_runs[0] if task_runs else None
        latest_changes = (
            [
                item
                for item in changes
                if latest and item["runId"] == latest["run_id"]
            ]
            if latest
            else []
        )
        source_record_count = (
            sum(
                int(item.get("collected_count") or 0)
                for item in latest["sourceStats"]
            )
            if latest
            else 0
        )
        source_change_count = (
            sum(
                int(item.get("matched_count") or 0)
                for item in latest["sourceStats"]
            )
            if latest
            else 0
        )
        tasks.append(
            {
                "taskId": task_id,
                "label": definition["label"],
                "description": definition["description"],
                "updates": definition["updates"],
                "sourceIds": definition["sourceIds"],
                "components": definition["components"],
                "latestRun": latest,
                "latestRecordCount": (
                    len(latest_changes)
                    if latest_changes
                    else source_record_count
                ),
                "latestChangeCount": (
                    sum(bool(item.get("changes")) for item in latest_changes)
                    if latest_changes
                    else source_change_count
                ),
                "runCount": len(task_runs),
            }
        )
    return tasks


def build_tracking_results(connection):
    results = []
    for row in connection.execute(
        """
        SELECT result.*, project.canonical_name, candidate.title AS case_title,
               run.job_name
        FROM tracking_task_runs result
        LEFT JOIN projects project ON project.project_id = result.project_id
        LEFT JOIN candidate_cases candidate ON candidate.case_id = result.case_id
        JOIN runs run ON run.run_id = result.run_id
        ORDER BY result.finished_at DESC, result.tracking_result_id DESC
        """
    ):
        results.append(
            {
                **dict(row),
                "projectName": (
                    row["canonical_name"]
                    or row["case_title"]
                    or row["case_id"]
                ),
                "statusLabel": TRACKING_STATUS_LABELS.get(
                    row["execution_status"],
                    row["execution_status"],
                ),
                "decisionLabel": TRACKING_DECISION_LABELS.get(
                    row["decision"],
                    row["decision"],
                ),
                "taskTypeLabel": TRACKING_TASK_TYPE_LABELS.get(
                    row["task_type"],
                    row["task_type"],
                ),
                "sourcesChecked": parse_json(
                    row["sources_checked_json"],
                    [],
                ),
                "sourceResults": parse_json(
                    row["source_results_json"],
                    [],
                ),
                "findings": parse_json(row["findings_json"], []),
            }
        )
    return results


def source_record_count(connection, source_id):
    queries = (
        "SELECT COUNT(*) FROM raw_events WHERE source_id = ?",
        "SELECT COUNT(*) FROM evidence_items WHERE source_id = ?",
        "SELECT COUNT(*) FROM asset_contracts WHERE source_id = ?",
        "SELECT COUNT(*) FROM scan_results WHERE source_id = ?",
        "SELECT COUNT(*) FROM source_discoveries WHERE source_id = ?",
    )
    return sum(connection.execute(query, (source_id,)).fetchone()[0] for query in queries)


def latest_source_stat(connection, source_id):
    row = connection.execute(
        """
        SELECT stat.*, run.job_name, run.started_at AS run_started_at
        FROM run_source_stats stat
        JOIN runs run ON run.run_id = stat.run_id
        WHERE stat.source_id = ?
        ORDER BY stat.started_at DESC, stat.run_source_stat_id DESC
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


def latest_source_error(connection, source_id):
    row = connection.execute(
        """
        SELECT error.*, run.job_name
        FROM run_errors error
        JOIN runs run ON run.run_id = error.run_id
        WHERE error.source_id = ?
        ORDER BY error.last_seen_at DESC, error.error_id DESC
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


def build_source_registry_snapshot(connection):
    sources = []
    for row in connection.execute(
        """
        SELECT *
        FROM sources
        WHERE scope LIKE 'convexity%' OR source_id = 'codex-convexity-thread'
        ORDER BY name COLLATE NOCASE, source_id
        """
    ):
        source = dict(row)
        latest_stat = latest_source_stat(connection, source["source_id"])
        latest_error = latest_source_error(connection, source["source_id"])
        record_count = source_record_count(connection, source["source_id"])
        boundary = SOURCE_BOUNDARIES.get(
            source["source_id"],
            {
                "category": source["source_type"],
                "proves": "保存该来源在凸性系统中的采集或研究记录。",
                "doesNotProve": "尚未补充更具体的证据边界，使用时保持待核验。",
            },
        )
        if latest_stat:
            presentation = source_stat_presentation(latest_stat)
            health_status = presentation["displayStatus"]
        elif source["status"] in ("paused", "error", "planned"):
            presentation = None
            health_status = source["status"]
        elif record_count:
            presentation = None
            health_status = "active"
        else:
            presentation = None
            health_status = "never_run"
        task_ids = [
            task_id
            for task_id, definition in TASK_DEFINITIONS.items()
            if task_id != "full_refresh"
            and source["source_id"] in definition["sourceIds"]
        ]
        sources.append(
            {
                **source,
                **boundary,
                "taskIds": task_ids,
                "primaryTaskId": task_for_source(source["source_id"]),
                "recordCount": record_count,
                "healthStatus": health_status,
                "healthStatusLabel": (
                    presentation["displayStatusLabel"]
                    if presentation
                    else STATUS_LABELS.get(health_status, health_status)
                ),
                "latestStat": (
                    {**latest_stat, **presentation}
                    if latest_stat and presentation
                    else latest_stat
                ),
                "latestError": latest_error,
            }
        )
    counts = {
        "total": len(sources),
        "active": sum(item["status"] == "active" for item in sources),
        "healthy": sum(
            item["healthStatus"] in ("success", "active") for item in sources
        ),
        "attention": sum(
            item["healthStatus"] in ("partial_success", "failed", "error", "review")
            for item in sources
        ),
        "continuing": sum(item["healthStatus"] == "continuing" for item in sources),
        "neverRun": sum(item["healthStatus"] == "never_run" for item in sources),
    }
    return {
        "product": "企鹅投研",
        "workspace": "凸性工作台",
        "workbenchVersion": "C1.1",
        "generatedAt": utc_now(),
        "policy": "信源健康、证据边界和更新任务分开记录。来源成功只代表本次取得数据，不代表项目身份或投资逻辑成立。",
        "counts": counts,
        "taskLabels": {
            task_id: definition["label"]
            for task_id, definition in TASK_DEFINITIONS.items()
        },
        "sources": sources,
    }


def build_update_center_snapshot(connection):
    runs = build_runs(connection)
    run_ids = [run["run_id"] for run in runs]
    changes = change_items(connection, run_ids)
    tasks = build_tasks(runs, changes)
    tracking_results = build_tracking_results(connection)
    latest = runs[0] if runs else None
    latest_source_presentations = []
    for row in connection.execute(
        """
        SELECT source_id
        FROM sources
        WHERE scope LIKE 'convexity%'
        """
    ):
        latest_stat = latest_source_stat(connection, row["source_id"])
        if latest_stat:
            latest_source_presentations.append(source_stat_presentation(latest_stat))
    resumable_task_ids = {
        item["actionTaskId"]
        for item in latest_source_presentations
        if item["actionKind"] in ("retry", "continue") and item["actionTaskId"]
    }
    return {
        "product": "企鹅投研",
        "workspace": "凸性工作台",
        "workbenchVersion": "C1.1",
        "generatedAt": utc_now(),
        "policy": "全部更新和单任务更新使用同一套写回规则。单项失败只重试对应任务，其他成功数据和上次有效快照继续保留。",
        "counts": {
            "tasks": len(tasks) - 1,
            "runs": len(runs),
            "changes": len(changes),
            "retryable": len(resumable_task_ids)
            + sum(
                item["retryable"]
                and item["retry_status"] in {
                    "not_requested",
                    "pending",
                    "failed",
                }
                for item in tracking_results
            ),
            "trackingExecutions": len(tracking_results),
            "trackingRetryable": sum(
                item["retryable"]
                and item["retry_status"] in {
                    "not_requested",
                    "pending",
                    "failed",
                }
                for item in tracking_results
            ),
            "continuing": sum(
                item["actionKind"] == "continue"
                for item in latest_source_presentations
            ),
            "review": sum(
                item["actionKind"] == "review"
                for item in latest_source_presentations
            ),
            "sources": connection.execute(
                "SELECT COUNT(*) FROM sources WHERE scope LIKE 'convexity%'"
            ).fetchone()[0],
        },
        "latestRun": latest,
        "tasks": tasks,
        "runs": runs,
        "changes": changes,
        "trackingResults": tracking_results,
        "statusLabels": STATUS_LABELS,
        "eventLabels": EVENT_LABELS,
        "trackingStatusLabels": TRACKING_STATUS_LABELS,
        "trackingDecisionLabels": TRACKING_DECISION_LABELS,
    }


def write_snapshot(variable_name, snapshot, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        f"window.{variable_name} = "
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return path


def rebuild_update_snapshots(
    db_path=DEFAULT_DB_PATH,
    update_path=DEFAULT_UPDATE_SNAPSHOT_PATH,
    source_path=DEFAULT_SOURCE_SNAPSHOT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        update_snapshot = build_update_center_snapshot(connection)
        source_snapshot = build_source_registry_snapshot(connection)
        write_snapshot(
            "PENGUIN_CONVEXITY_UPDATE_CENTER",
            update_snapshot,
            update_path,
        )
        write_snapshot(
            "PENGUIN_CONVEXITY_SOURCE_REGISTRY",
            source_snapshot,
            source_path,
        )
        return update_snapshot, source_snapshot
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成凸性更新中心与信源库快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--update-snapshot",
        type=Path,
        default=DEFAULT_UPDATE_SNAPSHOT_PATH,
    )
    parser.add_argument(
        "--source-snapshot",
        type=Path,
        default=DEFAULT_SOURCE_SNAPSHOT_PATH,
    )
    args = parser.parse_args()
    update_snapshot, source_snapshot = rebuild_update_snapshots(
        db_path=args.db,
        update_path=args.update_snapshot,
        source_path=args.source_snapshot,
    )
    print(
        json.dumps(
            {
                "updateCenter": update_snapshot["counts"],
                "sourceRegistry": source_snapshot["counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
