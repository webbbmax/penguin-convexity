#!/usr/bin/env python3
import sqlite3
import tempfile
import json
from pathlib import Path

from discover_network_tokens import (
    build_discovery_snapshot,
)
from refresh_candidate_pool import persist_refresh
from sync_thread_candidates import load_fixture, sync_candidates


def sample_record():
    return {
        "networkId": "robinhood-mainnet",
        "contractAddress": "0x1111111111111111111111111111111111111111",
        "tokenName": "Discovery Test",
        "symbol": "DTEST",
        "holdersCount": 128,
        "sourceIds": [
            "discovery-robinhood-blockscout",
            "discovery-dexscreener-profiles",
        ],
        "sourceUrls": [
            "https://robinhoodchain.blockscout.com/address/0x1111111111111111111111111111111111111111",
            "https://dexscreener.com/robinhood/test",
        ],
        "discoveryKinds": ["主网代币注册表", "最新资料"],
        "sourceBoundaries": [
            "主网存在不等于项目归属。",
            "推广资料只用于发现。",
        ],
        "rawPriceUsd": 0.01,
        "rawVolume24hUsd": 40000,
        "rawMarketCapUsd": 1000000,
        "pair": {},
        "pairError": "",
        "priceUsd": 0.01,
        "liquidityUsd": 30000,
        "volume24hUsd": 40000,
        "marketCapUsd": 1000000,
        "recentBuys24h": 40,
        "recentSells24h": 35,
        "exitNotionalUsd": 100,
        "estimatedExitSlippagePct": 0.6667,
        "contractExistsStatus": "verified",
        "metadataMatchStatus": "match",
        "pairMatchStatus": "match",
        "sellPathStatus": "read_only_verified",
        "contractRisk": "low",
        "preflightStatus": "pass",
        "verificationScope": "只读预检",
        "verificationEvidence": [],
        "discoveryScore": 90,
    }


def main():
    config = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "network-discovery-config-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert "limits" not in config
    assert "maximumChecksPerRun" not in config["identityReview"]

    bundle = {
        "records": [sample_record()],
        "sourceStats": {
            "dexscreener_profiles": {"collected": 1, "accepted": 1, "failed": 0},
            "robinhood_registry": {"collected": 1, "accepted": 1, "failed": 0},
        },
        "errors": [],
    }
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
            result = persist_refresh(
                connection,
                fixture,
                [],
                [],
                "network-discovery-test",
                discovery_bundle=bundle,
            )
            connection.commit()
            assert result["discoveriesObserved"] == 1
            assert result["discoveriesNew"] == 1
            assert result["discoveriesPreflightPassed"] == 1
            row = connection.execute(
                "SELECT * FROM network_discoveries"
            ).fetchone()
            assert row["queue_status"] == "preflight_pass"
            assert row["discovery_score"] == 90
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM raw_events
                WHERE event_type = 'network_token_discovery'
                """
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM scan_results"
            ).fetchone()[0] == 2
            snapshot = build_discovery_snapshot(connection)
            assert snapshot["counts"]["total"] == 1
            assert snapshot["counts"]["preflightPass"] == 1
            assert snapshot["counts"]["robinhood"] == 1
            assert "不是投资评分" in snapshot["boundary"]
        finally:
            connection.close()

    app_root = Path(__file__).resolve().parent.parent / "app"
    html = (app_root / "network-discovery.html").read_text(encoding="utf-8")
    script = (app_root / "network-discovery.js").read_text(encoding="utf-8")
    assert 'id="refreshDiscoveries"' in html
    assert "技术预检通过不等于投资结论" in html
    assert 'fetch(apiUrl("refresh-candidates")' in script
    assert 'location.pathname.startsWith("/convexity/")' in script
    assert "不是投资评分" in script
    print("network discovery checks passed")


if __name__ == "__main__":
    main()
