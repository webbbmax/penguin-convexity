#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "evidence-ledger-snapshot.js"
OUTPUT_PREFIX = "window.PENGUIN_CONVEXITY_EVIDENCE_LEDGER = "
PARSER_VERSION = "evidence-lineage-v1"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(*parts):
    digest = hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"lineage-{digest}"


def parse_ids(value):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return list(dict.fromkeys(str(item) for item in parsed if item))


def insert_lineage(
    connection,
    *,
    target_type,
    target_id,
    raw_event_id=None,
    evidence_id=None,
    project_id=None,
    case_id=None,
    source_id=None,
    run_id=None,
    relation_type,
    lineage_status,
    source_url="",
    detail="",
    captured_at,
    created_at,
):
    lineage_id = stable_id(
        PARSER_VERSION,
        target_type,
        target_id,
        raw_event_id or "",
        evidence_id or "",
        relation_type,
        lineage_status,
    )
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO evidence_lineage (
          lineage_id, target_type, target_id, raw_event_id, evidence_id,
          project_id, case_id, source_id, run_id, relation_type,
          lineage_status, parser_version, source_url, detail,
          captured_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lineage_id,
            target_type,
            target_id,
            raw_event_id,
            evidence_id,
            project_id,
            case_id,
            source_id,
            run_id,
            relation_type,
            lineage_status,
            PARSER_VERSION,
            source_url or "",
            detail,
            captured_at,
            created_at,
        ),
    )
    return int(cursor.rowcount > 0)


def sync_evidence_lineage(connection):
    now = utc_now()
    evidence_rows = {
        row["evidence_id"]: dict(row)
        for row in connection.execute(
            """
            SELECT evidence.*, raw.ingestion_run_id
            FROM evidence_items evidence
            LEFT JOIN raw_events raw ON raw.raw_event_id = evidence.raw_event_id
            """
        )
    }
    raw_ids = {
        row[0] for row in connection.execute("SELECT raw_event_id FROM raw_events")
    }
    linked_raw_ids = {
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT raw_event_id
            FROM evidence_items
            WHERE raw_event_id IS NOT NULL
            """
        )
    }

    inserted = 0
    for row in connection.execute(
        """
        SELECT raw_event_id, source_id, ingestion_run_id, source_url, collected_at
        FROM raw_events
        """
    ):
        status = "verified" if row["raw_event_id"] in linked_raw_ids else "raw_only"
        inserted += insert_lineage(
            connection,
            target_type="raw_event",
            target_id=row["raw_event_id"],
            raw_event_id=row["raw_event_id"],
            source_id=row["source_id"],
            run_id=row["ingestion_run_id"],
            relation_type="raw_capture",
            lineage_status=status,
            source_url=row["source_url"],
            detail=(
                "原始记录已形成研究证据。"
                if status == "verified"
                else "原始记录已留存，尚未形成研究证据。"
            ),
            captured_at=row["collected_at"],
            created_at=now,
        )

    for evidence in evidence_rows.values():
        raw_event_id = evidence["raw_event_id"]
        has_raw = bool(raw_event_id and raw_event_id in raw_ids)
        inserted += insert_lineage(
            connection,
            target_type="evidence_item",
            target_id=evidence["evidence_id"],
            raw_event_id=raw_event_id if has_raw else None,
            evidence_id=evidence["evidence_id"],
            project_id=evidence["project_id"],
            source_id=evidence["source_id"],
            run_id=evidence["ingestion_run_id"] if has_raw else None,
            relation_type=(
                "direct_normalization" if has_raw else "legacy_missing_raw"
            ),
            lineage_status="verified" if has_raw else "missing_raw",
            source_url=evidence["source_url"],
            detail=(
                "研究证据可回溯到不可变原始记录。"
                if has_raw
                else "历史研究证据缺少原始记录，等待后续重新采集。"
            ),
            captured_at=evidence["observed_at"],
            created_at=now,
        )

    downstream_specs = [
        (
            "machine_research_score",
            """
            SELECT score.machine_score_id AS target_id, score.case_id,
                   score.run_id, score.scored_at AS captured_at,
                   score.source_evidence_ids_json AS evidence_ids_json,
                   candidate.project_id
            FROM machine_research_scores score
            JOIN candidate_cases candidate ON candidate.case_id = score.case_id
            """,
        ),
        (
            "machine_conclusion",
            """
            SELECT conclusion.machine_conclusion_id AS target_id,
                   conclusion.case_id, conclusion.run_id,
                   conclusion.generated_at AS captured_at,
                   conclusion.source_evidence_ids_json AS evidence_ids_json,
                   candidate.project_id
            FROM machine_conclusions conclusion
            JOIN candidate_cases candidate ON candidate.case_id = conclusion.case_id
            """,
        ),
        (
            "state_transition",
            """
            SELECT transition.transition_id AS target_id, transition.case_id,
                   NULL AS run_id, transition.transitioned_at AS captured_at,
                   transition.evidence_ids_json AS evidence_ids_json,
                   candidate.project_id
            FROM state_transitions transition
            JOIN candidate_cases candidate ON candidate.case_id = transition.case_id
            """,
        ),
        (
            "tracking_decision_review",
            """
            SELECT review.tracking_review_id AS target_id, review.case_id,
                   task.run_id, review.reviewed_at AS captured_at,
                   review.evidence_ids_json AS evidence_ids_json,
                   candidate.project_id
            FROM tracking_decision_reviews review
            JOIN tracking_task_runs task
              ON task.tracking_result_id = review.tracking_result_id
            JOIN candidate_cases candidate ON candidate.case_id = review.case_id
            """,
        ),
    ]

    referenced = 0
    missing_references = 0
    for target_type, query in downstream_specs:
        for target in connection.execute(query):
            for evidence_id in parse_ids(target["evidence_ids_json"]):
                evidence = evidence_rows.get(evidence_id)
                if evidence:
                    raw_event_id = evidence["raw_event_id"]
                    has_raw = bool(raw_event_id and raw_event_id in raw_ids)
                    status = "verified" if has_raw else "missing_raw"
                    source_id = evidence["source_id"]
                    source_url = evidence["source_url"]
                else:
                    raw_event_id = None
                    status = "missing_reference"
                    source_id = None
                    source_url = ""
                    missing_references += 1
                inserted += insert_lineage(
                    connection,
                    target_type=target_type,
                    target_id=target["target_id"],
                    raw_event_id=raw_event_id if status == "verified" else None,
                    evidence_id=evidence_id,
                    project_id=target["project_id"],
                    case_id=target["case_id"],
                    source_id=source_id,
                    run_id=target["run_id"],
                    relation_type="referenced_input",
                    lineage_status=status,
                    source_url=source_url,
                    detail=(
                        "下游机器记录引用了该研究证据。"
                        if evidence
                        else "下游记录引用的证据ID在当前证据表中不存在。"
                    ),
                    captured_at=target["captured_at"],
                    created_at=now,
                )
                referenced += 1

    return {
        "inserted": inserted,
        "rawEvents": len(raw_ids),
        "evidenceItems": len(evidence_rows),
        "referencedInputs": referenced,
        "missingReferences": missing_references,
    }


def record_role(source_id, source_type):
    internal_prefixes = ("internal_", "derived_")
    if source_id.startswith("machine-") or source_type.startswith(internal_prefixes):
        return "machine_audit"
    return "external_source"


def build_evidence_ledger_snapshot(connection):
    evidence_by_raw = defaultdict(list)
    for row in connection.execute(
        """
        SELECT evidence_id, raw_event_id
        FROM evidence_items
        WHERE raw_event_id IS NOT NULL
        ORDER BY observed_at DESC
        """
    ):
        evidence_by_raw[row["raw_event_id"]].append(row["evidence_id"])

    downstream_by_raw = defaultdict(list)
    for row in connection.execute(
        """
        SELECT raw_event_id, target_type, target_id, captured_at
        FROM evidence_lineage
        WHERE raw_event_id IS NOT NULL
          AND target_type NOT IN ('raw_event', 'evidence_item')
        ORDER BY captured_at DESC
        """
    ):
        downstream_by_raw[row["raw_event_id"]].append(
            {
                "type": row["target_type"],
                "id": row["target_id"],
                "capturedAt": row["captured_at"],
            }
        )

    records = []
    for row in connection.execute(
        """
        SELECT raw.*, source.name AS source_name,
               source.source_type, source.confidence AS source_confidence
        FROM raw_events raw
        JOIN sources source ON source.source_id = raw.source_id
        ORDER BY raw.collected_at DESC, raw.raw_event_id
        """
    ):
        evidence_ids = evidence_by_raw.get(row["raw_event_id"], [])
        downstream = downstream_by_raw.get(row["raw_event_id"], [])
        role = record_role(row["source_id"], row["source_type"])
        records.append(
            {
                "rawEventId": row["raw_event_id"],
                "sourceId": row["source_id"],
                "sourceName": row["source_name"],
                "sourceType": row["source_type"],
                "sourceConfidence": row["source_confidence"],
                "recordRole": role,
                "externalId": row["external_id"],
                "runId": row["ingestion_run_id"] or "",
                "publishedAt": row["published_at"] or "",
                "collectedAt": row["collected_at"],
                "contentHash": row["content_hash"],
                "sourceUrl": row["source_url"],
                "excerpt": row["excerpt"],
                "projectHint": row["project_hint"],
                "assetHint": row["asset_hint"],
                "chainHint": row["chain_hint"],
                "eventType": row["event_type"],
                "traceStatus": "linked" if evidence_ids else "raw_only",
                "evidenceIds": evidence_ids,
                "downstreamCount": len(downstream),
                "downstreamTargets": downstream[:12],
            }
        )

    gaps = [
        {
            "evidenceId": row["evidence_id"],
            "projectId": row["project_id"] or "",
            "projectName": row["canonical_name"] or "",
            "sourceId": row["source_id"] or "",
            "sourceName": row["source_name"] or "",
            "evidenceType": row["evidence_type"],
            "observedAt": row["observed_at"],
            "sourceUrl": row["source_url"],
            "summary": row["summary"],
        }
        for row in connection.execute(
            """
            SELECT evidence.*, project.canonical_name,
                   source.name AS source_name
            FROM evidence_items evidence
            LEFT JOIN projects project ON project.project_id = evidence.project_id
            LEFT JOIN sources source ON source.source_id = evidence.source_id
            WHERE evidence.raw_event_id IS NULL
               OR NOT EXISTS (
                 SELECT 1 FROM raw_events raw
                 WHERE raw.raw_event_id = evidence.raw_event_id
               )
            ORDER BY evidence.observed_at DESC, evidence.evidence_id
            """
        )
    ]

    source_coverage = []
    for row in connection.execute(
        """
        SELECT source.source_id, source.name, source.source_type,
               COUNT(raw.raw_event_id) AS raw_count,
               COUNT(DISTINCT CASE WHEN evidence.evidence_id IS NOT NULL
                              THEN raw.raw_event_id END) AS linked_raw_count,
               COUNT(DISTINCT evidence.evidence_id) AS evidence_count,
               MAX(raw.collected_at) AS latest_collected_at
        FROM sources source
        LEFT JOIN raw_events raw ON raw.source_id = source.source_id
        LEFT JOIN evidence_items evidence ON evidence.raw_event_id = raw.raw_event_id
        GROUP BY source.source_id
        HAVING COUNT(raw.raw_event_id) > 0
        ORDER BY raw_count DESC, source.name
        """
    ):
        source_coverage.append(
            {
                "sourceId": row["source_id"],
                "sourceName": row["name"],
                "sourceType": row["source_type"],
                "recordRole": record_role(row["source_id"], row["source_type"]),
                "rawCount": row["raw_count"],
                "linkedRawCount": row["linked_raw_count"],
                "evidenceCount": row["evidence_count"],
                "latestCollectedAt": row["latest_collected_at"] or "",
            }
        )

    evidence_count = connection.execute(
        "SELECT COUNT(*) FROM evidence_items"
    ).fetchone()[0]
    linked_evidence_count = evidence_count - len(gaps)
    external_count = sum(
        1 for record in records if record["recordRole"] == "external_source"
    )
    return {
        "version": "C1.6-06",
        "generatedAt": utc_now(),
        "principle": "原始记录不可覆盖；历史缺口只标记，不伪造补齐。",
        "counts": {
            "rawEvents": len(records),
            "externalRecords": external_count,
            "machineAuditRecords": len(records) - external_count,
            "linkedRawEvents": sum(
                1 for record in records if record["traceStatus"] == "linked"
            ),
            "rawOnlyEvents": sum(
                1 for record in records if record["traceStatus"] == "raw_only"
            ),
            "evidenceItems": evidence_count,
            "linkedEvidenceItems": linked_evidence_count,
            "missingRawEvidenceItems": len(gaps),
            "lineageRows": connection.execute(
                "SELECT COUNT(*) FROM evidence_lineage"
            ).fetchone()[0],
            "missingReferences": connection.execute(
                """
                SELECT COUNT(*)
                FROM evidence_lineage
                WHERE lineage_status = 'missing_reference'
                """
            ).fetchone()[0],
        },
        "sourceCoverage": source_coverage,
        "records": records,
        "gaps": gaps,
    }


def write_evidence_ledger_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        OUTPUT_PREFIX
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)


def rebuild_evidence_ledger_snapshot(
    db_path=DEFAULT_DB_PATH,
    output_path=DEFAULT_OUTPUT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        lineage = sync_evidence_lineage(connection)
        connection.commit()
        snapshot = build_evidence_ledger_snapshot(connection)
    finally:
        connection.close()
    write_evidence_ledger_snapshot(snapshot, output_path)
    return snapshot, lineage


def main():
    parser = argparse.ArgumentParser(description="生成凸性原始证据账本与溯源关系")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot, lineage = rebuild_evidence_ledger_snapshot(args.db, args.output)
    print(
        json.dumps(
            {
                "status": "success",
                "snapshot": str(args.output),
                "counts": snapshot["counts"],
                "lineage": lineage,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
