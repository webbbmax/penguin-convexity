#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from init_db import DEFAULT_DB_PATH
from reset_production_data import RESET_RUN_ID, reset_production_data
from sync_thread_candidates import (
    MACHINE_RULE_VERSION,
    RULE_VERSION,
    SOURCE_ID,
    build_pool_snapshot,
    load_fixture,
    machine_fixture,
    sync_candidates,
)


def insert_safe_configuration_and_machine_case(connection):
    now = "2026-07-30T00:00:00Z"
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope,
          confidence, conflict_risk, status, schedule_text,
          last_checked_at, created_at, updated_at
        )
        VALUES (
          'test-machine-source', '测试机器信源', 'test',
          'https://example.com', 'read-only', 'convexity',
          '中', '低', 'active', 'manual', ?, ?, ?
        )
        """,
        (now, now, now),
    )
    connection.execute(
        """
        INSERT INTO networks (
          network_id, name, chain_type, chain_id, environment,
          rpc_url, explorer_url, discovery_priority, status,
          source_url, created_at, updated_at
        )
        VALUES (
          'test-chain', '测试链', 'EVM', '999999', 'mainnet',
          '', '', 'common', 'active', 'https://example.com', ?, ?
        )
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO projects (
          project_id, canonical_name, identity_status, first_seen_at,
          created_at, updated_at
        )
        VALUES ('machine-project', 'Machine Project', 'verified', ?, ?, ?)
        """,
        (now, now, now),
    )
    connection.execute(
        """
        INSERT INTO candidate_cases (
          case_id, project_id, title, rule_version, created_at, updated_at
        )
        VALUES (
          'auto-case-machine-project', 'machine-project',
          '机器发现案例', ?, ?, ?
        )
        """,
        (MACHINE_RULE_VERSION, now, now),
    )
    connection.commit()


def test_clean_room_reset():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        pool_path = root / "candidate-pool-snapshot.js"
        runtime_path = root / "runtime-snapshot.js"
        archive_root = root / "archive"
        sync_candidates(
            db_path=db_path,
            fixture_path=Path(__file__).resolve().parent.parent
            / "fixtures"
            / "thread-candidate-seeds-v1.json",
            pool_snapshot_path=pool_path,
            runtime_snapshot_path=runtime_path,
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            insert_safe_configuration_and_machine_case(connection)
            production = build_pool_snapshot(
                connection,
                machine_fixture(),
                production_only=True,
            )
            assert production["counts"]["total"] == 1
            assert production["cases"][0]["caseId"] == "auto-case-machine-project"
            assert not production["sourceThreadId"]
        finally:
            connection.close()

        first = reset_production_data(
            db_path=db_path,
            archive_root=archive_root,
            rebuild_snapshots=False,
        )
        assert first["status"] == "cleaned"
        assert Path(first["archiveDatabase"]).exists()
        assert Path(first["manifest"]).exists()

        archived = sqlite3.connect(first["archiveDatabase"])
        try:
            assert archived.execute(
                "SELECT COUNT(*) FROM candidate_cases WHERE rule_version = ?",
                (RULE_VERSION,),
            ).fetchone()[0] == len(load_fixture()["records"])
            assert archived.execute(
                "SELECT COUNT(*) FROM candidate_cases"
            ).fetchone()[0] == len(load_fixture()["records"]) + 1
        finally:
            archived.close()

        clean = sqlite3.connect(db_path)
        try:
            assert clean.execute(
                "SELECT COUNT(*) FROM candidate_cases"
            ).fetchone()[0] == 0
            assert clean.execute(
                "SELECT COUNT(*) FROM projects"
            ).fetchone()[0] == 0
            assert clean.execute(
                "SELECT COUNT(*) FROM sources WHERE source_id = ?",
                (SOURCE_ID,),
            ).fetchone()[0] == 0
            assert clean.execute(
                "SELECT last_checked_at FROM sources "
                "WHERE source_id = 'test-machine-source'"
            ).fetchone()[0] is None
            assert clean.execute(
                "SELECT COUNT(*) FROM networks WHERE network_id = 'test-chain'"
            ).fetchone()[0] == 1
            assert clean.execute(
                "SELECT COUNT(*) FROM runs WHERE run_id = ?",
                (RESET_RUN_ID,),
            ).fetchone()[0] == 1
            assert clean.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            clean.close()

        second = reset_production_data(
            db_path=db_path,
            archive_root=archive_root,
            rebuild_snapshots=False,
        )
        assert second["status"] == "already_clean"
        assert len(list(archive_root.glob("*/manifest.json"))) == 1


def test_production_legacy_import_is_blocked():
    try:
        sync_candidates(db_path=DEFAULT_DB_PATH)
    except RuntimeError as error:
        assert "禁止" in str(error)
    else:
        raise AssertionError("正式数据库仍允许导入旧凸性任务答案")


def test_machine_rule_version_is_independent():
    assert MACHINE_RULE_VERSION != RULE_VERSION
    assert machine_fixture()["records"] == []
    assert "旧对话答案已经隔离" in machine_fixture()["importBoundary"]


def main():
    test_clean_room_reset()
    test_production_legacy_import_is_blocked()
    test_machine_rule_version_is_independent()
    print("C1.5-00 生产数据清场、只读归档与旧答案隔离测试通过。")


if __name__ == "__main__":
    main()
