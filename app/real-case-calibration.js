(() => {
  const calibration = window.PENGUIN_CONVEXITY_REAL_CASES;
  if (!calibration) {
    document.getElementById("calibrationStatus").textContent = "真实校准快照读取失败";
    return;
  }

  const stateLabels = calibration.stateMachine.states;
  const boundaryLabels = {
    confirmed_fact: "已确认事实",
    project_claim: "项目方陈述",
    regulator_allegation: "监管或司法指控",
    court_confirmed: "法院确认",
    legal_record: "司法文件记录",
  };
  const marketReasonLabels = {
    no_supported_symbol: "没有受支持交易对",
    pair_not_listed_at_event: "事件时尚未上市或无历史记录",
    unsupported_or_delisted: "交易对已下线或不受支持",
    upstream_http_error: "行情来源返回错误",
    temporary_source_failure: "行情来源暂时失败",
    market_fetch_not_run: "尚未运行行情抓取",
  };
  let selectedCaseId = calibration.results[0]?.caseId || "";
  let selectedType = "all";
  let selectedMarketStatus = "all";

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
        }).format(new Date(value))
      : "未生成";

  const stateLabel = (state) => stateLabels[state]?.label || state;
  const sequenceLabel = (sequence) => sequence.map(stateLabel).join(" → ");
  const formatPrice = (value) => {
    if (value == null) return "--";
    if (value >= 100) return `US$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
    if (value >= 1) return `US$${value.toFixed(3)}`;
    return `US$${value.toPrecision(4)}`;
  };
  const formatPct = (value) => {
    if (value == null) return "--";
    return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
  };

  function renderSummary() {
    const summary = calibration.summary;
    document.getElementById("calibrationStatus").textContent =
      summary.failedCount === 0 ? "20 个真实案例校准通过" : `${summary.failedCount} 个案例需要复核`;
    document.getElementById("calibrationGeneratedAt").textContent =
      `校准 ${formatTime(calibration.generatedAt)}`;
    document.getElementById("calibrationNotice").textContent = calibration.notice;
    document.getElementById("realCaseCount").textContent = summary.caseCount;
    document.getElementById("realPassedCount").textContent =
      `${summary.passedCount}/${summary.caseCount}`;
    document.getElementById("transitionIssueCount").textContent = summary.transitionIssueCount;
    document.getElementById("primaryEvidenceCount").textContent =
      `${summary.primaryEvidenceCaseCount}/${summary.caseCount}`;
    document.getElementById("marketAvailableCount").textContent =
      `${summary.marketAvailableCount}/${summary.caseCount}`;
  }

  function renderFilters() {
    const select = document.getElementById("realCaseTypeFilter");
    Object.entries(calibration.caseTypeCounts).forEach(([type, count]) => {
      const option = document.createElement("option");
      option.value = type;
      option.textContent = `${type}（${count}）`;
      select.append(option);
    });
    select.addEventListener("change", () => {
      selectedType = select.value;
      renderCaseList();
    });
    document.getElementById("marketStatusFilter").addEventListener("change", (event) => {
      selectedMarketStatus = event.target.value;
      renderCaseList();
    });
  }

  function filteredCases() {
    return calibration.results.filter((item) => {
      const typeMatches = selectedType === "all" || item.caseType === selectedType;
      const marketMatches =
        selectedMarketStatus === "all" || item.marketReaction.status === selectedMarketStatus;
      return typeMatches && marketMatches;
    });
  }

  function renderCaseList() {
    const cases = filteredCases();
    if (!cases.some((item) => item.caseId === selectedCaseId)) {
      selectedCaseId = cases[0]?.caseId || "";
    }
    document.getElementById("realVisibleCount").textContent = `${cases.length} 条`;
    document.getElementById("realCaseList").innerHTML = cases.length
      ? cases
          .map(
            (item) => `
              <button type="button" class="case-list-item ${item.caseId === selectedCaseId ? "active" : ""}"
                data-real-case-id="${escapeHtml(item.caseId)}">
                <span>
                  <b>${escapeHtml(item.caseType)}</b>
                  <i class="${item.marketReaction.status === "available" ? "market-ok" : "market-missing"}">
                    ${item.marketReaction.status === "available" ? "有行情" : "行情缺失"}
                  </i>
                </span>
                <strong>${escapeHtml(item.project)} · ${escapeHtml(item.asset)}</strong>
                <small>${escapeHtml(sequenceLabel(item.actualSequence))}</small>
              </button>`
          )
          .join("")
      : `<div class="case-empty">当前筛选下没有案例。</div>`;

    document.querySelectorAll("[data-real-case-id]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedCaseId = button.dataset.realCaseId;
        renderCaseList();
      });
    });
    renderCaseDetail();
  }

  function renderSources(item) {
    return item.sources
      .map(
        (source) => `
          <article class="evidence-item">
            <div>
              <span class="evidence-boundary boundary-${escapeHtml(source.factBoundary)}">
                ${escapeHtml(boundaryLabels[source.factBoundary] || source.factBoundary)}
              </span>
              <small>${escapeHtml(source.sourceType)} · ${escapeHtml(source.publisher)} · ${escapeHtml(source.publishedAt)}</small>
            </div>
            <h5>${escapeHtml(source.title)}</h5>
            <p>${escapeHtml(source.summary)}</p>
            <a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">打开原始证据</a>
          </article>`
      )
      .join("");
  }

  function renderMarket(item) {
    const market = item.marketReaction;
    if (market.status !== "available") {
      return `
        <section class="market-panel market-unavailable">
          <header><span>MARKET REACTION</span><h4>历史行情不可得</h4></header>
          <strong>${escapeHtml(marketReasonLabels[market.reason] || "行情缺失")}</strong>
          <p>${escapeHtml(market.detail)}</p>
          <small>缺失值保持为空，不使用当前价格、第三方截图或估算值回填。</small>
        </section>`;
    }
    const points = market.points;
    const changes = market.changesPct;
    return `
      <section class="market-panel">
        <header>
          <div><span>MARKET REACTION</span><h4>事件窗口行情</h4></div>
          <small>${escapeHtml(market.provider)} · ${escapeHtml(market.symbol)}</small>
        </header>
        <div class="market-points">
          <div><span>事件前 30 日</span><strong>${formatPrice(points.pre30?.close)}</strong><small>${escapeHtml(points.pre30?.date || "--")}</small></div>
          <div><span>事件日附近</span><strong>${formatPrice(points.event?.close)}</strong><small>${escapeHtml(points.event?.date || "--")}</small></div>
          <div><span>事件后 7 日</span><strong>${formatPct(changes.eventToPost7)}</strong><small>${formatPrice(points.post7?.close)}</small></div>
          <div><span>事件后 30 日</span><strong>${formatPct(changes.eventToPost30)}</strong><small>${formatPrice(points.post30?.close)}</small></div>
          <div><span>事件后 90 日</span><strong>${formatPct(changes.eventToPost90)}</strong><small>${formatPrice(points.post90?.close)}</small></div>
        </div>
        <p>事件前 30 日至事件日：<strong>${formatPct(changes.pre30ToEvent)}</strong>。价格反应用于检查赔率是否衰减，不证明因果。</p>
      </section>`;
  }

  function renderTimeline(item) {
    return item.timeline
      .map(
        (point, index) => `
          <article class="real-timeline-step">
            <span>${String(index + 1).padStart(2, "0")}</span>
            <div>
              <header><time>${escapeHtml(point.at)}</time><strong>${escapeHtml(point.label)}</strong></header>
              <p>${escapeHtml(stateLabel(point.state))} · 错配分 ${point.score.totalScore} · ${escapeHtml(point.reason)}</p>
            </div>
          </article>`
      )
      .join("");
  }

  function renderCaseDetail() {
    const item = calibration.results.find((result) => result.caseId === selectedCaseId);
    const container = document.getElementById("realCaseDetail");
    if (!item) {
      container.innerHTML = `<div class="case-empty">请选择一个真实案例。</div>`;
      return;
    }
    container.innerHTML = `
      <header class="case-title real-case-title">
        <div>
          <span>${escapeHtml(item.caseType)} · ${escapeHtml(item.eventAt)}</span>
          <h3>${escapeHtml(item.title)}</h3>
          <p>${escapeHtml(item.calibrationQuestion)}</p>
        </div>
        <strong class="${item.passed ? "case-pass" : "case-fail"}">${item.passed ? "规则一致" : "需要复核"}</strong>
      </header>

      <section class="sequence-compare">
        <div><span>人工事前预期</span><strong>${escapeHtml(sequenceLabel(item.expectedSequence))}</strong></div>
        <div><span>规则引擎实际</span><strong>${escapeHtml(sequenceLabel(item.actualSequence))}</strong></div>
      </section>

      <section class="reason-panel">
        <span>校准结论</span>
        <strong>${escapeHtml(item.calibrationNote)}</strong>
        ${item.adjudicationNote ? `<p>复核记录：${escapeHtml(item.adjudicationNote)}</p>` : ""}
      </section>

      <div class="real-case-columns">
        <section class="evidence-panel">
          <header><span>EVIDENCE</span><h4>原始证据与边界</h4></header>
          ${renderSources(item)}
        </section>
        <section class="case-history-panel">
          <header><span>DECISION HISTORY</span><h4>规则时间线</h4></header>
          ${renderTimeline(item)}
        </section>
      </div>

      ${renderMarket(item)}
    `;
  }

  renderSummary();
  renderFilters();
  renderCaseList();
})();
