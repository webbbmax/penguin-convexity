#!/usr/bin/env python3
import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
DICTIONARY_PATH = PROJECT_ROOT / "storage" / "data-dictionary.json"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "convexity.db"
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "runtime-snapshot.js"
BACKUP_DIR = PROJECT_ROOT / "backups"


ZERO_RESULT_LABELS = {
    "none": "本次产生了有效结果",
    "initialization": "数据库初始化，本次没有执行采集",
    "no_qualifying_candidates": "采集正常，但没有线索通过候选门槛",
    "source_returned_no_data": "任务已运行，但上游来源没有返回数据",
    "task_not_run": "任务没有运行或未到执行时间",
    "rules_too_strict": "有原始线索，但全部被规则过滤，需要检查阈值",
    "source_failure": "一个或多个来源失败，结果不完整",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def table_names(connection):
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]


def table_columns(connection, table_name):
    return [
        {
            "name": row[1],
            "type": row[2] or "TEXT",
            "required": bool(row[3]),
            "primaryKey": bool(row[5]),
        }
        for row in connection.execute(f'PRAGMA table_info("{table_name}")')
    ]


def build_runtime_snapshot(connection):
    dictionary = json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
    names = table_names(connection)
    tables = []
    for name in names:
        metadata = dictionary["tables"].get(
            name,
            {"label": name, "purpose": "该数据表尚未补充中文说明。"},
        )
        count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        columns = table_columns(connection, name)
        tables.append(
            {
                "name": name,
                "label": metadata["label"],
                "purpose": metadata["purpose"],
                "rowCount": count,
                "columns": [
                    {
                        **column,
                        "label": dictionary["fieldLabels"].get(column["name"], column["name"]),
                    }
                    for column in columns
                ],
            }
        )

    latest_run_row = connection.execute(
        """
        SELECT *
        FROM runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    latest_run = dict(latest_run_row) if latest_run_row else None
    if latest_run:
        latest_run["zeroResultLabel"] = ZERO_RESULT_LABELS.get(
            latest_run["zero_result_class"],
            latest_run["zero_result_class"],
        )

    latest_source_stats = []
    if latest_run:
        latest_source_stats = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM run_source_stats
                WHERE run_id = ?
                ORDER BY collector_id
                """,
                (latest_run["run_id"],),
            )
        ]

    counts = {
        "tables": len(names),
        "sources": connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "rawEvents": connection.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0],
        "shadowCases": connection.execute(
            "SELECT COUNT(*) FROM candidate_cases WHERE workflow_state = 'shadow_signal'"
        ).fetchone()[0],
        "activeCases": connection.execute(
            """
            SELECT COUNT(*)
            FROM candidate_cases
            WHERE workflow_state IN ('active_embryo', 'priority_watch', 'extreme_test', 'trial_ready', 'igniting')
            """
        ).fetchone()[0],
        "retryableErrors": connection.execute(
            """
            SELECT COUNT(*)
            FROM run_errors
            WHERE retryable = 1 AND retry_status IN ('not_requested', 'pending', 'failed')
            """
        ).fetchone()[0],
    }

    return {
        "version": dictionary["version"],
        "title": dictionary["title"],
        "principle": dictionary["principle"],
        "generatedAt": utc_now(),
        "databaseStatus": "initialized",
        "counts": counts,
        "groups": dictionary["groups"],
        "tables": tables,
        "latestRun": latest_run,
        "latestSourceStats": latest_source_stats,
        "zeroResultLabels": ZERO_RESULT_LABELS,
    }


def write_runtime_snapshot(connection, snapshot_path):
    snapshot = build_runtime_snapshot(connection)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = snapshot_path.with_suffix(f"{snapshot_path.suffix}.tmp")
    temporary_path.write_text(
        f"window.PENGUIN_CONVEXITY_FOUNDATION = {json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary_path.replace(snapshot_path)
    return snapshot


def initialize_database(
    db_path=DEFAULT_DB_PATH,
    snapshot_path=DEFAULT_SNAPSHOT_PATH,
    backup=True,
    backup_dir=BACKUP_DIR,
    refresh_snapshot=True,
):
    db_path = Path(db_path).resolve()
    snapshot_path = Path(snapshot_path).resolve()
    backup_dir = Path(backup_dir).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if backup and db_path.exists() and db_path.stat().st_size:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"convexity-{safe_timestamp()}.db"
        shutil.copy2(db_path, backup_path)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        now = utc_now()
        connection.execute(
            """
            INSERT OR IGNORE INTO runs (
              run_id, job_name, mode, status, started_at, finished_at, duration_ms,
              zero_result_class, zero_result_explanation, triggered_by, schema_version
            )
            VALUES (?, ?, 'initialization', 'success', ?, ?, 0, 'initialization', ?, 'system', 1)
            """,
            (
                "convexity-bootstrap-v1",
                "凸性数据底座初始化",
                now,
                now,
                "数据底座已经建立，本次没有执行外部采集，因此候选数量为零。",
            ),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite 完整性检查失败：{integrity}")
        if refresh_snapshot or not snapshot_path.exists():
            snapshot = write_runtime_snapshot(connection, snapshot_path)
        else:
            snapshot = {
                "counts": {
                    "tables": len(table_names(connection)),
                }
            }
    finally:
        connection.close()

    return {
        "database": str(db_path),
        "snapshot": str(snapshot_path),
        "backup": str(backup_path) if backup_path else "",
        "tables": snapshot["counts"]["tables"],
        "status": "success",
    }


def main():
    parser = argparse.ArgumentParser(description="初始化企鹅投研凸性系统 SQLite 数据底座")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--skip-backup", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            initialize_database(args.db, args.snapshot, backup=not args.skip_backup),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
