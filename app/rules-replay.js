(() => {
  const replay = window.PENGUIN_CONVEXITY_REPLAY;
  if (!replay) {
    document.getElementById("ruleStatus").textContent = "规则快照读取失败";
    return;
  }

  const stateLabels = replay.stateMachine.states;
  const scoreLabels = {
    fact_certainty: "事实确定性",
    economic_increment: "经济增量",
    value_capture: "价值捕获",
    event_proximity: "事件临近",
    price_unreacted: "价格未反应",
  };
  const scoreMaximums = replay.rulebook.scoreWeights;
  const tradeabilityLabels = {
    standard: "标准",
    extreme: "极限",
    untradeable: "不可交易",
    unknown: "待核验",
  };
  const remainingLabels = {
    high: "高",
    medium: "中",
    low: "低",
    none: "无",
    unknown: "待核验",
  };
  let selectedCaseId = replay.results[0]?.caseId || "";
  let selectedType = "all";
  let selectedState = "";

  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  const formatTime = (value) =>
    new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));

  const stateLabel = (state) => stateLabels[state]?.label || state;
  const stateClass = (state) => `state-${state.replaceAll("_", "-")}`;

  function renderSummary() {
    const summary = replay.summary;
    document.getElementById("ruleStatus").textContent =
      summary.failedCount === 0 ? "规则回放全部通过" : `${summary.failedCount} 个案例未通过`;
    document.getElementById("replayGeneratedAt").textContent = `快照 ${formatTime(replay.generatedAt)}`;
    document.getElementById("fixtureNotice").textContent = replay.notice;
    document.getElementById("ruleVersion").textContent = replay.rulebook.version.replace("convexity-rules-", "");
    document.getElementById("caseCount").textContent = summary.caseCount;
    document.getElementById("passedCount").textContent = `${summary.passedCount}/${summary.caseCount}`;
    document.getElementById("stateCoverage").textContent =
      `${summary.coveredStateCount}/${summary.totalStateCount}`;
    document.getElementById("illegalTransitionCount").textContent =
      summary.allTransitionsLegal ? "0" : "有";
    document.getElementById("principleList").innerHTML = replay.rulebook.principles
      .map((principle) => `<span>${escapeHtml(principle)}</span>`)
      .join("");
  }

  function renderStateMachine() {
    const groups = {};
    Object.entries(stateLabels).forEach(([key, state]) => {
      groups[state.group] ||= [];
      groups[state.group].push({ key, ...state });
    });
    document.getElementById("stateMachine").innerHTML = Object.entries(groups)
      .map(
        ([group, states], groupIndex) => `
          <section class="state-lane">
            <header><span>0${groupIndex + 1}</span><strong>${escapeHtml(group)}</strong></header>
            ${states
              .map(
                (state) => `
                  <button class="state-node ${stateClass(state.key)} ${selectedState === state.key ? "selected" : ""}"
                    type="button" data-state="${state.key}">
                    <span>${escapeHtml(state.label)}</span>
                    <small>${replay.stateCoverage[state.key] || 0} 次回放经过</small>
                    <em>${escapeHtml(state.description)}</em>
                  </button>`
              )
              .join("")}
          </section>`
      )
      .join("");

    document.querySelectorAll("[data-state]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedState = button.dataset.state;
        document.getElementById("clearStateFilter").classList.remove("hidden");
        renderStateMachine();
        renderCaseList();
      });
    });
  }

  function filteredCases() {
    return replay.results.filter((item) => {
      const typeMatches = selectedType === "all" || item.caseType === selectedType;
      const stateMatches =
        !selectedState || item.timeline.some((point) => point.state === selectedState);
      return typeMatches && stateMatches;
    });
  }

  function renderTypeFilter() {
    const select = document.getElementById("caseTypeFilter");
    Object.keys(replay.caseTypeCounts).forEach((type) => {
      const option = document.createElement("option");
      option.value = type;
      option.textContent = `${type}（${replay.caseTypeCounts[type]}）`;
      select.append(option);
    });
    select.addEventListener("change", () => {
      selectedType = select.value;
      renderCaseList();
    });
  }

  function renderCaseList() {
    const cases = filteredCases();
    if (!cases.some((item) => item.caseId === selectedCaseId)) {
      selectedCaseId = cases[0]?.caseId || "";
    }
    document.getElementById("visibleCaseCount").textContent = `${cases.length} 条`;
    document.getElementById("caseList").innerHTML = cases.length
      ? cases
          .map(
            (item) => `
              <button type="button" class="case-list-item ${item.caseId === selectedCaseId ? "active" : ""}"
                data-case-id="${item.caseId}">
                <span><b>${escapeHtml(item.caseType)}</b><i>${item.passed ? "通过" : "异常"}</i></span>
                <strong>${escapeHtml(item.title)}</strong>
                <small>${item.actualSequence.map(stateLabel).join(" → ")}</small>
              </button>`
          )
          .join("")
      : `<div class="case-empty">当前筛选下没有案例。</div>`;

    document.querySelectorAll("[data-case-id]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedCaseId = button.dataset.caseId;
        renderCaseList();
      });
    });
    renderCaseDetail();
  }

  function renderScore(final) {
    return Object.entries(final.score.components)
      .map(([key, value]) => {
        const width = Math.round((value / scoreMaximums[key]) * 100);
        return `
          <div class="score-row">
            <span>${scoreLabels[key]}</span>
            <div><i style="width:${width}%"></i></div>
            <strong>${value}/${scoreMaximums[key]}</strong>
          </div>`;
      })
      .join("");
  }

  function renderGates(final) {
    return final.gates
      .map(
        (gate) => `
          <div class="gate gate-${gate.status}">
            <span>${gate.status === "pass" ? "通过" : gate.status === "pending" ? "待核验" : "未通过"}</span>
            <strong>${escapeHtml(gate.label)}</strong>
          </div>`
      )
      .join("");
  }

  function renderTimeline(item) {
    return item.timeline
      .map(
        (point, index) => `
          <article class="timeline-step">
            <div class="timeline-index">${String(index + 1).padStart(2, "0")}</div>
            <div>
              <header>
                <span>${escapeHtml(point.at)}</span>
                <strong>${escapeHtml(point.label)}</strong>
                <i class="${stateClass(point.state)}">${escapeHtml(stateLabel(point.state))}</i>
              </header>
              <p>${escapeHtml(point.reason)}</p>
              <small>
                ${point.fromState ? `${escapeHtml(stateLabel(point.fromState))} → ` : "首次判断 → "}
                ${escapeHtml(stateLabel(point.state))}
                · ${point.transitionLegal ? "合法迁移" : "非法迁移"}
              </small>
            </div>
          </article>`
      )
      .join("");
  }

  function renderCaseDetail() {
    const item = replay.results.find((result) => result.caseId === selectedCaseId);
    const container = document.getElementById("caseDetail");
    if (!item) {
      container.innerHTML = `<div class="case-empty">请选择一个案例。</div>`;
      return;
    }
    const final = item.final;
    container.innerHTML = `
      <header class="case-title">
        <div>
          <span>${escapeHtml(item.caseType)}</span>
          <h3>${escapeHtml(item.title)}</h3>
          <p>${escapeHtml(item.description)}</p>
        </div>
        <strong class="${item.passed ? "case-pass" : "case-fail"}">${item.passed ? "规则通过" : "需要检查"}</strong>
      </header>

      <div class="decision-grid">
        <div><span>事实成熟度</span><strong>${escapeHtml(final.maturity)}</strong></div>
        <div><span>工作流状态</span><strong>${escapeHtml(stateLabel(final.state))}</strong></div>
        <div><span>建议动作</span><strong>${escapeHtml(final.action)}</strong></div>
        <div><span>交易性</span><strong>${escapeHtml(tradeabilityLabels[final.tradeability] || final.tradeability)}</strong></div>
        <div><span>剩余凸性</span><strong>${escapeHtml(remainingLabels[final.remainingConvexity] || final.remainingConvexity)}</strong></div>
        <div><span>错配分</span><strong>${final.score.totalScore}</strong></div>
      </div>

      <section class="reason-panel">
        <span>最终判断依据</span>
        <strong>${escapeHtml(final.reason)}</strong>
        <p>仓位边界：${escapeHtml(final.positionGuidance)}。这里的目标仓位不是总资产比例。</p>
      </section>

      <div class="analysis-columns">
        <section>
          <header><span>SCORE</span><h4>错配评分</h4></header>
          ${renderScore(final)}
          <div class="deduction-row"><span>风险扣分</span><strong>-${final.score.riskDeduction}</strong></div>
          <p class="method-note">评分只决定研究优先级，不直接决定买卖。</p>
        </section>
        <section>
          <header><span>HARD GATES</span><h4>正式入库硬门槛</h4></header>
          <div class="gate-grid">${renderGates(final)}</div>
        </section>
      </div>

      <section class="convexity-card">
        <header><span>CONVEXITY CASE</span><h4>凸性专项判断</h4></header>
        <dl>
          <div><dt>主凸性来源</dt><dd>${escapeHtml(final.primaryConvexitySource)}</dd></div>
          <div><dt>最大可控亏损</dt><dd>${escapeHtml(final.maximumControllableLoss)}</dd></div>
          <div><dt>非线性上行</dt><dd>${escapeHtml(final.nonlinearUpsidePath)}</dd></div>
          <div><dt>点火条件</dt><dd>${escapeHtml(final.ignitionConditions)}</dd></div>
          <div><dt>赔率衰减</dt><dd>${escapeHtml(final.oddsDecayConditions)}</dd></div>
          <div><dt>失效窗口</dt><dd>${escapeHtml(final.invalidationWindow)}</dd></div>
        </dl>
      </section>

      <section class="timeline">
        <header><span>HISTORY</span><h4>状态变化</h4></header>
        ${renderTimeline(item)}
      </section>`;
  }

  document.getElementById("clearStateFilter").addEventListener("click", () => {
    selectedState = "";
    document.getElementById("clearStateFilter").classList.add("hidden");
    renderStateMachine();
    renderCaseList();
  });

  renderSummary();
  renderStateMachine();
  renderTypeFilter();
  renderCaseList();
})();
