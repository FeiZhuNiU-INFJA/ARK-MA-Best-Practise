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

topic6 专用轻量初始化:只问方舟 API Key + 扫码建飞书应用,写入 `~/.arkagent/config.env`。产出:

- `ARK_API_KEY`
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`

**不会**创建 digital-employee Agent,也不要求 mock 客户A MCP 地址——那些是另一场景的东西。
topic6 的 MA 资源在第 2 步用 `create_all.sh` 单独建。

> 注意:不要跑不带参数的 `arkagent init`,那是 digital-employee 场景专用,会强制要求输入 mock 客户A MCP 公网地址。

已建过 Bot 就跳过这步。

---

## 2. 建 topic6 MA 资源(首次)

```bash
cd scenarios/feishu-bot/cases/topic6

export ARK_API_KEY="xxx"
export HOT_TOPICS_MCP_URL="https://xxx/mcp"

# 2.1 打包 5 个 Skill zip → tools/out/*.zip
./tools/pack_skills.sh

# 2.2 上传 Skill → 写回 ma-resources/skill_ids.json
python3 tools/upload_skills.py

# 2.3 建 Environment / Memory Store / Annotator / Insighter / Coordinator
./ma-resources/create_all.sh
```

`create_all.sh` 会打印:

- `ENVIRONMENT_ID`
- `MEMORY_STORE_ID`
- `ANNOTATOR_AGENT_ID` / `INSIGHTER_AGENT_ID`
- `COORDINATOR_AGENT_ID` ← 后面要写进 config.env

后续只是改 Prompt / Skill,跑 `./ma-resources/create_all.sh --update-agent` 就地更新,不必再全量创建。

---

## 3. 补 topic6 环境变量

编辑 `~/.arkagent/config.env`,追加:

```env
TOPIC6_COORDINATOR_AGENT_ID="<create_all.sh 输出>"
TOPIC6_ENVIRONMENT_ID="<create_all.sh 输出,或省略回退 ARK_ENVIRONMENT_ID>"
TOPIC6_MEMORY_STORE_ID="<create_all.sh 输出>"
TOPIC6_PIPELINE_DB_PATH="./data/topic6_pipeline.db"
HOT_TOPICS_MCP_URL="https://xxx/mcp"
AUTHORIZED_OPEN_IDS="ou_xxx ou_yyy"
```

`TOPIC6_COORDINATOR_AGENT_ID` 是**开关**——不填则 topic6 场景不装配,Bot 只跑 digital-employee。

---

## 4. 起服务

```bash
cd scenarios/feishu-bot
python3 -m arkagent run
```

启动日志出现下面这行才算 topic6 装配成功:

```
- topic6 场景：已启用(coordinator=xxx, env=xxx)
topic6 触发词：热点报告 / 热点周报(可加 test/full 指定模式)
```

想拉更详细日志:`ARKAGENT_LOG_LEVEL=DEBUG python3 -m arkagent run`。

---

## 5. 冒烟测试

在飞书 Bot 私聊或群里发送:

```
热点周报 test
```

正常应该看到:

1. Bot 回复"已收到,启动 topic6 pipeline...";
2. Gateway 日志滚动 SSE `agent.message.delta`;
3. 依次弹出 **HC1 (蓝)** / **HC2 (绿)** / **HC3 (紫)** 三张交互卡片,每张都有「通过 / 打回 / 备注」三按钮;
4. 点击按钮 → Coordinator resume → 继续下一 Phase;
5. 最终产出飞书文档链接。

---

## 6. 常见故障排查

| 现象                              | 排查方向                                                                                                                    |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 日志"topic6 场景:未启用"          | 检查`TOPIC6_COORDINATOR_AGENT_ID` 是否已写入 config.env、拼写是否正确                                                     |
| Coordinator 拉不到 hot-topics MCP | `HOT_TOPICS_MCP_URL` 未 export 就跑了 `create_all.sh`,MCP URL 被空值渲染进 Agent 定义;重新 export 后 `--update-agent` |
| Skill 找不到                      | `skill_ids.json` 有 null 项,重跑 `upload_skills.py`                                                                     |
| HC 卡片点击后无响应               | Feishu Bot 后台"事件订阅"里是否开启`card.action.trigger` 权限                                                             |
| SSE 中断/超时                     | 单会话默认 10 分钟,超长任务加大`SESSION_TIMEOUT_MS`(毫秒)                                                                 |
| 图片抓取失败                      | 已知风险点,飞书`im.v1.images.get` 并发大图不稳定,重跑一次 Phase G 即可                                                    |

---

## 附:相关文件速查

- 主入口 [cli.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/arkagent/cli.py)
- 配置字段 [config.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/arkagent/config.py)
- topic6 运行器 [topic6_runner.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/arkagent/gateway/topic6_runner.py)
- HITL 卡片 [topic6_hitl.py](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/arkagent/gateway/topic6_hitl.py)
- 资源建 [create_all.sh](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/create_all.sh)
- Coordinator Prompt [coordinator.system.md](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/ma-resources/agents/coordinator.system.md)
- 架构全景 HTML [topic6_ma_architecture.html](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/topic6_ma_architecture.html)
- Pipeline 对照 HTML [topic6_pipeline_overview.html](file:///Users/bytedance/workspace/ark-agent-feishu-bot/scenarios/feishu-bot/cases/topic6/topic6_pipeline_overview.html)
