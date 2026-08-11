#!/usr/bin/env python3
"""C2.2 runtime recovery and independent-job setting regression tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from c2_2_runtime import atomic_json, load_config, pipeline_lock, update_config


class C22RuntimeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
