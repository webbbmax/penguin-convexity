#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from contract_tradeability import (
    SOURCE_DEFINITIONS as CONTRACT_SOURCE_DEFINITIONS,
    collect_contract_checks,
    persist_contract_checks,
    user_environment,
)
from build_project_master_pool import (
    build_master_pool_snapshot,
    write_master_pool_snapshot,
)
from build_project_detail_snapshot import (
    build_project_detail_snapshot,
    write_project_detail_snapshot,
)
from build_scan_center_snapshot import (
    build_scan_center_snapshot,
    write_scan_center_snapshot,
)
from build_manual_review_snapshot import (
    build_manual_review_snapshot,
    write_manual_review_snapshot,
)
from build_update_center_snapshot import rebuild_update_snapshots
from discover_network_tokens import (
    SOURCE_DEFINITIONS as DISCOVERY_SOURCE_DEFINITIONS,
    build_discovery_snapshot,
    collect_network_discoveries,
    persist_network_discoveries,
    write_discovery_snapshot,
)
from init_db import DEFAULT_DB_PATH, DEFAULT_SNAPSHOT_PATH, initialize_database, write_runtime_snapshot
from resolve_discovery_identities import (
    SOURCE_DEFINITION as IDENTITY_SOURCE_DEFINITION,
    collect_identity_reviews,
    persist_identity_reviews,
)
from enrich_project_profiles import (
    SOURCE_DEFINITION as PROFILE_ENRICHMENT_SOURCE_DEFINITION,
    persist_formal_project_enrichment,
)
from enrich_machine_asset_identities import (
    SOURCE_DEFINITION as PROJECT_ASSET_IDENTITY_SOURCE_DEFINITION,
    collect_machine_project_asset_identities,
    persist_machine_project_asset_identities,
)
from score_machine_research import (
    SOURCE_DEFINITION as MACHINE_RESEARCH_SCORING_SOURCE_DEFINITION,
    persist_machine_research_scores,
)
from publish_machine_conclusions import (
    SOURCE_DEFINITION as MACHINE_CONCLUSION_SOURCE_DEFINITION,
    persist_machine_conclusions,
)
from catalyst_trade_paths import (
    CATALYST_PATH_SOURCE_DEFINITION,
    persist_catalyst_trade_paths,
)
from monitoring_infrastructure import (
    SOURCE_DEFINITION as MONITORING_INFRASTRUCTURE_SOURCE_DEFINITION,
    persist_monitoring_targets,
)
from build_catalyst_trade_path_snapshot import (
    build_catalyst_trade_path_snapshot,
    write_catalyst_trade_path_snapshot,
)
from build_monitoring_infrastructure_snapshot import (
    build_monitoring_infrastructure_snapshot,
    write_monitoring_infrastructure_snapshot,
)
from weak_signal_inbox import (
    SOURCE_DEFINITION as WEAK_SIGNAL_SOURCE_DEFINITION,
    persist_weak_signals,
)
from build_weak_signal_snapshot import (
    build_weak_signal_snapshot,
    write_weak_signal_snapshot,
)
from data_backbone import (
    SOURCE_DEFINITION as DATA_BACKBONE_SOURCE_DEFINITION,
    build_data_backbone_snapshot,
    run_data_backbone,
    write_data_backbone_snapshot,
)
from enrich_formal_market_exit import (
    SOURCE_DEFINITION as FORMAL_MARKET_EXIT_SOURCE_DEFINITION,
    collect_formal_market_exit,
    persist_formal_market_exit,
)
from enrich_formal_research_materials import (
    SOURCE_DEFINITION as FORMAL_RESEARCH_MATERIALS_SOURCE_DEFINITION,
    collect_formal_research_materials,
    persist_formal_research_materials,
)
from refresh_project_lifecycle import refresh_lifecycle_cache
from project_identity_aliases import sync_project_identity_aliases
from source_adapter import (
    build_source_adapter_snapshot,
    run_source_adapter,
    write_source_adapter_snapshot,
)
from high_value_sources import (
    SOURCE_DEFINITIONS as HIGH_VALUE_SOURCE_DEFINITIONS,
    build_high_value_snapshot,
    collect_high_value_sources,
    persist_high_value_sources,
    write_high_value_snapshot,
)
from source_discovery_attribution import (
    SOURCE_DEFINITIONS as SOURCE_DISCOVERY_DEFINITIONS,
    build_source_discovery_snapshot,
    collect_source_discoveries,
    persist_source_discoveries,
    write_source_discovery_snapshot,
)
from build_discovery_funnel_snapshot import rebuild_discovery_funnel_snapshot
from build_evidence_ledger_snapshot import (
    build_evidence_ledger_snapshot,
    sync_evidence_lineage,
    write_evidence_ledger_snapshot,
)
from build_opportunity_center_snapshot import rebuild_opportunity_center_snapshot
from build_research_route_snapshot import rebuild_research_route_snapshot
from build_tracking_tasks_snapshot import rebuild_tracking_tasks_snapshot
from execute_tracking_tasks import execute_tracking_tasks
from build_change_explanations_snapshot import rebuild_change_explanations_snapshot
from build_decision_quality_snapshots import build_decision_quality_snapshots
from build_model_acceptance_snapshot import rebuild_model_acceptance_snapshot
from build_four_layer_screening_snapshot import (
    DEFAULT_GOLD_EXPECTED_PATH,
    DEFAULT_GOLD_INPUT_PATH,
    DEFAULT_OUTPUT_PATH as FOUR_LAYER_OUTPUT_PATH,
    build_snapshot as build_four_layer_snapshot,
    write_snapshot as write_four_layer_snapshot,
)
from rule_engine import load_rulebook
from sync_thread_candidates import (
    build_pool_snapshot,
    load_fixture,
    machine_fixture,
    stable_id,
    write_pool_snapshot,
)
from update_tasks import TASK_DEFINITIONS, task_definition


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "fixtures" / "candidate-refresh-sources-v1.json"
DEFAULT_POOL_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "candidate-pool-snapshot.js"
JOB_NAME = "凸性候选实时刷新"
USER_AGENT = "Penguin-Convexity/1.0"

SOURCE_DEFINITIONS = {
    "coingecko": {
        "source_id": "market-coingecko",
        "name": "CoinGecko",
        "source_type": "market_api",
        "url": "https://api.coingecko.com/api/v3",
        "access_method": "Demo API",
    },
    "dexscreener": {
        "source_id": "market-dexscreener",
        "name": "DexScreener",
        "source_type": "market_api",
        "url": "https://api.dexscreener.com",
        "access_method": "Public API",
    },
    "evidence": {
        "source_id": "evidence-link-health",
        "name": "证据链接健康检查",
        "source_type": "source_health",
        "url": "multiple://candidate-evidence",
        "access_method": "HTTP read-only",
    },
    "mapping": {
        "source_id": "candidate-market-mapping",
        "name": "候选资产映射",
        "source_type": "internal_registry",
        "url": "local://candidate-refresh-sources-v1",
        "access_method": "Local fixture",
    },
    "formal_market_exit": FORMAL_MARKET_EXIT_SOURCE_DEFINITION,
    "formal_research_materials": FORMAL_RESEARCH_MATERIALS_SOURCE_DEFINITION,
    "catalyst_path": CATALYST_PATH_SOURCE_DEFINITION,
    "monitoring_infrastructure": MONITORING_INFRASTRUCTURE_SOURCE_DEFINITION,
    "weak_signals": WEAK_SIGNAL_SOURCE_DEFINITION,
    "data_backbone": DATA_BACKBONE_SOURCE_DEFINITION,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_id_now():
    return datetime.now(timezone.utc).strftime("candidate-refresh-%Y%m%dT%H%M%S%fZ")


def load_config(path=DEFAULT_CONFIG_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def request_json(url, headers=None, timeout=20):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response), response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))


def coingecko_results(mappings, timeout=20):
    coin_ids = [item["coinId"] for item in mappings]
    query = urllib.parse.urlencode(
        {
            "vs_currency": "usd",
            "ids": ",".join(coin_ids),
            "price_change_percentage": "24h",
        }
    )
    headers = {}
    api_key = user_environment("COINGECKO_DEMO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    url = f"https://api.coingecko.com/api/v3/coins/markets?{query}"
    try:
        payload, _, _ = request_json(url, headers=headers, timeout=timeout)
        by_id = {item["id"]: item for item in payload}
        results = []
        for mapping in mappings:
            item = by_id.get(mapping["coinId"])
            if not item:
                results.append(
                    {
                        "caseId": mapping["caseId"],
                        "provider": "coingecko",
                        "status": "failed",
                        "sourceUrl": url,
                        "error": f"CoinGecko 没有返回 {mapping['coinId']}",
                    }
                )
                continue
            results.append(
                {
                    "caseId": mapping["caseId"],
                    "provider": "coingecko",
                    "status": "success",
                    "sourceUrl": f"https://www.coingecko.com/en/coins/{mapping['coinId']}",
                    "observedAt": item.get("last_updated") or utc_now(),
                    "priceUsd": item.get("current_price"),
                    "liquidityUsd": None,
                    "volume24hUsd": item.get("total_volume"),
                    "marketCapUsd": item.get("market_cap"),
                    "fdvUsd": item.get("fully_diluted_valuation"),
                    "circulatingSupply": item.get("circulating_supply"),
                    "priceChange24hPct": item.get("price_change_percentage_24h"),
                    "exitNotionalUsd": None,
                    "estimatedExitSlippagePct": None,
                    "definitionNote": "CoinGecko 聚合行情；24 小时成交额不等于可退出流动性。",
                    "raw": item,
                }
            )
        return results
    except Exception as error:
        return [
            {
                "caseId": mapping["caseId"],
                "provider": "coingecko",
                "status": "failed",
                "sourceUrl": url,
                "error": f"{type(error).__name__}: {error}",
            }
            for mapping in mappings
        ]


def estimate_constant_product_slippage(liquidity_usd, exit_notional_usd):
    if not liquidity_usd or liquidity_usd <= 0:
        return None
    return round(min(100, (200 * exit_notional_usd) / liquidity_usd), 4)


def dexscreener_result(mapping, exit_notional_usd, timeout=20):
    url = (
        "https://api.dexscreener.com/latest/dex/pairs/"
        f"{mapping['chainId']}/{mapping['pairId']}"
    )
    try:
        payload, _, _ = request_json(url, timeout=timeout)
        pairs = payload.get("pairs") or []
        if not pairs:
            raise RuntimeError("DexScreener 没有返回交易池")
        pair = pairs[0]
        liquidity = (pair.get("liquidity") or {}).get("usd")
        volume = (pair.get("volume") or {}).get("h24")
        return {
            "caseId": mapping["caseId"],
            "provider": "dexscreener",
            "status": "success",
            "sourceUrl": pair.get("url") or url,
            "observedAt": utc_now(),
            "priceUsd": float(pair["priceUsd"]) if pair.get("priceUsd") else None,
            "liquidityUsd": liquidity,
            "volume24hUsd": volume,
            "marketCapUsd": pair.get("marketCap"),
            "fdvUsd": pair.get("fdv"),
            "circulatingSupply": None,
            "priceChange24hPct": (pair.get("priceChange") or {}).get("h24"),
            "exitNotionalUsd": exit_notional_usd,
            "estimatedExitSlippagePct": estimate_constant_product_slippage(
                liquidity, exit_notional_usd
            ),
            "definitionNote": (
                f"DexScreener 单池数据；滑点按 {exit_notional_usd:.0f} 美元恒定乘积退出近似，"
                "不替代真实卖出测试。"
            ),
            "venue": {
                "name": pair.get("dexId") or "DEX",
                "pairSymbol": (
                    f"{(pair.get('baseToken') or {}).get('symbol', '')}/"
                    f"{(pair.get('quoteToken') or {}).get('symbol', '')}"
                ),
                "poolAddress": pair.get("pairAddress") or mapping["pairId"],
            },
            "raw": pair,
        }
    except Exception as error:
        return {
            "caseId": mapping["caseId"],
            "provider": "dexscreener",
            "status": "failed",
            "sourceUrl": url,
            "error": f"{type(error).__name__}: {error}",
        }


def check_evidence_link(case_id, evidence, timeout=12):
    url = evidence["url"]
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-65535"},
    )
    try:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read(65536)
                    status_code = response.status
                    headers = dict(response.headers)
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
        fingerprint_source = (
            headers.get("ETag")
            or headers.get("Last-Modified")
            or hashlib.sha256(body).hexdigest()
        )
        return {
            "caseId": case_id,
            "provider": "evidence",
            "status": "success",
            "sourceUrl": url,
            "httpStatus": status_code,
            "fingerprint": str(fingerprint_source),
            "summary": evidence["summary"],
        }
    except urllib.error.HTTPError as error:
        restricted = error.code in (401, 403, 429)
        return {
            "caseId": case_id,
            "provider": "evidence",
            "status": "restricted" if restricted else "failed",
            "sourceUrl": url,
            "httpStatus": error.code,
            "fingerprint": "",
            "summary": evidence["summary"],
            "error": f"HTTP {error.code}",
        }
    except Exception as error:
        return {
            "caseId": case_id,
            "provider": "evidence",
            "status": "failed",
            "sourceUrl": url,
            "httpStatus": None,
            "fingerprint": "",
            "summary": evidence["summary"],
            "error": f"{type(error).__name__}: {error}",
        }


def collect_market_data(config, timeout=20):
    mappings = config["projects"]
    coingecko = [item for item in mappings if item["provider"] == "coingecko"]
    dex = [item for item in mappings if item["provider"] == "dexscreener"]
    unmapped = [
        {
            "caseId": item["caseId"],
            "provider": "unmapped",
            "status": "skipped",
            "sourceUrl": "",
            "error": item["reason"],
        }
        for item in mappings
        if item["provider"] == "unmapped"
    ]

    market_results = coingecko_results(coingecko, timeout=timeout)
    exit_notional = config["marketAssumptions"]["extremeExitNotionalUsd"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                dexscreener_result,
                item,
                exit_notional,
                timeout,
            )
            for item in dex
        ]
        market_results.extend(future.result() for future in as_completed(futures))
    market_results.extend(unmapped)
    return market_results


def collect_evidence_data(fixture, timeout=20):
    evidence_jobs = [
        (record["caseId"], evidence)
        for record in fixture["records"]
        for evidence in record["evidence"]
    ]
    evidence_results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(check_evidence_link, case_id, evidence, timeout)
            for case_id, evidence in evidence_jobs
        ]
        evidence_results.extend(future.result() for future in as_completed(futures))
    return evidence_results


def collect_refresh_data(config, fixture, timeout=20):
    return (
        collect_market_data(config, timeout=timeout),
        collect_evidence_data(fixture, timeout=timeout),
    )


def formal_market_dependency_stats(bundle):
    """Describe the concrete sources attempted by formal market enrichment."""
    records = bundle.get("records") or []
    contract_results = bundle.get("contractResults") or []
    errors = bundle.get("errors") or []

    def source_errors(provider):
        return [
            str(item.get("error") or "来源运行失败")
            for item in errors
            if item.get("provider") == provider
        ]

    def build(source_id, attempted, matched, failed=0, messages=None):
        if failed and matched:
            status = "partial_success"
        elif failed:
            status = "failed"
        elif matched:
            status = "success"
        else:
            status = "no_data"
        return {
            "sourceId": source_id,
            "status": status,
            "collectedCount": attempted,
            "matchedCount": matched,
            "failedCount": failed,
            "error": "；".join(dict.fromkeys(messages or [])),
        }

    coin_errors = source_errors("coingecko")
    coin_targets = sum(bool(item.get("coinGeckoId")) for item in records)
    coin_matches = sum(
        "market-coingecko" in (item.get("sourceIds") or [])
        for item in records
    )
    dex_errors = source_errors("dexscreener")
    dex_targets = sum(
        bool(
            item.get("asset_id")
            and item.get("contract_address")
            and item.get("networkId")
        )
        for item in records
    )
    dex_matches = sum(
        "market-dexscreener" in (item.get("sourceIds") or [])
        for item in records
    )

    mapping_failures = [
        item
        for item in contract_results
        if item.get("status") == "failed"
    ]
    mapping_matches = len(contract_results) - len(mapping_failures)

    security = {"goplus": [], "robinhood_blockscout": []}
    for item in contract_results:
        expected = (
            "robinhood_blockscout"
            if item.get("networkId") == "robinhood-mainnet"
            else "goplus"
        )
        security[expected].append(item)

    def security_stat(provider, source_id):
        attempted = security[provider]
        matched = sum(
            item.get("provider") == provider and item.get("status") == "success"
            for item in attempted
        )
        failed_items = [
            item
            for item in attempted
            if not (
                item.get("provider") == provider
                and item.get("status") == "success"
            )
        ]
        messages = [
            evidence.get("detail", "来源运行失败")
            for item in failed_items
            for evidence in (item.get("evidence") or [])
            if evidence.get("label") == "合约安全接口"
        ]
        return build(
            source_id,
            len(attempted),
            matched,
            len(failed_items),
            messages,
        )

    return [
        build(
            "market-coingecko",
            coin_targets,
            coin_matches,
            len(coin_errors),
            coin_errors,
        ),
        build(
            "market-dexscreener",
            dex_targets,
            dex_matches,
            len(dex_errors),
            dex_errors,
        ),
        build(
            "contract-identity-mapping",
            len(contract_results),
            mapping_matches,
            len(mapping_failures),
            [item.get("error", "合约身份映射失败") for item in mapping_failures],
        ),
        security_stat("goplus", "security-goplus"),
        security_stat(
            "robinhood_blockscout",
            "chain-robinhood-blockscout",
        ),
    ]


def existing_discovery_bundle(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        records = [
            {
                "discoveryId": row["discovery_id"],
                "networkId": row["network_id"],
                "contractAddress": row["contract_address"],
                "symbol": row["symbol"],
                "tokenName": row["token_name"],
                "discoveryScore": row["discovery_score"],
                "preflightStatus": row["preflight_status"],
            }
            for row in connection.execute(
                """
                SELECT *
                FROM network_discoveries
                WHERE queue_status NOT IN ('rejected', 'promoted')
                ORDER BY discovery_score DESC, last_seen_at DESC
                """
            )
        ]
        return {"records": records, "sourceStats": {}, "errors": []}
    finally:
        connection.close()


def classify_market_grade(result, rulebook):
    if result["status"] != "success":
        return "unknown"
    values = (
        result.get("liquidityUsd"),
        result.get("volume24hUsd"),
        result.get("estimatedExitSlippagePct"),
    )
    if any(value is None for value in values):
        return "unknown"
    standard = rulebook["tradeability"]["standard"]
    if (
        result["liquidityUsd"] >= standard["minimum_liquidity_usd"]
        and result["volume24hUsd"] >= standard["minimum_volume_24h_usd"]
        and result["estimatedExitSlippagePct"] <= standard["maximum_exit_slippage_pct"]
    ):
        return "standard"
    extreme = rulebook["tradeability"]["extreme"]
    if (
        result["liquidityUsd"] >= extreme["minimum_liquidity_usd"]
        and result["volume24hUsd"] >= extreme["minimum_volume_24h_usd"]
        and result["estimatedExitSlippagePct"] <= extreme["maximum_exit_slippage_pct"]
    ):
        return "extreme"
    return "untradeable"


def market_change(previous, current):
    fields = (
        ("price_usd", "priceUsd", "价格"),
        ("liquidity_usd", "liquidityUsd", "流动性"),
        ("volume_24h_usd", "volume24hUsd", "24小时成交额"),
        ("market_cap_usd", "marketCapUsd", "市值"),
        ("fdv_usd", "fdvUsd", "FDV"),
    )
    changes = []
    for old_key, new_key, label in fields:
        old_value = previous[old_key] if previous else None
        new_value = current.get(new_key)
        if new_value is None:
            continue
        if old_value is None:
            changes.append({"field": label, "type": "new", "before": None, "after": new_value})
            continue
        if float(old_value) == float(new_value):
            continue
        pct = None if float(old_value) == 0 else ((float(new_value) - float(old_value)) / float(old_value)) * 100
        changes.append(
            {
                "field": label,
                "type": "changed",
                "before": old_value,
                "after": new_value,
                "changePct": round(pct, 4) if pct is not None else None,
            }
        )
    return changes


def register_sources(connection, now, selected_source_ids=None):
    selected_source_ids = set(selected_source_ids or [])
    definitions = [
        *SOURCE_DEFINITIONS.values(),
        *CONTRACT_SOURCE_DEFINITIONS.values(),
        *HIGH_VALUE_SOURCE_DEFINITIONS.values(),
        *SOURCE_DISCOVERY_DEFINITIONS.values(),
        FORMAL_MARKET_EXIT_SOURCE_DEFINITION,
    ]
    for definition in definitions:
        if selected_source_ids and definition["source_id"] not in selected_source_ids:
            continue
        connection.execute(
            """
            INSERT INTO sources (
              source_id, name, source_type, url, access_method, scope, confidence,
              conflict_risk, status, schedule_text, last_checked_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'convexity', '中', '低', 'active',
                    '手动一键刷新', ?, ?, ?)
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


def latest_market(connection, asset_id):
    if not asset_id:
        return None
    return connection.execute(
        """
        SELECT *
        FROM market_snapshots
        WHERE asset_id = ?
        ORDER BY observed_at DESC, snapshot_id DESC
        LIMIT 1
        """,
        (asset_id,),
    ).fetchone()


def previous_evidence_fingerprint(connection, case_id, source_url):
    row = connection.execute(
        """
        SELECT raw_payload_json
        FROM raw_events
        WHERE event_type = 'evidence_link_check'
          AND project_hint = ?
          AND source_url = ?
        ORDER BY collected_at DESC
        LIMIT 1
        """,
        (case_id, source_url),
    ).fetchone()
    if not row:
        return ""
    return json.loads(row["raw_payload_json"]).get("fingerprint", "")


def persist_refresh(
    connection,
    fixture,
    market_results,
    evidence_results,
    run_id,
    contract_results=None,
    discovery_bundle=None,
    identity_bundle=None,
    project_asset_identity_bundle=None,
    high_value_bundle=None,
    source_discovery_bundle=None,
    formal_market_exit_bundle=None,
    formal_research_materials_bundle=None,
    monitoring_infrastructure_summary=None,
    task_id="full_refresh",
    mode="manual",
    started_at=None,
    duration_ms=0,
):
    now = utc_now()
    started_at = started_at or now
    task = task_definition(task_id)
    selected_components = set(task["components"])
    contract_results = contract_results or []
    discovery_bundle = discovery_bundle or {
        "records": [],
        "sourceStats": {},
        "errors": [],
    }
    identity_bundle = identity_bundle or {
        "records": [],
        "sourceStats": {},
        "errors": [],
    }
    project_asset_identity_bundle = project_asset_identity_bundle or {
        "records": [],
        "errors": [],
        "projectsQueued": 0,
        "registryAssets": 0,
        "protocolRecords": 0,
    }
    high_value_bundle = high_value_bundle or {
        "records": [],
        "sourceStats": {},
        "errors": [],
        "targetVersion": "",
        "coverage": {},
    }
    source_discovery_bundle = source_discovery_bundle or {
        "records": [],
        "sourceStats": {},
        "errors": [],
        "version": "",
    }
    formal_market_exit_bundle = formal_market_exit_bundle or {
        "records": [],
        "contractResults": [],
        "caseRecords": {},
        "errors": [],
        "projectsReviewed": 0,
        "assetsReviewed": 0,
    }
    formal_research_materials_bundle = formal_research_materials_bundle or {
        "projectsReviewed": 0,
        "projects": [],
        "records": [],
        "issues": [],
        "errors": [],
    }
    monitoring_infrastructure_summary = (
        monitoring_infrastructure_summary
        or (
            persist_monitoring_targets(connection, now)
            if "monitoring_infrastructure" in selected_components
            else {
                "projectsReviewed": 0,
                "targetsPublished": 0,
                "recordsInserted": 0,
                "changedTargets": 0,
                "unchangedTargets": 0,
                "retiredTargets": 0,
                "projectsWithTargets": 0,
                "projectsWithReadyTargets": 0,
                "statusCounts": {},
                "typeCounts": {},
                "errors": [],
            }
        )
    )
    rulebook = load_rulebook()
    record_by_case = {item["caseId"]: item for item in fixture["records"]}
    for row in connection.execute(
        """
        SELECT
          cc.case_id,
          cc.project_id,
          cc.asset_id,
          p.canonical_name,
          a.symbol,
          a.chain
        FROM candidate_cases cc
        LEFT JOIN projects p ON p.project_id = cc.project_id
        LEFT JOIN assets a ON a.asset_id = cc.asset_id
        """
    ):
        record_by_case.setdefault(
            row["case_id"],
            {
                "caseId": row["case_id"],
                "projectId": row["project_id"],
                "assetId": row["asset_id"],
                "canonicalName": row["canonical_name"] or row["case_id"],
                "symbol": row["symbol"] or "",
                "chain": row["chain"] or "",
            },
        )
    register_sources(connection, now, task["sourceIds"])

    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at, zero_result_class,
          zero_result_explanation, triggered_by, schema_version
        )
        VALUES (?, ?, ?, 'running', ?, 'none', '', '凸性更新中心', 1)
        """,
        (run_id, task["jobName"], mode, started_at),
    )

    project_results = {}
    errors = []
    market_success = 0
    market_skipped = 0
    market_changed = 0
    for result in market_results:
        record = record_by_case[result["caseId"]]
        project_result = project_results.setdefault(
            result["caseId"],
            {
                "caseId": result["caseId"],
                "projectName": record["canonicalName"],
                "market": None,
                "evidence": [],
                "contracts": [],
            },
        )
        if result["status"] == "skipped":
            market_skipped += 1
            payload = {
                "provider": "unmapped",
                "status": "skipped",
                "summary": result["error"],
                "changes": [],
            }
            connection.execute(
                """
                INSERT INTO raw_events (
                  raw_event_id, source_id, ingestion_run_id, external_id,
                  published_at, collected_at, content_hash, source_url, excerpt,
                  project_hint, asset_hint, chain_hint, event_type, raw_payload_json,
                  status
                )
                VALUES (?, ?, ?, ?, NULL, ?, ?, '', ?, ?, ?, ?,
                        'market_mapping_skip', ?, 'rejected')
                """,
                (
                    stable_id("raw-market-skip", run_id, result["caseId"]),
                    SOURCE_DEFINITIONS["mapping"]["source_id"],
                    run_id,
                    f"{run_id}:{result['caseId']}:market-skip",
                    now,
                    hashlib.sha256(result["error"].encode("utf-8")).hexdigest(),
                    result["error"],
                    result["caseId"],
                    record.get("symbol", ""),
                    record.get("chain", ""),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            project_result["market"] = {
                "status": "skipped",
                "provider": "unmapped",
                "summary": result["error"],
                "sourceUrl": "",
                "changes": [],
            }
            continue
        if result["status"] != "success":
            errors.append(result)
            payload = {
                "provider": result["provider"],
                "status": "failed",
                "summary": result["error"],
                "changes": [],
            }
            connection.execute(
                """
                INSERT INTO raw_events (
                  raw_event_id, source_id, ingestion_run_id, external_id,
                  published_at, collected_at, content_hash, source_url, excerpt,
                  project_hint, asset_hint, chain_hint, event_type, raw_payload_json,
                  status
                )
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?,
                        'market_refresh_error', ?, 'rejected')
                """,
                (
                    stable_id("raw-market-error", run_id, result["caseId"]),
                    SOURCE_DEFINITIONS[result["provider"]]["source_id"],
                    run_id,
                    f"{run_id}:{result['caseId']}:market-error",
                    now,
                    hashlib.sha256(result["error"].encode("utf-8")).hexdigest(),
                    result["sourceUrl"],
                    result["error"],
                    result["caseId"],
                    record.get("symbol", ""),
                    record.get("chain", ""),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            project_result["market"] = {
                "status": "failed",
                "provider": result["provider"],
                "summary": result["error"],
                "sourceUrl": result["sourceUrl"],
                "changes": [],
            }
            continue

        market_success += 1
        asset_id = record.get("assetId")
        previous = latest_market(connection, asset_id)
        changes = market_change(previous, result)
        if changes:
            market_changed += 1
        source_id = SOURCE_DEFINITIONS[result["provider"]]["source_id"]
        venue_id = None
        if result.get("venue") and asset_id:
            venue_id = stable_id(
                "venue",
                asset_id,
                result["venue"]["poolAddress"],
            )
            connection.execute(
                """
                INSERT INTO venues (
                  venue_id, asset_id, venue_name, venue_type, pair_symbol,
                  pool_address, buy_status, sell_status, status, checked_at,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, 'DEX', ?, ?, 'unknown', 'unknown', 'active', ?, ?, ?)
                ON CONFLICT(venue_id) DO UPDATE SET
                  venue_name = excluded.venue_name,
                  pair_symbol = excluded.pair_symbol,
                  checked_at = excluded.checked_at,
                  updated_at = excluded.updated_at
                """,
                (
                    venue_id,
                    asset_id,
                    result["venue"]["name"],
                    result["venue"]["pairSymbol"],
                    result["venue"]["poolAddress"],
                    now,
                    now,
                    now,
                ),
            )

        if asset_id:
            snapshot_id = stable_id("market", run_id, asset_id)
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
                    snapshot_id,
                    asset_id,
                    venue_id,
                    result["observedAt"],
                    result.get("priceUsd"),
                    result.get("liquidityUsd"),
                    result.get("volume24hUsd"),
                    result.get("marketCapUsd"),
                    result.get("fdvUsd"),
                    result.get("circulatingSupply"),
                    result.get("exitNotionalUsd"),
                    result.get("estimatedExitSlippagePct"),
                    source_id,
                    result["definitionNote"],
                ),
            )

        grade = classify_market_grade(result, rulebook)
        if grade != "unknown":
            connection.execute(
                """
                UPDATE candidate_cases
                SET liquidity_grade = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (grade, now, result["caseId"]),
            )

        event_payload = {
            "provider": result["provider"],
            "status": "success",
            "priceUsd": result.get("priceUsd"),
            "liquidityUsd": result.get("liquidityUsd"),
            "volume24hUsd": result.get("volume24hUsd"),
            "marketCapUsd": result.get("marketCapUsd"),
            "fdvUsd": result.get("fdvUsd"),
            "priceChange24hPct": result.get("priceChange24hPct"),
            "estimatedExitSlippagePct": result.get("estimatedExitSlippagePct"),
            "marketGrade": grade,
            "changes": changes,
        }
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url, excerpt,
              project_hint, asset_hint, chain_hint, event_type, raw_payload_json,
              status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'market_snapshot_refresh', ?, 'normalized')
            """,
            (
                stable_id("raw-market", run_id, result["caseId"]),
                source_id,
                run_id,
                f"{run_id}:{result['caseId']}:market",
                result["observedAt"],
                now,
                hashlib.sha256(
                    json.dumps(event_payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                result["sourceUrl"],
                f"价格 {result.get('priceUsd')}；24小时成交额 {result.get('volume24hUsd')}",
                result["caseId"],
                record.get("symbol", ""),
                record.get("chain", ""),
                json.dumps(event_payload, ensure_ascii=False),
            ),
        )
        project_result["market"] = {
            "status": "success",
            "provider": result["provider"],
            "summary": (
                f"价格 {result.get('priceUsd')} 美元；"
                f"24小时成交额 {result.get('volume24hUsd')} 美元"
            ),
            "sourceUrl": result["sourceUrl"],
            "marketGrade": grade,
            "changes": changes,
        }

    contract_summary = (
        persist_contract_checks(
            connection,
            contract_results,
            record_by_case,
            run_id,
            now,
            stable_id,
        )
        if "contracts" in selected_components
        else {
            "success": 0,
            "passed": 0,
            "sellPathsVerified": 0,
            "conflicts": 0,
            "failed": 0,
            "errors": [],
            "byCase": {},
        }
    )
    errors.extend(contract_summary["errors"])
    for case_id, contracts in contract_summary["byCase"].items():
        record = record_by_case[case_id]
        project_result = project_results.setdefault(
            case_id,
            {
                "caseId": case_id,
                "projectName": record["canonicalName"],
                "market": None,
                "evidence": [],
                "contracts": [],
            },
        )
        project_result["contracts"].extend(contracts)

    discovery_summary = (
        persist_network_discoveries(
            connection,
            discovery_bundle,
            run_id,
            stable_id,
        )
        if "discovery" in selected_components
        else {
            "observed": 0,
            "new": 0,
            "preflightPassed": 0,
            "identityPending": 0,
            "existingAssets": 0,
            "rejected": 0,
            "errors": [],
        }
    )
    identity_summary = (
        persist_identity_reviews(
            connection,
            identity_bundle,
            run_id,
            stable_id,
        )
        if "identity" in selected_components
        else {
            "reviewed": 0,
            "corroborated": 0,
            "officialVerified": 0,
            "promoted": 0,
            "pending": 0,
            "rejected": 0,
            "conflicts": 0,
            "existing": 0,
            "errors": [],
        }
    )
    project_asset_identity_summary = (
        persist_machine_project_asset_identities(
            connection,
            project_asset_identity_bundle,
            run_id,
            now,
            stable_id,
        )
        if "project_asset_identity" in selected_components
        else {
            "projectsQueued": 0,
            "projectsReviewed": 0,
            "verified": 0,
            "corroborated": 0,
            "pending": 0,
            "conflicts": 0,
            "assetsCreated": 0,
            "assetsLinked": 0,
            "contractsUpserted": 0,
            "changedProjects": 0,
            "registryAssets": 0,
            "protocolRecords": 0,
            "errors": [],
        }
    )
    profile_enrichment_summary = (
        persist_formal_project_enrichment(
            connection,
            run_id,
            now,
            stable_id,
        )
        if "profile_enrichment" in selected_components
        else {
            "projectsReviewed": 0,
            "identityVerified": 0,
            "anchorsAdded": 0,
            "websiteAdded": 0,
            "socialAdded": 0,
            "repositoryAdded": 0,
            "remainingIdentityPending": 0,
            "changedProjects": 0,
        }
    )
    formal_market_exit_summary = (
        persist_formal_market_exit(
            connection,
            formal_market_exit_bundle,
            run_id,
            now,
            stable_id,
        )
        if "formal_market_exit" in selected_components
        else {
            "projectsReviewed": 0,
            "assetsReviewed": 0,
            "marketCoveredProjects": 0,
            "exitCoveredProjects": 0,
            "pendingProjects": 0,
            "changedProjects": 0,
            "contractChecks": 0,
            "sellPathsVerified": 0,
            "contractPassed": 0,
            "failed": 0,
            "errors": [],
            "projects": [],
        }
    )
    formal_research_materials_summary = (
        persist_formal_research_materials(
            connection,
            formal_research_materials_bundle,
            run_id,
            now,
        )
        if "formal_research_materials" in selected_components
        else {
            "projectsReviewed": 0,
            "recordsCollected": 0,
            "recordsAdded": 0,
            "duplicateRecords": 0,
            "projectsMatched": 0,
            "changedProjects": 0,
            "accessIssues": 0,
            "pendingProjects": 0,
            "documentsCovered": 0,
            "tokenomicsCovered": 0,
            "teamCovered": 0,
            "auditCovered": 0,
            "errors": [],
            "projects": [],
        }
    )

    evidence_success = 0
    evidence_restricted = 0
    evidence_failed = 0
    evidence_changed = 0
    evidence_source_id = SOURCE_DEFINITIONS["evidence"]["source_id"]
    for result in evidence_results:
        record = record_by_case[result["caseId"]]
        project_result = project_results.setdefault(
            result["caseId"],
            {
                "caseId": result["caseId"],
                "projectName": record["canonicalName"],
                "market": None,
                "evidence": [],
                "contracts": [],
            },
        )
        previous_fingerprint = previous_evidence_fingerprint(
            connection, result["caseId"], result["sourceUrl"]
        )
        changed = bool(
            result.get("fingerprint")
            and previous_fingerprint
            and result["fingerprint"] != previous_fingerprint
        )
        if changed:
            evidence_changed += 1
        if result["status"] == "success":
            evidence_success += 1
        elif result["status"] == "restricted":
            evidence_restricted += 1
        else:
            evidence_failed += 1
            errors.append(result)

        payload = {
            "status": result["status"],
            "httpStatus": result.get("httpStatus"),
            "fingerprint": result.get("fingerprint", ""),
            "changed": changed,
            "summary": result["summary"],
            "error": result.get("error", ""),
        }
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url, excerpt,
              project_hint, asset_hint, chain_hint, event_type, raw_payload_json,
              status
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'evidence_link_check', ?, 'normalized')
            """,
            (
                stable_id("raw-evidence", run_id, result["caseId"], result["sourceUrl"]),
                evidence_source_id,
                run_id,
                f"{run_id}:{result['caseId']}:{stable_id('url', result['sourceUrl'])}",
                now,
                hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                result["sourceUrl"],
                result["summary"],
                result["caseId"],
                record.get("symbol", ""),
                record.get("chain", ""),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        project_result["evidence"].append(
            {
                "status": result["status"],
                "summary": result["summary"],
                "sourceUrl": result["sourceUrl"],
                "httpStatus": result.get("httpStatus"),
                "changed": changed,
            }
        )

    high_value_summary = (
        persist_high_value_sources(
            connection,
            high_value_bundle,
            run_id,
            now,
            stable_id,
            record_by_case,
        )
        if "high_value_evidence" in selected_components
        else {
            "collected": 0,
            "normalized": 0,
            "matched": 0,
            "filtered": 0,
            "failed": 0,
            "changed": 0,
            "duplicates": 0,
            "projectsReviewed": 0,
            "verifiedProjects": 0,
            "identityBlocked": 0,
            "githubTargets": 0,
            "defillamaTargets": 0,
            "snapshotTargets": 0,
            "cactusTargets": 0,
        }
    )
    source_discovery_summary = (
        persist_source_discoveries(
            connection,
            source_discovery_bundle,
            run_id,
            now,
            stable_id,
        )
        if "source_discovery" in selected_components
        else {
            "collected": 0,
            "inserted": 0,
            "updated": 0,
            "matchedExisting": 0,
            "corroboratedClusters": 0,
            "pendingClusters": 0,
            "conflictClusters": 0,
            "autoPromotedProjects": 0,
            "autoCreatedCases": 0,
            "autoLinkedRecords": 0,
            "autoEligibleClusters": 0,
            "autoSkippedClusters": 0,
            "failed": 0,
            "errors": [],
        }
    )
    weak_signal_summary = (
        persist_weak_signals(connection, now)
        if "weak_signals" in selected_components
        else {
            "signalsPublished": 0,
            "recordsInserted": 0,
            "changedSignals": 0,
            "unchangedSignals": 0,
            "retiredSignals": 0,
            "projectsLinked": 0,
            "triageCounts": {},
            "sourceCounts": {},
            "signalTypeCounts": {},
            "errors": [],
        }
    )
    machine_scoring_summary = (
        persist_machine_research_scores(
            connection,
            run_id,
            now,
            stable_id,
        )
        if "machine_research_scoring" in selected_components
        else {
            "projectsScored": 0,
            "highConfidence": 0,
            "mediumConfidence": 0,
            "lowConfidence": 0,
            "insufficient": 0,
            "mismatchAbove65": 0,
            "readinessAbove65": 0,
            "changedProjects": 0,
            "lifecycleCounts": {"early": 0, "og": 0, "other": 0},
            "errors": [],
        }
    )
    machine_conclusion_summary = (
        persist_machine_conclusions(
            connection,
            run_id,
            now,
            stable_id,
        )
        if "machine_conclusion" in selected_components
        else {
            "projectsPublished": 0,
            "changedProjects": 0,
            "stateCounts": {
                "identity_pending": 0,
                "asset_pending": 0,
                "market_exit_pending": 0,
                "evidence_building": 0,
                "convexity_structure_pending": 0,
                "priority_watch": 0,
                "actionable": 0,
                "reflexive": 0,
                "invalidated": 0,
            },
            "actionCounts": {
                "ordinary": 0,
                "extreme": 0,
                "observe": 0,
                "reflexive": 0,
                "invalidated": 0,
            },
            "missingScores": 0,
            "errors": [],
        }
    )
    catalyst_path_summary = (
        persist_catalyst_trade_paths(
            connection,
            run_id,
            now,
            stable_id,
        )
        if "catalyst_trade_path" in selected_components
        else {
            "projectsProcessed": 0,
            "recordsInserted": 0,
            "changedProjects": 0,
            "withCatalyst": 0,
            "withAsset": 0,
            "exitModeled": 0,
            "stageCounts": {},
            "exitThresholdPct": 0,
            "modeledExitNotionalUsd": 20000,
            "errors": [],
        }
    )

    for error in errors:
        connection.execute(
            """
            INSERT INTO run_errors (
              error_id, run_id, source_id, task_name, error_type, message,
              retryable, retry_status, attempts, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, 'source_error', ?, 1, 'not_requested', 1, ?, ?)
            """,
            (
                stable_id("error", run_id, error["provider"], error["caseId"], error.get("sourceUrl")),
                run_id,
                (
                    SOURCE_DEFINITIONS.get(error["provider"])
                    or CONTRACT_SOURCE_DEFINITIONS.get(error["provider"])
                    or SOURCE_DEFINITIONS["mapping"]
                )["source_id"],
                f"{record_by_case[error['caseId']]['canonicalName']} · {error['provider']}",
                error.get("error", "未知错误"),
                now,
                now,
            ),
        )

    for error in discovery_summary["errors"]:
        provider = error["provider"]
        source = DISCOVERY_SOURCE_DEFINITIONS.get(provider)
        source_id = (
            source["source_id"]
            if source
            else DISCOVERY_SOURCE_DEFINITIONS["dexscreener_profiles"]["source_id"]
        )
        connection.execute(
            """
            INSERT INTO run_errors (
              error_id, run_id, source_id, task_name, error_type, message,
              retryable, retry_status, attempts, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, 'source_error', ?, 1, 'not_requested', 1, ?, ?)
            """,
            (
                stable_id("discovery-error", run_id, provider, error["sourceUrl"]),
                run_id,
                source_id,
                f"常用链发现 · {source['name'] if source else provider}",
                error["error"],
                now,
                now,
            ),
        )

    for error in identity_summary["errors"]:
        connection.execute(
            """
            INSERT INTO run_errors (
              error_id, run_id, source_id, task_name, error_type, message,
              retryable, retry_status, attempts, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, '发现身份自动复核', 'source_error', ?, 1,
                    'not_requested', 1, ?, ?)
            """,
            (
                stable_id(
                    "identity-error",
                    run_id,
                    error.get("discoveryId", ""),
                    error["error"],
                ),
                run_id,
                IDENTITY_SOURCE_DEFINITION["source_id"],
                error["error"],
                now,
                now,
            ),
        )

    stats = {
        "coingecko": [item for item in market_results if item["provider"] == "coingecko"],
        "dexscreener": [item for item in market_results if item["provider"] == "dexscreener"],
        "mapping": [item for item in market_results if item["provider"] == "unmapped"],
        "evidence": evidence_results,
        "goplus": [item for item in contract_results if item["provider"] == "goplus"],
        "robinhood_blockscout": [
            item
            for item in contract_results
            if item["provider"] == "robinhood_blockscout"
        ],
        "contract_mapping": [
            item
            for item in contract_results
            if item["provider"] == "contract_mapping"
        ],
    }
    for provider, items in stats.items():
        if not items:
            continue
        success_count = sum(item["status"] == "success" for item in items)
        failed_count = sum(item["status"] == "failed" for item in items)
        skipped_count = sum(
            item["status"] in ("skipped", "restricted", "conflict")
            for item in items
        )
        status = (
            "failed"
            if items and failed_count == len(items)
            else "partial_success"
            if failed_count or skipped_count
            else "success"
        )
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, provider),
                run_id,
                (
                    SOURCE_DEFINITIONS.get(provider)
                    or CONTRACT_SOURCE_DEFINITIONS[provider]
                )["source_id"],
                provider,
                status,
                now,
                now,
                len(items),
                success_count,
                skipped_count,
                failed_count,
                json.dumps(
                    {
                        "restricted": sum(
                            item["status"] == "restricted" for item in items
                        ),
                        "unmapped": sum(item["status"] == "skipped" for item in items),
                        "identityConflict": sum(
                            item["status"] == "conflict" for item in items
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "formal_market_exit" in selected_components:
        for source_stat in formal_market_dependency_stats(
            formal_market_exit_bundle
        ):
            connection.execute(
                """
                INSERT OR REPLACE INTO run_source_stats (
                  run_source_stat_id, run_id, source_id, collector_id, status,
                  started_at, finished_at, collected_count, matched_count,
                  filtered_count, failed_count, filter_reason_summary_json,
                  error_message
                )
                VALUES (?, ?, ?, 'formal_market_dependency', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id(
                        "run-source",
                        run_id,
                        source_stat["sourceId"],
                    ),
                    run_id,
                    source_stat["sourceId"],
                    source_stat["status"],
                    now,
                    now,
                    source_stat["collectedCount"],
                    source_stat["matchedCount"],
                    max(
                        0,
                        source_stat["collectedCount"]
                        - source_stat["matchedCount"]
                        - source_stat["failedCount"],
                    ),
                    source_stat["failedCount"],
                    json.dumps(
                        {
                            "boundary": (
                                "记录正式市场与退出资料实际调用的依赖来源；"
                                "不改变交易性、风险或行动结论"
                            )
                        },
                        ensure_ascii=False,
                    ),
                    source_stat["error"],
                ),
            )

    for provider, source_stat in discovery_bundle["sourceStats"].items():
        definition = DISCOVERY_SOURCE_DEFINITIONS[provider]
        status = "failed" if source_stat["failed"] else "success"
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, provider),
                run_id,
                definition["source_id"],
                provider,
                status,
                now,
                now,
                source_stat["collected"],
                source_stat["accepted"],
                max(0, source_stat["collected"] - source_stat["accepted"]),
                source_stat["failed"],
                json.dumps(
                    {
                        "boundary": "仅用于发现，不直接产生投资结论",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    for provider, source_stat in identity_bundle["sourceStats"].items():
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, provider),
                run_id,
                IDENTITY_SOURCE_DEFINITION["source_id"],
                provider,
                "partial_success" if source_stat["failed"] else "success",
                now,
                now,
                source_stat["collected"],
                source_stat["accepted"],
                source_stat["filtered"],
                source_stat["failed"],
                json.dumps(
                    {
                        "boundary": "只允许升格到影子研究库，不产生投资结论",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "project_asset_identity" in selected_components:
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'machine_project_asset_identity', ?,
                    ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, "machine_project_asset_identity"),
                run_id,
                PROJECT_ASSET_IDENTITY_SOURCE_DEFINITION["source_id"],
                (
                    "partial_success"
                    if project_asset_identity_summary["errors"]
                    else "success"
                ),
                now,
                now,
                project_asset_identity_summary["projectsQueued"],
                (
                    project_asset_identity_summary["verified"]
                    + project_asset_identity_summary["corroborated"]
                ),
                (
                    project_asset_identity_summary["pending"]
                    + project_asset_identity_summary["conflicts"]
                ),
                len(project_asset_identity_summary["errors"]),
                json.dumps(
                    {
                        "verified": project_asset_identity_summary["verified"],
                        "corroborated": project_asset_identity_summary[
                            "corroborated"
                        ],
                        "pending": project_asset_identity_summary["pending"],
                        "conflicts": project_asset_identity_summary["conflicts"],
                        "boundary": (
                            "只补齐资产身份与基础档案，"
                            "不产生凸性评分或行动建议"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "profile_enrichment" in selected_components:
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'formal_project_profile_enrichment', 'success',
                    ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                stable_id("run-source", run_id, "formal_project_profile_enrichment"),
                run_id,
                PROFILE_ENRICHMENT_SOURCE_DEFINITION["source_id"],
                now,
                now,
                profile_enrichment_summary["projectsReviewed"],
                profile_enrichment_summary["changedProjects"],
                profile_enrichment_summary["remainingIdentityPending"],
                json.dumps(
                    {
                        "identityVerified": profile_enrichment_summary[
                            "identityVerified"
                        ],
                        "anchorsAdded": profile_enrichment_summary["anchorsAdded"],
                        "boundary": (
                            "只补齐正式项目身份与官方入口，"
                            "不产生凸性评分或行动建议"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "formal_market_exit" in selected_components:
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'formal_project_market_exit_enrichment', ?,
                    ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id(
                    "run-source",
                    run_id,
                    "formal_project_market_exit_enrichment",
                ),
                run_id,
                FORMAL_MARKET_EXIT_SOURCE_DEFINITION["source_id"],
                (
                    "partial_success"
                    if formal_market_exit_summary["failed"]
                    else "success"
                ),
                now,
                now,
                formal_market_exit_summary["projectsReviewed"],
                formal_market_exit_summary["marketCoveredProjects"],
                formal_market_exit_summary["pendingProjects"],
                formal_market_exit_summary["failed"],
                json.dumps(
                    {
                        "assetsReviewed": formal_market_exit_summary[
                            "assetsReviewed"
                        ],
                        "exitCoveredProjects": formal_market_exit_summary[
                            "exitCoveredProjects"
                        ],
                        "contractChecks": formal_market_exit_summary[
                            "contractChecks"
                        ],
                        "sellPathsVerified": formal_market_exit_summary[
                            "sellPathsVerified"
                        ],
                        "boundary": (
                            "单池流动性与公式滑点只用于初筛，"
                            "不等于真实卖出，也不直接产生行动建议"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "formal_research_materials" in selected_components:
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, duplicate_count,
              matched_count, filtered_count, failed_count,
              filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'formal_project_research_materials', ?,
                    ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                stable_id(
                    "run-source",
                    run_id,
                    "formal_project_research_materials",
                ),
                run_id,
                FORMAL_RESEARCH_MATERIALS_SOURCE_DEFINITION["source_id"],
                (
                    "success"
                    if formal_research_materials_summary["recordsCollected"]
                    else "no_data"
                ),
                now,
                now,
                formal_research_materials_summary["projectsReviewed"],
                formal_research_materials_summary["duplicateRecords"],
                formal_research_materials_summary["projectsMatched"],
                formal_research_materials_summary["pendingProjects"],
                json.dumps(
                    {
                        "recordsCollected": formal_research_materials_summary[
                            "recordsCollected"
                        ],
                        "recordsAdded": formal_research_materials_summary[
                            "recordsAdded"
                        ],
                        "documentsCovered": formal_research_materials_summary[
                            "documentsCovered"
                        ],
                        "tokenomicsCovered": formal_research_materials_summary[
                            "tokenomicsCovered"
                        ],
                        "teamCovered": formal_research_materials_summary[
                            "teamCovered"
                        ],
                        "auditCovered": formal_research_materials_summary[
                            "auditCovered"
                        ],
                        "accessIssues": formal_research_materials_summary[
                            "accessIssues"
                        ],
                        "boundary": (
                            "仅记录官网和认证GitHub中的研究资料入口；"
                            "项目方陈述不自动成为投资、安全或团队实名结论"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "machine_research_scoring" in selected_components:
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'machine_research_scoring', ?,
                    ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, "machine_research_scoring"),
                run_id,
                MACHINE_RESEARCH_SCORING_SOURCE_DEFINITION["source_id"],
                (
                    "partial_success"
                    if machine_scoring_summary["errors"]
                    else "success"
                ),
                now,
                now,
                machine_scoring_summary["projectsScored"],
                machine_scoring_summary["changedProjects"],
                machine_scoring_summary["insufficient"],
                len(machine_scoring_summary["errors"]),
                json.dumps(
                    {
                        "highConfidence": machine_scoring_summary[
                            "highConfidence"
                        ],
                        "mediumConfidence": machine_scoring_summary[
                            "mediumConfidence"
                        ],
                        "lowConfidence": machine_scoring_summary["lowConfidence"],
                        "mismatchAbove65": machine_scoring_summary[
                            "mismatchAbove65"
                        ],
                        "readinessAbove65": machine_scoring_summary[
                            "readinessAbove65"
                        ],
                        "lifecycleCounts": machine_scoring_summary[
                            "lifecycleCounts"
                        ],
                        "boundary": (
                            "三个分数只用于资料质量、研究排序和凸性闭环准备度；"
                            "不直接改变当前动作，也不构成买卖建议"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "machine_conclusion" in selected_components:
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'machine_conclusion', ?,
                    ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, "machine_conclusion"),
                run_id,
                MACHINE_CONCLUSION_SOURCE_DEFINITION["source_id"],
                (
                    "partial_success"
                    if machine_conclusion_summary["errors"]
                    else "success"
                ),
                now,
                now,
                machine_conclusion_summary["projectsPublished"],
                machine_conclusion_summary["changedProjects"],
                machine_conclusion_summary["missingScores"],
                len(machine_conclusion_summary["errors"]),
                json.dumps(
                    {
                        "stateCounts": machine_conclusion_summary[
                            "stateCounts"
                        ],
                        "actionCounts": machine_conclusion_summary[
                            "actionCounts"
                        ],
                        "missingScores": machine_conclusion_summary[
                            "missingScores"
                        ],
                        "boundary": (
                            "评分只决定研究顺序；只有身份、证据、风险、"
                            "交易性和凸性结构硬门槛同时通过才允许发布行动结论。"
                            "人工复核可介入但不阻断发布"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "catalyst_trade_path" in selected_components:
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'catalyst_trade_path', ?,
                    ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, "catalyst_trade_path"),
                run_id,
                CATALYST_PATH_SOURCE_DEFINITION["source_id"],
                (
                    "partial_success"
                    if catalyst_path_summary["errors"]
                    else "success"
                ),
                now,
                now,
                catalyst_path_summary["projectsProcessed"],
                catalyst_path_summary["recordsInserted"],
                sum(
                    count
                    for stage, count in catalyst_path_summary["stageCounts"].items()
                    if stage not in ("research_ready", "action_ready")
                ),
                len(catalyst_path_summary["errors"]),
                json.dumps(
                    {
                        "stageCounts": catalyst_path_summary["stageCounts"],
                        "changedProjects": catalyst_path_summary[
                            "changedProjects"
                        ],
                        "modeledExitNotionalUsd": catalyst_path_summary[
                            "modeledExitNotionalUsd"
                        ],
                        "exitThresholdPct": catalyst_path_summary[
                            "exitThresholdPct"
                        ],
                        "boundary": (
                            "2万美元退出滑点为恒定乘积理论估算，"
                            "与只读卖出路径实际核验金额分开保存；系统不自动交易"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "monitoring_infrastructure" in selected_components:
        blocked_targets = (
            monitoring_infrastructure_summary["statusCounts"].get(
                "blocked",
                0,
            )
            + monitoring_infrastructure_summary["statusCounts"].get(
                "conflict",
                0,
            )
        )
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'monitoring_infrastructure', 'success',
                    ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                stable_id(
                    "run-source",
                    run_id,
                    "monitoring_infrastructure",
                ),
                run_id,
                MONITORING_INFRASTRUCTURE_SOURCE_DEFINITION["source_id"],
                now,
                now,
                monitoring_infrastructure_summary["projectsReviewed"],
                (
                    monitoring_infrastructure_summary["recordsInserted"]
                    + monitoring_infrastructure_summary["changedTargets"]
                ),
                blocked_targets,
                json.dumps(
                    {
                        "targetsPublished": (
                            monitoring_infrastructure_summary[
                                "targetsPublished"
                            ]
                        ),
                        "projectsWithReadyTargets": (
                            monitoring_infrastructure_summary[
                                "projectsWithReadyTargets"
                            ]
                        ),
                        "statusCounts": monitoring_infrastructure_summary[
                            "statusCounts"
                        ],
                        "typeCounts": monitoring_infrastructure_summary[
                            "typeCounts"
                        ],
                        "boundary": (
                            "监控目标登记不等于信源事实、凸性成立或行动结论；"
                            "身份和来源归属未通过的目标不会进入自动采集"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    if "weak_signals" in selected_components:
        blocked_signals = (
            weak_signal_summary["triageCounts"].get("identity_blocked", 0)
            + weak_signal_summary["triageCounts"].get("conflict", 0)
            + weak_signal_summary["triageCounts"].get("discovery_only", 0)
        )
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, 'weak_signal_inbox', 'success',
                    ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                stable_id("run-source", run_id, "weak_signal_inbox"),
                run_id,
                WEAK_SIGNAL_SOURCE_DEFINITION["source_id"],
                now,
                now,
                weak_signal_summary["signalsPublished"],
                weak_signal_summary["triageCounts"].get(
                    "ready_for_corroboration",
                    0,
                ),
                blocked_signals,
                json.dumps(
                    {
                        "recordsInserted": weak_signal_summary[
                            "recordsInserted"
                        ],
                        "changedSignals": weak_signal_summary[
                            "changedSignals"
                        ],
                        "projectsLinked": weak_signal_summary[
                            "projectsLinked"
                        ],
                        "triageCounts": weak_signal_summary["triageCounts"],
                        "sourceCounts": weak_signal_summary["sourceCounts"],
                        "boundary": (
                            "弱线索只扩大召回和安排补证，"
                            "不直接参与评分、结论或行动"
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    total_errors = (
        len(errors)
        + len(discovery_summary["errors"])
        + len(identity_summary["errors"])
        + len(project_asset_identity_summary["errors"])
        + high_value_summary["failed"]
        + source_discovery_summary["failed"]
        + formal_market_exit_summary["failed"]
        + len(formal_research_materials_summary["errors"])
        + len(machine_scoring_summary["errors"])
        + len(machine_conclusion_summary["errors"])
        + len(catalyst_path_summary["errors"])
        + len(monitoring_infrastructure_summary["errors"])
        + len(weak_signal_summary["errors"])
    )
    result_count = (
        market_success
        + evidence_success
        + contract_summary["success"]
        + discovery_summary["observed"]
        + identity_summary["reviewed"]
        + project_asset_identity_summary["projectsReviewed"]
        + profile_enrichment_summary["changedProjects"]
        + formal_market_exit_summary["marketCoveredProjects"]
        + formal_research_materials_summary["recordsCollected"]
        + high_value_summary["matched"]
        + source_discovery_summary["collected"]
        + machine_scoring_summary["projectsScored"]
        + machine_conclusion_summary["projectsPublished"]
        + catalyst_path_summary["projectsProcessed"]
        + monitoring_infrastructure_summary["targetsPublished"]
        + weak_signal_summary["signalsPublished"]
    )
    status = (
        "failed"
        if total_errors and result_count == 0
        else "partial_success"
        if total_errors
        else "success"
    )
    explanation_parts = []
    if (
        "market" in selected_components
        and "formal_market_exit" not in selected_components
    ):
        explanation_parts.append(
            f"行情成功 {market_success}，未映射 {market_skipped}，指标变化 {market_changed}"
        )
    if "evidence" in selected_components:
        explanation_parts.append(
            f"证据可访问 {evidence_success}，受限 {evidence_restricted}，"
            f"失败 {evidence_failed}，内容变化 {evidence_changed}"
        )
    if "high_value_evidence" in selected_components:
        explanation_parts.append(
            f"正式项目 {high_value_summary['projectsReviewed']}，"
            f"身份已核验 {high_value_summary['verifiedProjects']}，"
            f"持续证据匹配项目 {high_value_summary['matched']}，"
            f"采集 {high_value_summary['collected']}，"
            f"新增 {high_value_summary['normalized']}，"
            f"重复跳过 {high_value_summary['duplicates']}，"
            f"指标变化 {high_value_summary['changed']}，"
            f"失败 {high_value_summary['failed']}"
        )
    if "source_discovery" in selected_components:
        explanation_parts.append(
            f"项目级发现 {source_discovery_summary['collected']}，"
            f"新增 {source_discovery_summary['inserted']}，"
            f"匹配已有项目 {source_discovery_summary['matchedExisting']}，"
            f"跨源印证组 {source_discovery_summary['corroboratedClusters']}，"
            f"待核验组 {source_discovery_summary['pendingClusters']}，"
            f"机器建档 {source_discovery_summary['autoPromotedProjects']}，"
            f"创建观察档案 {source_discovery_summary['autoCreatedCases']}，"
            f"失败来源 {source_discovery_summary['failed']}"
        )
    if (
        "contracts" in selected_components
        and "formal_market_exit" not in selected_components
    ):
        explanation_parts.append(
            f"合约核验 {contract_summary['success']}，完整通过 {contract_summary['passed']}，"
            f"卖出路径通过 {contract_summary['sellPathsVerified']}，"
            f"身份冲突 {contract_summary['conflicts']}，失败 {contract_summary['failed']}"
        )
    if "discovery" in selected_components:
        explanation_parts.append(
            f"常用链发现 {discovery_summary['observed']}，新增 {discovery_summary['new']}，"
            f"技术预检通过 {discovery_summary['preflightPassed']}，"
            f"待身份核验 {discovery_summary['identityPending']}，"
            f"范围拦截 {discovery_summary['rejected']}"
        )
    if "identity" in selected_components:
        explanation_parts.append(
            f"身份复核 {identity_summary['reviewed']}，独立登记吻合 "
            f"{identity_summary['corroborated']}，官网确认 "
            f"{identity_summary['officialVerified']}，影子库升格 "
            f"{identity_summary['promoted']}，待补证 {identity_summary['pending']}，"
            f"冲突或排除 {identity_summary['conflicts'] + identity_summary['rejected']}"
        )
    if "profile_enrichment" in selected_components:
        explanation_parts.append(
            f"正式项目核验 {profile_enrichment_summary['projectsReviewed']}，"
            f"解除身份阻断 {profile_enrichment_summary['identityVerified']}，"
            f"新增官方入口证据 {profile_enrichment_summary['anchorsAdded']}，"
            f"仍待身份补证 {profile_enrichment_summary['remainingIdentityPending']}"
        )
    if "project_asset_identity" in selected_components:
        explanation_parts.append(
            f"机器项目入队 {project_asset_identity_summary['projectsQueued']}，"
            f"资产核验通过 {project_asset_identity_summary['verified']}，"
            f"交叉印证 {project_asset_identity_summary['corroborated']}，"
            f"新建资产 {project_asset_identity_summary['assetsCreated']}，"
            f"登记合约 {project_asset_identity_summary['contractsUpserted']}，"
            f"保留缺口 {project_asset_identity_summary['pending']}，"
            f"身份冲突 {project_asset_identity_summary['conflicts']}"
        )
    if "formal_market_exit" in selected_components:
        explanation_parts.append(
            f"正式项目复核 {formal_market_exit_summary['projectsReviewed']}，"
            f"取得市场资料 {formal_market_exit_summary['marketCoveredProjects']}，"
            f"取得退出估算 {formal_market_exit_summary['exitCoveredProjects']}，"
            f"只读卖出路径通过 {formal_market_exit_summary['sellPathsVerified']}，"
            f"仍待市场资料 {formal_market_exit_summary['pendingProjects']}"
        )
    if "formal_research_materials" in selected_components:
        explanation_parts.append(
            f"正式项目扫描 {formal_research_materials_summary['projectsReviewed']}，"
            f"产品文档覆盖 {formal_research_materials_summary['documentsCovered']}，"
            f"代币经济覆盖 {formal_research_materials_summary['tokenomicsCovered']}，"
            f"团队组织覆盖 {formal_research_materials_summary['teamCovered']}，"
            f"审计安全覆盖 {formal_research_materials_summary['auditCovered']}，"
            f"仍无目标资料 {formal_research_materials_summary['pendingProjects']}"
        )
    if "machine_research_scoring" in selected_components:
        explanation_parts.append(
            f"机器评分 {machine_scoring_summary['projectsScored']}，"
            f"证据置信度高/中/低/不足 "
            f"{machine_scoring_summary['highConfidence']}/"
            f"{machine_scoring_summary['mediumConfidence']}/"
            f"{machine_scoring_summary['lowConfidence']}/"
            f"{machine_scoring_summary['insufficient']}，"
            f"错配 65 分以上 {machine_scoring_summary['mismatchAbove65']}，"
            f"凸性准备度 65 分以上 {machine_scoring_summary['readinessAbove65']}，"
            f"本次变化 {machine_scoring_summary['changedProjects']}；"
            f"评分不改变当前动作"
        )
    if "machine_conclusion" in selected_components:
        state_counts = machine_conclusion_summary["stateCounts"]
        action_counts = machine_conclusion_summary["actionCounts"]
        explanation_parts.append(
            f"机器结论发布 {machine_conclusion_summary['projectsPublished']}，"
            f"项目主体待核验 {state_counts['identity_pending']}，"
            f"资产待核验 {state_counts['asset_pending']}，"
            f"市场与退出待闭环 {state_counts['market_exit_pending']}，"
            f"证据积累 {state_counts['evidence_building']}，"
            f"凸性结构待闭环 {state_counts['convexity_structure_pending']}，"
            f"重点跟踪 {state_counts['priority_watch']}，"
            f"可行动 {state_counts['actionable']}，"
            f"只观察 {action_counts['observe']}，"
            f"本次变化 {machine_conclusion_summary['changedProjects']}；"
            f"人工复核不阻断结论发布"
        )
    if "catalyst_trade_path" in selected_components:
        stage_counts = catalyst_path_summary["stageCounts"]
        explanation_parts.append(
            f"催化交易路径 {catalyst_path_summary['projectsProcessed']}，"
            f"发现候选催化 {catalyst_path_summary['withCatalyst']}，"
            f"价值传导待闭环 {stage_counts.get('transmission_pending', 0)}，"
            f"2万美元退出待闭环 {stage_counts.get('exit_pending', 0)}，"
            f"研究路径闭环 {stage_counts.get('research_ready', 0)}，"
            f"行动路径闭环 {stage_counts.get('action_ready', 0)}；"
            f"理论滑点与实际核验金额分开显示"
        )
    if "monitoring_infrastructure" in selected_components:
        status_counts = monitoring_infrastructure_summary["statusCounts"]
        explanation_parts.append(
            f"监控基础设施覆盖项目 "
            f"{monitoring_infrastructure_summary['projectsReviewed']}，"
            f"当前目标 {monitoring_infrastructure_summary['targetsPublished']}，"
            f"可自动采集 {status_counts.get('ready', 0)}，"
            f"已登记待适配 {status_counts.get('registered', 0)}，"
            f"身份阻断 {status_counts.get('blocked', 0)}，"
            f"归属冲突 {status_counts.get('conflict', 0)}，"
            f"本次新增 {monitoring_infrastructure_summary['recordsInserted']}，"
            f"变化 {monitoring_infrastructure_summary['changedTargets']}"
        )
    if "weak_signals" in selected_components:
        triage_counts = weak_signal_summary["triageCounts"]
        explanation_parts.append(
            f"弱线索归类 {weak_signal_summary['signalsPublished']}，"
            f"可进入补证 {triage_counts.get('ready_for_corroboration', 0)}，"
            f"仅供发现 {triage_counts.get('discovery_only', 0)}，"
            f"身份待核验 {triage_counts.get('identity_blocked', 0)}，"
            f"归属冲突 {triage_counts.get('conflict', 0)}，"
            f"本次新增 {weak_signal_summary['recordsInserted']}，"
            f"变化 {weak_signal_summary['changedSignals']}；"
            f"不直接改变评分、结论或行动"
        )
    explanation = "；".join(explanation_parts) + "。"
    zero_result_class = (
        "source_failure"
        if total_errors and result_count == 0
        else "source_returned_no_data"
        if result_count == 0
        else "none"
    )
    collected_total = (
        len(market_results)
        + len(evidence_results)
        + len(contract_results)
        + discovery_summary["observed"]
        + identity_summary["reviewed"]
        + project_asset_identity_summary["projectsReviewed"]
        + profile_enrichment_summary["projectsReviewed"]
        + formal_market_exit_summary["projectsReviewed"]
        + formal_research_materials_summary["projectsReviewed"]
        + high_value_summary["collected"]
        + source_discovery_summary["collected"]
        + machine_scoring_summary["projectsScored"]
        + machine_conclusion_summary["projectsPublished"]
        + catalyst_path_summary["projectsProcessed"]
        + monitoring_infrastructure_summary["projectsReviewed"]
        + weak_signal_summary["signalsPublished"]
    )
    normalized_total = (
        market_success
        + evidence_success
        + contract_summary["success"]
        + discovery_summary["observed"]
        + identity_summary["reviewed"]
        + project_asset_identity_summary["assetsLinked"]
        + profile_enrichment_summary["changedProjects"]
        + formal_market_exit_summary["marketCoveredProjects"]
        + formal_research_materials_summary["recordsCollected"]
        + high_value_summary["normalized"]
        + source_discovery_summary["collected"]
        + machine_scoring_summary["projectsScored"]
        + machine_conclusion_summary["projectsPublished"]
        + catalyst_path_summary["projectsProcessed"]
        + monitoring_infrastructure_summary["targetsPublished"]
        + weak_signal_summary["signalsPublished"]
    )
    matched_total = (
        market_success
        + contract_summary["success"]
        + identity_summary["corroborated"]
        + identity_summary["officialVerified"]
        + project_asset_identity_summary["assetsLinked"]
        + profile_enrichment_summary["changedProjects"]
        + formal_market_exit_summary["marketCoveredProjects"]
        + formal_research_materials_summary["recordsCollected"]
        + high_value_summary["matched"]
        + source_discovery_summary["matchedExisting"]
        + source_discovery_summary["corroboratedClusters"]
        + machine_scoring_summary["changedProjects"]
        + machine_conclusion_summary["changedProjects"]
        + catalyst_path_summary["changedProjects"]
        + monitoring_infrastructure_summary["changedTargets"]
        + monitoring_infrastructure_summary["recordsInserted"]
        + weak_signal_summary["triageCounts"].get(
            "ready_for_corroboration",
            0,
        )
    )
    filtered_total = (
        market_skipped
        + evidence_restricted
        + discovery_summary["rejected"]
        + identity_summary["rejected"]
        + identity_summary["conflicts"]
        + project_asset_identity_summary["pending"]
        + project_asset_identity_summary["conflicts"]
        + high_value_summary["filtered"]
        + source_discovery_summary["conflictClusters"]
        + formal_research_materials_summary["pendingProjects"]
        + monitoring_infrastructure_summary["statusCounts"].get("blocked", 0)
        + monitoring_infrastructure_summary["statusCounts"].get("conflict", 0)
        + weak_signal_summary["triageCounts"].get("discovery_only", 0)
        + weak_signal_summary["triageCounts"].get("identity_blocked", 0)
        + weak_signal_summary["triageCounts"].get("conflict", 0)
    )
    connection.execute(
        """
        UPDATE runs
        SET status = ?, finished_at = ?, duration_ms = ?,
            collected_count = ?, normalized_count = ?, matched_count = ?,
            filtered_count = ?, error_count = ?, zero_result_class = ?,
            zero_result_explanation = ?, error_summary = ?
        WHERE run_id = ?
        """,
        (
            status,
            now,
            duration_ms,
            collected_total,
            normalized_total,
            matched_total,
            filtered_total,
            total_errors,
            zero_result_class,
            explanation,
            "；".join(
                error.get("error", "")
                for error in (
                    errors
                    + discovery_summary["errors"]
                    + identity_summary["errors"]
                    + project_asset_identity_summary["errors"]
                    + formal_market_exit_summary["errors"]
                    + formal_research_materials_summary["errors"]
                    + high_value_bundle["errors"]
                    + source_discovery_summary["errors"]
                    + machine_scoring_summary["errors"]
                    + machine_conclusion_summary["errors"]
                    + monitoring_infrastructure_summary["errors"]
                    + weak_signal_summary["errors"]
                )[:5]
            ),
            run_id,
        ),
    )
    return {
        "runId": run_id,
        "taskId": task_id,
        "taskLabel": task["label"],
        "status": status,
        "explanation": explanation,
        "marketSuccess": market_success,
        "marketSkipped": market_skipped,
        "marketChanged": market_changed,
        "evidenceSuccess": evidence_success,
        "evidenceRestricted": evidence_restricted,
        "evidenceFailed": evidence_failed,
        "evidenceChanged": evidence_changed,
        "highValueCollected": high_value_summary["collected"],
        "highValueMatched": high_value_summary["matched"],
        "highValueChanged": high_value_summary["changed"],
        "highValueFailed": high_value_summary["failed"],
        "highValueAdded": high_value_summary["normalized"],
        "highValueDuplicates": high_value_summary["duplicates"],
        "highValueProjectsReviewed": high_value_summary["projectsReviewed"],
        "highValueVerifiedProjects": high_value_summary["verifiedProjects"],
        "highValueIdentityBlocked": high_value_summary["identityBlocked"],
        "highValueGithubTargets": high_value_summary["githubTargets"],
        "highValueDefillamaTargets": high_value_summary["defillamaTargets"],
        "highValueSnapshotTargets": high_value_summary["snapshotTargets"],
        "highValueCactusTargets": high_value_summary["cactusTargets"],
        "sourceDiscoveriesCollected": source_discovery_summary["collected"],
        "sourceDiscoveriesInserted": source_discovery_summary["inserted"],
        "sourceDiscoveriesMatchedExisting": source_discovery_summary["matchedExisting"],
        "sourceDiscoveriesCorroborated": source_discovery_summary["corroboratedClusters"],
        "sourceDiscoveriesPending": source_discovery_summary["pendingClusters"],
        "sourceDiscoveriesAutoPromoted": source_discovery_summary["autoPromotedProjects"],
        "sourceDiscoveriesCasesCreated": source_discovery_summary["autoCreatedCases"],
        "sourceDiscoveriesAutoLinked": source_discovery_summary["autoLinkedRecords"],
        "sourceDiscoveriesFailed": source_discovery_summary["failed"],
        "sourceDiscoveriesIncomplete": sum(
            bool(stat.get("incomplete"))
            for stat in source_discovery_bundle["sourceStats"].values()
        ),
        "contractSuccess": contract_summary["success"],
        "contractPassed": contract_summary["passed"],
        "sellPathsVerified": contract_summary["sellPathsVerified"],
        "contractConflicts": contract_summary["conflicts"],
        "contractFailed": contract_summary["failed"],
        "discoveriesObserved": discovery_summary["observed"],
        "discoveriesNew": discovery_summary["new"],
        "discoveriesPreflightPassed": discovery_summary["preflightPassed"],
        "discoveriesIdentityPending": discovery_summary["identityPending"],
        "discoveriesExistingAssets": discovery_summary["existingAssets"],
        "discoveriesRejected": discovery_summary["rejected"],
        "identityReviewed": identity_summary["reviewed"],
        "identityCorroborated": identity_summary["corroborated"],
        "identityOfficialVerified": identity_summary["officialVerified"],
        "identityPromoted": identity_summary["promoted"],
        "identityPending": identity_summary["pending"],
        "identityRejected": identity_summary["rejected"] + identity_summary["conflicts"],
        "machineAssetProjectsQueued": project_asset_identity_summary[
            "projectsQueued"
        ],
        "machineAssetProjectsReviewed": project_asset_identity_summary[
            "projectsReviewed"
        ],
        "machineAssetVerified": project_asset_identity_summary["verified"],
        "machineAssetCorroborated": project_asset_identity_summary[
            "corroborated"
        ],
        "machineAssetPending": project_asset_identity_summary["pending"],
        "machineAssetConflicts": project_asset_identity_summary["conflicts"],
        "machineAssetsCreated": project_asset_identity_summary["assetsCreated"],
        "machineAssetsLinked": project_asset_identity_summary["assetsLinked"],
        "machineAssetContracts": project_asset_identity_summary[
            "contractsUpserted"
        ],
        "machineAssetProjectsChanged": project_asset_identity_summary[
            "changedProjects"
        ],
        "formalProjectsReviewed": profile_enrichment_summary["projectsReviewed"],
        "formalProjectIdentitiesVerified": profile_enrichment_summary[
            "identityVerified"
        ],
        "formalProjectAnchorsAdded": profile_enrichment_summary["anchorsAdded"],
        "formalProjectsRemainingIdentityPending": profile_enrichment_summary[
            "remainingIdentityPending"
        ],
        "formalProjectsChanged": profile_enrichment_summary["changedProjects"],
        "formalMarketProjectsReviewed": formal_market_exit_summary[
            "projectsReviewed"
        ],
        "formalMarketAssetsReviewed": formal_market_exit_summary[
            "assetsReviewed"
        ],
        "formalMarketCoveredProjects": formal_market_exit_summary[
            "marketCoveredProjects"
        ],
        "formalExitCoveredProjects": formal_market_exit_summary[
            "exitCoveredProjects"
        ],
        "formalSellPathsVerified": formal_market_exit_summary[
            "sellPathsVerified"
        ],
        "formalMarketPendingProjects": formal_market_exit_summary[
            "pendingProjects"
        ],
        "formalMarketChangedProjects": formal_market_exit_summary[
            "changedProjects"
        ],
        "formalResearchProjectsReviewed": formal_research_materials_summary[
            "projectsReviewed"
        ],
        "formalResearchRecordsCollected": formal_research_materials_summary[
            "recordsCollected"
        ],
        "formalResearchRecordsAdded": formal_research_materials_summary[
            "recordsAdded"
        ],
        "formalResearchProjectsMatched": formal_research_materials_summary[
            "projectsMatched"
        ],
        "formalResearchDocumentsCovered": formal_research_materials_summary[
            "documentsCovered"
        ],
        "formalResearchTokenomicsCovered": formal_research_materials_summary[
            "tokenomicsCovered"
        ],
        "formalResearchTeamCovered": formal_research_materials_summary[
            "teamCovered"
        ],
        "formalResearchAuditCovered": formal_research_materials_summary[
            "auditCovered"
        ],
        "formalResearchPendingProjects": formal_research_materials_summary[
            "pendingProjects"
        ],
        "formalResearchAccessIssues": formal_research_materials_summary[
            "accessIssues"
        ],
        "machineScoringProjects": machine_scoring_summary["projectsScored"],
        "machineScoringHighConfidence": machine_scoring_summary[
            "highConfidence"
        ],
        "machineScoringMediumConfidence": machine_scoring_summary[
            "mediumConfidence"
        ],
        "machineScoringLowConfidence": machine_scoring_summary["lowConfidence"],
        "machineScoringInsufficient": machine_scoring_summary["insufficient"],
        "machineMismatchAbove65": machine_scoring_summary["mismatchAbove65"],
        "machineReadinessAbove65": machine_scoring_summary["readinessAbove65"],
        "machineScoringChangedProjects": machine_scoring_summary[
            "changedProjects"
        ],
        "machineScoringLifecycleCounts": machine_scoring_summary[
            "lifecycleCounts"
        ],
        "machineConclusionProjects": machine_conclusion_summary[
            "projectsPublished"
        ],
        "machineConclusionChangedProjects": machine_conclusion_summary[
            "changedProjects"
        ],
        "machineConclusionStateCounts": machine_conclusion_summary[
            "stateCounts"
        ],
        "machineConclusionActionCounts": machine_conclusion_summary[
            "actionCounts"
        ],
        "machineConclusionMissingScores": machine_conclusion_summary[
            "missingScores"
        ],
        "catalystPathProjects": catalyst_path_summary["projectsProcessed"],
        "catalystPathInserted": catalyst_path_summary["recordsInserted"],
        "catalystPathChangedProjects": catalyst_path_summary[
            "changedProjects"
        ],
        "catalystPathWithCatalyst": catalyst_path_summary["withCatalyst"],
        "catalystPathWithAsset": catalyst_path_summary["withAsset"],
        "catalystPathExitModeled": catalyst_path_summary["exitModeled"],
        "catalystPathStageCounts": catalyst_path_summary["stageCounts"],
        "monitoringProjectsReviewed": monitoring_infrastructure_summary[
            "projectsReviewed"
        ],
        "monitoringTargetsPublished": monitoring_infrastructure_summary[
            "targetsPublished"
        ],
        "monitoringTargetsInserted": monitoring_infrastructure_summary[
            "recordsInserted"
        ],
        "monitoringTargetsChanged": monitoring_infrastructure_summary[
            "changedTargets"
        ],
        "monitoringProjectsReady": monitoring_infrastructure_summary[
            "projectsWithReadyTargets"
        ],
        "monitoringStatusCounts": monitoring_infrastructure_summary[
            "statusCounts"
        ],
        "monitoringTypeCounts": monitoring_infrastructure_summary[
            "typeCounts"
        ],
        "weakSignalsPublished": weak_signal_summary["signalsPublished"],
        "weakSignalsInserted": weak_signal_summary["recordsInserted"],
        "weakSignalsChanged": weak_signal_summary["changedSignals"],
        "weakSignalProjectsLinked": weak_signal_summary["projectsLinked"],
        "weakSignalTriageCounts": weak_signal_summary["triageCounts"],
        "weakSignalSourceCounts": weak_signal_summary["sourceCounts"],
        "errors": total_errors,
        "projects": sorted(
            [
                *project_results.values(),
                *formal_market_exit_summary["projects"],
            ],
            key=lambda item: item["projectName"],
        ),
    }


def sync_refresh_data_backbone(
    connection,
    selected_components,
    ingestion_run_id,
    timeout,
):
    return run_data_backbone(
        connection,
        collect_software="data_backbone" in selected_components,
        ingestion_run_id=ingestion_run_id,
        timeout=timeout,
    )


def refresh_candidates(
    db_path=DEFAULT_DB_PATH,
    config_path=DEFAULT_CONFIG_PATH,
    pool_snapshot_path=DEFAULT_POOL_SNAPSHOT_PATH,
    runtime_snapshot_path=DEFAULT_SNAPSHOT_PATH,
    timeout=20,
    task_id="full_refresh",
    mode="manual",
    tracking_task_id="",
    progress_callback=None,
):
    production_mode = (
        Path(db_path).resolve() == Path(DEFAULT_DB_PATH).resolve()
    )
    task = task_definition(task_id)
    selected_components = set(task["components"])
    def notify(component, current_item):
        if progress_callback and component in selected_components:
            progress_callback(component, current_item)

    started_at = utc_now()
    started_clock = time.perf_counter()
    initialize_database(db_path, runtime_snapshot_path, backup=True)
    monitoring_sync_result = None
    if selected_components & {
        "monitoring_infrastructure",
        "high_value_evidence",
    }:
        infrastructure_connection = sqlite3.connect(db_path)
        infrastructure_connection.row_factory = sqlite3.Row
        try:
            infrastructure_connection.execute("PRAGMA foreign_keys = ON")
            monitoring_sync_result = persist_monitoring_targets(
                infrastructure_connection
            )
        finally:
            infrastructure_connection.close()
    fixture = machine_fixture() if production_mode else load_fixture()
    config = load_config(config_path)
    if production_mode:
        config = {**config, "projects": []}
    notify("market", "正在读取市场与退出数据")
    market_results = (
        collect_market_data(config, timeout=timeout)
        if "market" in selected_components
        and "formal_market_exit" not in selected_components
        else []
    )
    notify("high_value_evidence", "正在发现项目与高价值证据")
    evidence_results = (
        collect_evidence_data(fixture, timeout=timeout)
        if "evidence" in selected_components
        else []
    )
    notify("contracts", "正在确认合约与卖出路径")
    contract_results = (
        collect_contract_checks(
            config,
            fixture,
            market_results,
            timeout=timeout,
        )
        if "contracts" in selected_components
        and "formal_market_exit" not in selected_components
        else []
    )
    notify("source_discovery", "正在发现新的项目与证据入口")
    discovery_bundle = (
        collect_network_discoveries(timeout=timeout)
        if "discovery" in selected_components
        else {"records": [], "sourceStats": {}, "errors": []}
    )
    notify("identity", "正在核验项目主体与官方关系")
    identity_bundle = (
        collect_identity_reviews(
            (
                discovery_bundle
                if "discovery" in selected_components
                else existing_discovery_bundle(db_path)
            ),
            timeout=min(timeout, 15),
        )
        if "identity" in selected_components
        else {"records": [], "sourceStats": {}, "errors": []}
    )
    notify("project_asset_identity", "正在确认项目与可交易资产关系")
    project_asset_identity_bundle = (
        collect_machine_project_asset_identities(
            db_path=db_path,
            timeout=max(timeout, 30),
        )
        if "project_asset_identity" in selected_components
        else {
            "records": [],
            "errors": [],
            "projectsQueued": 0,
            "registryAssets": 0,
            "protocolRecords": 0,
        }
    )
    notify("high_value_evidence", "正在整理正式项目持续证据")
    high_value_bundle = (
        collect_high_value_sources(timeout=timeout, db_path=db_path)
        if "high_value_evidence" in selected_components
        else {
            "records": [],
            "sourceStats": {},
            "errors": [],
            "targetVersion": "",
            "coverage": {},
        }
    )
    selected_source_discovery_providers = [
        provider
        for provider, definition in SOURCE_DISCOVERY_DEFINITIONS.items()
        if definition["source_id"] in task["sourceIds"]
    ]
    notify("source_discovery", "正在处理项目发现队列")
    source_discovery_bundle = (
        collect_source_discoveries(
            timeout=max(timeout, 30),
            providers=selected_source_discovery_providers,
        )
        if "source_discovery" in selected_components
        else {
            "records": [],
            "sourceStats": {},
            "errors": [],
            "version": "",
        }
    )
    notify("formal_market_exit", "正在补充正式项目市场与退出资料")
    formal_market_exit_bundle = (
        collect_formal_market_exit(
            db_path=db_path,
            config_path=config_path,
            timeout=timeout,
        )
        if "formal_market_exit" in selected_components
        else {
            "records": [],
            "contractResults": [],
            "caseRecords": {},
            "errors": [],
            "projectsReviewed": 0,
            "assetsReviewed": 0,
        }
    )
    notify("formal_research_materials", "正在补充项目档案与研究材料")
    formal_research_materials_bundle = (
        collect_formal_research_materials(
            db_path=db_path,
            timeout=timeout,
        )
        if "formal_research_materials" in selected_components
        else {
            "projectsReviewed": 0,
            "projects": [],
            "records": [],
            "issues": [],
            "errors": [],
        }
    )
    current_run_id = run_id_now()
    duration_ms = round((time.perf_counter() - started_clock) * 1000)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        result = persist_refresh(
            connection,
            fixture,
            market_results,
            evidence_results,
            current_run_id,
            contract_results=contract_results,
            discovery_bundle=discovery_bundle,
            identity_bundle=identity_bundle,
            project_asset_identity_bundle=project_asset_identity_bundle,
            high_value_bundle=high_value_bundle,
            source_discovery_bundle=source_discovery_bundle,
            formal_market_exit_bundle=formal_market_exit_bundle,
            formal_research_materials_bundle=formal_research_materials_bundle,
            monitoring_infrastructure_summary=(
                monitoring_sync_result
                if "monitoring_infrastructure" in selected_components
                else None
            ),
            task_id=task_id,
            mode=mode,
            started_at=started_at,
            duration_ms=duration_ms,
        )
        result["identityAliases"] = sync_project_identity_aliases(connection)
        result["sourceAdapter"] = run_source_adapter(connection)
        result["evidenceLineage"] = sync_evidence_lineage(connection)
        notify("monitoring_infrastructure", "正在检查监控与数据健康")
        result["dataBackbone"] = sync_refresh_data_backbone(
            connection,
            selected_components,
            current_run_id,
            timeout,
        )
        if "data_backbone" in selected_components:
            open_backbone_gaps = connection.execute(
                """
                SELECT COUNT(*) FROM source_cursors_v2
                WHERE gap_status IN ('open', 'replaying')
                """
            ).fetchone()[0]
            result["explanation"] = (
                f"Event Schema v2 已检查 {result['dataBackbone']['normalized']['input']} 条原始事件，"
                f"新增 {result['dataBackbone']['normalized']['inserted']}，"
                f"幂等去重 {result['dataBackbone']['normalized']['duplicates']}，"
                f"GitHub 发布 {result['dataBackbone']['softwareCollection']['releaseRows']} 条，"
                f"包清单 {result['dataBackbone']['softwareCollection']['packageRows']} 条，"
                f"待归属证据 {result['dataBackbone']['orphanEvents']}，"
                f"开放断档 {open_backbone_gaps}。"
            )
            software_errors = result["dataBackbone"]["softwareCollection"]["errors"]
            if software_errors:
                result["status"] = "partial_success"
                result["errors"] = result.get("errors", 0) + len(software_errors)
                connection.execute(
                    """
                    UPDATE runs
                    SET status='partial_success', error_count=error_count+?
                    WHERE run_id=?
                    """,
                    (len(software_errors), current_run_id),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO run_source_stats (
                  run_source_stat_id, run_id, source_id, collector_id, status,
                  started_at, finished_at, collected_count, duplicate_count,
                  matched_count, filtered_count, shadow_added_count,
                  active_added_count, failed_count, filter_reason_summary_json,
                  error_message
                ) VALUES (?, ?, 'data-backbone-registry', 'data_backbone_v2',
                          'success', ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, '')
                """,
                (
                    stable_id("run-source", current_run_id, "data_backbone_v2"),
                    current_run_id, started_at, utc_now(),
                    result["dataBackbone"]["normalized"]["input"],
                    result["dataBackbone"]["normalized"]["duplicates"],
                    result["dataBackbone"]["normalized"]["input"]
                    - result["dataBackbone"]["orphanEvents"],
                    json.dumps(
                        {
                            "boundary": "数据主干不改变凸性评分、结论或动作",
                            "gapsDetected": result["dataBackbone"]["gapsDetected"],
                            "orphanEvents": result["dataBackbone"]["orphanEvents"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            if selected_components == {"data_backbone"}:
                connection.execute(
                    """
                    UPDATE runs
                    SET collected_count=?, duplicate_count=?, normalized_count=?,
                        matched_count=?, zero_result_class='none',
                        zero_result_explanation=''
                    WHERE run_id=?
                    """,
                    (
                        result["dataBackbone"]["normalized"]["input"],
                        result["dataBackbone"]["normalized"]["duplicates"],
                        result["dataBackbone"]["normalized"]["input"],
                        max(
                            0,
                            result["dataBackbone"]["normalized"]["input"]
                            - result["dataBackbone"]["orphanEvents"],
                        ),
                        current_run_id,
                    ),
                )
        connection.commit()
        lifecycle_result = (
            refresh_lifecycle_cache(connection, timeout=timeout)
            if selected_components
            & {
                "market",
                "formal_market_exit",
                "identity",
                "project_asset_identity",
                "discovery",
            }
            else {
                "status": "skipped",
                "updated": 0,
                "failed": 0,
                "reason": "本任务不涉及项目生命周期",
            }
        )
        result["lifecycle"] = lifecycle_result
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite 完整性检查失败：{integrity}")
        notify("page_snapshot_rebuild", "正在发布最新页面")
        write_pool_snapshot(
            build_pool_snapshot(
                connection,
                fixture,
                production_only=production_mode,
            ),
            pool_snapshot_path,
        )
        write_discovery_snapshot(build_discovery_snapshot(connection))
        write_master_pool_snapshot(build_master_pool_snapshot(connection))
        write_project_detail_snapshot(build_project_detail_snapshot(connection))
        write_scan_center_snapshot(build_scan_center_snapshot(connection))
        write_manual_review_snapshot(build_manual_review_snapshot(connection))
        write_runtime_snapshot(connection, runtime_snapshot_path)
        write_high_value_snapshot(build_high_value_snapshot(connection))
        write_source_discovery_snapshot(
            build_source_discovery_snapshot(connection)
        )
        write_evidence_ledger_snapshot(
            build_evidence_ledger_snapshot(connection)
        )
        write_source_adapter_snapshot(
            build_source_adapter_snapshot(
                connection,
                result["sourceAdapter"],
            )
        )
        write_catalyst_trade_path_snapshot(
            build_catalyst_trade_path_snapshot(connection)
        )
        write_monitoring_infrastructure_snapshot(
            build_monitoring_infrastructure_snapshot(connection)
        )
        write_weak_signal_snapshot(build_weak_signal_snapshot(connection))
        write_data_backbone_snapshot(build_data_backbone_snapshot(connection))
    finally:
        connection.close()
    notify("machine_research_scoring", "正在生成研究结论与催化摘要")
    write_four_layer_snapshot(
        build_four_layer_snapshot(
            pool_snapshot_path,
            DEFAULT_GOLD_INPUT_PATH,
            DEFAULT_GOLD_EXPECTED_PATH,
        ),
        FOUR_LAYER_OUTPUT_PATH,
    )
    rebuild_discovery_funnel_snapshot(db_path=db_path)
    rebuild_opportunity_center_snapshot(candidate_path=pool_snapshot_path)
    rebuild_research_route_snapshot()
    rebuild_tracking_tasks_snapshot(db_path=db_path)
    notify("tracking", "正在完成跟踪并发布页面快照")
    tracking_result = None
    if "tracking" in selected_components:
        tracking_result = execute_tracking_tasks(
            db_path=db_path,
            run_id=current_run_id,
            tracking_task_id=tracking_task_id,
            force=bool(tracking_task_id),
        )
        result["tracking"] = tracking_result
        result["explanation"] = (
            f"{result['explanation']} {tracking_result['explanation']}"
        )
        rebuild_tracking_tasks_snapshot(db_path=db_path)
        rebuild_opportunity_center_snapshot(candidate_path=pool_snapshot_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            run_row = connection.execute(
                "SELECT status, error_count FROM runs WHERE run_id = ?",
                (current_run_id,),
            ).fetchone()
            if run_row:
                result["status"] = run_row["status"]
                result["errors"] = run_row["error_count"]
        finally:
            connection.close()
        rebuild_tracking_tasks_snapshot(db_path=db_path)
    rebuild_update_snapshots(db_path=db_path)
    rebuild_change_explanations_snapshot(db_path=db_path)
    rebuild_model_acceptance_snapshot()
    try:
        result["decisionQuality"] = build_decision_quality_snapshots(db_path=db_path)
    except Exception as error:
        # C2.0 is a read-only derived layer; do not turn a completed business
        # refresh into a false collection failure when its projection fails.
        result["decisionQuality"] = {"status": "failed", "error": str(error)}
    return result


def main():
    parser = argparse.ArgumentParser(description="刷新凸性候选的市场与证据状态")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_POOL_SNAPSHOT_PATH)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument(
        "--task",
        choices=sorted(TASK_DEFINITIONS),
        default="full_refresh",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            refresh_candidates(
                db_path=args.db,
                config_path=args.config,
                pool_snapshot_path=args.snapshot,
                timeout=args.timeout,
                task_id=args.task,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
