#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from data_backbone import run_data_backbone
from init_db import initialize_database


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS {message}")


def insert_source(connection, source_id, name):
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, created_at, updated_at
        ) VALUES (?, ?, 'test', '', 'local test', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')
        """,
        (source_id, name),
    )


def insert_raw(connection, raw_id, source_id, external_id, project_hint=""):
    connection.execute(
        """
        INSERT INTO raw_events (
          raw_event_id, source_id, external_id, collected_at, content_hash,
          source_url, project_hint, event_type, raw_payload_json, status
        ) VALUES (?, ?, ?, '2026-08-01T01:00:00Z', ?, ?, ?,
                  'official_code_activity', '{}', 'normalized')
        """,
        (
            raw_id, source_id, external_id, f"hash-{raw_id}",
            f"https://example.test/{external_id}", project_hint,
        ),
    )


def main():
    with tempfile.TemporaryDirectory() as temporary:
        db_path = Path(temporary) / "backbone.db"
        initialize_database(db_path, Path(temporary) / "runtime.js", backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            insert_source(connection, "test-orphan", "Orphan Test")
            insert_source(connection, "test-silent", "Silent Test")
            insert_source(connection, "test-zero", "Zero Test")
            insert_raw(
                connection, "raw-test-orphan", "test-orphan", "orphan-1",
                "Future Protocol",
            )
            connection.execute(
                """
                INSERT INTO runs (
                  run_id, job_name, mode, status, started_at, finished_at
                ) VALUES ('run-zero', 'zero', 'manual', 'success',
                          '2026-08-01T00:30:00Z', '2026-08-01T00:31:00Z')
                """
            )
            connection.execute(
                """
                INSERT INTO run_source_stats (
                  run_source_stat_id, run_id, source_id, collector_id, status,
                  started_at, finished_at, collected_count
                ) VALUES
                  ('stat-zero', 'run-zero', 'test-zero', 'zero', 'no_data',
                   '2026-08-01T00:30:00Z', '2026-08-01T00:31:00Z', 0),
                  ('stat-silent', 'run-zero', 'test-silent', 'silent', 'success',
                   '2026-08-01T00:30:00Z', '2026-08-01T00:31:00Z', 1)
                """
            )

            first = run_data_backbone(connection)
            connection.commit()
            assert_true(first["normalized"]["inserted"] == 1, "Event Schema v2 首次写入原始事件")
            assert_true(
                connection.execute(
                    "SELECT attribution_status FROM orphan_events_v2 WHERE event_id=(SELECT event_id FROM normalized_events_v2 WHERE raw_event_id='raw-test-orphan')"
                ).fetchone()[0] == "pending",
                "无法唯一归属的原始证据不会丢失",
            )
            assert_true(
                connection.execute(
                    "SELECT health_state FROM source_health_v2 WHERE source_id='test-zero'"
                ).fetchone()[0] == "true_zero",
                "真实零结果与采集失败分开记录",
            )
            assert_true(
                connection.execute(
                    "SELECT gap_status FROM source_cursors_v2 WHERE source_id='test-silent'"
                ).fetchone()[0] == "open",
                "采集静默会自动打开断档",
            )

            second = run_data_backbone(connection)
            connection.commit()
            assert_true(
                second["normalized"]["inserted"] == 0
                and second["normalized"]["updated"] == 0
                and second["normalized"]["duplicates"] == 1,
                "重复回放保持幂等去重",
            )

            connection.execute(
                """
                INSERT INTO projects (
                  project_id, canonical_name, identity_status, first_seen_at,
                  created_at, updated_at
                ) VALUES ('future-protocol', 'Future Protocol', 'verified',
                          '2026-08-01T02:00:00Z', '2026-08-01T02:00:00Z',
                          '2026-08-01T02:00:00Z')
                """
            )
            insert_raw(
                connection, "raw-test-silent", "test-silent", "silent-1",
                "Future Protocol",
            )
            third = run_data_backbone(connection, mode="gap_recovery")
            connection.commit()
            event = connection.execute(
                "SELECT project_id, attribution_status FROM normalized_events_v2 WHERE raw_event_id='raw-test-orphan'"
            ).fetchone()
            assert_true(
                event["project_id"] == "future-protocol"
                and event["attribution_status"] == "verified",
                "身份锚点补齐后孤儿证据会重新归属",
            )
            assert_true(
                connection.execute(
                    "SELECT attribution_status FROM orphan_events_v2 WHERE event_id=(SELECT event_id FROM normalized_events_v2 WHERE raw_event_id='raw-test-orphan')"
                ).fetchone()[0] == "resolved",
                "孤儿证据保留已解决状态而不是被删除",
            )
            assert_true(
                connection.execute(
                    "SELECT COUNT(*) FROM event_attribution_history WHERE event_id=(SELECT event_id FROM normalized_events_v2 WHERE raw_event_id='raw-test-orphan')"
                ).fetchone()[0] >= 1,
                "再归属过程保留不可变审计历史",
            )
            assert_true(
                third["gapsRecovered"] >= 1
                and connection.execute(
                    "SELECT gap_status FROM source_cursors_v2 WHERE source_id='test-silent'"
                ).fetchone()[0] == "resolved",
                "来源恢复写入后断档会自动关闭",
            )
            assert_true(
                connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                "C1.7 数据主干 SQLite 完整性通过",
            )
        finally:
            connection.close()

    print("C1.7 data backbone checks passed")


if __name__ == "__main__":
    main()
