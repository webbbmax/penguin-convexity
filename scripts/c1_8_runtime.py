#!/usr/bin/env python3
"""C1.8 presentation and unattended-scheduler state.

This module owns only C1.8 presentation/runtime state.  It deliberately does
not calculate scores, actions, L0-L5, or any cross-product state.
"""

import json
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
DEFAULT_CONFIG_PATH = RUNTIME_ROOT / "c1.8-scheduler.json"
DEFAULT_STATE_PATH = RUNTIME_ROOT / "c1.8-scheduler-state.json"
DEFAULT_LOCK_PATH = RUNTIME_ROOT / "locks" / "c1.8-scheduler.lock"
WINDOWS_TASK_NAME = "PenguinConvexity-C1.8-Scheduler"

SCHEDULER_STATUS_LABELS = {
    "not_due": "尚未到期",
    "queued": "已排队",
    "running": "正在执行",
    "no_change": "已检查，无变化",
    "completed": "已完成",
    "partial": "部分完成",
    "failed": "执行失败",
    "paused": "自动运行已暂停",
    "quota_delayed": "额度保护延后",
}

DEFAULT_CONFIG = {
    "schemaVersion": "c1.8-scheduler-config-v1",
    "enabled": True,
    "paused": False,
    "dailyTime": "08:00",
    "timezone": "Asia/Shanghai",
    "hourlyDueCheck": True,
    "updatedAt": None,
}


def utc_now():
    return datetime.now(timezone.utc)


def iso_time(value):
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def load_json(path, fallback):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return dict(fallback) if isinstance(fallback, dict) else fallback


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def load_config(path=DEFAULT_CONFIG_PATH):
    config = dict(DEFAULT_CONFIG)
    config.update(load_json(path, {}))
    return config


def save_config(config, path=DEFAULT_CONFIG_PATH):
    merged = dict(DEFAULT_CONFIG)
    merged.update(config or {})
    validate_config(merged)
    merged["updatedAt"] = iso_time(utc_now())
    return write_json(path, merged)


def validate_config(config):
    value = str(config.get("dailyTime") or "")
    try:
        hour, minute = (int(item) for item in value.split(":", 1))
    except (ValueError, TypeError):
        raise ValueError("每日更新时间必须使用 HH:MM 格式。")
    if hour not in range(24) or minute not in range(60):
        raise ValueError("每日更新时间必须在 00:00 至 23:59 之间。")
    if config.get("timezone") != "Asia/Shanghai":
        raise ValueError("C1.8 当前只支持 Asia/Shanghai 调度时区。")
    return True


def _local_now(now):
    # Asia/Shanghai is UTC+8 and has no DST.  Keeping this local avoids a
    # runtime dependency on third-party timezone packages.
    return now.astimezone(timezone(timedelta(hours=8)))


def _next_daily(now, config):
    local = _local_now(now)
    hour, minute = (int(item) for item in config["dailyTime"].split(":", 1))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _next_hour(now):
    return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def _date_key(value):
    return _local_now(value).date().isoformat()


def load_state(path=DEFAULT_STATE_PATH):
    state = {
        "schemaVersion": "c1.8-scheduler-state-v1",
        "status": "not_due",
        "lastRunAt": None,
        "lastRunKind": None,
        "lastRunStatus": None,
        "lastError": "",
        "lastDailyDate": None,
        "lastHourlyAt": None,
        "nextDailyRunAt": None,
        "nextHourlyCheckAt": None,
        "queueCount": 0,
        "dueCount": 0,
        "updatedAt": None,
    }
    state.update(load_json(path, {}))
    return state


def windows_task_installed(task_name=WINDOWS_TASK_NAME):
    if os.name != "nt":
        return None
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def save_state(state, path=DEFAULT_STATE_PATH):
    value = dict(state or {})
    value["updatedAt"] = iso_time(utc_now())
    return write_json(path, value)


def _latest_refresh(opportunity):
    return opportunity.get("latestRefresh") or {}


def zero_result_state(opportunity, now=None):
    now = now or utc_now()
    counts = opportunity.get("counts") or {}
    refresh = _latest_refresh(opportunity)
    if counts.get("actionable", 0) > 0:
        return {
            "code": "ready_with_actions",
            "label": "已有项目达到行动门槛",
            "detail": "至少一个项目通过普通建仓或极限试仓门槛。",
            "isOldConclusion": False,
        }
    refresh_status = str(refresh.get("status") or "").lower()
    error_count = int(refresh.get("errorCount") or refresh.get("error_count") or 0)
    if refresh_status in {"failed", "partial_success", "error"} or error_count:
        return {
            "code": "update_failed",
            "label": "本轮更新失败，沿用上次有效结论",
            "detail": "关键更新未完整完成，0 个可执行项目不能视为市场结论。",
            "isOldConclusion": True,
        }
    generated = parse_time(opportunity.get("generatedAt"))
    if generated and (now - generated) > timedelta(hours=24):
        return {
            "code": "stale",
            "label": "数据已超过 24 小时，沿用旧结论",
            "detail": "页面仍保留上次有效结论，但必须等待新一轮完整更新。",
            "isOldConclusion": True,
        }
    if not refresh or not refresh_status:
        return {
            "code": "coverage_incomplete",
            "label": "关键覆盖尚未完成",
            "detail": "身份、资产、市场、退出或证据覆盖不足，暂不能把 0 个可执行项目当成市场结论。",
            "isOldConclusion": True,
        }
    return {
        "code": "qualified_none",
        "label": "覆盖完整，但没有项目通过全部行动条件",
        "detail": "本轮更新达到发布门槛，当前没有项目同时通过身份、证据、风险和交易条件。",
        "isOldConclusion": False,
    }


def _blocker_detail(item, label):
    stage = item.get("opportunityStage") or {}
    market = item.get("latestMarket") or {}
    path = item.get("catalystTradePath") or {}
    market_liquidity = market.get("liquidityUsd")
    if market_liquidity is None:
        market_liquidity = market.get("liquidity_usd")
    volume = market.get("volume24hUsd")
    if volume is None:
        volume = market.get("volume_24h_usd")
    slippage = path.get("modeled_exit_slippage_pct")
    if slippage is None:
        slippage = path.get("modeledExitSlippagePct")
    lowered = f"{label} {stage.get('blockerStatus', '')}".lower()
    if ("market" in lowered or "exit" in lowered or "交易" in label or "退出" in label) and (
        market_liquidity is not None or volume is not None or slippage is not None
    ):
        liquidity_text = f"约 {float(market_liquidity):,.0f} 美元" if market_liquidity is not None else "待取得"
        volume_text = f"约 {float(volume):,.0f} 美元" if volume is not None else "待取得"
        slippage_text = f"{float(slippage):.0f}%" if slippage is not None else "待估算"
        fact = (
            f"当前池子流动性{liquidity_text}、24小时成交额{volume_text}；"
            f"模拟退出2万美元滑点{slippage_text}。"
        )
        threshold = "2万美元理论退出滑点不超过 8%"
        if slippage is None:
            reason = "尚未取得2万美元理论滑点，不能确认退出能力。"
        elif float(slippage) > 8:
            reason = f"2万美元理论滑点{float(slippage):.0f}%超过8%门槛，无法可靠退出。"
        else:
            path_blockers = [
                str(value) for value in path.get("blockers") or []
                if any(marker in str(value) for marker in ("卖出", "退出", "合约"))
            ]
            reason = path_blockers[0] if path_blockers else "理论滑点已通过8%门槛，不应作为本次退出阻断。"
    else:
        fact = stage.get("finalActionReason") or stage.get("stageReason") or "关键条件尚未取得可复核事实。"
        threshold = "关键行动条件全部通过"
        reason = "该条件未通过会直接改变当前行动判断。"
    next_step = stage.get("nextStep") or "系统将继续采集对应一手来源并在下次到期时间复查。"
    return {
        "name": label,
        "fact": fact,
        "threshold": threshold,
        "impact": reason,
        "owner": "系统自动核验",
        "status": "blocked",
        "statusLabel": "当前阻断",
        "nextStep": next_step,
        "nextReviewAt": item.get("nextReviewAt") or None,
        "evidenceUrl": item.get("detailUrl") or "project-detail.html",
        "caseId": item.get("caseId"),
        "projectName": item.get("projectName"),
        "symbol": item.get("symbol"),
        "isGroup": False,
    }


def _group_blocker_detail(label, count, sample=None):
    definitions = {
        "项目主体待核验": (
            f"当前有 {count} 个项目尚未完成主体归属核验。",
            "项目官网、官方代码或资产登记至少形成可复核的独立归属链",
            "主体不确定时，后续资产、证据和市场数据可能归错项目，不能行动。",
            "系统继续比对官网、官方仓库、资产登记和第二独立来源。",
        ),
        "可购买资产待核验": (
            f"当前有 {count} 个项目尚未确认可购买资产、网络和合约关系。",
            "项目主体、受益资产、网络和合约关系全部核验一致",
            "无法确认价值由哪个可购买资产承接，不能形成交易动作。",
            "系统继续核验官方资产入口、网络、合约和独立资产登记。",
        ),
        "市场与退出待闭环": (
            f"当前有 {count} 个项目尚未同时完成市场、卖出路径和2万美元退出核验。",
            "交易池与合约匹配、卖出路径可用，且2万美元理论退出滑点不超过 8%",
            "退出能力没有完整通过时，潜在收益不能替代可控退出条件。",
            "系统继续获取最深交易池、24小时成交、合约风险和退出滑点。",
        ),
        "凸性结构待闭环": (
            f"当前有 {count} 个项目尚未闭环催化、价值捕获或剩余凸性证据。",
            "催化事实、价值传导、剩余凸性和失效边界均有可复核证据",
            "只有叙事而没有价值传导与失效边界时，不能形成凸性行动。",
            "系统继续核验催化事实、受益资产价值传导和失效条件。",
        ),
    }
    fact, threshold, impact, next_step = definitions.get(
        label,
        (
            f"当前有 {count} 个项目未通过这项行动条件。",
            "对应行动条件取得完整可复核事实",
            "条件未通过会阻止系统形成行动建议。",
            "系统将在下次到期时间继续核验对应来源。",
        ),
    )
    sample = sample or {}
    return {
        "name": label,
        "fact": fact,
        "threshold": threshold,
        "impact": impact,
        "owner": "系统自动核验",
        "status": "blocked",
        "statusLabel": "分组缺口",
        "nextStep": next_step,
        "nextReviewAt": sample.get("nextReviewAt"),
        "evidenceUrl": "candidate-pool.html?view=library#opportunityDirectory",
        "caseId": None,
        "projectName": None,
        "symbol": None,
        "isGroup": True,
    }


def build_c18_home(opportunity, now=None):
    now = now or utc_now()
    cases = opportunity.get("cases") or []
    observe = [
        item for item in cases
        if (item.get("opportunityStage") or {}).get("finalActionCategory") == "observe"
    ]

    def near_key(item):
        stage = item.get("opportunityStage") or {}
        screening = item.get("screening") or {}
        gaps = screening.get("failedReasons") or screening.get("pendingReasons") or []
        gap_count = max(1, len([item for item in gaps if str(item).strip()]))
        ignition = {"immediate": 0, "near": 1, "forming": 2, "distant": 3, "unknown": 4}.get(
            item.get("ignitionProximity"), 4
        )
        convexity = {"high": 0, "medium": 1, "low": 2, "none": 3, "unknown": 4}.get(
            item.get("remainingConvexity"), 4
        )
        liquidity = {"standard": 0, "extreme": 1, "limited": 2, "unknown": 3, "untradeable": 4}.get(
            item.get("liquidityGrade"), 4
        )
        risk = {"low": 0, "medium": 1, "high": 2, "unknown": 3, "blocked": 4}.get(
            item.get("riskLevel"), 4
        )
        return (gap_count, ignition, convexity, liquidity, risk, item.get("projectName", "").lower())

    near_action = []
    for item in sorted(observe, key=near_key)[:5]:
        stage = item.get("opportunityStage") or {}
        screening = item.get("screening") or {}
        gaps = screening.get("failedReasons") or screening.get("pendingReasons") or []
        gaps = [str(value).strip() for value in gaps if str(value).strip()]
        if not gaps:
            gaps = [stage.get("blockerLabel") or stage.get("stageReason") or "关键行动条件"]
        total = 5
        near_action.append({
            "caseId": item.get("caseId"),
            "projectName": item.get("projectName"),
            "symbol": item.get("symbol"),
            "detailUrl": item.get("detailUrl"),
            "currentAction": stage.get("finalActionLabel") or "只观察",
            "conditionsMet": max(0, total - len(gaps)),
            "conditionsTotal": total,
            "primaryGap": gaps[0],
            "owner": "系统自动核验",
            "nextReviewAt": item.get("nextReviewAt"),
            "possibleAction": "普通建仓或极限试仓复核",
        })

    blockers = (opportunity.get("conclusionBoard") or {}).get("blockers") or []
    blocker_details = []
    for blocker in blockers:
        sample = next((item for item in cases if item.get("caseId") in blocker.get("caseIds", [])), None)
        detail = _group_blocker_detail(
            blocker.get("label") or "行动条件",
            blocker.get("count", 0),
            sample,
        )
        detail.update({
            "id": blocker.get("id"),
            "count": blocker.get("count", 0),
            "caseIds": blocker.get("caseIds", []),
        })
        blocker_details.append(detail)
    # Hubble is the frozen readability example.  Keep its exact project facts
    # together without using it to change the underlying action.
    hubble = next(
        (item for item in cases if str(item.get("projectName") or "").lower() == "hubble"),
        None,
    )
    if hubble:
        detail = _blocker_detail(hubble, "市场与退出")
        detail.update({
            "id": f"case-blocker-{hubble.get('caseId')}",
            "count": 1,
            "caseIds": [hubble.get("caseId")],
            "relatedAction": (hubble.get("opportunityStage") or {}).get("finalActionLabel"),
        })
        blocker_details.append(detail)
    return {
        "version": "C1.8",
        "zeroResult": zero_result_state(opportunity, now),
        "nearAction": near_action,
        "blockerDetails": blocker_details,
        "pagination": {"pageSize": 20},
        "homeLimits": {"nearAction": 5, "importantChanges": 5, "systemWork": 5, "needsUser": 5},
    }


@contextmanager
def scheduler_lock(path=DEFAULT_LOCK_PATH, stale_after_seconds=3600):
    """A small cross-process lock for the scheduler only.

    The lock is independent from the desktop update lock, so closing the UI
    does not block a legitimate scheduled run.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    try:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime <= stale_after_seconds:
                    raise
                path.unlink()
                handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileNotFoundError:
                handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, f"pid={os.getpid()}\ncreated={time.time()}\n".encode("ascii"))
        yield True
    except FileExistsError:
        yield False
    finally:
        if handle is not None:
            os.close(handle)
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def scheduler_status(
    opportunity=None,
    tracking=None,
    now=None,
    config_path=DEFAULT_CONFIG_PATH,
    state_path=DEFAULT_STATE_PATH,
    due_count=None,
    task_installed=None,
):
    now = now or utc_now()
    config = load_config(config_path)
    state = load_state(state_path)
    opportunity = opportunity or {}
    tracking = tracking or {}
    if due_count is None:
        due_count = int((tracking.get("counts") or {}).get("due", 0))
    else:
        due_count = int(due_count)
    if config.get("paused") or not config.get("enabled"):
        status = "paused"
    elif task_installed is False:
        status = "failed"
        state["lastError"] = "Windows 自动任务未安装，关闭软件后不会自动运行。"
    elif state.get("status") in SCHEDULER_STATUS_LABELS:
        status = state["status"]
    else:
        status = "not_due"
    if due_count and status in {"not_due", "no_change", "completed"}:
        status = "queued"
    next_daily = parse_time(state.get("nextDailyRunAt"))
    if next_daily is None or next_daily <= now:
        next_daily = _next_daily(now, config)
    next_hourly = parse_time(state.get("nextHourlyCheckAt"))
    if next_hourly is None or next_hourly <= now:
        next_hourly = _next_hour(now)
    last_error = str(state.get("lastError") or "").strip()
    reasons = {
        "not_due": "尚未到每日全量更新时间，也没有项目到达每小时复查时间。",
        "queued": f"当前有 {due_count} 个到期项目，已等待本项目调度器执行。" if due_count else "任务已进入本项目执行队列。",
        "running": "本项目调度器正在采集所需来源并执行到期任务。",
        "no_change": "本轮到期项目已经检查，没有发现足以改变结论的新事实。",
        "completed": "本轮自动任务已经完成，结果和下次复查时间已保存。",
        "partial": "部分来源未完成；已保留成功结果和上次有效结论。",
        "failed": last_error or "本轮自动任务失败；上次有效结论仍保留。",
        "paused": "自动运行已由用户暂停，暂停期间不会执行每日或每小时任务。",
        "quota_delayed": last_error or "外部来源额度不足，本轮已延后且没有覆盖旧结论。",
    }
    next_actions = {
        "not_due": "等待下次每日全量更新或项目到期；无需用户操作。",
        "queued": "系统将在最近一次计划任务唤醒时自动执行。",
        "running": "等待系统完成；继续跟踪和无变化会自动落库。",
        "no_change": "按项目下次复查时间继续自动跟踪。",
        "completed": "按页面所示时间继续下一轮自动检查。",
        "partial": "系统保留成功项，并在工作台提供失败单项重试。",
        "failed": "系统保留旧结论；可在更新中心查看原因并单项重试。",
        "paused": "如需继续，在更新中心点击“恢复自动运行”。",
        "quota_delayed": "等待额度恢复后由系统重试，无需手动补数据。",
    }
    state.update({
        "status": status,
        "dueCount": due_count,
        "queueCount": due_count,
        "nextDailyRunAt": iso_time(next_daily),
        "nextHourlyCheckAt": iso_time(next_hourly),
    })
    return {
        "version": "C1.8",
        "enabled": bool(config.get("enabled")),
        "paused": bool(config.get("paused")),
        "dailyTime": config.get("dailyTime"),
        "timezone": config.get("timezone"),
        "hourlyDueCheck": bool(config.get("hourlyDueCheck")),
        "taskInstalled": task_installed,
        "status": status,
        "statusLabel": SCHEDULER_STATUS_LABELS[status],
        "owner": "系统自动运行" if status != "paused" else "用户在更新中心控制",
        "reason": reasons[status],
        "nextAction": next_actions[status],
        "nextDailyRunAt": iso_time(next_daily),
        "nextHourlyCheckAt": iso_time(next_hourly),
        "lastRunAt": state.get("lastRunAt"),
        "lastRunKind": state.get("lastRunKind"),
        "lastRunStatus": state.get("lastRunStatus"),
        "lastError": last_error,
        "dueCount": due_count,
        "zeroResult": zero_result_state(opportunity, now),
    }


def update_scheduler_config(payload, config_path=DEFAULT_CONFIG_PATH, state_path=DEFAULT_STATE_PATH):
    config = load_config(config_path)
    for key in ("enabled", "paused", "hourlyDueCheck"):
        if key in payload:
            config[key] = bool(payload[key])
    if "dailyTime" in payload:
        config["dailyTime"] = str(payload["dailyTime"])
    save_config(config, config_path)
    state = load_state(state_path)
    state["nextDailyRunAt"] = iso_time(_next_daily(utc_now(), config))
    state["nextHourlyCheckAt"] = iso_time(_next_hour(utc_now()))
    save_state(state, state_path)
    return config


def mark_scheduler_run(
    status,
    kind,
    now=None,
    error="",
    state_path=DEFAULT_STATE_PATH,
    config_path=DEFAULT_CONFIG_PATH,
):
    now = now or utc_now()
    state = load_state(state_path)
    config = load_config(config_path)
    state.update({
        "status": status,
        "lastRunAt": iso_time(now),
        "lastRunKind": kind,
        "lastRunStatus": status,
        "lastError": error,
        "lastDailyDate": _date_key(now) if kind == "daily" and status in {"completed", "no_change", "partial"} else state.get("lastDailyDate"),
        "lastHourlyAt": iso_time(now) if kind == "hourly" else state.get("lastHourlyAt"),
        "nextDailyRunAt": iso_time(_next_daily(now, config)),
        "nextHourlyCheckAt": iso_time(_next_hour(now)),
    })
    save_state(state, state_path)
    return state


def should_run(now=None, config_path=DEFAULT_CONFIG_PATH, state_path=DEFAULT_STATE_PATH, due_count=0):
    now = now or utc_now()
    config = load_config(config_path)
    state = load_state(state_path)
    if not config.get("enabled") or config.get("paused"):
        return None
    local = _local_now(now)
    hour, minute = (int(item) for item in config["dailyTime"].split(":", 1))
    scheduled_daily = parse_time(state.get("nextDailyRunAt"))
    daily_due = (
        now >= scheduled_daily
        if scheduled_daily
        else local.hour > hour or (local.hour == hour and local.minute >= minute)
    )
    if daily_due and state.get("lastDailyDate") != local.date().isoformat():
        return "daily"
    if config.get("hourlyDueCheck") and due_count and parse_time(state.get("lastHourlyAt")):
        if now - parse_time(state.get("lastHourlyAt")) >= timedelta(hours=1):
            return "hourly"
    if config.get("hourlyDueCheck") and due_count and not state.get("lastHourlyAt"):
        return "hourly"
    return None
