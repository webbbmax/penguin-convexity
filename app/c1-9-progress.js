(function c19ProgressWidget() {
  const panel = document.getElementById("c19ProgressPanel");
  if (!panel) return;
  const stages = ["市场与退出数据", "项目与证据发现", "合约与身份归属", "项目档案与研究材料", "监控与数据健康", "研究结论与催化", "跟踪与页面发布"];
  const byId = (id) => document.getElementById(id);
  const time = (value) => {
    if (!value) return "--";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const duration = (seconds) => {
    const total = Math.max(0, Number(seconds || 0));
    if (total < 60) return `${Math.round(total)} 秒`;
    return `${Math.floor(total / 60)} 分 ${Math.round(total % 60)} 秒`;
  };
  const failureCount = (status) => {
    if (Number(status?.failedCount) > 0) return Number(status.failedCount);
    const matches = [...String(status?.message || "").matchAll(/(?:失败|未完成)\s*(\d+)\s*项/g)];
    return matches.length ? Number(matches[matches.length - 1][1]) : 0;
  };
  const conciseResult = (status, label) => {
    const failures = failureCount(status);
    if (label === "正在运行") return "任务在后台继续运行，离开页面不会中断。";
    if (label === "可能卡住") return "长时间没有新进度；已完成的数据仍保留，请查看当前阶段和日志。";
    if (label === "部分完成") return `成功数据已保留${failures ? `，${failures} 项未完成` : "，仍有未完成项"}；可在更新中心查看并单独重试。`;
    if (label === "失败") return "本轮更新未完成；上次有效结果和本轮已成功数据仍保留。";
    if (label === "已完成") return "本轮任务已经完成，结果和数据时间已保存。";
    return "当前没有任务运行；系统会按计划检查到期项目。";
  };
  const effectiveProgressStatus = (status) => {
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
  };
  function renderAdminOverview(status, label) {
    const state = byId("c19AdminSystemState");
    if (!state) return;
    const failures = failureCount(status);
    state.textContent = label;
    byId("c19AdminSystemNote").textContent = conciseResult(status, label);
    if (["失败", "部分完成"].includes(label)) {
      byId("c19AdminPendingTitle").textContent = `必须处理：${failures || 1} 项未完成`;
      byId("c19AdminPendingNote").textContent = "先查看聚合失败原因；相同问题不会逐项目铺满页面。";
    } else if (["正在运行", "可能卡住"].includes(label)) {
      byId("c19AdminPendingTitle").textContent = label === "可能卡住" ? "建议处理：检查当前阶段" : "无需处理：任务仍在运行";
      byId("c19AdminPendingNote").textContent = label === "可能卡住" ? "先检查最近心跳和日志，再决定是否重试。" : "可以离开页面，返回后会继续读取同一任务。";
    } else {
      byId("c19AdminPendingTitle").textContent = "当前无需操作";
      byId("c19AdminPendingNote").textContent = "系统会按计划继续检查；只有异常或重大变更才需要处理。";
    }
    byId("c19AdminResultTitle").textContent = status?.finishedAt ? `${status.taskLabel || "最近任务"} · ${label}` : "尚无新的运行结果";
    byId("c19AdminResultNote").textContent = status?.finishedAt ? `${conciseResult(status, label)} 最近结果时间：${time(status.finishedAt)}。` : "运行完成后会在这里显示成功范围、失败范围和数据时间。";
    const primary = byId("c19AdminPrimary");
    primary.href = status?.taskId ? `update-center.html?task=${encodeURIComponent(status.taskId)}` : "update-center.html";
    primary.textContent = ["失败", "部分完成"].includes(label) ? `处理 ${failures || 1} 个未完成项` : ["正在运行", "可能卡住"].includes(label) ? "查看当前更新" : "查看最近结果";
  }
  function render(status) {
    status = effectiveProgressStatus(status);
    const running = status?.state === "running";
    const stage = Math.max(0, Math.min(7, Number(status?.progressStageIndex || (running ? 1 : 0))));
    const total = Number(status?.progressComponentTotal || 0);
    const completed = Number(status?.progressCompletedComponents || 0);
    const percent = status?.state === "success" ? 100 : total ? Math.round(completed / total * 100) : Math.round(stage / 7 * 100);
    const age = status?.progressHeartbeatAt ? Date.now() - new Date(status.progressHeartbeatAt).getTime() : 0;
    const stuck = running && Number.isFinite(age) && age > 180000;
    const label = stuck ? "可能卡住" : running ? "正在运行" : status?.state === "partial_success" ? "部分完成" : status?.state === "failed" ? "失败" : status?.state === "success" ? "已完成" : "等待任务";
    const badge = byId("c19ProgressState");
    badge.textContent = label;
    badge.className = `c19-badge ${stuck || status?.state === "failed" ? "bad" : status?.state === "partial_success" ? "warn" : status?.state === "success" ? "good" : ""}`;
    byId("c19ProgressTitle").textContent = `${status?.taskLabel || "凸性全量更新"} · ${label}`;
    byId("c19ProgressDetail").textContent = conciseResult(status, label);
    byId("c19ProgressBar").style.width = `${percent}%`;
    byId("c19ProgressStage").textContent = `阶段 ${stage || "--"} / 7`;
    byId("c19ProgressComponents").textContent = total ? `已处理 ${completed} / ${total} 个工作单元` : "工作单元总量待确认";
    byId("c19ProgressHeartbeat").textContent = `最近心跳 ${time(status?.progressHeartbeatAt)}`;
    byId("c19ProgressElapsed").textContent = `已运行 ${duration(status?.progressElapsedSeconds)}`;
    byId("c19ProgressCurrent").textContent = running
      ? status?.progressCurrentItem || "正在准备"
      : status?.state === "success"
        ? "本轮任务已经结束，结果和数据时间已保存"
        : status?.state === "partial_success"
          ? "本轮任务已经结束，请查看失败原因并单独重试"
          : status?.state === "failed"
            ? "本轮任务已经停止，请查看失败范围和可重试入口"
            : "--";
    const eta = status?.progressEtaSeconds;
    byId("c19ProgressEta").textContent = eta != null && Number.isFinite(Number(eta)) && Number(eta) > 0 ? `预计还需 ${duration(eta)}` : "剩余时间暂无法准确估计";
    byId("c19ProgressStages").innerHTML = stages.map((item, index) => `<span class="c19-stage ${index + 1 < stage || status?.state === "success" ? "is-done" : index + 1 === stage && stuck ? "is-failed" : index + 1 === stage && running ? "is-active" : ""}">${index + 1}. ${item}</span>`).join("");
    renderAdminOverview(status, label);
  }
  let timer;
  async function sync() {
    try {
      const response = await fetch("/api/update-status", { cache: "no-store" });
      if (!response.ok) throw new Error("状态读取失败");
      const status = await response.json();
      render(status);
      window.clearTimeout(timer);
      if (status.state === "running") timer = window.setTimeout(sync, 1500);
    } catch (_error) {
      render({ state: "idle", message: "暂时无法读取后台状态；更新中心可以重试。" });
    }
  }
  sync();
})();
