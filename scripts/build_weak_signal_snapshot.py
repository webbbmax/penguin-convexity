#!/usr/bin/env python3
import argparse
import json
import sqlite3
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH
from weak_signal_inbox import (
    SOURCE_POLICIES,
    TRIAGE_LABELS,
    latest_weak_signals,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "weak-signal-snapshot.js"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_json(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def signal_record(item):
    policy = SOURCE_POLICIES[item["source_id"]]
    metadata = parse_json(item["metadata_json"], {})
    return {
        "weakSignalId": item["weak_signal_id"],
        "sourceId": item["source_id"],
        "sourceName": item["source_name"],
        "sourceRecordType": item["source_record_type"],
        "sourceRecordId": item["source_record_id"],
        "projectId": item["project_id"],
        "projectName": item["project_name"],
        "caseId": item["case_id"],
        "rawEventId": item["raw_event_id"],
        "signalType": item["signal_type"],
        "signalTypeLabel": policy["signal_label"],
        "sourceTier": item["source_tier"],
        "sourceTierLabel": policy["tier_label"],
        "promotionBias": item["promotion_bias"],
        "projectRelationStatus": item["project_relation_status"],
        "triageStatus": item["triage_status"],
        "triageLabel": TRIAGE_LABELS[item["triage_status"]],
        "title": item["title"],
        "summary": item["summary"],
        "sourceUrl": item["source_url"],
        "upgradeRequirement": item["upgrade_requirement"],
        "observedAt": item["observed_at"],
        "metadata": metadata,
        "projectDetailUrl": (
            "project-detail.html?"
            + urllib.parse.urlencode(
                {"id": f"project:{item['project_id']}"}
            )
            if item["project_id"]
            else ""
        ),
    }


def build_weak_signal_snapshot(connection):
    rows = latest_weak_signals(connection)
    records = [signal_record(item) for item in rows]
    source_counts = Counter(item["sourceId"] for item in records)
    triage_counts = Counter(item["triageStatus"] for item in records)
    signal_type_counts = Counter(item["signalType"] for item in records)
    bias_counts = Counter(item["promotionBias"] for item in records)
    projects = {item["projectId"] for item in records if item["projectId"]}
    sources = []
    for source_id, policy in SOURCE_POLICIES.items():
        sources.append(
            {
                "sourceId": source_id,
                "label": policy["label"],
                "signalType": policy["signal_type"],
                "signalTypeLabel": policy["signal_label"],
                "sourceTier": policy["source_tier"],
                "sourceTierLabel": policy["tier_label"],
                "promotionBias": policy["promotion_bias"],
                "proves": policy["proves"],
                "doesNotProve": policy["does_not_prove"],
                "recordCount": source_counts.get(source_id, 0),
                "connectionStatus": (
                    "connected" if source_counts.get(source_id, 0) else "empty"
                ),
            }
        )
    sources.append(
        {
            "sourceId": "discovery-x-social",
            "label": "X 社交扩散",
            "signalType": "social_diffusion",
            "signalTypeLabel": "社交扩散",
            "sourceTier": "social_discovery",
            "sourceTierLabel": "社交发现线索",
            "promotionBias": "high",
            "proves": "接入后可用于发现 KOL、社区和项目官方账号正在讨论什么。",
            "doesNotProve": "当前未接入自动接口，也没有伪造记录；单条帖子、KOL 观点和热度不能成为行动依据。",
            "recordCount": 0,
            "connectionStatus": "not_connected",
        }
    )
    return {
        "version": "C1.6-06",
        "generatedAt": utc_now(),
        "boundary": (
            "弱线索只负责扩大召回和安排补证。任何线索都不能直接提高机器评分、"
            "改变结论或产生行动；付费推广、项目自填资料和社交热度必须经过项目身份、"
            "一手事实与独立来源补证。"
        ),
        "counts": {
            "signals": len(records),
            "projectsLinked": len(projects),
            "readyForCorroboration": triage_counts.get(
                "ready_for_corroboration",
                0,
            ),
            "discoveryOnly": triage_counts.get("discovery_only", 0),
            "identityBlocked": triage_counts.get("identity_blocked", 0),
            "conflicts": triage_counts.get("conflict", 0),
            "highPromotionBias": bias_counts.get("high", 0),
            "connectedSources": sum(
                item["connectionStatus"] == "connected" for item in sources
            ),
            "unconnectedSources": sum(
                item["connectionStatus"] == "not_connected" for item in sources
            ),
        },
        "triageLabels": TRIAGE_LABELS,
        "sourceCounts": dict(source_counts),
        "signalTypeCounts": dict(signal_type_counts),
        "sources": sources,
        "records": records,
    }


def write_weak_signal_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        "window.PENGUIN_CONVEXITY_WEAK_SIGNALS = "
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="生成凸性弱线索收件箱快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_weak_signal_snapshot(connection)
    finally:
        connection.close()
    write_weak_signal_snapshot(snapshot, args.output)
    print(json.dumps(snapshot["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
