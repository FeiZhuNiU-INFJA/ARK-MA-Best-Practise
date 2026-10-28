# Topicx · Topic5/Topic6 统一飞书入口

Topicx 用一个飞书 Bot 接收营销复盘与热点周报请求。每次根消息先创建独立的轻量
Router Session；识别成功后，再创建对应的 Topic5 或 Topic6 Business Session。
两条 Session 轨迹通过 Gateway 生成的同一个 `task_id` 串联，Session title 也包含该 ID。

整体组件关系、运行时链路、状态机、存储边界与部署装配见
[Topicx 整体架构](./topicx_architecture/topicx_architecture.html)。

## 路由边界

| 请求 | Router 结果 | Business Session |
|---|---|---|
| 营销活动复盘 | `topic5` | Topic5 Coordinator + Validator |
| 社媒热点周报 | `topic6` | Topic6 Coordinator + Annotator |
| 其他请求 | `unsupported` | 不创建 |

Router 只挂载 `submit_business_intent` Custom Tool，不持有业务 Skill、MCP、Memory、
用户 Vault 或 lease token。模型不能提交 task、Session、Job、用户或租约身份字段。

## Topic6 门禁

Topic6 在创建 Business Session 前按以下顺序执行：

1. 校验并刷新任务发起人的妙搭 OAuth。
2. Gateway SQLite 以事务抢占全局 Topic6 lease。
3. 创建带用户 Vault 的 Topic6 Business Session。
4. 创建 Job 后将 lease 从 `acquired` 激活为 `active`。

Topic5 不使用该全局 lease，因此可与 Topic6 并行；不同 Topic5 话题也可并行。

## 话题与停止

根消息创建任务话题，后续进度、人工关卡、OAuth 提示和终态都回复到同一话题。
停止按钮只向 Business Session 发送 interrupt，不删除或中断 Router Session。只有观测到
interrupt 之后的新 `idle` 才确认 `stopped` 并释放 Topic6 lease；超时或 API 失败时保守持锁。

普通用户只使用飞书消息、人工关卡卡片和“停止任务”按钮，不使用 `/new`、`/continue`
或 `/stop`。默认只有任务发起人能停止自己的任务；可选的管理员配置允许指定角色账号
停止任意任务，文档不记录个人姓名。

## 部署

配置、权限、测试和故障排查见
[TOPICX_DEBUG_QUICKSTART.md](./TOPICX_DEBUG_QUICKSTART.md)。

统一更新并启动：

```bash
cd scenarios/feishu-bot/cases/topicx
./update_ma.sh
cd ../..
python -m arkagent run --case topicx
```

`update_ma.sh` 复用 Topic5/Topic6 各自的 Skill、Environment、Memory、Agent 和 Vault
定义，只额外创建最小 Router Environment/Agent。它也会按旧资源 ID 精确删除已下线的
Topic6 Insighter。
