#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from c2_5_control import C25ControlService, ControlError
from c2_5_control_plane import C25ControlPlane, compose_authoritative_job_state, progress_payload
from c2_4_rules import evaluate_public_baseline, evaluate_strong_paths, normal_exit_decision
from c2_5_rule_governance import RuleGovernanceStore
from c2_5_rules import build_dual_replay_evidence, build_rule_transparency, reconcile_rule_values, replay_governed_rules, replay_rules, validate_active_override


def read_fixture(name: str) -> dict:
    return json.loads((PROJECT_ROOT / "fixtures" / "c2.5" / name).read_text(encoding="utf-8"))


class TaskStateTests(unittest.TestCase):
    def test_live_running_requires_process_lock_and_fresh_heartbeat(self):
        now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        raw = {"state": "running", "lastHeartbeatAt": "2026-08-14T11:59:30Z"}
        state, basis = compose_authoritative_job_state(raw, lock={"exists": True, "pid": 4242, "pidLive": True}, pause_requested=False, now=now)
        self.assertEqual(state, "running")
        self.assertEqual({row["kind"] for row in basis}, {"status_file", "process", "lock", "heartbeat"})
        stale, _ = compose_authoritative_job_state(raw, lock={"exists": False, "pid": 4242, "pidLive": False}, pause_requested=False, now=now)
        self.assertEqual(stale, "stale")

    def test_pause_request_is_not_safe_pause_until_checkpoint(self):
        now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        running, _ = compose_authoritative_job_state({"state": "running", "lastHeartbeatAt": "2026-08-14T11:59:30Z"}, lock={"exists": True, "pid": 4242, "pidLive": True}, pause_requested=True, now=now)
        paused, _ = compose_authoritative_job_state({"state": "paused", "checkpoint": {"id": "p-1"}}, lock={"exists": False, "pid": None, "pidLive": False}, pause_requested=True, now=now)
        self.assertEqual(running, "pause_requested")
        self.assertEqual(paused, "safe_paused")

    def test_progress_never_fakes_unknown_total_or_real_zero(self):
        unknown = progress_payload({"completed": 25, "total": None, "stage": "scan"})
        self.assertEqual(unknown["kind"], "indeterminate")
        self.assertIsNone(unknown["total"])
        self.assertIsNone(unknown["percent"])
        real_zero = progress_payload({"completed": 0, "total": 0})
        self.assertEqual(real_zero["kind"], "not_applicable")
        self.assertIsNone(real_zero["percent"])

    def test_fixture_scenarios_are_distinct(self):
        fixture = read_fixture("task-state-matrix.json")
        ids = [row["id"] for row in fixture["scenarios"]]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("real_zero_result", ids)
        self.assertIn("partial_success_with_scoped_source_failure", ids)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        fixture = read_fixture("windows-scheduler-matrix.json")
        self.plane = C25ControlPlane(project_root=PROJECT_ROOT, windows_reader=lambda: fixture["rows"], clock=lambda: datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc))

    def test_task_ledger_reconciles_exact_sets(self):
        payload = self.plane.tasks_payload()
        reconciliation = payload["reconciliation"]
        self.assertEqual(reconciliation["registeredTopLevelEntryCount"], 20)
        self.assertEqual(reconciliation["registeredUpdateTaskCatalogCount"], 21)
        self.assertEqual(reconciliation["registeredInheritedPostEndpointCount"], 20)
        self.assertEqual(reconciliation["observedInheritedPostEndpointCount"], 20)
        self.assertEqual(reconciliation["missingPostEndpoints"], [])
        self.assertEqual(reconciliation["unregisteredPostEndpoints"], [])
        self.assertEqual(reconciliation["duplicateTaskIds"], [])

    def test_windows_action_role_and_legacy_boundaries(self):
        tasks = {row["taskId"]: row for row in self.plane.tasks_payload()["tasks"]}
        scheduler = tasks["windows.scheduler.primary"]
        self.assertEqual(scheduler["displayName"], "当前统一自动更新调度器")
        self.assertIn("PenguinConvexity-C1.8-Scheduler", scheduler["machineNames"])
        self.assertEqual(scheduler["liveState"], "waiting")
        self.assertEqual(scheduler["schedulerNextTriggerAt"], "2026-08-14T12:15:00Z")
        self.assertIsNone(tasks["c22.screening"]["nextDueAt"])
        hidden = tasks["windows.hidden_runner"]
        self.assertEqual(len(hidden["downstreamResolved"]), 2)
        gate0 = tasks["gate0.backfill.disabled"]
        self.assertEqual(gate0["liveState"], "disabled")
        self.assertEqual(gate0["controls"], [])
        for task_id in ("c18.scheduler.legacy", "c21.pipeline.legacy"):
            self.assertEqual(tasks[task_id]["controls"], [])
            self.assertEqual(tasks[task_id]["lifecycleClass"], "legacy_callable")

    def test_windows_267009_only_marks_scheduler_running(self):
        fixture = read_fixture("windows-scheduler-matrix.json")
        fixture["rows"][0]["lastTaskResult"] = 267009
        plane = C25ControlPlane(project_root=PROJECT_ROOT, windows_reader=lambda: fixture["rows"], clock=self.plane.clock)
        tasks = {row["taskId"]: row for row in plane.tasks_payload()["tasks"]}
        self.assertEqual(tasks["windows.scheduler.primary"]["liveState"], "running")
        self.assertNotEqual(tasks["c22.screening"]["liveState"], "running")

    def test_completed_history_has_no_resume_or_restart_control(self):
        tasks = {row["taskId"]: row for row in self.plane.tasks_payload()["tasks"]}
        history = tasks["candidate.history_backlog"]
        self.assertEqual(history["liveState"], "completed")
        self.assertEqual(history["controls"], [])
        self.assertNotIn("run_now", {row["action"] for row in history["disabledControls"]})
        self.assertEqual(
            {row["action"] for row in history["disabledControls"]},
            {"resume_checkpoint", "retry_partition"},
        )

    def test_daily_candidate_without_checkpoint_has_no_resume_or_retry(self):
        task = {row["taskId"]: row for row in self.plane.tasks_payload()["tasks"]}["candidate.daily_incremental"]
        self.assertEqual(task["controls"], [])
        reasons = {row["action"]: row["reason"] for row in task["disabledControls"]}
        self.assertIn("没有未完成", reasons["resume_checkpoint"])
        self.assertIn("没有可重试", reasons["retry_partition"])

    def test_unknown_authoritative_state_disables_high_impact_controls(self):
        tasks = {row["taskId"]: row for row in self.plane.tasks_payload()["tasks"]}
        for task_id in ("c22.screening", "c22.convexity_tracking"):
            task = tasks[task_id]
            self.assertEqual(task["liveState"], "unknown")
            self.assertEqual(task["controls"], [])
            self.assertTrue(task["disabledControls"])
            self.assertTrue(all("权威状态不可用" in row["reason"] for row in task["disabledControls"]))


def complete_item(asset_id: str, loss: float, risk_state: str = "success", hard_block: bool = False) -> dict:
    return {
        "assetId": asset_id,
        "chainId": "solana-mainnet",
        "contractAddress": f"contract-{asset_id}",
        "pairAddress": f"pair-{asset_id}",
        "tokenSide": "base",
        "t0Status": "verified_in_supported_scope",
        "deepTrackingState": "completed",
        "evaluationWindowId": "window-1",
        "evaluationCompletedAt": "2026-08-14T11:00:00Z",
        "riskSourceState": risk_state,
        "sellQuoteState": "success",
        "sellQuoteLossPct": loss,
        "projectEvidenceQualified": True,
        "projectEvidenceAttributable": True,
        "relationshipClass": "A",
        "confirmedHardBlock": hard_block,
        "ageDays": 10,
        "liquidityUsd": 10000,
        "liquidityDropPct": 0,
        "observedBuys": 10,
        "observedSells": 10,
        "volumeUsd": 1000,
        "transactionCount": 20,
        "volumeLiquidityRatio": 0.1,
        "cohortThresholds": {
            "liquidityP50": 5000,
            "volumeP40": 100,
            "volumeP50": 500,
            "transactionsP50": 10,
            "volumeLiquidityRatioP50": 0.05,
        },
        "publicBaseline": {"ruleVersion": "c2.4-public-baseline-quote-success-trial-v1"},
        "strongPaths": [
            {"pathCode": "trade_demand_formation", "status": "formed", "metrics": {}},
            {"pathCode": "liquidity_exit_quality", "status": "not_formed" if hard_block else "formed", "metrics": {}},
            {"pathCode": "supply_holder_improvement", "status": "unavailable", "metrics": {}},
            {"pathCode": "indexed_pool_activity_vs_supply_adjusted_valuation", "status": "unavailable", "metrics": {}},
        ],
    }


class RuleTransparencyTests(unittest.TestCase):
    def test_current_baseline_override_and_disabled_gates_are_separate(self):
        payload = build_rule_transparency([complete_item("pass", 8), complete_item("added", 18, "no_data")])
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["frozenBaseline"]["hashMatchesFrozen"])
        self.assertTrue(payload["activeOverride"]["active"])
        values = {row["ruleId"]: row for row in payload["rules"]}
        self.assertEqual(values["public_sell_quote_loss"]["baselineValue"], 15)
        self.assertEqual(values["strong_path_sell_quote_loss"]["baselineValue"], 10)
        self.assertEqual(values["severe_immediate_exit_loss"]["baselineValue"], 20)
        self.assertEqual(sum(row["effectiveValue"] == "disabled_as_gate" for row in payload["rules"]), 6)

    def test_same_input_replay_recomputes_asset_id_sets(self):
        replay = replay_rules([complete_item("pass", 8), complete_item("added", 18, "no_data"), complete_item("blocked", 5, hard_block=True)], override_active=True)
        self.assertEqual(replay["baselinePassedCount"], 1)
        self.assertEqual(replay["effectivePassedCount"], 2)
        self.assertEqual(replay["addedAssetIds"], ["added"])
        self.assertEqual(replay["removedAssetIds"], [])
        self.assertTrue(replay["sameInput"])
        self.assertTrue(replay["assetIdSetRecomputed"])

    def test_current_tracking_projection_is_expanded_before_replay(self):
        compact = {
            "assetId": "compact-current",
            "chainId": "base-mainnet",
            "tokenAddress": "token-current",
            "poolId": "pool-current",
            "assetDirection": "base",
            "t0Status": "verified_in_supported_scope",
            "deepTrackingState": "completed",
            "evaluationWindowId": "window-current",
            "evaluationCompletedAt": "2026-08-15T00:00:00Z",
            "relationshipClass": "A",
            "sellQuoteState": "success",
            "sellQuoteLossPct": 18,
            "projectEvidenceState": "success",
            "sourceStates": {"risk": "no_data"},
            "firstGateChecks": [{"code": "no_confirmed_trade_block", "passed": True}],
            "publicBaseline": {"checks": [{"code": "project_evidence", "passed": True}]},
            "severeAnomaly": False,
        }
        replay = replay_rules([compact], override_active=True)
        self.assertEqual(replay["effectivePassedCount"], 1)
        self.assertEqual(replay["addedAssetIds"], ["compact-current"])

    def test_fixed_historical_and_current_readonly_samples_replay_separately(self):
        payload = build_rule_transparency(
            [complete_item("current-pass", 8), complete_item("current-added", 18, "no_data")],
            current_source={
                "sourcePath": "app/c2-4-tracking-snapshot.js",
                "sourceSha256": "current-readonly-sha",
                "snapshotId": "tracking-current-1",
                "dataAsOf": "2026-08-14T11:00:00Z",
                "readOnly": True,
            },
        )
        replays = payload["replaySets"]
        self.assertEqual(replays["fixedHistorical"]["sampleKind"], "fixed_historical")
        self.assertEqual(replays["currentReadOnly"]["sampleKind"], "current_read_only")
        self.assertEqual(replays["fixedHistorical"]["replay"]["inputCount"], 3)
        self.assertEqual(replays["currentReadOnly"]["replay"]["inputCount"], 2)
        self.assertEqual(replays["fixedHistorical"]["replay"]["sourcePassedCount"], 1)
        self.assertEqual(replays["fixedHistorical"]["replay"]["targetPassedCount"], 2)
        self.assertEqual(replays["fixedHistorical"]["replay"]["affectedAssetIds"], ["asset-override-added"])
        self.assertEqual(replays["currentReadOnly"]["replay"]["affectedAssetIds"], ["current-added"])
        self.assertEqual(replays["affectedAssetIds"], ["current-added"])
        self.assertEqual(replays["fixedHistoricalAffectedAssetIds"], ["asset-override-added"])
        self.assertNotEqual(replays["fixedHistorical"]["sourcePath"], replays["currentReadOnly"]["sourcePath"])
        self.assertEqual(replays["currentReadOnly"]["snapshotId"], "tracking-current-1")

    def test_governed_union_covers_strong_path_immediate_exit_and_risk_only_counterexamples(self):
        fixture = read_fixture("rule-governance-union-counterexamples.json")
        for case in fixture["cases"]:
            item = {**fixture["baseItem"], **case["overrides"], "assetId": case["assetId"]}
            replay = replay_governed_rules(
                [item],
                source_version=fixture["sourceVersion"],
                target_version=fixture["targetVersion"],
            )
            changed = sorted(row["ruleId"] for row in replay["rules"] if row["stateChangedAssetIds"])
            self.assertEqual(changed, sorted(case["expectedChangedRuleIds"]), case["id"])
            self.assertEqual(replay["affectedAssetIds"], [case["assetId"]], case["id"])
            self.assertTrue(replay["unionMatchesPerRule"], case["id"])
            if case.get("expectedSourceState"):
                supply = next(row for row in replay["rules"] if row["ruleId"] == "strong_path_supply_holder_state")
                self.assertEqual(supply["rows"][0]["sourceState"], case["expectedSourceState"])
                self.assertEqual(supply["rows"][0]["targetState"], case["expectedTargetState"])

    def test_incomplete_current_snapshot_blocks_rule_governance_instead_of_reporting_no_change(self):
        item = {
            **complete_item("incomplete-supply-projection", 8),
            "supplyHistoryState": "success",
            "supplyUnitScaleStable": None,
            "top10ShareChangePercentagePoints": -3,
        }
        evidence = build_dual_replay_evidence(
            [item],
            source_version="c2.4-rules-v1",
            target_version="c2.4-public-baseline-quote-success-trial-v1",
            current_source={"sourcePath": "fixture-current", "snapshotId": "incomplete-current", "readOnly": True},
        )
        supply = next(row for row in evidence["currentReadOnly"]["ruleImpacts"] if row["ruleId"] == "strong_path_supply_holder_state")
        self.assertEqual(supply["stateChangedAssetIds"], ["incomplete-supply-projection"])
        self.assertEqual(supply["executorMismatchAssetIds"], ["incomplete-supply-projection"])
        self.assertTrue(evidence["approvalBlocked"])
        self.assertFalse(evidence["impactCalculation"]["complete"])
        self.assertIn("影响无法完整计算", evidence["impactCalculation"]["reason"])

    def test_overall_affected_set_is_exact_union_of_every_registered_rule(self):
        items = [
            {**complete_item("union-strong", 12), "projectEvidenceQualified": False, "projectEvidenceAttributable": False},
            {**complete_item("union-risk", 8, "no_data"), "projectEvidenceQualified": False, "projectEvidenceAttributable": False, "sellQuoteState": "no_data", "sellQuoteLossPct": None},
        ]
        replay = replay_governed_rules(
            items,
            source_version="c2.4-rules-v1",
            target_version="c2.4-public-baseline-quote-success-trial-v1",
        )
        per_rule_union = sorted({asset_id for row in replay["rules"] for asset_id in row["stateChangedAssetIds"]})
        self.assertEqual(replay["affectedAssetIds"], per_rule_union)
        self.assertEqual(replay["affectedAssetIds"], ["union-risk", "union-strong"])

    def test_unapproved_expired_or_damaged_override_is_rejected(self):
        now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        base = {"status": "user_authorized_active_trial", "authorizedAt": "2026-08-13", "frozenBaseline": {"ruleConfigSha256": "775f9fad44e5f0db3b036e797643104a5ff9f075afbc4e1c16835606c8a88988"}}
        damaged = validate_active_override(base, trial_sha256="bad", rule_sha256="bad", now=now)
        self.assertFalse(damaged["active"])
        expired_payload = {**base, "effectiveUntil": "2026-08-01T00:00:00Z"}
        expired = validate_active_override(expired_payload, trial_sha256="7f6ccc9e35ab6ba7b5212911116facd9698489c0f7d0f27b9dbcf16dc0c7e202", rule_sha256="775f9fad44e5f0db3b036e797643104a5ff9f075afbc4e1c16835606c8a88988", now=now)
        self.assertFalse(expired["active"])
        unapproved = validate_active_override({**base, "status": "draft", "authorizedAt": None}, trial_sha256="7f6ccc9e35ab6ba7b5212911116facd9698489c0f7d0f27b9dbcf16dc0c7e202", rule_sha256="775f9fad44e5f0db3b036e797643104a5ff9f075afbc4e1c16835606c8a88988", now=now)
        self.assertFalse(unapproved["active"])

    def test_counterexample_only_changes_the_rule_whose_input_changed(self):
        fixture = read_fixture("rule-replay-counterexamples.json")
        case = fixture["cases"][0]
        payload = build_rule_transparency([case["item"]])
        changed = sorted(row["ruleId"] for row in payload["rules"] if row["counts"]["changed"])
        expected = set(case["expectedChangedRuleIds"])
        self.assertEqual(changed, sorted(expected))
        self.assertEqual(
            {row["ruleId"]: row["counts"]["changed"] for row in payload["rules"] if row["ruleId"] not in expected},
            {row["ruleId"]: 0 for row in payload["rules"] if row["ruleId"] not in expected},
        )

    def test_code_reconciliation_is_per_rule_and_detects_one_field_mismatch(self):
        payload = build_rule_transparency([complete_item("pass", 8)])
        self.assertTrue(payload["effective"]["reconciledWithCode"])
        self.assertTrue(all(row["codeReconciliation"]["matched"] for row in payload["rules"]))
        code_manifest = {row["ruleId"]: row["effectiveValue"] for row in payload["rules"]}
        code_manifest["public_sell_quote_loss"] = "damaged-code-value"
        reconciled = reconcile_rule_values(payload["rules"], code_manifest)
        failures = [row["ruleId"] for row in reconciled if not row["codeReconciliation"]["matched"]]
        self.assertEqual(failures, ["public_sell_quote_loss"])

    def test_rule_approval_and_explicit_version_rollback_preserve_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = RuleGovernanceStore(
                Path(temp),
                rule_path=PROJECT_ROOT / "docs" / "C2.4_RULE_CONFIG.json",
                trial_path=PROJECT_ROOT / "docs" / "C2.4_RULE_RELAXATION_TRIAL_20260813.json",
                clock=lambda: datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
            )
            evidence = build_dual_replay_evidence(
                [complete_item("current-added", 18, "success")],
                source_version="c2.4-public-baseline-quote-success-trial-v1",
                target_version="c2.4-rules-v1",
                current_source={"sourcePath": "fixture-current", "snapshotId": "snapshot-before", "readOnly": True},
            )
            draft = store.create_draft(
                target_version="c2.4-rules-v1",
                reason="恢复冻结基线",
                scope="全部现役规则消费端",
                end_condition="验证完成后保持",
                replay_evidence=evidence,
            )
            activation = store.approve_draft(draft["draftId"])
            self.assertEqual(activation["activeVersion"], "c2.4-rules-v1")
            strict = evaluate_public_baseline(
                complete_item("loss-18", 18, "success"),
                selector_path=store.selector_path,
            )
            self.assertFalse(strict["passed"])
            strict_path = evaluate_strong_paths(
                {
                    **complete_item("strict-path", 18, "success"),
                    "ageDays": 10,
                    "liquidityUsd": 20000,
                    "liquidityDropPct": 10,
                },
                selector_path=store.selector_path,
            )[1]
            self.assertEqual(strict_path["status"], "not_formed")
            self.assertTrue(normal_exit_decision({"sellQuoteLossPct": 20}, "window-1", False, selector_path=store.selector_path)["immediate"])
            rollback = store.rollback_version(
                "c2.4-public-baseline-quote-success-trial-v1",
                reason="回滚到明确历史版本",
                replay_evidence=evidence,
            )
            relaxed = evaluate_public_baseline(
                complete_item("loss-18", 18, "success"),
                selector_path=store.selector_path,
            )
            self.assertTrue(relaxed["passed"])
            self.assertEqual(evaluate_strong_paths({**complete_item("trial-path", 99), "ageDays": 10, "liquidityUsd": 0, "liquidityDropPct": 100}, selector_path=store.selector_path)[1]["status"], "formed")
            self.assertEqual(rollback["rollbackOfVersion"], "c2.4-rules-v1")
            state = store.state()
            self.assertEqual(state["activeVersion"], "c2.4-public-baseline-quote-success-trial-v1")
            self.assertEqual(len(state["history"]), 2)
            self.assertEqual(state["drafts"][0]["replayEvidence"], evidence)
            self.assertEqual(state["history"][-1]["runLinkStatus"], "pending_next_legal_run")
            link = store.link_next_legal_run(
                run_id="c22-20260814T120100Z-convexity_tracking",
                rule_version="c2.4-public-baseline-quote-success-trial-v1",
                snapshots=[
                    {"snapshotId": "tracking-build-1", "path": "app/c2-4-tracking-snapshot.js"},
                    {"snapshotId": "front-build-1", "path": "app/c2-4-front-snapshot.js"},
                    {"snapshotId": "admin-build-1", "path": "app/c2-4-admin-snapshot.js"},
                ],
            )
            self.assertEqual(link["linkedRunId"], "c22-20260814T120100Z-convexity_tracking")
            linked = next(row for row in store.state()["history"] if row["activationId"] == rollback["activationId"])
            self.assertEqual(linked["runLinkStatus"], "linked")
            self.assertEqual(linked["linkedRunId"], "c22-20260814T120100Z-convexity_tracking")
            self.assertEqual(linked["linkedSnapshotIds"], ["tracking-build-1", "front-build-1", "admin-build-1"])


class FakePlane:
    def __init__(self, root: Path):
        self.runtime_root = root / "runtime"
        self.app_root = root / "app"
        self.version = "v1"
        self.live_state = "waiting"
        self.sources = ["tracking-market"]
        self.partitions = [{"partition_id": "part-failed-1", "state": "failed"}]
        self.interval = 24
        self.paused = False

    def task_payload(self, task_id: str) -> dict:
        action_sets = {
            "c22.screening": ["run_now", "safe_pause", "pause_future_cycles", "resume_future_cycles", "set_interval", "retry_registered_source"],
            "c22.convexity_tracking": ["run_now", "safe_pause", "pause_future_cycles", "resume_future_cycles", "set_interval", "retry_registered_source"],
            "candidate.daily_incremental": ["resume_checkpoint", "safe_pause", "cancel_pause_request", "retry_partition"],
            "maintenance.temp_artifact_retention": ["run_retention_sweep"],
        }
        if task_id not in action_sets:
            return {"status": "not_found"}
        controls = [{"action": action, "label": action, "requiresPreview": True} for action in action_sets[task_id]]
        if self.live_state in {"running", "stale"}:
            controls = [row for row in controls if row["action"] != "run_now"]
        return {
            "status": "ready",
            "task": {
                "taskId": task_id,
                "liveState": self.live_state,
                "stateVersion": self.version,
                "schedule": {"mode": "automatic", "intervalHours": self.interval, "paused": self.paused},
                "checkpoint": {"cursor": "c-1"},
                "inputs": ["fixture-input"],
                "outputs": ["fixture-output"],
                "affectedPages": ["fixture.html"],
                "controls": controls,
                "disabledControls": [{"action": "run_now", "reason": "已有作业正在运行。"}] if self.live_state == "running" else [],
                "sources": self.sources,
                "partitions": self.partitions,
            },
        }

    def rules_payload(self) -> dict:
        return build_rule_transparency([complete_item("governance-sample", 18, "success")])

    def rule_change_preview(self, target_version: str) -> dict:
        source_version = (
            "c2.4-public-baseline-quote-success-trial-v1"
            if target_version == "c2.4-rules-v1"
            else "c2.4-rules-v1"
        )
        evidence = build_dual_replay_evidence(
            [complete_item("governance-sample", 18, "success")],
            source_version=source_version,
            target_version=target_version,
            current_source={"sourcePath": "fixture-current", "snapshotId": "tracking-current", "readOnly": True},
        )
        evidence["affectedTaskIds"] = ["c22.screening", "c22.convexity_tracking"]
        evidence["affectedSnapshots"] = [
            {"snapshotId": "tracking-current", "path": "app/c2-4-tracking-snapshot.js"},
            {"snapshotId": "front-current", "path": "app/c2-4-front-snapshot.js"},
            {"snapshotId": "admin-current", "path": "app/c2-4-admin-snapshot.js"},
        ]
        return evidence


class ControlSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plane = FakePlane(self.root)
        self.calls = []
        self.now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

        def record(name, result=None):
            def adapter(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return result or {"status": "accepted", "runId": f"run-{name}"}
            return adapter

        def update_config(job_code, updates):
            self.calls.append(("update_job_config", (job_code, updates), {}))
            if "intervalHours" in updates:
                self.plane.interval = int(updates["intervalHours"])
            if "paused" in updates:
                self.plane.paused = bool(updates["paused"])
            return {"jobs": {job_code: {**updates}}}

        self.adapters = {
            "launch_job": record("launch_job", {"status": "launched", "runId": "run-1"}),
            "pause_job": record("pause_job", True),
            "pause_screening_pipeline": record("pause_screening_pipeline", True),
            "update_job_config": update_config,
            "launch_candidate": record("launch_candidate"),
            "pause_candidate": record("pause_candidate", True),
            "resume_history": record("resume_history"),
            "retry_partition": record("retry_partition", {"status": "launched"}),
            "retention_sweep": record("retention_sweep", {"status": "completed"}),
        }
        self.service = C25ControlService(self.plane, runtime_root=self.root / "runtime" / "c2.5", clock=lambda: self.now, adapters=self.adapters)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, suffix: str, action: str = "run_now", parameters=None, task_id="c22.convexity_tracking"):
        return {"requestId": f"request-{suffix}-0001", "taskId": task_id, "action": action, "parameters": parameters or {}}

    def test_preview_execute_and_duplicate_request_are_idempotent(self):
        request = self.request("success")
        _, preview = self.service.preview(request)
        self.assertNotIn(preview["confirmationToken"], (self.service.audit_path.read_text(encoding="utf-8")))
        code, result = self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(code, 202)
        self.assertTrue(result["backendAccepted"])
        self.assertEqual(len(self.calls), 1)
        duplicate_code, duplicate = self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(duplicate_code, 409)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["status"], "duplicate_rejected")
        self.assertEqual(len(self.calls), 1)
        audit_rows = [json.loads(row) for row in self.service.audit_path.read_text(encoding="utf-8").splitlines()]
        required = {"auditId", "actor", "origin", "requestedAt", "requestId", "taskId", "action", "before", "afterRequested", "impactPreview", "confirmationId", "backendAccepted", "linkedRunId", "finalResult", "rollbackOf"}
        self.assertTrue(all(required.issubset(row) for row in audit_rows))
        self.assertNotIn(preview["confirmationToken"], json.dumps(audit_rows, ensure_ascii=False))

    def test_expired_token_and_state_drift_are_rejected(self):
        request = self.request("expired")
        _, preview = self.service.preview(request)
        self.now += timedelta(minutes=6)
        with self.assertRaisesRegex(ControlError, "过期"):
            self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.now -= timedelta(minutes=6)
        request = self.request("drift")
        _, preview = self.service.preview(request)
        self.plane.version = "v2"
        with self.assertRaisesRegex(ControlError, "状态已改变"):
            self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})

    def test_running_lock_frequency_source_and_partition_guards(self):
        self.plane.live_state = "running"
        with self.assertRaisesRegex(ControlError, "正在运行"):
            self.service.preview(self.request("lock"))
        self.plane.live_state = "waiting"
        with self.assertRaisesRegex(ControlError, "只能是"):
            self.service.preview(self.request("frequency", action="set_interval", parameters={"intervalHours": 2}))
        with self.assertRaisesRegex(ControlError, "权威登记范围"):
            self.service.preview(self.request("source", action="retry_registered_source", parameters={"sourceId": "wrong-source"}))
        with self.assertRaisesRegex(ControlError, "分片不存在"):
            self.service.preview(self.request("partition", action="retry_partition", parameters={"partitionId": "missing"}, task_id="candidate.daily_incremental"))
        audit_rows = [json.loads(row) for row in self.service.audit_path.read_text(encoding="utf-8").splitlines()]
        rejected = [row for row in audit_rows if row["finalResult"] == "preview_rejected"]
        self.assertEqual(len(rejected), 4)
        self.assertTrue(all(row["backendAccepted"] is False for row in rejected))

    def test_rejected_execute_is_audited_without_token_body(self):
        request = self.request("invalid-token")
        token = "sensitive-confirmation-token"
        with self.assertRaisesRegex(ControlError, "无效"):
            self.service.execute({"requestId": request["requestId"], "confirmationToken": token})
        audit = self.service.audit_path.read_text(encoding="utf-8")
        self.assertIn("execute_rejected", audit)
        self.assertNotIn(token, audit)

    def test_failure_is_audited_and_restart_does_not_repeat(self):
        def fail(*_args, **_kwargs):
            self.calls.append(("failed", (), {}))
            raise RuntimeError("fixture failure")

        self.adapters["launch_job"] = fail
        request = self.request("failure")
        _, preview = self.service.preview(request)
        code, result = self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(code, 409)
        self.assertFalse(result["backendAccepted"])
        audit = self.service.audit_path.read_text(encoding="utf-8")
        self.assertIn("fixture failure", audit)
        restarted = C25ControlService(self.plane, runtime_root=self.root / "runtime" / "c2.5", clock=lambda: self.now, adapters=self.adapters)
        duplicate_code, duplicate = restarted.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(duplicate_code, 409)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(self.calls), 1)

    def test_audit_intent_failure_blocks_backend_and_preserves_confirmation(self):
        request = self.request("audit-intent-failure")
        _, preview = self.service.preview(request)
        original_audit = self.service._audit

        def fail_intent(payload):
            if payload.get("auditStage") == "intent":
                raise OSError("audit unavailable")
            return original_audit(payload)

        self.service._audit = fail_intent
        with self.assertRaisesRegex(OSError, "audit unavailable"):
            self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(self.calls, [])
        ledger = json.loads(self.service.ledger_path.read_text(encoding="utf-8"))
        confirmation_id = hashlib.sha256(preview["confirmationToken"].encode("utf-8")).hexdigest()[:16]
        self.assertIsNone(ledger["confirmations"][confirmation_id]["usedAt"])
        self.assertEqual(ledger["requests"][request["requestId"]]["status"], "previewed")

    def test_final_audit_failure_returns_unknown_and_never_reexecutes(self):
        request = self.request("audit-result-failure")
        _, preview = self.service.preview(request)
        original_audit = self.service._audit

        def fail_result(payload):
            if payload.get("auditStage") == "result":
                raise OSError("result audit unavailable")
            return original_audit(payload)

        self.service._audit = fail_result
        code, result = self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(code, 500)
        self.assertEqual(result["status"], "executing_unknown")
        self.assertFalse(result["auditComplete"])
        self.assertEqual(len(self.calls), 1)
        duplicate_code, duplicate = self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(duplicate_code, 409)
        self.assertEqual(duplicate["status"], "duplicate_rejected")
        self.assertEqual(len(self.calls), 1)

    def test_pause_scope_and_allowed_frequency_use_one_existing_adapter(self):
        pause_request = self.request("pause-scope", action="pause_future_cycles", task_id="c22.screening")
        _, pause_preview = self.service.preview(pause_request)
        code, pause_result = self.service.execute({"requestId": pause_request["requestId"], "confirmationToken": pause_preview["confirmationToken"]})
        self.assertEqual(code, 202)
        self.assertTrue(pause_result["backendAccepted"])
        self.assertEqual(self.calls[-1], ("update_job_config", ("screening", {"paused": True}), {}))

        frequency_request = self.request("frequency-three", action="set_interval", parameters={"intervalHours": 3})
        _, frequency_preview = self.service.preview(frequency_request)
        code, frequency_result = self.service.execute({"requestId": frequency_request["requestId"], "confirmationToken": frequency_preview["confirmationToken"]})
        self.assertEqual(code, 202)
        self.assertEqual(self.calls[-1], ("update_job_config", ("convexity_tracking", {"mode": "automatic", "intervalHours": 3}), {}))
        self.assertEqual(frequency_result["authoritativeReadback"][0]["schedule"]["intervalHours"], 3)

    def test_frequency_and_future_pause_rollback_restore_explicit_audit_before_value(self):
        frequency_request = self.request("frequency-for-rollback", action="set_interval", parameters={"intervalHours": 3})
        _, preview = self.service.preview(frequency_request)
        _, changed = self.service.execute({"requestId": frequency_request["requestId"], "confirmationToken": preview["confirmationToken"]})
        rollback_request = self.request("frequency-rollback", action="rollback_control_change", parameters={"auditId": changed["auditId"]})
        _, rollback_preview = self.service.preview(rollback_request)
        self.assertEqual(rollback_preview["impactPreview"][0]["afterRequested"]["intervalHours"], 24)
        _, rolled_back = self.service.execute({"requestId": rollback_request["requestId"], "confirmationToken": rollback_preview["confirmationToken"]})
        self.assertEqual(rolled_back["authoritativeReadback"][0]["schedule"]["intervalHours"], 24)
        self.assertEqual(self.plane.interval, 24)

        pause_request = self.request("pause-for-rollback", action="pause_future_cycles", task_id="c22.screening")
        _, preview = self.service.preview(pause_request)
        _, paused = self.service.execute({"requestId": pause_request["requestId"], "confirmationToken": preview["confirmationToken"]})
        rollback_request = self.request("pause-rollback", action="rollback_control_change", parameters={"auditId": paused["auditId"]}, task_id="c22.screening")
        _, rollback_preview = self.service.preview(rollback_request)
        self.assertFalse(rollback_preview["impactPreview"][0]["afterRequested"]["paused"])
        _, rolled_back = self.service.execute({"requestId": rollback_request["requestId"], "confirmationToken": rollback_preview["confirmationToken"]})
        self.assertFalse(rolled_back["authoritativeReadback"][0]["schedule"]["paused"])
        self.assertFalse(self.plane.paused)
        result_rows = [json.loads(row) for row in self.service.audit_path.read_text(encoding="utf-8").splitlines() if '"auditStage":"result"' in row]
        self.assertIn(changed["auditId"], {row["rollbackOf"] for row in result_rows})
        self.assertIn(paused["auditId"], {row["rollbackOf"] for row in result_rows})
        with self.assertRaisesRegex(ControlError, "已经回滚") as duplicate:
            self.service.preview(self.request("frequency-rollback-again", action="rollback_control_change", parameters={"auditId": changed["auditId"]}))
        self.assertEqual(duplicate.exception.code, "rollback_already_applied")

    def test_screening_pause_failure_is_not_reported_as_success_and_is_compensated(self):
        self.plane.live_state = "running"

        def fail_associated(_paused):
            self.calls.append(("pause_screening_pipeline", (_paused,), {}))
            raise RuntimeError("associated pause failed")

        self.adapters["pause_screening_pipeline"] = fail_associated
        request = self.request("pause-associated-failure", action="safe_pause", task_id="c22.screening")
        _, preview = self.service.preview(request)
        code, result = self.service.execute({"requestId": request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(code, 409)
        self.assertFalse(result["backendAccepted"])
        self.assertIn("associated pause failed", result["error"])
        pause_calls = [call for call in self.calls if call[0] == "pause_job"]
        self.assertEqual([call[1][0] for call in pause_calls], [True, False])

    def test_rule_draft_approval_and_rollback_use_protected_two_stage_control(self):
        draft_request = self.request(
            "rule-draft",
            action="rule_create_draft",
            task_id="rule.governance",
            parameters={
                "targetVersion": "c2.4-rules-v1",
                "reason": "恢复冻结基线",
                "scope": "全部现役规则消费端",
                "endCondition": "验证完成后保持",
            },
        )
        _, preview = self.service.preview(draft_request)
        impact = preview["impactPreview"][0]
        self.assertIn("governance-sample", impact["affectedAssetIds"])
        self.assertEqual(set(impact["affectedTaskIds"]), {"c22.screening", "c22.convexity_tracking"})
        self.assertEqual([row["snapshotId"] for row in impact["affectedSnapshots"]], ["tracking-current", "front-current", "admin-current"])
        self.assertEqual(impact["replayEvidence"]["fixedHistorical"]["sampleKind"], "fixed_historical")
        self.assertEqual(impact["replayEvidence"]["currentReadOnly"]["sampleKind"], "current_read_only")
        self.assertIn("replayEvidence", impact["afterRequested"])
        _, created = self.service.execute({"requestId": draft_request["requestId"], "confirmationToken": preview["confirmationToken"]})
        draft_id = created["results"][0]["backend"]["draftId"]
        approve_request = self.request("rule-approve", action="rule_approve_draft", task_id="rule.governance", parameters={"draftId": draft_id})
        _, preview = self.service.preview(approve_request)
        _, approved = self.service.execute({"requestId": approve_request["requestId"], "confirmationToken": preview["confirmationToken"]})
        self.assertEqual(approved["authoritativeReadback"][0]["activeVersion"], "c2.4-rules-v1")

        rollback_request = self.request(
            "rule-rollback",
            action="rule_rollback_version",
            task_id="rule.governance",
            parameters={"targetVersion": "c2.4-public-baseline-quote-success-trial-v1", "reason": "恢复已批准试行"},
        )
        _, preview = self.service.preview(rollback_request)
        rollback_impact = preview["impactPreview"][0]
        self.assertEqual(set(rollback_impact["affectedTaskIds"]), {"c22.screening", "c22.convexity_tracking"})
        self.assertEqual(len(rollback_impact["affectedSnapshots"]), 3)
        self.assertIn("replayEvidence", rollback_impact["afterRequested"])
        _, rolled_back = self.service.execute({"requestId": rollback_request["requestId"], "confirmationToken": preview["confirmationToken"]})
        state = rolled_back["authoritativeReadback"][0]
        self.assertEqual(state["activeVersion"], "c2.4-public-baseline-quote-success-trial-v1")
        self.assertEqual(len(state["history"]), 2)
        result_audits = [json.loads(row) for row in self.service.audit_path.read_text(encoding="utf-8").splitlines() if '"auditStage":"result"' in row]
        self.assertEqual(result_audits[-1]["rollbackOf"], approved["authoritativeReadback"][0]["activeActivationId"])

        with self.assertRaisesRegex(ControlError, "不同于当前有效版本") as no_op:
            self.service.preview(
                self.request(
                    "rule-no-op-draft",
                    action="rule_create_draft",
                    task_id="rule.governance",
                    parameters={
                        "targetVersion": "c2.4-public-baseline-quote-success-trial-v1",
                        "reason": "不应接受的无效草案",
                        "scope": "全部现役规则消费端",
                        "endCondition": "不适用",
                    },
                )
            )
        self.assertEqual(no_op.exception.code, "rule_draft_target_rejected")

    def test_rule_governance_is_blocked_when_executor_impact_is_incomplete(self):
        original_preview = self.plane.rule_change_preview

        def incomplete_preview(target_version):
            evidence = original_preview(target_version)
            calculation = {
                **evidence["impactCalculation"],
                "status": "incomplete",
                "complete": False,
                "approvalBlocked": True,
                "reason": "当前快照字段不足，影响无法完整计算。",
                "executorMismatchAssetIds": ["incomplete-supply-projection"],
            }
            evidence["impactCalculation"] = calculation
            evidence["approvalBlocked"] = True
            evidence["currentReadOnly"]["replay"]["impactCalculation"] = calculation
            return evidence

        self.plane.rule_change_preview = incomplete_preview
        request = self.request(
            "rule-impact-incomplete",
            action="rule_create_draft",
            task_id="rule.governance",
            parameters={
                "targetVersion": "c2.4-rules-v1",
                "reason": "不应建立",
                "scope": "全部现役规则消费端",
                "endCondition": "不适用",
            },
        )
        with self.assertRaisesRegex(ControlError, "影响无法完整计算") as blocked:
            self.service.preview(request)
        self.assertEqual(blocked.exception.code, "rule_impact_incomplete")

    def test_batch_preview_is_explicit_and_ordered(self):
        request = self.request("batch", action="batch", task_id="batch", parameters={"items": [
            {"taskId": "c22.convexity_tracking", "action": "set_interval", "parameters": {"intervalHours": 3}},
            {"taskId": "maintenance.temp_artifact_retention", "action": "run_retention_sweep", "parameters": {}},
        ]})
        _, preview = self.service.preview(request)
        self.assertEqual([row["order"] for row in preview["proposed"]], [1, 2])
        self.assertEqual([row["taskId"] for row in preview["proposed"]], ["c22.convexity_tracking", "maintenance.temp_artifact_retention"])


class DataBoundaryTests(unittest.TestCase):
    def test_database_integrity_checks_are_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "fixture.db"
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parent(id))")
            connection.commit()
            connection.close()
            before = hashlib.sha256(db_path.read_bytes()).hexdigest()
            payload = C25ControlPlane(project_root=PROJECT_ROOT, windows_reader=lambda: [])._database_integrity(db_path)
            after = hashlib.sha256(db_path.read_bytes()).hexdigest()
            self.assertEqual(payload["quickCheck"], "ok")
            self.assertEqual(payload["foreignKeyViolations"], 0)
            self.assertTrue(payload["readOnly"])
            self.assertEqual(before, after)

    def test_inheritance_counts_and_single_channel_boundary(self):
        manifest = json.loads((PROJECT_ROOT / "docs" / "C2.5_INHERITANCE_MANIFEST.json").read_text(encoding="utf-8"))
        fixture = read_fixture("inheritance-registry-matrix.json")
        self.assertEqual(len(manifest["routes"]), fixture["inheritedRouteCount"])
        self.assertEqual(len(manifest["newRoutes"]), fixture["newManagerRouteCount"])
        self.assertEqual(manifest["approvedDeletionCount"], 0)
        self.assertEqual(fixture["singleChannel"]["windowsSchedulerCount"], 1)
        self.assertEqual(fixture["singleChannel"]["csharpBusinessChannelCount"], 0)
        self.assertFalse(fixture["singleChannel"]["managerAuditIsTaskStateAuthority"])

    def test_missing_snapshot_is_stale_and_preserves_previous_complete_file(self):
        with tempfile.TemporaryDirectory() as temp:
            plane = C25ControlPlane(project_root=PROJECT_ROOT, windows_reader=lambda: [])
            plane.app_root = Path(temp) / "empty-app"
            plane.data_root = Path(temp) / "empty-data"
            payload = plane.snapshots_payload()
            self.assertEqual(len(payload["snapshots"]), 5)
            for row in payload["snapshots"]:
                self.assertFalse(row["complete"])
                self.assertTrue(row["stale"])
                self.assertTrue(row["preservePreviousCompleteSnapshot"])
            self.assertFalse(payload["managerCompositionWritesBusinessDatabases"])

    def test_legacy_running_records_are_marked_legacy_and_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime_root = Path(temp) / "runtime"
            for version in ("c1.8", "c2.1"):
                path = runtime_root / version / "scheduler-state.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"lastStatus": "running", "lastStartedAt": "2026-08-01T00:00:00Z"}), encoding="utf-8")
            plane = C25ControlPlane(project_root=PROJECT_ROOT, windows_reader=lambda: [])
            plane.runtime_root = runtime_root
            rows = [row for row in plane.runs_audit_payload()["runs"] if row["legacy"]]
            self.assertEqual({row["sourceVersion"] for row in rows}, {"C1.8", "C2.1"})
            self.assertTrue(all(row["stale"] for row in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
