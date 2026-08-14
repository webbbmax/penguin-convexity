(function initializeUpdateCenter() {
  const snapshot = window.PENGUIN_CONVEXITY_UPDATE_CENTER;
  const sources = window.PENGUIN_CONVEXITY_SOURCE_REGISTRY;
  const trackingState = window.PENGUIN_CONVEXITY_TRACKING_TASKS;
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const isHttpUrl = (value) => /^https?:\/\//i.test(String(value || ""));
  const dateTime = (value) => {
    if (!value) return "--";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const duration = (value) => {
    const milliseconds = Number(value || 0);
    if (!milliseconds) return "--";
    if (milliseconds < 1000) return `${milliseconds}毫秒`;
    if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)}秒`;
    return `${(milliseconds / 60000).toFixed(1)}分钟`;
  };
  const apiUrl = location.pathname.startsWith("/convexity/")
    ? "/api/convexity/update-task"
    : "/api/update-task";
  const statusApiUrl = location.pathname.startsWith("/convexity/")
    ? "/api/convexity/update-status"
    : "/api/update-status";
  const scrollStorageKey = "convexity-update-scroll-y";
  const completionStorageKey = "convexity-update-last-completion";
  const sourceMap = Object.fromEntries(
    (sources?.sources || []).map((item) => [item.source_id, item]),
  );
  let currentUpdateStatus = null;
  let statusPollTimer = null;
  let requestOwnedByPage = false;
  let scrollSaveTimer = null;
  let changePage = 0;
  let trackingPage = 0;
  const changePageSize = 80;
  const trackingPageSize = 20;

  function statusFailureCount(status) {
    if (Number(status?.failedCount) > 0) return Number(status.failedCount);
    const matches = [...String(status?.message || "").matchAll(/(?:失败|未完成)\s*(\d+)\s*项/g)];
    return matches.length ? Number(matches[matches.length - 1][1]) : 0;
  }

  function conciseStatusMessage(status) {
    const failed = statusFailureCount(status);
    if (status?.state === "running") return "任务正在后台运行；离开页面或刷新不会中断，返回后会继续读取同一状态。";
    if (status?.state === "partial_success") return `本轮成功数据已保留${failed ? `，${failed} 项未完成` : "，仍有未完成项"}；请先查看下方聚合原因，再单独重试。`;
    if (["failed", "error"].includes(status?.state)) return "本轮更新未完成；上次有效结果和本轮已成功数据仍保留，可按失败范围重试。";
    if (status?.state === "success") return "本轮任务已完成，结果和数据时间已经保存。";
    return "当前没有任务运行；可以选择一项更新，开始后这里会持续显示进度。";
  }

  function effectiveProgressStatus(status) {
    if (!status || !["running", "partial_success", "success", "failed"].includes(status.progressState)) return status;
    const heartbeat = new Date(status.progressHeartbeatAt || 0).getTime();
    const finished = new Date(status.finishedAt || 0).getTime();
    if (!Number.isFinite(heartbeat) || (Number.isFinite(finished) && heartbeat <= finished)) return status;
    const running = status.progressState === "running";
    return {
      ...status,
      state: status.progressState,
      active: running,
      taskId: status.progressTaskId || status.taskId,
      taskLabel: status.progressTaskLabel || status.taskLabel,
      startedAt: status.progressStartedAt || status.startedAt,
      finishedAt: running ? null : status.progressHeartbeatAt,
      recoveryAvailable: ["partial_success", "failed"].includes(status.progressState),
    };
  }

  function feedback(type, title, detail) {
    const target = byId("updateFeedback");
    target.hidden = false;
    target.className = `update-feedback is-${type}`;
    target.innerHTML = `<strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p>`;
  }

  function saveScrollPosition() {
    sessionStorage.setItem(scrollStorageKey, String(Math.max(0, window.scrollY)));
  }

  function restoreScrollPosition() {
    const saved = Number(sessionStorage.getItem(scrollStorageKey));
    if (!Number.isFinite(saved) || saved <= 0) return;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => window.scrollTo({ top: saved, behavior: "instant" }));
    });
  }

  function activityClass(status) {
    if (status?.state === "running") return "running";
    if (status?.state === "partial_success") return "warning";
    if (status?.state === "failed" || status?.state === "error") return "error";
    if (status?.state === "success") return "success";
    return "idle";
  }

  function effectiveActivityStatus(status) {
    status = effectiveProgressStatus(status);
    if (status?.state === "running" || !snapshot.latestRun) return status;
    const latest = snapshot.latestRun;
    const latestTime = new Date(
      latest.finished_at || latest.started_at || 0,
    ).getTime();
    const statusTime = new Date(
      status?.finishedAt || status?.startedAt || 0,
    ).getTime();
    if (
      Number.isFinite(statusTime)
      && Number.isFinite(latestTime)
      && statusTime >= latestTime
    ) {
      return status;
    }
    return {
      state: latest.status,
      taskId: latest.taskId,
      taskLabel: latest.taskLabel,
      message: latest.zero_result_explanation || "运行结果已经写入更新记录。",
      startedAt: latest.started_at,
      finishedAt: latest.finished_at || latest.started_at,
    };
  }

  function renderActivity(status) {
    status = effectiveActivityStatus(status);
    const target = byId("updateActivity");
    const stateClass = activityClass(status);
    target.className = `update-activity is-${stateClass}`;
    if (status?.state === "running") {
      byId("updateActivityTitle").textContent = `正在更新：${status.taskLabel || status.taskId}`;
      byId("updateActivityDetail").textContent = "任务在后台继续运行。可以切换栏目，返回后仍会显示这一项。";
      byId("updateActivityTime").textContent = `开始于 ${dateTime(status.startedAt)}`;
      return;
    }
    if (status?.taskLabel && status?.finishedAt) {
      const titlePrefix = stateClass === "error"
        ? "最近失败"
        : stateClass === "warning"
          ? "最近部分完成"
          : "最近完成";
      byId("updateActivityTitle").textContent = `${titlePrefix}：${status.taskLabel}`;
      byId("updateActivityDetail").textContent = conciseStatusMessage(status);
      byId("updateActivityTime").textContent = `结束于 ${dateTime(status.finishedAt)}`;
      return;
    }
    byId("updateActivityTitle").textContent = "当前没有更新任务运行";
    byId("updateActivityDetail").textContent = "可以选择一项单独更新；开始后这里会始终显示具体任务名称。";
    byId("updateActivityTime").textContent = "后台空闲";
  }

  function renderWatchdog(status) {
    status = effectiveProgressStatus(status);
    const watchdog = status?.watchdog || {};
    const target = byId("updateWatchdog");
    const running = status?.state === "running";
    const state = running ? "monitoring" : watchdog.state || "idle";
    target.className = `update-watchdog is-${state}`;
    byId("watchdogState").textContent = running ? "正在守护" : watchdog.label || "等待检查";
    byId("watchdogMessage").textContent = status?.state ? conciseStatusMessage(status) : "尚未取得守护状态。";
    byId("watchdogCheckedAt").textContent = watchdog.lastCheckedAt
      ? `最近检查 ${dateTime(watchdog.lastCheckedAt)}`
      : "尚未完成检查";
    byId("watchdogDataProtection").textContent = watchdog.dataProtection
      || "单项失败不会清除其他成功结果。";
    byId("watchdogTimeout").textContent = watchdog.sourceTimeoutSeconds
      ? `单个外部请求约${watchdog.sourceTimeoutSeconds}秒超时，超时后保留为可重试记录`
      : "外部信源超时会转为可重试记录";
    byId("watchdogRecoverySummary").textContent = running
      ? `${status.taskLabel || status.taskId}仍在运行`
      : status?.recoveryAvailable
      ? `${status.taskLabel || status.taskId}需要恢复`
      : watchdog.recoveredRunCount
        ? `已自动接管${watchdog.recoveredRunCount}次中断`
        : "尚未发现中断任务";
    byId("watchdogRecoveryTime").textContent = running
      ? "完成前无需重试"
      : watchdog.lastRecoveryAt
      ? `最近接管 ${dateTime(watchdog.lastRecoveryAt)}`
      : "没有待恢复任务";
    const action = byId("watchdogRecoveryAction");
    const recoveryTaskId = status?.recoveryTaskId || "";
    action.hidden = !status?.recoveryAvailable || !recoveryTaskId;
    action.dataset.updateTask = recoveryTaskId;
    action.dataset.retryRun = status?.recoveryRunId || "";
    action.textContent = status?.taskLabel
      ? `只重新运行“${status.taskLabel}”`
      : "只恢复这项任务";
  }

  function renderC19Progress(status) {
    const stages = ["市场与退出数据", "项目与证据发现", "合约与身份归属", "项目档案与研究材料", "监控与数据健康", "研究结论与催化", "跟踪与页面发布"];
    const progress = effectiveProgressStatus(status) || {};
    const running = progress.state === "running";
    const stageIndex = Math.max(0, Math.min(stages.length, Number(progress.progressStageIndex || (running ? 1 : 0))));
    const componentTotal = Number(progress.progressComponentTotal || 0);
    const completed = Number(progress.progressCompletedComponents || 0);
    const componentIndex = Number(progress.progressComponentIndex || completed);
    const percent = progress.progressState === "success" || progress.state === "success"
      ? 100
      : componentTotal > 0
        ? Math.round((completed / componentTotal) * 100)
        : Math.round((stageIndex / stages.length) * 100);
    const heartbeatAt = progress.progressHeartbeatAt;
    const heartbeatAge = heartbeatAt ? Date.now() - new Date(heartbeatAt).getTime() : 0;
    const likelyStuck = running && Number.isFinite(heartbeatAge) && heartbeatAge > 180000;
    const stateLabel = likelyStuck ? "可能卡住" : running ? "正在运行" : progress.state === "partial_success" ? "部分完成" : progress.state === "failed" ? "失败" : progress.state === "success" ? "已完成" : "等待任务";
    const badge = byId("c19ProgressState");
    if (!badge) return;
    badge.textContent = stateLabel;
    badge.className = `c19-badge ${likelyStuck || progress.state === "failed" ? "bad" : progress.state === "partial_success" ? "warn" : progress.state === "success" ? "good" : ""}`;
    byId("c19ProgressTitle").textContent = running ? `${progress.taskLabel || progress.taskId || "更新任务"} · ${stateLabel}` : `${progress.taskLabel || "更新任务"} · ${stateLabel}`;
    byId("c19ProgressDetail").textContent = likelyStuck
      ? "超过 3 分钟没有心跳，先查看日志；系统没有自动把它判为失败。"
      : conciseStatusMessage(progress);
    byId("c19ProgressBar").style.width = `${Math.max(0, Math.min(100, percent))}%`;
    byId("c19ProgressStage").textContent = `阶段 ${stageIndex || "--"} / ${stages.length}`;
    byId("c19ProgressComponents").textContent = componentTotal ? `已处理 ${componentIndex || completed} / ${componentTotal} 个工作单元` : "工作单元总量待确认";
    byId("c19ProgressHeartbeat").textContent = heartbeatAt ? `最近心跳 ${dateTime(heartbeatAt)}` : "最近心跳 --";
    byId("c19ProgressElapsed").textContent = progress.progressElapsedSeconds != null ? `已运行 ${duration(Number(progress.progressElapsedSeconds) * 1000)}` : `开始于 ${dateTime(progress.startedAt)}`;
    byId("c19ProgressCurrent").textContent = running
      ? progress.progressCurrentItem || "正在准备"
      : progress.state === "success"
        ? "本轮任务已经结束，结果和数据时间已保存"
        : progress.state === "partial_success"
          ? "本轮任务已经结束，请查看下方聚合原因并单独重试"
          : progress.state === "failed"
            ? "本轮任务已经停止，请查看失败范围和可重试入口"
            : "--";
    byId("c19ProgressEta").textContent = progress.progressEtaSeconds != null && Number.isFinite(Number(progress.progressEtaSeconds)) && Number(progress.progressEtaSeconds) > 0 ? `预计还需 ${duration(Number(progress.progressEtaSeconds) * 1000)}` : "剩余时间暂无法准确估计";
    byId("c19ProgressStages").innerHTML = stages.map((label, index) => `<span class="c19-stage ${index + 1 < stageIndex || progress.state === "success" ? "is-done" : index + 1 === stageIndex && running ? "is-active" : index + 1 === stageIndex && progress.state === "failed" ? "is-failed" : ""}">${index + 1}. ${label}</span>`).join("");
  }

  function latestStatus(run) {
    if (!run) return '<span class="update-status status-never_run">尚未单独运行</span>';
    return `<span class="update-status status-${escapeHtml(run.displayStatus || run.status)}">${escapeHtml(run.statusLabel)}</span>`;
  }

  function renderTasks() {
    byId("updateTaskGrid").innerHTML = snapshot.tasks.map((task) => {
      const run = task.latestRun;
      const sourceNames = task.sourceIds
        .map((sourceId) => sourceMap[sourceId]?.name || sourceId)
        .join("、");
      return `
        <article class="update-task-card ${task.taskId === "full_refresh" ? "is-full" : ""}" data-task-card="${escapeHtml(task.taskId)}">
          <header><div><span>${task.taskId === "full_refresh" ? "ALL TASKS" : "SINGLE TASK"}</span><h3>${escapeHtml(task.label)}</h3></div>${latestStatus(run)}</header>
          <p>${escapeHtml(task.description)}</p>
          <dl>
            <div><dt>会更新什么</dt><dd>${escapeHtml(task.updates)}</dd></div>
            <div><dt>使用来源</dt><dd title="${escapeHtml(sourceNames)}">${escapeHtml(sourceNames || "本地数据")}</dd></div>
          </dl>
          <footer>
            <span>${run ? `最近 ${escapeHtml(dateTime(run.finished_at || run.started_at))} · 记录${task.latestRecordCount ?? run.collected_count}条 · 变化${task.latestChangeCount}条` : "尚无独立运行记录"}</span>
            <button type="button" data-update-task="${escapeHtml(task.taskId)}">${task.taskId === "full_refresh" ? "运行全部" : "单独更新"}</button>
          </footer>
        </article>`;
    }).join("");
    const focusedTask = new URLSearchParams(location.search).get("task");
    const focusedCard = focusedTask
      ? document.querySelector(`[data-task-card="${focusedTask}"]`)
      : null;
    focusedCard?.scrollIntoView({ block: "center" });
    focusedCard?.classList.add("is-focused");
  }

  function renderVerificationQueue() {
    const tasks = (trackingState?.tasks || []).filter(
      (item) => item.decisionFollowUp?.required,
    );
    const due = tasks.filter((item) => (
      ["pending", "failed"].includes(item.decisionFollowUp.status)
      && item.status === "due"
    ));
    const completed = tasks.filter(
      (item) => item.decisionFollowUp.status === "completed",
    );
    byId("verificationDueCount").textContent = due.length;
    byId("verificationQueueSummary").textContent = tasks.length
      ? `${due.length}个已经到期，${completed.length}个已经完成；确认上调次日复查，确认停止7天后复查，驳回立即重新取证。`
      : "当前没有确认或驳回后的二次验证任务；出现任务时会自动列在这里。";
    byId("verificationQueue").innerHTML = tasks.length
      ? tasks.map((task) => {
          const followUp = task.decisionFollowUp;
          const result = followUp.verificationResult;
          const canRun = (
            ["pending", "failed"].includes(followUp.status)
            && task.status === "due"
          );
          return `
            <article class="verification-task status-${escapeHtml(followUp.status)}">
              <header>
                <div><span>${escapeHtml(followUp.typeLabel)}</span><h3>${escapeHtml(task.projectName)}</h3></div>
                <strong>${escapeHtml(followUp.statusLabel)}</strong>
              </header>
              <p>${escapeHtml(task.whyNow)}</p>
              <dl>
                <div><dt>复核结论</dt><dd>${escapeHtml(followUp.reviewDecisionLabel)} · ${escapeHtml(followUp.reviewAction === "rejected" ? "未采用" : "已确认")}</dd></div>
                <div><dt>验证时间</dt><dd>${escapeHtml(dateTime(followUp.dueAt))}</dd></div>
                <div><dt>检查重点</dt><dd>${escapeHtml(task.nextStep)}</dd></div>
                <div><dt>最近结果</dt><dd>${result ? `${escapeHtml(result.statusLabel)} · ${escapeHtml(result.decisionLabel)}` : "尚未执行"}</dd></div>
              </dl>
              ${followUp.reviewNote ? `<small>复核备注：${escapeHtml(followUp.reviewNote)}</small>` : ""}
              <footer>
                <a href="${escapeHtml(task.detailUrl)}">查看项目任务</a>
                ${canRun ? `<button type="button" data-update-task="tracking_task_refresh" data-tracking-task="${escapeHtml(task.taskId)}">只验证这个项目</button>` : ""}
              </footer>
            </article>`;
        }).join("")
      : '<div class="update-empty">当前没有二次验证任务。系统会在结论确认或驳回后自动建立。</div>';
  }

  function trackingSourceMarkup(item) {
    const statusLabels = {
      success: "成功",
      partial_success: "部分完成",
      failed: "失败",
      no_data: "无数据",
      skipped: "跳过",
      not_run: "本轮未运行",
    };
    const sourceName = sourceMap[item.sourceId]?.name || item.sourceName;
    return `<span class="tracking-source-result status-${escapeHtml(item.status)}"><b>${escapeHtml(sourceName)}</b><small>${escapeHtml(statusLabels[item.status] || item.status)} · 项目匹配${Number(item.matchedCount || 0)}</small></span>`;
  }

  function trackingFindingsMarkup(result) {
    if (!result.findings?.length) {
      return `<p class="tracking-no-finding">已检查${result.sourceResults.filter((item) => item.status !== "not_run").length}个映射信源，本轮没有发现新的项目证据或指标变化。</p>`;
    }
    return `
      <details class="tracking-finding-details">
        <summary>${result.findings_count}条记录，其中${result.new_findings_count}条新增或变化</summary>
        <div>${result.findings.map((item) => `
          <article class="${item.isNew ? "is-new" : "is-confirmed"}">
            <strong>${item.isNew ? "新增/变化" : "重新确认"} · ${escapeHtml(item.sourceName)}</strong>
            <p>${escapeHtml(item.summary)}</p>
            <small>${escapeHtml(dateTime(item.observedAt))}${isHttpUrl(item.sourceUrl) ? ` · <a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">查看来源</a>` : ""}</small>
          </article>
        `).join("")}</div>
      </details>`;
  }

  function renderTrackingFailureGroups(records) {
    const target = byId("trackingFailureGroups");
    if (!target) return;
    const groups = new Map();
    records.filter((item) => ["failed", "partial_success"].includes(item.execution_status)).forEach((item) => {
      const failedSources = (item.sourceResults || [])
        .filter((source) => ["failed", "not_run"].includes(source.status) || Number(source.failedCount) > 0)
        .map((source) => sourceMap[source.sourceId]?.name || source.sourceName)
        .sort();
      const reason = failedSources.length ? `未完成来源：${failedSources.join("、")}` : item.error_message || item.reason || "本轮检查未完整完成";
      const key = `${item.execution_status}|${reason}`;
      const group = groups.get(key) || { reason, records: [], retryable: false, latestAt: "" };
      group.records.push(item);
      group.retryable = group.retryable || Boolean(item.retryable);
      if (!group.latestAt || new Date(item.finished_at || 0) > new Date(group.latestAt || 0)) group.latestAt = item.finished_at;
      groups.set(key, group);
    });
    const ordered = [...groups.values()].sort((left, right) => right.records.length - left.records.length);
    target.innerHTML = ordered.length
      ? `<div class="c19-failure-group-heading"><strong>最新一轮同类失败已聚合为 ${ordered.length} 组</strong><span>先看本轮影响范围和处理建议，再按需展开历史逐项目记录。</span></div>${ordered.map((group) => {
          const names = [...new Set(group.records.map((item) => item.projectName))];
          const examples = names.slice(0, 5).join("、");
          return `<details class="c19-failure-group"><summary><span><strong>影响 ${group.records.length} 条记录</strong><small>${escapeHtml(group.reason)}</small></span><em>${escapeHtml(dateTime(group.latestAt))}</em></summary><div><p>涉及项目：${escapeHtml(examples)}${names.length > 5 ? ` 等 ${names.length} 个项目` : ""}。</p><p>处理建议：${group.retryable ? "可在逐项目记录中单独重试；先确认来源恢复，避免重复运行已成功部分。" : "当前不可直接重试，请先查看运行日志和来源状态。"}</p><p>成功数据是否保留：是，本组失败不会清除其他成功结果或上次有效页面。</p></div></details>`;
        }).join("")}`
      : '<div class="c19-failure-group-heading is-clear"><strong>当前没有需要聚合的失败</strong><span>成功和无变化记录可在下方分页查看。</span></div>';
  }

  function renderTrackingExecutions() {
    const status = byId("trackingExecutionStatus").value;
    const decision = byId("trackingDecisionFilter").value;
    const query = byId("trackingExecutionSearch").value.trim().toLowerCase();
    const records = (snapshot.trackingResults || []).filter((item) => {
      if (status !== "all" && item.execution_status !== status) return false;
      if (decision !== "all" && item.decision !== decision) return false;
      if (!query) return true;
      return [
        item.projectName,
        item.task_type,
        item.reason,
        ...item.sourceResults.map((source) => source.sourceName),
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
    const latestRunId = records[0]?.run_id;
    const latestRunRecords = latestRunId ? records.filter((item) => item.run_id === latestRunId) : records;
    renderTrackingFailureGroups(latestRunRecords);
    const pageCount = Math.max(1, Math.ceil(records.length / trackingPageSize));
    trackingPage = Math.min(trackingPage, pageCount - 1);
    const pageRecords = records.slice(trackingPage * trackingPageSize, (trackingPage + 1) * trackingPageSize);
    byId("trackingExecutionSummary").textContent = snapshot.trackingResults?.length
      ? `筛选结果 ${records.length} 条 · 第 ${trackingPage + 1}/${pageCount} 页；上方只聚合最新一轮失败，历史逐项目依据保留在下方。`
      : "尚无项目到达复查时间，因此没有执行记录；这不是更新失败。到期后会由一键更新自动检查。";
    byId("trackingPageMeta").textContent = `第 ${trackingPage + 1} / ${pageCount} 页 · 每页最多 ${trackingPageSize} 条`;
    byId("trackingPreviousPage").disabled = trackingPage === 0;
    byId("trackingNextPage").disabled = trackingPage >= pageCount - 1;
    byId("trackingExecutionRows").innerHTML = pageRecords.length
      ? pageRecords.map((result) => `
          <tr>
            <td>
              <strong>${escapeHtml(result.projectName)}</strong>
              <small>${escapeHtml(result.taskTypeLabel)} · ${escapeHtml(result.priority)} · ${escapeHtml(dateTime(result.finished_at))}</small>
            </td>
            <td>
              <span class="update-status status-${escapeHtml(result.execution_status)}">${escapeHtml(result.statusLabel)}</span>
              <div class="tracking-source-results">${result.sourceResults.map(trackingSourceMarkup).join("")}</div>
              ${result.error_message ? `<p class="tracking-error-message">${escapeHtml(result.error_message)}</p>` : ""}
            </td>
            <td>${trackingFindingsMarkup(result)}</td>
            <td>
              <span class="tracking-decision decision-${escapeHtml(result.decision)}">${escapeHtml(result.decisionLabel)}</span>
              <p>${escapeHtml(result.reason)}</p>
              <small>下次复查：${escapeHtml(dateTime(result.next_review_at))}</small>
              ${result.retryable && ["not_requested", "pending", "failed"].includes(result.retry_status)
                ? `<button type="button" data-update-task="tracking_task_refresh" data-tracking-task="${escapeHtml(result.tracking_task_id)}">只重试这个项目</button>`
                : ""}
            </td>
          </tr>
        `).join("")
      : '<tr><td colspan="4" class="update-empty">当前筛选下没有项目跟踪执行记录。</td></tr>';
  }

  function resetAndRenderTrackingExecutions() {
    trackingPage = 0;
    renderTrackingExecutions();
  }

  function errorMarkup(run) {
    if (!run.errors.length) {
      return '<span class="update-no-error">没有阻断错误</span>';
    }
    return `
      <details class="update-error-details">
        <summary>${run.errors.length}条失败或异常</summary>
        <div>
          ${run.errors.map((error) => `
            <article>
              <strong>${escapeHtml(error.taskNameLabel || error.task_name)}</strong>
              <p>${escapeHtml(error.message)}</p>
              <small>${escapeHtml(error.sourceName)} · 已尝试${error.attempts}次 · ${escapeHtml(error.retry_status)}</small>
              ${error.retryable && error.retryTaskId
                ? `<button type="button" data-update-task="${escapeHtml(error.retryTaskId)}" data-retry-run="${escapeHtml(run.run_id)}">只重试这类任务</button>`
                : ""}
            </article>
          `).join("")}
        </div>
      </details>`;
  }

  function sourceStatMarkup(stat) {
    const action = stat.actionKind === "review" && stat.actionHref
      ? `<a href="${escapeHtml(stat.actionHref)}">${escapeHtml(stat.actionLabel)}</a>`
      : ["retry", "continue"].includes(stat.actionKind) && stat.actionTaskId
        ? `<button type="button" data-update-task="${escapeHtml(stat.actionTaskId)}">${escapeHtml(stat.actionLabel)}</button>`
        : "";
    return `
      <span class="update-source-stat status-${escapeHtml(stat.displayStatus || stat.status)}">
        <b>${escapeHtml(stat.sourceName)}：${escapeHtml(stat.displayStatusLabel || stat.statusLabel)}</b>
        <small>匹配 ${stat.matched_count} / 采集 ${stat.collected_count}</small>
        ${action}
      </span>`;
  }

  function renderRuns() {
    const latest = snapshot.latestRun;
    byId("latestUpdateRunMeta").textContent = latest
      ? `${latest.taskLabel} · ${dateTime(latest.started_at)} · ${latest.statusLabel}`
      : "尚无更新运行记录。";
    byId("updateRunRows").innerHTML = snapshot.runs.length
      ? snapshot.runs.map((run) => `
          <tr>
            <td><strong>${escapeHtml(run.taskLabel)}</strong><small>${escapeHtml(dateTime(run.started_at))} · ${escapeHtml(run.mode)} · ${escapeHtml(duration(run.duration_ms))}</small></td>
            <td>
              ${latestStatus(run)}
              <b>采集${run.collected_count} · 标准化${run.normalized_count} · 匹配${run.matched_count} · 错误${run.error_count}</b>
              <p>${escapeHtml(run.zero_result_explanation || run.zeroResultLabel)}</p>
            </td>
            <td>${run.sourceStats.length
              ? run.sourceStats.map(sourceStatMarkup).join("")
              : '<span class="update-no-error">旧记录未保留逐来源统计</span>'}</td>
            <td>${errorMarkup(run)}</td>
          </tr>
        `).join("")
      : '<tr><td colspan="4" class="update-empty">尚无更新历史。</td></tr>';
  }

  function fillChangeFilters() {
    byId("changeTaskFilter").insertAdjacentHTML(
      "beforeend",
      snapshot.tasks
        .filter((task) => task.taskId !== "full_refresh")
        .map((task) => `<option value="${escapeHtml(task.taskId)}">${escapeHtml(task.label)}</option>`)
        .join(""),
    );
    const types = [...new Map(snapshot.changes.map((item) => [item.eventType, item.eventLabel])).entries()];
    byId("changeTypeFilter").insertAdjacentHTML(
      "beforeend",
      types.map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join(""),
    );
  }

  function changesMarkup(item) {
    if (!item.changes?.length) return escapeHtml(item.summary);
    return `
      <p>${escapeHtml(item.summary)}</p>
      <ul>${item.changes.map((change) => `
        <li><b>${escapeHtml(change.field)}</b>：${change.before == null ? "首次记录" : escapeHtml(change.before)} → ${escapeHtml(change.after)}${change.changePct == null ? "" : `（${Number(change.changePct).toFixed(2)}%）`}</li>
      `).join("")}</ul>`;
  }

  function renderChanges() {
    const task = byId("changeTaskFilter").value;
    const type = byId("changeTypeFilter").value;
    const query = byId("changeSearch").value.trim().toLowerCase();
    const records = snapshot.changes.filter((item) => {
      if (task !== "all" && item.taskId !== task) return false;
      if (type !== "all" && item.eventType !== type) return false;
      if (!query) return true;
      return [
        item.projectKey,
        item.assetKey,
        item.chain,
        item.sourceName,
        item.summary,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
    const pageCount = Math.max(1, Math.ceil(records.length / changePageSize));
    changePage = Math.min(changePage, pageCount - 1);
    const pageRecords = records.slice(
      changePage * changePageSize,
      (changePage + 1) * changePageSize,
    );
    byId("visibleChangeCount").textContent = records.length;
    byId("changePageMeta").textContent = `第 ${changePage + 1} / ${pageCount} 页 · 每页最多 ${changePageSize} 条`;
    byId("changePreviousPage").disabled = changePage === 0;
    byId("changeNextPage").disabled = changePage >= pageCount - 1;
    byId("updateChangeRows").innerHTML = pageRecords.length
      ? pageRecords.map((item) => `
          <tr>
            <td><strong>${escapeHtml(item.eventLabel)}</strong><small>${escapeHtml(dateTime(item.collectedAt))}</small></td>
            <td><b>${escapeHtml(item.projectKey || "未归属项目")}</b><small>${escapeHtml([item.assetKey, item.chain].filter(Boolean).join(" · ") || "资产待归属")}</small></td>
            <td>${changesMarkup(item)}</td>
            <td><b>${escapeHtml(item.sourceName)}</b>${isHttpUrl(item.sourceUrl) ? `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">打开原始来源</a>` : "<small>本地记录</small>"}</td>
          </tr>
        `).join("")
      : '<tr><td colspan="4" class="update-empty">当前筛选条件下没有变化记录。</td></tr>';
  }

  function resetAndRenderChanges() {
    changePage = 0;
    renderChanges();
  }

  function setRunning(status) {
    const running = status?.state === "running";
    const activeTaskId = running ? status.taskId : "";
    document.querySelectorAll("[data-update-task]").forEach((button) => {
      button.disabled = running;
      if (!button.dataset.defaultLabel) {
        button.dataset.defaultLabel = button.textContent.trim();
      }
      button.textContent = running
        ? button.dataset.updateTask === activeTaskId
          ? "正在更新…"
          : "等待当前任务"
        : button.dataset.defaultLabel;
    });
    document.querySelectorAll("[data-task-card]").forEach((card) => {
      const active = running && card.dataset.taskCard === activeTaskId;
      card.classList.toggle("is-running", active);
      card.setAttribute("aria-busy", active ? "true" : "false");
    });
    renderActivity(status);
    renderWatchdog(status);
    renderC19Progress(status);
  }

  function scheduleStatusPoll() {
    window.clearTimeout(statusPollTimer);
    if (window.PENGUIN_CONVEXITY_C24_ADMIN) return;
    statusPollTimer = window.setTimeout(() => syncUpdateStatus(true), 1200);
  }

  async function syncUpdateStatus(reloadWhenFinished = false) {
    if (window.PENGUIN_CONVEXITY_C24_ADMIN) {
      window.clearTimeout(statusPollTimer);
      return;
    }
    try {
      const response = await fetch(statusApiUrl, { cache: "no-store" });
      if (!response.ok) throw new Error(`状态读取失败：${response.status}`);
      const status = await response.json();
      const previousToken = currentUpdateStatus?.state === "running"
        ? currentUpdateStatus.runToken
        : "";
      currentUpdateStatus = status;
      setRunning(status);
      if (status.state === "running") {
        scheduleStatusPoll();
        return;
      }
      window.clearTimeout(statusPollTimer);
      if (
        reloadWhenFinished
        && previousToken
        && previousToken === status.runToken
        && !requestOwnedByPage
        && sessionStorage.getItem(completionStorageKey) !== status.runToken
      ) {
        sessionStorage.setItem(completionStorageKey, status.runToken);
        sessionStorage.setItem("convexity-update-feedback", JSON.stringify({
          type: status.state === "success" ? "success" : status.state === "partial_success" ? "warning" : "error",
          title: status.state === "success" ? "更新完成" : status.state === "partial_success" ? "更新部分完成" : "更新失败",
          detail: status.message,
        }));
        saveScrollPosition();
        location.reload();
      }
    } catch (error) {
      window.clearTimeout(statusPollTimer);
      if (!currentUpdateStatus?.active) {
        setRunning({ state: "idle" });
        feedback("error", "暂时无法读取后台状态", error.message);
      }
    }
  }

  async function runTask(taskId, retryRunId = "", trackingTaskId = "") {
    const task = snapshot.tasks.find((item) => item.taskId === taskId);
    const localStatus = {
      state: "running",
      active: true,
      taskId,
      taskLabel: task?.label || taskId,
      retryRunId,
      trackingTaskId,
      runToken: new Date().toISOString(),
      startedAt: new Date().toISOString(),
    };
    requestOwnedByPage = true;
    currentUpdateStatus = localStatus;
    setRunning(localStatus);
    scheduleStatusPoll();
    feedback(
      "running",
      `正在${retryRunId ? "重试" : "运行"}：${task?.label || taskId}`,
      "任务在后台静默运行。可以切换栏目，返回更新中心后仍会显示这一项。",
    );
    try {
      const response = await fetch(apiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ taskId, retryRunId, trackingTaskId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok && payload.updateStatus?.state === "running") {
        requestOwnedByPage = false;
        currentUpdateStatus = payload.updateStatus;
        setRunning(payload.updateStatus);
        scheduleStatusPoll();
        feedback("warning", "已有任务正在更新", `当前正在更新：${payload.updateStatus.taskLabel || payload.updateStatus.taskId}`);
        return;
      }
      if (!response.ok) throw new Error(payload.error || payload.message || `请求失败：${response.status}`);
      const completedStatus = payload.updateStatus || {};
      if (completedStatus.runToken) {
        sessionStorage.setItem(completionStorageKey, completedStatus.runToken);
      }
      sessionStorage.setItem("convexity-update-feedback", JSON.stringify({
        type: payload.status === "success" ? "success" : payload.status === "partial_success" ? "warning" : "error",
        title: payload.status === "success" ? "更新完成" : payload.status === "partial_success" ? "更新部分完成" : "更新失败",
        detail: payload.message,
      }));
      saveScrollPosition();
      location.reload();
    } catch (error) {
      requestOwnedByPage = false;
      await syncUpdateStatus();
      if (currentUpdateStatus?.state !== "running") {
        feedback("error", "更新未能完成", error.message);
      }
    }
  }

  async function syncC18Scheduler() {
    try {
      const response = await fetch("/api/c1-8/status", { cache: "no-store" });
      if (!response.ok) throw new Error("自动调度状态读取失败");
      const status = await response.json();
      byId("c18SchedulerState").textContent = status.statusLabel || "状态待确认";
      byId("c18SchedulerReason").textContent = `${status.reason || "系统会按计划检查。"} 负责人：${status.owner || "系统自动运行"}；下一步：${status.nextAction || "等待下一次计划检查。"}`;
      byId("c18SchedulerDaily").textContent = status.dailyTime || "08:00";
      byId("c18SchedulerTime").value = status.dailyTime || "08:00";
      byId("c18SchedulerNextDaily").textContent = `下次：${dateTime(status.nextDailyRunAt)}`;
      byId("c18SchedulerDue").textContent = `${Number(status.dueCount || 0).toLocaleString("zh-CN")} 个到期`;
      byId("c18SchedulerNextHourly").textContent = `下次：${dateTime(status.nextHourlyCheckAt)}`;
      byId("c18SchedulerHuman").textContent = trackingState.tasks.filter((item) => item.decisionReview?.required && item.decisionReview.status === "pending").length;
      byId("c18SchedulerPause").disabled = Boolean(status.paused);
      byId("c18SchedulerResume").disabled = !status.paused;
    } catch (error) {
      byId("c18SchedulerState").textContent = "状态暂不可用";
      byId("c18SchedulerReason").textContent = error.message;
    }
  }

  async function changeC18Scheduler(payload, message) {
    const feedbackTarget = byId("c18SchedulerFeedback");
    feedbackTarget.textContent = "正在保存自动运行设置…";
    try {
      const response = await fetch("/api/c1-8/scheduler", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "自动运行设置保存失败");
      feedbackTarget.textContent = message;
      await syncC18Scheduler();
    } catch (error) {
      feedbackTarget.textContent = error.message;
    }
  }

  if (!snapshot || !sources || !trackingState) {
    feedback("error", "更新中心快照未生成", "请返回凸性工作台后重新打开更新中心。");
    return;
  }

  byId("updatePolicy").textContent = snapshot.policy;
  byId("updateGeneratedAt").textContent = `后台快照：${dateTime(snapshot.generatedAt)}`;
  byId("updateTaskCount").textContent = snapshot.counts.tasks;
  byId("updateRunCount").textContent = snapshot.counts.runs;
  byId("updateChangeCount").textContent = snapshot.counts.changes;
  byId("updateRetryCount").textContent = snapshot.counts.retryable;
  byId("updateSourceCount").textContent = snapshot.counts.sources;
  byId("trackingExecutionCount").textContent = snapshot.counts.trackingExecutions || 0;
  renderTasks();
  renderVerificationQueue();
  renderTrackingExecutions();
  renderRuns();
  fillChangeFilters();
  renderChanges();
  restoreScrollPosition();
  syncUpdateStatus();
  syncC18Scheduler();

  const storedFeedback = sessionStorage.getItem("convexity-update-feedback");
  if (storedFeedback) {
    sessionStorage.removeItem("convexity-update-feedback");
    try {
      const item = JSON.parse(storedFeedback);
      feedback(item.type, item.title, item.detail);
    } catch (_error) {
      // Ignore malformed one-time feedback.
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-update-task]");
    if (!button || button.disabled) return;
    runTask(
      button.dataset.updateTask,
      button.dataset.retryRun || "",
      button.dataset.trackingTask || "",
    );
  });
  ["changeTaskFilter", "changeTypeFilter"].forEach((id) => {
    byId(id).addEventListener("change", resetAndRenderChanges);
  });
  byId("changeSearch").addEventListener("input", resetAndRenderChanges);
  byId("changePreviousPage").addEventListener("click", () => {
    changePage = Math.max(0, changePage - 1);
    renderChanges();
  });
  byId("changeNextPage").addEventListener("click", () => {
    changePage += 1;
    renderChanges();
  });
  ["trackingExecutionStatus", "trackingDecisionFilter"].forEach((id) => {
    byId(id).addEventListener("change", resetAndRenderTrackingExecutions);
  });
  byId("trackingExecutionSearch").addEventListener("input", resetAndRenderTrackingExecutions);
  byId("trackingPreviousPage").addEventListener("click", () => {
    trackingPage = Math.max(0, trackingPage - 1);
    renderTrackingExecutions();
  });
  byId("trackingNextPage").addEventListener("click", () => {
    trackingPage += 1;
    renderTrackingExecutions();
  });
  byId("c18SchedulerPause").addEventListener("click", () => changeC18Scheduler({ action: "pause" }, "自动运行已暂停；已有结论保留。"));
  byId("c18SchedulerResume").addEventListener("click", () => changeC18Scheduler({ action: "resume" }, "自动运行已恢复。"));
  byId("c18SchedulerSaveTime").addEventListener("click", () => changeC18Scheduler({ action: "set_time", dailyTime: byId("c18SchedulerTime").value }, "每日自动更新时间已保存。"));
  byId("c18SchedulerRunNow").addEventListener("click", async () => {
    byId("c18SchedulerFeedback").textContent = "正在请求一次到期检查…";
    try {
      const response = await fetch("/api/c1-8/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "调度请求失败");
      byId("c18SchedulerFeedback").textContent = result.message || "调度请求已接受。";
      syncC18Scheduler();
    } catch (error) {
      byId("c18SchedulerFeedback").textContent = error.message;
    }
  });
  window.addEventListener("pagehide", saveScrollPosition);
  window.addEventListener("scroll", () => {
    window.clearTimeout(scrollSaveTimer);
    scrollSaveTimer = window.setTimeout(saveScrollPosition, 120);
  }, { passive: true });
}());
