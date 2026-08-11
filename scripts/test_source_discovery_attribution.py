#!/usr/bin/env python3
import hashlib
import sqlite3
import tempfile
from pathlib import Path

from init_db import initialize_database
from refresh_candidate_pool import persist_refresh
from source_discovery_attribution import (
    build_source_discovery_snapshot,
    discovery_record,
)
from sync_thread_candidates import load_fixture


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def stable_id(*parts):
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:24]
    return f"test-{digest}"


def test_bundle():
    records = [
        discovery_record(
            "defillama",
            "maple",
            "Maple Finance",
            slug="maple",
            website_url="https://maple.finance",
            source_url="https://defillama.com/protocol/maple",
            category="Lending",
        ),
        discovery_record(
            "defillama",
            "nova",
            "Nova Protocol",
            source_url="https://defillama.com/protocol/nova",
            category="DeFi",
        ),
        discovery_record(
            "snapshot",
            "nova.eth",
            "Nova DAO",
            source_url="https://snapshot.org/#/nova.eth",
            category="governance",
        ),
        discovery_record(
            "github",
            "solo/solo",
            "Solo",
            repository_url="https://github.com/solo/solo",
            source_url="https://github.com/solo/solo",
            category="web3",
        ),
        discovery_record(
            "defillama",
            "aurora-seed",
            "Aurora Seed",
            slug="aurora-seed",
            website_url="https://auroraseed.example",
            repository_url="https://github.com/aurora/seed",
            source_url="https://defillama.com/protocol/aurora-seed",
            category="DeFi",
            evidence={"tvlUsd": 2_000_000},
        ),
    ]
    return {
        "version": "C1.1-05-test",
        "records": records,
        "sourceStats": {
            "defillama": {
                "collected": 3,
                "pages": 1,
                "failed": 0,
                "boundary": "test",
                "upstreamLimit": "",
            },
            "snapshot": {
                "collected": 1,
                "pages": 1,
                "failed": 0,
                "boundary": "test",
                "upstreamLimit": "",
            },
            "github": {
                "collected": 1,
                "pages": 1,
                "failed": 0,
                "boundary": "test",
                "upstreamLimit": "",
            },
            "cactus": {
                "collected": 0,
                "pages": 1,
                "failed": 0,
                "boundary": "test",
                "upstreamLimit": "",
            },
        },
        "errors": [],
    }


def test_persistence_and_identity_boundaries():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        initialize_database(db_path, root / "runtime.js", backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute(
                """
                INSERT INTO projects (
                  project_id, canonical_name, website_domain, official_repo,
                  team_summary, identity_status, first_seen_at,
                  created_at, updated_at
                )
                VALUES (
                  'project-maple', 'Maple Finance', 'maple.finance', '',
                  '', 'verified', '2026-07-29T00:00:00Z',
                  '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z'
                )
                """
            )
            result = persist_refresh(
                connection,
                load_fixture(),
                [],
                [],
                "source-discovery-test-run",
                source_discovery_bundle=test_bundle(),
                task_id="source_discovery_refresh",
            )
            connection.commit()
            snapshot = build_source_discovery_snapshot(connection)
            rows = list(connection.execute("SELECT * FROM source_discoveries"))
            assets = connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            aurora_project = connection.execute(
                "SELECT * FROM projects WHERE canonical_name = 'Aurora Seed'"
            ).fetchone()
            aurora_case = connection.execute(
                """
                SELECT candidate.*
                FROM candidate_cases candidate
                JOIN projects project ON project.project_id = candidate.project_id
                WHERE project.canonical_name = 'Aurora Seed'
                """
            ).fetchone()
            first_project_count = connection.execute(
                "SELECT COUNT(*) FROM projects"
            ).fetchone()[0]
            first_case_count = connection.execute(
                "SELECT COUNT(*) FROM candidate_cases"
            ).fetchone()[0]
            second_result = persist_refresh(
                connection,
                load_fixture(),
                [],
                [],
                "source-discovery-test-run-2",
                source_discovery_bundle=test_bundle(),
                task_id="source_discovery_refresh",
            )
            connection.commit()
            second_project_count = connection.execute(
                "SELECT COUNT(*) FROM projects"
            ).fetchone()[0]
            second_case_count = connection.execute(
                "SELECT COUNT(*) FROM candidate_cases"
            ).fetchone()[0]
        finally:
            connection.close()

        assert result["sourceDiscoveriesCollected"] == 5
        assert result["sourceDiscoveriesMatchedExisting"] == 1
        assert result["sourceDiscoveriesAutoPromoted"] == 1
        assert result["sourceDiscoveriesCasesCreated"] == 1
        assert len(rows) == 5
        assert assets == 0
        assert aurora_project is not None
        assert aurora_case is not None
        assert aurora_case["asset_id"] is None
        assert aurora_case["workflow_state"] == "shadow_signal"
        assert aurora_case["action_stage"] == "只观察"
        assert aurora_case["rule_version"] == "convexity-auto-discovery-v1.0.0"
        assert second_result["sourceDiscoveriesAutoPromoted"] == 0
        assert second_result["sourceDiscoveriesCasesCreated"] == 0
        assert second_project_count == first_project_count
        assert second_case_count == first_case_count
        maple = next(item for item in snapshot["items"] if item["canonicalName"] == "Maple Finance")
        nova = next(item for item in snapshot["items"] if item["clusterKey"] == "name:nova")
        solo = next(item for item in snapshot["items"] if item["canonicalName"] == "Solo")
        assert maple["projectIdentityStatus"] == "verified"
        assert maple["assetIdentityStatus"] == "not_identified"
        assert maple["valueCaptureStatus"] == "unknown"
        assert nova["projectIdentityStatus"] == "corroborated"
        assert len(nova["sourceIds"]) == 2
        assert solo["projectIdentityStatus"] == "pending"
        assert snapshot["counts"]["rawDiscoveries"] == 5
        assert snapshot["counts"]["clusters"] == 4
        assert snapshot["counts"]["machineProjects"] == 1
        assert snapshot["counts"]["machineAssetNotIdentified"] == 1
        aurora_snapshot = next(
            item for item in snapshot["items"] if item["canonicalName"] == "Aurora Seed"
        )
        assert aurora_snapshot["machinePromoted"] is True
        assert aurora_snapshot["caseId"] == aurora_case["case_id"]
        assert aurora_snapshot["detailUrl"].startswith(
            "project-detail.html?id=project%3A"
        )


def test_static_entrypoints():
    html = (APP_ROOT / "source-discovery.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "source-discovery.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    assert "C1.5 无人值守机器结论版 · C1.5-05" in html
    assert "source-discovery-snapshot.js" in html
    assert "全部记录已保留在本地数据库" in html
    assert "pageSize = 100" in script
    assert "查看项目档案" in script
    assert "source-discovery.html" in workbench
    assert '["source-discovery.html", "机器发现"]' in navigation


def main():
    test_persistence_and_identity_boundaries()
    print("PASS 普通单源不自动建档，强结构化登记可自动建立观察档案")
    test_static_entrypoints()
    print("PASS C1.5-05 页面、工作台入口和桌面软件路由已接入")


if __name__ == "__main__":
    main()
