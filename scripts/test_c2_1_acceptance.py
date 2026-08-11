#!/usr/bin/env python3

import ast
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import c2_1_pipeline as pipeline
import run_c2_1_update as update_entry
from c2_1_db import initialize_database, open_pipeline_db


ROOT = Path(__file__).resolve().parent.parent
FIXED_TIME = "2026-08-10T12:00:00Z"


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
        return value if tz else value.replace(tzinfo=None)


class FixedUuid:
    hex = "123456abcdef00000000000000000000"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class C21IndependentAcceptanceTests(unittest.TestCase):
    def test_frozen_input_produces_identical_front_snapshot_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = []
            with mock.patch.object(pipeline, "utc_now", return_value=FIXED_TIME), mock.patch.object(
                pipeline, "datetime", FrozenDateTime
            ), mock.patch.object(pipeline.uuid, "uuid4", return_value=FixedUuid()):
                for name in ("manual", "automatic"):
                    folder = root / name
                    db = folder / "pipeline.db"
                    front = folder / "front.js"
                    backend = folder / "backend.js"
                    initialize_database(db)
                    with closing(open_pipeline_db(db)) as connection:
                        result = pipeline.build_snapshots(connection, front, backend)
                        business_state = connection.execute(
                            "SELECT COUNT(*),COALESCE(SUM(is_current),0) FROM evaluations"
                        ).fetchone()
                    results.append((result["frontSha256"], sha256(front), tuple(business_state)))
            self.assertEqual(results[0], results[1])

    def test_manual_and_automatic_use_same_pipeline_action(self):
        calls = []

        def fake_pipeline(action, trigger_kind):
            calls.append((action, trigger_kind))
            return {"status": "completed", "semanticResult": "same"}

        state = {"lastFinishedAt": None}
        config = {"mode": "automatic", "intervalHours": 1, "paused": False, "timezone": "Asia/Shanghai"}
        common = (
            mock.patch.object(update_entry, "load_state", return_value=state.copy()),
            mock.patch.object(update_entry, "load_config", return_value=config),
            mock.patch.object(update_entry, "save_state"),
            mock.patch.object(update_entry, "run_pipeline", side_effect=fake_pipeline),
        )
        with common[0], common[1], common[2], common[3]:
            manual = update_entry.run("all", "manual")
        with mock.patch.object(update_entry, "load_state", return_value=state.copy()), mock.patch.object(
            update_entry, "load_config", return_value=config
        ), mock.patch.object(update_entry, "save_state"), mock.patch.object(
            update_entry, "run_pipeline", side_effect=fake_pipeline
        ), mock.patch.object(update_entry, "pipeline_status", return_value={"state": "completed"}), mock.patch.object(
            update_entry, "interrupted_run_requires_resume", return_value=False
        ), mock.patch.object(update_entry, "due_source_resume", return_value=False), mock.patch.object(
            update_entry, "is_due", return_value=True
        ):
            automatic = update_entry.run("all", "automatic")
        self.assertEqual(manual["semanticResult"], automatic["semanticResult"])
        self.assertEqual([call[0] for call in calls], ["all", "all"])

    def test_front_contract_and_plain_language_are_present(self):
        source = (ROOT / "app" / "c2-1-front.js").read_text(encoding="utf-8")
        for token in (
            'params.get("evidenceType")', 'params.get("dataStatus")', '"latest_change"',
            '"t0_desc"', '"liquidity_desc"', '"volume_desc"', "c21-home-states",
            "主要风险", "最近变化", "发生了什么", "statusLabel[x.status]",
            "本产品只能在已接入范围内自动形成研究线索",
        ):
            self.assertIn(token, source)
        self.assertNotIn("早期观察与持续观察", source)

    def test_same_hidden_task_and_single_instance_are_frozen_in_release_scripts(self):
        hidden = (ROOT / "scripts" / "run-c2-1-update-hidden.vbs").read_text(encoding="utf-8-sig")
        installer = (ROOT / "scripts" / "install-c2.1-scheduler.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("shell.Run command, 0, True", hidden)
        self.assertIn('taskName = "PenguinConvexity-C1.8-Scheduler"', installer)
        self.assertIn('/SC MINUTE /MO 15', installer)
        self.assertIn('MultipleInstances = "IgnoreNew"', installer)
        self.assertNotIn("Register-ScheduledTask -TaskName \"PenguinConvexity-C2.1", installer)

    def test_desktop_launcher_recognizes_c21_and_handles_empty_error_log(self):
        launcher = (ROOT / "scripts" / "launch-convexity.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('$opportunityResponse.Content -match "c2-2-front.js"', launcher)
        self.assertIn('$health.experienceRelease -eq "C2.2"', launcher)
        self.assertNotIn('$health.experienceRelease -eq "C2.0"', launcher)
        self.assertIn("[string]::IsNullOrWhiteSpace($stderrContent)", launcher)

    def test_c21_desktop_startup_does_not_recover_legacy_updates_into_main_db(self):
        server = (ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
        tree = ast.parse(server)
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        startup_calls = {
            node.func.id
            for node in ast.walk(functions["rebuild_startup_snapshots"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        main_calls = {
            node.func.id
            for node in ast.walk(functions["main"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("initialize_update_recovery", main_calls)
        self.assertNotIn("initialize_database", main_calls)
        self.assertFalse(
            startup_calls
            & {
                "rebuild_source_adapter_snapshot",
                "rebuild_evidence_ledger_snapshot",
                "rebuild_master_pool_snapshot",
                "build_decision_quality_snapshots",
            }
        )
        self.assertIn("open_main_database_readonly", main_calls)
        self.assertIn(
            '(APP_ROOT / "c2-1-front-snapshot.js", "window.PENGUIN_CONVEXITY_C21 = ")',
            server,
        )
        self.assertIn(
            '(APP_ROOT / "c2-1-admin-snapshot.js", "window.PENGUIN_CONVEXITY_C21_ADMIN = ")',
            server,
        )

    def test_required_desktop_screenshot_set_has_no_horizontal_overflow(self):
        manifest_path = ROOT / "reports" / "c2.1-final-acceptance" / "screenshots" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(manifest["productDataMutated"])
        self.assertGreaterEqual(len(manifest["screenshots"]), 12)
        self.assertTrue(any(row["viewport"] == [1180, 760] for row in manifest["screenshots"]))
        self.assertTrue(any(row["viewport"] == [1440, 900] for row in manifest["screenshots"]))
        for row in manifest["screenshots"]:
            self.assertFalse(row["hasHorizontalOverflow"], row["file"])
            self.assertTrue((manifest_path.parent / row["file"]).is_file(), row["file"])


if __name__ == "__main__":
    unittest.main()
