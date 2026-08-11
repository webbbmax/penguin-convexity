(function convexityChangeExplanations() {
  const state = window.PENGUIN_CONVEXITY_CHANGE_EXPLANATIONS;
  const reviewApi = location.pathname.startsWith("/convexity/")
    ? "/api/convexity/tracking-decision-review"
    : "/api/tracking-decision-review";
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
  const number = (value) => Number(value || 0).toLocaleString("zh-CN");
  const dateTime = (value) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? String(value || "--")
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const statusLabels = {
    upgrade: "本轮上调",
    downgrade: "本轮下调",
    changed: "横向变化",
    stable: "分层未变",
    baseline: "比较基线",
  };
  const reviewStatusLabels = {
    pending: "待结论复核",
    confirmed: "已确认采用",
    rejected: "未采用，重新复查",
    not_required: "无需人工复核",
  };
  const pageSize = 20;
  let page = 0;

  function observedAt(item) {
    return item.latestHistory?.observed_at || item.latestChange?.observed_at || "";
  }

  function impactLevel(item) {
    if (item.decisionReview?.required || ["upgrade", "downgrade"].includes(item.currentStatus)) return "high";
    if (item.currentStatus === "changed") return "medium";
    return "low";
  }

  function actionImpact(item) {
    if (item.currentStatus === "upgrade") return "upgrade";
    if (item.currentStatus === "downgrade") return "downgrade";
    return "unchanged";
  }

  if (!state?.items) {
    byId("changeHeroTitle").textContent = "变化数据暂不可用";
    byId("changeProjectList").innerHTML = '<div class="change-empty">请从更新中心重新生成变化快照。</div>';
    return;
  }

  const items = [...state.items];
  const currentChanges = state.counts.upgrade + state.counts.downgrade + state.counts.changed;
  byId("changeHeroTitle").textContent = state.counts.decisionReviewPending
    ? `${number(state.counts.decisionReviewPending)} 个高影响结论待复核`
    : currentChanges
      ? `本轮记录 ${number(currentChanges)} 项有效变化`
    : state.counts.baseline === state.counts.total
      ? `已建立 ${number(state.counts.baseline)} 个比较基线`
      : "本轮没有达到阈值的变化";
  byId("changeHeroNote").textContent = state.counts.decisionReviewPending
    ? "只需处理上调与停止；普通新增证据、继续跟踪和行动后监测均自动完成。"
    : state.counts.baseline === state.counts.total
      ? "这是首轮比较起点，当前没有可据实认定的自动升降级。"
      : `上调 ${number(state.counts.upgrade)}，下调 ${number(state.counts.downgrade)}，横向变化 ${number(state.counts.changed)}。`;
  byId("changeTotalCount").textContent = number(state.counts.total);
  byId("changeUpgradeCount").textContent = number(state.counts.upgrade);
  byId("changeDowngradeCount").textContent = number(state.counts.downgrade);
  byId("changeChangedCount").textContent = number(state.counts.changed);
  byId("changeStableCount").textContent = number(state.counts.stable);
  byId("changeBaselineCount").textContent = number(state.counts.baseline);
  byId("changeReviewPendingCount").textContent = number(state.counts.decisionReviewPending);
  byId("changeGeneratedAt").textContent = `比较时间 ${dateTime(state.generatedAt)} · 历史记录 ${number(state.counts.history)} 条`;
  byId("changeBoundary").textContent = state.boundary;
  byId("changeThresholds").innerHTML = state.thresholds.map((item) => `
    <article>
      <span>${escapeHtml(item.field)}</span>
      <strong>${escapeHtml(item.rule)}</strong>
    </article>
  `).join("");

  function evidenceBlock(item) {
    if (!item.evidence?.length) {
      return '<p class="change-no-evidence">本轮没有可直接归因的新增采集证据；变化来自已写入字段或规则结果。</p>';
    }
    const title = item.currentStatus === "baseline"
      ? "最近相关信号（仅作基线上下文，不代表本轮触发）"
      : "本轮相关信号";
    return `
      <div class="change-evidence">
        <strong>${title}</strong>
        ${item.evidence.map((evidence) => `
          <article>
            <div><span>${escapeHtml(evidence.category)}</span><b>${escapeHtml(evidence.sourceName)}</b><time>${escapeHtml(dateTime(evidence.collectedAt))}</time></div>
            <p>${escapeHtml(evidence.summary)}</p>
            ${evidence.sourceUrl ? `<a href="${escapeHtml(evidence.sourceUrl)}" target="_blank" rel="noreferrer">查看原始来源</a>` : ""}
          </article>
        `).join("")}
      </div>`;
  }

  function projectCard(item) {
    const latest = item.latestHistory || {};
    const stageChange = latest.changedFields?.find((field) => field.field === "stage");
    const previousStage = latest.from_stage
      ? stageChange?.fromLabel || latest.from_stage
      : "首次基线";
    const categories = item.triggerCategories?.length
      ? item.triggerCategories
      : ["未达到变化阈值"];
    const review = item.decisionReview || {
      required: false,
      status: "not_required",
      statusLabel: "无需人工复核",
    };
    const reviewTrackingResult = item.reviewTrackingResult || item.latestTracking;
    const trackingResultId = reviewTrackingResult?.tracking_result_id || "";
    return `
      <article class="change-project-card status-${escapeHtml(item.currentStatus)}">
        <header>
          <div>
            <span>${escapeHtml(item.currentStageLabel)} · ${escapeHtml(item.maturity)}</span>
            <h3>${escapeHtml(item.projectName)}${item.symbol ? ` <small>${escapeHtml(item.symbol)}</small>` : ""}</h3>
          </div>
          <strong>${escapeHtml(statusLabels[item.currentStatus] || item.currentStatusLabel)}</strong>
        </header>
        <p class="change-explanation">${escapeHtml(item.currentExplanation)}</p>
        <div class="change-tags">
          ${categories.map((category) => `<span>${escapeHtml(category)}</span>`).join("")}
          <span>历史 ${number(item.historyCount)} 条</span>
          ${review.required ? `<span class="review-${escapeHtml(review.status)}">${escapeHtml(review.statusLabel)}</span>` : ""}
        </div>
        ${review.required ? `
          <section class="change-decision-review status-${escapeHtml(review.status)}">
            <header>
              <div><span>结论复核</span><strong>${escapeHtml(review.statusLabel)}</strong></div>
              <small>${review.reviewedAt ? escapeHtml(dateTime(review.reviewedAt)) : "仅高影响变化需要处理"}</small>
            </header>
            <p>${escapeHtml(reviewTrackingResult?.reason || item.currentExplanation)}</p>
            ${review.status === "pending" ? `
              <label>
                <span>复核备注</span>
                <textarea data-review-note="${escapeHtml(trackingResultId)}" rows="2" placeholder="确认可留空；不采纳时请写明原因"></textarea>
              </label>
              <div>
                <button type="button" data-review-result="${escapeHtml(trackingResultId)}" data-review-action="confirm">确认采用</button>
                <button class="is-reject" type="button" data-review-result="${escapeHtml(trackingResultId)}" data-review-action="reject">不采纳并重新复查</button>
              </div>
            ` : `
              <small>${review.note ? `备注：${escapeHtml(review.note)}` : "本次复核没有附加备注。"}${review.actor ? ` · ${escapeHtml(review.actor)}` : ""}</small>
            `}
          </section>
        ` : ""}
        <details>
          <summary>查看字段与信源依据</summary>
          <div class="change-detail-grid">
            <div>
              <span>比较关系</span>
              <strong>${escapeHtml(previousStage)} → ${escapeHtml(item.currentStageLabel)}</strong>
            </div>
            <div>
              <span>记录时间</span>
              <strong>${escapeHtml(dateTime(latest.observed_at))}</strong>
            </div>
          </div>
          ${latest.changedFields?.length ? `
            <div class="change-fields">
              ${latest.changedFields.map((field) => `
                <article>
                  <span>${escapeHtml(field.label)}</span>
                  <strong>${escapeHtml(field.fromLabel)} → ${escapeHtml(field.toLabel)}</strong>
                  ${field.deltaPct == null ? "" : `<small>${Number(field.deltaPct) > 0 ? "+" : ""}${escapeHtml(field.deltaPct)}%</small>`}
                </article>
              `).join("")}
            </div>` : ""}
          ${evidenceBlock(item)}
        </details>
        <a href="${escapeHtml(item.detailUrl)}">进入项目详情</a>
      </article>`;
  }

  function render() {
    const timeRange = byId("changeTimeFilter").value;
    const impact = byId("changeImpactFilter").value;
    const direction = byId("changeDirectionFilter").value;
    const action = byId("changeActionImpactFilter").value;
    const category = byId("changeCategoryFilter").value;
    const reviewStatus = byId("changeReviewFilter").value;
    const search = byId("changeSearch").value.trim().toLowerCase();
    const visible = items.filter((item) => {
      if (timeRange !== "all") {
        const observed = new Date(observedAt(item)).getTime();
        const duration = { "24h": 86400000, "7d": 604800000, "30d": 2592000000 }[timeRange];
        if (!Number.isFinite(observed) || Date.now() - observed > duration) return false;
      }
      if (impact !== "all" && impactLevel(item) !== impact) return false;
      if (direction !== "all" && item.currentStatus !== direction) return false;
      if (action !== "all" && actionImpact(item) !== action) return false;
      if (category !== "all" && !(item.triggerCategories || []).includes(category)) return false;
      if (
        reviewStatus !== "all"
        && (item.decisionReview?.status || "not_required") !== reviewStatus
      ) return false;
      if (!search) return true;
      return [
        item.projectName,
        item.symbol,
        item.currentStageLabel,
        item.currentExplanation,
      ].some((value) => String(value || "").toLowerCase().includes(search));
    });
    const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
    page = Math.max(0, Math.min(page, pageCount - 1));
    const pageStart = page * pageSize;
    const pageItems = visible.filter((_item, index) => index >= pageStart && index < pageStart + pageSize);
    byId("changeVisibleCount").textContent = number(visible.length);
    byId("changePageMeta").textContent = `第 ${page + 1} / ${pageCount} 页 · 每页 ${pageSize} 条`;
    byId("changePreviousPage").disabled = page === 0;
    byId("changeNextPage").disabled = page >= pageCount - 1;
    byId("changeProjectList").innerHTML = visible.length
      ? pageItems.map(projectCard).join("")
      : '<div class="change-empty"><strong>当前筛选没有项目</strong><p>可以清除变化方向、触发类别或搜索条件。</p></div>';
  }

  [
    "changeTimeFilter",
    "changeImpactFilter",
    "changeDirectionFilter",
    "changeActionImpactFilter",
    "changeCategoryFilter",
    "changeReviewFilter",
  ].forEach((id) => byId(id).addEventListener("change", () => { page = 0; render(); }));
  byId("changeSearch").addEventListener("input", () => { page = 0; render(); });
  byId("changePreviousPage").addEventListener("click", () => { page = Math.max(0, page - 1); render(); });
  byId("changeNextPage").addEventListener("click", () => { page += 1; render(); });
  byId("changeProjectList").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-review-result]");
    if (!button || button.disabled) return;
    const trackingResultId = button.dataset.reviewResult;
    const action = button.dataset.reviewAction;
    const note = document.querySelector(`[data-review-note="${CSS.escape(trackingResultId)}"]`)?.value.trim() || "";
    const feedback = byId("changeReviewFeedback");
    if (action === "reject" && note.length < 4) {
      feedback.hidden = false;
      feedback.className = "change-review-feedback is-error";
      feedback.textContent = "不采纳时请填写简短原因，避免以后无法追溯。";
      return;
    }
    document.querySelectorAll(`[data-review-result="${CSS.escape(trackingResultId)}"]`).forEach((item) => {
      item.disabled = true;
    });
    feedback.hidden = false;
    feedback.className = "change-review-feedback is-working";
    feedback.textContent = action === "confirm" ? "正在确认并写入状态历史…" : "正在驳回并重新安排复查…";
    try {
      const response = await fetch(reviewApi, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trackingResultId,
          action,
          note,
          actor: "software-user",
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || payload.message || "复核失败");
      feedback.className = "change-review-feedback is-success";
      feedback.textContent = payload.message;
      window.setTimeout(() => location.reload(), 700);
    } catch (error) {
      feedback.className = "change-review-feedback is-error";
      feedback.textContent = error.message || "复核失败，请稍后重试。";
      document.querySelectorAll(`[data-review-result="${CSS.escape(trackingResultId)}"]`).forEach((item) => {
        item.disabled = false;
      });
    }
  });
  const initialReviewStatus = new URLSearchParams(location.search).get("review");
  if (["pending", "confirmed", "rejected", "not_required"].includes(initialReviewStatus)) {
    byId("changeReviewFilter").value = initialReviewStatus;
  }
  render();
}());
