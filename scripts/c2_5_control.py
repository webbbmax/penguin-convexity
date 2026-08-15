#!/usr/bin/env python3
"""Two-stage C2.5 controls that call existing Python functions directly."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from c2_5_control_plane import C25ControlPlane, load_js_payload
from c2_5_rule_governance import RuleGovernanceStore
from c2_5_rules import iso_time, utc_now


ALLOWED_INTERVALS = {1, 3, 6, 12, 24}
TOKEN_TTL = timedelta(minutes=5)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class ControlError(RuntimeError):
    def __init__(self, message: str, *, code: str = "invalid_request", status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_ledger(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value.setdefault("schemaVersion", "c2.5-control-ledger-v1")
    value.setdefault("confirmations", {})
    value.setdefault("requests", {})
    return value


def _scrub(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in ("token", "secret", "authorization", "api_key", "apikey", "password")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _scrub(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str) and len(value) > 2048:
        return value[:2048] + "…"
    return value


class C25ControlService:
    def __init__(
        self,
        plane: C25ControlPlane,
        *,
        runtime_root: Path | None = None,
        clock: Callable[[], datetime] = utc_now,
        adapters: dict[str, Callable[..., Any]] | None = None,
        rule_governance: RuleGovernanceStore | None = None,
    ) -> None:
        self.plane = plane
        self.runtime_root = Path(runtime_root or (plane.runtime_root / "c2.5")).resolve()
        self.clock = clock
        self.ledger_path = self.runtime_root / "control-ledger.json"
        self.audit_path = self.runtime_root / "management-audit.jsonl"
        self._lock = threading.Lock()
        self.adapters = adapters or self._default_adapters()
        project_root = Path(getattr(plane, "project_root", Path(__file__).resolve().parent.parent))
        self.rule_governance = rule_governance or RuleGovernanceStore(
            self.runtime_root / "rule-governance",
            rule_path=project_root / "docs" / "C2.4_RULE_CONFIG.json",
            trial_path=project_root / "docs" / "C2.4_RULE_RELAXATION_TRIAL_20260813.json",
            clock=clock,
        )

    @staticmethod
    def _default_adapters() -> dict[str, Callable[..., Any]]:
        from c2_2_runtime import launch_hidden, request_pause_current, update_config
        from c2_1_runtime import request_pause_current as request_screening_pipeline_pause
        from candidate_production_runtime import (
            launch_hidden as launch_candidate,
            request_pause as request_candidate_pause,
            resume_authorized_history,
            retry_partition,
        )
        from temp_artifact_retention import TempArtifactRetention

        return {
            "launch_job": launch_hidden,
            "pause_job": request_pause_current,
            "pause_screening_pipeline": request_screening_pipeline_pause,
            "update_job_config": update_config,
            "launch_candidate": launch_candidate,
            "pause_candidate": request_candidate_pause,
            "resume_history": resume_authorized_history,
            "retry_partition": retry_partition,
            "retention_sweep": lambda: TempArtifactRetention().sweep(min_interval_hours=24),
        }

    def _audit(self, payload: dict[str, Any]) -> str:
        audit_id = str(payload.get("auditId") or f"audit-{uuid.uuid4().hex}")
        row = {
            "schemaVersion": "c2.5-management-audit-v1",
            "auditId": audit_id,
            "actor": "local_manager",
            "origin": "desktop_control_plane",
            **payload,
            "auditId": audit_id,
        }
        safe = _scrub(row)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")
        return audit_id

    def _audit_rejected_call(self, phase: str, payload: Any, error: Exception) -> str:
        request = payload if isinstance(payload, dict) else {}
        token = str(request.get("confirmationToken") or "")
        return self._audit(
            {
                "requestedAt": iso_time(self.clock()),
                "requestId": str(request.get("requestId") or ""),
                "taskId": str(request.get("taskId") or ""),
                "action": str(request.get("action") or ""),
                "before": None,
                "afterRequested": request.get("parameters") or None,
                "impactPreview": None,
                "confirmationId": hashlib.sha256(token.encode("utf-8")).hexdigest()[:16] if token else None,
                "backendAccepted": False,
                "linkedRunId": None,
                "finalResult": f"{phase}_rejected",
                "rollbackOf": None,
                "errorCode": getattr(error, "code", "unexpected_error"),
                "error": f"{type(error).__name__}: {error}",
            }
        )

    def _request(self, payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ControlError("请求必须是JSON对象。")
        request_id = str(payload.get("requestId") or "").strip()
        task_id = str(payload.get("taskId") or "").strip()
        action = str(payload.get("action") or "").strip()
        parameters = payload.get("parameters") or {}
        if not REQUEST_ID_PATTERN.fullmatch(request_id):
            raise ControlError("requestId格式无效；必须使用8—128位稳定标识。")
        if not task_id or not action:
            raise ControlError("taskId和action不能为空。")
        if not isinstance(parameters, dict):
            raise ControlError("parameters必须是JSON对象。")
        return request_id, task_id, action, parameters

    def _registered_sources(self, task_id: str) -> set[str]:
        job_code = "screening" if task_id == "c22.screening" else "convexity_tracking" if task_id == "c22.convexity_tracking" else ""
        if not job_code:
            return set()
        try:
            snapshot = load_js_payload(self.plane.app_root / "c2-2-admin-snapshot.js")
        except (OSError, ValueError, json.JSONDecodeError):
            return set()
        result = set()
        for row in snapshot.get("sourceHealth") or []:
            affected = row.get("affectedJobs") or []
            if isinstance(affected, str):
                affected = [affected]
            if job_code in affected:
                source_id = row.get("source_id") or row.get("sourceId")
                if source_id:
                    result.add(str(source_id))
        return result

    def _audit_record(self, audit_id: str) -> dict[str, Any]:
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise ControlError("找不到可回滚的管理审计记录。", code="rollback_audit_missing", status_code=409) from error
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("auditId") == audit_id and row.get("auditStage") == "result":
                return row
        raise ControlError("目标审计不是已完成的可回滚操作。", code="rollback_audit_missing", status_code=409)

    def _validate_control_rollback(self, task_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        audit_id = str(parameters.get("auditId") or "").strip()
        target = self._audit_record(audit_id)
        if target.get("taskId") != task_id or not target.get("backendAccepted"):
            raise ControlError("只能回滚同一任务已被后端接受的操作。", code="rollback_scope_rejected", status_code=409)
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("auditStage") == "result" and row.get("backendAccepted") and row.get("rollbackOf") == audit_id:
                raise ControlError("该管理操作已经回滚，不能重复执行。", code="rollback_already_applied", status_code=409)
        original_action = str(target.get("action") or "")
        if original_action not in {"set_interval", "pause_future_cycles", "resume_future_cycles", "safe_pause", "cancel_pause_request"}:
            raise ControlError("该审计操作不支持自动回滚。", code="rollback_action_rejected", status_code=409)
        before_rows = target.get("before") or []
        before = before_rows[0] if isinstance(before_rows, list) and before_rows else {}
        schedule = before.get("schedule") if isinstance(before.get("schedule"), dict) else {}
        restore: dict[str, Any]
        if original_action == "set_interval":
            if schedule.get("intervalHours") is None:
                raise ControlError("目标审计没有可恢复的明确频率前值。", code="rollback_value_missing", status_code=409)
            restore = {"mode": schedule.get("mode") or "automatic", "intervalHours": int(schedule["intervalHours"])}
        elif original_action in {"pause_future_cycles", "resume_future_cycles"}:
            if "paused" not in schedule:
                raise ControlError("目标审计没有可恢复的明确暂停前值。", code="rollback_value_missing", status_code=409)
            restore = {"paused": bool(schedule["paused"])}
        else:
            if "pauseRequested" not in before:
                raise ControlError("目标审计没有可恢复的明确安全暂停请求前值。", code="rollback_value_missing", status_code=409)
            restore = {"pauseRequested": bool(before["pauseRequested"])}
        return {"auditId": audit_id, "originalAction": original_action, "restore": restore}

    def _rules_payload(self) -> dict[str, Any]:
        payload = self.plane.rules_payload()
        if payload.get("status") not in {"ready", "attention"}:
            raise ControlError("规则透明度当前不可用，不能执行规则治理。", code="rules_unavailable", status_code=409)
        return payload

    def _validate_rule_control(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        payload = self._rules_payload()
        state = self.rule_governance.state()
        known = {row["version"] for row in state["knownVersions"]}
        proposed: dict[str, Any] = {"action": action, "parameters": {}}
        if action == "rule_create_draft":
            target = str(parameters.get("targetVersion") or "").strip()
            replay_evidence = self.plane.rule_change_preview(target) if target in known else {}
            normalized = {
                "targetVersion": target,
                "reason": str(parameters.get("reason") or "").strip(),
                "scope": str(parameters.get("scope") or "").strip(),
                "endCondition": str(parameters.get("endCondition") or "").strip(),
                "replayEvidence": replay_evidence,
            }
            if target not in known or not all(normalized[key] for key in ("reason", "scope", "endCondition")):
                raise ControlError("规则草案必须选择已冻结版本，并填写原因、范围和结束条件。")
            if target == state["activeVersion"]:
                raise ControlError("规则草案必须指向不同于当前有效版本的已冻结版本。", code="rule_draft_target_rejected", status_code=409)
            proposed["parameters"] = normalized
        elif action in {"rule_approve_draft", "rule_reject_draft"}:
            draft_id = str(parameters.get("draftId") or "").strip()
            draft = next((row for row in state["drafts"] if row.get("draftId") == draft_id and row.get("status") == "pending_approval"), None)
            if draft is None:
                raise ControlError("规则草案不存在或已经完成审批。", code="rule_draft_unavailable", status_code=409)
            normalized = {"draftId": draft_id, "replayEvidence": draft.get("replayEvidence") or {}}
            if action == "rule_reject_draft":
                normalized["reason"] = str(parameters.get("reason") or "").strip()
                if not normalized["reason"]:
                    raise ControlError("拒绝规则草案必须填写原因。")
            proposed["parameters"] = normalized
        elif action == "rule_rollback_version":
            target = str(parameters.get("targetVersion") or "").strip()
            if target not in known or target == state["activeVersion"]:
                raise ControlError("必须选择一个明确且不同于当前有效版本的历史版本。", code="rule_rollback_target_rejected", status_code=409)
            reason = str(parameters.get("reason") or "").strip()
            if not reason:
                raise ControlError("规则回滚必须填写原因。")
            proposed["parameters"] = {
                "targetVersion": target,
                "reason": reason,
                "replayEvidence": self.plane.rule_change_preview(target),
                "rollbackOf": state.get("activeActivationId") or state["activeVersion"],
            }
        else:
            raise ControlError("规则治理操作未获准。", code="control_not_allowed", status_code=409)
        task = {
            "taskId": "rule.governance",
            "liveState": "waiting",
            "stateVersion": state["stateVersion"],
            "schedule": {"mode": "next_legal_run", "activeVersion": state["activeVersion"]},
            "checkpoint": None,
            "inputs": ["冻结规则", "活动试行", "同输入重放证据"],
            "outputs": ["不可变草案/版本记录", "原子当前版本选择器"],
            "affectedPages": ["rule-transparency.html", "下一次合法业务快照"],
        }
        return {"task": task, "proposed": proposed}

    def _validate_single(self, task_id: str, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if task_id == "rule.governance":
            return self._validate_rule_control(action, parameters)
        detail = self.plane.task_payload(task_id)
        if detail.get("status") != "ready":
            raise ControlError("没有找到这个任务。", code="task_not_found", status_code=404)
        task = detail["task"]
        control = next((item for item in task.get("controls", []) if item.get("action") == action), None)
        if control is None and action != "rollback_control_change":
            reason = next((item.get("reason") for item in task.get("disabledControls", []) if item.get("action") == action), None)
            raise ControlError(reason or "该任务没有获准执行此操作。", code="control_not_allowed", status_code=409)
        if task["liveState"] == "stale":
            raise ControlError("真实状态陈旧，必须先恢复状态一致性。", code="stale_state", status_code=409)
        proposed: dict[str, Any] = {"action": action, "parameters": parameters}
        if action == "rollback_control_change":
            proposed["parameters"] = self._validate_control_rollback(task_id, parameters)
        elif action == "set_interval":
            try:
                interval = int(parameters.get("intervalHours"))
            except (TypeError, ValueError) as error:
                raise ControlError("运行频率必须是1、3、6、12或24小时。") from error
            if interval not in ALLOWED_INTERVALS:
                raise ControlError("运行频率只能是1、3、6、12或24小时。")
            proposed["parameters"] = {"intervalHours": interval}
        elif action == "retry_registered_source":
            source_id = str(parameters.get("sourceId") or "").strip()
            registered = self._registered_sources(task_id) or set(task.get("sources") or [])
            if not source_id or source_id not in registered:
                raise ControlError("来源不属于该任务的权威登记范围。", code="source_scope_rejected", status_code=409)
            proposed["parameters"] = {"sourceId": source_id}
        elif action == "retry_partition":
            partition_id = str(parameters.get("partitionId") or "").strip()
            partition = next((row for row in task.get("partitions", []) if str(row.get("partition_id") or row.get("partitionId")) == partition_id), None)
            if not partition or partition.get("state") not in {"failed", "retrying", "paused"}:
                raise ControlError("分片不存在或当前状态不允许重试。", code="partition_scope_rejected", status_code=409)
            proposed["parameters"] = {"partitionId": partition_id}
        return {"task": task, "proposed": proposed}

    def _impact(self, task: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
        action = proposed["action"]
        after = proposed.get("parameters") or {}
        if action == "pause_future_cycles":
            after = {"paused": True}
        elif action == "resume_future_cycles":
            after = {"paused": False}
        elif action == "safe_pause":
            after = {"pauseRequested": True, "safePaused": False}
        elif action == "cancel_pause_request":
            after = {"pauseRequested": False}
        elif action == "rollback_control_change":
            after = {**(after.get("restore") or {}), "rollbackOf": after.get("auditId"), "originalAction": after.get("originalAction")}
        pause_requested = task["liveState"] in {"pause_requested", "safe_paused"}
        replay_evidence = after.get("replayEvidence") if action.startswith("rule_") else None
        return {
            "before": {"liveState": task["liveState"], "schedule": task["schedule"], "checkpoint": task["checkpoint"], "pauseRequested": pause_requested},
            "afterRequested": after,
            "readWriteObjects": {"reads": task["inputs"], "writes": task["outputs"]},
            "conflictTaskIds": ["c22.screening", "c22.convexity_tracking"] if proposed["action"] in {"run_now", "resume_checkpoint", "retry_registered_source"} else [],
            "frontendImpact": task["affectedPages"],
            "affectedAssetIds": (replay_evidence or {}).get("affectedAssetIds", []),
            "affectedTaskIds": (replay_evidence or {}).get("affectedTaskIds", []),
            "affectedSnapshots": (replay_evidence or {}).get("affectedSnapshots", []),
            "replayEvidence": replay_evidence,
            "recovery": "运行已启动后不能撤销事实；可在安全点暂停。" if proposed["action"] in {"run_now", "resume_checkpoint"} else "规则历史与原始证据不改写；回滚生成新版本记录并在下一次合法运行生效。" if action.startswith("rule_") else "按审计前值回滚并权威回读。",
        }

    def preview(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            status_code, response = self._preview(payload)
        except Exception as error:
            self._audit_rejected_call("preview", payload, error)
            raise
        if response.get("status") == "already_executed":
            self._audit(
                {
                    "requestedAt": iso_time(self.clock()),
                    "requestId": response.get("requestId"),
                    "taskId": str(payload.get("taskId") or ""),
                    "action": str(payload.get("action") or ""),
                    "before": None,
                    "afterRequested": payload.get("parameters") or None,
                    "impactPreview": None,
                    "confirmationId": None,
                    "backendAccepted": False,
                    "linkedRunId": None,
                    "finalResult": "preview_duplicate_after_execution",
                    "rollbackOf": None,
                }
            )
        return status_code, response

    def _current_state_version(self, task_id: str) -> str | None:
        if task_id == "rule.governance":
            return self.rule_governance.state()["stateVersion"]
        current = self.plane.task_payload(task_id)
        return current.get("task", {}).get("stateVersion") if current.get("status") == "ready" else None

    @staticmethod
    def _rollback_reference(items: list[dict[str, Any]]) -> Any:
        references = []
        for item in items:
            parameters = item.get("parameters") or {}
            if item.get("action") == "rollback_control_change" and parameters.get("auditId"):
                references.append(parameters["auditId"])
            elif item.get("action") == "rule_rollback_version" and parameters.get("rollbackOf"):
                references.append(parameters["rollbackOf"])
        if len(references) == 1:
            return references[0]
        return references or None

    def _preview(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request_id, task_id, action, parameters = self._request(payload)
        now = self.clock()
        with self._lock:
            ledger = load_ledger(self.ledger_path)
            previous = ledger["requests"].get(request_id)
            if previous and previous.get("status") in {"accepted", "failed", "executing_unknown"}:
                return 200, {"schemaVersion": "c2.5-control-preview-v1", "status": "already_executed", "requestId": request_id, "result": previous.get("result"), "confirmationToken": None}

            items = []
            if task_id == "batch" and action == "batch":
                requested_items = parameters.get("items")
                if not isinstance(requested_items, list) or not requested_items:
                    raise ControlError("批量操作必须逐项列出任务、操作和参数。")
                for position, item in enumerate(requested_items, 1):
                    if not isinstance(item, dict):
                        raise ControlError("批量操作明细格式无效。")
                    validated = self._validate_single(str(item.get("taskId") or ""), str(item.get("action") or ""), item.get("parameters") or {})
                    items.append({"order": position, **validated, "impact": self._impact(validated["task"], validated["proposed"])})
            else:
                validated = self._validate_single(task_id, action, parameters)
                items.append({"order": 1, **validated, "impact": self._impact(validated["task"], validated["proposed"])})

            token = secrets.token_urlsafe(32)
            token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            confirmation_id = token_digest[:16]
            expires_at = now + TOKEN_TTL
            confirmation = {
                "requestId": request_id,
                "confirmationId": confirmation_id,
                "tokenDigest": token_digest,
                "createdAt": iso_time(now),
                "expiresAt": iso_time(expires_at),
                "usedAt": None,
                "stateVersions": {item["task"]["taskId"]: item["task"]["stateVersion"] for item in items},
                "items": [
                    {"order": item["order"], "taskId": item["task"]["taskId"], "action": item["proposed"]["action"], "parameters": item["proposed"]["parameters"], "impact": item["impact"]}
                    for item in items
                ],
            }
            ledger["confirmations"][confirmation_id] = confirmation
            ledger["requests"][request_id] = {"status": "previewed", "confirmationId": confirmation_id, "updatedAt": iso_time(now)}
            atomic_json(self.ledger_path, ledger)
            audit_id = self._audit(
                {
                    "requestedAt": iso_time(now),
                    "requestId": request_id,
                    "taskId": task_id,
                    "action": action,
                    "before": [item["impact"]["before"] for item in items],
                    "afterRequested": [item["impact"]["afterRequested"] for item in items],
                    "impactPreview": [item["impact"] for item in items],
                    "confirmationId": confirmation_id,
                    "backendAccepted": False,
                    "linkedRunId": None,
                    "finalResult": "previewed",
                    "rollbackOf": self._rollback_reference(confirmation["items"]),
                }
            )
            return 200, {
                "schemaVersion": "c2.5-control-preview-v1",
                "status": "previewed",
                "requestId": request_id,
                "allowed": True,
                "currentState": [item["impact"]["before"] for item in items],
                "proposed": confirmation["items"],
                "impactPreview": [item["impact"] for item in items],
                "confirmationToken": token,
                "confirmationId": confirmation_id,
                "expiresAt": iso_time(expires_at),
                "auditId": audit_id,
            }

    def _set_pipeline_pause(self, job_code: str, requested: bool) -> dict[str, Any]:
        primary = self.adapters["pause_job"](requested, job_code)
        if job_code != "screening":
            return {"status": "pause_requested" if requested else "accepted", "pauseCurrentRequested": primary}
        try:
            associated = self.adapters["pause_screening_pipeline"](requested)
        except Exception as error:
            compensation_error = None
            try:
                self.adapters["pause_job"](not requested, job_code)
            except Exception as compensation:
                compensation_error = compensation
            detail = f"关联筛选流水线暂停失败：{type(error).__name__}: {error}"
            if compensation_error:
                detail += f"；主作业补偿也失败，真实状态需人工核验：{type(compensation_error).__name__}: {compensation_error}"
            raise ControlError(detail, code="associated_pause_failed", status_code=409) from error
        return {"status": "pause_requested" if requested else "accepted", "pauseCurrentRequested": primary, "associatedPauseRequested": associated}

    def _execute_rule_control(self, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if action == "rule_create_draft":
            draft = self.rule_governance.create_draft(
                target_version=parameters["targetVersion"],
                reason=parameters["reason"],
                scope=parameters["scope"],
                end_condition=parameters["endCondition"],
                replay_evidence=parameters["replayEvidence"],
            )
            return {"status": "accepted", "draftId": draft["draftId"]}
        if action == "rule_approve_draft":
            version = self.rule_governance.approve_draft(parameters["draftId"])
            return {"status": "accepted", "activationId": version["activationId"], "activeVersion": version["activeVersion"], "effectiveTiming": "next_legal_run"}
        if action == "rule_reject_draft":
            decision = self.rule_governance.reject_draft(parameters["draftId"], parameters["reason"])
            return {"status": "accepted", "draftId": decision["draftId"], "decision": "rejected"}
        if action == "rule_rollback_version":
            version = self.rule_governance.rollback_version(parameters["targetVersion"], reason=parameters["reason"], replay_evidence=parameters["replayEvidence"])
            return {"status": "accepted", "activationId": version["activationId"], "activeVersion": version["activeVersion"], "rollbackOfVersion": version["rollbackOfVersion"], "effectiveTiming": "next_legal_run"}
        raise ControlError("规则治理操作没有适配器。", code="adapter_missing", status_code=409)

    def _execute_single(self, item: dict[str, Any]) -> dict[str, Any]:
        task_id = item["taskId"]
        action = item["action"]
        parameters = item.get("parameters") or {}
        if task_id == "rule.governance":
            return self._execute_rule_control(action, parameters)
        if task_id in {"c22.screening", "c22.convexity_tracking"}:
            job_code = "screening" if task_id == "c22.screening" else "convexity_tracking"
            if action in {"run_now", "resume_checkpoint"}:
                return self.adapters["launch_job"](job_code, "manual")
            if action == "safe_pause":
                return self._set_pipeline_pause(job_code, True)
            if action == "cancel_pause_request":
                return self._set_pipeline_pause(job_code, False)
            if action == "pause_future_cycles":
                config = self.adapters["update_job_config"](job_code, {"paused": True})
                return {"status": "accepted", "config": config}
            if action == "resume_future_cycles":
                config = self.adapters["update_job_config"](job_code, {"paused": False})
                return {"status": "accepted", "config": config}
            if action == "set_interval":
                config = self.adapters["update_job_config"](job_code, {"mode": "automatic", "intervalHours": int(parameters["intervalHours"])})
                return {"status": "accepted", "config": config}
            if action == "retry_registered_source":
                return self.adapters["launch_job"](job_code, "source_retry", parameters["sourceId"])
            if action == "rollback_control_change":
                restore = parameters["restore"]
                original_action = parameters["originalAction"]
                if original_action == "set_interval":
                    config = self.adapters["update_job_config"](job_code, restore)
                    return {"status": "accepted", "config": config, "rollbackOf": parameters["auditId"]}
                if original_action in {"pause_future_cycles", "resume_future_cycles"}:
                    config = self.adapters["update_job_config"](job_code, {"paused": restore["paused"]})
                    return {"status": "accepted", "config": config, "rollbackOf": parameters["auditId"]}
                return {**self._set_pipeline_pause(job_code, restore["pauseRequested"]), "rollbackOf": parameters["auditId"]}
        if task_id in {"candidate.daily_incremental", "candidate.history_backlog"}:
            queue = "daily_incremental" if task_id == "candidate.daily_incremental" else "historical_backlog"
            if action == "resume_checkpoint":
                return self.adapters["launch_candidate"](queue) if queue == "daily_incremental" else self.adapters["resume_history"]()
            if action == "safe_pause":
                return {"status": "pause_requested", "pauseRequested": self.adapters["pause_candidate"](True)}
            if action == "cancel_pause_request":
                return {"status": "accepted", "pauseRequested": self.adapters["pause_candidate"](False)}
            if action == "retry_partition":
                return self.adapters["retry_partition"](parameters["partitionId"])
        if task_id == "maintenance.temp_artifact_retention" and action == "run_retention_sweep":
            return self.adapters["retention_sweep"]()
        raise ControlError("该操作没有直接函数适配器。", code="adapter_missing", status_code=409)

    def execute(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            status_code, response = self._execute(payload)
        except Exception as error:
            self._audit_rejected_call("execute", payload, error)
            raise
        if response.get("duplicate"):
            self._audit(
                {
                    "requestedAt": iso_time(self.clock()),
                    "requestId": response.get("requestId"),
                    "taskId": "",
                    "action": "duplicate_execute",
                    "before": None,
                    "afterRequested": None,
                    "impactPreview": None,
                    "confirmationId": None,
                    "backendAccepted": False,
                    "linkedRunId": None,
                    "finalResult": "execute_duplicate_no_write",
                    "rollbackOf": None,
                }
            )
        return status_code, response

    def _execute(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request_id = str(payload.get("requestId") or "").strip() if isinstance(payload, dict) else ""
        token = str(payload.get("confirmationToken") or "").strip() if isinstance(payload, dict) else ""
        if not REQUEST_ID_PATTERN.fullmatch(request_id) or not token:
            raise ControlError("执行请求缺少有效requestId或confirmationToken。")
        now = self.clock()
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        confirmation_id = token_digest[:16]
        audit_id = f"audit-{uuid.uuid4().hex}"
        with self._lock:
            ledger = load_ledger(self.ledger_path)
            previous = ledger["requests"].get(request_id)
            if previous and previous.get("status") in {"accepted", "failed", "executing_unknown"}:
                result = previous.get("result") or {"status": previous.get("status")}
                return 409, {
                    "schemaVersion": "c2.5-control-execute-v1",
                    "status": "duplicate_rejected",
                    "duplicate": True,
                    "backendAccepted": False,
                    "requestId": request_id,
                    "previousResult": result,
                }
            confirmation = ledger["confirmations"].get(confirmation_id)
            if not confirmation or confirmation.get("tokenDigest") != token_digest or confirmation.get("requestId") != request_id:
                raise ControlError("确认令牌无效。", code="invalid_confirmation", status_code=409)
            if confirmation.get("usedAt"):
                raise ControlError("确认令牌已经使用。", code="used_confirmation", status_code=409)
            expires_at = parse_time(confirmation.get("expiresAt"))
            if expires_at is None or now >= expires_at:
                raise ControlError("确认令牌已过期，请重新预览。", code="expired_confirmation", status_code=409)
            for task_id, expected_version in confirmation.get("stateVersions", {}).items():
                if self._current_state_version(task_id) != expected_version:
                    raise ControlError("预览后真实状态已改变，请重新预览。", code="state_drift", status_code=409)
            self._audit(
                {
                    "auditId": audit_id,
                    "auditStage": "intent",
                    "requestedAt": iso_time(now),
                    "requestId": request_id,
                    "taskId": confirmation.get("items", [{}])[0].get("taskId") if len(confirmation.get("items", [])) == 1 else "batch",
                    "action": confirmation.get("items", [{}])[0].get("action") if len(confirmation.get("items", [])) == 1 else "batch",
                    "before": [item.get("impact", {}).get("before") for item in confirmation.get("items", [])],
                    "afterRequested": [item.get("impact", {}).get("afterRequested") for item in confirmation.get("items", [])],
                    "impactPreview": [item.get("impact") for item in confirmation.get("items", [])],
                    "confirmationId": confirmation_id,
                    "backendAccepted": False,
                    "linkedRunId": None,
                    "finalResult": "execution_authorized_pending_backend",
                    "rollbackOf": self._rollback_reference(confirmation.get("items", [])),
                }
            )
            confirmation["usedAt"] = iso_time(now)
            ledger["requests"][request_id] = {"status": "executing_unknown", "confirmationId": confirmation_id, "updatedAt": iso_time(now), "result": {"schemaVersion": "c2.5-control-execute-v1", "status": "executing_unknown", "requestId": request_id}}
            atomic_json(self.ledger_path, ledger)

        results = []
        accepted = True
        error_message = ""
        try:
            for item in confirmation.get("items", []):
                backend = self._execute_single(item)
                backend_status = str((backend or {}).get("status") or "accepted")
                item_accepted = backend_status not in {"failed", "not_authorized", "already_running", "blocked", "program_failure"}
                accepted = accepted and item_accepted
                results.append({"order": item.get("order"), "taskId": item["taskId"], "action": item["action"], "backendStatus": backend_status, "runId": (backend or {}).get("runId"), "accepted": item_accepted, "backend": backend})
                if not item_accepted:
                    break
        except Exception as error:
            accepted = False
            error_message = f"{type(error).__name__}: {error}"

        after = []
        for item in confirmation.get("items", []):
            if item["taskId"] == "rule.governance":
                after.append(self.rule_governance.state())
            else:
                current = self.plane.task_payload(item["taskId"])
                after.append(current.get("task") if current.get("status") == "ready" else {"taskId": item["taskId"], "status": current.get("status")})
        response = {
            "schemaVersion": "c2.5-control-execute-v1",
            "status": "accepted" if accepted else "failed",
            "requestId": request_id,
            "auditId": audit_id,
            "backendAccepted": accepted,
            "results": results,
            "authoritativeReadback": after,
            "error": error_message or None,
        }
        try:
            self._audit(
                {
                    "auditId": audit_id,
                    "auditStage": "result",
                    "requestedAt": iso_time(now),
                    "requestId": request_id,
                    "taskId": confirmation.get("items", [{}])[0].get("taskId") if len(confirmation.get("items", [])) == 1 else "batch",
                    "action": confirmation.get("items", [{}])[0].get("action") if len(confirmation.get("items", [])) == 1 else "batch",
                    "before": [item.get("impact", {}).get("before") for item in confirmation.get("items", [])],
                    "afterRequested": [item.get("impact", {}).get("afterRequested") for item in confirmation.get("items", [])],
                    "impactPreview": [item.get("impact") for item in confirmation.get("items", [])],
                    "confirmationId": confirmation_id,
                    "backendAccepted": accepted,
                    "linkedRunId": next((row.get("runId") for row in results if row.get("runId")), None),
                    "finalResult": response["status"],
                    "rollbackOf": self._rollback_reference(confirmation.get("items", [])),
                    "error": error_message or None,
                }
            )
        except Exception as audit_error:
            response = {
                **response,
                "status": "executing_unknown",
                "auditComplete": False,
                "error": f"最终审计写入失败：{type(audit_error).__name__}: {audit_error}",
            }
            with self._lock:
                ledger = load_ledger(self.ledger_path)
                ledger["requests"][request_id] = {"status": "executing_unknown", "confirmationId": confirmation_id, "updatedAt": iso_time(self.clock()), "result": response}
                atomic_json(self.ledger_path, ledger)
            return 500, response
        with self._lock:
            ledger = load_ledger(self.ledger_path)
            ledger["requests"][request_id] = {"status": response["status"], "confirmationId": confirmation_id, "updatedAt": iso_time(self.clock()), "result": response}
            atomic_json(self.ledger_path, ledger)
        return (202 if accepted else 409), response


__all__ = ["C25ControlService", "ControlError"]
