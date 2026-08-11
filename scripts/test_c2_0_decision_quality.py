#!/usr/bin/env python3
"""Machine checks for the frozen C2.0 derived decision-quality layer."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DB = ROOT / "data" / "convexity.db"
FRONT = APP / "decision-signals-snapshot.js"
QUALITY = APP / "decision-quality-snapshot.js"
SCIENTIFIC = re.compile(r"(?<![A-Za-z0-9_.-])[-+]?\d+(?:\.\d+)?[eE][-+]?\d+(?![A-Za-z0-9_-])")
FORBIDDEN_FRONT = re.compile(r"(?:task|watcher|cursor|schedule|retry|log|score|prompt|credential)", re.IGNORECASE)
TIER_ORDER = {"must_read": 0, "worth_following": 1, "observe": 2}
IMPACT_ORDER = {"tighten": 0, "improve": 1, "no_change": 2, "": 3}
DIMENSION_ORDER = {"invalidation": 0, "risk": 1, "action": 2, "tradeability": 3, "exit": 4, "value_capture": 5, "remaining_convexity": 6, "ignition": 7, "evidence_maturity": 8, "market": 9}


def payload(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"=\s*(.*);\s*$", text, re.DOTALL)
    assert match, path
    result = json.loads(match.group(1))
    assert isinstance(result, dict), path
    return result


def walk_front(value):
    if isinstance(value, dict):
        for key, child in value.items():
            assert not FORBIDDEN_FRONT.search(str(key)), key
            yield from walk_front(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_front(child)
    elif isinstance(value, str):
        assert not SCIENTIFIC.search(value), value


def stamp(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def test_snapshots() -> None:
    front = payload(FRONT)
    quality = payload(QUALITY)
    assert front["schemaVersion"] == "c2.0-decision-signals-v1"
    assert quality["schemaVersion"] == "c2.0-decision-quality-v1"
    for field in ("buildId", "generatedAt", "sourceSnapshotAt", "inputRunIds"):
        assert front[field] == quality[field], field
    assert len(front["homeSignals"]) <= 5
    assert front["counts"]["homeSignals"] == len(front["homeSignals"])
    assert len(front["projects"]) == front["counts"]["projects"]
    assert len({item["projectId"] for item in front["projects"]}) == len(front["projects"])
    assert all(item.get("projectId") and item.get("caseId") for item in front["projects"])
    assert all(item.get("summaryComplete") and not item.get("missingSummaryParts") for item in front["homeSignals"])
    latest_chain = {}
    for chain in front["changeChains"]:
        latest_chain.setdefault(chain["projectId"], chain)

    def sort_key(item):
        chain = latest_chain.get(item["projectId"], {})
        action_changed_or_actionable = item.get("actionLabel") in {"普通建仓", "极限试仓"} or "action" in (chain.get("dimensions") or [])
        boundary = ((item.get("summary") or {}).get("strongestSupport") or {}).get("factBoundary")
        boundary_order = 0 if boundary == "confirmed_fact" else 1 if boundary == "high_confidence_inference" else 2
        return (
            TIER_ORDER[item["readingTier"]],
            0 if action_changed_or_actionable else 1,
            IMPACT_ORDER.get(item.get("impact", ""), 3),
            min((DIMENSION_ORDER.get(value, 99) for value in (chain.get("dimensions") or [])), default=99),
            boundary_order,
            -stamp(chain.get("endedAt") or item.get("evidenceTime")),
            item.get("projectName", "").lower(),
        )

    assert front["projects"] == sorted(front["projects"], key=sort_key)
    assert all(1 <= len(item.get("sortReasons") or []) <= 3 for item in front["projects"])
    assert all(
        chain.get("impact") == "no_change"
        for chain in front["changeChains"]
        if set(chain.get("dimensions") or []) == {"market"}
    )
    serialized_changes = json.dumps(front["changeChains"], ensure_ascii=False)
    assert not re.search(r"machine_rule|read_only_verified|关注顺序分|错配分|模型动作|综合方向|研究观察|失效与排除", serialized_changes)
    list(walk_front(front))
    assert quality["reconciliation"]["countsMatch"] is True
    assert quality["reconciliation"]["funnelRowsReconciled"] is True
    assert len(quality["coverageFunnel"]) == 7
    for row in quality["coverageFunnel"]:
        assert row["closed"] + row["systemPending"] + row["humanPending"] == row["total"]
        assert row["reconciled"] is True
    for metric in quality["qualityMetrics"]:
        assert {"numerator", "denominator", "definition", "generatedAt"}.issubset(metric)
    queue = quality["closureQueue"]
    assert queue
    assert all({"taskId", "latestExecutionAt", "latestResult", "nextReviewAt", "targetUrl"}.issubset(item) for item in queue)
    assert sum(bool(item.get("taskId")) for item in queue) >= int(len(queue) * 0.9)
    assert sum(bool(item.get("latestExecutionAt")) for item in queue) >= int(len(queue) * 0.9)
    assert any(item.get("rawEventIds") for item in quality["traceIndex"].values())


def test_db_and_routes() -> None:
    roles = json.loads((ROOT / "docs" / "C2.0_ROUTE_ROLES.json").read_text(encoding="utf-8"))["roles"]
    active = {path.name for path in APP.glob("*.html")}
    active.discard("new-token-update.html")
    active.add("desktop/index.html")
    assert set(roles) == active
    assert roles["decision-quality.html"] == "admin"
    assert len(roles) == 29
    assert Counter(roles.values()) == Counter({"front": 2, "context-detail": 1, "host-only": 1, "admin": 25})
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == payload(FRONT)["counts"]["projects"]
        assert connection.execute("SELECT COUNT(*) FROM candidate_cases").fetchone()[0] == payload(FRONT)["counts"]["cases"]
        assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0] == 54
    finally:
        connection.close()
    lock = json.loads((ROOT / "docs" / "C2.0_BASELINE_MANIFEST.json").read_text(encoding="utf-8"))["immutableCoreFiles"]
    for relative, expected in lock.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative


def test_atomic_failure() -> None:
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (FRONT, QUALITY)}
    result = subprocess.run([sys.executable, "scripts/build_decision_quality_snapshots.py", "--simulate-failure", "after_front"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0, result.stdout
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (FRONT, QUALITY)}
    assert after == before
    assert not list(APP.glob(".decision-*-snapshot.js.*.tmp"))
    assert not list(APP.glob(".decision*.tmp"))


def test_syntax() -> None:
    assert SCIENTIFIC.search("1e+06")
    assert SCIENTIFIC.search("值为 -1e6。")
    assert not SCIENTIFIC.search("c2.0-758e608456")
    for relative in ("app/c2-front.js", "app/c2-quality.js", "app/workbench-nav.js"):
        result = subprocess.run(["node", "--check", str(ROOT / relative)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    front_source = (ROOT / "app" / "c2-front.js").read_text(encoding="utf-8")
    quality_source = (ROOT / "app" / "c2-quality.js").read_text(encoding="utf-8")
    css_source = (ROOT / "app" / "c2-0.css").read_text(encoding="utf-8")
    navigation_source = (ROOT / "app" / "workbench-nav.js").read_text(encoding="utf-8")
    assert "item.impactLabel" in front_source
    assert "decisionValue(signal.tradeabilityStatus)" in front_source
    assert "c2-detail-summary-grid" in front_source and "c2-detail-summary-grid" in css_source
    assert 'getElementById("c2QueueBody").addEventListener("click"' in quality_source
    assert navigation_source.count("当前版本 C2.2") == 1
    assert all(not re.search(r"当前版本 C2\.\d+", (APP / name).read_text(encoding="utf-8")) for name in ("candidate-pool.html", "change-explanations.html", "project-detail.html"))


if __name__ == "__main__":
    tests = [test_snapshots, test_db_and_routes, test_atomic_failure, test_syntax]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
