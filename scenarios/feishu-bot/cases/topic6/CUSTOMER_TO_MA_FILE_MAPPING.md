# Topic 6 客户原始文件与 MA 版本对应表

> 核对日期：2026-09-29
>
> 本表以仓库中的实际文件和 SHA-256 内容比对为准。客户原始文件均位于
> `assets_from_customer/`，MA 部署源码以 `ma-resources/` 为准；根目录下的
> Gateway、HITL 和打包脚本属于 MA 接入层。

## 标记说明

| 标记 | 含义 |
|---|---|
| 原样迁入 | 路径可能变化，但文件内容 SHA-256 一致 |
| 适配迁入 | 源自客户文件，因 MA 路径、接口、模型协议或职责边界而修改 |
| 合并重写 | 多个客户脚本合并为一个 MA 入口 |
| 拆分重写 | 一个客户脚本拆成多个 MA 脚本 |
| MA 新增 | 客户文件中没有直接对应物，为 MA 部署或运行新增 |
| 未迁移 | 客户侧保留，但当前 MA 包和运行链路未使用 |
| 运行产物 | 可重新生成，不作为迁移源码维护 |

## 一、总体对应关系

| 客户原始资源 | MA 版本 | 状态 | 说明 |
|---|---|---|---|
| `topic6/skill/fetch-normalize/` | `ma-resources/skills/topic6-fetch-normalize/` | 适配迁入 | Phase A+B；原单入口被拆为取数、标准化、抽样 3 个脚本 |
| `topic6/skill/annotation/` + `topic6/ks/` + `topic6/prompt/` + `topic6/tool/cost-tracker/` | `ma-resources/skills/topic6-annotation/` | 重组迁入 | Phase C+D；业务口径、活跃 Prompt、标注与合并脚本集中到一个 Skill |
| `blueai-canonical-event-registry/` | `ma-resources/skills/topic6-event-registry/` | 适配迁入 | C2 事件归档；主体原样保留，LLM Relay 改接 Ark OpenAI 兼容协议 |
| `topic6/skill/insight/` | `ma-resources/skills/topic6-insight/` | 适配迁入 | Phase E+F；保留活跃 Prompt，脚本改为 MA 沙箱路径和 Ark API |
| `artifact-template-bluefocus-hotspot-web-report/` | `ma-resources/skills/topic6-web-report/` | 适配迁入 | Phase G；模板和素材原样保留，Skill 名称改为 `topic6-web-report` |
| `topic6/CLAUDE.md` + `topic6/prompt/*.md` + `其他相关引用/Agent.Producer_CLAUDE.md` | `ma-resources/agents/coordinator.system.md` | 重新编排 | 原单 Agent 主流程改成 Coordinator + 子 Agent + 结构化 HC |
| `topic6/skill/annotation/SKILL.md` + 标注阶段契约 | `ma-resources/agents/annotator.system.md` | MA 新增 | 抽出单路 C0/C3/R1-R5 标注子 Agent |
| `topic6/skill/insight/SKILL.md` + Phase E 契约 | `ma-resources/agents/insighter.system.md` | MA 新增 | 抽出单版块 E1-E4 洞察子 Agent |
| `topic6/lm/` | `ma-resources/memory/topic6/` | 适配迁入 | 改造成 MA Memory Store 的只读业务记忆 |
| `Validator/` | 无 | 未迁移 | 当前 MA 流程没有独立 Validator Agent 或 QC Skill |

## 二、`topic6-fetch-normalize` 对应表

| MA 文件 | 客户原始文件 | 状态 | 主要变化 |
|---|---|---|---|
| `skills/topic6-fetch-normalize/SKILL.md` | `topic6/skill/fetch-normalize/` 无独立 `SKILL.md` | MA 新增 | 增加 SkillHub frontmatter、目录和执行契约 |
| `scripts/fetch_hot_topics.py` | `topic6/skill/fetch-normalize/pipeline_ab.py` | 拆分重写 | 提取 Phase A；改为 MA MCP、环境变量和 `/workspace` 路径 |
| `scripts/log1p_p1p99_normalize.py` | `topic6/skill/fetch-normalize/pipeline_ab.py` | 拆分重写 | 提取 Phase B；保留 log1p + P1/P99 核心算法 |
| `scripts/sample_500.py` | `topic6/skill/fetch-normalize/sample_500.py` | 适配迁入 | 改为 `--project-dir` 和 MA 工作区路径 |
| `references/平台热度基准_2026.json` | `topic6/skill/fetch-normalize/references/平台热度基准_2026.json` | 原样迁入 | SHA-256 一致 |
| `references/marketing_calendar_2026.csv` | `topic6/skill/fetch-normalize/references/marketing_calendar_2026.csv` | 原样迁入 | SHA-256 一致 |

## 三、`topic6-annotation` 对应表

### 3.1 Skill、知识库与流程 Prompt

| MA 文件 | 客户原始文件 | 状态 | 说明 |
|---|---|---|---|
| `skills/topic6-annotation/SKILL.md` | `topic6/skill/annotation/SKILL.md` | 适配迁入 | 重写为 MA Skill 入口，补 frontmatter、挂载路径和一体化编排说明 |
| `ks/_字段速查.md`、`00_产品定位.md`～`07_报告结构.md` | `topic6/ks/` 下同名 9 个文件 | 原样迁入 | SHA-256 全部一致 |
| `prompts/00_角色与触发.md`～`09_阶段_H妙搭发布.md` | `topic6/prompt/` 下同名 10 个文件 | 原样迁入 | SHA-256 全部一致 |
| `prompts/run_config契约.md`、`prompts/运行前置.md` | `topic6/prompt/` 下同名文件 | 原样迁入 | SHA-256 一致 |

### 3.2 标注 Prompt

| MA 文件 | 客户原始文件 | 状态 | 说明 |
|---|---|---|---|
| `prompts/C0_基础事实/v1.md`～`v4.md` | `topic6/skill/annotation/prompts/C0_基础事实/` 下同名文件 | 原样迁入 | 4 个版本全部保留 |
| `prompts/R1_平台借势/v1.md`～`v2.md` | 客户侧同路径文件 | 原样迁入 | 2 个版本全部保留 |
| `prompts/R2_商业合作/v1.md` | 客户侧同路径文件 | 原样迁入 | SHA-256 一致 |
| `prompts/R3_风险预警/v1.md` | 客户侧同路径文件 | 原样迁入 | SHA-256 一致 |
| `prompts/R4_创意借鉴/v1.md`～`v2.md` | 客户侧同路径文件 | 原样迁入 | 2 个版本全部保留 |
| `prompts/R5_消费者行为/v1.md`～`v2.md` | 客户侧同路径文件 | 原样迁入 | 2 个版本全部保留 |
| `prompts/节点标注/v7.md` | `topic6/skill/annotation/prompts/节点标注/v7.md` | 原样迁入 | 只打包当前活跃版 |
| 无 | `prompts/节点标注/v1.md`～`v6.md` | 未迁移 | 历史版本未进入 MA Skill 包 |
| 无 | `prompts/内容标注/v1.md`～`v9.md` | 未迁移 | 已被 C0 + R1-R5 分层标注方案替代 |

### 3.3 标注脚本与依赖

| MA 文件 | 客户原始文件 | 状态 | 主要变化 |
|---|---|---|---|
| `scripts/datahub_annotate.py` | `datahub_submit.py` + `datahub_poll.py` + `annotation_postprocess.py` + `c3_v6_flatten.py` + `c3_v6_timewindow.py` | 合并重写 | 提交、轮询、下载、JSON 展开、C3 时窗计算合并为单入口 |
| `scripts/c0_merge_phase1.py` | 客户侧同名脚本 | 适配迁入 | MA 路径化；移除 `--patch-run-id` 越权补丁分支 |
| `scripts/c0_filter_usable.py` | 客户侧同名脚本 | 适配迁入 | MA 路径化；移除强制放行参数，超过 5% 交由协调器处理 |
| `scripts/merge_annotations.py` | 客户侧同名脚本 | 适配迁入 | 对齐 MA 产物路径、28 列宽表和健康摘要 |
| `references/marketing_calendar/generate_marketing_calendar.py` | 客户侧同路径文件 | 原样迁入 | SHA-256 一致 |
| `references/marketing_calendar/nodes_definition.csv` | 客户侧同路径文件 | 原样迁入 | SHA-256 一致 |
| `references/marketing_calendar/nodes_definition_supplement.csv` | 客户侧同路径文件 | 原样迁入 | SHA-256 一致 |
| `tool/cost-tracker/cost_tracker.py` | `topic6/tool/cost-tracker/cost_tracker.py` | 适配迁入 | 精简为 MA 共用成本台账，路径改为 `/workspace` |
| 无 | `annotation/scripts/c0_retry_missing.py`、`retry_missing.py` | 未迁移 | 重试决策上移 Coordinator；当前 MA 包无同名重试脚本 |
| 无 | `annotation/scripts/c2_run_all_platforms.py` | 未迁移 | Coordinator 直接编排 `topic6-event-registry` 各阶段脚本 |
| 无 | `annotation/scripts/c3_postprocess.py` | 未迁移 | 能力并入 `datahub_annotate.py` |

## 四、`topic6-event-registry` 对应表

客户根目录：`assets_from_customer/blueai-canonical-event-registry/`  
MA 根目录：`ma-resources/skills/topic6-event-registry/`

| MA 文件 | 客户原始文件 | 状态 | 说明 |
|---|---|---|---|
| `SKILL.md` | 同名文件 | 适配迁入 | 增加 MA/Ark 运行约束 |
| `CHANGELOG.md` | 同名文件 | 适配迁入 | 记录 Ark OpenAI 协议迁移 |
| `references/runbook.md` | 同名文件 | 适配迁入 | 更新 MA 环境变量和运行方式 |
| `references/benchmarks.md`、`calibration.md`、`identity.md`、`open-questions.md`、`pipeline.md`、`prompts.md` | 同名文件 | 原样迁入 | 6 个文件 SHA-256 一致 |
| `scripts/relay.py` | 同名文件 | 适配迁入 | Anthropic `/v1/messages` 改为 Ark OpenAI `/v1/chat/completions` |
| `scripts/00_clean_titles.py`、`00_seed_from_registry.py`、`01_eventness.py`～`11_score_calibration.py` | 同名文件 | 原样迁入 | 13 个主流水线脚本 SHA-256 一致 |
| `scripts/x0_merge_platforms.py`～`x4_detail_table.py` | 同名文件 | 原样迁入 | 5 个跨平台脚本 SHA-256 一致 |
| `scripts/eventlib.py` | 同名文件 | 原样迁入 | SHA-256 一致 |
| `scripts/__pycache__/` | 客户侧缓存 | 未迁移 | Python 缓存，不属于源码 |

## 五、`topic6-insight` 对应表

| MA 文件 | 客户原始文件 | 状态 | 主要变化 |
|---|---|---|---|
| `skills/topic6-insight/SKILL.md` | `topic6/skill/insight/SKILL.md` | 适配迁入 | 补 SkillHub frontmatter、MA 路径和 Ark 环境变量 |
| `scripts/pipeline_e.py` | `topic6/skill/insight/pipeline_e.py` | 适配迁入并移动 | 去除本地目录上溯，改 `/mnt/skills`、`/workspace`、Ark OpenAI 接口 |
| `scripts/pipeline_f.py` | `topic6/skill/insight/pipeline_f.py` | 适配迁入并移动 | 改项目路径解析和 MA 产物目录 |
| `01_统计/_common.py`、`e1_industry.py`、`e3_platforms.py` | 客户侧同路径文件 | 原样迁入 | 3 个脚本 SHA-256 一致 |
| `01_统计/e2_nodes.py` | 客户侧同路径文件 | 适配迁入 | 对齐 C3 节点结果和 MA 日历路径 |
| `01_统计/e4_marketing.py` | 客户侧同路径文件 | 适配迁入 | 对齐 MA 宽表与 E4 打标链路 |
| `01_统计/run_stats.py` | 客户侧同路径文件 | 适配迁入 | 对齐 MA 项目路径和统计编排 |
| `02_洞察/_shared/role_style.md` | 客户侧同路径文件 | 原样迁入 | SHA-256 一致 |
| `02_洞察/E1_行业话题/v1.md` | 客户侧同路径文件 | 原样迁入 | 当前活跃版 |
| `02_洞察/E2_营销节点/v3.md` | 客户侧同路径文件 | 原样迁入 | 当前活跃版；v1-v2 未打包 |
| `02_洞察/E3_平台新鲜事/v1.md` | 客户侧同路径文件 | 原样迁入 | 当前活跃版 |
| `02_洞察/E4_营销发现/v6.md`、`_tagging/v1.md` | 客户侧同路径文件 | 原样迁入 | 当前活跃撰写版与打标版；v1-v5 未打包 |
| `references/E3_统计口径说明.md`、`综合热度指数算法说明.md` | 客户侧 `01_统计/references/` 下同名文件 | 原样迁入并移动 | 从统计子目录提升到 Skill 的 `references/` |

## 六、`topic6-web-report` 对应表

客户根目录：`assets_from_customer/artifact-template-bluefocus-hotspot-web-report/`  
MA 根目录：`ma-resources/skills/topic6-web-report/`

| MA 文件 | 客户原始文件 | 状态 | 说明 |
|---|---|---|---|
| `SKILL.md` | 同名文件 | 适配迁入 | Skill 名改为 `topic6-web-report`，补充 MA 场景分工 |
| `agents/openai.yaml` | 同名文件 | 适配迁入 | `default_prompt` 中的 Skill 名改为 `$topic6-web-report` |
| `CHANGELOG.md` | 无 | MA 新增 | 记录模板在 MA 侧的变更 |
| `artifact-template.json`、`CLAUDE.md` | 同名文件 | 原样迁入 | SHA-256 一致 |
| `references/document-image-extraction.md`、`html-snapshot-upload.md` | 同名文件 | 原样迁入 | SHA-256 一致 |
| `scripts/upload-html.mjs`、`validate-case-image-sources.mjs`、`validate-rendered-content.mjs`、`validate-template-assets.mjs` | 同名文件 | 原样迁入 | 4 个脚本 SHA-256 一致 |
| `assets/preview.png` | 同名文件 | 原样迁入 | SHA-256 一致 |
| `assets/source/app.js`、`build-report.mjs`、`index.html`、`styles.css`、`source.example.json`、`DESIGN_SPEC.md` | 同名文件 | 原样迁入 | 6 个模板源码文件 SHA-256 一致 |
| `assets/source/bluefocus-logo-white.png`、`hero-social-bg.jpg`、`title-monthly.png`、`title-weekly.png` | 同名文件 | 原样迁入 | 4 个根级素材 SHA-256 一致 |
| `assets/source/assets/case-1-car-livestream.jpg`～`case-4-brand-notice.jpg` | 同名文件 | 原样迁入 | 4 张案例图 SHA-256 一致 |
| `assets/source/assets/fonts/Poppins-{Bold,Light,Medium,Regular,SemiBold}.ttf` | 同名文件 | 原样迁入 | 5 个字体 SHA-256 一致 |
| `assets/source/assets/platform-{bilibili,douyin,weibo,zhihu}.svg`、`platform-logos-combined.svg` | 同名文件 | 原样迁入 | 5 个平台图标 SHA-256 一致 |
| `assets/source/assets/rank-{1,2,3}.svg` | 同名文件 | 原样迁入 | 3 个排名图标 SHA-256 一致 |
| `assets/source/.openai/hosting.json` | 同名文件 | 原样迁入 | 隐藏配置文件，SHA-256 一致 |

## 七、Agent、Memory 与 MA 部署资源

| MA 文件 | 客户原始文件/依据 | 状态 | 说明 |
|---|---|---|---|
| `agents/coordinator.system.md` | `topic6/CLAUDE.md`、`topic6/prompt/*.md`、`其他相关引用/Agent.Producer_CLAUDE.md` | MA 新增 | 汇总主流程并加入 Multi Agent、HC1-HC3、MA 挂载路径 |
| `agents/annotator.system.md` | 标注 Skill 与 Phase C 契约 | MA 新增 | 定义单路标注子 Agent 输入输出 |
| `agents/insighter.system.md` | 洞察 Skill 与 Phase E 契约 | MA 新增 | 定义单版块洞察子 Agent 输入输出 |
| `agents/coordinator.json`、`annotator.json`、`insighter.json` | 无直接客户文件 | MA 新增 | MA Agent 资源、模型、Skill、工具和子 Agent 关系 |
| `memory/topic6/MEMORY.md` | `topic6/lm/MEMORY.md` | 适配迁入 | 改为 MA 资源索引、挂载路径和执行职责 |
| `memory/topic6/_版本状态.md` | `topic6/lm/_版本状态.md` | 适配迁入 | 改为 MA 当前活跃 Prompt/Skill 版本 |
| `memory/topic6/错误案例库.md` | `topic6/lm/错误案例库.md` | 适配迁入 | 保留业务经验并改写 MA 相关规避规则 |
| `memory-store.json` | `topic6/lm/` | MA 新增 | 声明 3 个 Memory 文件及挂载路径 |
| `environment.json` | `其他相关引用/Topic6_环境变量依赖清单.md` + 各工具契约 | MA 新增 | 声明依赖、网络和环境变量占位符 |
| `create_all.sh` | 无 | MA 新增 | 创建/更新 Environment、Memory Store 和 3 个 Agent |
| `skill_ids.json.example` | 无 | MA 新增 | Skill 上传结果模板 |
| `skill_ids.json`、`created_ids.json` | 无 | 运行产物 | 上传/创建后生成的实际资源 ID，不是客户源码 |
| `version_mapping.md` | 无 | MA 新增 | 维护 SkillHub 版本、源码哈希和上传记录 |

## 八、Bot 接入层与本地发布工具

这些文件属于 MA 版本，但不打进 Skill 包。

| MA 文件 | 客户原始文件 | 状态 | 作用 |
|---|---|---|---|
| `config.py`、`env.example` | 无 | MA 新增 | Gateway 配置和环境变量样例 |
| `gateway.py` | 无 | MA 新增 | 飞书事件入口及 Topic 6 运行器装配 |
| `topic6_runner.py` | 无 | MA 新增 | 创建 MA Session、消费 SSE、驱动完整作业 |
| `pipeline_store.py` | 无 | MA 新增 | 作业、Session、HC 事件状态持久化 |
| `topic6_hitl.py` | 原流程中的 HC1/HC2/HC3 规则 | MA 新增 | 飞书审核卡片回调和继续执行 |
| `topic6_progress_card.py` | 无 | MA 新增 | 飞书进度卡片渲染 |
| `tools/pack_skills.sh` | 无 | MA 新增 | 校验并打包 5 个 Skill |
| `tools/upload_skills.py` | 无 | MA 新增 | 上传 SkillHub 并回填 ID/版本 |
| `tools/out/*.zip`、`*.sha256` | 无 | 运行产物 | 可由 `pack_skills.sh` 重建 |
| `README.md`、`TOPIC6_CHANGELOG.md`、`TOPIC6_DEBUG_QUICKSTART.md` | 无直接对应 | MA 新增 | MA 维护、约束变更和调试文档 |
| `topic6_ma_architecture.html`、`topic6_pipeline_overview.html` | 客户流程文档的可视化重述 | MA 新增 | MA 架构与流水线可视化 |

## 九、客户侧未进入 MA 运行链路的资源

| 客户原始文件/目录 | MA 状态 | 原因或替代方式 |
|---|---|---|
| `Validator/**` | 未迁移 | 当前没有 Validator Agent/QC Skill；Topic 6 报告检查清单也未接入 |
| `topic6/lm/执行日志.md` | 未迁移 | 运行状态改由 `pipeline_store.py` 和项目 `run_config.yaml` 承载 |
| `topic6/others/_待办登记.md` | 未迁移 | 非运行依赖 |
| `topic6/Projects/_项目模板/**`、`Projects/W37热点周报_*/**` | 未迁移 | MA 在 `/workspace/Projects/` 动态创建项目和产物 |
| `topic6/skill/_external_event-registry.md` | 未迁移 | 被实际挂载的 `topic6-event-registry` Skill 替代 |
| `topic6/skill/_external_node-tagging.md` | 未迁移 | 外部节点 Skill 已取消；能力由 C3 + 日历基线承担 |
| `topic6/skill/_external_web-report.md` | 未迁移 | 被实际挂载的 `topic6-web-report` Skill 替代 |
| `topic6/tool/anthropic-llm/契约.md` | 未迁移 | 模型调用统一改为 Ark/OpenAI 兼容协议 |
| `topic6/tool/datahub/契约.md` | 未直接迁入 | 契约落实在 `datahub_annotate.py` 和环境变量配置中 |
| `topic6/tool/hot-topics-mcp/契约.md` | 未直接迁入 | MCP 定义落实在 `coordinator.json` |
| `topic6/tool/lark-cli/契约.md` | 未直接迁入 | 发布职责由 Coordinator 与飞书接入层承担 |
| `topic6/tool/web-report/md2source.py`、`README.md` | 未迁移 | MA 网页 Skill 直接按 `source.example.json` 构造 `source.json` |
| `topic6/其他相关引用/authorized_users.json`、`permission_registry.json`、`permission-model.md`、`topic_registry.json`、`根_CLAUDE.md` | 未迁移 | 权限、Topic 路由和会话隔离由 Gateway/MA 资源配置承担 |
| 所有 `.DS_Store`、`__pycache__/` | 未迁移/忽略 | 本地系统或解释器缓存，不属于源码 |

## 十、维护规则

1. 客户文件只作为迁移基线，MA 生效源码统一修改 `ma-resources/`。
2. 修改 Skill 后依次运行 `tools/pack_skills.sh`、`tools/upload_skills.py`、`ma-resources/create_all.sh`。
3. 原样迁入项可用 SHA-256 检查漂移；适配项应以本表记录的来源和职责为审查基线。
4. 新增、拆分、合并或删除文件时，同步更新本表和 `ma-resources/version_mapping.md`。

## 十一、核对中发现的待修正引用

| 引用位置 | 当前引用 | 实际文件 | 结论 |
|---|---|---|---|
| `agents/coordinator.system.md` 的筛选失败处理 | `retry_missing.py --task c0` | MA Skill 中无 `retry_missing.py` 或 `c0_retry_missing.py` | Prompt 残留客户版命令，需要改成现有重试方案或补回脚本 |
| `agents/insighter.system.md` 的 E2 统计表 | `01_统计/e2_marketing_node.py` | `01_统计/e2_nodes.py` | 文件名引用错误 |
| `agents/insighter.system.md` 的 E3 统计表 | `01_统计/e3_platform.py` | `01_统计/e3_platforms.py` | 文件名引用错误 |
