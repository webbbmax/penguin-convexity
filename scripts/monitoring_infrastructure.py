#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH, initialize_database


RULE_VERSION = "monitoring-infrastructure-c1.6.04"
SOURCE_DEFINITION = {
    "source_id": "monitoring-infrastructure-registry",
    "name": "项目监控基础设施",
    "source_type": "internal_registry",
    "url": "local://project-monitoring-targets",
    "access_method": "本地规则注册表",
}
SUPPORTED_COLLECTION_TYPES = {
    "github_repository",
    "defillama_protocol",
    "snapshot_space",
    "cactus_governance",
    "asset",
    "contract",
}
NON_OFFICIAL_GITHUB_AGGREGATORS = {"api-evangelist"}
TARGET_LABELS = {
    "official_website": "官网",
    "official_social": "X",
    "github_organization": "GitHub组织",
    "github_repository": "GitHub仓库",
    "defillama_protocol": "DefiLlama协议",
    "snapshot_space": "Snapshot空间",
    "cactus_governance": "Cactus治理",
    "asset": "受益资产",
    "contract": "资产合约",
}
STATUS_RANK = {
    "blocked": 1,
    "corroborated": 2,
    "verified": 3,
    "conflict": 4,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(*parts):
    payload = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def parse_json(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def normalized_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if not hostname:
        return ""
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse(
        ("https", hostname, path, "", "", "")
    )


def github_target(value):
    url = normalized_url(value)
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    owner = parts[0]
    if len(parts) == 1:
        return {
            "targetType": "github_organization",
            "targetValue": owner,
            "targetUrl": f"https://github.com/{owner}",
            "metadata": {"owner": owner},
        }
    if parts[1] in {"blob", "tree", "commit", "commits"}:
        return None
    repository = f"{owner}/{parts[1].removesuffix('.git')}"
    return {
        "targetType": "github_repository",
        "targetValue": repository,
        "targetUrl": f"https://github.com/{repository}",
        "metadata": {"repository": repository},
    }


def path_segment_after(value, marker):
    try:
        parts = [
            part
            for part in urllib.parse.urlparse(str(value or "")).path.split("/")
            if part
        ]
    except ValueError:
        return ""
    try:
        return urllib.parse.unquote(parts[parts.index(marker) + 1])
    except (ValueError, IndexError):
        return ""


def snapshot_space(value):
    text = str(value or "")
    marker = "snapshot.org/#/"
    if marker not in text.lower():
        return ""
    tail = text[text.lower().index(marker) + len(marker):]
    return urllib.parse.unquote(tail.split("/", 1)[0].split("?", 1)[0])


def source_reference(connection, source_record_id):
    if not source_record_id:
        return {}
    row = connection.execute(
        """
        SELECT raw_event_id, evidence_id
        FROM source_adapter_records
        WHERE source_record_id = ?
        ORDER BY processed_at DESC, adapter_record_id DESC
        LIMIT 1
        """,
        (source_record_id,),
    ).fetchone()
    return dict(row) if row else {}


def relation_for_project(project_status, relation_status):
    if project_status == "rejected":
        return "conflict"
    if project_status != "verified":
        return "blocked"
    if relation_status in {"pending", "unverified", "unknown", ""}:
        return "blocked"
    if relation_status in STATUS_RANK:
        return relation_status
    return "corroborated"


def collection_status(target_type, relation_status):
    if relation_status == "conflict":
        return "conflict"
    if relation_status == "blocked":
        return "blocked"
    if target_type in SUPPORTED_COLLECTION_TYPES:
        return "ready"
    return "registered"


def target_record(
    project,
    target_type,
    target_value,
    target_url="",
    source_id="",
    source_record_type="",
    source_record_id="",
    raw_event_id=None,
    evidence_id=None,
    relation_status="corroborated",
    verification_method="",
    gap_reason="",
    metadata=None,
    observed_at=None,
):
    target_value = str(target_value or "").strip()
    if not target_value:
        return None
    relation_status = relation_for_project(
        project["identity_status"],
        relation_status,
    )
    if relation_status == "blocked" and not gap_reason:
        gap_reason = "项目主体身份尚未核验，目标保留但不进入自动采集"
    if relation_status == "conflict" and not gap_reason:
        gap_reason = "项目主体或目标归属存在冲突"
    identity_key = stable_id(
        "monitoring-target",
        project["project_id"],
        target_type,
        target_value.casefold(),
    )
    return {
        "target_identity_key": identity_key,
        "project_id": project["project_id"],
        "case_id": project.get("case_id"),
        "project_name": project["canonical_name"],
        "project_identity_status": project["identity_status"],
        "target_type": target_type,
        "target_value": target_value,
        "target_url": target_url,
        "source_id": source_id,
        "source_record_type": source_record_type,
        "source_record_id": source_record_id,
        "raw_event_id": raw_event_id,
        "evidence_id": evidence_id,
        "relation_status": relation_status,
        "collection_status": collection_status(
            target_type,
            relation_status,
        ),
        "verification_method": verification_method,
        "gap_reason": gap_reason,
        "metadata_json": json.dumps(
            metadata or {},
            ensure_ascii=False,
            sort_keys=True,
        ),
        "observed_at": observed_at,
        "rule_version": RULE_VERSION,
    }


def choose_target(existing, candidate):
    if existing is None:
        return candidate
    existing_rank = STATUS_RANK.get(existing["relation_status"], 0)
    candidate_rank = STATUS_RANK.get(candidate["relation_status"], 0)
    if candidate_rank != existing_rank:
        return candidate if candidate_rank > existing_rank else existing
    existing_trace = int(bool(existing.get("raw_event_id"))) + int(
        bool(existing.get("evidence_id"))
    )
    candidate_trace = int(bool(candidate.get("raw_event_id"))) + int(
        bool(candidate.get("evidence_id"))
    )
    if candidate_trace != existing_trace:
        return candidate if candidate_trace > existing_trace else existing
    return candidate if (
        candidate.get("observed_at") or "",
        candidate.get("source_record_id") or "",
    ) > (
        existing.get("observed_at") or "",
        existing.get("source_record_id") or "",
    ) else existing


def compile_monitoring_targets(connection):
    connection.row_factory = sqlite3.Row
    projects = {}
    for row in connection.execute(
        """
        SELECT p.*,
               (
                 SELECT cc.case_id
                 FROM candidate_cases cc
                 WHERE cc.project_id = p.project_id
                 ORDER BY cc.updated_at DESC, cc.case_id DESC
                 LIMIT 1
               ) AS case_id
        FROM projects p
        ORDER BY p.canonical_name
        """
    ):
        projects[row["project_id"]] = dict(row)

    targets = {}
    authoritative_github_repositories = {
        project_id: set() for project_id in projects
    }
    authoritative_github_organizations = {
        project_id: set() for project_id in projects
    }

    def add(candidate):
        if not candidate:
            return
        key = candidate["target_identity_key"]
        targets[key] = choose_target(targets.get(key), candidate)

    for project in projects.values():
        website = normalized_url(project.get("website_domain"))
        if website:
            add(
                target_record(
                    project,
                    "official_website",
                    urllib.parse.urlparse(website).hostname,
                    website,
                    "monitoring-infrastructure-registry",
                    "project",
                    project["project_id"],
                    relation_status=(
                        "verified"
                        if project["identity_status"] == "verified"
                        else "blocked"
                    ),
                    verification_method="项目身份主表官网锚点",
                )
            )
        repository = github_target(project.get("official_repo"))
        if repository:
            if repository["targetType"] == "github_repository":
                authoritative_github_repositories[
                    project["project_id"]
                ].add(repository["targetValue"].casefold())
            else:
                authoritative_github_organizations[
                    project["project_id"]
                ].add(repository["targetValue"].casefold())
            add(
                target_record(
                    project,
                    repository["targetType"],
                    repository["targetValue"],
                    repository["targetUrl"],
                    "monitoring-infrastructure-registry",
                    "project",
                    project["project_id"],
                    relation_status=(
                        "verified"
                        if project["identity_status"] == "verified"
                        else "blocked"
                    ),
                    verification_method="项目身份主表官方代码锚点",
                    metadata=repository["metadata"],
                )
            )

    evidence_types = {
        "official_website",
        "official_social",
        "official_repository",
    }
    for row in connection.execute(
        """
        SELECT *
        FROM evidence_items
        WHERE project_id IS NOT NULL
          AND evidence_type IN ('official_website', 'official_social',
                                'official_repository')
        ORDER BY observed_at, evidence_id
        """
    ):
        project = projects.get(row["project_id"])
        if not project or row["evidence_type"] not in evidence_types:
            continue
        target_type = row["evidence_type"]
        target_url = normalized_url(row["source_url"])
        metadata = {}
        if target_type == "official_repository":
            repository = github_target(target_url)
            if not repository:
                continue
            target_type = repository["targetType"]
            target_value = repository["targetValue"]
            target_url = repository["targetUrl"]
            metadata = repository["metadata"]
            if target_type == "github_repository":
                authoritative_github_repositories[
                    project["project_id"]
                ].add(target_value.casefold())
            else:
                authoritative_github_organizations[
                    project["project_id"]
                ].add(target_value.casefold())
        elif target_type == "official_social":
            target_value = target_url
        else:
            target_value = (
                urllib.parse.urlparse(target_url).hostname if target_url else ""
            )
        relation = (
            "verified"
            if row["confidence"] == "高"
            and row["fact_boundary"] == "confirmed_fact"
            else "corroborated"
        )
        add(
            target_record(
                project,
                target_type,
                target_value,
                target_url,
                row["source_id"],
                "evidence_item",
                row["evidence_id"],
                row["raw_event_id"],
                row["evidence_id"],
                relation,
                f"{row['fact_boundary']} · 证据置信度{row['confidence']}",
                metadata=metadata,
                observed_at=row["observed_at"],
            )
        )

    discovery_map = {
        "discovery-defillama-protocols": "defillama_protocol",
        "discovery-github-repositories": "github_repository",
        "discovery-snapshot-spaces": "snapshot_space",
        "discovery-cactus-organizations": "cactus_governance",
    }
    for row in connection.execute(
        """
        SELECT *
        FROM source_discoveries
        WHERE matched_project_id IS NOT NULL
          AND matched_project_id != ''
        ORDER BY last_seen_at, source_discovery_id
        """
    ):
        project = projects.get(row["matched_project_id"])
        target_type = discovery_map.get(row["source_id"])
        if not project or not target_type:
            continue
        metadata = {
            "externalId": row["external_id"],
            "slug": row["slug"],
            "attributionConfidence": row["attribution_confidence"],
        }
        if target_type == "github_repository":
            repository = github_target(row["repository_url"])
            if not repository:
                continue
            target_type = repository["targetType"]
            target_value = repository["targetValue"]
            target_url = repository["targetUrl"]
            metadata.update(repository["metadata"])
            owner = target_value.split("/", 1)[0].casefold()
            if owner in NON_OFFICIAL_GITHUB_AGGREGATORS:
                continue
            official_anchor_matched = (
                target_value.casefold()
                in authoritative_github_repositories[project["project_id"]]
                or owner
                in authoritative_github_organizations[project["project_id"]]
            )
            metadata["officialAnchorMatched"] = official_anchor_matched
        elif target_type == "defillama_protocol":
            target_value = row["slug"] or row["external_id"]
            target_url = (
                f"https://defillama.com/protocol/{target_value}"
                if target_value
                else row["source_url"]
            )
        elif target_type == "snapshot_space":
            target_value = row["slug"] or row["external_id"]
            target_url = (
                f"https://snapshot.org/#/{target_value}"
                if target_value
                else row["source_url"]
            )
        else:
            target_value = row["slug"] or row["external_id"]
            target_url = row["source_url"]
        reference = source_reference(
            connection,
            row["source_discovery_id"],
        )
        relation_status = row["project_identity_status"]
        verification_method = (
            row["attribution_reason"]
            or "项目级来源发现身份归属"
        )
        gap_reason = (
            ""
            if relation_status in {"verified", "corroborated"}
            else "来源发现尚未通过项目归属核验"
        )
        if (
            target_type == "github_repository"
            and not metadata.get("officialAnchorMatched")
        ):
            relation_status = "blocked"
            verification_method = (
                "GitHub 机器发现仅作为线索，尚无官方主表或官方证据确认"
            )
            gap_reason = "仓库所有权尚未由官方代码入口交叉确认"
        add(
            target_record(
                project,
                target_type,
                target_value,
                target_url,
                row["source_id"],
                "source_discovery",
                row["source_discovery_id"],
                reference.get("raw_event_id"),
                reference.get("evidence_id"),
                relation_status,
                verification_method,
                gap_reason=gap_reason,
                metadata=metadata,
                observed_at=row["last_seen_at"],
            )
        )

    for row in connection.execute(
        """
        SELECT a.*, p.identity_status AS project_identity_status,
               p.canonical_name,
               (
                 SELECT cc.case_id
                 FROM candidate_cases cc
                 WHERE cc.project_id = a.project_id
                 ORDER BY cc.updated_at DESC, cc.case_id DESC
                 LIMIT 1
               ) AS case_id
        FROM assets a
        JOIN projects p ON p.project_id = a.project_id
        ORDER BY a.asset_id
        """
    ):
        project = projects.get(row["project_id"])
        relation = (
            "verified" if row["identity_status"] == "verified" else "blocked"
        )
        add(
            target_record(
                project,
                "asset",
                row["asset_id"],
                "",
                "machine-project-asset-identity",
                "asset",
                row["asset_id"],
                relation_status=relation,
                verification_method=(
                    f"资产身份状态 {row['identity_status']}"
                ),
                gap_reason=(
                    ""
                    if relation == "verified"
                    else "资产身份尚未通过核验"
                ),
                metadata={
                    "symbol": row["symbol"],
                    "chain": row["chain"],
                    "assetType": row["asset_type"],
                    "captureGrade": row["capture_grade"],
                },
                observed_at=row["updated_at"],
            )
        )

    for row in connection.execute(
        """
        SELECT c.*, a.project_id, a.symbol, p.identity_status AS project_status
        FROM asset_contracts c
        JOIN assets a ON a.asset_id = c.asset_id
        JOIN projects p ON p.project_id = a.project_id
        ORDER BY c.asset_contract_id
        """
    ):
        project = projects.get(row["project_id"])
        if row["identity_status"] == "conflict":
            relation = "conflict"
        elif row["identity_status"] == "verified":
            relation = "verified"
        elif row["identity_status"] == "market_matched":
            relation = "corroborated"
        else:
            relation = "blocked"
        reference = source_reference(
            connection,
            row["asset_contract_id"],
        )
        add(
            target_record(
                project,
                "contract",
                f"{row['network_id']}:{row['contract_address'].lower()}",
                row["source_url"],
                row["source_id"],
                "asset_contract",
                row["asset_contract_id"],
                reference.get("raw_event_id"),
                reference.get("evidence_id"),
                relation,
                row["verification_method"]
                or f"合约身份状态 {row['identity_status']}",
                gap_reason=(
                    ""
                    if relation in {"verified", "corroborated"}
                    else "合约身份尚未通过核验"
                ),
                metadata={
                    "assetId": row["asset_id"],
                    "symbol": row["symbol"],
                    "networkId": row["network_id"],
                    "contractAddress": row["contract_address"],
                    "isPrimary": bool(row["is_primary"]),
                },
                observed_at=row["verified_at"] or row["observed_at"],
            )
        )

    return {
        "projects": projects,
        "targets": sorted(
            targets.values(),
            key=lambda item: (
                item["project_name"].casefold(),
                item["target_type"],
                item["target_value"].casefold(),
            ),
        ),
    }


def comparable(record):
    fields = (
        "project_id",
        "case_id",
        "target_type",
        "target_value",
        "target_url",
        "source_id",
        "source_record_type",
        "source_record_id",
        "raw_event_id",
        "evidence_id",
        "relation_status",
        "collection_status",
        "verification_method",
        "gap_reason",
        "metadata_json",
        "observed_at",
        "rule_version",
    )
    return {field: record.get(field) for field in fields}


def persist_monitoring_targets(connection, generated_at=None):
    generated_at = generated_at or utc_now()
    compiled = compile_monitoring_targets(connection)
    desired = {
        item["target_identity_key"]: item for item in compiled["targets"]
    }
    current = {
        row["target_identity_key"]: dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM project_monitoring_targets
            WHERE publication_status = 'published'
            """
        )
    }
    inserted = 0
    changed = 0
    unchanged = 0
    retired = 0

    for identity_key, existing in current.items():
        candidate = desired.get(identity_key)
        if candidate and comparable(existing) == comparable(candidate):
            unchanged += 1
            continue
        connection.execute(
            """
            UPDATE project_monitoring_targets
            SET publication_status = 'superseded'
            WHERE monitoring_target_id = ?
            """,
            (existing["monitoring_target_id"],),
        )
        if candidate:
            changed += 1
        else:
            retired += 1

    for identity_key, candidate in desired.items():
        existing = current.get(identity_key)
        if existing and comparable(existing) == comparable(candidate):
            continue
        fingerprint = hashlib.sha256(
            json.dumps(
                comparable(candidate),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        monitoring_target_id = (
            f"monitor-target-{stable_id(identity_key, fingerprint, generated_at)}"
        )
        connection.execute(
            """
            INSERT INTO project_monitoring_targets (
              monitoring_target_id, target_identity_key, project_id, case_id,
              target_type, target_value, target_url, source_id,
              source_record_type, source_record_id, raw_event_id, evidence_id,
              relation_status, collection_status, verification_method,
              gap_reason, metadata_json, observed_at, generated_at,
              publication_status, rule_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, 'published', ?)
            """,
            (
                monitoring_target_id,
                identity_key,
                candidate["project_id"],
                candidate["case_id"],
                candidate["target_type"],
                candidate["target_value"],
                candidate["target_url"],
                candidate["source_id"],
                candidate["source_record_type"],
                candidate["source_record_id"],
                candidate["raw_event_id"],
                candidate["evidence_id"],
                candidate["relation_status"],
                candidate["collection_status"],
                candidate["verification_method"],
                candidate["gap_reason"],
                candidate["metadata_json"],
                candidate["observed_at"],
                generated_at,
                candidate["rule_version"],
            ),
        )
        inserted += 1

    connection.commit()
    published = [
        dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM project_monitoring_targets
            WHERE publication_status = 'published'
            ORDER BY project_id, target_type, target_value
            """
        )
    ]
    status_counts = {}
    type_counts = {}
    projects_with_ready = set()
    projects_with_targets = set()
    for item in published:
        status_counts[item["collection_status"]] = (
            status_counts.get(item["collection_status"], 0) + 1
        )
        type_counts[item["target_type"]] = (
            type_counts.get(item["target_type"], 0) + 1
        )
        projects_with_targets.add(item["project_id"])
        if item["collection_status"] == "ready":
            projects_with_ready.add(item["project_id"])
    return {
        "version": "C1.6-06",
        "ruleVersion": RULE_VERSION,
        "generatedAt": generated_at,
        "projectsReviewed": len(compiled["projects"]),
        "targetsPublished": len(published),
        "recordsInserted": inserted,
        "changedTargets": changed,
        "unchangedTargets": unchanged,
        "retiredTargets": retired,
        "projectsWithTargets": len(projects_with_targets),
        "projectsWithReadyTargets": len(projects_with_ready),
        "statusCounts": status_counts,
        "typeCounts": type_counts,
        "errors": [],
    }


def latest_monitoring_targets(connection):
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT target.*, p.canonical_name AS project_name,
                   p.identity_status AS project_identity_status
            FROM project_monitoring_targets target
            JOIN projects p ON p.project_id = target.project_id
            WHERE target.publication_status = 'published'
            ORDER BY p.canonical_name, target.target_type, target.target_value
            """
        )
    ]


def main():
    parser = argparse.ArgumentParser(
        description="重建凸性项目监控目标注册表"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    initialize_database(args.db, backup=False)
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        result = persist_monitoring_targets(connection)
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
