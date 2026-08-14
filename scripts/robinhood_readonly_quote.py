#!/usr/bin/env python3
"""Read-only Uniswap V2/V3/V4 pool quotes for Robinhood Chain."""

from __future__ import annotations

from typing import Any


CHAIN_ID = 4663
NATIVE_CURRENCY = "0x0000000000000000000000000000000000000000"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
V2_ROUTER = "0x89e5db8b5aa49aa85ac63f691524311aeb649eba"
V3_QUOTER = "0x33e885ed0ec9bf04ecfb19341582aadcb4c8a9e7"
V4_POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
V4_QUOTER = "0x8dc178efb8111bb0973dd9d722ebeff267c98f94"

TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
FEE_SELECTOR = "0xddca3f43"
DECIMALS_SELECTOR = "0x313ce567"
V2_GET_AMOUNTS_OUT_SELECTOR = "0xd06ca61f"
V3_QUOTE_EXACT_INPUT_SINGLE_SELECTOR = "0xc6a5026a"
V4_QUOTE_EXACT_INPUT_SINGLE_SELECTOR = "0xaa9d21cb"
V4_INITIALIZE_TOPIC = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"


def _hex(value: Any) -> str:
    return str(value or "").lower().removeprefix("0x")


def _word(value: int) -> str:
    return f"{int(value) % (1 << 256):064x}"


def _address_word(value: str) -> str:
    normalized = _hex(value)
    if len(normalized) != 40:
        raise ValueError("invalid EVM address")
    return normalized.rjust(64, "0")


def _decode_int(value: Any, word_index: int = 0) -> int | None:
    raw = _hex(value)
    start = word_index * 64
    if len(raw) < start + 64:
        return None
    try:
        return int(raw[start : start + 64], 16)
    except ValueError:
        return None


def _decode_address(value: Any) -> str | None:
    raw = _hex(value)
    if len(raw) < 64:
        return None
    return "0x" + raw[24:64]


def _rpc(client, rpc_url: str, method: str, params: list[Any]):
    state, payload, _http, attempts = client.request(
        "robinhood_rpc",
        rpc_url,
        payload={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        minimum_interval=0.05,
    )
    if state != "success":
        return state, None, attempts
    if not isinstance(payload, dict):
        return "source_failure", None, attempts
    if payload.get("error"):
        error = payload.get("error") or {}
        attempts.append(
            {
                "attempt": len(attempts) + 1,
                "state": "no_data" if method == "eth_call" else "source_failure",
                "rpcErrorCode": error.get("code"),
                "rpcError": str(error.get("message") or "")[:240],
            }
        )
        return ("no_data" if method == "eth_call" else "source_failure"), None, attempts
    return "success", payload.get("result"), attempts


def _eth_call(client, rpc_url: str, address: str, data: str):
    return _rpc(client, rpc_url, "eth_call", [{"to": address, "data": data}, "latest"])


def token_decimals(client, rpc_url: str, token_address: str):
    if str(token_address).lower() == NATIVE_CURRENCY:
        return "success", 18, []
    state, result, attempts = _eth_call(client, rpc_url, token_address, DECIMALS_SELECTOR)
    value = _decode_int(result)
    if state == "success" and (value is None or value > 255):
        state = "no_data"
    return state, value, attempts


def _v4_pool_key(client, rpc_url: str, pool_id: str):
    state, logs, attempts = _rpc(
        client,
        rpc_url,
        "eth_getLogs",
        [
            {
                "address": V4_POOL_MANAGER,
                "fromBlock": "0x0",
                "toBlock": "latest",
                "topics": [V4_INITIALIZE_TOPIC, "0x" + _hex(pool_id).rjust(64, "0")],
            }
        ],
    )
    if state != "success":
        return state, None, attempts
    if not isinstance(logs, list) or not logs:
        return "no_data", None, attempts
    log = logs[-1]
    topics = log.get("topics") or []
    data = _hex(log.get("data"))
    if len(topics) < 4 or len(data) < 64 * 3:
        return "no_data", None, attempts
    try:
        tick_spacing = int(data[64:128], 16)
        if tick_spacing >= 1 << 255:
            tick_spacing -= 1 << 256
        key = {
            "currency0": "0x" + _hex(topics[2])[-40:],
            "currency1": "0x" + _hex(topics[3])[-40:],
            "fee": int(data[0:64], 16),
            "tickSpacing": tick_spacing,
            "hooks": "0x" + data[128:192][-40:],
        }
    except (TypeError, ValueError):
        return "no_data", None, attempts
    return "success", key, attempts


def _quote_v4(client, rpc_url: str, pool_id: str, token_in: str, amount_in: int):
    state, key, attempts = _v4_pool_key(client, rpc_url, pool_id)
    if state != "success" or not key:
        return {"state": state, "attempts": attempts}
    token = str(token_in).lower()
    currencies = (key["currency0"].lower(), key["currency1"].lower())
    if token not in currencies:
        return {"state": "no_data", "attempts": attempts, "reason": "candidate_not_in_pool_key"}
    zero_for_one = token == currencies[0]
    output_token = currencies[1] if zero_for_one else currencies[0]
    tuple_head = "".join(
        (
            _address_word(key["currency0"]),
            _address_word(key["currency1"]),
            _word(key["fee"]),
            _word(key["tickSpacing"]),
            _address_word(key["hooks"]),
            _word(int(zero_for_one)),
            _word(amount_in),
            _word(8 * 32),
        )
    )
    calldata = V4_QUOTE_EXACT_INPUT_SINGLE_SELECTOR + _word(32) + tuple_head + _word(0)
    quote_state, result, quote_attempts = _eth_call(client, rpc_url, V4_QUOTER, calldata)
    attempts += quote_attempts
    output_amount = _decode_int(result)
    if quote_state == "success" and output_amount is None:
        quote_state = "no_data"
    return {
        "state": quote_state,
        "provider": "Uniswap V4 Robinhood",
        "outputToken": output_token,
        "outputAmount": output_amount,
        "route": {"protocol": "uniswap_v4", "poolId": pool_id, "poolKey": key},
        "attempts": attempts,
    }


def _pair_tokens(client, rpc_url: str, pair_address: str):
    state0, result0, attempts0 = _eth_call(client, rpc_url, pair_address, TOKEN0_SELECTOR)
    state1, result1, attempts1 = _eth_call(client, rpc_url, pair_address, TOKEN1_SELECTOR)
    state = state0 if state0 != "success" else state1
    return state, _decode_address(result0), _decode_address(result1), attempts0 + attempts1


def _quote_v3_or_v2(client, rpc_url: str, pair_address: str, token_in: str, amount_in: int):
    state, token0, token1, attempts = _pair_tokens(client, rpc_url, pair_address)
    if state != "success" or not token0 or not token1:
        return {"state": state, "attempts": attempts}
    token = str(token_in).lower()
    pair = (token0.lower(), token1.lower())
    if token not in pair:
        return {"state": "no_data", "attempts": attempts, "reason": "candidate_not_in_pair"}
    output_token = pair[1] if token == pair[0] else pair[0]
    fee_state, fee_result, fee_attempts = _eth_call(client, rpc_url, pair_address, FEE_SELECTOR)
    attempts += fee_attempts
    fee = _decode_int(fee_result)
    if fee_state == "success" and fee is not None:
        calldata = V3_QUOTE_EXACT_INPUT_SINGLE_SELECTOR + "".join(
            (_address_word(token), _address_word(output_token), _word(amount_in), _word(fee), _word(0))
        )
        quote_state, result, quote_attempts = _eth_call(client, rpc_url, V3_QUOTER, calldata)
        attempts += quote_attempts
        output_amount = _decode_int(result)
        if quote_state == "success" and output_amount is None:
            quote_state = "no_data"
        return {
            "state": quote_state,
            "provider": "Uniswap V3 Robinhood",
            "outputToken": output_token,
            "outputAmount": output_amount,
            "route": {"protocol": "uniswap_v3", "pairAddress": pair_address, "fee": fee},
            "attempts": attempts,
        }
    calldata = V2_GET_AMOUNTS_OUT_SELECTOR + _word(amount_in) + _word(64) + _word(2) + _address_word(token) + _address_word(output_token)
    quote_state, result, quote_attempts = _eth_call(client, rpc_url, V2_ROUTER, calldata)
    attempts += quote_attempts
    raw = _hex(result)
    output_amount = _decode_int(result, max(0, len(raw) // 64 - 1)) if raw else None
    if quote_state == "success" and output_amount is None:
        quote_state = "no_data"
    return {
        "state": quote_state,
        "provider": "Uniswap V2 Robinhood",
        "outputToken": output_token,
        "outputAmount": output_amount,
        "route": {"protocol": "uniswap_v2", "pairAddress": pair_address},
        "attempts": attempts,
    }


def quote_pool(client, rpc_url: str, pool_id: str, token_in: str, amount_in: int) -> dict[str, Any]:
    """Quote one provider-indexed pool without signing or submitting a transaction."""

    raw_pool = _hex(pool_id)
    raw_token = _hex(token_in)
    if len(raw_token) != 40 or int(amount_in) <= 0:
        return {"state": "no_data", "attempts": [], "reason": "invalid_quote_input"}
    if len(raw_pool) == 64:
        return _quote_v4(client, rpc_url, "0x" + raw_pool, "0x" + raw_token, int(amount_in))
    if len(raw_pool) == 40:
        return _quote_v3_or_v2(client, rpc_url, "0x" + raw_pool, "0x" + raw_token, int(amount_in))
    return {"state": "unsupported", "attempts": [], "reason": "unsupported_pool_identifier"}
