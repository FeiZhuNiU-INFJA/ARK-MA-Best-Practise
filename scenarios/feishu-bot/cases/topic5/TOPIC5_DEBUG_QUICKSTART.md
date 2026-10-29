# topic5 · 调试快速上手

按顺序过一遍就能起服务、在飞书里跑通 `复盘报告 full`。所有命令默认在仓库根目录 `ark-agent-feishu-bot/` 下执行，`cd` 位置在每步开头标注。

topic5 与 topic6 的关键差异：**走飞书文档交付，不走妙搭 Miaoda**。因此没有妙搭用户 OAuth / Device Flow / token keepalive / artifact-sync 这些步骤。

---

## 0. 前置

- 已装好 `conda` / `curl` / `jq` / `node`（echarts skill 真渲染在沙箱内需要 node_modules，由 Environment 的 setup_script 安装）。
- 方舟账号：已开通 Managed Agents，拿到 `ARK_API_KEY`。
- 业务侧凭证：DataHub、SSJH、报告封面服务（见 §2.2）。缺失不阻断资源装配，但运行到对应节点会 fail-closed。

### 0.1 Python 环境（首次）

Topic5 与 Topic6 共用 `topicX` Python 环境。在仓库根目录首次创建并安装项目及开发依赖：

```bash
conda create -n topicX python=3.11 -y
conda activate topicX
python -m pip install -e ".[dev]"
```

后续每次打开新终端，先执行：

```bash
conda activate topicX
```

### 0.2 ECharts 中文字体（首次或新机器）

两个 Source Han Sans SC OTF 文件体积较大，不进入 Git。执行 `update_ma.sh` 前必须把：

```text
SourceHanSansSC-Regular.otf
SourceHanSansSC-Bold.otf
```

放入：

```text
scenarios/feishu-bot/cases/topic5/ma-resources/skills/
blueai-yuqing-echarts-skill-new/assets/fonts/
```

文件来源、复制命令和当前验收 SHA-256 见
[字体目录说明](./ma-resources/skills/blueai-yuqing-echarts-skill-new/assets/fonts/README.md)。
`tools/pack_skills.sh` 会在打包前检查两个文件，缺失时直接停止，避免部署出中文缺字的图表 Skill。

---

## 1. 建飞书 Bot（首次）

```bash
cd scenarios/feishu-bot
python3 -m arkagent init --topic5
```

topic5 轻量初始化：只问方舟 API Key + 扫码建飞书应用，写入 `~/.arkagent/cases/topicX/config.env`。产出：

- `ARK_API_KEY`
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`

**不会**创建 digital-employee Agent。topic5 的 MA 资源统一由第 3 步的 `update_ma.sh` 创建和更新。已建过 Bot 就跳过这步。

> `init --topic5` 是通用 case 初始化的简写，等价于 `init --case topic5`（CLI 会按 `cases/` 下的目录名识别合法 case）。**topic5 与 topic6 共用同一个运行时目录 `~/.arkagent/cases/topicX/`**：二者本就共用同一个飞书 Bot 与方舟 Key，共享凭据只存一份，case 专属 ID 以 `TOPIC5_*` / `TOPIC6_*` 前缀在同一 `config.env` 里区分。所以如果已经 `init --topic6` 建过 Bot，topic5 直接复用该 `config.env`，这步可跳过。

业务凭据（DataHub / SSJH / 封面 / 交付 open_id）在 §2 继续追加到同一文件。`TOPIC5_COORDINATOR_AGENT_ID` / `TOPIC5_ENVIRONMENT_ID` / `TOPIC5_MEMORY_STORE_ID` 由第 3 步的 `update_ma.sh` 自动回写，不用手填。

---

## 1.1 飞书权限清单（首次）

`init --topic5` 只帮你把应用建起来，**不会**自动申请权限 scope 与事件订阅——这些需人工在飞书 [Open Platform 后台](https://open.feishu.cn/app) 里点。

### 权限 scope（权限管理 → API 权限）

**消息与卡片（必需）**

- [ ] `im:message` — 接收群/单聊消息事件
- [ ] `im:message:send_as_bot` — 以 Bot 身份回复文本
- [ ] `im:resource` — 上传/下载图片、文件

**飞书文档（L 阶段交付必需）**

- [ ] `docx:document` — 读写飞书文档正文（n20 导出草稿、回读核对）
- [ ] `drive:drive` — 上传图表 PNG 为文档 media（`medias/upload_all` 原生插图，n14→n20）
- [ ] `docs:permission.member:create` — 把报告发起人与管理员添加为协作者
- [ ] `docs:permission.member:transfer` — 将报告所有权转给任务发起人（交付三门禁之一）
- [ ] `docs:permission.member:retrieve` — 授权后查询协作者列表做结果复核

> topic5 交付走**导出 + 回读核对 + 记录 revision_id** 三门禁，禁用 `drive:import` MD 直导。
> 报告图表由 n14 本地渲染为 PNG，n20 交付时先建空 image block、再 `drive/v1/medias/upload_all`
> 上传本地 PNG 拿 `file_token`、最后 `PATCH replace_image` 回填——飞书 image block 不吃外链
> URL，直接上传飞书 media，需 `drive:drive` 权限。

**可选**

- [ ] `contact:user.base:readonly` — 反查 open_id 对应真名，用于 HC 卡片「XXX 已通过/驳回」回显操作人姓名（未开时回退为 open_id 短标识）

### 事件订阅（事件与回调 → 事件订阅）

- [ ] `im.message.receive_v1` — 用户在群/单聊里发消息（触发词入口）
- [ ] `card.action.trigger` — 人工关卡卡片按钮点击回调（HITL 必需）

### 生效方式

企业自建应用保存后即时生效；应用状态要点到「启用」，并在目标群里把 Bot 加为群成员。

---

## 2. 环境变量清单（跑 update_ma.sh 前必须齐）

**全部写到 `~/.arkagent/cases/topicX/config.env`**（该文件已在 §1 由 `init --topic5` 生成，追加即可，不会入库）。

### 2.1 通用（方舟 + 飞书，必需）

```env
# 方舟——控制台申请
ARK_API_KEY=xxx

# 飞书——init --topic5 已自动写入
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

`update_ma.sh` 会默认将 `FEISHU_APP_ID/FEISHU_APP_SECRET` 复用为沙箱使用的
`LARK_APP_ID/LARK_APP_SECRET`。只有 Gateway 与沙箱需要使用不同飞书应用时，才显式设置。

### 2.2 业务侧 API Key（向业务对接人获取；核心能力缺失会阻断）

```env
# DataHub——n09/n11 批量 AI 标注
DATAHUB_API_KEY=<业务对接人给的 Key>
DATAHUB_BASE_URL=https://...
DATAHUB_ANNOTATE_MODEL=gpt-4o-mini

# SSJH 社媒统计——n05/n06/n13 取数
SSJH_API_TOKEN=<业务对接人给的 Token>

# 报告封面服务（n14，可选；缺失则无封面）
REPORT_COVER_API_KEY=xxx
REPORT_COVER_API_BASE_URL=https://...

# Ark Chat/Embedding（语义 handler；ARK_* 可回退 OPENAI_*，两者都缺失则阻断）
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

> 图表（n14）本地渲染为 PNG，交付时经**飞书原生 media 上传**（`drive/v1/medias/upload_all`）进文档 image block，不需要外部对象存储配置。缺图表渲染器（node_modules 未装）时 n14 阻断，禁止无图交付。
>
> `demo` 仅预填 G1 表单。进入流水线后与 `full` 使用相同标准；缺核心凭证或出现 `degraded:true` 时对应节点阻断。

### 2.3 飞书交付（L 阶段）

```env
# n20 使用 Session 注入的 FEISHU_USER_OPEN_ID，把所有权转给任务发起人。
# 正常任务无需在全局配置中设置 owner open_id。
FEISHU_OPENAPI_BASE=https://open.feishu.cn
# 可选：企业自定义飞书域名；未配置时使用 https://www.feishu.cn
FEISHU_WEB_BASE=https://your-tenant.feishu.cn
```

### 2.4 白名单（建议设，防误触）

```env
AUTHORIZED_OPEN_IDS=ou_xxx ou_yyy   # 空格或逗号分隔
```

### 手工执行子脚本时加载到 shell

```bash
set -a; source ~/.arkagent/cases/topicX/config.env; set +a
```

正常使用 `update_ma.sh` 时不需要，脚本会自动读取。只有绕过统一入口单独调试
`tools/pack_skills.sh`、`tools/upload_skills.py` 或 `ma-resources/create_all.sh` 时才需手工加载。

---

## 3. 更新资源并启动 Gateway

首次部署和后续更新都使用同一个入口。在 `scenarios/feishu-bot/cases/topic5` 目录执行：

只验证本地测试隔离、不打包或上传资源：

```bash
TOPIC5_TEST_ONLY=1 ./update_ma.sh
```

该模式仍会加载生产配置以复现真实启动环境，但测试子进程会移除外部服务凭据，强制走
mock/确定性降级；测试通过后立即退出。

完整更新：

```bash
./update_ma.sh
cd ../.. && python -m arkagent run --case topicx
```

`update_ma.sh` 已包含完整测试、Skill 打包上传、Environment 与 Memory 更新、Agent
重建以及 Gateway 配置回写（`TOPIC5_COORDINATOR_AGENT_ID` / `TOPIC5_ENVIRONMENT_ID` /
`TOPIC5_MEMORY_STORE_ID` 回写到 `config.env`）。

注意：

- Skill 和 Prompt 不会热更新到已有 Session；Gateway 启动后需从飞书新建任务验证。
- Environment / Memory Store 按名称原地更新，ID 不变；Skills 和 2 个 Agent 重建，ID 会变。
- 单个 Gateway 可并发维护多个 Topic5 Job；隔离键是飞书话题。同一话题最多一个活跃 Job，不同话题互不阻塞。这里与“同一飞书 App 只能启动一个 Gateway 进程”是两层不同约束。
- Topic5 话题是共享协作边界：同一话题内通过系统全局白名单的成员均可补充对话、上传活动明细、提交备注、操作审核卡片、停止或重试任务。任务发起人的 `open_id` 仍作为审计身份和最终飞书文档所有权目标；Topic6 的个人 OAuth 与发起人权限不随此规则放开。

---

## 4. 冒烟测试

在飞书 Bot 私聊或群里发送：

```
复盘报告 full
```

清洗后稳定抽样最多 500 条并继续生成演示报告时发送：

```text
复盘报告 demo
```

正常应该看到：

1. Bot 基于触发消息创建话题，并把进度卡放进该话题；
2. Gateway 日志滚动 SSE `agent.message.delta`；
3. G1 卡片展示结构化需求问卷，提交后进入方案编制；
4. 通过 **G2（方案确认）** 后，在当前话题发送活动明细 `.xlsx`；
5. Excel 通过飞书资源下载 → Ark Files API → Session `/mnt/session/uploads/`，挂载成功后自动续跑；n07 读取该文件，SSJH 仍负责 n05/n06/n13；
6. 后续固定弹出 **G4（用户终验）**；N10 仅在观点或 UGC 标签有歧义时弹出；
7. 点击按钮 → Coordinator `resume` → 编排器续跑下一阶段；
8. G3 由独立 `topic5-validator` 子 Agent 按客户完整规则适配表自动执行（脚本全量扫描 +
   Agent 全文语义检查），`p0_count==0` 自动放行，有 P0 自动返修，不发人工审核卡；
9. 最终产出飞书文档链接（经转移所有权 + 回读核对 + revision_id 记录三门禁）。

### HC 决策消息格式（严格）

卡片按钮回调注入 Session 的决定消息必须是：

```text
HCx pass
HCx reject: <note>
HCx remark: <note>
```

其中 `HCx` 是关卡种类（`G1`/`G2`/`N10`/`G3`/`G4`/`HC7`/`USER_ESCALATE`）。**备注（remark）无内容时只记录 remark，不触发 resume_job**，需等用户后续消息指令。
G1 是特殊结构化消息：Gateway 将需求问卷写入 Session 挂载文件，并以
`G1 pass: @/mnt/session/uploads/topic5_requirement_*.json` 注入。Coordinator 必须把文件引用
原样传给 `run_pipeline.py resume --note`，不得读取、摘要或改写。

---

## 5. 生产可用验收清单

逐项核对，确保不是「只跑通 demo」而是真正可上生产：

| 维度       | 验收点                                                                                  |
| ---------- | --------------------------------------------------------------------------------------- |
| 幂等       | 同一节点重复执行被 ledger 拦截，副作用不重放（render→ledger 重放/对账）                 |
| 去重与抽样 | 清洗阶段按 `fingerprint → id → item_id → url` 去重；剔除比例 >10% 触发 `needs_sampling_review`；demo 对清洗后活动明细稳定抽样最多 500 条 |
| 中断恢复   | 冷进程重启后 `resume` 从 checkpoint 续跑，不重跑已完成节点；retry 前先 reconcile       |
| 取消       | Gateway 异常退出后清理本地 `pipeline_jobs` 与 HC 记录，不影响远端 Session               |
| 落点对齐   | 运行期产物落在沙箱工作区，稳定产物对齐约定目录；版本链一致                               |
| 版本链     | Artifact / Validation / Delivery / Handoff 指向**同一 revision_id**（非 latest）         |
| 人工门     | 固定 G1/G2/G4；N10 仅歧义时触发；HC7/USER_ESCALATE 负责异常升级；G3 为 Validator 自动门 |
| 权限/费用  | 高风险写入、外部动作、费用可追踪；凭证不入库、不进日志                                   |
| Trace      | step-level token 用量订阅 Session Events SSE 聚合 `span.model_request_end.model_usage`  |

---

## 6. 常见故障排查

| 现象                          | 排查方向                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------ |
| 日志「topic5 场景：未启用」    | 检查 `TOPIC5_COORDINATOR_AGENT_ID` 是否已写入 config.env、拼写是否正确          |
| Skill 找不到                  | 重跑 `./update_ma.sh`                                                           |
| HC 卡片点击后无响应           | Feishu Bot 后台「事件订阅」里是否开启 `card.action.trigger` 权限                |
| SSE 中断/超时                 | 单会话默认超时，超长任务加大 `SESSION_TIMEOUT_MS`（毫秒）                       |
| n14 图表渲染失败              | 检查 `REPORT_COVER_API_KEY`；沙箱 node_modules 是否安装成功（看 setup_script 日志）|
| n09/n11 标注走降级            | 检查 `DATAHUB_API_KEY` / `DATAHUB_BASE_URL`；行数 <1000 会走小引擎 semantic     |
| 飞书交付失败                  | 检查 `docs:permission.member:transfer` 权限与 Session 是否注入 `FEISHU_USER_OPEN_ID` |
| 报告缺指标/口径不符           | 对照 README「口径红线」；缺 AI 情感会直接报错（设计如此，不兜底）               |

---

## 附：相关文件速查

- 确定性编排器入口 [run_pipeline.py](./ma-resources/skills/pipeline-orchestrator/run_pipeline.py)
- DAG 契约 [pipeline.v0.2.yaml](./ma-resources/io/contracts/pipeline.v0.2.yaml)
- topic5 运行器 [topic5_runner.py](./topic5_runner.py)
- HITL 卡片 [topic5_hitl.py](./topic5_hitl.py)
- 状态持久化 [pipeline_store.py](./pipeline_store.py)
- 配置字段 [config.py](./config.py)
- 统一更新入口 [update_ma.sh](./update_ma.sh)
- Coordinator Prompt [coordinator.system.md](./ma-resources/agents/coordinator.system.md)
- Validator Prompt [validator.system.md](./ma-resources/agents/validator.system.md)
- 架构全景 HTML [topic5_ma_architecture.html](./topic5_ma_architecture.html)
