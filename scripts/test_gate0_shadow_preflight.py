#!/usr/bin/env python3
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from gate0_shadow_preflight import (
    classify_candidate,
    existing_shadow_days,
    included_lookup,
    load_config,
    normalize_gecko_pool,
    normalize_bearer_token,
    RequestLedger,
    source_state,
)


def sample_payload():
    return {
        "data": [
            {
                "id": "eth_pool",
                "type": "pool",
                "attributes": {
                    "address": "0xpool",
                    "name": "NEW / ETH",
                    "pool_created_at": "2026-08-03T00:00:00Z",
                    "base_token_price_usd": "0.01",
                    "reserve_in_usd": "35000",
                    "volume_usd": {"h24": "18000"},
                    "price_change_percentage": {"h24": "12.5"},
                    "transactions": {
                        "h24": {"buys": 81, "sells": 44, "buyers": 60, "sellers": 31}
                    },
                    "market_cap_usd": None,
                    "fdv_usd": "1000000",
                },
                "relationships": {
                    "base_token": {"data": {"type": "token", "id": "eth_base"}},
                    "quote_token": {"data": {"type": "token", "id": "eth_quote"}},
                    "dex": {"data": {"type": "dex", "id": "uniswap-v3-ethereum"}},
                },
            }
        ],
        "included": [
            {
                "type": "token",
                "id": "eth_base",
                "attributes": {
                    "address": "0x1111111111111111111111111111111111111111",
                    "name": "New Project",
                    "symbol": "NEW",
                    "coingecko_coin_id": None,
                },
            },
            {
                "type": "token",
                "id": "eth_quote",
                "attributes": {"address": "0x0", "name": "Ether", "symbol": "ETH"},
            },
            {
                "type": "dex",
                "id": "uniswap-v3-ethereum",
                "attributes": {"name": "Uniswap V3 (Ethereum)"},
            },
        ],
    }


def main():
    config = load_config()
    assert config["boundary"]["newProjectWindowDays"] == 90
    assert config["boundary"]["exitFromNewProjectPoolOnDay"] == 91
    assert config["boundary"]["ageWeighting"] is False
    assert config["boundary"]["projectMinimumWaitDays"] == 0
    assert config["boundary"]["allProjectAgesUseSourceHistory"] is True
    assert config["boundary"]["shortHistorySyntheticDaysAllowed"] is False
    assert config["boundary"]["liveReliabilityBlocksBackfillOrDevelopment"] is False
    assert config["boundary"]["fixedCandidateCapAllowed"] is False
    network_map = {network["id"]: network for network in config["networks"]}
    assert config["sources"]["geckoterminal"]["credentialEnv"] == "GECKOTERMINAL_API_KEY"
    assert config["sources"]["geckoterminal"]["fallbackCredentialEnv"] == "GECKOTERMINAL_API_KEY_FALLBACK"
    assert config["sources"]["geckoterminal"]["minimumRequestIntervalSeconds"] == 2.5
    assert config["sources"]["coinmarketcap"]["credentialEnv"] == "COINMARKETCAP_API_KEY"
    assert config["sources"]["goplus"]["appSecretEnv"] == "GOPLUS_APP_SECRET"
    assert network_map["solana-mainnet"]["alchemyHost"] == "solana-mainnet.g.alchemy.com"
    assert network_map["solana-mainnet"]["alchemyProbeMethod"] == "getHealth"
    assert network_map["robinhood-mainnet"]["alchemyHost"] == "robinhood-mainnet.g.alchemy.com"
    assert source_state(http_status=429) == "quota_limited"
    assert source_state(http_status=403) == "configuration_missing"
    assert source_state(records=0) == "no_data"
    assert source_state(error="timeout") == "source_failure"
    assert normalize_bearer_token("Bearer abc") == "abc"
    assert normalize_bearer_token("abc") == "abc"

    ledger = RequestLedger(timeout=1)
    with patch(
        "gate0_shadow_preflight.urllib.request.urlopen",
        side_effect=ConnectionResetError("remote disconnected"),
    ):
        assert ledger.request_json("rpc", "https://example.invalid") is None
    assert ledger.requests[-1]["state"] == "source_failure"

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "manifest.jsonl").write_text(
            "\n".join(
                json.dumps(row)
                for row in (
                    {"finishedAt": "2026-08-03T00:00:00Z", "usableForShadowDay": False},
                    {"finishedAt": "2026-08-04T00:00:00Z", "usableForShadowDay": True},
                )
            ),
            encoding="utf-8",
        )
        assert existing_shadow_days(root) == ["2026-08-04"]

    payload = sample_payload()
    network = config["networks"][0]
    pool = normalize_gecko_pool(network, payload["data"][0], included_lookup(payload))
    assert pool["baseToken"]["symbol"] == "NEW"
    assert pool["reserveUsd"] == 35000
    assert pool["marketCapUsd"] is None
    assert pool["transactions24h"]["buyers"] == 60

    candidate = {
        "contractAddress": pool["baseToken"]["address"],
        "earliestObservedPoolCreatedAt": pool["poolCreatedAt"],
        "bestPool": pool,
        "projectUrls": ["https://new.example"],
        "githubUrls": [],
        "security": {"state": "success", "hardRisk": "clear", "flags": []},
    }
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    provisional = classify_candidate(config, candidate, now=now)
    assert provisional["ageDays"] == 1
    assert provisional["relationClass"] == "C"
    assert provisional["deterministicPreGatePass"] is False
    assert "project_evidence_not_independently_mapped" in provisional["blockingReasons"]

    local_asset = {
        "asset_identity_status": "verified",
        "project_identity_status": "verified",
        "official_repo": "https://github.com/example/new",
        "website_domain": "new.example",
    }
    verified = classify_candidate(config, candidate, local_asset=local_asset, now=now)
    assert verified["projectEvidenceState"] == "verified_local_mapping"
    assert verified["deterministicPreGatePass"] is True

    candidate["earliestObservedPoolCreatedAt"] = "2026-05-05T00:00:00Z"
    exited = classify_candidate(config, candidate, local_asset=local_asset, now=now)
    assert exited["ageDays"] == 91
    assert exited["relationClass"] == "D"
    assert exited["deterministicPreGatePass"] is False
    print("gate0 shadow preflight checks passed")


if __name__ == "__main__":
    main()
