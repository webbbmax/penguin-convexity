(function modelAcceptancePage() {
  const state = window.PENGUIN_CONVEXITY_MODEL_ACCEPTANCE;
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
      ? String(value || "--")
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const actionLabels = {
    ordinary: "普通建仓",
    extreme: "极限试仓",
    observe: "只观察",
    reflexive: "反身性管理",
    reject: "排除",
  };

  if (!state?.blind?.cases) {
    byId("acceptanceVerdict").textContent = "验收数据暂不可用";
    byId("acceptanceCaseList").innerHTML = '<div class="acceptance-empty">请重新生成模型验收报告。</div>';
    return;
  }

  const blind = state.blind.summary;
  const outcome = state.investmentValidation;
  byId("acceptanceVerdict").textContent = state.verdictLabel;
  byId("acceptanceVerdictNote").textContent = state.verdictExplanation;
  byId("acceptanceGold").textContent = `${state.gold.accuracyPct}%`;
  byId("acceptanceGoldMeta").textContent = `${state.gold.matched}/${state.gold.total} 个案例一致`;
  byId("acceptanceBlind").textContent = `${blind.exactAccuracyPct}%`;
  byId("acceptanceBlindMeta").textContent = `${blind.exactMatched}/${blind.total} 个情景一致`;
  byId("acceptanceRecall").textContent = `${blind.actionableRecallPct}%`;
  byId("acceptancePrecision").textContent = `${blind.actionablePrecisionPct}%`;
  byId("acceptanceEscapes").textContent = blind.safetyEscapes;
  byId("acceptanceOutcomes").textContent = outcome.availableOutcomeCases;
  byId("acceptanceBoundary").textContent = state.boundary;
  byId("acceptanceGeneratedAt").textContent = `验收时间 ${dateTime(state.generatedAt)} · 规则版本 ${state.ruleVersion}`;

  byId("acceptanceCriteria").innerHTML = state.criteria.map((criterion) => `
    <article class="${criterion.passed ? "passed" : "failed"}">
      <span>${criterion.passed ? "通过" : "未通过"}</span>
      <h3>${escapeHtml(criterion.label)}</h3>
      <strong>${escapeHtml(criterion.actual)}${escapeHtml(criterion.unit)} <small>/ 门槛 ${escapeHtml(criterion.target)}${escapeHtml(criterion.unit)}</small></strong>
      <p>${escapeHtml(criterion.boundary)}</p>
    </article>
  `).join("");

  const before = state.remediation.beforeFix;
  byId("acceptanceBeforeFix").textContent =
    `修复前动作一致性 ${before.exactMatched}/${before.total}，行动精确率 ${before.actionablePrecisionPct}%，安全型误报 ${before.safetyEscapes} 个。`;
  byId("acceptanceFindings").innerHTML = state.remediation.findings.map((finding, index) => `
    <article>
      <span>0${index + 1} · 阻断级缺陷</span>
      <h3>${escapeHtml(finding.problem)}</h3>
      <p><strong>修复：</strong>${escapeHtml(finding.remediation)}</p>
      <small>案例 ${escapeHtml(finding.caseId)}</small>
    </article>
  `).join("");

  byId("investmentValidationTitle").textContent = outcome.label;
  byId("investmentValidationProgress").textContent =
    `${outcome.availableOutcomeCases} / ${outcome.requiredOutcomeCases}`;
  byId("investmentValidationReason").textContent = outcome.reason;
  byId("investmentValidationNext").textContent = outcome.nextEvidence;

  function layerRows(item) {
    return item.layers.map((layer, index) => `
      <article class="${layer.status}">
        <span>第${index + 1}层 · ${escapeHtml(layer.label.replace(/^第.层：/, ""))}</span>
        <strong>${layer.status === "pass" ? "通过" : layer.status === "pending" ? "待核验" : "未通过"}</strong>
        <p>${escapeHtml(
          layer.failedReasons?.[0]
          || layer.pendingReasons?.[0]
          || layer.checks?.[0]?.detail
          || "本层检查通过"
        )}</p>
      </article>
    `).join("");
  }

  function caseCard(item) {
    const matched = item.actionMatched && item.layerMatched;
    return `
      <article class="acceptance-case-card ${matched ? "matched" : "mismatched"}">
        <header>
          <div>
            <span>${escapeHtml(item.cohort)} · ${escapeHtml(item.maturity)} · ${escapeHtml(item.risk)}</span>
            <h3>${escapeHtml(item.project)} <small>${escapeHtml(item.asset)}</small></h3>
          </div>
          <strong>${matched ? "验收一致" : "需要修正"}</strong>
        </header>
        <div class="acceptance-action-compare">
          <div><span>人工预期</span><strong>${escapeHtml(item.expectedAction)}</strong><small>首先停在第${item.expectedStoppedLayer}层</small></div>
          <div><span>模型实际</span><strong>${escapeHtml(item.actionLabel)}</strong><small>首先停在第${item.stoppedLayer}层</small></div>
        </div>
        <p class="acceptance-rationale"><strong>预期依据：</strong>${escapeHtml(item.rationale)}</p>
        <p class="acceptance-model-reason"><strong>模型解释：</strong>${escapeHtml(item.actionReason)}</p>
        <details>
          <summary>查看四层判断</summary>
          <div class="acceptance-layer-grid">${layerRows(item)}</div>
        </details>
      </article>`;
  }

  function renderCases() {
    const status = byId("acceptanceStatusFilter").value;
    const action = byId("acceptanceActionFilter").value;
    const search = byId("acceptanceSearch").value.trim().toLowerCase();
    const visible = state.blind.cases.filter((item) => {
      const matched = item.actionMatched && item.layerMatched;
      if (status === "matched" && !matched) return false;
      if (status === "mismatched" && matched) return false;
      if (action !== "all" && item.expectedCategory !== action) return false;
      if (!search) return true;
      return [
        item.project,
        item.asset,
        item.rationale,
        item.actionReason,
        item.primaryConvexity,
      ].some((value) => String(value || "").toLowerCase().includes(search));
    });
    byId("acceptanceVisibleCount").textContent = visible.length;
    byId("acceptanceCaseList").innerHTML = visible.length
      ? visible.map(caseCard).join("")
      : '<div class="acceptance-empty"><strong>当前筛选没有案例</strong><p>可以清除一致性、预期动作或搜索条件。</p></div>';
  }

  byId("acceptanceStatusFilter").addEventListener("change", renderCases);
  byId("acceptanceActionFilter").addEventListener("change", renderCases);
  byId("acceptanceSearch").addEventListener("input", renderCases);
  renderCases();
}());
