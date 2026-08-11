#!/usr/bin/env python3
"""Build the read-only Gate 0 background backfill progress page."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND_ROOT = PROJECT_ROOT / "runtime" / "gate0-shadow" / "backfill" / "background"
LATEST_PATH = BACKGROUND_ROOT / "latest.json"
PROGRESS_ROOT = PROJECT_ROOT / "reports" / "gate0-backfill-progress"
HTML_PATH = PROGRESS_ROOT / "report.html"
ARTIFACT_PATH = PROGRESS_ROOT / "artifact.json"


def write_atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".building")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def read_json(path, default=None):
    if not Path(path).exists():
        return default
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def pct(progress):
    total = int(progress.get("totalWeight") or 0)
    done = int(progress.get("completedWeight") or 0)
    return (done / total * 100) if total else 0


def state_text(state):
    return {
        "preparing": "正在准备固定计划",
        "running": "后台回扫运行中",
        "retrying": "上游失败，正在重试",
        "quota_wait": "额度或速率限制，等待重试",
        "paused": "已暂停，等待恢复",
        "completed": "已完成，后续触发不再请求网络",
        "failed": "存在失败分片，等待后台恢复",
        "already_running": "已有实例运行中",
    }.get(state, state or "未知")


def build_artifact(latest, plan):
    progress = latest.get("partitionProgress") or {}
    stale = False
    heartbeat = latest.get("lastHeartbeatAt")
    if heartbeat:
        try:
            from datetime import datetime, timezone

            observed = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            stale = (datetime.now(timezone.utc) - observed).total_seconds() > 600
        except ValueError:
            stale = True
    return {
        "surface": "gate0-backfill-progress",
        "generatedAt": latest.get("updatedAt"),
        "runId": latest.get("runId"),
        "state": latest.get("state"),
        "stateText": state_text(latest.get("state")),
        "window": {
            "start": latest.get("windowStart"),
            "end": latest.get("windowEnd"),
            "days": latest.get("windowDays"),
        },
        "partitionProgress": {
            **progress,
            "percent": round(pct(progress), 3),
        },
        "networkRequestProgress": latest.get("networkRequestProgress") or {},
        "reuseProgress": latest.get("reuseProgress") or {},
        "correctedFromRunId": latest.get("correctedFromRunId"),
        "networkProgress": latest.get("networkProgress") or [],
        "schemaCoverage": latest.get("schemaCoverage") or {},
        "requests": latest.get("requests") or {},
        "events": latest.get("events", 0),
        "candidateTokens": latest.get("candidateTokens", 0),
        "currentWork": latest.get("currentWork") or {},
        "lastCheckpoint": latest.get("lastCheckpoint") or {},
        "recoveryCount": latest.get("recoveryCount", 0),
        "failureSummary": latest.get("failureSummary") or {},
        "eta": latest.get("eta") or {},
        "heartbeat": {
            "last": heartbeat,
            "stale": stale,
            "text": "进程可能已停止，等待独立任务自动恢复" if stale else "心跳正常",
        },
        "boundary": {
            "realtime14DaysIsParallelEvidence": True,
            "notProjectObservationPeriod": True,
            "doesNotBlockDevelopment": True,
            "marketWideComplete": False,
            "usableAsGlobalT0": False,
        },
    }


def render(artifact):
    progress = artifact["partitionProgress"]
    network_requests = artifact.get("networkRequestProgress") or {}
    reused = artifact.get("reuseProgress") or {}
    rows = []
    for network in artifact["networkProgress"]:
        sub = network.get("partitions") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(network.get('networkId', '')))}</td>"
            f"<td>{html.escape(str(network.get('state', '')))}</td>"
            f"<td>{sub.get('completedCount', 0)}/{sub.get('totalCount', 0)}</td>"
            f"<td>{sub.get('completedWeight', 0):,}/{sub.get('totalWeight', 0):,}</td>"
            f"<td>{html.escape(', '.join(network.get('schemas') or []))}</td>"
            "</tr>"
        )
    request_states = artifact["requests"].get("byState") or {}
    request_rows = "".join(
        f"<tr><td>{html.escape(str(state))}</td><td>{int(request_states.get(state, 0)):,}</td></tr>"
        for state in ("success", "no_data", "quota_limited", "source_failure", "unsupported", "configuration_missing", "program_failure")
    )
    current = html.escape(json.dumps(artifact["currentWork"], ensure_ascii=False, indent=2))
    failures = html.escape(json.dumps(artifact["failureSummary"], ensure_ascii=False, indent=2))
    unsupported = html.escape(", ".join(artifact["schemaCoverage"].get("unsupported") or []))
    stale_class = " stale" if artifact["heartbeat"]["stale"] else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Gate 0 90 天回扫进度</title>
<style>
html,body{{max-width:100%;overflow-x:hidden}}body{{margin:0;background:#f4f8fb;color:#173047;font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1120px;margin:0 auto;padding:28px 20px 56px;min-width:0}}h1{{margin:0 0 8px;font-size:28px;overflow-wrap:anywhere}}
.muted{{color:#5d7485}}.panel{{background:#fff;border:1px solid #d7e3ea;border-radius:14px;padding:18px;margin:16px 0;box-shadow:0 4px 16px #1730470b}}
.state{{font-size:20px;font-weight:700}}.stale{{border-color:#c98538;background:#fff8ed}}.bar{{height:14px;border-radius:99px;background:#e5edf2;overflow:hidden;margin:12px 0}}
.bar>i{{display:block;height:100%;width:{progress.get('percent', 0)}%;background:#2d8a9e}}table{{width:100%;border-collapse:collapse;table-layout:fixed}}th,td{{text-align:left;padding:8px;border-bottom:1px solid #e4edf1;vertical-align:top;overflow-wrap:anywhere;word-break:break-word}}code,pre{{white-space:pre-wrap;word-break:break-word}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}.metric{{padding:12px;background:#f5f9fb;border-radius:10px}}
@media(max-width:700px){{main{{padding:18px 12px}}h1{{font-size:23px}}table{{font-size:13px}}th:nth-child(4),td:nth-child(4),th:nth-child(5),td:nth-child(5){{display:none}}}}
</style></head><body><main>
<h1>Gate 0：90 天 DEX 工厂事件回扫</h1>
<div class="muted">独立后台任务 · runId {html.escape(str(artifact.get('runId', '')))} · 页面每 30 秒刷新</div>
<section class="panel{stale_class}"><div class="state">{html.escape(artifact.get('stateText', ''))}</div>
<div>{html.escape(artifact['heartbeat']['text'])}；最后心跳：{html.escape(str(artifact['heartbeat']['last'] or '暂无'))}</div>
<div class="bar"><i></i></div><div>分片覆盖权重：<strong>{progress.get('completedWeight', 0):,}/{progress.get('totalWeight', 0):,}</strong>（{progress.get('percent', 0):.3f}%）</div>
<div class="muted">覆盖权重按不同链的区块数量计算，不代表已经耗费或剩余的时间，不能据此推算天数。</div>
<div>全部覆盖分片：<strong>{progress.get('completedCount', 0)}/{progress.get('totalCount', 0)}</strong>；仍需联网的分片：<strong>{network_requests.get('completedCount', 0)}/{network_requests.get('totalCount', 0)}</strong>；只读复用：<strong>{reused.get('completedCount', 0)}/{reused.get('totalCount', 0)}</strong></div>
<div>固定窗口：<strong>{html.escape(str(artifact['window'].get('start')))} — {html.escape(str(artifact['window'].get('end')))}</strong></div></section>
<section class="panel"><h2>当前结果</h2><div class="grid"><div class="metric">请求：<strong>{artifact['requests'].get('total', 0):,}</strong></div><div class="metric">事件：<strong>{artifact.get('events', 0):,}</strong></div><div class="metric">候选代币：<strong>{artifact.get('candidateTokens', 0):,}</strong></div><div class="metric">恢复次数：<strong>{artifact.get('recoveryCount', 0):,}</strong></div></div></section>
<section class="panel"><h2>六链与协议覆盖</h2><table><thead><tr><th>网络</th><th>状态</th><th>分片</th><th>覆盖权重</th><th>已登记 schema</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p class="muted">明确未支持观察标签：{unsupported or '当前计划未发现额外标签'}。未支持协议不会被猜测纳入。</p></section>
<section class="panel"><h2>请求分类</h2><table><thead><tr><th>状态</th><th>请求数</th></tr></thead><tbody>{request_rows}</tbody></table></section>
<section class="panel"><h2>当前工作与失败分类</h2><pre>{current}</pre><pre>{failures}</pre></section>
<section class="panel"><h2>边界说明</h2><p>实时稳定性目标 14 个自然日只是并行证据，不是项目观察期；不要求任何项目或开发等待，也不阻塞本次 90 天历史回扫。所有项目只使用接口返回的真实历史数据。本回扫不是全市场覆盖，也不能单独定义全局 T0。</p></section>
<div class="muted">生成时间：{html.escape(str(artifact.get('generatedAt', '')))}</div>
</main></body></html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build Gate 0 background progress report")
    parser.add_argument("--latest", default=str(LATEST_PATH))
    parser.add_argument("--output", default=str(HTML_PATH))
    args = parser.parse_args(argv)
    latest = read_json(args.latest, {}) or {}
    plan_path = BACKGROUND_ROOT / "runs" / str(latest.get("runId", "")) / "run-plan.json"
    plan = read_json(plan_path, {}) or {}
    artifact = build_artifact(latest, plan)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact_path = output.with_name("artifact.json")
    write_atomic_text(artifact_path, json.dumps(artifact, ensure_ascii=False, indent=2))
    write_atomic_text(output, render(artifact))
    print(str(output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
