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

let journalId = 0;
let journals = [];
let filterText = "";
let filterTextLower = "";

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

function normalizeList(items) {
  const cleaned = [];
  const seen = new Set();
  items.forEach((item) => {
    if (typeof item !== "string") return;
    const value = item.trim();
    if (!value || seen.has(value)) return;
    cleaned.push(value);
    seen.add(value);
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

function getDuplicateSet() {
  const counts = new Map();
  journals.forEach((item) => {
    const value = item.value.trim();
    if (!value) return;
    counts.set(value, (counts.get(value) || 0) + 1);
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
        badges.appendChild(badge);
      }

      if (validation === "empty") {
        const badge = document.createElement("span");
        badge.className = "badge badge--muted";
        badge.textContent = "空";
        badges.appendChild(badge);
      }

      if (trimmed && duplicates.has(trimmed)) {
        const badge = document.createElement("span");
        badge.className = "badge badge--warn";
        badge.textContent = "重复";
        badges.appendChild(badge);
      }

      const remove = document.createElement("button");
      remove.className = "btn btn--danger btn--small";
      remove.type = "button";
      remove.dataset.id = String(item.id);
      remove.textContent = "删除";

      row.appendChild(name);
      row.appendChild(input);
      row.appendChild(subject);
      row.appendChild(badges);
      row.appendChild(remove);
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
  if (active && active.classList && (active.classList.contains("journal-input") || active.classList.contains("journal-subject") || active.classList.contains("journal-name"))) {
    activeId = active.dataset.id;
    activeField = active.dataset.field;
    selectionStart = active.selectionStart;
    selectionEnd = active.selectionEnd;
  }
  renderList();
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
    applyList(items, meta);
    setStatus(`已加载 ${items.length} 条期刊。`);
  } catch (error) {
    setStatus("加载失败，请检查服务是否运行。");
  }
}

async function saveJournals() {
  const values = getCurrentValues();
  const meta = getCurrentMeta(values);
  try {
    setStatus("正在保存...");
    const res = await fetch("/api/journals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ journals: values, meta }),
    });
    const data = await res.json();
    if (data.status !== "ok") {
      throw new Error(data.message || "保存失败");
    }
    const nextValues = normalizeList(data.journals || values);
    const nextMeta = normalizeMeta(data.meta || meta, nextValues);
    applyList(nextValues, nextMeta);
    setStatus(`保存完成，共 ${values.length} 条。`);
  } catch (error) {
    setStatus("保存失败，请稍后重试。");
  }
}

function addRow() {
  if (filterText) {
    setFilter("");
    searchInput.value = "";
  }
  journals.unshift(createJournalItem(""));
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
    if (!value || seen.has(value)) return;
    cleaned.push({
      value,
      subject: (entry.subject || "").trim(),
      name: (entry.name || "").trim(),
    });
    seen.add(value);
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
  const added = Math.max(0, mergedValues.length - current.length);
  setStatus(`合并完成，新增 ${added} 条，当前 ${mergedValues.length} 条。`);
}

function replaceImport() {
  const incoming = normalizeEntries(parseImportText(importText.value));
  if (!incoming.length) {
    setStatus("导入内容为空。");
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
  applyList(values, meta);
  setStatus(`替换完成，当前 ${values.length} 条。`);
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
    renderListPreserveFocus();
  }
  if (target.classList.contains("journal-subject")) {
    const id = Number(target.dataset.id);
    const item = journals.find((entry) => entry.id === id);
    if (item) item.subject = target.value;
    renderListPreserveFocus();
  }
  if (target.classList.contains("journal-name")) {
    const id = Number(target.dataset.id);
    const item = journals.find((entry) => entry.id === id);
    if (item) item.name = target.value;
    renderListPreserveFocus();
  }
});

listEl.addEventListener("click", (event) => {
  const target = event.target;
  if (target.classList.contains("btn--danger")) {
    const id = Number(target.dataset.id);
    journals = journals.filter((entry) => entry.id !== id);
    renderList();
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
btnReload.addEventListener("click", loadJournals);
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
