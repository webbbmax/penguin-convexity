#!/usr/bin/env python3
"""One-shot SQD HTTP worker; request secrets travel only through stdin."""

import base64
import json
import sys
import urllib.error
import urllib.request


def main():
    payload = json.loads(sys.stdin.read())
    url = payload["url"]
    body = base64.b64decode(payload["bodyBase64"]) if payload.get("bodyBase64") else None
    request = urllib.request.Request(url, data=body, headers=payload.get("headers") or {})
    try:
        opener = (
            urllib.request.build_opener()
            if payload.get("useEnvironmentProxy")
            else urllib.request.build_opener(urllib.request.ProxyHandler({}))
        )
        with opener.open(request, timeout=float(payload["socketTimeoutSeconds"])) as response:
            result = {
                "status": response.status,
                "headers": dict(response.headers.items()),
                "bodyBase64": base64.b64encode(response.read()).decode("ascii"),
            }
    except urllib.error.HTTPError as error:
        result = {
            "status": error.code,
            "headers": dict(error.headers.items()) if error.headers else {},
            "bodyBase64": base64.b64encode(error.read()).decode("ascii"),
        }
    except Exception as error:
        message = str(error).replace(url, "[REDACTED_URL]")
        result = {"errorType": type(error).__name__, "error": message[:500]}
    sys.stdout.write(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
