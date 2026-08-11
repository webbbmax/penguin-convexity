(function networkDiscoveryApp() {
  const state = window.PENGUIN_NETWORK_DISCOVERIES;
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
  const queueLabels = {
    preflight_pass: "技术预检通过",
    identity_pending: "身份待核验",
    existing_asset: "已在候选库",
    rejected: "预检阻断",
    promoted: "已升格",
  };
  const checkLabels = {
    verified: "通过",
    missing: "未发现",
    match: "吻合",
    mismatch: "冲突",
    read_only_verified: "只读核验通过",
    blocked: "阻断",
    pass: "通过",
    pending: "待核验",
    fail: "失败",
    not_checked: "本轮未检查",
    unknown: "待核验",
  };
  const identityLabels = {
    verified: "官网合约确认",
    corroborated: "第三方登记吻合",
    pending: "待补证",
    conflict: "身份冲突",
    rejected: "范围排除",
    confirmed: "官网正文确认",
    registry_matched: "第三方合约吻合",
    not_found: "未发现",
    accessible: "可访问",
    restricted: "访问受限",
    failed: "访问失败",
    missing: "缺少官网",
    shadow_promoted: "已升格影子库",
    existing_project: "已有项目",
    claimed: "仅有用途描述",
    not_applicable: "不适用",
  };

  if (!state) {
    byId("discoveryStatus").textContent = "发现快照读取失败";
    byId("discoveryList").innerHTML = '<p class="empty-feedback">请先执行一次候选刷新。</p>';
    return;
  }

  let activeNetwork = "all";
  let activeStatus = "all";
  let searchTerm = "";
  let activeDiscoveryId = "";

  byId("discoveryStatus").textContent = "自动发现队列已载入";
  byId("discoveryGeneratedAt").textContent = `快照 ${dateTime(state.generatedAt)}`;
  byId("discoveryTotal").textContent = state.counts.total;
  byId("discoveryPassed").textContent = state.counts.preflightPass;
  byId("discoveryPending").textContent = state.counts.identityPending;
  byId("discoveryPromoted").textContent = state.counts.promoted;
  byId("discoveryExisting").textContent = state.counts.existingAssets;
  byId("discoveryRobinhood").textContent = state.counts.robinhood;
  byId("discoveryBoundary").textContent = state.boundary;

  const networks = Array.from(
    new Map(state.records.map((item) => [item.networkId, item.networkName])).entries()
  ).sort((a, b) => a[1].localeCompare(b[1], "zh-CN"));
  byId("discoveryNetworkFilter").innerHTML += networks.map(([id, name]) => (
    `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`
  )).join("");

  const storedMessage = sessionStorage.getItem("networkDiscoveryMessage");
  if (storedMessage) {
    byId("discoveryMessage").textContent = storedMessage;
    byId("discoveryMessage").classList.remove("hidden");
    sessionStorage.removeItem("networkDiscoveryMessage");
  }

  function visibleRecords() {
    return state.records.filter((item) => {
      if (activeNetwork !== "all" && item.networkId !== activeNetwork) return false;
      if (activeStatus !== "all" && item.queueStatus !== activeStatus) return false;
      if (!searchTerm) return true;
      return [
        item.tokenName,
        item.symbol,
        item.contractAddress,
        item.networkName,
      ].join(" ").toLowerCase().includes(searchTerm);
    });
  }

  function sourceMarkup(item) {
    return item.sourceUrls.map((url, index) => `
      <a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">
        来源 ${index + 1} · ${escapeHtml(item.discoveryKinds[index] || item.discoveryKinds[0] || "发现记录")}
      </a>
    `).join("");
  }

  function evidenceMarkup(item) {
    if (!item.evidence.length) {
      return '<p class="detail-empty">本轮尚未形成额外核验证据。</p>';
    }
    return item.evidence.map((entry) => `
      <article>
        <strong>${escapeHtml(entry.label || (entry.type === "source_boundary" ? "来源边界" : "核验证据"))}</strong>
        <p>${escapeHtml(entry.detail || entry.summary || "")}</p>
        ${entry.url ? `<a href="${escapeHtml(entry.url)}" target="_blank" rel="noreferrer">打开来源</a>` : ""}
      </article>
    `).join("");
  }

  function identityMarkup(item) {
    const review = item.identityReview;
    if (!review) {
      return `
        <section class="discovery-identity-review identity-review-pending">
          <header><div><span>IDENTITY REVIEW</span><h4>项目身份与升格</h4></div><strong>尚未复核</strong></header>
          <p>本条尚未取得独立资产登记和官网交叉证据，因此不能归属到具体项目。</p>
        </section>
      `;
    }
    const identityLinks = [
      review.websiteUrl ? `<a href="${escapeHtml(review.websiteUrl)}" target="_blank" rel="noreferrer">项目官网</a>` : "",
      review.coingeckoId ? `<a href="https://www.coingecko.com/en/coins/${escapeHtml(review.coingeckoId)}" target="_blank" rel="noreferrer">CoinGecko 登记</a>` : "",
      ...review.socialUrls.map((url, index) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">社交 ${index + 1}</a>`),
      ...review.repoUrls.map((url, index) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">代码库 ${index + 1}</a>`),
    ].filter(Boolean).join("");
    const evidence = review.evidence.length
      ? review.evidence.map((entry) => `
          <article>
            <strong>${escapeHtml(entry.type === "independent_registry" ? "独立资产登记" : "官网核对")}</strong>
            <p>${escapeHtml(entry.summary)}</p>
            ${entry.url ? `<a href="${escapeHtml(entry.url)}" target="_blank" rel="noreferrer">打开证据</a>` : ""}
          </article>
        `).join("")
      : '<p class="detail-empty">本次没有形成可升格证据。</p>';
    return `
      <section class="discovery-identity-review identity-review-${escapeHtml(review.resolutionStatus)}">
        <header>
          <div><span>IDENTITY REVIEW</span><h4>项目身份与升格</h4></div>
          <strong>${escapeHtml(identityLabels[review.resolutionStatus] || review.resolutionStatus)}</strong>
        </header>
        <p class="identity-review-reason">${escapeHtml(review.reason)}</p>
        <div class="identity-review-grid">
          <div><span>标准项目名</span><strong>${escapeHtml(review.canonicalName || "--")}</strong></div>
          <div><span>身份置信度</span><strong>${escapeHtml(review.confidence)}</strong></div>
          <div><span>官网状态</span><strong>${escapeHtml(identityLabels[review.websiteStatus] || review.websiteStatus)}</strong></div>
          <div><span>合约确认</span><strong>${escapeHtml(identityLabels[review.officialContractStatus] || review.officialContractStatus)}</strong></div>
          <div><span>价值捕获</span><strong>${escapeHtml(identityLabels[review.valueCaptureStatus] || review.valueCaptureStatus)}</strong></div>
          <div><span>升格状态</span><strong>${escapeHtml(identityLabels[review.promotionStatus] || review.promotionStatus)}</strong></div>
        </div>
        <div class="discovery-source-links">${identityLinks || "<span>没有可访问的项目链接</span>"}</div>
        <div class="discovery-evidence-grid">${evidence}</div>
        ${review.promotedCaseId ? `<a class="identity-case-link" href="candidate-pool.html?case=${escapeHtml(review.promotedCaseId)}">打开影子研究项目</a>` : ""}
      </section>
    `;
  }

  function renderDetail(item) {
    if (!item) {
      byId("discoveryDetail").innerHTML = '<p class="empty-feedback">当前筛选没有发现记录。</p>';
      return;
    }
    byId("discoveryDetail").innerHTML = `
      <header class="candidate-detail-head discovery-detail-head">
        <div>
          <span>${escapeHtml(item.networkName)} · ${escapeHtml(queueLabels[item.queueStatus] || item.queueStatus)}</span>
          <h3>${escapeHtml(item.tokenName || "未命名代币")} ${item.symbol ? `<small>${escapeHtml(item.symbol)}</small>` : ""}</h3>
          <p>${escapeHtml(item.statusReason)}</p>
        </div>
        <div class="discovery-score">
          <span>发现排序分</span>
          <strong>${item.discoveryScore}</strong>
          <small>不是投资评分</small>
        </div>
      </header>

      <div class="discovery-identity-strip">
        <div><span>网络</span><strong>${escapeHtml(item.networkName)}</strong><small>主网 · Chain ID ${escapeHtml(item.chainId)}</small></div>
        <div><span>合约标准</span><strong>${escapeHtml(item.contractStandard)}</strong><small>${escapeHtml(item.chainType)}</small></div>
        <div><span>来源冲突风险</span><strong>${escapeHtml(item.sourceConflictRisk)}</strong><small>推广来源不作证明</small></div>
        <div><span>首次 / 最近发现</span><strong>${dateTime(item.firstSeenAt)}</strong><small>${dateTime(item.lastSeenAt)}</small></div>
      </div>

      <div class="contract-address-row discovery-contract-row">
        <span>代币合约</span>
        <code>${escapeHtml(item.contractAddress)}</code>
        <button type="button" data-copy-contract="${escapeHtml(item.contractAddress)}">复制</button>
        <a href="${escapeHtml(item.explorerUrl)}" target="_blank" rel="noreferrer">区块浏览器</a>
      </div>

      ${identityMarkup(item)}

      <section class="candidate-live-market discovery-market">
        <header><div><span>MARKET PREFLIGHT</span><h4>市场与退出预检</h4></div><p>${escapeHtml(queueLabels[item.queueStatus] || item.queueStatus)}</p></header>
        <div class="live-market-grid">
          <div><span>价格</span><strong>${money(item.priceUsd)}</strong></div>
          <div><span>流动性</span><strong>${money(item.liquidityUsd)}</strong></div>
          <div><span>24小时成交</span><strong>${money(item.volume24hUsd)}</strong></div>
          <div><span>24小时买 / 卖</span><strong>${item.recentBuys24h == null ? "--" : item.recentBuys24h} / ${item.recentSells24h == null ? "--" : item.recentSells24h}</strong></div>
          <div><span>${money(item.exitNotionalUsd)} 退出滑点</span><strong>${item.estimatedExitSlippagePct == null ? "--" : `${Number(item.estimatedExitSlippagePct).toFixed(2)}%`}</strong></div>
        </div>
      </section>

      <div class="tradeability-check-grid discovery-check-grid">
        <div><span>技术预检</span><strong>${escapeHtml(checkLabels[item.preflightStatus] || item.preflightStatus)}</strong></div>
        <div><span>链上合约</span><strong>${escapeHtml(checkLabels[item.contractExistsStatus] || item.contractExistsStatus)}</strong></div>
        <div><span>代币资料</span><strong>${escapeHtml(checkLabels[item.metadataMatchStatus] || item.metadataMatchStatus)}</strong></div>
        <div><span>交易池合约</span><strong>${escapeHtml(checkLabels[item.pairMatchStatus] || item.pairMatchStatus)}</strong></div>
        <div><span>卖出路径</span><strong>${escapeHtml(checkLabels[item.sellPathStatus] || item.sellPathStatus)}</strong></div>
        <div><span>合约风险</span><strong>${escapeHtml(item.contractRisk)}</strong></div>
      </div>

      <section class="discovery-source-panel">
        <header>
          <div><span>SOURCES & BOUNDARIES</span><h4>发现来源与证据边界</h4></div>
          <p>${escapeHtml(item.discoveryKinds.join(" · "))}</p>
        </header>
        <div class="discovery-source-links">${sourceMarkup(item)}</div>
        <div class="discovery-evidence-grid">${evidenceMarkup(item)}</div>
      </section>
    `;
    byId("discoveryDetail").querySelectorAll("[data-copy-contract]").forEach((button) => {
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
    const records = visibleRecords();
    if (!records.some((item) => item.discoveryId === activeDiscoveryId)) {
      activeDiscoveryId = records[0]?.discoveryId || "";
    }
    byId("discoveryVisibleCount").textContent = records.length;
    byId("discoveryList").innerHTML = records.length
      ? records.map((item) => `
        <button type="button" data-discovery="${escapeHtml(item.discoveryId)}" class="${item.discoveryId === activeDiscoveryId ? "active" : ""}">
          <span class="candidate-list-top">
            <strong>${escapeHtml(item.tokenName || item.symbol || "未命名合约")}</strong>
            <small>${escapeHtml(item.symbol || "--")}</small>
          </span>
          <span class="candidate-list-meta">${escapeHtml(item.networkName)} · ${escapeHtml(queueLabels[item.queueStatus] || item.queueStatus)}</span>
          <span class="candidate-list-priority">发现分 ${item.discoveryScore} · 流动性 ${money(item.liquidityUsd)}</span>
          <span class="candidate-screening-tag screening-${["preflight_pass", "promoted", "existing_asset"].includes(item.queueStatus) ? "pass" : item.queueStatus === "rejected" ? "fail" : "pending"}">${escapeHtml(checkLabels[item.preflightStatus] || item.preflightStatus)}</span>
        </button>
      `).join("")
      : '<p class="empty-feedback">当前筛选没有发现记录。</p>';
    byId("discoveryList").querySelectorAll("[data-discovery]").forEach((button) => {
      button.addEventListener("click", () => {
        activeDiscoveryId = button.dataset.discovery;
        render();
      });
    });
    renderDetail(records.find((item) => item.discoveryId === activeDiscoveryId));
  }

  byId("discoveryNetworkFilter").addEventListener("change", (event) => {
    activeNetwork = event.target.value;
    render();
  });
  byId("discoveryStatusFilter").addEventListener("change", (event) => {
    activeStatus = event.target.value;
    render();
  });
  byId("discoverySearch").addEventListener("input", (event) => {
    searchTerm = event.target.value.trim().toLowerCase();
    render();
  });
  byId("refreshDiscoveries").addEventListener("click", async () => {
    const button = byId("refreshDiscoveries");
    const message = byId("discoveryMessage");
    button.disabled = true;
    button.textContent = "正在发现与核验…";
    message.textContent = "正在更新候选、常用链发现、合约、卖出路径和项目身份，请保持本页打开。";
    message.classList.remove("hidden", "error");
    try {
      const response = await fetch(apiUrl("refresh-candidates"), { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "更新失败");
      sessionStorage.setItem(
        "networkDiscoveryMessage",
        `更新完成：发现 ${payload.discoveriesObserved} 条，身份复核 ${payload.identityReviewed} 条，升格影子库 ${payload.identityPromoted} 条，身份冲突或排除 ${payload.identityRejected} 条。`
      );
      window.location.reload();
    } catch (error) {
      message.textContent = `更新失败：${error.message}`;
      message.classList.add("error");
      button.disabled = false;
      button.textContent = "重试全部更新";
    }
  });

  render();
})();
