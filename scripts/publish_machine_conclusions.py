#!/usr/bin/env python3
import hashlib
import json
from datetime import datetime, timedelta, timezone

from score_machine_research import load_inputs, parse_json


RULE_VERSION = "machine-conclusion-c1.5.0"
SOURCE_DEFINITION = {
    "source_id": "machine-conclusion-publication",
    "name": "机器状态与结论发布",
    "source_type": "internal_rule_engine",
    "url": "internal://convexity/machine-conclusions",
    "access_method": "本地规则引擎",
}

ACTION_LABELS = {
    "ordinary": "普通建仓",
    "extreme": "极限试仓",
    "observe": "只观察",
    "reflexive": "反身性管理",
    "invalidated": "已失去凸性",
}

STATE_CONFIG = {
    "identity_pending": {
        "label": "项目主体待核验",
        "workflow": "identity_pending",
        "reason": "项目主体身份尚未完成严格核验。",
        "nextStep": "自动补齐官网、官方社交、认证代码仓库和第二独立来源，确认同名记录属于同一项目主体。",
        "nextTaskId": "machine_asset_identity_refresh",
        "cadenceDays": 14,
        "upgrade": [
            "项目主体身份达到 verified",
            "官网或认证代码仓库与项目主体一致",
            "至少一条独立结构化来源完成交叉印证",
        ],
    },
    "asset_pending": {
        "label": "可购买资产待核验",
        "workflow": "tradeability_pending",
        "reason": "尚未确认项目自身可购买资产及其官方关系。",
        "nextStep": "自动核验项目代币、股票或其他可购买权益，并确认符号、网络、主合约和项目主体关系。",
        "nextTaskId": "machine_asset_identity_refresh",
        "cadenceDays": 7,
        "upgrade": [
            "项目自身可购买资产达到 verified",
            "官方关系、所在网络和主合约能够相互印证",
            "同名资产、母协议代币和包装资产冲突已经排除",
        ],
    },
    "market_exit_pending": {
        "label": "市场与退出待闭环",
        "workflow": "tradeability_pending",
        "reason": "资产身份已经建立，但市场、风险或退出路径尚未形成完整闭环。",
        "nextStep": "自动补齐价格、市值、FDV、流动性、最深交易池、合约风险、卖出路径和2万美元退出滑点。",
        "nextTaskId": "formal_market_exit_refresh",
        "cadenceDays": 3,
        "upgrade": [
            "市场快照存在且数据时间可追溯",
            "合约风险不是 blocked",
            "只读卖出路径和退出滑点达到当前门槛",
        ],
    },
    "evidence_building": {
        "label": "事实证据积累中",
        "workflow": "active_embryo",
        "reason": "基础身份与交易资料已经建立，但事实证据质量仍不足以进入凸性结构判断。",
        "nextStep": "按项目类别自动补齐基础资料、治理、代码、部署、产品、采用和监管等一手证据。",
        "nextTaskId": "high_value_evidence_refresh",
        "cadenceDays": 7,
        "upgrade": [
            "证据质量达到35分以上",
            "至少存在一条可复核硬事实",
            "项目方陈述与独立或执行层证据已经分开",
        ],
    },
    "convexity_structure_pending": {
        "label": "凸性结构待闭环",
        "workflow": "priority_watch",
        "reason": "事实值得继续跟踪，但价值捕获、最大亏损或非线性上行尚未形成闭环。",
        "nextStep": "自动核验价值捕获、最大可控亏损、非线性上行路径、点火条件、赔率衰减和失效窗口。",
        "nextTaskId": "tracking_task_refresh",
        "cadenceDays": 3,
        "upgrade": [
            "代币或权益价值捕获达到A或B",
            "最大可控亏损与非线性上行路径均可核验",
            "点火、确认、赔率衰减和失效条件完整",
            "凸性准备度达到65分以上",
        ],
    },
    "priority_watch": {
        "label": "重点机器跟踪",
        "workflow": "priority_watch",
        "reason": "研究闭环接近形成，但尚未同时通过全部行动硬门槛。",
        "nextStep": "按自动跟踪任务等待点火与确认信号，并持续复核价格反应、剩余凸性和退出能力。",
        "nextTaskId": "tracking_task_refresh",
        "cadenceDays": 2,
        "upgrade": [
            "证据、风险、交易性和凸性结构硬门槛全部通过",
            "事实成熟度和价格反应仍保留可执行赔率",
            "规则引擎重新计算后给出普通建仓或极限试仓",
        ],
    },
    "actionable": {
        "label": "行动门槛已通过",
        "workflow": "trial_ready",
        "reason": "",
        "nextStep": "持续监测确认信号、流动性、最大亏损和失效条件，不自动执行交易。",
        "nextTaskId": "tracking_task_refresh",
        "cadenceDays": 1,
        "upgrade": ["维持当前行动门槛并等待确认信号"],
    },
    "reflexive": {
        "label": "转入反身性管理",
        "workflow": "transferred_l5",
        "reason": "",
        "nextStep": "转入趋势、流动性和赔率衰减管理，不再按未兑现的早期凸性机会新增仓位。",
        "nextTaskId": "tracking_task_refresh",
        "cadenceDays": 1,
        "upgrade": ["价格与事实重新形成新的、可控亏损的非对称结构后再建新案"],
    },
    "invalidated": {
        "label": "失效或排除",
        "workflow": "invalidated",
        "reason": "",
        "nextStep": "保留失效事实和重新建案条件；没有新的一手证据时不恢复当前案例。",
        "nextTaskId": "",
        "cadenceDays": 30,
        "upgrade": ["出现能够推翻原失效事实的新一手证据"],
    },
}

UNIVERSAL_INVALIDATION = [
    "项目主体或资产官方关系出现冲突",
    "合约风险或卖出路径达到阻断级",
    "产品增长无法传导到可购买资产价值",
    "点火窗口失效、价格已经充分反应或剩余凸性消失",
]


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_time(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def meaningful(value):
    text = str(value or "").strip()
    if not text or text.lower() == "unknown":
        return False
    return not any(marker in text for marker in ("尚未", "待核验", "暂无法"))


def merged_invalidation_conditions(value):
    project_conditions = []
    for item in str(value or "").split("；"):
        condition = item.strip()
        if not condition:
            continue
        normalized = condition.rstrip("。")
        if any(
            normalized == universal.rstrip("。")
            for universal in UNIVERSAL_INVALIDATION
        ):
            continue
        if condition not in project_conditions:
            project_conditions.append(condition)
    return [
        *project_conditions,
        *UNIVERSAL_INVALIDATION,
    ]


def latest_scores(connection):
    rows = connection.execute(
        """
        SELECT score.*
        FROM machine_research_scores score
        WHERE NOT EXISTS (
          SELECT 1
          FROM machine_research_scores newer
          WHERE newer.case_id = score.case_id
            AND (
              newer.scored_at > score.scored_at
              OR (
                newer.scored_at = score.scored_at
                AND newer.machine_score_id > score.machine_score_id
              )
            )
        )
        """
    ).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        item["blockers"] = parse_json(item.pop("blockers_json"), [])
        item["sourceEvidenceIds"] = parse_json(
            item.pop("source_evidence_ids_json"), []
        )
        result[item["case_id"]] = item
    return result


def latest_conclusions(connection):
    rows = connection.execute(
        """
        SELECT conclusion.*
        FROM machine_conclusions conclusion
        WHERE conclusion.publication_status = 'published'
        ORDER BY conclusion.generated_at DESC,
                 conclusion.machine_conclusion_id DESC
        """
    ).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        result.setdefault(item["case_id"], item)
    return result


def strict_structure_ready(case):
    return (
        case.get("value_capture_grade") in {"A", "B"}
        and case.get("remaining_convexity") in {"high", "medium"}
        and meaningful(case.get("convexity_source"))
        and meaningful(case.get("invalidation"))
    )


def decide_action(case, score, market, risk, tradeability):
    risk_status = (risk or {}).get("overall_risk") or case.get("risk_level")
    tradeability_status = (tradeability or {}).get("overall_status")
    project_identity = case.get("project_identity_status")
    asset_identity = case.get("asset_identity_status")

    if (
        project_identity in {"conflict", "rejected"}
        or asset_identity in {"conflict", "rejected"}
        or risk_status == "blocked"
        or tradeability_status == "fail"
    ):
        return "invalidated"
    if case.get("maturity_level") == "L5":
        return "reflexive"

    common_ready = (
        project_identity == "verified"
        and asset_identity == "verified"
        and market is not None
        and tradeability_status == "pass"
        and risk_status in {"low", "medium"}
        and strict_structure_ready(case)
    )
    if not common_ready:
        return "observe"

    ordinary_ready = (
        case.get("maturity_level") in {"L2", "L3", "L4"}
        and score["confidence"] == "high"
        and score["evidence_quality_score"] >= 65
        and score["mismatch_score"] >= 60
        and score["convexity_readiness_score"] >= 75
        and case.get("liquidity_grade") == "standard"
    )
    if ordinary_ready:
        return "ordinary"

    extreme_ready = (
        case.get("maturity_level") in {"L0", "L1", "L2"}
        and score["confidence"] in {"high", "medium"}
        and score["evidence_quality_score"] >= 55
        and score["mismatch_score"] >= 35
        and score["convexity_readiness_score"] >= 70
        and case.get("remaining_convexity") == "high"
        and case.get("liquidity_grade") in {"standard", "extreme"}
    )
    return "extreme" if extreme_ready else "observe"


def decide_state(case, score, action, market, risk, tradeability):
    if action == "invalidated":
        return "invalidated"
    if action == "reflexive":
        return "reflexive"
    if action in {"ordinary", "extreme"}:
        return "actionable"
    if case.get("project_identity_status") != "verified":
        return "identity_pending"
    if case.get("asset_identity_status") != "verified":
        return "asset_pending"
    risk_status = (risk or {}).get("overall_risk")
    tradeability_status = (tradeability or {}).get("overall_status")
    if (
        market is None
        or tradeability_status != "pass"
        or risk_status not in {"low", "medium"}
    ):
        return "market_exit_pending"
    if (
        score["evidence_quality_score"] < 35
        or score["confidence"] in {"low", "insufficient"}
    ):
        return "evidence_building"
    if (
        not strict_structure_ready(case)
        or score["convexity_readiness_score"] < 65
    ):
        return "convexity_structure_pending"
    return "priority_watch"


def state_reason(state, score, risk, tradeability):
    if state == "invalidated":
        if (risk or {}).get("overall_risk") == "blocked":
            return "合约或安全风险已经达到阻断级。"
        if (tradeability or {}).get("overall_status") == "fail":
            return "卖出路径或交易性已经达到阻断级。"
        return "项目或资产身份出现无法继续采用的冲突。"
    if state == "reflexive":
        return "事实与价格已经进入L5反身性阶段，不再属于尚未兑现的早期凸性机会。"
    if state == "actionable":
        return "身份、证据、风险、交易性与凸性结构已经同时通过当前硬门槛。"
    reason = STATE_CONFIG[state]["reason"]
    first_blocker = next(
        (item for item in score.get("blockers", []) if item),
        "",
    )
    if first_blocker and first_blocker.rstrip("。") not in reason:
        return f"{reason.rstrip('。')}；{first_blocker}。"
    return reason


def opportunity_stage(action):
    if action in {"ordinary", "extreme"}:
        return "actionable"
    if action == "reflexive":
        return "reflexive"
    if action == "invalidated":
        return "invalidated"
    return "observe"


def conclusion_record(case, score, inputs, previous, now):
    project_id = case["project_id"]
    market = inputs["markets"].get(project_id)
    risk = inputs["risks"].get(project_id)
    tradeability = inputs["tradeability"].get(project_id)
    action = decide_action(case, score, market, risk, tradeability)
    state = decide_state(
        case,
        score,
        action,
        market,
        risk,
        tradeability,
    )
    config = STATE_CONFIG[state]
    workflow_state = config["workflow"]
    if action == "extreme":
        workflow_state = "extreme_test"
    elif action == "ordinary":
        workflow_state = "trial_ready"
    reason = state_reason(state, score, risk, tradeability)
    action_label = ACTION_LABELS[action]
    headline = (
        f"{action_label}：{reason.rstrip('。')}"
        if reason
        else action_label
    )
    existing_review_at = parse_time(case.get("next_review_at"))
    if (
        previous
        and previous["conclusion_state"] == state
        and existing_review_at
    ):
        next_review_at = existing_review_at
    else:
        next_review_at = now + timedelta(days=config["cadenceDays"])
    invalidation = merged_invalidation_conditions(case.get("invalidation"))
    return {
        "caseId": case["case_id"],
        "projectId": project_id,
        "projectName": case["canonical_name"],
        "symbol": case.get("symbol") or "",
        "machineScoreId": score["machine_score_id"],
        "state": state,
        "stateLabel": config["label"],
        "workflowState": workflow_state,
        "opportunityStage": opportunity_stage(action),
        "actionCategory": action,
        "actionLabel": action_label,
        "headline": headline,
        "whyNotActionable": reason if action == "observe" else "",
        "nextStep": config["nextStep"],
        "nextTaskId": config["nextTaskId"],
        "upgradeConditions": config["upgrade"],
        "invalidationConditions": invalidation,
        "sourceEvidenceIds": score["sourceEvidenceIds"],
        "sourceUrl": score.get("source_url") or "",
        "confidence": score["confidence"],
        "nextReviewAt": iso_time(next_review_at),
        "scoreSummary": {
            "evidenceQuality": score["evidence_quality_score"],
            "mismatch": score["mismatch_score"],
            "convexityReadiness": score["convexity_readiness_score"],
        },
    }


def semantic_changes(previous, record):
    fields = (
        ("机器状态", "conclusion_state", "state"),
        ("当前动作", "action_category", "actionCategory"),
        ("结论", "headline", "headline"),
        ("下一项自动任务", "next_task_id", "nextTaskId"),
    )
    changes = []
    for label, previous_key, current_key in fields:
        before = previous.get(previous_key) if previous else None
        after = record[current_key]
        if before != after:
            changes.append({"field": label, "before": before, "after": after})
    return changes


def register_source(connection, now):
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope,
          confidence, conflict_risk, status, schedule_text,
          last_checked_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'convexity_conclusion', '中', '低',
                'active', '结论更新后自动发布', ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          status = 'active',
          last_checked_at = excluded.last_checked_at,
          updated_at = excluded.updated_at
        """,
        (
            SOURCE_DEFINITION["source_id"],
            SOURCE_DEFINITION["name"],
            SOURCE_DEFINITION["source_type"],
            SOURCE_DEFINITION["url"],
            SOURCE_DEFINITION["access_method"],
            now,
            now,
            now,
        ),
    )


def persist_machine_conclusions(connection, run_id, now_text, stable_id):
    register_source(connection, now_text)
    inputs = load_inputs(connection)
    scores = latest_scores(connection)
    previous_by_case = latest_conclusions(connection)
    now = parse_time(now_text) or datetime.now(timezone.utc)
    case_by_id = {
        case["case_id"]: case
        for case in inputs["cases"]
    }
    records = []
    missing_scores = []
    for case in inputs["cases"]:
        score = scores.get(case["case_id"])
        if not score:
            missing_scores.append(case["case_id"])
            continue
        records.append(
            conclusion_record(
                case,
                score,
                inputs,
                previous_by_case.get(case["case_id"]),
                now,
            )
        )

    summary = {
        "projectsPublished": len(records),
        "changedProjects": 0,
        "stateCounts": {key: 0 for key in STATE_CONFIG},
        "actionCounts": {key: 0 for key in ACTION_LABELS},
        "missingScores": len(missing_scores),
        "errors": (
            [f"{len(missing_scores)}个项目缺少机器评分，未生成机器结论"]
            if missing_scores
            else []
        ),
    }
    for record in records:
        previous = previous_by_case.get(record["caseId"])
        changes = semantic_changes(previous, record)
        summary["changedProjects"] += int(bool(changes))
        summary["stateCounts"][record["state"]] += 1
        summary["actionCounts"][record["actionCategory"]] += 1
        conclusion_id = stable_id(
            "machine-conclusion",
            run_id,
            record["caseId"],
        )
        connection.execute(
            """
            UPDATE machine_conclusions
            SET publication_status = 'superseded'
            WHERE case_id = ?
              AND publication_status = 'published'
            """,
            (record["caseId"],),
        )
        connection.execute(
            """
            INSERT INTO machine_conclusions (
              machine_conclusion_id, case_id, run_id, machine_score_id,
              generated_at, conclusion_state, conclusion_state_label,
              opportunity_stage, action_category, action_label, headline,
              why_not_actionable, next_step, next_task_id,
              upgrade_conditions_json, invalidation_conditions_json,
              source_evidence_ids_json, source_url, confidence,
              publication_status, rule_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, 'published', ?)
            """,
            (
                conclusion_id,
                record["caseId"],
                run_id,
                record["machineScoreId"],
                now_text,
                record["state"],
                record["stateLabel"],
                record["opportunityStage"],
                record["actionCategory"],
                record["actionLabel"],
                record["headline"],
                record["whyNotActionable"],
                record["nextStep"],
                record["nextTaskId"],
                json.dumps(record["upgradeConditions"], ensure_ascii=False),
                json.dumps(record["invalidationConditions"], ensure_ascii=False),
                json.dumps(record["sourceEvidenceIds"], ensure_ascii=False),
                record["sourceUrl"],
                record["confidence"],
                RULE_VERSION,
            ),
        )

        current_case = case_by_id[record["caseId"]]
        if current_case["workflow_state"] != record["workflowState"]:
            connection.execute(
                """
                INSERT INTO state_transitions (
                  transition_id, case_id, from_state, to_state, reason,
                  evidence_ids_json, rule_version, actor, transitioned_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'rule_engine', ?)
                """,
                (
                    stable_id(
                        "machine-state-transition",
                        run_id,
                        record["caseId"],
                    ),
                    record["caseId"],
                    current_case["workflow_state"],
                    record["workflowState"],
                    record["headline"],
                    json.dumps(record["sourceEvidenceIds"], ensure_ascii=False),
                    RULE_VERSION,
                    now_text,
                ),
            )
        connection.execute(
            """
            UPDATE candidate_cases
            SET workflow_state = ?,
                action_stage = ?,
                current_thesis = ?,
                invalidation = ?,
                next_review_at = ?,
                updated_at = ?
            WHERE case_id = ?
            """,
            (
                record["workflowState"],
                record["actionLabel"],
                record["headline"],
                "；".join(record["invalidationConditions"]),
                record["nextReviewAt"],
                now_text,
                record["caseId"],
            ),
        )

        payload = {
            "summary": (
                f"{record['headline']}。下一步：{record['nextStep']}"
            ),
            "state": record["state"],
            "stateLabel": record["stateLabel"],
            "actionCategory": record["actionCategory"],
            "actionLabel": record["actionLabel"],
            "whyNotActionable": record["whyNotActionable"],
            "nextStep": record["nextStep"],
            "nextTaskId": record["nextTaskId"],
            "upgradeConditions": record["upgradeConditions"],
            "invalidationConditions": record["invalidationConditions"],
            "scoreSummary": record["scoreSummary"],
            "changes": changes,
        }
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url,
              excerpt, project_hint, asset_hint, chain_hint, event_type,
              raw_payload_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                    'machine_conclusion_publish', ?, 'normalized')
            """,
            (
                stable_id(
                    "machine-conclusion-event",
                    run_id,
                    record["caseId"],
                ),
                SOURCE_DEFINITION["source_id"],
                run_id,
                f"{run_id}:{record['caseId']}:machine-conclusion",
                now_text,
                now_text,
                hashlib.sha256(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
                record["sourceUrl"],
                payload["summary"],
                record["projectName"],
                record["symbol"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    return summary
