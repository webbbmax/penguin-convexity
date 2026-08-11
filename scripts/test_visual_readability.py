#!/usr/bin/env python3
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"


def main():
    pages = sorted(APP_ROOT.glob("*.html"))
    assert len(pages) == 28
    for page in pages:
        html = page.read_text(encoding="utf-8")
        version = (
            "20260801-c1p7a"
            if page.name == "data-backbone.html"
            else "20260802-c1p8-sol1"
            if page.name in {
                "candidate-pool.html",
                "action-gaps.html",
                "change-explanations.html",
                "catalyst-paths.html",
                "decision-quality.html",
                "update-center.html",
            }
            else "20260731-c1p6p06a"
        )
        assert (
            f"styles.css?v={version}" in html
        ), f"{page.name} 没有加载当前统一样式"

    styles = (APP_ROOT / "styles.css").read_text(encoding="utf-8")
    marker = styles.index("C1.2-09 desktop readability and visual consistency")
    readability = styles[marker:]
    for rule in (
        "body small",
        "font-size: 13px !important",
        "body input",
        "min-height: 40px",
        "body table",
        "font-size: 14px",
        ":focus-visible",
        "scrollbar-color",
    ):
        assert rule in readability, f"统一可读性样式缺少：{rule}"

    legacy_colors = (
        "#0f766e",
        "#047857",
        "#059669",
        "#10b981",
        "#14b8a6",
        "#0d9488",
        "#7c3aed",
        "#6d28d9",
    )
    compact_styles = re.sub(r"\s+", "", styles.lower())
    for color in legacy_colors:
        assert color not in compact_styles, f"仍存在旧版 VI 色：{color}"

    print("C1.2-09 全页样式版本、字号下限、控件可读性与 VI 色测试通过。")


if __name__ == "__main__":
    main()
