# MA 内置 bash 工具「抢戏」现象排查报告

> 面向对象：火山方舟 Managed Agents（MA）产品 / 工程团队
> 提交方：客户自研 Agent 轨迹 MA 重放对比评估项目组
> 日期：2026-09-10
> 一句话：**当 Agent 同时挂载内置工具集（含 `bash`）与业务工具（custom tool / MCP tool）时，
> doubao 模型有时会绕过已注入的业务工具，改用 `bash` 去沙箱里 `ls`/`find`/扫端口找"本地服务"，
> 导致该调的业务工具不调、耗时和 token 被浪费在无效探索上。** 本文给出可复现步骤、本轮实测证据、
> 历史更严重形态，以及给 MA 侧的排查建议。

---

## 一、现象定义

MA 的 Agent 可以同时挂三类工具（见官方 [Tools](https://docs.volcengine.com/docs/82379/2553719)）：

- 内置工具集 `agent_toolset_20260701`（含 `bash`/`read`/`glob`/`grep`/`write`/`edit`/`web_*`）；
- 业务工具：`custom`（业务侧回传结果）或 `mcp_toolset`（外部 MCP 服务）。

**期望行为**：模型需要业务数据时，直接发起已声明的业务工具调用（`agent.custom_tool_use`
或 MCP 工具调用）。

**观测到的异常行为（"bash 抢戏"）**：模型手里有 `bash` 时，面对"查数据/找服务"类任务，
会**先用 `bash` 去托管沙箱里翻找**——`ls` 插件目录、`find *mcp*`、`curl localhost:<port>`、
试探 `/rpc` `/sse` 等——把**本应由编排层注入的业务工具，误当成沙箱内的本地服务去找**。
轻则多几步无效探索、多烧 token；重则找不到、超时，甚至编造数据后谎称"服务不可用"。

> 关键点：业务工具（custom / MCP）由 MA **编排层**连接并作为函数注入给模型，**并不真实存在于沙箱文件系统里**。
> 模型用 `bash` 在沙箱里是永远找不到它们的——这正是"抢戏"无效且有害的根源。

---

## 二、本轮实测（2026-09-10，custom tool 形态）

### 复现条件

- 模型：`doubao-seed-2-1-pro-260628`
- Agent 工具：`agent_toolset_20260701`（含 bash）**＋** 一个业务 custom tool `api_call`（+ `current_time`）
- 场景：客服对某工单做"深度分诊"，需要通过 `api_call` 拉取该工单的业务数据
- mock：`api_call` 由客户端严格回放录制数据（命中返回真实数据，未命中返回结构化 `REPLAY_MISS`）

### 结果（单条 query）

| 指标 | 值 |
|---|---|
| 端到端耗时 | 149.0s |
| 模型请求数 | 14 |
| custom tool `api_call` 调用 | 5 次（命中 2 / 未命中 3） |
| **内置 bash 调用（被判定为抢戏）** | **2 次** |
| bash 抢戏检测 | 触发（`bash_snoop_detected=true`） |

### 捕获到的 bash 命令原文（证据）

本轮 2 条被判定为"抢戏"的 bash 命令是：

```
ls /mnt/skills/ticket_triage/
ls /mnt/skills/ticket_triage/references/
```

原始 artifact：[docs/evidence/custom-run-trajectory1.json](evidence/custom-run-trajectory1.json)
（见 `builtin_tool_calls` 里 `"name":"bash"` 且 `"snoop":true` 的两条）。

### 诚实结论（重要，避免误导）

**本轮观测到的是"抢戏"里相对温和的一种**：模型是用 `bash ls` 去**列已挂载 skill 的子文档目录**
（想知道 `ticket_triage` 下有哪些参考文档可读），**并不是**去扫 `localhost` 端口 / 试 `/rpc` `/sse`
找远程服务。而且模型**确实正常调用了业务工具 `api_call`（5 次）**，说明在 custom-tool 形态下，
业务工具基本没有被完全绕过。

因此对本轮而言，与其说是"抢戏"，不如说是"**内置 `bash` 与内置 `read`/`glob` 功能重叠，模型
偶尔用 `bash ls` 代替了 `glob`/`read` 去探路**"。它本身危害有限，但**暴露了同一个根因**：
模型倾向于用通用 `bash` 去"探路"，而不是优先使用更受约束的专用工具。在 MCP / 更强业务依赖的
场景下，这个倾向会放大成真正有害的抢戏（见第三节）。

> 说明：`bash_snoop_detected` 的判定口径偏宽——命令里只要包含 `ls`/`find`/`grep`/`localhost`/
> `127.0.0.1`/`curl`/`/rpc`/`/sse`/`plugins`/`8900`/`mcp` 任一关键词即标记。所以它会把"良性探路"
> 也标进来，**读结论时要结合命令原文判断严重程度**，不能只看布尔值。判定逻辑见
> [scripts/ma_runtime.py](../scripts/ma_runtime.py) 的 `BASH_SNOOP_HINTS` 与 `run_query()`。

---

## 三、历史观测到的更严重形态（MCP 形态，非本轮 artifact）

> 证据来源：本项目在**远程 MCP 形态**下早期实验的现场记录（非本次 custom-run artifact），
> 已做脱敏，一并提供给 MA 侧参考，因为它反映的是同一根因下更有害的表现。

在**远程 MCP** 形态下（业务工具经 `mcp_toolset` 注入）曾观测到：

- 模型面对"查这个用户/工单的业务数据"，**不直接调用已注入的 MCP function**，而是用 `bash` 在沙箱里
  `ls plugins`、`find *mcp*`、扫 `localhost:8900`、试 `/rpc` `/sse`，
  把**远程 MCP 工具误当成沙箱内的本地服务**去找；
- 找不到 → 超时 / 编造数据，甚至谎称 `allow_mcp_servers:false`（实际编排层鉴权与连接都正常）。
- 沙箱内 `SES_ADDR=:8900` 是平台预留端口，模型据此**误判本地有 MCP 在此监听**（实为空），
  这是它反复扫 8900 的诱因之一。

**对照实验的决定性结论**：把 `agent_toolset`（含 bash）全部关掉、只留业务工具后，
模型**立刻**直接发起业务工具调用、一次拿到真实数据。→ 证明"抢戏"的直接触发因素就是**手里有 bash**。

---

## 四、给 MA 侧的排查 / 改进建议

1. **确认 `SES_ADDR=:8900` 等平台预留信息是否会误导模型**：沙箱里暴露的这个端口是否会让模型
   推断"本地存在可连的服务"？建议评估是否需要在 system 层面明确告知模型"业务工具是注入函数、
   不在沙箱文件系统/本地端口上"。

2. **工具优先级 / 工具描述引导**：内置 `bash` 与业务工具、以及 `bash` 与 `read`/`glob` 存在能力重叠。
   建议在编排层或默认 system 提示里，引导模型"优先用专用工具（custom/MCP/read/glob），
   `bash` 仅用于确实需要执行命令的场景"，降低它拿 `bash` 去"探路"的倾向。

3. **提供官方的"最小工具集"最佳实践**：官方 Tools 文档已支持 `default_config.enabled=false`
   再按需开工具。建议补充一条针对"挂了业务工具就别乱给 bash"的最佳实践，帮助接入方规避此问题。

4. **可观测性**：建议 MA 事件流 / 控制台能直接标注"模型发起的内置工具调用命令原文"，
   方便接入方审计模型是否在做无效探索（我们目前是自己在客户端事件循环里抓 `agent.tool_use`
   的 command 字段才拿到命令原文）。

5. **对齐官方样板**：官方投研 Agent 教程（Wind MCP + static_bearer）是与我们场景一致的正样板，
   建议对照该样板确认"业务工具 + bash 共存"时的推荐配置，形成明确指引。

---

## 五、如何复现（供 MA 侧自查）

```bash
# 1. 造一个 Agent：同时挂 agent_toolset_20260701（含 bash）+ 一个业务 custom tool
# 2. 给它一个"必须查业务数据才能完成"的任务（业务数据只能通过 custom tool 拿到）
# 3. 看事件流里 agent.tool_use 是否出现 bash 去 ls/find/扫端口，而不是直接发 custom_tool_use
```

本项目的可复现装置（含严格回放、bash 命令原文捕获、抢戏判定）见
[ma-replica-builder](../SKILL.md) skill 的 `example-demo/scripts/run.py --mock custom`，
判定与采集逻辑在冻结引擎 [scripts/ma_runtime.py](../scripts/ma_runtime.py)。

---

## 附：本轮环境与口径

- 本轮为**离线严格回放**（业务数据来自录制轨迹，未命中返回 `REPLAY_MISS`，不喂假数据）。
- `custom_miss=3`：模型发起了 3 次录制里没有的 `api_call`（换了查询参数/接口），返回了结构化未命中——
  这与 bash 抢戏是两回事，属于"走出录制轨迹"，仅说明单条轨迹覆盖有限，不影响本报告结论。
- 自研侧 token/cache 不可恢复（客户导出只含 messages、无 usage），故本报告只聚焦 bash 抢戏行为本身，
  不做双边 token 对比。
