(function initializeSourceRegistry() {
  const snapshot = window.PENGUIN_CONVEXITY_SOURCE_REGISTRY;
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
  let selectedId = new URLSearchParams(location.search).get("source") || "";
  let visibleSources = [];

  function selectedSource() {
    return snapshot.sources.find((item) => item.source_id === selectedId)
      || visibleSources[0];
  }

  function renderList() {
    const category = byId("sourceCategoryFilter").value;
    const health = byId("sourceHealthFilter").value;
    const query = byId("sourceSearch").value.trim().toLowerCase();
    visibleSources = snapshot.sources.filter((item) => {
      if (category !== "all" && item.category !== category) return false;
      if (health !== "all" && item.healthStatus !== health) return false;
      if (!query) return true;
      return [
        item.name,
        item.category,
        item.source_type,
        item.proves,
        item.access_method,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
    if (!visibleSources.some((item) => item.source_id === selectedId)) {
      selectedId = visibleSources[0]?.source_id || "";
    }
    byId("sourceVisibleCount").textContent = visibleSources.length;
    byId("sourceRegistryList").innerHTML = visibleSources.length
      ? visibleSources.map((item) => `
          <button type="button" class="${item.source_id === selectedId ? "active" : ""}" data-source-id="${escapeHtml(item.source_id)}">
            <span><b>${escapeHtml(item.name)}</b><em class="status-${escapeHtml(item.healthStatus)}">${escapeHtml(item.healthStatusLabel)}</em></span>
            <strong>${escapeHtml(item.category)}</strong>
            <small>写入${item.recordCount}条 · ${escapeHtml(item.primaryTaskId ? snapshot.taskLabels[item.primaryTaskId] : "内部资料")}</small>
          </button>
        `).join("")
      : '<p class="source-empty">当前筛选条件下没有信源。</p>';
    renderDetail();
  }

  function renderDetail() {
    const item = selectedSource();
    if (!item) {
      byId("sourceRegistryDetail").innerHTML = '<p class="source-empty">请选择一个信源查看详情。</p>';
      return;
    }
    selectedId = item.source_id;
    const latest = item.latestStat;
    const taskLinks = item.taskIds.length
      ? item.taskIds.map((taskId) => `<a href="update-center.html?task=${escapeHtml(taskId)}">${escapeHtml(snapshot.taskLabels[taskId])}</a>`).join("")
      : '<span>内部资料，不执行自动更新</span>';
    byId("sourceRegistryDetail").innerHTML = `
      <header>
        <div>
          <span>${escapeHtml(item.category)} · ${escapeHtml(item.source_id)}</span>
          <h3>${escapeHtml(item.name)}</h3>
          <p>${escapeHtml(item.source_type)} · ${escapeHtml(item.access_method || "访问方式待补齐")}</p>
        </div>
        <strong class="source-health status-${escapeHtml(item.healthStatus)}">${escapeHtml(item.healthStatusLabel)}</strong>
      </header>

      <section class="source-boundary-grid">
        <article class="is-positive"><span>这个来源能证明什么</span><p>${escapeHtml(item.proves)}</p></article>
        <article class="is-negative"><span>不能证明什么</span><p>${escapeHtml(item.doesNotProve)}</p></article>
      </section>

      <section class="source-fact-grid">
        <div><span>系统状态</span><strong>${escapeHtml(item.status)}</strong></div>
        <div><span>可信度</span><strong>${escapeHtml(item.confidence)}</strong></div>
        <div><span>利益冲突风险</span><strong>${escapeHtml(item.conflict_risk)}</strong></div>
        <div><span>已写入记录</span><strong>${item.recordCount}</strong></div>
        <div><span>最近检查</span><strong>${escapeHtml(dateTime(item.last_checked_at))}</strong></div>
        <div><span>计划</span><strong>${escapeHtml(item.schedule_text || "按需手动更新")}</strong></div>
      </section>

      <section class="source-task-links">
        <header><span>关联更新任务</span><strong>点击进入对应单项更新</strong></header>
        <div>${taskLinks}</div>
      </section>

      <section class="source-latest-run">
        <header><span>LATEST RESULT</span><h4>最近一次来源反馈</h4></header>
        ${latest ? `
          <div class="source-latest-metrics">
            <div><span>状态</span><strong>${escapeHtml(item.healthStatusLabel)}</strong></div>
            <div><span>采集</span><strong>${latest.collected_count}</strong></div>
            <div><span>匹配</span><strong>${latest.matched_count}</strong></div>
            <div><span>过滤</span><strong>${latest.filtered_count}</strong></div>
            <div><span>失败</span><strong>${latest.failed_count}</strong></div>
          </div>
          <p>${escapeHtml(latest.error_message || "最近一次运行没有来源级错误。")}</p>
        ` : '<p>该来源目前没有逐来源运行统计，但可能已有人工导入或历史资料。</p>'}
      </section>

      ${item.latestError ? `
        <aside class="source-latest-error">
          <strong>最近异常：${escapeHtml(item.latestError.task_name)}</strong>
          <p>${escapeHtml(item.latestError.message)}</p>
          <a href="update-center.html?task=${escapeHtml(item.primaryTaskId || "full_refresh")}">前往单独重试</a>
        </aside>
      ` : ""}

      <footer class="source-detail-footer">
        ${item.url
          && !item.url.startsWith("local://")
          && !item.url.startsWith("multiple://")
          ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开采集入口</a>`
          : '<span>本地或多链接来源，没有单一外部入口</span>'}
      </footer>
    `;
  }

  if (!snapshot) return;
  byId("sourcePolicy").textContent = snapshot.policy;
  byId("sourceGeneratedAt").textContent = `后台快照：${dateTime(snapshot.generatedAt)}`;
  byId("sourceTotal").textContent = snapshot.counts.total;
  byId("sourceActive").textContent = snapshot.counts.active;
  byId("sourceHealthy").textContent = snapshot.counts.healthy;
  byId("sourceAttention").textContent = snapshot.counts.attention;
  byId("sourceNeverRun").textContent = snapshot.counts.neverRun;

  const categories = [...new Set(snapshot.sources.map((item) => item.category))];
  byId("sourceCategoryFilter").insertAdjacentHTML(
    "beforeend",
    categories.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join(""),
  );
  renderList();

  ["sourceCategoryFilter", "sourceHealthFilter"].forEach((id) => {
    byId(id).addEventListener("change", renderList);
  });
  byId("sourceSearch").addEventListener("input", renderList);
  byId("sourceRegistryList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-source-id]");
    if (!button) return;
    selectedId = button.dataset.sourceId;
    renderList();
  });
}());
