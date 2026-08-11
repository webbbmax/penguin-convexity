#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

import contract_tradeability
from refresh_candidate_pool import persist_refresh
from sync_thread_candidates import build_pool_snapshot, load_fixture, sync_candidates


def fake_robinhood_security(_contract_address, _expected_symbol, _timeout):
    return {
        "provider": "robinhood_blockscout",
        "sourceUrl": "https://robinhoodchain.blockscout.com/api/v2/smart-contracts/0xtest",
        "contractExistsStatus": "verified",
        "sourceCodeStatus": "verified",
        "metadataMatchStatus": "match",
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
        "evidence": [
            {
                "label": "测试链上合约",
                "status": "verified",
                "detail": "合约存在",
                "url": "https://example.com",
            }
        ],
    }


def fake_high_risk_evm(_network, _contract_address, _expected_symbol, _timeout):
    result = fake_robinhood_security("", "", 0)
    result["provider"] = "goplus"
    result["riskFlags"] = [
        {"code": "owner_change_balance", "level": "high", "detail": "管理权限风险"}
    ]
    return result


def main():
    candidate = {
        "caseId": "thread-cowl-20260728",
        "assetId": "cowl-protocol-cowl",
        "symbol": "COWL",
        "networkId": "robinhood-mainnet",
        "contractAddress": "0xfc7CB8A3Df69c0F658Ac5Fb1e31dE1843E04E38f",
        "identitySource": "DexScreener 交易池映射",
        "identitySourceId": "market-dexscreener",
        "sourceUrl": "https://dexscreener.com/robinhood/test",
        "isPrimary": True,
        "exitNotionalUsd": 100,
        "pair": {
            "chainId": "robinhood",
            "dexId": "uniswap",
            "pairAddress": "0xpool",
            "url": "https://dexscreener.com/robinhood/test",
            "baseToken": {
                "address": "0xfc7CB8A3Df69c0F658Ac5Fb1e31dE1843E04E38f",
                "symbol": "COWL",
            },
            "quoteToken": {"address": "0xquote", "symbol": "WETH"},
            "liquidity": {"usd": 25000},
            "txns": {"h24": {"buys": 18, "sells": 27}},
        },
    }
    original = contract_tradeability.robinhood_contract
    contract_tradeability.robinhood_contract = fake_robinhood_security
    try:
        result = contract_tradeability.verify_candidate(candidate, timeout=1)
    finally:
        contract_tradeability.robinhood_contract = original

    assert result["overallStatus"] == "pass"
    assert result["sellPathStatus"] == "read_only_verified"
    assert result["estimatedExitSlippagePct"] == 0.8
    assert result["identityStatus"] == "market_matched"
    assert result["chainId"] == "4663"

    evm_candidate = {
        **candidate,
        "networkId": "ethereum-mainnet",
        "contractAddress": "0x5a98fcbea516cf06857215779fd812ca3bef1b32",
        "pair": {
            **candidate["pair"],
            "chainId": "ethereum",
            "baseToken": {
                "address": "0x5a98fcbea516cf06857215779fd812ca3bef1b32",
                "symbol": "COWL",
            },
        },
    }
    original_evm = contract_tradeability.goplus_evm
    contract_tradeability.goplus_evm = fake_high_risk_evm
    try:
        high_risk = contract_tradeability.verify_candidate(evm_candidate, timeout=1)
    finally:
        contract_tradeability.goplus_evm = original_evm
    assert high_risk["sellPathStatus"] == "read_only_verified"
    assert high_risk["overallRisk"] == "high"

    fixture = load_fixture()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        sync_candidates(
            db_path=db_path,
            pool_snapshot_path=root / "pool.js",
            runtime_snapshot_path=root / "runtime.js",
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            persisted = persist_refresh(
                connection,
                fixture,
                [],
                [],
                "contract-check-test",
                contract_results=[result],
            )
            connection.commit()
            assert persisted["contractSuccess"] == 1
            assert persisted["sellPathsVerified"] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM asset_contracts"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM tradeability_checks"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT sell_status FROM venues WHERE asset_id = 'cowl-protocol-cowl'"
            ).fetchone()[0] == "verified"

            snapshot = build_pool_snapshot(connection, fixture)
            cowl = next(
                item
                for item in snapshot["cases"]
                if item["caseId"] == "thread-cowl-20260728"
            )
            assert cowl["assetContract"]["contractAddress"] == candidate["contractAddress"]
            assert cowl["assetContract"]["networkName"] == "Robinhood Chain"
            assert cowl["tradeabilityCheck"]["sellPathStatus"] == "read_only_verified"
            assert cowl["sellPathStatus"] == "verified"
            assert any(
                network["network_id"] == "robinhood-mainnet"
                for network in snapshot["discoveryNetworks"]
            )
        finally:
            connection.close()

    app_root = Path(__file__).resolve().parent.parent / "app"
    page = (app_root / "candidate-pool.html").read_text(
        encoding="utf-8"
    )
    screening_page = (app_root / "screening-console.html").read_text(
        encoding="utf-8"
    )
    screening_script = (app_root / "screening-console.js").read_text(
        encoding="utf-8"
    )
    detail_script = (app_root / "project-detail.js").read_text(
        encoding="utf-8"
    )
    assert 'id="discoveryNetworkList"' not in page
    assert 'id="discoveryNetworkList"' in screening_page
    assert "代币合约与卖出路径" in screening_script
    assert "同名仿盘风险" in screening_script
    assert "data-copy-contract" in screening_script
    assert "卖出路径与滑点" in detail_script
    print("contract and tradeability checks passed")


if __name__ == "__main__":
    main()
