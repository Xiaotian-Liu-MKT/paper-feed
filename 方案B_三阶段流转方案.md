Paper Feed 方案B：三阶段流转完整方案

目标
- 将当前“两阶段”（未筛选/收藏）升级为“三阶段”（收件箱/收藏/归档），符合“先筛选再精读最后归档”的真实流程。
- 保持现有交互逻辑与数据结构的稳定性，避免破坏 RSS 日常更新与偏好报告能力。

当前现状（关键约束）
- `interactions.json` 仅含 `favorites` 与 `hidden`。
- 前端过滤模式：`all`（未筛选）与 `favorites`（收藏）。
- “生成 AI 总结”“主题云”只在收藏视图显示。
- “待筛选为空”提示文案固定为“暂时没有新的文献了...”。

方案B：三阶段流转（收件箱 → 收藏 → 归档）
1) 收件箱（原“未筛选”）
   - 初始入口，显示未被收藏/归档/隐藏的文章。
   - 操作：收藏、隐藏。
2) 收藏（待精读）
   - 用户计划精读的文章清单。
   - 操作：归档、取消收藏（回收件箱）、隐藏。
   - 允许“生成 AI 总结”与“主题云”。
3) 归档（已读/已处理）
   - 读完后的归档区，默认不打扰主视图。
   - 操作：恢复到收藏、取消归档（回收件箱）、隐藏。

状态机与流转规则
- Inbox -> Favorites：收藏
- Favorites -> Archived：归档
- Archived -> Favorites：恢复到收藏
- Any -> Hidden：隐藏（隐藏优先级最高，隐藏后不再参与三阶段流转）
- Favorites -> Inbox：取消收藏（若未归档）
- Archived -> Inbox：取消归档

数据结构调整（interactions.json）
新增字段 `archived`（数组，保存 paper `id`）：
{
  "favorites": ["id1", "id2"],
  "archived": ["id3"],
  "hidden": ["id4"]
}

兼容策略
- 若旧文件不含 `archived`，默认空数组。
- 读取时统一做去重与交叉清洗（同一 id 仅能属于 favorites/archived/hidden 中的一个）。

API 层改动（server.py）
- `GET /api/interactions`：返回 `archived` 字段（如不存在则补空）。
- `POST /api/interactions`：接收并落盘 `archived`。
- 偏好报告（`/api/preference_report`）口径建议：
  - 推荐：`favorites + archived` 作为“正样本”（用户明确收藏且最终归档，反映长期偏好）。
  - 保守选项：仅使用 `favorites` 维持原语义。
  - 文档内需明确采用哪一口径，避免统计波动。

前端改动（web）
1) 视图入口
   - `web/index.html`：新增“归档”按钮/Tab，与现有“未筛选/收藏”并列。
   - `state.filterMode` 扩展为：`all` | `favorites` | `archived`。

2) 列表过滤逻辑（web/app.js）
   - 收件箱：不在 favorites、archived、hidden。
   - 收藏：在 favorites 且不在 hidden。
   - 归档：在 archived 且不在 hidden。

3) 卡片动作
   - 收件箱卡片：收藏 / 隐藏。
   - 收藏卡片：归档 / 取消收藏 / 隐藏。
   - 归档卡片：恢复到收藏 / 取消归档 / 隐藏。
   - 归档与收藏互斥，操作时需从对方数组移除。

4) 交互强化与文案
   - 空状态：
     - 收件箱：沿用“暂时没有新的文献了...”。
     - 收藏：可用“暂无收藏文章”。
     - 归档：可用“暂无已读归档文章”。
   - “生成 AI 总结”按钮仅在收藏视图显示（保持既有策略）。
   - 主题云仅在收藏视图显示（保持既有策略）。

5) 数据清洗
   - `loadInteractions()` 后进行一次互斥清洗，保证 id 不同时存在于 favorites 与 archived。

统计与报告（web/stats.js, server.py）
- 若采用“favorites + archived”为正样本：
  - 需在统计逻辑中合并两者，避免用户“归档后偏好消失”。
- 若保持“favorites”仅代表正样本：
  - 归档文章不计入偏好学习，偏好可能被低估。

边界与异常处理
- 文章 id 在 feed 中缺失：与现有 `missing_favorites` 相同，新增 `missing_archived` 统计可选。
- 同一 id 同时存在：以 hidden > archived > favorites 进行清洗。
- 用户从归档恢复到收藏：应保留之前的摘要与用户编辑内容。
- 批量总结：仅作用于 favorites。

实施步骤（建议顺序）
1) 修改 `server.py`：兼容 `archived` 读写；调整偏好报告口径。
2) 修改 `web/index.html`：新增归档 Tab/按钮与计数占位。
3) 修改 `web/app.js`：
   - state 结构、过滤逻辑、卡片动作、按钮显示逻辑、空状态文案。
4) 修改 `web/stats.js`：偏好统计口径与分母更新。
5) 手动创建/迁移 `web/interactions.json` 示例数据（可选）。

验收标准（可执行检查）
- 收件箱/收藏/归档三视图能稳定切换，筛选结果互斥。
- “归档”操作后文章从收藏消失并出现在归档。
- “恢复到收藏”后文章从归档消失并回到收藏。
- 隐藏优先生效：隐藏后不在任何视图出现。
- “生成 AI 总结”只在收藏视图显示并能正常执行。
- 偏好报告统计结果与选择的口径一致且可解释。

开放问题（需你确认）
- 偏好报告是否将“归档”算作正样本？（推荐：是）
- 归档视图是否需要主题云或其它统计？（推荐：否）
