(function c24Admin() {
  "use strict";
  const data = window.PENGUIN_CONVEXITY_C24_ADMIN;
  if (!data || data.schemaVersion !== "c2.4-admin-snapshot-v1" || !data.isComplete) return;
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const fmt = (value) => value == null ? "暂无" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
  const fmtTime = (value) => { if (!value) return "暂无"; const date = new Date(value); return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date); };
  const fmtCheckpoint = (value) => {
    if (!value) return "由作业自动保存";
    if (typeof value === "string") return value;
    const queue = value.queue || value;
    if (queue && Number.isFinite(Number(queue.remaining))) {
      return `已完成 ${fmt(queue.completed || 0)} / ${fmt(queue.total || 0)}，剩余 ${fmt(queue.remaining || 0)}`;
    }
    return "已保存到最近一个安全断点";
  };
  const page = location.pathname.split("/").pop() || "workbench.html";
  if (document.documentElement.dataset.adminRendererOwner === "c25") return;
  const main = $("main");
  if (!main) return;
  main.className = "c24-admin-main";

  const statusCopy = {
    success: ["最近一次已完成", "normal"], no_data: ["本轮没有返回可用数据", "boundary"], unsupported: ["当前接口不支持", "boundary"],
    quota_limited: ["接口额度暂时用完，等待冷却", "attention"], source_failure: ["来源连接失败，正在按断点重试", "attention"],
    configuration_missing: ["当前来源缺少必要配置", "attention"], program_failure: ["更新程序未完成", "attention"], normal_zero: ["本轮正常没有新增", "boundary"],
  };
  const needsAction = new Set(["quota_limited", "source_failure", "configuration_missing", "program_failure"]);
  const recoverableStates = new Set(["quota_limited", "source_failure"]);
  const retryable = new Set(["coingecko_new_pools", "dexscreener", "project_website_identity", "github", "goplus", "c2_1_path4", "standard_sell_quote", "robinhood_official_assets"]);
  const sourceNames = {
    coingecko_new_pools: "新池与候选发现", dexscreener: "市场与流动性", project_website_identity: "项目网站与身份链路",
    github: "官方代码仓库", goplus: "安全与供应", c2_1_path4: "池活动与供应历史", standard_sell_quote: "100 美元标准卖出报价", robinhood_official_assets: "Robinhood 官方资产登记",
    gate0_accepted_candidates: "已验收历史候选", candidate_production_dexscreener: "历史候选市场确认", candidate_first_gate: "第一关基础检查",
  };
  const screeningSources = new Set(["gate0_accepted_candidates", "coingecko_new_pools", "candidate_production_dexscreener", "candidate_first_gate"]);
  const sharedSources = new Set(["dexscreener"]);

  function runtimeMessage(job) {
    const message = job.message || job.stage || "处理中";
    if (job.state === "completed" && message.includes("仍有") && message.includes("连接失败")) {
      return `上次单项重试的结果：${message} 当前状态请以下方“需要处理”为准。`;
    }
    return message;
  }

  function head(kicker, title, intro) {
    return `<header class="c24-admin-head"><div><span>${esc(kicker)}</span><h1>${esc(title)}</h1><p>${esc(intro)}</p></div><div class="c24-admin-cutoff"><small>当前完整快照</small><strong>${esc(fmtTime(data.dataCutoffAt))}</strong></div></header>`;
  }

  function metric(label, value, note = "") {
    return `<article class="c24-admin-metric"><span>${esc(label)}</span><strong>${esc(fmt(value))}</strong>${note ? `<small>${esc(note)}</small>` : ""}</article>`;
  }

  const chainLabels = {
    "ethereum-mainnet": "Ethereum", "solana-mainnet": "Solana", "base-mainnet": "Base",
    "arbitrum-mainnet": "Arbitrum", "bnb-mainnet": "BNB Chain", "robinhood-mainnet": "Robinhood Chain",
  };
  const trackingLabels = {
    complete_tracking: "已完成本轮跟踪", waiting_public_baseline: "等待公开底线",
    continued_tracking: "90 天后持续跟踪", awaiting_first_tracking: "等待首轮跟踪",
    source_retry: "等待来源重试", observing: "后台观察",
  };
  const evidenceTypeLabels = {
    business: "可归属业务", deployed_product: "已部署产品", github: "官方代码仓库",
    product_usage: "产品使用", token_utility: "代币用途",
  };
  const evidenceStatusLabels = {
    qualifying: "达到证据条件", pending: "等待补齐", no_data: "当前无数据",
    unsupported: "当前来源不支持", non_qualifying: "本轮未达到条件",
  };
  const factorWeightLabels = {
    tradeDemand: "交易需求", liquidityExit: "流动性与退出", supplyStructure: "供应结构",
    activityValuation: "活动与估值", stabilityAnomaly: "稳定性与异常",
  };

  function percentage(value, total, digits = 1) {
    if (!total) return "0%";
    const result = Number(value || 0) / Number(total) * 100;
    return `${result < 0.1 && result > 0 ? result.toFixed(2) : result.toFixed(digits)}%`;
  }

  function horizontalBars(title, intro, rows, options = {}) {
    const values = rows.map((row) => Math.max(0, Number(row.value || 0)));
    const scaleMax = Math.max(Number(options.scaleMax || 0), ...values, 1);
    const suffix = options.suffix || "";
    const bars = rows.map((row) => {
      const value = Math.max(0, Number(row.value || 0));
      const width = Math.min(100, value / scaleMax * 100);
      const displayValue = row.displayValue ?? `${fmt(value)}${suffix}`;
      return `<article class="c24-bar-row" data-tone="${esc(row.tone || "blue")}">
        <div class="c24-bar-label"><span>${esc(row.label)}</span><strong>${esc(displayValue)}</strong></div>
        <div class="c24-bar-track" aria-hidden="true"><i style="--bar-width:${width}%"></i></div>
        ${row.note ? `<small>${esc(row.note)}</small>` : ""}
      </article>`;
    }).join("");
    return `<section class="c24-chart-panel">
      <div class="c24-chart-head"><div><h2>${esc(title)}</h2><p>${esc(intro)}</p></div><span>${esc(options.axisNote || "横条从 0 开始，右侧显示精确值")}</span></div>
      <div class="c24-bar-chart" role="img" aria-label="${esc(`${title}：${rows.map((row) => `${row.label} ${row.displayValue ?? `${fmt(row.value)}${suffix}`}`).join("，")}`)}">${bars}</div>
    </section>`;
  }

  function stackedBars(title, intro, rows, segments) {
    const legend = segments.map((segment) => `<span data-tone="${esc(segment.tone)}"><i></i>${esc(segment.label)}</span>`).join("");
    const bars = rows.map((row) => {
      const total = segments.reduce((sum, segment) => sum + Number(row.values?.[segment.key] || 0), 0);
      const parts = segments.map((segment) => {
        const value = Number(row.values?.[segment.key] || 0);
        const width = total ? value / total * 100 : 0;
        return `<i data-tone="${esc(segment.tone)}" style="--segment-width:${width}%" title="${esc(`${segment.label} ${fmt(value)}`)}"></i>`;
      }).join("");
      const details = segments.map((segment) => `${segment.label} ${fmt(row.values?.[segment.key] || 0)}`).join(" · ");
      return `<article class="c24-stacked-row"><div><span>${esc(row.label)}</span><strong>${esc(fmt(total))}</strong></div><div class="c24-stacked-track" aria-hidden="true">${parts}</div><small>${esc(details)}</small></article>`;
    }).join("");
    return `<section class="c24-chart-panel">
      <div class="c24-chart-head"><div><h2>${esc(title)}</h2><p>${esc(intro)}</p></div><div class="c24-chart-legend">${legend}</div></div>
      <div class="c24-stacked-chart" role="img" aria-label="${esc(title)}">${bars}</div>
    </section>`;
  }

  function stageFlow(title, intro, stages) {
    const rows = stages.map((stage, index) => {
      const previous = stages[index - 1];
      const connector = previous ? `<div class="c24-stage-connector"><i aria-hidden="true">→</i><strong>保留 ${esc(percentage(stage.value, previous.value))}</strong></div>` : "";
      return `${connector}<article class="c24-stage-card"><span>第 ${index + 1} 段</span><h3>${esc(stage.label)}</h3><strong>${esc(fmt(stage.value))}</strong><p>${esc(stage.note)}</p>${stage.href ? `<a href="${esc(stage.href)}">${esc(stage.linkLabel || "查看详情")} →</a>` : ""}</article>`;
    }).join("");
    return `<section class="c24-chart-panel c24-stage-panel"><div class="c24-chart-head"><div><h2>${esc(title)}</h2><p>${esc(intro)}</p></div><span>箭头上的百分比表示进入下一段的比例</span></div><div class="c24-stage-flow" role="img" aria-label="${esc(`${title}：${stages.map((stage) => `${stage.label} ${fmt(stage.value)}`).join("，")}`)}">${rows}</div></section>`;
  }

  function sourceStatusChart(jobCode = null) {
    const rows = jobCode ? sourceRows(jobCode) : data.sourceHealth.map(effectiveSourceRow);
    const priority = { normal: 1, boundary: 2, attention: 3 };
    const bySource = new Map();
    rows.forEach((row) => {
      const category = (statusCopy[row.status] || [row.status, "boundary"])[1];
      const current = bySource.get(row.source_id);
      if (!current || priority[category] > priority[current]) bySource.set(row.source_id, category);
    });
    const counts = { normal: 0, boundary: 0, attention: 0 };
    bySource.forEach((category) => { counts[category] += 1; });
    return horizontalBars("来源状态一眼看清", "每个来源只按当前最需要关注的状态归类一次，避免同一来源被重复计算。", [
      { label: "正常完成", value: counts.normal, tone: "green" },
      { label: "能力边界", value: counts.boundary, tone: "gray", note: "无数据或当前不支持，不是程序故障" },
      { label: "需要处理", value: counts.attention, tone: "orange", note: "额度、连接、配置或程序问题" },
    ], { axisNote: `共 ${fmt(bySource.size)} 个来源` });
  }

  function evidenceStatusChart() {
    const grouped = {};
    (data.evidenceSummary.counts || []).forEach((row) => {
      grouped[row.evidence_type] ||= {};
      grouped[row.evidence_type][row.status] = Number(row.count || 0);
    });
    const segments = [
      { key: "qualifying", label: evidenceStatusLabels.qualifying, tone: "green" },
      { key: "pending", label: evidenceStatusLabels.pending, tone: "orange" },
      { key: "no_data", label: evidenceStatusLabels.no_data, tone: "gray" },
      { key: "unsupported", label: evidenceStatusLabels.unsupported, tone: "blue-soft" },
      { key: "non_qualifying", label: evidenceStatusLabels.non_qualifying, tone: "ink" },
    ];
    return stackedBars("项目证据构成", "每一行按该证据类型的真实状态构成到 100%；下方表格继续保留精确证据明细。", Object.entries(grouped).map(([type, values]) => ({ label: evidenceTypeLabels[type] || type, values })), segments);
  }

  function trackingStateChart() {
    return horizontalBars("深度跟踪状态", "横条比较当前各状态对象数量；等待公开底线不等于程序失败。", Object.entries(data.trackingStateCounts || {}).map(([label, value]) => ({
      label: trackingLabels[label] || label, value, tone: label === "complete_tracking" ? "green" : "blue",
    })));
  }

  function factorWeightChart() {
    const rows = Object.entries(data.ruleSummary.factorWeights || {}).map(([name, value]) => ({
      label: factorWeightLabels[name] || name, value: Number(value || 0) * 100, displayValue: `${Math.round(Number(value || 0) * 100)}%`, tone: "blue",
    }));
    return horizontalBars("贝叶斯五因子权重", "五项权重合计 100%，只用于同链排序与变化解释，不控制第一关或公开资格。", rows, { scaleMax: 25, axisNote: "本组最高权重为 25%" });
  }

  function databaseChart() {
    const rows = [
      { label: "候选采集库", value: Number(data.database.candidateBytes || 0) / 1024 / 1024 / 1024, displayValue: `${(Number(data.database.candidateBytes || 0) / 1024 / 1024 / 1024).toFixed(2)} GiB`, tone: "blue" },
      { label: "产品主库", value: Number(data.database.mainBytes || 0) / 1024 / 1024 / 1024, displayValue: `${(Number(data.database.mainBytes || 0) / 1024 / 1024 / 1024).toFixed(2)} GiB`, tone: "green" },
    ];
    return horizontalBars("数据库体量对比", "候选采集库与产品主库职责不同，横条只比较当前文件体量，不代表运行速度。", rows, { axisNote: "单位 GiB，右侧为精确值" });
  }

  function recentRunChart() {
    const counts = {};
    (data.recentRuns || []).forEach((row) => { counts[row.state || "unknown"] = (counts[row.state || "unknown"] || 0) + 1; });
    const labels = { completed: "已完成", running: "运行中", retrying: "重试中", failed: "失败", paused: "已暂停", partial: "部分完成", unknown: "未标明" };
    return horizontalBars("最近运行状态", `只统计下方最近 ${fmt(data.recentRuns.length)} 条运行记录。`, Object.entries(counts).map(([state, value]) => ({
      label: labels[state] || state, value, tone: state === "completed" ? "green" : state === "failed" ? "red" : "blue",
    })));
  }

  function progressMarkup(prefix, label) {
    return `<div id="${prefix}Progress" class="c24-progress" data-state="loading">
      <div class="c24-progress-head"><span>${esc(label)}</span><strong id="${prefix}Percent">—</strong></div>
      <div class="c24-progress-track" id="${prefix}Track" role="progressbar" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="100"><i class="c24-progress-fill" id="${prefix}Bar"></i></div>
      <div class="c24-progress-scale"><span>0%</span><strong id="${prefix}Scale">正在读取进度</strong><span>100%</span></div>
      <div class="c24-progress-breakdown"><span>已完成 <strong id="${prefix}Completed">0</strong></span><span>剩余 <strong id="${prefix}Remaining">0</strong></span></div>
      <p id="${prefix}Message">正在读取当前状态</p>
    </div>`;
  }

  function updateProgress(prefix, { completed = 0, total = 0, message = "", state = "idle", unit = "项" }) {
    const safeTotal = Math.max(0, Number(total || 0));
    const safeCompleted = Math.max(0, Math.min(safeTotal || Number(completed || 0), Number(completed || 0)));
    const remaining = safeTotal ? Math.max(0, safeTotal - safeCompleted) : 0;
    const ratio = safeTotal ? Math.min(100, safeCompleted / safeTotal * 100) : 0;
    const root = $(`#${prefix}Progress`);
    if (!root) return;
    root.dataset.state = state || "idle";
    $(`#${prefix}Percent`).textContent = safeTotal ? `${ratio.toFixed(ratio < 10 ? 2 : 1)}%` : "—";
    const bar = $(`#${prefix}Bar`);
    bar.style.width = `${ratio}%`;
    bar.style.minWidth = ratio > 0 ? "3px" : "0";
    $(`#${prefix}Scale`).textContent = safeTotal ? `${fmt(safeCompleted)} / ${fmt(safeTotal)} ${unit}` : "本轮没有可量化总数";
    $(`#${prefix}Completed`).textContent = `${fmt(safeCompleted)} ${unit}`;
    $(`#${prefix}Remaining`).textContent = safeTotal ? `${fmt(remaining)} ${unit}` : "—";
    $(`#${prefix}Message`).textContent = message || "当前没有正在运行的进度";
    const track = $(`#${prefix}Track`);
    if (safeTotal) track.setAttribute("aria-valuenow", ratio.toFixed(2)); else track.removeAttribute("aria-valuenow");
    track.setAttribute("aria-valuetext", safeTotal ? `已完成 ${fmt(safeCompleted)} ${unit}，共 ${fmt(safeTotal)} ${unit}，剩余 ${fmt(remaining)} ${unit}` : "本轮没有可量化总数");
  }

  function renderFunnel(funnel, isScreening) {
    const stages = funnel.stages || [];
    const transitions = funnel.transitions || [];
    const reasonLabel = { not_passed: "本轮未通过", waiting: "等待处理" };
    const transitionCard = (transition, previousCount) => {
      const reasons = transition.primaryReasons || [];
      return `<article class="c24-funnel-rule" data-kind="${esc(transition.kind || "screening")}">
        <div class="c24-funnel-rule-head"><span>从本层到下一层</span><strong>进入 ${esc(fmt(transition.passed))}</strong></div>
        <h3>${esc(transition.title)}</h3>
        <ul class="c24-funnel-rules">${(transition.rules || []).map((rule) => `<li>${esc(rule)}</li>`).join("")}</ul>
        <div class="c24-funnel-totals"><span data-kind="passed">进入下一层<strong>${esc(fmt(transition.passed))}</strong></span><span data-kind="not_passed">本轮未通过<strong>${esc(fmt(transition.notPassed))}</strong></span><span data-kind="waiting">等待处理<strong>${esc(fmt(transition.waiting))}</strong></span></div>
        <p class="c24-funnel-equation">${esc(fmt(previousCount))} = ${esc(fmt(transition.passed))} 进入下一层 + ${esc(fmt(transition.notPassed))} 本轮未通过 + ${esc(fmt(transition.waiting))} 等待处理</p>
        ${reasons.length ? `<div class="c24-funnel-reasons">${reasons.map((reason) => `<div data-kind="${esc(reason.kind)}"><span>${esc(reasonLabel[reason.kind] || reason.kind)} · ${esc(reason.label)}</span><strong>${esc(fmt(reason.count))}</strong></div>`).join("")}</div>` : ""}
        <p class="c24-funnel-action">${esc(transition.manualAction || "")}</p>
      </article>`;
    };
    const rows = stages.map((stage, index) => {
      const width = Math.max(46, 100 - index * 18);
      const transition = transitions[index];
      const finalText = isScreening
        ? "最终进入凸性跟踪；通过第一关不等于已经成为公开机会。"
        : "这是生命周期结果，不是额外的凸性淘汰层。";
      return `<div class="c24-funnel-stage-wrap"><article class="c24-funnel-stage" data-kind="${esc(stage.kind || "stage")}" style="--stage-width:${width}%"><small>漏斗第 ${index + 1} 层</small><span>${esc(stage.label)}</span><strong>${esc(fmt(stage.count))}</strong></article>${transition ? '<i aria-hidden="true">↓</i>' : ""}</div>${transition ? transitionCard(transition, stage.count) : `<article class="c24-funnel-final"><span>当前漏斗结果</span><strong>${esc(fmt(stage.count))}</strong><p>${esc(finalText)}</p></article>`}`;
    }).join("");
    const outside = (funnel.outsideFunnel || []).filter((row) => Number(row.count || 0) >= 0);
    return `<section class="c24-funnel-panel">
      <div class="c24-admin-section-title"><div><h2>${isScreening ? "90 天候选筛选漏斗" : "凸性跟踪漏斗"}</h2><p>所有数量来自同一份完整快照；每一步都把进入、未通过和等待分开对账。</p></div><span>图形宽度表示层级，不按数量比例缩放</span></div>
      <div class="c24-funnel-grid">${rows}</div>
      ${outside.length ? `<section class="c24-funnel-outside"><div><h3>漏斗之外：等待与未通过</h3><p>这些状态不再冒充一个新的漏斗层，避免把等待处理误看成规则淘汰。</p></div><div>${outside.map((row) => `<article data-kind="${esc(row.kind)}"><span>${esc(row.label)}</span><strong>${esc(fmt(row.count))}</strong><p>${esc(row.detail)}</p><small>${esc(row.manualAction)}</small></article>`).join("")}</div></section>` : ""}
    </section>`;
  }

  async function api(path, payload) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `请求失败（${response.status}）`);
    return result;
  }

  function sourceOwner(sourceId) {
    if (screeningSources.has(sourceId)) return "screening";
    if (sharedSources.has(sourceId)) return "shared";
    return "convexity_tracking";
  }

  function effectiveSourceRow(row) {
    if (row.source_id === "project_website_identity" && row.status === "configuration_missing") {
      return { ...row, status: "unsupported", plain_reason: "项目网站拒绝自动访问，属于来源能力边界；重复更新不会改变。" };
    }
    return row;
  }

  function sourceRows(jobCode) {
    return data.sourceHealth.map(effectiveSourceRow).filter((row) => { const owner = sourceOwner(row.source_id); return owner === jobCode || owner === "shared"; });
  }

  function sourceCards(jobCode) {
    const rows = sourceRows(jobCode);
    const grouped = { attention: [], boundary: [], normal: [] };
    const bySource = new Map();
    rows.forEach((row) => {
      if (!bySource.has(row.source_id)) bySource.set(row.source_id, []);
      bySource.get(row.source_id).push(row);
    });
    bySource.forEach((sourceRowsForId, sourceId) => {
      const buckets = { attention: [], boundary: [], normal: [] };
      sourceRowsForId.forEach((row) => {
        const copy = statusCopy[row.status] || [row.status, "boundary"];
        buckets[copy[1]].push({ ...row, userLabel: copy[0] });
      });
      const key = buckets.attention.length ? "attention" : buckets.boundary.length ? "boundary" : "normal";
      const chosen = buckets[key];
      const otherCount = sourceRowsForId.reduce((sum, row) => sum + Number(row.object_count || 0), 0) - chosen.reduce((sum, row) => sum + Number(row.object_count || 0), 0);
      const recoverableCount = sourceRowsForId.filter((row) => recoverableStates.has(row.status)).reduce((sum, row) => sum + Number(row.object_count || 0), 0);
      const configurationCount = sourceRowsForId.filter((row) => row.status === "configuration_missing").reduce((sum, row) => sum + Number(row.object_count || 0), 0);
      const programCount = sourceRowsForId.filter((row) => row.status === "program_failure").reduce((sum, row) => sum + Number(row.object_count || 0), 0);
      const stateSummary = sourceRowsForId.map((row) => `${(statusCopy[row.status] || [row.status])[0]} ${fmt(row.object_count || 0)}`).join(" · ");
      const actionGuide = [
        recoverableCount ? `其中 ${fmt(recoverableCount)} 个属于可恢复的额度或连接问题；系统会按断点自动重试，也可以立即只重试这些范围。` : "",
        configurationCount ? `${fmt(configurationCount)} 个配置问题不会靠重复更新恢复，需要先补齐程序配置。` : "",
        programCount ? `${fmt(programCount)} 个程序问题需要先修复程序，单项更新不会消除。` : "",
      ].filter(Boolean).join(" ");
      grouped[key].push({
        source_id: sourceId,
        source_name: sourceNames[sourceId] || sourceId,
        object_count: chosen.reduce((sum, row) => sum + Number(row.object_count || 0), 0),
        userLabel: [...new Set(chosen.map((row) => row.userLabel))].join("；"),
        plain_reason: [...new Set(chosen.map((row) => row.plain_reason).filter(Boolean))].join("；"),
        updated_at: chosen.map((row) => row.updated_at).filter(Boolean).sort().at(-1),
        other_count: otherCount,
        recoverable_count: recoverableCount,
        state_summary: stateSummary,
        action_guide: actionGuide,
      });
    });
    const section = (key, title, intro) => `<section class="c24-source-group"><div class="c24-admin-section-title"><div><h2>${esc(title)}</h2><p>${esc(intro)}</p></div><strong>${grouped[key].reduce((sum, row) => sum + Number(row.object_count || 0), 0)}</strong></div>${grouped[key].length ? `<div class="c24-source-list">${grouped[key].map((row) => `<article data-source-status="${esc(key)}"><div><strong>${esc(row.source_name)}</strong><span>${esc(row.userLabel)}</span></div><code>${esc(row.source_id)}</code><p>${esc(row.action_guide || row.plain_reason || "当前没有更多说明。")}</p><small>${esc(row.state_summary)}${row.other_count ? ` · 另有 ${esc(fmt(row.other_count))} 个范围处于其他状态` : ""} · 最近状态 ${esc(fmtTime(row.updated_at))}</small>${key === "attention" && retryable.has(row.source_id) && row.recoverable_count ? `<button type="button" data-source-retry="${esc(row.source_id)}">只重试可恢复范围（${esc(fmt(row.recoverable_count))}）</button>` : ""}</article>`).join("")}</div>` : `<p class="c24-admin-empty">当前没有这一类来源状态。</p>`}</section>`;
    return section("attention", "需要处理", "只统计额度、连接、配置和程序问题；可执行时提供单项重试。") + section("boundary", "来源能力边界", "没有数据或当前不支持不等于程序故障，也不提供无意义重试。") + section("normal", "正常完成", "最近一次已完成的来源范围。") ;
  }

  function candidateProductionPanel() {
    return `<section class="c24-job-panel c24-backbone-panel" id="c24BackbonePanel">
      <div class="c24-admin-section-title"><div><h2>历史底座扫描</h2><p>这是之前授权的 459 万历史候选处理，不会因版本升级丢失；扫描与第一关各自保存断点。</p></div><span id="c24BackboneState">正在读取</span></div>
      ${progressMarkup("c24Backbone", "历史候选扫描进度")}
      <section class="c24-admin-metrics c24-backbone-metrics">
        ${metric("历史候选总数", 0)}${metric("已扫描历史候选", 0)}${metric("剩余历史候选", 0)}${metric("已完成第一关", 0)}
      </section>
      <dl class="c24-job-facts" id="c24BackboneFacts"></dl>
      <div class="c24-job-controls"><button type="button" id="c24BackboneResume">继续历史底座</button><button type="button" id="c24BackbonePause" class="is-secondary">在分片断点暂停</button></div>
      <p id="c24BackboneFeedback" class="c24-job-feedback" aria-live="polite"></p>
    </section>`;
  }

  function jobPage(jobCode) {
    const isScreening = jobCode === "screening";
    const funnel = isScreening ? data.candidateFunnel : data.trackingFunnel;
    main.innerHTML = head("更新中心", isScreening ? "90 天候选" : "凸性跟踪", isScreening ? "第一关只判断是否值得继续检查；通过不等于公开机会。手动与自动共用同一断点和恢复记录。" : "第二关检查风险、退出、项目证据和结构；只有新的完整结果达到公开底线后才发布。") + `
      ${renderFunnel(funnel, isScreening)}
      <section class="c24-job-panel" id="c24JobControl"><div class="c24-admin-section-title"><div><h2>本作业运行与控制</h2><p>后台隐藏运行；关闭软件不会中断，关机后由同一任务从断点恢复。</p></div><span id="c24JobState">正在读取</span></div>${progressMarkup("c24Job", isScreening ? "90 天候选本轮进度" : "凸性跟踪本轮进度")}<div class="c24-job-controls"><button type="button" id="c24RunNow">立即更新</button><button type="button" id="c24Resume">继续上次未完成</button><button type="button" id="c24PauseCurrent" class="is-secondary">在安全点暂停当前任务</button><button type="button" id="c24PauseSchedule" class="is-secondary">暂停自动新周期</button><label>自动频率<select id="c24Frequency"><option value="manual">仅手动</option><option value="1">每小时</option><option value="3">每 3 小时</option><option value="6">每 6 小时</option><option value="12">每 12 小时</option><option value="24">每天</option></select></label></div><p id="c24JobFeedback" class="c24-job-feedback" aria-live="polite"></p><dl id="c24JobFacts" class="c24-job-facts"></dl></section>
      ${isScreening ? candidateProductionPanel() : ""}
      ${sourceCards(jobCode)}`;
    let pollTimer;
    const feedback = $("#c24JobFeedback");
    const showFeedback = (message, error = false) => { feedback.textContent = message; feedback.dataset.error = error ? "true" : "false"; };
    const renderCandidateProduction = (runtime) => {
      if (!isScreening) return false;
      const production = runtime.candidateProduction || {};
      const historical = production.queueSummaries?.historical_backlog || {};
      const total = Number(historical.queuedCandidateCount || 0);
      const scanned = Number(historical.localScannedCount || 0);
      const pendingPartitions = (production.partitions || []).filter((row) => row.queue_name === "historical_backlog" && row.state !== "completed");
      const remaining = Math.max(0, total - scanned);
      const completedPartitions = (production.partitions || []).filter((row) => row.queue_name === "historical_backlog" && row.state === "completed").reduce((sum, row) => sum + Number(row.count || row.partition_count || 0), 0);
      const firstGatePending = Number(historical.firstGatePendingCount || 0);
      const firstGateOutsideWindow = Number(historical.firstGateOutsideWindowCount || 0);
      const firstGateDeferred = Number(historical.firstGateDeferredCount || 0);
      const currentState = production.currentRun?.state || production.state;
      const producerRunning = ["running", "retrying"].includes(currentState);
      const screeningHandoff = runtime.jobs?.screening?.state === "running" && currentState === "paused";
      const scanFinished = remaining === 0 && total > 0;
      const allFinished = scanFinished && firstGatePending === 0;
      const stateText = allFinished ? "历史底座与可执行第一关均已完成" : scanFinished ? "历史扫描已完成，第一关仍在核验" : screeningHandoff ? "正为 90 天候选更新让出数据库" : producerRunning ? "正在隐藏后台扫描" : currentState === "paused" ? "已在安全断点暂停" : "等待继续";
      $("#c24BackboneState").textContent = stateText;
      const finishedNote = firstGateOutsideWindow
        ? `历史扫描和可执行的第一关已完成；${fmt(firstGateOutsideWindow)} 条在核验前已超过 90 天，不再等待。`
        : `历史扫描和可执行的第一关均已完成。`;
      const progressMessage = scanFinished && firstGatePending ? `历史扫描已达 100%；还有 ${fmt(firstGatePending)} 条具备完整批次、正在等待第一关核验。` : scanFinished ? finishedNote : screeningHandoff ? `90 天候选更新完成后会自动续跑；历史断点未丢失，剩余 ${fmt(remaining)} 条。` : `${fmt(scanned)} / ${fmt(total)}，剩余 ${fmt(remaining)} 条历史候选。`;
      updateProgress("c24Backbone", { completed: scanned, total, message: progressMessage, state: allFinished ? "completed" : producerRunning || screeningHandoff ? "running" : currentState === "paused" ? "paused" : "idle", unit: "条" });
      const values = [total, scanned, remaining, production.historicalFirstGateProcessedCount || 0];
      document.querySelectorAll("#c24BackbonePanel .c24-backbone-metrics strong").forEach((node, index) => { node.textContent = fmt(values[index]); });
      const current = production.currentPartition || {};
      const eta = Number(production.etaSeconds);
      $("#c24BackboneFacts").innerHTML = `<div><dt>已完成分片</dt><dd>${esc(fmt(completedPartitions))}</dd></div><div><dt>待处理分片</dt><dd>${esc(fmt(pendingPartitions.reduce((sum, row) => sum + Number(row.count || row.partition_count || 0), 0)))}</dd></div><div><dt>已退出 90 天窗口</dt><dd>${esc(fmt(firstGateOutsideWindow))}</dd></div><div><dt>其他未形成可执行批次</dt><dd>${esc(fmt(Math.max(0, firstGateDeferred - firstGateOutsideWindow)))}</dd></div><div><dt>当前分片</dt><dd>${esc(current.partition_id || "当前在断点")}</dd></div><div><dt>最近断点</dt><dd>${esc(fmtTime(current.last_checkpoint_at || current.last_heartbeat_at || production.currentRun?.updated_at))}</dd></div><div><dt>预计剩余</dt><dd>${Number.isFinite(eta) && eta >= 0 ? `${Math.floor(eta / 60)} 分钟（估算）` : "暂无法可靠估算"}</dd></div>`;
      $("#c24BackboneResume").disabled = allFinished || producerRunning || screeningHandoff;
      $("#c24BackbonePause").disabled = !producerRunning;
      return producerRunning || screeningHandoff;
    };
    const renderRuntime = (runtime) => {
      const job = runtime.jobs?.[jobCode] || {};
      const progress = job.progress || {};
      const completed = Number(progress.completed || 0), total = Number(progress.total || 0);
      $("#c24JobState").textContent = ({ running: "正在后台更新", completed: "最近一次已完成", partial: "本轮已停在安全断点", failed: "最近一次未完成", paused: "已在安全点暂停" })[job.state] || "等待下一次运行";
      const message = runtimeMessage(job);
      updateProgress("c24Job", { completed, total, message, state: job.state || "idle", unit: isScreening ? "个候选" : "个跟踪对象" });
      $("#c24JobFacts").innerHTML = `<div><dt>最近完成</dt><dd>${esc(fmtTime(job.lastCompletedAt))}</dd></div><div><dt>最近心跳</dt><dd>${esc(fmtTime(job.lastHeartbeatAt))}</dd></div><div><dt>下一次运行</dt><dd>${esc(fmtTime(job.nextDueAt))}</dd></div><div><dt>恢复断点</dt><dd>${esc(fmtCheckpoint(job.checkpoint))}</dd></div>`;
      const config = runtime.config?.jobs?.[jobCode] || {};
      $("#c24Frequency").value = config.mode === "manual" ? "manual" : String(config.intervalHours || 24);
      $("#c24PauseSchedule").dataset.paused = String(Boolean(config.paused));
      $("#c24PauseSchedule").textContent = config.paused ? "恢复自动新周期" : "暂停自动新周期";
      const productionActive = renderCandidateProduction(runtime);
      if (job.state === "running" || productionActive) pollTimer = setTimeout(loadRuntime, 2000);
    };
    const loadRuntime = async () => { clearTimeout(pollTimer); try { const response = await fetch("/api/c2.4/status", { cache: "no-store" }); renderRuntime(await response.json()); } catch (error) { showFeedback(`无法读取当前进度：${error.message}`, true); } };
    const launch = async (trigger) => { showFeedback("已提交，正在确认后台单实例状态……"); try { const result = await api("/api/c2.4/run", { jobCode, trigger }); showFeedback(result.message || "任务已在隐藏后台启动；本页会继续显示进度。"); renderRuntime(result.runtime || {}); setTimeout(loadRuntime, 800); } catch (error) { showFeedback(error.message, true); } };
    $("#c24RunNow").onclick = () => launch("manual");
    $("#c24Resume").onclick = () => launch("resume");
    $("#c24PauseCurrent").onclick = async () => { showFeedback("暂停请求已提交，任务会在安全点保存断点。"); try { const result = await api("/api/c2.4/pause-current", { jobCode, paused: true }); renderRuntime(result.runtime || {}); } catch (error) { showFeedback(error.message, true); } };
    $("#c24PauseSchedule").onclick = async () => { const paused = $("#c24PauseSchedule").dataset.paused !== "true"; showFeedback("正在保存本作业的新周期设置……"); try { const result = await api("/api/c2.4/scheduler", { jobCode, paused }); showFeedback(paused ? "自动新周期已暂停；手动更新仍可使用。" : "自动新周期已恢复，只影响当前这项作业。"); renderRuntime(result.runtime || {}); } catch (error) { showFeedback(error.message, true); } };
    $("#c24Frequency").onchange = async (event) => { const value = event.target.value; showFeedback("正在保存本作业设置……"); try { const changes = value === "manual" ? { mode: "manual" } : { mode: "automatic", intervalHours: Number(value), paused: false }; const result = await api("/api/c2.4/scheduler", { jobCode, ...changes }); showFeedback("设置已保存，只影响当前这一项作业。"); renderRuntime(result.runtime || {}); } catch (error) { showFeedback(error.message, true); } };
    if (isScreening) {
      const backboneFeedback = $("#c24BackboneFeedback");
      $("#c24BackboneResume").onclick = async () => { backboneFeedback.textContent = "正在从已有分片断点继续……"; try { const result = await api("/api/c2.2/candidate-production/run", { queue: "historical_backlog" }); backboneFeedback.textContent = result.message || "历史底座已在隐藏后台继续。"; setTimeout(loadRuntime, 800); } catch (error) { backboneFeedback.textContent = error.message; backboneFeedback.dataset.error = "true"; } };
      $("#c24BackbonePause").onclick = async () => { backboneFeedback.textContent = "暂停请求已提交，会先保存当前分片断点。"; try { const result = await api("/api/c2.2/candidate-production/pause", { paused: true }); backboneFeedback.textContent = result.message || "历史底座会在安全断点暂停。"; setTimeout(loadRuntime, 800); } catch (error) { backboneFeedback.textContent = error.message; backboneFeedback.dataset.error = "true"; } };
    }
    document.querySelectorAll("[data-source-retry]").forEach((button) => button.onclick = async () => { const originalText = button.textContent; button.disabled = true; button.textContent = "已提交"; try { await api("/api/c2.4/run", { jobCode, trigger: "manual", sourceId: button.dataset.sourceRetry }); showFeedback(`已单独提交 ${button.dataset.sourceRetry} 的可恢复范围，不会重跑其他来源。`); setTimeout(loadRuntime, 800); } catch (error) { showFeedback(error.message, true); button.disabled = false; button.textContent = originalText; } });
    loadRuntime();
  }

  function overview() {
    const r = data.reconciliation, attention = data.sourceHealth.map(effectiveSourceRow).filter((row) => needsAction.has(row.status)).reduce((sum, row) => sum + Number(row.object_count || 0), 0);
    main.innerHTML = head("当前主链一眼看清", "工作台概览", "只显示 C2.4 当前在用的两项作业、三份业务快照和真实待处理事项；旧版本数字不会混入。") + `
      <section class="c24-admin-metrics">${metric("90 天候选当前队列", r.firstGateQueueCount, "已经进入深度跟踪，不是公开机会")}${metric("凸性跟踪对象", r.trackingCount, "包含 90 天内和 90 天后持续跟踪")}${metric("已达到公开底线", r.publicCount, "机会中心完整公开集合")}${metric("需要处理的来源范围", attention, "能力边界不计入")}</section>
      ${stageFlow("三段现役主链", "从第一关通过，到深度跟踪，再到机会中心公开；三个数量来自同一份完整快照。", [
        { label: "90 天候选", value: r.firstGateQueueCount, note: "通过四项基础检查，交给第二关继续核验。", href: "new-token-update.html", linkLabel: "查看候选作业" },
        { label: "凸性跟踪", value: r.trackingCount, note: "检查风险、退出、项目证据和四条强路径。", href: "update-center.html", linkLabel: "查看跟踪作业" },
        { label: "机会中心", value: r.publicCount, note: "只公开新的完整结果已经达到公开底线的项目。", href: "candidate-pool.html", linkLabel: "打开机会中心" },
      ])}
      ${horizontalBars("六链公开结果", "每条链独立展示当前公开项目数量；零结果保持为真实零。", Object.entries(r.chainPublicCounts).map(([chain, count]) => ({ label: chainLabels[chain] || chain, value: count, tone: "blue" })))}
      <section class="c24-admin-two"><article><h2>集合对账</h2><p>以下四项都应为 0；非 0 才表示主链交接不一致。</p><dl class="c24-job-facts"><div><dt>公开但未跟踪</dt><dd>${r.differences.publicNotTracked.length}</dd></div><div><dt>90 天内跟踪但未在初筛队列</dt><dd>${r.differences.newTrackedNotQueued.length}</dd></div><div><dt>跟踪但无第一关历史</dt><dd>${r.differences.trackedNotFirstGateHistory.length}</dd></div><div><dt>持续跟踪但历史不完整</dt><dd>${r.differences.continuedMissingHistory.length}</dd></div></dl></article><article><h2>怎么看这张图</h2><p>第一段与第二段数量相同，表示第一关交接已完成；第二段到第三段明显收窄，表示绝大多数对象仍在第二关补齐风险、退出、证据或结构条件，不代表它们被第一关删除。</p><a href="discovery-funnel.html">查看完整发现与筛选漏斗</a></article></section>`;
  }

  function table(headers, rows) {
    return `<div class="c24-admin-table-wrap"><table class="c24-admin-table"><thead><tr>${headers.map((value) => `<th>${esc(value)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  function genericPage() {
    const route = data.routeInventory.find((row) => row.path.endsWith(`/${page}`)) || {};
    const routeTitle = route.c2_4Location ? route.c2_4Location.split("/").pop() : "当前功能";
    const candidates = new Set(["project-master-pool.html", "source-discovery.html", "network-discovery.html", "discovery-funnel.html", "weak-signal-inbox.html", "scan-center.html"]);
    const sources = new Set(["source-registry.html", "source-adapter.html"]);
    const evidence = new Set(["evidence-ledger.html", "high-value-sources.html"]);
    const tracking = new Set(["monitoring-infrastructure.html", "catalyst-paths.html", "action-gaps.html"]);
    const rules = new Set(["decision-quality.html", "rules-replay.html", "screening-console.html", "four-layer-screening.html", "gold-calibration.html", "real-case-calibration.html", "model-acceptance.html"]);
    let body = "";
    if (candidates.has(page)) {
      body = `${stageFlow("候选到公开的当前路径", "先进入 90 天候选，再进入凸性跟踪，最后只有达到公开底线的项目进入机会中心。", [
        { label: "第一关当前队列", value: data.reconciliation.firstGateQueueCount, note: "已经通过四项基础检查。" },
        { label: "深度跟踪对象", value: data.reconciliation.trackingCount, note: "正在检查风险、退出、证据与结构。" },
        { label: "已公开", value: data.reconciliation.publicCount, note: "当前机会中心完整公开集合。" },
      ])}${horizontalBars("六链深度跟踪规模", "按链比较正在第二关继续处理的对象数量；右侧表格保留三段精确值。", Object.entries(data.chainFunnel).map(([chain, row]) => ({ label: chainLabels[chain] || chain, value: row.tracking, tone: "blue" })))}${table(["链", "第一关当前队列", "深度跟踪", "已公开"], Object.entries(data.chainFunnel).map(([chain, row]) => `<tr><td>${esc(chainLabels[chain] || chain)}</td><td>${esc(fmt(row.firstGateQueue))}</td><td>${esc(fmt(row.tracking))}</td><td>${esc(fmt(row.public))}</td></tr>`))}`;
    } else if (sources.has(page)) {
      body = sourceStatusChart() + sourceCards("screening") + sourceCards("convexity_tracking");
    } else if (evidence.has(page)) {
      body = evidenceStatusChart() + table(["项目", "链", "证据类型", "来源", "时间", "边界"], data.evidenceSummary.recentQualifying.map((row) => `<tr><td>${esc(row.canonical_name || row.asset_id || row.candidate_id)}</td><td>${esc(chainLabels[row.network_id] || row.network_id)}</td><td>${esc(evidenceTypeLabels[row.evidence_type] || row.evidence_type)}</td><td>${row.source_url ? `<a href="${esc(row.source_url)}" target="_blank" rel="noreferrer">${esc(row.source_name)}</a>` : esc(row.source_name)}</td><td>${esc(fmtTime(row.observed_at))}</td><td>${esc(row.boundary_note)}</td></tr>`));
    } else if (tracking.has(page)) {
      body = `${trackingStateChart()}<div class="c24-admin-callout"><h2>当前监控边界</h2><p>这里只监控已经通过第一关的同一 assetId，以及在 90 天内完成两关后转入持续跟踪的资产；不会从普通老项目市场补入对象。</p><a href="update-center.html">打开凸性跟踪更新</a></div>`;
    } else if (rules.has(page)) {
      const rule = data.ruleSummary;
      body = `${factorWeightChart()}<section class="c24-admin-two"><article><h2>当前规则版本</h2><p>${esc(rule.ruleVersion)}</p><p>权重只影响同链相对排序，缺失指标退出该指标分母，不补默认 50 分。</p></article><article><h2>固定退出边界</h2><p>公开卖出损失上限 ${esc(rule.sellQuote.publicMaximumLossPct)}%；达到 ${esc(rule.sellQuote.severeImmediateExitLossPctGte)}% 立即撤下。总分不控制第一关、公开资格或凸性线索。</p><a href="candidate-pool.html?view=method">查看普通用户说明</a></article></section>`;
    } else if (page === "data-backbone.html" || page === "data-dictionary.html") {
      body = `${databaseChart()}${recentRunChart()}${table(["作业", "状态", "阶段", "开始", "完成"], data.recentRuns.map((row) => `<tr><td>${esc(row.run_id)}</td><td>${esc(row.state)}</td><td>${esc(row.stage)}</td><td>${esc(fmtTime(row.started_at))}</td><td>${esc(fmtTime(row.finished_at))}</td></tr>`))}`;
    } else if (page === "manual-review.html") {
      body = `<div class="c24-admin-callout"><h2>当前版本不使用逐项目人工复核</h2><p>该历史功能作为回滚资产保留，但没有现役入口，也不会读取或发布旧人工复核数据。只有产品定位重新冻结后才可启用。</p><a href="workbench.html">返回工作台概览</a></div>`;
    } else {
      body = `<div class="c24-admin-callout"><h2>该继承功能已接入当前主链</h2><p>${esc(route.acceptance || "当前页面只使用C2.4完整快照，不再显示旧版本静态数字。")}</p><a href="workbench.html">返回工作台概览</a></div>`;
    }
    main.innerHTML = head("现役主干功能", routeTitle, route.acceptance || "本页已按 C2.4 当前数据与用语适配；没有可用当前数据的旧栏目不会显示。") + body;
  }

  if (page === "workbench.html") overview();
  else if (page === "new-token-update.html") jobPage("screening");
  else if (page === "update-center.html") jobPage("convexity_tracking");
  else genericPage();
})();
