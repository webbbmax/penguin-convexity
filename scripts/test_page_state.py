#!/usr/bin/env python3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def assert_before(text, first, second, message):
    assert first in text, f"{message}：缺少 {first}"
    assert second in text, f"{message}：缺少 {second}"
    assert text.index(first) < text.index(second), message


def main():
    helper = (APP_ROOT / "page-state.js").read_text(encoding="utf-8")
    assert "penguin.convexity.page-state.c1p08:" in helper
    assert "localStorage.getItem" in helper
    assert "localStorage.setItem" in helper
    assert "try {" in helper and "catch (_error)" in helper
    assert "restoreScroll" in helper
    assert "page-state-notice" in helper

    pages = {
        "manual-review": ("manual-review.js", "20260730-c1p2p1"),
        "candidate-pool": ("candidate-pool.js", "20260802-c1p8"),
        "project-master-pool": ("project-master-pool.js", "20260730-c1p5p05a"),
    }
    for page_name, (script_name, version) in pages.items():
        html = (APP_ROOT / f"{page_name}.html").read_text(encoding="utf-8")
        assert_before(
            html,
            "page-state.js?v=20260729-c1p08",
            f"{script_name}?v={version}",
            f"{page_name} 必须先加载状态记忆器",
        )

    manual = (APP_ROOT / "manual-review.js").read_text(encoding="utf-8")
    manual_html = (APP_ROOT / "manual-review.html").read_text(encoding="utf-8")
    assert 'pageState?.load("manual-review")' in manual
    assert "reviewListScrollTop" in manual
    assert "drafts: pageDrafts" in manual
    assert "lastOperation" in manual
    assert "当前填写内容已保留，可以修正后重试" in manual
    assert "已恢复未保存内容" in manual
    assert "manualReviewLastOperation" in manual_html

    candidate = (APP_ROOT / "candidate-pool.js").read_text(encoding="utf-8")
    assert 'pageState?.load("candidate-pool")' in candidate
    assert "stateControlIds" in candidate
    assert "opportunityStageFilter" in candidate
    assert "opportunitySearch" in candidate
    assert "restorePageControls();" in candidate
    assert "pagehide" in candidate

    master = (APP_ROOT / "project-master-pool.js").read_text(encoding="utf-8")
    assert 'pageState?.load("project-master-pool")' in master
    assert "activeId: restoredPageState.activeId" not in master
    assert "restoredPageState.activeId" in master
    assert "listScrollTop" in master
    assert "masterMarketCapFilter" in master
    assert "masterFdvFilter" in master
    assert "masterLiquidityFilter" in master
    assert "masterQualityFilter" in master
    date_function = master[
        master.index("function inDateWindow"):
        master.index("function numericSortValue")
    ]
    assert_before(
        date_function,
        "const parsed",
        'dateFilter === "180d"',
        "近半年筛选必须先解析项目日期",
    )

    styles = (APP_ROOT / "styles.css").read_text(encoding="utf-8")
    for class_name in (
        ".page-state-notice",
        ".manual-draft-notice",
        ".manual-review-last-operation",
    ):
        assert class_name in styles

    print("C1.2-08 页面状态记忆、草稿恢复与操作反馈测试通过。")


if __name__ == "__main__":
    main()
