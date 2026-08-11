#!/usr/bin/env python3
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


USER_AGENT = "Penguin-Convexity/1.0"
RULE_VERSION = "contract-tradeability-v1"

NETWORKS = {
    "ethereum-mainnet": {
        "name": "Ethereum",
        "chainId": "1",
        "chainType": "EVM",
        "dexSlug": "ethereum",
        "explorer": "https://etherscan.io",
        "platformKeys": ("ethereum",),
    },
    "solana-mainnet": {
        "name": "Solana",
        "chainId": "mainnet-beta",
        "chainType": "Solana",
        "dexSlug": "solana",
        "explorer": "https://solscan.io",
        "platformKeys": ("solana",),
    },
    "base-mainnet": {
        "name": "Base",
        "chainId": "8453",
        "chainType": "EVM",
        "dexSlug": "base",
        "explorer": "https://base.blockscout.com",
        "platformKeys": ("base",),
    },
    "arbitrum-mainnet": {
        "name": "Arbitrum One",
        "chainId": "42161",
        "chainType": "EVM",
        "dexSlug": "arbitrum",
        "explorer": "https://arbiscan.io",
        "platformKeys": ("arbitrum-one",),
    },
    "bnb-mainnet": {
        "name": "BNB Smart Chain",
        "chainId": "56",
        "chainType": "EVM",
        "dexSlug": "bsc",
        "explorer": "https://bscscan.com",
        "platformKeys": ("binance-smart-chain",),
    },
    "robinhood-mainnet": {
        "name": "Robinhood Chain",
        "chainId": "4663",
        "chainType": "EVM",
        "dexSlug": "robinhood",
        "explorer": "https://robinhoodchain.blockscout.com",
        "platformKeys": ("robinhood", "robinhood-chain"),
    },
}

CHAIN_TO_NETWORK = {
    "Ethereum": "ethereum-mainnet",
    "Solana": "solana-mainnet",
    "Base": "base-mainnet",
    "Arbitrum": "arbitrum-mainnet",
    "Arbitrum One": "arbitrum-mainnet",
    "BNB Smart Chain": "bnb-mainnet",
    "Robinhood Chain": "robinhood-mainnet",
}

DEX_TO_NETWORK = {
    item["dexSlug"]: network_id for network_id, item in NETWORKS.items()
}

SOURCE_DEFINITIONS = {
    "goplus": {
        "source_id": "security-goplus",
        "name": "GoPlus Security",
        "source_type": "contract_security_api",
        "url": "https://api.gopluslabs.io",
        "access_method": "Public API",
    },
    "robinhood_blockscout": {
        "source_id": "chain-robinhood-blockscout",
        "name": "Robinhood Chain Blockscout",
        "source_type": "chain_explorer_api",
        "url": "https://robinhoodchain.blockscout.com",
        "access_method": "Public API",
    },
    "contract_mapping": {
        "source_id": "contract-identity-mapping",
        "name": "代币合约身份映射",
        "source_type": "identity_registry",
        "url": "multiple://market-and-chain-sources",
        "access_method": "Read-only verification",
    },
}


class IdentityConflict(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def user_environment(name):
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except (FileNotFoundError, OSError):
        return ""


def request_json(url, headers=None, timeout=20, payload=None):
    body = None
    merged_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        merged_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=merged_headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))


def estimate_slippage(liquidity_usd, exit_notional_usd):
    if not liquidity_usd or liquidity_usd <= 0:
        return None
    return round(min(100, (200 * exit_notional_usd) / liquidity_usd), 4)


def address_equal(left, right, chain_type):
    if not left or not right:
        return False
    if chain_type == "EVM":
        return left.lower() == right.lower()
    return left == right


def explorer_address_url(network, contract_address):
    suffix = "token" if network["chainType"] == "Solana" else "address"
    return f"{network['explorer'].rstrip('/')}/{suffix}/{contract_address}"


def best_pair(network, contract_address, timeout):
    url = (
        "https://api.dexscreener.com/token-pairs/v1/"
        f"{network['dexSlug']}/{urllib.parse.quote(contract_address)}"
    )
    payload = request_json(url, timeout=timeout)
    pairs = payload if isinstance(payload, list) else payload.get("pairs") or []
    matching = [
        pair
        for pair in pairs
        if address_equal(
            (pair.get("baseToken") or {}).get("address"),
            contract_address,
            network["chainType"],
        )
    ]
    if not matching:
        return None
    return max(
        matching,
        key=lambda pair: float((pair.get("liquidity") or {}).get("usd") or 0),
    )


def goplus_evm(network, contract_address, expected_symbol, timeout):
    url = (
        f"https://api.gopluslabs.io/api/v1/token_security/{network['chainId']}?"
        + urllib.parse.urlencode({"contract_addresses": contract_address})
    )
    payload = request_json(url, timeout=timeout)
    result = payload.get("result") or {}
    token = result.get(contract_address.lower()) or result.get(contract_address) or {}
    if not token:
        raise RuntimeError("GoPlus 未返回该合约")

    actual_symbol = str(token.get("token_symbol") or "")
    risk_flags = []
    critical_checks = {
        "is_honeypot": "疑似蜜罐",
        "cannot_sell_all": "可能无法卖出全部余额",
    }
    caution_checks = {
        "is_mintable": "仍可增发",
        "slippage_modifiable": "滑点或税费可修改",
        "transfer_pausable": "转账可能被暂停",
        "trading_cooldown": "存在交易冷却限制",
    }
    high_risk_checks = {
        "owner_change_balance": "管理方可能修改余额",
        "hidden_owner": "存在隐藏管理员",
    }
    for field, label in critical_checks.items():
        if str(token.get(field) or "0") == "1":
            risk_flags.append({"code": field, "level": "blocked", "detail": label})
    for field, label in caution_checks.items():
        if str(token.get(field) or "0") == "1":
            risk_flags.append({"code": field, "level": "medium", "detail": label})
    for field, label in high_risk_checks.items():
        if str(token.get(field) or "0") == "1":
            risk_flags.append({"code": field, "level": "high", "detail": label})
    sell_tax = float(token.get("sell_tax") or 0)
    if sell_tax > 0:
        risk_flags.append(
            {
                "code": "sell_tax",
                "level": "blocked" if sell_tax >= 0.1 else "medium",
                "detail": f"卖出税 {sell_tax * 100:.2f}%",
            }
        )

    return {
        "provider": "goplus",
        "sourceUrl": url,
        "contractExistsStatus": "verified",
        "sourceCodeStatus": (
            "verified" if str(token.get("is_open_source") or "0") == "1" else "unverified"
        ),
        "metadataMatchStatus": (
            "match"
            if actual_symbol and actual_symbol.casefold() == expected_symbol.casefold()
            else "mismatch"
        ),
        "riskFlags": risk_flags,
        "riskAssessment": {
            "mint": "high" if str(token.get("is_mintable") or "0") == "1" else "low",
            "freeze": "unknown",
            "transferTax": "high" if sell_tax >= 0.1 else "medium" if sell_tax > 0 else "low",
            "pause": "high" if str(token.get("transfer_pausable") or "0") == "1" else "low",
            "upgrade": "medium" if str(token.get("is_proxy") or "0") == "1" else "low",
            "owner": "high"
            if any(str(token.get(field) or "0") == "1" for field in ("hidden_owner", "owner_change_balance"))
            else "low",
            "concentration": "unknown",
        },
        "evidence": [
            {
                "label": "GoPlus 合约安全",
                "status": "verified",
                "detail": f"链上代币资料返回 {actual_symbol or '未知符号'}",
                "url": url,
            }
        ],
    }


def goplus_solana(contract_address, expected_symbol, timeout):
    url = (
        "https://api.gopluslabs.io/api/v1/solana/token_security?"
        + urllib.parse.urlencode({"contract_addresses": contract_address})
    )
    payload = request_json(url, timeout=timeout)
    token = (payload.get("result") or {}).get(contract_address) or {}
    if not token:
        raise RuntimeError("GoPlus 未返回该 Solana Mint")
    metadata = token.get("metadata") or {}
    actual_symbol = str(metadata.get("symbol") or "")
    risk_flags = []
    checks = {
        "mintable": "仍保留增发权限",
        "freezable": "仍保留冻结权限",
        "closable": "代币账户可能被关闭",
    }
    for field, label in checks.items():
        if str((token.get(field) or {}).get("status") or "0") == "1":
            risk_flags.append({"code": field, "level": "medium", "detail": label})
    if str(token.get("non_transferable") or "0") == "1":
        risk_flags.append(
            {"code": "non_transferable", "level": "blocked", "detail": "代币不可转账"}
        )
    return {
        "provider": "goplus",
        "sourceUrl": url,
        "contractExistsStatus": "verified",
        "sourceCodeStatus": "not_applicable",
        "metadataMatchStatus": (
            "match"
            if actual_symbol and actual_symbol.casefold() == expected_symbol.casefold()
            else "mismatch"
        ),
        "riskFlags": risk_flags,
        "riskAssessment": {
            "mint": "high" if str((token.get("mintable") or {}).get("status") or "0") == "1" else "low",
            "freeze": "high" if str((token.get("freezable") or {}).get("status") or "0") == "1" else "low",
            "transferTax": "medium" if token.get("transfer_fee") else "low",
            "pause": "unknown",
            "upgrade": "unknown",
            "owner": "unknown",
            "concentration": "unknown",
        },
        "evidence": [
            {
                "label": "GoPlus Solana Mint",
                "status": "verified",
                "detail": f"Mint 资料返回 {actual_symbol or '未知符号'}",
                "url": url,
            }
        ],
    }


def robinhood_contract(contract_address, expected_symbol, timeout):
    rpc_url = "https://rpc.mainnet.chain.robinhood.com"
    chain_response = request_json(
        rpc_url,
        timeout=timeout,
        payload={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1},
    )
    code_response = request_json(
        rpc_url,
        timeout=timeout,
        payload={
            "jsonrpc": "2.0",
            "method": "eth_getCode",
            "params": [contract_address, "latest"],
            "id": 2,
        },
    )
    chain_ok = str(chain_response.get("result") or "").lower() == hex(4663)
    code_exists = str(code_response.get("result") or "0x") not in ("", "0x", "0x0")
    token_url = (
        "https://robinhoodchain.blockscout.com/api/v2/tokens/"
        f"{urllib.parse.quote(contract_address)}"
    )
    contract_url = (
        "https://robinhoodchain.blockscout.com/api/v2/smart-contracts/"
        f"{urllib.parse.quote(contract_address)}"
    )
    token = request_json(token_url, timeout=timeout)
    contract = request_json(contract_url, timeout=timeout)
    actual_symbol = str(token.get("symbol") or "")
    source_verified = bool(contract.get("is_verified"))
    evidence = [
        {
            "label": "Robinhood 主网",
            "status": "verified" if chain_ok else "failed",
            "detail": f"RPC Chain ID {chain_response.get('result') or '未知'}",
            "url": rpc_url,
        },
        {
            "label": "合约字节码",
            "status": "verified" if code_exists else "failed",
            "detail": "主网存在合约代码" if code_exists else "主网未发现合约代码",
            "url": contract_url,
        },
        {
            "label": "Blockscout 源码",
            "status": "verified" if source_verified else "pending",
            "detail": "源码已验证" if source_verified else "源码未验证",
            "url": contract_url,
        },
    ]
    return {
        "provider": "robinhood_blockscout",
        "sourceUrl": contract_url,
        "contractExistsStatus": "verified" if chain_ok and code_exists else "missing",
        "sourceCodeStatus": "verified" if source_verified else "unverified",
        "metadataMatchStatus": (
            "match"
            if actual_symbol and actual_symbol.casefold() == expected_symbol.casefold()
            else "mismatch"
        ),
        "riskFlags": [],
        "riskAssessment": {
            "mint": "unknown",
            "freeze": "unknown",
            "transferTax": "unknown",
            "pause": "unknown",
            "upgrade": "unknown",
            "owner": "unknown",
            "concentration": "unknown",
        },
        "evidence": evidence,
    }


def empty_security(provider="contract_mapping"):
    return {
        "provider": provider,
        "sourceUrl": "",
        "contractExistsStatus": "unknown",
        "sourceCodeStatus": "unknown",
        "metadataMatchStatus": "unknown",
        "riskFlags": [],
        "riskAssessment": {
            "mint": "unknown",
            "freeze": "unknown",
            "transferTax": "unknown",
            "pause": "unknown",
            "upgrade": "unknown",
            "owner": "unknown",
            "concentration": "unknown",
        },
        "evidence": [],
    }


def verify_candidate(candidate, timeout=20):
    network = NETWORKS[candidate["networkId"]]
    security = empty_security()
    try:
        if candidate["networkId"] == "robinhood-mainnet":
            security = robinhood_contract(
                candidate["contractAddress"], candidate["symbol"], timeout
            )
        elif network["chainType"] == "Solana":
            security = goplus_solana(
                candidate["contractAddress"], candidate["symbol"], timeout
            )
        else:
            security = goplus_evm(
                network, candidate["contractAddress"], candidate["symbol"], timeout
            )
    except Exception as error:
        security["evidence"].append(
            {
                "label": "合约安全接口",
                "status": "pending",
                "detail": f"{type(error).__name__}: {error}",
                "url": security.get("sourceUrl", ""),
            }
        )

    pair = candidate.get("pair")
    if pair is None:
        try:
            pair = best_pair(network, candidate["contractAddress"], timeout)
        except Exception as error:
            security["evidence"].append(
                {
                    "label": "交易池搜索",
                    "status": "pending",
                    "detail": f"{type(error).__name__}: {error}",
                    "url": "https://dexscreener.com",
                }
            )

    pair_match = "unknown"
    buys = None
    sells = None
    liquidity = None
    pool_address = ""
    pair_symbol = ""
    venue_name = ""
    pair_url = ""
    if pair:
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        pair_match = (
            "match"
            if address_equal(
                base.get("address"),
                candidate["contractAddress"],
                network["chainType"],
            )
            else "mismatch"
        )
        txns = (pair.get("txns") or {}).get("h24") or {}
        buys = txns.get("buys")
        sells = txns.get("sells")
        liquidity = (pair.get("liquidity") or {}).get("usd")
        pool_address = pair.get("pairAddress") or ""
        pair_symbol = f"{base.get('symbol', '')}/{quote.get('symbol', '')}"
        venue_name = pair.get("dexId") or "DEX"
        pair_url = pair.get("url") or ""
        security["evidence"].append(
            {
                "label": "交易池与近期卖出",
                "status": "verified" if pair_match == "match" and (sells or 0) > 0 else "pending",
                "detail": f"24小时买入 {buys or 0} 笔，卖出 {sells or 0} 笔",
                "url": pair_url,
            }
        )

    slippage = estimate_slippage(liquidity, candidate["exitNotionalUsd"])
    critical = any(flag["level"] == "blocked" for flag in security["riskFlags"])
    mismatch = (
        security["metadataMatchStatus"] == "mismatch" or pair_match == "mismatch"
    )
    if critical or mismatch or security["contractExistsStatus"] == "missing":
        sell_path = "blocked"
        overall = "fail"
    elif (
        security["contractExistsStatus"] == "verified"
        and security["metadataMatchStatus"] == "match"
        and pair_match == "match"
        and (sells or 0) > 0
        and slippage is not None
    ):
        sell_path = "read_only_verified"
        overall = "pass"
    else:
        sell_path = "unknown"
        overall = "pending"

    identity_status = (
        "conflict"
        if mismatch
        else "market_matched"
        if security["contractExistsStatus"] == "verified"
        else "pending"
    )
    risk_levels = [flag["level"] for flag in security["riskFlags"]]
    if "blocked" in risk_levels:
        overall_risk = "blocked"
    elif "high" in risk_levels:
        overall_risk = "high"
    elif risk_levels:
        overall_risk = "medium"
    elif security["provider"] == "goplus" and security["contractExistsStatus"] == "verified":
        overall_risk = "low"
    else:
        overall_risk = "unknown"

    return {
        **candidate,
        "status": "success",
        "provider": security["provider"],
        "networkName": network["name"],
        "chainId": network["chainId"],
        "chainType": network["chainType"],
        "contractStandard": "SPL" if network["chainType"] == "Solana" else "ERC-20",
        "identityStatus": identity_status,
        "sourceUrl": candidate.get("sourceUrl") or explorer_address_url(
            network, candidate["contractAddress"]
        ),
        "explorerUrl": explorer_address_url(network, candidate["contractAddress"]),
        "verificationSourceUrl": security["sourceUrl"],
        "contractExistsStatus": security["contractExistsStatus"],
        "sourceCodeStatus": security["sourceCodeStatus"],
        "metadataMatchStatus": security["metadataMatchStatus"],
        "pairMatchStatus": pair_match,
        "recentBuys24h": buys,
        "recentSells24h": sells,
        "sellPathStatus": sell_path,
        "estimatedExitSlippagePct": slippage,
        "overallStatus": overall,
        "overallRisk": overall_risk,
        "riskAssessment": security["riskAssessment"],
        "riskFlags": security["riskFlags"],
        "evidence": security["evidence"],
        "venue": (
            {
                "name": venue_name,
                "pairSymbol": pair_symbol,
                "poolAddress": pool_address,
                "sourceUrl": pair_url,
            }
            if pool_address
            else None
        ),
        "verificationScope": (
            "只读核验：确认链上合约、代币资料、交易池匹配、24小时卖出记录和池深滑点；"
            "未使用钱包执行真实卖出，也未把市场来源当作项目官方合约声明。"
        ),
    }


def persist_contract_checks(connection, results, records, run_id, now, stable_id):
    summary = {
        "success": 0,
        "failed": 0,
        "conflicts": 0,
        "passed": 0,
        "sellPathsVerified": 0,
        "errors": [],
        "byCase": {},
    }
    for result in results:
        record = records[result["caseId"]]
        if result["status"] != "success":
            is_conflict = result["status"] == "conflict"
            if is_conflict:
                summary["conflicts"] += 1
            else:
                summary["failed"] += 1
                summary["errors"].append(result)
            payload = {
                "provider": result["provider"],
                "status": result["status"],
                "summary": result["error"],
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
                        'contract_tradeability_check', ?, 'rejected')
                """,
                (
                    stable_id("raw-contract-error", run_id, result["caseId"]),
                    SOURCE_DEFINITIONS["contract_mapping"]["source_id"],
                    run_id,
                    f"{run_id}:{result['caseId']}:contract-error",
                    now,
                    hashlib.sha256(result["error"].encode("utf-8")).hexdigest(),
                    result.get("sourceUrl", ""),
                    result["error"],
                    result["caseId"],
                    record.get("symbol", ""),
                    record.get("chain", ""),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            summary["byCase"].setdefault(result["caseId"], []).append(payload)
            continue

        summary["success"] += 1
        summary["passed"] += result["overallStatus"] == "pass"
        summary["sellPathsVerified"] += (
            result["sellPathStatus"] == "read_only_verified"
        )
        asset_id = result["assetId"]
        normalized_address = (
            result["contractAddress"].lower()
            if result["chainType"] == "EVM"
            else result["contractAddress"]
        )
        asset_contract_id = stable_id(
            "asset-contract",
            asset_id,
            result["networkId"],
            normalized_address,
        )
        if result["isPrimary"]:
            connection.execute(
                "UPDATE asset_contracts SET is_primary = 0, updated_at = ? WHERE asset_id = ?",
                (now, asset_id),
            )
        connection.execute(
            """
            INSERT INTO asset_contracts (
              asset_contract_id, asset_id, network_id, contract_address,
              contract_standard, is_primary, identity_status, identity_source,
              source_id, source_url, observed_at, verified_at,
              verification_method, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, network_id, contract_address) DO UPDATE SET
              contract_standard = excluded.contract_standard,
              is_primary = excluded.is_primary,
              identity_status = excluded.identity_status,
              identity_source = excluded.identity_source,
              source_id = excluded.source_id,
              source_url = excluded.source_url,
              observed_at = excluded.observed_at,
              verified_at = excluded.verified_at,
              verification_method = excluded.verification_method,
              updated_at = excluded.updated_at
            """,
            (
                asset_contract_id,
                asset_id,
                result["networkId"],
                result["contractAddress"],
                result["contractStandard"],
                int(result["isPrimary"]),
                result["identityStatus"],
                result["identitySource"],
                result["identitySourceId"],
                result["sourceUrl"],
                now,
                now if result["contractExistsStatus"] == "verified" else None,
                result["verificationScope"],
                now,
                now,
            ),
        )
        asset_contract_id = connection.execute(
            """
            SELECT asset_contract_id
            FROM asset_contracts
            WHERE asset_id = ?
              AND network_id = ?
              AND contract_address = ?
            """,
            (
                asset_id,
                result["networkId"],
                result["contractAddress"],
            ),
        ).fetchone()[0]
        if result["isPrimary"]:
            connection.execute(
                """
                UPDATE assets
                SET contract_address = ?, updated_at = ?
                WHERE asset_id = ?
                """,
                (result["contractAddress"], now, asset_id),
            )

        venue_id = None
        if result.get("venue"):
            venue_id = stable_id(
                "venue",
                asset_id,
                result["venue"]["poolAddress"],
            )
            buy_status = (
                "verified"
                if (result.get("recentBuys24h") or 0) > 0
                else "unknown"
            )
            sell_status = (
                "verified"
                if result["sellPathStatus"] == "read_only_verified"
                else "blocked"
                if result["sellPathStatus"] == "blocked"
                else "unknown"
            )
            connection.execute(
                """
                INSERT INTO venues (
                  venue_id, asset_id, venue_name, venue_type, pair_symbol,
                  pool_address, buy_status, sell_status, status, checked_at,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, 'DEX', ?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(venue_id) DO UPDATE SET
                  venue_name = excluded.venue_name,
                  pair_symbol = excluded.pair_symbol,
                  buy_status = excluded.buy_status,
                  sell_status = excluded.sell_status,
                  status = excluded.status,
                  checked_at = excluded.checked_at,
                  updated_at = excluded.updated_at
                """,
                (
                    venue_id,
                    asset_id,
                    result["venue"]["name"],
                    result["venue"]["pairSymbol"],
                    result["venue"]["poolAddress"],
                    buy_status,
                    sell_status,
                    now,
                    now,
                    now,
                ),
            )

        verification_source_id = SOURCE_DEFINITIONS[
            result["provider"]
        ]["source_id"]
        connection.execute(
            """
            INSERT INTO tradeability_checks (
              check_id, asset_contract_id, venue_id, checked_at,
              contract_exists_status, source_code_status, metadata_match_status,
              pair_match_status, recent_buys_24h, recent_sells_24h,
              sell_path_status, exit_notional_usd, estimated_exit_slippage_pct,
              overall_status, verification_scope, risk_flags_json,
              evidence_json, source_id, rule_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("tradeability-check", run_id, asset_contract_id),
                asset_contract_id,
                venue_id,
                now,
                result["contractExistsStatus"],
                result["sourceCodeStatus"],
                result["metadataMatchStatus"],
                result["pairMatchStatus"],
                result.get("recentBuys24h"),
                result.get("recentSells24h"),
                result["sellPathStatus"],
                result["exitNotionalUsd"],
                result.get("estimatedExitSlippagePct"),
                result["overallStatus"],
                result["verificationScope"],
                json.dumps(result["riskFlags"], ensure_ascii=False),
                json.dumps(result["evidence"], ensure_ascii=False),
                verification_source_id,
                RULE_VERSION,
            ),
        )
        risk = result["riskAssessment"]
        connection.execute(
            """
            INSERT INTO contract_risks (
              contract_risk_id, asset_id, assessed_at, mint_risk, freeze_risk,
              transfer_tax_risk, pause_risk, upgrade_risk, owner_risk,
              lp_control_risk, concentration_risk, overall_risk,
              evidence_json, rule_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?, ?)
            """,
            (
                stable_id("contract-risk", run_id, asset_contract_id),
                asset_id,
                now,
                risk["mint"],
                risk["freeze"],
                risk["transferTax"],
                risk["pause"],
                risk["upgrade"],
                risk["owner"],
                risk["concentration"],
                result["overallRisk"],
                json.dumps(result["evidence"], ensure_ascii=False),
                RULE_VERSION,
            ),
        )
        tradeability_status = (
            "verified"
            if result["overallStatus"] == "pass"
            else "blocked"
            if result["overallStatus"] == "fail"
            else "limited"
            if result["contractExistsStatus"] == "verified"
            else "unknown"
        )
        connection.execute(
            """
            UPDATE candidate_cases
            SET tradeability_status = ?, updated_at = ?
            WHERE case_id = ?
            """,
            (tradeability_status, now, result["caseId"]),
        )
        payload = {
            "provider": result["provider"],
            "status": result["overallStatus"],
            "networkName": result["networkName"],
            "chainId": result["chainId"],
            "chainType": result["chainType"],
            "contractAddress": result["contractAddress"],
            "contractStandard": result["contractStandard"],
            "identityStatus": result["identityStatus"],
            "identitySource": result["identitySource"],
            "explorerUrl": result["explorerUrl"],
            "contractExistsStatus": result["contractExistsStatus"],
            "sourceCodeStatus": result["sourceCodeStatus"],
            "metadataMatchStatus": result["metadataMatchStatus"],
            "pairMatchStatus": result["pairMatchStatus"],
            "recentBuys24h": result.get("recentBuys24h"),
            "recentSells24h": result.get("recentSells24h"),
            "sellPathStatus": result["sellPathStatus"],
            "exitNotionalUsd": result["exitNotionalUsd"],
            "estimatedExitSlippagePct": result.get("estimatedExitSlippagePct"),
            "overallRisk": result["overallRisk"],
            "riskFlags": result["riskFlags"],
            "evidence": result["evidence"],
            "verificationScope": result["verificationScope"],
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
                    'contract_tradeability_check', ?, 'normalized')
            """,
            (
                stable_id("raw-contract", run_id, asset_contract_id),
                verification_source_id,
                run_id,
                f"{run_id}:{result['caseId']}:contract",
                now,
                hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                result["explorerUrl"],
                (
                    f"{result['networkName']} · {result['contractAddress']} · "
                    f"卖出路径 {result['sellPathStatus']}"
                ),
                result["caseId"],
                record.get("symbol", ""),
                result["networkName"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        summary["byCase"].setdefault(result["caseId"], []).append(payload)
    return summary


def coingecko_candidate(mapping, record, exit_notional_usd, timeout):
    network_id = CHAIN_TO_NETWORK.get(record.get("chain"))
    headers = {}
    api_key = user_environment("COINGECKO_DEMO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    query = urllib.parse.urlencode(
        {
            "localization": "false",
            "tickers": "false",
            "market_data": "false",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        }
    )
    url = f"https://api.coingecko.com/api/v3/coins/{mapping['coinId']}?{query}"
    detail = request_json(url, headers=headers, timeout=timeout)
    platforms = detail.get("platforms") or {}
    recognized = [
        (candidate_network_id, key, str(platforms.get(key) or "").strip())
        for candidate_network_id, candidate_network in NETWORKS.items()
        for key in candidate_network["platformKeys"]
        if str(platforms.get(key) or "").strip()
    ]
    if not network_id:
        unique_networks = {item[0] for item in recognized}
        if len(unique_networks) != 1:
            return None
        network_id = next(iter(unique_networks))
    network = NETWORKS[network_id]
    contract_address = next(
        (
            str(platforms.get(key) or "").strip()
            for key in network["platformKeys"]
            if str(platforms.get(key) or "").strip()
        ),
        "",
    )
    if not contract_address:
        if recognized:
            observed_networks = "、".join(
                sorted({NETWORKS[item[0]]["name"] for item in recognized})
            )
            raise IdentityConflict(
                f"CoinGecko 合约位于 {observed_networks}，与项目记录的 {record.get('chain') or '未知网络'} 冲突"
            )
        return None
    return {
        "caseId": mapping["caseId"],
        "assetId": record["assetId"],
        "symbol": record.get("symbol") or "",
        "networkId": network_id,
        "contractAddress": contract_address,
        "identitySource": "CoinGecko 平台合约映射",
        "identitySourceId": "market-coingecko",
        "sourceUrl": f"https://www.coingecko.com/en/coins/{mapping['coinId']}",
        "isPrimary": True,
        "exitNotionalUsd": exit_notional_usd,
        "pair": None,
    }


def dexscreener_candidate(mapping, record, result, exit_notional_usd):
    pair = result.get("raw") or {}
    network_id = DEX_TO_NETWORK.get(str(pair.get("chainId") or mapping.get("chainId")))
    base = pair.get("baseToken") or {}
    if not network_id or not base.get("address"):
        return None
    return {
        "caseId": mapping["caseId"],
        "assetId": record["assetId"],
        "symbol": record.get("symbol") or "",
        "networkId": network_id,
        "contractAddress": base["address"],
        "identitySource": "DexScreener 交易池映射",
        "identitySourceId": "market-dexscreener",
        "sourceUrl": result.get("sourceUrl") or "",
        "isPrimary": True,
        "exitNotionalUsd": exit_notional_usd,
        "pair": pair,
    }


def collect_contract_checks(config, fixture, market_results, timeout=20):
    records = {item["caseId"]: item for item in fixture["records"]}
    mappings = {item["caseId"]: item for item in config["projects"]}
    markets = {
        item["caseId"]: item
        for item in market_results
        if item.get("status") == "success"
    }
    exit_notional = float(
        config.get("marketAssumptions", {}).get("extremeExitNotionalUsd") or 100
    )
    candidates = []
    failures = []
    for case_id, mapping in mappings.items():
        record = records[case_id]
        if not record.get("assetId"):
            continue
        try:
            if mapping["provider"] == "dexscreener" and case_id in markets:
                candidate = dexscreener_candidate(
                    mapping, record, markets[case_id], exit_notional
                )
            elif mapping["provider"] == "coingecko":
                candidate = coingecko_candidate(
                    mapping, record, exit_notional, timeout
                )
            else:
                candidate = None
            if candidate:
                candidates.append(candidate)
        except Exception as error:
            failures.append(
                {
                    "caseId": case_id,
                    "assetId": record.get("assetId"),
                    "provider": "contract_mapping",
                    "status": (
                        "conflict"
                        if isinstance(error, IdentityConflict)
                        else "failed"
                    ),
                    "sourceUrl": "",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = {
            executor.submit(verify_candidate, candidate, timeout): candidate
            for candidate in candidates
        }
        for future in as_completed(pending):
            candidate = pending[future]
            try:
                results.append(future.result())
            except Exception as error:
                results.append(
                    {
                        **candidate,
                        "provider": "contract_mapping",
                        "status": "failed",
                        "sourceUrl": candidate.get("sourceUrl", ""),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
    return sorted(results + failures, key=lambda item: item["caseId"])
