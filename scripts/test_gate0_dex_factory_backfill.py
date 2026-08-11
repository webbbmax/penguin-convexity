#!/usr/bin/env python3
import gzip
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gate0_dex_factory_backfill import (
    BlockscoutLogClient,
    JsonRpcClient,
    POOL_CREATED_TOPIC,
    RpcError,
    base58_decode,
    build_coverage_rollup,
    choose_creation_log,
    chunk_log_filter,
    creation_log_filters,
    decode_log,
    estimate_window_start,
    first_block_at_or_after,
    historical_log_client,
    in_process_http_request,
    isolated_http_request,
    infer_group_schema,
    load_config,
    merge_schemas,
    normalize_portal_solana_block,
    observed_dex_groups,
    probe_solana_programs,
    read_response_with_deadline,
    decode_solana_creation_block,
    solana_archive_query,
    solana_portal_query,
    solana_schema_groups,
    sqd_request_retryable,
    response_error_detail,
    rpc_error_kind,
    scan_schema,
    scan_solana_archive,
    update_schema_registry,
)
from gate0_shadow_preflight import RequestLedger


TOKEN_A = "0x" + "11" * 20
TOKEN_B = "0x" + "22" * 20
POOL = "0x" + "33" * 20
EMITTER = "0x" + "44" * 20
TOPIC = "0x" + "55" * 32


def padded(value):
    return "0x" + value.removeprefix("0x").rjust(64, "0")


def sample_seed():
    return {
        "networkId": "ethereum-mainnet",
        "dexId": "test-v2",
        "poolAddress": POOL,
        "poolCreatedAt": "2026-08-04T00:00:00Z",
        "baseToken": {"address": TOKEN_A},
        "quoteToken": {"address": TOKEN_B},
    }


def sample_log(block=2):
    return {
        "address": EMITTER,
        "topics": [TOPIC, padded(TOKEN_A), padded(TOKEN_B)],
        "data": padded(POOL) + ("0" * 63) + "1",
        "blockNumber": hex(block),
        "transactionHash": "0x" + "66" * 32,
        "logIndex": "0x0",
    }


class TimestampRpc:
    def __init__(self):
        self.timestamps = [100, 110, 120, 130]

    def block_timestamp(self, number):
        return self.timestamps[number]


class FailingLogRpc:
    def __init__(self):
        self.calls = 0

    def call(self, method, params, attempts=3):
        self.calls += 1
        raise RpcError("network down")


class CreationBlockRpc:
    def __init__(self):
        self.calls = []

    def call(self, method, params, attempts=3):
        self.calls.append((method, params))
        if method == "eth_getCode":
            block = int(params[1], 16)
            return "0x6000" if block >= 6 else "0x"
        if method == "eth_getLogs":
            log_filter = params[0]
            assert int(log_filter["fromBlock"], 16) == 6
            assert int(log_filter["toBlock"], 16) == 6
            assert "address" not in log_filter
            assert "topics" not in log_filter
            return [sample_log(block=6)]
        raise AssertionError(method)


class Bytes32CreationRpc:
    def __init__(self, pool_id):
        self.pool_id = pool_id
        self.calls = []

    def block_timestamp(self, number):
        return 100 + number * 3

    def call(self, method, params, attempts=3):
        self.calls.append((method, params))
        assert method == "eth_getLogs"
        log_filter = params[0]
        assert int(log_filter["fromBlock"], 16) == 1
        assert int(log_filter["toBlock"], 16) == 5
        return [
            {
                "address": EMITTER,
                "topics": [TOPIC, self.pool_id, padded(TOKEN_A), padded(TOKEN_B)],
                "data": "0x",
                "blockNumber": "0x2",
                "transactionHash": "0x" + "77" * 32,
                "logIndex": "0x0",
            }
        ]


class ScanRpc:
    def __init__(self, always_fail=False):
        self.always_fail = always_fail
        self.calls = []

    def call(self, method, params, attempts=3):
        assert method == "eth_getLogs"
        row = params[0]
        start = int(row["fromBlock"], 16)
        end = int(row["toBlock"], 16)
        self.calls.append((start, end))
        if self.always_fail or end - start > 1:
            raise RpcError("range too wide")
        return [sample_log()] if start <= 2 <= end else []


class ReconnectingScanRpc:
    def __init__(self):
        self.log_calls = 0

    def call(self, method, params, attempts=3):
        if method == "eth_chainId":
            return "0x1"
        self.log_calls += 1
        if self.log_calls == 1:
            raise RpcError("tls disconnected", kind="transport_failure")
        return [sample_log()]


class ReconnectThenShrinkRpc:
    def __init__(self):
        self.log_calls = 0

    def call(self, method, params, attempts=3):
        if method == "eth_chainId":
            return "0x1"
        self.log_calls += 1
        row = params[0]
        start = int(row["fromBlock"], 16)
        end = int(row["toBlock"], 16)
        if self.log_calls == 1:
            raise RpcError("tls disconnected", kind="transport_failure")
        if end > start:
            raise RpcError("provider range limit", kind="rpc_response")
        return [sample_log()] if start <= 2 <= end else []


class RepeatedPoolScanRpc:
    def call(self, method, params, attempts=3):
        assert method == "eth_getLogs"
        block = int(params[0]["fromBlock"], 16)
        return [sample_log(block=block)]


class SolanaOwnerRpc:
    def __init__(self):
        self.calls = 0

    def call(self, method, params, attempts=3):
        assert method == "getAccountInfo"
        self.calls += 1
        return {"value": {"owner": "ProgramOwner111111111111111111111111111"}}


class FakeSolanaArchiveClient:
    def __init__(self, settings, ledger):
        pass

    def archive_height_at_timestamp(self, timestamp):
        return {"height": 100, "slot": 200, "timestamp": timestamp}

    def finalized_height(self):
        return 102

    def query(self, height, payload):
        return [
            {
                "header": {"number": payload["toBlock"], "timestamp": 1_700_000_000},
                "transactions": [],
                "instructions": [],
            }
        ]


class FakeSolanaPortalClient:
    def __init__(self, settings, ledger):
        pass

    def archive_height_at_timestamp(self, timestamp):
        return {"height": 100, "slot": 200, "timestamp": timestamp}

    def latest_archive_height_at_or_before(self, timestamp):
        return {"height": 102, "slot": 202, "timestamp": timestamp - 60}

    def portal_query(self, payload):
        slot = payload["toBlock"]
        return [
            {
                "header": {
                    "number": slot,
                    "height": slot - 100,
                    "timestamp": 1_700_000_000,
                },
                "transactions": [],
                "instructions": [],
            }
        ]


class FakeResponse:
    status = 200

    def __init__(self):
        self.served = False

    def getheaders(self):
        return [("content-encoding", "gzip")]

    def read(self, size=-1):
        if self.served:
            return b""
        self.served = True
        return gzip.compress(b'{"jsonrpc":"2.0","id":1,"result":"0x1"}')


class FakeConnection:
    instances = []

    def __init__(self, host, port, timeout):
        self.requests = []
        self.__class__.instances.append(self)

    def request(self, method, path, body, headers):
        self.requests.append((method, path))

    def getresponse(self):
        return FakeResponse()

    def close(self):
        pass


class FakeUrlOpenResponse:
    status = 200
    headers = {"content-type": "application/json"}
    requested_url = ""

    def __init__(self, request, timeout):
        self.__class__.requested_url = request.full_url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return b'{"status":"1","message":"OK","result":[]}'


class FakeInProcessResponse:
    status = 200
    headers = {"content-type": "application/json"}

    def __init__(self):
        self.chunks = [b"{}", b""]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size=-1):
        return self.chunks.pop(0)


class FakeOpener:
    def open(self, request, timeout):
        return FakeInProcessResponse()


def main():
    config = load_config()
    assert config["windowDays"] == 90
    assert config["boundary"]["projectMinimumWaitDays"] == 0
    assert config["boundary"]["allProjectAgesUseSourceHistory"] is True
    assert config["boundary"]["shortHistorySyntheticDaysAllowed"] is False
    assert config["boundary"]["liveReliabilityBlocksBackfillOrDevelopment"] is False
    assert config["boundary"]["fixedCandidateCapAllowed"] is False
    solana_groups = solana_schema_groups(config["solana"])
    assert set(solana_groups) == {
        "bags-fm",
        "fluxbeam",
        "meteora-damm-v2",
        "meteora-dbc",
        "orca",
        "pump-fun",
        "pumpswap",
    }
    assert base58_decode("1112") == b"\x00\x00\x00\x01"

    class ChunkedResponse:
        def __init__(self):
            self.chunks = [b"first", b"-second", b""]

        def read(self, size=-1):
            return self.chunks.pop(0)

    assert read_response_with_deadline(ChunkedResponse(), 1) == b"first-second"
    with patch(
        "gate0_dex_factory_backfill.urllib.request.build_opener",
        return_value=FakeOpener(),
    ):
        in_process = in_process_http_request(
            "https://worker.example/query/secret-value",
            b"{}",
            {"Authorization": "Bearer secret-value"},
            3,
            use_environment_proxy=False,
        )
    assert in_process["status"] == 200
    assert in_process["raw"] == b"{}"

    isolated_packet = {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "bodyBase64": "e30=",
    }
    with patch(
        "gate0_dex_factory_backfill.subprocess.run",
        return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps(isolated_packet), stderr=""
        ),
    ) as isolated_run:
        isolated = isolated_http_request(
            "https://worker.example/query/secret-value",
            b"{}",
            {"Authorization": "Bearer secret-value"},
            3,
            5,
        )
    assert isolated["raw"] == b"{}"
    assert "secret-value" not in " ".join(isolated_run.call_args.args[0])
    assert isolated_run.call_args.kwargs["timeout"] == 5
    isolated_input = json.loads(isolated_run.call_args.kwargs["input"])
    assert isolated_input["useEnvironmentProxy"] is False
    archive_query = solana_archive_query(
        config["solana"]["creationSchemas"], 100, 200
    )
    assert archive_query["type"] == "solana"
    assert archive_query["fromBlock"] == 100
    assert archive_query["toBlock"] == 200
    assert archive_query["includeAllBlocks"] is False
    assert any(row.get("transactionInstructions") for row in archive_query["instructions"])
    assert all(row.get("isCommitted") is True for row in archive_query["instructions"])
    portal_query = solana_portal_query(
        config["solana"]["creationSchemas"], 100, 200
    )
    assert "includeAllBlocks" not in portal_query
    assert portal_query["fields"]["block"] == {
        "number": True,
        "height": True,
        "timestamp": True,
    }
    normalized_portal_block = normalize_portal_solana_block(
        {"header": {"number": 200, "height": 100, "timestamp": 1}}
    )
    assert normalized_portal_block["header"]["number"] == 100
    assert normalized_portal_block["header"]["slot"] == 200
    assert sqd_request_retryable("source_failure", None, "transport_failure") is True
    assert sqd_request_retryable("source_failure", 500, "http_error") is False

    flux_schema = next(
        row for row in config["solana"]["creationSchemas"] if row["id"] == "fluxbeam-initialize-pool"
    )
    flux_pool = "Pool1111111111111111111111111111111111111"
    flux_vault_a = "VaultA11111111111111111111111111111111111"
    flux_vault_b = "VaultB11111111111111111111111111111111111"
    flux_base = "BaseMint111111111111111111111111111111111"
    flux_quote = "So11111111111111111111111111111111111111112"
    flux_block = {
        "header": {"number": 123, "slot": 456, "timestamp": 1785844886},
        "transactions": [
            {"transactionIndex": 9, "signatures": ["Signature111"], "err": None}
        ],
        "instructions": [
            {
                "transactionIndex": 9,
                "instructionAddress": [1],
                "programId": "11111111111111111111111111111111",
                "accounts": ["Payer111", flux_pool],
                "data": "",
                "isCommitted": True,
            },
            {
                "transactionIndex": 9,
                "instructionAddress": [2],
                "programId": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                "accounts": ["Payer111", flux_vault_a, "Owner111", flux_quote],
                "data": "",
                "isCommitted": True,
            },
            {
                "transactionIndex": 9,
                "instructionAddress": [3],
                "programId": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                "accounts": ["Payer111", flux_vault_b, "Owner111", flux_base],
                "data": "",
                "isCommitted": True,
            },
            {
                "transactionIndex": 9,
                "instructionAddress": [4],
                "programId": flux_schema["programId"],
                "accounts": [
                    flux_pool,
                    "Authority111",
                    flux_vault_a,
                    flux_vault_b,
                    "Mint111",
                    "Lp111",
                    "Lp111",
                    "TokenProgram111",
                ],
                "data": "1" + "1" * 20,
                "isCommitted": True,
            },
        ],
    }
    flux_events = decode_solana_creation_block(flux_block, [flux_schema])
    assert len(flux_events) == 1
    assert flux_events[0]["poolId"] == flux_pool
    assert set(flux_events[0]["tokenAddresses"]) == {flux_base, flux_quote}
    assert flux_events[0]["transactionSignature"] == "Signature111"
    assert flux_events[0]["slot"] == 456
    assert "10 block range" in response_error_detail(
        b'{"jsonrpc":"2.0","error":{"code":-32600,"message":"10 block range"}}'
    )
    assert rpc_error_kind({"code": -32005, "message": "ran out of cu"}) == "quota_limited"
    assert (
        rpc_error_kind({"code": -32000, "message": "exceed maximum block range: 50000"})
        == "rpc_response"
    )
    assert (
        POOL_CREATED_TOPIC
        == "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
    )

    bnb_settings = config["historicalLogSources"]["bnb-mainnet"]
    assert bnb_settings["type"] == "json_rpc_credential_path"
    with patch("gate0_dex_factory_backfill.user_environment", return_value="test-secret"):
        bnb_client, bnb_scan_settings = historical_log_client(
            {"id": "bnb-mainnet", "chainId": 56}, config, RequestLedger(timeout=1)
        )
    assert bnb_client.url.endswith("/test-secret")
    assert bnb_client.safe_url.endswith("/[REDACTED]")
    assert "test-secret" not in bnb_client.safe_url
    assert bnb_scan_settings["maximumBlockSpan"] == 50000

    robinhood_settings = config["historicalLogSources"]["robinhood-mainnet"]
    assert robinhood_settings["type"] == "blockscout_pro_logs"
    with patch("gate0_dex_factory_backfill.user_environment", return_value="test-secret"):
        robinhood_client, robinhood_scan_settings = historical_log_client(
            {"id": "robinhood-mainnet", "chainId": "4663"},
            config,
            RequestLedger(timeout=1),
        )
    assert robinhood_client.query_parameters == {
        "chain_id": "4663",
        "apikey": "test-secret",
    }
    assert "test-secret" not in robinhood_client.safe_url
    assert robinhood_scan_settings["suspiciousLogResultCap"] == 1000

    FakeConnection.instances = []
    with patch("gate0_dex_factory_backfill.http.client.HTTPSConnection", FakeConnection):
        client = JsonRpcClient(
            "test_rpc",
            "https://rpc.example/v2/secret",
            "https://rpc.example/v2/[REDACTED]",
            RequestLedger(timeout=1),
        )
        assert client.call("eth_chainId", []) == "0x1"
        assert client.call("eth_chainId", []) == "0x1"
        assert len(FakeConnection.instances) == 1
        assert len(FakeConnection.instances[0].requests) == 2

    blockscout_ledger = RequestLedger(timeout=1)
    blockscout = BlockscoutLogClient(
        "blockscout_test",
        "https://api.blockscout.test/v2/api",
        "4663",
        blockscout_ledger,
        query_parameters={"chain_id": "4663", "apikey": "test-secret"},
    )
    with patch("gate0_dex_factory_backfill.urllib.request.urlopen", FakeUrlOpenResponse):
        assert blockscout.call(
            "eth_getLogs",
            [
                {
                    "fromBlock": "0x1",
                    "toBlock": "0x2",
                    "address": EMITTER,
                    "topics": [TOPIC],
                }
            ],
        ) == []
    assert "chain_id=4663" in FakeUrlOpenResponse.requested_url
    assert "apikey=test-secret" in FakeUrlOpenResponse.requested_url
    assert "test-secret" not in blockscout.safe_url
    assert all("test-secret" not in row["url"] for row in blockscout_ledger.requests)

    assert first_block_at_or_after(TimestampRpc(), 111, 3) == 2
    assert first_block_at_or_after(TimestampRpc(), 130, 3) == 3
    estimated_range = estimate_window_start(
        TimestampRpc(),
        3,
        110,
        {"blockTimeSampleDistance": 3, "windowSafetyMultiplier": 1.1},
    )
    assert estimated_range["fromBlock"] == 0
    assert estimated_range["fromTimestamp"] <= 110
    failing_rpc = FailingLogRpc()
    inferred, errors = infer_group_schema(
        failing_rpc,
        {"networkId": "ethereum-mainnet", "dexId": "test", "seeds": [sample_seed()]},
        10,
        0,
        130,
        10,
        [2, 15, 120],
        3,
    )
    assert inferred is None and len(errors) == 1
    assert errors[0]["errorType"] == "rpc_failure"
    assert failing_rpc.calls == 1

    creation_rpc = CreationBlockRpc()
    inferred, errors = infer_group_schema(
        creation_rpc,
        {"networkId": "ethereum-mainnet", "dexId": "test", "seeds": [sample_seed()]},
        10,
        0,
        130,
        10,
        [2, 15, 120],
        3,
        maximum_requests=20,
    )
    assert inferred["seedBlockNumber"] == 6
    assert inferred["inferenceMethod"] == "contract_creation_block"
    assert inferred["poolLocation"] == {"source": "data", "index": 0}
    assert not errors

    pool_id = "0x" + "88" * 32
    bytes32_seed = sample_seed()
    bytes32_seed.update(
        {
            "poolAddress": pool_id,
            "poolCreatedAt": "1970-01-01T00:01:46Z",
        }
    )
    bytes32_rpc = Bytes32CreationRpc(pool_id)
    inferred, errors = infer_group_schema(
        bytes32_rpc,
        {
            "networkId": "ethereum-mainnet",
            "dexId": "test-v4",
            "seeds": [bytes32_seed],
        },
        10,
        0,
        130,
        3,
        [2, 15, 120],
        3,
        maximum_requests=20,
    )
    assert inferred["seedBlockNumber"] == 2
    assert inferred["poolLocation"] == {"source": "topic", "index": 1}
    assert inferred["tokenLocations"] == [
        {"source": "topic", "index": 2},
        {"source": "topic", "index": 3},
    ]
    assert inferred["inferenceMethod"] == "timestamp_anchored_bounded_creation_event"
    assert not errors

    solana_rpc = SolanaOwnerRpc()
    solana_group = {
        "networkId": "solana-mainnet",
        "dexId": "test-solana-dex",
        "seeds": [
            {"poolAddress": "PoolOne111111111111111111111111111111111"},
            {"poolAddress": "PoolTwo222222222222222222222222222222222"},
        ],
    }
    with patch("gate0_dex_factory_backfill.helius_client", return_value=solana_rpc):
        solana_results = probe_solana_programs(
            [solana_group],
            {"historicalPoolBackfillStatus": "decoder_required", "boundary": "test"},
            RequestLedger(timeout=1),
        )
    assert solana_rpc.calls == 1
    assert solana_results[0]["seedPoolsAvailable"] == 2
    assert solana_results[0]["seedPoolsChecked"] == 1

    seed = sample_seed()
    other_seed = dict(seed)
    other_seed["dexId"] = "single-pool-dex"
    run = {"pools": [seed, dict(seed), other_seed]}
    groups = observed_dex_groups(run)
    assert len(groups) == 2
    assert groups[0]["dexId"] == "test-v2"
    assert len(groups[0]["seeds"]) == 2

    schema = choose_creation_log([sample_log()], seed)
    assert schema["emitter"] == EMITTER
    assert schema["eventTopic"] == TOPIC
    assert len(schema["tokenLocations"]) == 2
    native_quote_seed = sample_seed()
    native_quote_seed["quoteToken"] = {
        "address": "0x0000000000000000000000000000000000000000"
    }
    assert len(choose_creation_log([sample_log()], native_quote_seed)["tokenLocations"]) == 1
    transfer_log = sample_log()
    transfer_log["topics"][0] = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    assert choose_creation_log([transfer_log], seed) is None
    filters = creation_log_filters(seed, 1, 2)
    assert len(filters) == 5
    assert filters[0]["topics"][0].startswith("0x0d3648")
    assert filters[-1]["topics"][3] == padded(POOL)
    chunks = list(chunk_log_filter({"fromBlock": "0x1", "toBlock": "0x19"}, 10))
    assert [(int(row["fromBlock"], 16), int(row["toBlock"], 16)) for row in chunks] == [
        (1, 10),
        (11, 20),
        (21, 25),
    ]
    schema.update(
        {
            "networkId": "ethereum-mainnet",
            "dexIds": ["test-v2"],
            "poolTemplate": POOL,
            "seedsAvailable": 1,
        }
    )
    event = decode_log(sample_log(), schema)
    assert event["poolId"] == POOL
    assert event["tokenAddresses"] == [TOKEN_A, TOKEN_B]

    duplicate = dict(schema)
    duplicate["dexIds"] = ["test-v2-alias"]
    duplicate["tokenLocations"] = list(reversed(schema["tokenLocations"]))
    merged = merge_schemas([schema, duplicate])
    assert len(merged) == 1
    assert merged[0]["dexIds"] == ["test-v2", "test-v2-alias"]

    v4_schema = dict(schema)
    v4_schema.update(
        {
            "dexIds": ["test-v4"],
            "poolLocation": {"source": "topic", "index": 1},
            "poolTemplate": "0x" + "88" * 32,
            "tokenLocations": [
                {"source": "topic", "index": 2},
                {"source": "topic", "index": 3},
            ],
        }
    )
    v4_alias = dict(v4_schema)
    v4_alias["dexIds"] = ["test-launcher-alias"]
    v4_alias["poolTemplate"] = "0x" + "99" * 32
    merged_v4 = merge_schemas([v4_schema, v4_alias])
    assert len(merged_v4) == 1
    assert merged_v4[0]["dexIds"] == ["test-launcher-alias", "test-v4"]

    settings = {
        "initialBlockSpan": 4,
        "minimumBlockSpan": 1,
        "maximumBlockSpan": 4,
        "suspiciousLogResultCap": 10,
    }
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        registry_config = dict(config)
        registry_config["schemaRegistry"] = str(root / "registry.json")
        registry = update_schema_registry(registry_config, [schema])
        assert len(registry["schemas"]) == 1
        assert registry["schemas"][0]["verification"] == "chain_event_matches_seed_pool_and_token_fields"

        rollup_config = dict(registry_config)
        rollup_config["outputRoot"] = str(root / "backfill")
        rollup_config["coverageRollup"] = str(root / "backfill" / "coverage-rollup.json")
        run_dir = root / "backfill" / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "summary.json").write_text(
            json.dumps(
                {
                    "runId": "test-run",
                    "finishedAt": "2026-08-04T00:00:00Z",
                    "execution": {"selectedNetworks": ["ethereum-mainnet"], "inferenceOnly": False},
                    "coverage": {
                        "observedDexGroups": 1,
                        "evmDexGroups": 1,
                        "evmScanUnits": 1,
                        "evmScansComplete": 1,
                        "solanaDexGroups": 0,
                        "candidateTokens": 1,
                        "eventRows": 1,
                        "allObservedEvmGroupsInferred": True,
                        "allEvmScansComplete": True,
                    },
                    "networkRanges": {},
                    "evmScanResults": [
                        {
                            "networkId": "ethereum-mainnet",
                            "dexIds": ["test-v2"],
                            "complete": True,
                            "events": 1,
                        }
                    ],
                    "solanaProgramResults": [],
                    "requestSummary": {"total": 1, "success": 1, "quotaLimited": 0, "sourceFailure": 0},
                }
            ),
            encoding="utf-8",
        )
        inference_dir = root / "backfill" / "runs" / "test-inference"
        inference_dir.mkdir(parents=True)
        (inference_dir / "summary.json").write_text(
            json.dumps(
                {
                    "runId": "test-inference",
                    "finishedAt": "2026-08-04T01:00:00Z",
                    "execution": {
                        "selectedNetworks": ["ethereum-mainnet"],
                        "inferenceOnly": True,
                    },
                    "coverage": {"candidateTokens": 0},
                    "networkRanges": {},
                    "evmGroupResults": [
                        {
                            "networkId": "ethereum-mainnet",
                            "dexId": "test-v2",
                            "state": "unsupported",
                        }
                    ],
                    "evmScanResults": [],
                    "solanaProgramResults": [],
                    "requestSummary": {
                        "total": 1,
                        "success": 1,
                        "quotaLimited": 0,
                        "sourceFailure": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        solana_dir = root / "backfill" / "runs" / "test-solana"
        solana_dir.mkdir(parents=True)
        (solana_dir / "summary.json").write_text(
            json.dumps(
                {
                    "runId": "test-solana",
                    "finishedAt": "2026-08-04T02:00:00Z",
                    "execution": {
                        "selectedNetworks": ["solana-mainnet"],
                        "inferenceOnly": False,
                    },
                    "coverage": {"candidateTokens": 3},
                    "networkRanges": {},
                    "evmGroupResults": [],
                    "evmScanResults": [],
                    "solanaProgramResults": [
                        {
                            "networkId": "solana-mainnet",
                            "dexId": "test-solana",
                            "state": "success",
                            "decoderAvailable": True,
                        }
                    ],
                    "solanaScanResults": [
                        {
                            "networkId": "solana-mainnet",
                            "events": 4,
                            "sourceRangeComplete": True,
                            "requestedWindowComplete": False,
                        }
                    ],
                    "requestSummary": {
                        "total": 1,
                        "success": 1,
                        "quotaLimited": 0,
                        "sourceFailure": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        rollup = build_coverage_rollup(rollup_config)
        assert rollup["coverage"]["verifiedEvmSchemas"] == 1
        assert rollup["coverage"]["eventRows"] == 5
        assert rollup["networkResults"][0]["latestRunId"] == "test-run"
        assert rollup["networkResults"][0]["latestActivityRunId"] == "test-inference"
        assert rollup["coverage"]["historicalBackfillComplete"] is False
        solana_rollup = next(
            row for row in rollup["networkResults"] if row["networkId"] == "solana-mainnet"
        )
        assert solana_rollup["eventRows"] == 4
        assert solana_rollup["solanaSourceRangeComplete"] is True
        assert solana_rollup["solanaRequestedWindowComplete"] is False
        assert solana_rollup["historicalBackfillComplete"] is False

        result = scan_schema(ScanRpc(), schema, 0, 3, settings, root / "events.jsonl")
        assert result["complete"] is True
        assert result["events"] == 1
        assert Path(result["path"]).exists()
        rows = [json.loads(line) for line in Path(result["path"]).read_text(encoding="utf-8").splitlines()]
        assert rows[0]["poolId"] == POOL

        solana_settings = dict(config["solana"])
        solana_settings.update(
            {
                "portalFullHistoryEnabled": False,
                "legacyArchiveWindowDays": 29,
                "legacyArchiveUseRequestedCutoff": False,
                "legacyArchiveBlockSpan": 1,
                "legacyArchiveWorkers": 1,
                "progressPrintEveryPages": 1,
            }
        )
        with patch(
            "gate0_dex_factory_backfill.SqdSolanaArchiveClient",
            FakeSolanaArchiveClient,
        ):
            solana_scan = scan_solana_archive(
                solana_settings,
                config["solana"]["creationSchemas"][:1],
                datetime.now(timezone.utc) - timedelta(days=90),
                root / "solana-events.jsonl",
                RequestLedger(timeout=1),
            )
        assert solana_scan["sourceRangeComplete"] is True
        assert solana_scan["requestedWindowComplete"] is False
        assert solana_scan["events"] == 0
        assert Path(solana_scan["path"]).exists()

        portal_settings = dict(config["solana"])
        portal_settings.update(
            {
                "portalFullHistoryEnabled": True,
                "portalBlockSpan": 1,
                "portalWorkers": 1,
                "progressPrintEveryPages": 1,
            }
        )
        with patch(
            "gate0_dex_factory_backfill.SqdSolanaArchiveClient",
            FakeSolanaPortalClient,
        ):
            portal_scan = scan_solana_archive(
                portal_settings,
                config["solana"]["creationSchemas"][:1],
                datetime.now(timezone.utc) - timedelta(days=90),
                root / "solana-portal-events.jsonl",
                RequestLedger(timeout=1),
            )
        assert portal_scan["historicalSource"] == "sqd_portal_finalized_stream"
        assert portal_scan["sourceRangeComplete"] is True
        assert portal_scan["requestedWindowComplete"] is True
        assert portal_scan["coverageStartsAtSlot"] == 200
        assert portal_scan["coverageStartsAtBlockHeight"] == 100
        assert portal_scan["coverageEndsAtSlot"] == 202
        assert portal_scan["coverageEndsAtBlockHeight"] == 102
        assert portal_scan["uncoveredRange"] is None

        reconnecting = ReconnectingScanRpc()
        reconnected = scan_schema(reconnecting, schema, 2, 2, settings, root / "reconnected.jsonl")
        assert reconnected["complete"] is True
        assert reconnecting.log_calls == 2

        reconnect_then_shrink = ReconnectThenShrinkRpc()
        shrunk = scan_schema(
            reconnect_then_shrink, schema, 0, 3, settings, root / "reconnect-shrink.jsonl"
        )
        assert shrunk["complete"] is True
        assert reconnect_then_shrink.log_calls > 4

        failed = scan_schema(ScanRpc(always_fail=True), schema, 0, 0, settings, root / "failed.jsonl")
        assert failed["complete"] is False
        assert failed["failedRange"]["fromBlock"] == 0
        assert failed["path"].endswith(".partial.jsonl")

        semantic_settings = dict(settings)
        semantic_settings.update(
            {
                "initialBlockSpan": 1,
                "maximumBlockSpan": 1,
                "semanticCheckMinimumEvents": 2,
                "maximumRepeatedPoolRatio": 0.1,
            }
        )
        semantic_failure = scan_schema(
            RepeatedPoolScanRpc(),
            schema,
            0,
            2,
            semantic_settings,
            root / "semantic-failure.jsonl",
        )
        assert semantic_failure["complete"] is False
        assert semantic_failure["failedRange"]["errorKind"] == "semantic_mismatch"
        assert semantic_failure["earliestTokens"] == {}

    print("gate0 DEX factory backfill checks passed")


if __name__ == "__main__":
    main()
