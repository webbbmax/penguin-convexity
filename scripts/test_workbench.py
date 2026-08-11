#!/usr/bin/env python3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
SHELL_ROOT = PROJECT_ROOT / "desktop"


def main():
    html = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "workbench.js").read_text(encoding="utf-8")
    nav_script = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    shell_html = (SHELL_ROOT / "index.html").read_text(encoding="utf-8")
    shell_script = (SHELL_ROOT / "shell.js").read_text(encoding="utf-8")

    assert "CONVEXITY WORKSPACE · C1.7" in html
    assert "本项目独立运行" in html
    assert "返回凸性机会中心" in html
    assert "今天从这里开始" in html
    assert "更新数据" in html
    assert "查看机器发现" in html
    assert "检查自动档案" in html
    assert "查看结论" in html
    assert "系统与模型" in html
    assert "workbenchRecommendationTitle" in html
    for page in (
        "new-token-update.html",
        "update-center.html",
        "scan-center.html",
        "manual-review.html",
        "screening-console.html",
        "source-registry.html",
        "gold-calibration.html",
        "four-layer-screening.html",
        "high-value-sources.html",
        "source-discovery.html",
        "discovery-funnel.html",
        "change-explanations.html",
        "model-acceptance.html",
        "monitoring-infrastructure.html",
        "weak-signal-inbox.html",
        "data-backbone.html",
    ):
        backend_html = (APP_ROOT / page).read_text(encoding="utf-8")
        assert page in html
        assert "workbench-nav.js" in backend_html, f"{page} 未接入统一后台导航"
        assert "C1.0" not in backend_html, f"{page} 仍显示旧凸性后台版本"
    for page in (
        "project-master-pool.html",
        "network-discovery.html",
        "data-dictionary.html",
        "rules-replay.html",
        "real-case-calibration.html",
    ):
        assert page in html
        backend_html = (APP_ROOT / page).read_text(encoding="utf-8")
        assert 'class="topbar workbench-topbar"' in backend_html
        assert "workbench-nav.js" in backend_html
    assert "candidate-pool.html" in html
    for snapshot in (
        "runtime-snapshot.js",
        "scan-center-snapshot.js",
        "project-master-pool-snapshot.js",
        "source-discovery-snapshot.js",
        "network-discovery-snapshot.js",
        "candidate-pool-snapshot.js",
    ):
        assert snapshot in html
    assert "sourceDiscovery.counts.machineProjects" in script
    assert "sourceDiscovery.counts.machineAssetNotIdentified" in script
    assert "manual-review-snapshot.js" in html
    assert "tracking-task-snapshot.js" in html
    assert "PENGUIN_CONVEXITY_TRACKING_TASKS" in script
    assert "materialTrackingChanges" in script
    assert "dueTrackingCount" in script
    assert "查看最近变化" in script
    assert "自动任务待处理" in html
    assert "PENGUIN_CONVEXITY_UPDATE_CENTER" in script
    assert "displayStatus" in script
    assert "actionKind" in script
    assert "zero_result_explanation" in script
    assert "recommendation" in script
    assert "workbenchStepUpdate" in script
    for page, label in (
        ("workbench.html", "工作台"),
        ("source-discovery.html", "机器发现"),
        ("weak-signal-inbox.html", "弱线索"),
        ("manual-review.html", "可选人工复核"),
        ("new-token-update.html", "90天新币筛选"),
        ("update-center.html", "凸性跟踪更新"),
        ("source-registry.html", "信源状态"),
        ("evidence-ledger.html", "原始证据"),
    ):
        assert page in nav_script
        assert label in nav_script
    assert "系统与模型" in nav_script
    assert "凸性工作台 C1.7" in nav_script
    assert r"C1(?:[.-]\d+)+" in nav_script
    assert 'data-page="candidate-pool.html"' in shell_html
    assert 'data-detail-entry' in shell_html
    assert 'data-page="workbench.html"' in shell_html
    assert "RWA" not in shell_html
    assert '"workbench.html": ["凸性工作台", "研究与机器任务"]' in shell_script
    assert "lastDetailRoute" in shell_script
    assert "function routeFromFrame" in shell_script
    print("convexity workbench checks passed")


if __name__ == "__main__":
    main()
