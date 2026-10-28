# Topic5 客户 28 节点功能等价审计

审计日期：2026-10-23

## 结论

- 口径：以客户原始 `pipeline.v0.2.yaml`、`knowledge/architecture`、`plan_pack` 为事实源，不以节点同名或测试通过代替功能等价。
- 三组复核原始输出合计 P1 18 项、P2 2 项；其中两条 Gateway 终态问题是同一根因；
  部署验证又发现 1 条 startup recovery 风险，最终为下表 18 条 P1、2 条 P2 修复项。
- 当前节点结论：28/28 已完成合同级实现核对；demo 只预填 G1，之后与 full 使用同一严格流水线。
- 未宣称生产 full 真交付完成：仍需真实 SSJH/DataHub/Ark/飞书凭据、真实活动 XLSX 和部署后 Gateway 联调。

## 逐节点矩阵

| # | 节点 | 客户契约重点 | 当前实现证据 | 回归证据 | 结论 |
|---:|---|---|---|---|---|
| 1 | n00 intake | 收集完整 RequirementDraft，产品词可选 | Host 表单采集品牌、活动、周期、平台、查询、业务问题、交付期望、允许动作；产品词支持二级漏斗 | `IntakeFormTest` | 等价 |
| 2 | n01 G1 | 用户确认完整快照，版本可追溯 | 结构化校验后写不可变 `requirement_confirmed`；通过卡回显业务问题、交付期望、允许动作；reject 回 n00 | Host snapshot + HC bridge tests | 等价 |
| 3 | n02 plan | 业务问题映射登记能力；未支持需求在 G2 前阻断 | `objective_capability_map` 逐目标绑定模块；ROI/销售转化等进入 `NEEDS_CAPABILITY_EXTENSION` | `test_plan_compile_blocks_unregistered_roi_objective` | 等价 |
| 4 | n03 G2 | 完整 AnalysisPlan/ExecutionPlan/模块选择确认并冻结 | 校验三 Artifact 同一 bundle hash、精确 refs/version/scope；非 READY 禁止 pass；reject 回 n02 | G2 bridge tests + CLI G2 review_context | 等价 |
| 5 | n04 run_init | 校验输入 schema，冻结输入/规则/Agent/Skill/Tool/模型/环境 | XLSX 校验互动列、分析对象、URL、平台、周期、活动分组与行数对账；ResourceLock 覆盖完整资源清单 | Excel 5 tests；full manifest fail test；CLI full 在 n04 fail closed | 等价 |
| 6 | n05 brand_pull | 品牌活动期 11 维统计，外部调用幂等 | SSJH 真适配；full 禁止模拟数据；effect journal + ledger | SSJH tests；effect journal crash test | 等价 |
| 7 | n06 baseline | 前 30 天不重叠；失败只降级相关模块并生成 DataIssue | 窗口确定性计算；unavailable 进入 `data_issues`，关闭 baseline lift | baseline/DataIssue tests | 等价 |
| 8 | n07 clean | 按发表时间过滤、去重、平台/KOL 派生、可核查对账 | publish_time CST；fingerprint→id→item_id→url；Excel 权威源优先 | clean/aggregate tests | 等价 |
| 9 | n08 ugc_discovery | UGC 模块按方案开关；PGC-only 显式 N/A | 读取 ConfirmedPlan；PGC-only 输出 `not_applicable=true`、空类别 | PGC-only round-trip test | 等价 |
| 10 | n09 annotate_r1 | 情感+相关性全量标注；full 禁止伪标注 | 小引擎/DataHub 路由；AI 情感缺失后续硬失败 | annotate tests | 等价 |
| 11 | n09b opinion_candidates | 基于 R1 活动相关样本生成候选 | 真模型路径；demo/full 均禁止 degraded fallback；禁用措辞已从候选移除 | semantic handler tests | 等价 |
| 12 | n10 N10 | 观点与 UGC 联合确认，绑定候选版本；reject 失效下游 | 同时绑定两类 refs；PGC-only 确认 N/A；reject 回 n08/n09b | HC bridge tests | 等价 |
| 13 | n11 annotate_r2 | 观点/UGC/讨论维度；不适用任务跳过且不阻断 | PGC-only 固定 `ugc_category=N/A`，coverage=`NOT_APPLICABLE`；其余任务继续 | PGC-only round-trip test | 等价 |
| 14 | n12 aggregate | 保留完整品牌块；逐活动计算，不相加；NSR 默认含中性 | `stats_bundle.brand.source` 原样保留 SSJH 11 维证据；campaigns 独立统计 | brand-preservation + multi-campaign tests | 等价 |
| 15 | n13 wordcloud_pull | 每活动独立 overall/brand/product 查询，平台隔离 | query 优先活动级 query/filter_words；多活动缺局部 query 时 full 阻断 | `test_wordcloud_uses_each_campaign_local_query` | 等价 |
| 16 | n14 charts | 品牌和每活动图表；真实词云；交付时上传飞书 media | 品牌/活动 spec 独立；wordcloud 原生 PNG；无 COS 中转 | chart spec/render tests | 等价 |
| 17 | n15 wordcloud_insight | 逐词查帖，结论绑定当前平台证据 | 校验 evidence ID 属于当前帖子集；议题词必须源于频次或帖子正文 | adversarial evidence test | 等价 |
| 18 | n16 report | 固定品牌 1.1-1.4、每活动 2.N.1-2.N.5，证据与图表同源 | Prompt 和 deterministic fallback 均生成完整结构；品牌与热门内容证据可消费 | report/Validator structure tests | 等价 |
| 19 | n17 G3 | 独立 Validator 检查报告、需求、统计、图表、规则并绑定执行身份 | 完整结构扫描；五类输入 hash/ref；Validator version + Task/Run/Attempt + independent execution | Validator + G3 bridge tests；CLI 独立扫描 | 等价 |
| 20 | n18 qc_gate | P0 返修，无 P0 放行，超 3 轮升级 | `has_p0/p0_count` 一致校验；路由确定性 | acceptance C1-C4 | 等价 |
| 21 | n19 fix | 只修问题，保留完整报告，新版本复检 | 完整正文输入模型；校验固定结构和原有 headings；不再截断 8000 字 | long-report tail sentinel test | 等价 |
| 22 | n_hc7 | 超限升级不能豁免 P0 | pass 仅回返修，不直接发布 | acceptance C3-C4 | 等价 |
| 23 | n20 delivery_draft | 生成飞书 draft，绑定报告版本/revision，回读检查 | draft 含 report_ref、delivery_ref 前置、revision、ownership/readback/degraded | delivery tests | 等价 |
| 24 | n21 G4 | 用户确认精确 report/delivery/revision；reject 回修 | confirmation 绑定当前 refs；reject 失效旧交付 ledger，回 n19/G3 | G4 reject + legacy ledger tests | 等价 |
| 25 | n22 user_gate | confirmed/issues/escalate 三路 | 非法/缺状态拒绝；issues 回修；escalate 不替代确认 | decision tests | 等价 |
| 26 | n_user_escalate | 2 工作日提醒，再 1 工作日升级；不自动提交 | reminder 优先；升级日从实际 reminder 重算；原子 claim | store timeout tests + acceptance A5-A6 | 等价 |
| 27 | n23 delivery_commit | full 要求用户绑定、所有权、链接、revision、非降级全部成立 | fail-closed 校验 acceptance/delivery/report refs、ownership、link/readback | full ownership/stale-ref tests | 等价 |
| 28 | n24 handoff | 实时复读 revision，同链校验后才可生产接收 | full 要求 live revision 非空且相等；`FINAL_RESULT` 由 durable state 生成；Gateway 只认同一对象 | full revision-none test + terminal splice tests + CLI E2E | 等价 |

## 本轮缺陷闭环

| ID | 严重度 | 原问题 | 修复状态 |
|---|---|---|---|
| F1 | P1 | n02 未支持业务问题仍 READY | 已修复：目标-能力映射与 capability extension 门 |
| F2 | P1 | PGC-only 仍执行 UGC 链 | 已修复：n08/N10/n11 显式 N/A |
| F3 | P1 | n04 输入 schema 缺互动、分析对象、URL、范围 | 已修复：文件级 schema/值域/对账 |
| F4 | P1 | ResourceLock 资源清单不完整 | 已修复：DAG/规则/Agent/Environment/Skills/Tools/model |
| F5 | P2 | G1 快照漏业务问题/交付期望/允许动作 | 已修复 |
| M1 | P1 | n12 丢完整品牌块 | 已修复 |
| M2 | P1 | n13 多活动复用全局 query | 已修复 |
| M3 | P1 | n15 接受伪造 evidence ID/词 | 已修复 |
| M4 | P1 | n17 只查四个大标题且输入未绑定 | 已修复 |
| M5 | P1 | n19 仅送前 8000 字并接受缩水报告 | 已修复 |
| D1 | P1 | G4 reject 对 dict ledger ref 崩溃 | 已修复 |
| D2 | P1 | full handoff 未强制 live revision/ownership/delivery ref | 已修复 |
| D3 | P1 | Gateway 可拼接不同 JSON 或信任 Agent 自报终态 | 已修复：唯一 `FINAL_RESULT` |
| D4 | P1 | `task.accepted` 在 T5 受理前提前写入 | 已修复：首次 G1 时写入 |
| D5 | P1 | 错过第 2 日后直接升级，跳过提醒 | 已修复 |
| D6 | P1 | HC stale 检查与 resolve 非原子 | 已修复：事务 claim |
| D7 | P1 | 同秒 HC latest 可能返回旧版本 | 已修复：`created_at DESC, id DESC` |
| D8 | P1 | 外部成功后 ledger 保存前崩溃会重复执行 | 已修复返回后窗口：durable effect journal |
| D9 | P2 | 标称 full 的 helper 强制改为 demo | 已修复：显式 mode；原测试更名 |
| D10 | P1 | 单个历史 Job 恢复可能阻塞串行 Gateway 启动恢复 | 已修复：逐 Job 30 秒有界恢复，后续 Job 继续 |

## 验证

- `topicX` 环境 orchestrator：142/142。
- Host/Gateway stdlib 与动态隔离测试：46/46。
- Acceptance：35/35。
- demo/full 仅 G1 表单是否预填不同；进入 n04 后使用同一节点与门禁。
- 严格负向：两种模式缺真实挂载输入时都在 n04 fail closed。
- `git diff --check`、`compileall`、关键 Bash `bash -n` 均通过。

## 剩余生产验证

1. 使用真实活动 XLSX 与生产 Vault 凭据跑 full，验证非降级 SSJH/DataHub/Ark/飞书链。
2. 部署后验证真实飞书所有权转移、链接权限和 live revision 复读。
3. Effect journal 消除了 handler 返回后的崩溃窗口；外部调用进行中发生进程死亡仍依赖外部幂等键或 reconcile API。
