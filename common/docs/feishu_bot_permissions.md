# 飞书 Bot 权限说明（Topicx 权限并集 + API + 错误码）

> 面向 Topicx 统一入口（Topic5 营销复盘 + Topic6 热点周报），并兼顾 digital-employee
> 与 customer-a demo，梳理应用实际调用的飞书 OpenAPI 权限、事件与回调、用户 OAuth
> 以及权限相关错误码。
>
> 权威出处为飞书开放平台（`open.feishu.cn`），各 API 页链接见文末「来源」。SDK
> （lark-channel-sdk）文档只覆盖收发消息 scope，**拉群历史 / 下载资源这两类不在 SDK 文档里**，
> 故单独在此汇总。海外租户把域名换成 `open.larksuite.com` 即可。

## 一、Topicx 共用应用权限清单

Topic5、Topic6 和 Topicx Router 共用一个飞书应用。完整权限基线是 Topic5 与 Topic6
的并集；[node-helper/register_app.mjs](../../scenarios/feishu-bot/node-helper/register_app.mjs)
只负责创建应用和申请基础权限，不能代替开放平台中的完整人工配置。

### 1.1 应用身份权限（权限管理 → API 权限）

| 权限 key                            | 必需范围        | 本项目用途                                                 | 主要 API                              |
| ----------------------------------- | --------------- | ---------------------------------------------------------- | ------------------------------------- |
| `im:message`                      | Topic5 + Topic6 | 接收群聊/单聊消息，读取消息基础信息                        | IM 消息 API                           |
| `im:message:send_as_bot`          | Topic5 + Topic6 | Bot 回复文本、发送进度卡片和人工关卡卡片                   | `POST /im/v1/messages`、`/reply`  |
| `im:message:update`               | Topic5 + Topic6 | 审核后更新原交互卡片；缺少时按钮回调可到达，但卡片无法刷新 | `PATCH /im/v1/messages/:message_id` |
| `im:resource`                     | Topic5 + Topic6 | 上传/下载消息中的图片或文件                                | IM image/file/resource API            |
| `docx:document`                   | Topic5 + Topic6 | 读写飞书文档正文                                           | Docx API                              |
| `docx:document.content:read`      | Topic6          | 读取文档内容；部分租户单独要求                             | Docx content API                      |
| `drive:drive`                     | Topic5          | 上传报告图表 PNG 并写入文档 image block                    | Drive media API                       |
| `space:folder:create`             | Topic6          | 创建交付用云空间文件夹                                     | Drive/Space API                       |
| `docs:document.media:upload`      | Topic6          | 上传 Markdown 导入所需临时素材                             | Docs import media API                 |
| `docs:document:import`            | Topic6          | 创建并查询云文档导入任务                                   | Docs import API                       |
| `docs:permission.member:create`   | Topic5 + Topic6 | 将发起人和管理员添加为文档协作者                           | Permission member API                 |
| `docs:permission.member:transfer` | Topic5 + Topic6 | 将报告所有权转给任务发起人                                 | Permission member API                 |
| `docs:permission.member:retrieve` | Topic5 + Topic6 | 查询协作者并复核交付权限                                   | Permission member API                 |
| `contact:user.base:readonly`      | 可选            | 将卡片操作人的 `open_id` 显示为姓名；未开时回退短 ID     | Contact API                           |
| `contact:user.id:readonly`        | 可选            | `open_id` 与 `user_id` 反查                            | Contact API                           |

以下权限仅在需要读取完整群历史或运行旧 digital-employee/customer-a 流程时启用，不是
Topicx 新任务入口的最低必需项：

| 权限 key                 | 用途                            |
| ------------------------ | ------------------------------- |
| `im:message.group_msg` | 读取完整群消息历史              |
| `im:chat:readonly`     | 查询群组和 thread/chat 容器信息 |

### 1.2 Topic6 妙搭用户身份权限

下列权限需要先在应用后台开通，再由每位妙搭发布人完成用户 OAuth；不能通过 Bot 的
`tenant_access_token` 代替：

| 权限 key              | 用途                                         |
| --------------------- | -------------------------------------------- |
| `auth:user.id:read` | 识别扫码授权用户的 `open_id`               |
| `spark:app:read`    | 查询妙搭应用及发布状态                       |
| `spark:app:write`   | 创建、更新妙搭应用并发起发布                 |
| `offline_access`    | 获取刷新能力，供 Gateway 在 token 到期前续期 |

### 1.3 事件与回调（不是 API scope）

| 类型     | key                       | 用途                                    |
| -------- | ------------------------- | --------------------------------------- |
| 事件订阅 | `im.message.receive_v1` | 接收群聊/单聊消息，触发 Topicx Router   |
| 回调订阅 | `card.action.trigger`   | 接收 Topic5/Topic6 人工关卡卡片按钮操作 |

本项目使用 lark-channel-sdk WebSocket，因此在开放平台的“事件与回调 → 回调配置”中
选择“使用长连接接收回调”，并单独添加新版“卡片回传交互”
`card.action.trigger`。它不是 `im:*` 权限，只有 API scope 而没有该回调时，客户端会提示
“该应用尚未配置卡片交互功能”。

> 改动 scope、事件或回调后，应保存配置并发布/启用应用版本；已有 token 不应被假定会
> 自动获得新权限。Bot 还必须处于应用可用范围内，并已加入目标群。

## 二、按动作拆解：每个 OpenAPI 要什么权限

### 1. 发送 / 回复消息 —— `POST /im/v1/messages`

- **权限**（任一即可）：`im:message:send_as_bot`（本项目用这个，以应用身份发）、
  `im:message`、`im:message:send`（历史版本）、`im:message.send_as_user`（用户身份，不用）。
- 权限相关错误码：`230002` bot 不在群里、`230006` 未启用机器人能力、
  `230013` bot 对该用户不可用（可用范围/离职）、`230027` 缺权限、`230035` 发送权限被拒。

### 2. 更新交互卡片 —— `PATCH /im/v1/messages/:message_id`

- **权限**：`im:message:update`。
- 只能更新当前应用发送的交互卡片；更换飞书应用后，新 Bot 无法更新旧 Bot 发送的卡片。
- 已发送卡片的可更新有效期通常为 14 天。
- `card.action.trigger` 负责把点击事件送到 Gateway，`im:message:update` 负责 Gateway
  处理后刷新卡片，两项缺一不可。

### 3. 读群历史 —— `GET /im/v1/messages`（窗口上下文的关键）

- **前提**：应用已启用机器人能力；**机器人必须在被查询的群里**。
- **权限**（分两步，本项目是「应用身份读群聊」）：
  1. 先要单聊场景任一：`im:message` / `im:message:readonly` / `im:message.history:readonly`（历史版本）。
  2. 读**群聊**消息，在上面基础上**还必须额外**开 `im:message.group_msg`（获取群组中所有消息）。
- `container_id_type` 取 `chat`（单聊+群聊，普通群里只能取到话题根消息）或 `thread`（话题，取话题内全部回复）。
- 权限相关错误码：
  - **`230027` Lack of necessary permissions** —— 本项目踩过的坑，报文常带
    `need scope: im:message.group_msg`，即缺群消息权限；加上并发版即可。
  - `230002` bot 不在群、`230073` 话题对操作者不可见、`231203` 群保密模式禁复制/取消息。

### 4. 下载消息资源（用户上传的文件/图片）—— `GET /im/v1/messages/:message_id/resources/:file_key`

- **前提**：启用机器人能力；机器人与该消息在同一会话内。
- **权限**（任一即可）：`im:message` / `im:message:readonly` / `im:message.history:readonly`。
  > 注意：官方该 API 页的「权限要求」列的是上面这些 `im:message*`，**并未单列
  > `im:resource`**。而部分实操教程要求开 `im:resource` 才能正常上传/下载资源。结论：
  > `im:message` 是明确要的；`im:resource` 是否额外必需以开放平台权限搜索框与该 API 页当时
  > 显示为准——**加着它属于冗余但无害**，本项目保留申请。
  >
- 使用限制：`type=file` 不分片仅支持 <100MB，≥100MB 用 `Range: bytes=<start>-<end>`
  分片（单片 ≤32MB）本地合并；`type=image` 不支持分片，仅完整下载 <100MB；不支持表情包/
  合并转发子消息/消息卡片资源。
- 权限/可用性错误码：`234004` App not in chat（bot 不在群）、`234009` 缺权限/外部群不支持、
  `234003` file_key 与 message_id 不匹配、`234038` 保密/防泄密模式禁下载、`234043` 不支持的消息类型。

### 5. 飞书文档交付与导入

- Topic5 使用 `docx:document`、`drive:drive` 和三项 `docs:permission.member:*` 权限，
  完成正文写入、图片上传、协作者校验与所有权转移。
- Topic6 在上述权限基础上使用 `docx:document.content:read`、
  `space:folder:create`、`docs:document.media:upload` 和 `docs:document:import`，
  完成素材读取、文件夹创建和文档导入。

### 6. Topic6 妙搭发布

- 应用后台需开通 `auth:user.id:read`、`spark:app:read`、`spark:app:write`。
- 每位发布人还需通过项目授权流程完成用户 OAuth，并授予 `offline_access`。
- 缺少应用权限与用户未授权是两个独立问题，需要分别处理。

### 7. 收群里 @bot 事件

- 群聊 @ 事件权限：`im:message.group_at_msg:readonly`（仅覆盖**用户**提及）与
  `im:message.group_at_msg.include_bot:readonly`（含 **bot** 提及）不同——若群里 @bot 收不到消息，
  优先核对是否开了「含 bot 提及」那一个。本项目走 `im.message.receive_v1` 事件订阅。

## 三、权限相关错误码速查

| 错误码                                   | 出现接口          | 含义                                                | 处理                                          |
| ---------------------------------------- | ----------------- | --------------------------------------------------- | --------------------------------------------- |
| `230027`                               | 读群历史 / 发消息 | 缺权限（常见缺 `im:message.group_msg`）           | 加对应 scope，重新发布应用版本                |
| `230002`                               | 读群历史 / 发消息 | bot 不在群里                                        | 把机器人加进该群                              |
| `230006`                               | 读群历史 / 发消息 | 未启用机器人能力                                    | 开放平台启用机器人能力                        |
| `230013`                               | 发消息            | bot 对该用户不可用（可用范围/离职）                 | 编辑应用可用范围并发版                        |
| `230035`                               | 发消息            | 发送权限被拒                                        | 核对 send scope 与可用范围                    |
| 客户端提示“该应用尚未配置卡片交互功能” | 点击卡片按钮      | 未订阅 `card.action.trigger` 或未配置回调接收方式 | 选择长连接并添加新版卡片回传交互回调          |
| `230001`                               | 更新卡片          | 参数无效，常见于当前 Bot 不是消息发送者             | 仅更新当前应用发送的卡片；新 Bot 不复用旧卡片 |
| `234004`                               | 下载资源          | App 不在消息所在群                                  | 把机器人加进群 / 核对 message_id              |
| `234009`                               | 下载资源          | 缺权限 / 外部群不支持                               | 补权限重发版；外部群另开对外共享              |

## 四、来源

- 获取会话历史消息：https://open.feishu.cn/document/server-docs/im-v1/message/list?lang=zh-CN
- 获取消息中的资源文件：https://open.feishu.cn/document/server-docs/im-v1/message/get-2?lang=zh-CN
- 发送消息：https://open.feishu.cn/document/server-docs/im-v1/message/create?lang=zh-CN
- 更新应用发送的消息卡片：https://open.feishu.cn/document/server-docs/im-v1/message/patch
- 处理卡片回调：https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/handle-card-callbacks?lang=zh-CN
- 机器人支持外部群（@事件权限）：https://open.feishu.cn/document/develop-robots/add-bot-to-external-group?lang=zh-CN
- 如何开通权限：https://open.feishu.cn/document/ukTMukTMukTM/uQjN3QjL0YzN04CN2cDN?lang=zh-CN
- 如何启用机器人能力：https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-enable-bot-ability

> 本文由 `common/skills/feishu-bot-perms-sync/` 抓取整理，内容随飞书开放平台更新可能变动，
> 以官方页面实时显示的「权限要求」为准。
