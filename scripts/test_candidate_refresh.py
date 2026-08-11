#!/usr/bin/env python3
import json
import sqlite3
import tempfile
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import serve_local
from refresh_candidate_pool import (
    DEFAULT_CONFIG_PATH,
    classify_market_grade,
    load_config,
    persist_refresh,
    sync_refresh_data_backbone,
)
from rule_engine import load_rulebook
from sync_thread_candidates import build_pool_snapshot, load_fixture, sync_candidates


def main():
    config = load_config()
    fixture = load_fixture()
    assert len(config["projects"]) == 20
    assert len({item["caseId"] for item in config["projects"]}) == 20
    assert {item["caseId"] for item in config["projects"]} == {
        item["caseId"] for item in fixture["records"]
    }

    rulebook = load_rulebook()
    market_base = {
        "caseId": "thread-cowl-20260728",
        "provider": "dexscreener",
        "status": "success",
        "sourceUrl": "https://dexscreener.com/example",
        "observedAt": "2026-07-28T12:00:00Z",
        "priceUsd": 0.00005,
        "liquidityUsd": 21000,
        "volume24hUsd": 30000,
        "marketCapUsd": 50000,
        "fdvUsd": 50000,
        "circulatingSupply": None,
        "priceChange24hPct": 2,
        "exitNotionalUsd": 100,
        "estimatedExitSlippagePct": 0.95,
        "definitionNote": "测试快照",
        "venue": {
            "name": "test-dex",
            "pairSymbol": "COWL/WETH",
            "poolAddress": "0xtest",
        },
        "raw": {},
    }
    assert classify_market_grade(market_base, rulebook) == "extreme"
    assert (
        classify_market_grade(
            {**market_base, "liquidityUsd": 19999},
            rulebook,
        )
        == "untradeable"
    )
    assert (
        classify_market_grade(
            {**market_base, "estimatedExitSlippagePct": None},
            rulebook,
        )
        == "unknown"
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        sync_candidates(
            db_path=db_path,
            pool_snapshot_path=root / "pool.js",
            runtime_snapshot_path=root / "runtime.js",
        )
        market_results = [
            market_base,
            {
                "caseId": "thread-uni-20260728",
                "provider": "coingecko",
                "status": "success",
                "sourceUrl": "https://www.coingecko.com/en/coins/uniswap",
                "observedAt": "2026-07-28T12:00:00Z",
                "priceUsd": 4,
                "liquidityUsd": None,
                "volume24hUsd": 1000000,
                "marketCapUsd": 2000000000,
                "fdvUsd": 4000000000,
                "circulatingSupply": 500000000,
                "priceChange24hPct": 1,
                "exitNotionalUsd": None,
                "estimatedExitSlippagePct": None,
                "definitionNote": "测试快照",
                "raw": {},
            },
            {
                "caseId": "thread-hashi-20260724",
                "provider": "unmapped",
                "status": "skipped",
                "sourceUrl": "",
                "error": "没有已核验资产",
            },
        ]
        evidence_results = [
            {
                "caseId": "thread-cowl-20260728",
                "provider": "evidence",
                "status": "success",
                "sourceUrl": "https://example.com/evidence",
                "httpStatus": 200,
                "fingerprint": "abc",
                "summary": "测试证据",
            }
        ]
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            result = persist_refresh(
                connection,
                fixture,
                market_results,
                evidence_results,
                "candidate-refresh-test",
            )
            connection.commit()
            assert result["status"] == "success"
            assert result["marketSuccess"] == 2
            assert result["marketSkipped"] == 1
            cowl = connection.execute(
                """
                SELECT workflow_state, action_stage, liquidity_grade
                FROM candidate_cases
                WHERE case_id = 'thread-cowl-20260728'
                """
            ).fetchone()
            assert dict(cowl) == {
                "workflow_state": "tradeability_pending",
                "action_stage": "只观察",
                "liquidity_grade": "extreme",
            }
            assert connection.execute(
                "SELECT COUNT(*) FROM market_snapshots"
            ).fetchone()[0] == 2
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM raw_events
                WHERE ingestion_run_id = 'candidate-refresh-test'
                  AND event_type NOT IN (
                    'machine_research_scoring_refresh',
                    'machine_conclusion_publish'
                  )
                """
            ).fetchone()[0] == 4
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM raw_events
                WHERE ingestion_run_id = 'candidate-refresh-test'
                  AND event_type = 'machine_research_scoring_refresh'
                """
            ).fetchone()[0] == result["machineScoringProjects"]
            assert connection.execute(
                """
                SELECT COUNT(*)
                FROM raw_events
                WHERE ingestion_run_id = 'candidate-refresh-test'
                  AND event_type = 'machine_conclusion_publish'
                """
            ).fetchone()[0] == result["machineConclusionProjects"]
            sync_refresh_data_backbone(
                connection,
                {"tracking"},
                "candidate-refresh-test",
                timeout=1,
            )
            assert connection.execute(
                "SELECT COUNT(*) FROM normalized_events_v2"
            ).fetchone()[0] == connection.execute(
                "SELECT COUNT(*) FROM raw_events"
            ).fetchone()[0]
            snapshot = build_pool_snapshot(connection, fixture)
            assert snapshot["latestRefresh"]["runId"] == "candidate-refresh-test"
            assert snapshot["latestRefresh"]["status"] == "success"
            cowl_snapshot = next(
                item
                for item in snapshot["cases"]
                if item["caseId"] == "thread-cowl-20260728"
            )
            assert cowl_snapshot["latestMarket"]["liquidityUsd"] == 21000
            assert cowl_snapshot["refresh"]["market"]["marketGrade"] == "extreme"
            assert cowl_snapshot["refresh"]["evidence"][0]["status"] == "success"
        finally:
            connection.close()

    assert DEFAULT_CONFIG_PATH.exists()

    original_refresh = serve_local.refresh_candidates
    serve_local.refresh_candidates = lambda: {
        "status": "success",
        "explanation": "测试刷新成功",
    }
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(serve_local.QuietHandler, directory=str(serve_local.APP_ROOT)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/refresh-candidates",
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
            assert response.status == 200
        assert payload["status"] == "success"
        assert payload["explanation"] == "测试刷新成功"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        serve_local.refresh_candidates = original_refresh

    print("candidate refresh checks passed")


if __name__ == "__main__":
    main()
