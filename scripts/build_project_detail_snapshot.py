#!/usr/bin/env python3
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from automatic_profile_quality import (
    PROFILE_BOUNDARY,
    PROFILE_VERSION,
    build_automatic_profile,
)
from build_project_master_pool import build_master_pool_snapshot
from catalyst_trade_paths import deserialize_path
from build_monitoring_infrastructure_snapshot import project_profile
from build_weak_signal_snapshot import signal_record
from discover_network_tokens import build_discovery_snapshot
from init_db import DEFAULT_DB_PATH
from weak_signal_inbox import latest_weak_signals


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "project-detail-snapshot.js"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_json(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def one_dict(connection, sql, values=()):
    row = connection.execute(sql, values).fetchone()
    return dict(row) if row else None


def all_dicts(connection, sql, values=()):
    return [dict(row) for row in connection.execute(sql, values)]


def dedupe_evidence(records):
    unique = []
    seen = set()
    for record in records:
        key = (
            record.get("evidence_type"),
            record.get("stance"),
            record.get("fact_boundary"),
            record.get("confidence"),
            record.get("source_id"),
            record.get("source_url"),
            record.get("summary"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def project_source_discoveries(connection, project_id):
    return all_dicts(
        connection,
        """
        SELECT discovery.*, source.name AS source_name
        FROM source_discoveries discovery
        JOIN sources source ON source.source_id = discovery.source_id
        WHERE discovery.matched_project_id = ?
          AND discovery.status = 'active'
        ORDER BY
          CASE discovery.project_identity_status
            WHEN 'verified' THEN 0
            WHEN 'corroborated' THEN 1
            WHEN 'pending' THEN 2
            WHEN 'conflict' THEN 3
            ELSE 4
          END,
          CASE discovery.attribution_confidence
            WHEN 'high' THEN 0
            WHEN 'medium' THEN 1
            ELSE 2
          END,
          discovery.last_seen_at DESC
        """,
        (project_id,),
    )


def project_assets(connection, project_id):
    assets = []
    for asset_row in connection.execute(
        """
        SELECT *
        FROM assets
        WHERE project_id = ?
        ORDER BY symbol, asset_id
        """,
        (project_id,),
    ):
        asset = dict(asset_row)
        contracts = all_dicts(
            connection,
            """
            SELECT ac.*, n.name AS network_name, n.chain_type, n.chain_id,
                   n.environment, n.explorer_url, s.name AS source_name
            FROM asset_contracts ac
            JOIN networks n ON n.network_id = ac.network_id
            LEFT JOIN sources s ON s.source_id = ac.source_id
            WHERE ac.asset_id = ?
            ORDER BY ac.is_primary DESC, n.name, ac.contract_address
            """,
            (asset["asset_id"],),
        )
        tradeability = all_dicts(
            connection,
            """
            SELECT tc.*, ac.network_id, ac.contract_address,
                   n.name AS network_name, s.name AS source_name
            FROM tradeability_checks tc
            JOIN asset_contracts ac
              ON ac.asset_contract_id = tc.asset_contract_id
            JOIN networks n ON n.network_id = ac.network_id
            LEFT JOIN sources s ON s.source_id = tc.source_id
            WHERE ac.asset_id = ?
              AND NOT EXISTS (
                SELECT 1
                FROM tradeability_checks newer
                WHERE newer.asset_contract_id = tc.asset_contract_id
                  AND (
                    newer.checked_at > tc.checked_at
                    OR (
                      newer.checked_at = tc.checked_at
                      AND newer.check_id > tc.check_id
                    )
                  )
              )
            ORDER BY n.name, tc.checked_at DESC
            """,
            (asset["asset_id"],),
        )
        for check in tradeability:
            check["risk_flags"] = parse_json(check.pop("risk_flags_json"), [])
            check["evidence"] = parse_json(check.pop("evidence_json"), [])
        contract_risk = one_dict(
            connection,
            """
            SELECT *
            FROM contract_risks
            WHERE asset_id = ?
            ORDER BY assessed_at DESC, contract_risk_id DESC
            LIMIT 1
            """,
            (asset["asset_id"],),
        )
        if contract_risk:
            contract_risk["evidence"] = parse_json(
                contract_risk.pop("evidence_json"), []
            )
        market = one_dict(
            connection,
            """
            SELECT ms.*, s.name AS source_name
            FROM market_snapshots ms
            LEFT JOIN sources s ON s.source_id = ms.data_source_id
            WHERE ms.asset_id = ?
            ORDER BY ms.observed_at DESC, ms.snapshot_id DESC
            LIMIT 1
            """,
            (asset["asset_id"],),
        )
        venues = all_dicts(
            connection,
            """
            SELECT *
            FROM venues
            WHERE asset_id = ?
            ORDER BY venue_name, pair_symbol
            """,
            (asset["asset_id"],),
        )
        assets.append(
            {
                **asset,
                "contracts": contracts,
                "tradeability": tradeability,
                "contractRisk": contract_risk,
                "latestMarket": market,
                "venues": venues,
            }
        )
    return assets


def project_cases(connection, project_id):
    cases = []
    for case_row in connection.execute(
        """
        SELECT *
        FROM candidate_cases
        WHERE project_id = ?
        ORDER BY updated_at DESC, case_id DESC
        """,
        (project_id,),
    ):
        case = dict(case_row)
        score = one_dict(
            connection,
            """
            SELECT *
            FROM mismatch_scores
            WHERE case_id = ?
            ORDER BY scored_at DESC, mismatch_score_id DESC
            LIMIT 1
            """,
            (case["case_id"],),
        )
        if score:
            score["deduction_detail"] = parse_json(
                score.pop("deduction_detail_json"), []
            )
        review = one_dict(
            connection,
            """
            SELECT *
            FROM convexity_reviews
            WHERE case_id = ?
            ORDER BY reviewed_at DESC, review_id DESC
            LIMIT 1
            """,
            (case["case_id"],),
        )
        if review:
            for source_field, output_field in (
                ("supporting_evidence_json", "supportingEvidence"),
                ("counter_evidence_json", "counterEvidence"),
                ("open_questions_json", "openQuestions"),
            ):
                review[output_field] = parse_json(review.pop(source_field), [])
        machine_score = one_dict(
            connection,
            """
            SELECT *
            FROM machine_research_scores
            WHERE case_id = ?
            ORDER BY scored_at DESC, machine_score_id DESC
            LIMIT 1
            """,
            (case["case_id"],),
        )
        if machine_score:
            machine_score["dimensionScores"] = parse_json(
                machine_score.pop("dimension_scores_json"), {}
            )
            machine_score["blockers"] = parse_json(
                machine_score.pop("blockers_json"), []
            )
            machine_score["sourceEvidenceIds"] = parse_json(
                machine_score.pop("source_evidence_ids_json"), []
            )
        machine_conclusion = one_dict(
            connection,
            """
            SELECT *
            FROM machine_conclusions
            WHERE case_id = ?
              AND publication_status = 'published'
            ORDER BY generated_at DESC, machine_conclusion_id DESC
            LIMIT 1
            """,
            (case["case_id"],),
        )
        if machine_conclusion:
            machine_conclusion["upgradeConditions"] = parse_json(
                machine_conclusion.pop("upgrade_conditions_json"), []
            )
            machine_conclusion["invalidationConditions"] = parse_json(
                machine_conclusion.pop(
                    "invalidation_conditions_json"
                ),
                [],
            )
            machine_conclusion["sourceEvidenceIds"] = parse_json(
                machine_conclusion.pop("source_evidence_ids_json"), []
            )
        catalyst_trade_path_row = connection.execute(
            """
            SELECT *
            FROM catalyst_trade_paths
            WHERE case_id = ?
              AND publication_status = 'published'
            ORDER BY generated_at DESC, catalyst_trade_path_id DESC
            LIMIT 1
            """,
            (case["case_id"],),
        ).fetchone()
        catalyst_trade_path = (
            deserialize_path(catalyst_trade_path_row)
            if catalyst_trade_path_row
            else None
        )
        transitions = all_dicts(
            connection,
            """
            SELECT *
            FROM state_transitions
            WHERE case_id = ?
            ORDER BY transitioned_at DESC, transition_id DESC
            """,
            (case["case_id"],),
        )
        for transition in transitions:
            transition["evidence_ids"] = parse_json(
                transition.pop("evidence_ids_json"), []
            )
        decisions = all_dicts(
            connection,
            """
            SELECT *
            FROM decision_reports
            WHERE case_id = ?
            ORDER BY generated_at DESC, report_id DESC
            """,
            (case["case_id"],),
        )
        outcomes = all_dicts(
            connection,
            """
            SELECT *
            FROM outcomes
            WHERE case_id = ?
            ORDER BY updated_at DESC, outcome_id DESC
            """,
            (case["case_id"],),
        )
        for outcome in outcomes:
            outcome["price_path"] = parse_json(outcome.pop("price_path_json"), [])
            outcome["facts_realized"] = parse_json(
                outcome.pop("facts_realized_json"), []
            )
        cases.append(
            {
                **case,
                "mismatchScore": score,
                "convexityReview": review,
                "machineResearchScore": machine_score,
                "machineConclusion": machine_conclusion,
                "catalystTradePath": catalyst_trade_path,
                "transitions": transitions,
                "decisions": decisions,
                "outcomes": outcomes,
            }
        )
    return cases


def project_detail(connection, master_record):
    project_id = master_record["projectId"]
    project = one_dict(
        connection,
        "SELECT * FROM projects WHERE project_id = ?",
        (project_id,),
    )
    evidence = dedupe_evidence(
        all_dicts(
            connection,
            """
            SELECT ei.*, s.name AS source_name
            FROM evidence_items ei
            LEFT JOIN sources s ON s.source_id = ei.source_id
            WHERE ei.project_id = ?
            ORDER BY ei.observed_at DESC, ei.evidence_id DESC
            """,
            (project_id,),
        )
    )
    annotations = all_dicts(
        connection,
        """
        SELECT *
        FROM manual_annotations
        WHERE project_id = ?
        ORDER BY updated_at DESC, annotation_id DESC
        """,
        (project_id,),
    )
    for annotation in annotations:
        annotation["annotation_value"] = parse_json(
            annotation.pop("annotation_value_json"), {}
        )
    publications = all_dicts(
        connection,
        """
        SELECT *
        FROM publication_records
        WHERE project_id = ?
        ORDER BY updated_at DESC, publication_id DESC
        """,
        (project_id,),
    )
    for publication in publications:
        publication["source_snapshot"] = parse_json(
            publication.pop("source_snapshot_json"), {}
        )
    source_discoveries = project_source_discoveries(connection, project_id)
    monitoring_targets = all_dicts(
        connection,
        """
        SELECT *
        FROM project_monitoring_targets
        WHERE project_id = ?
          AND publication_status = 'published'
        ORDER BY target_type, target_value
        """,
        (project_id,),
    )
    monitoring_case = one_dict(
        connection,
        """
        SELECT case_id
        FROM candidate_cases
        WHERE project_id = ?
        ORDER BY updated_at DESC, case_id DESC
        LIMIT 1
        """,
        (project_id,),
    )
    monitoring_project = {
        **project,
        "case_id": (
            monitoring_case.get("case_id") if monitoring_case else None
        ),
    }
    monitoring_infrastructure = project_profile(
        monitoring_project,
        monitoring_targets,
    )
    weak_signals = [
        signal_record(item)
        for item in latest_weak_signals(connection, project_id)
    ]
    asset_identity_review = one_dict(
        connection,
        """
        SELECT *
        FROM project_asset_identity_reviews
        WHERE project_id = ?
        ORDER BY reviewed_at DESC, project_asset_review_id DESC
        LIMIT 1
        """,
        (project_id,),
    )
    if asset_identity_review:
        asset_identity_review["platforms"] = parse_json(
            asset_identity_review.pop("platforms_json"), {}
        )
        asset_identity_review["officialLinks"] = parse_json(
            asset_identity_review.pop("official_links_json"), {}
        )
        asset_identity_review["evidence"] = parse_json(
            asset_identity_review.pop("evidence_json"), []
        )
        asset_identity_review["sourceUrl"] = (
            (
                "https://www.coingecko.com/en/coins/"
                f"{asset_identity_review['coingecko_id']}"
            )
            if asset_identity_review.get("coingecko_id")
            else next(
                (
                    item["source_url"]
                    for item in source_discoveries
                    if str(item.get("source_url") or "").startswith(
                        ("http://", "https://")
                    )
                ),
                "",
            )
        )
    detail = {
        "recordType": "project",
        "master": master_record,
        "project": project,
        "assets": project_assets(connection, project_id),
        "cases": project_cases(connection, project_id),
        "evidence": evidence,
        "annotations": annotations,
        "publications": publications,
        "officialSources": source_discoveries,
        "assetIdentityReview": asset_identity_review,
        "monitoringInfrastructure": monitoring_infrastructure,
        "weakSignals": weak_signals,
    }
    detail["automaticProfile"] = build_automatic_profile(
        detail, source_discoveries
    )
    return detail


def discovery_detail(connection, master_record, discovery):
    scan_history = all_dicts(
        connection,
        """
        SELECT sr.*, s.name AS source_name, n.name AS network_name,
               r.status AS run_status, r.started_at, r.finished_at
        FROM scan_results sr
        JOIN sources s ON s.source_id = sr.source_id
        JOIN networks n ON n.network_id = sr.network_id
        JOIN runs r ON r.run_id = sr.run_id
        WHERE sr.discovery_id = ?
        ORDER BY sr.observed_at DESC, sr.scan_result_id DESC
        """,
        (master_record["discoveryId"],),
    )
    for scan in scan_history:
        scan["raw_payload"] = parse_json(scan.pop("raw_payload_json"), {})
    annotations = all_dicts(
        connection,
        """
        SELECT *
        FROM manual_annotations
        WHERE discovery_id = ?
        ORDER BY updated_at DESC, annotation_id DESC
        """,
        (master_record["discoveryId"],),
    )
    for annotation in annotations:
        annotation["annotation_value"] = parse_json(
            annotation.pop("annotation_value_json"), {}
        )
    detail = {
        "recordType": "discovery",
        "master": master_record,
        "discovery": discovery,
        "scanHistory": scan_history,
        "annotations": annotations,
    }
    detail["automaticProfile"] = build_automatic_profile(detail)
    return detail


def build_project_detail_snapshot(connection):
    master_snapshot = build_master_pool_snapshot(connection)
    discovery_snapshot = build_discovery_snapshot(connection)
    discovery_by_id = {
        item["discoveryId"]: item for item in discovery_snapshot["records"]
    }
    records = {}
    for master_record in master_snapshot["records"]:
        if master_record["recordType"] == "project":
            records[master_record["masterId"]] = project_detail(
                connection, master_record
            )
        else:
            records[master_record["masterId"]] = discovery_detail(
                connection,
                master_record,
                discovery_by_id.get(master_record["discoveryId"], {}),
            )
    quality_counts = {
        grade: sum(
            item["automaticProfile"]["grade"] == grade
            for item in records.values()
        )
        for grade in (
            "research_ready",
            "partial",
            "thin",
            "identity_blocked",
        )
    }
    return {
        "version": PROFILE_VERSION,
        "generatedAt": utc_now(),
        "boundary": (
            "详情页区分项目主体、可交易资产和链上发现。"
            "发现排序分不是投资评分，技术预检通过也不等于可以建仓。"
        ),
        "profileBoundary": PROFILE_BOUNDARY,
        "counts": {
            "total": len(records),
            "projects": sum(
                item["recordType"] == "project" for item in records.values()
            ),
            "discoveries": sum(
                item["recordType"] == "discovery" for item in records.values()
            ),
            "profileQuality": quality_counts,
        },
        "order": [item["masterId"] for item in master_snapshot["records"]],
        "records": records,
    }


def write_project_detail_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_CONVEXITY_PROJECT_DETAILS = "
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def rebuild_project_detail_snapshot(
    db_path=DEFAULT_DB_PATH,
    snapshot_path=DEFAULT_SNAPSHOT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_project_detail_snapshot(connection)
        write_project_detail_snapshot(snapshot, snapshot_path)
        return snapshot
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成凸性项目详情页数据快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_project_detail_snapshot(args.db, args.snapshot)
    print(json.dumps(snapshot["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
