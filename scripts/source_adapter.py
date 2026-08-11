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
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "source-adapter-snapshot.js"
OUTPUT_PREFIX = "window.PENGUIN_CONVEXITY_SOURCE_ADAPTER = "
ADAPTER_VERSION = "source-adapter-v1"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(*parts, length=24):
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:length]


def parse_json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed


def record_adapter_audit(
    connection,
    *,
    source_id,
    run_id,
    source_record_type,
    source_record_id,
    raw_event_id,
    evidence_id,
    project_id,
    adapter_stage,
    adapter_status,
    content_hash="",
    detail="",
    processed_at,
):
    adapter_record_id = f"source-adapter-{digest(
        ADAPTER_VERSION,
        source_id,
        source_record_type,
        source_record_id,
        raw_event_id or "",
        evidence_id or "",
        adapter_stage,
        adapter_status,
    )}"
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO source_adapter_records (
          adapter_record_id, source_id, run_id, source_record_type,
          source_record_id, raw_event_id, evidence_id, project_id,
          adapter_stage, adapter_status, content_hash, detail,
          adapter_version, processed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            adapter_record_id,
            source_id,
            run_id,
            source_record_type,
            source_record_id,
            raw_event_id,
            evidence_id,
            project_id,
            adapter_stage,
            adapter_status,
            content_hash,
            detail,
            ADAPTER_VERSION,
            processed_at,
        ),
    )
    return int(cursor.rowcount > 0)


def persist_recovered_raw_event(
    connection,
    *,
    source_id,
    run_id,
    source_record_type,
    source_record_id,
    collected_at,
    source_url,
    excerpt,
    project_hint,
    event_type,
    payload,
):
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    content_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    external_id = (
        f"{ADAPTER_VERSION}:{source_record_type}:{source_record_id}:"
        f"{content_hash[:16]}"
    )
    raw_event_id = f"raw-adapter-{digest(source_id, external_id)}"
    connection.execute(
        """
        INSERT OR IGNORE INTO raw_events (
          raw_event_id, source_id, ingestion_run_id, external_id,
          published_at, collected_at, content_hash, source_url, excerpt,
          project_hint, asset_hint, chain_hint, event_type,
          raw_payload_json, status
        )
        VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, '', '', ?, ?, 'normalized')
        """,
        (
            raw_event_id,
            source_id,
            run_id,
            external_id,
            collected_at,
            content_hash,
            source_url or "",
            excerpt,
            project_hint or "",
            event_type,
            payload_text,
        ),
    )
    row = connection.execute(
        """
        SELECT raw_event_id, content_hash
        FROM raw_events
        WHERE source_id = ? AND external_id = ?
        """,
        (source_id, external_id),
    ).fetchone()
    return row["raw_event_id"], row["content_hash"]


def identity_review_urls(row):
    urls = {row["website_url"] or ""}
    for field in ("social_urls_json", "repo_urls_json"):
        values = parse_json(row[field], [])
        if isinstance(values, list):
            urls.update(str(value) for value in values if value)
    evidence = parse_json(row["evidence_json"], [])
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict) and item.get("url"):
                urls.add(str(item["url"]))
    return urls


def find_source_discovery(connection, evidence):
    if not evidence["source_url"]:
        return None
    return connection.execute(
        """
        SELECT *
        FROM source_discoveries
        WHERE source_id = ?
          AND matched_project_id = ?
          AND ? IN (source_url, website_url, repository_url, social_url)
        ORDER BY last_seen_at DESC, source_discovery_id
        LIMIT 1
        """,
        (
            evidence["source_id"],
            evidence["project_id"],
            evidence["source_url"],
        ),
    ).fetchone()


def find_identity_review(connection, evidence):
    if not evidence["source_url"]:
        return None
    rows = connection.execute(
        """
        SELECT *
        FROM discovery_identity_reviews
        WHERE ? IN (matched_project_id, promoted_project_id)
        ORDER BY reviewed_at DESC, identity_review_id
        """,
        (evidence["project_id"],),
    ).fetchall()
    for row in rows:
        if evidence["source_url"] in identity_review_urls(row):
            return row
    return None


def recover_evidence_gap(connection, evidence, project_name, now):
    source_id = evidence["source_id"] or ""
    source_record = None
    source_record_type = ""
    source_record_id = ""
    run_id = None
    collected_at = evidence["observed_at"]

    if source_id.startswith("discovery-"):
        source_record = find_source_discovery(connection, evidence)
        if source_record:
            source_record_type = "source_discovery"
            source_record_id = source_record["source_discovery_id"]
            run_id = source_record["last_run_id"]
            collected_at = source_record["last_seen_at"]
    elif source_id == "identity-coingecko-registry":
        source_record = find_identity_review(connection, evidence)
        if source_record:
            source_record_type = "identity_review"
            source_record_id = source_record["identity_review_id"]
            run_id = source_record["run_id"]
            collected_at = source_record["reviewed_at"]

    if not source_record:
        return {
            "status": "missing_raw",
            "evidenceId": evidence["evidence_id"],
            "sourceId": source_id,
        }

    payload = {
        "recoveryBoundary": (
            "本原始记录由历史上已保存的结构化来源记录精确重建，"
            "不是根据证据摘要反向猜测。"
        ),
        "sourceRecordType": source_record_type,
        "sourceRecordId": source_record_id,
        "sourceRecord": dict(source_record),
        "evidenceId": evidence["evidence_id"],
        "adapterVersion": ADAPTER_VERSION,
    }
    raw_event_id, content_hash = persist_recovered_raw_event(
        connection,
        source_id=source_id,
        run_id=run_id,
        source_record_type=source_record_type,
        source_record_id=f"{source_record_id}:{evidence['evidence_id']}",
        collected_at=collected_at or now,
        source_url=evidence["source_url"],
        excerpt=evidence["summary"],
        project_hint=project_name,
        event_type=evidence["evidence_type"],
        payload=payload,
    )

    current = connection.execute(
        """
        SELECT raw_event_id
        FROM evidence_items
        WHERE evidence_id = ?
        """,
        (evidence["evidence_id"],),
    ).fetchone()
    if current["raw_event_id"] and current["raw_event_id"] != raw_event_id:
        record_adapter_audit(
            connection,
            source_id=source_id,
            run_id=run_id,
            source_record_type=source_record_type,
            source_record_id=source_record_id,
            raw_event_id=raw_event_id,
            evidence_id=evidence["evidence_id"],
            project_id=evidence["project_id"],
            adapter_stage="recovery",
            adapter_status="conflict",
            content_hash=content_hash,
            detail="证据已经连接到另一条原始记录，适配器没有覆盖原关系。",
            processed_at=now,
        )
        return {
            "status": "conflict",
            "evidenceId": evidence["evidence_id"],
            "sourceId": source_id,
        }

    connection.execute(
        """
        UPDATE evidence_items
        SET raw_event_id = ?
        WHERE evidence_id = ? AND raw_event_id IS NULL
        """,
        (raw_event_id, evidence["evidence_id"]),
    )
    record_adapter_audit(
        connection,
        source_id=source_id,
        run_id=run_id,
        source_record_type=source_record_type,
        source_record_id=source_record_id,
        raw_event_id=raw_event_id,
        evidence_id=evidence["evidence_id"],
        project_id=evidence["project_id"],
        adapter_stage="recovery",
        adapter_status="recovered",
        content_hash=content_hash,
        detail="已用同一项目、同一来源和同一URL的历史结构化记录精确重绑定。",
        processed_at=now,
    )
    return {
        "status": "recovered",
        "evidenceId": evidence["evidence_id"],
        "sourceId": source_id,
        "rawEventId": raw_event_id,
    }


def recover_legacy_gaps(connection, now):
    rows = connection.execute(
        """
        SELECT evidence.*, project.canonical_name
        FROM evidence_items evidence
        LEFT JOIN projects project ON project.project_id = evidence.project_id
        WHERE evidence.raw_event_id IS NULL
        ORDER BY evidence.observed_at, evidence.evidence_id
        """
    ).fetchall()
    results = defaultdict(int)
    recovered_ids = set()
    for evidence in rows:
        result = recover_evidence_gap(
            connection,
            evidence,
            evidence["canonical_name"] or "",
            now,
        )
        results[result["status"]] += 1
        if result["status"] == "recovered":
            recovered_ids.add(result["evidenceId"])
    return {
        "total": len(rows),
        "recovered": results["recovered"],
        "remaining": results["missing_raw"],
        "conflicts": results["conflict"],
        "recoveredIds": recovered_ids,
    }


def sync_adapter_audit(connection, now, recovered_ids):
    inserted = 0
    recovered_ids = set(recovered_ids)
    recovered_ids.update(
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT evidence_id
            FROM source_adapter_records
            WHERE adapter_version = ?
              AND adapter_status = 'recovered'
              AND evidence_id IS NOT NULL
            """,
            (ADAPTER_VERSION,),
        )
    )
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
    for raw in connection.execute(
        """
        SELECT raw_event_id, source_id, ingestion_run_id, external_id,
               project_hint, content_hash
        FROM raw_events
        """
    ):
        status = (
            "complete"
            if raw["raw_event_id"] in linked_raw_ids
            else "raw_only"
        )
        inserted += record_adapter_audit(
            connection,
            source_id=raw["source_id"],
            run_id=raw["ingestion_run_id"],
            source_record_type="raw_event",
            source_record_id=raw["external_id"],
            raw_event_id=raw["raw_event_id"],
            evidence_id=None,
            project_id=None,
            adapter_stage="raw_capture",
            adapter_status=status,
            content_hash=raw["content_hash"],
            detail=(
                "原始记录已连接研究证据。"
                if status == "complete"
                else "原始记录已留存，当前不要求或尚未形成研究证据。"
            ),
            processed_at=now,
        )

    raw_ids = {
        row[0] for row in connection.execute("SELECT raw_event_id FROM raw_events")
    }
    for evidence in connection.execute(
        """
        SELECT evidence_id, source_id, project_id, raw_event_id
        FROM evidence_items
        """
    ):
        linked = bool(
            evidence["raw_event_id"] and evidence["raw_event_id"] in raw_ids
        )
        status = (
            "recovered"
            if evidence["evidence_id"] in recovered_ids
            else "complete"
            if linked
            else "missing_raw"
        )
        inserted += record_adapter_audit(
            connection,
            source_id=evidence["source_id"] or "unregistered",
            run_id=None,
            source_record_type="evidence_item",
            source_record_id=evidence["evidence_id"],
            raw_event_id=evidence["raw_event_id"] if linked else None,
            evidence_id=evidence["evidence_id"],
            project_id=evidence["project_id"],
            adapter_stage=(
                "recovery" if status == "recovered" else "evidence_link"
            ),
            adapter_status=status,
            detail=(
                "研究证据已由历史结构化来源记录恢复原始链路。"
                if status == "recovered"
                else "研究证据已连接原始记录。"
                if linked
                else "证据仍缺少可精确匹配的原始结构化记录。"
            ),
            processed_at=now,
        )
    return inserted


def run_source_adapter(connection):
    now = utc_now()
    recovery = recover_legacy_gaps(connection, now)
    inserted = sync_adapter_audit(
        connection,
        now,
        recovery.pop("recoveredIds"),
    )
    return {
        **recovery,
        "auditInserted": inserted,
        "adapterVersion": ADAPTER_VERSION,
    }


def source_status(row):
    if row["missing_raw_count"]:
        return "partial"
    if row["evidence_count"]:
        return "complete"
    if row["raw_count"]:
        return "raw_only"
    return "no_data"


def source_next_step(status):
    return {
        "complete": "继续按当前主干运行；新记录会自动留存并连接证据。",
        "partial": "优先重新采集无法精确重绑定的历史记录，不允许猜测归属。",
        "raw_only": "原始记录已留存；由后续归因和研究规则决定是否形成证据。",
        "no_data": "来源已登记但尚无数据，等待对应任务首次成功运行。",
    }[status]


def build_source_adapter_snapshot(connection, adapter_result=None):
    adapter_result = adapter_result or {}
    gap_examples = defaultdict(list)
    for row in connection.execute(
        """
        SELECT evidence.evidence_id, evidence.source_id, evidence.evidence_type,
               evidence.summary, evidence.source_url, project.canonical_name
        FROM evidence_items evidence
        LEFT JOIN projects project ON project.project_id = evidence.project_id
        WHERE evidence.raw_event_id IS NULL
        ORDER BY evidence.observed_at DESC, evidence.evidence_id
        """
    ):
        if len(gap_examples[row["source_id"]]) < 8:
            gap_examples[row["source_id"]].append(
                {
                    "evidenceId": row["evidence_id"],
                    "projectName": row["canonical_name"] or "未归属项目",
                    "evidenceType": row["evidence_type"],
                    "summary": row["summary"],
                    "sourceUrl": row["source_url"],
                }
            )

    source_rows = connection.execute(
        """
        SELECT source.source_id, source.name, source.source_type, source.url,
               source.status AS registry_status,
               COUNT(DISTINCT raw.raw_event_id) AS raw_count,
               COUNT(DISTINCT evidence.evidence_id) AS evidence_count,
               COUNT(DISTINCT CASE
                 WHEN evidence.raw_event_id IS NOT NULL
                  AND linked_raw.raw_event_id IS NOT NULL
                 THEN evidence.evidence_id END) AS linked_evidence_count,
               COUNT(DISTINCT CASE
                 WHEN evidence.evidence_id IS NOT NULL
                  AND (evidence.raw_event_id IS NULL
                    OR linked_raw.raw_event_id IS NULL)
                 THEN evidence.evidence_id END) AS missing_raw_count,
               MAX(raw.collected_at) AS latest_collected_at
        FROM sources source
        LEFT JOIN raw_events raw ON raw.source_id = source.source_id
        LEFT JOIN evidence_items evidence ON evidence.source_id = source.source_id
        LEFT JOIN raw_events linked_raw
          ON linked_raw.raw_event_id = evidence.raw_event_id
        GROUP BY source.source_id
        ORDER BY raw_count DESC, evidence_count DESC, source.name
        """
    ).fetchall()

    recovered_by_source = {
        row["source_id"]: row["count"]
        for row in connection.execute(
            """
            SELECT source_id, COUNT(DISTINCT evidence_id) AS count
            FROM source_adapter_records
            WHERE adapter_version = ? AND adapter_status = 'recovered'
            GROUP BY source_id
            """,
            (ADAPTER_VERSION,),
        )
    }
    conflict_by_source = {
        row["source_id"]: row["count"]
        for row in connection.execute(
            """
            SELECT source_id, COUNT(*) AS count
            FROM source_adapter_records
            WHERE adapter_version = ? AND adapter_status = 'conflict'
            GROUP BY source_id
            """,
            (ADAPTER_VERSION,),
        )
    }
    raw_only_by_source = {
        row["source_id"]: row["count"]
        for row in connection.execute(
            """
            SELECT raw.source_id, COUNT(*) AS count
            FROM raw_events raw
            WHERE NOT EXISTS (
              SELECT 1 FROM evidence_items evidence
              WHERE evidence.raw_event_id = raw.raw_event_id
            )
            GROUP BY raw.source_id
            """
        )
    }

    sources = []
    counts = defaultdict(int)
    for row in source_rows:
        status = source_status(row)
        counts[status] += 1
        sources.append(
            {
                "sourceId": row["source_id"],
                "sourceName": row["name"],
                "sourceType": row["source_type"],
                "sourceUrl": row["url"],
                "registryStatus": row["registry_status"],
                "adapterStatus": status,
                "rawCount": row["raw_count"],
                "rawOnlyCount": raw_only_by_source.get(row["source_id"], 0),
                "evidenceCount": row["evidence_count"],
                "linkedEvidenceCount": row["linked_evidence_count"],
                "missingRawCount": row["missing_raw_count"],
                "recoveredCount": recovered_by_source.get(row["source_id"], 0),
                "conflictCount": conflict_by_source.get(row["source_id"], 0),
                "latestCollectedAt": row["latest_collected_at"] or "",
                "nextStep": source_next_step(status),
                "gapExamples": gap_examples.get(row["source_id"], []),
            }
        )

    return {
        "version": "C1.6-06",
        "adapterVersion": ADAPTER_VERSION,
        "generatedAt": utc_now(),
        "principle": (
            "所有采集器统一经过原始留存、证据连接、身份检查和溯源刷新；"
            "无法精确匹配的历史记录保持缺口。"
        ),
        "lastAdapterRun": adapter_result,
        "counts": {
            "sources": len(sources),
            "completeSources": counts["complete"],
            "partialSources": counts["partial"],
            "rawOnlySources": counts["raw_only"],
            "noDataSources": counts["no_data"],
            "rawEvents": connection.execute(
                "SELECT COUNT(*) FROM raw_events"
            ).fetchone()[0],
            "evidenceItems": connection.execute(
                "SELECT COUNT(*) FROM evidence_items"
            ).fetchone()[0],
            "linkedEvidenceItems": connection.execute(
                """
                SELECT COUNT(*)
                FROM evidence_items evidence
                WHERE evidence.raw_event_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM raw_events raw
                    WHERE raw.raw_event_id = evidence.raw_event_id
                  )
                """
            ).fetchone()[0],
            "missingRawEvidenceItems": connection.execute(
                """
                SELECT COUNT(*)
                FROM evidence_items evidence
                WHERE evidence.raw_event_id IS NULL
                   OR NOT EXISTS (
                     SELECT 1 FROM raw_events raw
                     WHERE raw.raw_event_id = evidence.raw_event_id
                   )
                """
            ).fetchone()[0],
            "recoveredEvidenceItems": sum(recovered_by_source.values()),
            "conflicts": sum(conflict_by_source.values()),
            "adapterRecords": connection.execute(
                "SELECT COUNT(*) FROM source_adapter_records"
            ).fetchone()[0],
        },
        "sources": sources,
    }


def write_source_adapter_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
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


def rebuild_source_adapter_snapshot(
    db_path=DEFAULT_DB_PATH,
    output_path=DEFAULT_OUTPUT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        result = run_source_adapter(connection)
        connection.commit()
        snapshot = build_source_adapter_snapshot(connection, result)
    finally:
        connection.close()
    write_source_adapter_snapshot(snapshot, output_path)
    return snapshot, result


def main():
    parser = argparse.ArgumentParser(description="运行凸性采集主干适配器")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot, result = rebuild_source_adapter_snapshot(args.db, args.output)
    print(
        json.dumps(
            {
                "status": "success",
                "snapshot": str(args.output),
                "adapter": result,
                "counts": snapshot["counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
