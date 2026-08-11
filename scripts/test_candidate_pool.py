#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from pathlib import Path

from sync_thread_candidates import (
    DEFAULT_FIXTURE_PATH,
    PROJECT_ROOT,
    sync_candidates,
)


def read_snapshot(path):
    text = Path(path).read_text(encoding="utf-8")
    prefix = "window.PENGUIN_CONVEXITY_CANDIDATES = "
    assert text.startswith(prefix)
    return json.loads(text[len(prefix) :].rstrip().rstrip(";"))


def main():
    fixture = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(fixture["records"]) == 20
    assert len({item["caseId"] for item in fixture["records"]}) == 20

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        pool_snapshot_path = root / "candidate-pool-snapshot.js"
        runtime_snapshot_path = root / "runtime-snapshot.js"
        result = sync_candidates(
            db_path=db_path,
            pool_snapshot_path=pool_snapshot_path,
            runtime_snapshot_path=runtime_snapshot_path,
        )
        assert result["records"] == 20
        assert result["evidence"] >= 20

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 20
            assert connection.execute("SELECT COUNT(*) FROM candidate_cases").fetchone()[0] == 20
            assert connection.execute("SELECT COUNT(*) FROM convexity_reviews").fetchone()[0] == 20
            assert connection.execute("SELECT COUNT(*) FROM decision_reports").fetchone()[0] == 20

            cowl = connection.execute(
                """
                SELECT workflow_state, action_stage, liquidity_grade
                FROM candidate_cases
                WHERE case_id = 'thread-cowl-20260728'
                """
            ).fetchone()
            assert dict(cowl) == {
                "workflow_state": "tradeability_pending",
                "action_stage": "只观察",
                "liquidity_grade": "extreme",
            }

            uni = connection.execute(
                """
                SELECT workflow_state, action_stage
                FROM candidate_cases
                WHERE case_id = 'thread-uni-20260728'
                """
            ).fetchone()
            assert dict(uni) == {
                "workflow_state": "trial_ready",
                "action_stage": "普通建仓",
            }

            hashi = connection.execute(
                """
                SELECT asset_id, workflow_state
                FROM candidate_cases
                WHERE case_id = 'thread-hashi-20260724'
                """
            ).fetchone()
            assert hashi["asset_id"] is None
            assert hashi["workflow_state"] == "shadow_signal"
        finally:
            connection.close()

        snapshot = read_snapshot(pool_snapshot_path)
        assert snapshot["counts"]["total"] == 20
        assert snapshot["counts"]["ordinary"] == 2
        assert snapshot["counts"]["extremeReview"] == 1
        assert snapshot["counts"]["actionableExtreme"] == 0
        assert snapshot["counts"]["active"] + snapshot["counts"]["transferred"] == 20
        assert snapshot["counts"]["qualified"] == snapshot["gateScreening"]["summary"]["included"]
        assert snapshot["gateScreening"]["summary"]["total"] == 20
        assert len(snapshot["gateScreening"]["presets"]) == 4
        assert sum(
            component["maximum"]
            for component in snapshot["publicRanking"]["components"]
        ) == 100
        cowl_snapshot = next(
            item for item in snapshot["cases"] if item["caseId"] == "thread-cowl-20260728"
        )
        assert cowl_snapshot["sourceAction"] == "极限试仓"
        assert cowl_snapshot["normalizedAction"] == "只观察"
        assert "门槛已降至 2 万美元" in cowl_snapshot["normalizationNote"]
        assert cowl_snapshot["detailUrl"].startswith("project-detail.html?id=")
        assert cowl_snapshot["publicSignal"]["score"] <= 100
        assert "components" in cowl_snapshot["publicSignal"]

    rulebook = json.loads(
        (PROJECT_ROOT / "storage" / "rulebook-v1.json").read_text(encoding="utf-8")
    )
    assert rulebook["priorityPolicy"]["decisionPriority"]["hardGateVeto"] is True
    assert rulebook["priorityPolicy"]["decisionPriority"]["extremePoolMayBeEmpty"] is True

    for name in (
        "candidate-pool.html",
        "data-dictionary.html",
        "rules-replay.html",
        "real-case-calibration.html",
    ):
        html = (PROJECT_ROOT / "app" / name).read_text(encoding="utf-8")
        assert 'href="candidate-pool.html"' in html
    candidate_page = (PROJECT_ROOT / "app" / "candidate-pool.html").read_text(encoding="utf-8")
    candidate_script = (PROJECT_ROOT / "app" / "candidate-pool.js").read_text(encoding="utf-8")
    screening_page = (PROJECT_ROOT / "app" / "screening-console.html").read_text(encoding="utf-8")
    screening_script = (PROJECT_ROOT / "app" / "screening-console.js").read_text(encoding="utf-8")
    assert "<h1>凸性机会中心</h1>" in candidate_page
    assert 'id="opportunityDirectoryList"' in candidate_page
    assert 'id="refreshCandidates"' not in candidate_page
    assert 'id="gateScreeningForm"' not in candidate_page
    assert ".slice(" not in candidate_script
    assert "item.detailUrl" in candidate_script
    assert "state.publicRanking" in candidate_script
    assert 'id="refreshCandidates"' in screening_page
    assert 'id="gateScreeningForm"' in screening_page
    assert 'fetch(apiUrl("refresh-candidates")' in screening_script
    assert 'fetch(apiUrl("gate-screening")' in screening_script
    assert "凸性工作台 C1.6-06" in screening_page

    print("candidate pool checks passed")


if __name__ == "__main__":
    main()
