#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import threading
import unittest
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from c2_5_control_plane import C25ControlPlane
from serve_local import QuietHandler


class RouteAndShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((PROJECT_ROOT / "docs" / "C2.5_INHERITANCE_MANIFEST.json").read_text(encoding="utf-8"))

    def test_all_29_inherited_and_11_new_routes_exist(self):
        inherited = [row["path"] for row in self.manifest["routes"]]
        new = [row["path"] for row in self.manifest["newRoutes"]]
        self.assertEqual(len(inherited), 29)
        self.assertEqual(len(new), 11)
        self.assertEqual(self.manifest["approvedDeletionCount"], 0)
        for path in [*inherited, *new]:
            target = PROJECT_ROOT / path
            self.assertTrue(target.is_file(), path)
            self.assertIn("<!doctype html>", target.read_text(encoding="utf-8").lower(), path)

    def test_all_40_routes_return_html_over_http(self):
        handler = partial(QuietHandler, directory=str(PROJECT_ROOT / "app"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for item in [*self.manifest["routes"], *self.manifest["newRoutes"]]:
                name = Path(item["path"]).name
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/{name}", timeout=5) as response:
                    body = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200, name)
                    self.assertIn("企鹅投研", body, name)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_frontend_and_admin_shells_remain_separate(self):
        for name in ("candidate-pool.html", "change-explanations.html", "project-detail.html"):
            source = (PROJECT_ROOT / "app" / name).read_text(encoding="utf-8")
            self.assertNotIn('<script src="workbench-nav.js', source)
            self.assertNotIn("data-c25-control", source)
        for item in self.manifest["newRoutes"]:
            source = (PROJECT_ROOT / item["path"]).read_text(encoding="utf-8")
            self.assertIn("workbench-nav.js", source)

    def test_project_detail_return_context_contract_is_preserved(self):
        source = (PROJECT_ROOT / "app" / "c2-4-front.js").read_text(encoding="utf-8")
        self.assertIn("data-c24-back", source)
        self.assertIn('sessionStorage.setItem("c24-return"', source)
        self.assertIn("savedReturn.url", source)
        self.assertIn("scrollY", source)


class DesignSystemTests(unittest.TestCase):
    def setUp(self):
        self.css = (PROJECT_ROOT / "app" / "c2-5.css").read_text(encoding="utf-8")
        self.js = (PROJECT_ROOT / "app" / "c2-5-admin.js").read_text(encoding="utf-8")

    def test_exact_color_tokens_and_no_unregistered_hex_colors(self):
        expected = {"#0288c8", "#075e90", "#0878b9", "#10283b", "#587386", "#eef6fa", "#ffffff", "#f2f8fb", "#cfe2ec", "#147c5a", "#a85e12", "#b54141"}
        found = {value.lower() for value in re.findall(r"#[0-9a-fA-F]{6}", self.css)}
        self.assertEqual(found, expected)
        self.assertIn('"Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif', self.css)
        self.assertIn("--c25-sidebar-width: 232px", self.css)

    def test_button_progress_feedback_and_reduced_motion_states_exist(self):
        required = [":hover", ":active", ":focus-visible", '[aria-busy="true"]', ":disabled", '[data-result="failed"]', 'data-kind="indeterminate"', 'data-state="partial"', 'data-state="pause_requested"', 'data-state="failed"', "prefers-reduced-motion", "c25-dialog", "c25-toast", "c25-empty", "c25-error"]
        for marker in required:
            self.assertIn(marker, self.css + self.js, marker)
        board = (PROJECT_ROOT / "fixtures" / "c2.5" / "component-state-board.html").read_text(encoding="utf-8")
        for label in ("正常", "悬停", "按下", "键盘焦点", "正在处理", "不可用", "执行失败"):
            self.assertIn(label, board)
        for label in ("瞬时完成", "短时处理中", "已知总量", "总量未知", "暂停请求", "部分成功", "程序失败"):
            self.assertIn(label, board)
        for label in ("通知", "确认高影响操作", "运行频率", "全局错误", "空状态"):
            self.assertIn(label, board)

    def test_brand_and_windows_boundaries_are_present(self):
        nav = (PROJECT_ROOT / "app" / "workbench-nav.js").read_text(encoding="utf-8")
        self.assertIn("企鹅投研-凸性", nav)
        self.assertIn("洞见共识之外的价值", nav)
        self.assertEqual(nav.count("当前版本 C2.5"), 1)
        joined = self.css + self.js + nav
        self.assertNotRegex(joined.lower(), r"sf pro|traffic.?light|macos-window|red-yellow-green")
        self.assertNotIn("contextmenu", self.js)
        self.assertNotIn("preventDefault()", self.js.replace('event.preventDefault();', ''))

    def test_chart_includes_definition_time_filter_and_exact_table(self):
        for marker in ("口径：当前只读样本使用同一输入", "固定历史样本重放", "当前只读样本重放", "数据时间：", "筛选：当前完整输入", "查看精确表格", 'id="ruleExactTable"'):
            self.assertIn(marker, self.js)
        for marker in ("影响无法完整计算", "rule_create_draft", "rule_approve_draft", "rule_rollback_version", "data-c25-rule-impact-blocker"):
            self.assertIn(marker, self.js)

    def test_eight_information_domains_and_version_once(self):
        nav = (PROJECT_ROOT / "app" / "workbench-nav.js").read_text(encoding="utf-8")
        labels = ["管理者总览", "全部任务", "任务详情", "逐链与来源", "规则透明中心", "项目判定解释", "数据快照与交接", "运行记录与审计"]
        for label in labels:
            self.assertIn(f'label: "{label}"', nav)


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.plane = C25ControlPlane(project_root=PROJECT_ROOT, windows_reader=lambda: [])

    def test_manager_task_schema_and_unknown_progress_contract(self):
        payload = self.plane.tasks_payload()
        required = {"taskId", "displayName", "machineNames", "entryKind", "lifecycleClass", "liveState", "stateBasis", "capabilityBoundary", "triggerModes", "schedule", "lastStartedAt", "lastFinishedAt", "nextDueAt", "schedulerNextTriggerAt", "progress", "lastHeartbeatAt", "checkpoint", "chains", "sources", "inputs", "outputs", "upstreamTaskIds", "downstreamTaskIds", "affectedPages", "failure", "controls", "disabledControls", "logs", "auditUrl", "observedAt"}
        for task in payload["tasks"]:
            self.assertTrue(required.issubset(task), task["taskId"])
            self.assertIn(task["progress"]["kind"], {"determinate", "indeterminate", "not_applicable"})
            if task["progress"]["total"] is None:
                self.assertIsNone(task["progress"]["percent"])
        self.assertEqual(len(payload["tasks"]), 41)

    def test_gate0_legacy_and_history_have_no_misleading_controls(self):
        tasks = {row["taskId"]: row for row in self.plane.tasks_payload()["tasks"]}
        self.assertEqual(tasks["gate0.backfill.disabled"]["controls"], [])
        self.assertEqual(tasks["c18.scheduler.legacy"]["controls"], [])
        self.assertEqual(tasks["c21.pipeline.legacy"]["controls"], [])
        history = tasks["candidate.history_backlog"]
        actions = {row["action"] for row in history["controls"]}
        self.assertNotIn("run_now", actions)
        self.assertNotIn("run_from_start", actions)

    def test_status_time_snapshot_time_and_page_read_time_are_separate(self):
        overview = self.plane.control_plane_payload()
        self.assertIn("latestCompleteSuccess", overview)
        self.assertIn("latestBusinessSnapshot", overview)
        self.assertIn("pageReadAt", overview)
        scheduler = overview["windowsScheduler"]
        self.assertIn("nextDueAt", scheduler)
        self.assertIn("schedulerNextTriggerAt", scheduler)

    def test_chain_rules_snapshot_and_audit_contracts(self):
        chains = self.plane.chains_sources_payload()
        self.assertEqual(chains["chainOrder"], ["ethereum-mainnet", "solana-mainnet", "base-mainnet", "arbitrum-mainnet", "bnb-mainnet", "robinhood-mainnet"])
        self.assertTrue(all(row["status"] in {"success", "no_data", "quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure"} for row in chains["rows"]))
        rules = self.plane.rules_payload()
        self.assertEqual([row["status"] for row in rules["history"]], ["frozen_baseline", "active"])
        self.assertEqual(rules["effective"]["reconciledRuleCount"], 18)
        self.assertEqual(rules["effective"]["expectedRuleCount"], 18)
        rule_ids = {row["ruleId"] for row in rules["rules"]}
        self.assertTrue({"public_risk_source_success", "public_no_confirmed_hard_block", "public_no_confirmed_severe_anomaly"}.issubset(rule_ids))
        self.assertTrue(rules["replay"]["unionMatchesPerRule"])
        calculation = rules["replay"]["impactCalculation"]
        has_gap = calculation["complete"] is not True
        self.assertTrue(calculation["verificationRequired"])
        self.assertEqual(calculation["approvalBlocked"], has_gap)
        if rules["replay"]["inputCount"]:
            supply = next(row for row in rules["rules"] if row["ruleId"] == "strong_path_supply_holder_state")
            self.assertEqual(supply["counts"]["changed"], len(supply["stateChangedAssetIds"]))
            self.assertEqual(supply["executorMismatchAssetIds"], [])
        snapshots = self.plane.snapshots_payload()
        self.assertTrue(snapshots["stateBoundary"]["separate"])
        self.assertFalse(snapshots["managerCompositionWritesBusinessDatabases"])
        audit = self.plane.runs_audit_payload()
        self.assertIn("runs", audit)
        self.assertIn("managementAudit", audit)
        self.assertIn("不参与liveState组合", audit["separation"])

    def test_no_second_scheduler_http_business_channel_or_rwa_runtime_reference(self):
        new_sources = "\n".join((PROJECT_ROOT / path).read_text(encoding="utf-8") for path in ["scripts/c2_5_control.py", "scripts/c2_5_control_plane.py", "scripts/c2_5_rules.py", "app/c2-5-admin.js"])
        self.assertNotIn("requests.post", new_sources)
        self.assertNotIn("urllib.request", new_sources)
        self.assertNotIn("New-ScheduledTask", new_sources)
        self.assertNotIn("Register-ScheduledTask", new_sources)
        self.assertNotRegex(new_sources, r"[\\/]企鹅投研[\\/]RWA[\\/]")
        desktop_sources = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in (PROJECT_ROOT / "desktop-host").rglob("*.cs"))
        self.assertNotIn("/api/c2.5/", desktop_sources)
        fixture_server = (PROJECT_ROOT / "scripts" / "c2_5_visual_fixture_server.py").read_text(encoding="utf-8")
        self.assertIn("fixture_read_only", fixture_server)
        self.assertNotIn("C25ControlService", fixture_server)
        formal_probe = (PROJECT_ROOT / "scripts" / "c2_5_formal_readonly_probe.py").read_text(encoding="utf-8")
        self.assertIn("mode=ro", formal_probe)
        self.assertIn("PRAGMA query_only=ON", formal_probe)
        product_probe = (PROJECT_ROOT / "scripts" / "c2_5_formal_product_probe.py").read_text(encoding="utf-8")
        self.assertIn("C25ControlPlane", product_probe)
        self.assertNotIn("C25ControlService", product_probe)
        self.assertIn("direct_strong_path_executor_truth", product_probe)
        self.assertIn("executorStateDigest", product_probe)
        self.assertNotIn("--candidate-rebuild", product_probe)
        self.assertNotIn("candidate_rebuilt_tracking_sample", product_probe)
        self.assertIn("impact_calculation_is_j05_ready", product_probe)
        self.assertIn('rules.get("status") == "ready"', product_probe)
        self.assertNotIn("calculation_is_honest", product_probe)
        product_server = (PROJECT_ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
        self.assertIn("_resolve_candidate_product_state", product_server)
        self.assertIn("sealed_candidate_product_state", product_server)


if __name__ == "__main__":
    unittest.main(verbosity=2)
