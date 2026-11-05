# Topic 5 · 营销活动复盘报告

Topic 5 是一个运行在火山方舟 Managed Agents（MA）上的飞书 Bot 场景。用户在飞书中发送触发词后，系统会自动完成多平台社媒数据取数、清洗、AI 标注、指标聚合、图表渲染、报告撰写、质检和飞书文档交付。

架构采用**薄 Agent + Durable Workflow**：飞书前台 Agent 只负责「启动 / 抛人工关卡 / 续跑 / 交终态」四件事，所有业务编排与 LLM 语义调用都沉淀到沙箱内的确定性编排器（`pipeline-orchestrator`），由它驱动 29 节点 DAG，天然支持幂等重放、局部重试和冷进程续跑。

## 保持Agent ID的资源更新

已有部署在代码或提示词调整后，可依次打包、上传改动Skill，再执行
`tools/update_agents_in_place.py`。它使用`ma-resources/created_ids.json`定位现有
Coordinator/Validator，带当前版本原地更新并回读校验。支持`--dry-run`只读预检。
凭据从当前shell环境读取；不删除Agent，不更新Environment或Memory Store，不重启Gateway。
Agent ID不变，因此新建Session直接使用更新后的版本；已有Session不自动刷新。

N19所需完整Validator扫描器由`pack_skills.sh`从权威源码注入编排包。

Topic5 三份字体统一由 `tools/prepare_fonts.py` 管理：ECharts 使用 Source Han Sans SC
Regular/Bold 2.004R，词云使用 Noto Sans CJK Bold。字体只保存在仓库外缓存和临时部署目录；
`pack_skills.sh` 校验 SHA-256 后加入对应 Skill ZIP，`upload_skills.py` 随包上传 MA，
运行时无需下载。`update_ma.sh` 的单测也使用相同流程准备临时字体资源。
旧`update_ma.sh`仍是全量重建入口，其Agent ID变化及Gateway重启要求只适用于该旧入口。

## 文档导航

- [当前执行流程与阶段输出（客户核对版）](./TOPIC5_EXECUTION_FLOW_AND_OUTPUTS.md)
- [Pipeline 交互式全景（节点点击查看输入输出）](./topic5_pipeline_overview.html)
- [快速调试与完整配置](./TOPIC5_DEBUG_QUICKSTART.md)
- [客户文件迁移映射](./CUSTOMER_TO_MA_FILE_MAPPING.md)
- [架构说明（交互式）](./topic5_ma_architecture.html)
- [Pipeline 契约 v0.2](./ma-resources/io/contracts/pipeline.v0.2.html)
- [业务知识迁移审计](./KNOWLEDGE_MIGRATION_AUDIT.md) · [Memory 迁移需求（暂缓实施）](./KNOWLEDGE_MEMORY_REQUIREMENT.md)

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

## 暂停与继续

- “暂停任务”发送 `user.interrupt`；Session 回到 `idle` 后状态记为 `paused`，不删除
  Session、轨迹或产物。
- `paused` 任务仍占用原话题，但 Gateway 重启时不会自动恢复执行。
- 同一话题成员可点击“继续任务”，或发送一条普通消息，恢复同一 MA Session。
- Topic6 的“停止任务”是独立的不可逆删除语义，不复用 Topic5 暂停状态。

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
4. 收到“数据文件已校验”回执后，任务自动进入生产 Run。

活动 Excel 默认直接从飞书附件下载到 MA 沙箱，避免依赖方舟 Files 存储配额。
Gateway 先下载并校验文件容器，持久化原消息引用与 SHA256；流水线再下载同一附件，
校验哈希后缓存到 `/workspace/topic5_repro_inputs/`，重试与续跑复用经校验的缓存。
G1 需求 JSON 默认随控制消息原样传入，也不使用 Files 上传。沙箱仍需具备
`FEISHU_APP_ID/FEISHU_APP_SECRET`（或可用用户 token）。
可在 Gateway 环境设置 `TOPIC5_INPUT_TRANSPORT=ark_files` 恢复旧输入上传/挂载方式；
默认值为 `feishu_resource`。Coordinator v6 起新建 Session 使用直读；
旧 Session 按其冻结的技能版本继续使用旧输入契约。

Excel 是 n07 活动明细清洗的输入；n05/n06/n13 的品牌活动期、基准期和词云仍由
`SSJH-Social-Stat` 获取。验收时固定出现 **G1 / G2 / G4** 用户确认；N10 仅在标签有歧义时出现；
G3 由独立 `topic5-validator` 子 Agent 自动质检并按 `p0_count==0` 放行，不向用户发审核卡。
G2 通过到文件上传之间由 Gateway 持久化为 `wait_input`，不是额外的业务审核门。

附件按当前业务关卡处理：仅 G2 已确认后的 `wait_input` 阶段接收活动 Excel；
只有 n04 源文件校验失败可上传修正版。停在 N10 时提示先确认观点与 UGC 标签，
G1/G2/G4 等关卡分别提示当前待办，正常运行时提示等待后续通知。此类操作提示不会被
包装为“任务处理失败”，不会确认审核或替换原数据；暂停任务也不会因发送附件而自动续跑。
图文消息中的图片不作为活动明细，多个资源中优先选择文件；文件下载/上传期间若关卡
已推进，写入源文件引用时再次以 `wait_input` 状态为条件，避免覆盖冻结的输入。

已有部署的 Skill 或 Prompt 更新，在仓库根目录执行（先加载配置到 shell）：

```bash
bash scenarios/feishu-bot/cases/topic5/tools/pack_skills.sh
python scenarios/feishu-bot/cases/topic5/tools/upload_skills.py --only pipeline-orchestrator
python scenarios/feishu-bot/cases/topic5/tools/update_agents_in_place.py --only coordinator
```

示例针对编排器改动；其他 Skill 按其 key 上传，Validator 改动需更新相应 Agent。
Agent ID 保持不变，新建 Session 使用新版本，Gateway 无需重启。
首次创建或全量重建才使用 `update_ma.sh`。详见[快速调试与完整配置](./TOPIC5_DEBUG_QUICKSTART.md)。

## 系统组成

Topic 5 只注册 **2 个 MA Agent**（遵循「避免过度拆分子 Agent」的架构偏好）：

| 组件               | 职责                                                              |
| ------------------ | ----------------------------------------------------------------- |
| `topic5-coordinator` | 薄前台：启动 `run_pipeline.py`、抛出人工关卡 JSON、续跑、交终态   |
| `topic5-validator`   | 独立质检子 Agent：按客户完整规则适配表执行脚本全量扫描 + Agent 全文语义检查 |

> DAG 节点上的 owner 标签（`DiscoverySubagent` / `OpinionSubagent` / `InsightSubagent` / `NarrativeSubagent`）是**逻辑归属**，由确定性编排器里的 handler 实现，**不**各建独立 MA Agent。这样避免了子 Agent 冷启动与 IPC 开销，也让编排逻辑连贯可测。

N10 原审核卡按 UGC 参与方式、观点主题分组，每个候选可展开查看定义、边界与原帖片段。
N08/N09b 生成这些信息，代码按 `quote_id` 复制输入字段原句，保留帖子 ID、类型、链接及
来源版本；未知或改写引用报错，缺证据明确提示。样本数单独披露，不把抽样当全量统计。
审核通过会冻结名称、定义、边界和引用，第二轮的小引擎与 DataHub 均使用确认口径。
旧任务仅有标签名时显示信息缺失，不自动补写或重生成候选；修改本地代码不会更新已运行
MA Session 的资源快照。完整字段契约见
[编排器约束](ma-resources/skills/pipeline-orchestrator/SKILL.md)。

主要 Skill：

| Skill                              | 阶段与职责                                                       |
| ---------------------------------- | --------------------------------------------------------------- |
| `pipeline-orchestrator`            | 确定性 DAG 引擎 + 全部节点 handler（清洗/聚合/语义/标注/图表/交付）|
| `SSJH-Social-Stat`                 | n05/n06/n13 社媒品牌统计与词云取数                               |
| `blueai-datahub-skill`             | n09/n11 DataHub 批量 AI 标注（情感 / 观点 / 属性）              |
| `blueai-yuqing-echarts-skill-new`  | n14 ECharts 图表真渲染（沙箱内 resvg）                          |
| `report-cover-skill`               | n14 报告封面生成                                                |
| `wordcloud-insight`                | n14 客户 v0.2.6 双版词云；n15 全词表分析、原帖全文证据及 compose |
| `topic5-validator`                 | n17 自动质检（scan_report.py 机器全检 + Agent 全文语义检查）    |

词云链路为 `n12/n13 → n14 → n15 → n16`。分析保留全部原词与数值；渲染仅按客户 Top100 规则选词。n14 使用客户自然填隙布局和六色配色，默认 1400×880，同时生成透明与白底图。n15 按候选议题抽样阅读全文、按需补读反例，校验 schema2 原帖字段定位与关键原句，组装分析、预览及内部证据。无可用正文时正常按词表分析；已提供正文处理失败则阻断。报告默认引用透明图wordcloud.png，白底版及审计文件保存在任务目录。字体按客户优先顺序自动选择：Windows优先微软雅黑粗体，其他系统优先Noto Sans CJK粗体；字体二进制不入仓库，打包脚本从仓库外缓存校验并加入部署包，MA Linux沙箱无需运行时下载字体。`TOPIC5_WORDCLOUD_FONT`可用于显式客户字体覆盖或本地指定外部缓存路径，实际字体与哈希记入render.json。

n15 由模型选择 `quote_id`，代码复制原字段片段；原词必须精确属于当前范围且无重复，
证据帖子必须已读。未知词、重复词、未读帖子和引用冲突给出具体反馈后重试，
不能带无效证据进入报告。当前 Memory 仅三份记忆，客户业务知识统一迁移仍暂缓；
Agent 通过 `memory_ls` / `memory_read` 读取，脚本语义调用不自动继承。

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
L 交付    n20_delivery_draft → n20_delivery_gate → [G4] n21_hc_user → n22_user_gate → n_user_escalate
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
- Skill/Prompt 改动使用打包→上传→原地更新 Agent；依赖变更使用 `tools/update_environment_in_place.py`。
  Gateway 宿主 Python 代码修改或全量重建改变资源 ID 后需重启统一 Gateway；
  仅 Skill/Prompt 原地更新不需要重启。
- 真实凭证（`DATAHUB_API_KEY` / `SSJH_API_TOKEN` / `REPORT_COVER_API_KEY` 等）只写 `config.env`，入库文件一律 `${...}` 占位。
- `demo` 仅让 G1 表单自动预填；进入流水线后与 `full` 使用完全相同的 fail-closed 标准，默认模式为 `full`。
- 图表只在沙箱内渲染本地 PNG，n20 通过飞书 `medias/upload_all` 原生插图，不依赖外部对象存储。
- topic5 **走飞书文档交付**，不走妙搭 Miaoda，故无用户 OAuth / artifact-sync / token keepalive。
