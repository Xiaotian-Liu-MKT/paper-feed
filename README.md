# Paper Feed - 学术论文 RSS 订阅系统

一个 browser-first 的本地学术论文 RSS 筛选器：抓取期刊 RSS、按关键词过滤，并在浏览器中完成检索、刷卡分流、收藏、偏好分析和 RIS 导出。

## 主要功能

- **多源聚合**：从多个学术期刊 RSS 源获取最新论文。
- **智能过滤**：基于自定义关键词筛选相关论文。
- **双语与 AI 功能**：可选的 OpenAI 标题翻译、分类和收藏论文总结。
- **现代化 Web 界面**：关键词、期刊和摘要搜索；日期与期刊筛选；收藏、归档、隐藏；收件箱刷卡和撤销；收藏 RIS 导出。
- **本地优先数据**：SQLite 保存论文、状态和分析结果；兼容保留 RSS/XML/JSON 导出。

## 系统要求与安装

- 推荐 Python 3.11（当前依赖也已在 Python 3.13 验证）。
- 仅在刷新 RSS 或调用 OpenAI 时需要互联网连接。

在 Windows 中创建项目专用虚拟环境并安装依赖：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

主要依赖包括：

- `feedparser`：RSS feed 解析
- `rfeed`：RSS feed 生成
- `openai`：可选的 OpenAI API 客户端

## 配置

### 1. 配置 RSS 源（`journals.dat`）

在 `journals.dat` 中添加要订阅的 RSS 源，每行一个 URL：

```
https://academic.oup.com/rss/site_5397/advanceAccess_3258.xml
https://journals.sagepub.com/action/showFeed?...
```

`RSS list.md` 收录了市场营销与消费者行为、社会心理、管理、决策科学、信息系统、经济学、旅游与酒店管理等期刊的 RSS 链接。

### 2. 配置关键词（`keywords.dat`）

每行一个关键词，支持 `AND` 逻辑：

```
embarrassment
social media AND marketing
consumer behavior
```

### 3. 配置 OpenAI（可选）

可在根目录（已忽略）`config.json` 或环境变量中配置 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_PROXY`。不要将真实密钥写入文档、测试、日志或提交记录。

未配置 OpenAI 仍可浏览既有数据和抓取 RSS；翻译、AI 分类和 AI 总结功能不可用。

## 使用方法

### 方式一：刷新 RSS

```powershell
.\.venv\Scripts\python.exe get_RSS.py
```

该命令会从已配置 RSS 源获取论文、按关键词过滤、导入 SQLite，并更新兼容导出。它可能联网、调用 OpenAI，并修改 `data/paper_feed.sqlite3`、`filtered_feed.xml`、`web/feed.json`、`web/translations.json` 等本地生成物。

### 方式二：启动 Web 服务器

```powershell
.\.venv\Scripts\python.exe server.py
```

服务仅监听 `http://127.0.0.1:8000`，用于浏览现有本地数据。它包含本地写入接口和后台任务，不要进行公网端口转发。

Windows 推荐使用启动器；它始终使用 `.venv\Scripts\python.exe`，并通过只读 `/api/interactions` 的 `favorites`、`archived`、`hidden` 数组结构识别已运行的 Paper Feed 服务，否则报告端口冲突：

```powershell
run_web.bat          # 默认：先刷新 RSS，再启动或打开应用
run_web.bat refresh  # 与默认行为相同的显式别名
run_web.bat start    # 不刷新 RSS，仅启动或打开已有本地数据
```

**注意：** 裸运行 `run_web.bat` 和 `run_web.bat refresh` 都可能联网、调用 OpenAI 并修改生成物；只想查看现有结果时使用 `run_web.bat start`。

Web 界面支持浏览、搜索和筛选论文，收藏/归档/隐藏及收件箱刷卡（左右方向键和 `A`/`Z` 快捷键），并可对收藏生成 AI 总结和导出 RIS。更新 RSS、重新分析和 AI 总结以后台 job 运行，前端可轮询状态，避免阻塞浏览操作。

### 订阅 RSS Feed

`filtered_feed.xml` 是 SQLite 数据的兼容 RSS 导出，不是主数据源。需要在其他 RSS 阅读器中订阅时，可将该文件部署到你控制的 Web 服务器后添加其链接。

## 数据与文件结构

SQLite 是唯一的持久化真相源，默认路径是 `data/paper_feed.sqlite3`，可通过 `PAPER_FEED_DB` 覆盖。每篇论文使用稳定 `paper_id` 关联论文、交互状态、用户修正、AI 分析与 RIS 导出；新代码不得将旧 `id`、链接或 JSON 键作为新的持久化身份。

```
paper-feed/
├── get_RSS.py              # RSS 抓取、导入与兼容导出
├── server.py               # 127.0.0.1 本地 Web/API 与后台任务
├── paper_feed/             # SQLite、论文身份、导入与服务层
├── data/paper_feed.sqlite3 # SQLite 真相源（本地生成，默认忽略）
├── journals.dat            # RSS 源列表
├── keywords.dat            # 关键词列表
├── filtered_feed.xml       # 兼容 RSS 导出
├── web/feed.json           # 兼容前端导出
├── web/                    # Web 界面
├── run_web.bat             # Windows 启动器
└── tests/                  # 单元、服务与启动器测试
```

`filtered_feed.xml`、`web/feed.json`、`web/translations.json` 和其他 `web/*.json` 本地状态/导出均不是权威数据库。提交前请确认这些生成物变更确有意图；不要提交 `config.json`、密钥、数据库、虚拟环境或本地状态。

## 工作原理

1. 从 `journals.dat` 的 RSS 源抓取论文元数据。
2. 根据 `keywords.dat` 匹配标题和摘要元数据。
3. 使用 DOI、URL、来源标识等规范化信息确定稳定 `paper_id` 并写入 SQLite。
4. 保留论文的历史观察记录和用户状态，增量更新新数据。
5. 按配置调用 OpenAI 完成翻译、分类或总结；未配置时跳过这些功能。
6. 从 SQLite 投影出 XML/JSON 兼容导出，供 RSS 阅读器或静态降级界面使用。

## 高级配置与注意事项

除 `config.json` 外，`RSS_JOURNALS`、`RSS_KEYWORDS`、`OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_PROXY` 可通过环境变量配置。`config.json` 中的值会覆盖对应 OpenAI 环境变量；项目不会自动加载 `.env`。

1. OpenAI 调用可能产生费用；刷新间隔建议至少一小时，避免给期刊站点造成压力。
2. 部分期刊可能需要机构网络或 VPN。
3. `/api/fetch`、`/api/reanalyze`、`/api/summarize_favorites` 可能联网、写入或调用 OpenAI；测试和演示时不要无意触发。
4. 本地 API 无认证，尽管服务仅绑定 loopback，仍不应公开部署。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest

# 启动器参数与行为约定
.\.venv\Scripts\python.exe -m pytest tests/test_run_web_launcher.py

# 依赖检查
.\.venv\Scripts\python.exe -m pip check
```

浏览器测试若依赖 Playwright，需要单独安装浏览器依赖；常规测试不要使用真实密钥或触发有费用的后台 job。

## 自动化部署

`.github/workflows/rss_action.yaml` 可定时或手动运行 RSS 更新。该自动化应使用受控的密钥配置，并只提交明确需要的兼容导出；本地 SQLite 数据库不是自动化提交的真相源。

## 许可证与贡献

本项目仅供学习和个人使用，请遵守期刊服务条款。欢迎提交 Issue 和 Pull Request；改动论文身份、数据库路径、端口绑定、生成物路径或后台任务时，请同步更新 `DEV_CONTEXT.md` 与相关测试。
