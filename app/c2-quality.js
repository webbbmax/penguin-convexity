(function c20QualityWorkspace() {
  "use strict";

  const quality = window.PENGUIN_CONVEXITY_DECISION_QUALITY;
  const main = document.getElementById("c2QualityMain");
  if (!quality || !main) return;
  const pageSize = 20;
  const stateKey = "penguin.convexity.c2.quality";
  const esc = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const safe = (value, fallback = "资料待补充") => {
    const result = String(value == null ? "" : value).trim();
    return result || fallback;
  };
  const number = (value) => Number(value || 0).toLocaleString("zh-CN");
  const dateTime = (value, fallback = "时间待补充") => {
    if (!value) return fallback;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? safe(value, fallback) : date.toLocaleString("zh-CN", { hour12: false });
  };
  const stateRead = () => { try { return JSON.parse(localStorage.getItem(stateKey) || "{}") || {}; } catch (_error) { return {}; } };
  const stateWrite = (patch) => { try { localStorage.setItem(stateKey, JSON.stringify({ ...stateRead(), ...patch })); } catch (_error) { /* optional preference */ } };
  const currentState = stateRead();
  let state = { dimension: currentState.dimension || "all", owner: ["system", "human"].includes(currentState.owner) ? currentState.owner : "all", search: currentState.search || "", sort: currentState.sort || "nextReviewAt", page: Number(currentState.page) || 1, scrollTop: Number(currentState.scrollTop) || 0 };

  function notice() {
    const status = quality.dataStatus || {};
    const reconciliation = quality.reconciliation || {};
    const level = status.state && !["valid", "success", "ready"].includes(status.state) ? "is-error" : "";
    const normal = ["valid", "success", "ready"].includes(status.state) && reconciliation.countsMatch;
    return `<div class="c2-data-notice ${level}" role="status" ${normal ? "hidden" : ""}><strong>${esc(safe(status.label, "判断质量快照"))}</strong><br>${esc(safe(status.message, "只读质量快照已载入。"))}<br><small>生成时间：${esc(dateTime(quality.generatedAt))}；项目/案例对账：${reconciliation.countsMatch ? "通过" : "需检查"}</small></div>`;
  }
  async function statusNotice() {
    try {
      const response = await fetch("/api/c2.0/status", { cache: "no-store" });
      if (!response.ok) throw new Error("status");
      const status = await response.json();
      if (status.state && !["success", "valid", "ready"].includes(status.state)) {
        const node = main.querySelector(".c2-data-notice");
        if (node) { node.hidden = false; node.classList.add("is-error"); node.innerHTML = `<strong>判断质量快照状态：${esc(safe(status.state))}</strong><br>${esc(safe(status.error || status.message, "当前继续展示最近一次有效快照。"))}`; }
      }
    } catch (_error) { /* static view remains usable */ }
  }
  function funnelMarkup() {
    return (quality.coverageFunnel || []).map((row) => {
      const total = Math.max(1, Number(row.total || 0));
      const closed = Number(row.closed || 0); const system = Number(row.systemPending || 0); const human = Number(row.humanPending || 0);
      const width = (value) => `${Math.max(0, Math.min(100, value / total * 100))}%`;
      return `<div class="c2-funnel-row"><div class="c2-funnel-label"><strong>${esc(row.label)}</strong><small>${esc(row.definition)}</small></div><div class="c2-funnel-segments"><button type="button" style="width:${width(closed)}" data-funnel-dimension="${esc(row.dimension)}" data-funnel-owner="closed" aria-disabled="true" title="已闭环 ${number(closed)}，当前无需处理">${closed ? number(closed) : ""}</button><button type="button" class="system" style="width:${width(system)}" data-funnel-dimension="${esc(row.dimension)}" data-funnel-owner="system" title="待系统处理 ${number(system)}">${system ? number(system) : ""}</button><button type="button" class="human" style="width:${width(human)}" data-funnel-dimension="${esc(row.dimension)}" data-funnel-owner="human" title="待人工处理 ${number(human)}">${human ? number(human) : ""}</button></div><div class="c2-funnel-total">${number(row.total)}</div></div>`;
    }).join("");
  }
  function blockerRows() {
    return (quality.blockerRanking || []).slice(0, 10).map((row) => `<tr><td><strong>${esc(safe(row.name))}</strong><small>${esc(safe(row.dimensionLabel))}</small></td><td>${number(row.projectCount)}<small>影响前台结论 ${number(row.frontConclusionCount)}</small></td><td>${esc(safe(row.ownerLabel))}<small>${esc(safe(row.statusLabel))}</small></td><td>${esc(dateTime(row.latestExecutionAt, "尚无执行时间"))}<small>复查：${esc(dateTime(row.nextReviewAt, "尚未形成复查时间"))}</small></td><td>${esc(safe(row.nextStep))}<small>${esc(safe(row.reason))}</small></td><td><a href="${esc(safe(row.targetUrl, "workbench.html"))}">前往处理</a></td></tr>`).join("");
  }
  function metricRows() {
    return (quality.qualityMetrics || []).map((row) => {
      const value = row.value == null || !Number.isFinite(Number(row.value)) ? "无法计算" : `${(Number(row.value) * 100).toFixed(1)}%`;
      return `<tr><td><strong>${esc(safe(row.label))}</strong></td><td>${number(row.numerator)} / ${number(row.denominator)}</td><td>${esc(value)}</td><td>${esc(safe(row.definition))}<small>${esc(dateTime(row.generatedAt))}</small></td></tr>`;
    }).join("");
  }
  function queueRows() {
    const textQuery = state.search.trim().toLowerCase();
    let list = (quality.closureQueue || []).filter((row) => (state.dimension === "all" || row.dimension === state.dimension) && (state.owner === "all" || row.owner === state.owner) && (!textQuery || JSON.stringify(row).toLowerCase().includes(textQuery)));
    list = list.sort((left, right) => {
      if (state.sort === "projectName") return safe(left.projectName).localeCompare(safe(right.projectName), "zh-CN");
      if (state.sort === "dimension") return safe(left.dimensionLabel).localeCompare(safe(right.dimensionLabel), "zh-CN");
      return safe(left.nextReviewAt).localeCompare(safe(right.nextReviewAt));
    });
    const pages = Math.max(1, Math.ceil(list.length / pageSize)); state.page = Math.min(state.page, pages);
    const pageItems = list.slice((state.page - 1) * pageSize, state.page * pageSize);
    const body = document.getElementById("c2QueueBody");
    body.innerHTML = pageItems.length ? pageItems.map((row) => {
      const params = new URLSearchParams({ qualityFilter: row.dimension || "", qualityPage: String(state.page), qualityScroll: String(window.scrollY || 0), issueId: row.issueId || "" });
      const target = `${safe(row.targetUrl, "workbench.html")}${safe(row.targetUrl, "workbench.html").includes("?") ? "&" : "?"}${params.toString()}`;
      return `<tr><td><strong>${esc(safe(row.projectName, "未命名项目"))}</strong><small>${esc(safe(row.caseId, "案例待核验"))}</small></td><td>${esc(safe(row.dimensionLabel))}<small>${esc(safe(row.category))}</small></td><td><span class="c2-badge ${row.owner === "human" ? "warn" : ""}">${esc(safe(row.ownerLabel))}</span></td><td>${esc(safe(row.reason))}<small>下一步：${esc(safe(row.nextStep))}</small><small>最近结果：${esc(safe(row.latestResult))} · ${esc(dateTime(row.latestExecutionAt, "尚无执行时间"))}</small><small>下次检查：${esc(dateTime(row.nextReviewAt, "尚未形成复查时间"))}</small></td><td><a href="${esc(target)}" data-quality-action>前往处理</a></td></tr>`;
    }).join("") : `<tr><td colspan="5" class="c2-quality-empty">当前筛选下没有待闭环项目。</td></tr>`;
    document.getElementById("c2QueueCount").textContent = `${number(list.length)} 条记录`;
    document.getElementById("c2QueuePage").textContent = `第 ${state.page} / ${pages} 页`;
    document.getElementById("c2QueuePrev").disabled = state.page <= 1; document.getElementById("c2QueueNext").disabled = state.page >= pages;
    stateWrite(state);
  }
  function render() {
    const reconciliation = quality.reconciliation || {};
    main.innerHTML = `${notice()}<header class="c2-quality-header"><div><span class="c2-kicker">只读质量控制</span><h1>判断质量</h1><p>这里检查前台摘要是否有事实支撑、缺口是否可归因、变化是否可回溯。它不改变评分、动作、仓位或 L0-L5。</p></div><div class="c2-quality-note">项目 ${number(reconciliation.projects)} / 数据库 ${number(reconciliation.databaseProjects)}<br>案例 ${number(reconciliation.cases)} / 数据库 ${number(reconciliation.databaseCases)}<br>对账：${reconciliation.countsMatch && reconciliation.funnelRowsReconciled ? "通过" : "需处理"}</div></header><div class="c2-quality-grid"><section class="c2-quality-card"><h2>质量覆盖漏斗</h2><div class="c2-funnel">${funnelMarkup()}</div><p class="c2-quality-note">点击待系统处理或待人工确认的分段，可过滤下方闭环队列；已闭环分段表示当前无需处理。</p></section><section class="c2-quality-card"><h2>关键指标</h2><div class="c2-table-wrap"><table class="c2-table"><tbody>${metricRows()}</tbody></table></div></section><section class="c2-quality-card c2-quality-queue"><h2>阻断排名</h2><div class="c2-table-wrap"><table class="c2-table"><thead><tr><th>阻断</th><th>影响范围</th><th>责任与状态</th><th>最近执行/复查</th><th>下一步</th><th>入口</th></tr></thead><tbody>${blockerRows() || `<tr><td colspan="6" class="c2-quality-empty">当前没有阻断排名。</td></tr>`}</tbody></table></div></section><section class="c2-quality-card c2-quality-queue"><div class="c2-section-heading"><div><h2>闭环队列</h2><p>每条记录只提供一个主入口；返回时保留筛选、页码和滚动位置。</p></div><span id="c2QueueCount" class="c2-result-count"></span></div><div class="c2-toolbar"><input id="c2QueueSearch" type="search" placeholder="搜索项目、缺口或原因" value="${esc(state.search)}"><select id="c2QueueDimension"><option value="all">全部质量维度</option>${(quality.coverageFunnel || []).map((row) => `<option value="${esc(row.dimension)}" ${state.dimension === row.dimension ? "selected" : ""}>${esc(row.label)}</option>`).join("")}</select><select id="c2QueueOwner"><option value="all">全部责任方</option><option value="system" ${state.owner === "system" ? "selected" : ""}>待系统处理</option><option value="human" ${state.owner === "human" ? "selected" : ""}>待人工处理</option></select><select id="c2QueueSort"><option value="nextReviewAt" ${state.sort === "nextReviewAt" ? "selected" : ""}>按下次检查</option><option value="projectName" ${state.sort === "projectName" ? "selected" : ""}>按项目</option><option value="dimension" ${state.sort === "dimension" ? "selected" : ""}>按维度</option></select></div><div class="c2-table-wrap"><table class="c2-table"><thead><tr><th>项目</th><th>质量维度</th><th>责任方</th><th>缺口与下一步</th><th>主入口</th></tr></thead><tbody id="c2QueueBody"></tbody></table></div><div class="c2-pagination"><button id="c2QueuePrev" type="button">上一页</button><span id="c2QueuePage"></span><button id="c2QueueNext" type="button">下一页</button></div></section></div>`;
    const update = () => { state.search = document.getElementById("c2QueueSearch").value; state.dimension = document.getElementById("c2QueueDimension").value; state.owner = document.getElementById("c2QueueOwner").value; state.sort = document.getElementById("c2QueueSort").value; state.page = 1; queueRows(); };
    ["c2QueueSearch", "c2QueueDimension", "c2QueueOwner", "c2QueueSort"].forEach((id) => document.getElementById(id).addEventListener("input", update));
    document.getElementById("c2QueuePrev").addEventListener("click", () => { state.page -= 1; queueRows(); }); document.getElementById("c2QueueNext").addEventListener("click", () => { state.page += 1; queueRows(); });
    document.querySelectorAll("[data-funnel-dimension]").forEach((button) => button.addEventListener("click", () => {
      if (button.dataset.funnelOwner === "closed") return;
      state.dimension = button.dataset.funnelDimension; state.owner = button.dataset.funnelOwner; state.page = 1; document.getElementById("c2QueueDimension").value = state.dimension; document.getElementById("c2QueueOwner").value = state.owner; queueRows(); document.querySelector(".c2-quality-queue:last-child")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
    document.getElementById("c2QueueBody").addEventListener("click", (event) => {
      if (event.target.closest("[data-quality-action]")) stateWrite({ ...state, scrollTop: window.scrollY || 0 });
    });
    queueRows();
    if (state.scrollTop > 0) window.setTimeout(() => window.scrollTo(0, state.scrollTop), 0);
    statusNotice();
  }
  render();
})();
