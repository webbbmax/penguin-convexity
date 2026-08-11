(function c20FrontExperience() {
  "use strict";

  const signals = window.PENGUIN_CONVEXITY_DECISION_SIGNALS;
  if (!signals || signals.schemaVersion !== "c2.0-decision-signals-v1") return;

  const detailsSnapshot = window.PENGUIN_CONVEXITY_PROJECT_DETAILS || {};
  const query = new URLSearchParams(window.location.search);
  const requestedMode = query.get("view");
  const mode = requestedMode || document.body.dataset.pageMode || "home";
  const pageSize = 20;
  const stateKey = `penguin.convexity.c2.${mode}`;
  const frontMain = document.querySelector(".c19-front-main, .c19-detail-main");
  if (!frontMain) return;

  const esc = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const safe = (value, fallback = "资料待补充") => {
    const result = String(value == null ? "" : value).trim();
    return result || fallback;
  };
  const number = (value) => Number(value || 0).toLocaleString("zh-CN");
  const dateTime = (value, fallback = "时间待补充") => {
    if (!value) return fallback;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? safe(value, fallback) : date.toLocaleString("zh-CN", { hour12: false });
  };
  const tierLabel = (value) => ({ must_read: "现在必须看", worth_following: "值得继续看", observe: "保持观察" }[value] || safe(value));
  const decisionValue = (value) => ({
    unknown: "待核验",
    verified: "已核验",
    limited: "受限",
    blocked: "阻断",
    untradeable: "不可交易",
  }[String(value == null ? "" : value).trim().toLowerCase()] || safe(value, "待核验"));
  const actionClass = (value) => {
    const action = safe(value, "只观察");
    if (action === "普通建仓") return "good";
    if (action === "极限试仓") return "warn";
    if (action === "失效/排除") return "bad";
    return "";
  };
  const impactClass = (value) => value === "improve" ? "good" : value === "tighten" ? "bad" : "";
  const projectById = new Map((signals.projects || []).map((item) => [item.projectId, item]));
  const changes = Array.isArray(signals.changeChains) ? signals.changeChains : [];

  function readState() {
    try { return JSON.parse(localStorage.getItem(stateKey) || "{}") || {}; } catch (_error) { return {}; }
  }
  function writeState(patch) {
    try { localStorage.setItem(stateKey, JSON.stringify({ ...readState(), ...patch })); } catch (_error) { /* optional preference */ }
  }
  function detailUrl(item, page = 1) {
    const url = safe(item.detailUrl, `project-detail.html?id=${encodeURIComponent(item.projectId || "")}`);
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}from=${encodeURIComponent(mode)}&c2Page=${page}`;
  }
  function restoreScroll() {
    const state = readState();
    if (Number(state.scrollTop) > 0) window.setTimeout(() => window.scrollTo(0, Number(state.scrollTop)), 0);
  }
  function setNav() {
    document.querySelectorAll(".c19-front-nav a[data-front-mode]").forEach((link) => {
      link.classList.toggle("is-active", link.dataset.frontMode === mode);
    });
  }
  function noticeMarkup(className = "") {
    const status = signals.dataStatus || {};
    const message = safe(status.message, "判断质量快照正在准备。页面只展示最近一次有效判断。");
    const normal = ["valid", "success", "ready"].includes(status.state);
    return `<div class="c2-data-notice ${className}" role="status" ${normal ? "hidden" : ""}><strong>${esc(safe(status.label, "判断质量状态"))}</strong><br>${esc(message)}<br><small>判断时间：${esc(dateTime(signals.generatedAt))}；来源快照：${esc(dateTime(signals.sourceSnapshotAt))}</small></div>`;
  }
  async function checkRuntimeStatus() {
    try {
      const response = await fetch("/api/c2.0/status", { cache: "no-store" });
      if (!response.ok) throw new Error("status");
      const status = await response.json();
      if (status.state && !["success", "valid", "ready"].includes(status.state)) {
        const node = document.querySelector(".c2-data-notice");
        if (node) { node.hidden = false; node.classList.add("is-error"); node.innerHTML = `<strong>判断质量快照状态：${esc(safe(status.state))}</strong><br>${esc(safe(status.error || status.message, "当前继续展示最近一次有效快照。"))}`; }
      }
    } catch (_error) {
      // Static file use remains valid when the optional local status endpoint is unavailable.
    }
  }
  function renderSignalCard(item) {
    const summary = item.summary || {};
    const incomplete = item.summaryComplete === false;
    const missing = Array.isArray(item.missingSummaryParts) ? item.missingSummaryParts : [];
    return `<article class="c2-signal-card c2-card-clickable" data-detail-card data-detail-href="${esc(detailUrl(item))}" tabindex="0">
        <div class="c2-signal-head"><h3>${esc(safe(item.projectName, "未命名项目"))}${item.symbol ? ` <small>${esc(item.symbol)}</small>` : ""}</h3><span class="c2-badge">${esc(tierLabel(item.readingTier))}</span></div>
        <p>${esc(safe(item.whyPriority, "当前资料仍在积累，暂不改变动作。"))}</p>
        ${item.impactLabel ? `<p class="c2-impact-line ${impactClass(item.impact)}"><strong>${esc(item.impactLabel)}</strong> · 当前动作 ${esc(safe(item.actionLabel, "只观察"))}</p>` : `<p class="c2-impact-line"><strong>当前动作</strong> · ${esc(safe(item.actionLabel, "只观察"))}</p>`}
        <p><strong>首要阻断：</strong>${esc(safe(summary.primaryBlocker?.text, "尚未形成可核验的项目专属阻断。"))}</p>
        <p><strong>下一触发：</strong>${esc(safe(summary.nextTrigger?.text, "系统将继续检查下一项可核验事实。"))}</p>
        ${incomplete ? `<p class="c2-empty"><strong>资料不足，暂不进入优先展示。</strong>当前缺少：${esc(missing.join("、") || "项目专属摘要")}</p>` : ""}
        <small>${esc(safe(item.maturity, "生命周期待核验"))} · 证据时间 ${esc(dateTime(item.evidenceTime))}</small>
        <details class="c2-sort-reasons"><summary>为什么排在这里</summary><ul>${(item.sortReasons || []).map((reason) => `<li>${esc(reason)}</li>`).join("") || "<li>当前没有额外排序理由。</li>"}</ul></details>
        <a class="c2-card-link" href="${esc(detailUrl(item))}" data-detail-link>查看项目上下文 →</a>
      </article>`;
  }
  function renderChangeCard(item) {
    const steps = Array.isArray(item.steps) ? item.steps : [];
    const evidence = Array.isArray(item.evidence) ? item.evidence.slice(0, 4) : [];
    const numberLabels = { priceUsd: "价格", marketCapUsd: "市值", fdvUsd: "FDV", liquidityUsd: "流动性", volume24hUsd: "24 小时成交额", exitNotionalUsd: "退出金额", priceChange24hPct: "24 小时价格变化", priceChange7dPct: "7 天价格变化", estimatedExitSlippagePct: "预计退出滑点", modeledExitSlippagePct: "模拟退出滑点" };
    const displayNumbers = Array.isArray(item.displayNumbers) ? item.displayNumbers : [];
    const safeNumericSummary = displayNumbers.slice(0, 3).map((entry) => `${numberLabels[entry.field] || "监测指标"} ${safe(entry.fromText, "待核验")} → ${safe(entry.toText, "待核验")}（${safe(entry.unit, "口径待核验")}，${safe(entry.comparison, "与上一轮检查相比")}，数据时间 ${dateTime(entry.observedAt)}）`).join("；") + (displayNumbers.length > 3 ? `；其余 ${displayNumbers.length - 3} 项见完整变化链` : "");
    const headline = safe(item.headline, "项目状态发生变化");
    const why = safe(item.whyItMatters, "这次变化尚不改变当前判断。");
    return `<article class="c2-change-card">
      <div class="c2-change-meta"><span class="c2-badge ${impactClass(item.impact)}">${esc(safe(item.impactLabel))}</span><span>${esc(safe(item.projectName, "未命名项目"))}</span><span>${esc(dateTime(item.endedAt))}</span></div>
      <h3>${esc(headline)}</h3>
      <p>${esc(why)}</p>
      ${safeNumericSummary ? `<p class="c2-numeric-context"><strong>相关数据：</strong>${esc(safeNumericSummary)}</p>` : ""}
      <details class="c2-timeline"><summary>展开完整变化链（${steps.length} 步）</summary>${steps.map((step) => `<div><p><strong>${esc(safe(step.dimensionLabel, "判断维度"))}</strong>${step.semanticField === "market" || displayNumbers.length ? "" : `：${esc(safe(step.fromLabel, "待核验"))} → ${esc(safe(step.toLabel, "待核验"))}`}<br>${esc(safe(step.explanation, "变化已记录，仍需继续观察。"))}<br><small>${esc(dateTime(step.observedAt))}</small></p></div>`).join("") || "<p>尚无中间步骤。</p>"}</details>
      ${evidence.length ? `<ul class="c2-evidence-list">${evidence.map((entry) => `<li>${esc(safe(entry.sourceName, "来源待核验"))}：${esc(safe(entry.summary, "证据摘要待补充"))}（${esc(dateTime(entry.collectedAt))}）</li>`).join("")}</ul>` : ""}
      <a class="c2-card-link" href="${esc(detailUrl(item))}" data-detail-link>查看项目上下文 →</a>
    </article>`;
  }
  function renderHome() {
    const counts = signals.currentDecision?.actionCounts || {};
    const homeSignals = (signals.homeSignals || []).slice(0, 5);
    const recent = changes.filter((item) => item.impact !== "no_change").slice(0, 3);
    const blockers = (signals.topBlockers || []).slice(0, 3);
    frontMain.className = "c2-front-main";
    frontMain.innerHTML = `${noticeMarkup()}
      <section class="c2-hero" aria-labelledby="c2DecisionTitle"><article class="c2-hero-copy"><span class="c2-kicker">当前结论</span><h1 id="c2DecisionTitle">${esc(safe(signals.currentDecision?.headline, "正在读取当前判断"))}</h1><p>阅读顺序只决定先看什么。当前动作仍由事实、风险、交易与退出条件共同决定。</p><div class="c2-summary-strip">${Object.entries(counts).map(([label, value]) => `<span class="c2-count"><strong>${number(value)}</strong>${esc(label)}</span>`).join("")}</div></article><aside class="c2-hero-meta"><dl><div><dt>项目数量</dt><dd>${number(signals.counts?.projects)}</dd></div><div><dt>判断时间</dt><dd>${esc(dateTime(signals.currentDecision?.asOf))}</dd></div><div><dt>首页信号</dt><dd>${number(signals.counts?.homeSignals)} / 5</dd></div><div><dt>来源快照</dt><dd>${esc(dateTime(signals.sourceSnapshotAt))}</dd></div></dl></aside></section>
      <section class="c2-section" aria-labelledby="c2HomeSignals"><div class="c2-section-heading"><div><span class="c2-kicker">优先阅读</span><h2 id="c2HomeSignals">现在值得先看的机会</h2></div><a href="candidate-pool.html?view=all">查看全部机会 →</a></div>${homeSignals.length ? `<div class="c2-signal-grid">${homeSignals.map(renderSignalCard).join("")}</div>` : `<div class="c2-empty"><strong>本期没有需要优先阅读的新机会。</strong>当前行动门槛保持不变，系统会按计划继续检查。</div>`}</section>
      <section class="c2-section" aria-labelledby="c2Blockers"><div class="c2-section-heading"><div><span class="c2-kicker">当前限制</span><h2 id="c2Blockers">为什么现在不能直接行动</h2></div><p>限制项是阅读上下文，不会自动转化为补录任务或投资动作。</p></div><div class="c2-card-grid">${blockers.map((item) => `<article class="c2-signal-card"><div class="c2-signal-head"><h3>${esc(safe(item.category, "判断限制"))}</h3><span class="c2-badge warn">${number(item.projectCount)} 个项目</span></div><p>${esc(safe(item.text))}</p></article>`).join("") || `<div class="c2-empty">当前没有可归纳的限制项。</div>`}</div></section>
      <section class="c2-section" aria-labelledby="c2Changes"><div class="c2-section-heading"><div><span class="c2-kicker">最近变化</span><h2 id="c2Changes">可能改变判断的变化</h2></div><a href="change-explanations.html">查看全部变化 →</a></div>${recent.length ? `<div class="c2-card-grid c2-two">${recent.map(renderChangeCard).join("")}</div>` : `<div class="c2-empty"><strong>最近没有需要优先阅读的实质变化。</strong>横向市场变化不会自动等同于行动信号。</div>`}</section>`;
  }
  function renderAll() {
    const saved = readState();
    const state = { search: saved.search || "", action: saved.action || "all", tier: saved.tier || "all", maturity: saved.maturity || "all", page: Number(saved.page) || 1 };
    const actions = [...new Set((signals.projects || []).map((item) => item.actionLabel).filter(Boolean))];
    const maturities = [...new Set((signals.projects || []).map((item) => item.maturity).filter(Boolean))].sort();
    const draw = () => {
      const queryText = state.search.trim().toLowerCase();
      let list = (signals.projects || []).filter((item) => {
        const haystack = JSON.stringify(item).toLowerCase();
        return (!queryText || haystack.includes(queryText)) && (state.action === "all" || item.actionLabel === state.action) && (state.tier === "all" || item.readingTier === state.tier) && (state.maturity === "all" || item.maturity === state.maturity);
      });
      const pages = Math.max(1, Math.ceil(list.length / pageSize)); state.page = Math.min(state.page, pages);
      const pageItems = list.slice((state.page - 1) * pageSize, state.page * pageSize);
      const root = document.getElementById("c2AllList");
      root.innerHTML = pageItems.length ? pageItems.map(renderSignalCard).join("") : `<div class="c2-empty"><strong>没有符合当前筛选的机会。</strong>请放宽筛选条件，或等待下一轮检查。</div>`;
      document.getElementById("c2AllCount").textContent = `${number(list.length)} 个项目`;
      document.getElementById("c2AllPage").textContent = `第 ${state.page} / ${pages} 页`;
      document.getElementById("c2AllPrev").disabled = state.page <= 1; document.getElementById("c2AllNext").disabled = state.page >= pages;
      writeState(state);
    };
    frontMain.className = "c2-front-main";
    frontMain.innerHTML = `${noticeMarkup()}<section class="c2-section" aria-labelledby="c2AllTitle"><div class="c2-section-heading"><div><span class="c2-kicker">机会目录</span><h1 id="c2AllTitle" class="c19-page-title">全部机会</h1></div><p>按阅读优先级整理，不替代当前动作、评分、仓位或 L0-L5。</p></div><div class="c2-panel"><div class="c2-toolbar"><input id="c2AllSearch" type="search" autocomplete="off" placeholder="搜索项目、事实或限制" value="${esc(state.search)}"><select id="c2AllAction"><option value="all">全部动作</option>${actions.map((item) => `<option value="${esc(item)}" ${state.action === item ? "selected" : ""}>${esc(item)}</option>`).join("")}</select><select id="c2AllTier"><option value="all">全部阅读层级</option><option value="must_read" ${state.tier === "must_read" ? "selected" : ""}>现在必须看</option><option value="worth_following" ${state.tier === "worth_following" ? "selected" : ""}>值得继续看</option><option value="observe" ${state.tier === "observe" ? "selected" : ""}>保持观察</option></select><select id="c2AllMaturity"><option value="all">全部生命周期</option>${maturities.map((item) => `<option value="${esc(item)}" ${state.maturity === item ? "selected" : ""}>${esc(item)}</option>`).join("")}</select><span id="c2AllCount" class="c2-result-count"></span></div><div id="c2AllList" class="c2-card-grid"></div><div class="c2-pagination"><button id="c2AllPrev" type="button">上一页</button><span id="c2AllPage"></span><button id="c2AllNext" type="button">下一页</button></div></div></section>`;
    const update = () => { state.search = document.getElementById("c2AllSearch").value; state.action = document.getElementById("c2AllAction").value; state.tier = document.getElementById("c2AllTier").value; state.maturity = document.getElementById("c2AllMaturity").value; state.page = 1; draw(); };
    ["c2AllSearch", "c2AllAction", "c2AllTier", "c2AllMaturity"].forEach((id) => document.getElementById(id).addEventListener("input", update));
    document.getElementById("c2AllPrev").addEventListener("click", () => { state.page -= 1; draw(); }); document.getElementById("c2AllNext").addEventListener("click", () => { state.page += 1; draw(); });
    window.addEventListener("pageshow", () => {
      document.getElementById("c2AllSearch").value = state.search;
      document.getElementById("c2AllAction").value = state.action;
      document.getElementById("c2AllTier").value = state.tier;
      document.getElementById("c2AllMaturity").value = state.maturity;
      draw();
    }, { once: true });
    draw(); restoreScroll();
    window.setTimeout(() => {
      const search = document.getElementById("c2AllSearch");
      if (search && search.value !== state.search) search.value = state.search;
    }, 150);
  }
  function renderChanges() {
    const saved = readState();
    const state = { search: saved.search || "", time: saved.time || "7d", impact: saved.impact || "important", page: Number(saved.page) || 1 };
    const ageMs = { "24h": 86400000, "7d": 604800000, "30d": 2592000000 };
    const draw = () => {
      const cutoff = Date.now() - (ageMs[state.time] || ageMs["7d"]);
      const textQuery = state.search.trim().toLowerCase();
      let list = changes.filter((item) => (state.impact === "all" || item.impact === "improve" || item.impact === "tighten") && (!textQuery || JSON.stringify(item).toLowerCase().includes(textQuery)) && (!item.endedAt || new Date(item.endedAt).getTime() >= cutoff));
      const pages = Math.max(1, Math.ceil(list.length / pageSize)); state.page = Math.min(state.page, pages);
      const pageItems = list.slice((state.page - 1) * pageSize, state.page * pageSize);
      document.getElementById("c2ChangesList").innerHTML = pageItems.length ? pageItems.map(renderChangeCard).join("") : `<div class="c2-empty"><strong>当前筛选下没有可能改变判断的变化。</strong>“尚不改变判断”的横向变化默认隐藏。</div>`;
      document.getElementById("c2ChangesCount").textContent = `${number(list.length)} 条变化`;
      document.getElementById("c2ChangesPage").textContent = `第 ${state.page} / ${pages} 页`;
      document.getElementById("c2ChangesPrev").disabled = state.page <= 1; document.getElementById("c2ChangesNext").disabled = state.page >= pages;
      writeState(state);
    };
    frontMain.className = "c2-front-main";
    frontMain.innerHTML = `${noticeMarkup()}<section class="c2-section" aria-labelledby="c2ChangesTitle"><div class="c2-section-heading"><div><span class="c2-kicker">判断变化</span><h1 id="c2ChangesTitle" class="c19-page-title">重要变化</h1></div><p>默认只显示可能改善或可能收紧判断的变化；“尚不改变判断”可按需展开。</p></div><div class="c2-panel"><div class="c2-toolbar"><input id="c2ChangesSearch" type="search" autocomplete="off" placeholder="搜索项目、变化或证据" value="${esc(state.search)}"><select id="c2ChangesTime"><option value="24h" ${state.time === "24h" ? "selected" : ""}>最近 24 小时</option><option value="7d" ${state.time === "7d" ? "selected" : ""}>最近 7 天</option><option value="30d" ${state.time === "30d" ? "selected" : ""}>最近 30 天</option></select><select id="c2ChangesImpact"><option value="important" ${state.impact === "important" ? "selected" : ""}>可能改变判断</option><option value="all" ${state.impact === "all" ? "selected" : ""}>全部变化</option></select><span id="c2ChangesCount" class="c2-result-count"></span></div><div id="c2ChangesList" class="c2-card-grid c2-two"></div><div class="c2-pagination"><button id="c2ChangesPrev" type="button">上一页</button><span id="c2ChangesPage"></span><button id="c2ChangesNext" type="button">下一页</button></div></div></section>`;
    const update = () => { state.search = document.getElementById("c2ChangesSearch").value; state.time = document.getElementById("c2ChangesTime").value; state.impact = document.getElementById("c2ChangesImpact").value; state.page = 1; draw(); };
    ["c2ChangesSearch", "c2ChangesTime", "c2ChangesImpact"].forEach((id) => document.getElementById(id).addEventListener("input", update));
    document.getElementById("c2ChangesPrev").addEventListener("click", () => { state.page -= 1; draw(); }); document.getElementById("c2ChangesNext").addEventListener("click", () => { state.page += 1; draw(); });
    window.addEventListener("pageshow", () => {
      document.getElementById("c2ChangesSearch").value = state.search;
      document.getElementById("c2ChangesTime").value = state.time;
      document.getElementById("c2ChangesImpact").value = state.impact;
      draw();
    }, { once: true });
    draw(); restoreScroll();
    window.setTimeout(() => {
      const search = document.getElementById("c2ChangesSearch");
      if (search && search.value !== state.search) search.value = state.search;
    }, 150);
  }
  function renderMethod() {
    frontMain.className = "c2-front-main";
    const tiers = signals.methodBoundary?.readingTiers || ["现在必须看", "值得继续看", "保持观察"];
    frontMain.innerHTML = `${noticeMarkup()}<section class="c2-section" aria-labelledby="c2MethodTitle"><div class="c2-section-heading"><div><span class="c2-kicker">判断方法</span><h1 id="c2MethodTitle" class="c19-page-title">我们如何判断</h1></div></div><div class="c2-card-grid"><article class="c2-signal-card"><span class="c2-badge good">${esc(tiers[0])}</span><h3>先看是否已经足以改变行动</h3><p>只有事实、风险、交易与退出条件形成可回溯闭环，才会进入首页优先阅读。</p></article><article class="c2-signal-card"><span class="c2-badge">${esc(tiers[1])}</span><h3>继续看是否正在接近闭环</h3><p>存在具体触发条件或可验证变化，但当前仍不足以替代原有动作判断。</p></article><article class="c2-signal-card"><span class="c2-badge warn">${esc(tiers[2])}</span><h3>保持观察并明确缺口</h3><p>资料不完整、主体或资产关系待核验时，保留上下文而不把故事写成行动信号。</p></article></div><div class="c2-panel" style="margin-top:16px"><h2>边界</h2><p>${esc(safe(signals.methodBoundary?.statement))}</p><p>前台只展示判断、机会、变化、风险、催化和数据时间；维护、来源、调度与异常处理留在工作台。</p></div></section>`;
  }
  function detailRecord(projectId) {
    const key = `project:${projectId}`;
    return detailsSnapshot.records?.[key] || detailsSnapshot.records?.[projectId] || null;
  }
  function renderDetail() {
    const rawId = safe(query.get("id"));
    const projectId = decodeURIComponent(rawId.replace(/^project%3A/i, "").replace(/^project:/i, ""));
    const signal = projectById.get(projectId);
    const record = detailRecord(projectId);
    const caseRecord = record?.cases?.find((item) => item.case_id === signal?.caseId) || record?.cases?.[0] || {};
    const summary = signal?.summary || {};
    const from = query.get("from") || "all";
    const backHref = from === "home" ? "candidate-pool.html" : from === "changes" ? "change-explanations.html" : "candidate-pool.html?view=all";
    frontMain.className = "c2-front-main";
    if (!signal) { frontMain.innerHTML = `${noticeMarkup()}<div class="c2-empty"><strong>没有找到这个项目。</strong><a href="${backHref}">返回上一页</a></div>`; return; }
    const chain = changes.filter((item) => item.projectId === projectId).slice(0, 4);
    const support = summary.strongestSupport || {};
    const blocker = summary.primaryBlocker || {};
    const trigger = summary.nextTrigger || {};
    const invalidation = summary.invalidation || {};
    const review = record?.cases?.[0]?.convexityReview || {};
    const evidenceSources = [support.sourceName, support.sourceUrl, ...(record?.master?.sourceUrls || [])].filter(Boolean).slice(0, 8);
    frontMain.innerHTML = `${noticeMarkup()}<a class="c19-detail-back" href="${backHref}">← 返回${from === "changes" ? "重要变化" : from === "home" ? "机会首页" : "全部机会"}</a><header class="c2-section-heading" style="margin-top:18px"><div><span class="c2-kicker">项目上下文</span><h1 class="c19-page-title">${esc(safe(signal.projectName, "未命名项目"))}</h1><p style="text-align:left">${esc(safe(signal.symbol, ""))} · 证据时间 ${esc(dateTime(signal.evidenceTime))}</p></div><span class="c2-badge ${actionClass(signal.actionLabel)}">${esc(safe(signal.actionLabel, "只观察"))}</span></header><div class="c2-detail-summary"><div class="c2-detail-action"><span>当前动作</span><strong>${esc(safe(summary.action?.label, signal.actionLabel))}</strong><small>动作直接读取现有判断，不由阅读顺序重新推导。</small></div><div class="c2-detail-summary-grid"><details open><summary>当前最强支持事实</summary><p>${esc(safe(support.text))}</p></details><details open><summary>当前最大阻断或最大风险</summary><p>${esc(safe(blocker.text))}</p></details><details open><summary>下一触发条件</summary><p>${esc(safe(trigger.text))}</p></details><details open><summary>失效条件与证据时间</summary><p>${esc(safe(invalidation.text))}<br>${esc(dateTime(invalidation.evidenceTime || signal.evidenceTime))}</p></details></div></div><div class="c2-detail-body"><article class="c2-panel"><h2>判断依据</h2><p>${esc(safe(signal.whyPriority, "当前没有新的优先级解释。"))}</p><dl class="c2-detail-facts"><div><dt>生命周期</dt><dd>${esc(safe(signal.maturity, "待核验"))}</dd></div><div><dt>风险</dt><dd>${esc(decisionValue(signal.riskLevel))}</dd></div><div><dt>剩余凸性</dt><dd>${esc(decisionValue(signal.remainingConvexity))}</dd></div><div><dt>交易性</dt><dd>${esc(decisionValue(signal.tradeabilityStatus))}</dd></div><div><dt>点火距离</dt><dd>${esc(decisionValue(signal.ignitionProximity))}</dd></div><div><dt>凸性来源</dt><dd>${esc(safe(signal.convexitySource, "尚未形成可核验主凸性来源"))}</dd></div></dl><h2 style="margin-top:20px">催化与失效</h2><p><strong>催化：</strong>${esc(safe(review.ignition_conditions || trigger.text))}</p><p><strong>失效：</strong>${esc(safe(review.invalidation_window || invalidation.text))}</p></article><aside class="c2-panel"><h2>证据与数据时间</h2><ul class="c2-evidence-list">${evidenceSources.map((item) => `<li>${esc(item)}</li>`).join("") || "<li>来源名称或链接待补充。</li>"}</ul><p class="c2-quality-note">来源快照：${esc(dateTime(signals.sourceSnapshotAt))}<br>项目证据：${esc(dateTime(signal.evidenceTime))}</p></aside></div>${chain.length ? `<section class="c2-section"><div class="c2-section-heading"><div><span class="c2-kicker">最近变化</span><h2>这个项目最近发生了什么</h2></div></div><div class="c2-card-grid c2-two">${chain.map(renderChangeCard).join("")}</div></section>` : ""}`;
    if (signal.summaryComplete === false) {
      const incompleteNotice = document.createElement("div");
      incompleteNotice.className = "c2-data-notice";
      incompleteNotice.innerHTML = `<strong>资料不足，暂不进入优先展示。</strong><br>当前缺少：${esc((signal.missingSummaryParts || []).join("、") || "项目专属摘要")}。下次复查时间：${esc(dateTime(signal.nextReviewAt))}`;
      const summaryNode = frontMain.querySelector(".c2-detail-summary");
      if (summaryNode) frontMain.insertBefore(incompleteNotice, summaryNode);
    }
    writeState({ page: Number(query.get("c2Page")) || 1 });
  }
  function wireDetailReturn() {
    if (frontMain.dataset.c2DetailWired === "true") return;
    frontMain.dataset.c2DetailWired = "true";
    const navigate = (event, card) => {
      if (!card || event.target.closest("details") || event.target.closest("a")) return;
      writeState({ scrollTop: window.scrollY || 0 });
      window.location.href = card.dataset.detailHref;
    };
    frontMain.addEventListener("click", (event) => {
      if (event.target.closest("[data-detail-link]")) writeState({ scrollTop: window.scrollY || 0 });
      navigate(event, event.target.closest("[data-detail-card]"));
    });
    frontMain.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const card = event.target.closest("[data-detail-card]");
      if (!card || event.target.closest("details") || event.target.closest("a")) return;
      event.preventDefault();
      navigate(event, card);
    });
  }
  function render() {
    setNav();
    if (mode === "detail" || document.body.classList.contains("c19-detail")) renderDetail();
    else if (mode === "all") renderAll();
    else if (mode === "changes") renderChanges();
    else if (mode === "method") renderMethod();
    else renderHome();
    wireDetailReturn();
    checkRuntimeStatus();
  }
  render();
})();
