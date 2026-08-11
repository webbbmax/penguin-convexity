#!/usr/bin/env python3
"""Gate 0 only: read-only DEX creation-event backfill.

The tool derives active EVM creation-event schemas from the latest Gecko pool
run, scans those emitters for 90 days, and keeps every uncovered range visible.
It never writes the product database or C2.0 assets.
"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import http.client
import json
import math
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contract_tradeability import user_environment
from gate0_shadow_preflight import RequestLedger, atomic_write_json, utc_now


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "gate0-dex-backfill.json"
HEX_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEX_POOL_ID = re.compile(r"^0x[0-9a-fA-F]{64}$")
PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SOLANA_SYSTEM_PROGRAM = "11111111111111111111111111111111"
SOLANA_ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SOLANA_TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}
SQD_HTTP_WORKER = Path(__file__).resolve().with_name("sqd_http_worker.py")


class RpcError(RuntimeError):
    def __init__(self, message, kind="rpc_response"):
        super().__init__(message)
        self.kind = kind


def response_error_detail(raw):
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")[:500]
    error = decoded.get("error") if isinstance(decoded, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:500]
    return str(error or decoded)[:500]


def rpc_error_kind(error):
    if not isinstance(error, dict):
        return "rpc_response"
    message = str(error.get("message") or error).lower()
    quota_markers = (
        "ran out of cu",
        "compute units per second",
        "maximum api usage limit",
        "rate limit",
        "too many requests",
    )
    if int(error.get("code") or 0) == -32005 and any(
        marker in message for marker in quota_markers
    ):
        return "quota_limited"
    return "rpc_response"


def load_config(path=DEFAULT_CONFIG_PATH):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    boundary = payload["boundary"]
    if boundary["productCodeWritesAllowed"] or boundary["productionDatabaseWritesAllowed"]:
        raise ValueError("Gate 0 backfill must stay outside product code and database writes")
    if boundary["projectMinimumWaitDays"] != 0:
        raise ValueError("new projects must not wait for artificial observation days")
    if boundary["shortHistorySyntheticDaysAllowed"]:
        raise ValueError("historical days must never be fabricated")
    return payload


def parse_utc(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def hex_number(value):
    return int(value, 16) if isinstance(value, str) else int(value)


def hex_block(value):
    return hex(int(value))


def safe_slug(value):
    return re.sub(r"[^a-z0-9._-]+", "-", str(value).lower()).strip("-")


def base58_decode(value):
    number = 0
    for character in str(value or ""):
        number = number * 58 + BASE58_ALPHABET.index(character)
    leading_zeroes = len(str(value or "")) - len(str(value or "").lstrip("1"))
    payload = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * leading_zeroes + payload


def solana_schema_groups(settings):
    groups = defaultdict(list)
    for schema in settings.get("creationSchemas") or []:
        dex_ids = list(schema.get("dexIds") or [])
        if schema.get("bagsAccount"):
            dex_ids.append("bags-fm")
        for dex_id in sorted(set(dex_ids)):
            groups[dex_id].append(schema)
    return dict(groups)


def solana_archive_query(schemas, start_block, end_block):
    filters = {}
    for schema in schemas:
        key = (
            schema["programId"],
            schema["discriminatorField"],
            schema["discriminator"],
            bool(schema.get("includeTransactionInstructions")),
        )
        row = {
            "programId": [schema["programId"]],
            schema["discriminatorField"]: [schema["discriminator"]],
            "isCommitted": True,
            "transaction": True,
        }
        if schema.get("includeTransactionInstructions"):
            row["transactionInstructions"] = True
        filters[key] = row
    return {
        "type": "solana",
        "fromBlock": int(start_block),
        "toBlock": int(end_block),
        "includeAllBlocks": False,
        "fields": {
            "block": {"slot": True, "timestamp": True},
            "transaction": {"signatures": True, "err": True},
            "instruction": {
                "programId": True,
                "accounts": True,
                "data": True,
                "isCommitted": True,
                "error": True,
            },
        },
        "instructions": list(filters.values()),
    }


def solana_portal_query(schemas, start_slot, end_slot):
    query = solana_archive_query(schemas, start_slot, end_slot)
    query.pop("includeAllBlocks", None)
    query["fields"]["block"] = {
        "number": True,
        "height": True,
        "timestamp": True,
    }
    return query


def normalize_portal_solana_block(block):
    normalized = dict(block)
    header = dict(normalized.get("header") or {})
    slot = header.get("number")
    height = header.get("height")
    if slot is None or height is None:
        raise RpcError("SQD Portal block is missing slot or height")
    header["slot"] = int(slot)
    header["number"] = int(height)
    normalized["header"] = header
    return normalized


def sqd_request_retryable(state, status, error_kind):
    return state == "quota_limited" or status == 503 or error_kind == "transport_failure"


def read_response_with_deadline(response, timeout_seconds):
    """Read a response without allowing a trickling body to reset the timeout forever."""
    deadline = time.monotonic() + timeout_seconds
    chunks = []
    reader = getattr(response, "read1", response.read)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"total response deadline exceeded: {timeout_seconds}s")
        stream = getattr(response, "fp", None)
        raw_stream = getattr(stream, "raw", None)
        response_socket = getattr(raw_stream, "_sock", None)
        if response_socket is not None:
            response_socket.settimeout(min(5.0, max(0.1, remaining)))
        chunk = reader(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def isolated_http_request(url, body, headers, socket_timeout, total_timeout):
    payload = {
        "url": url,
        "bodyBase64": base64.b64encode(body).decode("ascii") if body is not None else "",
        "headers": headers,
        "socketTimeoutSeconds": socket_timeout,
        "useEnvironmentProxy": False,
    }
    creation_flags = 0x08000000 if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            [sys.executable, str(SQD_HTTP_WORKER)],
            input=json.dumps(payload, separators=(",", ":")),
            capture_output=True,
            text=True,
            timeout=total_timeout,
            creationflags=creation_flags,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f"total response deadline exceeded: {total_timeout}s") from error
    if completed.returncode != 0:
        raise ConnectionError(f"isolated HTTP worker exited {completed.returncode}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ConnectionError("isolated HTTP worker returned invalid JSON") from error
    if result.get("errorType"):
        raise ConnectionError(f"{result['errorType']}: {result.get('error', '')}")
    return {
        "status": int(result["status"]),
        "headers": {str(key).lower(): value for key, value in result.get("headers", {}).items()},
        "raw": base64.b64decode(result.get("bodyBase64") or ""),
    }


def in_process_http_request(url, body, headers, socket_timeout, use_environment_proxy=False):
    request = urllib.request.Request(url, data=body, headers=headers)
    opener = (
        urllib.request.build_opener()
        if use_environment_proxy
        else urllib.request.build_opener(urllib.request.ProxyHandler({}))
    )
    try:
        with opener.open(request, timeout=socket_timeout) as response:
            return {
                "status": response.status,
                "headers": {
                    str(key).lower(): value for key, value in response.headers.items()
                },
                "raw": read_response_with_deadline(response, socket_timeout),
            }
    except urllib.error.HTTPError as error:
        return {
            "status": error.code,
            "headers": {
                str(key).lower(): value for key, value in (error.headers.items() if error.headers else [])
            },
            "raw": error.read(),
        }


def solana_instruction_matches(instruction, schema):
    if instruction.get("programId") != schema["programId"]:
        return False
    try:
        raw = base58_decode(instruction.get("data") or "")
    except ValueError:
        return False
    width = int(schema["discriminatorField"].removeprefix("d"))
    return "0x" + raw[:width].hex() == schema["discriminator"].lower()


def solana_transaction_mints(instructions, transaction_index, vaults):
    mints = []
    vaults = set(vaults)
    for instruction in instructions:
        if instruction.get("transactionIndex") != transaction_index:
            continue
        accounts = instruction.get("accounts") or []
        program_id = instruction.get("programId")
        mint = ""
        if (
            program_id == SOLANA_ASSOCIATED_TOKEN_PROGRAM
            and len(accounts) > 3
            and accounts[1] in vaults
        ):
            mint = accounts[3]
        elif program_id in SOLANA_TOKEN_PROGRAMS and len(accounts) > 1 and accounts[0] in vaults:
            mint = accounts[1]
        if mint and mint not in mints:
            mints.append(mint)
    return mints


def decode_solana_creation_block(block, schemas):
    instructions = block.get("instructions") or []
    transaction_signatures = {
        row.get("transactionIndex"): (row.get("signatures") or [""])[0]
        for row in block.get("transactions") or []
    }
    events = []
    seen = set()
    for instruction in instructions:
        for schema in schemas:
            if not solana_instruction_matches(instruction, schema):
                continue
            accounts = instruction.get("accounts") or []
            pool_index = schema["poolAccountIndex"]
            transaction_index = instruction.get("transactionIndex")
            if pool_index >= len(accounts):
                continue
            pool_id = accounts[pool_index]
            if schema.get("requiresCreatedPoolInTransaction") and not any(
                row.get("transactionIndex") == transaction_index
                and row.get("programId") == SOLANA_SYSTEM_PROGRAM
                and len(row.get("accounts") or []) > 1
                and row["accounts"][1] == pool_id
                for row in instructions
            ):
                continue
            tokens = [
                accounts[index]
                for index in schema.get("tokenAccountIndices") or []
                if index < len(accounts)
            ]
            if schema.get("vaultAccountIndices"):
                vaults = [
                    accounts[index]
                    for index in schema["vaultAccountIndices"]
                    if index < len(accounts)
                ]
                tokens.extend(solana_transaction_mints(instructions, transaction_index, vaults))
            tokens.extend(schema.get("impliedTokenAddresses") or [])
            tokens = list(dict.fromkeys(token for token in tokens if token))
            dex_ids = list(schema.get("dexIds") or [])
            if schema.get("bagsAccount") in accounts:
                dex_ids.append("bags-fm")
            instruction_address = instruction.get("instructionAddress") or []
            event_key = (
                block["header"]["number"],
                transaction_index,
                tuple(instruction_address),
                schema["id"],
            )
            if event_key in seen:
                continue
            seen.add(event_key)
            events.append(
                {
                    "networkId": "solana-mainnet",
                    "dexIds": sorted(set(dex_ids)),
                    "schemaId": schema["id"],
                    "programId": schema["programId"],
                    "blockNumber": block["header"]["number"],
                    "slot": block["header"].get("slot"),
                    "blockTimestamp": block["header"].get("timestamp"),
                    "transactionIndex": transaction_index,
                    "transactionSignature": transaction_signatures.get(transaction_index, ""),
                    "instructionAddress": instruction_address,
                    "poolId": pool_id,
                    "tokenAddresses": tokens,
                    "decodeComplete": bool(tokens),
                    "decodeError": "" if tokens else "token_mints_not_decoded",
                }
            )
    return events


def word_for(value):
    raw = str(value or "").lower().removeprefix("0x")
    if len(raw) not in (40, 64) or not re.fullmatch(r"[0-9a-f]+", raw):
        return ""
    return raw.rjust(64, "0")


def data_words(value):
    raw = str(value or "").lower().removeprefix("0x")
    if len(raw) % 64:
        return []
    return [raw[index : index + 64] for index in range(0, len(raw), 64)]


def find_location(log, value):
    expected = word_for(value)
    if not expected:
        return None
    for index, topic in enumerate(log.get("topics") or []):
        if str(topic).lower().removeprefix("0x").rjust(64, "0") == expected:
            return {"source": "topic", "index": index}
    for index, word in enumerate(data_words(log.get("data"))):
        if word == expected:
            return {"source": "data", "index": index}
    return None


def value_at(log, location):
    if not location:
        return ""
    if location["source"] == "topic":
        values = log.get("topics") or []
        if location["index"] >= len(values):
            return ""
        return str(values[location["index"]]).lower().removeprefix("0x").rjust(64, "0")
    values = data_words(log.get("data"))
    if location["index"] >= len(values):
        return ""
    return values[location["index"]]


def as_address(word):
    if not word or len(word) != 64:
        return ""
    address = "0x" + word[-40:]
    return "" if address == "0x" + "0" * 40 else address


def as_pool_id(word, template):
    if not word or len(word) != 64:
        return ""
    if HEX_ADDRESS.fullmatch(str(template or "")):
        return "0x" + word[-40:]
    return "0x" + word


class JsonRpcClient:
    def __init__(
        self, source, url, safe_url, ledger, minimum_interval=0, use_environment_proxy=False
    ):
        self.source = source
        self.url = url
        self.safe_url = safe_url
        self.ledger = ledger
        self.minimum_interval = minimum_interval
        self.next_id = 1
        self.block_cache = {}
        parsed = urllib.parse.urlsplit(url)
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.connection = None
        self.use_environment_proxy = use_environment_proxy
        self.transport_label = (
            "system_proxy_https" if use_environment_proxy else "direct_persistent_https"
        )

    def _close(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
        self.connection = None

    def _record(self, started, status, state, raw, error, headers):
        self.ledger.last_request_at[self.source] = time.monotonic()
        self.ledger.requests.append(
            {
                "source": self.source,
                "url": self.safe_url,
                "observedAt": utc_now(),
                "httpStatus": status,
                "state": state,
                "latencyMs": round((time.monotonic() - started) * 1000),
                "responseBytes": len(raw),
                "rateLimit": {
                    "limit": headers.get("x-ratelimit-limit"),
                    "remaining": headers.get("x-ratelimit-remaining"),
                    "reset": headers.get("x-ratelimit-reset"),
                    "retryAfter": headers.get("retry-after"),
                },
                "error": error,
                "transport": self.transport_label,
            }
        )

    def _request(self, payload, attempts=3):
        previous = self.ledger.last_request_at.get(self.source)
        if previous is not None:
            wait_seconds = self.minimum_interval - (time.monotonic() - previous)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        last_error = ""
        last_kind = "transport_failure"
        for attempt in range(attempts):
            started = time.monotonic()
            status = None
            raw = b""
            headers = {}
            state = "source_failure"
            error_text = ""
            try:
                request_headers = {
                        "User-Agent": "Penguin-Convexity-Gate0-Backfill/0.1",
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "Content-Type": "application/json",
                        "Connection": "keep-alive",
                }
                if self.use_environment_proxy:
                    request = urllib.request.Request(
                        self.url, data=body, headers=request_headers, method="POST"
                    )
                    with urllib.request.urlopen(request, timeout=self.ledger.timeout) as response:
                        status = response.status
                        headers = {
                            str(key).lower(): value for key, value in response.headers.items()
                        }
                        raw = response.read()
                else:
                    if self.connection is None:
                        self.connection = http.client.HTTPSConnection(
                            self.host, self.port, timeout=self.ledger.timeout
                        )
                    self.connection.request("POST", self.path, body=body, headers=request_headers)
                    response = self.connection.getresponse()
                    status = response.status
                    headers = {str(key).lower(): value for key, value in response.getheaders()}
                    chunks = []
                    while True:
                        remaining = self.ledger.timeout - (time.monotonic() - started)
                        if remaining <= 0:
                            raise TimeoutError(
                                f"total response deadline exceeded: {self.ledger.timeout}s"
                            )
                        connection_socket = getattr(self.connection, "sock", None)
                        if connection_socket is not None:
                            connection_socket.settimeout(min(5.0, max(0.1, remaining)))
                        reader = getattr(response, "read1", response.read)
                        chunk = reader(64 * 1024)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                if str(headers.get("content-encoding", "")).lower() == "gzip":
                    raw = gzip.decompress(raw)
                if status == 429:
                    state = "quota_limited"
                    error_text = "HTTP 429"
                    last_kind = "quota_limited"
                elif status in (401, 403):
                    state = "configuration_missing"
                    error_text = f"HTTP {status}"
                    last_kind = "configuration_missing"
                elif not 200 <= status < 300:
                    state = "source_failure"
                    detail = response_error_detail(raw)
                    error_text = f"HTTP {status}: {detail}" if detail else f"HTTP {status}"
                    last_kind = "http_error"
                else:
                    decoded = json.loads(raw.decode("utf-8"))
                    state = "success"
                    self._record(started, status, state, raw, "", headers)
                    return decoded
            except urllib.error.HTTPError as error:
                status = error.code
                raw = error.read()
                headers = {str(key).lower(): value for key, value in error.headers.items()}
                error_text = f"HTTP {status}: {response_error_detail(raw)}"
                state = "quota_limited" if status == 429 else "source_failure"
                last_kind = "quota_limited" if status == 429 else "http_error"
            except (
                OSError,
                TimeoutError,
                ConnectionError,
                urllib.error.URLError,
                http.client.HTTPException,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ) as error:
                error_text = f"{type(error).__name__}: {error}"
                state = "source_failure"
                last_kind = "response_incomplete" if status == 200 else "transport_failure"
            self._record(started, status, state, raw, error_text, headers)
            last_error = error_text or state
            self._close()
            if attempt < attempts - 1 and state in {"source_failure", "quota_limited"}:
                failure_waits = (1, 3)
                quota_waits = (5, 15)
                wait_index = min(attempt, len(failure_waits) - 1)
                time.sleep(failure_waits[wait_index] if state == "source_failure" else quota_waits[wait_index])
                continue
            break
        raise RpcError(last_error or "RPC request failed", kind=last_kind)

    def call(self, method, params, attempts=3):
        request_id = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        response = self._request(payload, attempts=attempts)
        if response.get("error"):
            error = response["error"]
            kind = rpc_error_kind(error)
            if self.ledger.requests and self.ledger.requests[-1]["source"] == self.source:
                self.ledger.requests[-1]["state"] = (
                    "quota_limited" if kind == "quota_limited" else "source_failure"
                )
                self.ledger.requests[-1]["error"] = str(error)[:500]
            raise RpcError(json.dumps(error, ensure_ascii=False), kind=kind)
        return response.get("result")

    def batch(self, calls):
        payload = []
        ids = []
        for method, params in calls:
            request_id = self.next_id
            self.next_id += 1
            ids.append(request_id)
            payload.append({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        response = self._request(payload)
        if not isinstance(response, list):
            raise RpcError("RPC batch request failed")
        indexed = {row.get("id"): row for row in response}
        results = []
        for request_id in ids:
            row = indexed.get(request_id) or {}
            if row.get("error"):
                error = row["error"]
                kind = rpc_error_kind(error)
                if self.ledger.requests and self.ledger.requests[-1]["source"] == self.source:
                    self.ledger.requests[-1]["state"] = (
                        "quota_limited" if kind == "quota_limited" else "source_failure"
                    )
                    self.ledger.requests[-1]["error"] = str(error)[:500]
                raise RpcError(json.dumps(error, ensure_ascii=False), kind=kind)
            results.append(row.get("result"))
        return results

    def block(self, number):
        number = int(number)
        if number not in self.block_cache:
            row = self.call("eth_getBlockByNumber", [hex_block(number), False])
            if not row:
                raise RpcError(f"block {number} not returned")
            self.block_cache[number] = row
        return self.block_cache[number]

    def block_timestamp(self, number):
        return hex_number(self.block(number)["timestamp"])

    def block_timestamps(self, numbers, batch_size):
        missing = [int(number) for number in sorted(set(numbers)) if int(number) not in self.block_cache]
        for index in range(0, len(missing), batch_size):
            batch = missing[index : index + batch_size]
            rows = self.batch([("eth_getBlockByNumber", [hex_block(number), False]) for number in batch])
            for number, row in zip(batch, rows):
                if row:
                    self.block_cache[number] = row
        return {
            number: datetime.fromtimestamp(self.block_timestamp(number), timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
            for number in sorted(set(int(value) for value in numbers))
        }

    def range_for_window(self, cutoff_timestamp, settings):
        latest = hex_number(self.call("eth_blockNumber", []))
        return estimate_window_start(self, latest, int(cutoff_timestamp), settings)


class BlockscoutLogClient(JsonRpcClient):
    def __init__(
        self,
        source,
        base_url,
        chain_id,
        ledger,
        minimum_interval=0,
        query_parameters=None,
    ):
        super().__init__(source, base_url, base_url, ledger, minimum_interval)
        self.chain_id = chain_id
        self.query_parameters = dict(query_parameters or {})
        self.transport_label = "system_proxy_https"

    def _legacy_get(self, parameters, attempts=3):
        parameters = {**self.query_parameters, **parameters}
        query = urllib.parse.urlencode(parameters)
        path = self.path + ("&" if "?" in self.path else "?") + query
        previous = self.ledger.last_request_at.get(self.source)
        if previous is not None:
            wait_seconds = self.minimum_interval - (time.monotonic() - previous)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        last_error = ""
        last_kind = "transport_failure"
        for attempt in range(attempts):
            started = time.monotonic()
            status = None
            raw = b""
            headers = {}
            state = "source_failure"
            error_text = ""
            try:
                request = urllib.request.Request(
                    self.url + ("&" if "?" in self.url else "?") + query,
                    headers={
                        "User-Agent": "Penguin-Convexity-Gate0-Backfill/0.1",
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                    },
                )
                with urllib.request.urlopen(request, timeout=self.ledger.timeout) as response:
                    status = response.status
                    headers = {str(key).lower(): value for key, value in response.headers.items()}
                    raw = response.read()
                if str(headers.get("content-encoding", "")).lower() == "gzip":
                    raw = gzip.decompress(raw)
                if status == 429:
                    state = "quota_limited"
                    error_text = "HTTP 429"
                    last_kind = "quota_limited"
                elif not 200 <= status < 300:
                    detail = response_error_detail(raw)
                    error_text = f"HTTP {status}: {detail}" if detail else f"HTTP {status}"
                    last_kind = "http_error"
                else:
                    decoded = json.loads(raw.decode("utf-8"))
                    result = decoded.get("result") if isinstance(decoded, dict) else None
                    message = str(decoded.get("message") or "") if isinstance(decoded, dict) else ""
                    no_data = decoded.get("status") == "0" and "no" in message.lower()
                    if isinstance(decoded, dict) and decoded.get("error"):
                        error_text = str(decoded["error"])[:500]
                        last_kind = "rpc_response"
                    elif decoded.get("status") == "0" and not no_data:
                        error_text = str(result or message or "Blockscout source failure")[:500]
                        last_kind = "rpc_response"
                    else:
                        state = "success"
                        self._record(started, status, state, raw, "", headers)
                        return decoded
            except urllib.error.HTTPError as error:
                status = error.code
                raw = error.read()
                headers = {str(key).lower(): value for key, value in error.headers.items()}
                error_text = f"HTTP {status}: {response_error_detail(raw)}"
                last_kind = "quota_limited" if status == 429 else "http_error"
                state = "quota_limited" if status == 429 else "source_failure"
            except (
                OSError,
                TimeoutError,
                ConnectionError,
                urllib.error.URLError,
                http.client.HTTPException,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ) as error:
                error_text = f"{type(error).__name__}: {error}"
                last_kind = "response_incomplete" if status == 200 else "transport_failure"
            self._record(started, status, state, raw, error_text, headers)
            last_error = error_text or state
            self._close()
            if attempt < attempts - 1:
                waits = (15, 45) if state == "quota_limited" else (2, 6)
                time.sleep(waits[min(attempt, 1)])
                continue
            break
        raise RpcError(last_error or "Blockscout request failed", kind=last_kind)

    def call(self, method, params, attempts=3):
        if method == "eth_chainId":
            return hex(int(self.chain_id))
        if method != "eth_getLogs":
            raise RpcError(f"unsupported Blockscout method: {method}", kind="configuration_missing")
        log_filter = params[0]
        decoded = self._legacy_get(
            {
                "module": "logs",
                "action": "getLogs",
                "fromBlock": hex_number(log_filter["fromBlock"]),
                "toBlock": hex_number(log_filter["toBlock"]),
                "address": log_filter["address"],
                "topic0": log_filter["topics"][0],
            },
            attempts=attempts,
        )
        rows = decoded.get("result") if isinstance(decoded.get("result"), list) else []
        normalized = []
        for row in rows:
            normalized.append(
                {
                    "address": row.get("address"),
                    "topics": row.get("topics") or [],
                    "data": row.get("data") or "0x",
                    "blockNumber": row.get("blockNumber"),
                    "blockTimestamp": row.get("timeStamp"),
                    "transactionHash": row.get("transactionHash") or row.get("transaction_hash"),
                    "logIndex": row.get("logIndex")
                    if row.get("logIndex") not in {None, "0x"}
                    else "0x0",
                }
            )
        return normalized

    def range_for_window(self, cutoff_timestamp):
        latest = self._legacy_get({"module": "block", "action": "eth_block_number"})
        start = self._legacy_get(
            {
                "module": "block",
                "action": "getblocknobytime",
                "timestamp": int(cutoff_timestamp),
                "closest": "before",
            }
        )
        latest_block = hex_number(latest["result"])
        start_block = int(start["result"]["blockNumber"])
        latest_timestamp = int(datetime.now(timezone.utc).timestamp())
        elapsed = max(1, latest_timestamp - int(cutoff_timestamp))
        return {
            "fromBlock": start_block,
            "toBlock": latest_block,
            "fromTimestamp": int(cutoff_timestamp),
            "toTimestamp": latest_timestamp,
            "averageBlockSeconds": elapsed / max(1, latest_block - start_block),
            "rangeSource": "blockscout_getblocknobytime_closest_before",
        }


def first_block_at_or_after(rpc, target_timestamp, latest_block, lower_bound=0):
    low = max(0, int(lower_bound))
    high = int(latest_block)
    target = int(target_timestamp)
    low_timestamp = rpc.block_timestamp(low)
    if low_timestamp >= target:
        return low
    high_timestamp = rpc.block_timestamp(high)
    if high_timestamp < target:
        return high + 1
    while high - low > 1:
        if high_timestamp > low_timestamp:
            ratio = (target - low_timestamp) / (high_timestamp - low_timestamp)
            middle = low + int((high - low) * ratio)
            middle = min(high - 1, max(low + 1, middle))
        else:
            middle = (low + high) // 2
        middle_timestamp = rpc.block_timestamp(middle)
        if middle_timestamp >= target:
            high = middle
            high_timestamp = middle_timestamp
        else:
            low = middle
            low_timestamp = middle_timestamp
    return high


def estimate_window_start(rpc, latest_block, cutoff_timestamp, settings):
    latest_timestamp = rpc.block_timestamp(latest_block)
    sample_block = max(0, latest_block - settings["blockTimeSampleDistance"])
    sample_timestamp = rpc.block_timestamp(sample_block)
    elapsed_blocks = max(1, latest_block - sample_block)
    average_block_seconds = max(0.001, (latest_timestamp - sample_timestamp) / elapsed_blocks)
    required_seconds = max(0, latest_timestamp - int(cutoff_timestamp))
    blocks_back = math.ceil(
        required_seconds / average_block_seconds * settings["windowSafetyMultiplier"]
    )
    start_block = max(0, latest_block - blocks_back)
    start_timestamp = rpc.block_timestamp(start_block)
    while start_block > 0 and start_timestamp > cutoff_timestamp:
        blocks_back = max(blocks_back + 1, math.ceil(blocks_back * 1.5))
        start_block = max(0, latest_block - blocks_back)
        start_timestamp = rpc.block_timestamp(start_block)
    return {
        "fromBlock": start_block,
        "toBlock": latest_block,
        "fromTimestamp": start_timestamp,
        "toTimestamp": latest_timestamp,
        "averageBlockSeconds": average_block_seconds,
    }


def observed_dex_groups(seed_run, networks=None):
    groups = defaultdict(list)
    selected = set(networks or [])
    for pool in seed_run.get("pools") or []:
        if selected and pool.get("networkId") not in selected:
            continue
        key = (pool.get("networkId"), pool.get("dexId"))
        if not all(key) or not pool.get("poolAddress") or not pool.get("poolCreatedAt"):
            continue
        groups[key].append(pool)
    result = [
        {"networkId": key[0], "dexId": key[1], "seeds": rows}
        for key, rows in sorted(groups.items())
    ]
    return sorted(result, key=lambda row: (-len(row["seeds"]), row["networkId"], row["dexId"]))


def choose_creation_log(logs, seed):
    matches = []
    for log in logs:
        topics = log.get("topics") or []
        if not topics or str(topics[0]).lower() == TRANSFER_TOPIC:
            continue
        pool_location = find_location(log, seed["poolAddress"])
        if not pool_location:
            continue
        token_locations = []
        for side in ("baseToken", "quoteToken"):
            address = (seed.get(side) or {}).get("address")
            if str(address or "").lower() == ZERO_ADDRESS:
                continue
            location = find_location(log, address)
            if location and location not in token_locations:
                token_locations.append(location)
        if token_locations:
            matches.append((len(token_locations), log, pool_location, token_locations))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    _, log, pool_location, token_locations = matches[0]
    return {
        "emitter": str(log["address"]).lower(),
        "eventTopic": str(log["topics"][0]).lower(),
        "poolLocation": pool_location,
        "poolTemplate": seed["poolAddress"],
        "tokenLocations": token_locations,
        "seedTransactionHash": log.get("transactionHash"),
        "seedBlockNumber": hex_number(log["blockNumber"]),
    }


def creation_log_filters(seed, start, end):
    base = {"fromBlock": hex_block(start), "toBlock": hex_block(end)}
    filters = [
        {**base, "topics": [PAIR_CREATED_TOPIC]},
        {**base, "topics": [POOL_CREATED_TOPIC]},
    ]
    pool_word = word_for(seed["poolAddress"])
    if pool_word:
        topic = "0x" + pool_word
        filters.extend(
            [
                {**base, "topics": [None, topic]},
                {**base, "topics": [None, None, topic]},
                {**base, "topics": [None, None, None, topic]},
            ]
        )
    return filters


def chunk_log_filter(log_filter, maximum_block_span):
    start = hex_number(log_filter["fromBlock"])
    end = hex_number(log_filter["toBlock"])
    if end < start:
        yield log_filter
        return
    span = max(1, int(maximum_block_span or (end - start + 1)))
    for chunk_start in range(start, end + 1, span):
        yield {
            **log_filter,
            "fromBlock": hex_block(chunk_start),
            "toBlock": hex_block(min(end, chunk_start + span - 1)),
        }


def infer_group_schema(
    rpc,
    group,
    latest_block,
    window_start_block,
    latest_timestamp,
    average_block_seconds,
    tolerance_seconds,
    minimum_block_radius,
    maximum_log_block_span=None,
    maximum_requests=None,
):
    errors = []
    requests = 0
    seed = max(group["seeds"], key=lambda row: row["poolCreatedAt"])
    created = int(parse_utc(seed["poolCreatedAt"]).timestamp())
    if HEX_ADDRESS.fullmatch(seed["poolAddress"]):
        try:
            if maximum_requests is not None and requests + 2 > maximum_requests:
                errors.append(
                    {
                        "poolAddress": seed["poolAddress"],
                        "errorType": "inference_request_budget_exhausted",
                        "error": f"maximum {maximum_requests} inference requests reached",
                    }
                )
                return None, errors
            requests += 1
            code_at_window_start = rpc.call(
                "eth_getCode", [seed["poolAddress"], hex_block(window_start_block)]
            )
            requests += 1
            code_at_latest = rpc.call(
                "eth_getCode", [seed["poolAddress"], hex_block(latest_block)]
            )
            deployed_at_start = str(code_at_window_start or "").lower() not in {
                "",
                "0x",
                "0x0",
                "0x00",
            }
            deployed_at_latest = str(code_at_latest or "").lower() not in {
                "",
                "0x",
                "0x0",
                "0x00",
            }
            if not deployed_at_start and deployed_at_latest:
                low = window_start_block
                high = latest_block
                while low + 1 < high:
                    if maximum_requests is not None and requests >= maximum_requests:
                        errors.append(
                            {
                                "poolAddress": seed["poolAddress"],
                                "errorType": "inference_request_budget_exhausted",
                                "error": f"maximum {maximum_requests} inference requests reached",
                            }
                        )
                        return None, errors
                    middle = (low + high) // 2
                    requests += 1
                    middle_code = rpc.call(
                        "eth_getCode", [seed["poolAddress"], hex_block(middle)]
                    )
                    if str(middle_code or "").lower() in {"", "0x", "0x0", "0x00"}:
                        low = middle
                    else:
                        high = middle
                if maximum_requests is not None and requests >= maximum_requests:
                    errors.append(
                        {
                            "poolAddress": seed["poolAddress"],
                            "errorType": "inference_request_budget_exhausted",
                            "error": f"maximum {maximum_requests} inference requests reached",
                        }
                    )
                    return None, errors
                requests += 1
                logs = rpc.call(
                    "eth_getLogs",
                    [{"fromBlock": hex_block(high), "toBlock": hex_block(high)}],
                ) or []
                schema = choose_creation_log(logs, seed)
                if schema:
                    schema.update(
                        {
                            "networkId": group["networkId"],
                            "dexIds": [group["dexId"]],
                            "seedsAvailable": len(group["seeds"]),
                            "seedPoolAddress": seed["poolAddress"],
                            "seedPoolCreatedAt": seed["poolCreatedAt"],
                            "inferenceMethod": "contract_creation_block",
                        }
                    )
                    return schema, errors
                errors.append(
                    {
                        "poolAddress": seed["poolAddress"],
                        "creationBlock": high,
                        "errorType": "event_not_found",
                        "error": "contract_creation_log_not_found",
                    }
                )
            elif deployed_at_start:
                errors.append(
                    {
                        "poolAddress": seed["poolAddress"],
                        "errorType": "contract_predates_window",
                        "error": "contract code already exists at the 90-day window start",
                    }
                )
            else:
                errors.append(
                    {
                        "poolAddress": seed["poolAddress"],
                        "errorType": "not_contract_address",
                        "error": "pool identifier has no deployed code at latest block",
                    }
                )
        except RpcError as error:
            errors.append(
                {
                    "poolAddress": seed["poolAddress"],
                    "errorType": "rpc_failure",
                    "error": str(error)[:300],
                }
            )
            return None, errors
    elif HEX_POOL_ID.fullmatch(seed["poolAddress"]):
        try:
            exact_block = first_block_at_or_after(
                rpc,
                created,
                latest_block,
                window_start_block,
            )
            if exact_block > latest_block:
                errors.append(
                    {
                        "poolAddress": seed["poolAddress"],
                        "errorType": "invalid_seed_time",
                        "error": "pool timestamp is after the latest available block",
                    }
                )
                return None, errors
            tolerance = min(tolerance_seconds or [0])
            radius = max(
                minimum_block_radius,
                math.ceil(tolerance / max(0.001, average_block_seconds)),
            )
            start = max(window_start_block, exact_block - 1)
            end = min(latest_block, exact_block + radius)
            schema = None
            anchored_filter = {"fromBlock": hex_block(start), "toBlock": hex_block(end)}
            for chunk in chunk_log_filter(anchored_filter, maximum_log_block_span):
                if maximum_requests is not None and requests >= maximum_requests:
                    errors.append(
                        {
                            "poolAddress": seed["poolAddress"],
                            "errorType": "inference_request_budget_exhausted",
                            "error": f"maximum {maximum_requests} inference requests reached",
                        }
                    )
                    return None, errors
                requests += 1
                logs = rpc.call("eth_getLogs", [chunk]) or []
                schema = choose_creation_log(logs, seed)
                if schema:
                    break
            if schema:
                schema.update(
                    {
                        "networkId": group["networkId"],
                        "dexIds": [group["dexId"]],
                        "seedsAvailable": len(group["seeds"]),
                        "seedPoolAddress": seed["poolAddress"],
                        "seedPoolCreatedAt": seed["poolCreatedAt"],
                        "inferenceMethod": "timestamp_anchored_bounded_creation_event",
                    }
                )
                return schema, errors
            errors.append(
                    {
                        "poolAddress": seed["poolAddress"],
                        "anchorBlock": exact_block,
                        "anchorEndBlock": end,
                        "toleranceSeconds": tolerance,
                        "errorType": "event_not_found",
                        "error": "timestamp_anchored_bounded_creation_log_not_found",
                    }
                )
        except (RpcError, ValueError) as error:
            errors.append(
                {
                    "poolAddress": seed["poolAddress"],
                    "errorType": "rpc_failure" if isinstance(error, RpcError) else "invalid_seed",
                    "error": str(error)[:300],
                }
            )
        return None, errors
    for tolerance in tolerance_seconds:
        try:
            estimated = latest_block - round((latest_timestamp - created) / average_block_seconds)
            radius = max(minimum_block_radius, math.ceil(tolerance / average_block_seconds))
            start = max(window_start_block, estimated - radius)
            end = min(latest_block, estimated + radius)
            schema = None
            for log_filter in creation_log_filters(seed, start, end):
                for chunk in chunk_log_filter(log_filter, maximum_log_block_span):
                    if maximum_requests is not None and requests >= maximum_requests:
                        errors.append(
                            {
                                "poolAddress": seed["poolAddress"],
                                "toleranceSeconds": tolerance,
                                "errorType": "inference_request_budget_exhausted",
                                "error": f"maximum {maximum_requests} inference requests reached",
                            }
                        )
                        return None, errors
                    requests += 1
                    logs = rpc.call("eth_getLogs", [chunk]) or []
                    schema = choose_creation_log(logs, seed)
                    if schema:
                        break
                if schema:
                    break
            if schema:
                schema.update(
                    {
                        "networkId": group["networkId"],
                        "dexIds": [group["dexId"]],
                        "seedsAvailable": len(group["seeds"]),
                        "seedPoolAddress": seed["poolAddress"],
                        "seedPoolCreatedAt": seed["poolCreatedAt"],
                    }
                )
                return schema, errors
            errors.append(
                {
                    "poolAddress": seed["poolAddress"],
                    "toleranceSeconds": tolerance,
                    "errorType": "event_not_found",
                    "error": "creation_log_not_found",
                }
            )
        except (RpcError, ValueError) as error:
            errors.append(
                {
                    "poolAddress": seed["poolAddress"],
                    "toleranceSeconds": tolerance,
                    "errorType": "rpc_failure" if isinstance(error, RpcError) else "invalid_seed",
                    "error": str(error)[:300],
                }
            )
            if isinstance(error, RpcError):
                return None, errors
    return None, errors


def schema_key(schema):
    locations = json.dumps(
        {
            "pool": schema["poolLocation"],
            "tokens": sorted(
                schema["tokenLocations"],
                key=lambda location: (location["source"], location["index"]),
            ),
            "template": "address" if HEX_ADDRESS.fullmatch(schema["poolTemplate"]) else "bytes32",
        },
        sort_keys=True,
    )
    return schema["networkId"], schema["emitter"], schema["eventTopic"], locations


def merge_schemas(schemas):
    merged = {}
    for schema in schemas:
        key = schema_key(schema)
        if key not in merged:
            merged[key] = dict(schema)
            continue
        merged[key]["dexIds"] = sorted(set(merged[key]["dexIds"] + schema["dexIds"]))
        merged[key]["seedsAvailable"] += schema["seedsAvailable"]
    return list(merged.values())


def load_schema_registry(config):
    path = PROJECT_ROOT / config["schemaRegistry"]
    if not path.exists():
        return {"schemaVersion": "convexity-gate0-dex-schema-registry-v0.1", "schemas": []}
    return json.loads(path.read_text(encoding="utf-8"))


def registry_by_group(registry):
    result = {}
    for schema in registry.get("schemas") or []:
        for dex_id in schema.get("dexIds") or []:
            result[(schema["networkId"], dex_id)] = schema
    return result


def update_schema_registry(config, schemas):
    registry = load_schema_registry(config)
    indexed = registry_by_group(registry)
    for schema in schemas:
        for dex_id in schema["dexIds"]:
            row = dict(schema)
            row["dexIds"] = [dex_id]
            row["verifiedAt"] = utc_now()
            row["verification"] = "chain_event_matches_seed_pool_and_token_fields"
            indexed[(schema["networkId"], dex_id)] = row
    registry = {
        "schemaVersion": "convexity-gate0-dex-schema-registry-v0.1",
        "updatedAt": utc_now(),
        "schemas": sorted(indexed.values(), key=lambda row: (row["networkId"], row["dexIds"][0])),
    }
    atomic_write_json(PROJECT_ROOT / config["schemaRegistry"], registry)
    return registry


def build_coverage_rollup(config):
    root = PROJECT_ROOT / config["outputRoot"]
    latest_activity_by_network = {}
    latest_scan_by_network = {}
    for path in (root / "runs").glob("*/summary.json"):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for network_id in run.get("execution", {}).get("selectedNetworks") or []:
            existing = latest_activity_by_network.get(network_id)
            if not existing or run.get("finishedAt", "") > existing.get("finishedAt", ""):
                latest_activity_by_network[network_id] = run
            network_scans = [
                row
                for row in (run.get("evmScanResults") or [])
                + (run.get("solanaScanResults") or [])
                if row.get("networkId") == network_id
            ]
            if network_scans:
                existing_scan = latest_scan_by_network.get(network_id)
                if not existing_scan or run.get("finishedAt", "") > existing_scan.get(
                    "finishedAt", ""
                ):
                    latest_scan_by_network[network_id] = run

    registry = load_schema_registry(config)
    registry_counts = Counter(row["networkId"] for row in registry.get("schemas") or [])
    registry_dex_ids = defaultdict(set)
    for schema in registry.get("schemas") or []:
        registry_dex_ids[schema["networkId"]].update(schema.get("dexIds") or [])
    network_results = []
    for network_id, activity_run in sorted(latest_activity_by_network.items()):
        scan_run = latest_scan_by_network.get(network_id)
        activity_groups = [
            row
            for row in activity_run.get("evmGroupResults") or []
            if row.get("networkId") == network_id
        ]
        evm_scan_rows = [
            row
            for row in (scan_run or {}).get("evmScanResults") or []
            if row.get("networkId") == network_id
        ]
        solana_scan_rows = [
            row
            for row in (scan_run or {}).get("solanaScanResults") or []
            if row.get("networkId") == network_id
        ]
        complete_scan_dex_ids = {
            dex_id
            for row in evm_scan_rows
            if row.get("complete")
            for dex_id in row.get("dexIds") or []
        }
        evm_registered_scans_complete = bool(registry_dex_ids[network_id]) and registry_dex_ids[
            network_id
        ].issubset(complete_scan_dex_ids)
        evm_observed_coverage_complete = bool(activity_groups) and all(
            row.get("state") == "success" for row in activity_groups
        )
        solana_program_rows = [
            row
            for row in activity_run.get("solanaProgramResults") or []
            if row.get("networkId") == network_id
        ]
        solana_success = sum(
            row.get("state") == "success" and row.get("decoderAvailable")
            for row in solana_program_rows
        )
        solana_observed_coverage_complete = bool(solana_program_rows) and all(
            row.get("state") == "success" and row.get("decoderAvailable")
            for row in solana_program_rows
        )
        solana_source_range_complete = bool(solana_scan_rows) and all(
            row.get("sourceRangeComplete") for row in solana_scan_rows
        )
        solana_requested_window_complete = bool(solana_scan_rows) and all(
            row.get("requestedWindowComplete") for row in solana_scan_rows
        )
        registered_scans_complete = (
            solana_source_range_complete
            if solana_program_rows
            else evm_registered_scans_complete
        )
        observed_dex_coverage_complete = (
            solana_observed_coverage_complete
            if solana_program_rows
            else evm_observed_coverage_complete
        )
        scan_coverage = (scan_run or {}).get("coverage") or {}
        single_network_scan = len(
            (scan_run or {}).get("execution", {}).get("selectedNetworks") or []
        ) == 1
        network_results.append(
            {
                "networkId": network_id,
                "latestRunId": (scan_run or activity_run)["runId"],
                "latestActivityRunId": activity_run["runId"],
                "latestScanRunId": scan_run["runId"] if scan_run else "",
                "finishedAt": (scan_run or activity_run)["finishedAt"],
                "inferenceOnly": activity_run["execution"]["inferenceOnly"],
                "observedDexGroups": len(activity_groups)
                + sum(
                    row.get("networkId") == network_id
                    for row in activity_run.get("solanaProgramResults") or []
                ),
                "evmDexGroups": len(activity_groups),
                "verifiedEvmSchemas": registry_counts[network_id],
                "evmScanUnits": len(evm_scan_rows),
                "evmScansComplete": sum(row.get("complete") for row in evm_scan_rows),
                "solanaDexGroups": sum(
                    row.get("networkId") == network_id
                    for row in activity_run.get("solanaProgramResults") or []
                ),
                "solanaDexGroupsProgramIdentified": solana_success,
                "solanaScanUnits": len(solana_scan_rows),
                "solanaSourceRangeComplete": solana_source_range_complete,
                "solanaRequestedWindowComplete": solana_requested_window_complete,
                "candidateTokens": scan_coverage.get("candidateTokens", 0)
                if single_network_scan
                else 0,
                "eventRows": sum(row.get("events", 0) for row in evm_scan_rows)
                + sum(row.get("events", 0) for row in solana_scan_rows),
                "networkRange": ((scan_run or activity_run).get("networkRanges") or {}).get(
                    network_id
                ),
                "requestSummary": (scan_run or activity_run)["requestSummary"],
                "registeredScansComplete": registered_scans_complete,
                "observedDexCoverageComplete": observed_dex_coverage_complete,
                "historicalBackfillComplete": (
                    observed_dex_coverage_complete and solana_requested_window_complete
                    if solana_program_rows
                    else registered_scans_complete and observed_dex_coverage_complete
                ),
            }
        )
    rollup = {
        "schemaVersion": "convexity-gate0-dex-backfill-rollup-v0.1",
        "generatedAt": utc_now(),
        "boundary": {
            "projectMinimumWaitDays": 0,
            "allProjectAgesUseSourceHistory": True,
            "shortHistorySyntheticDaysAllowed": False,
            "marketWideCoverageGuaranteed": False,
            "usableAsGlobalT0": False,
        },
        "coverage": {
            "networksObserved": len(network_results),
            "observedDexGroups": sum(row["observedDexGroups"] for row in network_results),
            "verifiedEvmSchemas": len(registry.get("schemas") or []),
            "evmScanUnits": sum(row["evmScanUnits"] for row in network_results),
            "evmScansComplete": sum(row["evmScansComplete"] for row in network_results),
            "solanaDexGroups": sum(row["solanaDexGroups"] for row in network_results),
            "solanaDexGroupsProgramIdentified": sum(
                row["solanaDexGroupsProgramIdentified"] for row in network_results
            ),
            "solanaScanUnits": sum(row["solanaScanUnits"] for row in network_results),
            "solanaSourceRangesComplete": sum(
                row["solanaSourceRangeComplete"] for row in network_results
            ),
            "solanaRequestedWindowsComplete": sum(
                row["solanaRequestedWindowComplete"] for row in network_results
            ),
            "candidateTokens": sum(row["candidateTokens"] for row in network_results),
            "eventRows": sum(row["eventRows"] for row in network_results),
            "historicalBackfillComplete": bool(network_results)
            and all(row["historicalBackfillComplete"] for row in network_results),
            "marketWideComplete": False,
            "usableAsGlobalT0": False,
        },
        "networkResults": network_results,
        "schemaRegistry": config["schemaRegistry"],
    }
    atomic_write_json(PROJECT_ROOT / config["coverageRollup"], rollup)
    return rollup


def decode_log(log, schema):
    pool_id = as_pool_id(value_at(log, schema["poolLocation"]), schema["poolTemplate"])
    tokens = []
    for location in schema["tokenLocations"]:
        token = as_address(value_at(log, location))
        if token and token not in tokens:
            tokens.append(token)
    return {
        "networkId": schema["networkId"],
        "dexIds": schema["dexIds"],
        "emitter": schema["emitter"],
        "eventTopic": schema["eventTopic"],
        "blockNumber": hex_number(log["blockNumber"]),
        "blockTimestamp": hex_number(log["blockTimestamp"])
        if log.get("blockTimestamp")
        else None,
        "transactionHash": log.get("transactionHash"),
        "logIndex": hex_number(log.get("logIndex") or "0x0"),
        "poolId": pool_id,
        "tokenAddresses": tokens,
    }


def scan_schema(rpc, schema, start_block, end_block, settings, output_path):
    span = settings["initialBlockSpan"]
    minimum_span = settings["minimumBlockSpan"]
    maximum_span = settings["maximumBlockSpan"]
    suspicious_cap = settings["suspiciousLogResultCap"]
    current = start_block
    events = 0
    decode_failures = 0
    ranges = 0
    earliest_tokens = {}
    observed_pool_ids = set()
    failed_range = None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        while current <= end_block:
            selected_span = min(span, end_block - current + 1)
            selected_end = current + selected_span - 1
            params = {
                "address": schema["emitter"],
                "topics": [schema["eventTopic"]],
                "fromBlock": hex_block(current),
                "toBlock": hex_block(selected_end),
            }
            try:
                logs = rpc.call("eth_getLogs", [params], attempts=1) or []
                if len(logs) >= suspicious_cap and selected_span > minimum_span:
                    raise RpcError(f"suspicious result cap reached: {len(logs)}")
            except RpcError as error:
                if error.kind == "transport_failure":
                    try:
                        rpc.call("eth_chainId", [])
                        logs = rpc.call("eth_getLogs", [params], attempts=1) or []
                    except RpcError as retry_error:
                        error = retry_error
                    else:
                        error = None
                if error is None:
                    pass
                elif error.kind in {"quota_limited", "configuration_missing"}:
                    failed_range = {
                        "fromBlock": current,
                        "toBlock": selected_end,
                        "error": str(error)[:500],
                        "errorKind": error.kind,
                    }
                    break
                elif error.kind in {"transport_failure", "response_incomplete"} and selected_span > settings.get(
                    "minimumTransportRetryBlockSpan", minimum_span
                ):
                    span = max(
                        settings.get("minimumTransportRetryBlockSpan", minimum_span),
                        selected_span // 2,
                    )
                    continue
                elif error.kind in {"transport_failure", "response_incomplete"}:
                    failed_range = {
                        "fromBlock": current,
                        "toBlock": selected_end,
                        "error": str(error)[:500],
                        "errorKind": error.kind,
                    }
                    break
                elif selected_span > minimum_span:
                    span = max(minimum_span, selected_span // 2)
                    continue
                elif error is not None:
                    failed_range = {
                        "fromBlock": current,
                        "toBlock": selected_end,
                        "error": str(error)[:500],
                        "errorKind": error.kind,
                    }
                    break
            for log in logs:
                event = decode_log(log, schema)
                if not event["poolId"]:
                    decode_failures += 1
                else:
                    observed_pool_ids.add(event["poolId"])
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                events += 1
                for token in event["tokenAddresses"]:
                    existing = earliest_tokens.get(token)
                    if not existing or event["blockNumber"] < existing["blockNumber"]:
                        earliest_tokens[token] = event
            ranges += 1
            current = selected_end + 1
            semantic_minimum = settings.get("semanticCheckMinimumEvents", 100)
            repeated_pool_ratio = (
                1 - (len(observed_pool_ids) / events) if events else 0
            )
            if (
                events >= semantic_minimum
                and repeated_pool_ratio > settings.get("maximumRepeatedPoolRatio", 0.05)
            ):
                failed_range = {
                    "fromBlock": start_block,
                    "toBlock": selected_end,
                    "error": (
                        "creation-event semantic check failed: repeated pool ratio "
                        f"{repeated_pool_ratio:.6f}"
                    ),
                    "errorKind": "semantic_mismatch",
                    "eventsChecked": events,
                    "uniquePools": len(observed_pool_ids),
                    "repeatedPoolRatio": repeated_pool_ratio,
                }
                earliest_tokens = {}
                break
            handle.flush()
            if ranges % 25 == 0 or current > end_block:
                print(
                    json.dumps(
                        {
                            "networkId": schema["networkId"],
                            "dexIds": schema["dexIds"],
                            "rangesRead": ranges,
                            "nextBlock": current,
                            "endBlock": end_block,
                            "events": events,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
            if len(logs) < settings.get("spanGrowthLogResultCap", suspicious_cap // 4):
                span = min(maximum_span, max(span, selected_span) * 2)
        complete = current > end_block and failed_range is None
    final_path = output_path if complete else output_path.with_suffix(".partial.jsonl")
    temporary.replace(final_path)
    return {
        "complete": complete,
        "path": str(final_path.resolve()),
        "rangesRead": ranges,
        "events": events,
        "decodeFailures": decode_failures,
        "failedRange": failed_range,
        "earliestTokens": earliest_tokens,
    }


def alchemy_client(network, shadow_config, settings, ledger):
    key = user_environment(settings["credentialEnv"])
    if not key:
        raise RpcError(f"missing environment credential: {settings['credentialEnv']}")
    url = f"https://{network['alchemyHost']}/v2/{key}"
    safe_url = f"https://{network['alchemyHost']}/v2/[REDACTED]"
    return JsonRpcClient(
        f"alchemy_backfill_{network['id']}",
        url,
        safe_url,
        ledger,
        settings["minimumRequestIntervalSeconds"],
    )


def historical_log_client(network, config, ledger):
    settings = (config.get("historicalLogSources") or {}).get(network["id"])
    if not settings:
        return None, None
    if settings["type"] == "json_rpc_public":
        client = JsonRpcClient(
            f"public_rpc_backfill_{network['id']}",
            settings["baseUrl"],
            settings["baseUrl"],
            ledger,
            settings["minimumRequestIntervalSeconds"],
            use_environment_proxy=settings.get("useEnvironmentProxy", True),
        )
    elif settings["type"] == "json_rpc_credential_path":
        key = user_environment(settings["credentialEnv"])
        if not key:
            raise RpcError(
                f"missing environment credential: {settings['credentialEnv']}",
                kind="configuration_missing",
            )
        base_url = settings["baseUrl"].rstrip("/")
        client = JsonRpcClient(
            f"authenticated_rpc_backfill_{network['id']}",
            f"{base_url}/{key}",
            f"{base_url}/[REDACTED]",
            ledger,
            settings["minimumRequestIntervalSeconds"],
            use_environment_proxy=settings.get("useEnvironmentProxy", False),
        )
    elif settings["type"] in {"blockscout_legacy_logs", "blockscout_pro_logs"}:
        query_parameters = {}
        if settings["type"] == "blockscout_pro_logs":
            key = user_environment(settings["credentialEnv"])
            if not key:
                raise RpcError(
                    f"missing environment credential: {settings['credentialEnv']}",
                    kind="configuration_missing",
                )
            query_parameters = {"chain_id": str(network["chainId"]), "apikey": key}
        client = BlockscoutLogClient(
            f"blockscout_backfill_{network['id']}",
            settings["baseUrl"],
            network["chainId"],
            ledger,
            settings["minimumRequestIntervalSeconds"],
            query_parameters=query_parameters,
        )
    else:
        raise RpcError(
            f"unsupported historical log source: {settings['type']}",
            kind="configuration_missing",
        )
    scan_settings = dict(config["evm"])
    scan_settings["initialBlockSpan"] = settings["initialBlockSpan"]
    scan_settings["maximumBlockSpan"] = settings["maximumBlockSpan"]
    scan_settings["suspiciousLogResultCap"] = settings["resultCap"]
    if settings.get("minimumTransportRetryBlockSpan") is not None:
        scan_settings["minimumTransportRetryBlockSpan"] = settings[
            "minimumTransportRetryBlockSpan"
        ]
    if settings.get("spanGrowthLogResultCap") is not None:
        scan_settings["spanGrowthLogResultCap"] = settings["spanGrowthLogResultCap"]
    if settings.get("rangeSafetyMultiplier") is not None:
        scan_settings["windowSafetyMultiplier"] = settings["rangeSafetyMultiplier"]
    if settings.get("blockTimestampBatchSize") is not None:
        scan_settings["blockTimestampBatchSize"] = settings["blockTimestampBatchSize"]
    return client, scan_settings


def helius_client(settings, ledger):
    key = user_environment(settings["credentialEnv"])
    if not key:
        raise RpcError(f"missing environment credential: {settings['credentialEnv']}")
    url = f"https://{settings['rpcHost']}/?api-key={key}"
    safe_url = f"https://{settings['rpcHost']}/?api-key=[REDACTED]"
    return JsonRpcClient(
        "helius_backfill_solana",
        url,
        safe_url,
        ledger,
        settings["minimumRequestIntervalSeconds"],
    )


class SqdSolanaArchiveClient:
    _throttle_lock = threading.Lock()

    def __init__(self, settings, ledger):
        self.settings = settings
        self.ledger = ledger
        self.portal_base = settings["portalBaseUrl"].rstrip("/")
        self.archive_base = settings["legacyArchiveBaseUrl"].rstrip("/")
        self.api_key = user_environment(settings["legacyArchiveCredentialEnv"])
        if not self.api_key:
            raise RpcError(
                f"missing environment credential: {settings['legacyArchiveCredentialEnv']}",
                kind="configuration_missing",
            )

    @staticmethod
    def _safe_url(url):
        marker = "/worker/query/"
        return url.split(marker, 1)[0] + marker + "[REDACTED]" if marker in url else url

    def _request(self, source, url, payload=None, ndjson=False):
        minimum_interval = (
            self.settings["portalMinimumRequestIntervalSeconds"]
            if source == "sqd_portal_solana"
            else self.settings["legacyArchiveMinimumRequestIntervalSeconds"]
        )
        with self._throttle_lock:
            previous = self.ledger.last_request_at.get("sqd_solana_all")
            if previous is not None:
                wait_seconds = minimum_interval - (time.monotonic() - previous)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
            self.ledger.last_request_at["sqd_solana_all"] = time.monotonic()
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "User-Agent": "PenguinResearchGate0/1.0",
            "Accept": "application/x-ndjson" if ndjson else "application/json",
            "Accept-Encoding": "gzip",
            "Authorization": f"Bearer {self.api_key}",
            "X-SQD-API-Key": self.api_key,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        retry_seconds = self.settings.get("requestRetrySeconds") or [5, 15, 30]
        last_error = ""
        last_kind = "transport_failure"
        for attempt in range(len(retry_seconds) + 1):
            started = time.monotonic()
            status = None
            raw = b""
            response_headers = {}
            state = "source_failure"
            error_text = ""
            try:
                if self.settings.get("useIsolatedHttpWorker", True):
                    response_result = isolated_http_request(
                        url,
                        body,
                        headers,
                        self.ledger.timeout,
                        self.settings.get("requestTotalTimeoutSeconds", self.ledger.timeout),
                    )
                else:
                    response_result = in_process_http_request(
                        url,
                        body,
                        headers,
                        self.ledger.timeout,
                        self.settings.get("useEnvironmentProxy", False),
                    )
                status = response_result["status"]
                response_headers = response_result["headers"]
                raw = response_result["raw"]
                if status >= 400:
                    error_text = f"HTTP {status}: {response_error_detail(raw)}"
                    state = "quota_limited" if status in {429, 529} else "source_failure"
                    last_kind = "quota_limited" if state == "quota_limited" else "http_error"
                    raise RpcError(error_text, kind=last_kind)
                if response_headers.get("content-encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                if status == 204:
                    decoded = []
                elif ndjson:
                    decoded = [
                        json.loads(line)
                        for line in raw.decode("utf-8").splitlines()
                        if line.strip()
                    ]
                else:
                    text = raw.decode("utf-8")
                    try:
                        decoded = json.loads(text)
                    except json.JSONDecodeError:
                        decoded = text
                state = "success"
                error_text = ""
            except (
                OSError,
                TimeoutError,
                ConnectionError,
                urllib.error.URLError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                RpcError,
            ) as error:
                if isinstance(error, RpcError):
                    error_text = str(error)
                    last_kind = error.kind
                else:
                    error_text = f"{type(error).__name__}: {error}"
                    last_kind = "transport_failure"
                decoded = None
            self.ledger.last_request_at[source] = time.monotonic()
            self.ledger.requests.append(
                {
                    "source": source,
                    "url": self._safe_url(url),
                    "observedAt": utc_now(),
                    "httpStatus": status,
                    "state": state,
                    "latencyMs": round((time.monotonic() - started) * 1000),
                    "responseBytes": len(raw),
                    "rateLimit": {
                        "limit": response_headers.get("x-ratelimit-limit"),
                        "remaining": response_headers.get("x-ratelimit-remaining"),
                        "reset": response_headers.get("x-ratelimit-reset"),
                        "retryAfter": response_headers.get("retry-after"),
                    },
                    "error": error_text[:500],
                }
            )
            if state == "success":
                return decoded
            last_error = error_text
            if attempt < len(retry_seconds) and sqd_request_retryable(
                state, status, last_kind
            ):
                time.sleep(retry_seconds[attempt])
                continue
            break
        raise RpcError(last_error or "SQD request failed", kind=last_kind)

    def finalized_height(self):
        return int(self._request("sqd_legacy_solana_router", self.archive_base + "/height"))

    def worker_url(self, height):
        return str(
            self._request(
                "sqd_legacy_solana_router", self.archive_base + f"/{int(height)}/worker"
            )
        )

    def archive_height_at_timestamp(self, timestamp):
        slot = self.portal_slot_at_timestamp(timestamp)
        rows = self._request(
            "sqd_portal_solana",
            self.portal_base + "/finalized-stream",
            {
                "type": "solana",
                "fromBlock": slot,
                "toBlock": slot,
                "fields": {
                    "block": {
                        "number": True,
                        "height": True,
                        "timestamp": True,
                    }
                },
            },
            ndjson=True,
        )
        if not rows or rows[0].get("header", {}).get("height") is None:
            raise RpcError("SQD Portal did not map timestamp slot to archive height")
        return {
            "height": int(rows[0]["header"]["height"]),
            "slot": int(rows[0]["header"]["number"]),
            "timestamp": int(rows[0]["header"]["timestamp"]),
        }

    def portal_slot_at_timestamp(self, timestamp):
        slot_row = self._request(
            "sqd_portal_solana",
            self.portal_base + f"/timestamps/{int(timestamp)}/block",
        )
        return int(slot_row["block_number"])

    def portal_query(self, payload):
        return self._request(
            "sqd_portal_solana",
            self.portal_base + "/finalized-stream",
            payload,
            ndjson=True,
        )

    def latest_archive_height_at_or_before(self, timestamp):
        last_error = None
        for lag_seconds in (60, 120, 300, 600, 1800):
            try:
                return self.archive_height_at_timestamp(int(timestamp) - lag_seconds)
            except RpcError as error:
                last_error = error
        raise last_error or RpcError("SQD Portal finalized head is unavailable")

    def query(self, height, payload):
        return self._request("sqd_legacy_solana_worker", self.worker_url(height), payload)


def scan_solana_archive(settings, schemas, cutoff, output_path, ledger):
    now = datetime.now(timezone.utc)
    use_portal = bool(settings.get("portalFullHistoryEnabled"))
    source_label = "sqd_portal_finalized_stream" if use_portal else "sqd_legacy_v2_archive"
    source_cutoff = (
        cutoff - timedelta(seconds=int(settings.get("legacyArchiveStartSafetySeconds", 0)))
        if settings.get("legacyArchiveUseRequestedCutoff")
        else now - timedelta(days=settings["legacyArchiveWindowDays"])
    )
    requested_timestamp = int(cutoff.timestamp())
    source_timestamp = int(source_cutoff.timestamp())
    temporary = output_path.with_suffix(".jsonl.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    events = 0
    decode_failures = 0
    requests = 0
    earliest_tokens = {}
    schema_counts = Counter()
    dex_counts = Counter()
    failed_range = None
    current = None
    end_height = None
    end_mapping = None
    source_mapping = None
    seen = set()
    known_quotes = set(settings.get("knownQuoteTokens") or [])
    write_lock = threading.Lock()
    pages_complete = 0

    def record_page(page_events):
        nonlocal events, decode_failures, pages_complete
        with write_lock:
            for event in page_events:
                identity = (
                    event.get("slot") or event["blockNumber"],
                    event["transactionIndex"],
                    tuple(event["instructionAddress"]),
                    event["schemaId"],
                )
                if identity in seen:
                    continue
                seen.add(identity)
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                events += 1
                schema_counts[event["schemaId"]] += 1
                dex_counts.update(event["dexIds"])
                if not event["decodeComplete"]:
                    decode_failures += 1
                    continue
                for token in event["tokenAddresses"]:
                    if token in known_quotes:
                        continue
                    existing = earliest_tokens.get(token)
                    if not existing or event["blockNumber"] < existing["blockNumber"]:
                        earliest_tokens[token] = event
            handle.flush()
            pages_complete += 1
            if pages_complete % int(settings.get("progressPrintEveryPages", 20)) == 0:
                print(
                    json.dumps(
                        {
                            "networkId": "solana-mainnet",
                            "source": source_label,
                            "pagesComplete": pages_complete,
                            "events": events,
                            "decodeFailures": decode_failures,
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )

    def scan_height_range(bounds):
        range_start, range_end = bounds
        local_current = range_start
        local_requests = 0
        local_events = 0
        local_client = SqdSolanaArchiveClient(settings, ledger)
        try:
            while local_current <= range_end:
                if use_portal:
                    query = solana_portal_query(schemas, local_current, range_end)
                    blocks = local_client.portal_query(query)
                else:
                    query = solana_archive_query(schemas, local_current, range_end)
                    blocks = local_client.query(local_current, query)
                local_requests += 1
                if not blocks:
                    raise RpcError(f"{source_label} returned no continuation block")
                last_height = int(blocks[-1]["header"]["number"])
                if last_height < local_current or last_height > range_end:
                    raise RpcError(f"{source_label} continuation did not advance")
                page_events = []
                for block in blocks:
                    normalized = normalize_portal_solana_block(block) if use_portal else block
                    page_events.extend(decode_solana_creation_block(normalized, schemas))
                record_page(page_events)
                local_events += len(page_events)
                local_current = last_height + 1
            return {
                "fromBlockHeight": range_start,
                "toBlockHeight": range_end,
                "requests": local_requests,
                "events": local_events,
                "error": None,
            }
        except RpcError as error:
            return {
                "fromBlockHeight": local_current,
                "toBlockHeight": range_end,
                "requests": local_requests,
                "events": local_events,
                "error": error,
            }

    try:
        client = SqdSolanaArchiveClient(settings, ledger)
        if use_portal:
            end_mapping = client.latest_archive_height_at_or_before(int(now.timestamp()))
            target_window_seconds = int((now - cutoff).total_seconds())
            requested_timestamp = end_mapping["timestamp"] - target_window_seconds
            source_mapping = client.archive_height_at_timestamp(
                requested_timestamp - int(settings.get("portalStartSafetySeconds", 60))
            )
            current = source_mapping["slot"]
            end_height = end_mapping["slot"]
            span = settings["portalBlockSpan"]
        else:
            source_mapping = client.archive_height_at_timestamp(source_timestamp)
            current = source_mapping["height"]
            end_height = client.finalized_height()
            span = settings["legacyArchiveBlockSpan"]
        ranges = [
            (start, min(end_height, start + span - 1))
            for start in range(current, end_height + 1, span)
        ]
        workers = settings["portalWorkers"] if use_portal else settings["legacyArchiveWorkers"]
        with temporary.open("w", encoding="utf-8") as handle:
            failed_result = None
            completed_ranges = 0

            def accept_result(result):
                nonlocal requests, completed_ranges, failed_result
                requests += result["requests"]
                completed_ranges += 1
                if (
                    completed_ranges % int(settings.get("progressPrintEveryRanges", 20)) == 0
                    or completed_ranges == len(ranges)
                    or result["error"]
                ):
                    print(
                        json.dumps(
                            {
                                "networkId": "solana-mainnet",
                                "source": source_label,
                                "rangesComplete": completed_ranges,
                                "rangesTotal": len(ranges),
                                "requests": requests,
                                "events": events,
                                "decodeFailures": decode_failures,
                            },
                            separators=(",", ":"),
                        ),
                        flush=True,
                    )
                if result["error"]:
                    failed_result = result
                return not result["error"]

            if workers == 1:
                for bounds in ranges:
                    if not accept_result(scan_height_range(bounds)):
                        break
            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(scan_height_range, bounds): bounds for bounds in ranges
                    }
                    for future in as_completed(futures):
                        result = future.result()
                        if accept_result(result):
                            continue
                        for pending in futures:
                            pending.cancel()
                        break
            if failed_result:
                current = failed_result["fromBlockHeight"]
                raise failed_result["error"]
            current = end_height + 1
    except RpcError as error:
        failed_range = {
            "fromBlockHeight": current,
            "toBlockHeight": end_height,
            "errorKind": error.kind,
            "error": str(error)[:500],
        }
        if not temporary.exists():
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text("", encoding="utf-8")
    source_range_complete = end_height is not None and current is not None and current > end_height
    complete = source_range_complete and decode_failures == 0
    requested_window_complete = (
        bool(source_mapping)
        and source_mapping["timestamp"] <= requested_timestamp
        and complete
    )
    final_path = output_path if complete else output_path.with_suffix(".partial.jsonl")
    temporary.replace(final_path)
    coverage_end_timestamp = (
        end_mapping["timestamp"] if use_portal and end_mapping else int(now.timestamp())
    )
    covered_days = (
        max(0, coverage_end_timestamp - source_mapping["timestamp"]) / 86400
        if source_mapping
        else 0
    )
    return {
        "networkId": "solana-mainnet",
        "dexIds": sorted(solana_schema_groups({"creationSchemas": schemas})),
        "historicalSource": source_label,
        "complete": complete,
        "sourceRangeComplete": source_range_complete,
        "requestedWindowComplete": requested_window_complete,
        "requestedWindowDays": int((now - cutoff).total_seconds() // 86400),
        "coveredWindowDays": round(covered_days, 3),
        "coverageStartsAt": datetime.fromtimestamp(
            source_mapping["timestamp"], timezone.utc
        ).isoformat().replace("+00:00", "Z")
        if source_mapping
        else "",
        "coverageStartsAtSlot": source_mapping["slot"] if source_mapping else None,
        "coverageStartsAtBlockHeight": source_mapping["height"] if source_mapping else None,
        "coverageEndsAtBlockHeight": (
            end_mapping["height"] if use_portal and end_mapping else end_height
        ),
        "coverageEndsAtSlot": end_mapping["slot"] if use_portal and end_mapping else None,
        "coverageEndsAt": datetime.fromtimestamp(
            coverage_end_timestamp, timezone.utc
        ).isoformat().replace("+00:00", "Z"),
        "path": str(final_path.resolve()),
        "requests": requests,
        "events": events,
        "decodeFailures": decode_failures,
        "schemaEventCounts": dict(sorted(schema_counts.items())),
        "dexEventCounts": dict(sorted(dex_counts.items())),
        "schemasCovered": sum(bool(schema_counts[schema["id"]]) for schema in schemas),
        "schemasRegistered": len(schemas),
        "failedRange": failed_range,
        "uncoveredRange": None
        if requested_window_complete
        else {
            "fromAt": cutoff.isoformat().replace("+00:00", "Z"),
            "toAt": source_cutoff.isoformat().replace("+00:00", "Z"),
            "reason": "legacy_archive_retains_about_30_days_public_portal_resume_required",
        },
        "earliestTokens": earliest_tokens,
    }


def probe_solana_programs(groups, settings, ledger):
    results = []
    try:
        rpc = helius_client(settings, ledger)
    except RpcError as error:
        return [
            {
                "networkId": group["networkId"],
                "dexId": group["dexId"],
                "state": "configuration_missing",
                "historicalBackfill": "not_started",
                "error": str(error),
            }
            for group in groups
        ]
    decoder_groups = solana_schema_groups(settings)
    for group in groups:
        owners = set()
        errors = []
        checked = 0
        for seed in group["seeds"]:
            checked += 1
            try:
                row = rpc.call(
                    "getAccountInfo",
                    [seed["poolAddress"], {"encoding": "base64", "commitment": "finalized"}],
                )
                owner = ((row or {}).get("value") or {}).get("owner")
                if owner:
                    owners.add(owner)
                    break
            except RpcError as error:
                errors.append(str(error)[:300])
        results.append(
            {
                "networkId": group["networkId"],
                "dexId": group["dexId"],
                "state": "success" if owners else ("source_failure" if errors else "no_data"),
                "seedPoolsAvailable": len(group["seeds"]),
                "seedPoolsChecked": checked,
                "programOwners": sorted(owners),
                "decoderAvailable": group["dexId"] in decoder_groups,
                "decoderSchemaIds": [
                    row["id"] for row in decoder_groups.get(group["dexId"], [])
                ],
                "historicalBackfill": settings["historicalPoolBackfillStatus"],
                "boundary": settings["boundary"],
                "errors": errors[:3],
            }
        )
    return results


def run_backfill(
    config, shadow_config, seed_run, networks=None, inference_only=False, registered_only=False
):
    selected = set(networks or [])
    network_map = {row["id"]: row for row in shadow_config["networks"]}
    groups = observed_dex_groups(seed_run, networks=networks)
    evm_groups = [row for row in groups if network_map[row["networkId"]]["chainType"] == "EVM"]
    solana_groups = [row for row in groups if network_map[row["networkId"]]["chainType"] == "SOLANA"]
    ledger = RequestLedger(
        timeout=max(config["evm"]["rpcTimeoutSeconds"], config["solana"]["rpcTimeoutSeconds"])
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=config["windowDays"])
    output_root = PROJECT_ROOT / config["outputRoot"]
    run_id = "dex-backfill-" + utc_now().replace("-", "").replace(":", "").replace(".", "")
    run_root = output_root / "runs" / run_id
    network_ranges = {}
    group_results = []
    inferred = []
    clients = {}
    historical_clients = {}
    historical_settings = {}
    historical_source_types = {}
    unavailable_networks = set()
    registered = registry_by_group(load_schema_registry(config))

    for network_id in sorted({row["networkId"] for row in evm_groups}):
        network = network_map[network_id]
        try:
            rpc = alchemy_client(network, shadow_config, config["evm"], ledger)
            clients[network_id] = rpc
        except RpcError as error:
            network_ranges[network_id] = {"state": "source_failure", "error": str(error)[:500]}
            continue
        try:
            source_client, source_settings = historical_log_client(network, config, ledger)
            historical_clients[network_id] = source_client or rpc
            historical_settings[network_id] = source_settings or config["evm"]
            historical_source_types[network_id] = (
                (config.get("historicalLogSources") or {}).get(network_id, {}).get("type")
                or "alchemy_json_rpc"
            )
            if source_client:
                if isinstance(source_client, BlockscoutLogClient):
                    estimated_range = source_client.range_for_window(int(cutoff.timestamp()))
                else:
                    estimated_range = source_client.range_for_window(
                        int(cutoff.timestamp()), source_settings
                    )
            else:
                latest = hex_number(rpc.call("eth_blockNumber", []))
                estimated_range = estimate_window_start(
                    rpc, latest, int(cutoff.timestamp()), config["evm"]
                )
            network_ranges[network_id] = {
                "state": "success",
                **estimated_range,
                "cutoffAt": cutoff.isoformat().replace("+00:00", "Z"),
                "coverageStartsAtOrBeforeCutoff": estimated_range["fromTimestamp"] <= int(cutoff.timestamp()),
            }
        except RpcError as error:
            network_ranges[network_id] = {"state": "source_failure", "error": str(error)[:500]}

    for group in evm_groups:
        network_id = group["networkId"]
        registered_schema = registered.get((network_id, group["dexId"]))
        if registered_schema:
            schema = dict(registered_schema)
            schema["seedsAvailable"] = len(group["seeds"])
            inferred.append(schema)
            group_results.append(
                {
                    "networkId": network_id,
                    "dexId": group["dexId"],
                    "state": "success"
                    if (network_ranges.get(network_id) or {}).get("state") == "success"
                    else "source_failure",
                    "schemaSource": "verified_registry",
                    "schemaAvailable": True,
                    "seedsAvailable": len(group["seeds"]),
                    "emitter": schema["emitter"],
                    "eventTopic": schema["eventTopic"],
                    "tokenFieldsDecoded": len(schema["tokenLocations"]),
                    "error": ""
                    if (network_ranges.get(network_id) or {}).get("state") == "success"
                    else (network_ranges.get(network_id) or {}).get("error"),
                }
            )
            continue
        if registered_only:
            group_results.append(
                {
                    "networkId": network_id,
                    "dexId": group["dexId"],
                    "state": "unsupported",
                    "seedsAvailable": len(group["seeds"]),
                    "schemaAvailable": False,
                    "reason": "registered_only_backfill_deferred_schema_inference",
                }
            )
            continue
        if network_id in unavailable_networks:
            group_results.append(
                {
                    "networkId": network_id,
                    "dexId": group["dexId"],
                    "state": "source_failure",
                    "seedsAvailable": len(group["seeds"]),
                    "error": "network circuit open after an exhausted RPC retry",
                }
            )
            continue
        if network_id not in clients:
            group_results.append(
                {
                    "networkId": network_id,
                    "dexId": group["dexId"],
                    "state": "source_failure",
                    "seedsAvailable": len(group["seeds"]),
                    "error": network_ranges[network_id].get("error"),
                }
            )
            continue
        if (network_ranges.get(network_id) or {}).get("state") != "success":
            group_results.append(
                {
                    "networkId": network_id,
                    "dexId": group["dexId"],
                    "state": "source_failure",
                    "seedsAvailable": len(group["seeds"]),
                    "error": network_ranges[network_id].get("error"),
                }
            )
            continue
        bounds = network_ranges[network_id]
        schema, errors = infer_group_schema(
            clients[network_id],
            group,
            bounds["toBlock"],
            bounds["fromBlock"],
            bounds["toTimestamp"],
            bounds["averageBlockSeconds"],
            config["evm"]["timestampToleranceSeconds"],
            config["evm"]["minimumSeedBlockRadius"],
            config["evm"].get("maximumInferenceLogBlockSpan"),
            config["evm"].get("maximumInferenceRequestsPerDex"),
        )
        if schema:
            inferred.append(schema)
            group_results.append(
                {
                    "networkId": network_id,
                    "dexId": group["dexId"],
                    "state": "success",
                    "seedsAvailable": len(group["seeds"]),
                    "emitter": schema["emitter"],
                    "eventTopic": schema["eventTopic"],
                    "tokenFieldsDecoded": len(schema["tokenLocations"]),
                }
            )
        else:
            if any(row.get("errorType") == "rpc_failure" for row in errors):
                unavailable_networks.add(network_id)
            group_results.append(
                {
                    "networkId": network_id,
                    "dexId": group["dexId"],
                    "state": "unsupported",
                    "seedsAvailable": len(group["seeds"]),
                    "errors": errors[:5],
                }
            )

    scan_results = []
    earliest_by_network = defaultdict(dict)
    if not inference_only:
        for schema in merge_schemas(inferred):
            network_id = schema["networkId"]
            if network_id not in clients or (network_ranges.get(network_id) or {}).get("state") != "success":
                scan_results.append(
                    {
                        "complete": False,
                        "path": "",
                        "rangesRead": 0,
                        "events": 0,
                        "decodeFailures": 0,
                        "failedRange": {
                            "fromBlock": None,
                            "toBlock": None,
                            "error": network_ranges[network_id].get("error"),
                        },
                        "networkId": network_id,
                        "dexIds": schema["dexIds"],
                        "emitter": schema["emitter"],
                        "eventTopic": schema["eventTopic"],
                        "tokenFieldsDecoded": len(schema["tokenLocations"]),
                        "historicalSource": historical_source_types.get(network_id, "unavailable"),
                    }
                )
                continue
            digest = hashlib.sha256(schema_key(schema)[1].encode("utf-8") + schema_key(schema)[2].encode("utf-8")).hexdigest()[:12]
            filename = f"{safe_slug(network_id)}--{safe_slug(schema['dexIds'][0])}--{digest}.jsonl"
            result = scan_schema(
                historical_clients[network_id],
                schema,
                network_ranges[network_id]["fromBlock"],
                network_ranges[network_id]["toBlock"],
                historical_settings[network_id],
                run_root / "events" / filename,
            )
            for token, event in result.pop("earliestTokens").items():
                existing = earliest_by_network[network_id].get(token)
                if not existing or event["blockNumber"] < existing["blockNumber"]:
                    earliest_by_network[network_id][token] = event
            result.update(
                {
                    "networkId": network_id,
                    "dexIds": schema["dexIds"],
                    "emitter": schema["emitter"],
                    "eventTopic": schema["eventTopic"],
                    "tokenFieldsDecoded": len(schema["tokenLocations"]),
                    "historicalSource": historical_source_types[network_id],
                }
            )
            scan_results.append(result)

    solana_results = probe_solana_programs(solana_groups, config["solana"], ledger)
    solana_scan_results = []
    if solana_groups and not inference_only:
        observed_solana_dex_ids = {group["dexId"] for group in solana_groups}
        solana_schemas = []
        for schema in config["solana"].get("creationSchemas") or []:
            schema_dex_ids = set(schema.get("dexIds") or [])
            if schema.get("bagsAccount"):
                schema_dex_ids.add("bags-fm")
            if schema_dex_ids & observed_solana_dex_ids:
                solana_schemas.append(schema)
        solana_result = scan_solana_archive(
            config["solana"],
            solana_schemas,
            cutoff,
            run_root / "events" / "solana-mainnet--registered-creation-schemas.jsonl",
            ledger,
        )
        for token, event in solana_result.pop("earliestTokens").items():
            existing = earliest_by_network["solana-mainnet"].get(token)
            if not existing or event["blockNumber"] < existing["blockNumber"]:
                earliest_by_network["solana-mainnet"][token] = event
        solana_scan_results.append(solana_result)
        network_ranges["solana-mainnet"] = {
            "state": (
                "success"
                if solana_result["requestedWindowComplete"]
                else (
                    "source_range_complete_requested_window_incomplete"
                    if solana_result["sourceRangeComplete"]
                    else "source_failure"
                )
            ),
            "fromBlock": solana_result["coverageStartsAtBlockHeight"],
            "toBlock": solana_result["coverageEndsAtBlockHeight"],
            "fromSlot": solana_result["coverageStartsAtSlot"],
            "fromTimestamp": int(parse_utc(solana_result["coverageStartsAt"]).timestamp())
            if solana_result["coverageStartsAt"]
            else None,
            "cutoffAt": cutoff.isoformat().replace("+00:00", "Z"),
            "coverageStartsAtOrBeforeCutoff": solana_result["requestedWindowComplete"],
            "rangeSource": solana_result["historicalSource"],
        }

    candidate_path = run_root / "candidate-tokens.jsonl"
    candidates = 0
    if earliest_by_network:
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = candidate_path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for network_id, rows in sorted(earliest_by_network.items()):
                missing_timestamp_blocks = [
                    row["blockNumber"] for row in rows.values() if row.get("blockTimestamp") is None
                ]
                if missing_timestamp_blocks:
                    rpc = (
                        historical_clients[network_id]
                        if historical_source_types[network_id] == "json_rpc_public"
                        else clients[network_id]
                    )
                    timestamps = rpc.block_timestamps(
                        missing_timestamp_blocks,
                        historical_settings[network_id]["blockTimestampBatchSize"],
                    )
                else:
                    timestamps = {}
                for token, event in sorted(rows.items()):
                    observed_at = (
                        datetime.fromtimestamp(event["blockTimestamp"], timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                        if event.get("blockTimestamp") is not None
                        else timestamps[event["blockNumber"]]
                    )
                    candidate = {
                        "networkId": network_id,
                        "tokenAddress": token,
                        "earliestCoveredPoolAt": observed_at,
                        "poolId": event["poolId"],
                        "dexIds": event["dexIds"],
                        "t0EvidenceType": "covered_dex_pool_created",
                        "t0Status": "covered_dex_lower_bound_not_global_t0",
                    }
                    if event.get("slot") is not None:
                        candidate["earliestCoveredPoolSlot"] = event["slot"]
                        candidate["earliestCoveredArchiveBlockHeight"] = event["blockNumber"]
                    else:
                        candidate["earliestCoveredPoolBlock"] = event["blockNumber"]
                    handle.write(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")) + "\n")
                    candidates += 1
        temporary.replace(candidate_path)

    all_evm_groups_inferred = len(inferred) == len(evm_groups)
    all_evm_scans_complete = bool(scan_results) and all(row["complete"] for row in scan_results)
    all_solana_groups_decoded = bool(solana_results) and all(
        row.get("decoderAvailable") for row in solana_results
    )
    all_solana_source_scans_complete = bool(solana_scan_results) and all(
        row["sourceRangeComplete"] for row in solana_scan_results
    )
    all_solana_requested_window_complete = bool(solana_scan_results) and all(
        row["requestedWindowComplete"] for row in solana_scan_results
    )
    finished_at = utc_now()
    return {
        "schemaVersion": "convexity-gate0-dex-backfill-run-v0.1",
        "runId": run_id,
        "startedFromSeedRun": seed_run.get("runId"),
        "finishedAt": finished_at,
        "phase": "gate0_dex_backfill_not_product_frozen",
        "boundary": config["boundary"],
        "execution": {
            "windowDays": config["windowDays"],
            "selectedNetworks": sorted(selected) if selected else sorted({row["networkId"] for row in groups}),
            "inferenceOnly": inference_only,
            "registeredOnly": registered_only,
            "projectWaitDays": 0,
            "liveReliabilityCheckBlocksDevelopment": False,
        },
        "coverage": {
            "observedDexGroups": len(groups),
            "evmDexGroups": len(evm_groups),
            "evmSchemasInferred": len(inferred),
            "evmScanUnits": len(scan_results),
            "evmScansComplete": sum(row["complete"] for row in scan_results),
            "solanaDexGroups": len(solana_groups),
            "solanaDexGroupsDecoded": sum(
                bool(row.get("decoderAvailable")) for row in solana_results
            ),
            "solanaScanUnits": len(solana_scan_results),
            "solanaSourceScansComplete": sum(
                row["sourceRangeComplete"] for row in solana_scan_results
            ),
            "solanaRequestedWindowScansComplete": sum(
                row["requestedWindowComplete"] for row in solana_scan_results
            ),
            "candidateTokens": candidates,
            "eventRows": sum(row["events"] for row in scan_results)
            + sum(row["events"] for row in solana_scan_results),
            "allObservedEvmGroupsInferred": all_evm_groups_inferred,
            "allEvmScansComplete": all_evm_scans_complete,
            "allObservedSolanaGroupsDecoded": all_solana_groups_decoded,
            "allSolanaSourceScansComplete": all_solana_source_scans_complete,
            "allSolanaRequestedWindowComplete": all_solana_requested_window_complete,
            "marketWideComplete": False,
            "usableAsGlobalT0": False,
        },
        "networkRanges": network_ranges,
        "evmGroupResults": group_results,
        "evmSchemas": inferred,
        "evmScanResults": scan_results,
        "solanaProgramResults": solana_results,
        "solanaCreationSchemas": config["solana"].get("creationSchemas") or [],
        "solanaScanResults": solana_scan_results,
        "candidatePath": str(candidate_path.resolve()) if candidates else "",
        "requestSummary": {
            "total": len(ledger.requests),
            "success": sum(row["state"] == "success" for row in ledger.requests),
            "quotaLimited": sum(row["state"] == "quota_limited" for row in ledger.requests),
            "sourceFailure": sum(row["state"] == "source_failure" for row in ledger.requests),
        },
        "requests": ledger.requests,
        "gate0Conclusion": {
            "backfillBlocksOnFutureDays": False,
            "readyForAlgorithm": all_evm_groups_inferred
            and (all_evm_scans_complete or not evm_groups)
            and (all_solana_requested_window_complete or not solana_groups),
            "reason": "历史回扫立即执行；实时连续运行只验证采集稳定性，不要求项目或开发等待。",
        },
    }


def persist_run(run, config):
    invalid_groups = {
        (row["networkId"], dex_id)
        for row in run.get("evmScanResults") or []
        if (row.get("failedRange") or {}).get("errorKind") == "semantic_mismatch"
        for dex_id in row.get("dexIds") or []
    }
    if invalid_groups:
        run["evmSchemas"] = [
            schema
            for schema in run.get("evmSchemas") or []
            if not any(
                (schema["networkId"], dex_id) in invalid_groups
                for dex_id in schema.get("dexIds") or []
            )
        ]
        run["coverage"]["evmSchemasInferred"] = len(run["evmSchemas"])
        run["coverage"]["allObservedEvmGroupsInferred"] = False
        run["gate0Conclusion"]["readyForAlgorithm"] = False
        for row in run.get("evmGroupResults") or []:
            if (row["networkId"], row["dexId"]) in invalid_groups:
                row.update(
                    {
                        "state": "unsupported",
                        "error": "creation_event_semantic_mismatch_repeated_pool_ids",
                    }
                )
        registry = load_schema_registry(config)
        registry["schemas"] = [
            schema
            for schema in registry.get("schemas") or []
            if not any(
                (schema["networkId"], dex_id) in invalid_groups
                for dex_id in schema.get("dexIds") or []
            )
        ]
        registry["updatedAt"] = utc_now()
        atomic_write_json(PROJECT_ROOT / config["schemaRegistry"], registry)
    root = PROJECT_ROOT / config["outputRoot"]
    path = root / "runs" / run["runId"] / "summary.json"
    atomic_write_json(path, run)
    atomic_write_json(root / "latest.json", run)
    if run.get("evmSchemas"):
        update_schema_registry(config, run["evmSchemas"])
    build_coverage_rollup(config)
    return path


def main():
    parser = argparse.ArgumentParser(description="Gate 0 read-only 90-day DEX creation-event backfill")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--shadow-config", default=str(PROJECT_ROOT / "config" / "gate0-shadow-scope.json"))
    parser.add_argument("--seed-run")
    parser.add_argument("--network", action="append", dest="networks")
    parser.add_argument("--inference-only", action="store_true")
    parser.add_argument("--registered-only", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    config = load_config(arguments.config)
    shadow_config = json.loads(Path(arguments.shadow_config).read_text(encoding="utf-8"))
    seed_path = Path(arguments.seed_run or PROJECT_ROOT / config["seedRun"])
    seed_run = json.loads(seed_path.read_text(encoding="utf-8"))
    configured = {row["id"] for row in shadow_config["networks"]}
    if arguments.networks and not set(arguments.networks).issubset(configured):
        raise SystemExit("--network contains an unconfigured network")
    run = run_backfill(
        config,
        shadow_config,
        seed_run,
        networks=arguments.networks,
        inference_only=arguments.inference_only,
        registered_only=arguments.registered_only,
    )
    path = ""
    if not arguments.no_write:
        path = str(persist_run(run, config).resolve())
    print(
        json.dumps(
            {
                "runId": run["runId"],
                "summaryPath": path,
                "coverage": run["coverage"],
                "requestSummary": run["requestSummary"],
                "gate0Conclusion": run["gate0Conclusion"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
