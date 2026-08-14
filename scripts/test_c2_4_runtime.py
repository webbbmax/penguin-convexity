#!/usr/bin/env python3
"""Tier 0 tests for the bounded C2.4 refresh-cycle handoff."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import run_c2_2_update as update


class C24RuntimeTests(unittest.TestCase):
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
            patch("candidate_production_runtime.pause_for_screening", return_value={"status": "idle", "resumeAfter": False}),
            patch("candidate_production_runtime.resume_after_screening"),
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch", side_effect=batch) as tracker,
            patch("run_update_task.run_update_task", return_value={"status": "success"}) as legacy,
        ):
            result = update.run_tracking("development", "c24-test", False)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidateTracking"]["selected"], 3)
        self.assertEqual(result["candidateTracking"]["batches"], 2)
        self.assertEqual(calls, [set(), {1, 2}])
        self.assertTrue(all(call.kwargs["refresh_completed"] for call in tracker.call_args_list))
        legacy.assert_called_once()

    def test_partial_batch_stops_refresh_cycle_for_recoverable_retry(self):
        partial = {
            "status": "partial_success", "candidateIds": [1, 2], "selected": 2,
            "completed": 1, "partial": 1,
            "queue": {"completed": 2, "total": 10, "remaining": 8},
        }
        with (
            patch.object(update, "set_status"),
            patch.object(update, "pause_current_requested", return_value=False),
            patch.object(update, "build_c22_snapshots", return_value={"tracking": {"buildId": "tracking"}}),
            patch("candidate_production_runtime.pause_for_screening", return_value={"status": "idle", "resumeAfter": False}),
            patch("candidate_production_runtime.resume_after_screening"),
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch", return_value=partial) as tracker,
            patch("run_update_task.run_update_task", return_value={"status": "success"}),
        ):
            result = update.run_tracking("development", "c24-partial", False)

        self.assertEqual(tracker.call_count, 1)
        self.assertEqual(result["candidateTracking"]["status"], "partial_success")


if __name__ == "__main__":
    unittest.main()
