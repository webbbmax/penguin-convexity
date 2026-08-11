#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from build_manual_review_snapshot import (
    CLASSIFICATION_LABELS,
    RESEARCH_ROUTE_LABELS,
    build_manual_review_snapshot,
    write_manual_review_snapshot,
)
from build_opportunity_center_snapshot import rebuild_opportunity_center_snapshot
from build_research_route_snapshot import rebuild_research_route_snapshot
from build_tracking_tasks_snapshot import rebuild_tracking_tasks_snapshot
from build_project_detail_snapshot import (
    build_project_detail_snapshot,
    write_project_detail_snapshot,
)
from build_project_master_pool import (
    build_master_pool_snapshot,
    write_master_pool_snapshot,
)
from init_db import (
    DEFAULT_DB_PATH,
    DEFAULT_SNAPSHOT_PATH,
    initialize_database,
    write_runtime_snapshot,
)
from sync_thread_candidates import (
    build_pool_snapshot,
    load_fixture,
    write_pool_snapshot,
)


ALLOWED_CLASSIFICATIONS = set(CLASSIFICATION_LABELS)
ALLOWED_PRIORITIES = {"P0", "P1", "P2", "P3"}
ALLOWED_MATURITY = {"L0", "L1", "L2", "L3", "L4", "L5"}
ALLOWED_RISK = {"unknown", "low", "medium", "high", "blocking"}
ALLOWED_RESEARCH_ROUTES = set(RESEARCH_ROUTE_LABELS)
ACTOR = "local-owner"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(prefix, *parts):
    payload = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def target_from_snapshot(connection, target_key):
    target_key = str(target_key or "").strip()
    snapshot = build_master_pool_snapshot(connection)
    target = next(
        (item for item in snapshot["records"] if item["masterId"] == target_key),
        None,
    )
    if not target:
        raise ValueError("没有找到这条项目或发现记录，请刷新页面后重试。")
    return target


def target_columns(target):
    return {
        "project_id": target["projectId"] or None,
        "discovery_id": target["discoveryId"] or None,
        "case_id": target["caseId"] or None,
    }


def target_match(target):
    if target["projectId"]:
        return "project_id = ?", (target["projectId"],)
    if target["discoveryId"]:
        return "discovery_id = ?", (target["discoveryId"],)
    if target["caseId"]:
        return "case_id = ?", (target["caseId"],)
    raise ValueError("这条记录没有可保存的项目、发现或案例身份。")


def clean_text(value, label, maximum):
    text = str(value or "").strip()
    if len(text) > maximum:
        raise ValueError(f"{label}不能超过 {maximum} 个字符。")
    return text


def normalize_review(payload):
    classification = str(payload.get("classification", "")).strip()
    priority = str(payload.get("priority", "")).strip()
    maturity = str(payload.get("maturity", "")).strip()
    risk_level = str(payload.get("riskLevel", "")).strip()
    research_route = str(
        payload.get("researchRouteOverride", "auto")
    ).strip()
    if classification not in ALLOWED_CLASSIFICATIONS:
        raise ValueError("请选择有效的人工分类。")
    if priority not in ALLOWED_PRIORITIES:
        raise ValueError("请选择有效的复核优先级。")
    if maturity not in ALLOWED_MATURITY:
        raise ValueError("请选择有效的 L0-L5 阶段。")
    if risk_level not in ALLOWED_RISK:
        raise ValueError("请选择有效的风险等级。")
    if research_route not in ALLOWED_RESEARCH_ROUTES:
        raise ValueError("请选择有效的研究路线。")
    research_route_reason = clean_text(
        payload.get("researchRouteReason"),
        "研究路线调整原因",
        500,
    )
    if research_route != "auto" and not research_route_reason:
        raise ValueError("人工调整研究路线时必须填写原因，系统会保留历史记录。")
    return {
        "classification": classification,
        "priority": priority,
        "maturity": maturity,
        "convexitySource": clean_text(
            payload.get("convexitySource"),
            "主凸性来源",
            120,
        ),
        "riskLevel": risk_level,
        "researchRouteOverride": research_route,
        "researchRouteReason": research_route_reason,
        "identityConfirmed": bool(payload.get("identityConfirmed", False)),
        "note": clean_text(payload.get("note"), "复核备注", 3000),
    }


def supersede_active_reviews(connection, target):
    clause, parameters = target_match(target)
    connection.execute(
        f"""
        UPDATE manual_annotations
        SET status = 'superseded', updated_at = ?
        WHERE field_name = 'manual_review'
          AND status = 'active'
          AND {clause}
        """,
        (utc_now(), *parameters),
    )


def save_review(connection, target, payload):
    values = normalize_review(payload)
    now = utc_now()
    supersede_active_reviews(connection, target)
    annotation_id = stable_id(
        "annotation",
        target["masterId"],
        now,
        values["classification"],
        values["priority"],
    )
    columns = target_columns(target)
    connection.execute(
        """
        INSERT INTO manual_annotations (
          annotation_id, project_id, discovery_id, case_id, field_name,
          annotation_value_json, note, status, actor, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'manual_review', ?, ?, 'active', ?, ?, ?)
        """,
        (
            annotation_id,
            columns["project_id"],
            columns["discovery_id"],
            columns["case_id"],
            json.dumps(values, ensure_ascii=False),
            values["note"],
            ACTOR,
            now,
            now,
        ),
    )
    return {
        "message": f"{target['name']} 的人工复核已保存。",
        "annotationId": annotation_id,
    }


def withdraw_review(connection, target, payload):
    columns = target_columns(target)
    clause, parameters = target_match(target)
    active = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM manual_annotations
        WHERE field_name = 'manual_review'
          AND status = 'active'
          AND {clause}
        """,
        parameters,
    ).fetchone()[0]
    if not active:
        raise ValueError("这条记录目前没有可撤回的人工标注。")
    now = utc_now()
    connection.execute(
        f"""
        UPDATE manual_annotations
        SET status = 'withdrawn', updated_at = ?
        WHERE field_name = 'manual_review'
          AND status = 'active'
          AND {clause}
        """,
        (now, *parameters),
    )
    note = clean_text(payload.get("note"), "撤回说明", 3000)
    annotation_id = stable_id("annotation-withdrawal", target["masterId"], now)
    connection.execute(
        """
        INSERT INTO manual_annotations (
          annotation_id, project_id, discovery_id, case_id, field_name,
          annotation_value_json, note, status, actor, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'manual_review_withdrawal', ?, ?, 'active', ?, ?, ?)
        """,
        (
            annotation_id,
            columns["project_id"],
            columns["discovery_id"],
            columns["case_id"],
            json.dumps({"reason": note}, ensure_ascii=False),
            note or "用户撤回人工复核结论。",
            ACTOR,
            now,
            now,
        ),
    )
    return {
        "message": f"{target['name']} 的人工标注已撤回，历史记录仍然保留。",
        "annotationId": annotation_id,
    }


def reviewed_target(connection, target_key):
    snapshot = build_manual_review_snapshot(connection)
    target = next(
        (item for item in snapshot["targets"] if item["masterId"] == target_key),
        None,
    )
    if not target:
        raise ValueError("没有找到这条复核记录，请刷新页面后重试。")
    return target


def insert_publication(connection, target, status, payload):
    now = utc_now()
    note = clean_text(payload.get("note"), "发布说明", 3000)
    review = target.get("manualReview") or {}
    values = review.get("values") or {}
    title = target["name"]
    classification_label = target.get("manualClassificationLabel") or "未分类"
    summary = (
        note
        or values.get("note")
        or f"人工分类：{classification_label}；阶段：{values.get('maturity', '待核验')}。"
    )
    publication_id = stable_id(
        "publication",
        target["masterId"],
        status,
        now,
    )
    source_snapshot = {
        "targetKey": target["masterId"],
        "name": target["name"],
        "symbol": target["symbol"],
        "contractAddress": target["contractAddress"],
        "identityStatus": target["identityStatus"],
        "manualReview": values,
        "actor": ACTOR,
    }
    connection.execute(
        """
        INSERT INTO publication_records (
          publication_id, project_id, case_id, publication_status, visibility,
          title, summary, published_at, withdrawn_at, source_snapshot_json,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'public', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            publication_id,
            target["projectId"] or None,
            target["caseId"] or None,
            status,
            title,
            summary,
            now if status == "published" else None,
            now if status == "withdrawn" else None,
            json.dumps(source_snapshot, ensure_ascii=False),
            now,
            now,
        ),
    )
    return publication_id


def promote_target(connection, target_key, payload):
    target = reviewed_target(connection, target_key)
    if target["publicationStatus"] == "published":
        return {
            "message": f"{target['name']} 已经在凸性机会中心，无需重复升格。",
            "publicationId": target["publication"]["publication_id"],
        }
    if target["promotionBlockers"]:
        raise ValueError("暂不能升格：" + "；".join(target["promotionBlockers"]))
    publication_id = insert_publication(connection, target, "published", payload)
    return {
        "message": f"{target['name']} 已升格并进入凸性机会中心。",
        "publicationId": publication_id,
    }


def save_and_promote_target(connection, target, payload):
    was_published = target.get("publicationStatus") == "published"
    saved = save_review(connection, target, payload)
    try:
        promoted = promote_target(connection, target["masterId"], payload)
    except ValueError as error:
        raise ValueError(
            f"保存并发布没有完成：{error}。"
            "本次操作没有写入任何更改；如果只想保留标注，请点击“仅保存标注”。"
        ) from error
    return {
        "message": (
            f"{target['name']} 的人工标注已更新，发布状态继续有效。"
            if was_published
            else f"{target['name']} 的人工标注已保存，并已真实发布到凸性机会中心。"
        ),
        "annotationId": saved["annotationId"],
        "publicationId": promoted["publicationId"],
    }


def withdraw_publication(connection, target_key, payload):
    target = reviewed_target(connection, target_key)
    if target["publicationStatus"] != "published":
        raise ValueError("这条记录当前没有可撤回的机会中心发布。")
    publication_id = insert_publication(connection, target, "withdrawn", payload)
    return {
        "message": f"{target['name']} 已从凸性机会中心撤回，发布历史仍然保留。",
        "publicationId": publication_id,
    }


def rebuild_snapshots(connection):
    manual = build_manual_review_snapshot(connection)
    write_manual_review_snapshot(manual)
    write_master_pool_snapshot(build_master_pool_snapshot(connection))
    write_project_detail_snapshot(build_project_detail_snapshot(connection))
    write_pool_snapshot(
        build_pool_snapshot(connection, load_fixture(), production_only=True)
    )
    write_runtime_snapshot(connection, DEFAULT_SNAPSHOT_PATH)
    rebuild_opportunity_center_snapshot()
    rebuild_research_route_snapshot()
    rebuild_tracking_tasks_snapshot()
    return manual


def execute_manual_review_action(
    payload,
    db_path=DEFAULT_DB_PATH,
    runtime_snapshot_path=DEFAULT_SNAPSHOT_PATH,
    rebuild=True,
):
    operation = str(payload.get("operation", "")).strip()
    target_key = str(payload.get("targetKey", "")).strip()
    if operation not in {
        "save_review",
        "save_and_promote",
        "withdraw_review",
        "promote",
        "withdraw_publication",
    }:
        raise ValueError("不支持这项人工复核操作。")
    initialize_database(db_path, runtime_snapshot_path, backup=False)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        target = target_from_snapshot(connection, target_key)
        connection.execute("BEGIN IMMEDIATE")
        if operation == "save_review":
            result = save_review(connection, target, payload)
        elif operation == "save_and_promote":
            result = save_and_promote_target(connection, target, payload)
        elif operation == "withdraw_review":
            result = withdraw_review(connection, target, payload)
        elif operation == "promote":
            result = promote_target(connection, target_key, payload)
        else:
            result = withdraw_publication(connection, target_key, payload)
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"凸性数据库完整性检查失败：{integrity}")
        snapshot = (
            rebuild_snapshots(connection)
            if rebuild
            else build_manual_review_snapshot(connection)
        )
        updated = next(
            item
            for item in snapshot["targets"]
            if item["masterId"] == target_key
        )
        return {
            "status": "success",
            "operation": operation,
            "targetKey": target_key,
            **result,
            "target": {
                "name": updated["name"],
                "manualClassification": updated["manualClassification"],
                "researchRouteOverride": updated["researchRouteOverride"],
                "researchRouteReason": updated["researchRouteReason"],
                "publicationStatus": updated["publicationStatus"],
                "promotionEligible": updated["promotionEligible"],
                "promotionBlockers": updated["promotionBlockers"],
            },
            "counts": snapshot["counts"],
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="管理凸性人工复核、升格与撤回")
    parser.add_argument("--payload", required=True, help="JSON 操作载荷")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--no-rebuild", action="store_true")
    args = parser.parse_args()
    result = execute_manual_review_action(
        json.loads(args.payload),
        db_path=args.db,
        rebuild=not args.no_rebuild,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
