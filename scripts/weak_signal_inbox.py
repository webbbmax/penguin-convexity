#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from init_db import DEFAULT_DB_PATH, initialize_database


RULE_VERSION = "weak-signal-inbox-c1.6.05"
SOURCE_DEFINITION = {
    "source_id": "weak-signal-inbox-registry",
    "name": "弱线索统一收件箱",
    "source_type": "internal_registry",
    "url": "local://weak-signal-inbox",
    "access_method": "本地规则归类",
}
SOURCE_POLICIES = {
    "discovery-github-repositories": {
        "label": "GitHub 公开仓库",
        "signal_type": "code_activity",
        "signal_label": "代码活动",
        "source_tier": "public_code",
        "tier_label": "公开代码线索",
        "promotion_bias": "low",
        "proves": "发现近期活跃的公开仓库及其主题、更新时间和仓库入口。",
        "does_not_prove": "仓库名称和活跃度不证明它属于项目官方，也不证明产品采用或代币价值捕获。",
    },
    "discovery-defillama-protocols": {
        "label": "DefiLlama 协议目录",
        "signal_type": "protocol_listing",
        "signal_label": "协议目录",
        "source_tier": "independent_registry",
        "tier_label": "独立聚合目录",
        "promotion_bias": "low",
        "proves": "发现协议目录、分类、部署网络和聚合 TVL 线索。",
        "does_not_prove": "目录收录与 TVL 不证明代币归属、协议收入、价值捕获或凸性。",
    },
    "discovery-snapshot-spaces": {
        "label": "Snapshot 治理空间",
        "signal_type": "governance_activity",
        "signal_label": "治理活动",
        "source_tier": "community_governance",
        "tier_label": "社区治理线索",
        "promotion_bias": "medium",
        "proves": "发现近期存在提案活动的 Snapshot 空间。",
        "does_not_prove": "治理空间可能同名或仿冒，提案活跃不证明链上执行或资产受益。",
    },
    "discovery-cactus-organizations": {
        "label": "Cactus 治理组织",
        "signal_type": "governance_activity",
        "signal_label": "治理活动",
        "source_tier": "community_governance",
        "tier_label": "链上治理线索",
        "promotion_bias": "medium",
        "proves": "发现存在链上治理提案的组织。",
        "does_not_prove": "组织登记和提案状态不证明经济影响、代币价值捕获或价格传导。",
    },
    "discovery-robinhood-blockscout": {
        "label": "Robinhood Chain 区块浏览器",
        "signal_type": "contract_deployment",
        "signal_label": "合约部署",
        "source_tier": "chain_trace",
        "tier_label": "链上原始痕迹",
        "promotion_bias": "low",
        "proves": "发现 Robinhood Chain 上新出现或活跃的代币合约地址。",
        "does_not_prove": "合约存在不证明项目身份、可安全卖出、价值捕获或投资价值。",
    },
    "discovery-dexscreener-profiles": {
        "label": "DexScreener 代币资料",
        "signal_type": "token_profile",
        "signal_label": "代币资料",
        "source_tier": "promotional",
        "tier_label": "项目推广资料",
        "promotion_bias": "high",
        "proves": "发现项目主动提交的代币资料、链接和交易对线索。",
        "does_not_prove": "项目自填资料不证明身份真实、流动性安全、项目质量或凸性。",
    },
    "discovery-dexscreener-boosts": {
        "label": "DexScreener Boost",
        "signal_type": "paid_boost",
        "signal_label": "付费推广",
        "source_tier": "paid_promotion",
        "tier_label": "付费推广线索",
        "promotion_bias": "high",
        "proves": "发现正在购买 DexScreener 曝光的代币。",
        "does_not_prove": "付费曝光、热度和价格变化不能单独成为证据、评分加分或行动依据。",
    },
}
TRIAGE_LABELS = {
    "ready_for_corroboration": "可进入补证",
    "discovery_only": "仅供发现",
    "identity_blocked": "身份待核验",
    "conflict": "归属冲突",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(*parts):
    payload = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def parse_json(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def project_relation(project_id, relation_status):
    if relation_status in {"conflict", "rejected"}:
        return "conflict"
    if not project_id:
        return "unattributed"
    if relation_status == "verified":
        return "verified"
    if relation_status == "corroborated":
        return "corroborated"
    return "pending"


def triage_status(policy, relation_status):
    if relation_status == "conflict":
        return "conflict"
    if policy["source_tier"] in {"promotional", "paid_promotion"}:
        return "discovery_only"
    if relation_status in {"verified", "corroborated"}:
        return "ready_for_corroboration"
    return "identity_blocked"


def upgrade_requirement(policy, relation_status):
    if policy["source_tier"] in {"promotional", "paid_promotion"}:
        return "必须补充项目官方入口、项目身份和至少一个独立来源；推广或热度本身不能升级。"
    if relation_status == "conflict":
        return "先解决项目主体、资产或来源归属冲突，不得进入评分和结论。"
    if relation_status not in {"verified", "corroborated"}:
        return "先完成项目主体归属；同名、主题或符号匹配不能代替身份核验。"
    return "进入正式采集器补齐一手事实或独立证据；弱线索本身不参与评分和行动。"


def latest_case_by_project(connection):
    return {
        row["project_id"]: row["case_id"]
        for row in connection.execute(
            """
            SELECT cc.project_id, cc.case_id
            FROM candidate_cases cc
            WHERE cc.case_id = (
              SELECT newer.case_id
              FROM candidate_cases newer
              WHERE newer.project_id = cc.project_id
              ORDER BY newer.updated_at DESC, newer.case_id DESC
              LIMIT 1
            )
            """
        )
    }


def raw_event_maps(connection):
    source_runs = {}
    network_events = {}
    for row in connection.execute(
        """
        SELECT raw_event_id, source_id, ingestion_run_id, external_id, event_type
        FROM raw_events
        WHERE event_type IN ('project_source_discovery', 'network_token_discovery')
        """
    ):
        item = dict(row)
        if item["event_type"] == "project_source_discovery":
            source_runs[(item["source_id"], item["ingestion_run_id"])] = item[
                "raw_event_id"
            ]
        else:
            network_events[
                (item["ingestion_run_id"], item["external_id"])
            ] = item["raw_event_id"]
    return source_runs, network_events


def source_discovery_summary(row, evidence):
    parts = [row["category"], row["attribution_reason"]]
    if row["source_id"] == "discovery-defillama-protocols":
        if evidence.get("tvlUsd") is not None:
            parts.append(f"聚合 TVL ${float(evidence['tvlUsd']):,.0f}")
        chains = evidence.get("chains") or []
        if chains:
            parts.append(f"部署网络 {len(chains)} 个")
    elif row["source_id"] == "discovery-github-repositories":
        description = evidence.get("description") or evidence.get("summary")
        if description:
            parts.append(str(description))
    elif row["source_id"] in {
        "discovery-snapshot-spaces",
        "discovery-cactus-organizations",
    }:
        proposal_count = (
            evidence.get("proposalCount")
            or evidence.get("proposals")
            or evidence.get("proposal_count")
        )
        if proposal_count:
            parts.append(f"提案记录 {proposal_count}")
    return "；".join(str(part).strip() for part in parts if str(part or "").strip())


def compile_source_discovery_signals(connection, source_run_events, case_by_project):
    records = []
    for row in connection.execute(
        """
        SELECT *
        FROM source_discoveries
        WHERE status = 'active'
        ORDER BY source_id, source_discovery_id
        """
    ):
        item = dict(row)
        policy = SOURCE_POLICIES.get(item["source_id"])
        if not policy:
            continue
        relation = project_relation(
            item["matched_project_id"],
            item["project_identity_status"],
        )
        evidence = parse_json(item["evidence_json"], {})
        identity_key = (
            f"source-discovery:{item['source_id']}:{item['source_discovery_id']}"
        )
        records.append(
            {
                "signal_identity_key": identity_key,
                "source_id": item["source_id"],
                "source_record_type": "source_discovery",
                "source_record_id": item["source_discovery_id"],
                "project_id": item["matched_project_id"],
                "case_id": case_by_project.get(item["matched_project_id"]),
                "raw_event_id": source_run_events.get(
                    (item["source_id"], item["last_run_id"])
                ),
                "signal_type": policy["signal_type"],
                "source_tier": policy["source_tier"],
                "promotion_bias": policy["promotion_bias"],
                "project_relation_status": relation,
                "triage_status": triage_status(policy, relation),
                "title": item["canonical_name"],
                "summary": source_discovery_summary(item, evidence),
                "source_url": item["source_url"]
                or item["repository_url"]
                or item["website_url"],
                "upgrade_requirement": upgrade_requirement(policy, relation),
                "observed_at": item["last_seen_at"],
                "metadata_json": json.dumps(
                    {
                        "category": item["category"],
                        "websiteUrl": item["website_url"],
                        "repositoryUrl": item["repository_url"],
                        "socialUrl": item["social_url"],
                        "attributionConfidence": item[
                            "attribution_confidence"
                        ],
                        "projectIdentityStatus": item[
                            "project_identity_status"
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "rule_version": RULE_VERSION,
            }
        )
    return records


def compile_network_signals(connection, network_events, case_by_project):
    records = []
    rows = connection.execute(
        """
        SELECT nd.*, n.name AS network_name,
               ir.resolution_status, ir.matched_project_id,
               ir.promoted_project_id, ir.promoted_case_id
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
        ORDER BY nd.discovery_id
        """
    )
    for row in rows:
        item = dict(row)
        source_ids = parse_json(item["source_ids_json"], [])
        source_urls = parse_json(item["source_urls_json"], [])
        project_id = item["promoted_project_id"] or item["matched_project_id"]
        relation = project_relation(project_id, item["resolution_status"])
        for index, source_id in enumerate(source_ids):
            policy = SOURCE_POLICIES.get(source_id)
            if not policy:
                continue
            identity_key = f"network-discovery:{source_id}:{item['discovery_id']}"
            source_url = source_urls[index] if index < len(source_urls) else ""
            raw_external_id = f"{item['last_run_id']}:{item['discovery_id']}"
            title = item["token_name"] or item["symbol"] or item["contract_address"]
            summary_parts = [
                item["network_name"],
                item["symbol"],
                item["status_reason"],
            ]
            records.append(
                {
                    "signal_identity_key": identity_key,
                    "source_id": source_id,
                    "source_record_type": "network_discovery",
                    "source_record_id": item["discovery_id"],
                    "project_id": project_id,
                    "case_id": item["promoted_case_id"]
                    or case_by_project.get(project_id),
                    "raw_event_id": network_events.get(
                        (item["last_run_id"], raw_external_id)
                    ),
                    "signal_type": policy["signal_type"],
                    "source_tier": policy["source_tier"],
                    "promotion_bias": policy["promotion_bias"],
                    "project_relation_status": relation,
                    "triage_status": triage_status(policy, relation),
                    "title": title,
                    "summary": "；".join(
                        str(part).strip()
                        for part in summary_parts
                        if str(part or "").strip()
                    ),
                    "source_url": source_url,
                    "upgrade_requirement": upgrade_requirement(policy, relation),
                    "observed_at": item["last_seen_at"],
                    "metadata_json": json.dumps(
                        {
                            "networkId": item["network_id"],
                            "networkName": item["network_name"],
                            "contractAddress": item["contract_address"],
                            "symbol": item["symbol"],
                            "queueStatus": item["queue_status"],
                            "liquidityUsd": item["liquidity_usd"],
                            "marketCapUsd": item["market_cap_usd"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "rule_version": RULE_VERSION,
                }
            )
    return records


def compile_weak_signals(connection):
    source_run_events, network_events = raw_event_maps(connection)
    case_by_project = latest_case_by_project(connection)
    records = [
        *compile_source_discovery_signals(
            connection,
            source_run_events,
            case_by_project,
        ),
        *compile_network_signals(
            connection,
            network_events,
            case_by_project,
        ),
    ]
    return sorted(
        records,
        key=lambda item: (
            item["observed_at"],
            item["signal_identity_key"],
        ),
        reverse=True,
    )


def comparable(record):
    fields = (
        "source_id",
        "source_record_type",
        "source_record_id",
        "project_id",
        "case_id",
        "raw_event_id",
        "signal_type",
        "source_tier",
        "promotion_bias",
        "project_relation_status",
        "triage_status",
        "title",
        "summary",
        "source_url",
        "upgrade_requirement",
        "observed_at",
        "metadata_json",
        "rule_version",
    )
    return {field: record.get(field) for field in fields}


def persist_weak_signals(connection, generated_at=None):
    generated_at = generated_at or utc_now()
    compiled = compile_weak_signals(connection)
    desired = {
        item["signal_identity_key"]: item for item in compiled
    }
    current = {
        row["signal_identity_key"]: dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM weak_signal_inbox
            WHERE publication_status = 'published'
            """
        )
    }
    inserted = 0
    changed = 0
    unchanged = 0
    retired = 0
    for identity_key, existing in current.items():
        candidate = desired.get(identity_key)
        if candidate and comparable(existing) == comparable(candidate):
            unchanged += 1
            continue
        connection.execute(
            """
            UPDATE weak_signal_inbox
            SET publication_status = 'superseded'
            WHERE weak_signal_id = ?
            """,
            (existing["weak_signal_id"],),
        )
        if candidate:
            changed += 1
        else:
            retired += 1
    for identity_key, candidate in desired.items():
        existing = current.get(identity_key)
        if existing and comparable(existing) == comparable(candidate):
            continue
        fingerprint = hashlib.sha256(
            json.dumps(
                comparable(candidate),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO weak_signal_inbox (
              weak_signal_id, signal_identity_key, source_id,
              source_record_type, source_record_id, project_id, case_id,
              raw_event_id, signal_type, source_tier, promotion_bias,
              project_relation_status, triage_status, title, summary,
              source_url, upgrade_requirement, observed_at, metadata_json,
              generated_at, publication_status, rule_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, 'published', ?)
            """,
            (
                f"weak-signal-{stable_id(identity_key, fingerprint, generated_at)}",
                identity_key,
                candidate["source_id"],
                candidate["source_record_type"],
                candidate["source_record_id"],
                candidate["project_id"],
                candidate["case_id"],
                candidate["raw_event_id"],
                candidate["signal_type"],
                candidate["source_tier"],
                candidate["promotion_bias"],
                candidate["project_relation_status"],
                candidate["triage_status"],
                candidate["title"],
                candidate["summary"],
                candidate["source_url"],
                candidate["upgrade_requirement"],
                candidate["observed_at"],
                candidate["metadata_json"],
                generated_at,
                candidate["rule_version"],
            ),
        )
        inserted += 1
    published = [
        dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM weak_signal_inbox
            WHERE publication_status = 'published'
            """
        )
    ]
    triage_counts = Counter(item["triage_status"] for item in published)
    source_counts = Counter(item["source_id"] for item in published)
    signal_type_counts = Counter(item["signal_type"] for item in published)
    projects = {
        item["project_id"] for item in published if item["project_id"]
    }
    return {
        "version": "C1.6-06",
        "ruleVersion": RULE_VERSION,
        "generatedAt": generated_at,
        "signalsPublished": len(published),
        "recordsInserted": inserted,
        "changedSignals": changed,
        "unchangedSignals": unchanged,
        "retiredSignals": retired,
        "projectsLinked": len(projects),
        "triageCounts": dict(triage_counts),
        "sourceCounts": dict(source_counts),
        "signalTypeCounts": dict(signal_type_counts),
        "errors": [],
    }


def latest_weak_signals(connection, project_id=None):
    parameters = []
    project_filter = ""
    if project_id:
        project_filter = "AND signal.project_id = ?"
        parameters.append(project_id)
    return [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT signal.*, source.name AS source_name,
                   project.canonical_name AS project_name
            FROM weak_signal_inbox signal
            JOIN sources source ON source.source_id = signal.source_id
            LEFT JOIN projects project ON project.project_id = signal.project_id
            WHERE signal.publication_status = 'published'
              {project_filter}
            ORDER BY signal.observed_at DESC, signal.weak_signal_id DESC
            """,
            parameters,
        )
    ]


def main():
    parser = argparse.ArgumentParser(description="重建凸性弱线索统一收件箱")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    initialize_database(args.db, backup=False)
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        result = persist_weak_signals(connection)
        connection.commit()
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
