#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_project_master_pool import (
    add_months,
    build_master_pool_snapshot,
    lifecycle_context,
    lifecycle_fields,
    project_lifecycle,
    write_master_pool_snapshot,
)
from project_identity_aliases import sync_project_identity_aliases
from sync_thread_candidates import sync_candidates


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_lifecycle_boundaries():
    today = datetime.now(timezone.utc).date()
    thresholds = {"earlyMonths": 6, "ogYears": 5}
    six_month_cutoff = add_months(today, -6)
    five_year_cutoff = add_months(today, -60)
    exact_six_months = lifecycle_fields(
        {"launchDate": six_month_cutoff.isoformat(), "dateStatus": "verified"},
        thresholds,
    )
    still_early = lifecycle_fields(
        {
            "launchDate": (six_month_cutoff + timedelta(days=1)).isoformat(),
            "dateStatus": "verified",
        },
        thresholds,
    )
    exact_five_years = lifecycle_fields(
        {"launchDate": five_year_cutoff.isoformat(), "dateStatus": "verified"},
        thresholds,
    )
    pending = lifecycle_fields({}, thresholds)
    assert exact_six_months["lifecycleBucket"] == "other"
    assert still_early["lifecycleBucket"] == "early"
    assert exact_five_years["lifecycleBucket"] == "og"
    assert pending["lifecycleBucket"] == "other"
    assert pending["lifecycleDateStatus"] == "pending"


def test_generic_identity_rebinding(connection, root):
    now = "2026-07-31T00:00:00Z"
    project_id = "generated-project-id-001"
    connection.execute(
        """
        INSERT INTO projects (
          project_id, canonical_name, website_domain, official_repo,
          team_summary, identity_status, first_seen_at, created_at, updated_at
        )
        VALUES (?, 'Arbitrary Legacy', 'arbitrary.example', '', '',
                'verified', ?, ?, ?)
        """,
        (project_id, now, now, now),
    )
    fixture_path = root / "generic-lifecycle.json"
    fixture_path.write_text(
        json.dumps(
            {
                "thresholds": {"earlyMonths": 6, "ogYears": 5},
                "projects": {
                    "arbitrary-legacy": {
                        "launchDate": "2018-01-01",
                        "dateStatus": "verified",
                        "dateBasis": "通用旧身份键回归样本",
                        "sourceName": "测试样本",
                        "sourceUrl": "https://arbitrary.example/history",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache_path = root / "empty-lifecycle-cache.json"
    cache_path.write_text('{"projects": {}}', encoding="utf-8")
    summary = sync_project_identity_aliases(connection, observed_at=now)
    context = lifecycle_context(connection, fixture_path, cache_path)
    lifecycle = project_lifecycle(project_id, context)
    assert summary["records"] > 0
    assert connection.execute(
        """
        SELECT COUNT(*)
        FROM project_identity_aliases
        WHERE project_id = ? AND normalized_value = 'arbitrarylegacy'
        """,
        (project_id,),
    ).fetchone()[0] >= 1
    assert lifecycle["lifecycleBucket"] == "og"
    assert lifecycle["lifecycleDate"] == "2018-01-01"


def main():
    test_lifecycle_boundaries()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        snapshot_path = root / "master-pool.js"
        sync_candidates(
            db_path=db_path,
            pool_snapshot_path=root / "candidate-pool.js",
            runtime_snapshot_path=root / "runtime.js",
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            test_generic_identity_rebinding(connection, root)
            for index in range(125):
                project_id = f"unlimited-test-{index:03d}"
                connection.execute(
                    """
                    INSERT INTO projects (
                      project_id, canonical_name, website_domain, official_repo,
                      team_summary, identity_status, first_seen_at, created_at, updated_at
                    )
                    VALUES (?, ?, '', '', '', 'pending', ?, ?, ?)
                    """,
                    (
                        project_id,
                        f"Unlimited Test {index:03d}",
                        "2026-07-29T00:00:00Z",
                        "2026-07-29T00:00:00Z",
                        "2026-07-29T00:00:00Z",
                    ),
                )
            connection.commit()
            snapshot = build_master_pool_snapshot(connection)
            write_master_pool_snapshot(snapshot, snapshot_path)
            database_project_count = connection.execute(
                "SELECT COUNT(*) FROM projects"
            ).fetchone()[0]
            assert snapshot["counts"]["projects"] == database_project_count
            assert snapshot["counts"]["projects"] > 100
            assert len(snapshot["records"]) == snapshot["counts"]["total"]
            assert "productVersion" not in snapshot
            assert "不设置项目数量上限" in snapshot["noQuotaPolicy"]
            assert snapshot["lifecyclePolicy"]["early"].startswith("公开启动未满6个月")
            assert sum(
                snapshot["counts"][key] for key in ("early", "og", "other")
            ) == snapshot["counts"]["total"]
            assert all(
                item["lifecycleBucket"] in {"early", "og", "other"}
                for item in snapshot["records"]
            )
            assert all(
                item["recordType"] != "discovery"
                or item["lifecycleBucket"] == "other"
                for item in snapshot["records"]
            )
            assert {
                "scan_results",
                "manual_annotations",
                "publication_records",
            }.issubset(
                {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            )
        finally:
            connection.close()

        snapshot_text = snapshot_path.read_text(encoding="utf-8")
        assert "PENGUIN_CONVEXITY_MASTER_POOL" in snapshot_text
        assert "Unlimited Test 124" in snapshot_text

    app_root = PROJECT_ROOT / "app"
    html = (app_root / "project-master-pool.html").read_text(encoding="utf-8")
    script = (app_root / "project-master-pool.js").read_text(encoding="utf-8")
    assert "不设项目数量上限" in html
    assert "公开启动未满6个月" in html
    assert 'id="masterDateFilter"' in html
    assert 'id="masterMarketCapFilter"' in html
    assert 'id="masterFdvFilter"' in html
    assert 'id="masterLiquidityFilter"' in html
    assert 'id="masterSort"' in html
    assert "R2.0" not in html and "C1.0" not in html
    assert "state.records.filter" in script
    opportunity_script = (app_root / "candidate-pool.js").read_text(
        encoding="utf-8"
    )
    opportunity_html = (app_root / "candidate-pool.html").read_text(
        encoding="utf-8"
    )
    assert "resetDirectoryFilters" in opportunity_script
    assert "applyDirectoryContext" in opportunity_script
    assert 'id="opportunityFilterContext"' in opportunity_html
    for page in (
        "network-discovery.html",
        "data-dictionary.html",
        "rules-replay.html",
        "real-case-calibration.html",
    ):
        assert "project-master-pool.html" in (app_root / page).read_text(encoding="utf-8")
    assert "project-master-pool.html" not in (
        app_root / "candidate-pool.html"
    ).read_text(encoding="utf-8")
    print("project master pool checks passed")


if __name__ == "__main__":
    main()
