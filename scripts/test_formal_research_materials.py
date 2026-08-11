#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from automatic_profile_quality import (
    PROFILE_VERSION,
    build_automatic_profile,
)
from build_update_center_snapshot import rebuild_update_snapshots
from enrich_formal_research_materials import (
    ENRICHMENT_VERSION,
    SOURCE_DEFINITION,
    classify_candidate,
    github_repository,
    github_path_allowed,
    persist_formal_research_materials,
)
from init_db import initialize_database
from update_tasks import TASK_DEFINITIONS


NOW = "2026-07-30T09:00:00Z"


def insert_project(connection, project_id, name, identity_status):
    connection.execute(
        """
        INSERT INTO projects (
          project_id, canonical_name, website_domain, official_repo,
          team_summary, identity_status, first_seen_at, created_at, updated_at
        )
        VALUES (?, ?, '', '', '', ?, ?, ?, ?)
        """,
        (project_id, name, identity_status, NOW, NOW, NOW),
    )


def insert_run(connection, run_id):
    connection.execute(
        """
        INSERT INTO runs (
          run_id, job_name, mode, status, started_at,
          zero_result_class, zero_result_explanation, triggered_by,
          schema_version
        )
        VALUES (?, ?, 'manual', 'running', ?,
                'none', '', 'test', 1)
        """,
        (
            run_id,
            TASK_DEFINITIONS[
                "formal_research_materials_refresh"
            ]["jobName"],
            NOW,
        ),
    )


def record(project_id, project_name, evidence_type, url, boundary):
    labels = {
        "official_product_docs": "产品文档",
        "official_tokenomics": "代币经济",
        "official_team_or_organization": "团队与组织",
        "official_audit_or_security": "审计与安全",
    }
    return {
        "projectId": project_id,
        "projectName": project_name,
        "evidenceType": evidence_type,
        "label": labels[evidence_type],
        "sourceUrl": url,
        "sourceKind": "official_website",
        "title": labels[evidence_type],
        "description": "",
        "factBoundary": boundary,
        "confidence": "高",
        "summary": f"{project_name} 已发现{labels[evidence_type]}入口。",
    }


def build_bundle():
    records = [
        record(
            "verified-project",
            "Verified Project",
            "official_product_docs",
            "https://docs.example.com/guide",
            "confirmed_fact",
        ),
        record(
            "verified-project",
            "Verified Project",
            "official_tokenomics",
            "https://example.com/tokenomics",
            "project_claim",
        ),
        record(
            "verified-project",
            "Verified Project",
            "official_team_or_organization",
            "https://example.com/team",
            "project_claim",
        ),
        record(
            "verified-project",
            "Verified Project",
            "official_audit_or_security",
            "https://example.com/audits",
            "project_claim",
        ),
    ]
    return {
        "projectsReviewed": 2,
        "projects": [
            {
                "projectId": "verified-project",
                "projectName": "Verified Project",
                "records": records,
                "issues": [],
                "pendingReason": "",
            },
            {
                "projectId": "pending-project",
                "projectName": "Pending Project",
                "records": [],
                "issues": [],
                "pendingReason": "项目主体身份尚未核验",
            },
        ],
        "records": records,
        "issues": [],
        "errors": [],
    }


def main():
    assert ENRICHMENT_VERSION == "C1.4-03"
    assert PROFILE_VERSION == "C1.4-05"
    task = TASK_DEFINITIONS["formal_research_materials_refresh"]
    assert task["components"] == ["formal_research_materials"]
    assert SOURCE_DEFINITION["source_id"] in task["sourceIds"]

    assert classify_candidate(
        "https://docs.example.com/guide"
    )[0] == "official_product_docs"
    assert classify_candidate(
        "https://example.com/tokenomics"
    )[0] == "official_tokenomics"
    assert classify_candidate(
        "https://example.com/team"
    )[0] == "official_team_or_organization"
    assert classify_candidate(
        "https://example.com/audits"
    )[0] == "official_audit_or_security"
    assert classify_candidate(
        "https://example.com/news"
    ) == ("", 0)
    assert github_repository(
        "https://github.com/example/protocol/blob/main/README.md"
    ) == "example/protocol"
    assert github_path_allowed(
        "official_product_docs",
        "docs/architecture.md",
    )
    assert not github_path_allowed(
        "official_product_docs",
        "web/assets/docs.js",
    )
    assert not github_path_allowed(
        "official_audit_or_security",
        "lib/dependency/audits/report.md",
    )
    assert not github_path_allowed(
        "official_team_or_organization",
        "src/gtest/test_foundersreward.cpp",
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        initialize_database(
            db_path=db_path,
            snapshot_path=root / "runtime.js",
            backup=False,
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        insert_project(
            connection,
            "verified-project",
            "Verified Project",
            "verified",
        )
        insert_project(
            connection,
            "pending-project",
            "Pending Project",
            "pending",
        )
        insert_run(connection, "formal-material-test-1")
        result = persist_formal_research_materials(
            connection,
            build_bundle(),
            "formal-material-test-1",
            NOW,
        )
        connection.execute(
            """
            UPDATE runs
            SET status = 'success', finished_at = ?
            WHERE run_id = 'formal-material-test-1'
            """,
            (NOW,),
        )
        connection.commit()

        assert result["projectsReviewed"] == 2
        assert result["projectsMatched"] == 1
        assert result["recordsAdded"] == 4
        assert result["changedProjects"] == 1
        assert result["documentsCovered"] == 1
        assert result["tokenomicsCovered"] == 1
        assert result["teamCovered"] == 1
        assert result["auditCovered"] == 1
        assert result["pendingProjects"] == 1
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM evidence_items
            WHERE source_id = ?
            """,
            (SOURCE_DEFINITION["source_id"],),
        ).fetchone()[0] == 4
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM candidate_cases
            """
        ).fetchone()[0] == 0

        evidence = [
            dict(row)
            for row in connection.execute(
                """
                SELECT evidence.*, source.name AS source_name
                FROM evidence_items evidence
                JOIN sources source ON source.source_id = evidence.source_id
                WHERE evidence.project_id = 'verified-project'
                """
            )
        ]
        evidence.append(
            {
                "evidence_type": "official_code_activity",
                "fact_boundary": "confirmed_fact",
                "confidence": "high",
                "source_id": "evidence-github-official",
                "source_name": "GitHub 认证仓库",
                "source_url": "https://github.com/example/protocol",
                "summary": "Verified Project 官方代码仓库。",
                "observed_at": "2026-07-29T13:17:11Z",
                "created_at": "2026-07-29T13:17:11Z",
            }
        )
        profile = build_automatic_profile(
            {
                "recordType": "project",
                "master": {
                    "name": "Verified Project",
                    "lifecycleDateStatus": "pending",
                },
                "project": {
                    "project_id": "verified-project",
                    "canonical_name": "Verified Project",
                    "identity_status": "verified",
                    "website_domain": "",
                    "official_repo": "",
                    "team_summary": "",
                    "updated_at": NOW,
                },
                "assets": [],
                "evidence": evidence,
            }
        )
        fields = {
            field["id"]: field
            for section in profile["sections"]
            for field in section["fields"]
        }
        for field_id in ("productDocs", "tokenomics", "team"):
            assert fields[field_id]["status"] in {"verified", "available"}
            assert (
                fields[field_id]["nextTaskId"]
                == "formal_research_materials_refresh"
            )
        assert fields["audit"]["status"] in {"verified", "available"}
        assert fields["audit"]["nextTaskId"] == "high_value_evidence_refresh"
        assert fields["productDocs"]["sourceUrl"].endswith("/guide")
        assert fields["github"]["sourceUrl"] == "https://github.com/example/protocol"
        assert fields["audit"]["sourceUrl"].endswith("/audits")

        update, sources = rebuild_update_snapshots(
            db_path=db_path,
            update_path=root / "update-center.js",
            source_path=root / "source-registry.js",
        )
        assert any(
            item["taskId"] == "formal_research_materials_refresh"
            for item in update["tasks"]
        )
        assert any(
            item["eventType"] == "formal_project_research_material"
            and item["taskId"] == "formal_research_materials_refresh"
            for item in update["changes"]
        )
        formal_source = next(
            item
            for item in sources["sources"]
            if item["source_id"] == SOURCE_DEFINITION["source_id"]
        )
        assert formal_source["primaryTaskId"] == (
            "formal_research_materials_refresh"
        )
        assert formal_source["proves"]
        assert formal_source["doesNotProve"]

        insert_run(connection, "formal-material-test-2")
        repeated = persist_formal_research_materials(
            connection,
            build_bundle(),
            "formal-material-test-2",
            NOW,
        )
        connection.commit()
        assert repeated["recordsAdded"] == 0
        assert repeated["duplicateRecords"] == 4
        connection.close()

    print("C1.4-03 正式项目研究资料自动补齐测试通过。")


if __name__ == "__main__":
    main()
