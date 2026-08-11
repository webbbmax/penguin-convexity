#!/usr/bin/env python3
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_alias(value, alias_type="name"):
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if not text:
        return ""
    if alias_type == "domain":
        parsed = urlparse(text if "://" in text else f"https://{text}")
        text = (parsed.hostname or text).removeprefix("www.")
    elif alias_type == "repository":
        parsed = urlparse(text if "://" in text else f"https://{text}")
        text = f"{parsed.hostname or ''}{parsed.path}".removesuffix(".git")
    return "".join(character for character in text if character.isalnum())


def alias_record(
    project_id,
    alias_type,
    raw_value,
    source_kind,
    source_record_id,
    confidence,
    observed_at,
):
    normalized = normalize_alias(raw_value, alias_type)
    if not project_id or not normalized:
        return None
    seed = "\x1f".join(
        (
            str(project_id),
            alias_type,
            normalized,
            source_kind,
            str(source_record_id or ""),
        )
    )
    return {
        "aliasId": f"project-alias-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}",
        "projectId": str(project_id),
        "aliasType": alias_type,
        "aliasValue": str(raw_value or "").strip(),
        "normalizedValue": normalized,
        "sourceKind": source_kind,
        "sourceRecordId": str(source_record_id or ""),
        "confidence": confidence,
        "observedAt": observed_at or utc_now(),
    }


def collect_project_aliases(connection):
    records = []

    def add(*args):
        record = alias_record(*args)
        if record:
            records.append(record)

    for row in connection.execute(
        """
        SELECT project_id, canonical_name, website_domain, official_repo,
               identity_status, updated_at
        FROM projects
        WHERE identity_status <> 'rejected'
        """
    ):
        add(
            row["project_id"],
            "project_id",
            row["project_id"],
            "projects",
            row["project_id"],
            "strong",
            row["updated_at"],
        )
        add(
            row["project_id"],
            "name",
            row["canonical_name"],
            "projects",
            row["project_id"],
            "strong",
            row["updated_at"],
        )
        add(
            row["project_id"],
            "domain",
            row["website_domain"],
            "projects",
            row["project_id"],
            "strong",
            row["updated_at"],
        )
        add(
            row["project_id"],
            "repository",
            row["official_repo"],
            "projects",
            row["project_id"],
            "strong",
            row["updated_at"],
        )

    for row in connection.execute(
        """
        SELECT source_discovery_id, source_id, external_id, canonical_name,
               normalized_name, slug, website_domain, repository_url,
               matched_project_id, attribution_confidence, updated_at
        FROM source_discoveries
        WHERE matched_project_id IS NOT NULL
          AND matched_project_id <> ''
          AND project_identity_status IN ('verified', 'corroborated')
        """
    ):
        confidence = (
            "strong"
            if row["attribution_confidence"] in ("high", "verified")
            else "medium"
        )
        for alias_type, value in (
            ("name", row["canonical_name"]),
            ("name", row["normalized_name"]),
            ("source_external_id", row["external_id"]),
            ("source_slug", row["slug"]),
            ("domain", row["website_domain"]),
            ("repository", row["repository_url"]),
        ):
            add(
                row["matched_project_id"],
                alias_type,
                value,
                "source_discoveries",
                row["source_discovery_id"],
                confidence,
                row["updated_at"],
            )
        add(
            row["matched_project_id"],
            "source_qualified_id",
            f"{row['source_id']}:{row['external_id']}",
            "source_discoveries",
            row["source_discovery_id"],
            confidence,
            row["updated_at"],
        )

    for row in connection.execute(
        """
        SELECT identity_review_id, provider, canonical_name, coingecko_id,
               website_domain, matched_project_id, promoted_project_id,
               confidence, reviewed_at
        FROM discovery_identity_reviews
        WHERE resolution_status NOT IN ('rejected', 'conflict')
          AND (
            (promoted_project_id IS NOT NULL AND promoted_project_id <> '')
            OR (matched_project_id IS NOT NULL AND matched_project_id <> '')
          )
        """
    ):
        project_id = row["promoted_project_id"] or row["matched_project_id"]
        confidence = "strong" if row["confidence"] == "high" else "medium"
        for alias_type, value in (
            ("name", row["canonical_name"]),
            ("coingecko_id", row["coingecko_id"]),
            ("domain", row["website_domain"]),
        ):
            add(
                project_id,
                alias_type,
                value,
                "discovery_identity_reviews",
                row["identity_review_id"],
                confidence,
                row["reviewed_at"],
            )

    for row in connection.execute(
        """
        SELECT a.asset_id, a.project_id, a.chain, a.contract_address,
               a.identity_status, a.updated_at
        FROM assets a
        JOIN projects p ON p.project_id = a.project_id
        WHERE p.identity_status <> 'rejected'
          AND a.identity_status IN ('verified', 'pending')
          AND a.contract_address <> ''
        """
    ):
        add(
            row["project_id"],
            "contract",
            row["contract_address"],
            "assets",
            row["asset_id"],
            "strong" if row["identity_status"] == "verified" else "medium",
            row["updated_at"],
        )
        add(
            row["project_id"],
            "chain_contract",
            f"{row['chain']}:{row['contract_address']}",
            "assets",
            row["asset_id"],
            "strong" if row["identity_status"] == "verified" else "medium",
            row["updated_at"],
        )

    unique = {}
    for record in records:
        unique[record["aliasId"]] = record
    return list(unique.values())


def alias_owner_index(connection, records=None):
    owners = {}
    for record in records or collect_project_aliases(connection):
        owners.setdefault(record["normalizedValue"], set()).add(record["projectId"])

    table_exists = connection.execute(
        """
        SELECT COUNT(*)
        FROM sqlite_master
        WHERE type = 'table' AND name = 'project_identity_aliases'
        """
    ).fetchone()[0]
    if table_exists:
        for row in connection.execute(
            """
            SELECT alias.project_id, alias.normalized_value
            FROM project_identity_aliases alias
            JOIN projects project ON project.project_id = alias.project_id
            WHERE alias.status IN ('active', 'historical')
              AND project.identity_status <> 'rejected'
            """
        ):
            owners.setdefault(row["normalized_value"], set()).add(row["project_id"])
    return {
        normalized: next(iter(project_ids))
        for normalized, project_ids in owners.items()
        if len(project_ids) == 1
    }


def sync_project_identity_aliases(connection, observed_at=None):
    records = collect_project_aliases(connection)
    owner_sets = {}
    for record in records:
        owner_sets.setdefault(record["normalizedValue"], set()).add(
            record["projectId"]
        )
    now = observed_at or utc_now()
    for record in records:
        status = (
            "conflict"
            if len(owner_sets[record["normalizedValue"]]) > 1
            else "active"
        )
        connection.execute(
            """
            INSERT INTO project_identity_aliases (
              alias_id, project_id, alias_type, alias_value, normalized_value,
              source_kind, source_record_id, confidence, status,
              first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alias_id) DO UPDATE SET
              alias_value = excluded.alias_value,
              confidence = excluded.confidence,
              status = excluded.status,
              last_seen_at = excluded.last_seen_at,
              updated_at = excluded.updated_at
            WHERE project_identity_aliases.alias_value IS NOT excluded.alias_value
               OR project_identity_aliases.confidence IS NOT excluded.confidence
               OR project_identity_aliases.status IS NOT excluded.status
               OR project_identity_aliases.last_seen_at IS NOT excluded.last_seen_at
            """,
            (
                record["aliasId"],
                record["projectId"],
                record["aliasType"],
                record["aliasValue"],
                record["normalizedValue"],
                record["sourceKind"],
                record["sourceRecordId"],
                record["confidence"],
                status,
                record["observedAt"],
                record["observedAt"],
                now,
                now,
            ),
        )
    return {
        "records": len(records),
        "active": sum(
            len(owner_sets[record["normalizedValue"]]) == 1
            for record in records
        ),
        "conflicts": sum(
            len(owner_sets[record["normalizedValue"]]) > 1
            for record in records
        ),
    }
