#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from enrich_project_profiles import (
    PROFILE_ENRICHMENT_VERSION,
    persist_formal_project_enrichment,
)
from init_db import initialize_database
from sync_thread_candidates import stable_id
from update_tasks import TASK_DEFINITIONS


NOW = "2026-07-30T00:00:00Z"


def insert_source(connection, source_id, name):
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope,
          confidence, conflict_risk, status, schedule_text,
          last_checked_at, created_at, updated_at
        )
        VALUES (?, ?, 'test', 'https://example.com', 'test', 'convexity',
                '中', '低', 'active', 'test', ?, ?, ?)
        """,
        (source_id, name, NOW, NOW, NOW),
    )


def insert_run(connection, run_id):
    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at,
          zero_result_class, zero_result_explanation, triggered_by,
          schema_version
        )
        VALUES (?, '身份与官方入口测试', 'manual', 'running', ?,
                'none', '', 'test', 1)
        """,
        (run_id, NOW),
    )


def insert_project_bundle(connection, project_id, with_corroborator):
    asset_id = f"{project_id}-asset"
    discovery_id = f"{project_id}-discovery"
    domain = f"{project_id}.example"
    contract_address = f"0x{('1' if with_corroborator else '2') * 40}"
    connection.execute(
        """
        INSERT INTO projects (
          project_id, canonical_name, website_domain, official_repo,
          team_summary, identity_status, first_seen_at, created_at, updated_at
        )
        VALUES (?, ?, ?, '', '', 'pending', ?, ?, ?)
        """,
        (project_id, project_id, domain, NOW, NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO assets (
          asset_id, project_id, symbol, chain, contract_address,
          asset_type, capture_grade, identity_status, created_at, updated_at
        )
        VALUES (?, ?, 'TEST', 'Base', ?, 'token', 'unknown', 'pending', ?, ?)
        """,
        (asset_id, project_id, contract_address, NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO asset_contracts (
          asset_contract_id, asset_id, network_id, contract_address,
          contract_standard, is_primary, identity_status, identity_source,
          source_id, source_url, observed_at, verified_at,
          verification_method, created_at, updated_at
        )
        VALUES (?, ?, 'base-mainnet', ?, 'ERC-20', 1, 'market_matched',
                'CoinGecko 合约精确映射', 'identity-coingecko-registry',
                'https://www.coingecko.com/', ?, NULL,
                '第三方登记精确匹配', ?, ?)
        """,
        (
            f"{project_id}-contract",
            asset_id,
            contract_address,
            NOW,
            NOW,
            NOW,
        ),
    )
    connection.execute(
        """
        INSERT INTO network_discoveries (
          discovery_id, network_id, contract_address, token_name, symbol,
          contract_standard, first_seen_at, last_seen_at, last_run_id,
          discovery_score, queue_status, created_at, updated_at
        )
        VALUES (?, 'base-mainnet', ?, ?, 'TEST', 'ERC-20', ?, ?, 'test-run-1',
                80, 'promoted', ?, ?)
        """,
        (
            discovery_id,
            contract_address,
            project_id,
            NOW,
            NOW,
            NOW,
            NOW,
        ),
    )
    connection.execute(
        """
        INSERT INTO discovery_identity_reviews (
          identity_review_id, discovery_id, run_id, reviewed_at, provider,
          resolution_status, confidence, canonical_name, coingecko_id,
          website_url, website_domain, website_status,
          official_contract_status, name_match_status, social_urls_json,
          repo_urls_json, value_capture_status, promotion_status,
          matched_project_id, promoted_project_id, promoted_asset_id,
          reason, evidence_json, rule_version
        )
        VALUES (?, ?, 'test-run-1', ?, 'coingecko_registry', 'corroborated',
                'medium', ?, ?, ?, ?, 'accessible', 'registry_matched',
                'match', ?, ?, 'unknown', 'existing_project', ?, ?, ?,
                'test', '[]', 'test-v1')
        """,
        (
            f"{project_id}-review",
            discovery_id,
            NOW,
            project_id,
            project_id,
            f"https://{domain}",
            domain,
            f'["https://x.com/{project_id}"]',
            f'["https://github.com/{project_id}/core"]',
            project_id,
            project_id,
            asset_id,
        ),
    )
    if with_corroborator:
        connection.execute(
            """
            INSERT INTO source_discoveries (
              source_discovery_id, source_id, external_id, canonical_name,
              normalized_name, website_url, website_domain, repository_url,
              social_url, source_url, cluster_key, first_seen_at, last_seen_at,
              last_run_id, matched_project_id, project_identity_status,
              attribution_confidence, attribution_reason, created_at, updated_at
            )
            VALUES (?, 'discovery-defillama-protocols', ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, 'test-run-1', ?, 'verified', 'high',
                    '官网域名完全一致', ?, ?)
            """,
            (
                f"{project_id}-source",
                project_id,
                project_id,
                project_id.replace("-", ""),
                f"https://{domain}",
                domain,
                f"https://github.com/{project_id}/core",
                f"https://x.com/{project_id}",
                f"https://defillama.com/protocol/{project_id}",
                f"project:{project_id}",
                NOW,
                NOW,
                project_id,
                NOW,
                NOW,
            ),
        )


def main():
    assert PROFILE_ENRICHMENT_VERSION == "C1.4-01"
    identity_task = TASK_DEFINITIONS["profile_enrichment_refresh"]
    assert identity_task["label"] == "正式项目身份与官方入口"
    assert identity_task["components"] == ["profile_enrichment"]

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        initialize_database(
            db_path=db_path,
            snapshot_path=root / "runtime.js",
            backup=False,
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        insert_source(
            connection,
            "identity-coingecko-registry",
            "CoinGecko 资产身份注册",
        )
        insert_source(
            connection,
            "discovery-defillama-protocols",
            "DefiLlama 协议发现",
        )
        insert_run(connection, "test-run-1")
        insert_project_bundle(connection, "verified-project", True)
        insert_project_bundle(connection, "pending-project", False)

        result = persist_formal_project_enrichment(
            connection,
            "test-run-1",
            NOW,
            stable_id,
        )
        connection.commit()
        assert result["projectsReviewed"] == 2
        assert result["identityVerified"] == 1
        assert result["remainingIdentityPending"] == 1
        assert result["anchorsAdded"] >= 5
        assert connection.execute(
            "SELECT identity_status FROM projects WHERE project_id = 'verified-project'"
        ).fetchone()[0] == "verified"
        assert connection.execute(
            "SELECT identity_status FROM projects WHERE project_id = 'pending-project'"
        ).fetchone()[0] == "pending"
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM evidence_items
            WHERE project_id = 'verified-project'
              AND evidence_type = 'official_social'
            """
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM raw_events
            WHERE event_type = 'formal_project_profile_enrichment'
            """
        ).fetchone()[0] == 2

        insert_run(connection, "test-run-2")
        repeated = persist_formal_project_enrichment(
            connection,
            "test-run-2",
            NOW,
            stable_id,
        )
        connection.commit()
        assert repeated["identityVerified"] == 0
        assert repeated["anchorsAdded"] == 0
        assert repeated["changedProjects"] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM raw_events WHERE ingestion_run_id = 'test-run-2'"
        ).fetchone()[0] == 0
        connection.close()

    print("C1.4-01 正式项目身份与官方入口自动补齐测试通过。")


if __name__ == "__main__":
    main()
