#!/usr/bin/env python3
"""Negative and positive tests for the frozen D0 gate and release closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from d0_gate import REQUIREMENT_STAGE, evaluate


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class D0GateFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.formal = root / "formal"
        self.worktree_root = root / "worktrees"
        self.worktree = self.worktree_root / "task"
        self.formal.mkdir()
        self.worktree_root.mkdir()
        self.run(self.formal, "init", "-b", "main")
        self.run(self.formal, "config", "user.name", "D0 Test")
        self.run(self.formal, "config", "user.email", "d0@example.invalid")
        self.run(self.formal, "config", "core.autocrlf", "false")
        self._write_frozen_files()
        self.lock_hash = sha256(self.formal / "docs/D0_REQUIREMENTS_LOCK.json")
        self.run(self.formal, "add", ".")
        self.run(self.formal, "commit", "-m", "baseline")
        self.rollback = self.rev(self.formal)
        self.remote = root / "remote.git"
        subprocess.check_output(("git", "init", "--bare", str(self.remote)), stderr=subprocess.STDOUT)
        self.run(self.formal, "remote", "add", "origin", str(self.remote))
        self.run(self.formal, "push", "-u", "origin", "main")
        self.run(self.formal, "worktree", "add", "-b", "codex/test", str(self.worktree))
        self._write_evidence()
        self.run(self.worktree, "add", ".")
        self.run(self.worktree, "commit", "-m", "candidate")
        self.candidate = self.rev(self.worktree)

    @staticmethod
    def run(repo: Path, *args: str) -> str:
        return subprocess.check_output(
            ("git", "-C", str(repo), *args),
            text=True,
            encoding="utf-8",
            stderr=subprocess.STDOUT,
        ).strip()

    def rev(self, repo: Path) -> str:
        return self.run(repo, "rev-parse", "HEAD")

    @staticmethod
    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_frozen_files(self) -> None:
        documents = []
        for name, content in (
            ("docs/D0_FREEZE_DRAFT.md", "freeze\n"),
            ("docs/D0_DEVELOPMENT_WORKFLOW.md", "workflow\n"),
            ("docs/D0_ACCEPTANCE_PLAN.md", "acceptance\n"),
        ):
            path = self.formal / name
            self.write(path, content)
            documents.append({"path": name, "sha256": sha256(path)})
        dependencies = []
        for name in (
            "docs/C2.4_REQUIREMENTS_LOCK.json",
            "docs/C2.4_RELEASE_MANIFEST.json",
            "docs/MODEL_HANDOFF_PROTOCOL.md",
            "docs/TEST_PRIORITY_POLICY.json",
            "docs/TEMP_ARTIFACT_RETENTION_POLICY.json",
        ):
            path = self.formal / name
            self.write(path, name + "\n")
            dependencies.append({"path": name, "sha256": sha256(path)})
        canonical = "\n".join(f"{row['path']}:{row['sha256']}" for row in documents)
        lock = {
            "schemaVersion": "d0-requirements-lock-v1",
            "requirementSetSha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "documents": documents,
            "inheritedFrozenDependencies": dependencies,
        }
        self.write(
            self.formal / "docs/D0_REQUIREMENTS_LOCK.json",
            json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
        )
        self.write(self.formal / ".gitignore", "*.db\nruntime/\n")

    def _write_evidence(self) -> None:
        requirements = [
            {
                "id": requirement_id,
                "requiredByStage": required_stage,
                "status": "passed",
                "evidence": ["test fixture"],
            }
            for requirement_id, required_stage in REQUIREMENT_STAGE.items()
        ]
        self.write(
            self.worktree / "docs/D0_REQUIREMENT_TRACEABILITY.json",
            json.dumps({"requirements": requirements}, ensure_ascii=False, indent=2) + "\n",
        )
        self.write(self.worktree / "docs/tier0.json", '{"status":"passed"}\n')
        self.write(self.worktree / "docs/desktop.json", '{"status":"passed"}\n')
        artifact = self.worktree / "docs/tier0.json"
        release_manifest = {
            "status": "released",
            "releaseTag": "d0-test",
            "coreArtifacts": [
                {
                    "path": "docs/tier0.json",
                    "bytes": artifact.stat().st_size,
                    "sha256": sha256(artifact),
                }
            ],
        }
        self.write(
            self.worktree / "docs/release.json",
            json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n",
        )

    def config(self, stage: str, repo: Path | None = None) -> dict[str, object]:
        selected = repo or self.worktree
        return {
            "stage": stage,
            "repoRoot": str(selected),
            "formalRoot": str(self.formal),
            "authorizedWorktreeRoot": str(self.worktree_root),
            "allowedBranchPrefix": "codex/",
            "mainBranch": "main",
            "requirementsLock": {
                "path": "docs/D0_REQUIREMENTS_LOCK.json",
                "sha256": self.lock_hash,
            },
            "rollbackRef": self.rollback,
            "candidateCommit": self.candidate,
            "acceptedCommit": self.candidate,
            "releaseCommit": self.candidate,
            "tier0Evidence": "docs/tier0.json",
            "traceability": "docs/D0_REQUIREMENT_TRACEABILITY.json",
            "desktopEvidence": "docs/desktop.json",
            "userReleaseAuthorized": True,
            "releaseTag": "d0-test",
            "releaseManifest": "docs/release.json",
            "remoteName": "origin",
            "remoteBranch": "main",
        }

    def prepare_release(self) -> dict[str, object]:
        self.run(self.formal, "merge", "--ff-only", self.candidate)
        self.run(self.formal, "tag", "d0-test", self.candidate)
        self.run(self.formal, "push", "origin", "main")
        self.run(self.formal, "fetch", "origin", "main")
        return self.config("release", self.formal)

    def install_gate_contract(
        self,
        extra_requirements: dict[str, str],
        *,
        add_trace_rows: bool,
        inherited_hash_compatibility: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        stages = {**REQUIREMENT_STAGE, **extra_requirements}
        contract_path = self.worktree / "docs/custom-gate-contract.json"
        self.write(
            contract_path,
            json.dumps(
                {
                    "schemaVersion": "test-gate-contract-v1",
                    "requirementStages": stages,
                    "readinessArtifacts": [],
                    "inheritedDependencyHashCompatibility": inherited_hash_compatibility or [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        if add_trace_rows:
            trace_path = self.worktree / "docs/D0_REQUIREMENT_TRACEABILITY.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            trace["requirements"].extend(
                {
                    "id": requirement_id,
                    "requiredByStage": stage,
                    "status": "passed",
                    "evidence": ["custom gate fixture"],
                }
                for requirement_id, stage in extra_requirements.items()
            )
            self.write(trace_path, json.dumps(trace, ensure_ascii=False, indent=2) + "\n")
        self.run(
            self.worktree,
            "add",
            "docs/custom-gate-contract.json",
            "docs/D0_REQUIREMENT_TRACEABILITY.json",
            "docs/D0_REQUIREMENTS_LOCK.json",
        )
        self.run(self.worktree, "commit", "-m", "custom gate contract")
        self.candidate = self.rev(self.worktree)
        return {
            "path": "docs/custom-gate-contract.json",
            "sha256": sha256(contract_path),
        }

    def replace_inherited_hash(self, relative: str, frozen_hash: str) -> str:
        lock_path = self.worktree / "docs/D0_REQUIREMENTS_LOCK.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for row in lock["inheritedFrozenDependencies"]:
            if row["path"] == relative:
                row["sha256"] = frozen_hash
                break
        else:
            raise AssertionError(f"missing inherited dependency: {relative}")
        self.write(lock_path, json.dumps(lock, ensure_ascii=False, indent=2) + "\n")
        self.lock_hash = sha256(lock_path)
        return sha256(self.worktree / relative)


class D0GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="d0-gate-")
        self.fixture = D0GateFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_failed(self, result: dict[str, object], *check_ids: str) -> None:
        self.assertFalse(result["passed"])
        failed = set(result["failedChecks"])
        for check_id in check_ids:
            self.assertIn(check_id, failed)

    def test_f01_dirty_main_blocks_development(self) -> None:
        self.fixture.write(self.fixture.formal / "dirty.txt", "dirty\n")
        self.assert_failed(evaluate(self.fixture.config("development")), "FORMAL_MAIN")

    def test_f02_bad_lock_blocks_development(self) -> None:
        lock = self.fixture.worktree / "docs/D0_REQUIREMENTS_LOCK.json"
        lock.write_text(lock.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.assert_failed(evaluate(self.fixture.config("development")), "LOCK_FILE")

    def test_f03_wrong_workspace_and_branch_block_development(self) -> None:
        self.assert_failed(
            evaluate(self.fixture.config("development", self.fixture.formal)),
            "WORKTREE",
            "BRANCH",
        )

    def test_f04_failed_tier0_blocks_acceptance(self) -> None:
        self.fixture.write(self.fixture.worktree / "docs/tier0.json", '{"status":"failed"}\n')
        self.assert_failed(evaluate(self.fixture.config("acceptance")), "TIER0")
        self.assert_failed(evaluate(self.fixture.config("release_preflight")), "TIER0")

    def test_f05_candidate_mismatch_blocks_acceptance(self) -> None:
        config = self.fixture.config("acceptance")
        config["candidateCommit"] = self.fixture.rollback
        self.assert_failed(evaluate(config), "CANDIDATE_FIXED")

    def test_f06_missing_desktop_rollback_and_authorization_block_release(self) -> None:
        config = self.fixture.config("release_preflight")
        config["desktopEvidence"] = "docs/missing-desktop.json"
        config["rollbackRef"] = "deadbeef"
        config["userReleaseAuthorized"] = False
        self.assert_failed(evaluate(config), "DESKTOP", "ROLLBACK_REF", "USER_AUTH")

    def test_f07_secret_or_data_blocks_acceptance(self) -> None:
        self.fixture.write(self.fixture.worktree / "data/leak.db", "not a database\n")
        self.fixture.write(
            self.fixture.worktree / "secrets.txt",
            "alchemy=" + "alch_" + "1234567890abcdefghijklmnop" + "\n",
        )
        self.fixture.run(self.fixture.worktree, "add", "-f", "data/leak.db", "secrets.txt")
        self.fixture.run(self.fixture.worktree, "commit", "-m", "unsafe candidate")
        config = self.fixture.config("acceptance")
        config["candidateCommit"] = self.fixture.rev(self.fixture.worktree)
        self.assert_failed(evaluate(config), "SECRET_BOUNDARY")

    def test_f08_complete_release_state_passes(self) -> None:
        result = evaluate(self.fixture.prepare_release())
        self.assertTrue(result["passed"], result)

    def test_release_preflight_complete_state_passes(self) -> None:
        result = evaluate(self.fixture.config("release_preflight"))
        self.assertTrue(result["passed"], result)

    def test_release_manifest_hash_mismatch_blocks_release(self) -> None:
        path = self.fixture.worktree / "docs/release.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["coreArtifacts"][0]["sha256"] = "0" * 64
        self.fixture.write(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        self.fixture.run(self.fixture.worktree, "add", "docs/release.json")
        self.fixture.run(self.fixture.worktree, "commit", "-m", "bad release manifest")
        self.fixture.candidate = self.fixture.rev(self.fixture.worktree)
        self.assert_failed(
            evaluate(self.fixture.prepare_release()),
            "RELEASE_MANIFEST",
        )

    def test_custom_gate_contract_requires_every_declared_requirement(self) -> None:
        contract = self.fixture.install_gate_contract(
            {"I01": "acceptance", "J07": "release"},
            add_trace_rows=False,
        )
        config = self.fixture.config("acceptance")
        config["gateContract"] = contract
        result = evaluate(config)
        self.assert_failed(result, "TRACEABILITY")
        self.assertNotIn("GATE_CONTRACT", result["failedChecks"])

    def test_custom_gate_contract_hash_mismatch_blocks(self) -> None:
        contract = self.fixture.install_gate_contract(
            {"I01": "acceptance"},
            add_trace_rows=True,
        )
        contract["sha256"] = "0" * 64
        config = self.fixture.config("acceptance")
        config["gateContract"] = contract
        self.assert_failed(evaluate(config), "GATE_CONTRACT")

    def test_complete_custom_gate_contract_passes(self) -> None:
        contract = self.fixture.install_gate_contract(
            {"I01": "acceptance", "J07": "release"},
            add_trace_rows=True,
        )
        config = self.fixture.config("acceptance")
        config["gateContract"] = contract
        result = evaluate(config)
        self.assertTrue(result["passed"], result)

    def test_declared_crlf_to_lf_hash_compatibility_passes(self) -> None:
        relative = "docs/C2.4_RELEASE_MANIFEST.json"
        frozen_hash = "1" * 64
        canonical_hash = self.fixture.replace_inherited_hash(relative, frozen_hash)
        contract = self.fixture.install_gate_contract(
            {},
            add_trace_rows=False,
            inherited_hash_compatibility=[
                {
                    "path": relative,
                    "frozenWorkingTreeSha256": frozen_hash,
                    "canonicalGitBlobSha256": canonical_hash,
                    "transformation": "CRLF_to_LF_only",
                }
            ],
        )
        config = self.fixture.config("acceptance")
        config["gateContract"] = contract
        result = evaluate(config)
        self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
