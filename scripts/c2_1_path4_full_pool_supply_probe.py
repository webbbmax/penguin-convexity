#!/usr/bin/env python3
"""C2.1 planning probe for observable-pool activity and historical supply.

The tool is read-only with respect to the product database and Gate 0 artifacts.
Every long action checkpoints progress under its independent report directory.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from c2_1_strong_path_input_probe import (
    JsonClient,
    append_jsonl,
    atomic_json,
    config_networks,
    load_jsonl,
    load_latest,
    number,
    write_jsonl,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRONG_PATH_REPORT = PROJECT_ROOT / "reports" / "c2.1-strong-path-input-probe"
DEFAULT_SAMPLE = STRONG_PATH_REPORT / "sample-selection.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "c2.1-path4-full-pool-supply-probe"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "gate0-shadow-scope.json"
DEFAULT_PARTITIONS = (
    PROJECT_ROOT
    / "runtime"
    / "gate0-shadow"
    / "backfill"
    / "background"
    / "runs"
    / "gate0-solfinal-20260809T045924Z-f7bbd2"
    / "partitions"
)
HOURLY_BANDS = {"age_0_2", "age_3_6"}
TERMINAL_STATES = {"success", "no_data", "unsupported", "configuration_missing"}
USER_AGENT = "Penguin-Convexity-C2.1-Path4-Probe/0.1"
TOTAL_SUPPLY_SELECTOR = "0x18160ddd"
DECIMALS_SELECTOR = "0x313ce567"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_address(value: str, chain_type: str) -> str:
    text = str(value or "").strip()
    return text.lower() if chain_type == "EVM" else text


def scan_local_factory_pools(
    sample_path: Path, output_root: Path, config_path: Path, partitions_root: Path
) -> None:
    _, networks = config_networks(config_path)
    sample = load_jsonl(sample_path)
    wanted = defaultdict(dict)
    for row in sample:
        network = networks[row["networkId"]]
        address = normalized_address(row["tokenAddress"], network["chainType"])
        wanted[row["networkId"]][address] = row
    output_path = output_root / "local-covered-pool-events.jsonl"
    checkpoint_path = output_root / "local-pool-scan-checkpoint.json"
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists()
        else {"completedFiles": [], "matchedRows": 0, "invalidJsonRows": 0}
    )
    if checkpoint.get("completedFiles") and not output_path.exists():
        raise RuntimeError("local scan checkpoint exists but output is missing")
    completed = set(checkpoint.get("completedFiles") or [])
    files = sorted(path for path in partitions_root.rglob("*.jsonl") if path.is_file())
    for index, path in enumerate(files, start=1):
        relative = str(path.relative_to(partitions_root)).replace("\\", "/")
        if relative in completed:
            continue
        network_id = path.relative_to(partitions_root).parts[0]
        network_wanted = wanted.get(network_id) or {}
        matched = []
        invalid_json_rows = 0
        if network_wanted and path.stat().st_size:
            chain_type = networks[network_id]["chainType"]
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        invalid_json_rows += 1
                        continue
                    event_tokens = {
                        normalized_address(address, chain_type)
                        for address in event.get("tokenAddresses") or []
                    }
                    for token in event_tokens.intersection(network_wanted):
                        matched.append(
                            {
                                "networkId": network_id,
                                "tokenAddress": network_wanted[token]["tokenAddress"],
                                "poolAddress": event.get("poolId"),
                                "dexIds": event.get("dexIds") or [],
                                "blockNumber": event.get("blockNumber"),
                                "blockTimestamp": event.get("blockTimestamp"),
                                "source": "gate0_covered_factory_event",
                            }
                        )
        for row in matched:
            append_jsonl(output_path, row)
        completed.add(relative)
        checkpoint = {
            "schemaVersion": "c2.1-path4-local-pool-scan-v0.1",
            "updatedAt": utc_now(),
            "partitionsRoot": str(partitions_root.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "totalFiles": len(files),
            "completedFiles": sorted(completed),
            "matchedRows": int(checkpoint.get("matchedRows") or 0) + len(matched),
            "invalidJsonRows": int(checkpoint.get("invalidJsonRows") or 0)
            + invalid_json_rows,
            "complete": len(completed) == len(files),
        }
        atomic_json(checkpoint_path, checkpoint)
        if index % 20 == 0 or matched:
            print(
                f"local-pool-scan {len(completed)}/{len(files)} matched={checkpoint['matchedRows']}",
                flush=True,
            )
    events = load_jsonl(output_path)
    unique = {
        (row["networkId"], row["tokenAddress"], row.get("poolAddress"))
        for row in events
        if row.get("poolAddress")
    }
    atomic_json(
        output_root / "local-pool-scan-summary.json",
        {
            "completedAt": utc_now(),
            "files": len(files),
            "bytes": sum(path.stat().st_size for path in files),
            "matchedEventRows": len(events),
            "uniqueTokenPools": len(unique),
            "invalidJsonRows": int(checkpoint.get("invalidJsonRows") or 0),
            "state": "success"
            if int(checkpoint.get("invalidJsonRows") or 0) == 0
            else "program_failure",
            "boundary": "covered_factory_events_only_not_all_dexes",
        },
    )


def relationship_address(pool, side, chain_type):
    identifier = (
        ((((pool.get("relationships") or {}).get(side) or {}).get("data") or {}).get("id"))
        or ""
    )
    address = identifier.split("_", 1)[1] if "_" in identifier else identifier
    return normalized_address(address, chain_type)


def collect_gecko_pools(sample_path: Path, output_root: Path, config_path: Path) -> None:
    config, networks = config_networks(config_path)
    source = config["sources"]["geckoterminal"]
    key = (
        os.getenv(source["credentialEnv"], "").strip()
        or os.getenv(source["fallbackCredentialEnv"], "").strip()
        or os.getenv("COINGECKO_DEMO_API_KEY", "").strip()
    )
    headers = {source["credentialHeader"]: key} if key else {}
    output_path = output_root / "gecko-pool-enumeration.jsonl"
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
        network = networks[selected["networkId"]]
        token = selected["tokenAddress"]
        pools = {}
        page_states = []
        page_count = 0
        last_page_rows = 0
        for page in range(1, 11):
            query = urllib.parse.urlencode(
                {
                    "include": "base_token,quote_token,dex",
                    "include_inactive_source": "true",
                    "page": page,
                }
            )
            url = (
                f"{source['authenticatedBaseUrl']}/networks/{network['geckoTerminalId']}"
                f"/tokens/{urllib.parse.quote(token)}/pools?{query}"
            )
            state, payload, _, _ = client.get(
                "coingecko_onchain",
                url,
                headers=headers,
                minimum_interval=float(source["minimumRequestIntervalSeconds"]),
            )
            page_states.append(state)
            if state != "success":
                break
            page_count = page
            data = payload.get("data") or []
            last_page_rows = len(data)
            for pool in data:
                attributes = pool.get("attributes") or {}
                pool_address = attributes.get("address")
                if not pool_address:
                    continue
                chain_type = network["chainType"]
                normalized_token = normalized_address(token, chain_type)
                token_side = (
                    "base"
                    if normalized_token == relationship_address(pool, "base_token", chain_type)
                    else "quote"
                    if normalized_token == relationship_address(pool, "quote_token", chain_type)
                    else "extra_quote"
                    if normalized_token
                    in {
                        normalized_address(item.get("id", "").split("_", 1)[-1], chain_type)
                        for item in ((((pool.get("relationships") or {}).get("quote_tokens") or {}).get("data")) or [])
                    }
                    else None
                )
                pools[normalized_address(pool_address, chain_type)] = {
                    "poolAddress": pool_address,
                    "tokenSide": token_side,
                    "reserveUsd": number(attributes.get("reserve_in_usd")),
                    "volumeH24Usd": number((attributes.get("volume_usd") or {}).get("h24")),
                    "lastTradeTimestamp": attributes.get("last_trade_timestamp"),
                    "poolCreatedAt": attributes.get("pool_created_at"),
                }
            if len(data) < 20:
                break
        upstream_truncated = page_count == 10 and last_page_rows == 20
        terminal_state = next(
            (item for item in page_states if item != "success"),
            "success" if page_states else "program_failure",
        )
        append_jsonl(
            output_path,
            {
                "networkId": selected["networkId"],
                "tokenAddress": token,
                "effectiveAgeBand": selected["effectiveAgeBand"],
                "collectedAt": utc_now(),
                "state": terminal_state,
                "pageStates": page_states,
                "pagesRead": page_count,
                "lastPageRows": last_page_rows,
                "upstreamTruncated": upstream_truncated,
                "pools": list(pools.values()),
                "poolCount": len(pools),
                "boundary": "coingecko_top_pools_max_20_per_page_max_10_pages_current_plan",
            },
        )
        print(
            f"gecko-pools {selected['networkId']} pools={len(pools)} pages={page_count} truncated={upstream_truncated}",
            flush=True,
        )


def build_observable_pool_union(sample_path: Path, output_root: Path, config_path: Path) -> None:
    _, networks = config_networks(config_path)
    local = defaultdict(dict)
    for row in load_jsonl(output_root / "local-covered-pool-events.jsonl"):
        if not row.get("poolAddress"):
            continue
        network = networks[row["networkId"]]
        key = normalized_address(row["poolAddress"], network["chainType"])
        local[(row["networkId"], row["tokenAddress"])][key] = row
    gecko = load_latest(output_root / "gecko-pool-enumeration.jsonl")
    rows = []
    for selected in load_jsonl(sample_path):
        identity = (selected["networkId"], selected["tokenAddress"])
        network = networks[selected["networkId"]]
        chain_type = network["chainType"]
        merged = {}
        for key, event in local.get(identity, {}).items():
            merged[key] = {
                "poolAddress": event["poolAddress"],
                "sources": ["gate0_covered_factory_event"],
                "tokenSide": None,
                "reserveUsd": None,
            }
        gecko_row = gecko.get(identity) or {}
        for pool in gecko_row.get("pools") or []:
            key = normalized_address(pool["poolAddress"], chain_type)
            if key in merged:
                merged[key]["sources"].append("coingecko_top_pools")
                merged[key].update(
                    {
                        "tokenSide": pool.get("tokenSide"),
                        "reserveUsd": pool.get("reserveUsd"),
                        "volumeH24Usd": pool.get("volumeH24Usd"),
                    }
                )
            else:
                merged[key] = {**pool, "sources": ["coingecko_top_pools"]}
        best_pool = selected["bestPair"]["pairAddress"]
        best_key = normalized_address(best_pool, chain_type)
        if best_key not in merged:
            merged[best_key] = {
                "poolAddress": best_pool,
                "sources": ["selected_market_pair"],
                "tokenSide": selected["bestPair"].get("tokenSide"),
                "reserveUsd": selected["bestPair"].get("liquidityUsd"),
                "volumeH24Usd": selected["bestPair"].get("volumeH24Usd"),
            }
        rows.append(
            {
                "networkId": selected["networkId"],
                "tokenAddress": selected["tokenAddress"],
                "effectiveAgeBand": selected["effectiveAgeBand"],
                "effectiveAgeDays": selected["effectiveAgeDays"],
                "state": "success" if merged else "no_data",
                "localCoveredFactoryPoolCount": len(local.get(identity, {})),
                "geckoTopPoolCount": len(gecko_row.get("pools") or []),
                "geckoEnumerationState": gecko_row.get("state") or "no_data",
                "observablePoolCount": len(merged),
                "geckoUpstreamTruncated": bool(gecko_row.get("upstreamTruncated")),
                "pools": list(merged.values()),
                "globalAllPoolsClaimAllowed": False,
                "boundary": "observable_union_of_gate0_covered_factories_and_coingecko_top_pools",
            }
        )
    write_jsonl(output_root / "observable-pools.jsonl", rows)
    print(json.dumps(dict(Counter(row["observablePoolCount"] for row in rows)), ensure_ascii=False))


def pool_observation_key(row):
    return (
        row["networkId"],
        row["tokenAddress"],
        row["poolAddress"],
        row["timeframe"],
    )


def load_pool_observations(path: Path):
    latest = {}
    for row in load_jsonl(path):
        latest[pool_observation_key(row)] = row
    return latest


def collect_pool_ohlcv(output_root: Path, config_path: Path) -> None:
    config, networks = config_networks(config_path)
    source = config["sources"]["geckoterminal"]
    key = (
        os.getenv(source["credentialEnv"], "").strip()
        or os.getenv(source["fallbackCredentialEnv"], "").strip()
        or os.getenv("COINGECKO_DEMO_API_KEY", "").strip()
    )
    headers = {source["credentialHeader"]: key} if key else {}
    output_path = output_root / "pool-ohlcv.jsonl"
    completed = {
        identity
        for identity, row in load_pool_observations(output_path).items()
        if row.get("state") in TERMINAL_STATES
    }
    client = JsonClient()
    tasks = []
    for token_row in load_jsonl(output_root / "observable-pools.jsonl"):
        timeframe = "hour" if token_row["effectiveAgeBand"] in HOURLY_BANDS else "day"
        for pool in token_row.get("pools") or []:
            task = {
                "networkId": token_row["networkId"],
                "tokenAddress": token_row["tokenAddress"],
                "effectiveAgeBand": token_row["effectiveAgeBand"],
                "poolAddress": pool["poolAddress"],
                "sources": pool.get("sources") or [],
                "timeframe": timeframe,
            }
            if pool_observation_key(task) not in completed:
                tasks.append(task)
    for index, task in enumerate(tasks, start=1):
        network = networks[task["networkId"]]
        query = urllib.parse.urlencode(
            {
                "aggregate": 1,
                "limit": 336 if task["timeframe"] == "hour" else 100,
                "currency": "usd",
                "token": task["tokenAddress"],
                "include_empty_intervals": "true",
            }
        )
        url = (
            f"{source['authenticatedBaseUrl']}/networks/{network['geckoTerminalId']}"
            f"/pools/{urllib.parse.quote(task['poolAddress'])}/ohlcv/{task['timeframe']}?{query}"
        )
        state, payload, http_status, attempts = client.get(
            "coingecko_onchain",
            url,
            headers=headers,
            minimum_interval=float(source["minimumRequestIntervalSeconds"]),
        )
        candles = (
            (((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or [])
            if state == "success"
            else []
        )
        if state == "success" and not candles:
            state = "no_data"
        append_jsonl(
            output_path,
            {
                **task,
                "collectedAt": utc_now(),
                "state": state,
                "httpStatus": http_status,
                "attempts": attempts,
                "candles": candles,
                "candleCount": len(candles),
                "boundary": "pool_gross_volume_not_unique_user_notional",
            },
        )
        print(
            f"pool-ohlcv {index}/{len(tasks)} {task['networkId']} {task['timeframe']} {state} rows={len(candles)}",
            flush=True,
        )


def weighted_median(values):
    clean = [(float(value), max(0.0, float(weight))) for value, weight in values if value is not None]
    if not clean:
        return None
    total = sum(weight for _, weight in clean)
    if total <= 0:
        ordered = sorted(value for value, _ in clean)
        midpoint = len(ordered) // 2
        return (
            ordered[midpoint]
            if len(ordered) % 2
            else (ordered[midpoint - 1] + ordered[midpoint]) / 2
        )
    threshold = total / 2
    running = 0.0
    for value, weight in sorted(clean):
        running += weight
        if running >= threshold:
            return value
    return sorted(clean)[-1][0]


def trailing_contiguous(buckets, step):
    ordered = sorted(buckets)
    if not ordered:
        return []
    result = [ordered[-1]]
    for timestamp in reversed(ordered[:-1]):
        if result[-1] - timestamp != step:
            break
        result.append(timestamp)
    return list(reversed(result))


def positive_price(value):
    parsed = number(value)
    return parsed if parsed is not None and parsed > 0 else None


def aggregate_activity(output_root: Path) -> None:
    observable = {
        (row["networkId"], row["tokenAddress"]): row
        for row in load_jsonl(output_root / "observable-pools.jsonl")
    }
    observations = load_pool_observations(output_root / "pool-ohlcv.jsonl")
    now = int(time.time())
    rows = []
    for identity, token_row in observable.items():
        timeframe = "hour" if token_row["effectiveAgeBand"] in HOURLY_BANDS else "day"
        step = 3600 if timeframe == "hour" else 86400
        last_complete = (now // step) * step - step
        pool_rows = [
            row
            for row in observations.values()
            if (row["networkId"], row["tokenAddress"]) == identity
            and row["timeframe"] == timeframe
        ]
        successful = [row for row in pool_rows if row["state"] == "success"]
        buckets = defaultdict(list)
        per_pool_current = defaultdict(float)
        for pool in successful:
            for candle in pool.get("candles") or []:
                if len(candle) < 6 or int(candle[0]) > last_complete:
                    continue
                buckets[int(candle[0])].append(
                    {
                        "poolAddress": pool["poolAddress"],
                        "close": positive_price(candle[4]),
                        "volume": number(candle[5]) or 0.0,
                    }
                )
        aggregate = {}
        for timestamp, values in buckets.items():
            aggregate[timestamp] = {
                "timestamp": timestamp,
                "volumeUsd": sum(item["volume"] for item in values),
                "priceUsd": weighted_median(
                    [(item["close"], item["volume"]) for item in values if item["close"] is not None]
                ),
                "contributingPools": len({item["poolAddress"] for item in values}),
            }
        contiguous = trailing_contiguous(aggregate, step)
        if timeframe == "hour":
            width = min(24, len(contiguous) // 2)
            minimum_width = 6
        else:
            width = 3 if token_row["effectiveAgeBand"] in {"age_7_13", "age_14_30"} else 7
            minimum_width = width
        state = "success" if width >= minimum_width and len(contiguous) >= 2 * width else "no_data"
        metrics = {}
        if state == "success":
            previous_times = contiguous[-2 * width : -width]
            current_times = contiguous[-width:]
            previous_volume = sum(aggregate[item]["volumeUsd"] for item in previous_times) / width
            current_volume = sum(aggregate[item]["volumeUsd"] for item in current_times) / width
            previous_price = aggregate[previous_times[-1]]["priceUsd"]
            current_price = aggregate[current_times[-1]]["priceUsd"]
            if previous_price is None or current_price is None:
                state = "no_data"
        if state == "success":
            for pool in successful:
                for candle in pool.get("candles") or []:
                    if len(candle) >= 6 and int(candle[0]) in current_times:
                        per_pool_current[pool["poolAddress"]] += number(candle[5]) or 0.0
            total_current_volume = sum(per_pool_current.values())
            shares = [value / total_current_volume for value in per_pool_current.values() if total_current_volume > 0]
            metrics = {
                "windowUnit": timeframe,
                "windowWidth": width,
                "previousStartTimestamp": previous_times[0],
                "previousEndTimestamp": previous_times[-1] + step - 1,
                "currentStartTimestamp": current_times[0],
                "currentEndTimestamp": current_times[-1] + step - 1,
                "previousAverageVolumeUsd": previous_volume,
                "currentAverageVolumeUsd": current_volume,
                "activityLogChange": math.log((current_volume + 1) / (previous_volume + 1)),
                "previousPriceUsd": previous_price,
                "currentPriceUsd": current_price,
                "activePoolsCurrentWindow": sum(value > 0 for value in per_pool_current.values()),
                "topPoolVolumeSharePct": max(shares) * 100 if shares else None,
                "poolVolumeHhi": sum(share * share for share in shares) if shares else None,
            }
        observable_count = int(token_row.get("observablePoolCount") or 0)
        success_count = len(successful)
        indexed_rows = [
            row for row in pool_rows if "coingecko_top_pools" in (row.get("sources") or [])
        ]
        indexed_success_count = sum(row["state"] == "success" for row in indexed_rows)
        indexed_count = len(indexed_rows)
        coverage_state = (
            "upstream_truncated"
            if token_row.get("geckoUpstreamTruncated")
            else "gecko_enumeration_incomplete"
            if token_row.get("geckoEnumerationState") != "success"
            else "complete_for_observable_set"
            if success_count == observable_count
            else "partial_ohlcv"
        )
        indexed_coverage_state = (
            "upstream_truncated"
            if token_row.get("geckoUpstreamTruncated")
            else "gecko_enumeration_incomplete"
            if token_row.get("geckoEnumerationState") != "success"
            else "complete_for_indexed_set"
            if indexed_count > 0 and indexed_success_count == indexed_count
            else "partial_indexed_ohlcv"
        )
        rows.append(
            {
                "networkId": identity[0],
                "tokenAddress": identity[1],
                "effectiveAgeBand": token_row["effectiveAgeBand"],
                "calculatedAt": utc_now(),
                "state": state,
                "coverageState": coverage_state,
                "indexedCoverageState": indexed_coverage_state,
                "observablePools": observable_count,
                "coingeckoIndexedPools": indexed_count,
                "successfulIndexedOhlcvPools": indexed_success_count,
                "localOnlyDiscoveredPools": sum(
                    "coingecko_top_pools" not in (row.get("sources") or [])
                    for row in pool_rows
                ),
                "successfulOhlcvPools": success_count,
                "noDataOhlcvPools": sum(row["state"] == "no_data" for row in pool_rows),
                "sourceFailurePools": sum(row["state"] == "source_failure" for row in pool_rows),
                "contiguousIntervals": len(contiguous),
                **metrics,
                "boundary": "observable_pool_gross_volume_not_global_all_dexes_not_unique_wallet_activity",
            }
        )
    write_jsonl(output_root / "activity-aggregate.jsonl", rows)


class RpcClient:
    def __init__(self):
        self.request_number = 0

    def call(self, url, method, params):
        self.request_number += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self.request_number, "method": method, "params": params}
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        for attempt, delay in enumerate((0, 1, 3), start=1):
            if delay:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(request, timeout=40) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    if payload.get("error"):
                        return "no_data", payload
                    return "success", payload
            except urllib.error.HTTPError as error:
                state = (
                    "quota_limited"
                    if error.code == 429
                    else "configuration_missing"
                    if error.code in (401, 403)
                    else "source_failure"
                )
                if state == "configuration_missing" or attempt == 3:
                    return state, {"httpStatus": error.code}
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                if attempt == 3:
                    return "source_failure", {}
        return "program_failure", {}


def resolve_block_at_or_before(rpc, url, target_timestamp, cache):
    cache_key = str(target_timestamp)
    if cache_key in cache:
        return cache[cache_key]
    state, latest_payload = rpc.call(url, "eth_blockNumber", [])
    if state != "success":
        result = {"state": state}
        return result
    low, high = 0, int(latest_payload["result"], 16)
    best_timestamp = None
    while low < high:
        middle = (low + high + 1) // 2
        state, payload = rpc.call(url, "eth_getBlockByNumber", [hex(middle), False])
        block = payload.get("result") if state == "success" else None
        if not block:
            result = {"state": state if state != "success" else "no_data"}
            return result
        timestamp = int(block["timestamp"], 16)
        if timestamp <= target_timestamp:
            low = middle
            best_timestamp = timestamp
        else:
            high = middle - 1
    state, payload = rpc.call(url, "eth_getBlockByNumber", [hex(low), False])
    block = payload.get("result") if state == "success" else None
    if not block:
        result = {"state": state if state != "success" else "no_data"}
    else:
        timestamp = int(block["timestamp"], 16)
        result = {
            "state": "success",
            "blockNumber": low,
            "blockTimestamp": timestamp,
            "targetTimestamp": target_timestamp,
            "lagSeconds": target_timestamp - timestamp,
        }
    if result.get("state") == "success":
        cache[cache_key] = result
    return result


def historical_uint(rpc, url, token_address, block_number, selector):
    state, payload = rpc.call(
        url,
        "eth_call",
        [{"to": token_address, "data": selector}, hex(block_number)],
    )
    value = payload.get("result") if state == "success" else None
    if not value or value == "0x":
        return "no_data", None
    try:
        supply = int(value, 16)
    except (TypeError, ValueError):
        return "program_failure", None
    return "success", supply


def historical_total_supply(rpc, url, token_address, block_number):
    state, value = historical_uint(
        rpc, url, token_address, block_number, TOTAL_SUPPLY_SELECTOR
    )
    if state != "success":
        return state, None
    return ("success", value) if value > 0 else ("no_data", None)


def supply_category(change_pct):
    if change_pct is None:
        return None
    absolute = abs(change_pct)
    if absolute == 0:
        return "exact_stable"
    if absolute <= 0.1:
        return "near_stable_le_0_1pct"
    if absolute <= 1:
        return "minor_change_le_1pct"
    return "material_change_gt_1pct"


def collect_evm_supply_history(output_root: Path, config_path: Path) -> None:
    _, networks = config_networks(config_path)
    activity = load_jsonl(output_root / "activity-aggregate.jsonl")
    output_path = output_root / "supply-history.jsonl"
    completed = {
        identity
        for identity, row in load_latest(output_path).items()
        if row.get("state") in TERMINAL_STATES
        and (
            row.get("provider") != "alchemy_archive_eth_call"
            or "valuationComparable" in row
        )
    }
    key = os.getenv("ALCHEMY_API_KEY", "").strip()
    rpc = RpcClient()
    block_cache_path = output_root / "evm-block-time-map.json"
    block_cache = (
        json.loads(block_cache_path.read_text(encoding="utf-8"))
        if block_cache_path.exists()
        else {}
    )
    for row in activity:
        identity = (row["networkId"], row["tokenAddress"])
        if identity in completed or row["state"] != "success":
            continue
        network = networks[row["networkId"]]
        if network["chainType"] != "EVM":
            continue
        base = {
            "networkId": row["networkId"],
            "tokenAddress": row["tokenAddress"],
            "effectiveAgeBand": row["effectiveAgeBand"],
            "collectedAt": utc_now(),
            "provider": "alchemy_archive_eth_call",
        }
        if not key:
            append_jsonl(output_path, {**base, "state": "configuration_missing"})
            continue
        url = f"https://{network['alchemyHost']}/v2/{key}"
        network_cache = block_cache.setdefault(row["networkId"], {})
        previous_block = resolve_block_at_or_before(
            rpc, url, int(row["previousEndTimestamp"]), network_cache
        )
        current_block = resolve_block_at_or_before(
            rpc, url, int(row["currentEndTimestamp"]), network_cache
        )
        atomic_json(block_cache_path, block_cache)
        if previous_block.get("state") != "success" or current_block.get("state") != "success":
            state = (
                previous_block.get("state")
                if previous_block.get("state") != "success"
                else current_block.get("state")
            )
            append_jsonl(
                output_path,
                {**base, "state": state, "previousBlock": previous_block, "currentBlock": current_block},
            )
            continue
        previous_state, previous_supply = historical_total_supply(
            rpc, url, row["tokenAddress"], previous_block["blockNumber"]
        )
        current_state, current_supply = historical_total_supply(
            rpc, url, row["tokenAddress"], current_block["blockNumber"]
        )
        state = (
            "success"
            if previous_state == current_state == "success"
            else previous_state
            if previous_state != "success"
            else current_state
        )
        change_pct = (
            (current_supply / previous_supply - 1) * 100
            if state == "success" and previous_supply
            else None
        )
        previous_decimals_state, previous_decimals = historical_uint(
            rpc,
            url,
            row["tokenAddress"],
            previous_block["blockNumber"],
            DECIMALS_SELECTOR,
        )
        current_decimals_state, current_decimals = historical_uint(
            rpc,
            url,
            row["tokenAddress"],
            current_block["blockNumber"],
            DECIMALS_SELECTOR,
        )
        unit_scale_state = (
            "success"
            if previous_decimals_state == current_decimals_state == "success"
            else previous_decimals_state
            if previous_decimals_state != "success"
            else current_decimals_state
        )
        valuation_comparable = (
            state == "success"
            and unit_scale_state == "success"
            and previous_decimals == current_decimals
        )
        append_jsonl(
            output_path,
            {
                **base,
                "state": state,
                "previousBlock": previous_block,
                "currentBlock": current_block,
                "previousSupplyRaw": str(previous_supply) if previous_supply is not None else None,
                "currentSupplyRaw": str(current_supply) if current_supply is not None else None,
                "previousDecimals": previous_decimals,
                "currentDecimals": current_decimals,
                "unitScaleState": unit_scale_state,
                "unitScaleStable": unit_scale_state == "success"
                and previous_decimals == current_decimals,
                "valuationComparable": valuation_comparable,
                "supplyChangePct": change_pct,
                "supplyStabilityCategory": supply_category(change_pct),
                "boundary": "historical_total_supply_direct_call_no_current_value_backfill",
            },
        )
        print(f"evm-supply {row['networkId']} {state}", flush=True)


def helius_current_supply(rpc, url, mint):
    state, payload = rpc.call(url, "getTokenSupply", [mint])
    value = ((payload.get("result") or {}).get("value") or {}) if state == "success" else {}
    try:
        return state, int(value["amount"]), int(value["decimals"])
    except (KeyError, TypeError, ValueError):
        return ("no_data" if state == "success" else state), None, None


def helius_supply_events(key, mint, event_type, start_timestamp, end_timestamp):
    base = f"https://api-mainnet.helius-rpc.com/v0/addresses/{mint}/transactions"
    rows = []
    after_signature = None
    for _ in range(100):
        query = {
            "api-key": key,
            "type": event_type,
            "gte-time": start_timestamp,
            "lte-time": end_timestamp,
            "sort-order": "asc",
            "limit": 100,
        }
        if after_signature:
            query["after-signature"] = after_signature
        url = f"{base}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                page = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            state = (
                "quota_limited"
                if error.code == 429
                else "configuration_missing"
                if error.code in (401, 403)
                else "source_failure"
            )
            return state, rows, False
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return "source_failure", rows, False
        if not isinstance(page, list):
            return "source_failure", rows, False
        rows.extend(page)
        if len(page) < 100:
            return "success", rows, False
        signature = page[-1].get("signature")
        if not signature or signature == after_signature:
            return "source_failure", rows, True
        after_signature = signature
    return "quota_limited", rows, True


def event_raw_amount(event, mint, decimals):
    total = Decimal(0)
    for transfer in event.get("tokenTransfers") or []:
        if transfer.get("mint") != mint:
            continue
        try:
            total += Decimal(str(transfer.get("tokenAmount") or 0))
        except InvalidOperation:
            continue
    return int((total * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_HALF_UP))


def collect_solana_supply_history(output_root: Path) -> None:
    activity = load_jsonl(output_root / "activity-aggregate.jsonl")
    output_path = output_root / "supply-history.jsonl"
    completed = {
        identity
        for identity, row in load_latest(output_path).items()
        if row.get("state") in TERMINAL_STATES
        and (
            row.get("provider") != "helius_mint_burn_reconstruction"
            or "valuationComparable" in row
        )
    }
    key = os.getenv("HELIUS_API_KEY", "").strip()
    rpc = RpcClient()
    for row in activity:
        identity = (row["networkId"], row["tokenAddress"])
        if identity in completed or row["networkId"] != "solana-mainnet" or row["state"] != "success":
            continue
        base = {
            "networkId": row["networkId"],
            "tokenAddress": row["tokenAddress"],
            "effectiveAgeBand": row["effectiveAgeBand"],
            "collectedAt": utc_now(),
            "provider": "helius_mint_burn_reconstruction",
        }
        if not key:
            append_jsonl(output_path, {**base, "state": "configuration_missing"})
            continue
        rpc_url = f"https://mainnet.helius-rpc.com/?api-key={key}"
        supply_state, current_supply, decimals = helius_current_supply(
            rpc, rpc_url, row["tokenAddress"]
        )
        end = int(time.time())
        mint_state, mint_events, mint_truncated = helius_supply_events(
            key, row["tokenAddress"], "MINT_TO", int(row["previousEndTimestamp"]), end
        )
        burn_state, burn_events, burn_truncated = helius_supply_events(
            key, row["tokenAddress"], "BURN", int(row["previousEndTimestamp"]), end
        )
        states = [supply_state, mint_state, burn_state]
        state = "success" if all(item == "success" for item in states) and not (mint_truncated or burn_truncated) else next((item for item in states if item != "success"), "quota_limited")
        previous_supply = current_window_supply = None
        if state == "success":
            def net_after(timestamp):
                minted = sum(
                    event_raw_amount(event, row["tokenAddress"], decimals)
                    for event in mint_events
                    if int(event.get("timestamp") or 0) > timestamp
                )
                burned = sum(
                    event_raw_amount(event, row["tokenAddress"], decimals)
                    for event in burn_events
                    if int(event.get("timestamp") or 0) > timestamp
                )
                return minted - burned

            previous_supply = current_supply - net_after(int(row["previousEndTimestamp"]))
            current_window_supply = current_supply - net_after(int(row["currentEndTimestamp"]))
            if previous_supply <= 0 or current_window_supply <= 0:
                state = "program_failure"
        change_pct = (
            (current_window_supply / previous_supply - 1) * 100
            if state == "success" and previous_supply
            else None
        )
        append_jsonl(
            output_path,
            {
                **base,
                "state": state,
                "snapshotSupplyRaw": str(current_supply) if current_supply is not None else None,
                "previousSupplyRaw": str(previous_supply) if previous_supply is not None else None,
                "currentSupplyRaw": str(current_window_supply) if current_window_supply is not None else None,
                "decimals": decimals,
                "mintEvents": len(mint_events),
                "burnEvents": len(burn_events),
                "eventHistoryTruncated": mint_truncated or burn_truncated,
                "supplyChangePct": change_pct,
                "supplyStabilityCategory": supply_category(change_pct),
                "unitScaleState": "success",
                "unitScaleStable": True,
                "valuationComparable": state == "success",
                "boundary": "reconstructed_from_helius_mint_to_and_burn_events",
            },
        )


def calculate_path4(output_root: Path) -> None:
    activity = load_latest(output_root / "activity-aggregate.jsonl")
    supply = load_latest(output_root / "supply-history.jsonl")
    rows = []
    for identity, activity_row in activity.items():
        supply_row = supply.get(identity) or {}
        state = (
            "success"
            if activity_row.get("state") == supply_row.get("state") == "success"
            else activity_row.get("state")
            if activity_row.get("state") != "success"
            else supply_row.get("state") or "no_data"
        )
        metrics = {}
        if state == "success":
            if not supply_row.get("valuationComparable"):
                state = "no_data"
        if state == "success":
            previous_supply = int(supply_row["previousSupplyRaw"])
            current_supply = int(supply_row["currentSupplyRaw"])
            previous_value = float(activity_row["previousPriceUsd"]) * previous_supply
            current_value = float(activity_row["currentPriceUsd"]) * current_supply
            if previous_value > 0 and current_value > 0:
                valuation_change = math.log(current_value / previous_value)
                activity_change = float(activity_row["activityLogChange"])
                metrics = {
                    "valuationLogChange": valuation_change,
                    "relativeExpansion": activity_change - valuation_change,
                    "riskAdjustedSurplus": activity_change - abs(valuation_change),
                }
            else:
                state = "program_failure"
        rows.append(
            {
                "networkId": identity[0],
                "tokenAddress": identity[1],
                "effectiveAgeBand": activity_row.get("effectiveAgeBand"),
                "calculatedAt": utc_now(),
                "state": state,
                "coverageState": activity_row.get("coverageState"),
                "indexedCoverageState": activity_row.get("indexedCoverageState"),
                "path4InputUsable": state == "success"
                and activity_row.get("coverageState") == "complete_for_observable_set",
                "indexedPoolPath4InputUsable": state == "success"
                and activity_row.get("indexedCoverageState")
                == "complete_for_indexed_set",
                "activityLogChange": activity_row.get("activityLogChange"),
                "supplyChangePct": supply_row.get("supplyChangePct"),
                "supplyStabilityCategory": supply_row.get("supplyStabilityCategory"),
                "topPoolVolumeSharePct": activity_row.get("topPoolVolumeSharePct"),
                "poolVolumeHhi": activity_row.get("poolVolumeHhi"),
                **metrics,
                "thresholdStatus": "not_frozen_inputs_only",
                "boundary": "observable_pool_activity_and_historical_total_supply_not_global_all_dexes",
            }
        )
    write_jsonl(output_root / "path4-inputs.jsonl", rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "local-pools",
            "gecko-pools",
            "pool-union",
            "ohlcv",
            "aggregate",
            "evm-supply",
            "solana-supply",
            "path4",
        ),
    )
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--partitions-root", type=Path, default=DEFAULT_PARTITIONS)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    if args.action == "local-pools":
        scan_local_factory_pools(
            args.sample.resolve(), output_root, args.config.resolve(), args.partitions_root.resolve()
        )
    elif args.action == "gecko-pools":
        collect_gecko_pools(args.sample.resolve(), output_root, args.config.resolve())
    elif args.action == "pool-union":
        build_observable_pool_union(args.sample.resolve(), output_root, args.config.resolve())
    elif args.action == "ohlcv":
        collect_pool_ohlcv(output_root, args.config.resolve())
    elif args.action == "aggregate":
        aggregate_activity(output_root)
    elif args.action == "evm-supply":
        collect_evm_supply_history(output_root, args.config.resolve())
    elif args.action == "solana-supply":
        collect_solana_supply_history(output_root)
    else:
        calculate_path4(output_root)


if __name__ == "__main__":
    main()
