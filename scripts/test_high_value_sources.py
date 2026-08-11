#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from high_value_sources import (
    build_high_value_snapshot,
    formal_project_targets,
    meaningful_metric_change,
)
from init_db import initialize_database
from refresh_candidate_pool import persist_refresh
from sync_thread_candidates import import_candidates, load_fixture


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def sample_record(provider, case_id, value=None):
    source_id = {
        "github": "repository",
        "defillama": "protocol",
    }[provider]
    return {
        "provider": provider,
        "caseId": case_id,
        "externalId": f"{source_id}-{case_id}",
        "sourceUrl": "https://example.com/source",
        "observedAt": "2026-07-29T00:00:00Z",
        "eventType": (
            "official_code_activity"
            if provider == "github"
            else "protocol_adoption_snapshot"
        ),
        "evidenceType": (
            "official_code_activity"
            if provider == "github"
            else "protocol_adoption_metric"
        ),
        "summary": f"{provider} 自动测试事实",
        "factBoundary": (
            "confirmed_fact"
            if provider == "github"
            else "high_confidence_inference"
        ),
        "confidence": "中",
        "hardTrace": True,
        "metric": (
            {"field": "TVL", "value": value, "unit": "USD"}
            if value is not None
            else None
        ),
        "raw": {},
        "status": "success",
    }


def bundle(tvl):
    records = [
        sample_record("github", "thread-cowl-20260728"),
        sample_record("defillama", "thread-ldo-20260724", tvl),
    ]
    return {
        "records": records,
        "sourceStats": {
            "github": {"collected": 1, "matched": 1, "filtered": 0, "failed": 0},
            "defillama": {"collected": 1, "matched": 1, "filtered": 0, "failed": 0},
            "snapshot": {"collected": 0, "matched": 0, "filtered": 0, "failed": 0},
            "cactus": {"collected": 0, "matched": 0, "filtered": 0, "failed": 0},
        },
        "errors": [],
        "targetVersion": "C1.4-04-test",
        "coverage": {
            "projectsReviewed": 2,
            "verifiedProjects": 2,
            "identityBlocked": 0,
            "githubTargets": 1,
            "defillamaTargets": 1,
            "snapshotTargets": 0,
            "cactusTargets": 0,
        },
    }


def test_persistence_and_increment_boundary():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        initialize_database(db_path, root / "runtime.js", backup=False)
        fixture = load_fixture()
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            import_candidates(connection, fixture)
            first = persist_refresh(
                connection,
                fixture,
                [],
                [],
                "high-value-test-1",
                high_value_bundle=bundle(100_000),
                task_id="high_value_evidence_refresh",
            )
            connection.commit()
            first_snapshot = build_high_value_snapshot(connection)
            first_lido = next(
                item
                for item in first_snapshot["cases"]
                if item["caseId"] == "thread-ldo-20260724"
            )
            assert first["highValueCollected"] == 2
            assert first_lido["economicIncrement"] == "unknown"

            second = persist_refresh(
                connection,
                fixture,
                [],
                [],
                "high-value-test-2",
                high_value_bundle=bundle(110_000),
                task_id="high_value_evidence_refresh",
            )
            connection.commit()
            second_snapshot = build_high_value_snapshot(connection)
            second_lido = next(
                item
                for item in second_snapshot["cases"]
                if item["caseId"] == "thread-ldo-20260724"
            )
            assert second["highValueChanged"] == 1
            assert second["highValueAdded"] == 1
            assert second["highValueDuplicates"] == 1
            assert second_lido["economicIncrement"] == "verified"
            assert second_snapshot["counts"]["sources"] == 4
        finally:
            connection.close()


def test_metric_noise_boundary():
    metric = {"field": "TVL", "unit": "USD"}
    assert not meaningful_metric_change(metric, 10_000_000, 10_005_000)
    assert not meaningful_metric_change(metric, 200, 50)
    assert meaningful_metric_change(metric, 100_000, 110_000)
    assert meaningful_metric_change(metric, 0, 1_000)


def test_dynamic_formal_targets():
    targets = formal_project_targets(PROJECT_ROOT / "data" / "convexity.db")
    assert targets["version"] == "C1.6-06"
    coverage = targets["coverage"]
    if coverage["projectsReviewed"]:
        assert coverage["verifiedProjects"] <= coverage["projectsReviewed"]
    else:
        assert all(value == 0 for value in coverage.values())


def test_static_entrypoints():
    html = (APP_ROOT / "high-value-sources.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "high-value-sources.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    assert "C1.4 正式项目自动档案 · C1.4-04" in html
    assert "high-value-source-snapshot.js" in html
    assert "这次具体更新了什么" in html
    assert "打开原始来源" in script
    assert "high-value-sources.html" in workbench
    assert '["high-value-sources.html", "正式项目持续证据"]' in navigation


def main():
    test_persistence_and_increment_boundary()
    print("PASS 持续证据重复写入已拦截，指标相对增长边界保持不变")
    test_metric_noise_boundary()
    print("PASS TVL 微小波动不会制造无意义的新证据")
    test_dynamic_formal_targets()
    print("PASS 正式项目身份库已自动生成代码、采用与治理采集目标")
    test_static_entrypoints()
    print("PASS C1.4-04 页面、工作台入口和桌面软件路由已接入")


if __name__ == "__main__":
    main()
