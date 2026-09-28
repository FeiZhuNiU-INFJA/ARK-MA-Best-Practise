# topic6 · MA 约束变更日志

只记录 **因方舟 Managed Agents 的限制/机制** 引发的改动。纯代码 bug、口径微调、文案润色一律不进。

每条至少包含:
- 触发现象(报错/异常表现)
- MA 侧根因(为什么这个约束存在)
- 影响文件 & 参考出处

按日期倒序。

---

## 2026-09-24

### Agent Prompt 静态路径与 skill 挂载目录逐字符对齐

- **现象**:coordinator/insighter prompt 里 8 处 `/mnt/skills/...` 死链,导致 Coordinator 首次运行读契约文件时报 `not_found`。具体包括 `topic6-annotation/prompt/`(单复数错)、`/mnt/memory/topic6/xxx.md` 占位举例、`topic6-event-registry/scripts/00_run_all.sh` 不存在、`marketing_calendar_2026.csv` 错误 skill 前缀、`topic6-web-report/scripts/build-report.mjs` 不存在、`topic6-insight/ks/07_报告结构.md` 错误 skill 前缀等。
- **MA 侧根因**:方舟沙箱把 skill 包挂载在 `/mnt/skills/{skill_key}/`,是**包内目录的直投射**——不做路径重写、不做别名、不容错。这是与 Claude Code(客户原 Bot 可读整个客户机文件系统)的显著契约差异。所以 prompt 里所有静态路径必须逐字符对应 `ma-resources/skills/{skill_key}/` 下的真实布局。
- **影响文件**:
  - [agents/coordinator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.system.md)
  - [agents/insighter.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/insighter.system.md)
  - [skills/topic6-annotation/prompts/](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/prompts)(补 fork 11 个客户 prompt md,含 `run_config契约.md` / `00_角色与触发.md` / `01_pipeline总览.md` 等)
  - [skills/topic6-fetch-normalize/references/marketing_calendar_2026.csv](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-fetch-normalize/references/marketing_calendar_2026.csv)(补 fork)
- **应对规约**:后续任何 Agent Prompt 修改,必须跑一次静态校验(见 `/tmp/check_topic6_paths.py`)作为准入 gate;新 skill 上线时同步核对 SKILL.md 里的目录索引与磁盘实际结构。

## 2026-09-24

### SKILL.md 必须带 YAML frontmatter,且 `name` 匹配 `^[a-z0-9-]{1,64}$`

- **现象**:`POST /api/v3/skills` 返回 `400 InvalidParameter`,body 里明确报 `SKILL.md frontmatter name must match ^[a-z0-9-]{1,64}$ (got "topic6-fetch-normalize · MA 口径 v1")`。
- **MA 侧根因**:方舟 CreateSkill 会强制解析 `SKILL.md` 的 YAML frontmatter,`name` 是 skill 的稳定标识,只允许小写字母/数字/连字符,长度 1~64。若 frontmatter 缺失,则退化到用 H1 标题当 name——H1 含空格、中文、`·` 就直接 400。
- **影响文件**:
  - [topic6-fetch-normalize/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-fetch-normalize/SKILL.md)
  - [topic6-annotation/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-annotation/SKILL.md)
  - [topic6-insight/SKILL.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/skills/topic6-insight/SKILL.md)
- **参考**:[火山方舟 MA 文档](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1451-L1470)("按以下约束组织自定义 Skills")、event-registry / web-report 两个已经带 frontmatter 的 SKILL.md 做参照。
- **应对规约**:后续新增 skill 必须先写 frontmatter(`name` / `version` / `description`),再落 Markdown 正文。命名统一走 `topic6-<kebab-case>`。

### CreateSkill 必须带 `X-Ark-Beta: agentic-2026-06-01` header

- **现象**:`POST /api/v3/skills` 返回 `404 Not Found`,body 为空(不是 401/403,方舟直接当路径不存在)。同样规律也命中 `/api/v3/environments` `/api/v3/memory_stores` `/api/v3/agents` `/api/v3/sessions`——只要不带 beta header,curl 一律 404。
- **MA 侧根因**:整个 Managed Agents 面(environments / memory_stores / agents / sessions / skills)都属方舟 **agentic beta 面**,和普通 v3 API(chat/embedding 等)不共用路由。beta 面要求请求头显式声明版本 `X-Ark-Beta: agentic-2026-06-01`,不带就路由不到,直接 404。
- **影响文件**:
  - [tools/upload_skills.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/tools/upload_skills.py)
  - [ma-resources/create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh)(所有 curl 统一走 `ark_post` 封装,顺带解决 response 有时包 `.data` 壳的问题——用 `.data.id // .id` 兼容)
- **参考**:同仓 [ark_min.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/ma-replica/skills/ma-replica-builder/scripts/ark_min.py#L29-L30) 已验证过的写法、[MA 文档 L1477](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1477)。
- **应对规约**:所有直接打 `/api/v3/*` (MA 面) 的脚本必须带 beta header。走火山官方 SDK 的话 SDK 会自动补,不用管。

### CreateEnvironment 请求体必须嵌套在 `config` 下,且字段名固定为 `packages.pip` / `env` / `tos`

- **现象**:`POST /api/v3/environments` 返回 `400`。
- **MA 侧根因**:方舟 Environment 的 API 契约把所有沙箱配置塞在 `config` 对象里,顶层只放 `name` / `description`。若把 `type` / `networking` / `pip_packages` / `env_vars` 直接平铺到顶层,或用 `pip_packages` / `env_vars` 这类自造字段名,后端解析不到必需字段,返回 InvalidParameter。方舟对未知字段的容忍度比想象中低——不认识的字段直接 400,不会 silently ignore。
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
- **MA 侧根因**:方舟 Memory Store 的 path 用绝对路径语义(会被沙箱只读挂载到 `/mnt/memory/{path}`),入参必须以 `/` 开头。写成相对路径(如 `topic6/MEMORY.md`)后端不做归一化,直接 400。
- **影响文件**:
  - [ma-resources/memory-store.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/memory-store.json)(3 条 path 全部补 `/` 前缀)
  - [ma-resources/create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh)(POST 前兜底补 `/`,防未来漏改)
- **应对规约**:后续任何 memory-store.json 新增条目,path 必须写 `/topic6/...`。Agent prompt 里引用时,挂载点是 `/mnt/memory` + path,即 `/mnt/memory/topic6/MEMORY.md`,与旧口径完全一致。

### Agent `tools` 只认 `agent_toolset_20260701` / `custom` / `evolution` 三种 type

- **现象**:`POST /api/v3/agents` 返回 `400 InvalidParameter: tools[0].type: unsupported tool type "bash"`。
- **MA 侧根因**:方舟 MA 把 Claude Code 里散装的 `bash` / `read` / `write` / `edit` / `glob` / `grep` / `web_fetch` / `web_search` **聚合成一个内置工具集**,type 只写一个 `agent_toolset_20260701` 就默认全开(见 [MA 文档 L1881-L1898](file:///Users/bytedance/workspace/ark-agent-feishu-bot/common/docs/火山方舟_ManagedAgents_docs.md#L1881))。真正合法的 tool type 只有三种:`agent_toolset_20260701`(内置)、`custom`(业务侧回调)、`evolution`(演进能力,含 advisor)。
- **影响文件**:
  - [agents/annotator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/annotator.json)
  - [agents/insighter.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/insighter.json)
  - [agents/coordinator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.json)
  - 全部改为 `[{"type": "agent_toolset_20260701"}]`
- **应对规约**:后续新增 Agent 定义,`tools` 只允许出现上述三种 type。如果要精细化开关内置工具中的某几个(比如禁 web_search 省钱),用 `permission_policy` 而不是删条目;权限模型默认 `always_allow`。

### Agent `mcp_servers[].type` 必填,当前仅支持 `"url"`

- **现象**:`POST /api/v3/agents` 返回 `400 InvalidParameter: mcp_servers[0].type: must be "url" (got "")`。
- **MA 侧根因**:方舟 MCP server 定义走 discriminated union,`type` 是分派字段——即便当前实现只有 URL 一种,也必须显式声明(未来可能引入 `stdio`/`sse`/`streamable_http` 等子类)。省略等价于类型未定,后端一律拒绝。
- **影响文件**:[agents/coordinator.json](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.json) `mcp_servers[0]` 补 `"type": "url"`。
- **应对规约**:后续任何 Agent 定义,`mcp_servers[]` 每条必须至少含 `type` / `name` / `url` 三字段,不留隐式默认。
