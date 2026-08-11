#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from rule_engine import evaluate_snapshot, load_rulebook, load_state_machine, transition_is_legal


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_PATH = PROJECT_ROOT / "fixtures" / "real-historical-cases-v1.json"
DEFAULT_MARKET_PATH = PROJECT_ROOT / "fixtures" / "real-historical-market-v1.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "real-case-calibration-snapshot.js"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_market(path):
    path = Path(path)
    if not path.exists():
        return {
            "generatedAt": None,
            "source": {"name": "未运行历史行情抓取"},
            "results": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def calibration_note(case, passed, transitions_legal, actual_sequence):
    if passed:
        return "当前规则与人工事前判断一致。该结果只表示规则边界通过，不表示历史上应当实际买入。"
    if not transitions_legal:
        return "发现状态机不允许的真实路径，需要先修正状态迁移规则。"
    return (
        "当前引擎与人工事前判断不一致：预期 "
        + " → ".join(case["expectedSequence"])
        + "，实际 "
        + " → ".join(actual_sequence)
        + "。阈值不得为了让案例通过而直接改写。"
    )


def build_calibration_snapshot(
    cases_path=DEFAULT_CASES_PATH,
    market_path=DEFAULT_MARKET_PATH,
):
    fixtures = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    market = load_market(market_path)
    rulebook = load_rulebook()
    state_machine = load_state_machine()
    results = []
    case_types = Counter()
    evidence_boundaries = Counter()
    state_mismatches = Counter()

    for case in fixtures["cases"]:
        current_input = dict(fixtures["defaults"])
        previous_state = None
        timeline = []
        actual_sequence = []
        transitions_legal = True

        for point in case["timeline"]:
            current_input.update(point.get("input", {}))
            evaluation = evaluate_snapshot(current_input, previous_state, rulebook)
            legal = transition_is_legal(previous_state, evaluation["state"], state_machine)
            transitions_legal = transitions_legal and legal
            timeline.append(
                {
                    "at": point["at"],
                    "label": point["label"],
                    "fromState": previous_state,
                    "transitionLegal": legal,
                    **evaluation,
                }
            )
            actual_sequence.append(evaluation["state"])
            previous_state = evaluation["state"]

        expected_sequence = case["expectedSequence"]
        passed = actual_sequence == expected_sequence and transitions_legal
        if not passed:
            state_mismatches[
                f"{' → '.join(expected_sequence)} | {' → '.join(actual_sequence)}"
            ] += 1
        case_types[case["caseType"]] += 1
        for source in case["sources"]:
            evidence_boundaries[source["factBoundary"]] += 1

        results.append(
            {
                **{key: value for key, value in case.items() if key != "timeline"},
                "actualSequence": actual_sequence,
                "passed": passed,
                "allTransitionsLegal": transitions_legal,
                "calibrationNote": calibration_note(
                    case, passed, transitions_legal, actual_sequence
                ),
                "final": timeline[-1],
                "timeline": timeline,
                "marketReaction": market["results"].get(
                    case["caseId"],
                    {
                        "status": "unavailable",
                        "reason": "market_fetch_not_run",
                        "detail": "尚未运行历史行情抓取；不使用估算值替代。",
                    },
                ),
            }
        )

    passed_count = sum(1 for item in results if item["passed"])
    market_available = sum(
        1 for item in results if item["marketReaction"]["status"] == "available"
    )
    return {
        "generatedAt": utc_now(),
        "notice": fixtures["notice"],
        "fixtureVersion": fixtures["version"],
        "rulebook": rulebook,
        "stateMachine": state_machine,
        "marketSource": market.get("source", {}),
        "marketGeneratedAt": market.get("generatedAt"),
        "summary": {
            "caseCount": len(results),
            "passedCount": passed_count,
            "failedCount": len(results) - passed_count,
            "transitionIssueCount": sum(
                1 for item in results if not item["allTransitionsLegal"]
            ),
            "marketAvailableCount": market_available,
            "marketUnavailableCount": len(results) - market_available,
            "primaryEvidenceCaseCount": sum(
                1
                for item in results
                if any(
                    source["sourceType"]
                    in (
                        "官方协议公告",
                        "官方治理提案",
                        "项目官方公告",
                        "官方工程复盘",
                        "官方技术文档",
                        "项目官方上线公告",
                        "官方协议文档",
                        "项目官方事故公告",
                        "基金会公告",
                        "官方回顾",
                        "基金会年度报告",
                        "项目官方文档",
                        "官方法律与活动条款",
                        "监管诉讼公告",
                        "法院判决结果公告",
                        "监管案件档案",
                        "司法文件",
                        "刑事指控公告",
                    )
                    for source in item["sources"]
                )
            ),
        },
        "caseTypeCounts": dict(case_types),
        "evidenceBoundaryCounts": dict(evidence_boundaries),
        "mismatchCounts": dict(state_mismatches),
        "results": results,
    }


def write_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        "window.PENGUIN_CONVEXITY_REAL_CASES = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="运行凸性规则真实历史案例校准")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--market", type=Path, default=DEFAULT_MARKET_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot = build_calibration_snapshot(args.cases, args.market)
    write_snapshot(snapshot, args.output)
    print(
        json.dumps(
            {
                "status": "success" if snapshot["summary"]["failedCount"] == 0 else "review_required",
                "cases": snapshot["summary"]["caseCount"],
                "passed": snapshot["summary"]["passedCount"],
                "failed": snapshot["summary"]["failedCount"],
                "transitionIssues": snapshot["summary"]["transitionIssueCount"],
                "marketAvailable": snapshot["summary"]["marketAvailableCount"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if snapshot["summary"]["failedCount"] == 0 else 2)


if __name__ == "__main__":
    main()
