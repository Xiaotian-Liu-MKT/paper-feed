// Zero-dependency smoke tests for the paper_id front-end contract.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("web/app.js", "utf8").replace(/document\.addEventListener\("DOMContentLoaded", init\);/, "");
const storage = new Map();
const element = () => ({ value: "", checked: false, textContent: "", innerHTML: "", style: {}, addEventListener() {}, classList: { toggle() {}, add() {}, remove() {} } });
const document = {
  getElementById: () => element(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, createElement: () => element(), createDocumentFragment: () => element()
};
const context = { console, document, localStorage: { getItem: (k) => storage.get(k) || null, setItem: (k, v) => storage.set(k, v) },
  Element: function Element() {}, Intl, Date, Set, Map, JSON, encodeURIComponent, alert() {}, setTimeout, clearTimeout };
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
  console.log("frontend paper_id tests passed");
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
