#!/usr/bin/env python3
"""Build the read-only C2.2 7/14/30-day out-of-time calibration report."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
HORIZONS = (7, 14, 30)
RULE_VERSION = "c2.2-bayes-v1"
MODEL_VERSION = "c2.2-deterministic-hierarchical-eb-v1"


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _candidate_id(project_id: Any) -> int | None:
    text = str(project_id or "")
    if not text.startswith("c21-"):
        return None
    try:
        return int(text.split("-", 1)[1])
    except ValueError:
        return None


def _time(value: Any) -> str:
    return str(value or "")


def _checkpoint(connection: sqlite3.Connection, candidate_id: int, horizon: int) -> sqlite3.Row | None:
    # Only an evaluation at the requested historical age (one-day tolerance)
    # is a valid checkpoint.  A current 59-day result cannot be relabeled as
    # the project's 7/14/30-day outcome.
    return connection.execute(
        """
        SELECT * FROM evaluations
        WHERE candidate_id=? AND age_days BETWEEN ? AND ?
        ORDER BY ABS(age_days-?), evaluated_at ASC
        LIMIT 1
        """,
        (candidate_id, horizon, horizon + 1, horizon),
    ).fetchone()


def _labels(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "status": "not_available",
            "reason": "没有该真实日龄附近的完整历史评估；不使用当前结果倒推。",
            "hardGateRetained": None,
            "liquidityAboveGuardrail": None,
            "strongPathPersisted": None,
            "hardRiskFound": None,
            "productEvidenceChanged": None,
            "observedAt": None,
            "evaluationAgeDays": None,
        }
    paths = _json(row["paths_json"], [])
    market = _json(row["market_snapshot_json"], {})
    formed = [path for path in paths if path.get("status") == "formed"] if isinstance(paths, list) else []
    trade_path = next((path for path in formed if path.get("pathCode") == "trade_liquidity_formation"), None)
    liquidity = market.get("liquidityUsd") if isinstance(market, dict) else None
    threshold = None
    for metric in (trade_path or {}).get("supportingMetrics") or []:
        if metric.get("label") == "流动性":
            threshold = metric.get("threshold")
            break
    return {
        "status": "available",
        "reason": "存在该真实日龄附近的完整历史评估。",
        "hardGateRetained": row["hard_gate_status"] == "pass",
        "liquidityAboveGuardrail": bool(liquidity is not None and threshold is not None and float(liquidity) >= float(threshold)),
        "strongPathPersisted": bool(len(formed) >= 2 and trade_path),
        # The frozen pipeline evaluation does not contain a standalone risk
        # outcome column.  Null means not measured, never “no risk”.
        "hardRiskFound": None,
        "productEvidenceChanged": None,
        "observedAt": row["evaluated_at"],
        "evaluationAgeDays": row["age_days"],
    }


def build_calibration_payload(front: dict[str, Any], tracking: dict[str, Any], db_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        items = []
        for item in sorted(front.get("items") or [], key=lambda row: str(row.get("assetId") or "")):
            candidate_id = _candidate_id(item.get("projectId"))
            horizons = {}
            for horizon in HORIZONS:
                checkpoint = _checkpoint(connection, candidate_id, horizon) if candidate_id is not None else None
                horizons[str(horizon)] = _labels(checkpoint)
            tracking_item = next((row for row in tracking.get("items") or [] if row.get("assetId") == item.get("assetId")), {})
            items.append({
                "assetId": item.get("assetId"),
                "projectId": item.get("projectId"),
                "effectiveT0": item.get("effectiveT0"),
                "ageDaysAtReport": item.get("ageDays"),
                "currentPosterior": (tracking_item.get("factorPosteriors") or {}).get("factors") or [],
                "currentTotal": (tracking_item.get("factorPosteriors") or {}).get("total"),
                "currentConfidence": (tracking_item.get("factorPosteriors") or {}).get("confidenceScore"),
                "ruleVersion": RULE_VERSION,
                "horizons": horizons,
            })
    finally:
        connection.close()
    summary = {}
    for horizon in HORIZONS:
        rows = [item["horizons"][str(horizon)] for item in items]
        available = [row for row in rows if row.get("status") == "available"]
        sample_count = len(available)
        summary[str(horizon)] = {
            "availableCount": sample_count,
            "sampleStatus": "sample_insufficient" if sample_count < 30 else "ready_for_descriptive_report",
            "plainReason": "样本不足（少于30个时间外结果），不输出准确率结论。" if sample_count < 30 else "样本达到描述性报告门槛；仍不代表收益预测。",
            "hardGateRetentionRate": _rate(available, "hardGateRetained"),
            "liquidityGuardrailRetentionRate": _rate(available, "liquidityAboveGuardrail"),
            "strongPathPersistenceRate": _rate(available, "strongPathPersisted"),
        }
    generated_at = front.get("sourceCutoffAt") or front.get("generatedAt") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body = {
        "schemaVersion": "c2.2-calibration-v1",
        "modelVersion": MODEL_VERSION,
        "ruleVersion": RULE_VERSION,
        "generatedAt": generated_at,
        "sourceCutoffAt": front.get("sourceCutoffAt"),
        "labelBoundary": "只使用真实T0后7/14/30天附近完整评估；当前结果不倒推历史标签；收益率不作为成功标签。",
        "parameterMutation": "none",
        "summary": summary,
        "items": items,
    }
    body["buildId"] = "c22-calibration-" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return body


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if isinstance(row.get(key), bool)]
    return (sum(1 for value in values if value) / len(values)) if values else None


def write_calibration_payload(payload: dict[str, Any], output_path: Path | None = None) -> Path:
    target = output_path or (ROOT / "runtime" / "c2.2" / "calibration" / "latest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


def main() -> int:
    from build_tracking_tasks_snapshot import load_js_payload

    front = load_js_payload(ROOT / "app" / "c2-2-front-snapshot.js", "window.PENGUIN_CONVEXITY_C22 = ")
    tracking = load_js_payload(ROOT / "app" / "c2-2-tracking-snapshot.js", "window.PENGUIN_CONVEXITY_C22_TRACKING = ")
    payload = build_calibration_payload(front, tracking, ROOT / "data" / "c2.1-pipeline.db")
    path = write_calibration_payload(payload)
    print(json.dumps({"path": str(path), "buildId": payload["buildId"], "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
