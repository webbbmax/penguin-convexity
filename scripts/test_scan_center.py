#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import discover_network_tokens
import run_manual_network_scan
from build_scan_center_snapshot import build_scan_center_snapshot
from discover_network_tokens import load_config, source_candidates


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sample_record():
    return {
        "networkId": "ethereum-mainnet",
        "contractAddress": "0x1111111111111111111111111111111111111111",
        "tokenName": "Manual Scan Test",
        "symbol": "MST",
        "holdersCount": None,
        "sourceIds": ["discovery-dexscreener-profiles"],
        "sourceUrls": ["https://dexscreener.com/ethereum/manual-scan-test"],
        "discoveryKinds": ["最新资料"],
        "sourceBoundaries": ["只用于发现，不证明投资价值。"],
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
        "estimatedExitSlippagePct": 0.67,
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


def bundle():
    config = load_config()
    return {
        "version": config["version"],
        "collectedAt": "2026-07-29T00:00:00Z",
        "records": [sample_record()],
        "sourceStats": {
            "dexscreener_profiles": {
                "collected": 1,
                "accepted": 1,
                "failed": 0,
                "skipped": 0,
            }
        },
        "errors": [],
        "config": config,
        "scope": {
            "networkIds": ["ethereum-mainnet"],
            "sourceKeys": ["dexscreener_profiles"],
            "sourceIds": ["discovery-dexscreener-profiles"],
            "noLimit": True,
        },
    }


def empty_identity_bundle():
    return {"records": [], "errors": [], "sourceStats": {}}


def test_source_scope():
    config = load_config()
    rows = [
        {
            "chainId": "ethereum",
            "tokenAddress": f"0x{index:040x}",
            "url": f"https://dexscreener.com/ethereum/{index}",
        }
        for index in range(1, 81)
    ] + [
        {
            "chainId": "base",
            "tokenAddress": "0x9999999999999999999999999999999999999999",
            "url": "https://dexscreener.com/base/ignored",
        }
    ]
    with patch.object(discover_network_tokens, "request_json", return_value=rows):
        records, stats, errors = source_candidates(
            config,
            network_ids=["ethereum-mainnet"],
            source_keys=["dexscreener_profiles"],
        )
    assert not errors
    assert len(records) == 80
    assert set(stats) == {"dexscreener_profiles"}
    assert stats["dexscreener_profiles"]["accepted"] == 80

    with patch.object(
        discover_network_tokens,
        "request_json",
        side_effect=AssertionError("不应访问不兼容信源"),
    ):
        records, stats, errors = source_candidates(
            config,
            network_ids=["ethereum-mainnet"],
            source_keys=["robinhood_registry"],
        )
    assert records == []
    assert errors == []
    assert stats["robinhood_registry"]["skipped"] == 1


def test_manual_run_and_retry_history():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        runtime_path = root / "runtime.js"
        pool_path = root / "pool.js"
        with (
            patch.object(
                run_manual_network_scan,
                "collect_network_discoveries",
                return_value=bundle(),
            ),
            patch.object(
                run_manual_network_scan,
                "collect_identity_reviews",
                return_value=empty_identity_bundle(),
            ),
            patch.object(run_manual_network_scan, "rebuild_snapshots"),
        ):
            result = run_manual_network_scan.run_manual_scan(
                network_ids=["ethereum-mainnet"],
                source_ids=["discovery-dexscreener-profiles"],
                db_path=db_path,
                pool_snapshot_path=pool_path,
                runtime_snapshot_path=runtime_path,
            )
        assert result["status"] == "success"
        assert result["scope"]["networkIds"] == ["ethereum-mainnet"]
        assert result["scope"]["sourceIds"] == [
            "discovery-dexscreener-profiles"
        ]

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM scan_run_scopes"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM scan_results"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM run_source_stats"
            ).fetchone()[0] == 1
            snapshot = build_scan_center_snapshot(connection)
            assert snapshot["counts"]["runs"] == 1
            assert snapshot["counts"]["results"] == 1
            assert snapshot["latestRun"]["scope"]["networkIds"] == [
                "ethereum-mainnet"
            ]
            assert snapshot["results"][0]["externalKey"] == (
                "0x1111111111111111111111111111111111111111"
            )
        finally:
            connection.close()

        with (
            patch.object(
                run_manual_network_scan,
                "collect_network_discoveries",
                side_effect=RuntimeError("模拟上游失败"),
            ),
            patch.object(run_manual_network_scan, "rebuild_snapshots"),
        ):
            failed = run_manual_network_scan.run_manual_scan(
                network_ids=["base-mainnet"],
                source_ids=["discovery-dexscreener-boosts"],
                db_path=db_path,
                pool_snapshot_path=pool_path,
                runtime_snapshot_path=runtime_path,
            )
        assert failed["status"] == "failed"
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            snapshot = build_scan_center_snapshot(connection)
            assert snapshot["counts"]["retryableRuns"] == 1
            assert snapshot["latestRun"]["scope"]["networkIds"] == [
                "base-mainnet"
            ]
            assert snapshot["latestRun"]["scope"]["sourceIds"] == [
                "discovery-dexscreener-boosts"
            ]
            assert snapshot["latestRun"]["errors"]
        finally:
            connection.close()


def test_static_entrypoints():
    html = (PROJECT_ROOT / "app" / "scan-center.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "app" / "scan-center.js").read_text(encoding="utf-8")
    workbench = (PROJECT_ROOT / "app" / "workbench.html").read_text(
        encoding="utf-8"
    )
    navigation = (
        PROJECT_ROOT / "app" / "workbench-nav.js"
    ).read_text(encoding="utf-8")
    assert 'id="scanSelected"' in html
    assert 'id="scanAll"' in html
    assert "按原范围重试" in script
    assert "列表不分页、不截断" in html
    assert 'fetch(apiUrl' in script
    assert 'location.pathname.startsWith("/convexity/")' in script
    assert "scan-center.html" in workbench
    assert '["scan-center.html", "按链与信源扫描"]' in navigation


def main():
    test_source_scope()
    print("PASS 指定链与指定信源生效，80条结果未被截断")
    test_manual_run_and_retry_history()
    print("PASS 人工扫描范围、逐条结果和失败原范围重试已留痕")
    test_static_entrypoints()
    print("PASS 扫描中心已接入凸性工作台和企鹅投研桌面入口")


if __name__ == "__main__":
    main()
