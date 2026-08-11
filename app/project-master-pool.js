(function projectMasterPoolApp() {
  const state = window.PENGUIN_CONVEXITY_MASTER_POOL;
  const opportunityState = window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER;
  const routeState = window.PENGUIN_CONVEXITY_RESEARCH_ROUTES;
  const detailState = window.PENGUIN_CONVEXITY_PROJECT_DETAILS;
  const pageState = window.PenguinPageState;
  const restoredPageState = pageState?.load("project-master-pool") || {};
  const opportunityByCaseId = new Map(
    (opportunityState?.cases || []).map((item) => [item.caseId, item.opportunityStage]),
  );
  const routeByMasterId = new Map(
    (routeState?.records || []).map((item) => [item.masterId, item]),
  );
  const qualityByMasterId = new Map(
    Object.entries(detailState?.records || {}).map(([masterId, item]) => [
      masterId,
      item.automaticProfile,
    ]),
  );
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
  const dateTime = (value) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? (value || "--")
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const dateOnly = (value) => {
    if (!value) return "时间待核验";
    const parsed = new Date(`${String(value).slice(0, 10)}T00:00:00`);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleDateString("zh-CN");
  };
  const money = (value) => value == null
    ? "待补"
    : new Intl.NumberFormat("zh-CN", {
        style: "currency",
        currency: "USD",
        notation: Math.abs(Number(value)) >= 1000000 ? "compact" : "standard",
        maximumFractionDigits: 1,
      }).format(Number(value));
  const statusLabels = {
    published: "已发布",
    candidate: "候选",
    identity_pending: "身份待核验",
    rejected: "已排除",
  };
  const riskLabels = {
    low: "低",
    medium: "中",
    high: "高",
    blocked: "阻断",
    unknown: "待核验",
  };
  const lifecycleStatusLabels = {
    verified: "已核验",
    market_history: "市场记录",
    provisional: "临时证据",
    lower_bound: "仅有时间下界",
    pending: "待核验",
  };
  const observeFallback = {
    finalActionLabel: "只观察",
    finalActionReason: "尚未取得最新统一动作，旧数据库动作仅保留历史，不生成当前行动结论。",
  };
  const decisionFor = (item) => (
    item?.caseId ? opportunityByCaseId.get(item.caseId) || observeFallback : observeFallback
  );
  const routeFor = (item) => routeByMasterId.get(item?.masterId) || {
    routeId: "hybrid",
    routeLabel: "潜力项目",
    routeShortLabel: "潜力项目",
    routeReason: "生命周期分类快照待刷新。",
    routeSourceLabel: "系统待刷新",
    researchFocusLabel: "潜力项目",
    researchFocusSourceLabel: "系统待刷新",
    primaryFocus: "同时补齐基础档案与前置信号。",
    completeCount: 0,
    totalChecks: 0,
    nextEvidence: "刷新项目分类",
    queuePriorityScore: 0,
    queuePriorityBreakdown: [],
  };
  const qualityFor = (item) => qualityByMasterId.get(item?.masterId) || {
    score: 0,
    grade: "thin",
    gradeLabel: "档案待刷新",
    sections: [],
    missingCritical: [],
    nextAutoTask: {},
    boundary: "档案质量快照待刷新。",
  };
  let activeId = restoredPageState.activeId || "";
  let typeFilter = "all";
  let statusFilter = "all";
  let routeFilter = "all";
  let dateFilter = "all";
  let marketCapFilter = "all";
  let fdvFilter = "all";
  let liquidityFilter = "all";
  let qualityFilter = "all";
  let sortMode = "default";
  let searchTerm = "";
  let listScrollTop = Number(restoredPageState.listScrollTop || 0);

  if (!state) {
    byId("masterPoolStatus").textContent = "项目队列快照读取失败";
    byId("masterList").innerHTML = '<p class="empty-feedback">请先生成凸性项目队列。</p>';
    return;
  }

  byId("masterPoolStatus").textContent = "项目队列可用";
  byId("masterPoolGeneratedAt").textContent = `快照 ${dateTime(state.generatedAt)}`;
  byId("noQuotaPolicy").textContent = state.noQuotaPolicy;
  byId("publicationBoundary").textContent = state.publicationBoundary;
  byId("masterTotal").textContent = state.counts.total;
  byId("masterEarly").textContent = state.counts.early;
  byId("masterOg").textContent = state.counts.og;
  byId("masterOther").textContent = state.counts.other;
  byId("masterLifecyclePending").textContent = state.counts.lifecyclePending;
  byId("earlyLifecyclePolicy").textContent = state.lifecyclePolicy.early;
  byId("ogLifecyclePolicy").textContent = state.lifecyclePolicy.og;
  byId("otherLifecyclePolicy").textContent = state.lifecyclePolicy.other;

  const stateControlIds = [
    "masterTypeFilter",
    "masterStatusFilter",
    "masterRouteFilter",
    "masterDateFilter",
    "masterMarketCapFilter",
    "masterFdvFilter",
    "masterLiquidityFilter",
    "masterQualityFilter",
    "masterSort",
    "masterSearch",
  ];

  function persistPageState() {
    if (!pageState) return;
    pageState.save("project-master-pool", {
      activeId,
      listScrollTop,
      controls: Object.fromEntries(
        stateControlIds.map((id) => [id, byId(id).value]),
      ),
      scrollY: window.scrollY,
    });
  }

  function restorePageControls() {
    Object.entries(restoredPageState.controls || {}).forEach(([id, value]) => {
      const control = byId(id);
      if (!control) return;
      if (control instanceof HTMLSelectElement) {
        const allowed = [...control.options].some((item) => item.value === value);
        if (allowed) control.value = value;
      } else {
        control.value = String(value || "");
      }
    });
    typeFilter = byId("masterTypeFilter").value;
    statusFilter = byId("masterStatusFilter").value;
    routeFilter = byId("masterRouteFilter").value;
    dateFilter = byId("masterDateFilter").value;
    marketCapFilter = byId("masterMarketCapFilter").value;
    fdvFilter = byId("masterFdvFilter").value;
    liquidityFilter = byId("masterLiquidityFilter").value;
    qualityFilter = byId("masterQualityFilter").value;
    sortMode = byId("masterSort").value;
    searchTerm = byId("masterSearch").value.trim().toLowerCase();
  }

  function renderScanSummary() {
    const summary = state.scanSummary;
    if (!summary.latestRunId) {
      byId("scanSummary").innerHTML = '<p class="empty-feedback">现有项目已经进入项目队列；下一次扫描后，这里会逐项显示每条链、每个信源分别发现和排除了什么。</p>';
      return;
    }
    byId("scanRunLabel").textContent = `运行 ${summary.latestRunId} · ${dateTime(summary.observedAt)}`;
    byId("scanSummary").innerHTML = summary.rows.map((row) => `
      <article>
        <span>${escapeHtml(row.network_name)}</span>
        <strong>${escapeHtml(row.source_name)}</strong>
        <p>${escapeHtml(row.result_status)} · ${row.result_count} 条</p>
      </article>
    `).join("");
  }

  function inMoneyRange(value, preset, kind = "market") {
    if (preset === "all") return true;
    if (preset === "pending") return value == null;
    if (value == null) return false;
    const amount = Number(value);
    const marketRanges = {
      lt1m: [0, 1000000],
      "1m10m": [1000000, 10000000],
      "10m100m": [10000000, 100000000],
      "100m1b": [100000000, 1000000000],
      gte1b: [1000000000, Infinity],
    };
    const liquidityRanges = {
      lt20k: [0, 20000],
      "20k100k": [20000, 100000],
      "100k1m": [100000, 1000000],
      gte1m: [1000000, Infinity],
    };
    const [minimum, maximum] = (
      kind === "liquidity" ? liquidityRanges : marketRanges
    )[preset] || [0, Infinity];
    return amount >= minimum && amount < maximum;
  }

  function inDateWindow(item) {
    if (dateFilter === "all") return true;
    if (dateFilter === "pending") return !item.lifecycleDate;
    if (!item.lifecycleDate) return false;
    const parsed = new Date(`${item.lifecycleDate}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) return false;
    if (dateFilter === "180d") {
      const cutoff = new Date();
      cutoff.setHours(0, 0, 0, 0);
      cutoff.setMonth(cutoff.getMonth() - 6);
      return parsed.getTime() > cutoff.getTime();
    }
    const days = { "30d": 30, "90d": 90 }[dateFilter];
    return Date.now() - parsed.getTime() <= days * 86400000;
  }

  function numericSortValue(value, missingValue) {
    return value == null || Number.isNaN(Number(value))
      ? missingValue
      : Number(value);
  }

  function sortedRecords(records) {
    const rows = [...records];
    const byName = (left, right) => left.name.localeCompare(right.name, "zh-CN");
    if (sortMode === "dateDesc" || sortMode === "dateAsc") {
      const direction = sortMode === "dateDesc" ? -1 : 1;
      return rows.sort((left, right) => {
        if (!left.lifecycleDate && !right.lifecycleDate) return byName(left, right);
        if (!left.lifecycleDate) return 1;
        if (!right.lifecycleDate) return -1;
        return direction * left.lifecycleDate.localeCompare(right.lifecycleDate)
          || byName(left, right);
      });
    }
    const numericFields = {
      marketCapDesc: ["marketCapUsd", -1],
      marketCapAsc: ["marketCapUsd", 1],
      fdvDesc: ["fdvUsd", -1],
      liquidityDesc: ["liquidityUsd", -1],
      priorityDesc: ["queuePriorityScore", -1],
      qualityDesc: ["profileQualityScore", -1],
    };
    if (numericFields[sortMode]) {
      const [field, direction] = numericFields[sortMode];
      return rows.sort((left, right) => {
        const leftValue = field === "queuePriorityScore"
          ? routeFor(left)[field]
          : field === "profileQualityScore"
          ? qualityFor(left).score
          : left[field];
        const rightValue = field === "queuePriorityScore"
          ? routeFor(right)[field]
          : field === "profileQualityScore"
          ? qualityFor(right).score
          : right[field];
        const missing = direction > 0 ? Infinity : -Infinity;
        return direction * (
          numericSortValue(leftValue, missing)
          - numericSortValue(rightValue, missing)
        ) || byName(left, right);
      });
    }
    const routeOrder = { startup: 0, mature: 1, hybrid: 2 };
    return rows.sort((left, right) => {
      const leftRoute = routeFor(left).routeId;
      const rightRoute = routeFor(right).routeId;
      if (leftRoute !== rightRoute) {
        return (routeOrder[leftRoute] ?? 3) - (routeOrder[rightRoute] ?? 3);
      }
      if (leftRoute === "mature") {
        return (left.lifecycleDate || "9999").localeCompare(
          right.lifecycleDate || "9999",
        ) || byName(left, right);
      }
      if (leftRoute === "startup") {
        return (right.lifecycleDate || "").localeCompare(
          left.lifecycleDate || "",
        ) || (routeFor(right).queuePriorityScore - routeFor(left).queuePriorityScore)
          || byName(left, right);
      }
      return (routeFor(right).queuePriorityScore - routeFor(left).queuePriorityScore)
        || byName(left, right);
    });
  }

  function filteredRecords() {
    return sortedRecords(state.records.filter((item) => {
      if (typeFilter !== "all" && item.recordType !== typeFilter) return false;
      if (statusFilter !== "all" && item.poolStatus !== statusFilter) return false;
      if (routeFilter !== "all" && routeFor(item).routeId !== routeFilter) return false;
      if (!inDateWindow(item)) return false;
      if (!inMoneyRange(item.marketCapUsd, marketCapFilter)) return false;
      if (!inMoneyRange(item.fdvUsd, fdvFilter)) return false;
      if (!inMoneyRange(item.liquidityUsd, liquidityFilter, "liquidity")) return false;
      if (qualityFilter !== "all" && qualityFor(item).grade !== qualityFilter) return false;
      if (!searchTerm) return true;
      return [
        item.name,
        item.symbol,
        item.networkName,
        item.contractAddress,
        item.convexitySource,
        routeFor(item).routeLabel,
      ].join(" ").toLowerCase().includes(searchTerm);
    }));
  }

  function renderProfileSummary(profile) {
    const missing = profile.missingCritical?.length
      ? profile.missingCritical.map((item) => `<li>${escapeHtml(item.label)} · ${escapeHtml(item.nextTaskLabel || "等待自动补齐")}</li>`).join("")
      : "<li>关键字段没有阻断项；仍需结合凸性逻辑、风险与交易条件判断。</li>";
    return `
      <section class="master-profile-summary grade-${escapeHtml(profile.grade)}">
        <header>
          <div><span>AUTOMATIC PROFILE</span><h4>自动档案质量</h4></div>
          <div><strong>${escapeHtml(profile.score)}<small>/100</small></strong><em>${escapeHtml(profile.gradeLabel)}</em></div>
        </header>
        <div class="master-profile-bars">
          ${(profile.sections || []).map((section) => `
            <div>
              <span>${escapeHtml(section.label)}</span>
              <i><b style="width:${Math.max(0, Math.min(100, Number(section.score) / Number(section.maxScore || 1) * 100))}%"></b></i>
              <strong>${escapeHtml(section.score)}/${escapeHtml(section.maxScore)}</strong>
            </div>
          `).join("")}
        </div>
        <div class="master-profile-next">
          <div><strong>关键缺失</strong><ul>${missing}</ul></div>
          <div>
            <strong>下一项自动任务</strong>
            <p>${escapeHtml(profile.nextAutoTask?.fieldLabel ? `补齐“${profile.nextAutoTask.fieldLabel}”` : "暂无关键补齐任务")}</p>
            ${profile.nextAutoTask?.href ? `<a href="${escapeHtml(profile.nextAutoTask.href)}">${escapeHtml(profile.nextAutoTask.taskLabel)}</a>` : ""}
          </div>
        </div>
        <small>${escapeHtml(profile.boundary)}</small>
      </section>
    `;
  }

  function renderDetail(item) {
    if (!item) {
      byId("masterDetail").innerHTML = '<p class="empty-feedback">当前筛选没有记录。</p>';
      return;
    }
    const assets = item.assets.length
      ? item.assets.map((asset) => `
          <tr>
            <td>${escapeHtml(asset.symbol || "--")}</td>
            <td>${escapeHtml(asset.chain || "--")}</td>
            <td><code>${escapeHtml(asset.contract_address || "待补齐")}</code></td>
            <td>${escapeHtml(asset.identity_status)}</td>
          </tr>
        `).join("")
      : '<tr><td colspan="4">尚未建立正式资产；链上发现记录保留原始合约，等待身份复核。</td></tr>';
    const sourceRows = item.sourceIds.length
      ? item.sourceIds.map((sourceId, index) => `
          <li><strong>${escapeHtml(sourceId)}</strong>${item.sourceUrls[index] ? ` · <a href="${escapeHtml(item.sourceUrls[index])}" target="_blank" rel="noreferrer">查看来源</a>` : ""}</li>
        `).join("")
      : "<li>正式项目来源保存在证据与研究记录中。</li>";
    const detailHref = `project-detail.html?id=${encodeURIComponent(item.masterId)}&from=queue`;
    const decision = decisionFor(item);
    const route = routeFor(item);
    const quality = qualityFor(item);
    byId("masterDetail").innerHTML = `
      <header>
        <div>
          <span>${item.recordType === "project" ? "正式项目主体" : "链上发现记录"}</span>
          <h3><a href="${detailHref}">${escapeHtml(item.name)} ${item.symbol ? `<small>${escapeHtml(item.symbol)}</small>` : ""}</a></h3>
          <p>${escapeHtml(item.statusReason)}</p>
        </div>
        <div class="master-detail-actions"><strong class="master-status status-${escapeHtml(item.poolStatus)}">${escapeHtml(statusLabels[item.poolStatus] || item.poolStatus)}</strong><a href="${detailHref}">打开完整详情</a></div>
      </header>
      <dl class="master-detail-grid">
        <div><dt>当前阶段</dt><dd>${escapeHtml(item.statusLabel)}</dd></div>
        <div><dt>项目分类</dt><dd>${escapeHtml(route.routeLabel)}</dd></div>
        <div><dt>启动日期</dt><dd>${escapeHtml(dateOnly(item.lifecycleDate))}</dd></div>
        <div><dt>存活时间</dt><dd>${escapeHtml(item.lifecycleAgeLabel)}</dd></div>
        <div><dt>风险</dt><dd>${escapeHtml(riskLabels[item.riskLevel] || item.riskLevel)}</dd></div>
        <div><dt>当前动作</dt><dd>${escapeHtml(decision.finalActionLabel)}</dd></div>
        <div><dt>研究优先分</dt><dd>${escapeHtml(route.queuePriorityScore)}</dd></div>
        <div><dt>档案完整度</dt><dd>${escapeHtml(quality.score)}/100 · ${escapeHtml(quality.gradeLabel)}</dd></div>
        <div><dt>主凸性来源</dt><dd>${escapeHtml(item.convexitySource || "待研究")}</dd></div>
        <div><dt>流通市值</dt><dd>${escapeHtml(money(item.marketCapUsd))}</dd></div>
        <div><dt>FDV</dt><dd>${escapeHtml(money(item.fdvUsd))}</dd></div>
        <div><dt>流动性</dt><dd>${escapeHtml(money(item.liquidityUsd))}</dd></div>
      </dl>
      <p class="master-action-reason"><strong>动作依据</strong>${escapeHtml(decision.finalActionReason)}</p>
      ${renderProfileSummary(quality)}
      <section class="master-lifecycle-summary">
        <header><div><span>LIFECYCLE</span><h4>${escapeHtml(route.routeLabel)}</h4></div><strong>${escapeHtml(item.lifecycleAgeLabel)}</strong></header>
        <p>${escapeHtml(item.lifecycleReason)}</p>
        <dl>
          <div><dt>日期依据</dt><dd>${escapeHtml(item.lifecycleDateBasis || "创建时间待补")}</dd></div>
          <div><dt>日期状态</dt><dd>${escapeHtml(lifecycleStatusLabels[item.lifecycleDateStatus] || item.lifecycleDateStatus)}</dd></div>
          <div><dt>自动换类</dt><dd>${escapeHtml(item.lifecycleAutoMoveAt ? dateOnly(item.lifecycleAutoMoveAt) : "当前无需迁移")}</dd></div>
        </dl>
        ${item.lifecycleSourceUrl
          ? `<a href="${escapeHtml(item.lifecycleSourceUrl)}" target="_blank" rel="noreferrer">查看时间来源 · ${escapeHtml(item.lifecycleSourceName || "原始来源")}</a>`
          : '<small>尚无可点击的创建时间来源。</small>'}
      </section>
      <section class="master-route-summary">
        <header><div><span>${escapeHtml(route.routeSourceLabel)}</span><h4>本类研究权重</h4></div><strong>${route.queuePriorityScore}</strong></header>
        <p>${escapeHtml(route.routeReason)}</p>
        <p><b>研究重点：</b>${escapeHtml(route.researchFocusLabel || route.routeLabel)} · ${escapeHtml(route.researchFocusSourceLabel || "跟随项目类别")}</p>
        <p><b>先查：</b>${escapeHtml(route.primaryFocus)}</p>
        <div class="master-priority-breakdown">${(route.queuePriorityBreakdown || []).map((entry) => `<span>${escapeHtml(entry.label)} <strong>${escapeHtml(entry.points)}</strong></span>`).join("")}</div>
        <small>下一项补证：${escapeHtml(route.nextEvidence)}</small>
      </section>
      <section>
        <h4>资产、合约与所在链</h4>
        ${item.contractAddress ? `<p class="master-contract"><strong>${escapeHtml(item.networkName)}</strong><code>${escapeHtml(item.contractAddress)}</code></p>` : ""}
        <table>
          <thead><tr><th>资产</th><th>所在链</th><th>合约</th><th>身份</th></tr></thead>
          <tbody>${assets}</tbody>
        </table>
      </section>
      <section>
        <h4>发现来源</h4>
        <ul class="master-source-list">${sourceRows}</ul>
      </section>
      <aside class="master-boundary">
        <strong>记录边界</strong>
        <p>${item.recordType === "project"
          ? `该主体有 ${item.caseCount} 个研究案例、${item.assetCount} 个资产、${item.evidenceCount} 条证据。人工标注不会覆盖自动事实。`
          : "链上存在、交易池可用或技术预检通过，都不等于项目身份和凸性价值已经成立。"}
        </p>
      </aside>
    `;
  }

  function renderRecords(options = {}) {
    const list = byId("masterList");
    if (options.resetScroll) {
      listScrollTop = 0;
    } else if (list?.childElementCount) {
      listScrollTop = list.scrollTop;
    }
    const rows = filteredRecords();
    byId("masterVisibleCount").textContent = `${rows.length} / ${state.counts.total}`;
    if (!rows.some((item) => item.masterId === activeId)) {
      activeId = rows[0]?.masterId || "";
    }
    byId("masterList").innerHTML = rows.length
      ? rows.map((item) => {
          const decision = decisionFor(item);
          const route = routeFor(item);
          const quality = qualityFor(item);
          return `
            <button type="button" data-master-id="${escapeHtml(item.masterId)}" class="${item.masterId === activeId ? "active" : ""}">
              <span><strong>${escapeHtml(item.name)}</strong><small class="quality-${escapeHtml(quality.grade)}">档案 ${escapeHtml(quality.score)}</small></span>
              <em>${escapeHtml(route.routeShortLabel || route.routeLabel)} · 研究 ${escapeHtml(route.queuePriorityScore)} · ${escapeHtml(item.symbol || item.networkName || "项目主体")} · 市值 ${escapeHtml(money(item.marketCapUsd))} · ${escapeHtml(decision.finalActionLabel)}</em>
            </button>
          `;
        }).join("")
      : '<p class="empty-feedback">当前筛选没有记录。</p>';
    byId("masterList").scrollTop = listScrollTop;
    renderDetail(rows.find((item) => item.masterId === activeId));
    byId("masterList").querySelectorAll("[data-master-id]").forEach((button) => {
      button.addEventListener("click", () => {
        activeId = button.dataset.masterId;
        renderRecords();
        persistPageState();
      });
    });
  }

  byId("masterTypeFilter").addEventListener("change", (event) => {
    typeFilter = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterStatusFilter").addEventListener("change", (event) => {
    statusFilter = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterRouteFilter").addEventListener("change", (event) => {
    routeFilter = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterDateFilter").addEventListener("change", (event) => {
    dateFilter = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterMarketCapFilter").addEventListener("change", (event) => {
    marketCapFilter = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterFdvFilter").addEventListener("change", (event) => {
    fdvFilter = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterLiquidityFilter").addEventListener("change", (event) => {
    liquidityFilter = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterQualityFilter").addEventListener("change", (event) => {
    qualityFilter = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterSort").addEventListener("change", (event) => {
    sortMode = event.target.value;
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterSearch").addEventListener("input", (event) => {
    searchTerm = event.target.value.trim().toLowerCase();
    renderRecords({ resetScroll: true });
    persistPageState();
  });
  byId("masterResetFilters").addEventListener("click", () => {
    typeFilter = "all";
    statusFilter = "all";
    routeFilter = "all";
    dateFilter = "all";
    marketCapFilter = "all";
    fdvFilter = "all";
    liquidityFilter = "all";
    qualityFilter = "all";
    sortMode = "default";
    searchTerm = "";
    [
      "masterTypeFilter",
      "masterStatusFilter",
      "masterRouteFilter",
      "masterDateFilter",
      "masterMarketCapFilter",
      "masterFdvFilter",
      "masterLiquidityFilter",
      "masterQualityFilter",
      "masterSort",
    ].forEach((id) => {
      byId(id).value = "all";
    });
    byId("masterSort").value = "default";
    byId("masterSearch").value = "";
    renderRecords({ resetScroll: true });
    persistPageState();
  });

  restorePageControls();
  renderScanSummary();
  renderRecords();
  if (restoredPageState.savedAt) {
    pageState?.notify("已恢复项目队列的筛选、当前项目和阅读位置");
  }
  pageState?.restoreScroll(restoredPageState, { skipWhenHash: true });
  let listScrollSaveTimer = 0;
  byId("masterList").addEventListener("scroll", () => {
    listScrollTop = byId("masterList").scrollTop;
    clearTimeout(listScrollSaveTimer);
    listScrollSaveTimer = setTimeout(persistPageState, 120);
  }, { passive: true });
  let scrollSaveTimer = 0;
  window.addEventListener("scroll", () => {
    clearTimeout(scrollSaveTimer);
    scrollSaveTimer = setTimeout(persistPageState, 160);
  }, { passive: true });
  window.addEventListener("pagehide", persistPageState);
})();
