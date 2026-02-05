const statsState = {
  items: [],
  interactions: { favorites: [], archived: [], hidden: [] },
  categories: { topics: [], methods: [] }
};

const statsElements = {
  journalSelect: document.getElementById("statsJournal"),
  fromDate: document.getElementById("statsFromDate"),
  toDate: document.getElementById("statsToDate"),
  status: document.getElementById("statsStatus"),
  generatedAt: document.getElementById("statsGeneratedAt"),
  statTotal: document.getElementById("statTotal"),
  statSpan: document.getElementById("statSpan"),
  statPerWeek: document.getElementById("statPerWeek"),
  statWeekNote: document.getElementById("statWeekNote"),
  statPerMonth: document.getElementById("statPerMonth"),
  statMonthNote: document.getElementById("statMonthNote"),
  statLastDate: document.getElementById("statLastDate"),
  statLastNote: document.getElementById("statLastNote"),
  ratioLikeBar: document.getElementById("ratioLikeBar"),
  ratioDislikeBar: document.getElementById("ratioDislikeBar"),
  ratioNeutralBar: document.getElementById("ratioNeutralBar"),
  ratioLikeText: document.getElementById("ratioLikeText"),
  ratioDislikeText: document.getElementById("ratioDislikeText"),
  ratioNeutralText: document.getElementById("ratioNeutralText"),
  monthChart: document.getElementById("monthChart"),
  topicDistribution: document.getElementById("topicDistribution"),
  topicTrend: document.getElementById("topicTrend"),
  topicNetwork: document.getElementById("topicNetwork"),
  topicRadar: document.getElementById("topicRadar"),
  overviewBody: document.getElementById("overviewBody"),
  overviewCount: document.getElementById("overviewCount"),
  overviewSort: document.getElementById("overviewSort"),
  tabButtons: document.querySelectorAll("[data-tab]"),
  tabPanels: document.querySelectorAll("[data-tab-panel]"),
  fitJournalList: document.getElementById("fitJournalList"),
  fitSort: document.getElementById("fitSort"),
  fitOverviewCount: document.getElementById("fitOverviewCount"),
  fitCompareMode: document.getElementById("fitCompareMode"),
  fitCompareJournal: document.getElementById("fitCompareJournal"),
  fitCompareJournalWrap: document.getElementById("fitCompareJournalWrap"),
  fitTopicBaseline: document.getElementById("fitTopicBaseline"),
  fitTopicBaselineLabel: document.getElementById("fitTopicBaselineLabel"),
  fitMethodBaseline: document.getElementById("fitMethodBaseline"),
  fitMethodBaselineLabel: document.getElementById("fitMethodBaselineLabel"),
  fitTopicTopList: document.getElementById("fitTopicTopList"),
  fitMethodTopList: document.getElementById("fitMethodTopList"),
  fitAbstractInput: document.getElementById("fitAbstractInput"),
  fitMatchBtn: document.getElementById("fitMatchBtn"),
  fitMatchStatus: document.getElementById("fitMatchStatus"),
  fitMatchResults: document.getElementById("fitMatchResults")
};

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "2-digit"
});

const percentFormatter = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  maximumFractionDigits: 1
});

function setStatus(text) {
  statsElements.status.textContent = text;
}

function formatDate(date) {
  if (!date || Number.isNaN(date.getTime())) {
    return "日期未知";
  }
  return dateFormatter.format(date);
}

function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

function normalizeInteractions() {
  const favorites = ensureArray(statsState.interactions.favorites);
  const archived = ensureArray(statsState.interactions.archived);
  const hidden = ensureArray(statsState.interactions.hidden);

  const hiddenSet = new Set(hidden);
  const archivedSet = new Set(archived.filter((id) => !hiddenSet.has(id)));
  const favoritesSet = new Set(
    favorites.filter((id) => !hiddenSet.has(id) && !archivedSet.has(id))
  );

  statsState.interactions = {
    favorites: Array.from(favoritesSet),
    archived: Array.from(archivedSet),
    hidden: Array.from(hiddenSet)
  };
}

function getPositiveSet() {
  return new Set([
    ...(statsState.interactions.favorites || []),
    ...(statsState.interactions.archived || [])
  ]);
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

function extractTopicLabels(item) {
  if (!item) return [];
  const topics = item.topics;
  if (Array.isArray(topics)) {
    return topics
      .map((entry) => (entry && typeof entry === "object" ? entry.name : entry))
      .filter(Boolean);
  }
  if (item.topic) return [item.topic];
  return [];
}

function extractMethodLabels(item) {
  if (!item) return [];
  const methods = item.methods;
  if (Array.isArray(methods)) {
    return methods
      .map((entry) => (entry && typeof entry === "object" ? entry.name : entry))
      .filter(Boolean);
  }
  if (item.method) return [item.method];
  return [];
}

const topicPalette = ["#0ea5e9", "#f97316", "#10b981", "#6366f1", "#ec4899", "#f59e0b"];

function populateJournals(items) {
  const set = new Set(items.map((item) => item.journal).filter(Boolean));
  const journals = Array.from(set).sort((a, b) => a.localeCompare(b));

  const selects = [statsElements.journalSelect, statsElements.fitCompareJournal];
  selects.forEach((select) => {
    if (!select) return;
    select.querySelectorAll("option:not(:first-child)").forEach((opt) => opt.remove());
    for (const journal of journals) {
      const option = document.createElement("option");
      option.value = journal;
      option.textContent = journal;
      select.appendChild(option);
    }
  });

  if (journals.length && statsElements.journalSelect && statsElements.journalSelect.value === "") {
    statsElements.journalSelect.value = journals[0];
  }
  if (journals.length && statsElements.fitCompareJournal && statsElements.fitCompareJournal.value === "") {
    statsElements.fitCompareJournal.value = journals[0];
  }
}

function isWithinRange(item, fromDate, toDate) {
  if (fromDate && (!item.date || item.date < fromDate)) return false;
  if (toDate && (!item.date || item.date > toDate)) return false;
  return true;
}

function computeStats(items, favorites, hidden) {
  const total = items.length;
  const liked = items.filter((item) => favorites.has(item.link)).length;
  const disliked = items.filter((item) => hidden.has(item.link)).length;
  const neutral = Math.max(0, total - liked - disliked);
  const ratioBase = total || 1;
  const likePct = liked / ratioBase;
  const dislikePct = disliked / ratioBase;
  const neutralPct = neutral / ratioBase;
  const dated = items.filter((item) => item.date && !Number.isNaN(item.date.getTime()));
  let lastDate = null;
  if (dated.length) {
    const sorted = dated.map((item) => item.date).sort((a, b) => a - b);
    lastDate = sorted[sorted.length - 1];
  }

  return {
    total,
    liked,
    disliked,
    neutral,
    likePct,
    dislikePct,
    neutralPct,
    lastDate
  };
}

function computeConfidence(total) {
  if (!total) return 0;
  return Math.min(1, Math.log(total + 1) / Math.log(51));
}

function computeFitScore(stats) {
  if (!stats || !stats.total) return 0;
  return (stats.likePct - stats.dislikePct) * 100;
}

function buildLabelCounts(items, extractor) {
  const counts = new Map();
  let total = 0;
  items.forEach((item) => {
    const labels = extractor(item);
    if (!labels.length) return;
    labels.forEach((label) => {
      counts.set(label, (counts.get(label) || 0) + 1);
      total += 1;
    });
  });
  return { counts, total };
}

function normalizeDistribution(dist) {
  const entries = Array.from(dist.counts.entries()).map(([label, count]) => ({
    label,
    count,
    pct: dist.total ? count / dist.total : 0
  }));
  entries.sort((a, b) => b.count - a.count);
  return entries;
}

function buildVector(entries) {
  const labels = entries.map((entry) => entry.label);
  const values = entries.map((entry) => entry.pct);
  return { labels, values };
}

function cosineSimilarity(a, b) {
  if (!a || !b || !a.labels.length || !b.labels.length) return 0;
  const map = new Map();
  a.labels.forEach((label, idx) => map.set(label, { a: a.values[idx], b: 0 }));
  b.labels.forEach((label, idx) => {
    if (!map.has(label)) map.set(label, { a: 0, b: b.values[idx] });
    else map.get(label).b = b.values[idx];
  });
  let dot = 0;
  let normA = 0;
  let normB = 0;
  map.forEach((val) => {
    dot += val.a * val.b;
    normA += val.a * val.a;
    normB += val.b * val.b;
  });
  if (!normA || !normB) return 0;
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function countKeywordMatches(text, keyword) {
  if (!keyword) return 0;
  const normalized = keyword.toLowerCase().trim();
  if (!normalized) return 0;
  if (!text) return 0;
  if (normalized.length <= 2) {
    const re = new RegExp(`\\b${escapeRegExp(normalized)}\\b`, "g");
    return (text.match(re) || []).length;
  }
  if (normalized.includes(" ")) {
    return text.includes(normalized) ? 1 : 0;
  }
  const re = new RegExp(`\\b${escapeRegExp(normalized)}\\b`, "g");
  return (text.match(re) || []).length;
}

function getOverviewSort() {
  return (statsElements.overviewSort && statsElements.overviewSort.value) || "total_desc";
}

function getFitSort() {
  return (statsElements.fitSort && statsElements.fitSort.value) || "fit_desc";
}

function sortOverviewRows(rows, mode) {
  switch (mode) {
    case "like_desc":
      return rows.sort((a, b) => b.stats.likePct - a.stats.likePct || b.stats.total - a.stats.total);
    case "dislike_desc":
      return rows.sort((a, b) => b.stats.dislikePct - a.stats.dislikePct || b.stats.total - a.stats.total);
    case "last_desc":
      return rows.sort((a, b) => {
        const timeA = a.stats.lastDate ? a.stats.lastDate.getTime() : 0;
        const timeB = b.stats.lastDate ? b.stats.lastDate.getTime() : 0;
        return timeB - timeA || b.stats.total - a.stats.total;
      });
    case "name_asc":
      return rows.sort((a, b) => a.journal.localeCompare(b.journal));
    case "total_desc":
    default:
      return rows.sort((a, b) => b.stats.total - a.stats.total || a.journal.localeCompare(b.journal));
  }
}

function sortFitMetrics(rows, mode) {
  switch (mode) {
    case "structure_desc":
      return rows.sort((a, b) => b.structureScore - a.structureScore || b.stats.total - a.stats.total);
    case "confidence_desc":
      return rows.sort((a, b) => b.confidence - a.confidence || b.stats.total - a.stats.total);
    case "total_desc":
      return rows.sort((a, b) => b.stats.total - a.stats.total || a.journal.localeCompare(b.journal));
    case "name_asc":
      return rows.sort((a, b) => a.journal.localeCompare(b.journal));
    case "fit_desc":
    default:
      return rows.sort((a, b) => b.fitScore - a.fitScore || b.stats.total - a.stats.total);
  }
}

function renderOverview(items, fromDate, toDate) {
  statsElements.overviewBody.innerHTML = "";
  const favorites = getPositiveSet();
  const hidden = new Set(statsState.interactions.hidden || []);
  const grouped = new Map();

  for (const item of items) {
    if (!isWithinRange(item, fromDate, toDate)) continue;
    const key = item.journal || "未知期刊";
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(item);
  }

  const rawRows = Array.from(grouped.entries())
    .map(([journal, groupItems]) => ({
      journal,
      stats: computeStats(groupItems, favorites, hidden)
    }));
  const sortMode = getOverviewSort();
  const rows = sortOverviewRows(rawRows, sortMode);

  statsElements.overviewCount.textContent = rows.length ? `${rows.length} 本期刊` : "无期刊数据";

  if (!rows.length) {
    const empty = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.className = "overview-empty";
    cell.textContent = "当前范围内没有可展示的数据。";
    empty.appendChild(cell);
    statsElements.overviewBody.appendChild(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.className = "overview-row";
    tr.dataset.journal = row.journal;

    const name = document.createElement("td");
    name.className = "overview-name";
    name.textContent = row.journal;

    const spacer = document.createElement("td");
    spacer.className = "overview-spacer";

    const total = document.createElement("td");
    total.className = "overview-metric";
    total.textContent = row.stats.total;

    const liked = document.createElement("td");
    liked.className = "overview-metric";
    liked.textContent = row.stats.liked;

    const disliked = document.createElement("td");
    disliked.className = "overview-metric";
    disliked.textContent = row.stats.disliked;

    const neutral = document.createElement("td");
    neutral.className = "overview-metric";
    neutral.textContent = row.stats.neutral;

    const ratio = document.createElement("td");
    const ratioWrap = document.createElement("div");
    ratioWrap.className = "overview-ratio";

    const ratioBar = document.createElement("div");
    ratioBar.className = "overview-bar";

    const likeFill = document.createElement("div");
    likeFill.className = "ratio-fill ratio-fill--like";
    likeFill.style.width = `${Math.round(row.stats.likePct * 100)}%`;

    const dislikeFill = document.createElement("div");
    dislikeFill.className = "ratio-fill ratio-fill--dislike";
    dislikeFill.style.width = `${Math.round(row.stats.dislikePct * 100)}%`;

    const neutralFill = document.createElement("div");
    neutralFill.className = "ratio-fill ratio-fill--neutral";
    neutralFill.style.width = `${Math.round(row.stats.neutralPct * 100)}%`;

    ratioBar.appendChild(likeFill);
    ratioBar.appendChild(dislikeFill);
    ratioBar.appendChild(neutralFill);

    const ratioText = document.createElement("div");
    ratioText.className = "overview-ratio-text";
    ratioText.textContent = row.stats.total
      ? `${percentFormatter.format(row.stats.likePct)} 喜欢`
      : "0% 喜欢";

    ratioWrap.appendChild(ratioBar);
    ratioWrap.appendChild(ratioText);
    ratio.appendChild(ratioWrap);

    const last = document.createElement("td");
    last.className = "overview-metric";
    last.textContent = row.stats.lastDate ? formatDate(row.stats.lastDate) : "未知";

    tr.appendChild(name);
    tr.appendChild(spacer);
    tr.appendChild(total);
    tr.appendChild(liked);
    tr.appendChild(disliked);
    tr.appendChild(neutral);
    tr.appendChild(ratio);
    tr.appendChild(last);
    fragment.appendChild(tr);
  }

  statsElements.overviewBody.appendChild(fragment);
}

async function loadInteractions() {
  try {
    const res = await fetch("/api/interactions?t=" + Date.now(), { cache: "no-store" });
    if (res.ok) {
      statsState.interactions = await res.json();
      normalizeInteractions();
    }
  } catch (e) {
    console.warn("Failed to load interactions", e);
  }
}

async function loadFeed() {
  setStatus("加载中...");
  try {
    const response = await fetch("feed.json?t=" + Date.now(), {
      cache: "no-store",
      headers: {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
      }
    });
    if (!response.ok) {
      throw new Error("feed.json missing");
    }
    const payload = await response.json();
    statsState.items = (payload.items || []).map((item) => ({
      journal: cleanJournalName(item.journal) || "未知期刊",
      link: item.link,
      date: item.pub_date ? new Date(item.pub_date) : null,
      topic: item.topic,
      topics: item.topics || [],
      method: item.method,
      methods: item.methods || []
    }));

    populateJournals(statsState.items);
    statsElements.generatedAt.textContent = payload.generated_at
      ? `更新于 ${formatDate(new Date(payload.generated_at))}`
      : "";
    setStatus("请选择期刊");
  } catch (error) {
    setStatus("无法加载 feed.json，请先运行 get_RSS.py 并用本地服务器打开页面。");
  }
}

async function loadCategories() {
  try {
    const response = await fetch("categories.json?t=" + Date.now(), {
      cache: "no-store"
    });
    if (!response.ok) return;
    const payload = await response.json();
    statsState.categories.topics = payload.topics || [];
    statsState.categories.methods = payload.methods || [];
  } catch (error) {
    console.warn("Failed to load categories", error);
  }
}

function getMonthlyKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  return `${year}-${month}`;
}

function renderMonthlyChart(items) {
  statsElements.monthChart.innerHTML = "";
  if (!items.length) {
    statsElements.monthChart.textContent = "暂无有效日期数据。";
    return;
  }

  const counts = new Map();
  for (const item of items) {
    const key = getMonthlyKey(item.date);
    counts.set(key, (counts.get(key) || 0) + 1);
  }

  const keys = Array.from(counts.keys()).sort();
  const sliced = keys.slice(-12);
  const maxCount = Math.max(...sliced.map((key) => counts.get(key)));

  const fragment = document.createDocumentFragment();
  for (const key of sliced) {
    const count = counts.get(key);
    const item = document.createElement("div");
    item.className = "bar-item";

    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${Math.max(10, Math.round((count / maxCount) * 140))}px`;

    const value = document.createElement("div");
    value.className = "bar-value";
    value.textContent = count;

    const label = document.createElement("div");
    label.className = "bar-label";
    label.textContent = key;

    item.appendChild(bar);
    item.appendChild(value);
    item.appendChild(label);
    fragment.appendChild(item);
  }
  statsElements.monthChart.appendChild(fragment);
}

function renderTopicDistribution(items) {
  const container = statsElements.topicDistribution;
  if (!container) return;
  container.innerHTML = "";
  if (!items.length) {
    container.textContent = "暂无主题数据。";
    return;
  }

  const counts = new Map();
  items.forEach((item) => {
    extractTopicLabels(item).forEach((label) => {
      counts.set(label, (counts.get(label) || 0) + 1);
    });
  });

  const sorted = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 12);
  if (!sorted.length) {
    container.textContent = "暂无主题数据。";
    return;
  }

  const max = Math.max(...sorted.map((entry) => entry[1]));
  const fragment = document.createDocumentFragment();
  sorted.forEach(([label, count], index) => {
    const row = document.createElement("div");
    row.className = "topic-bar";

    const name = document.createElement("div");
    name.textContent = label;

    const track = document.createElement("div");
    track.className = "topic-bar__track";
    const fill = document.createElement("div");
    fill.className = "topic-bar__fill";
    fill.style.width = `${Math.round((count / max) * 100)}%`;
    fill.style.background = topicPalette[index % topicPalette.length];
    track.appendChild(fill);

    const value = document.createElement("div");
    value.textContent = count;

    row.appendChild(name);
    row.appendChild(track);
    row.appendChild(value);
    fragment.appendChild(row);
  });
  container.appendChild(fragment);
}

function renderDistributionBars(container, entries, maxItems, labelFallback) {
  if (!container) return;
  container.innerHTML = "";
  if (!entries.length) {
    container.textContent = labelFallback || "暂无数据";
    return;
  }

  const top = entries.slice(0, maxItems);
  const otherPct =
    entries.length > maxItems ? entries.slice(maxItems).reduce((sum, entry) => sum + entry.pct, 0) : 0;
  const segments = [...top];
  if (otherPct > 0.001) {
    segments.push({ label: "其他", pct: otherPct, count: 0 });
  }

  segments.forEach((entry, index) => {
    const seg = document.createElement("div");
    seg.className = "fit-bar-seg";
    seg.style.width = `${Math.max(2, Math.round(entry.pct * 100))}%`;
    seg.style.background = topicPalette[index % topicPalette.length];
    seg.title = `${entry.label} ${Math.round(entry.pct * 100)}%`;
    container.appendChild(seg);
  });
}

function renderBaselineList(container, baselineEntries, limit) {
  if (!container) return;
  container.innerHTML = "";
  if (!baselineEntries.length) {
    container.textContent = "暂无对比数据。";
    return;
  }

  const fragment = document.createDocumentFragment();
  baselineEntries.slice(0, limit).forEach((entry) => {
    const row = document.createElement("div");
    row.className = "fit-top-row";

    const name = document.createElement("div");
    name.className = "fit-top-name";
    name.textContent = entry.label;

    const values = document.createElement("div");
    values.className = "fit-top-values";
    values.textContent = `${Math.round(entry.pct * 100)}%`;

    row.appendChild(name);
    row.appendChild(values);
    fragment.appendChild(row);
  });
  container.appendChild(fragment);
}

function renderFitBaseline(baselineItems, baselineLabel) {
  const topicBaseline = normalizeDistribution(buildLabelCounts(baselineItems, extractTopicLabels));
  const methodBaseline = normalizeDistribution(buildLabelCounts(baselineItems, extractMethodLabels));

  renderDistributionBars(statsElements.fitTopicBaseline, topicBaseline, 6, "暂无对比数据");
  renderDistributionBars(statsElements.fitMethodBaseline, methodBaseline, 4, "暂无对比数据");
  if (statsElements.fitTopicBaselineLabel) statsElements.fitTopicBaselineLabel.textContent = baselineLabel;
  if (statsElements.fitMethodBaselineLabel) statsElements.fitMethodBaselineLabel.textContent = baselineLabel;

  renderBaselineList(statsElements.fitTopicTopList, topicBaseline, 5);
  renderBaselineList(statsElements.fitMethodTopList, methodBaseline, 3);

  return {
    topicVector: buildVector(topicBaseline),
    methodVector: buildVector(methodBaseline)
  };
}

function formatTopLabels(entries, limit) {
  if (!entries.length) return "";
  return entries.slice(0, limit).map((entry) => entry.label).join(" / ");
}

function describeConfidence(confidence) {
  if (confidence >= 0.7) return { label: "高置信", tone: "high" };
  if (confidence >= 0.4) return { label: "中等置信", tone: "mid" };
  return { label: "低置信", tone: "low" };
}

function renderFitJournalList(metrics, baselineVectors, baselineLabel) {
  if (!statsElements.fitJournalList) return;
  statsElements.fitJournalList.innerHTML = "";

  if (statsElements.fitOverviewCount) {
    statsElements.fitOverviewCount.textContent = metrics.length ? `${metrics.length} 本期刊` : "无期刊数据";
  }

  if (!metrics.length) {
    statsElements.fitJournalList.textContent = "当前范围内没有可展示的数据。";
    return;
  }

  const baselineReady = Boolean(
    (baselineVectors.topicVector && baselineVectors.topicVector.labels.length) ||
      (baselineVectors.methodVector && baselineVectors.methodVector.labels.length)
  );

  const rows = metrics.map((metric) => {
    const topicSim = baselineReady ? cosineSimilarity(metric.topicVector, baselineVectors.topicVector) : 0;
    const methodSim = baselineReady ? cosineSimilarity(metric.methodVector, baselineVectors.methodVector) : 0;
    const structureScore = baselineReady ? 0.7 * topicSim + 0.3 * methodSim : 0;
    return {
      ...metric,
      structureScore,
      baselineLabel
    };
  });

  const sorted = sortFitMetrics(rows, getFitSort());
  const fragment = document.createDocumentFragment();

  sorted.forEach((row) => {
    const card = document.createElement("div");
    card.className = "fit-journal-card";

    const header = document.createElement("div");
    header.className = "fit-journal-header";

    const info = document.createElement("div");
    const title = document.createElement("div");
    title.className = "fit-journal-title";
    title.textContent = row.journal;

    const meta = document.createElement("div");
    meta.className = "fit-journal-meta";
    meta.textContent = `样本 ${row.stats.total} · 喜欢 ${row.stats.liked} · 不喜欢 ${row.stats.disliked} · 未反馈 ${row.stats.neutral}`;

    info.appendChild(title);
    info.appendChild(meta);

    const score = document.createElement("div");
    score.className = "fit-journal-score";
    let scoreTone = "neutral";
    if (row.fitScore >= 20) scoreTone = "high";
    if (row.fitScore <= -20) scoreTone = "low";
    score.dataset.tone = scoreTone;
    const scoreValue = row.fitScore ? Math.round(row.fitScore) : 0;
    score.textContent = `${scoreValue >= 0 ? "+" : ""}${scoreValue}`;

    header.appendChild(info);
    header.appendChild(score);

    const badges = document.createElement("div");
    badges.className = "fit-journal-badges";
    const confidenceInfo = describeConfidence(row.confidence);
    const confidenceBadge = document.createElement("span");
    confidenceBadge.className = `fit-chip fit-chip--${confidenceInfo.tone}`;
    confidenceBadge.textContent = confidenceInfo.label;

    const structureBadge = document.createElement("span");
    structureBadge.className = "fit-chip";
    structureBadge.textContent = baselineReady
      ? `结构匹配 ${Math.round(row.structureScore * 100)}`
      : "结构匹配 -";

    badges.appendChild(confidenceBadge);
    badges.appendChild(structureBadge);

    const ratio = document.createElement("div");
    ratio.className = "fit-journal-ratio";
    const ratioBar = document.createElement("div");
    ratioBar.className = "ratio-bar ratio-bar--compact";

    const likeFill = document.createElement("div");
    likeFill.className = "ratio-fill ratio-fill--like";
    likeFill.style.width = `${Math.round(row.stats.likePct * 100)}%`;
    const dislikeFill = document.createElement("div");
    dislikeFill.className = "ratio-fill ratio-fill--dislike";
    dislikeFill.style.width = `${Math.round(row.stats.dislikePct * 100)}%`;
    const neutralFill = document.createElement("div");
    neutralFill.className = "ratio-fill ratio-fill--neutral";
    neutralFill.style.width = `${Math.round(row.stats.neutralPct * 100)}%`;

    ratioBar.appendChild(likeFill);
    ratioBar.appendChild(dislikeFill);
    ratioBar.appendChild(neutralFill);
    ratio.appendChild(ratioBar);

    const bars = document.createElement("div");
    bars.className = "fit-journal-bars";

    const topicRow = document.createElement("div");
    topicRow.className = "fit-journal-bar";
    const topicLabel = document.createElement("div");
    topicLabel.className = "fit-bar-label";
    topicLabel.textContent = "Topic";
    const topicTrack = document.createElement("div");
    topicTrack.className = "fit-bar-track";
    renderDistributionBars(topicTrack, row.topicEntries, 5, "暂无 Topic");
    topicRow.appendChild(topicLabel);
    topicRow.appendChild(topicTrack);

    const methodRow = document.createElement("div");
    methodRow.className = "fit-journal-bar";
    const methodLabel = document.createElement("div");
    methodLabel.className = "fit-bar-label";
    methodLabel.textContent = "Method";
    const methodTrack = document.createElement("div");
    methodTrack.className = "fit-bar-track";
    renderDistributionBars(methodTrack, row.methodEntries, 3, "暂无 Method");
    methodRow.appendChild(methodLabel);
    methodRow.appendChild(methodTrack);

    bars.appendChild(topicRow);
    bars.appendChild(methodRow);

    const tags = document.createElement("div");
    tags.className = "fit-journal-tags";
    const topicText = formatTopLabels(row.topicEntries, 3);
    const methodText = formatTopLabels(row.methodEntries, 2);
    tags.textContent = `Topic：${topicText || "暂无"} · Method：${methodText || "暂无"}`;

    const actions = document.createElement("div");
    actions.className = "fit-journal-actions";
    const link = document.createElement("a");
    link.className = "fit-match-filter";
    link.href = `index.html?journal=${encodeURIComponent(row.journal)}`;
    link.textContent = "筛选";
    actions.appendChild(link);

    card.appendChild(header);
    card.appendChild(badges);
    card.appendChild(ratio);
    card.appendChild(bars);
    card.appendChild(tags);
    card.appendChild(actions);

    fragment.appendChild(card);
  });

  statsElements.fitJournalList.appendChild(fragment);
}

function buildJournalGroups(fromDate, toDate) {
  const grouped = new Map();
  statsState.items.forEach((item) => {
    if (!isWithinRange(item, fromDate, toDate)) return;
    if (!grouped.has(item.journal)) grouped.set(item.journal, []);
    grouped.get(item.journal).push(item);
  });
  return grouped;
}

function buildJournalMetrics(grouped, favorites, hidden) {
  const metrics = [];
  grouped.forEach((items, journal) => {
    const stats = computeStats(items, favorites, hidden);
    const confidence = computeConfidence(stats.total);
    const fitScore = computeFitScore(stats);
    const topicEntries = normalizeDistribution(buildLabelCounts(items, extractTopicLabels));
    const methodEntries = normalizeDistribution(buildLabelCounts(items, extractMethodLabels));
    metrics.push({
      journal,
      stats,
      confidence,
      fitScore,
      topicEntries,
      methodEntries,
      topicVector: buildVector(topicEntries),
      methodVector: buildVector(methodEntries)
    });
  });
  return metrics;
}

function renderFitProfile(fromDate, toDate) {
  if (!statsElements.fitJournalList) return;
  const favorites = getPositiveSet();
  const hidden = new Set(statsState.interactions.hidden || []);
  const grouped = buildJournalGroups(fromDate, toDate);
  const metrics = buildJournalMetrics(grouped, favorites, hidden);

  let baselineItems = [];
  let baselineLabel = "对比基准";
  const mode = statsElements.fitCompareMode ? statsElements.fitCompareMode.value : "favorites";
  if (mode === "journal") {
    const compareJournal = statsElements.fitCompareJournal ? statsElements.fitCompareJournal.value : "";
    baselineItems = statsState.items.filter(
      (item) => item.journal === compareJournal && isWithinRange(item, fromDate, toDate)
    );
    baselineLabel = compareJournal ? `对比：${compareJournal}` : "对比期刊";
  } else {
    baselineItems = statsState.items.filter(
      (item) => favorites.has(item.link) && isWithinRange(item, fromDate, toDate)
    );
    baselineLabel = "我的收藏";
  }

  const baselineVectors = renderFitBaseline(baselineItems, baselineLabel);
  renderFitJournalList(metrics, baselineVectors, baselineLabel);
}

function buildQueryVectors(text) {
  const normalized = (text || "").toLowerCase();
  const topicEntries = [];
  const methodEntries = [];
  const topicMatches = [];
  const methodMatches = [];

  statsState.categories.topics.forEach((topic) => {
    if (!topic || !topic.name) return;
    let score = 0;
    const hits = [];
    (topic.keywords || []).forEach((keyword) => {
      const count = countKeywordMatches(normalized, keyword);
      if (count) {
        score += count;
        hits.push(keyword);
      }
    });
    if (score > 0) {
      topicEntries.push({ label: topic.name, count: score, pct: 0 });
      topicMatches.push({ label: topic.name, hits });
    }
  });

  statsState.categories.methods.forEach((method) => {
    if (!method || !method.name) return;
    let score = 0;
    const hits = [];
    (method.keywords || []).forEach((keyword) => {
      const count = countKeywordMatches(normalized, keyword);
      if (count) {
        score += count;
        hits.push(keyword);
      }
    });
    if (score > 0) {
      methodEntries.push({ label: method.name, count: score, pct: 0 });
      methodMatches.push({ label: method.name, hits });
    }
  });

  const topicTotal = topicEntries.reduce((sum, entry) => sum + entry.count, 0);
  const methodTotal = methodEntries.reduce((sum, entry) => sum + entry.count, 0);
  topicEntries.forEach((entry) => (entry.pct = topicTotal ? entry.count / topicTotal : 0));
  methodEntries.forEach((entry) => (entry.pct = methodTotal ? entry.count / methodTotal : 0));

  return {
    topicVector: buildVector(topicEntries),
    methodVector: buildVector(methodEntries),
    topicMatches,
    methodMatches
  };
}

function renderMatchResults(results, queryMatches) {
  if (!statsElements.fitMatchResults) return;
  statsElements.fitMatchResults.innerHTML = "";
  if (!results.length) {
    statsElements.fitMatchResults.textContent = "未找到匹配期刊，请补充更多关键词或研究描述。";
    return;
  }
  const fragment = document.createDocumentFragment();
  results.forEach((result) => {
    const card = document.createElement("div");
    card.className = "fit-match-card";

    const header = document.createElement("div");
    header.className = "fit-match-header";

    const title = document.createElement("div");
    title.className = "fit-match-title";
    title.textContent = result.journal;

    const score = document.createElement("div");
    score.className = "fit-match-score";
    score.textContent = `${(result.finalScore * 100).toFixed(0)}`;

    header.appendChild(title);
    header.appendChild(score);

    const detail = document.createElement("div");
    detail.className = "fit-match-detail";
    detail.textContent = `结构匹配 ${(result.structureScore * 100).toFixed(0)} · 置信 ${result.confidence.toFixed(2)} · 匹配度 ${result.fitScore.toFixed(0)}`;

    const explain = document.createElement("div");
    explain.className = "fit-match-explain";
    const topicText = queryMatches.topicMatches.slice(0, 3).map((item) => item.label).join(" / ");
    const methodText = queryMatches.methodMatches.slice(0, 2).map((item) => item.label).join(" / ");
    explain.textContent = `命中主题：${topicText || "未识别"} · 方法：${methodText || "未识别"}`;

    const link = document.createElement("a");
    link.className = "fit-match-filter";
    link.href = `index.html?journal=${encodeURIComponent(result.journal)}`;
    link.textContent = "筛选";

    card.appendChild(header);
    card.appendChild(detail);
    card.appendChild(explain);
    card.appendChild(link);
    fragment.appendChild(card);
  });
  statsElements.fitMatchResults.appendChild(fragment);
}

function runMatch(fromDate, toDate) {
  if (!statsElements.fitAbstractInput || !statsElements.fitMatchStatus) return;
  const raw = statsElements.fitAbstractInput.value.trim();
  if (!raw) {
    statsElements.fitMatchStatus.textContent = "请输入摘要后匹配。";
    renderMatchResults([], { topicMatches: [], methodMatches: [] });
    return;
  }
  if (!statsState.categories.topics.length && !statsState.categories.methods.length) {
    statsElements.fitMatchStatus.textContent = "分类关键词未加载。";
    return;
  }

  const query = buildQueryVectors(raw);
  if (!query.topicVector.labels.length && !query.methodVector.labels.length) {
    statsElements.fitMatchStatus.textContent = "未识别到 Topic/Method 关键词。";
    renderMatchResults([], query);
    return;
  }

  const favorites = getPositiveSet();
  const hidden = new Set(statsState.interactions.hidden || []);
  const grouped = buildJournalGroups(fromDate, toDate);
  const metrics = buildJournalMetrics(grouped, favorites, hidden);

  const results = metrics
    .map((metric) => {
      const topicSim = cosineSimilarity(query.topicVector, metric.topicVector);
      const methodSim = cosineSimilarity(query.methodVector, metric.methodVector);
      const structureScore = 0.7 * topicSim + 0.3 * methodSim;
      return {
        journal: metric.journal,
        structureScore,
        confidence: metric.confidence,
        fitScore: metric.fitScore,
        finalScore: structureScore * metric.confidence
      };
    })
    .filter((entry) => entry.structureScore > 0)
    .sort((a, b) => b.finalScore - a.finalScore)
    .slice(0, 10);

  statsElements.fitMatchStatus.textContent = results.length ? "匹配完成" : "无匹配结果";
  renderMatchResults(results, query);
}

function renderTopicTrend(items) {
  const container = statsElements.topicTrend;
  if (!container) return;
  container.innerHTML = "";
  if (!items.length) {
    container.textContent = "暂无趋势数据。";
    return;
  }

  const topicCounts = new Map();
  items.forEach((item) => {
    extractTopicLabels(item).forEach((label) => {
      topicCounts.set(label, (topicCounts.get(label) || 0) + 1);
    });
  });
  const topTopics = Array.from(topicCounts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([t]) => t);
  if (!topTopics.length) {
    container.textContent = "暂无趋势数据。";
    return;
  }

  const months = new Map();
  items.forEach((item) => {
    if (!item.date) return;
    const key = getMonthlyKey(item.date);
    if (!months.has(key)) months.set(key, {});
    const bucket = months.get(key);
    extractTopicLabels(item).forEach((label) => {
      if (!topTopics.includes(label)) return;
      bucket[label] = (bucket[label] || 0) + 1;
    });
  });

  const sortedMonths = Array.from(months.keys()).sort().slice(-6);
  if (!sortedMonths.length) {
    container.textContent = "暂无趋势数据。";
    return;
  }

  const fragment = document.createDocumentFragment();
  sortedMonths.forEach((monthKey) => {
    const barItem = document.createElement("div");
    barItem.className = "trend-bar-item";

    const value = document.createElement("div");
    value.className = "trend-bar-value";
    const total = topTopics.reduce((sum, topic) => sum + (months.get(monthKey)[topic] || 0), 0);
    value.textContent = total || 0;

    const barContainer = document.createElement("div");
    barContainer.className = "trend-bar-stacked";
    barContainer.style.height = "120px";

    topTopics.forEach((topic, index) => {
      const count = months.get(monthKey)[topic] || 0;
      const segment = document.createElement("div");
      segment.className = "trend-bar-segment";
      segment.style.background = topicPalette[index % topicPalette.length];
      segment.style.height = total ? `${(count / total) * 100}%` : "0%";
      segment.title = `${topic}: ${count}`;
      barContainer.appendChild(segment);
    });

    const label = document.createElement("div");
    label.className = "trend-bar-label";
    label.textContent = monthKey.split("-")[1] + "月";

    barItem.appendChild(value);
    barItem.appendChild(barContainer);
    barItem.appendChild(label);
    fragment.appendChild(barItem);
  });

  container.appendChild(fragment);
}

function renderTopicNetwork(items) {
  const container = statsElements.topicNetwork;
  if (!container) return;
  container.innerHTML = "";
  if (!items.length) {
    container.textContent = "暂无主题关联数据。";
    return;
  }

  const pairs = new Map();
  items.forEach((item) => {
    const labels = extractTopicLabels(item);
    if (labels.length < 2) return;
    for (let i = 0; i < labels.length; i += 1) {
      for (let j = i + 1; j < labels.length; j += 1) {
        const key = [labels[i], labels[j]].sort().join(" × ");
        pairs.set(key, (pairs.get(key) || 0) + 1);
      }
    }
  });

  const sorted = Array.from(pairs.entries()).sort((a, b) => b[1] - a[1]).slice(0, 12);
  if (!sorted.length) {
    container.textContent = "暂无主题关联数据。";
    return;
  }

  const fragment = document.createDocumentFragment();
  sorted.forEach(([label, count]) => {
    const pill = document.createElement("div");
    pill.className = "topic-link";
    pill.textContent = `${label} · ${count}`;
    fragment.appendChild(pill);
  });
  container.appendChild(fragment);
}

function renderTopicRadar(items) {
  const container = statsElements.topicRadar;
  if (!container) return;
  container.innerHTML = "";
  if (!items.length) {
    container.textContent = "暂无偏好数据。";
    return;
  }

  const favorites = getPositiveSet();
  const counts = new Map();
  items.forEach((item) => {
    if (!favorites.has(item.link)) return;
    extractTopicLabels(item).forEach((label) => {
      counts.set(label, (counts.get(label) || 0) + 1);
    });
  });

  const top = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 5);
  if (!top.length) {
    container.textContent = "暂无偏好数据。";
    return;
  }

  const max = Math.max(...top.map((entry) => entry[1]));
  const size = 280;
  const center = size / 2;
  const radius = 100;
  const angleStep = (Math.PI * 2) / top.length;

  const points = top.map((entry, idx) => {
    const value = entry[1] / max;
    const angle = -Math.PI / 2 + idx * angleStep;
    const r = radius * value;
    const x = center + r * Math.cos(angle);
    const y = center + r * Math.sin(angle);
    return { x, y, label: entry[0] };
  });

  const polygon = points.map((p) => `${p.x},${p.y}`).join(" ");

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);

  points.forEach((p, idx) => {
    const angle = -Math.PI / 2 + idx * angleStep;
    const x = center + radius * Math.cos(angle);
    const y = center + radius * Math.sin(angle);
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", center);
    line.setAttribute("y1", center);
    line.setAttribute("x2", x);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", "#e2e8f0");
    svg.appendChild(line);

    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", x);
    text.setAttribute("y", y);
    text.setAttribute("fill", "#475569");
    text.setAttribute("font-size", "10");
    text.setAttribute("text-anchor", x >= center ? "start" : "end");
    text.setAttribute("dominant-baseline", "middle");
    text.textContent = p.label;
    svg.appendChild(text);
  });

  const shape = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
  shape.setAttribute("points", polygon);
  shape.setAttribute("fill", "rgba(249, 115, 22, 0.3)");
  shape.setAttribute("stroke", "#f97316");
  shape.setAttribute("stroke-width", "2");
  svg.appendChild(shape);

  container.appendChild(svg);
}

function updateStats() {
  let journal = statsElements.journalSelect.value;
  const fromDate = statsElements.fromDate.value ? new Date(statsElements.fromDate.value) : null;
  const toDate = statsElements.toDate.value ? new Date(statsElements.toDate.value) : null;
  renderOverview(statsState.items, fromDate, toDate);

  if (!journal && statsElements.journalSelect.options.length > 1) {
    statsElements.journalSelect.selectedIndex = 1;
    journal = statsElements.journalSelect.value;
  }

  if (statsElements.fitCompareMode && statsElements.fitCompareJournalWrap) {
    statsElements.fitCompareJournalWrap.style.display =
      statsElements.fitCompareMode.value === "journal" ? "flex" : "none";
  }
  renderFitProfile(fromDate, toDate);

  if (!journal) {
    const inRange = statsState.items.filter((item) => isWithinRange(item, fromDate, toDate)).length;
    setStatus(inRange ? `当前范围共 ${inRange} 篇 · 请选择期刊` : "请选择期刊");
    statsElements.statTotal.textContent = "-";
    statsElements.statSpan.textContent = "-";
    statsElements.statPerWeek.textContent = "-";
    statsElements.statWeekNote.textContent = "-";
    statsElements.statPerMonth.textContent = "-";
    statsElements.statMonthNote.textContent = "-";
    statsElements.statLastDate.textContent = "-";
    statsElements.statLastNote.textContent = "-";
    statsElements.ratioLikeText.textContent = "-";
    statsElements.ratioDislikeText.textContent = "-";
    statsElements.ratioNeutralText.textContent = "-";
    statsElements.ratioLikeBar.style.width = "0%";
    statsElements.ratioDislikeBar.style.width = "0%";
    statsElements.ratioNeutralBar.style.width = "0%";
    statsElements.monthChart.textContent = "请选择期刊后查看统计。";
    if (statsElements.topicDistribution) statsElements.topicDistribution.textContent = "请选择期刊后查看统计。";
    if (statsElements.topicTrend) statsElements.topicTrend.textContent = "请选择期刊后查看统计。";
    if (statsElements.topicNetwork) statsElements.topicNetwork.textContent = "请选择期刊后查看统计。";
    if (statsElements.topicRadar) statsElements.topicRadar.textContent = "请选择期刊后查看统计。";
    return;
  }

  const filtered = statsState.items.filter(
    (item) => item.journal === journal && isWithinRange(item, fromDate, toDate)
  );

  const favorites = getPositiveSet();
  const hidden = new Set(statsState.interactions.hidden || []);
  const stats = computeStats(filtered, favorites, hidden);

  statsElements.ratioLikeBar.style.width = `${Math.round(stats.likePct * 100)}%`;
  statsElements.ratioDislikeBar.style.width = `${Math.round(stats.dislikePct * 100)}%`;
  statsElements.ratioNeutralBar.style.width = `${Math.round(stats.neutralPct * 100)}%`;

  statsElements.ratioLikeText.textContent = stats.total
    ? `${stats.liked} 篇 · ${percentFormatter.format(stats.likePct)}`
    : "0 篇 · 0%";
  statsElements.ratioDislikeText.textContent = stats.total
    ? `${stats.disliked} 篇 · ${percentFormatter.format(stats.dislikePct)}`
    : "0 篇 · 0%";
  statsElements.ratioNeutralText.textContent = stats.total
    ? `${stats.neutral} 篇 · ${percentFormatter.format(stats.neutralPct)}`
    : "0 篇 · 0%";

  statsElements.statTotal.textContent = stats.total ? `${stats.total} 篇` : "0 篇";
  if (stats.total === 0) {
    statsElements.statSpan.textContent = "没有匹配的文章";
  }

  const dated = filtered.filter((item) => item.date && !Number.isNaN(item.date.getTime()));
  if (!dated.length) {
    statsElements.statSpan.textContent = "无有效日期";
    statsElements.statPerWeek.textContent = "-";
    statsElements.statPerMonth.textContent = "-";
    statsElements.statWeekNote.textContent = "-";
    statsElements.statMonthNote.textContent = "-";
    statsElements.statLastDate.textContent = "-";
    statsElements.statLastNote.textContent = "-";
    renderMonthlyChart([]);
  } else {
    const dates = dated.map((item) => item.date).sort((a, b) => a - b);
    const minDate = dates[0];
    const maxDate = dates[dates.length - 1];
    const spanDays = Math.max(1, (maxDate - minDate) / 86400000);
    const weeks = spanDays / 7;
    const months = spanDays / 30.44;

    const perWeek = dated.length / weeks;
    const perMonth = dated.length / months;

    statsElements.statSpan.textContent = `${formatDate(minDate)} - ${formatDate(maxDate)}`;
    statsElements.statPerWeek.textContent = perWeek ? perWeek.toFixed(1) : "-";
    statsElements.statPerMonth.textContent = perMonth ? perMonth.toFixed(1) : "-";
    statsElements.statWeekNote.textContent = `基于 ${dated.length} 篇`;
    statsElements.statMonthNote.textContent = `基于 ${dated.length} 篇`;
    statsElements.statLastDate.textContent = formatDate(maxDate);
    statsElements.statLastNote.textContent = `最近 ${dated.length} 篇`;

    renderMonthlyChart(dated);
  }

  renderTopicDistribution(filtered);
  renderTopicTrend(filtered);
  renderTopicNetwork(filtered);
  renderTopicRadar(filtered);

  setStatus(`共 ${stats.total} 篇`);
}

function attachHandlers() {
  const controls = [statsElements.journalSelect, statsElements.fromDate, statsElements.toDate];
  controls.forEach((control) => {
    if (!control) return;
    control.addEventListener("input", updateStats);
    control.addEventListener("change", updateStats);
  });

  if (statsElements.overviewBody) {
    statsElements.overviewBody.addEventListener("click", (event) => {
      const row = event.target.closest("tr");
      if (!row || !row.dataset.journal) return;
      statsElements.journalSelect.value = row.dataset.journal;
      updateStats();
    });
  }

  if (statsElements.overviewSort) {
    statsElements.overviewSort.addEventListener("change", updateStats);
  }

  if (statsElements.fitCompareMode) {
    statsElements.fitCompareMode.addEventListener("change", () => {
      const mode = statsElements.fitCompareMode.value;
      if (statsElements.fitCompareJournalWrap) {
        statsElements.fitCompareJournalWrap.style.display = mode === "journal" ? "flex" : "none";
      }
      updateStats();
    });
  }

  if (statsElements.fitCompareJournal) {
    statsElements.fitCompareJournal.addEventListener("change", updateStats);
  }

  if (statsElements.fitSort) {
    statsElements.fitSort.addEventListener("change", updateStats);
  }

  if (statsElements.fitMatchBtn) {
    statsElements.fitMatchBtn.addEventListener("click", () => {
      const fromDate = statsElements.fromDate.value ? new Date(statsElements.fromDate.value) : null;
      const toDate = statsElements.toDate.value ? new Date(statsElements.toDate.value) : null;
      runMatch(fromDate, toDate);
    });
  }

  if (statsElements.tabButtons && statsElements.tabPanels) {
    statsElements.tabButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.getAttribute("data-tab");
        statsElements.tabButtons.forEach((btn) => {
          const isActive = btn === button;
          btn.classList.toggle("is-active", isActive);
          btn.setAttribute("aria-selected", isActive ? "true" : "false");
        });
        statsElements.tabPanels.forEach((panel) => {
          const isActive = panel.getAttribute("data-tab-panel") === target;
          panel.classList.toggle("is-active", isActive);
        });
      });
    });
  }
}

async function init() {
  attachHandlers();
  await loadInteractions();
  await loadCategories();
  await loadFeed();
  updateStats();
}

document.addEventListener("DOMContentLoaded", init);
