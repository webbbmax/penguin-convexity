(function initializeSourceDiscovery() {
  const snapshot = window.PENGUIN_CONVEXITY_SOURCE_DISCOVERY;
  const pageSize = 100;
  let page = 1;
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
  const runStatusLabels = {
    success: "成功",
    partial_success: "部分完成",
    failed: "失败",
    no_data: "没有返回数据",
    never_run: "尚未运行",
  };
  const identityLabels = {
    verified: "已建档或已有匹配",
    corroborated: "机器观察档案",
    pending: "证据不足待补齐",
    conflict: "归因冲突",
    rejected: "已排除",
  };
  const assetLabels = {
    verified: "资产已核验",
    pending: "资产待核验",
    not_identified: "资产未识别",
    conflict: "资产冲突",
  };
  const captureLabels = {
    verified: "价值捕获已核验",
    claimed: "仅项目方声称",
    unknown: "价值捕获未知",
    not_applicable: "不适用",
  };

  function sourceCards() {
    byId("sourceDiscoverySourceGrid").innerHTML = snapshot.sources.map((source) => `
      <article class="status-${escapeHtml(source.status)}">
        <header>
          <div><span>${escapeHtml(source.source_type)}</span><h3>${escapeHtml(source.name)}</h3></div>
          <strong>${escapeHtml(runStatusLabels[source.status] || source.status)}</strong>
        </header>
        <p>${escapeHtml(source.boundary || "尚未运行。")}</p>
        ${source.upstreamLimit ? `<small class="source-discovery-limit">${escapeHtml(source.upstreamLimit)}</small>` : ""}
        <footer><span>读取 ${source.collected}</span><span>请求 ${source.pages} 页</span><span>可归因 ${source.matched}</span><span>失败 ${source.failed}</span></footer>
      </article>`).join("");
  }

  function filteredItems() {
    const source = byId("sourceDiscoverySourceFilter").value;
    const identity = byId("sourceDiscoveryIdentityFilter").value;
    const asset = byId("sourceDiscoveryAssetFilter").value;
    const query = byId("sourceDiscoverySearch").value.trim().toLowerCase();
    return snapshot.items.filter((item) => {
      if (source !== "all" && !item.sourceIds.includes(source)) return false;
      if (identity !== "all" && item.projectIdentityStatus !== identity) return false;
      if (asset !== "all" && item.assetIdentityStatus !== asset) return false;
      if (!query) return true;
      return [
        item.canonicalName,
        item.matchedProjectId,
        item.categories.join(" "),
        item.sourceNames.join(" "),
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }

  function sourceLinks(item) {
    return item.sourceLinks.map((source) => (
      source.url
        ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.sourceName)}</a>`
        : `<span>${escapeHtml(source.sourceName)}</span>`
    )).join("");
  }

  function projectLinks(item) {
    const links = [];
    if (item.detailUrl) links.push(`<a href="${escapeHtml(item.detailUrl)}">查看项目档案</a>`);
    if (item.websiteUrl) links.push(`<a href="${escapeHtml(item.websiteUrl)}" target="_blank" rel="noreferrer">官网</a>`);
    if (item.repositoryUrl) links.push(`<a href="${escapeHtml(item.repositoryUrl)}" target="_blank" rel="noreferrer">代码</a>`);
    if (item.socialUrl) links.push(`<a href="${escapeHtml(item.socialUrl)}" target="_blank" rel="noreferrer">社交</a>`);
    return links.length ? links.join("") : "<span>未取得官方入口</span>";
  }

  function renderItems(resetPage) {
    if (resetPage) page = 1;
    const items = filteredItems();
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    page = Math.min(page, totalPages);
    const visible = items.slice((page - 1) * pageSize, page * pageSize);
    byId("sourceDiscoveryFilteredCount").textContent = items.length;
    byId("sourceDiscoveryVisibleCount").textContent = visible.length;
    byId("sourceDiscoveryPageLabel").textContent = `第 ${page} / ${totalPages} 页`;
    byId("sourceDiscoveryPrevious").disabled = page <= 1;
    byId("sourceDiscoveryNext").disabled = page >= totalPages;
    byId("sourceDiscoveryRows").innerHTML = visible.length
      ? visible.map((item) => `
          <tr>
            <td>
              <strong>${escapeHtml(item.canonicalName)}</strong>
              <small>${escapeHtml(item.categories.slice(0, 3).join(" · ") || "来源未分类")} · 最近 ${escapeHtml(dateTime(item.lastSeenAt))}</small>
              ${item.machinePromoted ? "<em>机器自动建档 · 当前只观察</em>" : ""}
              ${item.matchedProjectId ? `<em>项目ID ${escapeHtml(item.matchedProjectId)}</em>` : ""}
            </td>
            <td><b>${item.sourceIds.length} 个独立来源 · ${item.recordCount} 条记录</b><small>${escapeHtml(item.sourceNames.join("、"))}</small></td>
            <td><span class="source-discovery-badge status-${escapeHtml(item.projectIdentityStatus)}">${escapeHtml(identityLabels[item.projectIdentityStatus] || item.projectIdentityStatus)}</span><p>${escapeHtml(item.attributionReason)}</p></td>
            <td><strong>${escapeHtml(assetLabels[item.assetIdentityStatus] || item.assetIdentityStatus)}</strong><small>${escapeHtml(captureLabels[item.valueCaptureStatus] || item.valueCaptureStatus)}</small></td>
            <td><div class="source-discovery-links">${projectLinks(item)}</div><div class="source-discovery-links is-source">${sourceLinks(item)}</div></td>
          </tr>`).join("")
      : '<tr><td colspan="5" class="update-empty">当前筛选条件下没有项目级发现。</td></tr>';
  }

  if (!snapshot) return;
  byId("sourceDiscoveryPolicy").textContent = snapshot.policy;
  byId("sourceDiscoveryRaw").textContent = snapshot.counts.rawDiscoveries;
  byId("sourceDiscoveryClusters").textContent = snapshot.counts.clusters;
  byId("sourceDiscoveryMachine").textContent = snapshot.counts.machineProjects;
  byId("sourceDiscoveryCorroborated").textContent = snapshot.counts.corroborated;
  byId("sourceDiscoveryPending").textContent = snapshot.counts.pending;
  byId("sourceDiscoveryNoAsset").textContent = snapshot.counts.assetNotIdentified;
  byId("sourceDiscoveryRunStatus").textContent = snapshot.latestRun
    ? runStatusLabels[snapshot.latestRun.status] || snapshot.latestRun.status
    : "尚未运行";
  byId("sourceDiscoveryRunMeta").textContent = snapshot.latestRun
    ? `${snapshot.latestRun.job_name} · ${dateTime(snapshot.latestRun.finished_at || snapshot.latestRun.started_at)}`
    : "进入更新中心运行“机器发现与自动建档”。";
  byId("sourceDiscoverySourceFilter").insertAdjacentHTML(
    "beforeend",
    snapshot.sources.map((source) => `<option value="${escapeHtml(source.source_id)}">${escapeHtml(source.name)}</option>`).join(""),
  );
  sourceCards();
  renderItems(false);
  [
    "sourceDiscoverySourceFilter",
    "sourceDiscoveryIdentityFilter",
    "sourceDiscoveryAssetFilter",
  ].forEach((id) => byId(id).addEventListener("change", () => renderItems(true)));
  byId("sourceDiscoverySearch").addEventListener("input", () => renderItems(true));
  byId("sourceDiscoveryPrevious").addEventListener("click", () => {
    page -= 1;
    renderItems(false);
  });
  byId("sourceDiscoveryNext").addEventListener("click", () => {
    page += 1;
    renderItems(false);
  });
}());
