# 数据标注场景指南

本文档较长，按需跳读：

| 章节 | 内容 | 什么时候看 |
|------|------|-----------|
| 概述 / 完整工作流 | 上传 → 标注 → 下载的整体链路 | 第一次做标注任务 |
| Python 端到端示例 | 可直接改的完整代码 | 需要自定义流程，而非用现成脚本 |
| 标注 Prompt 模板 | 情感分析、分类、实体提取等模板 | 写 Prompt 时 |
| 视觉类型 Prompt | 图片/视频分析的配置与模型选择（约 400 行，本文最大章节） | 做视觉任务时 |
| 常见问题 | 占位符不匹配、结果格式异常等 | 结果不对时 |
| 最佳实践 | 批量大小、模型选择、成本控制 | 上量之前 |

## 概述

数据标注是使用 LLM 批量处理数据的典型场景。通过 DataHub API，您可以：
- 上传原始数据文件（Excel/CSV/JSON）
- 使用 LLM 自动执行标注任务
- 下载标注完成的结果文件

## 完整工作流

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   上传文件   │ -> │   创建任务   │ -> │   等待完成   │ -> │   下载结果   │
│ upload_file │    │ create_task │    │  poll task  │    │ result_url  │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

### 步骤说明

1. **上传文件** - 将本地数据文件上传到 DataHub，获取 `data_source_id`
2. **创建任务** - 使用 Prompt 和数据源创建 LLM 处理任务，获取 `task_id`
3. **等待完成** - 轮询任务状态直到 `task_status` 变为 `TASK_STATUS_GENERATED_RESULT`（或 `TASK_STATUS_SUCCESS`）
4. **下载结果** - 使用 `result_url` 下载标注完成的 Excel 文件

## Python 端到端示例

### 完整代码

```python
import os
import time
import requests

# 配置
BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"
API_KEY = os.environ.get("DATAHUB_API_KEY")

def get_headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def upload_file(file_path: str) -> str:
    """上传文件，返回 data_source_id（使用 multipart/form-data）"""
    url = f"{BASE_URL}/api/v1/file/upload"
    headers = get_headers()

    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        response = requests.post(
            url,
            params={"file_name": os.path.basename(file_path)},
            headers=headers,
            files=files
        )

    result = response.json()
    if result.get("code") != 0:
        raise Exception(f"上传失败: {result.get('message')}")

    return result["data"]["data_source_id"]


def create_labeling_task(
    data_source_id: str,
    prompt: str,
    model_id: str = "gpt-4o-mini",
    name: str = "数据标注任务",
    require_json: bool = False
) -> int:
    """创建标注任务，返回 task_id"""
    url = f"{BASE_URL}/api/v1/task"
    headers = get_headers()
    headers["Content-Type"] = "application/json"

    payload = {
        "name": name,
        "data_source": {"data_source_id": data_source_id},
        "prompt": {
            "prompt_text": prompt,
            "prompt_run_config": {"require_llm_result_json": require_json}
        },
        "model": {"model_id": model_id}
    }

    response = requests.post(url, headers=headers, json=payload)
    result = response.json()

    if result.get("code") != 0:
        raise Exception(f"创建任务失败: {result.get('message')}")

    return result["data"]["task_id"]


def wait_for_completion(task_id: int, interval: int = 5, timeout: int = 3600) -> dict:
    """轮询等待任务完成"""
    url = f"{BASE_URL}/api/v1/task/{task_id}"
    headers = get_headers()

    start_time = time.time()

    while True:
        response = requests.get(url, headers=headers)
        result = response.json()

        if result.get("code") != 0:
            raise Exception(f"查询任务失败: {result.get('message')}")

        data = result["data"]
        status = data.get("task_status")

        print(f"任务状态: {status}")

        # TASK_STATUS_GENERATED_RESULT: 已生成结果文件（标注任务终态）
        # TASK_STATUS_SUCCESS: 任务成功（无需导出的任务终态）
        if status == "TASK_STATUS_GENERATED_RESULT":
            return {
                "task_id": task_id,
                "result_url": data.get("result_url"),
                "total_count": data.get("total_count"),
                "total_tokens": data.get("total_tokens"),
                "total_consume": data.get("total_consume")
            }
        elif status == "TASK_STATUS_SUCCESS":
            return {
                "task_id": task_id,
                "result_url": data.get("result_url"),
                "total_count": data.get("total_count"),
                "total_tokens": data.get("total_tokens"),
                "total_consume": data.get("total_consume"),
                "message": "任务成功（无导出文件）"
            }
        elif status in ["TASK_STATUS_FAILED", "TASK_STATUS_CANCELED"]:
            raise Exception(f"任务失败，状态: {status}")

        # 检查超时
        if time.time() - start_time > timeout:
            raise TimeoutError(f"任务超时 ({timeout}秒)")

        time.sleep(interval)


def label_data(
    file_path: str,
    prompt: str,
    model_id: str = "gpt-4o-mini",
    name: str = "数据标注任务"
) -> dict:
    """一键完成数据标注"""
    print(f"1. 上传文件: {file_path}")
    data_source_id = upload_file(file_path)
    print(f"   获得 data_source_id: {data_source_id}")

    print(f"2. 创建标注任务...")
    task_id = create_labeling_task(
        data_source_id=data_source_id,
        prompt=prompt,
        model_id=model_id,
        name=name
    )
    print(f"   获得 task_id: {task_id}")

    print(f"3. 等待任务完成...")
    result = wait_for_completion(task_id)
    print(f"   任务完成!")

    return result


# 使用示例
if __name__ == "__main__":
    result = label_data(
        file_path="评论数据.xlsx",
        prompt="""分析以下评论的情感倾向。

评论内容: {{content}}

请判断情感为：正面、负面、中性""",
        model_id="gpt-4o-mini"
    )

    print(f"\n结果下载链接: {result['result_url']}")
    print(f"处理数据条数: {result['total_count']}")
    print(f"消耗 tokens: {result['total_tokens']}")
```

## 标注 Prompt 模板

### 情感分析

```
分析以下文本的情感倾向。

文本内容: {{content}}

请返回以下格式的 JSON:
{
  "sentiment": "正面/负面/中性",
  "confidence": 0.0-1.0,
  "reason": "判断依据"
}
```

### 文本分类

```
将以下文本分类到指定类别。

文本: {{text}}

可选类别: 产品咨询、售后问题、投诉建议、其他

请返回分类结果和置信度:
{
  "category": "类别名称",
  "confidence": 0.0-1.0
}
```

### 实体提取

```
从以下文本中提取关键实体。

文本: {{content}}

请提取以下类型的实体:
- 人名
- 地点
- 组织
- 日期
- 金额

返回 JSON 格式:
{
  "persons": [],
  "locations": [],
  "organizations": [],
  "dates": [],
  "amounts": []
}
```

### 关键词提取

```
从以下文本中提取关键词。

文本: {{content}}

请提取 3-5 个最重要的关键词，返回 JSON:
{
  "keywords": ["关键词1", "关键词2", ...],
  "summary": "一句话摘要"
}
```

### 质量评估

```
评估以下内容的质量。

内容: {{content}}

请从以下维度评分（1-10分）:
- 准确性
- 完整性
- 可读性
- 专业性

返回 JSON:
{
  "accuracy": 分数,
  "completeness": 分数,
  "readability": 分数,
  "professionalism": 分数,
  "overall": 平均分,
  "feedback": "改进建议"
}
```

## 视觉类型 Prompt

### 概述

视觉类型 Prompt（`PROMPT_TYPE_VISION`）用于处理包含图片的数据，让 LLM 能够"看到"并分析图片内容。这种类型特别适用于：

- **图片内容理解**：识别图片中的物体、场景、文字
- **商品分析**：分析电商商品图、商品属性提取
- **社交媒体图文分析**：小红书/抖音等平台的图文帖子综合分析
- **OCR + 理解**：提取图片中的文字并理解语义
- **图片质量评估**：评估图片清晰度、构图、美观度等

### 工作原理

```
┌─────────────────────────────────────────────────────────────┐
│ 数据文件 (Excel/CSV/JSON)                                    │
├──────────┬──────────────────────┬───────────────────────────┤
│ id       │ title                │ img_url                   │
├──────────┼──────────────────────┼───────────────────────────┤
│ 1        │ 夏日护肤分享         │ https://xxx.com/img1.jpg  │
│ 2        │ 好用的面霜推荐       │ https://xxx.com/img2.jpg  │
└──────────┴──────────────────────┴───────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Vision Prompt 配置                                          │
│                                                             │
│ prompt_type: PROMPT_TYPE_VISION                             │
│ vision_fields_names: ["img_url"]  ← 指定图片 URL 列         │
│ prompt_text: "分析图片和标题：{{title}}"                    │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ LLM 处理                                                    │
│ 1. 下载图片 → 2. 图片 + 文本一起输入模型 → 3. 返回分析结果 │
└─────────────────────────────────────────────────────────────┘
```

### 关键配置字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt_type` | string | 是 | 设置为 `PROMPT_TYPE_VISION` |
| `vision_fields_names` | array | 是 | 包含图片 URL 的列名列表 |
| `prompt_text` | string | 是 | Prompt 文本，可引用其他文本列 |

### Python 完整示例

```python
import os
import time
import requests

BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"
API_KEY = os.environ.get("DATAHUB_API_KEY")

def get_headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def create_vision_task(data_source_id: str) -> int:
    """创建视觉类型任务"""
    url = f"{BASE_URL}/api/v1/task"
    headers = get_headers()
    headers["Content-Type"] = "application/json"

    payload = {
        "name": "图片内容分析任务",
        "data_source": {"data_source_id": data_source_id},
        "prompt": {
            "prompt_type": "PROMPT_TYPE_VISION",  # 关键：指定视觉类型
            "prompt_text": """请分析这张图片的内容。

标题：{{title}}

请返回 JSON 格式：
{
  "image_description": "图片内容描述",
  "main_objects": ["物体1", "物体2"],
  "scene_type": "场景类型",
  "text_in_image": "图片中的文字（如有）",
  "quality_score": 1-10
}""",
            "prompt_run_config": {
                "vision_fields_names": ["img_url"],  # 关键：指定图片 URL 列名
                "require_llm_result_json": True
            }
        },
        "model": {"model_id": "gpt-4o"}  # 必须使用支持视觉的模型
    }

    response = requests.post(url, headers=headers, json=payload)
    result = response.json()

    if result.get("code") != 0:
        raise Exception(f"创建任务失败: {result.get('message')}")

    return result["data"]["task_id"]


def wait_for_completion(task_id: int, interval: int = 5, timeout: int = 3600) -> dict:
    """轮询等待任务完成"""
    url = f"{BASE_URL}/api/v1/task/{task_id}"
    headers = get_headers()
    start_time = time.time()

    while True:
        response = requests.get(url, headers=headers)
        result = response.json()

        if result.get("code") != 0:
            raise Exception(f"查询任务失败: {result.get('message')}")

        data = result["data"]
        status = data.get("task_status")
        print(f"任务状态: {status}")

        if status == "TASK_STATUS_GENERATED_RESULT":
            return {
                "task_id": task_id,
                "result_url": data.get("result_url"),
                "total_count": data.get("total_count")
            }
        elif status in ["TASK_STATUS_FAILED", "TASK_STATUS_CANCELED"]:
            raise Exception(f"任务失败，状态: {status}")

        if time.time() - start_time > timeout:
            raise TimeoutError(f"任务超时 ({timeout}秒)")

        time.sleep(interval)


# 使用示例
if __name__ == "__main__":
    # 假设已上传包含 img_url 列的数据文件
    data_source_id = "ds-xxx"

    task_id = create_vision_task(data_source_id)
    print(f"任务创建成功，task_id: {task_id}")

    result = wait_for_completion(task_id)
    print(f"结果下载链接: {result['result_url']}")
```

### 视觉 Prompt 模板

#### 图片内容理解

```
请仔细分析这张图片的内容。

标题：{{title}}
描述：{{description}}

请返回 JSON 格式：
{
  "image_description": "图片的详细描述",
  "main_objects": ["识别到的主要物体"],
  "colors": ["主要颜色"],
  "scene": "场景类型（室内/室外/产品图等）",
  "mood": "图片传达的情绪或氛围"
}
```

#### 电商商品图分析

```
分析这张商品图片，提取商品信息。

商品标题：{{product_name}}

请返回 JSON：
{
  "product_type": "商品类型",
  "main_features": ["商品特征1", "商品特征2"],
  "color": "颜色",
  "material": "材质（如能判断）",
  "usage_scenario": "使用场景",
  "target_audience": "目标人群",
  "quality_assessment": "图片质量评价"
}
```

#### 社交媒体图文分析

```
请综合分析这篇图文帖子的内容。

帖子标题：{{title}}
帖子正文：{{content}}

请从图片和文字两个维度分析，返回 JSON：
{
  "topic": "帖子主题",
  "image_content": "图片展示的内容",
  "text_image_relevance": "图文相关性（高/中/低）",
  "sentiment": "情感倾向",
  "key_products": ["提到的产品/品牌"],
  "target_audience": "目标受众",
  "content_quality": 1-10
}
```

#### OCR + 内容理解

```
请识别并理解图片中的文字内容。

来源：{{source}}

请返回 JSON：
{
  "extracted_text": "识别出的所有文字",
  "text_type": "文字类型（标题/正文/表格/手写等）",
  "language": "文字语言",
  "key_information": ["关键信息点"],
  "summary": "内容摘要"
}
```

#### 图片质量评估

```
评估这张图片的质量和视觉效果。

用途：{{purpose}}

请返回 JSON：
{
  "clarity_score": 1-10,
  "composition_score": 1-10,
  "lighting_score": 1-10,
  "aesthetic_score": 1-10,
  "overall_score": 1-10,
  "issues": ["存在的问题"],
  "suggestions": ["改进建议"]
}
```

#### 多图对比分析

```
请对比分析这组图片的差异和共同点。

图片组标题：{{group_title}}

请返回 JSON：
{
  "image_count": "图片数量",
  "common_elements": ["共同元素"],
  "differences": ["主要差异"],
  "best_image_index": "最佳图片索引",
  "recommendation": "选择建议"
}
```

### 注意事项

#### 1. 模型选择

| 模型 | 视觉能力 | 推荐场景 |
|------|----------|----------|
| `gpt-4o` | 强 | 复杂图片理解、多模态分析 |
| `gpt-4o-mini` | 强 | 简单图片分析、成本敏感场景 |
| `gpt-4-turbo` | 强 | 需要更长上下文的场景 |
| `gpt-3.5-turbo` | 无 | 不支持视觉，仅限文本 |
| `claude-3-*` | 强 | 可选替代方案（如支持） |

**重要**：必须使用支持视觉的模型，否则图片无法被处理。

##### 通过 API 动态查询支持视觉的模型

推荐使用 **模型列表 API** 动态获取最新的支持视觉的模型，而不是硬编码模型 ID：

```python
import requests
import os

BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"
API_KEY = os.environ.get("DATAHUB_API_KEY")

def get_vision_models(vision_type="image"):
    """获取支持指定视觉类型的模型列表

    Args:
        vision_type: 视觉类型，可选值: image, video, audio
    """
    url = f"{BASE_URL}/api/v1/model/list"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params = {"vision_type": vision_type}

    response = requests.get(url, headers=headers, params=params)
    result = response.json()

    if result.get("code") != 0:
        raise Exception(f"查询失败: {result.get('message')}")

    return result["data"]["models"]


# 获取支持图片理解的模型
image_models = get_vision_models("image")

print("支持图片理解的模型：")
for m in image_models:
    print(f"  - {m['model_id']}: {m['name']}")
    print(f"    视觉类型: {m['vision_types']}")
    print(f"    价格: 输入 {m['input_price']}, 输出 {m['output_price']}")
```

**API 参数说明**：

| 参数 | 说明 |
|------|------|
| `only_vision=true` | 只返回支持视觉的模型 |
| `vision_type=image` | 只返回支持图片理解的模型 |
| `vision_type=video` | 只返回支持视频理解的模型 |
| `vision_type=audio` | 只返回支持音频理解的模型 |

**返回字段**：

| 字段 | 说明 |
|------|------|
| `model_id` | 模型 ID，用于创建任务 |
| `vision_support` | 是否支持视觉 |
| `vision_types` | 支持的视觉类型列表：`["image"]`、`["video"]`、`["image", "video"]` 等 |
| `reasoning_support` | 是否支持推理（思维链） |

#### 2. 图片 URL 要求

- **必须是公网可访问的 URL**：LLM 需要能够下载图片
- **支持的格式**：JPG、PNG、GIF、WebP
- **建议图片大小**：单张 < 20MB
- **URL 有效性**：确保 URL 不会过期（如 OSS 签名 URL 注意有效期）

#### 3. 数据文件格式

```csv
id,title,content,img_url
1,产品测评,这款面霜很好用,https://example.com/img1.jpg
2,穿搭分享,今日OOTD,https://example.com/img2.jpg
```

**关键点**：
- 图片 URL 必须是单独的一列
- 列名要在 `vision_fields_names` 中正确引用
- 支持多个图片列（如 `["img_url_1", "img_url_2"]`）

#### 4. 多图处理

如果一条数据有多张图片，有两种方式：

**方式一：多列存储**
```json
{
  "vision_fields_names": ["img_url_1", "img_url_2", "img_url_3"]
}
```

**方式二：单列存储（逗号分隔）**
```csv
id,title,img_urls
1,产品图集,"https://a.com/1.jpg,https://a.com/2.jpg,https://a.com/3.jpg"
```

```json
{
  "vision_fields_names": ["img_urls"]
}
```

#### 5. 成本考虑

- 视觉任务的 token 消耗通常高于纯文本任务
- 图片越大、越复杂，消耗越高
- 建议先用小样本（10-20条）测试，评估成本后再批量处理
- 使用 `gpt-4o-mini` 可显著降低成本（约为 gpt-4o 的 1/10）

#### 6. 常见错误排查

| 错误现象 | 可能原因 | 解决方案 |
|----------|----------|----------|
| 图片未被分析 | 未设置 `prompt_type` 为 `PROMPT_TYPE_VISION` | 添加正确的 prompt_type |
| 图片字段为空 | `vision_fields_names` 列名拼写错误 | 检查列名是否与数据文件一致 |
| 图片加载失败 | URL 无法访问或已过期 | 确保 URL 公网可访问 |
| 模型报错 | 使用了不支持视觉的模型 | 改用 gpt-4o 或 gpt-4o-mini |
| Token 超限 | 图片过大或数量过多 | 压缩图片或减少单次图片数量 |

#### 7. 最佳实践

1. **明确图片分析目标**：Prompt 中清晰说明需要从图片中提取什么信息
2. **结合文本上下文**：利用 `{{title}}`、`{{description}}` 等文本字段辅助理解
3. **要求结构化输出**：使用 `require_llm_result_json: true` 确保返回格式一致
4. **控制图片数量**：单条数据建议不超过 5 张图片
5. **预处理图片 URL**：确保所有 URL 有效、可访问
6. **分批处理**：大量图片数据建议分批创建任务

## 常见问题

### Q: 如何处理大量数据？

DataHub API 单次任务支持处理大量数据。建议：
- 单个文件不超过 10MB
- 数据量大时使用 `gpt-4o-mini` 以降低成本
- 可通过分页参数获取部分结果进行预览

### Q: 任务通常需要多长时间？

处理时间取决于：
- 数据量大小
- Prompt 复杂度
- 使用的模型

一般估算：1000 条数据约 5-15 分钟

### Q: 如何获取最佳标注效果？

1. **明确的 Prompt** - 清晰描述标注规则和输出格式
2. **示例引导** - 在 Prompt 中提供 1-2 个标注示例
3. **结构化输出** - 使用 `require_json: true` 强制 JSON 格式
4. **合适的模型** - 复杂任务用 `gpt-4o`，简单任务用 `gpt-4o-mini`

### Q: 如何处理标注错误？

1. 检查 Prompt 是否包含正确的占位符 `{{变量名}}`
2. 确认变量名与数据文件列名匹配
3. 使用分页获取结果检查部分样本
4. 调整 Prompt 后重新提交任务

## 最佳实践

### 1. Prompt 设计

- 使用 `{{列名}}` 占位符引用数据
- 明确指定输出格式（JSON 最佳）
- 提供 few-shot 示例提升一致性
- 设置边界条件处理逻辑

### 2. 模型选择

| 场景 | 推荐模型 | 说明 |
|-----|---------|------|
| 简单分类/标签 | gpt-4o-mini | 成本低、速度快 |
| 复杂分析/推理 | gpt-4o | 准确度高 |
| 中文场景 | gpt-4o | 中文理解更好 |

### 3. 成本控制

- 先用小样本（100条）测试 Prompt 效果
- 简单任务优先使用 `gpt-4o-mini`
- 通过 `total_tokens` 和 `total_consume` 监控消耗

### 4. 错误处理

- 实现重试机制处理网络波动
- 设置合理的超时时间
- 保存中间状态以便断点续传
