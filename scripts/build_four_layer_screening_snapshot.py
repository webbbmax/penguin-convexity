#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from four_layer_screening import evaluate_case, sort_results, summarize


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATE_PATH = PROJECT_ROOT / "app" / "candidate-pool-snapshot.js"
DEFAULT_GOLD_INPUT_PATH = PROJECT_ROOT / "fixtures" / "four-layer-gold-inputs-c1.1.json"
DEFAULT_GOLD_EXPECTED_PATH = PROJECT_ROOT / "fixtures" / "gold-calibration-c1.1.json"
DEFAULT_HIGH_VALUE_PATH = PROJECT_ROOT / "app" / "high-value-source-snapshot.js"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "four-layer-screening-snapshot.js"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_js_payload(path, prefix):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"无法识别快照格式：{path}")
    return json.loads(text[len(prefix):-1])


def _risk(value):
    return {
        "low": "R2",
        "medium": "R3",
        "high": "R4",
        "blocked": "blocked",
    }.get(value, "unknown")


def _remaining(value):
    return {
        "high": "high",
        "medium": "medium",
        "low": "low",
        "none": "none",
    }.get(value, "unknown")


def _tradeability(value):
    return {
        "verified": "standard",
        "limited": "extreme",
        "blocked": "untradeable",
    }.get(value, "unknown")


def _loss_bound(text):
    text = str(text or "")
    if not text:
        return "unknown"
    zero_terms = ("归零", "不超过目标仓位", "单批可归零", "5%")
    return "bounded_zero" if any(term in text for term in zero_terms) else "bounded"


def normalize_live_case(case, high_value=None):
    high_value = high_value or {}
    evidence = case.get("evidence") or []
    confirmed = sum(item.get("factBoundary") == "confirmed_fact" for item in evidence)
    project_claims = sum(item.get("factBoundary") == "project_claim" for item in evidence)
    hard_trace = bool(case.get("hardTracePresent") or high_value.get("hardTrace"))
    if hard_trace and confirmed:
        evidence_grade = "verified"
    elif hard_trace:
        evidence_grade = "conditional"
    elif project_claims:
        evidence_grade = "weak"
    else:
        evidence_grade = "none"
    high_value_grade = high_value.get("evidenceGrade")
    grade_rank = {"none": 0, "weak": 1, "conditional": 2, "verified": 3}
    if grade_rank.get(high_value_grade, 0) > grade_rank.get(evidence_grade, 0):
        evidence_grade = high_value_grade

    capture = case.get("valueCaptureGrade")
    economic_increment = (
        high_value.get("economicIncrement", "unknown")
        if high_value
        else "verified" if hard_trace else "unknown"
    )
    market_refresh = (case.get("refresh") or {}).get("market") or {}
    price_change = market_refresh.get("priceChange24hPct")
    price_reaction = "low"
    if case.get("maturity") == "L5" or case.get("remainingConvexity") in {"low", "none"}:
        price_reaction = "full"
    elif isinstance(price_change, (int, float)) and price_change >= 20:
        price_reaction = "partial"

    return {
        "id": case["caseId"],
        "project": case.get("projectName") or "未命名项目",
        "asset": case.get("symbol") or "无直接代币",
        "detailUrl": case.get("detailUrl") or "",
        "maturity": case.get("maturity") or "L0",
        "risk": _risk(case.get("riskLevel")),
        "remainingConvexity": _remaining(case.get("remainingConvexity")),
        "mismatchScore": case.get("mismatchScore"),
        "projectIdentity": case.get("projectIdentityStatus") or "pending",
        "assetIdentity": case.get("assetIdentityStatus") or "pending",
        "officialRelation": (
            "verified"
            if case.get("projectIdentityStatus") == "verified"
            and case.get("assetIdentityStatus") == "verified"
            else "pending"
        ),
        "tradeability": _tradeability(case.get("tradeabilityStatus")),
        "sellPath": case.get("sellPathStatus") or "unknown",
        "contractRisk": case.get("contractRisk") or "unknown",
        "securityBlocked": case.get("riskLevel") == "blocked",
        "securityUnresolved": case.get("contractRisk") == "unknown",
        "coreInvalidated": (
            case.get("state") == "invalidated"
            or case.get("normalizedAction") == "已失去凸性"
        ),
        "hardTrace": hard_trace,
        "evidenceGrade": evidence_grade,
        "economicIncrement": economic_increment,
        "primaryConvexity": case.get("convexitySource") or "",
        "valueCapture": capture if capture in {"A", "B", "C"} else "unknown",
        "lossBound": _loss_bound(case.get("maximumControllableLoss")),
        "nonlinearUpside": bool(str(case.get("nonlinearUpsidePath") or "").strip()),
        "ignitionDefined": bool(str(case.get("ignitionConditions") or "").strip()),
        "invalidationDefined": bool(str(case.get("invalidation") or "").strip()),
        "priceReaction": price_reaction,
        "sourceAction": case.get("normalizedAction") or "",
        "sourceSnapshotAt": case.get("sourceSnapshotAt") or "",
        "highValueSourceIds": high_value.get("sourceIds") or [],
        "highValueRecordCount": high_value.get("recordCount") or 0,
    }


def expected_category(action):
    if action in {"普通建仓", "回撤后普通小仓"}:
        return "ordinary"
    if action == "极限试仓":
        return "extreme"
    if action in {"只观察", "保留原评级，不进今日重点"}:
        return "observe"
    if action == "排除":
        return "reject"
    if action.startswith("转入反身性管理"):
        return "reflexive"
    raise ValueError(f"未识别黄金集动作：{action}")


def build_snapshot(
    candidate_path,
    gold_input_path,
    gold_expected_path,
    high_value_path=DEFAULT_HIGH_VALUE_PATH,
):
    candidate_payload = load_js_payload(
        candidate_path,
        "window.PENGUIN_CONVEXITY_CANDIDATES = ",
    )
    high_value_payload = (
        load_js_payload(
            high_value_path,
            "window.PENGUIN_CONVEXITY_HIGH_VALUE_SOURCES = ",
        )
        if Path(high_value_path).is_file()
        else {"cases": []}
    )
    high_value_by_case = {
        item["caseId"]: item
        for item in high_value_payload.get("cases", [])
    }
    live_results = sort_results(
        [
            evaluate_case(
                normalize_live_case(case, high_value_by_case.get(case["caseId"]))
            )
            for case in candidate_payload["cases"]
        ]
    )

    gold_inputs = load_json(gold_input_path)
    gold_expected = load_json(gold_expected_path)
    expected_by_id = {
        item["id"]: {
            "expectedCategory": expected_category(item["action"]),
            "expectedAction": item["action"],
            "cohort": item["cohort"],
        }
        for item in gold_expected["cases"]
    }
    input_ids = {item["id"] for item in gold_inputs["cases"]}
    if input_ids != set(expected_by_id):
        raise ValueError("黄金集机器输入与预期案例不一致")

    calibration_results = []
    for machine_input in gold_inputs["cases"]:
        result = evaluate_case(machine_input)
        expected = expected_by_id[machine_input["id"]]
        result["expectedCategory"] = expected["expectedCategory"]
        result["expectedAction"] = expected["expectedAction"]
        result["cohort"] = expected["cohort"]
        result["calibrationMatched"] = (
            result["actionCategory"] == expected["expectedCategory"]
        )
        calibration_results.append(result)

    matched = sum(item["calibrationMatched"] for item in calibration_results)
    return {
        "version": "C1.1-03",
        "release": "C1.1",
        "generatedAt": utc_now(),
        "title": "四层自动筛选模型",
        "boundary": "模型只生成研究分层和人工复核优先级，不自动交易。未知数据保留为待核验，不用推测值补齐。",
        "source": {
            "liveCandidateVersion": candidate_payload.get("version"),
            "liveCandidateGeneratedAt": candidate_payload.get("generatedAt"),
            "goldInputVersion": gold_inputs["version"],
            "goldExpectedVersion": gold_expected["version"],
            "highValueSourceVersion": high_value_payload.get("version"),
            "highValueSourceGeneratedAt": high_value_payload.get("generatedAt"),
        },
        "layerDefinitions": [
            {"id": 1, "label": "身份与交易性", "question": "买的是什么，能否安全退出？"},
            {"id": 2, "label": "硬事实", "question": "事实是否真实，并形成经济增量？"},
            {"id": 3, "label": "凸性结构", "question": "亏损可控且上行非线性是否闭环？"},
            {"id": 4, "label": "行动分层", "question": "应该普通建仓、极限试仓、观察还是排除？"},
        ],
        "live": {
            "summary": summarize(live_results),
            "cases": live_results,
        },
        "calibration": {
            "summary": {
                **summarize(calibration_results),
                "matched": matched,
                "mismatched": len(calibration_results) - matched,
                "accuracyPct": round(matched / len(calibration_results) * 100, 1),
            },
            "cases": sort_results(calibration_results),
        },
    }


def write_snapshot(snapshot, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_CONVEXITY_FOUR_LAYER = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)


def main():
    parser = argparse.ArgumentParser(description="生成C1.1-03四层自动筛选快照")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--gold-inputs", type=Path, default=DEFAULT_GOLD_INPUT_PATH)
    parser.add_argument("--gold-expected", type=Path, default=DEFAULT_GOLD_EXPECTED_PATH)
    parser.add_argument("--high-value", type=Path, default=DEFAULT_HIGH_VALUE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot = build_snapshot(
        args.candidates,
        args.gold_inputs,
        args.gold_expected,
        args.high_value,
    )
    write_snapshot(snapshot, args.output)
    print(
        json.dumps(
            {
                "liveCases": snapshot["live"]["summary"]["total"],
                "goldCases": snapshot["calibration"]["summary"]["total"],
                "goldMatched": snapshot["calibration"]["summary"]["matched"],
                "accuracyPct": snapshot["calibration"]["summary"]["accuracyPct"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
