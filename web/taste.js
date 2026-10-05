// 「AI 品味」标签页（insights.html#taste）。作用域限定在 IIFE 内，由 insights.js 首次激活时调用 InsightsTaste.init()。
//   GET  /api/taste_profile          当前画像 + 样本计数 + AI 就绪状态
//   POST /api/taste_profile          后台 job（taste_profile）：AI 生成画像
//   POST /api/taste_profile/save     保存手动编辑的画像（source = user）
//   GET  /api/taste_score/pending    待打分的待筛选论文数
//   POST /api/taste_score            后台 job（taste_score）：为待筛选论文打分
// 模型生成的文本一律通过 textContent 渲染，不拼接 innerHTML。
(function () {
  "use strict";

  const SECTIONS = [
    { key: "likes", label: "偏好的研究问题 / 视角", hint: "一行一条" },
    { key: "dislikes", label: "不感兴趣的方向", hint: "一行一条" },
    { key: "boundaries", label: "边界判断", hint: "同样是 X，要 Y 不要 Z；一行一条" },
    { key: "methods", label: "偏好的方法 / 情境", hint: "一行一条" }
  ];
  const LIST_LIMIT = 12;
  const SUMMARY_LIMIT = 2000;
  const RESAMPLE_THRESHOLD = 30;
  const DEFAULT_MIN_SAMPLES = 10;
  const MIN_POSITIVES = 5;
  const JOB_LABELS = { taste_profile: "生成品味画像", taste_score: "品味打分" };
  const JOB_POLL_TIMEOUT_MS = 30 * 60 * 1000;
  const JOB_MAX_STATUS_ERRORS = 5;

  const $ = (id) => document.getElementById(id);
  const els = {};

  const state = {
    data: null, // GET /api/taste_profile
    pending: null, // GET /api/taste_score/pending
    editing: false,
    activeJobs: new Set()
  };

  const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });

  function formatDateTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return dateTimeFormatter.format(date);
  }

  function num(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function strList(value) {
    return Array.isArray(value) ? value.filter((v) => typeof v === "string" && v.trim()).map((v) => v.trim()) : [];
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function readJson(res) {
    try {
      return await res.json();
    } catch (_) {
      return {};
    }
  }

  function setStatus(text) {
    if (els.status) els.status.textContent = text || "";
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // ---------- 样本 / 就绪判断 ----------

  function counts() {
    const c = (state.data && state.data.counts) || {};
    return { favorite: num(c.favorite), archived: num(c.archived), hidden: num(c.hidden) };
  }

  function minSamples() {
    const m = state.data && Number(state.data.min_samples);
    return Number.isFinite(m) && m > 0 ? m : DEFAULT_MIN_SAMPLES;
  }

  // 与后端一致：正样本（收藏+归档）≥ 5，且正样本+不感兴趣 ≥ min_samples。
  function sampleShortfall() {
    const c = counts();
    const positives = c.favorite + c.archived;
    const min = minSamples();
    const needPositives = Math.min(MIN_POSITIVES, min);
    const parts = [];
    if (positives < needPositives) parts.push(`还需 ${needPositives - positives} 篇收藏/归档`);
    if (positives + c.hidden < min) parts.push(`收藏/归档 + 不感兴趣合计还差 ${min - positives - c.hidden} 篇`);
    return parts;
  }

  function aiReady() {
    return Boolean(state.data && state.data.ai_ready);
  }

  function aiReasonText() {
    const reason = state.data && state.data.ai_reason;
    return reason ? String(reason) : "未配置可用的 AI 后端。";
  }

  function profile() {
    const p = state.data && state.data.profile;
    return p && typeof p === "object" ? p : null;
  }

  // ---------- 渲染 ----------

  function renderCounts() {
    const c = counts();
    els.countFavorite.textContent = String(c.favorite);
    els.countArchived.textContent = String(c.archived);
    els.countHidden.textContent = String(c.hidden);
    const p = profile();
    const sc = p && p.sample_counts && typeof p.sample_counts === "object" ? p.sample_counts : null;
    els.countNote.textContent = sc
      ? `当前画像基于：收藏 ${num(sc.favorite)} · 归档 ${num(sc.archived)} · 不感兴趣 ${num(sc.hidden)}`
      : "";
  }

  function renderAlerts() {
    const box = els.alerts;
    box.textContent = "";
    if (!state.data) return;
    const add = (severity, text) => box.appendChild(el("div", `alert alert--${severity}`, text));
    if (!aiReady()) {
      add("high", `AI 未就绪：${aiReasonText()} 可在主页 ⚙️ 设置中配置；仍可手动编辑画像。`);
    }
    const shortfall = sampleShortfall();
    if (shortfall.length) {
      add("medium", `样本不足，暂不能生成画像（需要至少 ${minSamples()} 篇已处理样本，其中收藏/归档至少 ${Math.min(MIN_POSITIVES, minSamples())} 篇）：${shortfall.join("；")}。请先在待筛选中多收藏或标记不感兴趣。`);
    }
    const fresh = num(state.data.new_samples);
    if (profile() && fresh >= RESAMPLE_THRESHOLD) {
      add("medium", `自上次生成后新增 ${fresh} 条样本，建议重新生成。`);
    }
  }

  function renderMeta(p) {
    const meta = els.meta;
    meta.textContent = "";
    if (!p) return;
    const sourceLabel = p.source === "user" ? "✏️ 手动编辑" : "🤖 AI 生成";
    meta.appendChild(el("span", `meta-badge taste-source taste-source--${p.source === "user" ? "user" : "ai"}`, sourceLabel));
    const parts = [];
    if (p.created_at) parts.push(`生成于 ${formatDateTime(p.created_at)}`);
    if (p.model) parts.push(`模型 ${p.model}`);
    if (p.version) parts.push(`版本 ${String(p.version).slice(0, 12)}`);
    if (parts.length) meta.appendChild(el("span", "taste-meta__text", parts.join(" · ")));
  }

  function renderProfileView(p) {
    const view = els.view;
    view.textContent = "";
    if (!p) {
      const empty = el("div", "taste-empty");
      empty.appendChild(el("p", "taste-empty__title", "还没有品味画像。"));
      empty.appendChild(
        el(
          "p",
          "taste-empty__hint",
          "画像根据你的收藏/归档（正样本）与不感兴趣（负样本）由 AI 归纳；生成后可为待筛选论文打 0–100 的匹配分。也可以点击「编辑」手动写一份。"
        )
      );
      view.appendChild(empty);
      return;
    }
    const summary = typeof p.summary === "string" ? p.summary.trim() : "";
    if (summary) {
      const block = el("div", "taste-summary");
      block.appendChild(el("h4", "", "总体描述"));
      summary.split(/\n+/).forEach((para) => {
        if (para.trim()) block.appendChild(el("p", "", para.trim()));
      });
      view.appendChild(block);
    }
    const grid = el("div", "report-grid taste-grid");
    SECTIONS.forEach((section) => {
      const card = el("div", `report-card taste-card taste-card--${section.key}`);
      card.appendChild(el("h4", "", section.label));
      const items = strList(p[section.key]);
      if (!items.length) {
        card.appendChild(el("p", "report-summary", "（暂无）"));
      } else {
        const list = el("ul", "report-insights");
        items.forEach((text) => list.appendChild(el("li", "report-insight", text)));
        card.appendChild(list);
      }
      grid.appendChild(card);
    });
    view.appendChild(grid);
  }

  function renderEditor(p) {
    const form = els.editor;
    form.textContent = "";
    const source = p || {};
    const makeField = (key, label, hint, value, rows) => {
      const wrap = el("label", "taste-field");
      const head = el("span", "taste-field__label", label);
      if (hint) head.appendChild(el("small", "", `（${hint}）`));
      const textarea = document.createElement("textarea");
      textarea.className = "fit-input taste-field__input";
      textarea.name = key;
      textarea.rows = rows;
      textarea.value = value;
      wrap.append(head, textarea);
      return wrap;
    };
    form.appendChild(
      makeField("summary", "总体描述", `≤ ${SUMMARY_LIMIT} 字`, typeof source.summary === "string" ? source.summary : "", 4)
    );
    SECTIONS.forEach((section) => {
      form.appendChild(
        makeField(section.key, section.label, `${section.hint}，最多 ${LIST_LIMIT} 条`, strList(source[section.key]).join("\n"), 5)
      );
    });
    form.appendChild(el("p", "taste-editor__error", ""));
    const actions = el("div", "panel-actions");
    const save = el("button", "btn btn--primary", "保存画像");
    save.type = "submit";
    const cancel = el("button", "btn btn--secondary", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", () => setEditing(false));
    actions.append(save, cancel);
    form.appendChild(actions);
  }

  function setEditing(editing) {
    state.editing = editing;
    if (editing) renderEditor(profile());
    els.editor.hidden = !editing;
    els.view.hidden = editing;
    els.btnEdit.disabled = editing;
    if (editing) {
      const first = els.editor.querySelector("textarea");
      if (first) first.focus();
    }
  }

  function renderPending() {
    const p = state.pending;
    if (!profile()) {
      els.pendingNote.textContent = "生成画像后可为待筛选论文打分。";
      return;
    }
    if (!p) {
      els.pendingNote.textContent = "";
      return;
    }
    const pending = num(p.pending);
    const total = num(p.total_inbox);
    els.pendingNote.textContent = pending
      ? `待筛选 ${total} 篇，其中 ${pending} 篇尚未按当前画像打分。`
      : `待筛选 ${total} 篇均已按当前画像打分。`;
  }

  function syncButtons() {
    const ready = aiReady();
    const enough = sampleShortfall().length === 0;
    const generating = state.activeJobs.has("taste_profile");
    const scoring = state.activeJobs.has("taste_score");
    const hasProfile = Boolean(profile());

    els.btnGenerate.textContent = generating ? "生成中..." : hasProfile ? "重新生成画像" : "生成画像";
    els.btnGenerate.disabled = !state.data || generating || !ready || !enough;
    els.btnGenerate.title = !ready ? `AI 未就绪：${aiReasonText()}` : !enough ? "样本不足" : "根据收藏/归档与不感兴趣样本让 AI 归纳品味画像";

    els.btnScore.textContent = scoring ? "打分中..." : "为待筛选打分";
    els.btnScore.disabled = !state.data || scoring || !ready || !hasProfile;
    els.btnScore.title = !hasProfile ? "请先生成画像" : !ready ? `AI 未就绪：${aiReasonText()}` : "按当前画像为待筛选论文打 0–100 分";

    els.btnEdit.disabled = !state.data || state.editing;
    els.btnEdit.textContent = hasProfile ? "编辑" : "手动编写";
  }

  function render() {
    const p = profile();
    renderCounts();
    renderAlerts();
    renderMeta(p);
    if (!state.editing) renderProfileView(p);
    renderPending();
    syncButtons();
  }

  // ---------- 数据加载 ----------

  async function loadPending() {
    try {
      const res = await fetch("/api/taste_score/pending", { cache: "no-store" });
      state.pending = res.ok ? await readJson(res) : null;
    } catch (_) {
      state.pending = null;
    }
    return state.pending;
  }

  async function load() {
    setStatus("加载中...");
    try {
      const res = await fetch("/api/taste_profile", { cache: "no-store" });
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.message || data.error || `HTTP ${res.status}`);
      state.data = data && typeof data === "object" ? data : {};
      await loadPending();
      setStatus("");
    } catch (error) {
      state.data = null;
      setStatus(`无法读取品味画像：${error.message || "请确认本地服务正在运行"}`);
    }
    render();
  }

  // ---------- 后台任务 ----------

  function jobMessage(job) {
    if (!job) return "";
    const result = job.result || {};
    const text = job.message || job.error || result.message || result.error || "";
    return typeof text === "string" ? text : String(text);
  }

  function isLockBusy(job) {
    return Boolean(job && job.result && job.result.error === "lock_busy");
  }

  function jobSummary(job) {
    const result = (job && job.result) || {};
    if (job.kind === "taste_score") {
      const parts = [`已打分 ${num(result.scored)} 篇`];
      if (num(result.failed)) parts.push(`${num(result.failed)} 篇失败`);
      if (num(result.skipped)) parts.push(`${num(result.skipped)} 篇跳过`);
      return parts.join("，") + "。";
    }
    return result.message || "";
  }

  function renderJob(job) {
    const box = els.jobStatus;
    box.textContent = "";
    if (!job) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const label = JOB_LABELS[job.kind] || "后台任务";
    const running = job.status === "queued" || job.status === "running";
    const result = job.result || {};
    // 任务成功但流程跳过（AI 未就绪 / 样本不足）：按提示样式显示。
    const skipped = job.status === "succeeded" && result.status === "skipped";
    const flowError = job.status === "succeeded" && result.status === "error";
    const visual = running
      ? "running"
      : skipped
        ? "partial_failed"
        : flowError
          ? "failed"
          : job.status;
    box.className = `job-status job-status--${visual}`;

    const line = el("div", "job-status__line");
    let text;
    if (job.status === "queued") text = `${label}：已排队…`;
    else if (job.status === "running") {
      const progress = Number.isFinite(job.progress) ? `（${job.progress}%）` : "…";
      const extra = job.message && job.message !== "任务正在执行" ? ` · ${job.message}` : "";
      text = `${label}：进行中${progress}${extra}`;
    } else if (skipped) text = `${label}：已跳过。${result.message || ""}`;
    else if (flowError) text = `${label}：失败。${result.message || jobMessage(job)}`;
    else if (job.status === "succeeded") text = `${label}：完成。${jobSummary(job)}`;
    else if (job.status === "partial_failed") text = `${label}：部分完成。${jobSummary(job)} 已保留成功的结果。`;
    else if (isLockBusy(job)) text = `${label}：另一个任务正在运行，请稍后再试。${jobMessage(job)}`;
    else text = `${label}：失败。${jobMessage(job)} 原有数据保持不变。`;
    line.appendChild(el("span", "", text));
    if (!running) {
      const close = el("button", "job-status__close", "✕");
      close.type = "button";
      close.setAttribute("aria-label", "关闭任务状态");
      close.addEventListener("click", () => renderJob(null));
      line.appendChild(close);
    }
    box.appendChild(line);

    if (running && Number.isFinite(job.progress)) {
      const progress = document.createElement("progress");
      progress.className = "job-status__progress";
      progress.max = 100;
      progress.value = Math.max(0, Math.min(100, job.progress));
      box.appendChild(progress);
    }

    const errors = Array.isArray(result.errors) ? result.errors.map(String).filter(Boolean) : [];
    if (!running && errors.length) {
      const more = el("details", "job-status__details");
      more.appendChild(el("summary", "", `查看错误详情（${errors.length}）`));
      const list = el("ul");
      errors.slice(0, 20).forEach((detail) => list.appendChild(el("li", "", detail)));
      more.appendChild(list);
      box.appendChild(more);
    }
  }

  async function pollJob(job) {
    const kind = job.kind;
    state.activeJobs.add(kind);
    syncButtons();
    const startedAt = Date.now();
    let delay = 800;
    let statusErrors = 0;
    try {
      while (job.status === "queued" || job.status === "running") {
        renderJob(job);
        if (Date.now() - startedAt > JOB_POLL_TIMEOUT_MS) {
          renderJob({ ...job, status: "timeout", message: "等待超时，任务可能仍在后台运行；稍后刷新页面查看结果。" });
          return null;
        }
        await sleep(delay);
        delay = Math.min(delay * 1.25, 4000);
        try {
          const res = await fetch(`/api/jobs/${encodeURIComponent(job.id)}`, { cache: "no-store" });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          job = { kind, ...(await res.json()) };
          statusErrors = 0;
        } catch (error) {
          statusErrors += 1;
          if (statusErrors >= JOB_MAX_STATUS_ERRORS) throw new Error(`无法读取任务状态（${error.message}）`);
        }
      }
      renderJob(job);
      return job;
    } catch (error) {
      renderJob({ ...job, status: "failed", message: error.message, result: {} });
      return null;
    } finally {
      state.activeJobs.delete(kind);
      // 画像或分数可能已变化：重新读取（保持当前编辑状态）。
      await load();
    }
  }

  async function startJob(kind, endpoint, body) {
    if (state.activeJobs.has(kind)) return null;
    state.activeJobs.add(kind);
    syncButtons();
    let payload = {};
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {})
      });
      payload = await readJson(res);
      if (res.status !== 202 || !payload.job) {
        throw new Error(payload.message || payload.error || `无法启动后台任务（HTTP ${res.status}）`);
      }
    } catch (error) {
      state.activeJobs.delete(kind);
      syncButtons();
      renderJob({ kind, status: "failed", message: error.message, result: {} });
      return null;
    }
    state.activeJobs.delete(kind);
    if (payload.duplicate) {
      // 同类任务已在运行：请求被合并到现有任务（例如打分进行中又点了重新打分）。
      setStatus(`${JOB_LABELS[kind]}已在进行中，本次请求已合并到正在运行的任务${body && body.rescore ? "（不会额外重新打分，完成后可再试）" : ""}。`);
    }
    return pollJob({ kind, ...payload.job });
  }

  async function resumeRunningJobs() {
    try {
      const res = await fetch("/api/jobs", { cache: "no-store" });
      if (!res.ok) return;
      const payload = await readJson(res);
      const seen = new Set();
      (Array.isArray(payload.jobs) ? payload.jobs : []).forEach((job) => {
        if (!job || !JOB_LABELS[job.kind] || seen.has(job.kind)) return;
        seen.add(job.kind);
        if ((job.status === "queued" || job.status === "running") && !state.activeJobs.has(job.kind)) {
          pollJob(job);
        }
      });
    } catch (_) {
      /* 旧服务器没有 /api/jobs */
    }
  }

  // ---------- 操作 ----------

  function onGenerate() {
    if (!aiReady()) {
      setStatus(`AI 未就绪：${aiReasonText()}`);
      return;
    }
    if (sampleShortfall().length) {
      setStatus("样本不足，暂不能生成画像。");
      return;
    }
    const c = counts();
    const verb = profile() ? "重新生成" : "生成";
    const note = profile() && profile().source === "user" ? "\n当前手动编辑的画像会保留在历史中，新画像将成为当前画像。" : "";
    if (
      !window.confirm(
        `将根据 ${c.favorite + c.archived} 篇收藏/归档与 ${c.hidden} 篇不感兴趣样本${verb}品味画像（调用一次 AI）。${note}\n继续吗？`
      )
    ) {
      return;
    }
    startJob("taste_profile", "/api/taste_profile", {});
  }

  async function onScore() {
    if (!profile()) {
      setStatus("请先生成品味画像。");
      return;
    }
    if (!aiReady()) {
      setStatus(`AI 未就绪：${aiReasonText()}`);
      return;
    }
    const pending = await loadPending();
    renderPending();
    const count = num(pending && pending.pending);
    const total = num(pending && pending.total_inbox);
    let rescore = false;
    if (pending && !count) {
      if (!total) {
        setStatus("待筛选为空，无需打分。");
        return;
      }
      if (!window.confirm(`待筛选的 ${total} 篇都已按当前画像打分。要全部重新打分吗？（会调用 AI）`)) return;
      rescore = true;
    } else {
      const label = pending ? `${count} 篇` : "尚未打分的";
      if (!window.confirm(`将按当前画像为待筛选中${label}论文打分（调用 AI，按批处理）。继续吗？`)) return;
    }
    startJob("taste_score", "/api/taste_score", rescore ? { rescore: true } : {});
  }

  function readEditor() {
    const form = els.editor;
    const value = (name) => {
      const field = form.querySelector(`[name="${name}"]`);
      return field ? field.value : "";
    };
    const out = { summary: value("summary").trim().slice(0, SUMMARY_LIMIT) };
    SECTIONS.forEach((section) => {
      out[section.key] = value(section.key)
        .split(/\r?\n/)
        .map((line) => line.replace(/^\s*(?:[-*•·]|\d+[.)、])\s*/, "").trim())
        .filter(Boolean)
        .slice(0, LIST_LIMIT);
    });
    return out;
  }

  async function onSave(event) {
    event.preventDefault();
    const errorBox = els.editor.querySelector(".taste-editor__error");
    const showError = (text) => {
      if (errorBox) errorBox.textContent = text || "";
    };
    const payload = readEditor();
    const empty = !payload.summary && SECTIONS.every((section) => !payload[section.key].length);
    if (empty) {
      showError("画像内容不能全部为空。");
      return;
    }
    const saveBtn = els.editor.querySelector('button[type="submit"]');
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "保存中...";
    }
    showError("");
    try {
      const res = await fetch("/api/taste_profile/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: payload })
      });
      const data = await readJson(res);
      if (!res.ok || data.status !== "ok") {
        showError(`保存失败：${data.message || data.error || `HTTP ${res.status}`}`);
        return;
      }
      setEditing(false);
      setStatus("画像已保存。画像变化后，原有打分会被视为过期，可重新为待筛选打分。");
      await load();
    } catch (error) {
      showError(`保存失败：${error.message || "网络错误"}`);
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = "保存画像";
      }
    }
  }

  // ---------- 初始化 ----------

  let initialized = false;

  function init() {
    if (initialized) return Promise.resolve();
    initialized = true;
    Object.assign(els, {
      status: $("tasteStatus"),
      alerts: $("tasteAlerts"),
      meta: $("tasteMeta"),
      view: $("tasteProfileView"),
      editor: $("tasteEditor"),
      jobStatus: $("tasteJobStatus"),
      btnGenerate: $("btnTasteGenerate"),
      btnEdit: $("btnTasteEdit"),
      btnScore: $("btnTasteScore"),
      btnReload: $("btnTasteReload"),
      pendingNote: $("tastePendingNote"),
      countFavorite: $("tasteCountFavorite"),
      countArchived: $("tasteCountArchived"),
      countHidden: $("tasteCountHidden"),
      countNote: $("tasteCountNote")
    });
    if (!els.view || !els.editor) return Promise.resolve();
    els.btnGenerate.addEventListener("click", onGenerate);
    els.btnScore.addEventListener("click", onScore);
    els.btnEdit.addEventListener("click", () => setEditing(true));
    if (els.btnReload) els.btnReload.addEventListener("click", load);
    els.editor.addEventListener("submit", onSave);
    return load().then(resumeRunningJobs);
  }

  window.InsightsTaste = { init, reload: load };
})();
