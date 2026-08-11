#!/usr/bin/env python3
"""C2.1 resumable local conversion, read-only legacy bridge and snapshot build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import uuid
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from c2_1_db import (
    DEFAULT_DB_PATH,
    DEFAULT_MAIN_DB_PATH,
    initialize_database,
    json_text,
    open_main_db_readonly,
    open_pipeline_db,
    utc_now,
)
from c2_1_rules import age_band, age_days, evaluate_candidate, load_rules, number, parse_utc, product_evidence_summary
from c2_1_enrichment import run_enrichment


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "c2.1"
DEFAULT_STATUS_PATH = RUNTIME_ROOT / "pipeline-status.json"
DEFAULT_LOCK_PATH = RUNTIME_ROOT / "pipeline.lock"
DEFAULT_GATE0_RUN_ID = "gate0-solfinal-20260809T045924Z-f7bbd2"
DEFAULT_GATE0_ROOT = (
    PROJECT_ROOT
    / "runtime"
    / "gate0-shadow"
    / "backfill"
    / "background"
    / "runs"
    / DEFAULT_GATE0_RUN_ID
)
DEFAULT_CANDIDATE_PATH = DEFAULT_GATE0_ROOT / "candidate-tokens.jsonl"
DEFAULT_GATE0_SUMMARY_PATH = DEFAULT_GATE0_ROOT / "summary.json"
DEFAULT_FRONT_SNAPSHOT = APP_ROOT / "c2-1-front-snapshot.js"
DEFAULT_BACKEND_SNAPSHOT = APP_ROOT / "c2-1-admin-snapshot.js"
FRONT_PREFIX = "window.PENGUIN_CONVEXITY_C21 = "
BACKEND_PREFIX = "window.PENGUIN_CONVEXITY_C21_ADMIN = "
VALID_SOURCE_STATES = {
    "success",
    "no_data",
    "quota_limited",
    "source_failure",
    "unsupported",
    "configuration_missing",
    "program_failure",
}

SOURCE_AFFECTED_FIELDS = {
    "coingecko_new_pools": ["新项目发现覆盖"],
    "dexscreener": ["公开市场", "成交与流动性"],
    "project_website_identity": ["项目身份", "官方仓库链路"],
    "github": ["代码产品证据", "仓库活跃"],
    "goplus": ["显性硬风险", "当前供应与持仓"],
    "c2_1_path4": ["已索引池OHLCV", "历史供应"],
    "standard_sell_quote": ["100美元标准卖出报价"],
    "c2_1_pipeline": ["当前完整快照"],
    "convexity_main_readonly": ["项目身份", "既有市场与风险事实"],
}


class PipelinePaused(RuntimeError):
    pass


def ensure_not_paused():
    request = load_json(RUNTIME_ROOT / "pause-current.json", {})
    if request.get("requested"):
        raise PipelinePaused("用户已暂停当前任务；所有已提交断点均已保留。")


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def atomic_json(path: Path, payload) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def file_sha256(path: Path, chunk_size=8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def status_payload(**updates):
    payload = {
        "schemaVersion": "c2.1-pipeline-status-v1",
        "state": "idle",
        "stage": "not_started",
        "runId": "",
        "triggerKind": "",
        "startedAt": None,
        "updatedAt": utc_now(),
        "finishedAt": None,
        "completedUnits": 0,
        "totalUnits": 0,
        "progressPct": 0,
        "currentItem": "",
        "message": "C2.1流水线尚未运行。",
        "resumeAvailable": False,
        "errorCode": "",
        "errorDetail": "",
    }
    payload.update(load_json(DEFAULT_STATUS_PATH, {}))
    payload.update(updates)
    payload["updatedAt"] = utc_now()
    completed = int(payload.get("completedUnits") or 0)
    total = int(payload.get("totalUnits") or 0)
    payload["progressPct"] = round(completed / total * 100, 2) if total else 0
    atomic_json(DEFAULT_STATUS_PATH, payload)
    return payload


def pid_is_running(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


@contextmanager
def pipeline_lock(path=DEFAULT_LOCK_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    try:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            lock = load_json(path, {})
            if pid_is_running(lock.get("pid")):
                yield False
                return
            path.unlink(missing_ok=True)
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        payload = json.dumps({"pid": os.getpid(), "createdAt": utc_now()}, ensure_ascii=False).encode("utf-8")
        os.write(handle, payload)
        yield True
    finally:
        if handle is not None:
            os.close(handle)
            path.unlink(missing_ok=True)


def normalize_address(network_id, value):
    text = str(value or "").strip()
    return text if network_id == "solana-mainnet" else text.lower()


def run_row(connection, run_id, trigger_kind, state, stage, **updates):
    now = utc_now()
    connection.execute(
        """
        INSERT INTO pipeline_runs(
          run_id,trigger_kind,state,stage,started_at,updated_at,finished_at,current_item,
          completed_units,total_units,message,error_code,error_detail
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET
          state=excluded.state,stage=excluded.stage,updated_at=excluded.updated_at,
          finished_at=excluded.finished_at,current_item=excluded.current_item,
          completed_units=excluded.completed_units,total_units=excluded.total_units,
          message=excluded.message,error_code=excluded.error_code,error_detail=excluded.error_detail
        """,
        (
            run_id,
            trigger_kind,
            state,
            stage,
            updates.get("started_at") or now,
            now,
            updates.get("finished_at"),
            updates.get("current_item", ""),
            int(updates.get("completed_units") or 0),
            int(updates.get("total_units") or 0),
            updates.get("message", ""),
            updates.get("error_code", ""),
            updates.get("error_detail", ""),
        ),
    )


def source_cursor(connection, source_id, scope_key, stage):
    row = connection.execute(
        "SELECT * FROM source_cursors WHERE source_id=? AND scope_key=? AND stage=?",
        (source_id, scope_key, stage),
    ).fetchone()
    return dict(row) if row else None


def save_cursor(connection, source_id, scope_key, stage, cursor, status="success"):
    now = utc_now()
    connection.execute(
        """
        INSERT INTO source_cursors(source_id,scope_key,stage,cursor_json,status,last_success_at,updated_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(source_id,scope_key,stage) DO UPDATE SET
          cursor_json=excluded.cursor_json,status=excluded.status,last_success_at=excluded.last_success_at,
          consecutive_failures=CASE WHEN excluded.status='success' THEN 0 ELSE source_cursors.consecutive_failures END,
          updated_at=excluded.updated_at
        """,
        (source_id, scope_key, stage, json_text(cursor), status, now if status == "success" else None, now),
    )


def set_source_health(connection, source_id, scope_key, status, reason_code="", plain_reason="", **values):
    if status not in VALID_SOURCE_STATES:
        raise ValueError(f"不支持的来源状态：{status}")
    now = utc_now()
    connection.execute(
        """
        INSERT INTO source_health(
          source_id,scope_key,status,reason_code,plain_reason,http_status,quota_remaining,
          quota_reset_at,affected_object_count,last_success_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id,scope_key) DO UPDATE SET
          status=excluded.status,reason_code=excluded.reason_code,plain_reason=excluded.plain_reason,
          http_status=excluded.http_status,quota_remaining=excluded.quota_remaining,
          quota_reset_at=excluded.quota_reset_at,affected_object_count=excluded.affected_object_count,
          last_success_at=COALESCE(excluded.last_success_at,source_health.last_success_at),updated_at=excluded.updated_at
        """,
        (
            source_id,
            scope_key,
            status,
            reason_code,
            plain_reason,
            values.get("http_status"),
            values.get("quota_remaining"),
            values.get("quota_reset_at"),
            int(values.get("affected_object_count") or 0),
            now if status == "success" else values.get("last_success_at"),
            now,
        ),
    )


def gate0_total(candidate_path=DEFAULT_CANDIDATE_PATH):
    if Path(candidate_path).resolve() != DEFAULT_CANDIDATE_PATH.resolve():
        with Path(candidate_path).open("rb") as handle:
            return sum(1 for _line in handle)
    summary = load_json(DEFAULT_GATE0_SUMMARY_PATH, {})
    total = int((summary.get("coverage") or {}).get("candidateTokens") or summary.get("candidateTokens") or 0)
    if total:
        return total
    latest = load_json(PROJECT_ROOT / "runtime" / "gate0-shadow" / "backfill" / "latest.json", {})
    return int(latest.get("candidateTokens") or 0)


def import_gate0_candidates(connection, run_id, trigger_kind, candidate_path=DEFAULT_CANDIDATE_PATH, batch_size=25000):
    candidate_path = Path(candidate_path).resolve()
    if not candidate_path.exists():
        raise FileNotFoundError(candidate_path)
    source_id = "gate0_accepted_candidates"
    stage = "candidate_import"
    scope = DEFAULT_GATE0_RUN_ID
    cursor_row = source_cursor(connection, source_id, scope, stage)
    cursor = json.loads(cursor_row["cursor_json"]) if cursor_row else {}
    expected_size = candidate_path.stat().st_size
    if cursor and cursor.get("fileSize") not in {None, expected_size}:
        raise RuntimeError("Gate 0正式候选文件大小变化，拒绝从旧游标继续。")
    byte_offset = int(cursor.get("byteOffset") or 0)
    line_number = int(cursor.get("lineNumber") or 0)
    total = gate0_total(candidate_path)
    if cursor.get("completed") and line_number >= total:
        return {"imported": line_number, "total": total, "resumed": True, "completed": True}
    first_seen = utc_now()
    status_payload(state="running", stage=stage, runId=run_id, triggerKind=trigger_kind, completedUnits=line_number, totalUnits=total, currentItem=f"字节 {byte_offset:,}", message="正在把Gate 0正式候选转换到C2.1独立数据库。", resumeAvailable=bool(byte_offset))
    rows = []
    with candidate_path.open("rb") as handle:
        handle.seek(byte_offset)
        while raw := handle.readline():
            line_number += 1
            try:
                row = json.loads(raw)
                network_id = str(row["networkId"])
                token_address = str(row["tokenAddress"])
                t0 = str(row["earliestCoveredPoolAt"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(f"Gate 0候选第{line_number}行无效：{type(error).__name__}") from error
            now = utc_now()
            rows.append((
                network_id,
                token_address,
                normalize_address(network_id, token_address),
                t0,
                t0,
                str(row.get("t0EvidenceType") or "covered_dex_pool_created"),
                json_text({"scope": "gate0_accepted_registered_dex_range", "runId": DEFAULT_GATE0_RUN_ID}),
                str(row.get("poolId") or ""),
                json_text(row.get("dexIds") or []),
                DEFAULT_GATE0_RUN_ID,
                first_seen,
                now,
                now,
            ))
            if len(rows) >= batch_size:
                ensure_not_paused()
                byte_offset = handle.tell()
                _commit_candidate_batch(connection, rows, source_id, scope, stage, candidate_path, byte_offset, line_number, False)
                rows.clear()
                run_row(connection, run_id, trigger_kind, "running", stage, completed_units=line_number, total_units=total, current_item=f"候选 {line_number:,}", message="Gate 0候选本地转换已提交安全点。")
                connection.commit()
                status_payload(state="running", stage=stage, runId=run_id, triggerKind=trigger_kind, completedUnits=line_number, totalUnits=total, currentItem=f"候选 {line_number:,}", message="Gate 0候选本地转换已提交安全点。", resumeAvailable=True)
        byte_offset = handle.tell()
    if rows:
        _commit_candidate_batch(connection, rows, source_id, scope, stage, candidate_path, byte_offset, line_number, True)
        rows.clear()
    else:
        save_cursor(connection, source_id, scope, stage, {"filePath": str(candidate_path), "fileSize": expected_size, "byteOffset": byte_offset, "lineNumber": line_number, "completed": True}, "success")
    set_source_health(connection, source_id, scope, "success", plain_reason="Gate 0正式候选已只读转换完成。", affected_object_count=line_number)
    connection.commit()
    return {"imported": line_number, "total": total, "resumed": bool(cursor), "completed": True}


def _commit_candidate_batch(connection, rows, source_id, scope, stage, candidate_path, byte_offset, line_number, completed):
    connection.executemany(
        """
        INSERT INTO candidates(
          network_id,token_address,token_address_normalized,gate0_t0,effective_t0,
          t0_evidence_type,t0_scope_json,gate0_pool_id,dex_ids_json,source_run_id,
          first_seen_at,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(network_id,token_address_normalized) DO UPDATE SET
          gate0_t0=CASE WHEN excluded.gate0_t0<candidates.gate0_t0 THEN excluded.gate0_t0 ELSE candidates.gate0_t0 END,
          effective_t0=CASE WHEN excluded.effective_t0<candidates.effective_t0 THEN excluded.effective_t0 ELSE candidates.effective_t0 END,
          gate0_pool_id=CASE WHEN excluded.gate0_t0<candidates.gate0_t0 THEN excluded.gate0_pool_id ELSE candidates.gate0_pool_id END,
          dex_ids_json=CASE WHEN excluded.gate0_t0<candidates.gate0_t0 THEN excluded.dex_ids_json ELSE candidates.dex_ids_json END,
          updated_at=excluded.updated_at
        """,
        rows,
    )
    save_cursor(connection, source_id, scope, stage, {"filePath": str(candidate_path), "fileSize": candidate_path.stat().st_size, "byteOffset": byte_offset, "lineNumber": line_number, "completed": completed}, "success")
    connection.commit()


def sync_main_mappings(connection, main_db_path=DEFAULT_MAIN_DB_PATH):
    now = utc_now()
    mapped = 0
    continuity = 0
    evidence = 0
    with closing(open_main_db_readonly(main_db_path)) as main:
        mappings = main.execute(
            """
            SELECT ac.network_id,ac.contract_address,ac.is_primary,ac.identity_status,
                   a.asset_id,a.symbol,p.project_id,p.canonical_name,p.website_domain,p.official_repo
            FROM asset_contracts ac
            JOIN assets a ON a.asset_id=ac.asset_id
            JOIN projects p ON p.project_id=a.project_id
            WHERE COALESCE(ac.contract_address,'')!=''
            """
        ).fetchall()
        defillama = {}
        for row in main.execute(
            """
            SELECT matched_project_id,source_discovery_id,source_url,evidence_json,last_seen_at,
                   project_identity_status,attribution_confidence
            FROM source_discoveries
            WHERE source_id='discovery-defillama-protocols' AND status='active'
              AND COALESCE(matched_project_id,'')!=''
            """
        ):
            try:
                payload = json.loads(row[3] or "{}")
            except json.JSONDecodeError:
                payload = {}
            tvl = payload.get("tvlUsd")
            if row[5] == "verified" and row[6] == "high" and isinstance(tvl, (int, float)) and tvl > 0:
                defillama[row[0]] = {"evidenceId": row[1], "sourceUrl": row[2], "observedAt": row[4], "tvlUsd": tvl, "payload": payload}
    for row in mappings:
        network_id = row[0]
        address = normalize_address(network_id, row[1])
        candidate = connection.execute("SELECT candidate_id,gate0_pool_id,gate0_t0,dex_ids_json FROM candidates WHERE network_id=? AND token_address_normalized=?", (network_id, address)).fetchone()
        if not candidate:
            continue
        candidate_id = candidate[0]
        if candidate[1]:
            dex_ids = row_json(candidate, "dex_ids_json", [])
            connection.execute(
                """INSERT INTO candidate_pools(candidate_id,pool_id,dex_id,created_at,source_id,indexed_status)
                VALUES(?,?,?,?,?,'discovered') ON CONFLICT(candidate_id,pool_id) DO NOTHING""",
                (candidate_id, candidate[1], str(dex_ids[0] if dex_ids else ""), candidate[2], "gate0_accepted_pool"),
            )
        is_primary = bool(row[2])
        if not is_primary:
            continuity += 1
            connection.execute(
                """
                UPDATE candidates SET mapped_project_id=?,mapped_asset_id=?,canonical_name=?,symbol=?,website_domain=?,official_repo=?,identity_status=?,
                  continuity_status='known_continuation',continuity_reason='该合约属于已知资产的跨链、包装或连续部署，不以新池时间重置T0。',
                  relationship_class='D',relationship_reason='已知连续资产不进入新发项目池。',local_stage='continuity_excluded',local_reason='known_existing_asset_continuation',updated_at=?
                WHERE candidate_id=?
                """,
                (row[6], row[4], row[7], row[5], row[8] or "", row[9] or "", row[3] or "not_verified", now, candidate_id),
            )
            continue
        mapped += 1
        business = defillama.get(row[6])
        has_candidate_product = bool(row[9] or business)
        relationship = "C" if has_candidate_product and row[3] in {"verified", "market_matched"} else "D"
        reason = "项目与资产关系已有确定映射，但新/老项目关系不能由程序可靠区分。" if relationship == "C" else "当前只有代币或产品证据尚未达到冻结要求。"
        connection.execute(
            """
            UPDATE candidates SET mapped_project_id=?,mapped_asset_id=?,canonical_name=?,symbol=?,website_domain=?,official_repo=?,identity_status=?,
              t0_status=CASE WHEN ? IN ('verified','market_matched') THEN 'verified_in_supported_scope' ELSE t0_status END,
              continuity_status='candidate_asset',continuity_reason='',relationship_class=?,relationship_reason=?,
              local_stage='identity_mapped',local_reason='read_only_main_database_mapping',updated_at=?
            WHERE candidate_id=?
            """,
            (row[6], row[4], row[7], row[5], row[8] or "", row[9] or "", row[3] or "not_verified", row[3] or "not_verified", relationship, reason, now, candidate_id),
        )
        if row[9]:
            evidence_id = "c21-main-repo-" + hashlib.sha256(f"{network_id}|{address}|{row[9]}".encode()).hexdigest()[:20]
            connection.execute(
                """
                INSERT INTO product_evidence(evidence_id,candidate_id,evidence_type,status,identity_status,source_name,source_url,observed_at,payload_json,boundary_note)
                VALUES(?,?,'github','pending',?,'主库官方仓库映射',?,?,?,?)
                ON CONFLICT(evidence_id) DO UPDATE SET observed_at=excluded.observed_at,payload_json=excluded.payload_json
                """,
                (evidence_id, candidate_id, row[3] or "not_verified", row[9], now, json_text({"repositoryUrl": row[9]}), "仓库尚需自动核验非空、非归档、非Fork和自有提交。"),
            )
        if business:
            evidence_id = "c21-defillama-" + hashlib.sha256(f"{candidate_id}|{business['evidenceId']}".encode()).hexdigest()[:20]
            connection.execute(
                """
                INSERT INTO product_evidence(evidence_id,candidate_id,evidence_type,status,identity_status,source_name,source_url,observed_at,payload_json,boundary_note)
                VALUES(?,?,'business','qualifying','verified','DefiLlama结构化协议数据',?,?,?,?)
                ON CONFLICT(evidence_id) DO UPDATE SET status='qualifying',observed_at=excluded.observed_at,payload_json=excluded.payload_json
                """,
                (evidence_id, candidate_id, business["sourceUrl"], business["observedAt"] or now, json_text({"metric": "tvlUsd", "value": business["tvlUsd"], "definition": "DefiLlama协议登记TVL"}), "只证明结构化协议数据与项目身份已映射，不自动证明投资价值。"),
            )
            evidence += 1
    connection.execute(
        """
        UPDATE candidates SET relationship_class='C',relationship_reason='存在合格产品证据，但项目新旧关系不做猜测。',updated_at=?
        WHERE continuity_status='candidate_asset' AND identity_status IN ('verified','market_matched')
          AND EXISTS(SELECT 1 FROM product_evidence pe WHERE pe.candidate_id=candidates.candidate_id AND pe.status='qualifying')
        """,
        (now,),
    )
    set_source_health(connection, "convexity_main_readonly", "project_asset_mapping", "success", plain_reason="已只读复用本项目主库中的确定性项目与资产映射。", affected_object_count=mapped + continuity)
    connection.commit()
    return {"mappedPrimaryAssets": mapped, "continuityExcluded": continuity, "qualifyingBusinessEvidence": evidence}


def import_main_observations(connection, main_db_path=DEFAULT_MAIN_DB_PATH):
    now = utc_now()
    imported_market = 0
    imported_risk = 0
    with closing(open_main_db_readonly(main_db_path)) as main:
        market_rows = main.execute(
            """
            SELECT nd.network_id,nd.contract_address,nd.last_seen_at,nd.price_usd,nd.liquidity_usd,
                   nd.volume_24h_usd,nd.market_cap_usd,nd.recent_buys_24h,nd.recent_sells_24h,
                   nd.exit_notional_usd,nd.estimated_exit_slippage_pct,nd.pair_match_status,
                   nd.sell_path_status,nd.contract_exists_status,nd.evidence_json
            FROM network_discoveries nd
            WHERE COALESCE(nd.contract_address,'')!=''
            """
        ).fetchall()
        risk_rows = main.execute(
            """
            SELECT ac.network_id,ac.contract_address,tc.checked_at,tc.overall_status,tc.sell_path_status,
                   tc.risk_flags_json,tc.evidence_json,cr.freeze_risk,cr.transfer_tax_risk,cr.overall_risk
            FROM asset_contracts ac
            LEFT JOIN tradeability_checks tc ON tc.asset_contract_id=ac.asset_contract_id
            LEFT JOIN assets a ON a.asset_id=ac.asset_id
            LEFT JOIN contract_risks cr ON cr.asset_id=a.asset_id
            WHERE COALESCE(ac.contract_address,'')!=''
            """
        ).fetchall()
    for row in market_rows:
        address = normalize_address(row[0], row[1])
        candidate = connection.execute("SELECT candidate_id,gate0_pool_id FROM candidates WHERE network_id=? AND token_address_normalized=?", (row[0], address)).fetchone()
        if not candidate:
            continue
        observed_at = row[2] or now
        window_id = "legacy:" + str(observed_at)[:13]
        volume = row[5]
        liquidity = row[4]
        ratio = volume / liquidity if isinstance(volume, (int, float)) and isinstance(liquidity, (int, float)) and liquidity > 0 else None
        observation_id = "c21-main-market-" + hashlib.sha256(f"{candidate[0]}|{window_id}".encode()).hexdigest()[:20]
        connection.execute(
            """
            INSERT INTO market_observations(
              observation_id,candidate_id,window_id,source_name,source_status,observed_at,pair_address,
              token_side,liquidity_usd,market_cap_usd,volume_usd,transaction_count,observed_buys,
              observed_sells,volume_liquidity_ratio,price_usd,standard_sell_notional_usd,
              standard_sell_quote_state,standard_sell_quote_loss_pct,payload_json
            ) VALUES(?,?,?,'本项目主库只读市场事实','success',?,?,'matched',?,?,?,?,?,?,?,?,?,'no_data',NULL,?)
            ON CONFLICT(candidate_id,window_id,source_name) DO UPDATE SET
              observed_at=excluded.observed_at,liquidity_usd=excluded.liquidity_usd,volume_usd=excluded.volume_usd,
              transaction_count=excluded.transaction_count,observed_buys=excluded.observed_buys,
              observed_sells=excluded.observed_sells,volume_liquidity_ratio=excluded.volume_liquidity_ratio,
              price_usd=excluded.price_usd,payload_json=excluded.payload_json
            """,
            (observation_id, candidate[0], window_id, observed_at, candidate[1] or "", liquidity, row[6], volume, (row[7] or 0) + (row[8] or 0), row[7], row[8], ratio, row[3], row[9], json_text({"pairMatchStatus": row[11], "sellPathStatus": row[12], "contractExistsStatus": row[13], "estimatedPoolDepthSlippagePct": row[10], "boundary": "standard_sell_quote_not_replaced_by_pool_depth_estimate"})),
        )
        imported_market += 1
    for row in risk_rows:
        address = normalize_address(row[0], row[1])
        candidate = connection.execute("SELECT candidate_id FROM candidates WHERE network_id=? AND token_address_normalized=?", (row[0], address)).fetchone()
        if not candidate or not row[2]:
            continue
        try:
            flags = json.loads(row[5] or "[]")
        except json.JSONDecodeError:
            flags = []
        hard_codes = {"honeypot", "cannot_sell", "blacklist", "transfer_blocked", "frozen"}
        codes = [str(item.get("code") or "") for item in flags if isinstance(item, dict)]
        hard_block = row[4] in {"failed", "blocked", "untradeable"} or bool(hard_codes.intersection(codes)) or row[7] == "high"
        severe = hard_block or any(item in {"buy_or_sell_tax_ge_20", "liquidity_drop_ge_80"} for item in codes)
        observation_id = "c21-main-risk-" + hashlib.sha256(f"{candidate[0]}|{row[2]}|{','.join(codes)}".encode()).hexdigest()[:20]
        connection.execute(
            """
            INSERT OR REPLACE INTO risk_observations(
              observation_id,candidate_id,source_name,source_status,observed_at,hard_trade_block,
              severe_anomaly,reason_codes_json,payload_json
            ) VALUES(?,?,'本项目主库只读风险事实','success',?,?,?,?,?)
            """,
            (observation_id, candidate[0], row[2], int(hard_block), int(severe), json_text(codes), json_text({"overallStatus": row[3], "sellPathStatus": row[4], "freezeRisk": row[7], "transferTaxRisk": row[8], "overallRisk": row[9]})),
        )
        imported_risk += 1
    set_source_health(connection, "convexity_main_readonly", "market_and_risk", "success", plain_reason="已只读导入主库现有市场与显性风险事实；池深估算未冒充100美元标准卖出报价。", affected_object_count=imported_market + imported_risk)
    connection.commit()
    return {"marketObservations": imported_market, "riskObservations": imported_risk}


def row_json(row, key, fallback):
    try:
        return json.loads(row[key] or json_text(fallback)) if row else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


def latest_market(connection, candidate_id):
    row = connection.execute("SELECT * FROM market_observations WHERE candidate_id=? ORDER BY observed_at DESC LIMIT 1", (candidate_id,)).fetchone()
    if not row:
        return None
    return {
        "sourceName": row["source_name"], "sourceStatus": row["source_status"], "observedAt": row["observed_at"],
        "pairAddress": row["pair_address"], "pairCreatedAt": row["pair_created_at"], "tokenSide": row["token_side"],
        "liquidityUsd": row["liquidity_usd"], "fdvUsd": row["fdv_usd"], "marketCapUsd": row["market_cap_usd"],
        "volumeUsd": row["volume_usd"], "transactionCount": row["transaction_count"], "observedBuys": row["observed_buys"],
        "observedSells": row["observed_sells"], "volumeLiquidityRatio": row["volume_liquidity_ratio"], "priceUsd": row["price_usd"],
        "standardSellNotionalUsd": row["standard_sell_notional_usd"], "standardSellQuoteState": row["standard_sell_quote_state"],
        "standardSellQuoteLossPct": row["standard_sell_quote_loss_pct"], "evidenceIds": [row["observation_id"]],
        **row_json(row, "payload_json", {}),
    }


def candidate_input(connection, row, as_of):
    evidence_rows = connection.execute("SELECT * FROM product_evidence WHERE candidate_id=? ORDER BY observed_at DESC", (row["candidate_id"],)).fetchall()
    records = []
    for item in evidence_rows:
        payload = row_json(item, "payload_json", {})
        records.append({
            "evidenceId": item["evidence_id"], "evidenceType": item["evidence_type"], "status": item["status"],
            "identityStatus": item["identity_status"], "sourceName": item["source_name"], "sourceUrl": item["source_url"],
            "observedAt": item["observed_at"], "boundaryNote": item["boundary_note"], **payload,
        })
    health = connection.execute("SELECT * FROM source_health WHERE status IN ('quota_limited','source_failure','configuration_missing','program_failure') ORDER BY updated_at DESC").fetchall()
    relevant_scopes = {"all", row["network_id"], str(row["candidate_id"]), "market_and_risk", row["official_repo"]}
    affected = [
        item for item in health
        if item["source_id"] != "coingecko_new_pools" and item["scope_key"] in relevant_scopes
    ]
    critical = bool(affected)
    recovery_times = []
    for item in affected:
        cursor = connection.execute(
            "SELECT next_retry_at FROM source_cursors WHERE source_id=? AND scope_key=? AND next_retry_at IS NOT NULL ORDER BY updated_at DESC LIMIT 1",
            (item["source_id"], item["scope_key"]),
        ).fetchone()
        if cursor and cursor[0]:
            recovery_times.append(cursor[0])
    latest_success = connection.execute("SELECT MAX(last_success_at) FROM source_health WHERE last_success_at IS NOT NULL").fetchone()[0]
    history = connection.execute(
        """
        SELECT COUNT(DISTINCT date(observed_at)) AS valid_days,MIN(observed_at) AS first_at,MAX(observed_at) AS last_at,
               GROUP_CONCAT(DISTINCT source_name) AS sources
        FROM market_observations WHERE candidate_id=? AND source_status='success' AND datetime(observed_at)>=datetime(?)
        """,
        (row["candidate_id"], row["effective_t0"]),
    ).fetchone()
    market_now = latest_market(connection, row["candidate_id"])
    return {
        "candidateId": row["candidate_id"], "networkId": row["network_id"], "tokenAddress": row["token_address"],
        "effectiveT0": row["effective_t0"], "t0Status": row["t0_status"], "relationshipClass": row["relationship_class"],
        "identityStatus": row["identity_status"], "continuityStatus": row["continuity_status"], "continuityReason": row["continuity_reason"],
        "t0EvidenceIds": [f"{row['source_run_id']}:{row['gate0_pool_id'] or row['token_address']}"],
        "t0SourceNames": ["Gate 0已验收DEX创建事件" if row["source_run_id"] == DEFAULT_GATE0_RUN_ID else "C2.1日常新池增量发现"],
        "identityEvidenceIds": [item for item in (row["mapped_asset_id"], row["gate0_pool_id"]) if item],
        "identitySourceNames": ["本项目主库只读项目资产映射" if row["mapped_asset_id"] else "市场项目资料与代码仓库确定链路", "DEX交易池方向事实"],
        "productEvidenceRecords": records, "criticalDataInterrupted": critical,
        "independentSourceTypes": sorted({"market_pool_data" if market_now else "", "sell_quote_or_verified_route" if market_now and market_now.get("standardSellQuoteState") == "success" else "", "project_identity_registry" if row["mapped_project_id"] else "", "structured_business" if any(i["evidenceType"] == "business" and i["status"] == "qualifying" for i in records) else "", "official_code" if any(i["evidenceType"] == "github" and i["status"] == "qualifying" for i in records) else "", "direct_chain_historical_supply" if latest_pool_window(connection, row["candidate_id"]) else ""} - {""}),
        "validHistoryDays": int(history["valid_days"] or 0),
        "backfilledDays": int(history["valid_days"] or 0),
        "historySources": [item for item in str(history["sources"] or "").split(",") if item],
        "historyLastSuccessfulAt": history["last_at"] or latest_success,
        "sourceImpact": {
            "status": "interrupted" if critical else "healthy", "affectedProjectCount": 1 if critical else 0,
            "affectedChains": [row["network_id"]] if critical else [],
            "affectedFields": sorted({field for item in affected for field in SOURCE_AFFECTED_FIELDS.get(item["source_id"], ["关键判断字段"])}), "lastSuccessfulAt": latest_success,
            "reasonCode": affected[0]["reason_code"] if affected else "", "plainReason": affected[0]["plain_reason"] if affected else "当前关键来源可用。",
            "expectedRecoveryAt": min(recovery_times) if recovery_times else affected[0]["quota_reset_at"] if affected else None,
        },
    }


def latest_supply_input(connection, candidate_id, market, thresholds=None):
    rows = connection.execute(
        "SELECT * FROM supply_observations WHERE candidate_id=? AND source_status='success' ORDER BY observed_at DESC LIMIT 2",
        (candidate_id,),
    ).fetchall()
    if len(rows) < 2:
        return {"historyState": "current_only" if rows else "no_data", "unitScaleStable": None, "evidenceIds": [row["observation_id"] for row in rows]}
    current, previous = rows[0], rows[1]
    previous_supply = number(previous["supply_raw"])
    current_supply = number(current["supply_raw"])
    supply_change = (current_supply / previous_supply - 1) * 100 if previous_supply and current_supply is not None else None
    market_activity = number((market or {}).get("volumeUsd"))
    market_p50 = number((thresholds or {}).get("volumeP50"))
    return {
        "historyState": "success", "unitScaleStable": previous["decimals"] == current["decimals"] or previous["decimals"] is None or current["decimals"] is None,
        "previousTop10SharePct": previous["top10_share_pct"], "currentTop10SharePct": current["top10_share_pct"],
        "previousHolderHhi": previous["holder_hhi"], "currentHolderHhi": current["holder_hhi"],
        "supplyChangePct": supply_change, "marketActivityVsP50": market_activity / market_p50 if market_activity is not None and market_p50 else None,
        "evidenceIds": [previous["observation_id"], current["observation_id"]],
        "supportingMetrics": [{"label": "Top10持仓变化", "value": None if previous["top10_share_pct"] is None or current["top10_share_pct"] is None else current["top10_share_pct"] - previous["top10_share_pct"], "unit": "百分点"}, {"label": "供应变化", "value": supply_change, "unit": "%"}],
    }


def latest_pool_window(connection, candidate_id):
    row = connection.execute("SELECT * FROM pool_window_observations WHERE candidate_id=? ORDER BY observed_at DESC LIMIT 1", (candidate_id,)).fetchone()
    if not row:
        return None
    payload = row_json(row, "payload_json", {})
    return {
        "sourceStatus": row["source_status"], "indexedPoolCount": row["indexed_pool_count"], "ohlcvSuccessCount": row["ohlcv_success_count"],
        "unindexedDiscoveredPoolCount": row["unindexed_discovered_pool_count"], "previousAverageVolumeUsd": row["previous_average_volume_usd"],
        "currentAverageVolumeUsd": row["current_average_volume_usd"], "previousWeightedMedianPriceUsd": row["previous_weighted_median_price_usd"],
        "currentWeightedMedianPriceUsd": row["current_weighted_median_price_usd"], "activityLogChange": row["activity_log_change"],
        "valuationLogChange": row["valuation_log_change"], "relativeExpansion": row["relative_expansion"], "riskAdjustedSurplus": row["risk_adjusted_surplus"],
        "evidenceIds": [row["observation_id"]], **payload,
    }


def percentile(values, fraction):
    clean = sorted(value for value in (number(item) for item in values) if value is not None)
    if not clean:
        return None
    position = (len(clean) - 1) * fraction
    lower = int(position)
    upper = min(len(clean) - 1, lower + 1)
    weight = position - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def build_cohort_catalog(connection, as_of):
    cutoff = (parse_utc(as_of) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    rows = connection.execute(
        """
        SELECT c.network_id,c.effective_t0,m.liquidity_usd,m.volume_usd,m.transaction_count,m.volume_liquidity_ratio
        FROM market_observations m JOIN candidates c ON c.candidate_id=m.candidate_id
        WHERE m.observation_id=(SELECT m2.observation_id FROM market_observations m2 WHERE m2.candidate_id=m.candidate_id AND m2.source_status='success' ORDER BY m2.observed_at DESC LIMIT 1)
          AND m.observed_at>=? AND c.continuity_status='candidate_asset'
        """,
        (cutoff,),
    ).fetchall()
    expansion = [item[0] for item in connection.execute("SELECT relative_expansion FROM pool_window_observations WHERE source_status='success' AND observed_at>=? AND relative_expansion IS NOT NULL", (cutoff,))]
    return {"rows": rows, "expansion": expansion}


def cohort_context(connection, candidate, as_of, rules, catalog=None):
    band = age_band(age_days(candidate["effective_t0"], as_of), rules)
    if not band:
        return None
    catalog = catalog or build_cohort_catalog(connection, as_of)
    rows = catalog["rows"]
    same_band = [item for item in rows if age_band(age_days(item["effective_t0"], as_of), rules) == band]
    primary = [item for item in same_band if item["network_id"] == candidate["network_id"]]
    selected = primary if len(primary) >= int(rules["cohortPercentiles"]["primary"]["minimumValidObjects"]) else same_band if len(same_band) >= int(rules["cohortPercentiles"]["secondary"]["minimumValidObjects"]) else []
    if not selected:
        return None
    scope = "same_chain_same_age_band_rolling_30_days" if selected is primary else "all_supported_chains_same_age_band_rolling_30_days"
    metrics = {
        "liquidityP60": percentile([item["liquidity_usd"] for item in selected], .60),
        "volumeP60": percentile([item["volume_usd"] for item in selected], .60),
        "transactionsP60": percentile([item["transaction_count"] for item in selected], .60),
        "ratioP60": percentile([item["volume_liquidity_ratio"] for item in selected], .60),
        "volumeP70": percentile([item["volume_usd"] for item in selected], .70),
        "transactionsP70": percentile([item["transaction_count"] for item in selected], .70),
        "ratioP70": percentile([item["volume_liquidity_ratio"] for item in selected], .70),
        "volumeP50": percentile([item["volume_usd"] for item in selected], .50),
    }
    metrics["relativeExpansionP60"] = percentile(catalog["expansion"], .60)
    digest = hashlib.sha256(json_text({"scope": scope, "band": band, "sample": len(selected), **metrics}).encode()).hexdigest()[:16]
    return {"snapshotId": f"cohort:{band}:{digest}", "scope": scope, "sampleSize": len(selected), **metrics}


def previous_evaluation(connection, candidate_id):
    row = connection.execute("SELECT * FROM evaluations WHERE candidate_id=? AND is_current=1 ORDER BY evaluated_at DESC LIMIT 1", (candidate_id,)).fetchone()
    if not row:
        return None
    return {
        "evaluationWindowId": row["evaluation_window_id"],
        "frontEligible": row["hard_gate_status"] in {"pass", "stale"},
        "hardGate": row_json(row, "hard_gate_json", {}),
        "displayState": {"code": row["display_state"]},
        "dataConfidence": row_json(row, "confidence_json", {}),
        "factorDirections": row_json(row, "factor_directions_json", []),
        "consecutiveCompletedMisses": row["consecutive_completed_misses"],
    }


def product_usage_input(connection, candidate_id):
    row = connection.execute(
        "SELECT * FROM product_evidence WHERE candidate_id=? AND evidence_type='product_usage' AND status='qualifying' ORDER BY observed_at DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    if not row:
        return None
    return {"identityMappingStatus": row["identity_status"], "evidenceIds": [row["evidence_id"]], **row_json(row, "payload_json", {})}


def record_material_changes(connection, candidate_id, previous, result):
    if not previous:
        changes = [("front_eligibility", "未进入判断", "通过前台观察门槛", "首次形成可复算资格结果。")] if result["frontEligible"] else []
    else:
        changes = []
        previous_eligible = bool(previous.get("frontEligible"))
        if previous_eligible != bool(result["frontEligible"]):
            changes.append(("front_eligibility", "通过前台观察门槛" if previous_eligible else "后台", "通过前台观察门槛" if result["frontEligible"] else "退出前台", "前台资格发生变化，直接影响用户能否看到该项目。"))
        previous_state = (previous.get("displayState") or {}).get("code")
        current_state = result["displayState"]["code"]
        if previous_state != current_state:
            changes.append(("display_state", previous_state or "未判断", current_state, "项目所属观察状态发生变化，但不等同于投资动作。"))
        previous_confidence = (previous.get("dataConfidence") or {}).get("level")
        current_confidence = result["dataConfidence"]["level"]
        if previous_confidence != current_confidence:
            changes.append(("data_confidence", previous_confidence or "未判断", current_confidence, "数据可信度变化会影响当前结论能否完整更新。"))
    for change_type, previous_value, current_value, why in changes:
        changed_at = result["evaluatedAt"]
        change_id = "c21-change-" + hashlib.sha256(f"{candidate_id}|{change_type}|{previous_value}|{current_value}|{result['evaluationWindowId']}".encode()).hexdigest()[:24]
        connection.execute(
            """
            INSERT OR IGNORE INTO material_changes(change_id,candidate_id,changed_at,change_type,previous_value,current_value,why_it_matters,source_cutoff_at,evidence_json)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (change_id, candidate_id, changed_at, change_type, previous_value, current_value, why, result["sourceImpact"].get("lastSuccessfulAt") or changed_at, json_text(result["displayState"].get("triggerEvidenceIds") or [])),
        )


def evaluate_all(connection, as_of=None):
    as_of = as_of or utc_now()
    rules, rule_hash = load_rules()
    rows = connection.execute("SELECT * FROM candidates WHERE continuity_status='candidate_asset' AND (mapped_project_id!='' OR relationship_class IN ('A','B','C'))").fetchall()
    catalog = build_cohort_catalog(connection, as_of)
    counts = Counter()
    for row in rows:
        market = latest_market(connection, row["candidate_id"])
        risks = []
        for item in connection.execute("SELECT * FROM risk_observations WHERE candidate_id=? ORDER BY observed_at DESC LIMIT 4", (row["candidate_id"],)):
            risks.append({"sourceName": item["source_name"], "sourceStatus": item["source_status"], "observedAt": item["observed_at"], "hardTradeBlock": bool(item["hard_trade_block"]), "severeAnomaly": bool(item["severe_anomaly"]), "reasonCodes": row_json(item, "reason_codes_json", []), "evidenceIds": [item["observation_id"]]})
        input_row = candidate_input(connection, row, as_of)
        previous = previous_evaluation(connection, row["candidate_id"])
        cohort = cohort_context(connection, row, as_of, rules, catalog)
        usage = product_usage_input(connection, row["candidate_id"])
        supply = latest_supply_input(connection, row["candidate_id"], market, cohort)
        pool_window = latest_pool_window(connection, row["candidate_id"])
        result = evaluate_candidate(input_row, market=market, risks=risks, product_usage=usage, supply=supply, pool_window=pool_window, cohort=cohort, previous=previous, as_of=as_of, rules=rules, rule_hash=rule_hash)
        record_material_changes(connection, row["candidate_id"], previous, result)
        evaluation_id = "c21-eval-" + hashlib.sha256(f"{row['candidate_id']}|{result['evaluationWindowId']}|{rules['ruleVersion']}".encode()).hexdigest()[:24]
        connection.execute("UPDATE evaluations SET is_current=0 WHERE candidate_id=?", (row["candidate_id"],))
        connection.execute(
            """
            INSERT INTO evaluations(
              evaluation_id,candidate_id,evaluation_window_id,evaluated_at,rule_version,rule_config_hash,
              cohort_snapshot_id,cohort_scope,cohort_sample_size,age_days,age_band,hard_gate_status,
              hard_gate_json,display_state,display_reason,paths_json,factor_directions_json,confidence_json,
              threshold_context_json,market_snapshot_json,source_impact_json,sort_score,sort_reason,
              consecutive_completed_misses,formed_at,invalidated_at,is_current
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
            ON CONFLICT(candidate_id,evaluation_window_id,rule_version) DO UPDATE SET
              evaluated_at=excluded.evaluated_at,hard_gate_status=excluded.hard_gate_status,
              hard_gate_json=excluded.hard_gate_json,display_state=excluded.display_state,
              display_reason=excluded.display_reason,paths_json=excluded.paths_json,
              factor_directions_json=excluded.factor_directions_json,confidence_json=excluded.confidence_json,
              threshold_context_json=excluded.threshold_context_json,market_snapshot_json=excluded.market_snapshot_json,
              source_impact_json=excluded.source_impact_json,sort_score=excluded.sort_score,
              sort_reason=excluded.sort_reason,consecutive_completed_misses=excluded.consecutive_completed_misses,
              formed_at=COALESCE(evaluations.formed_at,excluded.formed_at),invalidated_at=excluded.invalidated_at,is_current=1
            """,
            (
                evaluation_id, row["candidate_id"], result["evaluationWindowId"], result["evaluatedAt"], result["ruleVersion"], result["ruleConfigHash"],
                result["cohortSnapshotId"], result["cohortScope"], result["cohortSampleSize"], result["ageDays"] if result["ageDays"] is not None else -9999,
                result["ageBand"], result["hardGate"]["status"], json_text(result["hardGate"]), result["displayState"]["code"], result["displayState"]["reason"],
                json_text(result["evidencePaths"]), json_text(result["factorDirections"]), json_text(result["dataConfidence"]), json_text(result["thresholdContext"]),
                json_text(result["marketSnapshot"]), json_text(result["sourceImpact"]), result["sortScore"], result["sortReason"], result["consecutiveCompletedMisses"],
                result["formedAt"], result["invalidatedAt"],
            ),
        )
        connection.execute("UPDATE candidates SET last_evaluated_at=?,updated_at=? WHERE candidate_id=?", (as_of, utc_now(), row["candidate_id"]))
        counts[result["hardGate"]["status"]] += 1
        counts[result["displayState"]["code"]] += 1
    connection.commit()
    return {"evaluated": len(rows), "counts": dict(counts), "ruleConfigHash": rule_hash}


def mask_contract(value):
    text = str(value or "")
    return text if len(text) <= 16 else f"{text[:8]}…{text[-6:]}"


def supported_scope():
    latest = load_json(PROJECT_ROOT / "runtime" / "gate0-shadow" / "backfill" / "latest.json", {})
    supported = (latest.get("schemaCoverage") or {}).get("supported") or []
    return {
        "chains": ["Ethereum", "Solana", "Base", "Arbitrum", "BNB Chain", "Robinhood Chain"],
        "dexProtocols": supported,
        "discoverySources": ["Gate 0已验收登记DEX创建事件", "日常增量发现"],
        "historySources": ["DexScreener", "GeckoTerminal", "已接入链上历史来源"],
        "unsupportedNotes": (latest.get("schemaCoverage") or {}).get("unsupported") or [],
    }


def front_item(connection, row):
    hard_gate = row_json(row, "hard_gate_json", {})
    paths = row_json(row, "paths_json", [])
    factors = row_json(row, "factor_directions_json", [])
    confidence = row_json(row, "confidence_json", {})
    threshold = row_json(row, "threshold_context_json", {})
    market = row_json(row, "market_snapshot_json", {})
    source_impact = row_json(row, "source_impact_json", {})
    evidence_rows = connection.execute("SELECT * FROM product_evidence WHERE candidate_id=?", (row["candidate_id"],)).fetchall()
    candidate = {
        "productEvidenceRecords": [{"evidenceId": item["evidence_id"], "evidenceType": item["evidence_type"], "status": item["status"], "identityStatus": item["identity_status"], "sourceName": item["source_name"], "sourceUrl": item["source_url"], "observedAt": item["observed_at"], **row_json(item, "payload_json", {})} for item in evidence_rows]
    }
    product = product_evidence_summary(candidate["productEvidenceRecords"])
    expected = max(0, row["age_days"] + 1)
    history_row = connection.execute(
        "SELECT COUNT(DISTINCT date(observed_at)) AS valid_days,MAX(observed_at) AS last_at,GROUP_CONCAT(DISTINCT source_name) AS sources FROM market_observations WHERE candidate_id=? AND source_status='success' AND datetime(observed_at)>=datetime(?)",
        (row["candidate_id"], row["effective_t0"]),
    ).fetchone()
    valid_days = min(expected, int(history_row["valid_days"] or 0))
    observation = {
        "t0": row["effective_t0"], "ageDays": row["age_days"], "expectedHistoryDays": expected,
        "backfilledDays": valid_days, "validHistoryDays": valid_days,
        "gapDays": max(0, expected - valid_days), "coverageRatio": min(1, valid_days / expected) if expected else 0,
        "effectiveWindowDays": min(expected, 14), "historyStage": "launch_0_2" if row["age_days"] <= 2 else "early_3_6" if row["age_days"] <= 6 else "forming_7_13" if row["age_days"] <= 13 else "full_14_90",
        "sources": [item for item in str(history_row["sources"] or "").split(",") if item], "lastSuccessfulAt": history_row["last_at"],
    }
    relationship_label = {"A": "新项目新币", "B": "老项目新资产", "C": "项目关系未确认"}.get(row["relationship_class"], "只有代币")
    change = connection.execute("SELECT * FROM material_changes WHERE candidate_id=? ORDER BY changed_at DESC LIMIT 1", (row["candidate_id"],)).fetchone()
    return {
        "projectId": f"c21-{row['candidate_id']}", "assetId": row["mapped_asset_id"] or f"asset-{row['candidate_id']}",
        "canonicalName": row["canonical_name"] or row["symbol"] or "未命名项目", "symbol": row["symbol"],
        "chainId": row["network_id"], "contractAddressMasked": mask_contract(row["token_address"]),
        "detailUrl": f"project-detail.html?id=c21-{row['candidate_id']}",
        "t0": {"value": row["effective_t0"], "label": "T0（目前查到的最早公开流通证据）", "status": row["t0_status"], "scope": row_json(row, "t0_scope_json", {})},
        "ageDays": row["age_days"], "ageBand": row["age_band"], "firstSeenAt": row["first_seen_at"],
        "discoveryLagDays": max(0, int((parse_utc(row["first_seen_at"]) - parse_utc(row["effective_t0"])).total_seconds() // 86400)) if parse_utc(row["first_seen_at"]) and parse_utc(row["effective_t0"]) else None,
        "relationshipClass": row["relationship_class"], "relationshipLabel": relationship_label,
        "hardGate": hard_gate, "productEvidence": product, "observationHistory": observation,
        "displayState": {"code": row["display_state"], "label": {"data_limited": "数据受限", "convexity_clue": "凸性线索", "active_project": "活跃项目", "early_observation": "新发观察", "continuous_observation": "持续观察"}.get(row["display_state"], row["display_state"]), "reason": row["display_reason"], "since": row["evaluated_at"], "triggerEvidenceIds": [item for path in paths if path.get("status") == "formed" for item in path.get("evidenceIds", [])], "nextTransitionConditions": ["下一完整窗口按冻结规则重新计算"], "invalidationConditions": ["硬门槛失败、第91天或已确认严重异常立即退出"]},
        "evidencePaths": paths, "factorDirections": factors, "dataConfidence": confidence,
        "thresholdContext": threshold, "marketSnapshot": market,
        "riskSummary": {"status": "no_confirmed_hard_block" if next((c for c in hard_gate.get("checks", []) if c.get("code") == "no_hard_trade_block"), {}).get("status") == "pass" else "pending", "plainReason": next((c.get("reason") for c in hard_gate.get("checks", []) if c.get("code") == "no_hard_trade_block"), "风险事实待更新")},
        "latestMaterialChange": ({"changeId": change["change_id"], "changedAt": change["changed_at"], "changeType": change["change_type"], "previousValue": change["previous_value"], "currentValue": change["current_value"], "whyItMatters": change["why_it_matters"], "sourceCutoffAt": change["source_cutoff_at"]} if change else None), "sourceImpact": source_impact, "sortReason": row["sort_reason"], "sortScore": row["sort_score"],
    }


def build_snapshots(connection, front_path=DEFAULT_FRONT_SNAPSHOT, backend_path=DEFAULT_BACKEND_SNAPSHOT):
    generated_at = utc_now()
    build_id = "c21-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    rows = connection.execute(
        """
        SELECT e.*,c.network_id,c.token_address,c.effective_t0,c.t0_status,c.t0_scope_json,
               c.first_seen_at,c.relationship_class,c.mapped_asset_id,c.canonical_name,c.symbol
        FROM evaluations e JOIN candidates c ON c.candidate_id=e.candidate_id
        WHERE e.is_current=1 AND e.hard_gate_status IN ('pass','stale')
        ORDER BY e.sort_score DESC,e.evaluated_at DESC,c.candidate_id
        """
    ).fetchall()
    items = [front_item(connection, row) for row in rows]
    hard_gate_passed = len(items)
    front_visible = len(items)
    if front_visible != hard_gate_passed:
        raise RuntimeError("前台资格对账失败：frontVisibleCount != hardGatePassedCount")
    counts = connection.execute(
        """
        SELECT COUNT(*) AS discovered,
          SUM(CASE WHEN julianday(?) - julianday(effective_t0) BETWEEN 0 AND 90 THEN 1 ELSE 0 END) AS within90,
          SUM(CASE WHEN t0_status='verified_in_supported_scope' THEN 1 ELSE 0 END) AS t0verified
        FROM candidates
        """,
        (generated_at,),
    ).fetchone()
    state_counts = Counter(item["displayState"]["code"] for item in items)
    blocker_counts = Counter()
    for evaluation in connection.execute("SELECT hard_gate_json FROM evaluations WHERE is_current=1 AND hard_gate_status!='pass'"):
        for item in row_json(evaluation, "hard_gate_json", {}).get("checks", []):
            if item.get("status") in {"fail", "pending", "unsupported"}:
                blocker_counts[item.get("code") or "unknown"] += 1
    health_rows = connection.execute("SELECT * FROM source_health ORDER BY updated_at DESC").fetchall()
    affected = [row for row in health_rows if row["status"] in {"quota_limited", "source_failure", "configuration_missing", "program_failure"}]
    affected_items = [item for item in items if item["sourceImpact"]["status"] != "healthy"]
    coverage_affected = [row for row in affected if row["source_id"] == "coingecko_new_pools"]
    recovery_times = [item["sourceImpact"].get("expectedRecoveryAt") for item in affected_items if item["sourceImpact"].get("expectedRecoveryAt")]
    if not recovery_times:
        recovery_times = [row[0] for row in connection.execute("SELECT next_retry_at FROM source_cursors WHERE next_retry_at IS NOT NULL AND status IN ('source_failure','quota_limited')") if row[0]]
    source_impact = {
        "status": "interrupted" if affected_items else "degraded" if coverage_affected else "healthy",
        "affectedProjectCount": len(affected_items),
        "affectedChains": sorted({chain for item in affected_items for chain in item["sourceImpact"].get("affectedChains", [])} | {row["scope_key"] for row in coverage_affected if row["scope_key"].endswith("-mainnet")}),
        "affectedFields": sorted({field for item in affected_items for field in item["sourceImpact"].get("affectedFields", [])} | {field for row in coverage_affected for field in SOURCE_AFFECTED_FIELDS["coingecko_new_pools"]}),
        "lastSuccessfulAt": max((row["last_success_at"] for row in health_rows if row["last_success_at"]), default=None),
        "reasonCode": affected_items[0]["sourceImpact"].get("reasonCode", "") if affected_items else coverage_affected[0]["reason_code"] if coverage_affected else "",
        "plainReason": affected_items[0]["sourceImpact"].get("plainReason", "") if affected_items else coverage_affected[0]["plain_reason"] if coverage_affected else "当前关键来源可用。",
        "expectedRecoveryAt": min(recovery_times) if recovery_times else None,
    }
    rules, rule_hash = load_rules()
    change_rows = connection.execute(
        """
        SELECT mc.*,c.canonical_name,c.symbol,c.network_id FROM material_changes mc
        JOIN candidates c ON c.candidate_id=mc.candidate_id
        WHERE datetime(mc.changed_at)>=datetime(?,'-30 days')
          AND (mc.change_type!='front_eligibility' OR mc.previous_value LIKE '%通过前台%' OR mc.current_value LIKE '%通过前台%')
        ORDER BY mc.changed_at DESC LIMIT 500
        """,
        (generated_at,),
    ).fetchall()
    material_changes = [{"changeId": row["change_id"], "projectId": f"c21-{row['candidate_id']}", "canonicalName": row["canonical_name"] or row["symbol"] or "未命名项目", "symbol": row["symbol"], "chainId": row["network_id"], "changedAt": row["changed_at"], "changeType": row["change_type"], "previousValue": row["previous_value"], "currentValue": row["current_value"], "whyItMatters": row["why_it_matters"], "sourceCutoffAt": row["source_cutoff_at"], "detailUrl": f"project-detail.html?id=c21-{row['candidate_id']}"} for row in change_rows]
    front = {
        "schemaVersion": "c2.1-front-snapshot-v1", "buildId": build_id, "generatedAt": generated_at,
        "sourceCutoffAt": source_impact["lastSuccessfulAt"] or generated_at, "modelVersion": "deterministic-empirical-bayes-v1",
        "ruleVersion": rules["ruleVersion"], "ruleConfigHash": rule_hash, "supportedScope": supported_scope(),
        "coverageSummary": {"discoveredCount": int(counts["discovered"] or 0), "suspectedWithin90DaysCount": int(counts["within90"] or 0), "t0VerifiedCount": int(counts["t0verified"] or 0), "hardGatePassedCount": hard_gate_passed, "frontVisibleCount": front_visible, "backfillCompleteCount": sum(item["observationHistory"]["gapDays"] == 0 for item in items), "backfillGapCount": sum(item["observationHistory"]["gapDays"] > 0 for item in items), "added24hCount": sum(parse_utc(item["firstSeenAt"]) and parse_utc(generated_at) - parse_utc(item["firstSeenAt"]) <= timedelta(hours=24) for item in items)},
        "sourceImpactSummary": source_impact, "statusCounts": {code: state_counts.get(code, 0) for code in ("data_limited", "convexity_clue", "active_project", "early_observation", "continuous_observation")},
        "hardGateCounts": dict(Counter(row["hard_gate_status"] for row in connection.execute("SELECT hard_gate_status FROM evaluations WHERE is_current=1"))),
        "blockerCounts": dict(blocker_counts), "materialChanges": material_changes, "items": items,
    }
    runs = [dict(row) for row in connection.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 30")]
    cursors = [{**dict(row), "cursor_json": row_json(row, "cursor_json", {})} for row in connection.execute("SELECT * FROM source_cursors ORDER BY updated_at DESC")]
    backend = {
        "schemaVersion": "c2.1-admin-snapshot-v1", "buildId": build_id, "generatedAt": generated_at,
        "database": {"path": "data/c2.1-pipeline.db", "mainDatabaseMode": "read_only"},
        "coverageSummary": front["coverageSummary"], "statusCounts": front["statusCounts"],
        "supportedScope": front["supportedScope"], "sourceImpactSummary": source_impact,
        "sourceHealth": [dict(row) for row in health_rows], "cursors": cursors, "runs": runs,
        "funnel": dict(connection.execute("SELECT local_stage,COUNT(*) FROM candidates GROUP BY local_stage").fetchall()),
        "relationshipCounts": dict(connection.execute("SELECT relationship_class,COUNT(*) FROM candidates GROUP BY relationship_class").fetchall()),
        "ruleVersion": rules["ruleVersion"], "ruleConfigHash": rule_hash,
        "ruleSummary": {"ageBands": rules["ageBands"], "strongPaths": rules["strongPaths"], "statePriority": rules["statePriority"], "confidenceWeights": rules["confidenceWeights"], "hysteresis": rules["hysteresis"]},
        "quality": {"confidenceLevels": dict(Counter(row_json(row, "confidence_json", {}).get("level", "unknown") for row in connection.execute("SELECT confidence_json FROM evaluations WHERE is_current=1"))), "factorDirections": dict(Counter(item.get("direction", "unknown") for row in connection.execute("SELECT factor_directions_json FROM evaluations WHERE is_current=1") for item in row_json(row, "factor_directions_json", []))), "blockerCounts": dict(blocker_counts)},
        "snapshotBuilds": [dict(row) for row in connection.execute("SELECT * FROM snapshot_builds ORDER BY generated_at DESC LIMIT 20")],
    }
    front_body = FRONT_PREFIX + json.dumps(front, ensure_ascii=False, separators=(",", ":")) + ";\n"
    backend_body = BACKEND_PREFIX + json.dumps(backend, ensure_ascii=False, separators=(",", ":")) + ";\n"
    front_tmp = Path(front_path).with_suffix(".js.c21tmp")
    backend_tmp = Path(backend_path).with_suffix(".js.c21tmp")
    atomic_text(front_tmp, front_body)
    atomic_text(backend_tmp, backend_body)
    front_hash = file_sha256(front_tmp)
    backend_hash = file_sha256(backend_tmp)
    front_previous = Path(front_path).with_suffix(".js.c21previous")
    backend_previous = Path(backend_path).with_suffix(".js.c21previous")
    if Path(front_path).exists():
        front_previous.write_bytes(Path(front_path).read_bytes())
    if Path(backend_path).exists():
        backend_previous.write_bytes(Path(backend_path).read_bytes())
    try:
        os.replace(front_tmp, front_path)
        os.replace(backend_tmp, backend_path)
    except Exception:
        if front_previous.exists():
            os.replace(front_previous, front_path)
        if backend_previous.exists():
            os.replace(backend_previous, backend_path)
        raise
    finally:
        front_tmp.unlink(missing_ok=True)
        backend_tmp.unlink(missing_ok=True)
        front_previous.unlink(missing_ok=True)
        backend_previous.unlink(missing_ok=True)
    connection.execute(
        "INSERT INTO snapshot_builds(build_id,state,generated_at,source_cutoff_at,front_path,backend_path,front_sha256,backend_sha256,front_visible_count,hard_gate_passed_count) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (build_id, "success", generated_at, front["sourceCutoffAt"], str(front_path), str(backend_path), front_hash, backend_hash, front_visible, hard_gate_passed),
    )
    connection.commit()
    return {"buildId": build_id, "frontVisibleCount": front_visible, "hardGatePassedCount": hard_gate_passed, "frontSha256": front_hash, "backendSha256": backend_hash}


def run_pipeline(action="all", trigger_kind="manual", db_path=DEFAULT_DB_PATH, candidate_path=DEFAULT_CANDIDATE_PATH):
    initialize_database(db_path)
    run_id = "c21-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    started_at = utc_now()
    with pipeline_lock() as acquired:
        if not acquired:
            return {"status": "already_running", "message": "已有一条C2.1更新流水线正在运行，未启动第二个写入者。", "runtime": load_json(DEFAULT_STATUS_PATH, {})}
        status_payload(state="running", stage="initializing", runId=run_id, triggerKind=trigger_kind, processId=os.getpid(), startedAt=started_at, finishedAt=None, completedUnits=0, totalUnits=0, currentItem="", message="C2.1更新流水线已启动。", errorCode="", errorDetail="")
        try:
            with closing(open_pipeline_db(db_path)) as connection:
                run_row(connection, run_id, trigger_kind, "running", "initializing", started_at=started_at, message="C2.1更新流水线已启动。")
                connection.commit()
                result = {}
                if action in {"all", "import"}:
                    ensure_not_paused()
                    result["import"] = import_gate0_candidates(connection, run_id, trigger_kind, candidate_path)
                if action in {"all", "sync"}:
                    ensure_not_paused()
                    status_payload(state="running", stage="identity_mapping", runId=run_id, triggerKind=trigger_kind, message="正在只读同步项目身份、连续资产和产品证据。")
                    result["mapping"] = sync_main_mappings(connection)
                    result["legacyObservations"] = import_main_observations(connection)
                if action in {"all", "enrich"}:
                    ensure_not_paused()
                    status_payload(state="running", stage="enrichment", runId=run_id, triggerKind=trigger_kind, message="正在分层采集市场、官方仓库和100美元标准卖出报价。")
                    result["enrichment"] = run_enrichment(
                        connection,
                        progress=lambda completed, total, item: status_payload(
                            state="running", stage="enrichment", runId=run_id, triggerKind=trigger_kind,
                            completedUnits=completed, totalUnits=total, currentItem=item,
                            message=f"正在采集：{item}。",
                        ),
                        pause=ensure_not_paused,
                    )
                if action in {"all", "evaluate"}:
                    ensure_not_paused()
                    status_payload(state="running", stage="rules", runId=run_id, triggerKind=trigger_kind, message="正在按冻结规则计算宽硬门槛、四路径和五状态。")
                    result["evaluation"] = evaluate_all(connection)
                if action in {"all", "snapshot"}:
                    ensure_not_paused()
                    set_source_health(connection, "c2_1_pipeline", "all", "success", plain_reason="C2.1本轮确定性处理成功。")
                    connection.commit()
                    status_payload(state="running", stage="snapshot", runId=run_id, triggerKind=trigger_kind, message="正在原子构建C2.1前后台快照。")
                    result["snapshot"] = build_snapshots(connection)
                finished_at = utc_now()
                run_row(connection, run_id, trigger_kind, "completed", "completed", finished_at=finished_at, message="C2.1本轮流水线已完成。")
                connection.commit()
            status_payload(state="completed", stage="completed", runId=run_id, triggerKind=trigger_kind, finishedAt=finished_at, completedUnits=1, totalUnits=1, currentItem="本轮全部阶段", message="C2.1本轮流水线已完成。", resumeAvailable=False)
            return {"status": "completed", "runId": run_id, **result}
        except PipelinePaused as error:
            finished_at = utc_now()
            with closing(open_pipeline_db(db_path)) as connection:
                run_row(connection, run_id, trigger_kind, "paused", "paused", finished_at=finished_at, message=str(error))
                connection.commit()
            status_payload(state="paused", stage="paused", runId=run_id, triggerKind=trigger_kind, finishedAt=finished_at, message=str(error), resumeAvailable=True)
            return {"status": "paused", "runId": run_id, "message": str(error)}
        except Exception as error:
            finished_at = utc_now()
            error_code = "program_failure"
            with closing(open_pipeline_db(db_path)) as connection:
                run_row(connection, run_id, trigger_kind, "failed", "failed", finished_at=finished_at, message="C2.1流水线失败，已保留上次完整快照和断点。", error_code=error_code, error_detail=f"{type(error).__name__}: {error}")
                set_source_health(connection, "c2_1_pipeline", "all", "program_failure", "program_failure", "本次处理发生程序错误，需要修复；旧快照保持可用。")
                connection.commit()
            status_payload(state="failed", stage="failed", runId=run_id, triggerKind=trigger_kind, finishedAt=finished_at, message="C2.1流水线失败，已保留上次完整快照和断点。", resumeAvailable=True, errorCode=error_code, errorDetail=f"{type(error).__name__}: {error}")
            return {"status": "failed", "runId": run_id, "errorCode": error_code, "error": f"{type(error).__name__}: {error}"}


def main():
    parser = argparse.ArgumentParser(description="C2.1可恢复生产流水线")
    parser.add_argument("action", nargs="?", choices=("all", "import", "sync", "enrich", "evaluate", "snapshot"), default="all")
    parser.add_argument("--trigger", choices=("manual", "automatic", "resume", "development"), default="manual")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--candidate-path", type=Path, default=DEFAULT_CANDIDATE_PATH)
    args = parser.parse_args()
    result = run_pipeline(args.action, args.trigger, args.db, args.candidate_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] in {"completed", "already_running"} else 1)


if __name__ == "__main__":
    main()
