(function convexityOpportunityCenter() {
  const state = window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER;
  const routeState = window.PENGUIN_CONVEXITY_RESEARCH_ROUTES;
  const trackingState = window.PENGUIN_CONVEXITY_TRACKING_TASKS;
  const changeState = window.PENGUIN_CONVEXITY_CHANGE_EXPLANATIONS;
  const pageState = window.PenguinPageState;
  const restoredPageState = pageState?.load("candidate-pool") || {};
  const pageMode = new URLSearchParams(window.location.search).get("view") || "home";
  const directoryPageSize = 20;
  let directoryPage = Number(restoredPageState.directoryPage || 0);
  if (pageMode === "home") document.body.classList.add("c18-home-mode");
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
  const money = (value) => {
    if (value == null) return "--";
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: "USD",
      notation: Math.abs(Number(value)) >= 1000000 ? "compact" : "standard",
      maximumFractionDigits: Math.abs(Number(value)) < 1 ? 6 : 2,
    }).format(Number(value));
  };
  const number = (value) => Number(value || 0).toLocaleString("zh-CN");
  const firstItems = (items, limit) => {
    const output = [];
    (items || []).forEach((item) => {
      if (output.length < limit) output.push(item);
    });
    return output;
  };
  const riskLabels = {
    low: "低",
    medium: "中",
    high: "高",
    unknown: "待核验",
    blocked: "阻断",
  };
  const remainingLabels = {
    high: "高",
    medium: "中",
    low: "低",
    unknown: "待核验",
    none: "无",
  };
  const ignitionLabels = {
    immediate: "临近",
    near: "较近",
    forming: "形成中",
    distant: "较远",
    unknown: "待核验",
  };
  const tradeabilityLabels = {
    standard: "标准",
    extreme: "极限小额",
    limited: "受限",
    unknown: "待核验",
    untradeable: "不可交易",
  };
  const sortingLabels = {
    attention: "综合关注顺序",
    ignition: "点火最近优先",
    convexity: "剩余凸性优先",
    tradeability: "交易性优先",
    risk: "风险较低优先",
  };
  const fieldOrder = {
    ignition: { immediate: 0, near: 1, forming: 2, distant: 3, unknown: 4 },
    convexity: { high: 0, medium: 1, low: 2, none: 3, unknown: 4 },
    tradeability: { standard: 0, extreme: 1, limited: 2, unknown: 3, untradeable: 4 },
    risk: { low: 0, medium: 1, high: 2, unknown: 3, blocked: 4 },
  };
  const changesByCase = new Map(
    (changeState?.items || []).map((item) => [item.caseId, item]),
  );
  const routesByCase = new Map(
    (routeState?.records || []).filter((item) => item.caseId).map((item) => [item.caseId, item]),
  );
  const trackingByCase = new Map(
    (trackingState?.tasks || []).map((item) => [item.caseId, item]),
  );
  const conclusionGroups = state?.conclusionBoard?.groups || [];
  const conclusionGroupById = new Map(
    conclusionGroups.map((group) => [group.id, group]),
  );
  const routeFor = (item) => routesByCase.get(item.caseId) || {
    routeId: "hybrid",
    routeLabel: "潜力项目",
    routeShortLabel: "潜力项目",
    routeReason: "生命周期分类快照待刷新。",
    primaryFocus: "同时补齐基础档案与前置信号。",
  };
  const sectionLinks = [...document.querySelectorAll('.opportunity-topbar .product-nav a[href^="#"]')];
  const sectionTargets = sectionLinks
    .map((link) => ({
      link,
      target: document.getElementById(link.getAttribute("href").replace(/^#/, "")),
    }))
    .filter((item) => item.target);
  const setActiveSection = (targetId) => {
    sectionTargets.forEach(({ link, target }) => {
      link.classList.toggle("active", target.id === targetId);
    });
  };
  const syncActiveSection = () => {
    const topbarHeight = document.querySelector(".opportunity-topbar")?.offsetHeight || 0;
    const readingLine = window.scrollY + topbarHeight + 24;
    const active = sectionTargets.reduce(
      (current, item) => (item.target.offsetTop <= readingLine ? item : current),
      sectionTargets[0],
    );
    if (active) setActiveSection(active.target.id);
  };
  sectionTargets.forEach(({ link, target }) => {
    link.addEventListener("click", () => setActiveSection(target.id));
  });
  let navScrollFrame = 0;
  window.addEventListener("scroll", () => {
    if (navScrollFrame) return;
    navScrollFrame = window.requestAnimationFrame(() => {
      navScrollFrame = 0;
      syncActiveSection();
    });
  }, { passive: true });

  function renderC18Home() {
    const c18 = state?.c18 || {};
    const zero = c18.zeroResult || {
      label: "关键覆盖尚未完成",
      detail: "正在等待本项目建立可发布基线。",
    };
    const counts = state?.actionCounts || {};
    byId("c18ZeroResultLabel").textContent = zero.label || "本轮状态待确认";
    byId("c18ZeroResultDetail").textContent = zero.detail || "系统正在核对覆盖与更新时间。";
    byId("c18OrdinaryCount").textContent = number(counts.ordinary);
    byId("c18ExtremeCount").textContent = number(counts.extreme);
    byId("c18ReflexiveCount").textContent = number(state?.counts?.reflexive);
    byId("c18InvalidatedCount").textContent = number(state?.counts?.invalidated);
    const refresh = state?.latestRefresh || {};
    byId("c18ConclusionTimes").textContent = `数据时间：${dateTime(refresh.startedAt || refresh.started_at || state?.source?.candidateGeneratedAt)} · 更新完成：${dateTime(refresh.finishedAt || refresh.finished_at)} · 页面生成：${dateTime(state?.generatedAt)}`;
    const blocker = firstItems(c18.blockerDetails, 1)[0];
    byId("c18PrimaryBlocker").innerHTML = blocker
      ? `<strong>首要行动缺口：${escapeHtml(blocker.name)}</strong> ${escapeHtml(blocker.fact)} ${escapeHtml(blocker.impact)} <a href="action-gaps.html">查看事实、门槛与下一步</a>`
      : "当前没有需要单独解释的行动缺口。";
    const near = firstItems(c18.nearAction, 5);
    byId("c18NearActionList").innerHTML = near.length
      ? near.map((item) => `<a href="${escapeHtml(item.detailUrl || "candidate-pool.html?view=library")}"><strong>${escapeHtml(item.projectName)}${item.symbol ? ` · ${escapeHtml(item.symbol)}` : ""}</strong><span>${escapeHtml(item.currentAction)} · 已满足 ${item.conditionsMet}/${item.conditionsTotal} 项</span><small>首要缺口：${escapeHtml(item.primaryGap)} · 负责人：${escapeHtml(item.owner)} · 下次检查：${escapeHtml(dateTime(item.nextReviewAt))}</small></a>`).join("")
      : '<div class="c18-empty">当前没有接近行动门槛的项目。系统会继续筛选。</div>';
    const changeSource = changeState?.recent24h?.length ? changeState.recent24h : (changeState?.recent7d || []);
    const changeByCase = new Map();
    changeSource.forEach((item) => {
      const key = item.case_id || item.caseId || item.projectName;
      if (!changeByCase.has(key)) changeByCase.set(key, item);
    });
    const changes = firstItems([...changeByCase.values()], 5);
    byId("c18ImportantChanges").innerHTML = changes.length
      ? changes.map((item) => {
        const changed = firstItems(item.changedFields || [], 1)[0];
        const transition = changed ? `${changed.label || changed.field}：${changed.fromLabel || changed.from || "首次记录"} → ${changed.toLabel || changed.to || "当前"}` : `${item.from_stage || "当前"} → ${item.to_stage || "当前"}`;
        const impact = item.change_direction === "upgrade" ? "可能上调行动判断" : item.change_direction === "downgrade" ? "可能下调行动判断" : "当前动作未必改变";
        return `<a href="${escapeHtml(item.detailUrl || "change-explanations.html")}"><strong>${escapeHtml(item.projectName || item.title || "项目")}</strong><span>${escapeHtml(transition)} · ${escapeHtml(impact)}</span><small>${escapeHtml(item.changeSourceLabel || "规则重算")} · ${escapeHtml(dateTime(item.observed_at || item.observedAt))} · 下一步由系统继续核验</small></a>`;
      }).join("")
      : '<div class="c18-empty">当前没有达到重要变化阈值的记录。普通行情波动已折叠。</div>';
    const needsUser = (trackingState?.tasks || []).filter((item) => item.decisionReview?.required && item.decisionReview.status === "pending");
    byId("c18NeedsUser").innerHTML = needsUser.length
      ? `<strong>需要你确认 ${number(needsUser.length)} 项重大结论</strong><span>只处理升级或停止；继续跟踪和无变化由系统自动完成。</span><a href="update-center.html#verificationQueueSection">打开人工确认队列</a>`
      : "<strong>当前无需你操作</strong><span>继续跟踪、无变化、身份核验、催化发现和市场采集由系统自动处理。</span>";
    refreshC18Scheduler();
  }

  function refreshC18Scheduler() {
    fetch("/api/c1-8/status", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("scheduler status")))
      .then((status) => {
        byId("c18SchedulerSummary").innerHTML = `<strong>${escapeHtml(status.statusLabel || "自动运行状态待确认")}</strong><span>${escapeHtml(status.reason || "系统会按计划检查。")}</span><small>下次每日更新：${escapeHtml(dateTime(status.nextDailyRunAt))} · 每小时到期检查：${escapeHtml(dateTime(status.nextHourlyCheckAt))} · 负责人：${escapeHtml(status.owner || "系统自动运行")}</small>`;
      })
      .catch(() => {
        byId("c18SchedulerSummary").textContent = "暂时无法读取自动运行状态；请打开凸性工作台查看本项目状态。";
      });
  }

  if (!state?.cases?.length) {
    const emptyReason = state?.emptyReason
      || "当前没有机器生成的项目，等待下一次自动扫描。";
    byId("candidateStatus").textContent = "机器候选池当前为空";
    byId("candidateGeneratedAt").textContent = dateTime(state?.generatedAt);
    ["ordinaryCount", "extremeCount", "observeCount", "reflexiveCount", "invalidatedCount"]
      .forEach((id) => { byId(id).textContent = "0"; });
    byId("heroVerdict").textContent = "当前没有机器生成的行动结论";
    byId("heroVerdictNote").textContent = emptyReason;
    byId("currentGateSummary").textContent = (
      "生产数据已经清场。系统会在后续扫描中自动发现、建档、筛选并输出结论，"
      + "无需人工先行复核。"
    );
    byId("opportunityStageBoard").innerHTML = (
      `<div class="opportunity-empty"><strong>等待机器首次扫描</strong>`
      + `<p>${escapeHtml(emptyReason)}</p></div>`
    );
    byId("opportunityRouteBoard").innerHTML = '<div class="opportunity-empty">尚无机器建档项目。</div>';
    byId("trackingTaskSummary").textContent = "尚无机器项目，因此没有跟踪任务。";
    ["trackingActiveCount", "trackingDueCount", "trackingP0Count", "trackingP1Count"]
      .forEach((id) => { byId(id).textContent = "0"; });
    byId("trackingTaskBoard").innerHTML = '<div class="opportunity-empty">等待项目进入机器跟踪流程。</div>';
    byId("opportunityBlockerBoard").innerHTML = '<div class="opportunity-empty">尚无项目，不生成虚构阻断原因。</div>';
    byId("opportunityChangeSummary").textContent = "清场后尚无机器结论变化。";
    byId("opportunityChangeFeed").innerHTML = '<div class="opportunity-empty">下一次有效机器结论将成为新基线。</div>';
    byId("opportunityCatalystSummary").textContent = "尚无项目，因此没有催化交易路径。";
    byId("opportunityCatalystMetrics").innerHTML = "";
    byId("opportunityCatalystBoard").innerHTML = '<div class="opportunity-empty">等待机器项目生成后建立催化路径。</div>';
    byId("opportunityVisibleCount").textContent = "0";
    byId("opportunitySortSummary").textContent = "当前无项目";
    byId("opportunityDirectoryList").innerHTML = (
      `<div class="opportunity-empty"><strong>生产候选池为空</strong>`
      + `<p>${escapeHtml(emptyReason)}</p></div>`
    );
    byId("opportunityStageMap").innerHTML = "";
    byId("rankingBoundary").textContent = "排序规则保留，待机器项目产生后自动应用。";
    byId("rankingComponents").innerHTML = "";
    renderC18Home();
    return;
  }

  const cases = [...state.cases];
  const casesById = new Map(cases.map((item) => [item.caseId, item]));
  const gateSummary = state.gateScreening.summary;
  const currentPreset = state.gateScreening.presets.find(
    (preset) => preset.id === state.gateScreening.active.activePresetId,
  );

  byId("candidateStatus").textContent = "凸性结论已更新";
  byId("candidateGeneratedAt").textContent = dateTime(state.generatedAt);
  byId("ordinaryCount").textContent = number(state.actionCounts.ordinary);
  byId("extremeCount").textContent = number(state.actionCounts.extreme);
  byId("observeCount").textContent = number(state.actionCounts.observe);
  byId("reflexiveCount").textContent = number(state.counts.reflexive);
  byId("invalidatedCount").textContent = number(state.counts.invalidated);
  byId("heroVerdict").textContent = state.conclusionBoard?.headline
    || (state.counts.actionable
      ? `${number(state.counts.actionable)}个当前可行动项目`
      : "本期没有满足完整行动门槛的项目");
  byId("heroVerdictNote").textContent = state.conclusionBoard?.note
    || `${number(state.actionCounts.observe)}个项目当前只观察，阻断原因在项目卡和详情页单独展示。`;
  byId("currentGateSummary").textContent = `${currentPreset?.name || "当前筛选方案"}：`
    + `${gateSummary.passed}个完整通过，${gateSummary.pending}个待核验，`
    + `${gateSummary.excluded}个未入选。下方依次解释阻断原因、最近变化、催化路径、项目类别和跟踪任务。`;
  renderC18Home();

  function facts(item) {
    return [
      `风险 ${riskLabels[item.riskLevel] || item.riskLevel}`,
      `剩余凸性 ${remainingLabels[item.remainingConvexity] || item.remainingConvexity}`,
      `点火 ${ignitionLabels[item.ignitionProximity] || item.ignitionProximity}`,
      `交易性 ${tradeabilityLabels[item.liquidityGrade] || item.liquidityGrade}`,
    ].join(" · ");
  }

  function changeBadge(item) {
    const change = changesByCase.get(item.caseId);
    if (!change) return "";
    return `<span class="opportunity-change-badge status-${escapeHtml(change.currentStatus)}">${escapeHtml(change.currentStatusLabel)}</span>`;
  }

  function reviewBadge(item) {
    const review = changesByCase.get(item.caseId)?.decisionReview;
    if (!review?.required) return "";
    return `<span class="opportunity-review-badge status-${escapeHtml(review.status)}">${escapeHtml(review.statusLabel)}</span>`;
  }

  function publicationBadge(item) {
    return item.publicationStatus === "published"
      ? '<span class="opportunity-publication-badge">人工发布</span>'
      : "";
  }

  function stageResultCard(item) {
    const stage = item.opportunityStage;
    const route = routeFor(item);
    const task = trackingByCase.get(item.caseId);
    const execution = task?.latestExecution;
    const catalystPath = item.catalystTradePath;
    return `
      <a class="opportunity-stage-result" href="${escapeHtml(item.detailUrl)}">
        <header>
          <h4>${escapeHtml(item.projectName)}${item.symbol ? ` <small>${escapeHtml(item.symbol)}</small>` : ""}</h4>
          <b>${item.publicSignal.score}</b>
        </header>
          <p>${escapeHtml(stage.finalActionLabel)} · ${escapeHtml(route.routeShortLabel)} · ${escapeHtml(facts(item))}</p>
          ${publicationBadge(item)}
          ${changeBadge(item)}
          ${reviewBadge(item)}
          <small>${escapeHtml(stage.finalActionReason)}</small>
          ${catalystPath ? `<small class="opportunity-stage-path">催化路径：${escapeHtml(catalystPath.path_stage_label)} · ${escapeHtml(catalystPath.catalyst_summary)}</small>` : ""}
          ${task ? `<small class="opportunity-task-preview">下一步：${escapeHtml(task.nextStep)} · ${escapeHtml(dateTime(task.nextReviewAt))}复查</small>` : ""}
          ${execution ? `<small class="opportunity-execution-preview">本轮依据：${escapeHtml(execution.decisionLabel)} · ${escapeHtml(execution.reason)}</small>` : ""}
      </a>`;
  }

  function renderRouteBoard() {
    byId("opportunityRouteBoard").innerHTML = (routeState?.routes || []).map((route) => {
      const matching = cases.filter((item) => routeFor(item).routeId === route.id);
      const matchingTasks = (trackingState?.tasks || []).filter(
        (item) => item.projectCategory === route.id && item.currentAction === "只观察",
      );
      return `
        <button type="button" data-route-filter="${escapeHtml(route.id)}">
          <span>${escapeHtml(route.shortLabel)}</span>
          <strong>${number(matching.length)}</strong>
          <p>${escapeHtml(route.primaryFocus)}</p>
          <small>${number(matchingTasks.length)}个继续跟踪任务 · 查看本类项目</small>
        </button>
      `;
    }).join("");
  }

  function trackingTaskCard(task) {
    const execution = task.latestExecution;
    return `
      <a class="tracking-task-card priority-${escapeHtml(task.priority)} status-${escapeHtml(task.status)}" href="${escapeHtml(task.detailUrl)}">
        <header>
          <div>
            <span>${escapeHtml(task.projectCategoryLabel)} · ${escapeHtml(task.taskTypeLabel)}</span>
            <h3>${escapeHtml(task.projectName)}${task.symbol ? ` <small>${escapeHtml(task.symbol)}</small>` : ""}</h3>
          </div>
          <div class="tracking-task-badges"><b>${escapeHtml(task.priority)}</b><em>${escapeHtml(task.statusLabel)}</em></div>
        </header>
        <p><strong>下一步</strong>${escapeHtml(task.nextStep)}</p>
        <div class="tracking-task-meta">
          <span><b>为什么现在做</b>${escapeHtml(task.whyNow)}</span>
          <span><b>证据进度</b>${number(task.evidenceCompleteCount)}/${number(task.evidenceTotal)}</span>
          <span><b>下次复查</b>${escapeHtml(dateTime(task.nextReviewAt))}</span>
        </div>
        ${task.decisionFollowUp?.required ? `
          <div class="tracking-follow-up status-${escapeHtml(task.decisionFollowUp.status)}">
            <strong>${escapeHtml(task.decisionFollowUp.typeLabel)} · ${escapeHtml(task.decisionFollowUp.statusLabel)}</strong>
            <span>${escapeHtml(task.nextStep)}</span>
          </div>
        ` : ""}
        <ol>${task.checklist.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>
        ${execution ? `
          <div class="tracking-task-execution status-${escapeHtml(execution.execution_status)}">
            <strong>最近执行：${escapeHtml(execution.statusLabel)} · ${escapeHtml(execution.decisionLabel)}</strong>
            <span>${escapeHtml(execution.reason)}</span>
            <small>${number(execution.findings_count)}条记录，${number(execution.new_findings_count)}条新增或变化 · ${escapeHtml(dateTime(execution.finished_at))}</small>
          </div>
        ` : `<div class="tracking-task-execution is-pending"><strong>${escapeHtml(task.c18StatusLabel || (task.status === "due" ? "已到期，等待调度" : "尚未到复查时间"))}</strong><span>${escapeHtml(task.c18StatusExplanation || `负责人：系统自动运行。下次检查：${task.nextReviewAt}`)}</span></div>`}
        <footer>
          <span><b>升级条件</b>${escapeHtml(task.upgradeCondition)}</span>
          <span><b>停止条件</b>${escapeHtml(task.stopCondition)}</span>
        </footer>
      </a>
    `;
  }

  function renderTrackingTasks() {
    if (!trackingState?.tasks) {
      byId("trackingTaskSummary").textContent = "跟踪任务数据尚未生成。";
      byId("trackingTaskBoard").innerHTML = '<div class="opportunity-empty">当前没有可展示的跟踪任务。</div>';
      return;
    }
    byId("trackingActiveCount").textContent = number(trackingState.counts.activeTracking);
    byId("trackingDueCount").textContent = number(trackingState.counts.due);
    byId("trackingP0Count").textContent = number(trackingState.counts.P0);
    byId("trackingP1Count").textContent = number(trackingState.counts.P1);
    const routeFilter = byId("trackingRouteFilter").value;
    const priorityFilter = byId("trackingPriorityFilter").value;
    const statusFilter = byId("trackingStatusFilter").value;
    const visible = trackingState.tasks.filter((task) => {
      if (routeFilter !== "all" && task.projectCategory !== routeFilter) return false;
      if (priorityFilter !== "all" && task.priority !== priorityFilter) return false;
      if (statusFilter === "active") {
        return task.currentAction === "只观察" && ["due", "open"].includes(task.status);
      }
      return statusFilter === "all" || task.status === statusFilter;
    });
    const previewTasks = firstItems(visible, 5);
    byId("trackingTaskSummary").textContent = visible.length > 5
      ? `当前有${number(visible.length)}个任务，首页只显示优先级最高的 5 个；完整进度请打开工作台。`
      : `当前显示${number(visible.length)}个任务；每个任务都包含下一步、复查时间、升级条件和停止条件。`;
    const categoryOrder = ["startup", "mature", "hybrid"];
    const categoryLabels = { startup: "早期项目", mature: "OG项目", hybrid: "潜力项目" };
    const groups = categoryOrder.map((category) => ({
      category,
      label: categoryLabels[category],
      tasks: previewTasks.filter((task) => task.projectCategory === category),
    })).filter((group) => group.tasks.length);
    byId("trackingTaskBoard").innerHTML = groups.length
      ? groups.map((group) => `
          <section class="tracking-task-group">
            <header><div><span>PROJECT CATEGORY</span><h3>${escapeHtml(group.label)}</h3></div><strong>${number(group.tasks.length)}</strong></header>
            <div>${group.tasks.map(trackingTaskCard).join("")}</div>
          </section>
        `).join("")
      : '<div class="opportunity-empty">当前筛选没有跟踪任务。</div>';
  }

  function renderRecentChanges() {
    if (!changeState?.items) {
      byId("opportunityChangeSummary").textContent = "变化比较尚未建立。";
      byId("opportunityChangeFeed").innerHTML = '<div class="opportunity-change-empty">本轮没有变化解释数据。</div>';
      return;
    }
    const recent24h = changeState.recent24h || [];
    const recent7d = changeState.recent7d || [];
    const recent = recent24h.length ? recent24h : recent7d;
    if (!recent.length) {
      const baselineOnly = changeState.counts.baseline === changeState.counts.total;
      byId("opportunityChangeSummary").textContent = baselineOnly
        ? `${number(changeState.counts.baseline)}个项目已建立首轮比较基线。`
        : "过去7天没有项目达到变化记录阈值。";
      byId("opportunityChangeFeed").innerHTML = `
        <div class="opportunity-change-empty">
          <strong>${baselineOnly ? "尚无可比较的自动升降级" : "本轮分层保持稳定"}</strong>
          <p>${baselineOnly ? "从下一次有效更新开始，项目升降级会明确显示触发字段与来源。" : "短期小幅行情波动不会被包装成新的投资结论。"}</p>
        </div>`;
      return;
    }
    byId("opportunityChangeSummary").textContent = recent24h.length
      ? `过去24小时记录 ${number(recent24h.length)} 条有效变化，当前详情页先展示最重要的 5 条。`
      : `过去24小时无新增，展示近7天 ${number(recent7d.length)} 条历史变化，当前详情页先展示最重要的 5 条。`;
    const directionLabels = {
      upgrade: "上调",
      downgrade: "下调",
      changed: "关键项变化",
    };
    byId("opportunityChangeFeed").innerHTML = firstItems(recent, 5).map((item) => `
      <a href="${escapeHtml(item.detailUrl || "change-explanations.html")}" class="status-${escapeHtml(item.change_direction)}">
        <span>${escapeHtml(directionLabels[item.change_direction] || "变化")} · ${escapeHtml(item.changeSourceLabel || "规则重算")}${item.trackingResult?.decisionReview?.required ? ` · ${escapeHtml(item.trackingResult.decisionReview.statusLabel)}` : ""}</span>
        <strong>${escapeHtml(item.projectName)}${item.symbol ? ` · ${escapeHtml(item.symbol)}` : ""}</strong>
        <p>${escapeHtml(item.explanation)}</p>
        <small>${number((item.evidence || []).length)}条依据 · ${escapeHtml(dateTime(item.observed_at))}</small>
      </a>
    `).join("");
  }

  function renderCatalystPathBoard() {
    const paths = cases
      .map((item) => ({ item, path: item.catalystTradePath }))
      .filter((entry) => entry.path);
    const withCatalyst = paths.filter(({ path }) => path.catalyst_status !== "missing");
    const withAsset = paths.filter(({ path }) => Boolean(path.expression_asset_text));
    const exitModeled = paths.filter(({ path }) => path.modeled_exit_slippage_pct != null);
    const researchReady = paths.filter(({ path }) => path.path_stage === "research_ready");
    const actionReady = paths.filter(({ path }) => path.path_stage === "action_ready");
    byId("opportunityCatalystSummary").textContent = (
      `${number(paths.length)}个项目已建立路径；${number(withCatalyst.length)}个出现候选催化，`
      + `${number(researchReady.length)}个达到研究闭环，${number(actionReady.length)}个达到行动闭环。`
    );
    byId("opportunityCatalystMetrics").innerHTML = [
      ["路径总数", paths.length, "所有机器项目均保留路径状态"],
      ["候选催化", withCatalyst.length, "已有90日内可溯源候选事实"],
      ["资产已映射", withAsset.length, "已找到可能承接价值的资产"],
      ["退出已估算", exitModeled.length, "已计算2万美元理论退出滑点"],
      ["研究闭环", researchReady.length, "证据与价值传导达到研究门槛"],
      ["行动闭环", actionReady.length, "仍须同时通过全部硬门槛"],
    ].map(([label, value, note]) => `
      <article><span>${escapeHtml(label)}</span><strong>${number(value)}</strong><small>${escapeHtml(note)}</small></article>
    `).join("");
    const stages = [
      ["action_ready", "行动闭环", "催化、资产、价值传导、市场和退出均通过。"],
      ["research_ready", "研究闭环", "研究路径完整，但行动门槛仍可能阻断。"],
      ["transmission_pending", "价值传导待闭环", "已有候选催化，尚未证明价值如何传导到可购买资产。"],
      ["catalyst_pending", "催化事实待发现", "项目已建档，继续寻找90日内可溯源催化。"],
      ["invalidated", "路径失效", "关键事实或项目状态已触发失效条件。"],
    ];
    byId("opportunityCatalystBoard").innerHTML = stages.map(([id, label, definition]) => {
      const matching = paths.filter(({ path }) => path.path_stage === id);
      const preview = matching.filter((entry, index) => index < 2);
      return `
        <article class="opportunity-catalyst-stage stage-${escapeHtml(id)}">
          <header><div><span>PATH STAGE</span><h3>${escapeHtml(label)}</h3></div><strong>${number(matching.length)}</strong></header>
          <p>${escapeHtml(definition)}</p>
          <div>
            ${preview.length
              ? preview.map(({ item, path }) => `
                  <a href="${escapeHtml(item.detailUrl)}#detailCatalystPath">
                    <strong>${escapeHtml(item.projectName)}${item.symbol ? ` · ${escapeHtml(item.symbol)}` : ""}</strong>
                    <span>${escapeHtml(path.catalyst_summary)}</span>
                    <small>${escapeHtml(path.expression_asset_text || "受益资产待确认")} · 下一步：${escapeHtml(path.next_step)}</small>
                  </a>`).join("")
              : '<div class="opportunity-stage-empty">当前没有项目，空结果保留。</div>'}
          </div>
        </article>`;
    }).join("");
  }

  function renderTransferGroups(groupCases) {
    const transferActions = [
      ["reflexive", "反身性管理", "已进入趋势或共振阶段，不再包装成早期凸性机会。"],
      ["invalidated", "失效排除", "核心事实、身份、安全边界或剩余凸性已经失效。"],
    ];
    return `<div class="opportunity-transfer-groups">${transferActions.map(([id, label, definition]) => {
      const matching = groupCases.filter(
        (item) => item.opportunityStage.finalActionCategory === id,
      );
      const preview = matching.filter((item, index) => index < 3);
      return `
        <section class="transfer-${escapeHtml(id)}">
          <header><div><span>TRANSFER RESULT</span><h4>${escapeHtml(label)}</h4></div><strong>${number(matching.length)}</strong></header>
          <p>${escapeHtml(definition)}</p>
          <div class="opportunity-stage-results">
            ${preview.length
              ? preview.map(stageResultCard).join("")
              : '<div class="opportunity-stage-empty">当前为空，保留独立分区。</div>'}
          </div>
          <button type="button" data-stage-filter="${escapeHtml(id)}">查看全部 ${number(matching.length)} 个</button>
        </section>`;
    }).join("")}</div>`;
  }

  function renderConclusionBoard() {
    byId("opportunityStageBoard").innerHTML = conclusionGroups.map((group) => {
      const groupCases = group.caseIds
        .map((caseId) => casesById.get(caseId))
        .filter(Boolean);
      const previewCases = groupCases.filter((item, index) => index < 3);
      const emptyText = group.id === "execution"
        ? "本期没有满足完整行动门槛的项目，空结果是有效结论。"
        : "当前没有项目，空结果保留。";
      return `
        <article class="opportunity-stage-lane conclusion-${escapeHtml(group.id)}">
          <header>
            <div><span>${escapeHtml(group.shortLabel)}</span><h3>${escapeHtml(group.label)}</h3></div>
            <strong>${number(group.count)}</strong>
          </header>
          <p>${escapeHtml(group.definition)}</p>
          ${group.id === "transferred"
            ? renderTransferGroups(groupCases)
            : `<div class="opportunity-stage-results">
                ${previewCases.length
                  ? previewCases.map(stageResultCard).join("")
                  : `<div class="opportunity-stage-empty">${escapeHtml(emptyText)}</div>`}
              </div>
              <button type="button" data-stage-filter="${escapeHtml(group.id)}">在完整目录查看全部 ${number(group.count)} 个</button>`}
        </article>`;
    }).join("");
  }

  function renderBlockerBoard() {
    const blockers = state.conclusionBoard?.blockers || [];
    byId("opportunityBlockerBoard").innerHTML = blockers.length
      ? blockers.map((blocker) => {
        const sampleCases = blocker.caseIds
          .map((caseId) => casesById.get(caseId))
          .filter(Boolean)
          .filter((item, index) => index < 3);
        return `
          <article class="opportunity-blocker-group">
            <header><strong>${escapeHtml(blocker.label)}</strong><span>${number(blocker.count)}个</span></header>
            <div class="opportunity-blocker-projects">
              ${sampleCases.map((item) => {
                const task = trackingByCase.get(item.caseId);
                const execution = task?.latestExecution;
                return `
                  <a href="${escapeHtml(item.detailUrl)}">
                    <strong>${escapeHtml(item.projectName)}${item.symbol ? ` · ${escapeHtml(item.symbol)}` : ""}</strong>
                    <span>${escapeHtml(item.opportunityStage.finalActionReason || item.opportunityStage.blockerLabel || blocker.label)}</span>
                    <small>${execution
                      ? `最近跟踪：${escapeHtml(execution.decisionLabel)} · ${escapeHtml(execution.reason)}`
                      : task
                        ? `下一步：${escapeHtml(task.nextStep)}`
                        : "跟踪任务待生成"}</small>
                  </a>`;
              }).join("")}
            </div>
            <button type="button" data-stage-filter="tracking">查看继续跟踪项目</button>
          </article>`;
      }).join("")
      : '<div class="opportunity-stage-empty">当前没有只观察项目，因此没有行动阻断项。</div>';
  }

  function renderMaturityMap() {
    const maturities = ["L0", "L1", "L2", "L3", "L4", "L5"];
    byId("opportunityStageMap").innerHTML = maturities.map((maturity) => {
      const matching = cases.filter((item) => item.maturity === maturity);
      const actionable = matching.filter(
        (item) => ["ordinary", "extreme"].includes(item.opportunityStage.finalActionCategory),
      ).length;
      return `
        <button type="button" data-maturity="${maturity}">
          <span>${maturity}</span>
          <strong>${matching.length}</strong>
          <small>${actionable ? `${actionable}个可行动` : "暂无行动级"}</small>
        </button>`;
    }).join("");
  }

  function addStageOptions() {
    byId("opportunityStageFilter").insertAdjacentHTML(
      "beforeend",
      `
        <optgroup label="结论分区">
          ${conclusionGroups.map((group) => (
            `<option value="${escapeHtml(group.id)}">${escapeHtml(group.label)}（${number(group.count)}）</option>`
          )).join("")}
        </optgroup>
        <optgroup label="精确动作">
          ${state.finalActions.map((action) => (
            `<option value="${escapeHtml(action.id)}">${escapeHtml(action.label)}（${number(state.actionCounts[action.id])}）</option>`
          )).join("")}
        </optgroup>`,
    );
  }

  function compareCases(left, right, mode) {
    const stageDifference = left.opportunityStage.finalActionOrder - right.opportunityStage.finalActionOrder;
    if (stageDifference) return stageDifference;
    const keyByMode = {
      ignition: "ignitionProximity",
      convexity: "remainingConvexity",
      tradeability: "liquidityGrade",
      risk: "riskLevel",
    };
    const itemKey = keyByMode[mode];
    if (itemKey) {
      const leftOrder = fieldOrder[mode][left[itemKey]] ?? 99;
      const rightOrder = fieldOrder[mode][right[itemKey]] ?? 99;
      if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    }
    const scoreDifference = right.publicSignal.score - left.publicSignal.score;
    return scoreDifference || left.projectName.localeCompare(right.projectName, "zh-CN");
  }

  function directoryCard(item, rank) {
    const signal = item.publicSignal;
    const stage = item.opportunityStage;
    const market = item.latestMarket;
    const invalidation = item.invalidation || signal.exitReasons.join("；") || "待补充";
    const route = routeFor(item);
    const task = trackingByCase.get(item.caseId);
    const execution = task?.latestExecution;
    const change = changesByCase.get(item.caseId);
    const catalystPath = item.catalystTradePath;
    const sourceUrl = item.machineConclusion?.source_url || catalystPath?.source_url || "";
    const nextStep = task?.nextStep || stage.nextStep || "等待下一轮机器任务生成。";
    const upgradeCondition = task?.upgradeCondition
      || stage.upgradeConditions?.[0]
      || "等待规则生成明确升级条件。";
    const recentChange = change?.explanation || "当前为比较基线，尚无达到记录阈值的变化。";
    return `
      <article class="opportunity-directory-card stage-${escapeHtml(stage.finalActionCategory)}">
        <div class="opportunity-rank">
          <span>${String(rank).padStart(2, "0")}</span>
          <small>关注顺序</small>
          <strong>${signal.score}</strong>
        </div>
        <div class="opportunity-card-main">
          <header>
            <div>
              <span class="opportunity-tier">当前动作 · ${escapeHtml(stage.finalActionLabel)}</span>
              <h3>${escapeHtml(item.projectName)}${item.symbol ? ` <small>${escapeHtml(item.symbol)}</small>` : ""}</h3>
            </div>
            <strong class="opportunity-action">${escapeHtml(item.maturity)}</strong>
          </header>
          <p>${escapeHtml(item.currentThesis)}</p>
          <div class="opportunity-route-line">
            <strong>${escapeHtml(route.routeLabel)}</strong>
            <span>${escapeHtml(route.routeReason)}</span>
          </div>
          <div class="opportunity-fact-line">
            <span>风险 <b>${escapeHtml(riskLabels[item.riskLevel] || item.riskLevel)}</b></span>
            <span>剩余凸性 <b>${escapeHtml(remainingLabels[item.remainingConvexity] || item.remainingConvexity)}</b></span>
            <span>点火距离 <b>${escapeHtml(ignitionLabels[item.ignitionProximity] || item.ignitionProximity)}</b></span>
            <span>交易性 <b>${escapeHtml(tradeabilityLabels[item.liquidityGrade] || item.liquidityGrade)}</b></span>
          </div>
          <div class="opportunity-stage-reason">
            <strong>为什么是这个动作</strong>
            <span>${escapeHtml(stage.finalActionReason)}</span>
          </div>
          ${catalystPath ? `
            <div class="opportunity-catalyst-path">
              <strong>催化交易路径 · ${escapeHtml(catalystPath.path_stage_label)}</strong>
              <span>${escapeHtml(catalystPath.catalyst_summary)}</span>
              <small>${escapeHtml(catalystPath.expression_asset_text || "资产待确认")} · 2万美元理论滑点 ${catalystPath.modeled_exit_slippage_pct == null ? "待估算" : `${Number(catalystPath.modeled_exit_slippage_pct).toFixed(2)}%`}</small>
            </div>` : ""}
          ${task && stage.finalActionCategory === "observe" ? `
            <div class="opportunity-next-task">
              <strong>下一步跟踪</strong>
              <span>${escapeHtml(task.nextStep)}</span>
              <small>${escapeHtml(task.priority)} · ${escapeHtml(task.statusLabel)} · ${escapeHtml(dateTime(task.nextReviewAt))}复查</small>
              ${execution ? `<small class="opportunity-latest-result">最近结果：${escapeHtml(execution.decisionLabel)} · ${escapeHtml(execution.reason)}</small>` : ""}
            </div>` : ""}
          ${stage.blockerLabel ? `<div class="opportunity-blocker"><strong>阻断状态</strong><span>${escapeHtml(stage.blockerLabel)}</span></div>` : ""}
          ${publicationBadge(item)}
          ${changeBadge(item)}
          ${reviewBadge(item)}
          <details class="opportunity-card-trace">
            <summary>查看证据、变化与升级条件</summary>
            <div>
              <p><strong>证据来源</strong>${sourceUrl
                ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">打开原始来源</a>`
                : `<a href="${escapeHtml(item.detailUrl)}#detailEvidence">查看项目证据</a>`}</p>
              <p><strong>最近变化</strong><span>${escapeHtml(recentChange)}</span></p>
              <p><strong>下一步任务</strong><span>${escapeHtml(nextStep)}</span></p>
              <p><strong>升级条件</strong><span>${escapeHtml(upgradeCondition)}</span></p>
              <p><strong>失效条件</strong><span>${escapeHtml(invalidation)}</span></p>
            </div>
          </details>
          <div class="opportunity-card-foot">
            <span>${market ? `价格 ${money(market.priceUsd)} · 24小时成交 ${money(market.volume24hUsd)}` : "市场快照待补齐"}</span>
            <span class="opportunity-invalidation">失效：${escapeHtml(invalidation)}</span>
          </div>
        </div>
        <a href="${escapeHtml(item.detailUrl)}">进入项目详情</a>
      </article>`;
  }

  function renderDirectory() {
    const stage = byId("opportunityStageFilter").value;
    const maturity = byId("opportunityMaturityFilter").value;
    const route = byId("opportunityRouteFilter").value;
    const risk = byId("opportunityRiskFilter").value;
    const remaining = byId("opportunityRemainingFilter").value;
    const ignition = byId("opportunityIgnitionFilter").value;
    const tradeability = byId("opportunityTradeabilityFilter").value;
    const sortMode = byId("opportunitySort").value;
    const search = byId("opportunitySearch").value.trim().toLowerCase();
    const visible = cases.filter((item) => {
      const groupedActions = conclusionGroupById.get(stage)?.actionIds;
      if (
        stage !== "all"
        && groupedActions
        && !groupedActions.includes(item.opportunityStage.finalActionCategory)
      ) return false;
      if (
        stage !== "all"
        && !groupedActions
        && item.opportunityStage.finalActionCategory !== stage
      ) return false;
      if (maturity !== "all" && item.maturity !== maturity) return false;
      if (route !== "all" && routeFor(item).routeId !== route) return false;
      if (risk !== "all" && item.riskLevel !== risk) return false;
      if (remaining !== "all" && item.remainingConvexity !== remaining) return false;
      if (ignition !== "all" && item.ignitionProximity !== ignition) return false;
      if (tradeability !== "all" && item.liquidityGrade !== tradeability) return false;
      if (!search) return true;
      return [
        item.projectName,
        item.symbol,
        item.convexitySource,
        item.currentThesis,
        item.opportunityStage.finalActionLabel,
        item.opportunityStage.finalActionReason,
        item.opportunityStage.blockerLabel,
        trackingByCase.get(item.caseId)?.nextStep,
        routeFor(item).routeLabel,
        routeFor(item).routeReason,
      ].some((value) => String(value || "").toLowerCase().includes(search));
    }).sort((left, right) => compareCases(left, right, sortMode));
    byId("opportunityVisibleCount").textContent = number(visible.length);
    const pageCount = Math.max(1, Math.ceil(visible.length / directoryPageSize));
    directoryPage = Math.max(0, Math.min(directoryPage, pageCount - 1));
    const pageStart = directoryPage * directoryPageSize;
    const pageEnd = pageStart + directoryPageSize;
    const pageItems = visible.filter((_item, index) => index >= pageStart && index < pageEnd);
    byId("opportunitySortSummary").textContent = `${sortingLabels[sortMode]}；当前第 ${directoryPage + 1} / ${pageCount} 页，每页 ${directoryPageSize} 个。`;
    byId("opportunityPageMeta").textContent = `第 ${directoryPage + 1} / ${pageCount} 页 · 每页 ${directoryPageSize} 个`;
    byId("opportunityPreviousPage").disabled = directoryPage === 0;
    byId("opportunityNextPage").disabled = directoryPage >= pageCount - 1;
    byId("opportunityDirectoryList").innerHTML = pageItems.length
      ? pageItems.map((item, index) => directoryCard(item, pageStart + index + 1)).join("")
      : '<div class="opportunity-empty"><strong>当前筛选没有项目</strong><p>可以切换行动阶段或清除其他筛选条件。</p></div>';
  }

  function renderMethod() {
    byId("rankingBoundary").textContent = state.boundary;
    byId("rankingComponents").innerHTML = state.publicRanking.components.map(
      (component) => `
        <article>
          <span>${escapeHtml(component.label)}</span>
          <strong>${component.maximum}分</strong>
          <small>${escapeHtml({
            actionReadiness: "当前规则动作与执行准备",
            remainingConvexity: "赔率是否仍有非线性空间",
            ignitionProximity: "点火条件距离兑现多远",
            evidenceAndMismatch: "事实证据和错配评分",
            tradeability: "流动性、卖出路径和滑点",
            riskQuality: "风险越可控，得分越高",
          }[component.key])}</small>
        </article>`,
    ).join("");
  }

  const stateControlIds = [
    "trackingRouteFilter",
    "trackingPriorityFilter",
    "trackingStatusFilter",
    "opportunityStageFilter",
    "opportunityMaturityFilter",
    "opportunityRouteFilter",
    "opportunityRiskFilter",
    "opportunityRemainingFilter",
    "opportunityIgnitionFilter",
    "opportunityTradeabilityFilter",
    "opportunitySort",
    "opportunitySearch",
  ];
  const directoryFilterIds = [
    "opportunityStageFilter",
    "opportunityMaturityFilter",
    "opportunityRouteFilter",
    "opportunityRiskFilter",
    "opportunityRemainingFilter",
    "opportunityIgnitionFilter",
    "opportunityTradeabilityFilter",
  ];

  function persistPageState() {
    if (!pageState) return;
    pageState.save("candidate-pool", {
      controls: Object.fromEntries(
        stateControlIds.map((id) => [id, byId(id).value]),
      ),
      directoryPage,
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

  function renderAndPersist() {
    renderDirectory();
    renderFilterContext();
    persistPageState();
  }

  function resetDirectoryFilters() {
    directoryFilterIds.forEach((id) => {
      byId(id).value = "all";
    });
    byId("opportunitySort").value = "attention";
    byId("opportunitySearch").value = "";
    directoryPage = 0;
  }

  function applyDirectoryContext(controlId, value) {
    resetDirectoryFilters();
    byId(controlId).value = value;
    renderAndPersist();
    byId("opportunityDirectory").scrollIntoView({ behavior: "smooth" });
  }

  function renderFilterContext() {
    const labels = directoryFilterIds.flatMap((id) => {
      const control = byId(id);
      if (control.value === "all") return [];
      const field = control.closest("label")?.querySelector("span")?.textContent || "筛选";
      const value = control.selectedOptions[0]?.textContent || control.value;
      return [`${field}：${value}`];
    });
    const search = byId("opportunitySearch").value.trim();
    if (search) labels.push(`查找：${search}`);
    byId("opportunityFilterContext").innerHTML = labels.length
      ? `<strong>当前筛选</strong>${labels.map((label) => `<span>${escapeHtml(label)}</span>`).join("")}`
        + '<button id="opportunityClearContext" type="button">清除全部筛选</button>'
      : "<strong>当前未附加筛选</strong><span>展示全部机器项目</span>";
    byId("opportunityClearContext")?.addEventListener("click", () => {
      resetDirectoryFilters();
      renderAndPersist();
    });
  }

  if (pageMode === "library") {
    document.body.classList.add("c18-library-mode");
    addStageOptions();
    restorePageControls();
    renderDirectory();
    renderFilterContext();
    renderMethod();
    [
      "opportunityStageFilter",
      "opportunityMaturityFilter",
      "opportunityRouteFilter",
      "opportunityRiskFilter",
      "opportunityRemainingFilter",
      "opportunityIgnitionFilter",
      "opportunityTradeabilityFilter",
      "opportunitySort",
    ].forEach((id) => byId(id).addEventListener("change", () => {
      directoryPage = 0;
      renderAndPersist();
    }));
    byId("opportunitySearch").addEventListener("input", () => {
      directoryPage = 0;
      renderAndPersist();
    });
    byId("opportunityPreviousPage").addEventListener("click", () => {
      directoryPage = Math.max(0, directoryPage - 1);
      renderAndPersist();
    });
    byId("opportunityNextPage").addEventListener("click", () => {
      directoryPage += 1;
      renderAndPersist();
    });
    byId("opportunityResetFilters").addEventListener("click", () => {
      resetDirectoryFilters();
      renderAndPersist();
    });
  }
  if (restoredPageState.savedAt) {
    pageState?.notify("已恢复机会中心的筛选和阅读位置");
  }
  pageState?.restoreScroll(restoredPageState, { skipWhenHash: true });
  window.requestAnimationFrame(syncActiveSection);
  let scrollSaveTimer = 0;
  window.addEventListener("scroll", () => {
    clearTimeout(scrollSaveTimer);
    scrollSaveTimer = setTimeout(persistPageState, 160);
  }, { passive: true });
  window.addEventListener("pagehide", persistPageState);
}());
