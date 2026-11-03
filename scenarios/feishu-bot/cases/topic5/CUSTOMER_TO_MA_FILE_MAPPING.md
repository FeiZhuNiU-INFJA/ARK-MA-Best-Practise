# Topic 5 客户原始文件与 MA 版本对应表

> 本表以仓库中的实际文件为准。客户原始文件均位于 `assets_from_customer/Ada topic5/`
> （已被 `.gitignore` 拦截，只读不入库），MA 部署源码以 `ma-resources/` 为准；
> 根目录下的 Gateway、HITL 和打包脚本属于 MA 接入层。

## 2026-11-02 报告完整性补齐

`pipeline-orchestrator/REPORT_CONTRACT.md`依据客户报告模板、飞书格式规范及Producer要求整理。
`report_contract.py`确定性装配数据说明、PGC/UGC各Top10、观点/UGC代表证据及正确图片；
写作和返修节点显式读取契约。品牌大盘词云由品牌统计分别接入图表和解读节点。
飞书原生Grid/Callout/富文本在`feishu.py`实现，完整性规则在独立Validator扫描器实现。
打包器注入同一扫描器供N19预检；不依赖客户原目录，不修改客户资产。

## 标记说明

截至2026-11-03，词云已使用客户 `wordcloud-insight` v0.2.6：n14 双版渲染，
n15 全词表/全文阅读/补读/compose；引用由模型选 `quote_id`、代码复制原字段片段，
原词范围、去重和已读帖子白名单校验失败给具体反馈后重试。有原帖证据失败必须阻断。
字体统一在仓库外缓存下载、校验后注入 ZIP；默认报告引用透明版。
业务知识 Memory 统一迁移暂缓，当前仅三份记忆，见
[迁移审计](./KNOWLEDGE_MIGRATION_AUDIT.md)及[需求记录](./KNOWLEDGE_MEMORY_REQUIREMENT.md)。

| 标记     | 含义                                                         |
| -------- | ------------------------------------------------------------ |
| 原样迁入 | 路径可能变化，但文件内容基本一致                             |
| 适配迁入 | 源自客户文件，因 MA 路径、接口、模型协议或职责边界而修改     |
| 合并重写 | 多个客户脚本/子 Agent 合并为一个 MA 入口                     |
| MA 新增  | 客户文件中没有直接对应物，为 MA 部署或运行新增               |
| 未迁移   | 客户侧保留，但当前 MA 包和运行链路未使用                     |
| 运行产物 | 可重新生成，不作为迁移源码维护                               |

## 一、总体对应关系

客户原始包采用「多 Agent 物理隔离」布局（`ma-resources-Topic5` / `ma-resources-ProjectAgent` /
`ma-resources-Validator` 三套独立 Agent 资源 + `ma-workspaces` 运行期产物）。MA 版本按
**薄 Agent + Durable Workflow** 重构：把 Topic5Agent / ProjectAgent 及 4 个
Subagent（Discovery/Opinion/Insight/Narrative）**合并**为确定性编排器里的 handler，
只保留 2 个物理 MA Agent（coordinator + validator）。

| 客户原始资源                                                | MA 版本                                                   | 状态     | 说明                                                                 |
| ----------------------------------------------------------- | -------------------------------------------------------- | -------- | -------------------------------------------------------------------- |
| `ma-resources-Topic5/` + `ma-resources-ProjectAgent/` 的编排与业务逻辑 | `ma-resources/skills/pipeline-orchestrator/`             | 合并重写 | 29 节点 DAG 引擎 + 全部节点 handler（清洗/聚合/语义/标注/图表/交付） |
| 4 个 Subagent（Discovery/Opinion/Insight/Narrative）         | `pipeline-orchestrator/semantic.py` + `handlers.py` 内的语义 handler | 合并重写 | sub-agent 归并为确定性 handler，不各建独立 MA Agent                  |
| SSJH 社媒统计工具                                           | `ma-resources/skills/SSJH-Social-Stat/`                  | 适配迁入 | n05/n06/n13 取数；凭据改为「入参 > 环境变量 > config.json」优先级    |
| DataHub 批量标注工具                                        | `ma-resources/skills/blueai-datahub-skill/`              | 适配迁入 | n09/n11 标注；`DATAHUB_API_KEY` 从环境变量读，不入库 `.env`          |
| 舆情 ECharts 图表工具                                       | `ma-resources/skills/blueai-yuqing-echarts-skill-new/`   | 适配迁入 | n14 真渲染；源码入库，字体校验后注入部署包；`node_modules` 沙箱内安装 |
| 客户 `wordcloud-insight` v0.2.6 | `ma-resources/skills/wordcloud-insight/` | 适配迁入 | n14 双版词云、n15 完整分析与 compose；适配器保留原字段证据 |
| 报告封面工具                                               | `ma-resources/skills/report-cover-skill/`                | 适配迁入 | n14 封面；移除硬编码 relay key，凭据必填 `REPORT_COVER_API_KEY`      |
| `ma-resources-Validator/`                                   | `ma-resources/skills/topic5-validator/` + `agents/validator.*` | 适配迁入 | J 阶段 n17 自动两段式审计：脚本机器全检 + Agent 全文语义检查；规则冲突见适配表 |
| Topic5Agent / ProjectAgent 的 CLAUDE/Prompt                 | `ma-resources/agents/coordinator.system.md`              | 重新编排 | 原多 Agent 主流程改成薄 Coordinator + 结构化 HC JSON                 |
| 业务记忆 / 错误案例库                                      | `ma-resources/memory/topic5/`                            | 适配迁入 | 改造成 MA Memory Store 的只读业务记忆                               |
| `ma-workspaces/`                                            | 无（沙箱 `/workspace` 动态创建）                         | 运行产物 | MA 在沙箱工作区动态创建项目和产物，不作为源码维护                   |

## 二、`pipeline-orchestrator`（确定性编排器）

这是 topic5 的核心，承载 29 节点 DAG 引擎与全部节点 handler。

| MA 文件            | 职责                                                                             | 状态     |
| ------------------ | -------------------------------------------------------------------------------- | -------- |
| `engine.py`        | DAG 执行引擎：拓扑调度、幂等 ledger 重放/对账、checkpoint、HC pause/resume        | MA 新增  |
| `run_pipeline.py`  | CLI 入口：`start` / `resume --hc {kind} --decision {pass\|reject\|remark}` / `status` | MA 新增  |
| `state.py`         | 运行态读写、params（含 `nsr_denominator_includes_neutral` 等口径参数）            | MA 新增  |
| `handlers.py`      | 各节点 handler 分发；本地确定性逻辑（n07 清洗 / n12 聚合等）                      | 合并重写 |
| `semantic.py`      | Ark Chat API 客户端（OpenAI 兼容）；n08/n09b/n15/n16/n19 语义 handler，含降级     | 合并重写 |
| `ssjh.py`          | SSJH 适配层：`api_available` / `pull` / `fallback`（n05/n06/n13）                 | 适配迁入 |
| `datahub.py`       | DataHub 适配层：CSV（utf-8 无 BOM）→上传→轮询→解析 `llm_result`（n09/n11）         | 适配迁入 |
| `annotate.py`      | 标注路由：行数≥阈值且 DataHub 可用→批量引擎，否则小引擎 semantic，再否则降级      | MA 新增  |
| `charts.py`        | `build_chart_specs` 产 echarts spec + `render_charts` 本地渲染 PNG + 降级（n14，去 COS）    | 合并重写 |
| `wordcloud_adapter.py` | n14/n15 调用客户 Skill，双图、完整词表、原帖引用选择与证据校验、compose | MA 新增 |
| `excel_source.py` | 冻结飞书附件引用、SHA256 下载/缓存校验、schema 与行结构映射，兼容旧挂载 | MA 新增 |
| `report_contract.py` | 确定性装配数据说明、Top10、观点/UGC证据和图片；校验完整结构 | MA 新增 |
| `REPORT_CONTRACT.md` | 写作、返修和发布遵循的当前报告契约 | MA 新增 |
| `feishu.py`        | 飞书原生发布、所有权或可管理协作者权限核验、回读、revision_id 和限流退避 | MA 新增  |
| `validator.py`     | J 阶段 n17 两段式审计，产 `report_qc_report.json`                                 | 适配迁入 |
| `acceptance.py`    | 质检口径判定辅助                                                                  | MA 新增  |
| `hc_bridge.py`     | `NODE_TO_HC` 节点↔人工关卡映射；`build_human_outputs` 翻译用户决定                | MA 新增  |
| `publish_spec.py`  | 报告发布 spec 构造                                                                | MA 新增  |
| `SKILL.md`         | SkillHub 入口契约                                                                 | MA 新增  |
| `tests/`           | 清洗、聚合、词云、附件输入、报告完整性、标注、质检和飞书交付回归用例；数量以当前测试发现结果为准 | MA 新增 |

## 三、数据与渲染类 Skill

| MA Skill                             | 节点        | 去敏处理                                                                |
| ------------------------------------ | ----------- | ---------------------------------------------------------------------- |
| `SSJH-Social-Stat/skill.py`          | n05/n06/n13 | **不入库 `config.json`**（含真 token）；凭据缺失即 raise                |
| `blueai-datahub-skill/`              | n09/n11     | 只迁 sdk + scripts + references；**不入库 `.env` / PII 响应 / mock**    |
| `blueai-yuqing-echarts-skill-new/`   | n14         | 源码入库，字体由仓库外缓存校验注入；`node_modules` 排除 |
| `wordcloud-insight/` | n14/n15 | 客户源码与规范，Noto Sans CJK Bold 构建注入；双图和审计文件为运行产物 |
| `report-cover-skill/`                | n14         | 移除硬编码 relay key，`.env` 不入库                                     |

## 四、`topic5-validator`（独立质检子 Agent）

| MA 文件                      | 职责                                                            | 状态    |
| ---------------------------- | --------------------------------------------------------------- | ------- |
| `topic5-validator/SKILL.md`  | 两段式审计契约（机器全检 + 全文语义检查）、qc_report 契约、分级口径 | MA 新增 |
| `topic5-validator/references/rule-adaptation.md` | 客户 v1.5 全规则到当前架构的 applicable/adapted/not_applicable 映射 | MA 新增 |
| `topic5-validator/scripts/`  | `scan_report.py` 全量扫描脚本                                    | 适配迁入 |
| `agents/validator.json`      | Validator MA Agent 资源定义（独立身份、只读、不改报告）          | MA 新增 |
| `agents/validator.system.md` | Validator 系统提示词：数字溯源/指标白名单/提及漏斗/NSR/逐活动不相加/证据充分性 | MA 新增 |

## 五、Agent、Memory 与 MA 部署资源

| MA 文件                         | 客户原始文件/依据             | 状态    | 说明                                                       |
| ------------------------------- | ----------------------------- | ------- | ---------------------------------------------------------- |
| `agents/coordinator.system.md`  | Topic5Agent/ProjectAgent 主流程 | MA 新增 | 薄前台：启动/抛关卡/续跑/交终态四件事 + 结构化 HC JSON     |
| `agents/coordinator.json`       | 无直接客户文件                | MA 新增 | coordinator Agent 资源、模型、Skill、子 Agent（validator） |
| `agents/validator.json/.system.md` | Validator 契约             | MA 新增 | 独立质检子 Agent                                           |
| `memory/topic5/MEMORY.md`       | 客户业务记忆索引              | 适配迁入 | MA 资源索引、触发词、薄 Agent 边界、7 门关卡、口径红线     |
| `memory/topic5/_版本状态.md`     | 客户版本状态                  | 适配迁入 | 波次 0-7 接真进度 + 口径红线快照 + 降级契约                |
| `memory/topic5/错误案例库.md`    | 客户错误案例库                | 适配迁入 | E1 依赖文档谎报 / E2 config 硬编码 token / E3 连通分量膨胀 / E4 CSV BOM |
| `memory-store.json`             | 无                            | MA 新增 | 声明 3 个 Memory 文件及挂载路径                            |
| `environment.json`              | 环境变量依赖清单 + 各工具契约 | MA 新增 | 声明依赖、网络、setup_script（装 echarts node_modules）和环境变量占位符 |
| `create_all.sh`                 | 无                            | MA 新增 | 创建/更新 Environment、Memory Store 和 2 个 Agent          |
| `skill_ids.json.example`        | 无                            | MA 新增 | 7 个 Skill 上传结果模板                                    |
| `skill_ids.json` / `created_ids.json` | 无                      | 运行产物 | 上传/创建后生成的实际资源 ID，不入库                      |

## 六、Bot 接入层与本地工具

这些文件属于 MA 版本，但不打进 Skill 包。topic5 由 topic6 对应文件移植改造，**删除**了
妙搭 Miaoda 相关（user_oauth 表 / Device Flow / token keepalive / artifact-sync）。

| MA 文件                     | 作用                                                                       | 状态    |
| --------------------------- | -------------------------------------------------------------------------- | ------- |
| `config.py`                 | `Topic5Config`，`REQUIRED_KEYS`（无 Miaoda 字段）                           | 适配迁入（自 topic6） |
| `gateway.py`                | 飞书事件入口及 topic5 运行器装配（删除 Miaoda 授权/keepalive/artifact_sync）| 适配迁入 |
| `topic5_runner.py`          | 创建 MA Session、消费 SSE、驱动作业；触发词 `复盘报告/营销复盘/活动复盘`     | 适配迁入 |
| `pipeline_store.py`         | 作业/Session/HC 事件状态持久化（`HC_KINDS=G1/G2/N10/G3/G4/HC7/USER_ESCALATE`，无 user_oauth 表）| 适配迁入 |
| `topic5_hitl.py`            | 人工关卡卡片回调；`HCx pass`/`HCx reject: note`/`HCx remark: note`          | 适配迁入 |
| `topic5_progress_card.py`   | 飞书进度卡片渲染（全程复用同一卡 patch 覆写）                              | 适配迁入 |
| `tools/pack_skills.sh`      | 校验并打包 7 个 Skill（额外 exclude `.env`/`config.json`/`Outputs`/`node_modules`）| 适配迁入 |
| `tools/prepare_fonts.py` | 下载三份字体至仓库外缓存、SHA256 校验、注入临时部署目录 | MA 新增 |
| `tools/update_agents_in_place.py` | 保留 Agent ID 更新 Prompt/Skill 绑定并回读校验 | MA 新增 |
| `tools/update_environment_in_place.py` | 保留 Environment ID 更新依赖配置 | MA 新增 |
| `tools/upload_skills.py`    | 上传 SkillHub 并回填 ID/版本                                                | 适配迁入 |
| `update_ma.sh`              | 首次创建/全量重建入口；日常修复使用原地更新脚本（共用 `CONDA_ENV=topicX`） | 适配迁入 |
| `tools/out/*.zip`、`*.sha256` | `pack_skills.sh` 重建的打包产物                                           | 运行产物 |
| `README.md`、`TOPIC5_DEBUG_QUICKSTART.md`、本表、`topic5_ma_architecture.html` | MA 维护与调试文档         | MA 新增 |

## 七、客户侧未进入 MA 运行链路的资源

| 客户原始文件/目录                        | MA 状态  | 原因或替代方式                                           |
| ---------------------------------------- | -------- | -------------------------------------------------------- |
| `ma-workspaces/**`                       | 未迁移   | MA 在沙箱 `/workspace/Projects/` 动态创建项目和产物      |
| 各 Skill 的 `config.json` / `.env`       | 未迁移   | 含真凭证，改为环境变量注入；`.gitignore` 拦截            |
| 各 Skill 的 `Outputs/` / PII 响应样例    | 未迁移   | 含真实抓取数据与个人信息，禁止入库                        |
| `knowledge/plan_pack/`、`knowledge/report_pack/` | 未统一迁入 Memory | 已有规则部分适配于契约/代码；完整业务知识统一源与节点注入暂缓实施 |
| `knowledge/architecture/`、`knowledge/Archive/` | 不进入运行时 Memory | 旧架构仅供追溯，Archive 为废弃内容 |
| 控制面节点 n04_run_init / n23_delivery_commit 的 Blueflare-Ada 依赖 | 本地 handler | 保留接入标记，当前已校验输入与交付，不是无条件通过的 stub |
| 所有 `.DS_Store`、`__pycache__/`         | 忽略     | 本地系统或解释器缓存，不属于源码                          |

## 八、维护规则

1. 客户文件只作为迁移基线，MA 生效源码统一修改 `ma-resources/`。
2. 修改 Skill 后运行 pack→upload→`tools/update_agents_in_place.py`，保留 Agent ID；需要改环境依赖时用`tools/update_environment_in_place.py`。`create_all.sh`仅用于首次资源创建，不用于这类原地修复。
3. 真实凭证只写 `config.env`；入库文件一律 `${...}` 占位。
4. 新增、拆分、合并或删除文件时，同步更新本表。

Memory 读取限于 Agent 内置 `memory_ls` / `memory_read`；先列出实际 memory root，
再读其下 `topic5/` 文件，不能假设沙箱 `/mnt/memory` 为可读目录。
Python/JS 调用的语义模型不会自动继承 Agent Memory；统一读取与按节点注入尚未实现。
