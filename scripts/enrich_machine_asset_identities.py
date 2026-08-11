#!/usr/bin/env python3
import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from contract_tradeability import NETWORKS, request_json, user_environment


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "convexity.db"
RULE_VERSION = "machine-project-asset-identity-c1.5.0"
COINGECKO_LIST_URL = (
    "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
)
DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"
SOURCE_DEFINITION = {
    "source_id": "machine-project-asset-identity",
    "name": "机器项目资产身份核验",
    "source_type": "derived_identity_registry",
    "url": "multiple://defillama-coingecko",
    "access_method": "DefiLlama 协议登记与 CoinGecko 资产注册交叉核验",
}
COINGECKO_SOURCE = {
    "source_id": "identity-coingecko-registry",
    "name": "CoinGecko 资产身份注册",
    "source_type": "independent_asset_registry",
    "url": "https://api.coingecko.com/api/v3",
    "access_method": "Demo API",
}
DEFILLAMA_SOURCE_ID = "discovery-defillama-protocols"
PLATFORM_NETWORKS = {
    platform: network_id
    for network_id, definition in NETWORKS.items()
    for platform in definition.get("platformKeys", ())
}
FAMILY_SUFFIXES = {
    "dao",
    "finance",
    "fi",
    "network",
    "protocol",
    "token",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized(value):
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", str(value or "")).casefold()
        if character.isalnum()
    )


def name_tokens(value):
    tokens = re.findall(
        r"[a-z0-9]+",
        unicodedata.normalize("NFKD", str(value or "")).casefold(),
    )
    while tokens and (
        re.fullmatch(r"v\d+", tokens[-1])
        or tokens[-1] in FAMILY_SUFFIXES
    ):
        tokens.pop()
    return tokens


def family_name(value):
    return "".join(name_tokens(value))


def normalized_address(value):
    text = str(value or "").strip()
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return text.casefold()


def protocol_addresses(protocol):
    value = protocol.get("address")
    if isinstance(value, list):
        values = value
    else:
        values = re.split(r"[,;]", str(value or ""))
    return {
        normalized_address(item)
        for item in values
        if normalized_address(item) not in {"", "-", "null", "none"}
    }


def coin_contracts(coin):
    return {
        normalized_address(address)
        for address in (coin.get("platforms") or {}).values()
        if normalized_address(address)
    }


def coingecko_headers():
    api_key = user_environment("COINGECKO_DEMO_API_KEY")
    return {"x-cg-demo-api-key": api_key} if api_key else {}


def load_machine_projects(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        projects = []
        for project in connection.execute(
            """
            SELECT project.*
            FROM projects project
            WHERE project.identity_status != 'rejected'
              AND EXISTS (
                SELECT 1
                FROM source_discoveries discovery
                WHERE discovery.matched_project_id = project.project_id
                  AND discovery.source_id = ?
                  AND discovery.status = 'active'
              )
            ORDER BY project.canonical_name, project.project_id
            """,
            (DEFILLAMA_SOURCE_ID,),
        ):
            sources = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT *
                    FROM source_discoveries
                    WHERE matched_project_id = ?
                      AND source_id = ?
                      AND status = 'active'
                    ORDER BY external_id
                    """,
                    (project["project_id"], DEFILLAMA_SOURCE_ID),
                )
            ]
            projects.append({**dict(project), "sourceRecords": sources})
        return projects
    finally:
        connection.close()


def project_protocols(project, protocols_by_slug):
    return [
        protocols_by_slug[row["external_id"]]
        for row in project["sourceRecords"]
        if row["external_id"] in protocols_by_slug
    ]


def official_links(project, protocols):
    website = next(
        (
            str(protocol.get("url") or "").strip()
            for protocol in protocols
            if str(protocol.get("url") or "").startswith(("http://", "https://"))
        ),
        f"https://{project['website_domain']}" if project["website_domain"] else "",
    )
    twitter = next(
        (
            f"https://x.com/{str(protocol.get('twitter')).lstrip('@')}"
            for protocol in protocols
            if protocol.get("twitter")
        ),
        "",
    )
    repository = ""
    for protocol in protocols:
        github = protocol.get("github") or []
        if isinstance(github, str):
            github = [github]
        if github:
            repository = f"https://github.com/{str(github[0]).strip('/')}"
            break
    return {
        "website": website,
        "twitter": twitter,
        "repository": repository,
    }


def candidate_flags(project, protocols, coin):
    project_name = normalized(project["canonical_name"])
    project_family = family_name(project["canonical_name"])
    source_names = {
        normalized(row["canonical_name"]) for row in project["sourceRecords"]
    }
    source_families = {
        family_name(row["canonical_name"]) for row in project["sourceRecords"]
    }
    source_slugs = {
        normalized(row["slug"] or row["external_id"])
        for row in project["sourceRecords"]
    }
    explicit_ids = {
        normalized(protocol.get("gecko_id"))
        for protocol in protocols
        if protocol.get("gecko_id")
    }
    addresses = set().union(*(protocol_addresses(item) for item in protocols))
    symbols = {
        normalized(protocol.get("symbol"))
        for protocol in protocols
        if protocol.get("symbol")
    }
    coin_name = normalized(coin.get("name"))
    coin_family = family_name(coin.get("name"))
    coin_id = normalized(coin.get("id"))
    coin_symbol = normalized(coin.get("symbol"))
    matching_contracts = sorted(addresses & coin_contracts(coin))
    return {
        "explicitId": coin_id in explicit_ids,
        "contractMatch": bool(matching_contracts),
        "matchingContracts": matching_contracts,
        "projectNameMatch": coin_name == project_name or coin_id == project_name,
        "sourceNameMatch": coin_name in source_names or coin_id in source_names,
        "familyNameMatch": bool(
            coin_family
            and len(coin_family) >= 3
            and (coin_family == project_family or coin_family in source_families)
        ),
        "slugMatch": coin_id in source_slugs,
        "symbolMatch": not symbols or coin_symbol in symbols,
        "protocolSymbols": sorted(symbols),
        "protocolAddresses": sorted(addresses),
    }


def candidate_score(flags):
    return (
        60 * flags["explicitId"]
        + 45 * flags["contractMatch"]
        + 30 * flags["projectNameMatch"]
        + 24 * flags["slugMatch"]
        + 20 * flags["sourceNameMatch"]
        + 12 * flags["familyNameMatch"]
        + 10 * flags["symbolMatch"]
    )


def candidate_is_eligible(flags):
    name_aligned = (
        flags["projectNameMatch"]
        or flags["sourceNameMatch"]
        or flags["familyNameMatch"]
        or flags["slugMatch"]
    )
    return bool(
        (
            flags["explicitId"]
            and name_aligned
            and (flags["contractMatch"] or flags["symbolMatch"])
        )
        or (
            flags["contractMatch"]
            and flags["symbolMatch"]
            and name_aligned
        )
        or (
            flags["projectNameMatch"]
            and flags["slugMatch"]
            and flags["symbolMatch"]
        )
    )


def candidate_resolution(flags):
    name_aligned = (
        flags["projectNameMatch"]
        or flags["sourceNameMatch"]
        or flags["familyNameMatch"]
        or flags["slugMatch"]
    )
    if flags["contractMatch"] and flags["symbolMatch"] and name_aligned:
        return "verified", "high"
    return "corroborated", "medium"


def candidate_methods(flags):
    labels = []
    for field, label in (
        ("explicitId", "DefiLlama CoinGecko ID"),
        ("contractMatch", "登记合约精确一致"),
        ("projectNameMatch", "项目名称一致"),
        ("sourceNameMatch", "协议名称一致"),
        ("familyNameMatch", "项目家族名称一致"),
        ("slugMatch", "项目标识一致"),
        ("symbolMatch", "代币符号一致"),
    ):
        if flags[field]:
            labels.append(label)
    return labels


def evaluate_project(project, protocols, registry_indexes):
    by_id, by_name, by_contract = registry_indexes
    candidate_ids = set()
    for protocol in protocols:
        gecko_id = normalized(protocol.get("gecko_id"))
        if gecko_id and gecko_id in by_id:
            candidate_ids.add(by_id[gecko_id]["id"])
        for address in protocol_addresses(protocol):
            candidate_ids.update(item["id"] for item in by_contract.get(address, []))
    for value in [
        project["canonical_name"],
        *(row["canonical_name"] for row in project["sourceRecords"]),
    ]:
        candidate_ids.update(item["id"] for item in by_name.get(normalized(value), []))
    for row in project["sourceRecords"]:
        slug = normalized(row["slug"] or row["external_id"])
        if slug in by_id:
            candidate_ids.add(by_id[slug]["id"])

    assessed = []
    for coin_id in candidate_ids:
        coin = by_id[normalized(coin_id)]
        flags = candidate_flags(project, protocols, coin)
        assessed.append(
            {
                "coin": coin,
                "flags": flags,
                "score": candidate_score(flags),
                "eligible": candidate_is_eligible(flags),
            }
        )
    assessed.sort(
        key=lambda item: (-item["score"], item["coin"]["id"])
    )
    eligible = [item for item in assessed if item["eligible"]]
    links = official_links(project, protocols)
    source_url = next(
        (
            str(row.get("source_url") or "").strip()
            for row in project["sourceRecords"]
            if str(row.get("source_url") or "").startswith(
                ("http://", "https://")
            )
        ),
        DEFILLAMA_PROTOCOLS_URL,
    )
    base = {
        "projectId": project["project_id"],
        "projectName": project["canonical_name"],
        "sourceUrl": source_url,
        "resolutionStatus": "pending",
        "confidence": "low",
        "coingeckoId": "",
        "assetName": "",
        "symbol": "",
        "matchMethod": "",
        "platforms": {},
        "officialLinks": links,
        "reason": "",
        "evidence": [],
        "candidateCount": len(assessed),
    }
    if not eligible:
        parent_like = next(
            (
                item
                for item in assessed
                if item["flags"]["contractMatch"]
                and item["flags"]["symbolMatch"]
            ),
            None,
        )
        if parent_like:
            base["reason"] = (
                f"发现同合约资产 {parent_like['coin']['name']}，但项目名称未能确认"
                "它是本项目自身代币，可能是母协议资产，暂不自动归属。"
            )
        elif assessed:
            base["reason"] = (
                "发现名称、标识或符号近似的资产，但缺少合约与项目主体的严格交叉吻合。"
            )
        else:
            base["reason"] = (
                "DefiLlama 项目登记中未找到可与 CoinGecko 独立资产注册严格对应的代币。"
            )
        return base
    if len(eligible) > 1 and eligible[0]["score"] - eligible[1]["score"] < 20:
        base.update(
            {
                "resolutionStatus": "conflict",
                "reason": (
                    "多个 CoinGecko 资产同时满足当前项目证据，最高候选差距不足，"
                    "自动流程已阻断写入。"
                ),
                "evidence": [
                    {
                        "type": "identity_conflict",
                        "summary": (
                            f"冲突候选：{eligible[0]['coin']['id']}、"
                            f"{eligible[1]['coin']['id']}。"
                        ),
                        "factBoundary": "unverified_signal",
                    }
                ],
            }
        )
        return base

    selected = eligible[0]
    coin = selected["coin"]
    flags = selected["flags"]
    status, confidence = candidate_resolution(flags)
    methods = candidate_methods(flags)
    base.update(
        {
            "resolutionStatus": status,
            "confidence": confidence,
            "coingeckoId": coin["id"],
            "assetName": coin["name"],
            "symbol": str(coin.get("symbol") or "").upper(),
            "matchMethod": " + ".join(methods),
            "platforms": coin.get("platforms") or {},
            "reason": (
                f"DefiLlama 项目登记与 CoinGecko 资产注册通过"
                f"{'、'.join(methods)}完成自动交叉核验。"
            ),
            "evidence": [
                {
                    "type": "project_registry",
                    "summary": (
                        f"DefiLlama 登记项目 {project['canonical_name']}；"
                        f"CoinGecko 登记资产 {coin['name']}（{str(coin.get('symbol') or '').upper()}）。"
                    ),
                    "url": f"https://www.coingecko.com/en/coins/{coin['id']}",
                    "factBoundary": "high_confidence_inference",
                },
                {
                    "type": "matching_basis",
                    "summary": "；".join(methods),
                    "factBoundary": "high_confidence_inference",
                },
            ],
        }
    )
    return base


def registry_indexes(coins):
    by_id = {}
    by_name = {}
    by_contract = {}
    for coin in coins:
        coin_id = normalized(coin.get("id"))
        if not coin_id:
            continue
        by_id[coin_id] = coin
        by_name.setdefault(normalized(coin.get("name")), []).append(coin)
        for address in coin_contracts(coin):
            by_contract.setdefault(address, []).append(coin)
    return by_id, by_name, by_contract


def collect_machine_project_asset_identities(
    db_path=DEFAULT_DB_PATH,
    timeout=30,
):
    projects = load_machine_projects(db_path)
    errors = []
    try:
        coins = request_json(
            COINGECKO_LIST_URL,
            headers=coingecko_headers(),
            timeout=timeout,
        )
    except Exception as error:
        coins = []
        errors.append(
            {
                "provider": "coingecko_project_identity",
                "sourceUrl": COINGECKO_LIST_URL,
                "error": f"{type(error).__name__}: {error}",
            }
        )
    try:
        protocols = request_json(DEFILLAMA_PROTOCOLS_URL, timeout=timeout)
    except Exception as error:
        protocols = []
        errors.append(
            {
                "provider": "defillama_project_identity",
                "sourceUrl": DEFILLAMA_PROTOCOLS_URL,
                "error": f"{type(error).__name__}: {error}",
            }
        )
    if errors:
        return {
            "records": [],
            "errors": errors,
            "projectsQueued": len(projects),
            "registryAssets": len(coins),
            "protocolRecords": len(protocols),
        }
    protocols_by_slug = {
        str(item.get("slug") or ""): item
        for item in protocols
        if item.get("slug")
    }
    indexes = registry_indexes(coins)
    records = [
        evaluate_project(
            project,
            project_protocols(project, protocols_by_slug),
            indexes,
        )
        for project in projects
    ]
    return {
        "records": records,
        "errors": [],
        "projectsQueued": len(projects),
        "registryAssets": len(coins),
        "protocolRecords": len(protocols),
    }


def register_sources(connection, now):
    for definition, scope, confidence in (
        (SOURCE_DEFINITION, "convexity_identity", "中"),
        (COINGECKO_SOURCE, "convexity_identity", "中"),
    ):
        connection.execute(
            """
            INSERT INTO sources (
              source_id, name, source_type, url, access_method, scope,
              confidence, conflict_risk, status, schedule_text,
              last_checked_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, '低', 'active',
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
                scope,
                confidence,
                now,
                now,
                now,
            ),
        )


def latest_project_coin_mapping(connection, project_id):
    row = connection.execute(
        """
        SELECT *
        FROM project_asset_identity_reviews review
        WHERE review.project_id = ?
        ORDER BY review.reviewed_at DESC, review.project_asset_review_id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def competing_project_mapping(connection, project_id, coingecko_id):
    return connection.execute(
        """
        SELECT review.project_id, project.canonical_name
        FROM project_asset_identity_reviews review
        JOIN projects project ON project.project_id = review.project_id
        WHERE review.coingecko_id = ?
          AND review.project_id != ?
          AND review.asset_id IS NOT NULL
          AND review.resolution_status IN ('verified', 'corroborated')
          AND NOT EXISTS (
            SELECT 1
            FROM project_asset_identity_reviews newer
            WHERE newer.project_id = review.project_id
              AND (
                newer.reviewed_at > review.reviewed_at
                OR (
                  newer.reviewed_at = review.reviewed_at
                  AND newer.project_asset_review_id > review.project_asset_review_id
                )
              )
          )
        LIMIT 1
        """,
        (coingecko_id, project_id),
    ).fetchone()


def platform_contract_rows(platforms):
    rows = []
    for platform, address in (platforms or {}).items():
        network_id = PLATFORM_NETWORKS.get(platform)
        clean_address = str(address or "").strip()
        if network_id and clean_address:
            rows.append(
                {
                    "networkId": network_id,
                    "networkName": NETWORKS[network_id]["name"],
                    "address": clean_address,
                }
            )
    rows.sort(
        key=lambda item: (
            item["networkId"] != "ethereum-mainnet",
            item["networkName"],
        )
    )
    return rows


def insert_review(connection, record, run_id, now, stable_id, asset_id=None):
    connection.execute(
        """
        INSERT INTO project_asset_identity_reviews (
          project_asset_review_id, project_id, run_id, reviewed_at,
          provider, resolution_status, confidence, coingecko_id,
          asset_name, symbol, match_method, asset_id, platforms_json,
          official_links_json, reason, evidence_json, rule_version
        )
        VALUES (?, ?, ?, ?, 'defillama+coingecko', ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?)
        """,
        (
            stable_id("project-asset-review", run_id, record["projectId"]),
            record["projectId"],
            run_id,
            now,
            record["resolutionStatus"],
            record["confidence"],
            record["coingeckoId"],
            record["assetName"],
            record["symbol"],
            record["matchMethod"],
            asset_id,
            json.dumps(record["platforms"], ensure_ascii=False, sort_keys=True),
            json.dumps(record["officialLinks"], ensure_ascii=False, sort_keys=True),
            record["reason"],
            json.dumps(record["evidence"], ensure_ascii=False),
            RULE_VERSION,
        ),
    )


def write_project_asset(connection, record, now, stable_id):
    project_id = record["projectId"]
    coin_id = record["coingeckoId"]
    asset = connection.execute(
        """
        SELECT *
        FROM assets
        WHERE project_id = ?
        ORDER BY updated_at DESC, asset_id
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if asset and normalized(asset["symbol"]) != normalized(record["symbol"]):
        return {
            "status": "conflict",
            "reason": (
                f"项目已有资产 {asset['symbol']}，本次候选为 {record['symbol']}，"
                "自动流程不覆盖既有资产。"
            ),
        }
    contract_rows = platform_contract_rows(record["platforms"])
    primary = contract_rows[0] if contract_rows else None
    asset_id = (
        asset["asset_id"]
        if asset
        else stable_id("project-asset", project_id, coin_id)
    )
    identity_status = (
        "verified"
        if record["resolutionStatus"] == "verified"
        else "pending"
    )
    connection.execute(
        """
        INSERT INTO assets (
          asset_id, project_id, symbol, chain, contract_address,
          asset_type, capture_grade, identity_status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'token', 'unknown', ?, ?, ?)
        ON CONFLICT(asset_id) DO UPDATE SET
          symbol = excluded.symbol,
          chain = excluded.chain,
          contract_address = excluded.contract_address,
          identity_status = CASE
            WHEN excluded.identity_status = 'verified' THEN 'verified'
            ELSE assets.identity_status
          END,
          updated_at = excluded.updated_at
        """,
        (
            asset_id,
            project_id,
            record["symbol"],
            primary["networkName"] if primary else "网络待映射",
            primary["address"] if primary else "",
            identity_status,
            now,
            now,
        ),
    )
    for index, contract in enumerate(contract_rows):
        connection.execute(
            """
            INSERT INTO asset_contracts (
              asset_contract_id, asset_id, network_id, contract_address,
              contract_standard, is_primary, identity_status,
              identity_source, source_id, source_url, observed_at,
              verified_at, verification_method, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'unknown', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, network_id, contract_address) DO UPDATE SET
              is_primary = excluded.is_primary,
              identity_status = excluded.identity_status,
              identity_source = excluded.identity_source,
              source_id = excluded.source_id,
              source_url = excluded.source_url,
              verified_at = excluded.verified_at,
              verification_method = excluded.verification_method,
              updated_at = excluded.updated_at
            """,
            (
                stable_id(
                    "project-asset-contract",
                    asset_id,
                    contract["networkId"],
                    contract["address"],
                ),
                asset_id,
                contract["networkId"],
                contract["address"],
                1 if index == 0 else 0,
                (
                    "verified"
                    if record["resolutionStatus"] == "verified"
                    else "market_matched"
                ),
                "DefiLlama 项目登记与 CoinGecko 资产注册交叉核验",
                COINGECKO_SOURCE["source_id"],
                f"https://www.coingecko.com/en/coins/{coin_id}",
                now,
                now if record["resolutionStatus"] == "verified" else None,
                record["matchMethod"],
                now,
                now,
            ),
        )
    connection.execute(
        """
        UPDATE candidate_cases
        SET asset_id = ?, updated_at = ?
        WHERE project_id = ? AND asset_id IS NULL
        """,
        (asset_id, now, project_id),
    )
    legacy_thesis = (
        "当前只确认项目主体线索，尚未识别可投资资产、价值捕获、"
        "凸性来源和点火条件。"
    )
    case_row = connection.execute(
        """
        SELECT current_thesis
        FROM candidate_cases
        WHERE project_id = ?
        ORDER BY updated_at DESC, case_id
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    thesis_updated = bool(
        case_row and legacy_thesis in str(case_row["current_thesis"] or "")
    )
    if thesis_updated:
        connection.execute(
            """
            UPDATE candidate_cases
            SET current_thesis = replace(current_thesis, ?, ?),
                updated_at = ?
            WHERE project_id = ?
            """,
            (
                legacy_thesis,
                (
                    f"已识别可交易资产 {record['symbol']}；价值捕获、风险、"
                    "交易性、凸性来源和点火条件仍待后续自动核验。"
                ),
                now,
                project_id,
            ),
        )
    links = record["officialLinks"]
    connection.execute(
        """
        UPDATE projects
        SET official_repo = CASE
              WHEN official_repo = '' AND ? != '' THEN ?
              ELSE official_repo
            END,
            identity_status = CASE
              WHEN ? = 'verified' AND website_domain != '' THEN 'verified'
              ELSE identity_status
            END,
            updated_at = ?
        WHERE project_id = ?
        """,
        (
            links.get("repository", ""),
            links.get("repository", ""),
            record["resolutionStatus"],
            now,
            project_id,
        ),
    )
    connection.execute(
        """
        UPDATE source_discoveries
        SET project_identity_status = CASE
              WHEN ? = 'verified' THEN 'verified'
              ELSE project_identity_status
            END,
            asset_identity_status = ?,
            attribution_confidence = CASE
              WHEN ? = 'verified' THEN 'high'
              ELSE attribution_confidence
            END,
            attribution_reason = CASE
              WHEN instr(attribution_reason, 'C1.5-02 资产复核：') > 0
                THEN attribution_reason
              ELSE attribution_reason || ?
            END,
            updated_at = ?
        WHERE matched_project_id = ?
          AND asset_identity_status != 'conflict'
        """,
        (
            record["resolutionStatus"],
            (
                "verified"
                if record["resolutionStatus"] == "verified"
                else "pending"
            ),
            record["resolutionStatus"],
            (
                f" C1.5-02 资产复核：{record['symbol']} "
                f"{record['resolutionStatus']}。"
            ),
            now,
            project_id,
        ),
    )
    return {
        "status": "linked",
        "assetId": asset_id,
        "assetCreated": not bool(asset),
        "contractsUpserted": len(contract_rows),
        "thesisUpdated": thesis_updated,
    }


def persist_machine_project_asset_identities(
    connection,
    bundle,
    run_id,
    now,
    stable_id,
):
    register_sources(connection, now)
    summary = {
        "projectsQueued": bundle["projectsQueued"],
        "projectsReviewed": len(bundle["records"]),
        "verified": 0,
        "corroborated": 0,
        "pending": 0,
        "conflicts": 0,
        "assetsCreated": 0,
        "assetsLinked": 0,
        "contractsUpserted": 0,
        "changedProjects": 0,
        "registryAssets": bundle["registryAssets"],
        "protocolRecords": bundle["protocolRecords"],
        "errors": bundle["errors"],
    }
    for original_record in bundle["records"]:
        record = {**original_record}
        previous = latest_project_coin_mapping(connection, record["projectId"])
        asset_result = None
        if record["resolutionStatus"] in {"verified", "corroborated"}:
            competing = competing_project_mapping(
                connection,
                record["projectId"],
                record["coingeckoId"],
            )
            if competing:
                record.update(
                    {
                        "resolutionStatus": "pending",
                        "confidence": "low",
                        "reason": (
                            f"资产已归属项目 {competing['canonical_name']}；"
                            "当前项目可能是子产品，缺少项目—母资产关系，暂不重复建币。"
                        ),
                    }
                )
            else:
                asset_result = write_project_asset(
                    connection,
                    record,
                    now,
                    stable_id,
                )
                if asset_result["status"] == "conflict":
                    record.update(
                        {
                            "resolutionStatus": "conflict",
                            "confidence": "low",
                            "reason": asset_result["reason"],
                        }
                    )
                    asset_result = None

        asset_id = asset_result.get("assetId") if asset_result else None
        insert_review(
            connection,
            record,
            run_id,
            now,
            stable_id,
            asset_id=asset_id,
        )
        summary[
            {
                "verified": "verified",
                "corroborated": "corroborated",
                "pending": "pending",
                "conflict": "conflicts",
            }[record["resolutionStatus"]]
        ] += 1
        if asset_result:
            summary["assetsLinked"] += 1
            summary["assetsCreated"] += int(asset_result["assetCreated"])
            summary["contractsUpserted"] += asset_result["contractsUpserted"]

        changes = []
        previous_status = previous["resolution_status"] if previous else None
        previous_coin = previous["coingecko_id"] if previous else None
        if previous_status != record["resolutionStatus"]:
            changes.append(
                {
                    "field": "资产身份状态",
                    "before": previous_status,
                    "after": record["resolutionStatus"],
                }
            )
        if previous_coin != record["coingeckoId"]:
            changes.append(
                {
                    "field": "CoinGecko 资产",
                    "before": previous_coin,
                    "after": record["coingeckoId"] or "未识别",
                }
            )
        if asset_result and asset_result["assetCreated"]:
            changes.append(
                {
                    "field": "可交易资产",
                    "before": None,
                    "after": record["symbol"],
                }
            )
        if asset_result and asset_result["thesisUpdated"]:
            changes.append(
                {
                    "field": "项目说明",
                    "before": "尚未识别可投资资产",
                    "after": f"已识别 {record['symbol']}，其余投资要素待核验",
                }
            )
        if changes:
            summary["changedProjects"] += 1

        payload = {
            "summary": record["reason"],
            "resolutionStatus": record["resolutionStatus"],
            "confidence": record["confidence"],
            "coingeckoId": record["coingeckoId"],
            "symbol": record["symbol"],
            "matchMethod": record["matchMethod"],
            "contractsRegistered": (
                asset_result["contractsUpserted"] if asset_result else 0
            ),
            "changes": changes,
        }
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url,
              excerpt, project_hint, asset_hint, chain_hint, event_type,
              raw_payload_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                    'project_asset_identity_refresh', ?, 'normalized')
            """,
            (
                stable_id(
                    "project-asset-event",
                    run_id,
                    record["projectId"],
                ),
                SOURCE_DEFINITION["source_id"],
                run_id,
                f"{run_id}:{record['projectId']}:asset-identity",
                now,
                now,
                hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(
                        "utf-8"
                    )
                ).hexdigest(),
                (
                    f"https://www.coingecko.com/en/coins/{record['coingeckoId']}"
                    if record["coingeckoId"]
                    else record["sourceUrl"]
                ),
                record["reason"],
                record["projectName"],
                record["symbol"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        evidence_id = stable_id(
            "project-asset-evidence",
            record["projectId"],
            record["coingeckoId"] or record["resolutionStatus"],
            RULE_VERSION,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO evidence_items (
              evidence_id, project_id, asset_id, raw_event_id,
              evidence_type, stance, fact_boundary, confidence,
              observed_at, expires_at, source_id, source_url,
              summary, created_at
            )
            VALUES (?, ?, ?, ?, 'project_asset_identity', 'neutral',
                    ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                record["projectId"],
                asset_id,
                stable_id(
                    "project-asset-event",
                    run_id,
                    record["projectId"],
                ),
                (
                    "high_confidence_inference"
                    if record["resolutionStatus"] in {"verified", "corroborated"}
                    else "unverified_signal"
                ),
                (
                    "高"
                    if record["resolutionStatus"] == "verified"
                    else "中"
                    if record["resolutionStatus"] == "corroborated"
                    else "待验证"
                ),
                now,
                SOURCE_DEFINITION["source_id"],
                (
                    f"https://www.coingecko.com/en/coins/{record['coingeckoId']}"
                    if record["coingeckoId"]
                    else record["sourceUrl"]
                ),
                record["reason"],
                now,
            ),
        )
    return summary
