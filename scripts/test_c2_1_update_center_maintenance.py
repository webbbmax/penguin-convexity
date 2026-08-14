#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import c2_1_enrichment as enrichment
from c2_1_db import initialize_database, open_pipeline_db
from c2_1_resilience import commit_cursor


ROOT = Path(__file__).resolve().parent.parent


class C21UpdateCenterMaintenanceTests(unittest.TestCase):
    def test_source_retry_releases_only_selected_recoverable_cursors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline.db"
            initialize_database(path)
            connection = open_pipeline_db(path)
            commit_cursor(connection, "dexscreener", "base-mainnet", "market", "window", "source_failure", {"candidateIds": [1]})
            commit_cursor(connection, "github", "repo", "official_repository", "window", "source_failure", {"candidateIds": [2]})
            pending = enrichment.prepare_source_retry(connection, "dexscreener")
            self.assertEqual(pending, 1)
            dexscreener = connection.execute("SELECT next_retry_at FROM source_cursors WHERE source_id='dexscreener'").fetchone()[0]
            github = connection.execute("SELECT next_retry_at FROM source_cursors WHERE source_id='github'").fetchone()[0]
            self.assertIsNone(dexscreener)
            self.assertIsNotNone(github)
            connection.close()

    def test_selected_source_runs_only_its_enrichment_stage(self):
        called = []
        with mock.patch.object(enrichment, "collect_market", side_effect=lambda connection, client=None: called.append("market") or {"ok": True}), \
             mock.patch.object(enrichment, "collect_incremental_new_pools", side_effect=AssertionError("unexpected discovery")), \
             mock.patch.object(enrichment, "collect_website_identity", side_effect=AssertionError("unexpected identity")), \
             mock.patch.object(enrichment, "collect_github", side_effect=AssertionError("unexpected github")), \
             mock.patch.object(enrichment, "collect_risk_and_supply", side_effect=AssertionError("unexpected goplus")), \
             mock.patch.object(enrichment, "collect_quotes", side_effect=AssertionError("unexpected quote")):
            result = enrichment.run_enrichment(object(), client=object(), only_source_id="dexscreener")
        self.assertEqual(called, ["market"])
        self.assertEqual(list(result), ["market"])

    def test_screening_scope_does_not_run_second_gate_sources(self):
        called = []
        with mock.patch.object(enrichment, "collect_incremental_new_pools", side_effect=lambda connection, client=None: called.append("discovery") or {"ok": True}), \
             mock.patch.object(enrichment, "collect_market", side_effect=lambda connection, client=None: called.append("market") or {"ok": True}), \
             mock.patch.object(enrichment, "collect_website_identity", side_effect=AssertionError("unexpected identity")), \
             mock.patch.object(enrichment, "collect_github", side_effect=AssertionError("unexpected github")), \
             mock.patch.object(enrichment, "collect_risk_and_supply", side_effect=AssertionError("unexpected goplus")), \
             mock.patch.object(enrichment, "collect_quotes", side_effect=AssertionError("unexpected quote")):
            result = enrichment.run_enrichment(object(), client=object(), job_scope="screening")
        self.assertEqual(called, ["discovery", "market"])
        self.assertEqual(list(result), ["incrementalDiscovery", "market"])

    def test_front_and_update_center_copy_follow_maintenance_contract(self):
        front = (ROOT / "app" / "c2-1-front.js").read_text(encoding="utf-8")
        admin = (ROOT / "app" / "c2-1-admin.js").read_text(encoding="utf-8")
        tracking_html = (ROOT / "app" / "update-center.html").read_text(encoding="utf-8")
        new_token_html = (ROOT / "app" / "new-token-update.html").read_text(encoding="utf-8")
        nav = (ROOT / "app" / "workbench-nav.js").read_text(encoding="utf-8")
        server = (ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts" / "c2_1_runtime.py").read_text(encoding="utf-8")
        pipeline = (ROOT / "scripts" / "c2_1_pipeline.py").read_text(encoding="utf-8")
        c22_runner = (ROOT / "scripts" / "run_c2_2_update.py").read_text(encoding="utf-8")
        c22_admin = (ROOT / "app" / "c2-2-admin.js").read_text(encoding="utf-8")

        self.assertNotIn("function health()", front)
        self.assertNotIn("${health()}", front)
        self.assertIn('"90天新币筛选"', front)
        self.assertIn("90天新币筛选", admin)
        self.assertIn("凸性跟踪更新", tracking_html)
        self.assertNotIn("c2-1-admin.js", tracking_html)
        self.assertNotIn("c2-1-admin-snapshot.js", tracking_html)
        self.assertIn("c2-1.css", tracking_html)
        self.assertIn("90天新币筛选", new_token_html)
        self.assertNotIn("c2-1-admin.js", new_token_html)
        self.assertIn("c2-2-admin.js", new_token_html)
        self.assertNotIn("update-center.js", new_token_html)
        self.assertNotIn("update-center-snapshot.js", new_token_html)
        self.assertNotIn("tracking-task-snapshot.js", new_token_html)
        self.assertIn('label: "更新中心"', nav)
        self.assertIn('["new-token-update.html", "90天新币筛选"]', nav)
        self.assertIn('["update-center.html", "凸性跟踪更新"]', nav)
        self.assertIn('action:"retry_source",sourceId:', admin)
        self.assertIn('data-c21-retry-source', admin)
        self.assertIn('"new-token-update.html":updateCenter', admin)
        self.assertNotIn('"update-center.html":updateCenter', admin)
        self.assertIn('"retry_source"', server)
        self.assertIn('command.extend(["--source-id", source_id])', runtime)
        self.assertIn("立即手动更新新币筛选", c22_admin)
        self.assertIn("实时任务进度", c22_admin)
        self.assertIn("data-c22-source-feedback", c22_admin)
        self.assertIn("sourceFeedback(triggerButton", c22_admin)
        self.assertIn("startJob(button.dataset.c22Job, button.dataset.c22RetrySource, button)", c22_admin)
        self.assertNotIn("C2.2 更新中心", c22_admin)
        self.assertIn("正在重新计算受影响项目", pipeline)
        self.assertIn("index % 100 == 0", pipeline)
        self.assertIn("remainingRecoverableScopes", c22_runner)
        self.assertIn("仍有{remaining}个范围连接失败", c22_runner)
        self.assertIn("当前版本 C2.3", nav)
        self.assertNotIn("当前版本 C2.2", nav)
        self.assertNotIn(".slice(0,12)", c22_admin)


if __name__ == "__main__":
    unittest.main()
