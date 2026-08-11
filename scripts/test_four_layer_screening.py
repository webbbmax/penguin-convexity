#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_four_layer_screening_snapshot.py"
SNAPSHOT_PATH = APP_ROOT / "four-layer-screening-snapshot.js"


def load_snapshot():
    prefix = "window.PENGUIN_CONVEXITY_FOUR_LAYER = "
    text = SNAPSHOT_PATH.read_text(encoding="utf-8").strip()
    assert text.startswith(prefix) and text.endswith(";")
    return json.loads(text[len(prefix):-1])


def main():
    subprocess.run([sys.executable, str(SCRIPT_PATH)], check=True)
    snapshot = load_snapshot()
    assert snapshot["version"] == "C1.1-03"
    live_total = snapshot["live"]["summary"]["total"]
    assert live_total == len(snapshot["live"]["cases"])
    assert live_total >= 0
    assert sum(snapshot["live"]["summary"]["actionCounts"].values()) == live_total
    assert snapshot["calibration"]["summary"]["total"] == 14
    assert snapshot["calibration"]["summary"]["matched"] == 14
    assert snapshot["calibration"]["summary"]["mismatched"] == 0
    assert snapshot["calibration"]["summary"]["accuracyPct"] == 100
    assert len(snapshot["layerDefinitions"]) == 4

    by_id = {item["id"]: item for item in snapshot["calibration"]["cases"]}
    assert by_id["gold-aero-20260729"]["actionCategory"] == "ordinary"
    assert by_id["gold-qubic-20260729"]["actionCategory"] == "extreme"
    assert by_id["gold-cowl-20260729"]["actionCategory"] == "extreme"
    assert by_id["gold-hashi-20260724"]["actionCategory"] == "observe"
    assert by_id["gold-glmr-20260729"]["actionCategory"] == "reject"
    assert by_id["gold-akedo-20260729"]["actionCategory"] == "reflexive"
    live_by_project = {item["project"]: item for item in snapshot["live"]["cases"]}
    if live_by_project:
        assert all(
            not item["id"].startswith("thread-")
            for item in snapshot["live"]["cases"]
        )
    assert all(len(item["layers"]) == 4 for item in snapshot["live"]["cases"])
    assert all(item["stoppedLayer"] in {1, 2, 3, 4} for item in snapshot["live"]["cases"])

    html = (APP_ROOT / "four-layer-screening.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "four-layer-screening.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    requirement_tree = (PROJECT_ROOT / "C1.1-凸性发现质量升级-需求树.md").read_text(encoding="utf-8")
    assert "C1.1 凸性发现质量升级 · C1.1-03" in html
    assert "four-layer-screening-snapshot.js" in html
    assert "goldCalibration" in script
    assert "four-layer-screening.html" in workbench
    assert "| C1.1-03 | 四层自动筛选模型" in requirement_tree
    print("C1.1-03 four-layer screening checks passed")


if __name__ == "__main__":
    main()
