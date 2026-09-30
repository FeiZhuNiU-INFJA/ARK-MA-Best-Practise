# 飞书 API 进度播报

进度播报通过 `lark-cli im +messages-send` 调用飞书消息 API，不使用浏览器、Webhook 猜测或页面操作。

## 启动前人工确认

发送消息对其他人可见。第一次发送前必须让用户一次性确认：

- 接收目标：群 `chat_id=oc_xxx` 或个人 `user_id=ou_xxx`，二选一。
- 发送身份：`user` 或 `bot`；建议沿用用户身份，但不能擅自决定。Bot 必须已经在目标群或具备私聊关系。
- 消息内容：确认使用下方固定模板。
- 播报级别：默认 `milestones`；可选 `final-only`。不要逐条输出内部命令。

若这些输入缺失，先保留人工输入节点；不能搜索相似群名后自动选群，也不能把“允许发布”解释为“允许发消息”。

## 关键节点

`milestones` 模式最多发送以下消息：

1. `preflight_passed`：HTML 预检通过，包含测试批次与 SHA-256 短值。
2. `app_ready`：新应用已创建，或现有目标已确认，包含 `app_id`。
3. `source_pushed`：源码已推送，包含 `app_id` 和 commit 短值。
4. `release_started`：妙搭发布已启动，包含 `release_id`。
5. `release_finished`：发布成功，包含妙搭 API 返回的链接。
6. `release_failed`：发布失败或需要人工恢复，包含安全的错误类别，不发送原始日志。
7. `authorization_required`：仅在鉴权确实需要用户操作时发送；不得附 token 或 device code。

`final-only` 只发送 `release_finished` 或 `release_failed`。

## 生成安全消息

```bash
python3 scripts/progress_payload.py \
  --run-id "<run_id>" \
  --stage release_started \
  --app-id "<app_id>" \
  --release-id "<release_id>"
```

脚本返回 `markdown` 和最长 50 字符的 `idempotency_key`。它只接受白名单字段，不接受自由格式日志、邮箱、token 或 cookie。

## 调用飞书 API

优先使用带确认门禁的 wrapper。未加 `--send` 时只调用 API dry-run，不发送消息：

```bash
python3 scripts/send_progress.py \
  --confirmed \
  --identity "<confirmed_identity>" \
  --chat-id "<confirmed_chat_id>" \
  --run-id "<run_id>" \
  --stage release_started \
  --app-id "<app_id>" \
  --release-id "<release_id>"
```

预览成功且确认仍有效后，用相同参数追加 `--send`。个人消息把 `--chat-id` 换成 `--user-id`。wrapper 会解析兼容的 CLI 绝对路径，只返回 `sent|failed|previewed`、幂等键和必要的 `message_id`，不会回显原始 API 响应。

若 CLI 明确返回 IM scope 不足，再请求相应 `im` 授权；不要为了播报清除已有 `apps` 授权。`--as user` 通常需要 `im:message.send_as_user` 和 `im:message`；`--as bot` 需要 bot 发消息权限及目标会话关系。

## 失败处理

- 用同一幂等键重试同一节点一次；一小时内不会重复发送。
- 第二次仍失败：记录 `progress_delivery_error`，继续安全可恢复的发布流程。
- 如果用户明确要求“消息送达是发布前置条件”，则在失败节点暂停，不创建后续外部写入。
- 最终交付列出每个节点的 `sent|failed|skipped` 和 `message_id`；不因播报失败回滚已经完成的 release。
