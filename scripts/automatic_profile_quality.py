#!/usr/bin/env python3
from urllib.parse import urlparse


PROFILE_VERSION = "C1.4-05"
PROFILE_BOUNDARY = (
    "档案完整度只衡量自动采集资料的覆盖与可核验程度，"
    "用于安排补齐和研究顺序，不代表凸性质量、收益赔率或行动建议。"
)

STATUS_FACTORS = {
    "verified": 1.0,
    "available": 0.75,
    "stale": 0.5,
    "pending": 0.25,
    "missing": 0.0,
    "conflict": 0.0,
}

SECTION_DEFINITIONS = (
    ("identity", "主体身份", 25),
    ("official", "官方入口", 20),
    ("asset", "资产与合约", 25),
    ("market", "市场与退出", 20),
    ("activity", "持续证据", 10),
)

NEXT_TASK_LABELS = {
    "identity_refresh": "发现队列身份复核",
    "profile_enrichment_refresh": "正式项目身份与官方入口",
    "machine_asset_identity_refresh": "机器项目资产与基础档案",
    "source_discovery_refresh": "项目与官方入口发现",
    "contract_refresh": "合约、风险与卖出路径核验",
    "market_refresh": "行情与流动性刷新",
    "formal_market_exit_refresh": "正式项目市场与退出资料",
    "formal_research_materials_refresh": "正式项目研究资料",
    "high_value_evidence_refresh": "正式项目持续证据",
}


def http_url(value):
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


def domain_url(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    return f"https://{text}"


def field(
    field_id,
    label,
    maximum,
    status,
    *,
    value="",
    source_name="",
    source_url="",
    updated_at="",
    next_task_id="",
):
    normalized_status = (
        status if status in STATUS_FACTORS else "missing"
    )
    return {
        "id": field_id,
        "label": label,
        "status": normalized_status,
        "value": str(value or "").strip(),
        "sourceName": str(source_name or "").strip(),
        "sourceUrl": http_url(source_url),
        "updatedAt": str(updated_at or "").strip(),
        "score": round(maximum * STATUS_FACTORS[normalized_status], 1),
        "maxScore": maximum,
        "autoFill": True,
        "nextTaskId": next_task_id,
        "nextTaskLabel": NEXT_TASK_LABELS.get(next_task_id, ""),
    }


def evidence_match(evidence, *, evidence_types=(), keywords=()):
    type_set = {item.casefold() for item in evidence_types}
    keyword_set = tuple(item.casefold() for item in keywords)
    matches = []
    for item in evidence:
        evidence_type = str(item.get("evidence_type") or "").casefold()
        searchable = " ".join(
            str(item.get(key) or "")
            for key in ("summary", "source_url", "source_name", "source_id")
        ).casefold()
        if evidence_type in type_set or any(
            keyword in searchable for keyword in keyword_set
        ):
            matches.append(item)
    return max(
        matches,
        key=lambda item: (
            str(item.get("observed_at") or ""),
            str(item.get("created_at") or ""),
        ),
        default=None,
    )


def preferred_evidence_match(evidence, *, evidence_types, keywords):
    return evidence_match(
        evidence,
        evidence_types=evidence_types,
    ) or evidence_match(
        evidence,
        keywords=keywords,
    )


def evidence_status(item):
    if not item:
        return "missing"
    if item.get("fact_boundary") == "confirmed_fact" and item.get(
        "confidence"
    ) in {"高", "high"}:
        return "verified"
    return "available"


def field_from_evidence(
    field_id,
    label,
    maximum,
    item,
    next_task_id,
):
    if not item:
        return field(
            field_id,
            label,
            maximum,
            "missing",
            next_task_id=next_task_id,
        )
    return field(
        field_id,
        label,
        maximum,
        evidence_status(item),
        value=item.get("summary"),
        source_name=item.get("source_name") or item.get("source_id"),
        source_url=item.get("source_url"),
        updated_at=item.get("observed_at") or item.get("created_at"),
        next_task_id=next_task_id,
    )


def source_discovery_field(
    rows,
    field_id,
    label,
    maximum,
    column,
):
    candidates = [item for item in rows if http_url(item.get(column))]
    if not candidates:
        return None
    rank = {
        ("verified", "high"): 0,
        ("verified", "medium"): 1,
        ("corroborated", "high"): 2,
        ("corroborated", "medium"): 3,
    }
    selected = min(
        candidates,
        key=lambda item: (
            rank.get(
                (
                    item.get("project_identity_status"),
                    item.get("attribution_confidence"),
                ),
                9,
            ),
            str(item.get("last_seen_at") or ""),
        ),
    )
    identity_status = selected.get("project_identity_status")
    if identity_status == "conflict":
        status = "conflict"
    elif (
        identity_status == "verified"
        and selected.get("attribution_confidence") == "high"
    ):
        status = "verified"
    elif identity_status in {"verified", "corroborated"}:
        status = "available"
    else:
        status = "pending"
    return field(
        field_id,
        label,
        maximum,
        status,
        value=selected[column],
        source_name=selected.get("source_name") or selected.get("source_id"),
        source_url=selected[column],
        updated_at=selected.get("last_seen_at"),
        next_task_id="source_discovery_refresh",
    )


def first_market(assets):
    markets = [
        (asset.get("latestMarket") or {}, asset)
        for asset in assets
        if asset.get("latestMarket")
    ]
    return max(
        markets,
        key=lambda item: (
            sum(
                item[0].get(key) is not None
                for key in (
                    "price_usd",
                    "market_cap_usd",
                    "fdv_usd",
                    "liquidity_usd",
                    "volume_24h_usd",
                )
            ),
            str(item[0].get("observed_at") or ""),
        ),
        default=({}, {}),
    )


def build_project_fields(detail, source_discoveries):
    master = detail.get("master") or {}
    project = detail.get("project") or {}
    assets = detail.get("assets") or []
    evidence = detail.get("evidence") or []
    contracts = [
        contract
        for asset in assets
        for contract in (asset.get("contracts") or [])
    ]
    tradeability = [
        check
        for asset in assets
        for check in (asset.get("tradeability") or [])
    ]
    contract_risks = [
        asset.get("contractRisk")
        for asset in assets
        if asset.get("contractRisk")
    ]
    market, market_asset = first_market(assets)

    identity_status = str(project.get("identity_status") or "pending")
    identity_field_status = (
        "verified"
        if identity_status == "verified"
        else "conflict"
        if identity_status in {"conflict", "rejected"}
        else "pending"
    )
    lifecycle_status = str(master.get("lifecycleDateStatus") or "pending")
    lifecycle_field_status = (
        "verified"
        if lifecycle_status in {"verified", "market_history"}
        else "available"
        if lifecycle_status in {"provisional", "lower_bound"}
        else "missing"
    )
    independent_sources = {
        str(item.get("source_id") or "")
        for item in source_discoveries
        if item.get("source_id")
    } | {
        str(item.get("source_id") or "")
        for item in evidence
        if item.get("source_id")
    }

    website_evidence = evidence_match(
        evidence,
        evidence_types=("official_website",),
    )
    website = None
    if project.get("website_domain"):
        website = field(
            "officialWebsite",
            "官网",
            4,
            "verified" if identity_status == "verified" else "available",
            value=project["website_domain"],
            source_name="项目身份主库",
            source_url=domain_url(project["website_domain"]),
            updated_at=project.get("updated_at"),
            next_task_id="source_discovery_refresh",
        )
    if website is None:
        website = source_discovery_field(
            source_discoveries,
            "officialWebsite",
            "官网",
            4,
            "website_url",
        )
    if website is None:
        website = field_from_evidence(
            "officialWebsite",
            "官网",
            4,
            website_evidence,
            "source_discovery_refresh",
        )

    x_evidence = evidence_match(
        evidence,
        keywords=("x.com/", "twitter.com/"),
    )
    official_x = source_discovery_field(
        source_discoveries,
        "officialX",
        "官方 X",
        3,
        "social_url",
    ) or field_from_evidence(
        "officialX",
        "官方 X",
        3,
        x_evidence,
        "source_discovery_refresh",
    )

    github_evidence = evidence_match(
        evidence,
        evidence_types=("official_code_activity",),
    )
    if project.get("official_repo"):
        github = field(
            "github",
            "GitHub",
            4,
            "verified" if identity_status == "verified" else "available",
            value=project["official_repo"],
            source_name="项目身份主库",
            source_url=project["official_repo"],
            updated_at=project.get("updated_at"),
            next_task_id="source_discovery_refresh",
        )
    else:
        github = source_discovery_field(
            source_discoveries,
            "github",
            "GitHub",
            4,
            "repository_url",
        ) or field_from_evidence(
            "github",
            "GitHub",
            4,
            github_evidence,
            "source_discovery_refresh",
        )

    docs = field_from_evidence(
        "productDocs",
        "产品文档",
        3,
        preferred_evidence_match(
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
        ),
        "formal_research_materials_refresh",
    )
    tokenomics = field_from_evidence(
        "tokenomics",
        "代币经济",
        3,
        preferred_evidence_match(
            evidence,
            evidence_types=("official_tokenomics",),
            keywords=(
                "tokenomics",
                "token economy",
                "代币经济",
                "supply",
                "unlock",
                "emission",
                "供应",
                "解锁",
                "排放",
            ),
        ),
        "formal_research_materials_refresh",
    )
    team_evidence = preferred_evidence_match(
        evidence,
        evidence_types=("official_team_or_organization",),
        keywords=("team", "founder", "团队", "创始人", "核心贡献者"),
    )
    if project.get("team_summary"):
        team = field(
            "team",
            "团队与组织",
            3,
            "available",
            value=project["team_summary"],
            source_name="项目身份主库",
            updated_at=project.get("updated_at"),
            next_task_id="formal_research_materials_refresh",
        )
    else:
        team = field_from_evidence(
            "team",
            "团队与组织",
            3,
            team_evidence,
            "formal_research_materials_refresh",
        )

    asset = assets[0] if assets else {}
    asset_identity_values = {
        str(item.get("identity_status") or "") for item in assets
    }
    asset_identity_status = (
        "verified"
        if asset_identity_values & {"verified", "market_matched"}
        else "conflict"
        if "conflict" in asset_identity_values
        else "pending"
        if assets
        else "missing"
    )
    contract = next(
        (
            item
            for item in contracts
            if item.get("identity_status") in {"verified", "market_matched"}
        ),
        contracts[0] if contracts else None,
    )
    contract_status = (
        "verified"
        if contract
        and contract.get("identity_status") in {"verified", "market_matched"}
        else "available"
        if contract
        else "missing"
    )
    contract_url = ""
    if contract and contract.get("explorer_url") and contract.get(
        "contract_address"
    ):
        contract_url = (
            str(contract["explorer_url"]).rstrip("/")
            + "/address/"
            + str(contract["contract_address"])
        )
    risk = contract_risks[0] if contract_risks else None
    sell_check = next(
        (
            item
            for item in tradeability
            if item.get("sell_path_status")
            in {"pass", "read_only_verified"}
        ),
        tradeability[0] if tradeability else None,
    )
    sell_status = (
        "verified"
        if sell_check
        and sell_check.get("sell_path_status")
        in {"pass", "read_only_verified"}
        else "conflict"
        if sell_check and sell_check.get("sell_path_status") in {"fail", "blocked"}
        else "pending"
        if sell_check
        else "missing"
    )
    market_source = market.get("source_name") or market.get(
        "data_source_id"
    )
    market_updated = market.get("observed_at")
    market_url = next(
        (
            http_url(item.get("source_url"))
            for item in (market_asset.get("contracts") or [])
            if http_url(item.get("source_url"))
        ),
        "",
    )

    adoption = evidence_match(
        evidence,
        evidence_types=("protocol_adoption_metric",),
    )
    governance = preferred_evidence_match(
        evidence,
        evidence_types=("onchain_governance", "governance_proposal"),
        keywords=("governance", "proposal", "治理", "提案"),
    )
    audit = preferred_evidence_match(
        evidence,
        evidence_types=(
            "official_audit_or_security",
            "official_security_activity",
        ),
        keywords=("audit", "audited", "审计", "security review"),
    )

    return {
        "identity": [
            field(
                "canonicalName",
                "标准名称",
                5,
                "verified" if identity_status == "verified" else "available",
                value=project.get("canonical_name") or master.get("name"),
                source_name="项目身份主库",
                updated_at=project.get("updated_at"),
                next_task_id="profile_enrichment_refresh",
            ),
            field(
                "projectIdentity",
                "项目主体身份",
                10,
                identity_field_status,
                value=identity_status,
                source_name="项目身份主库",
                updated_at=project.get("updated_at"),
                next_task_id="profile_enrichment_refresh",
            ),
            field(
                "lifecycle",
                "项目启动时间",
                5,
                lifecycle_field_status,
                value=master.get("lifecycleDate")
                or master.get("lifecycleDateBasis"),
                source_name=master.get("lifecycleSourceName"),
                source_url=master.get("lifecycleSourceUrl"),
                updated_at=master.get("lastSeenAt"),
                next_task_id="identity_refresh",
            ),
            field(
                "sourceCorroboration",
                "独立来源覆盖",
                5,
                "verified"
                if len(independent_sources) >= 3
                else "available"
                if len(independent_sources) >= 2
                else "pending"
                if len(independent_sources) == 1
                else "missing",
                value=f"{len(independent_sources)}个来源",
                source_name="自动来源归并",
                updated_at=master.get("lastSeenAt"),
                next_task_id="source_discovery_refresh",
            ),
        ],
        "official": [website, official_x, github, docs, tokenomics, team],
        "asset": [
            field(
                "tradableAsset",
                "可交易资产",
                5,
                "verified"
                if asset_identity_status == "verified"
                else "available"
                if assets
                else "missing",
                value="、".join(
                    str(item.get("symbol") or item.get("asset_id"))
                    for item in assets
                ),
                source_name="资产身份主库",
                updated_at=project.get("updated_at"),
                next_task_id="machine_asset_identity_refresh",
            ),
            field(
                "assetIdentity",
                "资产身份",
                5,
                asset_identity_status,
                value="、".join(sorted(asset_identity_values)),
                source_name="资产身份主库",
                updated_at=project.get("updated_at"),
                next_task_id="machine_asset_identity_refresh",
            ),
            field(
                "network",
                "所在链或网络",
                4,
                "verified" if contract_status == "verified" else "available"
                if (contract or asset.get("chain"))
                else "missing",
                value=(contract or {}).get("network_name")
                or asset.get("chain"),
                source_name=(contract or {}).get("source_name")
                or "资产身份主库",
                source_url=contract_url,
                updated_at=(contract or {}).get("observed_at"),
                next_task_id="contract_refresh",
            ),
            field(
                "contract",
                "代币合约",
                6,
                contract_status,
                value=(contract or {}).get("contract_address")
                or asset.get("contract_address"),
                source_name=(contract or {}).get("source_name")
                or "资产身份主库",
                source_url=contract_url,
                updated_at=(contract or {}).get("observed_at"),
                next_task_id="contract_refresh",
            ),
            field(
                "contractRisk",
                "合约风险核验",
                5,
                "verified"
                if risk
                and risk.get("overall_risk") not in {None, "", "unknown"}
                else "pending"
                if risk
                else "missing",
                value=(risk or {}).get("overall_risk"),
                source_name="合约自动核验" if risk else "",
                source_url=(risk or {}).get("source_url"),
                updated_at=(risk or {}).get("assessed_at"),
                next_task_id="contract_refresh",
            ),
        ],
        "market": [
            field(
                "marketSnapshot",
                "价格与估值快照",
                5,
                "verified"
                if any(
                    market.get(key) is not None
                    for key in ("price_usd", "market_cap_usd", "fdv_usd")
                )
                else "missing",
                value=market.get("market_cap_usd")
                or market.get("fdv_usd")
                or market.get("price_usd"),
                source_name=market_source,
                source_url=market_url,
                updated_at=market_updated,
                next_task_id="formal_market_exit_refresh",
            ),
            field(
                "liquidity",
                "流动性",
                5,
                "verified"
                if market.get("liquidity_usd") is not None
                else "missing",
                value=market.get("liquidity_usd"),
                source_name=market_source,
                source_url=market_url,
                updated_at=market_updated,
                next_task_id="formal_market_exit_refresh",
            ),
            field(
                "volume",
                "24小时成交",
                3,
                "verified"
                if market.get("volume_24h_usd") is not None
                else "missing",
                value=market.get("volume_24h_usd"),
                source_name=market_source,
                source_url=market_url,
                updated_at=market_updated,
                next_task_id="formal_market_exit_refresh",
            ),
            field(
                "sellPath",
                "卖出路径",
                4,
                sell_status,
                value=(sell_check or {}).get("sell_path_status"),
                source_name=(sell_check or {}).get("source_name"),
                source_url=(sell_check or {}).get("source_url"),
                updated_at=(sell_check or {}).get("checked_at"),
                next_task_id="formal_market_exit_refresh",
            ),
            field(
                "slippage",
                "退出滑点",
                3,
                "verified"
                if sell_check
                and sell_check.get("estimated_exit_slippage_pct") is not None
                else "missing",
                value=(sell_check or {}).get(
                    "estimated_exit_slippage_pct"
                ),
                source_name=(sell_check or {}).get("source_name"),
                source_url=(sell_check or {}).get("source_url"),
                updated_at=(sell_check or {}).get("checked_at"),
                next_task_id="formal_market_exit_refresh",
            ),
        ],
        "activity": [
            field_from_evidence(
                "adoption",
                "链上采用",
                3,
                adoption,
                "high_value_evidence_refresh",
            ),
            field_from_evidence(
                "governance",
                "治理提案",
                3,
                governance,
                "high_value_evidence_refresh",
            ),
            field_from_evidence(
                "codeActivity",
                "代码活动",
                2,
                github_evidence,
                "high_value_evidence_refresh",
            ),
            field_from_evidence(
                "audit",
                "审计与安全动态",
                2,
                audit,
                "high_value_evidence_refresh",
            ),
        ],
    }


def build_discovery_fields(detail):
    master = detail.get("master") or {}
    discovery = detail.get("discovery") or {}
    identity = discovery.get("identityReview") or {}
    identity_resolution = str(
        identity.get("resolutionStatus")
        or master.get("identityStatus")
        or "pending"
    )
    identity_status = (
        "verified"
        if identity_resolution == "verified"
        else "conflict"
        if identity_resolution in {"conflict", "rejected"}
        else "available"
        if identity_resolution == "corroborated"
        else "pending"
    )
    sources = {
        item for item in discovery.get("sourceIds") or [] if item
    }
    website_status = (
        "verified"
        if identity.get("websiteStatus") == "accessible"
        and identity_status == "verified"
        else "available"
        if identity.get("websiteUrl")
        else "missing"
    )
    official_contract = str(
        identity.get("officialContractStatus") or ""
    )
    contract_status = (
        "verified"
        if official_contract in {"confirmed", "registry_matched"}
        else "available"
        if discovery.get("contractAddress")
        else "missing"
    )
    sell_path = str(discovery.get("sellPathStatus") or "")
    sell_status = (
        "verified"
        if sell_path in {"pass", "read_only_verified"}
        else "conflict"
        if sell_path in {"fail", "blocked"}
        else "pending"
        if sell_path
        else "missing"
    )
    market_source = "、".join(sources)
    source_url = next(
        (
            http_url(item)
            for item in discovery.get("sourceUrls") or []
            if http_url(item)
        ),
        "",
    )
    social_urls = identity.get("socialUrls") or []
    repo_urls = identity.get("repoUrls") or []
    evidence_count = len(discovery.get("evidence") or [])

    return {
        "identity": [
            field(
                "canonicalName",
                "标准名称",
                5,
                identity_status if identity.get("canonicalName") else "available",
                value=identity.get("canonicalName")
                or discovery.get("tokenName")
                or master.get("name"),
                source_name=identity.get("provider") or "链上发现",
                updated_at=identity.get("reviewedAt")
                or discovery.get("lastSeenAt"),
                next_task_id="identity_refresh",
            ),
            field(
                "projectIdentity",
                "项目主体身份",
                10,
                identity_status,
                value=identity_resolution,
                source_name=identity.get("provider"),
                updated_at=identity.get("reviewedAt"),
                next_task_id="identity_refresh",
            ),
            field(
                "lifecycle",
                "项目启动时间",
                5,
                "missing",
                value=master.get("lifecycleDateBasis"),
                source_name="链上发现",
                updated_at=discovery.get("firstSeenAt"),
                next_task_id="identity_refresh",
            ),
            field(
                "sourceCorroboration",
                "独立来源覆盖",
                5,
                "verified"
                if len(sources) >= 3
                else "available"
                if len(sources) >= 2
                else "pending"
                if sources
                else "missing",
                value=f"{len(sources)}个来源",
                source_name=market_source,
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="source_discovery_refresh",
            ),
        ],
        "official": [
            field(
                "officialWebsite",
                "官网",
                4,
                website_status,
                value=identity.get("websiteUrl"),
                source_name=identity.get("provider"),
                source_url=identity.get("websiteUrl"),
                updated_at=identity.get("reviewedAt"),
                next_task_id="source_discovery_refresh",
            ),
            field(
                "officialX",
                "官方 X",
                3,
                "available" if social_urls else "missing",
                value=social_urls[0] if social_urls else "",
                source_name=identity.get("provider"),
                source_url=social_urls[0] if social_urls else "",
                updated_at=identity.get("reviewedAt"),
                next_task_id="source_discovery_refresh",
            ),
            field(
                "github",
                "GitHub",
                4,
                "available" if repo_urls else "missing",
                value=repo_urls[0] if repo_urls else "",
                source_name=identity.get("provider"),
                source_url=repo_urls[0] if repo_urls else "",
                updated_at=identity.get("reviewedAt"),
                next_task_id="source_discovery_refresh",
            ),
            field(
                "productDocs",
                "产品文档",
                3,
                "missing",
                next_task_id="source_discovery_refresh",
            ),
            field(
                "tokenomics",
                "代币经济",
                3,
                "missing",
                next_task_id="high_value_evidence_refresh",
            ),
            field(
                "team",
                "团队与组织",
                3,
                "missing",
                next_task_id="source_discovery_refresh",
            ),
        ],
        "asset": [
            field(
                "tradableAsset",
                "可交易资产",
                5,
                "available" if discovery.get("symbol") else "missing",
                value=discovery.get("symbol"),
                source_name=market_source or "链上发现",
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="identity_refresh",
            ),
            field(
                "assetIdentity",
                "资产身份",
                5,
                identity_status,
                value=identity.get("valueCaptureStatus"),
                source_name=identity.get("provider"),
                updated_at=identity.get("reviewedAt"),
                next_task_id="identity_refresh",
            ),
            field(
                "network",
                "所在链或网络",
                4,
                "verified" if discovery.get("networkName") else "missing",
                value=discovery.get("networkName"),
                source_name="链上发现",
                source_url=discovery.get("explorerUrl"),
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="contract_refresh",
            ),
            field(
                "contract",
                "代币合约",
                6,
                contract_status,
                value=discovery.get("contractAddress"),
                source_name=identity.get("provider") or "链上发现",
                source_url=discovery.get("explorerUrl"),
                updated_at=identity.get("reviewedAt")
                or discovery.get("lastSeenAt"),
                next_task_id="contract_refresh",
            ),
            field(
                "contractRisk",
                "合约风险核验",
                5,
                "verified"
                if discovery.get("contractRisk") not in {None, "", "unknown"}
                else "missing",
                value=discovery.get("contractRisk"),
                source_name=market_source,
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="contract_refresh",
            ),
        ],
        "market": [
            field(
                "marketSnapshot",
                "价格与估值快照",
                5,
                "verified"
                if any(
                    discovery.get(key) is not None
                    for key in ("priceUsd", "marketCapUsd")
                )
                else "missing",
                value=discovery.get("marketCapUsd")
                or discovery.get("priceUsd"),
                source_name=market_source,
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="market_refresh",
            ),
            field(
                "liquidity",
                "流动性",
                5,
                "verified"
                if discovery.get("liquidityUsd") is not None
                else "missing",
                value=discovery.get("liquidityUsd"),
                source_name=market_source,
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="market_refresh",
            ),
            field(
                "volume",
                "24小时成交",
                3,
                "verified"
                if discovery.get("volume24hUsd") is not None
                else "missing",
                value=discovery.get("volume24hUsd"),
                source_name=market_source,
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="market_refresh",
            ),
            field(
                "sellPath",
                "卖出路径",
                4,
                sell_status,
                value=sell_path,
                source_name=market_source,
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="contract_refresh",
            ),
            field(
                "slippage",
                "退出滑点",
                3,
                "verified"
                if discovery.get("estimatedExitSlippagePct") is not None
                else "missing",
                value=discovery.get("estimatedExitSlippagePct"),
                source_name=market_source,
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="contract_refresh",
            ),
        ],
        "activity": [
            field(
                "adoption",
                "链上采用",
                3,
                "available" if evidence_count else "missing",
                value=f"{evidence_count}条发现证据" if evidence_count else "",
                source_name=market_source,
                source_url=source_url,
                updated_at=discovery.get("lastSeenAt"),
                next_task_id="high_value_evidence_refresh",
            ),
            field(
                "governance",
                "治理提案",
                3,
                "missing",
                next_task_id="high_value_evidence_refresh",
            ),
            field(
                "codeActivity",
                "代码活动",
                2,
                "available" if repo_urls else "missing",
                value=repo_urls[0] if repo_urls else "",
                source_name=identity.get("provider"),
                source_url=repo_urls[0] if repo_urls else "",
                updated_at=identity.get("reviewedAt"),
                next_task_id="high_value_evidence_refresh",
            ),
            field(
                "audit",
                "审计与安全",
                2,
                "missing",
                next_task_id="high_value_evidence_refresh",
            ),
        ],
    }


def finalize_profile(fields_by_section):
    sections = []
    all_fields = []
    for section_id, label, maximum in SECTION_DEFINITIONS:
        fields = fields_by_section[section_id]
        score = round(sum(item["score"] for item in fields), 1)
        sections.append(
            {
                "id": section_id,
                "label": label,
                "score": score,
                "maxScore": maximum,
                "complete": sum(
                    item["status"] in {"verified", "available"}
                    for item in fields
                ),
                "total": len(fields),
                "fields": fields,
            }
        )
        all_fields.extend(fields)
    score = round(sum(item["score"] for item in all_fields))
    fields_by_id = {item["id"]: item for item in all_fields}
    critical_ids = (
        "projectIdentity",
        "officialWebsite",
        "tradableAsset",
        "contract",
        "liquidity",
        "sellPath",
    )
    missing_critical = [
        {
            "id": field_id,
            "label": fields_by_id[field_id]["label"],
            "status": fields_by_id[field_id]["status"],
            "nextTaskId": fields_by_id[field_id]["nextTaskId"],
            "nextTaskLabel": fields_by_id[field_id]["nextTaskLabel"],
        }
        for field_id in critical_ids
        if fields_by_id[field_id]["status"]
        not in {"verified", "available"}
    ]
    identity_blocked = fields_by_id["projectIdentity"]["status"] in {
        "pending",
        "missing",
        "conflict",
    }
    has_conflict = any(
        item["status"] == "conflict" for item in all_fields
    )
    if identity_blocked or has_conflict:
        grade = "identity_blocked"
        grade_label = "身份阻断"
    elif score >= 80 and not missing_critical:
        grade = "research_ready"
        grade_label = "可进入研究"
    elif score >= 55:
        grade = "partial"
        grade_label = "部分可用"
    else:
        grade = "thin"
        grade_label = "资料偏薄"

    next_missing = next(
        (
            item
            for item in all_fields
            if item["status"] in {"missing", "conflict", "pending"}
            and item["nextTaskId"]
        ),
        None,
    )
    return {
        "version": PROFILE_VERSION,
        "score": score,
        "grade": grade,
        "gradeLabel": grade_label,
        "automatedOnly": True,
        "boundary": PROFILE_BOUNDARY,
        "sections": sections,
        "missingCritical": missing_critical,
        "nextAutoTask": (
            {
                "taskId": next_missing["nextTaskId"],
                "taskLabel": next_missing["nextTaskLabel"],
                "fieldId": next_missing["id"],
                "fieldLabel": next_missing["label"],
                "href": (
                    "update-center.html?task="
                    + next_missing["nextTaskId"]
                ),
            }
            if next_missing
            else {
                "taskId": "",
                "taskLabel": "暂无关键补齐任务",
                "fieldId": "",
                "fieldLabel": "",
                "href": "",
            }
        ),
        "autoSourceCount": len(
            {
                item["sourceName"]
                for item in all_fields
                if item["sourceName"]
            }
        ),
    }


def build_automatic_profile(detail, source_discoveries=None):
    fields = (
        build_project_fields(detail, source_discoveries or [])
        if detail.get("recordType") == "project"
        else build_discovery_fields(detail)
    )
    return finalize_profile(fields)
