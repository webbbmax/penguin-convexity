import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from init_db import initialize_database
from publish_machine_conclusions import (
    conclusion_record,
    merged_invalidation_conditions,
    persist_machine_conclusions,
)
from sync_thread_candidates import stable_id


class MachineConclusionPublicationTest(unittest.TestCase):
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
        self._insert_case("pending", "Pending Project", "pending", False)
        self._insert_case("market", "Market Project", "verified", True)
        self._insert_run("conclusion-run-1", self.now)
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def _insert_run(self, run_id, started_at):
        self.connection.execute(
            """
            INSERT INTO runs (
              run_id, job_name, mode, status, started_at,
              zero_result_class, zero_result_explanation,
              triggered_by, schema_version
            )
            VALUES (?, 'machine conclusion test', 'manual', 'running', ?,
                    'none', '', 'test', 1)
            """,
            (run_id, started_at),
        )

    def _insert_case(self, suffix, name, project_status, with_asset):
        project_id = f"project-{suffix}"
        case_id = f"case-{suffix}"
        asset_id = f"asset-{suffix}" if with_asset else None
        self.connection.execute(
            """
            INSERT INTO projects (
              project_id, canonical_name, website_domain, official_repo,
              team_summary, identity_status, first_seen_at, created_at,
              updated_at
            )
            VALUES (?, ?, '', '', '', ?, ?, ?, ?)
            """,
            (project_id, name, project_status, self.now, self.now, self.now),
        )
        if asset_id:
            self.connection.execute(
                """
                INSERT INTO assets (
                  asset_id, project_id, symbol, chain, contract_address,
                  asset_type, capture_grade, identity_status, created_at,
                  updated_at
                )
                VALUES (?, ?, 'TEST', 'Ethereum', '', 'token', 'unknown',
                        'verified', ?, ?)
                """,
                (asset_id, project_id, self.now, self.now),
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
            VALUES (?, ?, ?, ?, 'L1', 'shadow_signal', 'unknown', 'unknown',
                    'unknown', 'unknown', 'unknown', '', '只观察', 'unknown',
                    '', '', NULL, 'test', ?, ?)
            """,
            (case_id, project_id, asset_id, f"{name} case", self.now, self.now),
        )
        mismatch_id = f"mismatch-{suffix}"
        review_id = f"review-{suffix}"
        self.connection.execute(
            """
            INSERT INTO mismatch_scores (
              mismatch_score_id, case_id, scored_at, fact_certainty,
              economic_increment, value_capture, event_proximity,
              price_unreacted, risk_deduction, total_score,
              deduction_detail_json, rule_version
            )
            VALUES (?, ?, ?, 5, 5, 5, 5, 5, 0, 25, '[]', 'test')
            """,
            (mismatch_id, case_id, self.now),
        )
        self.connection.execute(
            """
            INSERT INTO convexity_reviews (
              review_id, case_id, reviewed_at, primary_convexity_source,
              maximum_controllable_loss, nonlinear_upside_path,
              ignition_conditions, odds_decay_conditions,
              remaining_convexity, invalidation_window,
              supporting_evidence_json, counter_evidence_json,
              open_questions_json, reviewer_type, conclusion_version
            )
            VALUES (?, ?, ?, '', '', '', '', '', 'unknown', '', '[]', '[]',
                    '[]', 'rule_engine', 'test')
            """,
            (review_id, case_id, self.now),
        )
        self.connection.execute(
            """
            INSERT INTO machine_research_scores (
              machine_score_id, case_id, run_id, mismatch_score_id,
              convexity_review_id, scored_at, lifecycle_bucket,
              lifecycle_label, evidence_quality_score, mismatch_score,
              convexity_readiness_score, confidence, dimension_scores_json,
              blockers_json, source_evidence_ids_json, source_url,
              scoring_boundary, rule_version
            )
            VALUES (?, ?, NULL, ?, ?, ?, 'early', '早期项目', 20, 25, 10,
                    'low', '{}', ?, '[]', '', 'test boundary', 'test')
            """,
            (
                f"machine-score-{suffix}",
                case_id,
                mismatch_id,
                review_id,
                self.now,
                json.dumps(["资料不足"], ensure_ascii=False),
            ),
        )

    def test_publishes_current_state_without_human_gate(self):
        first = persist_machine_conclusions(
            self.connection,
            "conclusion-run-1",
            self.now,
            stable_id,
        )
        self.connection.commit()
        self.assertEqual(first["projectsPublished"], 2)
        self.assertEqual(first["changedProjects"], 2)
        self.assertEqual(first["stateCounts"]["identity_pending"], 1)
        self.assertEqual(first["stateCounts"]["market_exit_pending"], 1)
        self.assertEqual(first["actionCounts"]["observe"], 2)
        first_reviews = {
            row["case_id"]: row["next_review_at"]
            for row in self.connection.execute(
                "SELECT case_id, next_review_at FROM candidate_cases"
            )
        }

        self._insert_run("conclusion-run-2", "2026-07-30T13:00:00Z")
        second = persist_machine_conclusions(
            self.connection,
            "conclusion-run-2",
            "2026-07-30T13:00:00Z",
            stable_id,
        )
        self.connection.commit()
        self.assertEqual(second["changedProjects"], 0)
        self.assertEqual(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM machine_conclusions
                WHERE publication_status = 'published'
                """
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM machine_conclusions"
            ).fetchone()[0],
            4,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM state_transitions"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            {
                row["case_id"]: row["next_review_at"]
                for row in self.connection.execute(
                    "SELECT case_id, next_review_at FROM candidate_cases"
                )
            },
            first_reviews,
        )

    def test_action_requires_every_hard_gate(self):
        case = {
            "project_id": "project-ready",
            "case_id": "case-ready",
            "canonical_name": "Ready",
            "symbol": "READY",
            "maturity_level": "L1",
            "project_identity_status": "verified",
            "asset_identity_status": "verified",
            "risk_level": "low",
            "remaining_convexity": "high",
            "liquidity_grade": "extreme",
            "value_capture_grade": "A",
            "convexity_source": "供应与流动性凸性",
            "invalidation": "核心事实失效",
            "next_review_at": None,
        }
        score = {
            "machine_score_id": "score-ready",
            "confidence": "high",
            "evidence_quality_score": 70,
            "mismatch_score": 65,
            "convexity_readiness_score": 80,
            "blockers": [],
            "sourceEvidenceIds": ["evidence-ready"],
            "source_url": "https://example.com/evidence",
        }
        inputs = {
            "markets": {"project-ready": {"price_usd": 1}},
            "risks": {"project-ready": {"overall_risk": "low"}},
            "tradeability": {
                "project-ready": {"overall_status": "pass"}
            },
        }
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        ready = conclusion_record(case, score, inputs, None, now)
        self.assertEqual(ready["actionCategory"], "extreme")
        self.assertEqual(ready["workflowState"], "extreme_test")

        inputs["tradeability"]["project-ready"]["overall_status"] = "unknown"
        blocked = conclusion_record(case, score, inputs, None, now)
        self.assertEqual(blocked["actionCategory"], "observe")
        self.assertEqual(blocked["state"], "market_exit_pending")

    def test_invalidation_conditions_remain_unique_after_republish(self):
        conditions = merged_invalidation_conditions(
            "项目主体或资产官方关系出现冲突；"
            "项目自己的失效条件；"
            "项目主体或资产官方关系出现冲突；"
            "项目自己的失效条件"
        )
        self.assertEqual(conditions.count("项目自己的失效条件"), 1)
        self.assertEqual(
            conditions.count("项目主体或资产官方关系出现冲突"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
