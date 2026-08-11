(() => {
  const calibration = window.PENGUIN_CONVEXITY_GOLD_CALIBRATION;
  if (!calibration) {
    document.getElementById("goldCaseList").innerHTML = '<div class="case-empty">黄金校准集读取失败。</div>';
    return;
  }

  const state = {
    selectedId: calibration.cases[0]?.id || "",
    search: "",
    cohort: "all",
    action: "all",
    maturity: "all",
  };
  const cohortOrder = ["core_positive", "extreme_boundary", "observe_only", "rejected"];
  const cohortClasses = {
    core_positive: "positive",
    extreme_boundary: "extreme",
    observe_only: "observe",
    rejected: "rejected",
  };
  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  const formatTime = (value) =>
    value
      ? new Intl.DateTimeFormat("zh-CN", {
          year: "numeric",
          month: "numeric",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }).format(new Date(value))
      : "--";

  function cohortLabel(id) {
    return calibration.cohorts[id]?.label || id;
  }

  function renderSummary() {
    const counts = calibration.summary.cohortCounts;
    document.getElementById("goldBoundary").textContent = calibration.source.boundary;
    document.getElementById("goldCapturedAt").textContent = `任务截点 ${formatTime(calibration.capturedAt)}`;
    document.getElementById("goldCaseCount").textContent = calibration.summary.caseCount;
    document.getElementById("goldPositiveCount").textContent = counts.core_positive || 0;
    document.getElementById("goldExtremeCount").textContent = counts.extreme_boundary || 0;
    document.getElementById("goldObserveCount").textContent = counts.observe_only || 0;
    document.getElementById("goldRejectedCount").textContent = counts.rejected || 0;
    document.getElementById("goldSourceMeta").textContent =
      `来源：${calibration.source.threadTitle}任务 · ${calibration.summary.sourceTurnCount}次筛选截面`;
  }

  function addOptions(selectId, values, labeler = (value) => value) {
    const select = document.getElementById(selectId);
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = labeler(value);
      select.append(option);
    });
  }

  function renderFilters() {
    addOptions("goldCohortFilter", cohortOrder, cohortLabel);
    addOptions("goldActionFilter", [...new Set(calibration.cases.map((item) => item.action))]);
    addOptions("goldMaturityFilter", [...new Set(calibration.cases.map((item) => item.maturity))]);
    document.getElementById("goldSearch").addEventListener("input", (event) => {
      state.search = event.target.value.trim().toLowerCase();
      renderList();
    });
    document.getElementById("goldCohortFilter").addEventListener("change", (event) => {
      state.cohort = event.target.value;
      renderList();
    });
    document.getElementById("goldActionFilter").addEventListener("change", (event) => {
      state.action = event.target.value;
      renderList();
    });
    document.getElementById("goldMaturityFilter").addEventListener("change", (event) => {
      state.maturity = event.target.value;
      renderList();
    });
  }

  function filteredCases() {
    return calibration.cases.filter((item) => {
      const searchable = `${item.project} ${item.asset} ${item.primaryConvexity} ${item.modelLesson}`.toLowerCase();
      return (!state.search || searchable.includes(state.search))
        && (state.cohort === "all" || item.cohort === state.cohort)
        && (state.action === "all" || item.action === state.action)
        && (state.maturity === "all" || item.maturity === state.maturity);
    });
  }

  function renderList() {
    const cases = filteredCases();
    if (!cases.some((item) => item.id === state.selectedId)) {
      state.selectedId = cases[0]?.id || "";
    }
    document.getElementById("goldVisibleCount").textContent = `${cases.length} 条`;
    document.getElementById("goldCaseList").innerHTML = cases.length
      ? cases.map((item) => `
        <button type="button" class="gold-case-row ${item.id === state.selectedId ? "active" : ""}" data-gold-case-id="${escapeHtml(item.id)}">
          <span class="gold-rank">${String(item.priority).padStart(2, "0")}</span>
          <span class="gold-case-main">
            <small class="gold-cohort ${cohortClasses[item.cohort]}">${escapeHtml(cohortLabel(item.cohort))}</small>
            <strong>${escapeHtml(item.project)} · ${escapeHtml(item.asset)}</strong>
            <em>${escapeHtml(item.maturity)} · ${escapeHtml(item.risk)} · ${escapeHtml(item.action)}</em>
          </span>
          <span class="gold-score">${item.score == null ? "--" : item.score}<small>错配分</small></span>
        </button>`).join("")
      : '<div class="case-empty">当前筛选没有案例。</div>';
    document.querySelectorAll("[data-gold-case-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedId = button.dataset.goldCaseId;
        renderList();
      });
    });
    renderDetail();
  }

  function renderLinks(item) {
    if (!item.sourceLinks.length) return '<span class="gold-no-link">本条只保留任务内判断，等待下一轮补充原始链接。</span>';
    return item.sourceLinks
      .map((link) => `<a href="${escapeHtml(link.url)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`)
      .join("");
  }

  function renderDetail() {
    const item = calibration.cases.find((candidate) => candidate.id === state.selectedId);
    const container = document.getElementById("goldCaseDetail");
    if (!item) {
      container.innerHTML = '<div class="case-empty">请选择一个黄金案例。</div>';
      return;
    }
    const cohort = calibration.cohorts[item.cohort];
    container.innerHTML = `
      <header class="gold-detail-header">
        <div>
          <span class="gold-cohort ${cohortClasses[item.cohort]}">${escapeHtml(cohort.label)}</span>
          <h2>${escapeHtml(item.project)} <small>${escapeHtml(item.asset)}</small></h2>
          <p>${escapeHtml(item.primaryConvexity)}</p>
        </div>
        <div class="gold-detail-score"><strong>${item.score == null ? "--" : item.score}</strong><span>错配分</span></div>
      </header>
      <section class="gold-decision-strip">
        <div><span>动作</span><strong>${escapeHtml(item.action)}</strong></div>
        <div><span>线索位置</span><strong>${escapeHtml(item.maturity)}</strong></div>
        <div><span>风险</span><strong>${escapeHtml(item.risk)}</strong></div>
        <div><span>剩余凸性</span><strong>${escapeHtml(item.remainingConvexity)}</strong></div>
      </section>
      <section class="gold-detail-section">
        <h3>任务中的硬事实</h3>
        <ul>${item.facts.map((fact) => `<li>${escapeHtml(fact)}</li>`).join("")}</ul>
      </section>
      <section class="gold-reason-grid">
        <article><span>为什么纳入</span><p>${escapeHtml(item.whyIncluded)}</p></article>
        <article><span>为什么不能更高</span><p>${escapeHtml(item.whyNotHigher)}</p></article>
        <article><span>点火条件</span><p>${escapeHtml(item.ignition)}</p></article>
        <article><span>失效条件</span><p>${escapeHtml(item.invalidation)}</p></article>
      </section>
      <section class="gold-model-lesson">
        <span>模型必须学会</span>
        <strong>${escapeHtml(item.modelLesson)}</strong>
        <p>${escapeHtml(cohort.modelRequirement)}</p>
      </section>
      <footer class="gold-detail-footer">
        <div><span>任务截面</span><strong>${escapeHtml(item.sourceDate)} · ${escapeHtml(item.sourceTurnId)}</strong></div>
        <nav>${renderLinks(item)}</nav>
      </footer>`;
  }

  renderSummary();
  renderFilters();
  renderList();
})();
