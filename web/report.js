// 「我的偏好」标签页（insights.html#prefs）。作用域限定在 IIFE 内，由 insights.js 调用 InsightsReport.init()。
(function () {
"use strict";

const reportElements = {
  status: document.getElementById("reportStatus"),
  generatedAt: document.getElementById("reportGeneratedAt"),
  btnGenerate: document.getElementById("btnGenerateReport"),
  btnReload: document.getElementById("btnReloadReport"),
  favCount: document.getElementById("reportFavCount"),
  hiddenCount: document.getElementById("reportHiddenCount"),
  missingFav: document.getElementById("reportMissingFav"),
  missingHidden: document.getElementById("reportMissingHidden"),
  topFavSummary: document.getElementById("reportTopFavSummary"),
  topHiddenSummary: document.getElementById("reportTopHiddenSummary"),
  preferredSummary: document.getElementById("reportPreferredSummary"),
  avoidedSummary: document.getElementById("reportAvoidedSummary"),
  preferredBigramsSummary: document.getElementById("reportPreferredBigramsSummary"),
  avoidedBigramsSummary: document.getElementById("reportAvoidedBigramsSummary"),
  topicInsights: document.getElementById("reportTopicInsights"),
  methodInsights: document.getElementById("reportMethodInsights"),
  toneInsights: document.getElementById("reportToneInsights"),
  topFavTerms: document.getElementById("reportTopFavTerms"),
  topHiddenTerms: document.getElementById("reportTopHiddenTerms"),
  preferredTerms: document.getElementById("reportPreferredTerms"),
  avoidedTerms: document.getElementById("reportAvoidedTerms"),
  preferredBigrams: document.getElementById("reportPreferredBigrams"),
  avoidedBigrams: document.getElementById("reportAvoidedBigrams"),
  sourceJournalCoverage: document.getElementById("reportSourceJournalCoverage"),
  journalPreferredSummary: document.getElementById("reportJournalPreferredSummary"),
  journalAvoidedSummary: document.getElementById("reportJournalAvoidedSummary"),
  journalPreferredTerms: document.getElementById("reportJournalPreferredTerms"),
  journalAvoidedTerms: document.getElementById("reportJournalAvoidedTerms"),
  sourceTopFavSummary: document.getElementById("reportSourceTopFavSummary"),
  sourceTopHiddenSummary: document.getElementById("reportSourceTopHiddenSummary"),
  sourcePreferredSummary: document.getElementById("reportSourcePreferredSummary"),
  sourceAvoidedSummary: document.getElementById("reportSourceAvoidedSummary"),
  sourceTopFavTerms: document.getElementById("reportSourceTopFavTerms"),
  sourceTopHiddenTerms: document.getElementById("reportSourceTopHiddenTerms"),
  sourcePreferredTerms: document.getElementById("reportSourcePreferredTerms"),
  sourceAvoidedTerms: document.getElementById("reportSourceAvoidedTerms"),
  missingFavLinks: document.getElementById("reportMissingFavLinks"),
  missingHiddenLinks: document.getElementById("reportMissingHiddenLinks"),
  warnings: document.getElementById("reportWarnings"),
  stale: document.getElementById("reportStale"),
  insightsSummary: document.getElementById("insightsSummary"),
  methodFavBar: document.getElementById("methodFavBar"),
  methodHidBar: document.getElementById("methodHidBar"),
  methodStats: document.getElementById("methodStats"),
  methodTable: document.getElementById("methodTable"),
  topicFavBar: document.getElementById("topicFavBar"),
  topicHidBar: document.getElementById("topicHidBar"),
  topicStats: document.getElementById("topicStats"),
  topicTable: document.getElementById("topicTable"),
  trendChart: document.getElementById("trendChart")
};

const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});

function setStatus(text) {
  reportElements.status.textContent = text;
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return dateTimeFormatter.format(date);
}

function clearValue(el) {
  if (!el) return;
  el.textContent = "-";
}

function renderSummary(container, items, prefix) {
  if (!container) return;
  if (!items || items.length === 0) {
    container.textContent = "暂无数据";
    return;
  }
  const labels = items
    .map((item) => item.term || item.label || "")
    .filter(Boolean)
    .slice(0, 5);
  if (!labels.length) {
    container.textContent = "暂无数据";
    return;
  }
  container.textContent = `${prefix}${labels.join("、")} 等关键词。`;
}

const CATEGORY_MAP = {
  topic: [
    { label: "消费者行为", keys: ["consumer", "consumers", "customer", "choice", "behavior", "perception", "perceptions", "decision", "decisions"] },
    { label: "可持续与绿色", keys: ["sustainability", "sustainable", "green", "csr", "responsibility", "ethical", "ethics"] },
    { label: "食品与健康", keys: ["food", "health", "wellbeing", "nutrition", "sugar", "calorie"] },
    { label: "技术与AI", keys: ["ai", "genai", "algorithmic", "automation", "robot", "robots", "digital", "platform", "data"] },
    { label: "旅游与酒店", keys: ["tourism", "hotel", "hospitality", "travel"] },
    { label: "服务与体验", keys: ["service", "experience", "experiential", "quality", "value"] },
    { label: "市场与品牌", keys: ["brand", "branding", "market", "marketing", "advertising"] },
    { label: "组织与管理", keys: ["management", "firm", "strategy", "organizational", "governance"] }
  ],
  method: [
    { label: "实验与干预", keys: ["experiment", "experiments", "field", "randomized", "trial"] },
    { label: "调查与问卷", keys: ["survey", "questionnaire", "sampling", "respondents"] },
    { label: "模型与分析", keys: ["model", "models", "analysis", "analytical", "estimation"] },
    { label: "比较与综述", keys: ["review", "meta", "systematic", "comparing", "comparison"] },
    { label: "质性与案例", keys: ["case", "qualitative", "interview", "ethnography"] }
  ],
  tone: [
    { label: "因果与效应", keys: ["effect", "impact", "influence", "causal", "drives"] },
    { label: "权衡与悖论", keys: ["tradeoff", "paradox", "paradoxes", "tension"] },
    { label: "道德与规范", keys: ["moral", "ethical", "ethics", "responsibility"] },
    { label: "风险与负面", keys: ["risk", "harm", "backfire", "misbehavior", "waste"] },
    { label: "创新与未来", keys: ["innovation", "future", "emerging", "novel"] }
  ]
};

function scoreCategories(items, categoryDefs) {
  const scores = {};
  const examples = {};
  if (!items || items.length === 0) return { scores, examples };
  items.forEach((item) => {
    const term = (item.term || item.label || "").toLowerCase();
    if (!term) return;
    categoryDefs.forEach((cat) => {
      if (cat.keys.some((key) => term.includes(key))) {
        const weight = item.count ?? item.fav ?? 1;
        scores[cat.label] = (scores[cat.label] || 0) + weight;
        if (!examples[cat.label]) examples[cat.label] = [];
        if (examples[cat.label].length < 3 && !examples[cat.label].includes(item.term)) {
          examples[cat.label].push(item.term);
        }
      }
    });
  });
  return { scores, examples };
}

function topCategories(scores, limit) {
  return Object.entries(scores)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit);
}

function renderInsights(listEl, positive, negative, label) {
  if (!listEl) return;
  listEl.innerHTML = "";
  const fragment = document.createDocumentFragment();

  const buildLine = (title, cats, examples, prefix) => {
    const li = document.createElement("li");
    li.className = "report-insight";
    if (!cats.length) {
      li.textContent = `${prefix}暂无明显信号。`;
      return li;
    }
    const labels = cats.map((item) => item[0]).join("、");
    const sample = cats
      .map((item) => examples[item[0]] || [])
      .flat()
      .filter(Boolean)
      .slice(0, 6);
    const sampleText = sample.length ? `（示例：${sample.join("、")}）` : "";
    li.textContent = `${prefix}${labels}${sampleText}`;
    return li;
  };

  fragment.appendChild(
    buildLine(`偏好${label}`, positive.cats, positive.examples, `偏好${label}：`)
  );
  fragment.appendChild(
    buildLine(`不偏好${label}`, negative.cats, negative.examples, `不偏好${label}：`)
  );

  listEl.appendChild(fragment);
}

function renderTable(container, items, columns) {
  if (!container) return;
  container.innerHTML = "";
  if (!items || items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "report-row report-row--empty";
    empty.textContent = "暂无数据";
    container.appendChild(empty);
    return;
  }

  const colClass = columns.length === 2 ? "report-row--two-cols" : "report-row--three-cols";
  const header = document.createElement("div");
  header.className = `report-row report-row--head ${colClass}`;
  columns.forEach((col) => {
    const cell = document.createElement("div");
    cell.className = "report-cell";
    cell.textContent = col.label;
    if (col.help) {
      const help = document.createElement("span");
      help.className = "metric-help";
      help.textContent = "ⓘ";
      help.title = col.help;
      help.setAttribute("aria-label", col.help);
      help.tabIndex = 0;
      cell.appendChild(help);
    }
    header.appendChild(cell);
  });
  container.appendChild(header);

  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = `report-row ${colClass}`;
    columns.forEach((col) => {
      const cell = document.createElement("div");
      cell.className = "report-cell";
      if (col.render) {
        const node = col.render(item);
        if (node && node.nodeType) {
          cell.appendChild(node);
        } else {
          cell.textContent = node ?? "";
        }
      } else {
        const value = col.value ? col.value(item) : "";
        cell.textContent = value ?? "";
      }
      row.appendChild(cell);
    });
    container.appendChild(row);
  });
}

function renderLinkList(listEl, links) {
  if (!listEl) return;
  listEl.innerHTML = "";
  if (!links || links.length === 0) {
    const li = document.createElement("li");
    li.className = "report-empty";
    li.textContent = "暂无";
    listEl.appendChild(li);
    return;
  }
  const fragment = document.createDocumentFragment();
  links.forEach((link) => {
    const li = document.createElement("li");
    li.textContent = link;
    fragment.appendChild(li);
  });
  listEl.appendChild(fragment);
}

// view: "favorites" 用于偏好（收藏）相关条目，其余为 "all"。
function buildFilterLink(type, value, view = "all") {
  const params = new URLSearchParams();
  params.set(type, value);
  params.set("view", view);
  params.set("from", "insights");
  return `index.html?${params.toString()}`;
}

// 期刊名链接到「期刊表现」中该期刊的详情（同页切换标签）。
function createJournalStatsLink(journal) {
  if (!journal) return "-";
  const link = document.createElement("a");
  link.className = "term-link";
  link.href = `#journals/detail?journal=${encodeURIComponent(journal)}`;
  link.textContent = journal;
  link.title = "在「期刊表现」中查看该期刊的发刊频率与收藏/不感兴趣占比";
  return link;
}

function createFilterLink(type, value, view = "all") {
  if (!value) {
    const empty = document.createElement("span");
    empty.textContent = "-";
    return empty;
  }
  const link = document.createElement("a");
  link.className = "report-filter-link";
  link.href = buildFilterLink(type, value, view);
  link.textContent = "筛选";
  link.title = view === "favorites" ? "在「我的收藏」中筛选" : "在全部文章中筛选";
  return link;
}

const LIFT_HELP = "1.8 = 在收藏中出现的概率是不感兴趣中的 1.8 倍；>1 偏好，<1 回避";
const CONFIDENCE_HELP = "置信度：结合 Wilson 95% 置信区间宽度与出现次数，越高越可靠；低于 40% 仅供参考";

function liftColumn() {
  return { label: "偏好倍数", help: LIFT_HELP, value: (item) => formatLift(item.lift) };
}

function confidenceColumn() {
  return { label: "置信度", help: CONFIDENCE_HELP, render: (item) => createConfidenceBar(item.confidence) };
}

function countsColumn() {
  return { label: "收藏/不感兴趣", value: (item) => `${item.fav ?? 0}/${item.hidden ?? 0}` };
}

// 后端文案中的术语统一替换为页面用语。
function humanizeMetricText(text) {
  return String(text ?? "")
    .replace(/lift\s*值/gi, "偏好倍数")
    .replace(/lift\s*=\s*/gi, "偏好倍数 ")
    .replace(/\blift\b/gi, "偏好倍数")
    .replace(/不喜欢/g, "不感兴趣")
    .replace(/隐藏/g, "不感兴趣");
}

function formatLift(value) {
  if (value == null || value === "") return "-";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return `${num.toFixed(2)}×`;
}

function renderReport(report) {
  if (!report) {
    setStatus("尚未生成，点击「重新计算」。");
    setStaleNotice("");
    reportElements.generatedAt.textContent = "";
    clearValue(reportElements.favCount);
    clearValue(reportElements.hiddenCount);
    clearValue(reportElements.missingFav);
    clearValue(reportElements.missingHidden);
    renderSummary(reportElements.topFavSummary, [], "");
    renderSummary(reportElements.topHiddenSummary, [], "");
    renderSummary(reportElements.preferredSummary, [], "");
    renderSummary(reportElements.avoidedSummary, [], "");
    renderSummary(reportElements.preferredBigramsSummary, [], "");
    renderSummary(reportElements.avoidedBigramsSummary, [], "");
    renderInsights(reportElements.topicInsights, { cats: [], examples: {} }, { cats: [], examples: {} }, "主题");
    renderInsights(reportElements.methodInsights, { cats: [], examples: {} }, { cats: [], examples: {} }, "方法");
    renderInsights(reportElements.toneInsights, { cats: [], examples: {} }, { cats: [], examples: {} }, "语气");
    renderTable(reportElements.topFavTerms, [], []);
    renderTable(reportElements.topHiddenTerms, [], []);
    renderTable(reportElements.preferredTerms, [], []);
    renderTable(reportElements.avoidedTerms, [], []);
    renderTable(reportElements.preferredBigrams, [], []);
    renderTable(reportElements.avoidedBigrams, [], []);
    if (reportElements.sourceJournalCoverage) {
      reportElements.sourceJournalCoverage.textContent = "";
    }
    renderSummary(reportElements.journalPreferredSummary, [], "");
    renderSummary(reportElements.journalAvoidedSummary, [], "");
    renderSummary(reportElements.sourceTopFavSummary, [], "");
    renderSummary(reportElements.sourceTopHiddenSummary, [], "");
    renderSummary(reportElements.sourcePreferredSummary, [], "");
    renderSummary(reportElements.sourceAvoidedSummary, [], "");
    renderTable(reportElements.journalPreferredTerms, [], []);
    renderTable(reportElements.journalAvoidedTerms, [], []);
    renderTable(reportElements.sourceTopFavTerms, [], []);
    renderTable(reportElements.sourceTopHiddenTerms, [], []);
    renderTable(reportElements.sourcePreferredTerms, [], []);
    renderTable(reportElements.sourceAvoidedTerms, [], []);
    renderLinkList(reportElements.missingFavLinks, []);
    renderLinkList(reportElements.missingHiddenLinks, []);
    return;
  }

  const counts = report.counts || {};
  reportElements.favCount.textContent = counts.favorites ?? 0;
  reportElements.hiddenCount.textContent = counts.hidden ?? 0;
  const missingFavCount = (counts.missing_favorites ?? 0) + (counts.missing_archived ?? 0);
  reportElements.missingFav.textContent = missingFavCount;
  reportElements.missingHidden.textContent = counts.missing_hidden ?? 0;

  reportElements.generatedAt.textContent = report.generated_at
    ? `更新于 ${formatDateTime(report.generated_at)}`
    : "";

  // 渲染新功能
  const dataQuality = report.data_quality || {};
  renderWarnings(dataQuality);

  const insights = report.insights_summary || [];
  renderInsightsSummary(insights);

  renderMethodTopicViz(report);

  const trends = report.temporal_trends || [];
  renderTrendChart(trends);

  const terms = report.title_terms || {};
  const bigrams = report.title_bigrams || {};

  renderSummary(reportElements.topFavSummary, terms.top_favorites, "收藏高频词集中在 ");
  renderSummary(reportElements.topHiddenSummary, terms.top_hidden, "不感兴趣高频词集中在 ");
  renderSummary(reportElements.preferredSummary, terms.preferred, "更可能收藏的词集中在 ");
  renderSummary(reportElements.avoidedSummary, terms.avoided, "更可能避开的词集中在 ");
  renderSummary(reportElements.preferredBigramsSummary, bigrams.preferred, "更可能收藏的双词组合集中在 ");
  renderSummary(reportElements.avoidedBigramsSummary, bigrams.avoided, "更可能避开的双词组合集中在 ");

  const topicPos = scoreCategories(terms.preferred || terms.top_favorites || [], CATEGORY_MAP.topic);
  const topicNeg = scoreCategories(terms.avoided || terms.top_hidden || [], CATEGORY_MAP.topic);
  const methodPos = scoreCategories(terms.preferred || terms.top_favorites || [], CATEGORY_MAP.method);
  const methodNeg = scoreCategories(terms.avoided || terms.top_hidden || [], CATEGORY_MAP.method);
  const tonePos = scoreCategories(terms.preferred || terms.top_favorites || [], CATEGORY_MAP.tone);
  const toneNeg = scoreCategories(terms.avoided || terms.top_hidden || [], CATEGORY_MAP.tone);

  renderInsights(
    reportElements.topicInsights,
    { cats: topCategories(topicPos.scores, 3), examples: topicPos.examples },
    { cats: topCategories(topicNeg.scores, 3), examples: topicNeg.examples },
    "主题"
  );
  renderInsights(
    reportElements.methodInsights,
    { cats: topCategories(methodPos.scores, 3), examples: methodPos.examples },
    { cats: topCategories(methodNeg.scores, 3), examples: methodNeg.examples },
    "方法"
  );
  renderInsights(
    reportElements.toneInsights,
    { cats: topCategories(tonePos.scores, 3), examples: tonePos.examples },
    { cats: topCategories(toneNeg.scores, 3), examples: toneNeg.examples },
    "语气"
  );

  renderTable(reportElements.topFavTerms, terms.top_favorites, [
    { label: "词", render: (item) => createTermLink(item.term || item.label || "-") },
    { label: "次数", value: (item) => item.count ?? "-" }
  ]);
  renderTable(reportElements.topHiddenTerms, terms.top_hidden, [
    { label: "词", render: (item) => createTermLink(item.term || item.label || "-") },
    { label: "次数", value: (item) => item.count ?? "-" }
  ]);
  renderTable(reportElements.preferredTerms, terms.preferred, [
    { label: "词", render: (item) => createTermLink(item.term || item.label || "-") },
    liftColumn(),
    confidenceColumn(),
    countsColumn()
  ]);
  renderTable(reportElements.avoidedTerms, terms.avoided, [
    { label: "词", render: (item) => createTermLink(item.term || item.label || "-") },
    liftColumn(),
    confidenceColumn(),
    countsColumn()
  ]);
  renderTable(reportElements.preferredBigrams, bigrams.preferred, [
    { label: "短语", render: (item) => createTermLink(item.term || item.label || "-") },
    liftColumn(),
    confidenceColumn(),
    countsColumn()
  ]);
  renderTable(reportElements.avoidedBigrams, bigrams.avoided, [
    { label: "短语", render: (item) => createTermLink(item.term || item.label || "-") },
    liftColumn(),
    confidenceColumn(),
    countsColumn()
  ]);

  const sourceJournal = report.source_journal || {};
  const journalData = sourceJournal.journals || {};
  const sourceData = sourceJournal.sources || {};
  const coverage = sourceJournal.coverage || {};

  if (reportElements.sourceJournalCoverage) {
    const journalUnknown = coverage.journal_unknown ?? 0;
    const sourceUnknown = coverage.source_unknown ?? 0;
    reportElements.sourceJournalCoverage.textContent = `期刊缺失 ${journalUnknown} 条，来源缺失 ${sourceUnknown} 条。`;
  }

  renderSummary(reportElements.journalPreferredSummary, journalData.preferred, "更可能收藏的期刊集中在 ");
  renderSummary(reportElements.journalAvoidedSummary, journalData.avoided, "更可能避开的期刊集中在 ");
  renderSummary(reportElements.sourceTopFavSummary, sourceData.top_favorites, "收藏高频来源集中在 ");
  renderSummary(reportElements.sourceTopHiddenSummary, sourceData.top_hidden, "不感兴趣高频来源集中在 ");
  renderSummary(reportElements.sourcePreferredSummary, sourceData.preferred, "更可能收藏的来源集中在 ");
  renderSummary(reportElements.sourceAvoidedSummary, sourceData.avoided, "更可能避开的来源集中在 ");

  const labelOf = (item) => item.term || item.label || "";
  renderTable(reportElements.journalPreferredTerms, journalData.preferred, [
    { label: "期刊", render: (item) => createJournalStatsLink(labelOf(item)) },
    liftColumn(),
    { label: "筛选", render: (item) => createFilterLink("journal", labelOf(item), "favorites") }
  ]);
  renderTable(reportElements.journalAvoidedTerms, journalData.avoided, [
    { label: "期刊", render: (item) => createJournalStatsLink(labelOf(item)) },
    liftColumn(),
    { label: "筛选", render: (item) => createFilterLink("journal", labelOf(item), "all") }
  ]);

  renderTable(reportElements.sourceTopFavTerms, sourceData.top_favorites, [
    { label: "来源", value: (item) => labelOf(item) || "-" },
    { label: "次数", value: (item) => item.count ?? "-" },
    { label: "筛选", render: (item) => createFilterLink("source", labelOf(item), "favorites") }
  ]);
  renderTable(reportElements.sourceTopHiddenTerms, sourceData.top_hidden, [
    { label: "来源", value: (item) => labelOf(item) || "-" },
    { label: "次数", value: (item) => item.count ?? "-" },
    { label: "筛选", render: (item) => createFilterLink("source", labelOf(item), "all") }
  ]);
  renderTable(reportElements.sourcePreferredTerms, sourceData.preferred, [
    { label: "来源", value: (item) => labelOf(item) || "-" },
    liftColumn(),
    { label: "筛选", render: (item) => createFilterLink("source", labelOf(item), "favorites") }
  ]);
  renderTable(reportElements.sourceAvoidedTerms, sourceData.avoided, [
    { label: "来源", value: (item) => labelOf(item) || "-" },
    liftColumn(),
    { label: "筛选", render: (item) => createFilterLink("source", labelOf(item), "all") }
  ]);

  const missing = report.missing_links_sample || {};
  const missingFavLinks = [
    ...(missing.favorites || []),
    ...(missing.archived || [])
  ];
  renderLinkList(reportElements.missingFavLinks, missingFavLinks);
  renderLinkList(reportElements.missingHiddenLinks, missing.hidden || []);
  setStatus("报告已加载");
  checkStaleness(report);
}

function setStaleNotice(text) {
  const el = reportElements.stale;
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.textContent = text;
  el.hidden = false;
}

// 比较报告中的样本量与当前收藏/不感兴趣数量，提示报告是否已过期。
// 注：/api/papers 目前不返回 state_changed_at，因此按数量差估算（同时一增一减会互相抵消）。
async function checkStaleness(report) {
  setStaleNotice("");
  if (!report || !report.counts) return;
  try {
    const res = await fetch("/api/interactions?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    const hidden = new Set(data.hidden || []);
    const positives = new Set(
      [...(data.favorites || []), ...(data.archived || [])].filter((id) => !hidden.has(id))
    );
    const counts = report.counts || {};
    const reportPositive = Number(counts.favorites ?? 0);
    const reportHidden = Number(counts.hidden ?? 0);
    const delta = Math.abs(positives.size - reportPositive) + Math.abs(hidden.size - reportHidden);
    if (delta > 0) {
      setStaleNotice(
        `报告生成后新增约 ${delta} 条收藏/不感兴趣操作（当前收藏 ${positives.size}、不感兴趣 ${hidden.size}；报告中为 ${reportPositive}、${reportHidden}），建议点击「重新计算」。`
      );
    }
  } catch (error) {
    // 静默失败：过期提示只是辅助信息
  }
}

async function readJsonSafely(res) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (error) {
    return { __raw: text };
  }
}

async function loadReport() {
  setStatus("加载报告...");
  try {
    const res = await fetch("/api/preference_report?t=" + Date.now(), { cache: "no-store" });
    const data = await readJsonSafely(res);
    if (!res.ok) {
      renderReport(null);
      if (res.status !== 404) {
        setStatus(data?.message || `加载失败(${res.status})`);
      }
      return;
    }
    if (data?.status === "error") {
      renderReport(null);
      return;
    }
    const report = data?.report || data;
    if (!report || (typeof report === "object" && !report.counts && !report.generated_at)) {
      renderReport(null);
      return;
    }
    renderReport(report);
  } catch (error) {
    setStatus("加载失败，请确认服务器运行中。");
  }
}

async function generateReport() {
  setStatus("计算中...");
  if (reportElements.btnGenerate) {
    reportElements.btnGenerate.disabled = true;
    reportElements.btnGenerate.textContent = "计算中...";
  }
  try {
    const res = await fetch("/api/preference_report", { method: "POST" });
    const data = await readJsonSafely(res);
    if (!res.ok || data?.status !== "ok") {
      setStatus(data?.message || `计算失败(${res.status})`);
      return;
    }
    renderReport(data.report || data);
  } catch (error) {
    setStatus("计算失败，请确认服务器运行中。");
  } finally {
    if (reportElements.btnGenerate) {
      reportElements.btnGenerate.disabled = false;
      reportElements.btnGenerate.textContent = "重新计算";
    }
  }
}

function renderWarnings(dataQuality) {
    const container = reportElements.warnings;
    if (!container || !dataQuality) return;

    container.innerHTML = '';
    const warnings = (dataQuality.warnings || []).filter(Boolean);

    if (!warnings.length) return;

    const fragment = document.createDocumentFragment();
    warnings.forEach(warn => {
        const div = document.createElement('div');
        div.className = `alert alert--${warn.severity}`;
        div.textContent = humanizeMetricText(warn.message);
        fragment.appendChild(div);
    });
    container.appendChild(fragment);
}

function renderInsightsSummary(insights) {
    const container = reportElements.insightsSummary;
    if (!container || !insights || !insights.length) {
        if (container) container.innerHTML = '<p class="report-empty">暂无智能洞察</p>';
        return;
    }

    container.innerHTML = '';
    const fragment = document.createDocumentFragment();

    insights.forEach(insight => {
        const card = document.createElement('div');
        card.className = 'insight-card';

        const title = document.createElement('h4');
        title.className = 'insight-title';
        title.textContent = insight.title;

        const content = document.createElement('p');
        content.className = 'insight-content';
        content.textContent = humanizeMetricText(insight.content);

        card.appendChild(title);
        card.appendChild(content);
        fragment.appendChild(card);
    });

    container.appendChild(fragment);
}

function createConfidenceBar(confidence) {
    if (confidence == null) return document.createTextNode('-');

    const container = document.createElement('div');
    container.className = 'confidence-bar';

    const fill = document.createElement('div');
    fill.className = 'confidence-fill';
    fill.style.width = `${Math.round(confidence * 100)}%`;

    if (confidence > 0.7) {
        fill.style.background = '#10b981';
    } else if (confidence > 0.4) {
        fill.style.background = '#f59e0b';
    } else {
        fill.style.background = '#ef4444';
    }

    container.appendChild(fill);

    const label = document.createElement('span');
    label.className = 'confidence-label';
    label.textContent = `${Math.round(confidence * 100)}%`;
    container.appendChild(label);

    return container;
}

function createTermLink(term) {
    const link = document.createElement('a');
    link.className = 'term-link';
    const params = new URLSearchParams({ q: term, view: "all", from: "insights" });
    link.href = `index.html?${params.toString()}`;
    link.textContent = term;
    link.title = `点击查看包含"${term}"的论文`;
    return link;
}

function renderDistributionBar(favBarEl, hidBarEl, favData, hidData) {
    const favTotal = favData.reduce((sum, item) => sum + (item.count || 0), 0);
    const hidTotal = hidData.reduce((sum, item) => sum + (item.count || 0), 0);
    const total = favTotal + hidTotal;

    if (total === 0) return;

    if (favBarEl) favBarEl.style.width = `${(favTotal / total * 100).toFixed(1)}%`;
    if (hidBarEl) hidBarEl.style.width = `${(hidTotal / total * 100).toFixed(1)}%`;
}

function renderMethodTopicViz(report) {
    const methodTopic = report.method_topic || {};
    const methods = methodTopic.methods || {};
    const topics = methodTopic.topics || {};

    // 渲染Method分布
    renderDistributionBar(
        reportElements.methodFavBar,
        reportElements.methodHidBar,
        methods.top_favorites || [],
        methods.top_hidden || []
    );

    renderTable(reportElements.methodTable, methods.preferred || [], [
        { label: "方法", value: (item) => item.label || "-" },
        liftColumn(),
        confidenceColumn(),
        countsColumn()
    ]);

    // 渲染Topic分布
    renderDistributionBar(
        reportElements.topicFavBar,
        reportElements.topicHidBar,
        topics.top_favorites || [],
        topics.top_hidden || []
    );

    renderTable(reportElements.topicTable, topics.preferred || [], [
        { label: "主题", value: (item) => item.label || "-" },
        liftColumn(),
        confidenceColumn(),
        countsColumn()
    ]);
}

function renderTrendChart(trends) {
    const container = reportElements.trendChart;
    if (!container) return;

    if (!trends || !trends.length) {
        container.innerHTML = '<p class="report-empty">暂无时间趋势数据</p>';
        return;
    }

    container.innerHTML = '';
    const maxTotal = Math.max(...trends.map(t => t.total));

    const fragment = document.createDocumentFragment();
    trends.forEach(item => {
        const barItem = document.createElement('div');
        barItem.className = 'trend-bar-item';

        // 顶部数值标签
        const value = document.createElement('div');
        value.className = 'trend-bar-value';
        value.textContent = item.total;

        // 双色堆叠柱状图容器
        const barContainer = document.createElement('div');
        barContainer.className = 'trend-bar-stacked';
        const heightPct = Math.max(10, (item.total / maxTotal) * 120);
        barContainer.style.height = `${heightPct}px`;

        // 收藏部分
        const favBar = document.createElement('div');
        favBar.className = 'trend-bar-segment trend-bar-segment--fav';
        favBar.style.height = `${item.fav_rate * 100}%`;
        favBar.title = `收藏: ${item.favorites}`;

        // 隐藏部分
        const hidBar = document.createElement('div');
        hidBar.className = 'trend-bar-segment trend-bar-segment--hid';
        hidBar.style.height = `${(1 - item.fav_rate) * 100}%`;
        hidBar.title = `不感兴趣: ${item.hidden}`;

        barContainer.appendChild(favBar);
        barContainer.appendChild(hidBar);

        // 底部月份标签
        const label = document.createElement('div');
        label.className = 'trend-bar-label';
        const monthNum = item.month.split('-')[1];
        label.textContent = monthNum + '月';

        barItem.appendChild(value);
        barItem.appendChild(barContainer);
        barItem.appendChild(label);
        fragment.appendChild(barItem);
    });

    container.appendChild(fragment);
}

function attachHandlers() {
  if (reportElements.btnGenerate) {
    reportElements.btnGenerate.addEventListener("click", generateReport);
  }
  if (reportElements.btnReload) {
    reportElements.btnReload.addEventListener("click", loadReport);
  }
}

let initialized = false;

// 首次进入标签页时调用：只读取已保存的报告（GET），不会自动重新计算（POST）。
function init() {
  if (initialized) return;
  initialized = true;
  attachHandlers();
  loadReport();
}

window.InsightsReport = { init };
})();
