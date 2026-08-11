#!/usr/bin/env python3
import hashlib
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from contract_tradeability import user_environment


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "convexity.db"
ENRICHMENT_VERSION = "C1.4-03"
USER_AGENT = "Penguin-Convexity/1.4"
SOURCE_DEFINITION = {
    "source_id": "formal-project-research-materials",
    "name": "正式项目研究资料",
    "source_type": "official_material_discovery",
    "url": "multiple://official-websites-and-github",
    "access_method": "官网只读扫描与认证 GitHub REST API",
}

CATEGORY_DEFINITIONS = {
    "official_product_docs": {
        "label": "产品文档",
        "boundary": "已找到官方产品文档入口，仅证明资料存在，不证明产品已经采用。",
    },
    "official_tokenomics": {
        "label": "代币经济",
        "boundary": "项目方披露不等于代币已经形成价值捕获。",
    },
    "official_team_or_organization": {
        "label": "团队与组织",
        "boundary": "官方页面或代码组织不等于成员身份已经独立核验。",
    },
    "official_audit_or_security": {
        "label": "审计与安全",
        "boundary": "发现审计或安全材料不等于审计通过，也不代表协议没有风险。",
    },
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(*parts):
    digest = hashlib.sha256(
        "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    ).hexdigest()[:24]
    return f"formal-material-{digest}"


def clean_text(value, limit=240):
    text = " ".join(str(value or "").split())
    return text[:limit].strip()


def http_url(value):
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


def normalized_url(value, base_url=""):
    text = str(value or "").strip()
    if not text:
        return ""
    url = urllib.parse.urljoin(base_url, text)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )


def github_repository(value):
    url = http_url(value)
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def score_candidate(category, url, text=""):
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path).casefold()
    host = str(parsed.hostname or "").casefold()
    searchable = f"{host} {path} {clean_text(text, 300).casefold()}"

    if category == "official_product_docs":
        scores = (
            (10, "whitepaper"),
            (10, "litepaper"),
            (9, "documentation"),
            (8, "/docs"),
            (8, "docs."),
            (6, "developer"),
            (4, "readme"),
        )
    elif category == "official_tokenomics":
        scores = (
            (12, "tokenomics"),
            (11, "token-economics"),
            (8, "token economy"),
            (7, "token allocation"),
            (7, "token distribution"),
            (6, "token supply"),
            (6, "emission schedule"),
            (6, "unlock schedule"),
        )
    elif category == "official_team_or_organization":
        scores = (
            (12, "/team"),
            (10, "our team"),
            (9, "leadership"),
            (8, "founder"),
            (7, "contributors"),
            (6, "foundation"),
            (6, "core team"),
        )
    else:
        scores = (
            (12, "/audits"),
            (11, "/audit"),
            (10, "audit report"),
            (10, "bug-bounty"),
            (10, "bug bounty"),
            (9, "security audit"),
            (8, "security review"),
            (8, "security policy"),
            (7, "audited"),
        )
    return max((score for score, term in scores if term in searchable), default=0)


def classify_candidate(url, text=""):
    ranked = [
        (score_candidate(category, url, text), category)
        for category in CATEGORY_DEFINITIONS
    ]
    score, category = max(ranked)
    return (category, score) if score else ("", 0)


def github_path_allowed(category, path):
    lowered_path = str(path or "").casefold()
    path_parts = {part for part in lowered_path.split("/") if part}
    if path_parts & {
        "node_modules",
        "vendor",
        "third_party",
        "third-party",
        "submodules",
    }:
        return False
    if "lib" in path_parts and len(path_parts) > 2:
        return False
    suffix = Path(lowered_path).suffix
    basename = Path(lowered_path).name
    if category == "official_product_docs":
        return (
            suffix in {".md", ".mdx", ".rst", ".txt", ".pdf"}
            and not any(
                term in lowered_path
                for term in ("audit", "security", "bug-bounty", "bug_bounty")
            )
        )
    if category == "official_team_or_organization":
        return any(
            term in basename
            for term in ("team", "contributors", "authors", "people")
        )
    if category == "official_audit_or_security":
        return (
            "supply-chain/audits.toml" not in lowered_path
            and suffix in {".md", ".mdx", ".txt", ".pdf"}
            and (
                any(part.startswith("audit") for part in path_parts)
                or any(
                    term in lowered_path
                    for term in (
                        "security-review",
                        "security_review",
                        "bug-bounty",
                        "bug_bounty",
                        "security.md",
                    )
                )
            )
        )
    return True


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_parts = []
        self.description = ""
        self.links = []
        self._in_title = False
        self._current_link = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag.casefold() == "title":
            self._in_title = True
        elif tag.casefold() == "meta":
            name = str(
                attributes.get("name")
                or attributes.get("property")
                or ""
            ).casefold()
            if name in {"description", "og:description", "twitter:description"}:
                self.description = self.description or clean_text(
                    attributes.get("content")
                )
        elif tag.casefold() == "a" and attributes.get("href"):
            self._current_link = attributes["href"]
            self._current_text = []

    def handle_endtag(self, tag):
        if tag.casefold() == "title":
            self._in_title = False
        elif tag.casefold() == "a" and self._current_link:
            self.links.append(
                {
                    "href": self._current_link,
                    "text": clean_text(" ".join(self._current_text), 160),
                }
            )
            self._current_link = None
            self._current_text = []

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        if self._current_link:
            self._current_text.append(data)


def request_page(url, timeout=20):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        content_type = str(response.headers.get("Content-Type") or "").casefold()
        body = response.read(1_500_000)
    if "pdf" in content_type or final_url.casefold().endswith(".pdf"):
        return {
            "url": final_url,
            "title": Path(urllib.parse.urlparse(final_url).path).name,
            "description": "PDF 文档",
            "links": [],
        }
    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
    try:
        html = body.decode(charset, errors="replace")
    except LookupError:
        html = body.decode("utf-8", errors="replace")
    parser = PageParser()
    parser.feed(html)
    return {
        "url": final_url,
        "title": clean_text(" ".join(parser.title_parts), 180),
        "description": clean_text(parser.description, 260),
        "links": parser.links,
    }


def request_json(url, timeout=20):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = user_environment("BUYI_GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def latest_identity_reviews(connection):
    rows = connection.execute(
        """
        SELECT *
        FROM discovery_identity_reviews
        WHERE promoted_project_id IS NOT NULL OR matched_project_id IS NOT NULL
        ORDER BY reviewed_at DESC, identity_review_id DESC
        """
    )
    reviews = {}
    for row in rows:
        item = dict(row)
        for project_id in {
            item.get("promoted_project_id"),
            item.get("matched_project_id"),
        } - {None, ""}:
            reviews.setdefault(project_id, item)
    return reviews


def project_targets(connection):
    reviews = latest_identity_reviews(connection)
    evidence_by_project = {}
    for row in connection.execute(
        """
        SELECT project_id, evidence_type, fact_boundary, source_id,
               source_url, summary, observed_at
        FROM evidence_items
        WHERE project_id IS NOT NULL AND source_url != ''
        ORDER BY observed_at DESC, evidence_id DESC
        """
    ):
        item = dict(row)
        evidence_by_project.setdefault(item["project_id"], []).append(item)

    targets = []
    for row in connection.execute(
        """
        SELECT *
        FROM projects
        WHERE identity_status != 'rejected'
        ORDER BY canonical_name, project_id
        """
    ):
        project = dict(row)
        review = reviews.get(project["project_id"]) or {}
        evidence = evidence_by_project.get(project["project_id"], [])
        website_url = http_url(review.get("website_url"))
        if not website_url and project.get("website_domain"):
            website_url = normalized_url(project["website_domain"])
        if not website_url:
            website_url = next(
                (
                    item["source_url"]
                    for item in evidence
                    if item["evidence_type"] == "official_website"
                ),
                "",
            )

        repository = github_repository(project.get("official_repo"))
        if not repository:
            repository = next(
                (
                    github_repository(item["source_url"])
                    for item in evidence
                    if item["source_id"] == "evidence-github-official"
                    and github_repository(item["source_url"])
                ),
                "",
            )

        direct_links = []
        for item in evidence:
            category, score = classify_candidate(
                item["source_url"],
                item["summary"],
            )
            if category and score:
                direct_links.append(
                    {
                        "category": category,
                        "score": score + 2,
                        "url": item["source_url"],
                        "title": item["summary"],
                        "sourceKind": "existing_project_evidence",
                    }
                )
        targets.append(
            {
                "projectId": project["project_id"],
                "projectName": project["canonical_name"],
                "identityStatus": project["identity_status"],
                "websiteUrl": website_url,
                "repository": repository,
                "directLinks": direct_links,
            }
        )
    return targets


def website_candidates(target, timeout):
    website_url = target["websiteUrl"]
    if not website_url:
        return [], []
    try:
        page = request_page(website_url, timeout)
    except Exception as error:
        return [], [
            {
                "projectId": target["projectId"],
                "sourceUrl": website_url,
                "reason": f"{type(error).__name__}: {error}",
            }
        ]

    candidates = []
    for link in page["links"]:
        url = normalized_url(link["href"], page["url"])
        if not url:
            continue
        category, score = classify_candidate(url, link["text"])
        if not category:
            continue
        candidates.append(
            {
                "category": category,
                "score": score,
                "url": url,
                "title": link["text"],
                "sourceKind": "official_website",
            }
        )
    return candidates, []


def github_candidates(target, timeout):
    repository = target["repository"]
    if not repository:
        return [], []
    try:
        repo = request_json(
            f"https://api.github.com/repos/{repository}",
            timeout,
        )
        branch = repo.get("default_branch") or "main"
        tree = request_json(
            f"https://api.github.com/repos/{repository}/git/trees/"
            f"{urllib.parse.quote(branch, safe='')}?recursive=1",
            timeout,
        )
    except Exception as error:
        return [], [
            {
                "projectId": target["projectId"],
                "sourceUrl": f"https://github.com/{repository}",
                "reason": f"{type(error).__name__}: {error}",
            }
        ]

    candidates = []
    for item in tree.get("tree") or []:
        if item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        category, score = classify_candidate(
            f"https://github.com/{repository}/blob/{branch}/{path}",
            path,
        )
        if not category or score < 4:
            continue
        if not github_path_allowed(category, path):
            continue
        candidates.append(
            {
                "category": category,
                "score": score + 1,
                "url": (
                    f"https://github.com/{repository}/blob/"
                    f"{urllib.parse.quote(branch, safe='')}/"
                    f"{urllib.parse.quote(path, safe='/')}"
                ),
                "title": path,
                "sourceKind": "official_github",
            }
        )

    owner = repository.split("/", 1)[0]
    candidates.append(
        {
            "category": "official_team_or_organization",
            "score": 5,
            "url": f"https://github.com/{owner}",
            "title": f"GitHub 组织 {owner}",
            "sourceKind": "official_github",
        }
    )
    return candidates, []


def record_summary(target, candidate, page=None):
    definition = CATEGORY_DEFINITIONS[candidate["category"]]
    title = clean_text(
        (page or {}).get("title")
        or candidate.get("title")
        or candidate["url"],
        160,
    )
    description = clean_text((page or {}).get("description"), 220)
    detail = f"：{description}" if description else ""
    return (
        f"{target['projectName']} 已发现{definition['label']}入口“{title}”"
        f"{detail}。{definition['boundary']}"
    )


def collect_project(target, timeout):
    if target["identityStatus"] != "verified":
        return {
            "projectId": target["projectId"],
            "projectName": target["projectName"],
            "records": [],
            "issues": [],
            "pendingReason": "项目主体身份尚未核验",
        }
    candidates = list(target["directLinks"])
    website, website_issues = website_candidates(target, timeout)
    github, github_issues = github_candidates(target, timeout)
    candidates.extend(website)
    candidates.extend(github)

    records = []
    for category in CATEGORY_DEFINITIONS:
        options = [
            item for item in candidates if item["category"] == category
        ]
        if not options:
            continue
        selected = max(
            options,
            key=lambda item: (
                item["score"],
                item["sourceKind"] == "official_github",
                -len(item["url"]),
            ),
        )
        page = None
        if selected["sourceKind"] == "official_website":
            try:
                page = request_page(selected["url"], timeout)
            except Exception as error:
                website_issues.append(
                    {
                        "projectId": target["projectId"],
                        "sourceUrl": selected["url"],
                        "reason": f"{type(error).__name__}: {error}",
                    }
                )
                continue
        fact_boundary = (
            "confirmed_fact"
            if category == "official_product_docs"
            else "project_claim"
        )
        records.append(
            {
                "projectId": target["projectId"],
                "projectName": target["projectName"],
                "evidenceType": category,
                "label": CATEGORY_DEFINITIONS[category]["label"],
                "sourceUrl": (page or {}).get("url") or selected["url"],
                "sourceKind": selected["sourceKind"],
                "title": (page or {}).get("title") or selected["title"],
                "description": (page or {}).get("description") or "",
                "factBoundary": fact_boundary,
                "confidence": "高",
                "summary": record_summary(target, selected, page),
            }
        )
    pending_reason = ""
    if not records:
        pending_reason = (
            "缺少可信官网或官方仓库"
            if not target["websiteUrl"] and not target["repository"]
            else "官网与官方仓库未发现目标资料"
        )
    return {
        "projectId": target["projectId"],
        "projectName": target["projectName"],
        "records": records,
        "issues": website_issues + github_issues,
        "pendingReason": pending_reason,
    }


def collect_formal_research_materials(
    db_path=DEFAULT_DB_PATH,
    timeout=20,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        targets = project_targets(connection)
    finally:
        connection.close()

    projects = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        pending = {
            executor.submit(collect_project, target, timeout): target
            for target in targets
        }
        for future in as_completed(pending):
            target = pending[future]
            try:
                projects.append(future.result())
            except Exception as error:
                projects.append(
                    {
                        "projectId": target["projectId"],
                        "projectName": target["projectName"],
                        "records": [],
                        "issues": [
                            {
                                "projectId": target["projectId"],
                                "sourceUrl": (
                                    target["websiteUrl"]
                                    or f"https://github.com/{target['repository']}"
                                ),
                                "reason": f"{type(error).__name__}: {error}",
                            }
                        ],
                        "pendingReason": "采集异常，保留原有资料",
                    }
                )
    return {
        "projectsReviewed": len(targets),
        "projects": sorted(projects, key=lambda item: item["projectName"]),
        "records": [
            record
            for project in projects
            for record in project["records"]
        ],
        "issues": [
            issue for project in projects for issue in project["issues"]
        ],
        "errors": [],
    }


def register_source(connection, now):
    connection.execute(
        """
        INSERT INTO sources (
          source_id, name, source_type, url, access_method, scope,
          confidence, conflict_risk, status, schedule_text,
          last_checked_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'convexity', '中', '中', 'active',
                '凸性更新中心单项更新', ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          name = excluded.name,
          source_type = excluded.source_type,
          url = excluded.url,
          access_method = excluded.access_method,
          status = 'active',
          last_checked_at = excluded.last_checked_at,
          updated_at = excluded.updated_at
        """,
        (
            SOURCE_DEFINITION["source_id"],
            SOURCE_DEFINITION["name"],
            SOURCE_DEFINITION["source_type"],
            SOURCE_DEFINITION["url"],
            SOURCE_DEFINITION["access_method"],
            now,
            now,
            now,
        ),
    )


def persist_formal_research_materials(
    connection,
    bundle,
    run_id,
    now,
):
    register_source(connection, now)
    summary = {
        "projectsReviewed": bundle["projectsReviewed"],
        "recordsCollected": len(bundle["records"]),
        "recordsAdded": 0,
        "duplicateRecords": 0,
        "projectsMatched": sum(
            bool(project["records"]) for project in bundle["projects"]
        ),
        "changedProjects": 0,
        "accessIssues": len(bundle["issues"]),
        "pendingProjects": 0,
        "documentsCovered": 0,
        "tokenomicsCovered": 0,
        "teamCovered": 0,
        "auditCovered": 0,
        "errors": bundle.get("errors") or [],
        "projects": bundle["projects"],
    }
    changed_projects = set()
    for record in bundle["records"]:
        exists = connection.execute(
            """
            SELECT evidence_id
            FROM evidence_items
            WHERE project_id = ?
              AND evidence_type = ?
              AND source_url = ?
            LIMIT 1
            """,
            (
                record["projectId"],
                record["evidenceType"],
                record["sourceUrl"],
            ),
        ).fetchone()
        if exists:
            summary["duplicateRecords"] += 1
            continue

        raw_event_id = stable_id(
            "raw",
            run_id,
            record["projectId"],
            record["evidenceType"],
            record["sourceUrl"],
        )
        payload = {
            "summary": record["summary"],
            "category": record["evidenceType"],
            "label": record["label"],
            "sourceKind": record["sourceKind"],
            "title": record["title"],
            "description": record["description"],
            "factBoundary": record["factBoundary"],
            "version": ENRICHMENT_VERSION,
            "boundary": CATEGORY_DEFINITIONS[
                record["evidenceType"]
            ]["boundary"],
        }
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url, excerpt,
              project_hint, asset_hint, chain_hint, event_type,
              raw_payload_json, status
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, '', '',
                    'formal_project_research_material', ?, 'normalized')
            """,
            (
                raw_event_id,
                SOURCE_DEFINITION["source_id"],
                run_id,
                f"{run_id}:{record['projectId']}:{record['evidenceType']}",
                now,
                hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                record["sourceUrl"],
                record["summary"],
                record["projectName"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO evidence_items (
              evidence_id, project_id, asset_id, raw_event_id, evidence_type,
              stance, fact_boundary, confidence, observed_at, expires_at,
              source_id, source_url, summary, created_at
            )
            VALUES (?, ?, NULL, ?, ?, 'neutral', ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                stable_id(
                    "evidence",
                    record["projectId"],
                    record["evidenceType"],
                    record["sourceUrl"],
                ),
                record["projectId"],
                raw_event_id,
                record["evidenceType"],
                record["factBoundary"],
                record["confidence"],
                now,
                SOURCE_DEFINITION["source_id"],
                record["sourceUrl"],
                record["summary"],
                now,
            ),
        )
        if cursor.rowcount:
            summary["recordsAdded"] += 1
            changed_projects.add(record["projectId"])

    summary["changedProjects"] = len(changed_projects)
    coverage_keys = {
        "documentsCovered": "official_product_docs",
        "tokenomicsCovered": "official_tokenomics",
        "teamCovered": "official_team_or_organization",
        "auditCovered": "official_audit_or_security",
    }
    for summary_key, evidence_type in coverage_keys.items():
        summary[summary_key] = connection.execute(
            """
            SELECT COUNT(DISTINCT project_id)
            FROM evidence_items
            WHERE evidence_type = ?
            """,
            (evidence_type,),
        ).fetchone()[0]
    summary["pendingProjects"] = connection.execute(
        """
        SELECT COUNT(*)
        FROM projects project
        WHERE project.identity_status != 'rejected'
          AND NOT EXISTS (
            SELECT 1
            FROM evidence_items evidence
            WHERE evidence.project_id = project.project_id
              AND evidence.evidence_type IN (
                'official_product_docs',
                'official_tokenomics',
                'official_team_or_organization',
                'official_audit_or_security'
              )
          )
        """
    ).fetchone()[0]
    return summary
