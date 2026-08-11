#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from rule_engine import evaluate_snapshot, load_rulebook, load_state_machine, transition_is_legal


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES_PATH = PROJECT_ROOT / "fixtures" / "replay-scenarios-v1.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "rule-replay-snapshot.js"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_replay_snapshot(fixtures_path=DEFAULT_FIXTURES_PATH):
    fixtures = json.loads(Path(fixtures_path).read_text(encoding="utf-8"))
    rulebook = load_rulebook()
    state_machine = load_state_machine()
    results = []
    state_coverage = Counter()
    gate_failures = Counter()
    case_types = Counter()

    for case in fixtures["cases"]:
        current_input = dict(fixtures["defaults"])
        previous_state = None
        timeline = []
        actual_sequence = []
        all_transitions_legal = True

        for point in case["timeline"]:
            current_input.update(point.get("input", {}))
            evaluation = evaluate_snapshot(current_input, previous_state, rulebook)
            legal = transition_is_legal(previous_state, evaluation["state"], state_machine)
            all_transitions_legal = all_transitions_legal and legal
            state_coverage[evaluation["state"]] += 1
            for gate in evaluation["gates"]:
                if gate["status"] != "pass":
                    gate_failures[gate["key"]] += 1
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
        passed = actual_sequence == expected_sequence and all_transitions_legal
        case_types[case["caseType"]] += 1
        results.append(
            {
                "caseId": case["caseId"],
                "title": case["title"],
                "caseType": case["caseType"],
                "description": case["description"],
                "expectedSequence": expected_sequence,
                "actualSequence": actual_sequence,
                "passed": passed,
                "allTransitionsLegal": all_transitions_legal,
                "final": timeline[-1],
                "timeline": timeline,
            }
        )

    passed_count = sum(1 for result in results if result["passed"])
    return {
        "generatedAt": utc_now(),
        "notice": fixtures["notice"],
        "fixtureVersion": fixtures["version"],
        "rulebook": rulebook,
        "stateMachine": state_machine,
        "summary": {
            "caseCount": len(results),
            "passedCount": passed_count,
            "failedCount": len(results) - passed_count,
            "coveredStateCount": len(state_coverage),
            "totalStateCount": len(state_machine["states"]),
            "allTransitionsLegal": all(result["allTransitionsLegal"] for result in results),
        },
        "stateCoverage": dict(state_coverage),
        "gateFailureCounts": dict(gate_failures),
        "caseTypeCounts": dict(case_types),
        "results": results,
    }


def write_replay_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        "window.PENGUIN_CONVEXITY_REPLAY = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="运行企鹅投研凸性规则回放")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    snapshot = build_replay_snapshot(args.fixtures)
    write_replay_snapshot(snapshot, args.output)
    print(
        json.dumps(
            {
                "status": "success" if snapshot["summary"]["failedCount"] == 0 else "failed",
                "cases": snapshot["summary"]["caseCount"],
                "passed": snapshot["summary"]["passedCount"],
                "failed": snapshot["summary"]["failedCount"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if snapshot["summary"]["failedCount"] == 0 else 1)


if __name__ == "__main__":
    main()
