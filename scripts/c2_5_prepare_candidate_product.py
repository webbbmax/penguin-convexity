#!/usr/bin/env python3
"""Prepare a persisted, isolated C2.5 candidate product snapshot state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import build_c2_4_snapshots as builder
from temp_artifact_retention import TempArtifactRetention


SNAPSHOT_SPECS = {
    "candidate": (builder.DEFAULT_CANDIDATE.name, builder.CANDIDATE_PREFIX),
    "tracking": (builder.DEFAULT_TRACKING.name, builder.TRACKING_PREFIX),
    "front": (builder.DEFAULT_FRONT.name, builder.FRONT_PREFIX),
    "admin": (builder.DEFAULT_ADMIN.name, builder.ADMIN_PREFIX),
}
INHERITED_SNAPSHOT = "c2-2-admin-snapshot.js"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_snapshot(path: Path, prefix: str, payload: dict[str, Any]) -> None:
    text = prefix + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + ";\n"
    json.loads(text[len(prefix) : -2])
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _git_head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _ignore_generated_snapshots(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name.endswith("-snapshot.js")]


def prepare_candidate_product(candidate_root: Path, source_root: Path, *, retention_hours: float = 168.0) -> dict[str, Any]:
    candidate_root = Path(candidate_root).resolve()
    source_root = Path(source_root).resolve()
    if candidate_root == source_root:
        raise ValueError("候选工作区与正式只读来源必须隔离")
    for path in (
        candidate_root / "docs" / "C2.5_REQUIREMENTS_LOCK.json",
        source_root / "data" / "c2.1-pipeline.db",
        source_root / "data" / "convexity.db",
        source_root / "app" / INHERITED_SNAPSHOT,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    manager = TempArtifactRetention(
        project_root=candidate_root,
        managed_root=candidate_root / "runtime" / "temp-artifacts",
        state_path=candidate_root / "runtime" / "maintenance" / "temp-artifact-sweep.json",
        audit_path=candidate_root / "runtime" / "maintenance" / "temp-artifact-cleanup.jsonl",
    )
    marker = manager.create(
        owner_task="c2-5-pre-release-candidate-product",
        purpose="发布前正常候选产品路径的持久化完整规则重放快照",
        retention_hours=72.0,
    )
    artifact_root = Path(marker["absolutePath"])
    product_root = artifact_root / "product-state"
    snapshot_root = product_root / "app"
    snapshot_root.mkdir(parents=True)
    try:
        if (candidate_root / "app").is_dir():
            shutil.copytree(
                candidate_root / "app",
                snapshot_root,
                dirs_exist_ok=True,
                ignore=_ignore_generated_snapshots,
            )
        inherited_snapshot_count = 0
        for path in (source_root / "app").glob("*-snapshot.js"):
            shutil.copy2(path, snapshot_root / path.name)
            inherited_snapshot_count += 1
        original_root = builder.PROJECT_ROOT
        try:
            builder.PROJECT_ROOT = source_root
            payloads = builder.build_snapshots(
                db_path=source_root / "data" / "c2.1-pipeline.db",
                output_dir=source_root / "app",
                write=False,
            )
        finally:
            builder.PROJECT_ROOT = original_root
        for key, (name, prefix) in SNAPSHOT_SPECS.items():
            _write_snapshot(snapshot_root / name, prefix, payloads[key])
        governed_snapshot_names = [name for name, _prefix in SNAPSHOT_SPECS.values()] + [INHERITED_SNAPSHOT]
        snapshot_files = {name: _canonical_sha256(snapshot_root / name) for name in governed_snapshot_names}
        candidate_commit = _git_head(candidate_root)
        if not candidate_commit or len(candidate_commit) != 40:
            raise ValueError("候选工作区没有可锁定的Git HEAD")
        service_files = {
            path.relative_to(snapshot_root).as_posix(): _file_sha256(path)
            for path in sorted(snapshot_root.rglob("*"))
            if path.is_file()
        }
        service_tree_sha256 = _stable_digest(service_files)
        manifest = {
            "schemaVersion": "c2.5-candidate-product-manifest-v2",
            "preparedAt": _iso_now(),
            "candidateCommit": candidate_commit,
            "sourceProjectRoot": str(source_root),
            "sourceDatabaseMode": "sqlite_mode_ro_transaction",
            "sourceSnapshotBuildId": payloads["tracking"].get("buildId"),
            "sourceDataAsOf": payloads["tracking"].get("dataCutoffAt"),
            "trackingItemCount": len(payloads["tracking"].get("items") or []),
            "ruleReplayInputCount": sum(bool(row.get("ruleReplayInputs")) for row in payloads["tracking"].get("items") or []),
            "inheritedSnapshotCount": inherited_snapshot_count,
            "snapshotFiles": snapshot_files,
            "serviceFileCount": len(service_files),
            "serviceTreeSha256": service_tree_sha256,
            "serviceFiles": service_files,
        }
        manifest_path = product_root / "candidate-product-manifest.json"
        _atomic_json(manifest_path, manifest)
        manifest_sha256 = _file_sha256(manifest_path)
        manager.seal(artifact_root, retention_hours=retention_hours)
        binding = {
            "schemaVersion": "c2.5-candidate-product-state-v2",
            "preparedAt": manifest["preparedAt"],
            "candidateCommit": manifest["candidateCommit"],
            "snapshotRoot": snapshot_root.relative_to(candidate_root).as_posix(),
            "manifestPath": manifest_path.relative_to(candidate_root).as_posix(),
            "manifestSha256": manifest_sha256,
            "readOnlyDataRoot": str(source_root / "data"),
            "readOnlyRuntimeRoot": str(source_root / "runtime"),
            "sourceProjectRoot": str(source_root),
            "snapshotFiles": snapshot_files,
            "serviceFileCount": len(service_files),
            "serviceTreeSha256": service_tree_sha256,
        }
        binding_path = candidate_root / "runtime" / "c2.5" / "candidate-product-state.json"
        _atomic_json(binding_path, binding)
        return {
            "status": "prepared",
            "artifactRoot": str(artifact_root),
            "bindingPath": str(binding_path),
            "manifest": manifest,
        }
    except Exception:
        manager.seal(artifact_root, retention_hours=24.0)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="准备隔离且持久化的C2.5发布前候选产品状态")
    parser.add_argument("--candidate-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--retention-hours", type=float, default=168.0)
    args = parser.parse_args()
    print(json.dumps(
        prepare_candidate_product(args.candidate_root, args.source_root, retention_hours=args.retention_hours),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
