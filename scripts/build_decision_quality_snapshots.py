#!/usr/bin/env python3
"""Build the C2.0 read-only decision and quality snapshots.

The builder deliberately treats the existing database and C1.x snapshots as
authoritative inputs.  It never writes business data.  The two C2.0 outputs
are validated with the same build identity and replaced together with a
rollback guard so a partial write cannot become the visible state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = ROOT / "app"
DEFAULT_DB_PATH = ROOT / "data" / "convexity.db"
FRONT_OUTPUT = APP_ROOT / "decision-signals-snapshot.js"
QUALITY_OUTPUT = APP_ROOT / "decision-quality-snapshot.js"
STATUS_OUTPUT = ROOT / "runtime" / "c2.0-snapshot-status.json"
FRONT_GLOBAL = "PENGUIN_CONVEXITY_DECISION_SIGNALS"
QUALITY_GLOBAL = "PENGUIN_CONVEXITY_DECISION_QUALITY"

READING_LABELS = {
    "must_read": "现在必须看",
    "worth_following": "值得继续看",
    "observe": "保持观察",
}
IMPACT_LABELS = {
    "improve": "可能改善",
    "tighten": "可能收紧",
    "no_change": "尚不改变判断",
}
ACTION_LABELS = {
    "普通建仓": "普通建仓",
    "极限试仓": "极限试仓",
    "只观察": "只观察",
    "反身性管理": "反身性管理",
    "已失去凸性": "失效/排除",
    "失效/排除": "失效/排除",
}
DIMENSION_ORDER = {
    "invalidation": 0,
    "risk": 1,
    "action": 2,
    "tradeability": 3,
    "exit": 4,
    "value_capture": 5,
    "remaining_convexity": 6,
    "ignition": 7,
    "evidence_maturity": 8,
    "market": 9,
}
DIMENSION_LABELS = {
    "action": "当前动作",
    "invalidation": "失效条件",
    "risk": "阻断风险",
    "tradeability": "交易性",
    "exit": "交易与退出",
    "value_capture": "价值捕获",
    "remaining_convexity": "剩余凸性",
    "ignition": "点火条件",
    "evidence_maturity": "证据成熟度",
    "market": "市场变化",
}
QUALITY_DIMENSIONS = [
    ("identity", "主体身份", "项目主体身份是否有明确、可溯源的答案"),
    ("asset", "可购买资产", "资产身份、网络和项目关系是否已核对"),
    ("value_capture", "价值捕获", "事实能否传导到可购买资产价值"),
    ("max_loss", "最大可控亏损", "最大损失边界是否具体且有依据"),
    ("trade_exit", "交易与退出", "交易、卖出路径和退出口径是否明确"),
    ("ignition", "点火条件", "点火事实、窗口和观察条件是否具体"),
    ("evidence_freshness", "证据时效", "摘要所用证据是否可回指且未明确过期"),
]
QUALITY_TARGETS = {
    "identity": "project-master-pool.html",
    "asset": "project-master-pool.html",
    "value_capture": "catalyst-paths.html",
    "max_loss": "action-gaps.html",
    "trade_exit": "monitoring-infrastructure.html",
    "ignition": "catalyst-paths.html",
    "evidence_freshness": "evidence-ledger.html",
}
ALLOWED_NUMERIC_UNITS = {
    "priceusd": "美元/资产",
    "marketcapusd": "美元",
    "fdvusd": "美元",
    "liquidityusd": "美元",
    "volume24husd": "美元",
    "exitnotionalusd": "美元",
    "pricechange24hpct": "%",
    "pricechange7dpct": "%",
    "estimatedexitslippagepct": "%",
    "modeledexitslippagepct": "%",
}
GENERIC_PHRASES = (
    "事实仍在积累",
    "风险与交易条件仍需继续确认",
    "代币价值捕获路径尚未核验",
    "最大风险：尚未确认",
    "资料待完善",
    "继续观察",
    "等待进一步发展",
    "本次为横向变化",
)
FORBIDDEN_FRONT_KEY = re.compile(
    r"(?:task|watcher|cursor|schedule|retry|log|prompt|credential|internal_path|file_path|next_task|run_id)",
    re.IGNORECASE,
)
SCIENTIFIC_TEXT = re.compile(r"(?<![A-Za-z0-9_.-])[-+]?\d+(?:\.\d+)?[eE][-+]?\d+(?![A-Za-z0-9_-])")
FRONT_CHANGE_IGNORED_FIELDS = {"modelactioncategory", "score", "mismatchscore"}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def timestamp(value: Any) -> float:
    parsed = parse_time(value)
    return parsed.timestamp() if parsed else 0.0


def text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    result = str(value).strip()
    return result if result else fallback


def replace_scientific(value: Any) -> str:
    raw = text(value)

    def convert(match: re.Match[str]) -> str:
        try:
            number = float(match.group(0))
        except ValueError:
            return match.group(0)
        if number == 0:
            return "0"
        return format(number, ".12f").rstrip("0").rstrip(".")

    return SCIENTIFIC_TEXT.sub(convert, raw)


def parse_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def load_js_payload(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    match = re.search(r"window\.[A-Z0-9_]+\s*=\s*(.*);\s*$", raw, re.DOTALL)
    if not match:
        raise ValueError(f"快照格式无法解析：{path}")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError(f"快照不是对象：{path}")
    return payload


def dict_row(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row else {}


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def normalize_value(value: Any) -> str:
    return re.sub(r"\s+", " ", replace_scientific(value).strip().lower()) or "(empty)"


def normalize_template(value: Any, names: list[str]) -> str:
    result = replace_scientific(value).lower()
    for name in sorted({text(item) for item in names if text(item)}, key=len, reverse=True):
        result = result.replace(name.lower(), "<project>")
    result = re.sub(r"https?://\S+", "<url>", result)
    result = re.sub(r"\d+(?:\.\d+)?", "<num>", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def is_generic(value: Any) -> bool:
    raw = replace_scientific(value)
    lowered = raw.lower()
    if not raw:
        return True
    return any(phrase.lower() in lowered for phrase in GENERIC_PHRASES)


def action_label(value: Any) -> str:
    raw = text(value, "只观察")
    return ACTION_LABELS.get(raw, raw if raw in ACTION_LABELS.values() else "只观察")


def field_dimension(field: Any) -> str:
    key = re.sub(r"[^a-z0-9_]", "", text(field).lower())
    mapping = {
        "action": "action",
        "actionlabel": "action",
        "actioncategory": "action",
        "stage": "action",
        "opportunitystage": "action",
        "invalidation": "invalidation",
        "invalidationwindow": "invalidation",
        "risk": "risk",
        "risklevel": "risk",
        "contractrisk": "risk",
        "tradeability": "tradeability",
        "tradeabilitystatus": "tradeability",
        "liquiditygrade": "tradeability",
        "sellpathstatus": "exit",
        "exit": "exit",
        "exitnotionalusd": "exit",
        "estimatedexitslippagepct": "exit",
        "modeledexitslippagepct": "exit",
        "valuecapture": "value_capture",
        "valuecapturegrade": "value_capture",
        "remainingconvexity": "remaining_convexity",
        "ignition": "ignition",
        "ignitionproximity": "ignition",
        "evidencematurity": "evidence_maturity",
        "evidencequality": "evidence_maturity",
        "maturity": "evidence_maturity",
    }
    if key in mapping:
        return mapping[key]
    if any(token in key for token in ("price", "marketcap", "fdv", "liquidity", "volume")):
        return "market"
    return "evidence_maturity"


def front_change_label(value: Any) -> str:
    raw = replace_scientific(value)
    return {
        "observe": "只观察",
        "研究观察": "只观察",
        "invalidated": "失效/排除",
        "失效与排除": "失效/排除",
        "已失去凸性": "失效/排除",
    }.get(raw, raw)


def front_change_explanation(dimension: str, old: Any, new: Any) -> str:
    label = DIMENSION_LABELS.get(dimension, "判断维度")
    if dimension == "market":
        return "市场指标已更新；精确值只在单位和比较基准确认后展示。"
    old_label = front_change_label(old) or "首次记录"
    new_label = front_change_label(new) or "当前状态"
    if old_label == new_label:
        return f"{label}本轮没有净变化。"
    return f"{label}由“{old_label}”变为“{new_label}”；这一变化不会单独替代当前动作。"


def front_evidence_text(value: Any) -> str:
    return (
        replace_scientific(value)
        .replace("read_only_verified", "已核验")
        .replace("formal_project_market_exit_enrichment", "市场与退出资料更新")
        .replace("contract_tradeability_check", "合约交易性检查")
        .replace("blocked", "阻断")
        .replace("unknown", "待核验")
    )


def classify_impact(direction: Any, field: Any, old: Any, new: Any) -> str:
    dimension = field_dimension(field)
    if dimension == "market":
        return "no_change"
    raw_direction = text(direction).lower()
    if raw_direction in {"upgrade", "improve", "positive", "up"}:
        return "improve"
    if raw_direction in {"downgrade", "tighten", "negative", "down"}:
        return "tighten"
    old_value = normalize_value(old)
    new_value = normalize_value(new)
    if old_value == new_value:
        return "no_change"
    bad = {"blocked", "untradeable", "high", "critical", "failed", "invalidated", "none", "unknown"}
    good = {"verified", "limited", "standard", "low", "immediate", "near", "a", "b", "c"}
    if new_value in bad and old_value not in bad:
        return "tighten"
    if new_value in good and old_value in {"unknown", "pending", "missing", "untradeable"}:
        return "improve"
    if dimension in {"risk", "invalidation", "exit"} and new_value in bad:
        return "tighten"
    return "no_change"


def numeric_display(field: Any, old: Any, new: Any, observed_at: Any) -> tuple[dict[str, Any] | None, bool]:
    key = re.sub(r"[^a-z0-9]", "", text(field).lower())
    if key == "liquiditygrade":
        return None, False
    unit = ALLOWED_NUMERIC_UNITS.get(key)
    if unit is None:
        suspicious = bool(re.search(r"price|usd|cap|fdv|liquid|volume|slippage|pct", key))
        return None, suspicious and (old not in (None, "") or new not in (None, ""))
    try:
        old_number = float(old)
        new_number = float(new)
    except (TypeError, ValueError):
        return None, True
    def fmt(number: float) -> str:
        if unit == "%":
            return f"{number:.2f}%"
        absolute = abs(number)
        if absolute >= 100_000_000:
            return f"{number / 100_000_000:.2f} 亿美元"
        if absolute >= 10_000:
            return f"{number / 10_000:.2f} 万美元"
        return f"{number:,.6f}".rstrip("0").rstrip(".") + (" 美元" if unit.startswith("美元") else "")
    return {
        "field": text(field),
        "fromText": fmt(old_number),
        "toText": fmt(new_number),
        "unit": unit,
        "comparison": "与上一轮检查相比",
        "observedAt": text(observed_at),
    }, False


def read_inputs(db_path: Path) -> dict[str, Any]:
    files = {
        "candidate": APP_ROOT / "candidate-pool-snapshot.js",
        "opportunity": APP_ROOT / "opportunity-center-snapshot.js",
        "changes": APP_ROOT / "change-explanations-snapshot.js",
        "details": APP_ROOT / "project-detail-snapshot.js",
        "tracking": APP_ROOT / "tracking-task-snapshot.js",
        "routes": APP_ROOT / "research-route-snapshot.js",
    }
    snapshots = {name: load_js_payload(path) for name, path in files.items()}
    connection = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "projects",
                "candidate_cases",
                "raw_events",
                "normalized_events_v2",
                "evidence_items",
                "evidence_lineage",
                "opportunity_stage_history",
                "tracking_task_runs",
            )
        }
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        projects = {
            row["project_id"]: dict_row(row)
            for row in connection.execute("SELECT * FROM projects")
        }
        evidence_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute("SELECT * FROM evidence_items ORDER BY observed_at DESC"):
            evidence_by_project[row["project_id"]].append(dict_row(row))
        lineage_by_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            "SELECT evidence_id, raw_event_id, source_url, run_id, source_id FROM evidence_lineage"
        ):
            lineage_by_evidence[row["evidence_id"]].append(dict_row(row))
        reviews: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            "SELECT * FROM convexity_reviews ORDER BY reviewed_at DESC"
        ):
            reviews.setdefault(row["case_id"], dict_row(row))
        conclusions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            "SELECT * FROM machine_conclusions ORDER BY generated_at DESC"
        ):
            conclusions[row["case_id"]].append(dict_row(row))
        tracking_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            "SELECT * FROM tracking_task_runs ORDER BY started_at DESC"
        ):
            tracking_runs[row["case_id"]].append(dict_row(row))
    finally:
        connection.close()
    return {
        "snapshots": snapshots,
        "counts": counts,
        "integrity": integrity,
        "foreignErrors": foreign_errors,
        "projects": projects,
        "evidenceByProject": evidence_by_project,
        "lineageByEvidence": lineage_by_evidence,
        "reviews": reviews,
        "conclusions": conclusions,
        "trackingRuns": tracking_runs,
    }


def detail_maps(details_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, record in (details_payload.get("records") or {}).items():
        if not isinstance(record, dict):
            continue
        project = record.get("project") or {}
        master = record.get("master") or {}
        project_id = text(project.get("project_id") or master.get("projectId"))
        if not project_id and text(key).startswith("project:"):
            project_id = text(key).removeprefix("project:")
        if project_id:
            result[project_id] = record
    return result


def select_case(cases: list[dict[str, Any]]) -> dict[str, Any]:
    published = [
        item
        for item in cases
        if text((item.get("machineConclusion") or {}).get("publication_status")).lower() == "published"
        or text((item.get("machineConclusion") or {}).get("publicationStatus")).lower() == "published"
    ]
    pool = published or cases
    latest_generated = max(timestamp((item.get("machineConclusion") or {}).get("generated_at")) for item in pool)
    pool = [item for item in pool if timestamp((item.get("machineConclusion") or {}).get("generated_at")) == latest_generated]
    latest_updated = max(timestamp(item.get("updatedAt") or item.get("updated_at")) for item in pool)
    pool = [item for item in pool if timestamp(item.get("updatedAt") or item.get("updated_at")) == latest_updated]
    return sorted(pool, key=lambda item: text(item.get("caseId") or item.get("case_id")))[0]


def evidence_for_project(
    project_id: str,
    source_ids: list[str],
    evidence_by_project: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = evidence_by_project.get(project_id, [])
    source_set = {text(item) for item in source_ids if text(item)}
    selected = [row for row in rows if not source_set or row.get("evidence_id") in source_set]
    return selected or rows


def choose_support(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    allowed = {"confirmed_fact", "high_confidence_inference"}
    candidates = [row for row in evidence if row.get("fact_boundary") in allowed and text(row.get("summary"))]
    candidates.sort(
        key=lambda row: (
            0 if row.get("fact_boundary") == "confirmed_fact" else 1,
            -timestamp(row.get("observed_at")),
            text(row.get("evidence_id")),
        )
    )
    if not candidates:
        return None
    row = candidates[0]
    return {
        "text": replace_scientific(row.get("summary")),
        "factBoundary": row.get("fact_boundary"),
        "evidenceIds": [row.get("evidence_id")],
        "observedAt": row.get("observed_at"),
        "sourceId": row.get("source_id") or row.get("evidence_id"),
        "sourceUrl": row.get("source_url") or "",
        "expiresAt": row.get("expires_at"),
    }


def blocker_category(value: Any) -> str:
    lowered = text(value).lower()
    if any(token in lowered for token in ("价值", "捕获", "传导")):
        return "价值捕获"
    if any(token in lowered for token in ("卖出", "退出", "滑点", "交易")):
        return "交易与退出"
    if any(token in lowered for token in ("最大", "亏损", "风险", "安全", "合约")):
        return "风险与最大亏损"
    if any(token in lowered for token in ("点火", "催化", "窗口")):
        return "点火条件"
    if any(token in lowered for token in ("主体", "资产", "身份", "合约")):
        return "身份与资产"
    return "证据完整度"


def pick_blocker(case: dict[str, Any], review: dict[str, Any], catalyst: dict[str, Any]) -> str:
    conclusion = case.get("machineConclusion") or {}
    options: list[str] = []
    options.extend(text(item) for item in parse_json(review.get("open_questions_json"), []) if text(item))
    options.extend(text(item) for item in (catalyst.get("blockers") or []) if text(item))
    options.extend(text(item) for item in (conclusion.get("invalidationConditions") or []) if text(item))
    options.append(text(conclusion.get("why_not_actionable")))
    options.append(text(case.get("currentThesis")))
    for candidate in options:
        if candidate and not is_generic(candidate):
            return replace_scientific(candidate)
    return replace_scientific(next((item for item in options if item), "资料不足，尚未形成可核验阻断原因"))


def pick_invalidation(case: dict[str, Any], review: dict[str, Any]) -> str:
    conclusion = case.get("machineConclusion") or {}
    options = [
        *[text(item) for item in (conclusion.get("invalidationConditions") or [])],
        text(case.get("invalidation")),
        text(case.get("invalidationWindow")),
        text(review.get("invalidation_window")),
    ]
    return replace_scientific(next((item for item in options if item), "项目身份、资产关系或退出路径出现冲突时，当前判断失效。"))


def build_events(
    changes_payload: dict[str, Any],
    case_to_project: dict[str, str],
    selected_by_project: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    dedup: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    raw_candidates = 0
    unit_issues: list[dict[str, Any]] = []
    history = changes_payload.get("history") or changes_payload.get("recent7d") or []
    for item in history:
        case_id = text(item.get("case_id") or item.get("caseId"))
        project_id = case_to_project.get(case_id)
        if not project_id or project_id not in selected_by_project:
            continue
        run_id = text(item.get("run_id") or item.get("runId"), "unknown-run")
        observed_at = text(item.get("observed_at") or item.get("observedAt"))
        evidence = []
        for entry in item.get("evidence") or []:
            if not isinstance(entry, dict):
                continue
            evidence.append({
                "changeId": text(entry.get("changeId")),
                "sourceName": replace_scientific(entry.get("sourceName")),
                "eventLabel": front_evidence_text(entry.get("eventLabel")),
                "category": replace_scientific(entry.get("category")),
                "summary": front_evidence_text(entry.get("summary")),
                "sourceUrl": text(entry.get("sourceUrl")),
                "collectedAt": text(entry.get("collectedAt")),
            })
        fields = item.get("changedFields") or []
        if not fields:
            fields = [{
                "field": "action",
                "label": "当前动作",
                "from": item.get("from_stage"),
                "to": item.get("to_stage"),
                "fromLabel": item.get("from_stage"),
                "toLabel": item.get("to_stage"),
                "direction": item.get("change_direction"),
            }]
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_key = re.sub(r"[^a-z0-9]", "", text(field.get("field")).lower())
            if field_key in FRONT_CHANGE_IGNORED_FIELDS:
                continue
            semantic = field_dimension(field.get("field"))
            old = field.get("fromLabel") if field.get("fromLabel") not in (None, "") else field.get("from")
            new = field.get("toLabel") if field.get("toLabel") not in (None, "") else field.get("to")
            old = front_change_label(old)
            new = front_change_label(new)
            impact = classify_impact(field.get("direction") or item.get("change_direction"), field.get("field"), old, new)
            normalized_old = normalize_value(field.get("from"))
            normalized_new = normalize_value(field.get("to"))
            key = (project_id, run_id, semantic, normalized_old, normalized_new)
            raw_candidates += 1
            number, unresolved = numeric_display(field.get("field"), field.get("from"), field.get("to"), observed_at)
            if unresolved:
                unit_issues.append({
                    "projectId": project_id,
                    "caseId": case_id,
                    "field": text(field.get("field")),
                    "observedAt": observed_at,
                    "reason": "单位或比较基准尚未确认，前台隐藏精确值。",
                })
            step = {
                "semanticField": semantic,
                "dimensionLabel": DIMENSION_LABELS.get(semantic, semantic),
                "from": replace_scientific(old),
                "to": replace_scientific(new),
                "fromLabel": old,
                "toLabel": new,
                "observedAt": observed_at,
                "impact": impact,
                "explanation": front_change_explanation(semantic, old, new),
                "evidence": evidence,
                "displayNumber": number,
                "hasUnresolvedUnit": unresolved,
            }
            existing = dedup.get(key)
            if existing:
                existing["evidence"] = merge_evidence(existing["evidence"], evidence)
                existing["hasUnresolvedUnit"] = existing["hasUnresolvedUnit"] or unresolved
                if number and number not in existing["displayNumbers"]:
                    existing["displayNumbers"].append(number)
            else:
                dedup[key] = {
                    "projectId": project_id,
                    "caseId": case_id,
                    "runId": run_id,
                    "projectName": text(item.get("projectName") or selected_by_project[project_id].get("projectName"), "项目"),
                    "observedAt": observed_at,
                    "explanation": front_change_explanation(semantic, old, new),
                    "fields": {semantic},
                    "steps": [step],
                    "evidence": evidence,
                    "displayNumbers": [number] if number else [],
                    "hasUnresolvedUnit": unresolved,
                }
    run_events: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for event in dedup.values():
        key = (event["projectId"], event["runId"])
        current = grouped.get(key)
        if current is None:
            current = {
                **event,
                "fields": set(event["fields"]),
                "steps": list(event["steps"]),
                "displayNumbers": list(event["displayNumbers"]),
            }
            grouped[key] = current
        else:
            current["fields"].update(event["fields"])
            current["steps"].extend(event["steps"])
            current["evidence"] = merge_evidence(current["evidence"], event["evidence"])
            current["displayNumbers"] = unique_list([*current["displayNumbers"], *event["displayNumbers"]])
            current["hasUnresolvedUnit"] = current["hasUnresolvedUnit"] or event["hasUnresolvedUnit"]
            current["explanation"] = current["explanation"] or event["explanation"]
    run_events.extend(grouped.values())
    run_events.sort(key=lambda event: (event["projectId"], timestamp(event["observedAt"])))
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in run_events:
        project_clusters = clusters[event["projectId"]]
        event_time = timestamp(event["observedAt"])
        matches = [
            cluster
            for cluster in project_clusters
            if cluster["runIds"] and (
                event["runId"] in cluster["runIds"]
                or (set(event["fields"]) & cluster["fields"] and event_time - timestamp(cluster["endedAt"]) <= 86400)
            )
        ]
        if not matches:
            project_clusters.append({
                "projectId": event["projectId"],
                "caseId": selected_by_project[event["projectId"]]["caseId"],
                "projectName": event["projectName"],
                "runIds": {event["runId"]},
                "fields": set(event["fields"]),
                "startedAt": event["observedAt"],
                "endedAt": event["observedAt"],
                "steps": list(event["steps"]),
                "evidence": list(event["evidence"]),
                "displayNumbers": list(event["displayNumbers"]),
                "hasUnresolvedUnit": event["hasUnresolvedUnit"],
                "explanation": event["explanation"],
            })
            continue
        target = matches[0]
        for extra in matches[1:]:
            target["runIds"].update(extra["runIds"])
            target["fields"].update(extra["fields"])
            target["steps"].extend(extra["steps"])
            target["evidence"] = merge_evidence(target["evidence"], extra["evidence"])
            target["displayNumbers"] = unique_list([*target["displayNumbers"], *extra["displayNumbers"]])
            target["startedAt"] = min(target["startedAt"], extra["startedAt"], key=timestamp)
            target["endedAt"] = max(target["endedAt"], extra["endedAt"], key=timestamp)
            target["hasUnresolvedUnit"] = target["hasUnresolvedUnit"] or extra["hasUnresolvedUnit"]
            project_clusters.remove(extra)
        target["runIds"].add(event["runId"])
        target["fields"].update(event["fields"])
        target["steps"].extend(event["steps"])
        target["evidence"] = merge_evidence(target["evidence"], event["evidence"])
        target["displayNumbers"] = unique_list([*target["displayNumbers"], *event["displayNumbers"]])
        target["startedAt"] = min(target["startedAt"], event["observedAt"], key=timestamp)
        target["endedAt"] = max(target["endedAt"], event["observedAt"], key=timestamp)
        target["hasUnresolvedUnit"] = target["hasUnresolvedUnit"] or event["hasUnresolvedUnit"]
        target["explanation"] = target["explanation"] or event["explanation"]
    chains: list[dict[str, Any]] = []
    for project_clusters in clusters.values():
        for cluster in project_clusters:
            steps = sorted(cluster["steps"], key=lambda step: timestamp(step.get("observedAt")))
            unique_steps = []
            seen_steps = set()
            for step in steps:
                fingerprint = (step.get("semanticField"), step.get("from"), step.get("to"), step.get("observedAt"))
                if fingerprint in seen_steps:
                    continue
                seen_steps.add(fingerprint)
                unique_steps.append(step)
            impacts = [step.get("impact") for step in unique_steps]
            impact = "tighten" if "tighten" in impacts else "improve" if "improve" in impacts else "no_change"
            initial: dict[str, Any] = {}
            final: dict[str, Any] = {}
            net: dict[str, Any] = {}
            for field in sorted(cluster["fields"], key=lambda value: DIMENSION_ORDER.get(value, 99)):
                field_steps = [step for step in unique_steps if step.get("semanticField") == field]
                if not field_steps:
                    continue
                initial[field] = field_steps[0].get("fromLabel") or field_steps[0].get("from")
                final[field] = field_steps[-1].get("toLabel") or field_steps[-1].get("to")
                net[field] = {"from": initial[field], "to": final[field]}
            headline_steps = sorted(
                unique_steps,
                key=lambda step: (
                    DIMENSION_ORDER.get(step.get("semanticField"), 99),
                    timestamp(step.get("observedAt")),
                ),
            )
            transitions = []
            for step in headline_steps:
                display_number = step.get("displayNumber") or {}
                if display_number:
                    transitions.append(
                        f"{step.get('dimensionLabel')}: {display_number.get('fromText')} → {display_number.get('toText')}"
                    )
                elif step.get("hasUnresolvedUnit"):
                    transitions.append(f"{step.get('dimensionLabel')}: 口径尚未确认")
                else:
                    transitions.append(
                        f"{step.get('dimensionLabel')}: {step.get('fromLabel') or step.get('from') or '首次记录'} → {step.get('toLabel') or step.get('to') or '当前'}"
                    )
            project = selected_by_project[cluster["projectId"]]
            headline = "；".join(transitions[:3]) or cluster["explanation"] or "项目判断出现新记录。"
            headline = replace_scientific(headline)
            if impact == "no_change":
                why = f"{headline}。当前交易与退出判断和动作没有变化，因此暂不改变判断。"
            elif impact == "improve":
                why = f"{headline}。这可能改善相关判断，但尚不自动改变当前动作。"
            else:
                why = f"{headline}。这可能收紧相关判断，请先核对风险、退出或失效条件。"
            chain = {
                "chainId": "change-chain-" + stable_hash({"projectId": cluster["projectId"], "fields": sorted(cluster["fields"]), "startedAt": cluster["startedAt"], "endedAt": cluster["endedAt"], "steps": unique_steps}),
                "projectId": cluster["projectId"],
                "caseId": project["caseId"],
                "projectName": cluster["projectName"],
                "startedAt": cluster["startedAt"],
                "endedAt": cluster["endedAt"],
                "impact": impact,
                "impactLabel": IMPACT_LABELS[impact],
                "headline": headline,
                "whyItMatters": replace_scientific(why),
                "initialState": initial,
                "finalState": final,
                "netChange": net,
                "dimensions": sorted(cluster["fields"], key=lambda value: DIMENSION_ORDER.get(value, 99)),
                "steps": [
                    {
                        "semanticField": step["semanticField"],
                        "dimensionLabel": step["dimensionLabel"],
                        "from": step["from"],
                        "to": step["to"],
                        "fromLabel": step["fromLabel"],
                        "toLabel": step["toLabel"],
                        "observedAt": step["observedAt"],
                        "impact": step["impact"],
                        "explanation": step["explanation"],
                    }
                    for step in unique_steps
                ],
                "evidence": merge_evidence(cluster["evidence"], []),
                "displayNumbers": cluster["displayNumbers"],
                "hasUnresolvedUnit": cluster["hasUnresolvedUnit"],
                "detailUrl": text(project.get("detailUrl")),
            }
            chains.append(chain)
    chains.sort(key=lambda item: (-timestamp(item["endedAt"]), text(item["projectName"])))
    return chains, list(dedup.values()), unique_list(unit_issues)


def merge_evidence(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for entry in [*(left or []), *(right or [])]:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("changeId"), entry.get("sourceUrl"), entry.get("summary"), entry.get("evidenceId"))
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def unique_list(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def build_summary_record(
    case: dict[str, Any],
    detail: dict[str, Any],
    project_row: dict[str, Any],
    review_row: dict[str, Any],
    tracking_item: dict[str, Any],
    tracking_run: dict[str, Any],
    evidence_by_project: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    project_id = text(case.get("projectId") or project_row.get("project_id"))
    case_id = text(case.get("caseId"))
    conclusion = case.get("machineConclusion") or {}
    catalyst = case.get("catalystTradePath") or {}
    project_name = text(case.get("projectName") or project_row.get("canonical_name"), "未命名项目")
    symbol = text(case.get("symbol"))
    source_ids = list(conclusion.get("sourceEvidenceIds") or [])
    evidences = evidence_for_project(project_id, source_ids, evidence_by_project)
    support = choose_support(evidences)
    blocker = pick_blocker(case, review_row, catalyst)
    blocker = replace_scientific(blocker)
    next_text = replace_scientific(
        tracking_item.get("nextStep")
        or tracking_item.get("next_step")
        or conclusion.get("next_step")
        or case.get("ignitionConditions")
        or "系统将继续检查下一项可核验事实。"
    )
    invalidation = pick_invalidation(case, review_row)
    action = action_label(conclusion.get("action_label") or case.get("sourceAction") or case.get("normalizedAction"))
    next_review = text(tracking_item.get("nextReviewAt") or tracking_item.get("next_review_at") or case.get("nextReviewAt"))
    support_time = text(support.get("observedAt") if support else case.get("sourceSnapshotAt"))
    stale = bool(support and support.get("expiresAt") and timestamp(support.get("expiresAt")) < timestamp(iso_now()))
    owner = "human" if (tracking_item.get("decisionReview") or {}).get("required") else "system"
    summary = {
        "action": {"label": action, "sourceId": text(conclusion.get("machine_conclusion_id") or case_id)},
        "strongestSupport": support,
        "primaryBlocker": {
            "category": blocker_category(blocker),
            "text": blocker,
            "effect": "当前动作仍受该缺口限制。",
            "evidenceIds": [row.get("evidence_id") for row in evidences[:3] if row.get("evidence_id")],
        },
        "nextTrigger": {
            "text": next_text,
            "owner": owner,
            "nextReviewAt": next_review,
            "evidenceIds": source_ids[:5],
        },
        "invalidation": {
            "text": invalidation,
            "evidenceIds": source_ids[:5],
            "evidenceTime": support_time,
            "stale": stale,
        },
    }
    missing: list[str] = []
    if not support:
        missing.append("当前最强支持事实")
    if not blocker or is_generic(blocker):
        missing.append("当前最大阻断或最大风险")
    if not next_text or is_generic(next_text):
        missing.append("下一触发条件")
    if not invalidation or stale:
        missing.append("失效条件与证据时间")
    if not support_time:
        missing.append("证据时间")
    latest_run = text(tracking_run.get("run_id") or (tracking_item.get("latestExecution") or {}).get("runId"))
    return {
        "projectId": project_id,
        "caseId": case_id,
        "projectName": project_name,
        "symbol": symbol,
        "detailUrl": text(case.get("detailUrl"), f"project-detail.html?id=project%3A{project_id}"),
        "actionLabel": action,
        "maturity": text(case.get("maturity")),
        "riskLevel": text(case.get("riskLevel")),
        "remainingConvexity": text(case.get("remainingConvexity")),
        "tradeabilityStatus": text(case.get("tradeabilityStatus")),
        "ignitionProximity": text(case.get("ignitionProximity")),
        "convexitySource": text(case.get("convexitySource")),
        "projectIdentityStatus": text(case.get("projectIdentityStatus") or project_row.get("identity_status")),
        "assetIdentityStatus": text(case.get("assetIdentityStatus")),
        "assetMapped": bool(case.get("assetMapped")),
        "sellPathStatus": text(case.get("sellPathStatus")),
        "valueCaptureGrade": text(case.get("valueCaptureGrade")),
        "maximumControllableLoss": replace_scientific(case.get("maximumControllableLoss")),
        "ignitionConditions": replace_scientific(case.get("ignitionConditions")),
        "summary": summary,
        "summaryComplete": not missing,
        "missingSummaryParts": missing,
        "evidenceTime": support_time,
        "nextReviewAt": next_review,
        "readingTier": "observe",
        "readingTierLabel": READING_LABELS["observe"],
        "impact": "",
        "impactLabel": "",
        "whyPriority": "",
        "sortReasons": [],
        "sourceIds": source_ids,
        "evidenceIds": [row.get("evidence_id") for row in evidences if row.get("evidence_id")],
        "sourceUrls": unique_list([row.get("source_url") for row in evidences if row.get("source_url")]),
        "latestRunId": latest_run,
        "stale": stale,
        "tracking": tracking_item,
    }


def assign_priority(records: list[dict[str, Any]], chains: list[dict[str, Any]]) -> None:
    chains_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chain in chains:
        chains_by_project[chain["projectId"]].append(chain)
    for record in records:
        project_chains = sorted(chains_by_project.get(record["projectId"], []), key=lambda item: timestamp(item["endedAt"]), reverse=True)
        latest = project_chains[0] if project_chains else None
        if latest:
            record["impact"] = latest["impact"]
            record["impactLabel"] = latest["impactLabel"]
        action = record["actionLabel"]
        material_dimension = latest and any(
            dimension in {"action", "invalidation", "risk", "tradeability", "exit"}
            for dimension in latest.get("dimensions", [])
        )
        if action in {"普通建仓", "极限试仓"} or (latest and latest["impact"] == "tighten" and material_dimension):
            tier = "must_read"
        elif record["summaryComplete"] and record["summary"].get("strongestSupport"):
            tier = "worth_following"
        else:
            tier = "observe"
        record["readingTier"] = tier
        record["readingTierLabel"] = READING_LABELS[tier]
        reasons: list[str] = []
        if action in {"普通建仓", "极限试仓"}:
            reasons.append(f"当前动作是{action}")
        if latest and latest["impact"] in {"tighten", "improve"}:
            dimensions = "、".join(DIMENSION_LABELS.get(item, item) for item in latest.get("dimensions", [])[:2])
            reasons.append(f"最近变化{latest['impactLabel']}{dimensions or '相关判断'}")
        if record["summary"].get("strongestSupport"):
            boundary = record["summary"]["strongestSupport"].get("factBoundary")
            reasons.append("有可回溯的已确认事实" if boundary == "confirmed_fact" else "有可回溯的高置信推断")
        if record["summary"].get("nextTrigger", {}).get("text"):
            reasons.append("下一触发条件已经具体")
        record["sortReasons"] = reasons[:3]
        if latest:
            record["whyPriority"] = replace_scientific(
                latest["headline"] if latest["impact"] != "no_change" else (
                    f"{record['projectName']}有可回溯事实，但当前动作和交易与退出判断暂未变化。"
                )
            )
        elif record["summary"].get("strongestSupport"):
            record["whyPriority"] = replace_scientific(record["summary"]["strongestSupport"]["text"])
        else:
            record["whyPriority"] = f"{record['projectName']}当前保留在全部机会目录，资料仍需继续核验。"


def apply_template_rules(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [record["projectName"] for record in records]
    counters: Counter[str] = Counter()
    normalized_parts: dict[tuple[str, str], str] = {}
    for record in records:
        parts = {
            "whyPriority": record["whyPriority"],
            "strongestSupport": (record["summary"].get("strongestSupport") or {}).get("text", ""),
            "primaryBlocker": record["summary"].get("primaryBlocker", {}).get("text", ""),
            "nextTrigger": record["summary"].get("nextTrigger", {}).get("text", ""),
        }
        for part, value in parts.items():
            normalized = normalize_template(value, names)
            normalized_parts[(record["projectId"], part)] = normalized
            counters[normalized] += 1
    issues: list[dict[str, Any]] = []
    for record in records:
        template_parts: list[str] = []
        for part in ("whyPriority", "strongestSupport", "primaryBlocker", "nextTrigger"):
            normalized = normalized_parts[(record["projectId"], part)]
            if not normalized:
                continue
            if counters[normalized] >= 3 or any(phrase.lower() in normalized for phrase in GENERIC_PHRASES):
                template_parts.append(part)
                issues.append({
                    "projectId": record["projectId"],
                    "caseId": record["caseId"],
                    "part": part,
                    "text": record["whyPriority"] if part == "whyPriority" else ((record["summary"].get(part) or {}).get("text", "")),
                    "reason": "规范化后与至少三个项目重复或只有状态模板。",
                })
        record["templateParts"] = template_parts
        if any(part in template_parts for part in ("strongestSupport", "primaryBlocker", "nextTrigger")):
            record["summaryComplete"] = False
            for part, label in (
                ("strongestSupport", "当前最强支持事实"),
                ("primaryBlocker", "当前最大阻断或最大风险"),
                ("nextTrigger", "下一触发条件"),
            ):
                if part in template_parts and label not in record["missingSummaryParts"]:
                    record["missingSummaryParts"].append(label)
    return issues


def quality_status(record: dict[str, Any], dimension: str) -> tuple[str, str, str]:
    tracking = record.get("tracking") or {}
    task_type = text(tracking.get("taskType") or tracking.get("task_type"))
    decision_review = tracking.get("decisionReview") or {}
    requires_human = bool(decision_review.get("required"))
    if dimension == "identity":
        if text(record.get("projectIdentityStatus")) in {"conflict", "rejected"}:
            return "human_pending", "主体身份存在冲突，需要人工确认。", "请在现有后台处理入口核对身份与证据。"
        support = record.get("summary", {}).get("strongestSupport") or {}
        closed = text(record.get("projectIdentityStatus")) == "verified" and bool(record.get("evidenceIds")) and text(support.get("factBoundary")) in {"confirmed_fact", "high_confidence_inference"}
    elif dimension == "asset":
        if text(record.get("assetIdentityStatus")) in {"conflict", "rejected"}:
            return "human_pending", "可购买资产身份存在冲突，需要人工确认。", "请在现有后台处理入口核对资产关系。"
        support = record.get("summary", {}).get("strongestSupport") or {}
        closed = bool(record.get("assetMapped")) and text(record.get("assetIdentityStatus")) in {"verified", "corroborated"} and text(support.get("text"))
    elif dimension == "value_capture":
        value = text(record.get("valueCaptureGrade"))
        closed = value.upper() in {"A", "B", "C"}
    elif dimension == "max_loss":
        value = text(record.get("maximumControllableLoss"))
        closed = bool(value and not is_generic(value) and "尚未" not in value and "无法" not in value)
    elif dimension == "trade_exit":
        closed = text(record.get("sellPathStatus")) in {"verified", "limited", "blocked", "read_only_verified"} and text(record.get("tradeabilityStatus")) in {"verified", "limited", "blocked", "standard", "untradeable"}
    elif dimension == "ignition":
        value = text(record.get("ignitionConditions"))
        closed = bool(
            value
            and not is_generic(value)
            and "同时补齐基础档案" not in value
            and "尚未形成" not in value
            and ("条件" in value or "检查" in value or "核验" in value)
        )
    else:
        closed = bool(record.get("evidenceTime") and not record.get("stale") and record.get("evidenceIds"))
    if closed:
        return "closed", "答案明确且可回溯。", "当前无需人工操作。"
    if requires_human:
        return "human_pending", "当前缺口存在冲突或需要人工判断。", "请在现有后台处理入口核对证据后再回到队列。"
    if task_type or text(record.get("nextReviewAt")):
        return "system_pending", "当前缺口已有系统复查路径。", "系统继续补齐并在下次复查时更新。"
    if record.get("templateParts"):
        return "human_pending", "当前缺口存在冲突或需要人工判断。", "请在现有后台处理入口核对证据后再回到队列。"
    return "human_pending", "当前缺口尚未形成安全的自动处理路径。", "请先确认是否存在可用的现有处理入口。"


def build_quality(
    records: list[dict[str, Any]],
    chains: list[dict[str, Any]],
    premerge_event_count: int,
    premerge_numeric_count: int,
    template_issues: list[dict[str, Any]],
    unit_issues: list[dict[str, Any]],
    lineage_by_evidence: dict[str, list[dict[str, Any]]],
    input_run_ids: list[str],
    source_snapshot_at: str,
    generated_at: str,
    db_counts: dict[str, int],
    case_count: int,
) -> dict[str, Any]:
    funnel: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    blocker_groups: dict[str, dict[str, Any]] = {}
    for dimension, label, definition in QUALITY_DIMENSIONS:
        counts = Counter()
        for record in records:
            state, reason, next_step = quality_status(record, dimension)
            counts[state] += 1
            if state != "closed":
                tracking = record.get("tracking") or {}
                if dimension in {"identity", "asset", "value_capture"}:
                    target = f"screening-console.html?case={record['caseId']}"
                elif dimension == "trade_exit":
                    target = f"monitoring-infrastructure.html?project={record['projectId']}"
                else:
                    target = f"manual-review.html?target=project%3A{record['projectId']}&queue=all"
                issue = {
                    "issueId": f"quality-{stable_hash([record['projectId'], dimension])}",
                    "projectId": record["projectId"],
                    "caseId": record["caseId"],
                    "projectName": record["projectName"],
                    "dimension": dimension,
                    "dimensionLabel": label,
                    "category": record.get("summary", {}).get("primaryBlocker", {}).get("category", label),
                    "frontImpact": "影响前台摘要完整度和阅读优先级。",
                    "owner": "human" if state == "human_pending" else "system",
                    "ownerLabel": "待人工确认" if state == "human_pending" else "待系统处理",
                    "nextStep": next_step,
                    "latestExecutionAt": text(tracking.get("latestEvidenceAt") or (tracking.get("latestExecution") or {}).get("finishedAt")),
                    "latestResult": text((tracking.get("latestExecution") or {}).get("result") or tracking.get("statusLabel"), "尚无本轮结果"),
                    "nextReviewAt": text(record.get("nextReviewAt")),
                    "reason": reason,
                    "targetUrl": target,
                    "returnContext": {"qualityFilter": dimension, "qualityPage": 1, "qualityScroll": 0, "issueId": f"quality-{stable_hash([record['projectId'], dimension])}"},
                    "taskId": text(tracking.get("taskId")),
                    "lastRunId": text(record.get("latestRunId")),
                }
                queue.append(issue)
                group_key = f"{dimension}:{issue['category']}"
                group = blocker_groups.setdefault(group_key, {
                    "blockerId": "blocker-" + stable_hash(group_key),
                    "name": issue["category"],
                    "dimension": dimension,
                    "dimensionLabel": label,
                    "frontImpact": issue["frontImpact"],
                    "projectIds": set(),
                    "frontConclusionCount": 0,
                    "owner": issue["owner"],
                    "ownerLabel": issue["ownerLabel"],
                    "nextStep": issue["nextStep"],
                    "latestExecutionAt": issue["latestExecutionAt"],
                    "nextReviewAt": issue["nextReviewAt"],
                    "statusLabel": issue["ownerLabel"],
                    "reason": issue["reason"],
                    "targetUrl": QUALITY_TARGETS[dimension],
                })
                group["projectIds"].add(record["projectId"])
                group["frontConclusionCount"] += 1 if not record["summaryComplete"] else 0
                if timestamp(issue["latestExecutionAt"]) > timestamp(group.get("latestExecutionAt")):
                    group["latestExecutionAt"] = issue["latestExecutionAt"]
                issue_review_at = timestamp(issue["nextReviewAt"])
                group_review_at = timestamp(group.get("nextReviewAt"))
                if issue_review_at and (not group_review_at or issue_review_at < group_review_at):
                    group["nextReviewAt"] = issue["nextReviewAt"]
                if issue["owner"] == "human":
                    group["owner"] = "human"
                    group["ownerLabel"] = "待人工确认"
                    group["statusLabel"] = "待人工确认"
        funnel.append({
            "dimension": dimension,
            "label": label,
            "definition": definition,
            "closed": counts["closed"],
            "systemPending": counts["system_pending"],
            "humanPending": counts["human_pending"],
            "total": len(records),
            "reconciled": sum(counts.values()) == len(records),
        })
    for group in blocker_groups.values():
        group["projectCount"] = len(group.pop("projectIds"))
        group["impactRank"] = 0 if group["dimension"] in {"invalidation", "risk", "trade_exit", "max_loss"} else 1
    blockers = sorted(
        blocker_groups.values(),
        key=lambda item: (
            item["impactRank"],
            -item["projectCount"],
            timestamp(item.get("nextReviewAt")) or float("inf"),
            text(item["name"]),
        ),
    )
    for group in blockers:
        group.pop("impactRank", None)
    total_projects = len(records)
    complete = sum(1 for record in records if record["summaryComplete"])
    template_projects = len({issue["projectId"] for issue in template_issues})
    stale_projects = sum(1 for record in records if record.get("stale"))
    traceable_complete = sum(
        1
        for record in records
        if record["summaryComplete"] and record.get("evidenceIds") and record["summary"].get("action", {}).get("sourceId")
    )
    numeric_total = len(unit_issues) + premerge_numeric_count
    merged_candidate_count = max(len(chains), premerge_event_count)
    metric_specs = [
        ("summary_coverage", "项目专属摘要覆盖率", complete, total_projects, "summaryComplete=true 的唯一项目 / 当前唯一项目总数"),
        ("template_ratio", "通用模板占比", template_projects, total_projects, "任一核心段被判为通用模板的唯一项目 / 当前唯一项目总数"),
        ("merge_rate", "变化合并率", max(0, merged_candidate_count - len(chains)), merged_candidate_count, "合并前显示候选数 - 合并后事件链数 / 合并前显示候选数"),
        ("missing_unit_count", "前台缺少单位数量", len(unit_issues), numeric_total, "被隐藏的单位或比较基准不明数值 / 合并前数值变化总数"),
        ("stale_evidence_projects", "过期证据影响项目数", stale_projects, total_projects, "摘要所用证据明确过期的唯一项目 / 当前唯一项目总数"),
        ("home_signals", "首页有效信号数量", sum(1 for record in records if record.get("homeEligible")), 5, "实际首页项目数 / 固定上限 5"),
        ("traceability", "前台摘要可溯源率", traceable_complete, complete, "五段全部可回指的完整摘要项目 / 完整摘要项目数"),
    ]
    metrics = []
    for metric_id, label, numerator, denominator, definition in metric_specs:
        value = round(numerator / denominator, 4) if denominator else None
        metrics.append({"id": metric_id, "label": label, "numerator": numerator, "denominator": denominator, "value": value, "definition": definition, "generatedAt": generated_at})
    traces = {}
    for record in records:
        lineages = [
            lineage
            for evidence_id in record.get("evidenceIds", [])
            for lineage in lineage_by_evidence.get(evidence_id, [])
        ]
        traces[record["projectId"]] = {
            "projectId": record["projectId"],
            "caseId": record["caseId"],
            "detailUrl": record["detailUrl"],
            "machineConclusionId": record["summary"]["action"].get("sourceId"),
            "evidenceIds": record.get("evidenceIds", []),
            "sourceIds": record.get("sourceIds", []),
            "sourceUrls": record.get("sourceUrls", []),
            "rawEventIds": unique_list([item.get("raw_event_id") for item in lineages if item.get("raw_event_id")]),
            "lineageSourceIds": unique_list([item.get("source_id") for item in lineages if item.get("source_id")]),
            "runIds": unique_list([
                item
                for item in [record.get("latestRunId"), *[lineage.get("run_id") for lineage in lineages], *input_run_ids]
                if item
            ]),
        }
    return {
        "schemaVersion": "c2.0-decision-quality-v1",
        "buildId": "c2.0-" + uuid.uuid4().hex[:16],
        "generatedAt": generated_at,
        "sourceSnapshotAt": source_snapshot_at,
        "inputRunIds": input_run_ids,
        "dataStatus": {"state": "valid", "label": "本轮判断质量快照已完成", "message": f"{total_projects} 个项目已对账。"},
        "reconciliation": {
            "projects": total_projects,
            "cases": case_count,
            "databaseProjects": db_counts.get("projects"),
            "databaseCases": db_counts.get("candidate_cases"),
            "countsMatch": total_projects == db_counts.get("projects") and case_count == db_counts.get("candidate_cases"),
            "funnelRowsReconciled": all(item["reconciled"] for item in funnel),
            "anomalies": [],
        },
        "coverageFunnel": funnel,
        "blockerRanking": blockers,
        "qualityMetrics": metrics,
        "closureQueue": sorted(queue, key=lambda item: (0 if "影响前台" in item["frontImpact"] else 1, timestamp(item.get("nextReviewAt")) or float("inf"), text(item["projectName"]))),
        "traceIndex": traces,
        "unitIssues": unit_issues,
        "templateIssues": template_issues,
        "counts": {"projects": total_projects, "cases": case_count, "queue": len(queue), "blockers": len(blockers)},
    }


def build_payloads(db_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = read_inputs(db_path)
    snapshots = inputs["snapshots"]
    if inputs["integrity"] != "ok" or inputs["foreignErrors"]:
        raise RuntimeError(f"数据库完整性检查失败：{inputs['integrity']} / 外键异常 {inputs['foreignErrors']}")
    candidate_cases = snapshots["candidate"].get("cases") or []
    if not candidate_cases:
        raise RuntimeError("候选快照为空，拒绝生成 C2.0 派生快照")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    case_to_project: dict[str, str] = {}
    for case in candidate_cases:
        project_id = text(case.get("projectId") or case.get("project_id"))
        case_id = text(case.get("caseId") or case.get("case_id"))
        if not project_id or not case_id:
            continue
        grouped[project_id].append(case)
        case_to_project[case_id] = project_id
    selected_cases = {project_id: select_case(cases) for project_id, cases in grouped.items()}
    selected_by_project: dict[str, dict[str, Any]] = {}
    details = detail_maps(snapshots["details"])
    tracking_map = {text(item.get("caseId")): item for item in snapshots["tracking"].get("tasks") or []}
    records: list[dict[str, Any]] = []
    for project_id, case in selected_cases.items():
        detail = details.get(project_id) or {}
        detail_case = next((item for item in detail.get("cases") or [] if text(item.get("case_id") or item.get("caseId")) == text(case.get("caseId"))), {})
        review = detail_case.get("convexityReview") or inputs["reviews"].get(text(case.get("caseId"))) or {}
        tracking_item = tracking_map.get(text(case.get("caseId")), {})
        tracking_run = (inputs["trackingRuns"].get(text(case.get("caseId"))) or [{}])[0]
        record = build_summary_record(
            case,
            detail,
            inputs["projects"].get(project_id, {}),
            review,
            tracking_item,
            tracking_run,
            inputs["evidenceByProject"],
        )
        selected_by_project[project_id] = record
        records.append(record)
    records.sort(key=lambda item: text(item["projectName"]).lower())
    chains, dedup_events, unit_issues = build_events(snapshots["changes"], case_to_project, selected_by_project)
    assign_priority(records, chains)
    template_issues = apply_template_rules(records)
    for record in records:
        if not record["summaryComplete"] and record["readingTier"] != "must_read":
            record["readingTier"] = "observe"
            record["readingTierLabel"] = READING_LABELS["observe"]
    tier_order = {"must_read": 0, "worth_following": 1, "observe": 2}
    impact_order = {"tighten": 0, "improve": 1, "no_change": 2, "": 3}
    latest_chain_by_project: dict[str, dict[str, Any]] = {}
    for chain in chains:
        latest_chain_by_project.setdefault(chain["projectId"], chain)

    def reading_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
        latest = latest_chain_by_project.get(record["projectId"]) or {}
        action_changed_or_actionable = (
            record["actionLabel"] in {"普通建仓", "极限试仓"}
            or "action" in (latest.get("dimensions") or [])
        )
        boundary = text((record["summary"].get("strongestSupport") or {}).get("factBoundary"))
        boundary_order = 0 if boundary == "confirmed_fact" else 1 if boundary == "high_confidence_inference" else 2
        return (
            tier_order[record["readingTier"]],
            0 if action_changed_or_actionable else 1,
            impact_order.get(record.get("impact"), 3),
            min((DIMENSION_ORDER.get(dimension, 99) for dimension in (latest.get("dimensions") or [])), default=99),
            boundary_order,
            -timestamp(latest.get("endedAt") or record.get("evidenceTime")),
            text(record["projectName"]).lower(),
        )

    records.sort(key=reading_sort_key)
    home = []
    for record in records:
        record["homeEligible"] = bool(
            record["summaryComplete"]
            and not record.get("templateParts")
            and not record.get("stale")
            and record["readingTier"] in {"must_read", "worth_following"}
        )
        if record["homeEligible"]:
            home.append(record)
    home.sort(key=reading_sort_key)
    home = home[:5]
    selected_for_quality = [dict(record) for record in records]
    home_project_ids = {item["projectId"] for item in home}
    for record in selected_for_quality:
        record["homeEligible"] = record["projectId"] in home_project_ids
    for record in records:
        record.pop("tracking", None)
        record.pop("homeEligible", None)
    input_run_ids: set[str] = set()
    for payload in snapshots.values():
        for path in (("latestRefresh", "runId"), ("latestRun", "runId")):
            value = payload.get(path[0], {}).get(path[1]) if isinstance(payload.get(path[0]), dict) else None
            if text(value):
                input_run_ids.add(text(value))
    for record in selected_for_quality:
        if text(record.get("latestRunId")):
            input_run_ids.add(text(record["latestRunId"]))
    for event in dedup_events:
        if text(event.get("runId")):
            input_run_ids.add(text(event["runId"]))
    for chain in chains:
        for step in chain.get("steps") or []:
            # run ids intentionally remain in the top-level input list only;
            # the front chain does not expose operational run identifiers.
            _ = step
    generated_at = iso_now()
    source_times = [parse_time(payload.get("generatedAt")) for payload in snapshots.values() if parse_time(payload.get("generatedAt"))]
    source_snapshot_at = min(source_times).isoformat().replace("+00:00", "Z") if source_times else generated_at
    quality = build_quality(
        selected_for_quality,
        chains,
        len(dedup_events),
        sum(len(event.get("displayNumbers", [])) for event in dedup_events),
        template_issues,
        unit_issues,
        inputs["lineageByEvidence"],
        sorted(input_run_ids),
        source_snapshot_at,
        generated_at,
        inputs["counts"],
        len(candidate_cases),
    )
    # Use one identity for both outputs.  build_quality creates a provisional
    # id so it can be overwritten after the front object is assembled.
    build_id = quality["buildId"]
    action_counts = Counter(record["actionLabel"] for record in records)
    top_blockers = []
    blocker_counter: dict[str, dict[str, Any]] = {}
    for record in records:
        blocker = record["summary"].get("primaryBlocker") or {}
        key = normalize_template(blocker.get("text"), [record["projectName"]])
        entry = blocker_counter.setdefault(key, {"category": blocker.get("category", "证据完整度"), "text": blocker.get("text", "资料不足"), "projectCount": 0})
        entry["projectCount"] += 1
    top_blockers = sorted(blocker_counter.values(), key=lambda item: (-item["projectCount"], text(item["category"])))[:3]
    current_decision = {
        "headline": "当前有可行动机会" if action_counts["普通建仓"] + action_counts["极限试仓"] else "当前没有满足行动条件的机会",
        "actionCounts": {label: action_counts[label] for label in ("普通建仓", "极限试仓", "只观察", "反身性管理", "失效/排除")},
        "asOf": max((text((case.get("machineConclusion") or {}).get("generated_at")) for case in candidate_cases), key=timestamp, default=generated_at),
        "mainLimits": [item["category"] for item in top_blockers],
    }
    front_records = []
    for record in records:
        visible = {key: value for key, value in record.items() if key not in {"sourceIds", "evidenceIds", "sourceUrls", "latestRunId", "stale", "templateParts", "homeEligible", "projectIdentityStatus", "assetIdentityStatus", "assetMapped", "sellPathStatus", "valueCaptureGrade", "maximumControllableLoss", "ignitionConditions"}}
        visible["summary"]["strongestSupport"] = scrub_front(visible["summary"].get("strongestSupport"))
        visible["summary"]["nextTrigger"].pop("evidenceIds", None)
        visible["summary"]["invalidation"].pop("evidenceIds", None)
        front_records.append(scrub_front(visible))
    front_home = [next(item for item in front_records if item["projectId"] == selected["projectId"]) for selected in home]
    front = {
        "schemaVersion": "c2.0-decision-signals-v1",
        "buildId": build_id,
        "generatedAt": generated_at,
        "sourceSnapshotAt": source_snapshot_at,
        "inputRunIds": sorted(input_run_ids),
        "dataStatus": {"state": "valid", "label": "本轮判断质量快照已完成", "message": f"{len(records)} 个项目已对账，首页有效信号 {len(home)}/5。"},
        "counts": {
            "projects": len(records),
            "cases": len(candidate_cases),
            "mustRead": sum(item["readingTier"] == "must_read" for item in records),
            "worthFollowing": sum(item["readingTier"] == "worth_following" for item in records),
            "observe": sum(item["readingTier"] == "observe" for item in records),
            "homeSignals": len(home),
            "changeChains": len(chains),
        },
        "currentDecision": current_decision,
        "homeSignals": front_home,
        "projects": front_records,
        "changeChains": scrub_front(chains),
        "topBlockers": scrub_front(top_blockers),
        "methodBoundary": {
            "readingTiers": list(READING_LABELS.values()),
            "statement": "阅读顺序只决定先看什么，不是投资动作、评分、仓位或 L0-L5。",
        },
    }
    quality["buildId"] = build_id
    quality["generatedAt"] = generated_at
    quality["sourceSnapshotAt"] = source_snapshot_at
    quality["inputRunIds"] = sorted(input_run_ids)
    quality["dataStatus"]["message"] = f"{len(records)} 个项目已对账，首页有效信号 {len(home)}/5。"
    quality["reconciliation"]["countsMatch"] = quality["reconciliation"]["countsMatch"] and quality["reconciliation"]["funnelRowsReconciled"]
    validate_front(front)
    if not quality["reconciliation"]["countsMatch"]:
        raise RuntimeError("C2.0 项目、案例或漏斗对账失败，拒绝替换活动快照")
    return front, quality


def scrub_front(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: scrub_front(item) for key, item in value.items() if not FORBIDDEN_FRONT_KEY.search(str(key))}
    if isinstance(value, list):
        return [scrub_front(item) for item in value]
    if isinstance(value, str):
        return replace_scientific(value).replace("retry", "再次尝试").replace("Retry", "再次尝试").replace("重试", "再次尝试")
    return value


def validate_front(front: dict[str, Any]) -> None:
    if front.get("schemaVersion") != "c2.0-decision-signals-v1":
        raise ValueError("前台快照 schemaVersion 错误")
    if len(front.get("homeSignals") or []) > 5:
        raise ValueError("首页信号超过 5 个")
    if front.get("counts", {}).get("homeSignals") != len(front.get("homeSignals") or []):
        raise ValueError("首页信号数量对账失败")
    if SCIENTIFIC_TEXT.search(json.dumps(front, ensure_ascii=False)):
        raise ValueError("前台快照包含科学计数法")
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if FORBIDDEN_FRONT_KEY.search(str(key)):
                    raise ValueError(f"前台安全字段违规：{key}")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(front)
    for item in front.get("homeSignals") or []:
        if not item.get("summaryComplete") or item.get("missingSummaryParts"):
            raise ValueError("不完整摘要进入首页")


def write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        temp = STATUS_OUTPUT.with_name(f".{STATUS_OUTPUT.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, STATUS_OUTPUT)
    except OSError:
        pass


def existing_identity(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_js_payload(path)
    except Exception:
        return {}


def atomic_publish(front: dict[str, Any], quality: dict[str, Any], simulate_failure: str = "") -> None:
    front_text = f"window.{FRONT_GLOBAL} = " + json.dumps(front, ensure_ascii=False, indent=2) + ";\n"
    quality_text = f"window.{QUALITY_GLOBAL} = " + json.dumps(quality, ensure_ascii=False, indent=2) + ";\n"
    temp_paths: dict[Path, Path] = {}
    originals: dict[Path, bytes | None] = {path: path.read_bytes() if path.exists() else None for path in (FRONT_OUTPUT, QUALITY_OUTPUT)}
    try:
        for path, payload in ((FRONT_OUTPUT, front_text), (QUALITY_OUTPUT, quality_text)):
            handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False)
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temp_paths[path] = Path(handle.name)
            parsed = load_js_payload(temp_paths[path])
            if parsed.get("buildId") != front.get("buildId"):
                raise ValueError("临时快照构建 ID 不一致")
        if simulate_failure in {"before_replace", "parse"}:
            raise RuntimeError("模拟 C2.0 双快照发布失败")
        os.replace(temp_paths[FRONT_OUTPUT], FRONT_OUTPUT)
        if simulate_failure == "after_front":
            raise RuntimeError("模拟第二份快照替换失败")
        os.replace(temp_paths[QUALITY_OUTPUT], QUALITY_OUTPUT)
    except Exception:
        for path, original in originals.items():
            try:
                if original is None:
                    if path.exists():
                        path.unlink()
                else:
                    restore = tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.restore.", suffix=".tmp", delete=False)
                    with restore:
                        restore.write(original)
                        restore.flush()
                        os.fsync(restore.fileno())
                    os.replace(restore.name, path)
            except OSError:
                pass
        raise
    finally:
        for path in temp_paths.values():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def build_decision_quality_snapshots(
    db_path: Path = DEFAULT_DB_PATH,
    dry_run: bool = False,
    simulate_failure: str = "",
) -> dict[str, Any]:
    generated_at = iso_now()
    try:
        front, quality = build_payloads(Path(db_path))
        if dry_run:
            result = {"status": "dry_run", "buildId": front["buildId"], "projects": front["counts"]["projects"], "cases": front["counts"]["cases"], "homeSignals": front["counts"]["homeSignals"]}
            write_status({"state": "dry_run", "updatedAt": generated_at, **result})
            return result
        atomic_publish(front, quality, simulate_failure=simulate_failure)
        result = {"status": "success", "buildId": front["buildId"], "generatedAt": front["generatedAt"], "projects": front["counts"]["projects"], "cases": front["counts"]["cases"], "homeSignals": front["counts"]["homeSignals"], "changeChains": front["counts"]["changeChains"]}
        write_status({"state": "success", "updatedAt": generated_at, "lastValidAt": front["generatedAt"], **result})
        return result
    except Exception as error:
        previous = existing_identity(FRONT_OUTPUT)
        write_status({"state": "failed", "updatedAt": generated_at, "error": str(error), "lastValidAt": previous.get("generatedAt"), "lastValidBuildId": previous.get("buildId")})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 C2.0 机会信号与判断质量双快照")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--simulate-failure", choices=["before_replace", "after_front", "parse"], default="")
    args = parser.parse_args()
    try:
        print(json.dumps(build_decision_quality_snapshots(args.db, args.dry_run, args.simulate_failure), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
