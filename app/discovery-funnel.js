(function initializeDiscoveryFunnel() {
  const snapshot = window.PENGUIN_CONVEXITY_DISCOVERY_FUNNEL;
  const blockerById = Object.fromEntries((snapshot?.blockers || []).map((item) => [item.id, item]));
  const pageSize = 100;
  let page = 1;
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const number = (value) => Number(value || 0).toLocaleString("zh-CN");
  const dateTime = (value) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? String(value || "--")
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };

  function renderStages() {
    byId("discoveryFunnelStages").innerHTML = snapshot.stages.map((stage, index) => `
      <article>
        <span>0${stage.id}</span>
        <h3>${escapeHtml(stage.label)}</h3>
        <p>${escapeHtml(stage.question)}</p>
        <strong>${number(stage.passed)}</strong>
        <dl>
          <div><dt>进入本层</dt><dd>${number(stage.entered)}</dd></div>
          <div><dt>等待补证</dt><dd>${number(stage.waiting)}</dd></div>
          <div><dt>阻断 / 非行动</dt><dd>${number(stage.blocked)}</dd></div>
        </dl>
        ${index < snapshot.stages.length - 1 ? '<i aria-hidden="true">→</i>' : ""}
      </article>`).join("");
  }

  function renderBlockers() {
    const maxCount = Math.max(...snapshot.blockers.map((item) => item.count), 1);
    byId("discoveryFunnelBlockers").innerHTML = snapshot.blockers.map((item) => `
      <button type="button" data-blocker="${escapeHtml(item.id)}">
        <span><b>${escapeHtml(item.label)}</b><em>${number(item.count)}</em></span>
        <i><u style="width:${Math.max(2, item.count / maxCount * 100)}%"></u></i>
      </button>`).join("");
    document.querySelectorAll("[data-blocker]").forEach((button) => {
      button.addEventListener("click", () => {
        byId("discoveryFunnelBlockerFilter").value = button.dataset.blocker;
        page = 1;
        renderRows();
      });
    });
  }

  function addOptions() {
    snapshot.stages.forEach((stage) => {
      byId("discoveryFunnelStageFilter").insertAdjacentHTML(
        "beforeend",
        `<option value="${stage.id}">${escapeHtml(stage.label)}</option>`,
      );
    });
    snapshot.blockers.forEach((item) => {
      byId("discoveryFunnelBlockerFilter").insertAdjacentHTML(
        "beforeend",
        `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}（${number(item.count)}）</option>`,
      );
    });
    snapshot.sources.forEach((source) => {
      byId("discoveryFunnelSourceFilter").insertAdjacentHTML(
        "beforeend",
        `<option value="${escapeHtml(source.source_id)}">${escapeHtml(source.name)}</option>`,
      );
    });
  }

  function filteredItems() {
    const stage = byId("discoveryFunnelStageFilter").value;
    const blocker = byId("discoveryFunnelBlockerFilter").value;
    const source = byId("discoveryFunnelSourceFilter").value;
    const query = byId("discoveryFunnelSearch").value.trim().toLowerCase();
    return snapshot.items.filter((item) => {
      if (stage !== "all" && String(item.reachedStage) !== stage) return false;
      if (blocker !== "all" && item.blocker !== blocker) return false;
      if (source !== "all" && !item.sourceIds.includes(source)) return false;
      if (!query) return true;
      const definition = blockerById[item.blocker] || {};
      return [
        item.canonicalName,
        item.categories.join(" "),
        item.sourceNames.join(" "),
        definition.label,
        definition.reason,
        definition.nextAction,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }

  function sourceBadges(item) {
    return item.sourceNames.map((name) => `<span>${escapeHtml(name)}</span>`).join("");
  }

  function projectAnchor(item) {
    const url = item.websiteUrl || item.repositoryUrl || item.socialUrl;
    return url
      ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(item.canonicalName)}</a>`
      : `<strong>${escapeHtml(item.canonicalName)}</strong>`;
  }

  function renderRows() {
    const items = filteredItems();
    const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
    page = Math.min(page, pageCount);
    const visible = items.slice((page - 1) * pageSize, page * pageSize);
    byId("discoveryFunnelRows").innerHTML = visible.length
      ? visible.map((item) => {
        const definition = blockerById[item.blocker] || {};
        return `
        <tr>
          <td>
            ${projectAnchor(item)}
            <small>${escapeHtml(item.categories.join(" · ") || "未分类")}</small>
          </td>
          <td><div class="discovery-funnel-source-badges">${sourceBadges(item)}</div><small>${item.recordCount} 条原始记录</small></td>
          <td><b>0${item.reachedStage} ${escapeHtml(snapshot.stages[item.reachedStage].label)}</b><small>${item.matchedProjectId ? `项目ID：${escapeHtml(item.matchedProjectId)}` : "尚未关联正式项目"}</small></td>
          <td><strong>${escapeHtml(definition.label || item.blocker)}</strong><small>${escapeHtml(definition.reason || "暂无解释")}</small></td>
          <td><p>${escapeHtml(definition.nextAction || "等待人工核验")}</p></td>
        </tr>`;
      }).join("")
      : '<tr><td class="discovery-funnel-empty" colspan="5">没有符合当前筛选的项目。</td></tr>';
    byId("discoveryFunnelFilteredCount").textContent = number(items.length);
    byId("discoveryFunnelVisibleCount").textContent = number(visible.length);
    byId("discoveryFunnelPageLabel").textContent = `第 ${page} / ${pageCount} 页`;
    byId("discoveryFunnelPrevious").disabled = page <= 1;
    byId("discoveryFunnelNext").disabled = page >= pageCount;
  }

  if (!snapshot || !Array.isArray(snapshot.items)) {
    document.body.innerHTML = '<main class="snapshot-error"><h1>发现漏斗暂时无法读取</h1><p>请返回凸性更新中心，重新执行“项目发现”。</p></main>';
    return;
  }

  byId("discoveryFunnelPolicy").textContent = snapshot.policy;
  byId("discoveryFunnelGeneratedAt").textContent = `快照生成：${dateTime(snapshot.generatedAt)}`;
  byId("discoveryFunnelConclusion").textContent = snapshot.counts.actionReady
    ? `当前有 ${number(snapshot.counts.actionReady)} 个项目进入行动级`
    : `当前没有项目从来源发现完整通过至行动级`;
  [
    ["discoveryFunnelTotal", "total"],
    ["discoveryFunnelProject", "projectVerified"],
    ["discoveryFunnelAsset", "assetVerified"],
    ["discoveryFunnelCapture", "valueCaptureVerified"],
    ["discoveryFunnelCases", "researchCases"],
    ["discoveryFunnelAction", "actionReady"],
  ].forEach(([id, key]) => { byId(id).textContent = number(snapshot.counts[key]); });
  byId("discoveryFunnelSeparateCount").textContent = number(snapshot.separateCandidateBranch.count);
  byId("discoveryFunnelSeparateNote").textContent = snapshot.separateCandidateBranch.note;

  addOptions();
  renderStages();
  renderBlockers();
  renderRows();
  [
    "discoveryFunnelStageFilter",
    "discoveryFunnelBlockerFilter",
    "discoveryFunnelSourceFilter",
    "discoveryFunnelSearch",
  ].forEach((id) => byId(id).addEventListener(id.endsWith("Search") ? "input" : "change", () => {
    page = 1;
    renderRows();
  }));
  byId("discoveryFunnelPrevious").addEventListener("click", () => {
    page -= 1;
    renderRows();
  });
  byId("discoveryFunnelNext").addEventListener("click", () => {
    page += 1;
    renderRows();
  });
}());
