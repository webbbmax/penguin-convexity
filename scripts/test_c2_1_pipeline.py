#!/usr/bin/env python3
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import c2_1_pipeline as pipeline
from c2_1_db import initialize_database, open_pipeline_db


class C21PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "c2.1-pipeline.db"
        self.candidates = self.root / "candidate-tokens.jsonl"
        rows = [
            {"networkId": "ethereum-mainnet", "tokenAddress": "0x1111111111111111111111111111111111111111", "earliestCoveredPoolAt": "2026-08-08T00:00:00Z", "poolId": "pool-1", "dexIds": ["uniswap_v2"], "t0EvidenceType": "covered_dex_pool_created"},
            {"networkId": "base-mainnet", "tokenAddress": "0x2222222222222222222222222222222222222222", "earliestCoveredPoolAt": "2026-08-07T00:00:00Z", "poolId": "pool-2", "dexIds": ["uniswap-v2-base"], "t0EvidenceType": "covered_dex_pool_created"},
        ]
        self.candidates.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.old_status = pipeline.DEFAULT_STATUS_PATH
        pipeline.DEFAULT_STATUS_PATH = self.root / "status.json"
        initialize_database(self.db)

    def tearDown(self):
        pipeline.DEFAULT_STATUS_PATH = self.old_status
        self.temp.cleanup()

    def build_main(self):
        path = self.root / "main.db"
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(
                """
                CREATE TABLE projects(project_id TEXT PRIMARY KEY,canonical_name TEXT,website_domain TEXT,official_repo TEXT);
                CREATE TABLE assets(asset_id TEXT PRIMARY KEY,project_id TEXT,symbol TEXT);
                CREATE TABLE asset_contracts(network_id TEXT,contract_address TEXT,is_primary INTEGER,identity_status TEXT,asset_id TEXT);
                CREATE TABLE source_discoveries(source_id TEXT,matched_project_id TEXT,source_discovery_id TEXT,source_url TEXT,evidence_json TEXT,last_seen_at TEXT,project_identity_status TEXT,attribution_confidence TEXT,status TEXT);
                """
            )
            connection.execute("INSERT INTO projects VALUES('p1','Project One','one.example','https://github.com/example/repo')")
            connection.execute("INSERT INTO assets VALUES('a1','p1','ONE')")
            connection.execute("INSERT INTO asset_contracts VALUES('ethereum-mainnet','0x1111111111111111111111111111111111111111',1,'verified','a1')")
            connection.execute("INSERT INTO source_discoveries VALUES('discovery-defillama-protocols','p1','d1','https://defillama.com/protocol/one',?, '2026-08-10T00:00:00Z','verified','high','active')", (json.dumps({"tvlUsd": 1000}),))
            connection.commit()
        return path

    def test_resumable_import_and_read_only_mapping(self):
        with closing(open_pipeline_db(self.db)) as connection:
            first = pipeline.import_gate0_candidates(connection, "run-1", "development", self.candidates, batch_size=1)
            second = pipeline.import_gate0_candidates(connection, "run-2", "development", self.candidates, batch_size=1)
            self.assertEqual(first["imported"], 2)
            self.assertTrue(second["resumed"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 2)
            mapped = pipeline.sync_main_mappings(connection, self.build_main())
            self.assertEqual(mapped["mappedPrimaryAssets"], 1)
            row = connection.execute("SELECT relationship_class,t0_status FROM candidates WHERE network_id='ethereum-mainnet'").fetchone()
            self.assertEqual(tuple(row), ("C", "verified_in_supported_scope"))

    def test_atomic_snapshot_has_equal_front_counts(self):
        front = self.root / "front.js"
        back = self.root / "back.js"
        with closing(open_pipeline_db(self.db)) as connection:
            result = pipeline.build_snapshots(connection, front, back)
        self.assertEqual(result["frontVisibleCount"], result["hardGatePassedCount"])
        self.assertTrue(front.read_text(encoding="utf-8").startswith(pipeline.FRONT_PREFIX))
        self.assertTrue(back.read_text(encoding="utf-8").startswith(pipeline.BACKEND_PREFIX))


if __name__ == "__main__":
    unittest.main()
