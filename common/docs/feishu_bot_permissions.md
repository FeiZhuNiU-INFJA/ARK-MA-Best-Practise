# 飞书 Bot 权限说明（本项目用到的 scope + API + 错误码）

> 面向 digital-employee（数字员工，支持群聊与单聊）与 customer-a demo，梳理机器人**实际调用的每个飞书
> OpenAPI 所需权限**、对应的注册脚本配置、以及权限相关错误码的排查方法。
>
> 权威出处为飞书开放平台（`open.feishu.cn`），各 API 页链接见文末「来源」。SDK
> （lark-channel-sdk）文档只覆盖收发消息 scope，**拉群历史 / 下载资源这两类不在 SDK 文档里**，
> 故单独在此汇总。海外租户把域名换成 `open.larksuite.com` 即可。

## 一、本项目权限清单（一张表看全）

机器人由 [node-helper/register_app.mjs](../../scenarios/feishu-bot/node-helper/register_app.mjs)
的 `scopes.tenant` 申请以下 **tenant（应用身份）** 权限（不申请任何 user scope，无用户 OAuth）：

| 权限 key | 中文名 | 本项目用途 | 调用的 API |
|---|---|---|---|
| `im:message:send_as_bot` | 以应用的身份发消息 | Bot 回复群/单聊、发卡片 | `POST /im/v1/messages`、`/reply` |
| `im:message` | 获取与发送单聊、群组消息 | 收发消息的基础权限（读单聊消息、发消息都认它） | 多个 im API |
| `im:message.group_msg` | 获取群组中所有消息 | **读整段群历史**（窗口上下文靠它拉），缺则 `230027` | `GET /im/v1/messages`（群聊） |
| `im:chat:readonly` | 获取群组信息 | 读 thread/chat 容器信息（可选但保险） | 群信息类 API |
| `im:resource` | 获取与上传图片或文件资源 | 预留：下载群里用户上传的文件/图片（下载实现尚未落地） | `GET /im/v1/messages/:id/resources/:key` |
| `contact:user.id:readonly` | 获取用户 user ID | 取得租户级员工身份键，保证更换飞书应用后个人记忆仍可关联 | `GET /contact/v3/users/:user_id` / 消息事件 |

订阅事件：`im.message.receive_v1`（接收消息事件，WS 长连接消费）。

> ⚠️ 改动 scope 后**必须重新把应用发布/重装进 tenant**——已签发的 token 不会自动获得新
> scope。改注册脚本只是改代码，要么重新扫码注册，要么去开放平台手动加 scope 并发版。

## 二、按动作拆解：每个 OpenAPI 要什么权限

### 1. 发送 / 回复消息 —— `POST /im/v1/messages`

- **权限**（任一即可）：`im:message:send_as_bot`（本项目用这个，以应用身份发）、
  `im:message`、`im:message:send`（历史版本）、`im:message.send_as_user`（用户身份，不用）。
- 权限相关错误码：`230002` bot 不在群里、`230006` 未启用机器人能力、
  `230013` bot 对该用户不可用（可用范围/离职）、`230027` 缺权限、`230035` 发送权限被拒。

### 2. 读群历史 —— `GET /im/v1/messages`（窗口上下文的关键）

- **前提**：应用已启用机器人能力；**机器人必须在被查询的群里**。
- **权限**（分两步，本项目是「应用身份读群聊」）：
  1. 先要单聊场景任一：`im:message` / `im:message:readonly` / `im:message.history:readonly`（历史版本）。
  2. 读**群聊**消息，在上面基础上**还必须额外**开 `im:message.group_msg`（获取群组中所有消息）。
- `container_id_type` 取 `chat`（单聊+群聊，普通群里只能取到话题根消息）或 `thread`（话题，取话题内全部回复）。
- 权限相关错误码：
  - **`230027` Lack of necessary permissions** —— 本项目踩过的坑，报文常带
    `need scope: im:message.group_msg`，即缺群消息权限；加上并发版即可。
  - `230002` bot 不在群、`230073` 话题对操作者不可见、`231203` 群保密模式禁复制/取消息。

### 3. 下载消息资源（用户上传的文件/图片）—— `GET /im/v1/messages/:message_id/resources/:file_key`

- **前提**：启用机器人能力；机器人与该消息在同一会话内。
- **权限**（任一即可）：`im:message` / `im:message:readonly` / `im:message.history:readonly`。
  > 注意：官方该 API 页的「权限要求」列的是上面这些 `im:message*`，**并未单列
  > `im:resource`**。而部分实操教程要求开 `im:resource` 才能正常上传/下载资源。结论：
  > `im:message` 是明确要的；`im:resource` 是否额外必需以开放平台权限搜索框与该 API 页当时
  > 显示为准——**加着它属于冗余但无害**，本项目保留申请。
- 使用限制：`type=file` 不分片仅支持 <100MB，≥100MB 用 `Range: bytes=<start>-<end>`
  分片（单片 ≤32MB）本地合并；`type=image` 不支持分片，仅完整下载 <100MB；不支持表情包/
  合并转发子消息/消息卡片资源。
- 权限/可用性错误码：`234004` App not in chat（bot 不在群）、`234009` 缺权限/外部群不支持、
  `234003` file_key 与 message_id 不匹配、`234038` 保密/防泄密模式禁下载、`234043` 不支持的消息类型。

### 4. 收群里 @bot 事件

- 群聊 @ 事件权限：`im:message.group_at_msg:readonly`（仅覆盖**用户**提及）与
  `im:message.group_at_msg.include_bot:readonly`（含 **bot** 提及）不同——若群里 @bot 收不到消息，
  优先核对是否开了「含 bot 提及」那一个。本项目走 `im.message.receive_v1` 事件订阅。

## 三、权限相关错误码速查

| 错误码 | 出现接口 | 含义 | 处理 |
|---|---|---|---|
| `230027` | 读群历史 / 发消息 | 缺权限（常见缺 `im:message.group_msg`） | 加对应 scope，重新发布应用版本 |
| `230002` | 读群历史 / 发消息 | bot 不在群里 | 把机器人加进该群 |
| `230006` | 读群历史 / 发消息 | 未启用机器人能力 | 开放平台启用机器人能力 |
| `230013` | 发消息 | bot 对该用户不可用（可用范围/离职） | 编辑应用可用范围并发版 |
| `230035` | 发消息 | 发送权限被拒 | 核对 send scope 与可用范围 |
| `234004` | 下载资源 | App 不在消息所在群 | 把机器人加进群 / 核对 message_id |
| `234009` | 下载资源 | 缺权限 / 外部群不支持 | 补权限重发版；外部群另开对外共享 |

## 四、来源

- 获取会话历史消息：https://open.feishu.cn/document/server-docs/im-v1/message/list?lang=zh-CN
- 获取消息中的资源文件：https://open.feishu.cn/document/server-docs/im-v1/message/get-2?lang=zh-CN
- 发送消息：https://open.feishu.cn/document/server-docs/im-v1/message/create?lang=zh-CN
- 机器人支持外部群（@事件权限）：https://open.feishu.cn/document/develop-robots/add-bot-to-external-group?lang=zh-CN
- 如何开通权限：https://open.feishu.cn/document/ukTMukTMukTM/uQjN3QjL0YzN04CN2cDN?lang=zh-CN
- 如何启用机器人能力：https://open.feishu.cn/document/uAjLw4CM/ugTN1YjL4UTN24CO1UjN/trouble-shooting/how-to-enable-bot-ability

> 本文由 `common/skills/feishu-bot-perms-sync/` 抓取整理，内容随飞书开放平台更新可能变动，
> 以官方页面实时显示的「权限要求」为准。
