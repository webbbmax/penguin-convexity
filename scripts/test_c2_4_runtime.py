#!/usr/bin/env python3
"""Tier 0 tests for the bounded C2.4 refresh-cycle handoff."""

from __future__ import annotations

import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import run_c2_2_update as update


class C24RuntimeTests(unittest.TestCase):
    def test_atomic_snapshot_publication_links_the_rule_version_to_the_run(self):
        payloads = {
            "tracking": {"buildId": "c22-tracking"},
            "c24": {
                "tracking": {"buildId": "tracking-build", "ruleVersion": "c2.4-rules-v1"},
                "front": {"buildId": "front-build"},
                "admin": {"buildId": "admin-build"},
            },
        }
        with (
            patch.object(update, "build_c22_snapshots", return_value=payloads),
            patch.object(update, "_link_active_rule_run", return_value={"status": "linked"}) as link,
        ):
            result = update.build_c22_snapshots_for_run("c22-rule-link-run")
        link.assert_called_once_with("c22-rule-link-run", payloads)
        self.assertEqual(result["ruleVersionRunLink"]["status"], "linked")

    def test_rule_run_link_contains_real_run_and_three_snapshot_ids(self):
        payloads = {
            "c24": {
                "tracking": {"buildId": "tracking-build", "ruleVersion": "c2.4-rules-v1", "generatedAt": "2026-08-15T00:00:00Z"},
                "front": {"buildId": "front-build", "generatedAt": "2026-08-15T00:00:00Z"},
                "admin": {"buildId": "admin-build", "generatedAt": "2026-08-15T00:00:00Z"},
            }
        }
        with patch("c2_5_rule_governance.RuleGovernanceStore") as store_type:
            store_type.return_value.link_next_legal_run.return_value = {"status": "linked"}
            result = update._link_active_rule_run("c22-real-run", payloads)
        self.assertEqual(result["status"], "linked")
        kwargs = store_type.return_value.link_next_legal_run.call_args.kwargs
        self.assertEqual(kwargs["run_id"], "c22-real-run")
        self.assertEqual(kwargs["rule_version"], "c2.4-rules-v1")
        self.assertEqual([row["snapshotId"] for row in kwargs["snapshots"]], ["tracking-build", "front-build", "admin-build"])

    def test_changed_first_gate_contracts_are_rechecked_in_the_same_cycle(self):
        connection = unittest.mock.MagicMock()
        with (
            patch("c2_1_db.open_pipeline_db", return_value=connection),
            patch("candidate_production.changed_first_gate_contract_candidate_ids", return_value=[11, 12]) as changed,
            patch("candidate_production.refresh_production_contracts", return_value=[11, 12]) as refresh,
            patch("candidate_production.process_first_gate_candidates", return_value={"selected": 2, "evaluated": 2}) as recheck,
        ):
            result = update.recheck_changed_first_gate_contracts()

        self.assertEqual(result["changed"], 2)
        self.assertEqual(result["refreshed"], 2)
        self.assertEqual(result["firstGateRechecked"], 2)
        changed.assert_called_once_with(connection, limit=update.SCREENING_EVALUATION_BATCH_SIZE)
        refresh.assert_called_once_with(connection, [11, 12])
        recheck.assert_called_once_with(connection, candidate_ids=[11, 12], refresh_market=False)

    def test_materialized_first_gate_backlog_is_drained_before_publication(self):
        connection = unittest.mock.MagicMock()
        with (
            patch("c2_1_db.open_pipeline_db", return_value=connection),
            patch("candidate_production.pending_first_gate_candidate_ids", side_effect=[[21, 22], [23], []]) as pending,
            patch("candidate_production.process_first_gate_candidates", side_effect=[
                {"selected": 2, "evaluated": 2},
                {"selected": 1, "evaluated": 1},
            ]) as recheck,
        ):
            result = update.drain_first_gate_backlog()

        self.assertEqual(result, {"batches": 2, "processed": 3, "remaining": 0})
        self.assertEqual(pending.call_count, 3)
        self.assertEqual(recheck.call_count, 2)

    def test_only_the_active_job_is_failed_when_an_exception_escapes(self):
        with (
            patch.object(update, "pipeline_lock", return_value=nullcontext(True)),
            patch.object(update, "load_state", return_value={}),
            patch.object(update, "save_state"),
            patch.object(update, "pause_current_requested", return_value=False),
            patch.object(update, "run_screening", side_effect=ValueError("reconciliation")),
            patch.object(update, "run_tracking") as tracking,
            patch.object(update, "set_status") as status,
        ):
            result = update.run("all", trigger="development")

        self.assertEqual(result["status"], "failed")
        self.assertEqual([call.args[0] for call in status.call_args_list], ["screening"])
        tracking.assert_not_called()

    def test_hidden_scheduler_propagates_the_python_exit_code(self):
        script = (Path(__file__).resolve().parent / "run-c2-1-update-hidden.vbs").read_text(encoding="utf-8")
        self.assertIn("runnerExitCode = shell.Run(command, 0, True)", script)
        self.assertIn("WScript.Quit runnerExitCode", script)

    def test_tracking_refresh_advances_distinct_batches_before_legacy_components(self):
        calls: list[set[int]] = []

        def batch(**kwargs):
            excluded = set(kwargs.get("exclude_candidate_ids") or set())
            calls.append(excluded)
            if not excluded:
                return {
                    "status": "completed", "candidateIds": [1, 2], "selected": 2,
                    "completed": 2, "partial": 0,
                    "queue": {"completed": 2, "total": 3, "remaining": 1},
                }
            if excluded == {1, 2}:
                return {
                    "status": "completed", "candidateIds": [3], "selected": 1,
                    "completed": 1, "partial": 0,
                    "queue": {"completed": 3, "total": 3, "remaining": 0},
                }

        with (
            patch.object(update, "set_status"),
            patch.object(update, "pause_current_requested", return_value=False),
            patch.object(update, "build_c22_snapshots", return_value={"tracking": {"buildId": "tracking"}}),
            patch.object(update, "_link_active_rule_run", return_value={"status": "linked"}),
            patch.object(update, "reconcile_c24_history"),
            patch.object(update, "load_json", return_value={}),
            patch("candidate_production_runtime.pause_for_screening", return_value={"status": "idle", "resumeAfter": False}),
            patch("candidate_production_runtime.resume_after_screening"),
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch", side_effect=batch) as tracker,
            patch("c2_2_candidate_tracking.run_deep_structure_batch", return_value={"status": "completed", "hasMore": False}),
            patch("run_update_task.run_update_task", return_value={"status": "success"}) as legacy,
        ):
            result = update.run_tracking("development", "c24-test", False)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidateTracking"]["selected"], 3)
        self.assertEqual(result["candidateTracking"]["batches"], 2)
        self.assertEqual(calls, [set(), {1, 2}])
        self.assertTrue(all(call.kwargs["refresh_completed"] for call in tracker.call_args_list))
        legacy.assert_called_once()

    def test_partial_batch_keeps_advancing_distinct_candidates_until_empty(self):
        partial = {
            "status": "partial_success", "candidateIds": [1, 2], "selected": 2,
            "completed": 1, "partial": 1,
            "queue": {"completed": 2, "total": 10, "remaining": 8},
        }
        empty = {
            "status": "completed", "candidateIds": [], "selected": 0,
            "completed": 0, "partial": 0,
            "queue": {"completed": 2, "total": 10, "remaining": 8},
        }
        with (
            patch.object(update, "set_status"),
            patch.object(update, "pause_current_requested", return_value=False),
            patch.object(update, "build_c22_snapshots", return_value={"tracking": {"buildId": "tracking"}}),
            patch.object(update, "_link_active_rule_run", return_value={"status": "linked"}),
            patch.object(update, "reconcile_c24_history"),
            patch("candidate_production_runtime.pause_for_screening", return_value={"status": "idle", "resumeAfter": False}),
            patch("candidate_production_runtime.resume_after_screening"),
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch", side_effect=[partial, empty]) as tracker,
            patch("run_update_task.run_update_task", return_value={"status": "success"}),
        ):
            result = update.run_tracking("development", "c24-partial", False)

        self.assertEqual(tracker.call_count, 2)
        self.assertEqual(result["candidateTracking"]["status"], "partial_success")


if __name__ == "__main__":
    unittest.main()
