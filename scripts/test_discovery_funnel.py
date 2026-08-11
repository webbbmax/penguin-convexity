#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
sys.path.insert(0, str(ROOT / "scripts"))

from build_discovery_funnel_snapshot import (  # noqa: E402
    DEFAULT_FOUR_LAYER_PATH,
    blocker_for,
    build_snapshot,
)
from init_db import DEFAULT_DB_PATH  # noqa: E402


def item(project="pending", asset="not_identified", capture="unknown"):
    return {
        "projectIdentityStatus": project,
        "assetIdentityStatus": asset,
        "valueCaptureStatus": capture,
    }


def test_first_blocker_rules():
    assert blocker_for(item())["blocker"] == "identity_single_source"
    assert blocker_for(item(project="corroborated"))["blocker"] == "identity_corroborated_only"
    assert blocker_for(item(project="verified", asset="pending"))["blocker"] == "asset_pending"
    assert blocker_for(item(project="verified", asset="verified"))["blocker"] == "value_capture_unknown"
    assert blocker_for(
        item(project="verified", asset="verified", capture="verified")
    )["blocker"] == "no_research_case"
    assert blocker_for(
        item(project="verified", asset="verified", capture="verified"),
        case={"case_id": "case-1"},
    )["blocker"] == "no_four_layer_result"
    assert blocker_for(
        item(project="verified", asset="verified", capture="verified"),
        case={"case_id": "case-1"},
        four_layer={"actionCategory": "ordinary"},
    )["blocker"] == "action_ready"


def test_live_snapshot_invariants():
    connection = sqlite3.connect(DEFAULT_DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_snapshot(connection, DEFAULT_FOUR_LAYER_PATH)
    finally:
        connection.close()
    assert snapshot["version"] == "C1.1-06"
    assert snapshot["counts"]["total"] == len(snapshot["items"])
    assert sum(item["count"] for item in snapshot["blockers"]) == len(snapshot["items"])
    stage_passes = [stage["passed"] for stage in snapshot["stages"]]
    assert stage_passes == sorted(stage_passes, reverse=True)
    assert snapshot["counts"]["projectVerified"] <= snapshot["counts"]["total"]
    assert snapshot["counts"]["assetVerified"] <= snapshot["counts"]["projectVerified"]
    assert snapshot["counts"]["valueCaptureVerified"] <= snapshot["counts"]["assetVerified"]
    assert all(
        item["blocker"] != "action_ready"
        for item in snapshot["items"]
        if item["valueCaptureStatus"] != "verified"
    )
    assert snapshot["separateCandidateBranch"]["count"] >= 0


def test_static_entrypoints():
    html = (APP_ROOT / "discovery-funnel.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "discovery-funnel.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    assert "C1.1 凸性发现质量升级 · C1.1-06" in html
    assert "discovery-funnel-snapshot.js" in html
    assert "pageSize = 100" in script
    assert "discovery-funnel.html" in workbench
    assert '["discovery-funnel.html", "发现漏斗"]' in navigation


def main():
    test_first_blocker_rules()
    test_live_snapshot_invariants()
    test_static_entrypoints()
    print("discovery funnel checks passed")


if __name__ == "__main__":
    main()
