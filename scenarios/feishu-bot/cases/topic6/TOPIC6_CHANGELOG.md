# topic6 · 变更日志

记录影响 Topic6 运行流程、交付行为和方舟 Managed Agents 适配的变更。纯文案润色不进。

每条至少包含:
- 触发现象(报错/异常表现)
- 根因(为什么这个约束存在)
- 影响文件 & 参考出处

按日期倒序。

---

## 2026-09-29

### Phase C 性能与稳定性优化，新增 500 条演示模式

- **触发现象**：一次 test 轨迹中，C0/C3 虽于 `10:35:24` 并发启动，但 500 条数据分别耗时约 41/42 分钟；C0 使用约 572 万 tokens，C3 使用约 258 万 tokens。两路 DataHub 任务成功后均未返回 `result_url`，导致统一脚本报错，子 Agent 被迫手工从 `result_list` 分页恢复结果。
- **根因**：
  - DataHub 单任务吞吐约 12 行/分钟，是本轮墙钟时间的主要瓶颈；C0/C3 Prompt 较长则进一步放大 token 成本。
  - DataHub 成功响应存在两种结果形态：下载链接 `result_url`，或内联/分页 `result_list`；原脚本只支持前者。
  - 子 Agent 契约包含不存在的 `--output`、字符串 `--run-id` 和错误的成本命令，造成启动前纠错与手工兜底。
  - C0 解析失败行此前仍会进入 R1~R5，一条失败最多放大为五路无效调用。
- **实现**：
  - `datahub_annotate.py` 在 `result_url` 缺失时自动解析并分页拉取 `result_list`，兼容嵌套输入字段和结果字段别名，严格校验最终行数及 `llm_result`。
  - C0 筛选改为只有明确判定“是否营销可用=是”的记录进入 R1~R5；解析失败/缺失占比超过 5% 仍熔断。
  - 修正 Annotator/Coordinator 契约：Prompt 由脚本直接读取，不再先灌入 Agent 上下文；`run_id` 使用整数；成本按 completion metadata 的实际模型、平台、token 和 `total_consume` 记录。
  - Environment 增加 `lunardate>=0.2.2`，避免 C3 后处理临时安装依赖。
- **demo 模式**：
  - 新增触发词 `热点周报 demo`，固定使用 500 条分层样本。
  - 流程为 `A/B → 抽样 → C/D → HC1 → E/F → HC3 → G/H`；HC1 通过后明确跳过全量 C/D 和 HC2。
  - demo 使用独立的 `wide_table_demo_r{N}.xlsx`，不伪装成 full；最终报告自动标注“基于 500 条分层样本，仅供流程演示，不可作为正式全量结论”。
  - 原 `test` 保持“样本校准后继续全量”的语义，`full` 保持直接跑全量。
- **未默认启用**：Prompt 大幅裁剪、模型切换和 DataHub 多分片并发仍需先做质量、限流与重复计费基准，避免以未经验证的方式影响生产结果。
- **验证与发布**：完整测试集 `382 passed`；5 个 Skill 打包校验通过；已更新 Environment 并重建 Annotator、Insighter、Coordinator。
- **影响文件**：
  - [topic6_runner.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/topic6_runner.py)
  - [agents/coordinator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.system.md)
  - [agents/annotator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/annotator.system.md)
  - [agents/insighter.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/insighter.system.md)
  - [datahub_annotate.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/scripts/datahub_annotate.py)
  - [c0_filter_usable.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/scripts/c0_filter_usable.py)
  - [pipeline_e.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-insight/scripts/pipeline_e.py)
  - [pipeline_f.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-insight/scripts/pipeline_f.py)
  - [environment.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/environment.json)

### 对齐 2026-09-29 Managed Agents 文档更新

- **MCP Toolset**：`mcp_toolset` 必须作为 `tools[]` 条目，并通过 `mcp_server_name` 与 `mcp_servers[]` 一一对应；权限策略形状为 `default_config.permission_policy.type`。已修正 `agents/coordinator.json`，继续对只读热点数据 MCP 显式使用 `always_allow`。
- **SSE 启动顺序**：打开事件流后必须等到 `: ready`，再发送首个 `user.message`。已由共享 `_EventStream` 在进入上下文前消费 ready 信号，Topic 6 runner 无需自行解析 SSE 注释。
- **Session 终态**：`session.status_terminated` 是不可继续发送事件的终态，已按失败处理；正常轮次完成仍以 `session.status_idle` 为准。
- **Skill 上传限制**：上传 ZIP 不超过 30 MiB；解压后单文件不超过 30 MiB、总大小不超过 120 MiB、最多 500 个文件；统一顶层目录下直接包含唯一 `SKILL.md`。`tools/pack_skills.sh` 已同步全部门禁。
- **Memory 更新**：创建同路径 Memory 不会覆盖原内容。`create_all.sh --update-memory` 现在先按 path 查找 Memory ID，存在则调用更新接口，不存在才创建。
- **无需修改**：Topic 6 仅挂载 5 个 Skills，未触及单 Agent 50 个上限；Memory 继续使用 `read_only`，不启用本次新增明确化的 `read_write` 能力；Multi Agent 仍是一层协调器到子 Agent，符合嵌套限制。

## 2026-09-24

### Agent Prompt 静态路径与 skill 挂载目录逐字符对齐

- **现象**:coordinator/insighter prompt 里 8 处 `/mnt/skills/...` 死链,导致 Coordinator 首次运行读契约文件时报 `not_found`。具体包括 `topic6-annotation/prompt/`(单复数错)、`/mnt/memory/topic6/xxx.md` 占位举例、`topic6-event-registry/scripts/00_run_all.sh` 不存在、`marketing_calendar_2026.csv` 错误 skill 前缀、`topic6-web-report/scripts/build-report.mjs` 不存在、`topic6-insight/ks/07_报告结构.md` 错误 skill 前缀等。
- **根因**:方舟沙箱把 skill 包挂载在 `/mnt/skills/{skill_key}/`,是**包内目录的直投射**——不做路径重写、不做别名、不容错。prompt 里所有静态路径必须逐字符对应 `ma-resources/skills/{skill_key}/` 下的真实布局。
- **影响文件**:
  - [agents/coordinator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.system.md)
  - [agents/insighter.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/insighter.system.md)
  - [skills/topic6-annotation/prompts/](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/prompts)(补齐 11 个 prompt md,含 `run_config契约.md` / `00_角色与触发.md` / `01_pipeline总览.md` 等)
  - [skills/topic6-fetch-normalize/references/marketing_calendar_2026.csv](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-fetch-normalize/references/marketing_calendar_2026.csv)(补齐)
- **应对规约**:后续任何 Agent Prompt 修改,必须跑一次静态校验(见 `/tmp/check_topic6_paths.py`)作为准入 gate;新 skill 上线时同步核对 SKILL.md 里的目录索引与磁盘实际结构。

## 2026-09-24

### SKILL.md 必须带 YAML frontmatter,且 `name` 匹配 `^[a-z0-9-]{1,64}$`

- **现象**:`POST /api/v3/skills` 返回 `400 InvalidParameter`,body 里明确报 `SKILL.md frontmatter name must match ^[a-z0-9-]{1,64}$ (got "topic6-fetch-normalize · v1")`。
- **根因**:方舟 CreateSkill 会强制解析 `SKILL.md` 的 YAML frontmatter,`name` 是 skill 的稳定标识,只允许小写字母/数字/连字符,长度 1~64。若 frontmatter 缺失,则退化到用 H1 标题当 name——H1 含空格、中文、`·` 就直接 400。
- **影响文件**:
  - [topic6-fetch-normalize/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-fetch-normalize/SKILL.md)
  - [topic6-annotation/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/SKILL.md)
  - [topic6-insight/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-insight/SKILL.md)
- **参考**:[火山方舟 MA 文档](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1451-L1470)("按以下约束组织自定义 Skills")、event-registry / web-report 两个已经带 frontmatter 的 SKILL.md 做参照。
- **应对规约**:后续新增 skill 必须先写 frontmatter(`name` / `version` / `description`),再落 Markdown 正文。命名统一走 `topic6-<kebab-case>`。

### CreateSkill 必须带 `X-Ark-Beta: agentic-2026-06-01` header

- **现象**:`POST /api/v3/skills` 返回 `404 Not Found`,body 为空(不是 401/403,方舟直接当路径不存在)。同样规律也命中 `/api/v3/environments` `/api/v3/memory_stores` `/api/v3/agents` `/api/v3/sessions`——只要不带 beta header,curl 一律 404。
- **根因**:整个 Managed Agents 面(environments / memory_stores / agents / sessions / skills)都属方舟 **agentic beta 面**,和普通 v3 API(chat/embedding 等)不共用路由。beta 面要求请求头显式声明版本 `X-Ark-Beta: agentic-2026-06-01`,不带就路由不到,直接 404。
- **影响文件**:
  - [tools/upload_skills.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/tools/upload_skills.py)
  - [ma-resources/create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh)(所有 curl 统一走 `ark_post` 封装,顺带解决 response 有时包 `.data` 壳的问题——用 `.data.id // .id` 兼容)
- **参考**:同仓 [ark_min.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/ma-replica/skills/ma-replica-builder/scripts/ark_min.py#L29-L30) 已验证过的写法、[MA 文档 L1477](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1477)。
- **应对规约**:所有直接打 `/api/v3/*` (MA 面) 的脚本必须带 beta header。走火山官方 SDK 的话 SDK 会自动补,不用管。

### CreateEnvironment 请求体必须嵌套在 `config` 下,且字段名固定为 `packages.pip` / `env` / `tos`

- **现象**:`POST /api/v3/environments` 返回 `400`。
- **根因**:方舟 Environment 的 API 契约把所有沙箱配置塞在 `config` 对象里,顶层只放 `name` / `description`。若把 `type` / `networking` / `pip_packages` / `env_vars` 直接平铺到顶层,或用 `pip_packages` / `env_vars` 这类自造字段名,后端解析不到必需字段,返回 InvalidParameter。方舟对未知字段的容忍度比想象中低——不认识的字段直接 400,不会 silently ignore。
- **影响文件**:[ma-resources/environment.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/environment.json) 整体重构。
- **正确形状**(节选,详见 [MA 文档 L2430-L2490](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L2430)):
  ```json
  {
    "name": "...",
    "description": "...",
    "config": {
      "type": "cloud",
      "networking": { "type": "unrestricted" },
      "packages": { "pip": [...], "apt": [...] },
      "env": { "KEY": "VALUE" },
      "tos": { "bucket": "...", "prefix": "..." }
    }
  }
  ```
- **额外副作用**:方舟对 `config.env` 里的 `${VAR}` 字面串**不做二次插值**,不预处理就会把 `"${DATAHUB_ENDPOINT}"` 死字符串灌进沙箱。故 [create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh) 在 POST 前用 `envsubst` 展开一次。
- **应对规约**:后续任何 environment.json 改动,`config` 之外只加 `name`/`description`;未知字段(如自造的 `_output_storage_disabled`)禁止入库,注释走 markdown/changelog。

### Memory `path` 必须以 `/` 开头

- **现象**:`POST /api/v3/memory_stores/{id}/memories` 返回 `400 InvalidParameter: path must start with /`。
- **根因**:方舟 Memory Store 的 path 用绝对路径语义(会被沙箱只读挂载到 `/mnt/memory/{path}`),入参必须以 `/` 开头。写成相对路径(如 `topic6/MEMORY.md`)后端不做归一化,直接 400。
- **影响文件**:
  - [ma-resources/memory-store.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/memory-store.json)(3 条 path 全部补 `/` 前缀)
  - [ma-resources/create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh)(POST 前兜底补 `/`,防未来漏改)
- **应对规约**:后续任何 memory-store.json 新增条目,path 必须写 `/topic6/...`。Agent prompt 里引用时,挂载点是 `/mnt/memory` + path,即 `/mnt/memory/topic6/MEMORY.md`,与旧口径完全一致。

### 内置工具必须通过 `agent_toolset_20260701` 配置

- **现象**:`POST /api/v3/agents` 返回 `400 InvalidParameter: tools[0].type: unsupported tool type "bash"`。
- **根因**:方舟 MA 把 `bash` / `read` / `write` / `edit` / `glob` / `grep` / `web_fetch` / `web_search` **聚合成一个内置工具集**,type 只写一个 `agent_toolset_20260701` 就默认全开(见 [MA 文档 L1881-L1898](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1881))。除内置工具集外，`tools[]` 还可包含 `custom`、`evolution` 和 `mcp_toolset`；不能把 `bash`、`read` 等单个内置工具名直接写成 type。
- **影响文件**:
  - [agents/annotator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/annotator.json)
  - [agents/insighter.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/insighter.json)
  - [agents/coordinator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.json)
  - 全部改为 `[{"type": "agent_toolset_20260701"}]`
- **应对规约**:后续新增 Agent 定义，内置工具统一使用 `agent_toolset_20260701`；通过 `configs[].enabled` 控制单个工具启停，通过 `default_config.permission_policy` 或 `configs[].permission_policy` 控制执行前是否确认。

### Agent `mcp_servers[].type` 必填,当前仅支持 `"url"`

- **现象**:`POST /api/v3/agents` 返回 `400 InvalidParameter: mcp_servers[0].type: must be "url" (got "")`。
- **根因**:方舟 MCP server 定义走 discriminated union,`type` 是分派字段——即便当前实现只有 URL 一种,也必须显式声明(未来可能引入 `stdio`/`sse`/`streamable_http` 等子类)。省略等价于类型未定,后端一律拒绝。
- **影响文件**:[agents/coordinator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.json) `mcp_servers[0]` 补 `"type": "url"`。
- **应对规约**:后续任何 Agent 定义,`mcp_servers[]` 每条必须至少含 `type` / `name` / `url` 三字段,不留隐式默认。
