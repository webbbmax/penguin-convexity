#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contract_tradeability import user_environment
from init_db import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "fixtures" / "source-discovery-c1.1.json"
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "source-discovery-snapshot.js"
DEFAULT_CURSOR_STATE_PATH = PROJECT_ROOT / "data" / "source-discovery-cursors.json"
USER_AGENT = "Penguin-Convexity/1.1"
AUTO_PROMOTION_VERSION = "source-discovery-auto-promotion-c1.5.1"
MACHINE_CASE_RULE_VERSION = "convexity-auto-discovery-v1.0.0"
MIN_REGISTRY_TVL_USD = 1_000_000
MIN_WEBSITE_ONLY_TVL_USD = 50_000_000
GENERIC_PROJECT_NAMES = {
    "app",
    "blockchain",
    "bridge",
    "dao",
    "defi",
    "dex",
    "finance",
    "network",
    "protocol",
    "test",
    "token",
    "wallet",
    "web3",
}

SOURCE_DEFINITIONS = {
    "github": {
        "source_id": "discovery-github-repositories",
        "name": "GitHub 项目发现",
        "source_type": "project_discovery",
        "url": "https://api.github.com/search/repositories",
        "access_method": "认证 REST API",
    },
    "defillama": {
        "source_id": "discovery-defillama-protocols",
        "name": "DefiLlama 协议发现",
        "source_type": "project_discovery",
        "url": "https://api.llama.fi/protocols",
        "access_method": "公开 API",
    },
    "snapshot": {
        "source_id": "discovery-snapshot-spaces",
        "name": "Snapshot 治理空间发现",
        "source_type": "project_discovery",
        "url": "https://hub.snapshot.org/graphql",
        "access_method": "公开 GraphQL",
    },
    "cactus": {
        "source_id": "discovery-cactus-organizations",
        "name": "Cactus 治理组织发现",
        "source_type": "project_discovery",
        "url": "https://api.tally.xyz/query",
        "access_method": "认证 GraphQL",
    },
}

PLATFORM_DOMAINS = {
    "github.com",
    "snapshot.org",
    "tally.xyz",
    "twitter.com",
    "x.com",
    "discord.com",
    "discord.gg",
    "t.me",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_config(path=DEFAULT_CONFIG_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def request_json(url, method="GET", headers=None, body=None, timeout=30):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if payload else {}),
            **(headers or {}),
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response), dict(response.headers)
        except urllib.error.HTTPError as error:
            if error.code not in (403, 429, 500, 502, 503, 504) or attempt == 2:
                raise
            retry_after = error.headers.get("Retry-After")
            reset_at = error.headers.get("X-RateLimit-Reset")
            if retry_after:
                wait_seconds = float(retry_after)
            elif reset_at and str(reset_at).isdigit():
                wait_seconds = max(1, int(reset_at) - int(time.time()) + 1)
            else:
                wait_seconds = 30 * (attempt + 1) if error.code == 429 else 1.5 * (attempt + 1)
            time.sleep(min(wait_seconds, 65))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


def normalize_name(value):
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    tokens = [
        token
        for token in text.split()
        if token
        not in {
            "finance",
            "protocol",
            "network",
            "dao",
            "foundation",
            "labs",
            "lab",
            "app",
        }
        and not re.fullmatch(r"v\d+", token)
    ]
    return "".join(tokens) or re.sub(r"[^a-z0-9]+", "", text)


def domain_from_url(value):
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlparse(
            value if "://" in value else f"https://{value}"
        )
        domain = (parsed.hostname or "").lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return "" if domain in PLATFORM_DOMAINS else domain
    except ValueError:
        return ""


def discovery_record(
    provider,
    external_id,
    name,
    slug="",
    website_url="",
    repository_url="",
    social_url="",
    source_url="",
    category="",
    raw_project_type="",
    evidence=None,
):
    canonical_name = str(name or slug or external_id).strip()
    return {
        "provider": provider,
        "externalId": str(external_id),
        "canonicalName": canonical_name,
        "normalizedName": normalize_name(canonical_name),
        "slug": str(slug or ""),
        "websiteUrl": str(website_url or ""),
        "websiteDomain": domain_from_url(website_url),
        "repositoryUrl": str(repository_url or ""),
        "socialUrl": str(social_url or ""),
        "sourceUrl": str(source_url or ""),
        "category": str(category or ""),
        "rawProjectType": str(raw_project_type or ""),
        "evidence": evidence or {},
    }


def provider_result(
    provider,
    records,
    pages,
    boundary,
    upstream_limit="",
    error="",
    incomplete=False,
):
    return {
        "provider": provider,
        "records": records,
        "pages": pages,
        "boundary": boundary,
        "upstreamLimit": upstream_limit,
        "error": error,
        "incomplete": incomplete,
    }


def collect_defillama(config, timeout=30):
    url = SOURCE_DEFINITIONS["defillama"]["url"]
    try:
        payload, _headers = request_json(url, timeout=max(timeout, 30))
        records = []
        for item in payload if isinstance(payload, list) else []:
            slug = item.get("slug")
            name = item.get("name")
            if not slug or not name:
                continue
            github = item.get("github") or []
            if isinstance(github, str):
                github = [github]
            repository_url = (
                f"https://github.com/{github[0].strip('/')}" if github else ""
            )
            records.append(
                discovery_record(
                    "defillama",
                    slug,
                    name,
                    slug=slug,
                    website_url=item.get("url") or "",
                    repository_url=repository_url,
                    social_url=(
                        f"https://x.com/{item['twitter'].lstrip('@')}"
                        if item.get("twitter")
                        else ""
                    ),
                    source_url=f"https://defillama.com/protocol/{slug}",
                    category=item.get("category") or "",
                    raw_project_type="protocol",
                    evidence={
                        "chains": item.get("chains") or [],
                        "tvlUsd": item.get("tvl"),
                        "symbol": item.get("symbol") or "",
                        "address": item.get("address") or "",
                        "coinGeckoId": item.get("gecko_id") or "",
                        "coinMarketCapId": item.get("cmcId") or "",
                        "listed": True,
                    },
                )
            )
        return provider_result(
            "defillama",
            records,
            1,
            "全量读取接口当前返回的协议目录，不设置项目数量上限。",
        )
    except Exception as error:
        return provider_result(
            "defillama", [], 0, "协议目录读取失败。", error=f"{type(error).__name__}: {error}"
        )


def collect_snapshot(config, timeout=30):
    settings = config.get("snapshot") or {}
    lookback_days = max(1, int(settings.get("lookbackDays", 90)))
    cutoff = int(
        (datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp()
    )
    url = SOURCE_DEFINITIONS["snapshot"]["url"]
    query = (
        "query Recent($first:Int!,$skip:Int!){"
        "proposals(first:$first,skip:$skip,orderBy:\"created\",orderDirection:desc){"
        "id created space{id name}}}"
    )
    spaces = {}
    pages = 0
    skip = 0
    try:
        while True:
            payload, _headers = request_json(
                url,
                method="POST",
                body={
                    "query": query,
                    "variables": {"first": 100, "skip": skip},
                },
                timeout=timeout,
            )
            if payload.get("errors"):
                raise RuntimeError(payload["errors"][0].get("message", "GraphQL 查询失败"))
            proposals = (payload.get("data") or {}).get("proposals") or []
            pages += 1
            if not proposals:
                break
            reached_cutoff = False
            for proposal in proposals:
                created = int(proposal.get("created") or 0)
                if created < cutoff:
                    reached_cutoff = True
                    continue
                space = proposal.get("space") or {}
                space_id = space.get("id")
                if not space_id:
                    continue
                current = spaces.setdefault(
                    space_id,
                    {
                        "id": space_id,
                        "name": space.get("name") or space_id,
                        "proposalCount": 0,
                        "latestProposalAt": created,
                    },
                )
                current["proposalCount"] += 1
                current["latestProposalAt"] = max(current["latestProposalAt"], created)
            if reached_cutoff or len(proposals) < 100:
                break
            skip += len(proposals)
        records = [
            discovery_record(
                "snapshot",
                item["id"],
                item["name"],
                slug=item["id"],
                source_url=f"https://snapshot.org/#/{item['id']}",
                category="governance",
                raw_project_type="governance_space",
                evidence={
                    "proposalCountInWindow": item["proposalCount"],
                    "latestProposalAt": item["latestProposalAt"],
                },
            )
            for item in spaces.values()
        ]
        return provider_result(
            "snapshot",
            records,
            pages,
            f"覆盖最近 {lookback_days} 天出现提案的治理空间；不限制空间数量。",
        )
    except Exception as error:
        return provider_result(
            "snapshot",
            [
                discovery_record(
                    "snapshot",
                    item["id"],
                    item["name"],
                    slug=item["id"],
                    source_url=f"https://snapshot.org/#/{item['id']}",
                    category="governance",
                    raw_project_type="governance_space",
                    evidence={
                        "proposalCountInWindow": item["proposalCount"],
                        "latestProposalAt": item["latestProposalAt"],
                    },
                )
                for item in spaces.values()
            ],
            pages,
            f"最近 {lookback_days} 天治理空间采集未完整结束，已保留成功页。",
            error=f"{type(error).__name__}: {error}",
        )


def collect_cactus(config, timeout=30):
    url = SOURCE_DEFINITIONS["cactus"]["url"]
    api_key = user_environment("CACTUS_TALLY_API_KEY")
    if not api_key:
        return provider_result(
            "cactus", [], 0, "需要本机 Cactus API 密钥。", error="未配置 Cactus API 密钥"
        )
    only_with_proposals = bool(
        (config.get("cactus") or {}).get(
            "includeOnlyOrganizationsWithProposals", True
        )
    )
    time_slice_seconds = max(
        15, int((config.get("cactus") or {}).get("timeSliceSeconds", 45))
    )
    request_interval_seconds = max(
        0.0,
        float((config.get("cactus") or {}).get("requestIntervalSeconds", 10.5)),
    )
    query = (
        "query Organizations($input: OrganizationsInput!) { "
        "organizations(input: $input) { nodes { ... on Organization { "
        "id name slug proposalsCount hasActiveProposals chainIds "
        "} } pageInfo { lastCursor } } }"
    )
    records = []
    pages = 0
    try:
        cursor_state = json.loads(
            DEFAULT_CURSOR_STATE_PATH.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cursor_state = {}
    cursor = str(cursor_state.get("cactusAfterCursor") or "")
    resumed = bool(cursor)
    seen_cursors = set()
    started_clock = time.monotonic()

    def save_cursor(next_cursor):
        DEFAULT_CURSOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "cactusAfterCursor": next_cursor,
            "updatedAt": utc_now(),
            "cycleCompletedAt": (
                utc_now()
                if not next_cursor
                else cursor_state.get("cycleCompletedAt")
            ),
        }
        temporary = DEFAULT_CURSOR_STATE_PATH.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(DEFAULT_CURSOR_STATE_PATH)

    try:
        while True:
            page = {"limit": 20}
            if cursor:
                page["afterCursor"] = cursor
            payload, _headers = request_json(
                url,
                method="POST",
                headers={"Api-Key": api_key},
                body={
                    "query": query,
                    "variables": {
                        "input": {
                            "page": page,
                            "sort": {"sortBy": "id", "isDescending": False},
                        }
                    },
                },
                timeout=timeout,
            )
            if payload.get("errors"):
                raise RuntimeError(payload["errors"][0].get("message", "GraphQL 查询失败"))
            organizations = (payload.get("data") or {}).get("organizations") or {}
            nodes = organizations.get("nodes") or []
            pages += 1
            if not nodes:
                save_cursor("")
                break
            for item in nodes:
                proposals = int(item.get("proposalsCount") or 0)
                if only_with_proposals and proposals <= 0:
                    continue
                slug = item.get("slug") or item.get("id")
                records.append(
                    discovery_record(
                        "cactus",
                        item["id"],
                        item.get("name") or slug,
                        slug=slug,
                        source_url=f"https://www.tally.xyz/gov/{slug}",
                        category="governance",
                        raw_project_type="governance_organization",
                        evidence={
                            "proposalsCount": proposals,
                            "hasActiveProposals": bool(item.get("hasActiveProposals")),
                            "chainIds": item.get("chainIds") or [],
                        },
                    )
                )
            next_cursor = (organizations.get("pageInfo") or {}).get("lastCursor") or ""
            if len(nodes) < 20 or not next_cursor or next_cursor in seen_cursors:
                save_cursor("")
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            save_cursor(cursor)
            if time.monotonic() - started_clock >= time_slice_seconds:
                return provider_result(
                    "cactus",
                    records,
                    pages,
                    (
                        f"本轮按 {time_slice_seconds} 秒时间片续扫治理组织，"
                        "已保存分页游标，下次从断点继续；不设置项目数量上限。"
                    ),
                    "免费接口采用慢速时间片，完整一轮需要多次更新。",
                    incomplete=True,
                )
            if request_interval_seconds:
                time.sleep(request_interval_seconds)
        return provider_result(
            "cactus",
            records,
            pages,
            (
                "已完成一轮治理组织遍历；默认只保留至少存在一项提案的组织，"
                f"本轮{'从断点续扫' if resumed else '从头扫描'}，不设置项目数量上限。"
            ),
        )
    except Exception as error:
        return provider_result(
            "cactus",
            records,
            pages,
            "治理组织分页未完整结束，已保留成功页。",
            error=f"{type(error).__name__}: {error}",
        )


def collect_github(config, timeout=30):
    settings = config.get("github") or {}
    lookback_days = max(1, int(settings.get("lookbackDays", 7)))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
    topics = settings.get("topics") or ["defi", "blockchain", "web3"]
    minimum_stars = max(0, int(settings.get("minimumStars", 0)))
    token = user_environment("BUYI_GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    records_by_repo = {}
    pages = 0
    upstream_caps = []
    try:
        for topic in topics:
            qualifier = (
                f"topic:{topic} pushed:>={cutoff} archived:false fork:false "
                f"stars:>={minimum_stars}"
            )
            page = 1
            while True:
                url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
                    {
                        "q": qualifier,
                        "sort": "updated",
                        "order": "desc",
                        "per_page": 100,
                        "page": page,
                    }
                )
                payload, response_headers = request_json(
                    url, headers=headers, timeout=timeout
                )
                items = payload.get("items") or []
                pages += 1
                for item in items:
                    full_name = item.get("full_name")
                    if not full_name:
                        continue
                    records_by_repo[full_name.lower()] = discovery_record(
                        "github",
                        full_name.lower(),
                        item.get("name") or full_name,
                        slug=full_name,
                        website_url=item.get("homepage") or "",
                        repository_url=item.get("html_url") or f"https://github.com/{full_name}",
                        source_url=item.get("html_url") or f"https://github.com/{full_name}",
                        category=topic,
                        raw_project_type="repository",
                        evidence={
                            "owner": (item.get("owner") or {}).get("login"),
                            "description": item.get("description") or "",
                            "stars": item.get("stargazers_count") or 0,
                            "pushedAt": item.get("pushed_at"),
                            "topics": item.get("topics") or [],
                        },
                    )
                total = int(payload.get("total_count") or 0)
                searchable_total = min(total, 1000)
                if total > 1000:
                    message = f"{topic}: GitHub Search 仅开放前1000条"
                    if message not in upstream_caps:
                        upstream_caps.append(message)
                if not items or page * 100 >= searchable_total:
                    break
                page += 1
                remaining = int(response_headers.get("X-RateLimit-Remaining") or 1)
                if remaining <= 1:
                    reset_at = int(response_headers.get("X-RateLimit-Reset") or 0)
                    wait_seconds = max(0, reset_at - int(time.time()) + 1)
                    if wait_seconds > 60:
                        upstream_caps.append(f"{topic}: 本次认证检索配额已用尽")
                        break
                    time.sleep(wait_seconds)
        return provider_result(
            "github",
            list(records_by_repo.values()),
            pages,
            f"覆盖最近 {lookback_days} 天活跃且带指定主题的仓库；不设置站内项目数量上限。",
            "；".join(upstream_caps),
        )
    except Exception as error:
        return provider_result(
            "github",
            list(records_by_repo.values()),
            pages,
            f"最近 {lookback_days} 天主题仓库采集未完整结束，已保留成功页。",
            "；".join(upstream_caps),
            f"{type(error).__name__}: {error}",
        )


COLLECTORS = {
    "github": collect_github,
    "defillama": collect_defillama,
    "snapshot": collect_snapshot,
    "cactus": collect_cactus,
}


def collect_source_discoveries(config=None, timeout=30, providers=None):
    config = config or load_config()
    selected = list(providers or COLLECTORS)
    results = []
    with ThreadPoolExecutor(max_workers=len(selected)) as executor:
        futures = {
            executor.submit(COLLECTORS[provider], config, timeout): provider
            for provider in selected
        }
        for future in as_completed(futures):
            results.append(future.result())
    records = [
        record
        for result in results
        for record in result["records"]
    ]
    source_stats = {
        result["provider"]: {
            "collected": len(result["records"]),
            "pages": result["pages"],
            "failed": 1 if result["error"] else 0,
            "boundary": result["boundary"],
            "upstreamLimit": result["upstreamLimit"],
            "incomplete": bool(result.get("incomplete")),
        }
        for result in results
    }
    errors = [
        {
            "provider": result["provider"],
            "error": result["error"],
            "partialCount": len(result["records"]),
        }
        for result in results
        if result["error"]
    ]
    return {
        "version": config.get("version", "C1.1-05"),
        "records": records,
        "sourceStats": source_stats,
        "errors": errors,
    }


def source_discovery_id(source_id, external_id):
    digest = hashlib.sha256(f"{source_id}|{external_id}".encode("utf-8")).hexdigest()[:24]
    return f"source-discovery-{digest}"


def register_sources(connection, now):
    for definition in SOURCE_DEFINITIONS.values():
        connection.execute(
            """
            INSERT INTO sources (
              source_id, name, source_type, url, access_method, scope, confidence,
              conflict_risk, status, schedule_text, last_checked_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'convexity_discovery', '中', '中', 'active',
                    '手动更新', ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              name = excluded.name, url = excluded.url,
              access_method = excluded.access_method, status = 'active',
              last_checked_at = excluded.last_checked_at,
              updated_at = excluded.updated_at
            """,
            (
                definition["source_id"],
                definition["name"],
                definition["source_type"],
                definition["url"],
                definition["access_method"],
                now,
                now,
                now,
            ),
        )


def project_maps(connection):
    by_domain = {}
    by_name = {}
    by_repo = {}
    rows = list(connection.execute("SELECT * FROM projects WHERE identity_status != 'rejected'"))
    for project in rows:
        if project["website_domain"]:
            by_domain[project["website_domain"].lower()] = project["project_id"]
        normalized = normalize_name(project["canonical_name"])
        if normalized:
            by_name.setdefault(normalized, []).append(project["project_id"])
        if project["official_repo"]:
            by_repo[project["official_repo"].lower().rstrip("/")] = project["project_id"]
    return rows, by_domain, by_name, by_repo


def match_existing_project(record, by_domain, by_name, by_repo):
    domain = record["websiteDomain"].lower()
    repo = record["repositoryUrl"].lower().rstrip("/")
    normalized = record["normalizedName"]
    if domain and domain in by_domain:
        return by_domain[domain], "官网域名与已有项目完全一致", "high"
    if repo and repo in by_repo:
        return by_repo[repo], "官方仓库与已有项目完全一致", "high"
    matches = by_name.get(normalized) or []
    if len(matches) == 1:
        return "", "名称与已有项目相同，但缺少官网域名或官方仓库吻合", "low"
    if len(matches) > 1:
        return "", "标准化名称对应多个已有项目，需人工复核", "low"
    return "", "", "low"


def cluster_key_for(record, matched_project_id):
    if matched_project_id:
        return f"project:{matched_project_id}"
    if record["normalizedName"]:
        return f"name:{record['normalizedName']}"
    if record["websiteDomain"]:
        return f"domain:{record['websiteDomain']}"
    return f"source:{record['provider']}:{record['externalId']}"


def parse_evidence_payload(raw_value):
    try:
        payload = json.loads(raw_value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def source_cluster_promotion(rows):
    providers = {row["source_id"] for row in rows}
    normalized_names = {
        row["normalized_name"] for row in rows if row["normalized_name"]
    }
    defillama_rows = [
        row
        for row in rows
        if row["source_id"] == SOURCE_DEFINITIONS["defillama"]["source_id"]
    ]
    registry_rows = sorted(
        defillama_rows,
        key=lambda row: float(
            parse_evidence_payload(row["evidence_json"]).get("tvlUsd") or 0
        ),
        reverse=True,
    )
    registry_row = registry_rows[0] if registry_rows else None
    canonical_name = (
        registry_row["canonical_name"] if registry_row else rows[0]["canonical_name"]
    )
    canonical_name = re.sub(
        r"\s+v\d+$", "", canonical_name, flags=re.IGNORECASE
    ).strip()
    normalized = normalize_name(canonical_name)
    website_row = next(
        (row for row in registry_rows if row["website_domain"]),
        next((row for row in rows if row["website_domain"]), None),
    )
    repository_row = next(
        (row for row in registry_rows if row["repository_url"]),
        None,
    )
    tvl_usd = (
        float(parse_evidence_payload(registry_row["evidence_json"]).get("tvlUsd") or 0)
        if registry_row
        else 0
    )
    domain_sources = {}
    for row in rows:
        if row["website_domain"]:
            domain_sources.setdefault(row["website_domain"], set()).add(
                row["source_id"]
            )
    shared_official_domain = any(
        len(source_ids) >= 2 for source_ids in domain_sources.values()
    )
    governance_source_ids = {
        SOURCE_DEFINITIONS["snapshot"]["source_id"],
        SOURCE_DEFINITIONS["cactus"]["source_id"],
    }
    has_governance_confirmation = bool(providers & governance_source_ids)
    if (
        len(normalized) < 3
        or normalized in GENERIC_PROJECT_NAMES
        or len(normalized_names) > 1
    ):
        return {
            "eligible": False,
            "reason": "项目名称过于宽泛或同一归因组出现多个标准化名称。",
        }

    has_website = bool(website_row)
    has_repository = bool(repository_row)
    registry_anchored = bool(
        defillama_rows
        and has_website
        and (
            (has_repository and tvl_usd >= MIN_REGISTRY_TVL_USD)
            or tvl_usd >= MIN_WEBSITE_ONLY_TVL_USD
        )
    )
    cross_source_anchored = bool(
        len(providers) >= 2
        and has_website
        and (
            shared_official_domain
            or (defillama_rows and has_governance_confirmation)
        )
    )
    if not registry_anchored and not cross_source_anchored:
        return {
            "eligible": False,
            "reason": (
                "尚未同时满足结构化协议登记、采用规模、官网/代码锚点"
                "或两个独立来源交叉印证。"
            ),
        }

    identity_status = (
        "verified"
        if shared_official_domain or (registry_anchored and has_repository)
        else "pending"
    )
    reason = (
        (
            f"{len(providers)} 个独立来源交叉印证，"
            "且共享同一官网域名。"
        )
        if shared_official_domain
        else (
            f"{len(providers)} 个独立来源交叉发现，"
            "其中包含协议登记与治理记录。"
        )
        if cross_source_anchored and not registry_anchored
        else (
            "DefiLlama 结构化协议登记与官网存在，"
            f"{'并取得登记仓库，' if has_repository else ''}"
            f"当前 TVL 快照约为 {tvl_usd:,.0f} 美元。"
        )
    )
    return {
        "eligible": True,
        "canonicalName": canonical_name,
        "websiteDomain": website_row["website_domain"] if website_row else "",
        "repositoryUrl": (
            repository_row["repository_url"] if repository_row else ""
        ),
        "identityStatus": identity_status,
        "sourceCount": len(providers),
        "recordCount": len(rows),
        "tvlUsd": tvl_usd,
        "reason": reason,
    }


def auto_promote_source_clusters(connection, now, stable_id):
    grouped = {}
    for row in connection.execute(
        """
        SELECT *
        FROM source_discoveries
        WHERE status = 'active'
        ORDER BY cluster_key, source_id, external_id
        """
    ):
        grouped.setdefault(row["cluster_key"], []).append(row)

    summary = {
        "projectsCreated": 0,
        "casesCreated": 0,
        "recordsLinked": 0,
        "eligibleClusters": 0,
        "skippedClusters": 0,
    }
    for cluster_key, rows in grouped.items():
        linked_projects = {
            row["matched_project_id"]
            for row in rows
            if row["matched_project_id"]
        }
        if len(linked_projects) > 1:
            summary["skippedClusters"] += 1
            continue
        promotion = source_cluster_promotion(rows)
        if not promotion["eligible"]:
            summary["skippedClusters"] += 1
            continue
        summary["eligibleClusters"] += 1
        project_id = (
            next(iter(linked_projects))
            if linked_projects
            else stable_id("source-project", cluster_key)
        )
        project_exists = connection.execute(
            "SELECT 1 FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        first_seen_at = min(row["first_seen_at"] for row in rows)
        connection.execute(
            """
            INSERT INTO projects (
              project_id, canonical_name, website_domain, official_repo,
              team_summary, identity_status, first_seen_at,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
              canonical_name = CASE
                WHEN projects.canonical_name = '' THEN excluded.canonical_name
                ELSE projects.canonical_name
              END,
              website_domain = CASE
                WHEN projects.website_domain = '' THEN excluded.website_domain
                ELSE projects.website_domain
              END,
              official_repo = CASE
                WHEN projects.official_repo = '' THEN excluded.official_repo
                ELSE projects.official_repo
              END,
              identity_status = CASE
                WHEN excluded.identity_status = 'verified' THEN 'verified'
                ELSE projects.identity_status
              END,
              updated_at = excluded.updated_at
            """,
            (
                project_id,
                promotion["canonicalName"],
                promotion["websiteDomain"],
                promotion["repositoryUrl"],
                promotion["identityStatus"],
                first_seen_at,
                now,
                now,
            ),
        )
        if not project_exists:
            summary["projectsCreated"] += 1

        case = connection.execute(
            """
            SELECT case_id
            FROM candidate_cases
            WHERE project_id = ?
            ORDER BY updated_at DESC, case_id
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        case_id = (
            case["case_id"]
            if case
            else stable_id("auto-case", project_id, "project-seed")
        )
        if not case:
            connection.execute(
                """
                INSERT INTO candidate_cases (
                  case_id, project_id, asset_id, title, maturity_level,
                  workflow_state, risk_level, remaining_convexity,
                  ignition_proximity, tradeability_status, liquidity_grade,
                  convexity_source, action_stage, value_capture_grade,
                  current_thesis, invalidation, next_review_at, rule_version,
                  created_at, updated_at
                )
                VALUES (?, ?, NULL, ?, 'L0', 'shadow_signal', 'unknown',
                        'unknown', 'unknown', 'unknown', 'unknown', '',
                        '只观察', 'unknown', ?, ?, NULL, ?, ?, ?)
                """,
                (
                    case_id,
                    project_id,
                    f"{promotion['canonicalName']} 机器发现观察档案",
                    (
                        f"{promotion['reason']} 当前只确认项目主体线索，"
                        "尚未识别可投资资产、价值捕获、凸性来源和点火条件。"
                    ),
                    (
                        "若官网或代码归属发生冲突、独立登记无法确认项目主体，"
                        "或后续未找到可承接价值的资产，则撤销该观察档案。"
                    ),
                    MACHINE_CASE_RULE_VERSION,
                    now,
                    now,
                ),
            )
            summary["casesCreated"] += 1
            connection.execute(
                """
                INSERT OR IGNORE INTO state_transitions (
                  transition_id, case_id, from_state, to_state, reason,
                  evidence_ids_json, rule_version, actor, transitioned_at
                )
                VALUES (?, ?, 'source_discovery', 'shadow_signal', ?,
                        '[]', ?, 'machine_discovery', ?)
                """,
                (
                    stable_id(
                        "source-promotion-transition",
                        case_id,
                        AUTO_PROMOTION_VERSION,
                    ),
                    case_id,
                    promotion["reason"],
                    AUTO_PROMOTION_VERSION,
                    now,
                ),
            )

        attribution_status = (
            "verified"
            if promotion["identityStatus"] == "verified"
            else "corroborated"
        )
        attribution_confidence = (
            "high"
            if promotion["identityStatus"] == "verified"
            else "medium"
        )
        attribution_reason = (
            f"C1.5-02 机器建档：{promotion['reason']} "
            "资产与价值捕获仍需后续自动补齐。"
        )
        for row in rows:
            connection.execute(
                """
                UPDATE source_discoveries
                SET matched_project_id = ?, project_identity_status = ?,
                    attribution_confidence = ?, attribution_reason = ?,
                    cluster_key = ?, updated_at = ?
                WHERE source_discovery_id = ?
                """,
                (
                    project_id,
                    attribution_status,
                    attribution_confidence,
                    attribution_reason,
                    f"project:{project_id}",
                    now,
                    row["source_discovery_id"],
                ),
            )
            summary["recordsLinked"] += 1
            evidence_payload = parse_evidence_payload(row["evidence_json"])
            evidence_summary = (
                f"{row['canonical_name']} 由 {row['source_id']} 发现；"
                f"分类 {row['category'] or '未分类'}。"
            )
            if evidence_payload.get("tvlUsd") is not None:
                evidence_summary += (
                    f" 来源记录 TVL 约 "
                    f"{float(evidence_payload['tvlUsd'] or 0):,.0f} 美元。"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO evidence_items (
                  evidence_id, project_id, asset_id, raw_event_id,
                  evidence_type, stance, fact_boundary, confidence,
                  observed_at, expires_at, source_id, source_url,
                  summary, created_at
                )
                VALUES (?, ?, NULL, NULL, 'project_discovery', 'neutral',
                        ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    stable_id(
                        "source-promotion-evidence",
                        project_id,
                        row["source_id"],
                        row["external_id"],
                    ),
                    project_id,
                    (
                        "high_confidence_inference"
                        if row["source_id"]
                        == SOURCE_DEFINITIONS["defillama"]["source_id"]
                        else "unverified_signal"
                    ),
                    (
                        "中"
                        if row["source_id"]
                        == SOURCE_DEFINITIONS["defillama"]["source_id"]
                        else "低"
                    ),
                    row["last_seen_at"],
                    row["source_id"],
                    row["source_url"],
                    evidence_summary,
                    now,
                ),
            )
    return summary


def persist_source_discoveries(connection, bundle, run_id, now, stable_id):
    register_sources(connection, now)
    _projects, by_domain, by_name, by_repo = project_maps(connection)
    inserted = 0
    updated = 0
    matched_existing = 0
    records_for_attribution = []
    for record in bundle["records"]:
        definition = SOURCE_DEFINITIONS[record["provider"]]
        source_id = definition["source_id"]
        matched_project_id, reason, confidence = match_existing_project(
            record, by_domain, by_name, by_repo
        )
        cluster_key = cluster_key_for(record, matched_project_id)
        record_id = source_discovery_id(source_id, record["externalId"])
        previous = connection.execute(
            "SELECT source_discovery_id FROM source_discoveries WHERE source_id = ? AND external_id = ?",
            (source_id, record["externalId"]),
        ).fetchone()
        if previous:
            updated += 1
        else:
            inserted += 1
        connection.execute(
            """
            INSERT INTO source_discoveries (
              source_discovery_id, source_id, external_id, canonical_name,
              normalized_name, slug, website_url, website_domain,
              repository_url, social_url, source_url, category,
              raw_project_type, cluster_key, first_seen_at, last_seen_at,
              last_run_id, matched_project_id, project_identity_status,
              asset_identity_status, value_capture_status,
              attribution_confidence, attribution_reason, evidence_json,
              status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'pending', 'not_identified', 'unknown', ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(source_id, external_id) DO UPDATE SET
              canonical_name = excluded.canonical_name,
              normalized_name = excluded.normalized_name,
              slug = excluded.slug,
              website_url = excluded.website_url,
              website_domain = excluded.website_domain,
              repository_url = excluded.repository_url,
              social_url = excluded.social_url,
              source_url = excluded.source_url,
              category = excluded.category,
              raw_project_type = excluded.raw_project_type,
              cluster_key = excluded.cluster_key,
              last_seen_at = excluded.last_seen_at,
              last_run_id = excluded.last_run_id,
              matched_project_id = excluded.matched_project_id,
              attribution_confidence = excluded.attribution_confidence,
              attribution_reason = excluded.attribution_reason,
              evidence_json = excluded.evidence_json,
              status = 'active',
              updated_at = excluded.updated_at
            """,
            (
                record_id,
                source_id,
                record["externalId"],
                record["canonicalName"],
                record["normalizedName"],
                record["slug"],
                record["websiteUrl"],
                record["websiteDomain"],
                record["repositoryUrl"],
                record["socialUrl"],
                record["sourceUrl"],
                record["category"],
                record["rawProjectType"],
                cluster_key,
                now,
                now,
                run_id,
                matched_project_id or None,
                confidence,
                reason,
                json.dumps(record["evidence"], ensure_ascii=False),
                now,
                now,
            ),
        )
        records_for_attribution.append((record_id, cluster_key, matched_project_id))
        matched_existing += bool(matched_project_id)

    cluster_sources = {}
    for row in connection.execute(
        """
        SELECT cluster_key, source_id, matched_project_id
        FROM source_discoveries
        WHERE status = 'active'
        """
    ):
        item = cluster_sources.setdefault(
            row["cluster_key"], {"sources": set(), "projects": set()}
        )
        item["sources"].add(row["source_id"])
        if row["matched_project_id"]:
            item["projects"].add(row["matched_project_id"])

    corroborated = 0
    pending = 0
    conflicts = 0
    for cluster_key, cluster in cluster_sources.items():
        if len(cluster["projects"]) > 1:
            status, confidence = "conflict", "low"
            reason = "跨源记录指向多个已有项目，禁止自动归属"
            conflicts += 1
        elif len(cluster["projects"]) == 1:
            status, confidence = "verified", "high"
            reason = "已与凸性项目主体库建立确定匹配"
        elif len(cluster["sources"]) >= 2:
            status, confidence = "corroborated", "medium"
            reason = f"由 {len(cluster['sources'])} 个独立来源交叉发现，仍未建立资产身份"
            corroborated += 1
        else:
            status, confidence = "pending", "low"
            reason = "仅有单一来源，等待官网、资产登记或第二独立来源补证"
            pending += 1
        connection.execute(
            """
            UPDATE source_discoveries
            SET project_identity_status = ?, attribution_confidence = ?,
                attribution_reason = CASE
                  WHEN matched_project_id IS NOT NULL AND ? = 'verified'
                    THEN attribution_reason
                  ELSE ?
                END,
                updated_at = ?
            WHERE cluster_key = ?
            """,
            (status, confidence, status, reason, now, cluster_key),
        )

    auto_promotion = auto_promote_source_clusters(connection, now, stable_id)

    for project_id in {
        row["matched_project_id"]
        for row in connection.execute(
            """
            SELECT DISTINCT matched_project_id
            FROM source_discoveries
            WHERE matched_project_id IS NOT NULL
            """
        )
    }:
        assets = list(
            connection.execute(
                "SELECT identity_status, capture_grade FROM assets WHERE project_id = ?",
                (project_id,),
            )
        )
        asset_status = (
            "verified"
            if any(item["identity_status"] == "verified" for item in assets)
            else "pending"
            if assets
            else "not_identified"
        )
        value_status = (
            "verified"
            if any(item["capture_grade"] in ("A", "B", "C") for item in assets)
            else "unknown"
        )
        connection.execute(
            """
            UPDATE source_discoveries
            SET asset_identity_status = ?, value_capture_status = ?, updated_at = ?
            WHERE matched_project_id = ?
            """,
            (asset_status, value_status, now, project_id),
        )

    for provider, stat in bundle["sourceStats"].items():
        definition = SOURCE_DEFINITIONS[provider]
        status = (
            "partial_success"
            if stat.get("incomplete") or (stat["failed"] and stat["collected"])
            else "failed"
            if stat["failed"]
            else "no_data"
            if not stat["collected"]
            else "success"
        )
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                stable_id("run-source", run_id, definition["source_id"]),
                run_id,
                definition["source_id"],
                provider,
                status,
                now,
                now,
                stat["collected"],
                connection.execute(
                    """
                    SELECT COUNT(*) FROM source_discoveries
                    WHERE source_id = ? AND last_run_id = ?
                      AND project_identity_status IN ('verified', 'corroborated')
                    """,
                    (definition["source_id"], run_id),
                ).fetchone()[0],
                stat["failed"],
                json.dumps(
                    {
                        "pages": stat["pages"],
                        "boundary": stat["boundary"],
                        "upstreamLimit": stat["upstreamLimit"],
                        "incomplete": bool(stat.get("incomplete")),
                        "siteProjectLimit": None,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        summary = (
            f"{definition['name']} 本次读取 {stat['collected']} 条项目级记录，"
            f"请求 {stat['pages']} 页。"
        )
        payload = {
            "summary": summary,
            "boundary": stat["boundary"],
            "upstreamLimit": stat["upstreamLimit"],
            "changes": [],
        }
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url, excerpt,
              project_hint, asset_hint, chain_hint, event_type,
              raw_payload_json, status
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, '', '', '',
                    'project_source_discovery', ?, 'normalized')
            """,
            (
                stable_id("raw-source-discovery", run_id, provider),
                definition["source_id"],
                run_id,
                f"{run_id}:{provider}:summary",
                now,
                hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                definition["url"],
                summary,
                json.dumps(payload, ensure_ascii=False),
            ),
        )

    for error in bundle["errors"]:
        source_id = SOURCE_DEFINITIONS[error["provider"]]["source_id"]
        connection.execute(
            """
            INSERT INTO run_errors (
              error_id, run_id, source_id, task_name, error_type, message,
              retryable, retry_status, attempts, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, 'source_error', ?, 1, 'not_requested', 1, ?, ?)
            """,
            (
                stable_id("source-discovery-error", run_id, source_id),
                run_id,
                source_id,
                f"项目发现 · {SOURCE_DEFINITIONS[error['provider']]['name']}",
                error["error"],
                now,
                now,
            ),
        )

    return {
        "collected": len(bundle["records"]),
        "inserted": inserted,
        "updated": updated,
        "matchedExisting": matched_existing,
        "corroboratedClusters": corroborated,
        "pendingClusters": pending,
        "conflictClusters": conflicts,
        "autoPromotedProjects": auto_promotion["projectsCreated"],
        "autoCreatedCases": auto_promotion["casesCreated"],
        "autoLinkedRecords": auto_promotion["recordsLinked"],
        "autoEligibleClusters": auto_promotion["eligibleClusters"],
        "autoSkippedClusters": auto_promotion["skippedClusters"],
        "failed": len(bundle["errors"]),
        "errors": bundle["errors"],
    }


def build_source_discovery_snapshot(connection):
    source_ids = [item["source_id"] for item in SOURCE_DEFINITIONS.values()]
    placeholders = ",".join("?" for _ in source_ids)
    latest_run = connection.execute(
        f"""
        SELECT run.*
        FROM runs run
        WHERE EXISTS (
          SELECT 1 FROM run_source_stats stat
          WHERE stat.run_id = run.run_id
            AND stat.source_id IN ({placeholders})
        )
        ORDER BY run.started_at DESC, run.run_id DESC
        LIMIT 1
        """,
        source_ids,
    ).fetchone()
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT discovery.*, source.name AS source_name,
                   project.canonical_name AS matched_project_name,
                   project.website_domain AS matched_website_domain,
                   project.official_repo AS matched_official_repo
            FROM source_discoveries discovery
            JOIN sources source ON source.source_id = discovery.source_id
            LEFT JOIN projects project
              ON project.project_id = discovery.matched_project_id
            WHERE discovery.status = 'active'
            ORDER BY discovery.canonical_name COLLATE NOCASE,
                     discovery.source_id, discovery.external_id
            """
        )
    ]
    machine_cases_by_project = {}
    for row in connection.execute(
        """
        SELECT project_id, case_id
        FROM candidate_cases
        WHERE rule_version = ?
        ORDER BY updated_at DESC, case_id DESC
        """,
        (MACHINE_CASE_RULE_VERSION,),
    ):
        machine_cases_by_project.setdefault(row["project_id"], row["case_id"])
    clusters = {}
    for row in rows:
        matched_project_id = row["matched_project_id"] or ""
        machine_case_id = machine_cases_by_project.get(matched_project_id, "")
        matched_website_domain = row["matched_website_domain"] or ""
        cluster = clusters.setdefault(
            row["cluster_key"],
            {
                "clusterKey": row["cluster_key"],
                "canonicalName": (
                    row["matched_project_name"] or row["canonical_name"]
                ),
                "websiteUrl": (
                    f"https://{matched_website_domain}"
                    if matched_website_domain
                    else row["website_url"]
                ),
                "repositoryUrl": (
                    row["matched_official_repo"] or row["repository_url"]
                ),
                "socialUrl": row["social_url"],
                "matchedProjectId": matched_project_id,
                "caseId": machine_case_id,
                "machinePromoted": bool(machine_case_id),
                "detailUrl": (
                    "project-detail.html?id="
                    + urllib.parse.quote(f"project:{matched_project_id}", safe="")
                    if machine_case_id
                    else ""
                ),
                "projectIdentityStatus": row["project_identity_status"],
                "assetIdentityStatus": row["asset_identity_status"],
                "valueCaptureStatus": row["value_capture_status"],
                "attributionConfidence": row["attribution_confidence"],
                "attributionReason": row["attribution_reason"],
                "categories": [],
                "sourceIds": [],
                "sourceNames": [],
                "sourceLinks": [],
                "recordCount": 0,
                "lastSeenAt": row["last_seen_at"],
            },
        )
        cluster["recordCount"] += 1
        cluster["lastSeenAt"] = max(cluster["lastSeenAt"], row["last_seen_at"])
        if row["category"] and row["category"] not in cluster["categories"]:
            cluster["categories"].append(row["category"])
        if row["source_id"] not in cluster["sourceIds"]:
            cluster["sourceIds"].append(row["source_id"])
            cluster["sourceNames"].append(row["source_name"])
            cluster["sourceLinks"].append(
                {
                    "sourceId": row["source_id"],
                    "sourceName": row["source_name"],
                    "url": row["source_url"],
                }
            )
        for key, column in (
            ("websiteUrl", "website_url"),
            ("repositoryUrl", "repository_url"),
            ("socialUrl", "social_url"),
        ):
            if not cluster[key] and row[column]:
                cluster[key] = row[column]

    sources = []
    for provider, definition in SOURCE_DEFINITIONS.items():
        latest_stat = connection.execute(
            """
            SELECT * FROM run_source_stats
            WHERE source_id = ?
            ORDER BY started_at DESC, run_source_stat_id DESC
            LIMIT 1
            """,
            (definition["source_id"],),
        ).fetchone()
        filter_summary = (
            json.loads(latest_stat["filter_reason_summary_json"])
            if latest_stat
            else {}
        )
        upstream_limit = "；".join(
            dict.fromkeys(
                part.strip()
                for part in str(filter_summary.get("upstreamLimit", "")).split("；")
                if part.strip()
            )
        )
        sources.append(
            {
                "provider": provider,
                **definition,
                "status": latest_stat["status"] if latest_stat else "never_run",
                "collected": latest_stat["collected_count"] if latest_stat else 0,
                "matched": latest_stat["matched_count"] if latest_stat else 0,
                "failed": latest_stat["failed_count"] if latest_stat else 0,
                "pages": filter_summary.get("pages", 0),
                "boundary": filter_summary.get("boundary", ""),
                "upstreamLimit": upstream_limit,
                "incomplete": bool(filter_summary.get("incomplete")),
            }
        )
    items = sorted(
        clusters.values(),
        key=lambda item: (
            {"verified": 0, "corroborated": 1, "conflict": 2, "pending": 3}.get(
                item["projectIdentityStatus"], 4
            ),
            -len(item["sourceIds"]),
            item["canonicalName"].lower(),
        ),
    )
    return {
        "version": "C1.5-02",
        "release": "C1.5",
        "generatedAt": utc_now(),
        "title": "发现召回与身份归因",
        "policy": (
            "不设置站内项目数量上限。满足结构化协议登记、采用规模和官网或代码锚点，"
            "或取得两个独立来源交叉印证的项目，无需人工放行即可建立只观察档案；"
            "项目身份、资产身份和价值捕获仍分别核验。"
        ),
        "latestRun": dict(latest_run) if latest_run else None,
        "counts": {
            "rawDiscoveries": len(rows),
            "clusters": len(items),
            "machineProjects": sum(item["machinePromoted"] for item in items),
            "machineCases": len(
                {
                    item["caseId"]
                    for item in items
                    if item["machinePromoted"] and item["caseId"]
                }
            ),
            "machineAssetNotIdentified": sum(
                item["machinePromoted"]
                and item["assetIdentityStatus"] == "not_identified"
                for item in items
            ),
            "matchedExisting": sum(
                item["projectIdentityStatus"] == "verified" for item in items
            ),
            "corroborated": sum(
                item["projectIdentityStatus"] == "corroborated" for item in items
            ),
            "pending": sum(
                item["projectIdentityStatus"] == "pending" for item in items
            ),
            "conflicts": sum(
                item["projectIdentityStatus"] == "conflict" for item in items
            ),
            "assetNotIdentified": sum(
                item["assetIdentityStatus"] == "not_identified" for item in items
            ),
            "valueCaptureUnknown": sum(
                item["valueCaptureStatus"] == "unknown" for item in items
            ),
        },
        "sources": sources,
        "items": items,
    }


def write_source_discovery_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_CONVEXITY_SOURCE_DISCOVERY = "
        + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def rebuild_source_discovery_snapshot(
    db_path=DEFAULT_DB_PATH, output_path=DEFAULT_SNAPSHOT_PATH
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_source_discovery_snapshot(connection)
        write_source_discovery_snapshot(snapshot, output_path)
        return snapshot
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成凸性项目发现与身份归因快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_source_discovery_snapshot(args.db, args.output)
    print(json.dumps(snapshot["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
