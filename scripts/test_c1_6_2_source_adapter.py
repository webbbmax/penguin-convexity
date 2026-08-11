#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from init_db import initialize_database
from source_adapter import (
    build_source_adapter_snapshot,
    run_source_adapter,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def seed_exact_recovery_case(connection):
    now = "2026-07-31T05:00:00Z"
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope,
          confidence, conflict_risk, status, schedule_text,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'api', 'convexity', '中', '低', 'active', '',
                ?, ?)
        """,
        (
            "discovery-adapter-test",
            "主干测试来源",
            "project_discovery",
            "https://source.example",
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO projects (
          project_id, canonical_name, identity_status,
          first_seen_at, created_at, updated_at
        )
        VALUES ('adapter-project', 'Adapter Project', 'verified', ?, ?, ?)
        """,
        (now, now, now),
    )
    connection.execute(
        """
        INSERT INTO source_discoveries (
          source_discovery_id, source_id, external_id, canonical_name,
          normalized_name, website_url, source_url, first_seen_at,
          last_seen_at, matched_project_id, project_identity_status,
          attribution_confidence, created_at, updated_at
        )
        VALUES (
          'adapter-discovery-1', 'discovery-adapter-test', 'external-1',
          'Adapter Project', 'adapter project', 'https://adapter.example',
          'https://source.example/project', ?, ?, 'adapter-project',
          'verified', 'high', ?, ?
        )
        """,
        (now, now, now, now),
    )
    connection.executemany(
        """
        INSERT INTO evidence_items (
          evidence_id, project_id, evidence_type, stance, fact_boundary,
          confidence, observed_at, source_id, source_url, summary, created_at
        )
        VALUES (?, 'adapter-project', 'official_profile', 'neutral',
                'confirmed_fact', '高', ?, 'discovery-adapter-test', ?,
                ?, ?)
        """,
        (
            (
                "adapter-evidence-exact",
                now,
                "https://source.example/project",
                "存在可精确匹配的历史结构化来源。",
                now,
            ),
            (
                "adapter-evidence-empty-url",
                now,
                "",
                "没有来源链接，必须继续保留缺口。",
                now,
            ),
        ),
    )
    connection.commit()


def main():
    with tempfile.TemporaryDirectory() as temporary:
        db_path = Path(temporary) / "adapter-test.db"
        snapshot_path = Path(temporary) / "runtime.js"
        initialize_database(db_path, snapshot_path, backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            seed_exact_recovery_case(connection)

            first = run_source_adapter(connection)
            connection.commit()
            assert first["recovered"] == 1
            assert first["remaining"] == 1
            assert first["conflicts"] == 0

            exact = connection.execute(
                """
                SELECT raw_event_id FROM evidence_items
                WHERE evidence_id = 'adapter-evidence-exact'
                """
            ).fetchone()[0]
            empty_url = connection.execute(
                """
                SELECT raw_event_id FROM evidence_items
                WHERE evidence_id = 'adapter-evidence-empty-url'
                """
            ).fetchone()[0]
            assert exact and exact.startswith("raw-adapter-")
            assert empty_url is None, "空 URL 不得被当作精确匹配"

            raw_count = connection.execute(
                "SELECT COUNT(*) FROM raw_events"
            ).fetchone()[0]
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM source_adapter_records"
            ).fetchone()[0]
            second = run_source_adapter(connection)
            connection.commit()
            assert second["recovered"] == 0
            assert second["remaining"] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM raw_events"
            ).fetchone()[0] == raw_count
            assert connection.execute(
                "SELECT COUNT(*) FROM source_adapter_records"
            ).fetchone()[0] == audit_count

            snapshot = build_source_adapter_snapshot(connection, second)
            source = next(
                item
                for item in snapshot["sources"]
                if item["sourceId"] == "discovery-adapter-test"
            )
            assert source["adapterStatus"] == "partial"
            assert source["recoveredCount"] == 1
            assert source["missingRawCount"] == 1
            assert snapshot["counts"]["conflicts"] == 0

            adapter_id = connection.execute(
                "SELECT adapter_record_id FROM source_adapter_records LIMIT 1"
            ).fetchone()[0]
            try:
                connection.execute(
                    """
                    UPDATE source_adapter_records SET detail = 'changed'
                    WHERE adapter_record_id = ?
                    """,
                    (adapter_id,),
                )
                raise AssertionError("主干审计记录可以被覆盖")
            except sqlite3.IntegrityError:
                connection.rollback()
        finally:
            connection.close()

    html = (APP_ROOT / "source-adapter.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "source-adapter.js").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    assert "主干接入状态" in html
    assert "source-adapter-snapshot.js" in html
    assert "adapterStatus" in script and "gapExamples" in script
    assert '["source-adapter.html", "主干接入状态"]' in navigation
    assert 'href="source-adapter.html"' in workbench
    assert '"source-adapter.html"' in navigation
    adapter_source = (
        PROJECT_ROOT / "scripts" / "source_adapter.py"
    ).read_text(encoding="utf-8").lower()
    assert "rwa" not in adapter_source
    print("C1.6-05 主干精确恢复、幂等审计、空链接保护与可视入口测试通过。")


if __name__ == "__main__":
    main()
