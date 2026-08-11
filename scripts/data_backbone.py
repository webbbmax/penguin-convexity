#!/usr/bin/env python3
"""C1.7 maximum-funnel data backbone.

Normalizes the immutable raw-event ledger, keeps local cursor/health state,
retains unattributed evidence, and compiles reusable entity watchers. It does
not score projects or publish an investment action.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH, initialize_database


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "data-backbone-snapshot.js"
DISCOVERY_CURSOR_PATH = PROJECT_ROOT / "data" / "source-discovery-cursors.json"
RULE_VERSION = "c1.7-entity-watcher-v1"
SOURCE_DEFINITION = {
    "source_id": "data-backbone-registry",
    "name": "C1.7 最大漏斗数据主干",
    "source_type": "internal_pipeline",
    "url": "local://data-backbone-v2",
    "access_method": "Local SQLite",
}
SOFTWARE_SOURCE_DEFINITION = {
    "source_id": "evidence-github-releases-packages",
    "name": "GitHub 发布与包清单",
    "source_type": "official_code_api",
    "url": "https://api.github.com",
    "access_method": "GitHub REST API",
}
PACKAGE_MANIFESTS = {
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "foundry.toml", "hardhat.config.js", "hardhat.config.ts",
    "requirements.txt", "pnpm-workspace.yaml",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(prefix, *parts):
    material = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def normalized_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def safe_json(value, fallback=None):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {} if fallback is None else fallback


def nested_value(value, keys):
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for child in value.values():
            candidate = nested_value(child, keys)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = nested_value(child, keys)
            if candidate:
                return candidate
    return ""


def github_json(path, timeout=10):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Penguin-Convexity-C1.7",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("BUYI_GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.github.com{path}", headers=headers
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response), dict(response.headers)


def collect_repository_software(repository, timeout=10):
    result = {
        "repository": repository["target_value"],
        "projectId": repository["project_id"],
        "releases": [],
        "manifests": [],
        "errors": [],
    }
    repo = repository["target_value"].strip("/")
    try:
        releases, _headers = github_json(
            f"/repos/{repo}/releases?per_page=5", timeout
        )
        if isinstance(releases, list):
            result["releases"] = releases
    except urllib.error.HTTPError as error:
        result["errors"].append({
            "channel": "release", "status": error.code,
            "message": f"GitHub release HTTP {error.code}",
        })
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        result["errors"].append({
            "channel": "release", "status": 0, "message": str(error),
        })
    try:
        contents, _headers = github_json(f"/repos/{repo}/contents", timeout)
        if isinstance(contents, list):
            result["manifests"] = [
                item for item in contents
                if item.get("type") == "file" and item.get("name") in PACKAGE_MANIFESTS
            ]
    except urllib.error.HTTPError as error:
        result["errors"].append({
            "channel": "package", "status": error.code,
            "message": f"GitHub contents HTTP {error.code}",
        })
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        result["errors"].append({
            "channel": "package", "status": 0, "message": str(error),
        })
    return result


def collect_software_mainline(connection, ingestion_run_id="", timeout=10):
    repositories = connection.execute(
        """
        SELECT DISTINCT target_value, project_id
        FROM watcher_definitions
        WHERE publication_status='published'
          AND watcher_type='software_release'
          AND watcher_status='ready'
        ORDER BY target_value
        """
    ).fetchall()
    collected = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(repositories)))) as executor:
        futures = {
            executor.submit(collect_repository_software, row, timeout): row
            for row in repositories
        }
        for future in as_completed(futures):
            try:
                collected.append(future.result())
            except Exception as error:
                row = futures[future]
                collected.append({
                    "repository": row["target_value"],
                    "projectId": row["project_id"],
                    "releases": [], "manifests": [],
                    "errors": [{"channel": "repository", "status": 0, "message": str(error)}],
                })
    now = utc_now()
    inserted = 0
    duplicates = 0
    release_rows = 0
    package_rows = 0
    errors = []
    for item in collected:
        repo = item["repository"]
        project_id = item["projectId"]
        errors.extend({"repository": repo, **error} for error in item["errors"])
        for release in item["releases"]:
            release_rows += 1
            selected = {
                "repository": repo,
                "releaseId": release.get("id"),
                "tagName": release.get("tag_name", ""),
                "name": release.get("name", ""),
                "draft": bool(release.get("draft")),
                "prerelease": bool(release.get("prerelease")),
                "publishedAt": release.get("published_at"),
                "updatedAt": release.get("updated_at"),
                "author": (release.get("author") or {}).get("login", ""),
            }
            external_id = f"{repo}:release:{release.get('id')}:{release.get('updated_at') or ''}"
            raw_id = stable_id("raw-software-release", external_id)
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO raw_events (
                  raw_event_id, source_id, ingestion_run_id, external_id,
                  published_at, collected_at, content_hash, source_url, excerpt,
                  project_hint, event_type, raw_payload_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'software_release', ?, 'normalized')
                """,
                (
                    raw_id, SOFTWARE_SOURCE_DEFINITION["source_id"],
                    ingestion_run_id or None, external_id,
                    release.get("published_at"), now,
                    hashlib.sha256(json.dumps(selected, sort_keys=True).encode("utf-8")).hexdigest(),
                    release.get("html_url") or f"https://github.com/{repo}/releases",
                    f"{repo} 发布 {release.get('tag_name') or release.get('name') or release.get('id')}",
                    project_id, json.dumps(selected, ensure_ascii=False, sort_keys=True),
                ),
            )
            if connection.total_changes > before:
                inserted += 1
            else:
                duplicates += 1
        for manifest in item["manifests"]:
            package_rows += 1
            selected = {
                "repository": repo,
                "manifest": manifest.get("name", ""),
                "path": manifest.get("path", ""),
                "sha": manifest.get("sha", ""),
                "size": manifest.get("size", 0),
            }
            external_id = f"{repo}:package:{manifest.get('path')}:{manifest.get('sha')}"
            raw_id = stable_id("raw-package-manifest", external_id)
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO raw_events (
                  raw_event_id, source_id, ingestion_run_id, external_id,
                  collected_at, content_hash, source_url, excerpt, project_hint,
                  event_type, raw_payload_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'package_manifest', ?, 'normalized')
                """,
                (
                    raw_id, SOFTWARE_SOURCE_DEFINITION["source_id"],
                    ingestion_run_id or None, external_id, now,
                    manifest.get("sha") or hashlib.sha256(external_id.encode("utf-8")).hexdigest(),
                    manifest.get("html_url") or f"https://github.com/{repo}",
                    f"{repo} 根目录包清单 {manifest.get('name')}", project_id,
                    json.dumps(selected, ensure_ascii=False, sort_keys=True),
                ),
            )
            if connection.total_changes > before:
                inserted += 1
            else:
                duplicates += 1
    return {
        "repositories": len(repositories),
        "releaseRows": release_rows,
        "packageRows": package_rows,
        "inserted": inserted,
        "duplicates": duplicates,
        "errors": errors,
    }


def build_identity_index(connection):
    projects = {
        row["project_id"]: dict(row)
        for row in connection.execute(
            "SELECT project_id, canonical_name, identity_status FROM projects"
        )
    }
    case_to_project = {
        row["case_id"]: row["project_id"]
        for row in connection.execute(
            "SELECT case_id, project_id FROM candidate_cases WHERE project_id IS NOT NULL"
        )
    }
    names = {}
    for project_id, project in projects.items():
        names.setdefault(normalized_key(project["canonical_name"]), set()).add(project_id)
    for row in connection.execute(
        """
        SELECT project_id, alias_value
        FROM project_identity_aliases
        WHERE status = 'active' AND alias_type IN ('name', 'project_id')
        """
    ):
        names.setdefault(normalized_key(row["alias_value"]), set()).add(row["project_id"])

    symbol_projects = {}
    asset_ids = {}
    for row in connection.execute(
        "SELECT asset_id, project_id, symbol FROM assets"
    ):
        key = normalized_key(row["symbol"])
        symbol_projects.setdefault(key, set()).add(row["project_id"])
        asset_ids.setdefault((row["project_id"], key), row["asset_id"])

    contract_projects = {}
    contract_ids = {}
    for row in connection.execute(
        """
        SELECT ac.asset_contract_id, lower(ac.contract_address) AS address,
               a.project_id
        FROM asset_contracts ac
        JOIN assets a ON a.asset_id = ac.asset_id
        """
    ):
        contract_projects.setdefault(row["address"], set()).add(row["project_id"])
        contract_ids[(row["project_id"], row["address"])] = row["asset_contract_id"]
    return {
        "projects": projects,
        "cases": case_to_project,
        "names": names,
        "symbolProjects": symbol_projects,
        "assetIds": asset_ids,
        "contractProjects": contract_projects,
        "contractIds": contract_ids,
    }


def classify_entity(event_type):
    lowered = (event_type or "").lower()
    if "release" in lowered:
        return "release"
    if "package" in lowered:
        return "package"
    if "repository" in lowered or "code" in lowered or "github" in lowered:
        return "repository"
    if "governance" in lowered or "proposal" in lowered:
        return "governance"
    if "contract" in lowered:
        return "contract"
    if "network" in lowered or "chain" in lowered:
        return "network"
    if "asset" in lowered or "market" in lowered:
        return "asset"
    if "project" in lowered or "website" in lowered or "social" in lowered:
        return "project"
    return "unknown"


def classify_mainline(event_type, payload, chain_hint):
    lowered = (event_type or "").lower()
    if "release" in lowered:
        return "release"
    if "package" in lowered:
        return "package"
    if "repository" in lowered or "code" in lowered or "github" in lowered:
        return "git"
    chain_type = nested_value(payload, ("chainType", "chain_type"))
    chain_text = f"{chain_type} {chain_hint}".lower()
    if "solana" in chain_text:
        return "solana"
    if "evm" in chain_text or "ethereum" in chain_text or "contract" in lowered:
        return "evm"
    return "general"


def resolve_attribution(row, payload, index):
    direct = nested_value(payload, ("matched_project_id", "project_id", "projectId"))
    hint = row["project_hint"] or ""
    project_id = ""
    method = ""
    candidates = set()
    for value, candidate_method in ((direct, "payload_project_id"), (hint, "project_hint")):
        if value in index["projects"]:
            project_id, method = value, candidate_method
            break
        if value in index["cases"]:
            project_id, method = index["cases"][value], f"{candidate_method}_case"
            break
        candidates.update(index["names"].get(normalized_key(value), set()))
    payload_case = nested_value(payload, ("case_id", "caseId"))
    if not project_id and payload_case in index["cases"]:
        project_id, method = index["cases"][payload_case], "payload_case_id"
    address = nested_value(payload, ("contractAddress", "contract_address", "address")).lower()
    if not project_id and address:
        candidates.update(index["contractProjects"].get(address, set()))
        if len(index["contractProjects"].get(address, set())) == 1:
            method = "contract_address"
    symbol_key = normalized_key(row["asset_hint"])
    if not project_id and len(candidates) == 0 and symbol_key:
        symbol_candidates = index["symbolProjects"].get(symbol_key, set())
        if len(symbol_candidates) == 1:
            candidates.update(symbol_candidates)
            method = "unique_asset_symbol"
    if not project_id and len(candidates) == 1:
        project_id = next(iter(candidates))
        method = method or "unique_project_alias"
    if not project_id:
        status = "conflict" if len(candidates) > 1 else "unattributed"
        return "", status, method or "no_unique_identity_anchor", address
    identity_status = index["projects"][project_id]["identity_status"]
    status = "verified" if identity_status == "verified" else "corroborated"
    return project_id, status, method, address


def normalize_raw_events(connection, source_id="", range_from="", range_to=""):
    index = build_identity_index(connection)
    clauses = []
    params = []
    if source_id:
        clauses.append("r.source_id = ?")
        params.append(source_id)
    if range_from:
        clauses.append("COALESCE(r.published_at, r.collected_at) >= ?")
        params.append(range_from)
    if range_to:
        clauses.append("COALESCE(r.published_at, r.collected_at) <= ?")
        params.append(range_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""
        SELECT r.*, n.event_id AS existing_event_id, n.project_id AS old_project_id,
               n.entity_id AS old_entity_id, n.attribution_status AS old_attribution_status,
               n.content_hash AS old_content_hash, n.processing_status AS old_processing_status
        FROM raw_events r
        LEFT JOIN normalized_events_v2 n ON n.raw_event_id = r.raw_event_id
        {where}
        ORDER BY r.collected_at, r.raw_event_id
        """,
        params,
    ).fetchall()
    now = utc_now()
    counts = {"input": len(rows), "inserted": 0, "updated": 0, "duplicates": 0}
    for row in rows:
        payload = safe_json(row["raw_payload_json"])
        project_id, attribution_status, method, address = resolve_attribution(
            row, payload, index
        )
        entity_type = classify_entity(row["event_type"])
        mainline_type = classify_mainline(row["event_type"], payload, row["chain_hint"])
        entity_id = ""
        if entity_type == "project" and project_id:
            entity_id = project_id
        elif entity_type == "asset" and project_id:
            entity_id = index["assetIds"].get(
                (project_id, normalized_key(row["asset_hint"])), ""
            )
        elif entity_type in {"contract", "network"} and project_id and address:
            entity_id = index["contractIds"].get((project_id, address), "")
        elif entity_type in {"repository", "governance", "release", "package"}:
            entity_id = stable_id("entity", entity_type, row["source_url"] or row["external_id"])
        if project_id and attribution_status == "verified":
            evidence_grade = "confirmed" if row["source_url"] else "corroborated"
        elif project_id:
            evidence_grade = "corroborated"
        elif row["event_type"] in {"project_discovery", "network_token_discovery"}:
            evidence_grade = "weak"
        else:
            evidence_grade = "raw"
        processing_status = (
            "evidence_ready"
            if project_id and evidence_grade in {"confirmed", "corroborated"}
            else "attributed"
            if project_id
            else "normalized"
        )
        event_id = row["existing_event_id"] or stable_id("event-v2", row["raw_event_id"])
        compact_payload = json.dumps(
            {
                "sourceUrl": row["source_url"],
                "projectHint": row["project_hint"],
                "assetHint": row["asset_hint"],
                "chainHint": row["chain_hint"],
                "attributionMethod": method,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        changed = bool(
            row["existing_event_id"]
            and (
                row["old_project_id"] != (project_id or None)
                or row["old_entity_id"] != entity_id
                or row["old_attribution_status"] != attribution_status
                or row["old_content_hash"] != row["content_hash"]
                or row["old_processing_status"] != processing_status
            )
        )
        if not row["existing_event_id"]:
            counts["inserted"] += 1
        elif changed:
            counts["updated"] += 1
        else:
            counts["duplicates"] += 1
        if not row["existing_event_id"] or changed:
            connection.execute(
                """
                INSERT INTO normalized_events_v2 (
                  event_id, raw_event_id, source_id, source_record_type,
                  source_record_id, external_id, entity_type, entity_id,
                  project_id, event_type, mainline_type, event_time, collected_at,
                  raw_locator, content_hash, evidence_grade, attribution_status,
                  processing_status, payload_json, schema_version, created_at, updated_at
                ) VALUES (?, ?, ?, 'raw_event', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?)
                ON CONFLICT(raw_event_id) DO UPDATE SET
                  entity_type=excluded.entity_type,
                  entity_id=excluded.entity_id,
                  project_id=excluded.project_id,
                  event_type=excluded.event_type,
                  mainline_type=excluded.mainline_type,
                  event_time=excluded.event_time,
                  collected_at=excluded.collected_at,
                  raw_locator=excluded.raw_locator,
                  content_hash=excluded.content_hash,
                  evidence_grade=excluded.evidence_grade,
                  attribution_status=excluded.attribution_status,
                  processing_status=excluded.processing_status,
                  payload_json=excluded.payload_json,
                  updated_at=excluded.updated_at
                """,
                (
                    event_id, row["raw_event_id"], row["source_id"], row["raw_event_id"],
                    row["external_id"], entity_type, entity_id, project_id or None,
                    row["event_type"], mainline_type,
                    row["published_at"] or row["collected_at"], row["collected_at"],
                    f"sqlite:raw_events/{row['raw_event_id']}", row["content_hash"],
                    evidence_grade, attribution_status, processing_status,
                    compact_payload, now, now,
                ),
            )
        old_status = row["old_attribution_status"] or "unattributed"
        old_project = row["old_project_id"]
        if (not row["existing_event_id"] and project_id) or (
            row["existing_event_id"]
            and (old_project != (project_id or None) or old_status != attribution_status)
        ):
            connection.execute(
                """
                INSERT OR IGNORE INTO event_attribution_history (
                  attribution_id, event_id, from_project_id, to_project_id,
                  from_entity_id, to_entity_id, from_status, to_status,
                  attribution_method, reason, attributed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id(
                        "attribution", event_id, old_project, project_id,
                        old_status, attribution_status, method
                    ),
                    event_id, old_project, project_id or None,
                    row["old_entity_id"] or "", entity_id,
                    old_status, attribution_status, method,
                    "C1.7 identity-anchor attribution", now,
                ),
            )
        orphan = connection.execute(
            "SELECT attribution_status FROM orphan_events_v2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if project_id:
            if orphan and orphan["attribution_status"] != "resolved":
                connection.execute(
                    """
                    UPDATE orphan_events_v2
                    SET attribution_status='resolved', resolved_project_id=?,
                        resolved_entity_id=?, resolution_method=?, updated_at=?, resolved_at=?
                    WHERE event_id=?
                    """,
                    (project_id, entity_id, method, now, now, event_id),
                )
        else:
            orphan_status = "conflict" if attribution_status == "conflict" else "pending"
            connection.execute(
                """
                INSERT INTO orphan_events_v2 (
                  orphan_id, event_id, attribution_status, project_hint, asset_hint,
                  chain_hint, candidate_entity_type, candidate_entity_id, reason,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                  attribution_status=excluded.attribution_status,
                  project_hint=excluded.project_hint,
                  asset_hint=excluded.asset_hint,
                  chain_hint=excluded.chain_hint,
                  candidate_entity_type=excluded.candidate_entity_type,
                  candidate_entity_id=excluded.candidate_entity_id,
                  reason=excluded.reason,
                  updated_at=excluded.updated_at
                """,
                (
                    stable_id("orphan", event_id), event_id, orphan_status,
                    row["project_hint"], row["asset_hint"], row["chain_hint"],
                    entity_type, entity_id, method, now, now,
                ),
            )
    return counts


def upsert_node(connection, entity_type, canonical_key, display_name, project_id,
                status, source_type, source_id, metadata, now):
    node_id = stable_id("node", entity_type, canonical_key)
    connection.execute(
        """
        INSERT INTO entity_nodes (
          node_id, entity_type, canonical_key, display_name, project_id,
          identity_status, source_record_type, source_record_id, metadata_json,
          generated_at, publication_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published')
        ON CONFLICT(node_id) DO UPDATE SET
          display_name=excluded.display_name, project_id=excluded.project_id,
          identity_status=excluded.identity_status,
          source_record_type=excluded.source_record_type,
          source_record_id=excluded.source_record_id,
          metadata_json=excluded.metadata_json, generated_at=excluded.generated_at,
          publication_status='published'
        """,
        (
            node_id, entity_type, canonical_key, display_name, project_id,
            status, source_type, source_id,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True), now,
        ),
    )
    return node_id


def upsert_edge(connection, from_node, to_node, relation, status, source_id,
                source_record_id, raw_event_id, metadata, now):
    edge_id = stable_id("edge", from_node, to_node, relation)
    connection.execute(
        """
        INSERT INTO entity_edges (
          edge_id, from_node_id, to_node_id, relation_type, relation_status,
          source_id, source_record_id, raw_event_id, metadata_json, generated_at,
          publication_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published')
        ON CONFLICT(edge_id) DO UPDATE SET
          relation_status=excluded.relation_status, source_id=excluded.source_id,
          source_record_id=excluded.source_record_id,
          raw_event_id=excluded.raw_event_id, metadata_json=excluded.metadata_json,
          generated_at=excluded.generated_at, publication_status='published'
        """,
        (
            edge_id, from_node, to_node, relation, status, source_id,
            source_record_id, raw_event_id,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True), now,
        ),
    )


def upsert_watcher(connection, identity, project_id, node_id, watcher_type,
                   target_value, target_url, network_id, source_id, mode,
                   status, gap_reason, metadata, now):
    watcher_id = stable_id("watcher", identity)
    cursor_source_id = source_id if source_id and connection.execute(
        "SELECT 1 FROM source_cursors_v2 WHERE source_id=?", (source_id,)
    ).fetchone() else None
    connection.execute(
        """
        INSERT INTO watcher_definitions (
          watcher_id, watcher_identity_key, project_id, entity_node_id,
          watcher_type, target_value, target_url, network_id, source_id,
          cursor_source_id, collection_mode, watcher_status, gap_reason,
          metadata_json, generated_at, publication_status, rule_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?)
        ON CONFLICT(watcher_id) DO UPDATE SET
          project_id=excluded.project_id, entity_node_id=excluded.entity_node_id,
          target_value=excluded.target_value, target_url=excluded.target_url,
          network_id=excluded.network_id, source_id=excluded.source_id,
          cursor_source_id=excluded.cursor_source_id,
          collection_mode=excluded.collection_mode,
          watcher_status=excluded.watcher_status, gap_reason=excluded.gap_reason,
          metadata_json=excluded.metadata_json, generated_at=excluded.generated_at,
          publication_status='published', rule_version=excluded.rule_version
        """,
        (
            watcher_id, identity, project_id, node_id, watcher_type, target_value,
            target_url, network_id, source_id, cursor_source_id, mode, status,
            gap_reason, json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            now, RULE_VERSION,
        ),
    )


def compile_entity_watchers(connection):
    now = utc_now()
    connection.execute("UPDATE entity_nodes SET publication_status='superseded'")
    connection.execute("UPDATE entity_edges SET publication_status='superseded'")
    connection.execute("UPDATE watcher_definitions SET publication_status='superseded'")
    project_nodes = {}
    for row in connection.execute("SELECT * FROM projects"):
        status = "verified" if row["identity_status"] == "verified" else (
            "conflict" if row["identity_status"] == "conflict" else "pending"
        )
        project_nodes[row["project_id"]] = upsert_node(
            connection, "project", row["project_id"], row["canonical_name"],
            row["project_id"], status, "projects", row["project_id"], {}, now
        )
    network_nodes = {}
    for row in connection.execute("SELECT * FROM networks"):
        network_nodes[row["network_id"]] = upsert_node(
            connection, "network", row["network_id"], row["name"], None,
            "verified", "networks", row["network_id"],
            {"chainType": row["chain_type"], "chainId": row["chain_id"]}, now
        )
    asset_nodes = {}
    for row in connection.execute("SELECT * FROM assets"):
        status = "verified" if row["identity_status"] == "verified" else (
            "conflict" if row["identity_status"] == "conflict" else "pending"
        )
        node = upsert_node(
            connection, "asset", row["asset_id"], row["symbol"] or row["asset_id"],
            row["project_id"], status, "assets", row["asset_id"], {}, now
        )
        asset_nodes[row["asset_id"]] = node
        upsert_edge(
            connection, project_nodes[row["project_id"]], node,
            "project_has_asset", status, "", row["asset_id"], None, {}, now
        )
    for row in connection.execute(
        """
        SELECT ac.*, a.project_id, n.chain_type, n.name AS network_name
        FROM asset_contracts ac
        JOIN assets a ON a.asset_id=ac.asset_id
        JOIN networks n ON n.network_id=ac.network_id
        """
    ):
        relation = row["identity_status"]
        status = "verified" if relation == "verified" else (
            "corroborated" if relation == "market_matched" else
            "conflict" if relation == "conflict" else "blocked"
        )
        key = f"{row['network_id']}:{row['contract_address'].lower()}"
        node = upsert_node(
            connection, "contract", key, row["contract_address"], row["project_id"],
            status, "asset_contracts", row["asset_contract_id"],
            {"network": row["network_name"], "standard": row["contract_standard"]}, now
        )
        upsert_edge(
            connection, asset_nodes[row["asset_id"]], node, "asset_deployed_on",
            status, row["identity_source"], row["asset_contract_id"], None,
            {"networkNodeId": network_nodes[row["network_id"]]}, now
        )
        watcher_type = "solana_program" if row["chain_type"] == "Solana" else "evm_contract"
        upsert_watcher(
            connection, f"{watcher_type}:{key}", row["project_id"], node,
            watcher_type, row["contract_address"], "", row["network_id"],
            "chain-robinhood-blockscout" if watcher_type == "evm_contract" else "",
            "chain_rpc", "ready" if status in {"verified", "corroborated"} else status,
            "" if status in {"verified", "corroborated"} else "identity_not_verified",
            {"identityStatus": relation}, now,
        )
    for row in connection.execute(
        "SELECT * FROM project_monitoring_targets WHERE publication_status='published'"
    ):
        relation = row["relation_status"]
        status = row["collection_status"]
        if row["target_type"] in {"github_organization", "github_repository"}:
            node = upsert_node(
                connection, "repository", row["target_identity_key"],
                row["target_value"], row["project_id"], relation,
                "project_monitoring_targets", row["monitoring_target_id"],
                {"targetType": row["target_type"], "url": row["target_url"]}, now
            )
            upsert_edge(
                connection, project_nodes[row["project_id"]], node,
                "project_owns_repository", relation, row["source_id"],
                row["monitoring_target_id"], row["raw_event_id"], {}, now
            )
            for watcher_type, entity_type, relation_type in (
                ("git_activity", "repository", "project_monitors"),
                ("software_release", "release", "repository_publishes_release"),
                ("package_registry", "package", "project_publishes_package"),
            ):
                target_node = node
                if entity_type != "repository":
                    target_node = upsert_node(
                        connection, entity_type,
                        f"{row['target_identity_key']}:{entity_type}",
                        f"{row['target_value']} {entity_type}", row["project_id"],
                        relation, "project_monitoring_targets",
                        row["monitoring_target_id"], {}, now
                    )
                    edge_from = node if entity_type == "release" else project_nodes[row["project_id"]]
                    upsert_edge(
                        connection, edge_from, target_node, relation_type, relation,
                        row["source_id"], row["monitoring_target_id"],
                        row["raw_event_id"], {}, now
                    )
                upsert_watcher(
                    connection,
                    f"{watcher_type}:{row['target_identity_key']}",
                    row["project_id"], target_node, watcher_type,
                    row["target_value"], row["target_url"], None,
                    "evidence-github-official", "api", status, row["gap_reason"],
                    {"monitoringTargetId": row["monitoring_target_id"]}, now,
                )
        elif row["target_type"] in {"snapshot_space", "cactus_governance", "defillama_protocol"}:
            entity_type = "protocol" if row["target_type"] == "defillama_protocol" else "governance"
            node = upsert_node(
                connection, entity_type, row["target_identity_key"], row["target_value"],
                row["project_id"], relation, "project_monitoring_targets",
                row["monitoring_target_id"], {"url": row["target_url"]}, now
            )
            upsert_edge(
                connection, project_nodes[row["project_id"]], node,
                "project_monitors" if entity_type == "protocol" else "project_uses_governance",
                relation, row["source_id"], row["monitoring_target_id"],
                row["raw_event_id"], {}, now
            )
            upsert_watcher(
                connection, f"{entity_type}:{row['target_identity_key']}",
                row["project_id"], node,
                "protocol" if entity_type == "protocol" else "governance",
                row["target_value"], row["target_url"], None, row["source_id"],
                "api", status, row["gap_reason"], {}, now,
            )
        elif row["target_type"] == "official_website":
            upsert_watcher(
                connection, f"website:{row['target_identity_key']}", row["project_id"],
                project_nodes[row["project_id"]], "website", row["target_value"],
                row["target_url"], None, row["source_id"], "poll", status,
                row["gap_reason"], {}, now,
            )
    return {
        "nodes": connection.execute(
            "SELECT COUNT(*) FROM entity_nodes WHERE publication_status='published'"
        ).fetchone()[0],
        "edges": connection.execute(
            "SELECT COUNT(*) FROM entity_edges WHERE publication_status='published'"
        ).fetchone()[0],
        "watchers": connection.execute(
            "SELECT COUNT(*) FROM watcher_definitions WHERE publication_status='published'"
        ).fetchone()[0],
    }


def provider_cursor(source_id):
    if source_id != "discovery-cactus-organizations" or not DISCOVERY_CURSOR_PATH.exists():
        return ""
    data = safe_json(DISCOVERY_CURSOR_PATH.read_text(encoding="utf-8"))
    return str(data.get("cactusAfterCursor") or "")


def source_diagnostics(connection):
    result = []
    for source in connection.execute("SELECT * FROM sources ORDER BY source_id"):
        latest_stat = connection.execute(
            """
            SELECT rss.*, r.status AS run_status
            FROM run_source_stats rss JOIN runs r ON r.run_id=rss.run_id
            WHERE rss.source_id=? ORDER BY rss.started_at DESC LIMIT 1
            """,
            (source["source_id"],),
        ).fetchone()
        raw = connection.execute(
            """
            SELECT COUNT(*) AS total, MAX(collected_at) AS last_event,
                   MAX(COALESCE(published_at, collected_at)) AS high_water
            FROM raw_events WHERE source_id=?
            """,
            (source["source_id"],),
        ).fetchone()
        latest_error = connection.execute(
            """
            SELECT re.message FROM run_errors re
            WHERE re.source_id=? ORDER BY re.last_seen_at DESC LIMIT 1
            """,
            (source["source_id"],),
        ).fetchone()
        target_count = connection.execute(
            """
            SELECT COUNT(*) FROM project_monitoring_targets
            WHERE publication_status='published' AND source_id=?
            """,
            (source["source_id"],),
        ).fetchone()[0]
        error_text = (latest_error["message"] if latest_error else "").lower()
        last_count = latest_stat["collected_count"] if latest_stat else 0
        last_status = latest_stat["status"] if latest_stat else ""
        if any(token in error_text for token in ("quota", "rate limit", "429", "额度")):
            state, diagnosis = "quota_exhausted", "最近一次失败指向额度或速率限制。"
        elif latest_stat and latest_stat["status"] == "failed":
            state, diagnosis = "failed", "最近一次来源任务失败，需要单独重试。"
        elif (
            source["source_type"].startswith("internal_")
            and latest_stat
            and latest_stat["status"] in {"success", "no_data"}
        ):
            state, diagnosis = "healthy", "内部衍生任务按设计写入结构化表，不要求重复生成原始事件。"
        elif latest_stat and latest_stat["collected_count"] > 0 and not raw["last_event"]:
            state, diagnosis = "silent", "采集器报告有结果，但原始事件账本没有写入。"
        elif latest_stat and latest_stat["status"] == "no_data":
            state, diagnosis = "true_zero", "来源明确返回零条数据，不视为采集故障。"
        elif latest_stat and latest_stat["status"] == "success" and last_count == 0:
            state, diagnosis = "true_zero", "来源任务成功且明确返回零条数据。"
        elif raw["total"] > 0:
            state, diagnosis = "healthy", "原始事件账本已有可追溯记录。"
        elif target_count > 0:
            state, diagnosis = "rule_gap", "已有监控目标，但尚无原始事件进入主干。"
        elif source["status"] == "active":
            state, diagnosis = "unknown", "来源已启用，但尚无足够运行记录判断。"
        else:
            state, diagnosis = "unknown", "来源未启用或尚未运行。"
        result.append({
            "sourceId": source["source_id"], "state": state,
            "diagnosis": diagnosis, "rawCount": raw["total"],
            "lastEvent": raw["last_event"], "highWater": raw["high_water"],
            "lastRunId": latest_stat["run_id"] if latest_stat else None,
            "lastAttempt": latest_stat["started_at"] if latest_stat else None,
            "lastSuccess": (
                latest_stat["finished_at"]
                if latest_stat and latest_stat["status"] in {"success", "no_data"}
                else None
            ),
            "lastStatus": last_status, "lastCount": last_count,
        })
    return result


def update_runtime_state(connection, replay_run_id, diagnostics, now):
    gaps_detected = 0
    gaps_recovered = 0
    for item in diagnostics:
        old_cursor = connection.execute(
            "SELECT * FROM source_cursors_v2 WHERE source_id=?", (item["sourceId"],)
        ).fetchone()
        bad_state = item["state"] in {"silent", "failed", "quota_exhausted", "rule_gap"}
        if bad_state:
            gap_status = "open"
            gap_from = old_cursor["last_success_at"] if old_cursor else item["lastSuccess"]
            gap_to = item["lastAttempt"] or now
            gap_reason = item["diagnosis"]
            if not old_cursor or old_cursor["gap_status"] not in {"open", "replaying"}:
                gaps_detected += 1
        elif old_cursor and old_cursor["gap_status"] in {"open", "replaying"}:
            gap_status, gap_from, gap_to, gap_reason = "resolved", old_cursor["gap_from"], now, "本轮已恢复连续写入。"
            gaps_recovered += 1
        else:
            gap_status, gap_from, gap_to, gap_reason = "none", None, None, ""
        provider_value = provider_cursor(item["sourceId"])
        cursor_value = provider_value or item["highWater"] or (
            old_cursor["cursor_value"] if old_cursor else ""
        )
        cursor_kind = "hybrid" if provider_value else "event_high_water"
        backlog = connection.execute(
            """
            SELECT COUNT(*) FROM raw_events r
            LEFT JOIN normalized_events_v2 n ON n.raw_event_id=r.raw_event_id
            WHERE r.source_id=? AND n.event_id IS NULL
            """,
            (item["sourceId"],),
        ).fetchone()[0]
        failures = (
            (old_cursor["consecutive_failures"] if old_cursor else 0) + 1
            if item["state"] in {"failed", "quota_exhausted", "silent"}
            else 0
        )
        connection.execute(
            """
            INSERT INTO source_cursors_v2 (
              source_id, cursor_kind, cursor_value, replay_from_cursor,
              high_water_event_time, last_attempt_at, last_success_at, last_event_at,
              last_result_count, backlog_count, consecutive_failures, gap_status,
              gap_from, gap_to, gap_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              cursor_kind=excluded.cursor_kind,
              replay_from_cursor=source_cursors_v2.cursor_value,
              cursor_value=excluded.cursor_value,
              high_water_event_time=excluded.high_water_event_time,
              last_attempt_at=COALESCE(excluded.last_attempt_at, source_cursors_v2.last_attempt_at),
              last_success_at=COALESCE(excluded.last_success_at, source_cursors_v2.last_success_at),
              last_event_at=COALESCE(excluded.last_event_at, source_cursors_v2.last_event_at),
              last_result_count=excluded.last_result_count,
              backlog_count=excluded.backlog_count,
              consecutive_failures=excluded.consecutive_failures,
              gap_status=excluded.gap_status, gap_from=excluded.gap_from,
              gap_to=excluded.gap_to, gap_reason=excluded.gap_reason,
              updated_at=excluded.updated_at
            """,
            (
                item["sourceId"], cursor_kind, cursor_value,
                old_cursor["cursor_value"] if old_cursor else "",
                item["highWater"], item["lastAttempt"], item["lastSuccess"],
                item["lastEvent"], item["lastCount"], backlog, failures,
                gap_status, gap_from, gap_to, gap_reason, now,
            ),
        )
        old_health = connection.execute(
            "SELECT * FROM source_health_v2 WHERE source_id=?", (item["sourceId"],)
        ).fetchone()
        connection.execute(
            """
            INSERT INTO source_health_v2 (
              source_id, health_state, last_run_id, last_attempt_at, last_success_at,
              last_event_at, last_status, last_result_count, quota_remaining,
              silence_reason, diagnosis, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              health_state=excluded.health_state, last_run_id=excluded.last_run_id,
              last_attempt_at=excluded.last_attempt_at,
              last_success_at=COALESCE(excluded.last_success_at, source_health_v2.last_success_at),
              last_event_at=excluded.last_event_at, last_status=excluded.last_status,
              last_result_count=excluded.last_result_count,
              silence_reason=excluded.silence_reason, diagnosis=excluded.diagnosis,
              checked_at=excluded.checked_at
            """,
            (
                item["sourceId"], item["state"], item["lastRunId"],
                item["lastAttempt"], item["lastSuccess"], item["lastEvent"],
                item["lastStatus"], item["lastCount"],
                item["diagnosis"] if item["state"] == "silent" else "",
                item["diagnosis"], now,
            ),
        )
        if not old_health or old_health["health_state"] != item["state"] or old_health["diagnosis"] != item["diagnosis"]:
            connection.execute(
                """
                INSERT INTO source_health_history (
                  health_record_id, source_id, replay_run_id, health_state,
                  last_run_id, result_count, diagnosis, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id("health", item["sourceId"], replay_run_id, item["state"]),
                    item["sourceId"], replay_run_id, item["state"], item["lastRunId"],
                    item["lastCount"], item["diagnosis"], now,
                ),
            )
    return gaps_detected, gaps_recovered


def predicted_gap_counts(connection, diagnostics):
    detected = 0
    recovered = 0
    for item in diagnostics:
        old_cursor = connection.execute(
            "SELECT gap_status FROM source_cursors_v2 WHERE source_id=?",
            (item["sourceId"],),
        ).fetchone()
        bad_state = item["state"] in {"silent", "failed", "quota_exhausted", "rule_gap"}
        old_status = old_cursor["gap_status"] if old_cursor else "none"
        if bad_state and old_status not in {"open", "replaying"}:
            detected += 1
        elif not bad_state and old_status in {"open", "replaying"}:
            recovered += 1
    return detected, recovered


def run_data_backbone(connection, mode="incremental", source_id="", range_from="", range_to="",
                      collect_software=False, ingestion_run_id="", timeout=10):
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    started_at = utc_now()
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, status,
          schedule_text, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'active', '随全量更新和单项任务运行', ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          name=excluded.name, source_type=excluded.source_type,
          url=excluded.url, access_method=excluded.access_method,
          status='active', updated_at=excluded.updated_at
        """,
        (
            SOURCE_DEFINITION["source_id"], SOURCE_DEFINITION["name"],
            SOURCE_DEFINITION["source_type"], SOURCE_DEFINITION["url"],
            SOURCE_DEFINITION["access_method"], started_at, started_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, status,
          schedule_text, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'active', '随最大漏斗数据主干更新', ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          name=excluded.name, source_type=excluded.source_type,
          url=excluded.url, access_method=excluded.access_method,
          status='active', updated_at=excluded.updated_at
        """,
        (
            SOFTWARE_SOURCE_DEFINITION["source_id"],
            SOFTWARE_SOURCE_DEFINITION["name"],
            SOFTWARE_SOURCE_DEFINITION["source_type"],
            SOFTWARE_SOURCE_DEFINITION["url"],
            SOFTWARE_SOURCE_DEFINITION["access_method"], started_at, started_at,
        ),
    )
    replay_run_id = stable_id("backbone-run", started_at, mode, source_id, range_from, range_to)
    software = (
        collect_software_mainline(connection, ingestion_run_id, timeout)
        if collect_software else
        {"repositories": 0, "releaseRows": 0, "packageRows": 0,
         "inserted": 0, "duplicates": 0, "errors": []}
    )
    if collect_software and ingestion_run_id:
        software_status = (
            "success" if not software["errors"] and (software["releaseRows"] + software["packageRows"]) > 0
            else "no_data" if not software["errors"]
            else "partial_success" if (software["releaseRows"] + software["packageRows"]) > 0
            else "failed"
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, duplicate_count,
              matched_count, filtered_count, shadow_added_count,
              active_added_count, failed_count, filter_reason_summary_json,
              error_message
            ) VALUES (?, ?, ?, 'github_release_package_v1', ?, ?, ?, ?, ?, ?,
                      0, 0, 0, ?, ?, ?)
            """,
            (
                stable_id("run-source", ingestion_run_id, "github_release_package_v1"),
                ingestion_run_id, SOFTWARE_SOURCE_DEFINITION["source_id"],
                software_status, started_at, utc_now(),
                software["releaseRows"] + software["packageRows"],
                software["duplicates"], software["inserted"], len(software["errors"]),
                json.dumps({
                    "repositories": software["repositories"],
                    "releaseRows": software["releaseRows"],
                    "packageRows": software["packageRows"],
                }, ensure_ascii=False),
                "; ".join(error["message"] for error in software["errors"][:5]),
            ),
        )
        for error in software["errors"]:
            quota = error.get("status") in {403, 429}
            message = (
                f"GitHub quota or rate limit: {error['message']}"
                if quota else error["message"]
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO run_errors (
                  error_id, run_id, source_id, task_name, error_type, message,
                  retryable, retry_status, attempts, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'not_requested', 1, ?, ?)
                """,
                (
                    stable_id(
                        "error", ingestion_run_id, error.get("repository"),
                        error.get("channel"), error.get("status")
                    ),
                    ingestion_run_id, SOFTWARE_SOURCE_DEFINITION["source_id"],
                    f"GitHub {error.get('channel')} · {error.get('repository')}",
                    "quota_exhausted" if quota else "source_error",
                    message, started_at, utc_now(),
                ),
            )
    normalized = normalize_raw_events(connection, source_id, range_from, range_to)
    diagnostics = source_diagnostics(connection)
    # Cursor rows are required before watcher rows can reference them.
    for item in diagnostics:
        if not connection.execute(
            "SELECT 1 FROM source_cursors_v2 WHERE source_id=?", (item["sourceId"],)
        ).fetchone():
            connection.execute(
                """
                INSERT INTO source_cursors_v2 (
                  source_id, cursor_value, high_water_event_time, last_event_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (item["sourceId"], item["highWater"] or "", item["highWater"], item["lastEvent"], started_at),
            )
    graph = compile_entity_watchers(connection)
    orphan_count = connection.execute(
        "SELECT COUNT(*) FROM orphan_events_v2 WHERE attribution_status IN ('pending','conflict')"
    ).fetchone()[0]
    cursor_from = ""
    cursor_to = ""
    if source_id:
        cursor = connection.execute(
            "SELECT cursor_value, replay_from_cursor FROM source_cursors_v2 WHERE source_id=?",
            (source_id,),
        ).fetchone()
        if cursor:
            cursor_from, cursor_to = cursor["replay_from_cursor"], cursor["cursor_value"]
    expected_gaps_detected, expected_gaps_recovered = predicted_gap_counts(
        connection, diagnostics
    )
    connection.execute(
        """
        INSERT INTO event_replay_runs (
          replay_run_id, source_id, mode, cursor_from, cursor_to, range_from,
          range_to, input_count, inserted_count, updated_count, duplicate_count,
          orphan_count, gap_detected_count, gap_recovered_count, status,
          error_message, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', '', ?, ?)
        """,
        (
            replay_run_id, source_id or None, mode, cursor_from or "", cursor_to or "",
            range_from or None, range_to or None, normalized["input"],
            normalized["inserted"], normalized["updated"], normalized["duplicates"],
            orphan_count, expected_gaps_detected, expected_gaps_recovered,
            started_at, utc_now(),
        ),
    )
    gaps_detected, gaps_recovered = update_runtime_state(
        connection, replay_run_id, diagnostics, utc_now()
    )
    # Replay rows are immutable; record gap counts in the returned acceptance data.
    return {
        "status": "success", "replayRunId": replay_run_id, "mode": mode,
        "normalized": normalized, "orphanEvents": orphan_count,
        "gapsDetected": gaps_detected, "gapsRecovered": gaps_recovered,
        "entityGraph": graph,
        "softwareCollection": software,
    }


def build_data_backbone_snapshot(connection):
    connection.row_factory = sqlite3.Row
    scalar = lambda query, params=(): connection.execute(query, params).fetchone()[0]
    health = {
        row["health_state"]: row["total"]
        for row in connection.execute(
            "SELECT health_state, COUNT(*) total FROM source_health_v2 GROUP BY health_state"
        )
    }
    mainlines = {}
    for kind in ("git", "release", "package", "evm", "solana"):
        mainlines[kind] = {
            "events": scalar(
                "SELECT COUNT(*) FROM normalized_events_v2 WHERE mainline_type=?", (kind,)
            ),
            "watchers": scalar(
                "SELECT COUNT(*) FROM watcher_definitions WHERE publication_status='published' AND watcher_type IN (?, ?)",
                (
                    kind if kind in {"evm", "solana"} else {
                        "git": "git_activity", "release": "software_release", "package": "package_registry"
                    }[kind],
                    {"evm": "evm_contract", "solana": "solana_program"}.get(kind, "__none__"),
                ),
            ),
            "ready": scalar(
                "SELECT COUNT(*) FROM watcher_definitions WHERE publication_status='published' AND watcher_status='ready' AND watcher_type=?",
                ({"git": "git_activity", "release": "software_release", "package": "package_registry", "evm": "evm_contract", "solana": "solana_program"}[kind],),
            ),
        }
    latest_replay = connection.execute(
        "SELECT * FROM event_replay_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    continuity = {
        "sources": scalar("SELECT COUNT(*) FROM source_cursors_v2"),
        "openGaps": scalar("SELECT COUNT(*) FROM source_cursors_v2 WHERE gap_status IN ('open','replaying')"),
        "resolvedGaps": scalar("SELECT COUNT(*) FROM source_cursors_v2 WHERE gap_status='resolved'"),
        "backlog": scalar("SELECT COALESCE(SUM(backlog_count),0) FROM source_cursors_v2"),
    }
    orphan = {
        "pending": scalar("SELECT COUNT(*) FROM orphan_events_v2 WHERE attribution_status='pending'"),
        "conflict": scalar("SELECT COUNT(*) FROM orphan_events_v2 WHERE attribution_status='conflict'"),
        "resolved": scalar("SELECT COUNT(*) FROM orphan_events_v2 WHERE attribution_status='resolved'"),
    }
    return {
        "version": "C1.7",
        "generatedAt": utc_now(),
        "boundary": "数据主干只负责连续采集、标准化、归属和监控编译；不改变凸性评分、当前结论或动作。",
        "eventSchema": {
            "version": 2,
            "rawEvents": scalar("SELECT COUNT(*) FROM raw_events"),
            "normalizedEvents": scalar("SELECT COUNT(*) FROM normalized_events_v2"),
            "attributedEvents": scalar("SELECT COUNT(*) FROM normalized_events_v2 WHERE project_id IS NOT NULL"),
            "traceableEvents": scalar("SELECT COUNT(*) FROM normalized_events_v2 WHERE raw_locator<>'' AND content_hash<>''"),
        },
        "continuity": continuity,
        "sourceHealth": health,
        "orphanEvidence": orphan,
        "entityGraph": {
            "nodes": scalar("SELECT COUNT(*) FROM entity_nodes WHERE publication_status='published'"),
            "edges": scalar("SELECT COUNT(*) FROM entity_edges WHERE publication_status='published'"),
            "watchers": scalar("SELECT COUNT(*) FROM watcher_definitions WHERE publication_status='published'"),
            "readyWatchers": scalar("SELECT COUNT(*) FROM watcher_definitions WHERE publication_status='published' AND watcher_status='ready'"),
            "blockedWatchers": scalar("SELECT COUNT(*) FROM watcher_definitions WHERE publication_status='published' AND watcher_status IN ('blocked','conflict')"),
        },
        "mainlines": mainlines,
        "latestReplay": dict(latest_replay) if latest_replay else None,
        "healthRows": [
            dict(row) for row in connection.execute(
                """
                SELECT h.source_id, s.name, h.health_state, h.last_event_at,
                       h.last_result_count, h.diagnosis, c.gap_status, c.backlog_count
                FROM source_health_v2 h JOIN sources s ON s.source_id=h.source_id
                JOIN source_cursors_v2 c ON c.source_id=h.source_id
                ORDER BY CASE h.health_state WHEN 'healthy' THEN 1 WHEN 'true_zero' THEN 2 ELSE 0 END,
                         h.source_id
                """
            )
        ],
        "orphanRows": [
            dict(row) for row in connection.execute(
                """
                SELECT o.orphan_id, o.attribution_status, o.project_hint,
                       o.asset_hint, o.chain_hint, o.reason, n.source_id,
                       n.event_type, n.event_time, n.raw_locator
                FROM orphan_events_v2 o JOIN normalized_events_v2 n ON n.event_id=o.event_id
                WHERE o.attribution_status IN ('pending','conflict')
                ORDER BY n.event_time DESC LIMIT 100
                """
            )
        ],
    }


def write_data_backbone_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "window.PENGUIN_CONVEXITY_DATA_BACKBONE = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    return snapshot


def rebuild_data_backbone(db_path=DEFAULT_DB_PATH, mode="incremental", source_id="",
                          range_from="", range_to="", snapshot_path=DEFAULT_SNAPSHOT_PATH,
                          collect_software=False, timeout=10):
    initialize_database(db_path, backup=False)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        result = run_data_backbone(
            connection, mode, source_id, range_from, range_to,
            collect_software=collect_software, timeout=timeout,
        )
        connection.commit()
        snapshot = write_data_backbone_snapshot(
            build_data_backbone_snapshot(connection), snapshot_path
        )
        result["snapshot"] = snapshot
        return result
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="运行 C1.7 最大漏斗数据主干")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--mode", choices=("incremental", "replay", "gap_recovery"), default="incremental")
    parser.add_argument("--source", default="")
    parser.add_argument("--from-time", default="")
    parser.add_argument("--to-time", default="")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--collect-software", action="store_true")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(rebuild_data_backbone(
        args.db, args.mode, args.source, args.from_time, args.to_time, args.snapshot,
        collect_software=args.collect_software, timeout=args.timeout,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
