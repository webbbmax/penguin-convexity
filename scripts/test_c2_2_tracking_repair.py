#!/usr/bin/env python3
"""Core regressions for the C2.2 candidate-to-tracking repair."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import candidate_production
import c2_2_candidate_tracking as candidate_tracking
import c2_1_pipeline
from c2_1_db import initialize_database, open_pipeline_db
from c2_1_pipeline import evaluation_is_current_for_production


class C22TrackingRepairTests(unittest.TestCase):
    def test_json_client_can_bound_automatic_retry_time(self):
        sleeps = []
        client = candidate_tracking.JsonClient(
            timeout=8,
            sleep=sleeps.append,
            retry_delays=(0, 2),
        )
        with patch(
            "c2_1_enrichment.urllib.request.urlopen",
            side_effect=TimeoutError("upstream timeout"),
        ):
            state, _payload, _status, attempts = client.request(
                "project_website",
                "https://example.invalid/project",
            )
        self.assertEqual(state, "source_failure")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [2])

    def test_independent_path4_failure_does_not_block_baseline_completion(self):
        states = {
            "market": "success",
            "quote": "success",
            "risk": "no_data",
            "supply": "no_data",
            "path4": "program_failure",
            "product_usage": "no_data",
            "project_evidence": "success",
            "evaluation": "success",
        }
        self.assertTrue(candidate_tracking.baseline_states_complete(states))
        states["quote"] = "source_failure"
        self.assertFalse(candidate_tracking.baseline_states_complete(states))

    def test_cohort_context_reuses_one_chain_age_calculation(self):
        rules, _ = c2_1_pipeline.load_rules()
        rows = [
            {
                "network_id": "base-mainnet",
                "effective_t0": "2026-08-01T00:00:00Z",
                "liquidity_usd": 1000 + index,
                "volume_usd": 100 + index,
                "transaction_count": 10 + index,
                "volume_liquidity_ratio": 0.1,
            }
            for index in range(1000)
        ]
        catalog = {"rows": rows, "expansion": []}
        candidate = {"network_id": "base-mainnet", "effective_t0": "2026-08-01T00:00:00Z"}
        with patch.object(c2_1_pipeline, "age_days", wraps=c2_1_pipeline.age_days) as age:
            for _ in range(100):
                c2_1_pipeline.cohort_context(
                    None, candidate, "2026-08-12T00:00:00Z", rules, catalog
                )
        self.assertLess(age.call_count, 1200)

    def test_t0_or_production_change_invalidates_an_older_rule_evaluation(self):
        self.assertFalse(
            evaluation_is_current_for_production(
                evaluated_at="2026-08-12T04:49:00Z",
                evaluation_rule_hash="rules-v1",
                qualified_at="2026-08-12T03:00:00Z",
                production_updated_at="2026-08-12T06:21:00Z",
                candidate_updated_at="2026-08-12T06:30:00Z",
                current_rule_hash="rules-v1",
            )
        )

    def test_rule_change_invalidates_an_evaluation_even_when_times_are_current(self):
        self.assertFalse(
            evaluation_is_current_for_production(
                evaluated_at="2026-08-12T07:00:00Z",
                evaluation_rule_hash="rules-v0",
                qualified_at="2026-08-12T03:00:00Z",
                production_updated_at="2026-08-12T06:21:00Z",
                candidate_updated_at="2026-08-12T06:30:00Z",
                current_rule_hash="rules-v1",
            )
        )

    def test_evaluation_is_current_only_after_all_inputs_and_rule_hash_match(self):
        self.assertTrue(
            evaluation_is_current_for_production(
                evaluated_at="2026-08-12T07:00:00Z",
                evaluation_rule_hash="rules-v1",
                qualified_at="2026-08-12T03:00:00Z",
                production_updated_at="2026-08-12T06:21:00Z",
                candidate_updated_at="2026-08-12T06:30:00Z",
                current_rule_hash="rules-v1",
            )
        )

    def test_tracking_batch_is_checkpointed_and_not_repeated_after_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "pipeline.db"
            initialize_database(database)
            candidate_production.migrate_database(database)
            connection = open_pipeline_db(database)
            now = "2026-08-12T08:00:00Z"
            candidate_id = connection.execute(
                """INSERT INTO candidates(
                  network_id,token_address,token_address_normalized,gate0_t0,effective_t0,t0_status,
                  t0_evidence_type,source_run_id,first_seen_at,continuity_status,relationship_class,
                  mapped_project_id,mapped_asset_id,identity_status,local_stage,created_at,updated_at
                ) VALUES('base-mainnet','0xtest','0xtest','2026-08-01T00:00:00Z','2026-08-01T00:00:00Z',
                  'verified_in_supported_scope','factory_event','test-run','2026-08-01T00:00:00Z',
                  'candidate_asset','A','project-1','asset-1','verified','market_mapped',?,?)""",
                (now, now),
            ).lastrowid
            connection.execute(
                """INSERT INTO candidate_scan_partitions(
                  partition_id,queue_name,network_id,input_hash,state,total_count,created_at,updated_at
                ) VALUES('part-1','historical_backlog','base-mainnet','hash','completed',1,?,?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO candidate_scan_partition_members(partition_id,sequence_no,candidate_id,state)
                VALUES('part-1',1,?,'completed')""",
                (candidate_id,),
            )
            connection.execute(
                """INSERT INTO candidate_qualification_batches(
                  qualification_batch_id,partition_id,state,candidate_count,created_at,completed_at,input_hash
                ) VALUES('batch-1','part-1','completed',1,?,?, 'hash')""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO candidate_qualification_members(
                  qualification_batch_id,candidate_id,asset_id,relationship_class,front_eligible
                ) VALUES('batch-1',?,'asset-1','A',1)""",
                (candidate_id,),
            )
            connection.execute(
                """INSERT INTO candidate_first_gate_queue(
                  candidate_id,qualification_batch_id,source_queue,state,attempt_count,
                  enqueued_at,completed_at,updated_at
                ) VALUES(?,'batch-1','historical_backlog','completed',1,?,?,?)""",
                (candidate_id, now, now, now),
            )
            connection.execute(
                """INSERT INTO candidate_production_records(
                  candidate_id,asset_id,project_id,local_state,local_reason_code,local_plain_reason,
                  local_checked_at,rule_version,market_state,market_source_state,pair_address,token_side,
                  observed_buys,observed_sells,tracking_eligible,tracking_reason_code,t0_status,effective_t0,age_days,
                  identity_state,product_evidence_state,risk_data_state,relationship_class,
                  identity_consistent,qualifying_product_evidence,confirmed_hard_block,front_contract_ready,front_eligible,
                  qualification_batch_id,qualified_at,updated_at
                ) VALUES(?,'asset-1','project-1','local_pass','pass','pass',?,'rules','market_confirmed','success',
                  '0xpair','base',1,1,1,'eligible','verified_in_supported_scope','2026-08-01T00:00:00Z',11,
                  'verified','qualifying','no_data','A',1,1,0,1,1,'batch-1',?,?)""",
                (candidate_id, now, now, now),
            )
            connection.commit()
            connection.close()

            complete_states = {
                "market": "no_data",
                "quote": "no_data",
                "risk": "success",
                "supply": "no_data",
                "path4": "no_data",
                "product_usage": "no_data",
                "project_evidence": "success",
                "evaluation": "success",
            }
            def evaluate_and_advance_input(connection, **_kwargs):
                connection.execute(
                    "UPDATE candidate_production_records SET updated_at='2026-08-12T08:01:00Z' WHERE candidate_id=?",
                    (candidate_id,),
                )
                return {"evaluated": 1}

            with (
                patch.object(candidate_tracking, "_run_stage", return_value={"status": "ok"}) as stage,
                patch.object(candidate_tracking, "_candidate_source_states", return_value=complete_states),
                patch.object(candidate_tracking, "evaluate_all", side_effect=evaluate_and_advance_input),
            ):
                first = candidate_tracking.run_candidate_tracking_batch(db_path=database, limit=25)
                second = candidate_tracking.run_candidate_tracking_batch(db_path=database, limit=25)
                source_retry = candidate_tracking.run_candidate_tracking_batch(
                    db_path=database,
                    limit=25,
                    only_source_id="goplus",
                )

            self.assertEqual(first["selected"], 1)
            self.assertEqual(first["completed"], 1)
            self.assertEqual(second["selected"], 0)
            self.assertEqual(source_retry["selected"], 0)
            self.assertEqual(stage.call_count, 3)
            records = candidate_tracking.load_tracking_records(database)
            self.assertEqual(records[candidate_id]["state"], "completed")


if __name__ == "__main__":
    unittest.main()
