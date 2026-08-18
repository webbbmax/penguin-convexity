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
from pathlib import Path, PurePosixPath
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


def _git_tracked_worktree_is_clean(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def _git_committed_app_files(root: Path, commit: str) -> dict[str, bytes]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--full-tree", commit, "--", "app"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("无法从绑定提交枚举app Git tree")
    files: dict[str, bytes] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.split(b" ", 2)
            name = PurePosixPath(raw_path.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("候选Git tree记录无法解析") from error
        if not name.parts or name.parts[0] != "app" or object_type != b"blob" or mode not in {b"100644", b"100755"}:
            raise ValueError(f"候选Git tree包含非普通app文件：{name.as_posix()}")
        relative = PurePosixPath(*name.parts[1:])
        if not relative.parts or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"候选Git tree路径无效：{relative.as_posix()}")
        if relative.name.endswith("-snapshot.js"):
            continue
        blob = subprocess.run(
            ["git", "cat-file", "blob", object_id.decode("ascii")],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if blob.returncode != 0:
            raise ValueError(f"无法读取候选Git blob：{relative.as_posix()}")
        files[relative.as_posix()] = blob.stdout
    if not files:
        raise ValueError("绑定提交中没有可服务的app文件")
    return files


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
    candidate_commit = _git_head(candidate_root)
    if not candidate_commit or len(candidate_commit) != 40:
        raise ValueError("候选工作区没有可锁定的Git HEAD")
    if not _git_tracked_worktree_is_clean(candidate_root):
        raise ValueError("候选Git工作区存在未提交的跟踪文件变更")
    committed_app_files = _git_committed_app_files(candidate_root, candidate_commit)
    committed_app_hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in sorted(committed_app_files.items())}

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
        for name, payload in committed_app_files.items():
            target = snapshot_root.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
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
        if _git_head(candidate_root) != candidate_commit or not _git_tracked_worktree_is_clean(candidate_root):
            raise ValueError("候选Git工作区在产物准备期间发生变更")
        service_files = {
            path.relative_to(snapshot_root).as_posix(): _file_sha256(path)
            for path in sorted(snapshot_root.rglob("*"))
            if path.is_file()
        }
        service_tree_sha256 = _stable_digest(service_files)
        non_snapshot_service_files = {name: digest for name, digest in service_files.items() if not PurePosixPath(name).name.endswith("-snapshot.js")}
        if non_snapshot_service_files != committed_app_hashes:
            raise ValueError("候选非快照服务树与绑定提交Git blob不一致")
        commit_app_tree_sha256 = _stable_digest(committed_app_hashes)
        manifest = {
            "schemaVersion": "c2.5-candidate-product-manifest-v3",
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
            "commitAppFileCount": len(committed_app_hashes),
            "commitAppTreeSha256": commit_app_tree_sha256,
            "commitAppFiles": committed_app_hashes,
        }
        manifest_path = product_root / "candidate-product-manifest.json"
        _atomic_json(manifest_path, manifest)
        manifest_sha256 = _file_sha256(manifest_path)
        manager.seal(artifact_root, retention_hours=retention_hours)
        binding = {
            "schemaVersion": "c2.5-candidate-product-state-v3",
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
            "commitAppFileCount": len(committed_app_hashes),
            "commitAppTreeSha256": commit_app_tree_sha256,
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
