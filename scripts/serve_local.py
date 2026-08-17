#!/usr/bin/env python3
import argparse
import json
import sqlite3
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from init_db import DEFAULT_DB_PATH
from build_project_master_pool import build_master_pool_snapshot, write_master_pool_snapshot
from build_project_detail_snapshot import (
    build_project_detail_snapshot,
    write_project_detail_snapshot,
)
from build_scan_center_snapshot import (
    build_scan_center_snapshot,
    write_scan_center_snapshot,
)
from build_manual_review_snapshot import (
    build_manual_review_snapshot,
    write_manual_review_snapshot,
)
from build_update_center_snapshot import rebuild_update_snapshots
from high_value_sources import rebuild_high_value_snapshot
from source_discovery_attribution import rebuild_source_discovery_snapshot
from build_discovery_funnel_snapshot import rebuild_discovery_funnel_snapshot
from build_evidence_ledger_snapshot import rebuild_evidence_ledger_snapshot
from source_adapter import rebuild_source_adapter_snapshot
from build_opportunity_center_snapshot import rebuild_opportunity_center_snapshot
from build_research_route_snapshot import rebuild_research_route_snapshot
from build_tracking_tasks_snapshot import rebuild_tracking_tasks_snapshot
from build_change_explanations_snapshot import rebuild_change_explanations_snapshot
from build_decision_quality_snapshots import (
    STATUS_OUTPUT as C20_SNAPSHOT_STATUS_PATH,
    build_decision_quality_snapshots,
)
from build_model_acceptance_snapshot import rebuild_model_acceptance_snapshot
from data_backbone import build_data_backbone_snapshot, write_data_backbone_snapshot
from build_four_layer_screening_snapshot import (
    DEFAULT_CANDIDATE_PATH as FOUR_LAYER_CANDIDATE_PATH,
    DEFAULT_GOLD_EXPECTED_PATH,
    DEFAULT_GOLD_INPUT_PATH,
    DEFAULT_OUTPUT_PATH as FOUR_LAYER_OUTPUT_PATH,
    build_snapshot as build_four_layer_snapshot,
    write_snapshot as write_four_layer_snapshot,
)
from gate_screening import save_state as save_gate_screening_state
from discover_network_tokens import build_discovery_snapshot, write_discovery_snapshot
from manage_manual_review import execute_manual_review_action
from manage_tracking_decision_review import execute_tracking_decision_review
from refresh_candidate_pool import refresh_candidates
from run_manual_network_scan import run_manual_scan
from run_update_task import run_update_task
from run_real_case_calibration import build_calibration_snapshot, write_snapshot as write_calibration_snapshot
from run_rule_replay import build_replay_snapshot, write_replay_snapshot
from sync_thread_candidates import build_pool_snapshot, load_fixture, write_pool_snapshot
from update_tasks import TASK_DEFINITIONS
from update_watchdog import (
    DEFAULT_STATUS_PATH,
    default_update_status,
    load_update_status,
    recover_interrupted_updates,
    save_update_status,
    status_with_watchdog,
)
from c1_8_runtime import (
    DEFAULT_CONFIG_PATH as C18_CONFIG_PATH,
    DEFAULT_STATE_PATH as C18_STATE_PATH,
    load_config as load_c18_config,
    scheduler_status as c18_scheduler_status,
    windows_task_installed,
    update_scheduler_config,
)
from build_tracking_tasks_snapshot import load_js_payload as load_snapshot_payload
from c2_1_runtime import (
    RETRYABLE_SOURCE_STAGES,
    launch_hidden as launch_c21_hidden,
    request_pause_current as request_c21_pause,
    status_payload as c21_status_payload,
    update_config as update_c21_config,
)
from c2_2_runtime import (
    JOB_CODES as C22_JOB_CODES,
    launch_hidden as launch_c22_hidden,
    request_pause_current as request_c22_pause,
    status_payload as c22_status_payload,
    update_config as update_c22_config,
)
from candidate_production_runtime import (
    launch_hidden as launch_candidate_production,
    request_pause as request_candidate_production_pause,
    retry_partition as retry_candidate_partition,
)
from c2_5_control import C25ControlService, ControlError
from c2_5_control_plane import C25ControlPlane, _resolve_candidate_product_state


PROJECT_ROOT = Path(__file__).resolve().parent.parent
C25_CANDIDATE_PRODUCT_STATE, C25_CANDIDATE_APP_ROOT, C25_CANDIDATE_DATA_ROOT, C25_CANDIDATE_RUNTIME_ROOT = _resolve_candidate_product_state(PROJECT_ROOT)
APP_ROOT = C25_CANDIDATE_APP_ROOT or PROJECT_ROOT / "app"
DESKTOP_ROOT = PROJECT_ROOT / "desktop"
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
CACHE_ROOT = RUNTIME_ROOT / "cache"
LOG_ROOT = RUNTIME_ROOT / "logs"
CONVEXITY_RELEASE = "C1.7"
EXPERIENCE_RELEASE = "C2.4"
MIGRATION_RELEASE = "M1.0"
UPDATE_STATUS_LOCK = threading.Lock()
UPDATE_STATUS = load_update_status()
STARTUP_REBUILD_STATE_LOCK = threading.Lock()
STARTUP_REBUILD_STATE = {
    "state": "pending",
    "startedAt": None,
    "finishedAt": None,
    "error": "",
}
C25_SERVICE_LOCK = threading.Lock()
C25_PLANE = None
C25_CONTROL = None
C21_STARTUP_SNAPSHOTS = (
    (APP_ROOT / "c2-1-front-snapshot.js", "window.PENGUIN_CONVEXITY_C21 = "),
    (APP_ROOT / "c2-1-admin-snapshot.js", "window.PENGUIN_CONVEXITY_C21_ADMIN = "),
)
C22_STARTUP_SNAPSHOTS = (
    (APP_ROOT / "c2-2-front-snapshot.js", "window.PENGUIN_CONVEXITY_C22 = "),
    (APP_ROOT / "c2-2-tracking-snapshot.js", "window.PENGUIN_CONVEXITY_C22_TRACKING = "),
    (APP_ROOT / "c2-2-admin-snapshot.js", "window.PENGUIN_CONVEXITY_C22_ADMIN = "),
)
C24_STARTUP_SNAPSHOTS = (
    (APP_ROOT / "c2-4-candidate-snapshot.js", "window.PENGUIN_CONVEXITY_C24_CANDIDATES = "),
    (APP_ROOT / "c2-4-tracking-snapshot.js", "window.PENGUIN_CONVEXITY_C24_TRACKING = "),
    (APP_ROOT / "c2-4-front-snapshot.js", "window.PENGUIN_CONVEXITY_C24 = "),
    (APP_ROOT / "c2-4-admin-snapshot.js", "window.PENGUIN_CONVEXITY_C24_ADMIN = "),
)


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def open_main_database_readonly():
    database_path = (C25_CANDIDATE_DATA_ROOT / "convexity.db") if C25_CANDIDATE_DATA_ROOT else DEFAULT_DB_PATH
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def get_update_status(status_path=DEFAULT_STATUS_PATH):
    with UPDATE_STATUS_LOCK:
        # Progress telemetry is written by the worker; merge it into the
        # in-process status so a polling browser sees heartbeats immediately.
        persisted = load_update_status(status_path)
        persisted_token = persisted.get("runToken")
        current_token = UPDATE_STATUS.get("runToken")
        # A caller may use an isolated status path in tests or recovery tools.
        # Never let that path (or an older production run) overwrite a newer
        # in-process run.  A matching token is the worker/browser handoff key;
        # when no run is active, an external active status may be adopted.
        can_merge = bool(persisted_token) and (
            persisted_token == current_token
            or not UPDATE_STATUS.get("active")
        )
        if can_merge:
            UPDATE_STATUS.update(persisted)
        return status_with_watchdog(dict(UPDATE_STATUS))


def begin_update_status(
    task_id,
    retry_run_id="",
    tracking_task_id="",
    status_path=DEFAULT_STATUS_PATH,
):
    started_at = iso_now()
    if task_id not in TASK_DEFINITIONS:
        raise ValueError("没有找到这个凸性更新任务。")
    definition = TASK_DEFINITIONS[task_id]
    with UPDATE_STATUS_LOCK:
        UPDATE_STATUS.update(
            state="running",
            active=True,
            taskId=task_id,
            taskLabel=definition.get("label", task_id),
            retryRunId=retry_run_id,
            trackingTaskId=tracking_task_id,
            runToken=started_at,
            runId="",
            message="任务正在后台运行，离开更新中心不会中断。",
            startedAt=started_at,
            finishedAt=None,
            workerThreadId=threading.get_ident(),
            recoveryAvailable=False,
            recoveryTaskId="",
            recoveryRunId="",
        )
        save_update_status(UPDATE_STATUS, status_path)
        return status_with_watchdog(dict(UPDATE_STATUS))


def finish_update_status(result, status_path=DEFAULT_STATUS_PATH):
    result_status = result.get("status", "success")
    recovery_available = result_status in {"partial_success", "failed"}
    with UPDATE_STATUS_LOCK:
        UPDATE_STATUS.update(
            state=result_status,
            active=False,
            taskId=result.get("taskId", UPDATE_STATUS.get("taskId", "")),
            taskLabel=result.get("taskLabel", UPDATE_STATUS.get("taskLabel", "")),
            runId=result.get("runId", ""),
            message=result.get("message", "更新任务已经完成。"),
            finishedAt=iso_now(),
            workerThreadId=None,
            recoveryAvailable=recovery_available,
            recoveryTaskId=(
                result.get("taskId", UPDATE_STATUS.get("taskId", ""))
                if recovery_available
                else ""
            ),
            recoveryRunId=(
                result.get("runId", "")
                if recovery_available
                else ""
            ),
        )
        save_update_status(UPDATE_STATUS, status_path)
        return status_with_watchdog(dict(UPDATE_STATUS))


def fail_update_status(error, status_path=DEFAULT_STATUS_PATH):
    with UPDATE_STATUS_LOCK:
        UPDATE_STATUS.update(
            state="failed",
            active=False,
            message=str(error),
            finishedAt=iso_now(),
            workerThreadId=None,
            recoveryAvailable=bool(UPDATE_STATUS.get("taskId")),
            recoveryTaskId=UPDATE_STATUS.get("taskId", ""),
            recoveryRunId=UPDATE_STATUS.get("runId", ""),
        )
        save_update_status(UPDATE_STATUS, status_path)
        return status_with_watchdog(dict(UPDATE_STATUS))


def initialize_update_recovery(
    db_path=DEFAULT_DB_PATH,
    status_path=DEFAULT_STATUS_PATH,
):
    recovery = recover_interrupted_updates(
        db_path=db_path,
        status_path=status_path,
    )
    with UPDATE_STATUS_LOCK:
        UPDATE_STATUS.clear()
        UPDATE_STATUS.update(
            recovery.get("status") or default_update_status()
        )
    return recovery


def get_startup_rebuild_state():
    with STARTUP_REBUILD_STATE_LOCK:
        return dict(STARTUP_REBUILD_STATE)


def set_startup_rebuild_state(**updates):
    with STARTUP_REBUILD_STATE_LOCK:
        STARTUP_REBUILD_STATE.update(updates)
        return dict(STARTUP_REBUILD_STATE)


def get_c25_services():
    global C25_PLANE, C25_CONTROL
    with C25_SERVICE_LOCK:
        if C25_PLANE is None:
            C25_PLANE = C25ControlPlane(
                project_root=PROJECT_ROOT,
                startup_state_provider=get_startup_rebuild_state,
            )
            C25_CONTROL = C25ControlService(C25_PLANE)
        return C25_PLANE, C25_CONTROL


def rebuild_pool_snapshot():
    connection = open_main_database_readonly()
    try:
        snapshot = build_pool_snapshot(
            connection,
            load_fixture(),
            production_only=True,
        )
        write_pool_snapshot(snapshot)
        return snapshot
    finally:
        connection.close()


def rebuild_discovery_snapshot():
    connection = open_main_database_readonly()
    try:
        snapshot = build_discovery_snapshot(connection)
        write_discovery_snapshot(snapshot)
        return snapshot
    finally:
        connection.close()


def rebuild_master_pool_snapshot():
    connection = open_main_database_readonly()
    try:
        snapshot = build_master_pool_snapshot(connection)
        write_master_pool_snapshot(snapshot)
        return snapshot
    finally:
        connection.close()


def rebuild_project_detail_snapshot():
    connection = open_main_database_readonly()
    try:
        snapshot = build_project_detail_snapshot(connection)
        write_project_detail_snapshot(snapshot)
        return snapshot
    finally:
        connection.close()


def rebuild_scan_center_snapshot():
    connection = open_main_database_readonly()
    try:
        snapshot = build_scan_center_snapshot(connection)
        write_scan_center_snapshot(snapshot)
        return snapshot
    finally:
        connection.close()


def rebuild_manual_review_snapshot():
    connection = open_main_database_readonly()
    try:
        snapshot = build_manual_review_snapshot(connection)
        write_manual_review_snapshot(snapshot)
        return snapshot
    finally:
        connection.close()


def rebuild_four_layer_snapshot():
    snapshot = build_four_layer_snapshot(
        FOUR_LAYER_CANDIDATE_PATH,
        DEFAULT_GOLD_INPUT_PATH,
        DEFAULT_GOLD_EXPECTED_PATH,
    )
    write_four_layer_snapshot(snapshot, FOUR_LAYER_OUTPUT_PATH)
    return snapshot


def load_c18_page_snapshots():
    snapshots = {}
    for name, path, prefix in (
        ("opportunity", APP_ROOT / "opportunity-center-snapshot.js", "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = "),
        ("tracking", APP_ROOT / "tracking-task-snapshot.js", "window.PENGUIN_CONVEXITY_TRACKING_TASKS = "),
    ):
        try:
            snapshots[name] = load_snapshot_payload(path, prefix)
        except (OSError, ValueError):
            snapshots[name] = {}
    return snapshots


class QuietHandler(SimpleHTTPRequestHandler):
    refresh_lock = threading.Lock()

    def log_message(self, _format, *_args):
        return

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path):
        request_path = unquote(urlparse(path).path)
        if request_path.startswith("/desktop/"):
            relative_path = request_path.removeprefix("/desktop/").lstrip("/")
            target = (DESKTOP_ROOT / relative_path).resolve()
            desktop_root = DESKTOP_ROOT.resolve()
            if target != desktop_root and desktop_root not in target.parents:
                return str(desktop_root / "__invalid_path__")
            return str(target)
        return super().translate_path(path)

    def send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("请求长度无效") from error
        if length <= 0 or length > 100_000:
            raise ValueError("请求内容为空或过大")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("请求内容不是有效 JSON") from error

    def do_GET(self):
        parsed_request = urlparse(self.path)
        request_path = parsed_request.path
        query = parse_qs(parsed_request.query)
        if request_path == "/":
            self.send_response(302)
            self.send_header("Location", "/desktop/index.html")
            self.end_headers()
            return
        if request_path == "/api/health":
            connection = open_main_database_readonly()
            try:
                projects = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                cases = connection.execute("SELECT COUNT(*) FROM candidate_cases").fetchone()[0]
            finally:
                connection.close()
            self.send_json(200, {
                "product": "企鹅投研-凸性",
                "status": "ready",
                "migrationRelease": MIGRATION_RELEASE,
                "convexityRelease": CONVEXITY_RELEASE,
                "experienceRelease": EXPERIENCE_RELEASE,
                "port": self.server.server_port,
                "projects": projects,
                "candidateCases": cases,
                "desktopShell": DESKTOP_ROOT.is_dir(),
                "database": str(DEFAULT_DB_PATH),
                "runtimeRoot": str(RUNTIME_ROOT),
                "taskIds": sorted(TASK_DEFINITIONS),
                "startupRebuild": get_startup_rebuild_state(),
            })
            return
        if request_path in {"/api/c1-8/status", "/api/c1-8/scheduler"}:
            from run_c1_8_scheduler import due_task_count

            snapshots = load_c18_page_snapshots()
            self.send_json(
                200,
                c18_scheduler_status(
                    snapshots["opportunity"],
                    snapshots["tracking"],
                    due_count=due_task_count(DEFAULT_DB_PATH),
                    task_installed=windows_task_installed(),
                ),
            )
            return
        if request_path == "/api/update-status":
            self.send_json(200, get_update_status())
            return
        if request_path == "/api/c2.0/status":
            try:
                payload = json.loads(C20_SNAPSHOT_STATUS_PATH.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                payload = {
                    "state": "pending",
                    "message": "判断质量快照尚未生成。",
                }
            self.send_json(200, payload)
            return
        if request_path == "/api/c2.1/status":
            self.send_json(200, c21_status_payload())
            return
        if request_path in {"/api/c2.2/status", "/api/c2.4/status"}:
            self.send_json(200, c22_status_payload())
            return
        if request_path.startswith("/api/c2.5/"):
            plane, _control = get_c25_services()
            try:
                if request_path == "/api/c2.5/control-plane":
                    payload = plane.control_plane_payload()
                elif request_path == "/api/c2.5/tasks":
                    payload = plane.tasks_payload()
                elif request_path == "/api/c2.5/task":
                    payload = plane.task_payload((query.get("taskId") or [""])[0])
                elif request_path == "/api/c2.5/chains-sources":
                    payload = plane.chains_sources_payload()
                elif request_path == "/api/c2.5/rules":
                    payload = plane.rules_payload()
                elif request_path == "/api/c2.5/decision-trace":
                    payload = plane.decision_trace_payload((query.get("assetId") or [""])[0])
                elif request_path == "/api/c2.5/snapshots":
                    payload = plane.snapshots_payload()
                elif request_path == "/api/c2.5/runs-audit":
                    payload = plane.runs_audit_payload()
                else:
                    self.send_json(404, {"error": "接口不存在"})
                    return
                status_code = 404 if payload.get("status") == "not_found" else 400 if payload.get("status") == "invalid_request" else 200
                self.send_json(status_code, payload)
            except Exception as error:
                self.send_json(500, {"error": f"管理组合状态读取失败：{type(error).__name__}: {error}"})
            return
        super().do_GET()

    def do_POST(self):
        if self.path in {"/api/c2.5/control/preview", "/api/c2.5/control/execute"}:
            _plane, control = get_c25_services()
            try:
                payload = self.read_json()
                status_code, result = control.preview(payload) if self.path.endswith("/preview") else control.execute(payload)
                self.send_json(status_code, result)
            except ControlError as error:
                self.send_json(error.status_code, {"status": "rejected", "code": error.code, "error": str(error)})
            except Exception as error:
                self.send_json(500, {"status": "failed", "code": "program_failure", "error": f"管理控制失败：{type(error).__name__}: {error}"})
            return
        if self.path not in {
            "/api/refresh-candidates",
            "/api/gate-screening",
            "/api/manual-scan",
            "/api/manual-review",
            "/api/tracking-decision-review",
            "/api/update-task",
            "/api/c1-8/scheduler",
            "/api/c1-8/run",
            "/api/c2.1/scheduler",
            "/api/c2.1/run",
            "/api/c2.1/pause-current",
            "/api/c2.2/scheduler",
            "/api/c2.2/run",
            "/api/c2.2/pause-current",
            "/api/c2.2/candidate-production/run",
            "/api/c2.2/candidate-production/pause",
            "/api/c2.2/candidate-production/retry",
            "/api/c2.4/scheduler",
            "/api/c2.4/run",
            "/api/c2.4/pause-current",
        }:
            self.send_json(404, {"error": "接口不存在"})
            return
        if not self.refresh_lock.acquire(blocking=False):
            self.send_json(409, {
                "error": "候选库正在更新，请等待本次任务结束。",
                "updateStatus": get_update_status(),
            })
            return
        update_status_started = False
        try:
            if self.path == "/api/c2.1/scheduler":
                payload = self.read_json()
                changes = {key: payload[key] for key in ("mode", "intervalHours", "paused") if key in payload}
                config = update_c21_config(changes)
                self.send_json(200, {"status": "success", "config": config, "runtime": c21_status_payload()})
                return
            if self.path == "/api/c2.1/run":
                payload = self.read_json()
                action = str(payload.get("action") or "all")
                if action not in {"all", "sync", "enrich", "evaluate", "snapshot", "retry_source"}:
                    raise ValueError("不支持的C2.1更新范围。")
                source_id = str(payload.get("sourceId") or "").strip() or None
                if action == "retry_source" and source_id not in RETRYABLE_SOURCE_STAGES:
                    raise ValueError("单项更新缺少有效来源。")
                result = launch_c21_hidden("manual", action, source_id)
                self.send_json(202 if result.get("status") == "launched" else 200, {**result, "runtime": c21_status_payload()})
                return
            if self.path == "/api/c2.1/pause-current":
                payload = self.read_json()
                paused = request_c21_pause(bool(payload.get("paused", True)))
                self.send_json(200, {"status": "success", "pauseCurrentRequested": paused, "runtime": c21_status_payload()})
                return
            if self.path in {"/api/c2.2/scheduler", "/api/c2.4/scheduler"}:
                payload = self.read_json()
                job_code = str(payload.get("jobCode") or "").strip()
                changes = {key: payload[key] for key in ("mode", "intervalHours", "paused") if key in payload}
                config = update_c22_config(job_code, changes)
                self.send_json(200, {"status": "success", "config": config, "runtime": c22_status_payload()})
                return
            if self.path in {"/api/c2.2/run", "/api/c2.4/run"}:
                payload = self.read_json()
                job_code = str(payload.get("jobCode") or "").strip() or "all"
                if job_code not in {*C22_JOB_CODES, "all"}:
                    raise ValueError("不支持的更新范围。")
                trigger = str(payload.get("trigger") or "manual")
                source_id = str(payload.get("sourceId") or "").strip() or None
                result = launch_c22_hidden(job_code, trigger, source_id)
                self.send_json(202 if result.get("status") == "launched" else 200, {**result, "runtime": c22_status_payload()})
                return
            if self.path in {"/api/c2.2/pause-current", "/api/c2.4/pause-current"}:
                payload = self.read_json()
                job_code = str(payload.get("jobCode") or "").strip()
                if job_code not in C22_JOB_CODES:
                    raise ValueError("暂停当前任务必须指定新币筛选或凸性跟踪。")
                requested = bool(payload.get("paused", True))
                paused = request_c22_pause(requested, job_code)
                if job_code == "screening":
                    request_c21_pause(requested)
                self.send_json(200, {
                    "status": "success",
                    "message": "暂停请求已记录；当前作业会在安全点停止并保留已有结果。",
                    "pauseCurrentRequested": paused,
                    "runtime": c22_status_payload(),
                })
                return
            if self.path == "/api/c2.2/candidate-production/run":
                payload = self.read_json()
                queue = str(payload.get("queue") or "historical_backlog")
                result = launch_candidate_production(queue)
                status_code = 409 if result.get("status") == "not_authorized" else 202 if result.get("status") == "launched" else 200
                self.send_json(status_code, {**result, "runtime": c22_status_payload()})
                return
            if self.path == "/api/c2.2/candidate-production/pause":
                payload = self.read_json()
                paused = request_candidate_production_pause(bool(payload.get("paused", True)))
                self.send_json(200, {
                    "status": "success",
                    "message": "候选基础扫描会在当前批次安全点暂停并保留断点。" if paused else "暂停请求已取消。",
                    "pauseRequested": paused,
                    "runtime": c22_status_payload(),
                })
                return
            if self.path == "/api/c2.2/candidate-production/retry":
                payload = self.read_json()
                result = retry_candidate_partition(str(payload.get("partitionId") or "").strip())
                status_code = 409 if result.get("status") == "not_authorized" else 202 if result.get("status") == "launched" else 200
                self.send_json(status_code, {**result, "runtime": c22_status_payload()})
                return
            if self.path == "/api/c1-8/scheduler":
                payload = self.read_json()
                action = str(payload.get("action") or "").strip()
                changes = {}
                if action == "pause":
                    changes["paused"] = True
                elif action == "resume":
                    changes["paused"] = False
                elif action == "enable":
                    changes["enabled"] = True
                elif action == "disable":
                    changes["enabled"] = False
                elif action == "set_time":
                    changes["dailyTime"] = payload.get("dailyTime")
                else:
                    raise ValueError("不支持的 C1.8 调度操作。")
                update_scheduler_config(changes, C18_CONFIG_PATH, C18_STATE_PATH)
                from run_c1_8_scheduler import due_task_count

                snapshots = load_c18_page_snapshots()
                self.send_json(200, {
                    "status": "success",
                    "config": load_c18_config(C18_CONFIG_PATH),
                    "scheduler": c18_scheduler_status(
                        snapshots["opportunity"],
                        snapshots["tracking"],
                        due_count=due_task_count(DEFAULT_DB_PATH),
                        task_installed=windows_task_installed(),
                    ),
                })
                return
            if self.path == "/api/c1-8/run":
                from run_c1_8_scheduler import run_once

                payload = self.read_json()
                result = run_once(
                    DEFAULT_DB_PATH,
                    dry_run=bool(payload.get("dryRun")),
                    force=True,
                )
                self.send_json(200, result)
                return
            if self.path == "/api/refresh-candidates":
                self.send_json(200, refresh_candidates())
                return
            if self.path == "/api/manual-scan":
                payload = self.read_json()
                network_ids = payload.get("networkIds", [])
                source_ids = payload.get("sourceIds", [])
                if not isinstance(network_ids, list) or not isinstance(source_ids, list):
                    raise ValueError("网络和信源必须使用列表格式")
                self.send_json(
                    200,
                    run_manual_scan(
                        network_ids=network_ids,
                        source_ids=source_ids,
                    ),
                )
                return
            if self.path == "/api/manual-review":
                self.send_json(
                    200,
                    execute_manual_review_action(self.read_json()),
                )
                return
            if self.path == "/api/tracking-decision-review":
                self.send_json(
                    200,
                    execute_tracking_decision_review(self.read_json()),
                )
                return
            if self.path == "/api/update-task":
                payload = self.read_json()
                task_id = str(payload.get("taskId", "")).strip()
                retry_run_id = str(payload.get("retryRunId", "")).strip()
                tracking_task_id = str(payload.get("trackingTaskId", "")).strip()
                begin_update_status(task_id, retry_run_id, tracking_task_id)
                update_status_started = True
                result = run_update_task(
                    task_id=task_id,
                    retry_run_id=retry_run_id,
                    tracking_task_id=tracking_task_id,
                )
                status = finish_update_status(result)
                update_status_started = False
                response = dict(result)
                response["updateStatus"] = status
                self.send_json(
                    200,
                    response,
                )
                return
            state = save_gate_screening_state(self.read_json())
            snapshot = rebuild_pool_snapshot()
            self.send_json(
                200,
                {
                    "status": "success",
                    "message": "硬门槛筛选方案已保存并重新计算。",
                    "active": state,
                    "summary": snapshot["gateScreening"]["summary"],
                },
            )
        except ValueError as error:
            if update_status_started:
                fail_update_status(error)
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            if update_status_started:
                fail_update_status(error)
            self.send_json(500, {"error": f"候选库更新失败：{error}"})
        finally:
            self.refresh_lock.release()


def rebuild_data_backbone_snapshot_readonly():
    connection = open_main_database_readonly()
    try:
        write_data_backbone_snapshot(build_data_backbone_snapshot(connection))
    finally:
        connection.close()


def rebuild_startup_snapshots():
    started_at = iso_now()
    set_startup_rebuild_state(
        state="running",
        startedAt=started_at,
        finishedAt=None,
        error="",
    )
    print("企鹅投研凸性：正在校验 C2.1/C2.2/C2.4 业务快照。", flush=True)
    try:
        with QuietHandler.refresh_lock:
            for snapshot_path, prefix in (
                *C21_STARTUP_SNAPSHOTS,
                *C22_STARTUP_SNAPSHOTS,
                *C24_STARTUP_SNAPSHOTS,
            ):
                source = snapshot_path.read_text(encoding="utf-8").strip()
                if not source.startswith(prefix) or not source.endswith(";"):
                    raise ValueError(f"发布快照格式无效：{snapshot_path.name}")
                json.loads(source[len(prefix):-1])
        set_startup_rebuild_state(
            state="success",
            finishedAt=iso_now(),
            error="",
        )
        print("企鹅投研凸性：C2.1/C2.2/C2.4 业务快照校验通过。", flush=True)
    except Exception as error:
        set_startup_rebuild_state(
            state="failed",
            finishedAt=iso_now(),
            error=str(error),
        )
        print(f"企鹅投研凸性：C2.1/C2.2/C2.4 业务快照校验失败：{error}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="启动企鹅投研凸性系统本地页面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)

    connection = open_main_database_readonly()
    try:
        connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    finally:
        connection.close()
    handler = partial(QuietHandler, directory=str(APP_ROOT))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"企鹅投研-凸性：http://{args.host}:{args.port}/desktop/index.html", flush=True)
    if C25_CANDIDATE_PRODUCT_STATE.get("status") == "ready":
        set_startup_rebuild_state(
            state="completed",
            finishedAt=iso_now(),
            source="sealed_candidate_product_state",
        )
        print("企鹅投研凸性：已读取封存候选产品快照；未触发正式业务重建。", flush=True)
    else:
        threading.Thread(
            target=rebuild_startup_snapshots,
            name="convexity-startup-rebuild",
            daemon=True,
        ).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
