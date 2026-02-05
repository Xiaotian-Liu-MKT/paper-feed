# Paper Feed AI 功能技术文档

## 版本信息
**文档版本**: 2.5
**最后更新**: 2026-01-11
**项目**: Paper Feed - 学术论文 RSS 订阅系统

## 1. 核心功能：AI 深度分类与分析

本系统通过集成 GPT 模型，为每篇论文提供三个维度的智能分析：
1. **中文翻译**: 学术风格的标题翻译。
2. **研究方法分类**: 识别论文采用的主要研究范式（如实验、实证、理论等）。
3. **核心话题分类**: 基于定制化的商科/消费者行为话题（如 AI、CSR、情绪等）。

### 1.1 数据结构升级

`feed.json` 包含 `id`, `title`, `title_zh`, `method`, `topic`, `summary`, `abstract`, `raw_abstract`, `abstract_source` 等核心字段。
其中 `summary` 仅保留 RSS 元数据（Publication date / Source / Authors），**不保存摘要正文**。

`translations.json` 缓存结构:
```json
{
  "Paper Title": {
    "zh": "中文翻译",
    "method": "Experiment",
    "topic": "AI & Tech"
  }
}
```

### 1.2 分类体系定义
(保持不变...)

## 2. 核心逻辑流程

### 2.1 批量分析 (`batch_analyze_papers`)
(保持不变...)

### 2.2 增量更新与修补 (`run_reanalysis_flow`)
(保持不变...)

### 2.3 标签清洗 (`strip_tags`)
(保持不变...)

## 3. 前端交互设计

### 3.1 筛选器
*   **Method/Topic Filter**: 多选下拉，支持“全部方法/全部主题”；选择“全部”会自动取消其他选项。
*   **收藏/待筛选切换**: 采用“收件箱”逻辑，收藏后的文章会从“待筛选”列表中移出，进入“我的收藏”。
*   **空状态文案**: 待筛选为空时提示“暂时没有新的文献了...”。

### 3.2 视觉增强
*   **Badge System**: 渲染 Method/Topic/Source 徽章。
*   **Source Badges**:
    *   📚 **Crossref** / 🔬 **Semantic Scholar**: 外部抓取。
    *   🤖 **AI 生成** / **AI 总结**: GPT 处理结果。
    *   ✏️ **用户补充**: 用户手动编辑的内容。

### 3.3 交互优化
*   **已移除 NEW 标记与“标记已读”按钮**: 不再基于本地时间戳区分新旧文章。
*   **主题云**: 仅在“我的收藏”视图展示，用于快速过滤关注主题。
*   ✨ **生成 AI 总结按钮**: 仅在“我的收藏”视图显示。允许用户对感兴趣的文章按需触发总结，节省 Token 并加快日常更新速度。
*   ✏️ **补充/编辑摘要**: 点击卡片右上角铅笔图标，可手动粘贴原文。支持智能预填（优先显示原始英文，若是 AI 编造的内容则自动清空）。

### 3.4 偏好报告
*   **入口按钮**: 主页 header 增加“偏好报告”按钮，跳转到 `web/report.html`。
*   **报告页面**: 展示基于标题的偏好词、偏好短语、样本数量与缺失链接提示。
*   **来源/期刊偏好**: 统计收藏/隐藏中的来源与期刊偏好，提供 lift 与缺失覆盖提示。
*   **筛选跳转**: 报告中来源/期刊提供“筛选”按钮，跳转至 `index.html?journal=...` 或 `index.html?source=...`。
*   **触发逻辑**: 页面提供“生成报告 / 刷新”按钮，调用后端生成并读取报告。

## 4. API 接口 (`server.py`)

*   `GET /api/config`: 获取当前配置。
*   `POST /api/save_config`: 保存配置。
*   `POST /api/fetch`: 触发 RSS 抓取 + 标题翻译/分类（**不再自动抓取摘要/总结**）。
*   `POST /api/summarize_favorites`: 对收藏夹内的文章按需生成 AI 总结。
*   `GET /api/preference_report`: 读取偏好报告。
*   `POST /api/preference_report`: 生成并返回偏好报告（基于标题）。
*   `POST /api/update_abstract`: 保存用户手动补充的摘要内容。
*   `POST /api/reanalyze`: 触发标题 AI 重新分析。
*   `GET/POST /api/interactions`: 读写收藏/隐藏数据。

## 5. 摘要获取与总结策略 (策略调整)

系统采用 **“延迟加载/按需触发”** 策略:
1.  **日常更新阶段**: 彻底关闭摘要抓取与落盘。仅通过标题进行翻译和分类；RSS 的 `summary` 仅保留元数据行，不保存摘要正文。
2.  **按需总结阶段**: 用户点击“生成 AI 总结”时，系统按以下优先级处理：
    *   **已有摘要**: 若缓存已有原始摘要（含用户手动补充），AI 基于摘要进行总结 (`gpt_summarized`)。
    *   **无摘要**: AI 直接基于标题生成研究方向预测 (`gpt_generated`)。
    *   **外部抓取**: 仅作为可选后端辅助逻辑，默认不在 RSS 流程中使用。

## 6. 后期更新指导

### 6.1 功能清单与对应逻辑位置
*   **RSS 抓取**: `get_RSS.py` -> `run_rss_flow`
*   **标题分析**: `get_RSS.py` -> `write_feed_json` 调用 `batch_analyze_papers`。
*   **按需总结**: `get_RSS.py` -> `summarize_specific_papers`。
*   **摘要编辑**: `server.py` -> `/api/update_abstract` 写入 `web/abstracts.json`。
*   **偏好报告**: `server.py` -> `generate_title_report` 写入 `web/preference_report.json`；前端 `web/report.js` 渲染。
*   **来源/期刊筛选跳转**: `web/app.js` -> `applyUrlFilters` 处理 `journal/source/q` 查询参数。
*   **前端渲染**: `web/app.js` -> `renderList`。

### 6.2 修改注意点
*   **ID 匹配**: 系统使用论文的 `id` 字段作为摘要缓存的 Key。保存摘要时必须确保前端传递的是正确的 `id`。
*   **素材优先**: 编辑框应总是优先呈现 `raw_abstract` (原始素材)，而非 AI 润色后的中文结果。
*   **摘要落盘**: RSS 抓取不写入摘要正文；`summary` 仅为元数据，摘要正文只来自用户补充或 AI 总结。
*   **性能**: `renderList` 使用 `DocumentFragment` 批量插入，避免在处理上千条数据时出现 UI 卡顿。
