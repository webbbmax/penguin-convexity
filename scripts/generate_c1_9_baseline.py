#!/usr/bin/env python3
"""Create the immutable, read-only baseline manifest for the C1.9 implementation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "convexity.db"
MANIFEST_PATH = ROOT / "docs" / "C1.9_BASELINE_MANIFEST.json"
BACKUP_ROOT = ROOT / "backups"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_inventory() -> list[dict[str, object]]:
    roots = (ROOT / "app", ROOT / "desktop", ROOT / "scripts")
    allowed = {".html", ".js", ".css", ".py", ".ps1", ".vbs"}
    files: list[dict[str, object]] = []
    for base in roots:
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in allowed:
                continue
            if "__pycache__" in path.parts:
                continue
            files.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    return files


def database_snapshot() -> dict[str, object]:
    connection = sqlite3.connect(DB_PATH)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        projects = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        cases = connection.execute("SELECT COUNT(*) FROM candidate_cases").fetchone()[0]
    finally:
        connection.close()
    return {
        "path": DB_PATH.relative_to(ROOT).as_posix(),
        "bytes": DB_PATH.stat().st_size,
        "sha256": sha256(DB_PATH),
        "integrityCheck": integrity,
        "foreignKeyCheck": "ok" if not foreign_keys else foreign_keys,
        "projects": projects,
        "candidateCases": cases,
    }


def latest_backup() -> dict[str, object] | None:
    candidates = sorted(
        (path for path in BACKUP_ROOT.glob("c1.9-pre-implementation-*/convexity.db") if path.is_file()),
        reverse=True,
    )
    if not candidates:
        return None
    path = candidates[0]
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def main() -> None:
    manifest = {
        "schemaVersion": "c1.9-baseline-v1",
        "release": "C1.9",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "phase": "requirements_frozen_waiting_luna",
        "scope": "改版前可恢复基线；不改变业务数据、评分、动作或研究规则。",
        "routeBaseline": {
            "legacyRouteTests": 30,
            "frontEntry": "/candidate-pool.html",
            "defaultPort": 8766,
        },
        "database": database_snapshot(),
        "onlineBackup": latest_backup(),
        "activeFileInventory": file_inventory(),
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
