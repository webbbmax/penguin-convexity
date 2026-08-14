#!/usr/bin/env python3
"""Scan a Git candidate without printing credential values.

The scanner is intentionally conservative: it blocks known credential formats,
literal credential assignments, exact matches to credential-like environment
variables, and files that belong to the local runtime/data boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable


FORBIDDEN_PATH = re.compile(
    r"^(?:data|runtime|backups|archive|reports)/"
    r"|\.(?:db|sqlite|sqlite3|log|exe)$"
    r"|^app/.*-snapshot\.(?:js|json|html)$",
    re.IGNORECASE,
)

KNOWN_PATTERNS = (
    ("private_key_pem", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("alchemy_key", re.compile(r"\balch_[A-Za-z0-9_-]{16,}\b")),
    ("blockscout_key", re.compile(r"\bproapi_[A-Za-z0-9_.-]{20,}\b")),
    ("sqd_key", re.compile(r"\bsqd_data_[A-Za-z0-9_.-]{20,}\b")),
    ("quicknode_key", re.compile(r"\bQN_[A-Za-z0-9_.-]{20,}\b")),
    ("bearer_literal", re.compile(r"\bBearer\s+[A-Za-z0-9_.-]{20,}\b", re.IGNORECASE)),
)

CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|app[_-]?secret|client[_-]?secret|access[_-]?token|"
    r"refresh[_-]?token|password|passwd|private[_-]?key|secret[_-]?key)"
    r"[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_./+=-]{12,})"
)

PROVIDER_ASSIGNMENT = re.compile(
    r"(?i)(?:helius|alchemy|geckoterminal|goplus|coinmarketcap|blockscout|"
    r"etherscan|nodereal|chainstack|quicknode|sqd(?:\s+portal)?)"
    r"[^\r\n:=：]{0,40}[:=：]\s*([A-Za-z0-9_./+=-]{16,})"
)

PLACEHOLDER_WORDS = (
    "placeholder",
    "example",
    "environment",
    "env_var",
    "not_configured",
    "redacted",
    "none",
    "null",
)

ENV_CREDENTIAL_NAME = re.compile(
    r"(?i)(?:api|key|token|secret|password|alchemy|helius|gecko|goplus|"
    r"coinmarketcap|blockscout|etherscan|nodereal|chainstack|quicknode|sqd)"
)


def _git_bytes(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), stderr=subprocess.DEVNULL)


def _candidate_paths(index: bool) -> list[str]:
    args = ("ls-files", "-z") if index else ("ls-tree", "-r", "--name-only", "-z", "HEAD")
    return [
        item
        for item in _git_bytes(*args).decode("utf-8", "surrogateescape").split("\0")
        if item
    ]


def _candidate_bytes(path: str, index: bool) -> bytes:
    revision = f":{path}" if index else f"HEAD:{path}"
    return _git_bytes("show", revision)


def _fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:12]


def _environment_secrets() -> Iterable[tuple[str, bytes]]:
    for name, value in os.environ.items():
        if not ENV_CREDENTIAL_NAME.search(name) or len(value) < 16:
            continue
        if any(word in value.lower() for word in PLACEHOLDER_WORDS):
            continue
        yield name, value.encode("utf-8", "ignore")


def scan(index: bool) -> dict[str, object]:
    paths = _candidate_paths(index)
    environment_secrets = tuple(_environment_secrets())
    forbidden_paths: list[str] = []
    findings: list[dict[str, object]] = []
    text_scanned = 0
    binary_skipped = 0

    for path in paths:
        if FORBIDDEN_PATH.search(path):
            forbidden_paths.append(path)
        data = _candidate_bytes(path, index)
        if b"\0" in data[:8192]:
            binary_skipped += 1
            continue
        text_scanned += 1
        for _name, secret in environment_secrets:
            if secret and secret in data:
                findings.append(
                    {
                        "path": path,
                        "line": 0,
                        "kind": "environment_secret_exact_match",
                        "fingerprint": _fingerprint(secret),
                    }
                )
        text = data.decode("utf-8", "replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            for kind, pattern in KNOWN_PATTERNS:
                for match in pattern.finditer(line):
                    findings.append(
                        {
                            "path": path,
                            "line": line_number,
                            "kind": kind,
                            "fingerprint": _fingerprint(match.group(0).encode("utf-8")),
                        }
                    )
            for kind, pattern in (
                ("credential_assignment", CREDENTIAL_ASSIGNMENT),
                ("provider_credential_assignment", PROVIDER_ASSIGNMENT),
            ):
                for match in pattern.finditer(line):
                    value = match.group(1)
                    if any(word in value.lower() for word in PLACEHOLDER_WORDS):
                        continue
                    if kind == "provider_credential_assignment" and (
                        value.isidentifier() or not any(character.isdigit() for character in value)
                    ):
                        continue
                    findings.append(
                        {
                            "path": path,
                            "line": line_number,
                            "kind": kind,
                            "fingerprint": _fingerprint(value.encode("utf-8")),
                        }
                    )

    unique_findings: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()
    for finding in findings:
        key = tuple(finding.values())
        if key not in seen:
            seen.add(key)
            unique_findings.append(finding)

    return {
        "schemaVersion": "d0-secret-scan-v1",
        "scope": "git_index" if index else "head",
        "passed": not forbidden_paths and not unique_findings,
        "candidateFiles": len(paths),
        "textFilesScanned": text_scanned,
        "binaryFilesSkipped": binary_skipped,
        "forbiddenPaths": forbidden_paths,
        "highConfidenceFindings": unique_findings,
        "valueDisclosure": "Credential values are never written to this report.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", action="store_true", help="Scan the staged Git index instead of HEAD")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = scan(args.index)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        "SECRET_SCAN "
        f"passed={str(report['passed']).lower()} "
        f"files={report['candidateFiles']} "
        f"text={report['textFilesScanned']} "
        f"binary={report['binaryFilesSkipped']} "
        f"forbidden={len(report['forbiddenPaths'])} "
        f"findings={len(report['highConfidenceFindings'])}"
    )
    for path in report["forbiddenPaths"]:
        print(f"FORBIDDEN_PATH path={path}")
    for finding in report["highConfidenceFindings"]:
        print(
            "SECRET_FINDING "
            f"path={finding['path']} line={finding['line']} "
            f"kind={finding['kind']} fingerprint={finding['fingerprint']}"
        )
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
