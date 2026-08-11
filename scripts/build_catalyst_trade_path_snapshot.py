#!/usr/bin/env python3
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from catalyst_trade_paths import latest_paths
from init_db import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "catalyst-trade-path-snapshot.js"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_catalyst_trade_path_snapshot(connection):
    paths = latest_paths(connection)
    records = []
    for row in connection.execute(
        """
        SELECT case_item.case_id, project.canonical_name, asset.symbol
        FROM candidate_cases case_item
        JOIN projects project ON project.project_id = case_item.project_id
        LEFT JOIN assets asset ON asset.asset_id = case_item.asset_id
        ORDER BY project.canonical_name, case_item.case_id
        """
    ):
        path = paths.get(row["case_id"])
        if not path:
            continue
        records.append(
            {
                **path,
                "projectName": row["canonical_name"],
                "symbol": row["symbol"] or "",
                "detailUrl": (
                    "project-detail.html?id="
                    f"project%3A{path['project_id']}"
                ),
            }
        )
    stage_counts = {}
    catalyst_counts = {}
    for item in records:
        stage_counts[item["path_stage"]] = (
            stage_counts.get(item["path_stage"], 0) + 1
        )
        catalyst_counts[item["catalyst_type"]] = (
            catalyst_counts.get(item["catalyst_type"], 0) + 1
        )
    return {
        "version": "C1.6-06",
        "generatedAt": utc_now(),
        "boundary": (
            "催化路径只连接可溯源证据、受益资产、价值传导、市场与退出。"
            "2万美元滑点为恒定乘积理论估算，不代表真实成交或报价保证；系统不自动交易。"
        ),
        "counts": {
            "total": len(records),
            "withCatalyst": sum(
                item["catalyst_status"] in ("active", "stale")
                for item in records
            ),
            "withAsset": sum(bool(item["asset_id"]) for item in records),
            "exitModeled": sum(
                item["modeled_exit_slippage_pct"] is not None
                for item in records
            ),
            "researchReady": stage_counts.get("research_ready", 0),
            "actionReady": stage_counts.get("action_ready", 0),
            "blocked": sum(bool(item["blockers"]) for item in records),
        },
        "stageCounts": stage_counts,
        "catalystCounts": catalyst_counts,
        "records": records,
    }


def write_catalyst_trade_path_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_CONVEXITY_CATALYST_PATHS = "
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return snapshot


def rebuild_catalyst_trade_path_snapshot(
    db_path=DEFAULT_DB_PATH,
    snapshot_path=DEFAULT_SNAPSHOT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_catalyst_trade_path_snapshot(connection)
        return write_catalyst_trade_path_snapshot(snapshot, snapshot_path)
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成凸性催化交易路径页面快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args()
    print(
        json.dumps(
            rebuild_catalyst_trade_path_snapshot(args.db, args.snapshot)["counts"],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
