import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from init_db import initialize_database
from score_machine_research import (
    evidence_quality_score,
    persist_machine_research_scores,
)
from sync_thread_candidates import build_pool_snapshot, machine_fixture, stable_id


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class MachineResearchScoringTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "convexity.db"
        initialize_database(
            self.db_path,
            Path(self.temporary.name) / "runtime.js",
            backup=False,
        )
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.now = "2026-07-30T12:00:00Z"
        for source_id, name in (
            ("discovery-defillama-protocols", "DefiLlama 协议发现"),
            ("discovery-snapshot-spaces", "Snapshot 治理空间发现"),
        ):
            self.connection.execute(
                """
                INSERT INTO sources (
                  source_id, name, source_type, url, access_method, scope,
                  confidence, conflict_risk, status, schedule_text,
                  last_checked_at, created_at, updated_at
                )
                VALUES (?, ?, 'project_directory', '', 'test', 'convexity',
                        '中', '低', 'active', 'test', ?, ?, ?)
                """,
                (source_id, name, self.now, self.now, self.now),
            )
        self._insert_project("project-aave", "Aave", "verified")
        self._insert_project("project-horizon", "Aave Horizon RWA", "pending")
        self._insert_source(
            "project-aave",
            "aave-v3",
            "Aave V3",
            "discovery-defillama-protocols",
            "verified",
            13_000_000_000,
            ["Ethereum", "Base", "Arbitrum"],
        )
        self._insert_source(
            "project-aave",
            "aave-dao",
            "Aave DAO",
            "discovery-snapshot-spaces",
            "verified",
            None,
            [],
            proposal_count=12,
            proposal_time="2026-07-28T00:00:00Z",
        )
        self._insert_source(
            "project-horizon",
            "aave-horizon-rwa",
            "Aave Horizon RWA",
            "discovery-defillama-protocols",
            "corroborated",
            258_000_000,
            ["Ethereum"],
        )
        self._insert_aave_asset()
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def _insert_project(self, project_id, name, identity_status):
        self.connection.execute(
            """
            INSERT INTO projects (
              project_id, canonical_name, website_domain, official_repo,
              team_summary, identity_status, first_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, '', '', ?, ?, ?, ?)
            """,
            (
                project_id,
                name,
                "aave.com" if project_id == "project-aave" else "app.aave.com",
                identity_status,
                self.now,
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
                    '只观察', 'unknown', 'machine case', 'identity conflict',
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

    def _insert_source(
        self,
        project_id,
        slug,
        name,
        source_id,
        identity_status,
        tvl,
        chains,
        proposal_count=0,
        proposal_time=None,
    ):
        payload = {
            "tvlUsd": tvl,
            "chains": chains,
            "proposalCountInWindow": proposal_count,
            "latestProposalAt": proposal_time,
        }
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
            VALUES (?, ?, ?, ?, ?, ?, 'https://aave.com', 'aave.com',
                    '', 'https://x.com/aave', ?, 'Lending', 'protocol', ?,
                    ?, ?, NULL, ?, ?, ?, 'unknown', ?, 'test', ?,
                    'active', ?, ?)
            """,
            (
                f"source-{project_id}-{slug}",
                source_id,
                slug,
                name,
                name.casefold(),
                slug,
                (
                    f"https://snapshot.org/#/{slug}"
                    if source_id == "discovery-snapshot-spaces"
                    else f"https://defillama.com/protocol/{slug}"
                ),
                f"project:{project_id}",
                self.now,
                self.now,
                project_id,
                identity_status,
                "verified" if project_id == "project-aave" else "not_identified",
                "high" if identity_status == "verified" else "medium",
                json.dumps(payload),
                self.now,
                self.now,
            ),
        )

    def _insert_aave_asset(self):
        self.connection.execute(
            """
            INSERT INTO assets (
              asset_id, project_id, symbol, chain, contract_address,
              asset_type, capture_grade, identity_status, created_at, updated_at
            )
            VALUES ('asset-aave', 'project-aave', 'AAVE', 'Ethereum',
                    '0xaave', 'token', 'unknown', 'verified', ?, ?)
            """,
            (self.now, self.now),
        )
        self.connection.execute(
            """
            UPDATE candidate_cases
            SET asset_id = 'asset-aave'
            WHERE project_id = 'project-aave'
            """
        )
        self.connection.execute(
            """
            INSERT INTO asset_contracts (
              asset_contract_id, asset_id, network_id, contract_address,
              contract_standard, is_primary, identity_status,
              identity_source, source_id, source_url, observed_at,
              verified_at, verification_method, created_at, updated_at
            )
            VALUES ('contract-aave', 'asset-aave', 'ethereum-mainnet',
                    '0xaave', 'erc20', 1, 'verified', 'test',
                    'discovery-defillama-protocols',
                    'https://www.coingecko.com/en/coins/aave', ?, ?,
                    'test', ?, ?)
            """,
            (self.now, self.now, self.now, self.now),
        )
        self.connection.execute(
            """
            INSERT INTO project_asset_identity_reviews (
              project_asset_review_id, project_id, run_id, reviewed_at,
              provider, resolution_status, confidence, coingecko_id,
              asset_name, symbol, match_method, asset_id, platforms_json,
              official_links_json, reason, evidence_json, rule_version
            )
            VALUES ('review-aave', 'project-aave', NULL, ?,
                    'test', 'verified', 'high', 'aave', 'Aave', 'AAVE',
                    'test', 'asset-aave', '{}', '{}', 'verified', '[]',
                    'test')
            """,
            (self.now,),
        )

    def _insert_run(self, run_id, started_at):
        self.connection.execute(
            """
            INSERT INTO runs (
              run_id, job_name, mode, status, started_at,
              zero_result_class, zero_result_explanation,
              triggered_by, schema_version
            )
            VALUES (?, 'machine scoring test', 'manual', 'running', ?,
                    'none', '', 'test', 1)
            """,
            (run_id, started_at),
        )

    def test_category_weights_follow_research_route(self):
        foundation = {key: True for key in (
            "officialWebsite", "officialX", "github", "productDocs",
            "tokenomics", "asset", "contract", "market", "team", "audit",
        )}
        no_signals = {key: False for key in (
            "governance", "codeActivity", "contractDeployment",
            "productUpgrade", "onchainAdoption", "regulatory",
            "institutional", "tokenomicsAdjustment",
        )}
        source_info = {
            "sourceIds": {"one", "two"},
            "highConfidenceSources": 2,
        }
        early, _ = evidence_quality_score(
            "early", foundation, no_signals, source_info, None, None, "verified"
        )
        og, _ = evidence_quality_score(
            "og", foundation, no_signals, source_info, None, None, "verified"
        )
        self.assertGreater(early, og)

        all_signals = {key: True for key in no_signals}
        empty_foundation = {key: False for key in foundation}
        early_signal, _ = evidence_quality_score(
            "early", empty_foundation, all_signals, source_info, None, None,
            "verified",
        )
        og_signal, _ = evidence_quality_score(
            "og", empty_foundation, all_signals, source_info, None, None,
            "verified",
        )
        self.assertGreater(og_signal, early_signal)

    def test_machine_scores_preserve_observe_only_and_are_idempotent(self):
        self._insert_run("score-run-1", self.now)
        first = persist_machine_research_scores(
            self.connection,
            "score-run-1",
            self.now,
            stable_id,
        )
        self.connection.commit()
        self.assertEqual(first["projectsScored"], 2)
        self.assertEqual(first["changedProjects"], 2)

        scores = {
            row["canonical_name"]: dict(row)
            for row in self.connection.execute(
                """
                SELECT project.canonical_name, score.*
                FROM machine_research_scores score
                JOIN candidate_cases candidate ON candidate.case_id = score.case_id
                JOIN projects project ON project.project_id = candidate.project_id
                """
            )
        }
        self.assertGreater(
            scores["Aave"]["mismatch_score"],
            scores["Aave Horizon RWA"]["mismatch_score"],
        )
        self.assertGreater(
            scores["Aave"]["convexity_readiness_score"],
            scores["Aave Horizon RWA"]["convexity_readiness_score"],
        )
        self.assertEqual(
            self.connection.execute(
                """
                SELECT COUNT(*)
                FROM candidate_cases
                WHERE action_stage = '只观察'
                """
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT maturity_level FROM candidate_cases WHERE project_id = 'project-aave'"
            ).fetchone()[0],
            "L2",
        )

        self._insert_run("score-run-2", "2026-07-30T13:00:00Z")
        second = persist_machine_research_scores(
            self.connection,
            "score-run-2",
            "2026-07-30T13:00:00Z",
            stable_id,
        )
        self.connection.commit()
        self.assertEqual(second["changedProjects"], 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM machine_research_scores"
            ).fetchone()[0],
            4,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM raw_events"
            ).fetchone()[0],
            4,
        )
        pool = build_pool_snapshot(self.connection, machine_fixture())
        self.assertEqual(len(pool["cases"]), 2)
        self.assertEqual(len({item["caseId"] for item in pool["cases"]}), 2)


if __name__ == "__main__":
    unittest.main()
