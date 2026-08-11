#!/usr/bin/env python3
import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
SHELL_ROOT = PROJECT_ROOT / "desktop"
DEFAULT_BASE_URL = "http://127.0.0.1:8766/"

CONVEXITY_PAGES = {
    "candidate-pool.html",
    "change-explanations.html",
    "data-dictionary.html",
    "data-backbone.html",
    "discovery-funnel.html",
    "evidence-ledger.html",
    "source-adapter.html",
    "catalyst-paths.html",
    "decision-quality.html",
    "four-layer-screening.html",
    "gold-calibration.html",
    "high-value-sources.html",
    "manual-review.html",
    "model-acceptance.html",
    "monitoring-infrastructure.html",
    "network-discovery.html",
    "project-detail.html",
    "project-master-pool.html",
    "real-case-calibration.html",
    "rules-replay.html",
    "scan-center.html",
    "screening-console.html",
    "source-discovery.html",
    "source-registry.html",
    "update-center.html",
    "weak-signal-inbox.html",
    "workbench.html",
    "action-gaps.html",
}
STYLE_VERSIONS = {
    "data-backbone.html": "20260801-c1p7a",
    "candidate-pool.html": "20260802-c1p8-sol1",
    "action-gaps.html": "20260802-c1p8-sol1",
    "change-explanations.html": "20260802-c1p8-sol1",
    "catalyst-paths.html": "20260802-c1p8-sol1",
    "decision-quality.html": "20260802-c1p8-sol1",
    "update-center.html": "20260802-c1p8-sol1",
}


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, _tag, attrs):
        attributes = dict(attrs)
        for name in ("href", "src"):
            value = attributes.get(name, "").strip()
            if value:
                self.resources.append(value)


def local_resource(page, value):
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith(("#", "//", "data:")):
        return None
    path = parsed.path
    if not path:
        return None
    return (page.parent / path).resolve()


def verify_static_routes():
    pages = {page.name for page in APP_ROOT.glob("*.html")}
    assert pages == CONVEXITY_PAGES, (
        f"凸性页面清单不一致：缺少 {sorted(CONVEXITY_PAGES - pages)}；"
        f"多出 {sorted(pages - CONVEXITY_PAGES)}"
    )

    for page_name in sorted(CONVEXITY_PAGES):
        page = APP_ROOT / page_name
        html = page.read_text(encoding="utf-8")
        style_version = STYLE_VERSIONS.get(page_name, "20260731-c1p6p06a")
        assert f"styles.css?v={style_version}" in html, (
            f"{page_name} 没有加载当前样式"
        )
        parser = ResourceParser()
        parser.feed(html)
        for value in parser.resources:
            resource = local_resource(page, value)
            if resource and resource.suffix.lower() in {".css", ".html", ".js"}:
                assert resource.exists(), f"{page_name} 引用了不存在的资源：{value}"

    shell_html = (SHELL_ROOT / "index.html").read_text(encoding="utf-8")
    shell_js = (SHELL_ROOT / "shell.js").read_text(encoding="utf-8")
    assert 'data-page="candidate-pool.html"' in shell_html
    assert 'data-detail-entry' in shell_html
    assert 'data-page="workbench.html"' in shell_html
    assert "RWA" not in shell_html
    for marker in (
        "function cleanRoute",
        "function routeFromFrame",
        "function openRoute",
        "lastDetailRoute",
        'frame.addEventListener("load"',
    ):
        assert marker in shell_js, f"桌面软件缺少页面恢复机制：{marker}"


def verify_user_paths():
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    for href in (
        "update-center.html",
        "source-discovery.html",
        "manual-review.html",
        "evidence-ledger.html",
        "source-adapter.html",
        "catalyst-paths.html",
        "monitoring-infrastructure.html",
        "weak-signal-inbox.html",
        "data-backbone.html",
        "candidate-pool.html",
    ):
        assert f'href="{href}"' in workbench, f"工作台日常流程缺少入口：{href}"

    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    for page_name in sorted(CONVEXITY_PAGES - {
        "candidate-pool.html",
        "project-detail.html",
    }):
        assert f'"{page_name}"' in navigation, (
            f"凸性工作台导航缺少页面：{page_name}"
        )

    opportunity_html = (APP_ROOT / "candidate-pool.html").read_text(
        encoding="utf-8"
    )
    opportunity_js = (APP_ROOT / "candidate-pool.js").read_text(
        encoding="utf-8"
    )
    detail_html = (APP_ROOT / "project-detail.html").read_text(
        encoding="utf-8"
    )
    master_js = (APP_ROOT / "project-master-pool.js").read_text(
        encoding="utf-8"
    )
    assert 'id="opportunityDirectory"' in opportunity_html
    assert "item.detailUrl" in opportunity_js
    assert "进入项目详情" in opportunity_js
    assert 'href="candidate-pool.html#opportunityDirectory"' in detail_html
    assert "全部凸性项目" in detail_html
    assert "project-detail.html?id=" in master_js
    assert "打开完整详情" in master_js

    page_state = (APP_ROOT / "page-state.js").read_text(encoding="utf-8")
    for page_name in (
        "manual-review",
        "candidate-pool",
        "project-master-pool",
    ):
        script = (APP_ROOT / f"{page_name}.js").read_text(encoding="utf-8")
        assert f'pageState?.load("{page_name}")' in script
    assert "penguin.convexity.page-state.c1p08:" in page_state


def fetch_page(url):
    request = Request(url, headers={"User-Agent": "PenguinResearch-C1.2-QA"})
    with urlopen(request, timeout=10) as response:
        body = response.read()
        assert response.status == 200, f"{url} 返回 HTTP {response.status}"
        assert len(body) > 200, f"{url} 返回内容异常短"


def verify_live_routes(base_url):
    routes = [
        "desktop/index.html",
        "desktop/shell.js?v=m1p0",
        "styles.css?v=20260731-c1p6p06a",
    ]
    routes.extend(page for page in sorted(CONVEXITY_PAGES))
    for route in routes:
        fetch_page(urljoin(base_url, route))
    assert len(routes) == 31
    print(f"当前版本本地服务检查通过：{len(routes)} 个页面或资源均可访问。")


def main():
    parser = argparse.ArgumentParser(description="C1.2 完整使用流程封版检查")
    parser.add_argument(
        "--live",
        action="store_true",
        help="同时检查企鹅投研本地服务的页面响应",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    verify_static_routes()
    verify_user_paths()
    if args.live:
        verify_live_routes(args.base_url)
    print("页面、资源、桌面路由与核心用户路径测试通过。")


if __name__ == "__main__":
    main()
