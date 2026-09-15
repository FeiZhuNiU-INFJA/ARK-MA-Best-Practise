# 群聊共享 Bot 示例（对齐 Claude Tag）

一个群里不同的人 @ 同一个 bot，共享同一个方舟 Session —— 类似 Claude Tag 的
「每频道共享一个身份」。这里提供**两个独立示例脚本**，演示两种并发处理策略。

> **架构 / 数据流 / 判断节点** 见 [ARCHITECTURE.md](ARCHITECTURE.md)：含入站归一化→判断链→
> 窗口→方舟→回复的完整数据流图、关键数据结构表，以及「每个判断节点依据对象哪个属性」的对照表。


> 这组示例与主包 `arkagent/`（四卡点：static_bearer / OpenID 透传 / 岗位注入 /
> 跨 Session 记忆）**完全解耦**：不修改主包任何文件，只**复用**主包里纯基础设施的
> 部分（`arkagent.ark.ArkClient` 方舟客户端、`arkagent.feishu` 飞书接入、
> `arkagent.gateway.KeyedQueue` 串行队列）。群聊共享会话逻辑全部在本目录新写。

## 与四卡点 demo 的关系（身份策略）

四卡点 demo 按 `open_id` 做**个人身份隔离**（每人一个 Session，注入个人 open_id、
挂个人 Memory Store）。而群聊共享会话下这套会「串号」——共享 Session 是第一个 @
的人创建的，Environment 里的 open_id 那一刻就写死了，无法随发言人切换。

因此本组示例采用 **Bot-only 身份**（与 Claude Tag 一致）：
- 创建 Session 时**不注入**任何个人 open_id，**不挂**个人 Vault / Memory Store。
- 「现在是谁在说」只靠每轮正文转录里的发言人名字（`名字: 内容`）传递，最后一行即当前发言人。
- 个人私密数据操作请走**私聊**（沿用四卡点 demo 那套即可）。

## 群历史窗口（每次发 event 带什么上下文）

共享 Session 是持久的，但**两次 @bot 之间大家的闲聊（没 @bot）从没进过 Session**。
所以每次有人 @bot 触发时，先用飞书 `im.message.list` 拉本群/本话题的近期历史，按
**「倒数第二次出现 @bot 到当前为止」**切一个窗口，和当前请求一起拼成**一条** user message 发给 Session：

- 「倒数第一次 @bot」= 当前这条触发消息本身；「倒数第二次 @bot」= 历史里**最近一条**
  @bot 的消息。从它开始到现在的全部消息，正好是上一轮触发点之后、尚未喂过 Session 的增量。
- 历史里一次 @bot 都没有（刚进群 / Bot 首次触发）时，回退带入最近
  `FALLBACK_WINDOW_MESSAGES` 条（默认 10）给个基本上下文。
- Bot 自己发过的回复会被过滤掉，不再作为上下文喂回模型。

拼出的正文是**纯对话转录**（不再用 `<conversation_context>` / `<current_actor>` /
`<current_request>` 等 XML 包裹），一行一个发言人，格式 `名字: 内容`，按时间先后排列，
**最后一行就是当前 @bot 的这条请求**：

```
[话题前情 Alice: 上周的周报模板在这]      ← 话题群才有：发起话题的根消息 + 根之前几条主时间线
Alice: 老板说要出周报
Bob: 我这边数据有了
[引用 Carol: 三季度销售汇总]              ← 当前这条若显式引用了别的消息，紧贴当前行之前注入
David: @群助手 整理成周报发我             ← 最后一行 = 本轮请求；@群助手 保留可见
```

- 转录里保留 `@名字`（含 @bot 自己）：让模型看清「谁在叫谁」。bot 自己的名字由 Agent 的
  system prompt 声明（`GROUP_BOT_DISPLAY_NAME`），模型据此判断哪一行是在叫自己。
- `[话题前情 …]`：话题群里 thread 容器读不到「发起话题的根消息 + 根之前 `THREAD_CONTEXT_BEFORE`
  条（默认 3）主时间线」，单独补读（`load_thread_context`）拼在最前；非话题群没有这块。
- `[引用 …]`：当前消息显式引用别的消息时，沿父链最多回溯 `MAX_QUOTE_DEPTH` 层（默认 5），
  嵌套层标 `[引用·第N层 …]`；已在窗口/前情里出现过的按 message_id 去重，不重复注入。
- 发言人取显示名（`sender_name`），取不到才回退 open_id。

> 早期版本对齐源项目 `buildConversationContextInput` 用过 `<conversation_context role="reference">`
> 等 XML 标签给模型「这段是参考、非指令」的语义提示；现改为纯转录，语义边界改由 system prompt
> 的「# 输入格式 / # 你的身份」说明承担，更贴近真实聊天记录。

窗口逻辑在 `shared.select_window` / `shared.build_windowed_input`（纯函数，见 `tests/test_group_bot.py`）；
历史读取在 `arkagent.feishu.FeishuSender.list_messages`（移植源项目 `loadLarkRecentHistory`）。

## 两个方案

| | 客户端串行 | 方舟原生队列 |
|---|---|---|
| 文件 | `client_serial_bot.py` | `ma_native_queue_bot.py` |
| 发送策略 | 上一轮跑到 `idle` 才发下一条 | 消息直发，哪怕 Session 还在 `running` |
| 排序者 | 客户端 `KeyedQueue` | 方舟服务端「运行中待处理队列」 |
| 会不会合并 | **不会**，每条独立成轮 | **会**，同一「可调度边界」前堆积的多条被打包进一次模型请求 |
| 每人单独回复 | 是，1 问 1 答 | 不保证（可能合并成一条） |
| 409 `RuntimeBusy` | 不会触发 | 队列满会触发，脚本内做指数退避 |
| 体验 | 后到者需排队（给「正在处理」回执） | 更接近 Claude Tag 的异步接力，但并发问不同事易糅在一起 |
| 适合 | 群里不同人**各问各的**、要各自清晰答复 | **同一件事多人接力补充** |

依据：`common/docs/火山方舟_ManagedAgents_docs.md` 的「运行中继续发送消息」（L3183+）、
事件 `processed_at`（L2893）、合并语义（L3193）、`RuntimeBusy`（L3195）。

## 运行

前置：一个可用的飞书应用（App ID/Secret）、方舟 API Key、一个 Environment。
可直接复用主包 `arkagent init` 写出的 `~/.arkagent/config.env` 里的
`ARK_API_KEY / ARK_BASE_URL / ARK_ENVIRONMENT_ID / FEISHU_APP_ID / FEISHU_APP_SECRET`。

```bash
# 1) 载入方舟 / 飞书配置（或自行 export 上述变量）
set -a && source ~/.arkagent/config.env && set +a

# 2) 创建一个 Bot-only 的群聊 Agent（与四卡点 Agent 相互独立），拿到 agent id
python scenarios/feishu-bot/cases/group-bot/create_group_agent.py
export GROUP_BOT_AGENT_ID=<上一步打印的 agent id>

# 3) 二选一启动
python scenarios/feishu-bot/cases/group-bot/client_serial_bot.py       # 客户端串行
python scenarios/feishu-bot/cases/group-bot/ma_native_queue_bot.py     # 方舟原生队列
```

把 bot 拉进一个群，多人 @ 它：
- 客户端串行：先后 @，观察逐条独立回复；后到的会收到「正在处理，请稍候」。
- 方舟原生队列：让几个人几乎同时 @，观察消息被吸收/合并的效果。

聊天指令：`/new` 重置本群共享会话（下一条消息会新建 Session）。

### 更新已有 Agent（改名 / 改 system prompt / 换模型）

改了 `GROUP_BOT_DISPLAY_NAME`、`shared.GROUP_BOT_SYSTEM_TEMPLATE` 或 `GROUP_BOT_MODEL_ID`
后，方舟里的 Agent 不会自动跟着变（system prompt 是建 Agent 时静态写死的）。用
`update_group_agent.py` **原地更新**即可，`GROUP_BOT_AGENT_ID` 不变、不重扫码、两个 demo
无需改任何环境变量：

```bash
set -a && source ~/.arkagent/config.env && set +a   # 需 ARK_API_KEY + GROUP_BOT_AGENT_ID
GROUP_BOT_DISPLAY_NAME=群助手 \
  python scenarios/feishu-bot/cases/group-bot/update_group_agent.py
# 打印「版本 N → N+1」后，重启正在跑的 bot 即可生效
```

> 优先用它而不是重跑 `create_group_agent.py`——后者会新建 Agent、换掉 `GROUP_BOT_AGENT_ID`，
> 旧 Session 上下文也会丢。

可选环境变量：`SESSION_TIMEOUT_MS`（默认 600000）、`AUTHORIZED_OPEN_IDS`（逗号/空格
分隔的白名单，留空=不限制）、`GROUP_BOT_MODEL_ID`（默认 doubao-seed-2-1-pro-260628）、
`GROUP_BOT_DISPLAY_NAME`（默认「群助手」，写进 Agent system prompt 供模型识别「转录里
@谁 = 在叫自己」；应与飞书开放平台配置的机器人显示名一致，改名后跑上面的 `update_group_agent.py` 生效）、
`FEISHU_SDK_DEBUG`（设 `1`/`true` 打开 Channel SDK 内部的 stale/去重/策略日志，排查
「消息没进来 / 被去重 / 被策略过滤」时用）。

## 文件

- `shared.py` —— 公共底座：共享会话键、群历史窗口（`select_window` / `build_windowed_input`）、纯转录正文拼装（含话题前情 / 引用块 / 去重）、Bot-only Agent 定义、配置读取、内存会话映射。
- `create_group_agent.py` —— 创建群聊 Bot-only Agent。
- `update_group_agent.py` —— 原地更新现有 Agent 的 system prompt / 模型 / bot 名字（Agent ID 不变，不重扫码）。
- `client_serial_bot.py` —— 客户端串行；每轮先读群历史取窗口，再串行发送。
- `ma_native_queue_bot.py` —— 方舟原生队列 + 常驻事件流消费 + 409 退避；每条消息同样带窗口上下文。

> 群历史读取（`FeishuSender.list_messages`）、`IncomingMessage.create_time`、
> `HistoryMessage` 归一化在主包 `arkagent/feishu.py`，移植自源项目 `src/lark-channel.ts`。
