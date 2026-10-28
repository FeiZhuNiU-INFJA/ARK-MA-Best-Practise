# API 参考文档 (GPT-image-2)

## Configuration

| 配置项 | 值 |
|--------|-----|
| **API Base URL** | `https://bmc-llm-relay.bluemediagroup.cn` |
| **API Endpoint** | `/v1/images/generations` |
| **Model** | `gpt-image-2` |
| **Output Size** | 生成 2000×672 (3:1) → 裁切至 2000×400 (5:1) |
| **Output Format** | PNG |
| **Prompt 语言** | ⚠️ **纯英文**（中文触发 moderation_blocked） |

## API Key

- 环境变量: `REPORT_COVER_API_KEY`（必填，由 MA Vault / 部署环境注入，不入库）
- 无默认值：缺失时脚本直接报错，禁止硬编码真实 key

## GPT-image-2 限制

| 限制项 | 说明 |
|--------|------|
| Prompt 语言 | 必须纯英文，任何中文字符触发 Azure 审核拦截 |
| 最大比例 | 3:1（宽:高） |
| 尺寸对齐 | 宽和高必须被 16 整除 |
| 生成速度 | 60-120s（比 Gemini 慢 2-3 倍） |
| 限流 | 可能返回 429，需等待后重试 |

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 中文主标题（图片内渲染用） |
| `subtitle` | string | 是 | 中文副标题（图片内渲染用） |
| `title_en` | string | 是 | 英文主标题（prompt 描述用） |
| `subtitle_en` | string | 是 | 英文副标题（prompt 描述用） |
| `output_path` | string | 否 | 输出路径，默认 `cover.png` |
| `width` | int | 否 | 目标宽度，默认 2000 |
| `height` | int | 否 | 目标高度，默认 400 |
| `api_key` | string | 否 | API Key |

## Output

```python
{
    "success": True,
    "path": "cover.png",
    "size": (2000, 400),
    "error": None
}
```

## Error Handling

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `moderation_blocked` | Prompt 含中文字符 | 检查所有参数，确保 prompt 为纯英文 |
| 429 RateLimitReached | 请求过于频繁 | 等待 10s 后重试 |
| 请求超时 | GPT 模型生成较慢 | 重试，timeout 已设为 600s |
| API 调用失败 / 403 | Key 无效或 IP 限制 | 检查 Key 和网络 |

## 响应格式

GPT-image-2 通过 images/generations 接口返回：

```json
{
  "data": [
    {
      "b64_json": "iVBORw0KGgo...",
      "revised_prompt": "..."
    }
  ]
}
```

## 尺寸计算逻辑

目标 2000×400 (5:1) 超过 GPT 最大 3:1 限制：
1. 生成 2000×672 (≈3:1，被 16 整除)
2. 居中裁切高度：(672-400)/2 = 136px 上下各裁
3. 得到 2000×400
