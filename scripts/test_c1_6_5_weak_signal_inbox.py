#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from build_weak_signal_snapshot import build_weak_signal_snapshot
from init_db import initialize_database
from weak_signal_inbox import persist_weak_signals


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def insert_source(connection, source_id, name):
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope,
          confidence, conflict_risk, status, schedule_text,
          created_at, updated_at
        )
        VALUES (?, ?, 'test', 'https://example.com', 'test', 'convexity',
                '中', '低', 'active', '', '2026-07-31T00:00:00Z',
                '2026-07-31T00:00:00Z')
        """,
        (source_id, name),
    )


def main():
    with tempfile.TemporaryDirectory() as temporary:
        db_path = Path(temporary) / "weak-signals.db"
        initialize_database(db_path, backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            insert_source(
                connection,
                "discovery-github-repositories",
                "GitHub 公开仓库发现",
            )
            insert_source(
                connection,
                "discovery-defillama-protocols",
                "DefiLlama 协议目录发现",
            )
            insert_source(
                connection,
                "discovery-dexscreener-boosts",
                "DexScreener Boost",
            )
            connection.execute(
                """
                INSERT INTO projects (
                  project_id, canonical_name, identity_status,
                  first_seen_at, created_at, updated_at
                )
                VALUES ('project-a', 'Project A', 'verified',
                        '2026-07-31T00:00:00Z', '2026-07-31T00:00:00Z',
                        '2026-07-31T00:00:00Z')
                """
            )
            connection.execute(
                """
                INSERT INTO candidate_cases (
                  case_id, project_id, title, rule_version,
                  created_at, updated_at
                )
                VALUES ('case-a', 'project-a', 'Project A',
                        'test', '2026-07-31T00:00:00Z',
                        '2026-07-31T00:00:00Z')
                """
            )
            connection.execute(
                """
                INSERT INTO source_discoveries (
                  source_discovery_id, source_id, external_id,
                  canonical_name, first_seen_at, last_seen_at,
                  matched_project_id, project_identity_status,
                  attribution_confidence, attribution_reason,
                  created_at, updated_at
                )
                VALUES (
                  'github-a', 'discovery-github-repositories', 'repo-a',
                  'Project A', '2026-07-31T00:00:00Z',
                  '2026-07-31T01:00:00Z', 'project-a', 'verified',
                  'high', '已与项目主体匹配',
                  '2026-07-31T00:00:00Z', '2026-07-31T01:00:00Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO source_discoveries (
                  source_discovery_id, source_id, external_id,
                  canonical_name, first_seen_at, last_seen_at,
                  project_identity_status, attribution_confidence,
                  attribution_reason, created_at, updated_at
                )
                VALUES (
                  'llama-b', 'discovery-defillama-protocols', 'protocol-b',
                  'Protocol B', '2026-07-31T00:00:00Z',
                  '2026-07-31T01:00:00Z', 'pending', 'low',
                  '同名线索待核验',
                  '2026-07-31T00:00:00Z', '2026-07-31T01:00:00Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO network_discoveries (
                  discovery_id, network_id, contract_address, token_name,
                  symbol, first_seen_at, last_seen_at, source_ids_json,
                  source_urls_json, created_at, updated_at
                )
                VALUES (
                  'boost-c', 'ethereum-mainnet',
                  '0x1111111111111111111111111111111111111111',
                  'Boost C', 'BOOST', '2026-07-31T00:00:00Z',
                  '2026-07-31T01:00:00Z',
                  '["discovery-dexscreener-boosts"]',
                  '["https://dexscreener.com/ethereum/test"]',
                  '2026-07-31T00:00:00Z', '2026-07-31T01:00:00Z'
                )
                """
            )
            first = persist_weak_signals(
                connection,
                "2026-07-31T02:00:00Z",
            )
            connection.commit()
            second = persist_weak_signals(
                connection,
                "2026-07-31T03:00:00Z",
            )
            connection.commit()
            snapshot = build_weak_signal_snapshot(connection)
            schema_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        finally:
            connection.close()

        assert schema_version == 17
    assert first["signalsPublished"] == 3
    assert first["recordsInserted"] == 3
    assert first["triageCounts"] == {
        "ready_for_corroboration": 1,
        "identity_blocked": 1,
        "discovery_only": 1,
    }
    assert second["recordsInserted"] == 0
    assert second["changedSignals"] == 0
    assert second["unchangedSignals"] == 3
    assert snapshot["counts"]["signals"] == 3
    assert snapshot["counts"]["readyForCorroboration"] == 1
    assert snapshot["counts"]["highPromotionBias"] == 1
    assert snapshot["counts"]["unconnectedSources"] == 1
    assert any(
        item["sourceId"] == "discovery-x-social"
        and item["connectionStatus"] == "not_connected"
        for item in snapshot["sources"]
    )
    assert any(
        item["triageStatus"] == "discovery_only"
        and item["signalType"] == "paid_boost"
        for item in snapshot["records"]
    )

    html = (APP_ROOT / "weak-signal-inbox.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "weak-signal-inbox.js").read_text(encoding="utf-8")
    detail_script = (APP_ROOT / "project-detail.js").read_text(
        encoding="utf-8"
    )
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    assert "弱线索收件箱" in html
    assert "不直接提高评分或改变结论" in html
    assert "upgradeRequirement" in script
    assert "renderWeakSignals" in detail_script
    assert 'href="weak-signal-inbox.html"' in workbench
    print("C1.6-05 弱线索边界、归类、幂等、X缺口和无代码入口测试通过。")


if __name__ == "__main__":
    main()
