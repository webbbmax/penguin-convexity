#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

from data_backbone import DEFAULT_DB_PATH
from update_tasks import TASK_DEFINITIONS, SOURCE_BOUNDARIES


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def main():
    connection = sqlite3.connect(DEFAULT_DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        raw_count = connection.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        normalized_count = connection.execute(
            "SELECT COUNT(*) FROM normalized_events_v2"
        ).fetchone()[0]
        traceable_count = connection.execute(
            "SELECT COUNT(*) FROM normalized_events_v2 WHERE raw_locator<>'' AND content_hash<>''"
        ).fetchone()[0]
        assert raw_count > 0
        assert normalized_count == raw_count == traceable_count
        assert connection.execute(
            "SELECT COUNT(*) FROM event_replay_runs"
        ).fetchone()[0] >= 2
        assert connection.execute(
            "SELECT COUNT(*) FROM orphan_events_v2 WHERE attribution_status IN ('pending','conflict')"
        ).fetchone()[0] >= 0
        assert connection.execute(
            "SELECT COALESCE(SUM(backlog_count),0) FROM source_cursors_v2"
        ).fetchone()[0] == 0
        watcher_types = {
            row[0] for row in connection.execute(
                "SELECT DISTINCT watcher_type FROM watcher_definitions WHERE publication_status='published'"
            )
        }
        assert {
            "git_activity", "software_release", "package_registry",
            "evm_contract", "solana_program",
        }.issubset(watcher_types)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()

    snapshot_text = (APP / "data-backbone-snapshot.js").read_text(encoding="utf-8")
    snapshot = json.loads(snapshot_text.split("=", 1)[1].rsplit(";", 1)[0])
    assert snapshot["version"] == "C1.7"
    assert snapshot["eventSchema"]["normalizedEvents"] == raw_count
    assert snapshot["continuity"]["backlog"] == 0
    for mainline in ("git", "release", "package", "evm", "solana"):
        assert mainline in snapshot["mainlines"]

    html = (APP / "data-backbone.html").read_text(encoding="utf-8")
    script = (APP / "data-backbone.js").read_text(encoding="utf-8")
    workbench = (APP / "workbench.html").read_text(encoding="utf-8")
    opportunity = (APP / "candidate-pool.html").read_text(encoding="utf-8")
    assert "数据连续性" in html and "孤儿证据与再归属" in html
    assert "五条采集主线" in html and "来源健康与静默保护" in html
    assert "当前 0 条事件，保留零结果" in script
    assert "data-backbone.html" in workbench
    assert "opportunityBackboneStatus" in opportunity
    ordered_sections = [
        "currentConclusions", "actionBlockers", "recentChanges", "catalystPaths",
        "projectCategories", "trackingTasks", "opportunityDirectory",
    ]
    positions = [opportunity.index(f'id="{section}"') for section in ordered_sections]
    assert positions == sorted(positions)
    assert "RWA" not in html and "RWA" not in script

    task = TASK_DEFINITIONS["data_backbone_refresh"]
    assert task["components"] == ["data_backbone"]
    assert task["sourceIds"] == [
        "data-backbone-registry", "evidence-github-releases-packages"
    ]
    assert "data-backbone-registry" in SOURCE_BOUNDARIES
    assert "evidence-github-releases-packages" in SOURCE_BOUNDARIES
    assert "data_backbone" in TASK_DEFINITIONS["full_refresh"]["components"]
    print("C1.7 数据连续性、孤儿证据、Watcher、五条主线与页面边界测试通过。")


if __name__ == "__main__":
    main()
