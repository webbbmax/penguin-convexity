#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from c1_8_runtime import build_c18_home


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE_PATH = PROJECT_ROOT / "app" / "candidate-pool-snapshot.js"
DEFAULT_FOUR_LAYER_PATH = PROJECT_ROOT / "app" / "four-layer-screening-snapshot.js"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "opportunity-center-snapshot.js"
CANDIDATE_PREFIX = "window.PENGUIN_CONVEXITY_CANDIDATES = "
FOUR_LAYER_PREFIX = "window.PENGUIN_CONVEXITY_FOUR_LAYER = "

STAGES = [
    {
        "id": "actionable",
        "label": "当前可行动",
        "shortLabel": "可行动",
        "definition": "四层模型给出普通建仓或极限试仓，且现行硬门槛完整通过。",
    },
    {
        "id": "action_pending",
        "label": "动作待门槛",
        "shortLabel": "动作待门槛",
        "definition": "四层模型已经给出行动方向，但身份、证据、风险或交易门槛尚未通过。",
    },
    {
        "id": "qualified_pending",
        "label": "入选待补证",
        "shortLabel": "待补证",
        "definition": "进入当前筛选方案，但四层模型仍要求观察或有字段待核验。",
    },
    {
        "id": "observe",
        "label": "研究观察",
        "shortLabel": "观察",
        "definition": "保留研究和监测，不形成当前建仓或试仓动作。",
    },
    {
        "id": "decay",
        "label": "赔率衰减",
        "shortLabel": "衰减",
        "definition": "事实可能仍成立，但剩余赔率、点火窗口或价格反应已经变差。",
    },
    {
        "id": "model_pending",
        "label": "模型待运行",
        "shortLabel": "待运行",
        "definition": "候选尚未取得最新四层模型结果，不使用旧动作代替。",
    },
    {
        "id": "reflexive",
        "label": "反身性管理",
        "shortLabel": "反身性",
        "definition": "已进入L5或趋势共振阶段，不再作为未兑现的早期凸性机会。",
    },
    {
        "id": "invalidated",
        "label": "失效与排除",
        "shortLabel": "失效",
        "definition": "四层模型排除、风险阻断或原凸性逻辑已经失效。",
    },
]
STAGE_BY_ID = {item["id"]: item for item in STAGES}
STAGE_ORDER = {item["id"]: index for index, item in enumerate(STAGES)}

FINAL_ACTIONS = [
    {
        "id": "ordinary",
        "label": "普通建仓",
        "shortLabel": "普通建仓",
        "definition": "四层模型给出普通建仓，且现行身份、证据、风险和交易门槛完整通过。",
    },
    {
        "id": "extreme",
        "label": "极限试仓",
        "shortLabel": "极限试仓",
        "definition": "早期凸性与可交易性达到极限试仓标准，只允许现货、小额和预设可归零损失。",
    },
    {
        "id": "observe",
        "label": "只观察",
        "shortLabel": "只观察",
        "definition": "保留研究，但当前至少一项行动条件未通过，不形成建仓或试仓动作。",
    },
    {
        "id": "reflexive",
        "label": "反身性管理",
        "shortLabel": "反身性",
        "definition": "事实和价格已经进入趋势共振阶段，不再按早期凸性新增仓位。",
    },
    {
        "id": "invalidated",
        "label": "失效/排除",
        "shortLabel": "失效/排除",
        "definition": "核心事实、身份、安全边界或剩余凸性已经失效，退出当前机会范围。",
    },
]
FINAL_ACTION_BY_ID = {item["id"]: item for item in FINAL_ACTIONS}
FINAL_ACTION_ORDER = {
    item["id"]: index for index, item in enumerate(FINAL_ACTIONS)
}
CONCLUSION_GROUPS = [
    {
        "id": "execution",
        "label": "当前可执行",
        "shortLabel": "可执行",
        "actionIds": ["ordinary", "extreme"],
        "definition": "只收录已经通过当前证据、风险和交易门槛的普通建仓或极限试仓项目。",
    },
    {
        "id": "tracking",
        "label": "继续跟踪",
        "shortLabel": "跟踪",
        "actionIds": ["observe"],
        "definition": "仍有研究价值，但至少一项行动条件未通过；首要阻断原因必须明确展示。",
    },
    {
        "id": "transferred",
        "label": "转出管理",
        "shortLabel": "转出",
        "actionIds": ["reflexive", "invalidated"],
        "definition": "已经转入反身性管理或失效排除，不再混入尚未兑现的凸性机会。",
    },
]


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_js_payload(path, prefix):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"无法识别快照格式：{path}")
    return json.loads(text[len(prefix):-1])


def first_reason(values, fallback):
    return next((str(value).strip() for value in values or [] if str(value).strip()), fallback)


def classify_case(case, model_result=None):
    machine = case.get("machineConclusion")
    if machine:
        stage_id = machine["opportunity_stage"]
        final_action_id = machine["action_category"]
        stage = STAGE_BY_ID[stage_id]
        final_action = FINAL_ACTION_BY_ID[final_action_id]
        reason = (
            machine.get("why_not_actionable")
            or machine.get("headline")
            or "机器结论已发布。"
        )
        blocker_status = (
            machine["conclusion_state"]
            if final_action_id == "observe"
            else ""
        )
        return {
            "stage": stage_id,
            "stageLabel": stage["label"],
            "stageOrder": STAGE_ORDER[stage_id],
            "stageReason": reason,
            "modelActionCategory": "machine_rule",
            "modelActionLabel": machine["action_label"],
            "stoppedLayer": None,
            "stoppedLayerLabel": (
                machine["conclusion_state_label"]
                if blocker_status
                else ""
            ),
            "positionBoundary": "系统不自动交易",
            "finalActionCategory": final_action_id,
            "finalActionLabel": final_action["label"],
            "finalActionOrder": FINAL_ACTION_ORDER[final_action_id],
            "finalActionReason": machine["headline"],
            "blockerStatus": blocker_status,
            "blockerLabel": (
                machine["conclusion_state_label"]
                if blocker_status
                else ""
            ),
            "sourceActionLabel": case.get("normalizedAction")
            or case.get("sourceAction")
            or "",
            "nextStep": machine.get("next_step") or "",
            "nextTaskId": machine.get("next_task_id") or "",
            "upgradeConditions": machine.get("upgradeConditions") or [],
            "invalidationConditions": (
                machine.get("invalidationConditions") or []
            ),
            "conclusionSource": "machine_conclusion",
        }

    signal = case.get("publicSignal") or {}
    screening = case.get("screening") or {}
    category = (model_result or {}).get("actionCategory")

    if not model_result:
        stage_id = "model_pending"
        reason = "尚未取得最新四层模型结果，保留原始资料但不生成行动结论。"
    elif category == "reject":
        stage_id = "invalidated"
        reason = model_result.get("actionReason") or "四层模型已经排除该项目。"
    elif category == "reflexive":
        stage_id = "reflexive"
        reason = model_result.get("actionReason") or "项目已转入反身性管理。"
    elif not signal.get("active"):
        stage_id = "invalidated"
        reason = first_reason(signal.get("exitReasons"), "项目已转出当前凸性阶段。")
    elif signal.get("actionable") and category in {"ordinary", "extreme"}:
        stage_id = "actionable"
        reason = model_result.get("actionReason") or "四层模型与现行硬门槛同时通过。"
    elif category in {"ordinary", "extreme"}:
        stage_id = "action_pending"
        reason = first_reason(
            screening.get("failedReasons") or screening.get("pendingReasons"),
            "四层模型已给出行动方向，但现行硬门槛尚未完整通过。",
        )
    elif signal.get("qualified"):
        stage_id = "qualified_pending"
        reason = first_reason(
            screening.get("pendingReasons") or screening.get("failedReasons"),
            model_result.get("actionReason") or "项目已入选，但当前仍需补证。",
        )
    elif case.get("state") == "odds_decay" or signal.get("tier") == "decay":
        stage_id = "decay"
        reason = case.get("oddsDecayConditions") or "赔率或事件窗口正在衰减。"
    else:
        stage_id = "observe"
        reason = model_result.get("actionReason") or "四层模型当前结论为继续观察。"

    stage = STAGE_BY_ID[stage_id]
    if stage_id == "invalidated":
        final_action_id = "invalidated"
    elif stage_id == "reflexive":
        final_action_id = "reflexive"
    elif stage_id == "actionable" and category in {"ordinary", "extreme"}:
        final_action_id = category
    else:
        final_action_id = "observe"
    final_action = FINAL_ACTION_BY_ID[final_action_id]
    final_reason = reason
    if final_action_id == "observe" and category in {"ordinary", "extreme"}:
        final_reason = f"当前只观察。模型曾给出{(model_result or {}).get('actionLabel') or '行动方向'}，但{reason}"
    elif final_action_id == "observe" and stage_id in {
        "qualified_pending",
        "decay",
        "model_pending",
    }:
        final_reason = f"当前只观察。{reason}"
    blocker_status = "" if final_action_id in {
        "ordinary",
        "extreme",
        "reflexive",
        "invalidated",
    } else stage_id
    blocker_label = STAGE_BY_ID[blocker_status]["label"] if blocker_status else ""
    fallback_invalidation = case.get("invalidationConditions") or []
    if not fallback_invalidation and case.get("invalidation"):
        fallback_invalidation = [
            item.strip()
            for item in str(case["invalidation"]).split("；")
            if item.strip()
        ]
    return {
        "stage": stage_id,
        "stageLabel": stage["label"],
        "stageOrder": STAGE_ORDER[stage_id],
        "stageReason": reason,
        "modelActionCategory": category or "pending",
        "modelActionLabel": (model_result or {}).get("actionLabel") or "模型待运行",
        "stoppedLayer": (model_result or {}).get("stoppedLayer"),
        "stoppedLayerLabel": (model_result or {}).get("stoppedLayerLabel") or "",
        "positionBoundary": (model_result or {}).get("positionBoundary") or "",
        "finalActionCategory": final_action_id,
        "finalActionLabel": final_action["label"],
        "finalActionOrder": FINAL_ACTION_ORDER[final_action_id],
        "finalActionReason": final_reason,
        "blockerStatus": blocker_status,
        "blockerLabel": blocker_label,
        "sourceActionLabel": case.get("normalizedAction") or case.get("sourceAction") or "",
        "nextStep": "",
        "nextTaskId": "",
        "upgradeConditions": [],
        "invalidationConditions": fallback_invalidation,
        "conclusionSource": "four_layer_fallback",
    }


def build_conclusion_board(cases, action_counts):
    ranked_cases = sorted(
        cases,
        key=lambda item: (
            -item["publicSignal"]["score"],
            item["projectName"].lower(),
        ),
    )
    groups = []
    for group in CONCLUSION_GROUPS:
        matching = [
            item
            for item in ranked_cases
            if item["opportunityStage"]["finalActionCategory"] in group["actionIds"]
        ]
        groups.append({
            **group,
            "count": len(matching),
            "caseIds": [item["caseId"] for item in matching],
        })

    blocker_groups = {}
    for item in ranked_cases:
        stage = item["opportunityStage"]
        if stage["finalActionCategory"] != "observe":
            continue
        label = (
            stage.get("stoppedLayerLabel")
            or stage.get("blockerLabel")
            or "行动条件未完整"
        )
        blocker = blocker_groups.setdefault(label, {
            "id": f"blocker-{len(blocker_groups) + 1}",
            "label": label,
            "count": 0,
            "caseIds": [],
        })
        blocker["count"] += 1
        blocker["caseIds"].append(item["caseId"])

    blockers = sorted(
        blocker_groups.values(),
        key=lambda item: (-item["count"], item["label"]),
    )
    actionable = action_counts["ordinary"] + action_counts["extreme"]
    return {
        "headline": (
            f"本期有{actionable}个满足完整行动门槛的项目"
            if actionable
            else "本期没有满足完整行动门槛的项目"
        ),
        "note": (
            f"继续跟踪{action_counts['observe']}个，"
            f"反身性管理{action_counts['reflexive']}个，"
            f"失效/排除{action_counts['invalidated']}个。"
        ),
        "groups": groups,
        "blockers": blockers,
    }


def build_snapshot(
    candidate_path=DEFAULT_CANDIDATE_PATH,
    four_layer_path=DEFAULT_FOUR_LAYER_PATH,
):
    candidate = load_js_payload(candidate_path, CANDIDATE_PREFIX)
    four_layer = load_js_payload(four_layer_path, FOUR_LAYER_PREFIX)
    model_by_case = {
        item["id"]: item
        for item in four_layer.get("live", {}).get("cases", [])
    }

    cases = []
    for source_case in candidate.get("cases", []):
        item = dict(source_case)
        item["opportunityStage"] = classify_case(
            item,
            model_by_case.get(item["caseId"]),
        )
        cases.append(item)
    cases.sort(
        key=lambda item: (
            item["opportunityStage"]["stageOrder"],
            -item["publicSignal"]["score"],
            item["projectName"].lower(),
        )
    )
    stage_counts = Counter(
        item["opportunityStage"]["stage"]
        for item in cases
    )
    action_counts = Counter(
        item["opportunityStage"]["finalActionCategory"]
        for item in cases
    )
    action_conflicts = [
        {
            "caseId": item["caseId"],
            "projectName": item["projectName"],
            "stage": item["opportunityStage"]["stage"],
            "finalAction": item["opportunityStage"]["finalActionCategory"],
        }
        for item in cases
        if (
            item["opportunityStage"]["stage"] == "actionable"
            and item["opportunityStage"]["finalActionCategory"]
            not in {"ordinary", "extreme"}
        )
        or (
            item["opportunityStage"]["stage"] != "actionable"
            and item["opportunityStage"]["finalActionCategory"]
            in {"ordinary", "extreme"}
        )
    ]
    counts = {
        "total": len(cases),
        "actionable": action_counts["ordinary"] + action_counts["extreme"],
        "ordinary": action_counts["ordinary"],
        "extreme": action_counts["extreme"],
        "actionPending": stage_counts["action_pending"],
        "qualifiedPending": stage_counts["qualified_pending"],
        "observe": action_counts["observe"],
        "decay": stage_counts["decay"],
        "modelPending": stage_counts["model_pending"],
        "reflexive": action_counts["reflexive"],
        "invalidated": action_counts["invalidated"],
    }
    conclusion_board = build_conclusion_board(cases, action_counts)
    c18_home = build_c18_home({
        "counts": counts,
        "cases": cases,
        "conclusionBoard": conclusion_board,
        "latestRefresh": candidate.get("latestRefresh"),
        "generatedAt": candidate.get("generatedAt"),
    })

    return {
        "version": "C1.5-05",
        "release": "C1.5",
        "generatedAt": utc_now(),
        "title": "凸性机会中心统一动作",
        "boundary": (
            "当前动作只允许普通建仓、极限试仓、只观察、反身性管理、失效/排除五类。"
            "待补证、待门槛、身份待核验和模型待运行只作为阻断状态，不是投资动作。"
        ),
        "source": {
            "candidateVersion": candidate.get("version"),
            "candidateGeneratedAt": candidate.get("generatedAt"),
            "fourLayerVersion": four_layer.get("version"),
            "fourLayerGeneratedAt": four_layer.get("generatedAt"),
        },
        "counts": counts,
        "stageCounts": {
            stage["id"]: stage_counts[stage["id"]]
            for stage in STAGES
        },
        "stages": STAGES,
        "finalActions": FINAL_ACTIONS,
        "actionCounts": {
            action["id"]: action_counts[action["id"]]
            for action in FINAL_ACTIONS
        },
        "conclusionBoard": conclusion_board,
        "c18": c18_home,
        "consistency": {
            "checked": len(cases),
            "passed": len(cases) - len(action_conflicts),
            "conflicts": action_conflicts,
        },
        "priorityPolicy": candidate.get("priorityPolicy") or {},
        "publicRanking": {
            **(candidate.get("publicRanking") or {}),
            "version": "C1.5-05",
            "stageOrder": [stage["id"] for stage in STAGES],
            "finalActionOrder": [action["id"] for action in FINAL_ACTIONS],
        },
        "gateScreening": candidate.get("gateScreening") or {},
        "latestRefresh": candidate.get("latestRefresh"),
        "emptyReason": candidate.get("importBoundary") or (
            "当前没有机器生成的项目，等待下一次自动扫描。"
        ),
        "cases": cases,
    }


def write_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = "
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)


def rebuild_opportunity_center_snapshot(
    candidate_path=DEFAULT_CANDIDATE_PATH,
    four_layer_path=DEFAULT_FOUR_LAYER_PATH,
    output_path=DEFAULT_OUTPUT_PATH,
):
    snapshot = build_snapshot(candidate_path, four_layer_path)
    write_snapshot(snapshot, output_path)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="生成C1.2-06凸性机会中心结论快照")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--four-layer", type=Path, default=DEFAULT_FOUR_LAYER_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_opportunity_center_snapshot(
        candidate_path=args.candidates,
        four_layer_path=args.four_layer,
        output_path=args.output,
    )
    print(json.dumps(snapshot["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
