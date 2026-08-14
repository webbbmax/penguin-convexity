#!/usr/bin/env python3
"""Candidate-production coverage, recovery, and queue regression tests."""

from __future__ import annotations

import tempfile
import unittest
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import candidate_production as production
import candidate_production_runtime as production_runtime
import c2_1_enrichment
import c2_1_pipeline
import c2_2_candidate_tracking as candidate_tracking
from c2_1_db import initialize_database, open_pipeline_db


def iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def add_candidate(connection, *, network="ethereum-mainnet", address, days=10,
                  source_run_id=production.GATE0_RUN_ID, continuity="unknown",
                  relationship="D", project="", asset="", identity="not_verified",
                  t0_evidence="factory_event", local_stage="discovered") -> int:
    t0 = iso_days_ago(days)
    cursor = connection.execute(
        """
        INSERT INTO candidates(
          network_id,token_address,token_address_normalized,gate0_t0,effective_t0,t0_status,
          t0_evidence_type,source_run_id,first_seen_at,continuity_status,relationship_class,
          mapped_project_id,mapped_asset_id,identity_status,local_stage,created_at,updated_at
        ) VALUES(?,?,?,?,?,'verified_in_supported_scope',?,?,?,?,?,?,?,?,?,?,?)
        """,
        (network, address, address, t0, t0, t0_evidence, source_run_id, t0, continuity,
         relationship, project, asset, identity, local_stage, t0, t0),
    )
    return int(cursor.lastrowid)


class FakeProvider:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def lookup(self, network_id, rows):
        ids = [int(row["candidate_id"]) for row in rows]
        self.calls.append((network_id, ids))
        return {candidate_id: self.results[candidate_id] for candidate_id in ids}


class CandidateProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "pipeline.db"
        initialize_database(self.db_path)
        production.migrate_database(self.db_path)
        self.connection = open_pipeline_db(self.db_path)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_repair_requirement_lock_is_unchanged(self):
        root = Path(__file__).resolve().parent.parent
        lock = json.loads((root / "docs" / "C2.2_CANDIDATE_PRODUCTION_REPAIR_REQUIREMENTS_LOCK.json").read_text(encoding="utf-8"))
        canonical = []
        for item in lock["documents"]:
            digest = hashlib.sha256((root / item["path"]).read_bytes()).hexdigest()
            self.assertEqual(digest, item["sha256"], item["path"])
            canonical.append(f"{item['path']}:{digest}")
        self.assertEqual(hashlib.sha256("\n".join(canonical).encode()).hexdigest(), lock["requirementSetSha256"])
        dependency = lock["inheritedFrozenDependency"]
        self.assertEqual(hashlib.sha256((root / dependency["path"]).read_bytes()).hexdigest(), dependency["sha256"])

    def test_partition_membership_lookup_has_candidate_index(self):
        indexes = {
            row[1] for row in self.connection.execute(
                "PRAGMA index_list(candidate_scan_partition_members)"
            ).fetchall()
        }
        self.assertIn("idx_candidate_scan_members_candidate", indexes)
        evaluation_indexes = {
            row[1] for row in self.connection.execute(
                "PRAGMA index_list(evaluations)"
            ).fetchall()
        }
        self.assertIn("idx_c22_evaluations_candidate_current", evaluation_indexes)

    def test_every_candidate_gets_one_deterministic_local_state(self):
        ids = [
            add_candidate(self.connection, address="0xpass"),
            add_candidate(self.connection, address="0xcontinued", continuity="known_continuation"),
            add_candidate(self.connection, address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
            add_candidate(self.connection, address="0xold", days=91),
            add_candidate(self.connection, address="0xfuture", days=-1),
            add_candidate(self.connection, address="0xpending", source_run_id="", t0_evidence=""),
        ]
        rows = self.connection.execute("SELECT * FROM candidates ORDER BY candidate_id").fetchall()
        states = [production.classify_local(row, production.utc_now())["state"] for row in rows]
        self.assertEqual(
            states,
            ["local_pass", "known_continuation", "known_quote_or_wrapped_asset",
             "outside_90_days", "invalid_event_or_identity_conflict", "local_pending"],
        )
        self.assertEqual(len(ids), len(states))
        self.assertTrue(set(states) <= production.LOCAL_STATES)

    def test_retry_cadence_is_age_based_scheduling_only(self):
        observed = "2026-08-11T00:00:00Z"
        expected_hours = {0: 6, 2: 6, 3: 24, 7: 24, 8: 72, 30: 72, 31: 168, 90: 168}
        start = production.parse_time(observed)
        for age, hours in expected_hours.items():
            retry = production.parse_time(production.retry_at_for_age(age, observed))
            self.assertEqual((retry - start).total_seconds(), hours * 3600)
        self.assertIsNone(production.retry_at_for_age(91, observed))
        self.assertIsNone(production.retry_at_for_age(None, observed))

    def test_history_partition_requires_explicit_authorization(self):
        add_candidate(self.connection, address="0xhistory")
        self.connection.commit()
        rejected = production.prepare_partitions(self.connection, queue="historical_backlog")
        self.assertEqual(rejected["status"], "not_authorized")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM candidate_scan_partitions").fetchone()[0], 0)
        accepted = production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        self.assertEqual(accepted["createdMembers"], 1)
        self.assertTrue(production.historical_queue_is_fully_prepared(self.connection))

    def test_history_resume_reuses_complete_partition_plan(self):
        add_candidate(self.connection, address="0xplan-a")
        add_candidate(self.connection, address="0xplan-b", network="solana-mainnet")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        self.assertTrue(production.historical_queue_is_fully_prepared(self.connection))
        with patch.object(production, "prepare_partitions", wraps=production.prepare_partitions) as prepare:
            result = production.run_worker(
                db_path=self.db_path, historical_authorized=True, max_partitions=0,
                lock_path=Path(self.temp.name) / "worker-plan.lock",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual([call.kwargs.get("queue") for call in prepare.call_args_list], ["daily_incremental"])

    def test_daily_queue_only_enqueues_new_or_due_candidates(self):
        candidate_id = add_candidate(
            self.connection, address="0xdaily", source_run_id="daily-run", local_stage="incremental_discovered"
        )
        self.connection.commit()
        first = production.prepare_partitions(self.connection, queue="daily_incremental", partition_size=1)
        self.assertEqual(first["createdMembers"], 1)
        partition = production.claim_next_partition(self.connection, "daily_incremental")
        provider = FakeProvider({candidate_id: {
            "sourceState": "success", "pairAddress": "0xpool", "tokenSide": "base", "buys": 2, "sells": 2
        }})
        completed = production.process_partition(self.connection, partition["partition_id"], provider=provider)
        self.assertEqual(completed["status"], "completed")
        daily = production.funnel_status(self.connection)["queueSummaries"]["daily_incremental"]
        self.assertEqual(daily["queuedCandidateCount"], 1)
        self.assertEqual(daily["localScannedCount"], 1)
        self.assertEqual(daily["marketConfirmedCount"], 1)
        self.assertEqual(daily["trackingEligibleCount"], 1)
        self.assertEqual(daily["firstGatePendingCount"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT continuity_status FROM candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()[0],
            "candidate_asset",
        )
        second = production.prepare_partitions(self.connection, queue="daily_incremental", partition_size=1)
        self.assertEqual(second["createdMembers"], 0)

    def test_backfill_materializes_all_market_confirmations_into_t0_first_gate_queue(self):
        ids = [add_candidate(self.connection, address=f"0xlegacy-{index}") for index in range(2)]
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=2
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        provider = FakeProvider({
            candidate_id: {
                "sourceState": "success", "pairAddress": f"pool-{candidate_id}",
                "tokenSide": "base", "buys": 2, "sells": 1,
            }
            for candidate_id in ids
        })
        production.process_partition(self.connection, partition["partition_id"], provider=provider)
        self.connection.execute("DELETE FROM candidate_first_gate_queue")
        self.connection.execute(
            "UPDATE candidates SET t0_status='not_verified' WHERE candidate_id IN (?,?)",
            tuple(ids),
        )
        self.connection.commit()

        result = production.backfill_first_gate_handoff(self.connection)

        self.assertEqual(result, {"queued": 2, "completed": 0, "pending": 2, "failed": 0})
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidates WHERE candidate_id IN (?,?) AND t0_status='verified_in_supported_scope'",
                tuple(ids),
            ).fetchone()[0],
            2,
        )
        status = production.funnel_status(self.connection)
        self.assertEqual(status["t0HandoffCount"], 2)
        self.assertEqual(status["firstGateQueuedCount"], 2)
        self.assertEqual(status["firstGatePendingCount"], 2)

    def test_expired_first_gate_record_is_not_reported_as_waiting_work(self):
        candidate_id = add_candidate(self.connection, address="0xexpired-before-gate")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        production.process_partition(
            self.connection,
            partition["partition_id"],
            provider=FakeProvider({candidate_id: {
                "sourceState": "success",
                "pairAddress": "pool-expired",
                "pairCreatedAt": iso_days_ago(120),
                "tokenSide": "base",
                "buys": 3,
                "sells": 2,
            }}),
        )

        status = production.funnel_status(self.connection)

        self.assertEqual(status["firstGatePendingCount"], 0)
        self.assertEqual(status["firstGateOutsideWindowCount"], 1)
        self.assertEqual(
            status["queueSummaries"]["historical_backlog"]["firstGateOutsideWindowCount"],
            1,
        )

    def test_tracking_selection_gives_each_chain_a_fair_first_slot(self):
        candidate_ids = [
            add_candidate(self.connection, address=f"0xeth-{index}")
            for index in range(4)
        ]
        candidate_ids.append(
            add_candidate(
                self.connection,
                network="robinhood-mainnet",
                address="0xrobinhood",
            )
        )
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=10
        )
        provider = FakeProvider({candidate_id: {
            "sourceState": "success",
            "pairAddress": f"pool-{candidate_id}",
            "tokenSide": "base",
            "buys": 2,
            "sells": 2,
        } for candidate_id in candidate_ids})
        while True:
            partition = production.claim_next_partition(self.connection, "historical_backlog")
            if not partition:
                break
            production.process_partition(self.connection, partition["partition_id"], provider=provider)
        self.connection.execute("UPDATE candidate_first_gate_queue SET state='completed'")
        self.connection.commit()
        candidate_tracking.initialize_tracking_schema(self.connection)

        selected = candidate_tracking._select_candidates(self.connection, 2)

        self.assertEqual(
            {row["network_id"] for row in selected},
            {"ethereum-mainnet", "robinhood-mainnet"},
        )

    def test_t0_change_reopens_completed_first_gate_and_recalculates(self):
        candidate_id = add_candidate(self.connection, address="0xt0-reopen")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        production.process_partition(
            self.connection,
            partition["partition_id"],
            provider=FakeProvider({candidate_id: {
                "sourceState": "success", "pairAddress": "pool-t0", "tokenSide": "base",
                "buys": 2, "sells": 2,
            }}),
        )
        self.connection.execute(
            "UPDATE candidate_first_gate_queue SET state='completed',completed_at=? WHERE candidate_id=?",
            (production.utc_now(), candidate_id),
        )
        first = c2_1_pipeline.evaluate_all(self.connection, candidate_ids=[candidate_id])
        self.assertEqual(first["evaluated"], 1)
        old_evaluated_at = self.connection.execute(
            "SELECT evaluated_at FROM evaluations WHERE candidate_id=? AND is_current=1",
            (candidate_id,),
        ).fetchone()[0]
        corrected_t0 = iso_days_ago(20)
        changed_at = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
        self.connection.execute(
            "UPDATE candidates SET effective_t0=?,updated_at=? WHERE candidate_id=?",
            (corrected_t0, changed_at, candidate_id),
        )
        self.connection.commit()
        production.refresh_production_contracts(self.connection, [candidate_id])

        handoff = production.backfill_first_gate_handoff(
            self.connection, candidate_ids=[candidate_id]
        )
        queue_state = self.connection.execute(
            "SELECT state FROM candidate_first_gate_queue WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0]
        self.assertEqual(handoff["pending"], 1)
        self.assertEqual(queue_state, "pending")

        result = production.process_first_gate_candidates(
            self.connection, candidate_ids=[candidate_id], refresh_market=False
        )
        self.assertEqual(result["evaluated"], 1)
        unchanged_deep_evaluation = self.connection.execute(
            "SELECT evaluated_at FROM evaluations WHERE candidate_id=? AND is_current=1",
            (candidate_id,),
        ).fetchone()[0]
        self.assertEqual(unchanged_deep_evaluation, old_evaluated_at)
        first_gate_history = self.connection.execute(
            "SELECT age_days_at_pass,rule_version FROM c2_4_first_gate_history WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        self.assertGreaterEqual(int(first_gate_history[0]), 19)
        self.assertLessEqual(int(first_gate_history[0]), 20)
        self.assertEqual(first_gate_history[1], "c2.4-first-gate-v1")
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM candidate_first_gate_queue WHERE candidate_id=?", (candidate_id,)
            ).fetchone()[0],
            "completed",
        )

    def test_queue_reconciliation_reuses_only_fresh_completed_evaluations(self):
        candidate_id = add_candidate(self.connection, address="0xqueue-reconcile")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        production.process_partition(
            self.connection,
            partition["partition_id"],
            provider=FakeProvider({candidate_id: {
                "sourceState": "success", "pairAddress": "pool-reconcile", "tokenSide": "base",
                "buys": 2, "sells": 2,
            }}),
        )
        c2_1_pipeline.evaluate_all(self.connection, candidate_ids=[candidate_id])
        self.connection.execute(
            "UPDATE candidate_first_gate_queue SET state='pending',completed_at=NULL WHERE candidate_id=?",
            (candidate_id,),
        )
        self.connection.commit()

        restored = production.reconcile_first_gate_queue_from_evaluations(self.connection)

        self.assertEqual(restored["completed"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM candidate_first_gate_queue WHERE candidate_id=?", (candidate_id,)
            ).fetchone()[0],
            "completed",
        )
        future = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        self.connection.execute(
            "UPDATE candidate_production_records SET updated_at=? WHERE candidate_id=?",
            (future, candidate_id),
        )
        self.connection.commit()

        reopened = production.reconcile_first_gate_queue_from_evaluations(self.connection)

        self.assertEqual(reopened["pending"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM candidate_first_gate_queue WHERE candidate_id=?", (candidate_id,)
            ).fetchone()[0],
            "pending",
        )

    def test_github_cannot_promote_pending_asset_identity_to_relationship_c(self):
        candidate_id = add_candidate(
            self.connection,
            address="0xpending-identity",
            continuity="candidate_asset",
            relationship="D",
            identity="pending",
        )
        self.connection.execute(
            "UPDATE candidates SET official_repo='https://github.com/example/project' WHERE candidate_id=?",
            (candidate_id,),
        )
        self.connection.commit()

        class GithubClient:
            def request(self, source, url, **_kwargs):
                if "/commits?" in url:
                    return "success", [{"sha": "abc", "commit": {"committer": {"date": production.utc_now()}}}], 200, []
                if "/commits/abc" in url:
                    return "success", {"files": [{"filename": "src/app.py"}]}, 200, []
                return "program_failure", {}, None, []

        repository = {
            "full_name": "example/project", "html_url": "https://github.com/example/project",
            "owner": {"login": "example"}, "fork": False, "archived": False, "size": 10,
            "language": "Python",
        }
        with patch.object(
            c2_1_enrichment,
            "resolve_github_repository",
            return_value=("success", repository, 200, []),
        ):
            c2_1_enrichment.collect_github(
                self.connection, client=GithubClient(), candidate_ids=[candidate_id]
            )

        evidence = self.connection.execute(
            "SELECT status,identity_status FROM product_evidence WHERE candidate_id=? AND source_name='GitHub官方仓库'",
            (candidate_id,),
        ).fetchone()
        candidate = self.connection.execute(
            "SELECT relationship_class,identity_status FROM candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        self.assertEqual(tuple(evidence), ("pending", "pending"))
        self.assertEqual(tuple(candidate), ("D", "pending"))

    def test_github_discovery_only_accepts_explicit_repository_anchors(self):
        html = """
        <script>console.log('https://github.com/vendor/layout-helper')</script>
        <a href="https://github.com/project/official-repository/issues">Source</a>
        <a href="https://github.com/project">Profile only</a>
        """
        self.assertEqual(
            c2_1_enrichment.github_links_from_html(html),
            ["https://github.com/project/official-repository"],
        )

    def test_github_profile_does_not_select_an_unrelated_recent_repository(self):
        class NoRequestClient:
            def request(self, *_args, **_kwargs):
                raise AssertionError("profile-only target must not call GitHub repository listing")

        state, repository, _http, attempts = c2_1_enrichment.resolve_github_repository(
            NoRequestClient(), {"owner": "example", "repository": ""}, {}
        )
        self.assertEqual(state, "no_data")
        self.assertEqual(repository, {})
        self.assertEqual(attempts[0]["reason"], "github_profile_is_not_a_specific_repository")

    def test_website_recheck_retracts_script_embedded_repository_mapping(self):
        candidate_id = add_candidate(
            self.connection,
            address="0xscript-repository",
            continuity="candidate_asset",
            relationship="C",
            identity="market_matched",
        )
        self.connection.execute(
            """UPDATE candidates SET website_domain=?,official_repo=? WHERE candidate_id=?""",
            ("https://project.example", "https://github.com/vendor/layout-helper", candidate_id),
        )
        self.connection.execute(
            """INSERT INTO product_evidence(
                 evidence_id,candidate_id,evidence_type,status,identity_status,source_name,
                 source_url,observed_at,payload_json,boundary_note
               ) VALUES(?,?,'github','qualifying','market_matched','GitHub官方仓库',?,?,?,?)""",
            (
                "github-script-mapping",
                candidate_id,
                "https://github.com/vendor/layout-helper",
                production.utc_now(),
                "{}",
                "old mapping",
            ),
        )
        self.connection.commit()

        class WebsiteClient:
            def text(self, *_args, **_kwargs):
                return (
                    "success",
                    "<script>https://github.com/vendor/layout-helper</script>",
                    200,
                    [],
                )

        result = c2_1_enrichment.collect_website_identity(
            self.connection,
            client=WebsiteClient(),
            candidate_ids=[candidate_id],
            force_recheck=True,
        )
        candidate = self.connection.execute(
            "SELECT official_repo,relationship_class FROM candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        evidence = self.connection.execute(
            "SELECT status FROM product_evidence WHERE evidence_id='github-script-mapping'"
        ).fetchone()
        health = self.connection.execute(
            """SELECT status FROM source_health
               WHERE source_id='project_website_identity' AND scope_key=?""",
            (str(candidate_id),),
        ).fetchone()
        self.assertEqual(result["retractedMappings"], 1)
        self.assertEqual(tuple(candidate), ("", "D"))
        self.assertEqual(evidence[0], "non_qualifying")
        self.assertEqual(health[0], "no_data")

    def test_all_five_product_evidence_categories_have_honest_states(self):
        candidate_id = add_candidate(
            self.connection,
            address="0xusage-series",
            continuity="candidate_asset",
            relationship="D",
            project="project-usage",
            asset="asset-usage",
            identity="verified",
        )
        self.connection.commit()
        main_db = Path(self.temp.name) / "main.db"
        main = sqlite3.connect(main_db)
        main.executescript(
            """
            CREATE TABLE raw_events(raw_event_id TEXT PRIMARY KEY,source_url TEXT,raw_payload_json TEXT);
            CREATE TABLE normalized_events_v2(
              raw_event_id TEXT,project_id TEXT,event_type TEXT,event_time TEXT,
              processing_status TEXT,attribution_status TEXT
            );
            """
        )
        for index, value in enumerate((10.0, 15.0), start=1):
            raw_id = f"raw-{index}"
            main.execute(
                "INSERT INTO raw_events VALUES(?,?,?)",
                (raw_id, "https://defillama.com/protocol/example", json.dumps({"metric": {"value": value}})),
            )
            main.execute(
                "INSERT INTO normalized_events_v2 VALUES(?,?,'protocol_adoption_snapshot',?,'evidence_ready','verified')",
                (raw_id, "project-usage", f"2026-08-0{index}T00:00:00Z"),
            )
        main.commit()
        main.close()

        result = c2_1_pipeline.sync_product_evidence_states(
            self.connection, [candidate_id], main_db_path=main_db
        )
        states = dict(self.connection.execute(
            "SELECT evidence_type,status FROM product_evidence WHERE candidate_id=?",
            (candidate_id,),
        ).fetchall())
        self.assertEqual(set(states), {"github", "business", "deployed_product", "token_utility", "product_usage"})
        self.assertEqual(states["github"], "no_data")
        self.assertEqual(states["business"], "no_data")
        self.assertEqual(states["deployed_product"], "unsupported")
        self.assertEqual(states["token_utility"], "unsupported")
        self.assertEqual(states["product_usage"], "qualifying")
        self.assertEqual(result["productUsageSeries"], 1)
        self.assertEqual(result["relationshipUpgraded"], 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT relationship_class FROM candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()[0],
            "C",
        )

    def test_first_gate_does_not_collect_deep_evidence(self):
        candidate_id = add_candidate(
            self.connection,
            address="0xadmission-risk",
            continuity="candidate_asset",
            relationship="C",
            project="project-risk",
            asset="asset-risk",
            identity="verified",
        )
        self.connection.execute(
            """INSERT INTO product_evidence(
               evidence_id,candidate_id,evidence_type,status,identity_status,source_name,observed_at
               ) VALUES('risk-product',?,'github','qualifying','verified','GitHub',?)""",
            (candidate_id, production.utc_now()),
        )
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        production.process_partition(
            self.connection,
            partition["partition_id"],
            provider=FakeProvider({candidate_id: {
                "sourceState": "success", "pairAddress": "pool-risk", "tokenSide": "base",
                "buys": 2, "sells": 2,
            }}),
        )
        with (
            patch.object(c2_1_enrichment, "collect_website_identity") as website,
            patch.object(c2_1_enrichment, "collect_github") as github,
            patch.object(c2_1_enrichment, "collect_risk_and_supply") as risk,
            patch.object(c2_1_pipeline, "sync_product_evidence_states") as product_sync,
        ):
            result = production.process_first_gate_candidates(
                self.connection, candidate_ids=[candidate_id], refresh_market=False
            )
        self.assertEqual(result["firstGatePassed"], 1)
        website.assert_not_called()
        github.assert_not_called()
        risk.assert_not_called()
        product_sync.assert_not_called()

    def test_unchanged_contract_refresh_does_not_invalidate_evaluation_time(self):
        candidate_id = add_candidate(
            self.connection,
            address="0xstable-contract",
            continuity="candidate_asset",
            relationship="C",
            project="project-stable",
            asset="asset-stable",
            identity="verified",
        )
        observed_at = production.utc_now()
        self.connection.execute(
            """INSERT INTO product_evidence(
               evidence_id,candidate_id,evidence_type,status,identity_status,source_name,observed_at
               ) VALUES('stable-product',?,'github','qualifying','verified','GitHub',?)""",
            (candidate_id, observed_at),
        )
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        production.process_partition(
            self.connection,
            partition["partition_id"],
            provider=FakeProvider({candidate_id: {
                "sourceState": "success", "pairAddress": "pool-stable", "tokenSide": "base",
                "buys": 2, "sells": 2,
            }}),
        )
        production.refresh_production_contracts(self.connection, [candidate_id])
        before = self.connection.execute(
            "SELECT updated_at FROM candidate_production_records WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0]
        refreshed = production.refresh_production_contracts(self.connection, [candidate_id])
        after = self.connection.execute(
            "SELECT updated_at FROM candidate_production_records WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0]
        self.assertEqual(refreshed, [])
        self.assertEqual(after, before)

    def test_first_gate_waits_for_atomic_qualification_batch(self):
        candidate_id = add_candidate(self.connection, address="0xatomic-wait")
        checked_at = production.utc_now()
        row = self.connection.execute(
            "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        production._upsert_local(
            self.connection,
            row,
            production.classify_local(row, checked_at),
            checked_at,
        )
        self.connection.execute(
            """UPDATE candidate_production_records SET market_state='market_confirmed',
               tracking_eligible=1,qualification_batch_id=NULL,qualified_at=NULL WHERE candidate_id=?""",
            (candidate_id,),
        )
        production.enqueue_first_gate_candidate(
            self.connection, candidate_id, source_queue="historical_backlog"
        )
        self.connection.commit()
        self.assertEqual(production.pending_first_gate_candidate_ids(self.connection), [])

        self.connection.execute(
            """INSERT INTO candidate_scan_partitions(
               partition_id,queue_name,network_id,input_hash,state,total_count,created_at,updated_at
               ) VALUES('atomic-part','historical_backlog','ethereum-mainnet','hash','completed',1,?,?)""",
            (checked_at, checked_at),
        )
        self.connection.execute(
            """INSERT INTO candidate_qualification_batches(
               qualification_batch_id,partition_id,state,candidate_count,created_at,completed_at,input_hash
               ) VALUES('atomic-batch','atomic-part','completed',1,?,?, 'hash')""",
            (checked_at, checked_at),
        )
        self.connection.execute(
            """UPDATE candidate_production_records SET qualification_batch_id='atomic-batch',qualified_at=?
               WHERE candidate_id=?""",
            (checked_at, candidate_id),
        )
        self.connection.commit()
        self.assertEqual(production.pending_first_gate_candidate_ids(self.connection), [candidate_id])

    def test_history_worker_does_not_repeat_full_daily_prepare_between_partitions(self):
        add_candidate(self.connection, address="0xhourly", source_run_id="daily-run", local_stage="incremental_discovered")
        self.connection.commit()
        with patch.object(production, "prepare_partitions", wraps=production.prepare_partitions) as prepare:
            first = production.prepare_daily_if_due(self.connection, partition_size=1)
            second = production.prepare_daily_if_due(self.connection, partition_size=1)
        self.assertEqual(first["createdMembers"], 1)
        self.assertEqual(second["status"], "not_due")
        self.assertEqual(prepare.call_count, 1)

    def test_handoff_evaluates_only_newly_qualified_candidates(self):
        old_id = add_candidate(self.connection, address="0xold-qualified")
        new_id = add_candidate(self.connection, address="0xnew-qualified")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=2
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        provider = FakeProvider({
            old_id: {"sourceState": "success", "pairAddress": "old-pool", "tokenSide": "base", "buys": 1, "sells": 1},
            new_id: {"sourceState": "success", "pairAddress": "new-pool", "tokenSide": "base", "buys": 1, "sells": 1},
        })
        production.process_partition(self.connection, partition["partition_id"], provider=provider)
        self.connection.execute(
            "UPDATE candidate_first_gate_queue SET state='completed',completed_at=?",
            (production.utc_now(),),
        )
        self.connection.commit()
        qualified = self.connection.execute(
            "SELECT qualified_at FROM candidate_production_records WHERE candidate_id=?", (old_id,)
        ).fetchone()[0]
        _, rule_hash = c2_1_pipeline.load_rules()
        self.connection.execute(
            """INSERT INTO evaluations(
              evaluation_id,candidate_id,evaluation_window_id,evaluated_at,rule_version,rule_config_hash,
              cohort_snapshot_id,cohort_scope,cohort_sample_size,age_days,age_band,hard_gate_status,
              hard_gate_json,display_state,display_reason,paths_json,factor_directions_json,confidence_json,
              threshold_context_json,market_snapshot_json,source_impact_json,sort_score,sort_reason,
              consecutive_completed_misses,is_current
            ) VALUES('old-eval',?,'old-window',?,'test',?,'test','test',0,10,'age_7_13','fail',
              '{}','observing','test','[]','[]','{}','{}','{}','{}',0,'test',0,1)""",
            (old_id, qualified, rule_hash),
        )
        self.connection.commit()
        result = c2_1_pipeline.evaluate_all(self.connection, pending_qualification_only=True)
        self.assertEqual(result["evaluated"], 1)
        self.assertIsNotNone(self.connection.execute(
            "SELECT 1 FROM evaluations WHERE candidate_id=? AND is_current=1", (new_id,)
        ).fetchone())

    def test_tracking_handoff_batch_is_bounded_and_uses_production_t0(self):
        ids = [add_candidate(self.connection, address=f"0xhandoff-{index}") for index in range(2)]
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=2
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        provider = FakeProvider({
            candidate_id: {
                "sourceState": "success",
                "pairAddress": f"pool-{candidate_id}",
                "tokenSide": "base",
                "buys": 1,
                "sells": 1,
            }
            for candidate_id in ids
        })
        production.process_partition(self.connection, partition["partition_id"], provider=provider)
        self.connection.execute(
            "UPDATE candidate_first_gate_queue SET state='completed',completed_at=?",
            (production.utc_now(),),
        )
        self.connection.execute(
            "UPDATE candidates SET t0_status='not_verified' WHERE candidate_id IN (?,?)",
            tuple(ids),
        )
        self.connection.commit()

        result = c2_1_pipeline.evaluate_all(
            self.connection,
            pending_qualification_only=True,
            limit=1,
        )

        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM evaluations WHERE is_current=1").fetchone()[0],
            1,
        )
        hard_gate = json.loads(
            self.connection.execute(
                "SELECT hard_gate_json FROM evaluations WHERE is_current=1"
            ).fetchone()[0]
        )
        t0_check = next(row for row in hard_gate["checks"] if row["code"] == "t0_verified")
        self.assertEqual(t0_check["status"], "pass")

    def test_interleaves_queues_and_rotates_historical_chains(self):
        for index, network in enumerate(production.NETWORKS):
            add_candidate(self.connection, network=network, address=f"token-{index}")
        add_candidate(self.connection, address="0xdaily", source_run_id="daily-run", local_stage="incremental_discovered")
        self.connection.commit()
        production.prepare_partitions(self.connection, queue="daily_incremental", partition_size=1)
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        first = production.claim_next_partition(self.connection)
        self.assertEqual(first["queue_name"], "daily_incremental")
        self.connection.execute("UPDATE candidate_scan_partitions SET state='completed' WHERE partition_id=?", (first["partition_id"],))
        selected = []
        for _ in production.NETWORKS:
            row = production.claim_next_partition(self.connection, "historical_backlog")
            selected.append(row["network_id"])
            self.connection.execute("UPDATE candidate_scan_partitions SET state='completed' WHERE partition_id=?", (row["partition_id"],))
            self.connection.commit()
        self.assertEqual(selected, list(production.NETWORKS))

    def test_resume_does_not_repeat_completed_members_and_batch_is_atomic(self):
        first_id = add_candidate(self.connection, address="0xone", relationship="D")
        second_id = add_candidate(
            self.connection, address="0xtwo", relationship="C", project="project-2",
            asset="asset-2", identity="verified"
        )
        self.connection.execute(
            """INSERT INTO product_evidence(evidence_id,candidate_id,evidence_type,status,identity_status,
            source_name,observed_at) VALUES('product-2',?,'official_repo','qualifying','verified','GitHub',?)""",
            (second_id, production.utc_now()),
        )
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=2
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        provider = FakeProvider({
            first_id: {"sourceState": "success", "pairAddress": "pool-1", "tokenSide": "base", "buys": 1, "sells": 1},
            second_id: {"sourceState": "success", "pairAddress": "pool-2", "tokenSide": "quote", "buys": 3, "sells": 2},
        })
        paused = production.process_partition(
            self.connection, partition["partition_id"], provider=provider, market_batch_size=1, stop_after=3
        )
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(provider.calls, [("ethereum-mainnet", [first_id])])
        paused_status = production.funnel_status(self.connection)
        self.assertEqual(paused_status["trackingEligibleCount"], 1)
        self.assertEqual(paused_status["firstGatePendingCount"], 0)
        self.assertEqual(paused_status["firstGateDeferredCount"], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM candidate_qualification_batches").fetchone()[0], 0)
        resumed = production.process_partition(
            self.connection, partition["partition_id"], provider=provider, market_batch_size=1
        )
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(provider.calls, [("ethereum-mainnet", [first_id]), ("ethereum-mainnet", [second_id])])
        first = self.connection.execute(
            "SELECT tracking_eligible,front_eligible FROM candidate_production_records WHERE candidate_id=?", (first_id,)
        ).fetchone()
        second = self.connection.execute(
            "SELECT tracking_eligible,front_eligible,front_contract_ready FROM candidate_production_records WHERE candidate_id=?", (second_id,)
        ).fetchone()
        self.assertEqual(tuple(first), (1, 0))
        self.assertEqual(tuple(second), (1, 0, 1))
        batch = self.connection.execute("SELECT state,candidate_count FROM candidate_qualification_batches").fetchone()
        self.assertEqual(tuple(batch), ("completed", 2))
        self.assertEqual(production.funnel_status(self.connection)["trackingEligibleCount"], 2)
        status = production.funnel_status(self.connection)
        self.assertEqual(status["firstGateQueuedCount"], 2)
        self.assertEqual(status["firstGatePendingCount"], 2)
        self.assertEqual(c2_1_pipeline.evaluate_all(self.connection)["evaluated"], 0)

    def test_source_outcomes_remain_distinct(self):
        ids = [add_candidate(self.connection, address=f"0x{index}") for index in range(6)]
        states = ["no_data", "quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure"]
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=10
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        provider = FakeProvider({candidate_id: {"sourceState": state} for candidate_id, state in zip(ids, states)})
        production.process_partition(self.connection, partition["partition_id"], provider=provider)
        actual = dict(self.connection.execute(
            "SELECT market_source_state,COUNT(*) FROM candidate_production_records GROUP BY market_source_state"
        ))
        self.assertEqual(actual, {state: 1 for state in states})

    def test_missing_trade_counts_remain_no_data_instead_of_zero_activity(self):
        candidate_id = add_candidate(self.connection, address="0xmissing-trades")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        partition = production.claim_next_partition(self.connection, "historical_backlog")
        provider = FakeProvider({candidate_id: {
            "sourceState": "success", "pairAddress": "pool-missing", "tokenSide": "base",
            "buys": None, "sells": None,
        }})
        production.process_partition(self.connection, partition["partition_id"], provider=provider)
        row = self.connection.execute(
            """SELECT market_state,market_source_state,observed_buys,observed_sells,tracking_eligible
            FROM candidate_production_records WHERE candidate_id=?""",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(
            tuple(row),
            ("source_pending", "no_data", None, None, 0),
        )

    def test_formal_history_launcher_is_hard_blocked_without_authorization(self):
        with (
            patch.object(production_runtime, "load_config", return_value={
                "formalHistoricalScanAuthorized": False, "paused": False,
            }),
            patch.object(production_runtime.subprocess, "Popen") as popen,
        ):
            result = production_runtime.launch_hidden("historical_backlog")
        self.assertEqual(result["status"], "not_authorized")
        popen.assert_not_called()

    def test_scheduler_resumes_authorized_unfinished_history_only(self):
        authorized = {"formalHistoricalScanAuthorized": True, "paused": False}
        add_candidate(self.connection, address="0xresume")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        with (
            patch.object(production_runtime, "load_config", return_value=authorized),
            patch.object(production_runtime, "worker_pid", return_value=None),
            patch.object(production_runtime, "load_json", return_value={"requested": False}),
            patch.object(
                production_runtime,
                "launch_hidden",
                return_value={"status": "launched", "pid": 123},
            ) as launch,
        ):
            result = production_runtime.resume_authorized_history(self.db_path)
        self.assertEqual(result["status"], "launched")
        launch.assert_called_once_with("historical_backlog")

        self.connection.execute("UPDATE candidate_scan_partitions SET state='completed'")
        self.connection.commit()
        with (
            patch.object(production_runtime, "load_config", return_value=authorized),
            patch.object(production_runtime, "worker_pid", return_value=None),
            patch.object(production_runtime, "load_json", return_value={"requested": False}),
            patch.object(production_runtime, "launch_hidden") as launch,
        ):
            result = production_runtime.resume_authorized_history(self.db_path)
        self.assertEqual(result["status"], "completed")
        launch.assert_not_called()

    def test_screening_waits_for_history_writer_then_resumes_it(self):
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        self.connection.execute(
            "UPDATE candidate_scan_partitions SET state='running',updated_at=? WHERE queue_name='historical_backlog'",
            (production.utc_now(),),
        )
        self.connection.commit()
        with (
            patch.object(production_runtime, "DEFAULT_DB_PATH", self.db_path),
            patch.object(production_runtime, "worker_pid", side_effect=[321, 321, None]),
            patch.object(production_runtime, "request_pause") as pause,
            patch.object(production_runtime.time, "sleep"),
        ):
            handoff = production_runtime.pause_for_screening(timeout_seconds=2, poll_seconds=0)
        self.assertEqual(handoff["status"], "paused")
        self.assertTrue(handoff["resumeAfter"])
        pause.assert_called_once_with(True)
        with patch.object(
            production_runtime, "launch_hidden", return_value={"status": "launched", "pid": 654}
        ) as launch:
            resumed = production_runtime.resume_after_screening(handoff)
        self.assertEqual(resumed["status"], "launched")
        launch.assert_called_once_with("historical_backlog", authorized_resume=True)

    def test_cached_status_only_reads_small_runtime_tables(self):
        add_candidate(self.connection, address="0xcache")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        cache_path = Path(self.temp.name) / "status.json"
        cache_path.write_text(json.dumps({
            "schemaVersion": "c2.2-candidate-production-status-v1",
            "state": "ready",
            "importedCandidateCount": 123,
            "localScannedCount": 45,
            "firstGateDeferredCount": 0,
            "firstGateOutsideWindowCount": 0,
            "partitions": [],
        }), encoding="utf-8")
        with (
            patch.object(production_runtime, "load_config", return_value={"formalHistoricalScanAuthorized": True, "paused": False}),
            patch.object(production_runtime, "worker_pid", return_value=None),
            patch.object(production_runtime, "funnel_status", side_effect=AssertionError("不应重新全表统计")),
            patch.object(production_runtime, "load_json", side_effect=lambda path, fallback: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path) == cache_path else {}),
        ):
            payload = production_runtime.status_payload(self.db_path, cache_path)
        self.assertEqual(payload["importedCandidateCount"], 123)
        self.assertEqual(payload["localScannedCount"], 45)
        self.assertTrue(payload["formalHistoricalScanStarted"])

    def test_legacy_cached_status_is_rebuilt_once_with_current_queue_fields(self):
        cache_path = Path(self.temp.name) / "status.json"
        cache_path.write_text(json.dumps({
            "schemaVersion": "c2.2-candidate-production-status-v1",
            "state": "ready",
            "firstGatePendingCount": 525,
        }), encoding="utf-8")
        rebuilt = {
            "schemaVersion": "c2.2-candidate-production-status-v1",
            "state": "ready",
            "firstGatePendingCount": 0,
            "firstGateDeferredCount": 525,
            "firstGateOutsideWindowCount": 525,
            "partitions": [],
        }
        with (
            patch.object(production_runtime, "load_config", return_value={"formalHistoricalScanAuthorized": True, "paused": False}),
            patch.object(production_runtime, "worker_pid", return_value=None),
            patch.object(production_runtime, "funnel_status", return_value=rebuilt) as rebuild_status,
            patch.object(production_runtime, "load_json", side_effect=lambda path, fallback: json.loads(Path(path).read_text(encoding="utf-8")) if Path(path) == cache_path else {}),
        ):
            payload = production_runtime.status_payload(self.db_path, cache_path)
        rebuild_status.assert_called_once()
        self.assertEqual(payload["firstGatePendingCount"], 0)
        self.assertEqual(payload["firstGateOutsideWindowCount"], 525)
        self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8"))["firstGateOutsideWindowCount"], 525)

    def test_stopped_running_partition_recovers_from_saved_members(self):
        candidate_id = add_candidate(self.connection, address="0xrecover")
        self.connection.commit()
        production.prepare_partitions(
            self.connection, queue="historical_backlog", historical_authorized=True, partition_size=1
        )
        claimed = production.claim_next_partition(self.connection, "historical_backlog")
        self.assertEqual(claimed["state"], "running")
        provider = FakeProvider({candidate_id: {
            "sourceState": "success", "pairAddress": "recover-pool", "tokenSide": "base", "buys": 1, "sells": 1,
        }})
        result = production.run_worker(
            db_path=self.db_path, historical_authorized=True, max_partitions=1,
            provider=provider, lock_path=Path(self.temp.name) / "worker.lock",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(provider.calls, [("ethereum-mainnet", [candidate_id])])
        state = self.connection.execute(
            "SELECT state FROM candidate_scan_partitions WHERE partition_id=?", (claimed["partition_id"],)
        ).fetchone()[0]
        self.assertEqual(state, "completed")

    def test_partition_program_failure_keeps_exact_retry_range(self):
        candidate_id = add_candidate(
            self.connection, address="0xfail", source_run_id="daily-run", local_stage="incremental_discovered"
        )
        self.connection.commit()
        provider = FakeProvider({candidate_id: {
            "sourceState": "success", "pairAddress": "bad-pool", "tokenSide": "base", "buys": "bad", "sells": 1,
        }})
        result = production.run_worker(
            db_path=self.db_path, queue_only="daily_incremental", max_partitions=1,
            provider=provider, lock_path=Path(self.temp.name) / "worker.lock",
        )
        self.assertEqual(result["status"], "failed")
        failed = self.connection.execute(
            "SELECT state,source_state,next_retry_at,error_detail FROM candidate_scan_partitions WHERE partition_id=?",
            (result["partitionId"],),
        ).fetchone()
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["source_state"], "program_failure")
        self.assertTrue(failed["next_retry_at"])
        self.assertIn("ValueError", failed["error_detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
