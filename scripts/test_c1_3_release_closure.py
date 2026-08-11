#!/usr/bin/env python3
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
SHELL_ROOT = PROJECT_ROOT / "desktop"


def load_snapshot(filename, prefix):
    text = (APP_ROOT / filename).read_text(encoding="utf-8").strip()
    assert text.startswith(prefix) and text.endswith(";")
    return json.loads(text[len(prefix):-1])


def test_closure_snapshot_counts():
    tracking = load_snapshot(
        "tracking-task-snapshot.js",
        "window.PENGUIN_CONVEXITY_TRACKING_TASKS = ",
    )
    changes = load_snapshot(
        "change-explanations-snapshot.js",
        "window.PENGUIN_CONVEXITY_CHANGE_EXPLANATIONS = ",
    )
    assert tracking["version"] == "C1.5-05"
    assert changes["version"] == "C1.3-08"
    assert tracking["counts"]["decisionFollowUpDue"] == sum(
        item["decisionFollowUp"]["status"] in {"pending", "failed"}
        and item["status"] == "due"
        for item in tracking["tasks"]
    )
    assert tracking["counts"]["decisionFollowUpCompleted"] == sum(
        item["decisionFollowUp"]["status"] == "completed"
        for item in tracking["tasks"]
    )
    assert changes["counts"]["decisionReviewPending"] == len(
        changes["reviewQueue"]
    )


def test_workbench_closure_contract():
    html = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "workbench.js").read_text(encoding="utf-8")
    styles = (APP_ROOT / "styles.css").read_text(encoding="utf-8")
    stages = [
        "workbenchClosureUpdate",
        "workbenchClosureTracking",
        "workbenchClosureReview",
        "workbenchClosureVerification",
        "workbenchClosureConclusion",
    ]
    assert "REAL RESULT LOOP" in html
    assert "真实结果闭环" in html
    assert html.index("workbench-closure-section") < html.index(
        "workbench-daily-section"
    )
    for stage in stages:
        assert f'id="{stage}"' in html
        assert f'"{stage}"' in script
    assert "保持观察也是有效结果" in script
    assert "这不是更新失败，到期后系统自动执行" in script
    assert "setClosureStage" in script
    assert ".workbench-closure-flow" in styles
    for state in ("complete", "active", "attention", "issue"):
        assert f'data-state="{state}"' in styles


def test_user_entries_and_release_version():
    update_html = (APP_ROOT / "update-center.html").read_text(encoding="utf-8")
    shell_html = (SHELL_ROOT / "index.html").read_text(encoding="utf-8")
    server = (PROJECT_ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")
    assert 'id="trackingResults"' in update_html
    assert 'id="verificationQueueSection"' in update_html
    assert "企鹅投研-凸性" in shell_html
    assert "C1.7" in shell_html
    assert 'CONVEXITY_RELEASE = "C1.7"' in server


def main():
    test_closure_snapshot_counts()
    test_workbench_closure_contract()
    test_user_entries_and_release_version()
    print("C1.3-08 真实结果闭环、用户入口与封板版本测试通过。")


if __name__ == "__main__":
    main()
