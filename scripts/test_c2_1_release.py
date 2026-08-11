#!/usr/bin/env python3

import hashlib
import json
import re
import sqlite3
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from c2_1_runtime import load_config, update_config


ROOT = Path(__file__).resolve().parent.parent
VALID_SOURCE_STATES = {"success", "no_data", "quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure"}
VALID_DISPLAY_STATES = {"data_limited", "convexity_clue", "active_project", "early_observation", "continuous_observation"}


def load_js(path, prefix):
    text = path.read_text(encoding="utf-8")
    assert text.startswith(prefix)
    return json.loads(text[len(prefix):].strip().removesuffix(";"))


class FrontNavParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_front_nav = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "nav" and values.get("class") == "c19-front-nav":
            self.in_front_nav = True
        elif tag == "a" and self.in_front_nav:
            self.links.append(values.get("href"))

    def handle_endtag(self, tag):
        if tag == "nav" and self.in_front_nav:
            self.in_front_nav = False


def main():
    lock = json.loads((ROOT / "docs" / "C2.1_REQUIREMENTS_LOCK.json").read_text(encoding="utf-8-sig"))
    canonical = []
    for item in lock["documents"]:
        path = ROOT / item["path"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == item["sha256"], item["path"]
        canonical.append(f"{item['path']}:{digest}")
    assert hashlib.sha256("\n".join(canonical).encode()).hexdigest() == lock["requirementSetSha256"]

    baseline = json.loads((ROOT / "docs" / "C2.1_IMPLEMENTATION_BASELINE.json").read_text(encoding="utf-8-sig"))
    main_path = ROOT / baseline["productionDatabase"]["path"]
    connection = sqlite3.connect(f"file:{main_path.as_posix()}?mode=ro", uri=True)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == baseline["productionDatabase"]["projects"]
    assert connection.execute("SELECT COUNT(*) FROM candidate_cases").fetchone()[0] == baseline["productionDatabase"]["candidateCases"]
    connection.close()
    scheduler_config = ROOT / baseline["existingSchedulerGuard"]["configPath"]
    assert hashlib.sha256(scheduler_config.read_bytes()).hexdigest() == baseline["existingSchedulerGuard"]["configSha256"]

    pipeline_path = ROOT / "data" / "c2.1-pipeline.db"
    connection = sqlite3.connect(pipeline_path)
    connection.row_factory = sqlite3.Row
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] >= baseline["gate0AcceptedRun"]["candidateRows"]
    assert not connection.execute("SELECT 1 FROM source_health WHERE status NOT IN ('success','no_data','quota_limited','source_failure','unsupported','configuration_missing','program_failure') LIMIT 1").fetchone()
    assert not connection.execute("SELECT 1 FROM source_cursors WHERE status IN ('source_failure','quota_limited') AND next_retry_at IS NULL LIMIT 1").fetchone()
    latest_build = connection.execute("SELECT * FROM snapshot_builds ORDER BY generated_at DESC LIMIT 1").fetchone()
    assert latest_build and latest_build["front_visible_count"] == latest_build["hard_gate_passed_count"]
    connection.close()

    front = load_js(ROOT / "app" / "c2-1-front-snapshot.js", "window.PENGUIN_CONVEXITY_C21 = ")
    backend = load_js(ROOT / "app" / "c2-1-admin-snapshot.js", "window.PENGUIN_CONVEXITY_C21_ADMIN = ")
    assert front["buildId"] == backend["buildId"]
    assert len(front["items"]) == front["coverageSummary"]["frontVisibleCount"] == front["coverageSummary"]["hardGatePassedCount"]
    for item in front["items"]:
        assert item["relationshipClass"] in {"A", "B", "C"}
        assert 0 <= item["ageDays"] <= 90
        assert item["hardGate"]["status"] in {"pass", "stale"}
        assert item["displayState"]["code"] in VALID_DISPLAY_STATES
        if item["displayState"]["code"] == "convexity_clue":
            formed = [path["pathCode"] for path in item["evidencePaths"] if path["status"] == "formed"]
            assert "trade_liquidity_formation" in formed and len(formed) >= 2
        if item["displayState"]["code"] == "data_limited":
            assert item["sourceImpact"]["status"] != "healthy"
    assert not any(item["relationshipClass"] == "D" for item in front["items"])

    parser = FrontNavParser()
    parser.feed((ROOT / "app" / "candidate-pool.html").read_text(encoding="utf-8"))
    assert len(parser.links) == 4
    assert parser.links == ["candidate-pool.html", "candidate-pool.html?view=all", "candidate-pool.html?view=changes", "candidate-pool.html?view=method"]
    css = (ROOT / "app" / "c2-1.css").read_text(encoding="utf-8")
    assert not re.search(r"@media\s*\([^)]*(?:max-width|min-width|width\s*:)", css, re.I)
    assert 'href !== "manual-review.html"' in (ROOT / "app" / "workbench-nav.js").read_text(encoding="utf-8")
    runtime = (ROOT / "scripts" / "c2_1_runtime.py").read_text(encoding="utf-8")
    assert "CREATE_NO_WINDOW" in runtime and "DETACHED_PROCESS" in runtime
    hidden = (ROOT / "scripts" / "run-c2-1-update-hidden.vbs").read_text(encoding="utf-8-sig")
    assert "shell.Run command, 0, True" in hidden and "--trigger automatic" in hidden
    installer = (ROOT / "scripts" / "install-c2.1-scheduler.ps1").read_text(encoding="utf-8-sig")
    assert 'taskName = "PenguinConvexity-C1.8-Scheduler"' in installer
    assert '/SC MINUTE /MO 15' in installer and 'MultipleInstances = "IgnoreNew"' in installer
    server = (ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
    assert "rebuild_data_backbone()" not in server
    assert "build_data_backbone_snapshot(connection)" in server and "?mode=ro" in server

    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "config.json"
        update_config({"mode": "automatic", "intervalHours": 6}, config_path)
        assert load_config(config_path)["intervalHours"] == 6
        update_config({"mode": "manual"}, config_path)
        assert load_config(config_path)["intervalHours"] is None
    print("C2.1 release-readiness tests passed")


if __name__ == "__main__":
    main()
