# Paper Feed 开发上下文

## 项目定位与数据边界

Paper Feed 是本地 browser-first 学术 RSS 筛选器。主线是 RSS 抓取、SQLite 持久化、浏览器端分流与 RIS 导出；不再以 JSON 文件作为数据真相源，也不直连 Zotero API。

默认数据库为 `data/paper_feed.sqlite3`，可用 `PAPER_FEED_DB` 覆盖。所有持久化实体和新 API/前端调用以稳定 `paper_id` 为键；旧 RSS `id`、链接和 JSON 键仅在兼容层解析，不能作为新存储身份。`filtered_feed.xml`、`web/feed.json`、`web/translations.json` 与其他 `web/*.json` 是兼容导出或本地状态，不是权威数据库。

## 路径优先地图

| 路径 | 职责 |
| --- | --- |
| `get_RSS.py` | RSS 抓取、关键词过滤、可选 AI 富化、SQLite 导入和兼容导出 |
| `paper_feed/db.py` | SQLite schema、稳定身份和状态迁移 |
| `paper_feed/ingestion.py` | RSS/旧状态导入 |
| `paper_feed/service.py` | 以 `paper_id` 提供论文、状态、分析和导出服务 |
| `server.py` | `127.0.0.1:8000` 静态 UI、本地 API 与后台 job 队列 |
| `web/index.html`, `web/app.js`, `web/styles.css` | 浏览器界面、刷卡/撤销、`paper_id` 交互和 job 状态轮询 |
| `run_web.bat` | Windows 启动器，固定 `.venv\Scripts\python.exe` |
| `tests/` | SQLite、服务/API、前端与启动器测试 |
| `.github/workflows/rss_action.yaml` | 定时/手动 RSS 自动化 |

## 启动、后台任务与安全

`server.py` 仅绑定 `127.0.0.1:8000`。API 无认证且包含本地写操作，禁止公网暴露或端口转发。`/api/fetch`、`/api/reanalyze`、`/api/summarize_favorites` 通过单 worker 后台 job 队列执行，前端查询 `/api/jobs/<job_id>` 获取进度；同类排队/运行任务会去重，避免并发写入。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

run_web.bat          # 默认 refresh：刷新后启动/打开
run_web.bat refresh  # 默认行为的显式别名
run_web.bat start    # 不调用 get_RSS.py，仅使用已有数据
```

启动器会检查 `.venv\Scripts\python.exe`、参数数量和名称，并以只读 `/api/interactions` 返回的 `favorites`、`archived`、`hidden` 数组结构识别已运行的 Paper Feed；其他端口 8000 占用会失败。默认和 `refresh` 都可能访问 RSS 网络、调用 OpenAI 并修改数据库和兼容导出；`start` 没有这些刷新副作用。启动器不输出配置或密钥。

OpenAI 设置来自被忽略的根 `config.json` 或 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_PROXY` 环境变量。不得把真实密钥写入源码、测试、文档、日志或提交记录。未配置 OpenAI 时，现有数据浏览和 RSS 抓取可用，但 AI 翻译、分类和总结不可用。

## 验证入口与维护边界

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest tests/test_run_web_launcher.py
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

网络、OpenAI 和 Playwright 测试应使用受控环境，避免真实 API 费用。改动 RSS 导入、论文身份、数据库路径、端口/绑定、job 生命周期、API 或兼容导出时，同步检查 `get_RSS.py`、`paper_feed/`、`server.py`、`web/` 与相应测试；不要提交数据库、`config.json`、虚拟环境或无意生成物。
