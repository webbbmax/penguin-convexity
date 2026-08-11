(function candidatePoolApp() {
  const state = window.PENGUIN_CONVEXITY_CANDIDATES;
  const opportunityState = window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER;
  const routeState = window.PENGUIN_CONVEXITY_RESEARCH_ROUTES;
  const opportunityByCaseId = new Map(
    (opportunityState?.cases || []).map((item) => [item.caseId, item.opportunityStage]),
  );
  const routeByCaseId = new Map(
    (routeState?.records || []).filter((item) => item.caseId).map((item) => [item.caseId, item]),
  );
  const byId = (id) => document.getElementById(id);
  const apiUrl = (endpoint) => location.pathname.startsWith("/convexity/")
    ? `/api/convexity/${endpoint}`
    : `/api/${endpoint}`;

  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  const dateTime = (value) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const money = (value) => {
    if (value == null) return "--";
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: "USD",
      notation: Math.abs(Number(value)) >= 1000000 ? "compact" : "standard",
      maximumFractionDigits: Math.abs(Number(value)) < 1 ? 6 : 2,
    }).format(Number(value));
  };
  const sourceLabels = {
    coingecko: "CoinGecko 行情",
    dexscreener: "DexScreener 交易池",
    evidence: "证据链接",
    mapping: "资产映射",
    goplus: "GoPlus 合约安全",
    robinhood_blockscout: "Robinhood Chain 浏览器",
    contract_mapping: "代币合约身份",
    dexscreener_profiles: "DexScreener 最新资料",
    dexscreener_boosts: "DexScreener 推广线索",
    robinhood_registry: "Robinhood Chain 代币注册表",
  };
  const statusLabels = {
    success: "成功",
    partial_success: "部分成功",
    failed: "失败",
    skipped: "未映射",
    restricted: "访问受限",
    conflict: "身份冲突",
  };
  const screeningStatusLabels = {
    pass: "完整通过",
    pending: "待核验",
    fail: "被拦截",
  };
  const publicationStatusLabels = {
    not_created: "",
    published: "今日进入机会中心",
    withdrawn: "已撤回",
    draft: "待发布",
    preview: "预览",
  };
  const observeFallback = {
    finalActionLabel: "只观察",
    finalActionReason: "尚未取得最新统一动作，旧任务判断仅保留历史，不生成当前行动结论。",
    blockerLabel: "统一动作待刷新",
  };
  const decisionFor = (item) => (
    item?.caseId ? opportunityByCaseId.get(item.caseId) || observeFallback : observeFallback
  );
  const routeFor = (item) => routeByCaseId.get(item?.caseId) || {
    routeLabel: "潜力项目",
    routeReason: "生命周期分类快照待刷新。",
    routeSourceLabel: "系统待刷新",
    primaryFocus: "同时补齐基础档案与前置信号。",
    nextEvidence: "刷新项目分类",
  };

  if (!state) {
    byId("candidateStatus").textContent = "候选快照读取失败";
    byId("candidateList").innerHTML = '<p class="empty-feedback">请先运行候选导入。</p>';
    return;
  }

  const priorityRank = {
    "极高": 6,
    "高": 5,
    "中": 4,
    "待刷新": 3,
    "低": 2,
    "阻断": 1,
    "转出": 0,
  };
  const poolOrder = {
    ordinary: 0,
    extreme_review: 1,
    embryo: 2,
    decay: 3,
  };
  let activePool = "all";
  let searchTerm = "";
  let activeCaseId = new URLSearchParams(window.location.search).get("case") || "";
  const gateScreening = state.gateScreening;

  byId("candidateStatus").textContent = "任务候选已导入";
  byId("candidateGeneratedAt").textContent = `快照 ${dateTime(state.generatedAt)}`;
  byId("candidateTotal").textContent = state.counts.total;
  byId("ordinaryCount").textContent = state.counts.ordinary;
  byId("extremeReviewCount").textContent = state.counts.extremeReview;
  byId("embryoCount").textContent = state.counts.embryo;
  byId("actionableExtremeCount").textContent = state.counts.actionableExtreme;
  byId("discoveryPolicy").textContent = state.priorityPolicy.discovery;
  byId("decisionPolicy").textContent = state.priorityPolicy.decision;
  byId("quotaPolicy").textContent = state.priorityPolicy.noQuota;
  byId("candidateBoundary").textContent = state.importBoundary;
  byId("discoveryNetworkList").innerHTML = (state.discoveryNetworks || []).map((network) => `
    <a href="${escapeHtml(network.source_url)}" target="_blank" rel="noreferrer"
       class="${network.network_id === "robinhood-mainnet" ? "featured" : ""}">
      <strong>${escapeHtml(network.name)}</strong>
      <span>${escapeHtml(network.chain_type)} · Chain ID ${escapeHtml(network.chain_id)}</span>
      ${network.network_id === "robinhood-mainnet" ? "<small>新增常用链</small>" : ""}
    </a>
  `).join("");

  function presetById(id) {
    return gateScreening.presets.find((preset) => preset.id === id);
  }

  function gateSettingsFromForm() {
    return {
      marketGateEnabled: byId("gateMarketEnabled").checked,
      minimumLiquidityUsd: Number(byId("gateMinLiquidity").value),
      minimumVolume24hUsd: Number(byId("gateMinVolume").value),
      maximumExitSlippagePct: Number(byId("gateMaxSlippage").value),
      minimumMismatchScore: Number(byId("gateMinScore").value),
      allowedMaturities: Array.from(
        document.querySelectorAll('input[name="gateMaturity"]:checked')
      ).map((input) => input.value),
      maximumRiskLevel: byId("gateMaxRisk").value,
      minimumValueCaptureGrade: byId("gateMinValueCapture").value,
      minimumRemainingConvexity: byId("gateMinRemaining").value,
      assetPolicy: byId("gateAssetPolicy").value,
      tradeabilityPolicy: byId("gateTradeabilityPolicy").value,
      identityPolicy: byId("gateIdentityPolicy").value,
      sellPathPolicy: byId("gateSellPathPolicy").value,
      contractRiskPolicy: byId("gateContractRiskPolicy").value,
      requireHardTrace: byId("gateRequireHardTrace").checked,
      requireCompleteConvexity: byId("gateRequireCompleteConvexity").checked,
      unknownDataPolicy: byId("gateUnknownPolicy").value,
    };
  }

  function fillGateForm(settings) {
    byId("gateMarketEnabled").checked = settings.marketGateEnabled;
    byId("gateMinLiquidity").value = settings.minimumLiquidityUsd;
    byId("gateMinVolume").value = settings.minimumVolume24hUsd;
    byId("gateMaxSlippage").value = settings.maximumExitSlippagePct;
    byId("gateMinScore").value = settings.minimumMismatchScore;
    document.querySelectorAll('input[name="gateMaturity"]').forEach((input) => {
      input.checked = settings.allowedMaturities.includes(input.value);
    });
    byId("gateMaxRisk").value = settings.maximumRiskLevel;
    byId("gateMinValueCapture").value = settings.minimumValueCaptureGrade;
    byId("gateMinRemaining").value = settings.minimumRemainingConvexity;
    byId("gateAssetPolicy").value = settings.assetPolicy;
    byId("gateTradeabilityPolicy").value = settings.tradeabilityPolicy;
    byId("gateIdentityPolicy").value = settings.identityPolicy;
    byId("gateSellPathPolicy").value = settings.sellPathPolicy;
    byId("gateContractRiskPolicy").value = settings.contractRiskPolicy;
    byId("gateRequireHardTrace").checked = settings.requireHardTrace;
    byId("gateRequireCompleteConvexity").checked = settings.requireCompleteConvexity;
    byId("gateUnknownPolicy").value = settings.unknownDataPolicy;
    document.querySelector(".gate-market-fieldset").classList.toggle(
      "disabled",
      !settings.marketGateEnabled
    );
  }

  function setGatePresetDescription(presetId) {
    const preset = presetById(presetId);
    byId("gatePresetName").textContent = preset?.name || "自定义方案";
    byId("gatePresetDescription").textContent = preset?.description
      || "当前数值或选项已偏离预设，保存后会作为你的自定义筛选方案保留。";
  }

  function renderGateScreening() {
    const active = gateScreening.active;
    byId("gatePreset").innerHTML = gateScreening.presets.map((preset) => (
      `<option value="${escapeHtml(preset.id)}">${escapeHtml(preset.name)}</option>`
    )).join("") + '<option value="custom">自定义方案</option>';
    byId("gatePreset").value = active.activePresetId;
    byId("gateShowOnlyPassing").checked = active.showOnlyPassing;
    fillGateForm(active.settings);
    setGatePresetDescription(active.activePresetId);
    byId("gateIncludedCount").textContent = gateScreening.summary.included;
    byId("gateExactPassCount").textContent = gateScreening.summary.passed;
    byId("gatePendingCount").textContent = gateScreening.summary.pending;
    byId("gateExcludedCount").textContent = gateScreening.summary.excluded;

    const storedMessage = sessionStorage.getItem("gateScreeningMessage");
    if (storedMessage) {
      byId("gateMessage").textContent = storedMessage;
      byId("gateMessage").classList.remove("hidden");
      sessionStorage.removeItem("gateScreeningMessage");
    }
  }
  renderGateScreening();

  function renderLatestRefresh() {
    const run = state.latestRefresh;
    if (!run) {
      byId("latestCandidateRefresh").innerHTML = `
        <div class="empty-feedback">
          尚未执行候选实时刷新。点击“一键刷新候选”后，这里会显示每个项目、来源、指标变化和失败原因。
        </div>
      `;
      return;
    }
    const refreshedCases = state.cases.filter((item) => (
      item.refresh?.market || item.refresh?.evidence?.length || item.refresh?.contracts?.length
    ));
    const changedProjects = refreshedCases.filter((item) => (
      item.refresh?.market?.changes?.length
      || item.refresh?.evidence?.some((evidence) => evidence.changed)
      || item.refresh?.contracts?.length
    )).length;
    const sourceCards = run.sourceStats.map((item) => `
      <article>
        <div>
          <strong>${escapeHtml(sourceLabels[item.collector_id] || item.collector_id)}</strong>
          <span class="refresh-status status-${escapeHtml(item.status)}">${escapeHtml(statusLabels[item.status] || item.status)}</span>
        </div>
        <p>检查 ${item.collected_count} · 成功 ${item.matched_count} · 跳过 ${item.filtered_count} · 失败 ${item.failed_count}</p>
      </article>
    `).join("");
    const projectRows = refreshedCases.map((item) => {
      const market = item.refresh.market;
      const evidence = item.refresh.evidence || [];
      const accessible = evidence.filter((entry) => entry.status === "success").length;
      const restricted = evidence.filter((entry) => entry.status === "restricted").length;
      const failed = evidence.filter((entry) => entry.status === "failed").length;
      const changes = market?.changes || [];
      const contracts = item.refresh.contracts || [];
      const contract = contracts[0];
      return `
        <details class="refresh-project">
          <summary>
            <span><strong>${escapeHtml(item.projectName)}</strong><small>${escapeHtml(item.symbol || "无映射资产")}</small></span>
            <span class="refresh-project-result">${escapeHtml(
              market ? statusLabels[market.status] || market.status : "仅复核证据"
            )}</span>
          </summary>
          <div class="refresh-project-body">
            <p><strong>市场：</strong>${escapeHtml(
              market?.summary
                || (market?.status === "success"
                  ? `价格 ${money(market.priceUsd)}；24小时成交 ${money(market.volume24hUsd)}`
                  : "本次没有市场结果。")
            )}${market?.marketGrade ? ` · 交易性初筛 ${escapeHtml(market.marketGrade)}` : ""}</p>
            <p><strong>指标变化：</strong>${changes.length
              ? changes.map((change) => `${escapeHtml(change.field)} ${change.changePct == null ? "首次记录" : `${Number(change.changePct).toFixed(2)}%`}`).join(" · ")
              : "未发现可比较变化或首次建立快照"}</p>
            <p><strong>证据链接：</strong>可访问 ${accessible} · 受限 ${restricted} · 失败 ${failed}</p>
            <p><strong>合约与卖出：</strong>${contract
              ? `${escapeHtml(contract.networkName || "身份映射失败")} · ${escapeHtml(
                contract.status === "pass" ? "只读核验通过" : contract.summary || "仍有待核验项"
              )}${contract.recentSells24h == null ? "" : ` · 24小时卖出 ${contract.recentSells24h} 笔`}`
              : "本次未形成合约核验记录"}</p>
            ${market?.sourceUrl ? `<a href="${escapeHtml(market.sourceUrl)}" target="_blank" rel="noreferrer">打开市场来源</a>` : ""}
            ${contract?.explorerUrl ? `<a href="${escapeHtml(contract.explorerUrl)}" target="_blank" rel="noreferrer">打开合约浏览器</a>` : ""}
          </div>
        </details>
      `;
    }).join("");
    byId("latestCandidateRefresh").innerHTML = `
      <div class="refresh-run-head">
        <div>
          <span>最近运行</span>
          <strong>${dateTime(run.startedAt)}</strong>
          <small>${escapeHtml(run.explanation)}</small>
        </div>
        <span class="refresh-status status-${escapeHtml(run.status)}">${escapeHtml(statusLabels[run.status] || run.status)}</span>
      </div>
      <div class="refresh-metrics">
        <div><span>检查记录</span><strong>${run.collectedCount}</strong></div>
        <div><span>成功标准化</span><strong>${run.normalizedCount}</strong></div>
        <div><span>市场匹配</span><strong>${run.matchedCount}</strong></div>
        <div><span>项目有变化</span><strong>${changedProjects}</strong></div>
        <div><span>错误</span><strong>${run.errorCount}</strong></div>
      </div>
      <div class="refresh-source-grid">${sourceCards}</div>
      <details class="refresh-all-projects">
        <summary>查看 ${refreshedCases.length} 个项目的具体更新</summary>
        <div class="refresh-project-list">${projectRows}</div>
      </details>
      ${run.errors.length ? `
        <details class="refresh-errors">
          <summary>查看 ${run.errors.length} 条可重试错误</summary>
          ${run.errors.map((error) => `<p><strong>${escapeHtml(error.task_name)}</strong>：${escapeHtml(error.message)}</p>`).join("")}
        </details>
      ` : ""}
    `;
  }
  renderLatestRefresh();

  const storedMessage = sessionStorage.getItem("candidateRefreshMessage");
  if (storedMessage) {
    byId("refreshMessage").textContent = storedMessage;
    byId("refreshMessage").classList.remove("hidden");
    sessionStorage.removeItem("candidateRefreshMessage");
  }

  Object.entries(state.poolLabels).forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    byId("candidatePoolFilter").appendChild(option);
  });

  function visibleCases() {
    return state.cases
      .filter((item) => (
        !gateScreening.active.showOnlyPassing
        || item.screening?.included
        || item.caseId === activeCaseId
      ))
      .filter((item) => activePool === "all" || item.pool === activePool)
      .filter((item) => {
        if (!searchTerm) return true;
        const haystack = [
          item.projectName,
          item.symbol,
          item.title,
          item.convexitySource,
          item.currentThesis,
        ].join(" ").toLowerCase();
        return haystack.includes(searchTerm);
      })
      .sort((a, b) => (
        Number(b.screening?.included) - Number(a.screening?.included)
        || poolOrder[a.pool] - poolOrder[b.pool]
        || priorityRank[b.decisionPriority] - priorityRank[a.decisionPriority]
        || priorityRank[b.discoveryPriority] - priorityRank[a.discoveryPriority]
        || a.projectName.localeCompare(b.projectName, "zh-CN")
      ));
  }

  function priorityBadge(label, kind) {
    const className = label === "阻断" || label === "转出"
      ? "blocked"
      : label === "极高" || label === "高"
        ? "high"
        : "normal";
    return `<span class="priority-badge ${className}"><small>${kind}</small>${escapeHtml(label)}</span>`;
  }

  function evidenceMarkup(evidence) {
    if (!evidence.length) {
      return '<p class="detail-empty">任务中保留了判断结果，但本次导入没有可直接挂接的外部链接，后续补证时不会把空白伪装成完整。</p>';
    }
    return evidence.map((item) => `
      <article class="candidate-evidence">
        <div>
          <span>${escapeHtml(item.factBoundary === "confirmed_fact" ? "事实材料" : "项目方材料")}</span>
          <small>${escapeHtml(item.stance === "counter" ? "反面证据" : item.stance === "support" ? "支持证据" : "中性材料")}</small>
        </div>
        <p>${escapeHtml(item.summary)}</p>
        <a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开来源</a>
      </article>
    `).join("");
  }

  function contractMarkup(item) {
    const contract = item.assetContract;
    const check = item.tradeabilityCheck;
    if (!contract) {
      return `
        <section class="asset-identity-panel identity-missing">
          <header><div><span>ASSET IDENTITY</span><h4>代币合约与网络</h4></div><strong>待核验</strong></header>
          <p>尚未取得可交叉核验的代币合约。相同名称或符号的代币不得据此视为本项目资产。</p>
        </section>
      `;
    }
    const explorerSuffix = contract.chainType === "Solana" ? "token" : "address";
    const explorerUrl = `${contract.explorerUrl.replace(/\/$/, "")}/${explorerSuffix}/${contract.contractAddress}`;
    const identityLabel = {
      verified: "官方来源已核验",
      market_matched: "市场与链上吻合",
      conflict: "身份冲突",
      rejected: "已拒绝",
      pending: "待核验",
    }[contract.identityStatus] || contract.identityStatus;
    const sellPathLabel = {
      read_only_verified: "只读核验通过",
      blocked: "发现阻断",
      unknown: "待核验",
    }[check?.sellPathStatus || "unknown"];
    const statusLabel = {
      verified: "通过",
      missing: "未发现",
      match: "吻合",
      mismatch: "冲突",
      unverified: "未验证",
      not_applicable: "不适用",
      unknown: "待核验",
    };
    const evidence = check?.evidence || [];
    const riskFlags = check?.riskFlags || [];
    return `
      <section class="asset-identity-panel identity-${escapeHtml(contract.identityStatus)}">
        <header>
          <div><span>ASSET IDENTITY</span><h4>代币合约与卖出路径</h4></div>
          <strong>${escapeHtml(identityLabel)}</strong>
        </header>
        <div class="asset-network-line">
          <div>
            <span>网络</span>
            <strong>${escapeHtml(contract.networkName)}</strong>
            <small>${escapeHtml(contract.environment === "mainnet" ? "主网" : contract.environment)} · Chain ID ${escapeHtml(contract.chainId)} · ${escapeHtml(contract.contractStandard)}</small>
          </div>
          ${contract.discoveryPriority === "common" ? '<mark>常用发现网络</mark>' : ""}
        </div>
        <div class="contract-address-row">
          <span>代币合约</span>
          <code>${escapeHtml(contract.contractAddress)}</code>
          <button type="button" data-copy-contract="${escapeHtml(contract.contractAddress)}">复制</button>
          <a href="${escapeHtml(explorerUrl)}" target="_blank" rel="noreferrer">区块浏览器</a>
        </div>
        <p class="identity-warning">${contract.identityStatus === "verified"
          ? "合约身份已有官方来源交叉核验，仍需结合交易场所和风险记录判断。"
          : "当前合约来自市场映射并已做链上核验，但尚未取得项目官方合约清单的独立交叉确认；同名仿盘风险仍需保留。"}</p>
        <div class="tradeability-check-grid">
          <div><span>链上合约</span><strong>${escapeHtml(statusLabel[check?.contractExistsStatus] || "待核验")}</strong></div>
          <div><span>源码状态</span><strong>${escapeHtml(statusLabel[check?.sourceCodeStatus] || "待核验")}</strong></div>
          <div><span>代币资料</span><strong>${escapeHtml(statusLabel[check?.metadataMatchStatus] || "待核验")}</strong></div>
          <div><span>交易池合约</span><strong>${escapeHtml(statusLabel[check?.pairMatchStatus] || "待核验")}</strong></div>
          <div><span>24小时买 / 卖</span><strong>${check?.recentBuys24h == null ? "--" : check.recentBuys24h} / ${check?.recentSells24h == null ? "--" : check.recentSells24h}</strong></div>
          <div><span>${money(check?.exitNotionalUsd)} 退出滑点</span><strong>${check?.estimatedExitSlippagePct == null ? "--" : `${Number(check.estimatedExitSlippagePct).toFixed(2)}%`}</strong></div>
          <div class="sell-path"><span>卖出路径</span><strong>${escapeHtml(sellPathLabel)}</strong></div>
        </div>
        ${riskFlags.length ? `
          <div class="contract-risk-flags">
            ${riskFlags.map((flag) => `<span class="risk-${escapeHtml(flag.level)}">${escapeHtml(flag.detail)}</span>`).join("")}
          </div>
        ` : ""}
        ${evidence.length ? `
          <details class="contract-evidence">
            <summary>查看 ${evidence.length} 条核验证据</summary>
            ${evidence.map((entry) => `
              <p>
                <strong>${escapeHtml(entry.label)}</strong>
                <span>${escapeHtml(entry.detail)}</span>
                ${entry.url ? `<a href="${escapeHtml(entry.url)}" target="_blank" rel="noreferrer">来源</a>` : ""}
              </p>
            `).join("")}
          </details>
        ` : ""}
        <footer>
          <span>${escapeHtml(contract.identitySource)} · ${check ? dateTime(check.checkedAt) : "尚未执行可交易性核验"}</span>
          <p>${escapeHtml(check?.verificationScope || contract.verificationMethod)}</p>
        </footer>
      </section>
    `;
  }

  function marketMarkup(item) {
    const market = item.latestMarket;
    const refresh = item.refresh || { market: null, evidence: [] };
    if (!market && !refresh.market) {
      return `
        <section class="candidate-live-market market-unavailable">
          <strong>尚无实时市场快照</strong>
          <p>${escapeHtml(refresh.market?.summary || "点击页面上方“一键刷新候选”建立第一条市场记录。")}</p>
        </section>
      `;
    }
    const marketChanges = refresh.market?.changes || [];
    return `
      <section class="candidate-live-market">
        <header>
          <div><span>LIVE MARKET</span><h4>最近市场快照</h4></div>
          <p>${market ? `${escapeHtml(market.sourceName)} · ${dateTime(market.observedAt)}` : "本次没有形成市场快照"}</p>
        </header>
        <div class="live-market-grid">
          <div><span>价格</span><strong>${money(market?.priceUsd)}</strong></div>
          <div><span>流动性</span><strong>${money(market?.liquidityUsd)}</strong></div>
          <div><span>24小时成交</span><strong>${money(market?.volume24hUsd)}</strong></div>
          <div><span>市值</span><strong>${money(market?.marketCapUsd)}</strong></div>
          <div><span>退出滑点</span><strong>${market?.estimatedExitSlippagePct == null ? "--" : `${Number(market.estimatedExitSlippagePct).toFixed(2)}%`}</strong></div>
        </div>
        <p class="market-definition">${escapeHtml(market?.definitionNote || refresh.market?.summary || "")}</p>
        <div class="market-change-line">
          <strong>本次变化</strong>
          <span>${marketChanges.length
            ? marketChanges.map((change) => `${escapeHtml(change.field)} ${change.changePct == null ? "首次记录" : `${Number(change.changePct).toFixed(2)}%`}`).join(" · ")
            : "未发现可比较变化或首次建立快照"}</span>
        </div>
      </section>
    `;
  }

  function refreshEvidenceMarkup(item) {
    const evidence = item.refresh?.evidence || [];
    if (!evidence.length) return "";
    return `
      <section class="refresh-evidence-panel">
        <header><span>SOURCE CHECK</span><h4>本次证据链接复核</h4></header>
        <div>
          ${evidence.map((entry) => `
            <article>
              <span class="refresh-status status-${escapeHtml(entry.status)}">${escapeHtml(statusLabels[entry.status] || entry.status)}</span>
              <p>${escapeHtml(entry.summary)}</p>
              <small>HTTP ${escapeHtml(entry.httpStatus == null ? "--" : entry.httpStatus)}${entry.changed ? " · 页面指纹变化" : ""}</small>
              <a href="${escapeHtml(entry.sourceUrl)}" target="_blank" rel="noreferrer">打开来源</a>
            </article>
          `).join("")}
        </div>
      </section>
    `;
  }

  function screeningMarkup(item) {
    const result = item.screening;
    if (!result) return "";
    const headline = result.included
      ? (result.status === "pass" ? "进入结果，全部门槛通过" : "进入结果，但仍有数据待核验")
      : "未进入当前筛选结果";
    return `
      <section class="candidate-screening-result screening-${escapeHtml(result.status)}">
        <header>
          <div>
            <span>CURRENT SCREEN</span>
            <h4>${escapeHtml(headline)}</h4>
          </div>
          <strong>${result.passedCount}/${result.totalCount} 项通过</strong>
        </header>
        <div class="screening-gate-grid">
          ${result.gates.map((gate) => `
            <article class="gate-${escapeHtml(gate.status)}">
              <span>${escapeHtml(gate.label)}</span>
              <strong>${escapeHtml(
                gate.status === "pass" ? "通过" : gate.status === "pending" ? "待核验" : "拦截"
              )}</strong>
              <p>${escapeHtml(gate.detail)}</p>
            </article>
          `).join("")}
        </div>
      </section>
    `;
  }

  function renderDetail(item) {
    if (!item) {
      byId("candidateDetail").innerHTML = '<p class="empty-feedback">当前筛选没有项目。</p>';
      return;
    }
    const normalized = item.sourceAction !== item.normalizedAction;
    const publicationLabel = publicationStatusLabels[item.publicationStatus] || "";
    const decision = decisionFor(item);
    const route = routeFor(item);
    byId("candidateDetail").innerHTML = `
      <header class="candidate-detail-head">
        <div>
          <span>${escapeHtml(item.poolLabel)} · ${escapeHtml(item.stateLabel)}</span>
          <h3>${escapeHtml(item.projectName)}${item.symbol ? ` <small>${escapeHtml(item.symbol)}</small>` : ""}</h3>
          <p>${escapeHtml(item.title)}</p>
        </div>
        <div class="candidate-priorities">
          ${publicationLabel ? `<span class="candidate-publication-tag status-${escapeHtml(item.publicationStatus)}">${escapeHtml(publicationLabel)}</span>` : ""}
          ${priorityBadge(item.discoveryPriority, "搜索")}
          ${priorityBadge(item.decisionPriority, "行动")}
        </div>
      </header>

      <div class="candidate-facts">
        <div><span>成熟度</span><strong>${escapeHtml(item.maturity)}</strong></div>
        <div><span>风险</span><strong>${escapeHtml(item.riskLevel)}</strong></div>
        <div><span>剩余凸性</span><strong>${escapeHtml(item.remainingConvexity)}</strong></div>
        <div><span>交易性</span><strong>${escapeHtml(item.liquidityGrade)}</strong></div>
        <div><span>错配分</span><strong>${item.mismatchScore == null ? "--" : escapeHtml(item.mismatchScore)}</strong></div>
      </div>

      <section class="current-action-contract">
        <span>当前唯一动作</span>
        <strong>${escapeHtml(decision.finalActionLabel)}</strong>
        <p>${escapeHtml(decision.finalActionReason)}</p>
        ${decision.blockerLabel ? `<small>阻断状态：${escapeHtml(decision.blockerLabel)}</small>` : ""}
      </section>

      <section class="screening-research-route">
        <span>当前项目类别 · ${escapeHtml(route.routeSourceLabel)}</span>
        <strong>${escapeHtml(route.routeLabel)}</strong>
        <p>${escapeHtml(route.routeReason)}</p>
        <p><b>本类先查：</b>${escapeHtml(route.primaryFocus)}</p>
        <small>下一项补证：${escapeHtml(route.nextEvidence)}</small>
      </section>

      <section class="action-compare ${normalized ? "has-conflict" : ""}">
        <div>
          <span>任务原始判断（历史）</span>
          <strong>${escapeHtml(item.sourceAction)}</strong>
        </div>
        <div>
          <span>候选归一结果（历史）</span>
          <strong>${escapeHtml(item.normalizedAction)}</strong>
        </div>
        ${item.normalizationNote ? `<p><strong>归一说明：</strong>${escapeHtml(item.normalizationNote)}</p>` : ""}
      </section>

      ${screeningMarkup(item)}

      ${contractMarkup(item)}

      ${marketMarkup(item)}

      <section class="candidate-thesis">
        <span>主凸性来源</span>
        <h4>${escapeHtml(item.convexitySource)}</h4>
        <p>${escapeHtml(item.currentThesis)}</p>
      </section>

      <div class="candidate-logic-grid">
        <section><span>最大可控亏损</span><p>${escapeHtml(item.maximumControllableLoss)}</p></section>
        <section><span>非线性上行</span><p>${escapeHtml(item.nonlinearUpsidePath)}</p></section>
        <section><span>点火条件</span><p>${escapeHtml(item.ignitionConditions)}</p></section>
        <section><span>赔率衰减</span><p>${escapeHtml(item.oddsDecayConditions)}</p></section>
        <section class="wide danger"><span>失效条件</span><p>${escapeHtml(item.invalidation)}</p><small>${escapeHtml(item.invalidationWindow)}</small></section>
      </div>

      <section class="candidate-evidence-panel">
        <header>
          <div><span>EVIDENCE</span><h4>证据与来源</h4></div>
          <p>${escapeHtml(item.sourceReference)} · 截点 ${escapeHtml(item.sourceSnapshotAt)}</p>
        </header>
        <div class="candidate-evidence-grid">${evidenceMarkup(item.evidence)}</div>
      </section>
      ${refreshEvidenceMarkup(item)}
    `;
    byId("candidateDetail").querySelectorAll("[data-copy-contract]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(button.dataset.copyContract);
          button.textContent = "已复制";
        } catch (_error) {
          button.textContent = "复制失败";
        }
      });
    });
  }

  function render() {
    const cases = visibleCases();
    if (!cases.some((item) => item.caseId === activeCaseId)) {
      activeCaseId = cases[0]?.caseId || "";
    }
    byId("candidateVisibleCount").textContent = cases.length;
    byId("candidateList").innerHTML = cases.length
      ? cases.map((item) => `
        <button type="button" data-case="${escapeHtml(item.caseId)}" class="${item.caseId === activeCaseId ? "active" : ""}">
          <span class="candidate-list-top">
            <strong>${escapeHtml(item.projectName)}</strong>
            <small>${escapeHtml(item.maturity)}</small>
          </span>
          <span class="candidate-list-meta">${escapeHtml(item.poolLabel)} · ${escapeHtml(item.stateLabel)}</span>
          <span class="candidate-list-priority">搜索 ${escapeHtml(item.discoveryPriority)} · 行动 ${escapeHtml(item.decisionPriority)}</span>
          ${publicationStatusLabels[item.publicationStatus]
            ? `<span class="candidate-publication-tag status-${escapeHtml(item.publicationStatus)}">${escapeHtml(publicationStatusLabels[item.publicationStatus])}</span>`
            : ""}
          <span class="candidate-screening-tag screening-${escapeHtml(item.screening?.status || "pending")}">${escapeHtml(
            screeningStatusLabels[item.screening?.status] || "待核验"
          )} · ${item.screening?.passedCount || 0}/${item.screening?.totalCount || 0}</span>
        </button>
      `).join("")
      : '<p class="empty-feedback">当前筛选没有项目。</p>';
    renderDetail(cases.find((item) => item.caseId === activeCaseId));
    byId("candidateList").querySelectorAll("[data-case]").forEach((button) => {
      button.addEventListener("click", () => {
        activeCaseId = button.dataset.case;
        render();
      });
    });
  }

  byId("candidatePoolFilter").addEventListener("change", (event) => {
    activePool = event.target.value;
    render();
  });
  byId("candidateSearch").addEventListener("input", (event) => {
    searchTerm = event.target.value.trim().toLowerCase();
    render();
  });
  byId("gatePreset").addEventListener("change", (event) => {
    const preset = presetById(event.target.value);
    if (preset) fillGateForm(preset.settings);
    setGatePresetDescription(event.target.value);
  });
  byId("gateMarketEnabled").addEventListener("change", (event) => {
    document.querySelector(".gate-market-fieldset").classList.toggle(
      "disabled",
      !event.target.checked
    );
  });
  byId("gateScreeningForm").addEventListener("input", () => {
    byId("gatePreset").value = "custom";
    setGatePresetDescription("custom");
  });
  byId("gateShowOnlyPassing").addEventListener("change", () => {
    byId("gatePreset").value = "custom";
    setGatePresetDescription("custom");
  });
  byId("gateReset").addEventListener("click", () => {
    const preset = presetById(gateScreening.defaultPresetId);
    byId("gatePreset").value = preset.id;
    byId("gateShowOnlyPassing").checked = true;
    fillGateForm(preset.settings);
    setGatePresetDescription(preset.id);
  });
  byId("gateApply").addEventListener("click", async () => {
    const button = byId("gateApply");
    const message = byId("gateMessage");
    const settings = gateSettingsFromForm();
    if (!settings.allowedMaturities.length) {
      message.textContent = "保存失败：至少选择一个成熟度。";
      message.classList.remove("hidden");
      message.classList.add("error");
      return;
    }
    button.disabled = true;
    button.textContent = "正在重新筛选…";
    message.textContent = "正在保存门槛并重新计算全部候选。";
    message.classList.remove("hidden", "error");
    try {
      const response = await fetch(apiUrl("gate-screening"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          activePresetId: byId("gatePreset").value,
          showOnlyPassing: byId("gateShowOnlyPassing").checked,
          settings,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "保存失败");
      sessionStorage.setItem(
        "gateScreeningMessage",
        `筛选完成：${payload.summary.included} 个进入结果，${payload.summary.excluded} 个被排除。`
      );
      window.location.reload();
    } catch (error) {
      message.textContent = `保存失败：${error.message}`;
      message.classList.add("error");
      button.disabled = false;
      button.textContent = "重试保存并筛选";
    }
  });
  byId("refreshCandidates").addEventListener("click", async () => {
    const button = byId("refreshCandidates");
    const message = byId("refreshMessage");
    button.disabled = true;
    button.textContent = "正在刷新…";
    message.textContent = "正在检查 20 个候选的市场数据和证据链接，请保持本页打开。";
    message.classList.remove("hidden", "error");
    try {
      const response = await fetch(apiUrl("refresh-candidates"), { method: "POST" });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "刷新失败");
      }
      sessionStorage.setItem(
        "candidateRefreshMessage",
        `刷新完成：${payload.explanation}`
      );
      window.location.reload();
    } catch (error) {
      message.textContent = `刷新失败：${error.message}`;
      message.classList.add("error");
      button.disabled = false;
      button.textContent = "重试候选刷新";
    }
  });
  render();
})();
