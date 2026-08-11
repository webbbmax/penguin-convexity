#!/usr/bin/env python3
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from build_project_master_pool import build_master_pool_snapshot
from init_db import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "manual-review-snapshot.js"

CLASSIFICATION_LABELS = {
    "unclassified": "未分类",
    "watch_embryo": "观察胚胎",
    "ordinary_candidate": "普通候选",
    "extreme_candidate": "极限候选",
    "risk_excluded": "风险排除",
}

PRIORITY_LABELS = {
    "P0": "立即复核",
    "P1": "优先复核",
    "P2": "正常排队",
    "P3": "低优先级",
}

RISK_LABELS = {
    "unknown": "待核验",
    "low": "低",
    "medium": "中",
    "high": "高",
    "blocking": "阻断",
}

RESEARCH_ROUTE_LABELS = {
    "auto": "跟随项目类别",
    "startup": "早期项目研究重点",
    "mature": "OG项目研究重点",
    "hybrid": "平衡研究重点",
}

PUBLICATION_LABELS = {
    "not_created": "未升格",
    "draft": "草稿",
    "preview": "预览",
    "published": "已进入机会中心",
    "withdrawn": "已撤回",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_json(value, fallback=None):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def annotation_target_key(row):
    if row["project_id"]:
        return f"project:{row['project_id']}"
    if row["discovery_id"]:
        return f"discovery:{row['discovery_id']}"
    if row["case_id"]:
        return f"case:{row['case_id']}"
    return ""


def active_reviews(connection):
    result = {}
    for row in connection.execute(
        """
        SELECT *
        FROM manual_annotations
        WHERE field_name = 'manual_review' AND status = 'active'
        ORDER BY updated_at DESC, annotation_id DESC
        """
    ):
        key = annotation_target_key(row)
        if key and key not in result:
            result[key] = {
                "annotationId": row["annotation_id"],
                "values": parse_json(row["annotation_value_json"]),
                "note": row["note"],
                "actor": row["actor"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
    return result


def latest_publications(connection):
    by_project = {}
    by_case = {}
    for row in connection.execute(
        """
        SELECT *
        FROM publication_records
        ORDER BY updated_at DESC, publication_id DESC
        """
    ):
        item = dict(row)
        item["sourceSnapshot"] = parse_json(row["source_snapshot_json"])
        if row["project_id"] and row["project_id"] not in by_project:
            by_project[row["project_id"]] = item
        if row["case_id"] and row["case_id"] not in by_case:
            by_case[row["case_id"]] = item
    return by_project, by_case


def target_publication(record, by_project, by_case):
    if record["caseId"] and record["caseId"] in by_case:
        return by_case[record["caseId"]]
    if record["projectId"] and record["projectId"] in by_project:
        return by_project[record["projectId"]]
    return None


def promotion_blockers(record, review):
    values = (review or {}).get("values", {})
    blockers = []
    if record["recordType"] != "project":
        blockers.append("尚未建立项目主体，先保留在线索复核队列。")
    if not record["caseId"]:
        blockers.append("尚未建立候选研究案例，不能进入机会中心。")
    if not record["contractAddress"]:
        blockers.append("缺少可核验的代币合约或资产标识。")
    if values.get("classification", "unclassified") == "unclassified":
        blockers.append("请先保存人工分类。")
    if values.get("classification") == "risk_excluded":
        blockers.append("当前人工分类为风险排除。")
    identity_confirmed = (
        record["identityStatus"] == "verified"
        or bool(values.get("identityConfirmed"))
    )
    if not identity_confirmed:
        blockers.append("项目主体尚未自动确认，也没有人工确认身份与合约。")
    return blockers


def review_queue(record):
    identity_status = str(record.get("identityStatus") or "").lower()
    if record["publicationStatus"] == "published":
        return "published"
    if (
        identity_status == "conflict"
        or (
            record["recordType"] == "project"
            and (
                not record["caseId"]
                or not record["contractAddress"]
                or identity_status not in ("verified", "corroborated")
            )
        )
        or (record["manualReview"] and not record["promotionEligible"])
    ):
        return "must_handle"
    if (
        record["promotionEligible"]
        or (
            record["recordType"] == "project"
            and record["caseId"]
            and record["contractAddress"]
            and identity_status in ("verified", "corroborated")
        )
        or (
            record["recordType"] == "discovery"
            and record["contractAddress"]
            and identity_status in ("verified", "corroborated")
        )
    ):
        return "worth_review"
    return "low_priority"


def build_targets(connection):
    master = build_master_pool_snapshot(connection)
    reviews = active_reviews(connection)
    publications_by_project, publications_by_case = latest_publications(connection)
    targets = []
    for record in master["records"]:
        review = reviews.get(record["masterId"])
        publication = target_publication(
            record,
            publications_by_project,
            publications_by_case,
        )
        blockers = promotion_blockers(record, review)
        values = (review or {}).get("values", {})
        publication_status = (
            publication["publication_status"] if publication else "not_created"
        )
        target = {
                **record,
                "manualReview": review,
                "manualClassification": values.get(
                    "classification",
                    "unclassified",
                ),
                "manualClassificationLabel": CLASSIFICATION_LABELS.get(
                    values.get("classification", "unclassified"),
                    values.get("classification", "unclassified"),
                ),
                "manualPriority": values.get("priority", "P2"),
                "manualPriorityLabel": PRIORITY_LABELS.get(
                    values.get("priority", "P2"),
                    values.get("priority", "P2"),
                ),
                "manualRiskLevel": values.get(
                    "riskLevel",
                    record["riskLevel"] or "unknown",
                ),
                "researchRouteOverride": values.get(
                    "researchRouteOverride",
                    "auto",
                ),
                "researchRouteReason": values.get(
                    "researchRouteReason",
                    "",
                ),
                "identityConfirmedByUser": bool(
                    values.get("identityConfirmed")
                ),
                "publicationStatus": publication_status,
                "publicationStatusLabel": PUBLICATION_LABELS.get(
                    publication_status,
                    publication_status,
                ),
                "publication": publication,
                "promotionEligible": not blockers,
                "promotionBlockers": blockers,
            }
        target["reviewQueue"] = review_queue(target)
        targets.append(target)
    targets.sort(
        key=lambda item: (
            item["publicationStatus"] != "published",
            not bool(item["manualReview"]),
            {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(
                item["manualPriority"],
                4,
            ),
            item["name"].casefold(),
        )
    )
    return targets


def build_audit(connection):
    records = []
    for row in connection.execute(
        """
        SELECT *
        FROM manual_annotations
        WHERE field_name IN ('manual_review', 'manual_review_withdrawal')
        ORDER BY updated_at DESC, annotation_id DESC
        """
    ):
        values = parse_json(row["annotation_value_json"])
        records.append(
            {
                "auditId": row["annotation_id"],
                "auditType": "annotation",
                "action": (
                    "撤回人工标注"
                    if row["field_name"] == "manual_review_withdrawal"
                    or row["status"] == "withdrawn"
                    else "保存人工标注"
                ),
                "targetKey": annotation_target_key(row),
                "projectId": row["project_id"] or "",
                "discoveryId": row["discovery_id"] or "",
                "caseId": row["case_id"] or "",
                "actor": row["actor"],
                "status": row["status"],
                "summary": row["note"],
                "details": values,
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
        )
    for row in connection.execute(
        """
        SELECT *
        FROM publication_records
        ORDER BY updated_at DESC, publication_id DESC
        """
    ):
        records.append(
            {
                "auditId": row["publication_id"],
                "auditType": "publication",
                "action": (
                    "撤回机会中心发布"
                    if row["publication_status"] == "withdrawn"
                    else "升格到机会中心"
                ),
                "targetKey": (
                    f"project:{row['project_id']}"
                    if row["project_id"]
                    else f"case:{row['case_id']}"
                ),
                "projectId": row["project_id"] or "",
                "discoveryId": "",
                "caseId": row["case_id"] or "",
                "actor": "local-owner",
                "status": row["publication_status"],
                "summary": row["summary"],
                "details": parse_json(row["source_snapshot_json"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
        )
    records.sort(
        key=lambda item: (item["updatedAt"], item["auditId"]),
        reverse=True,
    )
    return records


def build_manual_review_snapshot(connection):
    targets = build_targets(connection)
    audit = build_audit(connection)
    return {
        "product": "企鹅投研",
        "workspace": "凸性工作台",
        "workbenchVersion": "C1.2-07",
        "generatedAt": utc_now(),
        "policy": "人工标注是可撤回的判断层，不覆盖自动采集事实。只有已经建立项目主体和候选案例、且身份已自动或人工确认的记录才能进入机会中心。",
        "counts": {
            "total": len(targets),
            "reviewed": sum(bool(item["manualReview"]) for item in targets),
            "unreviewed": sum(not item["manualReview"] for item in targets),
            "withMarketData": sum(
                item["marketDataStatus"] == "available" for item in targets
            ),
            "withoutMarketData": sum(
                item["marketDataStatus"] != "available" for item in targets
            ),
            "promotionEligible": sum(item["promotionEligible"] for item in targets),
            "published": sum(
                item["publicationStatus"] == "published" for item in targets
            ),
            "withdrawn": sum(
                item["publicationStatus"] == "withdrawn" for item in targets
            ),
            "audit": len(audit),
            "mustHandle": sum(
                item["reviewQueue"] == "must_handle" for item in targets
            ),
            "worthReview": sum(
                item["reviewQueue"] == "worth_review" for item in targets
            ),
            "lowPriority": sum(
                item["reviewQueue"] == "low_priority" for item in targets
            ),
        },
        "labels": {
            "classification": CLASSIFICATION_LABELS,
            "priority": PRIORITY_LABELS,
            "risk": RISK_LABELS,
            "researchRoute": RESEARCH_ROUTE_LABELS,
            "publication": PUBLICATION_LABELS,
        },
        "targets": targets,
        "audit": audit,
    }


def write_manual_review_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        "window.PENGUIN_CONVEXITY_MANUAL_REVIEW = "
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return path


def rebuild_manual_review_snapshot(
    db_path=DEFAULT_DB_PATH,
    snapshot_path=DEFAULT_SNAPSHOT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_manual_review_snapshot(connection)
        write_manual_review_snapshot(snapshot, snapshot_path)
        return snapshot
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成凸性人工标注与发布审计快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_manual_review_snapshot(
        db_path=args.db,
        snapshot_path=args.snapshot,
    )
    print(json.dumps(snapshot["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
