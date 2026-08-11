#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
DB_PATH = PROJECT_ROOT / "data" / "convexity.db"


def read_js(path, prefix):
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text[len(prefix):].removesuffix(";"))


def main():
    connection = sqlite3.connect(DB_PATH)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "project_identity_aliases" in tables
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 11"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM project_identity_aliases WHERE status = 'active'"
        ).fetchone()[0] > 0
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()

    master = read_js(
        APP_ROOT / "project-master-pool-snapshot.js",
        "window.PENGUIN_CONVEXITY_MASTER_POOL = ",
    )
    opportunity = read_js(
        APP_ROOT / "opportunity-center-snapshot.js",
        "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = ",
    )
    assert master["counts"]["early"] > 0
    assert master["counts"]["og"] > 0
    assert sum(
        master["counts"][key] for key in ("early", "og", "other")
    ) == master["counts"]["total"]
    transferred = next(
        group
        for group in opportunity["conclusionBoard"]["groups"]
        if group["id"] == "transferred"
    )
    assert transferred["count"] == len(transferred["caseIds"])
    assert transferred["count"] >= 1

    aliases = (PROJECT_ROOT / "scripts" / "project_identity_aliases.py").read_text(
        encoding="utf-8"
    )
    lifecycle = (
        PROJECT_ROOT / "scripts" / "build_project_master_pool.py"
    ).read_text(encoding="utf-8")
    opportunity_script = (APP_ROOT / "candidate-pool.js").read_text(
        encoding="utf-8"
    )
    assert "uniswap" not in aliases.casefold()
    assert "uniswap" not in lifecycle.casefold()
    assert "resetDirectoryFilters" in opportunity_script
    assert "applyDirectoryContext" in opportunity_script
    assert 'id="opportunityFilterContext"' in (
        APP_ROOT / "candidate-pool.html"
    ).read_text(encoding="utf-8")

    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    shell = (PROJECT_ROOT / "desktop" / "index.html").read_text(encoding="utf-8")
    server = (PROJECT_ROOT / "scripts" / "serve_local.py").read_text(
        encoding="utf-8"
    )
    assert "CONVEXITY WORKSPACE · C1.7" in workbench
    assert 'data-page="workbench.html"' in shell
    assert "C1.7" in shell
    assert 'CONVEXITY_RELEASE = "C1.7"' in server
    print("C1.6-01 身份重绑定、生命周期分类和机会中心可见性测试通过。")


if __name__ == "__main__":
    main()
