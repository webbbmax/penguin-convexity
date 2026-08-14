(function c24Front() {
  "use strict";
  const data = window.PENGUIN_CONVEXITY_C24;
  if (!data || data.schemaVersion !== "c2.4-public-snapshot-v1" || !data.isComplete) return;

  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const stateLabels = { convexity_clue: "凸性线索", active_project: "活跃项目", observing: "观察中" };
  const lifecycleLabels = { new_0_90: "90 天内", continued_91_plus: "90 天后持续跟踪" };
  const relationshipLabels = { A: "A 新项目新币", B: "B 老项目新资产", C: "C 项目关系未完全确认" };
  const pathLabels = {
    trade_demand_formation: "交易需求形成",
    liquidity_exit_quality: "流动性与退出质量",
    supply_holder_improvement: "供应与持币结构改善",
    indexed_pool_activity_vs_supply_adjusted_valuation: "已索引池活动跑赢供应调整估值",
  };
  const pathStatusLabels = { formed: "已形成", not_formed: "尚未形成", unavailable: "当前不可用" };
  const factorLabels = { D: "交易需求", L: "流动性与退出", S: "供应结构", G: "活动与估值", Q: "稳定性与异常" };
  const cohortLabels = {
    same_chain_same_age_band_rolling_30_days: "同链同日龄近 30 天",
    all_supported_chains_same_age_band_rolling_30_days: "六链同日龄近 30 天",
    same_chain_continued_91_plus_rolling_30_days: "同链 90 天后持续跟踪近 30 天",
    all_supported_chains_continued_91_plus_rolling_30_days: "六链 90 天后持续跟踪近 30 天",
    same_chain_age_31_90_rolling_30_days: "同链 31—90 天近 30 天",
    all_supported_chains_age_31_90_rolling_30_days: "六链 31—90 天近 30 天",
    frozen_age_band_fallback: "冻结历史后备值（当前可比样本不足）",
  };
  const metricLabels = { buys: "买入笔数", sells: "卖出笔数", volumeUsd: "成交额", volumeP40: "同组成交额 P40", volumeP50: "同组成交额 P50", transactionCount: "交易笔数", transactionsP50: "同组交易笔数 P50", volumeLiquidityRatio: "成交额 / 流动性", volumeLiquidityRatioP50: "同组成交额 / 流动性 P50", liquidityUsd: "流动性", liquidityP50: "同组流动性 P50", liquidityFloorUsd: "日龄流动性护栏", sellQuoteLossPct: "100 美元卖出损失", liquidityDropPct: "流动性下降", top10ShareChangePercentagePoints: "前 10 地址份额变化", holderHhiChangePct: "持币集中度变化", supplyChangePct: "供应变化", relativeExpansion: "池活动相对扩张", relativeExpansionP50: "同组池活动相对扩张 P50", riskAdjustedSurplus: "风险调整剩余", indexedPoolCount: "已索引池数", unindexedDiscoveredPoolCount: "发现但未索引池数" };
  const formatTime = (value) => {
    if (!value) return "暂无";
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  };
  const formatNumber = (value, digits = 2) => value == null ? "暂无" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits, notation: Math.abs(Number(value)) >= 1_000_000 ? "compact" : "standard" }).format(value);

  function horizontalBars(title, intro, rows, options = {}) {
    const values = rows.map((row) => Math.max(0, Number(row.value || 0)));
    const scaleMax = Math.max(Number(options.scaleMax || 0), ...values, 1);
    const bars = rows.map((row) => {
      const value = Math.max(0, Number(row.value || 0));
      const width = Math.min(100, value / scaleMax * 100);
      const displayValue = row.displayValue ?? formatNumber(value, options.digits ?? 0);
      return `<article class="c24-bar-row" data-tone="${esc(row.tone || "blue")}"><div class="c24-bar-label"><span>${esc(row.label)}</span><strong>${esc(displayValue)}</strong></div><div class="c24-bar-track" aria-hidden="true"><i style="--bar-width:${width}%"></i></div>${row.note ? `<small>${esc(row.note)}</small>` : ""}</article>`;
    }).join("");
    return `<section class="c24-chart-panel"><div class="c24-chart-head"><div><h2>${esc(title)}</h2><p>${esc(intro)}</p></div><span>${esc(options.axisNote || "横条从 0 开始，右侧显示精确值")}</span></div><div class="c24-bar-chart" role="img" aria-label="${esc(title)}">${bars}</div></section>`;
  }

  function compositionChart(title, intro, segments) {
    const total = segments.reduce((sum, segment) => sum + Number(segment.value || 0), 0);
    const bar = segments.map((segment) => {
      const value = Number(segment.value || 0);
      const width = total ? value / total * 100 : 0;
      return `<i data-tone="${esc(segment.tone)}" style="--segment-width:${width}%" title="${esc(`${segment.label} ${formatNumber(value, 0)}`)}"></i>`;
    }).join("");
    const legend = segments.map((segment) => `<span data-tone="${esc(segment.tone)}"><i></i>${esc(segment.label)} <strong>${esc(formatNumber(segment.value, 0))}</strong></span>`).join("");
    return `<section class="c24-chart-panel c24-composition-panel"><div class="c24-chart-head"><div><h2>${esc(title)}</h2><p>${esc(intro)}</p></div><strong>${esc(formatNumber(total, 0))}</strong></div><div class="c24-composition-track" role="img" aria-label="${esc(`${title}：${segments.map((segment) => `${segment.label} ${formatNumber(segment.value, 0)}`).join("，")}`)}">${bar}</div><div class="c24-chart-legend">${legend}</div></section>`;
  }

  function chainDistributionChart(rows, title = "六链结果分布") {
    const counts = Object.fromEntries(data.chainOrder.map((chain) => [chain, 0]));
    rows.forEach((row) => { if (row.chainId in counts) counts[row.chainId] += 1; });
    return horizontalBars(title, "六条链分别计数，零结果不会被隐藏。", data.chainOrder.map((chain) => ({ label: data.chainLabels[chain] || chain, value: counts[chain], tone: "blue" })));
  }

  function methodFlow() {
    const steps = [
      ["第一步", "90 天候选", "核验真实 T0、稳定资产身份、至少一买一卖，以及没有已确认硬交易阻断。"],
      ["第二步", "首轮跟踪", "核验100美元卖出报价、项目证据和已确认的冻结、黑名单或卖出阻断。"],
      ["第三步", "达到公开底线", "报价成功、项目证据可归属、身份稳定且没有已确认硬交易阻断后公开。"],
    ];
    return `<section class="c24-chart-panel c24-stage-panel"><div class="c24-chart-head"><div><h2>程序三步判断</h2><p>这是先后顺序，不是三张互不相干的说明卡。</p></div><span>年龄只决定生命周期池，不代表质量</span></div><div class="c24-stage-flow c24-method-flow" role="img" aria-label="程序三步判断：90 天候选、凸性跟踪、达到公开底线">${steps.map(([step, title, note], index) => `${index ? '<div class="c24-stage-connector"><i aria-hidden="true">→</i><strong>通过后继续</strong></div>' : ""}<article class="c24-stage-card"><span>${esc(step)}</span><h3>${esc(title)}</h3><p>${esc(note)}</p></article>`).join("")}</div></section>`;
  }

  function pathStatusMatrix(paths) {
    const statuses = ["formed", "not_formed", "unavailable"];
    return `<div class="c24-status-matrix" role="img" aria-label="四条强证据路径状态矩阵"><div class="c24-status-matrix-head"><span>强证据路径</span>${statuses.map((status) => `<span>${esc(pathStatusLabels[status])}</span>`).join("")}</div>${paths.map((path) => `<div class="c24-status-matrix-row"><strong>${esc(pathLabels[path.pathCode] || path.pathCode)}</strong>${statuses.map((status) => `<span data-selected="${path.status === status ? "true" : "false"}" data-tone="${esc(status)}">${path.status === status ? "●" : "○"}</span>`).join("")}</div>`).join("")}</div>`;
  }

  function sellLossGauge(value) {
    if (value == null) return `<div class="c24-loss-gauge" data-state="unavailable"><div><span>100 美元卖出损失</span><strong>当前不可用</strong></div><p>没有可复算报价时，不显示假进度。</p></div>`;
    const loss = Math.max(0, Number(value));
    const max = Math.max(100, Math.ceil(loss / 10) * 10);
    const width = Math.min(100, loss / max * 100);
    return `<div class="c24-loss-gauge" data-state="normal"><div><span>100 美元卖出损失</span><strong>${esc(formatNumber(loss))}%</strong></div><div class="c24-loss-track" role="img" aria-label="100 美元卖出损失 ${esc(formatNumber(loss))}%，当前只作信息记录"><i style="--loss-width:${width}%"></i></div><div class="c24-loss-scale"><span>0%</span><span>${esc(max)}%</span></div><p>当前只记录实际报价结果，不作为公开或强证据路径的百分比门槛。</p></div>`;
  }

  function factorScoreChart(factors) {
    if (!factors?.length) return `<p class="c24-note">当前没有足够实测指标形成可复算的五因子图，程序不会用默认 50 分补图。</p>`;
    return horizontalBars("五因子后验分", "横条范围为 0—100，只用于同链相对排序与变化解释。", factors.map((factor) => ({
      label: factorLabels[factor.factor] || factor.factor, value: Number(factor.score || 0), displayValue: formatNumber(factor.score), tone: "blue",
    })), { scaleMax: 100, digits: 2, axisNote: "统一 0—100 刻度" });
  }

  function renderPathMetrics(metrics) {
    const percent = new Set(["sellQuoteLossPct", "liquidityDropPct", "top10ShareChangePercentagePoints", "holderHhiChangePct", "supplyChangePct"]);
    const usd = new Set(["volumeUsd", "volumeP40", "volumeP50", "liquidityUsd", "liquidityP50", "liquidityFloorUsd", "riskAdjustedSurplus"]);
    const rows = Object.entries(metrics || {}).filter(([, value]) => value != null);
    if (!rows.length) return `<p class="c24-note">本路径当前没有可展开的实测指标。</p>`;
    return `<dl class="c24-facts">${rows.map(([key, value]) => `<div><dt>${esc(metricLabels[key] || key)}</dt><dd>${esc(formatNumber(value, key === "volumeLiquidityRatio" || key === "relativeExpansion" ? 4 : 2))}${percent.has(key) ? "%" : usd.has(key) ? " USD" : ""}</dd></div>`).join("")}</dl>`;
  }
  const main = $(".c19-front-main") || $(".c19-detail-main") || $("main");
  if (!main) return;
  main.className = "c24-main";

  const params = new URLSearchParams(location.search);
  const pageName = location.pathname.split("/").pop();
  const mode = pageName === "project-detail.html" ? "detail" : pageName === "change-explanations.html" ? "changes" : params.get("view") || "home";
  document.querySelectorAll(".c19-front-nav a").forEach((link) => {
    link.classList.toggle("is-active", link.dataset.frontMode === mode);
    if (link.dataset.frontMode === "changes") link.href = "change-explanations.html";
  });

  function head(kicker, title, intro) {
    return `<header class="c24-page-head"><div><span class="c24-kicker">${esc(kicker)}</span><h1>${esc(title)}</h1><p>${esc(intro)}</p></div><div class="c24-cutoff"><span>完整快照</span><strong>${esc(formatTime(data.dataCutoffAt))}</strong></div></header>`;
  }

  function chainTabs(selected, all = false, countRows = null) {
    const entries = all ? [["all", "全部链"], ...data.chainOrder.map((chain) => [chain, data.chainLabels[chain]])] : data.chainOrder.map((chain) => [chain, data.chainLabels[chain]]);
    const visibleCounts = countRows
      ? countRows.reduce((counts, row) => ({ ...counts, [row.chainId]: (counts[row.chainId] || 0) + 1 }), {})
      : data.chainCounts;
    const total = countRows ? countRows.length : data.items.length;
    return `<div class="c24-chain-tabs" role="tablist">${entries.map(([chain, label]) => `<button type="button" class="${selected === chain ? "is-active" : ""}" data-chain-tab="${esc(chain)}"><span>${esc(label)}</span><strong>${chain === "all" ? total : visibleCounts[chain] || 0}</strong></button>`).join("")}</div>`;
  }

  function statusBadge(item) {
    return `<span class="c24-status" data-state="${esc(item.publicState)}">${esc(stateLabels[item.publicState])}</span>`;
  }

  function card(item) {
    const rank = item.rankingAvailable ? `同链相对排名 #${item.bayesRankWithinChain}` : "尚无可复算排名";
    return `<a class="c24-card" href="${esc(item.detailHref)}" data-detail-link>
      <div class="c24-card-title"><div><span class="c24-chain">${esc(data.chainLabels[item.chainId] || item.chainId)}</span><h2>${esc(item.canonicalName)} <small>${esc(item.symbol)}</small></h2></div>${statusBadge(item)}</div>
      <div class="c24-card-meta"><span>${esc(lifecycleLabels[item.lifecyclePool])}</span><span>已公开流通第 ${esc(item.ageDays)} 天</span><span>${esc(rank)}</span></div>
      <dl class="c24-five-answers">
        <div><dt>为什么现在关注</dt><dd>${esc(item.whyNow)}</dd></div>
        <div><dt>关键程序证据</dt><dd>${esc(item.keyEvidence)}</dd></div>
        <div><dt>最大风险或反证</dt><dd>${esc(item.largestRisk)}</dd></div>
        <div><dt>接下来观察</dt><dd>${esc(item.nextWatch)}</dd></div>
        <div><dt>数据截止</dt><dd>${esc(formatTime(item.dataCutoffAt))}</dd></div>
      </dl><span class="c24-card-link">查看证据与计算依据 →</span>
    </a>`;
  }

  function empty(message) {
    return `<div class="c24-empty"><h2>${esc(message)}</h2><p>这里不放占位项目，也不把后台待处理对象写成公开机会。</p></div>`;
  }

  function rememberReturn() {
    sessionStorage.setItem("c24-return", JSON.stringify({ url: pageName + location.search, scrollY: window.scrollY }));
  }

  function restoreReturnScroll() {
    if (mode === "detail") return;
    try {
      const pending = JSON.parse(sessionStorage.getItem("c24-restore-scroll") || "null");
      if (!pending || pending.url !== pageName + location.search) return;
      sessionStorage.removeItem("c24-restore-scroll");
      requestAnimationFrame(() => scrollTo({ top: Number(pending.scrollY) || 0, behavior: "auto" }));
    } catch (_) {
      sessionStorage.removeItem("c24-restore-scroll");
    }
  }

  function bindDetailLinks() {
    document.querySelectorAll("[data-detail-link]").forEach((link) => link.addEventListener("click", rememberReturn));
  }

  function firstAvailableChain() {
    const saved = sessionStorage.getItem("c24-home-chain");
    if (saved && data.chainOrder.includes(saved)) return saved;
    return data.chainOrder.find((chain) => (data.chainCounts[chain] || 0) > 0) || data.chainOrder[0];
  }

  function renderHome() {
    let selectedChain = firstAvailableChain();
    const byAsset = new Map(data.items.map((item) => [item.assetId, item]));
    main.innerHTML = head("凸性机会中心", "按链看重点", "这里只展示已经完成第二关并达到公开底线的真实项目；首页顺序不限制后台跟踪和全部机会收录。") + `
      <div class="c24-chart-grid">
        ${compositionChart("公开结果构成", "三种公开状态互斥，三项合计等于已达到公开底线的项目总数。", [
          { label: "凸性线索", value: data.publicStateCounts.convexity_clue || 0, tone: "green" },
          { label: "活跃项目", value: data.publicStateCounts.active_project || 0, tone: "blue" },
          { label: "观察中", value: data.publicStateCounts.observing || 0, tone: "gray" },
        ])}
        ${compositionChart("生命周期构成", "90 天后持续跟踪只来自曾在 90 天内完成两关并达到公开底线的同一资产。", [
          { label: "90 天内", value: data.lifecycleCounts.new_0_90 || 0, tone: "blue" },
          { label: "90 天后持续跟踪", value: data.lifecycleCounts.continued_91_plus || 0, tone: "orange" },
        ])}
      </div>
      ${chainDistributionChart(data.items, "公开项目的六链分布")}
      <section class="c24-section"><div class="c24-section-title"><div><span class="c24-kicker">六条链分开判断</span><h2>选择要看的链</h2></div><p>同链前 10 仅决定首页展示顺序，不限制跟踪和全部机会收录。</p></div><div id="c24HomeTabs">${chainTabs(selectedChain)}</div><div id="c24HomeList"></div></section>
      <section class="c24-section c24-explainer"><div><span class="c24-kicker">页面只回答有用的问题</span><h2>为什么这里只有公开结果</h2></div><p>“90 天候选”先交给首轮跟踪；100 美元卖出报价成功、项目证据可归属、身份稳定且没有已确认硬交易阻断后即可公开。全池结构与历史供应随后独立补充，不再堵住公开队列。</p><a href="candidate-pool.html?view=method">查看判断方法与程序上限 →</a></section>`;
    const renderChain = () => {
      const ids = data.homeTop10[selectedChain] || [];
      const rows = ids.map((id) => byAsset.get(id)).filter(Boolean);
      $("#c24HomeTabs").innerHTML = chainTabs(selectedChain);
      const publicOnChain = data.items.filter((item) => item.chainId === selectedChain).length;
      $("#c24HomeList").innerHTML = rows.length
        ? `<div class="c24-card-grid">${rows.map(card).join("")}</div>`
        : publicOnChain
          ? `<div class="c24-empty"><h2>本链有 ${esc(publicOnChain)} 个公开项目，但暂时没有完整贝叶斯结果</h2><p>它们仍在继续跟踪，也已保留在“全部机会”；程序不用默认 50 分制造假排名。</p><a class="c24-external" href="candidate-pool.html?view=all&chain=${esc(selectedChain)}">查看本链全部公开项目 →</a></div>`
          : empty("本链目前没有完成第二关并达到公开底线的项目");
      document.querySelectorAll("[data-chain-tab]").forEach((button) => button.onclick = () => { selectedChain = button.dataset.chainTab; sessionStorage.setItem("c24-home-chain", selectedChain); renderChain(); });
      bindDetailLinks();
    };
    renderChain();
  }

  function renderAll() {
    let chain = params.get("chain") || "all";
    let lifecycle = params.get("lifecycle") || "all";
    let relationship = params.get("relationship") || "all";
    let state = params.get("state") || "all";
    let query = params.get("q") || "";
    let page = Math.max(1, Number(params.get("page") || 1));
    main.innerHTML = head("完整公开集合", "全部机会", "这里等于完整公开快照，不受首页每链前 10 影响；全部链模式不生成跨链总榜。") + `
      <section class="c24-section"><div id="c24AllChainTabs">${chainTabs(chain, true)}</div>
      <div class="c24-filters"><input id="c24Query" type="search" value="${esc(query)}" placeholder="搜索项目或代币符号"><select id="c24Lifecycle"><option value="all">两个生命周期池</option><option value="new_0_90">90 天内</option><option value="continued_91_plus">90 天后持续跟踪</option></select><select id="c24Relationship"><option value="all">A/B/C 全部</option><option value="A">A 新项目新币</option><option value="B">B 老项目新资产</option><option value="C">C 关系未完全确认</option></select><select id="c24State"><option value="all">三种公开状态</option><option value="convexity_clue">凸性线索</option><option value="active_project">活跃项目</option><option value="observing">观察中</option></select><span id="c24AllCount"></span></div>
      <div id="c24AllVisual"></div><div id="c24AllList"></div><div id="c24AllPages" class="c24-pages"></div></section>`;
    $("#c24Lifecycle").value = lifecycle; $("#c24Relationship").value = relationship; $("#c24State").value = state;
    const syncUrl = () => {
      const next = new URLSearchParams({ view: "all" });
      if (chain !== "all") next.set("chain", chain); if (lifecycle !== "all") next.set("lifecycle", lifecycle);
      if (relationship !== "all") next.set("relationship", relationship); if (state !== "all") next.set("state", state);
      if (query) next.set("q", query); if (page > 1) next.set("page", page);
      history.replaceState(null, "", `candidate-pool.html?${next}`);
    };
    const render = () => {
      let rows = data.items.filter((item) => (chain === "all" || item.chainId === chain) && (lifecycle === "all" || item.lifecyclePool === lifecycle) && (relationship === "all" || item.relationshipClass === relationship) && (state === "all" || item.publicState === state) && (!query || `${item.canonicalName} ${item.symbol}`.toLowerCase().includes(query.toLowerCase())));
      if (chain !== "all") rows.sort((a, b) => (a.bayesRankWithinChain || 999999) - (b.bayesRankWithinChain || 999999));
      else rows.sort((a, b) => data.chainOrder.indexOf(a.chainId) - data.chainOrder.indexOf(b.chainId) || String(b.dataCutoffAt).localeCompare(String(a.dataCutoffAt)));
      const pageCount = Math.max(1, Math.ceil(rows.length / 20)); page = Math.min(page, pageCount);
      $("#c24AllChainTabs").innerHTML = chainTabs(chain, true);
      $("#c24AllCount").textContent = `${rows.length} 个公开项目 · 第 ${page}/${pageCount} 页`;
      const stateCounts = rows.reduce((counts, item) => ({ ...counts, [item.publicState]: (counts[item.publicState] || 0) + 1 }), {});
      const lifecycleCounts = rows.reduce((counts, item) => ({ ...counts, [item.lifecyclePool]: (counts[item.lifecyclePool] || 0) + 1 }), {});
      $("#c24AllVisual").innerHTML = rows.length ? `<div class="c24-chart-grid">${compositionChart("当前筛选的状态构成", "图表会随链、生命周期、项目关系、公开状态和搜索条件一起变化。", [
        { label: "凸性线索", value: stateCounts.convexity_clue || 0, tone: "green" },
        { label: "活跃项目", value: stateCounts.active_project || 0, tone: "blue" },
        { label: "观察中", value: stateCounts.observing || 0, tone: "gray" },
      ])}${compositionChart("当前筛选的生命周期", "这只是当前筛选结果的构成，不改变任何项目的跟踪状态。", [
        { label: "90 天内", value: lifecycleCounts.new_0_90 || 0, tone: "blue" },
        { label: "90 天后持续跟踪", value: lifecycleCounts.continued_91_plus || 0, tone: "orange" },
      ])}</div>${chainDistributionChart(rows, "当前筛选的六链分布")}` : "";
      const pageRows = rows.slice((page - 1) * 20, page * 20);
      $("#c24AllList").innerHTML = pageRows.length ? `<div class="c24-card-grid">${pageRows.map(card).join("")}</div>` : empty("当前筛选条件下暂无符合公开底线的项目");
      $("#c24AllPages").innerHTML = Array.from({ length: pageCount }, (_, index) => `<button type="button" ${index + 1 === page ? 'aria-current="page"' : ""} data-page="${index + 1}">${index + 1}</button>`).join("");
      document.querySelectorAll("[data-chain-tab]").forEach((button) => button.onclick = () => { chain = button.dataset.chainTab; page = 1; render(); });
      document.querySelectorAll("[data-page]").forEach((button) => button.onclick = () => { page = Number(button.dataset.page); render(); scrollTo({ top: 0, behavior: "smooth" }); });
      syncUrl(); bindDetailLinks();
    };
    $("#c24Query").oninput = (event) => { query = event.target.value.trim(); page = 1; render(); };
    [["#c24Lifecycle", (value) => lifecycle = value], ["#c24Relationship", (value) => relationship = value], ["#c24State", (value) => state = value]].forEach(([selector, set]) => $(selector).onchange = (event) => { set(event.target.value); page = 1; render(); });
    render();
  }

  function renderChanges() {
    let chain = params.get("chain") || "all";
    let lifecycle = params.get("lifecycle") || "all";
    const render = () => {
      const changeUniverse = data.changes.filter((item) => lifecycle === "all" || item.lifecyclePool === lifecycle);
      const rows = changeUniverse.filter((item) => chain === "all" || item.chainId === chain);
      const lifecycleCounts = rows.reduce((counts, item) => ({ ...counts, [item.lifecyclePool]: (counts[item.lifecyclePool] || 0) + 1 }), {});
      main.innerHTML = head("会改变公开判断的事件", "重要变化", "这里只显示会改变状态、强路径、风险或可信度的变化；任务日志、接口错误和普通价格流水不会进入。") + `<section class="c24-section">${chainTabs(chain, true, changeUniverse)}<div class="c24-filters"><select id="c24ChangeLifecycle"><option value="all">两个生命周期池</option><option value="new_0_90">90 天内</option><option value="continued_91_plus">90 天后持续跟踪</option></select><span>${rows.length} 条变化</span></div>${rows.length ? `<div class="c24-chart-grid">${compositionChart("变化的生命周期构成", "只统计当前链与生命周期筛选下，会改变公开判断的变化。", [
        { label: "90 天内", value: lifecycleCounts.new_0_90 || 0, tone: "blue" },
        { label: "90 天后持续跟踪", value: lifecycleCounts.continued_91_plus || 0, tone: "orange" },
      ])}${chainDistributionChart(rows, "变化的六链分布")}</div><div class="c24-change-list">${rows.map((item) => `<article><div><span>${esc(data.chainLabels[item.chainId])}</span><time>${esc(formatTime(item.changedAt))}</time></div><h2>${esc(item.whatChanged)}</h2><p><strong>为什么影响判断：</strong>${esc(item.whyItMatters)}</p><p><strong>接下来观察：</strong>${esc(item.nextWatch)}</p><a href="${esc(item.detailHref)}" data-detail-link>查看项目依据 →</a></article>`).join("")}</div>` : empty("当前筛选条件下没有会改变公开判断的重要变化")}</section>`;
      $("#c24ChangeLifecycle").value = lifecycle;
      $("#c24ChangeLifecycle").onchange = (event) => { lifecycle = event.target.value; render(); };
      document.querySelectorAll("[data-chain-tab]").forEach((button) => button.onclick = () => { chain = button.dataset.chainTab; render(); });
      bindDetailLinks();
    };
    render();
  }

  function renderMethod() {
    main.innerHTML = head("程序边界公开", "我们如何判断", "程序分三步：发现候选、完成首轮基础跟踪、达到公开底线后发布；全池结构和历史供应在公开后继续增强。年龄只决定生命周期池，不代表质量。") + `
      ${methodFlow()}
      <section class="c24-method-grid">
        <article><span>生命周期</span><h2>90 天后不自动删除</h2><p>只有曾在 90 天内完成两关并达到公开底线的同一 assetId，才在第 91 天转入持续跟踪；不会借此发现普通老项目。</p></article>
        <article><span>公开状态</span><h2>三种状态互斥</h2><p>凸性线索需要至少两条独立强路径；活跃项目已有强路径或可核验活动；观察中已经完成检查，但当前结构证据仍不强。</p></article>
        <article><span>排序边界</span><h2>只做同链相对排名</h2><p>综合分只负责同链排序和变化解释，不定义凸性线索、不改变公开资格，也不自动调整规则。</p></article>
      </section>
      ${horizontalBars("贝叶斯五因子权重", "权重合计 100%；缺失指标退出该指标分母，不补默认分。", [
        { label: "交易需求", value: 25, displayValue: "25%", tone: "blue" },
        { label: "流动性与退出", value: 25, displayValue: "25%", tone: "blue" },
        { label: "供应结构", value: 20, displayValue: "20%", tone: "green" },
        { label: "活动与估值", value: 15, displayValue: "15%", tone: "gray" },
        { label: "稳定性与异常", value: 15, displayValue: "15%", tone: "gray" },
      ], { scaleMax: 25, axisNote: "本组最高权重为 25%" })}
      <details class="c24-advanced"><summary>查看 P50/P40、来源独立性和贝叶斯高级说明</summary><p>路径阈值优先使用同链同日龄的真实 30 天比较组；样本不足时依次扩大到六链同日龄，最后使用冻结历史样本。缺失指标退出该指标分母，不补 0。交易需求和流动性路径若复用同一市场来源，卖出报价必须独立，否则还需要供应或已索引池路径。</p></details>
      <section class="c24-boundary"><h2>程序最多只能做到这里</h2><p>程序只根据已接入来源返回且能够复算的数据形成研究线索。公开底线、路径、贝叶斯和排名都不证明项目安全、低估、值得买入或未来上涨；来源缺失时程序不会凭空补齐。</p></section>`;
  }

  function renderDetail() {
    const item = data.items.find((row) => row.assetId === params.get("assetId") || row.projectId === params.get("id"));
    let back = "candidate-pool.html?view=all";
    let savedReturn = null;
    try { savedReturn = JSON.parse(sessionStorage.getItem("c24-return") || "null"); if (savedReturn && savedReturn.url) back = savedReturn.url; } catch (_) {}
    const bindBack = () => {
      const link = $("[data-c24-back]");
      if (link && savedReturn) link.onclick = () => sessionStorage.setItem("c24-restore-scroll", JSON.stringify(savedReturn));
    };
    if (!item) { main.innerHTML = `<a class="c24-back" data-c24-back href="${esc(back)}">← 返回原页面</a>${head("项目详情", "当前完整公开快照中没有这个项目", "它可能已撤下公开展示；后台仍会保留真实历史和停止原因。")}`; bindBack(); return; }
    main.innerHTML = `<a class="c24-back" data-c24-back href="${esc(back)}">← 返回原页面</a><header class="c24-detail-head"><div><span class="c24-kicker">${esc(data.chainLabels[item.chainId])} · ${esc(lifecycleLabels[item.lifecyclePool])}</span><h1>${esc(item.canonicalName)} <small>${esc(item.symbol)}</small></h1><p>${esc(relationshipLabels[item.relationshipClass])} · 已公开流通第 ${esc(item.ageDays)} 天</p></div>${statusBadge(item)}</header>
      <section class="c24-detail-first"><article><span>为什么进入</span><h2>${esc(item.whyNow)}</h2></article><article><span>为什么是当前状态</span><h2>${esc(item.whyState)}</h2></article><article><span>什么会失效</span><h2>确认出现冻结、黑名单或无法卖出等硬交易阻断时会立即撤下；报价损失比例当前只记录</h2></article></section>
      <div class="c24-detail-grid"><div class="c24-detail-primary">
        <section class="c24-detail-panel"><h2>四条强证据路径</h2>${pathStatusMatrix(item.strongPaths)}${item.strongPaths.map((path) => `<article class="c24-path"><div><strong>${esc(pathLabels[path.pathCode])}</strong><span data-path-state="${esc(path.status)}">${esc(pathStatusLabels[path.status])}</span></div><p>${esc(path.plainReason)}</p><details><summary>查看本路径指标</summary>${renderPathMetrics(path.metrics)}</details></article>`).join("")}</section>
        <section class="c24-detail-panel"><h2>市场与退出</h2>${sellLossGauge(item.sellQuoteLossPct)}${horizontalBars("市场规模与交易方向", "金额和笔数分成两组比较，精确值仍列在下方。", [
          { label: "当前流动性", value: item.market.liquidityUsd || 0, displayValue: `${formatNumber(item.market.liquidityUsd)} USD`, tone: "green" },
          { label: "当前成交额", value: item.market.volumeUsd || 0, displayValue: `${formatNumber(item.market.volumeUsd)} USD`, tone: "blue" },
        ])}<dl class="c24-facts"><div><dt>买入 / 卖出</dt><dd>${esc(formatNumber(item.market.observedBuys, 0))} / ${esc(formatNumber(item.market.observedSells, 0))}</dd></div><div><dt>成交额 / 流动性</dt><dd>${esc(formatNumber(item.market.volumeLiquidityRatio, 4))}</dd></div></dl><p class="c24-note">100 美元报价不代表其他金额、未来时点或极端行情一定可以退出。</p></section>
      </div><aside>
        <section class="c24-detail-panel"><h2>项目证据边界</h2><p>${esc(item.projectEvidenceBoundary)}</p>${(item.projectEvidenceLinks || []).map((link) => `<a class="c24-external" href="${esc(link.url)}" target="_blank" rel="noreferrer">${esc(link.label || "打开外部证据")} ↗</a>`).join("")}</section>
        <section class="c24-detail-panel"><h2>同链相对排名</h2><p>${item.rankingAvailable ? `第 ${esc(item.bayesRankWithinChain)} 名 · 后验分 ${esc(formatNumber(item.bayesPosterior))}` : `尚无可复算排名；当前只有 ${esc(item.observedMetricCount)} 个有效实测指标`}</p>${factorScoreChart(item.bayesFactors)}<p class="c24-note">贝叶斯只用于同链排序；缺少实测指标时不用默认 50 分制造排名，也不决定公开资格或凸性线索。</p></section>
        <section class="c24-detail-panel"><h2>数据与证据</h2><p>截止 ${esc(formatTime(item.dataCutoffAt))}</p><p>比较组：${esc(cohortLabels[item.cohortScope] || "当前可比历史组")} · 样本 ${esc(item.cohortSampleSize)}</p><p>有效实测指标：${esc(item.observedMetricCount)}</p></section>
      </aside></div>`;
    bindBack();
  }

  ({ home: renderHome, all: renderAll, changes: renderChanges, method: renderMethod, detail: renderDetail }[mode] || renderHome)();
  restoreReturnScroll();
})();
