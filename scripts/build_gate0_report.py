#!/usr/bin/env python3
import html
import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = PROJECT_ROOT / "reports" / "gate0-data-preflight"
SUMMARY_PATH = REPORT_ROOT / "analysis-summary.json"
ARTIFACT_PATH = REPORT_ROOT / "artifact.json"
HTML_PATH = REPORT_ROOT / "report.html"
BACKFILL_ROLLUP_PATH = PROJECT_ROOT / "runtime" / "gate0-shadow" / "backfill" / "coverage-rollup.json"
BACKFILL_LATEST_PATH = PROJECT_ROOT / "runtime" / "gate0-shadow" / "backfill" / "latest.json"
BACKGROUND_LATEST_PATH = PROJECT_ROOT / "runtime" / "gate0-shadow" / "backfill" / "background" / "latest.json"
RESOURCE_CATALOG_PATH = PROJECT_ROOT / "config" / "strong-signal-resource-catalog.json"

NETWORK_NAMES = {
    "ethereum-mainnet": "ETH",
    "solana-mainnet": "Solana",
    "base-mainnet": "Base",
    "arbitrum-mainnet": "Arbitrum",
    "bnb-mainnet": "BNB",
    "robinhood-mainnet": "Robinhood",
}

REASON_NAMES = {
    "buy_and_sell_not_both_observed": "买卖未同时出现",
    "project_evidence_missing": "项目证据缺失",
    "project_evidence_not_independently_mapped": "项目链接待核验",
    "security_no_data": "安全资料缺失",
    "security_unsupported": "安全来源不支持",
    "security_quota_limited": "安全额度受限",
    "security_source_failure": "安全来源失败",
    "security_hard_risk": "合约硬风险",
    "t0_not_verified_in_window": "T0不在90天窗口",
}


def source(source_id, label, path, description, executed_at):
    return {
        "id": source_id,
        "label": label,
        "path": path,
        "description": description,
        "query": {
            "language": "sql",
            "engine": "sqlite",
            "executed_at": executed_at,
            "description": description,
            "sql": "SELECT json(:gate0_run) AS reviewed_gate0_run;",
            "tables_used": [],
            "filters": ["六条已配置链", "公开来源实际返回", "不使用随机抽样或固定候选上限"],
            "metric_definitions": {
                "poolsCollected": "本次运行在上游公开分页范围内实际返回的交易池数量，不代表全市场池总量。",
                "candidates": "本次影子候选中触发某项阻断原因的对象数；一个对象可同时触发多个原因。",
            },
        },
    }


def main():
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    backfill = (
        json.loads(BACKFILL_ROLLUP_PATH.read_text(encoding="utf-8"))
        if BACKFILL_ROLLUP_PATH.exists()
        else summary.get("backfill")
    )
    backfill_latest = (
        json.loads(BACKFILL_LATEST_PATH.read_text(encoding="utf-8"))
        if BACKFILL_LATEST_PATH.exists()
        else None
    )
    background_latest = (
        json.loads(BACKGROUND_LATEST_PATH.read_text(encoding="utf-8"))
        if BACKGROUND_LATEST_PATH.exists()
        else None
    )
    background_audit = None
    if background_latest and background_latest.get("runId"):
        audit_path = BACKGROUND_LATEST_PATH.parent / "runs" / background_latest["runId"] / "sol-independent-audit.json"
        if audit_path.exists():
            background_audit = json.loads(audit_path.read_text(encoding="utf-8"))
    backfill_complete = bool(
        background_latest
        and background_latest.get("state") == "completed"
        and background_audit
        and background_audit.get("pass") is True
        and background_audit.get("runId") == background_latest.get("runId")
    )
    resource_catalog = (
        json.loads(RESOURCE_CATALOG_PATH.read_text(encoding="utf-8"))
        if RESOURCE_CATALOG_PATH.exists()
        else {"resources": []}
    )
    # The report timestamp must describe this preflight/report generation, not the
    # last accepted backfill rollup (which can legitimately be older).
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    coverage_rows = [
        {
            "网络": NETWORK_NAMES.get(row["networkId"], row["networkId"]),
            "公开池返回数": row["poolsCollected"],
            "成功页数": row["pagesSucceeded"],
            "来源状态": row["state"],
            "停止原因": row["stopReason"],
            "覆盖90天": "否" if not row["coversNinetyDays"] else "是",
        }
        for row in summary["coverage"]
    ]
    blocking_rows = sorted(
        (
            {
                "阻断原因": REASON_NAMES.get(reason, reason),
                "候选数": count,
                "原因代码": reason,
            }
            for reason, count in summary["blockingReasons"].items()
        ),
        key=lambda row: row["候选数"],
        reverse=True,
    )
    probe_rows = [
        {
            "来源": row["source"],
            "网络": NETWORK_NAMES.get(row.get("networkId"), row.get("networkId") or "—"),
            "状态": row["state"],
            "剩余额度": row.get("creditsRemaining"),
            "说明": "缺少 " + "、".join(row.get("missing") or []) if row.get("missing") else "",
        }
        for row in summary["capabilityProbes"]
    ]
    source_run = source(
        "src_gate0_run",
        "Gate 0 有效影子运行",
        "runtime/gate0-shadow/latest.json",
        "读取有效 Day 1 的六链发现、请求账本、能力探针与硬门槛结果。",
        generated_at,
    )
    source_config = {
        "id": "src_gate0_config",
        "label": "Gate 0 影子范围配置",
        "path": "config/gate0-shadow-scope.json",
        "description": "固定90天窗口、项目零等待、六链范围、来源边界与失败状态枚举；14天仅为非阻塞的实时稳定性记录。",
    }
    source_backfill = {
        "id": "src_gate0_backfill",
        "label": "90天DEX回扫累计覆盖",
        "path": "runtime/gate0-shadow/backfill/coverage-rollup.json",
        "description": "按链取最新探测或回扫状态；EVM结构仅计入真实池及两侧代币均匹配的创建日志。",
    }
    source_backfill_latest = {
        "id": "src_gate0_backfill_latest",
        "label": "90天DEX回扫最近运行",
        "path": "runtime/gate0-shadow/backfill/latest.json",
        "description": "读取最近一次真实回扫运行的覆盖窗口、事件数和候选代币数；不把未完成请求窗口标记为90天完成。",
    }
    source_background_latest = {
        "id": "src_gate0_background_latest",
        "label": "90天可恢复回扫终态",
        "path": "runtime/gate0-shadow/backfill/background/latest.json",
        "description": "读取可恢复后台回扫的最终分片、请求、事件、候选和独立重算验收状态。",
    }
    source_resources = {
        "id": "src_gate0_resources",
        "label": "Gate 0 资源状态",
        "path": "config/strong-signal-resource-catalog.json",
        "description": "只记录凭据环境变量名、实测能力和接入边界，不记录密钥明文。",
    }
    counts = summary["counts"]
    request_summary = summary["requestSummary"]
    request_states_by_name = request_summary.get("byState") or {}
    # Older/fixture summaries only persisted the per-state breakdown.  Keep
    # the report backward compatible by deriving the total instead of failing
    # with KeyError when the optional aggregate is absent.
    request_total = request_summary.get("total")
    if request_total is None:
        request_total = sum(int(value or 0) for value in request_states_by_name.values())
    quota_hits = request_states_by_name.get("quota_limited", 0)
    reliability_days = summary.get("liveReliabilityDaysObserved", summary.get("shadowDaysObserved", 0))
    reliability_target = summary.get("liveReliabilityTargetDistinctDays", 14)
    request_states = [
        (state, request_states_by_name.get(state, 0))
        for state in (
            "success",
            "no_data",
            "quota_limited",
            "source_failure",
            "unsupported",
            "configuration_missing",
            "program_failure",
        )
    ]
    security_outcomes = summary.get("securityOutcomes") or []
    security_requested = sum(int(row.get("requested") or 0) for row in security_outcomes)
    security_returned = sum(int(row.get("returned") or 0) for row in security_outcomes)
    security_return_rate = (
        security_returned / security_requested if security_requested else None
    )
    evidence_rows = [
        ("候选代币", counts.get("candidateTokens", 0)),
        ("本地身份已匹配", counts.get("localAssetMatched", 0)),
        ("平台附带 GitHub", counts.get("githubLinked", 0)),
        ("平台附带网站", counts.get("websiteLinked", 0)),
        ("独立证据缺失", summary["blockingReasons"].get("project_evidence_missing", 0)),
        (
            "链接待独立映射",
            summary["blockingReasons"].get("project_evidence_not_independently_mapped", 0),
        ),
    ]
    truncated_networks = [
        NETWORK_NAMES.get(row["networkId"], row["networkId"])
        for row in summary["coverage"]
        if row.get("stopReason") == "upstream_page_cap_reached"
    ]
    latest_solana_scan = next(
        (
            row
            for row in (backfill_latest or {}).get("solanaScanResults", [])
            if row.get("networkId") == "solana-mainnet"
        ),
        {},
    )
    latest_backfill_text = (
        f"background/latest.json 回扫 {background_latest.get('runId')} 已完成："
        f"固定90天窗口、六链、{background_latest.get('partitionProgress', {}).get('completedCount', 0)}/"
        f"{background_latest.get('partitionProgress', {}).get('totalCount', 0)} 个分片，"
        f"事件 {background_latest.get('events', 0):,} 条、候选代币 {background_latest.get('candidateTokens', 0):,} 个；"
        "独立重算通过。这里只代表冻结协议范围，不代表全市场完整覆盖。"
        if backfill_complete
        else
        f"latest.json 回扫 {backfill_latest.get('runId')}：Solana 已完成真实来源窗口 "
        f"{latest_solana_scan.get('coveredWindowDays', 0):g}/{latest_solana_scan.get('requestedWindowDays', 90):g} 天，"
        f"创建事件 {latest_solana_scan.get('events', 0):,} 条、候选代币 {latest_solana_scan.get('candidateTokens', 0):,} 个；"
        "90 天请求窗口仍未完成，不能当作 90 天已通过。"
        if backfill_latest and latest_solana_scan
        else "latest.json 尚无可用的 90 天回扫结果。"
    )
    report_status = (
        "Gate 0 90天数据预检已通过；C2.1仍需另行冻结"
        if backfill_complete
        else ("尚未完成，继续即时验证" if not summary["gate0Passed"] else "通过，可进入正式冻结")
    )
    if backfill:
        backfill_coverage = backfill["coverage"]
        remaining_evm_groups = max(
            0,
            backfill_coverage["observedDexGroups"]
            - backfill_coverage["solanaDexGroups"]
            - backfill_coverage["verifiedEvmSchemas"],
        )
        backfill_text = (
            f"累计核查 **{backfill_coverage['networksObserved']}** 条链、**{backfill_coverage['observedDexGroups']}** 个观察DEX标签；"
            f"**{backfill_coverage['verifiedEvmSchemas']}** 个EVM DEX标签已登记创建事件结构；"
            f"Solana创建指令解码 **{backfill_coverage['solanaDexGroupsProgramIdentified']}/{backfill_coverage['solanaDexGroups']}**；"
            f"已登记结构90天完整扫描单元 {backfill_coverage['evmScansComplete']}/{backfill_coverage['evmScanUnits']}，"
            f"Solana来源窗口完成 {backfill_coverage.get('solanaSourceRangesComplete', 0)}/{backfill_coverage.get('solanaScanUnits', 0)}，"
            f"Solana 90天完成 {backfill_coverage.get('solanaRequestedWindowsComplete', 0)}/{backfill_coverage.get('solanaScanUnits', 0)}，"
            f"累计 **{backfill_coverage['eventRows']:,}** 条创建事件、**{backfill_coverage['candidateTokens']:,}** 个候选代币。"
        )
        backfill_followup = (
            "BNB和Robinhood的当前观察DEX标签已全部完成已登记结构回扫；"
            f"仍有 **{remaining_evm_groups}** 个观察EVM DEX标签缺创建事件结构。"
            "Solana 7个观察DEX标签的13种已登记创建指令已有确定性解码；当前接受运行覆盖约29天，90天起点已实测可读，完整90天运行仍在执行且尚未通过全文件校验。"
        )
    else:
        backfill_text = "90天DEX事件回扫器已经进入执行路径，当前尚无一轮完整回扫结果。"
        backfill_followup = "下一步是继续完成已登记结构回扫与缺失协议解码。"
    if backfill_complete:
        backfill_text = (
            f"冻结协议范围的六链90天回扫已完成：**{background_latest['partitionProgress']['completedCount']}/"
            f"{background_latest['partitionProgress']['totalCount']}** 个分片，"
            f"**{background_latest['events']:,}** 条创建事件、"
            f"**{background_latest['candidateTokens']:,}** 个唯一候选代币；"
            f"请求 **{background_latest['requests']['total']:,}** 次，独立重算和完成后幂等复验均通过。"
        )
        backfill_followup = (
            "Solana 7个观察DEX标签的13种已登记创建指令及冻结EVM标签均完成90天窗口回扫；"
            "Ethereum 4、Base 8、Arbitrum 6个未确认标签继续明确标记为 unsupported。"
            "结果是已登记协议范围的母池，不得解释为全市场完整母池或全局T0。"
        )
    accepted_candidate_count = (
        int(background_latest.get("candidateTokens") or 0)
        if backfill_complete
        else int(backfill_coverage["candidateTokens"] if backfill else 0)
    )
    badge_text = "Gate 0 通过" if backfill_complete else "Gate 0 未通过"
    algorithm_boundary_text = (
        f"当前 {accepted_candidate_count:,} 个候选只来自已验收的登记DEX创建事件；"
        "未登记EVM协议和其他强线索因子仍是后续产品范围问题，不能解释为全市场母池，也不能直接冻结评分算法。"
        if backfill_complete
        else f"当前 {accepted_candidate_count:,} 个候选只来自已接受的登记DEX创建事件；"
        "不同DEX标签可能共用同一物理创建来源，而未登记EVM协议、尚未验收的Solana完整90天运行和其他强线索因子仍是缺口，不能解释为全市场母池。"
    )
    resource_note = (
        "NodeReal、SQD 与 Blockscout 已完成冻结登记范围的历史回扫；Chainstack和QuickNode当前没有可用BNB端点。"
        if backfill_complete
        else "NodeReal承担BNB已登记来源的90天回扫；SQD完成BNB独立区间交叉验证、Solana约29天接受运行，并正在执行Solana完整90天回扫；Blockscout PRO承担Robinhood历史日志。Chainstack和QuickNode当前没有可用BNB端点。"
    )
    solana_boundary_text = (
        "Solana 7个DEX标签已实现13种确定性初始化指令解码；冻结登记范围的90天回扫已完成并通过独立重算。"
        if backfill_complete
        else "Solana 7个DEX标签已实现13种确定性初始化指令解码；约29天接受运行、90天起点探针和在途90天运行必须分开展示。"
    )
    blocking_lines = "\n".join(
        f"- **{row['阻断原因']}**：{row['候选数']} 个候选"
        for row in blocking_rows[:4]
    )
    coverage_table = "\n".join(
        ["| 网络 | 公开池 | 成功页 | 状态 | 停止原因 |", "|---|---:|---:|---|---|"]
        + [
            f"| {row['网络']} | {row['公开池返回数']} | {row['成功页数']} | {row['来源状态']} | {row['停止原因']} |"
            for row in coverage_rows
        ]
    )
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": "凸性 Gate 0 数据预检",
            "description": "C2.1冻结前的六链覆盖、免费额度、90天回溯和硬门槛数据质量实测。",
            "generatedAt": generated_at,
            "sources": [source_run, source_config, source_backfill, source_backfill_latest, source_background_latest, source_resources],
            "blocks": [
                {"id": "title", "type": "markdown", "body": "# 凸性 Gate 0 数据预检"},
                {
                    "id": "decision",
                    "type": "markdown",
                    "sourceId": "src_gate0_run",
                    "body": (
                        "## 结论：当前不能冻结 C2.1\n\n"
                         f"**{report_status}。** 公开返回 **{counts['pools']}** 个池、**{counts['candidateTokens']}** 个候选，深采前门槛通过 **{counts['preGatePass']}** 个。实时稳定性记录为 {summary['shadowDaysObserved']}/{summary['liveReliabilityTargetDistinctDays']}，但它不阻塞回扫、开发或项目展示。\n\n"
                         f"{backfill_text}\n\n"
                         f"主要阻断：项目证据缺失 **{summary['blockingReasons'].get('project_evidence_missing', 0)}** 个，安全资料缺失或不支持 **{summary['blockingReasons'].get('security_no_data', 0) + summary['blockingReasons'].get('security_unsupported', 0)}** 个；已登记结构可回扫，但尚未建立全市场完整母池。\n\n"
                         f"{backfill_followup}所有项目只使用接口返回的真实历史数据，不等待未来13天。C2.0保持冻结，本报告不是投资结论。"
                    ),
                },
                {"id": "coverage-chart-block", "type": "chart", "chartId": "coverage-chart", "layout": "half"},
            ],
            "charts": [
                {
                    "id": "coverage-chart",
                    "title": "本次六链公开池返回量",
                    "description": "本次实际返回；受最近48小时窗口、公开分页和上游限流约束。",
                    "type": "bar",
                    "layout": "half",
                    "surface": {"compact": True, "showControls": False, "viewMode": "visualization"},
                    "encodings": {
                        "x": {"field": "网络", "type": "nominal", "title": "网络"},
                        "y": {"field": "公开池返回数", "type": "quantitative", "title": "公开池返回数"},
                    },
                    "options": {"showLegend": False},
                    "sourceId": "src_gate0_run",
                    "dataset": "coverage_by_network",
                    "source": source_run,
                }
            ],
        },
        "snapshot": {
            "version": 1,
            "status": "ready",
            "generatedAt": generated_at,
            "datasets": {
                "coverage_by_network": coverage_rows,
                "request_states": [
                    {"state": state, "requests": count}
                    for state, count in request_states
                ],
                "project_evidence": [
                    {"metric": label, "count": count}
                    for label, count in evidence_rows
                ],
                "goplus_security": [
                    {
                        "requested": security_requested,
                        "returned": security_returned,
                        "returnRate": security_return_rate,
                    }
                ],
            },
        },
        "sources": [source_run, source_config, source_backfill, source_backfill_latest, source_background_latest, source_resources],
    }
    ARTIFACT_PATH.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def escape(value):
        return html.escape(str(value if value is not None else "—"))

    backfill_rows = []
    if backfill:
        for row in backfill["networkResults"]:
            scan_status = (
                "冻结范围90天完成"
                if backfill_complete and row["networkId"] == "solana-mainnet"
                else
                f"{row['evmScansComplete']}/{row['evmScanUnits']}"
                if row["evmScanUnits"]
                else (
                    f"来源窗口{' 完成' if row.get('solanaSourceRangeComplete') else ' 未完成'} / "
                    f"90天{' 完成' if row.get('solanaRequestedWindowComplete') else ' 未完成'}"
                )
            )
            backfill_rows.append(
                "<tr>"
                f"<td>{escape(NETWORK_NAMES.get(row['networkId'], row['networkId']))}</td>"
                f"<td>{row['observedDexGroups']}</td>"
                f"<td>{row['verifiedEvmSchemas']}</td>"
                f"<td>{row['solanaDexGroupsProgramIdentified']}/{row['solanaDexGroups']}</td>"
                f"<td>{scan_status}</td>"
                f"<td>{'完整' if row['historicalBackfillComplete'] else '未完成'}</td>"
                "</tr>"
            )
    resource_rows = []
    relevant_resource_ids = {
        "nodereal",
        "sqd-portal",
        "chainstack-platform",
        "quicknode-admin",
        "blockscout",
    }
    for row in resource_catalog.get("resources", []):
        if row.get("id") not in relevant_resource_ids:
            continue
        connection_status = row.get("connectionStatus")
        consumer_status = row.get("consumerStatus")
        if backfill_complete and row.get("id") == "sqd-portal":
            connection_status = "gate0_solana_90d_registered_scope_complete_sol_validated"
            consumer_status = "gate0_final_backfill_complete_read_only_evidence"
        resource_rows.append(
            "<tr>"
            f"<td>{escape(row.get('name'))}</td>"
            f"<td>{escape(connection_status)}</td>"
            f"<td>{escape(consumer_status)}</td>"
            "</tr>"
        )
    request_state_rows = "".join(
        f"<tr><td>{escape(state)}</td><td>{count}</td></tr>"
        for state, count in request_states
    )
    evidence_html_rows = "".join(
        f"<tr><td>{escape(label)}</td><td>{count}</td></tr>"
        for label, count in evidence_rows
    )
    security_rate_text = (
        "不可计算（没有可用请求）"
        if security_return_rate is None
        else f"{security_return_rate:.1%}"
    )
    truncation_text = "、".join(truncated_networks) if truncated_networks else "无"
    html_document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>凸性 Gate 0 数据预检</title>
<style>
:root{{--ink:#13233a;--muted:#66758a;--line:#dfe7f0;--blue:#0d5bd7;--pale:#f4f8fd;--warn:#a84a00}}
*{{box-sizing:border-box}}html,body{{max-width:100%;overflow-x:hidden}}body{{margin:0;background:#eef3f8;color:var(--ink);font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:1120px;margin:32px auto;padding:0 20px 48px;min-width:0}}header,.panel{{background:white;border:1px solid var(--line);border-radius:16px;padding:24px;margin-bottom:16px;min-width:0}}
h1{{font-size:28px;margin:6px 0}}h2{{font-size:20px;margin:0 0 14px}}p{{margin:8px 0}}.muted{{color:var(--muted)}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#fff1e8;color:var(--warn);font-weight:700}}
.notice{{border-left:4px solid var(--blue);background:var(--pale);padding:14px 16px;margin-top:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(160px,100%),1fr));gap:10px;margin-top:18px}}
.kpi{{background:var(--pale);padding:14px;border-radius:12px}}.kpi strong{{display:block;font-size:24px;color:var(--blue)}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border-bottom:1px solid var(--line);padding:10px 8px;text-align:left;overflow-wrap:anywhere}}th{{color:var(--muted);font-weight:600;background:#f8fafc}}.table-wrap{{max-width:100%;overflow-x:auto}}
code{{background:#eef3f8;padding:2px 5px;border-radius:5px}}ul{{padding-left:20px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr 1fr}}.table-wrap{{overflow-x:auto}}}}
</style></head><body><main>
<header><span class="badge">{escape(badge_text)}</span><h1>凸性 Gate 0 数据预检</h1>
<p class="muted">目的：确认程序是否具备全量发现、真实发币历史、交易与安全数据的自动采集能力；本页不是投资结论。</p>
<div class="notice"><strong>不用等待未来 13 天。</strong> 所有项目按接口返回的真实历史计算；14 个自然日只记录实时管道稳定性，不阻塞回扫、开发或项目展示。</div>
<div class="grid"><div class="kpi"><strong>{backfill_coverage['networksObserved'] if backfill else 0}</strong>已核查链</div><div class="kpi"><strong>{backfill_coverage['observedDexGroups'] if backfill else 0}</strong>实际 DEX 组</div><div class="kpi"><strong>{backfill_coverage['verifiedEvmSchemas'] if backfill else 0}</strong>EVM DEX标签已登记</div><div class="kpi"><strong>{backfill_coverage['solanaDexGroupsProgramIdentified'] if backfill else 0}/{backfill_coverage['solanaDexGroups'] if backfill else 0}</strong>Solana 创建解码</div><div class="kpi"><strong>{backfill_coverage['evmScansComplete'] if backfill else 0}/{backfill_coverage['evmScanUnits'] if backfill else 0}</strong>EVM物理创建来源90天扫描</div></div></header>
<section class="panel"><h2>本次实时稳定性与请求分类</h2><p>有效影子日：<strong>{reliability_days}/{reliability_target}</strong>；本次请求：<strong>{request_total}</strong>；六链覆盖：<strong>{len(summary['coverage'])}/6</strong>；上游分页截断：<strong>{escape(truncation_text)}</strong>。</p><p>这组 14 日记录只衡量实时管道稳定性，不是项目观察期，不要求项目或开发等待，也不阻塞 90 日回扫。</p><div class="table-wrap"><table><thead><tr><th>状态</th><th>请求数</th></tr></thead><tbody>{request_state_rows}</tbody></table></div></section>
<section class="panel"><h2>GoPlus 返回率与项目证据映射</h2><p>GoPlus 返回率：<strong>{security_returned}/{security_requested}</strong>（{security_rate_text}）。返回率只说明接口覆盖，不等同项目质量。</p><div class="table-wrap"><table><thead><tr><th>项目证据指标</th><th>数量</th></tr></thead><tbody>{evidence_html_rows}</tbody></table></div></section>
<section class="panel"><h2>当前结论</h2><p>{backfill_text.replace('**', '')}</p><p><strong>最近 90 天回扫进展：</strong>{escape(latest_backfill_text)}</p><p><strong>不能直接进入算法冻结：</strong>{escape(algorithm_boundary_text)}</p></section>
<section class="panel"><h2>各链回扫能力</h2><div class="table-wrap"><table><thead><tr><th>链</th><th>DEX组</th><th>已登记EVM标签</th><th>Solana创建解码</th><th>创建来源扫描</th><th>全观察DEX状态</th></tr></thead><tbody>{''.join(backfill_rows)}</tbody></table></div></section>
<section class="panel"><h2>本次新增历史资源</h2><div class="table-wrap"><table><thead><tr><th>资源</th><th>实测连接状态</th><th>当前用途</th></tr></thead><tbody>{''.join(resource_rows)}</tbody></table></div><p class="muted">{escape(resource_note)}</p></section>
<section class="panel"><h2>近期公开发现覆盖</h2><div class="table-wrap"><table><thead><tr><th>链</th><th>返回池</th><th>成功页</th><th>来源状态</th><th>停止原因</th></tr></thead><tbody>{''.join(f"<tr><td>{escape(row['网络'])}</td><td>{row['公开池返回数']}</td><td>{row['成功页数']}</td><td>{escape(row['来源状态'])}</td><td>{escape(row['停止原因'])}</td></tr>" for row in coverage_rows)}</tbody></table></div></section>
<section class="panel"><h2>为什么还不能评分</h2><ul>{''.join(f"<li><strong>{escape(row['阻断原因'])}</strong>：{row['候选数']} 个候选</li>" for row in blocking_rows[:6])}</ul><p>这些是数据能力缺口，不是项目质量结论。来源成功但无返回必须显示为 <code>no_data</code>，额度耗尽与连接失败必须分别显示。</p></section>
<section class="panel"><h2>数据边界</h2><ul><li>项目等待天数：0；不合成未来观察日。</li><li>回扫得到的是已覆盖 DEX 的最早公开池证据，不自动等同全市场首次流通时间。</li><li>EVM 只登记已由真实池、两侧代币和创建日志交叉匹配的事件结构。</li><li>{escape(solana_boundary_text)}</li><li>C2.0 保持冻结，Gate 0 不写生产数据库、不改既有产品调度。</li></ul><p class="muted">生成时间：{escape(generated_at)} · 证据：<code>runtime/gate0-shadow/backfill/background/latest.json</code>、<code>sol-independent-audit.json</code></p></section>
</main></body></html>"""
    HTML_PATH.write_text(html_document, encoding="utf-8")
    print(ARTIFACT_PATH)
    print(HTML_PATH)


if __name__ == "__main__":
    main()
