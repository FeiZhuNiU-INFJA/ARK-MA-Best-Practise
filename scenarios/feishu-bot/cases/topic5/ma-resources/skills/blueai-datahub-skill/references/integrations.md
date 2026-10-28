# 上下游 Skill 集成

本 skill 与两个上游 skill 配合，构成"采集/上传 → 标注"链路。
读这份文档的时机：用户说"对刚才采集的数据做标注"、"分析这些帖子"、"识别刚上传的图片"，
即数据来源不是用户手动给的文件，而是另一个 skill 的输出。

关键在于**从上下文里认出上游输出的结构**，直接取它的文件路径或 URL，
不要反问用户"文件在哪"——路径就在前面的对话里。

---

## 与社媒数据 Skill 集成

本 skill 可与 `blueai-social-media-data` skill 无缝协作，实现"采集 → 标注"一站式流程。

### 自动识别社媒数据输出

**当对话上下文中存在以下结构时，自动识别为社媒数据源**：

```json
{
  "_skill_result": "blueai-social-media-data",
  "status": "success",
  "output_file": "/path/to/task_xxx.xlsx",
  ...
}
```

**触发条件**：用户说"对刚才的数据进行标注"、"分析这些帖子"、"对采集的数据做情感分析"等。

**处理逻辑**：
1. 从上下文提取 `output_file` 路径
2. 自动上传文件到 DataHub 获取 `data_source_id`
3. 使用用户指定的 Prompt 创建标注任务

### 社媒数据列名映射

社媒数据 Excel 包含两个 Sheet，创建任务时需指定 `sheet_name`：

#### Sheet: posts（帖子数据）
| 常用列名 | 说明 | Prompt 占位符 |
|---------|------|--------------|
| `post_title` | 帖子标题 | `{{post_title}}` |
| `post_content` | 帖子正文 | `{{post_content}}` |
| `post_cover_url` | 封面图 URL | 用于视觉分析 |
| `post_pic_urls` | 图片 URL 列表 | 用于视觉分析 |
| `author_user_name` | 作者名称 | `{{author_user_name}}` |
| `post_like_cnt` | 点赞数 | `{{post_like_cnt}}` |
| `post_review_cnt` | 评论数 | `{{post_review_cnt}}` |

#### Sheet: comments（评论数据）
| 常用列名 | 说明 | Prompt 占位符 |
|---------|------|--------------|
| `content` | 评论内容 | `{{content}}` |
| `like_cnt` | 点赞数 | `{{like_cnt}}` |
| `post_id` | 关联帖子 ID | `{{post_id}}` |

### 社媒数据标注示例

**情感分析（帖子）**：
```bash
curl -X POST "https://bmc-data-hub.bluemediagroup.cn/api/v1/task" \
  -H "Authorization: Bearer ${DATAHUB_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "data_source": {
      "data_source_id": "{上传后的ID}",
      "local_file_run_config": {"sheet_name": "posts"}
    },
    "prompt": {
      "prompt_text": "分析以下社媒帖子的情感倾向。\n\n标题：{{post_title}}\n正文：{{post_content}}\n\n返回 JSON：{\"sentiment\": \"正面/负面/中性\", \"confidence\": 0.9}"
    }
  }'
```

**视觉分析（帖子图片）**：
```bash
curl -X POST "https://bmc-data-hub.bluemediagroup.cn/api/v1/task" \
  -H "Authorization: Bearer ${DATAHUB_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "data_source": {
      "data_source_id": "{上传后的ID}",
      "local_file_run_config": {"sheet_name": "posts"}
    },
    "prompt": {
      "prompt_type": "PROMPT_TYPE_VISION",
      "prompt_text": "分析这篇小红书帖子的图文内容。\n\n标题：{{post_title}}\n正文：{{post_content}}\n\n返回 JSON：{\"topic\": \"主题\", \"image_description\": \"图片描述\", \"sentiment\": \"情感\"}",
      "prompt_run_config": {
        "vision_fields_names": ["post_cover_url"]
      }
    },
    "model": {"model_id": "gpt-4o"}
  }'
```

**评论分析**：
```bash
curl -X POST "https://bmc-data-hub.bluemediagroup.cn/api/v1/task" \
  -H "Authorization: Bearer ${DATAHUB_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "data_source": {
      "data_source_id": "{上传后的ID}",
      "local_file_run_config": {"sheet_name": "comments"}
    },
    "prompt": {
      "prompt_text": "分析以下评论的情感和意图。\n\n评论：{{content}}\n\n返回 JSON：{\"sentiment\": \"正面/负面/中性\", \"intent\": \"咨询/吐槽/赞美/其他\"}"
    }
  }'
```

### 端到端工作流示例

```
用户：帮我采集小红书"防晒霜"相关帖子
     ↓
[blueai-social-media-data skill 执行]
     ↓
输出：{"_skill_result": "blueai-social-media-data", "output_file": "/path/to/task_xxx.xlsx", ...}
     ↓
用户：对这些数据进行情感分析
     ↓
[blueai-datahub-skill 自动识别 output_file]
     ↓
1. 上传 /path/to/task_xxx.xlsx → data_source_id
2. 创建任务（默认 sheet_name: posts）
3. 返回 task_id 和结果
```

## 与 Media Storage Skill 集成

本 skill 可与 `blueai-media-storage-skill` 无缝协作，实现"媒体上传 → 视觉分析"一站式流程。

### 自动识别 Media Storage 输出

**当对话上下文中存在以下结构时，自动识别为媒体数据源**：

```json
// 单文件上传结果
{
  "success": true,
  "data": {
    "task_id": "xxx",
    "file_url": "https://...",
    "media_id": "xxx"
  }
}

// 多文件上传结果
{
  "success": true,
  "data": {
    "task_id": "xxx",
    "files": [
      {"file_name": "a.jpg", "file_url": "https://...", "media_id": "m1", "success": true},
      {"file_name": "b.mp4", "file_url": "https://...", "media_id": "m2", "success": true}
    ]
  }
}
```

**触发条件**：用户说"对刚才上传的图片进行分析"、"识别这些视频内容"、"对这些媒体文件做标注"等。

### 处理逻辑

**单文件场景**：
1. 提取 `file_url` 作为视觉分析的输入
2. 创建单条数据的视觉任务

**多文件场景**：
1. 从 `files` 数组提取所有成功上传的 `file_url`
2. 生成临时 CSV/JSON 文件作为数据源
3. 上传到 DataHub 并创建批量视觉任务

### 媒体文件列名映射

从 Media Storage 输出生成的数据源包含以下列：

| 列名 | 说明 | Prompt 占位符 |
|------|------|--------------|
| `file_name` | 文件名 | `{{file_name}}` |
| `file_url` | COS 访问链接 | 用于视觉分析 |
| `gcp_url` | GCP 访问链接 | Gemini 模型使用 |
| `media_id` | 媒体 ID | `{{media_id}}` |

### 媒体文件视觉分析示例

**单文件分析**：
```bash
curl -X POST "https://bmc-data-hub.bluemediagroup.cn/api/v1/task" \
  -H "Authorization: Bearer ${DATAHUB_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "data_source": {
      "data_source_text": [{"url": "https://cos.example.com/video.mp4"}]
    },
    "prompt": {
      "prompt_type": "PROMPT_TYPE_VISION",
      "prompt_text": "分析这个视频的内容，返回 JSON：{\"description\": \"内容描述\", \"tags\": [\"标签\"]}",
      "prompt_run_config": {
        "vision_fields_names": ["url"]
      }
    },
    "model": {"model_id": "gpt-4o"}
  }'
```

**批量图片分析**（从多文件上传结果）：
```bash
# 1. 先将 Media Storage 输出转换为 CSV
# file_name,file_url,media_id
# a.jpg,https://...,m1
# b.jpg,https://...,m2

# 2. 上传 CSV 并创建批量视觉任务
curl -X POST "https://bmc-data-hub.bluemediagroup.cn/api/v1/task" \
  -H "Authorization: Bearer ${DATAHUB_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "data_source": {"data_source_id": "{上传后的ID}"},
    "prompt": {
      "prompt_type": "PROMPT_TYPE_VISION",
      "prompt_text": "分析图片 {{file_name}} 的内容：{\"objects\": [], \"scene\": \"\", \"quality\": 1-10}",
      "prompt_run_config": {
        "vision_fields_names": ["file_url"]
      }
    },
    "model": {"model_id": "gpt-4o"}
  }'
```

### 端到端工作流示例

```
用户：帮我上传这些产品图片到云存储
     ↓
[blueai-media-storage-skill 执行]
     ↓
输出：{"success": true, "data": {"files": [{"file_url": "https://...", "file_name": "product1.jpg"}, ...]}}
     ↓
用户：对这些图片进行产品识别和质量评估
     ↓
[blueai-datahub-skill 自动识别 files 数组]
     ↓
1. 生成临时 CSV（file_name, file_url, media_id）
2. 上传 CSV → data_source_id
3. 创建视觉分析任务（PROMPT_TYPE_VISION）
4. 返回 task_id 和结果
```
