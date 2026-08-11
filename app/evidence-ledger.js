(function evidenceLedgerPage() {
  const snapshot = window.PENGUIN_CONVEXITY_EVIDENCE_LEDGER;
  if (!snapshot) return;

  const pageSize = 100;
  const state = {
    mode: "records",
    role: "all",
    status: "all",
    source: "all",
    search: "",
    page: 1,
    selectedId: "",
  };

  const elements = {
    principle: document.getElementById("ledgerPrinciple"),
    generatedAt: document.getElementById("ledgerGeneratedAt"),
    rawTotal: document.getElementById("ledgerRawTotal"),
    externalTotal: document.getElementById("ledgerExternalTotal"),
    linkedTotal: document.getElementById("ledgerLinkedTotal"),
    rawOnlyTotal: document.getElementById("ledgerRawOnlyTotal"),
    gapTotal: document.getElementById("ledgerGapTotal"),
    sourceCoverage: document.getElementById("ledgerSourceCoverage"),
    title: document.getElementById("ledgerBrowserTitle"),
    role: document.getElementById("ledgerRoleFilter"),
    status: document.getElementById("ledgerStatusFilter"),
    source: document.getElementById("ledgerSourceFilter"),
    search: document.getElementById("ledgerSearch"),
    visibleCount: document.getElementById("ledgerVisibleCount"),
    head: document.getElementById("ledgerTableHead"),
    body: document.getElementById("ledgerTableBody"),
    previous: document.getElementById("ledgerPreviousPage"),
    next: document.getElementById("ledgerNextPage"),
    pageStatus: document.getElementById("ledgerPageStatus"),
    detail: document.getElementById("ledgerDetail"),
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
    if (!value) return "未记录";
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

  function shortId(value) {
    const text = String(value || "");
    return text.length > 28 ? `${text.slice(0, 18)}…${text.slice(-7)}` : text;
  }

  function roleLabel(value) {
    return value === "external_source" ? "外部来源" : "机器审计";
  }

  function statusLabel(value) {
    return value === "linked" ? "已形成证据" : "尚未转化";
  }

  function renderSummary() {
    const counts = snapshot.counts;
    elements.principle.textContent = snapshot.principle;
    elements.generatedAt.textContent = `账本生成：${formatTime(snapshot.generatedAt)}`;
    elements.rawTotal.textContent = counts.rawEvents.toLocaleString("zh-CN");
    elements.externalTotal.textContent = counts.externalRecords.toLocaleString("zh-CN");
    elements.linkedTotal.textContent = counts.linkedRawEvents.toLocaleString("zh-CN");
    elements.rawOnlyTotal.textContent = counts.rawOnlyEvents.toLocaleString("zh-CN");
    elements.gapTotal.textContent = counts.missingRawEvidenceItems.toLocaleString("zh-CN");
  }

  function renderSourceCoverage() {
    elements.sourceCoverage.innerHTML = snapshot.sourceCoverage.map((source) => `
      <button type="button" data-source-id="${escapeHtml(source.sourceId)}">
        <span>${escapeHtml(source.sourceName)}</span>
        <strong>${Number(source.rawCount).toLocaleString("zh-CN")}</strong>
        <small>已成证据 ${Number(source.linkedRawCount).toLocaleString("zh-CN")} · ${roleLabel(source.recordRole)}</small>
        <em>${formatTime(source.latestCollectedAt)}</em>
      </button>
    `).join("");
  }

  function populateSources() {
    let sources;
    if (state.mode === "gaps") {
      const gapSources = new Map();
      snapshot.gaps.forEach((gap) => {
        const sourceId = gap.sourceId || "unregistered";
        const current = gapSources.get(sourceId) || {
          sourceId,
          sourceName: gap.sourceName || "来源未登记",
          count: 0,
        };
        current.count += 1;
        gapSources.set(sourceId, current);
      });
      sources = [...gapSources.values()].map((source) => ({
        ...source,
        label: `${source.sourceName} (缺口 ${source.count})`,
      }));
    } else {
      sources = snapshot.sourceCoverage.map((source) => ({
        ...source,
        label: `${source.sourceName} (${source.rawCount})`,
      }));
    }
    sources.sort((a, b) => a.sourceName.localeCompare(b.sourceName, "zh-CN"));
    elements.source.innerHTML = [
      '<option value="all">全部信源</option>',
      ...sources.map((source) => (
        `<option value="${escapeHtml(source.sourceId)}">${escapeHtml(source.label)}</option>`
      )),
    ].join("");
    elements.source.value = state.source;
  }

  function searchableRecord(item) {
    return [
      item.rawEventId,
      item.externalId,
      item.sourceName,
      item.sourceId,
      item.projectHint,
      item.assetHint,
      item.chainHint,
      item.eventType,
      item.excerpt,
      ...(item.evidenceIds || []),
    ].join(" ").toLowerCase();
  }

  function searchableGap(item) {
    return [
      item.evidenceId,
      item.projectId,
      item.projectName,
      item.sourceId,
      item.sourceName,
      item.evidenceType,
      item.summary,
    ].join(" ").toLowerCase();
  }

  function filteredItems() {
    const query = state.search.trim().toLowerCase();
    if (state.mode === "gaps") {
      return snapshot.gaps.filter((item) => (
        (state.source === "all" || item.sourceId === state.source)
        && (!query || searchableGap(item).includes(query))
      ));
    }
    return snapshot.records.filter((item) => (
      (state.role === "all" || item.recordRole === state.role)
      && (state.status === "all" || item.traceStatus === state.status)
      && (state.source === "all" || item.sourceId === state.source)
      && (!query || searchableRecord(item).includes(query))
    ));
  }

  function recordRows(items) {
    return items.map((item) => `
      <tr data-record-id="${escapeHtml(item.rawEventId)}" class="${item.rawEventId === state.selectedId ? "is-selected" : ""}">
        <td><span class="ledger-role ${escapeHtml(item.recordRole)}">${roleLabel(item.recordRole)}</span></td>
        <td><strong>${escapeHtml(item.projectHint || item.assetHint || "未归属项目")}</strong><small>${escapeHtml(item.eventType || "未分类事件")}</small></td>
        <td><strong>${escapeHtml(item.sourceName)}</strong><small>${escapeHtml(shortId(item.externalId))}</small></td>
        <td><span class="ledger-trace ${escapeHtml(item.traceStatus)}">${statusLabel(item.traceStatus)}</span><small>${item.evidenceIds.length} 条证据 · ${item.downstreamCount} 次下游引用</small></td>
        <td>${escapeHtml(formatTime(item.collectedAt))}</td>
      </tr>
    `).join("");
  }

  function gapRows(items) {
    return items.map((item) => `
      <tr data-gap-id="${escapeHtml(item.evidenceId)}" class="${item.evidenceId === state.selectedId ? "is-selected" : ""}">
        <td><span class="ledger-trace missing">缺少原始记录</span></td>
        <td><strong>${escapeHtml(item.projectName || "未归属项目")}</strong><small>${escapeHtml(shortId(item.projectId))}</small></td>
        <td><strong>${escapeHtml(item.sourceName || "来源未登记")}</strong><small>${escapeHtml(item.evidenceType)}</small></td>
        <td><span>${escapeHtml(item.summary || "无摘要")}</span></td>
        <td>${escapeHtml(formatTime(item.observedAt))}</td>
      </tr>
    `).join("");
  }

  function renderDetail(item) {
    if (!item) {
      elements.detail.innerHTML = "<strong>选择一条记录</strong><p>这里会展示来源链接、运行批次、内容指纹、研究证据和下游用途。</p>";
      return;
    }
    if (state.mode === "gaps") {
      const href = safeUrl(item.sourceUrl);
      elements.detail.innerHTML = `
        <span class="ledger-trace missing">历史缺口</span>
        <h3>${escapeHtml(item.projectName || "未归属项目")}</h3>
        <p>${escapeHtml(item.summary || "该证据没有摘要。")}</p>
        <dl>
          <dt>证据ID</dt><dd>${escapeHtml(item.evidenceId)}</dd>
          <dt>证据类型</dt><dd>${escapeHtml(item.evidenceType)}</dd>
          <dt>登记来源</dt><dd>${escapeHtml(item.sourceName || item.sourceId || "未登记")}</dd>
          <dt>证据时间</dt><dd>${escapeHtml(formatTime(item.observedAt))}</dd>
        </dl>
        ${href ? `<a class="ledger-source-link" href="${escapeHtml(href)}" target="_blank" rel="noreferrer">打开现有来源链接</a>` : ""}
        <div class="ledger-gap-note"><strong>这意味着什么</strong><p>研究证据仍然保留，但当时没有保存可核验的原始记录。系统不会伪造补齐，后续对应信源重新采集时再建立新版本。</p></div>
      `;
      return;
    }

    const href = safeUrl(item.sourceUrl);
    const evidence = item.evidenceIds.length
      ? item.evidenceIds.map((id) => `<li>${escapeHtml(id)}</li>`).join("")
      : "<li>尚未形成研究证据</li>";
    const downstream = item.downstreamTargets.length
      ? item.downstreamTargets.map((target) => (
          `<li><strong>${escapeHtml(target.type)}</strong><span>${escapeHtml(target.id)}</span><small>${escapeHtml(formatTime(target.capturedAt))}</small></li>`
        )).join("")
      : "<li>尚未进入评分、结论或状态变化</li>";
    elements.detail.innerHTML = `
      <div class="ledger-detail-badges">
        <span class="ledger-role ${escapeHtml(item.recordRole)}">${roleLabel(item.recordRole)}</span>
        <span class="ledger-trace ${escapeHtml(item.traceStatus)}">${statusLabel(item.traceStatus)}</span>
      </div>
      <h3>${escapeHtml(item.projectHint || item.assetHint || item.eventType || "未归属原始记录")}</h3>
      <p>${escapeHtml(item.excerpt || "该原始记录没有文本摘要，可通过来源链接和ID核验。")}</p>
      <dl>
        <dt>原始记录ID</dt><dd>${escapeHtml(item.rawEventId)}</dd>
        <dt>信源</dt><dd>${escapeHtml(item.sourceName)} · ${escapeHtml(item.sourceId)}</dd>
        <dt>采集运行</dt><dd>${escapeHtml(item.runId || "未记录")}</dd>
        <dt>来源记录ID</dt><dd>${escapeHtml(item.externalId)}</dd>
        <dt>内容指纹</dt><dd>${escapeHtml(item.contentHash)}</dd>
        <dt>采集时间</dt><dd>${escapeHtml(formatTime(item.collectedAt))}</dd>
        <dt>原始发布时间</dt><dd>${escapeHtml(formatTime(item.publishedAt))}</dd>
      </dl>
      ${href ? `<a class="ledger-source-link" href="${escapeHtml(href)}" target="_blank" rel="noreferrer">打开原始来源</a>` : '<span class="ledger-no-link">该机器记录没有外部链接</span>'}
      <section><h4>形成的研究证据</h4><ul>${evidence}</ul></section>
      <section><h4>进入的下游用途</h4><ul class="ledger-downstream-list">${downstream}</ul>${item.downstreamCount > item.downstreamTargets.length ? `<small>这里只显示最近 ${item.downstreamTargets.length} 条，账本共保留 ${item.downstreamCount} 次引用。</small>` : ""}</section>
    `;
  }

  function render() {
    const items = filteredItems();
    const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
    state.page = Math.min(state.page, pageCount);
    const start = (state.page - 1) * pageSize;
    const pageItems = items.slice(start, start + pageSize);
    elements.title.textContent = state.mode === "records" ? "全部原始记录" : "历史原始记录缺口";
    elements.role.disabled = state.mode === "gaps";
    elements.status.disabled = state.mode === "gaps";
    elements.visibleCount.textContent = `找到 ${items.length.toLocaleString("zh-CN")} 条`;
    elements.pageStatus.textContent = `第 ${state.page} / ${pageCount} 页`;
    elements.previous.disabled = state.page <= 1;
    elements.next.disabled = state.page >= pageCount;
    elements.head.innerHTML = state.mode === "records"
      ? "<tr><th>角色</th><th>项目 / 事件</th><th>来源</th><th>溯源状态</th><th>采集时间</th></tr>"
      : "<tr><th>状态</th><th>项目</th><th>登记来源</th><th>证据摘要</th><th>证据时间</th></tr>";
    elements.body.innerHTML = state.mode === "records"
      ? recordRows(pageItems)
      : gapRows(pageItems);

    const selected = state.mode === "records"
      ? snapshot.records.find((item) => item.rawEventId === state.selectedId)
      : snapshot.gaps.find((item) => item.evidenceId === state.selectedId);
    renderDetail(selected);
  }

  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-mode]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.mode = button.dataset.mode;
      state.role = "all";
      state.status = "all";
      state.source = "all";
      state.page = 1;
      state.selectedId = "";
      elements.role.value = "all";
      elements.status.value = "all";
      populateSources();
      render();
    });
  });
  elements.role.addEventListener("change", () => {
    state.role = elements.role.value;
    state.page = 1;
    render();
  });
  elements.status.addEventListener("change", () => {
    state.status = elements.status.value;
    state.page = 1;
    render();
  });
  elements.source.addEventListener("change", () => {
    state.source = elements.source.value;
    state.page = 1;
    render();
  });
  elements.search.addEventListener("input", () => {
    state.search = elements.search.value;
    state.page = 1;
    render();
  });
  elements.previous.addEventListener("click", () => {
    state.page -= 1;
    render();
  });
  elements.next.addEventListener("click", () => {
    state.page += 1;
    render();
  });
  elements.body.addEventListener("click", (event) => {
    const row = event.target.closest("tr");
    if (!row) return;
    state.selectedId = row.dataset.recordId || row.dataset.gapId || "";
    render();
  });
  elements.sourceCoverage.addEventListener("click", (event) => {
    const button = event.target.closest("[data-source-id]");
    if (!button) return;
    state.mode = "records";
    state.source = button.dataset.sourceId;
    state.page = 1;
    state.selectedId = "";
    populateSources();
    document.querySelectorAll("[data-mode]").forEach((item) => {
      item.classList.toggle("active", item.dataset.mode === "records");
    });
    render();
    document.querySelector(".evidence-ledger-browser").scrollIntoView({ behavior: "smooth" });
  });

  renderSummary();
  renderSourceCoverage();
  populateSources();
  render();
})();
