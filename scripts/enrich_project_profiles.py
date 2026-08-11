#!/usr/bin/env python3
import hashlib
import json
from urllib.parse import urlparse


PROFILE_ENRICHMENT_VERSION = "C1.4-01"
IDENTITY_SOURCE_ID = "identity-coingecko-registry"
SOURCE_DEFINITION = {
    "source_id": "formal-project-profile-enrichment",
    "name": "正式项目身份与官方入口",
    "source_type": "derived_registry",
    "url": "local://formal-project-profile-enrichment",
    "access_method": "CoinGecko身份登记与独立同域来源交叉核验",
}


def parse_json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def domain_from_url(value):
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    domain = (parsed.hostname or "").casefold()
    return domain[4:] if domain.startswith("www.") else domain


def http_urls(values):
    return [
        str(value).strip()
        for value in values
        if str(value or "").strip().startswith(("http://", "https://"))
    ]


def register_sources(connection, now):
    for definition in (
        {
            "source_id": IDENTITY_SOURCE_ID,
            "name": "CoinGecko 资产身份注册",
            "source_type": "identity_registry",
            "url": "https://api.coingecko.com/api/v3",
            "access_method": "Demo API",
        },
        SOURCE_DEFINITION,
    ):
        connection.execute(
            """
            INSERT INTO sources (
              source_id, name, source_type, url, access_method, scope,
              confidence, conflict_risk, status, schedule_text,
              last_checked_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'convexity', '中', '低', 'active',
                    '凸性更新中心单项更新', ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              status = 'active',
              last_checked_at = excluded.last_checked_at,
              updated_at = excluded.updated_at
            """,
            (
                definition["source_id"],
                definition["name"],
                definition["source_type"],
                definition["url"],
                definition["access_method"],
                now,
                now,
                now,
            ),
        )


def latest_identity_reviews(connection):
    reviews = {}
    rows = connection.execute(
        """
        SELECT *
        FROM discovery_identity_reviews
        WHERE promoted_project_id IS NOT NULL
           OR matched_project_id IS NOT NULL
        ORDER BY reviewed_at DESC, identity_review_id DESC
        """
    )
    for row in rows:
        item = dict(row)
        project_ids = {
            item.get("promoted_project_id"),
            item.get("matched_project_id"),
        }
        for project_id in project_ids - {None, ""}:
            reviews.setdefault(project_id, item)
    return reviews


def trusted_source_discoveries(connection):
    records = {}
    rows = connection.execute(
        """
        SELECT discovery.*, source.name AS source_name
        FROM source_discoveries discovery
        JOIN sources source ON source.source_id = discovery.source_id
        WHERE discovery.matched_project_id IS NOT NULL
          AND discovery.status = 'active'
          AND discovery.project_identity_status = 'verified'
          AND discovery.attribution_confidence = 'high'
        ORDER BY discovery.last_seen_at DESC
        """
    )
    for row in rows:
        item = dict(row)
        records.setdefault(item["matched_project_id"], []).append(item)
    return records


def review_is_consistent(project, review):
    if not review:
        return False
    project_domain = domain_from_url(project["website_domain"])
    review_domain = domain_from_url(review["website_domain"])
    return bool(
        review["resolution_status"] in {"verified", "corroborated"}
        and review["official_contract_status"] in {"confirmed", "registry_matched"}
        and review["name_match_status"] == "match"
        and review["website_status"] == "accessible"
        and review_domain
        and (not project_domain or project_domain == review_domain)
    )


def corroborating_sources(project, review, discoveries):
    expected_domain = domain_from_url(
        project["website_domain"] or (review or {}).get("website_domain")
    )
    if not expected_domain:
        return []
    return [
        item
        for item in discoveries
        if domain_from_url(item.get("website_domain") or item.get("website_url"))
        == expected_domain
    ]


def insert_evidence(
    connection,
    *,
    stable_id,
    project_id,
    asset_id,
    evidence_type,
    source_id,
    source_url,
    summary,
    observed_at,
    confirmed=False,
):
    if not source_url:
        return False
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO evidence_items (
          evidence_id, project_id, asset_id, raw_event_id, evidence_type,
          stance, fact_boundary, confidence, observed_at, expires_at,
          source_id, source_url, summary, created_at
        )
        VALUES (?, ?, ?, NULL, ?, 'support', ?, ?, ?, NULL, ?, ?, ?, ?)
        """,
        (
            stable_id(
                "formal-profile-evidence",
                project_id,
                evidence_type,
                source_id,
                source_url,
            ),
            project_id,
            asset_id,
            evidence_type,
            "confirmed_fact" if confirmed else "high_confidence_inference",
            "高" if confirmed else "中",
            observed_at,
            source_id,
            source_url,
            summary,
            observed_at,
        ),
    )
    return bool(cursor.rowcount)


def record_change_event(
    connection,
    *,
    stable_id,
    run_id,
    now,
    project,
    asset,
    changes,
    basis,
    source_ids,
):
    if not changes:
        return
    summary = (
        f"{project['canonical_name']} 自动补齐 {len(changes)} 项身份或官方入口资料。"
    )
    payload = {
        "summary": summary,
        "changes": changes,
        "identityBasis": basis,
        "sourceIds": sorted(source_ids),
        "boundary": (
            "本记录只证明项目档案身份与官方入口的自动覆盖，"
            "不代表凸性质量、收益赔率或行动建议。"
        ),
        "version": PROFILE_ENRICHMENT_VERSION,
    }
    connection.execute(
        """
        INSERT INTO raw_events (
          raw_event_id, source_id, ingestion_run_id, external_id,
          published_at, collected_at, content_hash, source_url, excerpt,
          project_hint, asset_hint, chain_hint, event_type,
          raw_payload_json, status
        )
        VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?,
                'formal_project_profile_enrichment', ?, 'normalized')
        """,
        (
            stable_id("formal-profile-event", run_id, project["project_id"]),
            SOURCE_DEFINITION["source_id"],
            run_id,
            f"{run_id}:formal-profile:{project['project_id']}",
            now,
            hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            changes[0].get("sourceUrl", ""),
            summary,
            project["canonical_name"],
            asset["symbol"] if asset else "",
            asset["chain"] if asset else "",
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def persist_formal_project_enrichment(
    connection,
    run_id,
    now,
    stable_id,
):
    register_sources(connection, now)
    reviews = latest_identity_reviews(connection)
    discoveries = trusted_source_discoveries(connection)
    summary = {
        "projectsReviewed": 0,
        "identityVerified": 0,
        "anchorsAdded": 0,
        "websiteAdded": 0,
        "socialAdded": 0,
        "repositoryAdded": 0,
        "remainingIdentityPending": 0,
        "changedProjects": 0,
    }

    projects = list(
        connection.execute(
            """
            SELECT *
            FROM projects
            WHERE identity_status != 'rejected'
            ORDER BY canonical_name, project_id
            """
        )
    )
    for project_row in projects:
        project = dict(project_row)
        summary["projectsReviewed"] += 1
        asset_row = connection.execute(
            """
            SELECT *
            FROM assets
            WHERE project_id = ?
            ORDER BY updated_at DESC, asset_id
            LIMIT 1
            """,
            (project["project_id"],),
        ).fetchone()
        asset = dict(asset_row) if asset_row else None
        review = reviews.get(project["project_id"])
        project_discoveries = discoveries.get(project["project_id"], [])
        consistent_review = review_is_consistent(project, review)
        corroborators = corroborating_sources(
            project,
            review,
            project_discoveries,
        )
        official_contract_confirmed = bool(
            consistent_review
            and review["official_contract_status"] == "confirmed"
        )
        independently_corroborated = bool(consistent_review and corroborators)
        identity_can_verify = (
            official_contract_confirmed or independently_corroborated
        )
        changes = []
        source_ids = set()
        basis = []

        if consistent_review:
            source_ids.add(IDENTITY_SOURCE_ID)
            basis.append("CoinGecko 合约精确匹配、名称一致且官网可访问")
        if corroborators:
            source_ids.update(item["source_id"] for item in corroborators)
            basis.append(
                "独立来源以同一官网域名归属该项目"
            )
        if official_contract_confirmed:
            basis.append("官网正文确认同一合约")

        if (
            consistent_review
            and not project["website_domain"]
            and review["website_domain"]
        ):
            connection.execute(
                """
                UPDATE projects
                SET website_domain = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (review["website_domain"], now, project["project_id"]),
            )
            changes.append(
                {
                    "field": "官网",
                    "before": "",
                    "after": review["website_url"],
                    "sourceUrl": review["website_url"],
                }
            )
            summary["websiteAdded"] += 1

        review_repositories = (
            http_urls(parse_json(review["repo_urls_json"], []))
            if consistent_review
            else []
        )
        discovery_repositories = [
            item["repository_url"]
            for item in corroborators
            if str(item.get("repository_url") or "").startswith(
                ("http://", "https://")
            )
        ]
        repositories = review_repositories + [
            url for url in discovery_repositories if url not in review_repositories
        ]
        if not project["official_repo"] and repositories:
            connection.execute(
                """
                UPDATE projects
                SET official_repo = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (repositories[0], now, project["project_id"]),
            )
            changes.append(
                {
                    "field": "GitHub",
                    "before": "",
                    "after": repositories[0],
                    "sourceUrl": repositories[0],
                }
            )
            summary["repositoryAdded"] += 1

        asset_id = asset["asset_id"] if asset else None
        if consistent_review:
            website_added = insert_evidence(
                connection,
                stable_id=stable_id,
                project_id=project["project_id"],
                asset_id=asset_id,
                evidence_type="official_website",
                source_id=IDENTITY_SOURCE_ID,
                source_url=review["website_url"],
                summary=(
                    "CoinGecko 资产登记中的官网与当前项目域名一致，"
                    "且本轮访问成功。"
                ),
                observed_at=now,
                confirmed=official_contract_confirmed,
            )
            summary["anchorsAdded"] += website_added
            if website_added:
                changes.append(
                    {
                        "field": "官网入口证据",
                        "before": "",
                        "after": review["website_url"],
                        "sourceUrl": review["website_url"],
                    }
                )

            for social_url in http_urls(
                parse_json(review["social_urls_json"], [])
            ):
                if "x.com/" not in social_url and "twitter.com/" not in social_url:
                    continue
                added = insert_evidence(
                    connection,
                    stable_id=stable_id,
                    project_id=project["project_id"],
                    asset_id=asset_id,
                    evidence_type="official_social",
                    source_id=IDENTITY_SOURCE_ID,
                    source_url=social_url,
                    summary="CoinGecko 资产登记提供的项目官方 X 入口。",
                    observed_at=now,
                    confirmed=official_contract_confirmed,
                )
                summary["anchorsAdded"] += added
                summary["socialAdded"] += added
                if added:
                    changes.append(
                        {
                            "field": "官方 X",
                            "before": "",
                            "after": social_url,
                            "sourceUrl": social_url,
                        }
                    )

            for repository_url in repositories:
                repository_source = (
                    next(
                        (
                            item["source_id"]
                            for item in corroborators
                            if item.get("repository_url") == repository_url
                        ),
                        IDENTITY_SOURCE_ID,
                    )
                )
                added = insert_evidence(
                    connection,
                    stable_id=stable_id,
                    project_id=project["project_id"],
                    asset_id=asset_id,
                    evidence_type="official_repository",
                    source_id=repository_source,
                    source_url=repository_url,
                    summary=(
                        "项目身份来源登记的 GitHub 入口；"
                        "仅作为官方入口，不以代码活跃度替代产品采用。"
                    ),
                    observed_at=now,
                    confirmed=official_contract_confirmed,
                )
                summary["anchorsAdded"] += added
                summary["repositoryAdded"] += added
                if added:
                    changes.append(
                        {
                            "field": "GitHub 入口证据",
                            "before": "",
                            "after": repository_url,
                            "sourceUrl": repository_url,
                        }
                    )

        for discovery in corroborators:
            for evidence_type, column, label in (
                ("official_social", "social_url", "官方 X"),
                ("official_repository", "repository_url", "GitHub"),
            ):
                source_url = discovery.get(column) or ""
                if not source_url:
                    continue
                added = insert_evidence(
                    connection,
                    stable_id=stable_id,
                    project_id=project["project_id"],
                    asset_id=asset_id,
                    evidence_type=evidence_type,
                    source_id=discovery["source_id"],
                    source_url=source_url,
                    summary=(
                        f"{discovery['source_name']}以同一官网域名归属项目，"
                        f"并提供{label}入口。"
                    ),
                    observed_at=discovery["last_seen_at"] or now,
                )
                summary["anchorsAdded"] += added
                if added:
                    if evidence_type == "official_social":
                        summary["socialAdded"] += 1
                    else:
                        summary["repositoryAdded"] += 1
                    changes.append(
                        {
                            "field": f"{label}独立来源",
                            "before": "",
                            "after": source_url,
                            "sourceUrl": source_url,
                        }
                    )

        if project["identity_status"] != "verified" and identity_can_verify:
            connection.execute(
                """
                UPDATE projects
                SET identity_status = 'verified', updated_at = ?
                WHERE project_id = ?
                """,
                (now, project["project_id"]),
            )
            changes.append(
                {
                    "field": "项目主体身份",
                    "before": project["identity_status"],
                    "after": "verified",
                    "sourceUrl": review["website_url"],
                }
            )
            summary["identityVerified"] += 1

        if official_contract_confirmed and asset:
            connection.execute(
                """
                UPDATE assets
                SET identity_status = 'verified', updated_at = ?
                WHERE asset_id = ?
                """,
                (now, asset["asset_id"]),
            )
            connection.execute(
                """
                UPDATE asset_contracts
                SET identity_status = 'verified',
                    identity_source = '官网合约确认',
                    verified_at = ?,
                    verification_method = '官网正文包含同一合约',
                    updated_at = ?
                WHERE asset_id = ?
                  AND identity_status = 'market_matched'
                """,
                (now, now, asset["asset_id"]),
            )

        if changes:
            summary["changedProjects"] += 1
            record_change_event(
                connection,
                stable_id=stable_id,
                run_id=run_id,
                now=now,
                project=project,
                asset=asset,
                changes=changes,
                basis=basis,
                source_ids=source_ids,
            )

    summary["remainingIdentityPending"] = connection.execute(
        """
        SELECT COUNT(*)
        FROM projects
        WHERE identity_status = 'pending'
        """
    ).fetchone()[0]
    return summary
