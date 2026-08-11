#!/usr/bin/env python3
import json
from pathlib import Path

from update_tasks import TASK_DEFINITIONS, task_for_source


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
SHELL_ROOT = PROJECT_ROOT / "scripts"


def load_snapshot(name, prefix):
    text = (APP_ROOT / name).read_text(encoding="utf-8").strip()
    assert text.startswith(prefix) and text.endswith(";")
    return json.loads(text[len(prefix):-1])


def main():
    assert "cactus_discovery_continue" in TASK_DEFINITIONS
    assert TASK_DEFINITIONS["cactus_discovery_continue"]["sourceIds"] == [
        "discovery-cactus-organizations"
    ]
    assert (
        task_for_source("discovery-cactus-organizations")
        == "cactus_discovery_continue"
    )

    updates = load_snapshot(
        "update-center-snapshot.js",
        "window.PENGUIN_CONVEXITY_UPDATE_CENTER = ",
    )
    task_ids = {item["taskId"] for item in updates["tasks"]}
    assert "cactus_discovery_continue" in task_ids
    cactus_stats = [
        stat
        for run in updates["runs"]
        for stat in run["sourceStats"]
        if stat.get("sourceId") == "discovery-cactus-organizations"
    ]
    assert all(stat.get("actionKind") == "continue" for stat in cactus_stats)
    assert all(
        run["displayStatus"] != "partial_success"
        for run in updates["runs"]
        if not run["errors"]
        and any(
            stat.get("actionKind") in ("continue", "review")
            for stat in run["sourceStats"]
        )
    )

    changes = load_snapshot(
        "change-explanations-snapshot.js",
        "window.PENGUIN_CONVEXITY_CHANGE_EXPLANATIONS = ",
    )
    assert changes["counts"]["recent24h"] == len(changes["recent24h"])
    assert changes["counts"]["recent7d"] == len(changes["recent7d"])

    manual_html = (APP_ROOT / "manual-review.html").read_text(encoding="utf-8")
    manual_script = (APP_ROOT / "manual-review.js").read_text(encoding="utf-8")
    assert "默认只看必须处理" in manual_html
    assert '|| "must_handle"' in manual_script
    assert "下一步：" in manual_script

    launcher = (SHELL_ROOT / "launch-convexity.ps1").read_text(encoding="utf-8")
    window_helper = (
        SHELL_ROOT / "convexity-window-state.ps1"
    ).read_text(encoding="utf-8")
    assert "Local\\PenguinResearchConvexityDesktopLauncher" in launcher
    assert "Consolidate-PenguinAppWindows" in launcher
    assert "Activate-PenguinAppWindow" in launcher
    assert "Close-PenguinAppWindow" in window_helper
    print("C1.2.1 repair release checks passed")


if __name__ == "__main__":
    main()
