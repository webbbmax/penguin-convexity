#!/usr/bin/env python3
"""Read-only age-stratified market study over the accepted Gate 0 candidates."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATES = (
    PROJECT_ROOT
    / "runtime"
    / "gate0-shadow"
    / "backfill"
    / "background"
    / "runs"
    / "gate0-solfinal-20260809T045924Z-f7bbd2"
    / "candidate-tokens.jsonl"
)
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "gate0-shadow-scope.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "c2.1-age-threshold-analysis"
AGE_BANDS = (
    ("age_0_2", 0, 2),
    ("age_3_6", 3, 6),
    ("age_7_13", 7, 13),
    ("age_14_30", 14, 30),
    ("age_31_90", 31, 90),
)
USER_AGENT = "Penguin-Convexity-C2.1-Age-Study/0.1"


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def age_days(t0: str, as_of: datetime) -> int:
    return math.floor((as_of - parse_utc(t0)).total_seconds() / 86400)


def age_band(days: int) -> str | None:
    for name, low, high in AGE_BANDS:
        if low <= days <= high:
            return name
    return None


def atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    os.replace(temporary, path)


def load_networks(config_path: Path):
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    return {
        item["id"]: {
            "dexScreenerId": item["dexScreenerId"],
            "chainType": item["chainType"],
        }
        for item in config["networks"]
    }, config["sources"]["dexscreener"]


def normalized_address(value: str, chain_type: str) -> str:
    text = str(value or "").strip()
    return text.lower() if chain_type == "EVM" else text


def deterministic_rank(network_id: str, token_address: str) -> int:
    identity = f"{network_id}|{token_address}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(identity).digest()[:16], "big")


def select_sample(candidate_path: Path, output_root: Path, as_of: datetime, per_cell: int):
    networks, _ = load_networks(DEFAULT_CONFIG)
    heaps = defaultdict(list)
    counts = Counter()
    excluded = Counter()
    rows_seen = 0
    candidate_hasher = hashlib.sha256()
    with candidate_path.open("rb") as handle:
        for line in handle:
            candidate_hasher.update(line)
            rows_seen += 1
            if rows_seen % 500_000 == 0:
                print(f"sample-progress rows={rows_seen}", flush=True)
            try:
                row = json.loads(line)
                network_id = row["networkId"]
                token_address = row["tokenAddress"]
                t0 = row["earliestCoveredPoolAt"]
                chain_type = networks[network_id]["chainType"]
                token_address = normalized_address(token_address, chain_type)
                days = age_days(t0, as_of)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                excluded["invalid_row"] += 1
                continue
            band = age_band(days)
            if band is None:
                excluded["future" if days < 0 else "outside_90_days"] += 1
                continue
            cell = (network_id, band)
            counts[cell] += 1
            rank = deterministic_rank(network_id, token_address)
            selected = {
                "networkId": network_id,
                "chainType": chain_type,
                "tokenAddress": token_address,
                "gate0T0": t0,
                "gate0AgeDays": days,
                "gate0AgeBand": band,
                "gate0PoolId": row.get("poolId"),
                "gate0DexIds": row.get("dexIds") or [],
                "t0Status": row.get("t0Status"),
                "sampleRank": f"{rank:032x}",
            }
            heap = heaps[cell]
            entry = (-rank, token_address, selected)
            if len(heap) < per_cell:
                heapq.heappush(heap, entry)
            elif rank < -heap[0][0]:
                heapq.heapreplace(heap, entry)

    sample = []
    for cell in sorted(heaps):
        sample.extend(item[2] for item in heaps[cell])
    sample.sort(key=lambda item: (item["networkId"], item["gate0AgeBand"], item["sampleRank"]))
    sample_path = output_root / "sample-selection.jsonl"
    write_jsonl(sample_path, sample)
    profile = {
        "schemaVersion": "c2.1-age-threshold-sample-v0.1",
        "createdAt": utc_text(datetime.now(timezone.utc)),
        "asOf": utc_text(as_of),
        "candidatePath": str(candidate_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "candidateSha256": candidate_hasher.hexdigest(),
        "candidateRowsSeen": rows_seen,
        "perCellTarget": per_cell,
        "selectedRows": len(sample),
        "populationByCell": {
            f"{network}|{band}": value for (network, band), value in sorted(counts.items())
        },
        "selectedByCell": dict(
            Counter(f"{row['networkId']}|{row['gate0AgeBand']}" for row in sample)
        ),
        "excluded": dict(excluded),
    }
    atomic_json(output_root / "sample-profile.json", profile)
    print(json.dumps({"sample": str(sample_path), **profile}, ensure_ascii=False, indent=2))


def chunks(values, size):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def pair_created_at(pair):
    value = pair.get("pairCreatedAt")
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc) if value else None
    except (TypeError, ValueError, OSError):
        return None


def metric(mapping, period):
    return number((mapping or {}).get(period))


def transaction_metric(pair, period, field):
    return number(((pair.get("txns") or {}).get(period) or {}).get(field))


def summarize_pair(pair, token_address, chain_type):
    info = pair.get("info") or {}
    base_address = normalized_address(
        (pair.get("baseToken") or {}).get("address"), chain_type
    )
    quote_address = normalized_address(
        (pair.get("quoteToken") or {}).get("address"), chain_type
    )
    token_side = (
        "base"
        if token_address == base_address
        else "quote"
        if token_address == quote_address
        else "unmatched"
    )
    token_is_base = token_side == "base"
    return {
        "pairAddress": pair.get("pairAddress"),
        "dexId": pair.get("dexId"),
        "baseTokenAddress": base_address,
        "quoteTokenAddress": quote_address,
        "tokenSide": token_side,
        "pairCreatedAt": utc_text(pair_created_at(pair)) if pair_created_at(pair) else None,
        "liquidityUsd": number((pair.get("liquidity") or {}).get("usd")),
        "fdvUsd": number(pair.get("fdv")) if token_is_base else None,
        "marketCapUsd": number(pair.get("marketCap")) if token_is_base else None,
        "volumeH1Usd": metric(pair.get("volume"), "h1"),
        "volumeH6Usd": metric(pair.get("volume"), "h6"),
        "volumeH24Usd": metric(pair.get("volume"), "h24"),
        "buysH1": transaction_metric(pair, "h1", "buys"),
        "sellsH1": transaction_metric(pair, "h1", "sells"),
        "buysH6": transaction_metric(pair, "h6", "buys"),
        "sellsH6": transaction_metric(pair, "h6", "sells"),
        "buysH24": transaction_metric(pair, "h24", "buys"),
        "sellsH24": transaction_metric(pair, "h24", "sells"),
        "priceChangeH1Pct": metric(pair.get("priceChange"), "h1") if token_is_base else None,
        "priceChangeH6Pct": metric(pair.get("priceChange"), "h6") if token_is_base else None,
        "priceChangeH24Pct": metric(pair.get("priceChange"), "h24") if token_is_base else None,
        "websiteCount": len(info.get("websites") or []) if token_is_base else None,
        "socialCount": len(info.get("socials") or []) if token_is_base else None,
    }


def request_batch(base_url, dex_id, addresses, minimum_interval, last_request_at):
    elapsed = time.monotonic() - last_request_at[0]
    if elapsed < minimum_interval:
        time.sleep(minimum_interval - elapsed)
    encoded = urllib.parse.quote(",".join(addresses), safe=",")
    url = f"{base_url}/tokens/v1/{dex_id}/{encoded}"
    safe_url = f"{base_url}/tokens/v1/{dex_id}/<batch:{len(addresses)}>"
    attempts = []
    for attempt, delay in enumerate((0, 2, 5, 10), start=1):
        if delay:
            time.sleep(delay)
        started = time.monotonic()
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        try:
            last_request_at[0] = time.monotonic()
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                attempts.append(
                    {
                        "attempt": attempt,
                        "state": "success",
                        "httpStatus": response.status,
                        "latencyMs": round((time.monotonic() - started) * 1000),
                    }
                )
                return payload, safe_url, attempts, "success"
        except urllib.error.HTTPError as error:
            state = (
                "quota_limited"
                if error.code == 429
                else "configuration_missing"
                if error.code in (401, 403)
                else "source_failure"
            )
            attempts.append(
                {
                    "attempt": attempt,
                    "state": state,
                    "httpStatus": error.code,
                    "latencyMs": round((time.monotonic() - started) * 1000),
                }
            )
            if state == "configuration_missing" or attempt == 4:
                return None, safe_url, attempts, state
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            reason = getattr(error, "reason", None)
            attempts.append(
                {
                    "attempt": attempt,
                    "state": "source_failure",
                    "httpStatus": None,
                    "latencyMs": round((time.monotonic() - started) * 1000),
                    "errorType": type(error).__name__,
                    "errorReason": str(reason if reason is not None else error)[:160],
                }
            )
            if attempt == 4:
                return None, safe_url, attempts, "source_failure"
    return None, safe_url, attempts, "program_failure"


def fetch_market(sample_path: Path, output_root: Path, as_of: datetime, batch_size=None):
    networks, source = load_networks(DEFAULT_CONFIG)
    sample = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines() if line]
    observation_path = output_root / "market-observations.jsonl"
    ledger_path = output_root / "request-ledger.jsonl"
    completed = {}
    if observation_path.exists():
        for line in observation_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            best_pair = row.get("bestPair") or {}
            success_is_oriented = row.get("state") == "success" and best_pair.get(
                "tokenSide"
            ) in {"base", "quote"}
            if row.get("state") == "no_data" or success_is_oriented:
                completed[(row["networkId"], row["tokenAddress"])] = row
    pending_by_network = defaultdict(list)
    for row in sample:
        key = (row["networkId"], row["tokenAddress"])
        if key not in completed:
            pending_by_network[row["networkId"]].append(row)
    observation_path.parent.mkdir(parents=True, exist_ok=True)
    last_request_at = [0.0]
    request_number = 0
    with observation_path.open("a", encoding="utf-8", newline="\n") as observations, ledger_path.open(
        "a", encoding="utf-8", newline="\n"
    ) as ledger:
        for network_id in sorted(pending_by_network):
            settings = networks[network_id]
            chain_type = settings["chainType"]
            effective_batch_size = int(batch_size or source["tokenBatchSize"])
            for batch in chunks(pending_by_network[network_id], effective_batch_size):
                request_number += 1
                addresses = [row["tokenAddress"] for row in batch]
                payload, safe_url, attempts, state = request_batch(
                    source["baseUrl"],
                    settings["dexScreenerId"],
                    addresses,
                    float(source["minimumRequestIntervalSeconds"]),
                    last_request_at,
                )
                pairs = payload if isinstance(payload, list) else (payload or {}).get("pairs") or []
                pairs_by_token = defaultdict(list)
                for pair in pairs:
                    if str(pair.get("chainId") or "") != settings["dexScreenerId"]:
                        continue
                    for side in ("baseToken", "quoteToken"):
                        address = normalized_address(
                            (pair.get(side) or {}).get("address"), chain_type
                        )
                        if address in addresses:
                            pairs_by_token[address].append(pair)
                for selected in batch:
                    address = selected["tokenAddress"]
                    matches = pairs_by_token.get(address) or []
                    earliest_pair = min(
                        (created for created in (pair_created_at(pair) for pair in matches) if created),
                        default=None,
                    )
                    best = max(
                        matches,
                        key=lambda pair: number((pair.get("liquidity") or {}).get("usd")) or -1,
                        default=None,
                    )
                    effective_t0 = min(
                        parse_utc(selected["gate0T0"]),
                        earliest_pair or parse_utc(selected["gate0T0"]),
                    )
                    effective_days = age_days(utc_text(effective_t0), as_of)
                    row = {
                        **selected,
                        "state": "success" if best else ("no_data" if state == "success" else state),
                        "collectedAt": utc_text(datetime.now(timezone.utc)),
                        "effectiveT0": utc_text(effective_t0),
                        "effectiveAgeDays": effective_days,
                        "effectiveAgeBand": age_band(effective_days),
                        "matchedPairs": len(matches),
                        "bestPair": summarize_pair(best, address, chain_type) if best else None,
                    }
                    observations.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                observations.flush()
                os.fsync(observations.fileno())
                ledger.write(
                    json.dumps(
                        {
                            "requestNumber": request_number,
                            "networkId": network_id,
                            "safeUrl": safe_url,
                            "requested": len(batch),
                            "pairsReturned": len(pairs),
                            "state": state,
                            "attempts": attempts,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                ledger.flush()
                os.fsync(ledger.fileno())
                print(
                    f"fetch-progress request={request_number} network={network_id} requested={len(batch)} pairs={len(pairs)} state={state}",
                    flush=True,
                )
    print(
        json.dumps(
            {
                "sampleRows": len(sample),
                "alreadyCompleted": len(completed),
                "pendingAtStart": sum(len(rows) for rows in pending_by_network.values()),
                "newRequests": request_number,
                "observationPath": str(observation_path),
                "ledgerPath": str(ledger_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    global DEFAULT_CONFIG
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("sample", "fetch"))
    parser.add_argument("--candidate-path", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--per-cell", type=int, default=100)
    parser.add_argument("--batch-size", type=int)
    args = parser.parse_args()
    DEFAULT_CONFIG = args.config.resolve()
    as_of = parse_utc(args.as_of)
    if args.action == "sample":
        select_sample(args.candidate_path.resolve(), args.output_root.resolve(), as_of, args.per_cell)
    else:
        fetch_market(
            args.output_root.resolve() / "sample-selection.jsonl",
            args.output_root.resolve(),
            as_of,
            batch_size=args.batch_size,
        )


if __name__ == "__main__":
    main()
