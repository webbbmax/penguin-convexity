#!/usr/bin/env python3
import sqlite3
import tempfile
import unittest
from pathlib import Path

from enrich_machine_asset_identities import (
    evaluate_project,
    persist_machine_project_asset_identities,
    registry_indexes,
)
from sync_thread_candidates import stable_id


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


class MachineProjectAssetIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "convexity.db"
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.now = "2026-07-30T12:00:00Z"
        self.connection.execute(
            """
            INSERT INTO sources (
              source_id, name, source_type, url, access_method, scope,
              confidence, conflict_risk, status, schedule_text,
              last_checked_at, created_at, updated_at
            )
            VALUES ('discovery-defillama-protocols', 'DefiLlama 协议发现',
                    'project_directory', 'https://api.llama.fi/protocols',
                    'Public API', 'convexity', '中', '低', 'active',
                    'test', ?, ?, ?)
            """,
            (self.now, self.now, self.now),
        )
        self._insert_project(
            "project-aave",
            "Aave",
            "aave.com",
            "aave-v3",
            "Aave V3",
        )
        self._insert_project(
            "project-horizon",
            "Aave Horizon RWA",
            "app.aave.com",
            "aave-horizon-rwa",
            "Aave Horizon RWA",
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def _insert_project(self, project_id, name, domain, slug, source_name):
        self.connection.execute(
            """
            INSERT INTO projects (
              project_id, canonical_name, website_domain, official_repo,
              team_summary, identity_status, first_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, '', '', 'pending', ?, ?, ?)
            """,
            (project_id, name, domain, self.now, self.now, self.now),
        )
        self.connection.execute(
            """
            INSERT INTO source_discoveries (
              source_discovery_id, source_id, external_id, canonical_name,
              normalized_name, slug, website_url, website_domain,
              repository_url, social_url, source_url, category,
              raw_project_type, cluster_key, first_seen_at, last_seen_at,
              last_run_id, matched_project_id, project_identity_status,
              asset_identity_status, value_capture_status,
              attribution_confidence, attribution_reason, evidence_json,
              status, created_at, updated_at
            )
            VALUES (?, 'discovery-defillama-protocols', ?, ?, ?, ?, ?, ?,
                    '', '', ?, 'Lending', 'protocol', ?, ?, ?, NULL, ?,
                    'corroborated', 'not_identified', 'unknown', 'medium',
                    'machine project', '{}', 'active', ?, ?)
            """,
            (
                f"source-{project_id}",
                slug,
                source_name,
                source_name.casefold(),
                slug,
                f"https://{domain}",
                domain,
                f"https://defillama.com/protocol/{slug}",
                f"project:{project_id}",
                self.now,
                self.now,
                project_id,
                self.now,
                self.now,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO candidate_cases (
              case_id, project_id, asset_id, title, maturity_level,
              workflow_state, risk_level, remaining_convexity,
              ignition_proximity, tradeability_status, liquidity_grade,
              convexity_source, action_stage, value_capture_grade,
              current_thesis, invalidation, next_review_at, rule_version,
              created_at, updated_at
            )
            VALUES (?, ?, NULL, ?, 'L0', 'shadow_signal', 'unknown',
                    'unknown', 'unknown', 'unknown', 'unknown', '',
                    '只观察', 'unknown',
                    '当前只确认项目主体线索，尚未识别可投资资产、价值捕获、凸性来源和点火条件。',
                    'identity conflict',
                    NULL, 'convexity-auto-discovery-v1.0.0', ?, ?)
            """,
            (
                f"case-{project_id}",
                project_id,
                f"{name} machine case",
                self.now,
                self.now,
            ),
        )

    def _project_input(self, project_id):
        project = dict(
            self.connection.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        )
        project["sourceRecords"] = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM source_discoveries WHERE matched_project_id = ?",
                (project_id,),
            )
        ]
        return project

    def _bundle(self):
        coin = {
            "id": "aave",
            "name": "Aave",
            "symbol": "aave",
            "platforms": {
                "ethereum": "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",
                "base": "0x63706e401c06ac8513145b7687a14804d17f814b",
            },
        }
        protocol_base = {
            "address": "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",
            "symbol": "AAVE",
            "gecko_id": None,
            "url": "https://aave.com",
            "twitter": "aave",
            "github": None,
        }
        aave = evaluate_project(
            self._project_input("project-aave"),
            [{**protocol_base, "slug": "aave-v3", "name": "Aave V3"}],
            registry_indexes([coin]),
        )
        horizon = evaluate_project(
            self._project_input("project-horizon"),
            [
                {
                    **protocol_base,
                    "slug": "aave-horizon-rwa",
                    "name": "Aave Horizon RWA",
                }
            ],
            registry_indexes([coin]),
        )
        return {
            "records": [aave, horizon],
            "errors": [],
            "projectsQueued": 2,
            "registryAssets": 1,
            "protocolRecords": 2,
        }

    def _insert_run(self, run_id):
        self.connection.execute(
            """
            INSERT INTO runs (
              run_id, job_name, mode, status, started_at, finished_at,
              duration_ms, zero_result_class, zero_result_explanation,
              triggered_by, schema_version
            )
            VALUES (?, 'test', 'manual', 'running', ?, NULL, 0,
                    'task_not_run', '', 'test', 1)
            """,
            (run_id, self.now),
        )

    def test_strict_project_asset_identity_and_idempotence(self):
        self._insert_run("run-1")
        summary = persist_machine_project_asset_identities(
            self.connection,
            self._bundle(),
            "run-1",
            self.now,
            stable_id,
        )
        self.connection.commit()

        self.assertEqual(summary["projectsQueued"], 2)
        self.assertEqual(summary["verified"], 1)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["assetsCreated"], 1)
        self.assertEqual(summary["contractsUpserted"], 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM assets WHERE project_id = 'project-aave'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM assets WHERE project_id = 'project-horizon'"
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT asset_id FROM candidate_cases WHERE project_id = 'project-aave'"
            ).fetchone()["asset_id"]
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT action_stage
                FROM candidate_cases
                WHERE project_id = 'project-aave'
                """
            ).fetchone()[0],
            "只观察",
        )
        self.assertIn(
            "已识别可交易资产 AAVE",
            self.connection.execute(
                """
                SELECT current_thesis
                FROM candidate_cases
                WHERE project_id = 'project-aave'
                """
            ).fetchone()[0],
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT resolution_status
                FROM project_asset_identity_reviews
                WHERE project_id = 'project-horizon'
                """
            ).fetchone()[0],
            "pending",
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT source_url
                FROM raw_events
                WHERE project_hint = 'Aave Horizon RWA'
                """
            ).fetchone()[0],
            "https://defillama.com/protocol/aave-horizon-rwa",
        )

        self._insert_run("run-2")
        second = persist_machine_project_asset_identities(
            self.connection,
            self._bundle(),
            "run-2",
            "2026-07-30T13:00:00Z",
            stable_id,
        )
        self.connection.commit()
        self.assertEqual(second["assetsCreated"], 0)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM asset_contracts"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM raw_events"
            ).fetchone()[0],
            4,
        )


if __name__ == "__main__":
    unittest.main()
