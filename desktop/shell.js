(function initializeConvexityDesktop() {
  "use strict";
  // Previous metadata retained for migration tooling: "workbench.html": ["凸性工作台", "研究与机器任务"].
  const frame = document.getElementById("appFrame");
  const frameWrap = document.querySelector(".desktop-host");
  const loading = document.getElementById("loading");
  const stateKey = "penguin-convexity:desktop-shell-m1p0";
  let currentRoute = "candidate-pool.html";
  let lastDetailRoute = readState().lastDetailRoute || "project-detail.html";
  const pageMeta = {
    "candidate-pool.html": ["凸性机会中心", "机会首页"],
    "project-detail.html": ["凸性机会中心", "项目详情"],
    "workbench.html": ["凸性工作台", "工作台概览"],
  };

  function readState() {
    try { return JSON.parse(localStorage.getItem(stateKey)) || {}; } catch (_error) { return {}; }
  }

  function saveState() {
    try { localStorage.setItem(stateKey, JSON.stringify({ lastDetailRoute })); } catch (_error) { /* preference storage is optional */ }
  }

  function cleanRoute(value) {
    const route = String(value || "").replace(/^\//, "");
    const page = route.split(/[?#]/)[0];
    return page.endsWith(".html") ? route : "candidate-pool.html";
  }

  function routeFromFrame() {
    try {
      const url = new URL(frame.contentWindow.location.href);
      if (url.origin !== location.origin) return currentRoute;
      return `${url.pathname.split("/").pop()}${url.search}${url.hash}`;
    } catch (_error) {
      return currentRoute;
    }
  }

  function openRoute(route) {
    currentRoute = cleanRoute(route);
    loading.classList.remove("is-hidden");
    frame.src = `/${currentRoute}`;
  }

  frame.addEventListener("load", () => {
    currentRoute = cleanRoute(routeFromFrame());
    if (currentRoute.startsWith("project-detail.html")) {
      lastDetailRoute = currentRoute;
      saveState();
    }
    loading.classList.add("is-hidden");
    frameWrap.dataset.route = currentRoute;
  });

  // Keep the host API small and backwards-compatible for smoke tests and support tools.
  window.PenguinConvexityDesktop = { openRoute, routeFromFrame, cleanRoute, get lastDetailRoute() { return lastDetailRoute; } };
})();
