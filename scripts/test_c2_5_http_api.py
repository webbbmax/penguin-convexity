#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import serve_local


class FakePlane:
    def _payload(self, name: str) -> dict:
        return {"schemaVersion": f"fixture-{name}-v1", "status": "ready"}

    def control_plane_payload(self): return self._payload("control-plane")
    def tasks_payload(self): return self._payload("tasks")
    def task_payload(self, task_id): return {**self._payload("task"), "taskId": task_id}
    def chains_sources_payload(self): return self._payload("chains-sources")
    def rules_payload(self): return self._payload("rules")
    def decision_trace_payload(self, asset_id): return {"status": "not_found", "assetId": asset_id}
    def snapshots_payload(self): return self._payload("snapshots")
    def runs_audit_payload(self): return self._payload("runs-audit")


class FakeControl:
    def preview(self, payload):
        return 200, {"status": "previewed", "requestId": payload.get("requestId")}

    def execute(self, payload):
        return 202, {"status": "accepted", "requestId": payload.get("requestId")}


class C25HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_plane = serve_local.C25_PLANE
        cls.previous_control = serve_local.C25_CONTROL
        serve_local.C25_PLANE = FakePlane()
        serve_local.C25_CONTROL = FakeControl()
        handler = partial(serve_local.QuietHandler, directory=str(PROJECT_ROOT / "app"))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        serve_local.C25_PLANE = cls.previous_plane
        serve_local.C25_CONTROL = cls.previous_control

    @classmethod
    def get_json(cls, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(cls.base + path, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    @classmethod
    def post_json(cls, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            cls.base + path,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_all_read_routes_use_the_control_plane(self):
        routes = {
            "/api/c2.5/control-plane": "fixture-control-plane-v1",
            "/api/c2.5/tasks": "fixture-tasks-v1",
            "/api/c2.5/task?taskId=c22.screening": "fixture-task-v1",
            "/api/c2.5/chains-sources": "fixture-chains-sources-v1",
            "/api/c2.5/rules": "fixture-rules-v1",
            "/api/c2.5/snapshots": "fixture-snapshots-v1",
            "/api/c2.5/runs-audit": "fixture-runs-audit-v1",
        }
        for path, schema in routes.items():
            status, payload = self.get_json(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(payload["schemaVersion"], schema, path)
        status, payload = self.get_json("/api/c2.5/decision-trace?assetId=missing")
        self.assertEqual(status, 404)
        self.assertEqual(payload["assetId"], "missing")

    def test_preview_and_execute_have_distinct_protected_routes(self):
        status, preview = self.post_json(
            "/api/c2.5/control/preview",
            {"requestId": "request-http-preview-1", "taskId": "fixture", "action": "run_now"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["status"], "previewed")
        status, execute = self.post_json(
            "/api/c2.5/control/execute",
            {"requestId": "request-http-execute-1", "confirmationToken": "fixture"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(execute["status"], "accepted")

    def test_unregistered_c25_route_is_not_silently_served(self):
        status, payload = self.get_json("/api/c2.5/not-registered")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "接口不存在")


if __name__ == "__main__":
    unittest.main(verbosity=2)
