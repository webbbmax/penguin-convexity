#!/usr/bin/env python3
"""Independent acceptance tests for the C2.2 candidate-production repair.

Expected outcomes are restated from the frozen acceptance plan.  The tests use
small temporary databases and never write to either production database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import candidate_production as system_under_test
import candidate_production_runtime as runtime_under_test
from c2_1_db import initialize_database, open_pipeline_db


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_LOCAL_STATES = {
    "local_pass",
    "known_continuation",
    "known_quote_or_wrapped_asset",
    "outside_90_days",
    "invalid_event_or_identity_conflict",
    "local_pending",
}
EXPECTED_MARKET_STATES = {
    "market_confirmed",
    "waiting_for_trades",
    "market_not_indexed",
    "market_identity_conflict",
    "source_pending",
}
EXPECTED_SOURCE_FAILURE_STATES = {
    "no_data",
    "quota_limited",
    "source_failure",
    "unsupported",
    "configuration_missing",
    "program_failure",
}
EXPECTED_NETWORKS = (
    "ethereum-mainnet",
    "solana-mainnet",
    "base-mainnet",
    "arbitrum-mainnet",
    "bnb-mainnet",
    "robinhood-mainnet",
)


def iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def insert_candidate(
    connection: sqlite3.Connection,
    *,
    network: str = "ethereum-mainnet",
    address: str,
    days: int = 10,
    source_run_id: str = "gate0-solfinal-20260809T045924Z-f7bbd2",
    t0_evidence: str = "covered_dex_pool_created",
    continuity: str = "unknown",
    relationship: str = "D",
    identity: str = "not_verified",
    project_id: str = "",
    asset_id: str = "",
    local_stage: str = "discovered",
) -> int:
    t0 = iso_days_ago(days)
    cursor = connection.execute(
        """
        INSERT INTO candidates(
          network_id,token_address,token_address_normalized,gate0_t0,effective_t0,t0_status,
          t0_evidence_type,source_run_id,first_seen_at,continuity_status,relationship_class,
          mapped_project_id,mapped_asset_id,identity_status,local_stage,created_at,updated_at
        ) VALUES(?,?,?,?,?,'not_verified',?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            network,
            address,
            address,
            t0,
            t0,
            t0_evidence,
            source_run_id,
            t0,
            continuity,
            relationship,
            project_id,
            asset_id,
            identity,
            local_stage,
            t0,
            t0,
        ),
    )
    return int(cursor.lastrowid)


class FixedProvider:
    def __init__(self, outcomes: dict[int, dict]):
        self.outcomes = outcomes
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def lookup(self, network_id: str, rows: list[sqlite3.Row]) -> dict[int, dict]:
        candidate_ids = tuple(int(row["candidate_id"]) for row in rows)
        self.calls.append((network_id, candidate_ids))
        return {candidate_id: self.outcomes[candidate_id] for candidate_id in candidate_ids}


class CandidateProductionIndependentAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "acceptance.db"
        initialize_database(self.db_path)
        system_under_test.migrate_database(self.db_path)
        self.connection = open_pipeline_db(self.db_path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def test_frozen_locks_and_inherited_rule_hashes_match(self) -> None:
        for lock_path in (
            ROOT / "docs" / "C2.2_REQUIREMENTS_LOCK.json",
            ROOT / "docs" / "C2.2_CANDIDATE_PRODUCTION_REPAIR_REQUIREMENTS_LOCK.json",
        ):
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            canonical = []
            for item in lock["documents"]:
                digest = hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest()
                self.assertEqual(item["sha256"], digest, item["path"])
                canonical.append(f"{item['path']}:{digest}")
            self.assertEqual(
                lock["requirementSetSha256"],
                hashlib.sha256("\n".join(canonical).encode()).hexdigest(),
            )
        original = json.loads((ROOT / "docs" / "C2.2_REQUIREMENTS_LOCK.json").read_text(encoding="utf-8"))
        for dependency in original["inheritedFrozenDependencies"]:
            self.assertEqual(
                dependency["sha256"],
                hashlib.sha256((ROOT / dependency["path"]).read_bytes()).hexdigest(),
                dependency["path"],
            )

    def test_schema_exposes_frozen_states_and_atomic_batches(self) -> None:
        migration = (ROOT / "storage" / "c2.2-candidate-production-migration.sql").read_text(encoding="utf-8")
        for value in ("daily_incremental", "historical_backlog"):
            self.assertIn(value, migration)
        for value in ("pending", "running", "retrying", "paused", "completed", "failed"):
            self.assertIn(f"'{value}'", migration)
        self.assertEqual(system_under_test.LOCAL_STATES, EXPECTED_LOCAL_STATES)
        self.assertEqual(set(system_under_test.NETWORKS), set(EXPECTED_NETWORKS))
        self.assertEqual(system_under_test.SOURCE_STATES - {"success"}, EXPECTED_SOURCE_FAILURE_STATES)

    def test_all_local_outcomes_are_mutually_exclusive_and_day_90_91_is_exact(self) -> None:
        candidate_ids = [
            insert_candidate(self.connection, address="0xpass", days=90),
            insert_candidate(self.connection, address="0xold", days=91),
            insert_candidate(self.connection, address="0xcontinuation", continuity="known_continuation"),
            insert_candidate(self.connection, address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
            insert_candidate(self.connection, address="0xfuture", days=-1),
            insert_candidate(self.connection, address="0xpending", source_run_id="", t0_evidence=""),
        ]
        rows = self.connection.execute(
            f"SELECT * FROM candidates WHERE candidate_id IN ({','.join('?' for _ in candidate_ids)}) ORDER BY candidate_id",
            candidate_ids,
        ).fetchall()
        outcomes = [system_under_test.classify_local(row, system_under_test.utc_now())["state"] for row in rows]
        self.assertEqual(
            outcomes,
            [
                "local_pass",
                "outside_90_days",
                "known_continuation",
                "known_quote_or_wrapped_asset",
                "invalid_event_or_identity_conflict",
                "local_pending",
            ],
        )
        self.assertEqual(len(outcomes), len(candidate_ids))
        self.assertTrue(set(outcomes) <= EXPECTED_LOCAL_STATES)

    def test_market_gate_uses_buy_sell_facts_not_fixed_financial_thresholds(self) -> None:
        candidate_id = insert_candidate(self.connection, address="0xlowmarket")
        self.connection.commit()
        prepared = system_under_test.prepare_partitions(
            self.connection,
            queue="historical_backlog",
            historical_authorized=True,
            partition_size=1,
        )
        self.assertEqual(prepared["createdMembers"], 1)
        partition = system_under_test.claim_next_partition(self.connection, "historical_backlog")
        provider = FixedProvider({
            candidate_id: {
                "sourceState": "success",
                "pairAddress": "pool-low",
                "tokenSide": "base",
                "buys": 1,
                "sells": 1,
                "liquidityUsd": 0,
                "volumeUsd": 0,
                "marketCapUsd": 0,
                "fdvUsd": 0,
            }
        })
        result = system_under_test.process_partition(
            self.connection,
            partition["partition_id"],
            provider=provider,
        )
        self.assertEqual(result["status"], "completed")
        row = self.connection.execute(
            "SELECT market_state,tracking_eligible,front_eligible FROM candidate_production_records WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(tuple(row), ("market_confirmed", 1, 0))

    def test_missing_trade_counts_remain_unknown_instead_of_becoming_zero_activity(self) -> None:
        candidate_id = insert_candidate(self.connection, address="0xmissingtrades")
        self.connection.commit()
        system_under_test.prepare_partitions(
            self.connection,
            queue="historical_backlog",
            historical_authorized=True,
            partition_size=1,
        )
        partition = system_under_test.claim_next_partition(self.connection, "historical_backlog")
        provider = FixedProvider({
            candidate_id: {
                "sourceState": "success",
                "pairAddress": "pool-missing",
                "tokenSide": "base",
                "buys": None,
                "sells": None,
            }
        })
        system_under_test.process_partition(self.connection, partition["partition_id"], provider=provider)
        row = self.connection.execute(
            "SELECT market_state,market_source_state,observed_buys,observed_sells,tracking_eligible FROM candidate_production_records WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(row["market_state"], "source_pending")
        self.assertEqual(row["market_source_state"], "no_data")
        self.assertIsNone(row["observed_buys"])
        self.assertIsNone(row["observed_sells"])
        self.assertEqual(row["tracking_eligible"], 0)

    def test_partial_partition_is_not_visible_and_completed_d_stays_backend_only(self) -> None:
        first_id = insert_candidate(self.connection, address="0xfirst", relationship="D")
        second_id = insert_candidate(self.connection, address="0xsecond", relationship="D")
        self.connection.commit()
        system_under_test.prepare_partitions(
            self.connection,
            queue="historical_backlog",
            historical_authorized=True,
            partition_size=2,
        )
        partition = system_under_test.claim_next_partition(self.connection, "historical_backlog")
        provider = FixedProvider({
            first_id: {"sourceState": "success", "pairAddress": "pool-1", "tokenSide": "base", "buys": 1, "sells": 1},
            second_id: {"sourceState": "success", "pairAddress": "pool-2", "tokenSide": "base", "buys": 1, "sells": 1},
        })
        paused = system_under_test.process_partition(
            self.connection,
            partition["partition_id"],
            provider=provider,
            market_batch_size=1,
            stop_after=3,
        )
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM candidate_qualification_batches").fetchone()[0], 0)
        self.assertEqual(system_under_test.funnel_status(self.connection)["trackingEligibleCount"], 0)
        completed = system_under_test.process_partition(
            self.connection,
            partition["partition_id"],
            provider=provider,
            market_batch_size=1,
        )
        self.assertEqual(completed["status"], "completed")
        rows = self.connection.execute(
            "SELECT project_id,tracking_eligible,front_eligible,qualification_batch_id FROM candidate_production_records ORDER BY candidate_id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["project_id"] is None for row in rows))
        self.assertTrue(all(row["tracking_eligible"] == 1 for row in rows))
        self.assertTrue(all(row["front_eligible"] == 0 for row in rows))
        self.assertTrue(all(row["qualification_batch_id"] for row in rows))

    def test_queue_interleaving_six_chain_rotation_and_duplicate_writer_rejection(self) -> None:
        for index, network in enumerate(EXPECTED_NETWORKS):
            insert_candidate(self.connection, network=network, address=f"token-{index}")
        insert_candidate(
            self.connection,
            address="0xdaily",
            source_run_id="daily-source",
            local_stage="incremental_discovered",
        )
        self.connection.commit()
        system_under_test.prepare_partitions(self.connection, queue="daily_incremental", partition_size=1)
        system_under_test.prepare_partitions(
            self.connection,
            queue="historical_backlog",
            historical_authorized=True,
            partition_size=1,
        )
        first = system_under_test.claim_next_partition(self.connection)
        self.assertEqual(first["queue_name"], "daily_incremental")
        self.connection.execute("UPDATE candidate_scan_partitions SET state='completed' WHERE partition_id=?", (first["partition_id"],))
        selected = []
        for _ in EXPECTED_NETWORKS:
            row = system_under_test.claim_next_partition(self.connection, "historical_backlog")
            selected.append(row["network_id"])
            self.connection.execute("UPDATE candidate_scan_partitions SET state='completed' WHERE partition_id=?", (row["partition_id"],))
            self.connection.commit()
        self.assertEqual(tuple(selected), EXPECTED_NETWORKS)
        lock_path = Path(self.temporary.name) / "worker.lock"
        with system_under_test.worker_lock(lock_path) as first_lock:
            self.assertTrue(first_lock)
            with system_under_test.worker_lock(lock_path) as second_lock:
                self.assertFalse(second_lock)

    def test_retry_cadence_and_eta_gate_are_exact(self) -> None:
        observed = "2026-08-11T00:00:00Z"
        expected_hours = {0: 6, 2: 6, 3: 24, 7: 24, 8: 72, 30: 72, 31: 168, 90: 168}
        start = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        for age, hours in expected_hours.items():
            retry = datetime.fromisoformat(system_under_test.retry_at_for_age(age, observed).replace("Z", "+00:00"))
            self.assertEqual((retry - start).total_seconds(), hours * 3600)
        self.assertIsNone(system_under_test.retry_at_for_age(91, observed))
        status = system_under_test.funnel_status(self.connection)
        self.assertLess(status["stablePartitionCount"], 5)
        self.assertIsNone(status["etaSeconds"])
        self.assertIsNone(status["etaConfidence"])

    def test_formal_history_start_is_denied_before_separate_authorization(self) -> None:
        with (
            patch.object(runtime_under_test, "load_config", return_value={
                "formalHistoricalScanAuthorized": False,
                "paused": False,
            }),
            patch.object(runtime_under_test.subprocess, "Popen") as popen,
        ):
            result = runtime_under_test.launch_hidden("historical_backlog")
        self.assertEqual(result["status"], "not_authorized")
        popen.assert_not_called()

    def test_page_contract_keeps_three_responsibilities_separate(self) -> None:
        script = (ROOT / "app" / "c2-2-admin.js").read_text(encoding="utf-8")
        screening = (ROOT / "app" / "new-token-update.html").read_text(encoding="utf-8")
        tracking = (ROOT / "app" / "update-center.html").read_text(encoding="utf-8")
        self.assertIn("历史候选基础扫描", script)
        self.assertIn("90天新币筛选", script)
        self.assertIn("凸性跟踪", script)
        self.assertIn("candidateProductionBlock", script)
        self.assertIn("其中 D 类后台资产", script)
        self.assertIn("进入后台跟踪不等于进入机会前台", script)
        self.assertIn("page === \"new-token-update.html\"", script)
        self.assertIn("page === \"update-center.html\"", script)
        self.assertIn("c2-2-admin.js?v=c22-5", screening)
        self.assertIn("c2-2-admin.js?v=c22-5", tracking)


if __name__ == "__main__":
    unittest.main(verbosity=2)
