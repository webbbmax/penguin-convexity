#!/usr/bin/env python3
"""Read-only C2.1 capability probe for the four strong-evidence paths.

This script never writes the product database. Each network observation is appended
and fsynced so a rerun resumes completed rows instead of restarting the batch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGE_REPORT = PROJECT_ROOT / "reports" / "c2.1-age-threshold-analysis"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "c2.1-strong-path-input-probe"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "gate0-shadow-scope.json"
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "convexity.db"
AGE_BANDS = ("age_0_2", "age_3_6", "age_7_13", "age_14_30", "age_31_90")
LIQUIDITY_FLOORS = {
    "age_0_2": 2_000,
    "age_3_6": 2_000,
    "age_7_13": 3_000,
    "age_14_30": 3_000,
    "age_31_90": 5_000,
}
STABLE_OUTPUTS = {
    "ethereum-mainnet": ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6, "USDC"),
    "base-mainnet": ("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 6, "USDC"),
    "arbitrum-mainnet": ("0xaf88d065e77c8cc2239327c5edb3a432268e5831", 6, "USDC"),
    "bnb-mainnet": ("0x55d398326f99059ff775485246999027b3197955", 18, "USDT"),
    "solana-mainnet": ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6, "USDC"),
}
TERMINAL_STATES = {"success", "no_data", "unsupported", "configuration_missing"}
USER_AGENT = "Penguin-Convexity-C2.1-Strong-Path-Probe/0.1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def append_jsonl(path: Path, row) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_latest(path: Path):
    latest = {}
    for row in load_jsonl(path):
        latest[(row["networkId"], row["tokenAddress"])] = row
    return latest


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def quantile(values, probability):
    clean = sorted(value for value in (number(item) for item in values) if value is not None)
    if not clean:
        return None
    position = (len(clean) - 1) * probability
    low = int(position)
    high = min(low + 1, len(clean) - 1)
    fraction = position - low
    return clean[low] * (1 - fraction) + clean[high] * fraction


def market_metric(row, name):
    pair = row["bestPair"]
    if name == "liquidity":
        return pair.get("liquidityUsd")
    if name == "volume24":
        return pair.get("volumeH24Usd")
    if name == "tx24":
        return (pair.get("buysH24") or 0) + (pair.get("sellsH24") or 0)
    if name == "volumeLiquidity24":
        liquidity = pair.get("liquidityUsd")
        volume = pair.get("volumeH24Usd")
        return volume / liquidity if liquidity and volume is not None else None
    raise KeyError(name)


def select_market_shadow(source_path: Path, output_root: Path) -> None:
    latest = load_latest(source_path)
    rows = [
        row
        for row in latest.values()
        if row.get("state") == "success" and row.get("effectiveAgeBand") in AGE_BANDS
    ]
    cuts = {}
    for band in AGE_BANDS:
        band_rows = [row for row in rows if row["effectiveAgeBand"] == band]
        cuts[band] = {
            name: quantile([market_metric(row, name) for row in band_rows], 0.60)
            for name in ("liquidity", "volume24", "tx24", "volumeLiquidity24")
        }
    selected = []
    for row in rows:
        band = row["effectiveAgeBand"]
        pair = row["bestPair"]
        liquidity = market_metric(row, "liquidity")
        guard = (
            liquidity is not None
            and liquidity >= LIQUIDITY_FLOORS[band]
            and (pair.get("buysH24") or 0) >= 1
            and (pair.get("sellsH24") or 0) >= 1
        )
        demand_high = sum(
            market_metric(row, name) is not None
            and market_metric(row, name) >= cuts[band][name]
            for name in ("volume24", "tx24", "volumeLiquidity24")
        ) >= 2
        liquidity_high = liquidity is not None and liquidity >= cuts[band]["liquidity"]
        if guard and demand_high and liquidity_high:
            selected.append(
                {
                    **row,
                    "selectionRule": "effective_age_all_chain_p60_plus_liquidity_floor",
                    "bandCuts": cuts[band],
                }
            )
    selected.sort(key=lambda row: (row["effectiveAgeDays"], row["networkId"], row["tokenAddress"]))
    write_jsonl(output_root / "sample-selection.jsonl", selected)
    atomic_json(
        output_root / "sample-profile.json",
        {
            "schemaVersion": "c2.1-strong-path-sample-v0.1",
            "createdAt": utc_now(),
            "source": str(source_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sourceRows": len(latest),
            "effectiveIn90MarketRows": len(rows),
            "selectedRows": len(selected),
            "selectedByAge": dict(Counter(row["effectiveAgeBand"] for row in selected)),
            "selectedByNetwork": dict(Counter(row["networkId"] for row in selected)),
            "percentile": 0.60,
            "liquidityFloorsUsd": LIQUIDITY_FLOORS,
            "boundary": "capability_sample_not_front_qualified_not_frozen",
        },
    )
    print(json.dumps({"selectedRows": len(selected)}, ensure_ascii=False))


class JsonClient:
    def __init__(self):
        self.last_request_at = defaultdict(float)

    def get(
        self,
        source,
        url,
        *,
        headers=None,
        minimum_interval=0.0,
        no_data_http=(404, 422),
        payload=None,
    ):
        elapsed = time.monotonic() - self.last_request_at[source]
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        request_headers.update(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        attempts = []
        for attempt, delay in enumerate((0, 2, 5), start=1):
            if delay:
                time.sleep(delay)
            started = time.monotonic()
            try:
                self.last_request_at[source] = time.monotonic()
                request = urllib.request.Request(url, data=body, headers=request_headers)
                with urllib.request.urlopen(request, timeout=35) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    attempts.append({"attempt": attempt, "state": "success", "http": response.status})
                    return "success", data, response.status, attempts
            except urllib.error.HTTPError as error:
                try:
                    data = json.loads(error.read().decode("utf-8", "replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = {}
                state = (
                    "quota_limited"
                    if error.code == 429
                    else "configuration_missing"
                    if error.code in (401, 403)
                    else "no_data"
                    if error.code in no_data_http
                    else "source_failure"
                )
                attempts.append({"attempt": attempt, "state": state, "http": error.code})
                if state in {"configuration_missing", "no_data"} or attempt == 3:
                    return state, data, error.code, attempts
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                attempts.append(
                    {"attempt": attempt, "state": "source_failure", "errorType": type(error).__name__}
                )
                if attempt == 3:
                    return "source_failure", {}, None, attempts
        return "program_failure", {}, None, attempts


def config_networks(config_path: Path):
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    return config, {row["id"]: row for row in config["networks"]}


def relationship_address(data, side, chain_type):
    identifier = (
        (((data.get("relationships") or {}).get(side) or {}).get("data") or {}).get("id") or ""
    )
    address = identifier.split("_", 1)[1] if "_" in identifier else identifier
    return address.lower() if chain_type == "EVM" else address


def collect_market_inputs(sample_path: Path, output_root: Path, config_path: Path) -> None:
    config, networks = config_networks(config_path)
    source = config["sources"]["geckoterminal"]
    key = (
        os.getenv(source["credentialEnv"], "").strip()
        or os.getenv(source["fallbackCredentialEnv"], "").strip()
        or os.getenv("COINGECKO_DEMO_API_KEY", "").strip()
    )
    output_path = output_root / "market-inputs.jsonl"
    completed = {
        identity
        for identity, row in load_latest(output_path).items()
        if row.get("state") in TERMINAL_STATES
    }
    client = JsonClient()
    base = source["authenticatedBaseUrl"]
    interval = float(source["minimumRequestIntervalSeconds"])
    headers = {source["credentialHeader"]: key} if key else {}
    pending = [row for row in load_jsonl(sample_path) if (row["networkId"], row["tokenAddress"]) not in completed]
    for index, selected in enumerate(pending, start=1):
        network = networks[selected["networkId"]]
        gecko_id = network["geckoTerminalId"]
        token = selected["tokenAddress"]
        pool = selected["bestPair"]["pairAddress"]
        pool_url = f"{base}/networks/{gecko_id}/pools/{urllib.parse.quote(pool)}"
        pool_state, pool_payload, _, _ = client.get(
            "coingecko_onchain", pool_url, headers=headers, minimum_interval=interval
        )
        pool_data = (pool_payload.get("data") or {}) if pool_state == "success" else {}
        chain_type = network["chainType"]
        normalized_token = token.lower() if chain_type == "EVM" else token
        base_token = relationship_address(pool_data, "base_token", chain_type)
        quote_token = relationship_address(pool_data, "quote_token", chain_type)
        token_side = "base" if normalized_token == base_token else "quote" if normalized_token == quote_token else None
        info_url = f"{base}/networks/{gecko_id}/tokens/{urllib.parse.quote(token)}/info"
        info_state, info_payload, _, _ = client.get(
            "coingecko_onchain", info_url, headers=headers, minimum_interval=interval
        )
        info = (((info_payload.get("data") or {}).get("attributes")) or {}) if info_state == "success" else {}
        ohlcv_state, ohlcv = "no_data", []
        if pool_state == "success" and token_side:
            query = urllib.parse.urlencode(
                {
                    "aggregate": 1,
                    "limit": 100,
                    "currency": "usd",
                    "token": token_side,
                    "include_empty_intervals": "true",
                }
            )
            ohlcv_url = f"{base}/networks/{gecko_id}/pools/{urllib.parse.quote(pool)}/ohlcv/day?{query}"
            ohlcv_state, ohlcv_payload, _, _ = client.get(
                "coingecko_onchain", ohlcv_url, headers=headers, minimum_interval=interval
            )
            if ohlcv_state == "success":
                ohlcv = (((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or [])
        attributes = pool_data.get("attributes") or {}
        price = attributes.get(f"{token_side}_token_price_usd") if token_side else None
        holders = info.get("holders") or {}
        state = "success" if pool_state == "success" or info_state == "success" else pool_state
        row = {
            "networkId": selected["networkId"],
            "tokenAddress": token,
            "effectiveAgeBand": selected["effectiveAgeBand"],
            "collectedAt": utc_now(),
            "state": state,
            "poolState": pool_state,
            "tokenInfoState": info_state,
            "ohlcvState": ohlcv_state,
            "poolAddress": pool,
            "tokenSide": token_side,
            "decimals": info.get("decimals"),
            "priceUsd": number(price),
            "reserveUsd": number(attributes.get("reserve_in_usd")),
            "fdvUsd": number(attributes.get("fdv_usd")) if token_side == "base" else None,
            "marketCapUsd": number(attributes.get("market_cap_usd")) if token_side == "base" else None,
            "holderCount": holders.get("count"),
            "holderDistributionPct": holders.get("distribution_percentage") or {},
            "holdersLastUpdated": holders.get("last_updated"),
            "websites": info.get("websites") or [],
            "gtVerified": info.get("gt_verified"),
            "isHoneypot": info.get("is_honeypot"),
            "ohlcv": ohlcv,
            "ohlcvRows": len(ohlcv),
            "ohlcvBoundary": "selected_primary_pool_only_not_token_wide",
        }
        append_jsonl(output_path, row)
        print(f"market-input-progress {index}/{len(pending)} {selected['networkId']} {state}", flush=True)


def quote_input_amount(price_usd, decimals, notional_usd=Decimal("100")):
    try:
        price = Decimal(str(price_usd))
        decimal_count = int(decimals)
        if price <= 0 or decimal_count < 0:
            return None
        amount = (notional_usd / price * (Decimal(10) ** decimal_count)).to_integral_value(
            rounding=ROUND_DOWN
        )
        return format(amount, "f") if 0 < amount < Decimal(2) ** 256 else None
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def collect_quotes(sample_path: Path, output_root: Path, config_path: Path) -> None:
    _, networks = config_networks(config_path)
    market = load_latest(output_root / "market-inputs.jsonl")
    output_path = output_root / "quote-observations.jsonl"
    if output_path.exists():
        normalized_rows = []
        for row in load_jsonl(output_path):
            destination_usd = number(row.get("destinationUsd"))
            source_usd = number(row.get("sourceUsd"))
            normalized_rows.append(
                {
                    **row,
                    "schemaVersion": "c2.1-standard-sell-quote-v0.2",
                    "quoteLossPct": (100 - destination_usd) if destination_usd is not None else None,
                    "sourceValuationMismatchPct": (source_usd - 100) if source_usd is not None else None,
                    "quotePriceSource": "coingecko_onchain_selected_pool_spot",
                }
            )
        write_jsonl(output_path, normalized_rows)
    completed = {
        identity
        for identity, row in load_latest(output_path).items()
        if row.get("state") in TERMINAL_STATES
    }
    client = JsonClient()
    for selected in load_jsonl(sample_path):
        identity = (selected["networkId"], selected["tokenAddress"])
        if identity in completed:
            continue
        network_id, token = identity
        observed = market.get(identity) or {}
        base_row = {
            "schemaVersion": "c2.1-standard-sell-quote-v0.2",
            "networkId": network_id,
            "tokenAddress": token,
            "effectiveAgeBand": selected["effectiveAgeBand"],
            "collectedAt": utc_now(),
            "standardSellNotionalUsd": 100,
            "executionBoundary": "read_only_route_quote_not_executed_not_guaranteed",
            "quotePriceSource": "coingecko_onchain_selected_pool_spot",
        }
        if network_id == "robinhood-mainnet":
            append_jsonl(output_path, {**base_row, "state": "unsupported", "provider": None, "reason": "no_verified_route_aggregator"})
            continue
        if network_id not in STABLE_OUTPUTS:
            append_jsonl(output_path, {**base_row, "state": "unsupported", "provider": None, "reason": "network_not_configured"})
            continue
        amount = quote_input_amount(observed.get("priceUsd"), observed.get("decimals"))
        if not amount:
            append_jsonl(output_path, {**base_row, "state": "no_data", "provider": None, "reason": "missing_price_or_decimals"})
            continue
        stable, stable_decimals, stable_symbol = STABLE_OUTPUTS[network_id]
        if network_id == "solana-mainnet":
            query = urllib.parse.urlencode(
                {
                    "inputMint": token,
                    "outputMint": stable,
                    "amount": amount,
                    "slippageBps": 100,
                    "swapMode": "ExactIn",
                }
            )
            state, payload, http, _ = client.get(
                "jupiter", f"https://api.jup.ag/swap/v1/quote?{query}", minimum_interval=2.05, no_data_http=(400, 404, 422)
            )
            output_amount = number(payload.get("outAmount"))
            destination_usd = output_amount / (10**stable_decimals) if output_amount is not None else None
            row = {
                **base_row,
                "state": "success" if state == "success" and output_amount is not None else state,
                "provider": "jupiter",
                "httpStatus": http,
                "inputRawAmount": amount,
                "outputStable": stable_symbol,
                "destinationUsd": destination_usd,
                "quoteLossPct": 100 - destination_usd if destination_usd is not None else None,
                "sourceValuationMismatchPct": None,
                "priceImpactPct": number(payload.get("priceImpactPct")),
                "routeCount": len(payload.get("routePlan") or []),
                "gasCostUsd": None,
            }
        else:
            stable, stable_decimals, stable_symbol = STABLE_OUTPUTS[network_id]
            query = urllib.parse.urlencode(
                {
                    "srcToken": token,
                    "destToken": stable,
                    "amount": amount,
                    "srcDecimals": observed.get("decimals"),
                    "destDecimals": stable_decimals,
                    "side": "SELL",
                    "network": networks[network_id]["chainId"],
                    "version": "6.2",
                }
            )
            state, payload, http, _ = client.get(
                "velora", f"https://api.paraswap.io/prices?{query}", minimum_interval=0.25, no_data_http=(400, 404, 422)
            )
            route = payload.get("priceRoute") or {}
            source_usd = number(route.get("srcUSD"))
            destination_usd = number(route.get("destUSD"))
            row = {
                **base_row,
                "state": "success" if state == "success" and destination_usd is not None else state,
                "provider": "velora_paraswap_api",
                "httpStatus": http,
                "inputRawAmount": amount,
                "sourceUsd": source_usd,
                "outputStable": stable_symbol,
                "destinationUsd": destination_usd,
                "quoteLossPct": (100 - destination_usd) if destination_usd is not None else None,
                "sourceValuationMismatchPct": (source_usd - 100) if source_usd is not None else None,
                "priceImpactPct": number(route.get("priceImpact")),
                "routeCount": len(route.get("bestRoute") or []),
                "gasCostUsd": number(route.get("gasCostUSD")),
            }
        append_jsonl(output_path, row)
        print(f"quote-progress {network_id} {row['state']}", flush=True)


def goplus_token(client: JsonClient, config) -> tuple[str | None, str]:
    source = config["sources"]["goplus"]
    configured = os.getenv(source["accessTokenEnv"], "").strip()
    if configured:
        return configured[7:].strip() if configured.lower().startswith("bearer ") else configured, "configured_access_token"
    app_key = os.getenv(source["appKeyEnv"], "").strip()
    app_secret = os.getenv(source["appSecretEnv"], "").strip()
    if not app_key or not app_secret:
        return None, "public_fallback_missing_app_credentials"
    timestamp = int(time.time())
    signature = hashlib.sha1(f"{app_key}{timestamp}{app_secret}".encode("utf-8")).hexdigest()
    state, payload, _, _ = client.get(
        "goplus_auth",
        f"{source['baseUrl']}/token",
        payload={"app_key": app_key, "time": timestamp, "sign": signature},
    )
    value = str(((payload.get("result") or {}).get("access_token") or "")).strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return (value if state == "success" and value else None), ("app_credentials" if value else state)


def top_percent(rows) -> float | None:
    values = [number(row.get("percent")) for row in rows]
    clean = [value for value in values if value is not None]
    return sum(clean) * 100 if clean else None


def collect_supply_inputs(sample_path: Path, output_root: Path, config_path: Path) -> None:
    config, networks = config_networks(config_path)
    source = config["sources"]["goplus"]
    sample = load_jsonl(sample_path)
    output_path = output_root / "supply-inputs.jsonl"
    if output_path.exists():
        normalized_rows = []
        for row in load_jsonl(output_path):
            if row.get("schemaVersion") != "c2.1-supply-input-v0.2":
                for field in ("reportedTopHolderPct", "ownerPct", "creatorPct", "lpTopHolderPct"):
                    if row.get(field) is not None:
                        row[field] = number(row[field]) * 100
            row["schemaVersion"] = "c2.1-supply-input-v0.2"
            normalized_rows.append(row)
        write_jsonl(output_path, normalized_rows)
    completed = {
        identity
        for identity, row in load_latest(output_path).items()
        if row.get("state") in TERMINAL_STATES
    }
    client = JsonClient()
    access_token, access_mode = goplus_token(client, config)
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    for network_id in sorted({row["networkId"] for row in sample}):
        pending = [
            row for row in sample
            if row["networkId"] == network_id and (network_id, row["tokenAddress"]) not in completed
        ]
        if not pending:
            continue
        network = networks[network_id]
        if not network.get("goPlusSupported"):
            for selected in pending:
                append_jsonl(
                    output_path,
                    {
                        "schemaVersion": "c2.1-supply-input-v0.2",
                        "networkId": network_id,
                        "tokenAddress": selected["tokenAddress"],
                        "effectiveAgeBand": selected["effectiveAgeBand"],
                        "collectedAt": utc_now(),
                        "state": "unsupported",
                        "provider": "goplus",
                        "reason": "network_not_supported",
                    },
                )
            continue
        for start in range(0, len(pending), int(source["tokenBatchSize"])):
            batch = pending[start : start + int(source["tokenBatchSize"])]
            addresses = [row["tokenAddress"] for row in batch]
            encoded = urllib.parse.quote(",".join(addresses), safe=",")
            endpoint = (
                f"{source['baseUrl']}/solana/token_security?contract_addresses={encoded}"
                if network["chainType"] == "SOLANA"
                else f"{source['baseUrl']}/token_security/{network['chainId']}?contract_addresses={encoded}"
            )
            state, payload, _, _ = client.get(
                "goplus", endpoint, headers=headers, minimum_interval=float(source["minimumRequestIntervalSeconds"])
            )
            result = payload.get("result") or {}
            normalized = {
                (address.lower() if network["chainType"] == "EVM" else address): value
                for address, value in result.items()
            }
            for selected in batch:
                address = selected["tokenAddress"]
                key = address.lower() if network["chainType"] == "EVM" else address
                token = normalized.get(key) or {}
                token_state = "success" if token else "no_data" if state == "success" else state
                holders = token.get("holders") or []
                append_jsonl(
                    output_path,
                    {
                        "schemaVersion": "c2.1-supply-input-v0.2",
                        "networkId": network_id,
                        "tokenAddress": address,
                        "effectiveAgeBand": selected["effectiveAgeBand"],
                        "collectedAt": utc_now(),
                        "state": token_state,
                        "provider": "goplus",
                        "accessMode": access_mode,
                        "holderCount": token.get("holder_count"),
                        "reportedTopHolderRows": len(holders),
                        "reportedTopHolderPct": top_percent(holders),
                        "totalSupply": token.get("total_supply"),
                        "ownerPct": number(token.get("owner_percent")) * 100 if number(token.get("owner_percent")) is not None else None,
                        "creatorPct": number(token.get("creator_percent")) * 100 if number(token.get("creator_percent")) is not None else None,
                        "lpHolderCount": token.get("lp_holder_count"),
                        "lpTopHolderPct": top_percent(token.get("lp_holders") or []),
                        "isHoneypot": token.get("is_honeypot"),
                        "cannotSellAll": token.get("cannot_sell_all"),
                        "sellTax": number(token.get("sell_tax")),
                        "isMintable": token.get("is_mintable") if network["chainType"] == "EVM" else (token.get("mintable") or {}).get("status"),
                        "isFreezable": (token.get("freezable") or {}).get("status") if network["chainType"] == "SOLANA" else None,
                        "boundary": "current_snapshot_top_rows_include_contracts_and_known_entities",
                    },
                )
    collect_helius_supply(sample, output_root, client)
    probe_historical_holder_capability(sample, output_root, config, client)


def collect_helius_supply(sample, output_root: Path, client: JsonClient) -> None:
    key = os.getenv("HELIUS_API_KEY", "").strip()
    rows = [row for row in sample if row["networkId"] == "solana-mainnet"]
    output_path = output_root / "helius-supply-inputs.jsonl"
    completed = {
        identity
        for identity, row in load_latest(output_path).items()
        if row.get("state") in TERMINAL_STATES
    }
    for selected in rows:
        identity = (selected["networkId"], selected["tokenAddress"])
        if identity in completed:
            continue
        base = {
            "schemaVersion": "c2.1-helius-supply-input-v0.1",
            "networkId": selected["networkId"],
            "tokenAddress": selected["tokenAddress"],
            "effectiveAgeBand": selected["effectiveAgeBand"],
            "collectedAt": utc_now(),
            "provider": "helius_rpc",
        }
        if not key:
            append_jsonl(output_path, {**base, "state": "configuration_missing"})
            continue
        rpc = f"https://mainnet.helius-rpc.com/?api-key={key}"
        results = {}
        states = []
        calls = (
            ("supply", "getTokenSupply", [selected["tokenAddress"]]),
            ("largest", "getTokenLargestAccounts", [selected["tokenAddress"]]),
            ("accounts", "getTokenAccounts", {"mint": selected["tokenAddress"], "limit": 1}),
        )
        for name, method, params in calls:
            state, payload, _, _ = client.get(
                "helius", rpc, payload={"jsonrpc": "2.0", "id": name, "method": method, "params": params}
            )
            if payload.get("error"):
                state = "source_failure"
            states.append(state)
            results[name] = payload.get("result") or {}
        supply = (results["supply"].get("value") or {})
        largest = results["largest"].get("value") or []
        supply_amount = number(supply.get("amount"))
        largest_amount = sum(number(row.get("amount")) or 0 for row in largest)
        state = "success" if all(value == "success" for value in states) else next(value for value in states if value != "success")
        append_jsonl(
            output_path,
            {
                **base,
                "state": state,
                "supplyRaw": supply.get("amount"),
                "decimals": supply.get("decimals"),
                "largestAccountRows": len(largest),
                "largest20TokenAccountPct": (largest_amount / supply_amount * 100) if supply_amount else None,
                "tokenAccountCount": results["accounts"].get("total"),
                "boundary": "token_accounts_are_not_unique_wallet_owners",
            },
        )


def probe_historical_holder_capability(sample, output_root: Path, config, client: JsonClient) -> None:
    path = output_root / "supply-history-capability.json"
    if path.exists() or not sample:
        return
    source = config["sources"]["geckoterminal"]
    key = os.getenv(source["credentialEnv"], "").strip() or os.getenv("COINGECKO_DEMO_API_KEY", "").strip()
    selected = sample[0]
    network = next(row for row in config["networks"] if row["id"] == selected["networkId"])
    url = (
        f"{source['authenticatedBaseUrl']}/networks/{network['geckoTerminalId']}"
        f"/tokens/{urllib.parse.quote(selected['tokenAddress'])}/holders_chart?days=30"
    )
    state, payload, http, _ = client.get(
        "coingecko_onchain", url, headers={source["credentialHeader"]: key} if key else {}
    )
    status = payload.get("status") or {}
    message = str(status.get("error_message") or "")
    if http == 401 and "Analyst plan" in message:
        state = "unsupported"
    atomic_json(
        path,
        {
            "checkedAt": utc_now(),
            "provider": "coingecko_onchain_holders_chart",
            "state": state,
            "httpStatus": http,
            "reason": "requires_analyst_plan" if state == "unsupported" else None,
            "boundary": "current plan cannot supply historical holder-count series",
        },
    )


def normalized_domain(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text if "://" in text else f"https://{text}")
    domain = (parsed.hostname or "").lower()
    return domain[4:] if domain.startswith("www.") else domain


def local_project_map(database_path: Path, sample) -> dict:
    wanted = {(row["networkId"], row["tokenAddress"].lower()): row for row in sample}
    result = defaultdict(list)
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT ac.network_id, lower(ac.contract_address), p.project_id,
                   p.canonical_name, p.website_domain, p.official_repo
            FROM asset_contracts ac
            JOIN assets a ON a.asset_id = ac.asset_id
            JOIN projects p ON p.project_id = a.project_id
            """
        )
        for network_id, address, project_id, name, website, repo in rows:
            if (network_id, address) in wanted:
                result[(network_id, address)].append(
                    {"projectId": project_id, "name": name, "website": website, "officialRepo": repo}
                )
    finally:
        connection.close()
    return result


def collect_product_inputs(sample_path: Path, output_root: Path, database_path: Path) -> None:
    sample = load_jsonl(sample_path)
    market = load_latest(output_root / "market-inputs.jsonl")
    local = local_project_map(database_path, sample)
    client = JsonClient()
    state, protocols, _, _ = client.get("defillama", "https://api.llama.fi/protocols")
    protocols = protocols if state == "success" and isinstance(protocols, list) else []
    by_domain = defaultdict(list)
    for protocol in protocols:
        domain = normalized_domain(protocol.get("url"))
        if domain:
            by_domain[domain].append(protocol)
    pending_details = {}
    mappings = {}
    for selected in sample:
        identity = (selected["networkId"], selected["tokenAddress"])
        domains = {normalized_domain(url) for url in (market.get(identity) or {}).get("websites") or []}
        local_rows = local.get((identity[0], identity[1].lower())) or []
        domains.update(normalized_domain(row.get("website")) for row in local_rows)
        domains.discard("")
        matches = {row.get("slug"): row for domain in domains for row in by_domain.get(domain, []) if row.get("slug")}
        mappings[identity] = {"domains": sorted(domains), "localProjects": local_rows, "matches": list(matches.values())}
        if len(matches) == 1:
            slug = next(iter(matches))
            pending_details[slug] = None
    for slug in pending_details:
        detail_state, detail, _, _ = client.get(
            "defillama", f"https://api.llama.fi/protocol/{urllib.parse.quote(slug)}", minimum_interval=0.15
        )
        pending_details[slug] = detail if detail_state == "success" else None
    output = []
    for selected in sample:
        identity = (selected["networkId"], selected["tokenAddress"])
        mapping = mappings[identity]
        matches = mapping["matches"]
        slug = matches[0].get("slug") if len(matches) == 1 else None
        detail = pending_details.get(slug) if slug else None
        tvl = (detail or {}).get("tvl") or []
        tvl = [
            {"date": row.get("date"), "totalLiquidityUsd": number(row.get("totalLiquidityUSD"))}
            for row in tvl[-100:]
            if row.get("date") is not None and number(row.get("totalLiquidityUSD")) is not None
        ]
        output.append(
            {
                "networkId": identity[0],
                "tokenAddress": identity[1],
                "effectiveAgeBand": selected["effectiveAgeBand"],
                "collectedAt": utc_now(),
                "state": "success" if len(tvl) >= 2 else "no_data",
                "identityState": (
                    "verified_local_contract_mapping"
                    if len(matches) == 1 and mapping["localProjects"]
                    else "provisional_platform_domain_mapping"
                    if len(matches) == 1
                    else "ambiguous_domain"
                    if len(matches) > 1
                    else "unmapped"
                ),
                "localProjectMappings": mapping["localProjects"],
                "candidateDomains": mapping["domains"],
                "defiLlamaSlug": slug,
                "tvl": tvl,
                "boundary": "tvl_is_real_product_finance_input_but_not_universal_product_usage",
            }
        )
    write_jsonl(output_root / "product-inputs.jsonl", output)
    print(json.dumps({"rows": len(output), "realSeries": sum(row["state"] == "success" for row in output)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("select", "market", "quote", "supply", "product"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--age-observations", type=Path, default=AGE_REPORT / "market-observations.jsonl")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    sample_path = output_root / "sample-selection.jsonl"
    if args.action == "select":
        select_market_shadow(args.age_observations.resolve(), output_root)
    elif args.action == "market":
        collect_market_inputs(sample_path, output_root, args.config.resolve())
    elif args.action == "quote":
        collect_quotes(sample_path, output_root, args.config.resolve())
    elif args.action == "supply":
        collect_supply_inputs(sample_path, output_root, args.config.resolve())
    else:
        collect_product_inputs(sample_path, output_root, args.database.resolve())


if __name__ == "__main__":
    main()
