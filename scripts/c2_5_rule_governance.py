#!/usr/bin/env python3
"""C2.5 protected rule approval, activation and explicit version rollback."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from c2_4_rules import (
    EXPECTED_RULE_SHA256,
    EXPECTED_TRIAL_SHA256,
    FROZEN_PUBLIC_RULE_VERSION,
    TRIAL_PUBLIC_RULE_VERSION,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"规则治理记录不是JSON对象：{path.name}")
    return value


class RuleGovernanceStore:
    """Immutable drafts/evidence plus an atomic active-version selector."""

    def __init__(
        self,
        root: Path,
        *,
        rule_path: Path,
        trial_path: Path,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.root = Path(root).resolve()
        self.rule_path = Path(rule_path).resolve()
        self.trial_path = Path(trial_path).resolve()
        self.clock = clock
        self.selector_path = self.root / "current.json"
        self.draft_root = self.root / "drafts"
        self.version_root = self.root / "versions"
        self.decision_root = self.root / "decisions"
        self.run_link_root = self.root / "run-links"

    def _source_hashes(self) -> dict[str, str]:
        hashes = {"ruleConfig": _sha256(self.rule_path), "trial": _sha256(self.trial_path)}
        if hashes != {"ruleConfig": EXPECTED_RULE_SHA256, "trial": EXPECTED_TRIAL_SHA256}:
            raise ValueError("冻结规则或活动试行来源哈希不一致，规则治理保持阻断。")
        return hashes

    def _known_versions(self) -> list[dict[str, Any]]:
        hashes = self._source_hashes()
        return [
            {
                "version": FROZEN_PUBLIC_RULE_VERSION,
                "label": "C2.4冻结基线",
                "sourcePath": "docs/C2.4_RULE_CONFIG.json",
                "sourceSha256": hashes["ruleConfig"],
                "changes": "恢复15%公开底线、10%强路径和冻结严重异常门槛。",
            },
            {
                "version": TRIAL_PUBLIC_RULE_VERSION,
                "label": "用户已授权活动试行",
                "sourcePath": "docs/C2.4_RULE_RELAXATION_TRIAL_20260813.json",
                "sourceSha256": hashes["trial"],
                "changes": "报价成功即可，六项试行门槛不作为门槛但原始证据保留。",
            },
        ]

    def _validate_version(self, version: str) -> dict[str, Any]:
        row = next((item for item in self._known_versions() if item["version"] == version), None)
        if row is None:
            raise ValueError("目标规则版本不在C2.5已冻结的可选版本集合中。")
        return row

    def _selector(self) -> dict[str, Any]:
        if not self.selector_path.exists():
            return {
                "schemaVersion": "c2.5-rule-selector-v1",
                "activeVersion": TRIAL_PUBLIC_RULE_VERSION,
                "activationId": "inherited-c2.4-user-authorized-trial",
                "effectiveAt": "2026-08-13T00:00:00Z",
                "sourceHashes": self._source_hashes(),
                "inherited": True,
            }
        selector = _read_json(self.selector_path)
        self._validate_version(str(selector.get("activeVersion") or ""))
        if selector.get("sourceHashes") != self._source_hashes():
            raise ValueError("当前规则选择器的来源哈希不一致。")
        return selector

    @staticmethod
    def _all_json(root: Path) -> list[dict[str, Any]]:
        if not root.exists():
            return []
        rows = []
        for path in sorted(root.glob("*.json")):
            rows.append(_read_json(path))
        return rows

    def state(self) -> dict[str, Any]:
        selector = self._selector()
        decisions = self._all_json(self.decision_root)
        latest_decision = {row.get("draftId"): row for row in decisions if row.get("draftId")}
        drafts = []
        for row in self._all_json(self.draft_root):
            decision = latest_decision.get(row.get("draftId"))
            drafts.append({**row, "status": decision.get("decision") if decision else "pending_approval", "decision": decision})
        links = {
            row.get("activationId"): row
            for row in self._all_json(self.run_link_root)
            if row.get("activationId")
        }
        history = []
        for row in sorted(self._all_json(self.version_root), key=lambda item: (str(item.get("effectiveAt") or ""), str(item.get("activationId") or ""))):
            link = links.get(row.get("activationId"))
            history.append(
                {
                    **row,
                    "linkedRunId": link.get("linkedRunId") if link else None,
                    "linkedSnapshotIds": link.get("linkedSnapshotIds", []) if link else [],
                    "runLink": link,
                    "runLinkStatus": "linked" if link else "pending_next_legal_run",
                }
            )
        payload = {
            "schemaVersion": "c2.5-rule-governance-state-v1",
            "activeVersion": selector["activeVersion"],
            "activeActivationId": selector.get("activationId"),
            "selector": selector,
            "knownVersions": self._known_versions(),
            "drafts": drafts,
            "history": history,
        }
        payload["stateVersion"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return payload

    @staticmethod
    def _validate_replay_evidence(replay_evidence: dict[str, Any]) -> None:
        if replay_evidence.get("schemaVersion") != "c2.5-rule-dual-replay-evidence-v1":
            raise ValueError("规则变更必须保存分开的固定历史样本和当前只读样本重放。")
        expected = {
            "fixedHistorical": "fixed_historical",
            "currentReadOnly": "current_read_only",
        }
        for key, sample_kind in expected.items():
            sample = replay_evidence.get(key)
            replay = sample.get("replay") if isinstance(sample, dict) else None
            if (
                not isinstance(sample, dict)
                or sample.get("sampleKind") != sample_kind
                or sample.get("readOnly") is not True
                or not isinstance(replay, dict)
                or replay.get("sameInput") is not True
                or replay.get("assetIdSetRecomputed") is not True
            ):
                raise ValueError("固定历史样本和当前只读样本必须分别完成同输入assetId重放。")
        if int(replay_evidence["fixedHistorical"]["replay"].get("inputCount") or 0) < 1:
            raise ValueError("固定历史样本不能为空。")

    def create_draft(
        self,
        *,
        target_version: str,
        reason: str,
        scope: str,
        end_condition: str,
        replay_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        target = self._validate_version(str(target_version or ""))
        if not all(str(value or "").strip() for value in (reason, scope, end_condition)):
            raise ValueError("规则草案必须填写原因、适用范围和结束条件。")
        self._validate_replay_evidence(replay_evidence)
        current = self._selector()
        draft_id = f"rule-draft-{uuid.uuid4().hex}"
        draft = {
            "schemaVersion": "c2.5-rule-draft-v1",
            "draftId": draft_id,
            "createdAt": _iso_time(self.clock()),
            "createdBy": "local_manager",
            "sourceVersion": current["activeVersion"],
            "targetVersion": target["version"],
            "reason": str(reason).strip(),
            "scope": str(scope).strip(),
            "endCondition": str(end_condition).strip(),
            "difference": target["changes"],
            "replayEvidence": replay_evidence,
            "sourceHashes": self._source_hashes(),
        }
        _atomic_json(self.draft_root / f"{draft_id}.json", draft)
        return draft

    def _draft(self, draft_id: str) -> dict[str, Any]:
        path = self.draft_root / f"{Path(str(draft_id)).name}.json"
        if not path.exists():
            raise ValueError("规则草案不存在。")
        draft = _read_json(path)
        if any(row.get("draftId") == draft_id for row in self._all_json(self.decision_root)):
            raise ValueError("规则草案已经完成审批，不能重复处理。")
        return draft

    def _activate(self, *, target_version: str, kind: str, reason: str, replay_evidence: dict[str, Any], draft_id: str | None = None) -> dict[str, Any]:
        target = self._validate_version(target_version)
        before = self._selector()
        now = _iso_time(self.clock())
        activation_id = f"rule-version-{uuid.uuid4().hex}"
        activation = {
            "schemaVersion": "c2.5-rule-version-record-v1",
            "activationId": activation_id,
            "kind": kind,
            "effectiveAt": now,
            "approvedBy": "local_manager",
            "draftId": draft_id,
            "activeVersion": target["version"],
            "previousVersion": before["activeVersion"],
            "rollbackOfVersion": before["activeVersion"] if kind == "rollback" else None,
            "reason": reason,
            "difference": target["changes"],
            "replayEvidence": replay_evidence,
            "sourceHashes": self._source_hashes(),
            "historicalEvidencePreserved": True,
            "effectiveTiming": "next_legal_run",
            "linkedRunId": None,
            "linkedSnapshotIds": [],
            "runLinkStatus": "pending_next_legal_run",
        }
        _atomic_json(self.version_root / f"{activation_id}.json", activation)
        _atomic_json(
            self.selector_path,
            {
                "schemaVersion": "c2.5-rule-selector-v1",
                "activeVersion": target["version"],
                "activationId": activation_id,
                "effectiveAt": now,
                "sourceHashes": self._source_hashes(),
            },
        )
        return activation

    def approve_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self._draft(draft_id)
        activation = self._activate(
            target_version=draft["targetVersion"],
            kind="approval",
            reason=draft["reason"],
            replay_evidence=draft["replayEvidence"],
            draft_id=draft["draftId"],
        )
        _atomic_json(
            self.decision_root / f"{draft['draftId']}.json",
            {"schemaVersion": "c2.5-rule-decision-v1", "draftId": draft["draftId"], "decision": "approved", "decidedAt": _iso_time(self.clock()), "activationId": activation["activationId"]},
        )
        return activation

    def reject_draft(self, draft_id: str, reason: str) -> dict[str, Any]:
        draft = self._draft(draft_id)
        if not str(reason or "").strip():
            raise ValueError("拒绝规则草案必须填写原因。")
        decision = {"schemaVersion": "c2.5-rule-decision-v1", "draftId": draft["draftId"], "decision": "rejected", "decidedAt": _iso_time(self.clock()), "reason": str(reason).strip()}
        _atomic_json(self.decision_root / f"{draft['draftId']}.json", decision)
        return decision

    def rollback_version(self, target_version: str, *, reason: str, replay_evidence: dict[str, Any]) -> dict[str, Any]:
        if not str(reason or "").strip():
            raise ValueError("规则回滚必须填写原因。")
        self._validate_replay_evidence(replay_evidence)
        return self._activate(target_version=target_version, kind="rollback", reason=str(reason).strip(), replay_evidence=replay_evidence)

    def link_next_legal_run(
        self,
        *,
        run_id: str,
        rule_version: str,
        snapshots: list[dict[str, Any]],
    ) -> dict[str, Any]:
        selector = self._selector()
        activation_id = str(selector.get("activationId") or "")
        if selector["activeVersion"] != rule_version:
            raise ValueError("运行使用的规则版本与当前选择器不一致，不能关联。")
        version_path = self.version_root / f"{activation_id}.json"
        if not version_path.is_file():
            return {"status": "no_pending_c25_activation", "activationId": activation_id}
        existing = next(
            (row for row in self._all_json(self.run_link_root) if row.get("activationId") == activation_id),
            None,
        )
        if existing:
            return existing
        run_id = str(run_id or "").strip()
        snapshot_rows = [row for row in snapshots if isinstance(row, dict) and row.get("snapshotId")]
        if not run_id or not snapshot_rows:
            raise ValueError("规则版本生效关联必须包含真实运行ID和快照ID。")
        link = {
            "schemaVersion": "c2.5-rule-run-link-v1",
            "activationId": activation_id,
            "ruleVersion": rule_version,
            "linkedRunId": run_id,
            "linkedSnapshotIds": [str(row["snapshotId"]) for row in snapshot_rows],
            "snapshots": snapshot_rows,
            "linkedAt": _iso_time(self.clock()),
            "linkKind": "first_legal_snapshot_publication",
        }
        _atomic_json(self.run_link_root / f"{activation_id}--{Path(run_id).name}.json", link)
        return link


__all__ = ["RuleGovernanceStore"]
