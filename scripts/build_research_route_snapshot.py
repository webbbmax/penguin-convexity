#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
DEFAULT_MASTER_PATH = APP_ROOT / "project-master-pool-snapshot.js"
DEFAULT_DETAIL_PATH = APP_ROOT / "project-detail-snapshot.js"
DEFAULT_MANUAL_PATH = APP_ROOT / "manual-review-snapshot.js"
DEFAULT_OPPORTUNITY_PATH = APP_ROOT / "opportunity-center-snapshot.js"
DEFAULT_OUTPUT_PATH = APP_ROOT / "research-route-snapshot.js"

MASTER_PREFIX = "window.PENGUIN_CONVEXITY_MASTER_POOL = "
DETAIL_PREFIX = "window.PENGUIN_CONVEXITY_PROJECT_DETAILS = "
MANUAL_PREFIX = "window.PENGUIN_CONVEXITY_MANUAL_REVIEW = "
OPPORTUNITY_PREFIX = "window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER = "

ROUTES = [
    {
        "id": "startup",
        "label": "早期项目",
        "shortLabel": "早期项目",
        "primaryFocus": "先确认项目真实存在、资产可识别、产品可理解、风险可退出。",
        "definition": "公开启动未满6个月；满6个月后自动转入潜力项目。",
    },
    {
        "id": "mature",
        "label": "OG项目",
        "shortLabel": "OG项目",
        "primaryFocus": "优先寻找新闻发布前的治理、代码、部署、产品、链上、监管和机构事实。",
        "definition": "已存活至少5年，默认按创建时间从早到晚排列。",
    },
    {
        "id": "hybrid",
        "label": "潜力项目",
        "shortLabel": "潜力项目",
        "primaryFocus": "同时补齐基础档案与前置信号，证据足够后再重新分流。",
        "definition": "存活6个月至5年，或创建时间仍待核验的项目。",
    },
]
ROUTE_BY_ID = {item["id"]: item for item in ROUTES}
ALLOWED_OVERRIDES = {"auto", *ROUTE_BY_ID}

STARTUP_CHECKS = [
    ("officialWebsite", "官网"),
    ("officialX", "官方 X"),
    ("github", "GitHub"),
    ("productDocs", "产品文档"),
    ("tokenomics", "代币经济"),
    ("contractNetwork", "合约与所在链"),
    ("liquidity", "流动性"),
    ("team", "团队与组织"),
    ("audit", "审计"),
]
MATURE_CHECKS = [
    ("governanceProposal", "治理提案"),
    ("githubRelease", "GitHub 发布/关键提交"),
    ("contractDeployment", "新合约部署"),
    ("productUpgrade", "产品升级"),
    ("onchainData", "链上采用数据"),
    ("regulatory", "监管进展"),
    ("institutional", "机构动作"),
    ("tokenomicsAdjustment", "代币经济调整"),
]
HYBRID_CHECKS = [
    ("officialWebsite", "官网"),
    ("github", "GitHub"),
    ("contractNetwork", "合约与所在链"),
    ("liquidity", "流动性"),
    ("governanceProposal", "治理提案"),
    ("githubRelease", "GitHub 发布/关键提交"),
    ("productUpgrade", "产品升级"),
    ("onchainData", "链上采用数据"),
]


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_js_payload(path, prefix):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError(f"无法识别快照格式：{path}")
    return json.loads(text[len(prefix):-1])


def market_cap_label(value):
    if value is None:
        return "市值待补"
    value = float(value)
    if value >= 1_000_000_000:
        return f"市值约{value / 1_000_000_000:.2f}亿美元"
    if value >= 1_000_000:
        return f"市值约{value / 1_000_000:.1f}百万美元"
    return f"市值约{value:,.0f}美元"


def automatic_route(record):
    lifecycle_bucket = str(record.get("lifecycleBucket") or "other")
    lifecycle_date = str(record.get("lifecycleDate") or "")
    lifecycle_status = str(record.get("lifecycleDateStatus") or "pending")
    lifecycle_age = str(record.get("lifecycleAgeLabel") or "时间待核验")
    lifecycle_reason = str(record.get("lifecycleReason") or "")
    market_cap = record.get("marketCapUsd")
    route_id = {
        "early": "startup",
        "og": "mature",
        "other": "hybrid",
    }.get(lifecycle_bucket, "hybrid")
    signals = [
        f"生命周期：{lifecycle_age}",
        f"启动日期：{lifecycle_date or '待核验'}",
        market_cap_label(market_cap),
    ]
    return {
        "routeId": route_id,
        "confidence": (
            "high"
            if lifecycle_status == "verified"
            else "medium"
            if lifecycle_status in {"market_history", "provisional"}
            else "low"
        ),
        "reason": lifecycle_reason or "项目创建时间待核验，暂归潜力项目。",
        "signals": signals,
    }


def text_values(value):
    if value is None:
        return []
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(text_values(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(text_values(item))
        return result
    return [str(value)]


def contains_any(text, keywords):
    lowered = text.casefold()
    return any(keyword.casefold() in lowered for keyword in keywords)


def http_url(value):
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


def fact_item(
    field_id,
    label,
    available=False,
    evidence="",
    source_url="",
    source_name="",
    observed_at="",
    fact_boundary="",
):
    return {
        "id": field_id,
        "label": label,
        "status": "available" if available else "pending",
        "evidence": str(evidence or "").strip() if available else "",
        "sourceUrl": http_url(source_url) if available else "",
        "sourceName": str(source_name or "").strip() if available else "",
        "observedAt": str(observed_at or "").strip() if available else "",
        "factBoundary": str(fact_boundary or "").strip() if available else "",
    }


def evidence_match(evidence, *, keywords=(), evidence_types=()):
    type_set = {item.casefold() for item in evidence_types}
    type_matches = [
        item
        for item in evidence
        if str(item.get("evidence_type") or "").casefold() in type_set
    ]
    if type_matches:
        return max(
            type_matches,
            key=lambda item: str(
                item.get("observed_at") or item.get("created_at") or ""
            ),
        )

    keyword_matches = []
    for item in evidence:
        searchable = "\n".join(
            text_values(
                {
                    "summary": item.get("summary"),
                    "url": item.get("source_url"),
                    "source": item.get("source_name") or item.get("source_id"),
                    "type": item.get("evidence_type"),
                }
            )
        )
        if keywords and contains_any(searchable, keywords):
            keyword_matches.append(item)
    return max(
        keyword_matches,
        key=lambda item: str(item.get("observed_at") or item.get("created_at") or ""),
        default=None,
    )


def fact_from_evidence(field_id, label, item, fallback):
    if not item:
        return fact_item(field_id, label)
    return fact_item(
        field_id,
        label,
        True,
        item.get("summary") or fallback,
        item.get("source_url"),
        item.get("source_name") or item.get("source_id"),
        item.get("observed_at"),
        item.get("fact_boundary"),
    )


def research_facts(record, detail):
    project = detail.get("project") or {}
    discovery = detail.get("discovery") or {}
    evidence = detail.get("evidence") or []
    assets = detail.get("assets") or []
    contracts = [
        contract
        for asset in assets
        for contract in (asset.get("contracts") or [])
    ]
    markets = [
        (asset.get("latestMarket") or {}, asset)
        for asset in assets
        if (asset.get("latestMarket") or {}).get("liquidity_usd") is not None
        and float((asset.get("latestMarket") or {}).get("liquidity_usd") or 0) > 0
    ]

    website_domain = str(project.get("website_domain") or "").strip()
    project_identity_boundary = (
        "verified_identity"
        if project.get("identity_status") == "verified"
        else "unverified_identity"
    )
    website_url = (
        website_domain
        if website_domain.startswith(("http://", "https://"))
        else f"https://{website_domain}"
        if website_domain
        else ""
    )
    website_fact = fact_item(
        "officialWebsite",
        "官网",
        bool(website_domain),
        website_domain,
        website_url,
        "项目主体档案",
        project.get("updated_at"),
        project_identity_boundary,
    )

    x_evidence = evidence_match(
        evidence,
        keywords=("x.com/", "twitter.com/"),
    )
    github_evidence = evidence_match(
        evidence,
        keywords=("github.com/",),
        evidence_types=("official_code_activity",),
    )
    docs_evidence = evidence_match(
        evidence,
        evidence_types=("official_product_docs",),
        keywords=(
            "docs.",
            "/docs",
            "documentation",
            "whitepaper",
            "readme",
            "文档",
            "白皮书",
        ),
    )
    tokenomics_evidence = evidence_match(
        evidence,
        evidence_types=("official_tokenomics",),
        keywords=(
            "tokenomics",
            "token economy",
            "代币经济",
            "供应",
            "supply",
            "unlock",
            "emission",
            "排放",
        ),
    )
    audit_evidence = evidence_match(
        evidence,
        evidence_types=("official_audit_or_security", "official_security_activity"),
        keywords=("audit", "audited", "审计", "security review"),
    )
    team_evidence = evidence_match(
        evidence,
        evidence_types=("official_team_or_organization",),
        keywords=("team", "founder", "团队", "创始人", "核心贡献者"),
    )

    official_repo = str(project.get("official_repo") or "").strip()
    if official_repo:
        github_fact = fact_item(
            "github",
            "GitHub",
            True,
            official_repo,
            official_repo,
            "项目主体档案",
            project.get("updated_at"),
            project_identity_boundary,
        )
    else:
        github_fact = fact_from_evidence(
            "github",
            "GitHub",
            github_evidence,
            "已找到代码仓库痕迹",
        )

    contract = contracts[0] if contracts else None
    contract_address = (
        (contract or {}).get("contract_address")
        or record.get("contractAddress")
        or discovery.get("contractAddress")
        or ""
    )
    network_name = (
        (contract or {}).get("network_name")
        or record.get("networkName")
        or discovery.get("networkName")
        or ""
    )
    contract_url = (contract or {}).get("source_url") or discovery.get("explorerUrl")
    if not contract_url and contract and contract.get("explorer_url") and contract_address:
        contract_url = (
            str(contract["explorer_url"]).rstrip("/")
            + "/address/"
            + str(contract_address)
        )
    contract_fact = fact_item(
        "contractNetwork",
        "合约与所在链",
        bool(contract_address and network_name),
        f"{network_name} · {contract_address}" if contract_address and network_name else "",
        contract_url,
        (contract or {}).get("source_name")
        or discovery.get("sourceName")
        or "链上发现",
        (contract or {}).get("observed_at") or discovery.get("lastSeenAt"),
        "market_matched"
        if (contract or {}).get("identity_status") == "market_matched"
        else (contract or {}).get("identity_status") or "unverified_identity",
    )

    market, market_asset = markets[0] if markets else ({}, {})
    liquidity_value = (
        market.get("liquidity_usd")
        if market
        else record.get("liquidityUsd")
    )
    liquidity_source_url = ""
    for contract_item in market_asset.get("contracts") or []:
        liquidity_source_url = http_url(contract_item.get("source_url"))
        if liquidity_source_url:
            break
    if not liquidity_source_url:
        liquidity_source_url = next(
            (
                http_url(item)
                for item in discovery.get("sourceUrls") or []
                if http_url(item)
            ),
            "",
        )
    liquidity_fact = fact_item(
        "liquidity",
        "流动性",
        liquidity_value is not None and float(liquidity_value or 0) > 0,
        (
            f"流动性 ${float(liquidity_value):,.0f}"
            if liquidity_value is not None and float(liquidity_value or 0) > 0
            else ""
        ),
        liquidity_source_url,
        market.get("source_name") or "市场快照",
        market.get("observed_at") or discovery.get("lastSeenAt"),
        "market_snapshot",
    )

    governance = evidence_match(
        evidence,
        keywords=("governance", "proposal", "snapshot.org", "tally", "cactus", "治理", "提案"),
        evidence_types=("onchain_governance", "offchain_governance"),
    )
    github_release = evidence_match(
        evidence,
        keywords=("/releases", "/commit/", "/pull/", "recent push", "最近推送", "关键提交"),
        evidence_types=("official_code_activity",),
    )
    deployment = evidence_match(
        evidence,
        keywords=("deployed", "deployment", "合约部署", "新合约", "mainnet contract"),
    )
    product_upgrade = evidence_match(
        evidence,
        keywords=("upgrade", "mainnet", "release", "version", "产品升级", "主网", "上线"),
    )
    onchain = evidence_match(
        evidence,
        keywords=("tvl", "revenue", "fees", "users", "adoption", "链上数据", "收入", "用户"),
        evidence_types=("protocol_adoption_metric",),
    )
    regulatory = evidence_match(
        evidence,
        keywords=("regulatory", "regulation", "sec.gov", "监管", "批准", "执法"),
    )
    institutional = evidence_match(
        evidence,
        keywords=("institutional", "treasury", "机构", "基金", "银行", "asset manager"),
    )
    tokenomics_adjustment = evidence_match(
        evidence,
        keywords=("tokenomics", "emission", "burn", "buyback", "unlock", "排放", "销毁", "回购", "解锁"),
    )

    team_summary = str(project.get("team_summary") or "").strip()
    facts = {
        "officialWebsite": website_fact,
        "officialX": fact_from_evidence(
            "officialX", "官方 X", x_evidence, "已找到 X 入口"
        ),
        "github": github_fact,
        "productDocs": fact_from_evidence(
            "productDocs", "产品文档", docs_evidence, "已找到产品文档痕迹"
        ),
        "tokenomics": fact_from_evidence(
            "tokenomics", "代币经济", tokenomics_evidence, "已找到代币经济痕迹"
        ),
        "contractNetwork": contract_fact,
        "liquidity": liquidity_fact,
        "team": (
            fact_item(
                "team",
                "团队与组织",
                True,
                team_summary,
                "",
                "项目主体档案",
                project.get("updated_at"),
                project_identity_boundary,
            )
            if team_summary
            else fact_from_evidence(
                "team", "团队与组织", team_evidence, "已找到团队相关资料"
            )
        ),
        "audit": fact_from_evidence(
            "audit", "审计", audit_evidence, "已找到审计相关资料"
        ),
        "governanceProposal": fact_from_evidence(
            "governanceProposal", "治理提案", governance, "已找到治理提案"
        ),
        "githubRelease": fact_from_evidence(
            "githubRelease",
            "GitHub 发布/关键提交",
            github_release,
            "已找到代码发布或关键提交",
        ),
        "contractDeployment": fact_from_evidence(
            "contractDeployment", "新合约部署", deployment, "已找到新合约部署"
        ),
        "productUpgrade": fact_from_evidence(
            "productUpgrade", "产品升级", product_upgrade, "已找到产品升级"
        ),
        "onchainData": fact_from_evidence(
            "onchainData", "链上采用数据", onchain, "已找到链上采用数据"
        ),
        "regulatory": fact_from_evidence(
            "regulatory", "监管进展", regulatory, "已找到监管进展"
        ),
        "institutional": fact_from_evidence(
            "institutional", "机构动作", institutional, "已找到机构动作"
        ),
        "tokenomicsAdjustment": fact_from_evidence(
            "tokenomicsAdjustment",
            "代币经济调整",
            tokenomics_adjustment,
            "已找到代币经济调整",
        ),
    }
    return facts


def checklist_for(route_id, facts):
    definitions = {
        "startup": STARTUP_CHECKS,
        "mature": MATURE_CHECKS,
        "hybrid": HYBRID_CHECKS,
    }[route_id]
    return [{**facts[field_id], "label": label} for field_id, label in definitions]


def manual_override(target):
    values = ((target or {}).get("manualReview") or {}).get("values") or {}
    route_id = str(values.get("researchRouteOverride") or "auto")
    reason = str(values.get("researchRouteReason") or "").strip()
    if route_id not in ALLOWED_OVERRIDES:
        return None
    if route_id == "auto":
        return None
    return {
        "routeId": route_id,
        "reason": reason or "人工调整研究路线，原因待补充。",
        "annotationId": ((target or {}).get("manualReview") or {}).get("annotationId", ""),
        "updatedAt": ((target or {}).get("manualReview") or {}).get("updatedAt", ""),
    }


def queue_priority(record, route_id, foundation_count, signal_count):
    date_status = record.get("lifecycleDateStatus")
    date_points = {
        "verified": 10,
        "market_history": 8,
        "provisional": 5,
        "lower_bound": 4,
        "pending": 0,
    }.get(date_status, 0)
    market_cap = record.get("marketCapUsd")
    market_available = sum(
        record.get(field) is not None
        for field in ("marketCapUsd", "fdvUsd", "liquidityUsd")
    )
    evidence_count = int(record.get("evidenceCount") or 0)

    if route_id == "startup":
        if market_cap is None:
            tail_points = 0
        elif float(market_cap) < 1_000_000:
            tail_points = 20
        elif float(market_cap) < 10_000_000:
            tail_points = 15
        elif float(market_cap) < 100_000_000:
            tail_points = 8
        else:
            tail_points = 2
        breakdown = [
            ("基础档案", round(foundation_count / len(STARTUP_CHECKS) * 45)),
            ("合约与流动性", 20 if record.get("liquidityUsd") else 8 if record.get("contractAddress") else 0),
            ("低市值尾部", tail_points),
            ("时间可信度", date_points),
            ("证据积累", min(5, evidence_count)),
        ]
    elif route_id == "mature":
        age_days = int(record.get("lifecycleAgeDays") or 0)
        breakdown = [
            ("前置信号", round(signal_count / len(MATURE_CHECKS) * 45)),
            ("存活时间", min(25, round(age_days / 365 / 12 * 25))),
            ("市场数据", round(market_available / 3 * 10)),
            ("时间可信度", date_points),
            ("证据积累", min(10, evidence_count)),
        ]
    else:
        breakdown = [
            ("基础档案", round(foundation_count / len(STARTUP_CHECKS) * 25)),
            ("前置信号", round(signal_count / len(MATURE_CHECKS) * 30)),
            ("市场数据", round(market_available / 3 * 20)),
            ("时间可信度", date_points),
            ("证据积累", min(15, evidence_count)),
        ]
    return {
        "score": min(100, sum(points for _label, points in breakdown)),
        "breakdown": [
            {"label": label, "points": points}
            for label, points in breakdown
        ],
    }


def build_route_record(record, detail, opportunity, manual_target):
    auto = automatic_route(record)
    override = manual_override(manual_target)
    route_id = auto["routeId"]
    focus_id = override["routeId"] if override else route_id
    route = ROUTE_BY_ID[route_id]
    focus = ROUTE_BY_ID[focus_id]
    facts = research_facts(record, detail)
    checklist = checklist_for(focus_id, facts)
    foundation_profile = [
        {**facts[field_id], "label": label}
        for field_id, label in STARTUP_CHECKS
    ]
    pre_signals = [
        {**facts[field_id], "label": label}
        for field_id, label in MATURE_CHECKS
    ]
    complete_count = sum(item["status"] == "available" for item in checklist)
    pending = [item["label"] for item in checklist if item["status"] == "pending"]
    official_anchor_count = sum(
        facts[field_id]["status"] == "available"
        for field_id in ("officialWebsite", "officialX", "github", "productDocs")
    )
    startup_ready = (
        record.get("identityStatus") == "verified"
        and facts["contractNetwork"]["status"] == "available"
        and facts["liquidity"]["status"] == "available"
        and official_anchor_count >= 2
    )
    mature_signal_count = sum(
        facts[field_id]["status"] == "available"
        for field_id, _label in MATURE_CHECKS
    )
    foundation_count = sum(
        item["status"] == "available" for item in foundation_profile
    )
    priority = queue_priority(
        record,
        route_id,
        foundation_count,
        mature_signal_count,
    )
    layout_priority = {
        "startup": "foundation_first",
        "mature": "signals_first",
        "hybrid": "balanced",
    }[focus_id]
    layout_reason = {
        "startup": "早期项目先判断是否真实、可理解、可核验和可退出，再研究催化剂。",
        "mature": "OG项目先展示新闻前的可信事实变化，再回看基础档案。",
        "hybrid": "基础资料和前置信号都不足以单独主导判断，两个视角并列补证。",
    }[focus_id]
    return {
        "masterId": record["masterId"],
        "projectId": record.get("projectId") or "",
        "caseId": record.get("caseId") or "",
        "name": record["name"],
        "symbol": record.get("symbol") or "",
        "recordType": record["recordType"],
        "routeId": route_id,
        "routeLabel": route["label"],
        "routeShortLabel": route["shortLabel"],
        "routeReason": auto["reason"],
        "routeSignals": auto["signals"],
        "routeConfidence": auto["confidence"],
        "routeSource": "automatic",
        "routeSourceLabel": "生命周期自动分类",
        "researchFocusId": focus_id,
        "researchFocusLabel": focus["label"],
        "researchFocusReason": override["reason"] if override else auto["reason"],
        "researchFocusSource": "manual_override" if override else "automatic",
        "researchFocusSourceLabel": "人工调整" if override else "跟随项目类别",
        "primaryFocus": focus["primaryFocus"],
        "checklist": checklist,
        "foundationProfile": foundation_profile,
        "preSignals": pre_signals,
        "foundationCompleteCount": foundation_count,
        "foundationTotal": len(foundation_profile),
        "preSignalCount": mature_signal_count,
        "preSignalTotal": len(pre_signals),
        "layoutPriority": layout_priority,
        "layoutReason": layout_reason,
        "lifecycleBucket": record.get("lifecycleBucket") or "other",
        "lifecycleLabel": record.get("lifecycleLabel") or "潜力项目",
        "lifecycleDate": record.get("lifecycleDate") or "",
        "lifecycleDateStatus": record.get("lifecycleDateStatus") or "pending",
        "lifecycleDateBasis": record.get("lifecycleDateBasis") or "",
        "lifecycleSourceName": record.get("lifecycleSourceName") or "",
        "lifecycleSourceUrl": record.get("lifecycleSourceUrl") or "",
        "lifecycleAgeDays": record.get("lifecycleAgeDays"),
        "lifecycleAgeLabel": record.get("lifecycleAgeLabel") or "时间待核验",
        "lifecycleAutoMoveAt": record.get("lifecycleAutoMoveAt") or "",
        "lifecycleReason": record.get("lifecycleReason") or "",
        "queuePriorityScore": priority["score"],
        "queuePriorityBreakdown": priority["breakdown"],
        "completeCount": complete_count,
        "totalChecks": len(checklist),
        "nextEvidence": pending[0] if pending else "当前路线重点资料已覆盖",
        "startupResearchReady": startup_ready if route_id == "startup" else False,
        "maturePreSignalCount": mature_signal_count if route_id == "mature" else 0,
        "manualOverride": override,
        "currentAction": (
            (opportunity or {}).get("opportunityStage", {}).get("finalActionLabel")
            or "只观察"
        ),
        "boundary": "项目类别由生命周期证据自动决定；人工只能调整研究重点，不能改变早期、OG、潜力分类，也不直接改变当前动作。",
    }


def build_snapshot(
    master_path=DEFAULT_MASTER_PATH,
    detail_path=DEFAULT_DETAIL_PATH,
    manual_path=DEFAULT_MANUAL_PATH,
    opportunity_path=DEFAULT_OPPORTUNITY_PATH,
):
    master = load_js_payload(master_path, MASTER_PREFIX)
    detail = load_js_payload(detail_path, DETAIL_PREFIX)
    manual = load_js_payload(manual_path, MANUAL_PREFIX)
    opportunity = load_js_payload(opportunity_path, OPPORTUNITY_PREFIX)

    detail_by_master = detail.get("records") or {}
    manual_by_master = {
        item["masterId"]: item for item in manual.get("targets", [])
    }
    opportunity_by_case = {
        item["caseId"]: item for item in opportunity.get("cases", [])
    }
    records = [
        build_route_record(
            record,
            detail_by_master.get(record["masterId"]) or {},
            opportunity_by_case.get(record.get("caseId")) or {},
            manual_by_master.get(record["masterId"]),
        )
        for record in master.get("records", [])
    ]
    counts = Counter(item["routeId"] for item in records)
    return {
        "version": "C1.2-05",
        "release": "C1.2",
        "generatedAt": utc_now(),
        "title": "凸性项目生命周期分类与研究重点",
        "boundary": "项目类别由生命周期证据自动决定；研究重点只安排证据采集与复核顺序，不替代凸性模型、风险判断、交易性门槛或五类当前动作。",
        "thresholds": {
            "startup": "公开启动未满6个月；满6个月自动转入潜力项目。",
            "mature": "公开存活至少5年。",
            "hybrid": "存活6个月至5年，或创建时间仍待核验。",
        },
        "routes": ROUTES,
        "counts": {
            "total": len(records),
            "caseRecords": sum(bool(item["caseId"]) for item in records),
            "discoveryRecords": sum(
                item["recordType"] == "discovery" for item in records
            ),
            "startup": counts["startup"],
            "mature": counts["mature"],
            "hybrid": counts["hybrid"],
            "manualOverrides": sum(
                item["researchFocusSource"] == "manual_override" for item in records
            ),
            "startupResearchReady": sum(
                item["startupResearchReady"] for item in records
            ),
            "matureWithPreSignals": sum(
                item["maturePreSignalCount"] > 0 for item in records
            ),
        },
        "records": records,
    }


def write_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_CONVEXITY_RESEARCH_ROUTES = "
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return output_path


def rebuild_research_route_snapshot(
    master_path=DEFAULT_MASTER_PATH,
    detail_path=DEFAULT_DETAIL_PATH,
    manual_path=DEFAULT_MANUAL_PATH,
    opportunity_path=DEFAULT_OPPORTUNITY_PATH,
    output_path=DEFAULT_OUTPUT_PATH,
):
    snapshot = build_snapshot(
        master_path,
        detail_path,
        manual_path,
        opportunity_path,
    )
    write_snapshot(snapshot, output_path)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="生成凸性项目研究路线快照")
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER_PATH)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL_PATH)
    parser.add_argument("--manual", type=Path, default=DEFAULT_MANUAL_PATH)
    parser.add_argument("--opportunity", type=Path, default=DEFAULT_OPPORTUNITY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_research_route_snapshot(
        args.master,
        args.detail,
        args.manual,
        args.opportunity,
        args.output,
    )
    print(json.dumps(snapshot["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
