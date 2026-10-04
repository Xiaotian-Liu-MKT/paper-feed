const listEl = document.getElementById("journalList");
const searchInput = document.getElementById("searchInput");
const countLabel = document.getElementById("countLabel");
const statusText = document.getElementById("statusText");
const validationText = document.getElementById("validationText");
const importText = document.getElementById("importText");
const fileInput = document.getElementById("fileInput");
const groupList = document.getElementById("groupList");

const btnAdd = document.getElementById("btnAdd");
const btnSave = document.getElementById("btnSave");
const btnReload = document.getElementById("btnReload");
const btnExport = document.getElementById("btnExport");
const btnMerge = document.getElementById("btnMerge");
const btnReplace = document.getElementById("btnReplace");
const btnCopy = document.getElementById("btnCopy");
const btnImportOpen = document.getElementById("btnImportOpen");
const btnImportClose = document.getElementById("btnImportClose");
const importModal = document.getElementById("importModal");
const dirtyIndicator = document.getElementById("dirtyIndicator");

let journalId = 0;
let journals = [];
let filterText = "";
let filterTextLower = "";
// Unsaved edits on this page; drives the indicator and the beforeunload guard.
let dirty = false;
// Per-row「测试」results keyed by row id; cleared when that row's URL changes.
const testResults = new Map();

function setDirty(value) {
  dirty = Boolean(value);
  if (dirtyIndicator) dirtyIndicator.hidden = !dirty;
}

window.addEventListener("beforeunload", (event) => {
  if (!dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

function confirmDiscard(message) {
  return !dirty || window.confirm(message);
}

function setFilter(value) {
  filterText = value;
  filterTextLower = value.toLowerCase();
}

function createJournalItem(value, subject = "", name = "") {
  journalId += 1;
  return { id: journalId, value, subject, name };
}

function setStatus(message) {
  statusText.textContent = message;
}

// De-duplicates with the same normalisation as the catalog (catalogKey):
// utm_* parameters, trailing slashes and letter case do not make a new feed.
function normalizeList(items) {
  const cleaned = [];
  const seen = new Set();
  items.forEach((item) => {
    if (typeof item !== "string") return;
    const value = item.trim();
    const key = catalogKey(value);
    if (!value || seen.has(key)) return;
    cleaned.push(value);
    seen.add(key);
  });
  return cleaned;
}

function normalizeMeta(meta, values) {
  if (!meta || typeof meta !== "object") return {};
  const allowed = new Set(values || []);
  const cleaned = {};
  Object.entries(meta).forEach(([key, value]) => {
    if (!allowed.has(key)) return;
    if (typeof value === "string") {
      const subject = value.trim();
      if (subject) cleaned[key] = { subject };
      return;
    }
    if (!value || typeof value !== "object") return;
    const subject = typeof value.subject === "string" ? value.subject.trim() : "";
    const name = typeof value.name === "string" ? value.name.trim() : "";
    if (subject || name) cleaned[key] = { subject, name };
  });
  return cleaned;
}

function getValidationState(value) {
  if (!value) return "empty";
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "invalid";
    }
    return "valid";
  } catch (error) {
    return "invalid";
  }
}

// Returns the catalogKey()s that occur more than once.
function getDuplicateSet() {
  const counts = new Map();
  journals.forEach((item) => {
    const key = catalogKey(item.value.trim());
    if (!key) return;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  const duplicates = new Set();
  counts.forEach((count, value) => {
    if (count > 1) duplicates.add(value);
  });
  return duplicates;
}

function updateCount() {
  const total = journals.length;
  const values = journals.map((item) => item.value);
  const unique = normalizeList(values).length;
  const duplicates = getDuplicateSet().size;
  let invalid = 0;
  let empty = 0;
  values.forEach((value) => {
    const status = getValidationState(value.trim());
    if (status === "invalid") invalid += 1;
    if (status === "empty") empty += 1;
  });
  countLabel.textContent = `${unique} / ${total}`;
  validationText.textContent = `有效 ${unique}，重复 ${duplicates}，无效 ${invalid}，空行 ${empty}`;
}

function renderList() {
  listEl.innerHTML = "";
  const duplicates = getDuplicateSet();
  const filtered = journals.filter((item) => {
    if (!filterText) return true;
    if (filterText === "__invalid__") {
      const status = getValidationState(item.value.trim());
      return status === "invalid" || status === "empty";
    }
    if (filterText === "__uncategorized__") {
      const status = getValidationState(item.value.trim());
      return status === "valid" && !(item.subject || "").trim();
    }
    const value = item.value.toLowerCase();
    const subject = (item.subject || "").toLowerCase();
    const name = (item.name || "").toLowerCase();
    return value.includes(filterTextLower) || subject.includes(filterTextLower) || name.includes(filterTextLower);
  });

  if (!filtered.length) {
    const empty = document.createElement("p");
    empty.className = "panel-hint";
    empty.textContent = "暂无匹配记录，可以添加新行或导入列表。";
    listEl.appendChild(empty);
  } else {
    filtered.forEach((item) => {
      const row = document.createElement("div");
      row.className = "journal-row";
      const trimmed = item.value.trim();
      const validation = getValidationState(trimmed);
      if (validation === "invalid") row.classList.add("journal-row--invalid");

      const input = document.createElement("input");
      input.className = "journal-input";
      input.type = "text";
      input.value = item.value;
      input.placeholder = "https://...";
      input.dataset.id = String(item.id);
      input.dataset.field = "url";

      if (validation === "invalid") {
        input.classList.add("journal-input--invalid");
      } else if (validation === "empty") {
        input.classList.add("journal-input--empty");
      }

      const subject = document.createElement("input");
      subject.className = "journal-subject";
      subject.type = "text";
      subject.value = item.subject || "";
      subject.placeholder = "学科（可选）";
      subject.dataset.id = String(item.id);
      subject.dataset.field = "subject";

      const name = document.createElement("input");
      name.className = "journal-name";
      name.type = "text";
      name.value = item.name || "";
      name.placeholder = "期刊名称";
      name.dataset.id = String(item.id);
      name.dataset.field = "name";

      const badges = document.createElement("div");
      badges.className = "journal-badges";

      if (validation === "invalid") {
        const badge = document.createElement("span");
        badge.className = "badge badge--danger";
        badge.textContent = "无效";
        badge.title = "URL 必须以 http:// 或 https:// 开头";
        badges.appendChild(badge);
        input.setAttribute("aria-invalid", "true");
      }

      if (validation === "empty") {
        const badge = document.createElement("span");
        badge.className = "badge badge--muted";
        badge.textContent = "空";
        badges.appendChild(badge);
      }

      if (trimmed && duplicates.has(catalogKey(trimmed))) {
        const badge = document.createElement("span");
        badge.className = "badge badge--warn";
        badge.textContent = "重复";
        badges.appendChild(badge);
      }

      const test = document.createElement("button");
      test.className = "btn btn--secondary btn--small";
      test.type = "button";
      test.dataset.id = String(item.id);
      test.dataset.action = "test";
      test.textContent = "测试";
      test.title = "抓取一次该 RSS 源，检查能否解析";
      test.disabled = validation !== "valid";

      const remove = document.createElement("button");
      remove.className = "btn btn--danger btn--small";
      remove.type = "button";
      remove.dataset.id = String(item.id);
      remove.dataset.action = "delete";
      remove.textContent = "删除";
      remove.title = "从订阅列表移除（已入库论文不会被删除）";

      row.appendChild(name);
      row.appendChild(input);
      row.appendChild(subject);
      row.appendChild(badges);
      row.appendChild(test);
      row.appendChild(remove);

      const result = testResults.get(item.id);
      if (result) {
        const resultEl = document.createElement("p");
        resultEl.className = "journal-test-result";
        resultEl.dataset.state = result.state;
        resultEl.setAttribute("role", "status");
        resultEl.textContent = result.text;
        if (result.title) resultEl.title = result.title;
        row.appendChild(resultEl);
      }
      listEl.appendChild(row);
    });
  }

  updateCount();
  renderGroups();
}

function renderListPreserveFocus() {
  const active = document.activeElement;
  let activeId = null;
  let activeField = null;
  let selectionStart = null;
  let selectionEnd = null;
  let activeAction = null;
  if (active && active.classList && (active.classList.contains("journal-input") || active.classList.contains("journal-subject") || active.classList.contains("journal-name"))) {
    activeId = active.dataset.id;
    activeField = active.dataset.field;
    selectionStart = active.selectionStart;
    selectionEnd = active.selectionEnd;
  } else if (active && active.dataset && active.dataset.action && listEl.contains(active)) {
    activeId = active.dataset.id;
    activeAction = active.dataset.action;
  }
  renderList();
  if (activeId && activeAction) {
    const nextButton = listEl.querySelector(`button[data-id="${activeId}"][data-action="${activeAction}"]`);
    if (nextButton) nextButton.focus();
  }
  if (activeId && activeField) {
    const next = listEl.querySelector(`[data-id="${activeId}"][data-field="${activeField}"]`);
    if (next) {
      next.focus();
      if (selectionStart !== null && selectionEnd !== null) {
        next.setSelectionRange(selectionStart, selectionEnd);
      }
    }
  }
}

function getCurrentValues() {
  return normalizeList(journals.map((item) => item.value));
}

function getCurrentMeta(values) {
  const allowed = new Set(values || []);
  const result = {};
  journals.forEach((item) => {
    const url = item.value.trim();
    if (!url || !allowed.has(url)) return;
    const subject = (item.subject || "").trim();
    const name = (item.name || "").trim();
    if ((subject || name) && !result[url]) {
      const entry = {};
      if (subject) entry.subject = subject;
      if (name) entry.name = name;
      result[url] = entry;
    }
  });
  return result;
}

function applyList(values, meta = {}) {
  journals = values.map((value) => {
    const info = meta[value] || {};
    const subject = typeof info === "string" ? info : info.subject || "";
    const name = typeof info === "string" ? "" : info.name || "";
    return createJournalItem(value, subject, name);
  });
  renderList();
}

async function loadJournals() {
  try {
    setStatus("正在加载期刊列表...");
    const res = await fetch("/api/journals");
    const data = await res.json();
    const items = normalizeList(data.journals || []);
    const meta = normalizeMeta(data.meta || {}, items);
    testResults.clear();
    applyList(items, meta);
    setDirty(false);
    setStatus(`已加载 ${items.length} 条期刊。`);
    refreshCatalogSubscriptions();
  } catch (error) {
    setStatus("加载失败，请检查服务是否运行。");
  }
}

// 去除 utm_* 跟踪参数（后端也会处理，这里先在前端清理以便去重）。
function stripTrackingParams(value) {
  const trimmed = (value || "").trim();
  if (!trimmed || !/utm_/i.test(trimmed)) return trimmed;
  try {
    const url = new URL(trimmed);
    const keys = Array.from(url.searchParams.keys());
    let changed = false;
    keys.forEach((key) => {
      if (/^utm_/i.test(key)) {
        url.searchParams.delete(key);
        changed = true;
      }
    });
    if (!changed) return trimmed;
    let result = url.toString();
    if (!url.search && result.endsWith("?")) result = result.slice(0, -1);
    return result;
  } catch (error) {
    return trimmed;
  }
}

function stripTrackingFromList() {
  let changed = 0;
  journals.forEach((item) => {
    const cleaned = stripTrackingParams(item.value);
    if (cleaned !== item.value.trim()) {
      item.value = cleaned;
      changed += 1;
    }
  });
  return changed;
}

function countInvalidRows() {
  return journals.filter((item) => getValidationState(item.value.trim()) === "invalid").length;
}

async function saveJournals() {
  const invalid = countInvalidRows();
  if (invalid) {
    setStatus(`有 ${invalid} 条 URL 无效（必须以 http:// 或 https:// 开头），请修正或删除后再保存。可点击分组中的「无效」快速定位。`);
    return false;
  }
  const stripped = stripTrackingFromList();
  const values = getCurrentValues();
  const meta = getCurrentMeta(values);
  try {
    setStatus("正在保存...");
    const res = await fetch("/api/journals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ journals: values, meta }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status !== "ok") {
      throw new Error(data.message || `HTTP ${res.status}`);
    }
    const nextValues = normalizeList(data.journals || values);
    const nextMeta = normalizeMeta(data.meta || meta, nextValues);
    testResults.clear();
    applyList(nextValues, nextMeta);
    setDirty(false);
    const strippedNote = stripped ? `（已去除 ${stripped} 条链接中的 utm 跟踪参数）` : "";
    setStatus(`保存完成，共 ${nextValues.length} 条。${strippedNote}`);
    refreshCatalogSubscriptions();
    return true;
  } catch (error) {
    setStatus(`保存失败：${error.message || "请稍后重试"}。修改仍保留在页面上。`);
    return false;
  }
}

async function testJournal(id) {
  const item = journals.find((entry) => entry.id === id);
  if (!item) return;
  const url = item.value.trim();
  if (getValidationState(url) !== "valid") {
    testResults.set(id, { state: "error", text: "✗ URL 无效，必须以 http:// 或 https:// 开头。" });
    renderList();
    return;
  }
  testResults.set(id, { state: "pending", text: "正在测试该 RSS 源…" });
  renderListPreserveFocus();
  let result;
  try {
    const res = await fetch("/api/journals/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (res.status === 404 || res.status === 501) throw new Error("当前服务器不支持测试 RSS 源，请更新后端。");
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || data.message || `HTTP ${res.status}`);
    const titles = Array.isArray(data.latest_titles) ? data.latest_titles.filter(Boolean) : [];
    const feedTitle = data.feed_title ? `「${data.feed_title}」` : "";
    const latest = titles.length ? ` · 最新：${titles[0]}` : "";
    result = {
      state: "ok",
      text: `✓ 可用${feedTitle}，共 ${Number(data.entries) || 0} 条${latest}`,
      title: titles.length ? `最新条目：\n${titles.join("\n")}` : "",
    };
  } catch (error) {
    result = { state: "error", text: `✗ 测试失败：${error.message || "网络错误"}` };
  }
  // The row may have been edited or deleted while the request was running.
  const current = journals.find((entry) => entry.id === id);
  if (!current || current.value.trim() !== url) return;
  testResults.set(id, result);
  renderListPreserveFocus();
}

// ---------- 从目录添加 ----------
const catalogDetails = document.getElementById("catalogDetails");
const catalogGroups = document.getElementById("catalogGroups");
const catalogSearch = document.getElementById("catalogSearch");
const catalogCount = document.getElementById("catalogCount");
const btnCatalogAdd = document.getElementById("btnCatalogAdd");

const catalogState = {
  loaded: false,
  loading: false,
  unavailable: false,
  items: [],
  selected: new Set(),
  filter: "",
};

function catalogKey(url) {
  return stripTrackingParams(url || "").replace(/\/+$/, "").toLowerCase();
}

function getSubscribedKeys() {
  return new Set(journals.map((item) => catalogKey(item.value)).filter(Boolean));
}

function isCatalogItemSubscribed(item, subscribedKeys) {
  return Boolean(item.subscribed) || subscribedKeys.has(catalogKey(item.url));
}

async function loadCatalog() {
  if (catalogState.loaded || catalogState.loading || !catalogGroups) return;
  catalogState.loading = true;
  catalogGroups.innerHTML = '<p class="panel-hint">正在加载期刊目录...</p>';
  try {
    const res = await fetch("/api/journal_catalog?t=" + Date.now(), { cache: "no-store" });
    if (res.status === 404) {
      catalogState.unavailable = true;
      catalogGroups.innerHTML = '<p class="panel-hint">当前服务器版本不支持期刊目录，请更新后端后重试；也可以使用「导入 / 批量操作」手动添加。</p>';
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    catalogState.items = (Array.isArray(data.items) ? data.items : [])
      .filter((item) => item && typeof item.url === "string" && item.url.trim())
      .map((item) => ({
        name: (item.name || "").trim(),
        url: item.url.trim(),
        subject: (item.subject || "").trim() || "未分类",
        tags: Array.isArray(item.tags) ? item.tags.filter(Boolean) : [],
        subscribed: Boolean(item.subscribed),
      }));
    catalogState.loaded = true;
    renderCatalog();
  } catch (error) {
    catalogGroups.innerHTML = '<p class="panel-hint">期刊目录加载失败，请确认服务器运行中后重新展开。</p>';
  } finally {
    catalogState.loading = false;
  }
}

function catalogMatchesFilter(item) {
  if (!catalogState.filter) return true;
  const needle = catalogState.filter.toLowerCase();
  return [item.name, item.url, item.subject, ...item.tags].some((text) =>
    (text || "").toLowerCase().includes(needle)
  );
}

function updateCatalogFooter() {
  const count = catalogState.selected.size;
  if (btnCatalogAdd) {
    btnCatalogAdd.disabled = count === 0;
    btnCatalogAdd.textContent = count ? `添加所选（${count}）` : "添加所选";
  }
}

function renderCatalog() {
  if (!catalogGroups || !catalogState.loaded) return;
  const subscribedKeys = getSubscribedKeys();
  const visible = catalogState.items.filter(catalogMatchesFilter);
  const subscribedTotal = catalogState.items.filter((item) => isCatalogItemSubscribed(item, subscribedKeys)).length;
  if (catalogCount) {
    catalogCount.textContent = `目录 ${catalogState.items.length} 本 · 已订阅 ${subscribedTotal} 本`;
  }

  catalogGroups.innerHTML = "";
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "panel-hint";
    empty.textContent = catalogState.items.length ? "没有匹配的目录条目。" : "目录为空。";
    catalogGroups.appendChild(empty);
    updateCatalogFooter();
    return;
  }

  const groups = new Map();
  visible.forEach((item) => {
    if (!groups.has(item.subject)) groups.set(item.subject, []);
    groups.get(item.subject).push(item);
  });

  const fragment = document.createDocumentFragment();
  Array.from(groups.keys())
    .sort((a, b) => a.localeCompare(b, "zh-CN"))
    .forEach((subject) => {
      const items = groups.get(subject);
      const group = document.createElement("details");
      group.className = "catalog-group";
      if (catalogState.filter || groups.size <= 3) group.open = true;

      const summary = document.createElement("summary");
      const subscribedCount = items.filter((item) => isCatalogItemSubscribed(item, subscribedKeys)).length;
      summary.textContent = `${subject}（${items.length}，已订阅 ${subscribedCount}）`;
      group.appendChild(summary);

      items.forEach((item) => {
        const subscribed = isCatalogItemSubscribed(item, subscribedKeys);
        const key = catalogKey(item.url);
        const row = document.createElement("label");
        row.className = "catalog-item" + (subscribed ? " catalog-item--subscribed" : "");

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.dataset.key = key;
        checkbox.checked = subscribed || catalogState.selected.has(key);
        checkbox.disabled = subscribed;

        const name = document.createElement("span");
        name.className = "catalog-item__name";
        name.textContent = item.name || item.url;

        row.appendChild(checkbox);
        row.appendChild(name);

        if (subscribed) {
          const tag = document.createElement("span");
          tag.className = "catalog-tag catalog-tag--subscribed";
          tag.textContent = "已订阅";
          row.appendChild(tag);
        }
        item.tags.forEach((tagText) => {
          const tag = document.createElement("span");
          tag.className = "catalog-tag";
          tag.textContent = tagText;
          row.appendChild(tag);
        });

        const url = document.createElement("span");
        url.className = "catalog-item__url";
        url.textContent = item.url;
        row.appendChild(url);

        group.appendChild(row);
      });
      fragment.appendChild(group);
    });
  catalogGroups.appendChild(fragment);
  updateCatalogFooter();
}

function refreshCatalogSubscriptions() {
  if (!catalogState.loaded) return;
  const subscribedKeys = getSubscribedKeys();
  catalogState.items.forEach((item) => {
    if (subscribedKeys.has(catalogKey(item.url))) catalogState.selected.delete(catalogKey(item.url));
  });
  renderCatalog();
}

async function addSelectedFromCatalog() {
  if (!catalogState.selected.size) return;
  if (dirty && !window.confirm("订阅列表还有未保存的修改。添加所选期刊会立即保存整个列表，这些修改也会一并保存。继续吗？")) {
    return;
  }
  const subscribedKeys = getSubscribedKeys();
  const toAdd = catalogState.items.filter(
    (item) => catalogState.selected.has(catalogKey(item.url)) && !subscribedKeys.has(catalogKey(item.url))
  );
  if (!toAdd.length) {
    catalogState.selected.clear();
    renderCatalog();
    setStatus("所选期刊均已在订阅列表中。");
    return;
  }
  // 合并到现有列表（新条目放在末尾），再走原有的保存流程。
  const added = new Set();
  toAdd.forEach((item) => {
    const url = stripTrackingParams(item.url);
    const key = catalogKey(url);
    if (added.has(key)) return;
    added.add(key);
    journals.push(createJournalItem(url, item.subject === "未分类" ? "" : item.subject, item.name));
  });
  setDirty(true);
  renderList();
  if (btnCatalogAdd) btnCatalogAdd.disabled = true;
  const ok = await saveJournals();
  if (ok) {
    catalogState.selected.clear();
    refreshCatalogSubscriptions();
    setStatus(`已从目录添加 ${added.size} 本期刊并保存，共 ${journals.length} 条。`);
  } else {
    updateCatalogFooter();
  }
}

if (catalogDetails) {
  catalogDetails.addEventListener("toggle", () => {
    if (catalogDetails.open) loadCatalog();
  });
}
if (catalogGroups) {
  catalogGroups.addEventListener("change", (event) => {
    const target = event.target;
    if (!target || target.type !== "checkbox" || target.disabled) return;
    const key = target.dataset.key;
    if (!key) return;
    if (target.checked) catalogState.selected.add(key);
    else catalogState.selected.delete(key);
    updateCatalogFooter();
  });
}
if (catalogSearch) {
  catalogSearch.addEventListener("input", (event) => {
    catalogState.filter = event.target.value.trim();
    renderCatalog();
  });
}
if (btnCatalogAdd) {
  btnCatalogAdd.addEventListener("click", addSelectedFromCatalog);
}

function addRow() {
  if (filterText) {
    setFilter("");
    searchInput.value = "";
  }
  journals.unshift(createJournalItem(""));
  setDirty(true);
  renderList();
}

function exportFile() {
  const values = getCurrentValues();
  const meta = getCurrentMeta(values);
  const lines = values.map((value) => {
    const info = meta[value] || {};
    const subject = info.subject || "";
    const name = info.name || "";
    return [value, subject, name].join("\t");
  });
  const blob = new Blob([lines.join("\n") + "\n"], {
    type: "text/plain;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "journals.dat";
  link.click();
  URL.revokeObjectURL(url);
  setStatus(`已导出 ${values.length} 条期刊。`);
}

async function copyToClipboard() {
  const values = getCurrentValues();
  const meta = getCurrentMeta(values);
  const lines = values.map((value) => {
    const info = meta[value] || {};
    const subject = info.subject || "";
    const name = info.name || "";
    return [value, subject, name].join("\t");
  });
  try {
    await navigator.clipboard.writeText(lines.join("\n"));
    setStatus("已复制到剪贴板。");
  } catch (error) {
    setStatus("复制失败，请手动选择导出。");
  }
}

function parseImportText(text) {
  const entries = [];
  text.split(/\r?\n/).forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const parts = trimmed.split("\t");
    const url = (parts[0] || "").trim();
    const subject = (parts[1] || "").trim();
    const name = (parts[2] || "").trim();
    if (url) entries.push({ value: url, subject, name });
  });
  return entries;
}

function normalizeEntries(entries) {
  const cleaned = [];
  const seen = new Set();
  entries.forEach((entry) => {
    if (!entry || typeof entry.value !== "string") return;
    const value = entry.value.trim();
    if (!value || seen.has(catalogKey(value))) return;
    cleaned.push({
      value,
      subject: (entry.subject || "").trim(),
      name: (entry.name || "").trim(),
    });
    seen.add(catalogKey(value));
  });
  return cleaned;
}

function mergeImport() {
  const incoming = normalizeEntries(parseImportText(importText.value));
  if (!incoming.length) {
    setStatus("导入内容为空。");
    return;
  }
  const current = getCurrentValues();
  const mergedValues = normalizeList([...current, ...incoming.map((item) => item.value)]);
  const metaMap = getCurrentMeta(current);
  incoming.forEach((item) => {
    const subject = item.subject || "";
    const name = item.name || "";
    if (!subject && !name) return;
    if (!metaMap[item.value]) {
      metaMap[item.value] = {};
    }
    if (subject && !metaMap[item.value].subject) {
      metaMap[item.value].subject = subject;
    }
    if (name && !metaMap[item.value].name) {
      metaMap[item.value].name = name;
    }
  });
  applyList(mergedValues, normalizeMeta(metaMap, mergedValues));
  setDirty(true);
  const added = Math.max(0, mergedValues.length - current.length);
  setStatus(`合并完成，新增 ${added} 条，当前 ${mergedValues.length} 条。`);
}

function replaceImport() {
  const incoming = normalizeEntries(parseImportText(importText.value));
  if (!incoming.length) {
    setStatus("导入内容为空。");
    return;
  }
  if (!confirmDiscard("替换导入会用导入内容覆盖当前列表，当前未保存的修改将丢失。确定吗？")) {
    return;
  }
  const values = incoming.map((item) => item.value);
  const meta = {};
  incoming.forEach((item) => {
    const subject = item.subject || "";
    const name = item.name || "";
    if (subject || name) {
      meta[item.value] = { subject, name };
    }
  });
  testResults.clear();
  applyList(values, meta);
  setDirty(true);
  setStatus(`替换完成，当前 ${values.length} 条（尚未保存）。`);
}

function handleFileImport(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    importText.value = String(reader.result || "");
    setStatus("文件内容已载入，可选择合并或替换。");
  };
  reader.readAsText(file);
}

function buildGroups() {
  const groups = new Map();
  journals.forEach((item) => {
    const value = item.value.trim();
    const subject = (item.subject || "").trim();
    const status = getValidationState(value);
    if (status !== "valid") {
      groups.set("无效", (groups.get("无效") || 0) + 1);
      return;
    }
    const name = subject || "未分类";
    groups.set(name, (groups.get(name) || 0) + 1);
  });
  return groups;
}

function renderGroups() {
  groupList.innerHTML = "";
  const groups = buildGroups();
  const items = Array.from(groups.entries()).sort((a, b) => b[1] - a[1]);
  const allButton = document.createElement("button");
  allButton.type = "button";
  allButton.className = "group-btn";
  allButton.textContent = `全部 (${journals.length})`;
  allButton.dataset.filter = "";
  if (!filterText) {
    allButton.classList.add("is-active");
  }
  groupList.appendChild(allButton);

  items.forEach(([name, count]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "group-btn";
    const filterValue = name === "未分类" ? "__uncategorized__" : name;
    btn.dataset.filter = filterValue;
    if (name === "无效") btn.dataset.invalid = "true";
    btn.textContent = `${name} (${count})`;
    if (name === "无效" && filterText === "__invalid__") {
      btn.classList.add("is-active");
    } else if (name === "未分类" && filterText === "__uncategorized__") {
      btn.classList.add("is-active");
    } else if (name !== "无效" && name !== "未分类" && filterTextLower === name.toLowerCase()) {
      btn.classList.add("is-active");
    }
    groupList.appendChild(btn);
  });
}

listEl.addEventListener("input", (event) => {
  const target = event.target;
  if (target.classList.contains("journal-input")) {
    const id = Number(target.dataset.id);
    const item = journals.find((entry) => entry.id === id);
    if (item) item.value = target.value;
    testResults.delete(id);
    setDirty(true);
    renderListPreserveFocus();
  }
  if (target.classList.contains("journal-subject")) {
    const id = Number(target.dataset.id);
    const item = journals.find((entry) => entry.id === id);
    if (item) item.subject = target.value;
    setDirty(true);
    renderListPreserveFocus();
  }
  if (target.classList.contains("journal-name")) {
    const id = Number(target.dataset.id);
    const item = journals.find((entry) => entry.id === id);
    if (item) item.name = target.value;
    setDirty(true);
    renderListPreserveFocus();
  }
});

listEl.addEventListener("click", (event) => {
  const target = event.target.closest ? event.target.closest("button[data-action]") : null;
  if (!target) return;
  const id = Number(target.dataset.id);
  if (target.dataset.action === "test") {
    testJournal(id);
    return;
  }
  if (target.dataset.action === "delete") {
    const removed = journals.find((entry) => entry.id === id);
    journals = journals.filter((entry) => entry.id !== id);
    testResults.delete(id);
    if (removed && removed.value.trim()) setDirty(true);
    renderList();
    if (removed && removed.value.trim()) {
      const label = removed.name || removed.value.trim();
      setStatus(`已从列表移除「${label}」，保存后生效。已入库论文不会被删除。`);
    }
  }
});

groupList.addEventListener("click", (event) => {
  const target = event.target;
  if (!target.classList.contains("group-btn")) return;
  const invalid = target.dataset.invalid === "true";
  if (invalid) {
    setFilter("__invalid__");
    searchInput.value = "";
  } else {
    const value = target.dataset.filter || "";
    setFilter(value);
    searchInput.value = value === "__uncategorized__" ? "未分类" : value;
  }
  renderList();
});

searchInput.addEventListener("input", (event) => {
  setFilter(event.target.value.trim());
  renderList();
});

btnAdd.addEventListener("click", addRow);
btnSave.addEventListener("click", saveJournals);
btnReload.addEventListener("click", () => {
  if (!confirmDiscard("有未保存的修改，重新加载会丢弃这些修改。确定吗？")) return;
  loadJournals();
});
btnExport.addEventListener("click", exportFile);
btnMerge.addEventListener("click", mergeImport);
btnReplace.addEventListener("click", replaceImport);
btnCopy.addEventListener("click", copyToClipboard);
fileInput.addEventListener("change", handleFileImport);
btnImportOpen.addEventListener("click", () => {
  if (importModal) importModal.showModal();
});
btnImportClose.addEventListener("click", () => {
  if (importModal) importModal.close();
});
if (importModal) {
  importModal.addEventListener("cancel", (event) => {
    event.preventDefault();
    importModal.close();
  });
}

loadJournals();
