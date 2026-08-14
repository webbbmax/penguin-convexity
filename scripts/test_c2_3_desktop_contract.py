from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "desktop-host" / "PenguinConvexity.Desktop"
MAINTENANCE = ROOT / "docs" / "C2.3_POST_RELEASE_MAINTENANCE_20260812.json"


def tree_hash(relative: str) -> tuple[int, str]:
    files = sorted(path for path in (ROOT / relative).rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return len(files), digest.hexdigest()


def stable_app_tree_hash(excluded_paths: set[str]) -> tuple[int, str]:
    files = sorted(
        path for path in (ROOT / "app").rglob("*")
        if path.is_file()
        and not path.name.endswith("snapshot.js")
        and path.relative_to(ROOT).as_posix() not in excluded_paths
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return len(files), digest.hexdigest()


def main() -> int:
    baseline = json.loads((ROOT / "docs" / "C2.3_IMPLEMENTATION_BASELINE.json").read_text(encoding="utf-8"))
    sources = "\n".join(path.read_text(encoding="utf-8") for path in HOST.glob("*.cs"))
    forbidden = {
        "direct SQLite use": "Microsoft.Data.Sqlite",
        "main database path": "convexity.db",
        "pipeline database path": "c2.1-pipeline.db",
        "bulk process enumeration": "GetProcessesByName",
        "process tree kill": "entireProcessTree: true",
        "ordinary Edge executable": "msedge.exe",
        "ordinary Chrome executable": "chrome.exe",
    }
    failures = [name for name, marker in forbidden.items() if marker.lower() in sources.lower()]

    app_count, app_hash = tree_hash("app")
    desktop_count, desktop_hash = tree_hash("desktop")
    if app_count != baseline["businessAssets"]["appFileCount"] or app_hash != baseline["businessAssets"]["appTreeSha256"]:
        if not MAINTENANCE.exists():
            failures.append("existing app business assets changed without a maintenance record")
        else:
            maintenance = json.loads(MAINTENANCE.read_text(encoding="utf-8"))
            changed = {item["path"]: item["sha256"] for item in maintenance.get("changedFiles", [])}
            maintained_app_paths = {path for path in changed if path.startswith("app/")}
            for relative in maintained_app_paths:
                if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != changed[relative]:
                    failures.append(f"maintained app file changed: {relative}")
            stable_count, stable_hash = stable_app_tree_hash(maintained_app_paths)
            verification = maintenance.get("verification", {})
            if stable_count != verification.get("stableAppFileCountAfter") or stable_hash != verification.get("stableAppTreeSha256After"):
                failures.append("unrelated stable app assets changed after post-release maintenance")
    if desktop_count != baseline["businessAssets"]["desktopFileCount"] or desktop_hash != baseline["businessAssets"]["desktopTreeSha256"]:
        failures.append("existing desktop web assets changed")

    required = [
        "CoreWebView2Environment",
        "webview2",
        "user-data",
        "NamedPipeServerStream",
        "FrameNavigationStarting",
        "http://127.0.0.1:8766/desktop/index.html",
        "企鹅投研-凸性",
    ]
    normalized_sources = sources.replace("\\\\", "\\")
    for marker in required:
        if marker not in sources and marker not in normalized_sources:
            failures.append(f"missing contract marker: {marker}")

    result = {
        "status": "passed" if not failures else "failed",
        "sourceFileCount": len(list(HOST.glob("*.cs"))),
        "appBusinessTreeUnchanged": app_hash,
        "desktopWebTreeUnchanged": desktop_hash,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
