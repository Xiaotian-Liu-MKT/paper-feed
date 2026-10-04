// Zero-dependency smoke tests for the paper_id front-end contract.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("web/app.js", "utf8").replace(/document\.addEventListener\("DOMContentLoaded", init\);/, "");
const storage = new Map();
const element = () => ({ value: "", checked: false, textContent: "", innerHTML: "", style: {}, addEventListener() {}, appendChild() {}, append() {}, click() {}, remove() {}, setAttribute() {}, classList: { toggle() {}, add() {}, remove() {} } });
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
  vm.runInContext(source.match(/function populateJournals[\s\S]*?\n}\n/)[0], context);
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
    location: { search: "?search=nudge&view=all&from=report", pathname: "/index.html", hash: "" },
    history: { replaceState: (_s, _t, url) => { replaced = url; } }
  };
  vm.runInContext(`
    delete globalThis.applyUrlFilters;
  `, context);
  vm.runInContext(source.match(/function applyUrlFilters[\s\S]*?\n}\n/)[0], context);
  vm.runInContext(`
    elements.filterMethod = null; elements.filterTopic = null; elements.filterPreset = null;
    elements.searchInput = { value: "" };
    state.urlFiltersApplied = false; state.filterMode = "all"; state.inboxViewMode = "swipe";
  `, context);
  assert.strictEqual(vm.runInContext("applyUrlFilters()", context), true);
  assert.strictEqual(vm.runInContext("elements.searchInput.value", context), "nudge");
  assert.strictEqual(vm.runInContext("state.filterMode", context), "everything");
  assert.strictEqual(vm.runInContext("state.inboxViewMode", context), "list");
  assert.strictEqual(replaced, "/index.html");
  assert.strictEqual(vm.runInContext("applyUrlFilters()", context), false); // applied only once

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
  assert.strictEqual(vm.runInContext("state.undoStack.length", context), 20);
  vm.runInContext("undoLastInteraction()", context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(vm.runInContext("state.filtered.length", context), 6);
  assert.strictEqual(vm.runInContext("state.interactions.hidden.length", context), 12);
  assert.strictEqual(vm.runInContext("state.interactions.favorites.length", context), 12);
  assert.ok(!/UNDO_BAR_TIMEOUT_MS/.test(source));

  // A title-only AI guess is labelled distinctly from a real AI summary.
  assert.match(source, /基于标题推测/);
  assert.match(source, /未读取摘要，仅根据标题推测/);
  console.log("frontend paper_id tests passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
