#!/usr/bin/env python3
"""Checks for the frozen C1.8 experience and scheduler contract."""

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
sys.path.insert(0, str(ROOT / "scripts"))

from c1_8_runtime import load_config, scheduler_status  # noqa: E402
from run_c1_8_scheduler import due_task_count, run_once  # noqa: E402


def load_snapshot(path, prefix):
    text = path.read_text(encoding="utf-8").strip()
    assert text.startswith(prefix) and text.endswith(";")
    return json.loads(text[len(prefix):-1])


def test_frozen_lock():
    lock = json.loads((ROOT / "docs" / "C1.8_REQUIREMENTS_LOCK.json").read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        path = ROOT / relative.replace("/", "\\")
        content = path.read_bytes()
        assert len(content) == expected["bytes"]
        assert hashlib.sha256(content).hexdigest() == expected["sha256"]


def test_development_baseline():
    manifest = json.loads((ROOT / "docs" / "C1.8_BASELINE_MANIFEST.json").read_text(encoding="utf-8"))
    project_baseline = ROOT / manifest["projectFilesBaseline"]["source"]
    database_baseline = ROOT / manifest["databaseBaseline"]["source"]
    assert hashlib.sha256(project_baseline.read_bytes()).hexdigest() == manifest["projectFilesBaseline"]["sha256"]
    assert hashlib.sha256(database_baseline.read_bytes()).hexdigest() == manifest["databaseBaseline"]["sha256"]
    snapshot_root = ROOT / manifest["activeSnapshotBaseline"]["root"]
    files = manifest["activeSnapshotBaseline"]["files"]
    assert len(files) == manifest["activeSnapshotBaseline"]["fileCount"] == 27
    for item in files:
        path = snapshot_root / item["path"]
        assert path.stat().st_size == item["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    assert (ROOT / manifest["olderBaselines"]["c1_7"]).is_dir()
    assert (ROOT / manifest["olderBaselines"]["m1_0"]).is_dir()


def test_home_contract():
    opportunity = load_snapshot(APP / "opportunity-center-snapshot.js", "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = ")
    tracking = load_snapshot(APP / "tracking-task-snapshot.js", "window.PENGUIN_CONVEXITY_TRACKING_TASKS = ")
    c18 = opportunity["c18"]
    assert c18["homeLimits"] == {"nearAction": 5, "importantChanges": 5, "systemWork": 5, "needsUser": 5}
    assert len(c18["nearAction"]) <= 5
    assert c18["pagination"]["pageSize"] == 20
    assert tracking["c18"]["pageSize"] == 20
    assert all(item["c18Owner"] for item in tracking["tasks"])
    assert all(item["c18NextAction"] for item in tracking["tasks"])
    allowed = {"ordinary", "extreme", "observe", "reflexive", "invalidated"}
    assert set(opportunity["actionCounts"]) == allowed
    hubble = [item for item in c18["blockerDetails"] if item.get("projectName") == "Hubble"]
    assert hubble
    blocker = hubble[0]
    assert re.search(r"流动性约 [\d,]+ 美元", blocker["fact"])
    assert re.search(r"24小时成交额约 [\d,]+ 美元", blocker["fact"])
    assert "模拟退出2万美元滑点100%" in blocker["fact"]
    assert "8%" in blocker["threshold"]
    assert "无法可靠退出" in blocker["impact"]
    assert all(item.get("projectName") or item.get("isGroup") for item in c18["blockerDetails"])


def test_page_contract():
    html = (APP / "candidate-pool.html").read_text(encoding="utf-8")
    script = (APP / "candidate-pool.js").read_text(encoding="utf-8")
    gaps = (APP / "action-gaps.html").read_text(encoding="utf-8")
    assert "C1.8 决策首页" in html
    for marker in ("c18NearActionList", "c18ImportantChanges", "c18SchedulerSummary", "c18NeedsUser", "opportunityPageMeta"):
        assert marker in html
    assert "行动条件与缺口" in html and "为什么还不能行动" in html
    assert "c18-home-mode" in script
    assert ".slice(" not in script
    assert "action-gaps.js" in gaps
    assert "普通用户不需要手动完成" in gaps
    styles = (APP / "styles.css").read_text(encoding="utf-8")
    assert ".c18-library-mode #opportunityDirectory" in styles
    assert ".c18-library-mode .c18-decision-home" in styles
    changes = (APP / "change-explanations.html").read_text(encoding="utf-8")
    for marker in ("changeTimeFilter", "changeImpactFilter", "changeActionImpactFilter"):
        assert marker in changes
    catalyst_html = (APP / "catalyst-paths.html").read_text(encoding="utf-8")
    catalyst_js = (APP / "catalyst-paths.js").read_text(encoding="utf-8")
    for marker in ("catalystPathPreviousPage", "catalystPathNextPage", "tracking-task-snapshot.js"):
        assert marker in catalyst_html
    for marker in ("pageSize = 20", "下次自动检查"):
        assert marker in catalyst_js


def test_scheduler_isolated():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = root / "config.json"
        state = root / "state.json"
        lock = root / "scheduler.lock"
        config.write_text(json.dumps({
            "enabled": True,
            "paused": False,
            "dailyTime": "08:00",
            "timezone": "Asia/Shanghai",
            "hourlyDueCheck": True,
        }), encoding="utf-8")
        result = run_once(
            ROOT / "data" / "convexity.db",
            dry_run=True,
            force=True,
            config_path=config,
            state_path=state,
            lock_path=lock,
        )
        assert result["status"] == "queued"
        assert result["taskId"] == "full_refresh"
        status = scheduler_status(config_path=config, state_path=state)
        assert status["version"] == "C1.8"
        assert status["timezone"] == "Asia/Shanghai"
        assert status["status"] in {"queued", "not_due"}


def test_scheduler_due_selection_and_statuses():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        config = root / "config.json"
        state = root / "state.json"
        lock = root / "scheduler.lock"
        now = datetime(2027, 1, 2, 1, 0, tzinfo=timezone.utc)
        connection = sqlite3.connect(db_path)
        connection.execute(
            "CREATE TABLE candidate_cases (case_id TEXT, action_stage TEXT, next_review_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO candidate_cases VALUES (?, ?, ?)",
            [
                ("due", "只观察", "2027-01-02T00:00:00Z"),
                ("later", "只观察", "2027-01-03T00:00:00Z"),
                ("closed", "已失去凸性", "2027-01-01T00:00:00Z"),
            ],
        )
        connection.commit()
        connection.close()
        config.write_text(json.dumps({
            "enabled": True,
            "paused": False,
            "dailyTime": "08:00",
            "timezone": "Asia/Shanghai",
            "hourlyDueCheck": True,
        }), encoding="utf-8")
        state.write_text(json.dumps({
            "status": "not_due",
            "lastDailyDate": None,
            "nextDailyRunAt": "2027-01-03T00:00:00Z",
            "lastHourlyAt": "2027-01-01T23:00:00Z",
        }), encoding="utf-8")
        assert due_task_count(db_path, now) == 1
        selected = run_once(
            db_path,
            now=now,
            dry_run=True,
            config_path=config,
            state_path=state,
            lock_path=lock,
        )
        assert selected["kind"] == "hourly"
        assert selected["dueCount"] == 1

        reasons = set()
        for scheduler_state in (
            "not_due", "queued", "running", "no_change", "completed",
            "partial", "failed", "paused", "quota_delayed",
        ):
            config_payload = json.loads(config.read_text(encoding="utf-8"))
            config_payload["paused"] = scheduler_state == "paused"
            config.write_text(json.dumps(config_payload), encoding="utf-8")
            state.write_text(json.dumps({
                "status": scheduler_state,
                "lastError": "测试失败原因" if scheduler_state == "failed" else "",
                "nextDailyRunAt": "2026-01-01T00:00:00Z",
                "nextHourlyCheckAt": "2026-01-01T00:00:00Z",
            }), encoding="utf-8")
            shown = scheduler_status(
                now=now,
                config_path=config,
                state_path=state,
                due_count=0,
            )
            assert shown["status"] == scheduler_state
            assert shown["owner"] and shown["reason"] and shown["nextAction"]
            assert datetime.fromisoformat(shown["nextDailyRunAt"].replace("Z", "+00:00")) > now
            assert datetime.fromisoformat(shown["nextHourlyCheckAt"].replace("Z", "+00:00")) > now
            reasons.add(shown["reason"])
        assert len(reasons) >= 7


def test_scheduler_install_contract():
    script = (ROOT / "scripts" / "install-c1.8-scheduler.ps1").read_text(encoding="utf-8")
    for marker in (
        "--dry-run", "schtasks.exe", "PenguinConvexity-C1.8-Scheduler",
        "DisallowStartIfOnBatteries", "StartWhenAvailable", "IgnoreNew", "PT55M",
        "run-c1-8-scheduler-hidden.vbs", "wscript.exe",
    ):
        assert marker in script
    hidden_runner = (ROOT / "scripts" / "run-c1-8-scheduler-hidden.vbs").read_text(encoding="utf-8")
    assert 'shell.Run(command, 0, True)' in hidden_runner
    assert 'run_c1_8_scheduler.py' in hidden_runner


def test_python_syntax():
    targets = [
        APP / "candidate-pool.js",
        APP / "change-explanations.js",
        APP / "update-center.js",
        APP / "action-gaps.js",
    ]
    for target in targets:
        result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def main():
    test_frozen_lock()
    test_development_baseline()
    test_home_contract()
    test_page_contract()
    test_scheduler_isolated()
    test_scheduler_due_selection_and_statuses()
    test_scheduler_install_contract()
    test_python_syntax()
    print("C1.8 experience and isolated scheduler self-tests passed.")


if __name__ == "__main__":
    main()
