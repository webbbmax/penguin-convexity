(function convexityFoundationApp() {
  const state = window.PENGUIN_CONVEXITY_FOUNDATION;
  const byId = (id) => document.getElementById(id);

  if (!state) {
    byId("databaseStatus").textContent = "数据库快照读取失败";
    byId("latestRun").innerHTML = '<p class="empty-feedback">请先初始化凸性数据库。</p>';
    return;
  }

  const number = (value) => new Intl.NumberFormat("zh-CN").format(Number(value || 0));
  const dateTime = (value) => {
    if (!value) return "--";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  byId("databaseStatus").textContent = state.databaseStatus === "initialized" ? "数据库已初始化" : "数据库状态异常";
  byId("generatedAt").textContent = `快照 ${dateTime(state.generatedAt)}`;
  byId("tableCount").textContent = number(state.counts.tables);
  byId("sourceCount").textContent = number(state.counts.sources);
  byId("shadowCount").textContent = number(state.counts.shadowCases);
  byId("activeCount").textContent = number(state.counts.activeCases);
  byId("retryCount").textContent = number(state.counts.retryableErrors);

  const run = state.latestRun;
  if (!run) {
    byId("latestRun").innerHTML = '<p class="empty-feedback">尚无运行记录，系统不会把它误报为“没有候选”。</p>';
  } else {
    const metrics = [
      ["采集", run.collected_count],
      ["重复", run.duplicate_count],
      ["身份匹配", run.matched_count],
      ["过滤", run.filtered_count],
      ["影子池新增", run.shadow_added_count],
      ["正式库新增", run.active_added_count],
      ["升级", run.upgraded_count],
      ["错误", run.error_count],
    ];
    byId("latestRun").innerHTML = `
      <div class="run-head">
        <div><strong>${escapeHtml(run.job_name)}</strong><small> · ${dateTime(run.started_at)}</small></div>
        <span class="run-status">${run.status === "success" ? "成功" : escapeHtml(run.status)}</span>
      </div>
      <dl class="run-metrics">${metrics.map(([label, value]) => `<div><dt>${label}</dt><dd>${number(value)}</dd></div>`).join("")}</dl>
      <p class="run-explanation"><strong>${escapeHtml(run.zeroResultLabel)}</strong>：${escapeHtml(run.zero_result_explanation || "本次运行未补充说明。")}</p>
    `;
  }

  if (!state.latestSourceStats.length) {
    byId("sourceFeedback").innerHTML = '<div class="empty-feedback">当前还没有启用采集器。下一阶段接入后，这里会逐个显示来源采集、去重、过滤、入池、失败和重试情况。</div>';
  } else {
    byId("sourceFeedback").innerHTML = state.latestSourceStats.map((item) => `
      <article>
        <strong>${escapeHtml(item.collector_id)}</strong>
        <span>${escapeHtml(item.status)}</span>
        <small>采集 ${number(item.collected_count)} · 匹配 ${number(item.matched_count)} · 过滤 ${number(item.filtered_count)}</small>
      </article>
    `).join("");
  }

  const zeroResultDetails = {
    none: "本次发现了有效候选，不属于零结果。",
    initialization: "只建立或升级数据库，没有访问任何外部来源。",
    no_qualifying_candidates: "采集器正常返回数据，但没有项目通过身份、交易性或凸性门槛。",
    source_returned_no_data: "任务已经执行，上游接口或订阅源本次没有返回记录。",
    task_not_run: "任务未启动、未到执行时间，或者被前置条件跳过。",
    rules_too_strict: "有原始线索但全部被过滤，需要查看具体过滤原因和阈值。",
    source_failure: "来源超时、限频或结构变化，本次结果不完整，可以单独重试。",
  };
  byId("zeroResultGrid").innerHTML = Object.entries(state.zeroResultLabels).map(([key, label]) => `
    <article class="${run?.zero_result_class === key ? "current" : ""}">
      <strong>${escapeHtml(label)}</strong>
      <p>${escapeHtml(zeroResultDetails[key])}</p>
    </article>
  `).join("");

  const tableByName = new Map(state.tables.map((table) => [table.name, table]));
  let activeGroup = state.groups[0]?.id || "";
  let searchTerm = "";

  function filteredTables(group) {
    const names = group?.tables || state.tables.map((table) => table.name);
    return names
      .map((name) => tableByName.get(name))
      .filter(Boolean)
      .filter((table) => {
        if (!searchTerm) return true;
        const haystack = [
          table.name,
          table.label,
          table.purpose,
          ...table.columns.flatMap((column) => [column.name, column.label]),
        ].join(" ").toLowerCase();
        return haystack.includes(searchTerm);
      });
  }

  function renderTables() {
    const selectedGroup = state.groups.find((group) => group.id === activeGroup) || state.groups[0];
    byId("groupTabs").innerHTML = state.groups.map((group) => `
      <button type="button" data-group="${escapeHtml(group.id)}" class="${group.id === selectedGroup?.id ? "active" : ""}">${escapeHtml(group.label)}</button>
    `).join("");
    const tables = filteredTables(selectedGroup);
    byId("tableGrid").innerHTML = `
      <p class="group-note">${escapeHtml(selectedGroup?.description || "")}</p>
      ${tables.length ? tables.map((table) => `
        <article class="table-card">
          <header>
            <div><h3>${escapeHtml(table.label)}</h3><code>${escapeHtml(table.name)}</code></div>
            <span class="row-count">${number(table.rowCount)} 条</span>
          </header>
          <p>${escapeHtml(table.purpose)}</p>
          <details>
            <summary>查看 ${number(table.columns.length)} 个字段</summary>
            <dl class="field-list">${table.columns.map((column) => `
              <div>
                <dt>${escapeHtml(column.label)}</dt>
                <dd><code>${escapeHtml(column.name)}</code> · ${escapeHtml(column.type)}${column.primaryKey ? " · 主键" : ""}${column.required ? " · 必填" : ""}</dd>
              </div>
            `).join("")}</dl>
          </details>
        </article>
      `).join("") : '<div class="table-empty">当前分类没有匹配的数据表。</div>'}
    `;
    byId("groupTabs").querySelectorAll("[data-group]").forEach((button) => {
      button.addEventListener("click", () => {
        activeGroup = button.dataset.group;
        renderTables();
      });
    });
  }

  byId("data-table-search").addEventListener("input", (event) => {
    searchTerm = event.target.value.trim().toLowerCase();
    renderTables();
  });
  renderTables();
})();
