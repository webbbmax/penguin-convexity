(function renderDataBackbone() {
  const state = window.PENGUIN_CONVEXITY_DATA_BACKBONE;
  const byId = (id) => document.getElementById(id);
  const number = (value) => new Intl.NumberFormat("zh-CN").format(Number(value || 0));
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const dateTime = (value) => value
    ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value))
    : "尚无";

  if (!state) {
    byId("backboneVerdict").textContent = "尚无数据主干快照";
    byId("backboneVerdictNote").textContent = "请先在更新中心运行“最大漏斗数据主干”。";
    return;
  }

  const schema = state.eventSchema || {};
  const continuity = state.continuity || {};
  const orphan = state.orphanEvidence || {};
  const graph = state.entityGraph || {};
  const complete = schema.rawEvents === schema.normalizedEvents && continuity.backlog === 0;
  byId("backboneVerdict").textContent = complete
    ? (continuity.openGaps ? `主干完整，${number(continuity.openGaps)} 个来源断档待处理` : "主干连续，当前没有积压")
    : "标准化覆盖尚未完成";
  byId("backboneVerdictNote").textContent = complete
    ? `已把 ${number(schema.rawEvents)} 条原始事件全部纳入 Event Schema v2；来源断档单独保留，不伪装成零结果。`
    : `仍有 ${number(Math.max(0, (schema.rawEvents || 0) - (schema.normalizedEvents || 0)))} 条原始事件待进入标准层。`;
  byId("backboneRawEvents").textContent = number(schema.rawEvents);
  byId("backboneNormalizedEvents").textContent = number(schema.normalizedEvents);
  byId("backboneOrphanEvents").textContent = number((orphan.pending || 0) + (orphan.conflict || 0));
  byId("backboneOpenGaps").textContent = number(continuity.openGaps);
  byId("backboneWatchers").textContent = number(graph.watchers);
  byId("backboneTraceability").textContent = `${number(schema.traceableEvents)} / ${number(schema.normalizedEvents)}`;
  byId("backboneCursorSources").textContent = number(continuity.sources);
  byId("backboneBacklog").textContent = number(continuity.backlog);
  byId("backboneContinuityNote").textContent = complete
    ? "全部原始事件都已标准化并可回指原始位置；重复运行通过内容哈希和原始事件ID幂等去重。"
    : "仍有原始事件未进入标准事件层。";
  const replay = state.latestReplay;
  byId("backboneReplayStatus").textContent = replay ? ({
    incremental: "增量同步", replay: "历史重放", gap_recovery: "断档恢复",
  }[replay.mode] || replay.mode) : "尚无";
  byId("backboneReplayNote").textContent = replay
    ? `${dateTime(replay.finished_at)} · 新增 ${number(replay.inserted_count)} · 去重 ${number(replay.duplicate_count)}`
    : "等待运行记录";
  byId("backboneBoundary").textContent = state.boundary;

  const mainlineLabels = {
    git: ["Git", "代码活动"], release: ["Release", "软件发布"],
    package: ["Package", "包与依赖"], evm: ["EVM", "EVM 合约"],
    solana: ["Solana", "Solana 程序"],
  };
  byId("backboneMainlines").innerHTML = Object.entries(mainlineLabels).map(([key, labels]) => {
    const item = state.mainlines?.[key] || {};
    return `<article class="mainline-${key}">
      <span>${escapeHtml(labels[0])}</span><h3>${escapeHtml(labels[1])}</h3>
      <dl><div><dt>已登记 Watcher</dt><dd>${number(item.watchers)}</dd></div>
      <div><dt>可直接运行</dt><dd>${number(item.ready)}</dd></div>
      <div><dt>已采集事件</dt><dd>${number(item.events)}</dd></div></dl>
      <small>${item.events ? "已有真实事件进入主干" : "当前 0 条事件，保留零结果"}</small>
    </article>`;
  }).join("");

  const healthLabels = {
    healthy: "健康", true_zero: "真实零结果", silent: "采集静默",
    failed: "任务失败", quota_exhausted: "额度耗尽", rule_gap: "规则缺口",
    stale: "数据陈旧", unknown: "尚待判断",
  };
  byId("backboneHealthSummary").innerHTML = Object.entries(state.sourceHealth || {})
    .map(([key, value]) => `<span class="health-${escapeHtml(key)}"><b>${escapeHtml(healthLabels[key] || key)}</b><strong>${number(value)}</strong></span>`)
    .join("");
  byId("backboneHealthRows").innerHTML = (state.healthRows || []).map((row) => `
    <tr class="health-${escapeHtml(row.health_state)}">
      <td><strong>${escapeHtml(String(row.name || "").replace(/^C1\.7\s*/, ""))}</strong><small>${escapeHtml(row.source_id)}</small></td>
      <td><span>${escapeHtml(healthLabels[row.health_state] || row.health_state)}</span></td>
      <td>${escapeHtml(dateTime(row.last_event_at))}</td>
      <td>${escapeHtml(row.gap_status === "open" ? "开放" : row.gap_status === "resolved" ? "已恢复" : "无")}</td>
      <td>${escapeHtml(row.diagnosis)}</td>
    </tr>`).join("");

  const unresolved = (orphan.pending || 0) + (orphan.conflict || 0);
  byId("backboneOrphanNote").textContent = `当前 ${number(unresolved)} 条待归属，${number(orphan.resolved)} 条已在身份锚点补齐后重新归属；原始记录和归属历史均保留。`;
  const orphanRows = state.orphanRows || [];
  byId("backboneOrphanRows").innerHTML = orphanRows.length ? orphanRows.map((row) => `
    <tr>
      <td>${escapeHtml(dateTime(row.event_time))}</td>
      <td><strong>${escapeHtml(row.source_id)}</strong><small>${escapeHtml(row.event_type)}</small></td>
      <td>${escapeHtml(row.project_hint || "无")}</td>
      <td>${escapeHtml([row.asset_hint, row.chain_hint].filter(Boolean).join(" · ") || "无")}</td>
      <td>${escapeHtml(row.attribution_status === "conflict" ? "归属冲突" : "缺少唯一身份锚点")}</td>
      <td><code>${escapeHtml(row.raw_locator)}</code></td>
    </tr>`).join("") : '<tr><td colspan="6">当前没有待归属证据。</td></tr>';
})();
