(function c19FrontExperience() {
  "use strict";

  const center = window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER || {};
  const candidates = window.PENGUIN_CONVEXITY_CANDIDATES || {};
  const changes = window.PENGUIN_CONVEXITY_CHANGE_EXPLANATIONS || {};
  const details = window.PENGUIN_CONVEXITY_PROJECT_DETAILS || {};
  const routes = window.PENGUIN_CONVEXITY_RESEARCH_ROUTES || {};
  const tracking = window.PENGUIN_CONVEXITY_TRACKING_TASKS || {};
  const queryMode = new URLSearchParams(window.location.search).get("view");
  const mode = queryMode || document.body.dataset.pageMode || "home";
  const pageSize = 20;
  const storageKey = `penguin.convexity.c19.${mode}`;
  const byId = (id) => document.getElementById(id);
  const text = (value, fallback = "") => String(value == null || value === "" ? fallback : value);
  const number = (value) => Number(value || 0).toLocaleString("zh-CN");
  const escapeHtml = (value) => text(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const readableNumber = (value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return text(value);
    const absolute = Math.abs(numeric);
    const compact = (divisor, unit) => `${(numeric / divisor).toFixed(1).replace(/\.0$/, "")}${unit}`;
    if (absolute >= 100000000) return compact(100000000, "亿");
    if (absolute >= 10000) return compact(10000, "万");
    return numeric.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  };
  const friendlyCopy = (value, fallback = "") => text(value, fallback)
    .replaceAll("规则重算", "系统重新评估后")
    .replaceAll("machine_rule", "系统规则")
    .replaceAll("待核验", "尚未确认")
    .replaceAll("待闭环", "尚未形成完整依据")
    .replaceAll("待发现", "暂未发现")
    .replaceAll("尚未自动执行", "系统将在下次检查时继续确认")
    .replace(/-?\d+(?:\.\d+)?e[+-]?\d+/gi, (matched) => readableNumber(matched));
  const friendlyRisk = (value) => {
    const risk = text(value).trim();
    const labels = {
      unknown: "尚未确认",
      pending: "尚未确认",
      low: "较低",
      medium: "中等",
      high: "较高",
      blocked: "存在阻断风险",
      critical: "存在严重风险",
    };
    return labels[risk.toLowerCase()] || friendlyCopy(risk, "风险与交易条件仍需继续确认。");
  };
  const dateTime = (value) => {
    if (!value) return "时间待补充";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? text(value) : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const relativeTime = (value) => {
    if (!value) return "时间待补充";
    const parsed = new Date(value).getTime();
    if (!Number.isFinite(parsed)) return text(value);
    const hours = Math.max(0, Math.round((Date.now() - parsed) / 3600000));
    if (hours < 1) return "刚刚";
    if (hours < 24) return `${hours} 小时前`;
    return `${Math.floor(hours / 24)} 天前`;
  };
  const actionClass = (value) => {
    const action = text(value);
    if (action.includes("普通")) return "good";
    if (action.includes("极限")) return "warn";
    if (action.includes("失效") || action.includes("排除")) return "bad";
    return "";
  };
  const records = Array.isArray(center.cases) && center.cases.length
    ? center.cases
    : (candidates.cases || []);
  const routeByCase = new Map((routes.records || []).filter((item) => item.caseId).map((item) => [item.caseId, item]));
  const trackingByCase = new Map((tracking.tasks || []).filter((item) => item.caseId).map((item) => [item.caseId, item]));
  const changeItems = Array.isArray(changes.recent7d) ? changes.recent7d : [];

  function readListState() {
    return readModeState(mode);
  }

  function readModeState(targetMode) {
    try {
      return JSON.parse(localStorage.getItem(`penguin.convexity.c19.${targetMode}`) || "{}") || {};
    } catch (_error) {
      return {};
    }
  }

  function writeListState(updates = {}) {
    try {
      localStorage.setItem(storageKey, JSON.stringify({ ...readListState(), ...updates }));
    } catch (_error) {
      // Preferences are optional; the page remains usable when storage is blocked.
    }
  }

  function detailContextUrl(url) {
    const separator = String(url).includes("?") ? "&" : "?";
    return `${url}${separator}from=${encodeURIComponent(mode)}`;
  }

  function saveListPosition() {
    if (mode === "home") return;
    writeListState({ scrollTop: window.scrollY || 0 });
  }

  function restoreListPosition() {
    const state = readListState();
    if (Number(state.scrollTop) > 0) {
      window.setTimeout(() => window.scrollTo(0, Number(state.scrollTop)), 0);
    }
  }

  function normalizedCase(item) {
    const conclusion = item.machineConclusion || item.conclusion || {};
    const catalyst = item.catalystTradePath || item.catalyst || {};
    const trackingItem = trackingByCase.get(item.caseId) || {};
    const route = routeByCase.get(item.caseId) || {};
    const stage = item.opportunityStage || {};
    const action = text(item.currentAction || conclusion.currentAction || stage.finalActionLabel, "只观察");
    const projectName = text(item.projectName || item.name || conclusion.projectName, "未命名项目");
    const invalidationConditions = stage.invalidationConditions?.length
      ? stage.invalidationConditions
      : [item.invalidation, item.invalidationWindow].filter(Boolean);
    return {
      ...item,
      projectName,
      symbol: text(item.symbol || item.tokenSymbol),
      action,
      actionClass: actionClass(action),
      reason: friendlyCopy(item.currentConclusion || conclusion.currentConclusion || conclusion.reason || stage.finalActionReason || stage.stageReason, "事实仍在积累，暂未形成完整行动闭环。"),
      risk: friendlyRisk(item.riskSummary || conclusion.primaryBlocker || item.riskLevel || item.contractRisk),
      changedAt: item.latestChangeAt || item.updatedAt || conclusion.updatedAt || changes.generatedAt,
      detailUrl: text(item.detailUrl, `project-detail.html?id=${encodeURIComponent(item.projectId || item.caseId || "")}`),
      routeLabel: text(item.projectCategoryLabel || item.routeLabel || route.routeLabel, "潜力项目"),
      routeReason: friendlyCopy(item.projectCategoryReason || route.routeReason, "按生命周期证据安排研究顺序。"),
      catalyst: friendlyCopy(catalyst.summary || catalyst.catalyst || catalyst.currentStageLabel, "催化条件尚未形成完整闭环。"),
      invalidationConditions: invalidationConditions.map((value) => friendlyCopy(value)).filter(Boolean),
      nextReviewAt: trackingItem.nextReviewAt || trackingItem.next_review_at || item.nextReviewAt,
      sourceTime: item.publicationUpdatedAt || item.latestEvidenceAt || center.generatedAt,
    };
  }

  function navActive() {
    document.querySelectorAll(".c19-front-nav a[data-front-mode]").forEach((link) => {
      link.classList.toggle("is-active", link.dataset.frontMode === mode);
    });
  }

  function setNotice(status) {
    const notice = byId("c19DataNotice");
    if (!notice) return;
    if (!status || status === "success" || status === "ready") {
      notice.hidden = true;
      return;
    }
    notice.hidden = false;
    const copy = status === "partial_success"
      ? ["部分数据本轮未完成", "已成功的数据仍然保留，当前页面继续显示上次有效判断；系统会按计划处理未完成部分。"]
      : status === "failed"
        ? ["本轮检查未完成", "当前页面继续显示上次有效判断；已成功的数据不会丢失，系统会在后台保留恢复入口。"]
        : status === "running"
          ? ["系统正在检查新数据", "当前页面继续显示上次有效判断，检查完成后会自动刷新；无需手动操作。"]
          : ["数据状态提示", "当前判断可能不是最新结果，系统完成检查后会自动刷新。"];
    notice.innerHTML = `<strong>${copy[0]}</strong><p>${copy[1]}</p>`;
  }

  function renderHome() {
    const conclusion = center.conclusionBoard || {};
    const actionCounts = center.actionCounts || {};
    const refresh = center.latestRefresh || {};
    const blockers = (center.c18?.blockerDetails || []).filter((item) => item && !item.isGroup).slice(0, 3);
    const near = (center.c18?.nearAction || []).slice(0, 6).map(normalizedCase);
    const recent = [];
    const seen = new Set();
    for (const item of changeItems) {
      const key = item.case_id || item.caseId || item.projectName || item.title;
      if (seen.has(key)) continue;
      seen.add(key);
      recent.push(item);
      if (recent.length >= 5) break;
    }
    byId("c19Conclusion").textContent = text(conclusion.headline, "当前判断正在生成");
    byId("c19ConclusionNote").textContent = text(conclusion.note, "系统会把事实、风险、交易条件和催化路径分开核验，再给出当前动作。");
    byId("c19AsOf").textContent = dateTime(center.generatedAt);
    byId("c19ActionSummary").innerHTML = [
      ["普通建仓", actionCounts.ordinary],
      ["极限试仓", actionCounts.extreme],
      ["只观察", actionCounts.observe],
      ["反身性管理", actionCounts.reflexive],
      ["失效/排除", actionCounts.invalidated],
    ].map(([label, value]) => `<span><strong>${number(value)}</strong> ${label}</span>`).join("");
    byId("c19NearActionList").innerHTML = near.length
      ? near.map(cardMarkup).join("")
      : emptyMarkup("目前没有接近行动门槛的项目", "系统会继续从全部机会中筛选；没有凑数也不会强行给出动作。");
    byId("c19ChangeList").innerHTML = recent.length
      ? recent.map(changeMarkup).join("")
      : emptyMarkup("最近 7 天没有发现足以改变判断的重要变化", "普通行情波动不会进入这个列表。");
    byId("c19LimitList").innerHTML = blockers.length
      ? blockers.map((item, index) => `<li><span class="c19-limit-index">${index + 1}</span><div><strong>${escapeHtml(text(item.name || item.label, "当前限制"))}</strong><span>${escapeHtml(text(item.impact || item.fact || item.statusLabel, "这项事实还不足以支持行动。"))}</span></div></li>`).join("")
      : `<li><span class="c19-limit-index">✓</span><div><strong>当前没有单独需要解释的限制</strong><span>系统会继续检查新证据，动作仍以完整门槛为准。</span></div></li>`;
    byId("c19RefreshTime").textContent = dateTime(refresh.finishedAt || refresh.finished_at || center.generatedAt);
    byId("c19OpportunityCount").textContent = number(center.counts?.total || records.length);
    byId("c19TrackingHint").textContent = `${number(tracking.counts?.activeTracking || 0)} 个项目由系统按计划复查；你只需阅读变化和项目详情。`;
    setNotice(center.latestRefresh?.status);
  }

  function cardMarkup(item) {
    return `<a class="c19-card c19-card-link" href="${escapeHtml(detailContextUrl(item.detailUrl))}" data-detail-link><div class="c19-card-header"><h3>${escapeHtml(item.projectName)}${item.symbol ? ` <small>${escapeHtml(item.symbol)}</small>` : ""}</h3><span class="c19-badge ${item.actionClass}">${escapeHtml(item.action)}</span></div><p>${escapeHtml(item.reason)}</p><small>最大风险：${escapeHtml(item.risk)}<br>最近检查：${escapeHtml(relativeTime(item.changedAt))}</small></a>`;
  }

  function changeMarkup(item) {
    const name = text(item.projectName || item.title, "项目");
    const explanation = friendlyCopy(item.explanation || item.currentExplanation || item.summary, "出现了新的可核验信息。");
    const impact = item.change_direction === "upgrade" ? "可能改善判断" : item.change_direction === "downgrade" ? "可能收紧判断" : "需要继续观察";
    const detailUrl = text(item.detailUrl, `project-detail.html?id=${encodeURIComponent(item.case_id || item.caseId || "")}`);
    return `<a class="c19-card c19-card-link" href="${escapeHtml(detailContextUrl(detailUrl))}" data-detail-link><div class="c19-card-header"><h3>${escapeHtml(name)}</h3><span class="c19-badge ${item.change_direction === "downgrade" ? "bad" : item.change_direction === "upgrade" ? "good" : ""}">${escapeHtml(impact)}</span></div><p><strong>发生了什么：</strong>${escapeHtml(explanation)}</p><small>${escapeHtml(dateTime(item.observed_at || item.observedAt || changes.generatedAt))} · 依据：${escapeHtml(friendlyCopy(item.changeSourceLabel || item.title, "系统自动检查"))}</small></a>`;
  }

  function emptyMarkup(title, detail) {
    return `<div class="c19-empty"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div>`;
  }

  function renderAll() {
    const query = text(byId("c19Search")?.value).trim().toLowerCase();
    const action = text(byId("c19ActionFilter")?.value, "all");
    const category = text(byId("c19CategoryFilter")?.value, "all");
    const state = readListState();
    let page = Number(state.page || 1);
    const all = records.map(normalizedCase).filter((item) => {
      if (action !== "all" && item.action !== action) return false;
      if (category !== "all" && item.routeLabel !== category) return false;
      return !query || `${item.projectName} ${item.symbol} ${item.reason} ${item.routeLabel}`.toLowerCase().includes(query);
    });
    const totalPages = Math.max(1, Math.ceil(all.length / pageSize));
    if (page > totalPages) page = totalPages;
    writeListState({ page, query, action, category });
    const visible = all.slice((page - 1) * pageSize, page * pageSize);
    byId("c19AllList").innerHTML = visible.length ? visible.map(cardMarkup).join("") : emptyMarkup("没有符合条件的机会", "可以调整筛选条件；结果不会无限向下加载。 ");
    byId("c19AllCount").textContent = `共 ${number(all.length)} 个机会 · 第 ${page}/${totalPages} 页`;
    renderPagination("c19AllPagination", page, totalPages, (next) => { writeListState({ page: next }); renderAll(); });
  }

  function renderChanges() {
    const time = text(byId("changeTimeFilter")?.value, "7d");
    const impact = text(byId("changeImpactFilter")?.value, "all");
    const actionImpact = text(byId("changeActionImpactFilter")?.value, "all");
    const query = text(byId("c19ChangeSearch")?.value).trim().toLowerCase();
    const state = readListState();
    const now = Date.now();
    const maxAge = time === "24h" ? 86400000 : time === "30d" ? 2592000000 : 604800000;
    const list = changeItems.filter((item) => {
      const observed = new Date(item.observed_at || item.observedAt || 0).getTime();
      if (Number.isFinite(observed) && now - observed > maxAge) return false;
      if (impact !== "all" && text(item.change_direction, "stable") !== impact) return false;
      if (actionImpact === "action" && !["upgrade", "downgrade"].includes(item.change_direction)) return false;
      if (actionImpact === "watch" && item.change_direction !== "changed") return false;
      return !query || `${item.projectName} ${item.title} ${item.explanation}`.toLowerCase().includes(query);
    });
    const totalPages = Math.max(1, Math.ceil(list.length / pageSize));
    let page = Math.min(Math.max(1, Number(state.page || 1)), totalPages);
    writeListState({ page, query, time, impact, actionImpact });
    byId("c19ChangeCount").textContent = `最近 ${time === "24h" ? "24 小时" : time === "30d" ? "30 天" : "7 天"} · ${number(list.length)} 条重要变化`;
    const renderChangePage = (page) => {
      const start = (page - 1) * pageSize;
      byId("c19ChangePageList").innerHTML = list.slice(start, start + pageSize).map(changeMarkup).join("") || emptyMarkup("没有符合当前筛选的重要变化", "可以调整条件，或点击“重置筛选”恢复默认范围。");
      writeListState({ page });
      renderPagination("c19ChangePagination", page, totalPages, renderChangePage);
    };
    renderChangePage(page);
  }

  function renderPagination(id, page, totalPages, onChange) {
    const target = byId(id);
    if (!target) return;
    if (totalPages <= 1) { target.innerHTML = ""; return; }
    const buttons = [];
    buttons.push(`<button type="button" ${page <= 1 ? "disabled" : ""} data-page="${page - 1}">上一页</button>`);
    for (let index = 1; index <= totalPages; index += 1) {
      if (totalPages > 7 && index > 2 && index < totalPages - 1 && Math.abs(index - page) > 1) continue;
      buttons.push(`<button type="button" class="${index === page ? "is-current" : ""}" data-page="${index}">${index}</button>`);
    }
    buttons.push(`<button type="button" ${page >= totalPages ? "disabled" : ""} data-page="${page + 1}">下一页</button>`);
    target.innerHTML = buttons.join("");
    target.querySelectorAll("button[data-page]").forEach((button) => button.addEventListener("click", () => onChange(Number(button.dataset.page))));
  }

  function renderMethod() {
    const target = byId("c19MethodContent");
    if (target) target.innerHTML = `<div class="c19-method-grid"><article class="c19-method-card"><h3>先确认事实</h3><p>系统先核对项目主体、可交易资产、市场和退出条件，再判断是否值得继续研究。</p></article><article class="c19-method-card"><h3>再看凸性是否还在</h3><p>风险、剩余凸性、交易性和点火距离分别展示，任何一项都不会代替另一项。</p></article><article class="c19-method-card"><h3>最后给出当前动作</h3><p>动作只有普通建仓、极限试仓、只观察、反身性管理和失效/排除。没有完整闭环时，系统会明确说明为什么不行动。</p></article><article class="c19-method-card"><h3>证据有边界</h3><p>事实、项目方陈述和系统推断分开标记。普通用户不需要手工跑任务，系统会自动复查；重大升级或停止会进入确认流程。</p></article></div><div class="c19-method-note">这里展示的是研究方法和阅读方式，不是收益承诺，也不会自动交易。</div>`;
  }

  function findDetail() {
    const params = new URLSearchParams(location.search);
    const rawId = text(params.get("id"));
    const decoded = rawId ? decodeURIComponent(rawId) : "";
    const recordsById = details.records || {};
    const candidate = recordsById[decoded] || recordsById[rawId] || recordsById[`project:${decoded}`] || Object.values(recordsById).find((item) => [item.master?.masterId, item.project?.projectId, item.project?.id, item.master?.name, item.project?.name].includes(decoded));
    const base = records.map(normalizedCase).find((item) => [item.caseId, item.projectId, item.masterId, item.projectName].includes(decoded) || item.detailUrl.includes(encodeURIComponent(decoded))) || normalizedCase(candidate?.cases?.[0] || candidate || {});
    return { rawId: decoded, record: candidate, base };
  }

  function renderDetail() {
    const found = findDetail();
    const item = found.base;
    const record = found.record || {};
    const project = record.project || record.master || {};
    const asset = record.assets && (Array.isArray(record.assets) ? record.assets[0] : Object.values(record.assets)[0]);
    const back = document.querySelector(".c19-detail-back");
    if (back) {
      const from = text(new URLSearchParams(location.search).get("from"), "all");
      const sourceState = readModeState(from);
      const page = Math.max(1, Number(sourceState.page || 1));
      back.href = from === "changes" ? "candidate-pool.html?view=changes" : from === "home" ? "candidate-pool.html" : "candidate-pool.html?view=all";
      back.textContent = from === "changes" ? `← 返回重要变化${page > 1 ? `第 ${page} 页` : ""}` : from === "home" ? "← 返回机会首页" : `← 返回全部机会${page > 1 ? `第 ${page} 页` : ""}`;
    }
    byId("c19DetailTitle").textContent = item.projectName;
    byId("c19DetailAction").textContent = item.action;
    byId("c19DetailAction").className = `c19-badge ${item.actionClass}`;
    byId("c19DetailIntro").textContent = item.reason;
    byId("c19DetailWhy").textContent = friendlyCopy(project.description || project.summary || item.routeReason, "系统按生命周期与证据完整度安排研究顺序。");
    byId("c19DetailHandling").textContent = item.reason.startsWith(`${item.action}：`)
      ? item.reason
      : `${item.action}：${item.reason}`;
    const projectChanges = changeItems.filter((change) => change.case_id === item.caseId || change.caseId === item.caseId || change.projectName === item.projectName).slice(0, 5);
    byId("c19DetailChange").innerHTML = projectChanges.length
      ? `<ul class="c19-detail-change-list">${projectChanges.map((change) => `<li><strong>${escapeHtml(dateTime(change.observed_at || change.observedAt))}</strong><span>${escapeHtml(friendlyCopy(change.explanation || change.summary, "判断依据出现变化。"))}</span></li>`).join("")}</ul>`
      : "最近没有足以改变判断的新变化。";
    byId("c19DetailCatalyst").textContent = item.catalyst;
    byId("c19DetailRisk").textContent = item.risk;
    byId("c19DetailInvalidation").textContent = item.invalidationConditions.length
      ? item.invalidationConditions.join("；")
      : "当前尚未形成更具体的失效条件；若项目身份、资产关系或卖出路径出现冲突，系统会停止当前观察。";
    byId("c19DetailRoute").textContent = item.routeLabel;
    byId("c19DetailAsset").textContent = text(asset?.symbol || asset?.name || item.symbol, "尚未确认可交易资产");
    byId("c19DetailEvidence").innerHTML = `<strong>证据时间：</strong>${escapeHtml(dateTime(item.sourceTime))}<br><strong>说明：</strong>事实、项目方陈述和系统推断分开保存；依据来源和完整溯源由系统保留，可按数据时间复核。`;
    if (!found.record && !found.rawId) byId("c19DetailIntro").textContent = "请从机会首页、全部机会或重要变化进入项目详情。";
  }

  function bindFilters() {
    ["c19Search", "c19ActionFilter", "c19CategoryFilter"].forEach((id) => byId(id)?.addEventListener("input", renderAll));
    ["c19ChangeSearch", "changeTimeFilter", "changeImpactFilter", "changeActionImpactFilter"].forEach((id) => byId(id)?.addEventListener("input", renderChanges));
    byId("c19ResetChangeFilters")?.addEventListener("click", () => {
      byId("c19ChangeSearch").value = "";
      byId("changeTimeFilter").value = "7d";
      byId("changeImpactFilter").value = "all";
      byId("changeActionImpactFilter").value = "all";
      writeListState({ page: 1, query: "", time: "7d", impact: "all", actionImpact: "all" });
      renderChanges();
    });
    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-detail-link]")) saveListPosition();
    });
    window.addEventListener("pagehide", saveListPosition);
  }

  function restoreControls() {
    const state = readListState();
    const values = {
      c19Search: state.query,
      c19ActionFilter: state.action,
      c19CategoryFilter: state.category,
      c19ChangeSearch: state.query,
      changeTimeFilter: state.time,
      changeImpactFilter: state.impact,
      changeActionImpactFilter: state.actionImpact,
    };
    Object.entries(values).forEach(([id, value]) => {
      if (value != null && byId(id)) byId(id).value = value;
    });
  }

  function loadRuntimeNotice() {
    fetch("/api/update-status", { cache: "no-store" }).then((response) => response.ok ? response.json() : null).then((status) => {
      if (!status) return;
      if (["partial_success", "failed", "running"].includes(status.state)) setNotice(status.state);
    }).catch(() => {});
  }

  function ensureModeLayout() {
    const main = document.querySelector(".c19-front-main");
    if (!main || mode === "home") return;
    if (mode === "all") {
      main.innerHTML = `<section class="c19-section" aria-labelledby="allTitle"><span class="c19-kicker">机会目录</span><h1 id="allTitle" class="c19-page-title">全部机会</h1><p class="c19-page-intro">只读查看当前机会。每页最多 20 条，筛选和页码会保存在本机。</p><div class="c19-list-toolbar"><input id="c19Search" type="search" placeholder="搜索项目名称、符号或理由"><select id="c19ActionFilter"><option value="all">全部动作</option><option>普通建仓</option><option>极限试仓</option><option>只观察</option><option>反身性管理</option><option>失效/排除</option></select><select id="c19CategoryFilter"><option value="all">全部类别</option><option>早期项目</option><option>OG 项目</option><option>潜力项目</option></select><span id="c19AllCount" class="c19-result-count"></span></div><div id="c19AllList" class="c19-card-grid"></div><div id="c19AllPagination" class="c19-pagination" aria-label="全部机会分页"></div></section>`;
      return;
    }
    if (mode === "changes") {
      main.innerHTML = `<section class="c19-section" aria-labelledby="changesPageTitle"><span class="c19-kicker">最近 7 天</span><h1 id="changesPageTitle" class="c19-page-title">重要变化</h1><p class="c19-page-intro">这里只展示会改变或可能改变判断的变化；稳定状态和普通行情波动不会无限堆积。</p><div class="c19-list-toolbar"><input id="c19ChangeSearch" type="search" placeholder="搜索项目或变化说明"><select id="changeTimeFilter"><option value="7d">最近 7 天</option><option value="24h">最近 24 小时</option><option value="30d">最近 30 天</option></select><select id="changeImpactFilter"><option value="all">所有影响</option><option value="upgrade">可能改善判断</option><option value="downgrade">可能收紧判断</option><option value="changed">需要继续观察</option></select><select id="changeActionImpactFilter"><option value="all">全部变化</option><option value="action">只看可能改变动作</option><option value="watch">只看继续观察</option></select><button id="c19ResetChangeFilters" class="c19-reset-filter" type="button">重置筛选</button><span id="c19ChangeCount" class="c19-result-count"></span></div><div id="c19ChangePageList" class="c19-card-grid c19-two"></div><div id="c19ChangePagination" class="c19-pagination" aria-label="重要变化分页"></div></section>`;
      return;
    }
    if (mode === "method") {
      main.innerHTML = `<section class="c19-section"><span class="c19-kicker">阅读说明</span><h1 class="c19-page-title">我们如何判断</h1><p class="c19-page-intro">这是一页普通语言说明：系统如何从证据走到当前动作，哪些事情仍然需要等待。</p><div id="c19MethodContent"></div></section>`;
    }
  }

  ensureModeLayout();
  navActive();
  restoreControls();
  bindFilters();
  if (mode === "home") renderHome();
  if (mode === "all") renderAll();
  if (mode === "changes") renderChanges();
  if (mode === "method") renderMethod();
  if (mode === "detail") renderDetail();
  if (mode === "all" || mode === "changes") restoreListPosition();
  loadRuntimeNotice();
})();
