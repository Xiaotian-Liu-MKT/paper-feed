// Zero-dependency smoke tests for the paper_id front-end contract.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("web/app.js", "utf8").replace(/document\.addEventListener\("DOMContentLoaded", init\);/, "");
const storage = new Map();
const element = () => ({ value: "", checked: false, textContent: "", innerHTML: "", style: {}, dataset: {}, addEventListener() {}, appendChild() {}, append() {}, click() {}, remove() {}, setAttribute() {}, classList: { toggle() {}, add() {}, remove() {} } });
const document = {
  getElementById: () => element(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, createElement: () => element(), createDocumentFragment: () => element(), body: { appendChild() {} }
};
const context = { console, document, localStorage: { getItem: (k) => storage.get(k) || null, setItem: (k, v) => storage.set(k, v) },
  Element: function Element() {}, Intl, Date, Set, Map, JSON, encodeURIComponent, URLSearchParams, alert() {}, setTimeout, clearTimeout,
  URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} } };
vm.createContext(context);
vm.runInContext(source, context);

async function run() {
  assert.strictEqual(vm.runInContext('paperKey({paper_id:"p", id:"i", link:"l"})', context), "p");
  assert.strictEqual(vm.runInContext('paperKey({id:"i", link:"l"})', context), "i");
  assert.strictEqual(vm.runInContext('paperKey({link:"l"})', context), "l");

  let calls = [];
  context.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ interactions: { favorites: ["paper-1"], archived: [], hidden: [] } }) }; };
  vm.runInContext('state.paperApiAvailable=true; state.interactions={favorites:[],archived:[],hidden:[]}', context);
  await vm.runInContext('saveInteraction({paper_id:"paper-1", id:"legacy", link:"https://different"}, "like")', context);
  assert.strictEqual(calls[0].url, "/api/papers/paper-1/review");
  assert.deepStrictEqual(JSON.parse(calls[0].options.body), { action: "like" });
  assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(state.interactions)', context)), { favorites: ["paper-1"], archived: [], hidden: [] });

  vm.runInContext('state.interactions={favorites:["paper-1"],archived:[],hidden:[]}; applyInteractionAction("paper-1", "archive")', context);
  assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(state.interactions)', context)), { favorites: [], archived: ["paper-1"], hidden: [] });
  vm.runInContext('applyInteractionAction("paper-1", "unarchive")', context);
  assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(state.interactions)', context)), { favorites: [], archived: [], hidden: [] });

  // A failed new API write restores the optimistic state instead of losing it.
  context.fetch = async () => ({ ok: false, json: async () => ({ message: "offline" }) });
  vm.runInContext('state.paperApiAvailable=true; state.interactions={favorites:[],archived:[],hidden:[]}; applyFilters=()=>{}', context);
  vm.runInContext('performInteraction({paper_id:"paper-1", id:"legacy", link:"https://different"}, "like")', context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(state.interactions)', context)), { favorites: [], archived: [], hidden: [] });

  // Shortcuts remain deliberately limited to inbox/all and non-input targets.
  assert.match(source, /state\.filterMode !== "all" \|\| document\.querySelector\("dialog\[open\]"\)/);
  assert.match(source, /isTypingTarget\(event\.target\)/);

  calls = [];
  let apiAttempt = 0;
  context.fetch = async (url) => {
    calls.push(url);
    if (url.startsWith("/api/papers")) { apiAttempt++; return { ok: false, json: async () => ({}) }; }
    return { ok: true, json: async () => ({ items: [{ id: "legacy", link: "https://link", title: "T", pub_date: "2026-01-01" }] }) };
  };
  vm.runInContext('populateJournals=()=>{}; applyUrlFilters=()=>{}; attachHandlers=()=>{}; applyFilters=()=>{}; updateFilterCounts=()=>{}', context);
  assert.strictEqual(await vm.runInContext('loadFeed()', context), true);
  assert.strictEqual(apiAttempt, 1);
  assert.ok(calls.some((url) => url.startsWith("feed.json?")));
  assert.strictEqual(vm.runInContext('state.paperApiAvailable', context), false);

  // Swipe decisions keep paper_id as the identity, include all three inbox
  // actions, and undo in LIFO order without rebuilding from legacy links.
  calls = [];
  context.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ interactions: { favorites: [], archived: [], hidden: [] } }) };
  };
  vm.runInContext(`
    state.paperApiAvailable = true;
    state.filterMode = 'all'; state.inboxViewMode = 'swipe'; state.swipeBusy = false;
    state.interactions = { favorites: [], archived: [], hidden: [] };
    state.filtered = [
      { paper_id: 'p-like', link: 'legacy-like' },
      { paper_id: 'p-hide', link: 'legacy-hide' },
      { paper_id: 'p-archive', link: 'legacy-archive' }
    ];
    state.undoStack = []; state.swipeIndex = 0;
    elements.list.querySelector = () => null;
    renderList = () => {}; renderUndoStack = () => {}; updateFilterCounts = () => {};
  `, context);
  vm.runInContext(`commitSwipeAction('like', 'right')`, context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  vm.runInContext(`commitSwipeAction('hide', 'left')`, context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  vm.runInContext(`commitSwipeAction('archive', 'archive')`, context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepStrictEqual(calls.slice(0, 3).map((call) => call.url), [
    '/api/papers/p-like/review', '/api/papers/p-hide/review', '/api/papers/p-archive/review'
  ]);
  assert.deepStrictEqual(calls.slice(0, 3).map((call) => JSON.parse(call.options.body).action), ['like', 'hide', 'archive']);
  assert.strictEqual(vm.runInContext('state.undoStack.length', context), 3);
  vm.runInContext('undoLastInteraction()', context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  vm.runInContext('undoLastInteraction()', context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  vm.runInContext('undoLastInteraction()', context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(vm.runInContext('state.undoStack.length', context), 0);
  assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(state.filtered.map(paperKey))', context)), ['p-like', 'p-hide', 'p-archive']);

  // A rejected swipe write restores its optimistic interaction state.
  context.fetch = async () => ({ ok: false, json: async () => ({ message: 'offline' }) });
  vm.runInContext(`
    state.interactions = { favorites: [], archived: [], hidden: [] };
    state.filtered = [{ paper_id: 'p-fail', link: 'legacy-fail' }]; state.swipeIndex = 0;
    state.swipeBusy = false; applyFilters = () => { state.filtered = []; };
    commitSwipeAction('like', 'right');
  `, context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(state.interactions)', context)), { favorites: [], archived: [], hidden: [] });

  // Writes are mutually exclusive: neither an undo during a pending action nor
  // a second undo during a pending undo may issue a competing request.
  let releaseWrite;
  let pendingCalls = 0;
  context.fetch = () => {
    pendingCalls += 1;
    return new Promise((resolve) => { releaseWrite = () => resolve({ ok: true, json: async () => ({ interactions: { favorites: [], archived: [], hidden: [] } }) }); });
  };
  vm.runInContext(`
    state.interactions = { favorites: [], archived: [], hidden: [] };
    state.filtered = [{ paper_id: 'p-pending', link: 'legacy-pending' }];
    state.undoStack = []; state.swipeIndex = 0; state.swipeBusy = false;
    applyFilters = () => {}; commitSwipeAction('like', 'right');
  `, context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  vm.runInContext('undoLastInteraction()', context);
  assert.strictEqual(pendingCalls, 1);
  assert.strictEqual(vm.runInContext('state.undoStack.length', context), 1);
  releaseWrite();
  await new Promise((resolve) => setTimeout(resolve, 0));
  vm.runInContext('undoLastInteraction(); undoLastInteraction()', context);
  assert.strictEqual(pendingCalls, 2);
  assert.strictEqual(vm.runInContext('state.undoStack.length', context), 0);
  releaseWrite();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(vm.runInContext('state.swipeBusy', context), false);

  // RIS export consumes a blob, handles empty favorites without a request, and
  // reports HTTP failures without changing interaction state.
  calls = [];
  vm.runInContext('state.interactions={favorites:[],archived:[],hidden:[]}', context);
  context.fetch = async (...args) => { calls.push(args); throw new Error('should not fetch'); };
  assert.strictEqual(await vm.runInContext('exportFavoritesRis()', context), false);
  assert.strictEqual(calls.length, 0);
  vm.runInContext('state.interactions={favorites:["p-ris"],archived:[],hidden:[]}', context);
  context.fetch = async (url, options) => ({ ok: true, blob: async () => ({ type: 'application/x-research-info-systems' }) });
  assert.strictEqual(await vm.runInContext('exportFavoritesRis()', context), true);
  context.fetch = async () => ({ ok: false, status: 503, blob: async () => ({}) });
  assert.strictEqual(await vm.runInContext('exportFavoritesRis()', context), false);

  // Keyboard shortcuts are constrained to swipe inbox and ignored while typing.
  assert.match(source, /isTypingTarget\(event\.target\)/);
  assert.match(source, /state\.filterMode !== "all" \|\| document\.querySelector\("dialog\[open\]"\)/);
  assert.match(source, /key === "arrowright"/);

  // Buttons (tabs, toggles) must not swallow shortcuts; form fields still do.
  const fakeTarget = (matches) => Object.assign(Object.create(context.Element.prototype), { closest: (selector) => (matches(selector) ? {} : null) });
  context.buttonTarget = fakeTarget((selector) => /(^|,\s*)button(,|$)/.test(selector));
  context.inputTarget = fakeTarget((selector) => selector.includes("input"));
  assert.strictEqual(vm.runInContext("isTypingTarget(buttonTarget)", context), false);
  assert.strictEqual(vm.runInContext("isTypingTarget(inputTarget)", context), true);
  // Only text-like fields swallow shortcuts; selects and checkboxes do not.
  const typedInput = (type) => Object.assign(Object.create(context.Element.prototype), {
    closest: (selector) => (selector === "input" ? { getAttribute: () => type } : null)
  });
  context.textTarget = typedInput("text");
  context.searchTarget = typedInput("search");
  context.checkboxTarget = typedInput("checkbox");
  context.selectTarget = fakeTarget((selector) => selector === "select");
  context.textareaTarget = fakeTarget((selector) => selector.includes("textarea"));
  assert.strictEqual(vm.runInContext("isTypingTarget(textTarget)", context), true);
  assert.strictEqual(vm.runInContext("isTypingTarget(searchTarget)", context), true);
  assert.strictEqual(vm.runInContext("isTypingTarget(textareaTarget)", context), true);
  assert.strictEqual(vm.runInContext("isTypingTarget(checkboxTarget)", context), false);
  assert.strictEqual(vm.runInContext("isTypingTarget(selectTarget)", context), false);

  // Journal dropdown is rebuilt (no duplicates) and keeps the current selection.
  const fakeSelect = () => {
    const select = { options: [], _value: "", appendChild(option) { this.options.push(option); } };
    Object.defineProperty(select, "textContent", { set() { select.options = []; }, get() { return ""; } });
    Object.defineProperty(select, "value", {
      get() { return select._value; },
      set(v) { select._value = select.options.some((o) => o.value === v) ? v : ""; }
    });
    return select;
  };
  context.fakeJournalSelect = fakeSelect();
  vm.runInContext(`
    delete globalThis.populateJournals;
  `, context);
  vm.runInContext(source.match(/function populateJournals[\s\S]*?\r?\n}\r?\n/)[0], context);
  vm.runInContext(`
    elements.journalSelect = fakeJournalSelect;
    populateJournals([{ journal: "B" }, { journal: "A" }]);
    elements.journalSelect.value = "B";
    populateJournals([{ journal: "B" }, { journal: "A" }, { journal: "C" }]);
  `, context);
  assert.deepStrictEqual(context.fakeJournalSelect.options.map((o) => o.value), ["", "A", "B", "C"]);
  assert.strictEqual(context.fakeJournalSelect.value, "B");

  // URL filters: `search` is an alias of `q`, view=all means every paper,
  // any filter switches the inbox to list mode, and params are cleared once.
  let replaced = null;
  context.window = {
    location: { search: "?search=nudge&view=all&from=insights", pathname: "/index.html", hash: "" },
    history: { replaceState: (_s, _t, url) => { replaced = url; } }
  };
  vm.runInContext(`
    delete globalThis.applyUrlFilters;
  `, context);
  vm.runInContext(source.match(/function applyUrlFilters[\s\S]*?\r?\n}\r?\n/)[0], context);
  vm.runInContext(`
    elements.filterMethod = null; elements.filterTopic = null; elements.filterPreset = null;
    elements.searchInput = { value: "" };
    elements.backLink = { href: "", textContent: "", hidden: true };
    state.urlFiltersApplied = false; state.filterMode = "all"; state.inboxViewMode = "swipe";
  `, context);
  assert.strictEqual(vm.runInContext("applyUrlFilters()", context), true);
  // from=insights shows a back link to the merged 洞察 page.
  assert.strictEqual(vm.runInContext("elements.backLink.hidden", context), false);
  assert.strictEqual(vm.runInContext("elements.backLink.href", context), "insights.html");
  assert.strictEqual(vm.runInContext("elements.backLink.textContent", context), "← 返回洞察");
  assert.strictEqual(vm.runInContext("elements.searchInput.value", context), "nudge");
  assert.strictEqual(vm.runInContext("state.filterMode", context), "everything");
  assert.strictEqual(vm.runInContext("state.inboxViewMode", context), "list");
  assert.strictEqual(replaced, "/index.html");
  assert.strictEqual(vm.runInContext("applyUrlFilters()", context), false); // applied only once
  // Legacy from=report / from=stats links still get a back link to the matching 洞察 tab.
  for (const [legacy, target] of [["report", "insights.html#prefs"], ["stats", "insights.html#journals"]]) {
    context.window.location.search = `?q=x&from=${legacy}`;
    vm.runInContext(`
      elements.backLink = { href: "", textContent: "", hidden: true };
      state.urlFiltersApplied = false;
    `, context);
    vm.runInContext("applyUrlFilters()", context);
    assert.strictEqual(vm.runInContext("elements.backLink.href", context), target);
    assert.strictEqual(vm.runInContext("elements.backLink.textContent", context), "← 返回洞察");
  }
  // Unknown from values do not show a back link.
  context.window.location.search = "?q=x&from=elsewhere";
  vm.runInContext(`elements.backLink = { href: "", textContent: "", hidden: true }; state.urlFiltersApplied = false;`, context);
  vm.runInContext("applyUrlFilters()", context);
  assert.strictEqual(vm.runInContext("elements.backLink.hidden", context), true);

  // List actions keep pagination, are undoable, and the undo history is capped
  // instead of being wiped by a timer.
  context.fetch = async () => ({ ok: true, json: async () => ({ interactions: null }) });
  vm.runInContext(`
    state.filterMode = "all"; state.inboxViewMode = "list"; state.swipeBusy = false; state.pendingWrites = 0;
    state.interactions = { favorites: [], archived: [], hidden: [] };
    state.items = Array.from({ length: 30 }, (_, i) => ({ paper_id: "lp-" + i }));
    state.filtered = state.items.slice();
    state.visibleLimit = 80; state.undoStack = [];
    renderList = () => {};
    for (let i = 0; i < 25; i++) performInteraction(state.filtered[0], i % 2 ? "hide" : "like");
  `, context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(vm.runInContext("state.visibleLimit", context), 80);
  assert.strictEqual(vm.runInContext("state.filtered.length", context), 5);
  assert.strictEqual(vm.runInContext("state.undoStack.length", context), 25);
  vm.runInContext("undoLastInteraction()", context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(vm.runInContext("state.filtered.length", context), 6);
  assert.strictEqual(vm.runInContext("state.interactions.hidden.length", context), 12);
  assert.strictEqual(vm.runInContext("state.interactions.favorites.length", context), 12);
  // The history is capped at MAX_UNDO_STACK_SIZE (50), dropping the oldest.
  vm.runInContext(`state.undoStack = []; for (let i = 0; i < 60; i++) pushUndo({ id: "cap-" + i, action: "like" });`, context);
  assert.strictEqual(vm.runInContext("state.undoStack.length", context), 50);
  assert.strictEqual(vm.runInContext("state.undoStack[0].id", context), "cap-10");
  assert.ok(!/UNDO_BAR_TIMEOUT_MS/.test(source));

  // A title-only AI guess is labelled distinctly from a real AI summary.
  assert.match(source, /基于标题推测/);
  assert.match(source, /未读取摘要，仅根据标题推测/);

  // Checkbox filter popover: 全部 clears the others, clearing all falls back to 全部.
  const checkbox = (value, checked) => ({ value, checked });
  const boxes = [checkbox("", false), checkbox("Experiment", true), checkbox("Survey", false)];
  context.fakeFilter = { tagName: "DIV", id: "", dataset: {}, querySelectorAll: () => boxes };
  vm.runInContext('normalizeCheckboxFilter(fakeFilter, null)', context);
  assert.deepStrictEqual(boxes.map((b) => b.checked), [false, true, false]);
  boxes[0].checked = true;
  context.allBox = boxes[0];
  vm.runInContext('normalizeCheckboxFilter(fakeFilter, allBox)', context);
  assert.deepStrictEqual(boxes.map((b) => b.checked), [true, false, false]);
  boxes[2].checked = true;
  context.surveyBox = boxes[2];
  vm.runInContext('normalizeCheckboxFilter(fakeFilter, surveyBox)', context);
  assert.deepStrictEqual(boxes.map((b) => b.checked), [false, false, true]);
  boxes[2].checked = false;
  vm.runInContext('normalizeCheckboxFilter(fakeFilter, surveyBox)', context);
  assert.deepStrictEqual(boxes.map((b) => b.checked), [true, false, false]);

  // DOI links and the OpenAlex provenance badge.
  assert.strictEqual(vm.runInContext('normalizeDoi("https://doi.org/10.1000/xyz")', context), "10.1000/xyz");
  assert.strictEqual(vm.runInContext('normalizeDoi("doi:10.1000/abc")', context), "10.1000/abc");
  assert.strictEqual(vm.runInContext('createDoiLink({ doi: null })', context), null);
  assert.strictEqual(vm.runInContext('createDoiLink({ doi: "10.1000/a b" }).href', context), "https://doi.org/10.1000/a%20b");
  assert.match(source, /📖 OpenAlex/);

  // AI readiness: ai_ready wins when present, otherwise fall back to has_api_key.
  const aiReady = (config) => vm.runInContext(`configAiReady(${JSON.stringify(config)})`, context);
  assert.strictEqual(aiReady(null), false);
  assert.strictEqual(aiReady({ has_api_key: true }), true);
  assert.strictEqual(aiReady({ has_api_key: false }), false);
  assert.strictEqual(aiReady({ ai_ready: true, has_api_key: false }), true);
  assert.strictEqual(aiReady({ ai_ready: false, has_api_key: true }), false);
  const backend = (config) => vm.runInContext(`configEffectiveBackend(${JSON.stringify(config)})`, context);
  assert.strictEqual(backend({ has_api_key: true }), "openai");
  assert.strictEqual(backend({ AI_BACKEND: "codex", effective_backend: "codex" }), "codex");
  assert.strictEqual(backend({ AI_BACKEND: "codex", effective_backend: "openai", codex_available: false }), "openai");
  assert.match(vm.runInContext('aiCostNote({ effective_backend: "codex" })', context), /Codex CLI 调用 gpt-6-luna，消耗 ChatGPT 订阅额度/);
  assert.match(vm.runInContext('aiCostNote({ has_api_key: true })', context), /API 额度/);
  const status = (config) => JSON.parse(vm.runInContext(`JSON.stringify(describeAiStatus(${JSON.stringify(config)}))`, context));
  assert.deepStrictEqual(status({ has_api_key: true }), { text: "API Key 状态：✓ 已配置", state: "ok" });
  assert.strictEqual(status({ AI_BACKEND: "codex", CODEX_MODEL: "gpt-6-luna", codex_available: true, ai_ready: true }).text, "AI 状态：✓ 使用 Codex CLI（gpt-6-luna）");
  const fallback = status({ AI_BACKEND: "codex", codex_available: false, effective_backend: "openai", ai_ready: true, has_api_key: true });
  assert.match(fallback.text, /未找到 codex 命令.*npm i -g @openai\/codex.*codex login.*回退到 OpenAI/);
  assert.strictEqual(fallback.state, "missing");
  assert.match(status({ AI_BACKEND: "openai", has_api_key: false }).text, /✗ 未配置 API Key/);

  // Every POST declares a JSON body (the server rejects others with 415).
  const posts = source.match(/method:\s*"POST"[^}]*/g) || [];
  assert.ok(posts.length > 0);
  posts.forEach((call) => assert.match(call, /Content-Type/));
  // --- Persisted UI state: filters, sort, 显示摘要 and positions round-trip. ---
  const toasts = [];
  vm.runInContext(`showToast = (message, type, ms, action) => { __toasts.push({ message, type, action }); }`, Object.assign(context, { __toasts: toasts }));
  const multi = (values) => ({ tagName: "SELECT", multiple: true, selectedOptions: values.map((value) => ({ value })) });
  context.uiFixture = {
    searchInput: { value: "nudge" }, journalSelect: { value: "JM" },
    filterMethod: multi(["Experiment", "Survey"]), filterTopic: multi(["AI & Tech"]),
    filterMethodMode: { value: "all" }, filterTopicMode: { value: "any" }, filterPreset: { value: "cross" },
    fromDate: { value: "2026-01-01" }, toDate: { value: "" }, sortSelect: { value: "asc" }, summaryToggle: { checked: false }
  };
  storage.clear();
  vm.runInContext(`
    Object.assign(elements, uiFixture);
    state.transientUiState = false; state.pendingFilterSelections = null;
    state.filterMode = "favorites"; state.inboxViewMode = "list";
    saveUiState();
  `, context);
  const saved = JSON.parse(storage.get("paper-feed:ui-state"));
  assert.deepStrictEqual(saved, {
    search: "nudge", journal: "JM", tab: "favorites", mode: "list", methods: ["Experiment", "Survey"], topics: ["AI & Tech"],
    methodMode: "all", topicMode: "any", preset: "cross", fromDate: "2026-01-01", toDate: "", sort: "asc", showSummary: false
  });
  storage.set("paper-feed:ui-state", JSON.stringify({ ...saved, views: { favorites: { scroll: 640, limit: 120 } }, swipePaperId: "sw-b" }));
  context.blankFixture = {
    searchInput: { value: "" }, journalSelect: { value: "" }, filterMethod: null, filterTopic: null,
    filterMethodMode: { value: "any" }, filterTopicMode: { value: "any" }, filterPreset: { value: "" },
    fromDate: { value: "" }, toDate: { value: "" }, sortSelect: { value: "desc" }, summaryToggle: { checked: true }
  };
  vm.runInContext(`Object.assign(elements, blankFixture); state.filterMode = "all"; state.inboxViewMode = "swipe"; restoreUiState();`, context);
  const restored = JSON.parse(vm.runInContext(`JSON.stringify({
    tab: state.filterMode, mode: state.inboxViewMode, search: elements.searchInput.value, journal: state.pendingJournal,
    pending: state.pendingFilterSelections, methodMode: elements.filterMethodMode.value, preset: elements.filterPreset.value,
    fromDate: elements.fromDate.value, sort: elements.sortSelect.value, showSummary: elements.summaryToggle.checked,
    positions: state.restoredPositions })`, context));
  assert.deepStrictEqual(restored, {
    tab: "favorites", mode: "list", search: "nudge", journal: "JM",
    pending: { methods: ["Experiment", "Survey"], topics: ["AI & Tech"] }, methodMode: "all", preset: "cross",
    fromDate: "2026-01-01", sort: "asc", showSummary: false,
    positions: { views: { favorites: { scroll: 640, limit: 120 } }, swipePaperId: "sw-b" }
  });
  // Before categories load, saving keeps the restored method/topic selection.
  vm.runInContext("saveUiState()", context);
  assert.deepStrictEqual(JSON.parse(storage.get("paper-feed:ui-state")).methods, ["Experiment", "Survey"]);
  // Garbage in storage is ignored rather than throwing.
  storage.set("paper-feed:ui-state", "{not json");
  vm.runInContext("restoreUiState()", context);
  assert.strictEqual(vm.runInContext("state.filterMode", context), "favorites");

  // First render restores pagination depth + scroll for the current list view…
  const scrolls = [];
  context.window = { scrollY: 0, scrollTo: (x, y) => { scrolls.push(y); context.window.scrollY = y; }, location: { search: "", pathname: "/", hash: "" } };
  vm.runInContext(`
    state.transientUiState = false; state.positionRestored = false; state.positionPending = false;
    state.filterMode = "favorites"; state.visibleLimit = PAGE_SIZE;
    state.filtered = Array.from({ length: 200 }, (_, i) => ({ paper_id: "fv-" + i }));
    state.restoredPositions = { views: { favorites: { scroll: 640, limit: 120 } }, swipePaperId: "sw-b" };
  `, context);
  assert.strictEqual(vm.runInContext("restoreViewPosition()", context), true);
  assert.strictEqual(vm.runInContext("state.visibleLimit", context), 120);
  assert.deepStrictEqual(scrolls, [640]);
  // …and later scrolls are recorded per view (key includes the inbox mode).
  storage.set("paper-feed:ui-state", "{}");
  context.window.scrollY = 900;
  vm.runInContext("rememberViewPosition()", context);
  assert.deepStrictEqual(JSON.parse(storage.get("paper-feed:ui-state")).views, { favorites: { scroll: 900, limit: 120 } });
  assert.strictEqual(vm.runInContext('viewStateKey("all", "list")', context), "all:list");
  // Switching views flushes the old position and defers saving for the new one.
  vm.runInContext(`syncViewControls = () => {}; setFilterMode("archived")`, context);
  assert.strictEqual(vm.runInContext("state.positionPending", context), true);
  context.window.scrollY = 5;
  assert.strictEqual(vm.runInContext("rememberViewPosition()", context), false);

  // The swipe deck reopens on the same paper_id, not the same index.
  vm.runInContext(`
    state.positionRestored = false; state.filterMode = "all"; state.inboxViewMode = "swipe"; state.swipeIndex = 0;
    state.filtered = [{ paper_id: "sw-a" }, { paper_id: "sw-new" }, { paper_id: "sw-b" }];
    state.restoredPositions = { views: {}, swipePaperId: "sw-b" };
  `, context);
  vm.runInContext("restoreViewPosition()", context);
  assert.strictEqual(vm.runInContext("state.swipeIndex", context), 2);
  vm.runInContext(`state.positionRestored = false; state.swipeIndex = 0; state.restoredPositions = { views: {}, swipePaperId: "gone" };`, context);
  assert.strictEqual(vm.runInContext("restoreViewPosition()", context), false);
  assert.strictEqual(vm.runInContext("state.swipeIndex", context), 0);

  // URL jumps from 洞察 are transient: nothing is written over saved state.
  storage.set("paper-feed:ui-state", JSON.stringify({ tab: "favorites", mode: "swipe", journal: "Saved" }));
  context.window.location.search = "?journal=Other&from=insights";
  context.window.history = { replaceState() {} };
  vm.runInContext(`
    elements.journalSelect = { value: "", options: [{ value: "" }, { value: "Other" }] };
    elements.backLink = { href: "", textContent: "", hidden: true };
    state.urlFiltersApplied = false; state.transientUiState = false;
  `, context);
  assert.strictEqual(vm.runInContext("applyUrlFilters()", context), true);
  assert.strictEqual(vm.runInContext("state.transientUiState", context), true);
  assert.strictEqual(vm.runInContext("saveUiState()", context), false);
  assert.deepStrictEqual(JSON.parse(storage.get("paper-feed:ui-state")), { tab: "favorites", mode: "swipe", journal: "Saved" });
  vm.runInContext("state.transientUiState = false", context);

  // --- Undo survives view switches and says where the paper went. ---
  context.fetch = async () => ({ ok: true, json: async () => ({ interactions: null }) });
  toasts.length = 0;
  vm.runInContext(`
    state.filterMode = "all"; state.inboxViewMode = "list"; state.swipeBusy = false; state.pendingWrites = 0;
    state.interactions = { favorites: [], archived: [], hidden: [] };
    state.items = [{ paper_id: "uv-1", title: "Cross view paper" }, { paper_id: "uv-2", title: "Other" }];
    state.filtered = state.items.slice(); state.undoStack = [];
    performInteraction(state.items[0], "like");
  `, context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  vm.runInContext(`setFilterMode("archived"); state.filtered = [];`, context);
  assert.strictEqual(vm.runInContext("state.undoStack.length", context), 1);
  assert.strictEqual(vm.runInContext("state.undoStack[0].view", context), "all");
  vm.runInContext("undoLastInteraction()", context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepStrictEqual(JSON.parse(vm.runInContext("JSON.stringify(state.interactions)", context)), { favorites: [], archived: [], hidden: [] });
  assert.strictEqual(vm.runInContext("state.filtered.length", context), 0); // not reinserted into 归档
  assert.strictEqual(toasts.length, 1);
  assert.strictEqual(toasts[0].message, "已撤销：Cross view paper（现在位于“待筛选”视图）");
  assert.strictEqual(toasts[0].action.label, "前往待筛选");
  // Undo in the view where the action happened needs no toast.
  toasts.length = 0;
  vm.runInContext(`setFilterMode("all"); state.filtered = state.items.slice(); performInteraction(state.items[1], "archive");`, context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  vm.runInContext("undoLastInteraction()", context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(toasts.length, 0);
  assert.ok(vm.runInContext('state.filtered.some((item) => item.paper_id === "uv-2")', context));

  // --- Abstract drafts are keyed by paper_id and mirrored to localStorage. ---
  vm.runInContext(`setAbstractDraft("d-1", "prefill", "prefill")`, context);
  assert.strictEqual(vm.runInContext("hasUnsavedAbstractDrafts()", context), false); // untouched prefill
  vm.runInContext(`setAbstractDraft("d-1", "prefill plus my edits")`, context);
  assert.strictEqual(vm.runInContext("hasUnsavedAbstractDrafts()", context), true);
  assert.deepStrictEqual(JSON.parse(storage.get("paper-feed:abstract-drafts")), { "d-1": { text: "prefill plus my edits", base: "prefill" } });
  assert.strictEqual(vm.runInContext('loadAbstractDrafts().get("d-1").text', context), "prefill plus my edits");
  vm.runInContext(`clearAbstractDraft("d-1")`, context);
  assert.strictEqual(vm.runInContext("hasUnsavedAbstractDrafts()", context), false);
  assert.deepStrictEqual(JSON.parse(storage.get("paper-feed:abstract-drafts")), {});
  assert.strictEqual(vm.runInContext('abstractPrefill({ abstract: "guess", abstract_source: "gpt_generated" })', context), "");
  assert.strictEqual(vm.runInContext('abstractPrefill({ abstract: "zh", raw_abstract: "raw" })', context), "raw");

  // --- 补全摘要（免费）job: body, summary text, 404 degradation, no-DOI stop. ---
  assert.strictEqual(vm.runInContext('fetchAbstractsSummary({ kind: "fetch_abstracts", result: { fetched: 7, failed: 3, skipped: 0 } })', context), "补到 7 篇摘要，3 篇未找到。");
  assert.strictEqual(vm.runInContext('fetchAbstractsSummary({ kind: "summarize", result: {} })', context), null);
  assert.strictEqual(vm.runInContext('jobMessage({ error: "另一个任务正在运行（fetch）/ Another Paper Feed task is running" })', context), "另一个任务正在运行（fetch）/ Another Paper Feed task is running");
  calls = [];
  context.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url.startsWith("/api/fetch_abstracts/pending")) return { ok: true, status: 200, json: async () => ({ pending: 4, with_doi: 0, total: 9 }) };
    return { ok: true, status: 202, json: async () => ({ job: { id: "j1", status: "succeeded", result: { fetched: 1, failed: 0 } } }) };
  };
  context.confirm = () => true;
  toasts.length = 0;
  vm.runInContext('state.interactions = { favorites: ["f-1"], archived: [], hidden: [] }; state.paperApiAvailable = true; loadFeed = async () => true;', context);
  assert.strictEqual(await vm.runInContext("runFetchAbstracts()", context), null);
  assert.ok(calls.every((call) => !call.options || call.options.method !== "POST"));
  assert.strictEqual(toasts[0].message, "没有可按 DOI 查找的收藏。");
  calls = [];
  context.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url.startsWith("/api/fetch_abstracts/pending")) return { ok: true, status: 200, json: async () => ({ pending: 4, with_doi: 3, total: 9 }) };
    return { ok: true, status: 202, json: async () => ({ job: { id: "j1", status: "succeeded", result: { fetched: 2, failed: 1 } } }) };
  };
  let confirmText = "";
  context.confirm = (text) => { confirmText = text; return true; };
  toasts.length = 0;
  await vm.runInContext("runFetchAbstracts()", context);
  assert.match(confirmText, /免费查找 3 篇收藏的原始摘要（不消耗 AI 额度）/);
  const post = calls.find((call) => call.url === "/api/fetch_abstracts");
  assert.ok(post);
  assert.strictEqual(post.options.headers["Content-Type"], "application/json");
  assert.deepStrictEqual(JSON.parse(post.options.body), { view: "favorite" });
  assert.ok(toasts.some((toast) => toast.message === "补全摘要完成：补到 2 篇摘要，1 篇未找到。"));
  context.fetch = async () => ({ ok: false, status: 404, json: async () => ({}) });
  vm.runInContext("state.fetchAbstractsAvailable = null", context);
  assert.strictEqual(await vm.runInContext("fetchPendingAbstracts()", context), null);
  assert.strictEqual(vm.runInContext("state.fetchAbstractsAvailable", context), false);
  assert.match(source, /fetch_abstracts: "\/api\/fetch_abstracts"/); // resumable via GET /api/jobs

  console.log("frontend paper_id tests passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
