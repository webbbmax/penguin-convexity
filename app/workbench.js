(function convexityWorkbenchApp() {
  const foundation = window.PENGUIN_CONVEXITY_FOUNDATION;
  const masterPool = window.PENGUIN_CONVEXITY_MASTER_POOL;
  const sourceDiscovery = window.PENGUIN_CONVEXITY_SOURCE_DISCOVERY;
  const discoveries = window.PENGUIN_NETWORK_DISCOVERIES;
  const candidates = window.PENGUIN_CONVEXITY_CANDIDATES;
  const scanCenter = window.PENGUIN_CONVEXITY_SCAN_CENTER;
  const updates = window.PENGUIN_CONVEXITY_UPDATE_CENTER;
  const tracking = window.PENGUIN_CONVEXITY_TRACKING_TASKS;
  const changeState = window.PENGUIN_CONVEXITY_CHANGE_EXPLANATIONS;
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
  const statusLabels = {
    success: "成功",
    partial_success: "部分成功",
    failed: "失败",
    skipped: "跳过",
    no_data: "没有返回数据",
    running: "运行中",
    completed_continuing: "已完成，可继续续扫",
    completed_review: "已完成，存在待复核项",
    continuing: "本轮完成，可继续扫描",
    review: "待人工复核",
    restricted: "访问受限，已保留旧数据",
  };
  const updateStatusApiUrl = location.pathname.startsWith("/convexity/")
    ? "/api/convexity/update-status"
    : "/api/update-status";

  if (!foundation || !masterPool || !sourceDiscovery || !discoveries || !candidates || !updates || !tracking || !changeState) {
    byId("workbenchRecommendationTitle").textContent = "后台快照读取不完整";
    byId("workbenchRecommendationNote").textContent = "请重新打开企鹅投研；若仍无数据，再进入更新中心检查。";
    byId("workbenchRunSummary").innerHTML = '<p class="empty-feedback">请重新打开企鹅投研；若仍无数据，再进入数据与运行检查。</p>';
    return;
  }

  const latestRun = updates.latestRun || foundation.latestRun;
  const sourceStats = latestRun?.sourceStats || [];
  const sourceIssues = sourceStats.filter((item) => (
    item.actionKind === "retry" || item.displayStatus === "failed"
  ));
  const issueCount = Math.max(
    sourceIssues.length + Number(latestRun?.error_count || 0),
    0,
  );
  const latestTrackingResults = [];
  const seenTrackingTasks = new Set();
  (updates.trackingResults || []).forEach((item) => {
    if (!item.tracking_task_id || seenTrackingTasks.has(item.tracking_task_id)) return;
    seenTrackingTasks.add(item.tracking_task_id);
    latestTrackingResults.push(item);
  });
  const trackingIssues = latestTrackingResults.filter((item) => (
    item.retryable
    && ["not_requested", "pending", "failed"].includes(item.retry_status)
  ));
  const trackingChangeCutoff = Date.now() - 24 * 60 * 60 * 1000;
  const materialTrackingChanges = latestTrackingResults.filter((item) => {
    const finishedAt = new Date(item.finished_at || item.started_at || 0).getTime();
    return finishedAt >= trackingChangeCutoff && (
      ["upgrade", "stop"].includes(item.decision)
      || Number(item.new_findings_count || 0) > 0
    );
  });
  const dueTrackingCount = Number(tracking.counts?.due || 0);
  const pendingDecisionReviewCount = Number(changeState.counts?.decisionReviewPending || 0);
  const dueDecisionFollowUpCount = Number(tracking.counts?.decisionFollowUpDue || 0);
  const pendingDecisionFollowUpCount = Number(tracking.counts?.decisionFollowUpPending || 0);
  const failedDecisionFollowUpCount = Number(tracking.counts?.decisionFollowUpFailed || 0);
  const completedDecisionFollowUpCount = Number(tracking.counts?.decisionFollowUpCompleted || 0);
  const completedDecisionReviewCount = (
    Number(changeState.counts?.decisionReviewConfirmed || 0)
    + Number(changeState.counts?.decisionReviewRejected || 0)
  );
  const executedTrackingCount = Number(tracking.counts?.executed || 0);
  const evidenceTrackingChanges = materialTrackingChanges.filter(
    (item) => !["upgrade", "stop"].includes(item.decision),
  );
  const updateIssueCount = issueCount + trackingIssues.length;
  const pendingCount = (
    dueTrackingCount
    + evidenceTrackingChanges.length
    + updateIssueCount
    + dueDecisionFollowUpCount
    + failedDecisionFollowUpCount
  );
  byId("workbenchLatestRunTime").textContent = latestRun
    ? `最近运行 ${dateTime(latestRun.finished_at || latestRun.started_at)}`
    : "尚未执行正式更新";
  byId("workbenchProjectTotal").textContent = sourceDiscovery.counts.machineProjects;
  byId("workbenchDiscoveryTotal").textContent = sourceDiscovery.counts.rawDiscoveries;
  byId("workbenchPendingTotal").textContent = pendingCount;
  byId("workbenchActionableTotal").textContent = candidates.counts.actionable;

  function setClosureStage(id, state, status, note) {
    const stage = byId(id);
    stage.dataset.state = state;
    stage.querySelector("strong").textContent = status;
    stage.querySelector("small").textContent = note;
  }

  setClosureStage(
    "workbenchClosureUpdate",
    updateIssueCount ? "issue" : latestRun ? "complete" : "waiting",
    updateIssueCount
      ? `${updateIssueCount}项运行异常`
      : latestRun
        ? "最近更新已完成"
        : "尚未执行正式更新",
    updateIssueCount
      ? "先处理失败或部分完成任务"
      : latestRun
        ? `${dateTime(latestRun.finished_at || latestRun.started_at)}形成当前数据截点`
        : "运行一次更新后建立数据截点",
  );
  setClosureStage(
    "workbenchClosureTracking",
    trackingIssues.length
      ? "issue"
      : dueTrackingCount
        ? "attention"
        : executedTrackingCount
          ? "complete"
          : "waiting",
    trackingIssues.length
      ? `${trackingIssues.length}个跟踪任务失败`
      : dueTrackingCount
        ? `${dueTrackingCount}个任务已经到期`
        : executedTrackingCount
          ? `${executedTrackingCount}个项目已有执行记录`
          : "暂无项目到达复查时间",
    trackingIssues.length
      ? "失败项目可以单独重试"
      : dueTrackingCount
        ? "一键更新或单项目更新均可执行"
        : executedTrackingCount
          ? "每次检查均保留来源与结论"
          : "这不是更新失败，到期后系统自动执行",
  );
  setClosureStage(
    "workbenchClosureReview",
    pendingDecisionReviewCount
      ? "attention"
      : completedDecisionReviewCount
        ? "complete"
        : "waiting",
    pendingDecisionReviewCount
      ? `${pendingDecisionReviewCount}个高影响结论待确认`
      : completedDecisionReviewCount
        ? `${completedDecisionReviewCount}个高影响结论已处理`
        : "暂无高影响结论变化",
    pendingDecisionReviewCount
      ? "仅上调与停止需要人工确认"
      : completedDecisionReviewCount
        ? "确认与驳回均保留审计记录"
        : "继续跟踪和普通证据变化自动处理",
  );
  setClosureStage(
    "workbenchClosureVerification",
    failedDecisionFollowUpCount
      ? "issue"
      : dueDecisionFollowUpCount
        ? "attention"
        : completedDecisionFollowUpCount
          ? "complete"
          : "waiting",
    failedDecisionFollowUpCount
      ? `${failedDecisionFollowUpCount}个二次验证失败`
      : dueDecisionFollowUpCount
        ? `${dueDecisionFollowUpCount}个二次验证到期`
        : completedDecisionFollowUpCount
          ? `${completedDecisionFollowUpCount}个二次验证已完成`
          : pendingDecisionFollowUpCount
            ? `${pendingDecisionFollowUpCount}个任务等待到期`
            : "尚无二次验证任务",
    failedDecisionFollowUpCount
      ? "可以在更新中心单项目重试"
      : dueDecisionFollowUpCount
        ? "确认或驳回后的验证已到执行时间"
        : completedDecisionFollowUpCount
          ? "验证结果已经回写跟踪记录"
          : pendingDecisionFollowUpCount
            ? "系统将在计划时间自动列为到期"
            : "只有处理高影响结论后才会生成",
  );
  setClosureStage(
    "workbenchClosureConclusion",
    candidates.counts.actionable ? "active" : "waiting",
    candidates.counts.actionable
      ? `${candidates.counts.actionable}个当前可行动`
      : "当前没有行动级结论",
    candidates.counts.actionable
      ? "进入机会中心查看仓位边界与失效条件"
      : `${Number(candidates.counts.qualified || 0)}个项目通过部分门槛，继续等待证据`,
  );
  byId("workbenchClosureSummary").textContent = updateIssueCount
    ? "闭环停在数据更新：先处理运行异常，再使用后续结论。"
    : dueDecisionFollowUpCount || failedDecisionFollowUpCount
        ? "闭环停在二次验证：执行后才能确认高影响结论是否继续成立。"
        : dueTrackingCount
          ? "闭环停在项目跟踪：已有项目到达自动复查时间。"
          : candidates.counts.actionable
            ? "闭环已经形成行动级结论，可以进入机会中心查看依据与边界。"
            : "闭环运行正常，当前没有行动级结论；保持观察也是有效结果。";

  const recommendation = updateIssueCount
    ? {
        title: `先处理 ${updateIssueCount} 项运行异常`,
        note: trackingIssues.length
          ? `其中${trackingIssues.length}个项目跟踪任务可以单独重试，不必重新运行全部任务。`
          : "失败或部分成功的任务可以在更新中心单独重试，不必重新运行全部任务。",
        href: "update-center.html",
        action: "处理运行异常",
      }
    : dueDecisionFollowUpCount
        ? {
            title: `执行 ${dueDecisionFollowUpCount} 个结论二次验证`,
            note: "这些任务来自已确认或未采用的高影响结论；系统会按复核结果重新检查，不会原样重复旧结论。",
            href: "update-center.html#verificationQueue",
            action: "执行二次验证",
          }
      : evidenceTrackingChanges.length
      ? {
          title: `查看 ${evidenceTrackingChanges.length} 个关键项变化`,
          note: "自动跟踪发现了新证据，但没有直接改变行动结论。",
          href: "candidate-pool.html#recentChanges",
          action: "查看最近变化",
        }
      : dueTrackingCount
        ? {
            title: `执行 ${dueTrackingCount} 个到期跟踪任务`,
            note: "这些项目已经到达复查时间；在更新中心只运行项目跟踪即可。",
            href: "update-center.html?task=tracking_task_refresh",
            action: "更新到期跟踪",
          }
      : !sourceDiscovery.counts.machineProjects
        ? {
            title: "运行首次机器发现与自动建档",
            note: "正式生产库还没有机器项目档案；本任务会从免费项目级信源开始建立只观察案例。",
            href: "update-center.html?task=source_discovery_refresh",
            action: "开始机器发现",
          }
        : {
            title: "查看当前凸性结论",
            note: "目前没有自动任务阻断；人工复核是可选纠错，可以直接查看程序生成的当前结论。",
            href: "candidate-pool.html",
            action: "打开机会中心",
          };
  byId("workbenchRecommendationTitle").textContent = recommendation.title;
  byId("workbenchRecommendationNote").textContent = recommendation.note;
  byId("workbenchRecommendationLink").href = recommendation.href;
  byId("workbenchRecommendationLink").textContent = recommendation.action;
  byId("workbenchPrimaryAction").href = recommendation.href;
  byId("workbenchPrimaryAction").textContent = recommendation.action;

  const latestCollected = Number(latestRun?.collected_count || 0);
  const qualified = Number(candidates.counts.qualified || 0);
  const setStepState = (id, state) => {
    const step = byId(id);
    step.dataset.state = state;
  };
  byId("workbenchUpdateStatus").textContent = updateIssueCount ? `${updateIssueCount}项需要处理` : latestRun ? "最近更新已完成" : "尚未运行";
  byId("workbenchUpdateNote").textContent = updateIssueCount
    ? "存在失败、部分成功或可以单独重试的任务。"
    : latestRun
      ? `${dateTime(latestRun.finished_at || latestRun.started_at)} 完成最近更新。`
      : "先运行一次更新，建立当前数据截点。";
  setStepState("workbenchStepUpdate", updateIssueCount || !latestRun ? "attention" : "complete");

  byId("workbenchDiscoveryStatus").textContent = latestRun
    ? `最近采集 ${latestCollected} 条`
    : `${masterPool.counts.discoveries}条已有记录`;
  byId("workbenchDiscoveryNote").textContent = latestRun
    ? `其中 ${sourceDiscovery.counts.machineProjects} 个建立机器观察档案，其余记录继续等待自动补证。`
    : "可以查看已经保存的发现记录。";
  setStepState("workbenchStepDiscovery", latestCollected ? "active" : "idle");

  byId("workbenchReviewStatus").textContent = `${sourceDiscovery.counts.machineProjects}个机器档案`;
  byId("workbenchReviewNote").textContent = sourceDiscovery.counts.machineProjects
    ? `${sourceDiscovery.counts.machineAssetNotIdentified}个机器档案尚未识别资产；系统会继续自动补齐，人工可选纠错。`
    : "尚未建立机器档案，请先运行机器发现与自动建档。";
  setStepState(
    "workbenchStepReview",
    sourceDiscovery.counts.machineProjects ? "active" : "attention",
  );

  byId("workbenchConclusionStatus").textContent = candidates.counts.actionable
    ? `${candidates.counts.actionable}个当前可行动`
    : "当前没有行动级项目";
  byId("workbenchConclusionNote").textContent = qualified
    ? `${qualified}个项目进入门槛但仍需补证；不为了凑数量降低标准。`
    : "可以查看只观察、反身性管理和失效项目的判断原因。";
  setStepState("workbenchStepConclusion", candidates.counts.actionable ? "active" : "idle");

  if (!latestRun) {
    byId("workbenchRunSummary").innerHTML = '<p class="empty-feedback">尚未执行正式更新。当前页面仍会显示已经保存的项目队列和候选快照。</p>';
    byId("workbenchSourceFeedback").innerHTML = "";
    return;
  }

  const latestRunStatus = latestRun.displayStatus || latestRun.status;
  byId("workbenchRunMeta").textContent = `${latestRun.job_name} · ${dateTime(latestRun.started_at)} · ${statusLabels[latestRunStatus] || latestRun.statusLabel || latestRunStatus}`;
  byId("workbenchRunSummary").innerHTML = `
    <article><span>运行状态</span><strong>${escapeHtml(statusLabels[latestRunStatus] || latestRun.statusLabel || latestRunStatus)}</strong><small>${escapeHtml(latestRun.mode)}</small></article>
    <article><span>采集</span><strong>${latestRun.collected_count}</strong><small>本次原始记录</small></article>
    <article><span>匹配</span><strong>${latestRun.matched_count}</strong><small>身份或资产匹配</small></article>
    <article><span>影子池新增</span><strong>${latestRun.shadow_added_count}</strong><small>不等于投资结论</small></article>
    <article><span>正式库新增</span><strong>${latestRun.active_added_count}</strong><small>通过当前规则</small></article>
    <article><span>错误</span><strong>${latestRun.error_count}</strong><small>可在来源反馈检查</small></article>
    <p>${escapeHtml(latestRun.zeroResultLabel || latestRun.zero_result_explanation || "本次运行已经产生有效结果。")}</p>
  `;

  byId("workbenchSourceFeedback").innerHTML = sourceStats.length
    ? sourceStats.map((item) => `
        <article class="status-${escapeHtml(item.displayStatus || item.status)}">
          <header><strong>${escapeHtml(item.sourceName || item.collector_id)}</strong><span>${escapeHtml(item.displayStatusLabel || statusLabels[item.status] || item.status)}</span></header>
          <p>采集 ${item.collected_count} · 匹配 ${item.matched_count} · 过滤 ${item.filtered_count} · 失败 ${item.failed_count}</p>
          ${item.actionKind === "continue" && item.actionTaskId ? `<a href="update-center.html?task=${escapeHtml(item.actionTaskId)}">继续扫描这个来源</a>` : ""}
          ${item.actionKind === "review" && item.actionHref ? `<a href="${escapeHtml(item.actionHref)}">进入必须处理</a>` : ""}
          ${item.error_message ? `<small>${escapeHtml(item.error_message)}</small>` : ""}
        </article>
      `).join("")
    : '<p class="empty-feedback">最近运行没有单来源明细。进入“数据与运行”可以查看完整数据库状态。</p>';
  async function applyLiveUpdateGuard() {
    try {
      const response = await fetch(updateStatusApiUrl, { cache: "no-store" });
      if (!response.ok) return;
      const status = await response.json();
      if (status.state === "running") {
        setClosureStage(
          "workbenchClosureUpdate",
          "attention",
          `正在更新：${status.taskLabel || status.taskId}`,
          "任务在后台继续运行，切换栏目不会中断。",
        );
        byId("workbenchRecommendationTitle").textContent = "等待当前更新完成";
        byId("workbenchRecommendationNote").textContent = "更新中心会持续显示正在运行的具体任务和结束结果。";
        byId("workbenchRecommendationLink").href = "update-center.html";
        byId("workbenchRecommendationLink").textContent = "查看运行状态";
        byId("workbenchPrimaryAction").href = "update-center.html";
        byId("workbenchPrimaryAction").textContent = "查看运行状态";
        return;
      }
      if (!status.recoveryAvailable || !status.recoveryTaskId) return;
      const recoveryHref = `update-center.html?task=${encodeURIComponent(status.recoveryTaskId)}`;
      setClosureStage(
        "workbenchClosureUpdate",
        "issue",
        `需要恢复：${status.taskLabel || status.recoveryTaskId}`,
        "其他成功数据已经保留，只需重新运行这一项。",
      );
      byId("workbenchPendingTotal").textContent = pendingCount + 1;
      byId("workbenchRecommendationTitle").textContent = "恢复上次未完成的更新";
      byId("workbenchRecommendationNote").textContent = "系统已识别中断任务，其他成功结果不会重复更新。";
      byId("workbenchRecommendationLink").href = recoveryHref;
      byId("workbenchRecommendationLink").textContent = "前往单项恢复";
      byId("workbenchPrimaryAction").href = recoveryHref;
      byId("workbenchPrimaryAction").textContent = "前往单项恢复";
      byId("workbenchClosureSummary").textContent = "闭环停在数据更新：恢复中断任务后会继续使用已保留的成功结果。";
    } catch (_error) {
      // 静态快照仍可使用，实时守护状态稍后在更新中心重试读取。
    }
  }

  applyLiveUpdateGuard();
})();
