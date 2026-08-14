#!/usr/bin/env python3
"""D0 stage gate: reject ambiguous development, acceptance, or release state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


REQUIREMENT_STAGE = {
    **{f"A{i:02d}": "implementation" for i in range(1, 6)},
    **{f"B{i:02d}": "implementation" for i in range(1, 6)},
    **{f"C{i:02d}": "implementation" for i in range(1, 6)},
    "D01": "implementation",
    "D02": "implementation",
    "D03": "implementation",
    "D04": "release",
    "D05": "release",
    "D06": "release",
    "D07": "implementation",
    **{f"E{i:02d}": "implementation" for i in range(1, 7)},
    **{f"F{i:02d}": "implementation" for i in range(1, 9)},
    "G01": "implementation",
    "G02": "implementation",
    "G03": "implementation",
    "G04": "acceptance",
    "G05": "acceptance",
    "G06": "acceptance",
    **{f"H{i:02d}": "acceptance" for i in range(1, 7)},
}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def git_blob(repo: Path, relative: str) -> bytes | None:
    result = subprocess.run(
        ("git", "-C", str(repo), "show", f"HEAD:{relative}"),
        check=False,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def same_path(left: Path, right: Path) -> bool:
    return str(left.resolve()).casefold() == str(right.resolve()).casefold()


def within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def add_check(checks: list[dict[str, object]], check_id: str, passed: bool, detail: str) -> None:
    checks.append({"id": check_id, "passed": passed, "detail": detail})


def validate_requirements_lock(
    repo: Path,
    lock_config: dict[str, Any],
    checks: list[dict[str, object]],
) -> None:
    relative = lock_config.get("path", "docs/D0_REQUIREMENTS_LOCK.json")
    expected_lock_hash = str(lock_config.get("sha256", "")).lower()
    lock_path = repo / relative
    lock_blob = git_blob(repo, relative)
    if not lock_path.is_file() or lock_blob is None:
        add_check(checks, "LOCK_FILE", False, f"缺少需求锁：{relative}")
        return
    actual_lock_hash = hashlib.sha256(lock_blob).hexdigest()
    lock_worktree_clean = git(repo, "diff", "--quiet", "HEAD", "--", relative, check=False).returncode == 0
    add_check(
        checks,
        "LOCK_FILE",
        actual_lock_hash == expected_lock_hash and lock_worktree_clean,
        "需求锁哈希匹配且工作区未改写" if actual_lock_hash == expected_lock_hash and lock_worktree_clean else "需求锁哈希不匹配或工作区已改写",
    )
    try:
        lock = json.loads(lock_blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        add_check(checks, "LOCK_CONTENT", False, f"需求锁无法解析：{type(exc).__name__}")
        return

    errors: list[str] = []
    canonical_rows: list[str] = []
    for row in lock.get("documents", []):
        path = repo / row["path"]
        expected = str(row["sha256"]).lower()
        blob = git_blob(repo, row["path"])
        clean = git(repo, "diff", "--quiet", "HEAD", "--", row["path"], check=False).returncode == 0
        if not path.is_file() or blob is None or hashlib.sha256(blob).hexdigest() != expected or not clean:
            errors.append(row["path"])
        canonical_rows.append(f"{row['path']}:{expected}")
    canonical = "\n".join(canonical_rows).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != lock.get("requirementSetSha256"):
        errors.append("requirementSetSha256")
    for row in lock.get("inheritedFrozenDependencies", []):
        path = repo / row["path"]
        blob = git_blob(repo, row["path"])
        clean = git(repo, "diff", "--quiet", "HEAD", "--", row["path"], check=False).returncode == 0
        if not path.is_file() or blob is None or hashlib.sha256(blob).hexdigest() != str(row["sha256"]).lower() or not clean:
            errors.append(row["path"])
    add_check(
        checks,
        "LOCK_CONTENT",
        not errors,
        "冻结文件与继承依赖全部匹配" if not errors else "不匹配：" + ", ".join(errors),
    )


def validate_traceability(
    repo: Path,
    relative: str,
    required_through: str,
    checks: list[dict[str, object]],
) -> None:
    path = repo / relative
    if not path.is_file():
        add_check(checks, "TRACEABILITY", False, f"缺少需求追踪：{relative}")
        return
    try:
        rows = read_json(path).get("requirements", [])
    except (OSError, json.JSONDecodeError) as exc:
        add_check(checks, "TRACEABILITY", False, f"需求追踪无法解析：{type(exc).__name__}")
        return
    by_id = {row.get("id"): row for row in rows}
    missing = sorted(set(REQUIREMENT_STAGE) - set(by_id))
    duplicate = len(rows) != len(by_id)
    stage_rank = {"implementation": 1, "acceptance": 2, "release": 3}
    required_rank = stage_rank[required_through]
    failed = []
    for requirement_id, requirement_stage in REQUIREMENT_STAGE.items():
        if stage_rank[requirement_stage] <= required_rank:
            row = by_id.get(requirement_id, {})
            if row.get("status") != "passed" or not row.get("evidence"):
                failed.append(requirement_id)
    passed = not missing and not duplicate and not failed
    detail_parts = []
    if missing:
        detail_parts.append("缺少 " + ",".join(missing))
    if duplicate:
        detail_parts.append("存在重复编号")
    if failed:
        detail_parts.append("未通过 " + ",".join(failed))
    add_check(checks, "TRACEABILITY", passed, "需求追踪完整" if passed else "；".join(detail_parts))


def validate_evidence(
    repo: Path,
    relative: str | None,
    check_id: str,
    label: str,
    checks: list[dict[str, object]],
) -> None:
    if not relative:
        add_check(checks, check_id, False, f"缺少{label}文件路径")
        return
    path = repo / relative
    if not path.is_file():
        add_check(checks, check_id, False, f"缺少{label}：{relative}")
        return
    try:
        passed = read_json(path).get("status") == "passed"
    except (OSError, json.JSONDecodeError):
        passed = False
    add_check(checks, check_id, passed, f"{label}已通过" if passed else f"{label}未通过")


def run_secret_scan(repo: Path, checks: list[dict[str, object]]) -> None:
    scanner = Path(__file__).with_name("d0_secret_scan.py")
    result = subprocess.run(
        (sys.executable, str(scanner), "--repo-root", str(repo)),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    summary = result.stdout.splitlines()[0] if result.stdout else "密钥扫描没有返回摘要"
    add_check(checks, "SECRET_BOUNDARY", result.returncode == 0, summary)


def evaluate(config: dict[str, Any]) -> dict[str, Any]:
    stage = config.get("stage")
    if stage not in {"development", "acceptance", "release"}:
        raise ValueError("stage must be development, acceptance, or release")

    repo = Path(config["repoRoot"]).resolve()
    formal = Path(config["formalRoot"]).resolve()
    authorized_root = Path(config["authorizedWorktreeRoot"]).resolve()
    checks: list[dict[str, object]] = []

    validate_requirements_lock(repo, config.get("requirementsLock", {}), checks)
    rollback_ref = str(config.get("rollbackRef", ""))
    rollback = git(repo, "cat-file", "-e", f"{rollback_ref}^{{commit}}", check=False) if rollback_ref else None
    add_check(checks, "ROLLBACK_REF", bool(rollback_ref and rollback and rollback.returncode == 0), "回滚提交存在" if rollback_ref and rollback and rollback.returncode == 0 else "回滚提交缺失")

    branch = git(repo, "branch", "--show-current").stdout.strip()
    if stage in {"development", "acceptance"}:
        add_check(checks, "WORKTREE", within(repo, authorized_root), "开发目录位于授权 worktree 根目录" if within(repo, authorized_root) else "开发目录不在授权 worktree 根目录")
        prefix = str(config.get("allowedBranchPrefix", "codex/"))
        add_check(checks, "BRANCH", branch.startswith(prefix), f"分支为 {branch}" if branch.startswith(prefix) else f"分支 {branch} 不符合 {prefix} 前缀")
    else:
        add_check(checks, "FORMAL_ROOT", same_path(repo, formal), "发布在正式根目录执行" if same_path(repo, formal) else "发布不在正式根目录执行")
        main_branch = str(config.get("mainBranch", "main"))
        add_check(checks, "BRANCH", branch == main_branch, f"发布分支为 {branch}" if branch == main_branch else f"发布分支不是 {main_branch}")

    if stage == "development":
        formal_branch = git(formal, "branch", "--show-current").stdout.strip()
        formal_clean = not git(formal, "status", "--porcelain=v1").stdout.strip()
        main_branch = str(config.get("mainBranch", "main"))
        add_check(checks, "FORMAL_MAIN", formal_branch == main_branch and formal_clean, "正式 main 干净" if formal_branch == main_branch and formal_clean else "正式 main 不干净或分支错误")

    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    if stage in {"acceptance", "release"}:
        candidate = str(config.get("candidateCommit", ""))
        matches = bool(candidate and candidate == head)
        if stage == "release":
            matches = matches and config.get("acceptedCommit") == head and config.get("releaseCommit") == head
        add_check(checks, "CANDIDATE_FIXED", matches, "验收与发布候选提交一致" if matches else "候选、验收或发布提交不一致")
        clean = not git(repo, "status", "--porcelain=v1").stdout.strip()
        add_check(checks, "CANDIDATE_CLEAN", clean, "候选工作区干净" if clean else "候选工作区存在未提交变化")
        validate_evidence(repo, config.get("tier0Evidence"), "TIER0", "Tier 0 证据", checks)
        validate_traceability(
            repo,
            str(config.get("traceability", "docs/D0_REQUIREMENT_TRACEABILITY.json")),
            "implementation" if stage == "acceptance" else "release",
            checks,
        )

    if stage == "release":
        formal_clean = not git(formal, "status", "--porcelain=v1").stdout.strip()
        add_check(checks, "FORMAL_MAIN", formal_clean, "正式 main 干净" if formal_clean else "正式 main 不干净")
        validate_evidence(repo, config.get("desktopEvidence"), "DESKTOP", "真实桌面验收证据", checks)
        authorized = config.get("userReleaseAuthorized") is True
        add_check(checks, "USER_AUTH", authorized, "已有用户发布授权" if authorized else "缺少用户发布授权")
        tag = str(config.get("releaseTag", ""))
        tag_result = git(repo, "rev-list", "-n", "1", tag, check=False) if tag else None
        tag_commit = tag_result.stdout.strip() if tag_result and tag_result.returncode == 0 else ""
        add_check(checks, "RELEASE_TAG", bool(tag and tag_commit == head), "正式标签指向发布提交" if tag and tag_commit == head else "正式标签缺失或指向错误")

    run_secret_scan(repo, checks)
    failed = [row["id"] for row in checks if not row["passed"]]
    return {
        "schemaVersion": "d0-gate-result-v1",
        "stage": stage,
        "passed": not failed,
        "repoRoot": str(repo),
        "head": head,
        "failedChecks": failed,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(read_json(args.config))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(f"D0_GATE stage={result['stage']} passed={str(result['passed']).lower()} failed={','.join(result['failedChecks']) or 'none'}")
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
