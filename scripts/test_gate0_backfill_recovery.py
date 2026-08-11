#!/usr/bin/env python3
"""Fast, network-free recovery and contract checks for the Gate 0 runner."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parent / "gate0_backfill_background.py"
PROGRESS_SCRIPT = Path(__file__).resolve().parent / "build_gate0_backfill_progress.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan_fixture(module, run_dir):
    return {
        "schemaVersion": module.PLAN_SCHEMA,
        "runId": "gate0-test-run",
        "createdAt": "2026-08-08T00:00:00Z",
        "windowStart": "2026-08-01T00:00:00Z",
        "windowEnd": "2026-08-08T00:00:00Z",
        "windowDays": 7,
        "selectedNetworks": ["ethereum-mainnet", "solana-mainnet"],
        "selectedSchemas": ["ethereum-mainnet:test-dex", "solana-mainnet:test"],
        "unsupportedDexLabels": ["ethereum-mainnet:unknown-dex"],
        "partitions": [
            module.partition_row("eth-test-0-9", "ethereum-mainnet", "test-dex", "fake", 0, 9, 10, "block"),
            module.partition_row("sol-test-0-9", "solana-mainnet", "solana-creation-schemas", "fake", 0, 9, 10, "block", schema_ids=["test"]),
        ],
    }


def main():
    module = load(SCRIPT, "gate0_background_test")
    progress = load(PROGRESS_SCRIPT, "gate0_progress_test")
    assert set(module.CLASSIFICATION_STATES) == {
        "success", "no_data", "quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure"
    }
    assert module.classify_error(module.core.RpcError("quota", kind="quota_limited")) == "quota_limited"
    assert module.classify_error(module.core.RpcError("source")) == "source_failure"
    assert module.classify_error(module.core.RpcError("unsupported", kind="unsupported")) == "unsupported"
    assert module.classify_error(module.core.RpcError("missing", kind="configuration_missing")) == "configuration_missing"
    assert module.classify_error(ValueError("broken payload")) == "program_failure"
    unsupported = module.observed_unsupported_labels({}, {}, {}, {})
    assert len(unsupported) == 18
    assert sum(row.startswith("ethereum-mainnet:") for row in unsupported) == 4
    assert sum(row.startswith("base-mainnet:") for row in unsupported) == 8
    assert sum(row.startswith("arbitrum-mainnet:") for row in unsupported) == 6
    assert module.event_identity({"networkId": "ethereum-mainnet", "blockNumber": 1, "transactionHash": "0x1", "logIndex": 0}) != module.event_identity({"networkId": "ethereum-mainnet", "blockNumber": 2, "transactionHash": "0x1", "logIndex": 0})
    assert module.build_partition_progress([{"partitionId": "a", "weight": 5}], {"a"}) == {"completedCount": 1, "totalCount": 1, "completedWeight": 5, "totalWeight": 5}
    assert module.uncovered_ranges(0, 20, [(0, 4), (10, 14)]) == [(5, 9), (15, 20)]

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        old_root, old_lock = module.BACKGROUND_ROOT, module.LOCK_PATH
        module.BACKGROUND_ROOT = root / "background"
        module.LOCK_PATH = module.BACKGROUND_ROOT / "lock.json"
        run_dir = module.BACKGROUND_ROOT / "runs" / "gate0-test-run"
        run_dir.mkdir(parents=True)
        plan = plan_fixture(module, run_dir)

        # Three interruption points: the cursor is the recovery source of
        # truth, and recovery count increases without changing the window.
        partition = plan["partitions"][0]
        for cursor in (0, 4, 9):
            checkpoint = module.load_checkpoint(run_dir, partition, "worker-a")
            checkpoint["nextBlockOrSlot"] = cursor
            module.save_checkpoint(run_dir, partition, checkpoint)
            recovered = module.load_checkpoint(run_dir, partition, "worker-b")
            assert recovered["nextBlockOrSlot"] == cursor
            assert recovered["recoveryCount"] >= 1
            assert recovered["workerId"] == "worker-b"
        assert plan["windowStart"] == "2026-08-01T00:00:00Z"
        assert plan["windowEnd"] == "2026-08-08T00:00:00Z"
        ledger_path = run_dir / "request-ledger.jsonl"
        ledger_path.write_text(json.dumps({"source": "fake", "state": "success"}) + "\n", encoding="utf-8")
        restored_ledger = module.core.RequestLedger(timeout=1)
        assert module.load_request_ledger(run_dir, restored_ledger) == 1
        assert len(restored_ledger.requests) == 1

        # Completed partitions are skipped and cannot be counted twice.
        path, complete, _ = module.partition_paths(run_dir, partition)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"networkId": "ethereum-mainnet", "schemaId": "test-dex", "blockNumber": 1, "transactionHash": "0x1", "logIndex": 0, "poolId": "0xpool", "tokenAddresses": ["0xtoken"], "blockTimestamp": "2026-08-04T00:00:00Z"}
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        manifest = module.complete_partition(run_dir, partition, module.load_checkpoint(run_dir, partition, "worker-a"), plan, 1)[0]
        assert manifest and complete.exists()
        protected_hashes = (module.sha256_path(path), module.sha256_path(complete))
        found = module.existing_complete_manifests(plan, run_dir)
        assert set(found) == {partition["partitionId"]}
        assert module.build_network_progress(plan, found)[0]["partitions"]["completedCount"] == 1

        # Read-only reuse is materialized as a zero-request completed
        # partition and a missing/corrupt source can never fall back to the
        # network scanner.
        reuse_partition = module.partition_row(
            "sol-reuse-20-29", "solana-mainnet", "solana-creation-schemas",
            "accepted_read_only_baseline", 20, 29, 10, "block", schema_ids=["test"],
        )
        reuse_source = root / "accepted.jsonl"
        reuse_event = {
            "networkId": "solana-mainnet", "schemaId": "test", "blockNumber": 21,
            "slot": 121, "transactionSignature": "sig", "instructionAddress": [0],
            "poolId": "pool", "tokenAddresses": ["token"], "decodeComplete": True,
            "blockTimestamp": "2026-08-04T00:00:00Z",
        }
        reuse_source.write_text(json.dumps(reuse_event) + "\n", encoding="utf-8")
        reuse_partition["reuse"] = {
            "kind": "accepted_baseline", "runId": "accepted", "eventPath": str(reuse_source),
            "sha256": module.sha256_path(reuse_source), "rowCount": 1, "sourceState": "success",
            "eventIdentityUnique": True, "decodeFailures": 0,
            "minimumBlockOrSlot": 21, "maximumBlockOrSlot": 21,
        }
        reuse_plan = dict(plan)
        reuse_plan["partitions"] = [reuse_partition]
        module.materialize_reuse_manifests(reuse_plan, run_dir)
        reuse_found = module.existing_complete_manifests(reuse_plan, run_dir)
        assert reuse_found[reuse_partition["partitionId"]]["requestCount"] == 0
        assert module.event_file_path(run_dir, reuse_partition) == reuse_source

        overlap_plan = {
            "acceptedSolanaCoverage": {"fromBlockOrSlot": 20, "toBlockOrSlot": 29}
        }
        overlap_ledger = root / "overlap.jsonl"
        overlap_ledger.write_text(
            "\n".join(
                json.dumps(row)
                for row in (
                    {"networkId": "solana-mainnet", "fromBlockOrSlot": 0, "toBlockOrSlot": 19},
                    {"networkId": "solana-mainnet", "fromBlockOrSlot": 20, "toBlockOrSlot": 20},
                    {"networkId": "ethereum-mainnet", "fromBlockOrSlot": 20, "toBlockOrSlot": 29},
                )
            ) + "\n",
            encoding="utf-8",
        )
        assert module.accepted_overlap_request_count(overlap_plan, overlap_ledger) == 1

        # The safe range learned by one parent partition must survive into the
        # next parent partition of the same network/schema and be clamped to
        # the current source limits.
        hint_partition = module.partition_row(
            "rh-bankr-0-9", "robinhood-mainnet", "bankr-robinhood",
            "blockscout_pro_logs", 0, 9, 10, "block",
        )
        assert module.load_span_hint(run_dir, hint_partition, 2_000_000, 1_000, 10_000_000) == 2_000_000
        module.save_span_hint(run_dir, hint_partition, 62_500, "result_cap_range_resize")
        next_hint_partition = dict(hint_partition, partitionId="rh-bankr-10-19", fromBlockOrSlot=10, toBlockOrSlot=19)
        assert module.load_span_hint(run_dir, next_hint_partition, 2_000_000, 1_000, 10_000_000) == 62_500
        module.save_span_hint(run_dir, next_hint_partition, 50_000_000, "growth")
        assert module.load_span_hint(run_dir, hint_partition, 2_000_000, 1_000, 10_000_000) == 10_000_000

        # A malformed partition is isolated; no completion manifest is issued.
        bad = plan["partitions"][1]
        bad_path, bad_complete, _ = module.partition_paths(run_dir, bad)
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text('{"broken":\n', encoding="utf-8")
        try:
            module.complete_partition(run_dir, bad, module.load_checkpoint(run_dir, bad, "worker-a"), plan, 1)
        except ValueError:
            pass
        assert not bad_complete.exists()
        repaired_event = {
            "networkId": "solana-mainnet", "schemaId": "test", "blockNumber": 1,
            "slot": 101, "transactionSignature": "repaired", "instructionAddress": [0],
            "poolId": "pool", "tokenAddresses": ["token"], "decodeComplete": True,
            "blockTimestamp": "2026-08-04T00:00:00Z",
        }
        bad_path.write_text(json.dumps(repaired_event) + "\n", encoding="utf-8")
        repaired_manifest = module.complete_partition(
            run_dir, bad, module.load_checkpoint(run_dir, bad, "worker-b"), plan, 1
        )[0]
        assert repaired_manifest and repaired_manifest["rowCount"] == 1
        assert (module.sha256_path(path), module.sha256_path(complete)) == protected_hashes

        first = module.acquire_lock("gate0-test-run")
        assert first is not None
        assert module.acquire_lock("gate0-test-run") is None
        module.release_lock(first)
        assert not module.LOCK_PATH.exists()
        module.BACKGROUND_ROOT, module.LOCK_PATH = old_root, old_lock

        # A completed task may be triggered by Task Scheduler again.  It must
        # do zero network work, preserve cumulative counters and leave every
        # result artifact byte-identical.
        completed_latest = module.initial_latest(plan, state="completed")
        completed_latest["requests"] = {
            "total": 7,
            "byState": {state: (7 if state == "success" else 0) for state in module.CLASSIFICATION_STATES},
        }
        completed_artifact_hashes = (module.sha256_path(path), module.sha256_path(complete))
        old_latest_path = module.LATEST_PATH
        module.LATEST_PATH = root / "latest-completed.json"
        with patch.object(module, "maybe_build_progress"):
            idempotent = module.run_once(plan, {}, {}, run_dir, completed_latest, None, None)
        module.LATEST_PATH = old_latest_path
        assert idempotent["stage"] == "completed_idempotent_exit"
        assert idempotent["requests"]["total"] == 7
        assert (module.sha256_path(path), module.sha256_path(complete)) == completed_artifact_hashes

        # Small real-decoder acceptance: one EVM partition writes one event,
        # emits a completion manifest, and uses the same decoder as Gate 0.
        schema = {
            "networkId": "ethereum-mainnet",
            "dexIds": ["test-dex"],
            "emitter": "0x" + "44" * 20,
            "eventTopic": "0x" + "55" * 32,
            "poolLocation": {"source": "data", "index": 0},
            "poolTemplate": "0x" + "33" * 20,
            "tokenLocations": [{"source": "topic", "index": 1}, {"source": "topic", "index": 2}],
        }
        sample_log = {
            "address": schema["emitter"],
            "topics": [schema["eventTopic"], "0x" + "11" * 32, "0x" + "22" * 32],
            "data": "0x" + ("33" * 20).rjust(64, "0") + ("0" * 63) + "1",
            "blockNumber": "0x2",
            "blockTimestamp": "0x6a712b80",
            "transactionHash": "0x" + "66" * 32,
            "logIndex": "0x0",
        }

        class FakeClient:
            def __init__(self, ledger):
                self.ledger = ledger

            def call(self, method, params, attempts=1):
                start = int(params[0]["fromBlock"], 16)
                end = int(params[0]["toBlock"], 16)
                self.ledger.requests.append({"source": "fake", "state": "success"})
                return [sample_log] if start <= 2 <= end else []

        acceptance_partition = module.partition_row("eth-acceptance", "ethereum-mainnet", "test-dex", "fake", 0, 3, 4, "block")
        acceptance_plan = dict(plan)
        acceptance_plan["partitions"] = [acceptance_partition]
        acceptance_latest = {}
        ledger = module.core.RequestLedger(timeout=1)
        settings = {"initialBlockSpan": 4, "minimumBlockSpan": 1, "maximumBlockSpan": 4, "suspiciousLogResultCap": 10, "spanGrowthLogResultCap": 2}
        old_latest = module.LATEST_PATH
        module.LATEST_PATH = root / "latest-acceptance.json"
        with patch.object(module.core, "load_schema_registry", return_value={"schemas": [schema]}), patch.object(module.core, "historical_log_client", return_value=(FakeClient(ledger), settings)):
            manifest, failure = module.scan_evm_partition(run_dir, acceptance_partition, acceptance_plan, {"schemaRegistry": "unused", "evm": settings}, ledger, "worker-a", acceptance_latest, None, max_retries=0, no_sleep=True)
        module.LATEST_PATH = old_latest
        assert failure is None
        assert manifest and manifest["sourceState"] == "success" and manifest["rowCount"] == 1

    artifact = progress.build_artifact(
        {
            "runId": "test",
            "state": "running",
            "updatedAt": "2026-08-08T00:00:00Z",
            "lastHeartbeatAt": "2026-08-08T00:00:00Z",
            "windowStart": "2026-08-01T00:00:00Z",
            "windowEnd": "2026-08-08T00:00:00Z",
            "windowDays": 7,
            "partitionProgress": {"completedCount": 1, "totalCount": 2, "completedWeight": 5, "totalWeight": 10},
            "networkProgress": [],
            "requests": {"total": 3, "byState": {"success": 3}},
        },
        {},
    )
    html = progress.render(artifact)
    assert "分片覆盖权重" in html
    assert "不能据此推算天数" in html
    assert "实时稳定性目标 14 个自然日" in html
    assert 'meta name="viewport"' in html
    assert "overflow-x:hidden" in html
    assert "table-layout:fixed" in html
    assert "@media(max-width:700px)" in html
    assert artifact["partitionProgress"]["percent"] == 50.0
    print("PASS: Gate 0 background partition recovery, isolation, lock and progress checks")


if __name__ == "__main__":
    main()
