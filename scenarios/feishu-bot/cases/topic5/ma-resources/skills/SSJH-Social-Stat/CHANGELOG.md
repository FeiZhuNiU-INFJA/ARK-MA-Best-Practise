# CHANGELOG — SSJH-Social-Stat

## v1.7.2 (2026-09-10)

### 变更：合并两份互不完整的变更历史

- SKILL.md 末尾内嵌的 `## CHANGELOG` 段（最新只到 v1.7.0）整体迁入本文件，标题层级由 `###` 降为 `##`
- SKILL.md 该段替换为指向本文件的一行指针

**为什么改**：v1.7.1 的条目只写进了 `CHANGELOG.md`，而 v1.7.0 及之前的历史只存在于 SKILL.md 内嵌段。
两份各缺一半，读者打开哪个文件决定他认为当前版本是 1.7.0 还是 1.7.1。现以 `CHANGELOG.md` 为单一来源。

## v1.7.1 (2026-09-10)

### 变更：description 增加「数说取数必须走本 Skill」强制条款

**背景**：description 此前已把「数说取数」列入触发词，也已用 DO NOT trigger 排除了原始帖子采集与
LLM 打标两类场景，但只是"应当触发"级别的描述，没有排他性——用户明确点名「数说取数」时，仍可能
被改用社媒采集类 Skill 或临时手写脚本替代。用户要求不新建规则文件，直接在本 Skill 的 description
中写明强制性。

- `description` 的【直接触发关键词】之后新增【强制】条款：点名"数说取数"/"数说"/"数说聚合"/
  "datastory" 时必须使用本 Skill，禁止换用采集类 Skill / 手写脚本 / 其他数据源；**本 Skill 报错时
  也不得静默换路**，须如实报错并等待指示
- 版本 1.7.0 → 1.7.1（顶层 `version` 与 `metadata.version` 同步）
- 本次不改任何执行逻辑，仅 description 与版本号

---

## 以下为 v1.7.2 之前内嵌在 SKILL.md 中的历史条目（2026-09-10 迁入）

## v1.7.0 (2026-08-06)
- 修复平台聚合层错误合并（`total_volume`/`total_interaction`/`total_sentiment`/`data_type`/`sentiment_dist`/`volume_type`/`region_dist` 共 7 步）：原按 API `source` 分组只取 `pks[0]` 的 siteIds，导致同 `source` 下其余平台（如 douyin/kuaishou/shipinhao 均为 `shortVideo`）被静默丢弃；改为逐平台单独调用后求和/合并。真实 API 验证发现 `total_sentiment` 不能直接跨平台/跨时间段求和（其值是 `(正面-负面)/(正面+负面)` 净情感度百分比字符串，非可加计数），已改为从已正确聚合的 `sentiment_dist` 正负面计数派生
- `skill.py.run()` 新增 366 天自动分段：跨度超限时自动按 366 天切片、逐段取数、合并为单一完整结果，调用方无需感知分段逻辑
- `export_excel.py` 新增摘要形态检测：输入命中 `top_keywords`/`article_digest`/`top_regions` 等 summary 专属键但缺少 `cloud_word`/`articles`/`region_dist` 完整键时抛出异常，禁止导出为正式 Excel
- 删除 `scripts/skill.py`、`scripts/export_excel.py`（无引用的死代码副本）
- 归档 5 个一次性业务脚本至 `scripts/archive/`：`run_cxk.py`、`batch_monthly_top.py`、`fetch_negative_posts.py`、`export_excel_merged.py`、`shengnong_analysis.py`（各追加禁止复制作为模板的警告说明）
- 修正「使用方式」章节：移除与「对话流程」矛盾的裸 `skill.run()` 调用示例，改为指向 `scripts/run.py --mode execute` 标准入口

## v1.6.0 (2026-07-14)
- 新增 `scripts/send_plan_card.py`：飞书原生卡片发送脚本
  - 从 CC_SESSION_KEY 自动解析 chat_id（或通过 `--chat-id` 传入）
  - 卡片双列布局展示基本参数（关键词/排除词/平台/时间范围）
  - 4 项高影响参数（msg_type / content_mode / filter_ad / no_ocr）含当前值 + 可选值 + 推荐说明
  - 支持 `--dry-run` 仅打印 JSON 不发送，便于调试
- 新增 `## 对话流程` 节，规范 Agent 执行行为：
  - Step 1：用户已提供参数时可跳过收集，直接提取进入 Step 2
  - Step 2：必须通过 `send_plan_card.py` 发飞书卡片确认，**不可跳过**
  - Step 3：收到「确认」后调用 `run.py --mode execute`
  - filter_ad 参数提升至 Step 1 收集阶段

## v1.5.0 (2026-07-02)
- 新增 `scripts/run.py`：交互式三步取数脚本（必填收集 → 方案确认 → 执行取数）
  - `--mode plan`（默认）：打印取数方案模板，含 3 个高影响参数说明
  - `--mode execute`：逐 keyword 调用 skill.run(save=True)，多组 keyword 合并摘要保存
  - 支持 `--msg-type / --content-mode / --filter-ad` 三个可调参数
  - 结果默认保存至 `Outputs/`，多 keyword 时额外生成 `merged_*.json`
- 备份旧脚本为 `scripts/run_cxk.py.bak_*`（按日期命名，不删除）
