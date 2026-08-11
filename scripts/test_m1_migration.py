#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

from project_identity_aliases import sync_project_identity_aliases


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def test_database_and_history():
    database = ROOT / "data" / "convexity.db"
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        project_count = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        candidate_count = connection.execute("SELECT COUNT(*) FROM candidate_cases").fetchone()[0]
        evidence_count = connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0]
        assert project_count >= 585
        assert candidate_count >= project_count
        assert evidence_count >= 2519
        assert connection.execute("SELECT COUNT(*) FROM evidence_lineage").fetchone()[0] >= 44719
        assert connection.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0] >= 12446
    finally:
        connection.close()
    assert len(list((ROOT / "backups").glob("*.db"))) >= 1
    assert len(list((ROOT / "archive").rglob("*.db"))) >= 1


def test_alias_snapshot_rebuild_is_idempotent():
    database = ROOT / "data" / "convexity.db"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        before = connection.execute(
            "SELECT alias_id, updated_at FROM project_identity_aliases "
            "ORDER BY alias_id"
        ).fetchall()
        connection.execute("SAVEPOINT m1_alias_idempotency")
        sync_project_identity_aliases(
            connection,
            observed_at="2099-01-01T00:00:00Z",
        )
        after = connection.execute(
            "SELECT alias_id, updated_at FROM project_identity_aliases "
            "ORDER BY alias_id"
        ).fetchall()
        connection.execute("ROLLBACK TO m1_alias_idempotency")
        connection.execute("RELEASE m1_alias_idempotency")
        assert [tuple(row) for row in after] == [tuple(row) for row in before]
    finally:
        connection.close()


def test_independent_desktop_boundary():
    html = (ROOT / "desktop" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "desktop" / "shell.js").read_text(encoding="utf-8")
    server = (ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "launch-convexity.ps1").read_text(encoding="utf-8")
    assert html.count("<button") == 6
    assert html.count('class="is-active" type="button" data-page=') == 1
    assert html.count("<nav class=\"primary-nav\">") == 1
    assert html.count("<i>") == 3
    assert 'src="/candidate-pool.html"' in html
    assert "RWA" not in html
    assert "今日结论" not in html
    assert '"candidate-pool.html"' in script
    assert '"project-detail.html"' in script
    assert '"workbench.html"' in script
    assert "project-radar-site" not in server
    assert "project-radar-site" not in launcher
    assert 'default=8766' in server
    assert "PenguinResearchConvexityDesktopLauncher" in launcher
    assert 'Join-Path $runtimeRoot "logs"' in launcher
    assert "runtimeRoot" in server


def test_frozen_opportunity_order():
    html = (APP / "candidate-pool.html").read_text(encoding="utf-8")
    sections = (
        'id="currentConclusions"',
        'id="actionBlockers"',
        'id="recentChanges"',
        'id="catalystPaths"',
        'id="projectCategories"',
        'id="trackingTasks"',
        'id="opportunityDirectory"',
    )
    positions = [html.index(section) for section in sections]
    assert positions == sorted(positions)


def test_shared_api_catalog_boundary():
    catalog = json.loads(
        (ROOT / "config" / "shared-api-catalog.json").read_text(encoding="utf-8")
    )
    allowed = set(catalog["allowedFields"])
    assert len(catalog["resources"]) == 18
    for resource in catalog["resources"]:
        assert set(resource) == allowed
        assert "RWA" not in json.dumps(resource, ensure_ascii=False)
        for forbidden in ("health", "cursor", "enabled", "status", "consumers", "test"):
            assert forbidden not in resource
    for local_path in (
        ROOT / "data" / "source-discovery-cursors.json",
        ROOT / "data" / "update-runtime-status.json",
        ROOT / "data" / "convexity.db",
        ROOT / "runtime" / "logs",
        ROOT / "runtime" / "cache",
    ):
        assert local_path.exists(), local_path


def main():
    test_database_and_history()
    test_alias_snapshot_rebuild_is_idempotent()
    test_independent_desktop_boundary()
    test_frozen_opportunity_order()
    test_shared_api_catalog_boundary()
    print("M1.0 data, desktop, seven-section order and API boundary tests passed.")


if __name__ == "__main__":
    main()
