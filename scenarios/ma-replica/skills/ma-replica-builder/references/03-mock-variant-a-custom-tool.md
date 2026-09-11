# 03 · 变体 A：custom tool 动态回放（更高保真时选用）

> 默认走变体 B（`04-mock-variant-b-files.md`）。**只有当用户明确要"高保真性能对比 / 让模型自己
> 决策何时调工具"时才选变体 A。** 读到本 skill 时若用户没指明，先默认 B 并主动问一句要不要 A。

## 思路

保持"工具面"忠实于自研 Agent：凡是轨迹里出现过的业务工具（本例 `api_call`、`current_time`），
在 MA 上都声明成 **custom tool**（`type:"custom"` + `name` + `description` + `input_schema`）。
模型照常发起工具调用 → MA 通过事件流把调用推给客户端（`agent.custom_tool_use`）→ 客户端用
**录制数据严格回放**后，通过 `user.custom_tool_result` 回传。全程走事件流，**无需公网隧道**，
但**客户端要全程在线**接管工具执行。

这是**性能对比的首选**，因为它最接近自研 Agent 让模型自己决策"何时调哪个工具"的真实链路。

## 严格回放（strict replay）

- 命中录制键 → 返回原始数据。
- 未命中 → 返回结构化 `[tool_error] REPLAY_MISS`，让模型**如实感知"走出了录制轨迹"**，
  绝不喂假数据（喂假数据会污染对比）。
- 回放键归一化在客户样板 `example-demo/scripts/replay_lib.py` 的 `api_call_key()`：把"决定返回哪行
  数据"的参数纳入键，忽略分页/排序/列顺序等不影响命中的字段。**换客户改这里**（客户特有）。

## 冻结引擎 + 客户工具面（分层）

事件循环、指标累计、资源清理都在**冻结层** `scripts/ma_runtime.py`，不随客户变。变体 A 客户侧
只需在 `example-demo/scripts/run.py` 提供两小块：

- `build_custom_tools()`：每个业务工具一个 `type:"custom"` 声明（description / input_schema 客户特有）。
- `make_resolve()`：`resolve(name, args) -> (输出文本, 是否命中)`，路由到录制数据严格回放。

然后调 `ma_runtime.run_session(..., custom_tools=..., resolve=...)` 即可。

## 故意挂 agent_toolset（本轮实验意图）

变体 A 的 agent 配置**同时挂上内置工具集 `agent_toolset_20260701`（含 bash/read/glob/grep）**。
这是为了**实测**一个历史问题：模型手里有 bash 时，会不会"抢戏"——绕过已注入的 custom tool，
跑去沙箱里 `ls/find/扫端口`找工具，而不直接调用 `api_call`。

判断标准（`ma_runtime.run_query` 已自动记录）：

- **抢戏信号**：`agent.tool_use` 里出现 `bash` 且命令含 `ls/find/grep/localhost/127.0.0.1/curl//rpc//sse/plugins/8900/mcp`
  等关键词；或模型迟迟不发 `api_call` 的 `custom_tool_use`。→ `bash_snoop_detected=true`，记入 PROBLEMS。
- **正常**：模型直接发 `api_call` 的 `custom_tool_use`，bash 只用于正当用途。→ 说明 custom-tool 形态下问题不复现（有价值的新结论）。

## 运行

```bash
export ARK_API_KEY=...
cd scenarios/ma-replica/skills/ma-replica-builder
python example-demo/scripts/run.py --case-dir ../../ma-cases/demo --mock custom --model doubao-seed-evolving
# 默认只跑第一条 query；--all 跑全部；--keep 保留 agent/env/session 不删
```

事件循环（冻结层）处理的关键事件：
- `agent.custom_tool_use` → 严格回放 → `user.custom_tool_result`
- `agent.tool_use`（内置）→ 记录（判断 bash 抢戏）
- `span.model_request_end` → 累计 `model_usage`
- `session.status_idle` / `status_terminated` / `session.error` → 收尾

结果落 `ma_runs/<轨迹名>.json`（耗时、逐 step usage、内置工具序列、custom 未命中、最终消息）。
