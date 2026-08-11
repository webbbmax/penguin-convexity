#!/usr/bin/env python3
import argparse
import json
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH
from monitoring_infrastructure import TARGET_LABELS, latest_monitoring_targets


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "app" / "monitoring-infrastructure-snapshot.js"
)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_json(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def project_profile(project, targets):
    ready_types = {
        item["target_type"]
        for item in targets
        if item["collection_status"] == "ready"
    }
    all_types = {item["target_type"] for item in targets}
    conflicts = [
        item for item in targets if item["collection_status"] == "conflict"
    ]
    blocked = [
        item for item in targets if item["collection_status"] == "blocked"
    ]
    gaps = []
    if project["identity_status"] != "verified":
        gaps.append("项目主体身份待核验")
    if "official_website" not in all_types:
        gaps.append("官网目标待登记")
    if not ({"github_repository", "github_organization"} & all_types):
        gaps.append("GitHub目标待登记")
    if not (
        {
            "github_repository",
            "defillama_protocol",
            "snapshot_space",
            "cactus_governance",
        }
        & ready_types
    ):
        gaps.append("持续证据目标待核验")
    if "asset" not in ready_types:
        gaps.append("受益资产待核验")
    if "contract" not in ready_types:
        gaps.append("资产合约待核验")
    if blocked:
        gaps.append(f"{len(blocked)}个目标仍被身份或归属阻断")

    if conflicts:
        status = "conflict"
        status_label = "存在归属冲突"
    elif project["identity_status"] != "verified":
        status = "identity_blocked"
        status_label = "身份阻断"
    elif not targets:
        status = "empty"
        status_label = "尚无监控目标"
    elif not gaps:
        status = "ready"
        status_label = "监控基础设施完整"
    elif ready_types:
        status = "partial"
        status_label = "部分目标可运行"
    else:
        status = "registered"
        status_label = "已登记待核验"

    return {
        "projectId": project["project_id"],
        "caseId": project["case_id"],
        "projectName": project["canonical_name"],
        "identityStatus": project["identity_status"],
        "status": status,
        "statusLabel": status_label,
        "targetCount": len(targets),
        "readyCount": sum(
            item["collection_status"] == "ready" for item in targets
        ),
        "registeredCount": sum(
            item["collection_status"] == "registered" for item in targets
        ),
        "blockedCount": sum(
            item["collection_status"] == "blocked" for item in targets
        ),
        "conflictCount": len(conflicts),
        "gaps": gaps,
        "targetTypes": sorted(all_types),
        "detailUrl": (
            "project-detail.html?"
            + urllib.parse.urlencode(
                {"id": f"project:{project['project_id']}"}
            )
            if project["project_id"]
            else ""
        ),
        "targets": [
            {
                "targetId": item["monitoring_target_id"],
                "targetType": item["target_type"],
                "targetTypeLabel": TARGET_LABELS.get(
                    item["target_type"],
                    item["target_type"],
                ),
                "targetValue": item["target_value"],
                "targetUrl": item["target_url"],
                "sourceId": item["source_id"],
                "sourceRecordType": item["source_record_type"],
                "sourceRecordId": item["source_record_id"],
                "rawEventId": item["raw_event_id"],
                "evidenceId": item["evidence_id"],
                "relationStatus": item["relation_status"],
                "collectionStatus": item["collection_status"],
                "verificationMethod": item["verification_method"],
                "gapReason": item["gap_reason"],
                "metadata": parse_json(item["metadata_json"], {}),
                "observedAt": item["observed_at"],
            }
            for item in targets
        ],
    }


def build_monitoring_infrastructure_snapshot(connection):
    connection.row_factory = sqlite3.Row
    targets = latest_monitoring_targets(connection)
    by_project = {}
    for item in targets:
        by_project.setdefault(item["project_id"], []).append(item)
    projects = [
        dict(row)
        for row in connection.execute(
            """
            SELECT p.project_id, p.canonical_name, p.identity_status,
                   (
                     SELECT cc.case_id
                     FROM candidate_cases cc
                     WHERE cc.project_id = p.project_id
                     ORDER BY cc.updated_at DESC, cc.case_id DESC
                     LIMIT 1
                   ) AS case_id
            FROM projects p
            ORDER BY p.canonical_name
            """
        )
    ]
    profiles = [
        project_profile(project, by_project.get(project["project_id"], []))
        for project in projects
    ]
    status_counts = {}
    target_type_counts = {}
    collection_counts = {}
    for profile in profiles:
        status_counts[profile["status"]] = (
            status_counts.get(profile["status"], 0) + 1
        )
    for item in targets:
        target_type_counts[item["target_type"]] = (
            target_type_counts.get(item["target_type"], 0) + 1
        )
        collection_counts[item["collection_status"]] = (
            collection_counts.get(item["collection_status"], 0) + 1
        )
    return {
        "version": "C1.6-06",
        "generatedAt": utc_now(),
        "boundary": (
            "监控目标注册只证明系统知道应该去哪里检查。只有项目身份和来源归属通过的目标"
            "才能进入自动采集；登记数量、资料完整度和信源活跃度都不等于凸性或行动结论。"
        ),
        "counts": {
            "projects": len(profiles),
            "targets": len(targets),
            "readyTargets": collection_counts.get("ready", 0),
            "registeredTargets": collection_counts.get("registered", 0),
            "blockedTargets": collection_counts.get("blocked", 0),
            "conflictTargets": collection_counts.get("conflict", 0),
            "readyProjects": status_counts.get("ready", 0),
            "partialProjects": status_counts.get("partial", 0),
            "identityBlockedProjects": status_counts.get(
                "identity_blocked",
                0,
            ),
        },
        "statusCounts": status_counts,
        "targetTypeCounts": target_type_counts,
        "targetTypeLabels": TARGET_LABELS,
        "projects": profiles,
    }


def write_monitoring_infrastructure_snapshot(
    snapshot,
    output_path=DEFAULT_OUTPUT_PATH,
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        "window.PENGUIN_CONVEXITY_MONITORING_INFRASTRUCTURE = "
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return snapshot


def main():
    parser = argparse.ArgumentParser(
        description="生成凸性项目监控基础设施页面快照"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_monitoring_infrastructure_snapshot(connection)
    finally:
        connection.close()
    write_monitoring_infrastructure_snapshot(snapshot, args.output)
    print(json.dumps(snapshot["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
