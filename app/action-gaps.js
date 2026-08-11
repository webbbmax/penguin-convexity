(function renderActionGaps() {
  const state = window.PENGUIN_CONVEXITY_OPPORTUNITY_CENTER || {};
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const dateTime = (value) => {
    if (!value) return "下次自动检查时间待生成";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
  };
  const details = state.c18?.blockerDetails || [];
  byId("actionGapSummary").textContent = details.length
    ? `当前按项目分组展示 ${details.length} 类首要缺口；每一类包含事实、门槛、影响和系统下一步。`
    : "当前没有需要展示的行动缺口。";
  byId("actionGapList").innerHTML = details.map((item) => `
    <article class="c18-gap-card">
      <header><div><span>${escapeHtml(item.name)}</span><h2>${escapeHtml(item.projectName ? `${item.projectName}${item.symbol ? ` · ${item.symbol}` : ""}` : item.statusLabel || "项目分组")}</h2></div><strong>${Number(item.count || 0).toLocaleString("zh-CN")} 个项目</strong></header>
      <dl>
        <div><dt>当前事实</dt><dd>${escapeHtml(item.fact)}</dd></div>
        <div><dt>行动门槛</dt><dd>${escapeHtml(item.threshold)}</dd></div>
        <div><dt>为什么影响行动</dt><dd>${escapeHtml(item.impact)}</dd></div>
        <div><dt>负责人</dt><dd>${escapeHtml(item.owner)}</dd></div>
        <div><dt>下一步</dt><dd>${escapeHtml(item.nextStep)}</dd></div>
        <div><dt>下次检查</dt><dd>${escapeHtml(dateTime(item.nextReviewAt))}</dd></div>
      </dl>
      <a href="${escapeHtml(item.evidenceUrl || "candidate-pool.html?view=library")}">查看相关证据与项目</a>
    </article>`).join("") || '<div class="opportunity-empty">当前没有行动缺口。</div>';
}());
