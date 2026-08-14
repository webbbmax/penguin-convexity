#!/usr/bin/env python3
"""One-time, reversible repair for GitHub attribution found by C2.4 acceptance."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from c2_1_enrichment import collect_github, collect_website_identity, github_target


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "c2.1-pipeline.db"
REPORT_DIR = ROOT / "reports" / "c2.4-independent-acceptance"
BACKUP_PATH = REPORT_DIR / "github-attribution-repair-backup.json"
RESULT_PATH = REPORT_DIR / "github-attribution-repair-result.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def rows(connection: sqlite3.Connection, query: str, parameters=()) -> list[dict]:
    return [dict(row) for row in connection.execute(query, parameters)]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def candidate_sets(connection: sqlite3.Connection) -> tuple[list[int], list[int], list[int]]:
    website_ids = [
        int(row[0])
        for row in connection.execute(
            """SELECT DISTINCT c.candidate_id FROM candidates c
               JOIN product_evidence e ON e.candidate_id=c.candidate_id
               WHERE e.evidence_type='github' AND e.status='qualifying'
                 AND NOT EXISTS(SELECT 1 FROM product_evidence m
                   WHERE m.candidate_id=c.candidate_id AND m.evidence_id LIKE 'c21-main-repo-%')
                 AND julianday(?) - julianday(c.effective_t0) BETWEEN 0 AND 90
               ORDER BY c.candidate_id""",
            (now(),),
        )
    ]
    profile_ids = []
    for row in connection.execute(
        """SELECT DISTINCT c.candidate_id,c.official_repo FROM candidates c
           JOIN product_evidence e ON e.candidate_id=c.candidate_id
           WHERE e.evidence_type='github' AND e.status='qualifying'
             AND COALESCE(c.official_repo,'')!='' ORDER BY c.candidate_id"""
    ):
        target = github_target(row[1])
        if target and not target["repository"]:
            profile_ids.append(int(row[0]))
    public_ids = [
        int(row[0])
        for row in connection.execute(
            "SELECT candidate_id FROM c2_4_public_history WHERE public_active=1 ORDER BY candidate_id"
        )
    ]
    return website_ids, profile_ids, public_ids


def backup_payload(connection: sqlite3.Connection, candidate_ids: list[int]) -> dict:
    placeholders = ",".join("?" for _ in candidate_ids)
    if not placeholders:
        return {"createdAt": now(), "candidateIds": [], "tables": {}}
    tables = {
        "candidates": rows(connection, f"SELECT * FROM candidates WHERE candidate_id IN ({placeholders})", candidate_ids),
        "product_evidence": rows(connection, f"SELECT * FROM product_evidence WHERE candidate_id IN ({placeholders})", candidate_ids),
        "c2_4_public_history": rows(connection, f"SELECT * FROM c2_4_public_history WHERE candidate_id IN ({placeholders})", candidate_ids),
        "c2_4_lifecycle_state": rows(connection, f"SELECT * FROM c2_4_lifecycle_state WHERE candidate_id IN ({placeholders})", candidate_ids),
        "source_health": rows(
            connection,
            f"SELECT * FROM source_health WHERE scope_key IN ({placeholders})",
            [str(value) for value in candidate_ids],
        ),
    }
    return {"createdAt": now(), "database": str(DB_PATH), "candidateIds": candidate_ids, "tables": tables}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply the repair after writing a row-level backup.")
    args = parser.parse_args()
    connection = sqlite3.connect(DB_PATH, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    website_ids, profile_ids, public_ids = candidate_sets(connection)
    candidate_ids = sorted(set(website_ids) | set(profile_ids) | set(public_ids))
    plan = {
        "mode": "apply" if args.apply else "audit",
        "websiteMappingsToRecheck": len(website_ids),
        "profileOnlyMappingsToRetract": len(profile_ids),
        "currentlyPublicToReconcile": len(public_ids),
        "candidateIds": candidate_ids,
    }
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if BACKUP_PATH.exists():
        raise RuntimeError(f"Repair backup already exists: {BACKUP_PATH}")
    write_json(BACKUP_PATH, backup_payload(connection, candidate_ids))

    website_result = collect_website_identity(
        connection, candidate_ids=website_ids, force_recheck=True
    ) if website_ids else {}
    profile_result = collect_github(
        connection, candidate_ids=profile_ids, force_recheck=True
    ) if profile_ids else {}

    invalid_public_ids = [
        int(row[0])
        for row in connection.execute(
            """SELECT h.candidate_id FROM c2_4_public_history h
               WHERE h.public_active=1 AND NOT EXISTS(
                 SELECT 1 FROM product_evidence e
                 WHERE e.candidate_id=h.candidate_id AND e.status='qualifying'
               ) ORDER BY h.candidate_id"""
        )
    ]
    if invalid_public_ids:
        placeholders = ",".join("?" for _ in invalid_public_ids)
        connection.execute(
            f"""UPDATE c2_4_public_history SET public_active=0,
                 last_public_exit_reason='evidence_attribution_correction'
                 WHERE candidate_id IN ({placeholders})""",
            invalid_public_ids,
        )
        connection.commit()

    result = {
        **plan,
        "completedAt": now(),
        "backup": str(BACKUP_PATH.relative_to(ROOT)),
        "websiteRecheck": website_result,
        "profileOnlyRecheck": profile_result,
        "publicMappingsRetracted": invalid_public_ids,
        "remainingQualifyingGithubEvidence": connection.execute(
            "SELECT COUNT(DISTINCT candidate_id) FROM product_evidence WHERE evidence_type='github' AND status='qualifying'"
        ).fetchone()[0],
    }
    write_json(RESULT_PATH, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
