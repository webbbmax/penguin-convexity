#!/usr/bin/env python3
"""C2.2 runtime recovery and independent-job setting regression tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import run_update_task as update_runner
import run_c2_2_update as c22_update
from c2_2_runtime import (
    atomic_json,
    is_due,
    load_config,
    launch_hidden,
    next_run_at,
    pause_current_requested,
    pipeline_lock,
    request_pause_current,
    update_config,
)
from update_tasks import task_definition


class C22RuntimeTests(unittest.TestCase):
    def test_partial_continuation_is_due_at_the_next_scheduler_tick(self):
        now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
        config = {
            "jobs": {
                "convexity_tracking": {
                    "mode": "automatic",
                    "intervalHours": 24,
                    "paused": False,
                }
            }
        }
        with patch("c2_2_runtime.load_config", return_value=config):
            due = datetime.fromisoformat(
                next_run_at("convexity_tracking", from_time=now, continuation=True).replace("Z", "+00:00")
            )
        self.assertLessEqual(due, now)

    def test_partial_automatic_job_resumes_before_the_next_full_update_cycle(self):
        now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
        config = {
            "jobs": {
                "convexity_tracking": {
                    "mode": "automatic",
                    "intervalHours": 24,
                    "paused": False,
                }
            }
        }
        status = {
            "state": "partial",
            "lastCompletedAt": (now - timedelta(minutes=15)).isoformat(),
            "nextDueAt": (now + timedelta(hours=23)).isoformat(),
        }
        with (
            patch("c2_2_runtime.load_config", return_value=config),
            patch("c2_2_runtime.job_status", return_value=status),
        ):
            self.assertTrue(is_due("convexity_tracking", now=now))

    def test_completed_automatic_job_keeps_the_user_selected_frequency(self):
        now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
        config = {
            "jobs": {
                "convexity_tracking": {
                    "mode": "automatic",
                    "intervalHours": 24,
                    "paused": False,
                }
            }
        }
        status = {
            "state": "completed",
            "lastCompletedAt": (now - timedelta(minutes=15)).isoformat(),
            "nextDueAt": (now + timedelta(hours=23)).isoformat(),
        }
        with (
            patch("c2_2_runtime.load_config", return_value=config),
            patch("c2_2_runtime.job_status", return_value=status),
        ):
            self.assertFalse(is_due("convexity_tracking", now=now))

    def test_updating_screening_does_not_change_tracking_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "update-config.json"
            original = {
                "schemaVersion": "c2.2-update-config-v1",
                "timezone": "Asia/Shanghai",
                "updatedAt": None,
                "jobs": {
                    "screening": {"mode": "automatic", "intervalHours": 24, "paused": False},
                    "convexity_tracking": {"mode": "manual", "intervalHours": None, "paused": True},
                },
            }
            atomic_json(path, original)
            before_tracking = load_config(path)["jobs"]["convexity_tracking"].copy()
            updated = update_config("screening", {"mode": "automatic", "intervalHours": 6, "paused": False}, path)
            self.assertEqual(updated["jobs"]["convexity_tracking"], before_tracking)
            self.assertEqual(updated["jobs"]["screening"]["intervalHours"], 6)

    def test_pipeline_lock_rejects_live_owner_and_recovers_stale_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pipeline.lock"
            path.write_text(str(os.getpid()), encoding="ascii")
            with pipeline_lock(path) as acquired:
                self.assertFalse(acquired)
            self.assertTrue(path.exists())

            path.write_text("not-a-pid", encoding="ascii")
            with pipeline_lock(path) as acquired:
                self.assertTrue(acquired)
            self.assertFalse(path.exists())

    def test_atomic_json_never_leaves_partial_temp_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            payload = {"schemaVersion": "test-v1", "jobs": {"screening": {"state": "completed"}}}
            atomic_json(path, payload)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_pause_request_only_targets_the_selected_job(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pause-current.json"
            with patch("c2_2_runtime.DEFAULT_PAUSE_PATH", path):
                self.assertTrue(request_pause_current(True, "screening"))
                self.assertTrue(pause_current_requested("screening"))
                self.assertFalse(pause_current_requested("convexity_tracking"))
                self.assertFalse(request_pause_current(False, "screening"))

    def test_pause_request_rejects_unknown_job(self):
        with self.assertRaisesRegex(ValueError, "没有找到"):
            request_pause_current(True, "unknown")

    def test_tracking_update_stops_at_component_safe_point(self):
        def simulate_refresh(**kwargs):
            kwargs["progress_callback"]("market", "准备读取市场数据")
            raise AssertionError("暂停后不应继续执行")

        with (
            patch.object(update_runner, "task_definition", return_value={"label": "测试跟踪", "components": ["market"]}),
            patch.object(update_runner, "begin_progress"),
            patch.object(update_runner, "update_retry_status"),
            patch.object(update_runner, "refresh_candidates", side_effect=simulate_refresh),
            patch.object(update_runner, "rebuild_update_snapshots"),
            patch.object(update_runner, "finish_progress") as finish,
        ):
            result = update_runner.run_update_task(
                task_id="test_tracking",
                db_path=Path("unused.db"),
                pause_requested=lambda: True,
            )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(finish.call_args.args[0], "paused")

    def test_single_source_update_is_launched_through_the_selected_c22_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            process = MagicMock(pid=4321)
            with (
                patch("c2_2_runtime.DEFAULT_PAUSE_PATH", root / "pause.json"),
                patch("c2_2_runtime.DEFAULT_LOG_PATH", root / "runner.log"),
                patch("c2_2_runtime.job_status", side_effect=lambda code: {"jobCode": code, "state": "completed"}),
                patch("c2_2_runtime.load_state", return_value={}),
                patch("c2_2_runtime.save_state"),
                patch("c2_1_runtime.request_pause_current"),
                patch("c2_2_runtime.subprocess.Popen", return_value=process) as popen,
            ):
                result = launch_hidden("screening", "manual", "github")
        command = popen.call_args.args[0]
        self.assertEqual(result["status"], "launched")
        self.assertEqual(command[-2:], ["--source-id", "github"])
        self.assertIn("screening", command)

    def test_c22_tracking_task_excludes_legacy_decision_components(self):
        task = task_definition("c2_2_convexity_tracking_refresh")
        components = set(task["components"])
        self.assertTrue({
            "formal_market_exit",
            "high_value_evidence",
            "evidence",
            "data_backbone",
            "tracking",
        }.issubset(components))
        self.assertTrue({
            "machine_research_scoring",
            "machine_conclusion",
            "catalyst_trade_path",
            "source_discovery",
            "discovery",
            "identity",
            "project_asset_identity",
        }.isdisjoint(components))
        self.assertFalse(task["publishLegacySnapshots"])

    def test_c22_tracking_uses_the_dedicated_task_and_own_progress(self):
        observed_progress = []

        def fake_update(**kwargs):
            self.assertEqual(kwargs["task_id"], "c2_2_convexity_tracking_refresh")
            self.assertFalse(kwargs["legacy_status"])
            kwargs["status_callback"]("formal_market_exit", "正在更新市场与退出资料", 1, 5)
            observed_progress.append("called")
            return {"status": "success"}

        with (
            patch.object(c22_update, "pause_current_requested", return_value=False),
            patch.object(c22_update, "load_json", return_value={"schemaVersion": "c2.2-post-baseline-state-v1"}),
            patch.object(c22_update, "atomic_json"),
            patch.object(c22_update, "set_status") as set_status,
            patch.object(c22_update, "reconcile_c24_history"),
            patch.object(c22_update, "build_c22_snapshots", return_value={
                "front": {"buildId": "front"},
                "tracking": {"buildId": "tracking"},
            }),
            patch.object(c22_update, "_link_active_rule_run", return_value={"status": "linked"}),
            patch("candidate_production_runtime.pause_for_screening", return_value={
                "status": "idle", "resumeAfter": False,
            }),
            patch("candidate_production_runtime.resume_after_screening"),
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch", return_value={
                "status": "completed", "selected": 12, "completed": 10, "partial": 2,
            }) as candidate_tracking,
            patch("c2_2_candidate_tracking.run_deep_structure_batch", return_value={
                "status": "completed", "selected": 0, "hasMore": False,
            }),
            patch.object(update_runner, "run_update_task", side_effect=fake_update),
        ):
            result = c22_update.run_tracking("development", "run-1", False)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(observed_progress, ["called"])
        progress_calls = [call for call in set_status.call_args_list if call.kwargs.get("stage") == "formal_market_exit"]
        self.assertEqual(len(progress_calls), 1)
        self.assertEqual(progress_calls[0].kwargs["message"], "正在更新市场与退出资料")
        self.assertEqual(result["candidateTracking"]["selected"], 12)
        self.assertEqual(
            candidate_tracking.call_args.kwargs["limit"],
            c22_update.TRACKING_HANDOFF_BATCH_SIZE,
        )

    def test_incomplete_candidate_handoff_publishes_checkpoint_before_legacy_tracking(self):
        batch = {
            "status": "completed",
            "selected": 25,
            "completed": 25,
            "partial": 0,
            "candidateIds": list(range(1, 26)),
            "queue": {"total": 100, "completed": 25, "partial": 0, "remaining": 75},
        }
        with (
            patch.object(c22_update, "pause_current_requested", return_value=False),
            patch.object(c22_update, "TRACKING_HANDOFF_MAX_BATCHES", 1),
            patch.object(c22_update, "set_status") as set_status,
            patch.object(c22_update, "reconcile_c24_history") as reconcile,
            patch.object(c22_update, "build_c22_snapshots", return_value={
                "tracking": {"buildId": "tracking-checkpoint"},
            }) as build_snapshots,
            patch.object(c22_update, "_link_active_rule_run", return_value={"status": "linked"}),
            patch("candidate_production_runtime.pause_for_screening", return_value={
                "status": "idle", "resumeAfter": False,
            }),
            patch("candidate_production_runtime.resume_after_screening"),
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch", return_value=batch),
            patch("run_update_task.run_update_task") as legacy_tracking,
        ):
            result = c22_update.run_tracking("automatic", "run-partial", False)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["tracking"]["status"], "deferred_until_candidate_handoff_complete")
        legacy_tracking.assert_not_called()
        reconcile.assert_called_once_with()
        build_snapshots.assert_called_once_with()
        self.assertEqual(set_status.call_args.kwargs["state"], "partial")
        self.assertEqual(set_status.call_args.kwargs["completed"], 25)

    def test_isolated_partial_objects_do_not_stop_fresh_candidate_progress(self):
        batches = [
            {
                "status": "partial_success",
                "selected": 25,
                "completed": 24,
                "partial": 1,
                "candidateIds": list(range(1, 26)),
                "queue": {"total": 100, "completed": 24, "partial": 1, "remaining": 76},
            },
            {
                "status": "completed",
                "selected": 25,
                "completed": 25,
                "partial": 0,
                "candidateIds": list(range(26, 51)),
                "queue": {"total": 100, "completed": 49, "partial": 1, "remaining": 51},
            },
        ]
        with (
            patch.object(c22_update, "pause_current_requested", return_value=False),
            patch.object(c22_update, "TRACKING_HANDOFF_MAX_BATCHES", 2),
            patch.object(c22_update, "set_status"),
            patch.object(c22_update, "reconcile_c24_history"),
            patch.object(c22_update, "build_c22_snapshots", return_value={
                "tracking": {"buildId": "tracking-checkpoint"},
            }),
            patch.object(c22_update, "_link_active_rule_run", return_value={"status": "linked"}),
            patch("candidate_production_runtime.pause_for_screening", return_value={
                "status": "idle", "resumeAfter": False,
            }),
            patch("candidate_production_runtime.resume_after_screening"),
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch", side_effect=batches) as candidate_tracking,
            patch("run_update_task.run_update_task") as legacy_tracking,
        ):
            result = c22_update.run_tracking("automatic", "run-partial-progress", False)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(candidate_tracking.call_count, 2)
        self.assertEqual(result["candidateTracking"]["selected"], 50)
        self.assertEqual(result["candidateTracking"]["completed"], 49)
        legacy_tracking.assert_not_called()

    def test_same_cycle_continuation_does_not_repeat_legacy_mainline(self):
        cycle_key = datetime.now().astimezone().date().isoformat()
        batch = {
            "status": "completed",
            "selected": 0,
            "completed": 0,
            "partial": 0,
            "candidateIds": [],
            "queue": {"total": 100, "completed": 100, "partial": 0, "remaining": 0},
        }
        with (
            patch.object(c22_update, "pause_current_requested", return_value=False),
            patch.object(c22_update, "load_json", return_value={
                "schemaVersion": "c2.2-post-baseline-state-v1",
                "legacyMainlineCycleKey": cycle_key,
            }),
            patch.object(c22_update, "set_status"),
            patch.object(c22_update, "reconcile_c24_history"),
            patch.object(c22_update, "build_c22_snapshots", return_value={
                "tracking": {"buildId": "tracking-current-cycle"},
            }),
            patch.object(c22_update, "_link_active_rule_run", return_value={"status": "linked"}),
            patch("candidate_production_runtime.pause_for_screening", return_value={
                "status": "idle", "resumeAfter": False,
            }),
            patch("candidate_production_runtime.resume_after_screening"),
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch", return_value=batch),
            patch("c2_2_candidate_tracking.run_deep_structure_batch", return_value={
                "status": "completed", "selected": 0, "hasMore": False,
            }),
            patch.object(update_runner, "run_update_task") as legacy_tracking,
        ):
            result = c22_update.run_tracking("automatic", "run-current-cycle", False)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["tracking"]["skipped"])
        legacy_tracking.assert_not_called()

    def test_tracking_waits_for_candidate_checkpoint_instead_of_creating_a_second_writer(self):
        with (
            patch.object(c22_update, "set_status") as set_status,
            patch("candidate_production_runtime.pause_for_screening", return_value={
                "status": "timeout", "resumeAfter": False, "pid": 4321,
            }),
            patch("candidate_production_runtime.resume_after_screening") as resume,
            patch("c2_2_candidate_tracking.run_candidate_tracking_batch") as candidate_tracking,
            patch.object(update_runner, "run_update_task") as legacy_tracking,
        ):
            result = c22_update.run_tracking("development", "run-wait", False)

        self.assertEqual(result["status"], "already_running")
        candidate_tracking.assert_not_called()
        legacy_tracking.assert_not_called()
        resume.assert_not_called()
        self.assertEqual(set_status.call_args.kwargs["stage"], "waiting_for_candidate_checkpoint")

    def test_dedicated_tracking_does_not_write_legacy_status(self):
        callback = MagicMock()
        with (
            patch.object(update_runner, "task_definition", return_value={
                "label": "C2.2凸性跟踪更新",
                "components": ["formal_market_exit"],
                "publishLegacySnapshots": False,
            }),
            patch.object(update_runner, "refresh_candidates", return_value={
                "status": "success",
                "errors": 0,
                "runId": "copy-run",
                "explanation": "副本验证完成。",
            }),
            patch.object(update_runner, "begin_progress") as begin,
            patch.object(update_runner, "update_progress") as update,
            patch.object(update_runner, "finish_progress") as finish,
            patch.object(update_runner, "record_failed_run") as record_failed,
            patch.object(update_runner, "rebuild_update_snapshots") as rebuild_legacy,
        ):
            result = update_runner.run_update_task(
                task_id="c2_2_convexity_tracking_refresh",
                db_path=Path("copy.db"),
                status_callback=callback,
                legacy_status=False,
            )

        self.assertEqual(result["status"], "success")
        begin.assert_not_called()
        update.assert_not_called()
        finish.assert_not_called()
        record_failed.assert_not_called()
        rebuild_legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
