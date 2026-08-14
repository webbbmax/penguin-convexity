#!/usr/bin/env python3
"""Read-only C2.5 development probe for the published C2.4 data boundary."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DATABASES = ("data/convexity.db", "data/c2.1-pipeline.db")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_state(path: Path) -> dict:
    stat = path.stat()
    return {"bytes": stat.st_size, "mtimeNs": stat.st_mtime_ns}


def inspect_database(path: Path) -> dict:
    before = file_state(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only=ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()[0]
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_key_violations = sum(1 for _row in connection.execute("PRAGMA foreign_key_check"))
    finally:
        connection.close()
    after = file_state(path)
    return {
        "path": str(path),
        "available": True,
        "openMode": "sqlite_uri_mode_ro",
        "queryOnly": query_only == 1,
        "quickCheck": quick_check,
        "foreignKeyViolations": foreign_key_violations,
        "before": before,
        "after": after,
        "unchangedDuringProbe": before == after,
        "note": "若现役调度器并发提交，文件状态可变化；本探针连接仍由mode=ro与query_only双重限制。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="C2.5 formal database read-only probe")
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    rows = []
    for relative in DATABASES:
        path = root / relative
        if not path.is_file():
            rows.append({"path": str(path), "available": False})
            continue
        rows.append(inspect_database(path))
    passed = all(
        row.get("available")
        and row.get("openMode") == "sqlite_uri_mode_ro"
        and row.get("queryOnly")
        and row.get("quickCheck") == "ok"
        and row.get("foreignKeyViolations") == 0
        for row in rows
    )
    print(
        json.dumps(
            {
                "schemaVersion": "c2.5-formal-readonly-probe-v1",
                "status": "passed" if passed else "failed",
                "observedAt": iso_now(),
                "projectRoot": str(root),
                "databases": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
