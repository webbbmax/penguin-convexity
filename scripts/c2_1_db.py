#!/usr/bin/env python3
"""C2.1 isolated SQLite ownership and small persistence helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "c2.1-pipeline.db"
DEFAULT_MAIN_DB_PATH = PROJECT_ROOT / "data" / "convexity.db"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "storage" / "c2.1-schema.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def open_pipeline_db(path=DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def open_main_db_readonly(path=DEFAULT_MAIN_DB_PATH) -> sqlite3.Connection:
    path = Path(path).resolve()
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def initialize_database(path=DEFAULT_DB_PATH, schema_path=DEFAULT_SCHEMA_PATH) -> Path:
    path = Path(path)
    schema = Path(schema_path).read_text(encoding="utf-8")
    with closing(open_pipeline_db(path)) as connection:
        connection.executescript(schema)
        now = utc_now()
        for key, value in (
            ("schema_version", "c2.1-pipeline-schema-v1"),
            ("database_owner", "penguin-convexity-c2.1"),
            ("production_main_db_mode", "read_only"),
        ):
            connection.execute(
                """
                INSERT INTO schema_meta(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                """,
                (key, value, now),
            )
        connection.commit()
    return path


def integrity_result(path=DEFAULT_DB_PATH) -> dict:
    with closing(open_pipeline_db(path)) as connection:
        return {
            "integrityCheck": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreignKeyViolations": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        }


if __name__ == "__main__":
    initialized = initialize_database()
    print(json.dumps({"database": str(initialized), **integrity_result(initialized)}, ensure_ascii=False, indent=2))
