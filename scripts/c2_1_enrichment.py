#!/usr/bin/env python3
"""Deterministic C2.1 market, repository and standard quote enrichment."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

from c2_1_db import json_text, utc_now
from c2_1_resilience import commit_cursor, cursor_decision, day_window, hour_window
from c2_1_rules import age_days
from contract_tradeability import user_environment


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "gate0-shadow-scope.json"
USER_AGENT = "Penguin-Convexity-C2.1/1.0"
RETRY_DELAYS = (0, 5, 15, 60)
TOTAL_SUPPLY_SELECTOR = "0x18160ddd"
DECIMALS_SELECTOR = "0x313ce567"
TERMINAL_SOURCE_STATES = {"success", "no_data", "unsupported", "configuration_missing"}
STABLE_OUTPUTS = {
    "ethereum-mainnet": ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6, "USDC"),
    "base-mainnet": ("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 6, "USDC"),
    "arbitrum-mainnet": ("0xaf88d065e77c8cc2239327c5edb3a432268e5831", 6, "USDC"),
    "bnb-mainnet": ("0x55d398326f99059ff775485246999027b3197955", 18, "USDT"),
    "solana-mainnet": ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6, "USDC"),
}
RETRYABLE_SOURCE_STAGES = {
    "coingecko_new_pools": "incrementalDiscovery",
    "dexscreener": "market",
    "project_website_identity": "websiteIdentity",
    "github": "github",
    "goplus": "riskAndSupply",
    "c2_1_path4": "path4",
    "standard_sell_quote": "quotes",
}


def prepare_source_retry(connection, source_id):
    """Release cooldown only for the selected source's recoverable failures."""
    if source_id not in RETRYABLE_SOURCE_STAGES:
        raise ValueError("不支持单独更新这个来源。")
    pending = connection.execute(
        """
        SELECT COUNT(*) FROM source_cursors
        WHERE source_id=? AND status IN ('source_failure','quota_limited')
        """,
        (source_id,),
    ).fetchone()[0]
    connection.execute(
        """
        UPDATE source_cursors SET next_retry_at=NULL,updated_at=?
        WHERE source_id=? AND status IN ('source_failure','quota_limited')
        """,
        (utc_now(), source_id),
    )
    connection.commit()
    return int(pending)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


class JsonClient:
    def __init__(self, timeout=35, sleep=time.sleep):
        self.timeout = timeout
        self.sleep = sleep
        self.last_request_at = defaultdict(float)
        self.circuit = {}

    def request(self, source, url, *, headers=None, minimum_interval=0, no_data_http=(404, 422), payload=None):
        if source in self.circuit:
            state, http_status = self.circuit[source]
            return state, {}, http_status, [{"attempt": 0, "state": state, "reason": "provider_circuit_open_for_this_run"}]
        elapsed = time.monotonic() - self.last_request_at[source]
        if elapsed < minimum_interval:
            self.sleep(minimum_interval - elapsed)
        attempts = []
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            if delay:
                self.sleep(delay)
            started = time.monotonic()
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8") if payload is not None else None,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json", **({"Content-Type": "application/json"} if payload is not None else {}), **(headers or {})},
            )
            try:
                self.last_request_at[source] = time.monotonic()
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    attempts.append({"attempt": attempt, "state": "success", "httpStatus": response.status, "latencyMs": round((time.monotonic() - started) * 1000)})
                    return "success", payload, response.status, attempts
            except urllib.error.HTTPError as error:
                state = "quota_limited" if error.code == 429 else "configuration_missing" if error.code in {401, 403} else "no_data" if error.code in no_data_http else "source_failure"
                attempts.append({"attempt": attempt, "state": state, "httpStatus": error.code, "latencyMs": round((time.monotonic() - started) * 1000)})
                if state in {"configuration_missing", "no_data"} or attempt == len(RETRY_DELAYS):
                    if state in {"quota_limited", "source_failure"}:
                        self.circuit[source] = (state, error.code)
                    return state, {}, error.code, attempts
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                attempts.append({"attempt": attempt, "state": "source_failure", "errorType": type(error).__name__, "latencyMs": round((time.monotonic() - started) * 1000)})
                if attempt == len(RETRY_DELAYS):
                    self.circuit[source] = ("source_failure", None)
                    return "source_failure", {}, None, attempts
        return "program_failure", {}, None, attempts

    def text(self, source, url, *, minimum_interval=0):
        if source in self.circuit:
            state, http_status = self.circuit[source]
            return state, "", http_status, [{"attempt": 0, "state": state, "reason": "provider_circuit_open_for_this_run"}]
        elapsed = time.monotonic() - self.last_request_at[source]
        if elapsed < minimum_interval:
            self.sleep(minimum_interval - elapsed)
        attempts = []
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            if delay:
                self.sleep(delay)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            try:
                self.last_request_at[source] = time.monotonic()
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    content_type = str(response.headers.get("Content-Type") or "")
                    body = response.read(2_000_000).decode("utf-8", errors="replace")
                    attempts.append({"attempt": attempt, "state": "success", "httpStatus": response.status})
                    return ("success" if "html" in content_type.lower() or body else "no_data"), body, response.status, attempts
            except urllib.error.HTTPError as error:
                state = "quota_limited" if error.code == 429 else "configuration_missing" if error.code in {401, 403} else "no_data" if error.code in {404, 410, 422} else "source_failure"
                attempts.append({"attempt": attempt, "state": state, "httpStatus": error.code})
                if state in {"configuration_missing", "no_data"} or attempt == len(RETRY_DELAYS):
                    if state in {"quota_limited", "source_failure"}:
                        self.circuit[source] = (state, error.code)
                    return state, "", error.code, attempts
            except (urllib.error.URLError, TimeoutError) as error:
                attempts.append({"attempt": attempt, "state": "source_failure", "errorType": type(error).__name__})
                if attempt == len(RETRY_DELAYS):
                    self.circuit[source] = ("source_failure", None)
                    return "source_failure", "", None, attempts
        return "program_failure", "", None, attempts


def config(path=DEFAULT_CONFIG):
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return payload, {item["id"]: item for item in payload["networks"]}


def normalize(network, value):
    text = str(value or "").strip()
    return text if network["chainType"] == "SOLANA" else text.lower()


def pair_created_at(pair):
    try:
        value = pair.get("pairCreatedAt")
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc) if value else None
    except (TypeError, ValueError, OSError):
        return None


def pair_metric(pair, group, period):
    return number((pair.get(group) or {}).get(period))


def transaction_metric(pair, period, side):
    return number(((pair.get("txns") or {}).get(period) or {}).get(side))


def set_health(connection, source_id, scope_key, state, reason="", affected=0, http_status=None):
    now = utc_now()
    reason_code = {
        "quota_limited": "rate_limited",
        "source_failure": "provider_unavailable",
        "configuration_missing": "configuration_missing",
        "program_failure": "program_failure",
        "unsupported": "unsupported_chain",
        "no_data": "source_returned_no_data",
    }.get(state, "")
    connection.execute(
        """
        INSERT INTO source_health(source_id,scope_key,status,reason_code,plain_reason,http_status,affected_object_count,last_success_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id,scope_key) DO UPDATE SET status=excluded.status,reason_code=excluded.reason_code,
          plain_reason=excluded.plain_reason,http_status=excluded.http_status,affected_object_count=excluded.affected_object_count,
          last_success_at=COALESCE(excluded.last_success_at,source_health.last_success_at),updated_at=excluded.updated_at
        """,
        (source_id, scope_key, state, reason_code, reason, http_status, affected, now if state == "success" else None, now),
    )


def chunks(values, size):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def relationship_address(item, name, network):
    identifier = (((item.get("relationships") or {}).get(name) or {}).get("data") or {}).get("id") or ""
    address = identifier.split("_", 1)[1] if "_" in identifier else identifier
    return normalize(network, address)


def included_tokens(payload, network):
    result = {}
    for item in payload.get("included") or []:
        if item.get("type") != "token":
            continue
        identifier = str(item.get("id") or "")
        address = identifier.split("_", 1)[1] if "_" in identifier else identifier
        attributes = item.get("attributes") or {}
        result[normalize(network, address)] = {
            "address": address,
            "name": str(attributes.get("name") or "").strip(),
            "symbol": str(attributes.get("symbol") or "").strip(),
        }
    return result


def collect_incremental_new_pools(connection, client=None, config_path=DEFAULT_CONFIG):
    payload, networks = config(config_path)
    source = payload["sources"]["geckoterminal"]
    key = user_environment(source.get("credentialEnv", "")) or user_environment(source.get("fallbackCredentialEnv", "")) or user_environment("COINGECKO_DEMO_API_KEY")
    headers = {source["credentialHeader"]: key} if key else {}
    client = client or JsonClient()
    inserted = 0
    pool_rows = 0
    states = defaultdict(int)
    observed_at = utc_now()
    window_key = hour_window(observed_at)
    cutoff = parse_time(observed_at) - timedelta(days=90)
    skipped = 0
    source_breaker = None
    for network_id, network in networks.items():
        decision = cursor_decision(connection, "coingecko_new_pools", network_id, "incremental_discovery", window_key)
        if decision["action"] != "run":
            skipped += 1
            continue
        if source_breaker:
            state, reason = source_breaker
            set_health(connection, "coingecko_new_pools", network_id, state, "同一上游本轮已进入冷却；该链未重复发起请求。", 0)
            connection.commit()
            commit_cursor(
                connection, "coingecko_new_pools", network_id, "incremental_discovery", window_key,
                state, {"lastCommittedPage": 0, "nextPage": 1, "providerCircuitOpen": True},
            )
            states[state] += 1
            skipped += 1
            continue
        network_state = "success"
        http = None
        first_page = max(1, int((decision.get("cursor") or {}).get("nextPage") or 1))
        last_page = first_page - 1
        for page in range(first_page, int(source.get("publicPageLimit") or 10) + 1):
            query = urllib.parse.urlencode({"include": "base_token,quote_token,dex", "page": page})
            url = f"{source['authenticatedBaseUrl']}/networks/{network['geckoTerminalId']}/new_pools?{query}"
            state, response, http, attempts = client.request("coingecko_new_pools", url, headers=headers, minimum_interval=float(source["minimumRequestIntervalSeconds"]))
            if state != "success":
                network_state = state
                break
            data = response.get("data") or []
            tokens = included_tokens(response, network)
            if not data:
                break
            for pool in data:
                attributes = pool.get("attributes") or {}
                created = parse_time(attributes.get("pool_created_at"))
                pool_address = str(attributes.get("address") or "").strip()
                if not created or not pool_address or created < cutoff:
                    continue
                dex_identifier = (((pool.get("relationships") or {}).get("dex") or {}).get("data") or {}).get("id") or ""
                for side in ("base_token", "quote_token"):
                    address = relationship_address(pool, side, network)
                    token = tokens.get(address) or {"address": address, "name": "", "symbol": ""}
                    if not address:
                        continue
                    t0 = created.isoformat().replace("+00:00", "Z")
                    existing = connection.execute("SELECT candidate_id,gate0_t0 FROM candidates WHERE network_id=? AND token_address_normalized=?", (network_id, address)).fetchone()
                    if existing:
                        candidate_id = existing["candidate_id"]
                        if created < parse_time(existing["gate0_t0"]):
                            connection.execute("UPDATE candidates SET gate0_t0=?,effective_t0=?,gate0_pool_id=?,dex_ids_json=?,updated_at=? WHERE candidate_id=?", (t0, t0, pool_address, json_text([dex_identifier] if dex_identifier else []), observed_at, candidate_id))
                    else:
                        cursor = connection.execute(
                            """
                            INSERT INTO candidates(network_id,token_address,token_address_normalized,gate0_t0,effective_t0,t0_status,
                              t0_evidence_type,t0_scope_json,gate0_pool_id,dex_ids_json,source_run_id,first_seen_at,
                              continuity_status,relationship_class,canonical_name,symbol,identity_status,local_stage,local_reason,created_at,updated_at)
                            VALUES(?,?,?,?,?,'verified_in_supported_scope','provider_indexed_new_pool',?,?,?,'c2.1-incremental',?,
                              'candidate_asset','D',?,?,'not_verified','incremental_discovered','coingecko_new_pool',?,?)
                            """,
                            (network_id, token["address"], address, t0, t0, json_text({"scope": "coingecko_onchain_new_pools", "attempts": attempts}), pool_address, json_text([dex_identifier] if dex_identifier else []), observed_at, token["name"], token["symbol"], observed_at, observed_at),
                        )
                        candidate_id = cursor.lastrowid
                        inserted += 1
                    connection.execute(
                        """
                        INSERT INTO candidate_pools(candidate_id,pool_id,dex_id,created_at,source_id,indexed_status)
                        VALUES(?,?,?,?,?,'indexed')
                        ON CONFLICT(candidate_id,pool_id) DO UPDATE SET dex_id=excluded.dex_id,indexed_status='indexed'
                        """,
                        (candidate_id, pool_address, dex_identifier, t0, "coingecko_new_pools"),
                    )
                    pool_rows += 1
            connection.commit()
            last_page = page
            commit_cursor(
                connection, "coingecko_new_pools", network_id, "incremental_discovery", window_key,
                "running", {"lastCommittedPage": page, "nextPage": page + 1},
            )
            if len(data) < int(source.get("pageSize") or 20):
                break
        states[network_state] += 1
        set_health(connection, "coingecko_new_pools", network_id, network_state, "新池增量发现已完成。" if network_state == "success" else "新池增量来源暂未完成，已保留游标与旧快照。", inserted, http)
        connection.commit()
        commit_cursor(
            connection, "coingecko_new_pools", network_id, "incremental_discovery", window_key,
            network_state, {"lastCommittedPage": last_page, "nextPage": last_page + 1},
        )
        if network_state in {"source_failure", "quota_limited", "configuration_missing", "program_failure"}:
            source_breaker = (network_state, "provider_failure_after_bounded_retries")
    return {"newCandidates": inserted, "poolRows": pool_rows, "networkStates": dict(states), "skippedScopes": skipped}


def market_candidates(connection):
    return connection.execute(
        """
        SELECT c.* FROM candidates c
        WHERE c.continuity_status='candidate_asset' AND (
          c.local_stage='incremental_discovered' OR (c.identity_status IN ('verified','market_matched')
          AND (COALESCE(c.official_repo,'')!='' OR EXISTS(
            SELECT 1 FROM product_evidence pe WHERE pe.candidate_id=c.candidate_id AND pe.status='qualifying'
          ))))
        ORDER BY c.network_id,c.candidate_id
        """
    ).fetchall()


def collect_market(connection, client=None, config_path=DEFAULT_CONFIG):
    payload, networks = config(config_path)
    source = payload["sources"]["dexscreener"]
    client = client or JsonClient()
    by_network = defaultdict(list)
    for row in market_candidates(connection):
        by_network[row["network_id"]].append(row)
    collected = 0
    states = defaultdict(int)
    skipped = 0
    window_key = hour_window()
    for network_id, candidates in sorted(by_network.items()):
        network = networks[network_id]
        for batch in chunks(candidates, int(source["tokenBatchSize"])):
            scope = f"{network_id}:{batch[0]['candidate_id']}-{batch[-1]['candidate_id']}"
            if cursor_decision(connection, "dexscreener", scope, "market", window_key)["action"] != "run":
                skipped += len(batch)
                continue
            addresses = [row["token_address"] for row in batch]
            encoded = urllib.parse.quote(",".join(addresses), safe=",")
            url = f"{source['baseUrl']}/tokens/v1/{network['dexScreenerId']}/{encoded}"
            state, response, http, attempts = client.request("dexscreener", url, minimum_interval=float(source["minimumRequestIntervalSeconds"]))
            pairs = response if isinstance(response, list) else (response.get("pairs") or [] if isinstance(response, dict) else [])
            by_address = defaultdict(list)
            normalized_batch = {normalize(network, address) for address in addresses}
            for pair in pairs:
                if str(pair.get("chainId") or "") != network["dexScreenerId"]:
                    continue
                for side in ("baseToken", "quoteToken"):
                    address = normalize(network, (pair.get(side) or {}).get("address"))
                    if address in normalized_batch:
                        by_address[address].append(pair)
            for candidate in batch:
                address = normalize(network, candidate["token_address"])
                matches = by_address.get(address) or []
                earliest = min((value for value in (pair_created_at(pair) for pair in matches) if value), default=None)
                best = max(matches, key=lambda pair: number((pair.get("liquidity") or {}).get("usd")) or -1, default=None)
                observed_at = utc_now()
                candidate_state = "success" if best else "no_data" if state == "success" else state
                states[candidate_state] += 1
                if earliest:
                    effective = min(parse_time(candidate["gate0_t0"]), earliest)
                    days = age_days(effective.isoformat(), observed_at)
                    connection.execute(
                        "UPDATE candidates SET effective_t0=?,local_stage=?,local_reason=?,updated_at=? WHERE candidate_id=?",
                        (effective.isoformat().replace("+00:00", "Z"), "outside_90_days" if days is not None and days > 90 else "market_mapped", "earlier_indexed_public_pool_found" if effective < parse_time(candidate["gate0_t0"]) else "accepted_gate0_t0_retained", observed_at, candidate["candidate_id"]),
                    )
                if not best:
                    continue
                base = normalize(network, (best.get("baseToken") or {}).get("address"))
                quote = normalize(network, (best.get("quoteToken") or {}).get("address"))
                token_side = "base" if address == base else "quote" if address == quote else "unmatched"
                buys = transaction_metric(best, "h24", "buys")
                sells = transaction_metric(best, "h24", "sells")
                volume = pair_metric(best, "volume", "h24")
                liquidity = number((best.get("liquidity") or {}).get("usd"))
                ratio = volume / liquidity if volume is not None and liquidity and liquidity > 0 else None
                price = number(best.get("priceUsd"))
                token_payload = best.get("baseToken") if token_side == "base" else best.get("quoteToken") if token_side == "quote" else {}
                websites = [str(item.get("url") or "").strip() for item in ((best.get("info") or {}).get("websites") or []) if str(item.get("url") or "").strip()]
                website = websites[0] if websites else ""
                window_id = "market:" + observed_at[:13]
                observation_id = "c21-dex-" + hashlib.sha256(f"{candidate['candidate_id']}|{window_id}".encode()).hexdigest()[:22]
                connection.execute(
                    """
                    INSERT INTO market_observations(
                      observation_id,candidate_id,window_id,source_name,source_status,observed_at,pair_address,
                      pair_created_at,token_side,liquidity_usd,fdv_usd,market_cap_usd,volume_usd,
                      transaction_count,observed_buys,observed_sells,volume_liquidity_ratio,price_usd,
                      standard_sell_notional_usd,standard_sell_quote_state,payload_json
                    ) VALUES(?,?,?,'DexScreener','success',?,?,?,?,?,?,?,?,?,?,?,?,?,100,'no_data',?)
                    ON CONFLICT(candidate_id,window_id,source_name) DO UPDATE SET
                      observed_at=excluded.observed_at,pair_address=excluded.pair_address,pair_created_at=excluded.pair_created_at,
                      token_side=excluded.token_side,liquidity_usd=excluded.liquidity_usd,fdv_usd=excluded.fdv_usd,
                      market_cap_usd=excluded.market_cap_usd,volume_usd=excluded.volume_usd,
                      transaction_count=excluded.transaction_count,observed_buys=excluded.observed_buys,
                      observed_sells=excluded.observed_sells,volume_liquidity_ratio=excluded.volume_liquidity_ratio,
                      price_usd=excluded.price_usd,payload_json=excluded.payload_json
                    """,
                    (observation_id, candidate["candidate_id"], window_id, observed_at, best.get("pairAddress") or "", earliest.isoformat().replace("+00:00", "Z") if earliest else None, token_side, liquidity, number(best.get("fdv")) if token_side == "base" else None, number(best.get("marketCap")) if token_side == "base" else None, volume, (buys or 0) + (sells or 0), buys, sells, ratio, price, json_text({"dexId": best.get("dexId"), "attempts": attempts, "boundary": "provider_indexed_pairs_not_global_market"})),
                )
                connection.execute(
                    """
                    UPDATE candidates SET canonical_name=CASE WHEN canonical_name='' THEN ? ELSE canonical_name END,
                      symbol=CASE WHEN symbol='' THEN ? ELSE symbol END,
                      website_domain=CASE WHEN website_domain='' THEN ? ELSE website_domain END,
                      identity_status=CASE WHEN identity_status='not_verified' AND ?!='' THEN 'market_matched' ELSE identity_status END,
                      local_stage='market_mapped',local_reason='dexscreener_pool_and_token_direction_matched',updated_at=?
                    WHERE candidate_id=?
                    """,
                    (str((token_payload or {}).get("name") or "").strip(), str((token_payload or {}).get("symbol") or "").strip(), website, website, observed_at, candidate["candidate_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO candidate_pools(candidate_id,pool_id,dex_id,created_at,source_id,indexed_status)
                    VALUES(?,?,?,?,?,'indexed')
                    ON CONFLICT(candidate_id,pool_id) DO UPDATE SET dex_id=excluded.dex_id,indexed_status='indexed'
                    """,
                    (candidate["candidate_id"], best.get("pairAddress") or "", str(best.get("dexId") or ""), earliest.isoformat().replace("+00:00", "Z") if earliest else observed_at, "dexscreener"),
                )
                collected += 1
            connection.commit()
            set_health(connection, "dexscreener", network_id, state, "DEX市场批次已完成。" if state == "success" else "DEX市场来源暂时未完成，保留上次完整记录。", len(batch), http)
            connection.commit()
            commit_cursor(connection, "dexscreener", scope, "market", window_key, state, {"candidateIds": [row["candidate_id"] for row in batch]})
    return {"requestedCandidates": sum(len(rows) for rows in by_network.values()), "observations": collected, "states": dict(states), "skippedCandidates": skipped}


def github_target(value):
    try:
        parsed = urllib.parse.urlparse(str(value or ""))
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    return {"owner": parts[0], "repository": parts[1].removesuffix(".git") if len(parts) > 1 else ""}


def safe_public_web_url(value):
    try:
        parsed = urllib.parse.urlparse(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname.lower().strip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            return None
        try:
            if ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback:
                return None
        except ValueError:
            pass
        return parsed.geturl()
    except ValueError:
        return None


def github_links_from_html(html):
    links = []
    for value in re.findall(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", html or "", flags=re.IGNORECASE):
        clean = value.rstrip("/).,'\"<> ")
        if github_target(clean) and clean not in links:
            links.append(clean)
    return links


def collect_website_identity(connection, client=None):
    client = client or JsonClient()
    rows = connection.execute(
        """
        SELECT candidate_id,website_domain FROM candidates
        WHERE continuity_status='candidate_asset' AND COALESCE(website_domain,'')!='' AND COALESCE(official_repo,'')=''
          AND julianday(?) - julianday(effective_t0) BETWEEN 0 AND 90
        ORDER BY candidate_id
        """,
        (utc_now(),),
    ).fetchall()
    found = 0
    states = defaultdict(int)
    skipped = 0
    window_key = day_window()
    for row in rows:
        scope = str(row["candidate_id"])
        if cursor_decision(connection, "project_website_identity", scope, "identity", window_key)["action"] != "run":
            skipped += 1
            continue
        url = safe_public_web_url(row["website_domain"])
        if github_target(url):
            state, links, http, attempts = "success", [url], None, []
        elif url:
            state, html, http, attempts = client.text("project_website", url, minimum_interval=0.15)
            links = github_links_from_html(html) if state == "success" else []
            if state == "success" and not links:
                state = "no_data"
        else:
            state, links, http, attempts = "unsupported", [], None, []
        states[state] += 1
        if links:
            connection.execute(
                """
                UPDATE candidates SET official_repo=?,identity_status='market_matched',local_stage='repository_mapped',
                  local_reason='market_project_website_links_repository',updated_at=? WHERE candidate_id=?
                """,
                (links[0], utc_now(), row["candidate_id"]),
            )
            found += 1
        set_health(connection, "project_website_identity", str(row["candidate_id"]), state, "项目市场资料指向的网站与代码仓库链路已核验。" if links else "当前网站未形成可核验的官方代码仓库链路。", 1, http)
        connection.commit()
        commit_cursor(connection, "project_website_identity", scope, "identity", window_key, state, {"candidateId": row["candidate_id"]})
    return {"websites": len(rows), "repositoryLinksFound": found, "states": dict(states), "skippedCandidates": skipped}


def documentation_only(files):
    if not files:
        return False
    document_names = {"readme", "license", "changelog", "contributing", "code_of_conduct", "security"}
    for item in files:
        name = str((item or {}).get("filename") or "").replace("\\", "/").lower()
        stem = Path(name).stem
        if not (name.startswith("docs/") or Path(name).suffix in {".md", ".rst", ".txt"} or stem in document_names):
            return False
    return True


def resolve_github_repository(client, target, headers):
    owner = target["owner"]
    if target["repository"]:
        state, repo, http, attempts = client.request("github", f"https://api.github.com/repos/{owner}/{target['repository']}", headers=headers, minimum_interval=0.08)
        return state, repo, http, attempts
    for endpoint in ("orgs", "users"):
        state, repos, http, attempts = client.request("github", f"https://api.github.com/{endpoint}/{owner}/repos?per_page=100&sort=pushed", headers=headers, minimum_interval=0.08)
        if state == "success" and isinstance(repos, list):
            candidates = [repo for repo in repos if not repo.get("fork") and not repo.get("archived") and number(repo.get("size")) and number(repo.get("size")) > 0]
            return ("success", max(candidates, key=lambda repo: repo.get("pushed_at") or "", default={}), http, attempts) if candidates else ("no_data", {}, http, attempts)
        if state not in {"no_data"}:
            return state, {}, http, attempts
    return "no_data", {}, None, []


def collect_github(connection, client=None):
    client = client or JsonClient()
    token = user_environment("BUYI_GITHUB_TOKEN")
    headers = {"X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    candidate_rows = connection.execute(
        "SELECT candidate_id,mapped_project_id,official_repo,identity_status FROM candidates WHERE continuity_status='candidate_asset' AND COALESCE(official_repo,'')!=''"
    ).fetchall()
    projects = defaultdict(list)
    for candidate in candidate_rows:
        projects[candidate["official_repo"]].append(candidate)
    qualifying = 0
    states = defaultdict(int)
    skipped = 0
    window_key = day_window()
    for official_repo, candidates in projects.items():
        scope = hashlib.sha256(official_repo.encode()).hexdigest()[:24]
        if cursor_decision(connection, "github", scope, "official_repository", window_key)["action"] != "run":
            skipped += len(candidates)
            continue
        target = github_target(official_repo)
        state, repo, http, attempts = ("unsupported", {}, None, []) if not target else resolve_github_repository(client, target, headers)
        latest_commit = {}
        files = []
        if state == "success" and repo.get("full_name"):
            commit_state, commits, commit_http, commit_attempts = client.request("github", f"https://api.github.com/repos/{repo['full_name']}/commits?per_page=1", headers=headers, minimum_interval=0.08)
            attempts += commit_attempts
            if commit_state == "success" and isinstance(commits, list) and commits:
                latest_commit = commits[0]
                detail_state, detail, _, detail_attempts = client.request("github", f"https://api.github.com/repos/{repo['full_name']}/commits/{latest_commit.get('sha')}", headers=headers, minimum_interval=0.08)
                attempts += detail_attempts
                files = detail.get("files") or [] if detail_state == "success" else []
            else:
                state = commit_state if commit_state != "success" else "no_data"
                http = commit_http
        own_commit = bool(latest_commit) and not bool(repo.get("fork"))
        is_qualifying = state == "success" and bool(repo) and not repo.get("fork") and not repo.get("archived") and number(repo.get("size")) is not None and number(repo.get("size")) > 0 and own_commit
        states[state] += 1
        observed_at = utc_now()
        commit_data = latest_commit.get("commit") or {}
        last_commit_at = ((commit_data.get("committer") or {}).get("date") or (commit_data.get("author") or {}).get("date") or repo.get("pushed_at"))
        for candidate in candidates:
            evidence_id = "c21-github-" + hashlib.sha256(f"{candidate['candidate_id']}|{official_repo}".encode()).hexdigest()[:22]
            payload = {
                "organization": (repo.get("owner") or {}).get("login") or (target or {}).get("owner", ""),
                "repository": repo.get("full_name") or "",
                "isFork": bool(repo.get("fork")), "hasOwnCommits": own_commit,
                "hasNonDocumentOwnCommit": own_commit and not documentation_only(files),
                "isArchived": bool(repo.get("archived")), "isEmpty": not bool(number(repo.get("size"))),
                "primaryLanguage": repo.get("language"), "commitCountObserved": 1 if latest_commit else 0,
                "contributorCountObserved": None, "lastCommitAt": last_commit_at,
                "attempts": attempts, "boundary": "repository_activity_not_product_adoption_or_investment_value",
            }
            connection.execute(
                """
                INSERT INTO product_evidence(evidence_id,candidate_id,evidence_type,status,identity_status,source_name,source_url,observed_at,payload_json,boundary_note)
                VALUES(?,?,'github',?,?,'GitHub官方仓库',?,?,?,?)
                ON CONFLICT(evidence_id) DO UPDATE SET status=excluded.status,identity_status=excluded.identity_status,
                  source_name=excluded.source_name,source_url=excluded.source_url,observed_at=excluded.observed_at,
                  payload_json=excluded.payload_json,boundary_note=excluded.boundary_note
                """,
                (evidence_id, candidate["candidate_id"], "qualifying" if is_qualifying else "non_qualifying" if state in {"success", "no_data"} else state, candidate["identity_status"], repo.get("html_url") or official_repo, observed_at, json_text(payload), "目前只确认代码仓库，不证明产品部署、用户采用或投资价值。"),
            )
            if is_qualifying:
                connection.execute("UPDATE candidates SET relationship_class='C',relationship_reason='官方合格代码仓库已自动核验；项目新旧关系仍不猜测。',updated_at=? WHERE candidate_id=?", (observed_at, candidate["candidate_id"]))
                qualifying += 1
        set_health(connection, "github", official_repo, state, "官方仓库自动核验已完成。" if state == "success" else "官方仓库来源暂时未完成。", len(candidates), http)
        connection.commit()
        commit_cursor(connection, "github", scope, "official_repository", window_key, state, {"repository": official_repo, "candidateIds": [row["candidate_id"] for row in candidates]})
    return {"repositories": len(projects), "candidates": len(candidate_rows), "qualifyingCandidateEvidence": qualifying, "states": dict(states), "skippedCandidates": skipped}


def quote_input_amount(price_usd, decimals, notional=Decimal("100")):
    try:
        price = Decimal(str(price_usd))
        decimals = int(decimals)
        if price <= 0 or decimals < 0:
            return None
        amount = (notional / price * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_DOWN)
        return format(amount, "f") if 0 < amount < Decimal(2) ** 256 else None
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def affirmative(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def goplus_access_token(client, source):
    configured = user_environment(source.get("accessTokenEnv", ""))
    if configured:
        return configured[7:].strip() if configured.lower().startswith("bearer ") else configured, "configured_access_token"
    app_key = user_environment(source.get("appKeyEnv", ""))
    app_secret = user_environment(source.get("appSecretEnv", ""))
    if not app_key or not app_secret:
        return None, "public_fallback"
    timestamp = int(time.time())
    signature = hashlib.sha1(f"{app_key}{timestamp}{app_secret}".encode("utf-8")).hexdigest()
    state, payload, _, _ = client.request("goplus_auth", f"{source['baseUrl']}/token", payload={"app_key": app_key, "time": timestamp, "sign": signature})
    value = str(((payload.get("result") or {}).get("access_token") or "")).strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return (value if state == "success" and value else None), ("app_credentials" if state == "success" and value else state)


def holder_metrics(token):
    shares = []
    for item in token.get("holders") or []:
        value = number(item.get("percent"))
        if value is not None and value >= 0:
            shares.append(value * 100)
    return (sum(shares[:10]) if shares else None, sum((value / 100) ** 2 for value in shares) if shares else None)


def collect_risk_and_supply(connection, client=None, config_path=DEFAULT_CONFIG):
    payload, networks = config(config_path)
    source = payload["sources"]["goplus"]
    client = client or JsonClient()
    access_token, access_mode = goplus_access_token(client, source)
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    rows = connection.execute(
        """
        SELECT * FROM candidates WHERE continuity_status='candidate_asset' AND identity_status IN ('verified','market_matched')
          AND relationship_class IN ('A','B','C') AND julianday(?) - julianday(effective_t0) BETWEEN 0 AND 90
        ORDER BY network_id,candidate_id
        """,
        (utc_now(),),
    ).fetchall()
    by_network = defaultdict(list)
    for row in rows:
        by_network[row["network_id"]].append(row)
    risk_count = supply_count = 0
    states = defaultdict(int)
    skipped = 0
    window_key = day_window()
    for network_id, candidates in sorted(by_network.items()):
        network = networks[network_id]
        if not network.get("goPlusSupported"):
            states["unsupported"] += len(candidates)
            set_health(connection, "goplus", network_id, "unsupported", "该链当前不在GoPlus已接入范围内。", len(candidates))
            connection.commit()
            commit_cursor(
                connection, "goplus", network_id, "risk_supply", window_key, "unsupported",
                {"candidateIds": [row["candidate_id"] for row in candidates], "reason": "unsupported_chain"},
            )
            continue
        for batch in chunks(candidates, int(source["tokenBatchSize"])):
            scope = f"{network_id}:{batch[0]['candidate_id']}-{batch[-1]['candidate_id']}"
            if cursor_decision(connection, "goplus", scope, "risk_supply", window_key)["action"] != "run":
                skipped += len(batch)
                continue
            encoded = urllib.parse.quote(",".join(row["token_address"] for row in batch), safe=",")
            endpoint = f"{source['baseUrl']}/solana/token_security?contract_addresses={encoded}" if network["chainType"] == "SOLANA" else f"{source['baseUrl']}/token_security/{network['chainId']}?contract_addresses={encoded}"
            state, response, http, attempts = client.request("goplus", endpoint, headers=headers, minimum_interval=float(source["minimumRequestIntervalSeconds"]))
            result = response.get("result") or {}
            normalized = {normalize(network, address): value for address, value in result.items()}
            for candidate in batch:
                token = normalized.get(normalize(network, candidate["token_address"])) or {}
                item_state = "success" if token else "no_data" if state == "success" else state
                states[item_state] += 1
                observed_at = utc_now()
                sell_tax = number(token.get("sell_tax"))
                hard_codes = []
                if affirmative(token.get("is_honeypot")):
                    hard_codes.append("confirmed_honeypot")
                if affirmative(token.get("cannot_sell_all")):
                    hard_codes.append("confirmed_cannot_sell_all")
                if sell_tax is not None and sell_tax >= 0.20:
                    hard_codes.append("confirmed_sell_tax_ge_20pct")
                risk_id = "c21-goplus-risk-" + hashlib.sha256(f"{candidate['candidate_id']}|{observed_at[:13]}".encode()).hexdigest()[:22]
                connection.execute(
                    """
                    INSERT OR REPLACE INTO risk_observations(observation_id,candidate_id,source_name,source_status,observed_at,
                      hard_trade_block,severe_anomaly,reason_codes_json,payload_json) VALUES(?,?,'GoPlus',?,?,?,?,?,?)
                    """,
                    (risk_id, candidate["candidate_id"], item_state, observed_at, int(bool(hard_codes)), int(bool(hard_codes)), json_text(hard_codes), json_text({"accessMode": access_mode, "sellTax": sell_tax, "attempts": attempts, "boundary": "explicit_returned_flags_not_general_safety_claim"})),
                )
                risk_count += 1
                if token:
                    top10, hhi = holder_metrics(token)
                    supply_id = "c21-goplus-supply-" + hashlib.sha256(f"{candidate['candidate_id']}|{observed_at[:10]}".encode()).hexdigest()[:22]
                    connection.execute(
                        """
                        INSERT INTO supply_observations(observation_id,candidate_id,window_id,source_name,source_status,observed_at,
                          supply_raw,decimals,top10_share_pct,holder_hhi,payload_json)
                        VALUES(?,?,?,'GoPlus','success',?,?,?,?,?,?)
                        ON CONFLICT(candidate_id,window_id,source_name) DO UPDATE SET observed_at=excluded.observed_at,
                          supply_raw=excluded.supply_raw,decimals=excluded.decimals,top10_share_pct=excluded.top10_share_pct,
                          holder_hhi=excluded.holder_hhi,payload_json=excluded.payload_json
                        """,
                        (supply_id, candidate["candidate_id"], "supply:" + observed_at[:10], observed_at, str(token.get("total_supply")) if token.get("total_supply") is not None else None, None, top10, hhi, json_text({"holderCount": token.get("holder_count"), "reportedHolderRows": len(token.get("holders") or []), "boundary": "current_provider_holder_snapshot_includes_contract_entities"})),
                    )
                    supply_count += 1
            set_health(connection, "goplus", network_id, state, "显性风险和当前供应快照已完成。" if state == "success" else "GoPlus本批次未完成，缺失不记零。", len(batch), http)
            connection.commit()
            commit_cursor(connection, "goplus", scope, "risk_supply", window_key, state, {"candidateIds": [row["candidate_id"] for row in batch]})
    return {"candidates": len(rows), "riskObservations": risk_count, "supplyObservations": supply_count, "states": dict(states), "skippedCandidates": skipped}


def token_info(client, network, token, source, headers):
    url = f"{source['authenticatedBaseUrl']}/networks/{network['geckoTerminalId']}/tokens/{urllib.parse.quote(token)}/info"
    state, payload, http, attempts = client.request("coingecko_onchain", url, headers=headers, minimum_interval=float(source["minimumRequestIntervalSeconds"]))
    attributes = ((payload.get("data") or {}).get("attributes") or {}) if state == "success" else {}
    return state, attributes, http, attempts


def collect_quotes(connection, client=None, config_path=DEFAULT_CONFIG):
    payload, networks = config(config_path)
    source = payload["sources"]["geckoterminal"]
    key = user_environment(source.get("credentialEnv", "")) or user_environment(source.get("fallbackCredentialEnv", "")) or user_environment("COINGECKO_DEMO_API_KEY")
    headers = {source["credentialHeader"]: key} if key else {}
    client = client or JsonClient()
    rows = connection.execute(
        """
        SELECT m.*,c.network_id,c.token_address FROM market_observations m
        JOIN candidates c ON c.candidate_id=m.candidate_id
        WHERE m.observation_id=(SELECT m2.observation_id FROM market_observations m2 WHERE m2.candidate_id=m.candidate_id ORDER BY m2.observed_at DESC LIMIT 1)
          AND m.source_status='success' AND m.pair_address!=''
          AND c.relationship_class IN ('A','B','C')
        ORDER BY c.network_id,c.candidate_id
        """
    ).fetchall()
    states = defaultdict(int)
    skipped = 0
    window_key = hour_window()
    for row in rows:
        scope = str(row["candidate_id"])
        if cursor_decision(connection, "standard_sell_quote", scope, "quote", window_key)["action"] != "run":
            skipped += 1
            continue
        network_id = row["network_id"]
        state = "unsupported"
        loss = None
        provider = ""
        attempts = []
        if network_id in STABLE_OUTPUTS:
            info_state, info, http, info_attempts = token_info(client, networks[network_id], row["token_address"], source, headers)
            attempts += info_attempts
            amount = quote_input_amount(row["price_usd"], info.get("decimals"))
            if not amount:
                state = "no_data" if info_state == "success" else info_state
            else:
                stable, stable_decimals, stable_symbol = STABLE_OUTPUTS[network_id]
                if network_id == "solana-mainnet":
                    query = urllib.parse.urlencode({"inputMint": row["token_address"], "outputMint": stable, "amount": amount, "slippageBps": 100, "swapMode": "ExactIn"})
                    state, quote, _, quote_attempts = client.request("jupiter", f"https://api.jup.ag/swap/v1/quote?{query}", minimum_interval=2.05, no_data_http=(400, 404, 422))
                    attempts += quote_attempts
                    output = number(quote.get("outAmount"))
                    destination_usd = output / (10 ** stable_decimals) if output is not None else None
                    loss = 100 - destination_usd if destination_usd is not None else None
                    provider = "Jupiter"
                else:
                    query = urllib.parse.urlencode({"srcToken": row["token_address"], "destToken": stable, "amount": amount, "srcDecimals": info.get("decimals"), "destDecimals": stable_decimals, "side": "SELL", "network": networks[network_id]["chainId"], "version": "6.2"})
                    state, quote, _, quote_attempts = client.request("velora", f"https://api.paraswap.io/prices?{query}", minimum_interval=0.25, no_data_http=(400, 404, 422))
                    attempts += quote_attempts
                    route = quote.get("priceRoute") or {}
                    destination_usd = number(route.get("destUSD"))
                    loss = 100 - destination_usd if destination_usd is not None else None
                    provider = "Velora"
                if state == "success" and loss is None:
                    state = "no_data"
        states[state] += 1
        connection.execute(
            """
            UPDATE market_observations SET standard_sell_notional_usd=100,standard_sell_quote_state=?,
              standard_sell_quote_loss_pct=?,payload_json=? WHERE observation_id=?
            """,
            (state, loss, json_text({**json.loads(row["payload_json"] or "{}"), "quoteProvider": provider, "quoteAttempts": attempts, "quoteBoundary": "read_only_route_quote_not_executed_not_guaranteed"}), row["observation_id"]),
        )
        set_health(connection, "standard_sell_quote", str(row["candidate_id"]), state, "100美元标准卖出报价已完成。" if state == "success" else "100美元标准卖出报价当前不可用；不补零。", 1)
        connection.commit()
        commit_cursor(connection, "standard_sell_quote", scope, "quote", window_key, state, {"candidateId": row["candidate_id"]})
    return {"candidates": len(rows), "states": dict(states), "skippedCandidates": skipped}


def run_enrichment(connection, *, include_quotes=True, client=None, progress=None, pause=None, only_source_id=None):
    from c2_1_path4 import collect_path4

    client = client or JsonClient()
    config_payload, networks = config(DEFAULT_CONFIG)
    stages = [
        ("incrementalDiscovery", "新池增量发现", lambda: collect_incremental_new_pools(connection, client=client)),
        ("market", "行情与流动性", lambda: collect_market(connection, client=client)),
        ("websiteIdentity", "项目与仓库链路", lambda: collect_website_identity(connection, client=client)),
        ("github", "官方仓库证据", lambda: collect_github(connection, client=client)),
        ("riskAndSupply", "安全与供应", lambda: collect_risk_and_supply(connection, client=client)),
        ("path4", "已索引池与历史供应", lambda: collect_path4(connection, client, config_payload, networks, pause=pause)),
    ]
    if include_quotes:
        stages.append(("quotes", "100美元标准卖出报价", lambda: collect_quotes(connection, client=client)))
    if only_source_id is not None:
        stage_key = RETRYABLE_SOURCE_STAGES.get(only_source_id)
        if not stage_key:
            raise ValueError("不支持单独更新这个来源。")
        stages = [stage for stage in stages if stage[0] == stage_key]
    result = {}
    for index, (key, label, collect) in enumerate(stages, start=1):
        if pause:
            pause()
        if progress:
            progress(index - 1, len(stages), label)
        result[key] = collect()
        if progress:
            progress(index, len(stages), label)
    return result
