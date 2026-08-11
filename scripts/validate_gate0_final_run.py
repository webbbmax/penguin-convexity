import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


CLASSIFICATION_STATES = {
    "no_data",
    "quota_limited",
    "source_failure",
    "unsupported",
    "configuration_missing",
    "program_failure",
    "success",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_from_epoch(value):
    return datetime.fromtimestamp(int(value), timezone.utc).isoformat().replace("+00:00", "Z")


def event_identity(row):
    if row.get("transactionSignature"):
        return row["transactionSignature"], tuple(row.get("instructionAddress") or [])
    return row.get("transactionHash"), row.get("logIndex")


def expected_candidate(network_id, token, event):
    row = {
        "networkId": network_id,
        "tokenAddress": token,
        "earliestCoveredPoolAt": iso_from_epoch(event["blockTimestamp"]),
        "poolId": event["poolId"],
        "dexIds": event.get("dexIds") or [],
        "t0EvidenceType": "covered_dex_pool_created",
        "t0Status": "covered_dex_lower_bound_not_global_t0",
    }
    if event.get("slot") is not None:
        row["earliestCoveredPoolSlot"] = event["slot"]
        row["earliestCoveredArchiveBlockHeight"] = event["blockNumber"]
    else:
        row["earliestCoveredPoolBlock"] = event["blockNumber"]
    return row


def partition_paths(project_root, run_dir, partition):
    base = run_dir / "partitions" / partition["networkId"] / partition["schemaId"]
    stem = partition["partitionId"]
    reuse_path = (partition.get("reuse") or {}).get("eventPath")
    event_path = Path(reuse_path) if reuse_path else base / f"{stem}.jsonl"
    if not event_path.is_absolute():
        event_path = project_root / event_path
    return event_path, base / f"{stem}.complete.json"


def main():
    parser = argparse.ArgumentParser(description="Independently validate a completed Gate 0 run")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    project_root = Path(__file__).resolve().parents[1]
    plan = read_json(run_dir / "run-plan.json")
    summary = read_json(run_dir / "summary.json")
    config = read_json(project_root / "config" / "gate0-dex-backfill.json")
    known_quotes = set(config.get("solana", {}).get("knownQuoteTokens") or [])
    start_epoch = int(datetime.fromisoformat(plan["windowStart"].replace("Z", "+00:00")).timestamp())
    end_epoch = int(datetime.fromisoformat(plan["windowEnd"].replace("Z", "+00:00")).timestamp())
    errors = []
    event_count = 0
    hashes_checked = 0
    earliest = {}
    manifest_request_counts = Counter()
    event_counts_by_network = Counter()

    for index, partition in enumerate(plan["partitions"], 1):
        event_path, manifest_path = partition_paths(project_root, run_dir, partition)
        if not event_path.exists() or not manifest_path.exists():
            errors.append(f"missing partition artifact: {partition['partitionId']}")
            continue
        manifest = read_json(manifest_path)
        actual_hash = sha256(event_path)
        hashes_checked += 1
        if actual_hash != manifest.get("sha256"):
            errors.append(f"hash mismatch: {partition['partitionId']}")
        local_count = 0
        identities = set()
        for line_number, line in enumerate(event_path.open(encoding="utf-8"), 1):
            row = json.loads(line)
            local_count += 1
            event_count += 1
            event_counts_by_network[row.get("networkId")] += 1
            position = row.get("blockNumber")
            timestamp = row.get("blockTimestamp")
            if row.get("networkId") != partition["networkId"]:
                errors.append(f"network mismatch: {partition['partitionId']}:{line_number}")
            if position is None or not partition["fromBlockOrSlot"] <= int(position) <= partition["toBlockOrSlot"]:
                errors.append(f"range mismatch: {partition['partitionId']}:{line_number}")
            if timestamp is None or not start_epoch <= int(timestamp) <= end_epoch:
                errors.append(f"window mismatch: {partition['partitionId']}:{line_number}")
            if not row.get("poolId") or not row.get("tokenAddresses"):
                errors.append(f"missing pool/token: {partition['partitionId']}:{line_number}")
            identity = event_identity(row)
            if identity in identities:
                errors.append(f"duplicate event: {partition['partitionId']}:{line_number}")
            identities.add(identity)
            if row.get("decodeComplete") is False or row.get("decodeError"):
                errors.append(f"decode failure: {partition['partitionId']}:{line_number}")
            for token in row.get("tokenAddresses") or []:
                if row.get("networkId") == "solana-mainnet" and token in known_quotes:
                    continue
                key = row.get("networkId"), token
                previous = earliest.get(key)
                if previous is None or int(position) < int(previous["blockNumber"]):
                    earliest[key] = row
        if local_count != int(manifest.get("rowCount") or 0):
            errors.append(f"row count mismatch: {partition['partitionId']}")
        manifest_request_counts[partition["partitionId"]] = int(manifest.get("requestCount") or 0)
        if index % 25 == 0 or index == len(plan["partitions"]):
            print(f"partitions {index}/{len(plan['partitions'])}; events {event_count}; candidates {len(earliest)}", flush=True)

    ledger_counts = Counter()
    ledger_partition_counts = Counter()
    ledger_rows = 0
    ledger_errors = 0
    secret_errors = 0
    for line_number, line in enumerate((run_dir / "request-ledger.jsonl").open(encoding="utf-8"), 1):
        row = json.loads(line)
        ledger_rows += 1
        state = row.get("state")
        ledger_counts[state] += 1
        ledger_partition_counts[row.get("partitionId")] += 1
        required = ("source", "partitionId", "rangeKind", "fromBlockOrSlot", "toBlockOrSlot", "state", "observedAt", "latencyMs")
        if state not in CLASSIFICATION_STATES or any(row.get(key) is None for key in required):
            ledger_errors += 1
        text = json.dumps(row, ensure_ascii=False).lower()
        if any(marker in text for marker in ('"authorization"', '"api_key"', '"apikey"', '"secret"')) or ("[redacted]" not in text and "nodereal.io/v1/" in text):
            secret_errors += 1
    if ledger_partition_counts != manifest_request_counts:
        errors.append("request ledger and partition manifests disagree")
    if ledger_errors:
        errors.append(f"invalid request ledger rows: {ledger_errors}")
    if secret_errors:
        errors.append(f"request ledger secret exposure rows: {secret_errors}")

    candidate_count = 0
    candidate_counts_by_network = Counter()
    previous_key = None
    candidate_path = run_dir / "candidate-tokens.jsonl"
    for line_number, line in enumerate(candidate_path.open(encoding="utf-8"), 1):
        candidate = json.loads(line)
        key = candidate.get("networkId"), candidate.get("tokenAddress")
        candidate_count += 1
        candidate_counts_by_network[key[0]] += 1
        if previous_key is not None and key <= previous_key:
            errors.append(f"candidate order/uniqueness failure: line {line_number}")
        previous_key = key
        event = earliest.pop(key, None)
        if event is None:
            errors.append(f"candidate has no event: line {line_number}")
        elif candidate != expected_candidate(key[0], key[1], event):
            errors.append(f"candidate earliest-event mismatch: line {line_number}")
        if line_number % 500000 == 0:
            print(f"candidates {line_number}; unmatched expected {len(earliest)}", flush=True)
    if earliest:
        errors.append(f"events produced {len(earliest)} missing candidates")

    unsupported = plan.get("unsupportedDexLabels") or []
    unsupported_counts = Counter(label.split(":", 1)[0] for label in unsupported)
    checks = {
        "partitionCount": len(plan["partitions"]),
        "partitionHashesChecked": hashes_checked,
        "eventCount": event_count,
        "eventCountsByNetwork": dict(event_counts_by_network),
        "candidateCount": candidate_count,
        "candidateCountsByNetwork": dict(candidate_counts_by_network),
        "requestCount": ledger_rows,
        "requestStates": dict(ledger_counts),
        "unsupportedCounts": dict(unsupported_counts),
        "fixedWindow": {"start": plan["windowStart"], "end": plan["windowEnd"]},
        "remainingExpectedCandidates": len(earliest),
    }
    coverage = summary.get("coverage") or {}
    if event_count != int(coverage.get("events") or 0):
        errors.append("summary event count mismatch")
    if candidate_count != int(coverage.get("candidateTokens") or 0):
        errors.append("summary candidate count mismatch")
    if ledger_rows != int((summary.get("requestSummary") or {}).get("total") or 0):
        errors.append("summary request count mismatch")
    expected_unsupported = {"arbitrum-mainnet": 6, "base-mainnet": 8, "ethereum-mainnet": 4}
    if dict(unsupported_counts) != expected_unsupported:
        errors.append("frozen unsupported-label counts mismatch")
    result = {
        "schemaVersion": "convexity-gate0-sol-independent-audit-v1",
        "runId": plan["runId"],
        "auditedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "independent_local_recomputation_no_network",
        "checks": checks,
        "errors": errors[:100],
        "errorCount": len(errors),
        "pass": not errors,
    }
    output = run_dir / "sol-independent-audit.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
