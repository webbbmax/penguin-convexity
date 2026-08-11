#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
DEFAULT_OPPORTUNITY_PATH = APP_ROOT / "opportunity-center-snapshot.js"
DEFAULT_ROUTE_PATH = APP_ROOT / "research-route-snapshot.js"
DEFAULT_OUTPUT_PATH = APP_ROOT / "tracking-task-snapshot.js"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "convexity.db"
OPPORTUNITY_PREFIX = "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = "
ROUTE_PREFIX = "window.PENGUIN_CONVEXITY_RESEARCH_ROUTES = "
OUTPUT_PREFIX = "window.PENGUIN_CONVEXITY_TRACKING_TASKS = "

ROUTE_DEFAULTS = {
    "startup": {
        "label": "早期项目",
        "nextEvidence": "项目基础档案",
        "sources": ["项目官网", "官方 X", "GitHub", "产品文档", "链上浏览器"],
    },
    "mature": {
        "label": "OG项目",
        "nextEvidence": "新闻发布前的一手事实",
        "sources": ["治理论坛", "GitHub", "官方公告", "链上数据", "监管文件"],
    },
    "hybrid": {
        "label": "潜力项目",
        "nextEvidence": "基础档案与前置信号",
        "sources": ["项目官网", "GitHub", "治理提案", "链上数据", "官方公告"],
    },
}
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
STATUS_ORDER = {"due": 0, "open": 1, "monitoring": 2, "closed": 3}
ROUTE_ORDER = {"startup": 0, "mature": 1, "hybrid": 2}
EXECUTION_STATUS_LABELS = {
    "success": "已发现有效记录",
    "partial_success": "部分信源未完成",
    "no_change": "已检查，暂无新增",
    "failed": "检查失败",
}
DECISION_LABELS = {
    "upgrade": "升级复核",
    "continue": "继续跟踪",
    "stop": "停止跟踪",
    "monitor": "行动后监测",
    "undetermined": "暂无法判定",
}
REVIEW_STATUS_LABELS = {
    "pending": "待结论复核",
    "confirmed": "已确认采用",
    "rejected": "未采用，重新复查",
}
FOLLOW_UP_STATUS_LABELS = {
    "not_required": "无需二次验证",
    "pending": "等待二次验证",
    "completed": "二次验证已完成",
    "failed": "二次验证失败，等待重试",
}


def utc_now():
    return datetime.now(timezone.utc)


def iso_time(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decision_follow_up(decision_result, latest_execution):
    review = decision_result.get("decisionReview") or {}
    review_status = review.get("status")
    if review_status not in {"confirmed", "rejected"}:
        return {
            "required": False,
            "status": "not_required",
            "statusLabel": FOLLOW_UP_STATUS_LABELS["not_required"],
        }
    reviewed_at = parse_time(review.get("reviewedAt")) or utc_now()
    if review_status == "rejected":
        follow_up_type = "rejected_recheck"
        type_label = "驳回结论重新复查"
        cadence_days = 0
        priority = "P0"
    elif decision_result.get("decision") == "upgrade":
        follow_up_type = "verify_upgrade"
        type_label = "上调结论二次验证"
        cadence_days = 1
        priority = "P0"
    else:
        follow_up_type = "verify_stop"
        type_label = "停止结论二次验证"
        cadence_days = 7
        priority = "P1"
    due_at = reviewed_at + timedelta(days=cadence_days)
    verification = None
    if latest_execution:
        finished_at = parse_time(latest_execution.get("finished_at"))
        if (
            latest_execution.get("tracking_result_id")
            != decision_result.get("tracking_result_id")
            and finished_at
            and finished_at > reviewed_at
        ):
            verification = latest_execution
    status = "pending"
    if verification:
        status = (
            "failed"
            if verification.get("execution_status") in {"failed", "partial_success"}
            else "completed"
        )
    return {
        "required": True,
        "type": follow_up_type,
        "typeLabel": type_label,
        "status": status,
        "statusLabel": FOLLOW_UP_STATUS_LABELS[status],
        "priority": priority,
        "dueAt": iso_time(due_at),
        "reviewedAt": review.get("reviewedAt") or "",
        "reviewAction": review_status,
        "reviewNote": review.get("note") or "",
        "reviewDecision": decision_result.get("decision") or "",
        "reviewDecisionLabel": decision_result.get("decisionLabel") or "",
        "reviewTrackingResultId": decision_result.get("tracking_result_id") or "",
        "conclusionBefore": decision_result.get("conclusion_before") or "",
        "conclusionAfter": decision_result.get("conclusion_after") or "",
        "verificationResult": verification,
    }


def apply_decision_follow_up(task, follow_up, now):
    if not follow_up.get("required"):
        return
    task["decisionFollowUp"] = follow_up
    if follow_up["status"] == "completed":
        return
    due_at = parse_time(follow_up.get("dueAt")) or now
    if follow_up["status"] == "failed":
        due_at = now
    task["priority"] = follow_up["priority"]
    task["nextReviewAt"] = iso_time(due_at)
    task["status"] = "due" if due_at <= now else "open"
    task["statusLabel"] = (
        "二次验证失败，需重试"
        if follow_up["status"] == "failed"
        else "二次验证到期"
        if task["status"] == "due"
        else "等待二次验证"
    )
    task["taskType"] = follow_up["type"]
    task["taskTypeLabel"] = follow_up["typeLabel"]
    task["title"] = f"{follow_up['typeLabel']}：{task['projectName']}"
    task["reviewCadenceDays"] = (
        7 if follow_up["type"] == "verify_stop" else 1
    )
    task["suggestedSources"] = [
        "原结论所用一手来源",
        "独立交叉验证来源",
        "链上或市场数据",
        "项目官方最新状态",
    ]
    if follow_up["type"] == "rejected_recheck":
        reason = follow_up.get("reviewNote") or "本次自动结论未被采用。"
        task["whyNow"] = f"人工未采用本次结论：{reason}"
        task["nextStep"] = "围绕驳回原因重新取证，不复用原结论的同一组依据。"
        task["checklist"] = [
            f"首先核验驳回原因：{reason}",
            "至少补充一条独立于原结论的新证据，并标明来源与时间。",
            "没有新增独立证据时保持上一结论，不重复生成同一上调或停止判断。",
        ]
        task["evidenceTarget"] = f"驳回原因与新增独立证据：{reason}"
        task["currentConclusion"] = (
            "复查中：本次自动结论未采用；上一结论保持为"
            f"{follow_up.get('conclusionBefore') or '待确认'}"
        )
    elif follow_up["type"] == "verify_upgrade":
        task["whyNow"] = "上调结论已确认，需要验证确认信号没有在短时间内失效。"
        task["nextStep"] = "重新核验确认信号、交易性和失效条件，判断上调能否继续成立。"
        task["checklist"] = [
            "确认原上调依据仍然存在，且不是重复数据或短时噪声。",
            "重新检查流动性、卖出路径、滑点与最大可控亏损。",
            "若确认信号消失或失效条件触发，立即形成新的停止复核结论。",
        ]
        task["evidenceTarget"] = "已确认上调结论的持续性"
    else:
        task["whyNow"] = "停止结论已确认，需要在冷却期后验证失效事实是否持续。"
        task["nextStep"] = "复核停止条件是否仍成立，并检查是否出现足以重新建案的新事实。"
        task["checklist"] = [
            "确认原停止条件仍然存在，并保存最新一手证据。",
            "检查是否出现新的独立反证、官方修复或交易性恢复。",
            "没有足够反证时维持停止；出现实质变化时重新进入研究而非直接建仓。",
        ]
        task["evidenceTarget"] = "停止条件持续性与重新建案证据"


def load_js_payload(path, prefix):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"无法识别快照格式：{path}")
    return json.loads(text[len(prefix):-1])


def stable_id(case_id):
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16]
    return f"tracking-task-{digest}"


def fallback_route(case):
    return {
        "masterId": "",
        "caseId": case["caseId"],
        "routeId": "hybrid",
        "routeLabel": "潜力项目",
        "routeShortLabel": "潜力项目",
        "nextEvidence": "项目生命周期与官网",
        "completeCount": 0,
        "totalChecks": 0,
        "checklist": [],
    }


def primary_gap(case, route):
    screening = case.get("screening") or {}
    stage = case.get("opportunityStage") or {}
    reasons = (
        screening.get("failedReasons")
        or screening.get("pendingReasons")
        or [stage.get("stageReason"), route.get("nextEvidence")]
    )
    return next(
        (str(item).strip() for item in reasons if str(item or "").strip()),
        "当前行动条件尚未完整",
    )


def task_kind(case, route, gap):
    stage = case.get("opportunityStage") or {}
    final_action = stage.get("finalActionCategory")
    stage_id = stage.get("stage")
    machine_state = stage.get("blockerStatus")
    machine_task_types = {
        "identity_pending": ("identity", "身份补证"),
        "asset_pending": ("identity", "资产身份补证"),
        "market_exit_pending": ("tradeability", "市场与退出补证"),
        "convexity_structure_pending": (
            "value_capture",
            "凸性结构补证",
        ),
        "priority_watch": ("mismatch_scoring", "点火与确认监测"),
    }
    if machine_state in machine_task_types:
        return machine_task_types[machine_state]
    if machine_state == "evidence_building":
        if route.get("routeId") == "startup":
            return "foundation", "基础档案补齐"
        if route.get("routeId") == "mature":
            return "pre_signal", "前置信号监测"
        return "balanced_research", "基础与信号并行补齐"
    if final_action == "invalidated":
        return "closed_review", "失效复核"
    if final_action == "reflexive":
        return "reflexive_management", "反身性管理"
    if final_action in {"ordinary", "extreme"}:
        return "execution_monitor", "行动后监测"
    if any(word in gap for word in ("流动性", "滑点", "卖出路径", "交易性")):
        return "tradeability", "交易性补证"
    if "错配分" in gap:
        return "mismatch_scoring", "错配评分补证"
    if any(word in gap for word in ("身份", "项目主体", "资产映射")):
        return "identity", "身份补证"
    if any(word in gap for word in ("合约风险", "风险")):
        return "risk_review", "风险核验"
    if "价值捕获" in gap:
        return "value_capture", "价值捕获核验"
    if "允许 L" in gap or "成熟度" in gap:
        if route.get("routeId") == "startup":
            return "foundation", "基础档案补齐"
        if route.get("routeId") == "mature":
            return "pre_signal", "前置信号监测"
        return "balanced_research", "基础与信号并行补齐"
    if stage_id == "model_pending" or "模型" in gap:
        return "model_refresh", "模型刷新"
    if stage_id == "decay":
        return "odds_review", "赔率复核"
    if route.get("routeId") == "startup":
        return "foundation", "基础档案补齐"
    if route.get("routeId") == "mature":
        return "pre_signal", "前置信号监测"
    return "balanced_research", "基础与信号并行补齐"


def next_step_for(task_type, route, gap):
    evidence = route.get("nextEvidence") or ROUTE_DEFAULTS[
        route.get("routeId", "hybrid")
    ]["nextEvidence"]
    rules = {
        "tradeability": "重新核验可交易池、流动性、2万美元退出路径和预计滑点。",
        "mismatch_scoring": "补齐事实确定性、经济增量、价值捕获、事件临近和价格反应证据，重新计算错配分。",
        "identity": "交叉核验官网、第三方资产注册与链上合约，确认项目主体和可交易资产属于同一对象。",
        "risk_review": "核验增发、冻结、税费、升级权限、持仓集中和撤池风险，更新最大可控亏损。",
        "value_capture": "确认产品采用如何传导到代币需求、费用、回购、质押或供应收缩。",
        "model_refresh": "运行最新四层模型，确认当前结论仍为只观察还是已经发生升级或失效。",
        "odds_review": "复核点火窗口、价格反应和剩余凸性，判断继续保留还是转入失效。",
        "foundation": f"从官方一手来源补齐“{evidence}”，确认项目是否达到继续研究的最低资料线。",
        "pre_signal": f"检查“{evidence}”是否出现治理、代码、部署、监管或链上采用的新事实。",
        "balanced_research": f"补齐“{evidence}”，再判断应优先转向基础档案还是前置信号研究。",
        "execution_monitor": "核验确认信号、流动性和失效条件，避免把已触发动作当成永久结论。",
        "reflexive_management": "跟踪趋势、资金、流动性和价格结构，按反身性规则管理赔率衰减。",
        "closed_review": "保留失效证据和退出原因，仅在出现新的独立事实时重新建案。",
    }
    return rules[task_type], gap


def cadence_days(case, route):
    stage = case.get("opportunityStage") or {}
    machine_cadence = {
        "identity_pending": 14,
        "asset_pending": 7,
        "market_exit_pending": 3,
        "evidence_building": 7,
        "convexity_structure_pending": 3,
        "priority_watch": 2,
    }
    if stage.get("blockerStatus") in machine_cadence:
        return machine_cadence[stage["blockerStatus"]]
    if stage.get("finalActionCategory") in {"ordinary", "extreme", "reflexive"}:
        return 1
    if stage.get("finalActionCategory") == "invalidated":
        return 30
    if stage.get("stage") in {"action_pending", "model_pending"}:
        return 1
    if case.get("ignitionProximity") in {"immediate", "near"}:
        return 1
    if stage.get("stage") in {"qualified_pending", "decay"}:
        return 3
    return {"startup": 3, "mature": 1, "hybrid": 7}.get(
        route.get("routeId"), 7
    )


def priority_for(case, route):
    stage = case.get("opportunityStage") or {}
    final_action = stage.get("finalActionCategory")
    if final_action in {"ordinary", "extreme", "reflexive"}:
        return "P0"
    if final_action == "invalidated":
        return "P3"
    if stage.get("stage") == "action_pending":
        return "P0"
    if (
        stage.get("stage") in {"qualified_pending", "decay", "model_pending"}
        or case.get("ignitionProximity") in {"immediate", "near"}
        or route.get("routeId") == "mature"
    ):
        return "P1"
    return "P2"


def latest_reference(case, route, fallback):
    candidates = [
        parse_time(case.get("sourceSnapshotAt")),
        parse_time((case.get("latestMarket") or {}).get("observedAt")),
    ]
    candidates.extend(
        parse_time(item.get("observedAt"))
        for item in route.get("checklist") or []
    )
    return max((item for item in candidates if item), default=fallback)


def task_status(case, next_review_at, now):
    final_action = (case.get("opportunityStage") or {}).get(
        "finalActionCategory"
    )
    if final_action == "invalidated":
        return "closed", "已关闭"
    if final_action in {"ordinary", "extreme", "reflexive"}:
        return "monitoring", "持续监测"
    if next_review_at <= now:
        return "due", "需要复查"
    return "open", "等待复查"


def task_checklist(task_type, next_step):
    middle = {
        "tradeability": "记录真实交易池、退出金额、预计滑点和合约风险来源。",
        "mismatch_scoring": "每个评分项至少绑定一条可复核证据，不用主观印象补分。",
        "identity": "用至少两个独立入口确认项目主体、代币和合约关系。",
        "risk_review": "区分可控风险、行动阻断和仍待核验的风险。",
        "value_capture": "写清产品增长到代币价值之间的传导链。",
        "model_refresh": "确认输入数据时间和规则版本均为最新。",
        "odds_review": "比较事实兑现、价格反应和点火窗口是否同步变化。",
        "foundation": "保存官网、X、GitHub、文档或链上入口及核验时间。",
        "pre_signal": "优先保存治理、代码、部署、监管或链上数据的一手链接。",
        "balanced_research": "分别记录基础资料缺口与前置信号缺口。",
        "execution_monitor": "核验行动后确认信号、流动性与最大可控亏损。",
        "reflexive_management": "记录趋势、资金、流动性和赔率衰减。",
        "closed_review": "保存失效事实、退出原因和重新建案条件。",
    }[task_type]
    return [
        next_step,
        middle,
        "根据新证据更新当前结论：升级、继续跟踪或停止跟踪。",
    ]


def build_task(case, route, generated_at, now):
    stage = case.get("opportunityStage") or {}
    gap = primary_gap(case, route)
    task_type, task_type_label = task_kind(case, route, gap)
    next_step, why_now = next_step_for(task_type, route, gap)
    if stage.get("conclusionSource") == "machine_conclusion":
        next_step = stage.get("nextStep") or next_step
        why_now = stage.get("stageReason") or why_now
    cadence = cadence_days(case, route)
    latest_at = latest_reference(case, route, generated_at)
    next_review_at = (
        parse_time(case.get("nextReviewAt"))
        or latest_at + timedelta(days=cadence)
    )
    status, status_label = task_status(case, next_review_at, now)
    route_id = route.get("routeId") or "hybrid"
    route_defaults = ROUTE_DEFAULTS.get(route_id, ROUTE_DEFAULTS["hybrid"])
    evidence_target = {
        "tradeability": "流动性、卖出路径与退出滑点",
        "mismatch_scoring": "事实新闻错配评分所需证据",
        "identity": "项目主体、代币和合约身份",
        "risk_review": "合约权限、集中度与可控亏损",
        "value_capture": "产品采用到代币价值的传导证据",
        "model_refresh": "最新模型输入和规则版本",
        "odds_review": "点火窗口、价格反应与剩余凸性",
    }.get(
        task_type,
        route.get("nextEvidence") or route_defaults["nextEvidence"],
    )
    suggested_sources = {
        "tradeability": ["DexScreener", "CoinGecko", "链上浏览器", "合约只读调用"],
        "identity": ["项目官网", "CoinGecko", "链上浏览器", "官方文档"],
        "risk_review": ["链上浏览器", "合约代码", "审计报告", "持仓分布"],
        "mismatch_scoring": ["项目一手来源", "治理或代码记录", "链上数据", "市场数据"],
    }.get(task_type, route_defaults["sources"])
    priority = priority_for(case, route)
    final_action = stage.get("finalActionCategory") or "observe"
    upgrade_condition = (
        "；".join(stage.get("upgradeConditions") or [])
        or (
            "首要阻断项关闭，证据、风险和交易门槛全部通过，"
            "并由机器规则重新给出普通建仓或极限试仓。"
            if final_action == "observe"
            else "确认信号持续成立，且没有触发失效条件或赔率衰减。"
        )
    )
    stop_condition = (
        "；".join(stage.get("invalidationConditions") or [])
        or case.get("invalidation")
        or "项目主体无法核验、价值捕获不成立、交易性不可接受或点火窗口失效。"
    )
    conclusion_text = stage.get("finalActionReason") or why_now
    action_label = stage.get("finalActionLabel") or "只观察"
    current_conclusion = (
        conclusion_text
        if conclusion_text.startswith(action_label)
        else f"{action_label}：{conclusion_text}"
    )
    return {
        "taskId": stable_id(case["caseId"]),
        "caseId": case["caseId"],
        "projectId": case.get("projectId") or "",
        "masterId": route.get("masterId") or "",
        "projectName": case.get("projectName") or "",
        "symbol": case.get("symbol") or "",
        "detailUrl": case.get("detailUrl") or "project-detail.html",
        "projectCategory": route_id,
        "projectCategoryLabel": route.get("routeLabel") or route_defaults["label"],
        "currentAction": stage.get("finalActionLabel") or "只观察",
        "currentStage": stage.get("stage") or "observe",
        "currentStageLabel": stage.get("stageLabel") or "研究观察",
        "priority": priority,
        "status": status,
        "statusLabel": status_label,
        "taskType": task_type,
        "taskTypeLabel": task_type_label,
        "title": f"{task_type_label}：{case.get('projectName') or case['caseId']}",
        "whyNow": why_now,
        "nextStep": next_step,
        "checklist": task_checklist(task_type, next_step),
        "evidenceTarget": evidence_target,
        "suggestedSources": suggested_sources,
        "evidenceCompleteCount": int(route.get("completeCount") or 0),
        "evidenceTotal": int(route.get("totalChecks") or 0),
        "reviewCadenceDays": cadence,
        "latestEvidenceAt": iso_time(latest_at),
        "nextReviewAt": iso_time(next_review_at),
        "upgradeCondition": upgrade_condition,
        "stopCondition": stop_condition,
        "currentConclusion": current_conclusion,
        "taskVersion": "C1.5-05",
    }


def latest_executions(db_path):
    if not db_path or not Path(db_path).exists():
        return {}
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT result.*
            FROM tracking_task_runs result
            JOIN (
              SELECT tracking_task_id, MAX(finished_at) AS finished_at
              FROM tracking_task_runs
              GROUP BY tracking_task_id
            ) latest
              ON latest.tracking_task_id = result.tracking_task_id
             AND latest.finished_at = result.finished_at
            ORDER BY result.tracking_result_id DESC
            """
        ).fetchall()
        decision_rows = connection.execute(
            """
            SELECT result.*
            FROM tracking_task_runs result
            WHERE result.decision IN ('upgrade', 'stop')
              AND result.execution_status <> 'failed'
            ORDER BY result.finished_at DESC, result.tracking_result_id DESC
            """
        ).fetchall()
        review_rows = connection.execute(
            """
            SELECT review.*
            FROM tracking_decision_reviews review
            ORDER BY review.reviewed_at DESC, review.tracking_review_id DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        connection.close()
    latest_review_by_result = {}
    for review in review_rows:
        if review["tracking_result_id"] not in latest_review_by_result:
            latest_review_by_result[review["tracking_result_id"]] = dict(review)
    executions = {}
    for row in rows:
        if row["tracking_task_id"] in executions:
            continue
        review_required = (
            row["decision"] in {"upgrade", "stop"}
            and row["execution_status"] != "failed"
        )
        review = latest_review_by_result.get(row["tracking_result_id"])
        review_status = (
            review.get("review_action")
            if review
            else "pending" if review_required else "not_required"
        )
        executions[row["tracking_task_id"]] = {
            **dict(row),
            "statusLabel": EXECUTION_STATUS_LABELS.get(
                row["execution_status"],
                row["execution_status"],
            ),
            "decisionLabel": DECISION_LABELS.get(
                row["decision"],
                row["decision"],
            ),
            "sourcesChecked": json.loads(row["sources_checked_json"] or "[]"),
            "sourceResults": json.loads(row["source_results_json"] or "[]"),
            "findings": json.loads(row["findings_json"] or "[]"),
            "decisionReview": {
                "required": review_required,
                "status": review_status,
                "statusLabel": REVIEW_STATUS_LABELS.get(
                    review_status,
                    "无需人工复核",
                ),
                "reviewId": review.get("tracking_review_id") if review else "",
                "note": review.get("review_note") if review else "",
                "actor": review.get("actor") if review else "",
                "reviewedAt": review.get("reviewed_at") if review else "",
            },
        }
    latest_decision_by_task = {}
    for row in decision_rows:
        if row["tracking_task_id"] in latest_decision_by_task:
            continue
        review = latest_review_by_result.get(row["tracking_result_id"])
        review_status = review.get("review_action") if review else "pending"
        latest_decision_by_task[row["tracking_task_id"]] = {
            **dict(row),
            "statusLabel": EXECUTION_STATUS_LABELS.get(
                row["execution_status"],
                row["execution_status"],
            ),
            "decisionLabel": DECISION_LABELS.get(
                row["decision"],
                row["decision"],
            ),
            "findings": json.loads(row["findings_json"] or "[]"),
            "decisionReview": {
                "required": True,
                "status": review_status,
                "statusLabel": REVIEW_STATUS_LABELS[review_status],
                "reviewId": review.get("tracking_review_id") if review else "",
                "note": review.get("review_note") if review else "",
                "actor": review.get("actor") if review else "",
                "reviewedAt": review.get("reviewed_at") if review else "",
            },
        }
    for task_id, execution in executions.items():
        decision_change = latest_decision_by_task.get(task_id)
        if decision_change:
            decision_change["decisionFollowUp"] = decision_follow_up(
                decision_change,
                execution,
            )
        execution["latestDecisionChange"] = decision_change
    return executions


def build_snapshot(
    opportunity_path=DEFAULT_OPPORTUNITY_PATH,
    route_path=DEFAULT_ROUTE_PATH,
    now=None,
    db_path=None,
):
    opportunity = load_js_payload(opportunity_path, OPPORTUNITY_PREFIX)
    routes = load_js_payload(route_path, ROUTE_PREFIX)
    now = now or utc_now()
    generated_at = parse_time(opportunity.get("generatedAt")) or now
    route_by_case = {
        item["caseId"]: item
        for item in routes.get("records") or []
        if item.get("caseId")
    }
    executions = latest_executions(db_path)
    tasks = [
        build_task(
            case,
            route_by_case.get(case["caseId"]) or fallback_route(case),
            generated_at,
            now,
        )
        for case in opportunity.get("cases") or []
    ]
    for task in tasks:
        execution = executions.get(task["taskId"])
        task["latestExecution"] = execution
        decision_change = execution.get("latestDecisionChange") if execution else None
        task["reviewTrackingResult"] = decision_change
        task["decisionReview"] = (
            decision_change["decisionReview"]
            if decision_change
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
        task["decisionFollowUp"] = (
            decision_change.get("decisionFollowUp")
            if decision_change
            else {
                "required": False,
                "status": "not_required",
                "statusLabel": FOLLOW_UP_STATUS_LABELS["not_required"],
            }
        )
        if execution and execution.get("next_review_at"):
            next_review_at = parse_time(execution["next_review_at"])
            if next_review_at:
                task["nextReviewAt"] = iso_time(next_review_at)
                task["status"], task["statusLabel"] = task_status(
                    {"opportunityStage": {"finalActionCategory": (
                        "invalidated"
                        if task["currentAction"] == "失效/排除"
                        else "ordinary"
                        if task["currentAction"] == "普通建仓"
                        else "extreme"
                        if task["currentAction"] == "极限试仓"
                        else "reflexive"
                        if task["currentAction"] == "反身性管理"
                        else "observe"
                    )}},
                    next_review_at,
                    now,
                )
        apply_decision_follow_up(task, task["decisionFollowUp"], now)
        execution = task.get("latestExecution") or {}
        execution_status = execution.get("execution_status")
        if task["decisionReview"].get("required") and task["decisionReview"].get("status") == "pending":
            c18_status = "queued"
            owner = "用户确认"
            next_action = "查看支持证据和反面证据后确认采用或驳回；继续跟踪不需要你处理。"
        elif execution_status == "failed":
            c18_status = "failed"
            owner = "系统自动运行"
            next_action = "系统保留上次结论；在工作台查看失败范围并重试。"
        elif execution_status == "partial_success":
            c18_status = "partial"
            owner = "系统自动运行"
            next_action = "系统会继续缺失来源；不把部分结果当成完整结论。"
        elif execution_status == "no_change":
            c18_status = "no_change"
            owner = "系统自动运行"
            next_action = "系统已检查，本轮没有足以改变结论的新事实。"
        elif execution_status in {"success", "completed"}:
            c18_status = "completed"
            owner = "系统自动运行"
            next_action = "系统已回写本轮结果，并按下次复查时间继续。"
        elif task["status"] == "due":
            c18_status = "queued"
            owner = "系统自动调度"
            next_action = "已到复查时间，等待每小时调度器执行。"
        elif task["status"] == "closed":
            c18_status = "not_due"
            owner = "系统自动运行"
            next_action = "任务已关闭；只有出现独立新事实才重新建案。"
        else:
            c18_status = "not_due"
            owner = "系统自动运行"
            next_action = f"尚未到复查时间，系统将在 {task['nextReviewAt']} 自动检查。"
        task["c18Status"] = c18_status
        task["c18StatusLabel"] = {
            "not_due": "尚未到期",
            "queued": "已到期，等待调度",
            "running": "正在执行",
            "no_change": "已检查，无变化",
            "completed": "已执行并有结果",
            "partial": "部分来源未完成",
            "failed": "执行失败，可重试",
            "paused": "自动运行已暂停",
            "quota_delayed": "额度保护延后",
        }[c18_status]
        task["c18Owner"] = owner
        task["c18NextAction"] = next_action
        task["c18StatusExplanation"] = f"负责人：{owner}。{next_action}"
    tasks.sort(
        key=lambda item: (
            ROUTE_ORDER.get(item["projectCategory"], 9),
            PRIORITY_ORDER[item["priority"]],
            STATUS_ORDER[item["status"]],
            item["nextReviewAt"],
            item["projectName"].lower(),
        )
    )
    priorities = Counter(item["priority"] for item in tasks)
    statuses = Counter(item["status"] for item in tasks)
    categories = Counter(item["projectCategory"] for item in tasks)
    task_types = Counter(item["taskType"] for item in tasks)
    c18_statuses = Counter(item["c18Status"] for item in tasks)
    return {
        "version": "C1.5-05",
        "release": "C1.3",
        "generatedAt": iso_time(now),
        "title": "凸性项目自动跟踪任务",
        "boundary": (
            "跟踪任务只定义下一步核验动作、复查时间、升级条件和停止条件；"
            "任务优先级不直接产生建仓或试仓结论。"
        ),
        "source": {
            "opportunityVersion": opportunity.get("version"),
            "opportunityGeneratedAt": opportunity.get("generatedAt"),
            "routeVersion": routes.get("version"),
            "routeGeneratedAt": routes.get("generatedAt"),
        },
        "counts": {
            "total": len(tasks),
            "activeTracking": sum(
                item["currentAction"] == "只观察" for item in tasks
            ),
            "due": statuses["due"],
            "open": statuses["open"],
            "monitoring": statuses["monitoring"],
            "closed": statuses["closed"],
            "P0": priorities["P0"],
            "P1": priorities["P1"],
            "P2": priorities["P2"],
            "P3": priorities["P3"],
            "executed": sum(bool(item["latestExecution"]) for item in tasks),
            "retryable": sum(
                bool(
                    item["latestExecution"]
                    and item["latestExecution"]["retryable"]
                    and item["latestExecution"]["retry_status"]
                    in {"not_requested", "pending", "failed"}
                )
                for item in tasks
            ),
            "decisionReviewPending": sum(
                item["decisionReview"]["status"] == "pending"
                for item in tasks
            ),
            "decisionReviewRejected": sum(
                item["decisionReview"]["status"] == "rejected"
                for item in tasks
            ),
            "decisionFollowUpPending": sum(
                item["decisionFollowUp"]["status"] == "pending"
                for item in tasks
            ),
            "decisionFollowUpDue": sum(
                item["decisionFollowUp"]["status"] in {"pending", "failed"}
                and item["status"] == "due"
                for item in tasks
            ),
            "decisionFollowUpFailed": sum(
                item["decisionFollowUp"]["status"] == "failed"
                for item in tasks
            ),
            "decisionFollowUpCompleted": sum(
                item["decisionFollowUp"]["status"] == "completed"
                for item in tasks
            ),
        },
        "categoryCounts": dict(categories),
        "taskTypeCounts": dict(task_types),
        "c18": {
            "version": "C1.8",
            "statusCounts": dict(c18_statuses),
            "pageSize": 20,
            "ownerBoundary": "系统自动处理继续跟踪和无变化；重大升级或停止进入用户确认。",
        },
        "tasks": tasks,
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
    return output_path


def rebuild_tracking_tasks_snapshot(
    opportunity_path=DEFAULT_OPPORTUNITY_PATH,
    route_path=DEFAULT_ROUTE_PATH,
    output_path=DEFAULT_OUTPUT_PATH,
    db_path=DEFAULT_DB_PATH,
):
    snapshot = build_snapshot(
        opportunity_path,
        route_path,
        db_path=db_path,
    )
    write_snapshot(snapshot, output_path)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="生成C1.5-05凸性项目自动跟踪任务")
    parser.add_argument("--opportunity", type=Path, default=DEFAULT_OPPORTUNITY_PATH)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    snapshot = rebuild_tracking_tasks_snapshot(
        args.opportunity,
        args.routes,
        args.output,
        args.db,
    )
    print(json.dumps(snapshot["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
