#!/usr/bin/env python3
import argparse
import json
import sqlite3
from pathlib import Path

from build_change_explanations_snapshot import (
    rebuild_change_explanations_snapshot,
)
from build_decision_quality_snapshots import build_decision_quality_snapshots
from build_catalyst_trade_path_snapshot import (
    build_catalyst_trade_path_snapshot,
    write_catalyst_trade_path_snapshot,
)
from build_monitoring_infrastructure_snapshot import (
    build_monitoring_infrastructure_snapshot,
    write_monitoring_infrastructure_snapshot,
)
from monitoring_infrastructure import persist_monitoring_targets
from weak_signal_inbox import persist_weak_signals
from data_backbone import (
    build_data_backbone_snapshot,
    run_data_backbone,
    write_data_backbone_snapshot,
)
from build_weak_signal_snapshot import (
    build_weak_signal_snapshot,
    write_weak_signal_snapshot,
)
from build_discovery_funnel_snapshot import rebuild_discovery_funnel_snapshot
from build_evidence_ledger_snapshot import (
    build_evidence_ledger_snapshot,
    sync_evidence_lineage,
    write_evidence_ledger_snapshot,
)
from build_four_layer_screening_snapshot import (
    DEFAULT_GOLD_EXPECTED_PATH,
    DEFAULT_GOLD_INPUT_PATH,
    DEFAULT_OUTPUT_PATH as FOUR_LAYER_OUTPUT_PATH,
    build_snapshot as build_four_layer_snapshot,
    write_snapshot as write_four_layer_snapshot,
)
from build_manual_review_snapshot import (
    build_manual_review_snapshot,
    write_manual_review_snapshot,
)
from build_opportunity_center_snapshot import rebuild_opportunity_center_snapshot
from build_project_detail_snapshot import (
    build_project_detail_snapshot,
    write_project_detail_snapshot,
)
from build_project_master_pool import (
    build_master_pool_snapshot,
    write_master_pool_snapshot,
)
from build_research_route_snapshot import rebuild_research_route_snapshot
from build_scan_center_snapshot import (
    build_scan_center_snapshot,
    write_scan_center_snapshot,
)
from build_tracking_tasks_snapshot import rebuild_tracking_tasks_snapshot
from build_update_center_snapshot import rebuild_update_snapshots
from discover_network_tokens import (
    build_discovery_snapshot,
    write_discovery_snapshot,
)
from high_value_sources import (
    build_high_value_snapshot,
    write_high_value_snapshot,
)
from init_db import (
    DEFAULT_DB_PATH,
    DEFAULT_SNAPSHOT_PATH,
    initialize_database,
    write_runtime_snapshot,
)
from project_identity_aliases import sync_project_identity_aliases
from source_adapter import (
    build_source_adapter_snapshot,
    run_source_adapter,
    write_source_adapter_snapshot,
)
from source_discovery_attribution import (
    build_source_discovery_snapshot,
    write_source_discovery_snapshot,
)
from sync_thread_candidates import (
    DEFAULT_POOL_SNAPSHOT_PATH,
    build_pool_snapshot,
    machine_fixture,
    write_pool_snapshot,
)


def rebuild_production_snapshots(db_path=DEFAULT_DB_PATH):
    db_path = Path(db_path).resolve()
    initialize_database(db_path, backup=False)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        sync_project_identity_aliases(connection)
        monitoring_infrastructure = persist_monitoring_targets(connection)
        weak_signals = persist_weak_signals(connection)
        source_adapter = run_source_adapter(connection)
        lineage = sync_evidence_lineage(connection)
        data_backbone = run_data_backbone(connection)
        connection.commit()
        pool = build_pool_snapshot(
            connection,
            machine_fixture(),
            production_only=True,
        )
        write_pool_snapshot(pool)
        write_discovery_snapshot(build_discovery_snapshot(connection))
        write_master_pool_snapshot(build_master_pool_snapshot(connection))
        write_project_detail_snapshot(build_project_detail_snapshot(connection))
        write_scan_center_snapshot(build_scan_center_snapshot(connection))
        write_manual_review_snapshot(build_manual_review_snapshot(connection))
        write_runtime_snapshot(connection, DEFAULT_SNAPSHOT_PATH)
        write_high_value_snapshot(build_high_value_snapshot(connection))
        write_source_discovery_snapshot(
            build_source_discovery_snapshot(connection)
        )
        evidence_ledger = build_evidence_ledger_snapshot(connection)
        write_evidence_ledger_snapshot(evidence_ledger)
        write_source_adapter_snapshot(
            build_source_adapter_snapshot(connection, source_adapter)
        )
        catalyst_paths = write_catalyst_trade_path_snapshot(
            build_catalyst_trade_path_snapshot(connection)
        )
        monitoring_snapshot = write_monitoring_infrastructure_snapshot(
            build_monitoring_infrastructure_snapshot(connection)
        )
        weak_signal_snapshot = write_weak_signal_snapshot(
            build_weak_signal_snapshot(connection)
        )
        data_backbone_snapshot = write_data_backbone_snapshot(
            build_data_backbone_snapshot(connection)
        )
    finally:
        connection.close()

    write_four_layer_snapshot(
        build_four_layer_snapshot(
            DEFAULT_POOL_SNAPSHOT_PATH,
            DEFAULT_GOLD_INPUT_PATH,
            DEFAULT_GOLD_EXPECTED_PATH,
        ),
        FOUR_LAYER_OUTPUT_PATH,
    )
    discovery_funnel = rebuild_discovery_funnel_snapshot(db_path=db_path)
    opportunity = rebuild_opportunity_center_snapshot()
    routes = rebuild_research_route_snapshot()
    tracking = rebuild_tracking_tasks_snapshot(db_path=db_path)
    update_center, sources = rebuild_update_snapshots(db_path=db_path)
    changes = rebuild_change_explanations_snapshot(db_path=db_path)
    decision_quality = build_decision_quality_snapshots(db_path=db_path)
    return {
        "status": "success",
        "database": str(db_path),
        "candidateCases": pool["counts"]["total"],
        "discoveryRecords": discovery_funnel["counts"]["total"],
        "opportunityCases": opportunity["counts"]["total"],
        "researchRoutes": routes["counts"]["total"],
        "trackingTasks": tracking["counts"]["total"],
        "updateRuns": update_center["counts"]["runs"],
        "sources": sources["counts"]["total"],
        "changeRecords": changes["counts"]["total"],
        "decisionQuality": decision_quality,
        "rawEvidenceRecords": evidence_ledger["counts"]["rawEvents"],
        "lineageRows": evidence_ledger["counts"]["lineageRows"],
        "newLineageRows": lineage["inserted"],
        "sourceAdapter": source_adapter,
        "catalystTradePaths": catalyst_paths["counts"],
        "monitoringInfrastructure": {
            **monitoring_infrastructure,
            "snapshotCounts": monitoring_snapshot["counts"],
        },
        "weakSignals": {
            **weak_signals,
            "snapshotCounts": weak_signal_snapshot["counts"],
        },
        "dataBackbone": {
            **data_backbone,
            "snapshotCounts": {
                "rawEvents": data_backbone_snapshot["eventSchema"]["rawEvents"],
                "normalizedEvents": data_backbone_snapshot["eventSchema"]["normalizedEvents"],
                "watchers": data_backbone_snapshot["entityGraph"]["watchers"],
                "openGaps": data_backbone_snapshot["continuity"]["openGaps"],
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description="重建凸性正式环境的全部页面快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    print(
        json.dumps(
            rebuild_production_snapshots(args.db),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
