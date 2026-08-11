(function monitoringInfrastructurePage() {
  const snapshot = window.PENGUIN_CONVEXITY_MONITORING_INFRASTRUCTURE;
  if (!snapshot) return;

  const state = { status: "all", type: "all", search: "", selectedId: "" };
  const byId = (id) => document.getElementById(id);
  const collectionLabels = {
    ready: "自动采集已接通",
    registered: "已登记待适配",
    blocked: "身份阻断",
    conflict: "归属冲突",
  };

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

  function renderSummary() {
    const counts = snapshot.counts;
    byId("monitoringBoundary").textContent = snapshot.boundary;
    byId("monitoringGeneratedAt").textContent = `生成时间：${dateTime(snapshot.generatedAt)}`;
    byId("monitoringProjectTotal").textContent = counts.projects.toLocaleString("zh-CN");
    byId("monitoringTargetTotal").textContent = counts.targets.toLocaleString("zh-CN");
    byId("monitoringReadyTotal").textContent = counts.readyTargets.toLocaleString("zh-CN");
    byId("monitoringRegisteredTotal").textContent = counts.registeredTargets.toLocaleString("zh-CN");
    byId("monitoringBlockedTotal").textContent = counts.blockedTargets.toLocaleString("zh-CN");
    byId("monitoringConflictTotal").textContent = counts.conflictTargets.toLocaleString("zh-CN");
  }

  function filteredProjects() {
    const query = state.search.trim().toLowerCase();
    return snapshot.projects.filter((item) => (
      (state.status === "all" || item.status === state.status)
      && (state.type === "all" || item.targetTypes.includes(state.type))
      && (!query || [
        item.projectName,
        ...(item.gaps || []),
        ...(item.targets || []).flatMap((target) => [
          target.targetTypeLabel,
          target.targetValue,
          target.sourceId,
          target.verificationMethod,
          target.gapReason,
        ]),
      ].join(" ").toLowerCase().includes(query))
    ));
  }

  function renderList(projects) {
    if (!projects.length) {
      byId("monitoringProjectList").innerHTML = '<p class="monitoring-infrastructure-empty">当前筛选下没有项目。</p>';
      return;
    }
    byId("monitoringProjectList").innerHTML = projects.map((item) => `
      <button type="button" data-project-id="${escapeHtml(item.projectId)}" class="${item.projectId === state.selectedId ? "is-selected" : ""}">
        <span class="monitoring-profile-status status-${escapeHtml(item.status)}">${escapeHtml(item.statusLabel)}</span>
        <strong>${escapeHtml(item.projectName)}</strong>
        <p>${item.gaps?.length ? escapeHtml(item.gaps.slice(0, 2).join(" · ")) : "监控基础设施字段无缺口"}</p>
        <footer>
          <span>目标 ${escapeHtml(item.targetCount)}</span>
          <span>可采集 ${escapeHtml(item.readyCount)}</span>
          <span>阻断 ${escapeHtml(item.blockedCount + item.conflictCount)}</span>
        </footer>
      </button>
    `).join("");
  }

  function renderDetail(project) {
    if (!project) {
      byId("monitoringProjectDetail").innerHTML = "<strong>选择一个项目</strong><p>这里会显示哪些目标已经接通、哪些仍被身份阻断，以及每个目标对应的原始记录和研究证据。</p>";
      return;
    }
    const targets = project.targets?.length
      ? project.targets.map((item) => {
          const url = safeUrl(item.targetUrl);
          const value = url
            ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(item.targetValue)}</a>`
            : `<strong>${escapeHtml(item.targetValue)}</strong>`;
          return `
            <article class="monitoring-target status-${escapeHtml(item.collectionStatus)}">
              <header><span>${escapeHtml(item.targetTypeLabel)}</span><b>${escapeHtml(collectionLabels[item.collectionStatus] || item.collectionStatus)}</b></header>
              ${value}
              <p>${escapeHtml(item.verificationMethod || item.gapReason || "等待核验")}</p>
              <dl>
                <dt>采集来源</dt><dd>${escapeHtml(item.sourceId || "内部项目主表")}</dd>
                <dt>原始记录</dt><dd>${item.rawEventId ? "已连接" : "待连接"}</dd>
                <dt>研究证据</dt><dd>${item.evidenceId ? "已连接" : "待连接"}</dd>
              </dl>
            </article>
          `;
        }).join("")
      : '<p class="monitoring-infrastructure-empty">当前项目尚无监控目标。</p>';
    const gaps = project.gaps?.length
      ? project.gaps.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
      : "<li>当前基础设施字段无缺口；等待真实事实与催化出现。</li>";
    byId("monitoringProjectDetail").innerHTML = `
      <span class="monitoring-profile-status status-${escapeHtml(project.status)}">${escapeHtml(project.statusLabel)}</span>
      <h3>${escapeHtml(project.projectName)}</h3>
      <p>共 ${escapeHtml(project.targetCount)} 个目标，其中 ${escapeHtml(project.readyCount)} 个已接通自动采集。</p>
      <section class="monitoring-gap-list"><h4>当前缺口</h4><ul>${gaps}</ul></section>
      <div class="monitoring-target-detail-list">${targets}</div>
      <footer><a href="${escapeHtml(project.detailUrl)}">进入项目详情</a></footer>
    `;
  }

  function render() {
    const projects = filteredProjects();
    byId("monitoringVisibleCount").textContent = `当前显示 ${projects.length.toLocaleString("zh-CN")} 个项目`;
    renderList(projects);
    renderDetail(snapshot.projects.find((item) => item.projectId === state.selectedId));
  }

  byId("monitoringStatusFilter").addEventListener("change", (event) => {
    state.status = event.target.value;
    state.selectedId = "";
    render();
  });
  byId("monitoringTypeFilter").addEventListener("change", (event) => {
    state.type = event.target.value;
    state.selectedId = "";
    render();
  });
  byId("monitoringSearch").addEventListener("input", (event) => {
    state.search = event.target.value;
    state.selectedId = "";
    render();
  });
  byId("monitoringProjectList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-project-id]");
    if (!button) return;
    state.selectedId = button.dataset.projectId;
    render();
  });

  const requestedProject = new URLSearchParams(location.search).get("project");
  if (requestedProject && snapshot.projects.some((item) => item.projectId === requestedProject)) {
    state.selectedId = requestedProject;
  }
  renderSummary();
  render();
})();
