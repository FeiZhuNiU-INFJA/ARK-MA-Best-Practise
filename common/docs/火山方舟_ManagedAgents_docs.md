# 火山方舟 · Managed Agents 文档合集

> 来源：`docs.volcengine.com/docs/82379/{2553713..2553731}?lang=zh`
> 由页面自带的「复制 Markdown」原文拼接而成，未做二次改写。

## 目录

- [概述](#doc-2553713) · `2553713`
- [快速入门（代码）](#doc-2553714) · `2553714`
- [快速入门（控制台）](#doc-2553715) · `2553715`
- [Agent](#doc-2553716) · `2553716`
- [Skills](#doc-2553717) · `2553717`
- [MCP](#doc-2553718) · `2553718`
- [Tools](#doc-2553719) · `2553719`
- [工具权限策略](#doc-2553720) · `2553720`
- [配置云环境](#doc-2553721) · `2553721`
- [云沙箱参考](#doc-2553722) · `2553722`
- [启动 Session](#doc-2553723) · `2553723`
- [管理 Session](#doc-2553724) · `2553724`
- [Session 事件流](#doc-2553725) · `2553725`
- [使用 Vaults 认证](#doc-2553726) · `2553726`
- [上传与挂载文件](#doc-2553727) · `2553727`
- [持久化记忆](#doc-2553728) · `2553728`
- [Advisor](#doc-2553729) · `2553729`
- [编排 Multi Agent](#doc-2553730) · `2553730`
- [定义 Outcome](#doc-2553731) · `2553731`

<a id="doc-2553713"></a>

---

## 概述

> 来源：[https://docs.volcengine.com/docs/82379/2553713?lang=zh](https://docs.volcengine.com/docs/82379/2553713?lang=zh)

方舟 Managed Agents 是火山方舟提供的 **预构建、可配置的智能体框架**，运行在方舟托管的基础设施之上，最适合承载 **长时间运行、多轮工具调用、有状态的异步任务**。

基于模型 API 构建一个可用的 Agent，需要自行实现 AgentLoop、工具调用、上下文管理、沙箱调度、断点续跑与权限隔离等通用组件。建设周期通常需要 4 至 8 周，且需随模型迭代持续适配。方舟 Managed Agents 提供 Agent 层的完整基础设施，你只需定义 Agent 行为、发送用户事件并订阅结果流。

> 方舟目前提供两种基于豆包大模型的构建方式：需对 Agent 循环进行细粒度控制时，选用 **模型 API**；需将完整 Agent 交由平台托管、聚焦业务逻辑时，选用 **Managed Agents**。两者的详细差异参见下文 [与模型 API 对比](https://ark.volcengine.com/region:cn-beijing/docs/overview#vs_model_api)。

[快速入门](https://ark.volcengine.com/region:cn-beijing/docs/quick-start-code)

4 步跑通你的第一个方舟托管 Agent：创建 Agent、创建环境、开启会话、发送消息并接收流式响应。

[启动 Session](https://ark.volcengine.com/region:cn-beijing/docs/start-session)

基于 Agent 与 Environment 创建 Session，发送用户事件，让 Agent 开始执行任务。

## 基本概念

方舟 Managed Agents 围绕以下四个概念构建：

| 概念            | 描述                                                                                                                                                            |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Agent**       | 可版本化、可复用的智能体配置资产，定义了一个智能体的角色、能力、行为边界和运行规则，是所有任务运行的模板和基准。创建一次，通过 `agent_id` 在多个 Session 中复用 |
| **Environment** | Session 运行的环境配置，包含网络、预装依赖包、环境变量与产物存储。通过 `environment_id` 引用                                                                    |
| **Session**     | 在 Environment 中运行的 Agent 实例，用于执行一次具体任务。上下文、文件、状态相互隔离，同一 Agent 可发起多个并行 Session                                         |
| **Events**      | 应用与 Agent 之间交换的原子消息，涵盖用户消息、思考、工具调用、工具结果与状态更新。以只增不删的方式在服务端持久化，支持实时订阅或事后回放                       |

## 核心优势

### 与模型 API 对比

**模型 API** 是访问模型能力的最原子接口：需自行实现循环、解析工具调用、维护上下文，适用于需要 **细粒度控制** 的场景。**方舟 Managed Agents** 则是访问 Agent 能力的封装接口：将 Agent 层原本需自建的通用能力统一收敛至平台内部。

#### 不再需要自行实现的部分

| 之前（模型 API + 自建循环）                                                       | 之后（方舟 Managed Agents）                                                   |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 维护对话历史数组，每一轮将完整历史传回模型                                        | Session 由服务端持久化历史，仅需发送 / 订阅事件                               |
| 手动循环调用：模型返回后解析 `tool_use`，执行工具并追加 `tool_result`，再调用模型 | 平台内置 AgentLoop 自动驱动整个循环；Agent 结束时发出 `session.status_idle`   |
| 自建沙箱以执行 Agent 生成的代码，机器、容器、扩缩容与清理均需自行管理             | Session 自带沙箱，代码执行、文件读写、Bash 命令由平台代为承载，任务结束即释放 |
| 自研上下文压缩策略以避免超出上下文窗口                                            | 上下文自动压缩，Event API 全量留存，不丢失内容                                |
| 自研长程任务的状态保存与恢复机制                                                  | 自动保存沙箱状态，长程任务原生支持断点续跑                                    |
| 自研 IAM、密钥注入与执行环境隔离                                                  | 基于火山 IAM + 零信任凭证隔离，密钥不会进入沙箱进程                           |

#### 仍由你掌控的部分

- **模型与系统提示词**：字段保持不变，仅位置调整至 Agent 定义中。

- **自定义工具**：仍以 JSON Schema 声明 `input_schema`；执行方式调整为：由用户客户端订阅 `agent.custom_tool_use` 事件后自行执行。

- **上下文注入**：仍可通过系统提示词、Skills、上传文件等方式注入。

- **中断与引导**：可随时向 Session 发送额外用户事件以调整方向，或直接中断本轮执行。

#### 何时仍应选择模型 API

若目标 **恰在于自定义 Agent 循环本身**：例如探索新的推理策略、构建自研 Agent 框架、或需对每一步 `tool_use` 进行特殊改写：则模型 API 更为合适。Managed Agents 面向的是「直接获取可运行的 Agent」，而非「构建 Agent 引擎」。

### 与开源 Agent 对比

主流开源 Agent Runtime（LangChain、OpenCode 等）解决了「能够运行」的问题，但要实现「稳定、高效、具备业务价值」的运行仍需大量投入。方舟并非「在开源框架之上叠加一层豆包适配」，而是 **自底向上自研 AgentLoop 调度内核，并与豆包大模型的原生能力进行深度双向优化**：相当于为模型配备一套量身定制的操作系统，而非在通用硬件上粗放运行。

| 对比维度         | 方舟 Managed Agents（原生 AgentLoop）        | 开源 Agent Runtime                           |
| ---------------- | -------------------------------------------- | -------------------------------------------- |
| **模型适配方式** | 与豆包全系列原生双向优化                     | 通用 prompt 模板适配所有模型                 |
| **推理完成率**   | 思维链原生拆解、断点续跑、上下文自动压缩     | 长任务易偏离预期、中断后无法续跑、上下文丢失 |
| **工具调用**     | 协议级对齐，自动校验 / 重试 / 并行           | 通用 JSON 解析                               |
| **长程任务**     | 原生支持长程任务运行，自动生成快照、按需拉起 | 固定模板适配所有场景，需自建沙箱调度         |
| **接入成本**     | 开箱即用，零适配成本                         | 需 4–8 周自研适配，踩坑周期较长              |

## 适用场景

方舟 Managed Agents 最适合具备以下特征的工作负载：

- **长时间运行**：单次任务需运行数分钟至数小时，涉及多次工具调用与迭代思考。

- **有状态**：跨多轮交互持久保存文件系统、对话历史与执行状态，支持断点续跑。

- **异步执行**：任务发起后由 Agent 自主推进，客户端仅需订阅事件流即可获取结果。

- **最少的基础设施**：无需自建 Agent 循环、沙箱与工具执行层。

- **弹性调用**：调用量波峰波谷显著，期望按用量计费而非常驻资源。

在上述特征之上，以下为已验证 PMF 的典型业务场景：

| 场景                   | 重复度 | 风险 | 说明                                                                       |
| ---------------------- | ------ | ---- | -------------------------------------------------------------------------- |
| **IT 工单处理**        | 高     | 低   | 结构化强、模板化程度高，Agent 处理准确率高                                 |
| **报表自动生成**       | 高     | 低   | 数据抓取与汇总逻辑固定，易于 Agent 化                                      |
| **供应商邮件处理**     | 中     | 低   | 分类与回复流程明确，适合规则化自动化                                       |
| **代码 Review 与文档** | 高     | 低   | 研发团队收益直观，ROI 显著                                                 |
| **销售线索初筛**       | 中     | 低   | CRM 录入自动化，释放销售人力                                               |
| **视觉内容创作**       | 中     | 中   | 组合视觉搜索、图片生成、视频生成和视频合成工具，适合广告素材与短视频生产。 |

正在使用开源 Agent 自建业务、或通过脚本拼接 Agent 能力的团队，均可考虑迁移至方舟 Managed Agents 进行试点验证。

## 工作流程

方舟 Managed Agents 从创建到接入业务，通常经历以下 5 个步骤：

1. **创建 Agent**：在控制台或通过 API 定义模型、系统提示词与 Skills / Tools / MCPs，获得稳定的 `agent_id`，可在多个 Session 中通过 ID 复用。

2. **创建 Environment**：定义沙箱运行环境（网络、预装包、环境变量和产物存储），获得稳定的 `environment_id`。

3. **启动 Session**：基于 `agent_id` 与 `environment_id` 创建一次会话（Session）。

4. **发送事件并流式接收响应**：将用户消息作为事件发送至 Session，Agent 自主执行工具并通过 SSE（server\-sent events）流式返回结果，事件历史由服务端持久化保存，支持完整获取。

5. **引导或中断**：Agent 执行过程中，可随时发送额外用户事件以引导其调整方向，或直接中断本轮执行。

上手方式提供以下两种路径：

- 如需以 **可视化方式** 快速完成原型验证，参见 [快速入门（控制台）](https://ark.volcengine.com/region:cn-beijing/docs/quick-start-console)。

- 如需直接以 **代码 / API** 完成端到端接入，参见 [快速入门（代码）](https://ark.volcengine.com/region:cn-beijing/docs/quick-start-code)。

## 计费说明

Managed Agents 按照 Agent 运行过程中实际消耗的 Tokens、Agent 运行时时长和工具调用次数三项累加计费。详情请参见 [Managed Agents 计费说明](https://ark.volcengine.com/region:cn-beijing/docs/model-pricing#ma_billing)。

<a id="doc-2553714"></a>

---

## 快速入门（代码）

> 来源：[https://docs.volcengine.com/docs/82379/2553714?lang=zh](https://docs.volcengine.com/docs/82379/2553714?lang=zh)

4 步跑通你的第一个方舟托管 Agent：创建 Agent、创建环境、开启会话、发送消息并接收流式响应。

## 准备工作

### 1. 获取并配置 API Key

1. **获取 API Key**：访问 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)，创建你的 API Key。

2. **配置环境变量**：在终端中运行下面命令（替换 `your_api_key_here` 为你的方舟 API Key），配置 API Key 到环境变量。

配置持久化环境变量方法参见 [环境变量配置指南](https://ark.volcengine.com/region:cn-beijing/docs/environment-variable-configuration-guide)。

**macOS**

```Bash
export ARK_API_KEY="your_api_key_here"
```

**Linux**

```Bash
export ARK_API_KEY="your_api_key_here"
```

**Windows_CMD**

```Bash
setx ARK_API_KEY "your_api_key_here"
```

**Windows_PowerShell**

```PowerShell
$env:ARK_API_KEY = "your_api_key_here"
```

### 2. 开通 Managed Agents 服务

访问 [Managed Agents 开通页面](https://ark.volcengine.com/region:cn-beijing/openManagement?advancedActiveKey=manageAgent) 开通服务。

### 3. 开通模型服务

访问 [开通管理页面](https://ark.volcengine.com/region:cn-beijing/openManagement) 开通模型服务。

## 1. 创建 Agent

创建一个Agent，定义其模型、系统提示和可用工具。

```Bash
agent=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/agents" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<'EOF'
{
  "name": "Quick Start Agent",
  "model": {"id": "doubao-seed-2-1-pro-260628"},
  "system": "你是一个高效的编程助手，擅长代码编写和问题排查。",
  "tools": [
    {"type": "agent_toolset_20260916"}
  ]
}
EOF
)

AGENT_ID=$(jq -er '.id' <<<"$agent")

echo "Agent ID: $AGENT_ID"
```

## 2. 创建环境

Environment 定义 Agent 执行代码和调用工具时使用的沙箱环境，可配置预装依赖、环境变量和产物存储。创建 Session 时，通过 `environment_id` 引用 Environment。

本示例使用 `config.type=cloud` 的云托管环境，由方舟按需启动和自动休眠沙箱，无需自行维护主机。

> `name` 在当前项目内必须唯一；与已有环境重名时，创建请求会失败。

```Bash
environment=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/environments" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<'EOF'
{
  "name": "demo-env",
  "config": {
    "type": "cloud",
    "networking": {"type": "unrestricted"}
  }
}
EOF
)

ENVIRONMENT_ID=$(jq -er '.id' <<<"$environment")

echo "Environment ID: $ENVIRONMENT_ID"
```

## 3. 开启会话

创建一个引用您的Agent和环境的会话。

```Bash
session=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": "$AGENT_ID",
  "environment_id": "$ENVIRONMENT_ID",
  "title": "Quickstart session"
}
EOF
)

SESSION_ID=$(jq -er '.id' <<<"$session")

echo "Session ID: $SESSION_ID"
```

## 4. 发送消息并流式接收响应

获取 `session_id` 后，先通过 `SessionEvents_streamEvents` 建立 SSE 连接并等待服务端返回 `: ready`，再通过 `SessionEvents_send` 发送 `user.message` 事件。客户端通过已建立的连接接收 Agent 状态、工具调用和响应内容。

> **注意**

> SSE 只推送连接建立后产生的事件，不回放历史事件。如果先发送消息再建立连接，客户端会漏掉连接建立前已产生的事件。

```Bash
# Open the SSE stream and wait for the ready comment before sending the message
STREAM_DIR=$(mktemp -d)
STREAM_PIPE="$STREAM_DIR/events"
mkfifo "$STREAM_PIPE"

cleanup_stream() {
  exec 3<&- || true
  if kill -0 "$STREAM_PID" 2>/dev/null; then
    kill "$STREAM_PID" 2>/dev/null || true
  fi
  wait "$STREAM_PID" 2>/dev/null || true
  rm -f "$STREAM_PIPE"
  rmdir "$STREAM_DIR" 2>/dev/null || true
}

curl -sS -N --fail-with-body \
  "https://ark.cn-beijing.volces.com/api/v3/sessions/$SESSION_ID/events/stream" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Accept: text/event-stream" >"$STREAM_PIPE" &
STREAM_PID=$!
exec 3<"$STREAM_PIPE"
trap cleanup_stream EXIT

stream_ready=false
while IFS= read -r line <&3; do
  if [[ $line == ": ready" ]]; then
    stream_ready=true
    break
  fi
done
if [[ $stream_ready != true ]]; then
  printf 'SSE stream closed before it was ready.\n' >&2
  exit 1
fi

# Send the user message after the stream is ready
curl -sS --fail-with-body \
  "https://ark.cn-beijing.volces.com/api/v3/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d @- >/dev/null <<'EOF'
{
  "events": [
    {
      "type": "user.message",
      "content": [
        {"type": "text", "text": "用 Python 编写一个脚本，生成前 20 个斐波那契数，并将其保存到 fibonacci.txt。"}
      ]
    }
  ]
}
EOF

# Process events from the established stream
stream_finished=false
while IFS= read -r line <&3; do
  [[ $line == data:* ]] || continue
  json=${line#data: }
  case $(jq -r '.type' <<<"$json") in
    agent.message)
      jq -j '.content[] | select(.type == "text") | .text' <<<"$json"
      ;;
    agent.tool_use)
      printf '\n[Using tool: %s]\n' "$(jq -r '.name' <<<"$json")"
      ;;
    session.status_idle)
      printf '\n\nAgent finished.\n'
      stream_finished=true
      break
      ;;
  esac
done

if [[ $stream_finished != true ]]; then
  printf 'SSE stream closed before the Agent finished.\n' >&2
  exit 1
fi

cleanup_stream
trap - EXIT
```

**示例输出：**

```Plain Text
[Using tool: write]

[Using tool: bash]

[Using tool: read]
已完成任务：
1. 编写了 Python 脚本 /workspace/generate_fibonacci.py，用于生成斐波那契数列
2. 成功运行脚本，生成的前 20 个斐波那契数为：[0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597, 2584, 4181]
3. 结果已保存到 /workspace/fibonacci.txt 文件中，文件内按序号清晰列出了每一个斐波那契数，内容验证正确。

Agent finished.
```

## 完整脚本

将上面 4 步合并为一个可直接运行的脚本，开头加 `set -euo pipefail`，任一步失败立即终止。

**用法：**

1. 将下面代码保存为 `quickstart.sh`。

2. 确保已按「准备工作」配置好 `ARK_API_KEY` 环境变量，并已安装 `curl` 与 `jq`。

3. **执行**：

   ```Bash
   bash quickstart.sh
   ```

```Bash
#!/usr/bin/env bash
set -euo pipefail

export ARK_API_KEY="${ARK_API_KEY:?请先 export ARK_API_KEY}"
ARK_BASE_URL="https://ark.cn-beijing.volces.com"

# Step 1: Create Agent
agent=$(
  curl -sS --fail-with-body "$ARK_BASE_URL/api/v3/agents" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<'EOF'
{
  "name": "Quick Start Agent",
  "model": {"id": "doubao-seed-2-1-pro-260628"},
  "system": "你是一个高效的编程助手，擅长代码编写和问题排查。",
  "tools": [
    {"type": "agent_toolset_20260916"}
  ]
}
EOF
)
AGENT_ID=$(jq -er '.id' <<<"$agent")
echo "Agent ID: $AGENT_ID"

# Step 2: Create Environment
environment=$(
  curl -sS --fail-with-body "$ARK_BASE_URL/api/v3/environments" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<'EOF'
{
  "name": "demo-env",
  "config": {
    "type": "cloud",
    "networking": {"type": "unrestricted"}
  }
}
EOF
)
ENVIRONMENT_ID=$(jq -er '.id' <<<"$environment")
echo "Environment ID: $ENVIRONMENT_ID"

# Step 3: Create Session
session=$(
  curl -sS --fail-with-body "$ARK_BASE_URL/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": "$AGENT_ID",
  "environment_id": "$ENVIRONMENT_ID",
  "title": "Quickstart session"
}
EOF
)
SESSION_ID=$(jq -er '.id' <<<"$session")
echo "Session ID: $SESSION_ID"

# Step 4: Open the SSE stream before sending the message
STREAM_DIR=$(mktemp -d)
STREAM_PIPE="$STREAM_DIR/events"
mkfifo "$STREAM_PIPE"

cleanup_stream() {
  exec 3<&- || true
  if kill -0 "$STREAM_PID" 2>/dev/null; then
    kill "$STREAM_PID" 2>/dev/null || true
  fi
  wait "$STREAM_PID" 2>/dev/null || true
  rm -f "$STREAM_PIPE"
  rmdir "$STREAM_DIR" 2>/dev/null || true
}

curl -sS -N --fail-with-body \
  "$ARK_BASE_URL/api/v3/sessions/$SESSION_ID/events/stream" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Accept: text/event-stream" >"$STREAM_PIPE" &
STREAM_PID=$!
exec 3<"$STREAM_PIPE"
trap cleanup_stream EXIT

stream_ready=false
while IFS= read -r line <&3; do
  if [[ $line == ": ready" ]]; then
    stream_ready=true
    break
  fi
done
if [[ $stream_ready != true ]]; then
  printf 'SSE stream closed before it was ready.\n' >&2
  exit 1
fi

# Send the user message after the stream is ready
curl -sS --fail-with-body \
  "$ARK_BASE_URL/api/v3/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d @- >/dev/null <<'EOF'
{
  "events": [
    {
      "type": "user.message",
      "content": [
        {"type": "text", "text": "用 Python 编写一个脚本，生成前 20 个斐波那契数，并将其保存到 fibonacci.txt。"}
      ]
    }
  ]
}
EOF

stream_finished=false
while IFS= read -r line <&3; do
  [[ $line == data:* ]] || continue
  json=${line:5}
  json=${json# }
  case $(jq -r '.type' <<<"$json") in
    agent.message)
      jq -j '.content[] | select(.type == "text") | .text' <<<"$json"
      ;;
    agent.tool_use)
      printf '\n[Using tool: %s]\n' "$(jq -r '.name' <<<"$json")"
      ;;
    session.status_idle)
      printf '\n\nAgent finished.\n'
      stream_finished=true
      break
      ;;
  esac
done

if [[ $stream_finished != true ]]; then
  printf 'SSE stream closed before the Agent finished.\n' >&2
  exit 1
fi

cleanup_stream
trap - EXIT
```

### SDK 完整示例

**Python**

```Python
import json
import os
import time

import httpx
from arkruntime import Ark


client = Ark(
    api_key=os.environ["ARK_API_KEY"],
    base_url="https://ark.cn-beijing.volces.com/api/v3",
)
suffix = str(int(time.time()))

agent = client.agents.create(
    name=f"quick-start-agent-{suffix}",
    model={"id": "doubao-seed-2-1-pro-260915"},
    system="你是一个高效的编程助手，擅长代码编写和问题排查。",
    tools=[{"type": "agent_toolset_20260916"}],
)
environment = client.environments.create(
    name=f"quick-start-env-{suffix}",
    config={
        "type": "cloud",
        "networking": {"type": "unrestricted"},
    },
)
session = client.sessions.create(
    agent=agent.id,
    environment_id=environment.id,
    title="Quickstart session",
)

try:
    stream_url = "https://ark.cn-beijing.volces.com/api/v3/sessions/{}/events/stream".format(session.id)
    stream_headers = {
        "Authorization": "Bearer {}".format(os.environ["ARK_API_KEY"]),
        "Accept": "text/event-stream",
    }
    with httpx.stream(
        "GET", stream_url, headers=stream_headers, timeout=None
    ) as response:
        response.raise_for_status()
        lines = response.iter_lines()
        for line in lines:
            if line == ": ready":
                break
        else:
            raise RuntimeError("SSE stream closed before it was ready.")

        client.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [
                        {
                            "type": "text",
                            "text": "用 Python 输出前 20 个斐波那契数。",
                        }
                    ],
                }
            ],
        )

        agent_finished = False
        for line in lines:
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            print(event)
            if event["type"] in {
                "session.status_idle",
                "session.status_terminated",
            }:
                agent_finished = True
                break
        if not agent_finished:
            raise RuntimeError("SSE stream closed before the Agent finished.")
finally:
    client.sessions.delete(session.id)
    client.environments.delete(environment.id)
    client.agents.delete(agent.id)
```

**Go**

```Go
package main

import (
    "context"
    "fmt"
    "os"
    "time"

    "github.com/volcengine/ark-runtime-go/arkruntime"
    agentmodel "github.com/volcengine/ark-runtime-go/arkruntime/model/agent"
    envmodel "github.com/volcengine/ark-runtime-go/arkruntime/model/environment"
    sessionmodel "github.com/volcengine/ark-runtime-go/arkruntime/model/session"
)

func main() {
    ctx := context.Background()
    client := arkruntime.NewClientWithApiKey(
        os.Getenv("ARK_API_KEY"),
        arkruntime.WithBaseUrl("https://ark.cn-beijing.volces.com/api/v3"),
    )
    suffix := fmt.Sprint(time.Now().Unix())

    agent, err := client.CreateAgent(ctx, &agentmodel.CreateAgentRequest{
        Name:   "quick-start-agent-" + suffix,
        Model:  agentmodel.ModelConfig{ID: "doubao-seed-2-1-pro-260915"},
        System: agentmodel.NewOptString("你是一个高效的编程助手。"),
        Tools:  []agentmodel.ToolItem{{Type: "agent_toolset_20260916"}},
    })
    if err != nil {
        panic(err)
    }

    environment, err := client.CreateEnvironment(ctx, &envmodel.CreateEnvironmentRequest{
        Name: "quick-start-env-" + suffix,
        Config: envmodel.NewOptEnvConfig(envmodel.EnvConfig{
            Type: envmodel.EnvConfigTypeCloud,
            Networking: envmodel.NewOptNetworkingConfig(envmodel.NetworkingConfig{
                Type: envmodel.NetworkingTypeUnrestricted,
            }),
        }),
    })
    if err != nil {
        panic(err)
    }

    session, err := client.CreateSession(ctx, &sessionmodel.CreateSessionRequest{
        Agent:         sessionmodel.NewStringAgentIdentifier(agent.ID),
        EnvironmentID: sessionmodel.NewOptString(environment.ID),
        Title:         sessionmodel.NewOptString("Quickstart session"),
    })
    if err != nil {
        panic(err)
    }
    defer client.DeleteAgent(ctx, agent.ID)
    defer client.DeleteEnvironment(ctx, environment.ID)
    defer client.DeleteSession(ctx, session.ID)

    content := sessionmodel.ManagedAgentsMessageContentBlock{
        OneOf: sessionmodel.NewManagedAgentsTextBlockManagedAgentsMessageContentBlockSum(
            sessionmodel.ManagedAgentsTextBlock{Text: "用 Python 输出前 20 个斐波那契数。"},
        ),
    }
    event := sessionmodel.ManagedAgentsEventParams{
        OneOf: sessionmodel.NewManagedAgentsUserMessageEventParamsManagedAgentsEventParamsSum(
            sessionmodel.ManagedAgentsUserMessageEventParams{
                Content: []sessionmodel.ManagedAgentsMessageContentBlock{content},
            },
        ),
    }

    stream, err := client.StreamSessionEvents(ctx, session.ID)
    if err != nil {
        panic(err)
    }
    defer stream.Close()

    _, err = client.SendSessionEvents(ctx, session.ID, &sessionmodel.SendSessionEventsRequest{
        Events: []sessionmodel.ManagedAgentsEventParams{event},
    })
    if err != nil {
        panic(err)
    }

    agentFinished := false
    for stream.Next() {
        frame := stream.Event()
        fmt.Printf("%s: %s\n", frame.Type, frame.RawPayload)
        if frame.Type == "session.status_idle" || frame.Type == "session.status_terminated" {
            agentFinished = true
            break
        }
    }
    if err := stream.Err(); err != nil {
        panic(err)
    }
    if !agentFinished {
        panic("SSE stream closed before the Agent finished.")
    }
}
```

**Java**

```Java
package com.ark.sample;

import com.volcengine.ark.runtime.models.agent.Agent;
import com.volcengine.ark.runtime.models.agent.CreateAgentRequest;
import com.volcengine.ark.runtime.models.agent.ModelConfig;
import com.volcengine.ark.runtime.models.agent.ToolItem;
import com.volcengine.ark.runtime.models.environment.CreateEnvironmentRequest;
import com.volcengine.ark.runtime.models.environment.EnvConfig;
import com.volcengine.ark.runtime.models.environment.EnvConfigType;
import com.volcengine.ark.runtime.models.environment.Environment;
import com.volcengine.ark.runtime.models.environment.NetworkingConfig;
import com.volcengine.ark.runtime.models.environment.NetworkingType;
import com.volcengine.ark.runtime.models.session.AgentIdentifier;
import com.volcengine.ark.runtime.models.session.CreateSessionRequest;
import com.volcengine.ark.runtime.models.session.ManagedAgentsMessageContentBlock;
import com.volcengine.ark.runtime.models.session.ManagedAgentsTextBlock;
import com.volcengine.ark.runtime.models.session.ManagedAgentsUserMessageEventParams;
import com.volcengine.ark.runtime.models.session.SendSessionEventsRequest;
import com.volcengine.ark.runtime.models.session.Session;
import com.volcengine.ark.runtime.service.ArkService;
import okhttp3.ResponseBody;
import okio.BufferedSource;
import retrofit2.Response;

import java.util.Arrays;

public class ManagedAgentsQuickStart {
    public static void main(String[] args) throws Exception {
        ArkService service = ArkService.builder()
                .apiKey(System.getenv("ARK_API_KEY"))
                .baseUrl("https://ark.cn-beijing.volces.com/api/v3")
                .build();
        String suffix = String.valueOf(System.currentTimeMillis());

        Agent agent = service.createAgent(
                CreateAgentRequest.builder()
                        .name("quick-start-agent-" + suffix)
                        .model(ModelConfig.builder().id("doubao-seed-2-1-pro-260915").build())
                        .system("你是一个高效的编程助手。")
                        .tools(Arrays.asList(ToolItem.builder().type("agent_toolset_20260916").build()))
                        .build());
        Environment environment = service.createEnvironment(
                CreateEnvironmentRequest.builder()
                        .name("quick-start-env-" + suffix)
                        .config(EnvConfig.builder()
                                .type(EnvConfigType.CLOUD)
                                .networking(NetworkingConfig.builder()
                                        .type(NetworkingType.UNRESTRICTED)
                                        .build())
                                .build())
                        .build());
        Session session = service.createSession(
                CreateSessionRequest.builder()
                        .agent(AgentIdentifier.ofString(agent.getId()))
                        .environmentId(environment.getId())
                        .title("Quickstart session")
                        .build());

        try {
            ManagedAgentsTextBlock text = ManagedAgentsTextBlock.builder()
                    .text("用 Python 输出前 20 个斐波那契数。")
                    .build();
            ManagedAgentsUserMessageEventParams event =
                    ManagedAgentsUserMessageEventParams.builder()
                            .content(Arrays.<ManagedAgentsMessageContentBlock>asList(text))
                            .build();

            Response<ResponseBody> response =
                    service.streamSessionEvents(session.getId()).execute();
            if (!response.isSuccessful() || response.body() == null) {
                throw new IllegalStateException("Stream request failed: " + response.code());
            }
            try (ResponseBody body = response.body()) {
                BufferedSource source = body.source();
                service.sendSessionEvents(
                        session.getId(),
                        SendSessionEventsRequest.builder()
                                .events(Arrays.asList(event))
                                .build());

                String line;
                boolean agentFinished = false;
                while ((line = source.readUtf8Line()) != null) {
                    if (line.startsWith("data:")) {
                        System.out.println(line.substring(5).trim());
                    }
                    if (line.contains("session.status_idle")
                            || line.contains("session.status_terminated")) {
                        agentFinished = true;
                        break;
                    }
                }
                if (!agentFinished) {
                    throw new IllegalStateException(
                            "SSE stream closed before the Agent finished.");
                }
            }
        } finally {
            service.deleteSession(session.getId());
            service.deleteEnvironment(environment.getId());
            service.deleteAgent(agent.getId());
            service.shutdownExecutor();
        }
    }
}
```

## 运行说明

当您发送用户事件时，方舟托管 Agent 会：

1. **配置沙箱**：您的环境配置决定了沙箱的构建方式。

2. **运行Agent循环**：方舟根据您的消息确定要使用哪些工具。

3. **执行工具**：启动沙箱，在沙箱内运行文件写入、bash 命令和其他工具调用。

4. **流式传输事件**：您会在Agent工作时收到实时更新。

5. **进入空闲状态**：当Agent没有更多任务要执行时，会发出 `session.status_idle` 事件。

## 设计建议

配置 Agent 和 Session 时，按照配置的生命周期区分长期能力与单次任务：

- **长期能力写入 Agent 定义**：通过 `Agents_create` 或 `Agents_update` 配置系统提示词、Skills、Tools 和 MCP Server，供多个 Session 复用。

- **单次任务通过 Session 事件传入**：通过 `SessionEvents_send` 发送 `user.message` 事件，不要将本次任务写入 Agent 的 `system` 字段。

将单次任务写入 `system` 会降低 Agent 定义的复用性；将长期规则放入 Session 事件则需要重复传输，也容易造成不同 Session 的配置不一致。

## 后续步骤

[定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)

定义 Agent 的模型、系统提示词、工具集与运行行为。

[配置环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment)

配置 Agent 运行所在的云端沙箱环境，包含网络、预装依赖包与环境变量。

[Agent tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)

配置 Agent 在 Session 中可主动调用的工具集合。

[Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)

通过 SSE 事件流实时接收 Agent 的消息、工具调用与状态更新。

<a id="doc-2553715"></a>

---

## 快速入门（控制台）

> 来源：[https://docs.volcengine.com/docs/82379/2553715?lang=zh](https://docs.volcengine.com/docs/82379/2553715?lang=zh)

方舟 Managed Agents 控制台提供可视化界面，让你无需编写 API 代码即可创建、配置并测试 Agent。你可以在控制台里交互式地打磨 Agent 配置，验证行为符合预期后，再拿到对应的 ID 接入到自己的业务代码中。

> 首次访问需登录 [火山方舟控制台](https://ark.volcengine.com/region:cn-beijing/managed-agents/agents?projectName=default)，进入 **Managed Agents** 模块。

## 准备工作

### 1. 获取 API Key

访问 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)，单击 **创建 API Key**，作为后续接入业务代码的访问密钥。

### 2. 开通 Managed Agents 服务

访问 [Managed Agents 开通页面](https://ark.volcengine.com/region:cn-beijing/openManagement?advancedActiveKey=manageAgent) 开通服务。

### 3. 开通模型服务

访问 [开通管理页面](https://ark.volcengine.com/region:cn-beijing/openManagement) 开通模型服务。

创建 Seedance Managed Agent 前，根据控制台提示开通 Seedance Managed Agent 使用权限及所需的模型和工具服务。

## 构建 Agent

### 1. 创建 Agent

Agent 是可版本化、可复用的智能体配置资产，定义角色、能力、行为边界和运行规则，是所有任务运行的模板和基准。

访问 [火山方舟控制台](https://ark.volcengine.com/region:cn-beijing/managed-agents/agents?projectName=default)，进入 **Managed Agents** 模块。在 **Agents** 页面单击 **创建 Agent**，跟随界面引导填写：

- **Agent 版本**：根据任务类型选择通用 Agent 或 Seedance Managed Agent。

- **模型和系统提示词**：选择一个 **模型**，并编写 **系统提示词**，为 Agent 设定角色、做事风格与工作原则。

- **Skills**：从 **火山 SkillHub** 中挂载预置 **Skill**，或本地上传自定义 **Skill**，扩展 Agent 的领域能力。

- **Tools**：勾选内置的 **Agent Toolset**（如 `bash`、`edit`、`read`、`write` 等基础工具），让 Agent 能够读写文件、执行命令。

- **MCPs**：以 URL 形式接入远程 **MCP Server**，把外部系统能力接入 Agent。

- **Multi Agents**：将多个 Agent 组合成协同工作流，一个 Agent 可以调用另一个 Agent 完成子任务。

保存后，控制台会为该 Agent 生成一个稳定的 `agent_id`。每次修改配置都会自动生成新版本，支持版本对比、回滚和灰度发布。

### 2. 创建 Environment

定义 Agent 的运行环境模板，预置依赖、环境变量和产物存储等。创建 Session 时绑定 Environment，确保代码在一致环境中执行。

在 [Environments 页面](https://ark.volcengine.com/region:cn-beijing/managed-agents/environments?projectName=default) 单击 **创建 Environment**，跟随界面引导填写：

- **类型**：当前仅支持 **云托管**：沙箱由方舟按需拉起、自动休眠。

- **预装包**：按需添加 `pip`、`apt` 依赖（如 `numpy`、`pandas`），沙箱首次启动时安装。

- **环境变量**：以键值对形式注入到沙箱进程，沙箱启动时自动加载，供 Agent 执行的代码和命令直接读取。常用于配置业务侧的非敏感参数，例如设置时区 `TZ=Asia/Shanghai`。

- **产物存储**：可选。需要长期保留 Agent 写入 `/mnt/session/outputs/` 的最终产物时，选择已授权的自有 TOS 目录；不选择时使用方舟公共 TOS，你需要在平台 TTL 到期前下载所需文件。

保存后得到一个稳定的 `environment_id`。

> **说明**

> 你的 TOS Bucket 必须与 Managed Agents 服务部署在同一地域。首次选择 TOS 目录时，如果控制台提示未授权，先按页面引导完成项目授权。

## 调试 Agent

在控制台里以交互方式测试 Agent。每次调试创建一个 Session，展示事件流、工具调用、输入输出完整记录，可随时回放。

Session 是一次独立的 Agent 任务运行会话，包含从发起、执行到结束的完整生命周期：

1. 在 [Sessions 页面](https://ark.volcengine.com/region:cn-beijing/managed-agents/sessions?projectName=default) 单击 **创建 Session**，选择要绑定的 **Agent** 和 **Environment**。Session 默认继承该 Environment 的产物存储配置。

2. 在 **Session 详情** 页面调试会话：

   - **发起**：在 **会话面板** 输入自然语言指令，或上传附件（表格、文档、代码、图片等）作为任务输入。

   - **观察**：**事件时间轴（Trace）** 实时呈现 Agent 的思考过程、工具调用、代码执行、错误重试等全链路节点。

   - **验证**：Session 结束后可查看 **最终交付产物**、**总耗时**、**Token 消耗**、**工具调用次数** 等运行统计，判断 Agent 行为是否符合预期。

反复调整 **系统提示词**、**Tools**、**Skills**，直到 Session 试跑结果符合预期。

## 从原型到代码

Agent 配置验证通过后，把它接入到业务代码中只需两步。

### 1. 从控制台复制 ID

- 从 **Agent 详情页** 复制 `agent_id`。

- 从 **Environment 详情页** 复制 `environment_id`。

- 前往 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey) 单击 **创建 API Key**，作为访问密钥。

### 2. 在代码中引用它们创建 Session

将 `agent_id`、`environment_id` 和 API Key 填入下面的请求，创建一个绑定该 Agent 与环境的 Session：

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-xxxxxxxxxxxxxx-xxxxx",
    "environment_id": "env-xxxxxxxxxxxxxx-xxxxx",
    "title": "My first session"
  }'
```

拿到返回的 `session_id` 后，即可通过 Events API 向 Session 发送消息，并通过 SSE 流式接收 Agent 的响应事件。详情请参见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)。

<a id="doc-2553716"></a>

---

## Agent

> 来源：[https://docs.volcengine.com/docs/82379/2553716?lang=zh](https://docs.volcengine.com/docs/82379/2553716?lang=zh)

方舟 Managed Agents 是火山方舟平台推出的全托管 Agent 服务，给您带来开箱即用的 Agent 体验。Agent 是方舟 Managed Agents 的基础元件，是一套包含基本信息、System Prompt、扩展能力（Skills、Tools、MCP 等）的配置模板，可以被任意 Session 复用，并支持版本化管理。本文介绍如何定义一个 Agent。

## Agent 版本

创建 Agent 时，先根据任务选择 Agent 版本：

- **通用 Agent**：适用于代码执行、内容检索和文档处理等通用任务。

- **Seedance Managed Agent**：适用于图片生成、视频生成、视觉搜索和视频合成等任务。

## Agent 定义字段

Agent 的完整字段定义（包括名称、模型、System Prompt、Skills、Tools、MCP、多智能体协作、上下文压缩、元数据等），请参见[创建智能体](https://ark.volcengine.com/region:cn-beijing/docs/create-agent-api)。

## 准备工作

1. 获取 API Key。API Key 是调用方舟平台模型和服务的鉴权信息。

   访问 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey) 页面，创建你的 API Key。

2. 安装 `curl` 和 `jq`，并配置环境变量。API Key 是敏感信息，一旦意外泄露，可能会造成资金损失或安全风险，因此不要在代码中明文写入 API Key，应将其配置到环境变量中。

   将以下命令中的 `your_api_key_here` 替换为你的 API Key，并在终端中运行命令，即可将 API Key 配置到环境变量中。详见[环境变量配置指南](https://ark.volcengine.com/region:cn-beijing/docs/environment-variable-configuration-guide)。

   **macOS**

```Bash
   export ARK_API_KEY="your_api_key_here"
```

**Linux**

```Bash
   export ARK_API_KEY="your_api_key_here"
```

**Windows_CMD**

```CMD
   setx ARK_API_KEY "your_api_key_here"
```

**Windows_PowerShell**

```PowerShell
   $env:ARK_API_KEY = "your_api_key_here"
```

3. 开通 Managed Agents 服务。

   访问 [Managed Agents 开通页面](https://ark.volcengine.com/region:cn-beijing/openManagement?advancedActiveKey=manageAgent) 开通服务。

4. 开通模型服务。

   访问 [开通管理](https://ark.volcengine.com/region:cn-beijing/openManagement) 页面，开通模型服务。

## 创建 Agent

创建 Agent 后，接口会返回一个稳定的 Agent ID 和初始版本号 `1`。后续创建 Session 时，可以直接引用这个 Agent ID。

单个账号最多可创建 1000 个 Agent。

下面的示例创建一个带内置工具集和自定义 Skill 的热点新闻 Agent。示例显式配置内置工具的启用状态和权限策略，并将响应中的 Agent ID 与当前版本保存到环境变量：

```Bash
AGENT_RESPONSE=$(curl -sS --fail-with-body https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NewsAgent01",
    "model": {
      "id": "doubao-seed-2-1-pro-260628",
      "speed": "standard"
    },
    "description": "将热点新闻总结为图片的小助手。",
    "system": "你是一名热点新闻查询总结小助手，可将每天的前 10 条热点新闻以摘要图片的方式总结出来。",
    "skills": [
      {
        "type": "custom",
        "skill_id": "skill-20260812080348-****"
      }
    ],
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "default_config": {
          "enabled": true,
          "permission_policy": {
            "type": "always_allow"
          }
        }
      }
    ]
  }') || exit 1

jq . <<<"$AGENT_RESPONSE" || exit 1
AGENT_ID=$(jq -er '.id' <<<"$AGENT_RESPONSE") || exit 1
AGENT_VERSION=$(jq -er '.version' <<<"$AGENT_RESPONSE") || exit 1
export AGENT_ID AGENT_VERSION
printf 'Agent ID: %s\nAgent version: %s\n' "$AGENT_ID" "$AGENT_VERSION"
```

其中 `skill-20260812080348-****` 表示你上传自定义 Skill 后获得的 `skill_id`。

示例响应如下：

```Plain Text
{
  "id": "agent-20260812081435-*****",
  "type": "agent",
  "name": "NewsAgent01",
  "description": "将热点新闻总结为图片的小助手。",
  "version": 1,
  "model": {
    "id": "doubao-seed-2-1-pro-260628",
    "speed": "standard"
  },
  "system": "你是一名热点新闻查询总结小助手，可将每天的前 10 条热点新闻以摘要图片的方式总结出来。",
  "tools": [
    {
      "type": "agent_toolset_20260916",
      "default_config": {
        "enabled": true,
        "permission_policy": {
          "type": "always_allow"
        }
      }
    }
  ],
  "skills": [
    {
      "type": "custom",
      "skill_id": "skill-20260812080348-****"
    }
  ],
  "created_at": "2026-08-12T08:14:35Z",
  "updated_at": "2026-08-12T08:14:35Z"
}
```

## 更新 Agent 与版本

Agent 是版本化资源。每次更新配置时，都需要显式传入当前版本号；如果版本号不匹配，更新会失败。更新成功后，系统会生成一个新版本。

以下示例先查询 Agent 的当前版本，再提交更新。运行本页的创建示例后，可以直接复用 `AGENT_ID`；更新其他 Agent 时，先将其 ID 写入该环境变量。

```Bash
: "${AGENT_ID:?Run the create example first or set AGENT_ID}"

CURRENT_AGENT=$(curl -sS --fail-with-body \
  "https://ark.cn-beijing.volces.com/api/v3/agents/$AGENT_ID" \
  -H "Authorization: Bearer $ARK_API_KEY") || exit 1
AGENT_VERSION=$(jq -er '.version' <<<"$CURRENT_AGENT") || exit 1

UPDATE_RESPONSE=$(curl -sS --fail-with-body -X PUT \
  "https://ark.cn-beijing.volces.com/api/v3/agents/$AGENT_ID" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
    "version": $AGENT_VERSION,
    "system": "你是一名热点新闻查询总结小助手，可将每天的前 10 条热点新闻以摘要图片和音频播报的方式总结出来。",
    "skills": [
      {
        "type": "custom",
        "skill_id": "skill-20260812075208-****"
      },
      {
        "type": "custom",
        "skill_id": "skill-20260729084811-****"
      }
    ]
}
EOF
) || exit 1

jq . <<<"$UPDATE_RESPONSE" || exit 1
AGENT_VERSION=$(jq -er '.version' <<<"$UPDATE_RESPONSE") || exit 1
export AGENT_VERSION
printf 'Updated Agent version: %s\n' "$AGENT_VERSION"
```

示例响应如下：

```Plain Text
{
  "id": "agent-20260812081435-*****",
  "type": "agent",
  "name": "NewsAgent01",
  "description": "将热点新闻总结为图片的小助手。",
  "version": 2,
  "model": {
    "id": "doubao-seed-2-1-pro-260628",
    "speed": "standard"
  },
  "system": "你是一名热点新闻查询总结小助手，可将每天的前 10 条热点新闻以摘要图片和音频播报的方式总结出来。",
  "tools": [
    {
      "type": "agent_toolset_20260916",
      "default_config": {
        "enabled": true,
        "permission_policy": {
          "type": "always_allow"
        }
      }
    }
  ],
  "skills": [
    {
      "type": "custom",
      "skill_id": "skill-20260812075208-****"
    },
    {
      "type": "custom",
      "skill_id": "skill-20260729084811-****"
    }
  ],
  "created_at": "2026-08-12T08:14:35Z",
  "updated_at": "2026-08-12T08:25:21Z"
}
```

更新 Agent 时，建议关注以下规则：

- 更新请求必须带当前 `version`。

- 修改配置后会生成新的 Agent 版本。

- 更新接口返回 HTTP 400 `InvalidParameter` 且错误信息包含 `version: conflict` 时，说明其他请求已经更新了该 Agent。重新查询 Agent，合并最新配置后再提交，不要只递增本地版本号。

- 如果某些字段保持不变，可以只传需要修改的字段；例如只更新 `system` 时，不需要重复传 `tools`。

- `mcp_servers`、`tools` 和 `skills` 都使用整体替换逻辑。请求体一旦传入其中一个数组，服务端会用该数组整体覆盖对应的当前配置，不会在原有基础上追加。

- 如果只想新增或调整其中一项，先查询 Agent 的最新配置，在本地合并需要保留的条目，再把完整数组与当前 `version` 一起写回。

| 取值          | `mcp_servers`、`tools` 和 `skills` 的处理方式 | 适用场景                              |
| ------------- | --------------------------------------------- | ------------------------------------- |
| 省略或 `null` | 保留对应字段的当前配置。                      | 只更新其他字段。                      |
| 空数组 `[]`   | 清空对应字段的已保存配置。                    | 明确移除全部 MCP Server、工具或技能。 |
| 非空数组      | 使用请求中的数组整体替换当前配置。            | 先读取并合并所有需要保留的条目。      |

> **注意**

> 如需限制主 Agent 的内置工具范围，请显式配置内置工具集并关闭默认范围，不要通过清空 `tools` 实现。详情请参见 [核对默认工具范围](https://ark.volcengine.com/region:cn-beijing/docs/tools#tool-defaults-and-boundaries)。

## 设计建议

- 把稳定能力放进 Agent，把一次性任务放进 Session 事件。

- `system` 只定义角色、约束和长期规则，不要把当前任务直接写进 `system`。

- 需要外部系统能力时，优先使用 MCP。

- 需要复用领域知识或执行规范时，优先挂载 Skills，而不是把大段操作手册直接塞进 `system`。

## 相关文档

[Skills](https://ark.volcengine.com/region:cn-beijing/docs/skills)

Skills 用于给 Agent 补充领域知识、操作流程和最佳实践。

[MCP](https://ark.volcengine.com/region:cn-beijing/docs/mcp)

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。

[Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)

Tools 决定 Agent 在 Session 中能主动调用哪些执行能力。

[工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy)

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待业务应用确认。

<a id="doc-2553717"></a>

---

## Skills

> 来源：[https://docs.volcengine.com/docs/82379/2553717?lang=zh](https://docs.volcengine.com/docs/82379/2553717?lang=zh)

Skills 是可复用的能力包，用于给 Agent 补充领域知识、操作流程和最佳实践。与直接把大量规则写进 `system` 不同，Skills 更适合承载可复用、可维护的专业能力。

## 获取 Skills

方舟 Managed Agents 支持两类 Skills 来源：

- **预置 Skills**：从 SkillHub 选择。

- **自定义 Skills**：通过 `CreateSkill` 接口上传本地 Skills。

### 方式 1：从 SkillHub 选择预置 Skills

如果你使用的是平台预置 Skills，可以直接在 Agent 的 `skills` 配置中引用对应 Skill。预置 Skills 适合通用场景，例如文档处理、结构化内容生成或固定工作流编排。

由于预置 Skills 由平台统一维护，你不需要自己上传文件，也不需要关心版本包结构。选择完成后，把对应 Skill ID 写入 Agent 的 `skills` 数组即可。

如果你需要查询预置 Skill 的 ID，请前往 [SkillHub 页面](https://console.volcengine.com/skillhub) 查看。

### 方式 2：上传自定义 Skills

如果预置 Skills 无法覆盖你的场景，可以先通过 `CreateSkill` 接口上传本地 Skills，再在 Agent 中引用返回的 `skill_id`。

当前支持两种上传方式：

- **按文件上传**：逐个上传同一个 Skill 目录中的文件，并保留目录结构。

- **ZIP 上传**：将一个完整 Skill 目录打包成 ZIP 文件后上传。

> **说明**

> 每次请求只能创建一个 Skill，且技能文件中必须包含且仅包含一个 `SKILL.md`。

#### 上传限制与目录建议

按以下约束组织自定义 Skills：

- 每次请求上传的原始文件总大小不超过 **30 MiB**。

- 单个版本最多包含 **500** 个文件。

- ZIP 解压后单个文件不超过 **30 MiB**，全部文件总大小不超过 **120 MiB**。

- 所有文件必须位于同一个顶层目录，且该目录下必须直接包含且仅包含 **1 个** `SKILL.md`。

ZIP 包推荐使用统一顶层目录，`SKILL.md` 放在顶层目录的直接子级：

```text
basic_math.zip
└── basic_math/
    ├── SKILL.md
    └── meta.json
```

#### 按文件上传

按文件上传适合在构建流程中逐个组织文件。重复传入 `files[]`，并在每个字段的 `filename=` 中填写文件上传后在 Skill 中的路径。

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/skills \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -F "display_title=Web Artifacts Builder" \
  -F "files[]=@./basic_math/SKILL.md;filename=basic_math/SKILL.md;type=text/markdown" \
  -F "files[]=@./basic_math/scripts/init.sh;filename=basic_math/scripts/init.sh;type=text/plain"
```

其中：

- `files[]` 是按文件上传使用的字段名。

- `@` 后面是本地真实路径。

- `filename=` 指定文件上传后在 Skill 中的路径，例如 `basic_math/SKILL.md`。

- 所有 `filename=` 必须使用同一个顶层目录，例如 `basic_math/`。

- `type=` 是 MIME 类型，可以省略。

#### ZIP 上传

ZIP 方式更适合把整个 Skill 目录一次性打包上传：

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/skills \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -F "files=@./basic_math.zip;type=application/zip"
```

成功上传后，接口会返回 Skill 记录，重点关注 `id`、`latest_version`、`display_title` 和 `source`：

```json
{
  "id": "skill-20260926143025-a1b2c",
  "object": "skill",
  "created_at": 1790433025,
  "description": "Perform basic arithmetic operations.",
  "latest_version": "1",
  "display_title": "basic-math",
  "source": "custom",
  "updated_at": 1790433025,
  "name": "basic-math"
}
```

其中 `id` 就是后续挂载到 Agent 时要引用的 `skill_id`。

## 在 Agent 上引用 Skills

获取 Skills 后，你需要在 `CreateAgent` 或 `UpdateAgent` 的 `skills` 字段中引用 Skill。

- **预置 Skills**：从 SkillHub 选择后，把对应 Skill ID 写入 `skills`。

- **自定义 Skills**：先调用 `CreateSkill` 上传，拿到 `skill_id` 后再写入 `skills`。如果需要指定自定义 Skill 的版本，请显式传入 `version`，否则通常使用默认版本。

> **说明**

> 单个 Agent 最多可配置 50 个 Skills。

### `skills` 字段结构

在 `CreateAgent` 或 `UpdateAgent` 请求中，每个 Skill 条目都通过 `skills` 数组声明。关于字段结构的详细信息，请参见[创建智能体](https://ark.volcengine.com/region:cn-beijing/docs/create-agent-api)。

### 示例代码

示例如下：

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "数据处理 Agent",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "skills": [
      {
        "type": "skill_hub",
        "skill_id": "s-yepozmp9tsf2adf*****"
      },
      {
        "type": "custom",
        "skill_id": "skill-20260812080348-****",
        "version": "1"
      }
    ]
  }'
```

其中：

- `s-yepozmp9tsf2adf*****` 表示你从 [SkillHub 页面](https://console.volcengine.com/skillhub) 查到的预置 Skill ID。

- `skill-20260812080348-****` 表示 `CreateSkill` 返回的自定义 Skill ID。

## 使用建议

- 对稳定、通用的能力，优先选择 SkillHub 中的预置 Skills。

- 对团队私有流程、内部规范或自定义操作手册，使用自定义 Skills。

- 不要把一次性任务描述写进 Skill；Skill 负责定义能力，具体任务应通过 Session 事件传入。

- 上传前建议在本地先确认 `SKILL.md`、脚本和依赖文件路径关系，避免上传后引用失败。

## 相关文档

[Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)

Agent 是包含基本信息、System Prompt、扩展能力的配置模板。

[MCP](https://ark.volcengine.com/region:cn-beijing/docs/mcp)

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。

[Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)

Tools 决定 Agent 在 Session 中能主动调用哪些执行能力。

[工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy)

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待业务应用确认。

<a id="doc-2553718"></a>

---

## MCP

> 来源：[https://docs.volcengine.com/docs/82379/2553718?lang=zh](https://docs.volcengine.com/docs/82379/2553718?lang=zh)

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。通过 MCP，Agent 可以访问外部服务暴露出来的标准化工具，例如代码托管、项目协作、知识库或内部业务系统。

在方舟 Managed Agents 中，MCP 配置分成两层：

- **Agent 定义层**：声明要连接哪些 MCP Server。

- **Session 运行层**：通过 Vaults 注入对应凭据，完成鉴权。

这种拆分让 Agent 定义保持可复用，同时避免把终端用户密钥固定地写在 Agent 资源里。

## 在 Agent 上声明 MCP Server

创建 Agent 时，通过 `mcp_servers` 数组声明 MCP Server。每个 Server 需要三个字段：

| 字段   | 说明                                                                                                                                                                                                                                                                    |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `type` | MCP Server 的声明方式。当前统一使用 URL 方式声明，值为 `url`。                                                                                                                                                                                                          |
| `name` | MCP Server 名称，最长 255 个字符。省略名称或名称不符合命名规则时，服务端会根据 `url` 生成名称；多个名称冲突时，服务端会为后续名称添加数字后缀，调用方无需保证名称唯一。为保证后续引用稳定，建议主动填写归一化后仍唯一的 `[a-z0-9_-]{1,64}` 小写名称，不依赖服务端生成。 |
| `url`  | MCP Server 的访问地址。同一 Agent 中不能配置重复的地址。                                                                                                                                                                                                                |

每个 `mcp_toolset` 都必须通过 `mcp_server_name` 引用一个已声明的 MCP Server。你也可以只声明 MCP Server，暂不添加对应的 `mcp_toolset`；此时该 Server 的工具不会启用。

下面的示例把一个 GitHub MCP Server 挂到 Agent：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub 助理",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "mcp_servers": [
      {
        "type": "url",
        "name": "github",
        "url": "https://mcp.example.com/github"
      }
    ],
    "tools": [
      {
        "type": "agent_toolset_20260916"
      },
      {
        "type": "mcp_toolset",
        "mcp_server_name": "github"
      }
    ]
  }'
```

## 控制哪些 MCP 工具可用

`mcp_toolset` 的配置方式与内置工具集一致，也支持 `default_config` 和 `configs`。两类工具在显式提供 `default_config` 但省略 `permission_policy` 时采用不同的运行时默认值：内置工具使用 `always_allow`，MCP 工具使用 `always_ask`。为避免执行行为随工具类型变化，示例应同时显式配置 `enabled` 和 `permission_policy`。

如果某个 MCP Server 暴露的工具很多，建议先整体关闭，再按白名单开启：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "精简 GitHub 助理",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "mcp_servers": [
      {
        "type": "url",
        "name": "github",
        "url": "https://mcp.example.com/github"
      }
    ],
    "tools": [
      {
        "type": "mcp_toolset",
        "mcp_server_name": "github",
        "default_config": {
          "enabled": false,
          "permission_policy": {
            "type": "always_ask"
          }
        },
        "configs": [
          {
            "name": "list_issues",
            "enabled": true
          },
          {
            "name": "get_issue",
            "enabled": true
          },
          {
            "name": "add_issue_comment",
            "enabled": true
          }
        ]
      }
    ]
  }'
```

如果只需关闭少量 MCP 工具，将 `default_config.enabled` 设为 `true`，显式配置 `default_config.permission_policy`，再在 `configs` 中把对应工具设为 `enabled: false`。完整默认矩阵请参见 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy)。

## 在 Session 中注入 MCP 凭据

MCP Server 的认证不在 Agent 定义阶段传入，而是在创建 Session 时通过 `vault_ids` 引用 Vaults 中的凭据。这样你可以让同一个 Agent 在不同 Session 中代表不同终端用户访问外部系统。

最简示例如下：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-...",
    "environment_id": "env-...",
    "vault_ids": ["vlt-..."]
  }'
```

Vaults 的创建方法、`mcp_oauth` / `static_bearer` 等凭据类型，以及多 Vaults 匹配规则，详情请参见 [使用 Vaults 认证](https://ark.volcengine.com/region:cn-beijing/docs/use-vaults-authentication)。

## 约束与建议

- 同一个 Agent 中，`mcp_servers.url` 不能重复；`mcp_servers.name` 冲突时，服务端会自动追加数字后缀。

- 每个 `mcp_toolset` 必须引用一个已声明的 MCP Server；只声明 MCP Server 不会启用其工具。

- 不要把终端用户 token 直接写进 Agent 定义；应通过 Vaults 在 Session 级注入。

- **高风险工具**：对高风险 MCP 工具，建议配合 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy) 使用 `always_ask`。

## 相关文档

[Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)

Agent 是包含基本信息、System Prompt、扩展能力的配置模板。

[Skills](https://ark.volcengine.com/region:cn-beijing/docs/skills)

Skills 用于给 Agent 补充领域知识、操作流程和最佳实践。

[Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)

Tools 决定 Agent 在 Session 中能主动调用哪些执行能力。

[工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy)

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待业务应用确认。

<a id="doc-2553719"></a>

---

## Tools

> 来源：[https://docs.volcengine.com/docs/82379/2553719?lang=zh](https://docs.volcengine.com/docs/82379/2553719?lang=zh)

模型可以理解任务并生成内容，但无法仅靠推理直接操作文件、执行命令或访问业务系统。为 Agent 配置 Tools 后，Agent 可以调用相应的执行能力，并根据工具结果继续处理任务，完成从理解需求到执行操作的闭环。

## 选择工具

根据任务需要访问的资源和工具执行方，选择对应的工具类型。

- 任务需要操作文件、执行命令或获取网页信息时，使用基础工具集。自托管环境只改变其中部分工具的执行位置。

- 任务需要生成图片、视频或组合视觉素材时，使用多模态工具。

- 任务需要访问已经接入 MCP Server 的外部系统时，使用 MCP 工具。

- 任务只需调用少量自有业务操作，且由业务应用执行时，使用自定义工具。

### 使用内置工具

当任务需要处理文件、运行命令或获取网页信息时，新建 Agent 使用当前内置工具集 `agent_toolset_20260916`。典型场景包括整理输入文件、生成并运行代码，以及检索和汇总公开信息。

把 `agent_toolset_20260916` 加到 Agent 后，以下工具默认全部启用。

| 工具       | 配置名       | 说明                     |
| ---------- | ------------ | ------------------------ |
| Bash       | `bash`       | 在沙箱中执行 Bash 命令。 |
| Read       | `read`       | 读取沙箱内文件。         |
| Write      | `write`      | 写入或覆盖沙箱内文件。   |
| Edit       | `edit`       | 对文件执行字符串替换。   |
| Glob       | `glob`       | 按名称查找文件。         |
| Grep       | `grep`       | 按正则搜索文本内容。     |
| Web Fetch  | `web_fetch`  | 抓取指定 URL 内容。      |
| Web Search | `web_search` | 发起联网搜索。           |

不同版本的基础工具集包含相同的工具。历史取值仅用于兼容已有 Agent 配置，新建 Agent 使用 `agent_toolset_20260916`。

> **注意**

> Web Search 按实际调用次数计费。

### 使用多模态工具

Seedance Managed Agent 使用 `agent_toolset_multimodal_20260825` 提供图片生成、视频生成、视觉搜索、通用搜索、视频合成和任务进度管理能力。创建 Seedance Managed Agent 时，还必须同时配置 `agent_toolset_20260916`。

自托管 Session 也可以使用多模态工具集。Environment 类型不会改变多模态工具的执行位置；方舟侧负责执行多模态工具，并将结果写入 Session 事件流。

| 配置名           | 事件中的工具名  | 是否必须启用     | 说明                                                                                                                   |
| ---------------- | --------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `image_generate` | `GenImage`      | 是               | 生成图片。通过 `configs[].models` 绑定已开通的图片生成模型。                                                           |
| `video_generate` | `GenVideo`      | 否               | 生成视频。启用时，通过 `configs[].models` 绑定已开通的视频生成模型。                                                   |
| `visual_search`  | `VisualSearch`  | 是               | 检索与创作任务相关的视觉素材。                                                                                         |
| `general_search` | `GeneralSearch` | 是               | 检索创作任务需要的通用信息。                                                                                           |
| `compose_video`  | `ComposeVideo`  | 是               | 将视频、图片和音频素材合成为视频。                                                                                     |
| `todo_write`     | `TodoWrite`     | 默认启用，可关闭 | 为复杂、长耗时任务维护待办和进度。将 `default_config.enabled` 设置为 `false` 时，需要在 `configs[]` 中显式启用该工具。 |

事件流还可能出现 `ViewMedia` 和 `record_search_quality`。这两个工具由平台自动提供，不能通过 `configs[]` 配置。工具调用的顺序和次数由任务内容决定。

多模态工具产生的模型调用费用和工具调用费用，详情请参见 [Managed Agents 计费说明](https://ark.volcengine.com/region:cn-beijing/docs/model-pricing#ma_billing)。

在请求的 `configs[].models[]` 中，每项只需填写模型 ID 或推理接入点 ID（`id`）和是否为默认模型（`default`）。每个生成工具最多配置 10 项，ID 不能重复；配置多项时，必须且只能将一项的 `default` 设置为 `true`。省略已启用生成工具的 `models` 时，服务端绑定平台默认模型，并在响应中返回实际绑定结果。响应中的模型名称（`name`）和版本（`version`）由服务端返回，不需要在请求中填写。

> **注意**

> 启用 `general_search` 时，必须在基础工具集中关闭 `web_search`。两个搜索工具同时启用时，服务端返回 400 `InvalidParameter`。

> **自托管环境的数据边界**

> 自托管环境不会把图片生成、视频生成、视觉搜索和视频合成迁移到企业 Worker。多模态工具的调用参数和媒体输入会发送到方舟，执行结果也会进入 Session 事件流。业务数据不能进入该链路时，关闭多模态工具集。

Seedance Managed Agent 的完整工具配置和执行流程，详情请参见 [[基础] 使用 Seedance Managed Agent 生成图片和视频](https://ark.volcengine.com/region:cn-beijing/docs/visual-creation-agent)。

### 接入 MCP 工具

当任务需要访问已经通过 MCP Server 接入的外部系统，或需要复用一组标准化外部工具时，使用 MCP 工具集 `mcp_toolset`。典型场景包括查询项目数据、读取业务信息和触发外部工作流。

MCP 工具集通过 `type: "mcp_toolset"` 将一个 MCP Server 暴露的工具提供给 Agent。`mcp_server_name` 必须与 `mcp_servers[].name` 一致；只声明 MCP Server 而不添加对应的 `mcp_toolset` 时，Agent 无法调用该 Server 的工具。

云环境和自托管环境中的 MCP 工具均由方舟侧执行，不会转交企业 Worker。

可以通过 `default_config` 设置该 Server 下工具的默认状态，再通过 `configs` 按工具名称覆盖。完整配置和鉴权方式请参见 [MCP](https://ark.volcengine.com/region:cn-beijing/docs/mcp)。

显式提供 `default_config` 但省略 `permission_policy` 时，MCP 工具运行时使用 `always_ask`，内置工具使用 `always_allow`。为避免 MCP 调用意外暂停，建议显式配置权限策略。

### 接入自定义工具

当任务只需要调用少量自有业务操作，且无需为这些操作单独部署 MCP Server 时，使用自定义工具 `custom`。典型场景包括查询订单、提交工单和触发审批。业务应用负责执行实际操作，再把结果回传给 Agent。

自定义工具通过 `type: "custom"` 声明。每个工具需要配置名称、用途描述和输入参数 Schema。以下示例使用 `curl` 创建 Agent，并使用 `jq` 保存返回的 Agent ID。示例同时显式关闭内置工具集，避免主 Agent 回退启用全部内置工具：

**Curl**

```Bash
AGENT_RESPONSE=$(curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/agents" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CustomToolDemo",
    "model": {
      "id": "doubao-seed-2-1-pro-260915"
    },
    "system": "你是一个可以调用业务工具的助手。需要外部业务能力时，调用已声明的自定义工具。不要编造工具结果。",
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "default_config": {
          "enabled": false,
          "permission_policy": {
            "type": "always_allow"
          }
        }
      },
      {
        "type": "custom",
        "name": "query_order",
        "description": "根据订单 ID 查询订单状态。仅当用户要求查询订单信息时使用。",
        "input_schema": {
          "type": "object",
          "properties": {
            "order_id": {
              "type": "string",
              "description": "订单 ID。"
            }
          },
          "required": ["order_id"],
          "additionalProperties": false
        }
      }
    ]
  }') || exit 1

jq . <<<"$AGENT_RESPONSE" || exit 1
AGENT_ID=$(jq -er '.id' <<<"$AGENT_RESPONSE") || exit 1
export AGENT_ID
printf 'Agent ID: %s\n' "$AGENT_ID"
```

配置后，Agent 需要查询订单时会发送 `agent.custom_tool_use`。业务应用读取工具名称和输入参数，执行查询，再通过 `user.custom_tool_result` 回传结果。完整接入流程和多媒体场景示例请参见 [[基础] Custom Tool 使用教程](https://ark.volcengine.com/region:cn-beijing/docs/managed-agent-custom-tool-tutorial)。

## 配置内置工具

创建 Agent 前，先确定任务必须使用哪些工具、哪些工具可以修改数据，以及哪些调用需要业务应用确认。显式配置 `default_config.enabled` 和 `permission_policy`，再通过 `configs` 覆盖单个工具，可以避免不同工具类型的默认行为造成权限范围偏差。

> **注意**

> 不要通过省略 `tools`、传入空数组 `[]`，或只配置 MCP、自定义工具等其他工具来关闭主 Agent 的内置工具。主 Agent 在未找到内置工具集配置时会回退为启用全部内置工具，并使用 `always_allow`；如需限制工具范围，请显式配置 `agent_toolset_20260916`，将 `default_config.enabled` 设为 `false`，再逐项启用必要工具。详细行为请参见 [核对默认工具范围](https://ark.volcengine.com/region:cn-beijing/docs/tools#tool-defaults-and-boundaries) 。

### 启用全部内置工具

如果 Agent 需要使用全部基础执行能力，挂载 `agent_toolset_20260916`，并显式启用工具集和权限策略：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "代码助理",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "default_config": {
          "enabled": true,
          "permission_policy": {
            "type": "always_allow"
          }
        }
      }
    ]
  }'
```

### 关闭部分工具

如果 Agent 不应访问网页，可以保持工具集启用，再通过 `configs` 关闭 `web_fetch` 和 `web_search`：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "离线文档助理",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "default_config": {
          "enabled": true,
          "permission_policy": {
            "type": "always_allow"
          }
        },
        "configs": [
          {
            "name": "web_fetch",
            "enabled": false
          },
          {
            "name": "web_search",
            "enabled": false
          }
        ]
      }
    ]
  }'
```

### 只启用必要工具

如果 Agent 只需要读取和检索沙箱文件，先将工具集整体关闭，再显式启用 `read`、`glob` 和 `grep`：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "只读审查 Agent",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "default_config": {
          "enabled": false,
          "permission_policy": {
            "type": "always_ask"
          }
        },
        "configs": [
          {
            "name": "read",
            "enabled": true,
            "permission_policy": {
              "type": "always_allow"
            }
          },
          {
            "name": "glob",
            "enabled": true,
            "permission_policy": {
              "type": "always_allow"
            }
          },
          {
            "name": "grep",
            "enabled": true,
            "permission_policy": {
              "type": "always_allow"
            }
          }
        ]
      }
    ]
  }'
```

这种配置适合代码审查和资料检索等只读任务。

### 设置权限策略

`always_allow` 表示工具调用无需业务应用确认，`always_ask` 表示执行前暂停 Session，等待业务应用确认。权限策略不改变工具执行方。建议在请求中显式配置工具集的默认策略，并通过 `configs[].permission_policy` 覆盖风险更高的单个工具。

权限策略的完整配置方式请参见 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy)。

## 验证和处理工具调用

创建或更新 Agent 后，按以下顺序验证配置是否生效：

1. **查询 Agent 配置**：调用 [查询智能体详情](https://ark.volcengine.com/region:cn-beijing/docs/get-agent-api)，检查响应中的 `tools` 是否符合预期；使用 MCP 工具时，同时检查 `mcp_servers`。

2. **刷新 Session 配置**：创建新 Session，或调用 [升级会话](https://ark.volcengine.com/region:cn-beijing/docs/upgrade-session-api) 升级现有 Session。已创建的 Session 不会自动使用 Agent 的新版本。

3. **触发工具调用**：发送一条能够触发目标工具的测试任务。

4. **检查事件闭环**：检查 Session 事件，确认工具调用、执行结果和最终回复已经形成闭环。

以下以查询订单状态并整理结果为例说明主要事件流。具体事件取决于工具的执行方和权限策略。

### 处理云环境工具调用

云环境中，方舟服务端负责执行内置工具和 MCP 工具。下图展示 Agent 发起调用、按需等待业务应用审批并返回结果的过程。`always_allow` 会直接执行工具；`always_ask` 会先暂停 Session，由业务应用批准或拒绝。

![图片](https://arkdocs.tos-cn-beijing.volces.com/flowcharts/managed-agent/managed-tool-event-sequence.svg)

#### 自动执行

使用 `always_allow` 时，平台直接执行工具并返回结果：

- **内置工具**：调用事件为 `agent.tool_use`，结果事件为 `agent.tool_result`。

- **MCP 工具**：调用事件为 `agent.mcp_tool_use`，结果事件为 `agent.mcp_tool_result`。

同一轮可能调用多个工具。使用 `tool_use_id` 或 `mcp_tool_use_id` 将每个结果与对应调用配对，不要只依赖事件到达顺序。

#### 确认后执行

内置工具或 MCP 工具使用 `always_ask` 时，Session 会暂停并等待业务应用审批：

1. Session 返回 `agent.tool_use` 或 `agent.mcp_tool_use`。

2. Session 返回 `session.status_idle`，其中 `stop_reason.type=requires_action`，`stop_reason.event_ids` 列出需要处理的调用事件。

3. 客户端发送 `user.tool_confirmation`，通过 `tool_use_id` 指定调用事件，并将 `result` 设置为 `allow` 或 `deny`。

4. 所有需要处理的确认事件处理完成后，Session 返回 `session.status_running`。批准的工具继续执行，拒绝结果交给 Agent 调整后续处理。

> **处理确认结果**

> 拒绝调用时，可以通过 `deny_message` 把原因回传给 Agent。Agent 会根据拒绝原因选择其他工具、降级处理，或向最终用户说明无法继续执行。

> 在 `requires_action` 阶段，先处理 `stop_reason.event_ids` 中的全部确认事件，不要用新的 `user.message` 替代确认。`always_allow`、`always_ask` 及覆盖规则请参见 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy)。

### 处理自托管工具调用

自托管环境按工具类型分配执行方：

| 工具类型                                                    | 执行方                 | 结果处理                                          |
| ----------------------------------------------------------- | ---------------------- | ------------------------------------------------- |
| 主 Agent 的 `bash`、`read`、`write`、`edit`、`glob`、`grep` | 企业 Worker 和 Sandbox | Worker 通过 `user.tool_result` 回传结果           |
| `web_fetch`、`web_search`                                   | 方舟侧                 | 方舟将结果写入 Session 事件流                     |
| 多模态工具                                                  | 方舟侧                 | 方舟将图片、视频或其他工具结果写入 Session 事件流 |
| MCP 工具                                                    | 方舟侧                 | 方舟通过 `agent.mcp_tool_result` 返回结果         |
| 自定义工具                                                  | 业务应用               | 业务应用通过 `user.custom_tool_result` 回传结果   |

上述六个企业侧工具只支持主 Agent。子 Agent 不会通过企业 Worker 执行这些工具。

以下流程只适用于主 Agent 的六个企业侧工具：

![图片](https://arkdocs.tos-cn-beijing.volces.com/flowcharts/managed-agent/self-hosted-tool-event-sequence.svg)

1. Session 返回 `agent.tool_use`，下发 Bash 或文件工具调用。

2. Session 返回 `session.status_idle`，其中 `stop_reason.type=requires_action`。

3. Worker 获取对应的 Work，并在企业 Sandbox 中执行工具。

4. Worker 通过 `user.tool_result` 回传结果。`tool_use_id` 必须填写对应的 `agent.tool_use.id`；工具执行失败时，将 `is_error` 设为 `true`，并在 `content` 中返回失败说明。

5. 所有待处理结果回传后，Session 返回 `session.status_running`，Agent 根据工具结果继续处理任务。

Worker 执行期间持续调用 Heartbeat 维持 Work 租约；完整的 Poll、Acknowledge、Heartbeat 和执行环境配置流程请参见 [配置并运行自托管环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-self-hosted-environment)。

### 处理自定义工具调用

自定义工具由业务应用执行，事件闭环如下：

![图片](https://arkdocs.tos-cn-beijing.volces.com/flowcharts/managed-agent/custom-tool-event-sequence.svg)

1. Session 返回 `agent.custom_tool_use`，其中包含工具名称、输入参数和调用事件 ID。

2. Session 返回 `session.status_idle`，其中 `stop_reason.type=requires_action`，`stop_reason.event_ids` 列出待处理的调用事件。

3. 业务应用根据工具名称和输入参数执行对应操作。

4. 业务应用发送 `user.custom_tool_result`，并将调用事件 ID 原样填入 `custom_tool_use_id`。如果同一轮存在多个待处理调用，需要分别回传结果。

5. 所有待处理结果回传后，Session 返回 `session.status_running`，Agent 根据工具结果继续处理任务。

完整接入示例请参见 [[基础] Custom Tool 使用教程](https://ark.volcengine.com/region:cn-beijing/docs/managed-agent-custom-tool-tutorial)。所有工具事件的字段结构请参见 [会话事件结构参考](https://ark.volcengine.com/region:cn-beijing/docs/sessions-events-reference-v2)。

## 更新工具配置

更新 Agent 可以使用的工具时，`tools` 使用整体替换语义。为避免误取消其他可用工具，按照以下步骤读取、合并并写回完整 `tools` 数组：

1. **读取当前配置**：调用 [查询智能体详情](https://ark.volcengine.com/region:cn-beijing/docs/get-agent-api)，获取最新 `version` 和完整 `tools` 数组。

2. **合并工具数组**：在本地副本中保留不需要修改的工具，只调整目标工具。

3. **提交更新**：调用 [更新智能体](https://ark.volcengine.com/region:cn-beijing/docs/update-agent-api)，同时提交当前 `version` 和合并后的完整 `tools` 数组。

4. **核对结果**：再次查询 Agent，确认版本号和工具配置已经更新。

5. **验证调用**：创建新 Session，或升级现有 Session 后验证工具调用。

更新请求中 `tools` 的取值语义如下：

| 取值          | 配置结果                                                                                                                     | 使用建议                               |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| 省略或 `null` | 保留当前 `tools` 配置。                                                                                                      | 只修改其他 Agent 字段时使用。          |
| 空数组 `[]`   | 只清空已保存的 `tools` 配置，不会关闭主 Agent 的内置工具。主 Agent 运行时仍会回退为启用全部内置工具，并使用 `always_allow`。 | 如需限制内置工具，改用显式工具集配置。 |
| 非空数组      | 使用请求中的数组整体替换当前配置。                                                                                           | 先读取并合并所有需要保留的工具。       |

## 排查工具问题

先根据 Session 事件流定位中断点，再执行对应检查：

- **未出现目标工具的调用事件**：核对 Agent 配置、Session 版本和任务触发条件。

- **出现 ** **`stop_reason.type=requires_action`**：根据阻塞事件类型回传确认或工具结果。

- **工具结果包含错误**：根据调用 ID 定位输入、权限或执行环境。

- **出现 ** **`session.error`**：根据错误类型和重试状态决定等待、修正后重试或创建新 Session。

- **工具可以执行但权限范围过大**：核对文件、网络和凭据隔离边界。

### 核对配置、版本和触发条件

如果事件流中没有出现 `agent.tool_use`、`agent.mcp_tool_use` 或 `agent.custom_tool_use`，按以下顺序检查：

1. 查询 Agent，确认目标工具或工具集已经保存。MCP 工具还需要检查 `mcp_servers`。

2. 确认当前 Session 使用的 Agent 版本；Agent 更新不会自动影响已创建的 Session。

3. 按工具类型核对配置与触发条件。

4. 使用相同的明确测试任务比较新旧 Session，避免把任务差异误判为版本问题。

| 工具类型   | 配置检查                                                                                                                   | 任务触发条件                                                                                                     |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 内置工具   | `tools` 中包含 `type: "agent_toolset_20260916"`；根据 `default_config.enabled` 和 `configs` 的覆盖结果确认目标工具已启用。 | 测试任务明确要求读取实际文件、执行命令或访问网页，并提供文件路径、命令目标或检索主题。纯文本问答不要求调用工具。 |
| MCP 工具   | `mcp_servers[].name` 与 `tools[].mcp_server_name` 一致；根据 `default_config` 和 `configs` 确认目标 MCP 工具已启用。       | 测试任务明确要求访问对应外部系统，并提供项目、记录或其他业务标识。                                               |
| 自定义工具 | `tools` 中包含 `type: "custom"`，并保存了正确的 `name`、`description` 和 `input_schema`。`description` 需要说明适用条件。  | 测试任务明确要求执行该业务操作，并提供 `input_schema` 中的必需信息。                                             |

Agent 会根据任务内容和工具说明判断是否调用工具。单次未调用不能直接证明配置无效；应先使用明确要求目标能力的测试任务复现。

#### 核对默认工具范围

主 Agent 与子 Agent 在未配置内置工具集时采用不同的运行时行为：

| 配置                                                 | 主 Agent                                            | 子 Agent                                                   | 建议                                 |
| ---------------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------- | ------------------------------------ |
| 省略 `tools`、传 `[]`，或只配置其他工具类型          | 回退为启用全部内置工具，权限策略为 `always_allow`。 | 不提供内置工具；Session 启用的 Memory 工具不受此规则影响。 | 不依赖省略行为，显式配置内置工具集。 |
| 配置 `agent_toolset_20260916`，省略 `default_config` | 服务端保存 `enabled=true` 和 `always_allow`。       | 按相同配置提供内置工具。                                   | 在请求中显式写出启用状态和权限策略。 |
| 配置 `default_config.enabled=false`，再逐项启用      | 只提供 `configs` 中显式启用的工具。                 | 只提供 `configs` 中显式启用的工具。                        | 需要最小工具集时使用。               |

判断子 Agent 是否实际执行工具时，以子线程中的 `agent.tool_use` 和 `agent.tool_result` 为准。System Prompt 或最终回复中声称已执行工具，不代表运行环境实际提供或执行了该工具。

#### 核对默认权限策略

权限策略存在以下默认分支：

| 配置                                                | 内置工具                                    | MCP 工具                                    |
| --------------------------------------------------- | ------------------------------------------- | ------------------------------------------- |
| 整个 `default_config` 省略                          | 服务端保存 `always_allow`。                 | 服务端保存 `always_allow`。                 |
| 已提供 `default_config`，但省略 `permission_policy` | 运行时使用 `always_allow`。                 | 运行时使用 `always_ask`。                   |
| 显式配置 `permission_policy`                        | 使用配置的 `always_allow` 或 `always_ask`。 | 使用配置的 `always_allow` 或 `always_ask`。 |

### 处理 requires_action

Session 返回 `stop_reason.type=requires_action` 时，先读取 `stop_reason.event_ids`，再根据每个阻塞事件的类型执行对应操作：

| 阻塞事件                                 | 场景                                                                                     | 处理方式                                                                                                                                                                               |
| ---------------------------------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent.tool_use` 或 `agent.mcp_tool_use` | 云环境中的工具使用 `always_ask`。                                                        | 发送 `user.tool_confirmation`，将阻塞事件 ID 填入 `tool_use_id`，并设置 `allow` 或 `deny`。确认 MCP 调用时也使用 `tool_use_id`；`mcp_tool_use_id` 仅用于关联 `agent.mcp_tool_result`。 |
| `agent.tool_use`                         | 自托管 Environment 中的主 Agent 调用 `bash`、`read`、`write`、`edit`、`glob` 或 `grep`。 | Worker 完成执行后发送 `user.tool_result`，使用 `tool_use_id` 关联调用；失败时设置 `is_error: true` 并返回错误说明。                                                                    |
| `agent.custom_tool_use`                  | 业务应用执行自定义工具。                                                                 | 发送 `user.custom_tool_result`，使用 `custom_tool_use_id` 关联调用；失败时设置 `is_error: true` 并返回错误说明。                                                                       |

同一轮可能包含多个阻塞事件。处理 `stop_reason.event_ids` 中的全部事件后，Session 才会恢复运行。不要用新的 `user.message` 代替确认或工具结果。

### 排查工具结果错误

工具结果包含错误时，先关联对应的调用事件，再定位执行方：

- **`agent.tool_result`**：通过 `tool_use_id` 检查原始工具输入，再核对文件路径、Environment 权限、网络策略和执行日志。

- **`agent.mcp_tool_result`**：通过 `mcp_tool_use_id` 检查原始工具输入，再核对 MCP 工具启用状态、凭据和 Server 返回信息。

- `user.tool_result` 或 `user.custom_tool_result`：检查业务应用或 Worker 的执行日志，确认 `is_error`、`content` 和调用 ID 与实际结果一致。

工具结果尚未处理完成时，不要重复发送原始任务，否则同一工具可能重复执行。

### 处理 Session 错误

`session.error` 不一定由工具配置引起。先读取 `error.type`、`error.message` 和 `error.retry_status.type`，并结合错误前的最后一个事件定位问题。

| 重试状态    | 处理方式                                                                           |
| ----------- | ---------------------------------------------------------------------------------- |
| `retrying`  | 服务端正在自动重试。继续消费事件，不要重复发送任务。                               |
| `exhausted` | 当前轮次的重试次数已用尽。等待 Session 恢复为 `idle`，修正输入或配置后再发送任务。 |
| `terminal`  | Session 将进入 `terminated`，不能继续发送事件。修正配置后创建新 Session。          |

如果错误发生在 MCP 调用阶段，按错误类型继续检查：

- `mcp_authentication_failed_error`：检查 MCP Server 的鉴权配置、Session 引用的 Vaults 和凭据有效性。

- **`mcp_connection_failed_error`**：检查 MCP Server URL、网络连通性和服务状态。

修正 MCP 配置后，更新 Agent，并创建新 Session 或升级现有 Session。如果 `error.type` 不是 MCP 相关类型，且错误前没有工具调用事件，优先排查模型调用或 Session 运行问题，不要把所有 `session.error` 都归因于工具。事件字段和其他错误类型请参见 [会话事件结构参考](https://ark.volcengine.com/region:cn-beijing/docs/sessions-events-reference-v2)。

### 核对文件和网络隔离

> **注意**

> 关闭 `write`、`edit`、`web_fetch` 或 `web_search` 不等于完成强隔离。`read`、`glob` 和 `grep` 仍可能访问敏感文件；如果 Agent 仍可使用 `bash`，或者 Environment 允许不受限联网，命令仍可能读取或修改文件并访问网络。需要强隔离时，同时收敛内置工具、Environment 文件和网络策略，以及外部凭据权限。

## 相关文档

[Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)

Agent 是包含基本信息、System Prompt、扩展能力的配置模板。

[Skills](https://ark.volcengine.com/region:cn-beijing/docs/skills)

Skills 用于给 Agent 补充领域知识、操作流程和最佳实践。

[MCP](https://ark.volcengine.com/region:cn-beijing/docs/mcp)

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。

[工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy)

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待业务应用确认。

<a id="doc-2553720"></a>

---

## 工具权限策略

> 来源：[https://docs.volcengine.com/docs/82379/2553720?lang=zh](https://docs.volcengine.com/docs/82379/2553720?lang=zh)

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待业务应用确认。

当前权限策略只作用于服务端执行的工具：

- 内置工具集 `agent_toolset_20260916`

- 多模态工具集 `agent_toolset_multimodal_20260825`

- MCP 工具集 `mcp_toolset`

## 权限策略类型

| 策略           | 说明                                       |
| -------------- | ------------------------------------------ |
| `always_allow` | 工具调用自动执行，不需要人工确认。         |
| `always_ask`   | 工具调用前暂停，等待业务应用确认后再继续。 |

权限策略的默认值取决于是否提供整个 `default_config`：

| 配置                                              | 内置工具集                                    | MCP 工具集                                    |
| ------------------------------------------------- | --------------------------------------------- | --------------------------------------------- |
| 省略整个 `default_config`                         | 服务端保存 `enabled=true` 和 `always_allow`。 | 服务端保存 `enabled=true` 和 `always_allow`。 |
| 提供 `default_config`，但省略 `permission_policy` | 运行时使用 `always_allow`。                   | 运行时使用 `always_ask`。                     |
| 显式配置 `permission_policy`                      | 使用配置的策略。                              | 使用配置的策略。                              |

为了让 Agent 回显和运行时行为可直接核对，建议始终显式配置 `default_config.enabled` 和 `default_config.permission_policy`。

## 为整个工具集设置权限策略

你可以在 `default_config.permission_policy` 中为整个工具集配置统一策略。

下面的示例把内置工具集的默认策略改成 `always_ask`：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "谨慎执行 Agent",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "default_config": {
          "enabled": true,
          "permission_policy": {
            "type": "always_ask"
          }
        }
      }
    ]
  }'
```

不要只传 `default_config.enabled` 而省略 `permission_policy`。内置工具和 MCP 工具对空策略采用不同的运行时默认值。

## 为单个工具覆盖权限策略

除了为整个工具集设置默认策略外，你还可以在 `configs` 中对单个工具做更细粒度的覆盖。

下面的示例保留内置工具集默认自动执行，但要求 `bash` 每次执行前都先确认：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "命令审慎 Agent",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "default_config": {
          "enabled": true,
          "permission_policy": {
            "type": "always_allow"
          }
        },
        "configs": [
          {
            "name": "bash",
            "enabled": true,
            "permission_policy": {
              "type": "always_ask"
            }
          }
        ]
      }
    ]
  }'
```

这种覆盖关系适合把大部分低风险工具保持自动执行，只对高风险工具额外加一道确认。

## 为 MCP 工具集设置权限策略

下面的示例将可信 MCP Server 的工具集显式设置为 `always_allow`，便于直接核对自动执行范围：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub 协作 Agent",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "mcp_servers": [
      {
        "type": "url",
        "name": "github",
        "url": "https://mcp.example.com/github"
      }
    ],
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "default_config": {
          "enabled": true,
          "permission_policy": {
            "type": "always_allow"
          }
        }
      },
      {
        "type": "mcp_toolset",
        "mcp_server_name": "github",
        "default_config": {
          "enabled": true,
          "permission_policy": {
            "type": "always_allow"
          }
        }
      }
    ]
  }'
```

> **注意**

> 只有在确认 MCP Server 及其暴露工具都可信时，才将策略显式设置为 `always_allow`。如果 MCP Server 后续新增工具，工具集级的自动放行策略也会作用于新增工具。

## 使用建议

- 对只读工具，可以优先考虑 `always_allow`。

- 对会修改文件、执行命令或访问外部系统的工具，建议优先考虑 `always_ask`。

- 对 MCP 工具，建议先按最小权限原则收敛 `configs`，再决定是否自动放行。

工具集配置以及 `user.tool_confirmation` 对应的运行时事件流，详见 [Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)。如果你要管理 MCP Server 及其工具，详情请参见 [MCP](https://ark.volcengine.com/region:cn-beijing/docs/mcp)。

## 相关文档

[Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)

Agent 是包含基本信息、System Prompt、扩展能力的配置模板。

[Skills](https://ark.volcengine.com/region:cn-beijing/docs/skills)

Skills 用于给 Agent 补充领域知识、操作流程和最佳实践。

[MCP](https://ark.volcengine.com/region:cn-beijing/docs/mcp)

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。

[Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)

Tools 决定 Agent 在 Session 中能主动调用哪些执行能力。

<a id="doc-2553721"></a>

---

## 配置云环境

> 来源：[https://docs.volcengine.com/docs/82379/2553721?lang=zh](https://docs.volcengine.com/docs/82379/2553721?lang=zh)

本文介绍如何为方舟 Managed Agents 创建和管理云端沙箱环境，包括预装依赖包、环境变量、启动脚本和产物存储。

**Environment** 描述 Agent 运行时使用的沙箱模板。一个 Environment 可以被多个 Session 复用，但每个 Session 会启动独立的沙箱实例，文件系统状态彼此隔离。

Managed Agents 同时支持云环境和自托管环境。本页只介绍由方舟管理沙箱的云环境。如果你需要在企业基础设施中执行工具或访问内网资源，选择自托管环境，详情请参见 [自托管环境概述](https://ark.volcengine.com/region:cn-beijing/docs/self-hosted-environment-overview)。

## 创建环境

你可以在控制台的 [Environments](https://ark.volcengine.com/region:cn-beijing/managed-agents/environments?projectName=default) 页面创建 Environment，也可以使用 API 创建。

在控制台单击 **创建 Environment**，按需配置以下内容：

- **类型**：选择云托管环境。

- **预装包**：配置沙箱启动前安装的系统包和语言依赖。

- **环境变量**：配置注入沙箱进程的非敏感键值对。

- **初始化脚本**：配置沙箱启动阶段执行的脚本。

- **产物存储**：可选。需要长期保留 Agent 产物时，选择自己的 TOS 目录；不选择时使用方舟公共 TOS。

> **说明**

> 创建 Environment 时使用清晰且唯一的名称，便于区分开发、测试和生产等不同用途。

参考以下示例代码，使用 API 创建环境：

```bash
environment=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/environments" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<'EOF'
{
  "name": "<ENVIRONMENT_NAME>",
  "config": {
    "type": "cloud",
    "networking": {
      "type": "unrestricted"
      },
    "packages": {
      "pip": ["pandas"],
      "apt": ["curl"]
    },
    "env": {
      "MY_KEY_0": "value_0",
      "MY_KEY_1": "value_1"
    }
  }
}
EOF
)

ENVIRONMENT_ID=$(jq -er '.id' <<<"$environment")

echo "Environment ID: $ENVIRONMENT_ID"
```

主要配置项：

- **`name`**：Environment 名称，在当前项目内需要唯一。

- **`description`**：Environment 的说明信息。

- **`config.type`**：运行环境类型，云托管环境取值为 `cloud`。

- **`config.networking.type`**：沙箱的出站网络访问策略。`unrestricted` 允许完整出站网络访问，但仍受通用安全拦截列表限制。

- **`config.packages`**：沙箱启动前预安装的依赖包。依赖会在使用同一 Environment 的 Session 之间缓存。若同时配置多个包管理器，系统按 `apt`、`cargo`、`gem`、`go`、`npm`、`pip` 的顺序执行。依赖版本可以显式锁定，未指定版本时安装最新版本。

  | 字段    | 包管理器           | 示例                                        |
  | ------- | ------------------ | ------------------------------------------- |
  | `apt`   | 系统包（apt\-get） | `"ffmpeg"`                                  |
  | `cargo` | Rust（cargo）      | `"ripgrep@14.0.0"`                          |
  | `gem`   | Ruby（gem）        | `"rails:7.1.0"`                             |
  | `go`    | Go modules         | `"golang.org/x/tools/cmd/goimports@latest"` |
  | `npm`   | Node.js（npm）     | `"express@4.18.0"`                          |
  | `pip`   | Python（pip）      | `"pandas==2.2.0"`                           |

- **`config.env`**：注入沙箱进程的环境变量。需要让 Agent 读取非敏感业务参数时配置，例如时区或服务地址。

- **`config.setup_script`**：沙箱启动阶段执行的初始化脚本。需要在每次启动沙箱时准备目录、下载资源或执行自定义初始化命令时配置。

- **`config.tos`**：Agent 最终产物的 TOS 存储位置。需要长期保留 Agent 生成的报告、文件或代码时配置；不配置时产物使用方舟公共 TOS。

## 配置产物存储

需要将产物长期保存在自己的 TOS Bucket 时，为 Environment 配置 `config.tos`；继续使用方舟公共 TOS 时，省略该字段。`config.tos` 控制平台将 outputs 产物注册到 Files API 时使用的持久化位置，不等同于 `resources` 中 `type: tos` 的沙箱挂载目录。

| 你的配置            | 产物位置                                                                                                                                                    | 你需要做什么                                                                     |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 不配置 `config.tos` | 方舟公共 TOS                                                                                                                                                | 在平台 TTL 到期前下载所需文件。删除 Session 时，平台会清理默认存储中的关联产物。 |
| 配置 `config.tos`   | 你的 TOS Bucket。平台从 outputs 目录注册产物时，Files API 写入前缀为 `{prefix}outputs/{env-id}/{session-id}/`；最终对象路径以响应中的 `tos.object_key` 为准 | 在 TOS 中管理对象生命周期。删除 Session 不会删除 Bucket 中的对象。               |

将产物写入自己的 TOS Bucket 时，需要满足以下条件：

- 目标 Bucket 已在方舟项目中完成授权，详情请参见 [用户对象存储（TOS）授权](https://ark.volcengine.com/region:cn-beijing/docs/project-configuration#4eb1b277)。

- Bucket 与 Managed Agents 服务须部署在同一地域，跨地域 Bucket 会在预检查阶段被拒绝。

- `bucket` 和 `prefix` 必须同时提供，不能只配置其中一个字段。

- `prefix` 使用相对路径，例如 `ark-files/`。

- 平台从 outputs 目录注册产物时，会在该前缀后追加 `outputs/{env-id}/{session-id}/` 作为 Files API 写入前缀。最终对象路径以响应中的 `tos.object_key` 为准，通常还会包含 `ark_processed/{file_id}/{file_type}/{filename}`。

- 直接调用 Files API 并传入 `tos[prefix]` 时，不会追加 Environment ID 或 Session ID，但 Files API 仍会在该前缀下生成服务侧处理目录。

以下示例演示创建带产物存储的 Environment、更新存储位置，以及清空配置，使后续 Session 改用方舟公共 TOS：

```Bash
# Create an environment that stores outputs in your TOS bucket.
environment=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/environments" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<'EOF'
{
  "name": "<ENVIRONMENT_NAME>",
  "config": {
    "type": "cloud",
    "networking": {
      "type": "unrestricted"
    },
    "tos": {
      "bucket": "<TOS_BUCKET>",
      "prefix": "ark-files/"
    }
  }
}
EOF
)

ENVIRONMENT_ID=$(jq -er '.id' <<<"$environment")
echo "Environment ID: $ENVIRONMENT_ID"
# Example output: Environment ID: env-20260723142038-xxxxx

# Replace the output storage configuration for future sessions.
updated_environment=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/environments/$ENVIRONMENT_ID" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<'EOF'
{
  "config": {
    "tos": {
      "bucket": "<NEW_TOS_BUCKET>",
      "prefix": "new-prefix/"
    }
  }
}
EOF
)

jq '{id, tos: .config.tos}' <<<"$updated_environment"
# Example output:
# {
#   "id": "env-20260723142038-xxxxx",
#   "tos": {"bucket": "<NEW_TOS_BUCKET>", "prefix": "new-prefix/"}
# }

# Clear the configuration so future sessions use the default storage.
cleared_environment=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/environments/$ENVIRONMENT_ID" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
      "config": {
        "tos": {}
      }
    }'
)

jq '{id, tos: (.config.tos // null)}' <<<"$cleared_environment"
# Example output:
# {
#   "id": "env-20260723142038-xxxxx",
#   "tos": null
# }
```

> **注意**

> 先更新 Environment，再创建需要使用新存储配置的 Session。已有 Session 使用创建时冻结的配置快照，不会自动切换到新的 Bucket 或 `prefix`。

## 在 Session 中使用环境

创建 Environment 后，在创建 Session 时传入 `environment_id`，即可复用该 Environment 的运行配置和产物存储配置。

```bash
session=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": "$AGENT_ID",
  "environment_id": "$ENVIRONMENT_ID",
  "title": "Quickstart session"
}
EOF
)

SESSION_ID=$(jq -er '.id' <<<"$session")

echo "Session ID: $SESSION_ID"
```

如果只有某次任务需要使用不同的产物存储位置，可以在创建 Session 时使用 Environment 覆写，不需要修改可复用的 Environment。详情请参见 [覆写产物存储](https://ark.volcengine.com/region:cn-beijing/docs/start-session#override-output-storage)。

## 环境生命周期

- 多个 Session 可以引用同一个环境，但每个 Session 都会获得独立的沙箱实例。

- Session 之间不共享文件系统状态。

- 环境本身不做版本化管理。如果你频繁更新环境配置，建议在业务侧记录变更，以便追溯某个 Session 使用的是哪一版环境。

- Environment 被 `idle` 或 `running` 状态的 Session 引用时不能删除。删除前，先终止或删除这些 Session；其他状态的 Session 不会阻止删除 Environment。

## 管理环境

你可以列出、查看、更新或删除 Environment。

```bash
# List environments
environments=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/environments" \
    -H "Authorization: Bearer $ARK_API_KEY"
)

# Retrieve a specific environment
environment=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/environments/$ENVIRONMENT_ID" \
    -H "Authorization: Bearer $ARK_API_KEY"
)

# Update environment description
environment=$(
  curl -sS --fail-with-body -X POST "https://ark.cn-beijing.volces.com/api/v3/environments/$ENVIRONMENT_ID" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<'EOF'
{
  "description": "<UPDATED_ENVIRONMENT_DESC>"
}
EOF
)

# Delete an environment (only if no sessions reference it)
curl -sS --fail-with-body -X DELETE \
  "https://ark.cn-beijing.volces.com/api/v3/environments/$ENVIRONMENT_ID" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

## 预置运行时

云沙箱默认包含常见语言运行时、数据库和工具。需要确认具体内置版本、目录用途或完整清单时，详情请参见 [云沙箱参考](https://ark.volcengine.com/region:cn-beijing/docs/cloud-sandbox-reference)。

<a id="doc-2553722"></a>

---

## 云沙箱参考

> 来源：[https://docs.volcengine.com/docs/82379/2553722?lang=zh](https://docs.volcengine.com/docs/82379/2553722?lang=zh)

本文介绍 Managed Agents 云端沙箱预安装的编程语言、数据库、常用工具（Utilities）和基础资源规格，便于在配置环境或评估任务依赖时快速参考。

云端沙箱运行在方舟提供的隔离 Linux 容器中。运行时、数据库客户端和常用命令行工具已预安装，Agent 可以直接使用。

> 本文中规格描述适用于 `cloud` 类型的 Environments。

## 编程语言

预装常见编程语言及其对应的包管理工具，可直接用于脚本执行、项目构建和依赖安装。

| 编程语言 | 版本    | 包管理工具      |
| -------- | ------- | --------------- |
| Python   | 3.12+   | pip, uv         |
| Node.js  | 20+     | npm, yarn, pnpm |
| Go       | 1.25+   | go modules      |
| Rust     | 1.77+   | cargo           |
| Java     | 21+     | maven, gradle   |
| Ruby     | 3.3+    | bundler, gem    |
| PHP      | 8.3+    | composer        |
| C/C++    | GCC 13+ | make, cmake     |

## 数据库

默认提供轻量数据库和数据库客户端，适合本地数据处理，或连接外部数据库/实例。

| 数据库            | 描述                                    |
| ----------------- | --------------------------------------- |
| SQLite            | 已预安装，可立即使用                    |
| PostgreSQL 客户端 | 用于连接外部数据库的 `psql` 客户端。    |
| Redis 客户端      | 用于连接外部 Redis 实例的 `redis-cli`。 |

- SQLite 可在本地使用。

- 数据库服务器，例如 PostgreSQL 和 Redis，默认不在沙箱环境中运行。沙箱提供用于连接的客户端工具。

## Utilities 工具

内置多类常用工具，覆盖系统操作、开发构建、文件搜索、进程查看和文本处理等场景。

### 系统工具

| 工具                  | 描述                                     |
| --------------------- | ---------------------------------------- |
| `git`                 | 版本控制。                               |
| `curl`, `wget`        | HTTP 客户端。                            |
| `jq`                  | JSON 处理。                              |
| `tar`, `zip`, `unzip` | 归档、压缩与解压工具。                   |
| `ssh`, `scp`          | 远程访问与文件传输工具（需要启用网络）。 |
| `tmux`, `screen`      | 终端多路复用工具。                       |

### 开发工具

| 工具             | 描述             |
| ---------------- | ---------------- |
| `make`, `cmake`  | 构建系统。       |
| `ripgrep` (`rg`) | 快速文件搜索。   |
| `tree`           | 目录结构可视化。 |
| `htop`           | 进程监控。       |

### 文本处理

| 工具                 | 描述                 |
| -------------------- | -------------------- |
| `sed`, `awk`, `grep` | 流式文本处理工具。   |
| `vim`, `nano`        | 文本编辑器。         |
| `diff`, `patch`      | 文件比较与补丁工具。 |

## 沙箱规格

以下为云端沙箱的基础资源规格，可用于评估运行时资源和网络能力。

| 属性     | 具体配置         |
| -------- | ---------------- |
| 操作系统 | Ubuntu 22.04 LTS |
| 架构     | x86_64 (amd64)   |
| 内存     | 4 GB             |
| 磁盘空间 | 10 GB            |
| 网络     | 默认开启         |

## 关键目录及 Agent 权限

云端沙箱的文件系统按用途划分为**工作区**、**挂载目录**和**临时目录**三类。挂载目录 `/mnt` 下进一步区分只读的知识与资源（Memory、Skills、上传文件）和读写的产出与存储（Outputs、Storage）。Agent 通过标准文件工具访问这些目录，权限由平台强制约束。

### 目录布局

```text
/
├── workspace/                # 工作区，读写，Agent 的主要工作目录
│   └── AGENTS.md             # System Prompt，只读
├── mnt/                      # 挂载目录
│   ├── memory/               # Agent Memory，只读，见 persistent-memory
│   ├── skills/               # Agent Skills，只读，见 skills
│   └── session/
│       ├── uploads/          # 用户上传文件挂载目录，只读，见 upload-and-mount-files
│       ├── outputs/          # Agent 产出目录，读写，通过 Files API 注册和下载
│       └── storage/          # 通过 Session resources 挂载的 TOS 目录，读写
└── tmp/                      # 临时目录，读写
```

### 目录权限

| 目录                   | 用途                                                                                                                               | Agent 读权限 | Agent 写权限 |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------ | ------------ |
| `$HOME`                | 用户 Home                                                                                                                          | ✓            | ✓            |
| `/workspace`           | 工作区，Agent 的主要工作目录<br><br>**注意**：`/workspace/AGENTS.md`（System Prompt）为 read\-only 权限。                          | ✓            | ✓            |
| `/mnt`                 | 挂载目录                                                                                                                           | ✓            | ✓            |
| `/mnt/memory`          | Agent Memory 目录，参见 [Memory](https://ark.volcengine.com/region:cn-beijing/docs/persistent-memory)                              | ✓            | ✗            |
| `/mnt/skills`          | Agent Skills 目录，参见 [Skills](https://ark.volcengine.com/region:cn-beijing/docs/skills)                                         | ✓            | ✗            |
| `/mnt/session/uploads` | Files API 上传文件挂载目录，参见 [上传并挂载文件](https://ark.volcengine.com/region:cn-beijing/docs/upload-and-mount-files)        | ✓            | ✗            |
| `/mnt/session/outputs` | Agent 产出目录。写入这里的文件会注册到 Files API；需要将产物写入自己的 TOS Bucket 时，为 Environment 配置 `config.tos`             | ✓            | ✓            |
| `/mnt/session/storage` | Session 的 TOS 资源挂载目录，详情请参见 [上传并挂载文件](https://ark.volcengine.com/region:cn-beijing/docs/upload-and-mount-files) | ✓            | ✓            |
| `/tmp`                 | 临时目录                                                                                                                           | ✓            | ✓            |
| 其它任意路径           | \-                                                                                                                                 | ✗            | ✗            |

### 区分产物存储与 TOS 资源挂载

`/mnt/session/outputs` 和 `/mnt/session/storage` 都可能关联 TOS，但用途不同：

| 能力                 | 沙箱路径               | 用途                                                                                                                             |
| -------------------- | ---------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Environment 产物存储 | `/mnt/session/outputs` | Agent 写入最终交付物。不配置 `config.tos` 时使用方舟公共 TOS；需要写入自己的 TOS Bucket 时配置该字段。Agent 使用的沙箱路径不变。 |
| Session TOS 资源     | `/mnt/session/storage` | 将已有 TOS 对象作为 Session 输入或共享数据挂载到沙箱，通过 `resources` 配置。                                                    |

Environment 产物存储的配置方法，详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment#configure-output-storage)。

### 使用规则

Agent 在沙箱中读写文件时应遵循以下规则，避免误改只读资源或将中间产物遗留到用户可见目录：

- **主要工作目录**：使用 `/workspace` 存放项目文件、中间构建产物、脚本执行结果等 Agent 的主要工作内容。

- **读取用户输入**：从 `/mnt/session/uploads` 读取用户通过 Files API 上传的文件；该目录只读，如需修改，请在 `/workspace` 或 `/mnt/session/outputs` 下创建派生文件。

- **写入最终产物**：将需要回传给用户的最终交付物写入 `/mnt/session/outputs`；写入此目录的文件会注册到 Files API，并按 Environment 或 Session 覆写指定的位置存储。

- **跨 Session 复用数据**：需要让多个 Session 读写同一批业务文件时，通过 Session `resources` 将 TOS 目录挂载到 `/mnt/session/storage`。

- **临时文件**：使用 `/tmp` 存放临时缓存和命令中间结果；生命周期随 Session。

- **只读挂载**：不要修改 `/mnt/memory`、`/mnt/skills`、`/mnt/session/uploads`；这些目录承载 Agent 的记忆、能力和用户输入资源。

- **产物路径回报**：如生成了最终交付文件，在最终响应中告知用户对应的文件路径。

<a id="doc-2553723"></a>

---

## 启动 Session

> 来源：[https://docs.volcengine.com/docs/82379/2553723?lang=zh](https://docs.volcengine.com/docs/82379/2553723?lang=zh)

Session 是托管 Agent 在某个 Environment 中跑某个 Agent 的一次实例。一个 Session 在多次交互中维护对话历史、保留沙箱状态，允许 Agent 跨轮次记住之前做过的事情。

启动一个 Session 分两步：

1. **创建 Session**：配置沙箱、绑定 Agent 与 Environment，此时 Agent **不**开始任何工作。

2. **发送首个事件**：通过 `user.message` 事件把任务交给 Agent，Session 进入 `running` 状态开始执行。

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/base-url-and-authentication)。

## 创建 Session

创建 Session 必需两个上游资源 ID：

- **Agent ID**：由 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent) 创建后获得，形如 `agent-20260701120000-xxxxx`。

- **Environment ID**：由 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment) 创建后获得，形如 `env-20260701120000-xxxxx`。

### 使用 Agent 最新版本（推荐入门）

Agent 是带版本的资源，以字符串形式传入 `agent` ID 时，Session 会用该 Agent 的**最新版本**启动。

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-20260701120000-abcde",
    "environment_id": "env-20260701120000-fghij"
  }'
```

响应返回完整的 Session 记录，其中 `id` 字段（形如 `sesn-20260701120100-xxxxx`）是后续所有操作的入口：

```json
{
  "id": "sesn-20260701120100-xxxxx",
  "type": "session",
  "status": "idle",
  "environment_id": "env-20260701120000-xxxxx",
  "agent": {
    "id": "agent-20260701120000-xxxxx",
    "type": "agent",
    "version": 3
  },
  "created_at": "2026-06-29T10:00:00Z",
  "updated_at": "2026-06-29T10:00:00Z",
  "resources": [],
  "vault_ids": null
}
```

响应中 `status` 为 `idle`，表示 Session 已就绪等待首个事件。完整状态包括 `initializing`、`idle`、`running`、`rescheduling`、`upgrading`、`failed` 和 `terminated`。正常轮次结束后，Session 回到 `idle`；进入 `idle` 时，平台会保存沙箱状态快照，便于后续恢复。状态机与沙箱状态保留期详情请参见 [数据保留与删除影响](https://ark.volcengine.com/region:cn-beijing/docs/manage-session#checkpoint-retention)。

### 固定 Agent 版本（灰度发布场景）

当你需要把 Session 锁定到 Agent 的某个具体版本（用于回滚、灰度对比、产品定版）时，以对象形式传入 `agent`，显式指定 `version`：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": {"type": "agent", "id": "agent-20260701120000-abcde", "version": 1},
    "environment_id": "env-20260701120000-fghij"
  }'
```

固定版本后，即使该 Agent 后续发布了新版本，本 Session 仍按 `version: 1` 的行为运行。这让你可以分阶段灰度推出新版本，而不影响存量 Session 的行为一致性。

## 覆写 Agent 配置（可选）

大多数 Session 直接传入 Agent ID 或版本引用，沿用该版本的完整配置。只有当前任务需要不同的系统提示词、模型、工具、MCP 服务、技能、多智能体协作配置或上下文压缩策略时，才传入 `agent_with_overrides` 对象。

以下示例在本次 Session 中替换系统提示词和模型，将工具列表替换为内置工具集，并清空基础 Agent 的技能与 MCP 服务：

**Curl**

```Bash
session=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": {
    "type": "agent_with_overrides",
    "id": "$AGENT_ID",
    "version": 1,
    "system": "你是一个代码审查助手，只报告可复现的问题并给出修改建议。",
    "model": {
      "id": "doubao-seed-2-1-pro-260915"
    },
    "tools": [
      {
        "type": "agent_toolset_20260916",
        "name": "agent_toolset_20260916",
        "configs": [
          {
            "name": "bash",
            "enabled": true,
            "permission_policy": {
              "type": "always_allow"
            }
          }
        ],
        "default_config": {
          "enabled": false,
          "permission_policy": {
            "type": "always_allow"
          }
        }
      }
    ],
    "skills": [],
    "mcp_servers": []
  },
  "environment_id": "$ENVIRONMENT_ID",
  "title": "Session with agent configuration overrides"
}
EOF
)

SESSION_ID=$(jq -er '.id' <<<"$session")
echo "Session ID: $SESSION_ID"
```

Agent 覆写遵循以下规则：

- `agent.id` 指向基础 Agent；通过可选的 `agent.version` 指定基础版本，省略时使用最新版本。

- 省略的字段沿用基础 Agent 的配置。`model` 中省略的子字段也沿用基础 Agent 的对应配置。

- `system` 和 `model` 只修改当前 Session 的智能体快照，不创建 Agent 新版本，也不影响其他 Session。

- **工具与资源列表**：字段 `tools`、`mcp_servers` 和 `skills` 是完整列表覆写。传入非空数组时整体替换，传入 `[]` 时清空，省略时才沿用基础 Agent 的列表。

- **上下文管理策略**：字段 `context_management` 是完整列表覆写。省略或传 `null` 时沿用基础 Agent 版本的策略，传入一项时整体替换，传入 `[]` 时清除自定义策略并改用运行时默认行为。Session 响应可能展示平台当前默认压缩策略或默认阈值，当前默认阈值为 `192000` token；该展示值不写回 Session 快照。需要固定阈值时，在创建 Session 时显式传入 `compact_threshold`。

- **Multi Agent 配置**：字段 `multiagent` 是完整对象覆写。省略时沿用基础 Agent 的配置；提供时，`type` 固定为 `coordinator`，`agents` 最多包含 20 项，传入 `[]` 时清空子智能体列表。

- `multiagent.agents[].type` 为 `agent` 时，填写当前账号和项目中的子 Agent ID，可通过 `version` 固定版本，省略版本时使用最新版本；设为 `self` 时复用当前主 Agent 的配置。

- 创建成功后，检查响应中的 `agent` 对象，确认本次 Session 实际保存的配置快照。

- 多智能体配置生效后，主 Agent 可把任务委派给子 Agent；平台为子 Agent 创建独立线程，并将子 Agent 的结果返回主线程。

> **注意**

> 覆写列表时，省略和清空含义不同：

- > 不要为了表示“不修改”而给 `tools`、`mcp_servers`、`skills` 或 `multiagent.agents` 传 `[]`。空数组会清空基础 Agent 的对应列表；不需要覆写时应省略该字段。

- > `context_management: []` 也不是关闭自动压缩，只会清除当前 Session 继承的自定义压缩策略，改用平台默认压缩策略。需要配置阈值和摘要指令时，详情请参见 [配置上下文压缩](https://ark.volcengine.com/region:cn-beijing/docs/context-compaction)。

## 覆写 Environment 配置（可选）

大多数 Session 直接传入 `environment_id`，沿用 Environment 的完整运行配置。只有当前任务需要不同的依赖包、环境变量、初始化脚本或产物存储位置时，改用 `environment` 对象。

Environment 覆写遵循以下规则：

- `environment.id` 指向作为基础配置的 Environment。

- `environment.config.type` 必须与基础 Environment 的类型相同。

- `environment_id` 与 `environment` 允许同时提供，但同时提供时，`environment.id` 必须与 `environment_id` 相同，否则请求会被拒绝。

- 省略的 `config` 子字段沿用基础 Environment 的配置。提供的子字段只写入当前 Session 的环境快照，不修改原 Environment，也不影响之后创建的其他 Session。

### 覆写依赖包、环境变量和初始化脚本

需要为单个 Session 增加依赖、注入任务专用变量或执行初始化命令时，覆写 `config.packages`、`config.env` 和 `config.setup_script`：

**Curl**

```Bash
session=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": "$AGENT_ID",
  "environment": {
    "type": "environment_with_overrides",
    "id": "$ENVIRONMENT_ID",
    "config": {
      "type": "cloud",
      "packages": {
        "pip": ["pyfiglet==1.0.2"]
      },
      "env": {
        "SESSION_PROFILE": "document-analysis"
      },
      "setup_script": "mkdir -p /workspace/bootstrap && echo ready > /workspace/bootstrap/status.txt"
    }
  },
  "title": "Session with runtime configuration overrides"
}
EOF
)

SESSION_ID=$(jq -er '.id' <<<"$session")
echo "Session ID: $SESSION_ID"
```

- **`config.packages`**：配置当前 Session 启动时预装的 `pip`、`apt`、`npm`、`cargo`、`gem` 或 `go` 依赖。传入对象时整体替换基础 Environment 的依赖包配置；传入空对象 `{}` 时清空全部依赖。

- **`config.env`**：以字符串键值对配置当前 Session 的环境变量。传入对象时整体替换基础 Environment 的环境变量；传入空对象 `{}` 时清空全部环境变量。

- **`config.setup_script`**：配置当前 Session 启动时执行的 Bash 初始化脚本。传入非空字符串时替换基础 Environment 的脚本；传入空字符串 `""` 时清空。脚本在 `/workspace` 目录中运行，内容不能超过 256 KiB（262144 字节）。

> **注意**

> `setup_script` 超过大小限制时不会执行，脚本以非零状态码退出时也不会回滚退出前已完成的操作；这两种情况下，Session 仍可能创建成功并继续运行。让脚本写入可检查的标记文件，并在发送任务前确认依赖、目录或文件已经准备完成。

> **说明**

> 这些字段用于覆写 `cloud` Environment 的托管运行环境。`self_hosted` Environment 的依赖、环境变量和初始化过程由企业 Worker 管理。

创建成功后，响应中的 `environment.type` 为 `environment_with_overrides`，`environment.overridden_fields` 列出本次实际覆写的 `config` 子字段。未使用覆写对象时，`environment.type` 为 `environment`，响应不返回 `overridden_fields`。

### 覆写产物存储

需要沿用 Environment 的产物存储配置时，在创建 Session 时传入 `environment_id`：

- Environment 未配置 `config.tos`：产物使用方舟公共 TOS。你需要在平台 TTL 到期前下载所需文件。

- Environment 已配置 `config.tos`：平台从 outputs 目录注册到 Files API 的产物，会写入你配置的 TOS Bucket。Files API 写入前缀为 `{prefix}outputs/{env-id}/{session-id}/`，最终对象路径以响应中的 `tos.object_key` 为准。

只有当前任务需要使用不同的 Bucket 或 `prefix` 时，改用 `environment` 对象创建 Session，并通过 `environment_with_overrides` 临时覆写 `config.tos`：

**Curl**

```Bash
session=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": "$AGENT_ID",
  "environment": {
    "type": "environment_with_overrides",
    "id": "$ENVIRONMENT_ID",
    "config": {
      "type": "cloud",
      "tos": {
        "bucket": "<TOS_BUCKET>",
        "prefix": "session-outputs/"
      }
    }
  },
  "title": "Session with output storage override"
}
EOF
)

SESSION_ID=$(jq -er '.id' <<<"$session")
echo "Session ID: $SESSION_ID"
```

产物存储覆写遵循以下规则：

- `config.tos` 是原子配置。传非空对象时，`bucket` 和 `prefix` 必须同时提供。

- 省略 `config.tos` 时，产物使用 Environment 中配置的存储位置；传空对象 `{}` 时，本次 Session 的产物存入方舟公共 TOS。

> **注意**

> 无论沿用还是覆写存储配置，都让 Agent 将交付物写入 outputs 目录，例如 `/workspace/outputs/` 或 `/mnt/session/outputs/`。`config.tos` 覆写只影响 Files API 登记后的产物存储位置，不改变 `resources` 中 `type: tos` 的沙箱挂载目录。

## 通过 Vaults 注入终端用户凭据（可选）

如果 Agent 需要在 MCP 工具、CLI、SDK 等第三方调用中使用鉴权信息（详情请参见 [使用 Vaults 认证](https://ark.volcengine.com/region:cn-beijing/docs/use-vaults-authentication)），创建 Session 时通过 `vault_ids` 引用预存凭据。方舟会按凭据类型注入密钥。OAuth 凭据启用自动刷新后，方舟会在访问令牌临近过期时换取新令牌。

最简示例，把 Vaults（凭据保管库）挂到 Session：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-20260701120000-abcde",
    "environment_id": "env-20260701120000-fghij",
    "vault_ids": ["vlt-20260701120000-pqrst"]
  }'
```

多个 Vaults 匹配规则、无匹配时的运行时行为、轮换与诊断，详情请参见 [使用 Vaults 认证](https://ark.volcengine.com/region:cn-beijing/docs/use-vaults-authentication)。

## 启动 Session：先订阅事件流，再发送第一个事件

> **注意**

> **创建 Session 仅完成沙箱配置，不会启动任何工作。** 必须发送 `user.message` 事件，Agent 才会开始执行。

这种解耦设计让客户端可以先注入 Vaults 凭据、检查沙箱环境，再发送首个事件启动 Agent。需要实时接收本轮事件时，按以下顺序执行：

1. 打开 `GET /sessions/{session_id}/events/stream` 事件流。

2. 等待服务端返回 `: ready`，确认订阅已经建立。

3. 向 `POST /sessions/{session_id}/events` 提交一条 `user.message`。

4. 继续读取同一条事件流，直到收到 `session.status_idle` 或 `session.status_terminated`。

> **注意**

> SSE 只推送连接建立后产生的事件，不回放历史事件。如果先发送消息再建立连接，客户端可能漏掉已经产生的消息、工具调用或状态事件。

以下示例需要本地已安装 `curl` 和 `jq`：

**Curl**

```Bash
run_session_turn() (
  local session_id="$1"
  local user_text="$2"
  local stream_dir
  local stream_pipe=""
  local stream_error
  local stream_pid=""
  local stream_number=0
  local stream_ready=false
  local seen_ids_file
  local events_payload
  local turn_done=false
  local result_code=1
  local message_count=0
  local reconnect_count=0
  local max_reconnects="\\${ARK_MAX_SSE_RECONNECTS:-3}"
  local max_history_pages="\\${ARK_MAX_HISTORY_PAGES:-100}"
  local line
  local data

  : "\\${ARK_BASE_URL:?Set ARK_BASE_URL before calling run_session_turn}"
  : "\\${ARK_API_KEY:?Set ARK_API_KEY before calling run_session_turn}"
  : "\\${session_id:?Pass a Session ID as the first argument}"
  : "\\${user_text:?Pass a user message as the second argument}"
  command -v curl >/dev/null || {
    printf 'curl is required.\n' >&2
    return 1
  }
  command -v jq >/dev/null || {
    printf 'jq is required.\n' >&2
    return 1
  }
  [[ $max_reconnects =~ ^[0-9]+$ ]] || {
    printf 'ARK_MAX_SSE_RECONNECTS must be a non-negative integer.\n' >&2
    return 1
  }
  [[ $max_history_pages =~ ^[1-9][0-9]*$ ]] || {
    printf 'ARK_MAX_HISTORY_PAGES must be a positive integer.\n' >&2
    return 1
  }

  stream_dir=$(mktemp -d) || {
    printf 'Failed to create a temporary directory for the SSE stream.\n' >&2
    return 1
  }
  stream_error="$stream_dir/stream-error.log"
  seen_ids_file="$stream_dir/seen-event-ids"
  : >"$stream_error"
  : >"$seen_ids_file"

  stop_stream() {
    exec 3<&- || true
    if [[ -n $stream_pid ]] && kill -0 "$stream_pid" 2>/dev/null; then
      kill "$stream_pid" 2>/dev/null || true
    fi
    if [[ -n $stream_pid ]]; then
      wait "$stream_pid" 2>/dev/null || true
    fi
    if [[ -n $stream_pipe ]]; then
      rm -f "$stream_pipe"
    fi
    stream_pid=""
    stream_pipe=""
  }

  cleanup_stream() {
    stop_stream
    rm -rf "$stream_dir"
  }
  trap cleanup_stream EXIT

  start_stream() {
    stop_stream
    stream_number=$((stream_number + 1))
    stream_pipe="$stream_dir/events-$stream_number"
    stream_ready=false

    if ! mkfifo "$stream_pipe"; then
      printf 'Failed to create the SSE pipe at %s.\n' "$stream_pipe" >&2
      return 1
    fi

    curl -sS -N --fail-with-body --max-time 600 \
      "$ARK_BASE_URL/sessions/$session_id/events/stream" \
      -H "Authorization: Bearer $ARK_API_KEY" \
      -H "Accept: text/event-stream" >"$stream_pipe" 2>>"$stream_error" &
    stream_pid=$!
    exec 3<"$stream_pipe"

    while IFS= read -r line <&3; do
      line=\\${line%$'\r'}
      if [[ $line == ": ready" ]]; then
        stream_ready=true
        break
      fi
    done

    if [[ $stream_ready != true ]]; then
      printf 'SSE stream closed before it was ready.\n' >&2
      if [[ -s $stream_error ]]; then
        tail -n 1 "$stream_error" >&2
      fi
      return 1
    fi
  }

  event_seen() {
    local event_id="$1"
    [[ -n $event_id ]] && grep -Fqx -- "$event_id" "$seen_ids_file"
  }

  remember_event() {
    local event_id="$1"
    if [[ -n $event_id ]] && ! event_seen "$event_id"; then
      printf '%s\n' "$event_id" >>"$seen_ids_file"
    fi
  }

  process_event() {
    local event_json="$1"
    local event_id
    local event_type
    local stop_reason
    local retry_status
    local pending_ids

    event_id=$(jq -r '.id // empty' <<<"$event_json" 2>/dev/null || true)
    if event_seen "$event_id"; then
      return 0
    fi
    remember_event "$event_id"

    event_type=$(jq -r '.type // empty' <<<"$event_json" 2>/dev/null || true)
    case "$event_type" in
      agent.message)
        jq -r '.content[]? | select(.type == "text") | .text' <<<"$event_json"
        message_count=$((message_count + 1))
        ;;
      agent.tool_use | agent.mcp_tool_use | agent.custom_tool_use)
        printf '[tool] %s\n' "$(jq -r '.name // .type' <<<"$event_json")"
        ;;
      session.error)
        retry_status=$(jq -r '.error.retry_status.type // empty' <<<"$event_json")
        printf '[error] %s: %s\n' \
          "$(jq -r '.error.type // "unknown_error"' <<<"$event_json")" \
          "$(jq -r '.error.message // "No error message returned."' <<<"$event_json")" >&2
        case "$retry_status" in
          retrying)
            printf '[session] The service is retrying automatically.\n' >&2
            ;;
          exhausted)
            printf '[session] Retries were exhausted for this turn.\n' >&2
            ;;
          terminal)
            printf '[session] The Session cannot continue after this error.\n' >&2
            ;;
          *)
            printf '[session] Unknown error retry status: %s\n' "$retry_status" >&2
            result_code=4
            turn_done=true
            ;;
        esac
        ;;
      session.status_rescheduled)
        printf '[session] The Session was rescheduled; continue waiting.\n' >&2
        ;;
      session.status_idle)
        stop_reason=$(jq -r '.stop_reason.type // empty' <<<"$event_json")
        case "$stop_reason" in
          end_turn)
            if ((message_count == 0)); then
              printf 'The turn ended without an agent.message event.\n' >&2
              result_code=5
            else
              printf '[session] end_turn\n' >&2
              result_code=0
            fi
            turn_done=true
            ;;
          requires_action)
            pending_ids=$(jq -r '.stop_reason.event_ids // [] | join(",")' <<<"$event_json")
            printf '[session] requires_action: %s\n' "$pending_ids" >&2
            result_code=2
            turn_done=true
            ;;
          retries_exhausted)
            printf '[session] retries_exhausted\n' >&2
            result_code=3
            turn_done=true
            ;;
          *)
            printf 'Unknown session.status_idle stop_reason: %s\n' "$stop_reason" >&2
            result_code=5
            turn_done=true
            ;;
        esac
        ;;
      session.deleted)
        printf '[session] deleted\n' >&2
        result_code=4
        turn_done=true
        ;;
      session.status_terminated)
        printf '[session] terminated\n' >&2
        result_code=4
        turn_done=true
        ;;
    esac
  }

  read_history() {
    local mode="$1"
    local page=""
    local page_count=0
    local response
    local event_json

    while :; do
      page_count=$((page_count + 1))
      if ((page_count > max_history_pages)); then
        printf 'History recovery exceeded %s pages.\n' "$max_history_pages" >&2
        return 1
      fi

      if [[ -n $page ]]; then
        response=$(curl -sS --fail-with-body --get \
          "$ARK_BASE_URL/sessions/$session_id/events" \
          -H "Authorization: Bearer $ARK_API_KEY" \
          --data-urlencode "limit=200" \
          --data-urlencode "order=asc" \
          --data-urlencode "page=$page") || return 1
      else
        response=$(curl -sS --fail-with-body --get \
          "$ARK_BASE_URL/sessions/$session_id/events" \
          -H "Authorization: Bearer $ARK_API_KEY" \
          --data-urlencode "limit=200" \
          --data-urlencode "order=asc") || return 1
      fi

      while IFS= read -r event_json; do
        [[ -n $event_json ]] || continue
        if [[ $mode == "seed" ]]; then
          remember_event "$(jq -r '.id // empty' <<<"$event_json")"
        else
          process_event "$event_json"
          [[ $turn_done == true ]] && break
        fi
      done < <(jq -c '.data[]?' <<<"$response")

      [[ $turn_done == true ]] && return 0
      page=$(jq -r '.next_page // empty' <<<"$response")
      [[ -n $page ]] || return 0
    done
  }

  if ! start_stream; then
    return 1
  fi

  # Mark existing history so reconnect recovery only emits this turn.
  if ! read_history seed; then
    printf 'Failed to read the Session history before sending the message.\n' >&2
    return 1
  fi

  events_payload=$(jq -n --arg text "$user_text" \
    '{events: [{type: "user.message", content: [{type: "text", text: $text}]}]}')

  curl -sS --fail-with-body \
    "$ARK_BASE_URL/sessions/$session_id/events" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$events_payload" >/dev/null || return 1

  while [[ $turn_done != true ]]; do
    while IFS= read -r line <&3; do
      line=\\${line%$'\r'}
      if [[ $line == ": server draining, reconnect" ]]; then
        break
      fi
      [[ $line == data:* ]] || continue
      data=\\${line#data:}
      data=\\${data# }
      process_event "$data"
      [[ $turn_done == true ]] && break
    done

    [[ $turn_done == true ]] && break
    if ((reconnect_count >= max_reconnects)); then
      printf 'SSE reconnect limit reached before the turn finished.\n' >&2
      return 1
    fi

    reconnect_count=$((reconnect_count + 1))
    printf '[session] Reconnecting SSE and recovering history (%s/%s).\n' \
      "$reconnect_count" "$max_reconnects" >&2

    # Open the replacement stream first, then recover the disconnect window.
    if ! start_stream; then
      return 1
    fi
    if ! read_history recover; then
      printf 'Failed to recover Session history after reconnecting.\n' >&2
      return 1
    fi
  done

  return "$result_code"
)
```

```bash
export ARK_API_KEY="<ARK_API_KEY>"
export ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"

run_session_turn \
  "sesn-20260701120100-xxxxx" \
  "列出当前工作目录中的文件。"
```

### 响应模型与下一步

发送事件后，Session 进入 `running` 状态。上面的示例会通过已建立的 SSE 流接收消息、工具调用和状态事件，并在会话进入 `idle` 或 `terminated` 后结束监听。事件字段与断线续传方式请参见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)；若仅需轮询状态，可周期性 GET Session 详情，详情请参见 [管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/manage-session)。

<a id="doc-2553724"></a>

---

## 管理 Session

> 来源：[https://docs.volcengine.com/docs/82379/2553724?lang=zh](https://docs.volcengine.com/docs/82379/2553724?lang=zh)

Session 创建后，你可以查询状态、列出 Session、更新标题和标签、升级运行配置，以及永久删除 Session。

> **说明**

> 创建 Session 与发送首个事件的方法，详情请参见 [启动 Session](https://ark.volcengine.com/region:cn-beijing/docs/start-session)。

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/base-url-and-authentication)。

## Session 状态机

| 状态           | 说明                                                                        |
| -------------- | --------------------------------------------------------------------------- |
| `idle`         | Agent 正在等待输入，例如用户消息或工具确认。普通新建的 Session 以该状态启动 |
| `running`      | Agent 正在执行任务                                                          |
| `terminated`   | Session 已终止，不再接收新事件                                              |
| `rescheduling` | 发生暂时性错误后，系统正在重新调度 Session                                  |
| `initializing` | Session 正在初始化运行环境或恢复资源                                        |
| `failed`       | Session 初始化或运行失败                                                    |
| `upgrading`    | Session 正在升级 Agent 或 Environment 运行配置                              |

这些状态及主要迁移关系如下：

![图片](https://arkdocs.tos-cn-beijing.volces.com/flowcharts/managed-agent/session-resource-state-machine.svg)

状态迁移规律：

- Session 初始化或资源恢复完成后，状态从 `initializing` 变为 `idle`。

- Session 初始化或资源恢复失败后，状态从 `initializing` 变为 `failed`。

- 收到 `user.message` 或 `user.tool_confirmation` 等用户事件后，状态从 `idle` 变为 `running`。

- Agent 一轮工作结束（`end_turn`）或需要等用户输入时，状态从 `running` 变为 `idle`（`requires_action`，详情请参见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)）。

- Session 运行期间出现暂时性错误时，状态从 `running` 变为 `rescheduling`。方舟会自动恢复并重新调度 Session。恢复完成后，状态变回 `running`，你无需介入。

- 服务端受理运行配置升级后，状态从 `idle` 变为 `upgrading`。升级完成或回滚后，状态恢复为 `idle`。

- Session 可以从任意状态变为 `terminated`。终止后不再接收事件，但记录与事件历史保留。

> **说明**

> Session 资源的状态值是 `rescheduling`；对应的 SSE 状态事件名是 `session.status_rescheduled`。调用查询接口或使用 `status` 参数过滤时，应传 `rescheduling`，不要传事件名中的 `rescheduled`。

## 选择获取方式

Session 详情接口只返回资源的最新状态、用量统计和配置快照。实时回复和历史事件由 Session Events API 承载：

| 需要获取的内容                              | 使用接口                                   | 返回内容和限制                                                                                                |
| ------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| 实时接收 Agent 回复和状态；按需接收累计用量 | `GET /sessions/{session_id}/events/stream` | 通过 SSE（Server\-Sent Events）协议订阅会话主线程收到的实时事件。请建立 SSE 并等待 `: ready` 后，再发送事件。 |
| 已持久化的完整事件                          | `GET /sessions/{session_id}/events`        | 用于查询会话主线程中已经产生的事件，主要用于历史事件查询和断线补查，返回信息不包含预览帧。                    |
| 最新状态、累计用量和配置快照                | `GET /sessions/{session_id}`               | 查询指定会话的详细信息，包括智能体、会话运行环境、资源、状态和标签等，不返回 Agent 回复或完整事件历史         |

正常交互时，通过 SSE 实时接收 Agent 回复和状态；如需展示用量或记账，可读取 `session.usage` 累计用量快照。`GET /sessions/{session_id}/events` 仅用于历史事件查询和断线补查。不要只根据 `status=idle` 判断本轮成功。发送任务、接收回复和判断 `stop_reason` 的完整流程，详情请参见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)。

## 检索 Session

通过 `GET /sessions/{session_id}` 查询 Session 的详细信息，包括智能体、会话运行环境、资源、状态、累计用量和标签等：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno \
  -H "Authorization: Bearer $ARK_API_KEY"
```

响应示例：

```jsonc
{
  "id": "<SESSION_ID>",
  "status": "idle",
  "usage": {
    "active_seconds": 147.661,
    "cache_read_input_tokens": 271227,
    "input_tokens": 34,
    "output_tokens": 7774,
    "server_tool_use": {
      "general_search_requests": 1,
      "visual_search_requests": 1,
      "web_fetch_requests": 0,
      "web_search_requests": 2,
    },
  },
  "stats": {
    "active_seconds": 147.661,
    "duration_seconds": 1788.0,
  },
  "agent": {
    "id": "<AGENT_ID>",
    "version": 1,
  },
  "environment_id": "<ENVIRONMENT_ID>",
}
```

响应主要字段：

- **`id`**：Session ID。

- **`status`**：当前状态（见上表）。

- **`usage`**：Session 运行期间累计的运行时长、模型 token 和服务端搜索请求次数（详情请参见 [Session 事件流 § 跟踪用量](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)）。

  - `active_seconds` 表示 Session 至少有一个线程处于 `running` 状态的累计时长。

  - `cache_read_input_tokens`、`input_tokens` 和 `output_tokens` 分别记录缓存读取、未缓存输入和输出 token。

  - `server_tool_use` 记录服务端托管工具累计发起的请求次数，具体字段请参见 [查询会话详情](https://ark.volcengine.com/region:cn-beijing/docs/get-session-api) 。

- **`stats`**：Session 的累计运行时长和从创建到当前或终止时的总时长。

  - `active_seconds` 表示 Session 至少有一个线程处于 `running` 状态的累计时长，单位为秒。

  - `duration_seconds` 表示从创建到查询时刻的总时长；Session 终止后，该值固定为从创建到终止的时长。

- **`agent`**：绑定的 Agent 对象，内含 `id`、`version` 等字段。

- **`environment_id`**：绑定的云环境。

需要查看某个线程的时长和累计用量时，调用 [查询线程详情](https://ark.volcengine.com/region:cn-beijing/docs/get-thread-api) 并读取该线程的 `stats` 和 `usage`。

## 列出 Session

`GET /sessions` 支持按 `agent_id` 过滤、按创建时间倒序分页，响应以 `data` 数组形式返回 Session 列表：

**Curl**

```Bash
curl "https://ark.cn-beijing.volces.com/api/v3/sessions?agent_id=agent-20260701120000-abcde&limit=20" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

## 更新与升级 Session

| 需求                               | 接口                                                                                            | 状态变化                                          |
| ---------------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| 修改标题或标签                     | [更新 Session 标题和标签](https://ark.volcengine.com/region:cn-beijing/docs/update-session-api) | 不改变 Session 的运行状态                         |
| 修改 Agent 或 Environment 运行配置 | [升级会话](https://ark.volcengine.com/region:cn-beijing/docs/upgrade-session-api)               | 先从 `idle` 变为 `upgrading`，结束后恢复为 `idle` |

升级接口保持原 `session_id` 不变，也不支持换绑其他 Agent 或 Environment。发起升级前，Session 必须处于 `idle`，并且上一轮任务以 `end_turn` 结束。接口返回 `upgrading` 后，轮询 `GET /sessions/{session_id}`；状态恢复为 `idle` 表示升级流程已结束。

> **注意**

> 不要通过更新会话接口修改 Agent、Environment、`tools` 或 `mcp_servers`。该接口只更新标题和标签，运行配置变更必须调用升级会话接口。

## 删除 Session

> **注意**

> **删除 Session 操作不可逆。** 删除会永久移除 Session 记录、所有事件和关联沙箱。Session 必须处于 `idle` 或 `terminated` 状态才能删除，其他状态会返回 `InvalidAction`。如果 Session 正在 `running`，先发送 [中断事件](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)，等待状态回到 `idle` 后再删除。

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno \
  -X DELETE \
  -H "Authorization: Bearer $ARK_API_KEY"
```

## 数据保留与删除影响

Session 资源记录、完整事件、沙箱状态和 Agent 产物使用独立的保留策略：

| 对象             | 默认保留与恢复规则                                                                                                                        | 删除 Session 后                                              |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Session 资源记录 | 除非显式删除，否则持续保留                                                                                                                | 资源记录不可再查询                                           |
| Session 事件历史 | 每条已持久化的完整事件默认保留 6 个月；`event_start` 和 `event_delta` 预览帧不写入历史                                                    | 该 Session 的事件全部删除                                    |
| 沙箱状态         | Session 处于 `idle` 且尚未终止时，可以恢复文件系统、已安装软件包和沙箱中的临时文件。Session 连续处于 `idle` 达到 14 天后进入 `terminated` | 关联沙箱及其中的临时状态被清理                               |
| Agent 产物       | 方舟默认存储只用于临时保存关联产物；写入自有 TOS Bucket 的对象遵循你配置的 Bucket 生命周期                                                | 默认存储中的关联产物被清理；自有 TOS Bucket 中的对象不受影响 |

> **数据保留提示**

- > **独立资源不受 Session 删除影响**：删除 Session 时，方舟不会删除 Agent、Environment、Memory、Vaults、技能，也不会删除通过 Files API 独立上传的输入文件。这些对象是独立资源，需要按各自的生命周期管理。

- > **长期保存 Agent 产物**：如果需要长期保留 Agent 产物，在方舟默认存储中的关联产物被清理前，将产物写入自有 TOS Bucket；如果还需要保留事件记录，也要在事件到期前保存到自己的存储。产物存储的配置方法，详情请参见 [配置产物存储](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment#configure-output-storage)。

- > **延长沙箱状态保留期**：如果需要继续保留沙箱状态，在当前 14 天保留期到期前发送一条 `content` 非空的 `user.message`。Session 完成本轮任务并再次进入 `idle` 后，平台重新计算 14 天保留期。省略 `content` 或传入空数组不会刷新保留期。

<a id="doc-2553725"></a>

---

## Session 事件流

> 来源：[https://docs.volcengine.com/docs/82379/2553725?lang=zh](https://docs.volcengine.com/docs/82379/2553725?lang=zh)

与 Managed Agents 的通信基于事件。接入时，先建立 SSE 并等待 `: ready`，再发送事件并接收完整事件，最后根据 `stop_reason` 判断结果。如需展示用量或记账，再读取累计用量快照。连接中断后，先建立新流，再通过历史事件接口补齐中断窗口。

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/base-url-and-authentication)。

本地需要安装 `curl` 和 `jq`。

## 核心交互流程

以下流程适用于云端环境和自托管环境中的业务应用：

1. 通过 `GET /sessions/{session_id}/events/stream` 建立 SSE。

2. 等待服务端返回 `: ready`。

3. 通过 `POST /sessions/{session_id}/events` 发送 `user.message`。

4. 持续接收 `agent.*`、`session.*` 和 `span.*` 事件。

5. 仅在收到后续 `session.status_idle` 且 `stop_reason.type=end_turn` 时，将本轮判定为正常结束。

如需展示用量或记账，读取 `session.status_idle` 前返回的 `session.usage` 累计用量快照。`session.usage` 不是本轮完成判断条件。

> **注意**

> **不要先发送消息再建立 SSE。** SSE 只推送连接建立后产生的事件。先发送消息再建立 SSE，会漏掉连接建立前产生的实时事件，客户端还需要查询历史事件补齐。

正常完成一轮任务时，事件按以下顺序流转：

![图片](https://arkdocs.tos-cn-beijing.volces.com/flowcharts/managed-agent/session-turn-event-sequence.svg)

- 客户端先建立 SSE 并等待 `: ready`，再发送 `user.message`，避免漏掉连接建立前已经产生的事件。

- `session.status_running` 表示 Agent 已开始处理任务；`span.*` 和 `agent.thinking` 按任务执行过程产生，完整回复由 `agent.message` 返回。

- 本轮收尾时，服务端依次返回最后一个 `session.thread_status_idle`、`session.usage` 和 `session.status_idle`。无论 `stop_reason` 是什么，Session 进入 `idle` 前都会返回 `session.usage`。

- 收到 `agent.message` 或 `session.usage` 都不代表本轮结束。客户端继续读取事件，只有 `session.status_idle` 且 `stop_reason.type=end_turn` 表示正常结束。

如需直接复用完整实现，请参见 [运行完整示例](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream#complete-example) 。

## 理解事件通信

### 接口职责

根据不同用途选择对应接口：

| 接口                                       | 客户端动作                                         | 返回内容和限制                                               |
| ------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------------ |
| `GET /sessions/{session_id}/events/stream` | 建立 SSE 并等待 `: ready`，实时接收事件            | 只推送连接建立后产生的事件，不回放历史事件                   |
| `POST /sessions/{session_id}/events`       | 发送 `user.*` 或 `system.message` 事件             | HTTP 200 只表示事件已受理，不表示 Agent 已处理或本轮已结束   |
| `GET /sessions/{session_id}/events`        | 分页查询已持久化的完整事件，用于历史查看和断线补查 | 不包含 `event_start`、`event_delta` 预览帧，也不提供实时推送 |
| `GET /sessions/{session_id}`               | 查询 Session 最新状态、累计用量和配置快照          | 不返回完整事件历史或 Agent 回复内容                          |

### 事件模型

事件类型遵循 `{domain}.{action}` 命名约定。先根据事件域判断方向和职责，再根据完整的 `type` 读取对应结构。

| 事件域                     | 方向                 | 说明                                                                |
| -------------------------- | -------------------- | ------------------------------------------------------------------- |
| `user.*`、`system.message` | 客户端发送至 Session | 提交用户消息、动态系统提示词、中断、工具确认或工具结果              |
| `agent.*`                  | Session 发送至客户端 | 返回消息、思考过程、工具调用和主子线程通信事件                      |
| `session.*`                | Session 发送至客户端 | 返回 Session 或子线程的运行、等待、重调度、终止、错误状态和累计用量 |
| `span.*`                   | Session 发送至客户端 | 返回模型调用的过程和结果                                            |

每个完整事件都包含 `id`、`type` 和 `processed_at`。`processed_at` 为 `null` 时，表示事件已进入队列但尚未完成处理。完整事件类型、字段、取值和嵌套结构，详情请参见 [会话事件结构参考](https://ark.volcengine.com/region:cn-beijing/docs/sessions-events-reference-v2)。

### 运行环境差异

两种环境中的业务应用共用 Session Events API。Environment 类型只改变主 Agent 的 Bash 和文件工具的执行位置，不改变其他工具的执行方：

| 对比项                                                      | 云端环境                                          | 自托管环境                                                    |
| ----------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------- |
| Session 事件                                                | 业务应用建立 Session SSE 并发送事件               | 业务应用建立 Session SSE 并发送事件                           |
| 主 Agent 的 `bash`、`read`、`write`、`edit`、`glob`、`grep` | 方舟云沙箱执行，通过 `agent.tool_result` 返回结果 | 企业 Worker 和 Sandbox 执行，通过 `user.tool_result` 回传结果 |
| `web_fetch`、`web_search` 和多模态工具                      | 方舟侧执行                                        | 方舟侧执行                                                    |
| MCP 工具                                                    | 方舟侧执行                                        | 方舟侧执行                                                    |
| 自定义工具                                                  | 业务应用执行                                      | 业务应用执行                                                  |
| 额外链路                                                    | 无                                                | Worker Poll/Ack Work、建立 Worker SSE 并维持 Heartbeat        |

自托管环境中，业务应用不直接调用 Work API。Worker 的部署与运行机制，详情请参见 [配置并运行自托管环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-self-hosted-environment)。

## 发送事件

客户端通过 `POST /sessions/{session_id}/events` 向 Session 发送 `user.message` 事件，`content` 是一个块数组，可混合文本（`text`）、图片（`image`）、文档（`document`）、视频（`video`）四类块。

- 图片 `image` 的 `source.type` 支持三种：`base64`（内联 base64，需带 `media_type`）、`url`（公网可访问 URL）、`file`（已上传文件 ID，字段为 `file_id`）。

- 文档 `document` 的 `source.type` 支持四种：`file`（已上传文件 ID）、`url`（公网可访问 URL）、`base64`（内联 base64，需带 `media_type`）、`text`（直接内联纯文本，`media_type: text/plain` + `data` 字段）；可选 `title` 与 `context` 字段为文档附加标题与背景说明。

- 视频 `video` 的 `source.type` 支持三种：`base64`（内联 Base64，需带 `media_type` 和 `data`）、`url`（公网可访问 URL）、`file`（已上传视频文件 ID，字段为 `file_id`）。三种来源均可通过 `fps` 设置抽帧频率，取值范围为 0.2～5。

- 同一请求的 `content` 数组可混合多个图片、文档或视频块。

- 如需为当前轮次动态追加系统提示词，可在 `events` 数组里 `user.message` 后紧跟一个 `system.message`，详情请参见 [动态系统提示词](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream#dynamic-system-prompt)。

### 常见内容组合示例

**纯文本**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [{"type": "text", "text": "帮我分析这份销售数据，找出 Q2 异常"}]
    }]
  }'
```

**文本 + 图片（URL）**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [
        {"type": "text", "text": "看下这张趋势图哪里不对"},
        {"type": "image", "source": {"type": "url", "url": "https://cdn.example.com/q2-trend.png"}}
      ]
    }]
  }'
```

**文本 + 图片（base64）**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [
        {"type": "text", "text": "看下这张趋势图哪里不对"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANSUhEUgAA..."}}
      ]
    }]
  }'
```

**文本 + 图片（file_id）**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [
        {"type": "text", "text": "看下这张趋势图哪里不对"},
        {"type": "image", "source": {"type": "file", "file_id": "file_img_abc123"}}
      ]
    }]
  }'
```

**文本 + 文档（file_id）**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [
        {"type": "text", "text": "总结这份审计报告的关键风险"},
        {"type": "document", "title": "Q2 审计报告.pdf", "context": "Big4 出具的内部审计稿件", "source": {"type": "file", "file_id": "file_pdf_xxx"}}
      ]
    }]
  }'
```

**文本 + 文档（内联纯文本）**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [
        {"type": "text", "text": "帮我重写下面这段说明，更技术化一点"},
        {"type": "document", "title": "原稿", "source": {"type": "text", "media_type": "text/plain", "data": "本系统提供数据分析能力，包括但不限于报表生成、异常检测..."}}
      ]
    }]
  }'
```

**文本 + 文档（base64 PDF）**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [
        {"type": "text", "text": "提取这份合同里的关键条款"},
        {"type": "document", "title": "framework-agreement.pdf", "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjQKJaqr..."}}
      ]
    }]
  }'
```

**文本 + 文档（URL）**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [
        {"type": "text", "text": "把这份文档总结成 5 点"},
        {"type": "document", "title": "公司年报", "source": {"type": "url", "url": "https://investor.example.com/annual-2025.pdf"}}
      ]
    }]
  }'
```

**文本 + 多附件混合**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [
        {"type": "text", "text": "结合这份文字稿和两张图给我一份完整分析"},
        {"type": "document", "source": {"type": "file", "file_id": "file_doc_01"}},
        {"type": "image", "source": {"type": "file", "file_id": "file_img_01"}},
        {"type": "image", "source": {"type": "file", "file_id": "file_img_02"}}
      ]
    }]
  }'
```

请求体的 `events` 字段是事件数组，服务端按入队顺序串行处理。

### 运行中继续发送消息

Session 处于 `running` 状态时，你仍可以继续发送 `user.message`。服务端会先接收事件并写入待处理队列，等当前 Agent 执行到可调度边界后再把队列中的消息送入后续模型请求。常见边界包括当前模型请求结束、工具执行结果返回、当前回合结束等。

这种设计适合连续对话、实时追加约束、长任务中补充材料等场景。客户端不需要为了发送下一条消息轮询等待 `idle`，但仍需要通过 SSE 观察事件处理进度：

- HTTP 响应成功只表示事件已被服务端接收，不表示 Agent 已经处理完这条消息。

- 返回事件的 `processed_at` 为 `null` 时，表示该事件仍在队列中，等待前序事件处理完成。

- 多条排队消息可能在后续一次模型请求中合并处理，Agent 不一定为每条消息生成一条独立回复。

- 如果待处理队列已满，接口会返回 HTTP 409。此时不要快速重试，应等待 Agent 消费队列，或按业务需要发送 `user.interrupt` 中断当前执行。

### 动态系统提示词

除了在 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent) 时配置的固定系统提示词，客户端还可以在发送 `user.message` 的同一请求末尾追加一个 `system.message`，为当前轮次动态注入额外指令；固定系统提示词、运行时 `system.message` 与用户消息拼接后一起送入模型。

| 对比项 | Agent 定义的系统提示词                           | 运行时 `system.message` 事件              |
| ------ | ------------------------------------------------ | ----------------------------------------- |
| 位置   | 创建 Agent 时设置                                | 发 Events 时传入                          |
| 生效   | 创建 Session 时拼入，整个 Session 生命周期内固定 | 每次发送后追加到 Session 的系统角色上下文 |

同一请求需要区分 API 提交顺序和模型上下文顺序：

| 阶段                       | 顺序                                                                                        | 说明                                                                      |
| -------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| API 请求中的 `events` 数组 | 先发送 `user.message`，再发送 `system.message`                                              | `system.message` 必须紧跟在 `user.message` 后面，并且是数组的最后一个元素 |
| 进入模型前的上下文         | 依次为 Agent 系统提示词、历史 `system.message`、本轮 `system.message` 和本轮 `user.message` | 服务端先将本轮 `system.message` 追加到系统角色上下文，再发起本轮模型调用  |

模型上下文实际展开如下，省略普通会话历史。

| 轮次 | 上下文展开                                                                           |
| ---- | ------------------------------------------------------------------------------------ |
| 首轮 | `[Agent 系统提示词] [首轮 system.message] [首轮 user.message]`                       |
| 次轮 | `[Agent 系统提示词] [首轮 system.message] [次轮 system.message] [次轮 user.message]` |

`system.message` 按追加（append\-only）语义写入 Session 的系统角色上下文，不支持替换或清空。后续每次模型调用都会携带该 Session 内全部历史 `system.message` 内容。需要重置系统提示词时，必须新建 Session。

单独发送 `system.message`、未紧跟 `user.message` 或未放在 `events` 数组末尾，都会返回 HTTP 400 `InvalidPayload`。

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [
      {"type": "user.message", "content": [{"type": "text", "text": "我的订单 #1234 到哪了？"}]},
      {"type": "system.message", "content": [{"type": "text", "text": "你是 ACME 的客服助手，必须使用可用工具查询订单；绝对不能泄露内部客户 ID。"}]}
    ]
  }'
```

请求成功返回 HTTP 200，响应 `data` 数组按入参顺序包含服务端已受理的事件。

## 接收并处理事件

业务应用通过已经建立的 SSE 连接接收完整事件，并根据 `type` 更新状态、展示回复或进入对应分支。`agent.message`、`agent.thinking` 和 `agent.tool_result` 等完整事件会写入事件历史；客户端应记录事件 ID，用于断线补查时去重。

### 展示预览内容

需要在完整消息生成前更新界面时，通过 `event_deltas` 指定要预览的事件类型。当前支持 `agent.message` 和 `agent.thinking`：

多值可以重复传入，例如 `?event_deltas=agent.message&event_deltas=agent.thinking`；也可以使用逗号分隔，例如 `?event_deltas=agent.message,agent.thinking`。

> **注意**

> 参数名必须写为 `event_deltas`，不要写成 `event_deltas[]`。服务端会忽略未知参数名和不支持的事件类型，并继续返回 HTTP 200；客户端应根据是否收到对应的 `event_start` 和 `event_delta` 判断预览订阅是否生效。

![图片](https://arkdocs.tos-cn-beijing.volces.com/flowcharts/managed-agent/event-preview-assembly-sequence.svg)

- **预览开始**：事件 `event_start` 标识一个预览事件开始，并给出完整事件将使用的 ID。

- **预览增量**：事件 `event_delta` 按 `event_id` 和内容块 `index` 追加增量内容，同一事件可以返回多次。

- **预览帧标识**：每个预览帧的顶层 `id` 和 `processed_at` 标识该帧自身；使用 `event_start.event.id` 或 `event_delta.event_id` 关联最终完整事件。

- **完整事件**：事件 `agent.message` 或 `agent.thinking` 返回最终完整内容。

预览帧不写入事件历史，也可能在连接中断时不完整。界面可以使用预览帧实时更新，持久化和业务判断应以完整事件为准。参数写法和响应格式，详情请参见 [流式获取会话事件](https://ark.volcengine.com/region:cn-beijing/docs/stream-events-api)。

### 判断本轮结果

不要使用 HTTP 响应成功、`agent.message` 或 SSE 连接关闭作为本轮结束条件。客户端应继续读取状态事件，并根据以下结果处理：

Session 即将进入 `idle` 时，服务端会先返回 `session.usage`，再返回 `session.status_idle`。`session.usage` 只提供最新累计用量，不提供本轮处理结果；无论 `stop_reason` 为 `end_turn`、`requires_action` 还是 `retries_exhausted`，都应继续读取后续状态事件。

| 状态事件                     | 停止原因            | 客户端动作                                            |
| ---------------------------- | ------------------- | ----------------------------------------------------- |
| `session.status_idle`        | `end_turn`          | 本轮正常结束，可以发送下一条消息                      |
| `session.status_idle`        | `requires_action`   | 本轮尚未结束，根据 `event_ids` 回传工具确认或工具结果 |
| `session.status_idle`        | `retries_exhausted` | 当前轮次重试次数已用尽；本轮结束，但仍可发送新的输入  |
| `session.status_rescheduled` | 不适用              | 方舟正在自动恢复，继续监听，不要重复发送原事件        |
| `session.status_terminated`  | 不适用              | Session 已进入终态，停止发送事件                      |
| `session.deleted`            | 不适用              | Session 已被删除，结束事件流，不要重连或发送事件      |

收到 `session.error` 时，还需要检查 `error.retry_status.type`：

- **`retrying`**：服务端正在自动重试，继续监听。

- **`exhausted`**：当前轮次重试耗尽，等待对应的 `session.status_idle`。

- **`terminal`**：Session 将进入终止状态，停止发送新事件。

完整状态迁移，详情请参见 [管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/manage-session)。

### 处理工具确认

当工具权限策略要求执行前确认时，按以下步骤解除 `requires_action`：

1. 接收 `agent.tool_use` 或 `agent.mcp_tool_use` 事件。

2. 接收 `session.status_idle`，确认 `stop_reason.type=requires_action`。

3. 遍历 `stop_reason.event_ids`，为每个待处理事件发送一条 `user.tool_confirmation`。将事件 ID 原样填入 `tool_use_id`，并将 `result` 设置为 `allow` 或 `deny`。

4. 所有需要处理的确认事件处理完成后，Session 返回 `session.status_running` 并继续当前任务。

拒绝工具调用时，可以通过 `deny_message` 返回具体原因。Agent 会据此选择其他工具、降级处理，或说明无法继续执行。此阶段不要发送新的 `user.message` 替代确认。

工具权限策略的配置方法，详情请参见 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/tool-permission-policy)。

## 断线重连与历史补查

SSE 接口不支持从断点自动续传。要补齐中断期间产生的事件，按以下顺序处理：

1. 调用 `GET /sessions/{session_id}/events/stream` 打开新的事件流，并等待 `: ready`。

2. 调用 `GET /sessions/{session_id}/events` 查询历史事件。首次请求省略 `page`。

3. 响应包含 `next_page` 时，将该值原样作为下一次请求的 `page`，直到不再返回 `next_page`。

4. 按事件 ID 合并历史事件和新事件流中的实时事件，跳过已经处理的事件。

收到 `session.deleted` 表示 Session 已被删除，不属于可恢复的连接中断。此时结束事件流，不要重连或补查历史。

必须先建立新流再查询历史。如果先查历史再重连，历史查询结束到新流建立之间仍会留下新的监听空窗。本页的 [完整示例](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream#complete-example) 实现了该顺序和事件去重。

## 运行完整示例

以下 `run_session_turn` 函数串联 SSE 建连、事件发送、结果判断和断线补查。SSE 意外断开时，函数先建立新流，再分页查询历史事件，并按事件 ID 去重。

完整 cURL 函数

```Bash
run_session_turn() (
  local session_id="$1"
  local user_text="$2"
  local stream_dir
  local stream_pipe=""
  local stream_error
  local stream_pid=""
  local stream_number=0
  local stream_ready=false
  local seen_ids_file
  local events_payload
  local turn_done=false
  local result_code=1
  local message_count=0
  local reconnect_count=0
  local max_reconnects="${ARK_MAX_SSE_RECONNECTS:-3}"
  local max_history_pages="${ARK_MAX_HISTORY_PAGES:-100}"
  local line
  local data

  : "${ARK_BASE_URL:?Set ARK_BASE_URL before calling run_session_turn}"
  : "${ARK_API_KEY:?Set ARK_API_KEY before calling run_session_turn}"
  : "${session_id:?Pass a Session ID as the first argument}"
  : "${user_text:?Pass a user message as the second argument}"
  command -v curl >/dev/null || {
    printf 'curl is required.\n' >&2
    return 1
  }
  command -v jq >/dev/null || {
    printf 'jq is required.\n' >&2
    return 1
  }
  [[ $max_reconnects =~ ^[0-9]+$ ]] || {
    printf 'ARK_MAX_SSE_RECONNECTS must be a non-negative integer.\n' >&2
    return 1
  }
  [[ $max_history_pages =~ ^[1-9][0-9]*$ ]] || {
    printf 'ARK_MAX_HISTORY_PAGES must be a positive integer.\n' >&2
    return 1
  }

  stream_dir=$(mktemp -d) || {
    printf 'Failed to create a temporary directory for the SSE stream.\n' >&2
    return 1
  }
  stream_error="$stream_dir/stream-error.log"
  seen_ids_file="$stream_dir/seen-event-ids"
  : >"$stream_error"
  : >"$seen_ids_file"

  stop_stream() {
    exec 3<&- || true
    if [[ -n $stream_pid ]] && kill -0 "$stream_pid" 2>/dev/null; then
      kill "$stream_pid" 2>/dev/null || true
    fi
    if [[ -n $stream_pid ]]; then
      wait "$stream_pid" 2>/dev/null || true
    fi
    if [[ -n $stream_pipe ]]; then
      rm -f "$stream_pipe"
    fi
    stream_pid=""
    stream_pipe=""
  }

  cleanup_stream() {
    stop_stream
    rm -rf "$stream_dir"
  }
  trap cleanup_stream EXIT

  start_stream() {
    stop_stream
    stream_number=$((stream_number + 1))
    stream_pipe="$stream_dir/events-$stream_number"
    stream_ready=false

    if ! mkfifo "$stream_pipe"; then
      printf 'Failed to create the SSE pipe at %s.\n' "$stream_pipe" >&2
      return 1
    fi

    curl -sS -N --fail-with-body --max-time 600 \
      "$ARK_BASE_URL/sessions/$session_id/events/stream" \
      -H "Authorization: Bearer $ARK_API_KEY" \
      -H "Accept: text/event-stream" >"$stream_pipe" 2>>"$stream_error" &
    stream_pid=$!
    exec 3<"$stream_pipe"

    while IFS= read -r line <&3; do
      line=${line%$'\r'}
      if [[ $line == ": ready" ]]; then
        stream_ready=true
        break
      fi
    done

    if [[ $stream_ready != true ]]; then
      printf 'SSE stream closed before it was ready.\n' >&2
      if [[ -s $stream_error ]]; then
        tail -n 1 "$stream_error" >&2
      fi
      return 1
    fi
  }

  event_seen() {
    local event_id="$1"
    [[ -n $event_id ]] && grep -Fqx -- "$event_id" "$seen_ids_file"
  }

  remember_event() {
    local event_id="$1"
    if [[ -n $event_id ]] && ! event_seen "$event_id"; then
      printf '%s\n' "$event_id" >>"$seen_ids_file"
    fi
  }

  process_event() {
    local event_json="$1"
    local event_id
    local event_type
    local stop_reason
    local retry_status
    local pending_ids

    event_id=$(jq -r '.id // empty' <<<"$event_json" 2>/dev/null || true)
    if event_seen "$event_id"; then
      return 0
    fi
    remember_event "$event_id"

    event_type=$(jq -r '.type // empty' <<<"$event_json" 2>/dev/null || true)
    case "$event_type" in
      agent.message)
        jq -r '.content[]? | select(.type == "text") | .text' <<<"$event_json"
        message_count=$((message_count + 1))
        ;;
      agent.tool_use | agent.mcp_tool_use | agent.custom_tool_use)
        printf '[tool] %s\n' "$(jq -r '.name // .type' <<<"$event_json")"
        ;;
      session.error)
        retry_status=$(jq -r '.error.retry_status.type // empty' <<<"$event_json")
        printf '[error] %s: %s\n' \
          "$(jq -r '.error.type // "unknown_error"' <<<"$event_json")" \
          "$(jq -r '.error.message // "No error message returned."' <<<"$event_json")" >&2
        case "$retry_status" in
          retrying)
            printf '[session] The service is retrying automatically.\n' >&2
            ;;
          exhausted)
            printf '[session] Retries were exhausted for this turn.\n' >&2
            ;;
          terminal)
            printf '[session] The Session cannot continue after this error.\n' >&2
            ;;
          *)
            printf '[session] Unknown error retry status: %s\n' "$retry_status" >&2
            result_code=4
            turn_done=true
            ;;
        esac
        ;;
      session.status_rescheduled)
        printf '[session] The Session was rescheduled; continue waiting.\n' >&2
        ;;
      session.status_idle)
        stop_reason=$(jq -r '.stop_reason.type // empty' <<<"$event_json")
        case "$stop_reason" in
          end_turn)
            if ((message_count == 0)); then
              printf 'The turn ended without an agent.message event.\n' >&2
              result_code=5
            else
              printf '[session] end_turn\n' >&2
              result_code=0
            fi
            turn_done=true
            ;;
          requires_action)
            pending_ids=$(jq -r '.stop_reason.event_ids // [] | join(",")' <<<"$event_json")
            printf '[session] requires_action: %s\n' "$pending_ids" >&2
            result_code=2
            turn_done=true
            ;;
          retries_exhausted)
            printf '[session] retries_exhausted\n' >&2
            result_code=3
            turn_done=true
            ;;
          *)
            printf 'Unknown session.status_idle stop_reason: %s\n' "$stop_reason" >&2
            result_code=5
            turn_done=true
            ;;
        esac
        ;;
      session.deleted)
        printf '[session] deleted\n' >&2
        result_code=4
        turn_done=true
        ;;
      session.status_terminated)
        printf '[session] terminated\n' >&2
        result_code=4
        turn_done=true
        ;;
    esac
  }

  read_history() {
    local mode="$1"
    local page=""
    local page_count=0
    local response
    local event_json

    while :; do
      page_count=$((page_count + 1))
      if ((page_count > max_history_pages)); then
        printf 'History recovery exceeded %s pages.\n' "$max_history_pages" >&2
        return 1
      fi

      if [[ -n $page ]]; then
        response=$(curl -sS --fail-with-body --get \
          "$ARK_BASE_URL/sessions/$session_id/events" \
          -H "Authorization: Bearer $ARK_API_KEY" \
          --data-urlencode "limit=200" \
          --data-urlencode "order=asc" \
          --data-urlencode "page=$page") || return 1
      else
        response=$(curl -sS --fail-with-body --get \
          "$ARK_BASE_URL/sessions/$session_id/events" \
          -H "Authorization: Bearer $ARK_API_KEY" \
          --data-urlencode "limit=200" \
          --data-urlencode "order=asc") || return 1
      fi

      while IFS= read -r event_json; do
        [[ -n $event_json ]] || continue
        if [[ $mode == "seed" ]]; then
          remember_event "$(jq -r '.id // empty' <<<"$event_json")"
        else
          process_event "$event_json"
          [[ $turn_done == true ]] && break
        fi
      done < <(jq -c '.data[]?' <<<"$response")

      [[ $turn_done == true ]] && return 0
      page=$(jq -r '.next_page // empty' <<<"$response")
      [[ -n $page ]] || return 0
    done
  }

  if ! start_stream; then
    return 1
  fi

  # Mark existing history so reconnect recovery only emits this turn.
  if ! read_history seed; then
    printf 'Failed to read the Session history before sending the message.\n' >&2
    return 1
  fi

  events_payload=$(jq -n --arg text "$user_text" \
    '{events: [{type: "user.message", content: [{type: "text", text: $text}]}]}')

  curl -sS --fail-with-body \
    "$ARK_BASE_URL/sessions/$session_id/events" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$events_payload" >/dev/null || return 1

  while [[ $turn_done != true ]]; do
    while IFS= read -r line <&3; do
      line=${line%$'\r'}
      if [[ $line == ": server draining, reconnect" ]]; then
        break
      fi
      [[ $line == data:* ]] || continue
      data=${line#data:}
      data=${data# }
      process_event "$data"
      [[ $turn_done == true ]] && break
    done

    [[ $turn_done == true ]] && break
    if ((reconnect_count >= max_reconnects)); then
      printf 'SSE reconnect limit reached before the turn finished.\n' >&2
      return 1
    fi

    reconnect_count=$((reconnect_count + 1))
    printf '[session] Reconnecting SSE and recovering history (%s/%s).\n' \
      "$reconnect_count" "$max_reconnects" >&2

    # Open the replacement stream first, then recover the disconnect window.
    if ! start_stream; then
      return 1
    fi
    if ! read_history recover; then
      printf 'Failed to recover Session history after reconnecting.\n' >&2
      return 1
    fi
  done

  return "$result_code"
)
```

配置鉴权信息和已有 Session ID，然后调用函数：

```bash
export ARK_API_KEY="<ARK_API_KEY>"
export ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
export SESSION_ID="<SESSION_ID>"

run_session_turn \
  "$SESSION_ID" \
  "总结当前工作目录中的 README，并列出三个要点。"
```

正常完成时，函数输出完整的 `agent.message`，并以状态码 `0` 退出。其他结果使用非零状态码：

| 状态码 | 结果                                                 | 处理方式                                 |
| ------ | ---------------------------------------------------- | ---------------------------------------- |
| `0`    | `end_turn`，且已收到 `agent.message`                 | 本轮正常结束                             |
| `2`    | `requires_action`                                    | 根据输出的事件 ID 回传工具确认或工具结果 |
| `3`    | `retries_exhausted`                                  | 当前轮次失败，可以修正输入后重新发送     |
| `4`    | Session 终止、被删除或发生不可恢复错误               | 停止向当前 Session 发送事件              |
| `5`    | 状态事件缺少有效停止原因，或正常结束前未收到完整回复 | 查询历史事件和错误信息后再决定是否重试   |

## 中断和继续使用 Session

### 中断 Session

客户端发送 `user.interrupt` 事件可中断 Agent 当前执行。中断会清空尚未被调度的待处理消息，避免旧消息在中断后继续触发模型调用。

如果你只是补充当前任务的信息，直接继续发送 `user.message` 即可，消息会按队列顺序处理；如果你要放弃当前执行并切换到新任务，先发送 `user.interrupt`，等 Session 回到 `idle` 后再发送新的 `user.message`：

**Curl**

```Bash
# 第一步：发送中断事件
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"events": [{"type": "user.interrupt"}]}'

# 第二步：等 Session 回到 idle 后（收到 session.status_idle 事件），发送新消息
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [{"type": "text", "text": "Instead, focus on fixing the bug in line 42."}]
    }]
  }'
```

### 继续使用空闲 Session

在 Session 仍处于 `idle` 且尚未终止时，按 [发送消息](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream#send-message) 流程发送包含真实任务内容的 `user.message`，Session 会从 `idle` 变为 `running`。

如果需要继续保留沙箱状态，在当前期限到期前发送一条 `content` 非空的 `user.message`。Session 完成本轮任务并再次进入 `idle` 后，平台重新计算 14 天保留期。省略 `content` 或传入空数组不会刷新保留期。

需要长期保留事件和 Agent 产物时，将必要数据保存到自己的存储。保留与删除影响，详情请参见 [数据保留与删除影响](https://ark.volcengine.com/region:cn-beijing/docs/manage-session#checkpoint-retention)。

## 观测与排查

### 跟踪用量

根据统计范围选择对应事件：

- **Session 累计用量**：读取 `session.usage` 事件的 `usage`。该事件在 Session 每次进入 `idle` 前返回最新累计快照；如需计算本轮增量，将本次快照减去上一次 `session.usage` 快照。

- **单次模型请求用量**：读取 `span.model_request_end` 事件的 `model_usage`，用于分析某次模型调用的 token 消耗和推理速度。

以下 `session.usage` 示例包含会话累计运行时长、模型 token 和服务端搜索请求次数：

```jsonc
{
  "id": "sevt-usg-sevt-20260926143029-e5f6g",
  "type": "session.usage",
  "processed_at": "2026-09-26T14:30:29.220Z",
  "usage": {
    "active_seconds": 147.661,
    "cache_creation": {
      "ephemeral_1h_input_tokens": 0,
      "ephemeral_5m_input_tokens": 0,
    },
    "cache_read_input_tokens": 271227,
    "input_tokens": 34,
    "output_tokens": 7774,
    "server_tool_use": {
      "general_search_requests": 1,
      "visual_search_requests": 1,
      "web_fetch_requests": 0,
      "web_search_requests": 2,
    },
  },
}
```

`session.usage.id` 由对应的 `session.status_idle` 事件 ID 生成，格式为 `sevt-usg-<idle_event_id>`；两条事件的 `processed_at` 相同。客户端应保留服务端返回顺序，不要仅按 `processed_at` 排序或去重。当前事件中的 `cache_creation` 两个字段固定返回 `0`。

需要查询服务端记录的最新累计用量和时长时，调用 `GET /sessions/{session_id}` 读取 Session 的 `usage` 和 `stats`；需要按线程排查消耗和启动耗时时，调用 `GET /sessions/{session_id}/threads/{thread_id}` 读取当前线程的 `usage` 和 `stats`。存量 Session 的统计完整性边界，详情请参见 [管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/manage-session) 。

`span.model_request_end` 携带的 `model_usage` 只记录本次模型请求：

```json
{
  "id": "sevt-mre-01",
  "type": "span.model_request_end",
  "processed_at": "2026-05-31T16:00:02.100Z",
  "model_request_start_id": "sevt-mrs-01",
  "is_error": false,
  "model_usage": {
    "input_tokens": 1820,
    "output_tokens": 42,
    "cache_creation_input_tokens": 1500,
    "cache_read_input_tokens": 0,
    "speed": "standard"
  }
}
```

`model_usage` 字段含义：

- **`input_tokens`**：未缓存输入 token。

- **`output_tokens`**：全部输出 token。

- **`cache_creation_input_tokens`**：本次请求写入提示词缓存的输入 token 数。

- **`cache_read_input_tokens`**：本次请求从提示词缓存读取的输入 token 数。

- **`speed`**：本次请求速度档位。

### 控制台可观测性

控制台 [Managed Agents](https://ark.volcengine.com/region:cn-beijing/managedAgents) 提供 Session 的可视化时间线视图：

- **Session 列表**：全部 Session 及其状态、创建时间、模型。

- **追踪视图**：Session 内事件按时间排序展示，仅对开发者与管理员可见。

- **工具执行**：每次工具调用及其结果。

### 调试技巧

- **关注 ** **`session.error`**：根据 `error.retry_status.type` 判断服务端会重试、结束当前轮次还是终止 Session。

- **检查工具结果**：失败的工具调用通常解释了 Agent 异常行为的原因。

- **跟踪 token 用量**：监控消耗以优化提示并降低成本。

## 相关文档

- 配置内置工具、MCP 工具和自定义工具，详情请参见 [Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)。

- 观察主线程与子线程之间的任务委派，详情请参见 [编排 Multi Agent](https://ark.volcengine.com/region:cn-beijing/docs/multi-agent)。

- 查询、更新、升级或删除 Session，详情请参见 [管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/manage-session)。

<a id="doc-2553726"></a>

---

## 使用 Vaults 认证

> 来源：[https://docs.volcengine.com/docs/82379/2553726?lang=zh](https://docs.volcengine.com/docs/82379/2553726?lang=zh)

当你使用 Managed Agents 构建需要访问第三方服务的应用时，可以通过 Vaults（凭据保管库）保存并隔离每个应用用户的凭据。你的服务端只需为每个用户创建 Vault、写入 Credential，并保存用户与 `vault_id` 的映射，无需自行维护密钥存储，也无需在每次调用中重复传递 token。

创建 Session 时，通过 `vault_ids` 传入当前用户的 Vault ID；Agent 调用 MCP 服务时会自动使用对应的 Credential 完成鉴权。这样，同一个 Agent 可以安全地服务多个用户，并分别访问各自有权限的第三方资源。

> **示例**

> 你使用 Managed Agents 构建了一个 GitHub 代码助手。Alice 发起任务时，服务端创建 Session 并绑定 Alice 的 Vault，Agent 使用 Alice 的 GitHub 权限；Bob 发起任务时，服务端绑定 Bob 的 Vault，Agent 使用 Bob 的 GitHub 权限。

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/base-url-and-authentication)。

## 创建 Vault

> **权限边界**

> Vault 和凭据在工作空间内共享，不按 API Key 单独隔离。持有同一工作空间任一 API Key 的服务，都可以通过 Vault ID 在 Session 中引用其中的凭据。仅向受信任的服务开放工作空间 API Key。

Vault 是绑定到某个终端用户的凭据集合。给它一个 `display_name`，可选用 `metadata` 标记以便映射回你自己的用户记录：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/vaults \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Alice",
    "metadata": {"external_user_id": "usr_abc123"}
  }'
```

响应是完整的 Vault 记录：

```json
{
  "type": "vault",
  "id": "vlt-20260701120000-pqrst",
  "display_name": "Alice",
  "metadata": { "external_user_id": "usr_abc123" },
  "created_at": "2026-06-29T10:00:00Z",
  "updated_at": "2026-06-29T10:00:00Z"
}
```

## 添加凭据

| 类型                   | 适用场景                                                           | 注入方式                                                                            |
| ---------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| `mcp_oauth`            | MCP 服务器用 OAuth 2.0                                             | Session 连接 MCP URL 时注入访问令牌；启用自动刷新后，平台在令牌临近过期时换取新令牌 |
| `static_bearer`        | MCP 用固定 Bearer token（API Key、个人访问令牌）                   | 无刷新流程，直接注入                                                                |
| `mcp_static`           | MCP 使用固定密钥，需要以 Bearer、自定义 Header 或 URL 查询参数注入 | 通过 `injection_type` 选择注入位置                                                  |
| `environment_variable` | 通过环境变量鉴权的命令行、SDK、直接 API 调用                       | 沙箱内是不透明占位符，**出口处**替换为真实值，Agent 永远看不到密钥                  |
| `env_oauth`            | CLI 或 SDK 使用短效 OAuth 访问令牌                                 | 按 `secret_name` 注入；启用自动刷新后，在 Session 使用凭据时换取新令牌              |

你提供的实际密钥（`token`、`access_token`、`refresh_token`、`client_secret`、`secret_value`）是敏感的**只写**字段，API 响应不会返回这些字段。

### OAuth 回调地址

使用方舟发起 OAuth 授权流程时，根据凭据类型配置回调地址：

| 凭据类型                  | 回调地址                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------- |
| MCP OAuth（`mcp_oauth`）  | `https://console.volcengine.com/api/top/ark/cn-beijing/2024-01-01/VaultOAuthCallback` |
| 环境 OAuth（`env_oauth`） | `https://ark-agent.cn-beijing.volces.com/api/v3/vault_env_oauth_callback`             |

方舟按渠道和凭据类型使用固定回调地址，业务应用不需要提供自定义 callback URL。

> **回调地址边界**

- > 在第三方 OAuth 应用中精确登记表格中的完整 URL，不要通配整个控制台或 `ark-agent` 域名。

- > MCP OAuth 和环境 OAuth 使用不同的回调地址，不能互相替代。

### MCP OAuth 凭据

当 MCP 服务器使用 OAuth 2.0 时，使用 `mcp_oauth`。创建凭据时，配置 MCP 服务地址和当前访问令牌。

如需启用自动刷新，创建或更新凭据时同时提供 `expires_at` 和完整的 `refresh` 对象。Session 连接 MCP 服务时，平台读取该凭据。访问令牌进入 `expires_at` 前 5 分钟刷新窗口时，平台自动换取并注入新令牌。该机制由读取操作触发，不在后台定时刷新。

`refresh.token_endpoint_auth.type` 三选一：

- **`none`**：公共客户端，不提供 `client_secret`。

- **`client_secret_basic`**：使用 `client_secret` 的 HTTP Basic 鉴权，同时提供 `client_secret`。

- **`client_secret_post`**：把 `client_secret` 放在 POST 请求体里，同时提供 `client_secret`。

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/vaults/vlt-20260701120000-pqrst/credentials \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Alice Slack",
    "auth": {
      "type": "mcp_oauth",
      "mcp_server_url": "https://mcp.slack.com/mcp",
      "access_token": "xoxp-...",
      "expires_at": "2099-12-31T23:59:59Z",
      "refresh": {
        "token_endpoint": "https://slack.com/api/oauth.v2.access",
        "client_id": "1234567890.0987654321",
        "scope": "channels:read chat:write",
        "refresh_token": "xoxe-1-...",
        "token_endpoint_auth": {
          "type": "client_secret_post",
          "client_secret": "abc123..."
        }
      }
    }
  }'
```

### MCP 静态 Bearer 凭据

当 MCP 服务器接受固定 Bearer token（API Key、个人访问令牌） 时，用 `static_bearer`。无需刷新流程：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/vaults/vlt-20260701120000-pqrst/credentials \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Linear API key",
    "auth": {
      "type": "static_bearer",
      "mcp_server_url": "https://mcp.linear.app/mcp",
      "token": "lin_api_your_linear_key"
    }
  }'
```

### MCP 静态凭据

当 MCP 服务器使用固定密钥，但不一定采用 Bearer 鉴权时，使用 `mcp_static`。通过 `injection_type` 选择注入方式：

- **`bearer`**：以 `Authorization: Bearer <token>` 请求头注入。不要传 `injection_name`，`token` 中也不要包含 `Bearer` 前缀。

- **`header`**：以自定义请求头注入，必须通过 `injection_name` 指定请求头名称。

- **`query`**：以 URL 查询参数注入，必须通过 `injection_name` 指定参数名。

以下示例将密钥注入 `X-API-Key` 请求头：

```json
{
  "auth": {
    "type": "mcp_static",
    "mcp_server_url": "https://mcp.example.com/mcp",
    "token": "<MCP_API_KEY>",
    "injection_type": "header",
    "injection_name": "X-API-Key"
  }
}
```

> **说明**

> **最小权限原则。** 把固定密钥的权限范围限定为 Agent 所需的最小集合。Agent 可以执行该密钥允许的任何操作，过大的权限范围会在 Agent 异常时扩大影响。

### 环境变量凭据

用 `environment_variable` 通过环境变量对外部服务鉴权，适用于命令行、SDK 或直接 API 调用。

`networking.allowed_hosts` 控制密钥可以被替换到哪些出站主机：

- `"type": "limited"` + 显式主机列表（**推荐**）。

- `"type": "unrestricted"`（仅当调用方访问的域名无法提前枚举时使用）。

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/vaults/vlt-20260701120000-pqrst/credentials \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Notion API key for sandbox",
    "auth": {
      "type": "environment_variable",
      "secret_name": "NOTION_API_KEY",
      "secret_value": "sk-your-secret-here",
      "networking": {
        "type": "limited",
        "allowed_hosts": ["api.notion.com"]
      }
    }
  }'
```

> **说明**

> **替换仅发生在出站方向。** 如果客户端使用存储的密钥交换临时访问令牌，返回的令牌会以未脱敏形式进入运行环境。此类流程应由业务侧管理令牌交换和存储。

### 环境 OAuth 凭据

当 CLI 或 SDK 需要从环境变量读取 OAuth 访问令牌时，使用 `env_oauth`。创建凭据时，先配置当前访问令牌（`access_token`）、注入令牌的环境变量名（`secret_name`），以及允许携带该令牌的目标主机（`networking`）。

对于会过期的访问令牌，可以启用自动刷新。创建或更新凭据时，提供以下信息：

- **刷新时机**：通过 `expires_at` 提供当前访问令牌的过期时间。

- **令牌端点**：通过 `refresh.token_endpoint` 提供第三方 OAuth 服务的令牌端点。

- **刷新令牌**：通过 `refresh.refresh_token` 提供换取新访问令牌的凭据。

- **客户端身份**：通过 `refresh.client_id` 和 `refresh.token_endpoint_auth` 提供 OAuth 客户端身份及鉴权方式。鉴权方式需要客户端密钥时，还需提供 `refresh.token_endpoint_auth.client_secret`。

Session 访问 `networking` 允许的目标主机时，平台读取该凭据。访问令牌进入 `expires_at` 前 5 分钟刷新窗口时，平台调用 `refresh.token_endpoint` 获取新令牌、更新凭据，再把新令牌注入出站请求。该机制由读取操作触发，不在后台定时刷新。

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/vaults/vlt-20260701120000-pqrst/credentials \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "GitHub CLI OAuth",
    "auth": {
      "type": "env_oauth",
      "access_token": "<OAUTH_ACCESS_TOKEN>",
      "expires_at": "<ACCESS_TOKEN_EXPIRES_AT>",
      "refresh": {
        "token_endpoint": "https://github.com/login/oauth/access_token",
        "client_id": "<OAUTH_CLIENT_ID>",
        "refresh_token": "<OAUTH_REFRESH_TOKEN>",
        "token_endpoint_auth": {
          "type": "client_secret_post",
          "client_secret": "<OAUTH_CLIENT_SECRET>"
        }
      },
      "secret_name": "GITHUB_TOKEN",
      "networking": {
        "type": "limited",
        "allowed_hosts": ["api.github.com"]
      }
    }
  }'
```

> **注意**

- > **启用自动刷新时，同时提供****`expires_at`****和完整的****`refresh`****对象。** 缺少 `expires_at` 时，平台无法判断刷新时机；缺少 `refresh` 时，平台不会调用 OAuth 令牌端点。

- > **OAuth 令牌端点必须返回 JSON。** `access_token` 必须是字符串；如果返回 `expires_in`，该字段必须是整数秒数。`expires_in` 返回 `"3600"` 等字符串值时，刷新失败。平台会保留当前访问令牌，本次出站请求可能因令牌过期返回 401。检查 `refresh.token_endpoint` 返回字段的类型和 `refresh.token_endpoint_auth` 配置；刷新令牌失效时，重新授权并更新凭据。

- > **替换发生在沙箱出口处，不在沙箱内。** 沙箱里的进程看到的是不透明占位符，而不是真实值。具体影响如下：
  - > 启动时校验凭据格式的客户端可能拒绝占位符。

  - > 用密钥做请求签名的客户端（例如 AWS SigV4）会生成无效签名。

  - > 环境变量注入只适合把密钥值原样写入出站请求头的客户端。

- > **通过凭据限制注入目标。** 在凭据的 `networking` 中设置 `type: limited`，并通过 `allowed_hosts` 显式列出允许注入的出站主机。

### 凭据约束

- **每个 Vault 内的匹配键必须唯一。** `mcp_server_url`（`mcp_oauth`、`static_bearer`、`mcp_static`）和 `secret_name`（`environment_variable`、`env_oauth`）在同一 Vault 的有效凭据中不能重复。重复创建返回 409。

- **匹配键不可变。** 要修改 `mcp_server_url` 或 `secret_name`，需要删除旧凭据，再创建新凭据。

- **每个 Vault 最多包含 100 个凭据。** 超出限制时返回 409。

MCP 类型凭据（`mcp_oauth`、`static_bearer`、`mcp_static`）在创建时会立即连接目标 MCP 服务器探测握手。无法完成握手时，创建请求返回 4xx 错误。`environment_variable` 和 `env_oauth` 不在创建时验证密钥或访问令牌是否有效；无效值会在 Session 访问对应主机时表现为鉴权错误或下游服务错误，但不会阻止 Session 继续运行。

## 在创建 Session 时引用 Vaults

创建 Session 时传 `vault_ids` 数组，把一个或多个 Vaults 挂到 Session：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-20260701120000-abcde",
    "environment_id": "env-20260701120000-fghij",
    "vault_ids": ["vlt-20260701120000-pqrst"],
    "title": "Alice Slack digest"
  }'
```

**运行时行为**：

- 当 Agent 连接到某 MCP URL，且**没有任何凭据匹配** `mcp_server_url` 时，Agent 尝试匿名连接；若服务器要求鉴权则报错。

- **多个 Vaults 都包含匹配凭据时，使用第一个匹配的 Vaults**。

- **Multi Agent 凭据生效范围**：在 [多 Agent](https://ark.volcengine.com/region:cn-beijing/docs/multi-agent) 中，Vaults 凭据**按线程**生效；若某 Agent 自身定义里声明了匹配的 MCP 服务器，该 Agent 用这些凭据鉴权。

## 轮换凭据

密钥值和 `display_name` 可以更新。结构性字段（`mcp_server_url`、`secret_name`、`token_endpoint`、`client_id`） 在创建后即被锁定。要修改结构性字段，删除旧凭据再创建新的：

**Curl**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/vaults/vlt-20260701120000-pqrst/credentials/vcrd-20260701120500-uvwxy \
  -X POST \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "auth": {
      "type": "mcp_oauth",
      "access_token": "xoxp-new-...",
      "expires_at": "2099-12-31T23:59:59Z",
      "refresh": {"refresh_token": "xoxe-1-new-..."}
    }
  }'
```

OAuth 凭据的自动刷新信息可按以下方式更新：

- **启用自动刷新**：在 `auth` 中同时提供 `expires_at` 和完整的 `refresh` 对象。`token_endpoint_auth.type` 为 `client_secret_basic` 或 `client_secret_post` 时，同时提供 `client_secret`；为 `none` 时，省略 `client_secret`。

- **更新自动刷新信息**：只提供需要变更的字段，未提供的字段保留原值。可以更新 `refresh_token`、`token_endpoint_auth`、`resource` 和 `scope`，不能修改 `token_endpoint` 和 `client_id`。

## 凭据生命周期

Session 运行期间，平台在使用凭据时重新解析当前值。更新或删除凭据后，后续请求会读取新的凭据状态，无需重启 Session。

对于已启用自动刷新的 `mcp_oauth`、`env_oauth` 凭据，读取操作会在访问令牌进入 5 分钟刷新窗口时触发自动刷新。刷新成功后，平台持久化新的访问令牌和过期时间；OAuth 服务同时轮换 `refresh_token` 时，平台也会保存新的 `refresh_token`。

## 其他操作

- **列出 Vault、凭据**：调用 `GET /vaults` 或 `GET /vaults/{vault_id}/credentials`；分页返回，按最新优先排序。

- 如需停用单个凭据，删除对应凭据；如需停用某个终端用户的全部凭据，删除对应 Vault。删除操作不可恢复。

<a id="doc-2553727"></a>

---

## 上传与挂载文件

> 来源：[https://docs.volcengine.com/docs/82379/2553727?lang=zh](https://docs.volcengine.com/docs/82379/2553727?lang=zh)

本文介绍如何在方舟 Managed Agents 中上传和挂载输入文件、在 Session 运行时管理文件资源，以及查询 Agent 生成的产物。

Managed Agents 支持挂载通过 [文件输入(Files API)](https://ark.volcengine.com/region:cn-beijing/docs/file-api) 或 [TOS 对象存储](https://www.volcengine.com/docs/6349/74820?lang=zh) 上传的文件，再将文件作为 Session 资源挂载到沙箱环境目录中。Agent 可以读取这些输入文件，并将最终产物写入 `/mnt/session/outputs/`。

> **注意**

> 挂载 TOS 文件前，需要确保 TOS 和 Managed Agents 在同一火山账号中。

## 上传文件

你可以使用 Files API 或 TOS 上传需要被 Agent 读取的文件。

- **使用 Files API**：

  先将本地文件上传到 Files API。上传成功后，平台会返回 `file_id`，后续创建 Session 或向运行中的 Session 追加文件资源时都需要使用该 ID。

  > **说明**

  > 设置 `purpose=agent`，指定该文件供 Agent 使用。

  ```Bash
  file=$(
    curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/files" \
      -H "Authorization: Bearer $ARK_API_KEY" \
      -F 'purpose=agent' \
      -F 'file=@path/of/your/file'
  )

  FILE_ID=$(jq -er '.id' <<<"$file")

  echo "File ID: $FILE_ID"
  ```

- **使用 TOS 上传**：

  以下示例使用 TOS Python SDK 上传本地文件，更多使用方式，请参考 [TOS 官方文档](https://www.volcengine.com/docs/6349/74820?lang=zh) 。

  ```python
  import os
  import tos

  ak = os.getenv("TOS_ACCESS_KEY")
  sk = os.getenv("TOS_SECRET_KEY")

  endpoint = "tos-cn-beijing.volces.com"
  region = "cn-beijing"

  bucket_name = "<BUCKET_NAME>"
  object_key = "<OBJECT_KEY>"   # The path of the uploaded file. For example: agent-files/skill.md
  file_name = "/path/of/your/file"  # Local file path

  try:
      client = tos.TosClientV2(ak, sk, endpoint, region)

      result = client.put_object_from_file(
          bucket_name,
          object_key,
          file_name,
      )

      print("upload success")
      print("request_id:", result.request_id)
      print("etag:", result.etag)

  except tos.exceptions.TosClientError as e:
      print("client error:", e.message)
      print("cause:", e.cause)

  except tos.exceptions.TosServerError as e:
      print("server error code:", e.code)
      print("request_id:", e.request_id)
      print("message:", e.message)
      print("status_code:", e.status_code)
      print("request_url:", e.request_url)

  except Exception as e:
      print("unknown error:", str(e))
  ```

## 在创建 Session 时挂载文件

创建 Session 时，在 `resources` 数组中声明需要挂载的文件。

> **说明**

> 你也可以在 [Sessions 页面](https://ark.volcengine.com/region:cn-beijing/managed-agents/sessions?projectName=default) 创建 Session。

- **通过 Files ID 挂载**：

每个文件资源至少需要包含 `type` 和 `file_id`。

`mount_path` 可选。建议设置 `mount_path`，让 Agent 可以从稳定、可读的路径访问文件。如果不显式指定路径，请确保上传文件名足够清晰，便于 Agent 识别文件用途。

```bash
session=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": "$AGENT_ID",
  "environment_id": "$ENVIRONMENT_ID",
  "resources": [
    {
      "type": "file",
      "file_id": "$FILE_ID",
      "mount_path": "target/mounting/path/of/the/file"
    }
  ]
}
EOF
)

SESSION_ID=$(jq -er '.id' <<<"$session")

echo "Session ID: $SESSION_ID"
```

> **说明**

> `mount_path` 为云端沙箱中的目标挂载路径，通过 `file_id` 传入的文件将按照指定路径挂载到 `/mnt/session/uploads/` 目录中。

> 挂载时如指定 `my-skills/skill-1.md` 路径，挂载文件路径为 `/mnt/session/uploads/my-skills/skill-1.md`。

挂载后，平台会为该 Session 内的文件实例生成新的 `file_id`。这些 Session 内副本不计入用户的文件存储额度。

- **从 TOS 挂载**：

```bash
session=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": "$AGENT_ID",
  "environment_id": "$ENVIRONMENT_ID",
  "resources": [
    {
      "type": "tos",
      "tos_bucket": "<BUCKET_NAME>",
      "tos_key": "path/of/the/tos/directory/"
    }
  ]
}
EOF
)

SESSION_ID=$(jq -er '.id' <<<"$session")

echo "Session ID: $SESSION_ID"
```

> **说明**

- > `tos_key` 需为 TOS 桶中的一个目录，且必须以 `/` 结尾。例如 `project-resources/`。

- > 文件将被挂载至云端沙箱的 `/mnt/session/storage/` 目录中。

## 挂载多个文件

如果一次任务需要多个输入文件，可以在 `resources` 中添加多个条目。单个 Session 最多支持挂载 100 个文件。

以 Files ID 为例：

```json
{
  "resources": [
    { "type": "file", "file_id": "<FILE_ID_1>" },
    { "type": "file", "file_id": "<FILE_ID_2>" },
    { "type": "file", "file_id": "<FILE_ID_3>" }
  ]
}
```

## 在 Session 运行时管理文件

Session 创建后，可以通过 Session Resources API 继续添加文件，并查询当前已挂载的资源。

以下示例沿用前面创建 Session 后写入的 `$SESSION_ID`；添加 Files API 文件时也沿用上传步骤写入的 `$FILE_ID`。

### 添加文件资源

```bash
resource=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions/$SESSION_ID/resources" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "type": "file",
  "file_id": "$FILE_ID"
}
EOF
)

RESOURCE_ID=$(jq -er '.id' <<<"$resource")

echo "Resource ID: $RESOURCE_ID"
```

### 查询文件资源

```Bash
# List session resources
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions/$SESSION_ID/resources" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

## 获取 Session 运行时产物

Agent 将需要交付的文件写入 `/mnt/session/outputs/` 后，平台会将这些文件注册为当前 Session 下 `purpose=agent` 的文件。需要在你的服务中展示或下载报告、图片、视频、文档或代码等产物时，通过 Files API 获取：

1. 调用 `GET /files?scope_id=<session_id>&purpose=agent`，分页查询该 Session 的 Agent 产物。

2. 从列表响应中获取文件 ID、文件名、文件大小、MIME 类型和 `download_url`。

3. 使用 `download_url` 下载文件内容。

Seedance Managed Agent 生成的图片和视频也遵循相同规则。生成工具会将最终产物写入 `/mnt/session/outputs/` 并注册到 Files API，业务系统通过 Session ID 查询和下载产物。

```Bash
# List files associated with a session.
curl -sS --fail-with-body \
  "https://ark.cn-beijing.volces.com/api/v3/files?scope_id=$SESSION_ID&purpose=agent" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

> **说明**

> `scope_id` 只筛选当前 Session 下已注册为 `purpose=agent` 的输出产物，不包含通过 `resources` 挂载到 `/mnt/session/uploads/` 的输入文件副本，也不按 `/mnt/session/storage/` 中的用户 TOS 对象做目录扫描。查询挂载输入文件，调用 [查询会话资源列表](https://ark.volcengine.com/region:cn-beijing/docs/list-resources-api)。

列表响应中的 `download_url` 是服务端签发的预签名下载地址，具有时效性。

创建 Session 前，按产物的保留要求选择存储方式：

| 你的配置                                          | 产物位置                                                                                                                                                    | 你需要做什么                                                                     |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 不配置 `config.tos`                               | 方舟公共 TOS                                                                                                                                                | 在平台 TTL 到期前下载所需文件。删除 Session 时，平台会清理默认存储中的关联产物。 |
| 在 Environment 或 Session 覆写中配置 `config.tos` | 你的 TOS Bucket。平台从 outputs 目录注册产物时，Files API 写入前缀为 `{prefix}outputs/{env-id}/{session-id}/`；最终对象路径以响应中的 `tos.object_key` 为准 | 在 TOS 中管理对象生命周期。删除 Session 不会删除 Bucket 中的对象。               |

配置 Environment 产物存储及按 Session 临时覆写的方法，详情请参见 [配置产物存储](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment#configure-output-storage) 和 [覆写产物存储](https://ark.volcengine.com/region:cn-beijing/docs/start-session#override-output-storage)。

> **说明**

> 需要保存 Agent 产物时配置 `config.tos`，对应 Files API 登记的输出产物存储位置。需要挂载输入或共享数据时配置 `resources` 中的 `type: tos`，对应沙箱内的 `/mnt/session/storage/`。

## 支持的文件类型

Agent 可以处理多种文件类型。常见类型包括：

- 源代码文件，例如 `.py`、`.js`、`.ts`、`.go`、`.rs` 等。

- 数据文件，例如 `.csv`、`.json`、`.xml`、`.yaml`。

- 文本文档，例如 `.txt`、`.md`。

- 归档文件，例如 `.zip`、`.tar.gz`；Agent 可以在沙箱中使用 bash 解压后处理。

- 二进制文件；是否能正确处理取决于沙箱内是否具备相应工具。

## 使用建议

- 挂载到沙箱中的文件是只读副本。Agent 可以读取这些文件，但不能直接修改原始上传文件。

- 如果任务需要产出修改后的版本，应将结果写入沙箱中的新路径。

- 需要回传的输出文件，应在任务说明中要求 Agent 写入 `/mnt/session/outputs/`。需要长期保存时，为 Environment 配置自己的 TOS Bucket，并在 TOS 中管理对象生命周期。

- 如果运行中的 Session 不再需要某个大文件，及时移除对应资源，减少后续工具调用的上下文干扰。

<a id="doc-2553728"></a>

---

## 持久化记忆

> 来源：[https://docs.volcengine.com/docs/82379/2553728?lang=zh](https://docs.volcengine.com/docs/82379/2553728?lang=zh)

本文介绍如何在方舟 Managed Agents 中使用记忆存储（Memory Store），为 Agent 提供可跨 Session 保留的长期记忆。

默认情况下，每个 Session 都从新的上下文开始。Session 结束后，Agent 在本次运行中积累的偏好、约定、排障经验或业务背景不会自动带到下一次任务中。Memory Store 用于保存这些可复用信息，并在后续 Session 中重新挂载给 Agent 使用。

## 基本概念

一个 Memory Store 可以保存多条 Memory。每条 Memory 都通过独立的 `path` 标识，便于按用途组织，并在不同 Session 中复用。

Memory 的创建、读取、更新和删除不依赖 Session。要让 Agent 在运行过程中使用这些内容，需要在创建 Session 时将 Memory Store 挂载为资源。

挂载 Memory Store 后，`access` 决定 Agent 可以执行的操作。您也可以通过控制台直接管理 Memory。

## 创建 Memory Store

创建 Memory Store 时必须提供 `name`；`description` 可选。提供 `description` 时，该字段会展示给 Agent，用于说明这个 Store 中保存的内容和使用场景。

```bash
store=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "<MEMORY_STORE_NAME>",
      "description": "<MEMORY_STORE_DESCRIPTION>"
    }'
)

STORE_ID=$(jq -er '.id' <<<"$store")

echo "Memory Store ID: $STORE_ID"
```

返回的 Memory Store ID 通常形如 `memstore-...`。创建 Session 并挂载记忆时，需要传入该 ID。

## 预置 Memory 内容

在 Agent 开始运行前，可以先向 Store 中写入参考资料，例如项目规范、用户偏好、输出格式、术语表等。

根据写入数量选择接口：

- **写入单条 Memory**：调用 [创建记忆](https://ark.volcengine.com/region:cn-beijing/docs/create-memory-api)，适合补充零散内容。

- **批量写入 Memory**：调用 [批量创建记忆](https://ark.volcengine.com/region:cn-beijing/docs/batch-create-memories-api)，适合初始化术语表、用户偏好或输出模板。

```Bash
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/<MEMORY_1>.md",
    "content": "<CONTENT_OF_THE_MEMORY>"
  }'
```

单条 Memory 的内容上限为 100 KiB（102400 字节），约 25k tokens。单个 Store 最多保存 2,000 条 Memory。建议将记忆拆成多个小而聚焦的文件，避免使用长文档。

## 挂载 Memory Store 到 Session

Memory Store 需要在创建 Session 时通过 `resources` 数组挂载。与文件和代码仓库资源不同，Memory Store 只能在 Session 创建时挂载，不支持在运行中的 Session 中追加或移除。

在 `resources` 数组中，将资源 `type` 设置为 `memory_store`，并通过 `memory_store_id` 指定要挂载的 Memory Store。

以下示例将访问权限显式设置为 `read_only`，使 Agent 可以读取 Memory Store，但不能修改其中的内容。

```bash
session=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d @- <<EOF
{
  "agent": "$AGENT_ID",
  "environment_id": "$ENVIRONMENT_ID",
  "resources": [
    {
      "type": "memory_store",
      "memory_store_id": "$STORE_ID",
      "access": "read_only"
    }
  ]
}
EOF
)

printf '%s\n' "$session"

SESSION_ID=$(jq -er '.id' <<<"$session")
```

`access` 默认为 `read_write`。如果 Agent 会处理用户输入、网页内容或第三方工具输出等不可信内容，需要谨慎授予写权限。一次成功的 Prompt Injection 可能把恶意内容写入 Store，后续 Session 又会将其作为可信记忆读取。对于共享规范、知识库、术语表等参考内容，建议使用 `read_only`。

单个 Session 最多挂载 10 个 Memory Store。可以按不同使用场景拆分 Store，例如分别管理用户偏好、项目上下文和团队共享规范。

## Agent 如何访问 Memory

- **`read_only`**：Agent 可以浏览、读取和搜索 Memory，但不能修改内容。

- **`read_write`**：Agent 除了读取 Memory，还可以创建、更新、删除或移动内容。修改会保存在挂载的 Memory Store 中，供后续 Session 继续使用。

- Agent 对 Memory 的读取会作为普通工具调用出现在 Session 事件流中，例如 `agent.tool_use` 和 `agent.tool_result`。

## 查看和编辑 Memory

您可以通过 [查询记忆列表](https://ark.volcengine.com/region:cn-beijing/docs/list-memories-api)、[查询记忆详情](https://ark.volcengine.com/region:cn-beijing/docs/get-memory-api)、[更新记忆](https://ark.volcengine.com/region:cn-beijing/docs/update-memory-api) 和 [删除记忆](https://ark.volcengine.com/region:cn-beijing/docs/delete-memory-api) 查询和维护 Memory，用于审核内容、修正错误或清理过期信息。

### 查询 Memory 列表

可通过 `path_prefix` 按路径前缀浏览 Memory，类似查看目录。

```Bash
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories?path_prefix=/&order_by=path&depth=2" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  | jq -r '.data[] | "\(.type)  \(.path)"'
```

### 读取 Memory

读取单条 Memory 会返回完整内容。

```Bash
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories/$MEMORY_ID" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  | jq -r '.content'
```

### 创建 Memory

`create` 会在指定 `path` 下新建 Memory；如果路径已存在，不会覆盖原内容。修改已有 Memory 请使用更新接口。

```bash
memory=$(
  curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
      "path": "/path/of/the/memory",
      "content": "<CONTENT_OF_THE_MEMORY>"
    }'
)

MEMORY_ID=$(jq -r '.id' <<<"$memory")
MEMORY_SHA=$(jq -r '.content_sha256' <<<"$memory")
```

### 更新 Memory

更新接口可修改内容、路径，或同时修改二者。修改路径可用于重命名或归档。

```Bash
curl -sS --fail-with-body -X POST "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories/$MEMORY_ID" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/new/path"
  }'
```

### 删除 Memory

```Bash
curl -sS --fail-with-body -X DELETE "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories/$MEMORY_ID" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

## 管理 Memory Store

除创建外，Memory Store 还支持查询、删除等操作。

### 查询 Store 列表

```Bash
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

### 删除 Store

```Bash
curl -sS --fail-with-body -X DELETE "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

## 最佳实践

当 Store 达到 2,000 条 Memory 上限后，新 Memory 写入会失败。已有 Memory 仍可读取和更新。建议按以下方式管理长期记忆：

- **按用途拆分 Store**：不要把所有内容放进一个通用 Store。可以按用户、团队共享知识、项目上下文分别建 Store。

- **在接近上限前整理内容**：定期删除过期或重复 Memory，或将碎片化内容整理成更稳定的摘要 Store。

<a id="doc-2553729"></a>

---

## Advisor

> 来源：[https://docs.volcengine.com/docs/82379/2553729?lang=zh](https://docs.volcengine.com/docs/82379/2553729?lang=zh)

Advisor 是方舟 Managed Agents 提供的进阶能力，属于 `evolution`（演进工具）下的核心能力之一，也是「Agent 自我进化」能力的重要组成部分。开启后，当 Agent 在执行任务过程中多次执行失败、遇到当前模型无法处理的问题时，会自动唤起更强的顾问模型提供指导，帮助 Agent 突破能力边界、提升复杂任务的完成质量。

本文介绍如何为 Agent 开启和使用 Advisor。

> **说明**

> 此为邀测能力，如需使用，提交 [测试申请工单](https://console.volcengine.com/auth/login?redirectURI=%2Fworkorder%2Fcreate%3Fstep%3D2%26SubProductID%3DP00001166)。

## Advisor 配置说明

Advisor 是 `evolution` 工具集中的一个子工具，通过 Agent 的 `tools` 字段配置。

配置结构如下：

```json
{
  "tools": [
    {
      "type": "evolution",
      "configs": [
        {
          "name": "advisor",
          "enabled": true
        }
      ]
    }
  ]
}
```

> **说明**

> 当前不支持在 `evolution` 工具集上配置 `default_config`，也不支持为 `advisor` 单独配置 `permission_policy`。

字段说明

---

**tools[].type** `string` `必填`

工具类型，固定为 `evolution`。

---

**tools[].configs** `object[]` `必填`

演进工具的子工具配置列表。`tools[].type` 为 `evolution` 时，必须包含 `name` 为 `advisor` 的配置项。

---

configs.**name** `string` `必填`

子工具名称，Advisor 对应值为 `advisor`。

---

configs.**enabled** `boolean` `选填`

是否启用该子工具。默认值：`true`。

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/base-url-and-authentication)。

## 开启 Advisor

### 控制台方式

1. 进入 [Agent 管理页面](https://ark.volcengine.com/region:cn-beijing/managed-agent/agents) 。

2. 点击「创建 Agent」，或在已有 Agent 卡片上点击「编辑」。

3. 在「05 高级参数」区域，找到「Advisor」开关，打开即可。

4. 点击「保存」。

开启后，该 Agent 的所有新 Session 都会自动启用 Advisor 能力。

### API 方式

创建或更新 Agent 时，在 `tools` 中挂载 `evolution` 类型并配置 `advisor` 即可开启。

#### 创建时开启

**cURL**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-advisor-agent",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "system": "你是一个资深的代码工程师，负责处理复杂的编码和故障排查任务。",
    "tools": [
      {
        "type": "agent_toolset_20260916"
      },
      {
        "type": "evolution",
        "configs": [
          {
            "name": "advisor",
            "enabled": true
          }
        ]
      }
    ]
  }'
```

示例响应如下：

```Plain Text
{
  "id": "agent-20260702070355-xxxxx",
  "type": "agent",
  "name": "my-advisor-agent",
  "version": 1,
  "model": {
    "id": "doubao-seed-2-1-pro-260628",
    "speed": "standard"
  },
  "tools": [
    {
      "type": "agent_toolset_20260916",
      "default_config": {
        "enabled": true
      }
    },
    {
      "type": "evolution",
      "configs": [
        {
          "name": "advisor",
          "enabled": true
        }
      ]
    }
  ],
  "created_at": "2026-07-02T07:03:55Z",
  "updated_at": "2026-07-02T07:03:55Z"
}
```

#### 更新已有 Agent

> **说明**

> 更新时需要传入当前 `version`，版本号不匹配会更新失败。仅更新 `display_name` 时不生成新版本；更新 `advisors`、`system` 等版本化字段时，即使提交值与当前版本相同，也会生成新版本。

**cURL**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents/{agent_id} \
  -X POST \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "version": 1,
    "tools": [
      {
        "type": "agent_toolset_20260916"
      },
      {
        "type": "evolution",
        "configs": [
          {
            "name": "advisor",
            "enabled": true
          }
        ]
      }
    ]
  }'
```

> **注意**

> `tools` 使用覆盖逻辑。请求体一旦传入 `tools`，系统会用该数组整体覆盖 Agent 当前的 `tools` 配置，不会在原有基础上追加。如果你只想新增或调整某个工具，需要先读取当前 Agent 的 `tools`，再把「需要保留的工具 + 新的工具」一起写回请求体。

## 工作方式

开启 Advisor 后，Agent 在 Session 中执行任务时，遇到以下情况会自动唤起顾问模型：

- Agent 多次执行失败，无法通过自身能力解决

- 当前模型返回工具调用异常、输出格式错误

- 反复卡在同一问题无法推进

- 系统提示词中引导的关键决策节点

顾问模型会接收完整的任务上下文，给出指导建议后，Agent 继续执行。整个过程无需人工干预。

## 观察 Advisor 调用

Advisor 的调用过程可以通过控制台 Debug 模式观测。当前公开的 Session 事件结构中未定义独立的 Advisor 调用事件；通过事件流 API 可以继续观察 Agent 消息、工具调用、状态变化等会话执行过程。

查看方式：

- **控制台 Debug 模式**：在 Agent 调试页面切换到 Debug 模式，可直观看到 Advisor 调用的时间点和内容。

- **事件流 API**：通过 Session Events 接口获取完整事件流，编程方式处理会话执行事件。

事件流的使用方式详见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)。

## 系统提示词建议

开启 Advisor 后，建议在系统提示词中明确告知 Agent 何时应该寻求顾问帮助，以获得更好的效果。以下是一段参考模板：

```text
你是一个专业的代码工程师。你有一个顾问模型可以提供更高级的架构指导和代码审查。

在以下情况下，请主动寻求顾问的帮助：
1. 在开始编写复杂代码之前，先咨询架构设计方案
2. 当你连续两次尝试同一问题仍未解决时
3. 当你需要在多个技术方案之间做选择时
4. 在任务完成前，进行最终的代码质量审查

对于顾问的建议，请认真考虑并采纳。如果你有充分的理由认为建议不适用，可以坚持自己的方案，但需要在回复中说明原因。
```

## 计费说明

Advisor 调用产生的 Token 费用按顾问模型的价格单独计费，不计入执行模型的用量。具体计费规则请参见 [Managed Agents 计费说明](https://ark.volcengine.com/region:cn-beijing/docs/model-pricing#ma_billing)。

> **说明**

> Beta 阶段 Advisor 功能可能有免费额度或优惠政策，具体以控制台公示为准。

## 相关文档

[Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)

了解 Agent 支持的公开工具类型和配置方式。

[Multi Agent](https://ark.volcengine.com/region:cn-beijing/docs/multi-agent)

Multi Agent 支持一个协调器 Agent 将任务委派给多个子智能体并行执行。

[Outcome](https://ark.volcengine.com/region:cn-beijing/docs/define-outcome)

Outcome 支持定义任务验收标准，Agent 自动迭代直到达标。

[Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)

通过事件流实时观测 Agent 的执行过程，包括消息、工具调用和状态变化。

<a id="doc-2553730"></a>

---

## 编排 Multi Agent

> 来源：[https://docs.volcengine.com/docs/82379/2553730?lang=zh](https://docs.volcengine.com/docs/82379/2553730?lang=zh)

Multi Agent（多智能体协作）允许协调器 Agent 将任务委派给多个子智能体。子智能体使用各自的模型、系统提示词、技能和工具完成任务。协调器负责拆分任务并汇总结果。

本页从已有子智能体和 Environment 开始，演示如何创建协调器、运行任务、验证结果和处理异常。

## 选择并准备

Multi Agent 适合需要角色分工或并行处理的复杂任务。如果任务不需要独立上下文或角色边界，优先使用更简单的方式。

| 任务特征                               | 推荐方式               | 适用场景                       |
| -------------------------------------- | ---------------------- | ------------------------------ |
| 多个角色需要使用不同提示词、模型或工具 | Multi Agent            | 代码审查、测试设计等多角色任务 |
| 单个 Agent 按固定步骤调用工具          | Agent + 工具（Tools）  | 流程固定，不需要独立角色上下文 |
| 单个 Agent 复用操作说明                | Agent + 技能（Skills） | 需要沉淀流程，但不需要角色协作 |

开始前，完成以下准备：

- 创建 API Key，并配置为 `ARK_API_KEY`。详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey) 。

- 创建两个子智能体，并记录 Agent ID 和版本号。详情请参见 [Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)。

- 创建或复用云环境，并记录 Environment ID。详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment)。

- 选择协调器模型，并确认本地已安装 `curl` 和 `jq`。

配置环境变量：

```Bash
export ARK_API_KEY="<ARK_API_KEY>"
export ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
export COORDINATOR_MODEL_ID="<MODEL_ID>"
export REVIEWER_AGENT_ID="<REVIEWER_AGENT_ID>"
export REVIEWER_AGENT_VERSION="<REVIEWER_AGENT_VERSION>"
export TESTER_AGENT_ID="<TESTER_AGENT_ID>"
export TESTER_AGENT_VERSION="<TESTER_AGENT_VERSION>"
export ENVIRONMENT_ID="<ENVIRONMENT_ID>"
```

## 配置协调器

### 定义角色和委派规则

协调器的系统提示词需要说明每个子智能体的职责和输出。角色边界越明确，协调器越容易正确分配任务。

| 角色       | 职责                                             |
| ---------- | ------------------------------------------------ |
| 代码审查员 | 检查代码质量、架构和安全风险，输出问题与修改建议 |
| 测试工程师 | 设计测试用例，说明覆盖范围和未覆盖风险           |

系统提示词还需要要求协调器先拆分和委派任务，收到全部结果后再汇总。下方 cURL 包含完整提示词。

### 创建协调器

在 `multiagent.agents` 中传入子智能体 ID 和版本号。显式填写版本后，子智能体发布新版本不会改变当前协调器。

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${COORDINATOR_MODEL_ID:?Set COORDINATOR_MODEL_ID}"
: "\\${REVIEWER_AGENT_ID:?Set REVIEWER_AGENT_ID}"
: "\\${REVIEWER_AGENT_VERSION:?Set REVIEWER_AGENT_VERSION}"
: "\\${TESTER_AGENT_ID:?Set TESTER_AGENT_ID}"
: "\\${TESTER_AGENT_VERSION:?Set TESTER_AGENT_VERSION}"

COORDINATOR_SYSTEM='你有以下团队成员：

【代码审查员】
- 检查代码质量、架构和安全风险。
- 输出问题列表、严重程度和修改建议。
- 不负责编写新功能。

【测试工程师】
- 设计测试用例并检查覆盖范围。
- 输出测试用例、覆盖范围和未覆盖风险。
- 不修改产品需求。

先拆分任务，再分别委派。收到全部结果后，汇总为最终结论。'
export COORDINATOR_SYSTEM

coordinator_payload=$(
  jq -n \
    --arg model_id "$COORDINATOR_MODEL_ID" \
    --arg system "$COORDINATOR_SYSTEM" \
    --arg reviewer_id "$REVIEWER_AGENT_ID" \
    --argjson reviewer_version "$REVIEWER_AGENT_VERSION" \
    --arg tester_id "$TESTER_AGENT_ID" \
    --argjson tester_version "$TESTER_AGENT_VERSION" \
    '{
      name: "engineering-lead",
      model: {id: $model_id},
      system: $system,
      multiagent: {
        type: "coordinator",
        agents: [
          {type: "agent", id: $reviewer_id, version: $reviewer_version},
          {type: "agent", id: $tester_id, version: $tester_version}
        ]
      }
    }'
)

coordinator=$(
  curl -sS --fail-with-body "$ARK_BASE_URL/agents" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$coordinator_payload"
)

export COORDINATOR_AGENT_ID
export COORDINATOR_AGENT_VERSION
COORDINATOR_AGENT_ID=$(jq -er '.id' <<<"$coordinator")
COORDINATOR_AGENT_VERSION=$(jq -er '.version' <<<"$coordinator")

printf 'Coordinator Agent ID: %s\n' "$COORDINATOR_AGENT_ID"
printf 'Coordinator Agent version: %s\n' "$COORDINATOR_AGENT_VERSION"
```

脚本将协调器 ID 和版本号写入 `COORDINATOR_AGENT_ID` 和 `COORDINATOR_AGENT_VERSION`。字段说明请参见 [创建智能体](https://ark.volcengine.com/region:cn-beijing/docs/create-agent-api)。

也可以通过控制台配置：

1. 进入 [Agent 管理页面](https://ark.volcengine.com/region:cn-beijing/managed-agent/agents) 。

2. 创建或编辑 Agent，在 **04 能力扩展** 区域添加 **Multi Agents**。

3. 选择子智能体，填写前面的系统提示词并保存。

4. 记录协调器 Agent ID 和版本号。

## 运行任务

主流程使用两个终端。终端 A 接收主事件流，终端 B 创建 Session、发送任务和查询结果。需要实时观察子线程时，再打开终端 C。

### 1. 创建 Session

在终端 B 创建 Session：

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${COORDINATOR_AGENT_ID:?Set COORDINATOR_AGENT_ID}"
: "\\${ENVIRONMENT_ID:?Set ENVIRONMENT_ID}"

payload=$(
  jq -n \
    --arg agent_id "$COORDINATOR_AGENT_ID" \
    --arg environment_id "$ENVIRONMENT_ID" \
    '{
      agent: $agent_id,
      environment_id: $environment_id,
      title: "Multi Agent task"
    }'
)

session=$(
  curl -sS --fail-with-body "$ARK_BASE_URL/sessions" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$payload"
)

export SESSION_ID
SESSION_ID=$(jq -er '.id' <<<"$session")
printf 'Session ID: %s\n' "$SESSION_ID"
```

脚本将 Session ID 写入 `SESSION_ID`：

```Text
Session ID: sesn-20261005121000-xxxxx
```

### 2. 订阅主事件流

在终端 A 配置同一个 Session ID：

```Bash
export ARK_API_KEY="<ARK_API_KEY>"
export ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
export SESSION_ID="<SESSION_ID>"
```

建立主事件流：

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"

curl -sS -N --fail-with-body --max-time 600 \
  "$ARK_BASE_URL/sessions/$SESSION_ID/events/stream" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Accept: text/event-stream"
```

终端 A 输出 `: ready` 后保持连接。先建连再发送任务，可以避免遗漏实时事件。

### 3. 发送任务

回到终端 B，配置任务内容：

```Bash
TASK_TEXT=$(cat <<'EOF'
评审以下 Python 登录接口实现。代码审查员分析代码质量、架构和安全风险，测试工程师设计测试用例并说明覆盖范围，最后汇总结论。

def login(username, password):
    key = f"login_failures:{username}"
    failures = int(redis.get(key) or 0)
    if failures >= 5:
        return {"ok": False, "reason": "locked"}
    if not verify_password(username, password):
        redis.incr(key)
        redis.expire(key, 1800)
        return {"ok": False, "reason": "denied"}
    redis.delete(key)
    return {"ok": True}
EOF
)
export TASK_TEXT
```

发送任务：

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"
: "\\${TASK_TEXT:?Set TASK_TEXT}"

payload=$(
  jq -n --arg text "$TASK_TEXT" \
    '{events: [{type: "user.message", content: [{type: "text", text: $text}]}]}'
)

curl -sS --fail-with-body \
  "$ARK_BASE_URL/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$payload"
```

#### 实时观察子线程（可选）

只在需要实时观察子智能体时执行以下步骤：

1. 终端 A 收到 `session.thread_created` 后，记录 `session_thread_id`。

2. 在终端 C 配置鉴权信息、Session ID 和子线程 ID：

   ```Bash
   export ARK_API_KEY="<ARK_API_KEY>"
   export ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
   export SESSION_ID="<SESSION_ID>"
   export SESSION_THREAD_ID="<SESSION_THREAD_ID>"
   ```

3. **建立子线程事件流**：

   **cURL**

```Bash
   : "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
   : "\\${ARK_API_KEY:?Set ARK_API_KEY}"
   : "\\${SESSION_ID:?Set SESSION_ID}"
   : "\\${SESSION_THREAD_ID:?Set SESSION_THREAD_ID}"

   curl -sS -N --fail-with-body --max-time 600 \
     "$ARK_BASE_URL/sessions/$SESSION_ID/threads/$SESSION_THREAD_ID/stream" \
     -H "Authorization: Bearer $ARK_API_KEY" \
     -H "Accept: text/event-stream"
```

4. 终端 C 输出 `: ready` 后，在终端 B 按“验证结果”中的方式补查该子线程历史。

5. 按事件 `id` 合并实时和历史事件，忽略重复 ID。

每个子线程使用独立事件流。出现多个 `session_thread_id` 时，分别重复以上步骤。

### 4. 等待本轮结束

继续观察终端 A。正常事件轨迹如下：

```Text
session.thread_created
agent.thread_message_sent
session.thread_status_idle
agent.message
session.status_idle
```

收到 `session.status_idle` 后检查 `stop_reason.type`：

- **`end_turn`**：本轮执行结束，按下一节验证结果。

- **`requires_action`**：保持连接，处理全部待操作事件后继续观察。

- **`retries_exhausted`**：调整输入后可以发送新任务；如果修改 Agent 配置，请创建新 Session。

## 验证结果

在终端 B 列出全部线程：

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"

threads=$(
  curl -sS --fail-with-body \
    "$ARK_BASE_URL/sessions/$SESSION_ID/threads?limit=100" \
    -H "Authorization: Bearer $ARK_API_KEY"
)

printf 'Agent\tThread ID\tStatus\n'
jq -r '.data[] | [.agent_name, .id, .status] | @tsv' <<<"$threads"

export SESSION_THREAD_ID
if ! SESSION_THREAD_ID=$(jq -er '.data[1].id' <<<"$threads"); then
  printf 'No child thread is available yet.\n' >&2
  return 1 2>/dev/null || exit 1
fi
printf 'Selected child thread ID: %s\n' "$SESSION_THREAD_ID"
```

命令将第一个子线程 ID 写入 `SESSION_THREAD_ID`。检查其他子线程时，更新该变量并清空分页标识：

```Bash
unset THREAD_EVENTS_PAGE
```

查询该子线程的完整历史：

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"
: "\\${SESSION_THREAD_ID:?Set SESSION_THREAD_ID}"

request_args=(
  --get
  "$ARK_BASE_URL/sessions/$SESSION_ID/threads/$SESSION_THREAD_ID/events"
  --data-urlencode "limit=100"
  --data-urlencode "order=asc"
)

if [[ -n \\${THREAD_EVENTS_PAGE:-} ]]; then
  request_args+=(--data-urlencode "page=$THREAD_EVENTS_PAGE")
fi

if ! events=$(
  curl -sS --fail-with-body \
    "\\${request_args[@]}" \
    -H "Authorization: Bearer $ARK_API_KEY"
); then
  return 1 2>/dev/null || exit 1
fi

printf 'Events:\n'
jq -c '.data[]' <<<"$events"

export THREAD_EVENTS_PAGE
THREAD_EVENTS_PAGE=$(jq -r '.next_page // empty' <<<"$events")
printf 'Next page: %s\n' "$THREAD_EVENTS_PAGE"
```

如果 `Next page` 不为空，保持 `THREAD_EVENTS_PAGE` 并重复执行，直到下一页标识为空。切换子线程前必须再次清空该变量。

主线程和各子线程使用独立事件流，不能根据跨流到达顺序判断执行顺序。使用 `session_thread_id`、`from_session_thread_id` 和 `to_session_thread_id` 关联委派与返回消息。

只有同时满足以下条件，本轮任务才成功：

- 主线程产生最终 `agent.message`。

- Session 以 `stop_reason.type=end_turn` 进入 `idle`。

- 没有子线程停留在 `running` 或 `rescheduling`。

- 本轮没有出现 `session.thread_status_terminated`。

> **注意**

> HTTP 200 只表示服务端已受理事件。`session.status_idle` 不一定表示任务成功。

## 处理异常状态

| 信号                                | 含义                       | 处理动作                                        |
| ----------------------------------- | -------------------------- | ----------------------------------------------- |
| SSE 连接中断                        | 中断期间的事件不会自动回放 | 先重建连接，再查询历史并按事件 ID 去重          |
| `requires_action`                   | 等待工具确认或外部工具结果 | 处理全部 `stop_reason.event_ids`                |
| `session.thread_status_rescheduled` | 子线程正在自动重新调度     | 保持监听，不要并行手动重试                      |
| `session.thread_status_terminated`  | 子线程已终止               | 查询历史和错误事件，Session 空闲后重新分配任务  |
| 子线程长时间保持 `running`          | 子任务未结束               | 携带 `session_thread_id` 发送 `user.interrupt`  |
| `retries_exhausted`                 | 当前轮次重试次数已用尽     | 调整输入后重试；修改 Agent 配置后创建新 Session |

### 处理等待操作

子线程以 `requires_action` 进入 `idle` 后，按以下步骤处理：

1. 保持主事件流和目标子线程事件流连接。如果连接已关闭，先重新建连并等待 `: ready`，再补查历史。补查主线程前清空分页标识：

   ```Bash
   unset SESSION_EVENTS_PAGE
   ```

   **cURL**

```Bash
   : "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
   : "\\${ARK_API_KEY:?Set ARK_API_KEY}"
   : "\\${SESSION_ID:?Set SESSION_ID}"

   request_args=(
     --get
     "$ARK_BASE_URL/sessions/$SESSION_ID/events"
     --data-urlencode "limit=100"
     --data-urlencode "order=asc"
   )

   if [[ -n \\${SESSION_EVENTS_PAGE:-} ]]; then
     request_args+=(--data-urlencode "page=$SESSION_EVENTS_PAGE")
   fi

   if ! events=$(
     curl -sS --fail-with-body \
       "\\${request_args[@]}" \
       -H "Authorization: Bearer $ARK_API_KEY"
   ); then
     return 1 2>/dev/null || exit 1
   fi

   printf 'Events:\n'
   jq -c '.data[]' <<<"$events"

   export SESSION_EVENTS_PAGE
   SESSION_EVENTS_PAGE=$(jq -r '.next_page // empty' <<<"$events")
   printf 'Next page: %s\n' "$SESSION_EVENTS_PAGE"
```

2. 从 `session.thread_status_idle` 中记录 `session_thread_id` 和全部 `stop_reason.event_ids`。按“验证结果”中的方式补查子线程，并逐一找到这些事件。

3. 对 `agent.tool_use` 或 `agent.mcp_tool_use`，配置关联 ID 并发送确认：

   ```Bash
   export SESSION_THREAD_ID="<SESSION_THREAD_ID>"
   export TOOL_USE_ID="<TOOL_USE_ID>"
   ```

   **cURL**

```Bash
   : "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
   : "\\${ARK_API_KEY:?Set ARK_API_KEY}"
   : "\\${SESSION_ID:?Set SESSION_ID}"
   : "\\${SESSION_THREAD_ID:?Set SESSION_THREAD_ID}"
   : "\\${TOOL_USE_ID:?Set TOOL_USE_ID}"

   payload=$(
     jq -n \
       --arg thread_id "$SESSION_THREAD_ID" \
       --arg tool_use_id "$TOOL_USE_ID" \
       '{
         events: [{
           type: "user.tool_confirmation",
           session_thread_id: $thread_id,
           tool_use_id: $tool_use_id,
           result: "allow"
         }]
       }'
   )

   curl -sS --fail-with-body \
     "$ARK_BASE_URL/sessions/$SESSION_ID/events" \
     -H "Authorization: Bearer $ARK_API_KEY" \
     -H "Content-Type: application/json" \
     -d "$payload"
```

4. 对 `agent.custom_tool_use`，执行自定义工具并发送 `user.custom_tool_result`。请求需要携带当前 `session_thread_id` 和 `custom_tool_use_id`。详情请参见 [Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)。

5. 处理全部 `event_ids` 后继续观察。任务可能再次进入 `requires_action`；只有 Session 最终以 `end_turn` 结束，本轮任务才完成。

本页主流程使用云环境，不覆盖自托管 Environment 的 `user.tool_result` 流程。自托管流程请参见 [Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)。

### 中断线程

只停止一个子线程时，运行 `export SESSION_THREAD_ID="<SESSION_THREAD_ID>"`。停止全部活跃线程时，运行 `unset SESSION_THREAD_ID`。

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"

if [[ -n \\${SESSION_THREAD_ID:-} ]]; then
  payload=$(
    jq -n --arg thread_id "$SESSION_THREAD_ID" \
      '{events: [{type: "user.interrupt", session_thread_id: $thread_id}]}'
  )
else
  payload=$(jq -n '{events: [{type: "user.interrupt"}]}')
fi

curl -sS --fail-with-body \
  "$ARK_BASE_URL/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$payload"
```

中断指定子线程后等待该线程进入 `idle`；中断全部线程后等待 Session 进入 `idle`。

## 更新协作配置

新增、移除子智能体或切换版本时，先调用 [查询智能体详情](https://ark.volcengine.com/region:cn-beijing/docs/get-agent-api) 查询协调器最新配置。更新请求需要携带当前 `version`，并整体提交匹配的系统提示词和 `multiagent.agents`。

```Bash
export COORDINATOR_AGENT_VERSION="<LATEST_VERSION>"

COORDINATOR_AGENTS_JSON=$(
  jq -n \
    --arg reviewer_id "$REVIEWER_AGENT_ID" \
    --argjson reviewer_version "$REVIEWER_AGENT_VERSION" \
    --arg tester_id "$TESTER_AGENT_ID" \
    --argjson tester_version "$TESTER_AGENT_VERSION" \
    '[
      {type: "agent", id: $reviewer_id, version: $reviewer_version},
      {type: "agent", id: $tester_id, version: $tester_version}
    ]'
)
export COORDINATOR_AGENTS_JSON
```

通过 API 创建协调器时，创建脚本已导出 `COORDINATOR_SYSTEM`。如果通过控制台创建，请先将控制台使用的完整系统提示词配置到该变量。提示词必须与成员列表保持一致。

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${COORDINATOR_AGENT_ID:?Set COORDINATOR_AGENT_ID}"
: "\\${COORDINATOR_AGENT_VERSION:?Set COORDINATOR_AGENT_VERSION}"
: "\\${COORDINATOR_SYSTEM:?Set COORDINATOR_SYSTEM}"
: "\\${COORDINATOR_AGENTS_JSON:?Set COORDINATOR_AGENTS_JSON}"

update_payload=$(
  jq -n \
    --argjson version "$COORDINATOR_AGENT_VERSION" \
    --arg system "$COORDINATOR_SYSTEM" \
    --argjson agents "$COORDINATOR_AGENTS_JSON" \
    '{
      version: $version,
      system: $system,
      multiagent: {
        type: "coordinator",
        agents: $agents
      }
    }'
)

coordinator=$(
  curl -sS --fail-with-body \
    "$ARK_BASE_URL/agents/$COORDINATOR_AGENT_ID" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$update_payload"
)

export COORDINATOR_AGENT_VERSION
COORDINATOR_AGENT_VERSION=$(jq -er '.version' <<<"$coordinator")

printf 'Coordinator Agent version: %s\n' "$COORDINATOR_AGENT_VERSION"
```

脚本将新版本号写入 `COORDINATOR_AGENT_VERSION`。如果返回 `version: conflict`，重新查询并合并最新配置后再提交。

现有 Session 不会自动切换版本。只有 Session 处于 `idle`、上一轮以 `end_turn` 结束且没有其他升级请求时，才可以升级：

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"
: "\\${COORDINATOR_AGENT_ID:?Set COORDINATOR_AGENT_ID}"
: "\\${COORDINATOR_AGENT_VERSION:?Set COORDINATOR_AGENT_VERSION}"

payload=$(
  jq -n \
    --arg agent_id "$COORDINATOR_AGENT_ID" \
    --argjson version "$COORDINATOR_AGENT_VERSION" \
    '{agent: {type: "agent_with_upgrades", id: $agent_id, version: $version}}'
)

curl -sS --fail-with-body \
  "$ARK_BASE_URL/sessions/$SESSION_ID/upgrades" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$payload"
```

等待 Session 使用目标版本恢复为 `idle`：

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"
: "\\${COORDINATOR_AGENT_ID:?Set COORDINATOR_AGENT_ID}"
: "\\${COORDINATOR_AGENT_VERSION:?Set COORDINATOR_AGENT_VERSION}"
: "\\${SESSION_UPGRADE_MAX_ATTEMPTS:=60}"
: "\\${SESSION_UPGRADE_INTERVAL_SECONDS:=2}"

upgrade_result="timeout"

for ((attempt = 1; attempt <= SESSION_UPGRADE_MAX_ATTEMPTS; attempt++)); do
  session=$(
    curl -sS --fail-with-body \
      "$ARK_BASE_URL/sessions/$SESSION_ID" \
      -H "Authorization: Bearer $ARK_API_KEY"
  ) || {
    upgrade_result="request_failed"
    break
  }

  session_status=$(jq -er '.status' <<<"$session") || {
    upgrade_result="invalid_response"
    break
  }
  session_agent_id=$(jq -r '.agent.id // empty' <<<"$session")
  session_agent_version=$(jq -r '.agent.version // empty' <<<"$session")

  printf 'Attempt %s: status=%s, agent=%s, version=%s\n' \
    "$attempt" \
    "$session_status" \
    "$session_agent_id" \
    "$session_agent_version"

  if [[
    $session_status == "idle" &&
    $session_agent_id == "$COORDINATOR_AGENT_ID" &&
    $session_agent_version == "$COORDINATOR_AGENT_VERSION"
  ]]; then
    upgrade_result="completed"
    break
  fi

  case "$session_status" in
    terminated | failed)
      upgrade_result="$session_status"
      break
      ;;
  esac

  sleep "$SESSION_UPGRADE_INTERVAL_SECONDS"
done

if [[ $upgrade_result != "completed" ]]; then
  printf 'Session upgrade did not complete: %s\n' "$upgrade_result" >&2
  false
fi
```

脚本默认每 2 秒查询一次，最多 60 次。只有状态为 `idle`，且 `agent.id` 和 `agent.version` 与目标一致时才返回成功；超时或进入失败状态时，请检查 Session 或创建新 Session。

## 使用限制

- **子智能体数量**：单个协调器最多配置 20 个子智能体，详情请参见 [创建智能体](https://ark.volcengine.com/region:cn-beijing/docs/create-agent-api)。

- **委派层级**：Multi Agent 只展开一层。`multiagent.agents[].type` 为 `self` 时复用协调器配置创建子线程，但不会继续复用协调器的 `multiagent` 列表。

- **版本快照**：更新协调器不会修改已有 Session 的 Agent 快照。

- **资源消耗**：协调器和子智能体分别产生模型与运行资源用量，详情请参见 [Managed Agents 计费说明](https://ark.volcengine.com/region:cn-beijing/docs/model-pricing#ma_billing)。

## 清理资源

确认产物已转存后，使用精确 ID 删除本页创建的 Session。Session 仍在运行时，先中断全部线程并等待 `idle`。默认保留协调器：

```Bash
export CONFIRM_DELETE="yes"
export DELETE_COORDINATOR="no"
```

仅当协调器不再被任何 Session 使用时，才将 `DELETE_COORDINATOR` 改为 `yes`。执行删除：

**cURL**

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"
: "\\${CONFIRM_DELETE:?Set CONFIRM_DELETE to yes}"
: "\\${DELETE_COORDINATOR:=no}"

if [[ $CONFIRM_DELETE != "yes" ]]; then
  printf 'Set CONFIRM_DELETE=yes to delete the recorded resources.\n' >&2
  return 1 2>/dev/null || exit 1
fi

curl -sS --fail-with-body \
  "$ARK_BASE_URL/sessions/$SESSION_ID" \
  -X DELETE \
  -H "Authorization: Bearer $ARK_API_KEY"

if [[ $DELETE_COORDINATOR == "yes" ]]; then
  : "\\${COORDINATOR_AGENT_ID:?Set COORDINATOR_AGENT_ID}"
  curl -sS --fail-with-body \
    "$ARK_BASE_URL/agents/$COORDINATOR_AGENT_ID" \
    -X DELETE \
    -H "Authorization: Bearer $ARK_API_KEY"
fi
```

命令不会删除子智能体或 Environment。

> **警告**

> 删除 Session 或 Agent 后无法恢复。只使用本页记录的精确 ID，不要按名称模糊匹配或批量删除共享资源。

## 相关文档

- **定义 Agent**：[Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)

- **Session 事件流**：[Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)

- **Tools**：[Tools](https://ark.volcengine.com/region:cn-beijing/docs/tools)

- **管理 Session**：[管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/manage-session)

<a id="doc-2553731"></a>

---

## 定义 Outcome

> 来源：[https://docs.volcengine.com/docs/82379/2553731?lang=zh](https://docs.volcengine.com/docs/82379/2553731?lang=zh)

Outcome（任务验收）是方舟 Managed Agents 提供的进阶能力，也是「Agent 自我进化」能力的重要组成部分。在对话中定义任务目标和验收标准后，系统会自动拉起独立的评分器（Grader）对 Agent 的产出进行评估，让 Agent 检查要求可完成度，并根据反馈继续迭代优化，直到满足验收标准、达到迭代上限或任务失败。

Outcome 将 Session 从「对话」提升为「工作」：你只需要定义「完成」是什么样子，Agent 会自动朝着目标努力，自我评估、不断迭代，直到达成结果。

> **说明**

> 此为邀测能力，如需使用，提交 [测试申请工单](https://console.volcengine.com/auth/login?redirectURI=%2Fworkorder%2Fcreate%3Fstep%3D2%26SubProductID%3DP00001166)。

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/base-url-and-authentication)。

在使用 Outcome 之前，你需要：

1. 已经创建好 Agent 和 Environment，并能正常启动 Session。

2. 明确任务的验收标准。

Outcome 通过 Session 事件定义，无需创建专用 Agent。可以直接使用已有 Agent 的 `agent_id` 创建 Session。

## 使用 Outcome

### 基本流程

按照以下顺序创建任务并观察评估结果：

1. **创建 Session**：通过 [创建会话](https://ark.volcengine.com/region:cn-beijing/docs/create-session-api) 创建绑定 Agent 和 Environment 的 Session。

2. **订阅事件流**：通过 [流式获取会话事件](https://ark.volcengine.com/region:cn-beijing/docs/stream-events-api) 订阅 Session 事件流，并等待服务端返回 `: ready`。

3. **发送 Outcome 事件**：通过 [发送会话事件](https://ark.volcengine.com/region:cn-beijing/docs/send-event-api) 发送 `user.define_outcome` 事件，定义任务和验收标准。

4. **接收评估结果**：通过 SSE 持续接收 `span.outcome_evaluation_start`、`span.outcome_evaluation_ongoing` 和 `span.outcome_evaluation_end` 事件。

Agent 收到 `user.define_outcome` 事件后会立即开始工作，无需额外发送 `user.message`。

### 定义 Outcome

向 Session 的事件入口提交 `user.define_outcome` 事件：

**cURL**

```Bash
curl "https://ark.cn-beijing.volces.com/api/v3/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [
      {
        "type": "user.define_outcome",
        "description": "为 Costco 构建一个 DCF 财务模型，并输出到 /mnt/session/outputs/costco_dcf.xlsx",
        "rubric": {
          "type": "text",
          "content": "# DCF 模型验收标准\n\n## 收入预测\n- 使用了过去 5 个财年的历史收入数据\n- 至少预测了未来 5 年的收入\n- 增长率假设明确说明且合理\n\n## 输出质量\n- 所有数据写入 /mnt/session/outputs/costco_dcf.xlsx\n- 工作表命名清晰\n- 关键假设在单独的「假设」工作表中"
        },
        "max_iterations": 5
      }
    ]
  }'
```

`max_iterations` 用于限制评估轮数，取值范围为 1～20，默认值为 3。`user.define_outcome` 的完整字段结构，请参见 [会话事件结构参考](https://ark.volcengine.com/region:cn-beijing/docs/sessions-events-reference-v2)。

发送成功后，Session 状态会从 `idle` 切换到 `running`，Agent 开始执行任务。

### 观察执行状态

> **先建 SSE，再发 user.define_outcome**

> SSE 只推送连接建立后产生的事件。请先通过 [流式获取会话事件](https://ark.volcengine.com/region:cn-beijing/docs/stream-events-api) 建立连接并等待 `: ready`，再通过 [发送会话事件](https://ark.volcengine.com/region:cn-beijing/docs/send-event-api) 提交 `user.define_outcome` 事件，避免遗漏首轮评估事件。

#### Outcome 事件流转

下图展示一次 Outcome 中，任务执行、结果评估和自动修订之间的关系。客户端主要根据 `span.outcome_evaluation_end.result` 判断是继续等待下一轮评估，还是等待 Session 结束。

![图片](https://arkdocs.tos-cn-beijing.volces.com/flowcharts/managed-agent/outcome-execution-evaluation-revision.svg)

1. 客户端发送 `user.define_outcome` 后，Session 返回 `session.status_running`，Agent 开始执行任务。

2. Agent 生成本轮产出后，Session 返回 `span.outcome_evaluation_start`。评估期间可能多次返回 `span.outcome_evaluation_ongoing`，该事件只表示评估仍在进行。

3. Session 通过 `span.outcome_evaluation_end` 返回本轮评估结果。

4. 如果 `result=needs_revision`，Agent 根据评估反馈修订产出，然后进入下一轮评估；`iteration` 随评估轮次递增。

5. 如果返回其他结果，继续监听至 `session.status_idle`。`max_iterations_reached` 返回后，Agent 还会执行一次收尾。

如何建立和恢复 SSE 连接，请参见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)。各事件的字段、取值和关联 ID，请参见 [会话事件结构参考](https://ark.volcengine.com/region:cn-beijing/docs/sessions-events-reference-v2)。

#### 评估结果类型

`span.outcome_evaluation_end` 事件中的 `result` 字段表示本轮评估的结论：

| result                   | 含义                             | 后续行为                                                                                         |
| ------------------------ | -------------------------------- | ------------------------------------------------------------------------------------------------ |
| `satisfied`              | 产出物满足所有验收标准           | 继续监听至 `session.status_idle`，然后下载并验证交付物                                           |
| `needs_revision`         | 产出物未达标，需要修改           | 无需发送新事件；继续监听 Agent 修订和下一轮评估                                                  |
| `max_iterations_reached` | 已达到最大迭代次数               | 等待 Agent 完成收尾并进入 `idle`；如产物仍未达标，调整目标、验收标准或迭代次数后启动新的 Outcome |
| `failed`                 | 任务无法有效评估或存在根本性问题 | 查看 `explanation`，修正任务目标、验收标准或相关依赖后启动新的 Outcome                           |
| `interrupted`            | 任务被中断                       | 等待 Session 进入 `idle` 后，再决定是否启动新的 Outcome                                          |

#### 评估结束事件示例

```json
{
  "type": "span.outcome_evaluation_end",
  "id": "sevt-20260702070410-xxxxx",
  "outcome_evaluation_start_id": "eval-20260702070405-xxxxx",
  "outcome_id": "outc-20260702070355-xxxxx",
  "result": "satisfied",
  "explanation": "所有 12 条标准均已满足：收入预测使用了 5 年历史数据，WACC 假设已说明，包含敏感性分析表...",
  "iteration": 0,
  "usage": {
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 22240,
    "input_tokens": 2400,
    "output_tokens": 350
  },
  "processed_at": "2026-07-02T07:04:10Z"
}
```

### 中断任务

如果需要在 Outcome 运行过程中停止，可以发送 `user.interrupt` 事件：

**cURL**

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260702070355-xxxxx/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [
      {
        "type": "user.interrupt"
      }
    ]
  }'
```

中断后，当前 Outcome 会停止，`span.outcome_evaluation_end.result` 将标记为 `interrupted`。你可以在中断后启动一个新的 Outcome。

### 多个 Outcome 串联

一次只支持一个 Outcome 运行，但你可以按顺序串联多个 Outcome。在前一个 Outcome 的终止事件（`span.outcome_evaluation_end`）之后，发送新的 `user.define_outcome` 事件即可启动下一个任务。

这对于多阶段工作流非常有用，例如：

1. **阶段一**：需求分析

2. **阶段二**：方案设计

3. **阶段三**：编码实现

4. **阶段四**：测试验证

## 编写验收标准

验收标准（Rubric）是 Outcome 的核心，它定义了任务完成的评判标准。清晰、可验证的验收标准可以帮助 Agent 判断产出是否达标。

### 编写原则

**1. 明确、可量化，而非模糊描述**

✅ 好：`报告包含至少 3 个数据图表，且每个图表都有明确的标题和数据来源`

❌ 不好：`报告看起来不错`

**2. 描述「是什么」，而非「怎么做」**

验收标准用来判断「交付物是否达标」，而不是指导 Agent「怎么完成任务」。让 Agent 自己选择执行方式，你只需要定义终点。

✅ 好：`代码通过所有单元测试，覆盖率不低于 80%`

❌ 不好：`你应该先写测试再写代码，使用 pytest 框架...`

**3. 结构化、分条列项**

使用 Markdown 标题和列表组织，让评分器能够逐条评估。

**4. 从示例反推**

如果你手头有已知合格的产出物示例，可以先分析「是什么让它合格」，然后将这些特质转化为验收标准条目。

### 验收标准模板

以下是一个 DCF 财务模型的 Rubric 示例，供参考：

```markdown
# DCF 模型验收标准

## 收入预测

- 使用了过去 5 个财年的历史收入数据
- 至少预测了未来 5 年的收入
- 增长率假设明确说明且合理

## 成本结构

- 销货成本（COGS）和运营费用分别建模
- 利润率与历史趋势一致，或有合理解释

## 折现率

- WACC 计算包含权益成本和债务成本的假设说明
- Beta、无风险利率、股权风险溢价有来源或说明

## 终值

- 使用了永续增长法或退出倍数法（明确说明使用哪种）
- 终值增长率不超过长期 GDP 增长率

## 输出质量

- 所有数据在一个 .xlsx 文件中，工作表命名清晰
- 关键假设在单独的「假设」工作表中
- 包含 WACC 和终值增长率的敏感性分析
```

## 获取并验证交付物

Agent 将输出文件写入沙箱内的 `/mnt/session/outputs/` 目录。Session 进入 `idle` 后，产物可能暂时无法通过 Files API 查询。下面的示例从已完成的 `$SESSION_ID` 出发，完成以下操作：

1. 分页查询 `purpose=agent` 的文件。

2. 按文件名选择 `costco_dcf.xlsx`；尚未查询到目标文件时，每 2 秒重试一次，最多重试 10 次。

3. 从文件对象中提取 `download_url` 并下载。

4. 校验服务端返回的文件名与目标名称一致，并确认下载文件非空；任一条件不满足时返回非零状态。

需要将交付物长期保存在自己的 TOS Bucket 时，在创建 Environment 时配置 `config.tos`。未配置时，平台将产物存入方舟公共 TOS，请在平台 TTL 到期前下载所需文件。

**cURL**

```Bash
set -euo pipefail

: "\\${ARK_API_KEY:?Set ARK_API_KEY before running this script}"
: "\\${SESSION_ID:?Set SESSION_ID to the completed Session ID}"

OUTPUT_FILENAME="\\${OUTPUT_FILENAME:-costco_dcf.xlsx}"
OUTPUT_PATH="\\${OUTPUT_PATH:-./$OUTPUT_FILENAME}"
MAX_ATTEMPTS="\\${MAX_ATTEMPTS:-10}"
POLL_INTERVAL_SECONDS="\\${POLL_INTERVAL_SECONDS:-2}"

command -v jq >/dev/null 2>&1 || {
  echo "jq is required to parse the Files API response." >&2
  exit 1
}

file=""
for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
  after=""

  while :; do
    url="https://ark.cn-beijing.volces.com/api/v3/files?scope_id=$SESSION_ID&purpose=agent&limit=100&order=desc"
    if [[ -n "$after" ]]; then
      url="$url&after=$after"
    fi

    response=$(curl -fsS "$url" \
      -H "Authorization: Bearer $ARK_API_KEY")

    file=$(jq -cer --arg filename "$OUTPUT_FILENAME" '
      [.data[]?
        | select(.filename == $filename)
        | select(.status == "active")
        | select((.download_url // "") != "")]
      | first // empty
    ' <<<"$response" || true)

    if [[ -n "$file" ]]; then
      break 2
    fi

    if [[ $(jq -r '.has_more // false' <<<"$response") != "true" ]]; then
      break
    fi
    after=$(jq -er '.last_id' <<<"$response")
  done

  if ((attempt < MAX_ATTEMPTS)); then
    sleep "$POLL_INTERVAL_SECONDS"
  fi
done

if [[ -z "$file" ]]; then
  echo "File not ready after $MAX_ATTEMPTS attempts: $OUTPUT_FILENAME" >&2
  exit 1
fi

filename=$(jq -r '.filename' <<<"$file")
download_url=$(jq -er '.download_url' <<<"$file")
if [[ "$filename" != "$OUTPUT_FILENAME" ]]; then
  echo "Unexpected filename: $filename" >&2
  exit 1
fi

curl -fsSL "$download_url" -o "$OUTPUT_PATH"
if [[ ! -s "$OUTPUT_PATH" ]]; then
  echo "Downloaded file is empty: $OUTPUT_PATH" >&2
  exit 1
fi

printf 'Downloaded %s (%s bytes)\n' "$OUTPUT_PATH" "$(wc -c <"$OUTPUT_PATH")"
```

如果脚本重试后仍未找到目标文件，先确认 Session 已进入 `idle`，并检查 Agent 是否将文件写入 `/mnt/session/outputs/costco_dcf.xlsx`。仅重新执行查询脚本即可，不需要重新提交 Outcome。

查询和下载产物的方法，详情请参见 [上传与挂载文件](https://ark.volcengine.com/region:cn-beijing/docs/upload-and-mount-files)。配置产物存储的方法，详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/configure-cloud-environment#configure-output-storage)。

## 最佳实践

**从简单开始**：初次使用 Outcome 时，建议从简单、明确的任务开始，逐步调优验收标准。

**迭代优化验收标准**：根据实际运行结果调整验收标准。如果 Agent 总是在同一环节出错，增加对应的检查项。

**设置合理的迭代次数**：初次使用时可以保持默认值 3。如果多次出现 `max_iterations_reached`，先检查任务目标和验收标准是否明确，再在 1～20 的范围内调整 `max_iterations`。

## 计费说明

Outcome 的费用包含以下部分：

- **Agent 执行费用**：Agent 每轮迭代产生的模型 Token 费用，按 Agent 配置的模型价格计费。

- **评分器费用**：每轮评估产生的模型 Token 费用，评分器使用独立模型，按对应价格计费。

- **沙箱费用**：Session 运行期间的沙箱资源费用。

具体计费规则请参见 [Managed Agents 计费说明](https://ark.volcengine.com/region:cn-beijing/docs/model-pricing#ma_billing)。

## 相关文档

[Card](https://ark.volcengine.com/region:cn-beijing/docs/advisor)

[Advisor](https://ark.volcengine.com/region:cn-beijing/docs/advisor)

Advisor 在 Agent 遇到困难时自动唤起更强的顾问模型提供指导。

[Card](https://ark.volcengine.com/region:cn-beijing/docs/multi-agent)

[编排 Multi Agent](https://ark.volcengine.com/region:cn-beijing/docs/multi-agent)

Multi Agent 支持一个协调器 Agent 将任务委派给多个子智能体并行执行。

[Card](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)

[Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/session-event-stream)

通过事件流实时观测 Agent 的执行过程，包括 Outcome 评估。
