#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from automatic_profile_quality import (
    PROFILE_BOUNDARY,
    PROFILE_VERSION,
    SECTION_DEFINITIONS,
    field,
    finalize_profile,
)
from build_project_detail_snapshot import build_project_detail_snapshot
from sync_thread_candidates import sync_candidates


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def complete_fields(identity_status="verified"):
    fields = {}
    ids = {
        "identity": (
            "canonicalName",
            "projectIdentity",
            "lifecycle",
            "sourceCorroboration",
        ),
        "official": (
            "officialWebsite",
            "officialX",
            "github",
            "productDocs",
            "tokenomics",
            "team",
        ),
        "asset": (
            "tradableAsset",
            "assetIdentity",
            "network",
            "contract",
            "contractRisk",
        ),
        "market": (
            "marketSnapshot",
            "liquidity",
            "volume",
            "sellPath",
            "slippage",
        ),
        "activity": (
            "adoption",
            "governance",
            "codeActivity",
            "audit",
        ),
    }
    maximum_by_section = {
        section_id: maximum
        for section_id, _label, maximum in SECTION_DEFINITIONS
    }
    for section_id, field_ids in ids.items():
        maximum = maximum_by_section[section_id]
        base = maximum // len(field_ids)
        remainder = maximum - base * len(field_ids)
        fields[section_id] = [
            field(
                field_id,
                field_id,
                base + (1 if index < remainder else 0),
                identity_status
                if field_id == "projectIdentity"
                else "verified",
                value="自动测试资料",
                source_name="自动测试来源",
                source_url="https://example.com",
                next_task_id="identity_refresh",
            )
            for index, field_id in enumerate(field_ids)
        ]
    return fields


def main():
    ready = finalize_profile(complete_fields())
    assert ready["version"] == PROFILE_VERSION
    assert ready["score"] == 100
    assert ready["grade"] == "research_ready"
    assert ready["missingCritical"] == []
    assert ready["boundary"] == PROFILE_BOUNDARY

    blocked = finalize_profile(complete_fields("pending"))
    assert blocked["score"] < 100
    assert blocked["grade"] == "identity_blocked"
    assert any(
        item["id"] == "projectIdentity"
        for item in blocked["missingCritical"]
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        sync_candidates(
            db_path=db_path,
            pool_snapshot_path=root / "candidate-pool.js",
            runtime_snapshot_path=root / "runtime.js",
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            snapshot = build_project_detail_snapshot(connection)
        finally:
            connection.close()
        assert snapshot["version"] == PROFILE_VERSION
        assert sum(snapshot["counts"]["profileQuality"].values()) == snapshot[
            "counts"
        ]["total"]
        for record in snapshot["records"].values():
            profile = record["automaticProfile"]
            assert 0 <= profile["score"] <= 100
            assert sum(
                section["maxScore"] for section in profile["sections"]
            ) == 100
            assert profile["automatedOnly"] is True
            contract_risk = next(
                field_item
                for section in profile["sections"]
                for field_item in section["fields"]
                if field_item["id"] == "contractRisk"
            )
            if contract_risk["status"] == "verified":
                assert contract_risk["value"] in {
                    "low",
                    "medium",
                    "high",
                    "blocked",
                }

    app_root = PROJECT_ROOT / "app"
    detail_script = (app_root / "project-detail.js").read_text(
        encoding="utf-8"
    )
    master_html = (app_root / "project-master-pool.html").read_text(
        encoding="utf-8"
    )
    master_script = (app_root / "project-master-pool.js").read_text(
        encoding="utf-8"
    )
    styles = (app_root / "styles.css").read_text(encoding="utf-8")
    assert "renderAutomaticProfile" in detail_script
    assert "档案完整度只衡量" not in detail_script
    assert 'id="masterQualityFilter"' in master_html
    assert "project-detail-snapshot.js" in master_html
    assert "qualityFor" in master_script
    assert "renderProfileSummary" in master_script
    assert ".automatic-profile" in styles
    assert ".master-profile-summary" in styles
    print("C1.4-05 自动档案质量、项目队列与详情页测试通过。")


if __name__ == "__main__":
    main()
