#!/usr/bin/env python3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
BACKEND_PAGES = {
    path.name
    for path in APP_ROOT.glob("*.html")
    if path.name not in {"candidate-pool.html", "project-detail.html", "action-gaps.html"}
}
CORE_PAGES = (
    "workbench.html",
    "project-master-pool.html",
    "source-discovery.html",
    "weak-signal-inbox.html",
    "update-center.html",
    "source-registry.html",
    "evidence-ledger.html",
    "catalyst-paths.html",
)


def main():
    assert len(BACKEND_PAGES) == 25
    action_gaps = (APP_ROOT / "action-gaps.html").read_text(encoding="utf-8")
    assert 'class="topbar opportunity-topbar"' in action_gaps
    assert "行动条件与缺口" in action_gaps
    for page_name in sorted(BACKEND_PAGES):
        html = (APP_ROOT / page_name).read_text(encoding="utf-8")
        assert 'class="topbar workbench-topbar"' in html
        assert 'class="topbar-actions"' in html
        assert 'class="product-nav"' in html
        nav_version = "c20" if page_name == "decision-quality.html" else "20260801-c1p7a" if page_name == "data-backbone.html" else "20260731-c1p6p06a"
        assert f"workbench-nav.js?v={nav_version}" in html

    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    positions = [navigation.index(f'["{page_name}"') for page_name in CORE_PAGES]
    assert positions == sorted(positions), "核心栏目顺序不稳定"
    for marker in (
        "pageStatus.classList.add",
        "main.insertBefore(pageStatus",
        "if (!returnLink)",
        'topbar.classList.add("is-navigation-ready")',
    ):
        assert marker in navigation

    styles = (APP_ROOT / "styles.css").read_text(encoding="utf-8")
    for marker in (
        ".workbench-topbar .brand",
        "flex: 0 0 220px",
        ".workbench-topbar .product-nav",
        "grid-template-columns: 76px 88px 88px 80px 88px 88px 88px 88px",
        ".workbench-topbar .topbar-actions",
        "flex: 0 0 974px",
        ".workbench-topbar:not(.is-navigation-ready) .topbar-actions",
        ".workbench-page-status",
    ):
        assert marker in styles, f"顶部导航稳定样式缺少：{marker}"

    active_rule = styles[
        styles.index(".product-nav a.active"):
        styles.index(".topbar-status span")
    ]
    for forbidden in ("font-size", "font-weight", "padding", "width", "height"):
        assert forbidden not in active_rule, f"选中栏目会改变尺寸：{forbidden}"
    print("C1.3-00 顶部导航固定尺寸、固定顺序与状态下移测试通过。")


if __name__ == "__main__":
    main()
