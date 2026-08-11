#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from enrich_formal_market_exit import (
    ENRICHMENT_VERSION,
    SOURCE_DEFINITION,
    persist_formal_market_exit,
)
from build_update_center_snapshot import rebuild_update_snapshots
from init_db import initialize_database
from sync_thread_candidates import import_candidates, load_fixture, stable_id
from update_tasks import TASK_DEFINITIONS


NOW = "2026-07-30T08:00:00Z"


def insert_run(connection, run_id):
    job_name = TASK_DEFINITIONS["formal_market_exit_refresh"]["jobName"]
    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at,
          zero_result_class, zero_result_explanation, triggered_by,
          schema_version
        )
        VALUES (?, ?, 'manual', 'running', ?,
                'none', '', 'test', 1)
        """,
        (run_id, job_name, NOW),
    )


def market_record(connection):
    row = connection.execute(
        """
        SELECT
          p.project_id,
          p.canonical_name AS project_name,
          a.asset_id,
          a.symbol,
          a.chain,
          a.contract_address,
          cc.case_id
        FROM projects p
        JOIN assets a ON a.project_id = p.project_id
        JOIN candidate_cases cc ON cc.project_id = p.project_id
        WHERE p.project_id = 'cowl-protocol'
        LIMIT 1
        """
    ).fetchone()
    return {
        **dict(row),
        "project_identity_status": "verified",
        "asset_identity_status": "verified",
        "coinGeckoId": "",
        "networkId": "robinhood-mainnet",
        "status": "success",
        "observedAt": NOW,
        "priceUsd": 0.02,
        "liquidityUsd": 50000,
        "volume24hUsd": 60000,
        "marketCapUsd": 2000000,
        "fdvUsd": 2500000,
        "circulatingSupply": 100000000,
        "priceChange24hPct": 3.5,
        "exitNotionalUsd": 100,
        "estimatedExitSlippagePct": 0.4,
        "sourceIds": ["market-dexscreener"],
        "sourceUrl": "https://dexscreener.com/robinhood/test",
        "coinGeckoUrl": "",
        "dexScreenerUrl": "https://dexscreener.com/robinhood/test",
        "venue": {
            "name": "test-dex",
            "pairSymbol": "COWL/USDC",
            "poolAddress": "0xtestpool",
            "sourceUrl": "https://dexscreener.com/robinhood/test",
        },
        "pair": {},
        "definitionNote": "测试用最深单池与估算退出滑点。",
    }


def pending_record(connection):
    row = connection.execute(
        """
        SELECT
          p.project_id,
          p.canonical_name AS project_name,
          cc.case_id
        FROM projects p
        JOIN candidate_cases cc ON cc.project_id = p.project_id
        WHERE p.project_id = 'hashi'
        LIMIT 1
        """
    ).fetchone()
    return {
        **dict(row),
        "asset_id": None,
        "symbol": "",
        "chain": "",
        "contract_address": "",
        "project_identity_status": "verified",
        "asset_identity_status": "",
        "coinGeckoId": "",
        "networkId": "",
        "status": "no_data",
        "sourceIds": [],
        "summary": "项目尚无可映射资产。",
    }


def build_bundle(connection):
    market = market_record(connection)
    pending = pending_record(connection)
    return {
        "records": [market, pending],
        "contractResults": [],
        "caseRecords": {
            market["case_id"]: {
                "caseId": market["case_id"],
                "canonicalName": market["project_name"],
                "symbol": market["symbol"],
                "chain": market["chain"],
                "assetId": market["asset_id"],
            }
        },
        "errors": [],
        "projectsReviewed": 2,
        "assetsReviewed": 1,
    }


def main():
    assert ENRICHMENT_VERSION == "C1.4-02"
    task = TASK_DEFINITIONS["formal_market_exit_refresh"]
    assert task["label"] == "正式项目市场与退出资料"
    assert task["components"] == ["formal_market_exit"]

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        initialize_database(
            db_path=db_path,
            snapshot_path=root / "runtime.js",
            backup=False,
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        import_candidates(connection, load_fixture())
        insert_run(connection, "formal-market-test-1")
        result = persist_formal_market_exit(
            connection,
            build_bundle(connection),
            "formal-market-test-1",
            NOW,
            stable_id,
        )
        connection.execute(
            """
            UPDATE runs
            SET status = 'success', finished_at = ?
            WHERE run_id = 'formal-market-test-1'
            """,
            (NOW,),
        )
        connection.commit()

        assert result["projectsReviewed"] == 2
        assert result["assetsReviewed"] == 1
        assert result["marketCoveredProjects"] == 1
        assert result["exitCoveredProjects"] == 1
        assert result["pendingProjects"] == 1
        assert result["changedProjects"] == 1
        assert result["sellPathsVerified"] == 0
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM market_snapshots
            WHERE data_source_id = ?
            """,
            (SOURCE_DEFINITION["source_id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM raw_events
            WHERE event_type = 'formal_project_market_exit_enrichment'
            """
        ).fetchone()[0] == 2
        snapshot = connection.execute(
            """
            SELECT *
            FROM market_snapshots
            WHERE data_source_id = ?
            LIMIT 1
            """,
            (SOURCE_DEFINITION["source_id"],),
        ).fetchone()
        assert snapshot["liquidity_usd"] == 50000
        assert snapshot["estimated_exit_slippage_pct"] == 0.4
        update, sources = rebuild_update_snapshots(
            db_path=db_path,
            update_path=root / "update-center.js",
            source_path=root / "source-registry.js",
        )
        assert any(
            item["taskId"] == "formal_market_exit_refresh"
            for item in update["tasks"]
        )
        assert any(
            item["taskId"] == "formal_market_exit_refresh"
            and item["eventType"] == "formal_project_market_exit_enrichment"
            for item in update["changes"]
        )
        formal_source = next(
            item
            for item in sources["sources"]
            if item["source_id"] == SOURCE_DEFINITION["source_id"]
        )
        assert formal_source["primaryTaskId"] == "formal_market_exit_refresh"
        assert formal_source["proves"]
        assert formal_source["doesNotProve"]

        insert_run(connection, "formal-market-test-2")
        repeated = persist_formal_market_exit(
            connection,
            build_bundle(connection),
            "formal-market-test-2",
            NOW,
            stable_id,
        )
        connection.commit()
        assert repeated["changedProjects"] == 0
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM market_snapshots
            WHERE data_source_id = ?
            """,
            (SOURCE_DEFINITION["source_id"],),
        ).fetchone()[0] == 2
        connection.close()

    print("C1.4-02 正式项目市场与退出资料自动补齐测试通过。")


if __name__ == "__main__":
    main()
