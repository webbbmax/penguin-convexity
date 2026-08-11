(function convexityProjectDetailApp() {
  const state = window.PENGUIN_CONVEXITY_PROJECT_DETAILS;
  const opportunityState = window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER;
  const routeState = window.PENGUIN_CONVEXITY_RESEARCH_ROUTES;
  const trackingState = window.PENGUIN_CONVEXITY_TRACKING_TASKS;
  const opportunityByCaseId = new Map(
    (opportunityState?.cases || []).map((item) => [item.caseId, item.opportunityStage]),
  );
  const routeByMasterId = new Map(
    (routeState?.records || []).map((item) => [item.masterId, item]),
  );
  const trackingByCaseId = new Map(
    (trackingState?.tasks || []).map((item) => [item.caseId, item]),
  );
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
  const dateTime = (value) => {
    if (!value) return "--";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const number = (value) => value == null
    ? "--"
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(Number(value));
  const money = (value) => {
    if (value == null) return "--";
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: "USD",
      notation: Math.abs(Number(value)) >= 1000000 ? "compact" : "standard",
      maximumFractionDigits: Math.abs(Number(value)) < 1 ? 6 : 2,
    }).format(Number(value));
  };
  const pct = (value) => value == null ? "--" : `${number(value)}%`;
  const valueOrPending = (value, fallback = "待核验") => {
    const text = String(value == null ? "" : value).trim();
    return text && text !== "unknown" ? text : fallback;
  };
  const stateLabels = {
    shadow_signal: "影子信号",
    identity_pending: "身份待核验",
    tradeability_pending: "交易性待核验",
    active_embryo: "正式胚胎",
    priority_watch: "重点观察",
    extreme_test: "极限试仓",
    trial_ready: "可试仓",
    igniting: "正在点火",
    odds_decay: "赔率衰减",
    invalidated: "逻辑失效",
    transferred_l5: "转入 L5 管理",
    archived: "已归档",
  };
  const statusLabels = {
    verified: "已核验",
    corroborated: "交叉印证",
    pending: "待核验",
    conflict: "身份冲突",
    rejected: "已排除",
    pass: "通过",
    fail: "失败",
    not_checked: "未检查",
    read_only_verified: "只读卖出路径已核验",
    blocked: "阻断",
    unknown: "待核验",
    match: "匹配",
    mismatch: "不匹配",
    accessible: "可访问",
    restricted: "访问受限",
    missing: "缺失",
    failed: "失败",
    low: "低",
    medium: "中",
    high: "高",
    none: "无",
    limited: "受限",
    standard: "标准",
    extreme: "极限",
    untradeable: "不可交易",
    immediate: "临近",
    near: "较近",
    forming: "形成中",
    distant: "较远",
  };
  const observeFallback = {
    finalActionCategory: "observe",
    finalActionLabel: "只观察",
    finalActionReason: "尚未取得最新统一动作，旧数据库动作仅保留历史，不生成当前行动结论。",
    blockerLabel: "统一动作待刷新",
  };
  const decisionForCase = (caseItem) => (
    caseItem?.case_id ? opportunityByCaseId.get(caseItem.case_id) || observeFallback : observeFallback
  );
  const routeForRecord = (record) => routeByMasterId.get(record?.master?.masterId) || {
    routeId: "hybrid",
    routeLabel: "潜力项目",
    routeReason: "生命周期分类快照待刷新。",
    routeSourceLabel: "系统待刷新",
    researchFocusLabel: "潜力项目",
    researchFocusReason: "研究重点快照待刷新。",
    researchFocusSourceLabel: "系统待刷新",
    primaryFocus: "同时补齐基础档案与前置信号。",
    checklist: [],
    foundationProfile: [],
    preSignals: [],
    foundationCompleteCount: 0,
    foundationTotal: 9,
    preSignalCount: 0,
    preSignalTotal: 8,
    layoutPriority: "balanced",
    layoutReason: "生命周期分类快照待刷新，暂按两个视角并列展示。",
    completeCount: 0,
    totalChecks: 0,
    nextEvidence: "刷新项目分类",
    boundary: "项目类别只决定先查什么，不直接改变当前动作。",
  };

  if (!state || !state.order?.length) {
    byId("detailContent").innerHTML = '<p class="empty-feedback">项目详情数据尚未生成。</p>';
    return;
  }

  const publicOrder = state.order.filter((masterId) => state.records[masterId]);
  const query = new URLSearchParams(location.search);
  const requestedId = query.get("id");
  const openedFromQueue = query.get("from") === "queue";
  const activeId = publicOrder.includes(requestedId) ? requestedId : publicOrder[0];
  const active = state.records[activeId];
  const switcher = byId("detailProjectSwitcher");
  switcher.innerHTML = publicOrder.map((masterId) => {
    const item = state.records[masterId];
    return `<option value="${escapeHtml(masterId)}">${escapeHtml(item.master.name)}${item.master.symbol ? ` (${escapeHtml(item.master.symbol)})` : ""}</option>`;
  }).join("");
  switcher.value = activeId;
  switcher.addEventListener("change", () => {
    location.href = `project-detail.html?id=${encodeURIComponent(switcher.value)}${openedFromQueue ? "&from=queue" : ""}`;
  });
  if (openedFromQueue) {
    byId("detailBackLink").href = "project-master-pool.html";
    byId("detailBackLink").textContent = "返回项目队列";
  } else if (document.referrer.includes("view=library")) {
    byId("detailBackLink").href = "candidate-pool.html?view=library#opportunityDirectory";
    byId("detailBackLink").textContent = "返回项目库";
  }
  byId("detailBreadcrumb").textContent = active.master.name;
  document.title = `${active.master.name}｜凸性项目详情｜企鹅投研`;

  function metric(label, value, note) {
    return `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`;
  }

  function detailHero(item, typeLabel, description, action, status, actionReason) {
    return `
      <section class="convexity-detail-hero">
        <div>
          <span>${escapeHtml(typeLabel)}</span>
          <h1>${escapeHtml(item.name)}${item.symbol ? ` <small>${escapeHtml(item.symbol)}</small>` : ""}</h1>
          <p>${escapeHtml(description)}</p>
        </div>
        <aside>
          <span>当前动作</span>
          <strong>${escapeHtml(action)}</strong>
          <small>${escapeHtml(`${status} · ${actionReason}`)}</small>
        </aside>
      </section>
    `;
  }

  function renderScore(score) {
    if (!score) {
      return '<p class="detail-empty">尚未形成事实新闻错配评分。没有评分时不生成投资结论。</p>';
    }
    const rows = [
      ["事实确定性", score.fact_certainty, 20],
      ["经济增量", score.economic_increment, 20],
      ["代币价值捕获", score.value_capture, 25],
      ["事件临近程度", score.event_proximity, 20],
      ["价格尚未反应", score.price_unreacted, 15],
    ];
    return `
      <div class="detail-score-total"><span>错配总分</span><strong>${score.total_score}</strong><small>风险扣分 ${score.risk_deduction}</small></div>
      <div class="detail-score-bars">
        ${rows.map(([label, value, maximum]) => `
          <div><span>${label}</span><i><b style="width:${Math.max(0, Math.min(100, Number(value) / maximum * 100))}%"></b></i><strong>${value}/${maximum}</strong></div>
        `).join("")}
      </div>
      ${score.deduction_detail?.length ? `<ul class="detail-plain-list">${score.deduction_detail.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("")}</ul>` : ""}
    `;
  }

  function renderMachineResearchScore(score) {
    if (!score) {
      return '<p class="detail-empty">机器证据评分尚未运行。可在凸性工作台的更新中心单独运行“机器证据与凸性评分”。</p>';
    }
    const confidenceLabels = {
      high: "高",
      medium: "中",
      low: "低",
      insufficient: "资料不足",
    };
    const evidence = score.dimensionScores?.evidenceQuality || {};
    const readiness = score.dimensionScores?.convexityReadiness || {};
    const dimensions = [
      ["基础档案", evidence.foundation],
      ["前置信号", evidence.preSignals],
      ["市场与退出", evidence.marketExit],
      ["信源可信度", evidence.sourceConfidence],
      ["身份与交易性", readiness.identityAndTradeability],
      ["硬证据", readiness.hardEvidence],
      ["价值捕获", readiness.valueCapture],
      ["凸性结构", readiness.convexityStructure],
    ].filter(([, item]) => item);
    const sourceLink = /^https?:\/\//i.test(score.source_url || "")
      ? `<a href="${escapeHtml(score.source_url)}" target="_blank" rel="noreferrer">查看首条原始依据</a>`
      : "<span>原始依据已在下方证据区逐条保存</span>";
    return `
      <article class="machine-research-score">
        <header>
          <div>
            <span>${escapeHtml(score.lifecycle_label)} · 证据置信度 ${escapeHtml(confidenceLabels[score.confidence] || score.confidence)}</span>
            <strong>机器研究评分</strong>
            <p>${escapeHtml(score.scoring_boundary)}</p>
          </div>
          <small>${escapeHtml(score.rule_version)}</small>
        </header>
        <div class="machine-score-metrics">
          <section><span>证据质量</span><strong>${escapeHtml(score.evidence_quality_score)}</strong><small>资料是否足以继续研究</small></section>
          <section><span>事实新闻错配</span><strong>${escapeHtml(score.mismatch_score)}</strong><small>仅用于研究优先级</small></section>
          <section><span>凸性准备度</span><strong>${escapeHtml(score.convexity_readiness_score)}</strong><small>不是收益概率</small></section>
        </div>
        <div class="machine-score-dimensions">
          ${dimensions.map(([label, item]) => `
            <div>
              <span>${escapeHtml(label)}</span>
              <i><b style="width:${Math.max(0, Math.min(100, Number(item.score) / Number(item.maximum || 1) * 100))}%"></b></i>
              <strong>${escapeHtml(item.score)}/${escapeHtml(item.maximum)}</strong>
            </div>
          `).join("")}
        </div>
        <div class="machine-score-blockers">
          <strong>当前阻断项 ${score.blockers?.length || 0}</strong>
          ${score.blockers?.length
            ? `<ul>${score.blockers.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
            : "<p>当前无结构化阻断项，但仍需由下一阶段机器状态规则决定是否可行动。</p>"}
          <footer>${sourceLink}<span>评分时间 ${escapeHtml(dateTime(score.scored_at))}</span></footer>
        </div>
      </article>
    `;
  }

  function renderMachineConclusion(conclusion) {
    if (!conclusion) {
      return '<p class="detail-empty">机器结论尚未发布。可在凸性工作台的更新中心单独运行“机器状态与结论发布”。</p>';
    }
    const upgradeConditions = conclusion.upgradeConditions || [];
    const invalidationConditions = conclusion.invalidationConditions || [];
    const taskLink = conclusion.next_task_id
      ? `<a href="update-center.html?task=${encodeURIComponent(conclusion.next_task_id)}">运行下一项自动任务</a>`
      : "<span>当前没有待运行的自动任务</span>";
    const sourceLink = /^https?:\/\//i.test(conclusion.source_url || "")
      ? `<a href="${escapeHtml(conclusion.source_url)}" target="_blank" rel="noreferrer">查看首条结论依据</a>`
      : "<span>结论依据已保存在证据区和状态历史</span>";
    return `
      <article class="machine-conclusion">
        <header>
          <div>
            <span>${escapeHtml(conclusion.conclusion_state_label)}</span>
            <strong>${escapeHtml(conclusion.action_label)}</strong>
          </div>
          <small>${escapeHtml(conclusion.rule_version)}</small>
        </header>
        <h3>${escapeHtml(conclusion.headline)}</h3>
        ${conclusion.why_not_actionable
          ? `<section class="machine-conclusion-blocker"><span>为什么现在不能行动</span><p>${escapeHtml(conclusion.why_not_actionable)}</p></section>`
          : ""}
        <section class="machine-conclusion-next">
          <span>下一项自动任务</span>
          <p>${escapeHtml(conclusion.next_step || "当前没有待运行的自动任务。")}</p>
          ${taskLink}
        </section>
        <div class="machine-conclusion-conditions">
          <section>
            <strong>升级条件</strong>
            ${upgradeConditions.length
              ? `<ul>${upgradeConditions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
              : "<p>当前没有升级条件。</p>"}
          </section>
          <section>
            <strong>失效条件</strong>
            ${invalidationConditions.length
              ? `<ul>${invalidationConditions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
              : "<p>当前没有失效条件。</p>"}
          </section>
        </div>
        <footer>
          ${sourceLink}
          <span>发布时间 ${escapeHtml(dateTime(conclusion.generated_at))}</span>
          <span>人工复核可选，不阻断发布；系统不自动交易</span>
        </footer>
      </article>
    `;
  }

  function renderCatalystTradePath(path) {
    if (!path) {
      return '<p class="detail-empty">催化交易路径尚未生成。可在凸性工作台单独运行“催化交易路径”。</p>';
    }
    const steps = (path.transmissionSteps || []).map((step, index) => `
      <li class="${escapeHtml(step.status)}">
        <b>${index + 1}</b>
        <div><strong>${escapeHtml(step.label)}</strong><p>${escapeHtml(step.detail)}</p></div>
        <span>${step.status === "verified" ? "已有依据" : "待补齐"}</span>
      </li>
    `).join("");
    const blockers = path.blockers?.length
      ? `<ul>${path.blockers.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : "<p>当前无结构化阻断项。</p>";
    const sourceLink = /^https?:\/\//i.test(path.catalyst_source_url || "")
      ? `<a href="${escapeHtml(path.catalyst_source_url)}" target="_blank" rel="noreferrer">打开催化原始来源</a>`
      : "<span>当前没有可打开的催化原始来源</span>";
    const observed = path.observed_exit_notional_usd == null
      ? "待核验"
      : `${money(path.observed_exit_notional_usd)} · ${path.observed_exit_slippage_pct == null ? "滑点待核验" : `${Number(path.observed_exit_slippage_pct).toFixed(2)}%`}`;
    const modeled = path.modeled_exit_slippage_pct == null
      ? "待估算"
      : `${Number(path.modeled_exit_slippage_pct).toFixed(2)}%`;
    return `
      <article class="detail-catalyst-path">
        <header>
          <div><span>${escapeHtml(path.path_stage_label)}</span><strong>${escapeHtml(path.catalyst_summary)}</strong></div>
          <small>${escapeHtml(path.rule_version)}</small>
        </header>
        <ol class="catalyst-transmission-steps">${steps}</ol>
        <div class="detail-catalyst-exit">
          <section><span>表达资产</span><strong>${escapeHtml(path.expression_asset_text || "待确认")}</strong><small>${escapeHtml(path.network_name || "网络待确认")} · ${escapeHtml(path.venue_text || "交易场所待确认")}</small></section>
          <section><span>实际只读核验</span><strong>${escapeHtml(observed)}</strong><small>按数据库已有核验金额显示</small></section>
          <section><span>2万美元理论估算</span><strong>${escapeHtml(modeled)}</strong><small>恒定乘积近似，不是实际成交</small></section>
        </div>
        <section class="detail-catalyst-blockers"><strong>当前断点</strong>${blockers}</section>
        <footer>
          ${sourceLink}
          <a href="update-center.html?task=${encodeURIComponent(path.next_task_id)}">运行下一项机器任务</a>
          <a href="catalyst-paths.html">查看全部项目路径</a>
        </footer>
      </article>
    `;
  }

  function renderMonitoringInfrastructure(profile) {
    if (!profile) {
      return '<p class="detail-empty">项目监控基础设施尚未生成。</p>';
    }
    const collectionLabels = {
      ready: "自动采集已接通",
      registered: "已登记待适配",
      blocked: "身份阻断",
      conflict: "归属冲突",
    };
    const targets = profile.targets?.length
      ? profile.targets.map((item) => {
          const targetName = item.targetUrl
            ? `<a href="${escapeHtml(item.targetUrl)}" target="_blank" rel="noreferrer">${escapeHtml(item.targetValue)}</a>`
            : `<strong>${escapeHtml(item.targetValue)}</strong>`;
          const trace = [
            item.rawEventId ? "原始记录已连接" : "原始记录待连接",
            item.evidenceId ? "研究证据已连接" : "研究证据待连接",
          ].join(" · ");
          return `
            <article class="monitoring-target status-${escapeHtml(item.collectionStatus)}">
              <header><span>${escapeHtml(item.targetTypeLabel)}</span><b>${escapeHtml(collectionLabels[item.collectionStatus] || item.collectionStatus)}</b></header>
              ${targetName}
              <p>${escapeHtml(item.verificationMethod || item.gapReason || "等待归属核验")}</p>
              <small>${escapeHtml(trace)}${item.sourceId ? ` · ${escapeHtml(item.sourceId)}` : ""}</small>
            </article>
          `;
        }).join("")
      : '<p class="detail-empty">当前项目尚未登记可用监控目标。</p>';
    const gaps = profile.gaps?.length
      ? `<ul>${profile.gaps.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : "<p>当前基础设施字段无缺口；仍需等待真实事实与催化出现。</p>";
    return `
      <article class="detail-monitoring-infrastructure">
        <header>
          <div><span>${escapeHtml(profile.statusLabel)}</span><strong>${escapeHtml(profile.readyCount)} / ${escapeHtml(profile.targetCount)} 个目标可自动采集</strong></div>
          <a href="monitoring-infrastructure.html?project=${encodeURIComponent(profile.projectId)}">查看全站监控注册表</a>
        </header>
        <div class="monitoring-target-grid">${targets}</div>
        <section class="monitoring-gaps"><strong>当前基础设施缺口</strong>${gaps}</section>
      </article>
    `;
  }

  function renderWeakSignals(signals, projectId) {
    if (!signals?.length) {
      return `
        <article class="detail-weak-signals">
          <p class="detail-empty">当前没有归属到该项目的弱线索。系统仍会继续从项目目录、公开代码、治理和链上入口发现。</p>
          <a href="weak-signal-inbox.html?project=${encodeURIComponent(projectId)}">打开全站弱线索收件箱</a>
        </article>
      `;
    }
    const ready = signals.filter((item) => item.triageStatus === "ready_for_corroboration").length;
    const discoveryOnly = signals.filter((item) => item.triageStatus === "discovery_only").length;
    return `
      <article class="detail-weak-signals">
        <header>
          <div><span>发现线索 ${signals.length}</span><strong>${ready} 条可进入补证 · ${discoveryOnly} 条仅供发现</strong></div>
          <a href="weak-signal-inbox.html?project=${encodeURIComponent(projectId)}">查看完整收件箱</a>
        </header>
        <div>
          ${signals.map((item) => `
            <section class="status-${escapeHtml(item.triageStatus)}">
              <header><span>${escapeHtml(item.signalTypeLabel)}</span><b>${escapeHtml(item.triageLabel)}</b></header>
              <strong>${escapeHtml(item.title)}</strong>
              <p>${escapeHtml(item.summary || "当前只有发现记录。")}</p>
              <small>${escapeHtml(item.sourceName)} · ${escapeHtml(item.sourceTierLabel)} · 推广偏差 ${escapeHtml({ low: "低", medium: "中", high: "高" }[item.promotionBias] || item.promotionBias)}</small>
              <footer>
                <span>${escapeHtml(item.upgradeRequirement)}</span>
                ${item.sourceUrl ? `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">原始入口</a>` : ""}
              </footer>
            </section>
          `).join("")}
        </div>
        <p>边界：弱线索只用于扩大召回和安排补证，不直接参与评分、结论或行动。</p>
      </article>
    `;
  }

  function renderAutomaticProfile(profile) {
    if (!profile) {
      return '<p class="detail-empty">自动档案质量快照尚未生成。</p>';
    }
    const profileStatusLabels = {
      verified: "已核验",
      available: "已有资料",
      stale: "资料较旧",
      pending: "待核验",
      missing: "缺失",
      conflict: "冲突",
    };
    const critical = profile.missingCritical?.length
      ? profile.missingCritical.map((item) => `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(profileStatusLabels[item.status] || item.status)} · 由“${escapeHtml(item.nextTaskLabel || "自动补齐")}”处理</span></li>`).join("")
      : "<li><strong>关键字段无阻断</strong><span>仍需独立判断凸性来源、赔率、风险与退出条件。</span></li>";
    return `
      <article class="automatic-profile grade-${escapeHtml(profile.grade)}">
        <header>
          <div>
            <span>自动结构化档案 · ${escapeHtml(profile.version)}</span>
            <strong>${escapeHtml(profile.gradeLabel)}</strong>
            <p>仅使用接口、官方来源、链上记录和规则计算生成，不接收个性化手写结论。</p>
          </div>
          <div class="automatic-profile-score"><strong>${escapeHtml(profile.score)}</strong><small>/100</small></div>
        </header>
        <div class="automatic-profile-sections">
          ${(profile.sections || []).map((section) => `
            <section>
              <header><strong>${escapeHtml(section.label)}</strong><span>${escapeHtml(section.score)}/${escapeHtml(section.maxScore)}</span></header>
              <i><b style="width:${Math.max(0, Math.min(100, Number(section.score) / Number(section.maxScore || 1) * 100))}%"></b></i>
              <small>${escapeHtml(section.complete)}/${escapeHtml(section.total)} 项已有可用资料</small>
            </section>
          `).join("")}
        </div>
        <div class="automatic-profile-fields">
          ${(profile.sections || []).map((section) => `
            <section>
              <h3>${escapeHtml(section.label)}</h3>
              ${(section.fields || []).map((item) => `
                <article class="status-${escapeHtml(item.status)}">
                  <div><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(profileStatusLabels[item.status] || item.status)}</span></div>
                  <p>${escapeHtml(item.value || "尚未自动抓取")}</p>
                  <footer>
                    <small>${escapeHtml(item.sourceName || item.nextTaskLabel || "等待自动来源")}${item.updatedAt ? ` · ${escapeHtml(dateTime(item.updatedAt))}` : ""}</small>
                    ${item.sourceUrl
                      ? `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">原始来源</a>`
                      : item.nextTaskId
                        ? `<a href="update-center.html?task=${encodeURIComponent(item.nextTaskId)}">运行自动补齐</a>`
                        : ""}
                  </footer>
                </article>
              `).join("")}
            </section>
          `).join("")}
        </div>
        <div class="automatic-profile-gaps">
          <div><strong>关键缺失</strong><ul>${critical}</ul></div>
          <div>
            <strong>下一项自动补齐</strong>
            <p>${escapeHtml(profile.nextAutoTask?.fieldLabel ? `补齐“${profile.nextAutoTask.fieldLabel}”` : "暂无关键补齐任务")}</p>
            ${profile.nextAutoTask?.href
              ? `<a href="${escapeHtml(profile.nextAutoTask.href)}">${escapeHtml(profile.nextAutoTask.taskLabel || "进入更新中心")}</a>`
              : `<small>${escapeHtml(profile.nextAutoTask?.taskLabel || "等待下一轮自动更新")}</small>`}
          </div>
        </div>
        <p class="automatic-profile-boundary">${escapeHtml(profile.boundary)}</p>
      </article>
    `;
  }

  function renderConvexityLogic(caseItem) {
    const review = caseItem?.convexityReview;
    const cards = [
      ["主凸性来源", review?.primary_convexity_source || caseItem?.convexity_source],
      ["最大可控亏损", review?.maximum_controllable_loss],
      ["非线性上行路径", review?.nonlinear_upside_path],
      ["点火条件", review?.ignition_conditions],
      ["赔率衰减", review?.odds_decay_conditions],
      ["失效条件与窗口", [caseItem?.invalidation, review?.invalidation_window].filter(Boolean).join("；")],
    ];
    return `<div class="convexity-logic-grid">${cards.map(([label, value]) => `
      <article><span>${label}</span><p>${escapeHtml(valueOrPending(value, "尚未形成可核验结论"))}</p></article>
    `).join("")}</div>`;
  }

  function renderResearchRoute(route) {
    return `
      <article class="detail-research-route route-${escapeHtml(route.routeId)}">
        <header>
          <div><span>${escapeHtml(route.routeSourceLabel)}</span><h3>${escapeHtml(route.routeLabel)}</h3></div>
          <strong>${route.completeCount}/${route.totalChecks}</strong>
        </header>
        <p>${escapeHtml(route.routeReason)}</p>
        <aside><strong>研究重点 · ${escapeHtml(route.researchFocusLabel || route.routeLabel)}</strong><span>${escapeHtml(route.primaryFocus)}</span></aside>
        ${route.researchFocusSource === "manual_override" ? `<p>人工调整原因：${escapeHtml(route.researchFocusReason)}</p>` : ""}
        <div class="detail-route-layout-note">
          <strong>页面为什么这样排</strong>
          <span>${escapeHtml(route.layoutReason || "按当前项目类别安排阅读优先级。")}</span>
        </div>
        <footer><strong>下一项补证</strong><span>${escapeHtml(route.nextEvidence)}</span></footer>
        <small>${escapeHtml(route.boundary)}</small>
      </article>
    `;
  }

  function renderTrackingTask(task) {
    if (!task) {
      return '<p class="detail-empty">当前项目的自动跟踪任务尚未生成。刷新数据后会根据项目类别、行动阻断和失效条件自动建立。</p>';
    }
    const execution = task.latestExecution;
    const decisionReview = task.decisionReview || execution?.decisionReview;
    const followUp = task.decisionFollowUp;
    const sourceStatusLabels = {
      success: "完成",
      partial_success: "部分完成",
      failed: "失败",
      no_change: "无新增",
      not_run: "本轮未运行",
      restricted: "访问受限",
    };
    return `
      <article class="detail-tracking-task priority-${escapeHtml(task.priority)}">
        <header>
          <div><span>${escapeHtml(task.projectCategoryLabel)} · ${escapeHtml(task.taskTypeLabel)}</span><h3>${escapeHtml(task.title)}</h3></div>
          <div><b>${escapeHtml(task.priority)}</b><em>${escapeHtml(task.statusLabel)}</em></div>
        </header>
        <div class="detail-tracking-lead">
          <span>下一步</span>
          <strong>${escapeHtml(task.nextStep)}</strong>
          <small>下次复查：${escapeHtml(dateTime(task.nextReviewAt))} · 每${number(task.reviewCadenceDays)}天检查一次</small>
        </div>
        ${followUp?.required ? `
          <section class="detail-follow-up status-${escapeHtml(followUp.status)}">
            <header><span>结论二次验证</span><strong>${escapeHtml(followUp.statusLabel)}</strong></header>
            <p>${escapeHtml(task.nextStep)}</p>
            <small>${escapeHtml(followUp.typeLabel)} · 计划 ${escapeHtml(dateTime(followUp.dueAt))}</small>
            ${followUp.reviewNote ? `<small>复核备注：${escapeHtml(followUp.reviewNote)}</small>` : ""}
            ${["pending", "failed"].includes(followUp.status) && task.status === "due"
              ? `<a href="update-center.html?task=tracking_task_refresh">前往执行二次验证</a>`
              : ""}
          </section>
        ` : ""}
        <ol>${task.checklist.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>
        <div class="detail-tracking-grid">
          <section><span>为什么现在做</span><p>${escapeHtml(task.whyNow)}</p></section>
          <section><span>证据目标</span><p>${escapeHtml(task.evidenceTarget)} · 当前 ${number(task.evidenceCompleteCount)}/${number(task.evidenceTotal)}</p></section>
          <section><span>建议检查来源</span><p>${task.suggestedSources.map(escapeHtml).join("、")}</p></section>
          <section><span>当前结论</span><p>${escapeHtml(task.currentConclusion)}</p></section>
        </div>
        ${execution ? `
          <section class="detail-tracking-execution status-${escapeHtml(execution.execution_status)}">
            <header><span>最近自动执行</span><strong>${escapeHtml(execution.statusLabel)} · ${escapeHtml(execution.decisionLabel)}</strong></header>
            <p>${escapeHtml(execution.reason)}</p>
            <small>检查${number(execution.sourceResults.length)}个映射信源 · 记录${number(execution.findings_count)}条 · 新增或变化${number(execution.new_findings_count)}条 · ${escapeHtml(dateTime(execution.finished_at))}</small>
            <div class="detail-tracking-source-results">
              ${(execution.sourceResults || []).map((item) => `
                <span class="status-${escapeHtml(item.status)}">
                  <b>${escapeHtml(item.sourceName)}</b>
                  <small>${escapeHtml(sourceStatusLabels[item.status] || item.status)} · 采集${number(item.collectedCount)} · 匹配${number(item.matchedCount)}</small>
                </span>
              `).join("")}
            </div>
            ${(execution.findings || []).length ? `
              <div class="detail-tracking-findings">
                <strong>本轮发现的依据</strong>
                ${(execution.findings || []).map((item) => `
                  <article>
                    <span>${item.isNew ? "新增或变化" : "重复确认"} · ${escapeHtml(item.sourceName)}</span>
                    <p>${escapeHtml(item.summary)}</p>
                    <small>${escapeHtml(dateTime(item.observedAt || item.collectedAt))}</small>
                    ${item.sourceUrl ? `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">查看原始来源</a>` : ""}
                  </article>
                `).join("")}
              </div>
            ` : '<p class="detail-tracking-no-findings">本轮没有发现足以改变当前结论的新证据。</p>'}
            ${decisionReview?.required ? `
              <div class="detail-decision-review status-${escapeHtml(decisionReview.status)}">
                <div><span>结论复核</span><strong>${escapeHtml(decisionReview.statusLabel)}</strong></div>
                ${decisionReview.status === "pending"
                  ? '<a href="change-explanations.html?review=pending">前往复核</a>'
                  : `<small>${decisionReview.note ? escapeHtml(decisionReview.note) : "复核结果已写入状态历史。"}</small>`}
              </div>
            ` : ""}
          </section>
        ` : '<section class="detail-tracking-execution is-pending"><header><span>最近自动执行</span><strong>尚未到期执行</strong></header><p>到达复查时间后由一键更新自动检查，并在这里回写结果。</p></section>'}
        <footer>
          <p><strong>升级条件</strong>${escapeHtml(task.upgradeCondition)}</p>
          <p><strong>停止条件</strong>${escapeHtml(task.stopCondition)}</p>
        </footer>
      </article>
    `;
  }

  function renderResearchItems(items, emptyText) {
    if (!items?.length) {
      return `<p class="detail-empty">${escapeHtml(emptyText)}</p>`;
    }
    return `<div class="detail-research-items">${items.map((item) => {
      const available = item.status === "available";
      const sourceMeta = [item.sourceName, item.observedAt ? dateTime(item.observedAt) : ""]
        .filter(Boolean)
        .join(" · ");
      return `
        <article class="detail-research-item ${available ? "is-available" : "is-pending"}">
          <header>
            <span>${available ? "已有资料" : "待补资料"}</span>
            <strong>${escapeHtml(item.label)}</strong>
          </header>
          <p>${available ? escapeHtml(item.evidence || "资料已记录，摘要待补。") : "尚未发现足以核验的资料。"}</p>
          <footer>
            <small>${available ? escapeHtml(sourceMeta || item.factBoundary || "来源信息待补") : "不会用推测自动补齐"}</small>
            ${available && item.sourceUrl ? `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">查看来源</a>` : ""}
          </footer>
        </article>
      `;
    }).join("")}</div>`;
  }

  function foundationSection(route) {
    return `
      <section id="detailFoundation" class="detail-section detail-priority-section">
        <header>
          <div><span>FOUNDATION PROFILE</span><h2>基础档案</h2></div>
          <strong>${route.foundationCompleteCount || 0}/${route.foundationTotal || 9}</strong>
        </header>
        <p class="detail-section-intro">核验官网、X、GitHub、文档、代币经济、合约、流动性、团队和审计。资料达到最低线只表示值得继续研究，不构成建仓结论。</p>
        ${renderResearchItems(route.foundationProfile, "基础档案快照待生成。")}
      </section>
    `;
  }

  function preSignalSection(route) {
    return `
      <section id="detailPreSignals" class="detail-section detail-priority-section">
        <header>
          <div><span>PRE-NEWS SIGNALS</span><h2>新闻前置信号</h2></div>
          <strong>${route.preSignalCount || 0}/${route.preSignalTotal || 8}</strong>
        </header>
        <p class="detail-section-intro">追踪治理、代码发布、合约部署、产品升级、链上数据、监管、机构动作和代币经济调整。信号只提高研究优先级，仍需验证价格反应、风险、交易性和价值捕获。</p>
        ${renderResearchItems(route.preSignals, "前置信号快照待生成。")}
      </section>
    `;
  }

  function renderRoutePrioritySections(route) {
    const foundation = foundationSection(route);
    const signals = preSignalSection(route);
    if (route.layoutPriority === "signals_first") return signals + foundation;
    if (route.layoutPriority === "foundation_first") return foundation + signals;
    return `
      <div class="detail-balanced-priorities">
        ${foundation}
        ${signals}
      </div>
    `;
  }

  function routeAnchorLinks(route) {
    if (route.layoutPriority === "signals_first") {
      return '<a href="#detailPreSignals">前置信号</a><a href="#detailFoundation">基础档案</a>';
    }
    return '<a href="#detailFoundation">基础档案</a><a href="#detailPreSignals">前置信号</a>';
  }

  function renderEvidence(evidence) {
    if (!evidence.length) {
      return '<p class="detail-empty">尚未录入项目级证据。页面保留缺口，不用推测补齐。</p>';
    }
    return `<div class="detail-evidence-list">${evidence.map((item) => `
      <article>
        <header><span>${escapeHtml(item.fact_boundary)}</span><strong>${escapeHtml(item.stance)}</strong><time>${escapeHtml(dateTime(item.observed_at))}</time></header>
        <p>${escapeHtml(item.summary)}</p>
        <footer><small>${escapeHtml(item.source_name || item.source_id || "来源待核验")}</small>${item.source_url ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">查看原始来源</a>` : ""}</footer>
      </article>
    `).join("")}</div>`;
  }

  function renderAssets(assets) {
    if (!assets.length) {
      return '<p class="detail-empty">尚未建立可交易资产。项目存在不等于存在可购买代币。</p>';
    }
    return assets.map((asset) => {
      const market = asset.latestMarket;
      const contracts = asset.contracts.length
        ? asset.contracts.map((contract) => `
            <tr>
              <td>${escapeHtml(contract.network_name)}</td>
              <td>${escapeHtml(contract.contract_standard)}</td>
              <td><code>${escapeHtml(contract.contract_address)}</code></td>
              <td>${escapeHtml(statusLabels[contract.identity_status] || contract.identity_status)}</td>
              <td>${contract.explorer_url ? `<a href="${escapeHtml(contract.explorer_url.replace(/\/$/, ""))}/address/${escapeHtml(contract.contract_address)}" target="_blank" rel="noreferrer">浏览器</a>` : "--"}</td>
            </tr>
          `).join("")
        : '<tr><td colspan="5">尚未登记链上合约。</td></tr>';
      const checks = asset.tradeability.length
        ? asset.tradeability.map((check) => `
            <tr>
              <td>${escapeHtml(check.network_name)}</td>
              <td>${escapeHtml(statusLabels[check.contract_exists_status] || check.contract_exists_status)}</td>
              <td>${escapeHtml(statusLabels[check.sell_path_status] || check.sell_path_status)}</td>
              <td>${money(check.exit_notional_usd)}</td>
              <td>${pct(check.estimated_exit_slippage_pct)}</td>
              <td>${escapeHtml(statusLabels[check.overall_status] || check.overall_status)}</td>
            </tr>
          `).join("")
        : '<tr><td colspan="6">尚未执行卖出路径和滑点核验。</td></tr>';
      return `
        <article class="detail-asset-card">
          <header>
            <div><span>可交易资产</span><h3>${escapeHtml(asset.symbol)} <small>${escapeHtml(asset.chain)}</small></h3></div>
            <strong>价值捕获 ${escapeHtml(valueOrPending(asset.capture_grade))}</strong>
          </header>
          <div class="detail-market-grid">
            ${metric("价格", money(market?.price_usd), market?.source_name || "行情待补齐")}
            ${metric("流动性", money(market?.liquidity_usd), "退出能力参考")}
            ${metric("24小时成交", money(market?.volume_24h_usd), "自然成交")}
            ${metric("流通市值", money(market?.market_cap_usd), "非估值结论")}
          </div>
          <h4>合约与所在网络</h4>
          <div class="detail-table-wrap"><table><thead><tr><th>网络</th><th>标准</th><th>合约地址</th><th>身份</th><th>核验</th></tr></thead><tbody>${contracts}</tbody></table></div>
          <h4>卖出路径与滑点</h4>
          <div class="detail-table-wrap"><table><thead><tr><th>网络</th><th>合约存在</th><th>卖出路径</th><th>模拟退出</th><th>预计滑点</th><th>结果</th></tr></thead><tbody>${checks}</tbody></table></div>
        </article>
      `;
    }).join("");
  }

  function renderAssetIdentityReview(review) {
    if (!review) {
      return '<p class="detail-empty">尚未进入机器资产身份复核。项目存在不等于存在可购买代币。</p>';
    }
    const status = statusLabels[review.resolution_status] || review.resolution_status;
    const platformCount = Object.values(review.platforms || {}).filter(Boolean).length;
    const sourceUrl = review.sourceUrl || "";
    const sourceLabel = review.coingecko_id
      ? "查看独立资产登记"
      : "查看项目登记来源";
    return `
      <article class="detail-conclusion">
        <strong>${escapeHtml(status)}</strong>
        <p>${escapeHtml(review.reason)}</p>
        <small>资产：${escapeHtml(review.symbol || "未识别")} · CoinGecko：${escapeHtml(review.coingecko_id || "未识别")} · 已登记网络 ${number(platformCount)}</small>
        <small>匹配依据：${escapeHtml(review.match_method || "当前证据不足，未自动归属")}</small>
        ${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">${sourceLabel}</a>` : ""}
      </article>
    `;
  }

  function renderTransitions(cases) {
    const transitions = cases.flatMap((item) => item.transitions.map((transition) => ({
      ...transition,
      caseTitle: item.title,
    }))).sort((a, b) => String(b.transitioned_at).localeCompare(String(a.transitioned_at)));
    if (!transitions.length) {
      return '<p class="detail-empty">尚无状态变化记录。</p>';
    }
    return `<ol class="detail-timeline">${transitions.map((item) => `
      <li><time>${escapeHtml(dateTime(item.transitioned_at))}</time><strong>${escapeHtml(stateLabels[item.from_state] || item.from_state)} → ${escapeHtml(stateLabels[item.to_state] || item.to_state)}</strong><p>${escapeHtml(item.reason)}</p><small>${escapeHtml(item.caseTitle)}</small></li>
    `).join("")}</ol>`;
  }

  function renderProject(record) {
    const item = record.master;
    const project = record.project;
    const currentCase = record.cases[0];
    const currentDecision = decisionForCase(currentCase);
    const researchRoute = routeForRecord(record);
    const trackingTask = trackingByCaseId.get(currentCase?.case_id);
    const conclusion = currentCase?.current_thesis
      || item.statusReason
      || "项目已经进入项目队列，但尚未形成凸性投资结论。";
    const metrics = [
      metric("档案完整度", `${record.automaticProfile?.score ?? "--"}/100`, record.automaticProfile?.gradeLabel || "自动档案"),
      metric("事实阶段", currentCase?.maturity_level || "--", "L0-L5"),
      metric("错配分", currentCase?.mismatchScore?.total_score ?? "--", "不是凸性分"),
      metric("剩余凸性", statusLabels[currentCase?.remaining_convexity] || valueOrPending(currentCase?.remaining_convexity), "赔率是否仍在"),
      metric("风险", statusLabels[currentCase?.risk_level] || valueOrPending(currentCase?.risk_level), "阻断优先"),
      metric("交易性", statusLabels[currentCase?.tradeability_status] || valueOrPending(currentCase?.tradeability_status), "必须能退出"),
    ].join("");
    const caseOptions = record.cases.length
      ? record.cases.map((caseItem) => {
          const decision = decisionForCase(caseItem);
          return `<li><strong>${escapeHtml(caseItem.title)}</strong><span>${escapeHtml(stateLabels[caseItem.workflow_state] || caseItem.workflow_state)} · 当前动作：${escapeHtml(decision.finalActionLabel)}</span></li>`;
        }).join("")
      : "<li>尚未建立研究案例</li>";
    byId("detailContent").innerHTML = `
      ${detailHero(
        item,
        "正式项目主体",
        conclusion,
        currentDecision.finalActionLabel,
        stateLabels[currentCase?.workflow_state] || item.statusLabel,
        currentDecision.finalActionReason
      )}
      <section class="status-grid convexity-detail-metrics">${metrics}</section>
      <nav class="detail-anchor-nav"><a href="#detailOverview">当前结论</a><a href="#detailCatalystPath">催化交易路径</a><a href="#detailMonitoringInfrastructure">监控基础设施</a><a href="#detailWeakSignals">弱线索</a><a href="#detailAutomaticProfile">自动档案</a><a href="#detailResearchRoute">项目类别</a><a href="#detailTrackingTask">跟踪任务</a>${routeAnchorLinks(researchRoute)}<a href="#detailConvexity">凸性逻辑</a><a href="#detailAssetIdentity">资产身份</a><a href="#detailAssets">资产与合约</a><a href="#detailEvidence">证据</a><a href="#detailHistory">状态历史</a></nav>
      <div class="convexity-detail-layout">
        <div>
          <section id="detailOverview" class="detail-section">
            <header><span>INVESTMENT VIEW</span><h2>当前结论</h2></header>
            ${renderMachineConclusion(currentCase?.machineConclusion)}
          </section>
          <section id="detailCatalystPath" class="detail-section">
            <header><span>CATALYST TO TRADE</span><h2>催化交易路径</h2></header>
            ${renderCatalystTradePath(currentCase?.catalystTradePath)}
          </section>
          <section id="detailMonitoringInfrastructure" class="detail-section">
            <header><span>MONITORING INFRASTRUCTURE</span><h2>项目监控基础设施</h2></header>
            ${renderMonitoringInfrastructure(record.monitoringInfrastructure)}
          </section>
          <section id="detailWeakSignals" class="detail-section">
            <header><span>WEAK SIGNALS</span><h2>项目弱线索</h2></header>
            ${renderWeakSignals(record.weakSignals, project.project_id)}
          </section>
          <section id="detailAutomaticProfile" class="detail-section">
            <header><span>AUTOMATIC PROFILE QUALITY</span><h2>自动档案质量</h2></header>
            ${renderAutomaticProfile(record.automaticProfile)}
          </section>
          <section id="detailResearchRoute" class="detail-section">
            <header><span>LIFECYCLE & RESEARCH</span><h2>项目类别与研究重点</h2></header>
            ${renderResearchRoute(researchRoute)}
          </section>
          <section id="detailTrackingTask" class="detail-section">
            <header><span>NEXT TRACKING TASK</span><h2>下一步跟踪任务</h2></header>
            ${renderTrackingTask(trackingTask)}
          </section>
          ${renderRoutePrioritySections(researchRoute)}
          <section id="detailConvexity" class="detail-section">
            <header><span>CONVEXITY LOGIC</span><h2>凸性逻辑</h2></header>
            ${renderConvexityLogic(currentCase)}
          </section>
          <section class="detail-section">
            <header><span>FACT-NEWS MISMATCH</span><h2>事实新闻错配</h2></header>
            ${renderMachineResearchScore(currentCase?.machineResearchScore)}
            <div class="detail-score-panel">${renderScore(currentCase?.mismatchScore)}</div>
          </section>
          <section id="detailAssetIdentity" class="detail-section">
            <header><span>ASSET IDENTITY</span><h2>机器资产身份复核</h2></header>
            ${renderAssetIdentityReview(record.assetIdentityReview)}
          </section>
          <section id="detailAssets" class="detail-section">
            <header><span>ASSETS & EXIT</span><h2>资产、合约与退出路径</h2></header>
            ${renderAssets(record.assets)}
          </section>
          <section id="detailEvidence" class="detail-section">
            <header><span>EVIDENCE</span><h2>证据与反面证据</h2></header>
            ${renderEvidence(record.evidence)}
          </section>
          <section id="detailHistory" class="detail-section">
            <header><span>STATE HISTORY</span><h2>状态变化历史</h2></header>
            ${renderTransitions(record.cases)}
          </section>
        </div>
        <aside class="detail-side-rail">
          <section><span>项目身份</span><strong>${escapeHtml(statusLabels[project.identity_status] || project.identity_status)}</strong><p>${escapeHtml(project.canonical_name)}</p></section>
          <section><span>团队与组织</span><strong>${project.team_summary ? "已有资料" : "待补齐"}</strong><p>${escapeHtml(project.team_summary || "尚未录入可核验的团队与组织信息。")}</p></section>
          <section><span>官方入口</span><p>${project.website_domain ? `<a href="https://${escapeHtml(project.website_domain)}" target="_blank" rel="noreferrer">${escapeHtml(project.website_domain)}</a>` : "官网待补齐"}</p><p>${project.official_repo ? `<a href="${escapeHtml(project.official_repo)}" target="_blank" rel="noreferrer">官方代码库</a>` : "代码库待补齐"}</p></section>
          <section><span>研究案例</span><ul>${caseOptions}</ul></section>
        </aside>
      </div>
    `;
  }

  function renderDiscovery(record) {
    const item = record.master;
    const discovery = record.discovery || {};
    const identity = discovery.identityReview;
    const researchRoute = routeForRecord(record);
    const description = identity?.reason || discovery.statusReason || item.statusReason;
    const metrics = [
      metric("档案完整度", `${record.automaticProfile?.score ?? "--"}/100`, record.automaticProfile?.gradeLabel || "自动档案"),
      metric("发现排序分", discovery.discoveryScore ?? "--", "不是投资评分"),
      metric("技术预检", statusLabels[discovery.preflightStatus] || valueOrPending(discovery.preflightStatus), "只验证技术条件"),
      metric("合约风险", statusLabels[discovery.contractRisk] || valueOrPending(discovery.contractRisk), "身份仍待复核"),
      metric("流动性", money(discovery.liquidityUsd), "当前快照"),
      metric("卖出路径", statusLabels[discovery.sellPathStatus] || valueOrPending(discovery.sellPathStatus), "只读核验"),
    ].join("");
    const sourceRows = (discovery.sourceIds || []).map((sourceId, index) => `
      <li><strong>${escapeHtml(sourceId)}</strong>${discovery.sourceUrls?.[index] ? `<a href="${escapeHtml(discovery.sourceUrls[index])}" target="_blank" rel="noreferrer">查看来源</a>` : ""}</li>
    `).join("") || "<li>来源待补齐</li>";
    const scanRows = record.scanHistory.length
      ? record.scanHistory.map((scan) => `<tr><td>${escapeHtml(dateTime(scan.observed_at))}</td><td>${escapeHtml(scan.network_name)}</td><td>${escapeHtml(scan.source_name)}</td><td>${escapeHtml(scan.result_status)}</td><td>${escapeHtml(scan.reason)}</td></tr>`).join("")
      : '<tr><td colspan="5">旧发现记录尚无逐链逐源历史；下一次扫描后自动生成。</td></tr>';
    const identityEvidence = identity?.evidence?.length
      ? `<ul class="detail-plain-list">${identity.evidence.map((entry) => `<li>${escapeHtml(typeof entry === "string" ? entry : entry.summary || JSON.stringify(entry))}</li>`).join("")}</ul>`
      : '<p class="detail-empty">尚无足以确认项目主体的身份交叉证据。</p>';
    byId("detailContent").innerHTML = `
      ${detailHero(
        item,
        "链上发现 · 尚未升格",
        description || "发现记录已保留，等待项目主体和价值捕获核验。",
        "只观察",
        item.statusLabel,
        "尚未建立研究案例并通过统一行动门槛，只能保留观察。"
      )}
      <section class="status-grid convexity-detail-metrics">${metrics}</section>
      <nav class="detail-anchor-nav"><a href="#discoveryBoundary">当前边界</a><a href="#detailAutomaticProfile">自动档案</a><a href="#detailResearchRoute">项目类别</a>${routeAnchorLinks(researchRoute)}<a href="#discoveryContract">合约与交易</a><a href="#discoveryIdentity">身份复核</a><a href="#discoverySources">来源与扫描</a></nav>
      <div class="convexity-detail-layout">
        <div>
          <section id="discoveryBoundary" class="detail-section">
            <header><span>RESEARCH BOUNDARY</span><h2>当前边界</h2></header>
            <article class="detail-conclusion is-pending">
              <strong>尚未形成投资结论</strong>
              <p>${escapeHtml(description || "项目主体身份、代币价值捕获和凸性来源尚未完成核验。")}</p>
              <small>技术预检通过，只代表可以继续研究，不代表可以买入。</small>
            </article>
          </section>
          <section id="detailAutomaticProfile" class="detail-section">
            <header><span>AUTOMATIC PROFILE QUALITY</span><h2>自动档案质量</h2></header>
            ${renderAutomaticProfile(record.automaticProfile)}
          </section>
          <section id="detailResearchRoute" class="detail-section">
            <header><span>LIFECYCLE & RESEARCH</span><h2>项目类别与研究重点</h2></header>
            ${renderResearchRoute(researchRoute)}
          </section>
          ${renderRoutePrioritySections(researchRoute)}
          <section id="discoveryContract" class="detail-section">
            <header><span>CONTRACT & EXIT</span><h2>合约、所在链与退出路径</h2></header>
            <dl class="discovery-fact-grid">
              <div><dt>网络</dt><dd>${escapeHtml(discovery.networkName || item.networkName)}</dd></div>
              <div><dt>Chain ID</dt><dd>${escapeHtml(discovery.chainId || "--")}</dd></div>
              <div><dt>代币标准</dt><dd>${escapeHtml(discovery.contractStandard || "--")}</dd></div>
              <div><dt>合约存在</dt><dd>${escapeHtml(statusLabels[discovery.contractExistsStatus] || valueOrPending(discovery.contractExistsStatus))}</dd></div>
              <div class="wide"><dt>代币合约</dt><dd><code>${escapeHtml(discovery.contractAddress || item.contractAddress)}</code>${discovery.explorerUrl ? `<a href="${escapeHtml(discovery.explorerUrl)}" target="_blank" rel="noreferrer">区块浏览器</a>` : ""}</dd></div>
              <div><dt>价格</dt><dd>${money(discovery.priceUsd)}</dd></div>
              <div><dt>流动性</dt><dd>${money(discovery.liquidityUsd)}</dd></div>
              <div><dt>24小时成交</dt><dd>${money(discovery.volume24hUsd)}</dd></div>
              <div><dt>流通市值</dt><dd>${money(discovery.marketCapUsd)}</dd></div>
              <div><dt>近期买入</dt><dd>${number(discovery.recentBuys24h)}</dd></div>
              <div><dt>近期卖出</dt><dd>${number(discovery.recentSells24h)}</dd></div>
              <div><dt>模拟退出金额</dt><dd>${money(discovery.exitNotionalUsd)}</dd></div>
              <div><dt>预计退出滑点</dt><dd>${pct(discovery.estimatedExitSlippagePct)}</dd></div>
            </dl>
          </section>
          <section id="discoveryIdentity" class="detail-section">
            <header><span>IDENTITY REVIEW</span><h2>项目主体身份复核</h2></header>
            <div class="identity-review-panel">
              <dl>
                <div><dt>复核结论</dt><dd>${escapeHtml(identity ? statusLabels[identity.resolutionStatus] || identity.resolutionStatus : "尚未复核")}</dd></div>
                <div><dt>候选项目名</dt><dd>${escapeHtml(identity?.canonicalName || "--")}</dd></div>
                <div><dt>官方合约</dt><dd>${escapeHtml(identity ? statusLabels[identity.officialContractStatus] || identity.officialContractStatus : "--")}</dd></div>
                <div><dt>名称匹配</dt><dd>${escapeHtml(identity ? statusLabels[identity.nameMatchStatus] || identity.nameMatchStatus : "--")}</dd></div>
                <div><dt>价值捕获</dt><dd>${escapeHtml(identity?.valueCaptureStatus || "待核验")}</dd></div>
                <div><dt>升格状态</dt><dd>${escapeHtml(identity?.promotionStatus || "未升格")}</dd></div>
              </dl>
              ${identityEvidence}
            </div>
          </section>
          <section id="discoverySources" class="detail-section">
            <header><span>SOURCES & SCANS</span><h2>发现来源与扫描历史</h2></header>
            <ul class="discovery-source-list">${sourceRows}</ul>
            <div class="detail-table-wrap"><table><thead><tr><th>时间</th><th>链</th><th>信源</th><th>结果</th><th>原因</th></tr></thead><tbody>${scanRows}</tbody></table></div>
          </section>
        </div>
        <aside class="detail-side-rail">
          <section><span>身份状态</span><strong>${escapeHtml(item.statusLabel)}</strong><p>${escapeHtml(item.statusReason)}</p></section>
          <section><span>首次发现</span><strong>${escapeHtml(dateTime(discovery.firstSeenAt))}</strong><p>最近出现：${escapeHtml(dateTime(discovery.lastSeenAt))}</p></section>
          <section><span>来源冲突风险</span><strong>${escapeHtml(statusLabels[discovery.sourceConflictRisk] || valueOrPending(discovery.sourceConflictRisk))}</strong><p>推广或项目方资料只能用于发现，不能单独成为结论。</p></section>
          <section><span>人工标注</span><strong>${record.annotations.length} 条</strong><p>人工标注入口将在独立凸性工作台提供。</p></section>
        </aside>
      </div>
    `;
  }

  if (active.recordType === "project") {
    renderProject(active);
  } else {
    renderDiscovery(active);
  }
})();
