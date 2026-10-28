# Topic 5 · 营销活动复盘报告

Topic 5 是一个运行在火山方舟 Managed Agents（MA）上的飞书 Bot 场景。用户在飞书中发送触发词后，系统会自动完成多平台社媒数据取数、清洗、AI 标注、指标聚合、图表渲染、报告撰写、质检和飞书文档交付。

架构采用**薄 Agent + Durable Workflow**：飞书前台 Agent 只负责「启动 / 抛人工关卡 / 续跑 / 交终态」四件事，所有业务编排与 LLM 语义调用都沉淀到沙箱内的确定性编排器（`pipeline-orchestrator`），由它驱动 29 节点 DAG，天然支持幂等重放、局部重试和冷进程续跑。

## 文档导航

- [快速调试与完整配置](./TOPIC5_DEBUG_QUICKSTART.md)
- [客户文件迁移映射](./CUSTOMER_TO_MA_FILE_MAPPING.md)
- [架构说明（交互式）](./topic5_ma_architecture.html)
- [Pipeline 契约 v0.2](./ma-resources/io/contracts/pipeline.v0.2.html)

## 运行模式

| 飞书指令        | G1 表单       | 流水线数据与质量标准 | 审核流程                   |
| --------------- | ------------- | -------------------- | -------------------------- |
| `复盘报告 demo` | 自动预填示例值 | 清洗后稳定抽样最多 500 条，质量门禁不降级 | G1 → G2 → N10（仅歧义）→ G3（自动）→ G4 |
| `复盘报告 full` | 空表           | 全量数据、严格生产标准 | G1 → G2 → N10（仅歧义）→ G3（自动）→ G4 |

- 触发词支持 `复盘报告` / `营销复盘` / `活动复盘`；`demo` 表示 G1 自动预填，`full` 表示 G1 空表，不带模式时默认 `full`。
- `demo` 模式的 G1 需求问卷会预填瑞幸联名活动测试值（日期、平台、关键词、过滤词和内容范围）；`full` 模式保持空表。
- G1 提交后两种模式共用同一 DAG 和质量门禁；`demo` 在活动明细清洗后超过 500 条时做确定性抽样，`full` 保留全量。
- `demo` 抽样会把总体量、样本量和抽样方法写入 `clean_record`，报告必须披露“仅供参考、不代表全量结论”。
- Topic5 以飞书话题为任务隔离边界：不同话题可并发运行独立 MA Session；同一话题最多一个活跃 Job。主时间线每条新触发消息都会创建独立话题，不受其他任务影响。

## 开始使用

首次部署、环境变量、飞书权限、资源上传、Gateway 启动和故障排查统一维护在[快速调试与完整配置](./TOPIC5_DEBUG_QUICKSTART.md)，本文件不重复展开。

完成部署后，建议先发送：

```text
复盘报告 demo
```

Bot 会基于触发消息创建独立话题，按以下顺序推进：

1. 在 G1 卡片填写并提交品牌、活动周期、平台、取数关键词、产品关键词和内容范围；
2. 审核并通过 G2 分析与执行方案；
3. 在同一话题发送活动明细 `.xlsx` 文件；
4. 收到“数据文件已挂载”回执后，任务自动进入生产 Run。

Excel 是 n07 活动明细清洗的输入；n05/n06/n13 的品牌活动期、基准期和词云仍由
`SSJH-Social-Stat` 获取。验收时固定出现 **G1 / G2 / G4** 用户确认；N10 仅在标签有歧义时出现；
G3 由独立 `topic5-validator` 子 Agent 自动质检并按 `p0_count==0` 放行，不向用户发审核卡。
G2 通过到文件上传之间由 Gateway 持久化为 `wait_input`，不是额外的业务审核门。

资源或代码更新后，在仓库 `scenarios/feishu-bot/cases/topic5/` 目录下执行：

```bash
./update_ma.sh
```

该脚本会完成单元测试、Skill 打包上传、Environment/Memory 更新、Agent 重建和资源 ID 回写；Gateway 仍需手动重启。详见[快速调试与完整配置](./TOPIC5_DEBUG_QUICKSTART.md)。

## 系统组成

Topic 5 只注册 **2 个 MA Agent**（遵循「避免过度拆分子 Agent」的架构偏好）：

| 组件               | 职责                                                              |
| ------------------ | ----------------------------------------------------------------- |
| `topic5-coordinator` | 薄前台：启动 `run_pipeline.py`、抛出人工关卡 JSON、续跑、交终态   |
| `topic5-validator`   | 独立质检子 Agent：按客户完整规则适配表执行脚本全量扫描 + Agent 全文语义检查 |

> DAG 节点上的 owner 标签（`DiscoverySubagent` / `OpinionSubagent` / `InsightSubagent` / `NarrativeSubagent`）是**逻辑归属**，由确定性编排器里的 handler 实现，**不**各建独立 MA Agent。这样避免了子 Agent 冷启动与 IPC 开销，也让编排逻辑连贯可测。

主要 Skill：

| Skill                              | 阶段与职责                                                       |
| ---------------------------------- | --------------------------------------------------------------- |
| `pipeline-orchestrator`            | 确定性 DAG 引擎 + 全部节点 handler（清洗/聚合/语义/标注/图表/交付）|
| `SSJH-Social-Stat`                 | n05/n06/n13 社媒品牌统计与词云取数                               |
| `blueai-datahub-skill`             | n09/n11 DataHub 批量 AI 标注（情感 / 观点 / 属性）              |
| `blueai-yuqing-echarts-skill-new`  | n14 ECharts 图表真渲染（沙箱内 resvg）                          |
| `report-cover-skill`               | n14 报告封面生成                                                |
| `topic5-validator`                 | n17 自动质检（scan_report.py 机器全检 + Agent 全文语义检查）    |

## 29 节点 DAG 与人工关卡

```
A 意图    n00_intake → [G1] n01_hc_requirement
B 方案    n02_plan → [G2] n03_hc_plan
C 初始化  n04_run_init
D 取数    n05_brand_pull · n06_brand_baseline
E 清洗    n07_clean
F 发现    n08_ugc_discovery
G 标注一  n09_annotate_r1 → n09b_opinion_candidates → n10 策略自动确认（歧义才触发 N10）
H 标注二  n11_annotate_r2
I 聚合    n12_aggregate · n13_wordcloud_pull · n14_charts · n15_wordcloud_insight
J 撰写    n16_report
K 质检    n17_validate → n18_qc_gate →（不过）n19_fix / [HC7] n_hc7_escalate
L 交付    n20_delivery_draft → [G4] n21_hc_user → n22_user_gate → n_user_escalate
M 落定    n23_delivery_commit → n24_handoff
```

固定人工门：`G1`（需求确认）· `G2`（方案确认）· `G4`（用户终验）。条件人工门：
`N10`（标签歧义）· `HC7`（质检超限）· `USER_ESCALATE`（终验升级）。`G3` 是独立
Validator 自动门，不是用户人工门。

## 口径红线（不可违反）

- **NSR 分母默认含中性**（`nsr_denominator_includes_neutral=true`，参数化读 `state.params`，勿硬编码）。
- 指标白名单：**条均互动量 / 品牌提及率 / 产品提及占比 / NSR**；禁用措辞：人均 / 转化率 / 转化 / 性价比。
- 提及漏斗嵌套：产品提及是品牌提及子集，占比 ≤ 1。
- 日期取发表时间 `publish_time`（epoch ms，CST）；去重键 `fingerprint → id → item_id → url`。
- 多活动逐个计算**不相加**；活动相关行缺 AI 情感**直接报错不兜底**。

## 维护约定

- `ma-resources/` 是部署资源的唯一源码目录；不要直接修改 `tools/out/` 中的 ZIP。
- Skill/Prompt 改动统一通过 `./update_ma.sh` 走打包→上传→重建；Gateway 需手动重启。
- 真实凭证（`DATAHUB_API_KEY` / `SSJH_API_TOKEN` / `REPORT_COVER_API_KEY` 等）只写 `config.env`，入库文件一律 `${...}` 占位。
- `demo` 仅让 G1 表单自动预填；进入流水线后与 `full` 使用完全相同的 fail-closed 标准，默认模式为 `full`。
- 图表只在沙箱内渲染本地 PNG，n20 通过飞书 `medias/upload_all` 原生插图，不依赖外部对象存储。
- topic5 **走飞书文档交付**，不走妙搭 Miaoda，故无用户 OAuth / artifact-sync / token keepalive。
