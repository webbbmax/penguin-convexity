#!/usr/bin/env python3
from pathlib import Path

from build_opportunity_center_snapshot import (
    DEFAULT_CANDIDATE_PATH,
    DEFAULT_FOUR_LAYER_PATH,
    FINAL_ACTIONS,
    build_snapshot,
)


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
EXPECTED_LABELS = {
    "普通建仓",
    "极限试仓",
    "只观察",
    "反身性管理",
    "失效/排除",
}
CONSUMER_PAGES = (
    ("project-detail.html", "project-detail.js"),
    ("manual-review.html", "manual-review.js"),
    ("project-master-pool.html", "project-master-pool.js"),
    ("screening-console.html", "screening-console.js"),
)


def main():
    snapshot = build_snapshot(DEFAULT_CANDIDATE_PATH, DEFAULT_FOUR_LAYER_PATH)
    assert {item["label"] for item in FINAL_ACTIONS} == EXPECTED_LABELS
    assert snapshot["consistency"]["conflicts"] == []
    assert snapshot["consistency"]["passed"] == snapshot["counts"]["total"]

    allowed_categories = {item["id"] for item in FINAL_ACTIONS}
    for item in snapshot["cases"]:
        decision = item["opportunityStage"]
        assert decision["finalActionCategory"] in allowed_categories
        assert decision["finalActionLabel"] in EXPECTED_LABELS
        if decision["finalActionCategory"] in {"ordinary", "extreme"}:
            assert item["publicSignal"]["actionable"]

    for html_name, script_name in CONSUMER_PAGES:
        html = (APP_ROOT / html_name).read_text(encoding="utf-8")
        script = (APP_ROOT / script_name).read_text(encoding="utf-8")
        assert "opportunity-center-snapshot.js" in html
        assert "finalActionLabel" in script
        assert "旧数据库动作仅保留历史" in script or "旧任务判断仅保留历史" in script

    detail_script = (APP_ROOT / "project-detail.js").read_text(encoding="utf-8")
    master_script = (APP_ROOT / "project-master-pool.js").read_text(encoding="utf-8")
    screening_script = (APP_ROOT / "screening-console.js").read_text(encoding="utf-8")
    assert "currentCase?.action_stage" not in detail_script
    assert "${escapeHtml(item.actionStage)}" not in master_script
    assert "任务原始判断（历史）" in screening_script
    assert "候选归一结果（历史）" in screening_script
    print("C1.2-02 cross-page action language checks passed")


if __name__ == "__main__":
    main()
