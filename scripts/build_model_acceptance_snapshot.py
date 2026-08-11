#!/usr/bin/env python3
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from four_layer_screening import ACTION_LABELS, RULE_VERSION, evaluate_case


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FOUR_LAYER_PATH = PROJECT_ROOT / "app" / "four-layer-screening-snapshot.js"
DEFAULT_BLIND_INPUT_PATH = PROJECT_ROOT / "fixtures" / "model-acceptance-blind-inputs-c1.1.json"
DEFAULT_BLIND_EXPECTED_PATH = PROJECT_ROOT / "fixtures" / "model-acceptance-blind-expected-c1.1.json"
DEFAULT_REMEDIATION_PATH = PROJECT_ROOT / "fixtures" / "model-acceptance-remediation-c1.1.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "model-acceptance-snapshot.js"
FOUR_LAYER_PREFIX = "window.PENGUIN_CONVEXITY_FOUR_LAYER = "
OUTPUT_PREFIX = "window.PENGUIN_CONVEXITY_MODEL_ACCEPTANCE = "
ACTION_CATEGORIES = ["ordinary", "extreme", "observe", "reflexive", "reject"]
ACTIONABLE = {"ordinary", "extreme"}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_js_payload(path, prefix):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"无法识别快照格式：{path}")
    return json.loads(text[len(prefix):-1])


def percentage(numerator, denominator):
    return round(numerator / denominator * 100, 1) if denominator else 100.0


def build_blind_cases(inputs, expected_payload):
    expected_by_id = {item["id"]: item for item in expected_payload["cases"]}
    input_ids = {item["id"] for item in inputs["cases"]}
    if input_ids != set(expected_by_id):
        raise ValueError("盲测机器输入与预期案例不一致")

    results = []
    for source in inputs["cases"]:
        machine_input = {
            **inputs["defaults"],
            **source.get("input", {}),
            "id": source["id"],
            "project": source["project"],
            "asset": source["asset"],
        }
        result = evaluate_case(machine_input)
        expected = expected_by_id[source["id"]]
        result.update(
            {
                "cohort": expected["cohort"],
                "expectedCategory": expected["expectedCategory"],
                "expectedAction": ACTION_LABELS[expected["expectedCategory"]],
                "expectedStoppedLayer": expected["expectedStoppedLayer"],
                "rationale": expected["rationale"],
                "actionMatched": result["actionCategory"] == expected["expectedCategory"],
                "layerMatched": result["stoppedLayer"] == expected["expectedStoppedLayer"],
            }
        )
        result["safetyEscape"] = (
            result["actionCategory"] in ACTIONABLE
            and expected["expectedCategory"] not in ACTIONABLE
        )
        result["falseNegative"] = (
            result["actionCategory"] not in ACTIONABLE
            and expected["expectedCategory"] in ACTIONABLE
        )
        results.append(result)
    return results


def summarize_blind(results):
    expected_positive = sum(item["expectedCategory"] in ACTIONABLE for item in results)
    predicted_positive = sum(item["actionCategory"] in ACTIONABLE for item in results)
    true_positive = sum(
        item["expectedCategory"] in ACTIONABLE and item["actionCategory"] in ACTIONABLE
        for item in results
    )
    false_positives = [item for item in results if item["safetyEscape"]]
    false_negatives = [item for item in results if item["falseNegative"]]
    exact_matches = sum(item["actionMatched"] for item in results)
    layer_matches = sum(item["layerMatched"] for item in results)

    confusion = {
        expected: {actual: 0 for actual in ACTION_CATEGORIES}
        for expected in ACTION_CATEGORIES
    }
    cohort_rows = defaultdict(list)
    for item in results:
        confusion[item["expectedCategory"]][item["actionCategory"]] += 1
        cohort_rows[item["cohort"]].append(item)
    cohorts = {
        cohort: {
            "total": len(items),
            "matched": sum(item["actionMatched"] for item in items),
            "accuracyPct": percentage(
                sum(item["actionMatched"] for item in items),
                len(items),
            ),
        }
        for cohort, items in cohort_rows.items()
    }
    return {
        "total": len(results),
        "exactMatched": exact_matches,
        "exactMismatched": len(results) - exact_matches,
        "exactAccuracyPct": percentage(exact_matches, len(results)),
        "layerMatched": layer_matches,
        "layerAccuracyPct": percentage(layer_matches, len(results)),
        "expectedActionable": expected_positive,
        "predictedActionable": predicted_positive,
        "truePositive": true_positive,
        "falsePositive": len(false_positives),
        "falseNegative": len(false_negatives),
        "actionableRecallPct": percentage(true_positive, expected_positive),
        "actionablePrecisionPct": percentage(true_positive, predicted_positive),
        "safetyEscapes": len(false_positives),
        "actionCounts": dict(Counter(item["actionCategory"] for item in results)),
        "expectedActionCounts": dict(Counter(item["expectedCategory"] for item in results)),
        "confusion": confusion,
        "cohorts": cohorts,
        "failedCaseIds": [
            item["id"]
            for item in results
            if not item["actionMatched"] or not item["layerMatched"]
        ],
    }


def build_criteria(gold, blind):
    criteria = [
        {
            "id": "gold_regression",
            "label": "黄金集回归",
            "actual": gold["accuracyPct"],
            "target": 100.0,
            "unit": "%",
            "passed": gold["accuracyPct"] == 100.0,
            "boundary": "已知规则不能被新改动破坏。",
        },
        {
            "id": "blind_coverage",
            "label": "独立盲测覆盖",
            "actual": blind["total"],
            "target": 16,
            "unit": "个",
            "passed": blind["total"] >= 16,
            "boundary": "盲测情景与黄金集项目完全分离。",
        },
        {
            "id": "action_consistency",
            "label": "动作一致性",
            "actual": blind["exactAccuracyPct"],
            "target": 90.0,
            "unit": "%",
            "passed": blind["exactAccuracyPct"] >= 90.0,
            "boundary": "普通、极限、观察、转出和排除必须精确一致。",
        },
        {
            "id": "actionable_recall",
            "label": "行动机会召回",
            "actual": blind["actionableRecallPct"],
            "target": 80.0,
            "unit": "%",
            "passed": blind["actionableRecallPct"] >= 80.0,
            "boundary": "不能漏掉已经满足普通或极限行动条件的项目。",
        },
        {
            "id": "actionable_precision",
            "label": "行动机会精确率",
            "actual": blind["actionablePrecisionPct"],
            "target": 90.0,
            "unit": "%",
            "passed": blind["actionablePrecisionPct"] >= 90.0,
            "boundary": "不能把观察、排除或反身性项目冒充为行动机会。",
        },
        {
            "id": "safety_escape",
            "label": "安全型误报",
            "actual": blind["safetyEscapes"],
            "target": 0,
            "unit": "个",
            "passed": blind["safetyEscapes"] == 0,
            "boundary": "该项一票否决，不允许用平均分抵消。",
        },
        {
            "id": "layer_consistency",
            "label": "阻断层一致性",
            "actual": blind["layerAccuracyPct"],
            "target": 85.0,
            "unit": "%",
            "passed": blind["layerAccuracyPct"] >= 85.0,
            "boundary": "不仅动作要对，还要解释在哪一层停下。",
        },
    ]
    return criteria


def build_snapshot(
    four_layer_path=DEFAULT_FOUR_LAYER_PATH,
    blind_input_path=DEFAULT_BLIND_INPUT_PATH,
    blind_expected_path=DEFAULT_BLIND_EXPECTED_PATH,
    remediation_path=DEFAULT_REMEDIATION_PATH,
):
    four_layer = load_js_payload(four_layer_path, FOUR_LAYER_PREFIX)
    inputs = load_json(blind_input_path)
    expected = load_json(blind_expected_path)
    remediation = load_json(remediation_path)
    blind_cases = build_blind_cases(inputs, expected)
    blind_summary = summarize_blind(blind_cases)
    gold_summary = four_layer["calibration"]["summary"]
    criteria = build_criteria(gold_summary, blind_summary)
    failed_criteria = [item for item in criteria if not item["passed"]]
    safety_failed = any(
        item["id"] in {"gold_regression", "safety_escape"} and not item["passed"]
        for item in criteria
    )
    if safety_failed:
        verdict = "blocked"
        verdict_label = "模型验收未通过"
    elif failed_criteria:
        verdict = "conditional"
        verdict_label = "模型有条件通过"
    else:
        verdict = "passed"
        verdict_label = "规则模型验收通过"

    return {
        "version": "C1.1-09",
        "release": "C1.1",
        "ruleVersion": RULE_VERSION,
        "generatedAt": utc_now(),
        "title": "凸性四层模型验收",
        "verdict": verdict,
        "verdictLabel": verdict_label,
        "verdictExplanation": (
            "黄金集回归、独立情景盲测、行动召回、精确率和安全误报均达到冻结标准。"
            if verdict == "passed"
            else "至少一项验收标准未通过，不能把当前模型视为已验收。"
        ),
        "boundary": (
            "本页验证四层规则是否按产品定义稳定运行，不验证未来收益。"
            "真实投资有效性需要积累带时间截点、价格路径和失效结果的独立样本后另行评估。"
        ),
        "source": {
            "fourLayerVersion": four_layer.get("version"),
            "fourLayerGeneratedAt": four_layer.get("generatedAt"),
            "goldInputVersion": four_layer.get("source", {}).get("goldInputVersion"),
            "goldExpectedVersion": four_layer.get("source", {}).get("goldExpectedVersion"),
            "blindInputVersion": inputs["version"],
            "blindExpectedVersion": expected["version"],
        },
        "gold": {
            "total": gold_summary["total"],
            "matched": gold_summary["matched"],
            "mismatched": gold_summary["mismatched"],
            "accuracyPct": gold_summary["accuracyPct"],
        },
        "blind": {
            "summary": blind_summary,
            "cases": blind_cases,
        },
        "criteria": criteria,
        "failedCriteria": failed_criteria,
        "remediation": remediation,
        "investmentValidation": {
            "status": "insufficient_outcome_data",
            "label": "真实投资有效性尚未验证",
            "availableOutcomeCases": 0,
            "requiredOutcomeCases": 30,
            "reason": (
                "目前没有足够的独立、带时间截点的真实结果样本，"
                "因此不能计算真实胜率、收益分布、最大回撤或校准后命中率。"
            ),
            "nextEvidence": "后续保存每次结论的价格路径、事实兑现、失效原因和退出结果，累计至少30个独立案例。",
        },
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


def rebuild_model_acceptance_snapshot(
    four_layer_path=DEFAULT_FOUR_LAYER_PATH,
    blind_input_path=DEFAULT_BLIND_INPUT_PATH,
    blind_expected_path=DEFAULT_BLIND_EXPECTED_PATH,
    remediation_path=DEFAULT_REMEDIATION_PATH,
    output_path=DEFAULT_OUTPUT_PATH,
):
    snapshot = build_snapshot(
        four_layer_path,
        blind_input_path,
        blind_expected_path,
        remediation_path,
    )
    write_snapshot(snapshot, output_path)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="生成C1.1-09凸性模型验收快照")
    parser.add_argument("--four-layer", type=Path, default=DEFAULT_FOUR_LAYER_PATH)
    parser.add_argument("--blind-inputs", type=Path, default=DEFAULT_BLIND_INPUT_PATH)
    parser.add_argument("--blind-expected", type=Path, default=DEFAULT_BLIND_EXPECTED_PATH)
    parser.add_argument("--remediation", type=Path, default=DEFAULT_REMEDIATION_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_model_acceptance_snapshot(
        args.four_layer,
        args.blind_inputs,
        args.blind_expected,
        args.remediation,
        args.output,
    )
    print(
        json.dumps(
            {
                "verdict": snapshot["verdict"],
                "gold": snapshot["gold"],
                "blind": snapshot["blind"]["summary"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
