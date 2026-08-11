(function sourceAdapterPage() {
  const snapshot = window.PENGUIN_CONVEXITY_SOURCE_ADAPTER;
  if (!snapshot) return;

  const state = {
    status: "all",
    type: "all",
    search: "",
    selectedId: "",
  };
  const elements = {
    principle: document.getElementById("adapterPrinciple"),
    generatedAt: document.getElementById("adapterGeneratedAt"),
    sourceTotal: document.getElementById("adapterSourceTotal"),
    completeTotal: document.getElementById("adapterCompleteTotal"),
    partialTotal: document.getElementById("adapterPartialTotal"),
    rawOnlyTotal: document.getElementById("adapterRawOnlyTotal"),
    recoveredTotal: document.getElementById("adapterRecoveredTotal"),
    gapTotal: document.getElementById("adapterGapTotal"),
    visibleCount: document.getElementById("adapterVisibleCount"),
    status: document.getElementById("adapterStatusFilter"),
    type: document.getElementById("adapterTypeFilter"),
    search: document.getElementById("adapterSearch"),
    body: document.getElementById("adapterTableBody"),
    detail: document.getElementById("adapterDetail"),
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  }

  function formatTime(value) {
    if (!value) return "尚无成功采集";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value
      : new Intl.DateTimeFormat("zh-CN", {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        }).format(date);
  }

  function statusLabel(value) {
    return {
      complete: "完整接入",
      partial: "部分接入",
      raw_only: "仅原始留存",
      no_data: "未采到数据",
    }[value] || value;
  }

  function renderSummary() {
    const counts = snapshot.counts;
    elements.principle.textContent = snapshot.principle;
    elements.generatedAt.textContent = `状态生成：${formatTime(snapshot.generatedAt)}`;
    elements.sourceTotal.textContent = counts.sources.toLocaleString("zh-CN");
    elements.completeTotal.textContent = counts.completeSources.toLocaleString("zh-CN");
    elements.partialTotal.textContent = counts.partialSources.toLocaleString("zh-CN");
    elements.rawOnlyTotal.textContent = counts.rawOnlySources.toLocaleString("zh-CN");
    elements.recoveredTotal.textContent = counts.recoveredEvidenceItems.toLocaleString("zh-CN");
    elements.gapTotal.textContent = counts.missingRawEvidenceItems.toLocaleString("zh-CN");
  }

  function populateTypes() {
    const types = [...new Set(snapshot.sources.map((source) => source.sourceType).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b, "zh-CN"));
    elements.type.innerHTML = [
      '<option value="all">全部类型</option>',
      ...types.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`),
    ].join("");
  }

  function filteredSources() {
    const query = state.search.trim().toLowerCase();
    return snapshot.sources.filter((source) => (
      (state.status === "all" || source.adapterStatus === state.status)
      && (state.type === "all" || source.sourceType === state.type)
      && (!query || [
        source.sourceName,
        source.sourceId,
        source.sourceType,
      ].join(" ").toLowerCase().includes(query))
    ));
  }

  function renderRows(sources) {
    if (!sources.length) {
      elements.body.innerHTML = '<tr><td colspan="6" class="source-adapter-empty">当前筛选下没有信源。</td></tr>';
      return;
    }
    elements.body.innerHTML = sources.map((source) => `
      <tr data-source-id="${escapeHtml(source.sourceId)}" class="${source.sourceId === state.selectedId ? "is-selected" : ""}">
        <td><strong>${escapeHtml(source.sourceName)}</strong><small>${escapeHtml(source.sourceType)} · ${escapeHtml(source.sourceId)}</small></td>
        <td><span class="adapter-status ${escapeHtml(source.adapterStatus)}">${statusLabel(source.adapterStatus)}</span></td>
        <td><strong>${Number(source.rawCount).toLocaleString("zh-CN")}</strong><small>仅原始 ${Number(source.rawOnlyCount).toLocaleString("zh-CN")}</small></td>
        <td><strong>${Number(source.linkedEvidenceCount).toLocaleString("zh-CN")} / ${Number(source.evidenceCount).toLocaleString("zh-CN")}</strong><small>已连接 / 全部证据</small></td>
        <td><strong>${Number(source.missingRawCount).toLocaleString("zh-CN")}</strong><small>已恢复 ${Number(source.recoveredCount).toLocaleString("zh-CN")}</small></td>
        <td>${escapeHtml(formatTime(source.latestCollectedAt))}</td>
      </tr>
    `).join("");
  }

  function renderDetail(source) {
    if (!source) {
      elements.detail.innerHTML = "<strong>选择一个信源</strong><p>这里会解释它已经接入到哪一步、还有什么缺口，以及下一步应该做什么。</p>";
      return;
    }
    const href = safeUrl(source.sourceUrl);
    const gaps = source.gapExamples.length
      ? source.gapExamples.map((gap) => {
          const gapHref = safeUrl(gap.sourceUrl);
          return `
            <li>
              <strong>${escapeHtml(gap.projectName)}</strong>
              <span>${escapeHtml(gap.summary || gap.evidenceType)}</span>
              ${gapHref ? `<a href="${escapeHtml(gapHref)}" target="_blank" rel="noreferrer">打开现有来源</a>` : "<small>没有可打开的历史来源链接</small>"}
            </li>
          `;
        }).join("")
      : "<li><strong>当前没有历史缺口</strong><span>已有证据都能回到原始记录。</span></li>";
    elements.detail.innerHTML = `
      <span class="adapter-status ${escapeHtml(source.adapterStatus)}">${statusLabel(source.adapterStatus)}</span>
      <h3>${escapeHtml(source.sourceName)}</h3>
      <p>${escapeHtml(source.nextStep)}</p>
      <dl>
        <dt>信源类型</dt><dd>${escapeHtml(source.sourceType)}</dd>
        <dt>登记状态</dt><dd>${escapeHtml(source.registryStatus)}</dd>
        <dt>原始记录</dt><dd>${Number(source.rawCount).toLocaleString("zh-CN")} 条</dd>
        <dt>研究证据</dt><dd>${Number(source.linkedEvidenceCount).toLocaleString("zh-CN")} / ${Number(source.evidenceCount).toLocaleString("zh-CN")} 条已连接</dd>
        <dt>精确恢复</dt><dd>${Number(source.recoveredCount).toLocaleString("zh-CN")} 条</dd>
        <dt>冲突</dt><dd>${Number(source.conflictCount).toLocaleString("zh-CN")} 条</dd>
        <dt>最近采集</dt><dd>${escapeHtml(formatTime(source.latestCollectedAt))}</dd>
      </dl>
      ${href ? `<a class="ledger-source-link" href="${escapeHtml(href)}" target="_blank" rel="noreferrer">打开信源主页</a>` : ""}
      <section>
        <h4>剩余历史缺口样例</h4>
        <ul class="source-adapter-gap-list">${gaps}</ul>
        ${source.missingRawCount > source.gapExamples.length ? `<small>这里只显示 ${source.gapExamples.length} 条样例，该信源共保留 ${source.missingRawCount} 条缺口。</small>` : ""}
      </section>
    `;
  }

  function render() {
    const sources = filteredSources();
    elements.visibleCount.textContent = `找到 ${sources.length.toLocaleString("zh-CN")} 个信源`;
    renderRows(sources);
    renderDetail(snapshot.sources.find((source) => source.sourceId === state.selectedId));
  }

  elements.status.addEventListener("change", () => {
    state.status = elements.status.value;
    state.selectedId = "";
    render();
  });
  elements.type.addEventListener("change", () => {
    state.type = elements.type.value;
    state.selectedId = "";
    render();
  });
  elements.search.addEventListener("input", () => {
    state.search = elements.search.value;
    state.selectedId = "";
    render();
  });
  elements.body.addEventListener("click", (event) => {
    const row = event.target.closest("[data-source-id]");
    if (!row) return;
    state.selectedId = row.dataset.sourceId;
    render();
  });

  renderSummary();
  populateTypes();
  render();
})();
