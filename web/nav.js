// 站点统一导航：在 <nav id="site-nav"></nav> 中注入链接（找不到时插入到 header 顶部）。
// 自带少量样式，避免依赖 styles.css 的改动。
(function () {
  const NAV_ITEMS = [
    { href: "index.html", label: "📰 Feed" },
    { href: "report.html", label: "📈 偏好报告" },
    { href: "stats.html", label: "📊 期刊统计" },
    { href: "journals.html", label: "📚 期刊管理" }
  ];

  const STYLE_ID = "site-nav-style";
  const NAV_CSS = `
.site-nav { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.site-nav__link { text-decoration: none; }
.site-nav__link[aria-current="page"] {
  background: #0f172a;
  color: #fff;
  border-color: #0f172a;
  box-shadow: 0 0 0 2px rgba(15, 23, 42, 0.15);
  pointer-events: none;
}
`;

  function currentPage() {
    const path = window.location.pathname || "";
    const last = path.split("/").pop();
    return last || "index.html";
  }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = NAV_CSS;
    document.head.appendChild(style);
  }

  function buildNav(container) {
    const page = currentPage();
    container.classList.add("site-nav");
    if (!container.getAttribute("aria-label")) {
      container.setAttribute("aria-label", "站点导航");
    }
    container.innerHTML = "";
    NAV_ITEMS.forEach((item) => {
      const link = document.createElement("a");
      link.className = "btn btn--secondary site-nav__link";
      link.href = item.href;
      link.textContent = item.label;
      if (item.href === page) {
        link.setAttribute("aria-current", "page");
      }
      container.appendChild(link);
    });
  }

  function init() {
    injectStyle();
    let container = document.getElementById("site-nav");
    if (!container) {
      container = document.createElement("nav");
      container.id = "site-nav";
      const header = document.querySelector("header");
      if (header) {
        header.insertBefore(container, header.firstChild);
      } else {
        document.body.insertBefore(container, document.body.firstChild);
      }
    }
    buildNav(container);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
