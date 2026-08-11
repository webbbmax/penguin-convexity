#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
sys.path.insert(0, str(ROOT / "scripts"))

from build_model_acceptance_snapshot import (  # noqa: E402
    DEFAULT_BLIND_EXPECTED_PATH,
    DEFAULT_BLIND_INPUT_PATH,
    DEFAULT_FOUR_LAYER_PATH,
    DEFAULT_REMEDIATION_PATH,
    build_snapshot,
)


def main():
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_four_layer_screening_snapshot.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    snapshot = build_snapshot(
        DEFAULT_FOUR_LAYER_PATH,
        DEFAULT_BLIND_INPUT_PATH,
        DEFAULT_BLIND_EXPECTED_PATH,
        DEFAULT_REMEDIATION_PATH,
    )
    assert snapshot["version"] == "C1.1-09"
    assert snapshot["verdict"] == "passed"
    assert snapshot["gold"]["accuracyPct"] == 100.0
    assert snapshot["blind"]["summary"]["total"] == 17
    assert snapshot["blind"]["summary"]["exactAccuracyPct"] == 100.0
    assert snapshot["blind"]["summary"]["actionableRecallPct"] == 100.0
    assert snapshot["blind"]["summary"]["actionablePrecisionPct"] == 100.0
    assert snapshot["blind"]["summary"]["safetyEscapes"] == 0
    assert all(item["passed"] for item in snapshot["criteria"])
    assert snapshot["remediation"]["beforeFix"]["safetyEscapes"] == 2
    assert snapshot["investmentValidation"]["status"] == "insufficient_outcome_data"

    cases = {item["id"]: item for item in snapshot["blind"]["cases"]}
    assert cases["blind-observe-no-ignition"]["actionCategory"] == "observe"
    assert cases["blind-observe-priced"]["actionCategory"] == "observe"
    assert cases["blind-extreme-microcap"]["actionCategory"] == "extreme"
    assert cases["blind-ordinary-adoption"]["actionCategory"] == "ordinary"

    html = (APP_ROOT / "model-acceptance.html").read_text(encoding="utf-8")
    script = (APP_ROOT / "model-acceptance.js").read_text(encoding="utf-8")
    workbench = (APP_ROOT / "workbench.html").read_text(encoding="utf-8")
    navigation = (APP_ROOT / "workbench-nav.js").read_text(encoding="utf-8")
    assert "C1.1-09" in html
    assert 'id="acceptanceCriteria"' in html
    assert 'id="acceptanceCaseList"' in html
    assert "真实投资有效性尚未验证" in html
    assert "修复前动作一致性" in script
    assert ".slice(" not in script
    assert 'href="model-acceptance.html"' in workbench
    assert '["model-acceptance.html", "模型验收"]' in navigation

    print("C1.1-09 model acceptance checks passed")


if __name__ == "__main__":
    main()
