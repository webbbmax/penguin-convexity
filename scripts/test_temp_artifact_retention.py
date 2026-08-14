#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from temp_artifact_retention import (  # noqa: E402
    MARKER_NAME,
    RetentionError,
    TempArtifactRetention,
)


class TempArtifactRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_root = ROOT / "runtime" / "temp-artifact-retention-tests" / uuid.uuid4().hex
        self.project_root = self.test_root / "project"
        self.runtime_root = self.project_root / "runtime"
        self.managed_root = self.runtime_root / "temp-artifacts"
        self.manager = TempArtifactRetention(
            project_root=self.project_root,
            managed_root=self.managed_root,
            state_path=self.runtime_root / "maintenance" / "sweep.json",
            audit_path=self.runtime_root / "maintenance" / "audit.jsonl",
        )

    def tearDown(self) -> None:
        if self.test_root.exists():
            shutil.rmtree(self.test_root)
        try:
            self.test_root.parent.rmdir()
        except OSError:
            pass

    def test_sealed_due_artifact_is_deleted(self) -> None:
        created_at = datetime.now(timezone.utc)
        marker = self.manager.create(
            owner_task="acceptance",
            purpose="temporary database copy",
            retention_hours=1,
            owner_pid=99999999,
            now=created_at,
        )
        artifact_dir = Path(marker["absolutePath"])
        (artifact_dir / "copy.db").write_bytes(b"temporary")
        sealed_at = created_at + timedelta(seconds=1)
        self.manager.seal(artifact_dir, retention_hours=0, now=sealed_at)

        result = self.manager.sweep(force=True, now=sealed_at + timedelta(seconds=1))

        self.assertEqual(result["deletedArtifacts"], 1)
        self.assertEqual(result["deletedLogicalBytes"], len(b"temporary"))
        self.assertFalse(artifact_dir.exists())

    def test_active_artifact_owned_by_live_process_is_not_deleted(self) -> None:
        created_at = datetime.now(timezone.utc)
        marker = self.manager.create(
            owner_task="running-test",
            purpose="in-use fixture",
            retention_hours=0.01,
            owner_pid=os.getpid(),
            now=created_at,
        )
        artifact_dir = Path(marker["absolutePath"])

        result = self.manager.sweep(force=True, now=created_at + timedelta(hours=1))

        self.assertEqual(result["inUseArtifacts"], 1)
        self.assertEqual(result["deletedArtifacts"], 0)
        self.assertTrue(artifact_dir.exists())

    def test_paths_outside_managed_root_are_rejected(self) -> None:
        formal_database_root = self.project_root / "data"
        formal_database_root.mkdir(parents=True)

        with self.assertRaises(RetentionError):
            self.manager.register(
                formal_database_root,
                owner_task="bad-test",
                purpose="must never be accepted",
            )

    def test_unregistered_directory_is_left_untouched(self) -> None:
        unregistered = self.managed_root / "unregistered"
        unregistered.mkdir(parents=True)
        (unregistered / "keep.txt").write_text("keep", encoding="utf-8")

        result = self.manager.sweep(force=True)

        self.assertEqual(result["unregisteredEntries"], 1)
        self.assertEqual(result["deletedArtifacts"], 0)
        self.assertTrue((unregistered / "keep.txt").exists())

    def test_sealed_artifact_changed_after_seal_is_blocked(self) -> None:
        created_at = datetime.now(timezone.utc)
        marker = self.manager.create(
            owner_task="changed-test",
            purpose="change detection",
            retention_hours=1,
            owner_pid=99999999,
            now=created_at,
        )
        artifact_dir = Path(marker["absolutePath"])
        payload = artifact_dir / "payload.bin"
        payload.write_bytes(b"before")
        sealed_at = created_at + timedelta(seconds=1)
        self.manager.seal(artifact_dir, retention_hours=0, now=sealed_at)
        payload.write_bytes(b"after")
        future_timestamp = (sealed_at + timedelta(seconds=2)).timestamp()
        os.utime(payload, (future_timestamp, future_timestamp))

        result = self.manager.sweep(force=True, now=sealed_at + timedelta(hours=1))

        self.assertEqual(result["blockedArtifacts"], 1)
        self.assertEqual(result["deletedArtifacts"], 0)
        self.assertTrue(payload.exists())

    def test_invalid_marker_fails_closed(self) -> None:
        artifact_dir = self.managed_root / "invalid-marker"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "payload.bin").write_bytes(b"keep")
        (artifact_dir / MARKER_NAME).write_text(
            json.dumps({"schemaVersion": "unknown"}), encoding="utf-8"
        )

        result = self.manager.sweep(force=True)

        self.assertEqual(result["blockedArtifacts"], 1)
        self.assertEqual(result["deletedArtifacts"], 0)
        self.assertTrue(artifact_dir.exists())

    def test_scheduled_sweep_is_throttled_to_once_per_day(self) -> None:
        first = datetime.now(timezone.utc)
        initial = self.manager.sweep(force=True, now=first)
        second = self.manager.sweep(min_interval_hours=24, now=first + timedelta(hours=1))

        self.assertEqual(initial["status"], "completed")
        self.assertEqual(second["status"], "skipped_recently")

    def test_existing_hidden_scheduler_calls_retention_sweep(self) -> None:
        hidden_runner = (ROOT / "scripts" / "run-c2-1-update-hidden.vbs").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("temp_artifact_retention.py", hidden_runner)
        self.assertIn("sweep --min-interval-hours 24", hidden_runner)
        self.assertIn("run_c2_2_update.py", hidden_runner)


if __name__ == "__main__":
    unittest.main()
