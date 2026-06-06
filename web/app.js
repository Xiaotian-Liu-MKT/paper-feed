const state = {
  items: [],
  filtered: [],
  keywords: [],
  interactions: { favorites: [], archived: [], hidden: [] },
  interactionSets: { favorites: new Set(), archived: new Set(), hidden: new Set() },
  filterMode: 'all', // 'all' | 'favorites' | 'archived'
  inboxViewMode: "swipe", // 'swipe' | 'list'
  swipeIndex: 0,
  swipeBusy: false,
  undoStack: [],
  listRenderLimit: 80,
  preset: "",
  focusTopics: [],
  categories: {
    methods: [],
    topics: [],
    theories: [],
    contexts: [],
    subjects: []
  }
};

const elements = {
  list: document.getElementById("list"),
  countLabel: document.getElementById("countLabel"),
  generatedAt: document.getElementById("generatedAt"),
  modeEyebrow: document.getElementById("modeEyebrow"),
  modeTitle: document.getElementById("modeTitle"),
  modeDescription: document.getElementById("modeDescription"),
  insightChips: document.getElementById("insightChips"),
  heroInboxCount: document.getElementById("heroInboxCount"),
  heroFavoritesCount: document.getElementById("heroFavoritesCount"),
  heroArchivedCount: document.getElementById("heroArchivedCount"),
  heroVisibleCount: document.getElementById("heroVisibleCount"),
  heroVisibleNote: document.getElementById("heroVisibleNote"),
  searchInput: document.getElementById("searchInput"),
  journalSelect: document.getElementById("journalSelect"),
  filterMethod: document.getElementById("filterMethod"),
  filterTopic: document.getElementById("filterTopic"),
  filterMethodMode: document.getElementById("filterMethodMode"),
  filterTopicMode: document.getElementById("filterTopicMode"),
  filterPreset: document.getElementById("filterPreset"),
  fromDate: document.getElementById("fromDate"),
  toDate: document.getElementById("toDate"),
  sortSelect: document.getElementById("sortSelect"),
  summaryToggle: document.getElementById("summaryToggle"),
  btnExportFavorites: document.getElementById("btnExportFavorites"),
  inboxViewToggle: document.getElementById("inboxViewToggle"),
  cardTemplate: document.getElementById("cardTemplate"),
  topicCloud: document.getElementById("topicCloud"),
  topicCloudWrap: document.getElementById("topicCloudWrap")
};

const formatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "2-digit"
});

let undoTimeoutId = null;
let currentClassificationItem = null;
const LIST_INITIAL_RENDER_LIMIT = 80;
const LIST_RENDER_STEP = 80;
const UNDO_BAR_TIMEOUT_MS = 10000;
const MAX_UNDO_STACK_SIZE = 100;

// --- Interaction Logic ---

function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

function syncInteractionSets() {
  state.interactionSets = {
    favorites: new Set(state.interactions.favorites || []),
    archived: new Set(state.interactions.archived || []),
    hidden: new Set(state.interactions.hidden || [])
  };
}

function normalizeInteractions() {
  const favorites = ensureArray(state.interactions.favorites);
  const archived = ensureArray(state.interactions.archived);
  const hidden = ensureArray(state.interactions.hidden);

  const hiddenSet = new Set(hidden);
  
  // Prioritize Favorites: If an item is in both Favorites and Archived, keep it in Favorites.
  // This prevents "lost" favorites if data is messy.
  const favoritesSet = new Set(favorites.filter((id) => !hiddenSet.has(id)));
  const archivedSet = new Set(
    archived.filter((id) => !hiddenSet.has(id) && !favoritesSet.has(id))
  );

  state.interactions = {
    favorites: Array.from(favoritesSet),
    archived: Array.from(archivedSet),
    hidden: Array.from(hiddenSet)
  };
  syncInteractionSets();
}

async function loadInteractions() {
  try {
    const res = await fetch("/api/interactions?t=" + Date.now(), {
      cache: 'no-store'
    });
    if (res.ok) {
      state.interactions = await res.json();
      normalizeInteractions();
    }
  } catch (e) {
    console.warn("Failed to load interactions", e);
  }
}

async function saveInteraction(id, action) {
  try {
    await fetch("/api/interactions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, action })
    });
  } catch (e) {
    console.error("Failed to save interaction", e);
  }
}

function addUnique(list, id) {
  if (!list.includes(id)) {
    list.push(id);
  }
}

function removeValue(list, id) {
  return list.filter((value) => value !== id);
}

function applyInteractionToState(id, action) {
  normalizeInteractions();

  if (action === "like") {
    addUnique(state.interactions.favorites, id);
    state.interactions.hidden = removeValue(state.interactions.hidden, id);
    state.interactions.archived = removeValue(state.interactions.archived, id);
  } else if (action === "unlike") {
    state.interactions.favorites = removeValue(state.interactions.favorites, id);
  } else if (action === "archive") {
    addUnique(state.interactions.archived, id);
    state.interactions.favorites = removeValue(state.interactions.favorites, id);
    state.interactions.hidden = removeValue(state.interactions.hidden, id);
  } else if (action === "unarchive") {
    state.interactions.archived = removeValue(state.interactions.archived, id);
  } else if (action === "restore") {
    addUnique(state.interactions.favorites, id);
    state.interactions.archived = removeValue(state.interactions.archived, id);
    state.interactions.hidden = removeValue(state.interactions.hidden, id);
  } else if (action === "hide") {
    addUnique(state.interactions.hidden, id);
    state.interactions.favorites = removeValue(state.interactions.favorites, id);
    state.interactions.archived = removeValue(state.interactions.archived, id);
  } else if (action === "unhide") {
    state.interactions.hidden = removeValue(state.interactions.hidden, id);
  }

  syncInteractionSets();
}

function getUndoAction(action) {
  if (action === "like") return "unlike";
  if (action === "hide") return "unhide";
  return "";
}

function getInteractionMessage(action) {
  if (action === "like") return "已收藏文章";
  if (action === "hide") return "已跳过文章";
  return "已更新文章";
}

function shouldUseSwipeDeck() {
  return state.filterMode === "all" && state.inboxViewMode === "swipe";
}

function shouldLimitListRender() {
  return state.filterMode === "all" && state.inboxViewMode === "list";
}

function refreshAfterInteraction(resetSwipeIndex = false) {
  applyFilters({ resetSwipeIndex });
}

function clearUndoTimer() {
  if (undoTimeoutId) {
    clearTimeout(undoTimeoutId);
    undoTimeoutId = null;
  }
}

function clearUndoBar({ clearStack = false } = {}) {
  clearUndoTimer();
  if (clearStack) {
    state.undoStack = [];
  }
  const undoContainer = document.getElementById('undoContainer');
  if (!undoContainer) return;
  undoContainer.textContent = '';
}

function getUndoBarMessage(record) {
  const message = getInteractionMessage(record.action);
  const count = state.undoStack.length;
  return count > 1 ? `${message} · ${count} 项可撤销` : message;
}

function renderUndoBar() {
  const undoContainer = document.getElementById('undoContainer');
  if (!undoContainer) return;

  undoContainer.textContent = '';

  const record = state.undoStack[state.undoStack.length - 1];
  if (!record) return;

  const undoBar = document.createElement('div');
  undoBar.className = 'undo-bar';

  const span = document.createElement('span');
  span.textContent = getUndoBarMessage(record);

  const undoBtn = document.createElement('button');
  undoBtn.className = 'undo-btn';
  undoBtn.type = "button";
  undoBtn.textContent = state.undoStack.length > 1 ? `撤销 (${state.undoStack.length})` : '撤销';

  undoBar.appendChild(span);
  undoBar.appendChild(undoBtn);
  undoContainer.appendChild(undoBar);

  clearUndoTimer();
  undoTimeoutId = setTimeout(() => {
    clearUndoBar({ clearStack: true });
  }, UNDO_BAR_TIMEOUT_MS);

  undoBtn.onclick = () => {
    undoLastInteraction();
  };
}

function restoreCurrentCard(card) {
  if (card) {
    card.classList.remove('hidden');
  }
}

function getRenderedLogicalCount() {
  if (shouldLimitListRender()) {
    return Math.min(state.listRenderLimit, state.filtered.length);
  }
  return state.filtered.length;
}

function removeFilteredItem(id, preferredIndex = state.swipeIndex) {
  let index = state.filtered.findIndex((item) => item.link === id);
  if (index < 0 && preferredIndex >= 0 && preferredIndex < state.filtered.length) {
    index = preferredIndex;
  }
  if (index >= 0) {
    state.filtered.splice(index, 1);
  }
  if (state.swipeIndex >= state.filtered.length) {
    state.swipeIndex = Math.max(0, state.filtered.length - 1);
  }
}

function insertFilteredItem(item, index) {
  if (!item || state.filtered.some((entry) => entry.link === item.link)) return;
  const safeIndex = Math.max(0, Math.min(index, state.filtered.length));
  state.filtered.splice(safeIndex, 0, item);
  state.swipeIndex = safeIndex;
}

function recordUndoableInteraction(id, action, card = null, undoContext = null) {
  const undoAction = getUndoAction(action);
  if (!undoAction) return;

  state.undoStack.push({ id, action, undoAction, card, undoContext });
  if (state.undoStack.length > MAX_UNDO_STACK_SIZE) {
    state.undoStack.shift();
  }
  renderUndoBar();
}

function performPaperAction(id, action, options = {}) {
  const {
    card = null,
    hideCard = true,
    undoable = false,
    refresh = false,
    resetSwipeIndex = false,
    updateCounts = true,
    undoContext = null
  } = options;

  applyInteractionToState(id, action);
  saveInteraction(id, action);

  if (hideCard && card) {
    card.classList.add('hidden');
  }

  if (undoable) {
    recordUndoableInteraction(id, action, card, undoContext);
  }

  if (refresh) {
    refreshAfterInteraction(resetSwipeIndex);
  } else if (updateCounts) {
    updateFilterCounts();
  }
}

function toggleLike(id, btnElement) {
  const isLiked = state.interactionSets.favorites.has(id);
  const action = isLiked ? 'unlike' : 'like';
  const card = btnElement.closest('.card');

  performPaperAction(id, action, {
    card,
    undoable: action === "like",
    refresh: false
  });

  // Update button's state first (avoid re-rendering 1000 elements!)
  if (btnElement) {
    if (isLiked) {
      btnElement.classList.remove('liked');
      btnElement.title = "收藏";
      btnElement.setAttribute("aria-label", "收藏");
      setActionButtonContent(btnElement, "🤍", "收藏");
    } else {
      btnElement.classList.add('liked');
      btnElement.title = "取消收藏";
      btnElement.setAttribute("aria-label", "取消收藏");
      setActionButtonContent(btnElement, "❤️", "取消收藏");
    }
  }
}

function toggleHide(id, btnElement) {
  const card = btnElement.closest('.card');
  if (!card) return;

  requestAnimationFrame(() => {
    performPaperAction(id, "hide", {
      card,
      undoable: true,
      refresh: false
    });
  });
}

function toggleArchive(id, btnElement) {
  const isArchived = state.interactionSets.archived.has(id);
  const action = isArchived ? "unarchive" : "archive";
  const card = btnElement.closest(".card");

  performPaperAction(id, action, { card });
}

function restoreFromArchive(id, btnElement) {
  const card = btnElement.closest(".card");
  performPaperAction(id, "restore", { card });
}

// --- End Interaction Logic ---

function normalize(text) {
  return (text || "").toLowerCase();
}

function formatDate(date) {
  if (!date || Number.isNaN(date.getTime())) {
    return "日期未知";
  }
  return formatter.format(date);
}

function setStatus(text) {
  elements.countLabel.textContent = text;
}

function normalizeLabelEntries(rawEntries) {
  const entries = [];
  if (Array.isArray(rawEntries)) {
    rawEntries.forEach((entry) => {
      if (typeof entry === "string") {
        entries.push({ name: entry, confidence: 0.6 });
      } else if (entry && typeof entry === "object" && entry.name) {
        entries.push({
          name: entry.name,
          confidence: Number.isFinite(entry.confidence) ? entry.confidence : 0.6
        });
      }
    });
  } else if (typeof rawEntries === "string" && rawEntries.trim()) {
    entries.push({ name: rawEntries.trim(), confidence: 0.6 });
  }
  entries.sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  return entries;
}

function getLabelNames(entries, fallback) {
  if (Array.isArray(entries) && entries.length) {
    return entries.map((entry) => entry.name).filter(Boolean);
  }
  if (fallback) {
    return [fallback];
  }
  return [];
}

function getSelectedOptions(selectEl) {
  if (!selectEl) return [];
  return Array.from(selectEl.selectedOptions).map((option) => option.value).filter(Boolean);
}

function cacheMultiSelectState(selectEl) {
  if (!selectEl || selectEl.tagName !== "SELECT" || !selectEl.multiple) return;
  const selected = Array.from(selectEl.selectedOptions).map((option) => option.value);
  selectEl.dataset.prevSelected = JSON.stringify(selected);
}

function getPreviousMultiSelectValues(selectEl) {
  if (!selectEl || !selectEl.dataset.prevSelected) return [];
  try {
    const parsed = JSON.parse(selectEl.dataset.prevSelected);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    return [];
  }
}

function normalizeMultiSelectAll(selectEl) {
  if (!selectEl || selectEl.tagName !== "SELECT" || !selectEl.multiple) return;
  const options = Array.from(selectEl.options);
  const allOption = options.find((option) => option.value === "");
  if (!allOption) return;

  const previousSelected = getPreviousMultiSelectValues(selectEl);
  const prevHadAll = previousSelected.includes("");

  const selectedValues = options.filter((option) => option.selected).map((option) => option.value);
  const selectedOthers = selectedValues.filter((value) => value !== "");
  const hasAll = selectedValues.includes("");

  if (hasAll && !prevHadAll) {
    options.forEach((option) => {
      option.selected = option.value === "";
    });
    cacheMultiSelectState(selectEl);
    return;
  }

  if (selectedOthers.length === 0) {
    allOption.selected = true;
    options.forEach((option) => {
      if (option.value !== "") option.selected = false;
    });
    cacheMultiSelectState(selectEl);
    return;
  }

  if (hasAll) {
    allOption.selected = false;
  }
  cacheMultiSelectState(selectEl);
}

function getSelectedFilterValues(container) {
  if (!container) return [];
  if (container.tagName === "SELECT") {
    return getSelectedOptions(container);
  }
  return Array.from(container.querySelectorAll("input[type='checkbox']:checked"))
    .map((input) => input.value)
    .filter(Boolean);
}

function setFilterSelections(container, values) {
  if (!container) return;
  if (container.tagName === "SELECT") {
    if (!container.multiple) {
      container.value = values && values.length ? values[0] : "";
      return;
    }
    const selected = new Set(values || []);
    let hasSelection = selected.size > 0;
    Array.from(container.options).forEach((option) => {
      option.selected = selected.has(option.value);
    });
    if (!hasSelection) {
      const allOption = Array.from(container.options).find((option) => option.value === "");
      if (allOption) {
        allOption.selected = true;
      }
    } else {
      normalizeMultiSelectAll(container);
    }
    return;
  }
  const selected = new Set(values);
  container.querySelectorAll("input[type='checkbox']").forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

function getCategoryMap(type) {
  const list = type === "method" ? state.categories.methods : state.categories.topics;
  const map = {};
  list.forEach((item) => {
    if (!item || !item.name) return;
    map[item.name] = item;
  });
  return map;
}

function renderTopicCloud(items) {
  if (!elements.topicCloud) return;
  elements.topicCloud.innerHTML = "";
  if (!items || !items.length) {
    elements.topicCloud.textContent = "暂无主题数据。";
    return;
  }

  const counts = new Map();
  items.forEach((item) => {
    (item.topicLabels || []).forEach((label) => {
      counts.set(label, (counts.get(label) || 0) + 1);
    });
  });

  const sorted = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 24);
  if (!sorted.length) {
    elements.topicCloud.textContent = "暂无主题数据。";
    return;
  }

  const max = Math.max(...sorted.map((entry) => entry[1]));
  const fragment = document.createDocumentFragment();
  sorted.forEach(([label, count]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "topic-chip";
    btn.textContent = label;
    const scale = 0.8 + (count / max) * 0.6;
    btn.style.fontSize = `${scale}rem`;
    btn.onclick = () => {
      if (!elements.filterTopic) return;
      setFilterSelections(elements.filterTopic, [label]);
      applyFilters();
    };
    fragment.appendChild(btn);
  });
  elements.topicCloud.appendChild(fragment);
}

function updateTopicCloudVisibility() {
  if (!elements.topicCloudWrap) return;
  const shouldShow = state.filterMode === "favorites";
  elements.topicCloudWrap.classList.toggle("is-hidden", !shouldShow);
  if (!shouldShow && elements.topicCloud) {
    elements.topicCloud.textContent = "";
  }
}

function computeFocusTopics() {
  const favorites = new Set([
    ...state.interactionSets.favorites,
    ...state.interactionSets.archived
  ]);
  const counter = new Map();
  state.items.forEach((item) => {
    if (!favorites.has(item.link)) return;
    (item.topicLabels || []).forEach((topic) => {
      counter.set(topic, (counter.get(topic) || 0) + 1);
    });
  });
  return Array.from(counter.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([topic]) => topic);
}
function getBadgeColor(type, value) {
  const defaults = {
    method: {
      'Experiment': '#dbeafe|#1e40af',
      'Archival': '#f3e8ff|#6b21a8',
      'Theoretical': '#ffedd5|#9a3412',
      'Review': '#d1fae5|#065f46',
      'Qualitative': '#fce7f3|#9d174d'
    },
    topic: {
      'Other Marketing': '#f3f4f6|#6b7280'
    }
  };

  const categoryMap = getCategoryMap(type);
  const entry = categoryMap[value];
  if (entry && entry.color && entry.text) {
    return `background-color: ${entry.color}; color: ${entry.text};`;
  }
  if (defaults[type] && defaults[type][value]) {
    const [bg, color] = defaults[type][value].split('|');
    return `background-color: ${bg}; color: ${color};`;
  }
  return 'background-color: #f3f4f6; color: #4b5563;';
}

function appendBadge(container, type, entry, opts = {}) {
  if (!entry || !entry.name) return;
  if (entry.name === "Other") return;
  const span = document.createElement("span");
  span.className = "meta-badge";
  span.textContent = entry.name;
  span.style.cssText = getBadgeColor(type, entry.name);
  if (entry.confidence != null && entry.confidence < 0.7) {
    span.classList.add("meta-badge--low");
  }
  if (opts.title) {
    span.title = opts.title;
  } else if (entry.confidence != null) {
    span.title = `${entry.name} · ${Math.round(entry.confidence * 100)}%`;
  }
  container.appendChild(span);
}

function appendTagBadge(container, label) {
  if (!label) return;
  const span = document.createElement("span");
  span.className = "meta-badge meta-badge--tag";
  span.textContent = label;
  container.appendChild(span);
}

function getVisibleRenderedCount() {
  if (!elements.list) return state.filtered.length;
  const cards = Array.from(elements.list.querySelectorAll(".card"));
  if (!cards.length) return state.filtered.length;
  return cards.filter((card) => !card.classList.contains("hidden")).length;
}

function updateStatusLabel(counts, visibleCount) {
  if (state.filterMode === "favorites") {
    setStatus(`收藏夹中显示 ${visibleCount} 篇，共收藏 ${counts.favorites} 篇`);
  } else if (state.filterMode === "archived") {
    setStatus(`归档区中显示 ${visibleCount} 篇，共归档 ${counts.archived} 篇`);
  } else {
    setStatus(`收件箱中显示 ${visibleCount} 篇，待处理总数 ${counts.inbox} 篇`);
  }
}

function updateFilterCounts(visibleCountOverride = null) {
  const counts = getCollectionCounts();
  const visibleCount = Number.isFinite(visibleCountOverride)
    ? visibleCountOverride
    : getVisibleRenderedCount();

  const elInbox = document.getElementById("countInbox");
  const elFav = document.getElementById("countFavorites");
  const elArch = document.getElementById("countArchived");

  if (elInbox) elInbox.textContent = counts.inbox > 0 ? counts.inbox : "";
  if (elFav) elFav.textContent = counts.favorites > 0 ? counts.favorites : "";
  if (elArch) elArch.textContent = counts.archived > 0 ? counts.archived : "";

  updateStatusLabel(counts, visibleCount);
  renderWorkflowSummary(visibleCount);
}

function getCollectionCounts() {
  const { favorites, archived, hidden } = state.interactionSets;

  let inboxCount = 0;
  state.items.forEach((item) => {
    if (!favorites.has(item.link) && !archived.has(item.link) && !hidden.has(item.link)) {
      inboxCount++;
    }
  });

  return {
    inbox: inboxCount,
    favorites: favorites.size,
    archived: archived.size,
    hidden: hidden.size
  };
}

function collectTopLabels(items, key, limit = 3) {
  const counts = new Map();
  items.forEach((item) => {
    const values = Array.isArray(item[key]) ? item[key] : [];
    values.forEach((value) => {
      if (!value) return;
      counts.set(value, (counts.get(value) || 0) + 1);
    });
  });
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([label]) => label);
}

function renderInsightChips(chips) {
  if (!elements.insightChips) return;
  elements.insightChips.innerHTML = "";
  const fragment = document.createDocumentFragment();
  chips.forEach((chip) => {
    const node = document.createElement("span");
    node.className = `insight-chip insight-chip--${chip.tone || "muted"}`;
    node.textContent = chip.text;
    fragment.appendChild(node);
  });
  elements.insightChips.appendChild(fragment);
}

function setActionButtonContent(button, icon, label) {
  button.replaceChildren();

  const iconNode = document.createElement("span");
  iconNode.className = "action-btn__icon";
  iconNode.textContent = icon;

  const labelNode = document.createElement("span");
  labelNode.className = "action-btn__label";
  labelNode.textContent = label;

  button.appendChild(iconNode);
  button.appendChild(labelNode);
}

function configureActionButton(button, options) {
  const {
    icon,
    label,
    title,
    variant = "",
    isActive = false
  } = options;

  button.type = "button";
  button.className = ["action-btn", variant, isActive ? "liked" : ""]
    .filter(Boolean)
    .join(" ");
  button.title = title || label;
  button.setAttribute("aria-label", title || label);
  setActionButtonContent(button, icon, label);
}

function setModeActionVisibility() {
  const isFavorites = state.filterMode === "favorites";
  if (elements.inboxViewToggle) {
    elements.inboxViewToggle.style.display = state.filterMode === "all" ? "inline-flex" : "none";
  }
  if (btnSummarizeFavorites) {
    btnSummarizeFavorites.style.display = isFavorites ? "inline-flex" : "none";
  }
  if (btnExportFavorites) {
    btnExportFavorites.style.display = isFavorites ? "inline-flex" : "none";
  }
}

function renderWorkflowSummary(visibleCountOverride = null) {
  const counts = getCollectionCounts();
  const visibleCount = Number.isFinite(visibleCountOverride)
    ? visibleCountOverride
    : state.filtered.length;
  const topTopics = collectTopLabels(state.filtered, "topicLabels", 3);
  const topMethods = collectTopLabels(state.filtered, "methodLabels", 2);

  if (elements.heroInboxCount) elements.heroInboxCount.textContent = counts.inbox;
  if (elements.heroFavoritesCount) elements.heroFavoritesCount.textContent = counts.favorites;
  if (elements.heroArchivedCount) elements.heroArchivedCount.textContent = counts.archived;
  if (elements.heroVisibleCount) elements.heroVisibleCount.textContent = visibleCount;

  let eyebrow = "今日模式";
  let title = "待筛选收件箱";
  let description = "优先处理新论文，看到有意思的先收藏，剩下的快速跳过。";
  let visibleNote = "根据当前筛选显示";
  const chips = [];

  if (state.filterMode === "favorites") {
    eyebrow = "收藏工作台";
    title = "把值得追踪的论文留在这里";
    description = "收藏夹适合二次筛选、生成 AI 总结，并在准备好后导出为 RIS。";
    visibleNote = "收藏夹当前结果";
    chips.push({ tone: "accent", text: `已收藏 ${counts.favorites} 篇` });
    chips.push({ tone: "muted", text: "下一步：导出 RIS 或继续生成 AI 总结" });
  } else if (state.filterMode === "archived") {
    eyebrow = "归档回看";
    title = "这些论文暂时离开主视野";
    description = "归档区适合回头复查边界案例，不会干扰你的日常收件箱。";
    visibleNote = "归档区当前结果";
    chips.push({ tone: "accent", text: `已归档 ${counts.archived} 篇` });
    chips.push({ tone: "muted", text: "可随时恢复到收藏夹或收件箱" });
  } else {
    chips.push({ tone: "accent", text: `待处理 ${counts.inbox} 篇` });
    if (state.inboxViewMode === "swipe") {
      description = "像处理卡片一样逐篇判断：右键收藏，左键跳过，必要时随时撤销。";
      visibleNote = "当前刷卡队列";
      chips.push({ tone: "muted", text: "键盘：← 跳过，→ 收藏，Z 撤销" });
    } else {
      chips.push({ tone: "muted", text: "操作建议：❤️ 收藏，❌ 跳过" });
    }
  }

  if (topTopics.length) {
    chips.push({ tone: "muted", text: `高频主题：${topTopics.join(" / ")}` });
  }
  if (topMethods.length && state.filterMode !== "favorites") {
    chips.push({ tone: "muted", text: `方法集中在：${topMethods.join(" / ")}` });
  }

  if (elements.modeEyebrow) elements.modeEyebrow.textContent = eyebrow;
  if (elements.modeTitle) elements.modeTitle.textContent = title;
  if (elements.modeDescription) elements.modeDescription.textContent = description;
  if (elements.heroVisibleNote) elements.heroVisibleNote.textContent = visibleNote;

  renderInsightChips(chips);
  setModeActionVisibility();
}

function renderAbstractBlock(container, item, highlightTerms) {
  const sourceBadge = {
    crossref: { emoji: "📚", text: "Crossref", color: "#2563eb" },
    semantic_scholar: { emoji: "🔬", text: "Semantic Scholar", color: "#7c3aed" },
    gpt_generated: { emoji: "🤖", text: "AI 生成", color: "#ea580c" },
    gpt_summarized: { emoji: "✨", text: "AI 总结", color: "#d97706" },
    user_provided: { emoji: "✏️", text: "用户补充", color: "#059669" }
  };

  const source = sourceBadge[item.abstract_source] || { emoji: "📄", text: "摘要", color: "#64748b" };
  const head = document.createElement("div");
  head.className = "card__abstract-head";

  const badge = document.createElement("span");
  badge.className = "card__abstract-badge";
  badge.style.backgroundColor = source.color;
  badge.textContent = `${source.emoji} ${source.text}`;

  const caption = document.createElement("span");
  caption.className = "card__abstract-caption";
  caption.textContent = item.abstract_source === "gpt_generated" ? "基于标题预测的方向感摘要" : "便于快速判断是否值得继续读";

  const copy = document.createElement("div");
  copy.className = "card__abstract-copy";
  copy.style.borderLeftColor = source.color;
  copy.innerHTML = highlightText(item.abstract, highlightTerms);

  head.appendChild(badge);
  head.appendChild(caption);
  container.replaceChildren(head, copy);
  container.style.display = "block";
}

function setupAbstractEditor(item, editArea, textarea, btnSave, btnCancel, afterSave) {
  if (!editArea || !textarea || !btnSave || !btnCancel) return;

  const closeEditor = () => {
    editArea.style.display = "none";
  };

  btnCancel.onclick = closeEditor;

  btnSave.onclick = async function() {
    const newText = textarea.value.trim();
    if (!newText) return;

    btnSave.disabled = true;
    btnSave.textContent = "保存中...";

    try {
      const res = await fetch("/api/update_abstract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: item.id,
          abstract: newText
        })
      });

      if (res.ok) {
        item.abstract = newText;
        item.raw_abstract = newText;
        item.abstract_source = "user_provided";
        if (typeof afterSave === "function") {
          afterSave();
        }
      } else {
        alert("保存失败");
      }
    } catch (e) {
      alert("错误: " + e.message);
    } finally {
      btnSave.disabled = false;
      btnSave.textContent = "保存";
    }
  };
}

function toggleAbstractEditor(item, editArea, textarea) {
  if (!editArea || !textarea) return;

  if (editArea.style.display === "none" || !editArea.style.display) {
    editArea.style.display = "block";

    if (item.raw_abstract) {
      textarea.value = item.raw_abstract;
    } else if (item.abstract_source === "gpt_generated") {
      textarea.value = "";
    } else {
      textarea.value = item.abstract || "";
    }

    textarea.focus();
  } else {
    editArea.style.display = "none";
  }
}

function appendMetaBadges(container, item) {
  const methodSummary = (item.methods || [])
    .map((entry) => `${entry.name} (${Math.round((entry.confidence || 0) * 100)}%)`)
    .join(", ");
  const topicSummary = (item.topics || [])
    .map((entry) => `${entry.name} (${Math.round((entry.confidence || 0) * 100)}%)`)
    .join(", ");

  (item.methods || []).forEach((entry) => appendBadge(container, "method", entry, { title: methodSummary }));
  (item.topics || []).forEach((entry) => appendBadge(container, "topic", entry, { title: topicSummary }));
  if (item.user_corrected) {
    appendTagBadge(container, "用户修正");
  }
}

function createSwipeUtilityButton(icon, label, title) {
  const button = document.createElement("button");
  configureActionButton(button, {
    icon,
    label,
    title,
    variant: "action-btn--utility"
  });
  return button;
}

function createSwipeDeckButton(icon, label, title, tone) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `swipe-action swipe-action--${tone}`;
  button.title = title;
  button.setAttribute("aria-label", title);

  const iconNode = document.createElement("span");
  iconNode.className = "swipe-action__icon";
  iconNode.textContent = icon;

  const labelNode = document.createElement("span");
  labelNode.className = "swipe-action__label";
  labelNode.textContent = label;

  button.appendChild(iconNode);
  button.appendChild(labelNode);
  return button;
}

function createSwipePaperCard(item, options = {}) {
  const { isCurrent = false, isPreview = false } = options;
  const highlightTerms = getHighlightTerms();
  const card = document.createElement("article");
  card.className = ["swipe-card", isCurrent ? "swipe-card--current" : "", isPreview ? "swipe-card--preview" : ""]
    .filter(Boolean)
    .join(" ");

  const meta = document.createElement("div");
  meta.className = "swipe-card__meta";

  const metaText = document.createElement("span");
  metaText.className = "swipe-card__source";
  metaText.textContent = `${item.journal || "Unknown"} · ${formatDate(item.date)}`;
  meta.appendChild(metaText);
  appendMetaBadges(meta, item);

  const title = document.createElement("a");
  title.className = "swipe-card__title";
  title.href = item.link || "#";
  title.target = "_blank";
  title.rel = "noreferrer";
  title.innerHTML = highlightText(item.title || "Untitled", highlightTerms);

  const titleZh = document.createElement("div");
  titleZh.className = "swipe-card__title-zh";
  titleZh.innerHTML = item.title_zh ? highlightText(item.title_zh, highlightTerms) : "暂无中文标题";

  const fields = document.createElement("div");
  fields.className = "swipe-card__fields card__fields";
  appendField(fields, "作者", item.authors, highlightTerms);
  appendField(fields, "来源", item.source, highlightTerms);
  appendField(fields, "出版时间", item.publicationDate, highlightTerms);
  if (!fields.children.length) {
    fields.style.display = "none";
  }

  const abstractWrap = document.createElement("div");
  abstractWrap.className = "swipe-card__abstract";
  if (elements.summaryToggle.checked && item.abstract) {
    const previewItem = {
      ...item,
      abstract: truncateText(item.abstract, 520)
    };
    renderAbstractBlock(abstractWrap, previewItem, highlightTerms);
  } else {
    abstractWrap.style.display = "none";
  }

  const utility = document.createElement("div");
  utility.className = "swipe-card__utility";

  const btnOpen = createSwipeUtilityButton("↗", "打开", "打开论文链接");
  btnOpen.onclick = (e) => {
    e.preventDefault();
    if (item.link) {
      window.open(item.link, "_blank", "noopener,noreferrer");
    }
  };

  const btnClassify = createSwipeUtilityButton("🏷️", "分类", "编辑分类");
  btnClassify.onclick = (e) => {
    e.preventDefault();
    openClassificationModal(item);
  };

  const btnEdit = createSwipeUtilityButton("✏️", "摘要", "补充/编辑摘要");
  utility.appendChild(btnOpen);
  utility.appendChild(btnClassify);
  utility.appendChild(btnEdit);

  const editArea = document.createElement("div");
  editArea.className = "card__edit-area swipe-card__edit-area";
  editArea.style.display = "none";

  const textarea = document.createElement("textarea");
  textarea.placeholder = "在此粘贴或输入摘要...";

  const editActions = document.createElement("div");
  editActions.className = "swipe-card__edit-actions";

  const btnSave = document.createElement("button");
  btnSave.type = "button";
  btnSave.className = "btn btn--primary btn--small";
  btnSave.textContent = "保存";

  const btnCancel = document.createElement("button");
  btnCancel.type = "button";
  btnCancel.className = "btn btn--secondary btn--small";
  btnCancel.textContent = "取消";

  editActions.appendChild(btnSave);
  editActions.appendChild(btnCancel);
  editArea.appendChild(textarea);
  editArea.appendChild(editActions);
  setupAbstractEditor(item, editArea, textarea, btnSave, btnCancel, () => renderList());

  btnEdit.onclick = (e) => {
    e.preventDefault();
    toggleAbstractEditor(item, editArea, textarea);
  };

  card.appendChild(meta);
  card.appendChild(title);
  card.appendChild(titleZh);
  card.appendChild(fields);
  card.appendChild(abstractWrap);
  card.appendChild(utility);
  card.appendChild(editArea);

  return card;
}

function getCurrentSwipeItem() {
  if (!state.filtered.length) return null;
  if (state.swipeIndex < 0) state.swipeIndex = 0;
  if (state.swipeIndex >= state.filtered.length) {
    state.swipeIndex = Math.max(0, state.filtered.length - 1);
  }
  return state.filtered[state.swipeIndex] || null;
}

function commitSwipeAction(action, direction) {
  if (state.swipeBusy) return;
  const item = getCurrentSwipeItem();
  if (!item) return;

  state.swipeBusy = true;
  const previousIndex = state.swipeIndex;
  const card = elements.list.querySelector(".swipe-card--current");
  if (card) {
    card.classList.add(direction === "right" ? "swipe-card--leaving-right" : "swipe-card--leaving-left");
  }

  window.setTimeout(() => {
    performPaperAction(item.link, action, {
      hideCard: false,
      undoable: true,
      refresh: false,
      updateCounts: false,
      undoContext: { item, index: previousIndex }
    });
    removeFilteredItem(item.link, previousIndex);
    renderList();
    updateFilterCounts(getRenderedLogicalCount());
    state.swipeBusy = false;
  }, card ? 180 : 0);
}

function undoLastInteraction() {
  const record = state.undoStack.pop();
  if (!record) {
    clearUndoBar();
    return;
  }

  clearUndoTimer();
  applyInteractionToState(record.id, record.undoAction);
  saveInteraction(record.id, record.undoAction);

  if (shouldUseSwipeDeck() && record.undoContext && record.undoContext.item) {
    insertFilteredItem(record.undoContext.item, record.undoContext.index);
    renderList();
    updateFilterCounts(getRenderedLogicalCount());
  } else if (shouldUseSwipeDeck()) {
    applyFilters({ resetSwipeIndex: false });
  } else {
    restoreCurrentCard(record.card);
    updateFilterCounts();
  }

  renderUndoBar();
}

function renderSwipeDeck() {
  elements.list.innerHTML = "";
  elements.list.classList.add("grid--swipe");

  if (state.filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "swipe-empty";

    const heading = document.createElement("strong");
    heading.textContent = "暂时没有新的文献了。";

    const copy = document.createElement("span");
    copy.textContent = "可以调整筛选条件，或者切换到收藏夹继续处理已经留住的论文。";

    empty.appendChild(heading);
    empty.appendChild(copy);
    elements.list.appendChild(empty);
    return;
  }

  const currentItem = getCurrentSwipeItem();
  const nextItem = state.filtered[state.swipeIndex + 1];
  const deck = document.createElement("div");
  deck.className = "swipe-deck";

  if (nextItem) {
    deck.appendChild(createSwipePaperCard(nextItem, { isPreview: true }));
  }
  deck.appendChild(createSwipePaperCard(currentItem, { isCurrent: true }));

  const controls = document.createElement("div");
  controls.className = "swipe-actions";

  const btnSkip = createSwipeDeckButton("←", "跳过", "跳过这篇论文", "left");
  btnSkip.onclick = () => commitSwipeAction("hide", "left");

  const progress = document.createElement("div");
  progress.className = "swipe-progress";
  progress.textContent = `${state.swipeIndex + 1} / ${state.filtered.length}`;

  const btnLike = createSwipeDeckButton("→", "收藏", "收藏这篇论文", "right");
  btnLike.onclick = () => commitSwipeAction("like", "right");

  controls.appendChild(btnSkip);
  controls.appendChild(progress);
  controls.appendChild(btnLike);

  const hint = document.createElement("div");
  hint.className = "swipe-hint";
  hint.textContent = "键盘：← 跳过 · → 收藏 · Z 撤销";

  const shell = document.createElement("div");
  shell.className = "swipe-shell";
  shell.appendChild(deck);
  shell.appendChild(controls);
  shell.appendChild(hint);
  elements.list.appendChild(shell);
}

function renderLoadMoreControl(renderedCount, totalCount) {
  if (renderedCount >= totalCount) return;

  const more = document.createElement("div");
  more.className = "list-load-more";

  const label = document.createElement("span");
  label.textContent = `已显示 ${renderedCount} 篇 / 共 ${totalCount} 篇`;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn--secondary";
  button.textContent = "加载更多";
  button.onclick = () => {
    state.listRenderLimit = Math.min(state.listRenderLimit + LIST_RENDER_STEP, state.filtered.length);
    renderList();
    updateFilterCounts(getRenderedLogicalCount());
  };

  more.appendChild(label);
  more.appendChild(button);
  elements.list.appendChild(more);
}

function renderList() {
  if (shouldUseSwipeDeck()) {
    renderSwipeDeck();
    return;
  }

  elements.list.innerHTML = "";
  elements.list.classList.remove("grid--swipe");
  const showSummary = elements.summaryToggle.checked;
  const highlightTerms = getHighlightTerms();

  if (state.filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "card";
    const heading = document.createElement("strong");
    heading.style.display = "block";
    heading.style.marginBottom = "6px";
    heading.style.fontSize = "1.1rem";
    const description = document.createElement("div");
    description.style.color = "#64748b";
    if (state.filterMode === "favorites") {
      heading.textContent = "还没有收藏任何文章。";
      description.textContent = "在收件箱里看到值得追的论文时，点一下 ❤️ 它就会进到这里。";
    } else if (state.filterMode === "archived") {
      heading.textContent = "暂无已归档文章。";
      description.textContent = "归档区适合存放已经看过、但暂时不准备继续处理的论文。";
    } else {
      heading.textContent = "暂时没有新的文献了。";
      description.textContent = "可以稍后刷新，或者切换到收藏夹继续处理之前留住的论文。";
    }
    empty.appendChild(heading);
    empty.appendChild(description);
    elements.list.appendChild(empty);
    return;
  }

  // Use DocumentFragment for batch DOM insertion (1000x faster!)
  const fragment = document.createDocumentFragment();
  const itemsToRender = shouldLimitListRender()
    ? state.filtered.slice(0, state.listRenderLimit)
    : state.filtered;

  for (const item of itemsToRender) {
    const node = elements.cardTemplate.content.cloneNode(true);
    const card = node.querySelector(".card");
    const meta = node.querySelector(".card__meta");
    const title = node.querySelector(".card__title");
    const titleZh = node.querySelector(".card__title_zh");
    const abstractDiv = node.querySelector(".card__abstract");
    const summary = node.querySelector(".card__summary");
    const fields = node.querySelector(".card__fields");
    const toggle = node.querySelector(".card__toggle");

    // --- FIX: Meta Badges Rendering ---
    // Clear meta content first
    meta.innerHTML = '';
    
    const metaRow = document.createElement('div');
    metaRow.className = 'card__meta-row';

    const metaInfo = document.createElement('div');
    metaInfo.className = 'card__meta-info';

    // 1. Create Date/Journal Text
    const metaText = document.createElement('span');
    metaText.textContent = `${item.journal || "Unknown"} · ${formatDate(item.date)}`;
    metaText.style.marginRight = "12px";
    metaText.style.fontWeight = "700";
    metaText.style.color = "#334155";
    metaInfo.appendChild(metaText);
    
    // 2. Append Badges (Method & Topic)
    const methodSummary = (item.methods || [])
      .map((entry) => `${entry.name} (${Math.round((entry.confidence || 0) * 100)}%)`)
      .join(", ");
    const topicSummary = (item.topics || [])
      .map((entry) => `${entry.name} (${Math.round((entry.confidence || 0) * 100)}%)`)
      .join(", ");
    if (typeof appendBadge === "function") {
      (item.methods || []).forEach((entry) => appendBadge(metaInfo, "method", entry, { title: methodSummary }));
      (item.topics || []).forEach((entry) => appendBadge(metaInfo, "topic", entry, { title: topicSummary }));
    }
    if (item.user_corrected) {
      appendTagBadge(metaInfo, "用户修正");
    }
    // ----------------------------------

    title.innerHTML = highlightText(item.title || "Untitled", highlightTerms);
    
    title.href = item.link || "#";

    if (item.title_zh) {
      titleZh.innerHTML = highlightText(item.title_zh, highlightTerms);
      titleZh.style.display = "block";
    } else {
      titleZh.style.display = "none";
    }

    appendField(fields, "作者", item.authors, highlightTerms);
    appendField(fields, "来源", item.source, highlightTerms);
    appendField(fields, "出版时间", item.publicationDate, highlightTerms);
    appendField(fields, "DOI", item.doi, highlightTerms);
    appendField(fields, "理论", item.theoriesText, highlightTerms);
    appendField(fields, "情境", item.contextText, highlightTerms);
    appendField(fields, "对象", item.subjectsText, highlightTerms);
    if (!fields.children.length) {
      fields.remove();
    }

    // Display abstract if available
    if (showSummary && item.abstract) {
      renderAbstractBlock(abstractDiv, item, highlightTerms);
    } else {
      abstractDiv.style.display = "none";
    }

    if (showSummary && item.summary) {
      const hasLong = item.summaryShort && item.summaryShort !== item.summary;
      summary.innerHTML = highlightText(item.summaryShort || item.summary, highlightTerms);
      if (hasLong) {
        toggle.textContent = "展开全文摘要";
        toggle.addEventListener("click", () => {
          const expanded = toggle.getAttribute("data-expanded") === "true";
          const nextExpanded = !expanded;
          toggle.setAttribute("data-expanded", nextExpanded ? "true" : "false");
          toggle.textContent = nextExpanded ? "收起摘要" : "展开全文摘要";
          summary.innerHTML = highlightText(
            nextExpanded ? item.summary : item.summaryShort,
            highlightTerms
          );
        });
      } else {
        toggle.remove();
      }
    } else {
      summary.remove();
      toggle.remove();
      card.style.paddingBottom = "12px";
    }

    // --- Action Buttons ---
    const actionsDiv = document.createElement("div");
    actionsDiv.className = "article-actions";
    
    const isLiked = state.interactionSets.favorites.has(item.link);
    const isArchived = state.interactionSets.archived.has(item.link);
    
    const btnLike = document.createElement("button");
    configureActionButton(btnLike, {
      icon: isLiked ? "❤️" : "🤍",
      label: isLiked ? "取消收藏" : "收藏",
      title: isLiked ? "取消收藏" : "收藏",
      variant: "action-btn--accent",
      isActive: isLiked
    });
    btnLike.onclick = function(e) { e.preventDefault(); toggleLike(item.link, this); };

    const btnArchive = document.createElement("button");
    configureActionButton(btnArchive, {
      icon: isArchived ? "📥" : "📦",
      label: isArchived ? "回到收件箱" : "归档",
      title: isArchived ? "取消归档 (回到收件箱)" : "归档 (移出收藏)",
      variant: "action-btn--neutral"
    });
    btnArchive.onclick = function(e) { e.preventDefault(); toggleArchive(item.link, this); };

    const btnRestore = document.createElement("button");
    configureActionButton(btnRestore, {
      icon: "↩️",
      label: "恢复收藏",
      title: "恢复到收藏",
      variant: "action-btn--neutral"
    });
    btnRestore.onclick = function(e) { e.preventDefault(); restoreFromArchive(item.link, this); };

    const btnClassify = document.createElement("button");
    configureActionButton(btnClassify, {
      icon: "🏷️",
      label: "分类",
      title: "编辑分类",
      variant: "action-btn--utility"
    });
    btnClassify.onclick = function(e) {
      e.preventDefault();
      openClassificationModal(item);
    };

    // Edit Abstract Button
    const btnEdit = document.createElement("button");
    configureActionButton(btnEdit, {
      icon: "✏️",
      label: "摘要",
      title: "补充/编辑摘要",
      variant: "action-btn--utility"
    });
    
    // Edit Area Elements
    const editArea = node.querySelector(".card__edit-area");
    const textarea = editArea.querySelector("textarea");
    const btnSave = editArea.querySelector(".btn-save-abstract");
    const btnCancel = editArea.querySelector(".btn-cancel-abstract");

    btnEdit.onclick = function(e) {
        e.preventDefault();
        toggleAbstractEditor(item, editArea, textarea);
    };

    setupAbstractEditor(item, editArea, textarea, btnSave, btnCancel, () => renderList());
    
    const btnHide = document.createElement("button");
    configureActionButton(btnHide, {
      icon: "❌",
      label: "跳过",
      title: "不感兴趣",
      variant: "action-btn--danger"
    });
    btnHide.onclick = function(e) { 
      e.preventDefault(); 
      toggleHide(item.link, this); 
    };
    
    actionsDiv.appendChild(btnClassify);
    actionsDiv.appendChild(btnEdit); // Add Edit button
    
    // Explicit Button Logic
    if (state.filterMode === "favorites") {
      actionsDiv.appendChild(btnArchive); // Show Archive Button in Favorites
      actionsDiv.appendChild(btnLike);
      actionsDiv.appendChild(btnHide);
    } else if (state.filterMode === "archived") {
      actionsDiv.appendChild(btnRestore); // Restore to Favorites
      actionsDiv.appendChild(btnArchive); // Unarchive (to Inbox)
      actionsDiv.appendChild(btnHide);
    } else {
      // Inbox or other
      actionsDiv.appendChild(btnLike);
      actionsDiv.appendChild(btnHide);
    }
    
    metaRow.appendChild(metaInfo);
    metaRow.appendChild(actionsDiv);
    meta.appendChild(metaRow);
    // ---------------------

    fragment.appendChild(node);
  }

  // Single DOM insertion instead of 1000 (avoids 1000 reflows!)
  elements.list.appendChild(fragment);
  renderLoadMoreControl(itemsToRender.length, state.filtered.length);
}

function applyFilters(options = {}) {
  const { resetSwipeIndex = true } = options;
  if (resetSwipeIndex) {
    state.listRenderLimit = LIST_INITIAL_RENDER_LIMIT;
  }
  normalizeMultiSelectAll(elements.filterMethod);
  normalizeMultiSelectAll(elements.filterTopic);
  const keyword = normalize(elements.searchInput.value);
  const journal = elements.journalSelect.value;
  const methodFilters = getSelectedFilterValues(elements.filterMethod);
  const topicFilters = getSelectedFilterValues(elements.filterTopic);
  const methodMode = elements.filterMethodMode ? elements.filterMethodMode.value : "any";
  const topicMode = elements.filterTopicMode ? elements.filterTopicMode.value : "any";
  const preset = elements.filterPreset ? elements.filterPreset.value : "";
  state.preset = preset;
  const fromDate = elements.fromDate.value ? new Date(elements.fromDate.value) : null;
  const toDate = elements.toDate.value ? new Date(elements.toDate.value) : null;
  const recentCutoff = new Date(Date.now() - 1000 * 60 * 60 * 24 * 90);
  if (preset === "my_focus") {
    state.focusTopics = computeFocusTopics();
  }
  const { favorites, archived, hidden } = state.interactionSets;

  const filtered = state.items.filter((item) => {
    // 1. Check interactions first
    if (hidden.has(item.link)) return false;
    if (state.filterMode === 'favorites' && !favorites.has(item.link)) return false;
    if (state.filterMode === 'archived' && !archived.has(item.link)) return false;
    // In "all" mode, hide items that have been processed (favorites or archived)
    if (state.filterMode === 'all') {
      if (favorites.has(item.link)) return false;
      if (archived.has(item.link)) return false;
    }

    if (journal && item.journal !== journal) return false;

    if (methodFilters.length) {
      const labels = item.methodLabels || [];
      if (methodMode === "all") {
        if (!methodFilters.every((m) => labels.includes(m))) return false;
      } else {
        if (!methodFilters.some((m) => labels.includes(m))) return false;
      }
    }

    if (topicFilters.length) {
      const labels = item.topicLabels || [];
      if (topicMode === "all") {
        if (!topicFilters.every((t) => labels.includes(t))) return false;
      } else {
        if (!topicFilters.some((t) => labels.includes(t))) return false;
      }
    }

    if (preset === "cross") {
      if (!item.topicLabels || item.topicLabels.length < 2) return false;
    }
    if (preset === "recent_hot") {
      if (!item.date || item.date < recentCutoff) return false;
    }
    if (preset === "my_focus" && state.focusTopics.length) {
      if (!state.focusTopics.some((topic) => (item.topicLabels || []).includes(topic))) return false;
    }
    
    if (fromDate && item.date < fromDate) return false;
    if (toDate && item.date > toDate) return false;

    if (keyword) {
      const haystack = `${item.title} ${item.title_zh || ""} ${item.summary} ${item.abstract || ""} ${item.journal}`.toLowerCase();
      if (!haystack.includes(keyword)) {
        return false;
      }
    }

    return true;
  });

  const sortDir = elements.sortSelect.value;
  filtered.sort((a, b) => (sortDir === "asc" ? a.date - b.date : b.date - a.date));

  state.filtered = filtered;
  if (resetSwipeIndex) {
    state.swipeIndex = 0;
  } else if (state.swipeIndex >= state.filtered.length) {
    state.swipeIndex = Math.max(0, state.filtered.length - 1);
  }
  renderList();
  updateFilterCounts(getRenderedLogicalCount());
  updateTopicCloudVisibility();
  if (state.filterMode === "favorites") {
    renderTopicCloud(filtered);
  }
}

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      // case "'":
      //   return "&#39;";
      default:
        return char;
    }
  });
}

function cleanJournalName(name) {
  let clean = (name || "").trim();
  if (clean.toLowerCase() === "latest results") {
    return "Journal of the Academy of Marketing Science";
  }
  const prefixPatterns = [
    /^sciencedirect(?:\s+publication)?\s*[:\-]\s*/i,
    /^wiley\s*[:\-]\s*/i,
    /^sage publications inc\s*[:\-]\s*/i,
    /^sage publications ltd\s*[:\-]\s*/i,
    /^tandf\s*[:\-]\s*/i,
    /^iorms\s*[:\-]\s*/i,
    /^academy of management\s*[:\-]\s*/i,
    /^the university of chicago press\s*[:\-]\s*/i
  ];
  const suffixPatterns = [
    /\s*[:\-]?\s*table of contents\s*$/i,
    /\s*[:\-]?\s*advance access\s*$/i,
    /\s*[:\-]?\s*latest results\s*$/i,
    /\s*[:\-]?\s*vol(?:ume)?\s*\d+\s*,?\s*iss(?:ue)?\.?\s*\d+\s*$/i,
    /\s*[:\-]?\s*vol(?:ume)?\s*\d+\s*$/i,
    /\s*[:\-]?\s*iss(?:ue)?\.?\s*\d+\s*$/i
  ];

  let changed = true;
  while (changed) {
    changed = false;
    for (const pattern of prefixPatterns) {
      const next = clean.replace(pattern, "");
      if (next !== clean) {
        clean = next;
        changed = true;
      }
    }
    for (const pattern of suffixPatterns) {
      const next = clean.replace(pattern, "");
      if (next !== clean) {
        clean = next;
        changed = true;
      }
    }
  }

  clean = clean.replace(/\s*\[.*?\]\s*$/, "");
  clean = clean.replace(/\s+/g, " ").trim();
  return clean;
}

function stripBracketedPrefix(title) {
  return (title || "").replace(/^\[[^\]]+\]\s*/, "").trim();
}

function normalizeLine(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function splitSummaryText(text) {
  const normalized = normalizeLine(text);
  if (!normalized) {
    return [];
  }

  // Insert breaks before metadata labels, even if concatenated.
  let withBreaks = normalized
    .replace(/([^\s])\s*(Publication date|Source|Authors?\(s\)?)(\s*:\s*)/gi, "$1\n$2:")
    .replace(/\s*(Publication date|Source|Authors?\(s\)?)(\s*:\s*)/gi, "\n$1:");

  return withBreaks
    .split(/\n+/)
    .map((line) => normalizeLine(line))
    .filter(Boolean);
}

function decodeHtmlEntities(text) {
  const textArea = document.createElement("textarea");
  textArea.innerHTML = text;
  return textArea.value;
}

function parseSummary(html) {
  if (!html) {
    return { text: "", publicationDate: "", source: "", authors: "" };
  }

  // 1. Decode entities (e.g. &lt;p&gt; -> <p>)
  let decoded = decodeHtmlEntities(html);
  const hasTags = /<[^>]+>/.test(decoded);

  let lines = [];
  if (hasTags) {
    // 2. Parse HTML structure
    const doc = new DOMParser().parseFromString(decoded, "text/html");
    // 3. Extract paragraphs and filter common metadata patterns
    const paragraphs = Array.from(doc.body.querySelectorAll("p, div, span"))
      .map((p) => normalizeLine(p.textContent))
      .filter(Boolean);
    lines = paragraphs.length ? paragraphs : splitSummaryText(doc.body.textContent);
  } else {
    lines = splitSummaryText(decoded);
  }

  let publicationDate = "";
  let source = "";
  let authors = "";
  const textParts = [];

  // Common patterns for metadata in summary
  const patterns = [
    { key: 'publicationDate', regex: /^Publication date:\s*(.*)/i },
    { key: 'source', regex: /^Source:\s*(.*)/i },
    { key: 'authors', regex: /^Authors?(?:\(s\))?:\s*(.*)/i }
  ];

  for (const line of lines) {
    let isMetadata = false;
    for (const { key, regex } of patterns) {
      const match = line.match(regex);
      if (match) {
        if (key === 'publicationDate') publicationDate = match[1];
        if (key === 'source') source = match[1];
        if (key === 'authors') authors = match[1];
        isMetadata = true;
        break;
      }
    }
    if (!isMetadata) {
      // Avoid adding empty or purely structural lines
      if (line.length > 2) textParts.push(line);
    }
  }

  let text = textParts.join(" ");
  // Final cleanup of any lingering HTML tags if DOMParser missed something
  text = text.replace(/<\/?[^>]+(>|$)/g, "");
  
  return { text, publicationDate, source, authors };
}

function truncateText(text, maxLength) {
  if (!text || text.length <= maxLength) {
    return text;
  }
  const trimmed = text.slice(0, maxLength);
  return trimmed.replace(/\s+\S*$/, "") + "...";
}

function getHighlightTerms() {
  const input = elements.searchInput.value.trim();
  const terms = [...state.keywords];
  if (input) {
    terms.push(input);
  }
  return terms
    .map((term) => term.trim())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
}

function highlightText(text, terms) {
  const safeText = escapeHtml(text);
  if (!terms.length) {
    return safeText;
  }
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const regex = new RegExp(`(${escaped.join("|")})`, "gi");
  return safeText.replace(regex, '<mark class="hl">$1</mark>');
}

function appendField(container, label, value, highlightTerms) {
  if (!value) {
    return;
  }
  const row = document.createElement("div");
  row.className = "card__field";
  row.innerHTML = `<span class="card__label">${label}</span><span class="card__value">${highlightText(
    value,
    highlightTerms
  )}</span>`;
  container.appendChild(row);
}

function renderFilterOptions() {
  if (elements.filterMethod) {
    const selected = new Set(getSelectedFilterValues(elements.filterMethod));
    elements.filterMethod.innerHTML = "";
    if (elements.filterMethod.tagName === "SELECT") {
      const optionAll = document.createElement("option");
      optionAll.value = "";
      optionAll.textContent = "全部方法";
      optionAll.selected = selected.size === 0;
      elements.filterMethod.appendChild(optionAll);
      state.categories.methods.forEach((method) => {
        if (!method || !method.name) return;
        const option = document.createElement("option");
        option.value = method.name;
        option.textContent = method.label ? `${method.label} (${method.name})` : method.name;
        option.selected = selected.has(method.name);
        elements.filterMethod.appendChild(option);
      });
    } else {
      buildChipList(elements.filterMethod, state.categories.methods, selected);
    }
  }

  if (elements.filterTopic) {
    const selected = new Set(getSelectedFilterValues(elements.filterTopic));
    elements.filterTopic.innerHTML = "";
    if (elements.filterTopic.tagName === "SELECT") {
      const optionAll = document.createElement("option");
      optionAll.value = "";
      optionAll.textContent = "全部主题";
      optionAll.selected = selected.size === 0;
      elements.filterTopic.appendChild(optionAll);
      state.categories.topics.forEach((topic) => {
        if (!topic || !topic.name) return;
        const option = document.createElement("option");
        option.value = topic.name;
        option.textContent = topic.name;
        option.selected = selected.has(topic.name);
        elements.filterTopic.appendChild(option);
      });
    } else {
      buildChipList(elements.filterTopic, state.categories.topics, selected);
    }
  }
}

function populateJournals(items) {
  const set = new Set(items.map((item) => item.journal).filter(Boolean));
  const journals = Array.from(set).sort((a, b) => a.localeCompare(b));

  for (const journal of journals) {
    const option = document.createElement("option");
    option.value = journal;
    option.textContent = journal;
    elements.journalSelect.appendChild(option);
  }
}

async function loadCategories() {
  try {
    const res = await fetch("/api/categories?t=" + Date.now(), { cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      state.categories.methods = data.methods || [];
      state.categories.topics = data.topics || [];
      state.categories.theories = data.theories || [];
      state.categories.contexts = data.contexts || [];
      state.categories.subjects = data.subjects || [];
      renderFilterOptions();
      renderCategoryEditor();
      renderClassificationOptions();
    }
  } catch (e) {
    console.warn("Failed to load categories", e);
  }
}

function createCategoryRow(item = {}, type = "method") {
  const row = document.createElement("div");
  row.className = "category-row";
  row.dataset.type = type;

  const name = document.createElement("input");
  name.type = "text";
  name.placeholder = "名称";
  name.value = item.name || "";
  name.className = "cat-name";

  const label = document.createElement("input");
  label.type = "text";
  label.placeholder = "显示名";
  label.value = item.label || "";
  label.className = "cat-label";

  const keywords = document.createElement("input");
  keywords.type = "text";
  keywords.placeholder = "关键词(逗号分隔)";
  keywords.value = Array.isArray(item.keywords) ? item.keywords.join(", ") : "";
  keywords.className = "cat-keywords";

  const level = document.createElement("input");
  level.type = "number";
  level.min = "1";
  level.max = "3";
  level.placeholder = "层级";
  level.value = item.level || "";
  level.className = "cat-level";

  const parent = document.createElement("input");
  parent.type = "text";
  parent.placeholder = "父级";
  parent.value = item.parent || "";
  parent.className = "cat-parent";

  const color = document.createElement("input");
  color.type = "text";
  color.placeholder = "背景色";
  color.value = item.color || "";
  color.className = "cat-color";

  const text = document.createElement("input");
  text.type = "text";
  text.placeholder = "文字色";
  text.value = item.text || "";
  text.className = "cat-text";

  const btnDelete = document.createElement("button");
  btnDelete.type = "button";
  btnDelete.className = "btn btn--danger btn--small";
  btnDelete.textContent = "删除";
  btnDelete.onclick = () => row.remove();

  row.appendChild(name);
  row.appendChild(label);
  row.appendChild(keywords);
  if (type === "topic") {
    row.appendChild(level);
    row.appendChild(parent);
  }
  row.appendChild(color);
  row.appendChild(text);
  row.appendChild(btnDelete);

  return row;
}

function renderCategoryEditor() {
  const methodEditor = document.getElementById("methodEditor");
  const topicEditor = document.getElementById("topicEditor");
  if (!methodEditor || !topicEditor) return;
  methodEditor.innerHTML = "";
  topicEditor.innerHTML = "";
  state.categories.methods.forEach((item) => methodEditor.appendChild(createCategoryRow(item, "method")));
  state.categories.topics.forEach((item) => topicEditor.appendChild(createCategoryRow(item, "topic")));

  const theoryEditor = document.getElementById("theoryEditor");
  const contextEditor = document.getElementById("contextEditor");
  const subjectEditor = document.getElementById("subjectEditor");
  if (theoryEditor) theoryEditor.value = (state.categories.theories || []).join(", ");
  if (contextEditor) contextEditor.value = (state.categories.contexts || []).join(", ");
  if (subjectEditor) subjectEditor.value = (state.categories.subjects || []).join(", ");
}

function collectCategoryList(container, type) {
  if (!container) return [];
  const items = [];
  container.querySelectorAll(".category-row").forEach((row) => {
    const name = row.querySelector(".cat-name")?.value.trim();
    if (!name) return;
    const label = row.querySelector(".cat-label")?.value.trim() || "";
    const keywordsRaw = row.querySelector(".cat-keywords")?.value || "";
    const keywords = keywordsRaw
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean);
    const levelVal = row.querySelector(".cat-level")?.value;
    const parentVal = row.querySelector(".cat-parent")?.value.trim();
    const color = row.querySelector(".cat-color")?.value.trim();
    const text = row.querySelector(".cat-text")?.value.trim();
    const item = { name };
    if (label) item.label = label;
    if (keywords.length) item.keywords = keywords;
    if (type === "topic") {
      if (levelVal) item.level = Number(levelVal);
      if (parentVal) item.parent = parentVal;
    }
    if (color) item.color = color;
    if (text) item.text = text;
    items.push(item);
  });
  return items;
}

function buildChipList(container, items, selected) {
  if (!container) return;
  container.innerHTML = "";
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const label = typeof item === "string" ? item : item.name;
    if (!label) return;
    const chip = document.createElement("label");
    chip.className = "chip-item";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = label;
    input.checked = selected.has(label);
    const span = document.createElement("span");
    span.textContent = label;
    chip.appendChild(input);
    chip.appendChild(span);
    fragment.appendChild(chip);
  });
  container.appendChild(fragment);
}

function renderClassificationOptions(item = null) {
  const methodBox = document.getElementById("classificationMethods");
  const topicBox = document.getElementById("classificationTopics");
  const theoryBox = document.getElementById("classificationTheories");
  const contextBox = document.getElementById("classificationContexts");
  const subjectBox = document.getElementById("classificationSubjects");
  if (!methodBox || !topicBox || !theoryBox || !contextBox || !subjectBox) return;

  const methodSelected = new Set((item?.methodLabels || []).filter(Boolean));
  const topicSelected = new Set((item?.topicLabels || []).filter(Boolean));
  const theorySelected = new Set((item?.theories || []).filter(Boolean));
  const contextSelected = new Set((item?.context || []).filter(Boolean));
  const subjectSelected = new Set((item?.subjects || []).filter(Boolean));

  buildChipList(methodBox, state.categories.methods, methodSelected);
  buildChipList(topicBox, state.categories.topics, topicSelected);
  buildChipList(theoryBox, state.categories.theories, theorySelected);
  buildChipList(contextBox, state.categories.contexts, contextSelected);
  buildChipList(subjectBox, state.categories.subjects, subjectSelected);

  const novelty = document.getElementById("classificationNovelty");
  if (novelty) {
    novelty.value = item?.novelty_score ? String(item.novelty_score) : "";
  }

  const custom = document.getElementById("classificationCustom");
  if (custom) custom.value = "";
}

function applyUrlFilters() {
  const params = new URLSearchParams(window.location.search);
  if (!params.toString()) return;

  const journalParam = (params.get("journal") || "").trim();
  const sourceParam = (params.get("source") || "").trim();
  const queryParam = (params.get("q") || "").trim();

  if (journalParam && elements.journalSelect) {
    const options = Array.from(elements.journalSelect.options);
    const match = options.find(
      (option) => option.value.toLowerCase() === journalParam.toLowerCase()
    );
    if (match) {
      elements.journalSelect.value = match.value;
    } else if (!sourceParam && !queryParam && elements.searchInput) {
      elements.searchInput.value = journalParam;
    }
  }

  if (sourceParam && elements.searchInput) {
    elements.searchInput.value = sourceParam;
  } else if (queryParam && elements.searchInput) {
    elements.searchInput.value = queryParam;
  }
}

function attachHandlers() {
  const controls = [
    elements.searchInput,
    elements.journalSelect,
    elements.filterMethod,
    elements.filterTopic,
    elements.filterMethodMode,
    elements.filterTopicMode,
    elements.filterPreset,
    elements.fromDate,
    elements.toDate,
    elements.sortSelect,
    elements.summaryToggle
  ];
  controls.forEach((control) => {
    if (!control) return;
    if (control.tagName === "SELECT" && control.multiple) {
      control.addEventListener("focus", () => cacheMultiSelectState(control));
      control.addEventListener("mousedown", () => cacheMultiSelectState(control));
      control.addEventListener("keydown", () => cacheMultiSelectState(control));
    }
    control.addEventListener("input", applyFilters);
    control.addEventListener("change", applyFilters);
  });
}

async function loadFeed() {
  setStatus("加载中...");
  try {
    // 添加时间戳参数和缓存控制，确保每次都获取最新数据
    const response = await fetch("feed.json?t=" + Date.now(), {
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
      }
    });
    if (!response.ok) {
      throw new Error("feed.json missing");
    }
    const payload = await response.json();
    state.keywords = payload.keywords || [];
    state.items = (payload.items || []).map((item) => {
      const parsed = parseSummary(item.summary);
      const methods = normalizeLabelEntries(item.methods || item.method || "");
      const topics = normalizeLabelEntries(item.topics || item.topic || "");
      const theories = Array.isArray(item.theories) ? item.theories.filter(Boolean) : [];
      const contexts = Array.isArray(item.context) ? item.context.filter(Boolean) : [];
      const subjects = Array.isArray(item.subjects) ? item.subjects.filter(Boolean) : [];
      return {
        ...item,
        // Explicitly map new fields just in case spread operator misses them due to some weirdness
        method: item.method || (methods[0] ? methods[0].name : "Qualitative"),
        topic: item.topic || (topics[0] ? topics[0].name : "Other Marketing"),
        methods,
        topics,
        theories,
        context: contexts,
        subjects,
        methodLabels: getLabelNames(methods, item.method),
        topicLabels: getLabelNames(topics, item.topic),
        theoriesText: theories.join("、"),
        contextText: contexts.join("、"),
        subjectsText: subjects.join("、"),
        user_corrected: Boolean(item.user_corrected),

        journal: cleanJournalName(item.journal),
        title: stripBracketedPrefix(item.title || ""),
        summary: parsed.text,
        summaryShort: truncateText(parsed.text, 360),
        raw_abstract: item.raw_abstract || "",
        publicationDate: parsed.publicationDate,
        source: parsed.source,
        authors: parsed.authors,
        date: new Date(item.pub_date)
      };
    });

    populateJournals(state.items);
    applyUrlFilters();
    elements.generatedAt.textContent = payload.generated_at
      ? `更新于 ${formatDate(new Date(payload.generated_at))}`
      : "";
    attachHandlers();
    applyFilters();
  } catch (error) {
    setStatus("无法加载 feed.json，请先运行 get_RSS.py 并用本地服务器打开页面。");
  }
}

function openClassificationModal(item) {
  const modal = document.getElementById("classificationModal");
  const title = document.getElementById("classificationTitle");
  if (!modal) return;
  currentClassificationItem = item;
  if (title) {
    title.textContent = item.title || "未命名论文";
  }
  renderClassificationOptions(item);
  modal.showModal();
}

function collectChipValues(container) {
  if (!container) return [];
  const values = [];
  container.querySelectorAll("input[type='checkbox']").forEach((input) => {
    if (input.checked) values.push(input.value);
  });
  return values;
}

async function saveClassificationEdits() {
  if (!currentClassificationItem) return;
  const methodBox = document.getElementById("classificationMethods");
  const topicBox = document.getElementById("classificationTopics");
  const theoryBox = document.getElementById("classificationTheories");
  const contextBox = document.getElementById("classificationContexts");
  const subjectBox = document.getElementById("classificationSubjects");
  const novelty = document.getElementById("classificationNovelty");
  const custom = document.getElementById("classificationCustom");

  const methods = collectChipValues(methodBox).map((name) => ({ name, confidence: 0.95 }));
  const topics = collectChipValues(topicBox).map((name) => ({ name, confidence: 0.95 }));
  const theories = collectChipValues(theoryBox);
  const context = collectChipValues(contextBox);
  const subjects = collectChipValues(subjectBox);
  const noveltyScore = novelty && novelty.value ? Number(novelty.value) : null;
  const customTags = custom && custom.value
    ? custom.value.split(",").map((t) => t.trim()).filter(Boolean)
    : [];
  const mergedTheories = Array.from(new Set([...theories, ...customTags]));

  try {
    const res = await fetch("/api/update_classification", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: currentClassificationItem.id,
        methods,
        topics,
        theories: mergedTheories,
        context,
        subjects,
        novelty_score: noveltyScore
      })
    });
    if (!res.ok) {
      throw new Error("保存失败");
    }

    currentClassificationItem.methods = methods;
    currentClassificationItem.topics = topics;
    currentClassificationItem.methodLabels = methods.map((m) => m.name);
    currentClassificationItem.topicLabels = topics.map((t) => t.name);
    currentClassificationItem.theories = mergedTheories;
    currentClassificationItem.context = context;
    currentClassificationItem.subjects = subjects;
    currentClassificationItem.theoriesText = mergedTheories.join("、");
    currentClassificationItem.contextText = context.join("、");
    currentClassificationItem.subjectsText = subjects.join("、");
    currentClassificationItem.user_corrected = true;
    renderList();
  } catch (e) {
    alert("分类保存失败: " + e.message);
  }
}

// --- Settings & API Logic ---

const modal = document.getElementById("settingsModal");
const form = document.getElementById("settingsForm");
const btnSettings = document.getElementById("btnSettings");
const btnRefresh = document.getElementById("btnRefresh");
const btnReanalyze = document.getElementById("btnReanalyze");
const btnCancel = document.getElementById("btnCancel");
const btnCategories = document.getElementById("btnCategories");
const categoriesModal = document.getElementById("categoriesModal");
const categoriesForm = document.getElementById("categoriesForm");
const btnAddMethod = document.getElementById("btnAddMethod");
const btnAddTopic = document.getElementById("btnAddTopic");
const btnCancelCategories = document.getElementById("btnCancelCategories");

const classificationModal = document.getElementById("classificationModal");
const classificationForm = document.getElementById("classificationForm");
const btnCancelClassification = document.getElementById("btnCancelClassification");

const btnSummarizeFavorites = document.getElementById("btnSummarizeFavorites");
const btnExportFavorites = document.getElementById("btnExportFavorites");

function parseDownloadFilename(headerValue, fallbackName) {
  if (!headerValue) return fallbackName;
  const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch (e) {
      return utf8Match[1];
    }
  }
  const plainMatch = headerValue.match(/filename="?([^";]+)"?/i);
  if (plainMatch) {
    return plainMatch[1];
  }
  return fallbackName;
}

function triggerDownload(blob, filename) {
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
}

if (btnExportFavorites) {
  btnExportFavorites.addEventListener("click", async () => {
    if (state.interactions.favorites.length === 0) {
      alert("还没有收藏任何文章。");
      return;
    }

    if (!confirm(`确定要把 ${state.interactions.favorites.length} 篇收藏文章导出为 RIS 文件吗？`)) {
      return;
    }

    try {
      btnExportFavorites.disabled = true;
      btnExportFavorites.textContent = "导出中...";
      setStatus("正在生成 RIS 文件...");

      const res = await fetch("/api/export_favorites_ris", { method: "POST" });
      if (!res.ok) {
        const result = await res.json();
        throw new Error(result.message || "Unknown error");
      }

      const exportedCount = Number(res.headers.get("X-Paper-Feed-Exported") || "0");
      const missingCount = Number(res.headers.get("X-Paper-Feed-Missing") || "0");
      const disposition = res.headers.get("Content-Disposition");
      const filename = parseDownloadFilename(
        disposition,
        `paper-feed-favorites-${new Date().toISOString().slice(0, 10)}.ris`
      );
      const blob = await res.blob();
      triggerDownload(blob, filename);

      setStatus("RIS 导出完成");
      alert(`RIS 已下载：导出 ${exportedCount} 篇，缺失 ${missingCount} 篇。`);
    } catch (e) {
      alert("导出失败: " + e.message);
      setStatus("RIS 导出失败");
    } finally {
      btnExportFavorites.disabled = false;
      btnExportFavorites.textContent = "📄 导出 RIS";
    }
  });
}

if (btnSummarizeFavorites) {
  btnSummarizeFavorites.addEventListener("click", async () => {
    if (state.interactions.favorites.length === 0) {
      alert("还没有收藏任何文章。");
      return;
    }
    
    if (!confirm(`确定要对 ${state.interactions.favorites.length} 篇收藏的文章生成 AI 总结吗？\n这可能需要消耗一些 API Token。`)) {
      return;
    }
    
    try {
      btnSummarizeFavorites.disabled = true;
      btnSummarizeFavorites.textContent = "生成中...";
      setStatus("正在请求 AI 生成总结...");
      
      const res = await fetch("/api/summarize_favorites", { method: "POST" });
      const result = await res.json();
      
      if (res.ok) {
        setStatus("总结生成完成，正在重新加载...");
        await loadFeed();
        alert(result.message);
      } else {
        alert("生成失败: " + result.message);
      }
    } catch (e) {
      alert("生成出错: " + e.message);
    } finally {
      btnSummarizeFavorites.disabled = false;
      btnSummarizeFavorites.textContent = "✨ 生成 AI 总结";
    }
  });
}

if (btnReanalyze) {
  btnReanalyze.addEventListener("click", async () => {
    if (!confirm("确定要对所有未分类的文章进行 AI 分析吗？\n这可能需要一些时间，请确保 API Key 已配置。")) {
      return;
    }

    try {
      btnReanalyze.disabled = true;
      btnReanalyze.textContent = "分析中...";
      setStatus("正在请求 AI 分析文章分类...");

      const res = await fetch("/api/reanalyze", { method: "POST" });
      const result = await res.json();

      if (res.ok) {
        setStatus("分析完成，正在重新加载...");
        await loadFeed();
        alert(`AI 分析完成！\n${result.message}`);
      } else {
        throw new Error(result.message || "Unknown error");
      }
    } catch (e) {
      alert("分析失败：" + e.message);
      setStatus("AI 分析出错");
    } finally {
      btnReanalyze.disabled = false;
      btnReanalyze.textContent = "🧠 AI 分析";
    }
  });
}


if (btnSettings && modal) {
  btnSettings.addEventListener("click", async () => {
    // Load current config
    try {
      const res = await fetch("/api/config");
      if (res.ok) {
        const config = await res.json();
        form.OPENAI_API_KEY.value = config.OPENAI_API_KEY || "";
        form.OPENAI_BASE_URL.value = config.OPENAI_BASE_URL || "";
        form.OPENAI_PROXY.value = config.OPENAI_PROXY || "";
      }
    } catch (e) {
      console.warn("Failed to load config", e);
    }
    modal.showModal();
  });
}

if (btnCategories && categoriesModal) {
  btnCategories.addEventListener("click", async () => {
    if (!state.categories.methods.length && !state.categories.topics.length) {
      await loadCategories();
    }
    renderCategoryEditor();
    categoriesModal.showModal();
  });
}

if (btnAddMethod) {
  btnAddMethod.addEventListener("click", () => {
    const methodEditor = document.getElementById("methodEditor");
    if (methodEditor) {
      methodEditor.appendChild(createCategoryRow({}, "method"));
    }
  });
}

if (btnAddTopic) {
  btnAddTopic.addEventListener("click", () => {
    const topicEditor = document.getElementById("topicEditor");
    if (topicEditor) {
      topicEditor.appendChild(createCategoryRow({}, "topic"));
    }
  });
}

if (btnCancelCategories && categoriesModal) {
  btnCancelCategories.addEventListener("click", () => {
    categoriesModal.close();
  });
}

if (btnCancel && modal) {
  btnCancel.addEventListener("click", () => {
    modal.close();
  });
}

if (form && modal) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = {
      OPENAI_API_KEY: form.OPENAI_API_KEY.value.trim(),
      OPENAI_BASE_URL: form.OPENAI_BASE_URL.value.trim(),
      OPENAI_PROXY: form.OPENAI_PROXY.value.trim()
    };
    
    try {
      btnSettings.textContent = "保存中...";
      const res = await fetch("/api/save_config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data)
      });
      if (res.ok) {
        modal.close();
        alert("设置已保存！下次刷新时将生效。");
      } else {
        alert("保存失败，请检查服务器日志。");
      }
    } catch (e) {
      alert("保存出错：" + e.message);
    } finally {
      btnSettings.textContent = "⚙️ 设置";
    }
  });
}

if (categoriesForm && categoriesModal) {
  categoriesForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const methodEditor = document.getElementById("methodEditor");
    const topicEditor = document.getElementById("topicEditor");
    const theoryEditor = document.getElementById("theoryEditor");
    const contextEditor = document.getElementById("contextEditor");
    const subjectEditor = document.getElementById("subjectEditor");

    const payload = {
      version: "v2",
      methods: collectCategoryList(methodEditor, "method"),
      topics: collectCategoryList(topicEditor, "topic"),
      theories: theoryEditor ? theoryEditor.value.split(",").map((t) => t.trim()).filter(Boolean) : [],
      contexts: contextEditor ? contextEditor.value.split(",").map((t) => t.trim()).filter(Boolean) : [],
      subjects: subjectEditor ? subjectEditor.value.split(",").map((t) => t.trim()).filter(Boolean) : []
    };

    try {
      const res = await fetch("/api/categories", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        throw new Error("保存失败");
      }
      state.categories = {
        methods: payload.methods,
        topics: payload.topics,
        theories: payload.theories,
        contexts: payload.contexts,
        subjects: payload.subjects
      };
      renderFilterOptions();
      renderClassificationOptions(currentClassificationItem);
      categoriesModal.close();
      alert("分类配置已保存。");
    } catch (e) {
      alert("分类保存失败: " + e.message);
    }
  });
}

if (classificationForm && classificationModal) {
  classificationForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await saveClassificationEdits();
    classificationModal.close();
  });
}

if (btnCancelClassification && classificationModal) {
  btnCancelClassification.addEventListener("click", () => {
    classificationModal.close();
  });
}

if (btnRefresh) {
  btnRefresh.addEventListener("click", async () => {
    if (!confirm("确定要立即从 RSS 源更新数据吗？如果数据量大可能需要几十秒。")) {
      return;
    }
    
    try {
      btnRefresh.disabled = true;
      btnRefresh.textContent = "更新中...";
      setStatus("正在从服务器拉取最新论文...");
      
      const res = await fetch("/api/fetch", { method: "POST" });
      const result = await res.json();
      
      if (res.ok) {
        setStatus("更新完成，正在重新加载...");
        await loadFeed(); // Reload the feed data
        alert(`更新成功！\n${result.message}`);
      } else {
        throw new Error(result.message || "Unknown error");
      }
    } catch (e) {
      alert("更新失败：" + e.message);
      setStatus("更新出错");
    } finally {
      btnRefresh.disabled = false;
      btnRefresh.textContent = "🔄 立即更新";
    }
  });
}

function setupFilters() {
  const buttons = document.querySelectorAll('.filter-btn');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      // Toggle active class
      buttons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      
      // Update filter
      state.filterMode = btn.dataset.filter;
      if (state.filterMode !== "all") {
        state.swipeBusy = false;
      }

      applyFilters();
    });
  });
}

function updateInboxViewToggle() {
  if (!elements.inboxViewToggle) return;
  elements.inboxViewToggle.querySelectorAll("[data-inbox-view]").forEach((button) => {
    const isActive = button.dataset.inboxView === state.inboxViewMode;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

function setupInboxViewToggle() {
  if (!elements.inboxViewToggle) return;
  elements.inboxViewToggle.querySelectorAll("[data-inbox-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextMode = button.dataset.inboxView;
      if (!nextMode || nextMode === state.inboxViewMode) return;
      state.inboxViewMode = nextMode;
      state.swipeIndex = 0;
      state.listRenderLimit = LIST_INITIAL_RENDER_LIMIT;
      state.swipeBusy = false;
      updateInboxViewToggle();
      applyFilters();
    });
  });
  updateInboxViewToggle();
}

function isTextEditingTarget(target) {
  if (!target) return false;
  const tagName = target.tagName ? target.tagName.toLowerCase() : "";
  if (["input", "select", "textarea", "button"].includes(tagName)) return true;
  return Boolean(target.isContentEditable || target.closest("dialog[open]"));
}

function handleSwipeKeyboard(event) {
  if (!shouldUseSwipeDeck()) return;
  if (isTextEditingTarget(event.target)) return;

  if (event.key === "ArrowRight") {
    event.preventDefault();
    commitSwipeAction("like", "right");
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    commitSwipeAction("hide", "left");
  } else if (event.key.toLowerCase() === "z") {
    event.preventDefault();
    undoLastInteraction();
  }
}

async function init() {
  setupFilters();
  setupInboxViewToggle();
  document.addEventListener("keydown", handleSwipeKeyboard);
  await loadInteractions();
  await loadCategories();
  await loadFeed();
}

document.addEventListener("DOMContentLoaded", init);
