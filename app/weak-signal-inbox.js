(function renderWeakSignalInbox() {
  const snapshot = window.PENGUIN_CONVEXITY_WEAK_SIGNALS;
  if (!snapshot) return;

  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
  const dateTime = (value) => {
    if (!value) return "时间待补齐";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleString("zh-CN", { hour12: false });
  };
  const biasLabels = { low: "低", medium: "中", high: "高" };
  const relationLabels = {
    verified: "项目归属已核验",
    corroborated: "项目归属已交叉印证",
    pending: "项目归属待核验",
    conflict: "项目归属冲突",
    unattributed: "尚未归属项目",
  };
  const pageSize = 40;
  let page = 1;
  let selectedId = "";

  byId("weakSignalBoundary").textContent = snapshot.boundary;
  byId("weakSignalGeneratedAt").textContent = `生成于 ${dateTime(snapshot.generatedAt)}`;
  byId("weakSignalTotal").textContent = snapshot.counts.signals;
  byId("weakSignalReady").textContent = snapshot.counts.readyForCorroboration;
  byId("weakSignalDiscoveryOnly").textContent = snapshot.counts.discoveryOnly;
  byId("weakSignalBlocked").textContent = snapshot.counts.identityBlocked;
  byId("weakSignalHighBias").textContent = snapshot.counts.highPromotionBias;
  byId("weakSignalGaps").textContent = snapshot.counts.unconnectedSources;

  byId("weakSignalSourceGrid").innerHTML = snapshot.sources.map((source) => `
    <article class="status-${escapeHtml(source.connectionStatus)}">
      <header><span>${escapeHtml(source.signalTypeLabel)}</span><b>${escapeHtml(source.recordCount)}</b></header>
      <h3>${escapeHtml(source.label)}</h3>
      <p><strong>可用于：</strong>${escapeHtml(source.proves)}</p>
      <p><strong>不能证明：</strong>${escapeHtml(source.doesNotProve)}</p>
      <footer><span>${escapeHtml(source.sourceTierLabel)}</span><em>推广偏差 ${escapeHtml(biasLabels[source.promotionBias] || source.promotionBias)}</em></footer>
    </article>
  `).join("");

  snapshot.sources
    .filter((item) => item.recordCount)
    .forEach((item) => {
      byId("weakSignalSourceFilter").insertAdjacentHTML(
        "beforeend",
        `<option value="${escapeHtml(item.sourceId)}">${escapeHtml(item.label)}</option>`,
      );
    });
  const signalTypes = new Map();
  snapshot.records.forEach((item) => signalTypes.set(item.signalType, item.signalTypeLabel));
  [...signalTypes.entries()].sort((a, b) => a[1].localeCompare(b[1], "zh-CN")).forEach(([value, label]) => {
    byId("weakSignalTypeFilter").insertAdjacentHTML(
      "beforeend",
      `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`,
    );
  });

  function filteredRecords() {
    const source = byId("weakSignalSourceFilter").value;
    const type = byId("weakSignalTypeFilter").value;
    const triage = byId("weakSignalTriageFilter").value;
    const bias = byId("weakSignalBiasFilter").value;
    const query = byId("weakSignalSearch").value.trim().toLowerCase();
    return snapshot.records.filter((item) => {
      if (source !== "all" && item.sourceId !== source) return false;
      if (type !== "all" && item.signalType !== type) return false;
      if (triage !== "all" && item.triageStatus !== triage) return false;
      if (bias !== "all" && item.promotionBias !== bias) return false;
      if (!query) return true;
      return [
        item.title,
        item.summary,
        item.projectName,
        item.sourceName,
        item.metadata?.contractAddress,
        item.metadata?.symbol,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }

  function renderDetail(item) {
    if (!item) {
      byId("weakSignalDetail").innerHTML = `
        <strong>当前筛选没有线索</strong>
        <p>可以放宽信源、类型、状态或推广偏差条件。</p>
      `;
      return;
    }
    const projectLink = item.projectDetailUrl
      ? `<a href="${escapeHtml(item.projectDetailUrl)}">打开项目详情</a>`
      : "<span>尚未归属正式项目</span>";
    const sourceLink = item.sourceUrl
      ? `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">打开原始入口</a>`
      : "<span>原始入口待补齐</span>";
    byId("weakSignalDetail").innerHTML = `
      <header>
        <span class="status-${escapeHtml(item.triageStatus)}">${escapeHtml(item.triageLabel)}</span>
        <small>${escapeHtml(dateTime(item.observedAt))}</small>
      </header>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.summary || "当前只有发现记录，尚无可核验摘要。")}</p>
      <dl>
        <div><dt>发现来源</dt><dd>${escapeHtml(item.sourceName)}</dd></div>
        <div><dt>线索类型</dt><dd>${escapeHtml(item.signalTypeLabel)}</dd></div>
        <div><dt>来源层级</dt><dd>${escapeHtml(item.sourceTierLabel)}</dd></div>
        <div><dt>推广偏差</dt><dd>${escapeHtml(biasLabels[item.promotionBias] || item.promotionBias)}</dd></div>
        <div><dt>项目归属</dt><dd>${escapeHtml(relationLabels[item.projectRelationStatus] || item.projectRelationStatus)}</dd></div>
        <div><dt>原始记录</dt><dd>${escapeHtml(item.rawEventId || "尚未连接逐条原始记录")}</dd></div>
      </dl>
      <section>
        <strong>升级为正式证据前还缺什么</strong>
        <p>${escapeHtml(item.upgradeRequirement)}</p>
      </section>
      <footer>${sourceLink}${projectLink}<a href="evidence-ledger.html">查看原始证据账本</a></footer>
    `;
  }

  function render() {
    const records = filteredRecords();
    const pages = Math.max(1, Math.ceil(records.length / pageSize));
    page = Math.min(page, pages);
    const visible = records.slice((page - 1) * pageSize, page * pageSize);
    byId("weakSignalVisibleCount").textContent = `符合筛选 ${records.length} 条，本页 ${visible.length} 条`;
    byId("weakSignalPageLabel").textContent = `第 ${page} / ${pages} 页`;
    byId("weakSignalPrevious").disabled = page <= 1;
    byId("weakSignalNext").disabled = page >= pages;
    byId("weakSignalList").innerHTML = visible.length
      ? visible.map((item) => `
          <button class="${item.weakSignalId === selectedId ? "is-active" : ""}" type="button" data-signal-id="${escapeHtml(item.weakSignalId)}">
            <span class="status-${escapeHtml(item.triageStatus)}">${escapeHtml(item.triageLabel)}</span>
            <strong>${escapeHtml(item.title)}</strong>
            <p>${escapeHtml(item.summary || item.sourceName)}</p>
            <small>${escapeHtml(item.signalTypeLabel)} · ${escapeHtml(item.sourceName)} · ${escapeHtml(dateTime(item.observedAt))}</small>
          </button>
        `).join("")
      : '<p class="detail-empty">当前筛选没有线索。</p>';
    if (!visible.some((item) => item.weakSignalId === selectedId)) {
      selectedId = visible[0]?.weakSignalId || "";
    }
    renderDetail(visible.find((item) => item.weakSignalId === selectedId));
    byId("weakSignalList").querySelectorAll("[data-signal-id]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedId = button.dataset.signalId;
        render();
      });
    });
  }

  ["weakSignalSourceFilter", "weakSignalTypeFilter", "weakSignalTriageFilter", "weakSignalBiasFilter"].forEach((id) => {
    byId(id).addEventListener("change", () => {
      page = 1;
      render();
    });
  });
  byId("weakSignalSearch").addEventListener("input", () => {
    page = 1;
    render();
  });
  byId("weakSignalPrevious").addEventListener("click", () => {
    page = Math.max(1, page - 1);
    render();
  });
  byId("weakSignalNext").addEventListener("click", () => {
    page += 1;
    render();
  });

  const requestedProject = new URLSearchParams(location.search).get("project");
  if (requestedProject) {
    byId("weakSignalSearch").value = snapshot.records.find(
      (item) => item.projectId === requestedProject,
    )?.projectName || requestedProject;
  }
  render();
})();
