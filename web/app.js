const state = {
  items: [],
  filtered: [],
  keywords: [],
  interactions: { favorites: [], archived: [], hidden: [] },
  filterMode: 'all', // 'all' | 'favorites' | 'archived'
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
  topicCloudWrap: document.getElementById("topicCloudWrap")
};

const formatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "2-digit"
});

let undoTimeoutId = null;
let currentClassificationItem = null;

// --- Interaction Logic ---

function ensureArray(value) {
  return Array.isArray(value) ? value : [];
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

function toggleLike(id, btnElement) {
  const isLiked = state.interactions.favorites.includes(id);
  const action = isLiked ? 'unlike' : 'like';
  const card = btnElement.closest('.card');

  if (isLiked) {
    state.interactions.favorites = state.interactions.favorites.filter(x => x !== id);
  } else {
    state.interactions.favorites.push(id);
    // If liking, ensure it's unhidden in state
    if (state.interactions.hidden.includes(id)) {
      state.interactions.hidden = state.interactions.hidden.filter(x => x !== id);
    }
    if (state.interactions.archived.includes(id)) {
      state.interactions.archived = state.interactions.archived.filter(x => x !== id);
    }
  }

  saveInteraction(id, action);

  // Update button's state first (avoid re-rendering 1000 elements!)
  if (btnElement) {
    if (isLiked) {
      btnElement.classList.remove('liked');
      btnElement.innerHTML = '🤍';
      btnElement.title = "收藏";
    } else {
      btnElement.classList.add('liked');
      btnElement.innerHTML = '❤️';
      btnElement.title = "取消收藏";
    }
  }

  // Hide the card after interaction (makes the list feel like an inbox)
  if (card) {
    card.classList.add('hidden');
  }
  updateFilterCounts();
}

function toggleHide(id, btnElement) {
  // 1. Find the card element
  const card = btnElement.closest('.card');
  if (!card) return;

  // 2. Immediate visual feedback - collapse the card
  card.classList.add('hidden');

  // 3. Update State (synchronous for consistency)
  if (!state.interactions.hidden.includes(id)) {
    state.interactions.hidden.push(id);
  }
  if (state.interactions.favorites.includes(id)) {
    state.interactions.favorites = state.interactions.favorites.filter(x => x !== id);
  }
  if (state.interactions.archived.includes(id)) {
    state.interactions.archived = state.interactions.archived.filter(x => x !== id);
  }

  updateFilterCounts();

  // 4. Defer non-critical operations
  requestAnimationFrame(() => {
    // Persist to server (async, won't block UI)
    saveInteraction(id, 'hide');

    // Show Undo Bar after DOM settles
    showUndoBar(id, card);
  });
}

function showUndoBar(id, card) {
  const undoContainer = document.getElementById('undoContainer');

  // Clear any existing undo bar (use textContent for performance)
  undoContainer.textContent = '';

  // Create elements without innerHTML (faster)
  const undoBar = document.createElement('div');
  undoBar.className = 'undo-bar';

  const span = document.createElement('span');
  span.textContent = '已隐藏文章';

  const undoBtn = document.createElement('button');
  undoBtn.className = 'undo-btn';
  undoBtn.textContent = '撤销';

  undoBar.appendChild(span);
  undoBar.appendChild(undoBtn);

  // Append to fixed container
  undoContainer.appendChild(undoBar);

  // Auto-hide after 10 seconds
  if (undoTimeoutId) {
    clearTimeout(undoTimeoutId);
  }
  undoTimeoutId = setTimeout(() => {
    undoContainer.textContent = '';
  }, 10000);

  // Handle Undo
  undoBtn.onclick = () => {
    if (undoTimeoutId) {
      clearTimeout(undoTimeoutId);
      undoTimeoutId = null;
    }

    // Restore State
    state.interactions.hidden = state.interactions.hidden.filter(x => x !== id);
    saveInteraction(id, 'unhide');

    // Restore card
    card.classList.remove('hidden');
    updateFilterCounts();

    // Hide undo bar
    undoContainer.textContent = '';
  };
}

function toggleArchive(id, btnElement) {
  const isArchived = state.interactions.archived.includes(id);
  const action = isArchived ? "unarchive" : "archive";
  const card = btnElement.closest(".card");

  if (isArchived) {
    state.interactions.archived = state.interactions.archived.filter(x => x !== id);
  } else {
    state.interactions.archived.push(id);
    if (state.interactions.favorites.includes(id)) {
      state.interactions.favorites = state.interactions.favorites.filter(x => x !== id);
    }
    if (state.interactions.hidden.includes(id)) {
      state.interactions.hidden = state.interactions.hidden.filter(x => x !== id);
    }
  }

  saveInteraction(id, action);
  if (card) {
    card.classList.add("hidden");
  }
  updateFilterCounts();
}

function restoreFromArchive(id, btnElement) {
  const card = btnElement.closest(".card");
  if (!state.interactions.favorites.includes(id)) {
    state.interactions.favorites.push(id);
  }
  if (state.interactions.archived.includes(id)) {
    state.interactions.archived = state.interactions.archived.filter(x => x !== id);
  }
  if (state.interactions.hidden.includes(id)) {
    state.interactions.hidden = state.interactions.hidden.filter(x => x !== id);
  }

  saveInteraction(id, "restore");
  if (card) {
    card.classList.add("hidden");
  }
  updateFilterCounts();
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
    ...(state.interactions.favorites || []),
    ...(state.interactions.archived || [])
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

function updateFilterCounts() {
  const favorites = new Set(state.interactions.favorites);
  const archived = new Set(state.interactions.archived);
  const hidden = new Set(state.interactions.hidden);

  let inboxCount = 0;
  // Inbox is items NOT in favorites, archived, hidden
  state.items.forEach(item => {
    if (!favorites.has(item.link) && !archived.has(item.link) && !hidden.has(item.link)) {
      inboxCount++;
    }
  });
  
  const favCount = favorites.size;
  const archCount = archived.size;

  const elInbox = document.getElementById("countInbox");
  const elFav = document.getElementById("countFavorites");
  const elArch = document.getElementById("countArchived");

  if (elInbox) elInbox.textContent = inboxCount > 0 ? inboxCount : "";
  if (elFav) elFav.textContent = favCount > 0 ? favCount : "";
  if (elArch) elArch.textContent = archCount > 0 ? archCount : "";
}

function renderList() {
  elements.list.innerHTML = "";
  const showSummary = elements.summaryToggle.checked;
  const highlightTerms = getHighlightTerms();

  if (state.filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "card";
    if (state.filterMode === "favorites") {
      empty.textContent = "还没有收藏任何文章。";
    } else if (state.filterMode === "archived") {
      empty.textContent = "暂无已归档文章。";
    } else {
      empty.textContent = "暂时没有新的文献了...";
    }
    elements.list.appendChild(empty);
    return;
  }

  // Use DocumentFragment for batch DOM insertion (1000x faster!)
  const fragment = document.createDocumentFragment();

  for (const item of state.filtered) {
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
    appendField(fields, "理论", item.theoriesText, highlightTerms);
    appendField(fields, "情境", item.contextText, highlightTerms);
    appendField(fields, "对象", item.subjectsText, highlightTerms);
    if (!fields.children.length) {
      fields.remove();
    }

    // Display abstract if available
    if (showSummary && item.abstract) {
      abstractDiv.className = "card__abstract";

      // Add source badge
      const sourceBadge = {
        'crossref': { emoji: '📚', text: 'Crossref', color: '#2196F3' },
        'semantic_scholar': { emoji: '🔬', text: 'Semantic Scholar', color: '#9C27B0' },
        'gpt_generated': { emoji: '🤖', text: 'AI 生成', color: '#FF9800' },
        'gpt_summarized': { emoji: '🤖', text: 'AI 总结', color: '#FF9800' },
        'user_provided': { emoji: '✏️', text: '用户补充', color: '#4CAF50' }
      };

      const source = sourceBadge[item.abstract_source] || { emoji: '📄', text: '摘要', color: '#757575' };

      abstractDiv.innerHTML = `
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
          <span style="background: ${source.color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;">
            ${source.emoji} ${source.text}
          </span>
        </div>
        <div style="background: #f8f9fa; padding: 12px; border-radius: 8px; border-left: 3px solid ${source.color}; margin-bottom: 12px; line-height: 1.6; color: #444;">
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
    
    const isLiked = state.interactions.favorites.includes(item.link);
    const isArchived = state.interactions.archived.includes(item.link);
    
    const btnLike = document.createElement("button");
    btnLike.className = `action-btn ${isLiked ? 'liked' : ''}`;
    btnLike.innerHTML = isLiked ? '❤️' : '🤍';
    btnLike.title = isLiked ? "取消收藏" : "收藏";
    btnLike.onclick = function(e) { e.preventDefault(); toggleLike(item.link, this); };

    const btnArchive = document.createElement("button");
    btnArchive.className = "action-btn";
    // If in favorites (not archived), show box (archive). If in archived, show upload (unarchive).
    btnArchive.innerHTML = isArchived ? "📤" : "📦"; 
    btnArchive.title = isArchived ? "取消归档 (回到收件箱)" : "归档 (移出收藏)";
    btnArchive.onclick = function(e) { e.preventDefault(); toggleArchive(item.link, this); };

    const btnRestore = document.createElement("button");
    btnRestore.className = "action-btn";
    btnRestore.innerHTML = "↩️";
    btnRestore.title = "恢复到收藏";
    btnRestore.onclick = function(e) { e.preventDefault(); restoreFromArchive(item.link, this); };

    const btnClassify = document.createElement("button");
    btnClassify.className = "action-btn";
    btnClassify.innerHTML = '🏷️';
    btnClassify.title = "编辑分类";
    btnClassify.onclick = function(e) {
      e.preventDefault();
      openClassificationModal(item);
    };

    // Edit Abstract Button
    const btnEdit = document.createElement("button");
    btnEdit.className = "action-btn";
    btnEdit.innerHTML = '✏️';
    btnEdit.title = "补充/编辑摘要";
    
    // Edit Area Elements
    const editArea = node.querySelector(".card__edit-area");
    const textarea = editArea.querySelector("textarea");
    const btnSave = editArea.querySelector(".btn-save-abstract");
    const btnCancel = editArea.querySelector(".btn-cancel-abstract");

    btnEdit.onclick = function(e) {
        e.preventDefault();
        // Toggle visibility
        if (editArea.style.display === "none") {
            editArea.style.display = "block";
            
            // Intelligent pre-fill
            let prefillValue = "";
            if (item.raw_abstract) {
                prefillValue = item.raw_abstract;
            } else if (item.abstract_source === "gpt_generated") {
                // If it was generated from title only, don't prefill the "fake" summary.
                // Let user paste the real one.
                prefillValue = ""; 
            } else {
                // Fallback to whatever is current
                prefillValue = item.abstract || "";
            }
            
            textarea.value = prefillValue;
            textarea.focus();
        } else {
            editArea.style.display = "none";
        }
    };

    btnCancel.onclick = function() {
        editArea.style.display = "none";
    };

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
                    id: item.id, // Use strict ID
                    abstract: newText
                })
            });
            
            if (res.ok) {
                // Update local state temporarily so UI reflects change without full reload
                item.abstract = newText;
                item.raw_abstract = newText; // Also update raw so next edit shows this
                item.abstract_source = "user_provided";
                
                // Refresh the list to render new abstract state
                // (Optimally we'd just update DOM, but re-render is safer for badge logic)
                renderList(); 
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
    
    const btnHide = document.createElement("button");
    btnHide.className = "action-btn";
    btnHide.innerHTML = '❌';
    btnHide.title = "不感兴趣";
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

  const filtered = state.items.filter((item) => {
    // 1. Check interactions first
    if (state.interactions.hidden.includes(item.link)) return false;
    if (state.filterMode === 'favorites' && !state.interactions.favorites.includes(item.link)) return false;
    if (state.filterMode === 'archived' && !state.interactions.archived.includes(item.link)) return false;
    // In "all" mode, hide items that have been processed (favorites or archived)
    if (state.filterMode === 'all') {
      if (state.interactions.favorites.includes(item.link)) return false;
      if (state.interactions.archived.includes(item.link)) return false;
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
  setStatus(`共 ${filtered.length} 篇`);
  renderList();
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
    updateFilterCounts();
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
      
      // Toggle summarize button visibility
      if (btnSummarizeFavorites) {
        btnSummarizeFavorites.style.display = state.filterMode === 'favorites' ? 'inline-block' : 'none';
      }

      applyFilters();
    });
  });
}

async function init() {
  setupFilters();
  await loadInteractions();
  await loadCategories();
  await loadFeed();
}

document.addEventListener("DOMContentLoaded", init);
