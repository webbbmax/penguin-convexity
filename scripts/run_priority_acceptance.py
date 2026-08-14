#!/usr/bin/env python3
"""Run acceptance in fixed business-risk order and stop on the first failed tier."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TIERS = {
    "core": [
        [sys.executable, "scripts/test_c2_2_core_pipeline.py"],
        [sys.executable, "scripts/test_candidate_production.py"],
        [sys.executable, "scripts/test_c2_2_tracking_repair.py"],
    ],
    "data": [
        [sys.executable, "scripts/test_c2_2_snapshots.py"],
        [sys.executable, "scripts/test_c2_2_acceptance.py"],
        [sys.executable, "scripts/test_temp_artifact_retention.py"],
    ],
    "desktop": [
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/test-c2.3-desktop-smoke.ps1",
        ]
    ],
}
ORDER = ("core", "data", "desktop")


def main() -> int:
    parser = argparse.ArgumentParser(description="企鹅投研-凸性分层验收")
    parser.add_argument("--through", choices=ORDER, default="data")
    args = parser.parse_args()
    maximum = ORDER.index(args.through)
    results = []
    for tier in ORDER[: maximum + 1]:
        for command in TIERS[tier]:
            print(f"[priority:{tier}] {' '.join(command)}", flush=True)
            completed = subprocess.run(command, cwd=ROOT, check=False)
            results.append({"tier": tier, "command": command, "exitCode": completed.returncode})
            if completed.returncode:
                print(json.dumps({"status": "failed", "failedTier": tier, "results": results}, ensure_ascii=False, indent=2))
                return completed.returncode
    print(json.dumps({"status": "passed", "through": args.through, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
