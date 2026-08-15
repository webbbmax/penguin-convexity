#!/usr/bin/env python3
"""Persist C2.4 first-gate history and qualified day-91 lifecycle facts."""

from __future__ import annotations

import json
from pathlib import Path

from c2_1_db import utc_now
from c2_1_observation_state import confirmed_trade_block, latest_effective_market_row
from c2_4_rules import (
    determine_public_state,
    evaluate_first_gate,
    evaluate_public_baseline,
    evaluate_strong_paths,
    load_active_rule_version,
    normal_exit_decision,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = PROJECT_ROOT / "storage" / "c2.4-tracking.sql"


def initialize_schema(connection) -> None:
    connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
    public_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(c2_4_public_history)")
    }
    if "public_active" not in public_columns:
        connection.execute(
            "ALTER TABLE c2_4_public_history ADD COLUMN public_active INTEGER NOT NULL DEFAULT 1"
        )
    if "last_public_exit_reason" not in public_columns:
        connection.execute(
            "ALTER TABLE c2_4_public_history ADD COLUMN last_public_exit_reason TEXT NOT NULL DEFAULT ''"
        )
    connection.commit()


def record_first_gate_history(connection, candidate_ids: list[int]) -> int:
    selected = sorted({int(value) for value in candidate_ids})
    if not selected:
        return 0
    placeholders = ",".join("?" for _ in selected)
    rows = connection.execute(
        f"""SELECT p.*,c.network_id,c.token_address FROM candidate_production_records p
        JOIN candidates c ON c.candidate_id=p.candidate_id
        JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id AND q.state='completed'
        WHERE p.candidate_id IN ({placeholders})""",
        tuple(selected),
    ).fetchall()
    saved = 0
    for row in rows:
        gate = evaluate_first_gate({
            "assetId": row["asset_id"], "chainId": row["network_id"],
            "contractAddress": row["token_address"], "pairAddress": row["pair_address"],
            "tokenSide": row["token_side"], "t0Status": row["t0_status"],
            "ageDays": row["age_days"], "observedBuys": row["observed_buys"],
            "observedSells": row["observed_sells"], "confirmedHardBlock": bool(row["confirmed_hard_block"]),
        })
        if not gate["passed"]:
            continue
        passed_at = utc_now()
        connection.execute(
            """INSERT INTO c2_4_first_gate_history(candidate_id,asset_id,passed_at,age_days_at_pass,checks_json,rule_version)
            VALUES(?,?,?,?,?,?) ON CONFLICT(candidate_id) DO NOTHING""",
            (row["candidate_id"], row["asset_id"], passed_at, row["age_days"], json.dumps(gate["checks"], ensure_ascii=False, sort_keys=True), gate["ruleVersion"]),
        )
        connection.execute(
            """INSERT INTO c2_4_lifecycle_state(candidate_id,asset_id,lifecycle_pool,updated_at)
            VALUES(?,?,'new_0_90',?) ON CONFLICT(candidate_id) DO UPDATE SET
            asset_id=excluded.asset_id,updated_at=excluded.updated_at""",
            (row["candidate_id"], row["asset_id"], passed_at),
        )
        saved += 1
    connection.commit()
    return saved


def _latest(connection, table: str, candidate_id: int, completed_at: str | None = None):
    if table == "market_observations":
        return latest_effective_market_row(connection, candidate_id, completed_at)
    cutoff = " AND observed_at<=?" if completed_at else ""
    parameters = (candidate_id, completed_at) if completed_at else (candidate_id,)
    return connection.execute(
        f"SELECT * FROM {table} WHERE candidate_id=?{cutoff} ORDER BY observed_at DESC LIMIT 1",
        parameters,
    ).fetchone()


def _sell_tax_pct(risk) -> object:
    if not risk:
        return None
    keys = set(risk.keys()) if hasattr(risk, "keys") else set()
    if "sell_tax_pct" in keys and risk["sell_tax_pct"] is not None:
        return risk["sell_tax_pct"]
    try:
        payload = json.loads(risk["payload_json"] or "{}") if "payload_json" in keys else {}
    except (TypeError, ValueError):
        payload = {}
    return payload.get("sellTaxPct", payload.get("sell_tax_pct"))


def _evaluation_project_evidence_ids(hard_gate_json: str | None) -> set[str]:
    try:
        payload = json.loads(hard_gate_json or "{}")
    except (TypeError, ValueError):
        return set()
    for check in payload.get("checks") or []:
        if check.get("code") == "product_evidence_present" and check.get("status") == "pass":
            return {str(value) for value in check.get("evidenceIds") or [] if value}
    return set()


def _qualifying_evidence_rows(
    connection,
    candidate_id: int,
    completed_at: str | None,
    hard_gate_json: str | None,
):
    referenced_ids = sorted(_evaluation_project_evidence_ids(hard_gate_json))
    clauses = []
    parameters: list[object] = [candidate_id]
    if completed_at:
        clauses.append("observed_at<=?")
        parameters.append(completed_at)
    if referenced_ids:
        clauses.append(f"evidence_id IN ({','.join('?' for _ in referenced_ids)})")
        parameters.extend(referenced_ids)
    window = f" AND ({' OR '.join(clauses)})" if clauses else ""
    return connection.execute(
        f"""SELECT * FROM product_evidence WHERE candidate_id=? AND status='qualifying'
        AND identity_status IN ('verified','market_matched'){window}""",
        tuple(parameters),
    ).fetchall()


def _current_public_state(
    connection,
    candidate_id: int,
    age_days: int | None,
    evidence_rows,
    completed_at: str | None = None,
    active_rule_version: str | None = None,
) -> str:
    """Recompute the persisted public state from the same C2.4 path inputs as snapshots."""

    from build_c2_4_snapshots import _latest_and_previous, _path_input
    from c2_1_observation_state import latest_effective_market_rows

    def rows(table: str) -> list[dict]:
        return [
            dict(row) for row in connection.execute(
                f"SELECT * FROM {table} WHERE candidate_id=?"
                + (" AND observed_at<=?" if completed_at else "")
                + " ORDER BY observed_at",
                (candidate_id, completed_at) if completed_at else (candidate_id,),
            )
        ]

    market, previous_market = latest_effective_market_rows(rows("market_observations"))
    risk, _ = _latest_and_previous(rows("risk_observations"))
    supply, previous_supply = _latest_and_previous(rows("supply_observations"))
    pool, _ = _latest_and_previous(rows("pool_window_observations"))
    path_input = _path_input(
        {"ageDays": age_days},
        market.get(candidate_id, {}),
        previous_market.get(candidate_id, {}),
        risk.get(candidate_id, {}),
        supply.get(candidate_id, {}),
        previous_supply.get(candidate_id, {}),
        pool.get(candidate_id, {}),
    )
    recent_repository = False
    product_usage = False
    for evidence in evidence_rows:
        product_usage = product_usage or evidence["evidence_type"] == "product_usage"
        if evidence["evidence_type"] != "github":
            continue
        try:
            payload = json.loads(evidence["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        recent_repository = recent_repository or bool(payload.get("recentNonDocumentationCommit"))
    item = {
        **path_input,
        "publicEligible": True,
        "recentQualifyingRepositoryActivity": recent_repository,
        "newVerifiedProductUsage": product_usage,
    }
    return determine_public_state(item, evaluate_strong_paths(item, active_version=active_rule_version))["publicState"] or "observing"


def record_completed_public_history(connection, candidate_ids: list[int]) -> dict:
    """Persist public entry, two-window normal exit, and immediate exit facts."""

    selected = sorted({int(value) for value in candidate_ids})
    if not selected:
        return {"checked": 0, "public": 0, "retained": 0, "normalExit": 0, "continued": 0, "stopped": 0}
    initialize_schema(connection)
    active_rule_version = load_active_rule_version()
    placeholders = ",".join("?" for _ in selected)
    rows = connection.execute(
        f"""SELECT p.*,c.network_id,c.token_address,t.state tracking_state,t.completed_at,
        t.source_states_json,
        COALESCE(em.evaluation_window_id,ec.evaluation_window_id) evaluation_window_id,
        COALESCE(em.evaluated_at,ec.evaluated_at) evaluation_matched_at,
        COALESCE(em.hard_gate_json,ec.hard_gate_json) hard_gate_json
        FROM candidate_production_records p JOIN candidates c ON c.candidate_id=p.candidate_id
        JOIN candidate_tracking_records t ON t.candidate_id=p.candidate_id
        LEFT JOIN evaluations em ON em.candidate_id=t.candidate_id AND em.evaluated_at=t.evaluated_at
        LEFT JOIN evaluations ec ON ec.candidate_id=t.candidate_id AND ec.is_current=1
        WHERE p.candidate_id IN ({placeholders})""",
        tuple(selected),
    ).fetchall()
    published = retained = normal_exit = continued = stopped = 0
    now = utc_now()
    for row in rows:
        completed_at = row["completed_at"]
        market = _latest(
            connection, "market_observations", int(row["candidate_id"]), completed_at
        )
        risk = _latest(
            connection, "risk_observations", int(row["candidate_id"]), completed_at
        )
        evidence_rows = _qualifying_evidence_rows(
            connection,
            int(row["candidate_id"]),
            completed_at,
            row["hard_gate_json"],
        )
        baseline = evaluate_public_baseline({
            "assetId": row["asset_id"], "chainId": row["network_id"],
            "contractAddress": row["token_address"], "pairAddress": row["pair_address"],
            "tokenSide": row["token_side"], "t0Status": row["t0_status"],
            "relationshipClass": row["relationship_class"],
            "deepTrackingState": row["tracking_state"],
            "evaluationWindowId": row["evaluation_window_id"],
            "evaluationCompletedAt": row["completed_at"],
            "riskState": risk["source_status"] if risk else "no_data",
            "confirmedHardBlock": confirmed_trade_block(risk),
            "severeAnomaly": confirmed_trade_block(risk),
            "sellQuoteState": market["standard_sell_quote_state"] if market else "no_data",
            "sellQuoteLossPct": market["standard_sell_quote_loss_pct"] if market else None,
            "sellTaxPct": _sell_tax_pct(risk),
            "projectEvidenceQualified": bool(evidence_rows), "projectEvidenceAttributable": bool(evidence_rows),
        }, active_version=active_rule_version)
        lifecycle = connection.execute(
            "SELECT * FROM c2_4_lifecycle_state WHERE candidate_id=?",
            (row["candidate_id"],),
        ).fetchone()
        history = connection.execute(
            "SELECT * FROM c2_4_public_history WHERE candidate_id=?",
            (row["candidate_id"],),
        ).fetchone()
        window_id = str(row["evaluation_window_id"] or "")
        if baseline["trackingState"] == "stopped_active_tracking":
            connection.execute(
                """UPDATE c2_4_lifecycle_state SET consecutive_completed_misses=0,
                last_exit_window_id=?,stopped_at=COALESCE(stopped_at,?),updated_at=?
                WHERE candidate_id=?""",
                (window_id, now, now, row["candidate_id"]),
            )
            connection.execute(
                """UPDATE c2_4_public_history SET public_active=0,
                last_public_exit_reason='immediate_risk_or_exit_anomaly' WHERE candidate_id=?""",
                (row["candidate_id"],),
            )
            stopped += 1
            continue
        if baseline["passed"]:
            qualified_while_new = bool(
                history and int(history["last_public_age_days"]) <= 90
            )
            continued_after_day_90 = bool(
                lifecycle and lifecycle["lifecycle_pool"] == "continued_91_plus"
            )
            if (
                row["age_days"] is not None
                and int(row["age_days"]) >= 91
                and not qualified_while_new
                and not continued_after_day_90
            ):
                if history:
                    connection.execute(
                        """UPDATE c2_4_public_history SET public_active=0,
                        last_public_exit_reason='not_public_while_new_at_day91'
                        WHERE candidate_id=?""",
                        (row["candidate_id"],),
                    )
                continue
            state = _current_public_state(
                connection,
                int(row["candidate_id"]),
                row["age_days"],
                evidence_rows,
                completed_at,
                active_rule_version,
            )
            connection.execute(
                """INSERT INTO c2_4_public_history(candidate_id,asset_id,first_public_at,last_public_at,
                last_public_age_days,last_public_state,last_evaluation_window_id,public_active,last_public_exit_reason)
                VALUES(?,?,?,?,?,?,?,1,'') ON CONFLICT(candidate_id) DO UPDATE SET
                asset_id=excluded.asset_id,last_public_at=excluded.last_public_at,
                last_public_age_days=excluded.last_public_age_days,last_public_state=excluded.last_public_state,
                last_evaluation_window_id=excluded.last_evaluation_window_id,public_active=1,
                last_public_exit_reason=''""",
                (row["candidate_id"], row["asset_id"], now, now, row["age_days"], state, window_id),
            )
            connection.execute(
                """UPDATE c2_4_lifecycle_state SET consecutive_completed_misses=0,
                last_exit_window_id=?,stopped_at=NULL,updated_at=? WHERE candidate_id=?""",
                (window_id, now, row["candidate_id"]),
            )
            published += 1
            if row["age_days"] is not None and int(row["age_days"]) >= 91 and qualified_while_new:
                connection.execute(
                    """UPDATE c2_4_lifecycle_state SET lifecycle_pool='continued_91_plus',
                    continued_tracking_since=COALESCE(continued_tracking_since,?),updated_at=? WHERE candidate_id=?""",
                    (now, now, row["candidate_id"]),
                )
                continued += 1
            continue

        if not history or not int(history["public_active"] or 0):
            continue
        try:
            source_states = json.loads(row["source_states_json"] or "{}")
        except (TypeError, ValueError):
            source_states = {}
        non_project_states = {
            "quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure"
        }
        if row["tracking_state"] != "completed" or any(
            state in non_project_states for state in source_states.values()
        ):
            retained += 1
            continue
        decision = normal_exit_decision({
            "confirmedHardBlock": confirmed_trade_block(risk),
            "severeAnomaly": confirmed_trade_block(risk),
            "sellQuoteLossPct": market["standard_sell_quote_loss_pct"] if market else None,
            "sellTaxPct": _sell_tax_pct(risk),
            "consecutiveCompletedMisses": int(lifecycle["consecutive_completed_misses"] or 0) if lifecycle else 0,
            "lastExitWindowId": lifecycle["last_exit_window_id"] if lifecycle else "",
        }, window_id, True, active_version=active_rule_version)
        connection.execute(
            """UPDATE c2_4_lifecycle_state SET consecutive_completed_misses=?,
            last_exit_window_id=?,updated_at=? WHERE candidate_id=?""",
            (decision["consecutiveMisses"], window_id, now, row["candidate_id"]),
        )
        if decision["exit"]:
            connection.execute(
                """UPDATE c2_4_public_history SET public_active=0,
                last_public_exit_reason='two_distinct_completed_misses' WHERE candidate_id=?""",
                (row["candidate_id"],),
            )
            normal_exit += 1
        else:
            retained += 1
    connection.commit()
    return {
        "checked": len(rows), "public": published, "retained": retained,
        "normalExit": normal_exit, "continued": continued, "stopped": stopped,
    }


def migrate_qualified_day91(connection) -> int:
    """Move only assets with saved new-period public history; never discover old assets."""

    initialize_schema(connection)
    now = utc_now()
    connection.execute(
        """UPDATE c2_4_public_history AS ph SET public_active=0,
        last_public_exit_reason='not_public_while_new_at_day91'
        WHERE ph.public_active=1 AND ph.last_public_age_days>90
          AND EXISTS(
            SELECT 1 FROM c2_4_lifecycle_state l
            WHERE l.candidate_id=ph.candidate_id AND l.lifecycle_pool='new_0_90'
          )
          AND EXISTS(
            SELECT 1 FROM candidate_production_records p
            WHERE p.candidate_id=ph.candidate_id AND p.age_days>=91
          )"""
    )
    cursor = connection.execute(
        """UPDATE c2_4_lifecycle_state AS l SET lifecycle_pool='continued_91_plus',
        continued_tracking_since=COALESCE(continued_tracking_since,?),updated_at=?
        WHERE lifecycle_pool='new_0_90' AND stopped_at IS NULL
          AND EXISTS(SELECT 1 FROM candidate_production_records p WHERE p.candidate_id=l.candidate_id AND p.age_days>=91)
          AND EXISTS(SELECT 1 FROM c2_4_first_gate_history h WHERE h.candidate_id=l.candidate_id AND h.age_days_at_pass<=90)
          AND EXISTS(SELECT 1 FROM c2_4_public_history ph WHERE ph.candidate_id=l.candidate_id AND ph.last_public_age_days<=90)""",
        (now, now),
    )
    connection.commit()
    return int(cursor.rowcount or 0)


def reconcile_existing_tracking_history(connection) -> dict:
    """Persist C2.4 history for every already tracked object before publishing snapshots."""

    initialize_schema(connection)
    tracked_ids = [
        int(row[0])
        for row in connection.execute(
            "SELECT candidate_id FROM candidate_tracking_records ORDER BY candidate_id"
        )
    ]
    completed_ids = [
        int(row[0])
        for row in connection.execute(
            """SELECT t.candidate_id FROM candidate_tracking_records t
            LEFT JOIN evaluations em ON em.candidate_id=t.candidate_id AND em.evaluated_at=t.evaluated_at
            LEFT JOIN evaluations ec ON ec.candidate_id=t.candidate_id AND ec.is_current=1
            LEFT JOIN c2_4_public_history h ON h.candidate_id=t.candidate_id
            WHERE t.state='completed' AND (
              h.candidate_id IS NULL OR h.last_evaluation_window_id!=COALESCE(
                em.evaluation_window_id,ec.evaluation_window_id,''
              )
            ) ORDER BY t.candidate_id"""
        )
    ]
    first_gate = record_first_gate_history(connection, tracked_ids)
    public_history = record_completed_public_history(connection, completed_ids)
    migrated = migrate_qualified_day91(connection)
    return {
        "tracked": len(tracked_ids),
        "completed": len(completed_ids),
        "firstGateHistory": first_gate,
        "publicHistory": public_history,
        "migratedDay91": migrated,
    }
