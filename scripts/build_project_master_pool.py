#!/usr/bin/env python3
import argparse
import calendar
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH
from project_identity_aliases import (
    alias_owner_index,
    normalize_alias,
    sync_project_identity_aliases,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "project-master-pool-snapshot.js"
LIFECYCLE_FIXTURE_PATH = PROJECT_ROOT / "fixtures" / "project-lifecycle-v1.json"
LIFECYCLE_CACHE_PATH = PROJECT_ROOT / "data" / "project-lifecycle-cache-v1.json"

WORKFLOW_LABELS = {
    "shadow_signal": "影子信号",
    "identity_pending": "身份待核验",
    "tradeability_pending": "交易性待核验",
    "active_embryo": "正式胚胎",
    "priority_watch": "重点观察",
    "extreme_test": "极限试仓",
    "trial_ready": "可试仓",
    "igniting": "正在点火",
    "odds_decay": "赔率衰减",
    "invalidated": "逻辑失效",
    "transferred_l5": "转入 L5 管理",
    "archived": "已归档",
}

QUEUE_LABELS = {
    "preflight_pass": "技术预检通过",
    "identity_pending": "身份待核验",
    "existing_asset": "已有正式资产",
    "rejected": "已排除",
    "promoted": "已升格",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_list(value):
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def load_json(path, fallback):
    path = Path(path)
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def add_months(value, months):
    target_month = value.month - 1 + months
    year = value.year + target_month // 12
    month = target_month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def age_label(age_days):
    if age_days is None:
        return "时间待核验"
    if age_days < 183:
        months = max(0, age_days // 30)
        return f"约{months}个月"
    if age_days < 730:
        return f"约{age_days / 365:.1f}年"
    return f"约{age_days // 365}年"


def lifecycle_context(
    connection=None,
    fixture_path=LIFECYCLE_FIXTURE_PATH,
    cache_path=LIFECYCLE_CACHE_PATH,
):
    fixture = load_json(
        fixture_path,
        {"thresholds": {"earlyMonths": 6, "ogYears": 5}, "projects": {}},
    )
    cache = load_json(cache_path, {"projects": {}})
    return {
        "thresholds": fixture.get("thresholds")
        or {"earlyMonths": 6, "ogYears": 5},
        "fixture": fixture.get("projects") or {},
        "cache": cache.get("projects") or {},
        "aliasOwnerByValue": (
            alias_owner_index(connection) if connection is not None else {}
        ),
    }


def lifecycle_fields(profile, thresholds, *, provisional=False):
    today = datetime.now(timezone.utc).date()
    launch_date = parse_date(profile.get("launchDate"))
    date_status = str(profile.get("dateStatus") or "").strip()
    lower_bound_only = bool(profile.get("lowerBoundOnly"))
    early_months = int(thresholds.get("earlyMonths") or 6)
    og_years = int(thresholds.get("ogYears") or 5)
    early_cutoff = add_months(today, -early_months)
    og_cutoff = add_months(today, -(og_years * 12))

    if not launch_date:
        bucket = "other"
        bucket_label = "潜力项目"
        reason = "项目创建时间待核验，暂不进入早期项目或OG项目。"
        auto_move_at = ""
        age_days = None
        date_status = "pending"
    else:
        age_days = (today - launch_date).days
        if lower_bound_only:
            bucket = "other"
            bucket_label = "潜力项目"
            reason = (
                f"至少从{launch_date.isoformat()}已有市场记录；"
                "精确创建时间待补，暂不认定为OG项目。"
            )
            auto_move_at = ""
            date_status = "lower_bound"
        elif launch_date > early_cutoff:
            bucket = "early"
            bucket_label = "早期项目"
            auto_move_at = add_months(launch_date, early_months).isoformat()
            reason = (
                f"公开启动未满{early_months}个月；"
                f"到{auto_move_at}自动转入潜力项目。"
            )
        elif launch_date <= og_cutoff:
            bucket = "og"
            bucket_label = "OG项目"
            auto_move_at = ""
            reason = f"已存活至少{og_years}年，进入OG项目队列。"
        else:
            bucket = "other"
            bucket_label = "潜力项目"
            auto_move_at = add_months(launch_date, og_years * 12).isoformat()
            reason = (
                f"已超过{early_months}个月且未满{og_years}年；"
                f"到{auto_move_at}自动进入OG项目。"
            )
        if provisional and date_status == "":
            date_status = "provisional"

    return {
        "lifecycleBucket": bucket,
        "lifecycleLabel": bucket_label,
        "lifecycleDate": launch_date.isoformat() if launch_date else "",
        "lifecycleDateStatus": date_status or "verified",
        "lifecycleDateBasis": str(profile.get("dateBasis") or ""),
        "lifecycleSourceName": str(profile.get("sourceName") or ""),
        "lifecycleSourceUrl": str(profile.get("sourceUrl") or ""),
        "lifecycleAgeDays": age_days,
        "lifecycleAgeLabel": age_label(age_days),
        "lifecycleAutoMoveAt": auto_move_at,
        "lifecycleReason": reason,
    }


def project_profile(profiles, project_id, context):
    exact = profiles.get(project_id)
    if exact:
        return exact
    owners = context.get("aliasOwnerByValue") or {}
    for legacy_key, profile in profiles.items():
        normalized = normalize_alias(legacy_key)
        if normalized and owners.get(normalized) == project_id:
            return profile
    return {}


def project_lifecycle(project_id, context):
    explicit = project_profile(context["fixture"], project_id, context)
    if explicit:
        return lifecycle_fields(explicit, context["thresholds"])
    cached = project_profile(context["cache"], project_id, context)
    if cached.get("status") == "available" and cached.get("earliestMarketDate"):
        return lifecycle_fields(
            {
                "launchDate": cached["earliestMarketDate"],
                "dateStatus": (
                    "lower_bound" if cached.get("lowerBoundOnly") else "market_history"
                ),
                "dateBasis": (
                    "至少从该日已有CoinGecko市场记录"
                    if cached.get("lowerBoundOnly")
                    else "CoinGecko近365日首个市场记录"
                ),
                "sourceName": cached.get("sourceName") or "CoinGecko",
                "sourceUrl": cached.get("sourceUrl") or "",
                "lowerBoundOnly": cached.get("lowerBoundOnly"),
            },
            context["thresholds"],
        )
    return lifecycle_fields({}, context["thresholds"])


def discovery_lifecycle(first_seen_at, context, source_url=""):
    launch_date = parse_date(first_seen_at)
    return lifecycle_fields(
        {
            "launchDate": "",
            "dateStatus": "pending",
            "dateBasis": (
                f"系统于{launch_date.isoformat()}首次收录；"
                "该日期不是项目启动时间，不能据此归为早期项目"
                if launch_date
                else "系统收录时间待补；不能据此推测项目启动时间"
            ),
            "sourceName": "链上发现",
            "sourceUrl": source_url,
        },
        context["thresholds"],
    )


def latest_publication(connection, project_id=None, case_id=None):
    clauses = []
    values = []
    if project_id:
        clauses.append("project_id = ?")
        values.append(project_id)
    if case_id:
        clauses.append("case_id = ?")
        values.append(case_id)
    if not clauses:
        return None
    row = connection.execute(
        f"""
        SELECT *
        FROM publication_records
        WHERE {" OR ".join(clauses)}
        ORDER BY updated_at DESC, publication_id DESC
        LIMIT 1
        """,
        values,
    ).fetchone()
    return dict(row) if row else None


def annotation_count(connection, project_id=None, discovery_id=None, case_id=None):
    clauses = ["status = 'active'", "field_name = 'manual_review'"]
    values = []
    for field, value in (
        ("project_id", project_id),
        ("discovery_id", discovery_id),
        ("case_id", case_id),
    ):
        if value:
            clauses.append(f"{field} = ?")
            values.append(value)
    return connection.execute(
        f"SELECT COUNT(*) FROM manual_annotations WHERE {' AND '.join(clauses)}",
        values,
    ).fetchone()[0]


def empty_market_fields():
    return {
        "marketDataStatus": "missing",
        "marketAssetId": "",
        "marketSymbol": "",
        "priceUsd": None,
        "marketCapUsd": None,
        "fdvUsd": None,
        "liquidityUsd": None,
        "volume24hUsd": None,
        "marketObservedAt": "",
        "marketSourceName": "",
    }


def latest_project_market(connection, project_id):
    row = connection.execute(
        """
        SELECT ms.*, a.symbol, s.name AS source_name
        FROM market_snapshots ms
        JOIN assets a ON a.asset_id = ms.asset_id
        LEFT JOIN sources s ON s.source_id = ms.data_source_id
        WHERE a.project_id = ?
        ORDER BY ms.observed_at DESC, ms.snapshot_id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if not row:
        return empty_market_fields()
    market = dict(row)
    has_market_data = any(
        market[field] is not None
        for field in (
            "price_usd",
            "market_cap_usd",
            "fdv_usd",
            "liquidity_usd",
            "volume_24h_usd",
        )
    )
    return {
        "marketDataStatus": "available" if has_market_data else "missing",
        "marketAssetId": market["asset_id"],
        "marketSymbol": market["symbol"],
        "priceUsd": market["price_usd"],
        "marketCapUsd": market["market_cap_usd"],
        "fdvUsd": market["fdv_usd"],
        "liquidityUsd": market["liquidity_usd"],
        "volume24hUsd": market["volume_24h_usd"],
        "marketObservedAt": market["observed_at"],
        "marketSourceName": market["source_name"] or market["data_source_id"] or "",
    }


def project_records(connection, lifecycle):
    records = []
    for project in connection.execute(
        "SELECT * FROM projects ORDER BY canonical_name COLLATE NOCASE, project_id"
    ):
        project = dict(project)
        latest_case_row = connection.execute(
            """
            SELECT *
            FROM candidate_cases
            WHERE project_id = ?
            ORDER BY updated_at DESC, case_id DESC
            LIMIT 1
            """,
            (project["project_id"],),
        ).fetchone()
        latest_case = dict(latest_case_row) if latest_case_row else None
        assets = [
            dict(row)
            for row in connection.execute(
                """
                SELECT asset_id, symbol, chain, contract_address, asset_type,
                       capture_grade, identity_status
                FROM assets
                WHERE project_id = ?
                ORDER BY symbol, asset_id
                """,
                (project["project_id"],),
            )
        ]
        case_count = connection.execute(
            "SELECT COUNT(*) FROM candidate_cases WHERE project_id = ?",
            (project["project_id"],),
        ).fetchone()[0]
        evidence_count = connection.execute(
            "SELECT COUNT(*) FROM evidence_items WHERE project_id = ?",
            (project["project_id"],),
        ).fetchone()[0]
        publication = latest_publication(
            connection,
            project_id=project["project_id"],
            case_id=latest_case["case_id"] if latest_case else None,
        )
        market_fields = latest_project_market(connection, project["project_id"])
        lifecycle_data = project_lifecycle(project["project_id"], lifecycle)
        if project["identity_status"] == "rejected":
            pool_status = "rejected"
        elif publication and publication["publication_status"] == "published":
            pool_status = "published"
        elif project["identity_status"] != "verified":
            pool_status = "identity_pending"
        else:
            pool_status = "candidate"
        records.append(
            {
                "masterId": f"project:{project['project_id']}",
                "recordType": "project",
                "projectId": project["project_id"],
                "discoveryId": "",
                "name": project["canonical_name"],
                "symbol": assets[0]["symbol"] if assets else "",
                "networkId": "",
                "networkName": " / ".join(
                    sorted({asset["chain"] for asset in assets if asset["chain"]})
                ),
                "contractAddress": next(
                    (
                        asset["contract_address"]
                        for asset in assets
                        if asset["contract_address"]
                    ),
                    "",
                ),
                "identityStatus": project["identity_status"],
                "poolStatus": pool_status,
                "statusLabel": (
                    WORKFLOW_LABELS.get(latest_case["workflow_state"], latest_case["workflow_state"])
                    if latest_case
                    else "尚未建案"
                ),
                "statusReason": (
                    latest_case["current_thesis"]
                    if latest_case and latest_case["current_thesis"]
                    else "项目主体已进入项目队列，等待后续研究或身份补齐。"
                ),
                "maturityLevel": latest_case["maturity_level"] if latest_case else "",
                "riskLevel": latest_case["risk_level"] if latest_case else "unknown",
                "remainingConvexity": (
                    latest_case["remaining_convexity"] if latest_case else "unknown"
                ),
                "actionStage": latest_case["action_stage"] if latest_case else "只观察",
                "convexitySource": latest_case["convexity_source"] if latest_case else "",
                "caseId": latest_case["case_id"] if latest_case else "",
                "caseCount": case_count,
                "assetCount": len(assets),
                "assets": assets,
                "sourceIds": [],
                "sourceUrls": [],
                "evidenceCount": evidence_count,
                "annotationCount": annotation_count(
                    connection,
                    project_id=project["project_id"],
                    case_id=latest_case["case_id"] if latest_case else None,
                ),
                "publicationStatus": (
                    publication["publication_status"] if publication else "not_created"
                ),
                "lastSeenAt": project["updated_at"],
                **lifecycle_data,
                **market_fields,
            }
        )
    return records


def discovery_records(connection, lifecycle):
    records = []
    for row in connection.execute(
        """
        SELECT nd.*, n.name AS network_name,
               ir.matched_project_id, ir.promoted_project_id,
               ir.resolution_status AS identity_resolution_status,
               ir.reason AS identity_reason
        FROM network_discoveries nd
        JOIN networks n ON n.network_id = nd.network_id
        LEFT JOIN discovery_identity_reviews ir
          ON ir.identity_review_id = (
            SELECT newer.identity_review_id
            FROM discovery_identity_reviews newer
            WHERE newer.discovery_id = nd.discovery_id
            ORDER BY newer.reviewed_at DESC, newer.identity_review_id DESC
            LIMIT 1
          )
        ORDER BY nd.last_seen_at DESC, nd.discovery_id
        """
    ):
        item = dict(row)
        if item["matched_project_id"] or item["promoted_project_id"]:
            continue
        pool_status = {
            "rejected": "rejected",
            "preflight_pass": "candidate",
            "existing_asset": "candidate",
            "promoted": "candidate",
        }.get(item["queue_status"], "identity_pending")
        source_ids = json_list(item["source_ids_json"])
        source_urls = json_list(item["source_urls_json"])
        lifecycle_data = discovery_lifecycle(
            item["first_seen_at"],
            lifecycle,
            next((url for url in source_urls if str(url).startswith("http")), ""),
        )
        has_market_data = any(
            item[field] is not None
            for field in (
                "price_usd",
                "market_cap_usd",
                "liquidity_usd",
                "volume_24h_usd",
            )
        )
        records.append(
            {
                "masterId": f"discovery:{item['discovery_id']}",
                "recordType": "discovery",
                "projectId": "",
                "discoveryId": item["discovery_id"],
                "name": item["token_name"] or item["symbol"] or "未命名代币",
                "symbol": item["symbol"],
                "networkId": item["network_id"],
                "networkName": item["network_name"],
                "contractAddress": item["contract_address"],
                "identityStatus": (
                    item["identity_resolution_status"] or "pending"
                ),
                "poolStatus": pool_status,
                "statusLabel": QUEUE_LABELS.get(
                    item["queue_status"], item["queue_status"]
                ),
                "statusReason": (
                    item["identity_reason"]
                    or item["status_reason"]
                    or "等待项目主体身份复核。"
                ),
                "maturityLevel": "",
                "riskLevel": item["contract_risk"],
                "remainingConvexity": "unknown",
                "actionStage": "只观察",
                "convexitySource": "",
                "caseId": "",
                "caseCount": 0,
                "assetCount": 0,
                "assets": [],
                "sourceIds": source_ids,
                "sourceUrls": source_urls,
                "evidenceCount": len(json_list(item["evidence_json"])),
                "annotationCount": annotation_count(
                    connection, discovery_id=item["discovery_id"]
                ),
                "publicationStatus": "not_created",
                "lastSeenAt": item["last_seen_at"],
                **lifecycle_data,
                "marketDataStatus": "available" if has_market_data else "missing",
                "marketAssetId": "",
                "marketSymbol": item["symbol"],
                "priceUsd": item["price_usd"],
                "marketCapUsd": item["market_cap_usd"],
                "fdvUsd": None,
                "liquidityUsd": item["liquidity_usd"],
                "volume24hUsd": item["volume_24h_usd"],
                "marketObservedAt": item["last_seen_at"] if has_market_data else "",
                "marketSourceName": "、".join(source_ids),
            }
        )
    return records


def scan_summaries(connection):
    latest_run_row = connection.execute(
        """
        SELECT run_id, MAX(observed_at) AS observed_at
        FROM scan_results
        GROUP BY run_id
        ORDER BY observed_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not latest_run_row:
        return {"latestRunId": "", "observedAt": "", "rows": []}
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT sr.network_id, n.name AS network_name, sr.source_id,
                   s.name AS source_name, sr.result_status,
                   COUNT(*) AS result_count
            FROM scan_results sr
            JOIN networks n ON n.network_id = sr.network_id
            JOIN sources s ON s.source_id = sr.source_id
            WHERE sr.run_id = ?
            GROUP BY sr.network_id, n.name, sr.source_id, s.name, sr.result_status
            ORDER BY n.discovery_priority, s.name, sr.result_status
            """,
            (latest_run_row["run_id"],),
        )
    ]
    return {
        "latestRunId": latest_run_row["run_id"],
        "observedAt": latest_run_row["observed_at"],
        "rows": rows,
    }


def build_master_pool_snapshot(connection):
    lifecycle = lifecycle_context(connection)
    records = project_records(connection, lifecycle) + discovery_records(
        connection, lifecycle
    )
    records.sort(
        key=lambda item: (
            {"published": 0, "candidate": 1, "identity_pending": 2, "rejected": 3}.get(
                item["poolStatus"], 4
            ),
            item["name"].casefold(),
        )
    )
    counts = {
        "total": len(records),
        "projects": sum(item["recordType"] == "project" for item in records),
        "discoveries": sum(item["recordType"] == "discovery" for item in records),
        "identityPending": sum(
            item["poolStatus"] == "identity_pending" for item in records
        ),
        "published": sum(item["publicationStatus"] == "published" for item in records),
        "annotations": sum(item["annotationCount"] for item in records),
        "early": sum(item["lifecycleBucket"] == "early" for item in records),
        "og": sum(item["lifecycleBucket"] == "og" for item in records),
        "other": sum(item["lifecycleBucket"] == "other" for item in records),
        "lifecyclePending": sum(
            item["lifecycleDateStatus"] == "pending" for item in records
        ),
    }
    return {
        "product": "凸性机会中心",
        "dataModelVersion": "v6",
        "generatedAt": utc_now(),
        "noQuotaPolicy": "项目队列、链上扫描和身份复核不设置项目数量上限；排序只改变先后，不隐藏符合条件的记录。",
        "publicationBoundary": "项目队列保留待核验与排除记录。只有独立发布记录达到 published 状态，才进入未来大众展示。",
        "lifecyclePolicy": {
            "early": f"公开启动未满{lifecycle['thresholds']['earlyMonths']}个月；到期自动转入潜力项目。",
            "og": f"已存活至少{lifecycle['thresholds']['ogYears']}年；默认按创建时间从早到晚排列。",
            "other": f"存活{lifecycle['thresholds']['earlyMonths']}个月至{lifecycle['thresholds']['ogYears']}年，或创建时间仍待核验。",
            "dateBoundary": "项目收录时间不等于项目创建时间；缺少可靠日期时不推测为早期或OG项目。",
        },
        "counts": counts,
        "scanSummary": scan_summaries(connection),
        "records": records,
    }


def write_master_pool_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        "window.PENGUIN_CONVEXITY_MASTER_POOL = "
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return path


def rebuild_master_pool_snapshot(
    db_path=DEFAULT_DB_PATH,
    snapshot_path=DEFAULT_SNAPSHOT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        sync_project_identity_aliases(connection)
        connection.commit()
        snapshot = build_master_pool_snapshot(connection)
        write_master_pool_snapshot(snapshot, snapshot_path)
        return snapshot
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成凸性机会中心项目队列快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_master_pool_snapshot(args.db, args.snapshot)
    print(json.dumps(snapshot["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
