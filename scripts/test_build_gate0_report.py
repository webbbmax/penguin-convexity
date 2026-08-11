#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent / "build_gate0_report.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_gate0_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    module = load_module()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        module.REPORT_ROOT = root
        module.SUMMARY_PATH = root / "analysis-summary.json"
        module.ARTIFACT_PATH = root / "artifact.json"
        module.HTML_PATH = root / "report.html"
        module.BACKFILL_ROLLUP_PATH = root / "coverage-rollup.json"
        module.BACKGROUND_LATEST_PATH = root / "background" / "latest.json"
        module.RESOURCE_CATALOG_PATH = root / "resources.json"

        summary = {
            "finishedAt": "2026-08-04T12:06:40Z",
            "coverage": [],
            "blockingReasons": {},
            "capabilityProbes": [],
            "counts": {"pools": 1, "candidateTokens": 1, "preGatePass": 0},
            "requestSummary": {"byState": {}},
            "gate0Passed": False,
            "shadowDaysObserved": 1,
            "liveReliabilityTargetDistinctDays": 14,
            "backfill": {
                "coverage": {
                    "networksObserved": 1,
                    "observedDexGroups": 1,
                    "verifiedEvmSchemas": 1,
                    "solanaDexGroupsProgramIdentified": 0,
                    "solanaDexGroups": 0,
                    "evmScansComplete": 1,
                    "evmScanUnits": 1,
                    "candidateTokens": 10,
                    "eventRows": 10,
                },
                "networkResults": [],
            },
        }
        rollup = {
            "generatedAt": "2026-08-05T00:33:29Z",
            "coverage": {
                "networksObserved": 6,
                "observedDexGroups": 43,
                "verifiedEvmSchemas": 18,
                "solanaDexGroupsProgramIdentified": 7,
                "solanaDexGroups": 7,
                "evmScansComplete": 16,
                "evmScanUnits": 16,
                "solanaScanUnits": 1,
                "solanaSourceRangesComplete": 1,
                "solanaRequestedWindowsComplete": 0,
                "candidateTokens": 1496844,
                "eventRows": 1802896,
            },
            "networkResults": [
                {
                    "networkId": "bnb-mainnet",
                    "observedDexGroups": 5,
                    "verifiedEvmSchemas": 3,
                    "solanaDexGroupsProgramIdentified": 0,
                    "solanaDexGroups": 0,
                    "evmScansComplete": 3,
                    "evmScanUnits": 3,
                    "historicalBackfillComplete": False,
                }
            ],
        }
        resources = {
            "resources": [
                {
                    "id": "nodereal",
                    "name": "NodeReal MegaNode",
                    "connectionStatus": "gate0_bnb_90d_registered_backfill_complete",
                    "consumerStatus": "gate0_primary_historical_logs",
                }
            ]
        }
        background = {
            "runId": "gate0-solfinal-test",
            "state": "completed",
            "partitionProgress": {"completedCount": 262, "totalCount": 262},
            "events": 5922807,
            "candidateTokens": 4585955,
            "requests": {"total": 6723, "byState": {"success": 6477, "source_failure": 246}},
        }
        module.SUMMARY_PATH.write_text(json.dumps(summary), encoding="utf-8")
        module.BACKFILL_ROLLUP_PATH.write_text(json.dumps(rollup), encoding="utf-8")
        module.RESOURCE_CATALOG_PATH.write_text(json.dumps(resources), encoding="utf-8")
        module.BACKGROUND_LATEST_PATH.parent.mkdir(parents=True)
        module.BACKGROUND_LATEST_PATH.write_text(json.dumps(background), encoding="utf-8")
        audit_path = module.BACKGROUND_LATEST_PATH.parent / "runs" / background["runId"] / "sol-independent-audit.json"
        audit_path.parent.mkdir(parents=True)
        audit_path.write_text(json.dumps({"runId": background["runId"], "pass": True}), encoding="utf-8")

        module.main()

        html = module.HTML_PATH.read_text(encoding="utf-8")
        artifact = module.ARTIFACT_PATH.read_text(encoding="utf-8")
        assert "5,922,807" in html
        assert "4,585,955" in html
        assert "Gate 0 通过" in html
        assert 'meta name="viewport"' in html
        assert "overflow-x:hidden" in html
        assert "repeat(auto-fit" in html
        assert "16/16" in html
        assert "Solana 创建解码" in html
        assert "独立重算和完成后幂等复验均通过" in artifact
        assert "完整90天运行仍在执行" not in artifact
        assert "NodeReal MegaNode" in html
        assert "5,922,807" in artifact
        assert "Ethereum 4、Base 8、Arbitrum 6个未确认标签" in artifact
        assert "不得解释为全市场完整母池或全局T0" in artifact
        assert "BNB剩余2个、Robinhood 7个" not in artifact
        assert "617,524" not in html

    print("PASS: Gate 0 report reads the current backfill rollup and resource status")


if __name__ == "__main__":
    main()
