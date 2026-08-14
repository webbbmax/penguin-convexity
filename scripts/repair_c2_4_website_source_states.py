#!/usr/bin/env python3
"""Repair website-source states created by the pre-C2.4 shared circuit breaker."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from c2_1_db import open_pipeline_db, utc_now


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "c2.1-pipeline.db"
BACKUP_ROOT = PROJECT_ROOT / "runtime" / "c2.4" / "maintenance"
ACTIVE_LOCKS = (
    PROJECT_ROOT / "runtime" / "c2.1" / "pipeline.lock",
    PROJECT_ROOT / "runtime" / "c2.2" / "pipeline.lock",
)
BOUNDARY_REASON = "项目网站拒绝自动访问，属于来源能力边界；重复更新不会改变。"


def affected_rows(connection) -> tuple[list[dict], list[dict]]:
    blocked_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT * FROM source_health WHERE source_id='project_website_identity'
            AND status='configuration_missing' AND http_status IN (401,403)
            ORDER BY scope_key"""
        )
    ]
    poisoned_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT * FROM source_health WHERE source_id='project_website_identity'
            AND status='source_failure' AND http_status IS NULL ORDER BY scope_key"""
        )
    ]
    return blocked_rows, poisoned_rows


def repair(connection) -> dict:
    blocked_rows, poisoned_rows = affected_rows(connection)
    scopes_blocked = [row["scope_key"] for row in blocked_rows]
    scopes_poisoned = [row["scope_key"] for row in poisoned_rows]
    now = utc_now()
    with connection:
        for scope in scopes_blocked:
            connection.execute(
                """UPDATE source_health SET status='unsupported',reason_code='website_access_denied',
                plain_reason=?,affected_object_count=0,updated_at=?
                WHERE source_id='project_website_identity' AND scope_key=?""",
                (BOUNDARY_REASON, now, scope),
            )
            connection.execute(
                """UPDATE source_cursors SET status='unsupported',consecutive_failures=0,
                next_retry_at=NULL,updated_at=?
                WHERE source_id='project_website_identity' AND scope_key=?""",
                (now, scope),
            )
        for scope in scopes_poisoned:
            connection.execute(
                "DELETE FROM source_health WHERE source_id='project_website_identity' AND scope_key=?",
                (scope,),
            )
            connection.execute(
                """DELETE FROM source_cursors WHERE source_id='project_website_identity'
                AND scope_key=? AND status='source_failure'""",
                (scope,),
            )
    return {
        "reclassifiedAccessDenied": len(scopes_blocked),
        "resetSharedCircuitFailures": len(scopes_poisoned),
        "blockedRows": blocked_rows,
        "poisonedRows": poisoned_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="修复项目网站来源的错误状态语义")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--allow-active-lock", action="store_true")
    args = parser.parse_args()
    if not args.allow_active_lock and any(path.exists() for path in ACTIVE_LOCKS):
        raise SystemExit("检测到现役写入任务，未修改数据库；请等待任务到达终态。")
    connection = open_pipeline_db(args.db)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = BACKUP_ROOT / f"website-source-state-repair-{stamp}.json"
    try:
        blocked_rows, poisoned_rows = affected_rows(connection)
        prepared = {
            "schemaVersion": "c2.4-website-source-state-repair-v1",
            "database": str(args.db.resolve()),
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "state": "prepared",
            "blockedRows": blocked_rows,
            "poisonedRows": poisoned_rows,
        }
        temporary = backup_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, backup_path)
        result = repair(connection)
    finally:
        connection.close()
    payload = {
        **prepared,
        "state": "completed",
        **result,
    }
    temporary = backup_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, backup_path)
    print(json.dumps({key: value for key, value in payload.items() if key not in {"blockedRows", "poisonedRows"}} | {"backupPath": str(backup_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
