(function initializeManualReview() {
  const snapshot = window.PENGUIN_CONVEXITY_MANUAL_REVIEW;
  const opportunityState = window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER;
  const routeState = window.PENGUIN_CONVEXITY_RESEARCH_ROUTES;
  const pageState = window.PenguinPageState;
  const restoredPageState = pageState?.load("manual-review") || {};
  const opportunityByCaseId = new Map(
    (opportunityState?.cases || []).map((item) => [item.caseId, item.opportunityStage]),
  );
  const routeByMasterId = new Map(
    (routeState?.records || []).map((item) => [item.masterId, item]),
  );
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const dateTime = (value) => {
    if (!value) return "--";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const money = (value) => {
    if (value === null || value === undefined || value === "") return "暂无";
    const number = Number(value);
    if (!Number.isFinite(number)) return "暂无";
    if (Math.abs(number) >= 1_000_000_000) return `$${(number / 1_000_000_000).toFixed(2)}B`;
    if (Math.abs(number) >= 1_000_000) return `$${(number / 1_000_000).toFixed(2)}M`;
    if (Math.abs(number) >= 1_000) return `$${(number / 1_000).toFixed(2)}K`;
    return `$${number.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
  };
  const apiUrl = location.pathname.startsWith("/convexity/")
    ? "/api/convexity/manual-review"
    : "/api/manual-review";
  const maturityOptions = ["L0", "L1", "L2", "L3", "L4", "L5"];
  const convexitySources = [
    "供应凸性",
    "流动性凸性",
    "产品采用凸性",
    "技术凸性",
    "事件凸性",
    "监管凸性",
    "期权结构凸性",
    "反身性凸性",
    "其他 / 待判断",
  ];
  const marketPresetRanges = {
    market: {
      lt1m: [0, 1_000_000],
      "1m10m": [1_000_000, 10_000_000],
      "10m100m": [10_000_000, 100_000_000],
      gte100m: [100_000_000, null],
    },
    liquidity: {
      lt20k: [0, 20_000],
      "20k100k": [20_000, 100_000],
      "100k1m": [100_000, 1_000_000],
      gte1m: [1_000_000, null],
    },
  };
  const auditStatusLabels = {
    active: "当前有效",
    superseded: "已被新版本替代",
    withdrawn: "已撤回",
    published: "已进入机会中心",
    draft: "草稿",
    preview: "预览",
  };
  const observeFallback = {
    finalActionLabel: "只观察",
    finalActionReason: "尚未取得最新统一动作，旧数据库动作仅保留历史，不生成当前行动结论。",
    blockerLabel: "统一动作待刷新",
  };
  const decisionFor = (item) => (
    item?.caseId ? opportunityByCaseId.get(item.caseId) || observeFallback : observeFallback
  );
  const routeFor = (item) => routeByMasterId.get(item?.masterId) || {
    routeId: "hybrid",
    routeLabel: "潜力项目",
    routeReason: "生命周期分类快照待刷新。",
    routeSourceLabel: "系统待刷新",
    researchFocusLabel: "潜力项目",
    researchFocusReason: "研究重点快照待刷新。",
    researchFocusSourceLabel: "系统待刷新",
    primaryFocus: "同时补齐基础档案与前置信号。",
    completeCount: 0,
    totalChecks: 0,
    nextEvidence: "刷新项目分类",
  };
  const queryParams = new URLSearchParams(location.search);
  let visibleTargets = [];
  let selectedKey = queryParams.get("target")
    || restoredPageState.selectedKey
    || "";
  let activeQueuePreset = queryParams.get("queue")
    || (queryParams.get("target") ? "all" : restoredPageState.activeQueuePreset)
    || "must_handle";
  let reviewListScrollTop = Number(restoredPageState.reviewListScrollTop || 0);
  const pageDrafts = { ...(restoredPageState.drafts || {}) };
  let lastOperation = restoredPageState.lastOperation || null;

  function feedback(type, title, detail) {
    const target = byId("manualReviewFeedback");
    target.hidden = false;
    target.className = `manual-review-feedback is-${type}`;
    target.innerHTML = `<strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p>`;
    target.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function controlState() {
    return {
      reviewTypeFilter: byId("reviewTypeFilter").value,
      reviewClassFilter: byId("reviewClassFilter").value,
      reviewRouteFilter: byId("reviewRouteFilter").value,
      reviewPublishFilter: byId("reviewPublishFilter").value,
      reviewSearch: byId("reviewSearch").value,
      reviewMarketStatusFilter: byId("reviewMarketStatusFilter").value,
      reviewMarketCapPreset: byId("reviewMarketCapPreset").value,
      reviewFdvPreset: byId("reviewFdvPreset").value,
      reviewLiquidityPreset: byId("reviewLiquidityPreset").value,
      reviewVolumePreset: byId("reviewVolumePreset").value,
    };
  }

  function persistPageState() {
    if (!pageState) return;
    pageState.save("manual-review", {
      selectedKey,
      activeQueuePreset,
      reviewListScrollTop,
      controls: controlState(),
      drafts: pageDrafts,
      lastOperation,
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
  }

  function targetLabel(item) {
    return item.recordType === "project" ? "正式项目" : "链上发现";
  }

  function selectedTarget() {
    return snapshot.targets.find((item) => item.masterId === selectedKey) || visibleTargets[0];
  }

  function metricMatchesPreset(value, preset, rangeType) {
    if (preset === "all") return true;
    const missing = value === null || value === undefined || value === "";
    if (preset === "missing") return missing;
    if (missing) return false;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return false;
    const range = marketPresetRanges[rangeType]?.[preset];
    if (!range) return true;
    const [minimum, maximum] = range;
    return parsed >= minimum && (maximum === null || parsed < maximum);
  }

  function marketFilters() {
    return {
      status: byId("reviewMarketStatusFilter").value,
      marketCap: byId("reviewMarketCapPreset").value,
      fdv: byId("reviewFdvPreset").value,
      liquidity: byId("reviewLiquidityPreset").value,
      volume: byId("reviewVolumePreset").value,
    };
  }

  function reviewQueue(item) {
    if (item.reviewQueue) return item.reviewQueue;
    const identityStatus = String(item.identityStatus || "").toLowerCase();
    if (item.publicationStatus === "published") return "published";
    if (
      identityStatus === "conflict"
      || (
        item.recordType === "project"
        && (
          !item.caseId
          || !item.contractAddress
          || !["verified", "corroborated"].includes(identityStatus)
        )
      )
      || (item.manualReview && !item.promotionEligible)
    ) {
      return "must_handle";
    }
    if (
      item.promotionEligible
      || (
        item.recordType === "project"
        && item.caseId
        && item.contractAddress
        && ["verified", "corroborated"].includes(identityStatus)
      )
      || (
        item.recordType === "discovery"
        && item.contractAddress
        && ["verified", "corroborated"].includes(identityStatus)
      )
    ) {
      return "worth_review";
    }
    return "low_priority";
  }

  function queueRecommendation(item) {
    const queue = reviewQueue(item);
    if (queue === "published") return "检查现有结论；只有判断变化时才修改或撤回";
    if (item.identityStatus === "conflict") return "先核对项目主体、合约与所在网络，解决身份冲突";
    if (item.recordType === "project" && !item.caseId) return "先补建研究案例，再判断是否发布";
    if (item.recordType === "project" && !item.contractAddress) return "先补齐并核验代币合约与所在网络";
    if (queue === "must_handle") return "先处理身份或发布边界缺口，不急于形成投资动作";
    if (queue === "worth_review") return "阅读自动事实，补充凸性来源与风险后决定是否发布";
    return "自动保留为资料，不需要现在人工处理";
  }

  function queuePresetMatches(item, preset) {
    return preset === "all" || reviewQueue(item) === preset;
  }

  function renderPresetCounts() {
    byId("presetMustHandleCount").textContent = snapshot.targets.filter(
      (item) => queuePresetMatches(item, "must_handle"),
    ).length;
    byId("presetWorthReviewCount").textContent = snapshot.targets.filter(
      (item) => queuePresetMatches(item, "worth_review"),
    ).length;
    byId("presetLowPriorityCount").textContent = snapshot.targets.filter(
      (item) => queuePresetMatches(item, "low_priority"),
    ).length;
    byId("presetPublishedCount").textContent = snapshot.targets.filter(
      (item) => queuePresetMatches(item, "published"),
    ).length;
    document.querySelectorAll("[data-review-preset]").forEach((button) => {
      button.classList.toggle("active", button.dataset.reviewPreset === activeQueuePreset);
    });
  }

  function renderList(options = {}) {
    const list = byId("manualReviewList");
    if (options.resetScroll) {
      reviewListScrollTop = 0;
    } else if (list?.childElementCount) {
      reviewListScrollTop = list.scrollTop;
    }
    const type = byId("reviewTypeFilter").value;
    const classification = byId("reviewClassFilter").value;
    const route = byId("reviewRouteFilter").value;
    const publication = byId("reviewPublishFilter").value;
    const query = byId("reviewSearch").value.trim().toLowerCase();
    const market = marketFilters();
    visibleTargets = snapshot.targets.filter((item) => {
      if (!queuePresetMatches(item, activeQueuePreset)) return false;
      if (type !== "all" && item.recordType !== type) return false;
      if (classification !== "all" && item.manualClassification !== classification) return false;
      if (route !== "all" && routeFor(item).routeId !== route) return false;
      if (publication !== "all" && item.publicationStatus !== publication) return false;
      if (market.status !== "all" && item.marketDataStatus !== market.status) return false;
      if (!metricMatchesPreset(item.marketCapUsd, market.marketCap, "market")) return false;
      if (!metricMatchesPreset(item.fdvUsd, market.fdv, "market")) return false;
      if (!metricMatchesPreset(item.liquidityUsd, market.liquidity, "liquidity")) return false;
      if (!metricMatchesPreset(item.volume24hUsd, market.volume, "liquidity")) return false;
      if (!query) return true;
      return [
        item.name,
        item.symbol,
        item.networkName,
        item.contractAddress,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
    if (!visibleTargets.some((item) => item.masterId === selectedKey)) {
      selectedKey = visibleTargets[0]?.masterId || "";
    }
    byId("reviewVisibleCount").textContent = visibleTargets.length;
    byId("manualReviewList").innerHTML = visibleTargets.length
      ? visibleTargets.map((item) => {
          const decision = decisionFor(item);
          const route = routeFor(item);
          return `
            <button type="button" class="${item.masterId === selectedKey ? "active" : ""}" data-review-target="${escapeHtml(item.masterId)}">
              <span><b>${escapeHtml(item.name)} ${item.symbol ? `<small>${escapeHtml(item.symbol)}</small>` : ""}</b><em>${escapeHtml(item.manualPriority)}</em></span>
              <strong>当前动作：${escapeHtml(decision.finalActionLabel)}</strong>
              <small class="manual-next-action">下一步：${escapeHtml(queueRecommendation(item))}</small>
              <small>项目类别：${escapeHtml(route.routeLabel)} · 研究标签：${escapeHtml(item.manualClassificationLabel)}</small>
              <small>${escapeHtml(targetLabel(item))} · ${escapeHtml(item.networkName || "网络待补齐")} · ${escapeHtml(item.publicationStatusLabel)}</small>
              <small class="manual-market-line">市值 ${escapeHtml(money(item.marketCapUsd))} · FDV ${escapeHtml(money(item.fdvUsd))} · 流动性 ${escapeHtml(money(item.liquidityUsd))}</small>
            </button>
          `;
        }).join("")
      : '<p class="manual-review-empty">当前筛选条件下没有记录。</p>';
    byId("manualReviewList").scrollTop = reviewListScrollTop;
    renderPresetCounts();
    renderDetail();
  }

  function option(value, current, label) {
    return `<option value="${escapeHtml(value)}" ${value === current ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }

  function renderDetail() {
    const item = selectedTarget();
    if (!item) {
      byId("manualReviewDetail").innerHTML = '<p class="manual-review-empty">请选择一条记录开始复核。</p>';
      return;
    }
    selectedKey = item.masterId;
    const savedValues = item.manualReview?.values || {};
    const draft = pageDrafts[item.masterId];
    const values = { ...savedValues, ...(draft?.values || {}) };
    const classification = values.classification || "unclassified";
    const priority = values.priority || "P2";
    const maturity = values.maturity || item.maturityLevel || "L0";
    const riskLevel = values.riskLevel || item.riskLevel || "unknown";
    const convexitySource = values.convexitySource || item.convexitySource || "其他 / 待判断";
    const note = values.note ?? item.manualReview?.note ?? "";
    const blockers = item.promotionBlockers || [];
    const published = item.publicationStatus === "published";
    const reviewed = Boolean(item.manualReview);
    const decision = decisionFor(item);
    const route = routeFor(item);
    byId("manualReviewDetail").innerHTML = `
      <header>
        <div>
          <span>${escapeHtml(targetLabel(item))} · ${escapeHtml(item.masterId)}</span>
          <h3>${escapeHtml(item.name)} ${item.symbol ? `<small>${escapeHtml(item.symbol)}</small>` : ""}</h3>
          <p>${escapeHtml(item.statusReason || "等待人工复核。")}</p>
        </div>
        <strong class="manual-publication-status status-${escapeHtml(item.publicationStatus)}">${escapeHtml(item.publicationStatusLabel)}</strong>
      </header>

      <section class="manual-current-decision">
        <div><span>当前唯一动作</span><strong>${escapeHtml(decision.finalActionLabel)}</strong></div>
        <p>${escapeHtml(decision.finalActionReason)}</p>
        ${decision.blockerLabel ? `<small>阻断状态：${escapeHtml(decision.blockerLabel)}</small>` : ""}
      </section>

      <section class="manual-research-route">
        <div>
          <span>当前项目类别</span>
          <strong>${escapeHtml(route.routeLabel)}</strong>
          <small>${escapeHtml(route.routeSourceLabel)} · 已覆盖 ${route.completeCount}/${route.totalChecks} 项</small>
        </div>
        <p>${escapeHtml(route.routeReason)}</p>
        <p><b>研究重点：</b>${escapeHtml(route.researchFocusLabel || route.routeLabel)} · ${escapeHtml(route.researchFocusSourceLabel || "跟随项目类别")}</p>
        <p>${escapeHtml(route.researchFocusReason || route.routeReason)}</p>
        <p><b>先查：</b>${escapeHtml(route.primaryFocus)}</p>
        <small>下一项补证：${escapeHtml(route.nextEvidence)}</small>
      </section>

      <section class="manual-fact-grid" aria-label="自动事实">
        <div><span>网络</span><strong>${escapeHtml(item.networkName || "待补齐")}</strong></div>
        <div><span>合约 / 资产标识</span><strong title="${escapeHtml(item.contractAddress)}">${escapeHtml(item.contractAddress || "待补齐")}</strong></div>
        <div><span>自动身份状态</span><strong>${escapeHtml(item.identityStatus || "待核验")}</strong></div>
        <div><span>研究案例</span><strong>${escapeHtml(item.caseId || "尚未建案")}</strong></div>
      </section>

      <section class="manual-market-facts" aria-label="最新行情事实">
        <header>
          <div><strong>最新行情事实</strong><small>${item.marketDataStatus === "available" ? `${escapeHtml(item.marketSourceName || "行情采集")} · ${escapeHtml(dateTime(item.marketObservedAt))}` : "暂无可用行情数据"}</small></div>
          <span>用于缩小复核范围，不直接构成行动结论</span>
        </header>
        <div>
          <article><span>流通市值</span><strong>${escapeHtml(money(item.marketCapUsd))}</strong></article>
          <article><span>FDV</span><strong>${escapeHtml(money(item.fdvUsd))}</strong></article>
          <article><span>流动性</span><strong>${escapeHtml(money(item.liquidityUsd))}</strong></article>
          <article><span>24小时成交额</span><strong>${escapeHtml(money(item.volume24hUsd))}</strong></article>
        </div>
      </section>

      <section class="manual-publish-steps" aria-label="标注发布进度">
        <article class="${reviewed ? "is-complete" : "is-current"}"><span>1</span><div><strong>保存人工标注</strong><small>${reviewed ? `已保存 · ${dateTime(item.manualReview.updatedAt)}` : "选择标签、风险和研究重点"}</small></div></article>
        <article class="${item.promotionEligible || published ? "is-complete" : "is-current"}"><span>2</span><div><strong>通过发布边界</strong><small>${blockers.length && !published ? `还有 ${blockers.length} 项阻断` : "主体、合约、案例与身份已通过"}</small></div></article>
        <article class="${published ? "is-complete" : "is-current"}"><span>3</span><div><strong>写入机会中心</strong><small>${published ? "数据库已确认发布" : "成功后刷新仍会保留"}</small></div></article>
      </section>

      ${draft ? `
        <div class="manual-draft-notice">
          <div><strong>已恢复未保存内容</strong><small>${escapeHtml(dateTime(draft.updatedAt))} 自动保存在这台电脑。</small></div>
          <button type="button" data-clear-review-draft>放弃未保存内容</button>
        </div>` : ""}

      <form id="manualReviewForm" class="manual-review-form">
        <div class="manual-form-grid">
          <label><span>人工研究标签（不改变当前动作）</span><select name="classification">
            ${Object.entries(snapshot.labels.classification).map(([value, label]) => option(value, classification, label)).join("")}
          </select></label>
          <label><span>复核优先级</span><select name="priority">
            ${Object.entries(snapshot.labels.priority).map(([value, label]) => option(value, priority, `${value} · ${label}`)).join("")}
          </select></label>
          <label><span>事实成熟度</span><select name="maturity">
            ${maturityOptions.map((value) => option(value, maturity, value)).join("")}
          </select></label>
          <label><span>风险等级</span><select name="riskLevel">
            ${Object.entries(snapshot.labels.risk).map(([value, label]) => option(value, riskLevel, label)).join("")}
          </select></label>
          <label class="manual-form-wide"><span>主凸性来源</span><select name="convexitySource">
            ${convexitySources.map((value) => option(value, convexitySource, value)).join("")}
          </select></label>
          <label><span>研究重点（不改变项目类别）</span><select name="researchRouteOverride">
            ${Object.entries(snapshot.labels.researchRoute).map(([value, label]) => option(value, values.researchRouteOverride || "auto", label)).join("")}
          </select></label>
          <label class="manual-form-wide"><span>人工调整研究重点的原因（跟随类别时可留空）</span><textarea name="researchRouteReason" rows="3" placeholder="说明为什么需要调整研究重点，保存后会进入审计历史，但不会改变早期、OG、潜力项目的自动分类。">${escapeHtml(values.researchRouteReason || "")}</textarea></label>
          <label class="manual-identity-confirm manual-form-wide">
            <input type="checkbox" name="identityConfirmed" ${values.identityConfirmed ? "checked" : ""} />
            <span><b>我已人工确认项目主体、代币合约与所在网络一致</b><small>只在核对过官网、区块浏览器或其他可靠来源后勾选。它可以替代“自动身份待核验”这一项，但不能替代缺失的合约和研究案例。</small></span>
          </label>
          <label class="manual-form-wide"><span>复核备注</span><textarea name="note" rows="5" placeholder="记录判断依据、待补证问题、风险或撤回原因。">${escapeHtml(note)}</textarea></label>
        </div>
        <div class="manual-action-bar">
          <button class="manual-publish-button" type="button" data-review-operation="save_and_promote">${published ? "保存修改并保持发布" : "保存标注并发布"}</button>
          <button class="manual-save-button" type="submit">仅保存标注</button>
          <button type="button" data-review-operation="promote" ${item.promotionEligible && !published ? "" : "disabled"}>发布已保存标注</button>
          <button class="is-warning" type="button" data-review-operation="withdraw_publication" ${item.publicationStatus === "published" ? "" : "disabled"}>撤回机会中心发布</button>
          <button class="is-quiet" type="button" data-review-operation="withdraw_review" ${item.manualReview ? "" : "disabled"}>撤回人工标注</button>
        </div>
      </form>

      <aside class="manual-promotion-check ${published ? "is-published" : blockers.length ? "is-blocked" : "is-ready"}">
        <strong>${published ? "已真实发布" : blockers.length ? "当前不能发布" : "当前满足发布条件"}</strong>
        ${published
          ? "<p>发布状态已经写入本地数据库和审计历史。刷新页面后仍会保留，机会中心项目卡会显示“人工发布”。</p>"
          : blockers.length
          ? `<ul>${blockers.map((blocker) => `<li>${escapeHtml(blocker)}</li>`).join("")}</ul>`
          : "<p>保存的分类、项目身份、合约和研究案例均已满足发布边界。可以直接点击“保存标注并发布”。</p>"}
      </aside>
    `;
  }

  function formPayload(operation) {
    const form = byId("manualReviewForm");
    const data = new FormData(form);
    return {
      operation,
      targetKey: selectedKey,
      classification: data.get("classification"),
      priority: data.get("priority"),
      maturity: data.get("maturity"),
      riskLevel: data.get("riskLevel"),
      researchRouteOverride: data.get("researchRouteOverride"),
      researchRouteReason: data.get("researchRouteReason"),
      convexitySource: data.get("convexitySource"),
      identityConfirmed: data.get("identityConfirmed") === "on",
      note: data.get("note"),
    };
  }

  function rememberCurrentDraft() {
    const form = byId("manualReviewForm");
    if (!form || !selectedKey) return;
    const payload = formPayload("draft");
    const { operation: _operation, targetKey: _targetKey, ...values } = payload;
    pageDrafts[selectedKey] = {
      values,
      updatedAt: new Date().toISOString(),
    };
    persistPageState();
  }

  function clearCurrentDraft() {
    delete pageDrafts[selectedKey];
    persistPageState();
  }

  function renderLastOperation() {
    const target = byId("manualReviewLastOperation");
    if (!lastOperation) {
      target.hidden = true;
      return;
    }
    const item = snapshot.targets.find((row) => row.masterId === lastOperation.targetKey);
    target.hidden = false;
    target.className = `manual-review-last-operation is-${escapeHtml(lastOperation.status || "success")}`;
    target.innerHTML = `
      <div>
        <span>最近一次操作 · ${escapeHtml(dateTime(lastOperation.at))}</span>
        <strong>${escapeHtml(lastOperation.title || "操作记录")}</strong>
      </div>
      <p>${escapeHtml(item?.name || lastOperation.targetKey || "当前项目")} · ${escapeHtml(lastOperation.detail || "状态已记录")}</p>
    `;
  }

  function setRunning(running) {
    document.querySelectorAll(".manual-review-detail button").forEach((button) => {
      if (running) {
        button.dataset.wasDisabled = String(button.disabled);
        button.disabled = true;
      } else {
        button.disabled = button.dataset.wasDisabled === "true";
        delete button.dataset.wasDisabled;
      }
    });
  }

  async function submitOperation(operation) {
    setRunning(true);
    feedback(
      "running",
      operation === "save_and_promote" ? "正在保存并发布" : "正在保存操作",
      "系统正在写入数据库、重建页面快照并保留审计历史，请不要重复点击。",
    );
    try {
      const response = await fetch(apiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formPayload(operation)),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || payload.message || `请求失败：${response.status}`);
      lastOperation = {
        status: "success",
        title: operation === "save_and_promote" ? "标注与发布均已成功" : "操作成功",
        detail: payload.message,
        targetKey: selectedKey,
        at: new Date().toISOString(),
      };
      clearCurrentDraft();
      sessionStorage.setItem("convexity-manual-review-feedback", JSON.stringify({
        type: "success",
        title: operation === "save_and_promote" ? "标注与发布均已成功" : "操作成功",
        detail: `${payload.message} 刷新后状态仍会保留。`,
      }));
      const nextUrl = new URL(location.href);
      nextUrl.searchParams.set("target", selectedKey);
      location.replace(nextUrl.toString());
    } catch (error) {
      lastOperation = {
        status: "error",
        title: "操作没有完成",
        detail: error.message,
        targetKey: selectedKey,
        at: new Date().toISOString(),
      };
      persistPageState();
      renderLastOperation();
      feedback("error", "操作没有完成", `${error.message} 当前填写内容已保留，可以修正后重试。`);
      setRunning(false);
    }
  }

  function renderAudit() {
    const targetNames = Object.fromEntries(snapshot.targets.map((item) => [item.masterId, item.name]));
    byId("auditTotal").textContent = snapshot.audit.length;
    byId("manualAuditRows").innerHTML = snapshot.audit.length
      ? snapshot.audit.map((item) => `
          <tr>
            <td><strong>${escapeHtml(item.action)}</strong><small>${escapeHtml(dateTime(item.updatedAt))}</small></td>
            <td><b>${escapeHtml(targetNames[item.targetKey] || item.targetKey)}</b><small>${escapeHtml(item.targetKey)}</small></td>
            <td><span class="manual-audit-status status-${escapeHtml(item.status)}">${escapeHtml(auditStatusLabels[item.status] || item.status)}</span></td>
            <td>${escapeHtml(item.summary || "未填写说明")}${item.details?.researchRouteOverride && item.details.researchRouteOverride !== "auto" ? `<small>研究重点：${escapeHtml(snapshot.labels.researchRoute[item.details.researchRouteOverride] || item.details.researchRouteOverride)} · ${escapeHtml(item.details.researchRouteReason || "原因待补")}</small>` : ""}</td>
            <td>${escapeHtml(item.actor || "local-owner")}</td>
          </tr>
        `).join("")
      : '<tr><td colspan="5" class="manual-review-empty">尚无人工操作历史。</td></tr>';
  }

  if (!snapshot) {
    feedback("error", "人工复核快照未生成", "请返回凸性工作台后重新打开本页。");
    return;
  }

  byId("manualReviewPolicy").textContent = snapshot.policy;
  byId("reviewTotal").textContent = snapshot.counts.total;
  byId("mustHandleTotal").textContent = snapshot.targets.filter(
    (item) => reviewQueue(item) === "must_handle",
  ).length;
  byId("worthReviewTotal").textContent = snapshot.targets.filter(
    (item) => reviewQueue(item) === "worth_review",
  ).length;
  byId("lowPriorityTotal").textContent = snapshot.targets.filter(
    (item) => reviewQueue(item) === "low_priority",
  ).length;
  byId("publishedTotal").textContent = snapshot.counts.published;
  byId("manualReviewGeneratedAt").textContent = `后台快照：${dateTime(snapshot.generatedAt)}`;
  restorePageControls();
  if (!["all", "must_handle", "worth_review", "low_priority", "published"].includes(activeQueuePreset)) {
    activeQueuePreset = "must_handle";
  }
  renderPresetCounts();
  renderList();
  renderAudit();
  renderLastOperation();

  const savedFeedback = sessionStorage.getItem("convexity-manual-review-feedback");
  if (savedFeedback) {
    sessionStorage.removeItem("convexity-manual-review-feedback");
    try {
      const item = JSON.parse(savedFeedback);
      feedback(item.type, item.title, item.detail);
    } catch (_error) {
      // Ignore malformed one-time feedback.
    }
  } else if (restoredPageState.savedAt) {
    pageState?.notify("已恢复人工复核的筛选、当前项目和阅读位置");
  }
  pageState?.restoreScroll(restoredPageState, { skipWhenHash: true });

  ["reviewTypeFilter", "reviewClassFilter", "reviewRouteFilter", "reviewPublishFilter", "reviewMarketStatusFilter"].forEach((id) => {
    byId(id).addEventListener("change", () => {
      renderList({ resetScroll: true });
      persistPageState();
    });
  });
  byId("reviewSearch").addEventListener("input", () => {
    renderList({ resetScroll: true });
    persistPageState();
  });
  [
    "reviewMarketCapPreset",
    "reviewFdvPreset",
    "reviewLiquidityPreset",
    "reviewVolumePreset",
  ].forEach((id) => byId(id).addEventListener("change", () => {
    renderList({ resetScroll: true });
    persistPageState();
  }));
  byId("reviewMarketReset").addEventListener("click", () => {
    byId("reviewMarketStatusFilter").value = "all";
    [
      "reviewMarketCapPreset",
      "reviewFdvPreset",
      "reviewLiquidityPreset",
      "reviewVolumePreset",
    ].forEach((id) => {
      byId(id).value = "all";
    });
    renderList({ resetScroll: true });
    persistPageState();
  });
  document.querySelector(".manual-review-presets").addEventListener("click", (event) => {
    const button = event.target.closest("[data-review-preset]");
    if (!button) return;
    activeQueuePreset = button.dataset.reviewPreset;
    byId("reviewTypeFilter").value = "all";
    byId("reviewClassFilter").value = "all";
    byId("reviewRouteFilter").value = "all";
    byId("reviewPublishFilter").value = "all";
    byId("reviewSearch").value = "";
    byId("reviewMarketStatusFilter").value = "all";
    [
      "reviewMarketCapPreset",
      "reviewFdvPreset",
      "reviewLiquidityPreset",
      "reviewVolumePreset",
    ].forEach((id) => {
      byId(id).value = "all";
    });
    renderList({ resetScroll: true });
    persistPageState();
  });
  let listScrollSaveTimer = 0;
  byId("manualReviewList").addEventListener("scroll", () => {
    reviewListScrollTop = byId("manualReviewList").scrollTop;
    clearTimeout(listScrollSaveTimer);
    listScrollSaveTimer = setTimeout(persistPageState, 120);
  }, { passive: true });
  byId("manualReviewList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-review-target]");
    if (!button) return;
    selectedKey = button.dataset.reviewTarget;
    renderList();
    persistPageState();
  });
  byId("manualReviewDetail").addEventListener("input", (event) => {
    if (event.target.closest("#manualReviewForm")) rememberCurrentDraft();
  });
  byId("manualReviewDetail").addEventListener("change", (event) => {
    if (event.target.closest("#manualReviewForm")) rememberCurrentDraft();
  });
  byId("manualReviewDetail").addEventListener("submit", (event) => {
    event.preventDefault();
    submitOperation("save_review");
  });
  byId("manualReviewDetail").addEventListener("click", (event) => {
    const clearDraftButton = event.target.closest("[data-clear-review-draft]");
    if (clearDraftButton) {
      clearCurrentDraft();
      renderDetail();
      feedback("success", "未保存内容已放弃", "页面已恢复为最近一次成功保存的人工标注。");
      return;
    }
    const button = event.target.closest("[data-review-operation]");
    if (!button || button.disabled) return;
    submitOperation(button.dataset.reviewOperation);
  });
  let scrollSaveTimer = 0;
  window.addEventListener("scroll", () => {
    clearTimeout(scrollSaveTimer);
    scrollSaveTimer = setTimeout(persistPageState, 160);
  }, { passive: true });
  window.addEventListener("pagehide", persistPageState);
}());
