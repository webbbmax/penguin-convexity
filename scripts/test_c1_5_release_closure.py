#!/usr/bin/env python3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
SHELL_ROOT = PROJECT_ROOT / "desktop"
RELEASE = "C1.7"
STYLE_CACHE_KEY = "20260731-c1p6p06a"
C18_STYLE_CACHE_KEY = "20260802-c1p8-sol1"
C18_STYLE_PAGES = {
    "candidate-pool.html",
    "action-gaps.html",
    "change-explanations.html",
    "catalyst-paths.html",
    "decision-quality.html",
    "update-center.html",
}
SHELL_CACHE_KEY = "m1p0"


def read(path):
    return path.read_text(encoding="utf-8")


def test_release_history_is_complete():
    readme = read(PROJECT_ROOT / "README.md")
    assert f"凸性业务版本为 `{RELEASE}`" in readme
    assert "当前迁移版本为 `M1.0`" in readme
    for step in range(6):
        assert f"## C1.5-0{step} " in readme
    assert "## C1.6-01 " in readme
    assert "## C1.6-03 " in readme
    assert "## C1.6-05 " in readme
    assert "## C1.6-06 " in readme


def test_active_pages_use_current_release():
    old_markers = ("C1.5-04", "20260730-c1p5p04b")
    for page in APP_ROOT.glob("*.html"):
        text = read(page)
        style_cache = (
            "20260801-c1p7a"
            if page.name == "data-backbone.html"
            else C18_STYLE_CACHE_KEY
            if page.name in C18_STYLE_PAGES
            else STYLE_CACHE_KEY
        )
        assert f"styles.css?v={style_cache}" in text, page.name
        assert not any(marker in text for marker in old_markers), page.name

    shell = read(SHELL_ROOT / "index.html")
    assert "企鹅投研-凸性" in shell
    assert RELEASE in shell
    assert f"shell.js?v={SHELL_CACHE_KEY}" in shell


def test_watchdog_is_connected_to_no_code_pages():
    update_html = read(APP_ROOT / "update-center.html")
    update_js = read(APP_ROOT / "update-center.js")
    workbench_js = read(APP_ROOT / "workbench.js")
    local_server = read(PROJECT_ROOT / "scripts" / "serve_local.py")
    launcher = read(PROJECT_ROOT / "scripts" / "launch-convexity.ps1")

    assert 'id="updateWatchdog"' in update_html
    assert "自动守护与失败恢复" in update_html
    assert 'id="watchdogRecoveryAction"' in update_html
    assert "renderWatchdog" in update_js
    assert "data-update-task" in update_js
    assert "applyLiveUpdateGuard" in workbench_js
    assert "/api/convexity/update-status" in workbench_js
    assert "initialize_update_recovery" in local_server
    assert 'CONVEXITY_RELEASE = "C1.7"' in local_server
    assert f'$health.convexityRelease -eq "{RELEASE}"' in launcher
    assert '$health.migrationRelease -eq "M1.0"' in launcher


def test_failure_path_creates_retryable_history():
    runner = read(PROJECT_ROOT / "scripts" / "run_update_task.py")
    watchdog = read(PROJECT_ROOT / "scripts" / "update_watchdog.py")
    test_script = read(PROJECT_ROOT / "scripts" / "test_update_watchdog.py")

    assert "record_failed_run" in runner
    assert "recover_interrupted_updates" in watchdog
    assert "retryable, retry_status" in watchdog
    assert "VALUES (?, ?, NULL, ?, ?, ?, 1, 'not_requested'" in watchdog
    assert "test_timeout_before_normal_write_is_recorded" in test_script
    assert "test_interrupted_status_becomes_retryable_run" in test_script


def main():
    test_release_history_is_complete()
    test_active_pages_use_current_release()
    test_watchdog_is_connected_to_no_code_pages()
    test_failure_path_creates_retryable_history()
    print("C1.5/C1.6 历史完整且 C1.7 当前生产外壳测试通过。")


if __name__ == "__main__":
    main()
