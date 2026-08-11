#!/usr/bin/env python3
import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH
from source_discovery_attribution import build_source_discovery_snapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FOUR_LAYER_PATH = PROJECT_ROOT / "app" / "four-layer-screening-snapshot.js"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "discovery-funnel-snapshot.js"
FOUR_LAYER_PREFIX = "window.PENGUIN_CONVEXITY_FOUR_LAYER = "


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_js_payload(path, prefix):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"无法识别快照格式：{path}")
    return json.loads(text[len(prefix):-1])


def project_case_map(connection):
    rows = connection.execute(
        """
        SELECT
          case_row.case_id,
          case_row.project_id,
          case_row.asset_id,
          case_row.action_stage,
          case_row.workflow_state,
          case_row.updated_at,
          project.canonical_name AS project_name,
          asset.symbol,
          asset.identity_status AS asset_identity_status,
          asset.capture_grade
        FROM candidate_cases case_row
        LEFT JOIN projects project ON project.project_id = case_row.project_id
        LEFT JOIN assets asset ON asset.asset_id = case_row.asset_id
        WHERE case_row.project_id IS NOT NULL
        ORDER BY case_row.project_id, case_row.updated_at DESC, case_row.case_id
        """
    ).fetchall()
    cases = {}
    for row in rows:
        item = dict(row)
        cases.setdefault(item["project_id"], item)
    return cases


def blocker_for(item, case=None, four_layer=None):
    project_status = item["projectIdentityStatus"]
    asset_status = item["assetIdentityStatus"]
    capture_status = item["valueCaptureStatus"]

    if project_status != "verified":
        if project_status == "corroborated":
            return {
                "reachedStage": 0,
                "blocker": "identity_corroborated_only",
                "blockerLabel": "跨源印证，项目身份仍待核验",
                "blockerReason": "至少两个来源出现同一主体，但尚无官网域名或官方仓库等一手身份锚点。",
                "nextAction": "补官网、官方文档或官方仓库，确认同名记录属于同一项目主体。",
            }
        if project_status == "conflict":
            return {
                "reachedStage": 0,
                "blocker": "identity_conflict",
                "blockerLabel": "项目身份冲突",
                "blockerReason": "名称、域名或仓库指向不一致，不能安全归并。",
                "nextAction": "人工核对冲突来源，保留独立主体或修正归因。",
            }
        if project_status == "rejected":
            return {
                "reachedStage": 0,
                "blocker": "identity_rejected",
                "blockerLabel": "项目身份已排除",
                "blockerReason": "现有证据不支持把该记录作为有效项目主体。",
                "nextAction": "仅在出现新的官方身份材料时重新核验。",
            }
        return {
            "reachedStage": 0,
            "blocker": "identity_single_source",
            "blockerLabel": "单源发现，项目身份待核验",
            "blockerReason": "目前只有单一来源线索，不能据此确认项目主体。",
            "nextAction": "寻找第二来源和官方身份锚点，不自动进入候选研究。",
        }

    if asset_status != "verified":
        if asset_status == "conflict":
            return {
                "reachedStage": 1,
                "blocker": "asset_conflict",
                "blockerLabel": "资产身份冲突",
                "blockerReason": "代币符号、合约或所属项目存在冲突。",
                "nextAction": "核验官方合约、所在链和可卖出路径。",
            }
        if asset_status == "pending":
            return {
                "reachedStage": 1,
                "blocker": "asset_pending",
                "blockerLabel": "资产记录待核验",
                "blockerReason": "已经发现可能关联的资产，但官方关系或合约尚未完整确认。",
                "nextAction": "补官方代币说明、合约地址、所在链和交易路径。",
            }
        return {
            "reachedStage": 1,
            "blocker": "asset_not_identified",
            "blockerLabel": "尚未识别可交易资产",
            "blockerReason": "发现了项目主体，不代表存在可购买且与项目收益相关的资产。",
            "nextAction": "查找官方代币、股票或其他可交易权益；没有资产则保持项目观察。",
        }

    if capture_status != "verified":
        if capture_status == "claimed":
            return {
                "reachedStage": 2,
                "blocker": "value_capture_claimed",
                "blockerLabel": "价值捕获仅有项目方声称",
                "blockerReason": "尚无机制、收入分配或治理文件独立证明资产能够捕获项目价值。",
                "nextAction": "补代币经济、费用分配、回购或权益文件，并交叉验证。",
            }
        if capture_status == "not_applicable":
            return {
                "reachedStage": 2,
                "blocker": "value_capture_not_applicable",
                "blockerLabel": "资产不承接项目价值",
                "blockerReason": "现有资料表明该资产不适合作为项目价值捕获标的。",
                "nextAction": "停止按投资资产推进，必要时只保留项目级跟踪。",
            }
        return {
            "reachedStage": 2,
            "blocker": "value_capture_unknown",
            "blockerLabel": "价值捕获未知",
            "blockerReason": "资产身份已确认，但尚不能证明项目增长如何传导到资产价值。",
            "nextAction": "核验费用、现金流、治理权、供给与解锁等价值传导机制。",
        }

    if not case:
        return {
            "reachedStage": 3,
            "blocker": "no_research_case",
            "blockerLabel": "尚未建立研究案例",
            "blockerReason": "身份与价值捕获已经通过，但尚未进入凸性研究框架。",
            "nextAction": "建立研究案例，填写风险、剩余凸性、点火和失效条件。",
        }

    if not four_layer:
        return {
            "reachedStage": 4,
            "blocker": "no_four_layer_result",
            "blockerLabel": "尚未完成四层筛选",
            "blockerReason": "已有研究案例，但缺少最新四层机器判断。",
            "nextAction": "重新生成四层筛选快照并检查缺失字段。",
        }

    action = four_layer.get("actionCategory") or "observe"
    labels = {
        "ordinary": ("action_ready", "普通建仓条件成立"),
        "extreme": ("action_ready", "极限试仓条件成立"),
        "observe": ("four_layer_observe", "四层结果：只观察"),
        "reflexive": ("four_layer_reflexive", "四层结果：反身性管理"),
        "reject": ("four_layer_reject", "四层结果：排除"),
    }
    blocker, label = labels.get(action, ("no_four_layer_result", "四层结果待解释"))
    return {
        "reachedStage": 5,
        "blocker": blocker,
        "blockerLabel": label,
        "blockerReason": four_layer.get("actionReason") or "四层模型已完成分类。",
        "nextAction": (
            four_layer.get("positionBoundary")
            or ("进入行动级人工复核。" if blocker == "action_ready" else "按当前分层继续观察或转出。")
        ),
    }


def build_stage_summary(items):
    project_verified = [item for item in items if item["projectIdentityStatus"] == "verified"]
    asset_verified = [item for item in project_verified if item["assetIdentityStatus"] == "verified"]
    capture_verified = [item for item in asset_verified if item["valueCaptureStatus"] == "verified"]
    research_cases = [item for item in capture_verified if item["case"]]
    evaluated = [item for item in research_cases if item["fourLayer"]]
    action_ready = [item for item in evaluated if item["blocker"] == "action_ready"]

    return [
        {
            "id": 0,
            "label": "项目召回",
            "question": "各来源一共发现了多少项目主体线索？",
            "entered": len(items),
            "passed": len(items),
            "waiting": 0,
            "blocked": 0,
        },
        {
            "id": 1,
            "label": "项目身份",
            "question": "能否用一手身份锚点确认项目主体？",
            "entered": len(items),
            "passed": len(project_verified),
            "waiting": sum(
                item["projectIdentityStatus"] in {"pending", "corroborated"}
                for item in items
            ),
            "blocked": sum(
                item["projectIdentityStatus"] in {"conflict", "rejected"}
                for item in items
            ),
        },
        {
            "id": 2,
            "label": "资产身份",
            "question": "存在可购买资产，且确认它属于该项目吗？",
            "entered": len(project_verified),
            "passed": len(asset_verified),
            "waiting": sum(
                item["assetIdentityStatus"] in {"not_identified", "pending"}
                for item in project_verified
            ),
            "blocked": sum(
                item["assetIdentityStatus"] == "conflict"
                for item in project_verified
            ),
        },
        {
            "id": 3,
            "label": "价值捕获",
            "question": "项目增长能否传导到可购买资产？",
            "entered": len(asset_verified),
            "passed": len(capture_verified),
            "waiting": sum(
                item["valueCaptureStatus"] in {"unknown", "claimed"}
                for item in asset_verified
            ),
            "blocked": sum(
                item["valueCaptureStatus"] == "not_applicable"
                for item in asset_verified
            ),
        },
        {
            "id": 4,
            "label": "研究建案",
            "question": "是否已经形成可持续更新的凸性研究案例？",
            "entered": len(capture_verified),
            "passed": len(research_cases),
            "waiting": len(capture_verified) - len(research_cases),
            "blocked": 0,
        },
        {
            "id": 5,
            "label": "行动分层",
            "question": "四层模型给出建仓、试仓、观察、转出还是排除？",
            "entered": len(research_cases),
            "passed": len(action_ready),
            "waiting": len(research_cases) - len(evaluated),
            "blocked": len(evaluated) - len(action_ready),
        },
    ]


def build_snapshot(connection, four_layer_path=DEFAULT_FOUR_LAYER_PATH):
    source_snapshot = build_source_discovery_snapshot(connection)
    cases_by_project = project_case_map(connection)
    four_layer_payload = (
        load_js_payload(four_layer_path, FOUR_LAYER_PREFIX)
        if Path(four_layer_path).is_file()
        else {"live": {"summary": {"total": 0}, "cases": []}}
    )
    four_layer_by_case = {
        item["id"]: item
        for item in four_layer_payload.get("live", {}).get("cases", [])
    }

    items = []
    for source_item in source_snapshot["items"]:
        case = cases_by_project.get(source_item["matchedProjectId"])
        four_layer = four_layer_by_case.get(case["case_id"]) if case else None
        result = blocker_for(source_item, case=case, four_layer=four_layer)
        items.append(
            {
                **source_item,
                **result,
                "case": (
                    {
                        "caseId": case["case_id"],
                        "projectName": case["project_name"],
                        "symbol": case["symbol"],
                        "actionStage": case["action_stage"],
                        "workflowState": case["workflow_state"],
                    }
                    if case
                    else None
                ),
                "fourLayer": (
                    {
                        "actionCategory": four_layer.get("actionCategory"),
                        "actionLabel": four_layer.get("actionLabel"),
                        "stoppedLayer": four_layer.get("stoppedLayer"),
                        "stoppedLayerLabel": four_layer.get("stoppedLayerLabel"),
                    }
                    if four_layer
                    else None
                ),
            }
        )

    items.sort(
        key=lambda item: (
            -item["reachedStage"],
            -len(item["sourceIds"]),
            item["canonicalName"].lower(),
        )
    )
    stages = build_stage_summary(items)
    blocker_counts = Counter(item["blocker"] for item in items)
    blocker_definitions = {}
    for item in items:
        blocker_definitions.setdefault(
            item["blocker"],
            {
                "label": item["blockerLabel"],
                "reason": item["blockerReason"],
                "nextAction": item["nextAction"],
            },
        )
    blockers = sorted(
        (
            {
                "id": blocker_id,
                **blocker_definitions[blocker_id],
                "count": count,
            }
            for blocker_id, count in blocker_counts.items()
        ),
        key=lambda item: (-item["count"], item["label"]),
    )
    action_ready = sum(item["blocker"] == "action_ready" for item in items)
    for item in items:
        item.pop("blockerLabel")
        item.pop("blockerReason")
        item.pop("nextAction")

    return {
        "version": "C1.1-06",
        "release": "C1.1",
        "generatedAt": utc_now(),
        "title": "发现漏斗",
        "policy": "项目召回只代表发现线索。项目身份、资产身份、价值捕获、研究建案和行动分层必须逐级通过，缺口保持为缺口。",
        "source": {
            "sourceDiscoveryVersion": source_snapshot.get("version"),
            "sourceDiscoveryGeneratedAt": source_snapshot.get("generatedAt"),
            "fourLayerVersion": four_layer_payload.get("version"),
            "fourLayerGeneratedAt": four_layer_payload.get("generatedAt"),
        },
        "counts": {
            "total": len(items),
            "projectVerified": stages[1]["passed"],
            "assetVerified": stages[2]["passed"],
            "valueCaptureVerified": stages[3]["passed"],
            "researchCases": stages[4]["passed"],
            "actionReady": action_ready,
        },
        "stages": stages,
        "blockers": blockers,
        "sources": source_snapshot["sources"],
        "separateCandidateBranch": {
            "count": four_layer_payload.get("live", {}).get("summary", {}).get("total", 0),
            "label": "既有候选研究池",
            "note": "这批案例来自既有投研、任务对话框或链上发现，不等于本次项目级来源发现已经通过漏斗，因此不计入漏斗转化率。",
        },
        "items": items,
    }


def write_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_CONVEXITY_DISCOVERY_FUNNEL = "
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)


def rebuild_discovery_funnel_snapshot(
    db_path=DEFAULT_DB_PATH,
    four_layer_path=DEFAULT_FOUR_LAYER_PATH,
    output_path=DEFAULT_OUTPUT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_snapshot(connection, four_layer_path=four_layer_path)
        write_snapshot(snapshot, output_path)
        return snapshot
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成C1.1-06发现漏斗快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--four-layer", type=Path, default=DEFAULT_FOUR_LAYER_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_discovery_funnel_snapshot(
        db_path=args.db,
        four_layer_path=args.four_layer,
        output_path=args.output,
    )
    print(json.dumps(snapshot["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
