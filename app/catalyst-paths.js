(function catalystPathPage() {
  const snapshot = window.PENGUIN_CONVEXITY_CATALYST_PATHS;
  const trackingSnapshot = window.PENGUIN_CONVEXITY_TRACKING_TASKS || {};
  if (!snapshot) return;

  const stageLabels = {
    catalyst_pending: "系统尚未发现催化",
    asset_pending: "受益资产待确认",
    transmission_pending: "系统正在核验价值传导",
    market_pending: "市场表达待补齐",
    exit_pending: "系统正在核验2万美元退出",
    research_ready: "研究路径已闭环",
    action_ready: "行动路径已闭环",
    invalidated: "路径已失效",
  };
  const catalystLabels = {
    governance: "治理提案",
    code_release: "官方代码变化",
    security_change: "安全相关变化",
    product_release: "产品发布",
    regulatory: "监管事件",
    unknown: "系统尚未发现",
  };
  const stepStatusLabels = {
    verified: "已确认",
    missing: "系统正在检查",
    pending: "外部事实尚未发生或待核验",
    blocked: "来源或交易条件不可用",
    invalidated: "已失效",
  };
  const pageSize = 20;
  const state = { stage: "all", type: "all", search: "", selectedId: "", page: 0 };
  const byId = (id) => document.getElementById(id);
  const trackingByCase = new Map((trackingSnapshot.tasks || []).map((item) => [item.caseId, item]));

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  }

  function dateTime(value) {
    if (!value) return "待补齐";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value
      : new Intl.DateTimeFormat("zh-CN", {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        }).format(date);
  }

  function money(value) {
    if (value === null || value === undefined) return "待补齐";
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    }).format(Number(value));
  }

  function slippage(value) {
    return value === null || value === undefined
      ? "待补齐"
      : `${Number(value).toFixed(2)}%`;
  }

  function renderSummary() {
    const counts = snapshot.counts;
    byId("catalystPathBoundary").textContent = snapshot.boundary;
    byId("catalystPathGeneratedAt").textContent = `生成时间：${dateTime(snapshot.generatedAt)}`;
    byId("catalystPathTotal").textContent = counts.total.toLocaleString("zh-CN");
    byId("catalystPathCatalyst").textContent = counts.withCatalyst.toLocaleString("zh-CN");
    byId("catalystPathAsset").textContent = counts.withAsset.toLocaleString("zh-CN");
    byId("catalystPathExit").textContent = counts.exitModeled.toLocaleString("zh-CN");
    byId("catalystPathResearch").textContent = counts.researchReady.toLocaleString("zh-CN");
    byId("catalystPathAction").textContent = counts.actionReady.toLocaleString("zh-CN");
  }

  function filteredRecords() {
    const query = state.search.trim().toLowerCase();
    return snapshot.records.filter((item) => (
      (state.stage === "all" || item.path_stage === state.stage)
      && (state.type === "all" || item.catalyst_type === state.type)
      && (!query || [
        item.projectName,
        item.symbol,
        item.catalyst_summary,
        item.expression_asset_text,
      ].join(" ").toLowerCase().includes(query))
    ));
  }

  function renderList(records) {
    const pageCount = Math.max(1, Math.ceil(records.length / pageSize));
    state.page = Math.max(0, Math.min(state.page, pageCount - 1));
    const pageStart = state.page * pageSize;
    const pageItems = records.filter((_item, index) => index >= pageStart && index < pageStart + pageSize);
    byId("catalystPathPageMeta").textContent = `第 ${state.page + 1} / ${pageCount} 页 · 每页 ${pageSize} 个`;
    byId("catalystPathPreviousPage").disabled = state.page === 0;
    byId("catalystPathNextPage").disabled = state.page >= pageCount - 1;
    if (!records.length) {
      byId("catalystPathList").innerHTML = '<p class="catalyst-path-empty">当前筛选下没有项目。</p>';
      return;
    }
    byId("catalystPathList").innerHTML = pageItems.map((item) => {
      const task = trackingByCase.get(item.case_id);
      return `
      <button type="button" data-path-id="${escapeHtml(item.catalyst_trade_path_id)}" class="${item.catalyst_trade_path_id === state.selectedId ? "is-selected" : ""}">
        <span class="catalyst-path-stage stage-${escapeHtml(item.path_stage)}">${escapeHtml(stageLabels[item.path_stage] || item.path_stage)}</span>
        <strong>${escapeHtml(item.projectName)}${item.symbol ? ` <small>${escapeHtml(item.symbol)}</small>` : ""}</strong>
        <p>${escapeHtml(item.catalyst_summary)}</p>
        <footer>
          <span>${escapeHtml(catalystLabels[item.catalyst_type] || item.catalyst_type)}</span>
          <span>${escapeHtml(item.expression_asset_text || "资产待确认")}</span>
          <span>2万美元滑点 ${escapeHtml(slippage(item.modeled_exit_slippage_pct))}</span>
          <span>下次自动检查 ${escapeHtml(dateTime(task?.nextReviewAt))}</span>
        </footer>
      </button>
    `;
    }).join("");
  }

  function renderDetail(item) {
    if (!item) {
      byId("catalystPathDetail").innerHTML = "<strong>选择一个项目</strong><p>这里会展示整条传导路径、证据链接、实际核验金额、2万美元理论估算、失效条件与下一项自动任务。</p>";
      return;
    }
    const catalystUrl = safeUrl(item.catalyst_source_url);
    const trackingTask = trackingByCase.get(item.case_id);
    const steps = (item.transmissionSteps || []).map((step, index) => `
      <li class="${escapeHtml(step.status)}">
        <b>${index + 1}</b>
        <div><strong>${escapeHtml(step.label)}</strong><p>${escapeHtml(step.detail)}</p></div>
        <span>${escapeHtml(stepStatusLabels[step.status] || "系统正在检查")}</span>
      </li>
    `).join("");
    const blockers = item.blockers?.length
      ? item.blockers.map((value) => `<li>${escapeHtml(value)}</li>`).join("")
      : "<li>当前路径没有结构化阻断项。</li>";
    const invalidations = item.invalidationConditions?.length
      ? item.invalidationConditions.map((value) => `<li>${escapeHtml(value)}</li>`).join("")
      : "<li>系统尚未形成可核验失效条件；下次自动更新继续检查。</li>";
    byId("catalystPathDetail").innerHTML = `
      <span class="catalyst-path-stage stage-${escapeHtml(item.path_stage)}">${escapeHtml(stageLabels[item.path_stage] || item.path_stage)}</span>
      <h3>${escapeHtml(item.projectName)}${item.symbol ? ` <small>${escapeHtml(item.symbol)}</small>` : ""}</h3>
      <p>${escapeHtml(item.catalyst_summary)} · 负责人：系统自动检查 · 下一步：${escapeHtml(item.next_step || "下次自动更新时继续检查")} · 下次自动检查：${escapeHtml(dateTime(trackingTask?.nextReviewAt))}</p>
      ${catalystUrl ? `<a class="catalyst-source-link" href="${escapeHtml(catalystUrl)}" target="_blank" rel="noreferrer">打开催化原始来源</a>` : ""}
      <ol class="catalyst-transmission-steps">${steps}</ol>
      <dl class="catalyst-exit-facts">
        <dt>表达资产</dt><dd>${escapeHtml(item.expression_asset_text || "待确认")}</dd>
        <dt>网络 / 合约</dt><dd>${escapeHtml(item.network_name || "待确认")}<small>${escapeHtml(item.contract_address || "合约待确认")}</small></dd>
        <dt>交易场所</dt><dd>${escapeHtml(item.venue_text || "待确认")}</dd>
        <dt>实际只读核验</dt><dd>${money(item.observed_exit_notional_usd)} · ${slippage(item.observed_exit_slippage_pct)}<small>这是已有核验记录的金额，不等于2万美元</small></dd>
        <dt>2万美元理论估算</dt><dd>${slippage(item.modeled_exit_slippage_pct)}<small>${escapeHtml(item.modeled_exit_method)}</small></dd>
      </dl>
      <section><h4>当前阻断</h4><ul>${blockers}</ul></section>
      <section><h4>失效条件</h4><ul>${invalidations}</ul></section>
      <section class="catalyst-next-task">
        <h4>下一项机器任务</h4>
        <p>${escapeHtml(item.next_step)}</p>
        <a href="update-center.html?task=${encodeURIComponent(item.next_task_id)}">运行这项更新</a>
      </section>
      <footer>
        <a href="${escapeHtml(item.detailUrl)}">进入项目详情</a>
        <span>路径生成：${escapeHtml(dateTime(item.generated_at))}</span>
      </footer>
    `;
  }

  function render() {
    const records = filteredRecords();
    byId("catalystPathVisibleCount").textContent = `筛选结果 ${records.length.toLocaleString("zh-CN")} 条；列表每页最多 ${pageSize} 个`;
    renderList(records);
    renderDetail(snapshot.records.find((item) => item.catalyst_trade_path_id === state.selectedId));
  }

  byId("catalystPathStageFilter").addEventListener("change", (event) => {
    state.stage = event.target.value;
    state.selectedId = "";
    state.page = 0;
    render();
  });
  byId("catalystPathTypeFilter").addEventListener("change", (event) => {
    state.type = event.target.value;
    state.selectedId = "";
    state.page = 0;
    render();
  });
  byId("catalystPathSearch").addEventListener("input", (event) => {
    state.search = event.target.value;
    state.selectedId = "";
    state.page = 0;
    render();
  });
  byId("catalystPathList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-path-id]");
    if (!button) return;
    state.selectedId = button.dataset.pathId;
    render();
  });
  byId("catalystPathPreviousPage").addEventListener("click", () => {
    state.page = Math.max(0, state.page - 1);
    render();
  });
  byId("catalystPathNextPage").addEventListener("click", () => {
    state.page += 1;
    render();
  });

  renderSummary();
  render();
})();
