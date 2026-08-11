(() => {
  const model = window.PENGUIN_CONVEXITY_FOUR_LAYER;
  const listContainer = document.getElementById("fourLayerCaseList");
  if (!model) {
    listContainer.innerHTML = '<div class="case-empty">四层筛选结果读取失败。</div>';
    return;
  }

  const state = {
    mode: "live",
    selectedId: model.live.cases[0]?.id || "",
    search: "",
    action: "all",
    stoppedLayer: "all",
  };
  const actionOrder = ["ordinary", "extreme", "observe", "reflexive", "reject"];
  const actionLabels = {
    ordinary: "普通建仓",
    extreme: "极限试仓",
    observe: "只观察",
    reflexive: "反身性管理",
    reject: "排除",
  };
  const statusLabels = { pass: "通过", pending: "待核验", fail: "未通过" };
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

  function currentScope() {
    return state.mode === "goldCalibration" ? model.calibration : model.live;
  }

  function renderSummary() {
    const live = model.live.summary;
    const goldCalibration = model.calibration.summary;
    document.getElementById("fourLayerBoundary").textContent = model.boundary;
    document.getElementById("fourLayerGeneratedAt").textContent = `生成时间 ${formatTime(model.generatedAt)}`;
    document.getElementById("fourLayerTotal").textContent = live.total;
    document.getElementById("fourLayerOrdinary").textContent = live.actionCounts.ordinary || 0;
    document.getElementById("fourLayerExtreme").textContent = live.actionCounts.extreme || 0;
    document.getElementById("fourLayerObserve").textContent = live.actionCounts.observe || 0;
    document.getElementById("fourLayerAccuracy").textContent = `${goldCalibration.accuracyPct}%`;
    document.getElementById("fourLayerAccuracyMeta").textContent =
      `${goldCalibration.matched}/${goldCalibration.total} 个案例一致`;
  }

  function renderFlow() {
    const summary = currentScope().summary;
    const counts = summary.layerStatusCounts;
    document.getElementById("fourLayerFlow").innerHTML = model.layerDefinitions
      .map((layer) => {
        const status = counts[String(layer.id)] || {};
        const metrics = layer.id === 4
          ? [
              ["行动", (summary.actionCounts.ordinary || 0) + (summary.actionCounts.extreme || 0)],
              ["观察", summary.actionCounts.observe || 0],
              ["转出", (summary.actionCounts.reflexive || 0) + (summary.actionCounts.reject || 0)],
            ]
          : [
              ["通过", status.pass || 0],
              ["待核验", status.pending || 0],
              ["未通过", status.fail || 0],
            ];
        return `
          <article>
            <span>0${layer.id}</span>
            <div><strong>${escapeHtml(layer.label)}</strong><p>${escapeHtml(layer.question)}</p></div>
            <dl>
              ${metrics.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("")}
            </dl>
          </article>`;
      })
      .join("");
  }

  function renderOptions() {
    const actionSelect = document.getElementById("fourLayerAction");
    actionOrder.forEach((action) => {
      const option = document.createElement("option");
      option.value = action;
      option.textContent = actionLabels[action];
      actionSelect.append(option);
    });
    const stopSelect = document.getElementById("fourLayerStop");
    model.layerDefinitions.forEach((layer) => {
      const option = document.createElement("option");
      option.value = String(layer.id);
      option.textContent = `第${layer.id}层 ${layer.label}`;
      stopSelect.append(option);
    });
  }

  function filteredCases() {
    return currentScope().cases.filter((item) => {
      const searchable = `${item.project} ${item.asset} ${item.primaryConvexity}`.toLowerCase();
      return (!state.search || searchable.includes(state.search))
        && (state.action === "all" || item.actionCategory === state.action)
        && (state.stoppedLayer === "all" || String(item.stoppedLayer) === state.stoppedLayer);
    });
  }

  function renderList() {
    const cases = filteredCases();
    if (!cases.some((item) => item.id === state.selectedId)) {
      state.selectedId = cases[0]?.id || "";
    }
    document.getElementById("fourLayerListTitle").textContent =
      state.mode === "goldCalibration" ? "黄金集回放" : "当前候选";
    document.getElementById("fourLayerVisibleCount").textContent = `${cases.length} 条`;
    listContainer.innerHTML = cases.length
      ? cases
          .map(
            (item, index) => `
              <button type="button" class="four-layer-case-row ${item.id === state.selectedId ? "active" : ""}" data-four-layer-id="${escapeHtml(item.id)}">
                <span class="four-layer-rank">${String(index + 1).padStart(2, "0")}</span>
                <span class="four-layer-case-main">
                  <small class="four-layer-action ${item.actionCategory}">${escapeHtml(item.actionLabel)}</small>
                  <strong>${escapeHtml(item.project)} · ${escapeHtml(item.asset)}</strong>
                  <em>${escapeHtml(item.maturity)} · ${escapeHtml(item.risk)} · ${escapeHtml(item.stoppedLayerLabel)}</em>
                </span>
                <span class="four-layer-score">${item.mismatchScore == null ? "--" : item.mismatchScore}<small>错配分</small></span>
              </button>`
          )
          .join("")
      : '<div class="case-empty">当前筛选没有项目。</div>';
    document.querySelectorAll("[data-four-layer-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedId = button.dataset.fourLayerId;
        renderList();
      });
    });
    renderDetail();
  }

  function renderLayer(layer, index) {
    return `
      <section class="four-layer-card ${layer.status}">
        <header>
          <span>0${index + 1}</span>
          <div><strong>${escapeHtml(layer.label)}</strong><p>${escapeHtml(layer.purpose)}</p></div>
          <em>${statusLabels[layer.status]}</em>
        </header>
        <ul>
          ${layer.checks
            .map(
              (check) => `
                <li class="${check.status}">
                  <span>${escapeHtml(check.label)}</span>
                  <strong>${escapeHtml(check.detail)}</strong>
                </li>`
            )
            .join("")}
        </ul>
      </section>`;
  }

  function renderDetail() {
    const item = currentScope().cases.find((candidate) => candidate.id === state.selectedId);
    const container = document.getElementById("fourLayerDetail");
    if (!item) {
      container.innerHTML = '<div class="case-empty">请选择一个项目。</div>';
      return;
    }
    const calibration = state.mode === "goldCalibration"
      ? `<div class="four-layer-calibration ${item.calibrationMatched ? "matched" : "mismatched"}">
          <span>黄金集验收</span>
          <strong>${item.calibrationMatched ? "模型复现一致" : "模型复现不一致"}</strong>
          <p>预期：${escapeHtml(item.expectedAction)} · 实际：${escapeHtml(item.actionLabel)}</p>
        </div>`
      : "";
    container.innerHTML = `
      <header class="four-layer-detail-header">
        <div>
          <span class="four-layer-action ${item.actionCategory}">${escapeHtml(item.actionLabel)}</span>
          <h2>${escapeHtml(item.project)} <small>${escapeHtml(item.asset)}</small></h2>
          <p>${escapeHtml(item.primaryConvexity || "主凸性来源待补充")}</p>
        </div>
        <div class="four-layer-detail-score"><strong>${item.mismatchScore == null ? "--" : item.mismatchScore}</strong><span>错配分</span></div>
      </header>
      <section class="four-layer-verdict">
        <div><span>最终动作</span><strong>${escapeHtml(item.actionLabel)}</strong></div>
        <div><span>线索位置</span><strong>${escapeHtml(item.maturity)}</strong></div>
        <div><span>风险等级</span><strong>${escapeHtml(item.risk)}</strong></div>
        <div><span>首先停在</span><strong>第${item.stoppedLayer}层</strong></div>
      </section>
      <section class="four-layer-conclusion">
        <span>为什么得到这个结果</span>
        <strong>${escapeHtml(item.actionReason)}</strong>
        <p>${escapeHtml(item.positionBoundary)}</p>
      </section>
      ${calibration}
      <div class="four-layer-card-grid">
        ${item.layers.map(renderLayer).join("")}
      </div>`;
  }

  function resetFilters() {
    state.search = "";
    state.action = "all";
    state.stoppedLayer = "all";
    document.getElementById("fourLayerSearch").value = "";
    document.getElementById("fourLayerAction").value = "all";
    document.getElementById("fourLayerStop").value = "all";
  }

  document.querySelectorAll("[data-screen-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.mode = button.dataset.screenMode;
      document.querySelectorAll("[data-screen-mode]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      resetFilters();
      state.selectedId = currentScope().cases[0]?.id || "";
      renderFlow();
      renderList();
    });
  });
  document.getElementById("fourLayerSearch").addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    renderList();
  });
  document.getElementById("fourLayerAction").addEventListener("change", (event) => {
    state.action = event.target.value;
    renderList();
  });
  document.getElementById("fourLayerStop").addEventListener("change", (event) => {
    state.stoppedLayer = event.target.value;
    renderList();
  });

  renderSummary();
  renderOptions();
  renderFlow();
  renderList();
})();
