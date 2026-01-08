# Paper Feed - 学术论文 RSS 订阅系统

一个智能的学术论文订阅与过滤系统，帮助研究人员从海量学术期刊中快速找到感兴趣的论文。

## 主要功能

- **多源聚合**: 从多个顶级学术期刊的 RSS 源自动获取最新论文
- **智能过滤**: 基于自定义关键词自动筛选相关论文
- **双语支持**: 使用 OpenAI API 自动将论文标题翻译成中文
- **RSS 输出**: 生成标准 RSS feed，可在任何 RSS 阅读器中订阅
- **现代化 Web 界面**:
  - 支持按关键词/期刊/摘要搜索
  - 按日期范围和期刊筛选
  - 收藏和隐藏功能
  - 响应式设计，支持移动端

## 系统要求

- Python 3.7+
- 互联网连接（用于获取 RSS 源和调用 OpenAI API）

## 安装

1. 克隆项目或下载源代码
2. 安装依赖：

```bash
pip install -r requirements.txt
```

主要依赖包括：
- `feedparser`: RSS feed 解析
- `rfeed`: RSS feed 生成
- `openai`: OpenAI API 客户端（可选，用于标题翻译）

## 配置

### 1. 配置 RSS 源 (`journals.dat`)

在 `journals.dat` 文件中添加你想订阅的学术期刊 RSS 源，每行一个 URL：

```
https://academic.oup.com/rss/site_5397/advanceAccess_3258.xml
https://journals.sagepub.com/action/showFeed?...
```

项目中的 `RSS list.md` 文件包含了大量顶级期刊的 RSS 源链接，涵盖：
- 市场营销与消费者行为
- 社会心理学与判断决策
- 管理学、组织行为与战略创业
- 决策科学、运筹与运营管理
- 信息管理与信息系统
- 经济学与行为/实验经济学
- 旅游与酒店管理

### 2. 配置关键词 (`keywords.dat`)

在 `keywords.dat` 文件中添加你感兴趣的关键词，每行一个。支持 AND 逻辑：

```
embarrassment
social media AND marketing
consumer behavior
```

### 3. 配置 OpenAI API（可选）

如果需要论文标题自动翻译功能，创建 `config.json` 文件：

```json
{
  "OPENAI_API_KEY": "your-api-key-here",
  "OPENAI_BASE_URL": "",
  "OPENAI_PROXY": ""
}
```

也可以通过环境变量配置：

```bash
export OPENAI_API_KEY="your-api-key-here"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选
export OPENAI_PROXY="http://proxy:port"  # 可选
```

**注意**: 如果不配置 OpenAI API，系统仍可正常工作，只是不会提供中文翻译。

## 使用方法

### 方式一：命令行运行

直接运行 RSS 抓取脚本：

```bash
python get_RSS.py
```

这将：
1. 从配置的 RSS 源获取最新论文
2. 根据关键词过滤
3. 生成 `filtered_feed.xml` 文件
4. 生成 `web/feed.json` 文件供 Web 界面使用

### 方式二：启动 Web 服务器

启动本地 Web 服务器：

```bash
python server.py
```

或使用批处理文件（Windows）：

```bash
run_web.bat
```

然后在浏览器中访问 `http://localhost:8000`

Web 界面功能：
- 浏览所有过滤后的论文
- 搜索和高级筛选
- 收藏感兴趣的论文
- 隐藏不感兴趣的论文
- 点击"立即更新"按钮手动触发 RSS 抓取
- 在设置中配置 OpenAI API

### 订阅 RSS Feed

生成的 `filtered_feed.xml` 可以在任何 RSS 阅读器中订阅：

1. 将文件上传到 Web 服务器
2. 在 RSS 阅读器中添加订阅链接

## 文件结构

```
paper-feed/
├── get_RSS.py              # RSS 抓取和处理主程序
├── server.py               # Web 服务器
├── config.json             # OpenAI API 配置（需自行创建）
├── journals.dat            # RSS 源列表
├── keywords.dat            # 关键词列表
├── requirements.txt        # Python 依赖
├── filtered_feed.xml       # 生成的 RSS feed
├── RSS list.md            # 推荐的学术期刊 RSS 源
├── web/                   # Web 界面文件
│   ├── index.html         # 主页面
│   ├── app.js             # 前端逻辑
│   ├── styles.css         # 样式
│   ├── feed.json          # 论文数据
│   ├── interactions.json  # 用户交互数据（收藏、隐藏）
│   └── translations.json  # 翻译缓存
└── README.md              # 本文件
```

## 工作原理

1. **数据获取**: 从配置的 RSS 源抓取最新论文信息
2. **关键词匹配**: 使用关键词在标题和摘要中进行匹配
3. **去重处理**: 基于文章 ID 自动去重，避免重复抓取
4. **增量更新**: 保留历史数据，只添加新论文
5. **智能翻译**:
   - 使用 OpenAI GPT-4o-mini 批量翻译论文标题
   - 支持 50 线程并发翻译，提高效率
   - 自动缓存翻译结果，避免重复翻译
6. **多格式输出**:
   - 生成标准 RSS 2.0 XML 文件
   - 生成 JSON 文件供 Web 界面使用

## 高级功能

### 环境变量配置

除了使用配置文件，还支持通过环境变量配置：

- `RSS_JOURNALS`: RSS 源列表（用 `;` 或换行分隔）
- `RSS_KEYWORDS`: 关键词列表（用 `;` 或换行分隔）
- `OPENAI_API_KEY`: OpenAI API 密钥
- `OPENAI_BASE_URL`: OpenAI API 基础 URL（可选）
- `OPENAI_PROXY`: HTTP 代理（可选）

### 期刊列表变化检测

系统会自动检测 `journals.dat` 的变化：
- 当期刊列表变化时，自动清除历史缓存
- 避免不同期刊组合产生的数据混淆

## 注意事项

1. **API 配额**: OpenAI API 调用会产生费用，请注意配额管理
2. **请求频率**: RSS 源抓取建议间隔至少 1 小时，避免对期刊服务器造成压力
3. **数据隐私**: `config.json` 包含敏感信息，请勿提交到版本控制系统
4. **网络要求**: 部分学术期刊可能需要通过机构网络或 VPN 访问

## 自动化部署

可以使用 GitHub Actions 或其他 CI/CD 工具定期运行 `get_RSS.py`，实现自动更新。示例工作流：

```yaml
name: Update RSS Feed
on:
  schedule:
    - cron: '0 */6 * * *'  # 每 6 小时运行一次
  workflow_dispatch:

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.9'
      - run: pip install -r requirements.txt
      - run: python get_RSS.py
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      - run: |
          git config user.name "GitHub Actions"
          git config user.email "actions@github.com"
          git add filtered_feed.xml web/feed.json web/translations.json
          git commit -m "Auto-update RSS feed" || exit 0
          git push
```

## 许可证

本项目仅供学习和个人使用。使用时请遵守相关学术期刊的服务条款。

## 贡献

欢迎提交 Issue 和 Pull Request！

---

**提示**: 如果你发现了新的优质学术期刊 RSS 源，欢迎补充到 `RSS list.md` 文件中。
