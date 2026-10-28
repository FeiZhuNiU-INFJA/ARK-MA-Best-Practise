# Topicx · 调试快速上手

## 1. 初始化

Topic5、Topic6 和 Topicx 共用 `topicX` Python 环境与一份飞书 Bot 配置：

```bash
conda create -n topicX python=3.11 -y
conda activate topicX
python -m pip install -e ".[dev]"

cd scenarios/feishu-bot
python -m arkagent init --case topicx
```

初始化只创建飞书应用并写 `~/.arkagent/cases/topicX/config.env`，不创建业务 MA 资源。

## 2. 必需配置

在同一 `config.env` 中补齐：

```env
ARK_API_KEY=${ARK_API_KEY}
FEISHU_APP_ID=${FEISHU_APP_ID}
FEISHU_APP_SECRET=${FEISHU_APP_SECRET}

# 可选：留空或不配置表示所有飞书用户均可发起任务
AUTHORIZED_OPEN_IDS=
# 可选：不配置时只有任务发起人能停止自己的任务
TOPICX_ADMIN_OPEN_IDS=

HOT_TOPICS_MCP_URL=${HOT_TOPICS_MCP_URL}
BLUEAI_API_KEY=${BLUEAI_API_KEY}
DATAHUB_ENDPOINT=${DATAHUB_ENDPOINT}
DATAHUB_API_KEY=${DATAHUB_API_KEY}

SSJH_API_TOKEN=${SSJH_API_TOKEN}
REPORT_COVER_API_KEY=${REPORT_COVER_API_KEY}
# 可选兜底；Topic5 正常任务优先转移给发起人的 FEISHU_USER_OPEN_ID
FEISHU_DELIVERY_OWNER_OPEN_ID=ou_xxx
```

`AUTHORIZED_OPEN_IDS` 是可选白名单：为空表示所有用户可使用；非空时，仅其中用户可使用，
且管理员也必须在白名单内。`TOPICX_ADMIN_OPEN_IDS` 也是可选配置，支持一个或多个互不重复
的角色账号；为空时只有任务发起人能停止自己的任务。真实 Key、Secret 和 Token 只放本地
配置，不写入仓库。

Topic6 的每位妙搭发布人还需独立完成用户 OAuth：

```bash
cd scenarios/feishu-bot
python -m cases.topic6.authorize_miaoda_user
```

## 3. 更新资源

```bash
cd scenarios/feishu-bot/cases/topicx
./update_ma.sh
```

统一脚本按顺序执行：

1. 运行 Topic5、Topic6、Topicx 相关测试。
2. 打包并上传 Topic5/Topic6 Skills，更新两套业务 MA 资源。
3. 按旧 ID 精确删除已下线的 Topic6 Insighter。
4. 创建或原地更新最小 Router Environment 和 Router Agent。
5. 将三套资源 ID 回写并校验到同一 `config.env`。
6. 运行 JSON、Shell、占位符和敏感信息检查。

## 4. 启动

同一个飞书 App 只能运行一个 Gateway 进程：

```bash
cd scenarios/feishu-bot
python -m arkagent run --case topicx
```

不要同时启动旧的 `topic5` 或 `topic6` Gateway。

## 5. 冒烟测试

分别新发三条根消息：

```text
复盘报告 demo
热点周报 demo
帮我订会议室
```

预期：

- 复盘请求创建 Router Session 和 Topic5 Business Session。
- 热点请求先完成 OAuth，再取得 Topic6 lease，最后创建 Business Session。
- 不支持请求只创建 Router Session，并在原话题固定拒绝。
- 所有进度和卡片都位于各自触发消息的话题。

每个 Router 和 Business Session title 都含同一个 `task_id`。Gateway 控制库还能通过
Router Session ID、Business Session ID、Job ID、根消息或话题反查该任务。

## 6. 停止与恢复

运行中或等待人工关卡时，任务发起人和可选管理员可点击卡片底部“停止任务”。流程先进入
`stopping`，Gateway 只 interrupt Business Session；确认 interrupt 之后的新 `idle` 后才转
`stopped`。若 interrupt 超时或失败，任务保持 `stopping`，Topic6 lease 不释放。

Gateway 重启会恢复 `routing`、`authorizing`、`acquired`、`starting`、`running`、
`wait_hc` 和 `stopping` 任务。普通用户不通过 `/new`、`/continue` 或 `/stop` 操作任务；
这些旧命令不属于统一入口协议。

## 7. 关键检查

```bash
bash -n cases/topicx/update_ma.sh cases/topicx/ma-resources/create_all.sh
python -m unittest \
  tests.test_ark \
  tests.test_case_registry \
  tests.test_topicx_config \
  tests.test_topicx_control_store \
  tests.test_topicx_router_and_cards
```

真实端到端还需要有效方舟、飞书、DataHub、SSJH、热点 MCP 和妙搭 OAuth，离线测试不能
替代这部分验收。
