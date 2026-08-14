#!/usr/bin/env python3
"""Tier 0 database tests for the C2.4 handoff and lifecycle rules."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))

import candidate_production  # noqa: E402
import build_c2_4_snapshots  # noqa: E402
import run_c2_2_update  # noqa: E402
from build_c2_4_snapshots import build_snapshots as build_c24_snapshots  # noqa: E402
from c2_1_db import initialize_database, open_pipeline_db  # noqa: E402
from c2_2_candidate_tracking import (  # noqa: E402
    _baseline_prerequisite_ids,
    _select_candidates,
    _select_deep_structure_candidates,
    initialize_tracking_schema,
)
from c2_4_tracking import (  # noqa: E402
    migrate_qualified_day91,
    record_completed_public_history,
    record_first_gate_history,
    reconcile_existing_tracking_history,
)


NOW = "2026-08-13T00:00:00Z"


class C24TrackingDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "pipeline.db"
        initialize_database(self.database)
        candidate_production.migrate_database(self.database)
        self.connection = open_pipeline_db(self.database)
        initialize_tracking_schema(self.connection)
        self.connection.execute(
            """INSERT INTO candidate_scan_partitions(
              partition_id,queue_name,network_id,input_hash,state,total_count,created_at,updated_at
            ) VALUES('part','historical_backlog','base-mainnet','hash','completed',20,?,?)""",
            (NOW, NOW),
        )
        self.connection.execute(
            """INSERT INTO candidate_qualification_batches(
              qualification_batch_id,partition_id,state,candidate_count,created_at,completed_at,input_hash
            ) VALUES('batch','part','completed',20,?,?,'hash')""",
            (NOW, NOW),
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def add_candidate(self, suffix: str, relationship: str, age: int = 10, *, front_eligible: int = 0) -> int:
        token = f"0x{suffix}"
        asset_id = f"asset-{suffix}"
        candidate_id = self.connection.execute(
            """INSERT INTO candidates(
              network_id,token_address,token_address_normalized,gate0_pool_id,gate0_t0,effective_t0,t0_status,
              t0_evidence_type,source_run_id,first_seen_at,continuity_status,relationship_class,
              mapped_project_id,mapped_asset_id,identity_status,local_stage,created_at,updated_at
            ) VALUES('base-mainnet',?,?,?,? ,?,'verified_in_supported_scope','factory_event','run',?,
              'candidate_asset',?,?,?,'verified','market_mapped',?,?)""",
            (token, token, f"pool-{suffix}", "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z", relationship, f"project-{suffix}", asset_id, NOW, NOW),
        ).lastrowid
        self.connection.execute(
            "INSERT INTO candidate_scan_partition_members(partition_id,sequence_no,candidate_id,state) VALUES('part',?,?,'completed')",
            (candidate_id, candidate_id),
        )
        self.connection.execute(
            """INSERT INTO candidate_qualification_members(
              qualification_batch_id,candidate_id,asset_id,relationship_class,front_eligible
            ) VALUES('batch',?,?,?,?)""",
            (candidate_id, asset_id, relationship, front_eligible),
        )
        self.connection.execute(
            """INSERT INTO candidate_first_gate_queue(
              candidate_id,qualification_batch_id,source_queue,state,attempt_count,enqueued_at,completed_at,updated_at
            ) VALUES(?,'batch','historical_backlog','completed',1,?,?,?)""",
            (candidate_id, NOW, NOW, NOW),
        )
        self.connection.execute(
            """INSERT INTO candidate_production_records(
              candidate_id,asset_id,project_id,local_state,local_reason_code,local_plain_reason,
              local_checked_at,rule_version,market_state,market_source_state,pair_address,token_side,
              observed_buys,observed_sells,tracking_eligible,tracking_reason_code,t0_status,effective_t0,age_days,
              identity_state,product_evidence_state,risk_data_state,relationship_class,identity_consistent,
              qualifying_product_evidence,confirmed_hard_block,front_contract_ready,front_eligible,
              qualification_batch_id,qualified_at,updated_at
            ) VALUES(?,?,?,'local_pass','pass','pass',?,'c24','market_confirmed','success',?,'base',
              1,1,1,'eligible','verified_in_supported_scope','2026-08-01T00:00:00Z',?,
              'verified','no_data','no_data',?,1,0,0,0,?,'batch',?,?)""",
            (candidate_id, asset_id, f"project-{suffix}", NOW, f"pool-{suffix}", age, relationship, front_eligible, NOW, NOW),
        )
        self.connection.commit()
        return int(candidate_id)

    def test_a_b_c_and_d_all_enter_the_second_gate_without_front_flags(self):
        expected = {self.add_candidate(letter.lower(), letter) for letter in "ABCD"}
        selected = {int(row["candidate_id"]) for row in _select_candidates(self.connection, 20)}
        self.assertEqual(selected, expected)

    def test_source_retry_only_selects_a_real_recoverable_failure(self):
        failed = self.add_candidate("failed", "A")
        healthy = self.add_candidate("healthy", "A")
        for candidate_id, state in ((failed, "source_failure"), (healthy, "success")):
            self.connection.execute(
                """INSERT INTO candidate_tracking_records(
                  candidate_id,qualification_batch_id,input_updated_at,state,attempt_count,
                  source_states_json,last_attempt_at,updated_at
                ) VALUES(?,'batch',?,'partial',1,?,?,?)""",
                (candidate_id, NOW, json.dumps({"risk": state, "supply": state}), NOW, NOW),
            )
        self.connection.commit()
        selected = _select_candidates(self.connection, 20, retry_source_id="goplus")
        self.assertEqual([int(row["candidate_id"]) for row in selected], [failed])

    def test_day_91_migrates_only_an_asset_with_complete_new_period_history(self):
        qualified = self.add_candidate("qualified91", "A", 91)
        ordinary_old = self.add_candidate("ordinary91", "A", 91)
        self.connection.execute(
            "INSERT INTO c2_4_first_gate_history VALUES(?,?,?,?,?,?)",
            (qualified, "asset-qualified91", NOW, 90, "[]", "c2.4-first-gate-v1"),
        )
        self.connection.execute(
            """INSERT INTO c2_4_public_history(
              candidate_id,asset_id,first_public_at,last_public_at,last_public_age_days,
              last_public_state,last_evaluation_window_id
            ) VALUES(?,?,?,?,?,?,?)""",
            (qualified, "asset-qualified91", NOW, NOW, 90, "observing", "window-90"),
        )
        self.connection.execute(
            "INSERT INTO c2_4_lifecycle_state(candidate_id,asset_id,lifecycle_pool,updated_at) VALUES(?,?,'new_0_90',?)",
            (qualified, "asset-qualified91", NOW),
        )
        self.connection.commit()
        self.assertEqual(migrate_qualified_day91(self.connection), 1)
        selected = {int(row["candidate_id"]) for row in _select_candidates(self.connection, 20)}
        self.assertIn(qualified, selected)
        self.assertNotIn(ordinary_old, selected)

    def test_day_91_cannot_become_public_for_the_first_time(self):
        candidate_id = self.add_candidate("latepublic", "A", 91)
        self.connection.execute(
            "INSERT INTO c2_4_first_gate_history VALUES(?,?,?,?,?,?)",
            (candidate_id, "asset-latepublic", NOW, 90, "[]", "c2.4-first-gate-v1"),
        )
        self.connection.execute(
            "INSERT INTO c2_4_lifecycle_state(candidate_id,asset_id,lifecycle_pool,updated_at) VALUES(?,?,'new_0_90',?)",
            (candidate_id, "asset-latepublic", NOW),
        )
        self.connection.commit()
        self.set_completed_window(candidate_id, "window-late", 1, 10)

        result = record_completed_public_history(self.connection, [candidate_id])

        self.assertEqual(result["public"], 0)
        self.assertIsNone(self.connection.execute(
            "SELECT 1 FROM c2_4_public_history WHERE candidate_id=?", (candidate_id,)
        ).fetchone())

    def test_invalid_late_public_history_is_deactivated_in_reconciliation(self):
        candidate_id = self.add_candidate("latehistory", "A", 91)
        self.connection.execute(
            "INSERT INTO c2_4_first_gate_history VALUES(?,?,?,?,?,?)",
            (candidate_id, "asset-latehistory", NOW, 90, "[]", "c2.4-first-gate-v1"),
        )
        self.connection.execute(
            "INSERT INTO c2_4_lifecycle_state(candidate_id,asset_id,lifecycle_pool,updated_at) VALUES(?,?,'new_0_90',?)",
            (candidate_id, "asset-latehistory", NOW),
        )
        self.connection.execute(
            """INSERT INTO c2_4_public_history(
              candidate_id,asset_id,first_public_at,last_public_at,last_public_age_days,
              last_public_state,last_evaluation_window_id,public_active
            ) VALUES(?,?,?,?,?,?,?,1)""",
            (candidate_id, "asset-latehistory", NOW, NOW, 91, "observing", "window-late"),
        )
        self.connection.commit()

        self.assertEqual(migrate_qualified_day91(self.connection), 0)
        history = self.connection.execute(
            "SELECT public_active,last_public_exit_reason FROM c2_4_public_history WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(tuple(history), (0, "not_public_while_new_at_day91"))

    def test_backlog_and_late_history_repairs_restore_snapshot_reconciliation(self):
        pending_id = self.add_candidate("pendinghistory", "A", 10)
        record_first_gate_history(self.connection, [pending_id])
        self.connection.execute(
            "UPDATE candidate_first_gate_queue SET state='pending',completed_at=NULL WHERE candidate_id=?",
            (pending_id,),
        )
        late_id = self.add_candidate("lateinvalid", "A", 91)
        self.connection.execute(
            "INSERT INTO c2_4_first_gate_history VALUES(?,?,?,?,?,?)",
            (late_id, "asset-lateinvalid", NOW, 90, "[]", "c2.4-first-gate-v1"),
        )
        self.connection.execute(
            "INSERT INTO c2_4_lifecycle_state(candidate_id,asset_id,lifecycle_pool,updated_at) VALUES(?,?,'new_0_90',?)",
            (late_id, "asset-lateinvalid", NOW),
        )
        self.connection.execute(
            """INSERT INTO c2_4_public_history(
              candidate_id,asset_id,first_public_at,last_public_at,last_public_age_days,
              last_public_state,last_evaluation_window_id,public_active
            ) VALUES(?,?,?,?,?,?,?,1)""",
            (late_id, "asset-lateinvalid", NOW, NOW, 91, "observing", "window-late"),
        )
        self.connection.commit()

        with patch("c2_1_db.open_pipeline_db", return_value=open_pipeline_db(self.database)):
            drained = run_c2_2_update.drain_first_gate_backlog()
        self.assertEqual(drained["processed"], 1)
        self.assertEqual(migrate_qualified_day91(self.connection), 0)

        test_root = Path(self.temp.name) / "project"
        (test_root / "data").mkdir(parents=True)
        (test_root / "docs").mkdir()
        (test_root / "data" / "convexity.db").write_bytes(b"")
        (test_root / "docs" / "C2.4_INHERITANCE_MANIFEST.json").write_text(
            (SCRIPT_ROOT.parent / "docs" / "C2.4_INHERITANCE_MANIFEST.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        with patch.object(build_c2_4_snapshots, "PROJECT_ROOT", test_root):
            payloads = build_c24_snapshots(
                db_path=self.database,
                output_dir=Path(self.temp.name) / "app",
                write=False,
            )
        self.assertEqual(payloads["admin"]["reconciliation"]["differences"], {
            "publicNotTracked": [],
            "newTrackedNotQueued": [],
            "trackedNotFirstGateHistory": [],
            "continuedMissingHistory": [],
        })

    def test_expensive_structure_layer_requires_public_baseline_prerequisites(self):
        eligible = self.add_candidate("eligible", "A")
        class_d = self.add_candidate("classd", "D")
        for candidate_id in (eligible, class_d):
            self.connection.execute(
                """INSERT INTO risk_observations(
                  observation_id,candidate_id,source_name,source_status,observed_at,hard_trade_block,
                  severe_anomaly,reason_codes_json,payload_json
                ) VALUES(?,?,'GoPlus','success',?,0,0,'[]','{}')""",
                (f"risk-{candidate_id}", candidate_id, NOW),
            )
            self.connection.execute(
                """INSERT INTO product_evidence(
                  evidence_id,candidate_id,evidence_type,status,identity_status,source_name,
                  source_url,observed_at,payload_json,boundary_note
                ) VALUES(?,?,'github','qualifying','verified','GitHub','https://github.com/example/repo',?,'{}','code only')""",
                (f"evidence-{candidate_id}", candidate_id, NOW),
            )
            self.connection.execute(
                """INSERT INTO market_observations(
                  observation_id,candidate_id,window_id,source_name,source_status,observed_at,pair_address,
                  token_side,standard_sell_notional_usd,standard_sell_quote_state,
                  standard_sell_quote_loss_pct,payload_json
                ) VALUES(?,?,'window','DexScreener','success',?,?,'base',100,'success',10,'{}')""",
                (f"market-{candidate_id}", candidate_id, NOW, f"pool-{candidate_id}"),
            )
        self.connection.commit()
        self.assertEqual(_baseline_prerequisite_ids(self.connection, [eligible, class_d], require_quote=True), [eligible])

    def test_quote_collection_is_not_blocked_by_a_missing_risk_result(self):
        candidate_id = self.add_candidate("quotefirst", "A")
        self.connection.execute(
            """INSERT INTO product_evidence(
              evidence_id,candidate_id,evidence_type,status,identity_status,source_name,
              source_url,observed_at,payload_json,boundary_note
            ) VALUES('evidence-quote',?,'github','qualifying','verified','GitHub',
              'https://github.com/example/repo',?,'{}','code only')""",
            (candidate_id, NOW),
        )
        self.connection.commit()
        self.assertEqual(
            _baseline_prerequisite_ids(self.connection, [candidate_id], require_quote=False),
            [candidate_id],
        )
        self.assertEqual(
            _baseline_prerequisite_ids(self.connection, [candidate_id], require_quote=True),
            [],
        )

        self.connection.execute(
            """INSERT INTO market_observations(
              observation_id,candidate_id,window_id,source_name,source_status,observed_at,pair_address,
              token_side,standard_sell_notional_usd,standard_sell_quote_state,
              standard_sell_quote_loss_pct,payload_json
            ) VALUES('market-quote',?,'window','quote-test','success',?,'pool','base',
              100,'success',99,'{}')""",
            (candidate_id, NOW),
        )
        self.connection.commit()
        self.assertEqual(
            _baseline_prerequisite_ids(self.connection, [candidate_id], require_quote=True),
            [candidate_id],
        )

    def test_post_baseline_structure_queue_requires_evidence_and_quote_not_risk_response(self):
        eligible = self.add_candidate("deepeligible", "A")
        no_evidence = self.add_candidate("deepgeneric", "D")
        self.connection.execute(
            """INSERT INTO product_evidence(
              evidence_id,candidate_id,evidence_type,status,identity_status,source_name,
              source_url,observed_at,payload_json,boundary_note
            ) VALUES('deep-evidence',?,'github','qualifying','verified','GitHub',
              'https://github.com/example/deep',?,'{}','code only')""",
            (eligible, NOW),
        )
        for candidate_id in (eligible, no_evidence):
            self.connection.execute(
                """INSERT INTO candidate_tracking_records(
                  candidate_id,qualification_batch_id,input_updated_at,state,attempt_count,
                  source_states_json,evaluated_at,last_attempt_at,completed_at,updated_at
                ) VALUES(?,'batch',?,'completed',1,'{}',?,?,?,?)""",
                (candidate_id, NOW, NOW, NOW, NOW, NOW),
            )
            self.connection.execute(
                """INSERT INTO market_observations(
                  observation_id,candidate_id,window_id,source_name,source_status,observed_at,pair_address,
                  token_side,standard_sell_notional_usd,standard_sell_quote_state,payload_json
                ) VALUES(?,?,'window','quote-test','success',?,'pool','base',100,'success','{}')""",
                (f"deep-market-{candidate_id}", candidate_id, NOW),
            )
        self.connection.commit()
        self.assertEqual(_select_deep_structure_candidates(self.connection, 10), [eligible])

    def test_unseen_candidates_with_real_project_evidence_are_processed_first(self):
        self.add_candidate("genericfirst", "D")
        evidence_candidate = self.add_candidate("evidencefirst", "C")
        self.connection.execute(
            """INSERT INTO product_evidence(
              evidence_id,candidate_id,evidence_type,status,identity_status,source_name,
              source_url,observed_at,payload_json,boundary_note
            ) VALUES('evidence-priority',?,'github','qualifying','verified','GitHub',
              'https://github.com/example/priority',?,'{}','code only')""",
            (evidence_candidate, NOW),
        )
        self.connection.commit()
        selected = _select_candidates(self.connection, 1)
        self.assertEqual(int(selected[0]["candidate_id"]), evidence_candidate)

    def set_completed_window(
        self,
        candidate_id: int,
        window: str,
        sequence: int,
        quote_loss: float,
        *,
        source_states: dict[str, str] | None = None,
        hard_block: bool = False,
        quote_state: str = "success",
    ) -> None:
        observed_at = f"2026-08-13T00:00:{sequence:02d}Z"
        self.connection.execute("UPDATE evaluations SET is_current=0 WHERE candidate_id=?", (candidate_id,))
        self.connection.execute(
            """INSERT INTO evaluations(
              evaluation_id,candidate_id,evaluation_window_id,evaluated_at,rule_version,rule_config_hash,
              cohort_snapshot_id,cohort_scope,cohort_sample_size,age_days,age_band,hard_gate_status,
              hard_gate_json,display_state,display_reason,paths_json,factor_directions_json,confidence_json,
              threshold_context_json,market_snapshot_json,source_impact_json,sort_score,sort_reason,is_current
            ) VALUES(?,?,?,?,'test','hash','cohort','fallback',0,10,'age_7_30','pass','{}',
              'observing','test','[]','[]','{}','{}','{}','{}',0,'test',1)""",
            (f"evaluation-{window}", candidate_id, window, observed_at),
        )
        states = source_states or {
            "market": "success", "project_evidence": "success", "risk": "success",
            "supply": "success", "quote": quote_state, "path4": "success", "evaluation": "success",
        }
        self.connection.execute(
            """INSERT INTO candidate_tracking_records(
              candidate_id,qualification_batch_id,input_updated_at,state,attempt_count,source_states_json,
              evaluated_at,last_attempt_at,completed_at,error_detail,updated_at
            ) VALUES(?,'batch',?,'completed',1,?,?,?,?, '',?)
            ON CONFLICT(candidate_id) DO UPDATE SET state='completed',source_states_json=excluded.source_states_json,
              evaluated_at=excluded.evaluated_at,completed_at=excluded.completed_at,updated_at=excluded.updated_at""",
            (candidate_id, observed_at, json.dumps(states), observed_at, observed_at, observed_at, observed_at),
        )
        self.connection.execute(
            """INSERT INTO market_observations(
              observation_id,candidate_id,window_id,source_name,source_status,observed_at,pair_address,
              token_side,observed_buys,observed_sells,standard_sell_notional_usd,
              standard_sell_quote_state,standard_sell_quote_loss_pct,payload_json
            ) VALUES(?, ?, ?, 'DexScreener','success',?,?,'base',1,1,100,?,?,'{}')""",
            (f"market-{window}", candidate_id, window, observed_at, f"pool-{candidate_id}", quote_state, quote_loss),
        )
        self.connection.execute(
            """INSERT INTO risk_observations(
              observation_id,candidate_id,source_name,source_status,observed_at,hard_trade_block,
              severe_anomaly,reason_codes_json,payload_json
            ) VALUES(?,?,'GoPlus','success',?,?,0,'[]','{}')""",
            (f"risk-{window}", candidate_id, observed_at, int(hard_block)),
        )
        self.connection.execute(
            """INSERT OR IGNORE INTO product_evidence(
              evidence_id,candidate_id,evidence_type,status,identity_status,source_name,source_url,observed_at,payload_json
            ) VALUES(?,?,'github','qualifying','verified','GitHub','https://github.com/example/repo',?,'{}')""",
            (f"evidence-{candidate_id}", candidate_id, observed_at),
        )
        self.connection.commit()

    def test_public_exit_uses_two_distinct_complete_windows_and_can_reenter(self):
        candidate_id = self.add_candidate("hysteresis", "A")
        record_first_gate_history(self.connection, [candidate_id])
        self.set_completed_window(candidate_id, "window-1", 1, 10)
        self.assertEqual(record_completed_public_history(self.connection, [candidate_id])["public"], 1)

        self.set_completed_window(candidate_id, "window-2", 2, 17, quote_state="no_data")
        self.assertEqual(record_completed_public_history(self.connection, [candidate_id])["retained"], 1)
        lifecycle = self.connection.execute(
            "SELECT * FROM c2_4_lifecycle_state WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        history = self.connection.execute(
            "SELECT * FROM c2_4_public_history WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        self.assertEqual(lifecycle["consecutive_completed_misses"], 1)
        self.assertEqual(history["public_active"], 1)

        self.assertEqual(record_completed_public_history(self.connection, [candidate_id])["retained"], 1)
        self.assertEqual(self.connection.execute(
            "SELECT consecutive_completed_misses FROM c2_4_lifecycle_state WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0], 1)

        self.set_completed_window(candidate_id, "window-3", 3, 17, quote_state="no_data")
        self.assertEqual(record_completed_public_history(self.connection, [candidate_id])["normalExit"], 1)
        self.assertEqual(self.connection.execute(
            "SELECT public_active FROM c2_4_public_history WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0], 0)

        self.set_completed_window(candidate_id, "window-4", 4, 10)
        self.assertEqual(record_completed_public_history(self.connection, [candidate_id])["public"], 1)
        history = self.connection.execute(
            "SELECT public_active,last_public_exit_reason FROM c2_4_public_history WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        self.assertEqual(tuple(history), (1, ""))

    def test_screening_market_refresh_does_not_replace_the_completed_tracking_window(self):
        candidate_id = self.add_candidate("screeningrefresh", "A")
        record_first_gate_history(self.connection, [candidate_id])
        self.set_completed_window(candidate_id, "window-complete", 1, 10)
        self.connection.execute(
            """INSERT INTO market_observations(
              observation_id,candidate_id,window_id,source_name,source_status,observed_at,pair_address,
              token_side,observed_buys,observed_sells,standard_sell_notional_usd,
              standard_sell_quote_state,standard_sell_quote_loss_pct,payload_json
            ) VALUES('market-screening',?,'screening','DexScreener','success',
              '2026-08-13T00:01:00Z',?,'base',2,2,100,'no_data',NULL,'{}')""",
            (candidate_id, f"pool-{candidate_id}"),
        )
        self.connection.commit()

        result = record_completed_public_history(self.connection, [candidate_id])
        self.assertEqual(result["public"], 1)
        history = self.connection.execute(
            "SELECT public_active FROM c2_4_public_history WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(history[0], 1)

    def test_screening_evaluation_does_not_count_as_a_new_complete_tracking_window(self):
        candidate_id = self.add_candidate("screeningevaluation", "A")
        record_first_gate_history(self.connection, [candidate_id])
        self.set_completed_window(candidate_id, "window-complete", 1, 10)
        record_completed_public_history(self.connection, [candidate_id])
        self.connection.execute(
            "UPDATE evaluations SET is_current=0 WHERE candidate_id=?",
            (candidate_id,),
        )
        self.connection.execute(
            """INSERT INTO evaluations(
              evaluation_id,candidate_id,evaluation_window_id,evaluated_at,rule_version,rule_config_hash,
              cohort_snapshot_id,cohort_scope,cohort_sample_size,age_days,age_band,hard_gate_status,
              hard_gate_json,display_state,display_reason,paths_json,factor_directions_json,confidence_json,
              threshold_context_json,market_snapshot_json,source_impact_json,sort_score,sort_reason,is_current
            ) VALUES('evaluation-screening',?,'screening-window','2026-08-13T00:02:00Z',
              'test','hash','cohort','fallback',0,10,'age_7_30','pass','{}',
              'observing','screening','[]','[]','{}','{}','{}','{}',0,'screening',1)""",
            (candidate_id,),
        )
        self.connection.commit()

        result = reconcile_existing_tracking_history(self.connection)
        self.assertEqual(result["completed"], 0)
        history = self.connection.execute(
            "SELECT last_evaluation_window_id FROM c2_4_public_history WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(history[0], "window-complete")

    def test_unsupported_source_does_not_count_as_a_public_exit_window(self):
        candidate_id = self.add_candidate("unsupported", "A")
        record_first_gate_history(self.connection, [candidate_id])
        self.set_completed_window(candidate_id, "window-1", 1, 10)
        record_completed_public_history(self.connection, [candidate_id])
        self.set_completed_window(
            candidate_id,
            "window-2",
            2,
            17,
            source_states={"market": "success", "risk": "success", "quote": "no_data", "path4": "unsupported"},
            quote_state="no_data",
        )
        result = record_completed_public_history(self.connection, [candidate_id])
        self.assertEqual(result["retained"], 1)
        self.assertEqual(self.connection.execute(
            "SELECT consecutive_completed_misses FROM c2_4_lifecycle_state WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0], 0)

    def test_immediate_public_exit_keeps_tracking_history_and_allows_reentry(self):
        candidate_id = self.add_candidate("immediate", "A")
        record_first_gate_history(self.connection, [candidate_id])
        self.set_completed_window(candidate_id, "window-1", 1, 10)
        record_completed_public_history(self.connection, [candidate_id])
        self.set_completed_window(candidate_id, "window-2", 2, 20, hard_block=True)
        self.assertEqual(record_completed_public_history(self.connection, [candidate_id])["stopped"], 1)
        state = self.connection.execute(
            """SELECT h.public_active,l.stopped_at FROM c2_4_public_history h
            JOIN c2_4_lifecycle_state l USING(candidate_id) WHERE h.candidate_id=?""",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(state["public_active"], 0)
        self.assertIsNotNone(state["stopped_at"])
        self.assertEqual(
            int(_select_candidates(self.connection, 1, refresh_completed=True)[0]["candidate_id"]),
            candidate_id,
        )
        self.set_completed_window(candidate_id, "window-3", 3, 10)
        self.assertEqual(record_completed_public_history(self.connection, [candidate_id])["public"], 1)
        self.assertEqual(self.connection.execute(
            "SELECT public_active FROM c2_4_public_history WHERE candidate_id=?", (candidate_id,)
        ).fetchone()[0], 1)

    def test_completed_objects_rotate_by_oldest_completed_window(self):
        older = self.add_candidate("older", "A")
        newer = self.add_candidate("newer", "A")
        for candidate_id, completed_at in ((older, "2026-08-10T00:00:00Z"), (newer, "2026-08-12T00:00:00Z")):
            self.connection.execute(
                """INSERT INTO candidate_tracking_records(
                  candidate_id,qualification_batch_id,input_updated_at,state,attempt_count,source_states_json,
                  completed_at,updated_at
                ) VALUES(?,'batch',?,'completed',1,'{}',?,?)""",
                (candidate_id, NOW, completed_at, completed_at),
            )
        self.connection.commit()
        selected = _select_candidates(self.connection, 1, refresh_completed=True)
        self.assertEqual(int(selected[0]["candidate_id"]), older)


if __name__ == "__main__":
    unittest.main()
