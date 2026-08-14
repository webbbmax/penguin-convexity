#!/usr/bin/env python3
"""Resolve market facts without letting a later blank refresh erase a real quote."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable


QUOTE_PAYLOAD_KEYS = {
    "quoteProvider",
    "quoteAttempts",
    "quoteBoundary",
    "quoteRoute",
    "quoteOutput",
    "quoteOutputToken",
    "quoteOutputPriceUsd",
    "quoteObservedAt",
}
CANCELLED_PERCENTAGE_RISK_CODES = {
    "confirmed_sell_tax_ge_20pct",
    "buy_or_sell_tax_ge_20",
    "liquidity_drop_ge_80",
}


def _dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads((_dict(row).get("payload_json") or "{}"))
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def confirmed_trade_block(row: Any) -> bool:
    """Interpret persisted risk facts under the quote-success-only trial.

    Raw percentage observations remain stored, but a row whose only reason is a
    cancelled percentage rule is no longer treated as a hard trade block.
    """

    value = _dict(row)
    if value.get("source_status") != "success" or not bool(value.get("hard_trade_block")):
        return False
    try:
        codes = json.loads(value.get("reason_codes_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        codes = []
    normalized = {str(code) for code in codes if code}
    return not normalized or bool(normalized - CANCELLED_PERCENTAGE_RISK_CODES)


def is_quote_attempt(row: Any) -> bool:
    """Distinguish an actual quote attempt from a normal market row's default no_data."""

    value = _dict(row)
    payload = _payload(value)
    return bool(
        "quoteBoundary" in payload
        or "quoteAttempts" in payload
        or value.get("standard_sell_quote_state") not in {None, "", "no_data"}
        or value.get("standard_sell_quote_loss_pct") is not None
    )


def merge_quote_into_market(market_row: Any, quote_row: Any) -> dict[str, Any] | None:
    """Keep the newest market metrics and the newest explicit quote attempt independently."""

    if market_row is None:
        return None
    market = _dict(market_row)
    if quote_row is None:
        return market
    quote = _dict(quote_row)
    market["standard_sell_notional_usd"] = quote.get("standard_sell_notional_usd")
    market["standard_sell_quote_state"] = quote.get("standard_sell_quote_state") or "no_data"
    market["standard_sell_quote_loss_pct"] = quote.get("standard_sell_quote_loss_pct")
    market["effective_quote_observation_id"] = quote.get("observation_id")
    market["effective_quote_observed_at"] = quote.get("observed_at")
    payload = _payload(market)
    quote_payload = _payload(quote)
    for key in QUOTE_PAYLOAD_KEYS:
        if key in quote_payload:
            payload[key] = quote_payload[key]
    payload["quoteObservedAt"] = quote.get("observed_at")
    market["payload_json"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return market


def latest_effective_market_row(connection, candidate_id: int, completed_at: str | None = None):
    parameters: list[Any] = [int(candidate_id)]
    cutoff = ""
    if completed_at:
        cutoff = " AND observed_at<=?"
        parameters.append(completed_at)
    rows = connection.execute(
        f"""SELECT * FROM market_observations
        WHERE candidate_id=?{cutoff}
        ORDER BY observed_at DESC,observation_id DESC""",
        tuple(parameters),
    ).fetchall()
    if not rows:
        return None
    quote = next((row for row in rows if is_quote_attempt(row)), None)
    return merge_quote_into_market(rows[0], quote)


def latest_effective_market_rows(
    rows: Iterable[Any],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Bulk variant used by snapshot builders; previous means previous market window."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = _dict(row)
        grouped[int(value["candidate_id"])].append(value)
    latest: dict[int, dict[str, Any]] = {}
    previous: dict[int, dict[str, Any]] = {}
    for candidate_id, values in grouped.items():
        values.sort(
            key=lambda row: (str(row.get("observed_at") or ""), str(row.get("observation_id") or "")),
            reverse=True,
        )
        quote = next((row for row in values if is_quote_attempt(row)), None)
        latest[candidate_id] = merge_quote_into_market(values[0], quote) or values[0]
        if len(values) > 1:
            previous_quote = next(
                (
                    row
                    for row in values[1:]
                    if str(row.get("observed_at") or "") <= str(values[1].get("observed_at") or "")
                    and is_quote_attempt(row)
                ),
                None,
            )
            previous[candidate_id] = merge_quote_into_market(values[1], previous_quote) or values[1]
    return latest, previous
