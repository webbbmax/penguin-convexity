#!/usr/bin/env python3
"""C1.9 frozen experience acceptance test.

The checks are intentionally read-only for product data.  A temporary status
file is used for progress telemetry so the test cannot start a real update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from c1_9_progress import (
    STAGES,
    begin_progress,
    finish_progress,
    heartbeat_progress,
    update_progress,
)
from run_update_task import safe_progress_call
from update_tasks import TASK_DEFINITIONS
from update_watchdog import load_update_status


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DESKTOP = ROOT / "desktop"
DB_PATH = ROOT / "data" / "convexity.db"
ROLE_PATH = ROOT / "docs" / "C1.9_ROUTE_ROLES.json"
LOCK_PATH = ROOT / "docs" / "C1.9_REQUIREMENTS_LOCK.json"
PHASE_PATH = ROOT / "docs" / "C1.9_PHASE.json"


def without_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def without_scripts(text: str) -> str:
    return re.sub(r"<script\b.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)


def test_lock_and_roles() -> None:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    for item in lock["documents"]:
        path = ROOT / item["path"]
        payload = path.read_bytes()
        assert len(payload) == item["bytes"], item["path"]
        assert hashlib.sha256(payload).hexdigest() == item["sha256"], item["path"]

    phase = json.loads(PHASE_PATH.read_text(encoding="utf-8"))
    assert phase["release"] == "C1.9"
    assert phase["requirementsStatus"] == "frozen"
    assert phase["phase"] in {
        "requirements_frozen_waiting_luna",
        "luna_self_test_complete_waiting_sol",
        "sol_final_acceptance_complete_released",
    }

    roles = json.loads(ROLE_PATH.read_text(encoding="utf-8"))["roles"]
    active = {path.name for path in APP.glob("*.html")}
    active.add("desktop/index.html")
    active.discard("decision-quality.html")
    assert set(roles) == active
    assert set(roles.values()) == {"front", "context-detail", "host-only", "admin"}
    assert roles["candidate-pool.html"] == "front"
    assert roles["change-explanations.html"] == "front"
    assert roles["project-detail.html"] == "context-detail"
    assert roles["desktop/index.html"] == "host-only"
    assert sum(value == "front" for value in roles.values()) == 2


def test_front_boundary() -> None:
    forbidden = (
        "任务ID",
        "信源适配",
        "Watcher",
        "孤儿证据",
        "游标",
        "调度配置",
        "运行日志",
        "模型验收",
        "规则回放",
    )
    version_pattern = re.compile(r"\b(?:M1\.0|C1\.\d+|C2\.\d+)\b")
    for name in ("candidate-pool.html", "change-explanations.html", "project-detail.html"):
        html = (APP / name).read_text(encoding="utf-8")
        visible = without_scripts(without_comments(html))
        assert "c1-9.css" in visible
        assert visible.count('data-front-mode=') == 4, name
        assert "workbench-nav.js" not in visible, name
        assert not version_pattern.search(visible), name
        assert not any(token in visible for token in forbidden), name
    detail = (APP / "project-detail.html").read_text(encoding="utf-8")
    assert 'data-page-mode="detail"' in detail
    assert detail.count("c19-detail-back") == 1


def test_admin_boundary() -> None:
    navigation = (APP / "workbench-nav.js").read_text(encoding="utf-8")
    group_block = re.search(r"const groups = \[(.*?)\n  \];", navigation, flags=re.DOTALL)
    assert group_block and len(re.findall(r"\n\s*label:", group_block.group(1))) == 7
    expected_version = "C2.1"
    assert navigation.count(f"当前版本 {expected_version}") == 1
    assert len(re.findall(r"当前版本 C\d+\.\d+", navigation)) == 1
    assert "c19-workbench-sidebar" in navigation
    assert "c1-9.css?v=c19" in navigation
    for name in ("workbench.html", "update-center.html", "action-gaps.html"):
        html = without_comments((APP / name).read_text(encoding="utf-8"))
        assert "workbench-nav.js" in html, name
    host = without_comments((DESKTOP / "index.html").read_text(encoding="utf-8"))
    assert 'src="/candidate-pool.html"' in host
    assert "desktop-host" in host
    assert "primary-nav" in host  # hidden migration scaffold only
    assert not re.search(r"\b(?:M1\.0|C1\.\d+|C2\.\d+)\b", without_scripts(host))


def test_progress_contract() -> None:
    assert len(STAGES) == 7
    stage_sets = [components for _, _, components in STAGES]
    full_components = TASK_DEFINITIONS["full_refresh"]["components"]
    assert len(full_components) == 18
    assert all(sum(component in stage for stage in stage_sets) == 1 for component in full_components)
    assert sum("page_snapshot_rebuild" in stage for stage in stage_sets) == 1
    progress_py = (ROOT / "scripts" / "c1_9_progress.py").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "run_update_task.py").read_text(encoding="utf-8")
    assert "progressStageIndex" in progress_py
    assert "progressHeartbeatAt" in progress_py
    assert "progress_callback" in runner
    for name in ("workbench.html", "update-center.html"):
        html = (APP / name).read_text(encoding="utf-8")
        assert 'id="c19ProgressPanel"' in html
        assert "c1-9-progress.js" in html or name == "update-center.html"

    with TemporaryDirectory() as temporary:
        status_path = Path(temporary) / "update-runtime-status.json"
        begin_progress("full_refresh", "full", 18, status_path=status_path)
        running = load_update_status(status_path)
        assert running["state"] == "running" and running["active"] is True
        assert running["taskId"] == "full_refresh" and running["finishedAt"] is None
        assert running["progressState"] == "running"
        assert running["progressStageIndex"] == 1
        assert running["progressStageTotal"] == 7
        assert running["stageIndex"] == 1
        assert running["totalItems"] == 18
        assert running["waitingCount"] == 18
        update_progress("machine_conclusion", "conclusion", 16, 18, status_path=status_path)
        middle = load_update_status(status_path)
        assert middle["progressStageIndex"] == 6
        assert middle["progressCompletedComponents"] == 15
        assert middle["completedItems"] == 15
        assert middle["lastHeartbeatAt"]
        update_progress("formal_market_exit", "market", 17, 18, status_path=status_path)
        non_regressing = load_update_status(status_path)
        assert non_regressing["progressStageIndex"] == 6
        assert non_regressing["stageIndex"] == 6
        assert non_regressing["progressCompletedComponents"] == 16
        heartbeat_progress(status_path=status_path)
        heartbeat = load_update_status(status_path)
        assert heartbeat["progressStageIndex"] == 6
        assert heartbeat["progressCompletedComponents"] == 16
        assert heartbeat["progressHeartbeatAt"]
        finish_progress(
            "partial_success",
            "partial",
            status_path=status_path,
            success_count=16,
            failed_count=2,
            total_items=18,
        )
        finished = load_update_status(status_path)
        assert finished["progressState"] == "partial_success"
        assert finished["progressStageTotal"] == 7
        assert finished["successCount"] == 16
        assert finished["failedCount"] == 2
        assert finished["waitingCount"] == 0
        assert finished["state"] == "partial_success" and finished["active"] is False
        assert finished["recoveryAvailable"] is True

    def broken_telemetry():
        raise OSError("isolated telemetry failure")

    assert safe_progress_call(broken_telemetry) is None


def test_database_and_boundaries() -> None:
    connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= 585
        assert connection.execute("SELECT COUNT(*) FROM candidate_cases").fetchone()[0] >= 585
    finally:
        connection.close()

    forbidden_refs = ("convexity-system", "project-radar-site", "F:\\codex项目\\区块链")
    for root in (APP, DESKTOP, ROOT / "scripts"):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".html", ".js", ".css", ".py", ".ps1"}:
                continue
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            assert not any(ref.lower() in text for ref in forbidden_refs), path


def test_syntax() -> None:
    for relative in (
        "app/front-c19.js",
        "app/c1-9-progress.js",
        "app/workbench-nav.js",
        "app/update-center.js",
        "desktop/shell.js",
    ):
        result = subprocess.run(["node", "--check", str(ROOT / relative)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_screenshots() -> None:
    manifest_path = ROOT / "docs" / "C1.9_SCREENSHOT_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["release"] == "C1.9"
    entries = manifest["screenshots"]
    assert len(entries) >= 10
    for entry in entries:
        path = ROOT / manifest["directory"] / entry["file"]
        assert path.is_file() and path.stat().st_size > 0, entry["file"]
    for relative in (
        "scripts/c1_9_progress.py",
        "scripts/run_update_task.py",
        "scripts/refresh_candidate_pool.py",
        "scripts/serve_local.py",
    ):
        result = subprocess.run(["python", "-m", "py_compile", str(ROOT / relative)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_live_routes() -> None:
    roles = json.loads(ROLE_PATH.read_text(encoding="utf-8"))["roles"]
    for route in roles:
        if route == "desktop/index.html":
            url = "http://127.0.0.1:8766/desktop/index.html"
        else:
            url = f"http://127.0.0.1:8766/{route}"
        with urllib.request.urlopen(url, timeout=10) as response:
            assert response.status == 200, route


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    test_lock_and_roles()
    test_front_boundary()
    test_admin_boundary()
    test_progress_contract()
    test_database_and_boundaries()
    test_syntax()
    test_screenshots()
    if args.live:
        test_live_routes()
    print("C1.9 experience acceptance passed" + (" (static + live routes)" if args.live else " (static)"))


if __name__ == "__main__":
    main()
