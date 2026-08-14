#!/usr/bin/env python3
"""Production collector for C2.1 indexed-pool activity and historical supply."""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from c2_1_db import json_text, utc_now
from c2_1_resilience import commit_cursor, cursor_decision, day_window, hour_window
from c2_1_rules import age_band, age_days, load_rules, number
from contract_tradeability import user_environment


TOTAL_SUPPLY_SELECTOR = "0x18160ddd"
DECIMALS_SELECTOR = "0x313ce567"
HOURLY_BANDS = {"age_0_2", "age_3_6"}


def normalize(network, value):
    text = str(value or "").strip()
    return text if network["chainType"] == "SOLANA" else text.lower()


def relation_address(item, key, network):
    identifier = (((item.get("relationships") or {}).get(key) or {}).get("data") or {}).get("id") or ""
    return normalize(network, identifier.split("_", 1)[1] if "_" in identifier else identifier)


def weighted_median(values):
    clean = sorted((float(value), max(0, float(weight))) for value, weight in values if number(value) is not None)
    if not clean:
        return None
    total = sum(weight for _, weight in clean)
    if total <= 0:
        ordered = [value for value, _ in clean]
        middle = len(ordered) // 2
        return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    running = 0
    for value, weight in clean:
        running += weight
        if running >= total / 2:
            return value
    return clean[-1][0]


def completed_windows(buckets, band, now=None):
    now = int((now or datetime.now(timezone.utc)).timestamp())
    unit = "hour" if band in HOURLY_BANDS else "day"
    step = 3600 if unit == "hour" else 86400
    last_complete = now // step * step - step
    ordered = sorted(timestamp for timestamp in buckets if timestamp <= last_complete)
    contiguous = []
    for timestamp in reversed(ordered):
        if not contiguous or contiguous[-1] - timestamp == step:
            contiguous.append(timestamp)
        else:
            break
    contiguous.reverse()
    if unit == "hour":
        width = min(24, len(contiguous) // 2)
        minimum = 6
    else:
        width = 3 if band in {"age_7_13", "age_14_30"} else 7
        minimum = width
    if width < minimum or len(contiguous) < width * 2:
        return None
    previous = contiguous[-2 * width : -width]
    current = contiguous[-width:]
    return {"unit": unit, "step": step, "width": width, "previous": previous, "current": current}


class RpcClient:
    def __init__(self):
        self.request_id = 0

    def call(self, url, method, params):
        self.request_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}).encode()
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "Penguin-Convexity-C2.1/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                payload = json.loads(response.read().decode())
            return ("no_data", payload) if payload.get("error") else ("success", payload)
        except urllib.error.HTTPError as error:
            return ("quota_limited" if error.code == 429 else "configuration_missing" if error.code in {401, 403} else "source_failure"), {"httpStatus": error.code}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return "source_failure", {}


def block_at(rpc, url, timestamp, cache):
    key = str(timestamp)
    if key in cache:
        return cache[key]
    state, latest = rpc.call(url, "eth_blockNumber", [])
    if state != "success":
        return {"state": state}
    low, high = 0, int(latest["result"], 16)
    while low < high:
        middle = (low + high + 1) // 2
        state, response = rpc.call(url, "eth_getBlockByNumber", [hex(middle), False])
        block = response.get("result") if state == "success" else None
        if not block:
            return {"state": state if state != "success" else "no_data"}
        if int(block["timestamp"], 16) <= timestamp:
            low = middle
        else:
            high = middle - 1
    state, response = rpc.call(url, "eth_getBlockByNumber", [hex(low), False])
    block = response.get("result") if state == "success" else None
    result = {"state": "success", "blockNumber": low, "blockTimestamp": int(block["timestamp"], 16)} if block else {"state": state if state != "success" else "no_data"}
    if result["state"] == "success":
        cache[key] = result
    return result


def historical_uint(rpc, url, token, block, selector):
    state, response = rpc.call(url, "eth_call", [{"to": token, "data": selector}, hex(block)])
    value = response.get("result") if state == "success" else None
    if not value or value == "0x":
        return ("no_data" if state == "success" else state), None
    try:
        return "success", int(value, 16)
    except (TypeError, ValueError):
        return "program_failure", None


def normalized_supply(raw, decimals):
    """Normalize integer totalSupply using the decimals returned at that block."""

    try:
        return Decimal(raw).scaleb(-int(decimals))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def supply_history(network, token, windows, rpc, cache):
    if network["chainType"] != "EVM":
        return {"state": "unsupported", "reason": "solana_historical_supply_reconstruction_not_available_in_this_collector"}
    key = user_environment("ALCHEMY_API_KEY")
    if not key:
        return {"state": "configuration_missing", "reason": "ALCHEMY_API_KEY_missing"}
    url = f"https://{network['alchemyHost']}/v2/{key}"
    previous_timestamp = windows["previous"][-1] + windows["step"] - 1
    current_timestamp = windows["current"][-1] + windows["step"] - 1
    previous_block = block_at(rpc, url, previous_timestamp, cache)
    current_block = block_at(rpc, url, current_timestamp, cache)
    if previous_block["state"] != "success" or current_block["state"] != "success":
        return {"state": previous_block["state"] if previous_block["state"] != "success" else current_block["state"], "previousBlock": previous_block, "currentBlock": current_block}
    values = {}
    for prefix, block in (("previous", previous_block), ("current", current_block)):
        supply_state, supply = historical_uint(rpc, url, token, block["blockNumber"], TOTAL_SUPPLY_SELECTOR)
        decimals_state, decimals = historical_uint(rpc, url, token, block["blockNumber"], DECIMALS_SELECTOR)
        if supply_state != "success" or decimals_state != "success" or not supply:
            return {"state": supply_state if supply_state != "success" else decimals_state}
        values[prefix + "SupplyRaw"] = supply
        values[prefix + "Decimals"] = decimals
    values.update(state="success", unitScaleStable=values["previousDecimals"] == values["currentDecimals"], previousBlock=previous_block, currentBlock=current_block)
    return values


def source_health(connection, scope, state, reason, affected=1):
    now = utc_now()
    code = {"quota_limited": "rate_limited", "source_failure": "provider_unavailable", "unsupported": "unsupported_chain", "configuration_missing": "configuration_missing", "no_data": "source_returned_no_data", "program_failure": "program_failure"}.get(state, "")
    connection.execute(
        """INSERT INTO source_health(source_id,scope_key,status,reason_code,plain_reason,affected_object_count,last_success_at,updated_at)
        VALUES('c2_1_path4',?,?,?,?,?,?,?) ON CONFLICT(source_id,scope_key) DO UPDATE SET status=excluded.status,
        reason_code=excluded.reason_code,plain_reason=excluded.plain_reason,affected_object_count=excluded.affected_object_count,
        last_success_at=COALESCE(excluded.last_success_at,source_health.last_success_at),updated_at=excluded.updated_at""",
        (scope, state, code, reason, affected, now if state == "success" else None, now),
    )


def collect_path4(connection, client, config_payload, networks, pause=None, candidate_ids=None):
    source = config_payload["sources"]["geckoterminal"]
    key = user_environment(source.get("credentialEnv", "")) or user_environment(source.get("fallbackCredentialEnv", "")) or user_environment("COINGECKO_DEMO_API_KEY")
    headers = {source["credentialHeader"]: key} if key else {}
    rules, _ = load_rules()
    selected_ids = sorted({int(value) for value in (candidate_ids or [])})
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        rows = connection.execute(
            f"""SELECT * FROM candidates
            WHERE continuity_status='candidate_asset' AND candidate_id IN ({placeholders})
            ORDER BY candidate_id""",
            tuple(selected_ids),
        ).fetchall()
    else:
        rows = connection.execute(
            """SELECT c.* FROM candidates c JOIN evaluations e ON e.candidate_id=c.candidate_id AND e.is_current=1
            WHERE e.hard_gate_status IN ('pass','stale') AND c.continuity_status='candidate_asset' ORDER BY c.candidate_id"""
        ).fetchall()
    rpc = RpcClient()
    block_cache = defaultdict(dict)
    states = defaultdict(int)
    completed = 0
    skipped = 0
    for candidate in rows:
        if pause:
            pause()
        network = networks[candidate["network_id"]]
        band = age_band(age_days(candidate["effective_t0"], utc_now()), rules)
        timeframe = "hour" if band in HOURLY_BANDS else "day"
        window_key = hour_window() if timeframe == "hour" else day_window()
        scope = str(candidate["candidate_id"])
        if cursor_decision(connection, "c2_1_path4", scope, "indexed_pool_supply", window_key)["action"] != "run":
            skipped += 1
            continue
        pools = {}
        upstream_truncated = False
        state = "success"
        for page in range(1, 11):
            query = urllib.parse.urlencode({"include": "base_token,quote_token,dex", "include_inactive_source": "true", "page": page})
            url = f"{source['authenticatedBaseUrl']}/networks/{network['geckoTerminalId']}/tokens/{urllib.parse.quote(candidate['token_address'])}/pools?{query}"
            state, response, _, _ = client.request("coingecko_path4_pools", url, headers=headers, minimum_interval=float(source["minimumRequestIntervalSeconds"]))
            if state != "success":
                break
            data = response.get("data") or []
            for item in data:
                attributes = item.get("attributes") or {}
                address = str(attributes.get("address") or "")
                if not address:
                    continue
                token = normalize(network, candidate["token_address"])
                side = "base" if relation_address(item, "base_token", network) == token else "quote" if relation_address(item, "quote_token", network) == token else "unmatched"
                if side != "unmatched":
                    pools[normalize(network, address)] = {"address": address, "side": side}
            if len(data) < 20:
                break
            if page == 10:
                upstream_truncated = True
        buckets = defaultdict(list)
        ohlcv_success = 0
        if state == "success" and pools:
            for pool in pools.values():
                query = urllib.parse.urlencode({"aggregate": 1, "limit": 200, "currency": "usd", "token": pool["side"]})
                url = f"{source['authenticatedBaseUrl']}/networks/{network['geckoTerminalId']}/pools/{urllib.parse.quote(pool['address'])}/ohlcv/{timeframe}?{query}"
                pool_state, response, _, _ = client.request("coingecko_path4_ohlcv", url, headers=headers, minimum_interval=float(source["minimumRequestIntervalSeconds"]))
                candles = (((response.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []) if pool_state == "success" else []
                if pool_state == "success" and candles:
                    ohlcv_success += 1
                    for candle in candles:
                        if len(candle) >= 6 and number(candle[4]) is not None:
                            buckets[int(candle[0])].append((number(candle[4]), number(candle[5]) or 0))
                elif state == "success":
                    state = pool_state
        aggregate = {timestamp: {"volume": sum(volume for _, volume in values), "price": weighted_median(values)} for timestamp, values in buckets.items()}
        windows = completed_windows(aggregate, band) if aggregate else None
        supply = supply_history(network, candidate["token_address"], windows, rpc, block_cache[candidate["network_id"]]) if windows else {"state": "no_data"}
        if state != "success":
            final_state = state
        elif not pools or upstream_truncated or ohlcv_success != len(pools) or not windows:
            final_state = "no_data"
        elif supply.get("state") != "success":
            final_state = supply.get("state") or "no_data"
        else:
            final_state = "success"
        previous_volume = current_volume = previous_price = current_price = None
        activity = valuation = relative = surplus = None
        if windows:
            previous_volume = sum(aggregate[x]["volume"] for x in windows["previous"]) / windows["width"]
            current_volume = sum(aggregate[x]["volume"] for x in windows["current"]) / windows["width"]
            previous_price = aggregate[windows["previous"][-1]]["price"]
            current_price = aggregate[windows["current"][-1]]["price"]
        if final_state == "success" and previous_price and current_price:
            activity = math.log((current_volume + 1) / (previous_volume + 1))
            previous_supply = normalized_supply(
                supply["previousSupplyRaw"], supply["previousDecimals"]
            )
            current_supply = normalized_supply(
                supply["currentSupplyRaw"], supply["currentDecimals"]
            )
            previous_value = (
                Decimal(str(previous_price)) * previous_supply
                if previous_supply is not None
                else None
            )
            current_value = (
                Decimal(str(current_price)) * current_supply
                if current_supply is not None
                else None
            )
            if previous_value > 0 and current_value > 0:
                valuation = math.log(float(current_value / previous_value))
                relative = activity - valuation
                surplus = activity - abs(valuation)
            else:
                final_state = "program_failure"
        window_id = f"path4:{timeframe}:{windows['current'][-1] if windows else utc_now()[:13]}"
        local_pools = connection.execute("SELECT COUNT(*) FROM candidate_pools WHERE candidate_id=?", (candidate["candidate_id"],)).fetchone()[0]
        observation_id = "c21-path4-" + hashlib.sha256(f"{candidate['candidate_id']}|{window_id}".encode()).hexdigest()[:22]
        connection.execute(
            """INSERT INTO pool_window_observations(observation_id,candidate_id,window_id,source_name,source_status,observed_at,indexed_pool_count,
              ohlcv_success_count,unindexed_discovered_pool_count,previous_average_volume_usd,current_average_volume_usd,
              previous_weighted_median_price_usd,current_weighted_median_price_usd,activity_log_change,valuation_log_change,relative_expansion,risk_adjusted_surplus,payload_json)
            VALUES(?,?,?,'CoinGecko Onchain indexed pools + direct supply',?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(candidate_id,window_id,source_name) DO UPDATE SET source_status=excluded.source_status,observed_at=excluded.observed_at,
              indexed_pool_count=excluded.indexed_pool_count,ohlcv_success_count=excluded.ohlcv_success_count,unindexed_discovered_pool_count=excluded.unindexed_discovered_pool_count,
              previous_average_volume_usd=excluded.previous_average_volume_usd,current_average_volume_usd=excluded.current_average_volume_usd,
              previous_weighted_median_price_usd=excluded.previous_weighted_median_price_usd,current_weighted_median_price_usd=excluded.current_weighted_median_price_usd,
              activity_log_change=excluded.activity_log_change,valuation_log_change=excluded.valuation_log_change,relative_expansion=excluded.relative_expansion,
              risk_adjusted_surplus=excluded.risk_adjusted_surplus,payload_json=excluded.payload_json""",
            (observation_id, candidate["candidate_id"], window_id, final_state, utc_now(), len(pools), ohlcv_success, max(0, int(local_pools) - len(pools)), previous_volume, current_volume, previous_price, current_price, activity, valuation, relative, surplus, json_text({"comparisonWindowComplete": bool(windows), "supplyHistorySuccess": supply.get("state") == "success", "unitScaleStable": supply.get("unitScaleStable"), "unitScaleUse": "normalized_per_block_not_a_current_gate", "previousSupplyRaw": str(supply.get("previousSupplyRaw")) if supply.get("previousSupplyRaw") is not None else None, "currentSupplyRaw": str(supply.get("currentSupplyRaw")) if supply.get("currentSupplyRaw") is not None else None, "previousDecimals": supply.get("previousDecimals"), "currentDecimals": supply.get("currentDecimals"), "upstreamTruncated": upstream_truncated, "boundary": "provider_indexed_pools_not_global_all_dexes"})),
        )
        if supply.get("state") == "success" and windows:
            for label, timestamp in (("previous", windows["previous"][-1]), ("current", windows["current"][-1])):
                supply_window = f"path4-supply:{timeframe}:{timestamp}"
                supply_id = "c21-direct-supply-" + hashlib.sha256(f"{candidate['candidate_id']}|{supply_window}".encode()).hexdigest()[:22]
                connection.execute(
                    """INSERT INTO supply_observations(observation_id,candidate_id,window_id,source_name,source_status,observed_at,supply_raw,decimals,payload_json)
                    VALUES(?,?,?,'Direct historical totalSupply','success',?,?,?,?) ON CONFLICT(candidate_id,window_id,source_name) DO UPDATE SET
                    observed_at=excluded.observed_at,supply_raw=excluded.supply_raw,decimals=excluded.decimals,payload_json=excluded.payload_json""",
                    (supply_id, candidate["candidate_id"], supply_window, utc_now(), str(supply[label + "SupplyRaw"]), supply[label + "Decimals"], json_text({"block": supply[label + "Block"], "boundary": "direct_historical_total_supply"})),
                )
        source_health(connection, str(candidate["candidate_id"]), final_state, "路径四输入已完成。" if final_state == "success" else "路径四部分输入不可用；未补零，也未声称全球全池。")
        connection.commit()
        commit_cursor(
            connection, "c2_1_path4", scope, "indexed_pool_supply", window_key, final_state,
            {"candidateId": candidate["candidate_id"], "timeframe": timeframe, "windowId": window_id, "poolCount": len(pools)},
        )
        states[final_state] += 1
        completed += 1
    return {"candidates": len(rows), "completed": completed, "states": dict(states), "skippedCandidates": skipped}
