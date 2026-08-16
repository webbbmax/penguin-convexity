#!/usr/bin/env python3
"""Versioned, hash-verified inputs for exact C2.4 rule replay."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from c2_4_rules import (
    EXPECTED_RULE_SHA256,
    EXPECTED_TRIAL_SHA256,
    FROZEN_PUBLIC_RULE_VERSION,
    TRIAL_PUBLIC_RULE_VERSION,
)


RULE_REPLAY_INPUT_SCHEMA_VERSION = "c2.4-rule-replay-inputs-v1"
RULE_REPLAY_INPUT_FIELDS = (
    "assetId",
    "chainId",
    "contractAddress",
    "pairAddress",
    "tokenSide",
    "t0Status",
    "ageDays",
    "relationshipClass",
    "deepTrackingState",
    "evaluationWindowId",
    "evaluationCompletedAt",
    "riskState",
    "riskSourceState",
    "projectEvidenceQualified",
    "projectEvidenceAttributable",
    "confirmedHardBlock",
    "confirmedFreeze",
    "confirmedBlacklist",
    "confirmedSellBlock",
    "confirmedSevereAnomaly",
    "severeAnomaly",
    "riskReasonCodes",
    "sellQuoteState",
    "sellQuoteLossPct",
    "sellQuoteIndependent",
    "sellTaxPct",
    "observedBuys",
    "observedSells",
    "volumeUsd",
    "transactionCount",
    "volumeLiquidityRatio",
    "liquidityUsd",
    "liquidityDropPct",
    "materialCrossSourceConflict",
    "crossSourcePriceDeviationPct",
    "supplyHistoryState",
    "supplyUnitScaleStable",
    "supplyUnitScaleChanged",
    "supplyDecimalsChanged",
    "supplyUnitChanged",
    "top10ShareChangePercentagePoints",
    "holderHhiChangePct",
    "supplyChangePct",
    "poolHistoryState",
    "indexedPoolCount",
    "ohlcvSuccessCount",
    "unindexedDiscoveredPoolCount",
    "relativeExpansion",
    "riskAdjustedSurplus",
    "cohortThresholds",
    "publicEligible",
    "strongPathEvaluationEligible",
)


class RuleReplayInputError(ValueError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_rule_replay_inputs(
    evaluator_input: dict[str, Any],
    *,
    active_rule_version: str,
) -> dict[str, Any]:
    if active_rule_version not in {FROZEN_PUBLIC_RULE_VERSION, TRIAL_PUBLIC_RULE_VERSION}:
        raise RuleReplayInputError("规则重放输入包含未知活动版本。", code="unknown_version")
    values = {field: evaluator_input.get(field) for field in RULE_REPLAY_INPUT_FIELDS}
    if values["strongPathEvaluationEligible"] is None:
        values["strongPathEvaluationEligible"] = values["deepTrackingState"] == "completed"
    content = {
        "schemaVersion": RULE_REPLAY_INPUT_SCHEMA_VERSION,
        "activeRuleVersion": active_rule_version,
        "ruleConfigSha256": EXPECTED_RULE_SHA256,
        "activeOverrideSha256": EXPECTED_TRIAL_SHA256 if active_rule_version == TRIAL_PUBLIC_RULE_VERSION else None,
        "inputSha256": _hash(values),
        "values": values,
    }
    return {**content, "contentSha256": _hash(content)}


def load_rule_replay_inputs(item: dict[str, Any], *, require: bool) -> dict[str, Any]:
    envelope = item.get("ruleReplayInputs")
    if not isinstance(envelope, dict):
        if require:
            raise RuleReplayInputError("当前跟踪快照缺少 ruleReplayInputs。", code="missing")
        return dict(item)
    if envelope.get("schemaVersion") != RULE_REPLAY_INPUT_SCHEMA_VERSION:
        raise RuleReplayInputError("ruleReplayInputs 版本不可识别。", code="invalid")
    version = envelope.get("activeRuleVersion")
    if version not in {FROZEN_PUBLIC_RULE_VERSION, TRIAL_PUBLIC_RULE_VERSION}:
        raise RuleReplayInputError("ruleReplayInputs 活动版本不可识别。", code="invalid")
    if envelope.get("ruleConfigSha256") != EXPECTED_RULE_SHA256:
        raise RuleReplayInputError("ruleReplayInputs 规则配置哈希不一致。", code="invalid")
    expected_override = EXPECTED_TRIAL_SHA256 if version == TRIAL_PUBLIC_RULE_VERSION else None
    if envelope.get("activeOverrideSha256") != expected_override:
        raise RuleReplayInputError("ruleReplayInputs 活动覆盖哈希不一致。", code="invalid")
    values = envelope.get("values")
    if not isinstance(values, dict) or set(values) != set(RULE_REPLAY_INPUT_FIELDS):
        raise RuleReplayInputError("ruleReplayInputs 字段集合不完整。", code="invalid")
    if envelope.get("inputSha256") != _hash(values):
        raise RuleReplayInputError("ruleReplayInputs 输入哈希不一致。", code="invalid")
    content = {key: envelope.get(key) for key in (
        "schemaVersion",
        "activeRuleVersion",
        "ruleConfigSha256",
        "activeOverrideSha256",
        "inputSha256",
        "values",
    )}
    if envelope.get("contentSha256") != _hash(content):
        raise RuleReplayInputError("ruleReplayInputs 内容哈希不一致。", code="invalid")
    asset_id = str(item.get("assetId") or item.get("asset_id") or "").strip()
    if asset_id and str(values.get("assetId") or "").strip() != asset_id:
        raise RuleReplayInputError("ruleReplayInputs 的 assetId 与快照对象不一致。", code="invalid")
    baseline = item.get("publicBaseline") if isinstance(item.get("publicBaseline"), dict) else {}
    if baseline.get("ruleVersion") and baseline.get("ruleVersion") != version:
        raise RuleReplayInputError("ruleReplayInputs 活动版本与已保存执行结果不一致。", code="invalid")
    return dict(values)


__all__ = [
    "RULE_REPLAY_INPUT_FIELDS",
    "RULE_REPLAY_INPUT_SCHEMA_VERSION",
    "RuleReplayInputError",
    "build_rule_replay_inputs",
    "load_rule_replay_inputs",
]
