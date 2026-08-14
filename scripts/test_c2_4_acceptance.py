#!/usr/bin/env python3
"""Risk-layered C2.4 application and optional local-service acceptance."""

from __future__ import annotations

import json
import os
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
MANIFEST = json.loads((ROOT / "docs" / "C2.4_INHERITANCE_MANIFEST.json").read_text(encoding="utf-8"))
ROUTES = [item["path"] for item in MANIFEST["routes"]]
BASE_URL = os.environ.get("C24_BASE_URL", "").rstrip("/")


class C24StaticApplicationTests(unittest.TestCase):
    def test_all_29_inherited_routes_still_exist(self):
        self.assertEqual(len(ROUTES), 29)
        for relative in ROUTES:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_all_admin_routes_are_taken_over_by_the_current_workbench(self):
        front = {
            "app/candidate-pool.html",
            "app/change-explanations.html",
            "app/project-detail.html",
        }
        for relative in set(ROUTES) - front:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("workbench-nav.js", source, relative)

    def test_public_pages_read_only_the_c24_public_snapshot(self):
        for name in ("candidate-pool.html", "change-explanations.html", "project-detail.html"):
            source = (APP / name).read_text(encoding="utf-8")
            self.assertIn("c2-4-front-snapshot.js", source, name)
            self.assertIn("c2-4-front.js", source, name)
            active = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("<!--"))
            self.assertNotIn('<script src="c2-2-front', active, name)

    def test_important_change_chain_counts_use_change_records_not_public_projects(self):
        source = (APP / "c2-4-front.js").read_text(encoding="utf-8")
        self.assertIn("const changeUniverse = data.changes.filter", source)
        self.assertIn("chainTabs(chain, true, changeUniverse)", source)

    def test_project_detail_translates_internal_cohort_codes_for_ordinary_users(self):
        source = (APP / "c2-4-front.js").read_text(encoding="utf-8")
        self.assertIn("const cohortLabels", source)
        self.assertIn("cohortLabels[item.cohortScope]", source)
        self.assertIn("冻结历史后备值（当前可比样本不足）", source)

    def test_current_navigation_has_seven_groups_and_one_visible_version(self):
        source = (APP / "workbench-nav.js").read_text(encoding="utf-8")
        for label in (
            "工作台概览",
            "更新中心",
            "候选与项目",
            "证据与来源",
            "持续跟踪",
            "判断规则与质量",
            "系统设置与日志",
        ):
            self.assertIn(f'label: "{label}"', source)
        self.assertEqual(source.count("当前版本 C2.4"), 1)
        self.assertIn('window.addEventListener("load", loadC24', source)

    def test_two_update_pages_use_one_runtime_without_a_second_scheduler(self):
        server = (ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
        self.assertIn('"/api/c2.4/status"', server)
        self.assertIn('"/api/c2.4/run"', server)
        self.assertIn('"/api/c2.4/scheduler"', server)
        self.assertIn('"/api/c2.4/pause-current"', server)
        self.assertIn("launch_c22_hidden", server)
        self.assertNotIn("launch_c24_hidden", server)
        admin = (APP / "c2-4-admin.js").read_text(encoding="utf-8")
        self.assertIn('jobPage("screening")', admin)
        self.assertIn('jobPage("convexity_tracking")', admin)
        for control in (
            "立即更新",
            "继续上次未完成",
            "在安全点暂停当前任务",
            "暂停自动新周期",
            "自动频率",
            "只重试可恢复范围",
        ):
            self.assertIn(control, admin)
        self.assertIn("PenguinConvexity-C1.8-Scheduler", (ROOT / "scripts" / "c2_2_runtime.py").read_text(encoding="utf-8"))
        runner = (ROOT / "scripts" / "run_c2_2_update.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(runner.count("reconcile_c24_history()"), 3)

    def test_screening_page_keeps_historical_backbone_progress_and_plain_source_actions(self):
        admin = (APP / "c2-4-admin.js").read_text(encoding="utf-8")
        navigation = (APP / "workbench-nav.js").read_text(encoding="utf-8")
        self.assertIn('["new-token-update.html", "90 天候选"]', navigation)
        for marker in (
            "历史底座扫描",
            "candidateProduction",
            "剩余历史候选",
            "/api/c2.2/candidate-production/run",
            "/api/c2.2/candidate-production/pause",
            "项目网站与身份链路",
            "只重试可恢复范围",
        ):
            self.assertIn(marker, admin)

    def test_update_pages_render_reconciled_funnels_and_one_shared_real_progress_component(self):
        admin = (APP / "c2-4-admin.js").read_text(encoding="utf-8")
        styles = (APP / "c2-4.css").read_text(encoding="utf-8")
        for marker in (
            "renderFunnel",
            "漏斗之外：等待与未通过",
            "本轮未通过",
            "等待处理",
            "progressMarkup",
            "updateProgress",
            'role="progressbar"',
            'progressMarkup("c24Job"',
            'progressMarkup("c24Backbone"',
            "c24-progress-track",
        ):
            self.assertIn(marker, admin)
        for marker in (
            ".c24-funnel-panel",
            ".c24-funnel-stage",
            ".c24-funnel-rule",
            ".c24-progress-track",
            ".c24-progress-fill",
            ".c24-progress-breakdown",
        ):
            self.assertIn(marker, styles)
        self.assertNotIn("c24-job-progress", admin)
        self.assertNotIn(".c24-job-progress", styles)
        self.assertNotIn("c24-backbone-progress-track", admin)
        self.assertNotIn(".c24-backbone-progress-track", styles)
        self.assertNotIn("gradient", styles)

    def test_chart_suitable_front_and_admin_surfaces_use_semantic_visual_summaries(self):
        admin = (APP / "c2-4-admin.js").read_text(encoding="utf-8")
        front = (APP / "c2-4-front.js").read_text(encoding="utf-8")
        styles = (APP / "c2-4.css").read_text(encoding="utf-8")
        for marker in (
            "horizontalBars",
            "stackedBars",
            "stageFlow",
            "sourceStatusChart",
            "evidenceStatusChart",
            "trackingStateChart",
            "factorWeightChart",
            "databaseChart",
        ):
            self.assertIn(marker, admin)
        for marker in (
            "compositionChart",
            "chainDistributionChart",
            "methodFlow",
            "pathStatusMatrix",
            "sellLossGauge",
            "factorScoreChart",
        ):
            self.assertIn(marker, front)
        for marker in (
            ".c24-chart-panel",
            ".c24-bar-chart",
            ".c24-stacked-chart",
            ".c24-stage-flow",
            ".c24-status-matrix",
            ".c24-loss-gauge",
        ):
            self.assertIn(marker, styles)

    def test_c24_takeover_stops_legacy_admin_pollers(self):
        navigation = (APP / "workbench-nav.js").read_text(encoding="utf-8")
        self.assertIn("PENGUIN_CONVEXITY_C24_TAKEOVER_PENDING", navigation)
        for name in ("c2-1-admin.js", "c2-2-admin.js", "c1-9-progress.js"):
            source = (APP / name).read_text(encoding="utf-8")
            self.assertIn("PENGUIN_CONVEXITY_C24", source, name)

    def test_completed_single_source_message_is_labeled_as_historical(self):
        source = (APP / "c2-4-admin.js").read_text(encoding="utf-8")
        self.assertIn("上次单项重试的结果", source)
        self.assertIn("当前状态请以下方“需要处理”为准", source)

    def test_c24_does_not_add_mobile_or_csharp_business_logic(self):
        self.assertNotIn("@media", (APP / "c2-4.css").read_text(encoding="utf-8"))
        host_sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "desktop-host" / "PenguinConvexity.Desktop").glob("*.cs"))
        for marker in ("convexity.db", "c2.1-pipeline.db", "Microsoft.Data.Sqlite"):
            self.assertNotIn(marker, host_sources)


@unittest.skipUnless(BASE_URL, "set C24_BASE_URL for local-service acceptance")
class C24LocalServiceTests(unittest.TestCase):
    def get(self, path: str):
        with urllib.request.urlopen(f"{BASE_URL}/{path.lstrip('/')}", timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    def test_health_and_read_only_status_are_c24(self):
        status, source = self.get("api/health")
        self.assertEqual(status, 200)
        health = json.loads(source)
        self.assertEqual(health["product"], "企鹅投研-凸性")
        self.assertEqual(health["experienceRelease"], "C2.4")
        self.assertEqual(health["startupRebuild"]["state"], "success")
        status, source = self.get("api/c2.4/status")
        self.assertEqual(status, 200)
        runtime = json.loads(source)
        self.assertEqual(set(runtime["jobs"]), {"screening", "convexity_tracking"})
        self.assertEqual(runtime["singleWindowsTask"], "PenguinConvexity-C1.8-Scheduler")
        self.assertEqual(runtime["normalDesktopConsoleWindows"], 0)

    def test_all_29_routes_are_served(self):
        for relative in ROUTES:
            status, source = self.get(relative.removeprefix("app/"))
            self.assertEqual(status, 200, relative)
            self.assertIn("<!doctype html", source.lower(), relative)


if __name__ == "__main__":
    unittest.main()
