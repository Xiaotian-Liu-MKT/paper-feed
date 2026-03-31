# Paper Feed

一个以浏览器为主入口的学术论文 RSS 筛选器。项目会抓取期刊 RSS、按关键词过滤、用 OpenAI 做标题翻译与轻量分类，并在本地网页里完成收藏、忽略、摘要补充、AI 总结和 `RIS` 导出。

## 当前架构

- `get_RSS.py`
  - 抓 RSS
  - 关键词过滤
  - 标题翻译与分类
  - 生成 `filtered_feed.xml`、`web/feed.json`、`web/translations.json`
- `server.py`
  - 提供本地 API
  - 保存配置、交互状态、手动摘要
  - 处理收藏夹 `RIS` 导出
- `web/`
  - 浏览器主界面
  - 支持搜索、筛选、收藏、隐藏、按需 AI 总结

`Notion-first`、`automation-state`、直连 Zotero API 的实验分支已经移除；当前唯一的主流程是 `browser-first + RIS`。

## 安装

```bash
pip install -r requirements.txt
```

如果要跑测试：

```bash
pip install -r requirements-dev.txt
```

## 配置

### RSS 源

在 `journals.dat` 里配置期刊 RSS，每行一个 URL。

### 关键词

在 `keywords.dat` 里配置关键词，每行一个，支持 `AND` 逻辑，例如：

```text
embarrassment
social media AND marketing
consumer behavior
```

### OpenAI

如果要启用标题翻译、分类和按需 AI 总结，创建 `config.json`：

```json
{
  "OPENAI_API_KEY": "your-api-key-here",
  "OPENAI_BASE_URL": "",
  "OPENAI_PROXY": ""
}
```

也可以直接使用环境变量：

```bash
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=
OPENAI_PROXY=
```

不配置 OpenAI 时，RSS 抓取和网页浏览仍然可用，只是不会有 AI 翻译、分类和总结。

## 使用

### 1. 更新 feed

```bash
python get_RSS.py
```

会刷新：

- `filtered_feed.xml`
- `web/feed.json`
- `web/translations.json`
- `web/categories.json`

### 2. 启动浏览器界面

```bash
python server.py
```

然后访问 `http://localhost:8000`。

浏览器界面里可以：

- 搜索标题、期刊、摘要
- 按方法、主题、来源筛选
- 收藏或忽略论文
- 手动补充/编辑摘要
- 对收藏论文按需生成 AI 总结
- 从“我的收藏”直接导出 `RIS`

### 3. 导出到 Zotero

当前不再直连 Zotero API。  
推荐流程是：

1. 在浏览器的“我的收藏”里点 `📄 导出 RIS`
2. 下载 `paper-feed-favorites-*.ris`
3. 在 Zotero 里导入该 `RIS` 文件

导出时会跳过 `gpt_generated` 这类仅基于标题猜测出来的摘要，不把它写进 `RIS`。

## 自动化

仓库保留一个 GitHub Actions 工作流 `.github/workflows/rss_action.yaml`，每 6 小时自动运行一次：

- 安装依赖
- 运行 `python get_RSS.py`
- 提交更新后的 `filtered_feed.xml`、`web/feed.json`、`web/translations.json`、`web/categories.json`

需要的 secrets：

- `RSS_KEYWORDS`
- `OPENAI_API_KEY`，如果你希望云端也执行翻译/分类

## 主要文件

- `get_RSS.py`
- `server.py`
- `web/index.html`
- `web/app.js`
- `web/styles.css`
- `web/interactions.json`
- `web/abstracts.json`

## 注意事项

1. `config.json` 包含敏感信息，不应提交到版本库。
2. `state/` 是本地运行时目录，不参与版本控制。
3. `filtered_feed.xml`、`web/feed.json`、`web/translations.json` 都是生成物。
4. OpenAI 调用会产生费用。
