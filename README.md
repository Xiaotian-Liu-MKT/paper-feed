# Paper Feed - 学术论文 RSS 订阅系统

一个 browser-first 的本地学术论文 RSS 筛选器：抓取期刊 RSS、按关键词过滤，并在浏览器中完成检索、刷卡分流、收藏、偏好分析和 RIS 导出。

> ### 快速开始（4 步）
>
> 0. **安装 Python 3.11+**（已安装可跳过）：从 [python.org](https://www.python.org/downloads/) 下载安装包。Windows 安装时**勾选 “Add python.exe to PATH”**，并保留默认勾选的 “py launcher”；装好后在 PowerShell 运行 `py --version` 能看到版本号即可。
> 1. **创建虚拟环境并安装依赖**
>    ```powershell
>    py -3.11 -m venv .venv        # 没有 3.11 时可用：py -m venv .venv
>    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
>    ```
>    国内网络下载慢时可加清华镜像：`.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
> 2. **双击 `run_web.bat`**（或在终端运行 `run_web.bat start`；macOS/Linux：`./run_web.sh`）。
>    双击**只启动**：不刷新 RSS、不联网、不调用 AI、不产生费用，浏览器会打开 `http://127.0.0.1:8000`。
>    首次启动会创建一个**空数据库**（`data/paper_feed.sqlite3`），仓库里已有的 `filtered_feed.xml` / `web/feed.json` 不会被自动导入；想导入这些已有论文，先运行一次 `.\.venv\Scripts\python.exe -m paper_feed import-legacy`。
> 3. **在网页里完成配置后再更新**：点 ⚙️ **设置** 填入 API Key（可选），点 🔑 **关键词** 编辑并预览关键词，在 **期刊** 页勾选要订阅的期刊，最后点 **立即更新** 抓取新论文。以后想“先抓取再打开”，运行 `run_web.bat run`。

## 主要功能

- **多源聚合**：从多个学术期刊 RSS 源获取最新论文，期刊可在网页中从目录勾选订阅。
- **智能过滤**：基于自定义关键词规则（AND / 排除 / 短语）筛选相关论文，并可在保存前预览命中情况。
- **双语与 AI 功能**：可选的 OpenAI 兼容接口（含 DeepSeek 等）用于标题翻译、分类和收藏论文总结。
- **现代化 Web 界面**：关键词、期刊和摘要搜索；日期与期刊筛选；待筛选/收藏/归档/已隐藏四个视图；收件箱刷卡和撤销；键盘快捷键；收藏 RIS 导出。
- **本地优先数据**：SQLite 保存论文、状态和分析结果；兼容保留 RSS/XML/JSON 导出。

## 系统要求与安装

- Python **3.11+**（当前依赖也已在 Python 3.13 验证）。
- 仅在刷新 RSS 或调用 AI 时需要互联网连接。

**安装 Python（Windows）**：从 [python.org](https://www.python.org/downloads/) 下载 3.11 或更新版本的安装包，安装第一页**勾选 “Add python.exe to PATH”**，并保留 “py launcher”。安装后打开新的 PowerShell 窗口运行 `py --version`（或 `python --version`）确认。如果输入 `python` 弹出 Microsoft Store，说明 PATH 中还没有真正的 Python，请改用 `py` 或重新安装并勾选 PATH。

Windows：

```powershell
py -3.11 -m venv .venv
# 如果提示找不到 3.11，可直接使用已安装的默认 Python 3.11+：
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 国内网络较慢时可使用清华 PyPI 镜像：
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

主要依赖：`feedparser`（RSS 解析）、`rfeed`（RSS 生成）、`requests`、`openai`（可选的 AI 客户端）。

## 配置

### 1. AI 设置（可选）

两种方式任选其一：

- **网页设置**：启动后点击 ⚙️ **设置**，填写 API Key、Base URL、模型名等，保存后写入本地 `config.json`。
- **手动复制**：把 `config.json.example` 复制为 `config.json`（已被 `.gitignore` 忽略）后编辑。

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 使用 AI 时必填 | 保留 `your-api-key-here` 等以 `your-` 开头的占位值视为“未配置” |
| `OPENAI_BASE_URL` | 可选 | 留空使用 OpenAI 官方地址；使用 DeepSeek 等兼容服务时填其地址，如 `https://api.deepseek.com` |
| `OPENAI_MODEL` | 可选 | 默认 `gpt-4o-mini`；使用 DeepSeek 时可填 `deepseek-chat` |
| `OPENAI_PROXY` | 可选 | HTTP 代理地址，如 `http://127.0.0.1:7890` |
| `OPENALEX_MAILTO` | 可选 | 你的联系邮箱；免费摘要查找（`fetch-abstracts`）访问 OpenAlex 时附带，进入其 “polite pool”（更稳定）。可写在环境变量或 `config.json` 中，与 AI 无关 |

**配置优先级**：非空环境变量 > `config.json` 中的非空、非占位值 > 默认值。项目不会自动加载 `.env`。

不配置 AI 也能抓取 RSS、浏览和分流论文，只是标题翻译、AI 分类和 AI 总结会被跳过。AI 调用可能产生费用；不要将真实密钥写入文档、测试、日志或提交记录。

### 2. 关键词（`keywords.dat` 或网页 🔑 关键词）

推荐在网页中点击 🔑 **关键词** 编辑：编辑器提供 **预览**，会用已入库的论文（标题 + 摘要）统计每条规则的命中数并列出示例，确认后再保存。也可以直接编辑 `keywords.dat`，`keywords.dat.example` 提供了一份消费者行为方向的示例。

语法：

- 每行一条规则，多行之间是“**或**”关系，命中任意一行即保留。
- 同一行用 `AND` 连接多个词，所有词都必须出现（大小写均可：小写 `and` 同样会被当作连接词；若要匹配含 and 的短语，请加双引号，如 `"supply and demand"`）。
- 词前加 `-` 或 `NOT ` 表示排除。
- 不区分大小写，按**词首**匹配：`consum` 能匹配 consumer / consumption，`ai` 不会误匹配 said。
- 多词短语用英文双引号，如 `"word of mouth"`。
- 以 `#` 开头的行是注释。

```
# 情绪
embarrassment
shame AND consum
"social media" AND marketing
AI AND NOT agriculture
chatbot AND -medical
```

**注意**：关键词只在抓取时过滤**新抓取**的论文；修改关键词不会删除已经入库的论文。

### 3. 期刊（`journals.dat` 或网页“期刊”页）

在网页的 **期刊** 页面可以直接从期刊目录中勾选订阅，目录来自 `RSS list.md`，按学科分组，覆盖市场营销与消费者行为、社会心理、管理、决策科学、信息系统、经济学、旅游与酒店管理等。也可以粘贴任意 RSS 链接。保存时会自动去除 `utm_*` 等跟踪参数。

`journals.dat` 每行一个 RSS URL，可直接编辑：

```
https://academic.oup.com/rss/site_5397/advanceAccess_3258.xml
https://pubsonline.informs.org/action/showFeed?type=etoc&feed=rss&jc=mksc
```

### 4. 端口

默认端口是 `8000`。端口被占用时，可以设置环境变量 `PAPER_FEED_PORT`，或给命令行传 `--port`：

```powershell
$env:PAPER_FEED_PORT = "8010"; .\.venv\Scripts\python.exe -m paper_feed start
.\.venv\Scripts\python.exe -m paper_feed start --port 8010
```

启动器同样支持：端口取第 2 个参数，其次是 `PAPER_FEED_PORT`，最后是 8000，例如 `run_web.bat start 8010`、`./run_web.sh run 8010`。

## 使用方法

### 启动 Web 界面

Windows 推荐使用启动器（始终使用 `.venv\Scripts\python.exe`）：

```powershell
run_web.bat              # 默认（双击同此）：不刷新 RSS，仅启动或打开已有本地数据
run_web.bat start        # 与默认相同的显式写法
run_web.bat run          # 先刷新 RSS（联网，可能调用 AI），再启动或打开应用
run_web.bat refresh      # run 的别名
run_web.bat start 8010   # 第 2 个参数指定端口（也可设置 PAPER_FEED_PORT）
```

macOS / Linux 使用 `run_web.sh`，参数相同：

```bash
chmod +x run_web.sh   # 首次使用
./run_web.sh          # = start
./run_web.sh run
```

启动器只是 `python -m paper_feed start`（默认）和 `python -m paper_feed run`（`run`/`refresh`）的外壳，见下方“命令行”；提示信息为中英双语。服务只监听 `127.0.0.1`，包含本地写入接口和后台任务，**不要进行公网端口转发**。

**注意：** 只有 `run_web.bat run` / `run_web.bat refresh` 会联网、可能调用 AI（产生费用）并修改生成物；双击或 `start` 不会。

**首次启动是空数据库**：`start` 在 `data/paper_feed.sqlite3` 不存在时创建一个空库，不会自动导入仓库中已跟踪的 `filtered_feed.xml` / `web/feed.json`。如需把这些已有导出（以及旧版 `web/*.json` 状态）导入，运行一次：

```powershell
.\.venv\Scripts\python.exe -m paper_feed import-legacy --dry-run   # 先演练，只报告数量
.\.venv\Scripts\python.exe -m paper_feed import-legacy
```

导入是单向、幂等的，会保留导出中的 `paper_id` 和已有的标题翻译/分类。`refresh` / `run` 在没有数据库时同样只创建空库；只有在 CI 中（自动识别环境变量 `CI` / `GITHUB_ACTIONS`）或显式设置 `PAPER_FEED_BOOTSTRAP_FROM_EXPORTS=1` 时，才会在建库时自动从这些导出引导历史：

```powershell
$env:PAPER_FEED_BOOTSTRAP_FROM_EXPORTS = "1"; .\.venv\Scripts\python.exe -m paper_feed refresh
```

### 视图与快捷键

| 视图 | 说明 |
| --- | --- |
| 待筛选 | 收件箱，新论文都在这里；处理后移出 |
| 收藏 | 感兴趣的论文；可生成 AI 总结、补充摘要、导出 RIS，并显示主题云 |
| 归档 | 暂时不处理但想保留的论文 |
| 已隐藏 | 标为“不感兴趣”的论文；可在这里一键恢复 |
| 全部 | 所有论文（含已处理），卡片上显示当前状态 |

常用快捷键：

- **刷卡模式**：`→` 收藏、`←` 不感兴趣、`A`（或 `L`）归档、`O` 打开原文、`Z` 撤销；也可用鼠标/触屏左右拖动卡片。
- **列表模式**：`J`/`K` 移动高亮卡片，`F` 收藏、`A`（或 `L`）归档、`X` 不感兴趣、`O` 打开原文。
- `Z` 在任何视图都可撤销最近的操作（最多 20 步，切换视图时清空）；按 **`?`** 弹出完整快捷键说明。

### 🔎 洞察页（insights.html）

原「偏好报告」和「期刊统计」已合并为一个「洞察」页面，分三个标签：

- **我的偏好**：基于标题的偏好词、方法/主题偏好倍数、时间趋势、来源/期刊偏好。报告不会自动更新，标记新文章后点「重新计算」。
- **期刊表现**：全部期刊一览 + 单刊详情（发刊频率、收藏/不感兴趣占比、主题分布）。
- **期刊匹配**：按匹配度/结构匹配排序的期刊画像，以及粘贴研究摘要反向匹配期刊。

每个标签都有独立地址，可直接收藏或分享：`insights.html#prefs`、`#journals`、`#journals/detail?journal=期刊名`、`#fit`、`#fit/match`；页面会记住上次打开的标签。各处「筛选」会跳回 Feed 并显示「← 返回洞察」；旧的 `report.html` / `stats.html` 链接会自动跳转到对应标签。

顶部导航栏可在 Feed、洞察、期刊管理之间切换。更新 RSS、重新分析和 AI 总结以后台任务运行，刷新页面后仍能看到正在运行的任务。

### 命令行

所有命令行功能统一为 `python -m paper_feed <命令>`（Windows 下用 `.\.venv\Scripts\python.exe -m paper_feed ...`，macOS/Linux 用 `.venv/bin/python -m paper_feed ...`）。不带命令运行会显示帮助；每个命令都支持 `--help`。

| 命令 | 作用 | 联网 / AI 费用 |
| --- | --- | --- |
| `start [--port N] [--host H] [--no-browser]` | 用现有本地数据启动服务并打开浏览器；若该端口上已有 Paper Feed，直接打开它；端口被其他程序占用时报错退出 | 否 |
| `run [--port N] [--host H] [--no-browser]` | 先 `refresh`，再像 `start` 一样启动/打开；刷新失败时仍打开已有数据；配置了 OpenAI 密钥时会提示费用 | 是 |
| `serve [--port N] [--host 127.0.0.1] [--open]` | 只启动本地 Web/API 服务（`--open` 启动后打开浏览器） | 否（后台任务按需） |
| `refresh` | 抓取 RSS、按关键词过滤、写入 SQLite、AI 标题分析（若配置密钥）、重新生成 `filtered_feed.xml` / `web/feed.json` 等导出。退出码：`0` 已发布；`1` 所有 RSS 源都失败；`2` `journals.dat` 或关键词为空 | 是 |
| `reanalyze [--dry-run] [--yes]` | 对未分类或分类版本过旧的论文做 AI 标题分析；先显示数量并确认，`--dry-run` 只统计 | AI |
| `summarize-favorites [--dry-run] [--yes]` | 为尚无 AI 总结的收藏生成总结（缺原始摘要时先按 DOI 免费查找）；先显示数量并确认，`--dry-run` 只统计。未配置密钥时只做免费摘要查找、跳过 AI 总结 | AI（无密钥时仅联网） |
| `fetch-abstracts [--view favorite\|all\|inbox\|archived] [--yes]` | 按 DOI 依次通过 Crossref → OpenAlex → Semantic Scholar 免费获取尚无摘要论文的原始摘要（默认只处理收藏）；不需要 API Key、不消耗 token | 联网，免费 |
| `keywords show` | 显示当前生效的关键词规则及来源（`RSS_KEYWORDS` 环境变量会覆盖 `keywords.dat`） | 否 |
| `keywords preview [--text T \| --file F] [--json]` | 用库中已有论文预览规则命中数与示例（默认使用当前规则） | 否 |
| `doctor [--port N] [--ascii]` | 环境自检：Python 版本、依赖（含 httpx 代理兼容）、每项配置的来源（不显示密钥）、`journals.dat`/`keywords.dat`、数据库完整性与各状态论文数、端口、`web/` 文件；有阻塞问题时退出码为 1 | 否 |
| `backup [--out DIR] [--json]` | 用 sqlite3 备份 API 生成一致的数据库副本（服务运行时也安全），打印备份文件路径；`--json` 输出机器可读结果 | 否 |
| `restore <备份文件> [--yes]` | 用备份替换当前数据库（见下方“备份与恢复”）；服务运行时拒绝执行 | 否 |
| `import-legacy [--root R] [--database D] [--dry-run]` | 将旧版 `filtered_feed.xml`、`web/*.json` 单向、幂等地导入 SQLite | 否 |
| `publish-guard --xml X --json J [--baseline-xml B]` | 发布前校验导出（GitHub Actions 使用） | 否 |

`refresh` / `run` / `reanalyze` / `summarize-favorites` 可能联网、调用 AI，并修改 `data/paper_feed.sqlite3`、`filtered_feed.xml`、`web/feed.json`、`web/translations.json` 等本地生成物。`reanalyze` 与 `summarize-favorites` 在非交互环境中必须加 `--yes` 才会执行。

兼容入口仍然可用：`python get_RSS.py` 等同于 `python -m paper_feed refresh`（退出码相同），`python server.py [--port N]` 等同于 `python -m paper_feed serve`，`python -m paper_feed.publish_guard` 等同于 `publish-guard`。旧的“不带命令的 `python -m paper_feed` = 导入旧数据”已取消，请改用 `import-legacy`。

数据库是唯一的数据真相源，升级或大改前建议先执行 `backup`。遇到问题先运行 `doctor`。

### 备份与恢复

收藏、归档、隐藏等分流状态和你补充的摘要只保存在本机的 `data/paper_feed.sqlite3` 中，仓库里的导出文件不包含它们，请定期备份：

```powershell
.\.venv\Scripts\python.exe -m paper_feed backup
# 备份完成（N 篇论文）：data\paper_feed.sqlite3-backup-20261005T101500123456.sqlite3
```

恢复步骤：

1. 先停止 Paper Feed（在服务窗口按 Ctrl+C）。服务仍在端口上响应时 `restore` 会拒绝执行。
2. 运行 `.\.venv\Scripts\python.exe -m paper_feed restore data\paper_feed.sqlite3-backup-<时间>.sqlite3`。
3. 命令会先对备份做 `PRAGMA integrity_check` 并显示论文数与各状态数量，确认后（非交互环境需加 `--yes`）把当前数据库另存为 `data/paper_feed.sqlite3-pre-restore-<时间>.sqlite3`，再替换 `data/paper_feed.sqlite3` 并清除旧的 `-wal` / `-shm` 文件。

恢复错了也不要紧：`pre-restore` 文件本身就是一个备份，可以再用 `restore` 换回来。备份文件位于 `data/` 下并被 `.gitignore` 忽略，不会被提交。

### 在其他 RSS 阅读器中订阅

`filtered_feed.xml` 是 SQLite 数据的兼容 RSS 导出，不是主数据源。本仓库**没有**配置 GitHub Pages 等托管；如果想在其他阅读器订阅，需要自行把该文件放到你控制的 Web 服务器上。对于公开仓库，也可以直接订阅它在 GitHub 上的 raw 链接（`https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/filtered_feed.xml`）。

## Fork 与自动更新（GitHub Actions）

`.github/workflows/rss_action.yaml` 每 6 小时（也可手动）运行一次 RSS 更新，并把 `filtered_feed.xml`、`web/feed.json` 等导出提交回仓库。Fork 后按以下步骤启用：

1. **启用 Actions**：在你的 fork 中打开 **Actions** 标签页，点击 “I understand my workflows, go ahead and enable them”。定时任务在 fork 中默认是关闭的。
2. **配置 Secrets**：进入 **Settings → Secrets and variables → Actions → New repository secret**：

   | Secret | 是否必填 | 说明 |
   | --- | --- | --- |
   | `RSS_KEYWORDS` | 推荐 | 关键词规则，多条用换行或 `;` 分隔；不设置时使用仓库中的 `keywords.dat` |
   | `OPENAI_API_KEY` | 可选 | 不设置则跳过 AI 翻译与分类 |
   | `OPENAI_BASE_URL` | 可选 | 兼容接口地址（如 DeepSeek） |
   | `OPENAI_MODEL` | 可选 | 模型名，默认 `gpt-4o-mini` |

   设置了 `RSS_KEYWORDS` 时，导出的 `web/feed.json` 会**省略** `keywords` 字段（它只用于网页高亮），关键词本身不会被提交。但请注意：仓库里的 `keywords.dat` 仍是公开的（用 Secret 时可清空或替换它），而被筛选出的论文标题会出现在公开的 `filtered_feed.xml` / `web/feed.json` 中，仍可能间接反映你的研究方向；对此敏感时请使用私有仓库。
3. **清理继承来的导出与订阅**：fork 会带上原作者的数据。首次运行前，删除或替换这些文件并提交：
   - `filtered_feed.xml`、`web/feed.json`（以及存在时的 `web/translations.json`）：删除（自动化在没有数据库时会从它们“引导”历史数据，不删会继承原作者的论文）。
   - `journals.dat`：换成你自己的期刊列表。
   - `journals_meta.json`：删除或清空为 `{}`，避免残留旧期刊名称。
4. **手动运行一次**：在 Actions 页选择 “Auto RSS Fetch” → **Run workflow**，确认运行成功后，后续会按计划自动更新。

需要了解的几点：

- **测试失败会阻止 RSS 更新**：工作流先运行全部测试，任何测试失败都会让本次运行失败，不会抓取或提交新的导出。修好测试后手动 **Run workflow** 即可恢复。
- **自动化中的 SQLite 只是临时缓存**，每次运行都会删除并从已提交的导出重建，不会被提交。重建时会保留导出中的 `paper_id` 和标题翻译/分类，所以配置了 `OPENAI_API_KEY` 时只有新论文会调用 AI，不会每次都重新分析全部论文。
- **本地数据库与 CI 不同步**：收藏、归档、隐藏等分流状态只存在于你本机的 `data/paper_feed.sqlite3`；CI 提交的导出不包含这些状态，`git pull` 拉下来的新导出也不会自动写入本地数据库（本地用 `run_web.bat run` 自行抓取即可）。请用 `backup` 保护本地数据库。
- 同一时间只会运行一个更新任务（`concurrency: rss-publish`）；推送被拒时会先 `git pull --rebase` 再重试。

## 数据与文件结构

SQLite 是唯一的持久化真相源，默认路径是 `data/paper_feed.sqlite3`，可通过 `PAPER_FEED_DB` 覆盖。每篇论文使用稳定 `paper_id` 关联论文、交互状态、用户修正、AI 分析与 RIS 导出；新代码不得将旧 `id`、链接或 JSON 键作为新的持久化身份。

```
paper-feed/
├── get_RSS.py              # RSS 抓取、导入与兼容导出（`python get_RSS.py` = refresh）
├── server.py               # 127.0.0.1 本地 Web/API 与后台任务（`python server.py` = serve）
├── paper_feed/             # SQLite、论文身份、导入、备份与服务层；cli.py 为统一命令行
├── data/paper_feed.sqlite3 # SQLite 真相源（本地生成，默认忽略）
├── journals.dat            # RSS 源列表
├── keywords.dat            # 关键词规则（keywords.dat.example 为示例）
├── config.json.example     # AI 配置模板（复制为 config.json）
├── RSS list.md             # 期刊目录（期刊页的勾选来源）
├── filtered_feed.xml       # 兼容 RSS 导出
├── web/                    # Web 界面（见 web/README.md）
├── dev/                    # 调试页面
├── docs/history/           # 历史设计文档
├── run_web.bat / run_web.sh # Windows / macOS·Linux 启动器
└── tests/                  # 单元、服务与启动器测试
```

`filtered_feed.xml`、`web/feed.json`、`web/translations.json` 和其他 `web/*.json` 本地状态/导出均不是权威数据库。提交前请确认这些生成物变更确有意图；不要提交 `config.json`、密钥、数据库、虚拟环境或本地状态。

## 工作原理

1. 从 `journals.dat` 的 RSS 源抓取论文元数据。
2. 根据关键词规则匹配标题和摘要元数据，只保留命中的新论文。
3. 使用 DOI、URL、来源标识等规范化信息确定稳定 `paper_id` 并写入 SQLite。
4. 保留论文的历史观察记录和用户状态，增量更新新数据。
5. 按配置调用 AI 完成翻译、分类或总结；未配置时跳过这些功能。
6. 从 SQLite 投影出 XML/JSON 兼容导出，供 RSS 阅读器或自动化使用。

## 常见问题排查

- **先运行自检**：`.\.venv\Scripts\python.exe -m paper_feed doctor` 会逐项检查依赖、配置来源、关键词/期刊文件、数据库和端口，并标出阻塞问题。
- **`ModuleNotFoundError` / 缺少依赖**：确认使用的是 `.venv` 中的 Python，并重新执行 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。启动器提示找不到 `.venv\Scripts\python.exe` 时，说明还没有创建虚拟环境。
- **端口被占用**：启动器发现 8000 端口被其他程序占用时会报错退出，并打印可直接复制的完整命令（含当前 Python 路径）。关闭占用程序；若需换端口，请运行 `run_web.bat start 8010` 或 `.\.venv\Scripts\python.exe -m paper_feed start --port 8010`（也可设置 `PAPER_FEED_PORT`）。若占用者本身就是 Paper Feed，启动器会直接打开它。
- **AI 功能被跳过 / 没有翻译**：通常是未配置 API Key，或仍是 `your-api-key-here` 这样的占位值。在 ⚙️ 设置中检查“已配置密钥”状态；也要检查环境变量里是否有一个错误的 `OPENAI_API_KEY`（环境变量优先于 `config.json`）。
- **任务状态为 `partial_failed`**：任务整体完成，但部分条目失败（例如个别 RSS 源超时、少数 AI 请求出错）。任务结果中会列出失败数量和简短错误信息；通常稍后重试即可，不需要重置数据。
- **期刊抓取失败**：部分期刊需要机构网络或 VPN；也可能是 RSS 链接已失效，可在期刊页替换。
- **`refresh`（`get_RSS.py`）退出码**：`0` 已发布；`1` 所有 RSS 源都抓取失败；`2` `journals.dat` 或关键词为空。`run` / `run_web.bat run` 遇到 1/2 会提示并打开已有数据；GitHub Actions 中会让运行显示为失败。
- **分类领域不是营销**：可在 `web/categories.json` 中加一个可选的 `"domain"` 字段（如 `"Organizational Behavior"`），AI 分类提示词会使用它；分类失败时回退为已配置的类别或 `Unclassified`。
- **关键词改了但旧论文还在**：这是预期行为，关键词只过滤新抓取的论文。不想看的旧论文可在待筛选中隐藏。

## 注意事项

1. AI 调用可能产生费用；刷新间隔建议至少一小时，避免给期刊站点造成压力。
2. `RSS_JOURNALS`、`RSS_KEYWORDS` 环境变量会覆盖 `journals.dat`、`keywords.dat`（多条用换行或 `;` 分隔）；设置 `RSS_KEYWORDS` 时 `web/feed.json` 不写入 `keywords` 字段。
3. `/api/fetch`、`/api/reanalyze`、`/api/summarize_favorites` 可能联网、写入或调用 AI；测试和演示时不要无意触发。
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

## 许可证与贡献

本项目仅供学习和个人使用，请遵守期刊服务条款。欢迎提交 Issue 和 Pull Request；改动论文身份、数据库路径、端口绑定、生成物路径或后台任务时，请同步更新 `DEV_CONTEXT.md` 与相关测试。
