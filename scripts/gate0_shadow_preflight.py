#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from contract_tradeability import user_environment
from init_db import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "gate0-shadow-scope.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runtime" / "gate0-shadow"
USER_AGENT = "Penguin-Convexity-Gate0/0.2"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def age_days(value, now=None):
    observed = parse_utc(value)
    if not observed:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - observed).total_seconds() // 86400))


def as_number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sha256_path(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_config(path=DEFAULT_CONFIG_PATH):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    required_states = {
        "success",
        "no_data",
        "quota_limited",
        "source_failure",
        "unsupported",
        "configuration_missing",
        "program_failure",
    }
    if set(payload.get("classificationStates") or []) != required_states:
        raise ValueError("Gate 0 来源状态枚举不完整")
    if payload["boundary"]["productCodeWritesAllowed"]:
        raise ValueError("Gate 0 不得写产品代码")
    if payload["boundary"]["productionDatabaseWritesAllowed"]:
        raise ValueError("Gate 0 不得写生产数据库")
    return payload


def normalize_address(network, value):
    text = str(value or "").strip()
    return text.lower() if network["chainType"] == "EVM" else text


def source_state(http_status=None, error=None, records=None):
    if http_status == 429:
        return "quota_limited"
    if http_status in (401, 403):
        return "configuration_missing"
    if http_status in (404, 405, 501):
        return "unsupported"
    if error:
        return "source_failure"
    if records == 0:
        return "no_data"
    return "success"


class RequestLedger:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.requests = []
        self.last_request_at = {}
        self._ssl_context = ssl.create_default_context()

    def request_json(
        self,
        source,
        url,
        *,
        safe_url=None,
        headers=None,
        payload=None,
        minimum_interval=0,
    ):
        previous = self.last_request_at.get(source)
        if previous is not None:
            wait_seconds = minimum_interval - (time.monotonic() - previous)
            if wait_seconds > 0:
                time.sleep(min(wait_seconds, 30))
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body else {}),
                **(headers or {}),
            },
        )
        started = time.monotonic()
        status = None
        response_headers = {}
        raw = b""
        error_text = ""
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=self._ssl_context
            ) as response:
                status = response.status
                response_headers = dict(response.headers)
                raw = response.read()
                decoded = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as error:
            status = error.code
            response_headers = dict(error.headers or {})
            error_text = f"HTTP {error.code}"
            decoded = None
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as error:
            error_text = f"{type(error).__name__}: {error}"
            decoded = None
        finally:
            self.last_request_at[source] = time.monotonic()
        state = source_state(http_status=status, error=error_text)
        self.requests.append(
            {
                "source": source,
                "url": safe_url or url,
                "observedAt": utc_now(),
                "httpStatus": status,
                "state": state,
                "latencyMs": round((time.monotonic() - started) * 1000),
                "responseBytes": len(raw),
                "rateLimit": {
                    "limit": response_headers.get("X-RateLimit-Limit"),
                    "remaining": response_headers.get("X-RateLimit-Remaining"),
                    "reset": response_headers.get("X-RateLimit-Reset"),
                    "retryAfter": response_headers.get("Retry-After"),
                },
                "error": error_text,
            }
        )
        return decoded

    def request_json_with_quota_retry(
        self,
        source,
        url,
        *,
        quota_waits=(31, 31),
        source_failure_waits=(2, 4),
        **kwargs,
    ):
        quota_waits = iter(quota_waits)
        source_failure_waits = iter(source_failure_waits)
        while True:
            payload = self.request_json(source, url, **kwargs)
            if payload is not None:
                return payload
            last_request = self.requests[-1]
            if last_request["httpStatus"] == 429:
                wait_seconds = next(quota_waits, None)
            elif last_request["state"] == "source_failure":
                wait_seconds = next(source_failure_waits, None)
            else:
                wait_seconds = None
            if wait_seconds is None:
                return None
            time.sleep(wait_seconds)


def readonly_database_profile(db_path):
    resolved = Path(db_path).resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        counts = {}
        for table in (
            "projects",
            "assets",
            "candidate_cases",
            "evidence_items",
            "raw_events",
            "source_discoveries",
            "network_discoveries",
            "market_snapshots",
        ):
            counts[table] = connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        discovery_history = dict(
            connection.execute(
                """
                SELECT MIN(first_seen_at) AS earliest,
                       MAX(first_seen_at) AS latest,
                       COUNT(DISTINCT substr(first_seen_at, 1, 10)) AS distinct_days
                FROM network_discoveries
                """
            ).fetchone()
        )
        market_history = dict(
            connection.execute(
                """
                SELECT MIN(observed_at) AS earliest,
                       MAX(observed_at) AS latest,
                       COUNT(DISTINCT substr(observed_at, 1, 10)) AS distinct_days,
                       COUNT(DISTINCT asset_id) AS assets
                FROM market_snapshots
                """
            ).fetchone()
        )
        network_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT network_id,
                       COUNT(*) AS discoveries,
                       SUM(CASE WHEN recent_buys_24h > 0 THEN 1 ELSE 0 END) AS with_buys,
                       SUM(CASE WHEN recent_sells_24h > 0 THEN 1 ELSE 0 END) AS with_sells,
                       SUM(CASE WHEN liquidity_usd IS NOT NULL THEN 1 ELSE 0 END) AS with_liquidity
                FROM network_discoveries
                GROUP BY network_id
                ORDER BY discoveries DESC, network_id
                """
            )
        ]
        asset_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT a.asset_id, a.project_id, a.chain, a.contract_address,
                       a.identity_status AS asset_identity_status,
                       p.canonical_name, p.website_domain, p.official_repo,
                       p.identity_status AS project_identity_status
                FROM assets a
                JOIN projects p ON p.project_id = a.project_id
                WHERE trim(a.contract_address) <> ''
                """
            )
        ]
        return {
            "path": str(resolved),
            "openMode": "read_only",
            "integrityCheck": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreignKeyErrors": len(list(connection.execute("PRAGMA foreign_key_check"))),
            "counts": counts,
            "networkDiscoveryHistory": discovery_history,
            "marketSnapshotHistory": market_history,
            "networkDiscoveryCoverage": network_rows,
            "knownAssets": asset_rows,
        }
    finally:
        connection.close()


def included_lookup(payload):
    return {
        (row.get("type"), row.get("id")): row.get("attributes") or {}
        for row in payload.get("included") or []
    }


def normalize_gecko_pool(network, row, lookup):
    attributes = row.get("attributes") or {}
    relationships = row.get("relationships") or {}
    base_ref = (relationships.get("base_token") or {}).get("data") or {}
    quote_ref = (relationships.get("quote_token") or {}).get("data") or {}
    dex_ref = (relationships.get("dex") or {}).get("data") or {}
    base_token = lookup.get((base_ref.get("type"), base_ref.get("id")), {})
    quote_token = lookup.get((quote_ref.get("type"), quote_ref.get("id")), {})
    dex = lookup.get((dex_ref.get("type"), dex_ref.get("id")), {})
    transactions = attributes.get("transactions") or {}
    volume = attributes.get("volume_usd") or {}
    price_change = attributes.get("price_change_percentage") or {}
    return {
        "poolId": row.get("id"),
        "networkId": network["id"],
        "dexId": dex_ref.get("id"),
        "dexName": dex.get("name") or dex_ref.get("id"),
        "poolAddress": attributes.get("address"),
        "poolName": attributes.get("name"),
        "poolCreatedAt": attributes.get("pool_created_at"),
        "baseToken": {
            "address": base_token.get("address"),
            "name": base_token.get("name"),
            "symbol": base_token.get("symbol"),
            "coingeckoCoinId": base_token.get("coingecko_coin_id"),
        },
        "quoteToken": {
            "address": quote_token.get("address"),
            "name": quote_token.get("name"),
            "symbol": quote_token.get("symbol"),
            "coingeckoCoinId": quote_token.get("coingecko_coin_id"),
        },
        "priceUsd": as_number(attributes.get("base_token_price_usd")),
        "reserveUsd": as_number(attributes.get("reserve_in_usd")),
        "volume24hUsd": as_number(volume.get("h24")),
        "priceChange24hPct": as_number(price_change.get("h24")),
        "transactions24h": {
            "buys": int((transactions.get("h24") or {}).get("buys") or 0),
            "sells": int((transactions.get("h24") or {}).get("sells") or 0),
            "buyers": int((transactions.get("h24") or {}).get("buyers") or 0),
            "sellers": int((transactions.get("h24") or {}).get("sellers") or 0),
        },
        "marketCapUsd": as_number(attributes.get("market_cap_usd")),
        "fdvUsd": as_number(attributes.get("fdv_usd")),
        "source": "geckoterminal_new_pools",
    }


def collect_gecko_pools(config, ledger, maximum_pages=None, selected_networks=None):
    settings = config["sources"]["geckoterminal"]
    credential = user_environment(settings.get("credentialEnv", ""))
    fallback_credential = user_environment(settings.get("fallbackCredentialEnv", ""))
    base_url = settings["authenticatedBaseUrl"] if credential else settings["baseUrl"]
    public_base_url = settings["baseUrl"]
    headers = (
        {settings["credentialHeader"]: credential}
        if credential
        else None
    )
    source_id = "geckoterminal_authenticated" if credential else "geckoterminal_public"
    using_fallback = False
    used_public_endpoint = False
    page_limit = maximum_pages or settings["publicPageLimit"]
    networks = [
        network
        for network in config["networks"]
        if not selected_networks or network["id"] in selected_networks
    ]
    all_pools = []
    coverage = []
    for network in networks:
        network_pools = []
        previous_fingerprint = None
        stop_reason = "upstream_page_cap_reached"
        source_state_value = "success"
        pages_attempted = 0
        pages_succeeded = 0
        for page in range(1, page_limit + 1):
            pages_attempted += 1
            url = (
                f"{base_url}/networks/{network['geckoTerminalId']}"
                f"/new_pools?page={page}&include=base_token,quote_token,dex"
            )
            payload = ledger.request_json_with_quota_retry(
                source_id,
                url,
                headers=headers,
                minimum_interval=settings["minimumRequestIntervalSeconds"],
            )
            if (
                payload is None
                and ledger.requests[-1]["state"] == "configuration_missing"
                and credential
                and fallback_credential
                and not using_fallback
            ):
                credential = fallback_credential
                headers = {settings["credentialHeader"]: credential}
                source_id = "geckoterminal_authenticated_fallback"
                using_fallback = True
                payload = ledger.request_json_with_quota_retry(
                    source_id,
                    url,
                    headers=headers,
                    minimum_interval=settings["minimumRequestIntervalSeconds"],
                )
            if (
                payload is None
                and ledger.requests[-1]["state"] == "source_failure"
                and credential
                and not used_public_endpoint
            ):
                used_public_endpoint = True
                base_url = public_base_url
                source_id = "geckoterminal_public"
                headers = None
                payload = ledger.request_json_with_quota_retry(
                    source_id,
                    url.replace(settings["authenticatedBaseUrl"], settings["baseUrl"]),
                    headers=headers,
                    minimum_interval=settings["minimumRequestIntervalSeconds"],
                )
            if payload is None:
                source_state_value = ledger.requests[-1]["state"]
                stop_reason = source_state_value
                break
            rows = payload.get("data") or []
            pages_succeeded += 1
            if not rows:
                stop_reason = "upstream_exhausted"
                break
            fingerprint = hashlib.sha256(
                "|".join(str(row.get("id") or "") for row in rows).encode("utf-8")
            ).hexdigest()
            if fingerprint == previous_fingerprint:
                source_state_value = "program_failure"
                stop_reason = "upstream_pagination_stalled"
                break
            previous_fingerprint = fingerprint
            lookup = included_lookup(payload)
            network_pools.extend(
                normalize_gecko_pool(network, row, lookup) for row in rows
            )
            if len(rows) < settings["pageSize"]:
                stop_reason = "upstream_exhausted"
                break
        all_pools.extend(network_pools)
        dates = [pool["poolCreatedAt"] for pool in network_pools if pool["poolCreatedAt"]]
        coverage.append(
            {
                "networkId": network["id"],
                "state": source_state_value if network_pools or source_state_value != "success" else "no_data",
                "pagesAttempted": pages_attempted,
                "pagesSucceeded": pages_succeeded,
                "poolsCollected": len(network_pools),
                "newestPoolCreatedAt": max(dates) if dates else None,
                "oldestPoolCreatedAt": min(dates) if dates else None,
                "stopReason": stop_reason,
                "coversNinetyDays": False,
                "coverageBoundary": settings["boundary"],
            }
        )
        print(
            "GATE0_PROGRESS "
            + json.dumps(
                {
                    "stage": "geckoterminal_network_complete",
                    "networkId": network["id"],
                    "state": coverage[-1]["state"],
                    "poolsCollected": coverage[-1]["poolsCollected"],
                    "stopReason": coverage[-1]["stopReason"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return all_pools, coverage


def pool_candidates(config, pools):
    network_map = {network["id"]: network for network in config["networks"]}
    candidates = {}
    for pool in pools:
        network = network_map[pool["networkId"]]
        address = normalize_address(network, pool["baseToken"]["address"])
        if not address:
            continue
        key = (network["id"], address)
        item = candidates.setdefault(
            key,
            {
                "networkId": network["id"],
                "chainType": network["chainType"],
                "contractAddress": pool["baseToken"]["address"],
                "tokenName": pool["baseToken"]["name"],
                "symbol": pool["baseToken"]["symbol"],
                "coingeckoCoinId": pool["baseToken"]["coingeckoCoinId"],
                "poolIds": [],
                "earliestObservedPoolCreatedAt": None,
                "bestPool": None,
                "projectUrls": [],
                "githubUrls": [],
                "security": {"state": "not_collected", "hardRisk": "unknown", "flags": []},
            },
        )
        item["poolIds"].append(pool["poolId"])
        created = pool["poolCreatedAt"]
        if created and (
            not item["earliestObservedPoolCreatedAt"]
            or created < item["earliestObservedPoolCreatedAt"]
        ):
            item["earliestObservedPoolCreatedAt"] = created
        if item["bestPool"] is None or (pool["reserveUsd"] or -1) > (
            item["bestPool"]["reserveUsd"] or -1
        ):
            item["bestPool"] = pool
    return list(candidates.values())


def chunks(values, size):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def enrich_dexscreener(config, ledger, candidates):
    settings = config["sources"]["dexscreener"]
    network_map = {network["id"]: network for network in config["networks"]}
    by_network = defaultdict(list)
    for candidate in candidates:
        by_network[candidate["networkId"]].append(candidate)
    outcomes = []
    for network_id, rows in by_network.items():
        network = network_map[network_id]
        candidate_map = {
            normalize_address(network, row["contractAddress"]): row for row in rows
        }
        matched = set()
        for batch in chunks(list(candidate_map), settings["tokenBatchSize"]):
            addresses = urllib.parse.quote(",".join(batch), safe=",")
            url = (
                f"{settings['baseUrl']}/tokens/v1/"
                f"{network['dexScreenerId']}/{addresses}"
            )
            payload = ledger.request_json_with_quota_retry(
                "dexscreener",
                url,
                safe_url=f"{settings['baseUrl']}/tokens/v1/{network['dexScreenerId']}/<batch:{len(batch)}>",
                minimum_interval=settings["minimumRequestIntervalSeconds"],
                source_failure_waits=(2, 4),
            )
            if payload is None:
                outcomes.append(
                    {
                        "networkId": network_id,
                        "state": ledger.requests[-1]["state"],
                        "requested": len(batch),
                        "matched": 0,
                    }
                )
                continue
            pairs = payload if isinstance(payload, list) else payload.get("pairs") or []
            batch_matched = set()
            for pair in pairs:
                base = normalize_address(network, (pair.get("baseToken") or {}).get("address"))
                quote = normalize_address(network, (pair.get("quoteToken") or {}).get("address"))
                keys = [key for key in (base, quote) if key in candidate_map]
                info = pair.get("info") or {}
                websites = [
                    str(item.get("url") or "").strip()
                    for item in info.get("websites") or []
                    if str(item.get("url") or "").strip()
                ]
                for key in keys:
                    batch_matched.add(key)
                    matched.add(key)
                    candidate = candidate_map[key]
                    for website in websites:
                        target = (
                            candidate["githubUrls"]
                            if urllib.parse.urlparse(website).hostname in {"github.com", "www.github.com"}
                            else candidate["projectUrls"]
                        )
                        if website not in target:
                            target.append(website)
            outcomes.append(
                {
                    "networkId": network_id,
                    "state": source_state(records=len(batch_matched)),
                    "requested": len(batch),
                    "matched": len(batch_matched),
                    "pairsReturned": len(pairs),
                }
            )
        for key, candidate in candidate_map.items():
            candidate["dexScreenerState"] = "success" if key in matched else "no_data"
    return outcomes


def security_flags(token):
    flags = []
    blocked_fields = {
        "is_honeypot": "疑似蜜罐",
        "cannot_sell_all": "无法卖出全部余额",
        "non_transferable": "不可转账",
    }
    high_fields = {
        "owner_change_balance": "管理方可修改余额",
        "hidden_owner": "隐藏管理员",
    }
    caution_fields = {
        "is_mintable": "仍可增发",
        "transfer_pausable": "可暂停转账",
        "slippage_modifiable": "可修改滑点或税费",
    }
    for field, label in blocked_fields.items():
        value = token.get(field)
        if isinstance(value, dict):
            value = value.get("status")
        if str(value or "0") == "1":
            flags.append({"field": field, "level": "blocked", "label": label})
    for field, label in high_fields.items():
        if str(token.get(field) or "0") == "1":
            flags.append({"field": field, "level": "high", "label": label})
    for field, label in caution_fields.items():
        value = token.get(field)
        if isinstance(value, dict):
            value = value.get("status")
        if str(value or "0") == "1":
            flags.append({"field": field, "level": "caution", "label": label})
    sell_tax = as_number(token.get("sell_tax"))
    if sell_tax and sell_tax > 0:
        flags.append(
            {
                "field": "sell_tax",
                "level": "blocked" if sell_tax >= 0.1 else "caution",
                "label": f"卖出税 {sell_tax * 100:.2f}%",
            }
        )
    return flags


def normalize_bearer_token(value):
    token = str(value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def resolve_goplus_access_token(config, ledger):
    settings = config["sources"]["goplus"]
    access_token = normalize_bearer_token(
        user_environment(settings.get("accessTokenEnv", ""))
    )
    app_key = user_environment(settings.get("appKeyEnv", ""))
    app_secret = user_environment(settings.get("appSecretEnv", ""))
    token_source = "configured_access_token" if access_token else "app_credentials"
    if not access_token and app_key and app_secret:
        timestamp = int(time.time())
        signature = hashlib.sha1(
            f"{app_key}{timestamp}{app_secret}".encode("utf-8")
        ).hexdigest()
        payload = ledger.request_json_with_quota_retry(
            "goplus_auth_token",
            f"{settings['baseUrl']}/token",
            safe_url=f"{settings['baseUrl']}/token",
            payload={"app_key": app_key, "time": timestamp, "sign": signature},
            source_failure_waits=(2, 4),
        )
        access_token = normalize_bearer_token(
            ((payload or {}).get("result") or {}).get("access_token")
        )
        if not access_token:
            return None, {
                "source": "goplus_auth",
                "state": ledger.requests[-1]["state"] if ledger.requests else "no_data",
                "tokenSource": token_source,
            }
    if not access_token:
        missing = []
        if not app_key:
            missing.append(settings.get("appKeyEnv"))
        if app_key and not app_secret:
            missing.append(settings.get("appSecretEnv"))
        return None, {
            "source": "goplus_auth",
            "state": "configuration_missing",
            "missing": [item for item in missing if item],
            "publicFallback": True,
        }
    probe_address = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    payload = ledger.request_json(
        "goplus_auth_probe",
        f"{settings['baseUrl']}/token_security/1?contract_addresses={probe_address}",
        safe_url=f"{settings['baseUrl']}/token_security/1?contract_addresses=<probe>",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    records = len((payload or {}).get("result") or {})
    api_code = (payload or {}).get("code")
    state = (
        "success"
        if ledger.requests[-1]["state"] == "success" and api_code == 1 and records > 0
        else "configuration_missing"
        if api_code in (401, 4012)
        else ledger.requests[-1]["state"]
    )
    return (access_token if state == "success" else None), {
        "source": "goplus_auth",
        "state": state,
        "tokenSource": token_source,
        "tokenSecurityRecords": records,
        "publicFallback": state != "success",
    }


def collect_goplus(config, ledger, candidates, access_token=None):
    settings = config["sources"]["goplus"]
    network_map = {network["id"]: network for network in config["networks"]}
    by_network = defaultdict(list)
    for candidate in candidates:
        by_network[candidate["networkId"]].append(candidate)
    outcomes = []
    for network_id, rows in by_network.items():
        network = network_map[network_id]
        if not network["goPlusSupported"]:
            for candidate in rows:
                candidate["security"] = {
                    "state": "unsupported",
                    "hardRisk": "unknown",
                    "flags": [],
                }
            outcomes.append(
                {"networkId": network_id, "state": "unsupported", "requested": len(rows), "returned": 0}
            )
            continue
        candidate_map = {
            normalize_address(network, row["contractAddress"]): row for row in rows
        }
        for batch in chunks(list(candidate_map), settings["tokenBatchSize"]):
            addresses = urllib.parse.quote(",".join(batch), safe=",")
            if network["chainType"] == "SOLANA":
                url = f"{settings['baseUrl']}/solana/token_security?contract_addresses={addresses}"
            else:
                url = f"{settings['baseUrl']}/token_security/{network['chainId']}?contract_addresses={addresses}"
            payload = ledger.request_json_with_quota_retry(
                "goplus",
                url,
                safe_url=f"{settings['baseUrl']}/token_security/{network['id']}/<batch:{len(batch)}>",
                headers={"Authorization": f"Bearer {access_token}"} if access_token else None,
                minimum_interval=settings["minimumRequestIntervalSeconds"],
                source_failure_waits=(2, 4),
            )
            if payload is None:
                for address in batch:
                    candidate_map[address]["security"] = {
                        "state": ledger.requests[-1]["state"],
                        "hardRisk": "unknown",
                        "flags": [],
                    }
                outcomes.append(
                    {
                        "networkId": network_id,
                        "state": ledger.requests[-1]["state"],
                        "requested": len(batch),
                        "returned": 0,
                    }
                )
                continue
            result = payload.get("result") or {}
            normalized_result = {
                normalize_address(network, address): token for address, token in result.items()
            }
            returned = 0
            for address in batch:
                token = normalized_result.get(address)
                if not token:
                    candidate_map[address]["security"] = {
                        "state": "no_data",
                        "hardRisk": "unknown",
                        "flags": [],
                    }
                    continue
                returned += 1
                flags = security_flags(token)
                candidate_map[address]["security"] = {
                    "state": "success",
                    "hardRisk": "blocked" if any(flag["level"] == "blocked" for flag in flags) else "clear",
                    "flags": flags,
                }
            outcomes.append(
                {
                    "networkId": network_id,
                    "state": source_state(records=returned),
                    "requested": len(batch),
                    "returned": returned,
                }
            )
    return outcomes


def known_asset_map(config, database_profile):
    network_map = {network["id"]: network for network in config["networks"]}
    chain_aliases = {
        "Ethereum": "ethereum-mainnet",
        "Solana": "solana-mainnet",
        "Base": "base-mainnet",
        "Arbitrum": "arbitrum-mainnet",
        "Arbitrum One": "arbitrum-mainnet",
        "BNB Smart Chain": "bnb-mainnet",
        "Robinhood Chain": "robinhood-mainnet",
    }
    result = {}
    for row in database_profile["knownAssets"]:
        network_id = chain_aliases.get(row["chain"], row["chain"])
        network = network_map.get(network_id)
        if not network:
            continue
        result[(network_id, normalize_address(network, row["contract_address"]))] = row
    return result


def classify_candidate(config, candidate, local_asset=None, now=None):
    boundary = config["boundary"]
    observed_age = age_days(candidate["earliestObservedPoolCreatedAt"], now=now)
    best = candidate.get("bestPool") or {}
    transactions = best.get("transactions24h") or {}
    buy_sell_observed = (transactions.get("buys") or 0) > 0 and (transactions.get("sells") or 0) > 0
    if observed_age is None:
        time_state = "no_data"
        relation = "unknown"
    elif observed_age >= boundary["exitFromNewProjectPoolOnDay"]:
        time_state = "out_of_window"
        relation = "D"
    else:
        time_state = "verified_market_t0"
        relation = "C"
    verified_project_evidence = bool(
        local_asset
        and local_asset.get("asset_identity_status") == "verified"
        and local_asset.get("project_identity_status") == "verified"
        and (local_asset.get("official_repo") or local_asset.get("website_domain"))
    )
    provisional_project_evidence = bool(candidate.get("githubUrls") or candidate.get("projectUrls"))
    evidence_state = (
        "verified_local_mapping"
        if verified_project_evidence
        else "provisional_platform_link"
        if provisional_project_evidence
        else "no_data"
    )
    security = candidate.get("security") or {}
    reasons = []
    if time_state != "verified_market_t0":
        reasons.append("t0_not_verified_in_window")
    if not buy_sell_observed:
        reasons.append("buy_and_sell_not_both_observed")
    if evidence_state == "no_data":
        reasons.append("project_evidence_missing")
    elif evidence_state == "provisional_platform_link":
        reasons.append("project_evidence_not_independently_mapped")
    if security.get("state") != "success":
        reasons.append(f"security_{security.get('state') or 'not_collected'}")
    elif security.get("hardRisk") == "blocked":
        reasons.append("security_hard_risk")
    deterministic_pre_gate_pass = not reasons
    return {
        "ageDays": observed_age,
        "timeState": time_state,
        "relationClass": relation,
        "assetPoolConsistency": "match" if candidate.get("contractAddress") else "no_data",
        "buyAndSellObserved": buy_sell_observed,
        "projectEvidenceState": evidence_state,
        "securityState": security.get("state") or "not_collected",
        "securityHardRisk": security.get("hardRisk") or "unknown",
        "deterministicPreGatePass": deterministic_pre_gate_pass,
        "blockingReasons": reasons,
    }


def probe_capabilities(config, ledger):
    probes = []
    github = config["sources"]["github"]
    github_token = user_environment(github["credentialEnv"])
    if github_token:
        payload = ledger.request_json(
            "github",
            "https://api.github.com/rate_limit",
            headers={"Authorization": f"Bearer {github_token}", "X-GitHub-Api-Version": "2022-11-28"},
        )
        resources = (payload or {}).get("resources") or {}
        probes.append(
            {
                "source": "github",
                "state": ledger.requests[-1]["state"],
                "core": resources.get("core"),
                "search": resources.get("search"),
                "codeSearch": resources.get("code_search"),
            }
        )
    else:
        probes.append({"source": "github", "state": "configuration_missing"})

    coingecko = config["sources"]["coingecko"]
    coingecko_key = user_environment(coingecko["credentialEnv"])
    if coingecko_key:
        payload = ledger.request_json(
            "coingecko",
            "https://api.coingecko.com/api/v3/ping",
            safe_url="https://api.coingecko.com/api/v3/ping",
            headers={"x-cg-demo-api-key": coingecko_key},
        )
        probes.append(
            {"source": "coingecko", "state": ledger.requests[-1]["state"], "ping": (payload or {}).get("gecko_says")}
        )
    else:
        probes.append({"source": "coingecko", "state": "configuration_missing"})

    coinmarketcap = config["sources"]["coinmarketcap"]
    coinmarketcap_key = user_environment(coinmarketcap["credentialEnv"])
    if coinmarketcap_key:
        payload = ledger.request_json(
            "coinmarketcap",
            coinmarketcap["keyInfoUrl"],
            safe_url=coinmarketcap["keyInfoUrl"],
            headers={"X-CMC_PRO_API_KEY": coinmarketcap_key},
        )
        account = (payload or {}).get("data") or {}
        plan = account.get("plan") or {}
        usage = account.get("usage") or {}
        probes.append(
            {
                "source": "coinmarketcap_usage",
                "state": ledger.requests[-1]["state"],
                "plan": {
                    "rateLimitMinute": plan.get("rate_limit_minute"),
                    "creditLimitMonthly": plan.get("credit_limit_monthly"),
                },
                "usage": {
                    "currentMinute": usage.get("current_minute"),
                    "currentDay": usage.get("current_day"),
                    "currentMonth": usage.get("current_month"),
                },
            }
        )
    else:
        probes.append({"source": "coinmarketcap_usage", "state": "configuration_missing"})

    geckoterminal = config["sources"]["geckoterminal"]
    geckoterminal_key = user_environment(geckoterminal.get("credentialEnv", ""))
    if geckoterminal_key:
        payload = ledger.request_json(
            "geckoterminal_auth_probe",
            f"{geckoterminal['authenticatedBaseUrl']}/networks/eth/new_pools?page=1&include=base_token",
            safe_url=f"{geckoterminal['authenticatedBaseUrl']}/networks/eth/new_pools?page=1&include=base_token",
            headers={geckoterminal["credentialHeader"]: geckoterminal_key},
        )
        probes.append(
            {
                "source": "geckoterminal_authenticated",
                "state": ledger.requests[-1]["state"],
                "records": len((payload or {}).get("data") or []),
            }
        )
    else:
        probes.append({"source": "geckoterminal_authenticated", "state": "configuration_missing"})

    helius = config["sources"]["helius"]
    helius_key = user_environment(helius["credentialEnv"])
    helius_project_id = user_environment(helius["projectIdEnv"])
    if helius_key:
        payload = ledger.request_json(
            "helius",
            f"https://mainnet.helius-rpc.com/?api-key={urllib.parse.quote(helius_key)}",
            safe_url="https://mainnet.helius-rpc.com/?api-key=<redacted>",
            payload={"jsonrpc": "2.0", "id": 1, "method": "getHealth", "params": []},
        )
        probes.append(
            {"source": "helius_rpc", "state": ledger.requests[-1]["state"], "health": (payload or {}).get("result")}
        )
    else:
        probes.append({"source": "helius_rpc", "state": "configuration_missing"})
    probes.append(
        {"source": "helius_usage", "state": "configuration_missing", "missing": [helius["projectIdEnv"]]}
        if not (helius_project_id and helius_key)
        else _probe_helius_usage(ledger, helius_project_id, helius_key)
    )

    goplus_access_token, goplus_probe = resolve_goplus_access_token(config, ledger)
    probes.append(goplus_probe)

    alchemy = config["sources"]["alchemy"]
    alchemy_key = user_environment(alchemy["credentialEnv"])
    for network in config["networks"]:
        if not network["alchemyHost"]:
            probes.append({"source": "alchemy", "networkId": network["id"], "state": "unsupported"})
            continue
        if not alchemy_key:
            probes.append({"source": "alchemy", "networkId": network["id"], "state": "configuration_missing"})
            continue
        probe_method = network.get("alchemyProbeMethod", "eth_chainId")
        payload = ledger.request_json(
            "alchemy",
            f"https://{network['alchemyHost']}/v2/{urllib.parse.quote(alchemy_key)}",
            safe_url=f"https://{network['alchemyHost']}/v2/<redacted>",
            payload={"jsonrpc": "2.0", "id": 1, "method": probe_method, "params": []},
        )
        rpc_result = (payload or {}).get("result")
        probes.append(
            {
                "source": "alchemy",
                "networkId": network["id"],
                "state": ledger.requests[-1]["state"],
                "probeMethod": probe_method,
                "chainId": rpc_result if probe_method == "eth_chainId" else None,
                "health": rpc_result if probe_method == "getHealth" else None,
            }
        )
    return probes, {"goplusAccessToken": goplus_access_token}


def _probe_helius_usage(ledger, project_id, api_key):
    payload = ledger.request_json(
        "helius_admin",
        f"https://admin-api.helius.xyz/v0/admin/projects/{urllib.parse.quote(project_id)}/usage",
        safe_url="https://admin-api.helius.xyz/v0/admin/projects/<redacted>/usage",
        headers={"X-Api-Key": api_key},
    )
    return {
        "source": "helius_usage",
        "state": ledger.requests[-1]["state"],
        "creditsRemaining": (payload or {}).get("creditsRemaining"),
        "creditsUsed": (payload or {}).get("creditsUsed"),
        "creditCycle": (payload or {}).get("creditCycle"),
        "subscriptionDetails": (payload or {}).get("subscriptionDetails"),
    }


def summarize_requests(requests):
    by_state = Counter(request["state"] for request in requests)
    by_source = defaultdict(Counter)
    for request in requests:
        by_source[request["source"]][request["state"]] += 1
    return {
        "total": len(requests),
        "byState": dict(sorted(by_state.items())),
        "bySource": {source: dict(sorted(states.items())) for source, states in sorted(by_source.items())},
        "latencyMs": {
            "minimum": min((request["latencyMs"] for request in requests), default=None),
            "maximum": max((request["latencyMs"] for request in requests), default=None),
            "average": round(sum(request["latencyMs"] for request in requests) / len(requests), 1) if requests else None,
        },
    }


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def existing_shadow_days(output_root):
    manifest = Path(output_root) / "manifest.jsonl"
    if not manifest.exists():
        return []
    days = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("finishedAt") and row.get("usableForShadowDay") is True:
            days.add(row["finishedAt"][:10])
    return sorted(days)


def build_run(config, db_path, maximum_pages=None, selected_networks=None, skip_security=False):
    started_at = utc_now()
    ledger = RequestLedger()
    database_profile = readonly_database_profile(db_path)
    known_assets = known_asset_map(config, database_profile)
    probes, runtime_credentials = probe_capabilities(config, ledger)
    pools, discovery_coverage = collect_gecko_pools(
        config,
        ledger,
        maximum_pages=maximum_pages,
        selected_networks=selected_networks,
    )
    candidates = pool_candidates(config, pools)
    dexscreener_outcomes = enrich_dexscreener(config, ledger, candidates)
    if skip_security:
        security_outcomes = [
            {
                "networkId": network_id,
                "state": "not_collected",
                "requested": len(rows),
                "returned": 0,
            }
            for network_id, rows in sorted(
                ((key, list(values)) for key, values in _group_candidates(candidates).items())
            )
        ]
    else:
        security_outcomes = collect_goplus(
            config,
            ledger,
            candidates,
            access_token=runtime_credentials.get("goplusAccessToken"),
        )
    network_map = {network["id"]: network for network in config["networks"]}
    for candidate in candidates:
        network = network_map[candidate["networkId"]]
        key = (candidate["networkId"], normalize_address(network, candidate["contractAddress"]))
        local_asset = known_assets.get(key)
        candidate["localAssetMatch"] = local_asset
        candidate["preGate"] = classify_candidate(config, candidate, local_asset=local_asset)
    finished_at = utc_now()
    blockers = [
        {
            "code": "no_90d_initial_backfill",
            "detail": "GeckoTerminal new_pools公开接口只覆盖最近48小时，不能单独建立90天初始池。",
        },
        {
            "code": "project_contract_mapping_unproven",
            "detail": "平台附带网站或GitHub链接尚未形成独立可核验的项目-合约映射。",
        },
    ]
    if not user_environment(config["sources"]["helius"]["projectIdEnv"]):
        blockers.append(
            {
                "code": "helius_usage_unknown",
                "detail": "未配置HELIUS_PROJECT_ID，Helius剩余额度不能由程序读取。",
            }
        )
    goplus_settings = config["sources"]["goplus"]
    if (
        user_environment(goplus_settings.get("appKeyEnv", ""))
        and not user_environment(goplus_settings.get("appSecretEnv", ""))
        and not user_environment(goplus_settings.get("accessTokenEnv", ""))
    ):
        blockers.append(
            {
                "code": "goplus_authenticated_access_incomplete",
                "detail": "已登记GoPlus App Key，但缺少配套App Secret或Access Token；当前继续使用公共接口。",
            }
        )
    security_requested = sum(row["requested"] for row in security_outcomes if row["state"] != "unsupported")
    security_returned = sum(row["returned"] for row in security_outcomes if row["state"] != "unsupported")
    if security_requested and security_returned / security_requested < 0.5:
        blockers.append(
            {
                "code": "goplus_new_token_coverage_insufficient",
                "detail": f"GoPlus仅返回{security_returned}/{security_requested}个受支持链新代币，不能单独承担显性合约风险门禁。",
            }
        )
    selected_scope = selected_networks or [network["id"] for network in config["networks"]]
    full_network_scope = set(selected_scope) == {network["id"] for network in config["networks"]}
    full_public_paging = maximum_pages in (None, config["sources"]["geckoterminal"]["publicPageLimit"])
    discovery_run_complete = all(
        row["state"] in {"success", "no_data"}
        and row["stopReason"] in {"upstream_exhausted", "upstream_page_cap_reached"}
        for row in discovery_coverage
    )
    usable_for_shadow_day = full_network_scope and full_public_paging and discovery_run_complete and not skip_security
    return {
        "schemaVersion": "convexity-gate0-shadow-run-v0.2",
        "runId": "gate0-" + started_at.replace("-", "").replace(":", "").replace(".", ""),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "phase": "gate0_shadow_preflight_running_not_product_frozen",
        "boundary": config["boundary"],
        "execution": {
            "maximumPagesPerNetwork": maximum_pages or config["sources"]["geckoterminal"]["publicPageLimit"],
            "selectedNetworks": selected_scope,
            "securitySkipped": skip_security,
            "usableForShadowDay": usable_for_shadow_day,
            "usableForGate0Pass": False,
        },
        "databaseProfile": database_profile,
        "capabilityProbes": probes,
        "discoveryCoverage": discovery_coverage,
        "dexScreenerOutcomes": dexscreener_outcomes,
        "securityOutcomes": security_outcomes,
        "requestSummary": summarize_requests(ledger.requests),
        "requests": ledger.requests,
        "counts": {
            "pools": len(pools),
            "candidateTokens": len(candidates),
            "preGatePass": sum(candidate["preGate"]["deterministicPreGatePass"] for candidate in candidates),
            "githubLinked": sum(bool(candidate["githubUrls"]) for candidate in candidates),
            "websiteLinked": sum(bool(candidate["projectUrls"]) for candidate in candidates),
            "localAssetMatched": sum(bool(candidate["localAssetMatch"]) for candidate in candidates),
        },
        "preGateBlockingReasons": dict(
            sorted(
                Counter(
                    reason
                    for candidate in candidates
                    for reason in candidate["preGate"]["blockingReasons"]
                ).items()
            )
        ),
        "blockers": blockers,
        "pools": pools,
        "candidates": candidates,
    }


def _group_candidates(candidates):
    grouped = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["networkId"]].append(candidate)
    return grouped


def persist_run(run, output_root):
    root = Path(output_root)
    run_path = root / "runs" / f"{run['runId']}.json"
    atomic_write_json(run_path, run)
    atomic_write_json(root / "latest.json", run)
    manifest = root / "manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "runId": run["runId"],
                    "startedAt": run["startedAt"],
                    "finishedAt": run["finishedAt"],
                    "path": str(run_path.resolve()),
                    "counts": run["counts"],
                    "requestSummary": run["requestSummary"],
                    "usableForShadowDay": run["execution"]["usableForShadowDay"],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    days = existing_shadow_days(root)
    return run_path, days


def main():
    parser = argparse.ArgumentParser(description="C2.1 Gate 0只读影子数据预检")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--max-pages-per-network", type=int)
    parser.add_argument("--network", action="append", dest="networks")
    parser.add_argument("--skip-security", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    config = load_config(arguments.config)
    configured_networks = {network["id"] for network in config["networks"]}
    selected_networks = arguments.networks or None
    if selected_networks and not set(selected_networks).issubset(configured_networks):
        raise SystemExit("--network包含未配置网络")
    public_limit = config["sources"]["geckoterminal"]["publicPageLimit"]
    if arguments.max_pages_per_network is not None and not 1 <= arguments.max_pages_per_network <= public_limit:
        raise SystemExit(f"--max-pages-per-network必须在1到{public_limit}之间")
    run = build_run(
        config,
        arguments.db,
        maximum_pages=arguments.max_pages_per_network,
        selected_networks=selected_networks,
        skip_security=arguments.skip_security,
    )
    run["config"] = {
        "path": str(Path(arguments.config).resolve()),
        "sha256": sha256_path(arguments.config),
    }
    if arguments.no_write:
        print(json.dumps({"runId": run["runId"], "counts": run["counts"], "requestSummary": run["requestSummary"]}, ensure_ascii=False))
        return
    run_path, days = persist_run(run, arguments.output_root)
    print(
        json.dumps(
            {
                "runId": run["runId"],
                "runPath": str(run_path.resolve()),
                "shadowDaysObserved": len(days),
                "liveReliabilityDaysObserved": len(days),
                "liveReliabilityTargetDistinctDays": config["boundary"]["liveReliabilityTargetDistinctDays"],
                "liveReliabilityBlocksBackfillOrDevelopment": config["boundary"]["liveReliabilityBlocksBackfillOrDevelopment"],
                "counts": run["counts"],
                "requestSummary": run["requestSummary"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
