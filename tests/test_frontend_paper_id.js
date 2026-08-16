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
  Element: function Element() {}, Intl, Date, Set, Map, JSON, encodeURIComponent, alert() {}, setTimeout, clearTimeout,
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
  console.log("frontend paper_id tests passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
