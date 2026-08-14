#!/usr/bin/env python3
"""Create the non-destructive D0 inventory for the frozen Git status snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


FORMAL_SOURCE_PREFIXES = ("app/", "scripts/", "desktop-host/", "storage/")
RUNTIME_PREFIXES = ("data/", "runtime/", "backups/", "archive/", "reports/")
GENERATED_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "bin",
    "node_modules",
    "obj",
    "publish",
}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(("git", "-C", str(root), *args), text=True, encoding="utf-8")


def status_entries(root: Path) -> list[tuple[str, str]]:
    raw = subprocess.check_output(
        ("git", "-C", str(root), "status", "--porcelain=v1", "-z"),
    ).decode("utf-8", "surrogateescape")
    entries: list[tuple[str, str]] = []
    parts = raw.split("\0")
    index = 0
    while index < len(parts):
        item = parts[index]
        index += 1
        if not item:
            continue
        status = item[:2]
        path = item[3:]
        if "R" in status or "C" in status:
            index += 1
        entries.append((status, path.replace("\\", "/")))
    return entries


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_path(root: Path, relative: str) -> dict[str, object]:
    path = root / relative.rstrip("/")
    if path.is_file():
        return {"kind": "file", "bytes": path.stat().st_size, "sha256": sha256(path)}
    if path.is_dir():
        rows: list[str] = []
        total_bytes = 0
        file_count = 0
        excluded_generated_bytes = 0
        excluded_generated_files = 0
        for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.is_symlink()):
            rel = child.relative_to(path).as_posix()
            size = child.stat().st_size
            total_bytes += size
            file_count += 1
            if any(part in GENERATED_DIRECTORY_NAMES for part in child.relative_to(path).parts[:-1]):
                excluded_generated_bytes += size
                excluded_generated_files += 1
                continue
            digest = sha256(child)
            rows.append(f"{rel}\0{size}\0{digest}")
        tree = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
        return {
            "kind": "directory_status_entry",
            "bytes": total_bytes,
            "containedFiles": file_count,
            "formalFilesHashed": file_count - excluded_generated_files,
            "formalTreeSha256": tree,
            "excludedGeneratedFiles": excluded_generated_files,
            "excludedGeneratedBytes": excluded_generated_bytes,
            "generatedBoundary": "Recorded but excluded from the reconstructed Git baseline.",
        }
    return {"kind": "missing", "bytes": 0}


def classify(relative: str) -> tuple[str, str]:
    if relative == ".gitignore" or relative == "global.json":
        return "formal_source", "include_in_reconstructed_baseline"
    if relative.startswith("docs/"):
        return "formal_product_document", "include_in_reconstructed_baseline"
    if relative == "desktop-host/":
        return "formal_source", "include_formal_files_exclude_generated"
    if relative.startswith(FORMAL_SOURCE_PREFIXES):
        return "formal_source", "include_in_reconstructed_baseline"
    if relative.startswith(RUNTIME_PREFIXES):
        return "runtime_or_generated", "preserve_locally_do_not_commit"
    return "unknown_preserve", "preserve_without_deletion_pending_classification"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--expected-tracked", type=int)
    parser.add_argument("--expected-untracked", type=int)
    args = parser.parse_args()

    root = args.repo_root.resolve()
    excluded = {value.replace("\\", "/") for value in args.exclude}
    entries = [entry for entry in status_entries(root) if entry[1] not in excluded]
    tracked = sum(1 for status, _path in entries if status != "??")
    untracked = sum(1 for status, _path in entries if status == "??")

    errors: list[str] = []
    for label, actual, expected in (
        ("total", len(entries), args.expected_total),
        ("tracked", tracked, args.expected_tracked),
        ("untracked", untracked, args.expected_untracked),
    ):
        if expected is not None and actual != expected:
            errors.append(f"{label}: expected {expected}, got {actual}")

    rows: list[dict[str, object]] = []
    category_counts: dict[str, int] = {}
    for status, relative in entries:
        category, action = classify(relative)
        category_counts[category] = category_counts.get(category, 0) + 1
        rows.append(
            {
                "status": status,
                "path": relative,
                "category": category,
                "action": action,
                **inspect_path(root, relative),
            }
        )

    report = {
        "schemaVersion": "d0-workspace-classification-v1",
        "release": "D0",
        "freezeSnapshotAt": "2026-08-14T11:48:05+08:00",
        "sourceRoot": "F:\\codex项目\\企鹅投研\\凸性",
        "sourceBranch": git(root, "branch", "--show-current").strip(),
        "sourceHead": git(root, "rev-parse", "HEAD").strip(),
        "statusEntryDefinition": "git status --porcelain=v1 entries; an untracked directory is one entry and records its contained file count and tree hash",
        "excludedPostFreezeD0Files": sorted(excluded),
        "counts": {
            "total": len(entries),
            "trackedChanged": tracked,
            "untracked": untracked,
            "categories": category_counts,
            "unknown": category_counts.get("unknown_preserve", 0),
            "deletionStatuses": sum(1 for status, _path in entries if "D" in status),
        },
        "unknownDeletionCount": 0,
        "classificationErrors": errors,
        "entries": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"D0_INVENTORY total={len(entries)} tracked={tracked} untracked={untracked} "
        f"unknown={category_counts.get('unknown_preserve', 0)} errors={len(errors)}"
    )
    return 0 if not errors and category_counts.get("unknown_preserve", 0) == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
