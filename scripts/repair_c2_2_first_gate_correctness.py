#!/usr/bin/env python3
"""One-time, reproducible repair and acceptance audit for C2.2 first-gate facts."""

from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from c2_1_db import DEFAULT_MAIN_DB_PATH, open_pipeline_db
from c2_1_pipeline import build_cohort_catalog, evaluate_all, sync_product_evidence_states
from c2_1_rules import load_rules
from candidate_production import (
    backfill_first_gate_handoff,
    promote_market_confirmed_candidate_assets,
    reconcile_first_gate_queue_from_evaluations,
    refresh_production_contracts,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "c2.1-pipeline.db"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "c2.2-first-gate-correctness" / "latest.json"
EVIDENCE_TYPES = ("github", "deployed_product", "business", "token_utility", "product_usage")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def chunks(values, size):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def scalar(connection, query, parameters=()):
    return int(connection.execute(query, parameters).fetchone()[0] or 0)


def audit(connection, *, check_database: bool) -> dict:
    _, rule_hash = load_rules()
    evidence_states = {
        f"{row[0]}:{row[1]}": int(row[2])
        for row in connection.execute(
            """SELECT evidence_type,status,COUNT(*)
               FROM product_evidence GROUP BY evidence_type,status ORDER BY evidence_type,status"""
        )
    }
    coverage = {}
    for evidence_type in EVIDENCE_TYPES:
        coverage[evidence_type] = scalar(
            connection,
            """SELECT COUNT(DISTINCT q.candidate_id)
               FROM candidate_first_gate_queue q
               JOIN product_evidence pe ON pe.candidate_id=q.candidate_id
               WHERE pe.evidence_type=?""",
            (evidence_type,),
        )
    return {
        "capturedAt": utc_now(),
        "candidateCount": scalar(connection, "SELECT COUNT(*) FROM candidates"),
        "marketConfirmedCount": scalar(connection, "SELECT COUNT(*) FROM candidate_production_records WHERE market_state='market_confirmed'"),
        "firstGateQueuedCount": scalar(connection, "SELECT COUNT(*) FROM candidate_first_gate_queue"),
        "firstGateCompletedCount": scalar(connection, "SELECT COUNT(*) FROM candidate_first_gate_queue WHERE state='completed'"),
        "firstGatePendingCount": scalar(connection, "SELECT COUNT(*) FROM candidate_first_gate_queue WHERE state IN ('pending','retrying','running','failed')"),
        "currentEvaluationCount": scalar(connection, "SELECT COUNT(*) FROM evaluations WHERE is_current=1"),
        "staleQualifiedEvaluationCount": scalar(
            connection,
            """SELECT COUNT(*)
               FROM evaluations e
               JOIN candidates c ON c.candidate_id=e.candidate_id
               JOIN candidate_production_records p ON p.candidate_id=e.candidate_id
               JOIN candidate_first_gate_queue q ON q.candidate_id=e.candidate_id
               JOIN candidate_qualification_batches b
                 ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
               WHERE e.is_current=1 AND (
                 e.rule_config_hash<>?
                 OR datetime(e.evaluated_at)<datetime(COALESCE(p.qualified_at,p.updated_at))
                 OR datetime(e.evaluated_at)<datetime(p.updated_at)
                 OR datetime(e.evaluated_at)<datetime(c.updated_at)
               )""",
            (rule_hash,),
        ),
        "waitingAtomicQualificationCount": scalar(
            connection,
            """SELECT COUNT(*) FROM candidate_first_gate_queue q
               JOIN candidate_production_records p ON p.candidate_id=q.candidate_id
               LEFT JOIN candidate_qualification_batches b ON b.qualification_batch_id=p.qualification_batch_id
               WHERE q.state IN ('pending','retrying','running','failed')
                 AND (p.qualification_batch_id IS NULL OR COALESCE(b.state,'')<>'completed')""",
        ),
        "unjustifiedPendingCount": scalar(
            connection,
            """SELECT COUNT(*) FROM candidate_first_gate_queue q
               JOIN candidate_production_records p ON p.candidate_id=q.candidate_id
               JOIN candidate_qualification_batches b
                 ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
               WHERE q.state IN ('pending','retrying','running','failed')""",
        ),
        "pendingIdentityClassCCount": scalar(
            connection,
            "SELECT COUNT(*) FROM candidates WHERE relationship_class='C' AND identity_status NOT IN ('verified','market_matched')",
        ),
        "frontEligibleCount": scalar(connection, "SELECT COUNT(*) FROM candidate_production_records WHERE front_eligible=1"),
        "frontIdentityBypassCount": scalar(
            connection,
            """SELECT COUNT(*) FROM candidate_production_records p
               JOIN candidates c ON c.candidate_id=p.candidate_id
               WHERE p.front_eligible=1 AND (
                 c.relationship_class NOT IN ('A','B','C')
                 OR c.identity_status NOT IN ('verified','market_matched')
                 OR p.qualifying_product_evidence<>1
               )""",
        ),
        "trackingInputCount": scalar(
            connection,
            """SELECT COUNT(*) FROM candidate_production_records p
               JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id
               WHERE p.front_eligible=1 AND q.state='completed'""",
        ),
        "productEvidenceCategoryCoverage": coverage,
        "productEvidenceStates": evidence_states,
        "ruleConfigHash": rule_hash,
        "ruleConfigFileSha256": hashlib.sha256((PROJECT_ROOT / "docs" / "C2.1_RULE_CONFIG.json").read_bytes()).hexdigest(),
        "quickCheck": connection.execute("PRAGMA quick_check").fetchone()[0] if check_database else "not_run",
        "foreignKeyViolationCount": len(connection.execute("PRAGMA foreign_key_check").fetchall()) if check_database else None,
    }


def repair(connection, main_db_path: Path, batch_size: int) -> dict:
    state_candidate_ids = [
        int(row[0])
        for row in connection.execute(
            """SELECT candidate_id FROM candidate_production_records
               WHERE market_state='market_confirmed'
               UNION
               SELECT candidate_id FROM candidates
               WHERE relationship_class='C' AND identity_status NOT IN ('verified','market_matched')
               ORDER BY candidate_id"""
        )
    ]
    state_totals = {}
    usage_series = 0
    identity_downgraded = 0
    relationship_upgraded = 0
    candidate_assets_promoted = len(
        promote_market_confirmed_candidate_assets(connection, state_candidate_ids)
    )
    if state_candidate_ids:
        result = sync_product_evidence_states(
            connection, state_candidate_ids, main_db_path=main_db_path
        )
        for batch in chunks(state_candidate_ids, max(1, int(batch_size))):
            refresh_production_contracts(connection, batch)
        state_totals.update({key: int(value) for key, value in result["states"].items()})
        usage_series = int(result["productUsageSeries"])
        identity_downgraded = int(result["identityDowngraded"])
        relationship_upgraded = int(result["relationshipUpgraded"])

    missing_handoff_ids = [
        int(row[0])
        for row in connection.execute(
            """SELECT p.candidate_id
               FROM candidate_production_records p
               JOIN candidates c ON c.candidate_id=p.candidate_id
               LEFT JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id
               WHERE p.market_state='market_confirmed' AND (
                 q.candidate_id IS NULL
                 OR c.t0_status!='verified_in_supported_scope'
                 OR COALESCE(c.effective_t0,'')<>COALESCE(p.effective_t0,'')
               )
               ORDER BY p.candidate_id"""
        )
    ]
    if missing_handoff_ids:
        backfill_first_gate_handoff(connection, candidate_ids=missing_handoff_ids)
    handoff_before_evaluation = reconcile_first_gate_queue_from_evaluations(connection)
    evaluation_candidate_ids = [
        int(row[0])
        for row in connection.execute(
            """SELECT p.candidate_id
               FROM candidate_production_records p
               JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id
               JOIN candidate_qualification_batches b
                 ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
               WHERE p.market_state='market_confirmed'
                 AND q.state IN ('pending','retrying','failed')
               ORDER BY p.candidate_id"""
        )
    ]
    evaluation_counts = {}
    evaluated = 0
    as_of = utc_now()
    catalog = build_cohort_catalog(connection, as_of)
    for batch in chunks(evaluation_candidate_ids, max(1, int(batch_size))):
        batch_result = evaluate_all(
            connection,
            as_of=as_of,
            candidate_ids=batch,
            commit_interval=max(1, int(batch_size)),
            cohort_catalog=catalog,
        )
        evaluated += int(batch_result["evaluated"])
        for key, value in batch_result["counts"].items():
            evaluation_counts[key] = evaluation_counts.get(key, 0) + int(value)
    evaluation = {"evaluated": evaluated, "counts": evaluation_counts}
    handoff_after_evaluation = reconcile_first_gate_queue_from_evaluations(
        connection, candidate_ids=evaluation_candidate_ids
    )
    return {
        "selectedEvidenceStateCandidates": len(state_candidate_ids),
        "selectedStaleOrMissingEvaluations": len(evaluation_candidate_ids),
        "productEvidenceStatesWritten": state_totals,
        "productUsageSeries": usage_series,
        "identityDowngraded": identity_downgraded,
        "relationshipUpgraded": relationship_upgraded,
        "candidateAssetsPromoted": candidate_assets_promoted,
        "materializedMissingHandoff": len(missing_handoff_ids),
        "handoffBeforeEvaluation": handoff_before_evaluation,
        "evaluation": evaluation,
        "handoffAfterEvaluation": handoff_after_evaluation,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--main-db-path", type=Path, default=DEFAULT_MAIN_DB_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db_path = args.db_path.resolve()
    if db_path == Path(DEFAULT_MAIN_DB_PATH).resolve():
        raise SystemExit("拒绝把C2.2修复写入data/convexity.db。")
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}")
    with closing(open_pipeline_db(db_path)) as connection:
        before = audit(connection, check_database=False)
        repair_result = repair(connection, args.main_db_path.resolve(), args.batch_size) if args.apply else None
        after = audit(connection, check_database=True)
    payload = {
        "schemaVersion": "c2.2-first-gate-correctness-repair-v1",
        "database": str(db_path),
        "mainDatabaseReadOnly": str(args.main_db_path.resolve()),
        "applied": bool(args.apply),
        "startedFrom": before["capturedAt"],
        "finishedAt": after["capturedAt"],
        "before": before,
        "repair": repair_result,
        "after": after,
        "acceptance": {
            "noStaleQualifiedEvaluations": after["staleQualifiedEvaluationCount"] == 0,
            "noUnjustifiedPending": after["unjustifiedPendingCount"] == 0,
            "noPendingIdentityClassC": after["pendingIdentityClassCCount"] == 0,
            "noFrontIdentityBypass": after["frontIdentityBypassCount"] == 0,
            "trackingHandoffMatchesFront": after["trackingInputCount"] == after["frontEligibleCount"],
            "fiveEvidenceCategoriesMaterialized": all(
                after["productEvidenceCategoryCoverage"].get(key, 0) == after["firstGateQueuedCount"]
                for key in EVIDENCE_TYPES
            ),
            "databaseHealthy": after["quickCheck"] == "ok" and after["foreignKeyViolationCount"] == 0,
        },
    }
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report_path.with_suffix(args.report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(args.report_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.apply and not all(payload["acceptance"].values()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
