#!/usr/bin/env python3
"""Read-only local server for C2.5 browser fixture review.

This is a development test entry only. It never exposes product control POSTs and
never writes databases, runtime state, task settings, or management audit data.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
DESKTOP_ROOT = PROJECT_ROOT / "desktop"
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from c2_5_control_plane import C25ControlPlane


class FixtureHandler(SimpleHTTPRequestHandler):
    plane = C25ControlPlane(project_root=PROJECT_ROOT, windows_reader=lambda: [])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_ROOT), **kwargs)

    def translate_path(self, path: str) -> str:
        request_path = urlparse(path).path
        if request_path.startswith("/desktop/"):
            target = (DESKTOP_ROOT / request_path.removeprefix("/desktop/").lstrip("/")).resolve()
            desktop_root = DESKTOP_ROOT.resolve()
            if target != desktop_root and desktop_root not in target.parents:
                return str(desktop_root / "__invalid_path__")
            return str(target)
        return super().translate_path(path)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/__fixtures__/component-state-board.html":
            body = (PROJECT_ROOT / "fixtures" / "c2.5" / "component-state-board.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        routes = {
            "/api/c2.5/control-plane": self.plane.control_plane_payload,
            "/api/c2.5/tasks": self.plane.tasks_payload,
            "/api/c2.5/task": lambda: self.plane.task_payload((query.get("taskId") or [""])[0]),
            "/api/c2.5/chains-sources": self.plane.chains_sources_payload,
            "/api/c2.5/rules": self.plane.rules_payload,
            "/api/c2.5/decision-trace": lambda: self.plane.decision_trace_payload((query.get("assetId") or [""])[0]),
            "/api/c2.5/snapshots": self.plane.snapshots_payload,
            "/api/c2.5/runs-audit": self.plane.runs_audit_payload,
        }
        handler = routes.get(parsed.path)
        if handler is None:
            return super().do_GET()
        payload = handler()
        self._json(200 if payload.get("status") not in {"not_found", "invalid_request"} else 404, payload)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        self._json(
            405,
            {
                "status": "fixture_read_only",
                "message": "视觉夹具服务不开放任何产品写控制。",
            },
        )

    def log_message(self, format: str, *args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="C2.5 read-only visual fixture server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), FixtureHandler)
    print(f"C2.5 visual fixture: http://{args.host}:{args.port}/workbench.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
