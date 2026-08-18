(function c25ManagerControlPlane() {
  "use strict";

  const page = location.pathname.split("/").pop() || "workbench.html";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
  const jsonText = (value) => esc(JSON.stringify(value == null ? null : value, null, 2));
  const apiRoot = "/api/c2.5";
  const overviewReadTimeoutMs = 8000;
  const stateLabels = {
    not_started: "尚未运行", waiting: "等待运行", launching: "正在启动", running: "运行中",
    pause_requested: "将在安全点暂停", safe_paused: "已在安全点暂停", partial: "部分完成",
    completed: "已完成", failed: "失败", blocked: "阻断", stale: "状态已过期",
    disabled: "已停用", unknown: "状态未知", success: "成功", no_data: "真实为零 / 无数据",
    quota_limited: "额度受限", source_failure: "来源失败", unsupported: "不支持",
    configuration_missing: "缺少配置", program_failure: "程序失败",
  };
  const lifecycleLabels = {
    scheduled_active: "自动启用", manual_on_demand: "手动 / 按需", active_component: "现役内部组件",
    legacy_callable: "旧版兼容", dormant_compatibility: "休眠兼容", disabled: "已停用",
    completed_one_off: "已完成的一次性任务", startup_automatic: "启动自动检查", service_component: "服务组件",
  };
  const actionLabels = {
    run_now: "立即运行任务", resume_checkpoint: "从检查点恢复任务", safe_pause: "安全暂停任务",
    cancel_pause_request: "取消暂停请求", pause_future_cycles: "暂停后续自动周期",
    resume_future_cycles: "恢复后续自动周期", set_interval: "修改任务频率",
    retry_registered_source: "重试所选来源", retry_partition: "重试所选分片",
    run_retention_sweep: "运行一次保留检查", rollback_control_change: "回滚上次频率 / 暂停变更",
    rule_create_draft: "建立规则版本草案", rule_approve_draft: "批准规则草案",
    rule_reject_draft: "拒绝规则草案", rule_rollback_version: "回滚到明确规则版本",
  };

  function fmtTime(value) {
    if (!value) return "当前数据未提供";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
  }

  function fmtValue(value, fallback = "当前数据未提供") {
    if (value === null || value === undefined || value === "") return fallback;
    if (typeof value === "boolean") return value ? "是" : "否";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function ruleValue(value, unit) {
    if (value === "enabled") return "启用为门槛";
    if (value === "disabled_as_gate") return "不作为门槛（原始证据保留）";
    if (value === "quote_success_loss_recorded") return "报价成功即可；损失比例继续记录";
    if (value === "quote_success_no_confirmed_trade_block") return "报价成功且无已确认交易阻断";
    if (value === "record_only_no_immediate_exit_gate") return "仅记录，不单独形成立即退出门槛";
    if (value === "all_frozen_public_baseline_checks") return "冻结公开底线全部检查";
    if (value === "approved_trial_public_baseline_checks") return "已批准试行公开底线检查";
    if (value === "required") return "必须满足";
    if (value === "not_required_raw_state_preserved") return "不作为门槛，原始状态保留";
    if (value === "frozen_path_conditions") return "冻结强路径条件";
    if (value === "approved_trial_path_conditions") return "已批准试行强路径条件";
    if (value === "hard_block_or_loss_gte_20_or_sell_tax_gte_20") return "硬阻断、损失或卖出税达到20%立即退出";
    if (value === "confirmed_trade_block_only") return "仅已确认交易硬阻断立即退出";
    return `${fmtValue(value)}${unit === "%" ? "%" : unit && unit !== "state" ? ` ${unit}` : ""}`;
  }

  function ruleCounts(value) {
    if (!value) return "当前没有可重放输入";
    return `输入 ${fmtValue(value.input, "0")} · 适用 ${fmtValue(value.applicable, "0")} · 等待 ${fmtValue(value.waiting, "0")} · 失败 ${fmtValue(value.failed, "0")} · 基线通过 ${fmtValue(value.baselinePassed, "0")} · 当前通过 ${fmtValue(value.effectivePassed, "0")} · 变化 ${fmtValue(value.changed, "0")}`;
  }

  function assetSetSummary(value) {
    if (!Array.isArray(value) || !value.length) return "0 个";
    const sample = value.slice(0, 3).join("、");
    return `${value.length} 个 · ${sample}${value.length > 3 ? ` · 另 ${value.length - 3} 个` : ""}`;
  }

  function ruleSample(value) {
    if (value && typeof value === "object") return `${fmtValue(value.reason)}（来源：${fmtValue(value.sourcePath)}）`;
    return fmtValue(value);
  }

  function approvalLabel(value) {
    if (value === "user_explicit_authorization") return "用户明确授权";
    if (value === "C2.4 requirements lock") return "C2.4需求锁";
    return fmtValue(value);
  }

  function status(value) {
    const key = String(value || "unknown");
    return `<span class="c25-status" data-state="${esc(key)}">${esc(stateLabels[key] || key)}</span>`;
  }

  async function requestJson(path, options) {
    const response = await fetch(`${apiRoot}${path}`, {
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let payload;
    try { payload = await response.json(); } catch (_error) { payload = { error: `服务返回了无法读取的内容（HTTP ${response.status}）` }; }
    if (!response.ok) {
      const error = new Error(payload.error || `请求失败（HTTP ${response.status}）`);
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  async function requestOverviewJson() {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), overviewReadTimeoutMs);
    try {
      return await requestJson("/control-plane", { signal: controller.signal });
    } catch (error) {
      if (controller.signal.aborted) {
        throw new Error("读取超过8秒，已停止等待；未触发任务或写入。请重新读取，或进入对应详情页核对权威状态。");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function pageHeader(title, intro, dataAsOf, readAt, eyebrow = "管理者控制面") {
    return `<header class="c25-page-header"><div><span class="c25-eyebrow">${esc(eyebrow)}</span><h1>${esc(title)}</h1><p>${esc(intro)}</p></div><div class="c25-read-times"><span>数据对应时间</span><strong>${esc(fmtTime(dataAsOf))}</strong><span>页面读取时间</span><time>${esc(fmtTime(readAt || new Date().toISOString()))}</time></div></header>`;
  }

  function setMain(markup) {
    const main = $("main");
    if (!main) return null;
    main.className = "c25-page-main";
    main.innerHTML = markup;
    return main;
  }

  function showLoading(title, intro, message) {
    setMain(`${pageHeader(title, intro, null, new Date().toISOString())}<section class="c25-empty" role="status" aria-live="polite">${esc(message)}</section>`);
  }

  function errorPage(title, error) {
    setMain(`${pageHeader(title, "页面只读取权威状态；读取失败时不会回退为乐观状态。", null, new Date().toISOString())}<section class="c25-error" role="alert"><strong>管理状态加载失败</strong><p>发生了什么：${esc(error.message || error)}</p><p>影响什么：当前页面无法证明系统真实状态，所有高影响操作保持不可用。</p><p>下一步：可安全地重新读取；这不会触发任务或修改数据。</p><button class="c25-button" id="c25ReadRetry" type="button">重新读取</button></section>`);
    const retry = $("#c25ReadRetry");
    if (retry) retry.addEventListener("click", () => location.reload());
  }

  function progress(task) {
    const value = task.progress || {};
    const percent = value.kind === "determinate" ? Number(value.percent || 0) : 0;
    const amount = value.kind === "determinate"
      ? `${fmtValue(value.completed)} / ${fmtValue(value.total)} · ${fmtValue(value.percent)}%`
      : value.kind === "indeterminate" ? `总量未知 · 已处理 ${fmtValue(value.completed, "未知")}` : "不适用";
    return `<div class="c25-progress" data-kind="${esc(value.kind || "not_applicable")}" data-state="${esc(task.liveState)}" style="--c25-progress:${percent}%"><div class="c25-progress-head"><strong>${esc(amount)}</strong>${status(task.liveState)}</div><div class="c25-progress-track" role="progressbar" aria-label="${esc(task.displayName)}进度" ${value.kind === "determinate" ? `aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"` : ""}><span></span></div><div><span>${esc(fmtValue(value.stage, "当前步骤未提供"))}</span><small> · ${esc(fmtValue(value.message))}</small></div><small>最后心跳：${esc(fmtTime(task.lastHeartbeatAt))} · 检查点：${esc(fmtValue(task.checkpoint))}</small></div>`;
  }

  function controlMarkup(task) {
    const controls = (task.controls || []).map((item) => {
      let parameter = "";
      if (item.action === "set_interval") {
        parameter = `<label class="c25-field"><span>频率</span><select data-control-parameter="intervalHours">${(item.allowedValues || [1,3,6,12,24]).map((value) => `<option value="${value}">${value}小时</option>`).join("")}</select></label>`;
      } else if (item.action === "retry_registered_source") {
        parameter = task.sources?.length ? `<label class="c25-field"><span>登记来源</span><select data-control-parameter="sourceId">${task.sources.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join("")}</select></label>` : "";
      } else if (item.action === "retry_partition") {
        const partitions = (task.partitions || []).filter((row) => ["failed", "retrying", "paused"].includes(row.state));
        parameter = partitions.length ? `<label class="c25-field"><span>失败分片</span><select data-control-parameter="partitionId">${partitions.map((row) => `<option value="${esc(row.partition_id || row.partitionId)}">${esc(row.partition_id || row.partitionId)}</option>`).join("")}</select></label>` : "";
      }
      const fixed = item.parameters ? ` data-fixed-parameters="${esc(JSON.stringify(item.parameters))}"` : "";
      const unavailable = (item.action === "retry_registered_source" && !task.sources?.length) || (item.action === "retry_partition" && !parameter);
      return `${parameter}<button class="c25-button" type="button" data-c25-control data-task-id="${esc(task.taskId)}" data-action="${esc(item.action)}"${fixed} ${unavailable ? "disabled title=\"当前没有可重试对象\"" : ""}>${esc(actionLabels[item.action] || item.label || item.action)}</button>`;
    }).join("");
    const disabled = (task.disabledControls || []).map((item) => `<span class="c25-disabled-control"><button class="c25-button" type="button" disabled>${esc(actionLabels[item.action] || item.label || item.action)}</button><small>${esc(item.reason || "当前能力边界不允许直接控制。")}</small></span>`).join("");
    return `<div class="c25-controls">${controls || ""}${disabled || ""}</div>`;
  }

  function taskFacts(task) {
    return `<dl class="c25-facts"><div><dt>机器任务 ID</dt><dd class="c25-machine">${esc(task.taskId)}</dd></div><div><dt>机器名 / 别名</dt><dd class="c25-machine">${esc((task.machineNames || []).join(" · ") || "当前数据未提供")}</dd></div><div><dt>生命周期分类</dt><dd>${esc(lifecycleLabels[task.lifecycleClass] || task.lifecycleClass)}</dd></div><div><dt>当前实时状态</dt><dd>${status(task.liveState)}</dd></div><div><dt>能力边界</dt><dd>${esc(fmtValue(task.capabilityBoundary))}</dd></div><div><dt>触发方式</dt><dd>${esc((task.triggerModes || []).join(" · ") || "不适用")}</dd></div><div><dt>下次业务到期</dt><dd>${esc(fmtTime(task.nextDueAt))}</dd></div><div><dt>Windows 下次检查</dt><dd>${esc(fmtTime(task.schedulerNextTriggerAt))}</dd></div></dl>`;
  }

  function taskDetailMarkup(task) {
    const failure = task.failure || {};
    const basis = (task.stateBasis || []).map((row) => `<tr><td>${esc(row.kind)}</td><td class="c25-machine">${esc(fmtValue(row.value))}</td><td>${row.authoritative ? "权威依据" : "说明依据"}</td></tr>`).join("");
    const ioRows = [
      ["输入", (task.inputs || []).join(" · ")], ["输出", (task.outputs || []).join(" · ")],
      ["上游", (task.upstreamTaskIds || []).join(" · ")], ["下游", (task.downstreamTaskIds || []).join(" · ")],
      ["影响页面", (task.affectedPages || []).join(" · ")], ["来源", (task.sources || []).join(" · ")], ["链", (task.chains || []).join(" · ")],
    ].map(([label, value]) => `<tr><th>${esc(label)}</th><td>${esc(value || "不适用 / 当前数据未提供")}</td></tr>`).join("");
    return `<section class="c25-two-column"><article class="c25-panel"><div class="c25-panel-header"><div><h2>身份与能力边界</h2><p>生命周期和实时状态分开；机器名称可复制。</p></div>${status(task.liveState)}</div>${taskFacts(task)}</article><article class="c25-panel"><div class="c25-panel-header"><div><h2>真实进度</h2><p>未知总量不显示虚假百分比。</p></div></div>${progress(task)}</article></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>可执行控制</h2><p>高影响操作先预览、再确认，后端权威接受后才改变显示。</p></div></div>${controlMarkup(task)}</section><section class="c25-two-column"><article class="c25-panel"><div class="c25-panel-header"><div><h2>输入、输出与影响</h2><p>不同对象不因导航分组而共享写操作。</p></div></div><div class="c25-table-wrap"><table class="c25-table"><tbody>${ioRows}</tbody></table></div></article><article class="c25-panel ${failure.code ? "c25-warning" : ""}"><div class="c25-panel-header"><div><h2>失败与恢复</h2><p>来源失败只显示真实影响范围。</p></div></div><dl class="c25-facts"><div><dt>错误码</dt><dd class="c25-machine">${esc(fmtValue(failure.code, "没有失败"))}</dd></div><div><dt>摘要</dt><dd>${esc(fmtValue(failure.summary, "当前未记录失败"))}</dd></div><div><dt>影响对象</dt><dd>${esc(fmtValue(failure.affectedObjectCount, "不适用"))}</dd></div><div><dt>陈旧风险</dt><dd>${failure.staleRisk ? "有，不能代表当前状态" : "未发现"}</dd></div></dl></article></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>状态依据</h2><p>进程、锁、心跳、状态文件和 Windows 任务按权威顺序列出。</p></div><a href="${esc(task.auditUrl)}">查看审计</a></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>依据类型</th><th>读取值</th><th>责任</th></tr></thead><tbody>${basis || `<tr><td colspan="3">当前数据未提供</td></tr>`}</tbody></table></div></section>`;
  }

  function openDialog(title, body, primaryLabel, onPrimary) {
    let dialog = $("#c25Dialog");
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "c25Dialog";
      dialog.className = "c25-dialog";
      document.body.appendChild(dialog);
    }
    dialog.innerHTML = `<form method="dialog"><header><h2>${esc(title)}</h2></header><div class="c25-dialog-body">${body}</div><footer><button class="c25-button" value="cancel">取消</button><button class="c25-button" data-kind="primary" type="button" id="c25DialogPrimary">${esc(primaryLabel)}</button></footer></form>`;
    $("#c25DialogPrimary", dialog).addEventListener("click", onPrimary);
    dialog.showModal();
    return dialog;
  }

  function toast(message, failed = false) {
    $(".c25-toast")?.remove();
    const node = document.createElement("div");
    node.className = "c25-toast";
    node.dataset.state = failed ? "failed" : "success";
    node.setAttribute("role", failed ? "alert" : "status");
    node.textContent = message;
    document.body.appendChild(node);
    window.setTimeout(() => node.remove(), 8000);
  }

  function installControlHandlers(root = document) {
    $$('[data-c25-control]', root).forEach((button) => button.addEventListener("click", async () => {
      const zone = button.closest(".c25-controls") || root;
      let parameters = {};
      try { parameters = JSON.parse(button.dataset.fixedParameters || "{}"); } catch (_error) { parameters = {}; }
      $$('[data-control-parameter]', zone).forEach((field) => { parameters[field.dataset.controlParameter] = field.value; });
      const requestId = `c25-${crypto.randomUUID()}`;
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
      try {
        const preview = await requestJson("/control/preview", { method: "POST", body: JSON.stringify({ requestId, taskId: button.dataset.taskId, action: button.dataset.action, parameters }) });
        const item = preview.proposed?.[0] || {};
        const impact = preview.impactPreview?.[0] || {};
        const dialog = openDialog(actionLabels[button.dataset.action] || "确认管理操作", `<p>系统尚未执行。请核对对象、前值、后值、影响和恢复方式。</p><dl class="c25-facts"><div><dt>任务</dt><dd class="c25-machine">${esc(item.taskId)}</dd></div><div><dt>当前状态</dt><dd>${esc(fmtValue(impact.before))}</dd></div><div><dt>拟改变值</dt><dd>${esc(fmtValue(impact.afterRequested))}</dd></div><div><dt>受影响 assetId</dt><dd class="c25-machine">${esc(fmtValue(impact.affectedAssetIds, "无"))}</dd></div><div><dt>受影响任务</dt><dd>${esc(fmtValue(impact.affectedTaskIds, "无"))}</dd></div><div><dt>受影响快照</dt><dd>${esc(fmtValue(impact.affectedSnapshots, "无"))}</dd></div><div><dt>固定历史样本重放</dt><dd>${esc(fmtValue(impact.replayEvidence?.fixedHistorical, "不适用"))}</dd></div><div><dt>当前只读样本重放</dt><dd>${esc(fmtValue(impact.replayEvidence?.currentReadOnly, "不适用"))}</dd></div><div><dt>读写对象</dt><dd>${esc(fmtValue(impact.readWriteObjects))}</dd></div><div><dt>冲突任务</dt><dd>${esc(fmtValue(impact.conflictTaskIds, "无"))}</dd></div><div><dt>前台影响</dt><dd>${esc(fmtValue(impact.frontendImpact, "无"))}</dd></div><div><dt>恢复方式</dt><dd>${esc(fmtValue(impact.recovery))}</dd></div><div><dt>确认有效期</dt><dd>${esc(fmtTime(preview.expiresAt))}</dd></div></dl>`, actionLabels[button.dataset.action] || "确认执行", async () => {
          const primary = $("#c25DialogPrimary", dialog);
          primary.setAttribute("aria-busy", "true"); primary.disabled = true;
          try {
            const result = await requestJson("/control/execute", { method: "POST", body: JSON.stringify({ requestId, confirmationToken: preview.confirmationToken }) });
            dialog.close();
            toast(result.backendAccepted ? "后端已接受操作，页面将读取权威状态。" : "后端没有接受操作。", !result.backendAccepted);
            if (result.backendAccepted) window.setTimeout(() => location.reload(), 400);
          } catch (error) {
            primary.disabled = false; primary.removeAttribute("aria-busy");
            toast(`操作未执行：${error.message}。追溯编号：${preview.auditId || "未提供"}`, true);
          }
        });
      } catch (error) {
        button.dataset.result = "failed";
        toast(`影响预览失败：${error.message}。原状态保持不变。`, true);
      } finally {
        button.removeAttribute("aria-busy"); button.disabled = false;
      }
    }));
  }

  async function renderOverview() {
    const data = await requestOverviewJson();
    const counts = data.taskCountsByLiveState || {};
    const lifecycle = data.taskCountsByLifecycleClass || {};
    const scheduler = data.windowsScheduler || {};
    const summaries = Object.values(data.summaries || {}).filter(Boolean);
    const taskRows = summaries.map((task) => `<tr><td><a href="${task.taskId === "c22.screening" ? "new-token-update.html" : task.taskId === "c22.convexity_tracking" ? "update-center.html" : task.taskId.startsWith("candidate.") ? "candidate-production.html" : "maintenance-jobs.html"}">${esc(task.displayName)}</a><small class="c25-machine">${esc(task.taskId)}</small></td><td>${esc(lifecycleLabels[task.lifecycleClass] || task.lifecycleClass)}</td><td>${status(task.liveState)}</td><td>${esc(fmtTime(task.lastHeartbeatAt || task.lastFinishedAt))}</td><td>${esc(fmtTime(task.nextDueAt))}</td></tr>`).join("");
    const incidents = (data.recentIncidents || []).map((item) => `<tr><td class="c25-machine">${esc(item.taskId)}</td><td>${status(item.state)}</td><td>${esc(fmtValue(item.failure?.summary))}</td><td data-number>${esc(fmtValue(item.failure?.affectedObjectCount, "不适用"))}</td></tr>`).join("");
    const decisions = (data.decisionItems || []).map((item) => `<article class="c25-card ${item.severity === "blocking" ? "c25-blocker" : "c25-warning"}"><span>待决策</span><strong>${esc(item.kind)}</strong><small>${esc(item.message)}</small></article>`).join("");
    setMain(`${pageHeader("管理者总览", "十秒内回答：系统现在怎样、哪里异常、下一次什么时候、你能安全做什么。", data.latestBusinessSnapshot, data.pageReadAt)}<section class="c25-summary-grid"><article class="c25-card"><span>自动启用</span><strong>${lifecycle.scheduled_active || 0}</strong><a href="task-ledger.html">查看全部任务</a></article><article class="c25-card"><span>正在运行 / 暂停请求</span><strong>${(counts.running || 0) + (counts.pause_requested || 0)}</strong><a href="task-ledger.html?state=running">查看状态依据</a></article><article class="c25-card ${((counts.failed || 0)+(counts.stale || 0)+(counts.blocked || 0)) ? "c25-blocker" : ""}"><span>失败 / 陈旧 / 阻断</span><strong>${(counts.failed || 0)+(counts.stale || 0)+(counts.blocked || 0)}</strong><a href="task-ledger.html?risk=attention">检查影响</a></article><article class="c25-card"><span>旧版兼容入口</span><strong>${lifecycle.legacy_callable || 0}</strong><a href="legacy-jobs.html">查看但不直接控制</a></article></section><section class="c25-two-column"><article class="c25-panel"><div class="c25-panel-header"><div><h2>唯一 Windows 调度器</h2><p>Windows 下一次检查与具体作业下一次到期分开显示。</p></div>${status(scheduler.liveState)}</div>${scheduler.taskId ? taskFacts(scheduler) : `<div class="c25-empty">尚未独立观察到 Windows 调度器；这不等于任务已停用。</div>`}</article><article class="c25-panel"><div class="c25-panel-header"><div><h2>时间与规则</h2><p>状态时间、快照时间和页面读取时间互不替代。</p></div></div><dl class="c25-facts"><div><dt>最近完整成功</dt><dd>${esc(fmtTime(data.latestCompleteSuccess))}</dd></div><div><dt>最新业务快照</dt><dd>${esc(fmtTime(data.latestBusinessSnapshot))}</dd></div><div><dt>当前规则版本</dt><dd class="c25-machine">${esc(fmtValue(data.currentRuleVersion))}</dd></div><div><dt>活动覆盖</dt><dd>${data.activeOverrideCount || 0} 个</dd></div><div><dt>页面读取</dt><dd>${esc(fmtTime(data.pageReadAt))}</dd></div></dl></article></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>现役任务与维护</h2><p>摘要不替代独立详情；失败和陈旧优先显示。</p></div><a href="task-ledger.html">打开完整账本</a></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>任务</th><th>分类</th><th>状态</th><th>最近依据时间</th><th>下次业务到期</th></tr></thead><tbody>${taskRows || `<tr><td colspan="5">当前数据未提供</td></tr>`}</tbody></table></div></section><section class="c25-two-column"><article class="c25-panel"><div class="c25-panel-header"><div><h2>六链与来源健康</h2><p>颜色不是唯一线索，真实零和失败分开。</p></div><a href="chain-source-health.html">查看精确矩阵</a></div><dl class="c25-facts">${Object.entries(data.chainSourceSummary || {}).map(([key,value]) => `<div><dt>${esc(stateLabels[key] || key)}</dt><dd>${esc(value)}</dd></div>`).join("") || `<div><dt>状态</dt><dd>当前数据未提供</dd></div>`}</dl></article><article class="c25-panel"><div class="c25-panel-header"><div><h2>异常与待决策</h2><p>低优先级成功不会抵消核心阻断。</p></div></div>${decisions || `<div class="c25-empty">当前没有需要管理者处理的规则或登记阻断。</div>`}</article></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>最近异常</h2><p>一个来源失败只展示其真实影响对象。</p></div><a href="run-audit.html">查看运行与审计</a></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>任务</th><th>状态</th><th>原因</th><th data-number>影响对象</th></tr></thead><tbody>${incidents || `<tr><td colspan="4">真实为零：当前组合状态没有失败、陈旧、阻断或部分完成项。</td></tr>`}</tbody></table></div></section>`);
  }

  function ledgerRow(task) {
    return `<tr data-lifecycle="${esc(task.lifecycleClass)}" data-state="${esc(task.liveState)}" data-entry="${esc(task.entryKind)}"><td><a href="task-detail.html?taskId=${encodeURIComponent(task.taskId)}">${esc(task.displayName)}</a><small class="c25-machine">${esc(task.taskId)}</small></td><td>${esc(lifecycleLabels[task.lifecycleClass] || task.lifecycleClass)}</td><td>${status(task.liveState)}</td><td>${esc(fmtTime(task.lastHeartbeatAt || task.lastFinishedAt))}</td><td>${esc(fmtTime(task.nextDueAt))}</td><td>${esc((task.affectedPages || []).join(" · ") || "不适用")}</td><td>${(task.controls || []).length ? `<a href="task-detail.html?taskId=${encodeURIComponent(task.taskId)}">查看控制</a>` : "只读详情"}</td></tr>`;
  }

  async function renderLedger() {
    const data = await requestJson("/tasks");
    const r = data.reconciliation || {};
    setMain(`${pageHeader("全部任务账本", "默认显示现役、失败、停用、旧版和按需入口；遗漏或重复为红色阻断。", null, data.observedAt)}<section class="c25-summary-grid"><article class="c25-card"><span>现场发现 POST 入口</span><strong>${r.observedInheritedPostEndpointCount ?? "--"}</strong><small>另有 ${(r.managedControlPostEndpoints || []).length} 个受保护管理接口</small></article><article class="c25-card"><span>已登记顶级入口</span><strong>${r.registeredTopLevelEntryCount ?? "--"}</strong><small>按需子任务 ${r.registeredUpdateTaskCatalogCount ?? "--"} 个</small></article><article class="c25-card ${(r.missingPostEndpoints || []).length ? "c25-blocker" : ""}"><span>遗漏</span><strong>${(r.missingPostEndpoints || []).length}</strong><small>${esc((r.missingPostEndpoints || []).join(" · ") || "零遗漏")}</small></article><article class="c25-card ${(r.duplicateTaskIds || []).length ? "c25-blocker" : ""}"><span>重复登记</span><strong>${(r.duplicateTaskIds || []).length}</strong><small>${esc((r.duplicateTaskIds || []).join(" · ") || "零重复")}</small></article></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>完整任务集合</h2><p>生命周期和当前实时状态使用独立列。</p></div><span>${data.tasks.length} 个可见对象</span></div><div class="c25-toolbar"><label class="c25-field"><span>分类</span><select id="c25LifecycleFilter"><option value="all">全部分类</option>${data.filters.lifecycleClasses.map((value) => `<option value="${esc(value)}">${esc(lifecycleLabels[value] || value)}</option>`).join("")}</select></label><label class="c25-field"><span>实时状态</span><select id="c25StateFilter"><option value="all">全部状态</option>${data.filters.liveStates.map((value) => `<option value="${esc(value)}">${esc(stateLabels[value] || value)}</option>`).join("")}</select></label><label class="c25-field"><span>入口类型</span><select id="c25EntryFilter"><option value="all">全部入口</option>${data.filters.entryKinds.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join("")}</select></label><label class="c25-field"><span>搜索</span><input id="c25TaskSearch" type="search" placeholder="名称、taskId、机器名"></label><span id="c25TaskVisibleCount"></span></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>任务</th><th>分类</th><th>当前状态</th><th>心跳 / 结束</th><th>下次业务运行</th><th>影响</th><th>操作</th></tr></thead><tbody id="c25TaskRows">${data.tasks.map(ledgerRow).join("")}</tbody></table></div></section>`);
    const apply = () => {
      const lifecycle = $("#c25LifecycleFilter").value, stateValue = $("#c25StateFilter").value, entry = $("#c25EntryFilter").value, query = $("#c25TaskSearch").value.trim().toLowerCase();
      let visible = 0;
      $$("#c25TaskRows tr").forEach((row) => { const match = (lifecycle === "all" || row.dataset.lifecycle === lifecycle) && (stateValue === "all" || row.dataset.state === stateValue) && (entry === "all" || row.dataset.entry === entry) && (!query || row.textContent.toLowerCase().includes(query)); row.hidden = !match; if (match) visible += 1; });
      $("#c25TaskVisibleCount").textContent = `显示 ${visible} / ${data.tasks.length}`;
    };
    ["#c25LifecycleFilter", "#c25StateFilter", "#c25EntryFilter"].forEach((selector) => $(selector).addEventListener("change", apply));
    $("#c25TaskSearch").addEventListener("input", apply); apply();
  }

  async function renderTaskPage(title, intro, taskIds) {
    const results = await Promise.all(taskIds.map((taskId) => requestJson(`/task?taskId=${encodeURIComponent(taskId)}`).catch((error) => ({ status: "failed", taskId, error }))));
    const tasks = results.filter((row) => row.status === "ready").map((row) => row.task);
    const failures = results.filter((row) => row.status !== "ready");
    const body = tasks.map((task) => `<section aria-labelledby="title-${esc(task.taskId)}">${pageHeader(task.displayName, intro, task.lastFinishedAt, task.observedAt, title)}${taskDetailMarkup(task)}</section>`).join("");
    setMain(`${body || pageHeader(title, intro, null, new Date().toISOString())}${failures.map((row) => `<section class="c25-error"><strong>${esc(row.taskId)}</strong><p>${esc(row.error?.message || "当前无法读取该任务。")}</p></section>`).join("")}`);
    installControlHandlers();
  }

  async function renderGenericTask() {
    const taskId = new URLSearchParams(location.search).get("taskId") || "";
    await renderTaskPage("通用任务详情", "稳定taskId深链只承载单任务事实，不替代六类独立页面。", [taskId]);
  }

  async function renderOnDemand() {
    const data = await requestJson("/tasks");
    const catalog = data.tasks.filter((task) => task.entryKind === "update_task_catalog_child");
    const other = data.tasks.filter((task) => task.taskId.startsWith("legacy.") && task.taskId !== "legacy.update_task_catalog");
    const rows = [...catalog, ...other];
    setMain(`${pageHeader("按需工具", "21个TASK_DEFINITIONS逐项可见；未完成锁、检查点、影响和回滚合同的入口只显示可执行事实，不新增直接按钮。", null, data.observedAt)}<section class="c25-summary-grid"><article class="c25-card"><span>TASK_DEFINITIONS</span><strong>${catalog.length}</strong><small>精确ID集合</small></article><article class="c25-card"><span>其他手动入口</span><strong>${other.length}</strong><small>旧生产POST映射</small></article><article class="c25-card"><span>新增直接控制</span><strong>0</strong><small>安全合同未完成</small></article><article class="c25-card"><span>现役包装组件</span><strong>${catalog.filter((task) => task.lifecycleClass === "active_component").length}</strong><small>不能绕过现役编排器</small></article></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>按需任务与手动入口</h2><p>每项均可进入稳定taskId详情；只读可见不等于C2.5授权直接运行。</p></div></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>任务</th><th>分类</th><th>当前事实</th><th>控制边界</th><th>详情</th></tr></thead><tbody>${rows.map((task) => `<tr><td><strong>${esc(task.displayName)}</strong><small class="c25-machine">${esc(task.taskId)}</small></td><td>${esc(lifecycleLabels[task.lifecycleClass] || task.lifecycleClass)}</td><td>${status(task.liveState)}</td><td>${esc(task.capabilityBoundary)}</td><td><a href="task-detail.html?taskId=${encodeURIComponent(task.taskId)}">查看入口、读写对象与依据</a></td></tr>`).join("")}</tbody></table></div></section>`);
  }

  async function renderChainSources() {
    const data = await requestJson("/chains-sources");
    const rows = data.rows || [];
    setMain(`${pageHeader("逐链与来源", "链和来源分别展示真实适用范围、时间、处理量、失败影响与重试边界。", data.dataAsOf, data.observedAt)}<section class="c25-panel"><div class="c25-panel-header"><div><h2>六链健康矩阵</h2><p>口径：完整快照中的候选、跟踪与公开对象；真实零不写成失败。</p></div><small>数据时间 ${esc(fmtTime(data.dataAsOf))}</small></div><div class="c25-toolbar"><label class="c25-field"><span>链</span><select id="c25ChainFilter"><option value="all">全部链与全局来源</option>${data.chainOrder.map((id) => `<option value="${esc(id)}">${esc(data.chainLabels[id])}</option>`).join("")}</select></label><label class="c25-field"><span>状态</span><select id="c25SourceStateFilter"><option value="all">全部状态</option>${Object.keys(data.summary || {}).map((value) => `<option value="${esc(value)}">${esc(stateLabels[value] || value)}</option>`).join("")}</select></label><span id="c25SourceVisible"></span></div><div class="c25-table-wrap" id="sourceExactTable"><table class="c25-table"><thead><tr><th>链 / 来源</th><th>状态</th><th>最近尝试 / 成功</th><th>游标 / 检查点</th><th data-number>发现 / 接受 / 失败</th><th>影响与重试</th></tr></thead><tbody id="c25SourceRows">${rows.map((row) => `<tr data-chain="${esc(row.chainId)}" data-state="${esc(row.status)}"><td><strong>${esc(row.chainLabel)}</strong><small class="c25-machine">${esc(row.sourceId)}</small></td><td>${status(row.status)}<small>${row.applicable ? "适用" : "不适用"}</small></td><td>${esc(fmtTime(row.lastAttemptAt))}<small>成功：${esc(fmtTime(row.lastSuccessAt))}</small></td><td class="c25-machine">${esc(fmtValue(row.cursor, "无游标"))}<small>${esc(fmtValue(row.checkpoint, "无检查点"))}</small></td><td data-number>${esc(fmtValue(row.discovered, "不适用"))} / ${esc(fmtValue(row.accepted, "不适用"))} / ${esc(fmtValue(row.failed, "不适用"))}</td><td>${esc(fmtValue(row.plainReason))}<small>影响 ${esc(fmtValue(row.affectedObjects, "不适用"))} · ${esc(row.retryCapability)}</small></td></tr>`).join("")}</tbody></table></div></section>`);
    const apply = () => { const chain = $("#c25ChainFilter").value, stateValue = $("#c25SourceStateFilter").value; let count = 0; $$("#c25SourceRows tr").forEach((row) => { const match = (chain === "all" || row.dataset.chain === chain) && (stateValue === "all" || row.dataset.state === stateValue); row.hidden = !match; if (match) count += 1; }); $("#c25SourceVisible").textContent = `显示 ${count} / ${rows.length}`; };
    $("#c25ChainFilter").addEventListener("change", apply); $("#c25SourceStateFilter").addEventListener("change", apply); apply();
  }

  async function renderRules() {
    showLoading("规则透明中心", "冻结基线、当前有效规则、活动覆盖和历史版本分开读取。", "正在读取规则、版本与逐资产影响；加载期间不显示旧版通用结论。");
    const data = await requestJson("/rules");
    const replay = data.replay || {};
    const replaySets = data.replaySets || {};
    const governance = data.governance || { knownVersions: [], drafts: [], history: [] };
    const impactCalculation = replaySets.impactCalculation || replay.impactCalculation || {};
    const approvalBlocked = data.governanceApprovalBlocked === true || impactCalculation.approvalBlocked === true;
    const max = Math.max(1, replay.baselinePassedCount || 0, replay.effectivePassedCount || 0);
    const versionOptions = (governance.knownVersions || []).filter((row) => row.version !== governance.activeVersion).map((row) => `<option value="${esc(row.version)}">${esc(row.label)} · ${esc(row.version)}</option>`).join("");
    const drafts = (governance.drafts || []).map((row) => `<tr><td class="c25-machine">${esc(row.draftId)}</td><td>${esc(row.sourceVersion)} → ${esc(row.targetVersion)}<small>${esc(row.difference)}</small></td><td>${esc(row.reason)}<small>${esc(row.scope)} · 结束条件：${esc(row.endCondition)}</small></td><td>${esc(row.status)}</td><td><div class="c25-controls">${row.status === "pending_approval" ? `<button class="c25-button" type="button" data-c25-control data-task-id="rule.governance" data-action="rule_approve_draft" data-fixed-parameters="${esc(JSON.stringify({ draftId: row.draftId }))}">批准</button><label class="c25-field"><span>拒绝原因</span><input data-control-parameter="reason" placeholder="必填"></label><button class="c25-button" type="button" data-c25-control data-task-id="rule.governance" data-action="rule_reject_draft" data-fixed-parameters="${esc(JSON.stringify({ draftId: row.draftId }))}">拒绝</button>` : "已保留审批证据"}</div></td></tr>`).join("");
    const versionRows = (governance.knownVersions || []).map((row) => `<tr><td><strong>${esc(row.label)}</strong><small class="c25-machine">${esc(row.version)}</small></td><td>${row.version === governance.activeVersion ? "当前有效" : "可明确回滚"}</td><td>${esc(row.changes)}</td><td><div class="c25-controls">${row.version === governance.activeVersion ? "下一次合法运行读取此版本" : `<label class="c25-field"><span>回滚原因</span><input data-control-parameter="reason" placeholder="必填"></label><button class="c25-button" type="button" data-c25-control data-task-id="rule.governance" data-action="rule_rollback_version" data-fixed-parameters="${esc(JSON.stringify({ targetVersion: row.version }))}">回滚到此版本</button>`}</div></td></tr>`).join("");
    const activationRows = (governance.history || []).slice().reverse().map((row) => `<tr><td>${esc(fmtTime(row.effectiveAt))}<small class="c25-machine">${esc(row.activationId)}</small></td><td>${esc(row.kind)}</td><td>${esc(row.previousVersion)} → ${esc(row.activeVersion)}</td><td>${row.historicalEvidencePreserved ? "历史与原始证据已保留" : "证据状态未确认"}<small>${row.linkedRunId ? `关联运行：${esc(row.linkedRunId)} · 快照：${esc(fmtValue(row.linkedSnapshotIds))}` : "等待下一次合法运行关联"}</small></td></tr>`).join("");
    setMain(`${pageHeader("规则透明中心", "冻结 JSON 不在页面直接改写；版本草案、影响预览、管理者审批和明确版本回滚通过受保护流程执行。", null, data.observedAt)}<section class="c25-summary-grid"><article class="c25-card"><span>冻结基线</span><strong class="c25-machine">${esc(data.frozenBaseline?.ruleVersion)}</strong><small>哈希${data.frozenBaseline?.hashMatchesFrozen ? "匹配" : "不匹配"}</small></article><article class="c25-card"><span>当前有效规则</span><strong class="c25-machine">${esc(data.effective?.ruleVersion)}</strong><small>逐字段对账 ${esc(fmtValue(data.effective?.reconciledRuleCount, 0))} / ${esc(fmtValue(data.effective?.expectedRuleCount, 0))}</small></article><article class="c25-card"><span>活动覆盖</span><strong>${data.activeOverride?.active ? "正在生效" : "未生效"}</strong><small>${esc(fmtTime(data.activeOverride?.approvedAt))}</small></article><article class="c25-card"><span>当前只读样本变化</span><strong>${(replay.affectedAssetIds || []).length}</strong><small>全部受治理规则并集</small></article></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>建立规则版本草案</h2><p>只能选择冻结范围内已登记版本；草案不立即生效，批准或回滚后由下一次合法运行读取。</p></div></div><div class="c25-controls"><label class="c25-field"><span>目标版本</span><select data-control-parameter="targetVersion">${versionOptions || `<option value="">当前没有其他可选版本</option>`}</select></label><label class="c25-field"><span>变更原因</span><input data-control-parameter="reason" placeholder="必填"></label><label class="c25-field"><span>适用范围</span><input data-control-parameter="scope" value="全部现役规则消费端"></label><label class="c25-field"><span>结束条件</span><input data-control-parameter="endCondition" placeholder="必填"></label><button class="c25-button" data-kind="primary" type="button" data-c25-control data-task-id="rule.governance" data-action="rule_create_draft" ${versionOptions ? "" : "disabled"}>预览并建立草案</button></div></section><section class="c25-two-column"><article class="c25-panel"><div class="c25-panel-header"><div><h2>固定历史样本重放</h2><p>版本控制样本，不被当前快照替代。</p></div></div><pre class="c25-machine">${jsonText(replaySets.fixedHistorical)}</pre></article><article class="c25-panel"><div class="c25-panel-header"><div><h2>当前只读样本重放</h2><p>只读使用当前完整跟踪快照。</p></div></div><pre class="c25-machine">${jsonText(replaySets.currentReadOnly)}</pre></article></section><section class="c25-chart" aria-labelledby="ruleImpactTitle"><div class="c25-chart-head"><div><h3 id="ruleImpactTitle">基线与有效规则通过数量</h3><p>口径：当前只读样本使用同一输入，分别重放冻结基线和当前有效规则。</p></div><div><small>数据时间：${esc(fmtTime(data.observedAt))}</small><br><a href="#ruleExactTable">查看精确表格</a></div></div><div class="c25-bar"><span>冻结基线</span><i style="--c25-bar:${(replay.baselinePassedCount || 0) * 100 / max}%"></i><strong>${replay.baselinePassedCount || 0}</strong></div><div class="c25-bar"><span>当前有效</span><i style="--c25-bar:${(replay.effectivePassedCount || 0) * 100 / max}%"></i><strong>${replay.effectivePassedCount || 0}</strong></div><small>筛选：当前完整输入 · 空值不补零 · 综合分不控制资格或凸性线索 · 每条规则独立计算影响。</small></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>逐规则对账与影响</h2><p>每行使用本规则自己的适用输入、前后结果、反例和代码字段，不复制全局重放结果。</p></div><a href="decision-trace.html">按assetId解释</a></div><div class="c25-table-wrap" id="ruleExactTable"><table class="c25-table"><thead><tr><th>规则</th><th>冻结基线</th><th>当前有效 / 代码</th><th>独立影响数量</th><th>真实样本</th></tr></thead><tbody>${(data.rules || []).map((row) => `<tr><td><strong>${esc(row.plainName)}</strong><small class="c25-machine">${esc(row.ruleId)}</small><small>${esc(row.scope)} · ${esc(row.difference)}</small><small class="c25-machine">${esc(row.baselineVersion)} → ${esc(row.effectiveVersion)}</small></td><td>${esc(ruleValue(row.baselineValue, row.unit))}</td><td>${esc(ruleValue(row.effectiveValue, row.unit))}<small>${row.codeReconciliation?.matched ? "代码逐字段一致" : `不一致：代码=${esc(fmtValue(row.codeReconciliation?.codeEffectiveValue))}`}</small><small>${esc(row.effectiveSourcePath)}</small></td><td>${esc(ruleCounts(row.counts))}<small>新增：${esc(assetSetSummary(row.addedAssetIds))} · 移除：${esc(assetSetSummary(row.removedAssetIds))}</small><small>状态变化：${esc(assetSetSummary(row.stateChangedAssetIds))}</small></td><td>通过：${esc(ruleSample(row.passExample))}<small>未通过：${esc(ruleSample(row.nonPassExample))}</small></td></tr>`).join("")}</tbody></table></div></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>待审批草案</h2><p>批准与拒绝都经影响预览、确认令牌和管理审计。</p></div></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>草案</th><th>版本差异</th><th>原因 / 范围</th><th>状态</th><th>审批</th></tr></thead><tbody>${drafts || `<tr><td colspan="5">真实为零：当前没有规则草案。</td></tr>`}</tbody></table></div></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>明确版本回滚</h2><p>回滚不改写旧快照、原始证据或旧版本记录；会生成新的版本记录。</p></div></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>版本</th><th>状态</th><th>差异</th><th>操作</th></tr></thead><tbody>${versionRows}</tbody></table></div></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>审批与回滚历史证据</h2><p>${esc(data.bayesBoundary)}</p></div></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>选择时间 / 记录</th><th>类型</th><th>版本变化</th><th>运行与证据</th></tr></thead><tbody>${activationRows || `<tr><td colspan="4">当前没有C2.5规则版本变更；C2.4活动试行仍按继承记录生效。</td></tr>`}</tbody></table></div></section>`);
    if (approvalBlocked) {
      const header = $(".c25-page-header");
      header?.insertAdjacentHTML("afterend", `<section class="c25-panel c25-blocker" data-c25-rule-impact-blocker><h2>影响无法完整计算</h2><p>${esc(data.governanceBlockReason || impactCalculation.reason || "当前快照不足以完成真实执行器逐资产对账。")}</p><small>真实执行器不一致：${esc(fmtValue((impactCalculation.executorMismatchAssetIds || []).length, 0))} 个 assetId · 执行证据缺失：${esc(fmtValue((impactCalculation.executorMissingEvidenceAssetIds || []).length, 0))} 个 assetId。规则草案批准与版本回滚已阻断；拒绝草案仍可执行。</small></section>`);
      $$('[data-action="rule_create_draft"], [data-action="rule_approve_draft"], [data-action="rule_rollback_version"]').forEach((button) => {
        button.disabled = true;
        button.title = "影响无法完整计算，已阻断规则批准与回滚";
      });
    }
    installControlHandlers();
  }

  function traceMarkup(data) {
    if (data.status === "not_found") return `<div class="c25-empty">真实为零：当前业务快照没有找到这个 assetId。无需手动补造项目。</div>`;
    return `<section class="c25-flow">${data.path.map((step, index) => `<article><span class="c25-eyebrow">步骤 ${index + 1}</span><h3>${esc(step)}</h3><p>${index === 0 ? esc(fmtValue(data.evidence)) : index === 1 ? esc(fmtValue(data.dataTimes)) : index === 2 ? esc(fmtValue(data.ruleResults)) : index === 3 ? esc(fmtValue(data.waitOrFailureReasons)) : index === 4 ? esc(fmtValue(data.businessState)) : index === 5 ? esc(fmtValue(data.snapshotRefs)) : data.frontendVisibility ? "当前进入普通用户前台" : "当前不进入普通用户前台"}</p></article>`).join("")}</section><section class="c25-two-column"><article class="c25-panel"><div class="c25-panel-header"><div><h2>身份、T0与状态</h2></div></div><pre class="c25-machine">${jsonText({ identity: data.identity, t0: data.t0, businessState: data.businessState })}</pre></article><article class="c25-panel"><div class="c25-panel-header"><div><h2>排序字段</h2><p>贝叶斯只解释排序和变化。</p></div></div><pre class="c25-machine">${jsonText(data.ranking)}</pre></article></section>`;
  }

  async function renderDecisionTrace() {
    const initial = new URLSearchParams(location.search).get("assetId") || "";
    setMain(`${pageHeader("项目判定解释", "按稳定assetId追溯输入证据、数据时间、规则、结果、快照和前台显示。", null, new Date().toISOString())}<section class="c25-panel"><form id="c25TraceForm" class="c25-toolbar"><label class="c25-field"><span>稳定 assetId</span><input id="c25TraceAsset" value="${esc(initial)}" required placeholder="输入完整 assetId"></label><button class="c25-button" data-kind="primary" type="submit">追溯项目判定</button></form></section><div id="c25TraceResult">${initial ? `<div class="c25-empty">正在读取判定链。</div>` : `<div class="c25-empty">尚未选择项目。输入assetId后只读取现有证据，不触发更新。</div>`}</div>`);
    const load = async () => { const assetId = $("#c25TraceAsset").value.trim(); if (!assetId) return; const result = $("#c25TraceResult"); result.innerHTML = `<div class="c25-empty">正在读取判定链。</div>`; try { const data = await requestJson(`/decision-trace?assetId=${encodeURIComponent(assetId)}`); result.innerHTML = traceMarkup(data); history.replaceState(null, "", `decision-trace.html?assetId=${encodeURIComponent(assetId)}`); } catch (error) { result.innerHTML = `<div class="c25-error">发生了什么：${esc(error.message)}。影响：当前无法证明项目判定。下一步：核对assetId或快照状态。</div>`; } };
    $("#c25TraceForm").addEventListener("submit", (event) => { event.preventDefault(); load(); }); if (initial) await load();
  }

  async function renderSnapshots() {
    showLoading("数据快照与交接", "生产者、消费者、对象数、数据时间和校验逐项读取。", "正在读取快照交接；加载期间保留当前页面责任，不显示旧版通用结论。");
    const data = await requestJson("/snapshots");
    const flow = data.snapshots.map((row) => `<article data-complete="${row.complete}"><span class="c25-eyebrow">${esc(row.producerTaskId)}</span><h3>${esc(row.snapshotId)}</h3><p>${row.complete ? "完整原子快照" : "交接失败，保留上一份完整快照"}</p><p>${esc(fmtTime(row.dataAsOf))} · ${esc(fmtValue(row.objectCount, "对象数未知"))}</p></article>`).join("");
    setMain(`${pageHeader("数据快照与交接", "生产者、消费者、对象数、数据时间和校验逐项显示；失败不覆盖上一份完整快照。", Math.max(...data.snapshots.map((row) => new Date(row.dataAsOf || 0).getTime())) ? new Date(Math.max(...data.snapshots.map((row) => new Date(row.dataAsOf || 0).getTime()))).toISOString() : null, data.observedAt)}<section class="c25-panel"><div class="c25-panel-header"><div><h2>现役交接路径</h2><p>候选生产 → 筛选 → 跟踪 → 公开快照 → 前台；阻断节点不会显示为完成。</p></div></div><div class="c25-flow">${flow}</div></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>精确快照表</h2><p>生命周期状态与凸性跟踪状态分列，不合并。</p></div></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>快照 / 生产者</th><th>完整 / 陈旧</th><th data-number>对象数</th><th>数据时间</th><th>校验</th><th>消费者</th></tr></thead><tbody>${data.snapshots.map((row) => `<tr><td><strong>${esc(row.snapshotId)}</strong><small class="c25-machine">${esc(row.producerTaskId)} · ${esc(row.path)}</small></td><td>${status(row.complete ? row.stale ? "stale" : "completed" : "failed")}</td><td data-number>${esc(fmtValue(row.objectCount))}</td><td>${esc(fmtTime(row.dataAsOf))}</td><td class="c25-machine">${esc(fmtValue(row.validation))}</td><td>${esc((row.consumerPages || []).join(" · ") || "不适用")}</td></tr>`).join("")}</tbody></table></div></section><section class="c25-panel"><div class="c25-panel-header"><div><h2>两库只读完整性</h2><p>管理组合读取不写业务数据库。</p></div></div><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>数据库</th><th>可用</th><th>quick_check</th><th>外键异常</th><th>模式</th></tr></thead><tbody>${data.databases.map((row) => `<tr><td class="c25-machine">${esc(row.path)}</td><td>${row.available ? "是" : "当前工作区未提供"}</td><td>${esc(fmtValue(row.quickCheck))}</td><td>${esc(fmtValue(row.foreignKeyViolations))}</td><td>${row.readOnly ? "只读" : "未知"}</td></tr>`).join("")}</tbody></table></div></section>`);
  }

  async function renderAudit() {
    const data = await requestJson("/runs-audit");
    const runRows = data.runs.map((row) => `<tr><td class="c25-machine">${esc(row.runId)}</td><td>${esc(row.taskId)}${row.legacy ? `<small>历史 / ${esc(row.sourceVersion)}</small>` : ""}</td><td>${status(row.stale ? "stale" : row.finalState)}</td><td>${esc(fmtTime(row.startedAt))}<small>结束 ${esc(fmtTime(row.finishedAt))}</small></td><td>${esc(fmtValue(row.processed, "总量未知"))}</td><td>${esc(fmtValue(row.error?.detail, "无"))}</td></tr>`).join("");
    const auditRows = data.managementAudit.slice().reverse().map((row) => `<tr><td>${esc(fmtTime(row.requestedAt))}<small class="c25-machine">${esc(row.auditId)}</small></td><td>${esc(row.actor)}<small>${esc(row.origin)}</small></td><td>${esc(row.taskId)}<small>${esc(row.action)}</small></td><td>${esc(fmtValue(row.before))}</td><td>${esc(fmtValue(row.afterRequested))}</td><td>${row.backendAccepted ? "后端已接受" : esc(fmtValue(row.finalResult))}<small>${esc(fmtValue(row.linkedRunId, "无关联运行"))}</small></td></tr>`).join("");
    setMain(`${pageHeader("运行记录与管理审计", "运行记录回答系统做了什么；管理审计回答谁改变了什么，两者不互相冒充。", null, data.observedAt)}<section class="c25-panel"><div class="c25-toolbar" role="tablist" aria-label="记录类型"><button class="c25-button" data-audit-tab="runs" role="tab" aria-selected="true">运行记录</button><button class="c25-button" data-audit-tab="audit" role="tab" aria-selected="false">管理审计</button></div><div id="c25RunsPanel" role="tabpanel"><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>运行 ID</th><th>任务</th><th>终态</th><th>开始 / 结束</th><th>处理量</th><th>错误</th></tr></thead><tbody>${runRows || `<tr><td colspan="6">尚未运行：当前没有可读取的运行记录。</td></tr>`}</tbody></table></div></div><div id="c25AuditPanel" role="tabpanel" hidden><div class="c25-table-wrap"><table class="c25-table"><thead><tr><th>时间 / 审计 ID</th><th>操作者</th><th>对象 / 操作</th><th>前值</th><th>拟议值</th><th>结果</th></tr></thead><tbody>${auditRows || `<tr><td colspan="6">真实为零：当前没有管理操作审计。</td></tr>`}</tbody></table></div></div></section>`);
    $$('[data-audit-tab]').forEach((button) => button.addEventListener("click", () => { const audit = button.dataset.auditTab === "audit"; $("#c25RunsPanel").hidden = audit; $("#c25AuditPanel").hidden = !audit; $$('[data-audit-tab]').forEach((item) => item.setAttribute("aria-selected", String(item === button))); }));
  }

  function inheritedContext() {
    const main = $("main"); if (!main || $(".c25-inherited-context", main)) return;
    const note = document.createElement("section"); note.className = "c25-panel c25-inherited-context";
    note.innerHTML = `<div class="c25-panel-header"><div><span class="c25-eyebrow">继承页面</span><h2>当前页面保持原业务责任</h2><p>管理任务、调度、日志和安全控制只在管理者控制面独立页面出现；这里不新增跨对象写操作。</p></div><a href="task-ledger.html">查看全部任务</a></div>`;
    main.insertBefore(note, main.firstChild);
  }

  async function installTaskTray() {
    try {
      const data = await requestJson("/tasks");
      const active = data.tasks.filter((task) => ["running", "pause_requested", "failed", "stale", "blocked"].includes(task.liveState));
      if (!active.length) return;
      const tray = document.createElement("aside"); tray.className = "c25-task-tray"; tray.setAttribute("aria-live", "polite");
      tray.innerHTML = `<p><strong>${active.length} 个任务需要持续关注</strong><br><span>${esc(active.slice(0,3).map((task) => `${task.displayName}：${stateLabels[task.liveState] || task.liveState}`).join(" · "))}</span></p><a class="c25-button" href="task-ledger.html?risk=attention">查看真实状态</a>`;
      document.body.appendChild(tray);
    } catch (_error) { /* 页面本身已经负责显示读取失败，托盘静默不覆盖主错误。 */ }
  }

  const adapters = {
    "workbench.html": renderOverview,
    "task-ledger.html": renderLedger,
    "task-detail.html": renderGenericTask,
    "new-token-update.html": () => renderTaskPage("任务详情", "90天新币筛选只处理现役候选筛选，不替代凸性跟踪。", ["c22.screening"]),
    "update-center.html": () => renderTaskPage("任务详情", "凸性跟踪只处理现役跟踪和公开快照，不绕过筛选输入。", ["c22.convexity_tracking"]),
    "candidate-production.html": () => renderTaskPage("任务详情", "日常候选与已授权历史续跑分开；历史队列没有从头运行。", ["candidate.daily_incremental", "candidate.history_backlog"]),
    "maintenance-jobs.html": () => renderTaskPage("任务详情", "维护检查与服务启动校验独立表达，不影响现役业务调度。", ["maintenance.temp_artifact_retention", "service.startup_snapshot_validation"]),
    "legacy-jobs.html": () => renderTaskPage("任务详情", "旧C1.8、C2.1和Gate 0透明登记；C2.5不提供误导性运行或启用按钮。", ["c18.scheduler.legacy", "c21.pipeline.legacy", "gate0.backfill.disabled"]),
    "on-demand-tools.html": renderOnDemand,
    "chain-source-health.html": renderChainSources,
    "rule-transparency.html": renderRules,
    "decision-trace.html": renderDecisionTrace,
    "snapshot-handoffs.html": renderSnapshots,
    "run-audit.html": renderAudit,
  };

  const adapter = adapters[page];
  if (adapter) {
    Promise.resolve(adapter()).then(() => installTaskTray()).catch((error) => errorPage(document.title.split("｜")[0], error));
  } else {
    inheritedContext();
    installTaskTray();
  }
})();
