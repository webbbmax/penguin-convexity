#!/usr/bin/env python3
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from build_project_detail_snapshot import (
    build_project_detail_snapshot,
    write_project_detail_snapshot,
)
from build_evidence_ledger_snapshot import (
    build_evidence_ledger_snapshot,
    sync_evidence_lineage,
    write_evidence_ledger_snapshot,
)
from build_project_master_pool import (
    build_master_pool_snapshot,
    write_master_pool_snapshot,
)
from build_scan_center_snapshot import (
    build_scan_center_snapshot,
    write_scan_center_snapshot,
)
from contract_tradeability import NETWORKS
from discover_network_tokens import (
    DEFAULT_CONFIG_PATH,
    PROVIDER_CONFIG_KEYS,
    SOURCE_DEFINITIONS,
    SOURCE_ID_TO_PROVIDER,
    build_discovery_snapshot,
    collect_network_discoveries,
    load_config,
    persist_network_discoveries,
    write_discovery_snapshot,
)
from init_db import (
    DEFAULT_DB_PATH,
    DEFAULT_SNAPSHOT_PATH,
    initialize_database,
    write_runtime_snapshot,
)
from resolve_discovery_identities import (
    SOURCE_DEFINITION as IDENTITY_SOURCE_DEFINITION,
    collect_identity_reviews,
    persist_identity_reviews,
)
from source_adapter import (
    build_source_adapter_snapshot,
    run_source_adapter,
    write_source_adapter_snapshot,
)
from sync_thread_candidates import (
    DEFAULT_POOL_SNAPSHOT_PATH,
    build_pool_snapshot,
    load_fixture,
    stable_id,
    write_pool_snapshot,
)


JOB_NAME = "凸性按链按信源扫描"
SOURCE_LABELS = {
    "dexscreener_profiles": "DexScreener 最新代币资料",
    "dexscreener_boosts": "DexScreener 推广代币",
    "robinhood_registry": "Robinhood Chain 代币注册表",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_id_now():
    return datetime.now(timezone.utc).strftime(
        "convexity-manual-scan-%Y%m%dT%H%M%S%fZ"
    )


def normalize_scope(config, network_ids=None, source_ids=None):
    configured_networks = list(config["commonNetworks"])
    enabled_providers = [
        provider
        for provider, config_key in PROVIDER_CONFIG_KEYS.items()
        if config["sources"][config_key]["enabled"]
    ]
    selected_networks = list(dict.fromkeys(network_ids or configured_networks))
    unknown_networks = sorted(set(selected_networks) - set(configured_networks))
    if unknown_networks:
        raise ValueError(f"未知扫描网络：{', '.join(unknown_networks)}")

    requested_sources = source_ids or [
        SOURCE_DEFINITIONS[provider]["source_id"]
        for provider in enabled_providers
    ]
    selected_providers = []
    unknown_sources = []
    for source in dict.fromkeys(requested_sources):
        provider = SOURCE_ID_TO_PROVIDER.get(source, source)
        if provider not in enabled_providers:
            unknown_sources.append(source)
        elif provider not in selected_providers:
            selected_providers.append(provider)
    if unknown_sources:
        raise ValueError(f"未知或未启用信源：{', '.join(sorted(unknown_sources))}")
    selected_source_ids = [
        SOURCE_DEFINITIONS[provider]["source_id"]
        for provider in selected_providers
    ]
    return selected_networks, selected_providers, selected_source_ids


def insert_run(connection, run_id, network_ids, source_ids, now):
    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at, zero_result_class,
          zero_result_explanation, triggered_by, schema_version
        )
        VALUES (?, ?, 'manual', 'running', ?, 'none', '',
                '凸性扫描中心人工触发', 6)
        """,
        (run_id, JOB_NAME, now),
    )
    connection.execute(
        """
        INSERT INTO scan_run_scopes (
          scan_scope_id, run_id, requested_network_ids_json,
          requested_source_ids_json, triggered_by, no_limit, created_at
        )
        VALUES (?, ?, ?, ?, 'user', 1, ?)
        """,
        (
            stable_id("scan-scope", run_id),
            run_id,
            json.dumps(network_ids, ensure_ascii=False),
            json.dumps(source_ids, ensure_ascii=False),
            now,
        ),
    )


def persist_source_stats(connection, run_id, bundle, now):
    for provider, source_stat in bundle["sourceStats"].items():
        definition = SOURCE_DEFINITIONS[provider]
        if source_stat.get("failed"):
            status = "failed"
        elif source_stat.get("skipped") or not source_stat.get("collected"):
            status = "no_data"
        else:
            status = "success"
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json,
              error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, provider),
                run_id,
                definition["source_id"],
                provider,
                status,
                now,
                utc_now(),
                source_stat.get("collected", 0),
                source_stat.get("accepted", 0),
                max(
                    0,
                    source_stat.get("collected", 0)
                    - source_stat.get("accepted", 0),
                ),
                source_stat.get("failed", 0),
                json.dumps(
                    {
                        "boundary": bundle["config"]["sources"][
                            PROVIDER_CONFIG_KEYS[provider]
                        ]["boundary"],
                        "noLimit": True,
                    },
                    ensure_ascii=False,
                ),
                source_stat.get("explanation", ""),
            ),
        )


def persist_identity_stats(connection, run_id, identity_bundle, now):
    for provider, source_stat in identity_bundle["sourceStats"].items():
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, provider),
                run_id,
                IDENTITY_SOURCE_DEFINITION["source_id"],
                provider,
                "partial_success" if source_stat["failed"] else "success",
                now,
                utc_now(),
                source_stat["collected"],
                source_stat["accepted"],
                source_stat["filtered"],
                source_stat["failed"],
                json.dumps(
                    {
                        "boundary": "只建立身份归属并允许升格到影子研究库，不产生投资结论。",
                        "noLimit": True,
                    },
                    ensure_ascii=False,
                ),
            ),
        )


def persist_errors(connection, run_id, errors, now):
    for index, error in enumerate(errors):
        provider = error.get("provider", "unknown")
        source = SOURCE_DEFINITIONS.get(provider)
        source_id = source["source_id"] if source else None
        if provider == "coingecko_identity":
            source_id = IDENTITY_SOURCE_DEFINITION["source_id"]
        connection.execute(
            """
            INSERT INTO run_errors (
              error_id, run_id, source_id, task_name, error_type, message,
              retryable, retry_status, attempts, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, 'source_error', ?, 1, 'not_requested', 1, ?, ?)
            """,
            (
                stable_id(
                    "manual-scan-error",
                    run_id,
                    provider,
                    error.get("sourceUrl", ""),
                    index,
                ),
                run_id,
                source_id,
                f"凸性人工扫描 · {SOURCE_LABELS.get(provider, provider)}",
                error.get("error", "未知扫描错误"),
                now,
                now,
            ),
        )


def finish_run(
    connection,
    run_id,
    bundle,
    discovery_summary,
    identity_summary,
    started_at,
):
    all_errors = list(bundle["errors"]) + list(identity_summary["errors"])
    source_stats = list(bundle["sourceStats"].values())
    all_sources_failed = bool(source_stats) and all(
        item.get("failed") for item in source_stats
    )
    if all_sources_failed and not discovery_summary["observed"]:
        status = "failed"
    elif all_errors:
        status = "partial_success"
    else:
        status = "success"

    if discovery_summary["observed"]:
        zero_class = "none"
    elif all_errors:
        zero_class = "source_failure"
    else:
        zero_class = "source_returned_no_data"

    network_names = [
        NETWORKS[network_id]["name"]
        for network_id in bundle["scope"]["networkIds"]
    ]
    source_names = [
        SOURCE_LABELS[provider]
        for provider in bundle["scope"]["sourceKeys"]
    ]
    explanation = (
        f"扫描网络：{'、'.join(network_names)}；信源：{'、'.join(source_names)}。"
        f"发现 {discovery_summary['observed']} 条，新增 {discovery_summary['new']} 条，"
        f"技术预检通过 {discovery_summary['preflightPassed']} 条，"
        f"待身份核验 {discovery_summary['identityPending']} 条，"
        f"已有资产 {discovery_summary['existingAssets']} 条，"
        f"排除 {discovery_summary['rejected']} 条；"
        f"身份复核 {identity_summary['reviewed']} 条，"
        f"影子库升格 {identity_summary['promoted']} 条，"
        f"错误 {len(all_errors)} 条。"
    )
    finished_at = utc_now()
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    duration_ms = max(0, int((finish - start).total_seconds() * 1000))
    connection.execute(
        """
        UPDATE runs
        SET status = ?, finished_at = ?, duration_ms = ?,
            collected_count = ?, normalized_count = ?, matched_count = ?,
            filtered_count = ?, shadow_added_count = ?, error_count = ?,
            zero_result_class = ?, zero_result_explanation = ?,
            error_summary = ?
        WHERE run_id = ?
        """,
        (
            status,
            finished_at,
            duration_ms,
            discovery_summary["observed"],
            discovery_summary["observed"],
            identity_summary["corroborated"] + identity_summary["existing"],
            discovery_summary["rejected"] + identity_summary["rejected"],
            identity_summary["promoted"],
            len(all_errors),
            zero_class,
            explanation,
            "；".join(error.get("error", "") for error in all_errors[:5]),
            run_id,
        ),
    )
    return status, explanation, len(all_errors)


def rebuild_snapshots(connection, pool_snapshot_path, runtime_snapshot_path):
    adapter_result = run_source_adapter(connection)
    sync_evidence_lineage(connection)
    connection.commit()
    fixture = load_fixture()
    write_pool_snapshot(
        build_pool_snapshot(connection, fixture, production_only=True),
        pool_snapshot_path,
    )
    write_discovery_snapshot(build_discovery_snapshot(connection))
    write_master_pool_snapshot(build_master_pool_snapshot(connection))
    write_project_detail_snapshot(build_project_detail_snapshot(connection))
    write_scan_center_snapshot(build_scan_center_snapshot(connection))
    write_runtime_snapshot(connection, runtime_snapshot_path)
    write_evidence_ledger_snapshot(build_evidence_ledger_snapshot(connection))
    write_source_adapter_snapshot(
        build_source_adapter_snapshot(connection, adapter_result)
    )


def record_unhandled_failure(connection, run_id, error):
    now = utc_now()
    message = f"{type(error).__name__}: {error}"
    connection.execute(
        """
        INSERT OR REPLACE INTO run_errors (
          error_id, run_id, source_id, task_name, error_type, message,
          retryable, retry_status, attempts, first_seen_at, last_seen_at
        )
        VALUES (?, ?, NULL, ?, 'runtime_error', ?, 1, 'not_requested', 1, ?, ?)
        """,
        (
            stable_id("manual-scan-runtime-error", run_id),
            run_id,
            JOB_NAME,
            message,
            now,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE runs
        SET status = 'failed', finished_at = ?, error_count = error_count + 1,
            zero_result_class = 'source_failure',
            zero_result_explanation = ?,
            error_summary = ?
        WHERE run_id = ?
        """,
        (
            now,
            "扫描任务已启动，但执行过程中失败；可以按原链和信源范围单独重试。",
            message,
            run_id,
        ),
    )
    return message


def run_manual_scan(
    network_ids=None,
    source_ids=None,
    db_path=DEFAULT_DB_PATH,
    config_path=DEFAULT_CONFIG_PATH,
    pool_snapshot_path=DEFAULT_POOL_SNAPSHOT_PATH,
    runtime_snapshot_path=DEFAULT_SNAPSHOT_PATH,
    timeout=20,
):
    initialize_database(
        db_path=db_path,
        snapshot_path=runtime_snapshot_path,
        backup=False,
    )
    config = load_config(config_path)
    selected_networks, selected_providers, selected_source_ids = normalize_scope(
        config,
        network_ids=network_ids,
        source_ids=source_ids,
    )
    run_id = run_id_now()
    started_at = utc_now()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        insert_run(
            connection,
            run_id,
            selected_networks,
            selected_source_ids,
            started_at,
        )
        connection.commit()
        try:
            bundle = collect_network_discoveries(
                config_path=config_path,
                timeout=timeout,
                network_ids=selected_networks,
                source_keys=selected_providers,
            )
            identity_bundle = collect_identity_reviews(
                bundle,
                config_path=config_path,
                timeout=min(timeout, 15),
            )
            discovery_summary = persist_network_discoveries(
                connection,
                bundle,
                run_id,
                stable_id,
            )
            identity_summary = persist_identity_reviews(
                connection,
                identity_bundle,
                run_id,
                stable_id,
            )
            persist_source_stats(connection, run_id, bundle, started_at)
            persist_identity_stats(connection, run_id, identity_bundle, started_at)
            persist_errors(
                connection,
                run_id,
                list(bundle["errors"]) + list(identity_bundle["errors"]),
                started_at,
            )
            status, explanation, error_count = finish_run(
                connection,
                run_id,
                bundle,
                discovery_summary,
                identity_summary,
                started_at,
            )
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite 完整性检查失败：{integrity}")
            rebuild_snapshots(
                connection,
                pool_snapshot_path=pool_snapshot_path,
                runtime_snapshot_path=runtime_snapshot_path,
            )
            return {
                "runId": run_id,
                "status": status,
                "message": explanation,
                "scope": {
                    "networkIds": selected_networks,
                    "sourceIds": selected_source_ids,
                    "noLimit": True,
                },
                "discoveries": discovery_summary,
                "identities": identity_summary,
                "errors": error_count,
            }
        except Exception as error:
            connection.rollback()
            message = record_unhandled_failure(connection, run_id, error)
            connection.commit()
            rebuild_snapshots(
                connection,
                pool_snapshot_path=pool_snapshot_path,
                runtime_snapshot_path=runtime_snapshot_path,
            )
            return {
                "runId": run_id,
                "status": "failed",
                "message": message,
                "scope": {
                    "networkIds": selected_networks,
                    "sourceIds": selected_source_ids,
                    "noLimit": True,
                },
                "errors": 1,
            }
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="按指定链和信源执行凸性人工扫描")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--network", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    print(
        json.dumps(
            run_manual_scan(
                network_ids=args.network,
                source_ids=args.source,
                db_path=args.db,
                config_path=args.config,
                timeout=args.timeout,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
