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
from datetime import datetime, timezone
from pathlib import Path

from contract_tradeability import user_environment
from init_db import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS_PATH = PROJECT_ROOT / "fixtures" / "high-value-source-targets-c1.1.json"
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "app" / "high-value-source-snapshot.js"
USER_AGENT = "Penguin-Convexity/1.1"
ENRICHMENT_VERSION = "C1.4-04"

SOURCE_DEFINITIONS = {
    "github": {
        "source_id": "evidence-github-official",
        "name": "GitHub 官方仓库",
        "source_type": "official_code",
        "url": "https://api.github.com",
        "access_method": "认证 REST API",
    },
    "defillama": {
        "source_id": "evidence-defillama-protocols",
        "name": "DefiLlama 协议数据",
        "source_type": "protocol_metrics",
        "url": "https://api.llama.fi",
        "access_method": "公开 API",
    },
    "snapshot": {
        "source_id": "evidence-snapshot-governance",
        "name": "Snapshot 治理",
        "source_type": "offchain_governance",
        "url": "https://hub.snapshot.org/graphql",
        "access_method": "公开 GraphQL",
    },
    "cactus": {
        "source_id": "evidence-cactus-governance",
        "name": "Cactus 链上治理",
        "source_type": "onchain_governance",
        "url": "https://api.tally.xyz/query",
        "access_method": "认证 GraphQL",
    },
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_targets(path=DEFAULT_TARGETS_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def github_repository(value):
    try:
        parsed = urllib.parse.urlparse(str(value or ""))
    except ValueError:
        return ""
    if (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[1] in {"blob", "tree"}:
        return ""
    return f"{parts[0]}/{parts[1].removesuffix('.git')}"


def path_segment_after(value, marker):
    try:
        parts = [part for part in urllib.parse.urlparse(value).path.split("/") if part]
    except ValueError:
        return ""
    try:
        return parts[parts.index(marker) + 1]
    except (ValueError, IndexError):
        return ""


def snapshot_space(value):
    match = re.search(r"snapshot\.org/#/([^/?#]+)", str(value or ""), re.I)
    return urllib.parse.unquote(match.group(1)) if match else ""


def monitoring_registry_targets(connection, projects, verified):
    table_exists = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'project_monitoring_targets'
        """
    ).fetchone()
    if not table_exists:
        return None
    target_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM project_monitoring_targets
        WHERE publication_status = 'published'
        """
    ).fetchone()[0]
    if not target_count:
        return None

    github = []
    defillama_by_project = {}
    snapshot = []
    cactus = []
    for row in connection.execute(
        """
        SELECT *
        FROM project_monitoring_targets
        WHERE publication_status = 'published'
          AND collection_status = 'ready'
          AND target_type IN (
            'github_repository',
            'defillama_protocol',
            'snapshot_space',
            'cactus_governance'
          )
        ORDER BY project_id, target_type, target_value
        """
    ):
        project = verified.get(row["project_id"])
        if not project:
            continue
        if row["target_type"] == "github_repository":
            github.append(
                {
                    "caseId": project["case_id"],
                    "projectId": row["project_id"],
                    "repository": row["target_value"],
                }
            )
        elif row["target_type"] == "defillama_protocol":
            defillama_by_project.setdefault(row["project_id"], set()).add(
                row["target_value"]
            )
        elif row["target_type"] == "snapshot_space":
            snapshot.append(
                {
                    "caseId": project["case_id"],
                    "projectId": row["project_id"],
                    "spaceId": row["target_value"],
                    "limit": 5,
                }
            )
        elif row["target_type"] == "cactus_governance":
            metadata = {}
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                pass
            organization_id = metadata.get("externalId")
            if organization_id:
                cactus.append(
                    {
                        "caseId": project["case_id"],
                        "projectId": row["project_id"],
                        "organizationId": organization_id,
                        "organizationSlug": (
                            metadata.get("slug") or row["target_value"]
                        ),
                        "limit": 5,
                    }
                )

    defillama = [
        {
            "caseId": verified[project_id]["case_id"],
            "projectId": project_id,
            "slugs": sorted(slugs),
        }
        for project_id, slugs in sorted(defillama_by_project.items())
    ]
    return {
        "version": "C1.6-06",
        "updatedAt": utc_now(),
        "boundary": (
            "Continuous evidence collection only consumes current monitoring "
            "targets whose project identity and source attribution passed the "
            "C1.6-04 infrastructure gate."
        ),
        "coverage": {
            "projectsReviewed": len(projects),
            "verifiedProjects": len(verified),
            "identityBlocked": len(projects) - len(verified),
            "registeredTargets": target_count,
            "githubTargets": len(github),
            "defillamaTargets": len(defillama),
            "snapshotTargets": len(snapshot),
            "cactusTargets": len(cactus),
        },
        "github": github,
        "defillama": defillama,
        "snapshot": snapshot,
        "cactus": cactus,
    }


def formal_project_targets(db_path=DEFAULT_DB_PATH):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        projects = [
            dict(row)
            for row in connection.execute(
                """
                SELECT p.*,
                       (
                         SELECT cc.case_id
                         FROM candidate_cases cc
                         WHERE cc.project_id = p.project_id
                         ORDER BY cc.updated_at DESC, cc.case_id DESC
                         LIMIT 1
                       ) AS case_id
                FROM projects p
                WHERE p.identity_status != 'rejected'
                ORDER BY p.canonical_name
                """
            )
        ]
        verified = {
            item["project_id"]: item
            for item in projects
            if item["identity_status"] == "verified" and item.get("case_id")
        }
        registered = monitoring_registry_targets(
            connection,
            projects,
            verified,
        )
        if registered is not None:
            return registered

        repositories = {project_id: set() for project_id in verified}
        defillama_slugs = {project_id: set() for project_id in verified}
        snapshot_spaces = {project_id: set() for project_id in verified}
        cactus_targets = {project_id: {} for project_id in verified}

        for project_id, project in verified.items():
            repository = github_repository(project.get("official_repo"))
            if repository:
                repositories[project_id].add(repository)

        for row in connection.execute(
            """
            SELECT project_id, evidence_type, source_id, source_url
            FROM evidence_items
            WHERE project_id IS NOT NULL
              AND evidence_type IN (
                'official_code_activity',
                'protocol_adoption_metric',
                'governance_proposal',
                'onchain_governance'
              )
            """
        ):
            project_id = row["project_id"]
            if project_id not in verified:
                continue
            if row["evidence_type"] == "official_code_activity":
                repository = github_repository(row["source_url"])
                if repository:
                    repositories[project_id].add(repository)
            elif row["evidence_type"] == "protocol_adoption_metric":
                slug = path_segment_after(row["source_url"], "protocol")
                if slug:
                    defillama_slugs[project_id].add(slug)
            elif row["evidence_type"] == "governance_proposal":
                space_id = snapshot_space(row["source_url"])
                if space_id:
                    snapshot_spaces[project_id].add(space_id)
            elif row["evidence_type"] == "onchain_governance":
                slug = path_segment_after(row["source_url"], "gov")
                if not slug:
                    continue
                organization = connection.execute(
                    """
                    SELECT external_id
                    FROM source_discoveries
                    WHERE source_id = 'discovery-cactus-organizations'
                      AND slug = ?
                    ORDER BY
                      CASE WHEN project_identity_status = 'corroborated' THEN 0 ELSE 1 END,
                      last_seen_at DESC
                    LIMIT 1
                    """,
                    (slug,),
                ).fetchone()
                if organization:
                    cactus_targets[project_id][slug] = organization["external_id"]

        for row in connection.execute(
            """
            SELECT matched_project_id, source_id, external_id, slug, repository_url
            FROM source_discoveries
            WHERE matched_project_id IS NOT NULL
              AND matched_project_id != ''
            """
        ):
            project_id = row["matched_project_id"]
            if project_id not in verified:
                continue
            if row["source_id"] == "discovery-github-repositories":
                repository = github_repository(row["repository_url"])
                if repository:
                    repositories[project_id].add(repository)
            elif row["source_id"] == "discovery-defillama-protocols":
                slug = row["slug"] or row["external_id"]
                if slug:
                    defillama_slugs[project_id].add(slug)
            elif row["source_id"] == "discovery-snapshot-spaces":
                space_id = row["slug"] or row["external_id"]
                if space_id:
                    snapshot_spaces[project_id].add(space_id)
            elif row["source_id"] == "discovery-cactus-organizations":
                if row["slug"] and row["external_id"]:
                    cactus_targets[project_id][row["slug"]] = row["external_id"]

        github = []
        defillama = []
        snapshot = []
        cactus = []
        for project_id, project in verified.items():
            for repository in sorted(repositories[project_id]):
                github.append(
                    {
                        "caseId": project["case_id"],
                        "projectId": project_id,
                        "repository": repository,
                    }
                )
            if defillama_slugs[project_id]:
                defillama.append(
                    {
                        "caseId": project["case_id"],
                        "projectId": project_id,
                        "slugs": sorted(defillama_slugs[project_id]),
                    }
                )
            for space_id in sorted(snapshot_spaces[project_id]):
                snapshot.append(
                    {
                        "caseId": project["case_id"],
                        "projectId": project_id,
                        "spaceId": space_id,
                        "limit": 5,
                    }
                )
            for slug, organization_id in sorted(cactus_targets[project_id].items()):
                cactus.append(
                    {
                        "caseId": project["case_id"],
                        "projectId": project_id,
                        "organizationId": organization_id,
                        "organizationSlug": slug,
                        "limit": 5,
                    }
                )
        return {
            "version": ENRICHMENT_VERSION,
            "updatedAt": utc_now(),
            "boundary": (
                "Only verified project identities and previously verified source mappings "
                "can enter continuous evidence collection."
            ),
            "coverage": {
                "projectsReviewed": len(projects),
                "verifiedProjects": len(verified),
                "identityBlocked": len(projects) - len(verified),
                "githubTargets": len(github),
                "defillamaTargets": len(defillama),
                "snapshotTargets": len(snapshot),
                "cactusTargets": len(cactus),
            },
            "github": github,
            "defillama": defillama,
            "snapshot": snapshot,
            "cactus": cactus,
        }
    finally:
        connection.close()


def request_json(url, method="GET", headers=None, body=None, timeout=20):
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
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))


def success_record(
    provider,
    case_id,
    external_id,
    source_url,
    observed_at,
    event_type,
    evidence_type,
    summary,
    fact_boundary,
    confidence,
    raw,
    metric=None,
):
    return {
        "provider": provider,
        "caseId": case_id,
        "externalId": external_id,
        "sourceUrl": source_url,
        "observedAt": observed_at or utc_now(),
        "eventType": event_type,
        "evidenceType": evidence_type,
        "summary": summary,
        "factBoundary": fact_boundary,
        "confidence": confidence,
        "hardTrace": True,
        "metric": metric,
        "raw": raw,
        "status": "success",
    }


def failure_record(provider, case_id, source_url, error):
    return {
        "provider": provider,
        "caseId": case_id,
        "sourceUrl": source_url,
        "status": "failed",
        "error": f"{type(error).__name__}: {error}",
    }


def collect_github_target(target, timeout=20):
    repository = target["repository"]
    url = f"https://api.github.com/repos/{repository}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = user_environment("BUYI_GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        payload = request_json(url, headers=headers, timeout=timeout)
        commits = request_json(
            f"{url}/commits?per_page=5",
            headers=headers,
            timeout=timeout,
        )
        pushed_at = payload.get("pushed_at") or payload.get("updated_at") or utc_now()
        latest_commit = commits[0] if isinstance(commits, list) and commits else {}
        latest_commit_data = latest_commit.get("commit") or {}
        latest_commit_meta = latest_commit_data.get("committer") or {}
        latest_commit_at = latest_commit_meta.get("date") or pushed_at
        latest_message = str(latest_commit_data.get("message") or "").splitlines()[0]
        summary = (
            f"官方映射仓库 {payload.get('full_name') or repository} 最近推送于 "
            f"{pushed_at}；默认分支 {payload.get('default_branch') or '未返回'}。"
            f"最近提交：{latest_message or '提交信息未返回'}"
            f"（{str(latest_commit.get('sha') or '')[:12] or 'SHA未返回'}）。"
        )
        records = [
            success_record(
                "github",
                target["caseId"],
                f"{repository.lower()}:{latest_commit.get('sha') or pushed_at}",
                payload.get("html_url") or f"https://github.com/{repository}",
                pushed_at,
                "official_code_activity",
                "official_code_activity",
                summary,
                "confirmed_fact",
                "高",
                {
                    "repository": payload.get("full_name") or repository,
                    "defaultBranch": payload.get("default_branch"),
                    "pushedAt": pushed_at,
                    "archived": bool(payload.get("archived")),
                    "fork": bool(payload.get("fork")),
                    "latestCommit": {
                        "sha": latest_commit.get("sha"),
                        "url": latest_commit.get("html_url"),
                        "message": latest_message,
                        "committedAt": latest_commit_at,
                    },
                },
            )
        ]
        security_terms = (
            "security",
            "audit",
            "vulnerability",
            "exploit",
            "permission",
            "access control",
            "reentr",
            "安全",
            "审计",
            "漏洞",
        )
        for commit in commits if isinstance(commits, list) else []:
            commit_data = commit.get("commit") or {}
            message = str(commit_data.get("message") or "").splitlines()[0]
            if not any(term in message.casefold() for term in security_terms):
                continue
            committed_at = (
                (commit_data.get("committer") or {}).get("date")
                or (commit_data.get("author") or {}).get("date")
                or pushed_at
            )
            records.append(
                success_record(
                    "github",
                    target["caseId"],
                    f"security:{commit.get('sha')}",
                    commit.get("html_url")
                    or payload.get("html_url")
                    or f"https://github.com/{repository}",
                    committed_at,
                    "official_security_activity",
                    "official_security_activity",
                    (
                        f"官方仓库 {repository} 出现安全相关提交“"
                        f"{message or str(commit.get('sha') or '')[:12]}”。"
                        "仅证明代码活动涉及安全主题，不代表漏洞已经完全修复。"
                    ),
                    "confirmed_fact",
                    "高",
                    {
                        "repository": repository,
                        "sha": commit.get("sha"),
                        "message": message,
                        "committedAt": committed_at,
                    },
                )
            )
        return records
    except Exception as error:
        return [failure_record("github", target["caseId"], url, error)]


def collect_defillama_targets(targets, timeout=30):
    url = "https://api.llama.fi/protocols"
    try:
        payload = request_json(url, timeout=timeout)
        by_slug = {item.get("slug"): item for item in payload if item.get("slug")}
        records = []
        for target in targets:
            protocols = [by_slug[slug] for slug in target["slugs"] if slug in by_slug]
            if not protocols:
                records.append(
                    failure_record(
                        "defillama",
                        target["caseId"],
                        url,
                        RuntimeError("没有返回已核验的协议标识"),
                    )
                )
                continue
            tvl_values = [
                float(item["tvl"])
                for item in protocols
                if isinstance(item.get("tvl"), (int, float))
            ]
            total_tvl = sum(tvl_values) if tvl_values else None
            names = "、".join(item.get("name") or item["slug"] for item in protocols)
            tvl_text = (
                f"{total_tvl:,.0f} 美元"
                if total_tvl is not None
                else "接口未返回可汇总TVL"
            )
            summary = f"DefiLlama 已匹配 {names}，当前汇总TVL为 {tvl_text}。"
            records.append(
                success_record(
                    "defillama",
                    target["caseId"],
                    ",".join(target["slugs"]),
                    f"https://defillama.com/protocol/{target['slugs'][0]}",
                    utc_now(),
                    "protocol_adoption_snapshot",
                    "protocol_adoption_metric",
                    summary,
                    "high_confidence_inference",
                    "中",
                    {
                        "protocols": [
                            {
                                "name": item.get("name"),
                                "slug": item.get("slug"),
                                "category": item.get("category"),
                                "chains": item.get("chains") or [],
                                "tvl": item.get("tvl"),
                            }
                            for item in protocols
                        ],
                        "totalTvlUsd": total_tvl,
                    },
                    metric={"field": "TVL", "value": total_tvl, "unit": "USD"},
                )
            )
        return records
    except Exception as error:
        return [
            failure_record("defillama", target["caseId"], url, error)
            for target in targets
        ]


def collect_snapshot_target(target, timeout=20):
    url = "https://hub.snapshot.org/graphql"
    body = {
        "query": (
            "query Proposals($space: String!, $limit: Int!) { "
            "proposals(first: $limit, where: {space: $space}, "
            "orderBy: \"created\", orderDirection: desc) { "
            "id title state start end created space { id name } } }"
        ),
        "variables": {
            "space": target["spaceId"],
            "limit": int(target.get("limit", 5)),
        },
    }
    try:
        payload = request_json(url, method="POST", body=body, timeout=timeout)
        if payload.get("errors"):
            raise RuntimeError(payload["errors"][0].get("message", "GraphQL 查询失败"))
        proposals = (payload.get("data") or {}).get("proposals") or []
        if not proposals:
            raise RuntimeError("该治理空间没有返回提案")
        records = []
        for proposal in proposals:
            proposal_url = (
                f"https://snapshot.org/#/{target['spaceId']}/proposal/{proposal['id']}"
            )
            summary = (
                f"Snapshot 提案《{proposal.get('title') or '未命名提案'}》"
                f"当前状态 {proposal.get('state') or 'unknown'}。"
            )
            records.append(
                success_record(
                    "snapshot",
                    target["caseId"],
                    proposal["id"],
                    proposal_url,
                    datetime.fromtimestamp(
                        int(proposal.get("created") or 0),
                        timezone.utc,
                    ).isoformat().replace("+00:00", "Z")
                    if proposal.get("created")
                    else utc_now(),
                    "offchain_governance_proposal",
                    "governance_proposal",
                    summary,
                    "confirmed_fact",
                    "中",
                    proposal,
                )
            )
        return records
    except Exception as error:
        return [failure_record("snapshot", target["caseId"], url, error)]


def collect_cactus_target(target, timeout=25):
    url = "https://api.tally.xyz/query"
    api_key = user_environment("CACTUS_TALLY_API_KEY")
    if not api_key:
        return [
            failure_record(
                "cactus",
                target["caseId"],
                url,
                RuntimeError("本机未配置 Cactus/Tally API 密钥"),
            )
        ]
    body = {
        "query": (
            "query Proposals($input: ProposalsInput!) { "
            "proposals(input: $input) { nodes { ... on Proposal { "
            "id onchainId status metadata { title } organization { id name } "
            "} } } }"
        ),
        "variables": {
            "input": {
                "filters": {
                    "organizationId": target["organizationId"],
                    "includeArchived": True,
                },
                "page": {"limit": int(target.get("limit", 5))},
                "sort": {"sortBy": "proposedAt", "isDescending": True},
            }
        },
    }
    try:
        payload = request_json(
            url,
            method="POST",
            headers={"Api-Key": api_key},
            body=body,
            timeout=timeout,
        )
        if payload.get("errors"):
            raise RuntimeError(payload["errors"][0].get("message", "GraphQL 查询失败"))
        proposals = ((payload.get("data") or {}).get("proposals") or {}).get("nodes") or []
        if not proposals:
            raise RuntimeError("该链上治理组织没有返回提案")
        records = []
        for proposal in proposals:
            title = ((proposal.get("metadata") or {}).get("title") or "未命名提案")
            status = proposal.get("status") or "unknown"
            proposal_url = (
                f"https://www.tally.xyz/gov/{target['organizationSlug']}/proposal/"
                f"{proposal['id']}"
            )
            records.append(
                success_record(
                    "cactus",
                    target["caseId"],
                    proposal["id"],
                    proposal_url,
                    utc_now(),
                    "onchain_governance_proposal",
                    "onchain_governance",
                    f"Cactus 链上提案《{title}》当前状态 {status}。",
                    "confirmed_fact",
                    "高" if status == "executed" else "中",
                    {
                        "id": proposal.get("id"),
                        "onchainId": proposal.get("onchainId"),
                        "status": status,
                        "title": title,
                        "organization": proposal.get("organization"),
                    },
                )
            )
        return records
    except Exception as error:
        return [failure_record("cactus", target["caseId"], url, error)]


def collect_high_value_sources(targets=None, timeout=20, db_path=DEFAULT_DB_PATH):
    targets = targets or formal_project_targets(db_path)
    records = []
    futures = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for target in targets.get("github", []):
            futures.append(executor.submit(collect_github_target, target, timeout))
        for target in targets.get("snapshot", []):
            futures.append(executor.submit(collect_snapshot_target, target, timeout))
        for target in targets.get("cactus", []):
            futures.append(executor.submit(collect_cactus_target, target, timeout))
        futures.append(
            executor.submit(
                collect_defillama_targets,
                targets.get("defillama", []),
                max(timeout, 30),
            )
        )
        for future in as_completed(futures):
            records.extend(future.result())

    source_stats = {}
    errors = []
    for provider in SOURCE_DEFINITIONS:
        provider_records = [item for item in records if item["provider"] == provider]
        success = [item for item in provider_records if item["status"] == "success"]
        failed = [item for item in provider_records if item["status"] == "failed"]
        target_count = len(targets.get(provider, []))
        verified_projects = int(
            (targets.get("coverage") or {}).get("verifiedProjects") or 0
        )
        source_stats[provider] = {
            "collected": len(success),
            "matched": len({item["caseId"] for item in success}),
            "filtered": max(0, verified_projects - target_count),
            "failed": len(failed),
        }
        errors.extend(failed)
    return {
        "records": records,
        "sourceStats": source_stats,
        "errors": errors,
        "targetVersion": targets.get("version"),
        "coverage": targets.get("coverage") or {},
    }


def previous_metric(connection, source_id, case_id, event_type):
    row = connection.execute(
        """
        SELECT raw_payload_json
        FROM raw_events
        WHERE source_id = ? AND project_hint = ? AND event_type = ?
        ORDER BY collected_at DESC, raw_event_id DESC
        LIMIT 1
        """,
        (source_id, case_id, event_type),
    ).fetchone()
    if not row:
        return None
    payload = json.loads(row["raw_payload_json"])
    metric = payload.get("metric") or {}
    value = metric.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def evidence_already_recorded(
    connection,
    project_id,
    evidence_type,
    source_url,
    summary,
):
    return bool(
        connection.execute(
            """
            SELECT 1
            FROM evidence_items
            WHERE project_id = ?
              AND evidence_type = ?
              AND source_url = ?
              AND summary = ?
            LIMIT 1
            """,
            (project_id, evidence_type, source_url, summary),
        ).fetchone()
    )


def meaningful_metric_change(metric, previous, current):
    if not isinstance(current, (int, float)) or previous is None:
        return False
    current = float(current)
    previous = float(previous)
    if current == previous:
        return False
    if previous == 0:
        return current > 0

    absolute_change = abs(current - previous)
    relative_change = absolute_change / abs(previous)
    if metric.get("field") == "TVL" and metric.get("unit") == "USD":
        return absolute_change >= 10_000 and relative_change >= 0.01
    return relative_change >= 0.01


def persist_high_value_sources(
    connection,
    bundle,
    run_id,
    now,
    stable_id,
    record_by_case,
):
    normalized = 0
    changed = 0
    duplicates = 0
    matched_projects = set()
    for item in bundle["records"]:
        if item["status"] != "success":
            continue
        definition = SOURCE_DEFINITIONS[item["provider"]]
        source_id = definition["source_id"]
        case = record_by_case.get(item["caseId"])
        if not case:
            continue
        project_id = case.get("projectId")
        if not project_id:
            continue
        matched_projects.add(project_id)
        metric = item.get("metric") or {}
        previous = previous_metric(
            connection,
            source_id,
            item["caseId"],
            item["eventType"],
        )
        current = metric.get("value")
        changes = []
        economic_increment_verified = False
        if isinstance(current, (int, float)) and previous is not None:
            if meaningful_metric_change(metric, previous, current):
                change_pct = None if previous == 0 else (float(current) - previous) / previous * 100
                changes.append(
                    {
                        "field": metric.get("field") or "指标",
                        "before": previous,
                        "after": current,
                        "changePct": round(change_pct, 4) if change_pct is not None else None,
                    }
                )
                changed += 1
            economic_increment_verified = previous > 0 and float(current) >= previous * 1.05
            if not changes:
                duplicates += 1
                continue

        if evidence_already_recorded(
            connection,
            project_id,
            item["evidenceType"],
            item["sourceUrl"],
            item["summary"],
        ) and not changes:
            duplicates += 1
            continue

        payload = {
            "provider": item["provider"],
            "status": "success",
            "summary": item["summary"],
            "evidenceType": item["evidenceType"],
            "factBoundary": item["factBoundary"],
            "confidence": item["confidence"],
            "hardTrace": item["hardTrace"],
            "metric": metric or None,
            "economicIncrementVerified": economic_increment_verified,
            "changes": changes,
            "raw": item["raw"],
        }
        raw_event_id = stable_id(
            "raw-high-value",
            run_id,
            source_id,
            item["caseId"],
            item["externalId"],
        )
        connection.execute(
            """
            INSERT INTO raw_events (
              raw_event_id, source_id, ingestion_run_id, external_id,
              published_at, collected_at, content_hash, source_url, excerpt,
              project_hint, asset_hint, chain_hint, event_type,
              raw_payload_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'normalized')
            """,
            (
                raw_event_id,
                source_id,
                run_id,
                f"{run_id}:{item['externalId']}",
                item["observedAt"],
                now,
                hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                item["sourceUrl"],
                item["summary"],
                item["caseId"],
                case.get("symbol", ""),
                case.get("chain", ""),
                item["eventType"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            INSERT INTO evidence_items (
              evidence_id, project_id, asset_id, raw_event_id, evidence_type,
              stance, fact_boundary, confidence, observed_at, expires_at,
              source_id, source_url, summary, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'neutral', ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                stable_id("evidence-high-value", raw_event_id),
                case.get("projectId"),
                case.get("assetId"),
                raw_event_id,
                item["evidenceType"],
                item["factBoundary"],
                item["confidence"],
                item["observedAt"],
                source_id,
                item["sourceUrl"],
                item["summary"],
                now,
            ),
        )
        normalized += 1

    for provider, stat in bundle["sourceStats"].items():
        definition = SOURCE_DEFINITIONS[provider]
        if stat["failed"] and stat["collected"] == 0:
            status = "failed"
        elif stat["failed"]:
            status = "partial_success"
        elif stat["collected"] == 0:
            status = "no_data"
        else:
            status = "success"
        connection.execute(
            """
            INSERT INTO run_source_stats (
              run_source_stat_id, run_id, source_id, collector_id, status,
              started_at, finished_at, collected_count, matched_count,
              filtered_count, failed_count, filter_reason_summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("run-source", run_id, provider),
                run_id,
                definition["source_id"],
                provider,
                status,
                now,
                now,
                stat["collected"],
                stat["matched"],
                stat["filtered"],
                stat["failed"],
                json.dumps(
                    {
                        "boundary": (
                            "持续证据只记录代码、采用、治理与安全相关事实，"
                            "不直接改变凸性质量、收益赔率或行动等级"
                        ),
                        "targetVersion": bundle.get("targetVersion"),
                        "coverage": bundle.get("coverage") or {},
                    },
                    ensure_ascii=False,
                ),
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
                stable_id(
                    "high-value-error",
                    run_id,
                    source_id,
                    error.get("caseId"),
                    error.get("sourceUrl"),
                ),
                run_id,
                source_id,
                f"高价值信源 · {error.get('caseId') or '未归属项目'}",
                error["error"],
                now,
                now,
            ),
        )
    coverage = {
        "projectsReviewed": 0,
        "verifiedProjects": 0,
        "identityBlocked": 0,
        "githubTargets": 0,
        "defillamaTargets": 0,
        "snapshotTargets": 0,
        "cactusTargets": 0,
        **(bundle.get("coverage") or {}),
    }
    return {
        "collected": sum(item["status"] == "success" for item in bundle["records"]),
        "normalized": normalized,
        "matched": len(matched_projects),
        "filtered": duplicates,
        "failed": len(bundle["errors"]),
        "changed": changed,
        "duplicates": duplicates,
        **coverage,
    }


def build_high_value_snapshot(connection):
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
    records = []
    seen_records = set()
    for row in connection.execute(
        f"""
        SELECT event.*, source.name AS source_name
        FROM raw_events event
        JOIN sources source ON source.source_id = event.source_id
        WHERE event.source_id IN ({placeholders})
        ORDER BY event.collected_at DESC, event.raw_event_id DESC
        """,
        source_ids,
    ):
        record_key = (
            row["source_id"],
            row["project_hint"],
            row["event_type"],
            row["source_url"],
        )
        if record_key in seen_records:
            continue
        seen_records.add(record_key)
        payload = json.loads(row["raw_payload_json"])
        records.append(
            {
                "recordId": row["raw_event_id"],
                "caseId": row["project_hint"],
                "asset": row["asset_hint"],
                "sourceId": row["source_id"],
                "sourceName": row["source_name"],
                "eventType": row["event_type"],
                "summary": row["excerpt"],
                "sourceUrl": row["source_url"],
                "observedAt": row["published_at"] or row["collected_at"],
                "factBoundary": payload.get("factBoundary"),
                "confidence": payload.get("confidence"),
                "hardTrace": bool(payload.get("hardTrace")),
                "economicIncrementVerified": bool(
                    payload.get("economicIncrementVerified")
                ),
                "metric": payload.get("metric"),
                "changes": payload.get("changes") or [],
            }
        )

    case_map = {}
    for record in records:
        case = case_map.setdefault(
            record["caseId"],
            {
                "caseId": record["caseId"],
                "sourceIds": [],
                "recordCount": 0,
                "hardTrace": False,
                "hasStructuredAdoption": False,
                "hasExecutedGovernance": False,
                "economicIncrement": "unknown",
            },
        )
        case["recordCount"] += 1
        if record["sourceId"] not in case["sourceIds"]:
            case["sourceIds"].append(record["sourceId"])
        case["hardTrace"] = case["hardTrace"] or record["hardTrace"]
        metric_value = (record.get("metric") or {}).get("value")
        if record["sourceId"] == SOURCE_DEFINITIONS["defillama"]["source_id"]:
            case["hasStructuredAdoption"] = case["hasStructuredAdoption"] or (
                isinstance(metric_value, (int, float)) and metric_value > 0
            )
        if record["sourceId"] == SOURCE_DEFINITIONS["cactus"]["source_id"]:
            case["hasExecutedGovernance"] = case["hasExecutedGovernance"] or (
                "executed" in record["summary"].lower()
            )
        if record["economicIncrementVerified"]:
            case["economicIncrement"] = "verified"
    for case in case_map.values():
        if case["hasStructuredAdoption"] or case["hasExecutedGovernance"]:
            case["evidenceGrade"] = "verified"
        elif case["hardTrace"]:
            case["evidenceGrade"] = "conditional"
        else:
            case["evidenceGrade"] = "none"

    sources = []
    for provider, definition in SOURCE_DEFINITIONS.items():
        latest_stat = None
        if latest_run:
            latest_stat = connection.execute(
                """
                SELECT *
                FROM run_source_stats
                WHERE run_id = ? AND source_id = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (latest_run["run_id"], definition["source_id"]),
            ).fetchone()
        sources.append(
            {
                "provider": provider,
                **definition,
                "status": latest_stat["status"] if latest_stat else "never_run",
                "collected": latest_stat["collected_count"] if latest_stat else 0,
                "matched": latest_stat["matched_count"] if latest_stat else 0,
                "failed": latest_stat["failed_count"] if latest_stat else 0,
            }
        )
    return {
        "version": ENRICHMENT_VERSION,
        "release": "C1.4",
        "generatedAt": utc_now(),
        "title": "正式项目持续证据",
        "policy": "四类信源只补充代码、采用、治理与安全相关活动。单一来源成功不证明代币价值捕获，也不自动生成行动结论。",
        "latestRun": dict(latest_run) if latest_run else None,
        "counts": {
            "sources": len(sources),
            "records": len(records),
            "cases": len(case_map),
            "failedSources": sum(item["status"] == "failed" for item in sources),
            "changed": sum(bool(item["changes"]) for item in records),
        },
        "sources": sources,
        "cases": sorted(case_map.values(), key=lambda item: item["caseId"]),
        "records": records,
    }


def write_high_value_snapshot(snapshot, path=DEFAULT_SNAPSHOT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        "window.PENGUIN_CONVEXITY_HIGH_VALUE_SOURCES = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def rebuild_high_value_snapshot(
    db_path=DEFAULT_DB_PATH,
    output_path=DEFAULT_SNAPSHOT_PATH,
):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = build_high_value_snapshot(connection)
        write_high_value_snapshot(snapshot, output_path)
        return snapshot
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="生成凸性高价值信源快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args()
    snapshot = rebuild_high_value_snapshot(args.db, args.output)
    print(json.dumps(snapshot["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
