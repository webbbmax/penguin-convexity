(function c22Admin() {
  "use strict";

  if (window.PENGUIN_CONVEXITY_C24_TAKEOVER_PENDING) return;

  const snapshot = window.PENGUIN_CONVEXITY_C22_ADMIN;
  if (!snapshot || snapshot.schemaVersion !== "c2.2-admin-v1") return;

  const page = location.pathname.split("/").pop();
  const pageJob = page === "new-token-update.html"
    ? "screening"
    : page === "update-center.html"
      ? "convexity_tracking"
      : "";
  const host = document.querySelector("main") || document.body;
  const panel = document.createElement("section");
  panel.className = "c21-admin c22-admin";
  panel.id = "c22AdminPanel";

  const stateLabels = {
    not_started: "尚未运行",
    never_run: "尚未运行",
    running: "正在运行",
    completed: "已完成",
    partial: "部分完成",
    partial_success: "部分完成",
    paused: "已暂停",
    failed: "运行失败",
  };
  const sourceStateLabels = {
    success: "成功",
    no_data: "没有返回可用数据",
    quota_limited: "额度暂时受限",
    source_failure: "来源连接失败",
    unsupported: "当前不支持",
    configuration_missing: "配置缺失",
    program_failure: "程序错误",
  };
  const sourceMeta = {
    c2_1_pipeline: "筛选流水线",
    gate0_accepted_candidates: "已验收候选入口",
    coingecko_new_pools: "新池与新币发现",
    project_website_identity: "项目网站与身份链路",
    github: "官方代码仓库证据",
    dexscreener: "市场与流动性（共享上游）",
    standard_sell_quote: "100美元标准卖出报价",
    c2_1_path4: "全池成交与供应历史",
    goplus: "公开风险与供应",
    convexity_main_readonly: "既有凸性主干",
  };
  const ownerLabels = {
    screening: "90天新币筛选",
    convexity_tracking: "凸性跟踪",
    shared: "共享上游",
  };
  const actionableStates = new Set([
    "quota_limited",
    "source_failure",
    "configuration_missing",
    "program_failure",
  ]);
  const retryableStates = new Set(["quota_limited", "source_failure"]);
  const boundaryStates = new Set(["no_data", "unsupported"]);

  const esc = (value) => String(value ?? "").replace(
    /[&<>"']/g,
    (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
  );
  const fmt = (value) => value == null
    ? "--"
    : new Intl.NumberFormat("zh-CN", {
      maximumFractionDigits: 2,
      notation: Math.abs(Number(value)) >= 1000000 ? "compact" : "standard",
    }).format(value);
  const time = (value) => {
    if (!value) return "尚无";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(parsed);
  };
  const stats = (rows) => `<div class="c21-admin-grid">${rows.map(([key, value, id]) => `
    <div class="c21-admin-stat"><span>${esc(key)}</span><strong${id ? ` id="${esc(id)}"` : ""}>${esc(value)}</strong></div>
  `).join("")}</div>`;

  function header(title, note) {
    return `<div class="c21-admin-head">
      <div><span class="c21-kicker">更新中心</span><h2>${esc(title)}</h2><p>${esc(note)}</p></div>
      <span class="c21-time">业务快照 ${esc(time(snapshot.generatedAt))}</span>
    </div>`;
  }

  function relevantSourceRows(jobCode) {
    return (snapshot.sourceHealth || []).filter((row) => {
      const affected = row.affectedJobs || (row.owner === "shared" ? ["screening", "convexity_tracking"] : [row.owner]);
      return affected.includes(jobCode);
    });
  }

  function groupedSources(jobCode) {
    const groups = new Map();
    for (const row of relevantSourceRows(jobCode)) {
      const sourceId = row.source_id || "unknown";
      if (!groups.has(sourceId)) groups.set(sourceId, []);
      groups.get(sourceId).push(row);
    }
    return [...groups.entries()].map(([sourceId, rows]) => {
      const counts = {};
      for (const row of rows) counts[row.status] = (counts[row.status] || 0) + 1;
      return {
        sourceId,
        rows,
        owner: rows[0]?.owner || "shared",
        counts,
        actionableCount: rows.filter((row) => actionableStates.has(row.status)).length,
        boundaryCount: rows.filter((row) => boundaryStates.has(row.status)).length,
        retryable: rows.some((row) => retryableStates.has(row.status) && row.sourceRetrySupported),
        lastSuccess: rows.map((row) => row.last_success_at).filter(Boolean).sort().at(-1) || null,
      };
    });
  }

  function sourceCard(group, jobCode) {
    const statusChips = Object.entries(group.counts).map(([state, count]) => (
      `<span class="c22-source-chip is-${esc(state)}">${esc(sourceStateLabels[state] || state)} ${esc(fmt(count))}</span>`
    )).join("");
    const retry = group.retryable
      ? `<div class="c22-source-action">
          <button class="c21-button" type="button" data-c22-retry-source="${esc(group.sourceId)}" data-c22-job="${esc(jobCode)}">只更新这个来源</button>
          <p class="c22-source-action-feedback" data-c22-source-feedback role="status" aria-live="polite"></p>
        </div>`
      : "";
    return `<article class="c22-source-card ${group.actionableCount ? "has-action" : ""}">
      <div><span>${esc(ownerLabels[group.owner] || group.owner)}</span><h4>${esc(sourceMeta[group.sourceId] || group.sourceId)}</h4></div>
      <div class="c22-source-chips">${statusChips}</div>
      <small>记录 ${esc(fmt(group.rows.length))} · 最后成功 ${esc(time(group.lastSuccess))}</small>
      ${retry}
    </article>`;
  }

  function sourceHealthBlock(jobCode) {
    const groups = groupedSources(jobCode);
    const actionable = groups.filter((group) => group.actionableCount);
    const boundaries = groups.filter((group) => group.boundaryCount && !group.actionableCount);
    const actionableRows = actionable.reduce((sum, group) => sum + group.actionableCount, 0);
    const boundaryRows = groups.reduce((sum, group) => sum + group.boundaryCount, 0);
    const actionText = actionableRows
      ? "先按下方原因处理；只有额度受限或连接失败提供单项重试。"
      : "当前没有需要你处理的来源问题。";
    return `<section class="c22-source-health" data-c22-source-health="${esc(jobCode)}">
      <header><div><span class="c21-kicker">本页来源状态</span><h3>来源归属已经按作业拆开</h3><p>${esc(actionText)}</p></div></header>
      <div class="c22-source-summary">
        <article class="${actionableRows ? "has-action" : "is-clear"}"><span>需要处理</span><strong>${esc(fmt(actionableRows))}</strong><small>额度、连接、配置或程序问题</small></article>
        <article><span>来源能力边界</span><strong>${esc(fmt(boundaryRows))}</strong><small>无数据或当前不支持，不是程序故障</small></article>
      </div>
      ${actionable.length ? `<div class="c22-source-grid">${actionable.map((group) => sourceCard(group, jobCode)).join("")}</div>` : ""}
      ${boundaries.length ? `<details class="c22-boundary-details"><summary>查看无需处理的来源能力边界（${esc(fmt(boundaryRows))}条）</summary><div class="c22-source-grid">${boundaries.map((group) => sourceCard(group, jobCode)).join("")}</div></details>` : ""}
      <p class="c21-source-note">没有数据和当前不支持不会反复重试；配置缺失需先补配置，程序错误需先修复。完整逐条记录继续保留在信源库。</p>
    </section>`;
  }

  function scheduleControls(jobCode) {
    const config = (snapshot.config?.jobs || {})[jobCode] || {};
    const mode = config.mode || "automatic";
    const interval = Number(config.intervalHours || 24);
    return `<section class="c22-controls" data-c22-controls="${esc(jobCode)}">
      <div class="c21-admin-actions">
        <button class="c21-button is-primary" id="c22RunNow" type="button">${jobCode === "screening" ? "立即手动更新新币筛选" : "立即手动更新凸性跟踪"}</button>
        <button class="c21-button" id="c22PauseCurrent" type="button">暂停当前任务</button>
      </div>
      <div class="c22-schedule-controls">
        <label>更新方式<select id="c22Mode">
          <option value="automatic" ${mode === "automatic" ? "selected" : ""}>自动更新</option>
          <option value="manual" ${mode === "manual" ? "selected" : ""}>仅手动</option>
        </select></label>
        <label>自动频率<select id="c22Interval">
          <option value="1" ${interval === 1 ? "selected" : ""}>每1小时</option>
          <option value="3" ${interval === 3 ? "selected" : ""}>每3小时</option>
          <option value="6" ${interval === 6 ? "selected" : ""}>每6小时</option>
          <option value="12" ${interval === 12 ? "selected" : ""}>每12小时</option>
          <option value="24" ${interval === 24 ? "selected" : ""}>每天</option>
        </select></label>
        <button class="c21-button" id="c22SaveSchedule" type="button">保存本页设置</button>
        <button class="c21-button" id="c22ToggleAutomatic" type="button">${config.paused ? "恢复自动更新" : "暂停自动更新"}</button>
      </div>
      <p class="c21-source-note">手动与自动共用同一条隐藏、单实例、可恢复流水线；离开页面不会中断，黑框为0。两个页面的设置互不修改。</p>
      <p id="c22Feedback" class="c21-action-feedback" aria-live="polite"></p>
    </section>`;
  }

  function liveStatusBlock(jobCode) {
    return `<section class="c22-live-status" id="c22LiveStatus" data-job="${esc(jobCode)}" aria-live="polite">
      <div class="c22-live-head"><div><span class="c21-kicker">实时任务进度</span><h3 id="c22LiveTitle">正在读取后台状态</h3><p id="c22LiveMessage">页面会持续读取同一个后台任务，不需要反复点击更新。</p></div><strong id="c22LiveBadge">连接中</strong></div>
      <div class="c22-progress" aria-label="当前任务进度"><span id="c22ProgressBar"></span></div>
      <div class="c22-live-grid">
        <article><span>当前阶段</span><strong id="c22LiveStage">--</strong></article>
        <article><span>处理进度</span><strong id="c22LiveUnits">-- / --</strong></article>
        <article><span>最近心跳</span><strong id="c22LiveHeartbeat">--</strong></article>
        <article><span>最近完成</span><strong id="c22LiveCompleted">--</strong></article>
        <article><span>下次自动更新</span><strong id="c22LiveNext">--</strong></article>
        <article><span>当前活动</span><strong id="c22LiveCurrent">--</strong></article>
      </div>
    </section>`;
  }

  function candidateProductionBlock() {
    return `<section class="c22-production" id="c22CandidateProduction" aria-live="polite">
      <div class="c22-live-head">
        <div><span class="c21-kicker">独立历史底座</span><h3 id="c22ProductionTitle">历史候选基础扫描 · 正在读取</h3><p id="c22ProductionMessage">覆盖存量候选的确定性本地检查和公开市场确认，不重新扫描 Gate 0 区块链历史。</p></div>
        <strong id="c22ProductionBadge">读取中</strong>
      </div>
      <div class="c22-progress" aria-label="历史候选基础扫描进度"><span id="c22ProductionBar"></span></div>
      <div class="c22-production-grid">
        <article><span>已导入候选</span><strong id="c22ProductionImported">--</strong></article>
        <article><span>本地检查完成</span><strong id="c22ProductionLocal">--</strong></article>
        <article><span>本地确定性排除</span><strong id="c22ProductionExcluded">--</strong></article>
        <article><span>等待市场形成</span><strong id="c22ProductionWaiting">--</strong></article>
        <article><span>市场请求对象</span><strong id="c22ProductionRequested">--</strong></article>
        <article><span>市场确认</span><strong id="c22ProductionMarket">--</strong></article>
        <article><span>T0正式交给第一关</span><strong id="c22ProductionTracking">--</strong></article>
        <article><span>其中历史底座</span><strong id="c22ProductionHistoricalHandoff">--</strong></article>
        <article><span>其中日常新增</span><strong id="c22ProductionDailyHandoff">--</strong></article>
        <article><span>其中待确认身份</span><strong id="c22ProductionPendingIdentity">--</strong></article>
        <article><span>其中 D 类代币</span><strong id="c22ProductionClassD">--</strong></article>
        <article><span>第一关待处理</span><strong id="c22ProductionGatePending">--</strong></article>
        <article><span>第一关处理完成</span><strong id="c22ProductionEvaluated">--</strong></article>
        <article><span>通过并交给凸性跟踪</span><strong id="c22ProductionFront">--</strong></article>
        <article><span>当前分片</span><strong id="c22ProductionPartition">--</strong></article>
        <article><span>最近断点/心跳</span><strong id="c22ProductionHeartbeat">--</strong></article>
        <article><span>运行时间</span><strong id="c22ProductionRuntime">--</strong></article>
        <article><span>预计剩余</span><strong id="c22ProductionEta">稳定分片不足5个，暂不显示</strong></article>
      </div>
      <div class="c21-admin-actions c22-production-actions">
        <button class="c21-button is-primary" id="c22ProductionRun" type="button">开始/继续历史候选扫描</button>
        <button class="c21-button" id="c22ProductionPause" type="button">暂停历史候选扫描</button>
      </div>
      <div id="c22ProductionFailures"></div>
      <p class="c21-source-note" id="c22ProductionBoundary">历史候选先完成 T0、身份、A/B/C/D、产品证据和宽硬门槛核验。D 类、身份或产品证据未闭环的对象留在新币筛选后台；只有通过第一关的 A/B/C 项目才交给凸性跟踪。</p>
      <p id="c22ProductionFeedback" class="c21-action-feedback" aria-live="polite"></p>
    </section>`;
  }

  function dailyCandidateSummaryBlock() {
    return `<section class="c22-daily-funnel" id="c22DailyFunnel" aria-live="polite">
      <div><span class="c21-kicker">日常高优先级队列</span><h3>本页日常候选去向</h3><p>只统计日常新增和到期复查，不包含459万历史积压。</p></div>
      <div class="c22-production-grid">
        <article><span>进入日常队列</span><strong id="c22DailyQueued">0</strong></article>
        <article><span>本地检查完成</span><strong id="c22DailyLocal">0</strong></article>
        <article><span>市场确认</span><strong id="c22DailyMarket">0</strong></article>
        <article><span>等待交易形成</span><strong id="c22DailyWaiting">0</strong></article>
        <article><span>进入第一关核验</span><strong id="c22DailyTracking">0</strong></article>
        <article><span>交给凸性跟踪</span><strong id="c22DailyFront">0</strong></article>
        <article><span>来源未完成</span><strong id="c22DailySources">0</strong></article>
        <article><span>已安排复查</span><strong id="c22DailyRetry">0</strong></article>
      </div>
      <p class="c21-source-note">来源未完成会在下方按来源说明；只有支持单项恢复的来源才显示“只更新这个来源”。</p>
    </section>`;
  }

  function legacyWorkbenchBlock() {
    return `<section class="c22-workbench-bridge" id="c22WorkbenchBridge" aria-live="polite">
      <div class="c22-live-head">
        <div><span class="c21-kicker">历史主干维护</span><h3 id="c22WorkbenchTitle">历史主干维护（不影响现役前台）</h3><p id="c22WorkbenchMessage">这里保留旧版页面和回滚任务，不会计入现役故障，也不会阻断两项现役作业。</p></div>
        <strong id="c22WorkbenchBadge">读取中</strong>
      </div>
      <div class="c22-workbench-grid">
        <article><span>历史任务</span><strong id="c22WorkbenchTask">--</strong></article>
        <article><span>历史记录时间</span><strong id="c22WorkbenchTime">--</strong></article>
        <article><span>何时使用</span><strong>只在维护旧版页面或回滚能力时</strong></article>
      </div>
      <button class="c21-button" id="c22OpenLegacy" type="button">查看历史任务明细与手动维护</button>
    </section>`;
  }

  function screeningPage() {
    const coverage = snapshot.screening?.coverageSummary || {};
    panel.innerHTML = header(
      "90天新币筛选",
      "第一关只负责候选发现、T0、A/B/C、宽硬门槛和产品证据；凸性跟踪在另一页独立更新。",
    ) + stats([
      ["T0已核验总数", fmt(coverage.t0VerifiedCount), "c22TopT0Verified"],
      ["已进入第一关", fmt(coverage.firstGateQueuedCount), "c22TopFirstGateQueued"],
      ["第一关已完成", fmt(coverage.firstGateCompletedCount), "c22TopFirstGateCompleted"],
      ["第一关待处理", fmt(coverage.firstGatePendingCount), "c22TopFirstGatePending"],
      ["通过硬门槛", fmt(coverage.hardGatePassedCount), "c22TopHardGatePassed"],
      ["当前前台", fmt(coverage.frontVisibleCount), "c22TopFrontVisible"],
    ]) + liveStatusBlock("screening") + scheduleControls("screening") + dailyCandidateSummaryBlock() + candidateProductionBlock() + sourceHealthBlock("screening")
      + '<a class="c22-page-link" href="update-center.html">转到凸性跟踪更新 →</a>';
  }

  function trackingPage() {
    const tracking = snapshot.tracking || {};
    const input = tracking.inputSummary || snapshot.trackingQualification || {};
    panel.innerHTML = header(
      "凸性跟踪更新",
      "第二关只负责市场、流动性、供应、产品使用、风险、四条强路径和动态后验；新币筛选在另一页独立更新。",
    ) + stats([
      ["跟踪候选总数", fmt(input.candidateCount ?? tracking.items?.length ?? 0)],
      ["待确认项目身份", fmt(input.backendIdentityPendingCount || 0)],
      ["已进入第二关", fmt(input.publicCandidateCount ?? input.detailedPublicItemCount ?? 0)],
      ["首轮跟踪完成", fmt(input.completedFirstTrackingCount || 0)],
      ["首轮尚未完整", fmt(input.pendingFirstTrackingCount || 0)],
      ["凸性线索", fmt(tracking.stateCounts?.convexity_clue)],
    ]) + liveStatusBlock("convexity_tracking") + scheduleControls("convexity_tracking") + sourceHealthBlock("convexity_tracking")
      + legacyWorkbenchBlock()
      + '<a class="c22-page-link" href="new-token-update.html">转到90天新币筛选 →</a>';
  }

  function overview() {
    const coverage = snapshot.screening?.coverageSummary || {};
    panel.innerHTML = header(
      "筛选—跟踪主干运行概览",
      "同一个产品、两个独立作业、一个可恢复调度入口。进入对应更新页面后可以手动更新、调整频率并查看实时进度。",
    ) + stats([
      ["当前前台", fmt(coverage.frontVisibleCount)],
      ["筛选候选", fmt(snapshot.screening?.candidateCount)],
      ["后台跟踪候选", fmt(snapshot.tracking?.inputSummary?.candidateCount ?? snapshot.tracking?.items?.length)],
      ["来源记录", fmt((snapshot.sourceHealth || []).length)],
    ]) + '<div class="c22-overview-links"><a href="new-token-update.html">管理90天新币筛选</a><a href="update-center.html">管理凸性跟踪更新</a></div>';
  }

  let legacyDetails = null;
  function collapseLegacyTrackingPage() {
    if (page !== "update-center.html") return;
    const legacy = [...host.children].filter((node) => node !== panel);
    if (!legacy.length) return;
    legacyDetails = document.createElement("details");
    legacyDetails.className = "c22-legacy-details";
    legacyDetails.innerHTML = "<summary>历史主干维护（不影响现役前台）</summary>";
    legacy.forEach((node) => legacyDetails.appendChild(node));
    host.appendChild(legacyDetails);
  }

  function renderLegacyWorkbenchStatus(status) {
    if (page !== "update-center.html") return;
    const state = status?.state || "not_started";
    const recovering = Boolean(status?.recoveryAvailable && status?.recoveryTaskId);
    const running = state === "running";
    const needsAttention = recovering || ["failed", "error", "partial_success"].includes(state);
    const title = document.querySelector("#c22WorkbenchTitle");
    const message = document.querySelector("#c22WorkbenchMessage");
    const badge = document.querySelector("#c22WorkbenchBadge");
    const task = document.querySelector("#c22WorkbenchTask");
    const finishedAt = status?.finishedAt || status?.finished_at || status?.startedAt || status?.started_at;
    title.textContent = running
      ? "历史主干任务正在运行"
      : needsAttention
        ? "历史任务保留失败记录"
        : "历史主干当前没有运行任务";
    message.textContent = running
      ? "这是一项旧版主干维护任务；不会改变两项现役作业的状态。"
      : needsAttention
        ? `${status.taskLabel || status.recoveryTaskId || "历史任务"}曾经失败；不会计入现役故障，也不会阻断两项现役作业。`
        : "累计历史记录继续保留，仅在维护旧版页面时使用。";
    badge.textContent = running ? "历史任务运行中" : needsAttention ? "历史失败记录" : "历史任务空闲";
    badge.className = running ? "is-warn" : "is-neutral";
    task.textContent = status?.taskLabel || status?.recoveryTaskId || "当前无";
    document.querySelector("#c22WorkbenchTime").textContent = time(finishedAt);
  }

  function detailedProgress(jobCode, runtime, childStatus) {
    const job = runtime.jobs?.[jobCode] || {};
    const base = {
      completed: Number(job.progress?.completed || 0),
      total: Number(job.progress?.total || 0),
      stage: job.stage || "--",
      heartbeat: job.lastHeartbeatAt,
      current: job.message || "--",
    };
    if (job.state !== "running" || !childStatus || jobCode !== "screening") return base;
    if (jobCode === "screening") {
      const pipeline = childStatus.pipeline || {};
      return {
        completed: Number(pipeline.completedUnits || base.completed),
        total: Number(pipeline.totalUnits || base.total),
        stage: pipeline.stage || base.stage,
        heartbeat: pipeline.updatedAt || pipeline.lastHeartbeatAt || base.heartbeat,
        current: pipeline.currentItem || pipeline.message || base.current,
      };
    }
    return base;
  }

  let observedRunId = "";
  function renderLiveStatus(jobCode, runtime, childStatus) {
    const job = runtime.jobs?.[jobCode] || {};
    const config = runtime.config?.jobs?.[jobCode] || {};
    const progress = detailedProgress(jobCode, runtime, childStatus);
    const completed = progress.completed;
    const total = progress.total;
    const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((completed / total) * 100))) : job.state === "completed" ? 100 : 0;
    const heartbeatAge = progress.heartbeat ? Date.now() - new Date(progress.heartbeat).getTime() : 0;
    const stale = job.state === "running" && heartbeatAge > 180000;
    const stateLabel = stale ? "可能中断" : stateLabels[job.state] || job.state || "尚未运行";
    const otherRunning = Object.values(runtime.jobs || {}).find((item) => item.jobCode !== jobCode && item.state === "running");

    document.querySelector("#c22LiveTitle").textContent = `${jobCode === "screening" ? "90天新币筛选" : "凸性跟踪"} · ${stateLabel}`;
    document.querySelector("#c22LiveMessage").textContent = stale
      ? "超过3分钟没有取得这一层的心跳；详细工作台仍保留实际底层进度，系统不会自动从头重跑。"
      : job.message || "当前没有任务运行。";
    const badge = document.querySelector("#c22LiveBadge");
    badge.textContent = stateLabel;
    badge.className = stale || job.state === "failed" ? "is-bad" : job.state === "partial" || job.state === "paused" ? "is-warn" : job.state === "completed" ? "is-good" : "";
    document.querySelector("#c22ProgressBar").style.width = `${percent}%`;
    document.querySelector("#c22LiveStage").textContent = progress.stage;
    document.querySelector("#c22LiveUnits").textContent = total ? `${fmt(completed)} / ${fmt(total)}` : "总量待确认";
    document.querySelector("#c22LiveHeartbeat").textContent = time(progress.heartbeat);
    document.querySelector("#c22LiveCompleted").textContent = time(job.lastCompletedAt);
    document.querySelector("#c22LiveNext").textContent = config.mode === "manual" ? "仅手动" : config.paused ? "自动更新已暂停" : time(job.nextDueAt);
    document.querySelector("#c22LiveCurrent").textContent = otherRunning ? `等待${otherRunning.jobCode === "screening" ? "新币筛选" : "凸性跟踪"}完成` : progress.current;

    const runButton = document.querySelector("#c22RunNow");
    const pauseButton = document.querySelector("#c22PauseCurrent");
    const toggleButton = document.querySelector("#c22ToggleAutomatic");
    const mode = document.querySelector("#c22Mode");
    const interval = document.querySelector("#c22Interval");
    runButton.disabled = job.state === "running" || Boolean(otherRunning);
    pauseButton.disabled = job.state !== "running";
    mode.value = config.mode || "automatic";
    interval.value = String(config.intervalHours || 24);
    interval.disabled = mode.value === "manual";
    toggleButton.textContent = config.paused ? "恢复自动更新" : "暂停自动更新";
    toggleButton.dataset.paused = String(Boolean(config.paused));

    if (job.state === "running") observedRunId = job.runId || observedRunId;
  }

  function renderCandidateProduction(production) {
    if (page !== "new-token-update.html" || !production) return;
    const total = Number(production.importedCandidateCount || 0);
    const completed = Number(production.localScannedCount || 0);
    const percent = total ? Math.max(0, Math.min(100, Math.round((completed / total) * 100))) : 0;
    const authorized = Boolean(production.formalHistoricalScanAuthorized);
    const running = production.state === "running";
    const paused = Boolean(production.paused);
    const current = production.currentPartition || null;
    const daily = production.queueSummaries?.daily_incremental || {};
    const firstGateRunning = Number(production.firstGateRunningCount || 0) > 0
      || production.currentRun?.selected_queue === "first_gate_backlog";
    const stateText = production.state === "not_migrated" ? "尚未安装数据结构"
      : production.state === "program_failure" ? "状态读取失败"
        : firstGateRunning ? "正在处理新币筛选第一关"
          : running ? "正在隐藏后台扫描" : paused ? "已请求暂停" : "当前空闲";
    document.querySelector("#c22ProductionTitle").textContent = `候选生产底座 · ${stateText}`;
    document.querySelector("#c22ProductionMessage").textContent = current
      ? `${current.network_id} · ${current.queue_name === "historical_backlog" ? "历史低优先级" : "日常高优先级"} · ${fmt(current.processed_count || current.local_scanned_count)} / ${fmt(current.total_count)}`
      : firstGateRunning
        ? production.currentRun?.message || `正在处理第一关，剩余${fmt(production.firstGatePendingCount || 0)}条。`
        : production.runtimeBoundary || "分片、断点和游标都会保留。";
    const badge = document.querySelector("#c22ProductionBadge");
    badge.textContent = authorized ? stateText : "正式全量未授权";
    badge.className = production.state === "program_failure" ? "is-bad" : running || paused ? "is-warn" : authorized ? "is-good" : "is-neutral";
    document.querySelector("#c22ProductionBar").style.width = `${percent}%`;
    document.querySelector("#c22ProductionImported").textContent = fmt(total);
    document.querySelector("#c22ProductionLocal").textContent = `${fmt(completed)} · ${percent}%`;
    document.querySelector("#c22ProductionExcluded").textContent = fmt(production.localExcludedCount);
    document.querySelector("#c22ProductionWaiting").textContent = fmt(production.waitingForTradesCount);
    document.querySelector("#c22ProductionRequested").textContent = fmt(production.marketRequestedCount);
    document.querySelector("#c22ProductionMarket").textContent = fmt(production.marketConfirmedCount);
    document.querySelector("#c22ProductionTracking").textContent = fmt(production.t0HandoffCount ?? production.trackingEligibleCount);
    document.querySelector("#c22ProductionHistoricalHandoff").textContent = fmt(production.historicalT0HandoffCount || 0);
    document.querySelector("#c22ProductionDailyHandoff").textContent = fmt(production.dailyT0HandoffCount || 0);
    document.querySelector("#c22ProductionPendingIdentity").textContent = fmt(production.trackingPendingIdentityCount);
    document.querySelector("#c22ProductionClassD").textContent = fmt(production.trackingClassDCount);
    document.querySelector("#c22ProductionGatePending").textContent = fmt(production.firstGatePendingCount || 0);
    document.querySelector("#c22ProductionEvaluated").textContent = fmt(production.firstGateProcessedCount || 0);
    document.querySelector("#c22ProductionFront").textContent = fmt(production.convexityTrackingInputCount || 0);
    const liveTopValues = {
      c22TopT0Verified: production.t0VerifiedCount,
      c22TopFirstGateQueued: production.firstGateQueuedCount,
      c22TopFirstGateCompleted: production.firstGateProcessedCount,
      c22TopFirstGatePending: production.firstGatePendingCount,
      c22TopHardGatePassed: production.convexityTrackingInputCount,
      c22TopFrontVisible: production.convexityTrackingInputCount,
    };
    Object.entries(liveTopValues).forEach(([id, value]) => {
      const node = document.querySelector(`#${id}`);
      if (node && value != null) node.textContent = fmt(value);
    });
    document.querySelector("#c22DailyQueued").textContent = fmt(daily.queuedCandidateCount || 0);
    document.querySelector("#c22DailyLocal").textContent = fmt(daily.localScannedCount || 0);
    document.querySelector("#c22DailyMarket").textContent = fmt(daily.marketConfirmedCount || 0);
    document.querySelector("#c22DailyWaiting").textContent = fmt(daily.waitingForTradesCount || 0);
    document.querySelector("#c22DailyTracking").textContent = fmt(daily.trackingEligibleCount || 0);
    document.querySelector("#c22DailyFront").textContent = fmt(daily.convexityTrackingInputCount || 0);
    document.querySelector("#c22DailySources").textContent = fmt(daily.sourceIncompleteCount || 0);
    document.querySelector("#c22DailyRetry").textContent = fmt(daily.scheduledRetryCount || 0);
    document.querySelector("#c22ProductionPartition").textContent = current ? `${current.network_id} · ${fmt(current.last_committed_cursor)}号断点` : "当前无运行分片";
    document.querySelector("#c22ProductionHeartbeat").textContent = current ? time(current.last_heartbeat_at || current.last_checkpoint_at) : time(production.currentRun?.updated_at);
    const startedAt = production.currentRun?.started_at ? new Date(production.currentRun.started_at).getTime() : 0;
    const runtimeEnd = production.currentRun?.finished_at ? new Date(production.currentRun.finished_at).getTime() : Date.now();
    const runtimeSeconds = startedAt ? Math.max(0, Math.floor((runtimeEnd - startedAt) / 1000)) : null;
    document.querySelector("#c22ProductionRuntime").textContent = runtimeSeconds == null ? "尚未运行" : `${Math.floor(runtimeSeconds / 3600)}小时${Math.floor((runtimeSeconds % 3600) / 60)}分`;
    const eta = production.etaSeconds == null ? null : Number(production.etaSeconds);
    document.querySelector("#c22ProductionEta").textContent = Number(production.firstGatePendingCount || 0) > 0
      ? `第一关尚余${fmt(production.firstGatePendingCount)}条；完成后继续历史分片`
      : eta != null && Number.isFinite(eta) && eta >= 0
        ? `${Math.floor(eta / 3600)}小时${Math.floor((eta % 3600) / 60)}分 · ${production.etaConfidence === "high" ? "较高" : "中等"}可信度`
        : `稳定分片${fmt(production.stablePartitionCount || 0)}个，暂不显示`;
    const run = document.querySelector("#c22ProductionRun");
    const pause = document.querySelector("#c22ProductionPause");
    run.disabled = !authorized || running;
    run.textContent = authorized ? "继续第一关与历史扫描" : "等待单独授权后启动";
    pause.disabled = !running;
    document.querySelector("#c22ProductionBoundary").textContent = authorized
      ? "市场确认对象会先正式写入T0与第一关队列；第一关完成后，只有通过宽硬门槛的A/B/C项目才交给凸性跟踪。D类、身份或产品证据未闭环对象继续留在新币筛选后台。"
      : "459万历史候选正式扫描尚未启动，也不会由页面或自动任务越权启动；日常新增候选仍随本页手动或自动更新处理。";
    const failures = (production.recentPartitions || []).filter((item) => item.state === "failed");
    document.querySelector("#c22ProductionFailures").innerHTML = failures.length
      ? `<div class="c22-production-failures"><strong>可单独重试的失败分片</strong>${failures.map((item) => `<button class="c21-button" type="button" data-c22-production-retry="${esc(item.partition_id)}">${esc(item.network_id)} · ${esc(item.partition_id)}</button>`).join("")}</div>`
      : "";
    document.querySelectorAll("[data-c22-production-retry]").forEach((button) => {
      button.onclick = () => retryCandidatePartition(button.dataset.c22ProductionRetry);
    });
  }

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`状态读取失败：${response.status}`);
    return response.json();
  }

  let pollTimer = null;
  async function loadLiveStatus() {
    if (window.PENGUIN_CONVEXITY_C24_ADMIN) {
      window.clearTimeout(pollTimer);
      return;
    }
    if (!pageJob) return;
    try {
      const detailUrl = pageJob === "screening" ? "/api/c2.1/status" : null;
      const [runtime, detail, legacyStatus] = await Promise.all([
        fetchJson("/api/c2.2/status"),
        detailUrl ? fetchJson(detailUrl).catch(() => null) : Promise.resolve(null),
        pageJob === "convexity_tracking" ? fetchJson("/api/update-status").catch(() => null) : Promise.resolve(null),
      ]);
      renderLiveStatus(pageJob, runtime, detail);
      if (pageJob === "screening") renderCandidateProduction(runtime.candidateProduction);
      if (pageJob === "convexity_tracking") renderLegacyWorkbenchStatus(legacyStatus);
      const job = runtime.jobs?.[pageJob] || {};
      if (observedRunId && job.state !== "running" && job.runId === observedRunId) {
        const reloadKey = `c22-reloaded-${observedRunId}`;
        if (!sessionStorage.getItem(reloadKey)) {
          sessionStorage.setItem(reloadKey, "1");
          location.reload();
          return;
        }
      }
      window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(loadLiveStatus, job.state === "running" ? 2000 : 5000);
    } catch (error) {
      const title = document.querySelector("#c22LiveTitle");
      const message = document.querySelector("#c22LiveMessage");
      if (title) title.textContent = "后台状态暂时无法读取";
      if (message) message.textContent = error.message;
      window.clearTimeout(pollTimer);
      if (!window.PENGUIN_CONVEXITY_C24_ADMIN) {
        pollTimer = window.setTimeout(loadLiveStatus, 5000);
      }
    }
  }

  async function post(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || result.message || "操作失败");
    return result;
  }

  function feedback(message) {
    const target = document.querySelector("#c22Feedback");
    if (target) target.textContent = message;
  }

  function sourceFeedback(button, message, state = "") {
    const target = button?.closest(".c22-source-action")?.querySelector("[data-c22-source-feedback]");
    if (!target) return;
    target.textContent = message;
    target.dataset.state = state;
  }

  function productionFeedback(message) {
    const target = document.querySelector("#c22ProductionFeedback");
    if (target) target.textContent = message;
  }

  async function retryCandidatePartition(partitionId) {
    productionFeedback("正在只重试这个失败分片……");
    try {
      const result = await post("/api/c2.2/candidate-production/retry", { partitionId });
      productionFeedback(result.message || "失败分片已在隐藏后台重新进入队列。");
      await loadLiveStatus();
    } catch (error) {
      productionFeedback(`未能重试：${error.message}`);
    }
  }

  async function startJob(jobCode, sourceId = "", triggerButton = null) {
    const isSourceRetry = Boolean(sourceId);
    const originalLabel = triggerButton?.textContent || "";
    let keepDisabled = false;
    feedback(isSourceRetry ? "正在启动这个来源的单项更新……" : "正在启动手动更新……");
    if (triggerButton) {
      triggerButton.disabled = true;
      triggerButton.setAttribute("aria-busy", "true");
      triggerButton.textContent = "正在提交……";
      sourceFeedback(triggerButton, "正在提交单项更新请求……", "pending");
    }
    try {
      const result = await post("/api/c2.2/run", { jobCode, trigger: "manual", sourceId: sourceId || undefined });
      const accepted = result.status === "launched";
      const occupied = result.status === "already_running";
      const message = accepted
        ? (isSourceRetry ? "已受理，后台正在只更新这个来源。" : "更新已在隐藏后台启动；离开页面不会中断。")
        : occupied
          ? "已有更新任务正在隐藏后台运行，本次没有重复启动。"
          : result.message || "更新请求已处理。";
      feedback(message);
      if (triggerButton) {
        keepDisabled = accepted || occupied;
        triggerButton.textContent = accepted ? "已提交" : occupied ? "任务运行中" : originalLabel;
        sourceFeedback(
          triggerButton,
          accepted ? "已受理，可在上方查看实时任务进度。" : occupied ? "已有任务在运行，可在上方查看实时任务进度。" : message,
          result.status || "completed",
        );
      }
      await loadLiveStatus();
    } catch (error) {
      feedback(`未能启动：${error.message}`);
      sourceFeedback(triggerButton, `未能启动：${error.message}`, "failed");
    } finally {
      if (triggerButton && !keepDisabled) {
        triggerButton.disabled = false;
        triggerButton.removeAttribute("aria-busy");
        triggerButton.textContent = originalLabel;
      }
    }
  }

  function bindControls(jobCode) {
    const mode = document.querySelector("#c22Mode");
    const interval = document.querySelector("#c22Interval");
    document.querySelector("#c22RunNow").onclick = () => startJob(jobCode);
    document.querySelector("#c22PauseCurrent").onclick = async () => {
      feedback("正在请求当前任务在安全点暂停……");
      try {
        const result = await post("/api/c2.2/pause-current", { jobCode, paused: true });
        feedback(result.message || "暂停请求已记录；任务会在安全点停止并保留断点。");
        await loadLiveStatus();
      } catch (error) {
        feedback(`未能暂停：${error.message}`);
      }
    };
    document.querySelector("#c22SaveSchedule").onclick = async () => {
      feedback("正在保存本页更新设置……");
      try {
        await post("/api/c2.2/scheduler", {
          jobCode,
          mode: mode.value,
          intervalHours: mode.value === "manual" ? null : Number(interval.value),
        });
        feedback("本页设置已保存；另一项作业的设置没有改变。");
        await loadLiveStatus();
      } catch (error) {
        feedback(`设置未保存：${error.message}`);
      }
    };
    document.querySelector("#c22ToggleAutomatic").onclick = async (event) => {
      feedback("正在更新自动运行状态……");
      try {
        await post("/api/c2.2/scheduler", { jobCode, paused: event.currentTarget.dataset.paused !== "true" });
        feedback("自动运行状态已更新；当前业务快照继续保留。");
        await loadLiveStatus();
      } catch (error) {
        feedback(`自动运行状态未更新：${error.message}`);
      }
    };
    mode.onchange = () => { interval.disabled = mode.value === "manual"; };
    document.querySelectorAll("[data-c22-retry-source]").forEach((button) => {
      button.onclick = () => startJob(button.dataset.c22Job, button.dataset.c22RetrySource, button);
    });
    const productionRun = document.querySelector("#c22ProductionRun");
    if (productionRun) productionRun.onclick = async () => {
      productionFeedback("正在启动历史候选基础扫描……");
      try {
        const result = await post("/api/c2.2/candidate-production/run", { queue: "historical_backlog" });
        productionFeedback(result.message || "历史候选基础扫描已在隐藏后台启动。");
        await loadLiveStatus();
      } catch (error) {
        productionFeedback(`未能启动：${error.message}`);
      }
    };
    const productionPause = document.querySelector("#c22ProductionPause");
    if (productionPause) productionPause.onclick = async () => {
      productionFeedback("正在请求历史候选扫描在安全点暂停……");
      try {
        const result = await post("/api/c2.2/candidate-production/pause", { paused: true });
        productionFeedback(result.message || "暂停请求已保存。");
        await loadLiveStatus();
      } catch (error) {
        productionFeedback(`未能暂停：${error.message}`);
      }
    };
    const openLegacy = document.querySelector("#c22OpenLegacy");
    if (openLegacy) {
      openLegacy.onclick = () => {
        if (!legacyDetails) return;
        legacyDetails.open = true;
        legacyDetails.scrollIntoView({ behavior: "smooth", block: "start" });
      };
    }
  }

  if (page === "new-token-update.html") {
    const hero = document.querySelector(".new-token-update-hero");
    if (hero) hero.insertAdjacentElement("afterend", panel);
    else host.insertBefore(panel, host.firstChild);
    screeningPage();
    bindControls("screening");
    renderLiveStatus("screening", { jobs: snapshot.jobs || {}, config: snapshot.config || {} }, null);
    loadLiveStatus();
  } else if (page === "update-center.html") {
    host.insertBefore(panel, host.firstChild);
    trackingPage();
    collapseLegacyTrackingPage();
    bindControls("convexity_tracking");
    renderLiveStatus("convexity_tracking", { jobs: snapshot.jobs || {}, config: snapshot.config || {} }, null);
    loadLiveStatus();
  } else {
    host.insertBefore(panel, host.firstChild);
    overview();
  }
}());
