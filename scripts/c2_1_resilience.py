#!/usr/bin/env python3
"""Committed source cursors and bounded cooldowns for C2.1 collectors."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from c2_1_db import json_text, utc_now


COMPLETED_STATES = {"success", "no_data", "unsupported"}
COOLDOWN_SECONDS = (15 * 60, 30 * 60, 60 * 60, 6 * 60 * 60)


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def cursor_decision(connection, source_id, scope_key, stage, window_key, now=None):
    """Return run/complete/cooldown/blocked without moving a cursor."""
    row = connection.execute(
        "SELECT * FROM source_cursors WHERE source_id=? AND scope_key=? AND stage=?",
        (source_id, scope_key, stage),
    ).fetchone()
    if not row:
        return {"action": "run", "cursor": {}}
    try:
        cursor = json.loads(row["cursor_json"] or "{}")
    except json.JSONDecodeError:
        cursor = {}
    same_window = cursor.get("windowKey") == window_key
    if same_window and row["status"] in COMPLETED_STATES and cursor.get("completed"):
        return {"action": "complete", "cursor": cursor}
    current = now or datetime.now(timezone.utc)
    next_retry = parse_time(row["next_retry_at"])
    if same_window and next_retry and current < next_retry:
        return {"action": "cooldown", "cursor": cursor, "nextRetryAt": row["next_retry_at"]}
    if same_window and row["status"] in {"configuration_missing", "program_failure"}:
        return {"action": "blocked", "cursor": cursor}
    return {"action": "run", "cursor": cursor if same_window else {}}


def commit_cursor(connection, source_id, scope_key, stage, window_key, state, cursor=None, quota_reset_at=None):
    """Commit only after the corresponding data transaction has committed."""
    previous = connection.execute(
        "SELECT consecutive_failures FROM source_cursors WHERE source_id=? AND scope_key=? AND stage=?",
        (source_id, scope_key, stage),
    ).fetchone()
    failures = int(previous[0]) if previous else 0
    completed = state in COMPLETED_STATES
    if state in {"source_failure", "quota_limited"}:
        failures += 1
    elif completed:
        failures = 0
    next_retry = None
    if state == "source_failure":
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=COOLDOWN_SECONDS[min(failures - 1, len(COOLDOWN_SECONDS) - 1)])
    elif state == "quota_limited":
        next_retry = parse_time(quota_reset_at) or datetime.now(timezone.utc) + timedelta(seconds=COOLDOWN_SECONDS[min(failures - 1, len(COOLDOWN_SECONDS) - 1)])
    payload = {**(cursor or {}), "windowKey": window_key, "completed": completed}
    now = utc_now()
    connection.execute(
        """
        INSERT INTO source_cursors(source_id,scope_key,stage,cursor_json,status,consecutive_failures,next_retry_at,last_success_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id,scope_key,stage) DO UPDATE SET cursor_json=excluded.cursor_json,status=excluded.status,
          consecutive_failures=excluded.consecutive_failures,next_retry_at=excluded.next_retry_at,
          last_success_at=COALESCE(excluded.last_success_at,source_cursors.last_success_at),updated_at=excluded.updated_at
        """,
        (
            source_id, scope_key, stage, json_text(payload), state, failures,
            next_retry.isoformat().replace("+00:00", "Z") if next_retry else None,
            now if state == "success" else None, now,
        ),
    )
    connection.commit()
    return payload


def hour_window(value=None):
    return str(value or utc_now())[:13]


def day_window(value=None):
    return str(value or utc_now())[:10]
