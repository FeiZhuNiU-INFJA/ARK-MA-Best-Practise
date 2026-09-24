# 数字员工可视化管理系统（admin）

给「被拉进飞书群的数字员工」做可视化配置的后端 + 静态前端：管理 **identity（身份）/ knowledge / skills / mcp / memory（记忆）**，并按 **项目 → 多个飞书群（共享群记忆）** 的模型组织。

配置库是**全量权威**（方案 A）：所有配置先落本地 SQLite，再由**控制面单向同步**到方舟（建/更新 Agent、懒建 Memory Store、置备 Environment/Vault）。方舟侧资源是被同步的下游。

> **仅限本地/内网使用**：鉴权只做了简单 token，切勿直接暴露公网。

---

## 分层架构（ark-gateway：控制面 / 数据面）

管理系统建在 **ark-gateway 控制面**上，runtime（`digital_employee.py`）建在**控制面 + 数据面**上。二者共享配置库与「配置→MA」映射逻辑，与飞书传输层（`lark-channel`）正交。

```
┌──────────────────────────────────────────────────────────────────────────┐
│  应用层                                                                     │
│   ┌────────────────────────┐        ┌──────────────────────────────────┐  │
│   │  管理系统 admin/          │        │  runtime digital_employee.py      │  │
│   │  server.py + web/        │        │  TopicSessionBot                  │  │
│   │  建/改 员工·项目·bundle    │        │  飞书消息 → MA session            │  │
│   │  触发同步·编辑记忆         │        │  生命周期·附件·鉴权·回复           │  │
│   └───────────┬────────────┘        └───────────┬──────────────────────┘  │
│               │ 依赖 控制面                        │ 依赖 控制面 + 数据面      │
└───────────────┼───────────────────────────────────┼───────────────────────┘
                │                                   │
┌───────────────▼───────────────────────────────────▼───────────────────────┐
│  ark-gateway  (arkagent/gateway/*)                                          │
│   ┌───────────────────────────┐   ┌───────────────────────────────────┐   │
│   │  控制面 MAControlPlane      │   │  数据面 MADataPlane                │   │
│   │  control.py                │   │  data.py                           │   │
│   │  ─ build_agent_config      │   │  ─ resolve_runtime_config(路由)     │   │
│   │  ─ sync_employee(建/改Agent)│   │  ─ resolve_memory_scope(项目作用域) │   │
│   │  ─ sync_project_memory     │   │  ─ writable_categories_for_scope   │   │
│   │  ─ ensure_environment/vault│   │  ─ decide_reply_strategy(话题开关)  │   │
│   │  ↕ 操作 Agent 定义层        │   │  ↕ 操作 Session 运行层             │   │
│   └─────────────┬─────────────┘   └──────────────┬────────────────────┘   │
│                 │        共享 ConfigStore + 映射逻辑 │                        │
│                 └──────────────┬───────────────────┘                        │
└────────────────────────────────┼───────────────────────────────────────────┘
                                 │ 都通过 ArkClient 访问 MA
              ┌──────────────────┴───────────────────┐
              ▼                                      ▼
┌──────────────────────────┐            ┌──────────────────────────────────┐
│  ArkClient (arkagent/ark) │            │  ConfigStore (gateway/config_store)│
│  MA REST 裸封装            │            │  配置库=全量权威 (SQLite)          │
└─────────────┬────────────┘            └──────────────────────────────────┘
              │ HTTPS
              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  火山方舟 Managed Agents（Agent 定义层 + Session 运行层）                    │
└──────────────────────────────────────────────────────────────────────────┘
```

**要点**：
- 管理系统**只碰控制面**，不需要飞书 channel、也不装配 session。
- runtime **以数据面为主 + 少量控制面**，并额外依赖 `lark-channel` 完成飞书收发。
- 同步严格**单向**（配置库 → 方舟），不做反向回捞。

---

## 数据模型（配置库 = `data/digital_employee_admin.db`）

| 实体 | 表 | 关键字段 | 说明 |
|---|---|---|---|
| **数字员工 / persona** | `digital_employee` | `identity_prompt`（→Agent `system`）、`model_id`、`bundle_id`、`ark_agent_id/version`、`sync_status` | 一个数字员工 ⇔ 方舟一个 Agent 定义 |
| **项目** | `project` | `memory_store_id`、`writable_memory_categories`、`reply_uses_topic`、`multimodal/markdown_enabled` | 一个项目含多个群，群间共享群记忆（scope 键 = `project_id`） |
| **群绑定（路由表）** | `feishu_group_binding` | `chat_id` → `project_id` + `digital_employee_id`（`tenant_key` 仅作归属列） | runtime 的核心路由 |
| **能力包 / bundle** | `capability_bundle` | `skills`、`mcp_servers`、`builtin_tool_toggles`、`writable_memory_categories` | 具名可复用的能力/权限集合 |
| **同步日志** | `sync_log` | `entity_type/action/status/detail` | 同步审计 |

> 路径可用环境变量 `DIGITAL_EMPLOYEE_ADMIN_DB_PATH` 覆盖；与会话库 / 记忆库物理分离。

---

## API 一览（`admin/server.py`）

所有 `/api/*` 在设了 `ADMIN_API_TOKEN` 时需带 `Authorization: Bearer <token>`（或 `X-Admin-Token: <token>`）。

| 方法 & 路径 | 作用 |
|---|---|
| `GET /api/overview` | 总览：各实体计数 + 最近同步日志 |
| `GET/POST /api/employees`，`GET/PUT/DELETE /api/employees/{id}` | 数字员工 CRUD |
| `GET/POST /api/projects`，`GET/PUT/DELETE /api/projects/{id}` | 项目 CRUD |
| `GET/POST /api/bindings`，`DELETE /api/bindings/{chat_id}` | 群绑定 列/增改/删（`GET` 可带 `?project_id=`） |
| `GET/POST /api/bundles`，`GET/PUT/DELETE /api/bundles/{id}` | 能力包 CRUD |
| `GET/POST /api/memory/{project_id}` | 项目共享记忆 列/新增（透传方舟 memory API） |
| `GET/PUT/DELETE /api/memory/{project_id}/{memory_id}` | 单条记忆 读/改/删 |
| `POST /api/sync/employee/{id}` | 同步单个数字员工到方舟（无 `ark_agent_id` 则建 Agent，有则带 version 更新） |
| `POST /api/sync/project-memory/{id}` | 懒建项目共享 Memory Store，回填 `memory_store_id` |
| `GET /api/sync/logs` | 最近同步日志 |

> 记忆相关接口要求项目已有 `memory_store_id`；未创建时返回 `409`，先调 `POST /api/sync/project-memory/{id}`。

---

## 前端页面（`admin/web/`，静态 HTML + 原生 JS，无构建）

| 页面 | 职责 |
|---|---|
| **总览** | 实体计数卡片 + 最近同步日志 |
| **数字员工** | 建/改身份提示词、模型、引用能力包；一键「同步」到方舟；查看 Agent ID/版本/同步状态 |
| **项目** | 建/改项目开关（话题/多模态/Markdown）、可写记忆子集；一键「建 Store」 |
| **群绑定** | 维护 `chat_id → 项目 + 员工` 路由表 |
| **能力包** | 编辑 skills / mcp_servers（JSON）、内置工具开关、可写记忆子集 |
| **项目记忆** | 选项目 → 懒建 Store → 列/增/改/删共享记忆条目（透传方舟） |

顶部 token 输入框：开启鉴权时填入，前端会随每个请求带上。

---

## 启动

```bash
set -a && source ~/.arkagent/config.env && set +a   # 需要 ARK_API_KEY[/ARK_BASE_URL]
python scenarios/feishu-bot/cases/digital-employee/admin/run_admin.py
# 打开 http://127.0.0.1:8787
```

可选环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `ADMIN_HOST` | `127.0.0.1` | 监听地址 |
| `ADMIN_PORT` | `8787` | 监听端口 |
| `ADMIN_API_TOKEN` | 空 | 非空则开启 token 鉴权；空=无鉴权（仅本地/内网） |
| `ARK_BASE_URL` | 北京 | 方舟 API 基址 |
| `DIGITAL_EMPLOYEE_ADMIN_DB_PATH` | `data/digital_employee_admin.db` | 配置库路径 |

---

## 与 runtime 的衔接

1. **管理系统**建员工/项目/能力包 → 编辑记忆 → 点「同步」把 Agent 推上方舟、点「建 Store」懒建项目共享 Memory Store。
2. **绑群**：把 `chat_id` 绑到项目 + 数字员工。
3. **runtime**（`digital_employee.py`）收到 `@bot` 消息后，经**数据面** `resolve_runtime_config` 按 `chat_id` 查绑定，路由出对应 Agent 与项目共享 Store；群记忆作用域用 `project_id`，实现「同项目多群共享群记忆」；`reply_uses_topic` 决定回复走话题还是直发群聊。
4. **无绑定**时 runtime 回退现有 env 单员工模式（向后兼容）。

> 同步严格单向：管理系统改配置库 → 同步到方舟。不从方舟反向回捞，避免双向一致性复杂度。
