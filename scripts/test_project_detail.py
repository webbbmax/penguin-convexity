#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from build_project_detail_snapshot import (
    build_project_detail_snapshot,
    dedupe_evidence,
    write_project_detail_snapshot,
)
from sync_thread_candidates import sync_candidates


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_exact_evidence_deduplication():
    base = {
        "evidence_type": "official_code_activity",
        "stance": "neutral",
        "fact_boundary": "confirmed_fact",
        "confidence": "高",
        "source_id": "github-official",
        "source_url": "https://github.com/example/repo",
    }
    records = [
        {**base, "summary": "same", "observed_at": "2026-07-30T02:00:00Z"},
        {**base, "summary": "same", "observed_at": "2026-07-30T01:00:00Z"},
        {**base, "summary": "new commit", "observed_at": "2026-07-30T00:00:00Z"},
    ]
    unique = dedupe_evidence(records)
    assert len(unique) == 2
    assert unique[0]["observed_at"] == "2026-07-30T02:00:00Z"
    assert unique[1]["summary"] == "new commit"


def main():
    test_exact_evidence_deduplication()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        snapshot_path = root / "project-detail-snapshot.js"
        sync_candidates(
            db_path=db_path,
            pool_snapshot_path=root / "candidate-pool.js",
            runtime_snapshot_path=root / "runtime.js",
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                INSERT INTO network_discoveries (
                  discovery_id, network_id, contract_address, token_name, symbol,
                  first_seen_at, last_seen_at, last_run_id, discovery_score,
                  queue_status, status_reason, created_at, updated_at
                )
                VALUES (
                  'detail-discovery-test', 'base-mainnet',
                  '0x1111111111111111111111111111111111111111',
                  'Detail Discovery', 'DDETAIL',
                  '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z',
                  'convexity-thread-candidates-v1', 75, 'identity_pending',
                  '等待身份核验', '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO scan_results (
                  scan_result_id, run_id, network_id, source_id, discovery_id,
                  external_key, result_status, reason, source_url,
                  raw_payload_json, observed_at
                )
                VALUES (
                  'scan-detail-test', 'convexity-thread-candidates-v1',
                  'base-mainnet', 'codex-convexity-thread',
                  'detail-discovery-test',
                  '0x1111111111111111111111111111111111111111',
                  'pending', '等待身份核验', 'codex-thread://detail',
                  '{}', '2026-07-29T00:00:00Z'
                )
                """
            )
            connection.commit()
            snapshot = build_project_detail_snapshot(connection)
            write_project_detail_snapshot(snapshot, snapshot_path)
            assert snapshot["counts"]["total"] == len(snapshot["records"])
            assert snapshot["counts"]["projects"] >= 20
            assert snapshot["counts"]["discoveries"] == 1
            discovery = snapshot["records"]["discovery:detail-discovery-test"]
            assert discovery["discovery"]["networkName"] == "Base"
            assert discovery["scanHistory"][0]["source_name"] == "Codex 凸性任务"
            project = next(
                item
                for item in snapshot["records"].values()
                if item["recordType"] == "project"
                and item["cases"]
                and item["cases"][0]["convexityReview"]
                and item["cases"][0]["mismatchScore"]
            )
            assert project["project"]["canonical_name"]
            assert project["cases"][0]["convexityReview"]
            assert project["cases"][0]["mismatchScore"]
            assert isinstance(project["assets"], list)
            assert project["automaticProfile"]["version"] == "C1.4-05"
            assert project["automaticProfile"]["automatedOnly"] is True
            assert sum(
                section["maxScore"]
                for section in project["automaticProfile"]["sections"]
            ) == 100
            assert all(
                field["autoFill"] is True
                for section in project["automaticProfile"]["sections"]
                for field in section["fields"]
            )
            assert discovery["automaticProfile"]["grade"] == "identity_blocked"
            assert any(
                item["recordType"] == "project"
                and item["cases"]
                and item["cases"][0]["mismatchScore"] is None
                for item in snapshot["records"].values()
            )
            assert all(
                "machineResearchScore" in case
                for item in snapshot["records"].values()
                if item["recordType"] == "project"
                for case in item["cases"]
            )
        finally:
            connection.close()

        text = snapshot_path.read_text(encoding="utf-8")
        assert "PENGUIN_CONVEXITY_PROJECT_DETAILS" in text
        assert "Detail Discovery" in text

    app_root = PROJECT_ROOT / "app"
    html = (app_root / "project-detail.html").read_text(encoding="utf-8")
    script = (app_root / "project-detail.js").read_text(encoding="utf-8")
    master_script = (app_root / "project-master-pool.js").read_text(encoding="utf-8")
    assert "项目主体、资产合约、交易性和凸性结论分层展示" in html
    assert "opportunity-center-snapshot.js" in html
    assert "tracking-task-snapshot.js" in html
    assert "R2.0" not in html and "C1.0" not in html
    assert "主凸性来源" in script
    assert "卖出路径与滑点" in script
    assert "尚未形成投资结论" in script
    assert "finalActionLabel" in script
    assert "旧数据库动作仅保留历史" in script
    assert "基础档案" in script
    assert "新闻前置信号" in script
    assert "查看来源" in script
    assert "layoutPriority" in script
    assert "renderRoutePrioritySections" in script
    assert "renderTrackingTask" in script
    assert "renderAutomaticProfile" in script
    assert "renderMachineResearchScore" in script
    assert "凸性准备度" in script
    assert "不是收益概率" in script
    assert "当前阻断项" in script
    assert "自动结构化档案" in script
    assert "不接收个性化手写结论" in script
    assert "运行自动补齐" in script
    assert "profile.nextAutoTask?.href" in script
    assert "下一步跟踪任务" in script
    assert "currentCase?.action_stage" not in script
    assert "publicOrder" in script
    assert "state.order.filter((masterId) => state.records[masterId])" in script
    assert "发布状态" not in script
    assert 'href="candidate-pool.html#opportunityDirectory"' in html
    assert "project-detail.html?id=" in master_script
    assert "&from=queue" in master_script
    assert 'id="detailBackLink"' in html
    assert "返回项目队列" in script
    print("project detail checks passed")


if __name__ == "__main__":
    main()
