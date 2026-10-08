# 火山方舟 · Managed Agents 文档合集

> 来源：`docs.volcengine.com/docs/82379/{2553713..2553730}?lang=zh`
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

<a id="doc-2553713"></a>

---

## 概述

> 来源：[https://docs.volcengine.com/docs/82379/2553713?lang=zh](https://docs.volcengine.com/docs/82379/2553713?lang=zh)

方舟 Managed Agents 是火山方舟提供的 **预构建、可配置的智能体框架**，运行在方舟托管的基础设施之上，最适合承载 **长时间运行、多轮工具调用、有状态的异步任务**。

基于模型 API 构建一个可用的 Agent，需要自行实现 AgentLoop、工具调用、上下文管理、沙箱调度、断点续跑与权限隔离等通用组件。建设周期通常需要 4 至 8 周，且需随模型迭代持续适配。方舟 Managed Agents 提供 Agent 层的完整基础设施，你只需定义 Agent 行为、发送用户事件并订阅结果流。

> 方舟目前提供两种基于豆包大模型的构建方式：需对 Agent 循环进行细粒度控制时，选用 **模型 API**；需将完整 Agent 交由平台托管、聚焦业务逻辑时，选用 **Managed Agents**。两者的详细差异参见下文 [与模型 API 对比](https://www.volcengine.com/docs/82379/2553713#vs_model_api)。

<columns>
<columnsItem zoneid="dF1gNxNSJG">

<card mode="container" href="/docs/82379/2553714" >

**快速入门**

4 步跑通你的第一个方舟托管 Agent：创建 Agent、创建环境、开启会话、发送消息并接收流式响应。

</card>

</columnsItem>
<columnsItem zoneid="MLOvZNjbVX">

<card mode="container" href="/docs/82379/2553723" >

**启动 Session**

基于 Agent 与 Environment 创建 Session，发送用户事件，让 Agent 开始执行任务。

</card>

</columnsItem>
</columns>

<span id="core_concepts"></span>

# 基本概念

方舟 Managed Agents 围绕以下四个概念构建：

| 概念            | 描述                                                                                                                                                            |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Agent**       | 可版本化、可复用的智能体配置资产，定义了一个智能体的角色、能力、行为边界和运行规则，是所有任务运行的模板和基准。创建一次，通过 `agent_id` 在多个 Session 中复用 |
| **Environment** | Session 运行的环境配置，包含网络、预装依赖包、环境变量与产物存储。通过 `environment_id` 引用                                                                    |
| **Session**     | 在 Environment 中运行的 Agent 实例，用于执行一次具体任务。上下文、文件、状态相互隔离，同一 Agent 可发起多个并行 Session                                         |
| **Events**      | 应用与 Agent 之间交换的原子消息，涵盖用户消息、思考、工具调用、工具结果与状态更新。以只增不删的方式在服务端持久化，支持实时订阅或事后回放                       |

<span id="advantages"></span>

# 核心优势

<span id="vs_model_api"></span>

## 与模型 API 对比

**模型 API** 是访问模型能力的最原子接口——需自行实现循环、解析工具调用、维护上下文，适用于需要 **细粒度控制** 的场景。**方舟 Managed Agents** 则是访问 Agent 能力的封装接口——将 Agent 层原本需自建的通用能力统一收敛至平台内部。

<span id="no_longer_needed"></span>

### 不再需要自行实现的部分

| 之前（模型 API + 自建循环）                                                             | 之后（方舟 Managed Agents）                                                   |
| --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 维护对话历史数组，每一轮将完整历史传回模型                                              | Session 由服务端持久化历史，仅需发送 / 订阅事件                               |
| 手动循环调用：模型返回 → 解析 `tool_use` → 执行工具 → 追加 `tool_result` → 再次调用模型 | 平台内置 AgentLoop 自动驱动整个循环；Agent 结束时发出 `session.status_idle`   |
| 自建沙箱以执行 Agent 生成的代码，机器、容器、扩缩容与清理均需自行管理                   | Session 自带沙箱，代码执行、文件读写、Bash 命令由平台代为承载，任务结束即释放 |
| 自研上下文压缩策略以避免超出上下文窗口                                                  | 上下文自动压缩，Event API 全量留存，不丢失内容                                |
| 自研长程任务的状态保存与恢复机制                                                        | 自动保存沙箱状态，长程任务原生支持断点续跑                                    |
| 自研 IAM、密钥注入与执行环境隔离                                                        | 基于火山 IAM + 零信任凭证隔离，密钥不会进入沙箱进程                           |

<span id="still_controlled"></span>

### 仍由你掌控的部分

- **模型与系统提示词**：字段保持不变，仅位置调整至 Agent 定义中。

- **自定义工具**：仍以 JSON Schema 声明 `input_schema`；执行方式调整为——由用户客户端订阅 `agent.custom_tool_use` 事件后自行执行。

- **上下文注入**：仍可通过系统提示词、Skills、上传文件等方式注入。

- **中断与引导**：可随时向 Session 发送额外用户事件以调整方向，或直接中断本轮执行。

<span id="when_to_use_model_api"></span>

### 何时仍应选择模型 API

若目标 **恰在于自定义 Agent 循环本身**——例如探索新的推理策略、构建自研 Agent 框架、或需对每一步 `tool_use` 进行特殊改写——则模型 API 更为合适。Managed Agents 面向的是「直接获取可运行的 Agent」，而非「构建 Agent 引擎」。

<span id="vs_open_source_agent"></span>

## 与开源 Agent 对比

主流开源 Agent Runtime（LangChain、OpenCode 等）解决了「能够运行」的问题，但要实现「稳定、高效、具备业务价值」的运行仍需大量投入。方舟并非「在开源框架之上叠加一层豆包适配」，而是 **自底向上自研 AgentLoop 调度内核，并与豆包大模型的原生能力进行深度双向优化**——相当于为模型配备一套量身定制的操作系统，而非在通用硬件上粗放运行。

| 对比维度         | 方舟 Managed Agents（原生 AgentLoop）        | 开源 Agent Runtime                           |
| ---------------- | -------------------------------------------- | -------------------------------------------- |
| **模型适配方式** | 与豆包全系列原生双向优化                     | 通用 prompt 模板适配所有模型                 |
| **推理完成率**   | 思维链原生拆解、断点续跑、上下文自动压缩     | 长任务易偏离预期、中断后无法续跑、上下文丢失 |
| **工具调用**     | 协议级对齐，自动校验 / 重试 / 并行           | 通用 JSON 解析                               |
| **长程任务**     | 原生支持长程任务运行，自动生成快照、按需拉起 | 固定模板适配所有场景，需自建沙箱调度         |
| **接入成本**     | 开箱即用，零适配成本                         | 需 4–8 周自研适配，踩坑周期较长              |

<span id="use_cases"></span>

# 适用场景

方舟 Managed Agents 最适合具备以下特征的工作负载：

- **长时间运行**：单次任务需运行数分钟至数小时，涉及多次工具调用与迭代思考。

- **有状态**：跨多轮交互持久保存文件系统、对话历史与执行状态，支持断点续跑。

- **异步执行**：任务发起后由 Agent 自主推进，客户端仅需订阅事件流即可获取结果。

- **最少的基础设施**：无需自建 Agent 循环、沙箱与工具执行层。

- **弹性调用**：调用量波峰波谷显著，期望按用量计费而非常驻资源。

在上述特征之上，以下为已验证 PMF 的典型业务场景：

| 场景                   | 重复度 | 风险 | 说明                                       |
| ---------------------- | ------ | ---- | ------------------------------------------ |
| **IT 工单处理**        | 高     | 低   | 结构化强、模板化程度高，Agent 处理准确率高 |
| **报表自动生成**       | 高     | 低   | 数据抓取与汇总逻辑固定，易于 Agent 化      |
| **供应商邮件处理**     | 中     | 低   | 分类与回复流程明确，适合规则化自动化       |
| **代码 Review 与文档** | 高     | 低   | 研发团队收益直观，ROI 显著                 |
| **销售线索初筛**       | 中     | 低   | CRM 录入自动化，释放销售人力               |

正在使用开源 Agent 自建业务、或通过脚本拼接 Agent 能力的团队，均可考虑迁移至方舟 Managed Agents 进行试点验证。

<span id="workflow"></span>

# 工作流程

方舟 Managed Agents 从创建到接入业务，通常经历以下 5 个步骤：

1. **创建 Agent**：在控制台或通过 API 定义模型、系统提示词与 Skills / Tools / MCPs，获得稳定的 `agent_id`，可在多个 Session 中通过 ID 复用。

2. **创建 Environment**：定义沙箱运行环境（网络、预装包、环境变量和产物存储），获得稳定的 `environment_id`。

3. **启动 Session**：基于 `agent_id` 与 `environment_id` 创建一次会话（Session）。

4. **发送事件并流式接收响应**：将用户消息作为事件发送至 Session，Agent 自主执行工具并通过 SSE（server\-sent events）流式返回结果，事件历史由服务端持久化保存，支持完整获取。

5. **引导或中断**：Agent 执行过程中，可随时发送额外用户事件以引导其调整方向，或直接中断本轮执行。

上手方式提供以下两种路径：

- 如需以 **可视化方式** 快速完成原型验证，参见 [快速入门（控制台）](https://www.volcengine.com/docs/82379/2553715)。

- 如需直接以 **代码 / API** 完成端到端接入，参见 [快速入门（代码）](https://www.volcengine.com/docs/82379/2553714)。

<span id="pricing"></span>

# 计费说明

Managed Agents 按照 Agent 运行过程中实际消耗的 Tokens、Agent 运行时时长和工具调用次数三项累加计费。详情参见 [Managed Agents 计费](https://www.volcengine.com/docs/82379/1544106#ma_billing)。

<a id="doc-2553714"></a>

---

## 快速入门（代码）

> 来源：[https://docs.volcengine.com/docs/82379/2553714?lang=zh](https://docs.volcengine.com/docs/82379/2553714?lang=zh)

4 步跑通你的第一个方舟托管 Agent：创建 Agent、创建环境、开启会话、发送消息并接收流式响应。

<span id="prerequisites"></span>

# 准备工作

<span id="get_api_key"></span>

## 1. 获取并配置 API Key

1. 获取 API Key：访问 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)，创建你的 API Key。

2. 配置环境变量：在终端中运行下面命令（替换 `your_api_key_here` 为你的方舟 API Key），配置 API Key 到环境变量。

配置持久化环境变量方法参见 [环境变量配置指南](https://ark.volcengine.com/region:cn-beijing/docs/ark/environment-variable-configuration-guide)。

<Tabs>
<Tab zoneid="lUGILl8vfx" title="macOS">
<TabTitle>macOS</TabTitle>

```Bash
export ARK_API_KEY="your_api_key_here"
```

</Tab>
<Tab zoneid="uUwKggQuzj" title="Linux">
<TabTitle>Linux</TabTitle>

```Bash
export ARK_API_KEY="your_api_key_here"
```

</Tab>
<Tab zoneid="nyR36GiDOf" title="Windows_CMD">
<TabTitle>Windows_CMD</TabTitle>

```Bash
setx ARK_API_KEY "your_api_key_here"
```

</Tab>
<Tab zoneid="uwHrSGWnA9" title="Windows_PowerShell">
<TabTitle>Windows_PowerShell</TabTitle>

```PowerShell
$env:ARK_API_KEY = "your_api_key_here"
```

</Tab>
</Tabs>

<span id="enable_managed_agent"></span>

## 2. 开通 Managed Agents 服务

访问 [开通管理页面](https://ark.volcengine.com/region:cn-beijing/openManagement)，切换到 **Managed Agents** 页签开通服务。

<span id="enable_model_service"></span>

## 3. 开通模型服务

访问 [开通管理页面](https://ark.volcengine.com/region:cn-beijing/openManagement) 开通模型服务。

<span id="create_agent"></span>

# 1. 创建 Agent

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
    {"type": "agent_toolset_20260701"}
  ]
}
EOF
)

AGENT_ID=$(jq -er '.id' <<<"$agent")

echo "Agent ID: $AGENT_ID"
```

<span id="create_environment"></span>

# 2. 创建环境

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

<span id="create_session"></span>

# 3. 开启会话

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

<span id="send_message_stream"></span>

# 4. 发送消息并流式接收响应

获取 `session_id` 后，先通过 `SessionEvents_streamEvents` 建立 SSE 连接并等待服务端返回 `: ready`，再通过 `SessionEvents_send` 发送 `user.message` 事件。客户端通过已建立的连接接收 Agent 状态、工具调用和响应内容。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">SSE 只推送连接建立后产生的事件，不回放历史事件。如果先发送消息再建立连接，客户端会漏掉连接建立前已产生的事件。</div>

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

<span id="full_script"></span>

# 完整脚本

将上面 4 步合并为一个可直接运行的脚本，开头加 `set -euo pipefail`，任一步失败立即终止。

**用法：**

1. 将下面代码保存为 `quickstart.sh`。

2. 确保已按「准备工作」配置好 `ARK_API_KEY` 环境变量，并已安装 `curl` 与 `jq`。

3. 执行：

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
    {"type": "agent_toolset_20260701"}
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

<span id=".c2RrLeWujOaVtOekuuS-iw=="></span>

## SDK 完整示例

<Tabs>
<Tab zoneid="YnugBF04CM" title="Python">
<TabTitle>Python</TabTitle>

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
    tools=[{"type": "agent_toolset_20260701"}],
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

</Tab>
<Tab zoneid="zv95YwUW9e" title="Go">
<TabTitle>Go</TabTitle>

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
        Tools:  []agentmodel.ToolItem{{Type: "agent_toolset_20260701"}},
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

</Tab>
<Tab zoneid="oUndr01Wp1" title="Java">
<TabTitle>Java</TabTitle>

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
                        .tools(Arrays.asList(ToolItem.builder().type("agent_toolset_20260701").build()))
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

</Tab>
</Tabs>

<span id="runtime_notes"></span>

# 运行说明

当您发送用户事件时，方舟托管 Agent 会：

1. **配置沙箱**：您的环境配置决定了沙箱的构建方式。

2. **运行Agent循环**：方舟根据您的消息确定要使用哪些工具。

3. **执行工具**：启动沙箱，在沙箱内运行文件写入、bash 命令和其他工具调用。

4. **流式传输事件**：您会在Agent工作时收到实时更新。

5. **进入空闲状态**：当Agent没有更多任务要执行时，会发出 `session.status_idle` 事件。

<span id=".6K6-6K6h5bu66K6u"></span>

# 设计建议

配置 Agent 和 Session 时，按照配置的生命周期区分长期能力与单次任务：

- **长期能力写入 Agent 定义**：通过 `Agents_create` 或 `Agents_update` 配置系统提示词、Skills、Tools 和 MCP Server，供多个 Session 复用。

- **单次任务通过 Session 事件传入**：通过 `SessionEvents_send` 发送 `user.message` 事件，不要将本次任务写入 Agent 的 `system` 字段。

将单次任务写入 `system` 会降低 Agent 定义的复用性；将长期规则放入 Session 事件则需要重复传输，也容易造成不同 Session 的配置不一致。

<span id="next_steps"></span>

# 后续步骤

<columns>
<columnsItem zoneid="Yc3exU1uIN">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent" >

**定义 Agent**

定义 Agent 的模型、系统提示词、工具集与运行行为。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment" >

**配置环境**

配置 Agent 运行所在的云端沙箱环境，包含网络、预装依赖包与环境变量。

</card>

</columnsItem>
<columnsItem zoneid="CQYA3NWOAn">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tools" >

**Agent tools**

配置 Agent 在 Session 中可主动调用的工具集合。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream" >

**Session 事件流**

通过 SSE 事件流实时接收 Agent 的消息、工具调用与状态更新。

</card>

</columnsItem>
</columns>

<a id="doc-2553715"></a>

---

## 快速入门（控制台）

> 来源：[https://docs.volcengine.com/docs/82379/2553715?lang=zh](https://docs.volcengine.com/docs/82379/2553715?lang=zh)

方舟 Managed Agents 控制台提供可视化界面，让你无需编写 API 代码即可创建、配置并测试 Agent。你可以在控制台里交互式地打磨 Agent 配置，验证行为符合预期后，再拿到对应的 ID 接入到自己的业务代码中。

> 首次访问需登录 [火山方舟控制台](https://console.volcengine.com/ark/region:cn-beijing/managed-agents/agents?projectName=default)，进入 **Managed Agents** 模块。

<span id="prerequisites"></span>

# 准备工作

<span id="get_api_key"></span>

## 1. 获取 API Key

访问 [API Key 管理](https://console.volcengine.com/ark/region:cn-beijing/apiKey)，单击 **创建 API Key**，作为后续接入业务代码的访问密钥。

<span id="enable_managed_agent"></span>

## 2. 开通 Managed Agents 服务

访问 [开通管理页面](https://console.volcengine.com/ark/region:cn-beijing/openManagement)，切换到 **Managed Agents** 页签开通服务。

<span id="enable_model_service"></span>

## 3. 开通模型服务

访问 [开通管理页面](https://console.volcengine.com/ark/region:cn-beijing/openManagement) 开通模型服务。

<span id="build_agent"></span>

# 构建 Agent

<span id="create_agent"></span>

## 1. 创建 Agent

Agent 是可版本化、可复用的智能体配置资产，定义角色、能力、行为边界和运行规则，是所有任务运行的模板和基准。

访问 [火山方舟控制台](https://console.volcengine.com/ark/region:cn-beijing/managed-agents/agents?projectName=default)，进入 **Managed Agents** 模块。在 **Agents** 页面单击 **创建 Agent**，跟随界面引导填写：

- **模型和系统提示词**：选择一个 **模型**，并编写 **系统提示词**，为 Agent 设定角色、做事风格与工作原则。

- **Skills**：从 **火山 SkillHub** 中挂载预置 **Skill**，或本地上传自定义 **Skill**，扩展 Agent 的领域能力。

- **Tools**：勾选内置的 **Agent Toolset**（如 `bash`、`edit`、`read`、`write` 等基础工具），让 Agent 能够读写文件、执行命令。

- **MCPs**：以 URL 形式接入远程 **MCP Server**，把外部系统能力接入 Agent。

- **Multi Agents**：将多个 Agent 组合成协同工作流，一个 Agent 可以调用另一个 Agent 完成子任务。

保存后，控制台会为该 Agent 生成一个稳定的 `agent_id`。每次修改配置都会自动生成新版本，支持版本对比、回滚和灰度发布。

<span id="create_environment"></span>

## 2. 创建 Environment

定义 Agent 的运行环境模板，预置依赖、环境变量和产物存储等。创建 Session 时绑定 Environment，确保代码在一致环境中执行。

在 [Environments 页面](https://console.volcengine.com/ark/region:cn-beijing/managed-agents/environments?projectName=default) 单击 **创建 Environment**，跟随界面引导填写：

- **类型**：当前仅支持 **云托管**——沙箱由方舟按需拉起、自动休眠。

- **预装包**：按需添加 `pip`、`apt` 依赖（如 `numpy`、`pandas`），沙箱首次启动时安装。

- **环境变量**：以键值对形式注入到沙箱进程，沙箱启动时自动加载，供 Agent 执行的代码和命令直接读取。常用于配置业务侧的非敏感参数，例如设置时区 `TZ=Asia/Shanghai`。

- **产物存储**：可选。需要长期保留 Agent 写入 `/mnt/session/outputs/` 的最终产物时，选择已授权的自有 TOS 目录；不选择时使用方舟公共 TOS，你需要在平台 TTL 到期前下载所需文件。

保存后得到一个稳定的 `environment_id`。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">你的 TOS Bucket 必须与 Managed Agents 服务部署在同一地域。首次选择 TOS 目录时，如果控制台提示未授权，先按页面引导完成项目授权。</div>

<span id="debug_agent"></span>

# 调试 Agent

在控制台里以交互方式测试 Agent。每次调试创建一个 Session，展示事件流、工具调用、输入输出完整记录，可随时回放。

Session 是一次独立的 Agent 任务运行会话，包含从发起、执行到结束的完整生命周期：

1. 在 [Sessions 页面](https://console.volcengine.com/ark/region:cn-beijing/managed-agents/sessions?projectName=default) 单击 **创建 Session**，选择要绑定的 **Agent** 和 **Environment**。Session 默认继承该 Environment 的产物存储配置。

2. 在 **Session 详情** 页面调试会话：

   - **发起**：在 **会话面板** 输入自然语言指令，或上传附件（表格、文档、代码、图片等）作为任务输入。

   - **观察**：**事件时间轴（Trace）** 实时呈现 Agent 的思考过程、工具调用、代码执行、错误重试等全链路节点。

   - **验证**：Session 结束后可查看 **最终交付产物**、**总耗时**、**Token 消耗**、**工具调用次数** 等运行统计，判断 Agent 行为是否符合预期。

反复调整 **系统提示词**、**Tools**、**Skills**，直到 Session 试跑结果符合预期。

<span id="prototype_to_code"></span>

# 从原型到代码

Agent 配置验证通过后，把它接入到业务代码中只需两步。

<span id="copy_ids_from_console"></span>

## 1. 从控制台复制 ID

- 从 **Agent 详情页** 复制 `agent_id`。

- 从 **Environment 详情页** 复制 `environment_id`。

- 前往 [API Key 管理](https://console.volcengine.com/ark/region:cn-beijing/apiKey) 单击 **创建 API Key**，作为访问密钥。

<span id="create_session_in_code"></span>

## 2. 在代码中引用它们创建 Session

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

拿到返回的 `session_id` 后，即可通过 Events API 向 Session 发送消息，并通过 SSE 流式接收 Agent 的响应事件。详情请参见 [Session 事件流](https://www.volcengine.com/docs/82379/2553725)。

<a id="doc-2553716"></a>

---

## Agent

> 来源：[https://docs.volcengine.com/docs/82379/2553716?lang=zh](https://docs.volcengine.com/docs/82379/2553716?lang=zh)

方舟 Managed Agents 是火山方舟平台推出的全托管 Agent 服务，给您带来开箱即用的 Agent 体验。Agent 是方舟 Managed Agents 的基础元件，是一套包含基本信息、System Prompt、扩展能力（Skills、Tools、MCP 等）的配置模板，可以被任意 Session 复用，并支持版本化管理。本文介绍如何定义一个 Agent。

<span id=".YWdlbnQt5a6a5LmJ5a2X5q61"></span>

## Agent 定义字段

Agent 的完整字段定义（包括名称、模型、System Prompt、Skills、Tools、MCP、多智能体协作、元数据等），请参见[创建智能体](https://ark.volcengine.com/region:cn-beijing/docs/ark/create-agent-api)。

<span id=".5YeG5aSH5bel5L2c"></span>

## 准备工作

1. 获取 API Key。API Key 是调用方舟平台模型和服务的鉴权信息。

   访问 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey) 页面，创建你的 API Key。

2. 安装 `curl` 和 `jq`，并配置环境变量。API Key 是敏感信息，一旦意外泄露，可能会造成资金损失或安全风险，因此不要在代码中明文写入 API Key，应将其配置到环境变量中。

   将以下命令中的 `your_api_key_here` 替换为你的 API Key，并在终端中运行命令，即可将 API Key 配置到环境变量中。详见[环境变量配置指南](https://ark.volcengine.com/region:cn-beijing/docs/ark/environment-variable-configuration-guide)。

   <Tabs>
   <Tab zoneid="ZGeUT3SOIW" title="macOS">
   <TabTitle>macOS</TabTitle>

   ```Bash
   export ARK_API_KEY="your_api_key_here"
   ```

   </Tab>
   <Tab zoneid="MeKGmZDoxq" title="Linux">
   <TabTitle>Linux</TabTitle>

   ```Bash
   export ARK_API_KEY="your_api_key_here"
   ```

   </Tab>
   <Tab zoneid="LLpN1ZISaL" title="Windows_CMD">
   <TabTitle>Windows_CMD</TabTitle>

   ```CMD
   setx ARK_API_KEY "your_api_key_here"
   ```

   </Tab>
   <Tab zoneid="WK19Z4W4tY" title="Windows_PowerShell">
   <TabTitle>Windows_PowerShell</TabTitle>

   ```PowerShell
   $env:ARK_API_KEY = "your_api_key_here"
   ```

   </Tab>
   </Tabs>

3. 开通 Managed Agents 服务。

   访问 [开通管理页面](https://ark.volcengine.com/region:cn-beijing/openManagement)，切换到 **Managed Agents** 页签开通服务。

4. 开通模型服务。

   访问 [开通管理](https://ark.volcengine.com/region:cn-beijing/openManagement) 页面，开通模型服务。

<span id=".5Yib5bu6LWFnZW50"></span>

## 创建 Agent

创建 Agent 后，接口会返回一个稳定的 Agent ID 和初始版本号 `1`。后续创建 Session 时，可以直接引用这个 Agent ID。

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
        "type": "agent_toolset_20260701",
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
      "type": "agent_toolset_20260701",
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

<span id=".5pu05pawLWFnZW50LeS4jueJiOacrA=="></span>

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
      "type": "agent_toolset_20260701",
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

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 取值          | `mcp_servers`、`tools` 和 `skills` 的处理方式 | 适用场景                              |
| ------------- | --------------------------------------------- | ------------------------------------- |
| 省略或 `null` | 保留对应字段的当前配置。                      | 只更新其他字段。                      |
| 空数组 `[]`   | 清空对应字段的已保存配置。                    | 明确移除全部 MCP Server、工具或技能。 |
| 非空数组      | 使用请求中的数组整体替换当前配置。            | 先读取并合并所有需要保留的条目。      |

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">如需限制主 Agent 的内置工具范围，请显式配置内置工具集并关闭默认范围，不要通过清空 <code>tools</code> 实现。详情请参见 <a href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tools#tool-defaults-and-boundaries">核对默认工具范围</a>。</div>

<span id=".6K6-6K6h5bu66K6u"></span>

## 设计建议

- 把稳定能力放进 Agent，把一次性任务放进 Session 事件。

- `system` 只定义角色、约束和长期规则，不要把当前任务直接写进 `system`。

- 需要外部系统能力时，优先使用 MCP。

- 需要复用领域知识或执行规范时，优先挂载 Skills，而不是把大段操作手册直接塞进 `system`。

<span id=".55u45YWz5paH5qGj"></span>

## 相关文档

<columns>
<columnsItem zoneid="Artsyf5RmA">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/skills" >

**Skills**

Skills 用于给 Agent 补充领域知识、操作流程和最佳实践。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/mcp" >

**MCP**

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。

</card>

</columnsItem>
<columnsItem zoneid="M0Vn6YrnUZ">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tools" >

**Tools**

Tools 决定 Agent 在 Session 中能主动调用哪些执行能力。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy" >

**工具权限策略**

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待确认。

</card>

</columnsItem>
</columns>

<a id="doc-2553717"></a>

---

## Skills

> 来源：[https://docs.volcengine.com/docs/82379/2553717?lang=zh](https://docs.volcengine.com/docs/82379/2553717?lang=zh)

Skills 是可复用的能力包，用于给 Agent 补充领域知识、操作流程和最佳实践。与直接把大量规则写进 `system` 不同，Skills 更适合承载可复用、可维护的专业能力。

<span id=".6I635Y-WLXNraWxscw=="></span>

# 获取 Skills

方舟 Managed Agents 支持两类 Skills 来源：

- 预置 Skills：从 SkillHub 选择。

- 自定义 Skills：通过 `CreateSkill` 接口上传本地 Skills。

<span id=".5pa55byPLTHvvJrku44tc2tpbGxodWIt6YCJ5oup6aKE572uLXNraWxscw=="></span>

## 方式 1：从 SkillHub 选择预置 Skills

如果你使用的是平台预置 Skills，可以直接在 Agent 的 `skills` 配置中引用对应 Skill。预置 Skills 适合通用场景，例如文档处理、结构化内容生成或固定工作流编排。

由于预置 Skills 由平台统一维护，你不需要自己上传文件，也不需要关心版本包结构。选择完成后，把对应 Skill ID 写入 Agent 的 `skills` 数组即可。

如果你需要查询预置 Skill 的 ID，请前往 [SkillHub 页面](https://console.volcengine.com/skillhub) 查看。

<span id=".5pa55byPLTLvvJrkuIrkvKDoh6rlrprkuYktc2tpbGxz"></span>

## 方式 2：上传自定义 Skills

如果预置 Skills 无法覆盖你的场景，可以先通过 `CreateSkill` 接口上传本地 Skills，再在 Agent 中引用返回的 `skill_id`。

当前支持两种上传方式：

- 按文件上传：逐个上传同一个 Skill 目录中的文件，并保留目录结构。

- ZIP 上传：将一个完整 Skill 目录打包成 ZIP 文件后上传。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">每次请求只能创建一个 Skill，且技能文件中必须包含且仅包含一个 <code>SKILL.md</code>。</div>

<span id=".5LiK5Lyg6ZmQ5Yi25LiO55uu5b2V5bu66K6u"></span>

### 上传限制与目录建议

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

<span id=".5oyJ5paH5Lu25LiK5Lyg"></span>

### 按文件上传

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

<span id=".emlwLeS4iuS8oA=="></span>

### ZIP 上传

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

<span id=".5ZyoLWFnZW50LeS4iuW8leeUqC1za2lsbHM="></span>

# 在 Agent 上引用 Skills

获取 Skills 后，你需要在 `CreateAgent` 或 `UpdateAgent` 的 `skills` 字段中引用 Skill。

- 预置 Skills：从 SkillHub 选择后，把对应 Skill ID 写入 `skills`。

- 自定义 Skills：先调用 `CreateSkill` 上传，拿到 `skill_id` 后再写入 `skills`。如果需要指定自定义 Skill 的版本，请显式传入 `version`，否则通常使用默认版本。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">单个 Agent 最多可配置 50 个 Skills。</div>

<span id=".c2tpbGxzLeWtl-autee7k-aehA=="></span>

## `skills` 字段结构

在 `CreateAgent` 或 `UpdateAgent` 请求中，每个 Skill 条目都通过 `skills` 数组声明。关于字段结构的详细信息，请参见[创建智能体](https://ark.volcengine.com/region:cn-beijing/docs/ark/create-agent-api)。

<span id=".56S65L6L5Luj56CB"></span>

## 示例代码

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

<span id=".5L2_55So5bu66K6u"></span>

# 使用建议

- 对稳定、通用的能力，优先选择 SkillHub 中的预置 Skills。

- 对团队私有流程、内部规范或自定义操作手册，使用自定义 Skills。

- 不要把一次性任务描述写进 Skill；Skill 负责定义能力，具体任务应通过 Session 事件传入。

- 上传前建议在本地先确认 `SKILL.md`、脚本和依赖文件路径关系，避免上传后引用失败。

<span id=".55u45YWz5paH5qGj"></span>

# 相关文档

<columns>
<columnsItem zoneid="h9rlFZgob8">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent" >

**Agent**

Agent 是包含基本信息、System Prompt、扩展能力的配置模板。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/mcp" >

**MCP**

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。

</card>

</columnsItem>
<columnsItem zoneid="WGVMUN3KaY">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tools" >

**Tools**

Tools 决定 Agent 在 Session 中能主动调用哪些执行能力。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy" >

**工具权限策略**

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待确认。

</card>

</columnsItem>
</columns>

<a id="doc-2553718"></a>

---

## MCP

> 来源：[https://docs.volcengine.com/docs/82379/2553718?lang=zh](https://docs.volcengine.com/docs/82379/2553718?lang=zh)

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。通过 MCP，Agent 可以访问外部服务暴露出来的标准化工具，例如代码托管、项目协作、知识库或内部业务系统。

在方舟 Managed Agents 中，MCP 配置分成两层：

- Agent 定义层：声明要连接哪些 MCP Server。

- Session 运行层：通过 Vaults 注入对应凭据，完成鉴权。

这种拆分让 Agent 定义保持可复用，同时避免把终端用户密钥固定地写在 Agent 资源里。

<span id=".5ZyoLWFnZW50LeS4iuWjsOaYji1tY3Atc2VydmVy"></span>

# 在 Agent 上声明 MCP Server

创建 Agent 时，通过 `mcp_servers` 数组声明 MCP Server。每个 Server 需要三个字段：

<span aceTableMode="list" aceTableWidth="1,3"></span>

| 字段   | 说明                                                                                                                                                                                                                                                                               |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `type` | MCP Server 的声明方式。当前统一使用 URL 方式声明，值为 `url`。                                                                                                                                                                                                                     |
| `name` | MCP Server 名称，需在当前 Agent 内唯一。服务端会将符合 `[a-zA-Z0-9_-]{1,64}` 格式的名称转换为小写；名称不合规时，会尝试替换空格或根据 URL 生成名称；名称冲突时，会自动追加数字后缀。为保证后续引用稳定，建议直接填写归一化后仍唯一的 `[a-z0-9_-]{1,64}` 小写名称，不依赖自动生成。 |
| `url`  | MCP Server 的访问地址。                                                                                                                                                                                                                                                            |

每个 `mcp_toolset` 都必须通过 `mcp_server_name` 引用一个已声明的 MCP Server。你也可以只声明 MCP Server，暂不添加对应的 `mcp_toolset`；此时该 Server 的工具不会启用。

下面的示例把一个 GitHub MCP Server 挂到 Agent：

<Tabs>
<Tab zoneid="Q9fBu7h65x" title="Curl">
<TabTitle>Curl</TabTitle>

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
        "type": "agent_toolset_20260701"
      },
      {
        "type": "mcp_toolset",
        "mcp_server_name": "github"
      }
    ]
  }'
```

</Tab>
</Tabs>

<span id=".5o6n5Yi25ZOq5LqbLW1jcC3lt6Xlhbflj6_nlKg="></span>

# 控制哪些 MCP 工具可用

`mcp_toolset` 的配置方式与内置工具集一致，也支持 `default_config` 和 `configs`。两类工具在显式提供 `default_config` 但省略 `permission_policy` 时采用不同的运行时默认值：内置工具使用 `always_allow`，MCP 工具使用 `always_ask`。为避免执行行为随工具类型变化，示例应同时显式配置 `enabled` 和 `permission_policy`。

如果某个 MCP Server 暴露的工具很多，建议先整体关闭，再按白名单开启：

<Tabs>
<Tab zoneid="OiuldGrClM" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

如果只需关闭少量 MCP 工具，将 `default_config.enabled` 设为 `true`，显式配置 `default_config.permission_policy`，再在 `configs` 中把对应工具设为 `enabled: false`。完整默认矩阵请参见 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy)。

<span id=".5ZyoLXNlc3Npb24t5Lit5rOo5YWlLW1jcC3lh63mja4="></span>

# 在 Session 中注入 MCP 凭据

MCP Server 的认证不在 Agent 定义阶段传入，而是在创建 Session 时通过 `vault_ids` 引用 Vaults 中的凭据。这样你可以让同一个 Agent 在不同 Session 中代表不同终端用户访问外部系统。

最简示例如下：

<Tabs>
<Tab zoneid="mXynIWFpap" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

Vaults 的创建方法、`mcp_oauth` / `static_bearer` 等凭据类型，以及多 Vaults 匹配规则，详情请参见 [使用 Vaults 认证](https://ark.volcengine.com/region:cn-beijing/docs/ark/use-vaults-authentication)。

<span id=".57qm5p2f5LiO5bu66K6u"></span>

# 约束与建议

- 同一个 Agent 中，`mcp_servers.name` 必须唯一。

- 每个 `mcp_toolset` 必须引用一个已声明的 MCP Server；只声明 MCP Server 不会启用其工具。

- 不要把终端用户 token 直接写进 Agent 定义；应通过 Vaults 在 Session 级注入。

- 对高风险 MCP 工具，建议配合 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy) 使用 `always_ask`。

<span id=".55u45YWz5paH5qGj"></span>

# 相关文档

<columns>
<columnsItem zoneid="QkHYmjkmle">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent" >

**Agent**

Agent 是包含基本信息、System Prompt、扩展能力的配置模板。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/skills" >

**Skills**

Skills 用于给 Agent 补充领域知识、操作流程和最佳实践。

</card>

</columnsItem>
<columnsItem zoneid="MDjHrb24jz">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tools" >

**Tools**

Tools 决定 Agent 在 Session 中能主动调用哪些执行能力。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy" >

**工具权限策略**

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待确认。

</card>

</columnsItem>
</columns>

<a id="doc-2553719"></a>

---

## Tools

> 来源：[https://docs.volcengine.com/docs/82379/2553719?lang=zh](https://docs.volcengine.com/docs/82379/2553719?lang=zh)

模型可以理解任务并生成内容，但无法仅靠推理直接操作文件、执行命令或访问业务系统。为 Agent 配置 Tools 后，Agent 可以调用相应的执行能力，并根据工具结果继续处理任务，完成从理解需求到执行操作的闭环。

<span id=".6YCJ5oup5bel5YW3"></span>

## 选择工具

根据任务需要访问的资源和工具执行方，选择对应的工具类型。

- 任务需要在云环境中操作文件、执行命令或获取网页信息时，使用内置工具。

- 任务需要访问已经接入 MCP Server 的外部系统时，使用 MCP 工具。

- 任务只需调用少量自有业务操作，且由业务应用执行时，使用自定义工具。

<span id=".5L2_55So5YaF572u5bel5YW3"></span>

### 使用内置工具

当任务需要在方舟提供的云环境中处理文件、运行命令或获取网页信息时，使用内置工具集 `agent_toolset_20260701`。典型场景包括整理上传的文件、生成并运行代码，以及检索和汇总公开信息。

把 `agent_toolset_20260701` 加到 Agent 后，以下工具默认全部启用。

<span aceTableMode="list" aceTableWidth="1,1,3"></span>

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

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">Web Search 按实际调用次数计费。</div>

<span id=".5o6l5YWlLW1jcC3lt6Xlhbc="></span>

### 接入 MCP 工具

当任务需要访问已经通过 MCP Server 接入的外部系统，或需要复用一组标准化外部工具时，使用 MCP 工具集 `mcp_toolset`。典型场景包括查询项目数据、读取业务信息和触发外部工作流。

MCP 工具集通过 `type: "mcp_toolset"` 将一个 MCP Server 暴露的工具提供给 Agent。`mcp_server_name` 必须与 `mcp_servers[].name` 一致；只声明 MCP Server 而不添加对应的 `mcp_toolset` 时，Agent 无法调用该 Server 的工具。

可以通过 `default_config` 设置该 Server 下工具的默认状态，再通过 `configs` 按工具名称覆盖。完整配置和鉴权方式请参见 [MCP](https://ark.volcengine.com/region:cn-beijing/docs/ark/mcp)。

显式提供 `default_config` 但省略 `permission_policy` 时，MCP 工具运行时使用 `always_ask`，内置工具使用 `always_allow`。为避免 MCP 调用意外暂停，建议显式配置权限策略。

<span id=".5o6l5YWl6Ieq5a6a5LmJ5bel5YW3"></span>

### 接入自定义工具

当任务只需要调用少量自有业务操作，且无需为这些操作单独部署 MCP Server 时，使用自定义工具 `custom`。典型场景包括查询订单、提交工单和触发审批。业务应用负责执行实际操作，再把结果回传给 Agent。

自定义工具通过 `type: "custom"` 声明。每个工具需要配置名称、用途描述和输入参数 Schema。以下示例使用 `curl` 创建 Agent，并使用 `jq` 保存返回的 Agent ID。示例同时显式关闭内置工具集，避免主 Agent 回退启用全部内置工具：

<Tabs>
<Tab zoneid="n4rpplv7Dl" title="Curl">
<TabTitle>Curl</TabTitle>

```Bash
AGENT_RESPONSE=$(curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/agents" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CustomToolDemo",
    "model": {
      "id": "doubao-seed-2-1-pro-260628"
    },
    "system": "你是一个可以调用业务工具的助手。需要外部业务能力时，调用已声明的自定义工具。不要编造工具结果。",
    "tools": [
      {
        "type": "agent_toolset_20260701",
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

</Tab>
</Tabs>

配置后，Agent 需要查询订单时会发送 `agent.custom_tool_use`。业务应用读取工具名称和输入参数，执行查询，再通过 `user.custom_tool_result` 回传结果。完整接入流程和多媒体场景示例请参见 [[基础] Custom Tool 使用教程](https://ark.volcengine.com/region:cn-beijing/docs/ark/managed-agent-custom-tool-tutorial)。

<span id=".6YWN572u5YaF572u5bel5YW3"></span>

## 配置内置工具

创建 Agent 前，先确定任务必须使用哪些工具、哪些工具可以修改数据，以及哪些调用需要业务应用确认。显式配置 `default_config.enabled` 和 `permission_policy`，再通过 `configs` 覆盖单个工具，可以避免不同工具类型的默认行为造成权限范围偏差。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">不要通过省略 <code>tools</code>、传入空数组 <code>[]</code>，或只配置 MCP、自定义工具等其他工具来关闭主 Agent 的内置工具。主 Agent 在未找到内置工具集配置时会回退为启用全部内置工具，并使用 <code>always_allow</code>；如需限制工具范围，请显式配置 <code>agent_toolset_20260701</code>，将 <code>default_config.enabled</code> 设为 <code>false</code>，再逐项启用必要工具。详细行为请参见 <a href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tools#tool-defaults-and-boundaries">核对默认工具范围</a>。</div>

<span id=".5ZCv55So5YWo6YOo5YaF572u5bel5YW3"></span>

### 启用全部内置工具

如果 Agent 需要使用全部基础执行能力，挂载 `agent_toolset_20260701`，并显式启用工具集和权限策略：

<Tabs>
<Tab zoneid="sI5poBW8D9" title="Curl">
<TabTitle>Curl</TabTitle>

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
        "type": "agent_toolset_20260701",
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

</Tab>
</Tabs>

<span id=".5YWz6Zet6YOo5YiG5bel5YW3"></span>

### 关闭部分工具

如果 Agent 不应访问网页，可以保持工具集启用，再通过 `configs` 关闭 `web_fetch` 和 `web_search`：

<Tabs>
<Tab zoneid="OuB101nD1s" title="Curl">
<TabTitle>Curl</TabTitle>

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
        "type": "agent_toolset_20260701",
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

</Tab>
</Tabs>

<span id=".5Y-q5ZCv55So5b-F6KaB5bel5YW3"></span>

### 只启用必要工具

如果 Agent 只需要读取和检索沙箱文件，先将工具集整体关闭，再显式启用 `read`、`glob` 和 `grep`：

<Tabs>
<Tab zoneid="LSEea8ZSAd" title="Curl">
<TabTitle>Curl</TabTitle>

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
        "type": "agent_toolset_20260701",
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

</Tab>
</Tabs>

这种配置适合代码审查和资料检索等只读任务。

<span id=".6K6-572u5p2D6ZmQ562W55Wl"></span>

### 设置权限策略

`always_allow` 表示平台直接执行工具，`always_ask` 表示平台在执行前暂停 Session，等待业务应用确认。建议在请求中显式配置工具集的默认策略，并通过 `configs[].permission_policy` 覆盖风险更高的单个工具。

权限策略的完整配置方式请参见 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy)。

<span id=".6aqM6K-B5ZKM5aSE55CG5bel5YW36LCD55So"></span>

## 验证和处理工具调用

创建或更新 Agent 后，按以下顺序验证配置是否生效：

1. 调用[查询智能体详情](https://ark.volcengine.com/region:cn-beijing/docs/ark/get-agent-api)，检查响应中的 `tools` 是否符合预期；使用 MCP 工具时，同时检查 `mcp_servers`。

2. 创建新 Session，或调用[升级会话](https://ark.volcengine.com/region:cn-beijing/docs/ark/upgrade-session-api)升级现有 Session。已创建的 Session 不会自动使用 Agent 的新版本。

3. 发送一条能够触发目标工具的测试任务。

4. 检查 Session 事件，确认工具调用、执行结果和最终回复已经形成闭环。

以下以查询订单状态并整理结果为例说明主要事件流。具体事件取决于工具的执行方和权限策略。

<span id=".5aSE55CG5LqR546v5aKD5bel5YW36LCD55So"></span>

### 处理云环境工具调用

云环境中，方舟服务端负责执行内置工具和 MCP 工具。下图展示 Agent 发起调用、按需等待确认并返回结果的过程。`always_allow` 会直接执行工具；`always_ask` 会先暂停 Session，等待业务应用批准或拒绝。

<img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI3MjAiIGhlaWdodD0iNzkwIiB2aWV3Qm94PSIwIDAgNzIwIDc5MCIgcm9sZT0iaW1nIiBhcmlhLWxhYmVsbGVkYnk9InRpdGxlIGRlc2MiPjx0aXRsZSBpZD0idGl0bGUiPuaJmOeuoeeOr+Wig+aJp+ihjOWGhee9ruW3peWFt+aIliBNQ1Ag5bel5YW3PC90aXRsZT48ZGVzYyBpZD0iZGVzYyI+5Y+C5LiO5pa55LmL6Ze05oyJ5pe26Ze06aG65bqP5Lyg6YCS5Lya6K+d5LqL5Lu2PC9kZXNjPjxkZWZzPjxtYXJrZXIgaWQ9ImFycm93IiBtYXJrZXJXaWR0aD0iOCIgbWFya2VySGVpZ2h0PSI4IiByZWZYPSI3IiByZWZZPSI0IiBvcmllbnQ9ImF1dG8iIG1hcmtlclVuaXRzPSJzdHJva2VXaWR0aCI+PHBhdGggZD0iTTAsMCBMOCw0IEwwLDggWiIgZmlsbD0iIzMxNTM2ZiIvPjwvbWFya2VyPjwvZGVmcz48cmVjdCB3aWR0aD0iNzIwIiBoZWlnaHQ9Ijc5MCIgZmlsbD0iI2ZmZmZmZiIvPjxyZWN0IHg9IjI2IiB5PSIxMiIgd2lkdGg9IjE0OCIgaGVpZ2h0PSI0MCIgcng9IjQiIGZpbGw9IiNmNGY3ZjkiIHN0cm9rZT0iIzkxYTRiMyIvPjx0ZXh0IHg9IjEwMCIgeT0iMzciIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjE1IiBmaWxsPSIjMTcyYjNhIj7kuJrliqHlupTnlKg8L3RleHQ+PGxpbmUgeDE9IjEwMCIgeTE9IjUyIiB4Mj0iMTAwIiB5Mj0iNzcyIiBzdHJva2U9IiNhYWI3YzIiIHN0cm9rZS13aWR0aD0iMSIgc3Ryb2tlLWRhc2hhcnJheT0iNCA1Ii8+PHJlY3QgeD0iNTQ2IiB5PSIxMiIgd2lkdGg9IjE0OCIgaGVpZ2h0PSI0MCIgcng9IjQiIGZpbGw9IiNmNGY3ZjkiIHN0cm9rZT0iIzkxYTRiMyIvPjx0ZXh0IHg9IjYyMCIgeT0iMzciIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjE1IiBmaWxsPSIjMTcyYjNhIj7mlrnoiJ/mnI3liqHnq688L3RleHQ+PGxpbmUgeDE9IjYyMCIgeTE9IjUyIiB4Mj0iNjIwIiB5Mj0iNzcyIiBzdHJva2U9IiNhYWI3YzIiIHN0cm9rZS13aWR0aD0iMSIgc3Ryb2tlLWRhc2hhcnJheT0iNCA1Ii8+PHRleHQgeD0iMzYwIiB5PSI5NSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjEuIOW7uueriyBTU0Ug6L+e5o6lIMK3IOetieW+heacjeWKoeerr+Wwsee7qjwvdGV4dD48bGluZSB4MT0iMTA4IiB5MT0iMTA2IiB4Mj0iNjEyIiB5Mj0iMTA2IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjM2MCIgeT0iMTYxIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+Mi4gdXNlci5tZXNzYWdlIMK3IOaPkOS6pOS7u+WKoTwvdGV4dD48bGluZSB4MT0iMTA4IiB5MT0iMTcyIiB4Mj0iNjEyIiB5Mj0iMTcyIiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjM2MCIgeT0iMjI3IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+My4gc2Vzc2lvbi5zdGF0dXNfcnVubmluZyDCtyDlvIDlp4vlpITnkIY8L3RleHQ+PGxpbmUgeDE9IjYxMiIgeTE9IjIzOCIgeDI9IjEwOCIgeTI9IjIzOCIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSIzNjAiIHk9IjI5MyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjQuIGFnZW50LnRvb2xfdXNlIC8gYWdlbnQubWNwX3Rvb2xfdXNlIMK3IOWPkei1t+W3peWFt+iwg+eUqDwvdGV4dD48bGluZSB4MT0iNjEyIiB5MT0iMzA0IiB4Mj0iMTA4IiB5Mj0iMzA0IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjM2MCIgeT0iMzU5IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+NS4gc2Vzc2lvbi5zdGF0dXNfaWRsZe+8iGFsd2F5c19hc2vvvIkgwrcg562J5b6F56Gu6K6kPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSIzNzAiIHgyPSIxMDgiIHkyPSIzNzAiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIHN0cm9rZS1kYXNoYXJyYXk9IjcgNSIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSIzNjAiIHk9IjQyNSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjYuIHVzZXIudG9vbF9jb25maXJtYXRpb27vvIhhbHdheXNfYXNr77yJIMK3IOaJueWHhuaIluaLkue7nTwvdGV4dD48bGluZSB4MT0iMTA4IiB5MT0iNDM2IiB4Mj0iNjEyIiB5Mj0iNDM2IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBzdHJva2UtZGFzaGFycmF5PSI3IDUiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSI0OTEiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj43LiBzZXNzaW9uLnN0YXR1c19ydW5uaW5n77yI5om55YeG5ZCO77yJIMK3IOaBouWkjeaJp+ihjDwvdGV4dD48bGluZSB4MT0iNjEyIiB5MT0iNTAyIiB4Mj0iMTA4IiB5Mj0iNTAyIiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBzdHJva2UtZGFzaGFycmF5PSI3IDUiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSI1NTciIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj44LiBhZ2VudC50b29sX3Jlc3VsdCAvIGFnZW50Lm1jcF90b29sX3Jlc3VsdCDCtyDov5Tlm57lt6Xlhbfnu5Pmnpw8L3RleHQ+PGxpbmUgeDE9IjYxMiIgeTE9IjU2OCIgeDI9IjEwOCIgeTI9IjU2OCIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSIzNjAiIHk9IjYyMyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjkuIGFnZW50Lm1lc3NhZ2Ugwrcg6L+U5Zue5pyA57uI5Zue5aSNPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSI2MzQiIHgyPSIxMDgiIHkyPSI2MzQiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSI2ODkiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4xMC4gc2Vzc2lvbi5zdGF0dXNfaWRsZSDCtyDnu5PmnZ/lvZPliY3ova7mrKE8L3RleHQ+PGxpbmUgeDE9IjYxMiIgeTE9IjcwMCIgeDI9IjEwOCIgeTI9IjcwMCIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48L3N2Zz4=" width="720px" />

<span id=".6Ieq5Yqo5omn6KGM"></span>

#### 自动执行

使用 `always_allow` 时，平台直接执行工具并返回结果：

- 内置工具：`agent.tool_use` → `agent.tool_result`

- MCP 工具：`agent.mcp_tool_use` → `agent.mcp_tool_result`

同一轮可能调用多个工具。使用 `tool_use_id` 或 `mcp_tool_use_id` 将每个结果与对应调用配对，不要只依赖事件到达顺序。

<span id="confirm-tool-use"></span>

#### 确认后执行

内置工具或 MCP 工具使用 `always_ask` 时，Session 会暂停并等待确认：

1. Session 返回 `agent.tool_use` 或 `agent.mcp_tool_use`。

2. Session 返回 `session.status_idle`，其中 `stop_reason.type=requires_action`，`stop_reason.event_ids` 列出待确认的调用事件。

3. 客户端发送 `user.tool_confirmation`，通过 `tool_use_id` 指定调用事件，并将 `result` 设置为 `allow` 或 `deny`。

4. 所有待确认事件处理完成后，Session 返回 `session.status_running`。批准的工具继续执行，拒绝结果交给 Agent 调整后续处理。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">处理确认结果</div>

<div data-tips="true" data-tips-type="warning">拒绝调用时，可以通过 <code>deny_message</code> 把原因回传给 Agent。Agent 会根据拒绝原因选择其他工具、降级处理，或向最终用户说明无法继续执行。</div>

<div data-tips="true" data-tips-type="warning">在 <code>requires_action</code> 阶段，先处理 <code>stop_reason.event_ids</code> 中的全部确认事件，不要用新的 <code>user.message</code> 替代确认。<code>always_allow</code>、<code>always_ask</code> 及覆盖规则请参见 <a href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy">工具权限策略</a>。</div>

<span id=".5aSE55CG6Ieq5omY566h5bel5YW36LCD55So"></span>

### 处理自托管工具调用

自托管 Environment 中的 Worker 完成内置工具后，通过 `user.tool_result` 回传结果：

<img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI5NjAiIGhlaWdodD0iOTIyIiB2aWV3Qm94PSIwIDAgOTYwIDkyMiIgcm9sZT0iaW1nIiBhcmlhLWxhYmVsbGVkYnk9InRpdGxlIGRlc2MiPjx0aXRsZSBpZD0idGl0bGUiPuiHquaJmOeuoSBFbnZpcm9ubWVudCDmiafooYzlhoXnva7lt6Xlhbc8L3RpdGxlPjxkZXNjIGlkPSJkZXNjIj7lj4LkuI7mlrnkuYvpl7TmjInml7bpl7Tpobrluo/kvKDpgJLkvJror53kuovku7Y8L2Rlc2M+PGRlZnM+PG1hcmtlciBpZD0iYXJyb3ciIG1hcmtlcldpZHRoPSI4IiBtYXJrZXJIZWlnaHQ9IjgiIHJlZlg9IjciIHJlZlk9IjQiIG9yaWVudD0iYXV0byIgbWFya2VyVW5pdHM9InN0cm9rZVdpZHRoIj48cGF0aCBkPSJNMCwwIEw4LDQgTDAsOCBaIiBmaWxsPSIjMzE1MzZmIi8+PC9tYXJrZXI+PC9kZWZzPjxyZWN0IHdpZHRoPSI5NjAiIGhlaWdodD0iOTIyIiBmaWxsPSIjZmZmZmZmIi8+PHJlY3QgeD0iMjYiIHk9IjEyIiB3aWR0aD0iMTQ4IiBoZWlnaHQ9IjQwIiByeD0iNCIgZmlsbD0iI2Y0ZjdmOSIgc3Ryb2tlPSIjOTFhNGIzIi8+PHRleHQgeD0iMTAwIiB5PSIzNyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTUiIGZpbGw9IiMxNzJiM2EiPuS4muWKoeW6lOeUqDwvdGV4dD48bGluZSB4MT0iMTAwIiB5MT0iNTIiIHgyPSIxMDAiIHkyPSI5MDQiIHN0cm9rZT0iI2FhYjdjMiIgc3Ryb2tlLXdpZHRoPSIxIiBzdHJva2UtZGFzaGFycmF5PSI0IDUiLz48cmVjdCB4PSIyNzkiIHk9IjEyIiB3aWR0aD0iMTQ4IiBoZWlnaHQ9IjQwIiByeD0iNCIgZmlsbD0iI2Y0ZjdmOSIgc3Ryb2tlPSIjOTFhNGIzIi8+PHRleHQgeD0iMzUzIiB5PSIzNyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTUiIGZpbGw9IiMxNzJiM2EiPuaWueiIn+acjeWKoeerrzwvdGV4dD48bGluZSB4MT0iMzUzIiB5MT0iNTIiIHgyPSIzNTMiIHkyPSI5MDQiIHN0cm9rZT0iI2FhYjdjMiIgc3Ryb2tlLXdpZHRoPSIxIiBzdHJva2UtZGFzaGFycmF5PSI0IDUiLz48cmVjdCB4PSI1MzMiIHk9IjEyIiB3aWR0aD0iMTQ4IiBoZWlnaHQ9IjQwIiByeD0iNCIgZmlsbD0iI2Y0ZjdmOSIgc3Ryb2tlPSIjOTFhNGIzIi8+PHRleHQgeD0iNjA3IiB5PSIzNyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTUiIGZpbGw9IiMxNzJiM2EiPuiHquaJmOeuoSBXb3JrZXI8L3RleHQ+PGxpbmUgeDE9IjYwNyIgeTE9IjUyIiB4Mj0iNjA3IiB5Mj0iOTA0IiBzdHJva2U9IiNhYWI3YzIiIHN0cm9rZS13aWR0aD0iMSIgc3Ryb2tlLWRhc2hhcnJheT0iNCA1Ii8+PHJlY3QgeD0iNzg2IiB5PSIxMiIgd2lkdGg9IjE0OCIgaGVpZ2h0PSI0MCIgcng9IjQiIGZpbGw9IiNmNGY3ZjkiIHN0cm9rZT0iIzkxYTRiMyIvPjx0ZXh0IHg9Ijg2MCIgeT0iMzciIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjE1IiBmaWxsPSIjMTcyYjNhIj7kvIHkuJogU2FuZGJveDwvdGV4dD48bGluZSB4MT0iODYwIiB5MT0iNTIiIHgyPSI4NjAiIHkyPSI5MDQiIHN0cm9rZT0iI2FhYjdjMiIgc3Ryb2tlLXdpZHRoPSIxIiBzdHJva2UtZGFzaGFycmF5PSI0IDUiLz48dGV4dCB4PSIyMjYuNSIgeT0iOTUiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4xLiDlu7rnq4vkuJrliqEgU1NFIMK3IOetieW+heacjeWKoeerr+Wwsee7qjwvdGV4dD48bGluZSB4MT0iMTA4IiB5MT0iMTA2IiB4Mj0iMzQ1IiB5Mj0iMTA2IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjIyNi41IiB5PSIxNjEiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4yLiB1c2VyLm1lc3NhZ2Ugwrcg5o+Q5Lqk5Lu75YqhPC90ZXh0PjxsaW5lIHgxPSIxMDgiIHkxPSIxNzIiIHgyPSIzNDUiIHkyPSIxNzIiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iNDgwIiB5PSIyMjciIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4zLiBQb2xsIC8gQWNrbm93bGVkZ2UgV29yayDCtyDorqTpooYgV29yazwvdGV4dD48bGluZSB4MT0iNTk5IiB5MT0iMjM4IiB4Mj0iMzYxIiB5Mj0iMjM4IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjIyNi41IiB5PSIyOTMiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj40LiBzZXNzaW9uLnN0YXR1c19ydW5uaW5nIMK3IOW8gOWni+WkhOeQhjwvdGV4dD48bGluZSB4MT0iMzQ1IiB5MT0iMzA0IiB4Mj0iMTA4IiB5Mj0iMzA0IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjQ4MCIgeT0iMzU5IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+NS4g5bu656uLIFdvcmtlciBTU0Ugwrcg6K6i6ZiF5bel5YW35LqL5Lu2PC90ZXh0PjxsaW5lIHgxPSI1OTkiIHkxPSIzNzAiIHgyPSIzNjEiIHkyPSIzNzAiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iNDgwIiB5PSI0MjUiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj42LiBhZ2VudC50b29sX3VzZSDCtyDkuIvlj5Hlt6XlhbfosIPnlKg8L3RleHQ+PGxpbmUgeDE9IjM2MSIgeTE9IjQzNiIgeDI9IjU5OSIgeTI9IjQzNiIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSI3MzMuNSIgeT0iNDkxIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+Ny4g5omn6KGM5bel5YW3IMK3IOi/kOihjOacrOWcsOaTjeS9nDwvdGV4dD48bGluZSB4MT0iNjE1IiB5MT0iNTAyIiB4Mj0iODUyIiB5Mj0iNTAyIiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjczMy41IiB5PSI1NTciIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj44LiDov5Tlm57miafooYznu5Pmnpwgwrcg5a6M5oiQ5pys5Zyw5pON5L2cPC90ZXh0PjxsaW5lIHgxPSI4NTIiIHkxPSI1NjgiIHgyPSI2MTUiIHkyPSI1NjgiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iNDgwIiB5PSI2MjMiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj45LiB1c2VyLnRvb2xfcmVzdWx0IMK3IOWbnuS8oOW3peWFt+e7k+aenDwvdGV4dD48bGluZSB4MT0iNTk5IiB5MT0iNjM0IiB4Mj0iMzYxIiB5Mj0iNjM0IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjIyNi41IiB5PSI2ODkiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4xMC4gYWdlbnQudG9vbF9yZXN1bHQgwrcg6L+U5Zue57uT5p6c5LqL5Lu2PC90ZXh0PjxsaW5lIHgxPSIzNDUiIHkxPSI3MDAiIHgyPSIxMDgiIHkyPSI3MDAiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMjI2LjUiIHk9Ijc1NSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjExLiBhZ2VudC5tZXNzYWdlIMK3IOi/lOWbnuacgOe7iOWbnuWkjTwvdGV4dD48bGluZSB4MT0iMzQ1IiB5MT0iNzY2IiB4Mj0iMTA4IiB5Mj0iNzY2IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjIyNi41IiB5PSI4MjEiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4xMi4gc2Vzc2lvbi5zdGF0dXNfaWRsZSDCtyDnu5PmnZ/lvZPliY3ova7mrKE8L3RleHQ+PGxpbmUgeDE9IjM0NSIgeTE9IjgzMiIgeDI9IjEwOCIgeTI9IjgzMiIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48L3N2Zz4=" width="960px" />

1. Session 返回 `agent.tool_use`，下发内置工具调用。

2. Session 返回 `session.status_idle`，其中 `stop_reason.type=requires_action`。

3. Worker 获取对应的 Work，并在企业 Sandbox 中执行工具。

4. Worker 通过 `user.tool_result` 回传结果。`tool_use_id` 必须填写对应的 `agent.tool_use.id`；工具执行失败时，将 `is_error` 设为 `true`，并在 `content` 中返回失败说明。

5. 所有待处理结果回传后，Session 返回 `session.status_running`，Agent 根据工具结果继续处理任务。

Worker 执行期间持续调用 Heartbeat 维持 Work 租约；完整的 Poll、Acknowledge、Heartbeat 和执行环境配置流程请参见 [配置并运行自托管环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-self-hosted-environment)。

<span id=".5aSE55CG6Ieq5a6a5LmJ5bel5YW36LCD55So"></span>

### 处理自定义工具调用

自定义工具由业务应用执行，事件闭环如下：

<img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI3MjAiIGhlaWdodD0iNzI0IiB2aWV3Qm94PSIwIDAgNzIwIDcyNCIgcm9sZT0iaW1nIiBhcmlhLWxhYmVsbGVkYnk9InRpdGxlIGRlc2MiPjx0aXRsZSBpZD0idGl0bGUiPuS4muWKoeW6lOeUqOaJp+ihjCBDdXN0b20gVG9vbDwvdGl0bGU+PGRlc2MgaWQ9ImRlc2MiPuWPguS4juaWueS5i+mXtOaMieaXtumXtOmhuuW6j+S8oOmAkuS8muivneS6i+S7tjwvZGVzYz48ZGVmcz48bWFya2VyIGlkPSJhcnJvdyIgbWFya2VyV2lkdGg9IjgiIG1hcmtlckhlaWdodD0iOCIgcmVmWD0iNyIgcmVmWT0iNCIgb3JpZW50PSJhdXRvIiBtYXJrZXJVbml0cz0ic3Ryb2tlV2lkdGgiPjxwYXRoIGQ9Ik0wLDAgTDgsNCBMMCw4IFoiIGZpbGw9IiMzMTUzNmYiLz48L21hcmtlcj48L2RlZnM+PHJlY3Qgd2lkdGg9IjcyMCIgaGVpZ2h0PSI3MjQiIGZpbGw9IiNmZmZmZmYiLz48cmVjdCB4PSIyNiIgeT0iMTIiIHdpZHRoPSIxNDgiIGhlaWdodD0iNDAiIHJ4PSI0IiBmaWxsPSIjZjRmN2Y5IiBzdHJva2U9IiM5MWE0YjMiLz48dGV4dCB4PSIxMDAiIHk9IjM3IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNSIgZmlsbD0iIzE3MmIzYSI+5Lia5Yqh5bqU55SoPC90ZXh0PjxsaW5lIHgxPSIxMDAiIHkxPSI1MiIgeDI9IjEwMCIgeTI9IjcwNiIgc3Ryb2tlPSIjYWFiN2MyIiBzdHJva2Utd2lkdGg9IjEiIHN0cm9rZS1kYXNoYXJyYXk9IjQgNSIvPjxyZWN0IHg9IjU0NiIgeT0iMTIiIHdpZHRoPSIxNDgiIGhlaWdodD0iNDAiIHJ4PSI0IiBmaWxsPSIjZjRmN2Y5IiBzdHJva2U9IiM5MWE0YjMiLz48dGV4dCB4PSI2MjAiIHk9IjM3IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNSIgZmlsbD0iIzE3MmIzYSI+5pa56Iif5pyN5Yqh56uvPC90ZXh0PjxsaW5lIHgxPSI2MjAiIHkxPSI1MiIgeDI9IjYyMCIgeTI9IjcwNiIgc3Ryb2tlPSIjYWFiN2MyIiBzdHJva2Utd2lkdGg9IjEiIHN0cm9rZS1kYXNoYXJyYXk9IjQgNSIvPjx0ZXh0IHg9IjM2MCIgeT0iOTUiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4xLiDlu7rnq4sgU1NFIOi/nuaOpSDCtyDnrYnlvoXmnI3liqHnq6/lsLHnu6o8L3RleHQ+PGxpbmUgeDE9IjEwOCIgeTE9IjEwNiIgeDI9IjYxMiIgeTI9IjEwNiIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSIzNjAiIHk9IjE2MSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjIuIHVzZXIubWVzc2FnZSDCtyDmj5DkuqTku7vliqE8L3RleHQ+PGxpbmUgeDE9IjEwOCIgeTE9IjE3MiIgeDI9IjYxMiIgeTI9IjE3MiIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSIzNjAiIHk9IjIyNyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjMuIHNlc3Npb24uc3RhdHVzX3J1bm5pbmcgwrcg5byA5aeL5aSE55CGPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSIyMzgiIHgyPSIxMDgiIHkyPSIyMzgiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSIyOTMiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj40LiBhZ2VudC5jdXN0b21fdG9vbF91c2Ugwrcg6K+35rGC5omn6KGM5bel5YW3PC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSIzMDQiIHgyPSIxMDgiIHkyPSIzMDQiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSIzNTkiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj41LiBzZXNzaW9uLnN0YXR1c19pZGxlIMK3IOetieW+heW3peWFt+e7k+aenDwvdGV4dD48bGluZSB4MT0iNjEyIiB5MT0iMzcwIiB4Mj0iMTA4IiB5Mj0iMzcwIiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjM2MCIgeT0iNDI1IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+Ni4gdXNlci5jdXN0b21fdG9vbF9yZXN1bHQgwrcg5Zue5Lyg5omn6KGM57uT5p6cPC90ZXh0PjxsaW5lIHgxPSIxMDgiIHkxPSI0MzYiIHgyPSI2MTIiIHkyPSI0MzYiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSI0OTEiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj43LiBzZXNzaW9uLnN0YXR1c19ydW5uaW5nIMK3IOaBouWkjeaJp+ihjDwvdGV4dD48bGluZSB4MT0iNjEyIiB5MT0iNTAyIiB4Mj0iMTA4IiB5Mj0iNTAyIiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjM2MCIgeT0iNTU3IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+OC4gYWdlbnQubWVzc2FnZSDCtyDov5Tlm57mnIDnu4jlm57lpI08L3RleHQ+PGxpbmUgeDE9IjYxMiIgeTE9IjU2OCIgeDI9IjEwOCIgeTI9IjU2OCIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSIzNjAiIHk9IjYyMyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjkuIHNlc3Npb24uc3RhdHVzX2lkbGUgwrcg57uT5p2f5b2T5YmN6L2u5qyhPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSI2MzQiIHgyPSIxMDgiIHkyPSI2MzQiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PC9zdmc+" width="720px" />

1. Session 返回 `agent.custom_tool_use`，其中包含工具名称、输入参数和调用事件 ID。

2. Session 返回 `session.status_idle`，其中 `stop_reason.type=requires_action`，`stop_reason.event_ids` 列出待处理的调用事件。

3. 业务应用根据工具名称和输入参数执行对应操作。

4. 业务应用发送 `user.custom_tool_result`，并将调用事件 ID 原样填入 `custom_tool_use_id`。如果同一轮存在多个待处理调用，需要分别回传结果。

5. 所有待处理结果回传后，Session 返回 `session.status_running`，Agent 根据工具结果继续处理任务。

完整接入示例请参见 [[基础] Custom Tool 使用教程](https://ark.volcengine.com/region:cn-beijing/docs/ark/managed-agent-custom-tool-tutorial)。所有工具事件的字段结构请参见 [会话事件结构参考](https://ark.volcengine.com/region:cn-beijing/docs/ark/sessions-events-reference-v2)。

<span id=".5pu05paw5bel5YW36YWN572u"></span>

## 更新工具配置

更新 Agent 可以使用的工具时，`tools` 使用整体替换语义。为避免误取消其他可用工具，按照以下步骤读取、合并并写回完整 `tools` 数组：

1. 调用[查询智能体详情](https://ark.volcengine.com/region:cn-beijing/docs/ark/get-agent-api)，获取最新 `version` 和完整 `tools` 数组。

2. 在本地副本中保留不需要修改的工具，只调整目标工具。

3. 调用[更新智能体](https://ark.volcengine.com/region:cn-beijing/docs/ark/update-agent-api)，同时提交当前 `version` 和合并后的完整 `tools` 数组。

4. 再次查询 Agent，确认版本号和工具配置已经更新。

5. 创建新 Session，或升级现有 Session 后验证工具调用。

更新请求中 `tools` 的取值语义如下：

<span aceTableMode="list" aceTableWidth="1,2,3"></span>

| 取值          | 配置结果                                                                                                                     | 使用建议                               |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| 省略或 `null` | 保留当前 `tools` 配置。                                                                                                      | 只修改其他 Agent 字段时使用。          |
| 空数组 `[]`   | 只清空已保存的 `tools` 配置，不会关闭主 Agent 的内置工具。主 Agent 运行时仍会回退为启用全部内置工具，并使用 `always_allow`。 | 如需限制内置工具，改用显式工具集配置。 |
| 非空数组      | 使用请求中的数组整体替换当前配置。                                                                                           | 先读取并合并所有需要保留的工具。       |

<span id=".5o6S5p-l5bel5YW36Zeu6aKY"></span>

## 排查工具问题

先根据 Session 事件流定位中断点，再执行对应检查：

- 未出现目标工具的调用事件：核对 Agent 配置、Session 版本和任务触发条件。

- 出现 `stop_reason.type=requires_action`：根据阻塞事件类型回传确认或工具结果。

- 工具结果包含错误：根据调用 ID 定位输入、权限或执行环境。

- 出现 `session.error`：根据错误类型和重试状态决定等待、修正后重试或创建新 Session。

- 工具可以执行但权限范围过大：核对文件、网络和凭据隔离边界。

<span id=".5qC45a-56YWN572u44CB54mI5pys5ZKM6Kem5Y-R5p2h5Lu2"></span>

### 核对配置、版本和触发条件

如果事件流中没有出现 `agent.tool_use`、`agent.mcp_tool_use` 或 `agent.custom_tool_use`，按以下顺序检查：

1. 查询 Agent，确认目标工具或工具集已经保存。MCP 工具还需要检查 `mcp_servers`。

2. 确认当前 Session 使用的 Agent 版本；Agent 更新不会自动影响已创建的 Session。

3. 按工具类型核对配置与触发条件。

4. 使用相同的明确测试任务比较新旧 Session，避免把任务差异误判为版本问题。

<span aceTableMode="list" aceTableWidth="1,3,3"></span>

| 工具类型   | 配置检查                                                                                                                   | 任务触发条件                                                                                                     |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 内置工具   | `tools` 中包含 `type: "agent_toolset_20260701"`；根据 `default_config.enabled` 和 `configs` 的覆盖结果确认目标工具已启用。 | 测试任务明确要求读取实际文件、执行命令或访问网页，并提供文件路径、命令目标或检索主题。纯文本问答不要求调用工具。 |
| MCP 工具   | `mcp_servers[].name` 与 `tools[].mcp_server_name` 一致；根据 `default_config` 和 `configs` 确认目标 MCP 工具已启用。       | 测试任务明确要求访问对应外部系统，并提供项目、记录或其他业务标识。                                               |
| 自定义工具 | `tools` 中包含 `type: "custom"`，并保存了正确的 `name`、`description` 和 `input_schema`。`description` 需要说明适用条件。  | 测试任务明确要求执行该业务操作，并提供 `input_schema` 中的必需信息。                                             |

Agent 会根据任务内容和工具说明判断是否调用工具。单次未调用不能直接证明配置无效；应先使用明确要求目标能力的测试任务复现。

<span id="tool-defaults-and-boundaries"></span>

#### 核对默认工具范围

主 Agent 与子 Agent 在未配置内置工具集时采用不同的运行时行为：

<span aceTableMode="list" aceTableWidth="2,2,2,3"></span>

| 配置                                                 | 主 Agent                                            | 子 Agent                                                   | 建议                                 |
| ---------------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------- | ------------------------------------ |
| 省略 `tools`、传 `[]`，或只配置其他工具类型          | 回退为启用全部内置工具，权限策略为 `always_allow`。 | 不提供内置工具；Session 启用的 Memory 工具不受此规则影响。 | 不依赖省略行为，显式配置内置工具集。 |
| 配置 `agent_toolset_20260701`，省略 `default_config` | 服务端保存 `enabled=true` 和 `always_allow`。       | 按相同配置提供内置工具。                                   | 在请求中显式写出启用状态和权限策略。 |
| 配置 `default_config.enabled=false`，再逐项启用      | 只提供 `configs` 中显式启用的工具。                 | 只提供 `configs` 中显式启用的工具。                        | 需要最小工具集时使用。               |

判断子 Agent 是否实际执行工具时，以子线程中的 `agent.tool_use` 和 `agent.tool_result` 为准。System Prompt 或最终回复中声称已执行工具，不代表运行环境实际提供或执行了该工具。

<span id=".5qC45a-56buY6K6k5p2D6ZmQ562W55Wl"></span>

#### 核对默认权限策略

权限策略存在以下默认分支：

<span aceTableMode="list" aceTableWidth="2,2,2"></span>

| 配置                                                | 内置工具                                    | MCP 工具                                    |
| --------------------------------------------------- | ------------------------------------------- | ------------------------------------------- |
| 整个 `default_config` 省略                          | 服务端保存 `always_allow`。                 | 服务端保存 `always_allow`。                 |
| 已提供 `default_config`，但省略 `permission_policy` | 运行时使用 `always_allow`。                 | 运行时使用 `always_ask`。                   |
| 显式配置 `permission_policy`                        | 使用配置的 `always_allow` 或 `always_ask`。 | 使用配置的 `always_allow` 或 `always_ask`。 |

<span id=".5aSE55CGLXJlcXVpcmVzLWFjdGlvbg=="></span>

### 处理 requires_action

Session 返回 `stop_reason.type=requires_action` 时，先读取 `stop_reason.event_ids`，再根据每个阻塞事件的类型执行对应操作：

<span aceTableMode="list" aceTableWidth="2,2,3"></span>

| 阻塞事件                                 | 场景                                          | 处理方式                                                                                                                                                                               |
| ---------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent.tool_use` 或 `agent.mcp_tool_use` | 云环境中的工具使用 `always_ask`。             | 发送 `user.tool_confirmation`，将阻塞事件 ID 填入 `tool_use_id`，并设置 `allow` 或 `deny`。确认 MCP 调用时也使用 `tool_use_id`；`mcp_tool_use_id` 仅用于关联 `agent.mcp_tool_result`。 |
| `agent.tool_use`                         | 自托管 Environment 中的 Worker 执行内置工具。 | Worker 完成执行后发送 `user.tool_result`，使用 `tool_use_id` 关联调用；失败时设置 `is_error: true` 并返回错误说明。                                                                    |
| `agent.custom_tool_use`                  | 业务应用执行自定义工具。                      | 发送 `user.custom_tool_result`，使用 `custom_tool_use_id` 关联调用；失败时设置 `is_error: true` 并返回错误说明。                                                                       |

同一轮可能包含多个阻塞事件。处理 `stop_reason.event_ids` 中的全部事件后，Session 才会恢复运行。不要用新的 `user.message` 代替确认或工具结果。

<span id=".5o6S5p-l5bel5YW357uT5p6c6ZSZ6K-v"></span>

### 排查工具结果错误

工具结果包含错误时，先关联对应的调用事件，再定位执行方：

- `agent.tool_result`：通过 `tool_use_id` 检查原始工具输入，再核对文件路径、Environment 权限、网络策略和执行日志。

- `agent.mcp_tool_result`：通过 `mcp_tool_use_id` 检查原始工具输入，再核对 MCP 工具启用状态、凭据和 Server 返回信息。

- `user.tool_result` 或 `user.custom_tool_result`：检查业务应用或 Worker 的执行日志，确认 `is_error`、`content` 和调用 ID 与实际结果一致。

工具结果尚未处理完成时，不要重复发送原始任务，否则同一工具可能重复执行。

<span id=".5aSE55CGLXNlc3Npb24t6ZSZ6K-v"></span>

### 处理 Session 错误

`session.error` 不一定由工具配置引起。先读取 `error.type`、`error.message` 和 `error.retry_status.type`，并结合错误前的最后一个事件定位问题。

<span aceTableMode="list" aceTableWidth="1,3"></span>

| 重试状态    | 处理方式                                                                           |
| ----------- | ---------------------------------------------------------------------------------- |
| `retrying`  | 服务端正在自动重试。继续消费事件，不要重复发送任务。                               |
| `exhausted` | 当前轮次的重试次数已用尽。等待 Session 恢复为 `idle`，修正输入或配置后再发送任务。 |
| `terminal`  | Session 将进入 `terminated`，不能继续发送事件。修正配置后创建新 Session。          |

如果错误发生在 MCP 调用阶段，按错误类型继续检查：

- `mcp_authentication_failed_error`：检查 MCP Server 的鉴权配置、Session 引用的 Vaults 和凭据有效性。

- `mcp_connection_failed_error`：检查 MCP Server URL、网络连通性和服务状态。

修正 MCP 配置后，更新 Agent，并创建新 Session 或升级现有 Session。如果 `error.type` 不是 MCP 相关类型，且错误前没有工具调用事件，优先排查模型调用或 Session 运行问题，不要把所有 `session.error` 都归因于工具。事件字段和其他错误类型请参见 [会话事件结构参考](https://ark.volcengine.com/region:cn-beijing/docs/ark/sessions-events-reference-v2)。

<span id=".5qC45a-55paH5Lu25ZKM572R57uc6ZqU56a7"></span>

### 核对文件和网络隔离

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">关闭 <code>write</code>、<code>edit</code>、<code>web_fetch</code> 或 <code>web_search</code> 不等于完成强隔离。<code>read</code>、<code>glob</code> 和 <code>grep</code> 仍可能访问敏感文件；如果 Agent 仍可使用 <code>bash</code>，或者 Environment 允许不受限联网，命令仍可能读取或修改文件并访问网络。需要强隔离时，同时收敛内置工具、Environment 文件和网络策略，以及外部凭据权限。</div>

<span id=".55u45YWz5paH5qGj"></span>

## 相关文档

<columns>
<columnsItem zoneid="gkdBnB72cv">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent" >

**Agent**

Agent 是包含基本信息、System Prompt、扩展能力的配置模板。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/skills" >

**Skills**

Skills 用于给 Agent 补充领域知识、操作流程和最佳实践。

</card>

</columnsItem>
<columnsItem zoneid="JNsNfG8hPM">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/mcp" >

**MCP**

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy" >

**工具权限策略**

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待确认。

</card>

</columnsItem>
</columns>

<a id="doc-2553720"></a>

---

## 工具权限策略

> 来源：[https://docs.volcengine.com/docs/82379/2553720?lang=zh](https://docs.volcengine.com/docs/82379/2553720?lang=zh)

工具权限策略用于控制 Agent 发起工具调用时，是自动执行，还是暂停等待确认。

当前权限策略只作用于服务端执行的工具：

- 内置工具集 `agent_toolset_20260701`

- MCP 工具集 `mcp_toolset`

<span id=".5p2D6ZmQ562W55Wl57G75Z6L"></span>

## 权限策略类型

<span aceTableMode="list" aceTableWidth="1,3"></span>

| 策略           | 说明                               |
| -------------- | ---------------------------------- |
| `always_allow` | 工具调用自动执行，不需要人工确认。 |
| `always_ask`   | 工具调用前暂停，等待确认后再继续。 |

权限策略的默认值取决于是否提供整个 `default_config`：

<span aceTableMode="list" aceTableWidth="2,2,2"></span>

| 配置                                              | 内置工具集                                    | MCP 工具集                                    |
| ------------------------------------------------- | --------------------------------------------- | --------------------------------------------- |
| 省略整个 `default_config`                         | 服务端保存 `enabled=true` 和 `always_allow`。 | 服务端保存 `enabled=true` 和 `always_allow`。 |
| 提供 `default_config`，但省略 `permission_policy` | 运行时使用 `always_allow`。                   | 运行时使用 `always_ask`。                     |
| 显式配置 `permission_policy`                      | 使用配置的策略。                              | 使用配置的策略。                              |

为了让 Agent 回显和运行时行为可直接核对，建议始终显式配置 `default_config.enabled` 和 `default_config.permission_policy`。

<span id=".5Li65pW05Liq5bel5YW36ZuG6K6-572u5p2D6ZmQ562W55Wl"></span>

## 为整个工具集设置权限策略

你可以在 `default_config.permission_policy` 中为整个工具集配置统一策略。

下面的示例把内置工具集的默认策略改成 `always_ask`：

<Tabs>
<Tab zoneid="z2MRkS8JUy" title="Curl">
<TabTitle>Curl</TabTitle>

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
        "type": "agent_toolset_20260701",
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

</Tab>
</Tabs>

不要只传 `default_config.enabled` 而省略 `permission_policy`。内置工具和 MCP 工具对空策略采用不同的运行时默认值。

<span id=".5Li65Y2V5Liq5bel5YW36KaG55uW5p2D6ZmQ562W55Wl"></span>

## 为单个工具覆盖权限策略

除了为整个工具集设置默认策略外，你还可以在 `configs` 中对单个工具做更细粒度的覆盖。

下面的示例保留内置工具集默认自动执行，但要求 `bash` 每次执行前都先确认：

<Tabs>
<Tab zoneid="vVekHNFx4l" title="Curl">
<TabTitle>Curl</TabTitle>

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
        "type": "agent_toolset_20260701",
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

</Tab>
</Tabs>

这种覆盖关系适合把大部分低风险工具保持自动执行，只对高风险工具额外加一道确认。

<span id=".5Li6LW1jcC3lt6Xlhbfpm4borr7nva7mnYPpmZDnrZbnlaU="></span>

## 为 MCP 工具集设置权限策略

下面的示例将可信 MCP Server 的工具集显式设置为 `always_allow`，便于直接核对自动执行范围：

<Tabs>
<Tab zoneid="agvBzPWIlp" title="Curl">
<TabTitle>Curl</TabTitle>

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
        "type": "agent_toolset_20260701",
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

</Tab>
</Tabs>

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">只有在确认 MCP Server 及其暴露工具都可信时，才将策略显式设置为 <code>always_allow</code>。如果 MCP Server 后续新增工具，工具集级的自动放行策略也会作用于新增工具。</div>

<span id=".5L2_55So5bu66K6u"></span>

## 使用建议

- 对只读工具，可以优先考虑 `always_allow`。

- 对会修改文件、执行命令或访问外部系统的工具，建议优先考虑 `always_ask`。

- 对 MCP 工具，建议先按最小权限原则收敛 `configs`，再决定是否自动放行。

工具集配置以及 `user.tool_confirmation` 对应的运行时事件流，详见 [Tools](https://ark.volcengine.com/region:cn-beijing/docs/ark/tools)。如果你要管理 MCP Server 及其工具，详情请参见 [MCP](https://ark.volcengine.com/region:cn-beijing/docs/ark/mcp)。

<span id=".55u45YWz5paH5qGj"></span>

## 相关文档

<columns>
<columnsItem zoneid="JqDDSpoKWg">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent" >

**Agent**

Agent 是包含基本信息、System Prompt、扩展能力的配置模板。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/skills" >

**Skills**

Skills 用于给 Agent 补充领域知识、操作流程和最佳实践。

</card>

</columnsItem>
<columnsItem zoneid="itFDPGI5eK">

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/mcp" >

**MCP**

MCP（Model Context Protocol）用于把第三方系统的工具与数据源接入 Agent。

</card>

<card mode="container" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tools" >

**Tools**

Tools 决定 Agent 在 Session 中能主动调用哪些执行能力。

</card>

</columnsItem>
</columns>

<a id="doc-2553721"></a>

---

## 配置云环境

> 来源：[https://docs.volcengine.com/docs/82379/2553721?lang=zh](https://docs.volcengine.com/docs/82379/2553721?lang=zh)

本文介绍如何为方舟 Managed Agents 创建和管理云端沙箱环境，包括预装依赖包、环境变量、启动脚本和产物存储。

**Environment** 描述 Agent 运行时使用的沙箱模板。一个 Environment 可以被多个 Session 复用，但每个 Session 会启动独立的沙箱实例，文件系统状态彼此隔离。

Managed Agents 同时支持云环境和自托管环境。本页只介绍由方舟管理沙箱的云环境。如果你需要在企业基础设施中执行工具或访问内网资源，选择自托管环境，详情请参见 [自托管环境概述](https://ark.volcengine.com/region:cn-beijing/docs/ark/self-hosted-environment-overview)。

<span id="create-environment"></span>

## 创建环境

你可以在控制台的 [Environments](https://ark.volcengine.com/region:cn-beijing/managed-agents/environments?projectName=default) 页面创建 Environment，也可以使用 API 创建。

在控制台单击 **创建 Environment**，按需配置以下内容：

- 类型：选择云托管环境。

- 预装包：配置沙箱启动前安装的系统包和语言依赖。

- 环境变量：配置注入沙箱进程的非敏感键值对。

- 初始化脚本：配置沙箱启动阶段执行的脚本。

- 产物存储：可选。需要长期保留 Agent 产物时，选择自己的 TOS 目录；不选择时使用方舟公共 TOS。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">创建 Environment 时使用清晰且唯一的名称，便于区分开发、测试和生产等不同用途。</div>

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

- `name`：Environment 名称，在当前项目内需要唯一。

- `description`：Environment 的说明信息。

- `config.type`：运行环境类型，云托管环境取值为 `cloud`。

- `config.networking.type`：沙箱的出站网络访问策略。`unrestricted` 允许完整出站网络访问，但仍受通用安全拦截列表限制。

- `config.packages`：沙箱启动前预安装的依赖包。依赖会在使用同一 Environment 的 Session 之间缓存。若同时配置多个包管理器，系统按 `apt`、`cargo`、`gem`、`go`、`npm`、`pip` 的顺序执行。依赖版本可以显式锁定，未指定版本时安装最新版本。

  | 字段    | 包管理器           | 示例                                        |
  | ------- | ------------------ | ------------------------------------------- |
  | `apt`   | 系统包（apt\-get） | `"ffmpeg"`                                  |
  | `cargo` | Rust（cargo）      | `"ripgrep@14.0.0"`                          |
  | `gem`   | Ruby（gem）        | `"rails:7.1.0"`                             |
  | `go`    | Go modules         | `"golang.org/x/tools/cmd/goimports@latest"` |
  | `npm`   | Node.js（npm）     | `"express@4.18.0"`                          |
  | `pip`   | Python（pip）      | `"pandas==2.2.0"`                           |

- `config.env`：注入沙箱进程的环境变量。需要让 Agent 读取非敏感业务参数时配置，例如时区或服务地址。

- `config.setup_script`：沙箱启动阶段执行的初始化脚本。需要在每次启动沙箱时准备目录、下载资源或执行自定义初始化命令时配置。

- `config.tos`：Agent 最终产物的 TOS 存储位置。需要长期保留 Agent 生成的报告、文件或代码时配置；不配置时产物使用方舟公共 TOS。

<span id="configure-output-storage"></span>

## 配置产物存储

需要将产物长期保存在自己的 TOS Bucket 时，为 Environment 配置 `config.tos`；继续使用方舟公共 TOS 时，省略该字段。无论选择哪种存储方式，Agent 都将最终交付物写入沙箱内的 `/mnt/session/outputs/`。

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 你的配置            | 产物位置                                                                       | 你需要做什么                                                                     |
| ------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| 不配置 `config.tos` | 方舟公共 TOS                                                                   | 在平台 TTL 到期前下载所需文件。删除 Session 时，平台会清理默认存储中的关联产物。 |
| 配置 `config.tos`   | 你的 TOS Bucket，完整对象路径为 `{prefix}outputs/{env-id}/{session-id}/{file}` | 在 TOS 中管理对象生命周期。删除 Session 不会删除 Bucket 中的对象。               |

将产物写入自己的 TOS Bucket 时，需要满足以下条件：

- 目标 Bucket 已在方舟项目中完成授权，详情请参见 [用户对象存储（TOS）授权](https://ark.volcengine.com/region:cn-beijing/docs/ark/project-configuration#4eb1b277)。

- Bucket 与 Managed Agents 服务须部署在同一地域，跨地域 Bucket 会在预检查阶段被拒绝。

- `bucket` 和 `prefix` 必须同时提供，不能只配置其中一个字段。

- `prefix` 使用相对路径，例如 `ark-files/`。最终对象路径会自动追加 `outputs/{env-id}/{session-id}/{file}`。

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

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">先更新 Environment，再创建需要使用新存储配置的 Session。已有 Session 使用创建时冻结的配置快照，不会自动切换到新的 Bucket 或 <code>prefix</code>。</div>

<span id="use-environment"></span>

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

如果只有某次任务需要使用不同的产物存储位置，可以在创建 Session 时使用 Environment 覆写，不需要修改可复用的 Environment。详情请参见 [覆写产物存储（可选）](https://ark.volcengine.com/region:cn-beijing/docs/ark/start-session#override-output-storage)。

<span id="environment-lifecycle"></span>

## 环境生命周期

- 多个 Session 可以引用同一个环境，但每个 Session 都会获得独立的沙箱实例。

- Session 之间不共享文件系统状态。

- 环境本身不做版本化管理。如果你频繁更新环境配置，建议在业务侧记录变更，以便追溯某个 Session 使用的是哪一版环境。

- Environment 被 `idle` 或 `running` 状态的 Session 引用时不能删除。删除前，先终止或删除这些 Session；其他状态的 Session 不会阻止删除 Environment。

<span id="manage-environment"></span>

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

<span id="preinstalled-runtimes"></span>

## 预置运行时

云沙箱默认包含常见语言运行时、数据库和工具。需要确认具体内置版本、目录用途或完整清单时，详情请参见 [云沙箱参考](https://ark.volcengine.com/region:cn-beijing/docs/ark/cloud-sandbox-reference)。

<a id="doc-2553722"></a>

---

## 云沙箱参考

> 来源：[https://docs.volcengine.com/docs/82379/2553722?lang=zh](https://docs.volcengine.com/docs/82379/2553722?lang=zh)

本文介绍 Managed Agents 云端沙箱预安装的编程语言、数据库、常用工具（Utilities）和基础资源规格，便于在配置环境或评估任务依赖时快速参考。

云端沙箱运行在方舟提供的隔离 Linux 容器中。运行时、数据库客户端和常用命令行工具已预安装，Agent 可以直接使用。

> 本文中规格描述适用于 `cloud` 类型的 Environments。

<span id=".57yW56iL6K-t6KiA"></span>

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

<span id=".5pWw5o2u5bqT"></span>

## 数据库

默认提供轻量数据库和数据库客户端，适合本地数据处理，或连接外部数据库/实例。

| 数据库            | 描述                                    |
| ----------------- | --------------------------------------- |
| SQLite            | 已预安装，可立即使用                    |
| PostgreSQL 客户端 | 用于连接外部数据库的 `psql` 客户端。    |
| Redis 客户端      | 用于连接外部 Redis 实例的 `redis-cli`。 |

- SQLite 可在本地使用。

- 数据库服务器，例如 PostgreSQL 和 Redis，默认不在沙箱环境中运行。沙箱提供用于连接的客户端工具。

<span id=".dXRpbGl0aWVzLeW3peWFtw=="></span>

## Utilities 工具

内置多类常用工具，覆盖系统操作、开发构建、文件搜索、进程查看和文本处理等场景。

<span id=".57O757uf5bel5YW3"></span>

### 系统工具

| 工具                  | 描述                                     |
| --------------------- | ---------------------------------------- |
| `git`                 | 版本控制。                               |
| `curl`, `wget`        | HTTP 客户端。                            |
| `jq`                  | JSON 处理。                              |
| `tar`, `zip`, `unzip` | 归档、压缩与解压工具。                   |
| `ssh`, `scp`          | 远程访问与文件传输工具（需要启用网络）。 |
| `tmux`, `screen`      | 终端多路复用工具。                       |

<span id=".5byA5Y-R5bel5YW3"></span>

### 开发工具

| 工具             | 描述             |
| ---------------- | ---------------- |
| `make`, `cmake`  | 构建系统。       |
| `ripgrep` (`rg`) | 快速文件搜索。   |
| `tree`           | 目录结构可视化。 |
| `htop`           | 进程监控。       |

<span id=".5paH5pys5aSE55CG"></span>

### 文本处理

| 工具                 | 描述                 |
| -------------------- | -------------------- |
| `sed`, `awk`, `grep` | 流式文本处理工具。   |
| `vim`, `nano`        | 文本编辑器。         |
| `diff`, `patch`      | 文件比较与补丁工具。 |

<span id=".5rKZ566x6KeE5qC8"></span>

## 沙箱规格

以下为云端沙箱的基础资源规格，可用于评估运行时资源和网络能力。

| 属性     | 具体配置         |
| -------- | ---------------- |
| 操作系统 | Ubuntu 22.04 LTS |
| 架构     | x86_64 (amd64)   |
| 内存     | 4 GB             |
| 磁盘空间 | 10 GB            |
| 网络     | 默认开启         |

<span id=".5YWz6ZSu55uu5b2V5Y-KLWFnZW50Leadg-mZkA=="></span>

## 关键目录及 Agent 权限

云端沙箱的文件系统按用途划分为**工作区**、**挂载目录**和**临时目录**三类。挂载目录 `/mnt` 下进一步区分只读的知识与资源（Memory、Skills、上传文件）和读写的产出与存储（Outputs、Storage）。Agent 通过标准文件工具访问这些目录，权限由平台强制约束。

<span id=".55uu5b2V5biD5bGA"></span>

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

<span id=".55uu5b2V5p2D6ZmQ"></span>

### 目录权限

| 目录                   | 用途                                                                                                                   | Agent 读权限 | Agent 写权限 |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------ | ------------ |
| `$HOME`                | 用户 Home                                                                                                              | ✓            | ✓            |
| `/workspace`           | 工作区，Agent 的主要工作目录<br><br>**注意**：`/workspace/AGENTS.md`（System Prompt）为 read\-only 权限。              | ✓            | ✓            |
| `/mnt`                 | 挂载目录                                                                                                               | ✓            | ✓            |
| `/mnt/memory`          | Agent Memory 目录，参见 [Memory](https://www.volcengine.com/docs/82379/2553728)                                        | ✓            | ✗            |
| `/mnt/skills`          | Agent Skills 目录，参见 [Skills](https://www.volcengine.com/docs/82379/2553717)                                        | ✓            | ✗            |
| `/mnt/session/uploads` | Files API 上传文件挂载目录，参见 [上传并挂载文件](https://www.volcengine.com/docs/82379/2553727)                       | ✓            | ✗            |
| `/mnt/session/outputs` | Agent 产出目录。写入这里的文件会注册到 Files API；需要将产物写入自己的 TOS Bucket 时，为 Environment 配置 `config.tos` | ✓            | ✓            |
| `/mnt/session/storage` | Session 的 TOS 资源挂载目录，详情请参见 [上传与挂载文件](https://www.volcengine.com/docs/82379/2553727)                | ✓            | ✓            |
| `/tmp`                 | 临时目录                                                                                                               | ✓            | ✓            |
| 其它任意路径           | \-                                                                                                                     | ✗            | ✗            |

<span id=".5Yy65YiG5Lqn54mp5a2Y5YKo5LiOLXRvcy3otYTmupDmjILovb0="></span>

### 区分产物存储与 TOS 资源挂载

`/mnt/session/outputs` 和 `/mnt/session/storage` 都可能关联 TOS，但用途不同：

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 能力                 | 沙箱路径               | 用途                                                                                                                             |
| -------------------- | ---------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Environment 产物存储 | `/mnt/session/outputs` | Agent 写入最终交付物。不配置 `config.tos` 时使用方舟公共 TOS；需要写入自己的 TOS Bucket 时配置该字段。Agent 使用的沙箱路径不变。 |
| Session TOS 资源     | `/mnt/session/storage` | 将已有 TOS 对象作为 Session 输入或共享数据挂载到沙箱，通过 `resources` 配置。                                                    |

Environment 产物存储的配置方法，详情请参见 [配置产物存储](https://www.volcengine.com/docs/82379/2553721#configure-output-storage)。

<span id=".5L2_55So6KeE5YiZ"></span>

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

1. 创建 Session：配置沙箱、绑定 Agent 与 Environment，此时 Agent **不**开始任何工作。

2. 发送首个事件：通过 `user.message` 事件把任务交给 Agent，Session 进入 `running` 状态开始执行。

<span id=".5YeG5aSH5bel5L2c"></span>

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/ark/base-url-and-authentication)。

<span id=".5Yib5bu6LXNlc3Npb24="></span>

## 创建 Session

创建 Session 必需两个上游资源 ID：

- Agent ID：由 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent) 创建后获得，形如 `agent-20260701120000-xxxxx`。

- Environment ID：由 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment) 创建后获得，形如 `env-20260701120000-xxxxx`。

<span id=".5L2_55SoLWFnZW50LeacgOaWsOeJiOacrO-8iOaOqOiNkOWFpemXqO-8iQ=="></span>

### 使用 Agent 最新版本（推荐入门）

Agent 是带版本的资源，以字符串形式传入 `agent` ID 时，Session 会用该 Agent 的**最新版本**启动。

<Tabs>
<Tab zoneid="WSMbp6Doof" title="Curl">
<TabTitle>Curl</TabTitle>

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-20260701120000-abcde",
    "environment_id": "env-20260701120000-fghij"
  }'
```

</Tab>
</Tabs>

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

响应中 `status` 为 `idle`，表示 Session 已就绪等待首个事件。完整状态包括 `initializing`、`idle`、`running`、`rescheduling`、`upgrading`、`failed` 和 `terminated`。正常轮次结束后，Session 回到 `idle`；进入 `idle` 时，平台会保存沙箱状态快照，便于后续恢复。状态机与沙箱状态保留期详情请参见 [数据保留与删除影响](https://ark.volcengine.com/region:cn-beijing/docs/ark/manage-session#checkpoint-retention)。

<span id=".5Zu65a6aLWFnZW50LeeJiOacrO-8iOeBsOW6puWPkeW4g-WcuuaZr--8iQ=="></span>

### 固定 Agent 版本（灰度发布场景）

当你需要把 Session 锁定到 Agent 的某个具体版本（用于回滚、灰度对比、产品定版）时，以对象形式传入 `agent`，显式指定 `version`：

<Tabs>
<Tab zoneid="cwogc5kDov" title="Curl">
<TabTitle>Curl</TabTitle>

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": {"type": "agent", "id": "agent-20260701120000-abcde", "version": 1},
    "environment_id": "env-20260701120000-fghij"
  }'
```

</Tab>
</Tabs>

固定版本后，即使该 Agent 后续发布了新版本，本 Session 仍按 `version: 1` 的行为运行。这让你可以分阶段灰度推出新版本，而不影响存量 Session 的行为一致性。

<span id="override-output-storage"></span>

## 覆写产物存储（可选）

需要沿用 Environment 的产物存储配置时，在创建 Session 时传入 `environment_id`：

- Environment 未配置 `config.tos`：产物使用方舟公共 TOS。你需要在平台 TTL 到期前下载所需文件。

- Environment 已配置 `config.tos`：产物写入你配置的 TOS Bucket，完整对象路径为 `{prefix}outputs/{env-id}/{session-id}/{file}`。

只有当前任务需要使用不同的 Bucket 或 `prefix` 时，改用 `environment` 对象创建 Session，并通过 `environment_with_overrides` 临时覆写 `config.tos`：

<Tabs>
<Tab zoneid="LibIpSaA0U" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

Environment 覆写遵循以下规则：

- `environment_id` 与 `environment` 允许同时提供，但同时提供时，`environment.id` 必须与 `environment_id` 相同，否则请求会被拒绝。

- `environment.id` 指向作为基础配置的 Environment。

- `config.tos` 是原子配置。传非空对象时，`bucket` 和 `prefix` 必须同时提供。

- 省略 `config.tos` 时，产物使用 Environment 中配置的存储位置；传空对象 `{}` 时，本次 Session 的产物存入方舟公共 TOS。

- 覆写只对当前 Session 生效，不会修改原 Environment，也不会影响之后创建的其他 Session。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">无论沿用还是覆写存储配置，都让 Agent 将交付物写入 <code>/mnt/session/outputs/</code>。Environment 覆写不会改变 Agent 使用的沙箱目录。</div>

<span id=".6YCa6L-HLXZhdWx0cy3ms6jlhaXnu4jnq6_nlKjmiLflh63mja7vvIjlj6_pgInvvIk="></span>

## 通过 Vaults 注入终端用户凭据（可选）

如果 Agent 配置了需要鉴权的 MCP 工具（详情请参见 [使用 Vaults 认证](https://ark.volcengine.com/region:cn-beijing/docs/ark/use-vaults-authentication)），创建 Session 时通过 `vault_ids` 引用预存凭据。方舟会自动管理 token 刷新与注入。

最简示例，把 Vaults（凭据保管库）挂到 Session：

<Tabs>
<Tab zoneid="K0fezpzT0c" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

多个 Vaults 匹配规则、无匹配时的运行时行为、轮换与诊断，详情请参见 [使用 Vaults 认证](https://ark.volcengine.com/region:cn-beijing/docs/ark/use-vaults-authentication)。

<span id=".5ZCv5YqoLXNlc3Npb27vvJrlhYjorqLpmIXkuovku7bmtYHvvIzlho3lj5HpgIHnrKzkuIDkuKrkuovku7Y="></span>

## 启动 Session：先订阅事件流，再发送第一个事件

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning"><strong>创建 Session 仅完成沙箱配置，不会启动任何工作。</strong> 必须发送 <code>user.message</code> 事件，Agent 才会开始执行。</div>

这种解耦设计让客户端可以先注入 Vaults 凭据、检查沙箱环境，再发送首个事件启动 Agent。需要实时接收本轮事件时，按以下顺序执行：

1. 打开 `GET /sessions/{session_id}/events/stream` 事件流。

2. 等待服务端返回 `: ready`，确认订阅已经建立。

3. 向 `POST /sessions/{session_id}/events` 提交一条 `user.message`。

4. 继续读取同一条事件流，直到收到 `session.status_idle` 或 `session.status_terminated`。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">SSE 只推送连接建立后产生的事件，不回放历史事件。如果先发送消息再建立连接，客户端可能漏掉已经产生的消息、工具调用或状态事件。</div>

以下示例需要本地已安装 `curl` 和 `jq`：

<Tabs>
<Tab zoneid="lLZlGoNYhI" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

<span id=".5ZON5bqU5qih5Z6L5LiO5LiL5LiA5q2l"></span>

### 响应模型与下一步

发送事件后，Session 进入 `running` 状态。上面的示例会通过已建立的 SSE 流接收消息、工具调用和状态事件，并在会话进入 `idle` 或 `terminated` 后结束监听。事件字段与断线续传方式请参见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream)；若仅需轮询状态，可周期性 GET Session 详情，详情请参见 [管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/ark/manage-session)。

<a id="doc-2553724"></a>

---

## 管理 Session

> 来源：[https://docs.volcengine.com/docs/82379/2553724?lang=zh](https://docs.volcengine.com/docs/82379/2553724?lang=zh)

Session 创建后，你可以查询状态、列出 Session、更新标题和标签、升级运行配置，以及永久删除 Session。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">创建 Session 与发送首个事件的方法，详情请参见 <a href="https://ark.volcengine.com/region:cn-beijing/docs/ark/start-session">启动 Session</a>。</div>

<span id=".5YeG5aSH5bel5L2c"></span>

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/ark/base-url-and-authentication)。

<span id=".c2Vzc2lvbi3nirbmgIHmnLo="></span>

## Session 状态机

<span aceTableMode="list" aceTableWidth="1,2"></span>

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

<img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI3MjAiIGhlaWdodD0iNTIwIiB2aWV3Qm94PSIwIDAgNzIwIDUyMCIgcm9sZT0iaW1nIiBhcmlhLWxhYmVsbGVkYnk9InRpdGxlIGRlc2MiPjx0aXRsZSBpZD0idGl0bGUiPlNlc3Npb24g6LWE5rqQ54q25oCB5py6PC90aXRsZT48ZGVzYyBpZD0iZGVzYyI+U2Vzc2lvbiDku47liJ3lp4vljJbov5vlhaXnqbrpl7LmiJblpLHotKXvvIznqbrpl7LkuI7ov5DooYzjgIHljYfnuqfkuYvpl7TliIfmjaLvvIzov5DooYzkuI7ph43mlrDosIPluqbkuYvpl7TliIfmjaLvvIzlubblj6/ku47ku7vmhI/nirbmgIHov5vlhaXnu4jmraLnirbmgIE8L2Rlc2M+PGRlZnM+PG1hcmtlciBpZD0iYXJyb3ciIG1hcmtlcldpZHRoPSI4IiBtYXJrZXJIZWlnaHQ9IjgiIHJlZlg9IjciIHJlZlk9IjQiIG9yaWVudD0iYXV0byIgbWFya2VyVW5pdHM9InN0cm9rZVdpZHRoIj48cGF0aCBkPSJNMCwwIEw4LDQgTDAsOCBaIiBmaWxsPSIjNDI1YjcwIi8+PC9tYXJrZXI+PC9kZWZzPjxyZWN0IHdpZHRoPSI3MjAiIGhlaWdodD0iNTIwIiBmaWxsPSIjZmZmIi8+PHJlY3QgeD0iMjAiIHk9IjIwIiB3aWR0aD0iNjgwIiBoZWlnaHQ9IjM5MCIgcng9IjYiIGZpbGw9IiNmYWZiZmMiIHN0cm9rZT0iI2FhYjdjMiIgc3Ryb2tlLWRhc2hhcnJheT0iNSA1Ii8+PHRleHQgeD0iMzYiIHk9IjQ2IiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzUzNmI3ZCI+U2Vzc2lvbiDotYTmupDnirbmgIE8L3RleHQ+PHJlY3QgeD0iNTAiIHk9IjcwIiB3aWR0aD0iMTMwIiBoZWlnaHQ9IjQ2IiByeD0iNCIgZmlsbD0iI2ZmZjdlOCIgc3Ryb2tlPSIjYjc3OTFmIi8+PHRleHQgeD0iMTE1IiB5PSI5OCIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTQiIGZpbGw9IiM3MTRiMTIiPmluaXRpYWxpemluZzwvdGV4dD48cmVjdCB4PSIyOTUiIHk9IjcwIiB3aWR0aD0iMTMwIiBoZWlnaHQ9IjQ2IiByeD0iNCIgZmlsbD0iI2VlZjhmMiIgc3Ryb2tlPSIjNGI4YjY1Ii8+PHRleHQgeD0iMzYwIiB5PSI5OCIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTQiIGZpbGw9IiMyNDVjMzkiPmlkbGU8L3RleHQ+PHJlY3QgeD0iNTQwIiB5PSI3MCIgd2lkdGg9IjEzMCIgaGVpZ2h0PSI0NiIgcng9IjQiIGZpbGw9IiNlZWY1ZmEiIHN0cm9rZT0iIzM5NzM5YSIvPjx0ZXh0IHg9IjYwNSIgeT0iOTgiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjE0IiBmaWxsPSIjMjI1NTc2Ij5ydW5uaW5nPC90ZXh0PjxyZWN0IHg9IjUwIiB5PSIyNjAiIHdpZHRoPSIxMzAiIGhlaWdodD0iNDYiIHJ4PSI0IiBmaWxsPSIjZmZmMWYwIiBzdHJva2U9IiNiOTRhNDgiLz48dGV4dCB4PSIxMTUiIHk9IjI4OCIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTQiIGZpbGw9IiM4NDJkMmIiPmZhaWxlZDwvdGV4dD48cmVjdCB4PSIyOTUiIHk9IjI2MCIgd2lkdGg9IjEzMCIgaGVpZ2h0PSI0NiIgcng9IjQiIGZpbGw9IiNmZmY3ZTgiIHN0cm9rZT0iI2I3NzkxZiIvPjx0ZXh0IHg9IjM2MCIgeT0iMjg4IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZmlsbD0iIzcxNGIxMiI+dXBncmFkaW5nPC90ZXh0PjxyZWN0IHg9IjU0MCIgeT0iMjYwIiB3aWR0aD0iMTMwIiBoZWlnaHQ9IjQ2IiByeD0iNCIgZmlsbD0iI2ZmZjdlOCIgc3Ryb2tlPSIjYjc3OTFmIi8+PHRleHQgeD0iNjA1IiB5PSIyODgiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjE0IiBmaWxsPSIjNzE0YjEyIj5yZXNjaGVkdWxpbmc8L3RleHQ+PGxpbmUgeDE9IjE4MCIgeTE9IjkzIiB4Mj0iMjk1IiB5Mj0iOTMiIHN0cm9rZT0iIzQyNWI3MCIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMjM3IiB5PSI4MSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTIiIGZpbGw9IiM1MzZiN2QiPuWIneWni+WMluWujOaIkDwvdGV4dD48bGluZSB4MT0iMTE1IiB5MT0iMTE2IiB4Mj0iMTE1IiB5Mj0iMjYwIiBzdHJva2U9IiM0MjViNzAiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjEyNiIgeT0iMTkyIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMiIgZmlsbD0iIzg0MmQyYiI+5Yid5aeL5YyW5aSx6LSlPC90ZXh0PjxsaW5lIHgxPSI0MjUiIHkxPSI5MyIgeDI9IjU0MCIgeTI9IjkzIiBzdHJva2U9IiM0MjViNzAiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjQ4MiIgeT0iODEiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEyIiBmaWxsPSIjNTM2YjdkIj7mlLbliLDnlKjmiLfkuovku7Y8L3RleHQ+PHBhdGggZD0iTTU0MCAxMTEgQzUwMCAxNzAgNDY1IDE3MCA0MjUgMTExIiBmaWxsPSJub25lIiBzdHJva2U9IiM0MjViNzAiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjQ4MiIgeT0iMTc0IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMiIgZmlsbD0iIzUzNmI3ZCI+5LiA6L2u57uT5p2f5oiW562J5b6F5pON5L2cPC90ZXh0PjxsaW5lIHgxPSIzNDAiIHkxPSIxMTYiIHgyPSIzNDAiIHkyPSIyNjAiIHN0cm9rZT0iIzQyNWI3MCIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzI4IiB5PSIxOTIiIHRleHQtYW5jaG9yPSJlbmQiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEyIiBmaWxsPSIjNTM2YjdkIj7lj5HotbfljYfnuqc8L3RleHQ+PGxpbmUgeDE9IjM4MCIgeTE9IjI2MCIgeDI9IjM4MCIgeTI9IjExNiIgc3Ryb2tlPSIjNDI1YjcwIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSIzOTIiIHk9IjIxMiIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTIiIGZpbGw9IiM1MzZiN2QiPuWujOaIkOaIluWbnua7mjwvdGV4dD48bGluZSB4MT0iNTg1IiB5MT0iMTE2IiB4Mj0iNTg1IiB5Mj0iMjYwIiBzdHJva2U9IiM0MjViNzAiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjU3MyIgeT0iMTkyIiB0ZXh0LWFuY2hvcj0iZW5kIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMiIgZmlsbD0iIzUzNmI3ZCI+5pqC5pe25oCn6ZSZ6K+vPC90ZXh0PjxsaW5lIHgxPSI2MjUiIHkxPSIyNjAiIHgyPSI2MjUiIHkyPSIxMTYiIHN0cm9rZT0iIzQyNWI3MCIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iNjM3IiB5PSIyMTIiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEyIiBmaWxsPSIjNTM2YjdkIj7mgaLlpI3ov5DooYw8L3RleHQ+PGxpbmUgeDE9IjM2MCIgeTE9IjQxMCIgeDI9IjM2MCIgeTI9IjQ1MCIgc3Ryb2tlPSIjNDI1YjcwIiBzdHJva2Utd2lkdGg9IjEuOCIgc3Ryb2tlLWRhc2hhcnJheT0iNSA0IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjM4MCIgeT0iNDM1IiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMiIgZmlsbD0iIzUzNmI3ZCI+5Lu75oSP54q25oCB77ya57uI5q2i5oiW5LiN5Y+v5oGi5aSN6ZSZ6K+vPC90ZXh0PjxyZWN0IHg9IjI5NSIgeT0iNDUwIiB3aWR0aD0iMTMwIiBoZWlnaHQ9IjQ2IiByeD0iNCIgZmlsbD0iI2YxZjNmNSIgc3Ryb2tlPSIjNjY3Nzg1Ii8+PHRleHQgeD0iMzYwIiB5PSI0NzgiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjE0IiBmaWxsPSIjMzM0OTU3Ij50ZXJtaW5hdGVkPC90ZXh0Pjwvc3ZnPgo=" width="720px" />

状态迁移规律：

- `initializing` → `idle`：Session 初始化或资源恢复完成。

- `initializing` → `failed`：Session 初始化或资源恢复失败。

- `idle` → `running`：收到 `user.message` 或 `user.tool_confirmation` 等用户事件。

- `running` → `idle`：Agent 一轮工作结束（`end_turn`）或需要等用户输入（`requires_action`，详情请参见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream)）。

- `running` → `rescheduling` → `running`：框架遇到暂时性错误后自动重试，你无需介入。

- `idle` → `upgrading` → `idle`：服务端受理运行配置升级，升级完成或回滚后恢复为空闲状态。

- 任意状态 → `terminated`：Session 终止。终止后不再接收事件，但记录与事件历史保留。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">Session 资源的状态值是 <code>rescheduling</code>；对应的 SSE 状态事件名是 <code>session.status_rescheduled</code>。调用查询接口或使用 <code>status</code> 参数过滤时，应传 <code>rescheduling</code>，不要传事件名中的 <code>rescheduled</code>。</div>

<span id=".6YCJ5oup6I635Y-W5pa55byP"></span>

## 选择获取方式

Session 详情接口只返回资源的最新状态、用量统计和配置快照。实时回复和历史事件由 Session Events API 承载：

<span aceTableMode="list" aceTableWidth="2,3,3"></span>

| 需要获取的内容               | 使用接口                                   | 返回内容和限制                                                                                                |
| ---------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| 实时接收 Agent 回复和状态    | `GET /sessions/{session_id}/events/stream` | 通过 SSE（Server\-Sent Events）协议订阅会话主线程收到的实时事件。请建立 SSE 并等待 `: ready` 后，再发送事件。 |
| 已持久化的完整事件           | `GET /sessions/{session_id}/events`        | 用于查询会话主线程中已经产生的事件，主要用于历史事件查询和断线补查，返回信息不包含预览帧。                    |
| 最新状态、累计用量和配置快照 | `GET /sessions/{session_id}`               | 查询指定会话的详细信息，包括智能体、会话运行环境、资源、状态和标签等，不返回 Agent 回复或完整事件历史         |

正常交互时，通过 SSE 实时接收 Agent 回复和状态。`GET /sessions/{session_id}/events` 仅用于历史事件查询和断线补查。不要只根据 `status=idle` 判断本轮成功。发送任务、接收回复和判断 `stop_reason` 的完整流程，详情请参见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream)。

<span id=".5qOA57SiLXNlc3Npb24="></span>

## 检索 Session

通过 `GET /sessions/{session_id}` 查询 Session 的详细信息，包括智能体、会话运行环境、资源、状态和标签等：

<Tabs>
<Tab zoneid="thpJHqGlcC" title="Curl">
<TabTitle>Curl</TabTitle>

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno \
  -H "Authorization: Bearer $ARK_API_KEY"
```

</Tab>
</Tabs>

响应主要字段：

- `id`：Session ID。

- `status`：当前状态（见上表）。

- `usage`：累计 token 用量（详情请参见 [Session 事件流 § 跟踪用量](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream)）。

- `agent`：绑定的 Agent 对象，内含 `id`、`version` 等字段。

- `environment_id`：绑定的云环境。

<span id=".5YiX5Ye6LXNlc3Npb24="></span>

## 列出 Session

`GET /sessions` 支持按 `agent_id` 过滤、按创建时间倒序分页，响应以 `data` 数组形式返回 Session 列表：

<Tabs>
<Tab zoneid="SOJ9pCr0TR" title="Curl">
<TabTitle>Curl</TabTitle>

```Bash
curl "https://ark.cn-beijing.volces.com/api/v3/sessions?agent_id=agent-20260701120000-abcde&limit=20" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

</Tab>
</Tabs>

<span id=".5pu05paw5LiO5Y2H57qnLXNlc3Npb24="></span>

## 更新与升级 Session

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 需求                               | 接口                                                                                                | 状态变化                      |
| ---------------------------------- | --------------------------------------------------------------------------------------------------- | ----------------------------- |
| 修改标题或标签                     | [更新 Session 标题和标签](https://ark.volcengine.com/region:cn-beijing/docs/ark/update-session-api) | 不改变 Session 的运行状态     |
| 修改 Agent 或 Environment 运行配置 | [升级会话](https://ark.volcengine.com/region:cn-beijing/docs/ark/upgrade-session-api)               | `idle` → `upgrading` → `idle` |

升级接口保持原 `session_id` 不变，也不支持换绑其他 Agent 或 Environment。发起升级前，Session 必须处于 `idle`，并且上一轮任务以 `end_turn` 结束。接口返回 `upgrading` 后，轮询 `GET /sessions/{session_id}`；状态恢复为 `idle` 表示升级流程已结束。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">不要通过更新会话接口修改 Agent、Environment、<code>tools</code> 或 <code>mcp_servers</code>。该接口只更新标题和标签，运行配置变更必须调用升级会话接口。</div>

<span id=".5Yig6ZmkLXNlc3Npb24="></span>

## 删除 Session

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning"><strong>删除 Session 操作不可逆。</strong> 删除会永久移除 Session 记录、所有事件和关联沙箱。Session 必须处于 <code>idle</code> 或 <code>terminated</code> 状态才能删除，其他状态会返回 <code>InvalidAction</code>。如果 Session 正在 <code>running</code>，先发送 <a href="https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream">中断事件</a>，等待状态回到 <code>idle</code> 后再删除。</div>

<Tabs>
<Tab zoneid="DQQAJZbv0G" title="Curl">
<TabTitle>Curl</TabTitle>

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno \
  -X DELETE \
  -H "Authorization: Bearer $ARK_API_KEY"
```

</Tab>
</Tabs>

<span id="checkpoint-retention"></span>

## 数据保留与删除影响

Session 资源记录、完整事件、沙箱状态和 Agent 产物使用独立的保留策略：

<span aceTableMode="list" aceTableWidth="1,3,3"></span>

| 对象             | 默认保留与恢复规则                                                                                                                        | 删除 Session 后                                              |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Session 资源记录 | 除非显式删除，否则持续保留                                                                                                                | 资源记录不可再查询                                           |
| Session 事件历史 | 每条已持久化的完整事件默认保留 6 个月；`event_start` 和 `event_delta` 预览帧不写入历史                                                    | 该 Session 的事件全部删除                                    |
| 沙箱状态         | Session 处于 `idle` 且尚未终止时，可以恢复文件系统、已安装软件包和沙箱中的临时文件。Session 连续处于 `idle` 达到 14 天后进入 `terminated` | 关联沙箱及其中的临时状态被清理                               |
| Agent 产物       | 方舟默认存储只用于临时保存关联产物；写入自有 TOS Bucket 的对象遵循你配置的 Bucket 生命周期                                                | 默认存储中的关联产物被清理；自有 TOS Bucket 中的对象不受影响 |

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">Agent、Environment、Memory、Vaults、技能，以及通过 Files API 独立上传的输入文件，都是独立资源，不受 Session 删除影响。</div>

<div data-tips="true" data-tips-type="tip">需要长期保留时，在事件到期前将必要记录保存到自己的存储，并将 Agent 产物写入自有 TOS Bucket。产物存储的配置方法，详情请参见 <a href="https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment#configure-output-storage">配置产物存储</a>。</div>

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">延长沙箱状态保留期</div>

<div data-tips="true" data-tips-type="tip">如果需要继续保留沙箱状态，在当前期限到期前发送一条 <code>content</code> 非空的 <code>user.message</code>。Session 完成本轮任务并再次进入 <code>idle</code> 后，平台重新计算 14 天保留期。省略 <code>content</code> 或传入空数组不会刷新保留期。</div>

<a id="doc-2553725"></a>

---

## Session 事件流

> 来源：[https://docs.volcengine.com/docs/82379/2553725?lang=zh](https://docs.volcengine.com/docs/82379/2553725?lang=zh)

与 Managed Agents 的通信基于事件。接入时应固定遵循“建立 SSE 并等待 `: ready` → 发送事件 → 接收完整事件 → 根据 `stop_reason` 判断结果”的顺序。连接中断后，先建立新流，再通过历史事件接口补齐中断窗口。

<span id=".5YeG5aSH5bel5L2c"></span>

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/ark/base-url-and-authentication)。

本地需要安装 `curl` 和 `jq`。

<span id=".5qC45b-D5Lqk5LqS5rWB56iL"></span>

## 核心交互流程

以下流程适用于云端环境和自托管环境中的业务应用：

1. 通过 `GET /sessions/{session_id}/events/stream` 建立 SSE。

2. 等待服务端返回 `: ready`。

3. 通过 `POST /sessions/{session_id}/events` 发送 `user.message`。

4. 持续接收 `agent.*`、`session.*` 和 `span.*` 事件。

5. 仅在收到 `session.status_idle` 且 `stop_reason.type=end_turn` 时，将本轮判定为正常结束。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning"><strong>不要先发送消息再建立 SSE。</strong> SSE 只推送连接建立后产生的事件。先发送消息再建立 SSE，会漏掉连接建立前产生的实时事件，客户端还需要查询历史事件补齐。</div>

正常完成一轮任务时，事件按以下顺序流转：

<img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI3MjAiIGhlaWdodD0iNTI2IiB2aWV3Qm94PSIwIDAgNzIwIDUyNiIgcm9sZT0iaW1nIiBhcmlhLWxhYmVsbGVkYnk9InRpdGxlIGRlc2MiPjx0aXRsZSBpZD0idGl0bGUiPuWujOaIkOS4gOi9riBTZXNzaW9uIOS6pOS6kjwvdGl0bGU+PGRlc2MgaWQ9ImRlc2MiPuS4muWKoeW6lOeUqOWFiOW7uueriyBTU0Ug5bm2562J5b6FIHJlYWR577yM5YaN5Y+R6YCB55So5oi35raI5oGv77yM5pyA5ZCO5LulIHNlc3Npb24uc3RhdHVzX2lkbGUg5LiUIHN0b3BfcmVhc29uLnR5cGUg562J5LqOIGVuZF90dXJuIOWIpOaWreacrOi9ruato+W4uOe7k+adnzwvZGVzYz48ZGVmcz48bWFya2VyIGlkPSJhcnJvdyIgbWFya2VyV2lkdGg9IjgiIG1hcmtlckhlaWdodD0iOCIgcmVmWD0iNyIgcmVmWT0iNCIgb3JpZW50PSJhdXRvIiBtYXJrZXJVbml0cz0ic3Ryb2tlV2lkdGgiPjxwYXRoIGQ9Ik0wLDAgTDgsNCBMMCw4IFoiIGZpbGw9IiMzMTUzNmYiLz48L21hcmtlcj48L2RlZnM+PHJlY3Qgd2lkdGg9IjcyMCIgaGVpZ2h0PSI1MjYiIGZpbGw9IiNmZmYiLz48cmVjdCB4PSIyNiIgeT0iMTIiIHdpZHRoPSIxNDgiIGhlaWdodD0iNDAiIHJ4PSI0IiBmaWxsPSIjZjRmN2Y5IiBzdHJva2U9IiM5MWE0YjMiLz48dGV4dCB4PSIxMDAiIHk9IjM3IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNSIgZmlsbD0iIzE3MmIzYSI+5Lia5Yqh5bqU55SoPC90ZXh0PjxsaW5lIHgxPSIxMDAiIHkxPSI1MiIgeDI9IjEwMCIgeTI9IjUwOCIgc3Ryb2tlPSIjYWFiN2MyIiBzdHJva2Utd2lkdGg9IjEiIHN0cm9rZS1kYXNoYXJyYXk9IjQgNSIvPjxyZWN0IHg9IjU0NiIgeT0iMTIiIHdpZHRoPSIxNDgiIGhlaWdodD0iNDAiIHJ4PSI0IiBmaWxsPSIjZjRmN2Y5IiBzdHJva2U9IiM5MWE0YjMiLz48dGV4dCB4PSI2MjAiIHk9IjM3IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNSIgZmlsbD0iIzE3MmIzYSI+5pa56Iif5pyN5Yqh56uvPC90ZXh0PjxsaW5lIHgxPSI2MjAiIHkxPSI1MiIgeDI9IjYyMCIgeTI9IjUwOCIgc3Ryb2tlPSIjYWFiN2MyIiBzdHJva2Utd2lkdGg9IjEiIHN0cm9rZS1kYXNoYXJyYXk9IjQgNSIvPjx0ZXh0IHg9IjM2MCIgeT0iODgiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4xLiDlu7rnq4sgU1NFIOi/nuaOpeW5tuetieW+hSA6IHJlYWR5PC90ZXh0PjxsaW5lIHgxPSIxMDgiIHkxPSI5OSIgeDI9IjYxMiIgeTI9Ijk5IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjM2MCIgeT0iMTU0IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+Mi4gdXNlci5tZXNzYWdl77yI5Y+v5ZCM5om56L+95YqgIHN5c3RlbS5tZXNzYWdl77yJPC90ZXh0PjxsaW5lIHgxPSIxMDgiIHkxPSIxNjUiIHgyPSI2MTIiIHkyPSIxNjUiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSIyMjAiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj4zLiBzZXNzaW9uLnN0YXR1c19ydW5uaW5nPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSIyMzEiIHgyPSIxMDgiIHkyPSIyMzEiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSIyODYiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ0NjVmIj40LiBzcGFuLiogLyBhZ2VudC50aGlua2luZ++8iOaMieS7u+WKoeS6p+eUn++8iTwvdGV4dD48bGluZSB4MT0iNjEyIiB5MT0iMjk3IiB4Mj0iMTA4IiB5Mj0iMjk3IiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjx0ZXh0IHg9IjM2MCIgeT0iMzUyIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+NS4gYWdlbnQubWVzc2FnZe+8iOWujOaVtOWbnuWkje+8iTwvdGV4dD48bGluZSB4MT0iNjEyIiB5MT0iMzYzIiB4Mj0iMTA4IiB5Mj0iMzYzIiBzdHJva2U9IiMzMTUzNmYiIHN0cm9rZS13aWR0aD0iMS42IiBtYXJrZXItZW5kPSJ1cmwoI2Fycm93KSIvPjxyZWN0IHg9IjIxNCIgeT0iMzk5IiB3aWR0aD0iMjkyIiBoZWlnaHQ9IjYyIiByeD0iNCIgZmlsbD0iI2VlZjhmMiIgc3Ryb2tlPSIjNGI4YjY1Ii8+PHRleHQgeD0iMzYwIiB5PSI0MjIiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ1YzM5Ij42LiBzZXNzaW9uLnN0YXR1c19pZGxlPC90ZXh0Pjx0ZXh0IHg9IjM2MCIgeT0iNDQ1IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NWMzOSI+c3RvcF9yZWFzb24udHlwZSA9IGVuZF90dXJuPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSI0NzIiIHgyPSIxMDgiIHkyPSI0NzIiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PC9zdmc+Cg==" width="720px" />

- 客户端先建立 SSE 并等待 `: ready`，再发送 `user.message`，避免漏掉连接建立前已经产生的事件。

- `session.status_running` 表示 Agent 已开始处理任务；`span.*` 和 `agent.thinking` 按任务执行过程产生，完整回复由 `agent.message` 返回。

- 收到 `agent.message` 不代表本轮结束。客户端继续读取事件，只有 `session.status_idle` 且 `stop_reason.type=end_turn` 表示正常结束。

如需直接复用完整实现，请参见 [运行完整示例](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream#complete-example)。

<span id=".55CG6Kej5LqL5Lu26YCa5L-h"></span>

## 理解事件通信

<span id=".5o6l5Y-j6IGM6LSj"></span>

### 接口职责

根据不同用途选择对应接口：

<span aceTableMode="list" aceTableWidth="2,3,3"></span>

| 接口                                       | 客户端动作                                         | 返回内容和限制                                               |
| ------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------------ |
| `GET /sessions/{session_id}/events/stream` | 建立 SSE 并等待 `: ready`，实时接收事件            | 只推送连接建立后产生的事件，不回放历史事件                   |
| `POST /sessions/{session_id}/events`       | 发送 `user.*` 或 `system.message` 事件             | HTTP 200 只表示事件已受理，不表示 Agent 已处理或本轮已结束   |
| `GET /sessions/{session_id}/events`        | 分页查询已持久化的完整事件，用于历史查看和断线补查 | 不包含 `event_start`、`event_delta` 预览帧，也不提供实时推送 |
| `GET /sessions/{session_id}`               | 查询 Session 最新状态、累计用量和配置快照          | 不返回完整事件历史或 Agent 回复内容                          |

<span id=".5LqL5Lu25qih5Z6L"></span>

### 事件模型

事件类型遵循 `{domain}.{action}` 命名约定。先根据事件域判断方向和职责，再根据完整的 `type` 读取对应结构。

<span aceTableMode="list" aceTableWidth="1,1,3"></span>

| 事件域                     | 方向             | 说明                                                      |
| -------------------------- | ---------------- | --------------------------------------------------------- |
| `user.*`、`system.message` | 客户端 → Session | 提交用户消息、动态系统提示词、中断、工具确认或工具结果    |
| `agent.*`                  | Session → 客户端 | 返回消息、思考过程、工具调用和主子线程通信事件            |
| `session.*`                | Session → 客户端 | 返回 Session 或子线程的运行、等待、重调度、终止和错误状态 |
| `span.*`                   | Session → 客户端 | 返回模型调用的过程和结果                                  |

每个完整事件都包含 `id`、`type` 和 `processed_at`。`processed_at` 为 `null` 时，表示事件已进入队列但尚未完成处理。完整事件类型、字段、取值和嵌套结构，详情请参见 [会话事件结构参考](https://ark.volcengine.com/region:cn-beijing/docs/ark/sessions-events-reference-v2)。

<span id=".6L-Q6KGM546v5aKD5beu5byC"></span>

### 运行环境差异

两种环境中的业务应用共用 Session Events API。差异主要在内置工具的执行责任：

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 对比项         | 云端环境                            | 自托管环境                                             |
| -------------- | ----------------------------------- | ------------------------------------------------------ |
| Session 事件   | 业务应用建立 Session SSE 并发送事件 | 业务应用建立 Session SSE 并发送事件                    |
| 内置工具执行方 | 方舟服务端和云端执行环境            | 自托管 Worker 和企业 Sandbox                           |
| 额外链路       | 无                                  | Worker Poll/Ack Work、建立 Worker SSE 并维持 Heartbeat |
| 工具结果       | 方舟完成执行并推送结果              | Worker 回传结果后，方舟继续运行 Session                |

自托管环境中，业务应用不直接调用 Work API。Worker 的部署与运行机制，详情请参见 [配置并运行自托管环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-self-hosted-environment)。

<span id="send-message"></span>

## 发送事件

客户端通过 `POST /sessions/{session_id}/events` 向 Session 发送 `user.message` 事件，`content` 是一个块数组，可混合文本（`text`）、图片（`image`）、文档（`document`）三类块。

- 图片 `image` 的 `source.type` 支持三种：`base64`（内联 base64，需带 `media_type`）、`url`（公网可访问 URL）、`file`（已上传文件 ID，字段为 `file_id`）。

- 文档 `document` 的 `source.type` 支持四种：`file`（已上传文件 ID）、`url`（公网可访问 URL）、`base64`（内联 base64，需带 `media_type`）、`text`（直接内联纯文本，`media_type: text/plain` + `data` 字段）；可选 `title` 与 `context` 字段为文档附加标题与背景说明。

- 同一请求的 `content` 数组可混合多个图片或文档块。

- 如需为当前轮次动态追加系统提示词，可在 `events` 数组里 `user.message` 后紧跟一个 `system.message`，详情请参见 [动态系统提示词](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream#dynamic-system-prompt)。

<span id=".5bi46KeB5YaF5a6557uE5ZCI56S65L6L"></span>

### 常见内容组合示例

<Tabs>
<Tab zoneid="DjEtTO2DxJ" title="纯文本">
<TabTitle>纯文本</TabTitle>

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

</Tab>
<Tab zoneid="Pt5ENY8CSR" title="文本 + 图片（URL）">
<TabTitle>文本 + 图片（URL）</TabTitle>

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

</Tab>
<Tab zoneid="OGWEtTXDDp" title="文本 + 图片（base64）">
<TabTitle>文本 + 图片（base64）</TabTitle>

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

</Tab>
<Tab zoneid="bpvTXiUdis" title="文本 + 图片（file_id）">
<TabTitle>文本 + 图片（file_id）</TabTitle>

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

</Tab>
<Tab zoneid="FlIwKBZl60" title="文本 + 文档（file_id）">
<TabTitle>文本 + 文档（file_id）</TabTitle>

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

</Tab>
<Tab zoneid="Id48DPzyim" title="文本 + 文档（内联纯文本）">
<TabTitle>文本 + 文档（内联纯文本）</TabTitle>

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

</Tab>
<Tab zoneid="gJkBokkSdy" title="文本 + 文档（base64 PDF）">
<TabTitle>文本 + 文档（base64 PDF）</TabTitle>

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

</Tab>
<Tab zoneid="wNq9wTjaVk" title="文本 + 文档（URL）">
<TabTitle>文本 + 文档（URL）</TabTitle>

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

</Tab>
<Tab zoneid="JnspumT6g7" title="文本 + 多附件混合">
<TabTitle>文本 + 多附件混合</TabTitle>

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

</Tab>
</Tabs>

请求体的 `events` 字段是事件数组，服务端按入队顺序串行处理。

<span id=".6L-Q6KGM5Lit57un57ut5Y-R6YCB5raI5oGv"></span>

### 运行中继续发送消息

Session 处于 `running` 状态时，你仍可以继续发送 `user.message`。服务端会先接收事件并写入待处理队列，等当前 Agent 执行到可调度边界后再把队列中的消息送入后续模型请求。常见边界包括当前模型请求结束、工具执行结果返回、当前回合结束等。

这种设计适合连续对话、实时追加约束、长任务中补充材料等场景。客户端不需要为了发送下一条消息轮询等待 `idle`，但仍需要通过 SSE 观察事件处理进度：

- HTTP 响应成功只表示事件已被服务端接收，不表示 Agent 已经处理完这条消息。

- 返回事件的 `processed_at` 为 `null` 时，表示该事件仍在队列中，等待前序事件处理完成。

- 多条排队消息可能在后续一次模型请求中合并处理，Agent 不一定为每条消息生成一条独立回复。

- 如果待处理队列已满，接口会返回 HTTP 409。此时不要快速重试，应等待 Agent 消费队列，或按业务需要发送 `user.interrupt` 中断当前执行。

<span id="dynamic-system-prompt"></span>

### 动态系统提示词

除了在 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent) 时配置的固定系统提示词，客户端还可以在发送 `user.message` 的同一请求末尾追加一个 `system.message`，为当前轮次动态注入额外指令；固定系统提示词、运行时 `system.message` 与用户消息拼接后一起送入模型。

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 对比项 | Agent 定义的系统提示词                           | 运行时 `system.message` 事件              |
| ------ | ------------------------------------------------ | ----------------------------------------- |
| 位置   | 创建 Agent 时设置                                | 发 Events 时传入                          |
| 生效   | 创建 Session 时拼入，整个 Session 生命周期内固定 | 每次发送后追加到 Session 的系统角色上下文 |

同一请求需要区分 API 提交顺序和模型上下文顺序：

<span aceTableMode="list" aceTableWidth="1,2,3"></span>

| 阶段                       | 顺序                                                                                   | 说明                                                                      |
| -------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| API 请求中的 `events` 数组 | `user.message` → `system.message`                                                      | `system.message` 必须紧跟在 `user.message` 后面，并且是数组的最后一个元素 |
| 进入模型前的上下文         | Agent 系统提示词 → 历史 `system.message` → 本轮 `system.message` → 本轮 `user.message` | 服务端先将本轮 `system.message` 追加到系统角色上下文，再发起本轮模型调用  |

模型上下文实际展开如下，省略普通会话历史：

- 首轮：`[Agent 系统提示词] [首轮 system.message] [首轮 user.message]`。

- 次轮：`[Agent 系统提示词] [首轮 system.message] [次轮 system.message] [次轮 user.message]`。

`system.message` 按追加（append\-only）语义写入 Session 的系统角色上下文，不支持替换或清空。后续每次模型调用都会携带该 Session 内全部历史 `system.message` 内容。需要重置系统提示词时，必须新建 Session。

单独发送 `system.message`、未紧跟 `user.message` 或未放在 `events` 数组末尾，都会返回 HTTP 400 `InvalidPayload`。

<Tabs>
<Tab zoneid="jrbaWCcQP3" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

请求成功返回 HTTP 200，响应 `data` 数组按入参顺序包含服务端已受理的事件。

<span id=".5o6l5pS25bm25aSE55CG5LqL5Lu2"></span>

## 接收并处理事件

业务应用通过已经建立的 SSE 连接接收完整事件，并根据 `type` 更新状态、展示回复或进入对应分支。`agent.message`、`agent.thinking` 和 `agent.tool_result` 等完整事件会写入事件历史；客户端应记录事件 ID，用于断线补查时去重。

<span id=".5bGV56S66aKE6KeI5YaF5a65"></span>

### 展示预览内容

需要在完整消息生成前更新界面时，通过 `event_deltas` 指定要预览的事件类型。当前支持 `agent.message` 和 `agent.thinking`：

<img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI3MjAiIGhlaWdodD0iMzk0IiB2aWV3Qm94PSIwIDAgNzIwIDM5NCIgcm9sZT0iaW1nIiBhcmlhLWxhYmVsbGVkYnk9InRpdGxlIGRlc2MiPjx0aXRsZSBpZD0idGl0bGUiPumihOiniOW4p+WIsOWujOaVtOS6i+S7tjwvdGl0bGU+PGRlc2MgaWQ9ImRlc2MiPuacjeWKoeerr+WFiOWPkemAgSBldmVudF9zdGFydO+8jOWGjeWPkemAgeS4gOasoeaIluWkmuasoSBldmVudF9kZWx0Ye+8jOacgOWQjuWPkemAgeWujOaVtOeahCBhZ2VudC5tZXNzYWdlIOaIliBhZ2VudC50aGlua2luZyDkuovku7Y8L2Rlc2M+PGRlZnM+PG1hcmtlciBpZD0iYXJyb3ciIG1hcmtlcldpZHRoPSI4IiBtYXJrZXJIZWlnaHQ9IjgiIHJlZlg9IjciIHJlZlk9IjQiIG9yaWVudD0iYXV0byIgbWFya2VyVW5pdHM9InN0cm9rZVdpZHRoIj48cGF0aCBkPSJNMCwwIEw4LDQgTDAsOCBaIiBmaWxsPSIjMzE1MzZmIi8+PC9tYXJrZXI+PC9kZWZzPjxyZWN0IHdpZHRoPSI3MjAiIGhlaWdodD0iMzk0IiBmaWxsPSIjZmZmIi8+PHJlY3QgeD0iMjYiIHk9IjEyIiB3aWR0aD0iMTQ4IiBoZWlnaHQ9IjQwIiByeD0iNCIgZmlsbD0iI2Y0ZjdmOSIgc3Ryb2tlPSIjOTFhNGIzIi8+PHRleHQgeD0iMTAwIiB5PSIzNyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTUiIGZpbGw9IiMxNzJiM2EiPuS4muWKoeW6lOeUqDwvdGV4dD48bGluZSB4MT0iMTAwIiB5MT0iNTIiIHgyPSIxMDAiIHkyPSIzNzYiIHN0cm9rZT0iI2FhYjdjMiIgc3Ryb2tlLXdpZHRoPSIxIiBzdHJva2UtZGFzaGFycmF5PSI0IDUiLz48cmVjdCB4PSI1NDYiIHk9IjEyIiB3aWR0aD0iMTQ4IiBoZWlnaHQ9IjQwIiByeD0iNCIgZmlsbD0iI2Y0ZjdmOSIgc3Ryb2tlPSIjOTFhNGIzIi8+PHRleHQgeD0iNjIwIiB5PSIzNyIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTUiIGZpbGw9IiMxNzJiM2EiPuaWueiIn+acjeWKoeerrzwvdGV4dD48bGluZSB4MT0iNjIwIiB5MT0iNTIiIHgyPSI2MjAiIHkyPSIzNzYiIHN0cm9rZT0iI2FhYjdjMiIgc3Ryb2tlLXdpZHRoPSIxIiBzdHJva2UtZGFzaGFycmF5PSI0IDUiLz48dGV4dCB4PSIzNjAiIHk9Ijk1IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+MS4g5bu656uLIFNTRSDlubborqLpmIXlm57lpI3pooTop4g8L3RleHQ+PGxpbmUgeDE9IjEwOCIgeTE9IjEwNiIgeDI9IjYxMiIgeTI9IjEwNiIgc3Ryb2tlPSIjMzE1MzZmIiBzdHJva2Utd2lkdGg9IjEuNiIgbWFya2VyLWVuZD0idXJsKCNhcnJvdykiLz48dGV4dCB4PSIzNjAiIHk9IjE2MSIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTMiIGZpbGw9IiMyNDQ2NWYiPjIuIGV2ZW50X3N0YXJ077yI57uZ5Ye65a6M5pW05LqL5Lu2IElE77yJPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSIxNzIiIHgyPSIxMDgiIHkyPSIxNzIiIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHJlY3QgeD0iMTkyIiB5PSIxOTkiIHdpZHRoPSIzMzYiIGhlaWdodD0iNTciIHJ4PSI0IiBmaWxsPSIjZjZmOGZhIiBzdHJva2U9IiM5YWE5YjUiIHN0cm9rZS1kYXNoYXJyYXk9IjQgNCIvPjx0ZXh0IHg9IjM2MCIgeT0iMjIwIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsU2Vnb2UgVUksc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxMyIgZmlsbD0iIzI0NDY1ZiI+My4gZXZlbnRfZGVsdGEgw5cgTjwvdGV4dD48dGV4dCB4PSIzNjAiIHk9IjI0MiIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1mYW1pbHk9Ii1hcHBsZS1zeXN0ZW0sQmxpbmtNYWNTeXN0ZW1Gb250LFNlZ29lIFVJLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTIiIGZpbGw9IiM1MzZiN2QiPuaMiSBldmVudF9pZCDlkozlhoXlrrnlnZcgaW5kZXgg57Sv56evPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSIyNjciIHgyPSIxMDgiIHkyPSIyNjciIHN0cm9rZT0iIzMxNTM2ZiIgc3Ryb2tlLXdpZHRoPSIxLjYiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PHRleHQgeD0iMzYwIiB5PSIzMjIiIHRleHQtYW5jaG9yPSJtaWRkbGUiIGZvbnQtZmFtaWx5PSItYXBwbGUtc3lzdGVtLEJsaW5rTWFjU3lzdGVtRm9udCxTZWdvZSBVSSxzYW5zLXNlcmlmIiBmb250LXNpemU9IjEzIiBmaWxsPSIjMjQ1YzM5Ij40LiDlrozmlbQgYWdlbnQubWVzc2FnZSAvIGFnZW50LnRoaW5raW5nPC90ZXh0PjxsaW5lIHgxPSI2MTIiIHkxPSIzMzMiIHgyPSIxMDgiIHkyPSIzMzMiIHN0cm9rZT0iIzRiOGI2NSIgc3Ryb2tlLXdpZHRoPSIxLjgiIG1hcmtlci1lbmQ9InVybCgjYXJyb3cpIi8+PC9zdmc+Cg==" width="720px" />

- `event_start`：标识一个预览事件开始，并给出完整事件将使用的 ID。

- `event_delta`：按 `event_id` 和内容块 `index` 追加增量内容，同一事件可以返回多次。

- `agent.message` 或 `agent.thinking`：返回最终完整内容。

预览帧不写入事件历史，也可能在连接中断时不完整。界面可以使用预览帧实时更新，持久化和业务判断应以完整事件为准。参数写法和响应格式，详情请参见 [流式获取会话事件](https://ark.volcengine.com/region:cn-beijing/docs/ark/stream-events-api)。

<span id=".5Yik5pat5pys6L2u57uT5p6c"></span>

### 判断本轮结果

不要使用 HTTP 响应成功、`agent.message` 或 SSE 连接关闭作为本轮结束条件。客户端应继续读取状态事件，并根据以下结果处理：

<span aceTableMode="list" aceTableWidth="2,2,4"></span>

| 状态事件                     | 停止原因            | 客户端动作                                            |
| ---------------------------- | ------------------- | ----------------------------------------------------- |
| `session.status_idle`        | `end_turn`          | 本轮正常结束，可以发送下一条消息                      |
| `session.status_idle`        | `requires_action`   | 本轮尚未结束，根据 `event_ids` 回传工具确认或工具结果 |
| `session.status_idle`        | `retries_exhausted` | 当前轮次重试次数已用尽；本轮结束，但仍可发送新的输入  |
| `session.status_rescheduled` | 不适用              | 方舟正在自动恢复，继续监听，不要重复发送原事件        |
| `session.status_terminated`  | 不适用              | Session 已进入终态，停止发送事件                      |

收到 `session.error` 时，还需要检查 `error.retry_status.type`：

- `retrying`：服务端正在自动重试，继续监听。

- `exhausted`：当前轮次重试耗尽，等待对应的 `session.status_idle`。

- `terminal`：Session 将进入终止状态，停止发送新事件。

完整状态迁移，详情请参见 [管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/ark/manage-session)。

<span id="confirm-tool-use"></span>

### 处理工具确认

当工具权限策略要求执行前确认时，按以下步骤解除 `requires_action`：

1. 接收 `agent.tool_use` 或 `agent.mcp_tool_use` 事件。

2. 接收 `session.status_idle`，确认 `stop_reason.type=requires_action`。

3. 遍历 `stop_reason.event_ids`，为每个待处理事件发送一条 `user.tool_confirmation`。将事件 ID 原样填入 `tool_use_id`，并将 `result` 设置为 `allow` 或 `deny`。

4. 所有待确认事件处理完成后，Session 返回 `session.status_running` 并继续当前任务。

拒绝工具调用时，可以通过 `deny_message` 返回具体原因。Agent 会据此选择其他工具、降级处理，或说明无法继续执行。此阶段不要发送新的 `user.message` 替代确认。

工具权限策略的配置方法，详情请参见 [工具权限策略](https://ark.volcengine.com/region:cn-beijing/docs/ark/tool-permission-policy)。

<span id=".5pat57q_6YeN6L-e5LiO5Y6G5Y-y6KGl5p-l"></span>

## 断线重连与历史补查

SSE 接口不支持从断点自动续传。要补齐中断期间产生的事件，按以下顺序处理：

1. 调用 `GET /sessions/{session_id}/events/stream` 打开新的事件流，并等待 `: ready`。

2. 调用 `GET /sessions/{session_id}/events` 查询历史事件。首次请求省略 `page`。

3. 响应包含 `next_page` 时，将该值原样作为下一次请求的 `page`，直到不再返回 `next_page`。

4. 按事件 ID 合并历史事件和新事件流中的实时事件，跳过已经处理的事件。

必须先建立新流再查询历史。如果先查历史再重连，历史查询结束到新流建立之间仍会留下新的监听空窗。本页的 [完整示例](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream#complete-example) 实现了该顺序和事件去重。

<span id="complete-example"></span>

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

&nbsp;

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

<span aceTableMode="list" aceTableWidth="1,2,3"></span>

| 状态码 | 结果                                                 | 处理方式                                 |
| ------ | ---------------------------------------------------- | ---------------------------------------- |
| `0`    | `end_turn`，且已收到 `agent.message`                 | 本轮正常结束                             |
| `2`    | `requires_action`                                    | 根据输出的事件 ID 回传工具确认或工具结果 |
| `3`    | `retries_exhausted`                                  | 当前轮次失败，可以修正输入后重新发送     |
| `4`    | Session 终止或发生不可恢复错误                       | 停止向当前 Session 发送事件              |
| `5`    | 状态事件缺少有效停止原因，或正常结束前未收到完整回复 | 查询历史事件和错误信息后再决定是否重试   |

<span id=".5Lit5pat5ZKM57un57ut5L2_55SoLXNlc3Npb24="></span>

## 中断和继续使用 Session

<span id=".5Lit5patLXNlc3Npb24="></span>

### 中断 Session

客户端发送 `user.interrupt` 事件可中断 Agent 当前执行。中断会清空尚未被调度的待处理消息，避免旧消息在中断后继续触发模型调用。

如果你只是补充当前任务的信息，直接继续发送 `user.message` 即可，消息会按队列顺序处理；如果你要放弃当前执行并切换到新任务，先发送 `user.interrupt`，等 Session 回到 `idle` 后再发送新的 `user.message`：

<Tabs>
<Tab zoneid="Ezlv4tnUpH" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

<span id=".57un57ut5L2_55So56m66ZeyLXNlc3Npb24="></span>

### 继续使用空闲 Session

在 Session 仍处于 `idle` 且尚未终止时，按 [发送消息](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream#send-message) 流程发送包含真实任务内容的 `user.message`，Session 会从 `idle` 变为 `running`。

如果需要继续保留沙箱状态，在当前期限到期前发送一条 `content` 非空的 `user.message`。Session 完成本轮任务并再次进入 `idle` 后，平台重新计算 14 天保留期。省略 `content` 或传入空数组不会刷新保留期。

需要长期保留事件和 Agent 产物时，将必要数据保存到自己的存储。保留与删除影响，详情请参见 [数据保留与删除影响](https://ark.volcengine.com/region:cn-beijing/docs/ark/manage-session#checkpoint-retention)。

<span id=".6KeC5rWL5LiO5o6S5p-l"></span>

## 观测与排查

<span id=".6Lef6Liq55So6YeP"></span>

### 跟踪用量

客户端通过 `span.model_request_end` 事件获取本次模型请求的 token 用量。事件携带 `model_usage` 字段，记录本次请求的用量明细：

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

字段含义：

- `input_tokens`：未缓存输入 token。

- `output_tokens`：全部输出 token。

- `cache_creation_input_tokens`：本次请求写入提示词缓存的输入 token 数。

- `cache_read_input_tokens`：本次请求从提示词缓存读取的输入 token 数。

- `speed`：本次请求速度档位。

如果需要通过事件流实时统计 Session 级累计用量，客户端需要聚合 `span.model_request_end` 事件的 `model_usage`。如果只需查询服务端记录的最新累计用量，调用 `GET /sessions/{session_id}` 读取 `usage`。

<span id=".5o6n5Yi25Y-w5Y-v6KeC5rWL5oCn"></span>

### 控制台可观测性

控制台 [Managed Agents](https://ark.volcengine.com/region:cn-beijing/managedAgents) 提供 Session 的可视化时间线视图：

- Session 列表：全部 Session 及其状态、创建时间、模型。

- 追踪视图：Session 内事件按时间排序展示，仅对开发者与管理员可见。

- 工具执行：每次工具调用及其结果。

<span id=".6LCD6K-V5oqA5ben"></span>

### 调试技巧

- 关注 `session.error`：根据 `error.retry_status.type` 判断服务端会重试、结束当前轮次还是终止 Session。

- 检查工具结果：失败的工具调用通常解释了 Agent 异常行为的原因。

- 跟踪 token 用量：监控消耗以优化提示并降低成本。

<span id=".55u45YWz5paH5qGj"></span>

## 相关文档

- 配置内置工具、MCP 工具和自定义工具，详情请参见 [Tools](https://ark.volcengine.com/region:cn-beijing/docs/ark/tools)。

- 观察主线程与子线程之间的任务委派，详情请参见 [编排 Multi Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/multi-agent)。

- 查询、更新、升级或删除 Session，详情请参见 [管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/ark/manage-session)。

<a id="doc-2553726"></a>

---

## 使用 Vaults 认证

> 来源：[https://docs.volcengine.com/docs/82379/2553726?lang=zh](https://docs.volcengine.com/docs/82379/2553726?lang=zh)

当你使用 Managed Agents 构建需要访问第三方服务的应用时，可以通过 Vaults（凭据保管库）保存并隔离每个应用用户的凭据。你的服务端只需为每个用户创建 Vault、写入 Credential，并保存用户与 `vault_id` 的映射，无需自行维护密钥存储，也无需在每次调用中重复传递 token。

创建 Session 时，通过 `vault_ids` 传入当前用户的 Vault ID；Agent 调用 MCP 服务时会自动使用对应的 Credential 完成鉴权。这样，同一个 Agent 可以安全地服务多个用户，并分别访问各自有权限的第三方资源。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">示例</div>

<div data-tips="true" data-tips-type="tip">你使用 Managed Agents 构建了一个 GitHub 代码助手。Alice 发起任务时，服务端创建 Session 并绑定 Alice 的 Vault，Agent 使用 Alice 的 GitHub 权限；Bob 发起任务时，服务端绑定 Bob 的 Vault，Agent 使用 Bob 的 GitHub 权限。</div>

<span id=".5YeG5aSH5bel5L2c"></span>

## 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/ark/base-url-and-authentication)。

<span id=".5Yib5bu6LXZhdWx0cw=="></span>

## 创建 Vaults

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning"><strong>作用域。</strong> Vaults 与凭据按工作空间隔离，同工作空间的 API Key 都能引用。要撤销访问，删除对应 Vaults 或凭据。</div>

Vaults 是绑定到某个终端用户的凭据集合。给它一个 `display_name`，可选用 `metadata` 标记以便映射回你自己的用户记录：

<Tabs>
<Tab zoneid="OtNL8vELCf" title="Curl">
<TabTitle>Curl</TabTitle>

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/vaults \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Alice",
    "metadata": {"external_user_id": "usr_abc123"}
  }'
```

</Tab>
</Tabs>

响应是完整的 Vaults 记录：

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

<span id=".5re75Yqg5Yet5o2u"></span>

## 添加凭据

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 类型                   | 适用场景                                                           | 注入方式                                                           |
| ---------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------ |
| `mcp_oauth`            | MCP 服务器用 OAuth 2.0                                             | 平台代刷 token，Session 连接 MCP URL 时自动注入                    |
| `static_bearer`        | MCP 用固定 Bearer token（API Key、个人访问令牌）                   | 无刷新流程，直接注入                                               |
| `mcp_static`           | MCP 使用固定密钥，需要以 Bearer、自定义 Header 或 URL 查询参数注入 | 通过 `injection_type` 选择注入位置                                 |
| `environment_variable` | 通过环境变量鉴权的命令行、SDK、直接 API 调用                       | 沙箱内是不透明占位符，**出口处**替换为真实值，Agent 永远看不到密钥 |
| `env_oauth`            | 使用 OAuth 访问令牌，并通过环境变量向运行环境注入                  | 按 `secret_name` 注入，作用范围由 `networking` 限制                |

你提供的实际密钥（`token`、`access_token`、`refresh_token`、`client_secret`、`secret_value`）是敏感的**只写**字段，API 响应不会返回这些字段。

<span id=".bWNwLW9hdXRoLeWHreaNrg=="></span>

### MCP OAuth 凭据

当 MCP 服务器使用 OAuth 2.0 时，用 `mcp_oauth`。提供 `refresh` 块后，平台会在 access token 过期时代你刷新。

`refresh.token_endpoint_auth.type` 三选一：

- `none`：公共客户端。

- `client_secret_basic`：使用 `client_secret` 的 HTTP Basic 鉴权。

- `client_secret_post`：把 `client_secret` 放在 POST 请求体里。

<Tabs>
<Tab zoneid="VRZlKQf6x9" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

<span id=".bWNwLemdmeaAgS1iZWFyZXIt5Yet5o2u"></span>

### MCP 静态 Bearer 凭据

当 MCP 服务器接受固定 Bearer token（API Key、个人访问令牌） 时，用 `static_bearer`。无需刷新流程：

<Tabs>
<Tab zoneid="qJ9IkqTeZn" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

<span id=".bWNwLemdmeaAgeWHreaNrg=="></span>

### MCP 静态凭据

当 MCP 服务器使用固定密钥，但不一定采用 Bearer 鉴权时，使用 `mcp_static`。通过 `injection_type` 选择注入方式：

- `bearer`：以 `Authorization: Bearer <token>` 请求头注入。不要传 `injection_name`，`token` 中也不要包含 `Bearer` 前缀。

- `header`：以自定义请求头注入，必须通过 `injection_name` 指定请求头名称。

- `query`：以 URL 查询参数注入，必须通过 `injection_name` 指定参数名。

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

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip"><strong>最小权限原则。</strong> 把固定密钥的权限范围限定为 Agent 所需的最小集合。Agent 可以执行该密钥允许的任何操作，过大的权限范围会在 Agent 异常时扩大影响。</div>

<span id=".546v5aKD5Y-Y6YeP5Yet5o2u"></span>

### 环境变量凭据

用 `environment_variable` 通过环境变量对外部服务鉴权，适用于命令行、SDK 或直接 API 调用。

`networking.allowed_hosts` 控制密钥可以被替换到哪些出站主机：

- `"type": "limited"` + 显式主机列表（**推荐**）。

- `"type": "unrestricted"`（仅当调用方访问的域名无法提前枚举时使用）。

<Tabs>
<Tab zoneid="cQmhHlhw6i" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip"><strong>替换仅发生在出站方向。</strong> 如果客户端使用存储的密钥交换临时访问令牌，返回的令牌会以未脱敏形式进入运行环境。此类流程应由业务侧管理令牌交换和存储。</div>

<span id=".546v5aKDLW9hdXRoLeWHreaNrg=="></span>

### 环境 OAuth 凭据

当命令行或 SDK 需要从环境变量读取 OAuth 访问令牌时，使用 `env_oauth`。创建时提供 `access_token`、`secret_name` 和 `networking`；访问令牌仍在沙箱出口处注入，不会直接暴露给 Agent。

访问令牌过期后，调用更新凭据接口写入新的 `access_token`。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

- <div data-tips="true" data-tips-type="warning"><strong>替换发生在沙箱出口处，不在沙箱内。</strong> 沙箱里的进程看到的是不透明占位符，而不是真实值。具体影响如下：</div>
  - <div data-tips="true" data-tips-type="warning">启动时校验凭据格式的客户端可能拒绝占位符。</div>

  - <div data-tips="true" data-tips-type="warning">用密钥做请求签名的客户端（例如 AWS SigV4） 会生成无效签名。</div>

  - <div data-tips="true" data-tips-type="warning">环境变量凭据<strong>只适合「把密钥值原样塞进出站请求头」的客户端</strong>。</div>

- <div data-tips="true" data-tips-type="warning"><strong>通过 Credential 限制凭据注入目标。</strong> 在 Credential 的 <code>networking</code> 中设置 <code>type: limited</code>，并通过 <code>allowed_hosts</code> 显式列出允许注入的出站主机。</div>

<span id=".5Yet5o2u57qm5p2f"></span>

### 凭据约束

- **每个 Vault 内的匹配键必须唯一。** `mcp_server_url`（`mcp_oauth`、`static_bearer`、`mcp_static`）和 `secret_name`（`environment_variable`、`env_oauth`）在同一 Vault 的有效凭据中不能重复。重复创建返回 409。

- **匹配键不可变。** 要修改 `mcp_server_url` 或 `secret_name`，需要删除旧凭据，再创建新凭据。

- **每个 Vault 最多包含 100 个凭据。** 超出限制时返回 409。

MCP 类型凭据（`mcp_oauth`、`static_bearer`、`mcp_static`）在创建时会立即连接目标 MCP 服务器探测握手。无法完成握手时，创建请求返回 4xx 错误。`environment_variable` 和 `env_oauth` 不在创建时验证密钥或访问令牌是否有效；无效值会在 Session 访问对应主机时表现为鉴权错误或下游服务错误，但不会阻止 Session 继续运行。

<span id=".5Zyo5Yib5bu6LXNlc3Npb24t5pe25byV55SoLXZhdWx0cw=="></span>

## 在创建 Session 时引用 Vaults

创建 Session 时传 `vault_ids` 数组，把一个或多个 Vaults 挂到 Session：

<Tabs>
<Tab zoneid="ozH9Q5hHad" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

**运行时行为**：

- 当 Agent 连接到某 MCP URL 时，**没有任何凭据匹配** `mcp_server_url` → 尝试匿名连接；若服务器要求鉴权则报错。

- **多个 Vaults 都包含匹配凭据** → **第一个匹配的 Vaults 优先**。

- 在 [多 Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/multi-agent) 中，Vaults 凭据**按线程**生效；若某 Agent 自身定义里声明了匹配的 MCP 服务器，该 Agent 用这些凭据鉴权。

<span id=".6L2u5o2i5Yet5o2u"></span>

## 轮换凭据

密钥值和 `display_name` 可以更新。结构性字段（`mcp_server_url`、`secret_name`、`token_endpoint`、`client_id`） 在创建后即被锁定。要修改结构性字段，删除旧凭据再创建新的：

<Tabs>
<Tab zoneid="GKr3Y7RgXv" title="Curl">
<TabTitle>Curl</TabTitle>

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

</Tab>
</Tabs>

<span id=".5Yet5o2u55Sf5ZG95ZGo5pyf"></span>

## 凭据生命周期

凭据会在 Session 期间与 Vaults 生命周期内**周期性重新解析**。这确保凭据的轮换、删除、刷新失败都能传播到正在运行的 Session，无需重启。

对于 `mcp_oauth` 凭据，重新解析还会在 access token 过期时刷新它。如果刷新失败，系统会记录失败事件。未来版本将支持通过 Webhook 订阅 `vault.* / vault_credential.*` 事件，届时可在本节订阅这些生命周期事件。

<span aceTableMode="list" aceTableWidth="1,2"></span>

| 事件                              | 触发                                                                            |
| --------------------------------- | ------------------------------------------------------------------------------- |
| `vault.deleted`                   | Vaults 被删除（级联触发底层凭据 `vault_credential.deleted`）                    |
| `vault_credential.deleted`        | 凭据被删除（直接删除或因 Vaults 删除）                                          |
| `vault_credential.refresh_failed` | `mcp_oauth` 凭据刷新失败（refresh token 无效，或 OAuth 服务器返回不可恢复错误） |

<span id=".5YW25LuW5pON5L2c"></span>

## 其他操作

- 列出 Vaults 、凭据：`GET /vaults` 或 `GET /vaults/{id}/credentials`；分页返回，按最新优先排序。

- 删除 Vaults 、凭据：硬删除，所有相关记录与密钥一并清除，不可恢复。

<a id="doc-2553727"></a>

---

## 上传与挂载文件

> 来源：[https://docs.volcengine.com/docs/82379/2553727?lang=zh](https://docs.volcengine.com/docs/82379/2553727?lang=zh)

本文介绍如何在方舟 Managed Agents 中上传和挂载输入文件、在 Session 运行时管理文件资源，以及查询 Agent 生成的产物。

Managed Agents 支持挂载通过 [文件输入(Files API)](https://ark.volcengine.com/region:cn-beijing/docs/ark/file-api) 或 [TOS 对象存储](https://www.volcengine.com/docs/6349/74820?lang=zh) 上传的文件，再将文件作为 Session 资源挂载到沙箱环境目录中。Agent 可以读取这些输入文件，并将最终产物写入 `/mnt/session/outputs/`。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">挂载 TOS 文件前，需要确保 TOS 和 Managed Agents 在同一火山账号中。</div>

<span id=".5LiK5Lyg5paH5Lu2"></span>

## 上传文件

你可以使用 Files API 或 TOS 上传需要被 Agent 读取的文件。

- 使用 Files API：

先将本地文件上传到 Files API。上传成功后，平台会返回 `file_id`，后续创建 Session 或向运行中的 Session 追加文件资源时都需要使用该 ID。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">设置 <code>purpose=agent</code>，指定该文件供 Agent 使用。</div>

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

- 使用 TOS 上传：

以下示例使用 TOS Python SDK 上传本地文件，更多使用方式，请参考 [TOS 官方文档](https://www.volcengine.com/docs/6349/74820?lang=zh)。

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

<span id=".5Zyo5Yib5bu6LXNlc3Npb24t5pe25oyC6L295paH5Lu2"></span>

## 在创建 Session 时挂载文件

创建 Session 时，在 `resources` 数组中声明需要挂载的文件。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">你也可以在 <a href="https://ark.volcengine.com/region:cn-beijing/managed-agents/sessions?projectName=default">Sessions 页面</a> 创建 Session。</div>

- 通过 Files ID 挂载：

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

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip"><code>mount_path</code> 为云端沙箱中的目标挂载路径，通过 <code>file_id</code> 传入的文件将按照指定路径挂载到 <code>/mnt/session/uploads/</code> 目录中。</div>

<div data-tips="true" data-tips-type="tip">挂载时如指定 <code>my-skills/skill-1.md</code> 路径，挂载文件路径为 <code>/mnt/session/uploads/my-skills/skill-1.md</code>。</div>

挂载后，平台会为该 Session 内的文件实例生成新的 `file_id`。这些 Session 内副本不计入用户的文件存储额度。

- 从 TOS 挂载：

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

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

- <div data-tips="true" data-tips-type="tip"><code>tos_key</code> 需为 TOS 桶中的一个目录，且必须以 <code>/</code> 结尾。例如 <code>project-resources/</code>。</div>

- <div data-tips="true" data-tips-type="tip">文件将被挂载至云端沙箱的 <code>/mnt/session/storage/</code> 目录中。</div>

<span id=".5oyC6L295aSa5Liq5paH5Lu2"></span>

## 挂载多个文件

如果一次任务需要多个输入文件，可以在 `resources` 中添加多个条目。单个 Session 最多支持挂载 100 个文件。

以 Files ID 为例：

```json
"resources": [
  { "type": "file", "file_id": "<FILE_ID_1>" },
  { "type": "file", "file_id": "<FILE_ID_2>" },
  { "type": "file", "file_id": "<FILE_ID_3>" }
]
```

<span id=".5ZyoLXNlc3Npb24t6L-Q6KGM5pe2566h55CG5paH5Lu2"></span>

## 在 Session 运行时管理文件

Session 创建后，可以通过 Session Resources API 继续添加文件，并查询当前已挂载的资源。

以下示例沿用前面创建 Session 后写入的 `$SESSION_ID`；添加 Files API 文件时也沿用上传步骤写入的 `$FILE_ID`。

<span id=".5re75Yqg5paH5Lu26LWE5rqQ"></span>

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

<span id=".5p-l6K-i5paH5Lu26LWE5rqQ"></span>

### 查询文件资源

```Bash
# List session resources
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/sessions/$SESSION_ID/resources" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

<span id=".6I635Y-WLXNlc3Npb24t6L-Q6KGM5pe25Lqn54mp"></span>

## 获取 Session 运行时产物

Agent 将需要交付的文件写入 `/mnt/session/outputs/` 后，平台会将这些文件注册为当前 Session 下 `purpose=agent` 的文件。需要在你的服务中展示或下载报告、图片、视频、文档或代码等产物时，通过 Files API 获取：

1. 调用 `GET /files?scope_id=<session_id>&purpose=agent`，分页查询该 Session 的 Agent 产物。

2. 从列表响应中获取文件 ID、文件名、文件大小、MIME 类型和 `download_url`。

3. 使用 `download_url` 下载文件内容。

```Bash
# List files associated with a session
curl -sS --fail-with-body \
  "https://ark.cn-beijing.volces.com/api/v3/files?scope_id=$SESSION_ID&purpose=agent" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">省略 <code>purpose=agent</code> 时，查询结果还可能包含用户挂载到该 Session 的文件副本。</div>

列表响应中的 `download_url` 是服务端签发的预签名下载地址，具有时效性。

创建 Session 前，按产物的保留要求选择存储方式：

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 你的配置                                          | 产物位置                                                                       | 你需要做什么                                                                     |
| ------------------------------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| 不配置 `config.tos`                               | 方舟公共 TOS                                                                   | 在平台 TTL 到期前下载所需文件。删除 Session 时，平台会清理默认存储中的关联产物。 |
| 在 Environment 或 Session 覆写中配置 `config.tos` | 你的 TOS Bucket，完整对象路径为 `{prefix}outputs/{env-id}/{session-id}/{file}` | 在 TOS 中管理对象生命周期。删除 Session 不会删除 Bucket 中的对象。               |

配置 Environment 产物存储及按 Session 临时覆写的方法，详情请参见 [配置产物存储](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment#configure-output-storage) 和 [覆写产物存储（可选）](https://ark.volcengine.com/region:cn-beijing/docs/ark/start-session#override-output-storage)。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">需要保存 Agent 产物时配置 <code>config.tos</code>，对应 <code>/mnt/session/outputs/</code>。需要挂载输入或共享数据时配置 <code>resources</code> 中的 <code>type: tos</code>，对应 <code>/mnt/session/storage/</code>。</div>

<span id=".5pSv5oyB55qE5paH5Lu257G75Z6L"></span>

## 支持的文件类型

Agent 可以处理多种文件类型。常见类型包括：

- 源代码文件，例如 `.py`、`.js`、`.ts`、`.go`、`.rs` 等。

- 数据文件，例如 `.csv`、`.json`、`.xml`、`.yaml`。

- 文本文档，例如 `.txt`、`.md`。

- 归档文件，例如 `.zip`、`.tar.gz`；Agent 可以在沙箱中使用 bash 解压后处理。

- 二进制文件；是否能正确处理取决于沙箱内是否具备相应工具。

<span id=".5L2_55So5bu66K6u"></span>

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

<span id=".5Z-65pys5qaC5b-1"></span>

## 基本概念

将 Memory Store 挂载到 Session 后，Agent 可以根据挂载时配置的访问权限读取或修改记忆内容。

每条 Memory 都有独立路径，您可以通过 API 或控制台直接读取、创建、更新和删除。

使用 Memory Store 时，需要在创建 Agent 时启用 Agent Toolset。可执行的操作由挂载时的 `access` 决定。

<span id=".5Yib5bu6LW1lbW9yeS1zdG9yZQ=="></span>

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

<span id=".6aKE572uLW1lbW9yeS3lhoXlrrk="></span>

## 预置 Memory 内容

在 Agent 开始运行前，可以先向 Store 中写入参考资料，例如项目规范、用户偏好、输出格式、术语表等。

根据写入数量选择接口：

- 调用 [创建记忆](https://ark.volcengine.com/region:cn-beijing/docs/ark/create-memory-api) 写入单条 Memory，适合补充零散内容。

- 调用 [批量创建记忆](https://ark.volcengine.com/region:cn-beijing/docs/ark/batch-create-memories-api) 批量写入多条 Memory，适合初始化术语表、用户偏好或输出模板。

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

<span id=".5oyC6L29LW1lbW9yeS1zdG9yZS3liLAtc2Vzc2lvbg=="></span>

## 挂载 Memory Store 到 Session

Memory Store 需要在创建 Session 时通过 `resources` 数组挂载。与文件和代码仓库资源不同，Memory Store 只能在 Session 创建时挂载，不支持在运行中的 Session 中追加或移除。

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

单个 Session 最多挂载 10 个 Memory Store。可以按不同使用场景拆分 Store，例如用户偏好、项目上下文、团队共享规范分别管理，并为每个 Store 设置独立访问权限和生命周期。

<span id=".YWdlbnQt5aaC5L2V6K6_6ZeuLW1lbW9yeQ=="></span>

## Agent 如何访问 Memory

- `read_only`：Agent 可以浏览、读取和搜索 Memory，但不能修改内容。

- `read_write`：Agent 除了读取 Memory，还可以创建、更新、删除或移动内容。修改会保存在挂载的 Memory Store 中，供后续 Session 继续使用。

- Agent 对 Memory 的读取会作为普通工具调用出现在 Session 事件流中，例如 `agent.tool_use` 和 `agent.tool_result`。

<span id=".5p-l55yL5ZKM57yW6L6RLW1lbW9yeQ=="></span>

## 查看和编辑 Memory

你可以通过 [查询记忆列表](https://ark.volcengine.com/region:cn-beijing/docs/ark/list-memories-api)、[查询记忆详情](https://ark.volcengine.com/region:cn-beijing/docs/ark/get-memory-api)、[更新记忆](https://ark.volcengine.com/region:cn-beijing/docs/ark/update-memory-api) 和 [删除记忆](https://ark.volcengine.com/region:cn-beijing/docs/ark/delete-memory-api) 查询和维护 Memory，用于审核内容、修正错误或清理过期信息。

<span id=".5p-l6K-iLW1lbW9yeS3liJfooag="></span>

### 查询 Memory 列表

可通过 `path_prefix` 按路径前缀浏览 Memory，类似查看目录。

```Bash
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories?path_prefix=/&order_by=path&depth=2" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  | jq -r '.data[] | "\(.type)  \(.path)"'
```

<span id=".6K-75Y-WLW1lbW9yeQ=="></span>

### 读取 Memory

读取单条 Memory 会返回完整内容。

```Bash
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories/$MEMORY_ID" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  | jq -r '.content'
```

<span id=".5Yib5bu6LW1lbW9yeQ=="></span>

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

<span id=".5pu05pawLW1lbW9yeQ=="></span>

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

<span id=".5Yig6ZmkLW1lbW9yeQ=="></span>

### 删除 Memory

```Bash
curl -sS --fail-with-body -X DELETE "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID/memories/$MEMORY_ID" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

<span id=".566h55CGLW1lbW9yeS1zdG9yZQ=="></span>

## 管理 Memory Store

除创建外，Memory Store 还支持查询、删除等操作。

<span id=".5p-l6K-iLXN0b3JlLeWIl-ihqA=="></span>

### 查询 Store 列表

```Bash
curl -sS --fail-with-body "https://ark.cn-beijing.volces.com/api/v3/memory_stores" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

<span id=".5Yig6ZmkLXN0b3Jl"></span>

### 删除 Store

```Bash
curl -sS --fail-with-body -X DELETE "https://ark.cn-beijing.volces.com/api/v3/memory_stores/$STORE_ID" \
  -H "Authorization: Bearer $ARK_API_KEY"
```

<span id=".5pyA5L2z5a6e6Le1"></span>

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

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">此为邀测能力，如需使用，提交 <a href="https://console.volcengine.com/auth/login?redirectURI=%2Fworkorder%2Fcreate%3Fstep%3D2%26SubProductID%3DP00001166">测试申请工单</a>。</div>

<span id=".YWR2aXNvci3phY3nva7or7TmmI4="></span>

# Advisor 配置说明

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

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">当前不支持在 <code>evolution</code> 工具集上配置 <code>default_config</code>，也不支持为 <code>advisor</code> 单独配置 <code>permission_policy</code>。</div>

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

&nbsp;

<span id=".5YeG5aSH5bel5L2c"></span>

# 准备工作

开始前你需要：

- 已创建的 API Key：配置为环境变量 `ARK_API_KEY`，详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 已创建的 Agent：详情请参见 [定义 Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent)。

- 已创建的 Environment：详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment)。

本章节示例的 Base URL 与鉴权方式详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/ark/base-url-and-authentication)。

<span id=".5byA5ZCvLWFkdmlzb3I="></span>

# 开启 Advisor

<span id=".5o6n5Yi25Y-w5pa55byP"></span>

## 控制台方式

1. 进入 [Agent 管理页面](https://ark.volcengine.com/region:cn-beijing/managed-agent/agents)。

2. 点击「创建 Agent」，或在已有 Agent 卡片上点击「编辑」。

3. 在「05 高级参数」区域，找到「Advisor」开关，打开即可。

4. 点击「保存」。

开启后，该 Agent 的所有新 Session 都会自动启用 Advisor 能力。

<span id=".YXBpLeaWueW8jw=="></span>

## API 方式

创建或更新 Agent 时，在 `tools` 中挂载 `evolution` 类型并配置 `advisor` 即可开启。

<span id=".5Yib5bu65pe25byA5ZCv"></span>

### 创建时开启

<Tabs>
<Tab zoneid="OgYYDCYNLm" title="cURL">
<TabTitle>cURL</TabTitle>

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
        "type": "agent_toolset_20260701"
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

</Tab>
</Tabs>

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
      "type": "agent_toolset_20260701",
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

<span id=".5pu05paw5bey5pyJLWFnZW50"></span>

### 更新已有 Agent

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">更新时需要传入当前 <code>version</code>，版本号不匹配会更新失败。更新成功后会生成新版本。</div>

<Tabs>
<Tab zoneid="O2DpGUXSb9" title="cURL">
<TabTitle>cURL</TabTitle>

```Bash
curl https://ark.cn-beijing.volces.com/api/v3/agents/{agent_id} \
  -X POST \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "version": 1,
    "tools": [
      {
        "type": "agent_toolset_20260701"
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

</Tab>
</Tabs>

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning"><code>tools</code> 使用覆盖逻辑。请求体一旦传入 <code>tools</code>，系统会用该数组整体覆盖 Agent 当前的 <code>tools</code> 配置，不会在原有基础上追加。如果你只想新增或调整某个工具，需要先读取当前 Agent 的 <code>tools</code>，再把「需要保留的工具 + 新的工具」一起写回请求体。</div>

<span id=".5bel5L2c5pa55byP"></span>

# 工作方式

开启 Advisor 后，Agent 在 Session 中执行任务时，遇到以下情况会自动唤起顾问模型：

- Agent 多次执行失败，无法通过自身能力解决

- 当前模型返回工具调用异常、输出格式错误

- 反复卡在同一问题无法推进

- 系统提示词中引导的关键决策节点

顾问模型会接收完整的任务上下文，给出指导建议后，Agent 继续执行。整个过程无需人工干预。

<span id=".6KeC5a-fLWFkdmlzb3It6LCD55So"></span>

# 观察 Advisor 调用

Advisor 的调用过程可以通过控制台 Debug 模式观测。当前公开的 Session 事件结构中未定义独立的 Advisor 调用事件；通过事件流 API 可以继续观察 Agent 消息、工具调用、状态变化等会话执行过程。

查看方式：

- **控制台 Debug 模式**：在 Agent 调试页面切换到 Debug 模式，可直观看到 Advisor 调用的时间点和内容。

- **事件流 API**：通过 Session Events 接口获取完整事件流，编程方式处理会话执行事件。

事件流的使用方式详见 [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream)。

<span id=".57O757uf5o-Q56S66K-N5bu66K6u"></span>

# 系统提示词建议

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

<span id=".6K6h6LS56K-05piO"></span>

# 计费说明

Advisor 调用产生的 Token 费用按顾问模型的价格单独计费，不计入执行模型的用量。具体计费规则请参考方舟 Managed Agents 的 [计费说明](https://ark.volcengine.com/region:cn-beijing/docs/ark/model-pricing#ma_billing)。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">Beta 阶段 Advisor 功能可能有免费额度或优惠政策，具体以控制台公示为准。</div>

<span id=".55u45YWz5paH5qGj"></span>

# 相关文档

<columns>
<columnsItem zoneid="q5UNKU9wAS">

<card mode="section" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/tools" >

<span id="tools"></span>

#### [Tools](https://ark.volcengine.com/region:cn-beijing/docs/ark/tools)

了解 Agent 支持的公开工具类型和配置方式。

</card>

<card mode="section" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/multi-agent" >

<span id=".57yW5o6SLW11bHRpLWFnZW50"></span>

#### [编排 Multi Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/multi-agent)

Multi Agent 支持一个协调器 Agent 将任务委派给多个子智能体并行执行。

</card>

</columnsItem>
<columnsItem zoneid="jS87a6P4FQ">

<card mode="section" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/define-outcome" >

<span id=".5a6a5LmJLW91dGNvbWU="></span>

#### [定义 Outcome](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-outcome)

Outcome 支持定义任务验收标准，Agent 自动迭代直到达标。

</card>

<card mode="section" href="https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream" >

<span id=".c2Vzc2lvbi3kuovku7bmtYE="></span>

#### [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream)

通过事件流实时观测 Agent 的执行过程，包括消息、工具调用和状态变化。

</card>

</columnsItem>
</columns>

<a id="doc-2553730"></a>

---

## 编排 Multi Agent

> 来源：[https://docs.volcengine.com/docs/82379/2553730?lang=zh](https://docs.volcengine.com/docs/82379/2553730?lang=zh)

Multi Agent（多智能体协作）允许协调器 Agent 将任务委派给多个子智能体。子智能体使用各自的模型、系统提示词、技能和工具完成任务。协调器负责拆分任务并汇总结果。

本页从已有子智能体和 Environment 开始，演示如何创建协调器、运行任务、验证结果和处理异常。

<span id=".6YCJ5oup5bm25YeG5aSH"></span>

## 选择并准备

Multi Agent 适合需要角色分工或并行处理的复杂任务。如果任务不需要独立上下文或角色边界，优先使用更简单的方式。

<span aceTableMode="list" aceTableWidth="1,2,2"></span>

| 任务特征                               | 推荐方式               | 适用场景                       |
| -------------------------------------- | ---------------------- | ------------------------------ |
| 多个角色需要使用不同提示词、模型或工具 | Multi Agent            | 代码审查、测试设计等多角色任务 |
| 单个 Agent 按固定步骤调用工具          | Agent + 工具（Tools）  | 流程固定，不需要独立角色上下文 |
| 单个 Agent 复用操作说明                | Agent + 技能（Skills） | 需要沉淀流程，但不需要角色协作 |

开始前，完成以下准备：

- 创建 API Key，并配置为 `ARK_API_KEY`。详情请参见 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey)。

- 创建两个子智能体，并记录 Agent ID 和版本号。详情请参见 [Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent)。

- 创建或复用云环境，并记录 Environment ID。详情请参见 [配置云环境](https://ark.volcengine.com/region:cn-beijing/docs/ark/configure-cloud-environment)。

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

<span id=".6YWN572u5Y2P6LCD5Zmo"></span>

## 配置协调器

<span id=".5a6a5LmJ6KeS6Imy5ZKM5aeU5rS-6KeE5YiZ"></span>

### 定义角色和委派规则

协调器的系统提示词需要说明每个子智能体的职责和输出。角色边界越明确，协调器越容易正确分配任务。

<span aceTableMode="list" aceTableWidth="1,3"></span>

| 角色       | 职责                                             |
| ---------- | ------------------------------------------------ |
| 代码审查员 | 检查代码质量、架构和安全风险，输出问题与修改建议 |
| 测试工程师 | 设计测试用例，说明覆盖范围和未覆盖风险           |

系统提示词还需要要求协调器先拆分和委派任务，收到全部结果后再汇总。下方 cURL 包含完整提示词。

<span id=".5Yib5bu65Y2P6LCD5Zmo"></span>

### 创建协调器

在 `multiagent.agents` 中传入子智能体 ID 和版本号。显式填写版本后，子智能体发布新版本不会改变当前协调器。

<Tabs>
<Tab zoneid="pxdiGAJjRg" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

脚本将协调器 ID 和版本号写入 `COORDINATOR_AGENT_ID` 和 `COORDINATOR_AGENT_VERSION`。字段说明请参见 [创建智能体](https://ark.volcengine.com/region:cn-beijing/docs/ark/create-agent-api)。

也可以通过控制台配置：

1. 进入 [Agent 管理页面](https://ark.volcengine.com/region:cn-beijing/managed-agent/agents)。

2. 创建或编辑 Agent，在 **04 能力扩展** 区域添加 **Multi Agents**。

3. 选择子智能体，填写前面的系统提示词并保存。

4. 记录协调器 Agent ID 和版本号。

<span id=".6L-Q6KGM5Lu75Yqh"></span>

## 运行任务

主流程使用两个终端。终端 A 接收主事件流，终端 B 创建 Session、发送任务和查询结果。需要实时观察子线程时，再打开终端 C。

<span id=".XzEt5Yib5bu6LXNlc3Npb24="></span>

### 1. 创建 Session

在终端 B 创建 Session：

<Tabs>
<Tab zoneid="n1s79FVgRk" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

脚本将 Session ID 写入 `SESSION_ID`：

```Text
Session ID: sesn-20261005121000-xxxxx
```

<span id=".XzIt6K6i6ZiF5Li75LqL5Lu25rWB"></span>

### 2. 订阅主事件流

在终端 A 配置同一个 Session ID：

```Bash
export ARK_API_KEY="<ARK_API_KEY>"
export ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
export SESSION_ID="<SESSION_ID>"
```

建立主事件流：

<Tabs>
<Tab zoneid="gznU4GImzi" title="cURL">
<TabTitle>cURL</TabTitle>

```Bash
: "\\${ARK_BASE_URL:?Set ARK_BASE_URL}"
: "\\${ARK_API_KEY:?Set ARK_API_KEY}"
: "\\${SESSION_ID:?Set SESSION_ID}"

curl -sS -N --fail-with-body --max-time 600 \
  "$ARK_BASE_URL/sessions/$SESSION_ID/events/stream" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Accept: text/event-stream"
```

</Tab>
</Tabs>

终端 A 输出 `: ready` 后保持连接。先建连再发送任务，可以避免遗漏实时事件。

<span id=".XzMt5Y-R6YCB5Lu75Yqh"></span>

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

<Tabs>
<Tab zoneid="S0N0M0xiAG" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

<span id=".5a6e5pe26KeC5a-f5a2Q57q_56iL77yI5Y-v6YCJ77yJ"></span>

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

3. 建立子线程事件流：

   <Tabs>
   <Tab zoneid="lqq5vpIBVs" title="cURL">
   <TabTitle>cURL</TabTitle>

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

   </Tab>
   </Tabs>

4. 终端 C 输出 `: ready` 后，在终端 B 按“验证结果”中的方式补查该子线程历史。

5. 按事件 `id` 合并实时和历史事件，忽略重复 ID。

每个子线程使用独立事件流。出现多个 `session_thread_id` 时，分别重复以上步骤。

<span id=".XzQt562J5b6F5pys6L2u57uT5p2f"></span>

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

- `end_turn`：本轮执行结束，按下一节验证结果。

- `requires_action`：保持连接，处理全部待操作事件后继续观察。

- `retries_exhausted`：调整输入后可以发送新任务；如果修改 Agent 配置，请创建新 Session。

<span id=".6aqM6K-B57uT5p6c"></span>

## 验证结果

在终端 B 列出全部线程：

<Tabs>
<Tab zoneid="Da0mxXthe6" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

命令将第一个子线程 ID 写入 `SESSION_THREAD_ID`。检查其他子线程时，更新该变量并清空分页标识：

```Bash
unset THREAD_EVENTS_PAGE
```

查询该子线程的完整历史：

<Tabs>
<Tab zoneid="lI7iWtDF0U" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

如果 `Next page` 不为空，保持 `THREAD_EVENTS_PAGE` 并重复执行，直到下一页标识为空。切换子线程前必须再次清空该变量。

主线程和各子线程使用独立事件流，不能根据跨流到达顺序判断执行顺序。使用 `session_thread_id`、`from_session_thread_id` 和 `to_session_thread_id` 关联委派与返回消息。

只有同时满足以下条件，本轮任务才成功：

- 主线程产生最终 `agent.message`。

- Session 以 `stop_reason.type=end_turn` 进入 `idle`。

- 没有子线程停留在 `running` 或 `rescheduling`。

- 本轮没有出现 `session.thread_status_terminated`。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

<div data-tips="true" data-tips-type="warning">HTTP 200 只表示服务端已受理事件。<code>session.status_idle</code> 不一定表示任务成功。</div>

<span id=".5aSE55CG5byC5bi454q25oCB"></span>

## 处理异常状态

<span aceTableMode="list" aceTableWidth="2,3,4"></span>

| 信号                                | 含义                       | 处理动作                                        |
| ----------------------------------- | -------------------------- | ----------------------------------------------- |
| SSE 连接中断                        | 中断期间的事件不会自动回放 | 先重建连接，再查询历史并按事件 ID 去重          |
| `requires_action`                   | 等待工具确认或外部工具结果 | 处理全部 `stop_reason.event_ids`                |
| `session.thread_status_rescheduled` | 子线程正在自动重新调度     | 保持监听，不要并行手动重试                      |
| `session.thread_status_terminated`  | 子线程已终止               | 查询历史和错误事件，Session 空闲后重新分配任务  |
| 子线程长时间保持 `running`          | 子任务未结束               | 携带 `session_thread_id` 发送 `user.interrupt`  |
| `retries_exhausted`                 | 当前轮次重试次数已用尽     | 调整输入后重试；修改 Agent 配置后创建新 Session |

<span id=".5aSE55CG562J5b6F5pON5L2c"></span>

### 处理等待操作

子线程以 `requires_action` 进入 `idle` 后，按以下步骤处理：

1. 保持主事件流和目标子线程事件流连接。如果连接已关闭，先重新建连并等待 `: ready`，再补查历史。补查主线程前清空分页标识：

   ```Bash
   unset SESSION_EVENTS_PAGE
   ```

   <Tabs>
   <Tab zoneid="HPRko2t0DK" title="cURL">
   <TabTitle>cURL</TabTitle>

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

   </Tab>
   </Tabs>

2. 从 `session.thread_status_idle` 中记录 `session_thread_id` 和全部 `stop_reason.event_ids`。按“验证结果”中的方式补查子线程，并逐一找到这些事件。

3. 对 `agent.tool_use` 或 `agent.mcp_tool_use`，配置关联 ID 并发送确认：

   ```Bash
   export SESSION_THREAD_ID="<SESSION_THREAD_ID>"
   export TOOL_USE_ID="<TOOL_USE_ID>"
   ```

   <Tabs>
   <Tab zoneid="Aj7OP5M0XH" title="cURL">
   <TabTitle>cURL</TabTitle>

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

   </Tab>
   </Tabs>

4. 对 `agent.custom_tool_use`，执行自定义工具并发送 `user.custom_tool_result`。请求需要携带当前 `session_thread_id` 和 `custom_tool_use_id`。详情请参见 [Tools](https://ark.volcengine.com/region:cn-beijing/docs/ark/tools)。

5. 处理全部 `event_ids` 后继续观察。任务可能再次进入 `requires_action`；只有 Session 最终以 `end_turn` 结束，本轮任务才完成。

本页主流程使用云环境，不覆盖自托管 Environment 的 `user.tool_result` 流程。自托管流程请参见 [Tools](https://ark.volcengine.com/region:cn-beijing/docs/ark/tools)。

<span id=".5Lit5pat57q_56iL"></span>

### 中断线程

只停止一个子线程时，运行 `export SESSION_THREAD_ID="<SESSION_THREAD_ID>"`。停止全部活跃线程时，运行 `unset SESSION_THREAD_ID`。

<Tabs>
<Tab zoneid="DGzyfPf9dG" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

中断指定子线程后等待该线程进入 `idle`；中断全部线程后等待 Session 进入 `idle`。

<span id=".5pu05paw5Y2P5L2c6YWN572u"></span>

## 更新协作配置

新增、移除子智能体或切换版本时，先调用 [查询智能体详情](https://ark.volcengine.com/region:cn-beijing/docs/ark/get-agent-api) 查询协调器最新配置。更新请求需要携带当前 `version`，并整体提交匹配的系统提示词和 `multiagent.agents`。

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

<Tabs>
<Tab zoneid="zJENnQ4M2Y" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

脚本将新版本号写入 `COORDINATOR_AGENT_VERSION`。如果返回 `version: conflict`，重新查询并合并最新配置后再提交。

现有 Session 不会自动切换版本。只有 Session 处于 `idle`、上一轮以 `end_turn` 结束且没有其他升级请求时，才可以升级：

<Tabs>
<Tab zoneid="gxdo2ZydGy" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

等待 Session 使用目标版本恢复为 `idle`：

<Tabs>
<Tab zoneid="Fkl4G1o9Wf" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

脚本默认每 2 秒查询一次，最多 60 次。只有状态为 `idle`，且 `agent.id` 和 `agent.version` 与目标一致时才返回成功；超时或进入失败状态时，请检查 Session 或创建新 Session。

<span id=".5L2_55So6ZmQ5Yi2"></span>

## 使用限制

- 子智能体数量：单个协调器最多配置 20 个子智能体，详情请参见 [创建智能体](https://ark.volcengine.com/region:cn-beijing/docs/ark/create-agent-api)。

- 委派层级：Multi Agent 只展开一层。`multiagent.agents[].type` 为 `self` 时复用协调器配置创建子线程，但不会继续复用协调器的 `multiagent` 列表。

- 版本快照：更新协调器不会修改已有 Session 的 Agent 快照。

- 资源消耗：协调器和子智能体分别产生模型与运行资源用量，详情请参见 [Managed Agents](https://ark.volcengine.com/region:cn-beijing/docs/ark/model-pricing#ma_billing)。

<span id=".5riF55CG6LWE5rqQ"></span>

## 清理资源

确认产物已转存后，使用精确 ID 删除本页创建的 Session。Session 仍在运行时，先中断全部线程并等待 `idle`。默认保留协调器：

```Bash
export CONFIRM_DELETE="yes"
export DELETE_COORDINATOR="no"
```

仅当协调器不再被任何 Session 使用时，才将 `DELETE_COORDINATOR` 改为 `yes`。执行删除：

<Tabs>
<Tab zoneid="YVCh88Ot8b" title="cURL">
<TabTitle>cURL</TabTitle>

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

</Tab>
</Tabs>

命令不会删除子智能体或 Environment。

<div data-tips="true" data-tips-type="danger" data-tips-is-title="true">警告</div>

<div data-tips="true" data-tips-type="danger">删除 Session 或 Agent 后无法恢复。只使用本页记录的精确 ID，不要按名称模糊匹配或批量删除共享资源。</div>

<span id=".55u45YWz5paH5qGj"></span>

## 相关文档

- [Agent](https://ark.volcengine.com/region:cn-beijing/docs/ark/define-agent)

- [Session 事件流](https://ark.volcengine.com/region:cn-beijing/docs/ark/session-event-stream)

- [Tools](https://ark.volcengine.com/region:cn-beijing/docs/ark/tools)

- [管理 Session](https://ark.volcengine.com/region:cn-beijing/docs/ark/manage-session)
