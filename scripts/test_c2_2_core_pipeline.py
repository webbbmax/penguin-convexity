#!/usr/bin/env python3
"""Highest-priority regressions for the C2.2 screening-to-tracking pipeline."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import candidate_production as production
import c2_2_candidate_tracking as candidate_tracking
from c2_1_db import initialize_database, open_pipeline_db
from c2_2_tracking import load_tracking_candidates


def iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


class FakeProvider:
    def __init__(self, results):
        self.results = results

    def lookup(self, _network_id, rows):
        return {int(row["candidate_id"]): self.results[int(row["candidate_id"])] for row in rows}


class C22CorePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "pipeline.db"
        initialize_database(self.db_path)
        production.migrate_database(self.db_path)
        self.connection = open_pipeline_db(self.db_path)
        candidate_tracking.initialize_tracking_schema(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def add_candidate(self, address: str, *, days: int = 10) -> int:
        t0 = iso_days_ago(days)
        cursor = self.connection.execute(
            """
            INSERT INTO candidates(
              network_id,token_address,token_address_normalized,gate0_t0,effective_t0,t0_status,
              t0_evidence_type,source_run_id,first_seen_at,continuity_status,relationship_class,
              mapped_project_id,mapped_asset_id,identity_status,local_stage,created_at,updated_at
            ) VALUES('base-mainnet',?,?,?,?,'verified_in_supported_scope','factory_event',?,?,
              'candidate_asset','D','','','not_verified','discovered',?,?)
            """,
            (address, address, t0, t0, production.GATE0_RUN_ID, t0, t0, t0),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def process_market(self, candidate_id: int, result: dict) -> None:
        production.prepare_partitions(
            self.connection,
            queue="historical_backlog",
            historical_authorized=True,
            partition_size=1,
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        completed = production.process_partition(
            self.connection,
            partition["partition_id"],
            provider=FakeProvider({candidate_id: result}),
        )
        self.assertEqual(completed["status"], "completed")

    def test_market_scan_preserves_identity_inputs_and_earliest_real_t0(self):
        candidate_id = self.add_candidate("0xmarket-metadata", days=10)
        earlier = datetime.now(timezone.utc) - timedelta(days=20)
        self.process_market(candidate_id, {
            "sourceState": "success",
            "pairAddress": "pool-metadata",
            "tokenSide": "base",
            "buys": 3,
            "sells": 2,
            "pairCreatedAt": int(earlier.timestamp() * 1000),
            "tokenName": "New Project",
            "tokenSymbol": "NEW",
            "website": "https://new.example",
        })

        candidate = self.connection.execute(
            """SELECT canonical_name,symbol,website_domain,identity_status,effective_t0,t0_status
            FROM candidates WHERE candidate_id=?""",
            (candidate_id,),
        ).fetchone()
        production_row = self.connection.execute(
            "SELECT effective_t0,age_days FROM candidate_production_records WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        market = self.connection.execute(
            "SELECT pair_created_at FROM market_observations WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()

        self.assertEqual(tuple(candidate[:4]), ("New Project", "NEW", "https://new.example", "market_matched"))
        self.assertEqual(candidate["t0_status"], "verified_in_supported_scope")
        self.assertEqual(candidate["effective_t0"], production_row["effective_t0"])
        self.assertGreaterEqual(int(production_row["age_days"]), 19)
        self.assertTrue(str(market["pair_created_at"]).startswith(earlier.strftime("%Y-%m-%d")))
        handoff = self.connection.execute(
            "SELECT source_queue,state FROM candidate_first_gate_queue WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(tuple(handoff), ("historical_backlog", "pending"))

    def test_four_check_first_gate_reaches_tracking_without_deep_evidence(self):
        passed_id = self.add_candidate("0xpassed")
        blocked_id = self.add_candidate("0xmarket-only")
        self.process_market(passed_id, {
            "sourceState": "success", "pairAddress": "pool-pass", "tokenSide": "base",
            "buys": 2, "sells": 2, "tokenName": "Passed", "tokenSymbol": "PASS",
            "website": "https://passed.example",
        })
        self.process_market(blocked_id, {
            "sourceState": "success", "pairAddress": "pool-market", "tokenSide": "base",
            "buys": 4, "sells": 0, "tokenName": "One Way Only", "tokenSymbol": "ONE",
        })

        result = production.process_first_gate_candidates(
            self.connection,
            candidate_ids=[passed_id, blocked_id],
            refresh_market=False,
        )

        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM candidate_first_gate_queue WHERE candidate_id=?",
                (passed_id,),
            ).fetchone()[0],
            "completed",
        )
        passed = self.connection.execute(
            """SELECT project_id,relationship_class,identity_consistent,front_contract_ready,front_eligible
            FROM candidate_production_records WHERE candidate_id=?""",
            (passed_id,),
        ).fetchone()
        blocked = self.connection.execute(
            "SELECT tracking_eligible,front_eligible FROM candidate_production_records WHERE candidate_id=?",
            (blocked_id,),
        ).fetchone()
        self.assertEqual(tuple(passed), (None, "D", 1, 0, 0))
        self.assertEqual(tuple(blocked), (0, 0))

        selected = candidate_tracking._select_candidates(self.connection, 20)
        self.assertEqual([int(row["candidate_id"]) for row in selected], [passed_id])
        self.connection.commit()
        handoff = load_tracking_candidates(self.db_path)
        self.assertEqual([int(row["_candidateId"]) for row in handoff], [passed_id])
        self.assertIsNone(handoff[0]["projectId"])


if __name__ == "__main__":
    unittest.main()
