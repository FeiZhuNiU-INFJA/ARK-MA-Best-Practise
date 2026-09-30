# topic6 · 调试快速上手

按顺序过一遍就能起服务、在飞书里跑通 `热点周报 test`。所有命令默认在仓库根目录 `ark-agent-feishu-bot/` 下执行,`cd` 位置在每步开头标注。

---

## 0. 前置

- 已装好 `python3` / `curl` / `jq` / `node`（`arkagent init` 内部会调 Node 子进程完成飞书应用扫码建站）。
- 方舟账号:已开通 Managed Agents,拿到 `ARK_API_KEY`。
- topic6 数据源 MCP:已部署或本地起好 mock,拿到 `HOT_TOPICS_MCP_URL`。

---

## 1. 建飞书 Bot(首次)

```bash
cd scenarios/feishu-bot
python3 -m arkagent init --topic6
```

topic6 专用轻量初始化:只问方舟 API Key + 扫码建飞书应用,写入 `~/.arkagent/cases/topic6/config.env`。产出:

- `ARK_API_KEY`
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`

**不会**创建 digital-employee Agent,也不要求 mock MCP 地址——那些是另一场景的东西。
topic6 的 MA 资源在第 2 步用 `create_all.sh` 单独建。

> 注意:不要跑不带参数的 `arkagent init`,那是 digital-employee 场景专用,会强制要求输入 mock MCP 公网地址。

已建过 Bot 就跳过这步。

---

## 1.1 飞书权限清单(首次)

`init --topic6` 只帮你把应用建起来,**不会**自动申请任何权限 scope 与事件订阅——这些都得人工在飞书 [Open Platform 后台](https://open.feishu.cn/app) 里点。按下面 checklist 一次点完,后面调试就不用来回补权限。

### 权限 scope(权限管理 → API 权限)

**消息与卡片(必需)**

- [ ] `im:message` — 接收群/单聊消息事件
- [ ] `im:message:send_as_bot` — 以 Bot 身份回复文本
- [ ] `im:resource` — 上传/下载图片、文件(Phase G 抓飞书图片必需)

**飞书文档(F/G/H 阶段必需)**

- [ ] `docx:document` — 读写飞书文档正文(Phase F 拉草稿、Phase H 写回)
- [ ] `docx:document.content:read` — 仅读文档内容(部分租户单独开)
- [ ] `space:folder:create` — 创建云空间文件夹；在“应用身份权限”中搜索“创建云空间文件夹”。不要误选 `drive:drive:version` 等文档版本权限
- [ ] `docs:document.media:upload` — 上传 Phase F 导入所需的 Markdown 临时素材
- [ ] `docs:document:import` — 创建并查询云文档导入任务
- [ ] `docs:permission.member:create` — 把报告发起人和管理员添加为文档协作者
- [ ] `docs:permission.member:transfer` — 将报告所有权转给任务发起人
- [ ] `docs:permission.member:retrieve` — 授权后查询协作者列表做结果复核

**妙搭用户身份(Phase H 必需)**

- [ ] `auth:user.id:read` — 扫码后识别授权用户的 `open_id`
- [ ] `spark:app:read` — 查询妙搭应用与发布状态
- [ ] `spark:app:write` — 创建/更新妙搭应用并发起发布

以上三项需在开发者后台为应用开通；用户再通过 §2.3 的脚本扫码同意。脚本还会申请
`offline_access` 以便 Gateway 自动续期。

**可选**

- [ ] `contact:user.id:readonly` — open_id ↔ user_id 反查(若白名单只用 open_id 可省)
- [ ] `contact:user.base:readonly` — 反查 open_id 对应真名,用于 HC 卡片"XXX 已通过/驳回"回显操作人姓名(未开时回退为 open_id 后 6 位短标识,不影响流转)

### 事件订阅(事件与回调 → 事件订阅)

- [ ] `im.message.receive_v1` — 用户在群/单聊里发消息(触发词入口)
- [ ] `card.action.trigger` — HC1/HC2/HC3 卡片按钮点击回调(HITL 必需)

### 生效方式

新增权限后必须提交审核并发布应用版本；仅勾选但未发布不会对
`tenant_access_token` 生效。

- **企业自建应用**:保存后即时生效,无需审核。
- **应用状态**要点到「启用」,并在目标群里把 Bot 加为群成员;单聊需管理员放开可用范围。

跑通冒烟测试后,如果发现某个动作报权限错误,回来对照这份清单补齐即可。

---

## 2. 环境变量清单(跑 create_all.sh 前必须齐)

**全部写到 `~/.arkagent/cases/topic6/config.env`**(该文件已在 §1 由 `init --topic6` 生成,追加即可,不会入库)。

### 2.1 通用(方舟 + 飞书)

```env
# 方舟——控制台申请
ARK_API_KEY=xxx

# 飞书——init --topic6 已自动写入
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

`create_all.sh` 会默认将 `FEISHU_APP_ID/FEISHU_APP_SECRET` 复用为沙箱使用的
`LARK_APP_ID/LARK_APP_SECRET`，并把 App ID 同步为 lark-cli 使用的
`LARKSUITE_CLI_APP_ID`。只有 Gateway 与沙箱需要使用不同飞书应用时，才在配置中
显式设置 `LARK_APP_ID` 和 `LARK_APP_SECRET`。

### 2.2 业务侧 API Key(向业务对接人获取)

```env
# 热点 MCP——BlueView 的爬虫服务
HOT_TOPICS_MCP_URL=https://smartai.blueviewai.com/mcp/crawler-hot-topics-server
BLUEAI_API_KEY=<业务对接人给的 Key>

# DataHub——Phase C 标注要用
DATAHUB_ENDPOINT=https://bmc-data-hub.bluemediagroup.cn/...
DATAHUB_API_KEY=<业务对接人给的 Key>
```

### 2.3 妙搭用户授权(每位操作人首次使用前)

Phase H 不使用 `MIAODA_TOKEN`，也不读取 Gateway 主机上的 lark-cli 登录态。运行
Topic6 专用脚本，由操作人扫码后把短期 access token 写入其专属 Ark Vault：

```bash
cd scenarios/feishu-bot
python cases/topic6/authorize_miaoda_user.py
```

脚本通过 token 自动查询扫码者的 `open_id`，所以不需要传 open_id。管理员希望
防止扫错账号时，可用：

```bash
python cases/topic6/authorize_miaoda_user.py --expected-open-id ou_xxx
```

refresh token 仅保存在权限为 `0600` 的 Topic6 SQLite 数据库中，不会进入 MA
沙箱或日志。Gateway 在每次新建 Session 前按消息发送者查找并按需刷新 token；
未授权用户会在任务启动前收到提示。

自有 TOS Bucket 调试期不必配置，`environment.json` 已禁用 output storage。

### 2.4 白名单(建议设,防误触)

```env
AUTHORIZED_OPEN_IDS=ou_xxx ou_yyy   # 空格或逗号分隔
```

### 加载到 shell(每次开新终端都要跑)

```bash
set -a; source ~/.arkagent/cases/topic6/config.env; set +a
```

`set -a` 让 source 出来的所有变量自动 export,省去挨个 export。跑 `pack_skills.sh` / `upload_skills.py` / `create_all.sh` 都依赖这一步。

---

## 3. 建 topic6 MA 资源(首次)

```bash
cd scenarios/feishu-bot/cases/topic6

# 3.1 打包 6 个 Skill zip → tools/out/*.zip
./tools/pack_skills.sh

# 3.2 上传 Skill → 写回 ma-resources/skill_ids.json
python3 tools/upload_skills.py

# 3.3 建 Environment / Memory Store / Annotator / Insighter / Coordinator
./ma-resources/create_all.sh
```

 `create_all.sh` 会打印:

- `ENVIRONMENT_ID`
- `MEMORY_STORE_ID`
- `AGENT_ANNOTATOR_ID` / `AGENT_INSIGHTER_ID`
- `AGENT_COORDINATOR_ID`（脚本会自动回写 config.env）

后续只是改 Prompt / Skill,直接重跑 `./ma-resources/create_all.sh` 即可——3 个 Agent 每次都会强制重建,skill_id / prompt 都会一并生效。仅当 `environment.json` 或 memory md 文件也改过时,才分别加上 `--update-env` / `--update-memory`。

### 3.4 全量更新所有 Topic 6 资源

以下流程会:

- 运行完整测试
- 重新打包并强制上传全部 6 个 Skills
- 原地更新 Environment
- 更新已有 Memory 内容
- 删除并重建 Annotator、Insighter、Coordinator
- 自动回写新的资源 ID

在任意目录执行均可:

```bash
/Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/update_ma.sh
```

脚本默认读取 `~/.arkagent/cases/topic6/config.env`，并自动使用当前
`nio-ma-demo` 环境；若未激活但本机有 `conda`，则自动通过 `conda run` 执行。
它不会停止或重启 Gateway。更新完成后手动重启：

| 组件 | 更新方式 | ID |
|---|---|---|
| 6 个 Skills | 强制重新上传 | **变化** |
| Annotator / Insighter / Coordinator | 删除同名旧 Agent 后重建 | **变化** |
| Environment | 按名称原地更新 | **不变** |
| Memory Store | 按名称原地更新 | **不变** |
| 飞书 App / Vault | 不由脚本更新 | **不变** |

Coordinator 的新 ID 会自动回写 `~/.arkagent/cases/topic6/config.env`。运行中的
Gateway 仍持有旧 ID，所以脚本结束后必须重启：

```bash
cd /Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot
python cases/topic6/authorize_miaoda_user.py  # 每位妙搭发布人首次执行一次
python -m arkagent run --case topic6
```
---

## 4. 确认 topic6 资源 ID

`create_all.sh` 会打印以下 ID:

- `ENVIRONMENT_ID`
- `MEMORY_STORE_ID`
- `AGENT_ANNOTATOR_ID`
- `AGENT_INSIGHTER_ID`
- `AGENT_COORDINATOR_ID`

如果 `~/.arkagent/cases/topic6/config.env` 已存在,脚本会自动更新:

```env
TOPIC6_COORDINATOR_AGENT_ID=<AGENT_COORDINATOR_ID>
TOPIC6_ENVIRONMENT_ID=<ENVIRONMENT_ID>
TOPIC6_MEMORY_STORE_ID=<MEMORY_STORE_ID>
```

更新完成后重新加载配置,或重启 Gateway 使新 ID 生效。

---

## 5. 起服务

```bash
cd scenarios/feishu-bot
python3 -m arkagent run --case topic6
```

启动日志出现下面这行才算 topic6 装配成功:

```
- topic6 场景：已启用(coordinator=xxx, env=xxx)
topic6 触发词：热点报告 / 热点周报(可加 test/demo/full 指定模式)
```

想拉更详细日志:`ARKAGENT_LOG_LEVEL=DEBUG python3 -m arkagent run --case topic6`。

---

## 5. 冒烟测试

在飞书 Bot 私聊或群里发送:

```
热点周报 test
```

需要只跑 50 条样本并继续生成演示报告时发送：

```text
热点周报 demo
```

demo 在 HC1 通过后直接进入洞察与报告阶段,不会触发全量标注和 HC2。

正常应该看到:

1. Bot 回复"已收到,启动 topic6 pipeline...";
2. Gateway 日志滚动 SSE `agent.message.delta`;
3. 依次弹出 **HC1 (蓝)** / **HC2 (绿)** / **HC3 (紫)** 三张交互卡片,每张都有「通过 / 打回 / 备注」三按钮;
4. 点击按钮 → Coordinator resume → 继续下一 Phase;
5. 最终产出飞书文档链接。

---

## 6. 常见故障排查

| 现象                              | 排查方向                                                                                                                                                             |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 日志"topic6 场景:未启用"          | 检查`TOPIC6_COORDINATOR_AGENT_ID` 是否已写入 config.env、拼写是否正确                                                                                              |
| Coordinator 拉不到 hot-topics MCP | `HOT_TOPICS_MCP_URL` 未 export 就跑了 `create_all.sh`,MCP URL 被空值渲染进 Agent 定义;重新 export 后加 `--update-env` 重跑 `create_all.sh`(Agent 会自动重建) |
| Skill 找不到                      | `skill_ids.json` 有 null 项,重跑 `upload_skills.py`                                                                                                              |
| HC 卡片点击后无响应               | Feishu Bot 后台"事件订阅"里是否开启`card.action.trigger` 权限                                                                                                      |
| SSE 中断/超时                     | 单会话默认 10 分钟,超长任务加大`SESSION_TIMEOUT_MS`(毫秒)                                                                                                          |
| 图片抓取失败                      | 已知风险点,飞书`im.v1.images.get` 并发大图不稳定,重跑一次 Phase G 即可                                                                                             |

---

## 附:相关文件速查

- MA 约束变更日志 [TOPIC6_CHANGELOG.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/TOPIC6_CHANGELOG.md) — 因方舟侧限制/机制导致的历次修改
- 主入口 [cli.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/arkagent/cli.py)
- 配置字段 [config.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/arkagent/config.py)
- topic6 运行器 [topic6_runner.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/arkagent/gateway/topic6_runner.py)
- HITL 卡片 [topic6_hitl.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/arkagent/gateway/topic6_hitl.py)
- 资源建 [create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh)
- Coordinator Prompt [coordinator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.system.md)
- 架构全景 HTML [topic6_ma_architecture.html](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/topic6_ma_architecture.html)
- Pipeline 对照 HTML [topic6_pipeline_overview.html](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/topic6_pipeline_overview.html)
