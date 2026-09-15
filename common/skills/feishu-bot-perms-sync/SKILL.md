---
name: feishu-bot-perms-sync
description: 汇总并刷新飞书机器人（Bot）相关的权限说明为单个文档 common/docs/feishu_bot_permissions.md。覆盖本项目实际调用的飞书 OpenAPI（发消息 / 读群历史 / 下载消息资源 / 收群@事件）所需的 scope、注册脚本配置、以及权限相关错误码（如 230027 缺 im:message.group_msg）。当用户要求整理/更新飞书 bot 权限文档、核对某个 im API 需要哪些权限、或排查权限类错误码时使用。
---

# feishu-bot-perms-sync：飞书 Bot 权限说明汇总

把飞书开放平台上**本项目机器人实际用到的每个 OpenAPI 的权限要求 + 错误码**，
汇总成一份文档：`common/docs/feishu_bot_permissions.md`。

## 为什么单独做这份

lark-channel-sdk 的文档（见 `lark-channel-docs-sync` 生成的合集）只覆盖 SDK 经手的
**收发消息** scope；而本项目还要：

- **读整段群历史**（`GET /im/v1/messages`，群聊需 `im:message.group_msg`）——窗口上下文的关键；
- **下载群里用户上传的文件/图片**（`GET /im/v1/messages/:id/resources/:key`）。

这两类 scope 属于飞书 OpenAPI，不在 SDK 文档里，故单独汇总，避免翻多处。

## 何时使用

- 用户说「整理/更新飞书 bot 权限文档」「机器人要哪些权限」。
- 排查权限类错误码：`230027`（缺权限，常见缺 `im:message.group_msg`）、`234009` 等。
- 核对某个 im-v1 API 需要哪些 scope。

不负责：SDK 用法（走 `lark-channel-docs-sync`）；需要登录鉴权的后台页面。

## 权威来源（人工核对时打开这些页）

| 用途 | URL |
|---|---|
| 读群历史 | https://open.feishu.cn/document/server-docs/im-v1/message/list?lang=zh-CN |
| 下载消息资源 | https://open.feishu.cn/document/server-docs/im-v1/message/get-2?lang=zh-CN |
| 发送消息 | https://open.feishu.cn/document/server-docs/im-v1/message/create?lang=zh-CN |
| 群 @ 事件权限（对外共享） | https://open.feishu.cn/document/develop-robots/add-bot-to-external-group?lang=zh-CN |

> 这些页面是客户端渲染的 SPA，`curl` 抓到的是空壳。刷新本文档时**用带 JS 渲染能力的
> 抓取方式**（如 Agent 的 WebFetch，对每个 URL 提取「权限要求 + 前提条件 + 错误码表」），
> 再按 `feishu_bot_permissions.md` 现有结构人工/半自动整理。核对脚本 `check_doc.py` 只做
> 本地一致性校验，不联网。

## 校验（更新后本地跑）

```bash
python3 common/skills/feishu-bot-perms-sync/check_doc.py
```

脚本会检查 `common/docs/feishu_bot_permissions.md`：
- 是否覆盖本项目声明的全部 scope（与 `register_app.mjs` 的 `scopes.tenant` 对齐）；
- 关键错误码（`230027` 等）是否在文档里有说明；
- 四个官方来源 URL 是否都在文末列出。

## 关键事实（务必记住）

- 应用身份**读群聊历史** = 单聊任一权限（`im:message` 等）**＋额外** `im:message.group_msg`。
  只有 `im:message` 时读群历史会 `230027 need scope: im:message.group_msg`。
- **改 scope 后必须重新发布/重装应用**，旧 token 不会自动获得新 scope。
- 机器人**必须在被查询的群里**，否则读历史 `230002` / 下载资源 `234004`。
- 下载资源接口官方权限列的是 `im:message*`，未单列 `im:resource`；`im:resource` 作为冗余保留。
