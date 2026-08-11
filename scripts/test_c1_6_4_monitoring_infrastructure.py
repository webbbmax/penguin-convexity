#!/usr/bin/env python3
import shutil
import sqlite3
import tempfile
from pathlib import Path

from build_monitoring_infrastructure_snapshot import (
    build_monitoring_infrastructure_snapshot,
)
from high_value_sources import formal_project_targets
from init_db import DEFAULT_DB_PATH, initialize_database
from monitoring_infrastructure import (
    latest_monitoring_targets,
    persist_monitoring_targets,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def test_registry_rules_and_idempotence():
    with tempfile.TemporaryDirectory() as temporary_dir:
        db_path = Path(temporary_dir) / "convexity.db"
        snapshot_path = Path(temporary_dir) / "runtime.js"
        shutil.copy2(DEFAULT_DB_PATH, db_path)
        initialize_database(db_path, snapshot_path, backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            project = connection.execute(
                """
                SELECT project_id, canonical_name
                FROM projects
                WHERE identity_status = 'verified'
                ORDER BY project_id
                LIMIT 1
                """
            ).fetchone()
            connection.execute(
                """
                INSERT INTO source_discoveries (
                  source_discovery_id, source_id, external_id, canonical_name,
                  normalized_name, repository_url, source_url, first_seen_at,
                  last_seen_at, matched_project_id, project_identity_status,
                  attribution_confidence, attribution_reason, created_at,
                  updated_at
                )
                VALUES (
                  'monitoring-false-repository',
                  'discovery-github-repositories',
                  'unrelated-owner/not-official',
                  ?, 'notofficial',
                  'https://github.com/unrelated-owner/not-official',
                  'https://github.com/unrelated-owner/not-official',
                  '2026-07-31T05:59:00Z', '2026-07-31T05:59:00Z',
                  ?, 'verified', 'high',
                  '测试：项目主体已核验但仓库所有权没有官方锚点',
                  '2026-07-31T05:59:00Z', '2026-07-31T05:59:00Z'
                )
                """,
                (project["canonical_name"], project["project_id"]),
            )
            connection.commit()
            first = persist_monitoring_targets(
                connection,
                "2026-07-31T06:00:00Z",
            )
            second = persist_monitoring_targets(
                connection,
                "2026-07-31T06:01:00Z",
            )
            targets = latest_monitoring_targets(connection)
            project_total = connection.execute(
                "SELECT COUNT(*) FROM projects"
            ).fetchone()[0]
            duplicate_current = connection.execute(
                """
                SELECT COUNT(*)
                FROM (
                  SELECT target_identity_key
                  FROM project_monitoring_targets
                  WHERE publication_status = 'published'
                  GROUP BY target_identity_key
                  HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            schema_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            page_snapshot = build_monitoring_infrastructure_snapshot(
                connection
            )
        finally:
            connection.close()
        evidence_targets = formal_project_targets(db_path)

    assert first["projectsReviewed"] == project_total
    assert first["targetsPublished"] > 0
    assert second["recordsInserted"] == 0
    assert second["changedTargets"] == 0
    assert duplicate_current == 0
    assert schema_version == 17
    assert page_snapshot["counts"]["projects"] == project_total
    assert page_snapshot["counts"]["targets"] == len(targets)
    assert all(
        "project-detail.html?id=project%3A" in profile["detailUrl"]
        for profile in page_snapshot["projects"]
    )
    assert all(
        profile["blockedCount"] == 0
        for profile in page_snapshot["projects"]
        if profile["status"] == "ready"
    )
    assert all(
        item["relation_status"] in {"verified", "corroborated"}
        for item in targets
        if item["collection_status"] == "ready"
    )
    assert not any(
        "api-evangelist" in item["target_value"].lower()
        for item in targets
    ), "历史代码活动不能反向冒充官方监控目标"
    false_repository = next(
        item
        for item in targets
        if item["target_value"].lower()
        == "unrelated-owner/not-official"
    )
    assert false_repository["collection_status"] == "blocked"
    assert "官方" in false_repository["gap_reason"]
    assert evidence_targets["version"] == "C1.6-06"
    assert not any(
        "api-evangelist" in item["repository"].lower()
        for item in evidence_targets["github"]
    )
    assert not any(
        item["repository"].lower() == "unrelated-owner/not-official"
        for item in evidence_targets["github"]
    )


def test_ui_update_and_shell_integration():
    html = (
        APP_ROOT / "monitoring-infrastructure.html"
    ).read_text(encoding="utf-8")
    script = (
        APP_ROOT / "monitoring-infrastructure.js"
    ).read_text(encoding="utf-8")
    detail = (APP_ROOT / "project-detail.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    update_tasks = (
        PROJECT_ROOT / "scripts" / "update_tasks.py"
    ).read_text(encoding="utf-8")
    refresh = (
        PROJECT_ROOT / "scripts" / "refresh_candidate_pool.py"
    ).read_text(encoding="utf-8")
    engine = (
        PROJECT_ROOT / "scripts" / "monitoring_infrastructure.py"
    ).read_text(encoding="utf-8").lower()

    assert "项目监控基础设施" in html
    assert "身份或来源归属未通过" in html
    assert "原始记录" in script and "研究证据" in script
    assert "renderMonitoringInfrastructure" in detail
    assert 'id="detailMonitoringInfrastructure"' in detail
    assert 'href="monitoring-infrastructure.html"' in workbench
    assert '["monitoring-infrastructure.html", "项目监控基础设施"]' in navigation
    assert '"monitoring_infrastructure_refresh"' in update_tasks
    assert '"monitoring_infrastructure"' in refresh
    assert '"monitoring-infrastructure.html"' in navigation
    assert "rwa" not in engine


def main():
    test_registry_rules_and_idempotence()
    test_ui_update_and_shell_integration()
    print("C1.6-05 监控目标、身份门槛、证据引用、幂等与无代码入口测试通过。")


if __name__ == "__main__":
    main()
