#!/usr/bin/env python3
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from contract_tradeability import (
    DEX_TO_NETWORK,
    NETWORKS,
    best_pair,
    explorer_address_url,
    request_json,
    verify_candidate,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "fixtures" / "network-discovery-config-v1.json"
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "network-discovery-snapshot.js"
RULE_VERSION = "common-network-discovery-v1"

SOURCE_DEFINITIONS = {
    "dexscreener_profiles": {
        "source_id": "discovery-dexscreener-profiles",
        "name": "DexScreener 最新代币资料",
        "source_type": "promotional_discovery_api",
        "url": "https://api.dexscreener.com/token-profiles/latest/v1",
        "access_method": "Public API",
        "conflict_risk": "高",
    },
    "dexscreener_boosts": {
        "source_id": "discovery-dexscreener-boosts",
        "name": "DexScreener 最新推广代币",
        "source_type": "paid_signal_discovery_api",
        "url": "https://api.dexscreener.com/token-boosts/latest/v1",
        "access_method": "Public API",
        "conflict_risk": "高",
    },
    "robinhood_registry": {
        "source_id": "discovery-robinhood-blockscout",
        "name": "Robinhood Chain 代币注册表",
        "source_type": "chain_registry_api",
        "url": "https://robinhoodchain.blockscout.com/api/v2/tokens?type=ERC-20",
        "access_method": "Public API",
        "conflict_risk": "中",
    },
}

PROVIDER_CONFIG_KEYS = {
    "dexscreener_profiles": "dexscreenerProfiles",
    "dexscreener_boosts": "dexscreenerBoosts",
    "robinhood_registry": "robinhoodBlockscout",
}

SOURCE_ID_TO_PROVIDER = {
    definition["source_id"]: provider
    for provider, definition in SOURCE_DEFINITIONS.items()
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_config(path=DEFAULT_CONFIG_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_address(network_id, address):
    if NETWORKS[network_id]["chainType"] == "EVM":
        return address.lower()
    return address


def merge_candidate(bucket, candidate):
    key = (
        candidate["networkId"],
        normalize_address(candidate["networkId"], candidate["contractAddress"]),
    )
    item = bucket.setdefault(
        key,
        {
            "networkId": candidate["networkId"],
            "contractAddress": candidate["contractAddress"],
            "tokenName": "",
            "symbol": "",
            "holdersCount": None,
            "sourceIds": [],
            "sourceUrls": [],
            "discoveryKinds": [],
            "sourceBoundaries": [],
            "rawPriceUsd": None,
            "rawVolume24hUsd": None,
            "rawMarketCapUsd": None,
        },
    )
    for field in (
        "tokenName",
        "symbol",
        "holdersCount",
        "rawPriceUsd",
        "rawVolume24hUsd",
        "rawMarketCapUsd",
    ):
        if candidate.get(field) not in (None, ""):
            item[field] = candidate[field]
    for field in ("sourceIds", "sourceUrls", "discoveryKinds", "sourceBoundaries"):
        for value in candidate.get(field, []):
            if value and value not in item[field]:
                item[field].append(value)


def normalize_scan_scope(config, network_ids=None, source_keys=None):
    configured_networks = list(config["commonNetworks"])
    enabled_sources = [
        provider
        for provider, config_key in PROVIDER_CONFIG_KEYS.items()
        if config["sources"][config_key]["enabled"]
    ]
    selected_networks = list(network_ids) if network_ids else configured_networks
    selected_sources = list(source_keys) if source_keys else enabled_sources
    unknown_networks = sorted(set(selected_networks) - set(configured_networks))
    unknown_sources = sorted(set(selected_sources) - set(enabled_sources))
    if unknown_networks:
        raise ValueError(f"未知扫描网络：{', '.join(unknown_networks)}")
    if unknown_sources:
        raise ValueError(f"未知或未启用信源：{', '.join(unknown_sources)}")
    return selected_networks, selected_sources


def source_candidates(config, timeout=20, network_ids=None, source_keys=None):
    selected_networks, selected_sources = normalize_scan_scope(
        config,
        network_ids=network_ids,
        source_keys=source_keys,
    )
    common = set(selected_networks)
    selected_sources = set(selected_sources)
    bucket = {}
    source_stats = {}
    errors = []

    source_jobs = []
    if "dexscreener_profiles" in selected_sources:
        source_jobs.append(
            (
                "dexscreener_profiles",
                config["sources"]["dexscreenerProfiles"],
            )
        )
    if "dexscreener_boosts" in selected_sources:
        source_jobs.append(
            (
                "dexscreener_boosts",
                config["sources"]["dexscreenerBoosts"],
            )
        )
    for provider, settings in source_jobs:
        try:
            payload = request_json(settings["url"], timeout=timeout)
            accepted = 0
            for row in payload if isinstance(payload, list) else []:
                network_id = DEX_TO_NETWORK.get(str(row.get("chainId") or ""))
                address = str(row.get("tokenAddress") or "").strip()
                if network_id not in common or not address:
                    continue
                accepted += 1
                merge_candidate(
                    bucket,
                    {
                        "networkId": network_id,
                        "contractAddress": address,
                        "sourceIds": [SOURCE_DEFINITIONS[provider]["source_id"]],
                        "sourceUrls": [row.get("url") or settings["url"]],
                        "discoveryKinds": [
                            "最新资料" if provider == "dexscreener_profiles" else "推广曝光"
                        ],
                        "sourceBoundaries": [settings["boundary"]],
                    },
                )
            source_stats[provider] = {
                "collected": len(payload) if isinstance(payload, list) else 0,
                "accepted": accepted,
                "failed": 0,
                "skipped": 0,
            }
        except Exception as error:
            source_stats[provider] = {
                "collected": 0,
                "accepted": 0,
                "failed": 1,
                "skipped": 0,
            }
            errors.append(
                {
                    "provider": provider,
                    "error": f"{type(error).__name__}: {error}",
                    "sourceUrl": settings["url"],
                }
            )

    settings = config["sources"]["robinhoodBlockscout"]
    if "robinhood_registry" in selected_sources and "robinhood-mainnet" not in common:
        source_stats["robinhood_registry"] = {
            "collected": 0,
            "accepted": 0,
            "failed": 0,
            "skipped": 1,
            "explanation": "Robinhood Blockscout 只扫描 Robinhood Chain，本次所选网络不包含该链。",
        }
    elif "robinhood_registry" in selected_sources:
        provider = "robinhood_registry"
        try:
            payload = request_json(settings["url"], timeout=timeout)
            rows = payload.get("items") or []
            for row in rows:
                address = str(row.get("address_hash") or "").strip()
                if not address:
                    continue
                merge_candidate(
                    bucket,
                    {
                        "networkId": "robinhood-mainnet",
                        "contractAddress": address,
                        "tokenName": str(row.get("name") or ""),
                        "symbol": str(row.get("symbol") or ""),
                        "holdersCount": int(row["holders_count"])
                        if row.get("holders_count") not in (None, "")
                        else None,
                        "rawPriceUsd": float(row["exchange_rate"])
                        if row.get("exchange_rate") not in (None, "")
                        else None,
                        "rawVolume24hUsd": float(row["volume_24h"])
                        if row.get("volume_24h") not in (None, "")
                        else None,
                        "rawMarketCapUsd": float(row["circulating_market_cap"])
                        if row.get("circulating_market_cap") not in (None, "")
                        else None,
                        "sourceIds": [SOURCE_DEFINITIONS[provider]["source_id"]],
                        "sourceUrls": [
                            explorer_address_url(
                                NETWORKS["robinhood-mainnet"],
                                address,
                            )
                        ],
                        "discoveryKinds": ["主网代币注册表"],
                        "sourceBoundaries": [settings["boundary"]],
                    },
                )
            source_stats[provider] = {
                "collected": len(rows),
                "accepted": len(rows),
                "failed": 0,
                "skipped": 0,
            }
        except Exception as error:
            source_stats[provider] = {
                "collected": 0,
                "accepted": 0,
                "failed": 1,
                "skipped": 0,
            }
            errors.append(
                {
                    "provider": provider,
                    "error": f"{type(error).__name__}: {error}",
                    "sourceUrl": settings["url"],
                }
            )

    candidates = list(bucket.values())
    candidates.sort(
        key=lambda item: (
            "discovery-robinhood-blockscout" in item["sourceIds"],
            len(item["sourceIds"]),
            math.log10(max(float(item.get("rawVolume24hUsd") or 0), 1)),
        ),
        reverse=True,
    )
    return candidates, source_stats, errors


def pair_check(candidate, timeout):
    network = NETWORKS[candidate["networkId"]]
    pair = best_pair(network, candidate["contractAddress"], timeout)
    if not pair:
        return {**candidate, "pair": None, "pairError": "未找到以该代币为基础资产的交易池"}
    base = pair.get("baseToken") or {}
    if not candidate["symbol"]:
        candidate["symbol"] = str(base.get("symbol") or "")
    if not candidate["tokenName"]:
        candidate["tokenName"] = str(base.get("name") or "")
    return {**candidate, "pair": pair, "pairError": ""}


def market_priority(candidate):
    pair = candidate.get("pair") or {}
    liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
    volume = float((pair.get("volume") or {}).get("h24") or 0)
    txns = (pair.get("txns") or {}).get("h24") or {}
    return (
        int(bool(pair)) * 1000
        + min(math.log10(max(liquidity, 1)), 8) * 100
        + min(math.log10(max(volume, 1)), 9) * 80
        + min(int(txns.get("sells") or 0), 500)
    )


def discovery_score(item, config):
    preflight = config["preflight"]
    score = 0
    source_ids = set(item["sourceIds"])
    chain_registry = "discovery-robinhood-blockscout" in source_ids
    market_discovery = bool(
        source_ids
        & {
            "discovery-dexscreener-profiles",
            "discovery-dexscreener-boosts",
        }
    )
    if chain_registry:
        score += 10
    if chain_registry and market_discovery:
        score += 10
    if (item.get("liquidityUsd") or 0) >= preflight["minimumLiquidityUsd"]:
        score += 20
    elif (item.get("liquidityUsd") or 0) >= 5000:
        score += 10
    if (item.get("volume24hUsd") or 0) >= preflight["minimumVolume24hUsd"]:
        score += 20
    elif (item.get("volume24hUsd") or 0) >= 5000:
        score += 10
    if (item.get("recentSells24h") or 0) > 0:
        score += 15
    if item.get("contractExistsStatus") == "verified":
        score += 10
    if item.get("metadataMatchStatus") == "match":
        score += 5
    if item.get("pairMatchStatus") == "match":
        score += 10
    return min(score, 100)


def build_unchecked(candidate, config):
    pair = candidate.get("pair") or {}
    txns = (pair.get("txns") or {}).get("h24") or {}
    liquidity = (pair.get("liquidity") or {}).get("usd")
    volume = (pair.get("volume") or {}).get("h24")
    price = float(pair["priceUsd"]) if pair.get("priceUsd") else candidate["rawPriceUsd"]
    market_cap = pair.get("marketCap") or candidate["rawMarketCapUsd"]
    result = {
        **candidate,
        "priceUsd": price,
        "liquidityUsd": liquidity,
        "volume24hUsd": volume,
        "marketCapUsd": market_cap,
        "recentBuys24h": txns.get("buys"),
        "recentSells24h": txns.get("sells"),
        "exitNotionalUsd": config["preflight"]["exitNotionalUsd"],
        "estimatedExitSlippagePct": None,
        "contractExistsStatus": "unknown",
        "metadataMatchStatus": "unknown",
        "pairMatchStatus": "match" if pair else "unknown",
        "sellPathStatus": "unknown",
        "contractRisk": "unknown",
        "preflightStatus": "not_checked",
        "verificationScope": "尚未进入本轮合约安全预检额度。",
        "verificationEvidence": [],
    }
    result["discoveryScore"] = discovery_score(result, config)
    return result


def apply_verification(candidate, verification, config):
    pair = candidate.get("pair") or {}
    result = {
        **candidate,
        "priceUsd": float(pair["priceUsd"]) if pair.get("priceUsd") else candidate["rawPriceUsd"],
        "liquidityUsd": (pair.get("liquidity") or {}).get("usd"),
        "volume24hUsd": (pair.get("volume") or {}).get("h24"),
        "marketCapUsd": pair.get("marketCap") or candidate["rawMarketCapUsd"],
        "recentBuys24h": verification.get("recentBuys24h"),
        "recentSells24h": verification.get("recentSells24h"),
        "exitNotionalUsd": verification["exitNotionalUsd"],
        "estimatedExitSlippagePct": verification.get("estimatedExitSlippagePct"),
        "contractExistsStatus": verification["contractExistsStatus"],
        "metadataMatchStatus": verification["metadataMatchStatus"],
        "pairMatchStatus": verification["pairMatchStatus"],
        "sellPathStatus": verification["sellPathStatus"],
        "contractRisk": verification["overallRisk"],
        "preflightStatus": verification["overallStatus"],
        "verificationScope": verification["verificationScope"],
        "verificationEvidence": verification["evidence"],
    }
    result["discoveryScore"] = discovery_score(result, config)
    return result


def collect_network_discoveries(
    config_path=DEFAULT_CONFIG_PATH,
    timeout=20,
    network_ids=None,
    source_keys=None,
):
    config = load_config(config_path)
    selected_networks, selected_sources = normalize_scan_scope(
        config,
        network_ids=network_ids,
        source_keys=source_keys,
    )
    candidates, source_stats, errors = source_candidates(
        config,
        timeout=timeout,
        network_ids=selected_networks,
        source_keys=selected_sources,
    )
    pair_results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(pair_check, candidate, timeout): candidate
            for candidate in candidates
        }
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                pair_results.append(future.result())
            except Exception as error:
                pair_results.append(
                    {
                        **candidate,
                        "pair": None,
                        "pairError": f"{type(error).__name__}: {error}",
                    }
                )

    pair_results.sort(key=market_priority, reverse=True)
    verified = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for candidate in pair_results:
            if not candidate.get("pair") or not candidate.get("symbol"):
                continue
            verification_candidate = {
                "caseId": f"discovery:{candidate['networkId']}:{candidate['contractAddress']}",
                "assetId": "",
                "symbol": candidate["symbol"],
                "networkId": candidate["networkId"],
                "contractAddress": candidate["contractAddress"],
                "identitySource": "常用链自动发现",
                "identitySourceId": candidate["sourceIds"][0],
                "sourceUrl": candidate["sourceUrls"][0],
                "isPrimary": False,
                "exitNotionalUsd": config["preflight"]["exitNotionalUsd"],
                "pair": candidate["pair"],
            }
            futures[executor.submit(verify_candidate, verification_candidate, timeout)] = candidate
        for future in as_completed(futures):
            candidate = futures[future]
            key = (
                candidate["networkId"],
                normalize_address(candidate["networkId"], candidate["contractAddress"]),
            )
            try:
                verified[key] = future.result()
            except Exception as error:
                errors.append(
                    {
                        "provider": "security_preflight",
                        "error": f"{type(error).__name__}: {error}",
                        "sourceUrl": candidate["sourceUrls"][0],
                    }
                )

    records = []
    for candidate in pair_results:
        key = (
            candidate["networkId"],
            normalize_address(candidate["networkId"], candidate["contractAddress"]),
        )
        if key in verified:
            records.append(apply_verification(candidate, verified[key], config))
        else:
            records.append(build_unchecked(candidate, config))
    records.sort(
        key=lambda item: (
            item["preflightStatus"] == "pass",
            item["discoveryScore"],
            item.get("liquidityUsd") or 0,
        ),
        reverse=True,
    )
    return {
        "version": config["version"],
        "collectedAt": utc_now(),
        "records": records,
        "sourceStats": source_stats,
        "errors": errors,
        "config": config,
        "scope": {
            "networkIds": selected_networks,
            "sourceKeys": selected_sources,
            "sourceIds": [
                SOURCE_DEFINITIONS[provider]["source_id"]
                for provider in selected_sources
            ],
            "noLimit": True,
        },
    }


def register_sources(connection, now):
    for definition in SOURCE_DEFINITIONS.values():
        connection.execute(
            """
            INSERT INTO sources (
              source_id, name, source_type, url, access_method, scope, confidence,
              conflict_risk, status, schedule_text, last_checked_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'convexity_discovery', '中', ?, 'active',
                    '候选库一键刷新', ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              status = 'active',
              conflict_risk = excluded.conflict_risk,
              last_checked_at = excluded.last_checked_at,
              updated_at = excluded.updated_at
            """,
            (
                definition["source_id"],
                definition["name"],
                definition["source_type"],
                definition["url"],
                definition["access_method"],
                definition["conflict_risk"],
                now,
                now,
                now,
            ),
        )


def persist_network_discoveries(connection, bundle, run_id, stable_id):
    now = utc_now()
    register_sources(connection, now)
    known_contracts = {
        (
            row["network_id"],
            normalize_address(row["network_id"], row["contract_address"]),
        )
        for row in connection.execute(
            "SELECT network_id, contract_address FROM asset_contracts"
        )
    }
    summary = {
        "observed": len(bundle["records"]),
        "new": 0,
        "preflightPassed": 0,
        "identityPending": 0,
        "existingAssets": 0,
        "rejected": 0,
        "errors": bundle["errors"],
    }
    for item in bundle["records"]:
        normalized = normalize_address(item["networkId"], item["contractAddress"])
        key = (item["networkId"], normalized)
        existing_row = connection.execute(
            """
            SELECT discovery_id
            FROM network_discoveries
            WHERE network_id = ? AND contract_address = ?
            """,
            (item["networkId"], normalized),
        ).fetchone()
        non_project_reason = ""
        if "robinhood token" in item["tokenName"].casefold():
            non_project_reason = "代币化股票或证券产品，不属于本轮加密项目发现范围。"
        if key in known_contracts:
            queue_status = "existing_asset"
            reason = "合约已属于正式候选资产，本次仅更新发现痕迹。"
            summary["existingAssets"] += 1
        elif non_project_reason:
            queue_status = "rejected"
            reason = non_project_reason
            summary["rejected"] += 1
        elif item["preflightStatus"] == "fail":
            queue_status = "rejected"
            reason = "合约、代币资料或卖出路径预检出现阻断。"
            summary["rejected"] += 1
        elif item["preflightStatus"] == "pass":
            queue_status = "preflight_pass"
            reason = "技术预检通过，仍需核验项目主体、价值捕获和凸性来源。"
            summary["preflightPassed"] += 1
        else:
            queue_status = "identity_pending"
            reason = "尚未完成本轮技术预检或项目主体身份核验。"
            summary["identityPending"] += 1
        if not existing_row:
            summary["new"] += 1

        source_conflict_risk = (
            "medium"
            if "discovery-robinhood-blockscout" in item["sourceIds"]
            else "high"
        )
        evidence = [
            {
                "type": "source_boundary",
                "summary": boundary,
            }
            for boundary in item["sourceBoundaries"]
        ] + item["verificationEvidence"]
        discovery_id = stable_id(
            "network-discovery",
            item["networkId"],
            normalized,
        )
        connection.execute(
            """
            INSERT INTO network_discoveries (
              discovery_id, network_id, contract_address, token_name, symbol,
              contract_standard, first_seen_at, last_seen_at, last_run_id,
              discovery_kinds_json, source_ids_json, source_urls_json,
              source_conflict_risk, holders_count, price_usd, liquidity_usd,
              volume_24h_usd, market_cap_usd, recent_buys_24h,
              recent_sells_24h, exit_notional_usd,
              estimated_exit_slippage_pct, contract_exists_status,
              metadata_match_status, pair_match_status, sell_path_status,
              contract_risk, preflight_status, discovery_score, queue_status,
              status_reason, evidence_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network_id, contract_address) DO UPDATE SET
              token_name = excluded.token_name,
              symbol = excluded.symbol,
              contract_standard = excluded.contract_standard,
              last_seen_at = excluded.last_seen_at,
              last_run_id = excluded.last_run_id,
              discovery_kinds_json = excluded.discovery_kinds_json,
              source_ids_json = excluded.source_ids_json,
              source_urls_json = excluded.source_urls_json,
              source_conflict_risk = excluded.source_conflict_risk,
              holders_count = excluded.holders_count,
              price_usd = excluded.price_usd,
              liquidity_usd = excluded.liquidity_usd,
              volume_24h_usd = excluded.volume_24h_usd,
              market_cap_usd = excluded.market_cap_usd,
              recent_buys_24h = excluded.recent_buys_24h,
              recent_sells_24h = excluded.recent_sells_24h,
              exit_notional_usd = excluded.exit_notional_usd,
              estimated_exit_slippage_pct = excluded.estimated_exit_slippage_pct,
              contract_exists_status = excluded.contract_exists_status,
              metadata_match_status = excluded.metadata_match_status,
              pair_match_status = excluded.pair_match_status,
              sell_path_status = excluded.sell_path_status,
              contract_risk = excluded.contract_risk,
              preflight_status = excluded.preflight_status,
              discovery_score = excluded.discovery_score,
              queue_status = excluded.queue_status,
              status_reason = excluded.status_reason,
              evidence_json = excluded.evidence_json,
              updated_at = excluded.updated_at
            """,
            (
                discovery_id,
                item["networkId"],
                normalized,
                item["tokenName"],
                item["symbol"],
                "SPL" if NETWORKS[item["networkId"]]["chainType"] == "Solana" else "ERC-20",
                now,
                now,
                run_id,
                json.dumps(item["discoveryKinds"], ensure_ascii=False),
                json.dumps(item["sourceIds"], ensure_ascii=False),
                json.dumps(item["sourceUrls"], ensure_ascii=False),
                source_conflict_risk,
                item["holdersCount"],
                item["priceUsd"],
                item["liquidityUsd"],
                item["volume24hUsd"],
                item["marketCapUsd"],
                item["recentBuys24h"],
                item["recentSells24h"],
                item["exitNotionalUsd"],
                item["estimatedExitSlippagePct"],
                item["contractExistsStatus"],
                item["metadataMatchStatus"],
                item["pairMatchStatus"],
                item["sellPathStatus"],
                item["contractRisk"],
                item["preflightStatus"],
                item["discoveryScore"],
                queue_status,
                reason,
                json.dumps(evidence, ensure_ascii=False),
                now,
                now,
            ),
        )
        payload = {
            "networkId": item["networkId"],
            "contractAddress": normalized,
            "tokenName": item["tokenName"],
            "symbol": item["symbol"],
            "discoveryKinds": item["discoveryKinds"],
            "sourceIds": item["sourceIds"],
            "queueStatus": queue_status,
            "statusReason": reason,
            "discoveryScore": item["discoveryScore"],
            "preflightStatus": item["preflightStatus"],
            "liquidityUsd": item["liquidityUsd"],
            "volume24hUsd": item["volume24hUsd"],
            "recentSells24h": item["recentSells24h"],
            "sellPathStatus": item["sellPathStatus"],
        }
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url, excerpt,
              project_hint, asset_hint, chain_hint, event_type, raw_payload_json,
              status
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, '', ?, ?,
                    'network_token_discovery', ?, 'normalized')
            """,
            (
                stable_id("raw-network-discovery", run_id, discovery_id),
                item["sourceIds"][0],
                run_id,
                f"{run_id}:{discovery_id}",
                now,
                hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                item["sourceUrls"][0],
                f"{item['tokenName']} {item['symbol']} · {reason}",
                item["symbol"],
                NETWORKS[item["networkId"]]["name"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        scan_status = {
            "preflight_pass": "eligible",
            "identity_pending": "pending",
            "existing_asset": "existing",
            "rejected": "rejected",
            "promoted": "eligible",
        }[queue_status]
        for source_index, source_id in enumerate(item["sourceIds"]):
            source_url = (
                item["sourceUrls"][source_index]
                if source_index < len(item["sourceUrls"])
                else ""
            )
            connection.execute(
                """
                INSERT INTO scan_results (
                  scan_result_id, run_id, network_id, source_id, discovery_id,
                  external_key, result_status, reason, source_url,
                  raw_payload_json, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id(
                        "scan-result",
                        run_id,
                        item["networkId"],
                        source_id,
                        normalized,
                    ),
                    run_id,
                    item["networkId"],
                    source_id,
                    discovery_id,
                    normalized,
                    scan_status,
                    reason,
                    source_url,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
    return summary


def build_discovery_snapshot(connection):
    rows = []
    for row in connection.execute(
        """
        SELECT nd.*, n.name AS network_name, n.chain_type, n.chain_id,
               n.environment, n.explorer_url, n.discovery_priority,
               ir.identity_review_id, ir.reviewed_at AS identity_reviewed_at,
               ir.provider AS identity_provider,
               ir.resolution_status AS identity_resolution_status,
               ir.confidence AS identity_confidence,
               ir.canonical_name AS identity_canonical_name,
               ir.coingecko_id, ir.website_url, ir.website_domain,
               ir.website_status, ir.official_contract_status,
               ir.name_match_status, ir.social_urls_json, ir.repo_urls_json,
               ir.value_capture_status, ir.promotion_status,
               ir.matched_project_id, ir.promoted_project_id,
               ir.promoted_asset_id, ir.promoted_case_id,
               ir.reason AS identity_reason,
               ir.evidence_json AS identity_evidence_json
        FROM network_discoveries nd
        JOIN networks n ON n.network_id = nd.network_id
        LEFT JOIN discovery_identity_reviews ir
          ON ir.identity_review_id = (
            SELECT newer.identity_review_id
            FROM discovery_identity_reviews newer
            WHERE newer.discovery_id = nd.discovery_id
            ORDER BY newer.reviewed_at DESC, newer.identity_review_id DESC
            LIMIT 1
          )
        ORDER BY
          CASE nd.queue_status
            WHEN 'promoted' THEN 0
            WHEN 'preflight_pass' THEN 1
            WHEN 'identity_pending' THEN 2
            WHEN 'existing_asset' THEN 3
            WHEN 'rejected' THEN 4
            ELSE 4
          END,
          nd.discovery_score DESC,
          nd.last_seen_at DESC
        """
    ):
        item = dict(row)
        rows.append(
            {
                "discoveryId": item["discovery_id"],
                "networkId": item["network_id"],
                "networkName": item["network_name"],
                "chainType": item["chain_type"],
                "chainId": item["chain_id"],
                "environment": item["environment"],
                "explorerUrl": explorer_address_url(
                    NETWORKS[item["network_id"]],
                    item["contract_address"],
                ),
                "discoveryPriority": item["discovery_priority"],
                "contractAddress": item["contract_address"],
                "tokenName": item["token_name"],
                "symbol": item["symbol"],
                "contractStandard": item["contract_standard"],
                "firstSeenAt": item["first_seen_at"],
                "lastSeenAt": item["last_seen_at"],
                "lastRunId": item["last_run_id"],
                "discoveryKinds": json.loads(item["discovery_kinds_json"]),
                "sourceIds": json.loads(item["source_ids_json"]),
                "sourceUrls": json.loads(item["source_urls_json"]),
                "sourceConflictRisk": item["source_conflict_risk"],
                "holdersCount": item["holders_count"],
                "priceUsd": item["price_usd"],
                "liquidityUsd": item["liquidity_usd"],
                "volume24hUsd": item["volume_24h_usd"],
                "marketCapUsd": item["market_cap_usd"],
                "recentBuys24h": item["recent_buys_24h"],
                "recentSells24h": item["recent_sells_24h"],
                "exitNotionalUsd": item["exit_notional_usd"],
                "estimatedExitSlippagePct": item["estimated_exit_slippage_pct"],
                "contractExistsStatus": item["contract_exists_status"],
                "metadataMatchStatus": item["metadata_match_status"],
                "pairMatchStatus": item["pair_match_status"],
                "sellPathStatus": item["sell_path_status"],
                "contractRisk": item["contract_risk"],
                "preflightStatus": item["preflight_status"],
                "discoveryScore": item["discovery_score"],
                "queueStatus": item["queue_status"],
                "statusReason": item["status_reason"],
                "evidence": json.loads(item["evidence_json"]),
                "identityReview": (
                    {
                        "reviewedAt": item["identity_reviewed_at"],
                        "provider": item["identity_provider"],
                        "resolutionStatus": item["identity_resolution_status"],
                        "confidence": item["identity_confidence"],
                        "canonicalName": item["identity_canonical_name"],
                        "coingeckoId": item["coingecko_id"],
                        "websiteUrl": item["website_url"],
                        "websiteDomain": item["website_domain"],
                        "websiteStatus": item["website_status"],
                        "officialContractStatus": item[
                            "official_contract_status"
                        ],
                        "nameMatchStatus": item["name_match_status"],
                        "socialUrls": json.loads(item["social_urls_json"]),
                        "repoUrls": json.loads(item["repo_urls_json"]),
                        "valueCaptureStatus": item["value_capture_status"],
                        "promotionStatus": item["promotion_status"],
                        "matchedProjectId": item["matched_project_id"],
                        "promotedProjectId": item["promoted_project_id"],
                        "promotedAssetId": item["promoted_asset_id"],
                        "promotedCaseId": item["promoted_case_id"],
                        "reason": item["identity_reason"],
                        "evidence": json.loads(item["identity_evidence_json"]),
                    }
                    if item["identity_review_id"]
                    else None
                ),
            }
        )
    latest_run = connection.execute(
        """
        SELECT *
        FROM runs
        WHERE run_id LIKE 'candidate-refresh-%'
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "version": RULE_VERSION,
        "generatedAt": utc_now(),
        "boundary": (
            "发现排序分只用于安排身份与研究复核顺序，不是投资评分。"
            "推广资料不能证明项目身份，技术预检通过也不能直接进入行动池。"
            "自动身份归属只允许升格到影子研究库，价值捕获与凸性仍需独立研究。"
        ),
        "latestRun": dict(latest_run) if latest_run else None,
        "counts": {
            "total": len(rows),
            "preflightPass": sum(row["queueStatus"] == "preflight_pass" for row in rows),
            "identityPending": sum(row["queueStatus"] == "identity_pending" for row in rows),
            "existingAssets": sum(row["queueStatus"] == "existing_asset" for row in rows),
            "rejected": sum(row["queueStatus"] == "rejected" for row in rows),
            "promoted": sum(row["queueStatus"] == "promoted" for row in rows),
            "identityReviewed": sum(row["identityReview"] is not None for row in rows),
            "robinhood": sum(row["networkId"] == "robinhood-mainnet" for row in rows),
        },
        "records": rows,
    }


def write_discovery_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_NETWORK_DISCOVERIES = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(path)
