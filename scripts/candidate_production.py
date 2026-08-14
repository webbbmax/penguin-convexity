#!/usr/bin/env python3
"""C2.2 candidate production worker with explicit migration and resumable queues."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
import urllib.parse
import uuid
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from c2_1_db import DEFAULT_DB_PATH, json_text, open_pipeline_db, utc_now
from c2_1_enrichment import (
    JsonClient,
    collect_market,
    config as load_source_config,
    normalize as normalize_for_network,
)
from c2_1_resilience import commit_cursor, cursor_decision, day_window
from c2_1_observation_state import confirmed_trade_block


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = PROJECT_ROOT / "storage" / "c2.2-candidate-production-migration.sql"
FIRST_GATE_MIGRATION_PATH = PROJECT_ROOT / "storage" / "c2.2-first-gate-handoff-migration.sql"
RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "c2.2" / "candidate-production"
STATUS_PATH = RUNTIME_ROOT / "status.json"
LOCK_PATH = RUNTIME_ROOT / "worker.lock"
RULE_VERSION = "c2.2-candidate-production-v1"
GATE0_RUN_ID = "gate0-solfinal-20260809T045924Z-f7bbd2"
QUEUES = ("daily_incremental", "historical_backlog")
NETWORKS = (
    "ethereum-mainnet",
    "solana-mainnet",
    "base-mainnet",
    "arbitrum-mainnet",
    "bnb-mainnet",
    "robinhood-mainnet",
)
SOURCE_STATES = {
    "success", "no_data", "quota_limited", "source_failure", "unsupported",
    "configuration_missing", "program_failure",
}
LOCAL_STATES = {
    "local_pass", "known_continuation", "known_quote_or_wrapped_asset",
    "outside_90_days", "invalid_event_or_identity_conflict", "local_pending",
}
KNOWN_QUOTE_OR_WRAPPED = {
    ("ethereum-mainnet", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): "USDC",
    ("ethereum-mainnet", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"): "WETH",
    ("base-mainnet", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): "USDC",
    ("base-mainnet", "0x4200000000000000000000000000000000000006"): "WETH",
    ("arbitrum-mainnet", "0xaf88d065e77c8cc2239327c5edb3a432268e5831"): "USDC",
    ("arbitrum-mainnet", "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"): "WETH",
    ("bnb-mainnet", "0x55d398326f99059ff775485246999027b3197955"): "USDT",
    ("bnb-mainnet", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"): "WBNB",
    ("solana-mainnet", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"): "USDC",
    ("solana-mainnet", "So11111111111111111111111111111111111111112"): "WSOL",
}


def parse_time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def age_days(t0: str | None, as_of: str | None = None) -> int | None:
    start = parse_time(t0)
    end = parse_time(as_of) if as_of else datetime.now(timezone.utc)
    if not start or not end:
        return None
    return math.floor((end - start).total_seconds() / 86400)


def market_pair_time(value) -> datetime | None:
    """Normalize provider pool creation time without inventing a T0."""

    if value in {None, ""}:
        return None
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, timezone.utc)
    except (TypeError, ValueError, OSError):
        return parse_time(str(value))


def retry_at_for_age(age: int | None, observed_at: str) -> str | None:
    if age is None or age > 90:
        return None
    hours = 6 if age <= 2 else 24 if age <= 7 else 72 if age <= 30 else 24 * 7
    return (parse_time(observed_at) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def deterministic_asset_id(row: sqlite3.Row) -> str:
    if row["mapped_asset_id"]:
        return row["mapped_asset_id"]
    digest = hashlib.sha256(f"{row['network_id']}|{row['token_address_normalized']}".encode()).hexdigest()[:24]
    return f"candidate-asset-{digest}"


def schema_ready(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key='candidate_production_schema_version'"
    ).fetchone()
    handoff = connection.execute(
        "SELECT value FROM schema_meta WHERE key='candidate_first_gate_handoff_schema_version'"
    ).fetchone()
    return bool(
        row and row[0] == "c2.2-candidate-production-v1"
        and handoff and handoff[0] == "c2.2-first-gate-handoff-v1"
    )


def migrate_database(path: Path = DEFAULT_DB_PATH) -> dict:
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    first_gate_migration = FIRST_GATE_MIGRATION_PATH.read_text(encoding="utf-8")
    with closing(open_pipeline_db(path)) as connection:
        connection.executescript(migration)
        connection.executescript(first_gate_migration)
        connection.commit()
        return {
            "status": "completed",
            "schemaVersion": connection.execute(
                "SELECT value FROM schema_meta WHERE key='candidate_production_schema_version'"
            ).fetchone()[0],
            "firstGateHandoffSchemaVersion": connection.execute(
                "SELECT value FROM schema_meta WHERE key='candidate_first_gate_handoff_schema_version'"
            ).fetchone()[0],
        }


def classify_local(row: sqlite3.Row, as_of: str) -> dict:
    network_id = str(row["network_id"] or "")
    address = str(row["token_address_normalized"] or "")
    effective_t0 = str(row["effective_t0"] or "")
    age = age_days(effective_t0, as_of)
    evidence = [f"candidate:{row['candidate_id']}", f"source:{row['source_run_id']}"]
    if network_id not in NETWORKS or not address or age is None or age < 0:
        return {"state": "invalid_event_or_identity_conflict", "reason": "invalid_or_future_event_identity", "plain": "链、地址或公开流通时间存在可复现冲突。", "age": age, "evidence": evidence}
    if age > 90:
        return {"state": "outside_90_days", "reason": "age_day_91_or_later", "plain": "当前已到第91天或更晚，退出90天候选池。", "age": age, "evidence": evidence}
    if row["continuity_status"] == "known_continuation":
        return {"state": "known_continuation", "reason": "known_existing_asset_continuation", "plain": "本地主库已确认这是既有资产的连续、包装或跨链关系。", "age": age, "evidence": evidence}
    label = KNOWN_QUOTE_OR_WRAPPED.get((network_id, address))
    if label:
        return {"state": "known_quote_or_wrapped_asset", "reason": "known_quote_or_wrapped_asset", "plain": f"确定地址表识别为常见报价或包装资产（{label}）。", "age": age, "evidence": evidence}
    if not row["source_run_id"] or not row["t0_evidence_type"]:
        return {"state": "local_pending", "reason": "deterministic_t0_evidence_pending", "plain": "现有本地事实不足以确认可复现T0，等待确定性证据。", "age": age, "evidence": evidence}
    return {"state": "local_pass", "reason": "deterministic_local_checks_passed", "plain": "已通过地址、事件、时间、连续资产和已知报价资产检查。", "age": age, "evidence": evidence}


def _input_hash(rows: list[sqlite3.Row]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row['candidate_id']}|{row['network_id']}|{row['token_address_normalized']}|{row['effective_t0']}\n".encode())
    return digest.hexdigest()


def _create_partition(connection: sqlite3.Connection, queue: str, network_id: str, rows: list[sqlite3.Row]) -> str:
    if not rows:
        raise ValueError("空分片不能创建。")
    digest = _input_hash(rows)
    partition_id = f"{queue}:{network_id}:{rows[0]['candidate_id']}-{rows[-1]['candidate_id']}:{digest[:12]}"
    now = utc_now()
    connection.execute(
        """
        INSERT OR IGNORE INTO candidate_scan_partitions(
          partition_id,queue_name,network_id,input_hash,state,total_count,created_at,updated_at
        ) VALUES(?,?,?,?,'pending',?,?,?)
        """,
        (partition_id, queue, network_id, digest, len(rows), now, now),
    )
    connection.executemany(
        "INSERT OR IGNORE INTO candidate_scan_partition_members(partition_id,sequence_no,candidate_id) VALUES(?,?,?)",
        [(partition_id, index, row["candidate_id"]) for index, row in enumerate(rows, start=1)],
    )
    connection.commit()
    return partition_id


def prepare_partitions(
    connection: sqlite3.Connection,
    *,
    queue: str,
    partition_size: int = 5000,
    historical_authorized: bool = False,
) -> dict:
    if queue not in QUEUES:
        raise ValueError("不支持的候选队列。")
    if queue == "historical_backlog" and not historical_authorized:
        return {"status": "not_authorized", "createdPartitions": 0, "createdMembers": 0}
    now = utc_now()
    created = members = 0
    for network_id in NETWORKS:
        if queue == "historical_backlog":
            sql = """
              SELECT c.* FROM candidates c
              WHERE c.network_id=? AND c.source_run_id=?
                AND NOT EXISTS(SELECT 1 FROM candidate_production_records p WHERE p.candidate_id=c.candidate_id)
                AND NOT EXISTS(
                  SELECT 1 FROM candidate_scan_partition_members m
                  JOIN candidate_scan_partitions sp ON sp.partition_id=m.partition_id
                  WHERE m.candidate_id=c.candidate_id AND sp.state IN ('pending','running','retrying','paused','failed')
                )
              ORDER BY c.candidate_id
            """
            params = (network_id, GATE0_RUN_ID)
        else:
            sql = """
              SELECT c.* FROM candidates c
              LEFT JOIN candidate_production_records p ON p.candidate_id=c.candidate_id
              WHERE c.network_id=? AND (
                (p.candidate_id IS NULL AND (c.source_run_id<>? OR c.local_stage='incremental_discovered'))
                OR (p.next_retry_at IS NOT NULL AND datetime(p.next_retry_at)<=datetime(?))
              ) AND NOT EXISTS(
                SELECT 1 FROM candidate_scan_partition_members m
                JOIN candidate_scan_partitions sp ON sp.partition_id=m.partition_id
                WHERE m.candidate_id=c.candidate_id AND sp.state IN ('pending','running','retrying','paused','failed')
              )
              ORDER BY c.candidate_id
            """
            params = (network_id, GATE0_RUN_ID, now)
        cursor = connection.execute(sql, params)
        while True:
            rows = cursor.fetchmany(partition_size)
            if not rows:
                break
            _create_partition(connection, queue, network_id, rows)
            created += 1
            members += len(rows)
    return {"status": "completed", "createdPartitions": created, "createdMembers": members}


def historical_queue_is_fully_prepared(connection: sqlite3.Connection) -> bool:
    queued = connection.execute(
        "SELECT COALESCE(SUM(total_count),0) FROM candidate_scan_partitions WHERE queue_name='historical_backlog'"
    ).fetchone()[0]
    source_count = connection.execute(
        "SELECT COUNT(*) FROM candidates WHERE source_run_id=?",
        (GATE0_RUN_ID,),
    ).fetchone()[0]
    return int(source_count or 0) > 0 and int(queued or 0) == int(source_count or 0)


def _meta(connection: sqlite3.Connection, key: str, fallback: str = "") -> str:
    row = connection.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else fallback


def _save_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """INSERT INTO schema_meta(key,value,updated_at) VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
        (key, value, utc_now()),
    )


def prepare_daily_if_due(
    connection: sqlite3.Connection,
    *,
    partition_size: int,
    force: bool = False,
    interval_seconds: int = 3600,
) -> dict:
    now = datetime.now(timezone.utc)
    last_checked = parse_time(_meta(connection, "candidate_production_last_daily_prepare_at"))
    if not force and last_checked and (now - last_checked).total_seconds() < interval_seconds:
        return {"status": "not_due", "createdPartitions": 0, "createdMembers": 0}
    result = prepare_partitions(connection, queue="daily_incremental", partition_size=partition_size)
    _save_meta(connection, "candidate_production_last_daily_prepare_at", now.isoformat().replace("+00:00", "Z"))
    connection.commit()
    return result


def claim_next_partition(connection: sqlite3.Connection, queue_only: str | None = None) -> sqlite3.Row | None:
    available = {
        row[0] for row in connection.execute(
            "SELECT DISTINCT queue_name FROM candidate_scan_partitions WHERE state IN ('pending','retrying','paused','failed') AND (next_retry_at IS NULL OR datetime(next_retry_at)<=datetime(?))",
            (utc_now(),),
        )
    }
    if queue_only:
        available &= {queue_only}
    if not available:
        return None
    last_queue = _meta(connection, "candidate_production_last_queue")
    queue = "daily_incremental" if "daily_incremental" in available and last_queue != "daily_incremental" else "historical_backlog" if "historical_backlog" in available else next(iter(available))
    if queue == "historical_backlog":
        last_chain = _meta(connection, "candidate_production_last_historical_chain")
        start = (NETWORKS.index(last_chain) + 1) % len(NETWORKS) if last_chain in NETWORKS else 0
        chain_order = NETWORKS[start:] + NETWORKS[:start]
    else:
        chain_order = NETWORKS
    row = None
    for network_id in chain_order:
        row = connection.execute(
            """
            SELECT * FROM candidate_scan_partitions
            WHERE queue_name=? AND network_id=? AND state IN ('pending','retrying','paused','failed')
              AND (next_retry_at IS NULL OR datetime(next_retry_at)<=datetime(?))
            ORDER BY created_at,partition_id LIMIT 1
            """,
            (queue, network_id, utc_now()),
        ).fetchone()
        if row:
            break
    if not row:
        return None
    now = utc_now()
    connection.execute(
        "UPDATE candidate_scan_partitions SET state='running',started_at=COALESCE(started_at,?),last_heartbeat_at=?,updated_at=?,error_detail='' WHERE partition_id=?",
        (now, now, now, row["partition_id"]),
    )
    _save_meta(connection, "candidate_production_last_queue", queue)
    if queue == "historical_backlog":
        _save_meta(connection, "candidate_production_last_historical_chain", row["network_id"])
    connection.commit()
    return connection.execute("SELECT * FROM candidate_scan_partitions WHERE partition_id=?", (row["partition_id"],)).fetchone()


def _risk_state(connection: sqlite3.Connection, candidate_id: int) -> tuple[str, str]:
    row = connection.execute(
        "SELECT source_status,hard_trade_block,reason_codes_json FROM risk_observations WHERE candidate_id=? ORDER BY observed_at DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    if not row or row["source_status"] != "success":
        return "pending", "pending"
    return ("confirmed", "available") if confirmed_trade_block(row) else ("not_confirmed", "available")


def _product_state(connection: sqlite3.Connection, candidate_id: int) -> str:
    return "qualifying" if connection.execute(
        "SELECT 1 FROM product_evidence WHERE candidate_id=? AND status='qualifying' LIMIT 1", (candidate_id,)
    ).fetchone() else "pending"


def _upsert_local(connection: sqlite3.Connection, row: sqlite3.Row, result: dict, checked_at: str) -> None:
    asset_id = deterministic_asset_id(row)
    product_state = _product_state(connection, row["candidate_id"])
    hard_state, risk_state = _risk_state(connection, row["candidate_id"])
    identity_consistent = row["identity_status"] in {"verified", "market_matched"}
    project_id = row["mapped_project_id"] if row["mapped_project_id"] and row["relationship_class"] in {"A", "B", "C"} else None
    t0_status = "verified_in_supported_scope" if row["source_run_id"] == GATE0_RUN_ID or row["t0_status"] == "verified_in_supported_scope" else row["t0_status"]
    connection.execute(
        """
        INSERT INTO candidate_production_records(
          candidate_id,asset_id,project_id,local_state,local_reason_code,local_plain_reason,
          local_evidence_refs_json,local_checked_at,rule_version,t0_status,effective_t0,age_days,
          hard_block_state,identity_state,product_evidence_state,risk_data_state,
          relationship_class,identity_consistent,qualifying_product_evidence,confirmed_hard_block,
          front_contract_ready,front_eligible,front_reason_code,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(candidate_id) DO UPDATE SET
          asset_id=excluded.asset_id,project_id=excluded.project_id,local_state=excluded.local_state,
          local_reason_code=excluded.local_reason_code,local_plain_reason=excluded.local_plain_reason,
          local_evidence_refs_json=excluded.local_evidence_refs_json,local_checked_at=excluded.local_checked_at,
          rule_version=excluded.rule_version,t0_status=excluded.t0_status,effective_t0=excluded.effective_t0,
          age_days=excluded.age_days,hard_block_state=excluded.hard_block_state,
          identity_state=excluded.identity_state,product_evidence_state=excluded.product_evidence_state,
          risk_data_state=excluded.risk_data_state,relationship_class=excluded.relationship_class,
          identity_consistent=excluded.identity_consistent,
          qualifying_product_evidence=excluded.qualifying_product_evidence,
          confirmed_hard_block=excluded.confirmed_hard_block,updated_at=excluded.updated_at
        """,
        (
            row["candidate_id"], asset_id, project_id, result["state"], result["reason"], result["plain"],
            json_text(result["evidence"]), checked_at, RULE_VERSION, t0_status, row["effective_t0"], result["age"],
            hard_state, row["identity_status"], product_state, risk_state, row["relationship_class"],
            int(identity_consistent), int(product_state == "qualifying"), int(hard_state == "confirmed"), 0, 0,
            "awaiting_market_confirmation" if result["state"] == "local_pass" else "local_gate_not_passed", checked_at,
        ),
    )


class DexScreenerProvider:
    def __init__(self, client: JsonClient | None = None):
        self.client = client or JsonClient()
        payload, self.networks = load_source_config()
        self.source = payload["sources"]["dexscreener"]

    def lookup(self, network_id: str, rows: list[sqlite3.Row]) -> dict[int, dict]:
        network = self.networks[network_id]
        addresses = [row["token_address"] for row in rows]
        encoded = urllib.parse.quote(",".join(addresses), safe=",")
        url = f"{self.source['baseUrl']}/tokens/v1/{network['dexScreenerId']}/{encoded}"
        state, response, http, attempts = self.client.request(
            "candidate_production_dexscreener", url,
            minimum_interval=float(self.source["minimumRequestIntervalSeconds"]),
        )
        pairs = response if isinstance(response, list) else []
        by_address: dict[str, list[dict]] = {normalize_for_network(network, value): [] for value in addresses}
        for pair in pairs:
            if str(pair.get("chainId") or "") != network["dexScreenerId"]:
                continue
            for side in ("baseToken", "quoteToken"):
                address = normalize_for_network(network, (pair.get(side) or {}).get("address"))
                if address in by_address:
                    by_address[address].append(pair)
        output = {}
        for row in rows:
            address = normalize_for_network(network, row["token_address"])
            matches = by_address.get(address) or []
            best = max(matches, key=lambda pair: float((pair.get("liquidity") or {}).get("usd") or -1), default=None)
            if not best:
                output[row["candidate_id"]] = {"sourceState": "no_data" if state == "success" else state, "httpStatus": http, "attempts": attempts}
                continue
            base = normalize_for_network(network, (best.get("baseToken") or {}).get("address"))
            quote = normalize_for_network(network, (best.get("quoteToken") or {}).get("address"))
            side = "base" if address == base else "quote" if address == quote else "unmatched"
            token = best.get("baseToken") if side == "base" else best.get("quoteToken") if side == "quote" else {}
            websites = [
                str(item.get("url") or "").strip()
                for item in ((best.get("info") or {}).get("websites") or [])
                if str(item.get("url") or "").strip()
            ]
            earliest = min(
                (created for created in (market_pair_time(pair.get("pairCreatedAt")) for pair in matches) if created),
                default=None,
            )
            txns = (best.get("txns") or {}).get("h24") or {}
            output[row["candidate_id"]] = {
                "sourceState": "success", "pairAddress": best.get("pairAddress") or "", "tokenSide": side,
                "buys": txns.get("buys"), "sells": txns.get("sells"), "observedAt": utc_now(),
                "pairCreatedAt": earliest.isoformat().replace("+00:00", "Z") if earliest else None,
                "tokenName": str((token or {}).get("name") or "").strip(),
                "tokenSymbol": str((token or {}).get("symbol") or "").strip(),
                "website": websites[0] if websites else "",
                "liquidityUsd": (best.get("liquidity") or {}).get("usd"),
                "volumeUsd": (best.get("volume") or {}).get("h24"), "priceUsd": best.get("priceUsd"),
                "fdvUsd": best.get("fdv"), "marketCapUsd": best.get("marketCap"),
                "dexId": best.get("dexId"), "attempts": attempts,
            }
        return output


def enqueue_first_gate_candidate(
    connection: sqlite3.Connection,
    candidate_id: int,
    *,
    source_queue: str,
    qualification_batch_id: str | None = None,
    enqueued_at: str | None = None,
    rule_hash: str | None = None,
) -> None:
    """Materialize the screening handoff instead of treating missing cursors as a queue."""

    if source_queue not in {*QUEUES, "unassigned"}:
        source_queue = "unassigned"
    now = enqueued_at or utc_now()
    from c2_1_rules import load_rules

    if rule_hash is None:
        _, rule_hash = load_rules()
    completed = connection.execute(
        """SELECT 1
        FROM evaluations e
        JOIN candidates c ON c.candidate_id=e.candidate_id
        JOIN candidate_production_records p ON p.candidate_id=e.candidate_id
        JOIN candidate_qualification_batches b
          ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
        WHERE e.candidate_id=? AND e.is_current=1 AND e.rule_config_hash=?
          AND datetime(e.evaluated_at)>=datetime(COALESCE(p.qualified_at,p.updated_at))
          AND datetime(e.evaluated_at)>=datetime(p.updated_at)
          AND datetime(e.evaluated_at)>=datetime(c.updated_at)""",
        (int(candidate_id), rule_hash),
    ).fetchone()
    state = "completed" if completed else "pending"
    connection.execute(
        """
        INSERT INTO candidate_first_gate_queue(
          candidate_id,qualification_batch_id,source_queue,state,enqueued_at,
          completed_at,updated_at
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(candidate_id) DO UPDATE SET
          qualification_batch_id=COALESCE(excluded.qualification_batch_id,candidate_first_gate_queue.qualification_batch_id),
          source_queue=CASE
            WHEN candidate_first_gate_queue.source_queue='historical_backlog' THEN candidate_first_gate_queue.source_queue
            ELSE excluded.source_queue
          END,
          state=CASE
            WHEN excluded.state='completed' THEN 'completed'
            WHEN candidate_first_gate_queue.state='running' THEN candidate_first_gate_queue.state
            ELSE 'pending'
          END,
          completed_at=CASE
            WHEN excluded.state='completed' THEN COALESCE(candidate_first_gate_queue.completed_at,excluded.completed_at)
            WHEN candidate_first_gate_queue.state='running' THEN candidate_first_gate_queue.completed_at
            ELSE NULL
          END,
          next_retry_at=NULL,error_code='',error_detail='',updated_at=excluded.updated_at
        """,
        (
            int(candidate_id), qualification_batch_id, source_queue, state, now,
            now if state == "completed" else None, now,
        ),
    )


def backfill_first_gate_handoff(
    connection: sqlite3.Connection,
    candidate_ids: list[int] | None = None,
) -> dict:
    """Synchronize every confirmed 90-day market candidate into the formal T0/first-gate queue."""

    from c2_1_rules import load_rules

    now = utc_now()
    _, rule_hash = load_rules()
    selected_ids = sorted({int(value) for value in (candidate_ids or [])})
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        connection.execute(
            f"""UPDATE candidates SET
              t0_status='verified_in_supported_scope',
              effective_t0=(SELECT p.effective_t0 FROM candidate_production_records p
                            WHERE p.candidate_id=candidates.candidate_id),
              updated_at=?
            WHERE candidate_id IN ({placeholders})
              AND EXISTS(
                SELECT 1 FROM candidate_production_records p
                WHERE p.candidate_id=candidates.candidate_id
                  AND p.market_state='market_confirmed'
                  AND p.t0_status='verified_in_supported_scope'
              ) AND (
                t0_status!='verified_in_supported_scope'
                OR effective_t0!=(SELECT p.effective_t0 FROM candidate_production_records p
                                  WHERE p.candidate_id=candidates.candidate_id)
              )""",
            (now, *selected_ids),
        )
        rows = connection.execute(
            f"""SELECT p.candidate_id,p.qualification_batch_id,q.source_queue
                FROM candidate_production_records p
                LEFT JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id
                WHERE p.market_state='market_confirmed'
                  AND p.tracking_eligible=1
                  AND p.age_days BETWEEN 0 AND 90
                  AND p.candidate_id IN ({placeholders})
                ORDER BY p.candidate_id""",
            tuple(selected_ids),
        ).fetchall()
        for row in rows:
            source_queue = row["source_queue"] or "unassigned"
            enqueue_first_gate_candidate(
                connection,
                int(row["candidate_id"]),
                source_queue=source_queue,
                qualification_batch_id=row["qualification_batch_id"],
                enqueued_at=now,
                rule_hash=rule_hash,
            )
        connection.commit()
        counts = dict(connection.execute(
            "SELECT state,COUNT(*) FROM candidate_first_gate_queue GROUP BY state"
        ).fetchall())
        return {
            "queued": sum(int(value) for value in counts.values()),
            "completed": int(counts.get("completed", 0)),
            "pending": sum(int(counts.get(state, 0)) for state in ("pending", "retrying", "running")),
            "failed": int(counts.get("failed", 0)),
        }
    connection.execute(
        f"""
        UPDATE candidates SET
          t0_status='verified_in_supported_scope',
          effective_t0=(SELECT p.effective_t0 FROM candidate_production_records p WHERE p.candidate_id=candidates.candidate_id),
          updated_at=?
        WHERE candidate_id IN (
          SELECT p.candidate_id FROM candidate_production_records p
          WHERE p.market_state='market_confirmed'
            AND p.t0_status='verified_in_supported_scope'
        ) AND (
          t0_status!='verified_in_supported_scope'
          OR effective_t0!=(SELECT p.effective_t0 FROM candidate_production_records p WHERE p.candidate_id=candidates.candidate_id)
        )
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO candidate_first_gate_queue(
          candidate_id,qualification_batch_id,source_queue,state,attempt_count,
          enqueued_at,completed_at,updated_at
        )
        SELECT p.candidate_id,p.qualification_batch_id,
          CASE
            WHEN EXISTS(
              SELECT 1 FROM candidate_scan_partition_members m
              JOIN candidate_scan_partitions sp ON sp.partition_id=m.partition_id
              WHERE m.candidate_id=p.candidate_id AND sp.queue_name='historical_backlog'
            ) THEN 'historical_backlog'
            WHEN EXISTS(
              SELECT 1 FROM candidate_scan_partition_members m
              JOIN candidate_scan_partitions sp ON sp.partition_id=m.partition_id
              WHERE m.candidate_id=p.candidate_id AND sp.queue_name='daily_incremental'
            ) THEN 'daily_incremental'
            ELSE 'unassigned'
          END,
          CASE WHEN EXISTS(
            SELECT 1 FROM source_cursors sc
            WHERE sc.source_id='candidate_first_gate' AND sc.scope_key=CAST(p.candidate_id AS TEXT)
              AND sc.stage='hard_gate' AND sc.status='success'
          ) THEN 'completed' ELSE 'pending' END,
          0,COALESCE(p.market_observed_at,p.updated_at,?),
          CASE WHEN EXISTS(
            SELECT 1 FROM source_cursors sc
            WHERE sc.source_id='candidate_first_gate' AND sc.scope_key=CAST(p.candidate_id AS TEXT)
              AND sc.stage='hard_gate' AND sc.status='success'
          ) THEN COALESCE(p.updated_at,?) ELSE NULL END,?
        FROM candidate_production_records p
        WHERE p.market_state='market_confirmed'
          AND p.tracking_eligible=1
          AND p.age_days BETWEEN 0 AND 90
        ON CONFLICT(candidate_id) DO UPDATE SET
          qualification_batch_id=COALESCE(excluded.qualification_batch_id,candidate_first_gate_queue.qualification_batch_id),
          source_queue=CASE
            WHEN candidate_first_gate_queue.source_queue='historical_backlog' THEN candidate_first_gate_queue.source_queue
            ELSE excluded.source_queue
          END,
          state=CASE
            WHEN candidate_first_gate_queue.state='completed' OR excluded.state='completed' THEN 'completed'
            WHEN candidate_first_gate_queue.state='running' THEN 'retrying'
            ELSE candidate_first_gate_queue.state
          END,
          completed_at=COALESCE(candidate_first_gate_queue.completed_at,excluded.completed_at),
          updated_at=excluded.updated_at
        """,
        (now, now, now),
    )
    connection.execute(
        """UPDATE candidate_first_gate_queue AS q SET
          state=CASE WHEN q.state='running' THEN 'retrying' ELSE 'pending' END,
          completed_at=NULL,next_retry_at=NULL,error_code='',error_detail='',updated_at=?
        WHERE EXISTS(
          SELECT 1 FROM candidate_production_records p
          WHERE p.candidate_id=q.candidate_id AND p.market_state='market_confirmed'
            AND p.tracking_eligible=1 AND p.age_days BETWEEN 0 AND 90
        ) AND NOT EXISTS(
          SELECT 1
          FROM evaluations e
          JOIN candidates c ON c.candidate_id=e.candidate_id
          JOIN candidate_production_records p ON p.candidate_id=e.candidate_id
          JOIN candidate_qualification_batches b
            ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
          WHERE e.candidate_id=q.candidate_id AND e.is_current=1 AND e.rule_config_hash=?
            AND datetime(e.evaluated_at)>=datetime(COALESCE(p.qualified_at,p.updated_at))
            AND datetime(e.evaluated_at)>=datetime(p.updated_at)
            AND datetime(e.evaluated_at)>=datetime(c.updated_at)
        )""",
        (now, rule_hash),
    )
    connection.execute(
        """UPDATE candidate_first_gate_queue AS q SET
          state='completed',
          completed_at=(SELECT e.evaluated_at FROM evaluations e
                        WHERE e.candidate_id=q.candidate_id AND e.is_current=1
                        ORDER BY datetime(e.evaluated_at) DESC LIMIT 1),
          next_retry_at=NULL,error_code='',error_detail='',updated_at=?
        WHERE EXISTS(
          SELECT 1
          FROM evaluations e
          JOIN candidates c ON c.candidate_id=e.candidate_id
          JOIN candidate_production_records p ON p.candidate_id=e.candidate_id
          JOIN candidate_qualification_batches b
            ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
          WHERE e.candidate_id=q.candidate_id AND e.is_current=1 AND e.rule_config_hash=?
            AND datetime(e.evaluated_at)>=datetime(COALESCE(p.qualified_at,p.updated_at))
            AND datetime(e.evaluated_at)>=datetime(p.updated_at)
            AND datetime(e.evaluated_at)>=datetime(c.updated_at)
        )""",
        (now, rule_hash),
    )
    connection.commit()
    counts = dict(connection.execute(
        "SELECT state,COUNT(*) FROM candidate_first_gate_queue GROUP BY state"
    ).fetchall())
    return {
        "queued": sum(int(value) for value in counts.values()),
        "completed": int(counts.get("completed", 0)),
        "pending": sum(int(counts.get(state, 0)) for state in ("pending", "retrying", "running")),
        "failed": int(counts.get("failed", 0)),
    }


def reconcile_first_gate_queue_from_evaluations(
    connection: sqlite3.Connection,
    candidate_ids: list[int] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> dict:
    """Restore queue states from current local evaluations without rescanning candidates."""

    from c2_1_rules import load_rules

    selected_ids = sorted({int(value) for value in (candidate_ids or [])})
    if not selected_ids:
        selected_ids = [
            int(row[0])
            for row in connection.execute(
                "SELECT candidate_id FROM candidate_first_gate_queue ORDER BY candidate_id"
            )
        ]
    selected_set = set(selected_ids)
    current_evaluations = {}
    for row in connection.execute(
        "SELECT candidate_id,evaluated_at,rule_config_hash FROM evaluations WHERE is_current=1"
    ):
        candidate_id = int(row["candidate_id"])
        if candidate_id not in selected_set:
            continue
        current = current_evaluations.get(candidate_id)
        if current is None or (parse_time(row["evaluated_at"]) or datetime.min.replace(tzinfo=timezone.utc)) > (
            parse_time(current["evaluated_at"]) or datetime.min.replace(tzinfo=timezone.utc)
        ):
            current_evaluations[candidate_id] = row
    _, rule_hash = load_rules()
    now = utc_now()
    seen = set()
    waiting_atomic = 0
    updated_count = 0
    for start in range(0, len(selected_ids), 500):
        batch = selected_ids[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"""SELECT q.candidate_id,q.state,q.completed_at,
              c.updated_at AS candidate_updated_at,
              p.updated_at AS production_updated_at,p.qualified_at,
              b.state AS qualification_state
            FROM candidate_first_gate_queue q
            JOIN candidates c ON c.candidate_id=q.candidate_id
            JOIN candidate_production_records p ON p.candidate_id=q.candidate_id
            LEFT JOIN candidate_qualification_batches b
              ON b.qualification_batch_id=p.qualification_batch_id
            WHERE q.candidate_id IN ({placeholders})""",
            tuple(batch),
        ).fetchall()
        batch_updates = []
        for row in rows:
            candidate_id = int(row["candidate_id"])
            seen.add(candidate_id)
            evaluation = current_evaluations.get(candidate_id)
            qualification_complete = row["qualification_state"] == "completed"
            evaluated_at = evaluation["evaluated_at"] if evaluation else None
            evaluation_rule_hash = evaluation["rule_config_hash"] if evaluation else None
            evaluated = parse_time(evaluated_at)
            qualified = parse_time(row["qualified_at"])
            production_updated = parse_time(row["production_updated_at"])
            candidate_updated = parse_time(row["candidate_updated_at"])
            fresh = bool(
                qualification_complete
                and evaluated
                and qualified
                and production_updated
                and candidate_updated
                and evaluated >= qualified
                and evaluated >= production_updated
                and evaluated >= candidate_updated
                and str(evaluation_rule_hash or "") == rule_hash
            )
            if not qualification_complete:
                waiting_atomic += 1
            desired_state = "completed" if fresh else "retrying" if row["state"] == "running" else "pending"
            desired_completed_at = evaluated_at if fresh else None
            if row["state"] == desired_state and row["completed_at"] == desired_completed_at:
                continue
            batch_updates.append((
                desired_state, desired_completed_at, now, candidate_id,
            ))
        connection.executemany(
            """UPDATE candidate_first_gate_queue SET
              state=?,completed_at=?,next_retry_at=NULL,error_code='',error_detail='',updated_at=?
            WHERE candidate_id=?""",
            batch_updates,
        )
        connection.commit()
        updated_count += len(batch_updates)
        if progress:
            progress(min(start + len(batch), len(selected_ids)), len(selected_ids), updated_count)
    counts = dict(connection.execute(
        "SELECT state,COUNT(*) FROM candidate_first_gate_queue GROUP BY state"
    ).fetchall())
    return {
        "selected": len(seen),
        "updated": updated_count,
        "queued": sum(int(value) for value in counts.values()),
        "completed": int(counts.get("completed", 0)),
        "pending": sum(int(counts.get(state, 0)) for state in ("pending", "retrying", "running")),
        "failed": int(counts.get("failed", 0)),
        "waitingAtomicQualification": waiting_atomic,
    }


def _save_market_result(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    result: dict,
    observed_at: str,
    *,
    source_queue: str,
) -> None:
    state = result.get("sourceState") or "program_failure"
    if state not in SOURCE_STATES:
        state = "program_failure"
    pair = str(result.get("pairAddress") or "")
    side = str(result.get("tokenSide") or "")
    buys = result.get("buys")
    sells = result.get("sells")
    if state == "success" and pair and side not in {"base", "quote"}:
        market_state, reason, plain = "market_identity_conflict", "market_token_side_conflict", "市场返回的链、地址或资产方向存在冲突。"
    elif state == "success" and pair and (buys is None or sells is None):
        state = "no_data"
        market_state, reason, plain = "source_pending", "market_trade_counts_missing", "公开池已索引，但本轮没有返回可确认的买入或卖出笔数。"
    elif state == "success" and pair and float(buys or 0) >= 1 and float(sells or 0) >= 1:
        market_state, reason, plain = "market_confirmed", "public_pool_with_buy_and_sell", "公开池、资产方向、买入和卖出均已确认。"
    elif state == "success" and pair:
        market_state, reason, plain = "waiting_for_trades", "buy_or_sell_not_formed", "公开市场存在，但买入或卖出尚未同时形成。"
    elif state in {"success", "no_data"}:
        market_state, reason, plain = "market_not_indexed", "provider_market_not_indexed", "当前公开市场来源尚未索引到可确认交易池。"
    else:
        market_state, reason, plain = "source_pending", state, "本轮来源未完成，已保留对象和下一次复查时间。"
    pair_created = market_pair_time(result.get("pairCreatedAt"))
    current_t0 = parse_time(row["effective_t0"])
    effective_t0 = min(current_t0, pair_created) if current_t0 and pair_created else current_t0 or pair_created
    effective_t0_text = effective_t0.isoformat().replace("+00:00", "Z") if effective_t0 else row["effective_t0"]
    age = age_days(effective_t0_text, observed_at)
    website = str(result.get("website") or "").strip()
    identity_state = "market_matched" if row["identity_status"] == "not_verified" and website else row["identity_status"]
    local_state = "outside_90_days" if age is not None and age > 90 else row["local_stage"]
    local_reason = "earlier_indexed_public_pool_found" if pair_created and current_t0 and pair_created < current_t0 else row["local_reason"]
    connection.execute(
        """
        UPDATE candidates SET
          canonical_name=CASE WHEN COALESCE(canonical_name,'')='' THEN ? ELSE canonical_name END,
          symbol=CASE WHEN COALESCE(symbol,'')='' THEN ? ELSE symbol END,
          website_domain=CASE WHEN COALESCE(website_domain,'')='' THEN ? ELSE website_domain END,
          identity_status=?,effective_t0=?,local_stage=?,local_reason=?,updated_at=?
        WHERE candidate_id=?
        """,
        (
            str(result.get("tokenName") or "").strip(),
            str(result.get("tokenSymbol") or "").strip(),
            website,
            identity_state,
            effective_t0_text,
            local_state,
            local_reason,
            observed_at,
            row["candidate_id"],
        ),
    )
    hard_state, risk_state = _risk_state(connection, row["candidate_id"])
    product_state = _product_state(connection, row["candidate_id"])
    t0_status = "verified_in_supported_scope" if row["source_run_id"] == GATE0_RUN_ID else row["t0_status"]
    tracking = market_state == "market_confirmed" and hard_state != "confirmed" and t0_status == "verified_in_supported_scope" and age is not None and 0 <= age <= 90
    identity_ok = identity_state in {"verified", "market_matched"} and side in {"base", "quote"}
    front_contract = tracking and row["relationship_class"] in {"A", "B", "C"} and identity_ok and product_state == "qualifying"
    next_retry = retry_at_for_age(age, observed_at) if market_state in {"waiting_for_trades", "market_not_indexed", "source_pending"} else None
    connection.execute(
        """
        UPDATE candidate_production_records SET market_state=?,market_reason_code=?,market_plain_reason=?,
          market_source='DexScreener',market_source_state=?,market_attempt_count=?,market_observed_at=?,pair_address=?,token_side=?,
          observed_buys=?,observed_sells=?,next_retry_at=?,tracking_eligible=?,tracking_reason_code=?,
          t0_status=?,effective_t0=?,age_days=?,local_state=CASE WHEN ?='outside_90_days' THEN ? ELSE local_state END,
          local_reason_code=CASE WHEN ?='outside_90_days' THEN 'age_day_91_or_later' ELSE local_reason_code END,
          local_plain_reason=CASE WHEN ?='outside_90_days' THEN '当前已到第91天或更晚，退出90天候选池。' ELSE local_plain_reason END,
          hard_block_state=?,identity_state=?,product_evidence_state=?,risk_data_state=?,identity_consistent=?,
          qualifying_product_evidence=?,confirmed_hard_block=?,front_contract_ready=?,
          front_eligible=0,front_reason_code=?,updated_at=?
        WHERE candidate_id=?
        """,
        (
            market_state, reason, plain, state, len(result.get("attempts") or []), observed_at, pair, side, buys, sells, next_retry, int(tracking),
            "market_and_trade_direction_confirmed" if tracking else "confirmed_hard_trade_block" if hard_state == "confirmed" else reason,
            t0_status, effective_t0_text, age, local_state, local_state, local_state, local_state,
            hard_state, identity_state, product_state, risk_state, int(identity_ok), int(product_state == "qualifying"),
            int(hard_state == "confirmed"), int(front_contract),
            "awaiting_hard_gate_evaluation" if front_contract else "backend_tracking_only", observed_at, row["candidate_id"],
        ),
    )
    if market_state == "market_confirmed":
        connection.execute(
            """UPDATE candidates SET t0_status=?,effective_t0=?,
              continuity_status=CASE WHEN continuity_status='unknown' THEN 'candidate_asset' ELSE continuity_status END,
              continuity_reason=CASE WHEN continuity_status='unknown' THEN 'public_market_confirmed_candidate_asset' ELSE continuity_reason END,
              updated_at=?
            WHERE candidate_id=?""",
            (t0_status, effective_t0_text, observed_at, row["candidate_id"]),
        )
        if tracking:
            enqueue_first_gate_candidate(
                connection,
                int(row["candidate_id"]),
                source_queue=source_queue,
                enqueued_at=observed_at,
            )
    if state == "success" and pair:
        observation_id = "candidate-production-market-" + hashlib.sha256(f"{row['candidate_id']}|{observed_at[:13]}".encode()).hexdigest()[:22]
        volume = result.get("volumeUsd")
        liquidity = result.get("liquidityUsd")
        ratio = float(volume) / float(liquidity) if volume is not None and liquidity not in {None, 0} else None
        connection.execute(
            """
            INSERT INTO market_observations(
              observation_id,candidate_id,window_id,source_name,source_status,observed_at,pair_address,
              pair_created_at,token_side,liquidity_usd,fdv_usd,market_cap_usd,volume_usd,transaction_count,
              observed_buys,observed_sells,volume_liquidity_ratio,price_usd,standard_sell_notional_usd,
              standard_sell_quote_state,payload_json
            ) VALUES(?,?,?,'DexScreener','success',?,?,?,?,?,?,?,?,?,?,?,?,?,100,'no_data',?)
            ON CONFLICT(candidate_id,window_id,source_name) DO UPDATE SET
              observed_at=excluded.observed_at,pair_address=excluded.pair_address,pair_created_at=excluded.pair_created_at,
              token_side=excluded.token_side,
              liquidity_usd=excluded.liquidity_usd,volume_usd=excluded.volume_usd,transaction_count=excluded.transaction_count,
              observed_buys=excluded.observed_buys,observed_sells=excluded.observed_sells,
              volume_liquidity_ratio=excluded.volume_liquidity_ratio,price_usd=excluded.price_usd,payload_json=excluded.payload_json
            """,
            (
                observation_id, row["candidate_id"], "candidate-production:" + observed_at[:13], observed_at, pair,
                pair_created.isoformat().replace("+00:00", "Z") if pair_created else None, side,
                liquidity, result.get("fdvUsd"), result.get("marketCapUsd"), volume,
                float(buys or 0) + float(sells or 0), buys, sells, ratio, result.get("priceUsd"),
                json_text({"dexId": result.get("dexId"), "attempts": result.get("attempts") or [], "boundary": "provider_indexed_pairs_not_global_market"}),
            ),
        )


def refresh_production_contracts(connection: sqlite3.Connection, candidate_ids: list[int]) -> list[int]:
    """Recalculate the screening contract from the latest first-gate evidence."""

    selected_ids = sorted({int(value) for value in candidate_ids})
    if not selected_ids:
        return []
    placeholders = ",".join("?" for _ in selected_ids)
    rows = connection.execute(
        f"""SELECT c.*,p.market_state,p.pair_address,p.token_side,p.observed_buys,p.observed_sells,
          p.asset_id AS production_asset_id,p.project_id AS production_project_id,
          p.tracking_eligible AS production_tracking_eligible,
          p.tracking_reason_code AS production_tracking_reason_code,
          p.t0_status AS production_t0_status,p.effective_t0 AS production_effective_t0,
          p.age_days AS production_age_days,p.hard_block_state AS production_hard_block_state,
          p.identity_state AS production_identity_state,
          p.product_evidence_state AS production_product_evidence_state,
          p.risk_data_state AS production_risk_data_state,
          p.relationship_class AS production_relationship_class,
          p.identity_consistent AS production_identity_consistent,
          p.qualifying_product_evidence AS production_qualifying_product_evidence,
          p.confirmed_hard_block AS production_confirmed_hard_block,
          p.front_contract_ready AS production_front_contract_ready,
          p.front_reason_code AS production_front_reason_code
        FROM candidates c JOIN candidate_production_records p ON p.candidate_id=c.candidate_id
        WHERE c.candidate_id IN ({placeholders})""",
        tuple(selected_ids),
    ).fetchall()
    qualifying_product_ids = {
        int(row[0])
        for row in connection.execute(
            f"""SELECT DISTINCT candidate_id FROM product_evidence
            WHERE status='qualifying' AND candidate_id IN ({placeholders})""",
            tuple(selected_ids),
        ).fetchall()
    }
    latest_risk = {}
    for risk_row in connection.execute(
        f"""SELECT candidate_id,source_status,hard_trade_block,reason_codes_json FROM risk_observations
        WHERE candidate_id IN ({placeholders}) ORDER BY candidate_id,datetime(observed_at) DESC""",
        tuple(selected_ids),
    ).fetchall():
        latest_risk.setdefault(int(risk_row["candidate_id"]), risk_row)
    updated_at = utc_now()
    refreshed = []
    for row in rows:
        candidate_id = int(row["candidate_id"])
        risk_row = latest_risk.get(candidate_id)
        if not risk_row or risk_row["source_status"] != "success":
            hard_state, risk_state = "pending", "pending"
        else:
            hard_state, risk_state = (("confirmed", "available") if confirmed_trade_block(risk_row) else ("not_confirmed", "available"))
        product_state = "qualifying" if candidate_id in qualifying_product_ids else "pending"
        relationship = str(row["relationship_class"] or "D")
        identity_ok = (
            row["identity_status"] in {"verified", "market_matched"}
            and row["token_side"] in {"base", "quote"}
            and bool(row["pair_address"])
        )
        t0_status = "verified_in_supported_scope" if row["source_run_id"] == GATE0_RUN_ID else row["t0_status"]
        age = age_days(row["effective_t0"], updated_at)
        tracking = (
            row["market_state"] == "market_confirmed"
            and hard_state != "confirmed"
            and t0_status == "verified_in_supported_scope"
            and age is not None
            and 0 <= age <= 90
        )
        front_contract = (
            tracking
            and relationship in {"A", "B", "C"}
            and identity_ok
            and product_state == "qualifying"
        )
        project_id = row["mapped_project_id"] if row["mapped_project_id"] and relationship in {"A", "B", "C"} else None
        tracking_reason = "market_and_trade_direction_confirmed" if tracking else "first_gate_not_passed"
        front_reason = "awaiting_hard_gate_evaluation" if front_contract else "first_gate_contract_incomplete"
        current_contract = (
            row["production_asset_id"], row["production_project_id"], int(row["production_tracking_eligible"]),
            row["production_tracking_reason_code"], row["production_t0_status"], row["production_effective_t0"],
            row["production_age_days"], row["production_hard_block_state"], row["production_identity_state"],
            row["production_product_evidence_state"], row["production_risk_data_state"],
            row["production_relationship_class"], int(row["production_identity_consistent"]),
            int(row["production_qualifying_product_evidence"]), int(row["production_confirmed_hard_block"]),
            int(row["production_front_contract_ready"]), row["production_front_reason_code"],
        )
        next_contract = (
            deterministic_asset_id(row), project_id, int(tracking), tracking_reason, t0_status,
            row["effective_t0"], age, hard_state, row["identity_status"], product_state, risk_state,
            relationship, int(identity_ok), int(product_state == "qualifying"), int(hard_state == "confirmed"),
            int(front_contract), front_reason,
        )
        if current_contract == next_contract:
            continue
        connection.execute(
            """
            UPDATE candidate_production_records SET asset_id=?,project_id=?,tracking_eligible=?,
              tracking_reason_code=?,t0_status=?,effective_t0=?,age_days=?,hard_block_state=?,
              identity_state=?,product_evidence_state=?,risk_data_state=?,relationship_class=?,
              identity_consistent=?,qualifying_product_evidence=?,confirmed_hard_block=?,
              front_contract_ready=?,front_eligible=CASE WHEN ?=1 THEN front_eligible ELSE 0 END,
              front_reason_code=?,updated_at=? WHERE candidate_id=?
            """,
            (
                deterministic_asset_id(row), project_id, int(tracking), tracking_reason,
                t0_status, row["effective_t0"], age, hard_state, row["identity_status"], product_state,
                risk_state, relationship, int(identity_ok), int(product_state == "qualifying"),
                int(hard_state == "confirmed"), int(front_contract), int(front_contract),
                front_reason,
                updated_at, candidate_id,
            ),
        )
        refreshed.append(candidate_id)
    connection.commit()
    return refreshed


def promote_market_confirmed_candidate_assets(
    connection: sqlite3.Connection,
    candidate_ids: list[int],
) -> list[int]:
    """Close the internal asset-candidate state after a public market is confirmed."""

    selected_ids = sorted({int(value) for value in candidate_ids})
    if not selected_ids:
        return []
    promoted = []
    now = utc_now()
    for start in range(0, len(selected_ids), 500):
        batch = selected_ids[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"""SELECT c.candidate_id
              FROM candidates c JOIN candidate_production_records p ON p.candidate_id=c.candidate_id
              WHERE c.candidate_id IN ({placeholders})
                AND c.continuity_status='unknown'
                AND p.market_state='market_confirmed' AND p.local_state='local_pass'""",
            tuple(batch),
        ).fetchall()
        batch_ids = [int(row[0]) for row in rows]
        if not batch_ids:
            continue
        update_placeholders = ",".join("?" for _ in batch_ids)
        connection.execute(
            f"""UPDATE candidates SET continuity_status='candidate_asset',
              continuity_reason='public_market_confirmed_candidate_asset',updated_at=?
              WHERE candidate_id IN ({update_placeholders}) AND continuity_status='unknown'""",
            (now, *batch_ids),
        )
        connection.commit()
        promoted.extend(batch_ids)
    return promoted


def process_first_gate_candidates(
    connection: sqlite3.Connection,
    *,
    candidate_ids: list[int],
    refresh_market: bool,
    client=None,
) -> dict:
    """Complete only the four C2.4 initial-screen checks before deep tracking."""

    selected_ids = sorted({int(value) for value in candidate_ids})
    if not selected_ids:
        return {"selected": 0, "evaluated": 0, "frontEligible": 0}
    placeholders = ",".join("?" for _ in selected_ids)
    eligible_ids = [
        int(row[0])
        for row in connection.execute(
            f"""SELECT p.candidate_id FROM candidate_production_records p
            JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id
            JOIN candidate_qualification_batches b
              ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
            WHERE p.candidate_id IN ({placeholders}) AND p.market_state='market_confirmed'
            ORDER BY p.candidate_id""",
            tuple(selected_ids),
        ).fetchall()
    ]
    if not eligible_ids:
        return {"selected": 0, "evaluated": 0, "frontEligible": 0}

    started_at = utc_now()
    placeholders = ",".join("?" for _ in eligible_ids)
    connection.execute(
        f"""UPDATE candidate_first_gate_queue SET state='running',attempt_count=attempt_count+1,
        started_at=COALESCE(started_at,?),next_retry_at=NULL,error_code='',error_detail='',updated_at=?
        WHERE candidate_id IN ({placeholders}) AND state!='completed'""",
        (started_at, started_at, *eligible_ids),
    )
    connection.commit()

    stages = {}
    if refresh_market:
        stages["market"] = collect_market(connection, client=client, candidate_ids=eligible_ids)
    stages["candidateAssetPromotion"] = {
        "promoted": len(promote_market_confirmed_candidate_assets(connection, eligible_ids))
    }
    refresh_production_contracts(connection, eligible_ids)
    passed_ids = [
        int(row[0])
        for row in connection.execute(
            f"""SELECT candidate_id FROM candidate_production_records
            WHERE candidate_id IN ({placeholders}) AND tracking_eligible=1
              AND age_days BETWEEN 0 AND 90 ORDER BY candidate_id""",
            tuple(eligible_ids),
        ).fetchall()
    ]
    completed_at = utc_now()
    for candidate_id in eligible_ids:
        commit_cursor(
            connection,
            "candidate_first_gate",
            str(candidate_id),
            "hard_gate",
            RULE_VERSION,
            "success",
            {
                "candidateId": candidate_id,
                "evaluatedAt": completed_at,
                "firstGatePassed": candidate_id in passed_ids,
                "ruleVersion": "c2.4-first-gate-v1",
            },
        )
    connection.execute(
        f"""UPDATE candidate_first_gate_queue SET state='completed',completed_at=?,
        next_retry_at=NULL,error_code='',error_detail='',updated_at=?
        WHERE candidate_id IN ({placeholders})""",
        (completed_at, completed_at, *eligible_ids),
    )
    connection.commit()
    from c2_4_tracking import initialize_schema as initialize_c2_4_schema, record_first_gate_history

    initialize_c2_4_schema(connection)
    history_saved = record_first_gate_history(connection, passed_ids)
    return {
        "selected": len(eligible_ids),
        "evaluated": len(eligible_ids),
        "firstGatePassed": len(passed_ids),
        "historySaved": history_saved,
        "frontEligible": 0,
        "stages": stages,
    }


def pending_first_gate_candidate_ids(connection: sqlite3.Connection, limit: int = 50) -> list[int]:
    """Return a bounded, materialized T0/first-gate backlog."""

    rows = connection.execute(
        """
        SELECT q.candidate_id
        FROM candidate_first_gate_queue q
        JOIN candidate_production_records p ON p.candidate_id=q.candidate_id
        JOIN candidate_qualification_batches b
          ON b.qualification_batch_id=p.qualification_batch_id AND b.state='completed'
        WHERE p.market_state='market_confirmed'
          AND q.state IN ('pending','retrying','failed')
          AND (q.next_retry_at IS NULL OR datetime(q.next_retry_at)<=datetime('now'))
        ORDER BY q.enqueued_at,q.candidate_id
        LIMIT ?
        """,
        (max(0, int(limit)),),
    ).fetchall()
    return [int(row[0]) for row in rows]


def retry_first_gate_candidates(
    connection: sqlite3.Connection,
    candidate_ids: list[int],
    error: Exception,
) -> None:
    selected_ids = sorted({int(value) for value in candidate_ids})
    if not selected_ids:
        return
    failed_at = utc_now()
    retry_at = (parse_time(failed_at) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    placeholders = ",".join("?" for _ in selected_ids)
    detail = f"{type(error).__name__}: {error}"
    connection.execute(
        f"""UPDATE candidate_first_gate_queue SET state='retrying',next_retry_at=?,
        error_code='program_failure',error_detail=?,updated_at=?
        WHERE candidate_id IN ({placeholders}) AND state!='completed'""",
        (retry_at, detail, failed_at, *selected_ids),
    )
    connection.commit()


def publish_first_gate_snapshots(connection: sqlite3.Connection) -> dict:
    """Publish the screening result after each completed first-gate batch."""

    from c2_1_pipeline import build_snapshots as build_c21_snapshots
    from build_c2_2_snapshots import build_snapshots as build_c22_snapshots

    c21 = build_c21_snapshots(connection)
    c22 = build_c22_snapshots(
        c21_front_path=PROJECT_ROOT / "app" / "c2-1-front-snapshot.js",
        c21_admin_path=PROJECT_ROOT / "app" / "c2-1-admin-snapshot.js",
        write=True,
    )
    return {
        "c21FrontVisibleCount": int(c21["frontVisibleCount"]),
        "c22FrontVisibleCount": len(c22["front"]["items"]),
        "c22TrackingInputCount": int(c22["tracking"]["inputSummary"]["candidateCount"]),
    }


def changed_first_gate_contract_candidate_ids(connection: sqlite3.Connection, limit: int = 500) -> list[int]:
    """Find evidence changes whose production contract has not been refreshed."""

    from c2_1_rules import load_rules

    _, rule_hash = load_rules()
    rows = connection.execute(
        """
        SELECT p.candidate_id
        FROM candidate_production_records p JOIN candidates c ON c.candidate_id=p.candidate_id
        WHERE p.qualification_batch_id IS NOT NULL AND (
          p.identity_state<>c.identity_status
          OR p.relationship_class<>c.relationship_class
          OR p.effective_t0<>c.effective_t0
          OR p.product_evidence_state<>CASE WHEN EXISTS(
            SELECT 1 FROM product_evidence pe WHERE pe.candidate_id=p.candidate_id AND pe.status='qualifying'
          ) THEN 'qualifying' ELSE 'pending' END
          OR p.hard_block_state<>COALESCE((
            SELECT CASE WHEN ro.source_status='success' AND ro.hard_trade_block=1
                         AND NOT (
                           json_valid(ro.reason_codes_json)
                           AND json_array_length(ro.reason_codes_json)>0
                           AND NOT EXISTS(
                             SELECT 1 FROM json_each(ro.reason_codes_json) reason
                             WHERE reason.value NOT IN (
                               'confirmed_sell_tax_ge_20pct',
                               'buy_or_sell_tax_ge_20',
                               'liquidity_drop_ge_80'
                             )
                           )
                         ) THEN 'confirmed'
                        WHEN ro.source_status='success' THEN 'not_confirmed' ELSE 'pending' END
            FROM risk_observations ro WHERE ro.candidate_id=p.candidate_id
            ORDER BY ro.observed_at DESC LIMIT 1
          ),'pending')
          OR NOT EXISTS(
            SELECT 1 FROM evaluations e
            WHERE e.candidate_id=p.candidate_id AND e.is_current=1
              AND e.rule_config_hash=?
              AND datetime(e.evaluated_at)>=datetime(COALESCE(p.qualified_at,p.updated_at))
              AND datetime(e.evaluated_at)>=datetime(p.updated_at)
              AND datetime(e.evaluated_at)>=datetime(c.updated_at)
          )
        )
        ORDER BY p.candidate_id LIMIT ?
        """,
        (rule_hash, max(0, int(limit))),
    ).fetchall()
    return [int(row[0]) for row in rows]


def process_partition(
    connection: sqlite3.Connection,
    partition_id: str,
    *,
    provider=None,
    market_batch_size: int = 30,
    pause_requested: Callable[[], bool] | None = None,
    stop_after: int | None = None,
    first_gate_processor: Callable[..., dict] | None = None,
) -> dict:
    provider = provider or DexScreenerProvider()
    pause_requested = pause_requested or (lambda: False)
    partition = connection.execute("SELECT * FROM candidate_scan_partitions WHERE partition_id=?", (partition_id,)).fetchone()
    if not partition:
        raise ValueError("没有找到候选分片。")
    handled = 0
    as_of = utc_now()
    local_rows = connection.execute(
        """
        SELECT m.sequence_no,c.* FROM candidate_scan_partition_members m
        JOIN candidates c ON c.candidate_id=m.candidate_id
        WHERE m.partition_id=? AND m.state='pending' ORDER BY m.sequence_no
        """,
        (partition_id,),
    ).fetchall()
    for row in local_rows:
        if pause_requested() or (stop_after is not None and handled >= stop_after):
            connection.execute("UPDATE candidate_scan_partitions SET state='paused',stage='local_scan',last_checkpoint_at=?,updated_at=? WHERE partition_id=?", (utc_now(), utc_now(), partition_id))
            connection.commit()
            return {"status": "paused", "partitionId": partition_id, "handled": handled}
        result = classify_local(row, as_of)
        _upsert_local(connection, row, result, as_of)
        member_state = "local_done" if result["state"] == "local_pass" else "completed"
        connection.execute("UPDATE candidate_scan_partition_members SET state=? WHERE partition_id=? AND sequence_no=?", (member_state, partition_id, row["sequence_no"]))
        handled += 1
        if handled % 500 == 0:
            connection.execute("UPDATE candidate_scan_partitions SET stage='local_scan',local_scanned_count=local_scanned_count+500,last_committed_cursor=?,last_checkpoint_at=?,last_heartbeat_at=?,updated_at=? WHERE partition_id=?", (row["sequence_no"], as_of, utc_now(), utc_now(), partition_id))
            connection.commit()
    remaining_local = connection.execute("SELECT COUNT(*) FROM candidate_scan_partition_members WHERE partition_id=? AND state='pending'", (partition_id,)).fetchone()[0]
    local_done_count = connection.execute("SELECT COUNT(*) FROM candidate_scan_partition_members WHERE partition_id=? AND state IN ('local_done','completed')", (partition_id,)).fetchone()[0]
    connection.execute("UPDATE candidate_scan_partitions SET stage='market_confirmation',local_scanned_count=?,last_checkpoint_at=?,last_heartbeat_at=?,updated_at=? WHERE partition_id=?", (local_done_count, utc_now(), utc_now(), utc_now(), partition_id))
    connection.commit()
    if remaining_local:
        return {"status": "paused", "partitionId": partition_id, "handled": handled}
    while True:
        rows = connection.execute(
            """
            SELECT m.sequence_no,c.* FROM candidate_scan_partition_members m
            JOIN candidates c ON c.candidate_id=m.candidate_id
            WHERE m.partition_id=? AND m.state='local_done'
            ORDER BY m.sequence_no LIMIT ?
            """,
            (partition_id, market_batch_size),
        ).fetchall()
        if not rows:
            break
        if pause_requested() or (stop_after is not None and handled >= stop_after):
            connection.execute("UPDATE candidate_scan_partitions SET state='paused',stage='market_confirmation',last_checkpoint_at=?,updated_at=? WHERE partition_id=?", (utc_now(), utc_now(), partition_id))
            connection.commit()
            return {"status": "paused", "partitionId": partition_id, "handled": handled}
        try:
            results = provider.lookup(partition["network_id"], rows)
        except Exception as error:
            results = {
                int(row["candidate_id"]): {
                    "sourceState": "program_failure",
                    "attempts": [{"error": f"{type(error).__name__}: {error}"}],
                }
                for row in rows
            }
        observed_at = utc_now()
        for row in rows:
            _save_market_result(
                connection,
                row,
                results.get(row["candidate_id"], {"sourceState": "program_failure"}),
                observed_at,
                source_queue=str(partition["queue_name"]),
            )
            connection.execute("UPDATE candidate_scan_partition_members SET state='completed' WHERE partition_id=? AND sequence_no=?", (partition_id, row["sequence_no"]))
            handled += 1
        connection.execute(
            """
            UPDATE candidate_scan_partitions SET market_requested_count=market_requested_count+?,
              last_committed_cursor=?,last_checkpoint_at=?,last_heartbeat_at=?,updated_at=? WHERE partition_id=?
            """,
            (len(rows), rows[-1]["sequence_no"], observed_at, observed_at, observed_at, partition_id),
        )
        connection.commit()
    counts = connection.execute(
        """
        SELECT COUNT(*) total,
          SUM(CASE WHEN p.market_state='market_confirmed' THEN 1 ELSE 0 END) market_confirmed,
          SUM(CASE WHEN p.tracking_eligible=1 THEN 1 ELSE 0 END) tracking_eligible
        FROM candidate_scan_partition_members m
        LEFT JOIN candidate_production_records p ON p.candidate_id=m.candidate_id
        WHERE m.partition_id=?
        """,
        (partition_id,),
    ).fetchone()
    completed_at = utc_now()
    batch_id = "qualification-" + hashlib.sha256(f"{partition_id}|{partition['input_hash']}".encode()).hexdigest()[:24]
    batch_state = "building" if first_gate_processor else "completed"
    connection.execute(
        "INSERT OR REPLACE INTO candidate_qualification_batches(qualification_batch_id,partition_id,state,candidate_count,created_at,completed_at,input_hash) VALUES(?,?,?,?,?,?,?)",
        (batch_id, partition_id, batch_state, int(counts["tracking_eligible"] or 0), completed_at, None if first_gate_processor else completed_at, partition["input_hash"]),
    )
    connection.execute("DELETE FROM candidate_qualification_members WHERE qualification_batch_id=?", (batch_id,))
    connection.execute(
        """
        INSERT INTO candidate_qualification_members(qualification_batch_id,candidate_id,asset_id,relationship_class,front_eligible)
        SELECT ?,p.candidate_id,p.asset_id,p.relationship_class,p.front_eligible
        FROM candidate_scan_partition_members m JOIN candidate_production_records p ON p.candidate_id=m.candidate_id
        WHERE m.partition_id=? AND p.tracking_eligible=1
        """,
        (batch_id, partition_id),
    )
    connection.execute("UPDATE candidate_production_records SET qualification_batch_id=?,qualified_at=? WHERE candidate_id IN (SELECT candidate_id FROM candidate_qualification_members WHERE qualification_batch_id=?)", (batch_id, completed_at, batch_id))
    connection.execute(
        """UPDATE candidate_first_gate_queue SET qualification_batch_id=?,updated_at=?
        WHERE candidate_id IN (
          SELECT candidate_id FROM candidate_qualification_members WHERE qualification_batch_id=?
        )""",
        (batch_id, completed_at, batch_id),
    )
    connection.commit()
    if first_gate_processor:
        candidate_ids = [
            int(row[0]) for row in connection.execute(
                "SELECT candidate_id FROM candidate_qualification_members WHERE qualification_batch_id=? ORDER BY candidate_id",
                (batch_id,),
            ).fetchall()
        ]
        first_gate_processor(connection, candidate_ids=candidate_ids, refresh_market=False)
        connection.execute(
            """UPDATE candidate_qualification_members SET
              relationship_class=(SELECT p.relationship_class FROM candidate_production_records p WHERE p.candidate_id=candidate_qualification_members.candidate_id),
              front_eligible=(SELECT p.front_eligible FROM candidate_production_records p WHERE p.candidate_id=candidate_qualification_members.candidate_id)
            WHERE qualification_batch_id=?""",
            (batch_id,),
        )
        connection.execute(
            "UPDATE candidate_qualification_batches SET state='completed',completed_at=? WHERE qualification_batch_id=?",
            (utc_now(), batch_id),
        )
    connection.execute(
        """
        UPDATE candidate_scan_partitions SET state='completed',stage='completed',processed_count=?,
          market_confirmed_count=?,tracking_eligible_count=?,last_checkpoint_at=?,last_heartbeat_at=?,
          updated_at=?,completed_at=?,source_state='success' WHERE partition_id=?
        """,
        (int(counts["total"] or 0), int(counts["market_confirmed"] or 0), int(counts["tracking_eligible"] or 0), completed_at, completed_at, completed_at, completed_at, partition_id),
    )
    connection.commit()
    return {"status": "completed", "partitionId": partition_id, "qualificationBatchId": batch_id, "handled": handled, "trackingEligible": int(counts["tracking_eligible"] or 0)}


def funnel_status(connection: sqlite3.Connection) -> dict:
    if not schema_ready(connection):
        return {"schemaVersion": "c2.2-candidate-production-status-v1", "state": "not_migrated", "formalHistoricalScanAuthorized": False}
    candidate_count = connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    row = connection.execute(
        """
        SELECT COUNT(*) local_scanned,
          SUM(CASE WHEN local_state='local_pending' THEN 1 ELSE 0 END) local_pending,
          SUM(CASE WHEN local_state NOT IN ('local_pass','local_pending') THEN 1 ELSE 0 END) local_excluded,
          SUM(CASE WHEN market_state IS NOT NULL THEN 1 ELSE 0 END) market_requested,
          SUM(CASE WHEN market_state='market_confirmed' THEN 1 ELSE 0 END) market_confirmed,
          SUM(CASE WHEN market_state='waiting_for_trades' THEN 1 ELSE 0 END) waiting_for_trades,
          SUM(CASE WHEN market_state='market_not_indexed' THEN 1 ELSE 0 END) market_not_indexed,
          SUM(CASE WHEN tracking_eligible=1 THEN 1 ELSE 0 END) tracking_eligible,
          SUM(CASE WHEN tracking_eligible=1 AND identity_consistent=0 THEN 1 ELSE 0 END) tracking_pending_identity,
          SUM(CASE WHEN tracking_eligible=1 AND relationship_class='D' THEN 1 ELSE 0 END) tracking_class_d,
          SUM(CASE WHEN front_eligible=1 AND EXISTS(
            SELECT 1 FROM candidate_first_gate_queue fq
            WHERE fq.candidate_id=candidate_production_records.candidate_id AND fq.state='completed'
          ) THEN 1 ELSE 0 END) front_eligible
        FROM candidate_production_records
        """
    ).fetchone()
    first_gate = connection.execute(
        """SELECT COUNT(*) queued,
          SUM(CASE WHEN q.state='completed' THEN 1 ELSE 0 END) completed,
          SUM(CASE WHEN q.state IN ('pending','retrying','running','failed')
                        AND p.market_state='market_confirmed' AND b.state='completed' THEN 1 ELSE 0 END) pending,
          SUM(CASE WHEN q.state IN ('pending','retrying','running','failed')
                        AND (b.state IS NULL OR b.state!='completed') THEN 1 ELSE 0 END) deferred,
          SUM(CASE WHEN q.state IN ('pending','retrying','running','failed')
                        AND (p.age_days<0 OR p.age_days>90) THEN 1 ELSE 0 END) outside_window,
          SUM(CASE WHEN q.state='running' THEN 1 ELSE 0 END) running,
          SUM(CASE WHEN q.state='failed' THEN 1 ELSE 0 END) failed,
          SUM(CASE WHEN q.source_queue='historical_backlog' THEN 1 ELSE 0 END) historical_queued,
          SUM(CASE WHEN q.source_queue='historical_backlog' AND q.state='completed' THEN 1 ELSE 0 END) historical_completed,
          SUM(CASE WHEN q.source_queue='historical_backlog'
                        AND q.state IN ('pending','retrying','running','failed')
                        AND p.market_state='market_confirmed' AND b.state='completed' THEN 1 ELSE 0 END) historical_pending,
          SUM(CASE WHEN q.source_queue='historical_backlog'
                        AND q.state IN ('pending','retrying','running','failed')
                        AND (p.age_days<0 OR p.age_days>90) THEN 1 ELSE 0 END) historical_outside_window,
          SUM(CASE WHEN q.source_queue='daily_incremental' THEN 1 ELSE 0 END) daily_queued,
          SUM(CASE WHEN q.source_queue='daily_incremental' AND q.state='completed' THEN 1 ELSE 0 END) daily_completed
        FROM candidate_first_gate_queue q
        LEFT JOIN candidate_production_records p ON p.candidate_id=q.candidate_id
        LEFT JOIN candidate_qualification_batches b
          ON b.qualification_batch_id=p.qualification_batch_id"""
    ).fetchone()
    first_gate_outside_window = connection.execute(
        """SELECT COUNT(*) FROM candidate_production_records p
        LEFT JOIN candidate_first_gate_queue q ON q.candidate_id=p.candidate_id
        WHERE p.market_state='market_confirmed' AND (p.age_days<0 OR p.age_days>90)
          AND (q.candidate_id IS NULL OR q.state!='completed')"""
    ).fetchone()[0]
    partitions = [dict(item) for item in connection.execute("SELECT queue_name,state,COUNT(*) count,SUM(total_count) candidates FROM candidate_scan_partitions GROUP BY queue_name,state")]
    current = connection.execute("SELECT * FROM candidate_scan_partitions WHERE state='running' ORDER BY updated_at DESC LIMIT 1").fetchone()
    recent = [dict(item) for item in connection.execute(
        """SELECT * FROM candidate_scan_partitions
        WHERE state IN ('running','paused','retrying','failed')
        ORDER BY updated_at DESC LIMIT 12"""
    )]
    current_run = connection.execute(
        "SELECT * FROM candidate_production_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    queue_summaries = {}
    for queue_name in QUEUES:
        summary = connection.execute(
            """WITH queue_candidates AS (
              SELECT DISTINCT m.candidate_id
              FROM candidate_scan_partition_members m
              JOIN candidate_scan_partitions sp ON sp.partition_id=m.partition_id
              WHERE sp.queue_name=?
            )
            SELECT COUNT(*) queued,
              SUM(CASE WHEN p.candidate_id IS NOT NULL THEN 1 ELSE 0 END) local_scanned,
              SUM(CASE WHEN p.local_state NOT IN ('local_pass','local_pending') THEN 1 ELSE 0 END) local_excluded,
              SUM(CASE WHEN p.market_state IS NOT NULL THEN 1 ELSE 0 END) market_requested,
              SUM(CASE WHEN p.market_state='market_confirmed' THEN 1 ELSE 0 END) market_confirmed,
              SUM(CASE WHEN p.market_state='waiting_for_trades' THEN 1 ELSE 0 END) waiting_for_trades,
              SUM(CASE WHEN p.tracking_eligible=1 AND p.age_days BETWEEN 0 AND 90 THEN 1 ELSE 0 END) tracking_eligible,
              SUM(CASE WHEN fq.state='completed' THEN 1 ELSE 0 END) first_gate_processed,
              SUM(CASE WHEN fq.state IN ('pending','retrying','running','failed')
                            AND p.market_state='market_confirmed' AND b.state='completed' THEN 1 ELSE 0 END) first_gate_pending,
              SUM(CASE WHEN fq.state IN ('pending','retrying','running','failed')
                            AND (b.state IS NULL OR b.state!='completed') THEN 1 ELSE 0 END) first_gate_deferred,
              SUM(CASE WHEN p.market_state='market_confirmed' AND (p.age_days<0 OR p.age_days>90)
                            AND (fq.candidate_id IS NULL OR fq.state!='completed') THEN 1 ELSE 0 END) first_gate_outside_window,
              SUM(CASE WHEN p.front_eligible=1 AND fq.state='completed' THEN 1 ELSE 0 END) front_eligible,
              SUM(CASE WHEN p.market_source_state IN ('quota_limited','source_failure','configuration_missing','program_failure') THEN 1 ELSE 0 END) source_incomplete,
              SUM(CASE WHEN p.next_retry_at IS NOT NULL THEN 1 ELSE 0 END) scheduled_retry
            FROM queue_candidates q
            LEFT JOIN candidate_production_records p ON p.candidate_id=q.candidate_id
            LEFT JOIN candidate_first_gate_queue fq ON fq.candidate_id=q.candidate_id
            LEFT JOIN candidate_qualification_batches b
              ON b.qualification_batch_id=p.qualification_batch_id""",
            (queue_name,),
        ).fetchone()
        queue_summaries[queue_name] = {
            "queuedCandidateCount": int(summary["queued"] or 0),
            "localScannedCount": int(summary["local_scanned"] or 0),
            "localExcludedCount": int(summary["local_excluded"] or 0),
            "marketRequestedCount": int(summary["market_requested"] or 0),
            "marketConfirmedCount": int(summary["market_confirmed"] or 0),
            "waitingForTradesCount": int(summary["waiting_for_trades"] or 0),
            "trackingEligibleCount": int(summary["tracking_eligible"] or 0),
            "firstGateProcessedCount": int(summary["first_gate_processed"] or 0),
            "firstGatePendingCount": int(summary["first_gate_pending"] or 0),
            "firstGateDeferredCount": int(summary["first_gate_deferred"] or 0),
            "firstGateOutsideWindowCount": int(summary["first_gate_outside_window"] or 0),
            "convexityTrackingInputCount": int(summary["front_eligible"] or 0),
            "frontEligibleCount": int(summary["front_eligible"] or 0),
            "sourceIncompleteCount": int(summary["source_incomplete"] or 0),
            "scheduledRetryCount": int(summary["scheduled_retry"] or 0),
        }
    completed_history = connection.execute(
        """SELECT total_count,started_at,completed_at FROM candidate_scan_partitions
        WHERE queue_name='historical_backlog' AND state='completed' AND completed_at IS NOT NULL
        ORDER BY completed_at DESC LIMIT 10"""
    ).fetchall()
    rates = []
    for item in completed_history:
        started = parse_time(item["started_at"])
        finished = parse_time(item["completed_at"])
        seconds = (finished - started).total_seconds() if started and finished else 0
        if seconds > 0 and item["total_count"]:
            rates.append(float(item["total_count"]) / seconds)
    eta_seconds = None
    eta_confidence = None
    if len(rates) >= 5:
        mean = sum(rates) / len(rates)
        variance = sum((value - mean) ** 2 for value in rates) / len(rates)
        coefficient = math.sqrt(variance) / mean if mean > 0 else float("inf")
        if coefficient <= 0.5:
            remaining = max(0, int(candidate_count) - int(row["local_scanned"] or 0))
            eta_seconds = round(remaining / mean) if mean > 0 else None
            eta_confidence = "high" if coefficient <= 0.25 else "medium"
    return {
        "schemaVersion": "c2.2-candidate-production-status-v1",
        "state": "running" if current else "ready",
        "importedCandidateCount": int(candidate_count),
        "localScannedCount": int(row["local_scanned"] or 0),
        "localPendingCount": int(row["local_pending"] or 0),
        "localExcludedCount": int(row["local_excluded"] or 0),
        "marketRequestedCount": int(row["market_requested"] or 0),
        "marketConfirmedCount": int(row["market_confirmed"] or 0),
        "waitingForTradesCount": int(row["waiting_for_trades"] or 0),
        "marketNotIndexedCount": int(row["market_not_indexed"] or 0),
        "trackingEligibleCount": int(first_gate["queued"] or 0),
        "t0HandoffCount": int(first_gate["queued"] or 0),
        "t0VerifiedCount": int(connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE t0_status='verified_in_supported_scope'"
        ).fetchone()[0]),
        "trackingPendingIdentityCount": int(row["tracking_pending_identity"] or 0),
        "trackingClassDCount": int(row["tracking_class_d"] or 0),
        "firstGateQueuedCount": int(first_gate["queued"] or 0),
        "firstGateProcessedCount": int(first_gate["completed"] or 0),
        "firstGatePendingCount": int(first_gate["pending"] or 0),
        "firstGateDeferredCount": int(first_gate["deferred"] or 0),
        "firstGateOutsideWindowCount": int(first_gate_outside_window or 0),
        "firstGateRunningCount": int(first_gate["running"] or 0),
        "firstGateFailedCount": int(first_gate["failed"] or 0),
        "historicalT0HandoffCount": int(first_gate["historical_queued"] or 0),
        "historicalFirstGateProcessedCount": int(first_gate["historical_completed"] or 0),
        "historicalFirstGatePendingCount": int(first_gate["historical_pending"] or 0),
        "historicalFirstGateOutsideWindowCount": int(first_gate["historical_outside_window"] or 0),
        "dailyT0HandoffCount": int(first_gate["daily_queued"] or 0),
        "dailyFirstGateProcessedCount": int(first_gate["daily_completed"] or 0),
        "convexityTrackingInputCount": int(row["front_eligible"] or 0),
        "frontEligibleCount": int(row["front_eligible"] or 0),
        "hardGateEvaluatedCount": int(connection.execute(
            """SELECT COUNT(DISTINCT e.candidate_id) FROM evaluations e
            JOIN candidate_production_records p ON p.candidate_id=e.candidate_id
            WHERE e.is_current=1 AND p.qualification_batch_id IS NOT NULL"""
        ).fetchone()[0]),
        "localExcludedCountByReason": dict(connection.execute("SELECT local_state,COUNT(*) FROM candidate_production_records WHERE local_state NOT IN ('local_pass','local_pending') GROUP BY local_state")),
        "partitions": partitions,
        "currentPartition": dict(current) if current else None,
        "recentPartitions": recent,
        "currentRun": dict(current_run) if current_run else None,
        "queueSummaries": queue_summaries,
        "sourceStateCounts": dict(connection.execute(
            "SELECT market_source_state,COUNT(*) FROM candidate_production_records WHERE market_source_state IS NOT NULL GROUP BY market_source_state"
        )),
        "nextRetryAt": connection.execute(
            "SELECT MIN(next_retry_at) FROM candidate_production_records WHERE next_retry_at IS NOT NULL"
        ).fetchone()[0],
        "stablePartitionCount": len(rates),
        "etaSeconds": eta_seconds,
        "etaConfidence": eta_confidence,
        "formalHistoricalScanAuthorized": False,
        "gate0Rerun": False,
    }


def _status_path_for_database(db_path: Path, explicit_path: Path | None) -> Path | None:
    if explicit_path is not None:
        return Path(explicit_path)
    try:
        return STATUS_PATH if Path(db_path).resolve() == DEFAULT_DB_PATH.resolve() else None
    except OSError:
        return None


def publish_status_snapshot(connection: sqlite3.Connection, path: Path | None) -> dict:
    payload = funnel_status(connection)
    if path is None:
        return payload
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return payload


@contextmanager
def worker_lock(path: Path = LOCK_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        acquired = True
    except FileExistsError:
        try:
            from c2_2_runtime import pid_is_running

            stale = not pid_is_running(int(path.read_text(encoding="ascii").strip()))
        except (OSError, ValueError):
            stale = True
        if stale:
            path.unlink(missing_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                acquired = True
            except (FileExistsError, OSError):
                acquired = False
    try:
        yield acquired
    finally:
        if acquired:
            path.unlink(missing_ok=True)


def run_worker(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    historical_authorized: bool = False,
    queue_only: str | None = None,
    max_partitions: int | None = None,
    partition_size: int = 5000,
    lock_path: Path = LOCK_PATH,
    provider=None,
    pause_requested: Callable[[], bool] | None = None,
    status_path: Path | None = None,
) -> dict:
    if pause_requested is None:
        if Path(db_path).resolve() == DEFAULT_DB_PATH.resolve():
            def pause_requested() -> bool:
                try:
                    payload = json.loads((RUNTIME_ROOT / "pause.json").read_text(encoding="utf-8"))
                except (FileNotFoundError, OSError, json.JSONDecodeError):
                    return False
                return bool(payload.get("requested"))
        else:
            pause_requested = lambda: False
    run_id = "candidate-production-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    status_path = _status_path_for_database(db_path, status_path)
    with worker_lock(lock_path) as acquired:
        if not acquired:
            return {"status": "already_running", "message": "历史候选基础扫描已有一个写入者。"}
        with closing(open_pipeline_db(db_path)) as connection:
            if not schema_ready(connection):
                return {"status": "not_migrated", "message": "候选生产化显式数据库迁移尚未执行。"}
            connection.execute(
                """UPDATE candidate_scan_partitions SET state='paused',updated_at=?,
                error_detail=CASE WHEN error_detail='' THEN 'recovered_after_process_stop' ELSE error_detail END
                WHERE state='running'""",
                (utc_now(),),
            )
            connection.execute(
                """UPDATE candidate_first_gate_queue SET state='retrying',updated_at=?,
                error_code=CASE WHEN error_code='' THEN 'recovered_after_process_stop' ELSE error_code END,
                error_detail=CASE WHEN error_detail='' THEN '进程停止后从已保存的第一关队列恢复。' ELSE error_detail END
                WHERE state='running'""",
                (utc_now(),),
            )
            connection.commit()
            handoff_backfill = backfill_first_gate_handoff(connection)
            prepare_daily_if_due(
                connection,
                partition_size=partition_size,
                force=queue_only == "daily_incremental",
            )
            if historical_authorized and not historical_queue_is_fully_prepared(connection):
                prepare_partitions(
                    connection, queue="historical_backlog", historical_authorized=True,
                    partition_size=partition_size,
                )
            now = utc_now()
            connection.execute("INSERT INTO candidate_production_runs(run_id,trigger_kind,state,started_at,updated_at,message) VALUES(?,?,'running',?,?,?)", (run_id, "manual", now, now, "候选生产化工作进程已启动。"))
            connection.commit()
            results = []
            first_gate_repairs = []
            production_first_gate = process_first_gate_candidates if provider is None else None
            while max_partitions is None or len(results) < max_partitions:
                if pause_requested():
                    break
                if production_first_gate and max_partitions != 0:
                    repair_ids = pending_first_gate_candidate_ids(
                        connection,
                        limit=250 if max_partitions is None else 50,
                    )
                    if repair_ids:
                        connection.execute(
                            """UPDATE candidate_production_runs SET selected_queue='first_gate_backlog',
                            current_partition_id=NULL,updated_at=?,message=? WHERE run_id=?""",
                            (
                                utc_now(),
                                f"正在处理新币筛选第一关：本批{len(repair_ids)}条。",
                                run_id,
                            ),
                        )
                        connection.commit()
                        try:
                            first_gate_repairs.append(
                                production_first_gate(
                                    connection,
                                    candidate_ids=repair_ids,
                                    refresh_market=False,
                                )
                            )
                            if Path(db_path).resolve() == DEFAULT_DB_PATH.resolve():
                                first_gate_repairs[-1]["snapshots"] = publish_first_gate_snapshots(connection)
                        except Exception as error:
                            retry_first_gate_candidates(connection, repair_ids, error)
                            failed_at = utc_now()
                            detail = f"{type(error).__name__}: {error}"
                            connection.rollback()
                            connection.execute(
                                """UPDATE candidate_production_runs SET state='failed',updated_at=?,finished_at=?,
                                error_code='program_failure',error_detail=?,message=? WHERE run_id=?""",
                                (failed_at, failed_at, detail, "候选第一关补处理失败；已保留市场扫描断点。", run_id),
                            )
                            connection.commit()
                            failure_status = publish_status_snapshot(connection, status_path)
                            return {
                                "status": "failed", "runId": run_id,
                                "errorCode": "program_failure", "error": detail,
                                "stage": "first_gate_repair", "funnel": failure_status,
                            }
                        latest_status = publish_status_snapshot(connection, status_path)
                        if max_partitions is None:
                            continue
                partition = claim_next_partition(connection, queue_only)
                if not partition:
                    break
                connection.execute(
                    "UPDATE candidate_production_runs SET selected_queue=?,current_partition_id=?,updated_at=? WHERE run_id=?",
                    (partition["queue_name"], partition["partition_id"], utc_now(), run_id),
                )
                connection.commit()
                try:
                    result = process_partition(
                        connection, partition["partition_id"], provider=provider,
                        pause_requested=pause_requested,
                        first_gate_processor=production_first_gate,
                    )
                except Exception as error:
                    failed_at = utc_now()
                    retry_at = (parse_time(failed_at) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
                    detail = f"{type(error).__name__}: {error}"
                    connection.rollback()
                    try:
                        connection.execute(
                            """UPDATE candidate_scan_partitions SET state='failed',source_state='program_failure',
                            next_retry_at=?,error_detail=?,last_heartbeat_at=?,updated_at=? WHERE partition_id=?""",
                            (retry_at, detail, failed_at, failed_at, partition["partition_id"]),
                        )
                        connection.execute(
                            """UPDATE candidate_production_runs SET state='failed',updated_at=?,finished_at=?,
                            current_partition_id=?,error_code='program_failure',error_detail=?,message=? WHERE run_id=?""",
                            (failed_at, failed_at, partition["partition_id"], detail, "候选分片发生程序错误；已保留断点和失败范围。", run_id),
                        )
                        connection.commit()
                    except sqlite3.Error:
                        connection.rollback()
                    failure_status = publish_status_snapshot(connection, status_path)
                    return {
                        "status": "failed", "runId": run_id,
                        "errorCode": "program_failure", "error": detail,
                        "partitionId": partition["partition_id"], "funnel": failure_status,
                    }
                results.append(result)
                latest_status = publish_status_snapshot(connection, status_path)
                if result["status"] != "completed":
                    break
                # A long historical worker refreshes the small high-priority queue
                # between partitions so newly discovered candidates are not starved.
                if historical_authorized and queue_only is None:
                    prepare_daily_if_due(connection, partition_size=min(partition_size, 300))
            final = "paused" if pause_requested() else "completed"
            finished = utc_now()
            connection.execute("UPDATE candidate_production_runs SET state=?,updated_at=?,finished_at=?,completed_partitions=?,message=? WHERE run_id=?", (final, finished, finished, len([item for item in results if item["status"] == "completed"]), "候选生产化本轮已保存全部断点。", run_id))
            connection.commit()
            latest_status = publish_status_snapshot(connection, status_path)
            return {
                "status": final,
                "runId": run_id,
                "partitions": results,
                "firstGateRepairs": first_gate_repairs,
                "firstGateHandoff": handoff_backfill,
                "funnel": latest_status,
            }


def main() -> int:
    parser = argparse.ArgumentParser(description="C2.2历史候选基础扫描")
    parser.add_argument("action", choices=("status", "migrate", "prepare-daily", "prepare-history", "run"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--history-authorized", action="store_true")
    parser.add_argument("--queue", choices=QUEUES)
    parser.add_argument("--max-partitions", type=int)
    parser.add_argument("--partition-size", type=int, default=5000)
    args = parser.parse_args()
    if args.action == "migrate":
        result = migrate_database(args.db)
    else:
        with closing(open_pipeline_db(args.db)) as connection:
            if args.action == "status":
                result = funnel_status(connection)
            elif args.action == "prepare-daily":
                result = prepare_partitions(connection, queue="daily_incremental")
            elif args.action == "prepare-history":
                result = prepare_partitions(connection, queue="historical_backlog", historical_authorized=args.history_authorized)
            else:
                result = None
        if args.action == "run":
            result = run_worker(
                db_path=args.db, historical_authorized=args.history_authorized,
                queue_only=args.queue, max_partitions=args.max_partitions,
                partition_size=args.partition_size,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"failed", "not_migrated"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
