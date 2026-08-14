#!/usr/bin/env python3
"""Read-only structural audit for the C2.5 development-readiness baseline."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ACCEPTANCE_ID_PATTERN = re.compile(r"^- ([A-J]\d{2})：", re.MULTILINE)
VALID_STAGES = {"implementation", "acceptance", "release"}
EXPECTED_SUPPLEMENTED_ALIASES = {
    "/api/c2.2/candidate-production/pause",
    "/api/c2.2/candidate-production/retry",
    "/api/c2.2/pause-current",
    "/api/c2.2/scheduler",
    "/api/c2.4/pause-current",
    "/api/c2.4/scheduler",
}
REQUIRED_FIXTURE_SETS = {
    "task_state_matrix",
    "windows_scheduler_matrix",
    "rule_transparency_matrix",
    "control_safety_matrix",
    "inheritance_registry_matrix",
    "desktop_visual_interaction_matrix",
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def add_check(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: str) -> None:
    checks.append({"id": check_id, "passed": passed, "detail": detail})


def literal_dict_assignment(path: Path, variable: str) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == variable for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return value
    raise ValueError(f"{variable} not found")


def post_endpoint_set(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "do_POST":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Compare) or len(child.ops) != 1:
                continue
            if not isinstance(child.ops[0], ast.NotIn) or len(child.comparators) != 1:
                continue
            left = child.left
            values = child.comparators[0]
            if (
                isinstance(left, ast.Attribute)
                and isinstance(left.value, ast.Name)
                and left.value.id == "self"
                and left.attr == "path"
                and isinstance(values, ast.Set)
            ):
                result = {
                    item.value
                    for item in values.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
                if result:
                    return result
    raise ValueError("do_POST allowlist not found")


def verify_lock(
    repo: Path,
    baseline: dict[str, Any],
    contract: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    lock_relative = baseline["frozenC2_5PlanningAnchor"]["requirementsLockPath"]
    lock_blob = git_blob(repo, lock_relative)
    expected_lock_hash = baseline["frozenC2_5PlanningAnchor"]["requirementsLockSha256"]
    if lock_blob is None or sha256(lock_blob) != expected_lock_hash:
        add_check(checks, "C25_LOCK", False, "C2.5需求锁未提交或哈希不匹配")
        return
    try:
        lock = json.loads(lock_blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        add_check(checks, "C25_LOCK", False, "C2.5需求锁无法解析")
        return
    mismatches = []
    compatibility_by_path = {
        row["path"]: row
        for row in contract.get("inheritedDependencyHashCompatibility", [])
    }
    for row in [*lock.get("documents", []), *lock.get("inheritedFrozenDependencies", [])]:
        blob = git_blob(repo, row["path"])
        expected = str(row["sha256"]).lower()
        actual = sha256(blob) if blob is not None else ""
        compatibility = compatibility_by_path.get(row["path"], {})
        compatible = bool(
            compatibility.get("frozenWorkingTreeSha256") == expected
            and compatibility.get("canonicalGitBlobSha256") == actual
            and compatibility.get("transformation") == "CRLF_to_LF_only"
        )
        if blob is None or (actual != expected and not compatible):
            mismatches.append(row["path"])
    add_check(
        checks,
        "C25_LOCK",
        not mismatches,
        "C2.5冻结文件与继承依赖哈希匹配" if not mismatches else "哈希不匹配：" + ",".join(mismatches),
    )


def verify_baseline(repo: Path, baseline: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    source = baseline["sourceBaseline"]["commit"]
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    source_exists = git(repo, "cat-file", "-e", f"{source}^{{commit}}", check=False).returncode == 0
    source_is_ancestor = source_exists and git(repo, "merge-base", "--is-ancestor", source, head, check=False).returncode == 0
    tag = baseline["sourceBaseline"]["tag"]
    tag_result = git(repo, "rev-list", "-n", "1", tag, check=False)
    tag_matches = tag_result.returncode == 0 and tag_result.stdout.strip() == source
    commits_match = all(
        git(repo, "merge-base", "--is-ancestor", row["commit"], source, check=False).returncode == 0
        for row in baseline["postPlanningBaselineCommitsIncluded"]
    )
    add_check(
        checks,
        "SOURCE_BASELINE",
        source_is_ancestor and tag_matches and commits_match,
        "C2.4正式源基线、标签和冻结后修复链有效" if source_is_ancestor and tag_matches and commits_match else "正式源基线、标签或修复链不一致",
    )

    isolation = baseline["c2_6Isolation"]
    isolated_commit = git(repo, "rev-parse", isolation["branch"], check=False)
    branch_matches = isolated_commit.returncode == 0 and isolated_commit.stdout.strip() == isolation["commit"]
    absent_from_head = all(git_blob(repo, relative) is None for relative in isolation["paths"])
    present_on_branch = all(
        git(repo, "cat-file", "-e", f"{isolation['branch']}:{relative}", check=False).returncode == 0
        for relative in isolation["paths"]
    )
    add_check(
        checks,
        "C26_ISOLATION",
        branch_matches and absent_from_head and present_on_branch,
        "C2.6规划已独立保存且未进入C2.5基线" if branch_matches and absent_from_head and present_on_branch else "C2.6隔离分支或路径不一致",
    )


def verify_traceability(repo: Path, checks: list[dict[str, Any]]) -> None:
    acceptance_text = (repo / "docs/C2.5_ACCEPTANCE_PLAN.md").read_text(encoding="utf-8-sig")
    acceptance_ids = ACCEPTANCE_ID_PATTERN.findall(acceptance_text)
    trace = load_json(repo / "docs/C2.5_REQUIREMENT_TRACEABILITY.json")
    rows = trace.get("requirements", [])
    trace_ids = [row.get("id") for row in rows]
    contract = load_json(repo / "docs/C2.5_GATE_CONTRACT.json")
    contract_stages = contract.get("requirementStages", {})
    structural = all(
        isinstance(row.get("summary"), str)
        and row.get("requiredByStage") in VALID_STAGES
        and isinstance(row.get("implementationOwner"), str)
        and bool(row.get("plannedTestIds"))
        and isinstance(row.get("evidenceExpected"), str)
        and row.get("status") == "pending"
        and row.get("evidence") == []
        for row in rows
    )
    exact = (
        len(acceptance_ids) == 92
        and len(set(acceptance_ids)) == 92
        and trace.get("requirementCount") == 92
        and len(trace_ids) == 92
        and len(set(trace_ids)) == 92
        and set(trace_ids) == set(acceptance_ids) == set(contract_stages)
        and all(contract_stages[row["id"]] == row["requiredByStage"] for row in rows)
    )
    add_check(
        checks,
        "TRACEABILITY_92",
        exact and structural,
        "92项需求、阶段、责任、测试和证据槽位精确完整" if exact and structural else "92项需求追踪集合或结构不完整",
    )


def verify_task_inventory(repo: Path, checks: list[dict[str, Any]]) -> None:
    frozen = load_json(repo / "docs/C2.5_TASK_INVENTORY.json")
    supplement = load_json(repo / "docs/C2.5_TASK_INVENTORY_SUPPLEMENT.json")
    frozen_ids = {row["taskId"] for row in frozen.get("entries", [])}
    mappings = supplement.get("postEndpointMappings", [])
    mapped_paths = [row.get("path") for row in mappings]
    actual_paths = post_endpoint_set(repo / "scripts/serve_local.py")
    alias_paths = {
        row["path"] for row in mappings if row.get("coverage") == "supplemented_alias"
    }
    mapped_targets_valid = all(
        row.get("targetTaskIds") and set(row["targetTaskIds"]).issubset(frozen_ids)
        for row in mappings
    )
    endpoints_exact = (
        len(actual_paths) == 20
        and len(mapped_paths) == 20
        and len(set(mapped_paths)) == 20
        and set(mapped_paths) == actual_paths
        and alias_paths == EXPECTED_SUPPLEMENTED_ALIASES
        and mapped_targets_valid
    )
    add_check(
        checks,
        "POST_TASK_MAPPING",
        endpoints_exact,
        "20个生产POST入口已映射到冻结taskId且6个别名已补齐" if endpoints_exact else "POST入口、补充别名或taskId映射不一致",
    )

    task_definitions = literal_dict_assignment(repo / "scripts/update_tasks.py", "TASK_DEFINITIONS")
    catalog = supplement.get("updateTaskCatalogTaskIds", [])
    catalog_exact = (
        frozen.get("frozenFacts", {}).get("updateTaskCatalogCount") == 21
        and len(task_definitions) == 21
        and len(catalog) == 21
        and len(set(catalog)) == 21
        and set(catalog) == set(task_definitions)
    )
    add_check(
        checks,
        "TASK_CATALOG_21",
        catalog_exact,
        "21个TASK_DEFINITIONS逐项一致" if catalog_exact else "21项任务目录不一致",
    )


def verify_fixture_and_scope(repo: Path, checks: list[dict[str, Any]]) -> None:
    fixture = load_json(repo / "docs/C2.5_FIXTURE_DESIGN.json")
    fixture_ids = {row.get("id") for row in fixture.get("fixtureSets", [])}
    fixture_ok = (
        fixture.get("status") == "design_only_not_implemented"
        and fixture.get("productionDataAllowed") is False
        and fixture_ids == REQUIRED_FIXTURE_SETS
        and all(row.get("scenarios") for row in fixture.get("fixtureSets", []))
    )
    add_check(
        checks,
        "FIXTURE_DESIGN",
        fixture_ok,
        "六组夹具设计完整且未使用生产数据" if fixture_ok else "夹具设计集合或边界不完整",
    )

    inheritance = load_json(repo / "docs/C2.5_INHERITANCE_MANIFEST.json")
    new_routes = [row["path"] for row in inheritance.get("newRoutes", [])]
    product_absent = all(not (repo / relative).exists() for relative in new_routes)
    add_check(
        checks,
        "NO_C25_PRODUCT_IMPLEMENTATION",
        product_absent,
        "11个C2.5新管理路由均未实现" if product_absent else "检测到C2.5产品路由实现",
    )


def verify_readiness_artifacts(repo: Path, checks: list[dict[str, Any]]) -> None:
    contract = load_json(repo / "docs/C2.5_GATE_CONTRACT.json")
    mismatches = []
    for row in contract.get("readinessArtifacts", []):
        blob = git_blob(repo, row["path"])
        if blob is None or sha256(blob) != row["sha256"]:
            mismatches.append(row["path"])
    add_check(
        checks,
        "READINESS_ARTIFACT_HASHES",
        not mismatches,
        "门禁保护的就绪产物哈希匹配" if not mismatches else "就绪产物哈希不匹配：" + ",".join(mismatches),
    )


def evaluate(repo: Path, *, allow_dirty: bool = False) -> dict[str, Any]:
    repo = repo.resolve()
    checks: list[dict[str, Any]] = []
    baseline = load_json(repo / "docs/C2.5_DEVELOPMENT_READINESS_BASELINE.json")
    contract = load_json(repo / "docs/C2.5_GATE_CONTRACT.json")
    verify_baseline(repo, baseline, checks)
    verify_lock(repo, baseline, contract, checks)
    verify_traceability(repo, checks)
    verify_task_inventory(repo, checks)
    verify_fixture_and_scope(repo, checks)
    verify_readiness_artifacts(repo, checks)
    dirty = bool(git(repo, "status", "--porcelain=v1").stdout.strip())
    add_check(
        checks,
        "FORMAL_MAIN_CLEAN",
        allow_dirty or not dirty,
        "正式工作区干净" if not dirty else "开发就绪产物尚未提交或仍有未解释改动",
    )
    failed = [row["id"] for row in checks if not row["passed"]]
    return {
        "schemaVersion": "c2.5-development-readiness-evidence-v1",
        "status": "passed" if not failed else "failed",
        "passed": not failed,
        "head": git(repo, "rev-parse", "HEAD").stdout.strip(),
        "failedChecks": failed,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    result = evaluate(args.repo_root, allow_dirty=args.allow_dirty)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        "C2_5_READINESS "
        f"passed={str(result['passed']).lower()} "
        f"failed={','.join(result['failedChecks']) or 'none'}"
    )
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
