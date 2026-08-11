#!/usr/bin/env python3
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from contract_tradeability import (
    NETWORKS,
    address_equal,
    request_json,
    user_environment,
)
from discover_network_tokens import DEFAULT_CONFIG_PATH, load_config


RULE_VERSION = "discovery-identity-promotion-v1"
CANDIDATE_RULE_VERSION = "convexity-auto-discovery-v1.0.0"
COINGECKO_PLATFORMS = {
    "ethereum-mainnet": "ethereum",
    "solana-mainnet": "solana",
    "base-mainnet": "base",
    "arbitrum-mainnet": "arbitrum-one",
    "bnb-mainnet": "binance-smart-chain",
    "robinhood-mainnet": "robinhood",
}
SOURCE_DEFINITION = {
    "source_id": "identity-coingecko-registry",
    "name": "CoinGecko 资产身份注册",
    "source_type": "independent_asset_registry",
    "url": "https://api.coingecko.com/api/v3",
    "access_method": "Demo API",
    "conflict_risk": "低",
}
VALUE_CAPTURE_KEYWORDS = (
    "governance",
    "staking",
    "protocol fee",
    "revenue",
    "buyback",
    "burn",
    "utility token",
    "fee sharing",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_text(value):
    return " ".join(str(value or "").casefold().split())


def website_domain(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.casefold().removeprefix("www.")


def first_http_url(values):
    for value in values or []:
        url = str(value or "").strip()
        if url.startswith(("https://", "http://")):
            return url
    return ""


def excluded_asset_reason(record, settings):
    symbol = str(record.get("symbol") or "").upper().lstrip("$")
    name = normalized_text(record.get("tokenName"))
    if symbol in set(settings["excludedAssetSymbols"]):
        return "稳定币、包装资产或锚定资产本身不具备本轮非线性上行标的属性。"
    if any(fragment in name for fragment in settings["excludedNameFragments"]):
        return "代币化证券、包装资产或流动性凭证不作为独立凸性项目升格。"
    return ""


def coin_name_match(record, coin):
    name_equal = normalized_text(record.get("tokenName")) == normalized_text(coin.get("name"))
    symbol_equal = normalized_text(record.get("symbol")).lstrip("$") == normalized_text(
        coin.get("symbol")
    ).lstrip("$")
    if name_equal and symbol_equal:
        return "match"
    if name_equal or symbol_equal:
        return "partial"
    return "mismatch"


def coin_contract_status(record, detail):
    platform = COINGECKO_PLATFORMS.get(record["networkId"], "")
    registered = str((detail.get("platforms") or {}).get(platform) or "").strip()
    if not registered:
        return "not_found"
    chain_type = NETWORKS[record["networkId"]]["chainType"]
    return (
        "registry_matched"
        if address_equal(registered, record["contractAddress"], chain_type)
        else "conflict"
    )


def fetch_website(url, contract_address, chain_type, timeout):
    if not url:
        return {"status": "missing", "contractConfirmed": False, "error": ""}
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Penguin-Convexity/1.0",
            "Range": "bytes=0-524287",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(524288).decode("utf-8", errors="ignore")
        contract_confirmed = (
            contract_address.lower() in body.lower()
            if chain_type == "EVM"
            else contract_address in body
        )
        return {
            "status": "accessible",
            "contractConfirmed": contract_confirmed,
            "error": "",
        }
    except urllib.error.HTTPError as error:
        return {
            "status": "restricted" if error.code in (401, 403, 429) else "failed",
            "contractConfirmed": False,
            "error": f"HTTP {error.code}",
        }
    except Exception as error:
        return {
            "status": "failed",
            "contractConfirmed": False,
            "error": f"{type(error).__name__}: {error}",
        }


def base_review(record):
    return {
        "discoveryId": record.get("discoveryId", ""),
        "networkId": record["networkId"],
        "contractAddress": record["contractAddress"],
        "symbol": record.get("symbol", ""),
        "tokenName": record.get("tokenName", ""),
        "resolutionStatus": "pending",
        "confidence": "low",
        "canonicalName": record.get("tokenName", ""),
        "coingeckoId": "",
        "websiteUrl": "",
        "websiteDomain": "",
        "websiteStatus": "missing",
        "officialContractStatus": "not_found",
        "nameMatchStatus": "unknown",
        "socialUrls": [],
        "repoUrls": [],
        "valueCaptureStatus": "unknown",
        "promotionEligible": False,
        "reason": "",
        "evidence": [],
    }


def evaluate_identity_candidate(record, search_coins, coin_details, website_result, settings):
    review = base_review(record)
    exclusion = excluded_asset_reason(record, settings)
    if exclusion:
        review.update(
            {
                "resolutionStatus": "rejected",
                "valueCaptureStatus": "not_applicable",
                "reason": exclusion,
                "evidence": [
                    {
                        "type": "scope_rule",
                        "summary": exclusion,
                        "factBoundary": "confirmed_fact",
                    }
                ],
            }
        )
        return review

    ranked = sorted(
        search_coins,
        key=lambda coin: {"match": 2, "partial": 1, "mismatch": 0}[
            coin_name_match(record, coin)
        ],
        reverse=True,
    )
    conflict = None
    selected = None
    selected_match = "unknown"
    for coin in ranked[:4]:
        match_status = coin_name_match(record, coin)
        if match_status == "mismatch":
            continue
        detail = coin_details.get(coin["id"])
        if not detail:
            continue
        contract_status = coin_contract_status(record, detail)
        if contract_status == "registry_matched":
            selected = detail
            selected_match = match_status
            break
        if contract_status == "conflict" and conflict is None:
            conflict = (detail, match_status)

    if not selected:
        if conflict:
            detail, match_status = conflict
            review.update(
                {
                    "resolutionStatus": "conflict",
                    "canonicalName": detail.get("name") or review["canonicalName"],
                    "coingeckoId": detail.get("id", ""),
                    "officialContractStatus": "conflict",
                    "nameMatchStatus": match_status,
                    "reason": "名称或符号相似，但 CoinGecko 在该网络登记的是另一份合约，按同名仿盘风险阻断。",
                }
            )
        else:
            review["reason"] = "未找到名称、符号和当前网络合约同时精确匹配的独立资产登记。"
        return review

    links = selected.get("links") or {}
    homepage = first_http_url(links.get("homepage"))
    social_urls = []
    if links.get("twitter_screen_name"):
        social_urls.append(f"https://x.com/{links['twitter_screen_name']}")
    social_urls.extend(
        url
        for url in (
            links.get("telegram_channel_identifier")
            and f"https://t.me/{links['telegram_channel_identifier']}",
            links.get("subreddit_url"),
        )
        if url
    )
    repo_urls = [
        url
        for group in (links.get("repos_url") or {}).values()
        for url in group
        if url
    ]
    description = " ".join((selected.get("description") or {}).values()).casefold()
    value_capture_status = (
        "claimed" if any(keyword in description for keyword in VALUE_CAPTURE_KEYWORDS) else "unknown"
    )
    official_contract_status = (
        "confirmed"
        if website_result.get("contractConfirmed")
        else "registry_matched"
    )
    resolution_status = (
        "verified" if official_contract_status == "confirmed" else "corroborated"
    )
    evidence = [
        {
            "type": "independent_registry",
            "summary": (
                f"CoinGecko 的 {COINGECKO_PLATFORMS[record['networkId']]} 合约登记"
                "与当前发现地址精确一致。"
            ),
            "url": f"https://www.coingecko.com/en/coins/{selected['id']}",
            "factBoundary": "high_confidence_inference",
        }
    ]
    if homepage:
        evidence.append(
            {
                "type": "official_website",
                "summary": (
                    "官网可访问且正文包含当前合约。"
                    if website_result.get("contractConfirmed")
                    else f"官网访问状态：{website_result['status']}；正文未发现当前合约。"
                ),
                "url": homepage,
                "factBoundary": (
                    "confirmed_fact"
                    if website_result.get("contractConfirmed")
                    else "project_claim"
                ),
            }
        )
    review.update(
        {
            "resolutionStatus": resolution_status,
            "confidence": "high" if resolution_status == "verified" else "medium",
            "canonicalName": selected.get("name") or review["canonicalName"],
            "coingeckoId": selected["id"],
            "websiteUrl": homepage,
            "websiteDomain": website_domain(homepage),
            "websiteStatus": website_result.get("status", "missing"),
            "officialContractStatus": official_contract_status,
            "nameMatchStatus": selected_match,
            "socialUrls": social_urls,
            "repoUrls": repo_urls,
            "valueCaptureStatus": value_capture_status,
            "promotionEligible": bool(homepage and website_result.get("status") == "accessible"),
            "reason": (
                "第三方资产登记与合约一致，官网可访问；仅允许升格到影子研究库。"
                if homepage and website_result.get("status") == "accessible"
                else "合约映射已得到第三方交叉确认，但官网缺失或不可访问，暂不自动升格。"
            ),
            "evidence": evidence,
        }
    )
    return review


def coingecko_headers():
    api_key = user_environment("COINGECKO_DEMO_API_KEY")
    return {"x-cg-demo-api-key": api_key} if api_key else {}


def live_identity_review(record, settings, timeout):
    query = urllib.parse.quote(record.get("tokenName") or record.get("symbol") or "")
    search_url = settings["coingeckoSearchUrl"].format(query=query)
    search_payload = request_json(search_url, headers=coingecko_headers(), timeout=timeout)
    search_coins = search_payload.get("coins") or []
    details = {}
    for coin in search_coins[:8]:
        if coin_name_match(record, coin) == "mismatch":
            continue
        coin_url = settings["coingeckoCoinUrl"].format(
            coin_id=urllib.parse.quote(coin["id"])
        )
        details[coin["id"]] = request_json(
            coin_url,
            headers=coingecko_headers(),
            timeout=timeout,
        )
        if coin_contract_status(record, details[coin["id"]]) == "registry_matched":
            break
    selected = next(
        (
            detail
            for detail in details.values()
            if coin_contract_status(record, detail) == "registry_matched"
        ),
        None,
    )
    homepage = first_http_url((selected.get("links") or {}).get("homepage")) if selected else ""
    website_result = fetch_website(
        homepage,
        record["contractAddress"],
        NETWORKS[record["networkId"]]["chainType"],
        timeout,
    )
    return evaluate_identity_candidate(
        record,
        search_coins,
        details,
        website_result,
        settings,
    )


def collect_identity_reviews(discovery_bundle, config_path=DEFAULT_CONFIG_PATH, timeout=15):
    settings = load_config(config_path)["identityReview"]
    if not settings["enabled"]:
        return {"records": [], "errors": [], "sourceStats": {}}
    eligible = [
        item
        for item in discovery_bundle["records"]
        if item["discoveryScore"] >= settings["minimumDiscoveryScore"]
        and (
            not settings["requirePreflightPass"]
            or item["preflightStatus"] == "pass"
        )
    ]
    records = []
    errors = []
    for item in eligible:
        exclusion = excluded_asset_reason(item, settings)
        if exclusion:
            records.append(evaluate_identity_candidate(item, [], {}, {}, settings))
            continue
        try:
            records.append(live_identity_review(item, settings, timeout))
        except Exception as error:
            review = base_review(item)
            review["reason"] = "身份复核来源暂时失败，本条保留待重试，不降级为错误项目。"
            records.append(review)
            errors.append(
                {
                    "provider": "coingecko_identity",
                    "discoveryId": item.get("discoveryId", ""),
                    "sourceUrl": SOURCE_DEFINITION["url"],
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    return {
        "records": records,
        "errors": errors,
        "sourceStats": {
            "coingecko_identity": {
                "collected": len(eligible),
                "accepted": sum(
                    item["resolutionStatus"] in ("verified", "corroborated")
                    for item in records
                ),
                "filtered": sum(
                    item["resolutionStatus"] in ("rejected", "conflict")
                    for item in records
                ),
                "failed": len(errors),
            }
        },
    }


def register_source(connection, now):
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope, confidence,
          conflict_risk, status, schedule_text, last_checked_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'convexity_identity', '中', ?, 'active',
                '候选库一键刷新', ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          status = 'active',
          last_checked_at = excluded.last_checked_at,
          updated_at = excluded.updated_at
        """,
        (
            SOURCE_DEFINITION["source_id"],
            SOURCE_DEFINITION["name"],
            SOURCE_DEFINITION["source_type"],
            SOURCE_DEFINITION["url"],
            SOURCE_DEFINITION["access_method"],
            SOURCE_DEFINITION["conflict_risk"],
            now,
            now,
            now,
        ),
    )


def existing_contract(connection, network_id, contract_address):
    chain_type = NETWORKS[network_id]["chainType"]
    for row in connection.execute(
        """
        SELECT ac.asset_id, a.project_id, ac.contract_address
        FROM asset_contracts ac
        JOIN assets a ON a.asset_id = ac.asset_id
        WHERE ac.network_id = ?
        """,
        (network_id,),
    ):
        if address_equal(row["contract_address"], contract_address, chain_type):
            return row
    return None


def project_for_review(connection, review):
    if review["websiteDomain"]:
        row = connection.execute(
            "SELECT * FROM projects WHERE lower(website_domain) = ? LIMIT 1",
            (review["websiteDomain"].casefold(),),
        ).fetchone()
        if row:
            return row
    return connection.execute(
        "SELECT * FROM projects WHERE lower(canonical_name) = ? LIMIT 1",
        (review["canonicalName"].casefold(),),
    ).fetchone()


def conflicting_project_contract(connection, project_id, review):
    chain_type = NETWORKS[review["networkId"]]["chainType"]
    for row in connection.execute(
        """
        SELECT ac.contract_address
        FROM assets a
        JOIN asset_contracts ac ON ac.asset_id = a.asset_id
        WHERE a.project_id = ? AND ac.network_id = ?
        """,
        (project_id, review["networkId"]),
    ):
        if not address_equal(
            row["contract_address"],
            review["contractAddress"],
            chain_type,
        ):
            return True
    return False


def upsert_shadow_project(connection, review, discovery, now, stable_id):
    project = project_for_review(connection, review)
    if project and conflicting_project_contract(connection, project["project_id"], review):
        return {
            "status": "conflict",
            "reason": "已存在同名项目，但同一网络登记了不同合约，禁止自动归属。",
        }
    project_id = (
        project["project_id"]
        if project
        else stable_id("auto-project", review["coingeckoId"] or review["websiteDomain"])
    )
    verified = review["officialContractStatus"] == "confirmed"
    project_status = "verified" if verified else (project["identity_status"] if project else "pending")
    repository = review["repoUrls"][0] if review["repoUrls"] else ""
    connection.execute(
        """
        INSERT INTO projects (
          project_id, canonical_name, website_domain, official_repo, team_summary,
          identity_status, first_seen_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
          website_domain = CASE
            WHEN projects.website_domain = '' THEN excluded.website_domain
            ELSE projects.website_domain
          END,
          official_repo = CASE
            WHEN projects.official_repo = '' THEN excluded.official_repo
            ELSE projects.official_repo
          END,
          identity_status = CASE
            WHEN excluded.identity_status = 'verified' THEN 'verified'
            ELSE projects.identity_status
          END,
          updated_at = excluded.updated_at
        """,
        (
            project_id,
            review["canonicalName"],
            review["websiteDomain"],
            repository,
            project_status,
            now,
            now,
            now,
        ),
    )
    symbol = str(review["symbol"] or "").upper().lstrip("$")
    asset = connection.execute(
        """
        SELECT *
        FROM assets
        WHERE project_id = ? AND upper(symbol) = ?
        LIMIT 1
        """,
        (project_id, symbol),
    ).fetchone()
    asset_id = asset["asset_id"] if asset else stable_id(
        "auto-asset",
        project_id,
        symbol,
    )
    asset_status = "verified" if verified else (asset["identity_status"] if asset else "pending")
    connection.execute(
        """
        INSERT INTO assets (
          asset_id, project_id, symbol, chain, contract_address, asset_type,
          capture_grade, identity_status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'token', 'unknown', ?, ?, ?)
        ON CONFLICT(asset_id) DO UPDATE SET
          identity_status = CASE
            WHEN excluded.identity_status = 'verified' THEN 'verified'
            ELSE assets.identity_status
          END,
          updated_at = excluded.updated_at
        """,
        (
            asset_id,
            project_id,
            symbol,
            NETWORKS[review["networkId"]]["name"],
            review["contractAddress"],
            asset_status,
            now,
            now,
        ),
    )
    has_primary = connection.execute(
        "SELECT 1 FROM asset_contracts WHERE asset_id = ? AND is_primary = 1",
        (asset_id,),
    ).fetchone()
    contract_id = stable_id(
        "auto-contract",
        asset_id,
        review["networkId"],
        review["contractAddress"],
    )
    connection.execute(
        """
        INSERT INTO asset_contracts (
          asset_contract_id, asset_id, network_id, contract_address,
          contract_standard, is_primary, identity_status, identity_source,
          source_id, source_url, observed_at, verified_at, verification_method,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(asset_id, network_id, contract_address) DO UPDATE SET
          identity_status = excluded.identity_status,
          identity_source = excluded.identity_source,
          source_url = excluded.source_url,
          verified_at = excluded.verified_at,
          verification_method = excluded.verification_method,
          updated_at = excluded.updated_at
        """,
        (
            contract_id,
            asset_id,
            review["networkId"],
            review["contractAddress"],
            discovery["contract_standard"],
            0 if has_primary else 1,
            "verified" if verified else "market_matched",
            "官网合约确认" if verified else "CoinGecko 合约精确映射",
            SOURCE_DEFINITION["source_id"],
            review["websiteUrl"]
            if verified
            else f"https://www.coingecko.com/en/coins/{review['coingeckoId']}",
            now,
            now if verified else None,
            (
                "官网正文包含同一合约"
                if verified
                else "第三方资产注册的网络合约与链上发现精确一致"
            ),
            now,
            now,
        ),
    )
    case = connection.execute(
        """
        SELECT case_id
        FROM candidate_cases
        WHERE project_id = ? AND asset_id = ?
        LIMIT 1
        """,
        (project_id, asset_id),
    ).fetchone()
    case_id = case["case_id"] if case else stable_id(
        "auto-case",
        project_id,
        asset_id,
    )
    if not case:
        connection.execute(
            """
            INSERT INTO candidate_cases (
              case_id, project_id, asset_id, title, maturity_level,
              workflow_state, risk_level, remaining_convexity,
              ignition_proximity, tradeability_status, liquidity_grade,
              convexity_source, action_stage, value_capture_grade,
              current_thesis, invalidation, next_review_at, rule_version,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'L0', 'shadow_signal', 'unknown', 'unknown',
                    'unknown', 'limited', 'extreme', '', '只观察', 'unknown',
                    ?, ?, NULL, ?, ?, ?)
            """,
            (
                case_id,
                project_id,
                asset_id,
                f"{review['canonicalName']} 自动发现身份已归属",
                "身份与合约已完成自动归属，但价值捕获、凸性来源和点火条件尚未独立核验。",
                "若官方渠道否认该合约、出现同名合约冲突或官网身份失效，则撤销归属。",
                CANDIDATE_RULE_VERSION,
                now,
                now,
            ),
        )
    return {
        "status": "promoted",
        "projectId": project_id,
        "assetId": asset_id,
        "caseId": case_id,
        "existingCase": bool(case),
    }


def persist_identity_reviews(connection, bundle, run_id, stable_id):
    now = utc_now()
    register_source(connection, now)
    summary = {
        "reviewed": len(bundle["records"]),
        "corroborated": 0,
        "officialVerified": 0,
        "promoted": 0,
        "pending": 0,
        "rejected": 0,
        "conflicts": 0,
        "existing": 0,
        "errors": bundle["errors"],
    }
    for review in bundle["records"]:
        discovery = connection.execute(
            "SELECT * FROM network_discoveries WHERE discovery_id = ?",
            (review["discoveryId"],),
        ).fetchone()
        if not discovery:
            if NETWORKS[review["networkId"]]["chainType"] == "EVM":
                discovery = connection.execute(
                    """
                    SELECT *
                    FROM network_discoveries
                    WHERE network_id = ? AND lower(contract_address) = lower(?)
                    """,
                    (review["networkId"], review["contractAddress"]),
                ).fetchone()
            else:
                discovery = connection.execute(
                    """
                    SELECT *
                    FROM network_discoveries
                    WHERE network_id = ? AND contract_address = ?
                    """,
                    (review["networkId"], review["contractAddress"]),
                ).fetchone()
        if not discovery:
            continue
        review["discoveryId"] = discovery["discovery_id"]
        promotion_status = "pending"
        matched_project_id = None
        promoted_project_id = None
        promoted_asset_id = None
        promoted_case_id = None
        reason = review["reason"]
        known = existing_contract(
            connection,
            review["networkId"],
            review["contractAddress"],
        )
        if known:
            matched_project_id = known["project_id"]
            auto_case = connection.execute(
                """
                SELECT case_id
                FROM candidate_cases
                WHERE asset_id = ? AND case_id LIKE 'auto-case-%'
                LIMIT 1
                """,
                (known["asset_id"],),
            ).fetchone()
            if auto_case:
                promotion_status = "shadow_promoted"
                promoted_project_id = known["project_id"]
                promoted_asset_id = known["asset_id"]
                promoted_case_id = auto_case["case_id"]
                reason = "项目已在影子研究库，本轮只更新身份与市场证据，保持只观察。"
                connection.execute(
                    "UPDATE network_discoveries SET queue_status = 'promoted', status_reason = ?, updated_at = ? WHERE discovery_id = ?",
                    (reason, now, discovery["discovery_id"]),
                )
            else:
                promotion_status = "existing_project"
                reason = "合约已属于现有候选资产，本轮身份复核只更新证据。"
                connection.execute(
                    "UPDATE network_discoveries SET queue_status = 'existing_asset', status_reason = ?, updated_at = ? WHERE discovery_id = ?",
                    (reason, now, discovery["discovery_id"]),
                )
            summary["existing"] += 1
        elif review["resolutionStatus"] == "rejected":
            promotion_status = "rejected"
            connection.execute(
                "UPDATE network_discoveries SET queue_status = 'rejected', status_reason = ?, updated_at = ? WHERE discovery_id = ?",
                (reason, now, discovery["discovery_id"]),
            )
            summary["rejected"] += 1
        elif review["resolutionStatus"] == "conflict":
            promotion_status = "rejected"
            connection.execute(
                "UPDATE network_discoveries SET queue_status = 'rejected', status_reason = ?, updated_at = ? WHERE discovery_id = ?",
                (reason, now, discovery["discovery_id"]),
            )
            summary["conflicts"] += 1
        elif review["resolutionStatus"] in ("verified", "corroborated"):
            summary["corroborated"] += 1
            summary["officialVerified"] += review["resolutionStatus"] == "verified"
            if review["promotionEligible"]:
                promotion = upsert_shadow_project(
                    connection,
                    review,
                    discovery,
                    now,
                    stable_id,
                )
                if promotion["status"] == "conflict":
                    review["resolutionStatus"] = "conflict"
                    promotion_status = "rejected"
                    reason = promotion["reason"]
                    summary["conflicts"] += 1
                    connection.execute(
                        "UPDATE network_discoveries SET queue_status = 'rejected', status_reason = ?, updated_at = ? WHERE discovery_id = ?",
                        (reason, now, discovery["discovery_id"]),
                    )
                else:
                    promotion_status = "shadow_promoted"
                    promoted_project_id = promotion["projectId"]
                    promoted_asset_id = promotion["assetId"]
                    promoted_case_id = promotion["caseId"]
                    reason = (
                        "身份归属已建立并升格到影子研究库；价值捕获与凸性尚未核验，动作保持只观察。"
                    )
                    summary["promoted"] += 1
                    connection.execute(
                        "UPDATE network_discoveries SET queue_status = 'promoted', status_reason = ?, updated_at = ? WHERE discovery_id = ?",
                        (reason, now, discovery["discovery_id"]),
                    )
                    for index, evidence in enumerate(review["evidence"]):
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO evidence_items (
                              evidence_id, project_id, asset_id, raw_event_id,
                              evidence_type, stance, fact_boundary, confidence,
                              observed_at, expires_at, source_id, source_url,
                              summary, created_at
                            )
                            VALUES (?, ?, ?, NULL, ?, 'support', ?, ?, ?, NULL,
                                    ?, ?, ?, ?)
                            """,
                            (
                                stable_id(
                                    "identity-evidence",
                                    discovery["discovery_id"],
                                    evidence["type"],
                                    index,
                                ),
                                promoted_project_id,
                                promoted_asset_id,
                                evidence["type"],
                                evidence.get("factBoundary", "high_confidence_inference"),
                                "高"
                                if evidence.get("factBoundary") == "confirmed_fact"
                                else "中",
                                now,
                                SOURCE_DEFINITION["source_id"],
                                evidence.get("url", ""),
                                evidence["summary"],
                                now,
                            ),
                        )
            else:
                connection.execute(
                    "UPDATE network_discoveries SET queue_status = 'identity_pending', status_reason = ?, updated_at = ? WHERE discovery_id = ?",
                    (reason, now, discovery["discovery_id"]),
                )
                summary["pending"] += 1
        else:
            connection.execute(
                "UPDATE network_discoveries SET queue_status = 'identity_pending', status_reason = ?, updated_at = ? WHERE discovery_id = ?",
                (reason, now, discovery["discovery_id"]),
            )
            summary["pending"] += 1

        connection.execute(
            """
            INSERT INTO discovery_identity_reviews (
              identity_review_id, discovery_id, run_id, reviewed_at, provider,
              resolution_status, confidence, canonical_name, coingecko_id,
              website_url, website_domain, website_status,
              official_contract_status, name_match_status, social_urls_json,
              repo_urls_json, value_capture_status, promotion_status,
              matched_project_id, promoted_project_id, promoted_asset_id,
              promoted_case_id, reason, evidence_json, rule_version
            )
            VALUES (?, ?, ?, ?, 'coingecko_registry', ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("identity-review", run_id, discovery["discovery_id"]),
                discovery["discovery_id"],
                run_id,
                now,
                review["resolutionStatus"],
                review["confidence"],
                review["canonicalName"],
                review["coingeckoId"],
                review["websiteUrl"],
                review["websiteDomain"],
                review["websiteStatus"],
                review["officialContractStatus"],
                review["nameMatchStatus"],
                json.dumps(review["socialUrls"], ensure_ascii=False),
                json.dumps(review["repoUrls"], ensure_ascii=False),
                review["valueCaptureStatus"],
                promotion_status,
                matched_project_id,
                promoted_project_id,
                promoted_asset_id,
                promoted_case_id,
                reason,
                json.dumps(review["evidence"], ensure_ascii=False),
                RULE_VERSION,
            ),
        )
    return summary
