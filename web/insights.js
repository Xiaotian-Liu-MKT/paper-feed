// 洞察页（insights.html）的标签路由：
//   #prefs                         我的偏好（偏好报告，report.js）
//   #journals | #journals/detail   期刊表现：全部期刊一览 / 单刊详情（stats.js）
//   #journals/detail?journal=名称   直接打开某本期刊的详情
//   #fit | #fit/match              期刊匹配：匹配画像 / 研究摘要匹配（stats.js）
//   #taste                         AI 品味画像与待筛选打分（taste.js）
// 旧版 stats.html 的视图名（overview / detail / match）作为别名兼容。
// 每个标签首次激活时才加载数据；偏好报告只读取已保存结果，不会自动重新计算。
(function () {
  "use strict";

  const STORAGE_KEY = "paperFeed.insights.lastTab";
  const DEFAULT_ROUTE = "prefs";

  // 规范路由 -> 地址栏 hash
  const ROUTE_HASH = {
    prefs: "prefs",
    "journals/overview": "journals",
    "journals/detail": "journals/detail",
    "fit/profile": "fit",
    "fit/match": "fit/match",
    taste: "taste"
  };

  const ALIASES = {
    prefs: "prefs",
    report: "prefs",
    journals: "journals/overview",
    "journals/overview": "journals/overview",
    overview: "journals/overview",
    stats: "journals/overview",
    "journals/detail": "journals/detail",
    detail: "journals/detail",
    fit: "fit/profile",
    "fit/profile": "fit/profile",
    "fit/match": "fit/match",
    match: "fit/match",
    taste: "taste"
  };

  function parseRoute(raw) {
    const text = String(raw || "").replace(/^#/, "");
    if (!text) return null;
    const qIndex = text.indexOf("?");
    const path = (qIndex >= 0 ? text.slice(0, qIndex) : text).trim().toLowerCase().replace(/\/+$/, "");
    const query = qIndex >= 0 ? text.slice(qIndex + 1) : "";
    const route = ALIASES[path];
    if (!route) return null;
    let params;
    try {
      params = new URLSearchParams(query);
    } catch (_) {
      params = new URLSearchParams();
    }
    // 带 journal 参数时总是进入单刊详情
    if (params.get("journal") && route.startsWith("journals")) {
      return { route: "journals/detail", params };
    }
    return { route, params };
  }

  function readStored() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function writeStored(route) {
    try {
      window.localStorage.setItem(STORAGE_KEY, route);
    } catch (_) {
      /* 浏览器禁用存储时忽略 */
    }
  }

  function setHash(route, replace) {
    const hash = "#" + (ROUTE_HASH[route] || route);
    if (window.location.hash === hash) return;
    const url = window.location.pathname + window.location.search + hash;
    try {
      if (replace && window.history && typeof window.history.replaceState === "function") {
        window.history.replaceState(null, "", url);
        return;
      }
    } catch (_) {
      /* 回退到直接赋值 */
    }
    window.location.hash = hash;
  }

  const loaded = { prefs: false, stats: false, taste: false };

  function ensureLoaded(tab) {
    if (tab === "prefs") {
      if (!loaded.prefs && window.InsightsReport) {
        loaded.prefs = true;
        window.InsightsReport.init();
      }
      return Promise.resolve();
    }
    if (tab === "taste") {
      if (!loaded.taste && window.InsightsTaste) {
        loaded.taste = true;
        return window.InsightsTaste.init();
      }
      return Promise.resolve();
    }
    if (window.InsightsStats) {
      loaded.stats = true;
      return window.InsightsStats.init();
    }
    return Promise.resolve();
  }

  function applyRoute(parsed) {
    const route = parsed.route;
    const tab = route.split("/")[0];

    document.querySelectorAll("[data-insights-tab]").forEach((btn) => {
      const active = btn.getAttribute("data-insights-tab") === tab;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
      btn.tabIndex = active ? 0 : -1;
      // 再次点回该标签时回到上次看的子视图
      if (active) btn.setAttribute("href", "#" + (ROUTE_HASH[route] || route));
    });
    document.querySelectorAll("[data-insights-panel]").forEach((panel) => {
      panel.hidden = panel.getAttribute("data-insights-panel") !== tab;
    });
    document.querySelectorAll("[data-insights-subtab]").forEach((btn) => {
      const active = btn.getAttribute("data-insights-subtab") === route;
      btn.classList.toggle("is-active", active);
      if (active) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });
    document.querySelectorAll("[data-insights-subpanel]").forEach((panel) => {
      const name = panel.getAttribute("data-insights-subpanel");
      if (!name.startsWith(tab + "/")) return;
      panel.hidden = name !== route;
    });

    const rangePanel = document.getElementById("statsRangePanel");
    // 时间范围只作用于期刊表现 / 期刊匹配
    if (rangePanel) rangePanel.hidden = tab !== "journals" && tab !== "fit";

    writeStored(ROUTE_HASH[route] || route);

    const ready = ensureLoaded(tab);
    const journal = parsed.params && parsed.params.get("journal");
    if (journal && window.InsightsStats) {
      ready.then(() => window.InsightsStats.showJournal(journal));
    }
  }

  function onHashChange() {
    const parsed = parseRoute(window.location.hash);
    if (parsed) {
      applyRoute(parsed);
    } else {
      // 页面内其他锚点（或无效 hash）不改变当前标签
      const fallback = parseRoute(readStored()) || { route: DEFAULT_ROUTE, params: new URLSearchParams() };
      applyRoute(fallback);
      setHash(fallback.route, true);
    }
  }

  function onTabKeydown(event) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const tabs = Array.from(document.querySelectorAll("[data-insights-tab]"));
    const index = tabs.indexOf(event.currentTarget);
    if (index < 0) return;
    const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    event.preventDefault();
    next.focus();
    next.click();
  }

  function init() {
    document.querySelectorAll("[data-insights-tab]").forEach((btn) => {
      btn.addEventListener("keydown", onTabKeydown);
    });
    window.addEventListener("hashchange", onHashChange);

    const fromHash = parseRoute(window.location.hash);
    const initial =
      fromHash || parseRoute(readStored()) || { route: DEFAULT_ROUTE, params: new URLSearchParams() };
    applyRoute(initial);
    // 规范化地址栏（例如旧别名 #overview -> #journals）；带参数的深链保持原样
    if (!fromHash || !Array.from(fromHash.params.keys()).length) setHash(initial.route, true);

    // 在单刊详情里切换期刊时同步地址栏，方便复制链接
    const journalSelect = document.getElementById("statsJournal");
    if (journalSelect) {
      journalSelect.addEventListener("change", () => {
        if (!journalSelect.value) return;
        const url =
          window.location.pathname +
          window.location.search +
          "#journals/detail?journal=" +
          encodeURIComponent(journalSelect.value);
        try {
          window.history.replaceState(null, "", url);
        } catch (_) {
          /* 地址栏同步只是辅助 */
        }
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
