#!/usr/bin/env python3
"""Bounded, resumable C2.2 tracking for candidates handed off by screening."""

from __future__ import annotations

import json
from pathlib import Path

from c2_1_db import DEFAULT_DB_PATH, open_pipeline_db, utc_now
from c2_1_enrichment import (
    JsonClient,
    collect_github,
    collect_market,
    collect_quotes,
    collect_risk_and_supply,
    collect_robinhood_official_assets,
    collect_website_identity,
    config,
)
from c2_1_observation_state import confirmed_trade_block, latest_effective_market_row
from c2_1_path4 import collect_path4
from c2_1_pipeline import evaluate_all, sync_product_evidence_states
from c2_1_resilience import cursor_decision, day_window, hour_window
from c2_1_rules import age_band, age_days, load_rules
from candidate_production import refresh_production_contracts
from c2_4_tracking import (
    initialize_schema as initialize_c2_4_schema,
    migrate_qualified_day91,
    record_completed_public_history,
    record_first_gate_history,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = PROJECT_ROOT / "storage" / "c2.2-tracking-repair.sql"
TERMINAL_SOURCE_STATES = {"success", "no_data", "unsupported"}
BASELINE_COMPLETION_KEYS = {
    "market",
    "quote",
    "risk",
    "supply",
    "product_usage",
    "project_evidence",
    "evaluation",
}
SOURCE_TO_STAGE = {
    "dexscreener": "market",
    "project_website_identity": "website_identity",
    "github": "github",
    "goplus": "risk_supply",
    "standard_sell_quote": "quote",
    "c2_1_path4": "path4",
    "robinhood_official_assets": "robinhood_official_assets",
}
SOURCE_STATE_KEYS = {
    "dexscreener": ("market",),
    "project_website_identity": ("project_evidence",),
    "github": ("project_evidence",),
    "goplus": ("risk", "supply"),
    "standard_sell_quote": ("quote",),
    "c2_1_path4": ("path4",),
    "robinhood_official_assets": ("project_evidence", "risk"),
}
RETRYABLE_SOURCE_STATES = {
    "quota_limited",
    "source_failure",
    "configuration_missing",
    "program_failure",
}


def baseline_states_complete(states: dict[str, str]) -> bool:
    return all(
        states.get(key) in TERMINAL_SOURCE_STATES
        or key == "evaluation" and states.get(key) == "success"
        for key in BASELINE_COMPLETION_KEYS
    )


def reconcile_baseline_completion(connection) -> int:
    """Release records blocked only by the independent path4 evidence layer."""

    changed = 0
    completed_at = utc_now()
    rows = connection.execute(
        "SELECT candidate_id,source_states_json FROM candidate_tracking_records WHERE state='partial'"
    ).fetchall()
    for row in rows:
        try:
            states = json.loads(row["source_states_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not baseline_states_complete(states):
            continue
        connection.execute(
            """UPDATE candidate_tracking_records
            SET state='completed',completed_at=COALESCE(completed_at,?),updated_at=?
            WHERE candidate_id=?""",
            (completed_at, completed_at, int(row["candidate_id"])),
        )
        changed += 1
    if changed:
        connection.commit()
    return changed


def initialize_tracking_schema(connection) -> None:
    connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
    initialize_c2_4_schema(connection)
    connection.commit()


def load_tracking_records(db_path=DEFAULT_DB_PATH) -> dict[int, dict]:
    path = Path(db_path)
    if not path.exists():
        return {}
    connection = open_pipeline_db(path)
    try:
        present = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_tracking_records'"
        ).fetchone()
        if not present:
            return {}
        return {
            int(row["candidate_id"]): {
                "state": row["state"],
                "inputUpdatedAt": row["input_updated_at"],
                "sourceStates": json.loads(row["source_states_json"] or "{}"),
                "evaluatedAt": row["evaluated_at"],
                "lastAttemptAt": row["last_attempt_at"],
                "completedAt": row["completed_at"],
                "errorDetail": row["error_detail"],
            }
            for row in connection.execute("SELECT * FROM candidate_tracking_records")
        }
    finally:
        connection.close()


def _select_candidates(
    connection,
    limit: int,
    *,
    retry_source_id: str | None = None,
    refresh_completed: bool = False,
    exclude_candidate_ids: set[int] | None = None,
) -> list:
    retry_clause = ""
    parameters: list[object] = []
    if retry_source_id:
        state_checks = []
        for key in SOURCE_STATE_KEYS[retry_source_id]:
            state_checks.append(
                f"json_extract(t.source_states_json,'$.{key}') IN ({','.join('?' for _ in RETRYABLE_SOURCE_STATES)})"
            )
            parameters.extend(sorted(RETRYABLE_SOURCE_STATES))
        retry_clause = f"""AND (
          (t.state='partial' AND ({' OR '.join(state_checks)}))
          OR EXISTS(
            SELECT 1 FROM source_health sh
            WHERE sh.source_id=? AND sh.scope_key=CAST(p.candidate_id AS TEXT)
              AND sh.status IN ('quota_limited','source_failure','configuration_missing','program_failure')
          )
        )"""
        parameters.append(retry_source_id)
    elif refresh_completed:
        retry_clause = ""
    else:
        retry_clause = """AND (
            t.candidate_id IS NULL
            OR t.state!='completed'
            OR datetime(t.input_updated_at)<datetime(p.updated_at)
          )"""
    excluded = sorted({int(value) for value in (exclude_candidate_ids or set())})
    exclude_clause = ""
    if excluded:
        exclude_clause = f"AND p.candidate_id NOT IN ({','.join('?' for _ in excluded)})"
        parameters.extend(excluded)
    parameters.append(max(0, int(limit)))
    return connection.execute(
        f"""
        WITH ranked AS (
          SELECT c.*,p.qualification_batch_id,p.updated_at AS production_updated_at,
                 p.front_eligible,p.front_contract_ready,p.qualified_at,
                 CASE WHEN t.candidate_id IS NULL THEN 0 WHEN t.state!='completed' THEN 1 ELSE 2 END
                   AS processing_state_rank,
                 CASE
                   WHEN EXISTS(SELECT 1 FROM product_evidence pe
                     WHERE pe.candidate_id=c.candidate_id AND pe.status='qualifying') THEN 0
                   WHEN COALESCE(c.official_repo,'')!='' OR COALESCE(c.mapped_project_id,'')!='' THEN 1
                   ELSE 2
                 END AS evidence_priority_rank,
                 ROW_NUMBER() OVER (
                   PARTITION BY c.network_id
                   ORDER BY CASE WHEN t.candidate_id IS NULL THEN 0 WHEN t.state!='completed' THEN 1 ELSE 2 END,
                            CASE
                              WHEN EXISTS(SELECT 1 FROM product_evidence pe
                                WHERE pe.candidate_id=c.candidate_id AND pe.status='qualifying') THEN 0
                              WHEN COALESCE(c.official_repo,'')!='' OR COALESCE(c.mapped_project_id,'')!='' THEN 1
                              ELSE 2
                            END,
                            datetime(COALESCE(t.completed_at,p.qualified_at)),p.candidate_id
                 ) AS chain_rank,
                 CASE c.network_id
                   WHEN 'ethereum-mainnet' THEN 1
                   WHEN 'solana-mainnet' THEN 2
                   WHEN 'base-mainnet' THEN 3
                   WHEN 'arbitrum-mainnet' THEN 4
                   WHEN 'bnb-mainnet' THEN 5
                   WHEN 'robinhood-mainnet' THEN 6
                   ELSE 99
                 END AS chain_order
          FROM candidate_production_records p
          JOIN candidate_qualification_batches b
            ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
          JOIN candidate_first_gate_queue fq
            ON fq.candidate_id=p.candidate_id AND fq.state='completed'
          JOIN candidates c ON c.candidate_id=p.candidate_id
          LEFT JOIN candidate_tracking_records t ON t.candidate_id=p.candidate_id
          LEFT JOIN c2_4_lifecycle_state l ON l.candidate_id=p.candidate_id
          WHERE (
              (p.tracking_eligible=1 AND p.age_days BETWEEN 0 AND 90)
              OR (l.lifecycle_pool='continued_91_plus' AND l.stopped_at IS NULL)
            )
            {retry_clause}
            {exclude_clause}
        )
        SELECT * FROM ranked
        ORDER BY processing_state_rank,chain_rank,chain_order,candidate_id
        LIMIT ?
        """,
        tuple(parameters),
    ).fetchall()


def _queue_counts(connection) -> dict[str, int]:
    row = connection.execute(
        """SELECT COUNT(*) total,
        SUM(CASE WHEN t.state='completed' AND datetime(t.input_updated_at)>=datetime(p.updated_at) THEN 1 ELSE 0 END) completed,
        SUM(CASE WHEN t.state='partial' THEN 1 ELSE 0 END) partial
        FROM candidate_production_records p
        JOIN candidate_qualification_batches b
          ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
        JOIN candidate_first_gate_queue fq
          ON fq.candidate_id=p.candidate_id AND fq.state='completed'
        LEFT JOIN candidate_tracking_records t ON t.candidate_id=p.candidate_id
        LEFT JOIN c2_4_lifecycle_state l ON l.candidate_id=p.candidate_id
        WHERE (p.tracking_eligible=1 AND p.age_days BETWEEN 0 AND 90)
           OR (l.lifecycle_pool='continued_91_plus' AND l.stopped_at IS NULL)"""
    ).fetchone()
    total = int(row["total"] or 0)
    completed = int(row["completed"] or 0)
    partial = int(row["partial"] or 0)
    return {
        "total": total,
        "completed": completed,
        "partial": partial,
        "remaining": max(0, total - completed),
    }


def _baseline_prerequisite_ids(connection, candidate_ids: list[int], *, require_quote: bool) -> list[int]:
    """Return objects worth the next, more expensive layer of deep tracking."""

    selected = []
    for candidate_id in candidate_ids:
        production = connection.execute(
            "SELECT relationship_class FROM candidate_production_records WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if not production or production["relationship_class"] == "D":
            continue
        evidence = connection.execute(
            """SELECT 1 FROM product_evidence WHERE candidate_id=? AND status='qualifying'
            AND identity_status IN ('verified','market_matched') LIMIT 1""",
            (candidate_id,),
        ).fetchone()
        if not evidence:
            continue
        if require_quote:
            risk = connection.execute(
                "SELECT * FROM risk_observations WHERE candidate_id=? ORDER BY observed_at DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
            # A missing risk response is not a confirmed trade block.  The
            # user-authorized trial lets a successful read-only quote proceed;
            # only an explicit freeze/blacklist/sell block stops deeper work.
            if risk and confirmed_trade_block(risk):
                continue
            market = latest_effective_market_row(connection, candidate_id)
            if (
                not market
                or market["standard_sell_quote_state"] != "success"
            ):
                continue
        selected.append(candidate_id)
    return selected


def _latest_state(connection, table: str, candidate_id: int) -> str | None:
    row = connection.execute(
        f"SELECT source_status FROM {table} WHERE candidate_id=? ORDER BY observed_at DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _candidate_source_states(connection, candidate_id: int) -> dict[str, str]:
    market_row = latest_effective_market_row(connection, candidate_id)
    production = connection.execute(
        "SELECT market_source_state,market_state FROM candidate_production_records WHERE candidate_id=?",
        (candidate_id,),
    ).fetchone()
    market_state = (
        str(market_row["source_status"])
        if market_row and market_row["source_status"]
        else str((production["market_source_state"] or production["market_state"] or "no_data"))
        if production
        else "no_data"
    )
    quote_state = (
        str(market_row["standard_sell_quote_state"])
        if market_row and market_row["standard_sell_quote_state"]
        else "no_data" if market_state in TERMINAL_SOURCE_STATES else market_state
    )
    risk_state = _latest_state(connection, "risk_observations", candidate_id) or "no_data"
    supply_state = _latest_state(connection, "supply_observations", candidate_id)
    if not supply_state:
        supply_state = "no_data" if risk_state in TERMINAL_SOURCE_STATES else risk_state
    path4_state = _latest_state(connection, "pool_window_observations", candidate_id) or "no_data"
    usage = connection.execute(
        """SELECT 1 FROM product_evidence
        WHERE candidate_id=? AND evidence_type='product_usage' AND status='qualifying' LIMIT 1""",
        (candidate_id,),
    ).fetchone()
    project_evidence = connection.execute(
        "SELECT 1 FROM product_evidence WHERE candidate_id=? AND status='qualifying' LIMIT 1",
        (candidate_id,),
    ).fetchone()
    evaluation = connection.execute(
        "SELECT evaluated_at FROM evaluations WHERE candidate_id=? AND is_current=1",
        (candidate_id,),
    ).fetchone()
    return {
        "market": market_state,
        "quote": quote_state,
        "risk": risk_state,
        "supply": supply_state,
        "path4": path4_state,
        "product_usage": "success" if usage else "no_data",
        "project_evidence": "success" if project_evidence else "no_data",
        "evaluation": "success" if evaluation else "program_failure",
    }


def _select_deep_structure_candidates(connection, limit: int) -> list[int]:
    """Select a bounded post-baseline path4 queue without blocking publication."""

    rules, _ = load_rules()
    rows = connection.execute(
        """SELECT c.candidate_id,c.effective_t0,p.relationship_class
        FROM candidate_tracking_records t
        JOIN candidate_production_records p ON p.candidate_id=t.candidate_id
        JOIN candidates c ON c.candidate_id=t.candidate_id
        LEFT JOIN c2_4_lifecycle_state l ON l.candidate_id=t.candidate_id
        WHERE t.state='completed' AND p.relationship_class IN ('A','B','C')
          AND (p.age_days BETWEEN 0 AND 90
               OR (l.lifecycle_pool='continued_91_plus' AND l.stopped_at IS NULL))
          AND EXISTS(
            SELECT 1 FROM product_evidence pe
            WHERE pe.candidate_id=t.candidate_id AND pe.status='qualifying'
              AND pe.identity_status IN ('verified','market_matched')
          )
        ORDER BY COALESCE((
          SELECT MAX(pw.observed_at) FROM pool_window_observations pw
          WHERE pw.candidate_id=t.candidate_id
        ),''),t.completed_at,t.candidate_id
        LIMIT ?""",
        (max(50, max(1, int(limit)) * 50),),
    ).fetchall()
    selected = []
    for row in rows:
        candidate_id = int(row["candidate_id"])
        if not _baseline_prerequisite_ids(
            connection, [candidate_id], require_quote=True
        ):
            continue
        band = age_band(age_days(row["effective_t0"], utc_now()), rules)
        window_key = hour_window() if band in {"age_0_2", "age_3_6"} else day_window()
        if cursor_decision(
            connection,
            "c2_1_path4",
            str(candidate_id),
            "indexed_pool_supply",
            window_key,
        )["action"] != "run":
            continue
        selected.append(candidate_id)
        if len(selected) >= max(0, int(limit)):
            break
    return selected


def run_deep_structure_batch(*, db_path=DEFAULT_DB_PATH, limit=1) -> dict:
    """Run one recoverable path4 enhancement only after baseline handoff completes."""

    connection = open_pipeline_db(db_path)
    try:
        initialize_tracking_schema(connection)
        candidate_ids = _select_deep_structure_candidates(connection, limit)
        if not candidate_ids:
            return {
                "status": "completed",
                "selected": 0,
                "hasMore": False,
                "queueRole": "post_baseline_independent_enhancement",
            }
        client = JsonClient()
        config_payload, networks = config()
        result = collect_path4(
            connection,
            client,
            config_payload,
            networks,
            candidate_ids=candidate_ids,
        )
        evaluation = evaluate_all(connection, candidate_ids=candidate_ids)
        completed_at = utc_now()
        for candidate_id in candidate_ids:
            states = _candidate_source_states(connection, candidate_id)
            evaluated = connection.execute(
                "SELECT evaluated_at FROM evaluations WHERE candidate_id=? AND is_current=1",
                (candidate_id,),
            ).fetchone()
            connection.execute(
                """UPDATE candidate_tracking_records SET state='completed',
                source_states_json=?,evaluated_at=?,completed_at=?,error_detail='',updated_at=?
                WHERE candidate_id=?""",
                (
                    json.dumps(states, ensure_ascii=False, sort_keys=True),
                    evaluated[0] if evaluated else None,
                    completed_at,
                    completed_at,
                    candidate_id,
                ),
            )
        connection.commit()
        public_history = record_completed_public_history(connection, candidate_ids)
        migrated = migrate_qualified_day91(connection)
        has_more = bool(_select_deep_structure_candidates(connection, 1))
        retryable = any(
            int((result.get("states") or {}).get(state) or 0)
            for state in RETRYABLE_SOURCE_STATES
        )
        return {
            "status": "partial_success" if retryable else "completed",
            "selected": len(candidate_ids),
            "candidateIds": candidate_ids,
            "hasMore": has_more and not retryable,
            "queueRole": "post_baseline_independent_enhancement",
            "path4": result,
            "evaluation": evaluation,
            "publicHistory": public_history,
            "migratedDay91": migrated,
        }
    finally:
        connection.close()


def _run_stage(connection, stage: str, candidate_ids: list[int], client, config_payload, networks):
    if stage == "market":
        return collect_market(connection, client=client, candidate_ids=candidate_ids)
    if stage == "website_identity":
        return collect_website_identity(connection, client=client, candidate_ids=candidate_ids)
    if stage == "github":
        result = collect_github(connection, client=client, candidate_ids=candidate_ids)
        result["evidenceStateSync"] = sync_product_evidence_states(connection, candidate_ids)
        return result
    if stage == "risk_supply":
        return collect_risk_and_supply(connection, client=client, candidate_ids=candidate_ids)
    if stage == "quote":
        return collect_quotes(connection, client=client, candidate_ids=candidate_ids)
    if stage == "path4":
        return collect_path4(
            connection,
            client,
            config_payload,
            networks,
            candidate_ids=candidate_ids,
        )
    if stage == "robinhood_official_assets":
        return collect_robinhood_official_assets(
            connection,
            client=client,
            candidate_ids=candidate_ids,
            force_recheck=True,
        )
    raise ValueError(f"unsupported tracking stage: {stage}")


def run_candidate_tracking_batch(
    *,
    db_path=DEFAULT_DB_PATH,
    limit=25,
    only_source_id: str | None = None,
    refresh_completed: bool = False,
    exclude_candidate_ids: set[int] | None = None,
) -> dict:
    """Run one bounded candidate batch; callers own the single-writer pause."""

    if only_source_id is not None and only_source_id not in SOURCE_TO_STAGE:
        raise ValueError("这个来源不属于候选凸性跟踪。")
    connection = open_pipeline_db(db_path)
    try:
        initialize_tracking_schema(connection)
        reconciled_baseline = reconcile_baseline_completion(connection)
        # The automatic first-pass queue favors frequent recoverable
        # checkpoints over waiting several minutes for one project website.
        # A manual single-source retry keeps the original patient policy.
        client = (
            JsonClient()
            if only_source_id
            else JsonClient(timeout=8, retry_delays=(0, 2))
        )
        if only_source_id == "robinhood_official_assets":
            registry = collect_robinhood_official_assets(
                connection, client=client, force_recheck=True
            )
            refreshed = refresh_production_contracts(
                connection, registry.get("candidateIds") or []
            )
            return {
                "status": "completed" if registry.get("state") in {"success", "no_data"} else "partial_success",
                "selected": len(registry.get("candidateIds") or []),
                "completed": len(refreshed),
                "partial": 0 if registry.get("state") in {"success", "no_data"} else 1,
                "stages": {"robinhood_official_assets": registry},
                "migratedDay91": 0,
                "queue": _queue_counts(connection),
                "reconciledBaseline": reconciled_baseline,
            }
        registry = collect_robinhood_official_assets(connection, client=client)
        refresh_production_contracts(connection, registry.get("candidateIds") or [])
        migrated_day91 = migrate_qualified_day91(connection)
        rows = _select_candidates(
            connection,
            limit,
            retry_source_id=only_source_id,
            refresh_completed=refresh_completed,
            exclude_candidate_ids=exclude_candidate_ids,
        )
        if not rows:
            return {
                "status": "completed",
                "selected": 0,
                "completed": 0,
                "partial": 0,
                "stages": {},
                "migratedDay91": migrated_day91,
                "queue": _queue_counts(connection),
                "reconciledBaseline": reconciled_baseline,
            }
        candidate_ids = [int(row["candidate_id"]) for row in rows]
        record_first_gate_history(connection, candidate_ids)
        started_at = utc_now()
        for row in rows:
            connection.execute(
                """INSERT INTO candidate_tracking_records(
                  candidate_id,qualification_batch_id,input_updated_at,state,attempt_count,
                  source_states_json,last_attempt_at,updated_at
                ) VALUES(?,?,?,'running',1,'{}',?,?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                  qualification_batch_id=excluded.qualification_batch_id,
                  input_updated_at=excluded.input_updated_at,state='running',
                  attempt_count=candidate_tracking_records.attempt_count+1,
                  last_attempt_at=excluded.last_attempt_at,updated_at=excluded.updated_at,
                  error_detail=''""",
                (
                    row["candidate_id"],
                    row["qualification_batch_id"],
                    row["production_updated_at"],
                    started_at,
                    started_at,
                ),
            )
        connection.commit()

        config_payload, networks = config()
        stage_results = {"robinhood_official_assets": registry}
        stage_errors = {}
        stage_targets: dict[str, list[int]] = {}
        if only_source_id:
            stage_targets[SOURCE_TO_STAGE[only_source_id]] = candidate_ids
        else:
            # First collect the inexpensive facts that determine whether an
            # object has an attributable project identity.  Do not spend risk
            # quota on D/no-evidence objects that can never enter the public
            # A/B/C set.
            for stage in ("market", "website_identity", "github"):
                stage_targets[stage] = candidate_ids
        for stage, target_ids in list(stage_targets.items()):
            try:
                stage_results[stage] = _run_stage(
                    connection,
                    stage,
                    target_ids,
                    client,
                    config_payload,
                    networks,
                )
            except Exception as error:
                stage_errors[stage] = f"{type(error).__name__}: {error}"

        if not only_source_id:
            refreshed_contracts = refresh_production_contracts(connection, candidate_ids)
            stage_results["production_contract_refresh"] = {
                "candidates": len(candidate_ids),
                "changed": len(refreshed_contracts),
            }
            evidence_ids = _baseline_prerequisite_ids(
                connection, candidate_ids, require_quote=False
            )
            stage_targets["risk_supply"] = evidence_ids
            if evidence_ids:
                try:
                    stage_results["risk_supply"] = _run_stage(
                        connection,
                        "risk_supply",
                        evidence_ids,
                        client,
                        config_payload,
                        networks,
                    )
                except Exception as error:
                    stage_errors["risk_supply"] = f"{type(error).__name__}: {error}"
            else:
                stage_results["risk_supply"] = {
                    "candidates": 0,
                    "state": "not_required_without_attributable_project_evidence",
                }

            # Quote collection is independent from risk-source availability.
            # A source failure must not prevent a real read-only sell quote.
            quote_ids = evidence_ids
            stage_targets["quote"] = quote_ids
            if quote_ids:
                try:
                    stage_results["quote"] = _run_stage(
                        connection, "quote", quote_ids, client, config_payload, networks
                    )
                except Exception as error:
                    stage_errors["quote"] = f"{type(error).__name__}: {error}"
            else:
                stage_results["quote"] = {"candidates": 0, "state": "not_required_this_layer"}

            # Full-pool OHLCV and historical-supply reconstruction is an
            # independent strong-evidence path, not a prerequisite for the
            # 21k-object first tracking handoff.  It remains available through
            # the dedicated c2_1_path4 source retry and runs after this queue;
            # doing it inline here made every 25-object checkpoint take many
            # minutes and prevented the baseline queue from ever finishing.
            stage_results["path4"] = {
                "candidates": 0,
                "state": "deferred_until_baseline_queue_complete",
            }

        evaluation = evaluate_all(connection, candidate_ids=candidate_ids)
        completed = partial = 0
        finished_at = utc_now()
        stage_source_keys = {
            "market": ("market",),
            "website_identity": ("project_evidence",),
            "github": ("project_evidence",),
            "risk_supply": ("risk", "supply"),
            "quote": ("quote",),
            "path4": ("path4",),
        }
        for row in rows:
            candidate_id = int(row["candidate_id"])
            states = _candidate_source_states(connection, candidate_id)
            for stage, detail in stage_errors.items():
                if candidate_id not in stage_targets.get(stage, []):
                    continue
                for key in stage_source_keys[stage]:
                    if states.get(key) != "success":
                        states[key] = "program_failure"
            is_complete = baseline_states_complete(states)
            state = "completed" if is_complete else "partial"
            completed += int(is_complete)
            partial += int(not is_complete)
            evaluated = connection.execute(
                "SELECT evaluated_at FROM evaluations WHERE candidate_id=? AND is_current=1",
                (candidate_id,),
            ).fetchone()
            production_updated = connection.execute(
                """SELECT COALESCE(
                  (SELECT updated_at FROM candidate_production_records WHERE candidate_id=?),
                  (SELECT updated_at FROM candidates WHERE candidate_id=?)
                )""",
                (candidate_id, candidate_id),
            ).fetchone()
            connection.execute(
                """UPDATE candidate_tracking_records SET state=?,input_updated_at=?,source_states_json=?,evaluated_at=?,
                completed_at=?,error_detail=?,updated_at=? WHERE candidate_id=?""",
                (
                    state,
                    production_updated[0] if production_updated else row["production_updated_at"],
                    json.dumps(states, ensure_ascii=False, sort_keys=True),
                    evaluated[0] if evaluated else None,
                    finished_at if is_complete else None,
                    "; ".join(stage_errors.values()),
                    finished_at,
                    candidate_id,
                ),
            )
        connection.commit()
        public_history = record_completed_public_history(connection, candidate_ids)
        migrated_day91 += migrate_qualified_day91(connection)
        return {
            "status": "completed" if not partial else "partial_success",
            "candidateIds": candidate_ids,
            "selected": len(rows),
            "completed": completed,
            "partial": partial,
            "stages": stage_results,
            "stageErrors": stage_errors,
            "evaluation": evaluation,
            "publicHistory": public_history,
            "migratedDay91": migrated_day91,
            "queue": _queue_counts(connection),
            "reconciledBaseline": reconciled_baseline,
        }
    finally:
        connection.close()
