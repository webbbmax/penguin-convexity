#!/usr/bin/env python3
"""Independent, resumable Gate 0 historical backfill worker.

This module deliberately keeps the existing Gate 0 protocol decoders in
``gate0_dex_factory_backfill`` and adds only runner mechanics: immutable run
plans, bounded partitions, checkpoints, a single-instance lock, validation and
progress aggregation.  It never calls ``persist_run`` and therefore never
touches the accepted/legacy backfill outputs or the product database.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sys
import time
import uuid

from gate0_shadow_preflight import atomic_write_json, utc_now
import gate0_dex_factory_backfill as core


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "gate0-dex-backfill.json"
SHADOW_CONFIG_PATH = PROJECT_ROOT / "config" / "gate0-shadow-scope.json"
BASELINE_PATH = PROJECT_ROOT / "docs" / "GATE0_BASELINE_MANIFEST.json"
BACKGROUND_ROOT = PROJECT_ROOT / "runtime" / "gate0-shadow" / "backfill" / "background"
PROGRESS_ROOT = PROJECT_ROOT / "reports" / "gate0-backfill-progress"
LATEST_PATH = BACKGROUND_ROOT / "latest.json"
LOCK_PATH = BACKGROUND_ROOT / "lock.json"

RUNNER_SCHEMA = "convexity-gate0-background-run-v1"
PLAN_SCHEMA = "convexity-gate0-background-plan-v1"
CHECKPOINT_SCHEMA = "convexity-gate0-background-checkpoint-v1"
MANIFEST_SCHEMA = "convexity-gate0-background-partition-complete-v1"
SPAN_HINT_SCHEMA = "convexity-gate0-background-span-hint-v1"
FROZEN_UNSUPPORTED_LABELS = (
    "arbitrum-mainnet:camelot",
    "arbitrum-mainnet:ekubo-v3-arbitrum",
    "arbitrum-mainnet:maverick-v2-arbitrum",
    "arbitrum-mainnet:uniswap-v2-arbitrum",
    "arbitrum-mainnet:uniswap-v4-arbitrum",
    "arbitrum-mainnet:uniswap_v3_arbitrum",
    "base-mainnet:aerodrome-base",
    "base-mainnet:aerodrome-slipstream-3",
    "base-mainnet:bankr",
    "base-mainnet:curve-base",
    "base-mainnet:mint-club-base",
    "base-mainnet:pancakeswap-infinity-clmm-base",
    "base-mainnet:pancakeswap-v2-base",
    "base-mainnet:uniswap-v3-base",
    "ethereum-mainnet:balancer-v3-ethereum",
    "ethereum-mainnet:dodo-pmm-ethereum",
    "ethereum-mainnet:pancakeswap-v3-ethereum",
    "ethereum-mainnet:uniswap_v3",
)

CLASSIFICATION_STATES = (
    "success",
    "no_data",
    "quota_limited",
    "source_failure",
    "unsupported",
    "configuration_missing",
    "program_failure",
)
RUN_STATES = {"preparing", "running", "quota_wait", "retrying", "paused", "completed", "failed"}
EVM_PARTITION_SPAN = 2_000_000
SOLANA_PARTITION_SPAN = 200_000
HEARTBEAT_STALE_SECONDS = 10 * 60
DEFAULT_RETRY_LIMIT = 3
DEFAULT_RETRY_DELAYS = (5, 15, 60)
_last_progress_emit = 0.0


def parse_utc(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def iso_at(value=None):
    if value is None:
        return utc_now()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_path(path):
    path = Path(path)
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def safe_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def project_path(value):
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def project_relative(path):
    return str(Path(path).resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")


def safe_slug(value):
    return core.safe_slug(value) or "unknown"


def run_id_now():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"gate0-backfill-{stamp}-{uuid.uuid4().hex[:8]}"


def classify_error(error):
    if isinstance(error, core.RpcError):
        kind = str(error.kind or "").lower()
        if kind in {"quota_limited", "quota", "rate_limit"}:
            return "quota_limited"
        if kind in {"configuration_missing", "missing_credential", "auth"}:
            return "configuration_missing"
        if kind in {"unsupported", "configuration"}:
            return "unsupported"
        return "source_failure"
    if isinstance(error, (KeyError, TypeError, ValueError, json.JSONDecodeError)):
        return "program_failure"
    if isinstance(error, FileNotFoundError):
        return "configuration_missing"
    return "source_failure"


def network_map(shadow_config):
    return {row["id"]: row for row in shadow_config.get("networks") or []}


def event_identity(event):
    network = event.get("networkId")
    if network == "solana-mainnet" or event.get("slot") is not None:
        return (
            network,
            event.get("slot") if event.get("slot") is not None else event.get("blockNumber"),
            event.get("transactionSignature") or event.get("transactionHash") or "",
            tuple(event.get("instructionAddress") or ()),
            event.get("schemaId") or "",
        )
    return (
        network,
        event.get("blockNumber"),
        event.get("transactionHash") or "",
        event.get("logIndex"),
        event.get("schemaId") or event.get("eventTopic") or "",
    )


def event_position(event, range_kind):
    value = event.get("slot") if range_kind == "slot" else event.get("blockNumber")
    if value is None:
        return None
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None


def event_timestamp(event):
    value = event.get("blockTimestamp")
    if value in (None, ""):
        return None
    try:
        if isinstance(value, str) and not value.isdigit():
            return parse_utc(value).timestamp()
        return float(value)
    except (TypeError, ValueError):
        return None


def event_in_fixed_window(event, plan):
    timestamp = event_timestamp(event)
    if timestamp is None:
        return True
    return parse_utc(plan["windowStart"]).timestamp() <= timestamp <= parse_utc(plan["windowEnd"]).timestamp()


def partition_paths(run_dir, partition):
    network = safe_slug(partition["networkId"])
    schema = safe_slug(partition["schemaId"])
    partition_id = safe_slug(partition["partitionId"])
    directory = Path(run_dir) / "partitions" / network / schema
    return (
        directory / f"{partition_id}.jsonl",
        directory / f"{partition_id}.complete.json",
        Path(run_dir) / "checkpoints" / f"{partition_id}.json",
    )


def failure_path(run_dir, partition):
    _, _, checkpoint = partition_paths(run_dir, partition)
    return checkpoint.with_name(checkpoint.stem + ".failure.json")


def write_request_ledger(run_dir, ledger, offset=0):
    rows = ledger.requests[offset:]
    if not rows:
        return offset
    path = Path(run_dir) / "request-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return offset + len(rows)


def load_request_ledger(run_dir, ledger):
    """Restore cumulative request accounting after a scheduled-task restart."""
    path = Path(run_dir) / "request-ledger.jsonl"
    if not path.exists():
        return 0
    loaded = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A torn ledger line is not used to fabricate a request.  The
                # partition checkpoint remains the recovery authority.
                continue
            if isinstance(row, dict):
                ledger.requests.append(row)
                loaded += 1
    return loaded


def request_summary(ledger):
    by_state = Counter()
    for row in ledger.requests:
        state = row.get("state") or "program_failure"
        by_state[state if state in CLASSIFICATION_STATES else "program_failure"] += 1
    return {
        "total": len(ledger.requests),
        "byState": {state: by_state.get(state, 0) for state in CLASSIFICATION_STATES},
    }


def annotate_new_requests(ledger, offset, partition, start, end):
    for row in ledger.requests[offset:]:
        row.setdefault("networkId", partition.get("networkId"))
        row.setdefault("schemaId", partition.get("schemaId"))
        row.setdefault("partitionId", partition.get("partitionId"))
        row.setdefault("rangeKind", partition.get("rangeKind") or "block")
        row.setdefault("fromBlockOrSlot", int(start))
        row.setdefault("toBlockOrSlot", int(end))


def accepted_overlap_request_count(plan, ledger_path):
    coverage = plan.get("acceptedSolanaCoverage") or {}
    accepted_start = coverage.get("fromBlockOrSlot")
    accepted_end = coverage.get("toBlockOrSlot")
    path = Path(ledger_path)
    if accepted_start is None or accepted_end is None or not path.exists():
        return 0
    overlaps = 0
    for row in iter_jsonl(path):
        if row.get("networkId") != "solana-mainnet":
            continue
        lower = row.get("fromBlockOrSlot")
        upper = row.get("toBlockOrSlot")
        if lower is None or upper is None:
            continue
        if int(lower) <= int(accepted_end) and int(upper) >= int(accepted_start):
            overlaps += 1
    return overlaps


def load_baseline():
    if not BASELINE_PATH.exists():
        return {"acceptedRunIds": [], "protectedFiles": {}}
    payload = safe_json(BASELINE_PATH)
    accepted = []
    accepted_run = payload.get("acceptedRun") or {}
    if accepted_run.get("runId"):
        accepted.append(str(accepted_run["runId"]))
    for key in ("acceptedRunId", "acceptedRunIds", "protectedRunIds"):
        value = payload.get(key)
        if isinstance(value, str):
            accepted.append(value)
        elif isinstance(value, list):
            accepted.extend(str(item) for item in value)
    protected = payload.get("protectedFiles") or {}
    if isinstance(protected, list):
        protected = {str(row.get("path")): row.get("sha256", "") for row in protected}
    accepted_files = accepted_run.get("files") or []
    if isinstance(accepted_files, list):
        for row in accepted_files:
            if isinstance(row, dict) and row.get("path") and row.get("sha256"):
                protected.setdefault(row["path"], row["sha256"])
    return {
        "acceptedRunIds": sorted(set(accepted)),
        "protectedFiles": protected,
        "acceptedRun": accepted_run,
    }


def accepted_solana_coverage(baseline=None):
    """Return and verify the frozen accepted Solana coverage descriptor."""
    baseline = baseline or load_baseline()
    accepted = baseline.get("acceptedRun") or {}
    run_id = accepted.get("runId")
    files = {row.get("path"): row for row in accepted.get("files") or [] if row.get("path")}
    summary_row = next((row for path, row in files.items() if path.endswith("/summary.json")), None)
    event_row = next(
        (
            row
            for path, row in files.items()
            if "/events/" in path and "solana-mainnet--registered-creation-schemas.jsonl" in path
        ),
        None,
    )
    if not run_id or not summary_row or not event_row:
        raise ValueError("accepted Solana baseline descriptor is incomplete")
    for row in (summary_row, event_row):
        path = project_path(row["path"])
        if not path.exists() or sha256_path(path) != row.get("sha256"):
            raise ValueError(f"accepted baseline hash mismatch: {row['path']}")
    summary = safe_json(project_path(summary_row["path"]))
    result = next(
        (row for row in summary.get("solanaScanResults") or [] if row.get("networkId") == "solana-mainnet"),
        None,
    )
    if not result or not result.get("complete") or int(result.get("decodeFailures") or 0):
        raise ValueError("accepted Solana baseline validation is not complete")
    start = result.get("coverageStartsAtBlockHeight")
    end = result.get("coverageEndsAtBlockHeight")
    if start is None or end is None or int(end) < int(start):
        raise ValueError("accepted Solana baseline range is invalid")
    return {
        "runId": run_id,
        "fromBlockOrSlot": int(start),
        "toBlockOrSlot": int(end),
        "eventPath": event_row["path"],
        "sha256": event_row["sha256"],
        "rowCount": int(result.get("events") or accepted.get("eventRows") or 0),
        "sourceState": "success",
        "eventIdentityUnique": True,
        "decodeFailures": 0,
        "minimumBlockOrSlot": int(start),
        "maximumBlockOrSlot": int(end),
        "minimumTimestamp": result.get("coverageStartsAt"),
        "maximumTimestamp": summary.get("finishedAt"),
    }


def observed_unsupported_labels(seed_run, registry, config, shadow_config):
    # Gate 0 freezes the unsupported boundary captured by the accepted
    # coverage rollup.  A later shadow-day label set must not silently widen
    # or reassign that formal acceptance scope.
    return list(FROZEN_UNSUPPORTED_LABELS)


def partition_row(
    partition_id,
    network_id,
    schema_id,
    source,
    start,
    end,
    weight,
    range_kind,
    schema_ids=None,
    planning_failure=None,
):
    row = {
        "partitionId": partition_id,
        "networkId": network_id,
        "schemaId": schema_id,
        "source": source,
        "fromBlockOrSlot": start,
        "toBlockOrSlot": end,
        "rangeKind": range_kind,
        "weight": max(1, int(weight or 1)),
        "state": "pending",
    }
    if schema_ids:
        row["schemaIds"] = list(schema_ids)
    if planning_failure:
        row["planningFailure"] = planning_failure
    return row


def add_range_partitions(partitions, network_id, schema_id, source, start, end, span, range_kind):
    if start is None or end is None or int(end) < int(start):
        return
    start = int(start)
    end = int(end)
    span = max(1, int(span))
    for number in range(start, end + 1, span):
        upper = min(end, number + span - 1)
        partition_id = (
            f"{safe_slug(network_id)}-{safe_slug(schema_id)}-"
            f"{range_kind}-{number}-{upper}"
        )
        partitions.append(
            partition_row(
                partition_id,
                network_id,
                schema_id,
                source,
                number,
                upper,
                upper - number + 1,
                range_kind,
            )
        )


def add_solana_range_partitions(partitions, start, end, source, schema_ids, span=SOLANA_PARTITION_SPAN):
    if start is None or end is None or int(end) < int(start):
        return
    for number in range(int(start), int(end) + 1, int(span)):
        upper = min(int(end), number + int(span) - 1)
        partitions.append(
            partition_row(
                f"solana-creation-schemas-block-{number}-{upper}",
                "solana-mainnet",
                "solana-creation-schemas",
                source,
                number,
                upper,
                upper - number + 1,
                "block",
                schema_ids=schema_ids,
            )
        )


def reuse_partition(partition, run_id, event_path, manifest, kind):
    row = dict(partition)
    row["state"] = "reused"
    row["reuse"] = {
        "kind": kind,
        "runId": run_id,
        "eventPath": project_relative(event_path),
        "sha256": manifest["sha256"],
        "rowCount": int(manifest.get("rowCount") or 0),
        "sourceState": manifest.get("sourceState") or "success",
        "eventIdentityUnique": bool(manifest.get("eventIdentityUnique", True)),
        "decodeFailures": int(manifest.get("decodeFailures") or 0),
        "minimumBlockOrSlot": manifest.get("minimumBlockOrSlot"),
        "maximumBlockOrSlot": manifest.get("maximumBlockOrSlot"),
        "minimumTimestamp": manifest.get("minimumTimestamp"),
        "maximumTimestamp": manifest.get("maximumTimestamp"),
    }
    return row


def uncovered_ranges(start, end, covered):
    merged = []
    for lower, upper in sorted((max(int(start), int(a)), min(int(end), int(b))) for a, b in covered):
        if upper < lower:
            continue
        if merged and lower <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))
    gaps = []
    cursor = int(start)
    for lower, upper in merged:
        if cursor < lower:
            gaps.append((cursor, lower - 1))
        cursor = max(cursor, upper + 1)
    if cursor <= int(end):
        gaps.append((cursor, int(end)))
    return gaps


def planning_failure_partitions(partitions, network_id, schema_id, source, range_kind, error):
    partition_id = f"{safe_slug(network_id)}-{safe_slug(schema_id)}-{range_kind}-planning"
    partitions.append(
        partition_row(
            partition_id,
            network_id,
            schema_id,
            source,
            None,
            None,
            1,
            range_kind,
            planning_failure={"state": classify_error(error), "error": str(error)[:500]},
        )
    )


def config_hashes(config_path, shadow_config_path, registry_path):
    return {
        "config": sha256_path(config_path),
        "shadowConfig": sha256_path(shadow_config_path),
        "schemaRegistry": sha256_path(registry_path),
        "baselineManifest": sha256_path(BASELINE_PATH),
    }


def build_partition_progress(partitions, completed=None):
    completed = completed or set()
    total_weight = sum(int(row.get("weight") or 1) for row in partitions)
    completed_weight = sum(
        int(row.get("weight") or 1) for row in partitions if row.get("partitionId") in completed
    )
    return {
        "completedCount": len(completed & {row["partitionId"] for row in partitions}),
        "totalCount": len(partitions),
        "completedWeight": completed_weight,
        "totalWeight": total_weight,
    }


def build_network_progress(plan, completed_manifests=None):
    completed_manifests = completed_manifests or {}
    by_network = defaultdict(list)
    for row in plan.get("partitions") or []:
        by_network[row["networkId"]].append(row)
    rows = []
    for network_id in plan.get("selectedNetworks") or []:
        partitions = by_network.get(network_id, [])
        done = {row["partitionId"] for row in partitions if row["partitionId"] in completed_manifests}
        rows.append(
            {
                "networkId": network_id,
                "partitions": build_partition_progress(partitions, done),
                "schemas": sorted({row["schemaId"] for row in partitions}),
                "state": (
                    "unsupported" if not partitions else
                    "completed" if len(done) == len(partitions) else "running"
                ),
            }
        )
    return rows


def initial_latest(plan, state="preparing"):
    return {
        "schemaVersion": RUNNER_SCHEMA,
        "runId": plan["runId"],
        "state": state,
        "stage": "plan_created",
        "startedAt": plan["createdAt"],
        "updatedAt": plan["createdAt"],
        "lastHeartbeatAt": plan["createdAt"],
        "windowStart": plan["windowStart"],
        "windowEnd": plan["windowEnd"],
        "windowDays": plan["windowDays"],
        "partitionProgress": build_partition_progress(plan["partitions"]),
        "networkProgress": build_network_progress(plan),
        "schemaCoverage": {"supported": plan.get("selectedSchemas") or [], "unsupported": plan.get("unsupportedDexLabels") or []},
        "requests": {"total": 0, "byState": {state: 0 for state in CLASSIFICATION_STATES}},
        "events": 0,
        "candidateTokens": 0,
        "currentWork": {},
        "lastCheckpoint": {},
        "recoveryCount": 0,
        "failureSummary": {},
        "eta": {"available": False, "reason": "need_five_stable_chunks"},
        "boundary": {
            "projectMinimumWaitDays": 0,
            "allProjectAgesUseSourceHistory": True,
            "marketWideComplete": False,
            "usableAsGlobalT0": False,
        },
    }


def make_plan(config_path=CONFIG_PATH, shadow_config_path=SHADOW_CONFIG_PATH, networks=None):
    config = core.load_config(config_path)
    shadow = safe_json(shadow_config_path)
    registry = core.load_schema_registry(config)
    seed_path = PROJECT_ROOT / config["seedRun"]
    seed_run = safe_json(seed_path)
    all_networks = network_map(shadow)
    selected = [network_id for network_id in all_networks if not networks or network_id in set(networks)]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    window_start = now - timedelta(days=int(config["windowDays"]))
    run_id = run_id_now()
    run_dir = BACKGROUND_ROOT / "runs" / run_id
    partitions = []
    selected_schemas = []
    planning_ledger = core.RequestLedger(timeout=int(config["evm"].get("rpcTimeoutSeconds", 30)))
    registry_rows = registry.get("schemas") or []
    for network_id in selected:
        network = all_networks[network_id]
        if network.get("chainType") == "EVM":
            schemas = [row for row in registry_rows if row.get("networkId") == network_id]
            selected_schemas.extend(f"{network_id}:{row.get('dexIds', [''])[0]}" for row in schemas)
            if not schemas:
                continue
            try:
                client, scan_settings = core.historical_log_client(network, config, planning_ledger)
                if client is None:
                    raise core.RpcError("no configured historical source", kind="unsupported")
                if isinstance(client, core.BlockscoutLogClient):
                    window_range = client.range_for_window(int(window_start.timestamp()))
                else:
                    window_range = client.range_for_window(int(window_start.timestamp()), scan_settings)
                for schema in schemas:
                    add_range_partitions(
                        partitions,
                        network_id,
                        schema["dexIds"][0],
                        scan_settings.get("historicalSource", "historical_log_source"),
                        window_range.get("fromBlock"),
                        window_range.get("toBlock"),
                        EVM_PARTITION_SPAN,
                        "block",
                    )
            except Exception as error:
                failure = {"state": classify_error(error), "error": str(error)[:500]}
                for schema in schemas:
                    planning_failure_partitions(
                        partitions,
                        network_id,
                        schema["dexIds"][0],
                        "historical_log_source",
                        "block",
                        error,
                    )
                selected_schemas.append(f"{network_id}:planning_failure:{failure['state']}")
        elif network.get("chainType") == "SOLANA":
            schemas = list(config.get("solana", {}).get("creationSchemas") or [])
            schema_ids = [row["id"] for row in schemas]
            selected_schemas.extend(f"{network_id}:{schema_id}" for schema_id in schema_ids)
            if not schemas:
                continue
            try:
                settings = config["solana"]
                client = core.SqdSolanaArchiveClient(settings, planning_ledger)
                use_portal = bool(settings.get("portalFullHistoryEnabled"))
                source = "sqd_portal_finalized_stream" if use_portal else "sqd_legacy_v2_archive"
                if use_portal:
                    start_map = client.latest_archive_height_at_or_before(int(window_start.timestamp()))
                    end_map = client.latest_archive_height_at_or_before(int(now.timestamp()))
                    start = start_map["slot"]
                    end = end_map["slot"]
                    range_kind = "slot"
                else:
                    start = client.archive_height_at_timestamp(
                        int(window_start.timestamp()) - int(settings.get("legacyArchiveStartSafetySeconds", 0))
                    )["height"]
                    end = client.finalized_height()
                    range_kind = "block"
                partition_id_prefix = "solana-creation-schemas"
                # The source query remains bounded by the existing decoder
                # settings, but the resumable runner owns a larger parent
                # partition so a 90-day run does not create one thousand tiny
                # scheduled units.
                span = SOLANA_PARTITION_SPAN
                for number in range(int(start), int(end) + 1, span):
                    upper = min(int(end), number + span - 1)
                    partition_id = f"{partition_id_prefix}-{range_kind}-{number}-{upper}"
                    partitions.append(
                        partition_row(
                            partition_id,
                            network_id,
                            partition_id_prefix,
                            source,
                            number,
                            upper,
                            upper - number + 1,
                            range_kind,
                            schema_ids=schema_ids,
                        )
                    )
            except Exception as error:
                planning_failure_partitions(
                    partitions,
                    network_id,
                    "solana-creation-schemas",
                    "sqd_archive",
                    "block",
                    error,
                )
    baseline = load_baseline()
    registry_path = PROJECT_ROOT / config["schemaRegistry"]
    plan = {
        "schemaVersion": PLAN_SCHEMA,
        "runId": run_id,
        "createdAt": iso_at(now),
        "windowStart": iso_at(window_start),
        "windowEnd": iso_at(now),
        "windowDays": int(config["windowDays"]),
        "selectedNetworks": selected,
        "selectedSchemas": sorted(set(selected_schemas)),
        "unsupportedDexLabels": observed_unsupported_labels(seed_run, registry, config, shadow),
        "configHashes": config_hashes(config_path, shadow_config_path, registry_path),
        "baselineRunIds": baseline["acceptedRunIds"],
        "baselineFileHashes": baseline["protectedFiles"],
        "partitions": partitions,
        "runnerParameters": {
            "evmPartitionSpan": EVM_PARTITION_SPAN,
            "solanaPartitionSpan": SOLANA_PARTITION_SPAN,
            "heartbeatStaleSeconds": HEARTBEAT_STALE_SECONDS,
        },
        "boundary": {
            "productCodeWritesAllowed": False,
            "productionDatabaseWritesAllowed": False,
            "existingSchedulerChangesAllowed": False,
            "projectMinimumWaitDays": 0,
            "allProjectAgesUseSourceHistory": True,
            "shortHistorySyntheticDaysAllowed": False,
            "marketWideCoverageGuaranteed": False,
            "usableAsGlobalT0": False,
        },
        "planningRequestCount": len(planning_ledger.requests),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "run-plan.json", plan)
    write_request_ledger(run_dir, planning_ledger, 0)
    latest = initial_latest(plan)
    atomic_write_json(LATEST_PATH, latest)
    return plan, config, shadow, registry, run_dir


def make_corrected_plan(source_run_id, config_path=CONFIG_PATH, shadow_config_path=SHADOW_CONFIG_PATH):
    """Create a fixed-window run that reuses verified completed coverage.

    The source run remains immutable forensic evidence.  The new plan references
    only fully non-overlapping completed source partitions plus the frozen
    accepted Solana interval, then schedules network requests for the gaps.
    """
    source_run_dir = BACKGROUND_ROOT / "runs" / str(source_run_id)
    source_plan_path = source_run_dir / "run-plan.json"
    if not source_plan_path.exists():
        raise FileNotFoundError(f"source run plan not found: {source_run_id}")
    source_plan = safe_json(source_plan_path)
    if source_plan.get("schemaVersion") != PLAN_SCHEMA:
        raise ValueError("source run plan schema is not supported")
    config = core.load_config(config_path)
    shadow = safe_json(shadow_config_path)
    registry = core.load_schema_registry(config)
    current_hashes = config_hashes(config_path, shadow_config_path, PROJECT_ROOT / config["schemaRegistry"])
    for key in ("config", "shadowConfig", "schemaRegistry", "baselineManifest"):
        if source_plan.get("configHashes", {}).get(key) != current_hashes.get(key):
            raise ValueError(f"fixed source plan {key} hash no longer matches")

    source_manifests = existing_complete_manifests(source_plan, source_run_dir)
    baseline = load_baseline()
    accepted = accepted_solana_coverage(baseline)
    source_solana = [
        row for row in source_plan.get("partitions") or []
        if row.get("networkId") == "solana-mainnet" and row.get("fromBlockOrSlot") is not None
    ]
    if not source_solana:
        raise ValueError("source run has no Solana target range")
    target_start = min(int(row["fromBlockOrSlot"]) for row in source_solana)
    target_end = max(int(row["toBlockOrSlot"]) for row in source_solana)
    accepted_start = int(accepted["fromBlockOrSlot"])
    accepted_end = int(accepted["toBlockOrSlot"])
    if not (target_start <= accepted_start <= accepted_end <= target_end):
        raise ValueError("fixed target window does not fully contain the accepted Solana baseline")

    partitions = []
    reused_coverage = []
    for partition in source_plan.get("partitions") or []:
        if partition.get("networkId") == "solana-mainnet":
            continue
        manifest = source_manifests.get(partition["partitionId"])
        if manifest:
            event_path, _, _ = partition_paths(source_run_dir, partition)
            partitions.append(reuse_partition(partition, source_run_id, event_path, manifest, "completed_partition"))
            reused_coverage.append(
                {
                    "networkId": partition["networkId"],
                    "fromBlockOrSlot": partition.get("fromBlockOrSlot"),
                    "toBlockOrSlot": partition.get("toBlockOrSlot"),
                    "kind": "completed_partition",
                    "runId": source_run_id,
                }
            )
        else:
            partitions.append(dict(partition))

    covered = []
    for partition in source_solana:
        manifest = source_manifests.get(partition["partitionId"])
        if not manifest:
            continue
        lower = int(partition["fromBlockOrSlot"])
        upper = int(partition["toBlockOrSlot"])
        if not (upper < accepted_start or lower > accepted_end):
            continue
        event_path, _, _ = partition_paths(source_run_dir, partition)
        partitions.append(reuse_partition(partition, source_run_id, event_path, manifest, "completed_partition"))
        covered.append((lower, upper))
        reused_coverage.append(
            {
                "networkId": "solana-mainnet",
                "fromBlockOrSlot": lower,
                "toBlockOrSlot": upper,
                "kind": "completed_partition",
                "runId": source_run_id,
            }
        )

    schema_ids = list(source_solana[0].get("schemaIds") or [])
    accepted_partition = partition_row(
        f"solana-accepted-read-only-block-{accepted_start}-{accepted_end}",
        "solana-mainnet",
        "solana-creation-schemas",
        "accepted_read_only_baseline",
        accepted_start,
        accepted_end,
        accepted_end - accepted_start + 1,
        "block",
        schema_ids=schema_ids,
    )
    accepted_manifest = {key: accepted.get(key) for key in (
        "sha256", "rowCount", "sourceState", "eventIdentityUnique", "decodeFailures",
        "minimumBlockOrSlot", "maximumBlockOrSlot", "minimumTimestamp", "maximumTimestamp",
    )}
    partitions.append(
        reuse_partition(
            accepted_partition,
            accepted["runId"],
            project_path(accepted["eventPath"]),
            accepted_manifest,
            "accepted_baseline",
        )
    )
    covered.append((accepted_start, accepted_end))
    reused_coverage.append(
        {
            "networkId": "solana-mainnet",
            "fromBlockOrSlot": accepted_start,
            "toBlockOrSlot": accepted_end,
            "kind": "accepted_baseline",
            "runId": accepted["runId"],
        }
    )
    source = source_solana[0].get("source") or "sqd_legacy_v2_archive"
    for lower, upper in uncovered_ranges(target_start, target_end, covered):
        add_solana_range_partitions(partitions, lower, upper, source, schema_ids)

    run_id = run_id_now()
    run_dir = BACKGROUND_ROOT / "runs" / run_id
    plan = {
        **{key: source_plan.get(key) for key in (
            "windowStart", "windowEnd", "windowDays", "selectedNetworks", "selectedSchemas",
            "unsupportedDexLabels", "boundary",
        )},
        "schemaVersion": PLAN_SCHEMA,
        "runId": run_id,
        "createdAt": utc_now(),
        "configHashes": current_hashes,
        "baselineRunIds": baseline["acceptedRunIds"],
        "baselineFileHashes": baseline["protectedFiles"],
        "partitions": partitions,
        "runnerParameters": dict(source_plan.get("runnerParameters") or {}),
        "planningRequestCount": 0,
        "correctedFromRunId": source_run_id,
        "correctionReason": "accepted_29_day_overlap_was_rescanned_and_runner_ignored_read_only_reuse",
        "sourcePlanSha256": sha256_path(source_plan_path),
        "reusedCoverage": reused_coverage,
        "acceptedSolanaCoverage": {
            "runId": accepted["runId"],
            "fromBlockOrSlot": accepted_start,
            "toBlockOrSlot": accepted_end,
            "duplicateNetworkRequestsAllowed": 0,
        },
    }
    pending_solana = [
        row for row in partitions
        if row.get("networkId") == "solana-mainnet" and not row.get("reuse")
    ]
    if any(
        int(row["fromBlockOrSlot"]) <= accepted_end and int(row["toBlockOrSlot"]) >= accepted_start
        for row in pending_solana
    ):
        raise ValueError("corrected plan still schedules accepted Solana overlap")
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "run-plan.json", plan)
    materialize_reuse_manifests(plan, run_dir)
    latest = initial_latest(plan)
    latest["stage"] = "corrected_plan_created"
    latest["correctedFromRunId"] = source_run_id
    latest["reusedCoverage"] = reused_coverage
    ledger = core.RequestLedger(timeout=int(config["evm"].get("rpcTimeoutSeconds", 30)))
    manifests = existing_complete_manifests(plan, run_dir)
    update_run_counters(latest, plan, manifests, ledger)
    atomic_write_json(LATEST_PATH, latest)
    maybe_build_progress(latest, plan, force=True)
    return plan, config, shadow, registry, run_dir


def load_plan_from_latest():
    latest = safe_json(LATEST_PATH)
    run_dir = BACKGROUND_ROOT / "runs" / latest["runId"]
    plan = safe_json(run_dir / "run-plan.json")
    return plan, run_dir, latest


def iter_jsonl(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {error}") from error


def read_jsonl(path):
    return list(iter_jsonl(path))


def acquire_lock(run_id):
    BACKGROUND_ROOT.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if LOCK_PATH.exists():
        try:
            existing = safe_json(LOCK_PATH)
            heartbeat = parse_utc(existing.get("lastHeartbeatAt") or existing.get("acquiredAt"))
            stale = now - heartbeat.timestamp() > HEARTBEAT_STALE_SECONDS
        except (OSError, ValueError, json.JSONDecodeError):
            stale = True
        if not stale:
            return None
        stale_path = LOCK_PATH.with_name(f"lock.stale.{int(now)}.json")
        try:
            LOCK_PATH.replace(stale_path)
        except FileNotFoundError:
            pass
    payload = {
        "schemaVersion": "convexity-gate0-background-lock-v1",
        "runId": run_id,
        "workerId": f"{platform.node()}:{os.getpid()}:{uuid.uuid4().hex[:8]}",
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "acquiredAt": utc_now(),
        "lastHeartbeatAt": utc_now(),
    }
    try:
        with LOCK_PATH.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    except FileExistsError:
        return None
    return payload


def release_lock(lock_payload):
    if not lock_payload or not LOCK_PATH.exists():
        return
    try:
        current = safe_json(LOCK_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        return
    if current.get("workerId") == lock_payload.get("workerId"):
        LOCK_PATH.unlink(missing_ok=True)


def touch_lock(lock_payload):
    if not lock_payload:
        return
    lock_payload["lastHeartbeatAt"] = utc_now()
    atomic_write_json(LOCK_PATH, lock_payload)


def maybe_build_progress(latest, plan, force=False):
    """Keep the fixed progress artifact fresh without making it a worker gate."""
    global _last_progress_emit
    now = time.monotonic()
    if not force and now - _last_progress_emit < 30:
        return
    try:
        import build_gate0_backfill_progress as progress_builder

        artifact = progress_builder.build_artifact(latest, plan)
        PROGRESS_ROOT.mkdir(parents=True, exist_ok=True)
        # The worker and a user/browser may read these files concurrently.
        # Publish each complete artifact atomically so progress never appears
        # as a half-written JSON/HTML document.
        progress_builder.write_atomic_text(
            PROGRESS_ROOT / "artifact.json",
            json.dumps(artifact, ensure_ascii=False, indent=2),
        )
        progress_builder.write_atomic_text(
            PROGRESS_ROOT / "report.html", progress_builder.render(artifact)
        )
        _last_progress_emit = now
    except Exception:
        # Progress rendering is observability only; a rendering problem must
        # never turn a source result into a backfill failure.
        return


def load_checkpoint(run_dir, partition, worker_id):
    _, _, path = partition_paths(run_dir, partition)
    if not path.exists():
        return {
            "schemaVersion": CHECKPOINT_SCHEMA,
            "workerId": worker_id,
            "partitionId": partition["partitionId"],
            "nextBlockOrSlot": partition.get("fromBlockOrSlot"),
            "lastSuccessfulAt": None,
            "lastHeartbeatAt": utc_now(),
            "requests": 0,
            "events": 0,
            "retryCount": 0,
            "recoveryCount": 0,
            "state": "pending",
            "lastFailure": {},
        }
    try:
        checkpoint = safe_json(path)
        checkpoint["recoveryCount"] = int(checkpoint.get("recoveryCount") or 0) + 1
        checkpoint["workerId"] = worker_id
        checkpoint["lastHeartbeatAt"] = utc_now()
        return checkpoint
    except (OSError, ValueError, json.JSONDecodeError) as error:
        corrupt = path.with_name(path.name + f".corrupt.{int(time.time())}")
        try:
            path.replace(corrupt)
        except OSError:
            pass
        return {
            "schemaVersion": CHECKPOINT_SCHEMA,
            "workerId": worker_id,
            "partitionId": partition["partitionId"],
            "nextBlockOrSlot": partition.get("fromBlockOrSlot"),
            "lastSuccessfulAt": None,
            "lastHeartbeatAt": utc_now(),
            "requests": 0,
            "events": 0,
            "retryCount": 0,
            "recoveryCount": 1,
            "state": "program_failure",
            "lastFailure": {"state": "program_failure", "error": str(error)[:500]},
        }


def save_checkpoint(run_dir, partition, checkpoint):
    _, _, path = partition_paths(run_dir, partition)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint["schemaVersion"] = CHECKPOINT_SCHEMA
    checkpoint["lastHeartbeatAt"] = utc_now()
    atomic_write_json(path, checkpoint)


def span_hint_path(run_dir, partition):
    name = f"{safe_slug(partition['networkId'])}--{safe_slug(partition['schemaId'])}.json"
    return Path(run_dir) / "checkpoints" / "span-hints" / name


def load_span_hint(run_dir, partition, default_span, minimum_span, maximum_span):
    default_span = max(int(minimum_span), min(int(maximum_span), int(default_span)))
    path = span_hint_path(run_dir, partition)
    if not path.exists():
        return default_span
    try:
        payload = safe_json(path)
        if payload.get("schemaVersion") != SPAN_HINT_SCHEMA:
            return default_span
        value = int(payload.get("blockSpan"))
        return max(int(minimum_span), min(int(maximum_span), value))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return default_span


def save_span_hint(run_dir, partition, block_span, reason):
    path = span_hint_path(run_dir, partition)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        {
            "schemaVersion": SPAN_HINT_SCHEMA,
            "networkId": partition["networkId"],
            "schemaId": partition["schemaId"],
            "blockSpan": max(1, int(block_span)),
            "reason": str(reason),
            "updatedAt": utc_now(),
        },
    )


def update_latest(latest, plan, lock_payload, **changes):
    latest.update(changes)
    latest["updatedAt"] = utc_now()
    latest["lastHeartbeatAt"] = latest["updatedAt"]
    atomic_write_json(LATEST_PATH, latest)
    touch_lock(lock_payload)
    maybe_build_progress(latest, plan)


def event_file_path(run_dir, partition):
    reuse = partition.get("reuse") or {}
    if reuse.get("eventPath"):
        return project_path(reuse["eventPath"])
    return partition_paths(run_dir, partition)[0]


def materialize_reuse_manifests(plan, run_dir):
    for partition in plan.get("partitions") or []:
        reuse = partition.get("reuse") or {}
        if not reuse:
            continue
        source_path = event_file_path(run_dir, partition)
        expected = reuse.get("sha256")
        if not source_path.exists() or not expected or sha256_path(source_path) != expected:
            raise ValueError(f"reuse artifact hash mismatch: {partition['partitionId']}")
        _, complete, _ = partition_paths(run_dir, partition)
        manifest = {
            "schemaVersion": MANIFEST_SCHEMA,
            "partitionId": partition["partitionId"],
            "completedAt": utc_now(),
            "sourceState": reuse.get("sourceState") or "success",
            "rowCount": int(reuse.get("rowCount") or 0),
            "eventIdentityUnique": bool(reuse.get("eventIdentityUnique", True)),
            "decodeFailures": int(reuse.get("decodeFailures") or 0),
            "minimumBlockOrSlot": reuse.get("minimumBlockOrSlot"),
            "maximumBlockOrSlot": reuse.get("maximumBlockOrSlot"),
            "minimumTimestamp": reuse.get("minimumTimestamp"),
            "maximumTimestamp": reuse.get("maximumTimestamp"),
            "sha256": expected,
            "requestCount": 0,
            "reuse": {
                "kind": reuse.get("kind"),
                "runId": reuse.get("runId"),
                "eventPath": reuse.get("eventPath"),
            },
        }
        atomic_write_json(complete, manifest)


def existing_complete_manifests(plan, run_dir):
    found = {}
    for partition in plan.get("partitions") or []:
        _, complete, _ = partition_paths(run_dir, partition)
        if not complete.exists():
            continue
        try:
            manifest = safe_json(complete)
            data_path = event_file_path(run_dir, partition)
            if (
                manifest.get("schemaVersion") == MANIFEST_SCHEMA
                and data_path.exists()
                and manifest.get("sha256")
                and sha256_path(data_path) == manifest.get("sha256")
            ):
                found[partition["partitionId"]] = manifest
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return found


def load_existing_seen(path):
    if not Path(path).exists():
        return set(), 0
    seen = set()
    count = 0
    for event in read_jsonl(path):
        identity = event_identity(event)
        if identity in seen:
            continue
        seen.add(identity)
        count += 1
    return seen, count


def append_events(handle, events, seen):
    added = 0
    for event in events:
        identity = event_identity(event)
        if identity in seen:
            continue
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        seen.add(identity)
        added += 1
    handle.flush()
    try:
        os.fsync(handle.fileno())
    except OSError:
        pass
    return added


def retry_delay(retry_count, delays=DEFAULT_RETRY_DELAYS):
    if not delays:
        return 0
    return int(delays[min(max(0, int(retry_count) - 1), len(delays) - 1)])


def partition_failure(run_dir, partition, checkpoint, state, error):
    checkpoint["state"] = state
    checkpoint["lastFailure"] = {"state": state, "error": str(error)[:500], "at": utc_now()}
    save_checkpoint(run_dir, partition, checkpoint)
    atomic_write_json(
        failure_path(run_dir, partition),
        {
            "schemaVersion": "convexity-gate0-background-partition-failure-v1",
            "partitionId": partition["partitionId"],
            "state": state,
            "error": str(error)[:500],
            "updatedAt": utc_now(),
        },
    )
    return {"partitionId": partition["partitionId"], "state": state, "error": str(error)[:500]}


def validate_rows(path, partition, plan):
    rows = read_jsonl(path) if Path(path).exists() else []
    seen = set()
    duplicate = False
    decode_failures = 0
    minimum = None
    maximum = None
    minimum_timestamp = None
    maximum_timestamp = None
    errors = []
    start = partition.get("fromBlockOrSlot")
    end = partition.get("toBlockOrSlot")
    range_kind = partition.get("rangeKind", "block")
    end_timestamp = parse_utc(plan["windowEnd"]).timestamp()
    start_timestamp = parse_utc(plan["windowStart"]).timestamp()
    for event in rows:
        identity = event_identity(event)
        if identity in seen:
            duplicate = True
        seen.add(identity)
        if event.get("decodeComplete") is False:
            decode_failures += 1
        if not event.get("poolId"):
            errors.append("missing_pool")
        if not [token for token in (event.get("tokenAddresses") or []) if token]:
            errors.append("missing_token")
        position = event_position(event, range_kind)
        if position is None:
            errors.append("missing_block_or_slot")
        elif start is not None and (position < int(start) or position > int(end)):
            errors.append("out_of_partition_range")
        timestamp = event_timestamp(event)
        if timestamp is None:
            errors.append("missing_timestamp")
        else:
            minimum_timestamp = timestamp if minimum_timestamp is None else min(minimum_timestamp, timestamp)
            maximum_timestamp = timestamp if maximum_timestamp is None else max(maximum_timestamp, timestamp)
            if timestamp < start_timestamp or timestamp > end_timestamp:
                errors.append("out_of_window_timestamp")
            if timestamp > end_timestamp + 300:
                errors.append("future_timestamp")
        if position is not None:
            minimum = position if minimum is None else min(minimum, position)
            maximum = position if maximum is None else max(maximum, position)
    if duplicate:
        errors.append("duplicate_event_identity")
    return {
        "rows": rows,
        "rowCount": len(rows),
        "eventIdentityUnique": not duplicate,
        "decodeFailures": decode_failures,
        "minimumBlockOrSlot": minimum,
        "maximumBlockOrSlot": maximum,
        "minimumTimestamp": datetime.fromtimestamp(minimum_timestamp, timezone.utc).isoformat().replace("+00:00", "Z") if minimum_timestamp is not None else None,
        "maximumTimestamp": datetime.fromtimestamp(maximum_timestamp, timezone.utc).isoformat().replace("+00:00", "Z") if maximum_timestamp is not None else None,
        "errors": sorted(set(errors)),
    }


def complete_partition(run_dir, partition, checkpoint, plan, request_count):
    path, complete, _ = partition_paths(run_dir, partition)
    validation = validate_rows(path, partition, plan)
    valid = not validation["errors"] and validation["decodeFailures"] == 0
    if not valid:
        return None, validation
    source_state = "no_data" if validation["rowCount"] == 0 else "success"
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA,
        "partitionId": partition["partitionId"],
        "completedAt": utc_now(),
        "sourceState": source_state,
        "rowCount": validation["rowCount"],
        "eventIdentityUnique": validation["eventIdentityUnique"],
        "decodeFailures": validation["decodeFailures"],
        "minimumBlockOrSlot": validation["minimumBlockOrSlot"],
        "maximumBlockOrSlot": validation["maximumBlockOrSlot"],
        "minimumTimestamp": validation["minimumTimestamp"],
        "maximumTimestamp": validation["maximumTimestamp"],
        "sha256": sha256_path(path),
        "requestCount": int(request_count),
    }
    atomic_write_json(complete, manifest)
    checkpoint["state"] = source_state
    checkpoint["nextBlockOrSlot"] = (partition.get("toBlockOrSlot") or 0) + 1
    checkpoint["lastSuccessfulAt"] = utc_now()
    checkpoint["lastFailure"] = {}
    save_checkpoint(run_dir, partition, checkpoint)
    return manifest, validation


def update_run_counters(latest, plan, manifests, ledger, current_work=None, failures=None):
    latest["partitionProgress"] = build_partition_progress(plan["partitions"], set(manifests))
    network_partitions = [row for row in plan["partitions"] if not row.get("reuse")]
    reused_partitions = [row for row in plan["partitions"] if row.get("reuse")]
    latest["networkRequestProgress"] = build_partition_progress(network_partitions, set(manifests))
    latest["reuseProgress"] = build_partition_progress(reused_partitions, set(manifests))
    latest["networkProgress"] = build_network_progress(plan, manifests)
    latest["requests"] = request_summary(ledger)
    latest["events"] = sum(int(row.get("rowCount") or 0) for row in manifests.values())
    latest["currentWork"] = current_work or {}
    failure_counts = Counter(row.get("state") for row in (failures or []) if row.get("state"))
    latest["failureSummary"] = {state: failure_counts.get(state, 0) for state in CLASSIFICATION_STATES if failure_counts.get(state)}


def scan_evm_partition(
    run_dir,
    partition,
    plan,
    config,
    ledger,
    worker_id,
    latest,
    lock_payload,
    max_retries=DEFAULT_RETRY_LIMIT,
    no_sleep=False,
):
    network_id = partition["networkId"]
    schema_id = partition["schemaId"]
    registry = core.load_schema_registry(config)
    schema = next(
        (
            row
            for row in registry.get("schemas") or []
            if row.get("networkId") == network_id and schema_id in (row.get("dexIds") or [])
        ),
        None,
    )
    if schema is None:
        return None, partition_failure(run_dir, partition, load_checkpoint(run_dir, partition, worker_id), "unsupported", "schema is not in the verified registry")
    checkpoint = load_checkpoint(run_dir, partition, worker_id)
    if partition.get("planningFailure"):
        state = partition["planningFailure"].get("state", "source_failure")
        return None, partition_failure(run_dir, partition, checkpoint, state, partition["planningFailure"].get("error", "planning failed"))
    try:
        network = next(row for row in safe_json(SHADOW_CONFIG_PATH).get("networks", []) if row["id"] == network_id)
        client, settings = core.historical_log_client(network, config, ledger)
    except Exception as error:
        return None, partition_failure(run_dir, partition, checkpoint, classify_error(error), error)
    current = int(checkpoint.get("nextBlockOrSlot") or partition["fromBlockOrSlot"])
    end = int(partition["toBlockOrSlot"])
    path, _, _ = partition_paths(run_dir, partition)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        seen, _ = load_existing_seen(path)
    except Exception as error:
        return None, partition_failure(run_dir, partition, checkpoint, "program_failure", error)
    default_span = max(1, int(settings.get("initialBlockSpan", config["evm"]["initialBlockSpan"])))
    minimum_span = max(1, int(settings.get("minimumBlockSpan", config["evm"]["minimumBlockSpan"])))
    maximum_span = max(default_span, int(settings.get("maximumBlockSpan", config["evm"]["maximumBlockSpan"])))
    span = load_span_hint(run_dir, partition, default_span, minimum_span, maximum_span)
    retry_count = int(checkpoint.get("retryCount") or 0)
    ledger_offset = len(ledger.requests)
    with path.open("a", encoding="utf-8") as handle:
        while current <= end:
            selected_span = min(span, end - current + 1)
            selected_end = current + selected_span - 1
            params = {
                "address": schema["emitter"],
                "topics": [schema["eventTopic"]],
                "fromBlock": core.hex_block(current),
                "toBlock": core.hex_block(selected_end),
            }
            try:
                logs = client.call("eth_getLogs", [params], attempts=1) or []
                annotate_new_requests(ledger, ledger_offset, partition, current, selected_end)
                if len(logs) >= settings.get("suspiciousLogResultCap", 10000) and selected_span > minimum_span:
                    raise core.RpcError("suspicious result cap reached")
                retry_count = 0
            except Exception as error:
                annotate_new_requests(ledger, ledger_offset, partition, current, selected_end)
                # Blockscout and several public log endpoints return a
                # successful response capped at their page limit.  This is a
                # range-sizing signal, not an upstream failure: shrink only
                # this request and retry the same cursor without consuming the
                # bounded source-failure retry budget.
                if (
                    "suspicious result cap reached" in str(error).lower()
                    and selected_span > minimum_span
                ):
                    span = max(minimum_span, selected_span // 2)
                    save_span_hint(run_dir, partition, span, "result_cap_range_resize")
                    new_request_count = len(ledger.requests) - ledger_offset
                    for request in ledger.requests[ledger_offset:]:
                        request["state"] = "source_failure"
                        request["error"] = "result_cap_range_resize"
                    checkpoint.update(
                        {
                            "nextBlockOrSlot": current,
                            "requests": checkpoint.get("requests", 0) + new_request_count,
                            "state": "retrying",
                            "lastFailure": {"state": "source_failure", "reason": "result_cap_range_resize", "error": str(error)[:500]},
                        }
                    )
                    save_checkpoint(run_dir, partition, checkpoint)
                    ledger_offset = write_request_ledger(run_dir, ledger, ledger_offset)
                    update_latest(
                        latest,
                        plan,
                        lock_payload,
                        state="retrying",
                        stage="partition_range_resize",
                        currentWork={"partitionId": partition["partitionId"], "networkId": network_id, "schemaId": schema_id, "nextBlockOrSlot": current, "state": "source_failure", "reason": "result_cap_range_resize", "learnedBlockSpan": span},
                        lastCheckpoint=checkpoint,
                        requests=request_summary(ledger),
                    )
                    continue
                state = classify_error(error)
                retry_count += 1
                checkpoint.update(
                    {
                        "nextBlockOrSlot": current,
                        "retryCount": retry_count,
                        "requests": checkpoint.get("requests", 0) + len(ledger.requests) - ledger_offset,
                        "state": "quota_wait" if state == "quota_limited" else "retrying",
                        "lastFailure": {"state": state, "error": str(error)[:500]},
                    }
                )
                save_checkpoint(run_dir, partition, checkpoint)
                ledger_offset = write_request_ledger(run_dir, ledger, ledger_offset)
                update_latest(
                    latest,
                    plan,
                    lock_payload,
                    state="quota_wait" if state == "quota_limited" else "retrying",
                    stage="partition_retry",
                    currentWork={"partitionId": partition["partitionId"], "networkId": network_id, "schemaId": schema_id, "nextBlockOrSlot": current, "state": state},
                    lastCheckpoint=checkpoint,
                    requests=request_summary(ledger),
                )
                if state in {"configuration_missing", "unsupported", "program_failure"} or retry_count > max_retries:
                    return None, partition_failure(run_dir, partition, checkpoint, state, error)
                if not no_sleep:
                    time.sleep(retry_delay(retry_count))
                if state in {"source_failure", "quota_limited"} and selected_span > int(settings.get("minimumTransportRetryBlockSpan", minimum_span)):
                    span = max(int(settings.get("minimumTransportRetryBlockSpan", minimum_span)), selected_span // 2)
                    save_span_hint(run_dir, partition, span, "transport_retry_shrink")
                continue
            page_events = []
            for log in logs:
                event = core.decode_log(log, schema)
                event["schemaId"] = schema_id
                if event_in_fixed_window(event, plan):
                    page_events.append(event)
            append_events(handle, page_events, seen)
            current = selected_end + 1
            checkpoint.update(
                {
                    "nextBlockOrSlot": current,
                    "lastSuccessfulAt": utc_now(),
                    "requests": checkpoint.get("requests", 0) + len(ledger.requests) - ledger_offset,
                    "events": len(seen),
                    "retryCount": 0,
                    "state": "running",
                    "lastFailure": {},
                }
            )
            save_checkpoint(run_dir, partition, checkpoint)
            ledger_offset = write_request_ledger(run_dir, ledger, ledger_offset)
            update_latest(
                latest,
                plan,
                lock_payload,
                state="running",
                stage="partition_scan",
                currentWork={"partitionId": partition["partitionId"], "networkId": network_id, "schemaId": schema_id, "nextBlockOrSlot": current, "toBlockOrSlot": end, "learnedBlockSpan": span},
                lastCheckpoint=checkpoint,
                requests=request_summary(ledger),
            )
            if len(logs) < settings.get("spanGrowthLogResultCap", settings.get("suspiciousLogResultCap", 10000) // 4):
                next_span = min(maximum_span, max(span, selected_span) * 2)
                if next_span != span:
                    span = next_span
                    save_span_hint(run_dir, partition, span, "low_result_growth")
    save_span_hint(run_dir, partition, span, "partition_complete")
    manifest, validation = complete_partition(run_dir, partition, checkpoint, plan, checkpoint.get("requests", 0))
    if manifest is None:
        return None, partition_failure(run_dir, partition, checkpoint, "program_failure", "; ".join(validation["errors"]) or "partition validation failed")
    return manifest, None


def scan_solana_partition(
    run_dir,
    partition,
    plan,
    config,
    ledger,
    worker_id,
    latest,
    lock_payload,
    max_retries=DEFAULT_RETRY_LIMIT,
    no_sleep=False,
):
    checkpoint = load_checkpoint(run_dir, partition, worker_id)
    if partition.get("planningFailure"):
        state = partition["planningFailure"].get("state", "source_failure")
        return None, partition_failure(run_dir, partition, checkpoint, state, partition["planningFailure"].get("error", "planning failed"))
    settings = config["solana"]
    schemas = [schema for schema in settings.get("creationSchemas") or [] if schema["id"] in set(partition.get("schemaIds") or [])]
    if not schemas:
        return None, partition_failure(run_dir, partition, checkpoint, "unsupported", "no registered Solana creation schema")
    try:
        client = core.SqdSolanaArchiveClient(settings, ledger)
    except Exception as error:
        return None, partition_failure(run_dir, partition, checkpoint, classify_error(error), error)
    use_portal = partition.get("source") == "sqd_portal_finalized_stream"
    current = int(checkpoint.get("nextBlockOrSlot") or partition["fromBlockOrSlot"])
    end = int(partition["toBlockOrSlot"])
    path, _, _ = partition_paths(run_dir, partition)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        seen, _ = load_existing_seen(path)
    except Exception as error:
        return None, partition_failure(run_dir, partition, checkpoint, "program_failure", error)
    retry_count = int(checkpoint.get("retryCount") or 0)
    ledger_offset = len(ledger.requests)
    with path.open("a", encoding="utf-8") as handle:
        while current <= end:
            try:
                request_start = current
                query = core.solana_portal_query(schemas, current, end) if use_portal else core.solana_archive_query(schemas, current, end)
                blocks = client.portal_query(query) if use_portal else client.query(current, query)
                annotate_new_requests(ledger, ledger_offset, partition, request_start, end)
                if not blocks:
                    raise core.RpcError("archive returned no continuation block")
                normalized_blocks = [core.normalize_portal_solana_block(row) for row in blocks] if use_portal else blocks
                last_height = int(normalized_blocks[-1]["header"]["number"])
                if last_height < current or last_height > end:
                    raise core.RpcError("archive continuation did not advance")
                page_events = []
                for block in normalized_blocks:
                    page_events.extend(
                        event
                        for event in core.decode_solana_creation_block(block, schemas)
                        if event_in_fixed_window(event, plan)
                    )
                append_events(handle, page_events, seen)
                current = last_height + 1
                retry_count = 0
                checkpoint.update(
                    {
                        "nextBlockOrSlot": current,
                        "lastSuccessfulAt": utc_now(),
                        "requests": checkpoint.get("requests", 0) + len(ledger.requests) - ledger_offset,
                        "events": len(seen),
                        "retryCount": 0,
                        "state": "running",
                        "lastFailure": {},
                    }
                )
                save_checkpoint(run_dir, partition, checkpoint)
                ledger_offset = write_request_ledger(run_dir, ledger, ledger_offset)
                update_latest(
                    latest,
                    plan,
                    lock_payload,
                    state="running",
                    stage="partition_scan",
                    currentWork={"partitionId": partition["partitionId"], "networkId": "solana-mainnet", "schemaId": partition["schemaId"], "nextBlockOrSlot": current, "toBlockOrSlot": end},
                    lastCheckpoint=checkpoint,
                    requests=request_summary(ledger),
                )
            except Exception as error:
                annotate_new_requests(ledger, ledger_offset, partition, current, end)
                state = classify_error(error)
                retry_count += 1
                checkpoint.update(
                    {
                        "nextBlockOrSlot": current,
                        "retryCount": retry_count,
                        "requests": checkpoint.get("requests", 0) + len(ledger.requests) - ledger_offset,
                        "state": "quota_wait" if state == "quota_limited" else "retrying",
                        "lastFailure": {"state": state, "error": str(error)[:500]},
                    }
                )
                save_checkpoint(run_dir, partition, checkpoint)
                ledger_offset = write_request_ledger(run_dir, ledger, ledger_offset)
                update_latest(
                    latest,
                    plan,
                    lock_payload,
                    state="quota_wait" if state == "quota_limited" else "retrying",
                    stage="partition_retry",
                    currentWork={"partitionId": partition["partitionId"], "networkId": "solana-mainnet", "nextBlockOrSlot": current, "state": state},
                    lastCheckpoint=checkpoint,
                    requests=request_summary(ledger),
                )
                if state in {"configuration_missing", "unsupported", "program_failure"} or retry_count > max_retries:
                    return None, partition_failure(run_dir, partition, checkpoint, state, error)
                if not no_sleep:
                    time.sleep(retry_delay(retry_count))
    manifest, validation = complete_partition(run_dir, partition, checkpoint, plan, checkpoint.get("requests", 0))
    if manifest is None:
        return None, partition_failure(run_dir, partition, checkpoint, "program_failure", "; ".join(validation["errors"]) or "partition validation failed")
    return manifest, None


def candidate_rows_from_manifests(plan, run_dir, manifests, config):
    earliest = {}
    known_quotes = set(config.get("solana", {}).get("knownQuoteTokens") or [])
    for partition in plan.get("partitions") or []:
        if partition["partitionId"] not in manifests:
            continue
        path = event_file_path(run_dir, partition)
        try:
            rows = iter_jsonl(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for event in rows:
            network_id = event.get("networkId")
            for token in event.get("tokenAddresses") or []:
                if network_id == "solana-mainnet" and token in known_quotes:
                    continue
                key = (network_id, token)
                current = earliest.get(key)
                event_position_value = event.get("blockNumber")
                if current is None or int(event_position_value or 0) < int(current.get("blockNumber") or 0):
                    earliest[key] = event
    return earliest


def write_candidates(plan, run_dir, manifests, config):
    rows = candidate_rows_from_manifests(plan, run_dir, manifests, config)
    network_counts = Counter(network_id for network_id, _token in rows)
    path = Path(run_dir) / "candidate-tokens.jsonl"
    temporary = Path(run_dir) / "candidate-tokens.jsonl.building"
    with temporary.open("w", encoding="utf-8") as handle:
        for (network_id, token), event in sorted(rows.items()):
            timestamp = event_timestamp(event)
            observed_at = (
                datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
                if timestamp is not None
                else None
            )
            candidate = {
                "networkId": network_id,
                "tokenAddress": token,
                "earliestCoveredPoolAt": observed_at,
                "poolId": event.get("poolId"),
                "dexIds": event.get("dexIds") or [],
                "t0EvidenceType": "covered_dex_pool_created",
                "t0Status": "covered_dex_lower_bound_not_global_t0",
            }
            if event.get("slot") is not None:
                candidate["earliestCoveredPoolSlot"] = event.get("slot")
                candidate["earliestCoveredArchiveBlockHeight"] = event.get("blockNumber")
            else:
                candidate["earliestCoveredPoolBlock"] = event.get("blockNumber")
            handle.write(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)
    return path, len(rows), dict(network_counts)


def validate_run(plan, run_dir, manifests, failures):
    errors = []
    event_count = 0
    event_duplicates = 0
    future_events = 0
    out_of_range = 0
    decode_failures = 0
    for partition in plan.get("partitions") or []:
        pid = partition["partitionId"]
        manifest = manifests.get(pid)
        if not manifest:
            continue
        event_count += int(manifest.get("rowCount") or 0)
        if not manifest.get("eventIdentityUnique", False):
            event_duplicates += 1
        decode_failures += int(manifest.get("decodeFailures") or 0)
        minimum = manifest.get("minimumBlockOrSlot")
        maximum = manifest.get("maximumBlockOrSlot")
        if minimum is not None and int(minimum) < int(partition["fromBlockOrSlot"]):
            out_of_range += 1
        if maximum is not None and int(maximum) > int(partition["toBlockOrSlot"]):
            out_of_range += 1
        maximum_timestamp = manifest.get("maximumTimestamp")
        if maximum_timestamp and parse_utc(maximum_timestamp).timestamp() > parse_utc(plan["windowEnd"]).timestamp() + 300:
            future_events += 1
    if event_duplicates:
        errors.append({"state": "program_failure", "error": "duplicate event identities", "count": event_duplicates})
    if decode_failures:
        errors.append({"state": "program_failure", "error": "decode failures", "count": decode_failures})
    if out_of_range:
        errors.append({"state": "program_failure", "error": "out-of-range events", "count": out_of_range})
    if future_events:
        errors.append({"state": "program_failure", "error": "future timestamps", "count": future_events})
    overlap_requests = accepted_overlap_request_count(plan, Path(run_dir) / "request-ledger.jsonl")
    if overlap_requests:
        errors.append({"state": "program_failure", "error": "accepted baseline overlap was requested", "count": overlap_requests})
    errors.extend(failures)
    return {
        "schemaVersion": "convexity-gate0-background-validation-v1",
        "runId": plan["runId"],
        "validatedAt": utc_now(),
        "partitionCount": len(plan.get("partitions") or []),
        "completedPartitionCount": len(manifests),
        "eventCount": event_count,
        "eventIdentityDuplicates": event_duplicates,
        "decodeFailures": decode_failures,
        "outOfRangeEvents": out_of_range,
        "futureEvents": future_events,
        "acceptedBaselineOverlapRequests": overlap_requests,
        "validationMode": "verified_partition_manifests_plus_disjoint_fixed_ranges",
        "failures": errors,
        "pass": not errors and len(manifests) == len(plan.get("partitions") or []),
    }


def final_summary(plan, run_dir, manifests, validation, candidate_count, candidate_counts_by_network, ledger):
    by_network = defaultdict(lambda: {"partitions": 0, "events": 0, "candidates": 0, "states": Counter()})
    for partition in plan.get("partitions") or []:
        network = by_network[partition["networkId"]]
        network["partitions"] += 1
        manifest = manifests.get(partition["partitionId"])
        if manifest:
            network["events"] += int(manifest.get("rowCount") or 0)
            network["states"][manifest.get("sourceState", "success")] += 1
    network_results = []
    for network_id in plan.get("selectedNetworks") or []:
        row = by_network[network_id]
        network_results.append(
            {
                "networkId": network_id,
                "partitionCount": row["partitions"],
                "completedPartitions": sum(row["states"].values()),
                "events": row["events"],
                "candidateTokens": int(candidate_counts_by_network.get(network_id, 0)),
                "states": dict(row["states"]),
            }
        )
    return {
        "schemaVersion": "convexity-gate0-background-summary-v1",
        "runId": plan["runId"],
        "startedAt": plan["createdAt"],
        "finishedAt": utc_now(),
        "windowStart": plan["windowStart"],
        "windowEnd": plan["windowEnd"],
        "windowDays": plan["windowDays"],
        "coverage": {
            "networksSelected": len(plan.get("selectedNetworks") or []),
            "partitions": len(plan.get("partitions") or []),
            "partitionsComplete": len(manifests),
            "events": sum(int(row.get("rowCount") or 0) for row in manifests.values()),
            "candidateTokens": candidate_count,
            "marketWideComplete": False,
            "usableAsGlobalT0": False,
        },
        "networkResults": network_results,
        "requestSummary": request_summary(ledger),
        "validation": validation,
        "candidatePath": str((Path(run_dir) / "candidate-tokens.jsonl").resolve()) if candidate_count else "",
        "boundary": {
            "projectMinimumWaitDays": 0,
            "allProjectAgesUseSourceHistory": True,
            "liveReliabilityBlocksBackfill": False,
            "marketWideCoverageGuaranteed": False,
            "usableAsGlobalT0": False,
        },
        "unsupportedDexLabels": plan.get("unsupportedDexLabels") or [],
    }


def run_once(plan, config, shadow, run_dir, latest, lock_payload, args):
    if latest.get("state") == "completed":
        latest["stage"] = "completed_idempotent_exit"
        latest["currentWork"] = {}
        latest["updatedAt"] = utc_now()
        latest["lastHeartbeatAt"] = latest["updatedAt"]
        atomic_write_json(LATEST_PATH, latest)
        maybe_build_progress(latest, plan, force=True)
        return latest
    latest["state"] = "running"
    latest["stage"] = "partition_scan"
    latest["recoveryCount"] = int(latest.get("recoveryCount") or 0) + 1
    atomic_write_json(LATEST_PATH, latest)
    ledger = core.RequestLedger(timeout=int(config["evm"].get("rpcTimeoutSeconds", 30)))
    ledger_file_start = load_request_ledger(run_dir, ledger)
    manifests = existing_complete_manifests(plan, run_dir)
    failures = []
    worker_id = lock_payload["workerId"]
    for partition in plan.get("partitions") or []:
        pid = partition["partitionId"]
        if pid in manifests:
            continue
        if partition.get("reuse"):
            failure = partition_failure(
                run_dir,
                partition,
                load_checkpoint(run_dir, partition, worker_id),
                "program_failure",
                "verified read-only reuse artifact is unavailable; network fallback is forbidden",
            )
            failures.append(failure)
            update_run_counters(latest, plan, manifests, ledger, failures=failures)
            atomic_write_json(LATEST_PATH, latest)
            continue
        if partition.get("planningFailure"):
            checkpoint = load_checkpoint(run_dir, partition, worker_id)
            failure = partition_failure(
                run_dir,
                partition,
                checkpoint,
                partition["planningFailure"].get("state", "source_failure"),
                partition["planningFailure"].get("error", "planning failed"),
            )
            failures.append(failure)
            update_run_counters(latest, plan, manifests, ledger, failures=failures)
            atomic_write_json(LATEST_PATH, latest)
            continue
        try:
            if partition["networkId"] == "solana-mainnet":
                manifest, failure = scan_solana_partition(
                    run_dir, partition, plan, config, ledger, worker_id, latest, lock_payload, args.max_retries, args.no_sleep
                )
            else:
                manifest, failure = scan_evm_partition(
                    run_dir, partition, plan, config, ledger, worker_id, latest, lock_payload, args.max_retries, args.no_sleep
                )
            if manifest:
                manifests[pid] = manifest
            if failure:
                failures.append(failure)
        except Exception as error:
            failure = partition_failure(
                run_dir,
                partition,
                load_checkpoint(run_dir, partition, worker_id),
                "program_failure",
                error,
            )
            failures.append(failure)
        update_run_counters(latest, plan, manifests, ledger, current_work={}, failures=failures)
        atomic_write_json(LATEST_PATH, latest)
    try:
        candidate_path, candidate_count, candidate_counts_by_network = write_candidates(plan, run_dir, manifests, config)
    except Exception as error:
        failures.append({"partitionId": "candidate-aggregation", "state": "program_failure", "error": str(error)[:500]})
        candidate_count = 0
        candidate_counts_by_network = {}
    validation = validate_run(plan, run_dir, manifests, failures)
    atomic_write_json(Path(run_dir) / "validation.json", validation)
    summary = final_summary(
        plan, run_dir, manifests, validation, candidate_count, candidate_counts_by_network, ledger
    )
    atomic_write_json(Path(run_dir) / "summary.json", summary)
    if validation["pass"]:
        latest["state"] = "completed"
        latest["stage"] = "completed"
    else:
        latest["state"] = "failed"
        latest["stage"] = "awaiting_partition_recovery"
    latest["candidateTokens"] = candidate_count
    latest["events"] = validation["eventCount"]
    latest["failureSummary"] = summary.get("validation", {}).get("failures", [])
    update_run_counters(latest, plan, manifests, ledger, current_work={}, failures=failures)
    latest["state"] = "completed" if validation["pass"] else "failed"
    latest["stage"] = "completed" if validation["pass"] else "awaiting_partition_recovery"
    latest["currentWork"] = {}
    atomic_write_json(LATEST_PATH, latest)
    maybe_build_progress(latest, plan, force=True)
    return latest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Independent resumable Gate 0 90-day backfill")
    parser.add_argument("--resume", action="store_true", help="resume the latest immutable plan")
    parser.add_argument("--correct-run", help="create a corrected fixed-window plan from a preserved run id")
    parser.add_argument("--network", action="append", dest="networks")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--shadow-config", default=str(SHADOW_CONFIG_PATH))
    parser.add_argument("--max-retries", type=int, default=DEFAULT_RETRY_LIMIT)
    parser.add_argument("--no-sleep", action="store_true", help="test mode; do not wait between retries")
    parser.add_argument("--print-plan", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.resume and args.correct_run:
        raise ValueError("--resume and --correct-run are mutually exclusive")
    if args.correct_run:
        plan, config, shadow, _registry, run_dir = make_corrected_plan(
            args.correct_run, args.config, args.shadow_config
        )
        latest = safe_json(LATEST_PATH)
    elif args.resume and LATEST_PATH.exists():
        plan, run_dir, latest = load_plan_from_latest()
        config = core.load_config(args.config)
        shadow = safe_json(args.shadow_config)
    else:
        plan, config, shadow, _registry, run_dir = make_plan(args.config, args.shadow_config, args.networks)
        latest = safe_json(LATEST_PATH)
    if args.print_plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    lock_payload = acquire_lock(plan["runId"])
    if lock_payload is None:
        print(json.dumps({"runId": plan["runId"], "state": "already_running"}, ensure_ascii=False))
        return 0
    try:
        latest = run_once(plan, config, shadow, run_dir, latest, lock_payload, args)
        print(
            json.dumps(
                {
                    "runId": plan["runId"],
                    "state": latest.get("state"),
                    "progress": latest.get("partitionProgress"),
                    "reportPath": str((PROGRESS_ROOT / "report.html").resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 0 if latest.get("state") in {"completed", "failed"} else 1
    finally:
        release_lock(lock_payload)


if __name__ == "__main__":
    raise SystemExit(main())
