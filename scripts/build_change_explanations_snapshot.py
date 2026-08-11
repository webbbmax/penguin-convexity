#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "convexity.db"
DEFAULT_OPPORTUNITY_PATH = PROJECT_ROOT / "app" / "opportunity-center-snapshot.js"
DEFAULT_UPDATE_PATH = PROJECT_ROOT / "app" / "update-center-snapshot.js"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "change-explanations-snapshot.js"
OPPORTUNITY_PREFIX = "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = "
UPDATE_PREFIX = "window.PENGUIN_CONVEXITY_UPDATE_CENTER = "
OUTPUT_PREFIX = "window.PENGUIN_CONVEXITY_CHANGE_EXPLANATIONS = "
RULE_VERSION = "C1.3-08"

TRACKING_DIRECTION_LABELS = {
    "upgrade": "上调",
    "downgrade": "下调",
    "changed": "关键项变化",
}
REVIEW_STATUS_LABELS = {
    "pending": "待结论复核",
    "confirmed": "已确认采用",
    "rejected": "未采用，重新复查",
}

FIELD_RULES = {
    "stage": ("行动阶段", "规则与动作"),
    "modelActionCategory": ("模型动作", "规则与动作"),
    "riskLevel": ("风险", "风险"),
    "remainingConvexity": ("剩余凸性", "研究判断"),
    "ignitionProximity": ("点火距离", "研究判断"),
    "liquidityGrade": ("交易性等级", "交易性"),
    "tradeabilityStatus": ("交易状态", "交易性"),
    "maturity": ("事实成熟度", "研究判断"),
    "gateStatus": ("硬门槛状态", "规则与动作"),
    "gateIncluded": ("是否进入筛选", "规则与动作"),
}

VALUE_LABELS = {
    "actionable": "当前可行动",
    "action_pending": "动作待门槛",
    "qualified_pending": "入选待补证",
    "observe": "研究观察",
    "decay": "赔率衰减",
    "model_pending": "模型待运行",
    "reflexive": "反身性管理",
    "invalidated": "失效与排除",
    "low": "低",
    "medium": "中",
    "high": "高",
    "blocked": "阻断",
    "unknown": "待核验",
    "none": "无",
    "immediate": "临近",
    "near": "较近",
    "forming": "形成中",
    "distant": "较远",
    "standard": "标准",
    "limited": "受限",
    "untradeable": "不可交易",
    "verified": "已核验",
    "pass": "通过",
    "excluded": "未入选",
    "failed": "未通过",
    True: "是",
    False: "否",
}

FIELD_VALUE_LABELS = {
    "modelActionCategory": {
        "ordinary": "普通建仓",
        "extreme": "极限试仓",
        "observe": "只观察",
        "reflexive": "反身性管理",
        "reject": "排除",
        "pending": "模型待运行",
    },
    "liquidityGrade": {
        "standard": "标准",
        "extreme": "极限小额",
        "untradeable": "不可交易",
        "unknown": "待核验",
    },
    "gateStatus": {
        "pass": "通过",
        "pending": "待核验",
        "failed": "未通过",
        "excluded": "未入选",
    },
}

DIRECTION_ORDERS = {
    "riskLevel": {"low": 0, "medium": 1, "high": 2, "blocked": 3},
    "remainingConvexity": {"high": 0, "medium": 1, "low": 2, "none": 3},
    "ignitionProximity": {"immediate": 0, "near": 1, "forming": 2, "distant": 3},
    "liquidityGrade": {"standard": 0, "extreme": 1, "untradeable": 2},
    "tradeabilityStatus": {"verified": 0, "limited": 1, "blocked": 2},
    "gateStatus": {"pass": 0, "pending": 1, "failed": 2, "excluded": 2},
}

NUMERIC_RULES = {
    "score": {"label": "关注顺序分", "category": "研究判断", "absolute": 5},
    "mismatchScore": {"label": "错配分", "category": "研究判断", "absolute": 5},
    "priceUsd": {"label": "价格", "category": "行情", "relative": 0.10},
    "volume24hUsd": {"label": "24小时成交额", "category": "行情", "relative": 0.25},
    "liquidityUsd": {"label": "流动性", "category": "交易性", "relative": 0.25},
    "fdvUsd": {"label": "FDV", "category": "行情", "relative": 0.25},
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_js_payload(path, prefix):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"无法识别快照格式：{path}")
    return json.loads(text[len(prefix):-1])


def numeric(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def display_value(value):
    if value in VALUE_LABELS:
        return VALUE_LABELS[value]
    if value is None or value == "":
        return "暂无"
    if isinstance(value, float):
        return f"{value:,.6g}"
    return str(value)


def display_field_value(field, value):
    return FIELD_VALUE_LABELS.get(field, {}).get(value, display_value(value))


def build_state(case):
    stage = case.get("opportunityStage") or {}
    signal = case.get("publicSignal") or {}
    screening = case.get("screening") or {}
    market = case.get("latestMarket") or {}
    return {
        "stage": stage.get("stage") or "model_pending",
        "stageLabel": stage.get("stageLabel") or "模型待运行",
        "stageOrder": int(stage.get("stageOrder", 99)),
        "modelActionCategory": stage.get("modelActionCategory") or "pending",
        "modelActionLabel": stage.get("modelActionLabel") or "模型待运行",
        "riskLevel": case.get("riskLevel") or "unknown",
        "remainingConvexity": case.get("remainingConvexity") or "unknown",
        "ignitionProximity": case.get("ignitionProximity") or "unknown",
        "liquidityGrade": case.get("liquidityGrade") or "unknown",
        "tradeabilityStatus": case.get("tradeabilityStatus") or "unknown",
        "maturity": case.get("maturity") or "L0",
        "gateStatus": screening.get("status") or "pending",
        "gateIncluded": bool(screening.get("included")),
        "score": numeric(signal.get("score")),
        "mismatchScore": numeric(case.get("mismatchScore")),
        "priceUsd": numeric(market.get("priceUsd")),
        "volume24hUsd": numeric(market.get("volume24hUsd")),
        "liquidityUsd": numeric(market.get("liquidityUsd")),
        "fdvUsd": numeric(market.get("fdvUsd")),
    }


def categorical_direction(field, old, new):
    if old in (None, "", "unknown") or new in (None, "", "unknown"):
        return "neutral"
    order = DIRECTION_ORDERS.get(field)
    if not order or old not in order or new not in order:
        return "neutral"
    if order[new] < order[old]:
        return "upgrade"
    if order[new] > order[old]:
        return "downgrade"
    return "neutral"


def compare_states(previous, current):
    changes = []
    for field, (label, category) in FIELD_RULES.items():
        old = previous.get(field)
        new = current.get(field)
        if old == new:
            continue
        changes.append(
            {
                "field": field,
                "label": label,
                "from": old,
                "to": new,
                "fromLabel": display_field_value(field, old),
                "toLabel": display_field_value(field, new),
                "category": category,
                "direction": categorical_direction(field, old, new),
            }
        )

    for field, rule in NUMERIC_RULES.items():
        old = numeric(previous.get(field))
        new = numeric(current.get(field))
        if old is None or new is None:
            continue
        delta = new - old
        if "absolute" in rule:
            if abs(delta) < rule["absolute"]:
                continue
            relative = None
        else:
            if old == 0:
                continue
            relative = delta / abs(old)
            if abs(relative) < rule["relative"]:
                continue
        changes.append(
            {
                "field": field,
                "label": rule["label"],
                "from": old,
                "to": new,
                "fromLabel": display_value(old),
                "toLabel": display_value(new),
                "deltaPct": round(relative * 100, 2) if relative is not None else None,
                "category": rule["category"],
                "direction": "neutral",
            }
        )
    return changes


def change_direction(previous, current, changes):
    old_stage = previous.get("stageOrder")
    new_stage = current.get("stageOrder")
    if old_stage is not None and new_stage is not None and old_stage != new_stage:
        return "upgrade" if new_stage < old_stage else "downgrade"
    directional = {item["direction"] for item in changes if item["direction"] != "neutral"}
    if directional == {"upgrade"}:
        return "upgrade"
    if directional == {"downgrade"}:
        return "downgrade"
    return "changed"


def build_explanation(previous, current, changes, direction):
    old_stage = previous.get("stageLabel") or display_value(previous.get("stage"))
    new_stage = current.get("stageLabel") or display_value(current.get("stage"))
    if previous.get("stage") != current.get("stage"):
        opening = f"行动阶段从“{old_stage}”调整为“{new_stage}”。"
    else:
        opening = f"行动阶段保持“{new_stage}”，但关键监测项发生变化。"
    details = []
    for item in changes[:6]:
        if item.get("deltaPct") is not None:
            details.append(
                f"{item['label']}从 {item['fromLabel']} 变为 {item['toLabel']}（{item['deltaPct']:+.2f}%）"
            )
        else:
            details.append(f"{item['label']}从“{item['fromLabel']}”变为“{item['toLabel']}”")
    direction_note = {
        "upgrade": "综合方向为上调",
        "downgrade": "综合方向为下调",
        "changed": "本次为横向变化，不自动等同于更值得买入",
    }[direction]
    return f"{opening}主要触发：{'；'.join(details)}。{direction_note}。"


def evidence_category(change):
    event_type = str(change.get("eventType") or "")
    task_id = str(change.get("taskId") or "")
    if "market" in event_type or "market" in task_id:
        return "行情"
    if "risk" in event_type or "contract" in event_type:
        return "风险"
    if "trade" in event_type or "liquidity" in event_type:
        return "交易性"
    return "事实"


def compact_evidence(change):
    return {
        "changeId": change.get("changeId") or "",
        "sourceName": change.get("sourceName") or "未标明来源",
        "eventLabel": change.get("eventLabel") or change.get("eventType") or "来源更新",
        "category": evidence_category(change),
        "summary": change.get("summary") or "该来源产生了新的项目记录。",
        "sourceUrl": change.get("sourceUrl") or "",
        "collectedAt": change.get("collectedAt") or "",
        "changes": change.get("changes") or [],
    }


def select_evidence(update_changes, case_id, since=None, limit=5):
    matching = [
        item
        for item in update_changes
        if item.get("projectKey") == case_id
        and (not since or str(item.get("collectedAt") or "") > since)
    ]
    matching.sort(key=lambda item: item.get("collectedAt") or "", reverse=True)
    return [compact_evidence(item) for item in matching[:limit]]


def tracking_direction(result):
    decision = result.get("decision")
    if decision == "upgrade":
        return "upgrade"
    if decision == "stop":
        return "downgrade"
    if (
        decision in {"continue", "monitor"}
        and int(result.get("new_findings_count") or 0) > 0
    ):
        return "changed"
    return None


def latest_decision_reviews(connection):
    try:
        rows = connection.execute(
            """
            SELECT review.*
            FROM tracking_decision_reviews review
            ORDER BY review.reviewed_at DESC, review.tracking_review_id DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    reviews = {}
    for row in rows:
        if row["tracking_result_id"] in reviews:
            continue
        reviews[row["tracking_result_id"]] = dict(row)
    return reviews


def attach_decision_review(result, review_by_result):
    payload = dict(result)
    required = (
        result.get("decision") in {"upgrade", "stop"}
        and result.get("execution_status") != "failed"
    )
    review = review_by_result.get(result.get("tracking_result_id")) if required else None
    status = review.get("review_action") if review else "pending" if required else "not_required"
    payload["decisionReview"] = {
        "required": required,
        "status": status,
        "statusLabel": REVIEW_STATUS_LABELS.get(status, "无需人工复核"),
        "reviewId": review.get("tracking_review_id") if review else "",
        "note": review.get("review_note") if review else "",
        "actor": review.get("actor") if review else "",
        "reviewedAt": review.get("reviewed_at") if review else "",
    }
    return payload


def compact_tracking_evidence(finding):
    return {
        "changeId": finding.get("evidenceId") or "",
        "sourceName": finding.get("sourceName") or "自动跟踪信源",
        "eventLabel": finding.get("eventType") or "跟踪发现",
        "category": evidence_category(finding),
        "summary": finding.get("summary") or "自动跟踪发现新的项目事实。",
        "sourceUrl": finding.get("sourceUrl") or "",
        "collectedAt": finding.get("collectedAt") or finding.get("observedAt") or "",
        "changes": finding.get("changes") or [],
    }


def tracking_history_payload(result, project):
    direction = tracking_direction(result)
    observed_at = result.get("finished_at") or result.get("started_at") or utc_now()
    evidence = [
        compact_tracking_evidence(item)
        for item in result.get("findings") or []
        if item.get("isNew")
    ]
    decision_label = result.get("decisionLabel") or TRACKING_DIRECTION_LABELS.get(
        direction,
        result.get("decision") or "自动跟踪",
    )
    explanation = result.get("reason") or "自动跟踪发现关键项目事实发生变化。"
    return {
        "history_id": f"tracking-change-{result.get('tracking_result_id') or stable_history_id(result.get('case_id') or '', observed_at, direction, {})}",
        "case_id": result.get("case_id") or "",
        "run_id": result.get("run_id") or "",
        "observed_at": observed_at,
        "change_direction": direction,
        "from_stage": result.get("conclusion_before") or "",
        "to_stage": result.get("conclusion_after") or "",
        "from_stage_order": None,
        "to_stage_order": None,
        "explanation": explanation,
        "changedFields": [],
        "triggerCategories": ["自动跟踪"],
        "evidence": evidence,
        "state": {},
        "rule_version": RULE_VERSION,
        "created_at": observed_at,
        "projectName": project.get("projectName") or result.get("projectName") or result.get("case_id") or "",
        "symbol": project.get("symbol") or "",
        "detailUrl": project.get("detailUrl") or "",
        "changeSource": "tracking",
        "changeSourceLabel": "自动跟踪",
        "trackingResult": result,
    }


def merge_tracking_history(history, tracking_results, result_by_case):
    for result in tracking_results:
        direction = tracking_direction(result)
        case_id = result.get("case_id")
        project = result_by_case.get(case_id)
        if not direction or not project:
            continue
        existing = next(
            (
                item
                for item in history
                if item.get("case_id") == case_id
                and item.get("run_id")
                and item.get("run_id") == result.get("run_id")
                and item.get("change_direction") != "baseline"
            ),
            None,
        )
        tracking_payload = tracking_history_payload(result, project)
        if existing:
            existing_evidence = {
                (
                    item.get("changeId"),
                    item.get("sourceUrl"),
                    item.get("summary"),
                )
                for item in existing.get("evidence") or []
            }
            existing["evidence"] = (existing.get("evidence") or []) + [
                item
                for item in tracking_payload["evidence"]
                if (
                    item.get("changeId"),
                    item.get("sourceUrl"),
                    item.get("summary"),
                )
                not in existing_evidence
            ]
            existing["triggerCategories"] = list(
                dict.fromkeys(
                    (existing.get("triggerCategories") or []) + ["自动跟踪"]
                )
            )
            tracking_reason = result.get("reason") or ""
            if tracking_reason and tracking_reason not in existing["explanation"]:
                existing["explanation"] = (
                    f"{existing['explanation']} 自动跟踪结果：{tracking_reason}"
                )
            existing["changeSource"] = "stage_and_tracking"
            existing["changeSourceLabel"] = "规则重算 + 自动跟踪"
            existing["trackingResult"] = result
            continue
        history.append(tracking_payload)
    history.sort(
        key=lambda item: (
            item.get("observed_at") or "",
            item.get("created_at") or "",
        ),
        reverse=True,
    )
    return history


def history_payload(row):
    payload = dict(row)
    payload["changedFields"] = json.loads(payload.pop("changed_fields_json") or "[]")
    payload["triggerCategories"] = json.loads(payload.pop("trigger_categories_json") or "[]")
    payload["evidence"] = json.loads(payload.pop("evidence_json") or "[]")
    payload["state"] = json.loads(payload.pop("state_json") or "{}")
    return payload


def latest_run_id(connection, opportunity):
    run_id = (opportunity.get("latestRefresh") or {}).get("runId")
    if not run_id:
        return None
    row = connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return run_id if row else None


def stable_history_id(case_id, observed_at, direction, state):
    digest = hashlib.sha256(
        json.dumps(
            [case_id, observed_at, direction, state],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"opportunity-history-{digest}"


def build_snapshot(connection, opportunity, update_center):
    now = utc_now()
    update_changes = update_center.get("changes") or []
    review_by_result = latest_decision_reviews(connection)
    tracking_results = [
        attach_decision_review(item, review_by_result)
        for item in update_center.get("trackingResults") or []
    ]
    run_id = latest_run_id(connection, opportunity)
    results = []
    inserted_ids = set()

    case_ids = {
        row[0]
        for row in connection.execute("SELECT case_id FROM candidate_cases")
    }
    missing = [
        item.get("caseId")
        for item in opportunity.get("cases", [])
        if item.get("caseId") not in case_ids
    ]
    if missing:
        raise ValueError(f"机会中心存在未写入候选主表的项目：{', '.join(missing[:5])}")

    for case in opportunity.get("cases", []):
        case_id = case["caseId"]
        current = build_state(case)
        previous_row = connection.execute(
            """
            SELECT *
            FROM opportunity_stage_history
            WHERE case_id = ?
            ORDER BY observed_at DESC, created_at DESC, rowid DESC
            LIMIT 1
            """,
            (case_id,),
        ).fetchone()

        if previous_row is None:
            direction = "baseline"
            explanation = "首次建立变化比较基线；以后只有达到明确阈值的变化才记录。"
            changed_fields = []
            categories = ["比较基线"]
            evidence = select_evidence(update_changes, case_id, limit=3)
            history_id = stable_history_id(case_id, now, direction, current)
            connection.execute(
                """
                INSERT INTO opportunity_stage_history (
                  history_id, case_id, run_id, observed_at, change_direction,
                  from_stage, to_stage, from_stage_order, to_stage_order,
                  explanation, changed_fields_json, trigger_categories_json,
                  evidence_json, state_json, rule_version, created_at
                )
                VALUES (?, ?, ?, ?, ?, '', ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    case_id,
                    run_id,
                    now,
                    direction,
                    current["stage"],
                    current["stageOrder"],
                    explanation,
                    json.dumps(changed_fields, ensure_ascii=False),
                    json.dumps(categories, ensure_ascii=False),
                    json.dumps(evidence, ensure_ascii=False),
                    json.dumps(current, ensure_ascii=False, sort_keys=True),
                    RULE_VERSION,
                    now,
                ),
            )
            inserted_ids.add(history_id)
        else:
            previous = json.loads(previous_row["state_json"] or "{}")
            changed_fields = compare_states(previous, current)
            if changed_fields:
                direction = change_direction(previous, current, changed_fields)
                categories = list(dict.fromkeys(item["category"] for item in changed_fields))
                explanation = build_explanation(previous, current, changed_fields, direction)
                evidence = select_evidence(
                    update_changes,
                    case_id,
                    since=previous_row["observed_at"],
                    limit=5,
                )
                history_id = stable_history_id(case_id, now, direction, current)
                connection.execute(
                    """
                    INSERT INTO opportunity_stage_history (
                      history_id, case_id, run_id, observed_at, change_direction,
                      from_stage, to_stage, from_stage_order, to_stage_order,
                      explanation, changed_fields_json, trigger_categories_json,
                      evidence_json, state_json, rule_version, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        history_id,
                        case_id,
                        run_id,
                        now,
                        direction,
                        previous.get("stage") or "",
                        current["stage"],
                        previous.get("stageOrder"),
                        current["stageOrder"],
                        explanation,
                        json.dumps(changed_fields, ensure_ascii=False),
                        json.dumps(categories, ensure_ascii=False),
                        json.dumps(evidence, ensure_ascii=False),
                        json.dumps(current, ensure_ascii=False, sort_keys=True),
                        RULE_VERSION,
                        now,
                    ),
                )
                inserted_ids.add(history_id)

        rows = connection.execute(
            """
            SELECT *
            FROM opportunity_stage_history
            WHERE case_id = ?
            ORDER BY observed_at DESC, created_at DESC, rowid DESC
            """,
            (case_id,),
        ).fetchall()
        history = [history_payload(row) for row in rows]
        latest = history[0]
        latest_change = next(
            (item for item in history if item["change_direction"] != "baseline"),
            None,
        )
        if latest["history_id"] in inserted_ids:
            status = latest["change_direction"]
        elif len(history) == 1 and latest["change_direction"] == "baseline":
            status = "baseline"
        else:
            status = "stable"
        results.append(
            {
                "caseId": case_id,
                "projectName": case.get("projectName") or case_id,
                "symbol": case.get("symbol") or "",
                "detailUrl": case.get("detailUrl") or "",
                "maturity": case.get("maturity") or "L0",
                "currentStage": current["stage"],
                "currentStageLabel": current["stageLabel"],
                "currentStatus": status,
                "currentStatusLabel": {
                    "baseline": "已建立比较基线",
                    "stable": "本轮分层未变",
                    "upgrade": "本轮上调",
                    "downgrade": "本轮下调",
                    "changed": "本轮关键项变化",
                }[status],
                "currentExplanation": (
                    latest["explanation"]
                    if status != "stable"
                    else "本轮没有达到记录阈值的阶段、风险、交易性或行情变化。"
                ),
                "triggerCategories": latest["triggerCategories"] if status != "stable" else [],
                "evidence": latest["evidence"] if status != "stable" else [],
                "latestHistory": latest,
                "latestChange": latest_change,
                "historyCount": len(history),
            }
        )

    connection.commit()
    result_by_case = {item["caseId"]: item for item in results}
    results.sort(
        key=lambda item: (
            {"upgrade": 0, "downgrade": 1, "changed": 2, "baseline": 3, "stable": 4}[item["currentStatus"]],
            item["projectName"].lower(),
        )
    )
    history_rows = connection.execute(
        """
        SELECT h.*, c.title
        FROM opportunity_stage_history h
        JOIN candidate_cases c ON c.case_id = h.case_id
        ORDER BY h.observed_at DESC, h.created_at DESC, h.rowid DESC
        """
    ).fetchall()
    history = []
    for row in history_rows:
        payload = history_payload(row)
        item = result_by_case.get(payload["case_id"])
        payload["projectName"] = item["projectName"] if item else row["title"]
        payload["symbol"] = item["symbol"] if item else ""
        payload["detailUrl"] = item["detailUrl"] if item else ""
        payload["changeSource"] = "stage"
        payload["changeSourceLabel"] = "规则重算"
        history.append(payload)
    history = merge_tracking_history(history, tracking_results, result_by_case)

    current_run_ids = {
        item
        for item in (
            run_id,
            (update_center.get("latestRun") or {}).get("run_id"),
        )
        if item
    }
    latest_tracking_by_case = {}
    for tracking_result in sorted(
        tracking_results,
        key=lambda item: item.get("finished_at") or item.get("started_at") or "",
        reverse=True,
    ):
        case_id = tracking_result.get("case_id")
        if case_id and case_id not in latest_tracking_by_case:
            latest_tracking_by_case[case_id] = tracking_result
    history_by_case = defaultdict(list)
    for item in history:
        history_by_case[item["case_id"]].append(item)
    for result in results:
        case_history = history_by_case[result["caseId"]]
        latest = case_history[0]
        latest_change = next(
            (item for item in case_history if item["change_direction"] != "baseline"),
            None,
        )
        latest_tracking = latest_tracking_by_case.get(result["caseId"])
        result["latestHistory"] = latest
        result["latestChange"] = latest_change
        result["latestTracking"] = latest_tracking
        result["decisionReview"] = (
            latest_tracking.get("decisionReview")
            if latest_tracking
            else {
                "required": False,
                "status": "not_required",
                "statusLabel": "无需人工复核",
                "reviewId": "",
                "note": "",
                "actor": "",
                "reviewedAt": "",
            }
        )
        result["historyCount"] = len(case_history)
        if (
            latest.get("run_id") in current_run_ids
            and latest.get("changeSource") == "stage_and_tracking"
            and result["currentStatus"] not in {"stable", "baseline"}
        ):
            result["currentExplanation"] = latest["explanation"]
            result["triggerCategories"] = latest["triggerCategories"]
            result["evidence"] = latest["evidence"]
        current_tracking_direction = tracking_direction(latest_tracking or {})
        if (
            current_tracking_direction
            and latest_tracking.get("run_id") in current_run_ids
            and result["currentStatus"] in {"stable", "baseline"}
        ):
            result["currentStatus"] = current_tracking_direction
            result["currentStatusLabel"] = {
                "upgrade": "本轮上调",
                "downgrade": "本轮下调",
                "changed": "本轮关键项变化",
            }[current_tracking_direction]
            result["currentExplanation"] = latest_tracking.get("reason") or latest["explanation"]
            result["triggerCategories"] = ["自动跟踪"]
            result["evidence"] = latest["evidence"]
    results.sort(
        key=lambda item: (
            {"upgrade": 0, "downgrade": 1, "changed": 2, "baseline": 3, "stable": 4}[item["currentStatus"]],
            item["projectName"].lower(),
        )
    )

    generated_at = datetime.now(timezone.utc)
    non_baseline_history = [
        item for item in history if item["change_direction"] != "baseline"
    ]
    recent_24h = [
        item
        for item in non_baseline_history
        if parse_timestamp(item.get("observed_at"))
        and parse_timestamp(item.get("observed_at")) >= generated_at - timedelta(hours=24)
    ]
    recent_7d = [
        item
        for item in non_baseline_history
        if parse_timestamp(item.get("observed_at"))
        and parse_timestamp(item.get("observed_at")) >= generated_at - timedelta(days=7)
    ]
    counts = Counter(item["currentStatus"] for item in results)
    latest_reviewable = {}
    for item in sorted(
        tracking_results,
        key=lambda row: row.get("finished_at") or row.get("started_at") or "",
        reverse=True,
    ):
        if not item["decisionReview"]["required"]:
            continue
        task_key = item.get("tracking_task_id") or item.get("case_id")
        if task_key not in latest_reviewable:
            latest_reviewable[task_key] = item
    latest_reviewable_by_case = {
        item.get("case_id"): item
        for item in latest_reviewable.values()
        if item.get("case_id")
    }
    for result in results:
        reviewable = latest_reviewable_by_case.get(result["caseId"])
        if not reviewable:
            continue
        result["reviewTrackingResult"] = reviewable
        result["decisionReview"] = reviewable["decisionReview"]
    review_queue = [
        {
            **item,
            "detailUrl": result_by_case.get(item.get("case_id"), {}).get("detailUrl", ""),
            "symbol": result_by_case.get(item.get("case_id"), {}).get("symbol", ""),
        }
        for item in latest_reviewable.values()
        if item["decisionReview"]["status"] == "pending"
    ]
    review_counts = Counter(
        item["decisionReview"]["status"] for item in latest_reviewable.values()
    )
    return {
        "version": RULE_VERSION,
        "release": "C1.3",
        "generatedAt": now,
        "title": "凸性机会变化解释",
        "boundary": (
            "变化解释同时读取四层规则结果与自动跟踪证据。"
            "新证据只能形成关键项变化；只有规则已经改变行动结论时才显示上调或停止。"
            "同一运行合并展示，重复确认不制造假变化。"
        ),
        "thresholds": [
            {"field": "阶段与分类", "rule": "发生变化即记录"},
            {"field": "关注顺序分与错配分", "rule": "累计变化达到5分"},
            {"field": "价格", "rule": "相对基线累计变化达到10%"},
            {"field": "成交额、流动性与FDV", "rule": "相对基线累计变化达到25%"},
        ],
        "counts": {
            "total": len(results),
            "upgrade": counts["upgrade"],
            "downgrade": counts["downgrade"],
            "changed": counts["changed"],
            "stable": counts["stable"],
            "baseline": counts["baseline"],
            "history": len(history),
            "recent24h": len(recent_24h),
            "recent7d": len(recent_7d),
            "trackingExecutions": len(tracking_results),
            "trackingMaterial": sum(
                bool(tracking_direction(item)) for item in tracking_results
            ),
            "trackingUpgrade": sum(
                tracking_direction(item) == "upgrade" for item in tracking_results
            ),
            "trackingStop": sum(
                tracking_direction(item) == "downgrade" for item in tracking_results
            ),
            "trackingNewEvidence": sum(
                tracking_direction(item) == "changed" for item in tracking_results
            ),
            "decisionReviewRequired": len(latest_reviewable),
            "decisionReviewPending": review_counts["pending"],
            "decisionReviewConfirmed": review_counts["confirmed"],
            "decisionReviewRejected": review_counts["rejected"],
        },
        "latestRun": {
            "runId": run_id or "",
            "opportunityGeneratedAt": opportunity.get("generatedAt") or "",
            "updateGeneratedAt": update_center.get("generatedAt") or "",
        },
        "c18": {
            "version": "C1.8",
            "homeLimit": 5,
            "pageSize": 20,
            "mergeKey": "case_id",
            "noisePolicy": "普通行情变化在首页折叠，完整变化页按影响等级和时间筛选。",
        },
        "items": results,
        "recent24h": recent_24h,
        "recent7d": recent_7d,
        "reviewQueue": review_queue,
        "history": history,
    }


def write_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        OUTPUT_PREFIX
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)


def rebuild_change_explanations_snapshot(
    db_path=DEFAULT_DB_PATH,
    opportunity_path=DEFAULT_OPPORTUNITY_PATH,
    update_path=DEFAULT_UPDATE_PATH,
    output_path=DEFAULT_OUTPUT_PATH,
):
    opportunity = load_js_payload(opportunity_path, OPPORTUNITY_PREFIX)
    update_center = load_js_payload(update_path, UPDATE_PREFIX)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        snapshot = build_snapshot(connection, opportunity, update_center)
    finally:
        connection.close()
    write_snapshot(snapshot, output_path)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="生成C1.3-08凸性机会变化解释快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--opportunity", type=Path, default=DEFAULT_OPPORTUNITY_PATH)
    parser.add_argument("--updates", type=Path, default=DEFAULT_UPDATE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_change_explanations_snapshot(
        db_path=args.db,
        opportunity_path=args.opportunity,
        update_path=args.updates,
        output_path=args.output,
    )
    print(json.dumps(snapshot["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
