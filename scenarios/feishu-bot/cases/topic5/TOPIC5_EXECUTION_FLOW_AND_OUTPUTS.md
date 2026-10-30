# Topic5 当前执行流程与输出说明

> 口径日期：2026-10-30  
> 适用范围：当前 MA 版 Topic5 营销活动复盘报告  
> 事实源：`ma-resources/io/contracts/pipeline.v0.2.yaml`、`pipeline-orchestrator/handlers.py`、
> `topic5_runner.py`。若本文与历史方案或客户原始资源包不一致，以当前代码和 DAG 为准。

## 1. 先说明容易产生“出入”的地方

当前运行链路不是客户原始资源包的原样部署，而是适配 MA 后的
**薄 Agent + Durable Workflow** 实现。因此，旧文档中的角色数量、节点数量和人工关卡展示
与现网会有差异。

| 对比项 | 客户原始设计/旧文档 | 当前实际实现 |
| --- | --- | --- |
| 物理 Agent | ProjectAgent、Topic5Agent、多个 Subagent、Validator | 2 个 MA Agent：`topic5-coordinator`、`topic5-validator` |
| Subagent | Discovery、Opinion、Insight、Narrative 分别运行 | 保留为 DAG 逻辑 owner，由编排器 handler 执行，不单独创建 MA Agent |
| 节点数量 | 部分文档仍写 28 节点 | 当前权威 DAG 为 29 节点 |
| G3 | 容易被理解为用户审核卡 | 独立 Validator 自动质检；`p0_count == 0` 自动放行，不向用户发卡 |
| Excel 上传 | 容易被理解为 G1/G2 表单附件 | G2 方案通过后，Gateway 进入 `wait_input`，用户在同一话题单独上传 `.xlsx` |
| 图表交付 | 旧方案提到 COS/CDN | 当前在沙箱生成本地 PNG，n20 发布时上传为飞书文档 media |
| 流程状态 | 依赖 Agent 对话上下文 | Run、节点状态、Artifact 版本和审核记录持久化，可局部重试和冷恢复 |

## 2. 系统执行边界

| 组件 | 责任 |
| --- | --- |
| 飞书 Gateway | 接收触发消息和文件、创建/恢复 MA Session、发送进度卡和审核卡、同步 Job 状态 |
| `topic5-coordinator` | 薄前台；只负责启动/续跑编排器、透传人工决定、输出关卡或终态 |
| `pipeline-orchestrator` | 读取 29 节点 DAG，执行数据处理、状态推进、Artifact 版本化、局部重试和 QC 循环 |
| `topic5-validator` | 独立读取冻结版本，执行机器规则扫描和全文语义检查，输出 `qc_report` |
| 用户 | 提交 G1、确认 G2、上传活动明细 Excel、处理条件人工关卡、完成 G4 终验 |

Coordinator 不负责计算指标、撰写报告或判断质检是否通过；这些动作都由编排器和 Validator
按确定性契约执行。

## 3. 端到端流程与阶段输出

| 阶段 | 责任主体 | 输入 | 执行节点/动作 | 持久化输出 | 用户可见结果 |
| --- | --- | --- | --- | --- | --- |
| 0. 任务接入 | Gateway | 飞书消息：`复盘报告`、`营销复盘` 或 `活动复盘` | 创建 Topicx Control Task、Topic5 Job 和 MA Session；启动 Coordinator | `task_id`、`job_id`、`session_id` | 同一话题中的进度卡 |
| 1. 需求确认 | 用户 + 编排器 | 触发消息、G1 表单 | `n00_intake` 生成问卷；`n01_hc_requirement` 等待 G1 | `requirement_snapshot`、`requirement_confirmed` | G1 需求确认卡；通过后进入方案编制 |
| 2. 方案编制与确认 | 编排器 + 用户 | `requirement_confirmed` | `n02_plan` 编制分析/执行方案；`n03_hc_plan` 等待 G2 | `analysis_plan`、`execution_plan`、`selected_modules` | G2 方案确认卡 |
| 3. 等待活动文件 | Gateway + 用户 | 已通过的 G2 | Gateway 将 Job 置为 `wait_input`；用户在原话题上传 `.xlsx` | Session 文件挂载引用 | “数据文件已挂载”回执 |
| 4. 冻结生产输入 | 编排器 | G2 方案、Excel 挂载引用 | G2 resume 生成 `confirmed_plan`；`n04_run_init` 校验 Excel schema 并冻结资源 | `confirmed_plan`、`run_record`、`resource_lock`、`context_snapshot` | 进度卡进入生产 Run |
| 5. 并行取数与清洗 | 工具 + 编排器 | `confirmed_plan`、活动 Excel | `n05_brand_pull`、`n06_brand_baseline`、`n13_wordcloud_pull` 并行；`n07_clean` 清洗活动明细 | `brand_stats`、`brand_stats_baseline`、`wordcloud_data`、`cleaned_data` | 进度卡显示取数/清洗进度 |
| 6. 两轮 AI 标注 | 编排器 + 标注工具 | `cleaned_data` | `n08_ugc_discovery`；`n09_annotate_r1`；`n09b_opinion_candidates`；`n10_hc_opinion`；`n11_annotate_r2` | `ugc_category_list`、`annotated_round1`、`opinion_candidates`、`confirmed_opinion_labels`、`annotated_data` | 无歧义时自动通过；有歧义时展示 N10 标签确认卡 |
| 7. 统计、图表与洞察 | 编排器 + 工具 | 标注数据、品牌数据、词云数据 | `n12_aggregate`、`n14_charts`、`n15_wordcloud_insight` | `stats_bundle`、`chart_urls`、`wordcloud_insight` | 进度卡显示聚合、图表和洞察状态 |
| 8. 报告撰写 | 编排器 | 统计、图表、词云洞察 | `n16_report` 生成内容层报告 | `report_md` | 暂不直接交付；先进入独立质检 |
| 9. 独立质检与返修 | Validator + 编排器 | `report_md`、`chart_urls`、`stats_bundle` | `n17_validate` → `n18_qc_gate`；有 P0 时 `n19_fix` 后回到 n17 | `qc_report`；返修后生成新版 `report_md` | G3 自动执行；超过最大轮次或外部权限异常时展示 HC7 |
| 10. 飞书草稿与发布后检查 | 编排器 + 飞书 API | 已通过质检的报告和图片 | `n20_delivery_draft` 创建飞书文档、上传图片、转移所有权并回读；`n20_delivery_gate` 路由 | `delivery_draft`、`external_receipts`、`feishu_doc`、更新后的 `qc_report` | G4 卡片展示飞书文档链接供用户终验 |
| 11. 用户终验 | 用户 + 编排器 | `delivery_draft` | `n21_hc_user` → `n22_user_gate` | `user_confirmation` | 通过则提交；打回则回到 n20；超时则进入升级 |
| 12. 交付闭环 | 编排器 + Gateway | 飞书文档、用户确认 | `n23_delivery_commit` 做发布后最终检查；`n24_handoff` 校验闭环 | `delivery_record`、`task_result`、`handoff` | 最终进度卡、飞书文档 URL 和完成状态 |

## 4. 主流程图

```text
飞书触发
  ↓
G1 需求确认
  ↓
方案编制 → G2 方案确认
  ↓
wait_input：用户上传活动明细 Excel
  ↓
n04 冻结输入与校验
  ↓
品牌活动期取数 ─┐
品牌基准期取数 ─┼→ 清洗 → 两轮标注 → 统计聚合
词云取数 ──────┘                     ↓
                         图表 + 词云洞察
                                  ↓
                              报告撰写
                                  ↓
                    G3 独立 Validator 自动质检
                      ├─ P0 → 修复 → 重新质检
                      └─ 通过
                                  ↓
                    飞书草稿 + 发布后机器检查
                                  ↓
                             G4 用户终验
                      ├─ 打回 → 重新生成草稿
                      ├─ 超时 → 升级
                      └─ 通过
                                  ↓
                       Delivery Commit + Handoff
```

## 5. 人工关卡和自动关卡

| 关卡 | 放行主体 | 是否固定出现 | 确认对象 | 结果 |
| --- | --- | --- | --- | --- |
| G1 | 用户 | 是 | 结构化业务需求 | 冻结 `requirement_confirmed` |
| G2 | 用户 | 是 | 分析方案、执行方案、模块选择 | 通过后等待 Excel；文件到位后冻结 `confirmed_plan` |
| N10 | 系统或用户 | 否 | 观点/UGC 标签策略 | 无歧义自动确认，有歧义才发卡 |
| G3 | 独立 Validator | 是 | 当前版本 `report_md` | `p0_count == 0` 自动放行，不向用户发卡 |
| HC7 | 用户/项目负责人 | 否 | QC 超限或外部权限异常 | 人工决定继续、降级或终止 |
| G4 | 用户 | 是 | 飞书文档草稿及其 revision | 通过、打回或超时升级 |
| USER_ESCALATE | Supervisor | 否 | G4 长时间未响应 | 升级处理后进入闭环 |

## 6. 关键输入

### 6.1 G1 业务需求

主要包括品牌、活动名称、活动周期、平台、品牌/活动/产品关键词、内容范围、业务问题和交付期望。
G1 不接收活动明细文件。

### 6.2 活动明细 Excel

Excel 在 G2 通过后单独上传。生产 Run 只消费写入 `confirmed_plan.source_file` 的冻结引用。

- 初次上传成功后，Job 从 `wait_input` 进入生产 Run。
- 流程已经进入后续节点时，再上传其他文件会被拒绝，不会覆盖当前输入。
- 只有 `n04_run_init` 因 `excel_source` 格式或字段校验失败时，才允许上传修正版。
- 修正版使用新的唯一挂载路径，并生成新版 `confirmed_plan`，随后只重试 n04。
- 其他节点失败时禁止换源，避免输入和既有产物不一致。

### 6.3 受控外部数据

`n05`、`n06`、`n13` 通过 `SSJH-Social-Stat` 获取品牌活动期、基准期和词云数据。
这些数据与用户 Excel 责任不同：Excel 用于活动明细分析，SSJH 用于品牌及基准对比。

## 7. 最终输出

### 7.1 用户最终获得

1. 一份完成终验的飞书营销活动复盘文档；
2. 报告中的统计表、趋势图、情感/观点/UGC/KOL 等分析和词云议题；
3. 任务完成卡片及最终文档 URL。

### 7.2 系统保留的可审计输出

| 类别 | 主要 Artifact |
| --- | --- |
| 需求与方案 | `requirement_confirmed`、`analysis_plan`、`execution_plan`、`selected_modules`、`confirmed_plan` |
| 数据与标注 | `cleaned_data`、`annotated_round1`、`confirmed_opinion_labels`、`annotated_data` |
| 分析结果 | `stats_bundle`、`chart_urls`、`wordcloud_insight`、`report_md` |
| 质检 | `qc_report`，绑定被检查的 Report Artifact 版本 |
| 交付 | `delivery_draft`、`external_receipts`、`feishu_doc`、`delivery_record` |
| 闭环 | `task_result`、`handoff` |

Artifact 不原地覆盖。内容变化会生成新版本，因此返修前后的报告、质检结果和交付记录可以追溯。

## 8. 失败、重试与恢复

| 场景 | 当前处理 |
| --- | --- |
| 节点临时失败 | 按节点 retry 配置有限重试；失败后可从当前节点显式重试 |
| 外部副作用状态未知 | 先对账再重试，避免重复拉取、重复标注或重复创建文档 |
| Gateway 重启 | 从 SQLite Job 状态和沙箱 Run checkpoint 恢复，不从头执行 |
| n04 Excel 校验失败 | 允许上传修正版，更新冻结源引用，只重试 n04 |
| 后续节点失败 | 保留原输入和既有 Artifact，禁止换 Excel |
| Validator 发现 P0 | 修复后生成新版报告，再次独立质检 |
| QC 超过最大轮次 | 进入 HC7 人工升级 |
| G4 打回 | 回到交付草稿阶段处理，并保持版本链 |

## 9. 文档维护规则

1. 节点、输入、输出和路由以
   `ma-resources/io/contracts/pipeline.v0.2.yaml` 为准。
2. 节点实际业务行为以
   `ma-resources/skills/pipeline-orchestrator/handlers.py` 为准。
3. 飞书侧状态、卡片和文件上传行为以 `topic5_runner.py`、`topic5_hitl.py` 为准。
4. 修改 DAG、关卡、Artifact 或用户可见流程时，必须同步更新本文。
5. 客户原始 `assets_from_customer/` 只作为迁移历史，不作为当前运行口径。
