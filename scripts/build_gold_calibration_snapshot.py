#!/usr/bin/env python3
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = ROOT / "fixtures" / "gold-calibration-c1.1.json"
OUTPUT_PATH = ROOT / "app" / "gold-calibration-snapshot.js"
VALID_COHORTS = {"core_positive", "extreme_boundary", "observe_only", "rejected"}
REQUIRED_FIELDS = {
    "id",
    "priority",
    "project",
    "asset",
    "cohort",
    "action",
    "maturity",
    "risk",
    "remainingConvexity",
    "primaryConvexity",
    "sourceTurnId",
    "sourceDate",
    "facts",
    "whyIncluded",
    "whyNotHigher",
    "ignition",
    "invalidation",
    "modelLesson",
    "sourceLinks",
}


def build_snapshot():
    payload = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not cases:
        raise ValueError("黄金校准集不能为空")
    ids = [item.get("id") for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("黄金校准集存在重复案例ID")
    priorities = [item.get("priority") for item in cases]
    if len(priorities) != len(set(priorities)):
        raise ValueError("黄金校准集存在重复优先级")
    for item in cases:
        missing = sorted(REQUIRED_FIELDS - set(item))
        if missing:
            raise ValueError(f"{item.get('id', 'unknown')} 缺少字段：{', '.join(missing)}")
        if item["cohort"] not in VALID_COHORTS:
            raise ValueError(f"{item['id']} 使用了无效分组：{item['cohort']}")
        if len(item["facts"]) < 2:
            raise ValueError(f"{item['id']} 至少需要两条校准事实")
    ordered = sorted(cases, key=lambda item: item["priority"])
    cohort_counts = Counter(item["cohort"] for item in ordered)
    action_counts = Counter(item["action"] for item in ordered)
    return {
        **payload,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "caseCount": len(ordered),
            "cohortCounts": dict(cohort_counts),
            "actionCounts": dict(action_counts),
            "scoredCount": sum(item.get("score") is not None for item in ordered),
            "sourceTurnCount": len({item["sourceTurnId"] for item in ordered}),
        },
        "cases": ordered,
    }


def main():
    snapshot = build_snapshot()
    OUTPUT_PATH.write_text(
        "window.PENGUIN_CONVEXITY_GOLD_CALIBRATION = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    print(
        f"gold calibration snapshot built: {snapshot['summary']['caseCount']} cases, "
        f"{snapshot['summary']['sourceTurnCount']} source turns"
    )


if __name__ == "__main__":
    main()
