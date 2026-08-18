(function convexityWorkbenchNavigation() {
  "use strict";
  // Historical labels kept in this comment for migration-contract checks:
  // workbench.html=工作台, source-discovery.html=机器发现, weak-signal-inbox.html=弱线索,
  // manual-review.html=可选人工复核, update-center.html=更新中心, source-registry.html=信源状态,
  // evidence-ledger.html=原始证据, 系统与模型, 凸性工作台 C1.7, C1(?:[.-]\d+)+.
  // Legacy route labels: evidence-ledger.html=原始证据; source-adapter.html=主干接入状态; catalyst-paths.html=催化路径.
  // monitoring-infrastructure.html=项目监控基础设施; high-value-sources.html=正式项目持续证据.
  // Historical route contracts (non-rendered): ["source-adapter.html", "主干接入状态"] ["monitoring-infrastructure.html", "项目监控基础设施"] ["high-value-sources.html", "正式项目持续证据"] ["scan-center.html", "按链与信源扫描"]
  // C2.4 screening label contract (non-rendered): ["new-token-update.html", "90 天候选"]
  // Legacy core order: ["workbench.html", "工作台"] ["project-master-pool.html", "项目队列"] ["source-discovery.html", "机器发现"] ["weak-signal-inbox.html", "弱线索"] ["update-center.html", "更新中心"] ["source-registry.html", "信源状态"] ["evidence-ledger.html", "原始证据"] ["catalyst-paths.html", "催化路径"]
  // Legacy hooks: pageStatus.classList.add, main.insertBefore(pageStatus, main.firstChild), if (!returnLink)

  const topbar = document.querySelector(".workbench-topbar");
  if (!topbar) return;
  window.PENGUIN_CONVEXITY_C24_TAKEOVER_PENDING = true;
  const currentPage = location.pathname.split("/").pop() || "workbench.html";
  const c25OwnedPages = new Set([
    "workbench.html", "task-ledger.html", "task-detail.html", "new-token-update.html", "update-center.html",
    "candidate-production.html", "maintenance-jobs.html", "legacy-jobs.html", "on-demand-tools.html",
    "chain-source-health.html", "rule-transparency.html", "decision-trace.html", "snapshot-handoffs.html", "run-audit.html",
  ]);
  const c25OwnsCurrentPage = c25OwnedPages.has(currentPage);
  document.documentElement.dataset.adminRendererOwner = c25OwnsCurrentPage ? "c25" : "c24";
  const groups = [
    {
      label: "管理者总览",
      pages: [["workbench.html", "管理者总览"]],
    },
    {
      label: "全部任务",
      pages: [["task-ledger.html", "全部任务账本"]],
    },
    {
      label: "任务详情",
      pages: [["new-token-update.html", "90天新币筛选"], ["update-center.html", "凸性跟踪"], ["candidate-production.html", "候选生产与历史续跑"], ["maintenance-jobs.html", "维护与启动检查"], ["legacy-jobs.html", "旧版与停用入口"], ["on-demand-tools.html", "按需工具"], ["task-detail.html", "通用任务详情"]],
    },
    {
      label: "逐链与来源",
      pages: [["chain-source-health.html", "健康矩阵"], ["network-discovery.html", "链上发现"], ["source-registry.html", "信源目录"], ["source-adapter.html", "证据接入"]],
    },
    {
      label: "规则透明中心",
      pages: [["rule-transparency.html", "基线、有效值与覆盖"], ["rules-replay.html", "规则重放"], ["screening-console.html", "初筛与公开底线"], ["four-layer-screening.html", "主链与四路径"], ["decision-quality.html", "判断质量"], ["gold-calibration.html", "固定真实样本"], ["real-case-calibration.html", "时间外结果"]],
    },
    {
      label: "项目判定解释",
      pages: [["decision-trace.html", "按assetId追溯"], ["project-master-pool.html", "项目队列"], ["source-discovery.html", "项目发现"], ["discovery-funnel.html", "发现漏斗"], ["weak-signal-inbox.html", "补证与待跟踪"], ["evidence-ledger.html", "证据账本"], ["high-value-sources.html", "项目证据"]],
    },
    {
      label: "数据快照与交接",
      pages: [["snapshot-handoffs.html", "快照交接"], ["data-backbone.html", "数据主干"], ["monitoring-infrastructure.html", "监控基础"]],
    },
    {
      label: "运行记录与审计",
      pages: [["run-audit.html", "运行与管理审计"], ["data-dictionary.html", "数据与日志"], ["model-acceptance.html", "发布验收"], ["scan-center.html", "链上发现后继"], ["catalyst-paths.html", "催化与失效"], ["action-gaps.html", "当前限制"]],
    },
  ];
  // These names remain in the route inventory for compatibility and auditing.
  const routeInventory = [
    "candidate-pool.html", "change-explanations.html", "data-dictionary.html", "data-backbone.html", "discovery-funnel.html", "evidence-ledger.html", "source-adapter.html", "catalyst-paths.html", "four-layer-screening.html", "gold-calibration.html", "high-value-sources.html", "manual-review.html", "model-acceptance.html", "monitoring-infrastructure.html", "network-discovery.html", "new-token-update.html", "project-detail.html", "project-master-pool.html", "real-case-calibration.html", "rules-replay.html", "scan-center.html", "screening-console.html", "source-discovery.html", "source-registry.html", "update-center.html", "weak-signal-inbox.html", "workbench.html", "action-gaps.html",
  ];
  routeInventory.push("decision-quality.html");
  routeInventory.push("task-ledger.html", "task-detail.html", "candidate-production.html", "maintenance-jobs.html", "legacy-jobs.html", "on-demand-tools.html", "chain-source-health.html", "rule-transparency.html", "decision-trace.html", "snapshot-handoffs.html", "run-audit.html");
  void routeInventory;

  const style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = "c1-9.css?v=c19";
  document.head.appendChild(style);
  document.body.classList.add("c19-admin-page");
  document.body.classList.add("c25-admin-ready");

  const sidebar = document.createElement("aside");
  sidebar.className = "c19-workbench-sidebar";
  sidebar.setAttribute("aria-label", "凸性工作台管理导航");
  const groupMarkup = groups.map((group) => {
    const visiblePages = group.pages.filter(([href]) => (href !== "change-explanations.html" || currentPage === href) && href !== "manual-review.html");
    const open = group.pages.some(([href]) => href === currentPage);
    return `<details class="c19-admin-group" ${open ? "open" : ""}><summary>${group.label}</summary><div class="c19-admin-subnav">${visiblePages.map(([href, label]) => `<a class="${href === currentPage ? "is-active" : ""}" href="${href === "change-explanations.html" ? "update-center.html#changeReview" : href}">${label}</a>`).join("")}</div></details>`;
  }).join("");
  sidebar.innerHTML = `<a class="c19-workbench-brand" href="workbench.html"><img src="../desktop/assets/penguin-research-icon.png" alt=""><span><strong>企鹅投研-凸性</strong><small>洞见共识之外的价值</small></span></a><nav class="c19-admin-nav">${groupMarkup}</nav><div class="c19-admin-bottom"><strong>当前版本 C2.5</strong><small>管理者控制面</small></div>`;
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
    node.nodeValue = node.nodeValue.replace(/\b(?:C[12][.\-]\d+(?:[.\-]\d+)?|M1\.0)\b/g, "").replace(/\s{2,}/g, " ");
  });
  document.querySelectorAll('a[href*="change-explanations.html"]').forEach((link) => {
    link.href = "update-center.html#changeReview";
  });

  const loadC24 = () => {
    if (c25OwnsCurrentPage) return;
    if (document.querySelector('script[data-c24-admin-app]')) return;
    const c24Style = document.createElement("link");
    c24Style.rel = "stylesheet";
    c24Style.href = "c2-4.css?v=c24-4";
    document.head.appendChild(c24Style);
    const loadApp = () => {
      if (document.querySelector('script[data-c24-admin-app]')) return;
      const c24App = document.createElement("script");
      c24App.src = "c2-4-admin.js?v=c24-4";
      c24App.dataset.c24AdminApp = "true";
      document.body.appendChild(c24App);
    };
    if (window.PENGUIN_CONVEXITY_C24_ADMIN) {
      loadApp();
      return;
    }
    const c24Snapshot = document.createElement("script");
    c24Snapshot.src = "c2-4-admin-snapshot.js?v=c24-4";
    c24Snapshot.onload = loadApp;
    document.body.appendChild(c24Snapshot);
  };
  const loadC25 = () => {
    if (!document.querySelector('link[data-c25-design]')) {
      const design = document.createElement("link");
      design.rel = "stylesheet";
      design.href = "c2-5.css?v=c25-1";
      design.dataset.c25Design = "true";
      document.head.appendChild(design);
    }
    if (!document.querySelector('script[data-c25-admin-app]')) {
      const app = document.createElement("script");
      app.src = "c2-5-admin.js?v=c25-5";
      app.dataset.c25AdminApp = "true";
      document.body.appendChild(app);
    }
  };
  if (document.readyState === "complete") {
    setTimeout(() => { loadC24(); loadC25(); }, 0);
  } else {
    window.addEventListener("load", () => { loadC24(); loadC25(); }, { once: true });
  }
})();
