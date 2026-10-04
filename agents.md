# Paper Feed AI 功能技术文档

## 版本信息
**文档版本**: 2.6
**最后更新**: 2026-10-04
**项目**: Paper Feed - 学术论文 RSS 订阅系统

> 2.6 变更要点：数据真相源已迁移到 SQLite（`data/paper_feed.sqlite3`，经 `paper_feed/` 包访问），所有持久化以稳定 `paper_id` 为键；`feed.json` / `filtered_feed.xml` / `translations.json` 仅为兼容导出或缓存。新增关键词编辑器、期刊目录、已隐藏视图、导航栏、`OPENAI_MODEL`、端口配置与备份命令。更完整的维护边界见 `DEV_CONTEXT.md`。

## 1. 核心功能：AI 深度分类与分析

本系统通过集成 OpenAI 兼容模型（`OPENAI_MODEL`，默认 `gpt-4o-mini`，也可用 DeepSeek 等兼容端点），为每篇论文提供三个维度的智能分析：
1. **中文翻译**: 学术风格的标题翻译。
2. **研究方法分类**: 识别论文采用的主要研究范式（如实验、实证、理论等）。
3. **核心话题分类**: 基于定制化的商科/消费者行为话题（如 AI、CSR、情绪等）。

### 1.1 数据存储

**SQLite 是唯一真相源**：默认 `data/paper_feed.sqlite3`，可用 `PAPER_FEED_DB` 覆盖。代码通过 `paper_feed/` 包访问：

| 模块 | 职责 |
| --- | --- |
| `paper_feed/db.py` | schema、迁移、`PaperRepository`（稳定 `paper_id` 解析与标识符认领） |
| `paper_feed/identity.py` | DOI / URL / 来源标识规范化，生成 `paper_id` |
| `paper_feed/ingestion.py` | RSS 抓取结果入库；`ensure_database` 在无库时（如 CI）从已提交导出引导 |
| `paper_feed/importer.py` | 旧版 JSON 状态导入（`python -m paper_feed import-legacy`） |
| `paper_feed/service.py` | `PaperFeedService`：按 `paper_id` 列表/查询、分流、保存摘要与分类修正 |
| `paper_feed/exporter.py` | 从数据库投影 `filtered_feed.xml` 与 `web/feed.json` |
| `paper_feed/publish_guard.py` | 自动化发布前的导出完整性检查 |
| `paper_feed/cli.py` | 统一命令行 `python -m paper_feed <命令>`（见 §4.0） |

主要表：`papers`、`paper_identifiers`、`paper_observations`（每次 RSS 观察）、`paper_review_state`（`inbox`/`favorite`/`archived`/`hidden`）、`paper_review_events`、`paper_analyses`（`analysis_kind` = `translation` / `abstract`）、`paper_user_overrides`（如 `user_correction`）、`fetch_runs`、`source_fetches`、`migration_unresolved`。

**兼容导出**：`web/feed.json` 仍包含 `id`, `paper_id`, `title`, `title_zh`, `method`, `topic`, `summary`, `abstract`, `raw_abstract`, `abstract_source` 等字段，但它是数据库的投影，不是数据源。其中 `summary` 仅保留 RSS 元数据（Publication date / Source / Authors），**不保存摘要正文**。`web/translations.json`、`web/abstracts.json`、`web/interactions.json` 只是旧版遗留/兼容文件，新代码不得把它们当作持久化位置。

翻译与分类结果按 `paper_id` 存入 `paper_analyses`（`analysis_kind='translation'`），payload 形如：
```json
{
  "zh": "中文翻译",
  "method": "Experiment",
  "topic": "AI & Tech",
  "classification_version": "..."
}
```

### 1.2 分类体系定义
分类体系定义在 `web/categories.json`，经 `GET/POST /api/categories` 读写；变更分类版本会使旧分析在下次分析时被视为过期。

## 2. 核心逻辑流程

### 2.1 RSS 抓取与关键词过滤 (`run_rss_flow`)
并发抓取 `journals.dat`（或 `RSS_JOURNALS`）中的源，用 `match_entry` 按关键词规则过滤，经 `ingest_fetch_results` 写入 SQLite，再分析缺失/过期的翻译并导出。关键词只在入库时过滤**新抓取**的条目，不会回溯删除已入库论文。全部源失败时不更新导出。

**关键词语法**（`keywords.dat` / `RSS_KEYWORDS` / `/api/keywords`）：每行一条规则，行间为 OR；`AND`（不区分大小写的整词）连接多个词；以 `-` 或 `NOT ` 开头的词为排除；匹配不区分大小写，在词首加词边界（正则 `\bterm`，故 `ai` 不匹配 said，`consum` 仍匹配 consumer）；`#` 开头为注释行；支持引号包裹的多词短语。

### 2.2 批量分析 (`batch_analyze_papers` / `analyze_database_items`)
仅对缺少翻译或 `classification_version` 过期的论文按标题批量调用模型，结果按 `paper_id` 保存。未配置 API Key 时直接跳过。

### 2.3 重新分析 (`run_reanalysis_flow`)
对数据库内全部论文重新分析并重新导出；不受当前关键词影响。

### 2.4 标签清洗 (`strip_tags`)
清除 RSS 摘要中的 HTML 标签，`extract_metadata_summary` 只保留元数据行。

## 3. 前端交互设计

### 3.1 视图与筛选
*   **四个视图**: 待筛选（inbox）/ 收藏（favorite）/ 归档（archived）/ 已隐藏（hidden），对应 `GET /api/papers?view=...`。已隐藏视图可恢复（unhide）。
*   **收件箱逻辑**: 分流后的文章从“待筛选”移出，进入相应视图。
*   **Method/Topic Filter**: 多选下拉，支持“全部方法/全部主题”；选择“全部”会自动取消其他选项。
*   **空状态文案**: 待筛选为空时提示“暂时没有新的文献了...”。
*   **导航栏**: `web/nav.js` 为 Feed、洞察、期刊管理提供统一导航。

### 3.2 视觉增强
*   **Badge System**: 渲染 Method/Topic/Source 徽章。
*   **Source Badges**（`abstract_source`）:
    *   📚 **Crossref** / 🔬 **Semantic Scholar**: 外部抓取。
    *   🤖 **AI 生成**（`gpt_generated`，仅基于标题的推测）/ **AI 总结**（`gpt_summarized`，基于摘要）。
    *   ✏️ **用户补充**（`user_provided`）: 用户手动编辑的内容。

### 3.3 交互优化
*   **刷卡与快捷键**: 刷卡模式 `→` 收藏、`←` 隐藏、`A` 归档、`Z` 撤销；列表模式 `F`/`L`/`X`/`O`；按 `?` 显示快捷键说明浮层。
*   **主题云**: 仅在“我的收藏”视图展示，用于快速过滤关注主题。
*   ✨ **生成 AI 总结按钮**: 仅在“我的收藏”视图显示，按需触发，节省 Token。
*   ✏️ **补充/编辑摘要**: 点击卡片右上角铅笔图标手动粘贴原文；优先预填 `raw_abstract`，若是 AI 编造内容则清空。
*   ⚙️ **设置**: 编辑 API Key、Base URL、`OPENAI_MODEL`、代理。
*   🔑 **关键词编辑器**: 编辑 `keywords.dat`，保存前可预览对已入库论文的命中数和示例。
*   **后台任务**: 抓取/分析/总结以 job 运行；页面重载后通过 `GET /api/jobs` 找回进行中的任务；`partial_failed` 表示部分条目失败。

### 3.4 洞察页（偏好报告 + 期刊统计）
*   **入口**: 导航栏“🔎 洞察”，页面 `web/insights.html`（`insights.js` 负责标签切换与 hash 路由：`#prefs`、`#journals`、`#journals/detail?journal=`、`#fit`、`#fit/match`）。`report.js` / `stats.js` 作为模块分别暴露 `window.InsightsReport` / `window.InsightsStats`，各标签首次激活时才加载数据。`report.html` / `stats.html` 仅为跳转页。
*   **报告内容**: 基于收藏/隐藏标题的偏好词、偏好短语、样本数量与缺失链接提示；来源/期刊偏好提供 lift 与缺失覆盖提示。
*   **筛选跳转**: “筛选”按钮跳转至 `index.html?journal=...&view=...&from=insights`（或 `source=` / `q=`），主页显示“← 返回洞察”。
*   **触发逻辑**: “重新计算”(POST) 生成报告，“重新载入”(GET) 读取；页面打开时只 GET，不会自动生成。

### 3.5 期刊管理
*   `web/journals.html`：管理订阅列表；可从期刊目录（`RSS list.md` 解析，按学科分组）勾选添加。保存时去除 `utm_*` 跟踪参数。

## 4.0 命令行入口 (`paper_feed/cli.py`)

唯一的命令行入口是 `python -m paper_feed <命令>`（argparse 子命令，中英文帮助；不带命令只打印帮助）。`get_RSS`/`server` 在命令处理函数内**延迟导入**，避免循环依赖，也让 `--help` / `doctor` 在缺依赖时仍可运行。

| 命令 | 实现 |
| --- | --- |
| `refresh` | `get_RSS.run_rss_flow` + `cli.refresh_exit_code`（0 已发布 / 1 全部源失败 / 2 配置为空） |
| `serve [--port] [--host] [--open]` | `server.run_server(port, host, on_ready)` |
| `start` / `run` | `cli._start`：`probe_port` 探测 `/api/interactions`；已有 Paper Feed 则只打开浏览器，其他程序占用则退出 1；`run` 先 refresh（失败仍打开）并在配置密钥时提示费用 |
| `reanalyze` / `summarize-favorites` | 计数（`get_RSS.stale_analysis_items` / `get_RSS.pending_summary_items`）→ 确认（`--yes`）→ `run_reanalysis_flow` / `summarize_specific_papers`；`--dry-run` 只计数 |
| `keywords show` / `keywords preview` | 规则来源与 `get_RSS.load_config` 相同；预览调用 `server.keyword_preview`（SQLite） |
| `doctor` | `cli.run_doctor`：Python、依赖、httpx `proxy=`、配置来源（不打印密钥）、期刊/关键词、数据库 `integrity_check` 与各状态计数、端口、`web/` 资源；有 FAIL 时退出 1 |
| `backup` / `import-legacy` / `publish-guard` | `paper_feed.backup` / `paper_feed.importer` / `paper_feed.publish_guard.run` |

兼容外壳：`python get_RSS.py` → `refresh`，`python server.py` → `serve`，`python -m paper_feed.publish_guard` 保留；`run_web.bat` / `run_web.sh` 分别调用 `-m paper_feed run|start --port ...`。新增命令行功能请加在 `cli.py` 并补 `tests/test_cli.py`。

## 4. API 接口 (`server.py`)

服务仅绑定 `127.0.0.1`，端口默认 8000，可用 `PAPER_FEED_PORT` 或 `--port` 修改；无认证，禁止公网暴露。

*   `GET /api/papers?view=inbox|favorite|archived|hidden|all`: 按视图列出论文。
*   `GET /api/papers/<paper_id>`: 单篇论文。
*   `POST /api/papers/<paper_id>/review`: 分流动作 like/unlike/archive/unarchive/hide/unhide。
*   `GET /api/config`: 获取配置（密钥脱敏，返回 `has_api_key`、`OPENAI_MODEL` 等）。
*   `POST /api/save_config`: 保存配置（含 `OPENAI_MODEL`）。优先级：非空环境变量 > config.json 非空非占位值 > 默认值。
*   `GET /api/keywords` / `POST /api/keywords`: 读写 `keywords.dat`，返回 `{text, keywords}`。
*   `POST /api/keywords/preview`: 用与入库相同的匹配器预览规则在已入库论文中的命中情况。
*   `GET /api/journals` / `POST /api/journals`: 读写订阅列表。
*   `GET /api/journal_catalog`: 期刊目录（`name`, `url`, `subject`, `tags`, `subscribed`）。
*   `POST /api/fetch`: 后台 job：RSS 抓取 + 标题翻译/分类（**不自动抓取摘要/总结**）。
*   `POST /api/reanalyze`: 后台 job：标题 AI 重新分析。
*   `POST /api/summarize_favorites`: 后台 job：对收藏按需生成 AI 总结。
*   `GET /api/jobs` / `GET /api/jobs/<job_id>`: 最近任务列表 / 单个任务状态（可能为 `partial_failed`，结果含 `failed` 与 `errors`）。
*   `POST /api/update_abstract`: 按 `paper_id` 保存用户补充摘要。
*   `POST /api/update_classification`: 按 `paper_id` 保存用户分类修正。
*   `GET/POST /api/categories`: 读写分类体系。
*   `GET /api/preference_report` / `POST /api/preference_report`: 读取 / 生成偏好报告。
*   `GET /api/export_favorites_ris`: 导出收藏为 RIS。
*   `GET/POST /api/interactions`: 旧版兼容的收藏/归档/隐藏数组接口（新代码使用 review 接口）。

## 5. 摘要获取与总结策略

系统采用 **“延迟加载/按需触发”** 策略:
1.  **日常更新阶段**: 关闭摘要抓取与落盘。仅通过标题进行翻译和分类；RSS 的 `summary` 仅保留元数据行。
2.  **按需总结阶段**: 用户点击“生成 AI 总结”时：
    *   **已有摘要**: 若数据库已有原始摘要（含用户手动补充），AI 基于摘要总结 (`gpt_summarized`)。
    *   **无摘要**: AI 基于标题生成研究方向预测 (`gpt_generated`)。
    *   **外部抓取**: 仅作为可选后端辅助逻辑，默认不在 RSS 流程中使用。

## 6. 后期更新指导

### 6.1 功能清单与对应逻辑位置
*   **RSS 抓取**: `get_RSS.py` -> `run_rss_flow`（入库：`paper_feed.ingestion.ingest_fetch_results`）。
*   **关键词匹配**: `get_RSS.py` -> `match_entry`（入库过滤与 `/api/keywords/preview` 共用）。
*   **标题分析**: `get_RSS.py` -> `analyze_database_items` 调用 `batch_analyze_papers`，结果经 `paper_feed.ingestion.save_translations` 按 `paper_id` 写入。
*   **按需总结**: `get_RSS.py` -> `summarize_specific_papers`，结果经 `save_abstracts` 写入 `paper_analyses`。
*   **摘要编辑**: `server.py` -> `/api/update_abstract` -> `PaperFeedService.save_abstract(paper_id, ...)`（`paper_analyses`, `analysis_kind='abstract'`, `source='user_provided'`）。
*   **分流**: `PaperFeedService.review(paper_id, action)` 写 `paper_review_state` 与 `paper_review_events`。
*   **兼容导出**: `paper_feed.exporter`（`filtered_feed.xml`、`web/feed.json`）。
*   **命令行**: `paper_feed/cli.py`（`python -m paper_feed --help`）；备份 / 旧数据导入为 `backup` / `import-legacy` 子命令，环境自检为 `doctor`。
*   **偏好报告**: `server.py` -> `generate_title_report` 写入 `web/preference_report.json`；前端 `web/report.js` 在 `web/insights.html#prefs` 渲染。
*   **来源/期刊筛选跳转**: `web/app.js` -> `applyUrlFilters` 处理 `journal/source/q` 查询参数。
*   **前端渲染**: `web/app.js` -> `renderList`。
*   **调试页面**: `dev/`（不随 `web/` 发布）。

### 6.2 修改注意点
*   **ID 匹配**: 所有持久化与新 API 一律使用稳定 `paper_id`。旧 RSS `id`、链接、标题只能经 `PaperFeedService.resolve_reference` 在兼容层解析，不得作为新的存储键。
*   **素材优先**: 编辑框应总是优先呈现 `raw_abstract`（原始素材），而非 AI 润色后的中文结果。
*   **摘要落盘**: RSS 抓取不写入摘要正文；摘要正文只来自用户补充或 AI 总结，并存于 SQLite。
*   **导出不是数据源**: 不要直接修改 `web/feed.json` / `filtered_feed.xml` 来改数据；改数据库后重新导出。
*   **密钥**: 不要把真实密钥写入源码、测试、文档、日志或提交记录。
*   **性能**: `renderList` 使用 `DocumentFragment` 批量插入，避免在处理上千条数据时出现 UI 卡顿。
