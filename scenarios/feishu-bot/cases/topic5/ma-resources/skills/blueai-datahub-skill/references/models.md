# DataHub 可用模型列表

DataHub 支持多个 AI 模型平台，可通过 `GET /api/v1/model/list` API 获取最新模型列表。

## 视觉分析模型

以下模型支持图片/视频/音频分析，适用于 Media Storage + DataHub 批量打标场景。

### OpenAI / Azure OpenAI

| 模型 ID | 名称 | 视觉能力 | URL 类型 | 备注 |
|---------|------|---------|---------|------|
| `gpt-4o` | GPT-4o | 图片 | HTTPS (`url`) | 推荐，性价比高 |
| `gpt-4o-mini` | GPT-4o Mini | 图片 | HTTPS (`url`) | 轻量版 |
| `gpt-5` | GPT-5 | 图片 | HTTPS (`url`) | 最新 |
| `gpt-5.1` | GPT-5.1 | 图片 | HTTPS (`url`) | |
| `gpt-5.2` | GPT-5.2 | 图片 | HTTPS (`url`) | |
| `gpt-5-mini` | GPT-5 Mini | 图片 | HTTPS (`url`) | |
| `o1` | O1 | 图片 | HTTPS (`url`) | 推理模型 |
| `o3` | O3 | 图片 | HTTPS (`url`) | |
| `o3-pro` | O3 Pro | 图片 | HTTPS (`url`) | |
| `o4-mini` | O4 Mini | 图片 | HTTPS (`url`) | |

### Google Gemini

| 模型 ID | 名称 | 视觉能力 | URL 类型 | 备注 |
|---------|------|---------|---------|------|
| `gemini-2.5-flash` | Gemini 2.5 Flash | 图片+视频+音频 | gs:// (`gcp_url`) | 推荐，多模态 |
| `gemini-2.5-pro` | Gemini 2.5 Pro | 图片+视频+音频 | gs:// (`gcp_url`) | |
| `gemini-3-flash-preview` | Gemini 3 Flash | 图片+视频+音频 | gs:// (`gcp_url`) | 预览版 |
| `gemini-3-pro-preview` | Gemini 3 Pro | 图片+视频+音频 | gs:// (`gcp_url`) | 预览版 |

> **重要**：Gemini 模型必须使用 `gcp_url`（gs:// 格式链接），不支持 HTTPS URL。

### Anthropic Claude (AWS Bedrock)

| 模型 ID | 名称 | 视觉能力 | URL 类型 |
|---------|------|---------|---------|
| `us.anthropic.claude-sonnet-4-20250514-v1:0` | Claude Sonnet 4 | 图片 | HTTPS (`url`) |
| `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | Claude Sonnet 4.5 | 图片 | HTTPS (`url`) |
| `global.anthropic.claude-haiku-4-5-20251001-v1:0` | Claude Haiku 4.5 | 图片 | HTTPS (`url`) |
| `global.anthropic.claude-opus-4-5-20251101-v1:0` | Claude Opus 4.5 | 图片 | HTTPS (`url`) |

### 阿里百炼 (Qwen)

| 模型 ID | 名称 | 视觉能力 | URL 类型 |
|---------|------|---------|---------|
| `qwen-vl-max-latest` | Qwen VL Max | 图片+视频 | HTTPS (`url`) |
| `qwen-vl-plus-latest` | Qwen VL Plus | 图片+视频 | HTTPS (`url`) |
| `qwen-vl-ocr-latest` | Qwen VL OCR | 图片 | HTTPS (`url`) |

### 火山引擎 (Doubao)

| 模型 ID | 名称 | 视觉能力 | URL 类型 |
|---------|------|---------|---------|
| `bmc-Doubao-Seed-1.6` | Doubao Seed 1.6 | 图片+视频 | HTTPS (`url`) |
| `bmc-Doubao-Seed-1.6-thinking` | Doubao Seed 1.6 Thinking | 图片+视频 | HTTPS (`url`) |
| `Doubao-1.5-vision-pro` | Doubao 1.5 Vision Pro | 图片+视频 | HTTPS (`url`) |
| `Doubao-1.5-vision-pro-32k` | Doubao 1.5 Vision Pro 32K | 图片 | HTTPS (`url`) |

---

## 文本模型

以下模型仅支持文本处理，不支持视觉分析。

| 模型 ID | 名称 | 平台 |
|---------|------|------|
| `o1-mini` | O1 Mini | Azure OpenAI |
| `o3-mini` | O3 Mini | Azure OpenAI |
| `qwen-max` | Qwen Max | 阿里百炼 |
| `qwen-plus` | Qwen Plus | 阿里百炼 |
| `qwen-turbo-0624` | Qwen Turbo | 阿里百炼 |
| `qwen2.5-72b-instruct` | Qwen 2.5 72B | 阿里百炼 |
| `qwen3-235b-a22b` | Qwen 3 235B | 阿里百炼 |
| `DeepSeek-R1` | DeepSeek R1 | 火山引擎 |
| `DeepSeek-R1-Distill-Qwen-32B` | DeepSeek R1 Distill | 火山引擎 |
| `DeepSeek-V3` | DeepSeek V3 | 火山引擎 |
| `Doubao-pro-128k` | Doubao Pro 128K | 火山引擎 |
| `Doubao-pro-32k` | Doubao Pro 32K | 火山引擎 |
| `Doubao-lite-128k` | Doubao Lite 128K | 火山引擎 |

---

## Embedding 模型

用于文本向量化，不用于数据标注任务。

| 模型 ID | 名称 | 平台 |
|---------|------|------|
| `text-embedding-3-large` | Text Embedding 3 Large | Azure OpenAI |
| `text-embedding-3-small` | Text Embedding 3 Small | Azure OpenAI |
| `text-embedding-ada-002` | Text Embedding Ada 002 | Azure OpenAI |

---

## URL 类型选择指南

| 模型平台 | URL 字段 | 格式示例 |
|---------|---------|---------|
| **Google Gemini** | `gcp_url` | `gs://bucket/path/image.jpg` |
| **其他所有模型** | `url` | `https://cdn.example.com/image.jpg` |

### Media Storage 输出示例

使用 `--output-format datahub` 时，Media Storage 会同时输出两种 URL：

```json
{
  "media_id": "media-xxx",
  "file_name": "image.jpg",
  "media_type": "IMAGE",
  "url": "https://cdn.example.com/image.jpg",
  "gcp_url": "gs://bucket/path/image.jpg"
}
```

### Prompt 中引用

```
# OpenAI/Claude/Qwen/Doubao 模型
分析图片内容：{{url}}

# Gemini 模型
分析图片内容：{{gcp_url}}
```

---

## 获取最新模型列表

```python
import requests

API_KEY = "your-api-key"
BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"

# 获取所有模型
response = requests.get(
    f"{BASE_URL}/api/v1/model/list",
    headers={"Authorization": f"Bearer {API_KEY}"}
)
models = response.json()["data"]["models"]

# 只获取支持视觉的模型
response = requests.get(
    f"{BASE_URL}/api/v1/model/list?only_vision=true",
    headers={"Authorization": f"Bearer {API_KEY}"}
)

# 只获取支持图片的模型
response = requests.get(
    f"{BASE_URL}/api/v1/model/list?vision_type=image",
    headers={"Authorization": f"Bearer {API_KEY}"}
)
```
