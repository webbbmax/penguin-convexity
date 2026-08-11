(function initializePenguinPageState() {
  const prefix = "penguin.convexity.page-state.c1p08:";

  function load(pageId) {
    try {
      return JSON.parse(localStorage.getItem(`${prefix}${pageId}`) || "{}");
    } catch (_error) {
      return {};
    }
  }

  function save(pageId, value) {
    try {
      localStorage.setItem(
        `${prefix}${pageId}`,
        JSON.stringify({ ...value, savedAt: new Date().toISOString() }),
      );
      return true;
    } catch (_error) {
      return false;
    }
  }

  function restoreScroll(value, options = {}) {
    if (options.skipWhenHash && location.hash) return;
    const scrollY = Number(value?.scrollY || 0);
    if (scrollY <= 0) return;
    setTimeout(() => window.scrollTo({ top: scrollY, behavior: "auto" }), 80);
  }

  function notify(message) {
    const existing = document.querySelector(".page-state-notice");
    if (existing) existing.remove();
    const notice = document.createElement("div");
    notice.className = "page-state-notice";
    notice.setAttribute("role", "status");
    notice.textContent = message;
    document.body.appendChild(notice);
    setTimeout(() => notice.classList.add("is-visible"), 20);
    setTimeout(() => {
      notice.classList.remove("is-visible");
      setTimeout(() => notice.remove(), 220);
    }, 3200);
  }

  window.PenguinPageState = {
    load,
    save,
    restoreScroll,
    notify,
  };
}());
