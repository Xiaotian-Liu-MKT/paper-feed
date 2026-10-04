# Paper Feed Web

本目录是 Paper Feed 的浏览器界面，由根目录的 `server.py` 提供静态文件和本地 API。**不要再用 `python -m http.server` 直接托管本目录**：那样没有 `/api/*` 接口，收藏、关键词、期刊、AI 总结等功能都无法使用。

## 启动

在项目根目录运行：

```powershell
run_web.bat start          # Windows：不刷新 RSS，直接打开已有数据
./run_web.sh start         # macOS / Linux
.\.venv\Scripts\python.exe server.py   # 或直接启动服务
```

默认地址为 `http://127.0.0.1:8000`，端口可用 `PAPER_FEED_PORT` 环境变量或 `server.py --port` 修改。详见根目录 `README.md`。

## 文件一览

| 文件 | 作用 |
| --- | --- |
| `index.html` / `app.js` / `styles.css` | 主界面：待筛选 / 收藏 / 归档 / 已隐藏视图、刷卡分流、搜索筛选、⚙️ 设置、🔑 关键词编辑器、RIS 导出 |
| `nav.js` | 各页面共享的顶部导航栏 |
| `journals.html` / `journals.js` | 期刊订阅管理，支持从期刊目录（`RSS list.md`）勾选添加 |
| `report.html` / `report.js` | 基于收藏/隐藏标题的偏好报告 |
| `stats.html` / `stats.js` | 统计页 |
| `categories.json` | 研究方法 / 主题分类体系 |
| `feed.json` | SQLite 数据的兼容导出（生成物，非数据源） |
| `translations.json`、`preference_report.json`、`journals.hash` 等 | 本地生成物或缓存，已被 `.gitignore` 忽略或仅由自动化提交 |

调试页面已移到仓库根目录的 `dev/`，不再随主界面发布。
