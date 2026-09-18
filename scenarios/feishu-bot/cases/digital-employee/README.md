# 数字员工阿J（群聊与单聊）

这里只有一个运行入口 `digital_employee.py`。数字员工阿J既可在群聊中响应 `@bot` 并创建
飞书话题，也可在单聊中提供个人协作服务。群聊中**一个话题对应一个方舟 Session**；后续仍只有
`@bot` 才触发回复，但中间普通消息会作为上下文带入，不同话题严格隔离。启动参数可选择客户端
串行或方舟原生队列。

> **兼容性说明**：运行脚本与文档已统一使用 `digital_employee` 命名；`GROUP_BOT_*` 配置键、
> 既有 Environment/Vault 资源名和会话数据库名保留不变，以复用现有线上 Agent、凭据和会话状态。

> **架构 / 数据流 / 判断节点** 见 [ARCHITECTURE.md](ARCHITECTURE.md)：含入站归一化→判断链→
> 窗口→方舟→回复的完整数据流图、关键数据结构表，以及「每个判断节点依据对象哪个属性」的对照表。


> 这组示例与主包 `arkagent/`（四卡点：static_bearer / OpenID 透传 / 岗位注入 /
> 跨 Session 记忆）**完全解耦**：不修改主包任何文件，只**复用**主包里纯基础设施的
> 部分（`arkagent.ark.ArkClient` 方舟客户端、`arkagent.feishu` 飞书接入、
> `arkagent.gateway.KeyedQueue` 串行队列）。群聊共享会话逻辑全部在本目录新写。

## 推荐方案：一个话题一个 Session

`digital_employee.py` 的规则：

- 主时间线只有明确 `@bot` 的消息会被处理；每条这样的消息都成为一个新话题根。
- Bot 使用飞书 `reply_in_thread=true` 回复首条消息，因此回复和后续讨论都留在该话题。
- 话题内普通消息不触发 Bot；下一次 `@bot` 时统一作为本轮上下文。
- Session 键为 `tenant_key + chat_id + thread_id`；不同话题永不复用 Session。创建话题的
  首条 `@bot` 到达时尚无 `thread_id`，先以该消息自身 ID 建 Session；Bot 首次回复后从飞书
  响应取得新生成的 `thread_id`，再绑定到同一个 Session。
- 每轮会精确读取 `root_id` 对应的话题根消息，并读取当前 thread，把「上一次 `@bot` 之后
  到本次 `@bot`」的消息、附件和显式引用拼进 `content`。
- 不扫描主群时间线或其他话题；若话题由“回复一个文件”创建，该根文件会在首轮挂载给 Agent。
- `@bot /new` 只替换当前话题的 Session，不影响同群其他话题。

这意味着主群里先发文件、再另发一条无引用关系的 `@bot` 消息时，Bot 不会猜测两者有关。
需要把材料带入新话题时，可回复该文件创建话题，也可在首条 `@bot` 消息中直接附带文件或
显式引用目标消息。

## 与四卡点 demo 的关系（身份策略）

本数字员工采用 **群聊 Bot-only、单聊按需用户只读授权**，并把长期记忆按作用域隔离：
- 单聊按 `tenant_key + open_id` 懒创建并挂载个人 Memory Store。
- 群聊按 `tenant_key + chat_id` 懒创建并挂载群 Memory Store；同群所有话题挂同一个 Store，
  不创建话题级 Store。
- 群聊 Session **不注入**任何个人 open_id，**不挂**个人 Vault / Memory Store；因此群聊和
  群话题无法读取个人记忆。
- 「现在是谁在说」只靠每轮正文转录里的发言人名字（`名字: 内容`）传递，最后一行即当前发言人。
- 单聊 Session 挂当前发送者的独立用户 Vault，并注入可信 `FEISHU_USER_OPEN_ID`。默认仍用 Bot；
  只有读取本人的身份、日历、忙闲或搜索本人可见文档时才用用户身份，所有写操作仍用 Bot。
- 首次执行用户读取命令时，Bot 会发送飞书 Device OAuth 授权卡片；校验授权账号与消息发送者一致后，
  原地更新用户 Credential，并自动续跑原请求。用户 token 按业务域单独签发，避免多个业务域的
  scope 使 token 超过 Vault 限制；卡片失效时发送“重新授权”会生成一张带新链接的卡片。

Memory Store 在 Session 中只读；增删改查由 Agent 的 `memory_list` / `memory_get` /
`memory_upsert` / `memory_forget` Custom Tool 触发，Gateway 根据 `session_id` 的持久化绑定
决定目标 Store，Agent 不能传 `store_id`、`open_id` 或 `chat_id`。修改 Agent 配置后需运行
`update_digital_employee_agent.py`，已有飞书会话再发送 `/new` 才会创建挂载 Memory Store 的新 Session。

## 话题增量窗口

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
[话题前情 Alice: 上周的周报模板在这]      ← 每轮精确补入发起话题的根消息
Alice: 老板说要出周报
Bob: 我这边数据有了
[引用 Carol: 三季度销售汇总]              ← 当前这条若显式引用了别的消息，紧贴当前行之前注入
David: @群助手 整理成周报发我             ← 最后一行 = 本轮请求；@群助手 保留可见
```

- 转录里保留 `@名字`（含 @bot 自己）：让模型看清「谁在叫谁」。bot 自己的名字由 Agent 的
  system prompt 声明（`GROUP_BOT_DISPLAY_NAME`），模型据此判断哪一行是在叫自己。
- `[话题前情 …]`：每轮精确读取 `root_id` 对应的根消息并拼在最前，根消息携带的
  文件也会一并挂载；不会扫描根消息之前的主时间线。
- `[引用 …]`：当前消息显式引用别的消息时，沿父链最多回溯 `MAX_QUOTE_DEPTH` 层（默认 5），
  嵌套层标 `[引用·第N层 …]`；已在窗口/前情里出现过的按 message_id 去重，不重复注入。
- 发言人取显示名（`sender_name`），取不到才回退 open_id。

> 早期版本对齐源项目 `buildConversationContextInput` 用过 `<conversation_context role="reference">`
> 等 XML 标签给模型「这段是参考、非指令」的语义提示；现改为纯转录，语义边界改由 system prompt
> 的「# 输入格式 / # 你的身份」说明承担，更贴近真实聊天记录。

窗口逻辑在 `shared.select_window` / `shared.build_windowed_input`（纯函数，见 `tests/test_group_bot.py`）；
历史读取在 `arkagent.feishu.FeishuSender.list_messages`（移植源项目 `loadLarkRecentHistory`）。

## 多模态：图片 / 文件（挂载 Session 文件系统）

群里有人 @bot 时**发图片或文件**（含只发一张图不带文字），bot 会把附件挂进方舟 Session
的沙箱文件系统，让 Agent 用文件工具去读——对齐源项目 `src/gateway.ts` 的「上传并挂载」方案，
而非把二进制塞进 user message 的多模态块。链路：

1. **抽取**：入站消息里 SDK 已把图片/文件归一化成 `ResourceDescriptor`，
   `feishu._extract_resources` 只挑 `image` / `file` 两类（sticker/audio/video 不挂），
   映射成 `ResourceRef`（挂到 `IncomingMessage.resources`）。图片消息本身没正文，`text` 清空。
2. **下载**：`FeishuSender.download_resource(message_id, file_key, type)` 走
   `GET /im/v1/messages/{id}/resources/{key}` 取原始字节（同步调用，丢线程池）。
3. **上传**（`shared.prepare_attachments`）：所有文件类型，包括 `.md`、`.markdown`、`.txt`、
   PDF 和图片，统一上传方舟 **Files API**（`purpose=agent`）拿 `file_id`；不把文件原文
   直接展开进消息上下文。
4. **挂载**：`ArkClient.add_session_file` 把 `file_id` 挂到本 Session 的
   `/mnt/session/uploads/{短哈希}/{安全文件名}`；正文里列出这些绝对路径，提示 Agent 去读。
   Session 失效重建（404）时附件会重新挂到新 Session（`file_id` 与 Session 无关，仍有效）。
5. **降级**：单个附件下载/上传失败、超单文件 40 MB、单轮总量超 40 MB 等，
   都降级成一句可读的 `notice`（拼进正文「另外：…」），不拖垮本轮其余附件与回复。

拼进正文的附件块（追加在当前请求行**之后**）：

```
【最新对话】
David: @群助手 帮我看看这份报告          ← 当前请求行（纯图片消息则给一句默认「请读取并总结…」）
【文件挂载】
报告.pdf： /mnt/session/uploads/9f3a…/报告.pdf
notes.md： /mnt/session/uploads/81ab…/notes.md
另外：                                     ← 有降级时如实说明
- 附件「big.bin」未能处理：单个文件超过 40 MB，无法上传
```

开关 `GROUP_BOT_MULTIMODAL`（默认开启）：设 `0`/`false`/`no`/`off` 关闭后，带附件的消息按
纯文本处理，正文里只留一句「[附件已忽略：多模态未开启]」，不下载不上传。挂载编排（下载→
上传→挂载及降级判定）在 `shared.prepare_attachments` / `_attachment_blocks`（纯函数，
两个 IO 能力由 bot 注入，见 `tests/test_group_bot.py`）；方舟侧接口在
`arkagent.ark.ArkClient.upload_file` / `add_session_file`。

### 附件去重：同一文件不重复下载 / 上传 / 挂载

同一份文件常被多轮反复引用（多人接力、话题里反复提到同一份报告），甚至跨群出现。以飞书
`file_key`（资源稳定身份）为键做**两层去重**，都挂在 `SqliteSessionMap` 上（`InMemorySessionMap`
同接口）：

- **文件缓存**（`attachments` 表，`file_key → file_id`）：命中就**跳过下载 + 上传**，直接复用旧
  `file_id`。`file_id` 与 Session 无关、可跨会话复用，所以这层**跨群 / 话题 / 进程重启**都共享——
  即「跨 session 也不重复下载挂载」。
- **挂载记录**（`attachment_mounts` 表，`(session_id, file_key)`）：同一 Session 里同一资源**只挂
  一次**，之后每轮引用同一路径即可，不再 `add_session_file`。

`_mount_path` 由 `file_key` 哈希决定（不掺 message_id），保证同一资源恒定落到同一挂载路径。
效果：同 Session 内第二次引用 → 0 下载 / 0 上传 / 0 挂载；跨 Session 第二次引用 → 0 下载 / 0 上传，
各 Session 各挂一次（复用同一 `file_id`）。详见 [ARCHITECTURE.md](ARCHITECTURE.md) §8.1，测试见
`tests/test_digital_employee.py`。

### 历史消息里的附件：文件单独发、之后另一条消息才 @bot

飞书里文件/图片常是**单独一条消息**发出来的，用户之后才在**另一条**消息里 @bot「说说这个
PDF」。此时触发消息本身**没有**附件、只有正文——若只看触发消息的 `resources`，那份文件就被
漏掉：Agent 只看到历史转录里的 `[文件：xxx.pdf]` 占位却读不到内容。

修法是把「本轮上下文里出现过的」附件都收齐、范围与注入正文的历史范围一致：

1. 归一化历史时，`_extract_history_resources` 从每条历史消息抽出图片/文件附件，挂到
  `HistoryMessage.resources`，并给每个 `ResourceRef` 记上**它自己所属消息**的 `message_id`。
2. 两种执行模式都先读取当前话题增量和引用链，再用
   `collect_round_resources` 把「触发消息 + `select_window` 窗口历史 + 话题前情」里的附件按
   `file_key` 去重收齐（触发消息优先）。
3. 下载时按 `ref.message_id` 定位所属消息（`file_key` 只在其所属消息里有效），历史附件走它
   自己的 id，触发消息附件兜底用当前消息 id。

这样「进正文的转录范围」与「挂进 Session 的附件范围」严格一致；撤回消息只留占位文本、不带
附件。详见 [ARCHITECTURE.md](ARCHITECTURE.md) §8.2，测试见 `tests/test_group_bot.py`
（`collect_round_resources`）与 `tests/test_digital_employee.py`。

## 回复渲染：Markdown → 飞书富文本（post）

Agent 的回复本身是 Markdown（`## 标题`、`**加粗**`、`- 列表`、代码块……）。飞书**纯文本消息
不渲染 Markdown**，直发会把 `**`、`##` 这些记号原样显示。因此 `reply` / `send_to_chat` 默认把
正文经 SDK 的 `markdown_to_post_ast` 转成飞书 **post 富文本**（`msg_type=post`）再发，由飞书端
渲染标题/加粗/列表/代码块等。

- 复用 lark-channel-sdk 自带的出站 Markdown 能力（`lark_channel.channel.outbound.markdown`），
  转换是纯字符串处理、无 IO，见 `arkagent.feishu._text_to_post_content`。
- **失败自动降级**：post 转换或发送一旦异常，立刻用同一段文字按纯文本再发一次——宁可不渲染
  也要把消息发出去，不会因为渲染问题吞掉回复。回执 / 报错这类短句同样走 post（纯文本在 post 里
  渲染一致），实现简单统一。
- reply 仍保留「在原消息下引用回复」的语义，不改交互形态。
- 开关 `GROUP_BOT_MARKDOWN`（默认开启）：设 `0`/`false`/`no`/`off` 关闭后退回老的纯文本直发
  （排障、或对端确实不需要富文本时用）。测试见 `tests/test_feishu.py`。

### 回复里 @人：`@名字` → 可点击提及

Agent 常在回复里点名群成员（「@张三 请跟进」）。若直接发文字，`@张三` 只是几个字、点不动，
也不会真的通知到人。因此群聊回复前会先取**本群成员名册**（名字 → open_id），把正文里的
`@名字` 重写成飞书可点击的 `<at user_id="ou_...">名字</at>` 提及。

- **名册来源**：`FeishuSender.chat_roster(chat_id)` 走 `im.v1.chats/:chat_id/members` 拉群成员，
  归一成 `名字 → open_id` 映射，自带 60s TTL 缓存（进退群不频繁，避免每条回复都拉一次）。
  依赖权限 `im:chat.members:read`（或 `im:chat:readonly`），且要**发布版本**后生效；未开通时该接口返回
  400（错误码 99991672），`chat_roster` 会捕获并降级为不 @，不影响其余回复。
- **同名消歧**：一个显示名对应多个不同 open_id（群里真有两个「张三」）时，该名字整体从名册剔除
  ——出站找不到唯一目标就原样保留 `@张三`，宁可不 @ 也不 @ 错人。见 `_build_roster`。
- **重写 + 混合渲染**：用 SDK 的 `resolve_mentions_in_text` 把命中名册的 `@名字` 换成 `<at>`
  （不在名册/歧义的名字原样留字面），再逐块渲染——含 `<at>` 的段走 structured（飞书才能把 `<at>`
  渲成可点击提及），其余段仍走 native md（保留标题/列表/代码块）。见 `_text_to_post_content`。
- **绝不拖垮回复**：名册拉取失败退回空名册（不 @，正文照发）；私聊没有 @ 别人的语义，不拉名册。
- **发问人显示名**：转录里「当前请求行」也优先用发言人显示名（`IncomingMessage.user_name`），
  取不到才回退 open_id，和历史行同一口径。测试见 `tests/test_feishu.py` /
  `tests/test_digital_employee.py`。

## Agent 的飞书操作能力（lark-cli，群聊/单聊双身份）

除了对话，Agent 还能用运行环境里预装的 `lark-cli` 访问飞书资源。群聊始终使用本应用 Bot
身份；单聊默认使用 Bot，仅在读取当前用户自己的身份、日历或忙闲时按需使用用户身份。
凭据分三处安放，各司其职：

- **Environment**：`setup_script` 在 Session 首次拉起沙箱时安装原版 lark-cli
  （SHA256 校验 + npmmirror 加速），`packages.pip` 预装固定版本 `pypdf`；
  `env.LARKSUITE_CLI_APP_ID` 明文写入飞书 App Id（非敏感）。重复置备会原地同步配置，
  保持 Environment ID 不变。
- **Bot 主机**：用 App ID/Secret 换取短期 tenant access token；App Secret 不进入方舟 Vault
  或 Agent 沙箱。
- **Vault**：只保存 `LARKSUITE_CLI_TENANT_ACCESS_TOKEN`。Bot 在每轮发送前检查有效期，
  临近过期时原地刷新凭据，保持 Vault ID 与长寿命 Session 不变。
- **用户 Vault**：每个单聊用户独立保存 `LARKSUITE_CLI_USER_ACCESS_TOKEN`。refresh token
  仅保存在本地 SQLite；访问令牌临近过期时由 Bot 主机刷新并原地更新 Credential。
- **每轮 create_session**：群聊只挂 Bot Vault；单聊同时挂 Bot Vault 与发送者的用户 Vault。
  同时注入
  `$FEISHU_CHAT_ID` / `$FEISHU_THREAD_ID` /
  触发消息 id 等**当前位置**定位变量。

要点：
- **身份边界**：群 Session 永远只注入 Bot 上下文，禁止 `--as user`。单聊只有日历只读类
  请求可 `--as user`；创建、修改、删除等写操作始终 `--as bot`。授权账号的 `open_id`
  必须与当前消息发送者一致。
- **按需授权**：用户命令返回结构化 `token_missing` 后发送授权卡片，成功后自动续跑原任务；
  每条消息最多自动授权重试一次。
- **幂等**：`initialize_digital_employee.py` 会自动建（或复用）这套 Environment + Vault，把
  `GROUP_BOT_ENVIRONMENT_ID` / `GROUP_BOT_LARK_VAULT_ID` 写回 config.env，重复跑不会堆资源。
- **开关**：只有配了 `GROUP_BOT_LARK_VAULT_ID` 才启用 lark-cli（`lark_cli_enabled`）；没配则 Agent
  退回纯对话，不挂 Vault、不注入定位变量。
- **权限**：lark-cli 能做什么取决于飞书开放平台给应用勾了哪些权限——除消息类外，按业务域
  （docx / drive / calendar…）在开放平台补齐并**发布版本**后才生效。
- **用户权限**：当前实现需要
  `offline_access`、`auth:user.id:read`、`calendar:calendar:read`、
  `calendar:calendar.event:read`、`calendar:calendar.free_busy:read`。已有应用也必须在开放平台
  增加这些用户身份权限并发布新版本；只改代码不会让权限自动生效。

### 文档读取故障排查与旧凭据迁移

- Bot 读取 Wiki/云文档需要同时满足两层授权：开放平台已发布对应 API 权限
  （如 `wiki:node:read`、`docx:document:readonly`），且目标文档或知识空间已把应用 Bot
  加为可阅读协作者。只有其中一层时仍会失败。
- 如果协作者和 API 权限都正确，但返回 `app secret invalid`、`token_missing` 或无法获取
  tenant token，应检查 Vault 凭据方案，不要继续重复调整文档 ACL。
- 不要把 App Secret 作为 `environment_variable` 放进 Vault，再从沙箱内调用 token 接口。
  该变量在沙箱内是 opaque placeholder，只适合在出站请求中原样替换，不能参与 JSON body
  的 token 交换。正确链路是 Bot 主机换取 tenant token，再把
  `LARKSUITE_CLI_TENANT_ACCESS_TOKEN` 写入 Vault。
- 从旧 App Secret/wrapper 方案升级时，运行 `provision_digital_employee_lark_cli.py` 创建 token-v3
  Environment/Vault 并更新 `config.env`，然后重启 Bot。已经创建的 Session 仍绑定旧 Vault；
  可在对应话题发送 `/new`，或在停服后清理当前执行模式数据库的 `sessions` 映射，使下一条消息
  自动创建挂载新 Vault 的 Session。附件缓存无需清理。
- 验证时应使用与生产完全相同的 Agent、Environment、Vault 和 Bot 身份创建临时 Session，
  实际执行 `lark-cli docs +fetch <文档 URL> --as bot`。仅验证本机 CLI 或 token 接口成功，
  不能证明方舟 Session 内的凭据挂载正确。

详细的三处安放与数据流见 [ARCHITECTURE.md](ARCHITECTURE.md) §9。

## 两种执行模式

| | `serial`（默认） | `native-queue` |
|---|---|---|
| 入口 | `digital_employee.py --execution-mode serial` | `digital_employee.py --execution-mode native-queue` |
| Session 粒度 | **每个话题一个** | **每个话题一个** |
| 上下文输入 | 当前话题内上次 `@bot` 之后至今 | 相同 |
| 发送策略 | 每话题 `KeyedQueue` 串行，调用 `run` | `send_message` 直发，运行中由方舟吸收/合并 |
| 回复位置 | 始终在话题内 | 始终在话题内，回合结束取最后一条回复 |
| 适合 | 每次触发需要独立、稳定回复 | 同话题多人接力，允许服务端合并 |

依据：`common/docs/火山方舟_ManagedAgents_docs.md` 的「运行中继续发送消息」（L3183+）、
事件 `processed_at`（L2893）、合并语义（L3193）、`RuntimeBusy`（L3195）。

## 运行

前置：方舟 API Key（+ 可选 `ARK_BASE_URL`）。飞书应用、群聊 Agent、装了 lark-cli 的
Environment、存短期 tenant token 的 Vault 都由 `initialize_digital_employee.py` 一键置备。

### 一键初始化（推荐）

`initialize_digital_employee.py` 会：扫码建飞书应用 → 建双身份边界 Agent → 置备 lark-cli 能力
（装了 lark-cli 的 Environment + 存短期 tenant token 的 Vault，均幂等） → 把
`FEISHU_APP_ID/SECRET`、`GROUP_BOT_AGENT_ID`、`GROUP_BOT_ENVIRONMENT_ID`、
`GROUP_BOT_LARK_VAULT_ID` 都写回 `~/.arkagent/config.env`：

```bash
# 只需 config.env 里已有 ARK_API_KEY（跑过一次主包 arkagent init 即有），脚本自己读
python scenarios/feishu-bot/cases/digital-employee/initialize_digital_employee.py

# 按提示去飞书开放平台确认权限 + 事件订阅 + 发布版本后启动：
set -a && source ~/.arkagent/config.env && set +a
python scenarios/feishu-bot/cases/digital-employee/digital_employee.py --execution-mode serial
# 或：--execution-mode native-queue
```

### 手动分步（已有飞书应用时）

复用主包 `arkagent init` 写出的 `~/.arkagent/config.env` 里的
`ARK_API_KEY / ARK_BASE_URL / FEISHU_APP_ID / FEISHU_APP_SECRET`，只补群聊 Agent：

```bash
# 1) 载入方舟 / 飞书配置（或自行 export 上述变量）
set -a && source ~/.arkagent/config.env && set +a

# 2) 创建群聊 Bot-only、单聊按需用户只读 OAuth 的 Agent，拿到 agent id
python scenarios/feishu-bot/cases/digital-employee/create_digital_employee_agent.py
export GROUP_BOT_AGENT_ID=<上一步打印的 agent id>

# 3) 群聊 Bot 用自己的 Environment（装了 lark-cli 的那个）。缺 GROUP_BOT_ENVIRONMENT_ID
#    时回退共用 ARK_ENVIRONMENT_ID，但那个没装 lark-cli、也没挂 Vault，Agent 只能纯对话。
#    要启用 lark-cli：跑一次 initialize_digital_employee.py（或手动建 Environment + Vault）并 export：
#      export GROUP_BOT_ENVIRONMENT_ID=<装了 lark-cli 的 environment id>
#      export GROUP_BOT_LARK_VAULT_ID=<存短期 tenant token 的 vault id>

# 4) 启动唯一入口；默认 serial
python scenarios/feishu-bot/cases/digital-employee/digital_employee.py --execution-mode serial
# 需要服务端吸收/合并时改为：--execution-mode native-queue
```

把 bot 拉进一个群：主时间线 `@bot` 创建话题；话题内普通消息不回复，下次 `@bot`
时进入上下文。`serial` 会逐条独立回复；`native-queue` 允许同话题并发消息被方舟吸收/合并。

聊天指令：在当前话题发 `@bot /new`，只重置该话题。私聊直接发 `/new`。

### 更新已有 Agent（改名 / 改 system prompt / 换模型）

改了 `GROUP_BOT_DISPLAY_NAME`、`shared.GROUP_BOT_SYSTEM_TEMPLATE` 或 `GROUP_BOT_MODEL_ID`
后，方舟里的 Agent 不会自动跟着变（system prompt 是建 Agent 时静态写死的）。用
`update_digital_employee_agent.py` **原地更新**即可，`GROUP_BOT_AGENT_ID` 不变、不重扫码、运行入口
无需改任何环境变量：

```bash
set -a && source ~/.arkagent/config.env && set +a   # 需 ARK_API_KEY + GROUP_BOT_AGENT_ID
GROUP_BOT_DISPLAY_NAME=数字员工阿J \
  python scenarios/feishu-bot/cases/digital-employee/update_digital_employee_agent.py
# 打印「版本 N → N+1」后，重启正在跑的 bot 即可生效
```

> 优先用它而不是重跑 `create_digital_employee_agent.py`——后者会新建 Agent、换掉 `GROUP_BOT_AGENT_ID`，
> 旧 Session 上下文也会丢。

必需环境变量：`ARK_API_KEY`、`GROUP_BOT_AGENT_ID`、`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、
`GROUP_BOT_ENVIRONMENT_ID`（缺失回退 `ARK_ENVIRONMENT_ID`）。

可选环境变量：`ARK_BASE_URL`（默认北京）、`SESSION_TIMEOUT_MS`（默认 600000）、
`AUTHORIZED_USER_IDS`（逗号/空格分隔的员工白名单，优先使用；留空=不限制）、
`AUTHORIZED_OPEN_IDS`（旧白名单兼容项；迁移完成后移除）、
`GROUP_BOT_MODEL_ID`（默认 doubao-seed-evolving）、
`GROUP_BOT_DISPLAY_NAME`（默认「数字员工阿J」，写进 Agent system prompt 供模型识别「转录里
@谁 = 在叫自己」；应与飞书开放平台配置的机器人显示名一致，改名后跑上面的 `update_digital_employee_agent.py` 生效）、
`GROUP_BOT_MULTIMODAL`（默认开启；设 `0`/`false`/`no`/`off` 关闭图片/文件的下载挂载，带附件的消息按纯文本处理）、
`GROUP_BOT_MARKDOWN`（默认开启；把回复渲染成飞书 post 富文本，设 `0`/`false`/`no`/`off` 退回纯文本直发）、
`GROUP_BOT_LARK_VAULT_ID`（存短期 tenant token 的 Vault id；配了才启用 lark-cli，否则 Agent 退回纯对话）、
`TOPIC_BOT_DB_PATH`（SQLite 路径；不指定时 serial 使用 `data/topic_bot_sessions.db`，
native-queue 使用 `data/topic_bot_native_queue_sessions.db`，避免切换执行语义时复用状态）、
`DIGITAL_EMPLOYEE_MEMORY_DB_PATH`（记忆作用域 SQLite 路径；默认 `data/digital_employee_memory.db`，
serial/native-queue 共用，保证切换执行模式后仍复用原 Store）、
`FEISHU_SDK_DEBUG`（设 `1`/`true` 打开 Channel SDK 内部的 stale/去重/策略日志，排查
「消息没进来 / 被去重 / 被策略过滤」时用）。

## 文件

- `shared.py` —— 公共底座：共享会话键、群历史窗口、附件挂载与去重、lark-cli
  Environment/Bot Vault、双身份会话环境、OAuth/Session Vault 持久化、Agent 定义与配置读取。
- `memory.py` —— 个人/群记忆作用域、共享 SQLite 映射、Session 挂载与四个 Memory Custom Tool。
- `user_oauth.py` —— 单聊 Device OAuth、每用户 Vault/Credential、token 刷新、账号一致性校验与续跑编排。
- `initialize_digital_employee.py` —— 一键初始化：扫码建飞书应用 + 建数字员工 Agent + 置备 lark-cli（Environment + Vault，幂等）+ 把各 ID 写回 config.env。
- `create_digital_employee_agent.py` —— 只创建数字员工 Agent（不含 lark-cli 置备；配套手动分步用）。
- `update_digital_employee_agent.py` —— 原地更新现有 Agent 的 system prompt / 模型 / bot 名字（Agent ID 不变，不重扫码）。
- `provision_digital_employee_lark_cli.py` —— 为现有数字员工应用补齐 lark-cli Environment 和 Vault。
- `digital_employee.py` —— 唯一入口；一个话题一个 Session，仅 `@bot` 回复，并通过
  `--execution-mode serial|native-queue` 选择客户端串行或方舟原生队列。

> 群历史读取（`FeishuSender.list_messages`）、`IncomingMessage.create_time`、
> `HistoryMessage` 归一化在主包 `arkagent/feishu.py`，移植自源项目 `src/lark-channel.ts`。
