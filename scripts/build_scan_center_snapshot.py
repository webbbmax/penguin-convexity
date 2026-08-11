#!/usr/bin/env python3
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from contract_tradeability import NETWORKS
from discover_network_tokens import (
    DEFAULT_CONFIG_PATH,
    PROVIDER_CONFIG_KEYS,
    SOURCE_DEFINITIONS,
    load_config,
)
from init_db import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "scan-center-snapshot.js"

SOURCE_LABELS = {
    "dexscreener_profiles": "DexScreener 最新代币资料",
    "dexscreener_boosts": "DexScreener 推广代币",
    "robinhood_registry": "Robinhood Chain 代币注册表",
}

STATUS_LABELS = {
    "running": "运行中",
    "success": "成功",
    "partial_success": "部分成功",
    "failed": "失败",
    "skipped": "已跳过",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_list(value):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def count_statuses(connection, run_id=None, network_id=None, source_id=None):
    clauses = []
    values = []
    for field, value in (
        ("run_id", run_id),
        ("network_id", network_id),
        ("source_id", source_id),
    ):
        if value:
            clauses.append(f"{field} = ?")
            values.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    counts = {
        "total": 0,
        "eligible": 0,
        "pending": 0,
        "existing": 0,
        "rejected": 0,
        "error": 0,
    }
    for row in connection.execute(
        f"""
        SELECT result_status, COUNT(*) AS count
        FROM scan_results
        {where}
        GROUP BY result_status
        """,
        values,
    ):
        counts[row["result_status"]] = row["count"]
        counts["total"] += row["count"]
    return counts


def run_scope(connection, run_id):
    row = connection.execute(
        "SELECT * FROM scan_run_scopes WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row:
        return {
            "networkIds": json_list(row["requested_network_ids_json"]),
            "sourceIds": json_list(row["requested_source_ids_json"]),
            "triggeredBy": row["triggered_by"],
            "noLimit": bool(row["no_limit"]),
            "explicit": True,
        }
    network_ids = [
        item[0]
        for item in connection.execute(
            "SELECT DISTINCT network_id FROM scan_results WHERE run_id = ? ORDER BY network_id",
            (run_id,),
        )
    ]
    source_ids = [
        item[0]
        for item in connection.execute(
            "SELECT DISTINCT source_id FROM scan_results WHERE run_id = ? ORDER BY source_id",
            (run_id,),
        )
    ]
    return {
        "networkIds": network_ids,
        "sourceIds": source_ids,
        "triggeredBy": "历史全量更新",
        "noLimit": True,
        "explicit": False,
    }


def build_runs(connection):
    records = []
    for row in connection.execute(
        """
        SELECT *
        FROM runs
        WHERE EXISTS (
          SELECT 1 FROM scan_results WHERE scan_results.run_id = runs.run_id
        )
        OR EXISTS (
          SELECT 1 FROM scan_run_scopes WHERE scan_run_scopes.run_id = runs.run_id
        )
        ORDER BY started_at DESC, run_id DESC
        """
    ):
        record = dict(row)
        record["scope"] = run_scope(connection, row["run_id"])
        record["counts"] = count_statuses(connection, run_id=row["run_id"])
        record["networkCounts"] = {
            network_id: count_statuses(
                connection,
                run_id=row["run_id"],
                network_id=network_id,
            )
            for network_id in record["scope"]["networkIds"]
        }
        record["sourceCounts"] = {
            source_id: count_statuses(
                connection,
                run_id=row["run_id"],
                source_id=source_id,
            )
            for source_id in record["scope"]["sourceIds"]
        }
        record["statusLabel"] = STATUS_LABELS.get(row["status"], row["status"])
        record["sourceStats"] = [
            dict(item)
            for item in connection.execute(
                """
                SELECT *
                FROM run_source_stats
                WHERE run_id = ?
                ORDER BY collector_id
                """,
                (row["run_id"],),
            )
        ]
        record["errors"] = [
            dict(item)
            for item in connection.execute(
                """
                SELECT *
                FROM run_errors
                WHERE run_id = ?
                ORDER BY last_seen_at DESC, error_id
                """,
                (row["run_id"],),
            )
        ]
        record["canRetry"] = (
            row["status"] in ("failed", "partial_success")
            or bool(record["errors"])
        )
        records.append(record)
    return records


def latest_for_scope(runs, field, value):
    return next(
        (
            {
                "runId": run["run_id"],
                "status": run["status"],
                "statusLabel": run["statusLabel"],
                "startedAt": run["started_at"],
                "finishedAt": run["finished_at"],
                "counts": (
                    run["networkCounts"].get(value, run["counts"])
                    if field == "networkIds"
                    else run["sourceCounts"].get(value, run["counts"])
                ),
                "explanation": run["zero_result_explanation"],
            }
            for run in runs
            if value in run["scope"][field]
        ),
        None,
    )


def build_results(connection):
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT
              sr.scan_result_id AS scanResultId,
              sr.run_id AS runId,
              sr.network_id AS networkId,
              n.name AS networkName,
              sr.source_id AS sourceId,
              s.name AS sourceName,
              sr.discovery_id AS discoveryId,
              sr.external_key AS externalKey,
              sr.result_status AS resultStatus,
              sr.reason,
              sr.source_url AS sourceUrl,
              sr.observed_at AS observedAt,
              nd.token_name AS tokenName,
              nd.symbol,
              nd.queue_status AS queueStatus,
              nd.discovery_score AS discoveryScore,
              (
                SELECT dir.resolution_status
                FROM discovery_identity_reviews dir
                WHERE dir.discovery_id = sr.discovery_id
                ORDER BY dir.reviewed_at DESC, dir.identity_review_id DESC
                LIMIT 1
              ) AS identityStatus
            FROM scan_results sr
            JOIN networks n ON n.network_id = sr.network_id
            JOIN sources s ON s.source_id = sr.source_id
            LEFT JOIN network_discoveries nd ON nd.discovery_id = sr.discovery_id
            ORDER BY sr.observed_at DESC, sr.scan_result_id
            """
        )
    ]


def build_scan_center_snapshot(connection, config_path=DEFAULT_CONFIG_PATH):
    config = load_config(config_path)
    runs = build_runs(connection)
    networks = []
    for network_id in config["commonNetworks"]:
        counts = count_statuses(connection, network_id=network_id)
        networks.append(
            {
                "networkId": network_id,
                "name": NETWORKS[network_id]["name"],
                "chainType": NETWORKS[network_id]["chainType"],
                "counts": counts,
                "latestRun": latest_for_scope(runs, "networkIds", network_id),
            }
        )

    sources = []
    for provider, config_key in PROVIDER_CONFIG_KEYS.items():
        settings = config["sources"][config_key]
        if not settings["enabled"]:
            continue
        definition = SOURCE_DEFINITIONS[provider]
        source_id = definition["source_id"]
        sources.append(
            {
                "providerKey": provider,
                "sourceId": source_id,
                "name": SOURCE_LABELS[provider],
                "url": settings["url"],
                "boundary": settings["boundary"],
                "conflictRisk": settings["conflictRisk"],
                "networkIds": (
                    ["robinhood-mainnet"]
                    if provider == "robinhood_registry"
                    else list(config["commonNetworks"])
                ),
                "counts": count_statuses(connection, source_id=source_id),
                "latestRun": latest_for_scope(runs, "sourceIds", source_id),
            }
        )

    results = build_results(connection)
    return {
        "product": "企鹅投研",
        "workspace": "凸性工作台",
        "workbenchVersion": "C1.1",
        "dataModelVersion": "v6",
        "generatedAt": utc_now(),
        "noLimitPolicy": "扫描、身份复核和结果列表均不设置项目数量上限；并发只影响完成速度，不会截断符合条件的记录。",
        "counts": {
            "networks": len(networks),
            "sources": len(sources),
            "runs": len(runs),
            "results": len(results),
            "retryableRuns": sum(run["canRetry"] for run in runs),
        },
        "networks": networks,
        "sources": sources,
        "runs": runs,
        "latestRun": runs[0] if runs else None,
        "results": results,
    }


def write_scan_center_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        "window.PENGUIN_CONVEXITY_SCAN_CENTER = "
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return path


def rebuild_scan_center_snapshot(
    db_path=DEFAULT_DB_PATH,
    snapshot_path=DEFAULT_SNAPSHOT_PATH,
    config_path=DEFAULT_CONFIG_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_scan_center_snapshot(connection, config_path=config_path)
        write_scan_center_snapshot(snapshot, snapshot_path)
        return snapshot
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成凸性按链按信源扫描中心快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()
    snapshot = rebuild_scan_center_snapshot(
        db_path=args.db,
        snapshot_path=args.snapshot,
        config_path=args.config,
    )
    print(json.dumps(snapshot["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
