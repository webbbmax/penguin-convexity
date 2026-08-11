#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from build_evidence_ledger_snapshot import (
    build_evidence_ledger_snapshot,
    sync_evidence_lineage,
    write_evidence_ledger_snapshot,
)
from init_db import initialize_database


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def seed(connection):
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, confidence, conflict_risk,
          status, created_at, updated_at
        )
        VALUES (
          'test-official', '测试官方来源', 'official_document',
          'https://example.com', '高', '低', 'active', '2026-07-31T00:00:00Z',
          '2026-07-31T00:00:00Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO projects (
          project_id, canonical_name, identity_status, first_seen_at,
          created_at, updated_at
        )
        VALUES (
          'project-test', 'Test Project', 'verified', '2026-07-31T00:00:00Z',
          '2026-07-31T00:00:00Z', '2026-07-31T00:00:00Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO candidate_cases (
          case_id, project_id, title, rule_version, created_at, updated_at
        )
        VALUES (
          'case-test', 'project-test', 'Test Project',
          'test-v1', '2026-07-31T00:00:00Z', '2026-07-31T00:00:00Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO raw_events (
          raw_event_id, source_id, ingestion_run_id, external_id,
          collected_at, content_hash, source_url, excerpt, project_hint,
          event_type, status
        )
        VALUES (
          'raw-test', 'test-official', 'convexity-bootstrap-v1', 'official-1',
          '2026-07-31T00:00:00Z', 'hash-test',
          'https://example.com/fact', '测试原始事实', 'Test Project',
          'official_fact', 'normalized'
        )
        """
    )
    evidence_values = [
        (
            "evidence-linked",
            "raw-test",
            "https://example.com/fact",
            "能够回到原始记录的证据",
        ),
        (
            "evidence-legacy-gap",
            None,
            "https://example.com/legacy",
            "历史上缺少原始记录的证据",
        ),
    ]
    connection.executemany(
        """
        INSERT INTO evidence_items (
          evidence_id, project_id, raw_event_id, evidence_type, stance,
          fact_boundary, confidence, observed_at, source_id, source_url,
          summary, created_at
        )
        VALUES (
          ?, 'project-test', ?, 'official_fact', 'support',
          'confirmed_fact', '中', '2026-07-31T00:00:00Z',
          'test-official', ?, ?, '2026-07-31T00:00:00Z'
        )
        """,
        evidence_values,
    )
    connection.execute(
        """
        INSERT INTO state_transitions (
          transition_id, case_id, from_state, to_state, reason,
          evidence_ids_json, rule_version, actor, transitioned_at
        )
        VALUES (
          'transition-test', 'case-test', 'shadow_signal', 'identity_pending',
          '测试证据引用',
          '["evidence-linked","evidence-legacy-gap"]',
          'test-v1', 'rule_engine', '2026-07-31T00:05:00Z'
        )
        """
    )
    connection.commit()


def expect_immutable(connection, sql, message):
    try:
        connection.execute(sql)
        connection.commit()
        raise AssertionError(message)
    except sqlite3.IntegrityError:
        connection.rollback()


def test_lineage_and_snapshot():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "test.db"
        runtime_path = root / "runtime.js"
        output_path = root / "ledger.js"
        initialize_database(db_path, runtime_path, backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            seed(connection)
            first = sync_evidence_lineage(connection)
            connection.commit()
            second = sync_evidence_lineage(connection)
            connection.commit()

            assert first["inserted"] == 5
            assert second["inserted"] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM evidence_lineage"
            ).fetchone()[0] == 5
            assert connection.execute(
                """
                SELECT COUNT(*) FROM evidence_lineage
                WHERE lineage_status = 'missing_raw'
                """
            ).fetchone()[0] == 2
            assert connection.execute(
                """
                SELECT COUNT(*) FROM evidence_lineage
                WHERE target_type = 'state_transition'
                  AND lineage_status = 'verified'
                """
            ).fetchone()[0] == 1

            snapshot = build_evidence_ledger_snapshot(connection)
            write_evidence_ledger_snapshot(snapshot, output_path)
            assert snapshot["counts"]["rawEvents"] == 1
            assert snapshot["counts"]["missingRawEvidenceItems"] == 1
            assert snapshot["counts"]["missingReferences"] == 0
            assert len(snapshot["records"]) == 1
            assert len(snapshot["gaps"]) == 1

            expect_immutable(
                connection,
                "UPDATE raw_events SET excerpt = 'changed' WHERE raw_event_id = 'raw-test'",
                "原始记录仍可被覆盖",
            )
            expect_immutable(
                connection,
                "DELETE FROM raw_events WHERE raw_event_id = 'raw-test'",
                "原始记录仍可被删除",
            )
            expect_immutable(
                connection,
                "UPDATE evidence_lineage SET detail = 'changed' WHERE lineage_id = (SELECT lineage_id FROM evidence_lineage LIMIT 1)",
                "证据关系仍可被覆盖",
            )
            assert "PENGUIN_CONVEXITY_EVIDENCE_LEDGER" in output_path.read_text(
                encoding="utf-8"
            )
        finally:
            connection.close()


def test_release_routes():
    html = (PROJECT_ROOT / "app" / "evidence-ledger.html").read_text(
        encoding="utf-8"
    )
    script = (PROJECT_ROOT / "app" / "evidence-ledger.js").read_text(
        encoding="utf-8"
    )
    navigation = (PROJECT_ROOT / "app" / "workbench-nav.js").read_text(
        encoding="utf-8"
    )
    workbench = (PROJECT_ROOT / "app" / "workbench.html").read_text(
        encoding="utf-8"
    )
    assert "RAW EVIDENCE LEDGER · C1.6-06" in html
    assert "原始记录不可覆盖" in html
    assert "历史缺口" in html
    assert "downstreamTargets" in script
    assert "const gapSources = new Map()" in script
    assert 'state.source = "all"' in script
    assert '["evidence-ledger.html", "原始证据"]' in navigation
    assert 'href="evidence-ledger.html"' in workbench
    assert '"evidence-ledger.html"' in navigation
    assert "rwa" not in Path(
        PROJECT_ROOT / "scripts" / "build_evidence_ledger_snapshot.py"
    ).read_text(encoding="utf-8").lower()


def main():
    test_lineage_and_snapshot()
    test_release_routes()
    print("C1.6-01 原始证据不可覆盖、关系回填、历史缺口与桌面路由测试通过。")


if __name__ == "__main__":
    main()
