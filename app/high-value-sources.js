(function initializeHighValueSources() {
  const snapshot = window.PENGUIN_CONVEXITY_HIGH_VALUE_SOURCES;
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
    success: "成功",
    partial_success: "部分完成",
    failed: "失败",
    no_data: "没有返回数据",
    never_run: "尚未运行",
  };
  const boundaries = {
    github: {
      proves: "官方仓库、最近提交及安全相关代码活动",
      limit: "安全相关提交不等于漏洞已经修复，也不证明采用或代币价值捕获",
    },
    defillama: {
      proves: "协议TVL、分类和部署网络的结构化快照",
      limit: "TVL不等于收入，也不证明代币承接价值",
    },
    snapshot: {
      proves: "链下治理提案、投票窗口和状态",
      limit: "关闭或通过不等于链上执行",
    },
    cactus: {
      proves: "链上治理提案及执行状态",
      limit: "执行不等于价格传导或经济增量",
    },
  };

  function metricMarkup(record) {
    const metric = record.metric;
    if (!metric || metric.value == null) return "";
    const value = Number(metric.value);
    const display = Number.isFinite(value)
      ? value.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
      : metric.value;
    return `<small class="high-value-metric">${escapeHtml(metric.field)}：${escapeHtml(display)} ${escapeHtml(metric.unit || "")}</small>`;
  }

  function renderSources() {
    byId("highValueSourceGrid").innerHTML = snapshot.sources.map((source) => {
      const boundary = boundaries[source.provider] || { proves: "取得结构化事实", limit: "不能单独产生投资结论" };
      return `
        <article class="status-${escapeHtml(source.status)}">
          <header><div><span>${escapeHtml(source.source_type)}</span><h3>${escapeHtml(source.name)}</h3></div><strong>${escapeHtml(statusLabels[source.status] || source.status)}</strong></header>
          <dl>
            <div><dt>能证明</dt><dd>${escapeHtml(boundary.proves)}</dd></div>
            <div><dt>不能证明</dt><dd>${escapeHtml(boundary.limit)}</dd></div>
          </dl>
          <footer><span>采集 ${source.collected}</span><span>匹配项目 ${source.matched}</span><span>失败 ${source.failed}</span></footer>
        </article>`;
    }).join("");
  }

  function renderRecords() {
    const sourceId = byId("highValueSourceFilter").value;
    const query = byId("highValueSearch").value.trim().toLowerCase();
    const records = snapshot.records.filter((record) => {
      if (sourceId !== "all" && record.sourceId !== sourceId) return false;
      if (!query) return true;
      return [record.caseId, record.asset, record.sourceName, record.summary]
        .some((value) => String(value || "").toLowerCase().includes(query));
    });
    byId("highValueVisibleCount").textContent = records.length;
    byId("highValueRows").innerHTML = records.length
      ? records.map((record) => `
          <tr>
            <td><strong>${escapeHtml(record.caseId)}</strong><small>${escapeHtml(record.asset || "无直接代币")} · ${escapeHtml(dateTime(record.observedAt))}</small></td>
            <td><b>${escapeHtml(record.sourceName)}</b><small>${escapeHtml(record.factBoundary)} · 可信度${escapeHtml(record.confidence)}</small></td>
            <td><p>${escapeHtml(record.summary)}</p>${metricMarkup(record)}${record.changes.length ? `<small class="high-value-changed">相对上次记录发生 ${record.changes.length} 项变化</small>` : ""}</td>
            <td><a href="${escapeHtml(record.sourceUrl)}" target="_blank" rel="noreferrer">打开原始来源</a></td>
          </tr>`).join("")
      : '<tr><td colspan="4" class="update-empty">当前筛选条件下没有事实记录。</td></tr>';
  }

  if (!snapshot) return;
  byId("highValuePolicy").textContent = snapshot.policy;
  byId("highValueSourceCount").textContent = snapshot.counts.sources;
  byId("highValueRecordCount").textContent = snapshot.counts.records;
  byId("highValueCaseCount").textContent = snapshot.counts.cases;
  byId("highValueChangeCount").textContent = snapshot.counts.changed;
  byId("highValueFailureCount").textContent = snapshot.counts.failedSources;
  byId("highValueRunStatus").textContent = snapshot.latestRun
    ? statusLabels[snapshot.latestRun.status] || snapshot.latestRun.status
    : "尚未运行";
  byId("highValueRunMeta").textContent = snapshot.latestRun
    ? `${snapshot.latestRun.job_name} · ${dateTime(snapshot.latestRun.finished_at || snapshot.latestRun.started_at)}`
    : "进入更新中心运行“正式项目持续证据”。";

  byId("highValueSourceFilter").insertAdjacentHTML(
    "beforeend",
    snapshot.sources.map((source) => `<option value="${escapeHtml(source.source_id)}">${escapeHtml(source.name)}</option>`).join(""),
  );
  renderSources();
  renderRecords();
  byId("highValueSourceFilter").addEventListener("change", renderRecords);
  byId("highValueSearch").addEventListener("input", renderRecords);
}());
