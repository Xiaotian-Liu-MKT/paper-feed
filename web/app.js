const state = {
  items: [],
  filtered: [],
  keywords: [],
  interactions: { favorites: [], archived: [], hidden: [] },
  paperApiAvailable: false,
  offline: false,
  filterMode: 'all', // 'all' (= 待筛选 inbox) | 'favorites' | 'archived' | 'hidden' | 'everything'
  inboxViewMode: "swipe", // 'swipe' | 'list'
  swipeIndex: 0,
  swipeBusy: false,
  pendingWrites: 0,
  undoStack: [],
  visibleLimit: 40,
  listCursor: 0,
  pendingJournal: "",
  pendingFilterSelections: null, // { methods, topics } restored before categories load
  urlFiltersApplied: false,
  // A jump from 洞察 (?journal=…&from=insights) applies its filters for this
  // page load only; persisted filters / mode / positions are left untouched.
  transientUiState: false,
  restoredPositions: null, // { views, swipePaperId } read once at startup
  positionRestored: false,
  positionPending: false, // a view switch happened; its saved position is not restored yet
  fetchAbstractsAvailable: null, // null = unknown, false = server lacks the endpoint
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
  statusMessage: document.getElementById("statusMessage"),
  keyboardHint: document.getElementById("keyboardHint"),
  filterChips: document.getElementById("filterChips"),
  backLink: document.getElementById("backLink"),
  jobStatus: document.getElementById("jobStatus"),
  generatedAt: document.getElementById("generatedAt"),
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
  cardTemplate: document.getElementById("cardTemplate"),
  topicCloud: document.getElementById("topicCloud"),
  topicCloudWrap: document.getElementById("topicCloudWrap"),
  advancedFilters: document.getElementById("advancedFilters"),
  clearAdvancedFilters: document.getElementById("btnClearAdvancedFilters"),
  inboxViewToggle: document.getElementById("inboxViewToggle"),
  loadMore: document.getElementById("btnLoadMore")
};

const PAGE_SIZE = 40;
// The undo history survives view / mode switches (records target paper_id);
// it is bounded so a long triage session does not retain unbounded objects.
const MAX_UNDO_STACK_SIZE = 50;
const UI_STATE_KEY = "paper-feed:ui-state";
const ABSTRACT_DRAFTS_KEY = "paper-feed:abstract-drafts";
const VIEW_LABELS = { all: "待筛选", favorites: "收藏", archived: "归档", hidden: "已隐藏", everything: "全部" };
const VIEW_OF_REVIEW_STATE = { inbox: "all", favorite: "favorites", archived: "archived", hidden: "hidden" };
const VIEW_MODES = ["all", "favorites", "archived", "hidden", "everything"];

const formatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "2-digit"
});

let currentClassificationItem = null;
let searchDebounceId = null;
let handlersAttached = false;

// --- Small UI helpers (status region, toasts, offline banner) ---

function setStatus(text) {
  // Transient messages go to the dedicated aria-live region so the
  // "共 N 篇" count label is never overwritten.
  if (elements.statusMessage) elements.statusMessage.textContent = text || "";
}

function updateCountLabel() {
  if (elements.countLabel) elements.countLabel.textContent = `共 ${state.filtered.length} 篇`;
}

// `action` = { label, onClick } adds a button that runs onClick and closes the toast.
function showToast(message, type = "info", timeoutMs = 6000, action = null) {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  const text = document.createElement("span");
  text.textContent = message;
  toast.appendChild(text);
  if (action && action.label && typeof action.onClick === "function") {
    const actionButton = document.createElement("button");
    actionButton.type = "button";
    actionButton.className = "toast__action";
    actionButton.textContent = action.label;
    actionButton.onclick = () => { toast.remove(); action.onClick(); };
    toast.appendChild(actionButton);
  }
  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast__close";
  close.textContent = "✕";
  close.setAttribute("aria-label", "关闭提示");
  close.onclick = () => toast.remove();
  toast.appendChild(close);
  container.appendChild(toast);
  if (timeoutMs) setTimeout(() => toast.remove(), timeoutMs);
}

function setOfflineMode(offline) {
  state.offline = Boolean(offline);
  const banner = document.getElementById("offlineBanner");
  if (banner) banner.hidden = !state.offline;
}

function safeStorageGet(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}

function safeStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (_) { /* storage is optional */ }
}

// --- Interaction Logic ---

function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

// A paper_id is the durable server identity.  id/link remain only for old
// feed.json exports and the legacy interactions endpoint.
function paperKey(item) {
  return item && (item.paper_id || item.id || item.link);
}

function legacyReference(item) {
  return item && { paper_id: item.paper_id, id: item.id, link: item.link };
}

const LOCAL_INTERACTIONS_KEY = "paper-feed:interactions";

function loadLocalInteractions() {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_INTERACTIONS_KEY) || "null");
  } catch (_) {
    return null;
  }
}

function saveLocalInteractions() {
  try { localStorage.setItem(LOCAL_INTERACTIONS_KEY, JSON.stringify(state.interactions)); } catch (_) { /* storage is optional */ }
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
}

async function loadInteractions() {
  try {
    const res = await fetch("/api/interactions?t=" + Date.now(), {
      cache: 'no-store'
    });
    if (res.ok) {
      state.interactions = await res.json();
      normalizeInteractions();
      saveLocalInteractions();
      setOfflineMode(false);
      return true;
    }
  } catch (e) {
    console.warn("Failed to load interactions", e);
  }
  const local = loadLocalInteractions();
  if (local) {
    state.interactions = local;
    normalizeInteractions();
  }
  setOfflineMode(true);
  return false;
}

function applyInteractionAction(id, action) {
  const lists = state.interactions;
  lists.favorites = ensureArray(lists.favorites).filter((x) => x !== id);
  lists.archived = ensureArray(lists.archived).filter((x) => x !== id);
  lists.hidden = ensureArray(lists.hidden).filter((x) => x !== id);
  if (action === "like" || action === "restore") lists.favorites.push(id);
  if (action === "archive") lists.archived.push(id);
  if (action === "hide") lists.hidden.push(id);
  normalizeInteractions();
}

async function saveInteraction(item, action) {
  const id = paperKey(item);
  if (!id) throw new Error("论文缺少可用标识，无法保存操作。");
  if (item.paper_id && state.paperApiAvailable) {
    const res = await fetch(`/api/papers/${encodeURIComponent(item.paper_id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action })
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).message || "论文状态保存失败");
    const payload = await res.json();
    if (payload.interactions) {
      state.interactions = payload.interactions;
      normalizeInteractions();
      saveLocalInteractions();
    }
    return payload;
  }
  // Static GitHub Pages / legacy feed compatibility.
  try {
    const res = await fetch("/api/interactions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...legacyReference(item), id, action })
    });
    if (!res.ok) throw new Error("旧互动接口不可用");
    const payload = await res.json();
    if (payload && payload.favorites) state.interactions = payload;
  } catch (e) {
    if (!state.paperApiAvailable) {
      saveLocalInteractions();
      setOfflineMode(true);
      return null;
    }
    throw e;
  }
}

function interactionSets() {
  return {
    favorites: new Set(ensureArray(state.interactions.favorites)),
    archived: new Set(ensureArray(state.interactions.archived)),
    hidden: new Set(ensureArray(state.interactions.hidden))
  };
}

function reviewStateOf(id, sets = interactionSets()) {
  if (sets.hidden.has(id)) return "hidden";
  if (sets.favorites.has(id)) return "favorite";
  if (sets.archived.has(id)) return "archived";
  return "inbox";
}

function belongsToView(id, mode = state.filterMode, sets = interactionSets()) {
  const reviewState = reviewStateOf(id, sets);
  switch (mode) {
    case "favorites": return reviewState === "favorite";
    case "archived": return reviewState === "archived";
    case "hidden": return reviewState === "hidden";
    case "everything": return true;
    default: return reviewState === "inbox";
  }
}

// The server action that moves a paper back into `targetState`; used for undo
// and for rolling back a failed optimistic write.
function actionToReach(targetState, performedAction) {
  if (targetState === "favorite") return "like";
  if (targetState === "archived") return "archive";
  if (targetState === "hidden") return "hide";
  return ({ like: "unlike", restore: "unlike", archive: "unarchive", hide: "unhide" })[performedAction] || "unhide";
}

function rerenderPreservingScroll() {
  const hasWindow = typeof window !== "undefined";
  const scrollY = hasWindow ? window.scrollY : 0;
  renderList();
  if (hasWindow && !shouldUseSwipeDeck() && typeof window.scrollTo === "function") window.scrollTo(0, scrollY);
}

// Removes items that no longer belong to the current view (e.g. a favorited
// paper in 待筛选) without resetting pagination or scroll position.
function refreshAfterInteraction() {
  const sets = interactionSets();
  state.filtered = state.filtered.filter((item) => belongsToView(paperKey(item), state.filterMode, sets));
  rerenderPreservingScroll();
  updateFilterCounts();
  updateCountLabel();
}

function reinsertItem(item, index) {
  const id = paperKey(item);
  if (!belongsToView(id)) return;
  if (state.filtered.some((candidate) => paperKey(candidate) === id)) return;
  const position = Math.max(0, Math.min(index || 0, state.filtered.length));
  state.filtered.splice(position, 0, item);
  if (shouldUseSwipeDeck()) state.swipeIndex = position;
}

function pushUndo(record) {
  if (!record.view) record.view = state.filterMode;
  state.undoStack.push(record);
  if (state.undoStack.length > MAX_UNDO_STACK_SIZE) state.undoStack.shift();
  renderUndoStack();
}

function removeUndoRecord(record) {
  state.undoStack = state.undoStack.filter((entry) => entry !== record);
}

function performInteraction(item, action) {
  const id = paperKey(item);
  if (!id) return;
  const previousState = reviewStateOf(id);
  const index = state.filtered.findIndex((candidate) => paperKey(candidate) === id);
  const record = { item, id, action, undoAction: actionToReach(previousState, action), index: index >= 0 ? index : 0 };
  applyInteractionAction(id, action);
  pushUndo(record);
  refreshAfterInteraction();
  state.pendingWrites += 1;
  saveInteraction(item, action).then(() => {
    updateFilterCounts();
  }).catch((error) => {
    applyInteractionAction(id, record.undoAction);
    removeUndoRecord(record);
    reinsertItem(item, record.index);
    refreshAfterInteraction();
    renderUndoStack();
    setStatus(`操作未保存，已恢复原状态：${error.message}`);
    showToast(`操作失败，已恢复原状态：${error.message}`, "error");
  }).finally(() => {
    state.pendingWrites = Math.max(0, state.pendingWrites - 1);
  });
}

function toggleLike(item) {
  const id = paperKey(item);
  const isLiked = state.interactions.favorites.includes(id);
  const action = isLiked ? 'unlike' : 'like';
  performInteraction(item, action);
}

function toggleHide(item) {
  performInteraction(item, "hide");
}

function unhideItem(item) {
  performInteraction(item, "unhide");
}

// Inbox deck and list share one bounded history so several decisions can be
// undone one by one.  The paper object is retained only for rendering; all
// state and server writes use paperKey(item), which prefers the durable paper_id.
function shouldUseSwipeDeck() {
  return state.filterMode === "all" && state.inboxViewMode === "swipe";
}

function clearUndoBar({ clearStack = false } = {}) {
  if (clearStack) state.undoStack = [];
  const container = document.getElementById("undoContainer");
  if (container) container.textContent = "";
}

function undoMessage(action) {
  return ({
    like: "已收藏文章",
    hide: "已标记不感兴趣（可在“已隐藏”中恢复）",
    archive: "已归档文章",
    unlike: "已取消收藏",
    unarchive: "已移回待筛选",
    unhide: "已恢复到待筛选",
    restore: "已恢复到收藏"
  })[action] || "已更新文章";
}

function renderUndoStack() {
  const container = document.getElementById("undoContainer");
  if (!container) return;
  container.textContent = "";
  const record = state.undoStack[state.undoStack.length - 1];
  if (!record) return;
  const bar = document.createElement("div");
  bar.className = "undo-bar";
  const message = document.createElement("span");
  const origin = record.view && record.view !== state.filterMode && VIEW_LABELS[record.view] ? `（在“${VIEW_LABELS[record.view]}”视图）` : "";
  const text = `${undoMessage(record.action)}${origin}`;
  message.textContent = state.undoStack.length > 1 ? `${text} · ${state.undoStack.length} 项可撤销` : text;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "undo-btn";
  button.textContent = state.undoStack.length > 1 ? `撤销 (${state.undoStack.length}) · Z` : "撤销 · Z";
  button.onclick = undoLastInteraction;
  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.className = "undo-dismiss";
  dismiss.textContent = "✕";
  dismiss.title = "关闭（清空撤销记录）";
  dismiss.setAttribute("aria-label", "关闭撤销提示");
  dismiss.onclick = () => clearUndoBar({ clearStack: true });
  bar.append(message, button, dismiss);
  container.appendChild(bar);
}

function currentSwipeItem() {
  state.swipeIndex = Math.max(0, Math.min(state.swipeIndex, Math.max(0, state.filtered.length - 1)));
  return state.filtered[state.swipeIndex] || null;
}

function removeSwipeItem(item, index) {
  const id = paperKey(item);
  const found = state.filtered.findIndex((candidate) => paperKey(candidate) === id);
  state.filtered.splice(found >= 0 ? found : index, 1);
  state.swipeIndex = Math.min(index, Math.max(0, state.filtered.length - 1));
}

function setSwipeBusy(busy, message = "") {
  state.swipeBusy = busy;
  document.querySelectorAll(".swipe-action, .undo-btn").forEach((button) => {
    button.disabled = busy;
    button.setAttribute("aria-busy", busy ? "true" : "false");
  });
  if (message) setStatus(message);
}

function commitSwipeAction(action, direction) {
  if (!shouldUseSwipeDeck() || state.swipeBusy) return;
  const item = currentSwipeItem();
  const id = paperKey(item);
  if (!item || !id) return;
  setSwipeBusy(true);
  const index = state.swipeIndex;
  const previousState = reviewStateOf(id);
  const card = elements.list.querySelector(".swipe-card--current");
  if (card) card.classList.add(direction === "right" ? "swipe-card--leaving-right" : "swipe-card--leaving-left");
  setTimeout(() => {
    applyInteractionAction(id, action);
    removeSwipeItem(item, index);
    const record = { item, id, action, undoAction: actionToReach(previousState, action), index };
    pushUndo(record);
    renderList();
    updateFilterCounts();
    updateCountLabel();
    saveInteraction(item, action).catch((error) => {
      applyInteractionAction(id, record.undoAction);
      removeUndoRecord(record);
      reinsertItem(item, index);
      renderList();
      updateFilterCounts();
      updateCountLabel();
      renderUndoStack();
      setStatus(`操作未保存，已恢复原状态：${error.message}`);
      showToast(`操作失败，已恢复原状态：${error.message}`, "error");
    }).finally(() => { setSwipeBusy(false); });
  }, card ? 180 : 0);
}

function undoLastInteraction() {
  if (state.swipeBusy || state.pendingWrites > 0) {
    setStatus("正在保存上一项操作，请稍候再撤销。");
    return;
  }
  const record = state.undoStack.pop();
  if (!record) return clearUndoBar();
  setSwipeBusy(true, "正在保存撤销…");
  // Records may predate a feed reload; prefer the live object for this paper_id.
  const liveItem = state.items.find((candidate) => paperKey(candidate) === record.id);
  if (liveItem) record.item = liveItem;
  const stateBeforeUndo = reviewStateOf(record.id);
  applyInteractionAction(record.id, record.undoAction);
  reinsertItem(record.item, record.index);
  if (shouldUseSwipeDeck()) renderList(); else rerenderPreservingScroll();
  updateFilterCounts();
  updateCountLabel();
  renderUndoStack();
  saveInteraction(record.item, record.undoAction).then(() => {
    setStatus("已撤销。");
    notifyUndoElsewhere(record);
  }).catch((error) => {
    applyInteractionAction(record.id, actionToReach(stateBeforeUndo, record.undoAction));
    const sets = interactionSets();
    state.filtered = state.filtered.filter((item) => belongsToView(paperKey(item), state.filterMode, sets));
    state.undoStack.push(record);
    renderList();
    updateFilterCounts();
    updateCountLabel();
    renderUndoStack();
    setStatus(`撤销未保存，已恢复原状态：${error.message}`);
    showToast(`撤销失败，已恢复原状态：${error.message}`, "error");
  }).finally(() => { setSwipeBusy(false); });
}

// After undoing a decision made in another view, the paper may not be visible
// here; say where it went and offer to jump there.
function undoTargetView(record) {
  if (!record || belongsToView(record.id)) return null;
  return VIEW_OF_REVIEW_STATE[reviewStateOf(record.id)] || null;
}

function notifyUndoElsewhere(record) {
  const target = undoTargetView(record);
  if (!target) return null;
  const title = truncateText((record.item && record.item.title) || "未命名论文", 60);
  const message = `已撤销：${title}（现在位于“${VIEW_LABELS[target]}”视图）`;
  setStatus(message);
  showToast(message, "info", 8000, {
    label: `前往${VIEW_LABELS[target]}`,
    onClick: () => {
      if (state.swipeBusy) return;
      setFilterMode(target);
      applyFilters();
      restoreViewPosition();
    }
  });
  return target;
}

function toggleArchive(item) {
  const id = paperKey(item);
  const isArchived = state.interactions.archived.includes(id);
  const action = isArchived ? "unarchive" : "archive";
  performInteraction(item, action);
}

function restoreFromArchive(item) {
  performInteraction(item, "restore");
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

// --- Checkbox popover used for the method / topic filters ---
// The container holds one checkbox per category plus a "全部" checkbox with an
// empty value.  Choosing 全部 clears the others; clearing every specific
// choice falls back to 全部, mirroring the old <select multiple> semantics.

function updateMultiFilterSummary(container) {
  if (!container || container.tagName === "SELECT" || !container.id) return;
  const summary = document.getElementById(`${container.id}Summary`);
  if (!summary) return;
  const values = getSelectedFilterValues(container);
  const allLabel = (container.dataset && container.dataset.allLabel) || "全部";
  if (!values.length) summary.textContent = allLabel;
  else if (values.length <= 2) summary.textContent = values.join("、");
  else summary.textContent = `已选 ${values.length} 项`;
  summary.title = values.length ? values.join("、") : allLabel;
}

function normalizeCheckboxFilter(container, changedInput) {
  if (!container || container.tagName === "SELECT" || typeof container.querySelectorAll !== "function") return;
  const inputs = Array.from(container.querySelectorAll("input[type='checkbox']"));
  const allInput = inputs.find((input) => input.value === "");
  const others = inputs.filter((input) => input.value !== "");
  if (changedInput && changedInput.value === "") {
    others.forEach((input) => { input.checked = false; });
    if (allInput) allInput.checked = true;
  } else if (allInput) {
    allInput.checked = !others.some((input) => input.checked);
  }
  updateMultiFilterSummary(container);
}

function buildFilterCheckboxes(container, entries, selected) {
  if (!container) return;
  container.textContent = "";
  const fragment = document.createDocumentFragment();
  const allLabel = (container.dataset && container.dataset.allLabel) || "全部";
  const makeOption = (value, text) => {
    const label = document.createElement("label");
    label.className = value === "" ? "multi-filter__option multi-filter__option--all" : "multi-filter__option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = value;
    input.checked = value === "" ? selected.size === 0 : selected.has(value);
    const span = document.createElement("span");
    span.textContent = text;
    label.append(input, span);
    fragment.appendChild(label);
  };
  makeOption("", allLabel);
  entries.forEach((entry) => makeOption(entry.value, entry.text));
  container.appendChild(fragment);
  normalizeCheckboxFilter(container, null);
}

function setupMultiFilterDropdowns() {
  const dropdowns = Array.from(document.querySelectorAll(".multi-filter"));
  if (!dropdowns.length) return;
  dropdowns.forEach((dropdown) => {
    dropdown.addEventListener("toggle", () => {
      if (!dropdown.open) return;
      dropdowns.forEach((other) => { if (other !== dropdown) other.open = false; });
    });
  });
  document.addEventListener("click", (event) => {
    dropdowns.forEach((dropdown) => {
      if (dropdown.open && event.target instanceof Element && !dropdown.contains(event.target)) dropdown.open = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    dropdowns.forEach((dropdown) => {
      if (!dropdown.open) return;
      const hadFocus = dropdown.contains(document.activeElement);
      dropdown.open = false;
      const summary = dropdown.querySelector("summary");
      if (hadFocus && summary) summary.focus();
    });
  });
}

// Selects and checkboxes keep focus after a change, which would make the
// browser (not the triage shortcuts) handle ←/→ next.  Text fields keep focus.
function releaseFilterFocus(target) {
  if (!target || typeof target.blur !== "function") return;
  const isCheckbox = target.tagName === "INPUT" && (target.type === "checkbox" || target.type === "radio");
  if (target.tagName === "SELECT" || isCheckbox) target.blur();
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
  normalizeCheckboxFilter(container, null);
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
    ...(state.interactions.favorites || []),
    ...(state.interactions.archived || [])
  ]);
  const counter = new Map();
  state.items.forEach((item) => {
    if (!favorites.has(paperKey(item))) return;
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

function updateFilterCounts() {
  const favorites = new Set(state.interactions.favorites);
  const archived = new Set(state.interactions.archived);
  const hidden = new Set(state.interactions.hidden);

  let inboxCount = 0;
  // Inbox is items NOT in favorites, archived, hidden
  state.items.forEach(item => {
    if (!favorites.has(paperKey(item)) && !archived.has(paperKey(item)) && !hidden.has(paperKey(item))) {
      inboxCount++;
    }
  });
  
  // Count only papers that are actually loaded so the tab badges match the list.
  let favCount = 0;
  let archCount = 0;
  let hiddenCount = 0;
  state.items.forEach((item) => {
    const key = paperKey(item);
    if (hidden.has(key)) hiddenCount++;
    else if (favorites.has(key)) favCount++;
    else if (archived.has(key)) archCount++;
  });

  const counts = {
    countInbox: inboxCount,
    countFavorites: favCount,
    countArchived: archCount,
    countHidden: hiddenCount,
    countAll: state.items.length
  };
  Object.entries(counts).forEach(([elementId, value]) => {
    const el = document.getElementById(elementId);
    if (el) el.textContent = String(value);
  });
}

// Abstract provenance badges.  `gpt_generated` is a title-only guess and is
// deliberately styled differently from a real AI summary of an abstract.
const ABSTRACT_SOURCE_BADGES = {
  crossref: { key: "crossref", label: "📚 Crossref", color: "#2196F3" },
  semantic_scholar: { key: "semantic_scholar", label: "🔬 Semantic Scholar", color: "#9C27B0" },
  openalex: { key: "openalex", label: "📖 OpenAlex", color: "#0F766E" },
  gpt_generated: { key: "gpt_generated", label: "⚠ 基于标题推测", color: "#78716c", tooltip: "未读取摘要，仅根据标题推测" },
  gpt_summarized: { key: "gpt_summarized", label: "🤖 AI 总结", color: "#FF9800" },
  user_provided: { key: "user_provided", label: "✏️ 用户补充", color: "#4CAF50" }
};
const ABSTRACT_SOURCE_DEFAULT = { key: "default", label: "📄 摘要", color: "#757575" };

function abstractSourceOf(item) {
  return ABSTRACT_SOURCE_BADGES[item && item.abstract_source] || ABSTRACT_SOURCE_DEFAULT;
}

function createAbstractSourceBadge(item) {
  const source = abstractSourceOf(item);
  const badge = document.createElement("span");
  badge.className = `abstract-badge abstract-badge--${source.key}`;
  badge.style.background = source.color;
  badge.textContent = source.label;
  if (source.tooltip) badge.title = source.tooltip;
  return badge;
}

// Accepts bare DOIs as well as doi: / https://doi.org/ forms.
function normalizeDoi(value) {
  if (typeof value !== "string") return "";
  return value.trim().replace(/^doi:\s*/i, "").replace(/^https?:\/\/(dx\.)?doi\.org\//i, "").trim();
}

function createDoiLink(item) {
  const doi = normalizeDoi(item && item.doi);
  if (!doi) return null;
  const link = document.createElement("a");
  link.className = "doi-link";
  link.href = `https://doi.org/${encodeURIComponent(doi).replace(/%2F/gi, "/")}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "DOI";
  link.title = `DOI: ${doi}`;
  link.setAttribute("aria-label", `通过 DOI 打开：${doi}`);
  link.draggable = false;
  return link;
}

// AI taste score (0-100) from the current taste profile, or null when the
// paper has not been scored.  Personal data: only comes from /api/papers.
const SORT_VALUES = ["desc", "asc", "taste"];

function tasteScoreOf(item) {
  const value = item ? item.taste_score : null;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function tasteBand(score) {
  if (score >= 75) return "high";
  if (score >= 50) return "mid";
  return "low";
}

function createTasteBadge(item) {
  const score = tasteScoreOf(item);
  if (score === null) return null;
  const badge = document.createElement("span");
  badge.className = `meta-badge taste-badge taste-badge--${tasteBand(score)}`;
  badge.textContent = `匹配 ${score}`;
  const reason = typeof item.taste_reason === "string" ? item.taste_reason.trim() : "";
  badge.title = reason ? `AI 匹配度 ${score}：${reason}` : `AI 匹配度 ${score}`;
  badge.setAttribute("aria-label", badge.title);
  return badge;
}

// Shared by the list and swipe cards: method/topic badges plus the
// user-correction tag.
function appendClassificationBadges(container, item) {
  const describe = (entries) => (entries || [])
    .map((entry) => `${entry.name} (${Math.round((entry.confidence || 0) * 100)}%)`)
    .join(", ");
  const methodSummary = describe(item.methods);
  const topicSummary = describe(item.topics);
  (item.methods || []).forEach((entry) => appendBadge(container, "method", entry, { title: methodSummary }));
  (item.topics || []).forEach((entry) => appendBadge(container, "topic", entry, { title: topicSummary }));
  if (item.user_corrected) appendTagBadge(container, "用户修正");
}

// Offered by empty states whenever a filter may be hiding papers.
function createClearFiltersButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn--secondary btn--small empty-state__action";
  button.textContent = "清除筛选";
  button.onclick = () => { clearAllFilters(); applyFilters(); };
  return button;
}

function createSwipeAction(label, action, direction) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `swipe-action swipe-action--${direction}`;
  button.textContent = label;
  button.onclick = () => commitSwipeAction(action, direction);
  return button;
}

function renderSwipeDeck() {
  elements.list.textContent = "";
  elements.list.classList.add("grid--swipe");
  const item = currentSwipeItem();
  if (item) rememberSwipePosition(item);
  if (!item) {
    const empty = document.createElement("div");
    empty.className = "swipe-empty";
    if (countActiveFilters() > 0) {
      empty.textContent = "当前筛选条件下没有待筛选的文献，其余文献可能被筛选条件隐藏了。";
      const hint = document.createElement("span");
      hint.className = "empty-state__hint";
      hint.textContent = "清除筛选后可继续刷卡。";
      empty.append(hint, createClearFiltersButton());
    } else {
      empty.textContent = "暂时没有新的文献了。切换到收藏或归档可继续处理。";
    }
    elements.list.appendChild(empty);
    return;
  }
  const shell = document.createElement("div");
  shell.className = "swipe-shell";
  const deck = document.createElement("div");
  deck.className = "swipe-deck";
  const next = state.filtered[state.swipeIndex + 1];
  if (next) {
    const preview = document.createElement("article");
    preview.className = "swipe-card swipe-card--preview";
    preview.textContent = next.title || "Untitled";
    deck.appendChild(preview);
  }
  const card = document.createElement("article");
  card.className = "swipe-card swipe-card--current";
  const meta = document.createElement("div");
  meta.className = "swipe-card__meta";
  meta.textContent = `${item.journal || "Unknown"} · ${formatDate(item.date)}`;
  const title = document.createElement("a");
  title.className = "swipe-card__title";
  title.href = item.link || "#";
  title.target = "_blank";
  title.rel = "noreferrer";
  title.textContent = item.title || "Untitled";
  const titleZh = document.createElement("div");
  titleZh.className = "swipe-card__title-zh";
  titleZh.textContent = item.title_zh || "";
  const badges = document.createElement("div");
  badges.className = "swipe-card__badges";
  const tasteBadge = createTasteBadge(item);
  if (tasteBadge) badges.appendChild(tasteBadge);
  appendClassificationBadges(badges, item);
  const showAbstract = elements.summaryToggle.checked && Boolean(item.abstract);
  if (showAbstract) badges.appendChild(createAbstractSourceBadge(item));
  const doiLink = createDoiLink(item);
  if (doiLink) badges.appendChild(doiLink);
  const authors = document.createElement("div");
  authors.className = "swipe-card__authors";
  authors.textContent = item.authors ? `作者：${item.authors}` : "";
  const abstract = document.createElement("p");
  abstract.className = "swipe-card__abstract";
  abstract.textContent = showAbstract ? truncateText(item.abstract, 520) : "";
  if (item.abstract_source === "gpt_generated") {
    abstract.classList.add("abstract-body--guess");
    abstract.title = "未读取摘要，仅根据标题推测";
  }
  card.append(meta, title, titleZh);
  if (item.authors) card.appendChild(authors);
  if (badges.children && badges.children.length) card.appendChild(badges);
  card.appendChild(abstract);
  attachSwipeGesture(card, title);
  deck.appendChild(card);
  const actions = document.createElement("div");
  actions.className = "swipe-actions";
  actions.append(
    createSwipeAction("← 不感兴趣", "hide", "left"),
    createSwipeAction("归档", "archive", "archive"),
    createSwipeAction("收藏 →", "like", "right")
  );
  const progress = document.createElement("div");
  progress.className = "swipe-progress";
  progress.textContent = `${state.swipeIndex + 1} / ${state.filtered.length}`;
  const hint = document.createElement("div");
  hint.className = "swipe-hint";
  hint.textContent = "可左右拖动卡片 · 键盘：← 不感兴趣 · → 收藏 · A 归档 · Z 撤销 · ? 全部快捷键";
  shell.append(deck, actions, progress, hint);
  elements.list.appendChild(shell);
}

// Basic pointer/touch swipe: drag left = 不感兴趣, drag right = 收藏.
const SWIPE_COMMIT_PX = 110;

function attachSwipeGesture(card, titleLink) {
  if (!card || typeof card.addEventListener !== "function") return;
  let startX = null;
  let startY = 0;
  let deltaX = 0;
  let dragging = false;
  let suppressClick = false;
  if (titleLink) titleLink.draggable = false;

  const reset = () => {
    startX = null;
    deltaX = 0;
    dragging = false;
    card.style.transform = "";
    card.style.transition = "";
    card.classList.remove("swipe-card--drag-left", "swipe-card--drag-right");
  };

  card.addEventListener("pointerdown", (event) => {
    if (state.swipeBusy || (event.pointerType === "mouse" && event.button !== 0)) return;
    startX = event.clientX;
    startY = event.clientY;
    deltaX = 0;
    dragging = false;
    suppressClick = false;
  });
  card.addEventListener("pointermove", (event) => {
    if (startX === null) return;
    deltaX = event.clientX - startX;
    const deltaY = event.clientY - startY;
    if (!dragging) {
      if (Math.abs(deltaX) < 10 || Math.abs(deltaX) < Math.abs(deltaY)) return;
      dragging = true;
      try { card.setPointerCapture(event.pointerId); } catch (_) { /* optional */ }
    }
    card.style.transition = "none";
    card.style.transform = `translateX(${deltaX}px) rotate(${deltaX / 40}deg)`;
    card.classList.toggle("swipe-card--drag-left", deltaX < -SWIPE_COMMIT_PX / 2);
    card.classList.toggle("swipe-card--drag-right", deltaX > SWIPE_COMMIT_PX / 2);
  });
  const finish = () => {
    if (startX === null) return;
    const wasDragging = dragging;
    const finalDelta = deltaX;
    reset();
    if (!wasDragging) return;
    suppressClick = true;
    if (finalDelta <= -SWIPE_COMMIT_PX) commitSwipeAction("hide", "left");
    else if (finalDelta >= SWIPE_COMMIT_PX) commitSwipeAction("like", "right");
  };
  card.addEventListener("pointerup", finish);
  card.addEventListener("pointercancel", reset);
  // A drag that ends on the title must not also open the paper.
  card.addEventListener("click", (event) => {
    if (suppressClick) {
      event.preventDefault();
      event.stopPropagation();
      suppressClick = false;
    }
  }, true);
}

// --- Abstract edit drafts ---
// The inline editor is rebuilt on every renderList(); drafts keyed by
// paper_id (mirrored to localStorage) reopen it with the typed text.
// A draft exists exactly while its editor is open.  `base` is the prefill,
// so an untouched editor does not count as unsaved work.
const abstractDrafts = loadAbstractDrafts();

function loadAbstractDrafts() {
  const drafts = new Map();
  try {
    const parsed = JSON.parse(safeStorageGet(ABSTRACT_DRAFTS_KEY) || "null");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      Object.entries(parsed).forEach(([key, draft]) => {
        if (key && draft && typeof draft.text === "string") {
          drafts.set(key, { text: draft.text, base: typeof draft.base === "string" ? draft.base : "" });
        }
      });
    }
  } catch (_) { /* corrupt drafts are dropped */ }
  return drafts;
}

function persistAbstractDrafts() {
  safeStorageSet(ABSTRACT_DRAFTS_KEY, JSON.stringify(Object.fromEntries(abstractDrafts)));
}

function setAbstractDraft(key, text, base) {
  if (!key) return;
  const previous = abstractDrafts.get(key);
  abstractDrafts.set(key, { text: String(text || ""), base: base !== undefined ? String(base || "") : (previous ? previous.base : "") });
  persistAbstractDrafts();
}

function clearAbstractDraft(key) {
  if (!abstractDrafts.delete(key)) return;
  persistAbstractDrafts();
}

function hasUnsavedAbstractDrafts() {
  return Array.from(abstractDrafts.values()).some((draft) => draft.text.trim() && draft.text !== draft.base);
}

function setupDraftUnloadWarning() {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") return;
  window.addEventListener("beforeunload", (event) => {
    if (!hasUnsavedAbstractDrafts()) return;
    event.preventDefault();
    event.returnValue = "有尚未保存的摘要草稿。";
  });
}

function abstractPrefill(item) {
  if (item.raw_abstract) return item.raw_abstract;
  // A title-only guess is not source material: let the user paste the real one.
  if (item.abstract_source === "gpt_generated") return "";
  return item.abstract || "";
}

function renderList() {
  if (shouldUseSwipeDeck()) return renderSwipeDeck();
  elements.list.classList.remove("grid--swipe");
  elements.list.innerHTML = "";
  const showSummary = elements.summaryToggle.checked;
  const highlightTerms = getHighlightTerms();

  if (state.filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "card";
    const hasFilters = countActiveFilters() > 0;
    if (state.filterMode === "favorites") {
      empty.textContent = "还没有收藏任何文章。";
    } else if (state.filterMode === "archived") {
      empty.textContent = "暂无已归档文章。";
    } else if (state.filterMode === "hidden") {
      empty.textContent = "没有已隐藏的文章。标记为“不感兴趣”的文章会出现在这里，可随时恢复。";
    } else if (state.filterMode === "everything") {
      empty.textContent = "没有符合条件的文章。";
    } else {
      empty.textContent = "暂时没有新的文献了...";
    }
    if (hasFilters) {
      const hint = document.createElement("span");
      hint.className = "empty-state__hint";
      hint.textContent = "当前有筛选条件生效，部分文章可能被隐藏。";
      empty.append(hint, createClearFiltersButton());
    }
    elements.list.appendChild(empty);
    updateLoadMoreButton();
    return;
  }

  // Use DocumentFragment for batch DOM insertion (1000x faster!)
  const fragment = document.createDocumentFragment();

  const visibleItems = state.filtered.slice(0, state.visibleLimit);
  const isInbox = state.filterMode === "all";
  state.listCursor = Math.max(0, Math.min(state.listCursor, visibleItems.length - 1));
  let cardIndex = -1;
  for (const item of visibleItems) {
    cardIndex += 1;
    const node = elements.cardTemplate.content.cloneNode(true);
    const card = node.querySelector(".card");
    card.dataset.cardIndex = String(cardIndex);
    if (isInbox) {
      card.classList.add("card--compact");
      if (cardIndex === state.listCursor) card.classList.add("card--cursor");
      card.addEventListener("click", () => setListCursor(Number(card.dataset.cardIndex), false));
    }
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
    metaInfo.appendChild(metaText);
    
    // 2. Append Badges (taste score, Method & Topic), then the DOI link
    const tasteBadge = createTasteBadge(item);
    if (tasteBadge) metaInfo.appendChild(tasteBadge);
    appendClassificationBadges(metaInfo, item);
    const doiLink = createDoiLink(item);
    if (doiLink) metaInfo.appendChild(doiLink);
    if (state.filterMode === "everything") {
      const reviewLabel = { favorite: "已收藏", archived: "已归档", hidden: "已隐藏" }[reviewStateOf(paperKey(item))];
      if (reviewLabel) appendTagBadge(metaInfo, reviewLabel);
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
    appendField(fields, "理论", item.theoriesText, highlightTerms);
    appendField(fields, "情境", item.contextText, highlightTerms);
    appendField(fields, "对象", item.subjectsText, highlightTerms);
    if (!fields.children.length) {
      fields.remove();
    }

    // Display abstract if available
    if (showSummary && item.abstract) {
      abstractDiv.className = "card__abstract";

      const source = abstractSourceOf(item);
      const tooltip = source.tooltip ? ` title="${escapeHtml(source.tooltip)}"` : "";

      abstractDiv.innerHTML = `
        <div class="abstract-badge-row">
          <span class="abstract-badge abstract-badge--${source.key}" style="background: ${source.color};"${tooltip}>
            ${source.label}
          </span>
        </div>
        <div class="abstract-body abstract-body--${source.key}" style="border-left-color: ${source.color};"${tooltip}>
          ${highlightText(item.abstract, highlightTerms)}
        </div>
      `;
      abstractDiv.style.display = "block";
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

    const isLiked = state.interactions.favorites.includes(paperKey(item));
    const isArchived = state.interactions.archived.includes(paperKey(item));
    const isHidden = state.interactions.hidden.includes(paperKey(item));

    const makeActionButton = (label, title, extraClass = "") => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `action-btn ${extraClass}`.trim();
      button.textContent = label;
      button.title = title;
      button.setAttribute("aria-label", title);
      return button;
    };

    const btnLike = makeActionButton(isInbox ? "收藏" : (isLiked ? "❤️" : "🤍"), isLiked ? "取消收藏" : "收藏", isLiked ? "liked" : "");
    btnLike.dataset.triageAction = "favorite";
    btnLike.onclick = function(e) { e.preventDefault(); toggleLike(item); };

    const btnArchive = makeActionButton(
      isInbox ? "归档" : (isArchived ? "📤" : "📦"),
      isArchived ? "取消归档（移回待筛选）" : state.filterMode === "favorites" ? "归档（移出收藏）" : "归档"
    );
    btnArchive.dataset.triageAction = "archive";
    btnArchive.onclick = function(e) { e.preventDefault(); toggleArchive(item); };

    const btnRestore = makeActionButton("↩️", "恢复到收藏");
    btnRestore.onclick = function(e) { e.preventDefault(); restoreFromArchive(item); };

    const btnUnhide = makeActionButton("恢复", "恢复到待筛选", "action-btn--text");
    btnUnhide.onclick = function(e) { e.preventDefault(); unhideItem(item); };

    const btnClassify = makeActionButton("🏷️", "编辑分类", "action-btn--secondary");
    btnClassify.onclick = function(e) {
      e.preventDefault();
      openClassificationModal(item);
    };

    // Edit Abstract Button
    const btnEdit = makeActionButton("✏️", "补充/编辑摘要", "action-btn--secondary");

    // Edit Area Elements
    const editArea = node.querySelector(".card__edit-area");
    const textarea = editArea.querySelector("textarea");
    const editError = editArea.querySelector(".card__edit-error");
    const btnSave = editArea.querySelector(".btn-save-abstract");
    const btnCancel = editArea.querySelector(".btn-cancel-abstract");
    const showEditError = (message) => {
      if (!editError) return;
      editError.textContent = message || "";
      editError.hidden = !message;
    };

    const draftKey = paperKey(item);
    const draft = draftKey ? abstractDrafts.get(draftKey) : null;
    if (draft) {
        // Reopen an editor that a re-render (filter, job reload, …) closed.
        editArea.style.display = "block";
        textarea.value = draft.text;
    }
    textarea.addEventListener("input", () => setAbstractDraft(draftKey, textarea.value));

    btnEdit.onclick = function(e) {
        e.preventDefault();
        // Toggle visibility
        if (editArea.style.display === "none") {
            editArea.style.display = "block";
            showEditError("");
            // Prefer the raw source material over AI output.
            const prefillValue = abstractPrefill(item);
            textarea.value = prefillValue;
            setAbstractDraft(draftKey, prefillValue, prefillValue);
            textarea.focus();
        } else {
            editArea.style.display = "none";
            clearAbstractDraft(draftKey);
        }
    };

    btnCancel.onclick = function() {
        editArea.style.display = "none";
        clearAbstractDraft(draftKey);
    };

    btnSave.onclick = async function() {
        const newText = textarea.value.trim();
        if (!newText && (item.abstract || item.raw_abstract) && !confirm("摘要为空，保存后将清除该文章的摘要。确定吗？")) {
            return;
        }

        btnSave.disabled = true;
        btnSave.textContent = "保存中...";
        showEditError("");

        try {
            const res = await fetch("/api/update_abstract", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    ...legacyReference(item),
                    abstract: newText
                })
            });

            if (res.ok) {
                // Update local state temporarily so UI reflects change without full reload
                item.abstract = newText;
                item.raw_abstract = newText; // Also update raw so next edit shows this
                item.abstract_source = newText ? "user_provided" : "";
                clearAbstractDraft(draftKey);
                setStatus(newText ? "摘要已保存。" : "摘要已清除。");
                rerenderPreservingScroll();
            } else {
                const payload = await res.json().catch(() => ({}));
                showEditError(`保存失败：${payload.message || `HTTP ${res.status}`}`);
            }
        } catch (e) {
            showEditError(`保存失败：${e.message || "网络错误"}`);
        } finally {
            btnSave.disabled = false;
            btnSave.textContent = "保存";
        }
    };

    const btnHide = makeActionButton(isInbox ? "不感兴趣" : "❌", "不感兴趣（移到“已隐藏”，可恢复）");
    btnHide.dataset.triageAction = "hide";
    btnHide.onclick = function(e) {
      e.preventDefault();
      toggleHide(item);
    };

    actionsDiv.appendChild(btnClassify);
    actionsDiv.appendChild(btnEdit); // Add Edit button
    if (isInbox) {
      const btnDetails = makeActionButton("详情", "展开详情", "action-btn--details");
      btnDetails.setAttribute("aria-expanded", "false");
      btnDetails.onclick = () => {
        const expanded = card.classList.toggle("is-expanded");
        btnDetails.textContent = expanded ? "收起" : "详情";
        btnDetails.setAttribute("aria-expanded", String(expanded));
      };
      actionsDiv.appendChild(btnDetails);
    }

    // Explicit Button Logic
    if (state.filterMode === "favorites") {
      actionsDiv.appendChild(btnArchive); // Show Archive Button in Favorites
      actionsDiv.appendChild(btnLike);
      actionsDiv.appendChild(btnHide);
    } else if (state.filterMode === "archived") {
      actionsDiv.appendChild(btnRestore); // Restore to Favorites
      actionsDiv.appendChild(btnArchive); // Unarchive (to Inbox)
      actionsDiv.appendChild(btnHide);
    } else if (state.filterMode === "hidden" || (state.filterMode === "everything" && isHidden)) {
      actionsDiv.appendChild(btnUnhide);
    } else {
      // Inbox or everything
      actionsDiv.appendChild(btnLike);
      actionsDiv.appendChild(btnArchive);
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
  updateLoadMoreButton();
}

function updateLoadMoreButton() {
  if (!elements.loadMore) return;
  const remaining = state.filtered.length - state.visibleLimit;
  elements.loadMore.hidden = remaining <= 0;
  elements.loadMore.textContent = remaining > 0 ? `加载更多（${Math.min(PAGE_SIZE, remaining)}）` : "加载更多";
}

// Only text-like fields are typing targets.  Buttons, selects and checkboxes
// are not: after clicking a tab, toggle or filter, single-key shortcuts must
// keep working (filter selects/checkboxes are also blurred after a change).
// Date/time inputs count as typing because they use the arrow keys themselves.
const TYPING_INPUT_TYPES = new Set([
  "", "text", "search", "url", "email", "password", "number", "tel",
  "date", "datetime-local", "month", "week", "time"
]);

function isTypingTarget(target) {
  if (!(target instanceof Element)) return false;
  if (target.isContentEditable) return true;
  if (target.closest("textarea, [contenteditable='true'], [contenteditable='']")) return true;
  const input = target.closest("input");
  if (!input) return false;
  const rawType = typeof input.getAttribute === "function" ? input.getAttribute("type") : input.type;
  return TYPING_INPUT_TYPES.has(String(rawType || "").trim().toLowerCase());
}

function updateShortcutHint() {
  if (!elements.keyboardHint) return;
  let hint;
  if (shouldUseSwipeDeck()) {
    hint = "刷卡：← 不感兴趣 · → 收藏 · A 归档 · Z 撤销 · ? 快捷键";
  } else if (state.filterMode === "all") {
    hint = "列表：J/K 选择 · F 收藏 · A 归档 · X 不感兴趣 · O 打开 · Z 撤销 · ? 快捷键";
  } else {
    hint = "Z 撤销 · ? 快捷键";
  }
  elements.keyboardHint.textContent = hint;
}

function listCards() {
  return Array.from(elements.list.querySelectorAll(".card[data-card-index]"));
}

function setListCursor(index, scroll = true) {
  const cards = listCards();
  if (!cards.length) return;
  state.listCursor = Math.max(0, Math.min(index, cards.length - 1));
  cards.forEach((card, i) => card.classList.toggle("card--cursor", i === state.listCursor));
  const current = cards[state.listCursor];
  if (scroll && current && typeof current.scrollIntoView === "function") {
    current.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function toggleShortcutsOverlay(forceOpen) {
  const dialog = document.getElementById("shortcutsModal");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const open = forceOpen === undefined ? !dialog.open : forceOpen;
  if (open && !dialog.open) dialog.showModal();
  if (!open && dialog.open) dialog.close();
}

function handleTriageShortcut(event) {
  if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || isTypingTarget(event.target)) return;
  if (event.key === "?") {
    const shortcutsOpen = Boolean(document.querySelector("#shortcutsModal[open]"));
    if (shortcutsOpen || !document.querySelector("dialog[open]")) {
      event.preventDefault();
      toggleShortcutsOverlay(!shortcutsOpen);
    }
    return;
  }
  const lowerKey = (event.key || "").toLowerCase();
  if (lowerKey === "z" && !document.querySelector("dialog[open]") && state.filterMode !== "all") {
    if (state.undoStack.length) {
      event.preventDefault();
      undoLastInteraction();
    }
    return;
  }
  if (state.filterMode !== "all" || document.querySelector("dialog[open]")) return;

  if (shouldUseSwipeDeck()) {
    const key = lowerKey;
    const swipeAction = key === "arrowright" ? ["like", "right"] : key === "arrowleft" ? ["hide", "left"] : (key === "a" || key === "l") ? ["archive", "archive"] : null;
    if (swipeAction) {
      event.preventDefault();
      commitSwipeAction(swipeAction[0], swipeAction[1]);
    } else if (key === "z") {
      event.preventDefault();
      undoLastInteraction();
    } else if (key === "o") {
      const link = elements.list.querySelector(".swipe-card__title");
      if (link && link.href && !link.href.endsWith("#")) {
        event.preventDefault();
        window.open(link.href, "_blank", "noopener");
      }
    }
    return;
  }

  const key = lowerKey;
  if (key === "z") {
    event.preventDefault();
    undoLastInteraction();
    return;
  }
  if (key === "j" || key === "k") {
    event.preventDefault();
    setListCursor(state.listCursor + (key === "j" ? 1 : -1));
    return;
  }
  const cards = listCards();
  const card = cards[state.listCursor] || cards[0];
  if (!card) return;
  // A and L are both 归档 (L kept as a legacy alias).
  const actionByKey = { f: "favorite", a: "archive", l: "archive", x: "hide" };

  if (key === "o") {
    const link = card.querySelector(".card__title");
    if (link && link.href) {
      event.preventDefault();
      window.open(link.href, "_blank", "noopener");
    }
    return;
  }

  const action = actionByKey[key];
  if (!action) return;
  const button = card.querySelector(`[data-triage-action="${action}"]`);
  if (button) {
    event.preventDefault();
    button.click();
  }
}

function applyFilters() {
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
  const sets = interactionSets();

  const filtered = state.items.filter((item) => {
    // 1. Check interactions first ("all" = 待筛选 shows only unprocessed papers)
    if (!belongsToView(paperKey(item), state.filterMode, sets)) return false;

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
      if (!item.searchText.includes(keyword)) {
        return false;
      }
    }

    return true;
  });

  const sortDir = elements.sortSelect.value;
  if (sortDir === "taste") {
    // AI 匹配度：高分在前，未打分的排在最后，同分按日期新→旧。
    filtered.sort((a, b) => {
      const sa = tasteScoreOf(a);
      const sb = tasteScoreOf(b);
      if (sa !== sb) {
        if (sa === null) return 1;
        if (sb === null) return -1;
        return sb - sa;
      }
      return b.date - a.date;
    });
  } else {
    filtered.sort((a, b) => (sortDir === "asc" ? a.date - b.date : b.date - a.date));
  }

  state.filtered = filtered;
  state.visibleLimit = PAGE_SIZE;
  state.listCursor = 0;
  updateCountLabel();
  renderList();
  updateTopicCloudVisibility();
  if (state.filterMode === "favorites") {
    renderTopicCloud(filtered);
  }
  renderFilterChips();
  updateShortcutHint();
  saveUiState();
}

// --- Active filter chips, persisted UI state and URL filters ---

function activeFilterDescriptors() {
  const chips = [];
  const search = elements.searchInput ? elements.searchInput.value.trim() : "";
  if (search) chips.push({ label: `搜索=${search}`, clear: () => { elements.searchInput.value = ""; } });
  const journal = elements.journalSelect ? elements.journalSelect.value : "";
  if (journal) chips.push({ label: `期刊=${journal}`, clear: () => { elements.journalSelect.value = ""; } });
  getSelectedFilterValues(elements.filterMethod).forEach((value) => {
    chips.push({ label: `方法=${value}`, clear: () => setFilterSelections(elements.filterMethod, getSelectedFilterValues(elements.filterMethod).filter((v) => v !== value)) });
  });
  getSelectedFilterValues(elements.filterTopic).forEach((value) => {
    chips.push({ label: `主题=${value}`, clear: () => setFilterSelections(elements.filterTopic, getSelectedFilterValues(elements.filterTopic).filter((v) => v !== value)) });
  });
  if (elements.filterPreset && elements.filterPreset.value) {
    const option = Array.from(elements.filterPreset.options || []).find((o) => o.value === elements.filterPreset.value);
    chips.push({ label: `预设=${option ? option.textContent : elements.filterPreset.value}`, clear: () => { elements.filterPreset.value = ""; } });
  }
  if (elements.fromDate && elements.fromDate.value) chips.push({ label: `起始=${elements.fromDate.value}`, clear: () => { elements.fromDate.value = ""; } });
  if (elements.toDate && elements.toDate.value) chips.push({ label: `截止=${elements.toDate.value}`, clear: () => { elements.toDate.value = ""; } });
  return chips;
}

function countActiveFilters() {
  try { return activeFilterDescriptors().length; } catch (_) { return 0; }
}

function clearAllFilters() {
  if (elements.searchInput) elements.searchInput.value = "";
  if (elements.journalSelect) elements.journalSelect.value = "";
  if (elements.filterMethod) setFilterSelections(elements.filterMethod, []);
  if (elements.filterTopic) setFilterSelections(elements.filterTopic, []);
  if (elements.filterMethodMode) elements.filterMethodMode.value = "any";
  if (elements.filterTopicMode) elements.filterTopicMode.value = "any";
  if (elements.filterPreset) elements.filterPreset.value = "";
  if (elements.fromDate) elements.fromDate.value = "";
  if (elements.toDate) elements.toDate.value = "";
}

function renderFilterChips() {
  const bar = elements.filterChips;
  if (!bar) return;
  const chips = activeFilterDescriptors();
  bar.textContent = "";
  bar.hidden = chips.length === 0;
  if (!chips.length) return;
  const summary = document.createElement("span");
  summary.className = "filter-chips__summary";
  summary.textContent = `${chips.length} 个筛选生效 · `;
  const clearAll = document.createElement("button");
  clearAll.type = "button";
  clearAll.className = "filter-chips__clear";
  clearAll.textContent = "清除";
  clearAll.onclick = () => { clearAllFilters(); applyFilters(); };
  summary.appendChild(clearAll);
  bar.appendChild(summary);
  chips.forEach((chip) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "filter-chip";
    button.textContent = `${chip.label} ✕`;
    button.title = "移除此筛选";
    button.setAttribute("aria-label", `移除筛选：${chip.label}`);
    button.onclick = () => { chip.clear(); applyFilters(); };
    bar.appendChild(button);
  });
}

// Persisted UI state (localStorage, best effort):
//   filters / view / mode / sort / 显示摘要  -> saveUiState (on every applyFilters)
//   views[viewStateKey] = { scroll, limit } -> rememberViewPosition (throttled scroll)
//   swipePaperId (current card, by paper_id) -> rememberSwipePosition
// While state.transientUiState is set (URL jump from 洞察) nothing is written.

function readStoredUiState() {
  try {
    const parsed = JSON.parse(safeStorageGet(UI_STATE_KEY) || "null");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

function writeUiState(patch) {
  if (state.transientUiState) return false;
  safeStorageSet(UI_STATE_KEY, JSON.stringify({ ...readStoredUiState(), ...patch }));
  return true;
}

function viewStateKey(mode = state.filterMode, inboxMode = state.inboxViewMode) {
  return mode === "all" ? `all:${inboxMode}` : mode;
}

function collectUiState() {
  // Until categories load, the checkboxes do not exist yet: keep the restored selection.
  const pending = state.pendingFilterSelections;
  return {
    search: elements.searchInput ? elements.searchInput.value : "",
    journal: elements.journalSelect ? elements.journalSelect.value : "",
    tab: state.filterMode,
    mode: state.inboxViewMode,
    methods: pending ? pending.methods : getSelectedFilterValues(elements.filterMethod),
    topics: pending ? pending.topics : getSelectedFilterValues(elements.filterTopic),
    methodMode: elements.filterMethodMode ? elements.filterMethodMode.value : "any",
    topicMode: elements.filterTopicMode ? elements.filterTopicMode.value : "any",
    preset: elements.filterPreset ? elements.filterPreset.value : "",
    fromDate: elements.fromDate ? elements.fromDate.value : "",
    toDate: elements.toDate ? elements.toDate.value : "",
    sort: elements.sortSelect ? elements.sortSelect.value : "desc",
    showSummary: elements.summaryToggle ? Boolean(elements.summaryToggle.checked) : true
  };
}

function saveUiState() {
  return writeUiState(collectUiState());
}

const DATE_INPUT_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function stringList(value) {
  return Array.isArray(value) ? value.filter((entry) => typeof entry === "string" && entry) : [];
}

function restoreUiState() {
  const saved = readStoredUiState();
  if (VIEW_MODES.includes(saved.tab)) state.filterMode = saved.tab;
  if (saved.mode === "swipe" || saved.mode === "list") state.inboxViewMode = saved.mode;
  if (typeof saved.search === "string" && elements.searchInput) elements.searchInput.value = saved.search;
  if (typeof saved.journal === "string") state.pendingJournal = saved.journal;
  const methods = stringList(saved.methods);
  const topics = stringList(saved.topics);
  state.pendingFilterSelections = methods.length || topics.length ? { methods, topics } : null;
  if (elements.filterMethodMode && (saved.methodMode === "any" || saved.methodMode === "all")) elements.filterMethodMode.value = saved.methodMode;
  if (elements.filterTopicMode && (saved.topicMode === "any" || saved.topicMode === "all")) elements.filterTopicMode.value = saved.topicMode;
  if (elements.filterPreset && typeof saved.preset === "string") elements.filterPreset.value = saved.preset;
  if (elements.fromDate && (saved.fromDate === "" || DATE_INPUT_PATTERN.test(saved.fromDate || ""))) elements.fromDate.value = saved.fromDate;
  if (elements.toDate && (saved.toDate === "" || DATE_INPUT_PATTERN.test(saved.toDate || ""))) elements.toDate.value = saved.toDate;
  if (elements.sortSelect && SORT_VALUES.includes(saved.sort)) elements.sortSelect.value = saved.sort;
  if (elements.summaryToggle && typeof saved.showSummary === "boolean") elements.summaryToggle.checked = saved.showSummary;
  const views = saved.views && typeof saved.views === "object" && !Array.isArray(saved.views) ? saved.views : {};
  state.restoredPositions = { views, swipePaperId: typeof saved.swipePaperId === "string" ? saved.swipePaperId : "" };
}

// Records the scroll offset and "加载更多" depth of the current list view.
function rememberViewPosition() {
  if (!state.positionRestored || state.positionPending || state.transientUiState || typeof window === "undefined") return false;
  if (shouldUseSwipeDeck()) return false;
  const stored = readStoredUiState();
  const views = stored.views && typeof stored.views === "object" && !Array.isArray(stored.views) ? stored.views : {};
  views[viewStateKey()] = { scroll: Math.max(0, Math.round(window.scrollY || 0)), limit: state.visibleLimit };
  return writeUiState({ views });
}

function rememberSwipePosition(item) {
  if (!state.positionRestored || state.positionPending || state.transientUiState) return false;
  const id = paperKey(item) || "";
  if (readStoredUiState().swipePaperId === id) return false;
  return writeUiState({ swipePaperId: id });
}

// Restores pagination + scroll (list) or the current card (swipe) for the
// current view.  At startup it uses the values read before the first render.
function restoreViewPosition() {
  const startup = !state.positionRestored;
  const source = startup ? (state.restoredPositions || {}) : readStoredUiState();
  state.positionRestored = true;
  state.positionPending = false;
  if (state.transientUiState) return false;
  if (shouldUseSwipeDeck()) {
    const wanted = typeof source.swipePaperId === "string" ? source.swipePaperId : "";
    const index = wanted ? state.filtered.findIndex((item) => paperKey(item) === wanted) : -1;
    if (index > 0) {
      state.swipeIndex = index;
      renderList();
    }
    rememberSwipePosition(currentSwipeItem());
    return index >= 0;
  }
  const views = source.views && typeof source.views === "object" ? source.views : {};
  const saved = views[viewStateKey()];
  if (!saved || typeof saved !== "object") return false;
  const limit = Number(saved.limit);
  if (Number.isFinite(limit) && limit > state.visibleLimit) {
    state.visibleLimit = Math.max(PAGE_SIZE, Math.min(limit, Math.ceil(state.filtered.length / PAGE_SIZE) * PAGE_SIZE));
    renderList();
  }
  const scroll = Number(saved.scroll);
  if (Number.isFinite(scroll) && scroll > 0 && typeof window !== "undefined" && typeof window.scrollTo === "function") {
    window.scrollTo(0, scroll);
    // Layout may settle a frame later (fonts, badges); retry once.
    if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(() => window.scrollTo(0, scroll));
  }
  return true;
}

let scrollSaveTimer = null;

function setupPositionTracking() {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") return;
  try {
    if (window.history && "scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
  } catch (_) { /* optional */ }
  window.addEventListener("scroll", () => {
    if (scrollSaveTimer) return;
    scrollSaveTimer = setTimeout(() => {
      scrollSaveTimer = null;
      rememberViewPosition();
    }, 300);
  }, { passive: true });
  window.addEventListener("pagehide", () => rememberViewPosition());
}

// Reflects state.filterMode / state.inboxViewMode on the tab and toggle buttons.
function syncViewControls() {
  document.querySelectorAll(".filter-btn").forEach((button) => {
    const active = button.dataset.filter === state.filterMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (elements.inboxViewToggle && typeof elements.inboxViewToggle.querySelectorAll === "function") {
    elements.inboxViewToggle.hidden = state.filterMode !== "all";
    elements.inboxViewToggle.querySelectorAll("[data-inbox-view]").forEach((entry) => {
      const active = entry.dataset.inboxView === state.inboxViewMode;
      entry.classList.toggle("is-active", active);
      entry.setAttribute("aria-pressed", String(active));
    });
  }
  const showFavoriteTools = state.filterMode === "favorites";
  const summarize = document.getElementById("btnSummarizeFavorites");
  const exportRis = document.getElementById("btnExportFavorites");
  const fetchAbstracts = document.getElementById("btnFetchAbstracts");
  if (summarize) summarize.hidden = !showFavoriteTools;
  if (exportRis) exportRis.hidden = !showFavoriteTools;
  if (fetchAbstracts) fetchAbstracts.hidden = !showFavoriteTools || state.fetchAbstractsAvailable === false;
  if (showFavoriteTools) probeFetchAbstracts();
}

// The undo history is deliberately kept across view / mode switches.
function setFilterMode(mode) {
  if (!VIEW_MODES.includes(mode)) return;
  if (mode !== state.filterMode) {
    rememberViewPosition();
    state.positionPending = true; // until restoreViewPosition() for the new view
  }
  state.filterMode = mode;
  state.swipeIndex = 0;
  syncViewControls();
}

function setInboxViewMode(mode) {
  if (mode !== "swipe" && mode !== "list") return;
  if (mode !== state.inboxViewMode) {
    rememberViewPosition();
    state.positionPending = true;
  }
  state.inboxViewMode = mode;
  state.swipeIndex = 0;
  syncViewControls();
}

const URL_VIEW_ALIASES = { inbox: "all", favorites: "favorites", favorite: "favorites", archived: "archived", hidden: "hidden", all: "everything", everything: "everything" };

// Applies ?journal / source / q|search / topic / method / view / from once,
// then strips them from the address bar so a reload does not re-apply them.
function applyUrlFilters() {
  if (state.urlFiltersApplied) return false;
  state.urlFiltersApplied = true;
  if (typeof window === "undefined" || !window.location) return false;
  const params = new URLSearchParams(window.location.search);
  if (!params.toString()) return false;

  const journalParam = (params.get("journal") || "").trim();
  const sourceParam = (params.get("source") || "").trim();
  const queryParam = (params.get("q") || params.get("search") || "").trim();
  const topicParam = (params.get("topic") || "").trim();
  const methodParam = (params.get("method") || "").trim();
  const viewParam = (params.get("view") || "").trim().toLowerCase();
  const fromParam = (params.get("from") || "").trim().toLowerCase();
  let applied = false;

  if (journalParam || sourceParam || queryParam || topicParam || methodParam) {
    clearAllFilters();
  }

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
    applied = true;
  }

  if (sourceParam && elements.searchInput) {
    elements.searchInput.value = sourceParam;
    applied = true;
  } else if (queryParam && elements.searchInput) {
    elements.searchInput.value = queryParam;
    applied = true;
  }
  if (topicParam && elements.filterTopic) {
    setFilterSelections(elements.filterTopic, [topicParam]);
    applied = true;
  }
  if (methodParam && elements.filterMethod) {
    setFilterSelections(elements.filterMethod, [methodParam]);
    applied = true;
  }
  if (URL_VIEW_ALIASES[viewParam]) {
    setFilterMode(URL_VIEW_ALIASES[viewParam]);
    applied = true;
  }
  // Arriving with an explicit filter means "show me the matching papers",
  // which the one-at-a-time swipe deck cannot do.
  if (applied) setInboxViewMode("list");
  // A jump from 洞察 is for this page load only: never persist it over the
  // user's saved filters / mode / positions.
  if (applied || fromParam) state.transientUiState = true;

  // from=insights（旧链接 report / stats 同样接受）显示返回洞察页的链接；
  // 洞察页会记住上次打开的标签，旧值则直接回到对应标签。
  const BACK_LINK_TARGETS = { insights: "insights.html", report: "insights.html#prefs", stats: "insights.html#journals" };
  if (Object.prototype.hasOwnProperty.call(BACK_LINK_TARGETS, fromParam) && elements.backLink) {
    elements.backLink.href = BACK_LINK_TARGETS[fromParam];
    elements.backLink.textContent = "← 返回洞察";
    elements.backLink.hidden = false;
  }

  try {
    if (window.history && typeof window.history.replaceState === "function") {
      window.history.replaceState(null, "", window.location.pathname + window.location.hash);
    }
  } catch (_) { /* address bar cleanup is cosmetic */ }
  return applied;
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
  // Selections restored from localStorage are applied once the categories exist.
  const restored = state.pendingFilterSelections;
  state.pendingFilterSelections = null;
  if (elements.filterMethod) {
    const selected = new Set(restored ? restored.methods : getSelectedFilterValues(elements.filterMethod));
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
      buildFilterCheckboxes(elements.filterMethod, state.categories.methods
        .filter((method) => method && method.name)
        .map((method) => ({ value: method.name, text: method.label ? `${method.label} (${method.name})` : method.name })), selected);
    }
  }

  if (elements.filterTopic) {
    const selected = new Set(restored ? restored.topics : getSelectedFilterValues(elements.filterTopic));
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
      buildFilterCheckboxes(elements.filterTopic, state.categories.topics
        .filter((topic) => topic && topic.name)
        .map((topic) => ({ value: topic.name, text: topic.name })), selected);
    }
  }
}

function populateJournals(items) {
  const set = new Set(items.map((item) => item.journal).filter(Boolean));
  const journals = Array.from(set).sort((a, b) => a.localeCompare(b));
  const select = elements.journalSelect;
  // Rebuilt from scratch on every load (feed reloads after jobs) so options
  // never duplicate; the current or restored selection is preserved.
  const desired = select.value || state.pendingJournal || "";
  state.pendingJournal = "";
  select.textContent = "";
  const optionAll = document.createElement("option");
  optionAll.value = "";
  optionAll.textContent = "全部期刊";
  select.appendChild(optionAll);

  for (const journal of journals) {
    const option = document.createElement("option");
    option.value = journal;
    option.textContent = journal;
    select.appendChild(option);
  }
  select.value = journals.includes(desired) ? desired : "";
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

function attachHandlers() {
  if (handlersAttached) return;
  handlersAttached = true;
  const controls = [
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
    if (control.tagName !== "SELECT" && control.classList && control.classList.contains("multi-filter__panel")) {
      // Checkbox popover: one change event per click; normalise 全部 first.
      control.addEventListener("change", (event) => {
        normalizeCheckboxFilter(control, event.target);
        applyFilters();
        releaseFilterFocus(event.target);
      });
      return;
    }
    if (control.tagName === "SELECT" && control.multiple) {
      control.addEventListener("focus", () => cacheMultiSelectState(control));
      control.addEventListener("mousedown", () => cacheMultiSelectState(control));
      control.addEventListener("keydown", () => cacheMultiSelectState(control));
    }
    control.addEventListener("input", applyFilters);
    control.addEventListener("change", (event) => {
      applyFilters();
      releaseFilterFocus(event.target);
    });
  });

  if (elements.searchInput) {
    elements.searchInput.addEventListener("input", () => {
      clearTimeout(searchDebounceId);
      searchDebounceId = setTimeout(applyFilters, 200);
    });
  }
  if (elements.loadMore) {
    elements.loadMore.addEventListener("click", () => {
      state.visibleLimit += PAGE_SIZE;
      renderList();
      rememberViewPosition();
    });
  }
  if (elements.advancedFilters) {
    const saved = safeStorageGet("paper-feed:advanced-filters");
    elements.advancedFilters.open = saved === "open";
    elements.advancedFilters.addEventListener("toggle", () => {
      safeStorageSet("paper-feed:advanced-filters", elements.advancedFilters.open ? "open" : "closed");
    });
  }
  if (elements.clearAdvancedFilters) {
    elements.clearAdvancedFilters.addEventListener("click", () => {
      if (elements.filterMethod) setFilterSelections(elements.filterMethod, []);
      if (elements.filterTopic) setFilterSelections(elements.filterTopic, []);
      if (elements.filterMethodMode) elements.filterMethodMode.value = "any";
      if (elements.filterTopicMode) elements.filterTopicMode.value = "any";
      if (elements.filterPreset) elements.filterPreset.value = "";
      if (elements.fromDate) elements.fromDate.value = "";
      if (elements.toDate) elements.toDate.value = "";
      applyFilters();
    });
  }
}

// Shown in the list area when neither the API nor feed.json answered, so the
// page is not just an empty grid.
function renderLoadError() {
  if (!elements.list) return;
  elements.list.textContent = "";
  if (elements.list.classList) elements.list.classList.remove("grid--swipe");
  const card = document.createElement("div");
  card.className = "card load-error";
  card.setAttribute("role", "alert");
  const title = document.createElement("strong");
  title.textContent = "无法加载论文列表。";
  const hint = document.createElement("span");
  hint.className = "empty-state__hint";
  hint.textContent = "请确认服务已启动：";
  const command = document.createElement("code");
  command.textContent = "python -m paper_feed start";
  hint.appendChild(command);
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "btn btn--primary btn--small empty-state__action";
  retry.textContent = "重试";
  retry.onclick = async () => {
    retry.disabled = true;
    retry.textContent = "重试中…";
    if (!state.paperApiAvailable) await loadInteractions();
    if (!state.categories.methods.length && !state.categories.topics.length) await loadCategories();
    const ok = await loadFeed();
    if (ok) resumeRunningJobs();
  };
  card.append(title, hint, retry);
  elements.list.appendChild(card);
  if (elements.countLabel) elements.countLabel.textContent = "加载失败";
  if (elements.loadMore) elements.loadMore.hidden = true;
}

async function loadFeed() {
  setStatus("加载中...");
  const priorVisibleLimit = state.visibleLimit;
  let payload;
  try {
    const response = await fetch("/api/papers?view=all", { cache: "no-store" });
    if (!response.ok) throw new Error("papers API unavailable");
    payload = await response.json();
    if (!Array.isArray(payload.items)) throw new Error("papers API returned no items");
    state.paperApiAvailable = true;
  } catch (apiError) {
    state.paperApiAvailable = false;
    try {
      // Static GitHub Pages has no API; retain the legacy export as a readable fallback.
      const response = await fetch("feed.json?t=" + Date.now(), {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache", "Pragma": "no-cache" }
      });
      if (!response.ok) throw new Error("feed.json missing");
      payload = await response.json();
      console.warn("Paper API unavailable; using feed.json fallback", apiError);
    } catch (feedError) {
      setStatus("无法加载论文：Paper API 与 feed.json 均不可用。");
      renderLoadError();
      return false;
    }
  }
  try {
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
        date: new Date(item.pub_date),
        searchText: `${item.title || ""} ${item.title_zh || ""} ${parsed.text || ""} ${item.abstract || ""} ${item.journal || ""}`.toLowerCase()
      };
    });

    populateJournals(state.items);
    applyUrlFilters();
    elements.generatedAt.textContent = payload.generated_at
      ? `更新于 ${formatDate(new Date(payload.generated_at))}`
      : "";
    attachHandlers();
    syncViewControls();
    applyFilters();
    if (!state.positionRestored) {
      restoreViewPosition();
    } else if (priorVisibleLimit > PAGE_SIZE) {
      state.visibleLimit = Math.min(priorVisibleLimit, state.filtered.length);
      renderList();
    }
    updateFilterCounts();
    setStatus("");
    return true;
  } catch (error) {
    console.error("Failed to render papers", error);
    setStatus("论文数据格式无效，无法显示。");
    return false;
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

function setFormError(elementId, message) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = message || "";
  el.hidden = !message;
}

async function responseMessage(res, fallback) {
  const payload = await res.json().catch(() => ({}));
  return payload.message || `${fallback}（HTTP ${res.status}）`;
}

// Returns true only when the server accepted the edit; the modal stays open
// (with the user's selections intact) on failure.
async function saveClassificationEdits() {
  if (!currentClassificationItem) return false;
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

  setFormError("classificationError", "");
  try {
    const res = await fetch("/api/update_classification", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...legacyReference(currentClassificationItem),
        methods,
        topics,
        theories: mergedTheories,
        context,
        subjects,
        novelty_score: noveltyScore
      })
    });
    if (!res.ok) {
      throw new Error(await responseMessage(res, "保存失败"));
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
    rerenderPreservingScroll();
    setStatus("分类已保存。");
    return true;
  } catch (e) {
    setFormError("classificationError", `分类保存失败：${e.message || "网络错误"}。修改仍保留，可重试。`);
    return false;
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
const btnFetchAbstracts = document.getElementById("btnFetchAbstracts");
const btnExportFavorites = document.getElementById("btnExportFavorites");

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

// --- Background jobs ---

const JOB_ENDPOINTS = { fetch: "/api/fetch", reanalyze: "/api/reanalyze", summarize: "/api/summarize_favorites", fetch_abstracts: "/api/fetch_abstracts" };
const JOB_LABELS = { fetch: "更新 RSS", reanalyze: "AI 分析", summarize: "生成 AI 总结", fetch_abstracts: "补全摘要" };
const JOB_BUSY_LABELS = { fetch: "更新中...", reanalyze: "分析中...", summarize: "生成中...", fetch_abstracts: "补全中..." };
// POST bodies; jobs not listed send {}.
const JOB_BODIES = { fetch_abstracts: { view: "favorite" } };
const JOB_POLL_TIMEOUT_MS = 30 * 60 * 1000;
const JOB_MAX_STATUS_ERRORS = 5;
const activeJobs = new Set();
let jobStatusHideId = null;

function jobButton(kind) {
  return { fetch: btnRefresh, reanalyze: btnReanalyze, summarize: btnSummarizeFavorites, fetch_abstracts: btnFetchAbstracts }[kind] || null;
}

function setJobButtonBusy(kind, busy) {
  const button = jobButton(kind);
  if (!button) return;
  if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent;
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
  button.textContent = busy ? (JOB_BUSY_LABELS[kind] || "处理中...") : button.dataset.idleLabel;
}

function jobFailureDetails(job) {
  const result = (job && job.result) || {};
  const errors = ensureArray(result.errors).map(String);
  const failedSources = ensureArray(result.failed_sources).map((source) => (typeof source === "string" ? source : JSON.stringify(source)));
  const details = errors.length ? errors : failedSources;
  const failed = Number.isFinite(result.failed) ? result.failed : (failedSources.length || errors.length);
  return { failed, details };
}

// Server messages may live in `message` or `error` (e.g. the cross-process
// lock: "另一个任务正在运行（…）/ Another Paper Feed task is running").
function jobMessage(job) {
  if (!job) return "";
  const text = job.message || job.error || (job.result && (job.result.message || job.result.error)) || "";
  return typeof text === "string" ? text : String(text);
}

// "补到 X 篇摘要，Y 篇未找到" for fetch_abstracts; null for other kinds.
function fetchAbstractsSummary(job) {
  if (!job || job.kind !== "fetch_abstracts") return null;
  const result = job.result || {};
  const count = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
  const skipped = count(result.skipped);
  return `补到 ${count(result.fetched)} 篇摘要，${count(result.failed)} 篇未找到${skipped ? `，${skipped} 篇已跳过` : ""}。`;
}

function renderJobStatus(job) {
  const box = elements.jobStatus;
  if (!box) return;
  if (jobStatusHideId) { clearTimeout(jobStatusHideId); jobStatusHideId = null; }
  box.textContent = "";
  if (!job) { box.hidden = true; return; }
  box.hidden = false;
  const label = JOB_LABELS[job.kind] || "后台任务";
  const running = job.status === "queued" || job.status === "running";
  // fetch_abstracts reports "DOI but no abstract found" as `failed`, so its
  // partial_failed is a normal finish: no error styling and no retry prompt.
  const normalFinish = job.status === "succeeded" || (job.status === "partial_failed" && Boolean(fetchAbstractsSummary(job)));
  box.className = `job-status job-status--${running ? "running" : (normalFinish ? "succeeded" : job.status)}`;

  const line = document.createElement("div");
  line.className = "job-status__line";
  const text = document.createElement("span");
  if (job.status === "queued") text.textContent = `${label}：已排队，仍可继续浏览论文。`;
  else if (job.status === "running") text.textContent = `${label}：进行中${Number.isFinite(job.progress) ? `（${job.progress}%）` : "…"}${job.message && job.message !== "任务正在执行" ? ` · ${job.message}` : ""}`;
  else if ((job.status === "succeeded" || job.status === "partial_failed") && fetchAbstractsSummary(job)) text.textContent = `${label}：完成。${fetchAbstractsSummary(job)}`;
  else if (job.status === "succeeded") text.textContent = `${label}：完成。${job.result && job.result.message ? job.result.message : ""}`;
  else if (job.status === "partial_failed") text.textContent = `${label}：完成，但 ${jobFailureDetails(job).failed} 项失败。已保留成功的结果。`;
  else text.textContent = `${label}：失败。${jobMessage(job)} 原有数据保持不变。`;
  line.appendChild(text);

  if (!normalFinish && (job.status === "partial_failed" || job.status === "failed" || job.status === "timeout") && JOB_ENDPOINTS[job.kind]) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn btn--secondary btn--small";
    retry.textContent = "重试";
    retry.onclick = () => startJob(job.kind);
    line.appendChild(retry);
  }
  if (!running) {
    const close = document.createElement("button");
    close.type = "button";
    close.className = "job-status__close";
    close.textContent = "✕";
    close.setAttribute("aria-label", "关闭任务状态");
    close.onclick = () => renderJobStatus(null);
    line.appendChild(close);
  }
  box.appendChild(line);

  if (running && Number.isFinite(job.progress)) {
    const progress = document.createElement("progress");
    progress.className = "job-status__progress";
    progress.max = 100;
    progress.value = Math.max(0, Math.min(100, job.progress));
    progress.setAttribute("aria-label", `${label}进度`);
    box.appendChild(progress);
  }

  const { details } = jobFailureDetails(job);
  if (!running && !normalFinish && details.length) {
    const more = document.createElement("details");
    more.className = "job-status__details";
    const summary = document.createElement("summary");
    summary.textContent = `查看错误详情（${details.length}）`;
    const list = document.createElement("ul");
    details.slice(0, 20).forEach((detail) => {
      const li = document.createElement("li");
      li.textContent = detail;
      list.appendChild(li);
    });
    more.append(summary, list);
    box.appendChild(more);
  }
  if (normalFinish) {
    jobStatusHideId = setTimeout(() => renderJobStatus(null), 10000);
  }
}

// Polls a job until it finishes, the server stops answering, or the overall
// timeout elapses.  Used both for new jobs and for jobs resumed after reload.
async function pollJob(job) {
  const kind = job.kind;
  activeJobs.add(kind);
  setJobButtonBusy(kind, true);
  const startedAt = Date.now();
  let delay = 800;
  let statusErrors = 0;
  try {
    while (job.status === "queued" || job.status === "running") {
      renderJobStatus(job);
      if (Date.now() - startedAt > JOB_POLL_TIMEOUT_MS) {
        renderJobStatus({ ...job, status: "timeout", message: "等待超时，任务可能仍在后台运行；稍后刷新页面查看结果。" });
        showToast(`${JOB_LABELS[kind] || "任务"}等待超时。`, "error");
        return null;
      }
      await sleep(delay);
      delay = Math.min(delay * 1.25, 4000);
      try {
        const statusResponse = await fetch(`/api/jobs/${job.id}`, { cache: "no-store" });
        if (!statusResponse.ok) throw new Error(`HTTP ${statusResponse.status}`);
        job = { kind, ...(await statusResponse.json()) };
        statusErrors = 0;
      } catch (error) {
        statusErrors += 1;
        if (statusErrors >= JOB_MAX_STATUS_ERRORS) {
          throw new Error(`无法读取任务状态（${error.message}）`);
        }
      }
    }
    if (job.status === "succeeded" || job.status === "partial_failed") {
      await loadFeed();
    }
    renderJobStatus(job);
    const abstractsSummary = fetchAbstractsSummary(job);
    if (abstractsSummary && (job.status === "succeeded" || job.status === "partial_failed")) {
      setStatus(`${JOB_LABELS[kind]}完成：${abstractsSummary}`);
      showToast(`${JOB_LABELS[kind]}完成：${abstractsSummary}`, "success");
    } else if (job.status === "partial_failed") {
      showToast(`${JOB_LABELS[kind]}完成，但 ${jobFailureDetails(job).failed} 项失败。详情见任务状态。`, "warn");
    } else if (job.status === "failed" || job.status === "cancelled") {
      showToast(`${JOB_LABELS[kind]}失败：${jobMessage(job) || "原有数据未变更。"}`, "error");
    } else {
      setStatus(`${JOB_LABELS[kind]}完成。`);
    }
    return job;
  } catch (error) {
    renderJobStatus({ ...job, status: "failed", message: error.message });
    showToast(`${JOB_LABELS[kind] || "任务"}出错：${error.message}`, "error");
    return null;
  } finally {
    activeJobs.delete(kind);
    setJobButtonBusy(kind, false);
  }
}

async function startJob(kind) {
  const endpoint = JOB_ENDPOINTS[kind];
  if (!endpoint) return null;
  if (activeJobs.has(kind)) {
    setStatus(`${JOB_LABELS[kind]}已在进行中。`);
    return null;
  }
  setJobButtonBusy(kind, true);
  let payload = {};
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(JOB_BODIES[kind] || {})
    });
    payload = await response.json().catch(() => ({}));
    if (response.status === 404 && kind === "fetch_abstracts") markFetchAbstractsUnavailable();
    if (response.status !== 202 || !payload.job) {
      throw new Error(payload.message || payload.error || `无法启动后台任务（HTTP ${response.status}）`);
    }
  } catch (error) {
    setJobButtonBusy(kind, false);
    renderJobStatus({ kind, status: "failed", message: error.message, result: {} });
    showToast(`${JOB_LABELS[kind]}失败：${error.message}`, "error");
    return null;
  }
  return pollJob({ kind, ...payload.job });
}

// Kept for backwards compatibility with older callers.
async function runBackgroundJob(endpoint) {
  const kind = Object.keys(JOB_ENDPOINTS).find((key) => JOB_ENDPOINTS[key] === endpoint);
  return kind ? startJob(kind) : null;
}

async function resumeRunningJobs() {
  try {
    const response = await fetch("/api/jobs", { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    const seen = new Set();
    ensureArray(payload.jobs).forEach((job) => {
      if (!job || !JOB_ENDPOINTS[job.kind] || seen.has(job.kind)) return;
      seen.add(job.kind);
      if ((job.status === "queued" || job.status === "running") && !activeJobs.has(job.kind)) {
        pollJob(job);
      }
    });
  } catch (_) {
    /* older servers have no /api/jobs; nothing to resume */
  }
}

async function fetchConfig() {
  try {
    const res = await fetch("/api/config", { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

// Returns {pending, total} or null when the endpoint is missing / unreadable.
async function fetchPendingSummaries() {
  try {
    const res = await fetch("/api/summarize_favorites/pending?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) return null;
    const payload = await res.json();
    const pending = Number(payload && payload.pending);
    if (!Number.isFinite(pending)) return null;
    const total = Number(payload.total_favorites);
    return { pending, total: Number.isFinite(total) ? total : NaN };
  } catch (_) {
    return null;
  }
}

function configHasApiKey(config) {
  if (!config) return false;
  if (typeof config.has_api_key === "boolean") return config.has_api_key;
  return Boolean(config.api_key_configured);
}

const DEFAULT_CODEX_MODEL = "gpt-6-luna";

// Whether AI tasks can run. Newer servers report `ai_ready` (Codex CLI or an
// OpenAI key); older ones only know about the OpenAI key.
function configAiReady(config) {
  if (!config) return false;
  if (typeof config.ai_ready === "boolean") return config.ai_ready;
  return configHasApiKey(config);
}

// Backend that AI tasks will actually use: "codex" or "openai".
function configEffectiveBackend(config) {
  if (!config) return "openai";
  if (config.effective_backend === "codex" || config.effective_backend === "openai") return config.effective_backend;
  if (config.AI_BACKEND === "codex" && config.codex_available !== false && config.effective_backend !== null) return "codex";
  return "openai";
}

function configCodexModel(config) {
  return (config && typeof config.CODEX_MODEL === "string" && config.CODEX_MODEL.trim()) || DEFAULT_CODEX_MODEL;
}

// Wording for confirm dialogs: what the AI call will consume.
function aiCostNote(config) {
  if (configEffectiveBackend(config) === "codex") {
    return `将通过 Codex CLI 调用 ${configCodexModel(config)}，消耗 ChatGPT 订阅额度`;
  }
  return "这会调用 AI 接口并消耗 API 额度";
}

// Blocks an AI action only when a newer server explicitly reports ai_ready=false.
function warnIfAiNotReady(config) {
  if (!config || config.ai_ready !== false) return false;
  const message = config.AI_BACKEND === "codex"
    ? "AI 未就绪：未找到可用的 Codex CLI，请安装并登录（npm i -g @openai/codex，然后 codex login），或在 ⚙️ 设置中改用 OpenAI 兼容 API。"
    : "AI 未就绪：请在 ⚙️ 设置中填写 API Key，或改用 Codex CLI。";
  setStatus(message);
  showToast(message, "error");
  return true;
}

if (btnSummarizeFavorites) {
  btnSummarizeFavorites.addEventListener("click", async () => {
    btnSummarizeFavorites.blur();
    if (state.interactions.favorites.length === 0) {
      showToast("还没有收藏任何文章。", "info");
      return;
    }

    const [pending, config] = await Promise.all([fetchPendingSummaries(), fetchConfig()]);
    if (pending && pending.pending <= 0) {
      const message = "所有收藏都已有 AI 总结或用户补充的摘要，无需生成。";
      setStatus(message);
      showToast(message, "info");
      return;
    }
    if (warnIfAiNotReady(config)) return;
    const costNote = aiCostNote(config);
    let question;
    if (pending) {
      const totalNote = Number.isFinite(pending.total) ? `（共 ${pending.total} 篇收藏）` : "";
      question = `将为 ${pending.pending} 篇尚无总结的收藏文章生成 AI 总结${totalNote}。\n${costNote}。确定吗？`;
    } else {
      // Older servers have no pending endpoint: fall back to the favourite count.
      question = `确定要对 ${state.interactions.favorites.length} 篇收藏的文章生成 AI 总结吗？\n${costNote}。`;
    }
    if (state.fetchAbstractsAvailable !== false && btnFetchAbstracts) {
      question += "\n提示：先运行“📚 补全摘要（免费）”取得原始摘要，AI 总结会更准确。";
    }
    if (!confirm(question)) {
      return;
    }

    await startJob("summarize");
  });
}

// --- 补全摘要（免费）: DOI lookup via Crossref / OpenAlex / Semantic Scholar ---

function markFetchAbstractsUnavailable() {
  state.fetchAbstractsAvailable = false;
  if (btnFetchAbstracts) btnFetchAbstracts.hidden = true;
}

// Returns {pending, with_doi, total}, or null when unavailable / unreadable.
// A 404 means the server predates the feature: the button is hidden.
async function fetchPendingAbstracts() {
  try {
    const res = await fetch("/api/fetch_abstracts/pending?view=favorite&t=" + Date.now(), { cache: "no-store" });
    if (res.status === 404) {
      markFetchAbstractsUnavailable();
      return null;
    }
    if (!res.ok) return null;
    const payload = await res.json();
    const count = (value) => (Number.isFinite(Number(value)) ? Number(value) : NaN);
    const result = { pending: count(payload && payload.pending), with_doi: count(payload && payload.with_doi), total: count(payload && payload.total) };
    if (!Number.isFinite(result.with_doi)) return null;
    state.fetchAbstractsAvailable = true;
    return result;
  } catch (_) {
    return null;
  }
}

let fetchAbstractsProbe = null;

// Checked once, the first time the favorites toolbar is shown.
function probeFetchAbstracts() {
  if (state.fetchAbstractsAvailable !== null || fetchAbstractsProbe || !state.paperApiAvailable) return;
  fetchAbstractsProbe = fetchPendingAbstracts().finally(() => { fetchAbstractsProbe = null; });
}

async function runFetchAbstracts() {
  if (activeJobs.has("fetch_abstracts")) {
    setStatus("补全摘要已在进行中。");
    return null;
  }
  if (!ensureArray(state.interactions.favorites).length) {
    showToast("还没有收藏任何文章。", "info");
    return null;
  }
  const pending = await fetchPendingAbstracts();
  if (state.fetchAbstractsAvailable === false) {
    showToast("当前服务不支持补全摘要，请更新 Paper Feed。", "info");
    return null;
  }
  if (!pending) {
    showToast("无法读取待补全的收藏数量，请稍后重试。", "error");
    return null;
  }
  if (pending.with_doi <= 0) {
    const message = "没有可按 DOI 查找的收藏";
    setStatus(`${message}。`);
    showToast(`${message}。`, "info");
    return null;
  }
  const count = Number.isFinite(pending.pending) ? Math.min(pending.pending, pending.with_doi) : pending.with_doi;
  const totalNote = Number.isFinite(pending.total) ? `（共 ${pending.total} 篇收藏）` : "";
  if (!confirm(`将按 DOI 从 Crossref / OpenAlex / Semantic Scholar 免费查找 ${count} 篇收藏的原始摘要（不消耗 AI 额度）${totalNote}。确定吗？`)) {
    return null;
  }
  return startJob("fetch_abstracts");
}

if (btnFetchAbstracts) {
  btnFetchAbstracts.addEventListener("click", () => {
    btnFetchAbstracts.blur();
    runFetchAbstracts();
  });
}

if (btnReanalyze) {
  btnReanalyze.addEventListener("click", async () => {
    closeMoreMenu();
    const config = await fetchConfig();
    if (warnIfAiNotReady(config)) return;
    if (!confirm(`对尚未分析或分类版本已过期的论文进行 AI 翻译与分类？\n${aiCostNote(config)}，可能需要一些时间。`)) {
      return;
    }

    await startJob("reanalyze");
  });
}


const CONFIG_FIELDS = ["AI_BACKEND", "CODEX_MODEL", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "OPENAI_PROXY"];
const CONFIG_SOURCE_LABELS = {
  env: "来源：环境变量 · 由环境变量提供，此处修改不会生效",
  config: "来源：config.json",
  default: "来源：默认值",
  unset: "来源：未设置"
};
let settingsSources = {};

function renderConfigSources(sources) {
  settingsSources = sources && typeof sources === "object" ? sources : {};
  CONFIG_FIELDS.forEach((field) => {
    const note = document.querySelector(`[data-source-for="${field}"]`);
    if (!note) return;
    const source = settingsSources[field];
    const label = CONFIG_SOURCE_LABELS[source];
    note.textContent = label || "";
    note.hidden = !label;
    if (label) note.dataset.source = source;
    else delete note.dataset.source;
  });
}

function setConnectionResult(text, resultState = "") {
  const result = document.getElementById("testConnectionResult");
  if (!result) return;
  result.textContent = text || "";
  if (resultState) result.dataset.state = resultState;
  else delete result.dataset.state;
}

// True once /api/config reported AI_BACKEND (servers with Codex CLI support).
let settingsBackendSupported = false;
// Last config read for the settings modal; used by the connection test copy.
let settingsConfig = null;

function selectedBackend() {
  if (!settingsBackendSupported || !form || !form.AI_BACKEND) return "openai";
  return form.AI_BACKEND.value === "openai" ? "openai" : "codex";
}

// Shows the Codex or OpenAI fields for the selected backend. Without backend
// support the OpenAI fields are shown exactly as before.
function syncBackendSections() {
  const backend = selectedBackend();
  const choice = document.getElementById("aiBackendChoice");
  const codexSection = document.getElementById("codexSettings");
  const openaiSection = document.getElementById("openaiSettings");
  const openaiTitle = document.getElementById("openaiSettingsTitle");
  const clearButton = document.getElementById("btnClearApiKey");
  if (choice) choice.hidden = !settingsBackendSupported;
  if (codexSection) codexSection.hidden = backend !== "codex";
  if (openaiSection) openaiSection.hidden = backend !== "openai";
  if (openaiTitle) openaiTitle.hidden = !settingsBackendSupported;
  if (clearButton) clearButton.hidden = backend !== "openai";
}

function apiKeyStatusText(hasKey) {
  return hasKey ? "✓ 已配置 API Key" : "✗ 未配置 API Key（AI 翻译、分类和总结将不可用）";
}

// Backend-aware status line: {text, state}.
function describeAiStatus(config) {
  if (!config) return { text: "AI 状态：无法读取服务器配置", state: "missing" };
  const hasKey = configHasApiKey(config);
  if (typeof config.AI_BACKEND !== "string") {
    return { text: hasKey ? "API Key 状态：✓ 已配置" : "API Key 状态：✗ 未配置（AI 翻译、分类和总结将不可用）", state: hasKey ? "ok" : "missing" };
  }
  const openaiModel = config.OPENAI_MODEL || "gpt-4o-mini";
  if (config.AI_BACKEND === "codex") {
    if (config.codex_available === false) {
      let text = "AI 状态：✗ 未找到 codex 命令，请安装并登录：npm i -g @openai/codex，然后 codex login";
      if (config.effective_backend === "openai") text += `（暂时回退到 OpenAI 兼容 API：${openaiModel}）`;
      return { text, state: "missing" };
    }
    if (config.ai_ready === false) {
      return { text: "AI 状态：✗ Codex CLI 暂不可用，请确认已运行 codex login", state: "missing" };
    }
    return { text: `AI 状态：✓ 使用 Codex CLI（${configCodexModel(config)}）`, state: "ok" };
  }
  return { text: `AI 状态（OpenAI 兼容 API）：${apiKeyStatusText(hasKey)}`, state: hasKey ? "ok" : "missing" };
}

async function populateSettings() {
  const keyStatus = document.getElementById("apiKeyStatus");
  if (keyStatus) { keyStatus.textContent = "AI 状态：读取中…"; delete keyStatus.dataset.state; }
  const config = await fetchConfig();
  settingsConfig = config;
  settingsBackendSupported = Boolean(config && typeof config.AI_BACKEND === "string");
  if (config) {
    const hasKey = configHasApiKey(config);
    form.OPENAI_API_KEY.value = "";
    form.OPENAI_API_KEY.placeholder = hasKey ? "已配置（留空则保持不变）" : "sk-...";
    if (form.OPENAI_MODEL) form.OPENAI_MODEL.value = config.OPENAI_MODEL || "";
    form.OPENAI_BASE_URL.value = config.OPENAI_BASE_URL || "";
    form.OPENAI_PROXY.value = config.OPENAI_PROXY || "";
    if (settingsBackendSupported) {
      if (form.AI_BACKEND) form.AI_BACKEND.value = config.AI_BACKEND === "openai" ? "openai" : "codex";
      if (form.CODEX_MODEL) form.CODEX_MODEL.value = typeof config.CODEX_MODEL === "string" ? config.CODEX_MODEL : "";
    }
    renderConfigSources(config.sources);
    const btnClearApiKey = document.getElementById("btnClearApiKey");
    if (btnClearApiKey) btnClearApiKey.disabled = !hasKey;
  } else {
    renderConfigSources(null);
  }
  syncBackendSections();
  if (keyStatus) {
    const status = describeAiStatus(config);
    keyStatus.textContent = status.text;
    keyStatus.dataset.state = status.state;
  }
  return config;
}

if (form && form.AI_BACKEND && typeof form.AI_BACKEND.forEach === "function") {
  form.AI_BACKEND.forEach((radio) => radio.addEventListener("change", syncBackendSections));
}

function backendLabel(backend) {
  if (backend === "codex") return "Codex CLI";
  if (backend === "openai") return "OpenAI 兼容 API";
  return backend ? String(backend) : "";
}

async function testConnection() {
  const button = document.getElementById("btnTestConnection");
  // The test uses the saved config, so describe the saved effective backend.
  const savedBackend = settingsBackendSupported ? configEffectiveBackend(settingsConfig) : "openai";
  if (button) { button.disabled = true; button.textContent = "测试中…"; }
  setConnectionResult(savedBackend === "codex"
    ? "正在测试…Codex 首次调用可能需要几十秒"
    : "正在发送一次极小的测试请求…");
  try {
    const res = await fetch("/api/test_connection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    if (res.status === 404 || res.status === 501) throw new Error("当前服务器不支持连接测试，请更新后端。");
    const payload = await res.json().catch(() => ({}));
    const backendText = backendLabel(payload.backend);
    if (!res.ok || !payload.ok) {
      const detail = payload.error || payload.message || `HTTP ${res.status}`;
      throw new Error(backendText ? `${backendText} · ${detail}` : detail);
    }
    const latency = payload.latency_ms != null && Number.isFinite(Number(payload.latency_ms)) ? ` · ${Math.round(Number(payload.latency_ms))} ms` : "";
    const parts = [backendText, payload.model].filter(Boolean).map((part) => ` · ${part}`).join("");
    setConnectionResult(`✓ 连接成功${parts}${latency}`, "ok");
  } catch (error) {
    setConnectionResult(`✗ 连接失败：${error.message || "网络错误"}`, "error");
  } finally {
    if (button) { button.disabled = false; button.textContent = "🔌 测试连接"; }
  }
}

async function clearApiKey() {
  const fromEnv = settingsSources.OPENAI_API_KEY === "env";
  const question = fromEnv
    ? "清除 config.json 中保存的 API Key？\n注意：当前生效的密钥来自环境变量，清除后仍会继续使用环境变量中的密钥。"
    : "清除已保存的 API Key？\n清除后 AI 翻译、分类和总结将不可用，直到重新填写。";
  if (!confirm(question)) return;
  const button = document.getElementById("btnClearApiKey");
  setFormError("settingsError", "");
  if (button) button.disabled = true;
  try {
    const res = await fetch("/api/save_config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear_api_key: true })
    });
    if (!res.ok) throw new Error(await responseMessage(res, "清除失败"));
    setConnectionResult("");
    showToast(fromEnv ? "已清除 config.json 中的密钥（环境变量中的密钥仍然生效）。" : "API Key 已清除。", "success");
    await populateSettings();
  } catch (error) {
    setFormError("settingsError", `清除密钥失败：${error.message || "网络错误"}`);
    if (button) button.disabled = false;
  }
}

if (btnSettings && modal) {
  btnSettings.addEventListener("click", async () => {
    setFormError("settingsError", "");
    setConnectionResult("");
    modal.showModal();
    await populateSettings();
  });
}

const btnTestConnection = document.getElementById("btnTestConnection");
if (btnTestConnection) btnTestConnection.addEventListener("click", testConnection);
const btnClearApiKey = document.getElementById("btnClearApiKey");
if (btnClearApiKey) btnClearApiKey.addEventListener("click", clearApiKey);

if (btnCategories && categoriesModal) {
  btnCategories.addEventListener("click", async () => {
    closeMoreMenu();
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
    if (form.OPENAI_MODEL) data.OPENAI_MODEL = form.OPENAI_MODEL.value.trim();
    if (settingsBackendSupported) {
      data.AI_BACKEND = selectedBackend();
      if (form.CODEX_MODEL) data.CODEX_MODEL = form.CODEX_MODEL.value.trim();
    }
    const saveButton = document.getElementById("btnSaveSettings");
    setFormError("settingsError", "");

    try {
      if (saveButton) { saveButton.disabled = true; saveButton.textContent = "保存中…"; }
      const res = await fetch("/api/save_config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data)
      });
      if (res.ok) {
        modal.close();
        showToast("设置已保存，将立即用于下次 AI 任务。", "success");
      } else {
        setFormError("settingsError", `保存失败：${await responseMessage(res, "服务器错误")}`);
      }
    } catch (err) {
      setFormError("settingsError", `保存出错：${err.message || "网络错误"}`);
    } finally {
      if (saveButton) { saveButton.disabled = false; saveButton.textContent = "保存"; }
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
        throw new Error(await responseMessage(res, "保存失败"));
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
      showToast("分类配置已保存。", "success");
    } catch (err) {
      showToast("分类保存失败：" + err.message, "error");
    }
  });
}

if (classificationForm && classificationModal) {
  classificationForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const saveButton = document.getElementById("btnSaveClassification");
    if (saveButton) { saveButton.disabled = true; saveButton.textContent = "保存中…"; }
    const saved = await saveClassificationEdits();
    if (saveButton) { saveButton.disabled = false; saveButton.textContent = "保存"; }
    if (saved) classificationModal.close();
  });
}

if (btnCancelClassification && classificationModal) {
  btnCancelClassification.addEventListener("click", () => {
    classificationModal.close();
  });
}

if (btnRefresh) {
  btnRefresh.addEventListener("click", async () => {
    btnRefresh.blur();
    // Fetching is free; only ask for confirmation when new titles will be
    // sent to the AI (AI is ready and therefore quota/costs apply).
    const config = await fetchConfig();
    const refreshCost = configEffectiveBackend(config) === "codex"
      ? `将通过 Codex CLI 调用 ${configCodexModel(config)}，消耗少量 ChatGPT 订阅额度`
      : "将消耗少量 API 额度";
    if (configAiReady(config) && !confirm(`立即从 RSS 源更新？\n新论文的标题会调用 AI 进行翻译和分类，${refreshCost}。`)) {
      return;
    }
    await startJob("fetch");
  });
}

// --- Keyword editor ---

const keywordsModal = document.getElementById("keywordsModal");
const keywordsForm = document.getElementById("keywordsForm");
const keywordsText = document.getElementById("keywordsText");
const keywordsPreview = document.getElementById("keywordsPreview");
const KEYWORDS_PLACEHOLDER = "consumer\n\"social media\" AND brand\n# 注释行";

function keywordsUnsupported(res) {
  return res.status === 404 || res.status === 501;
}

function renderKeywordPreview(result) {
  if (!keywordsPreview) return;
  keywordsPreview.textContent = "";
  const summary = document.createElement("p");
  summary.className = "keywords-preview__summary";
  summary.textContent = `已入库 ${result.total_papers ?? 0} 篇中，有 ${result.matched ?? 0} 篇匹配这些规则。`;
  keywordsPreview.appendChild(summary);
  const terms = ensureArray(result.terms);
  if (terms.length) {
    const list = document.createElement("ul");
    list.className = "keywords-preview__terms";
    terms.forEach((entry) => {
      const li = document.createElement("li");
      li.textContent = `${entry.term}：${entry.count} 篇`;
      if (!entry.count) li.classList.add("is-zero");
      list.appendChild(li);
    });
    keywordsPreview.appendChild(list);
  }
  const samples = ensureArray(result.samples);
  if (samples.length) {
    const title = document.createElement("p");
    title.className = "keywords-preview__label";
    title.textContent = "匹配示例：";
    const list = document.createElement("ul");
    list.className = "keywords-preview__samples";
    samples.slice(0, 10).forEach((sample) => {
      const li = document.createElement("li");
      li.textContent = sample.title || sample.paper_id || "";
      list.appendChild(li);
    });
    keywordsPreview.append(title, list);
  }
}

async function openKeywordsModal() {
  if (!keywordsModal) return;
  setFormError("keywordsError", "");
  if (keywordsPreview) keywordsPreview.textContent = "";
  if (keywordsText) { keywordsText.value = ""; keywordsText.placeholder = "加载中…"; }
  keywordsModal.showModal();
  try {
    const res = await fetch("/api/keywords", { cache: "no-store" });
    if (keywordsUnsupported(res)) throw new Error("当前服务器不支持在线编辑关键词，请更新后端或直接编辑 keywords.dat。");
    if (!res.ok) throw new Error(await responseMessage(res, "读取失败"));
    const payload = await res.json();
    if (keywordsText) keywordsText.value = payload.text || "";
  } catch (error) {
    setFormError("keywordsError", error.message || "无法读取关键词。");
  } finally {
    if (keywordsText) keywordsText.placeholder = KEYWORDS_PLACEHOLDER;
  }
}

async function previewKeywords() {
  const button = document.getElementById("btnPreviewKeywords");
  setFormError("keywordsError", "");
  if (button) { button.disabled = true; button.textContent = "预览中…"; }
  try {
    const res = await fetch("/api/keywords/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: keywordsText ? keywordsText.value : "" })
    });
    if (keywordsUnsupported(res)) throw new Error("当前服务器不支持关键词预览。");
    if (!res.ok) throw new Error(await responseMessage(res, "预览失败"));
    renderKeywordPreview(await res.json());
  } catch (error) {
    setFormError("keywordsError", error.message || "预览失败。");
  } finally {
    if (button) { button.disabled = false; button.textContent = "预览匹配"; }
  }
}

if (keywordsForm && keywordsModal) {
  keywordsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = document.getElementById("btnSaveKeywords");
    setFormError("keywordsError", "");
    if (button) { button.disabled = true; button.textContent = "保存中…"; }
    try {
      const res = await fetch("/api/keywords", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: keywordsText ? keywordsText.value : "" })
      });
      if (keywordsUnsupported(res)) throw new Error("当前服务器不支持保存关键词，请更新后端或直接编辑 keywords.dat。");
      if (!res.ok) throw new Error(await responseMessage(res, "保存失败"));
      const payload = await res.json().catch(() => ({}));
      keywordsModal.close();
      const count = ensureArray(payload.keywords).length;
      showToast(`关键词已保存${count ? `（${count} 条规则）` : ""}，将用于下次抓取的新论文。`, "success");
    } catch (error) {
      setFormError("keywordsError", error.message || "保存失败。");
    } finally {
      if (button) { button.disabled = false; button.textContent = "保存"; }
    }
  });
}

const btnKeywords = document.getElementById("btnKeywords");
if (btnKeywords) btnKeywords.addEventListener("click", openKeywordsModal);
const btnPreviewKeywords = document.getElementById("btnPreviewKeywords");
if (btnPreviewKeywords) btnPreviewKeywords.addEventListener("click", previewKeywords);
const btnCancelKeywords = document.getElementById("btnCancelKeywords");
if (btnCancelKeywords && keywordsModal) btnCancelKeywords.addEventListener("click", () => keywordsModal.close());

// --- Header "更多" menu & shortcuts overlay ---

function closeMoreMenu() {
  const menu = document.getElementById("moreMenu");
  if (menu) menu.open = false;
}

function setupMoreMenu() {
  const menu = document.getElementById("moreMenu");
  if (!menu) return;
  document.addEventListener("click", (event) => {
    if (menu.open && event.target instanceof Element && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.open) {
      menu.open = false;
      const summary = menu.querySelector("summary");
      if (summary) summary.focus();
    }
  });
  const btnShortcuts = document.getElementById("btnShortcuts");
  if (btnShortcuts) btnShortcuts.addEventListener("click", () => { closeMoreMenu(); toggleShortcutsOverlay(true); });
  const btnCloseShortcuts = document.getElementById("btnCloseShortcuts");
  if (btnCloseShortcuts) btnCloseShortcuts.addEventListener("click", () => toggleShortcutsOverlay(false));
}

function setupFilters() {
  const buttons = document.querySelectorAll('.filter-btn');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      // Tabs must not keep focus, otherwise the focused button would make
      // the page feel unresponsive to single-key shortcuts.
      btn.blur();
      if (state.swipeBusy) {
        setStatus("正在保存操作，请稍候再切换视图。");
        return;
      }
      setFilterMode(btn.dataset.filter);
      applyFilters();
      restoreViewPosition();
    });
  });
}

async function exportFavoritesRis() {
  if (!ensureArray(state.interactions.favorites).length) {
    const message = "还没有收藏论文，无法导出 RIS。";
    setStatus(message);
    showToast(message, "info");
    return false;
  }
  try {
    if (btnExportFavorites) btnExportFavorites.disabled = true;
    const response = await fetch("/api/export_favorites_ris", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    if (!response.ok) throw new Error(`导出失败（HTTP ${response.status || "错误"}）`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "paper-feed-favorites.ris";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setStatus("RIS 文件已开始下载。");
    return true;
  } catch (error) {
    const message = `RIS 导出失败：${error.message || "网络错误"}`;
    setStatus(message);
    showToast(message, "error");
    return false;
  } finally {
    if (btnExportFavorites) btnExportFavorites.disabled = false;
  }
}

if (btnExportFavorites) btnExportFavorites.addEventListener("click", exportFavoritesRis);

function setupInboxViewToggle() {
  if (!elements.inboxViewToggle) return;
  elements.inboxViewToggle.querySelectorAll("[data-inbox-view]").forEach((button) => {
    button.addEventListener("click", () => {
      button.blur();
      if (state.swipeBusy) {
        setStatus("正在保存操作，请稍候再切换视图。");
        return;
      }
      const mode = button.dataset.inboxView;
      if (!mode || mode === state.inboxViewMode) return;
      setInboxViewMode(mode);
      applyFilters();
      restoreViewPosition();
    });
  });
}

async function init() {
  restoreUiState();
  syncViewControls();
  setupFilters();
  setupInboxViewToggle();
  setupMoreMenu();
  setupMultiFilterDropdowns();
  setupPositionTracking();
  setupDraftUnloadWarning();
  document.addEventListener("keydown", handleTriageShortcut);
  await loadInteractions();
  await loadCategories();
  await loadFeed();
  resumeRunningJobs();
}

document.addEventListener("DOMContentLoaded", init);
