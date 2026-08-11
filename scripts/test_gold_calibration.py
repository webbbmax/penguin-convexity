#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def main():
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_gold_calibration_snapshot.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    fixture = json.loads((ROOT / "fixtures" / "gold-calibration-c1.1.json").read_text(encoding="utf-8"))
    html = (APP / "gold-calibration.html").read_text(encoding="utf-8")
    script = (APP / "gold-calibration.js").read_text(encoding="utf-8")
    workbench = (APP / "workbench.html").read_text(encoding="utf-8")
    requirement_tree = (ROOT / "C1.1-凸性发现质量升级-需求树.md").read_text(encoding="utf-8")

    assert fixture["version"] == "C1.1-02"
    assert len(fixture["cases"]) == 14
    assert {item["cohort"] for item in fixture["cases"]} == {
        "core_positive",
        "extreme_boundary",
        "observe_only",
        "rejected",
    }
    assert all(item["sourceTurnId"] for item in fixture["cases"])
    assert all(len(item["facts"]) >= 2 for item in fixture["cases"])
    assert "C1.1 凸性发现质量升级 · C1.1-02" in html
    assert "data-gold-case-id" in script
    assert "模型必须学会" in script
    assert 'href="gold-calibration.html"' in workbench
    for branch in range(1, 10):
        assert f"C1.1-{branch:02d}" in requirement_tree
    print("C1.1-02 gold calibration checks passed")


if __name__ == "__main__":
    main()
