# 火山 Managed Agent BYOK 模型接入指南（对客版）

## 1. 需要准备什么

### 1.1 Direct 模式说明

| 模式 | 适用情况 | 客户需提供 |
| --- | --- | --- |
| Direct | 客户已有 OpenAI/Anthropic 兼容的模型 Endpoint，希望由 Gateway 直接转发请求。仅支持 `anthropic_messages` 和 `openai_chat_completions` 两种协议。 | - 模型 Endpoint<br>- 模型 ID 与 Provider 名称<br>- 模型协议<br>- 模型鉴权 Header 完整值 |

### 1.2 准备接入步骤

**For 销售、行解、产解**

1. 提供要开白的火山账号，以及约定的 Provider name。

   `provider` 是客户与平台在接入前约定并注册的模型网关或模型平台标识，用于识别请求来源并匹配相应的访问权限。只有请求中填写的 `provider` 与已注册名称一致时，Managed Agent 才能访问对应的模型服务；即使使用相同的 `base_url`，未注册或填写错误的 `provider` 也无法访问。因此，`provider` 不是可随意填写的备注字段，请使用接入阶段双方确认的名称。

2. 填写邀测申请，待审批通过后功能即可使用：  
   <https://www.doubao.com/share/base/form/shrcnKcGA2kXgqH6sgyCLcT8jkh>

## 2. 如何使用（重点）

### 2.1 步骤 1：创建 Agent

请将占位符替换为客户实际信息，并根据底层模型真实能力填写 `token_limits`。

创建 Direct 模式 Agent：

```bash
curl --silent --show-error \
  --request POST "${ARK_HOST}/api/v3/agents" \
  --header "Authorization: Bearer ${ARK_API_KEY}" \
  --header "Content-Type: application/json" \
  --data-binary '{
    "name": "my-agent-direct",
    "base_agent": "ark_agent_preview",  // Optional
    "model": {
      "id": "<your-model-id>",
      "provider": "<your-provider-name>",
      "service_tier": "default",
      "base_url": "https://<your-model-endpoint>/v1/chat/completions",
      "protocol": "openai_chat_completions",
      "token_limits": {
        "context_window": 128000,
        "max_input_token_length": 128000,
        "max_output_token_length": 4096
      }
    },
    "mcp_servers": [],
    "tools": [],
    "skills": [],
    "tags": []
  }'
```

`model` 参数说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 客户模型的实际模型 ID，须与该 Endpoint 接受的模型标识一致。 |
| `provider` | 是 | 模型服务提供方或客户网关的名称，用于标识模型来源和定位问题；按客户实际约定填写。 |
| `service_tier` | 否 | 模型服务层级。网关不支持可不填。 |
| `base_url` | 是 | 客户模型的完整服务地址，必须可由公网 Gateway 访问，并与 `protocol` 对应。 |
| `protocol` | 是 | 客户模型接口采用的协议，必须与 Endpoint 的请求、响应、Tool Call 和 SSE 格式一致。仅支持 `anthropic_messages` 和 `openai_chat_completions` 两种协议。 |
| `token_limits` | 是 | 必须显式填写模型的 Token 能力：`context_window` 为上下文窗口，`max_input_token_length` 为最大输入长度，`max_output_token_length` 为最大输出长度。三个值均须为大于 0 的整数，并按底层模型真实能力填写。 |

创建成功后，保存返回体中的 `id`：

```bash
export AGENT_ID="<上一步返回的 agent id>"
```

### 2.2 步骤 2：创建 Session

创建会话：

```bash
curl --silent --show-error \
  --request POST "${ARK_HOST}/api/v3/sessions" \
  --header "Authorization: Bearer ${ARK_API_KEY}" \
  --header "Content-Type: application/json" \
  --data-binary "{\"agent_id\": \"${AGENT_ID}\"}"
```

创建成功后，保存返回体中的 `id`：

```bash
export SESSION_ID="<上一步返回的 session id>"
```

### 2.3 步骤 3：发送消息（重点）

调用 Send events 接口时，需要带上网关支持的 API key。

发送消息（Direct）：

```bash
export TURN_ID="req_$(date +%s)"
export MODEL_AUTH_KEY="Bearer <your-model-token>"

curl --silent --show-error \
  --request POST "${ARK_HOST}/api/v3/sessions/${SESSION_ID}/events" \
  --header "Authorization: Bearer ${ARK_API_KEY}" \
  --header "Content-Type: application/json" \
  --header "X-Client-Request-Id: ${TURN_ID}" \
  --header "X-Request-Id: ${TURN_ID}" \
  --header "X-User-Model-Header-Authorization: ${MODEL_AUTH_KEY}" \
  --data-binary '{
    "events": [{
      "type": "user.message",
      "content": [{"type": "text", "text": "你好"}]
    }]
  }'
```

### 2.4 步骤 4：查询事件流并验证输出

消息提交成功后，Agent Loop 异步运行。调用以下接口查询模型输出、工具调用和最终回答：

```bash
curl --silent --show-error \
  --request GET "${ARK_HOST}/api/v3/sessions/${SESSION_ID}/events" \
  --header "Authorization: Bearer ${ARK_API_KEY}"
```

正常情况下，事件流会依次出现 `agent.message.started`、`agent.message.content.delta` 和 `agent.message.completed` 等事件。

## 3. 注意事项

### 3.1 Provider

`provider` 是客户与平台在接入前约定并注册的模型网关或模型平台标识，用于识别请求来源并匹配相应的访问权限。

只有请求中填写的 `provider` 与已注册名称一致时，Managed Agent 才能访问对应的模型服务；即使使用相同的 `base_url`，未注册或填写错误的 `provider` 也无法访问。因此，`provider` 不是可随意填写的备注字段，请使用接入阶段双方确认的名称。

例如客户网关平台叫 `modelcenter_A`，既可以将 `provider` 定义为 `modelcenter_A`。

### 3.2 鉴权与 Header 规则

- 区分两类凭证：
  - `Authorization: Bearer ${ARK_API_KEY}` 用于方舟平台鉴权。
  - `X-User-Model-Header-*` 用于向客户模型服务传递请求级凭证。
- 按请求传递：客户模型凭证仅放在消息请求 Header 中，不要写入 Agent 或 Session 的持久配置。
- Header 转换：`X-User-Model-Header-<原始 Header 名>` 校验通过后会去掉前缀，并以原始 Header 名转发给客户模型服务。

🔑 通用透传规则：客户 Gateway 需要哪个鉴权 Header，就把该 Header 的原始名称拼接到 `X-User-Model-Header-` 后。Runtime 校验通过后移除该前缀，并将 Header 名和值原样转发；不会改名、补充 `Bearer` 或解析凭证内容。需要多个 Header 时，每个 Header 分别按此规则传入。

示例：客户 Gateway 使用 `openai-apikey` 鉴权。

- 客户 Gateway 期望收到：`openai-apikey: ${MODEL_API_KEY}`
- 调用 Events API 时传入：`X-User-Model-Header-openai-apikey: ${MODEL_API_KEY}`
- Runtime 去掉前缀后，实际转发：`openai-apikey: ${MODEL_API_KEY}`

同理，如果客户 Gateway 要求 `Authorization: Bearer xxx`，则传入 `X-User-Model-Header-Authorization: Bearer xxx`。

这里的 `Authorization` 属于客户模型凭证，与方舟平台鉴权使用的 `Authorization: Bearer ${ARK_API_KEY}` 相互独立。

### 3.3 Token 参数必须真实有效

💡 Direct 模式属于非 Ark Provider。创建或更新 Agent 时必须显式提供 `model.token_limits`；`context_window`、`max_input_token_length`、`max_output_token_length` 均须为大于 0 的整数，并按底层模型真实能力填写。

> 注：部分内容由豆包工作 AI 生成。
