# 火山方舟 Managed Agents · 最佳实践仓库

> 一组围绕**火山方舟 Managed Agents（MA）**的可运行最佳实践：按**场景**组织，通用件下沉到 `common/`。

每个场景是一个独立目录（含自己的 README、代码、案例）；跨场景共享的东西（MA 官方文档合集及其维护脚本）放在 `common/`。想上手某个场景，直接进它的目录看 README。

## 顶层结构

```text
ark-agent-feishu-bot/
├── README.md                         # 本文件：最佳实践索引
├── index.html                        # 交互式场景与架构文档入口
├── pyproject.toml                    # 打包入口（指向 scenarios/feishu-bot）
├── environment.yml                   # MA × 飞书 Bot 的 conda 环境
├── debug-oauth-vault-token-limit.md  # OAuth Vault Token 上限排查记录
├── common/                           # 跨场景共享的文档与维护工具
│   ├── docs/
│   │   ├── managed-agents-architecture-atlas.html  # MA 交互式架构全景
│   │   ├── 火山方舟_ManagedAgents_docs.md          # MA 官方文档合集
│   │   ├── lark_channel_sdk_docs.md                # 飞书 Channel SDK 文档合集
│   │   └── feishu_bot_permissions.md               # 飞书 Bot 权限说明
│   └── skills/                       # 上述文档的同步与校验 skill
│       ├── volc-docs-sync/
│       ├── lark-channel-docs-sync/
│       └── feishu-bot-perms-sync/
└── scenarios/
    ├── feishu-bot/                   # 场景1：MA × 飞书 Bot
    │   ├── arkagent/                 # Gateway、CLI 与 MA 客户端
    │   ├── mock_mcp/                 # 客户 A 示例 MCP
    │   ├── node-helper/              # 扫码创建飞书应用
    │   ├── cases/                    # Topic 6、数字员工、客户 A 四卡点
    │   └── tests/
    └── ma-replica/                   # 场景2：客户 Agent 轨迹在 MA 重放对比
```

## 浏览入口

访问 **[GitHub Pages 在线站点](https://feizhuniu-infja.github.io/ARK-MA-Best-Practise/)**
可浏览场景索引，并进入
[Managed Agents 交互式架构全景](common/docs/managed-agents-architecture-atlas.html)、
Topic 6 架构图和完整流水线。本地也可以直接打开 [index.html](index.html)。

## 场景

| 场景 | 一句话 | 目录 |
| --- | --- | --- |
| **MA × 飞书 Bot** | 把飞书对话机器人接到 MA；包含 Topic 6 社媒热点周刊、数字员工阿 J、客户 A 四卡点三个案例。 | [scenarios/feishu-bot/](scenarios/feishu-bot/) |
| **客户 Agent 轨迹重放** | 将客户自研 Agent 的运行轨迹在 MA 上模拟重放，对比耗时、token、cache 与结果等效性。 | [scenarios/ma-replica/](scenarios/ma-replica/) |

## 通用件（common/）

- **MA 交互式架构全景**：[common/docs/managed-agents-architecture-atlas.html](common/docs/managed-agents-architecture-atlas.html) —— 从组件关系、运行逻辑、权限门禁和生命周期理解 MA。
- **MA 官方文档合集**：[common/docs/火山方舟_ManagedAgents_docs.md](common/docs/火山方舟_ManagedAgents_docs.md) —— 从火山方舟文档中心抓取拼接的 Managed Agents 文档。
- **飞书 Channel SDK 文档合集**：[common/docs/lark_channel_sdk_docs.md](common/docs/lark_channel_sdk_docs.md) —— 飞书官方 Python Channel SDK 文档快照。
- **飞书 Bot 权限说明**：[common/docs/feishu_bot_permissions.md](common/docs/feishu_bot_permissions.md) —— 汇总本项目实际使用的 OpenAPI scope 与常见错误码。
- **文档维护 skill**：[common/skills/](common/skills/) —— 分别负责同步 MA 文档、同步 Channel SDK 文档及校验 Bot 权限说明。常用命令：

  ```bash
  python3 common/skills/volc-docs-sync/update_docs.py
  python3 common/skills/lark-channel-docs-sync/update_docs.py
  python3 common/skills/feishu-bot-perms-sync/check_doc.py
  ```

## 上手

每个场景自带完整的环境搭建与运行说明——进对应目录看 README。跨场景**没有统一的一键安装**：两个场景的依赖与环境相互独立。

- **场景1（MA × 飞书 Bot）**：conda 环境、`pip install -e .`（打包配置在仓库根，`package-dir` 把包目录指到 `scenarios/feishu-bot/`，import 名仍是 `arkagent` / `mock_mcp`，故安装/测试命令**从仓库根执行**）、扫码建应用及按 case 运行 Gateway —— 见 [scenarios/feishu-bot/README.md](scenarios/feishu-bot/README.md)。
- **场景2（客户 Agent 轨迹重放）**：最小环境（仅依赖 `httpx`，不碰场景1 的包）、轨迹抽取 / mock / MA 实跑 / 对比报告 —— 见 [scenarios/ma-replica/README.md](scenarios/ma-replica/README.md)。

> 外部一手文档（火山方舟 / 飞书官网链接）随场景收录在各自 README 的「参考资料」；跨场景权威出处是上面 `common/` 里的 MA 文档合集。
