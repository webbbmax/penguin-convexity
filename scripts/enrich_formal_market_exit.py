#!/usr/bin/env python3
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from contract_tradeability import (
    CHAIN_TO_NETWORK,
    NETWORKS,
    best_pair,
    persist_contract_checks,
    request_json,
    user_environment,
    verify_candidate,
)
from rule_engine import load_rulebook


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "convexity.db"
DEFAULT_CONFIG_PATH = (
    PROJECT_ROOT / "fixtures" / "candidate-refresh-sources-v1.json"
)
ENRICHMENT_VERSION = "C1.4-02"
SOURCE_DEFINITION = {
    "source_id": "formal-project-market-exit-enrichment",
    "name": "正式项目市场与退出资料",
    "source_type": "derived_market_registry",
    "url": "local://formal-project-market-exit-enrichment",
    "access_method": "CoinGecko、DexScreener 与只读合约核验",
}
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
EXIT_NOTIONAL_USD = 100.0


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def register_source(connection, now):
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
            SOURCE_DEFINITION["source_id"],
            SOURCE_DEFINITION["name"],
            SOURCE_DEFINITION["source_type"],
            SOURCE_DEFINITION["url"],
            SOURCE_DEFINITION["access_method"],
            now,
            now,
            now,
        ),
    )


def latest_coingecko_ids(connection):
    mappings = {}
    if connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'project_asset_identity_reviews'
        """
    ).fetchone():
        for row in connection.execute(
            """
            SELECT review.project_id, review.coingecko_id
            FROM project_asset_identity_reviews review
            WHERE review.coingecko_id != ''
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
                      AND newer.project_asset_review_id >
                          review.project_asset_review_id
                    )
                  )
              )
            """
        ):
            mappings[row["project_id"]] = row["coingecko_id"]
    rows = connection.execute(
        """
        SELECT *
        FROM discovery_identity_reviews
        WHERE coingecko_id != ''
          AND (promoted_project_id IS NOT NULL OR matched_project_id IS NOT NULL)
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
            mappings.setdefault(project_id, item["coingecko_id"])
    return mappings


def configured_coingecko_ids(connection, config_path=DEFAULT_CONFIG_PATH):
    if not Path(config_path).exists():
        return {}
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    by_case = {
        item["caseId"]: item["coinId"]
        for item in config.get("projects", [])
        if item.get("provider") == "coingecko" and item.get("coinId")
    }
    return {
        row["project_id"]: by_case[row["case_id"]]
        for row in connection.execute(
            "SELECT project_id, case_id FROM candidate_cases"
        )
        if row["case_id"] in by_case
    }


def formal_assets(connection, config_path=DEFAULT_CONFIG_PATH):
    coin_ids = configured_coingecko_ids(connection, config_path)
    coin_ids.update(latest_coingecko_ids(connection))
    rows = connection.execute(
        """
        SELECT
          p.project_id,
          p.canonical_name AS project_name,
          p.identity_status AS project_identity_status,
          a.asset_id,
          a.symbol,
          a.chain,
          a.contract_address,
          a.identity_status AS asset_identity_status,
          (
            SELECT cc.case_id
            FROM candidate_cases cc
            WHERE cc.project_id = p.project_id
            ORDER BY cc.updated_at DESC, cc.case_id DESC
            LIMIT 1
          ) AS case_id,
          (
            SELECT ac.network_id
            FROM asset_contracts ac
            WHERE ac.asset_id = a.asset_id
            ORDER BY ac.is_primary DESC, ac.updated_at DESC
            LIMIT 1
          ) AS contract_network_id
        FROM projects p
        LEFT JOIN assets a ON a.project_id = p.project_id
        WHERE p.identity_status != 'rejected'
        ORDER BY p.canonical_name, a.symbol, a.asset_id
        """
    )
    records = []
    for row in rows:
        item = dict(row)
        item["coinGeckoId"] = coin_ids.get(item["project_id"], "")
        item["networkId"] = (
            item.get("contract_network_id")
            or CHAIN_TO_NETWORK.get(item.get("chain"))
            or ""
        )
        records.append(item)
    return records


def fetch_coingecko(records, timeout):
    coin_ids = sorted({item["coinGeckoId"] for item in records if item["coinGeckoId"]})
    if not coin_ids:
        return {}, []
    query = (
        "?vs_currency=usd&per_page=250&page=1&sparkline=false"
        "&price_change_percentage=24h&ids="
        + ",".join(coin_ids)
    )
    headers = {}
    api_key = user_environment("COINGECKO_DEMO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    try:
        payload = request_json(
            f"{COINGECKO_MARKETS_URL}{query}",
            headers=headers,
            timeout=timeout,
        )
        return {item["id"]: item for item in payload}, []
    except Exception as error:
        return {}, [
            {
                "provider": "coingecko",
                "error": f"{type(error).__name__}: {error}",
            }
        ]


def fetch_dex_pairs(records, timeout):
    eligible = [
        item
        for item in records
        if item.get("asset_id")
        and item.get("contract_address")
        and item.get("networkId") in NETWORKS
    ]
    pairs = {}
    errors = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        pending = {
            executor.submit(
                best_pair,
                NETWORKS[item["networkId"]],
                item["contract_address"],
                timeout,
            ): item
            for item in eligible
        }
        for future in as_completed(pending):
            item = pending[future]
            try:
                pairs[item["asset_id"]] = future.result()
            except Exception as error:
                errors.append(
                    {
                        "provider": "dexscreener",
                        "projectId": item["project_id"],
                        "assetId": item["asset_id"],
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
    return pairs, errors


def estimate_slippage(liquidity_usd, exit_notional_usd):
    if not liquidity_usd or float(liquidity_usd) <= 0:
        return None
    return round(
        min(100, (200 * float(exit_notional_usd)) / float(liquidity_usd)),
        4,
    )


def combined_market_record(asset, coin, pair, now, exit_notional_usd):
    pair = pair or {}
    liquidity = (pair.get("liquidity") or {}).get("usd")
    dex_volume = (pair.get("volume") or {}).get("h24")
    source_ids = []
    if coin:
        source_ids.append("market-coingecko")
    if pair:
        source_ids.append("market-dexscreener")
    fields = {
        "priceUsd": (
            coin.get("current_price")
            if coin and coin.get("current_price") is not None
            else float(pair["priceUsd"])
            if pair.get("priceUsd")
            else None
        ),
        "liquidityUsd": liquidity,
        "volume24hUsd": (
            coin.get("total_volume")
            if coin and coin.get("total_volume") is not None
            else dex_volume
        ),
        "marketCapUsd": (
            coin.get("market_cap")
            if coin and coin.get("market_cap") is not None
            else pair.get("marketCap")
        ),
        "fdvUsd": (
            coin.get("fully_diluted_valuation")
            if coin and coin.get("fully_diluted_valuation") is not None
            else pair.get("fdv")
        ),
        "circulatingSupply": coin.get("circulating_supply") if coin else None,
        "priceChange24hPct": (
            coin.get("price_change_percentage_24h") if coin else None
        ),
    }
    available = [name for name, value in fields.items() if value is not None]
    if not available:
        return {
            **asset,
            "status": "no_data",
            "sourceIds": source_ids,
            "summary": "现有免费行情源没有返回可用市场或交易池数据。",
        }
    pair_url = pair.get("url") or ""
    coin_url = (
        f"https://www.coingecko.com/en/coins/{asset['coinGeckoId']}"
        if asset.get("coinGeckoId")
        else ""
    )
    venue = None
    if pair.get("pairAddress"):
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        venue = {
            "name": pair.get("dexId") or "DEX",
            "pairSymbol": f"{base.get('symbol', '')}/{quote.get('symbol', '')}",
            "poolAddress": pair["pairAddress"],
            "sourceUrl": pair_url,
        }
    return {
        **asset,
        **fields,
        "status": "success",
        "observedAt": now,
        "exitNotionalUsd": exit_notional_usd if liquidity is not None else None,
        "estimatedExitSlippagePct": estimate_slippage(
            liquidity,
            exit_notional_usd,
        ),
        "sourceIds": source_ids,
        "sourceUrl": pair_url or coin_url,
        "coinGeckoUrl": coin_url,
        "dexScreenerUrl": pair_url,
        "venue": venue,
        "pair": pair or None,
        "definitionNote": (
            "CoinGecko 提供聚合价格、市值、FDV与成交快照；"
            "DexScreener 提供当前最深单池流动性和成交池。"
            f"滑点按 {exit_notional_usd:.0f} 美元恒定乘积退出近似，"
            "仅用于初筛，不替代真实卖出测试。"
        ),
    }


def contract_candidate(record, exit_notional_usd):
    if (
        not record.get("asset_id")
        or not record.get("case_id")
        or not record.get("contract_address")
        or record.get("networkId") not in NETWORKS
    ):
        return None
    network = NETWORKS[record["networkId"]]
    source_url = record.get("coinGeckoUrl") or (
        f"{network['explorer'].rstrip('/')}/"
        f"{'token' if network['chainType'] == 'Solana' else 'address'}/"
        f"{record['contract_address']}"
    )
    return {
        "caseId": record["case_id"],
        "assetId": record["asset_id"],
        "symbol": record.get("symbol") or "",
        "networkId": record["networkId"],
        "contractAddress": record["contract_address"],
        "identitySource": "正式项目主库与市场身份登记",
        "identitySourceId": (
            "identity-coingecko-registry"
            if record.get("coinGeckoId")
            else "contract-identity-mapping"
        ),
        "sourceUrl": source_url,
        "isPrimary": True,
        "exitNotionalUsd": exit_notional_usd,
        "pair": record.get("pair"),
    }


def collect_formal_market_exit(
    db_path=DEFAULT_DB_PATH,
    config_path=DEFAULT_CONFIG_PATH,
    timeout=20,
    exit_notional_usd=EXIT_NOTIONAL_USD,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        assets = formal_assets(connection, config_path)
    finally:
        connection.close()
    now = utc_now()
    coins, coin_errors = fetch_coingecko(assets, timeout)
    pairs, pair_errors = fetch_dex_pairs(assets, timeout)
    records = [
        combined_market_record(
            asset,
            coins.get(asset.get("coinGeckoId")),
            pairs.get(asset.get("asset_id")),
            now,
            exit_notional_usd,
        )
        for asset in assets
    ]
    candidates = [
        candidate
        for candidate in (
            contract_candidate(record, exit_notional_usd) for record in records
        )
        if candidate
    ]
    contract_results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        pending = {
            executor.submit(verify_candidate, candidate, timeout): candidate
            for candidate in candidates
        }
        for future in as_completed(pending):
            candidate = pending[future]
            try:
                contract_results.append(future.result())
            except Exception as error:
                contract_results.append(
                    {
                        **candidate,
                        "provider": "contract_mapping",
                        "status": "failed",
                        "sourceUrl": candidate.get("sourceUrl", ""),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
    case_records = {
        item["case_id"]: {
            "caseId": item["case_id"],
            "canonicalName": item["project_name"],
            "symbol": item.get("symbol") or "",
            "chain": item.get("chain") or "",
            "assetId": item.get("asset_id"),
        }
        for item in assets
        if item.get("case_id")
    }
    return {
        "records": records,
        "contractResults": sorted(
            contract_results,
            key=lambda item: item["caseId"],
        ),
        "caseRecords": case_records,
        "errors": coin_errors + pair_errors,
        "projectsReviewed": len({item["project_id"] for item in assets}),
        "assetsReviewed": sum(bool(item.get("asset_id")) for item in assets),
    }


def market_changes(previous, current):
    fields = (
        ("price_usd", "priceUsd", "价格"),
        ("liquidity_usd", "liquidityUsd", "流动性"),
        ("volume_24h_usd", "volume24hUsd", "24小时成交额"),
        ("market_cap_usd", "marketCapUsd", "流通市值"),
        ("fdv_usd", "fdvUsd", "FDV"),
        (
            "estimated_exit_slippage_pct",
            "estimatedExitSlippagePct",
            "估算退出滑点",
        ),
    )
    changes = []
    for old_key, new_key, label in fields:
        old_value = previous[old_key] if previous else None
        new_value = current.get(new_key)
        if new_value is None:
            continue
        if old_value is not None and float(old_value) == float(new_value):
            continue
        changes.append(
            {
                "field": label,
                "before": old_value,
                "after": new_value,
                "sourceUrl": (
                    current.get("dexScreenerUrl")
                    if new_key in {"liquidityUsd", "estimatedExitSlippagePct"}
                    else current.get("coinGeckoUrl")
                    or current.get("dexScreenerUrl")
                ),
            }
        )
    return changes


def classify_market_grade(record):
    required = (
        record.get("liquidityUsd"),
        record.get("volume24hUsd"),
        record.get("estimatedExitSlippagePct"),
    )
    if any(value is None for value in required):
        return "unknown"
    rulebook = load_rulebook()
    standard = rulebook["tradeability"]["standard"]
    if (
        record["liquidityUsd"] >= standard["minimum_liquidity_usd"]
        and record["volume24hUsd"] >= standard["minimum_volume_24h_usd"]
        and record["estimatedExitSlippagePct"]
        <= standard["maximum_exit_slippage_pct"]
    ):
        return "standard"
    extreme = rulebook["tradeability"]["extreme"]
    if (
        record["liquidityUsd"] >= extreme["minimum_liquidity_usd"]
        and record["volume24hUsd"] >= extreme["minimum_volume_24h_usd"]
        and record["estimatedExitSlippagePct"]
        <= extreme["maximum_exit_slippage_pct"]
    ):
        return "extreme"
    return "untradeable"


def persist_formal_market_exit(
    connection,
    bundle,
    run_id,
    now,
    stable_id,
):
    register_source(connection, now)
    changed_projects = set()
    market_projects = set()
    exit_projects = set()
    pending_projects = set()
    project_results = []
    for record in bundle["records"]:
        project_id = record["project_id"]
        if record["status"] != "success" or not record.get("asset_id"):
            pending_projects.add(project_id)
            changes = [
                {
                    "field": "市场与退出资料",
                    "before": "",
                    "after": record.get("summary") or "项目尚无可映射资产。",
                    "sourceUrl": "",
                }
            ]
            summary = (
                f"{record['project_name']} 暂未取得可用市场或退出资料。"
            )
        else:
            market_projects.add(project_id)
            previous = connection.execute(
                """
                SELECT *
                FROM market_snapshots
                WHERE asset_id = ?
                ORDER BY observed_at DESC, snapshot_id DESC
                LIMIT 1
                """,
                (record["asset_id"],),
            ).fetchone()
            changes = market_changes(previous, record)
            if changes:
                changed_projects.add(project_id)
            venue_id = None
            if record.get("venue"):
                venue_id = stable_id(
                    "venue",
                    record["asset_id"],
                    record["venue"]["poolAddress"],
                )
                connection.execute(
                    """
                    INSERT INTO venues (
                      venue_id, asset_id, venue_name, venue_type, pair_symbol,
                      pool_address, buy_status, sell_status, status, checked_at,
                      created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'DEX', ?, ?, 'unknown', 'unknown',
                            'active', ?, ?, ?)
                    ON CONFLICT(venue_id) DO UPDATE SET
                      venue_name = excluded.venue_name,
                      pair_symbol = excluded.pair_symbol,
                      status = excluded.status,
                      checked_at = excluded.checked_at,
                      updated_at = excluded.updated_at
                    """,
                    (
                        venue_id,
                        record["asset_id"],
                        record["venue"]["name"],
                        record["venue"]["pairSymbol"],
                        record["venue"]["poolAddress"],
                        now,
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO market_snapshots (
                  snapshot_id, asset_id, venue_id, observed_at, price_usd,
                  liquidity_usd, volume_24h_usd, market_cap_usd, fdv_usd,
                  circulating_supply, exit_notional_usd,
                  estimated_exit_slippage_pct, data_source_id, definition_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id("formal-market", run_id, record["asset_id"]),
                    record["asset_id"],
                    venue_id,
                    record["observedAt"],
                    record.get("priceUsd"),
                    record.get("liquidityUsd"),
                    record.get("volume24hUsd"),
                    record.get("marketCapUsd"),
                    record.get("fdvUsd"),
                    record.get("circulatingSupply"),
                    record.get("exitNotionalUsd"),
                    record.get("estimatedExitSlippagePct"),
                    SOURCE_DEFINITION["source_id"],
                    record["definitionNote"],
                ),
            )
            grade = classify_market_grade(record)
            if grade != "unknown":
                connection.execute(
                    """
                    UPDATE candidate_cases
                    SET liquidity_grade = ?, updated_at = ?
                    WHERE case_id = ?
                    """,
                    (grade, now, record["case_id"]),
                )
            if record.get("estimatedExitSlippagePct") is not None:
                exit_projects.add(project_id)
            refreshed = [
                label
                for key, label in (
                    ("priceUsd", "价格"),
                    ("marketCapUsd", "流通市值"),
                    ("fdvUsd", "FDV"),
                    ("liquidityUsd", "流动性"),
                    ("volume24hUsd", "24小时成交额"),
                    ("estimatedExitSlippagePct", "估算退出滑点"),
                )
                if record.get(key) is not None
            ]
            summary = (
                f"{record['project_name']} 刷新 "
                f"{'、'.join(refreshed) if refreshed else '市场资料'}。"
            )
        payload = {
            "summary": summary,
            "changes": changes,
            "sourceIds": record.get("sourceIds", []),
            "boundary": (
                "CoinGecko 是聚合市场快照；DexScreener 是当前最深单池快照。"
                "估算滑点不是钱包真实卖出测试，资料补齐也不直接产生行动结论。"
            ),
            "version": ENRICHMENT_VERSION,
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
                    'formal_project_market_exit_enrichment', ?, 'normalized')
            """,
            (
                stable_id(
                    "formal-market-event",
                    run_id,
                    record["project_id"],
                    record.get("asset_id") or "no-asset",
                ),
                SOURCE_DEFINITION["source_id"],
                run_id,
                (
                    f"{run_id}:formal-market:"
                    f"{record['project_id']}:{record.get('asset_id') or 'none'}"
                ),
                now,
                hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                record.get("sourceUrl", ""),
                summary,
                record["project_name"],
                record.get("symbol") or "",
                record.get("chain") or "",
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        project_results.append(
            {
                "projectId": project_id,
                "projectName": record["project_name"],
                "assetId": record.get("asset_id"),
                "status": record["status"],
                "summary": summary,
                "changes": changes,
                "sourceUrl": record.get("sourceUrl", ""),
            }
        )

    contract_summary = persist_contract_checks(
        connection,
        bundle.get("contractResults", []),
        bundle.get("caseRecords", {}),
        run_id,
        now,
        stable_id,
    )
    errors = list(bundle.get("errors", [])) + contract_summary["errors"]
    return {
        "projectsReviewed": bundle["projectsReviewed"],
        "assetsReviewed": bundle["assetsReviewed"],
        "marketCoveredProjects": len(market_projects),
        "exitCoveredProjects": len(exit_projects),
        "pendingProjects": len(pending_projects),
        "changedProjects": len(changed_projects),
        "contractChecks": contract_summary["success"],
        "sellPathsVerified": contract_summary["sellPathsVerified"],
        "contractPassed": contract_summary["passed"],
        "failed": len(errors),
        "errors": errors,
        "projects": project_results,
    }
