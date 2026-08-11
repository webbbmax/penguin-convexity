#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH, SCHEMA_PATH
from rebuild_production_snapshots import rebuild_production_snapshots
from sync_thread_candidates import RULE_VERSION, SOURCE_ID


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_ROOT = PROJECT_ROOT / "archive" / "c1.5-00"
RESET_RUN_ID = "convexity-c1.5-clean-room-reset-v1"
SAFE_TABLES = ("sources", "source_accounts", "networks")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def table_counts(connection):
    names = [
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
    return {
        name: connection.execute(
            f'SELECT COUNT(*) FROM "{name}"'
        ).fetchone()[0]
        for name in names
    }


def legacy_counts(connection):
    return {
        "legacyCases": connection.execute(
            """
            SELECT COUNT(*)
            FROM candidate_cases
            WHERE case_id LIKE 'thread-%' OR rule_version = ?
            """,
            (RULE_VERSION,),
        ).fetchone()[0],
        "legacySources": connection.execute(
            "SELECT COUNT(*) FROM sources WHERE source_id = ?",
            (SOURCE_ID,),
        ).fetchone()[0],
    }


def read_table(connection, table_name):
    columns = [
        row[1]
        for row in connection.execute(f'PRAGMA table_info("{table_name}")')
    ]
    rows = [tuple(row) for row in connection.execute(f'SELECT * FROM "{table_name}"')]
    return columns, rows


def insert_rows(connection, table_name, columns, rows):
    if not rows:
        return
    column_sql = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f'INSERT OR REPLACE INTO "{table_name}" '
        f'({column_sql}) VALUES ({placeholders})',
        rows,
    )


def archive_database(connection, archive_root, counts, legacy):
    archive_dir = Path(archive_root).resolve() / safe_timestamp()
    archive_dir.mkdir(parents=True, exist_ok=False)
    archive_db = archive_dir / "convexity-before-clean.db"
    backup = sqlite3.connect(archive_db)
    try:
        connection.backup(backup)
    finally:
        backup.close()
    manifest = {
        "version": "C1.5-00",
        "archivedAt": utc_now(),
        "archiveOnly": True,
        "productionReadable": False,
        "reason": "隔离旧凸性任务答案及其全部衍生数据",
        "database": archive_db.name,
        "sha256": file_sha256(archive_db),
        "tableCounts": counts,
        "legacy": legacy,
    }
    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return archive_dir, archive_db, manifest_path


def create_clean_database(target_path, preserved):
    target_path = Path(target_path)
    for candidate in (
        target_path,
        Path(f"{target_path}-wal"),
        Path(f"{target_path}-shm"),
    ):
        if candidate.exists():
            candidate.unlink()
    connection = sqlite3.connect(target_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        for table_name in SAFE_TABLES:
            columns, rows = preserved[table_name]
            if table_name == "sources":
                last_checked_index = columns.index("last_checked_at")
                rows = [
                    tuple(
                        None if index == last_checked_index else value
                        for index, value in enumerate(row)
                    )
                    for row in rows
                    if row[columns.index("source_id")] != SOURCE_ID
                ]
            elif table_name == "source_accounts":
                valid_source_ids = {
                    row[preserved["sources"][0].index("source_id")]
                    for row in preserved["sources"][1]
                    if row[preserved["sources"][0].index("source_id")] != SOURCE_ID
                }
                source_index = columns.index("source_id")
                rows = [
                    row for row in rows
                    if row[source_index] in valid_source_ids
                ]
            insert_rows(connection, table_name, columns, rows)

        now = utc_now()
        connection.execute(
            """
            INSERT INTO runs (
              run_id, job_name, mode, status, started_at, finished_at,
              duration_ms, zero_result_class, zero_result_explanation,
              triggered_by, schema_version
            )
            VALUES (?, 'C1.5生产数据清场', 'initialization', 'success', ?, ?,
                    0, 'initialization', ?, 'system', 1)
            """,
            (
                RESET_RUN_ID,
                now,
                now,
                "旧对话答案已隔离。当前生产候选为零，等待机器首次扫描。",
            ),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"新生产数据库完整性检查失败：{integrity}")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    finally:
        connection.close()


def reset_production_data(
    db_path=DEFAULT_DB_PATH,
    archive_root=DEFAULT_ARCHIVE_ROOT,
    rebuild_snapshots=True,
):
    db_path = Path(db_path).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"生产数据库不存在：{db_path}")

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        marker_exists = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE run_id = ?",
            (RESET_RUN_ID,),
        ).fetchone()[0]
        legacy = legacy_counts(connection)
        if marker_exists and not any(legacy.values()):
            result = {
                "status": "already_clean",
                "database": str(db_path),
                "legacy": legacy,
                "archive": "",
            }
        else:
            counts = table_counts(connection)
            preserved = {
                table_name: read_table(connection, table_name)
                for table_name in SAFE_TABLES
            }
            archive_dir, archive_db, manifest = archive_database(
                connection,
                archive_root,
                counts,
                legacy,
            )
            result = {
                "status": "cleaned",
                "database": str(db_path),
                "legacy": legacy,
                "archive": str(archive_dir),
                "archiveDatabase": str(archive_db),
                "manifest": str(manifest),
            }
    finally:
        connection.close()

    if result["status"] == "cleaned":
        temporary = db_path.with_name(f"{db_path.name}.c1.5-clean.tmp")
        create_clean_database(temporary, preserved)
        for sidecar in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            if sidecar.exists():
                sidecar.unlink()
        os.replace(temporary, db_path)

    if rebuild_snapshots:
        result["snapshots"] = rebuild_production_snapshots(db_path)
    return result


def main():
    parser = argparse.ArgumentParser(description="隔离旧对话答案并重建凸性生产数据库")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--skip-snapshots", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            reset_production_data(
                db_path=args.db,
                archive_root=args.archive_root,
                rebuild_snapshots=not args.skip_snapshots,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
