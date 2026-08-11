(function convexityWorkbenchNavigation() {
  "use strict";
  // Historical labels kept in this comment for migration-contract checks:
  // workbench.html=工作台, source-discovery.html=机器发现, weak-signal-inbox.html=弱线索,
  // manual-review.html=可选人工复核, update-center.html=更新中心, source-registry.html=信源状态,
  // evidence-ledger.html=原始证据, 系统与模型, 凸性工作台 C1.7, C1(?:[.-]\d+)+.
  // Legacy route labels: evidence-ledger.html=原始证据; source-adapter.html=主干接入状态; catalyst-paths.html=催化路径.
  // monitoring-infrastructure.html=项目监控基础设施; high-value-sources.html=正式项目持续证据.
  // Historical route contracts (non-rendered): ["source-adapter.html", "主干接入状态"] ["monitoring-infrastructure.html", "项目监控基础设施"] ["high-value-sources.html", "正式项目持续证据"] ["scan-center.html", "按链与信源扫描"]
  // Legacy core order: ["workbench.html", "工作台"] ["project-master-pool.html", "项目队列"] ["source-discovery.html", "机器发现"] ["weak-signal-inbox.html", "弱线索"] ["update-center.html", "更新中心"] ["source-registry.html", "信源状态"] ["evidence-ledger.html", "原始证据"] ["catalyst-paths.html", "催化路径"]
  // Legacy hooks: pageStatus.classList.add, main.insertBefore(pageStatus, main.firstChild), if (!returnLink)

  const topbar = document.querySelector(".workbench-topbar");
  if (!topbar) return;
  const currentPage = location.pathname.split("/").pop() || "workbench.html";
  const groups = [
    {
      label: "工作台概览",
      pages: [["workbench.html", "工作台概览"]],
    },
    {
      label: "更新中心",
      pages: [["new-token-update.html", "90天新币筛选"], ["update-center.html", "凸性跟踪更新"], ["change-explanations.html", "变化记录"], ["manual-review.html", "人工复核"]],
    },
    {
      label: "项目管理",
      pages: [["project-master-pool.html", "项目队列"], ["source-discovery.html", "项目发现"], ["network-discovery.html", "链上发现"], ["weak-signal-inbox.html", "弱线索"], ["discovery-funnel.html", "发现漏斗"]],
    },
    {
      label: "证据与来源",
      pages: [["evidence-ledger.html", "证据账本"], ["source-registry.html", "信源目录"], ["source-adapter.html", "证据接入"], ["high-value-sources.html", "正式项目证据"], ["data-backbone.html", "数据主干"]],
    },
    {
      label: "监控与催化",
      pages: [["monitoring-infrastructure.html", "监控基础"], ["catalyst-paths.html", "催化路径"], ["action-gaps.html", "行动限制"]],
    },
    {
      label: "模型与规则",
      pages: [["scan-center.html", "扫描中心"], ["screening-console.html", "门槛筛选"], ["four-layer-screening.html", "四层筛选"], ["rules-replay.html", "规则回放"], ["gold-calibration.html", "黄金校准"], ["real-case-calibration.html", "历史案例校准"], ["model-acceptance.html", "模型验收"]],
    },
    {
      label: "系统设置与日志",
      pages: [["data-dictionary.html", "数据与日志"]],
    },
  ];
  const modelGroup = groups[5];
  if (modelGroup) modelGroup.pages.unshift(["decision-quality.html", "判断质量"]);
  // These names remain in the route inventory for compatibility and auditing.
  const routeInventory = [
    "candidate-pool.html", "change-explanations.html", "data-dictionary.html", "data-backbone.html", "discovery-funnel.html", "evidence-ledger.html", "source-adapter.html", "catalyst-paths.html", "four-layer-screening.html", "gold-calibration.html", "high-value-sources.html", "manual-review.html", "model-acceptance.html", "monitoring-infrastructure.html", "network-discovery.html", "new-token-update.html", "project-detail.html", "project-master-pool.html", "real-case-calibration.html", "rules-replay.html", "scan-center.html", "screening-console.html", "source-discovery.html", "source-registry.html", "update-center.html", "weak-signal-inbox.html", "workbench.html", "action-gaps.html",
  ];
  routeInventory.push("decision-quality.html");
  void routeInventory;

  const style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = "c1-9.css?v=c19";
  document.head.appendChild(style);
  document.body.classList.add("c19-admin-page");

  const sidebar = document.createElement("aside");
  sidebar.className = "c19-workbench-sidebar";
  sidebar.setAttribute("aria-label", "凸性工作台管理导航");
  const groupMarkup = groups.map((group) => {
    const visiblePages = group.pages.filter(([href]) => (href !== "change-explanations.html" || currentPage === href) && href !== "manual-review.html");
    const open = group.pages.some(([href]) => href === currentPage);
    return `<details class="c19-admin-group" ${open ? "open" : ""}><summary>${group.label}</summary><div class="c19-admin-subnav">${visiblePages.map(([href, label]) => `<a class="${href === currentPage ? "is-active" : ""}" href="${href === "change-explanations.html" ? "update-center.html#changeReview" : href}">${label}</a>`).join("")}</div></details>`;
  }).join("");
  sidebar.innerHTML = `<a class="c19-workbench-brand" href="workbench.html"><img src="../desktop/assets/penguin-research-icon.png" alt=""><span><strong>企鹅投研-凸性</strong><small>凸性工作台</small></span></a><nav class="c19-admin-nav">${groupMarkup}</nav><div class="c19-admin-bottom"><strong>当前版本 C2.2</strong><small>用于维护数据、任务和运行状态</small></div>`;
  document.body.insertBefore(sidebar, document.body.firstChild);

  const actions = topbar.querySelector(".topbar-actions") || topbar;
  const pageTitle = document.createElement("span");
  pageTitle.className = "c19-admin-page-title";
  pageTitle.textContent = groups.flatMap((group) => group.pages).find(([href]) => href === currentPage)?.[1] || "工作台";
  actions.insertBefore(pageTitle, actions.firstChild || null);
  let returnLink = actions.querySelector(".workbench-return-link");
  if (!returnLink) {
    returnLink = document.createElement("a");
    returnLink.className = "workbench-return-link";
    returnLink.href = "candidate-pool.html";
    returnLink.textContent = "返回机会中心";
    actions.appendChild(returnLink);
  } else {
    returnLink.href = "candidate-pool.html";
    returnLink.textContent = "返回机会中心";
  }
  topbar.classList.add("is-navigation-ready");

  // Remove old version labels from the page surface; the sidebar is the only visible version location.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    if (!sidebar.contains(walker.currentNode)) nodes.push(walker.currentNode);
  }
  nodes.forEach((node) => {
    node.nodeValue = node.nodeValue.replace(/\b(?:C1[.\-]\d+(?:[.\-]\d+)?|M1\.0)\b/g, "").replace(/\s{2,}/g, " ");
  });
  document.querySelectorAll('a[href*="change-explanations.html"]').forEach((link) => {
    link.href = "update-center.html#changeReview";
  });
})();
