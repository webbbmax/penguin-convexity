from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FixtureHandler(BaseHTTPRequestHandler):
    mode = "port_conflict"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.mode == "identity_mismatch" and self.path == "/api/health":
            body = json.dumps(
                {
                    "product": "not-penguin-convexity",
                    "status": "ready",
                    "migrationRelease": "M1.0",
                    "convexityRelease": "C1.7",
                    "experienceRelease": "C2.2",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = b"fixture-port-owner"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("port_conflict", "identity_mismatch"), required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    FixtureHandler.mode = args.mode
    ThreadingHTTPServer(("127.0.0.1", args.port), FixtureHandler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
