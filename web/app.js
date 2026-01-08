const state = {
  items: [],
  filtered: [],
  keywords: [],
  interactions: { favorites: [], hidden: [] },
  filterMode: 'all' // 'all' or 'favorites'
};

const elements = {
  list: document.getElementById("list"),
  countLabel: document.getElementById("countLabel"),
  generatedAt: document.getElementById("generatedAt"),
  searchInput: document.getElementById("searchInput"),
  journalSelect: document.getElementById("journalSelect"),
  fromDate: document.getElementById("fromDate"),
  toDate: document.getElementById("toDate"),
  sortSelect: document.getElementById("sortSelect"),
  summaryToggle: document.getElementById("summaryToggle"),
  cardTemplate: document.getElementById("cardTemplate")
};

const formatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "2-digit"
});

// --- Interaction Logic ---

async function loadInteractions() {
  try {
    const res = await fetch("/api/interactions?t=" + Date.now());
    if (res.ok) {
      state.interactions = await res.json();
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

  // Auto-hide after 5 seconds
  let timeoutId = setTimeout(() => {
    undoContainer.textContent = '';
  }, 5000);

  // Handle Undo
  undoBtn.onclick = () => {
    clearTimeout(timeoutId);

    // Restore State
    state.interactions.hidden = state.interactions.hidden.filter(x => x !== id);
    saveInteraction(id, 'unhide');

    // Restore card
    card.classList.remove('hidden');

    // Hide undo bar
    undoContainer.textContent = '';
  };
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

function renderList() {
  elements.list.innerHTML = "";
  const showSummary = elements.summaryToggle.checked;
  const highlightTerms = getHighlightTerms();

  if (state.filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "card";
    empty.textContent = state.filterMode === 'favorites'
      ? "还没有收藏任何文章。"
      : "没有匹配结果，换个条件试试。";
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
    const summary = node.querySelector(".card__summary");
    const fields = node.querySelector(".card__fields");
    const toggle = node.querySelector(".card__toggle");

    meta.textContent = `${item.journal || "Unknown"} · ${formatDate(item.date)}`;
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
    if (!fields.children.length) {
      fields.remove();
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
    
    const btnLike = document.createElement("button");
    btnLike.className = `action-btn ${isLiked ? 'liked' : ''}`;
    btnLike.innerHTML = isLiked ? '❤️' : '🤍';
    btnLike.title = isLiked ? "取消收藏" : "收藏";
    btnLike.onclick = function(e) { e.preventDefault(); toggleLike(item.link, this); };
    
    const btnHide = document.createElement("button");
    btnHide.className = "action-btn";
    btnHide.innerHTML = '❌';
    btnHide.title = "不感兴趣";
    btnHide.onclick = function(e) { 
      e.preventDefault(); 
      toggleHide(item.link, this); 
    };
    
    actionsDiv.appendChild(btnLike);
    actionsDiv.appendChild(btnHide);
    
    card.appendChild(actionsDiv);
    // ---------------------

    fragment.appendChild(node);
  }

  // Single DOM insertion instead of 1000 (avoids 1000 reflows!)
  elements.list.appendChild(fragment);
}

function applyFilters() {
  const keyword = normalize(elements.searchInput.value);
  const journal = elements.journalSelect.value;
  const fromDate = elements.fromDate.value ? new Date(elements.fromDate.value) : null;
  const toDate = elements.toDate.value ? new Date(elements.toDate.value) : null;

  const filtered = state.items.filter((item) => {
    // 1. Check interactions first
    if (state.interactions.hidden.includes(item.link)) return false;
    if (state.filterMode === 'favorites' && !state.interactions.favorites.includes(item.link)) return false;
    // In "all" mode, hide items that have been favorited (inbox-style: processed items disappear)
    if (state.filterMode === 'all' && state.interactions.favorites.includes(item.link)) return false;

    if (journal && item.journal !== journal) {
      return false;
    }

    if (fromDate && item.date < fromDate) {
      return false;
    }

    if (toDate && item.date > toDate) {
      return false;
    }

    if (keyword) {
      const haystack = `${item.title} ${item.title_zh || ""} ${item.summary} ${item.journal}`.toLowerCase();
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
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      case "'":
        return "&#39;";
      default:
        return char;
    }
  });
}

function cleanJournalName(name) {
  return (name || "")
    .replace(/^ScienceDirect(?:\s+Publication)?(?:[:\-]|\s)+/i, "")
    .replace(/\s*\[.*?\]\s*$/, "")
    .trim();
}

function stripBracketedPrefix(title) {
  return (title || "").replace(/^\[[^\]]+\]\s*/, "").trim();
}

function normalizeLine(text) {
  return (text || "").replace(/\s+/g, " ").trim();
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

  // 2. Parse HTML structure
  const doc = new DOMParser().parseFromString(decoded, "text/html");
  
  // 3. Extract paragraphs and filter common metadata patterns
  const paragraphs = Array.from(doc.body.querySelectorAll("p, div, span"))
    .map((p) => normalizeLine(p.textContent))
    .filter(Boolean);

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

  // Fallback: if no paragraphs detected, try splitting by newline on raw text
  const lines = paragraphs.length ? paragraphs : normalizeLine(doc.body.textContent).split(". ");

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

function attachHandlers() {
  const controls = [
    elements.searchInput,
    elements.journalSelect,
    elements.fromDate,
    elements.toDate,
    elements.sortSelect,
    elements.summaryToggle
  ];
  controls.forEach((control) => control.addEventListener("input", applyFilters));
}

async function loadFeed() {
  setStatus("加载中...");
  try {
    const response = await fetch("feed.json");
    if (!response.ok) {
      throw new Error("feed.json missing");
    }
    const payload = await response.json();
    state.keywords = payload.keywords || [];
    state.items = (payload.items || []).map((item) => {
      const parsed = parseSummary(item.summary);
      return {
        ...item,
        journal: cleanJournalName(item.journal),
        title: stripBracketedPrefix(item.title || ""),
        summary: parsed.text,
        summaryShort: truncateText(parsed.text, 360),
        publicationDate: parsed.publicationDate,
        source: parsed.source,
        authors: parsed.authors,
        date: new Date(item.pub_date)
      };
    });

    populateJournals(state.items);
    elements.generatedAt.textContent = payload.generated_at
      ? `更新于 ${formatDate(new Date(payload.generated_at))}`
      : "";
    attachHandlers();
    applyFilters();
  } catch (error) {
    setStatus("无法加载 feed.json，请先运行 get_RSS.py 并用本地服务器打开页面。");
  }
}

// --- Settings & API Logic ---

const modal = document.getElementById("settingsModal");
const form = document.getElementById("settingsForm");
const btnSettings = document.getElementById("btnSettings");
const btnRefresh = document.getElementById("btnRefresh");
const btnCancel = document.getElementById("btnCancel");

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
      applyFilters();
    });
  });
}

async function init() {
  setupFilters();
  await loadInteractions();
  await loadFeed();
}

document.addEventListener("DOMContentLoaded", init);
