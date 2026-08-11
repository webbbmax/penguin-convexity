(function initializeScanCenter() {
  const snapshot = window.PENGUIN_CONVEXITY_SCAN_CENTER;
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const dateTime = (value) => {
    if (!value) return "--";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const statusLabels = {
    running: "运行中",
    success: "成功",
    partial_success: "部分成功",
    failed: "失败",
    skipped: "已跳过",
    eligible: "技术可进入",
    pending: "待复核",
    existing: "已有资产",
    rejected: "已排除",
    error: "错误",
    verified: "官网确认",
    corroborated: "独立登记吻合",
    conflict: "身份冲突",
    identity_pending: "身份待核验",
    preflight_pass: "技术预检通过",
    existing_asset: "已有资产",
    promoted: "已升格",
  };
  const riskLabels = {
    high: "高",
    medium: "中",
    low: "低",
  };
  const apiUrl = location.pathname.startsWith("/convexity/")
    ? "/api/convexity/manual-scan"
    : "/api/manual-scan";
  const selectedNetworks = new Set();
  const selectedSources = new Set();

  function feedback(type, title, detail) {
    const target = byId("scanFeedback");
    target.hidden = false;
    target.className = `scan-feedback is-${type}`;
    target.innerHTML = `<strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p>`;
  }

  function latestRunLine(latestRun) {
    if (!latestRun) return '<small>尚未在该范围执行扫描</small>';
    return `
      <small class="scan-last-run status-${escapeHtml(latestRun.status)}">
        ${escapeHtml(latestRun.statusLabel || statusLabels[latestRun.status] || latestRun.status)}
        · ${escapeHtml(dateTime(latestRun.finishedAt || latestRun.startedAt))}
        · ${latestRun.counts.total} 条
      </small>`;
  }

  function renderNetworks() {
    byId("scanNetworkGrid").innerHTML = snapshot.networks.map((network) => `
      <article class="scan-scope-card">
        <label>
          <input type="checkbox" data-network-choice value="${escapeHtml(network.networkId)}" checked />
          <span><b>${escapeHtml(network.name)}</b><small>${escapeHtml(network.chainType)} · ${escapeHtml(network.networkId)}</small></span>
        </label>
        <div class="scan-card-counts">
          <span><b>${network.counts.total}</b>历史明细</span>
          <span><b>${network.counts.eligible}</b>技术可进入</span>
          <span><b>${network.counts.pending}</b>待复核</span>
        </div>
        ${latestRunLine(network.latestRun)}
        <button type="button" data-scan-network="${escapeHtml(network.networkId)}" data-scan-action>单独扫描这条链</button>
      </article>
    `).join("");
    snapshot.networks.forEach((network) => selectedNetworks.add(network.networkId));
  }

  function renderSources() {
    byId("scanSourceGrid").innerHTML = snapshot.sources.map((source) => `
      <article class="scan-scope-card scan-source-card">
        <label>
          <input type="checkbox" data-source-choice value="${escapeHtml(source.sourceId)}" checked />
          <span><b>${escapeHtml(source.name)}</b><small>利益冲突风险：${escapeHtml(riskLabels[source.conflictRisk] || source.conflictRisk)}</small></span>
        </label>
        <p>${escapeHtml(source.boundary)}</p>
        <div class="scan-card-counts">
          <span><b>${source.counts.total}</b>历史明细</span>
          <span><b>${source.counts.eligible}</b>技术可进入</span>
          <span><b>${source.counts.rejected}</b>已排除</span>
        </div>
        ${latestRunLine(source.latestRun)}
        <div class="scan-source-actions">
          <a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">查看采集入口</a>
          <button type="button" data-scan-source="${escapeHtml(source.sourceId)}" data-scan-action>单独扫描这个信源</button>
        </div>
      </article>
    `).join("");
    snapshot.sources.forEach((source) => selectedSources.add(source.sourceId));
  }

  function renderLatestRun() {
    const run = snapshot.latestRun;
    if (!run) {
      byId("scanLatestRun").innerHTML = '<p class="empty-feedback">尚无扫描记录。可先选择一条链或一个信源进行扫描。</p>';
      return;
    }
    byId("scanLatestRunMeta").textContent = `${dateTime(run.started_at)} · ${run.statusLabel} · ${run.scope.explicit ? "人工指定范围" : "历史全量更新"}`;
    byId("scanLatestRun").innerHTML = `
      <article><span>运行状态</span><strong class="status-text-${escapeHtml(run.status)}">${escapeHtml(run.statusLabel)}</strong><small>${escapeHtml(run.mode)}</small></article>
      <article><span>逐源结果</span><strong>${run.counts.total}</strong><small>本次写入扫描明细</small></article>
      <article><span>技术可进入</span><strong>${run.counts.eligible}</strong><small>仍需身份与价值核验</small></article>
      <article><span>待复核</span><strong>${run.counts.pending}</strong><small>保留在发现队列</small></article>
      <article><span>已有 / 排除</span><strong>${run.counts.existing} / ${run.counts.rejected}</strong><small>不重复冒充新项目</small></article>
      <article><span>错误</span><strong>${run.error_count}</strong><small>${run.canRetry ? "可以按原范围重试" : "没有阻断错误"}</small></article>
      <p>${escapeHtml(run.zero_result_explanation || "本次扫描已完成。")}</p>
    `;
  }

  function scopeNames(run) {
    const networkMap = Object.fromEntries(snapshot.networks.map((item) => [item.networkId, item.name]));
    const sourceMap = Object.fromEntries(snapshot.sources.map((item) => [item.sourceId, item.name]));
    const networks = run.scope.networkIds.map((id) => networkMap[id] || id).join("、") || "全部链";
    const sources = run.scope.sourceIds.map((id) => sourceMap[id] || id).join("、") || "全部信源";
    return { networks, sources };
  }

  function renderRunHistory() {
    byId("scanRunRows").innerHTML = snapshot.runs.length
      ? snapshot.runs.map((run) => {
        const scope = scopeNames(run);
        return `
          <tr>
            <td><strong class="status-text-${escapeHtml(run.status)}">${escapeHtml(run.statusLabel)}</strong><small>${escapeHtml(dateTime(run.started_at))}</small></td>
            <td><b>${escapeHtml(scope.networks)}</b><small>${escapeHtml(scope.sources)}</small></td>
            <td><b>${run.counts.total} 条</b><small>可进入 ${run.counts.eligible} · 待复核 ${run.counts.pending} · 排除 ${run.counts.rejected}</small></td>
            <td><b>${run.error_count}</b><small>${escapeHtml(run.error_summary || "无错误")}</small></td>
            <td>${run.canRetry
              ? `<button type="button" data-retry-run="${escapeHtml(run.run_id)}" data-scan-action>按原范围重试</button>`
              : '<span class="scan-complete-label">已完成</span>'}</td>
          </tr>`;
      }).join("")
      : '<tr><td colspan="5" class="scan-empty-cell">尚无扫描历史</td></tr>';
  }

  function fillFilters() {
    byId("scanResultNetwork").insertAdjacentHTML(
      "beforeend",
      snapshot.networks.map((item) => `<option value="${escapeHtml(item.networkId)}">${escapeHtml(item.name)}</option>`).join(""),
    );
    byId("scanResultSource").insertAdjacentHTML(
      "beforeend",
      snapshot.sources.map((item) => `<option value="${escapeHtml(item.sourceId)}">${escapeHtml(item.name)}</option>`).join(""),
    );
  }

  function renderResults() {
    const network = byId("scanResultNetwork").value;
    const source = byId("scanResultSource").value;
    const status = byId("scanResultStatus").value;
    const query = byId("scanResultSearch").value.trim().toLowerCase();
    const sourceMap = Object.fromEntries(snapshot.sources.map((item) => [item.sourceId, item.name]));
    const records = snapshot.results.filter((item) => {
      if (network && item.networkId !== network) return false;
      if (source && item.sourceId !== source) return false;
      if (status && item.resultStatus !== status) return false;
      if (!query) return true;
      return [
        item.tokenName,
        item.symbol,
        item.externalKey,
        item.networkName,
        sourceMap[item.sourceId] || item.sourceName,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
    byId("scanVisibleCount").textContent = records.length;
    byId("scanResultRows").innerHTML = records.length
      ? records.map((item) => `
          <tr>
            <td><strong>${escapeHtml(item.tokenName || "未命名代币")} ${item.symbol ? `(${escapeHtml(item.symbol)})` : ""}</strong><small title="${escapeHtml(item.externalKey)}">${escapeHtml(item.externalKey)}</small></td>
            <td><b>${escapeHtml(item.networkName)}</b><small>${escapeHtml(item.networkId)}</small></td>
            <td><b>${escapeHtml(sourceMap[item.sourceId] || item.sourceName)}</b><small>${escapeHtml(item.sourceId)}</small></td>
            <td><span class="scan-result-status status-${escapeHtml(item.resultStatus)}">${escapeHtml(statusLabels[item.resultStatus] || item.resultStatus)}</span><small>${escapeHtml(item.reason)}</small></td>
            <td><b>${escapeHtml(statusLabels[item.identityStatus] || item.identityStatus || item.queueStatus || "尚未复核")}</b><small>发现分 ${item.discoveryScore ?? "--"}</small></td>
            <td><b>${escapeHtml(dateTime(item.observedAt))}</b><small>${escapeHtml(item.runId)}</small></td>
            <td>${item.sourceUrl ? `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">打开原始来源</a>` : "--"}</td>
          </tr>
        `).join("")
      : '<tr><td colspan="7" class="scan-empty-cell">当前筛选条件下没有结果</td></tr>';
  }

  function setRunning(running) {
    document.querySelectorAll("[data-scan-action]").forEach((button) => {
      button.disabled = running;
    });
  }

  async function runScan(networkIds, sourceIds, label) {
    setRunning(true);
    feedback("running", `正在${label}`, "正在采集、核验并写入逐条结果。页面会在任务结束后自动刷新。");
    try {
      const response = await fetch(apiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ networkIds, sourceIds, mode: "manual" }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || payload.message || `请求失败：${response.status}`);
      sessionStorage.setItem("convexity-scan-feedback", JSON.stringify({
        type: payload.status === "failed" ? "error" : payload.status === "partial_success" ? "warning" : "success",
        title: payload.status === "failed" ? "扫描失败，已保留重试范围" : payload.status === "partial_success" ? "扫描部分完成" : "扫描完成",
        detail: payload.message || "扫描结果已写入。",
      }));
      location.reload();
    } catch (error) {
      feedback("error", "扫描未能启动或返回失败", error.message);
      setRunning(false);
    }
  }

  if (!snapshot) {
    feedback("error", "扫描中心快照未生成", "请返回凸性工作台后重新打开扫描中心。");
    return;
  }

  byId("scanNoLimitPolicy").textContent = snapshot.noLimitPolicy;
  byId("scanGeneratedAt").textContent = `后台快照：${dateTime(snapshot.generatedAt)}`;
  renderNetworks();
  renderSources();
  renderLatestRun();
  renderRunHistory();
  fillFilters();
  renderResults();

  const savedFeedback = sessionStorage.getItem("convexity-scan-feedback");
  if (savedFeedback) {
    sessionStorage.removeItem("convexity-scan-feedback");
    try {
      const item = JSON.parse(savedFeedback);
      feedback(item.type, item.title, item.detail);
    } catch (_error) {
      // Ignore malformed one-time feedback.
    }
  }

  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-network-choice]")) {
      event.target.checked
        ? selectedNetworks.add(event.target.value)
        : selectedNetworks.delete(event.target.value);
    }
    if (event.target.matches("[data-source-choice]")) {
      event.target.checked
        ? selectedSources.add(event.target.value)
        : selectedSources.delete(event.target.value);
    }
    if (event.target.closest(".scan-result-controls")) renderResults();
  });
  byId("scanResultSearch").addEventListener("input", renderResults);

  document.addEventListener("click", (event) => {
    const networkButton = event.target.closest("[data-scan-network]");
    if (networkButton) {
      runScan([networkButton.dataset.scanNetwork], [], "单链扫描");
      return;
    }
    const sourceButton = event.target.closest("[data-scan-source]");
    if (sourceButton) {
      runScan([], [sourceButton.dataset.scanSource], "单信源扫描");
      return;
    }
    const retryButton = event.target.closest("[data-retry-run]");
    if (retryButton) {
      const run = snapshot.runs.find((item) => item.run_id === retryButton.dataset.retryRun);
      if (run) runScan(run.scope.networkIds, run.scope.sourceIds, "原范围重试");
    }
  });

  byId("scanSelected").addEventListener("click", () => {
    if (!selectedNetworks.size || !selectedSources.size) {
      feedback("warning", "请至少选择一条链和一个信源", "勾选完成后再点击“扫描选中组合”。");
      return;
    }
    runScan([...selectedNetworks], [...selectedSources], "选中组合扫描");
  });
  byId("scanAll").addEventListener("click", () => runScan([], [], "全量扫描"));
})();
