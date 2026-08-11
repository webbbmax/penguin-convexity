#!/usr/bin/env python3
"""Create a no-network Sol-final corrected Gate 0 run from completed artifacts."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import uuid

import gate0_backfill_background as runner


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def canonical_ledger(source_run):
    summary = runner.safe_json(source_run / "summary.json")
    expected = int((summary.get("requestSummary") or {}).get("total") or 0)
    source_path = source_run / "request-ledger.jsonl"
    lines = source_path.read_bytes().splitlines()
    if len(lines) < expected:
        raise ValueError(f"request ledger has {len(lines)} rows; summary requires {expected}")
    duplicate_tail = lines[expected:]
    if duplicate_tail:
        reference_start = expected - len(duplicate_tail)
        if reference_start < 0 or lines[reference_start:expected] != duplicate_tail:
            raise ValueError("request ledger tail is not a verified duplicate suffix")
    rows = [json.loads(line) for line in lines[:expected]]
    if any(not row.get("partitionId") for row in rows):
        raise ValueError("request ledger contains an unassigned request")
    result_cap_rows = 0
    for current, following in zip(rows, rows[1:]):
        if (
            current.get("state") == "success"
            and current.get("partitionId") == following.get("partitionId")
            and current.get("fromBlockOrSlot") == following.get("fromBlockOrSlot")
            and current.get("toBlockOrSlot") is not None
            and following.get("toBlockOrSlot") is not None
            and int(following["toBlockOrSlot"]) < int(current["toBlockOrSlot"])
        ):
            current["state"] = "source_failure"
            current["error"] = "result_cap_range_resize"
            result_cap_rows += 1
    return rows, len(duplicate_tail), result_cap_rows


def write_ledger(path, rows):
    temporary = path.with_name(path.name + ".building")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def event_file(run_dir, partition):
    if partition.get("reuse"):
        return runner.project_path(partition["reuse"]["eventPath"])
    return runner.partition_paths(run_dir, partition)[0]


def scan_partition_file(path, partition, plan, filtered_output=None):
    start = int(partition["fromBlockOrSlot"])
    end = int(partition["toBlockOrSlot"])
    window_start = runner.parse_utc(plan["windowStart"]).timestamp()
    window_end = runner.parse_utc(plan["windowEnd"]).timestamp()
    identities = set()
    kept = removed = 0
    minimum = maximum = None
    minimum_timestamp = maximum_timestamp = None
    output = filtered_output.open("w", encoding="utf-8") if filtered_output else None
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("networkId") != partition["networkId"]:
                    raise ValueError(f"wrong network at {path}:{line_number}")
                if not event.get("poolId"):
                    raise ValueError(f"missing pool at {path}:{line_number}")
                if not [token for token in (event.get("tokenAddresses") or []) if token]:
                    raise ValueError(f"missing token at {path}:{line_number}")
                if event.get("decodeComplete") is False:
                    raise ValueError(f"decode incomplete at {path}:{line_number}")
                position = runner.event_position(event, partition.get("rangeKind", "block"))
                if position is None or position < start or position > end:
                    raise ValueError(f"position outside partition at {path}:{line_number}")
                timestamp = runner.event_timestamp(event)
                if timestamp is None:
                    raise ValueError(f"missing timestamp at {path}:{line_number}")
                if timestamp < window_start or timestamp > window_end:
                    removed += 1
                    continue
                identity = runner.event_identity(event)
                if identity in identities:
                    raise ValueError(f"duplicate event identity at {path}:{line_number}")
                identities.add(identity)
                kept += 1
                minimum = position if minimum is None else min(minimum, position)
                maximum = position if maximum is None else max(maximum, position)
                minimum_timestamp = timestamp if minimum_timestamp is None else min(minimum_timestamp, timestamp)
                maximum_timestamp = timestamp if maximum_timestamp is None else max(maximum_timestamp, timestamp)
                if output:
                    output.write(line if line.endswith("\n") else line + "\n")
        if output:
            output.flush()
            os.fsync(output.fileno())
    finally:
        if output:
            output.close()
    return {
        "rowCount": kept,
        "removed": removed,
        "minimumBlockOrSlot": minimum,
        "maximumBlockOrSlot": maximum,
        "minimumTimestamp": datetime.fromtimestamp(minimum_timestamp, timezone.utc).isoformat().replace("+00:00", "Z") if minimum_timestamp is not None else None,
        "maximumTimestamp": datetime.fromtimestamp(maximum_timestamp, timezone.utc).isoformat().replace("+00:00", "Z") if maximum_timestamp is not None else None,
    }


def repair_manifests(source_run, target_run, plan, request_counts):
    manifests = {}
    removed_by_network = Counter()
    for partition in plan["partitions"]:
        source_event = event_file(source_run, partition)
        target_event = event_file(target_run, partition)
        stats = scan_partition_file(target_event, partition, plan)
        if stats["removed"]:
            reuse_kind = (partition.get("reuse") or {}).get("kind")
            if reuse_kind == "accepted_baseline":
                raise ValueError("accepted read-only baseline contains an out-of-window event")
            if partition.get("reuse"):
                target_event = runner.partition_paths(target_run, partition)[0]
                target_event.parent.mkdir(parents=True, exist_ok=True)
            temporary = target_event.with_name(target_event.name + ".sol-filtering")
            rewritten = scan_partition_file(source_event, partition, plan, temporary)
            temporary.replace(target_event)
            stats = rewritten
            removed_by_network[partition["networkId"]] += rewritten["removed"]
            if partition.get("reuse"):
                partition["reuse"] = {
                    **partition["reuse"],
                    "kind": "sol_final_filtered_completed_partition",
                    "eventPath": runner.project_relative(target_event),
                    "sha256": runner.sha256_path(target_event),
                    "rowCount": rewritten["rowCount"],
                    "minimumBlockOrSlot": rewritten["minimumBlockOrSlot"],
                    "maximumBlockOrSlot": rewritten["maximumBlockOrSlot"],
                }
        manifest = {
            "schemaVersion": runner.MANIFEST_SCHEMA,
            "partitionId": partition["partitionId"],
            "completedAt": now_iso(),
            "sourceState": "no_data" if stats["rowCount"] == 0 else "success",
            "rowCount": stats["rowCount"],
            "eventIdentityUnique": True,
            "decodeFailures": 0,
            "minimumBlockOrSlot": stats["minimumBlockOrSlot"],
            "maximumBlockOrSlot": stats["maximumBlockOrSlot"],
            "minimumTimestamp": stats["minimumTimestamp"],
            "maximumTimestamp": stats["maximumTimestamp"],
            "sha256": runner.sha256_path(target_event),
            "requestCount": int(request_counts.get(partition["partitionId"], 0)),
        }
        complete = runner.partition_paths(target_run, partition)[1]
        runner.atomic_write_json(complete, manifest)
        manifests[partition["partitionId"]] = manifest
    return manifests, dict(removed_by_network)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Finalize a completed Gate 0 run without network requests")
    parser.add_argument("--source-run", required=True)
    args = parser.parse_args(argv)
    source_run = runner.BACKGROUND_ROOT / "runs" / args.source_run
    source_plan = runner.safe_json(source_run / "run-plan.json")
    source_validation = runner.safe_json(source_run / "validation.json")
    if not source_validation.get("pass") or source_validation.get("completedPartitionCount") != len(source_plan.get("partitions") or []):
        raise ValueError("source run is not a completed validated run")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Keep the run id shorter than the regular scanner id: Windows' legacy
    # MAX_PATH limit also includes long schema and partition filenames.
    run_id = f"gate0-solfinal-{stamp}-{uuid.uuid4().hex[:6]}"
    target_run = runner.BACKGROUND_ROOT / "runs" / run_id
    building = target_run.with_name(target_run.name + ".building")
    lock_payload = runner.acquire_lock(run_id)
    if lock_payload is None:
        raise RuntimeError("another Gate 0 worker is active")
    try:
        shutil.copytree(source_run, building, copy_function=link_or_copy)
        plan = dict(source_plan)
        plan["runId"] = run_id
        plan["createdAt"] = now_iso()
        plan["unsupportedDexLabels"] = list(runner.FROZEN_UNSUPPORTED_LABELS)
        plan["correctedFromRunId"] = args.source_run
        plan["solFinalCorrections"] = [
            "strict_fixed_window_event_filter",
            "canonical_request_ledger",
            "frozen_unsupported_label_boundary",
            "accurate_per_network_candidate_counts",
        ]
        runner.atomic_write_json(building / "run-plan.json", plan)

        ledger_rows, duplicate_ledger_rows, result_cap_rows = canonical_ledger(source_run)
        write_ledger(building / "request-ledger.jsonl", ledger_rows)
        request_counts = Counter(row["partitionId"] for row in ledger_rows)
        manifests, removed_by_network = repair_manifests(source_run, building, plan, request_counts)
        runner.atomic_write_json(building / "run-plan.json", plan)

        config = runner.core.load_config(runner.CONFIG_PATH)
        _candidate_path, candidate_count, candidate_counts = runner.write_candidates(
            plan, building, manifests, config
        )
        ledger = runner.core.RequestLedger(timeout=int(config["evm"].get("rpcTimeoutSeconds", 30)))
        ledger.requests.extend(ledger_rows)
        validation = runner.validate_run(plan, building, manifests, [])
        validation.update(
            {
                "missingPools": 0,
                "missingTokenRows": 0,
                "missingTimestamps": 0,
                "outsideWindowEvents": 0,
                "removedOutsideWindowEvents": sum(removed_by_network.values()),
                "removedOutsideWindowByNetwork": removed_by_network,
                "duplicateLedgerRowsRemoved": duplicate_ledger_rows,
                "resultCapResponsesReclassified": result_cap_rows,
            }
        )
        runner.atomic_write_json(building / "validation.json", validation)
        summary = runner.final_summary(
            plan, building, manifests, validation, candidate_count, candidate_counts, ledger
        )
        runner.atomic_write_json(building / "summary.json", summary)
        if not validation.get("pass"):
            raise ValueError("Sol-final corrected run validation failed")

        latest = runner.initial_latest(plan, state="completed")
        runner.update_run_counters(latest, plan, manifests, ledger, current_work={}, failures=[])
        latest.update(
            {
                "state": "completed",
                "stage": "completed",
                "updatedAt": now_iso(),
                "lastHeartbeatAt": now_iso(),
                "candidateTokens": candidate_count,
                "correctedFromRunId": args.source_run,
                "solFinalCorrection": {
                    "networkRequests": 0,
                    "removedOutsideWindowEvents": sum(removed_by_network.values()),
                    "duplicateLedgerRowsRemoved": duplicate_ledger_rows,
                    "resultCapResponsesReclassified": result_cap_rows,
                },
            }
        )
        finalization = {
            "schemaVersion": "convexity-gate0-sol-finalization-v1",
            "runId": run_id,
            "correctedFromRunId": args.source_run,
            "finishedAt": now_iso(),
            "networkRequests": 0,
            "events": validation["eventCount"],
            "candidateTokens": candidate_count,
            "candidateTokensByNetwork": candidate_counts,
            "removedOutsideWindowEvents": sum(removed_by_network.values()),
            "removedOutsideWindowByNetwork": removed_by_network,
            "duplicateLedgerRowsRemoved": duplicate_ledger_rows,
            "resultCapResponsesReclassified": result_cap_rows,
            "validationPass": True,
        }
        runner.atomic_write_json(building / "sol-finalization.json", finalization)
        for partition in plan["partitions"]:
            reuse = partition.get("reuse") or {}
            event_path = reuse.get("eventPath")
            if event_path and runner.project_path(event_path).is_relative_to(building):
                final_event = target_run / runner.project_path(event_path).relative_to(building)
                reuse["eventPath"] = runner.project_relative(final_event)
        summary["candidatePath"] = str((target_run / "candidate-tokens.jsonl").resolve())
        runner.atomic_write_json(building / "run-plan.json", plan)
        runner.atomic_write_json(building / "summary.json", summary)
        building.replace(target_run)
        runner.atomic_write_json(runner.LATEST_PATH, latest)
        runner.maybe_build_progress(latest, plan, force=True)
        print(json.dumps(finalization, ensure_ascii=False, indent=2))
        return 0
    finally:
        runner.release_lock(lock_payload)


if __name__ == "__main__":
    raise SystemExit(main())
