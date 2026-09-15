# 场景1：MA × 飞书 Bot

> 一个可运行的骨架 + 一组**案例**：把飞书对话机器人接到火山方舟 Managed Agents（MA）。

飞书用户 @ Bot → 本地 Gateway（Python）→ 火山方舟 MA Session →（按案例）MCP Server。
主体是 Python；只有「扫码创建飞书应用」这一步没有 Python 等价物，保留为一个 Node 小岛（子进程调用）。

## 本场景的案例

| 案例 | 涉及能力 | 目录 |
| --- | --- | --- |
| **客户A 四卡点 ABCD** | 身份鉴权(static_bearer) · OpenID 透传 · 岗位信息注入 · 跨 Session 记忆 | [cases/customer-a-4checkpoints/](cases/customer-a-4checkpoints/) |
| **群聊共享 Bot** | 群内多人共享同一 Session（对齐 Claude Tag）· 串行 vs 方舟原生队列 | [cases/group-bot/](cases/group-bot/) |

> 客户A 是「文档型 case」：它的运行代码就是本场景共享的 `arkagent/` + `mock_mcp/`；群聊共享 Bot
> 是独立示例脚本，**复用** `arkagent` 的纯基础设施，不改主包。

## 通用架构

```text
飞书用户 ──@──> Bot（长连接收消息）
                 │
                 ▼
          本地 Gateway（Python）
   去重 · Session 复用 · 指令分发（/new /role /whoami /remember …）
                 │
                 ├── 创建 Session：environment_with_overrides（注入会话级环境变量）
                 │                 vault_ids（凭据，如 static_bearer）
                 │                 resources（挂载 Memory Store → /mnt/memory/）
                 ├── 发消息：user.message ＋（按需）system.message
                 ▼
      火山方舟 Managed Agents Session
                 │  连 MCP 时按凭据注入鉴权头
                 ▼
             MCP Server（按案例，公网可达）
```

各能力点如何落到具体客户问题上，见 [cases/customer-a-4checkpoints/](cases/customer-a-4checkpoints/) 的文档。

## 准备

- **conda**（管理 Python 环境）；
- 一个可用的**火山方舟 Managed Agents API Key**（`init` 掩码输入）；
- 一个能创建企业自建应用的**飞书账号**（用于扫码建应用）；
- 一个**内网穿透工具**（推荐 [cpolar](https://www.cpolar.com)，国内节点更稳；ngrok / frp 亦可），把本地 MCP 暴露成公网 HTTPS——方舟创建 `static_bearer` 等凭据时会**立即握手探测** MCP，不可达会直接失败。

## 快速开始

打包配置在**仓库根**（`pyproject.toml` / `environment.yml`），命令从仓库根执行：

```bash
# 1) 环境
conda env create -f environment.yml
conda activate customer-a-ma-demo
pip install -e ".[dev]"                                   # 安装本包（arkagent / customer-a-mock-mcp 命令）
(cd scenarios/feishu-bot/node-helper && npm install)      # 装 Node 小岛依赖（registerApp 扫码用）
pytest -q                                                 # 确认环境就绪

# 2) 初始化（只扫一次码：建飞书应用 + 建 Agent/Vault/Environment，写 config）
arkagent init
arkagent doctor                                           # 检查配置

# 3) 运行
arkagent run
```

各案例可能需要先起自己的 MCP（如客户A案例的 mock）并暴露公网，再 `init`——以案例文档为准。

## `arkagent` CLI

| 命令 | 作用 |
| --- | --- |
| `arkagent init` | 首次初始化：掩码输入 API Key → 建 Agent（挂 `mcp_servers` + `mcp_toolset`）→ 扫码建飞书应用（仅 tenant 身份，无用户 OAuth）→ 建/复用 Vault + 凭据 → 建/复用 Environment → 写 `~/.arkagent/config.env`（目录 `0700`、文件 `0600`）。 |
| `arkagent doctor` | 检查配置完整性。 |
| `arkagent run` | 启动 Gateway，连飞书 WebSocket 收发消息。 |
| `arkagent update-agent` | **日常迭代**：改了 Agent 的 system prompt / 工具配置后，读 `config.env` 里现有 `ARK_AGENT_ID`，用最新配置原地更新（方舟 Agent 是版本化资源，更新生成新版本，**Agent ID 不变、飞书 Bot 不动**）。**不需要**重跑 `init` 重新扫码。 |

> 只在**首次**、或**换了 MCP 公网地址**（需把新 URL 写进 Agent 定义）时才需 `init`。仅换地址也可先改 `config.env` 的 `MCP_SERVER_URL` 再 `update-agent`。

## 配置项（`~/.arkagent/config.env`）

`init` 自动写入；也可参考 [.env.example](.env.example)。

| Key | 说明 |
| --- | --- |
| `ARK_API_KEY` | 方舟 API Key，Gateway 调用 MA API |
| `ARK_AGENT_ID` / `ARK_ENVIRONMENT_ID` / `ARK_VAULT_ID` | init 创建的资源 ID |
| `ARK_BASE_URL` | 方舟 API 基址（默认北京） |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 扫码创建的飞书应用凭证（WebSocket 鉴权 + 发消息） |
| `MCP_SERVER_URL` | MCP 公网地址（含 `/mcp`） |
| `GATEWAY_DB_PATH` | SQLite 状态库（会话映射 / 事件去重 / 岗位缓存），默认 `./data/gateway.db` |
| `SESSION_TIMEOUT_MS` | 单次运行超时，默认 600000 |
| `ROLE_TTL_MS` | 岗位缓存 TTL，默认 86400000（24h） |
| `AUTHORIZED_OPEN_IDS` | 允许对话的 open_id 白名单（逗号分隔；**留空 = 不限制**） |
| `TEAM_STORE_ENABLED` | 是否为同岗位挂载团队共享 Memory Store（可选） |

配置目录 `0700`、文件 `0600`；不要在 Agent prompt 或日志中打印凭证。

## 消息与 Session 行为

- 单聊处理文本消息；群聊只处理明确 `@Bot` 的文本消息。
- 会话隔离键为 `(tenant_key, chat_id, thread_id, user_open_id)` 四元组：**不同发送者不复用 Session**（群里各人独立）；`/new` 显式重置当前会话。
- 新建 Session 会立即回复「正在处理」；复用 Session 仅在超过 ~2.5 秒未完成时提示一次。
- Gateway 不转发工具执行过程，只发处理中提示和最终结果。
- 图片、文件、富文本、交互卡片暂不处理。

## 目录结构

```text
scenarios/feishu-bot/
  arkagent/            # Python 主体（安装后提供 arkagent 命令）
    __init__.py        # 包声明
    __main__.py        # python -m arkagent 入口，转调 cli.main()
    cli.py             # 命令分发：init / doctor / run / update-agent；WS 主线程 + asyncio 后台线程
    init.py            # 首次初始化：建 Agent/Vault/凭据/Environment，写 config.env
    node_helper.py     # 以子进程调用 node-helper/register_app.mjs（扫码建应用），读回凭证
    gateway.py         # 编排核心：去重 / Session 复用 / 指令分发 / 组装环境变量·凭据·记忆
    ark.py             # 火山方舟 Managed Agents httpx 异步客户端 + SSE 解析
    feishu.py          # 飞书长连接（WS）收消息 + im.v1 发消息 + 消息归一化
    role.py            # 岗位缓存 + 动态 system.message 注入（含可替换的 HR Provider）
    memory.py          # 每用户 / 团队 Memory Store 的创建与挂载、/remember 写入
    store.py           # SQLite 状态：会话映射 / 事件去重 / 岗位缓存 / Store 映射
    config.py          # 加载校验 config.env（KEY="json-value"），产出 GatewayConfig
    paths.py           # 解析 ~/.arkagent/{config.env,gateway.db}（可 ARKAGENT_HOME 覆盖）
    masked_input.py    # TTY 掩码输入（读 API Key 时以 • 回显）
  mock_mcp/            # 示例 MCP Server（客户A 案例用，FastMCP streamable-http）
    __init__.py        # 包声明
    __main__.py        # python -m mock_mcp 启动（读 MCP_STATIC_BEARER/HOST/PORT）
    server.py          # 工具定义 + static_bearer 鉴权中间件 + 掩码日志
    data.py            # 按 open_id 分桶的演示数据与权限位
  node-helper/         # 唯一保留的 Node 小岛
    register_app.mjs   # registerApp 扫码建飞书应用，把凭证写回 JSON
  tests/               # pytest（asyncio_mode=auto）
  cases/               # 本场景的案例（customer-a-4checkpoints / group-bot）
```

### 模块职责速查

| 模块 | 职责 | 关键点 |
| --- | --- | --- |
| [cli.py](arkagent/cli.py) | 命令入口 `init/doctor/run/update-agent` | 飞书 WS 在主线程阻塞，asyncio 事件循环跑后台线程，回调 `call_soon_threadsafe` 投递协程，满足飞书 3 秒处理约束 |
| [init.py](arkagent/init.py) | 首次初始化全部方舟资源 + 飞书应用 | 建 Agent（挂 `mcp_servers`+`mcp_toolset`+`agent_toolset`）、Vault、`static_bearer` 凭据（创建时方舟会握手探测 MCP）、Environment，写 `config.env` |
| [node_helper.py](arkagent/node_helper.py) | 封装 Node 小岛 | `registerApp` 无 Python 等价物；子进程运行，stderr 透传二维码，从临时 JSON 读回 `{appId,appSecret,userOpenId}` |
| [gateway.py](arkagent/gateway.py) | 网关编排核心 | 串行队列；指令分发（`/new` `/role` `/whoami` `/remember`）；创建 Session 时组装 `env_overrides`（OpenID 透传）+ `vault_ids`（凭据）+ `resources`（挂 Memory Store） |
| [ark.py](arkagent/ark.py) | 方舟 MA 异步客户端 | `create_session`(resources) / `send_message`(system.message) / `create_static_bearer_credential` / `create_memory_store`+`create_memory` / `update_agent`；SSE 流式 + 超时回查 |
| [feishu.py](arkagent/feishu.py) | 飞书接入层 | 入站基于官方 `lark-channel-sdk`（`FeishuChannel`：WS 长连接/断线重连/事件归一化/去重打包），出站沿用 SDK 自带的同步 OpenAPI `Client`；单聊全量、群聊仅 `@Bot` |
| [role.py](arkagent/role.py) | 岗位信息（软层） | 24h TTL 缓存；仅在「本 Session 未注入过」时挂一次 `system.message`；`on_role_change` 清标记强制重注入；HR Provider 可替换（默认 mock） |
| [memory.py](arkagent/memory.py) | 跨 Session 记忆 | 每 open_id 一个专属 Store，仅创建 Session 时挂载；岗位调动不新建 Store 只开新 Session；写入靠应用侧 `/remember` 调 API（Agent 对 `/mnt/memory` 只读） |
| [store.py](arkagent/store.py) | SQLite 持久化 | 会话映射、事件去重、`role_cache`、`memory_stores`/`team_stores` 表 |
| [config.py](arkagent/config.py) | 配置加载校验 | 解析 `KEY="json-quoted-value"`；校验必填项；产出不可变 `GatewayConfig` |
| [paths.py](arkagent/paths.py) | 状态目录解析 | `~/.arkagent/{config.env,gateway.db}`，`ARKAGENT_HOME` 可覆盖 |
| [masked_input.py](arkagent/masked_input.py) | 安全输入 | TTY raw 模式逐字符读、`•` 回显、支持退格/Ctrl-C，仅真实终端可用 |
| [mock_mcp/server.py](mock_mcp/server.py) | 示例 MCP 服务 | FastMCP streamable-http；工具函数 + `StaticBearerMiddleware`（传输层 Bearer 校验）+ 掩码日志；关 DNS-rebinding 保护 |
| [mock_mcp/data.py](mock_mcp/data.py) | 示例数据与权限 | 按 open_id 分桶的账号（name/role/store/permissions/leads/kpi）、团队漏斗、车型目录及辅助查询函数 |
| [node-helper/register_app.mjs](node-helper/register_app.mjs) | 扫码建飞书应用 | 唯一保留的 Node 脚本，把应用凭证写回 JSON 供 Python 读取 |

> 各模块如何协作解决具体客户问题（数据怎么一跳跳流动），见客户A 案例的 [数据流转说明](cases/customer-a-4checkpoints/MA迁移Demo-四卡点数据流转说明.md)。

## 开发

```bash
pytest -q            # 全量单测（testpaths 已指向 scenarios/feishu-bot/tests）
python -m mock_mcp   # 本地起示例 mock MCP（不带 token 则不校验，仅本地调试）
```

## 参考资料

- MA 官方文档合集：[../../common/docs/火山方舟_ManagedAgents_docs.md](../../common/docs/火山方舟_ManagedAgents_docs.md)（维护脚本见 [../../common/skills/volc-docs-sync/](../../common/skills/volc-docs-sync/)）
- [火山方舟：Managed Agents API](https://docs.volcengine.com/docs/82379/2555910?lang=zh)
- [飞书：一键创建飞书智能体应用](https://open.feishu.cn/document/mcp_open_tools/integrating-agents-with-feishu/overview)
- [飞书：使用长连接接收事件](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)
