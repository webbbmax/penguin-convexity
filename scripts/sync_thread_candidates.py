#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH, DEFAULT_SNAPSHOT_PATH, initialize_database, write_runtime_snapshot
from gate_screening import build_screening_snapshot
from update_tasks import TASK_DEFINITIONS
from catalyst_trade_paths import latest_paths


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE_PATH = PROJECT_ROOT / "fixtures" / "thread-candidate-seeds-v1.json"
DEFAULT_POOL_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "candidate-pool-snapshot.js"
SOURCE_ID = "codex-convexity-thread"
RULE_VERSION = "convexity-thread-import-v1.0.0"
MACHINE_RULE_VERSION = "convexity-auto-discovery-v1.0.0"
PRODUCTION_POOL_VERSION = "convexity-machine-candidates-c1.5.0"
RUN_ID = "convexity-thread-candidates-v1"
UPDATE_JOB_NAMES = sorted(
    {
        name
        for definition in TASK_DEFINITIONS.values()
        for name in [definition["jobName"], *definition.get("legacyJobNames", [])]
    }
)

POOL_LABELS = {
    "ordinary": "普通建仓候选",
    "extreme_review": "极限资格待核验",
    "embryo": "叙事胚胎与观察",
    "decay": "降级、失效与转出",
}

STATE_LABELS = {
    "shadow_signal": "影子信号",
    "identity_pending": "身份待核验",
    "tradeability_pending": "交易性待核验",
    "active_embryo": "正式胚胎",
    "priority_watch": "重点观察",
    "extreme_test": "极限试仓",
    "trial_ready": "可试仓",
    "igniting": "正在点火",
    "odds_decay": "赔率衰减",
    "invalidated": "逻辑失效",
    "transferred_l5": "转入 L5 管理",
    "archived": "已归档",
}

PUBLIC_EXIT_STATES = {"invalidated", "transferred_l5", "archived"}
PUBLIC_ACTION_POINTS = {
    "普通建仓": 25,
    "极限试仓": 23,
    "只观察": 10,
    "反身性管理": 4,
    "已失去凸性": 0,
}
PUBLIC_REMAINING_POINTS = {
    "high": 20,
    "medium": 13,
    "low": 5,
    "unknown": 3,
    "none": 0,
}
PUBLIC_IGNITION_POINTS = {
    "immediate": 15,
    "near": 12,
    "forming": 8,
    "distant": 4,
    "unknown": 3,
}
PUBLIC_TRADEABILITY_POINTS = {
    "standard": 15,
    "extreme": 10,
    "limited": 8,
    "unknown": 3,
    "untradeable": 0,
}
PUBLIC_RISK_POINTS = {
    "low": 10,
    "medium": 7,
    "high": 3,
    "unknown": 2,
    "blocked": 0,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_fixture(path=DEFAULT_FIXTURE_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def machine_fixture():
    return {
        "version": PRODUCTION_POOL_VERSION,
        "sourceThreadId": "",
        "sourceThreadTitle": "",
        "importBoundary": (
            "生产结果只接受自动采集、身份解析和规则计算生成的项目。"
            "旧对话答案已经隔离，不参与发现、排序或行动结论。"
        ),
        "records": [],
    }


def stable_id(prefix, *parts):
    payload = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def confidence_for_boundary(boundary):
    return {
        "confirmed_fact": "中",
        "high_confidence_inference": "中",
        "project_claim": "低",
        "unverified_signal": "待验证",
    }.get(boundary, "待验证")


def decision_action(record):
    if record["actionStage"] in ("普通建仓", "极限试仓", "反身性管理"):
        return record["actionStage"]
    if record["actionStage"] == "已失去凸性":
        return "退出"
    return "只观察"


def public_signal(case):
    mismatch_score = case.get("mismatchScore")
    evidence_points = (
        round(max(0, min(100, mismatch_score)) * 0.12)
        if mismatch_score is not None
        else 0
    )
    if case.get("hardTracePresent"):
        evidence_points = min(15, evidence_points + 3)
    components = {
        "actionReadiness": PUBLIC_ACTION_POINTS.get(
            case.get("normalizedAction"),
            0,
        ),
        "remainingConvexity": PUBLIC_REMAINING_POINTS.get(
            case.get("remainingConvexity"),
            0,
        ),
        "ignitionProximity": PUBLIC_IGNITION_POINTS.get(
            case.get("ignitionProximity"),
            0,
        ),
        "evidenceAndMismatch": evidence_points,
        "tradeability": PUBLIC_TRADEABILITY_POINTS.get(
            case.get("liquidityGrade"),
            0,
        ),
        "riskQuality": PUBLIC_RISK_POINTS.get(
            case.get("riskLevel"),
            0,
        ),
    }
    score = sum(components.values())
    screening = case.get("screening") or {}
    exit_reasons = []
    if case.get("state") in PUBLIC_EXIT_STATES:
        exit_reasons.append(case.get("stateLabel") or "已转出当前凸性阶段")
    if case.get("riskLevel") == "blocked":
        exit_reasons.append("风险已达到阻断级")
    if case.get("remainingConvexity") == "none":
        exit_reasons.append("剩余凸性为无")
    if case.get("normalizedAction") == "已失去凸性":
        exit_reasons.append("现规则判断已失去凸性")

    active = not exit_reasons
    qualified = active and bool(screening.get("included"))
    actionable = (
        qualified
        and screening.get("status") == "pass"
        and case.get("normalizedAction") in {"普通建仓", "极限试仓"}
    )
    high_tail = (
        active
        and case.get("remainingConvexity") == "high"
        and case.get("discoveryPriority") in {"极高", "高"}
    )
    if not active:
        tier = "transferred"
        tier_label = "转出或失效"
    elif actionable:
        tier = "actionable"
        tier_label = "当前可行动"
    elif qualified and screening.get("status") == "pass":
        tier = "qualified"
        tier_label = "完整通过"
    elif qualified:
        tier = "pending"
        tier_label = "入选但待核验"
    elif case.get("state") == "odds_decay":
        tier = "decay"
        tier_label = "赔率衰减"
    elif high_tail:
        tier = "high_tail"
        tier_label = "高尾部观察"
    else:
        tier = "research"
        tier_label = "研究观察"
    return {
        "score": score,
        "components": components,
        "tier": tier,
        "tierLabel": tier_label,
        "active": active,
        "qualified": qualified,
        "actionable": actionable,
        "highTail": high_tail,
        "exitReasons": exit_reasons,
        "scoreBoundary": (
            "关注顺序分只用于同组排序；当前门槛是否通过、风险阻断和"
            "L5转出拥有更高优先级。"
        ),
    }


def import_candidates(connection, fixture):
    now = utc_now()
    records = fixture["records"]
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope, confidence,
          conflict_risk, status, schedule_text, last_checked_at, created_at, updated_at
        )
        VALUES (?, ?, 'internal_research', ?, 'Codex 本地任务记录', 'convexity',
                '中', '低', 'active', '按任务结论导入', ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          name = excluded.name,
          url = excluded.url,
          access_method = excluded.access_method,
          last_checked_at = excluded.last_checked_at,
          updated_at = excluded.updated_at
        """,
        (
            SOURCE_ID,
            "Codex 凸性任务",
            f"codex-thread://{fixture['sourceThreadId']}",
            now,
            now,
            now,
        ),
    )

    imported_evidence = 0
    for record in records:
        project_status = (
            "pending"
            if record["canonicalName"] in {"SAID", "Robiance"}
            else "verified"
        )
        connection.execute(
            """
            INSERT INTO projects (
              project_id, canonical_name, website_domain, official_repo, team_summary,
              identity_status, first_seen_at, created_at, updated_at
            )
            VALUES (?, ?, '', '', '', ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
              canonical_name = excluded.canonical_name,
              identity_status = excluded.identity_status,
              updated_at = excluded.updated_at
            """,
            (
                record["projectId"],
                record["canonicalName"],
                project_status,
                record["sourceSnapshotAt"],
                now,
                now,
            ),
        )

        asset_id = record.get("assetId")
        if asset_id:
            asset_status = (
                "pending"
                if record.get("chain") in ("", "Unknown")
                else "verified"
            )
            connection.execute(
                """
                INSERT INTO assets (
                  asset_id, project_id, symbol, chain, contract_address, asset_type,
                  capture_grade, identity_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, '', 'token', ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                  project_id = excluded.project_id,
                  symbol = excluded.symbol,
                  chain = excluded.chain,
                  capture_grade = excluded.capture_grade,
                  identity_status = excluded.identity_status,
                  updated_at = excluded.updated_at
                """,
                (
                    asset_id,
                    record["projectId"],
                    record.get("symbol", ""),
                    record.get("chain", ""),
                    record["valueCaptureGrade"],
                    asset_status,
                    now,
                    now,
                ),
            )

        connection.execute(
            """
            INSERT INTO candidate_cases (
              case_id, project_id, asset_id, title, maturity_level, workflow_state,
              risk_level, remaining_convexity, ignition_proximity,
              tradeability_status, liquidity_grade, convexity_source, action_stage,
              value_capture_grade, current_thesis, invalidation, next_review_at,
              rule_version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
              project_id = excluded.project_id,
              asset_id = excluded.asset_id,
              title = excluded.title,
              maturity_level = excluded.maturity_level,
              workflow_state = excluded.workflow_state,
              risk_level = excluded.risk_level,
              remaining_convexity = excluded.remaining_convexity,
              ignition_proximity = excluded.ignition_proximity,
              tradeability_status = excluded.tradeability_status,
              liquidity_grade = excluded.liquidity_grade,
              convexity_source = excluded.convexity_source,
              action_stage = excluded.action_stage,
              value_capture_grade = excluded.value_capture_grade,
              current_thesis = excluded.current_thesis,
              invalidation = excluded.invalidation,
              next_review_at = excluded.next_review_at,
              rule_version = excluded.rule_version,
              updated_at = excluded.updated_at
            """,
            (
                record["caseId"],
                record["projectId"],
                asset_id,
                record["title"],
                record["maturity"],
                record["workflowState"],
                record["riskLevel"],
                record["remainingConvexity"],
                record["ignitionProximity"],
                record["tradeabilityStatus"],
                record["liquidityGrade"],
                record["convexitySource"],
                record["actionStage"],
                record["valueCaptureGrade"],
                record["currentThesis"],
                record["invalidation"],
                None,
                RULE_VERSION,
                now,
                now,
            ),
        )

        connection.execute("DELETE FROM mismatch_scores WHERE case_id = ?", (record["caseId"],))
        if record.get("score"):
            score = record["score"]
            connection.execute(
                """
                INSERT INTO mismatch_scores (
                  mismatch_score_id, case_id, scored_at, fact_certainty,
                  economic_increment, value_capture, event_proximity, price_unreacted,
                  risk_deduction, total_score, deduction_detail_json, rule_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)
                """,
                (
                    stable_id("score", record["caseId"], record["sourceSnapshotAt"]),
                    record["caseId"],
                    record["sourceSnapshotAt"],
                    score["factCertainty"],
                    score["economicIncrement"],
                    score["valueCapture"],
                    score["eventProximity"],
                    score["priceUnreacted"],
                    score["riskDeduction"],
                    score["total"],
                    RULE_VERSION,
                ),
            )

        review_id = stable_id("review", record["caseId"], record["sourceSnapshotAt"])
        connection.execute("DELETE FROM convexity_reviews WHERE case_id = ?", (record["caseId"],))
        connection.execute(
            """
            INSERT INTO convexity_reviews (
              review_id, case_id, reviewed_at, primary_convexity_source,
              maximum_controllable_loss, nonlinear_upside_path, ignition_conditions,
              odds_decay_conditions, remaining_convexity, invalidation_window,
              supporting_evidence_json, counter_evidence_json, open_questions_json,
              reviewer_type, conclusion_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'human', ?)
            """,
            (
                review_id,
                record["caseId"],
                record["sourceSnapshotAt"],
                record["convexitySource"],
                record["maximumControllableLoss"],
                record["nonlinearUpsidePath"],
                record["ignitionConditions"],
                record["oddsDecayConditions"],
                record["remainingConvexity"],
                record["invalidationWindow"],
                json.dumps(
                    [item for item in record["evidence"] if item["stance"] == "support"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    [item for item in record["evidence"] if item["stance"] == "counter"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    [record["normalizationNote"]]
                    if record.get("normalizationNote")
                    else [],
                    ensure_ascii=False,
                ),
                RULE_VERSION,
            ),
        )

        connection.execute("DELETE FROM evidence_items WHERE project_id = ? AND raw_event_id IS NULL AND source_id = ?", (record["projectId"], SOURCE_ID))
        for index, evidence in enumerate(record["evidence"]):
            evidence_id = stable_id(
                "evidence",
                record["caseId"],
                record["sourceSnapshotAt"],
                index,
                evidence["url"],
            )
            connection.execute(
                """
                INSERT INTO evidence_items (
                  evidence_id, project_id, asset_id, raw_event_id, evidence_type,
                  stance, fact_boundary, confidence, observed_at, expires_at,
                  source_id, source_url, summary, created_at
                )
                VALUES (?, ?, ?, NULL, 'thread_import', ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    record["projectId"],
                    asset_id,
                    evidence["stance"],
                    evidence["factBoundary"],
                    confidence_for_boundary(evidence["factBoundary"]),
                    record["sourceSnapshotAt"],
                    SOURCE_ID,
                    evidence["url"],
                    evidence["summary"],
                    now,
                ),
            )
            imported_evidence += 1

        connection.execute("DELETE FROM decision_reports WHERE case_id = ?", (record["caseId"],))
        connection.execute(
            """
            INSERT INTO decision_reports (
              report_id, case_id, generated_at, action, position_stage, conditions,
              invalidation, review_at, confidence, conclusion_version, visibility
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'internal')
            """,
            (
                stable_id("decision", record["caseId"], record["sourceSnapshotAt"]),
                record["caseId"],
                record["sourceSnapshotAt"],
                decision_action(record),
                record["actionStage"],
                record.get("normalizationNote", record["ignitionConditions"]),
                record["invalidation"],
                "中" if record["decisionPriority"] in ("极高", "高") else "待验证",
                RULE_VERSION,
            ),
        )

        transition_id = stable_id("transition", record["caseId"], record["sourceSnapshotAt"])
        connection.execute("DELETE FROM state_transitions WHERE transition_id = ?", (transition_id,))
        connection.execute(
            """
            INSERT INTO state_transitions (
              transition_id, case_id, from_state, to_state, reason,
              evidence_ids_json, rule_version, actor, transitioned_at
            )
            VALUES (?, ?, 'thread_import', ?, ?, '[]', ?, 'Codex 凸性任务导入', ?)
            """,
            (
                transition_id,
                record["caseId"],
                record["workflowState"],
                record.get("normalizationNote", record["sourceReference"]),
                RULE_VERSION,
                record["sourceSnapshotAt"],
            ),
        )

    active_count = sum(
        record["workflowState"]
        in {"active_embryo", "priority_watch", "extreme_test", "trial_ready", "igniting"}
        for record in records
    )
    shadow_count = sum(
        record["workflowState"]
        in {"shadow_signal", "identity_pending", "tradeability_pending"}
        for record in records
    )
    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at, finished_at, duration_ms,
          collected_count, normalized_count, matched_count, shadow_added_count,
          active_added_count, zero_result_class, zero_result_explanation,
          triggered_by, schema_version
        )
        VALUES (?, '凸性任务候选首批导入', 'manual', 'success', ?, ?, 0, ?, ?, ?, ?, ?,
                'none', ?, 'Codex 凸性任务', 1)
        ON CONFLICT(run_id) DO UPDATE SET
          status = excluded.status,
          finished_at = excluded.finished_at,
          collected_count = excluded.collected_count,
          normalized_count = excluded.normalized_count,
          matched_count = excluded.matched_count,
          shadow_added_count = excluded.shadow_added_count,
          active_added_count = excluded.active_added_count,
          zero_result_class = excluded.zero_result_class,
          zero_result_explanation = excluded.zero_result_explanation
        """,
        (
            RUN_ID,
            now,
            now,
            len(records),
            len(records),
            len(records),
            shadow_count,
            active_count,
            f"已从凸性任务导入 {len(records)} 个项目快照；其中行动、胚胎、待核验和转出状态分别保留。",
        ),
    )
    return {
        "records": len(records),
        "evidence": imported_evidence,
        "active": active_count,
        "shadow": shadow_count,
    }


def build_pool_snapshot(connection, fixture=None, production_only=False):
    fixture = machine_fixture() if production_only else (fixture or load_fixture())
    seed_by_case = (
        {}
        if production_only
        else {record["caseId"]: record for record in fixture["records"]}
    )
    identity_evidence = {}
    for evidence in connection.execute(
        """
        SELECT *
        FROM evidence_items
        WHERE evidence_type IN ('independent_registry', 'official_website')
        ORDER BY observed_at, evidence_id
        """
    ):
        key = (evidence["project_id"], evidence["asset_id"])
        identity_evidence.setdefault(key, []).append(
            {
                "url": evidence["source_url"],
                "summary": evidence["summary"],
                "factBoundary": evidence["fact_boundary"],
                "stance": evidence["stance"],
            }
        )
    latest_refresh_row = connection.execute(
        f"""
        SELECT *
        FROM runs
        WHERE job_name IN ({",".join("?" for _ in UPDATE_JOB_NAMES)})
        ORDER BY started_at DESC
        LIMIT 1
        """,
        UPDATE_JOB_NAMES,
    ).fetchone()
    latest_refresh = dict(latest_refresh_row) if latest_refresh_row else None
    refresh_by_case = {}
    refresh_source_stats = []
    refresh_errors = []
    if latest_refresh:
        refresh_source_stats = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM run_source_stats
                WHERE run_id = ?
                ORDER BY collector_id
                """,
                (latest_refresh["run_id"],),
            )
        ]
        refresh_errors = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM run_errors
                WHERE run_id = ?
                ORDER BY task_name
                """,
                (latest_refresh["run_id"],),
            )
        ]
        for event in connection.execute(
            """
            SELECT *
            FROM raw_events
            WHERE ingestion_run_id = ?
              AND event_type IN (
                'market_snapshot_refresh',
                'market_mapping_skip',
                'market_refresh_error',
                'contract_tradeability_check',
                'evidence_link_check'
              )
            ORDER BY project_hint, event_type, source_url
            """,
            (latest_refresh["run_id"],),
        ):
            payload = json.loads(event["raw_payload_json"])
            bucket = refresh_by_case.setdefault(
                event["project_hint"],
                {"market": None, "evidence": [], "contracts": []},
            )
            item = {
                **payload,
                "sourceUrl": event["source_url"],
                "collectedAt": event["collected_at"],
            }
            if event["event_type"] == "evidence_link_check":
                bucket["evidence"].append(item)
            elif event["event_type"] == "contract_tradeability_check":
                bucket["contracts"].append(item)
            else:
                bucket["market"] = item

    latest_market_by_asset = {}
    for market in connection.execute(
        """
        SELECT ms.*, s.name AS source_name
        FROM market_snapshots ms
        LEFT JOIN sources s ON s.source_id = ms.data_source_id
        WHERE NOT EXISTS (
          SELECT 1
          FROM market_snapshots newer
          WHERE newer.asset_id = ms.asset_id
            AND (
              newer.observed_at > ms.observed_at
              OR (
                newer.observed_at = ms.observed_at
                AND newer.snapshot_id > ms.snapshot_id
              )
            )
        )
        """
    ):
        latest_market_by_asset[market["asset_id"]] = dict(market)

    latest_venue_by_asset = {}
    for venue in connection.execute(
        """
        SELECT v.*
        FROM venues v
        WHERE NOT EXISTS (
          SELECT 1
          FROM venues newer
          WHERE newer.asset_id = v.asset_id
            AND COALESCE(newer.checked_at, newer.updated_at) >
                COALESCE(v.checked_at, v.updated_at)
        )
        """
    ):
        latest_venue_by_asset[venue["asset_id"]] = dict(venue)

    latest_contract_risk_by_asset = {}
    for contract_risk in connection.execute(
        """
        SELECT cr.*
        FROM contract_risks cr
        WHERE NOT EXISTS (
          SELECT 1
          FROM contract_risks newer
          WHERE newer.asset_id = cr.asset_id
            AND newer.assessed_at > cr.assessed_at
        )
        """
    ):
        latest_contract_risk_by_asset[contract_risk["asset_id"]] = dict(contract_risk)

    primary_contract_by_asset = {}
    for contract in connection.execute(
        """
        SELECT
          ac.*,
          n.name AS network_name,
          n.chain_type,
          n.chain_id,
          n.environment,
          n.explorer_url,
          n.discovery_priority,
          s.name AS identity_source_name
        FROM asset_contracts ac
        JOIN networks n ON n.network_id = ac.network_id
        LEFT JOIN sources s ON s.source_id = ac.source_id
        WHERE ac.is_primary = 1
          AND NOT EXISTS (
            SELECT 1
            FROM asset_contracts newer
            WHERE newer.asset_id = ac.asset_id
              AND newer.is_primary = 1
              AND newer.updated_at > ac.updated_at
          )
        """
    ):
        primary_contract_by_asset[contract["asset_id"]] = dict(contract)

    latest_tradeability_by_asset = {}
    for check in connection.execute(
        """
        SELECT tc.*, ac.asset_id, s.name AS source_name
        FROM tradeability_checks tc
        JOIN asset_contracts ac
          ON ac.asset_contract_id = tc.asset_contract_id
        LEFT JOIN sources s ON s.source_id = tc.source_id
        WHERE NOT EXISTS (
          SELECT 1
          FROM tradeability_checks newer
          JOIN asset_contracts newer_ac
            ON newer_ac.asset_contract_id = newer.asset_contract_id
          WHERE newer_ac.asset_id = ac.asset_id
            AND newer.checked_at > tc.checked_at
        )
        """
    ):
        latest_tradeability_by_asset[check["asset_id"]] = dict(check)

    latest_publication_by_case = {}
    for publication in connection.execute(
        """
        SELECT publication.*
        FROM publication_records publication
        WHERE publication.case_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM publication_records newer
            WHERE newer.case_id = publication.case_id
              AND (
                newer.updated_at > publication.updated_at
                OR (
                  newer.updated_at = publication.updated_at
                  AND newer.publication_id > publication.publication_id
                )
              )
          )
        """
    ):
        latest_publication_by_case[publication["case_id"]] = dict(publication)

    latest_machine_conclusion_by_case = {}
    for conclusion in connection.execute(
        """
        SELECT conclusion.*
        FROM machine_conclusions conclusion
        WHERE conclusion.publication_status = 'published'
          AND NOT EXISTS (
            SELECT 1
            FROM machine_conclusions newer
            WHERE newer.case_id = conclusion.case_id
              AND newer.publication_status = 'published'
              AND (
                newer.generated_at > conclusion.generated_at
                OR (
                  newer.generated_at = conclusion.generated_at
                  AND newer.machine_conclusion_id >
                      conclusion.machine_conclusion_id
                )
              )
          )
        """
    ):
        item = dict(conclusion)
        item["upgradeConditions"] = json.loads(
            item.pop("upgrade_conditions_json")
        )
        item["invalidationConditions"] = json.loads(
            item.pop("invalidation_conditions_json")
        )
        item["sourceEvidenceIds"] = json.loads(
            item.pop("source_evidence_ids_json")
        )
        latest_machine_conclusion_by_case[item["case_id"]] = item
    latest_catalyst_path_by_case = latest_paths(connection)

    case_filter = (
        "c.case_id NOT LIKE 'thread-%' AND c.rule_version <> ?"
        if production_only
        else "1 = 1"
    )
    rows = connection.execute(
        f"""
        SELECT
          c.*,
          p.canonical_name,
          p.identity_status AS project_identity_status,
          a.symbol,
          a.chain,
          a.identity_status AS asset_identity_status,
          s.total_score,
          r.maximum_controllable_loss,
          r.nonlinear_upside_path,
          r.ignition_conditions,
          r.odds_decay_conditions,
          r.invalidation_window
        FROM candidate_cases c
        JOIN projects p ON p.project_id = c.project_id
        LEFT JOIN assets a ON a.asset_id = c.asset_id
        LEFT JOIN mismatch_scores s
          ON s.mismatch_score_id = (
            SELECT latest_score.mismatch_score_id
            FROM mismatch_scores latest_score
            WHERE latest_score.case_id = c.case_id
            ORDER BY latest_score.scored_at DESC,
                     latest_score.mismatch_score_id DESC
            LIMIT 1
          )
        LEFT JOIN convexity_reviews r
          ON r.review_id = (
            SELECT latest_review.review_id
            FROM convexity_reviews latest_review
            WHERE latest_review.case_id = c.case_id
            ORDER BY latest_review.reviewed_at DESC,
                     latest_review.review_id DESC
            LIMIT 1
          )
        WHERE {case_filter}
        ORDER BY c.updated_at DESC, p.canonical_name
        """,
        (RULE_VERSION,) if production_only else (),
    ).fetchall()

    cases = []
    for row in rows:
        data = dict(row)
        seed = seed_by_case.get(data["case_id"])
        if not seed:
            seed = {
                "pool": "embryo",
                "sourceAction": "只观察",
                "discoveryPriority": "中",
                "decisionPriority": "待刷新",
                "sourceSnapshotAt": data["created_at"],
                "sourceReference": "常用链自动发现 · 身份自动归属",
                "sourceTurnId": "",
                "normalizationNote": "仅完成项目与资产身份归属，尚未形成凸性结论。",
                "evidence": identity_evidence.get(
                    (data["project_id"], data["asset_id"]),
                    [],
                ),
            }
        market = latest_market_by_asset.get(data["asset_id"])
        venue = latest_venue_by_asset.get(data["asset_id"])
        contract_risk = latest_contract_risk_by_asset.get(data["asset_id"])
        asset_contract = primary_contract_by_asset.get(data["asset_id"])
        tradeability_check = latest_tradeability_by_asset.get(data["asset_id"])
        publication = latest_publication_by_case.get(data["case_id"])
        machine_conclusion = latest_machine_conclusion_by_case.get(
            data["case_id"]
        )
        convexity_fields = (
            data["convexity_source"],
            data["maximum_controllable_loss"],
            data["nonlinear_upside_path"],
            data["ignition_conditions"],
            data["invalidation"],
        )
        cases.append(
            {
                "caseId": data["case_id"],
                "projectId": data["project_id"],
                "detailUrl": (
                    "project-detail.html?id="
                    f"project%3A{data['project_id']}"
                ),
                "publicationStatus": (
                    publication["publication_status"]
                    if publication
                    else "not_created"
                ),
                "publicationVisibility": (
                    publication["visibility"] if publication else "internal"
                ),
                "publicationUpdatedAt": (
                    publication["updated_at"] if publication else ""
                ),
                "machineConclusion": machine_conclusion,
                "catalystTradePath": latest_catalyst_path_by_case.get(
                    data["case_id"]
                ),
                "assetMapped": data["asset_id"] is not None,
                "projectIdentityStatus": data["project_identity_status"],
                "assetIdentityStatus": data["asset_identity_status"] or "unknown",
                "sellPathStatus": (
                    "verified"
                    if tradeability_check
                    and tradeability_check["sell_path_status"] == "read_only_verified"
                    else "blocked"
                    if tradeability_check
                    and tradeability_check["sell_path_status"] == "blocked"
                    else venue["sell_status"]
                    if venue
                    else "unknown"
                ),
                "contractRisk": contract_risk["overall_risk"] if contract_risk else "unknown",
                "hardTracePresent": any(
                    evidence.get("factBoundary") == "confirmed_fact"
                    for evidence in seed["evidence"]
                ),
                "convexityFieldsComplete": all(
                    isinstance(value, str) and value.strip()
                    for value in convexity_fields
                ),
                "projectName": data["canonical_name"],
                "symbol": data["symbol"] or "",
                "chain": data["chain"] or "",
                "assetContract": (
                    {
                        "networkId": asset_contract["network_id"],
                        "networkName": asset_contract["network_name"],
                        "chainType": asset_contract["chain_type"],
                        "chainId": asset_contract["chain_id"],
                        "environment": asset_contract["environment"],
                        "explorerUrl": asset_contract["explorer_url"],
                        "discoveryPriority": asset_contract["discovery_priority"],
                        "contractAddress": asset_contract["contract_address"],
                        "contractStandard": asset_contract["contract_standard"],
                        "identityStatus": asset_contract["identity_status"],
                        "identitySource": asset_contract["identity_source"],
                        "identitySourceName": asset_contract["identity_source_name"] or "",
                        "sourceUrl": asset_contract["source_url"],
                        "observedAt": asset_contract["observed_at"],
                        "verifiedAt": asset_contract["verified_at"],
                        "verificationMethod": asset_contract["verification_method"],
                    }
                    if asset_contract
                    else None
                ),
                "tradeabilityCheck": (
                    {
                        "checkedAt": tradeability_check["checked_at"],
                        "contractExistsStatus": tradeability_check[
                            "contract_exists_status"
                        ],
                        "sourceCodeStatus": tradeability_check["source_code_status"],
                        "metadataMatchStatus": tradeability_check[
                            "metadata_match_status"
                        ],
                        "pairMatchStatus": tradeability_check["pair_match_status"],
                        "recentBuys24h": tradeability_check["recent_buys_24h"],
                        "recentSells24h": tradeability_check["recent_sells_24h"],
                        "sellPathStatus": tradeability_check["sell_path_status"],
                        "exitNotionalUsd": tradeability_check["exit_notional_usd"],
                        "estimatedExitSlippagePct": tradeability_check[
                            "estimated_exit_slippage_pct"
                        ],
                        "overallStatus": tradeability_check["overall_status"],
                        "verificationScope": tradeability_check["verification_scope"],
                        "riskFlags": json.loads(
                            tradeability_check["risk_flags_json"]
                        ),
                        "evidence": json.loads(tradeability_check["evidence_json"]),
                        "sourceName": tradeability_check["source_name"] or "",
                    }
                    if tradeability_check
                    else None
                ),
                "title": data["title"],
                "pool": seed["pool"],
                "poolLabel": POOL_LABELS[seed["pool"]],
                "maturity": data["maturity_level"],
                "state": data["workflow_state"],
                "stateLabel": STATE_LABELS[data["workflow_state"]],
                "riskLevel": data["risk_level"],
                "remainingConvexity": data["remaining_convexity"],
                "ignitionProximity": data["ignition_proximity"],
                "tradeabilityStatus": data["tradeability_status"],
                "liquidityGrade": data["liquidity_grade"],
                "convexitySource": data["convexity_source"],
                "sourceAction": seed["sourceAction"],
                "normalizedAction": data["action_stage"],
                "valueCaptureGrade": data["value_capture_grade"],
                "discoveryPriority": seed["discoveryPriority"],
                "decisionPriority": seed["decisionPriority"],
                "sourceSnapshotAt": seed["sourceSnapshotAt"],
                "sourceReference": seed["sourceReference"],
                "sourceTurnId": seed.get("sourceTurnId", ""),
                "normalizationNote": seed.get("normalizationNote", ""),
                "currentThesis": data["current_thesis"],
                "nextReviewAt": data["next_review_at"] or "",
                "maximumControllableLoss": data["maximum_controllable_loss"],
                "nonlinearUpsidePath": data["nonlinear_upside_path"],
                "ignitionConditions": data["ignition_conditions"],
                "oddsDecayConditions": data["odds_decay_conditions"],
                "invalidation": data["invalidation"],
                "invalidationWindow": data["invalidation_window"],
                "mismatchScore": data["total_score"],
                "evidence": seed["evidence"],
                "latestMarket": (
                    {
                        "observedAt": market["observed_at"],
                        "priceUsd": market["price_usd"],
                        "liquidityUsd": market["liquidity_usd"],
                        "volume24hUsd": market["volume_24h_usd"],
                        "marketCapUsd": market["market_cap_usd"],
                        "fdvUsd": market["fdv_usd"],
                        "exitNotionalUsd": market["exit_notional_usd"],
                        "estimatedExitSlippagePct": market[
                            "estimated_exit_slippage_pct"
                        ],
                        "sourceName": market["source_name"] or "",
                        "definitionNote": market["definition_note"],
                    }
                    if market
                    else None
                ),
                "refresh": refresh_by_case.get(
                    data["case_id"],
                    {"market": None, "evidence": [], "contracts": []},
                ),
            }
        )

    pool_counts = {
        key: sum(case["pool"] == key for case in cases)
        for key in POOL_LABELS
    }
    gate_screening = build_screening_snapshot(cases)
    for case in cases:
        case["publicSignal"] = public_signal(case)
    public_counts = {
        "qualified": sum(
            case["publicSignal"]["qualified"] for case in cases
        ),
        "actionable": sum(
            case["publicSignal"]["actionable"] for case in cases
        ),
        "highTail": sum(
            case["publicSignal"]["highTail"]
            and case["publicSignal"]["active"]
            for case in cases
        ),
        "active": sum(case["publicSignal"]["active"] for case in cases),
        "transferred": sum(
            not case["publicSignal"]["active"] for case in cases
        ),
    }
    discovery_networks = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
              network_id, name, chain_type, chain_id, environment,
              explorer_url, discovery_priority, status, source_url
            FROM networks
            WHERE discovery_priority = 'common' AND status = 'active'
            ORDER BY
              CASE WHEN network_id = 'robinhood-mainnet' THEN 1 ELSE 0 END,
              name
            """
        )
    ]
    return {
        "version": fixture["version"],
        "generatedAt": utc_now(),
        "sourceThreadId": fixture["sourceThreadId"],
        "sourceThreadTitle": fixture["sourceThreadTitle"],
        "importBoundary": fixture["importBoundary"],
        "priorityPolicy": {
            "discovery": "搜索权重优先覆盖 L0-L2、新叙事和极限试仓可能性；它决定先研究谁，不直接决定买谁。",
            "decision": "行动权重由证据、价值捕获、可交易性、剩余凸性和点火条件决定；任一硬门槛失败即可阻断行动。",
            "noQuota": "每天不强制凑极限试仓名额；允许极限行动池为空，但高搜索权重的胚胎仍要保留。",
        },
        "publicRanking": {
            "name": "凸性关注顺序 v1",
            "boundary": (
                "先执行门槛、风险阻断和L5转出，再在同组内按关注顺序分排序；"
                "排序分不是收益预测，也不能把未入选项目变成行动结论。"
            ),
            "components": [
                {"key": "actionReadiness", "label": "行动准备度", "maximum": 25},
                {"key": "remainingConvexity", "label": "剩余凸性", "maximum": 20},
                {"key": "ignitionProximity", "label": "点火距离", "maximum": 15},
                {"key": "evidenceAndMismatch", "label": "证据与错配", "maximum": 15},
                {"key": "tradeability", "label": "可交易性", "maximum": 15},
                {"key": "riskQuality", "label": "风险质量", "maximum": 10},
            ],
        },
        "discoveryNetworks": discovery_networks,
        "poolLabels": POOL_LABELS,
        "gateScreening": gate_screening,
        "latestRefresh": (
            {
                "runId": latest_refresh["run_id"],
                "status": latest_refresh["status"],
                "startedAt": latest_refresh["started_at"],
                "finishedAt": latest_refresh["finished_at"],
                "collectedCount": latest_refresh["collected_count"],
                "normalizedCount": latest_refresh["normalized_count"],
                "matchedCount": latest_refresh["matched_count"],
                "filteredCount": latest_refresh["filtered_count"],
                "errorCount": latest_refresh["error_count"],
                "explanation": latest_refresh["zero_result_explanation"],
                "sourceStats": refresh_source_stats,
                "errors": refresh_errors,
            }
            if latest_refresh
            else None
        ),
        "counts": {
            "total": len(cases),
            "ordinary": pool_counts["ordinary"],
            "extremeReview": pool_counts["extreme_review"],
            "embryo": pool_counts["embryo"],
            "decay": pool_counts["decay"],
            "actionableExtreme": sum(
                case["state"] == "extreme_test" for case in cases
            ),
            **public_counts,
        },
        "cases": cases,
    }


def write_pool_snapshot(snapshot, path=DEFAULT_POOL_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        "window.PENGUIN_CONVEXITY_CANDIDATES = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def sync_candidates(
    db_path=DEFAULT_DB_PATH,
    fixture_path=DEFAULT_FIXTURE_PATH,
    pool_snapshot_path=DEFAULT_POOL_SNAPSHOT_PATH,
    runtime_snapshot_path=DEFAULT_SNAPSHOT_PATH,
    allow_production_legacy_import=False,
):
    if (
        Path(db_path).resolve() == Path(DEFAULT_DB_PATH).resolve()
        and not allow_production_legacy_import
    ):
        raise RuntimeError(
            "C1.5 已禁止把旧凸性任务答案导入正式数据库。"
            "该工具只保留给隔离测试使用。"
        )
    initialize_database(db_path, runtime_snapshot_path, backup=True)
    fixture = load_fixture(fixture_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        result = import_candidates(connection, fixture)
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite 完整性检查失败：{integrity}")
        pool_snapshot = build_pool_snapshot(connection, fixture)
        write_pool_snapshot(pool_snapshot, pool_snapshot_path)
        write_runtime_snapshot(connection, runtime_snapshot_path)
    finally:
        connection.close()
    return {
        **result,
        "database": str(Path(db_path).resolve()),
        "snapshot": str(Path(pool_snapshot_path).resolve()),
    }


def main():
    parser = argparse.ArgumentParser(description="导入凸性任务既有候选项目")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_POOL_SNAPSHOT_PATH)
    args = parser.parse_args()
    print(
        json.dumps(
            sync_candidates(args.db, args.fixture, args.snapshot),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
