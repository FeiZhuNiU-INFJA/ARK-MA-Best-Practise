---
name: blueai-datahub-skill
description: Use when calling DataHub OPEN API to create LLM data processing tasks, manage data sources, upload files, and query task results. Trigger this skill whenever users mention 数据标注, 批量处理, 情感分析, 文本分类, 实体提取,  LLM批量任务, or want to use AI/LLM to process tabular data (CSV/Excel/JSON) at scale. Also triggers when user wants to analyze/label data from blueai-social-media-data or blueai-media-storage-skill output.
license: MIT
metadata:
  author: BMC Team
  version: "1.4.0"
  domain: api-integration
  role: specialist
  scope: implementation
  output-format: code
  related-skills: blueai-social-media-data, blueai-media-storage-skill
---

# BLUEAI DATAHUB SKILL

DataHub OPEN API 集成专家，专注于 LLM 数据处理任务创建、多数据源管理和社交媒体数据采集。

## Quick Start

所有脚本统一通过 `scripts/run.sh` 执行 —— 它会自动准备虚拟环境并注入 `PYTHONPATH`。
`DATAHUB_API_KEY` 由 MA Vault 作为环境变量注入，禁止写入 skill 目录。
下面的相对路径均相对于 skill 根目录。

```bash
# 跑标注任务：上传 → 建任务 → 轮询到完成 → 打印结果链接与成本
scripts/run.sh scripts/run_labeling.py --file data.csv --prompt "分析情感：{{content}}"

# 事后单独查成本（不重复建任务、不产生费用）
scripts/run.sh scripts/query_cost.py <task_id>
```

> **关键点**：Prompt 中的 `{{content}}` 必须对应数据文件的**列名**。如果你的 CSV 有 `text` 列，就用 `{{text}}`。

### API Key 注入

部署时通过 MA Vault 配置 `DATAHUB_API_KEY`。运行脚本只读取进程环境；缺失时应报错或由上层
orchestrator 走确定性降级。禁止在对话、日志或文件中写入、回显 API Key。

## CRITICAL CONSTRAINTS

**必须遵守以下规则：**

1. **必须执行 API 调用** - 执行数据标注任务时，必须通过 Bash 工具执行 Python/curl 调用 DataHub REST API，禁止模拟或编造结果
2. **禁止编造返回值** - 不允许自行生成 task_id、result_list、data_source_id 等字段，这些必须来自实际的 API 调用
3. **先调用，后分析** - 只有在 API 返回真实结果后，才能对结果进行分析和展示
4. **简洁输出** - 返回结果时只需清晰表达任务结果（task_id、状态、结果文件路径），不要对数据内容进行额外分析
5. **使用异步模式** - 创建任务后立即返回 task_id，使用轮询查询结果，避免超时
6. **URL 展示格式** - 对于包含签名参数的长 URL，使用 Markdown 链接格式：`[下载结果文件](完整URL)`，禁止截断



## When to Use This Skill

- 创建 LLM 数据处理任务（模型任务）
- 上传本地文件（Excel、CSV、JSON）到 DataHub
- 配置数据源（本地文件、数据库、连接器、BI 表格）
- 查询任务执行状态和获取 LLM 处理结果
- 数据标注：使用 LLM 批量标注数据（情感分析、文本分类、实体提取等）
- **对社媒数据进行智能标注**（与 `blueai-social-media-data` skill 无缝集成）



## 上下游 Skill 集成

本 skill 可接 `blueai-social-media-data`（社媒采集）和 `blueai-media-storage-skill`（媒体上传）
的输出，做"采集/上传 → 标注"一站式流程。

用户说"对刚才的数据做标注"、"分析这些帖子"、"识别刚上传的图片"时，读 `references/integrations.md`——
里面有两个 skill 的输出结构、Excel 的 sheet/列名映射（社媒数据分 `posts` / `comments` 两个 Sheet，
建任务必须指定 `sheet_name`，否则默认读第一个 Sheet 会拿错数据），以及可直接改用的 curl 示例。

## Prompt 占位符语法

**核心规则**：`{{变量名}}` 中的变量名必须与数据文件的**列名完全一致**。

```
数据文件 (data.csv):
┌─────────┬──────────────────────┐
│ id      │ content              │
├─────────┼──────────────────────┤
│ 1       │ 这个产品太棒了！     │
│ 2       │ 服务态度很差         │
└─────────┴──────────────────────┘

Prompt 模板:
"请分析以下内容的情感：{{content}}"
                         ↑
                    必须与列名一致
```

**支持多个变量**：`"标题：{{title}}，内容：{{content}}，请分析..."`

## 常用 Prompt 模板



### 情感分析

```
分析以下文本的情感倾向：{{content}}

返回 JSON：{"sentiment": "正面/负面/中性", "confidence": 0.9, "reason": "判断依据"}
```



### 文本分类

```
将以下文本分类到指定类别：{{text}}

可选类别：产品咨询、售后问题、投诉建议、其他

返回 JSON：{"category": "类别名称", "confidence": 0.9}
```



### 实体提取

```
从以下文本中提取关键实体：{{content}}

提取：人名、地点、组织、日期、金额

返回 JSON：{"persons": [], "locations": [], "organizations": [], "dates": [], "amounts": []}
```



### 内容摘要

```
为以下内容生成摘要：{{content}}

返回 JSON：{"summary": "一句话摘要", "keywords": ["关键词1", "关键词2"]}
```



### 视觉类型 - 图片分析

```
请分析这张图片的内容。

标题：{{title}}

返回 JSON：{
  "image_description": "图片内容描述",
  "main_objects": ["物体1", "物体2"],
  "scene_type": "场景类型",
  "quality_score": 1-10
}
```

**视觉 Prompt 配置要点**：

```json
{
  "prompt": {
    "prompt_type": "PROMPT_TYPE_VISION",
    "prompt_text": "分析图片...",
    "prompt_run_config": {
      "vision_fields_names": ["img_url"]
    }
  },
  "model": {"model_id": "gpt-4o"}
}
```

> **注意**：视觉任务的 Prompt 中**不需要**使用 `{{img_url}}` 占位符。系统会根据 `vision_fields_names` 自动读取图片 URL 并传递给视觉模型。Prompt 只需描述分析任务即可。

> 详细的视觉 Prompt 文档请参考 `references/data-labeling.md` 中的"视觉类型 Prompt"章节。



## REST API 概览


| API  | 方法   | 路径                                     | 功能                |
| ---- | ---- | -------------------------------------- | ----------------- |
| 上传文件 | POST | `/api/v1/file/upload?file_name={name}` | 返回 data_source_id |
| 创建任务 | POST | `/api/v1/task`                         | 返回 task_id        |
| 查询任务 | GET  | `/api/v1/task/{task_id}`               | 获取状态和结果           |
| 模型列表 | GET  | `/api/v1/model/list`                   | 获取可用模型及其能力        |


**Base URL**: `https://bmc-data-hub.bluemediagroup.cn`

## 查询可用模型

使用模型列表 API 可以获取所有可用模型及其能力信息，特别是视觉能力支持。

### 基本用法

```python
import requests
import os

BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"
API_KEY = os.environ.get("DATAHUB_API_KEY")

def list_models(only_vision=False, vision_type=None):
    """获取可用模型列表

    Args:
        only_vision: 是否只返回支持视觉的模型
        vision_type: 按视觉类型过滤，可选值: image, video, audio
    """
    url = f"{BASE_URL}/api/v1/model/list"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params = {}

    if only_vision:
        params["only_vision"] = "true"
    if vision_type:
        params["vision_type"] = vision_type

    response = requests.get(url, headers=headers, params=params)
    return response.json()

# 获取所有模型
all_models = list_models()

# 只获取支持视觉的模型
vision_models = list_models(only_vision=True)

# 只获取支持图片理解的模型
image_models = list_models(vision_type="image")
```



### 返回字段说明


| 字段                             | 说明                                |
| ------------------------------ | --------------------------------- |
| `model_id`                     | 模型 ID，用于创建任务                      |
| `name`                         | 模型名称                              |
| `platform`                     | 模型厂商（OpenAI、Anthropic 等）          |
| `vision_support`               | 是否支持视觉理解                          |
| `vision_types`                 | 支持的视觉类型列表：`image`、`video`、`audio` |
| `reasoning_support`            | 是否支持推理（思维链）                       |
| `max_context_length`           | 最大上下文长度                           |
| `input_price` / `output_price` | 价格信息                              |




### 视觉任务模型选择示例

```python
# 1. 查询支持图片理解的模型
response = list_models(vision_type="image")
models = response["data"]["models"]

# 2. 打印可用模型
print("支持图片理解的模型：")
for m in models:
    print(f"  - {m['model_id']}: {m['name']} ({m['platform']})")

# 3. 选择模型创建视觉任务
model_id = models[0]["model_id"]  # 选择第一个

task_payload = {
    "data_source": {"data_source_id": "ds-xxx"},
    "prompt": {
        "prompt_type": "PROMPT_TYPE_VISION",
        "prompt_text": "分析图片内容：{{title}}",
        "prompt_run_config": {
            "vision_fields_names": ["img_url"]
        }
    },
    "model": {"model_id": model_id}
}
```



### Gemini 模型自动适配

SDK 会自动检测 Gemini 模型并切换到 `gcp_url`（gs:// 链接）：

```python
from sdk import DataHubClient

client = DataHubClient()

# 使用 Gemini 模型时，SDK 会自动将 vision_fields 中的 "url" 替换为 "gcp_url"
task_id = client.create_task(
    data_source_id="995",
    prompt="分析这张图片的内容",          # 视觉任务无需 {{url}} 占位符
    model="gemini-2.5-flash",             # Gemini 模型
    prompt_type="PROMPT_TYPE_VISION",
    vision_fields=["url"]                 # 自动转换为 ["gcp_url"]
)
```

> **注意**：数据源中需要同时包含 `url` 和 `gcp_url` 字段。使用 Media Storage 的 `--output-format datahub` 会自动输出这两个字段。



## Python SDK 使用

现成脚本覆盖不了的场景（自定义轮询逻辑、需要读 `result_list` 做行级处理等）直接用 SDK。
API Key 由 MA Vault 注入为 `DATAHUB_API_KEY`。

### 基本用法

```bash
# 通过 run.sh 执行（自动处理虚拟环境与 PYTHONPATH）
scripts/run.sh -c "
from sdk import DataHubClient

client = DataHubClient()

# 一站式执行：上传 + 创建任务 + 等待结果
result = client.run_task(
    'data.csv',                        # 支持相对路径（相对于执行命令的目录）
    '分析情感：{{content}}',           # Prompt（变量名 = 列名）
    verbose=True                       # 显示进度
)

print(f'结果下载：{result[\"result_url\"]}')
"
```



### 查看可用模型

```python
from sdk import DataHubClient

client = DataHubClient()

# 打印所有支持视觉的模型及其能力
client.print_models(only_vision=True)

# 输出示例：
# 可用模型列表：
# --------------------------------------------------------------------------------
# 模型 ID                             名称                 视觉能力         URL 类型
# --------------------------------------------------------------------------------
# gpt-4o                              GPT-4o               图片            url (https://)
# gemini-2.5-flash                    Gemini 2.5 Flash     图片+视频+音频   gcp_url (gs://)
# qwen-vl-max-latest                  Qwen VL Max          图片+视频        url (https://)
# ...
```



### 分步执行

```python
from sdk import DataHubClient, DataHubError

client = DataHubClient()

try:
    # 步骤 1：上传文件（支持相对路径）
    ds_id = client.upload_file("data.csv")

    # 步骤 2：创建任务
    task_id = client.create_task(ds_id, "分析情感：{{content}}")

    # 步骤 3：等待结果
    result = client.wait_for_task(task_id, timeout=600)

    print(f"结果：{result['result_url']}")
except DataHubError as e:
    print(f"错误：{e.message}")
```

SDK 位置：`sdk/client.py`，完整示例：`examples/sentiment_analysis.py`

## Reference Guide


| Topic            | Reference                                 | Load When                                          |
| ---------------- | ----------------------------------------- | -------------------------------------------------- |
| Python SDK       | `sdk/client.py`                           | 需要调用 DataHub API 时直接使用 SDK                         |
| SDK 示例           | `examples/sentiment_analysis.py`          | 参考情感分析等批量标注任务实现                                    |
| 端到端脚本            | `scripts/run_labeling.py`                 | 上传→建任务→轮询到完成，并打印结果链接与成本                            |
| **成本查询**         | `scripts/query_cost.py`                   | 已完成任务的 token / 费用查询与人民币换算（见 Cost Query 章节）         |
| **Models**       | `references/models.md`                    | 选择模型时查看可用模型列表和视觉能力                                 |
| Data Sources     | `references/data-sources.md`              | 配置数据源（本地文件、DB、连接器、BI表格）或社交媒体采集                     |
| Data Labeling    | `references/data-labeling.md`             | 数据标注场景（情感分析、分类、实体提取）的 Prompt 模板                    |
| Vision Prompt    | `references/data-labeling.md#视觉类型-prompt` | 视觉类型任务（图片分析、多模态）的配置和模型选择                           |
| **Integrations** | `references/integrations.md`              | 数据来自社媒采集或媒体上传 skill 的输出时                           |
| Error Handling   | `references/error-handling.md`            | 处理错误码；**以及"不报错但结果是错的"实测陷阱**（无任务列表接口、CSV BOM、部分行失败） |




## Execution Mode


| 模式   | 说明                                   | 推荐场景     |
| ---- | ------------------------------------ | -------- |
| 异步模式 | 创建任务后立即返回 task_id，轮询 `get_task` 获取结果 | 所有场景（推荐） |


- **推荐使用异步模式**：创建任务后立即返回 task_id，通过定期查询任务状态获取结果，**避免超时问题**
- 查询间隔建议 5-10 秒，设置合理的超时时间（如 3600 秒）



## Result Handling

任务完成后通过 `get_task` API 获取结果：

- `task_status` 为 `TASK_STATUS_GENERATED_RESULT` 或 `TASK_STATUS_SUCCESS` 时表示完成
- `result_url` **只在** `TASK_STATUS_GENERATED_RESULT` **时有值**；终态为 `TASK_STATUS_SUCCESS` 时没有导出文件，
需在创建任务时加 `export_all_result=true`，或分页读 `result_list`
- 可通过分页参数 `page_num`、`page_size` 获取 LLM 处理结果详情



## Cost Query（任务成本查询与人民币换算）

任务跑完后查成本，用独立脚本 `query_cost.py`，不重复建任务、不产生额外费用。

```bash
# 单个任务
scripts/run.sh scripts/query_cost.py 4368

# 多个任务（自动汇总）
scripts/run.sh scripts/query_cost.py 4368 4369 4370

# 覆盖汇率
scripts/run.sh scripts/query_cost.py 4368 --rate 6.75

# JSON 输出，供后续程序消费
scripts/run.sh scripts/query_cost.py 4368 --json
```



### 成本字段来源

全部来自 `GET /api/v1/task/{task_id}`：


| 字段                                                          | 说明                         |
| ----------------------------------------------------------- | -------------------------- |
| `total_count`                                               | 实际处理条数                     |
| `total_tokens`                                              | 总 token 消耗                 |
| `total_consume`                                             | 总费用（数值）                    |
| `llm_consume_currency`                                      | 币种，**同一账号下实测出现过 `USD` 和 `CNY` 两种**（文档未列出该字段） |
| `failed_count` / `empty_result_count` / `unprocessed_count` | 行级异常计数                     |


`result_list` 每行另带 `llm_input_tokens` / `llm_output_tokens` / `llm_total_tokens`，可做行级成本归因。

### 注意事项

- **汇率是写死的预设值 7.0，非实时汇率**。输出中会标注「预设值，非实时」或「手动指定」，
需要精确金额时用 `--rate` 传当日汇率。
- **币种不固定**：`llm_consume_currency` 实测同账号既返回过 `USD` 也返回过 `CNY`。
`CNY` 直接按原值显示（标注「接口原生 CNY，未换算」），`USD` 才乘汇率，
其他币种输出「无法换算」而不是按错误汇率算出假数字。多任务合计统一按人民币口径相加。
- **任务状态为成功不代表所有行都成功**：必须同时看 `failed_count` / `empty_result_count` /
`unprocessed_count`，否则会把不完整结果的成本当成完整成本。
- 任务未到终态时脚本会提示「成本可能不完整」。注意**实测顶层 `total_tokens`/`total_consume`
在 `RUNNING` 期间根本不回填（恒为 0），不是"部分成本"**——需要中途成本就遍历 `result_list`
累加 `llm_total_tokens`。
- **`unprocessed_count` 长时间不变说明任务挂死了，不是慢**。平台会把任务永久留在
`TASK_STATUS_RUNNING` 而不翻成 FAILED，轮询会一直干等到超时。判断和处理见
`references/error-handling.md` 的「实测陷阱」章节。
- 与 `run_labeling.py` 完全独立：`run_labeling.py` 负责「上传→建任务→轮询」并顺带打印成本，
`query_cost.py` 只做事后查询和换算，两者互不依赖。



## Constraints



### MUST DO

- **创建任务必须提供数据源** - `data_source_id` 是必填参数，需先通过上传文件 API 获取或使用已有数据源
- **仅支持 CSV、Excel、JSON 文件** - 上传文件格式限制为 `.csv`、`.xlsx`、`.xls`、`.json`
- Prompt 必须包含至少一个 `{{变量名}}` 占位符
- 查询任务结果时使用分页参数处理大数据量
- **必须设置 API Key** - 由 MA Vault 注入 `DATAHUB_API_KEY`，禁止写入 skill 目录
- **自己生成 CSV 时用** `encoding="utf-8"`**，不要** `utf-8-sig` - BOM 会让首列列名变成 ﻿`id`，
占位符静默匹配不上，任务照样跑完照样计费但那列是空的



### MUST NOT DO

- 不要在代码中硬编码 API Key
- 不要在 Prompt 变量名中使用空格或特殊字符
- 不要在 Prompt 中遗漏 `{{变量名}}` 占位符
- 不要同时提供多个数据源类型（如同时提供 `data_source_id` 和 `data_source_url`）
- 不要在任务未完成时就尝试获取结果（先检查 `task_status`）
- **不要试图查询"最近一次任务"** - 平台没有任务列表接口（`GET /api/v1/task` 返回 404），
task_id 只能由用户从控制台提供；不要遍历或猜测 task_id
