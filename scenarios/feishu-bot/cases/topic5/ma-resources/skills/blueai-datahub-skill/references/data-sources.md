# DataHub Data Sources Configuration

DataHub 支持四种数据源类型，每种类型有对应的配置方式。

## Data Source Options

数据源必须提供以下三者之一：

| Option | Description |
|--------|-------------|
| `data_source_id` | 上传文件返回的数据源 ID |
| `data_source_url` | 飞书多维表格链接或数据源下载链接 |
| `data_source_text` | 直接传入 JSON 数据数组 |

---

## 1. LocalFileRunConfig - 本地文件

用于配置 Excel、CSV、JSON 文件的解析方式。

### Configuration

```json
{
  "data_source": {
    "data_source_id": "ds-xxx",
    "local_file_run_config": {
      "header_row_number": 1,
      "sheet_name": "Sheet1"
    }
  }
}
```

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `header_row_number` | integer | No | 1 | 表头行号 |
| `sheet_name` | string | No | 第一个 sheet | Excel 工作表名称 |

### Supported Formats

- Excel: `.xlsx`, `.xls`
- CSV: `.csv`
- JSON: `.json`

### Example: Upload and Create Task

```python
import os
import requests

API_KEY = "your-api-key"
BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"

# Step 1: Upload file (multipart/form-data)
def upload_file(file_path):
    file_name = os.path.basename(file_path)
    url = f"{BASE_URL}/api/v1/file/upload?file_name={file_name}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    with open(file_path, "rb") as f:
        files = {"file": (file_name, f)}
        response = requests.post(url, headers=headers, files=files)
    return response.json()["data"]["data_source_id"]

# Step 2: Create task with local file config
def create_task_with_file(data_source_id, prompt):
    url = f"{BASE_URL}/api/v1/task"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "data_source": {
            "data_source_id": data_source_id,
            "local_file_run_config": {
                "header_row_number": 1,
                "sheet_name": "数据"
            }
        },
        "prompt": {
            "prompt_text": prompt
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

# Usage
data_source_id = upload_file("/path/to/data.xlsx")
result = create_task_with_file(data_source_id, "请分析：{{内容}}")
```

---

## 2. ConnectorRunConfig - 社交媒体采集

用于从社交媒体平台采集数据。

### Supported Platforms

| MediaTypeEnum | Platform |
|---------------|----------|
| `MEDIA_TYPE_RED_NOTE` | 小红书 |
| `MEDIA_TYPE_TIKTOK` | 抖音 |
| `MEDIA_TYPE_BILI_BILI` | B站 |
| `MEDIA_TYPE_WEIBO` | 微博 |
| `MEDIA_TYPE_WEIXIN_PUBLIC_ACCOUNT` | 微信公众号 |
| `MEDIA_TYPE_WEIXIN_VIDEO_ACCOUNT` | 微信视频号 |
| `MEDIA_TYPE_JINGDONG` | 京东 |
| `MEDIA_TYPE_TAOBAO` | 淘宝 |

### Configuration

```json
{
  "data_source": {
    "connector_run_config": {
      "media_type": "MEDIA_TYPE_RED_NOTE",
      "data_type": "MEDIA_DATA_TYPE_POST",
      "content_type": "MEDIA_CONTENT_TYPE_TEXT_IMAGE",
      "match_type": "MEDIA_MATCH_TYPE_TITLE",
      "keywords": "美妆+护肤",
      "exclude_keywords": "广告",
      "start_time": "2024-01-01 00:00:00",
      "end_time": "2024-01-31 23:59:59",
      "min_interaction": 100,
      "max_interaction": 10000,
      "sort_type": "MEDIA_SORT_TYPE_TIME_DESC",
      "limit": 100
    }
  }
}
```

### Required Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `keywords` | string | 关键词，`+` 表示且关系，`\|` 表示或关系 |
| `start_time` | string | 查询开始时间 (yyyy-MM-dd HH:mm:ss) |
| `end_time` | string | 查询结束时间 (yyyy-MM-dd HH:mm:ss) |

### Optional Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `media_type` | enum | 媒体平台类型 |
| `data_type` | enum | 媒体数据类型 |
| `content_type` | enum | 媒体内容类型 |
| `match_type` | enum | 媒体匹配方式 |
| `exclude_keywords` | string | 排除词 |
| `min_interaction` | integer | 最小互动量 |
| `max_interaction` | integer | 最大互动量 |
| `sort_type` | enum | 排序类型 |
| `limit` | integer | 数量限制 |
| `timeliness` | integer | 0=离线, 1=在线 |

### Data Type Options

| MediaDataTypeEnum | Description |
|-------------------|-------------|
| `MEDIA_DATA_TYPE_POST` | 帖子 |
| `MEDIA_DATA_TYPE_COMMENT` | 评论 |
| `MEDIA_DATA_TYPE_POST_COMMENT_MUST_HAVE_COMMENT` | 帖子+评论（必须有评论） |
| `MEDIA_DATA_TYPE_POST_COMMENT_CAN_NO_COMMENT` | 帖子+评论（可无评论） |
| `MEDIA_DATA_TYPE_POST_LIST` | 帖子列表 |
| `MEDIA_DATA_TYPE_POST_LIST_AND_DETAIL` | 帖子列表+详情 |
| `MEDIA_DATA_TYPE_VIDEO` | 视频 |

### Sort Type Options

| MediaSortTypeEnum | Description |
|-------------------|-------------|
| `MEDIA_SORT_TYPE_TIME_ASC` | 时间升序 |
| `MEDIA_SORT_TYPE_TIME_DESC` | 时间降序 |
| `MEDIA_SORT_TYPE_INTERACTION_ASC` | 互动量升序 |
| `MEDIA_SORT_TYPE_INTERACTION_DESC` | 互动量降序 |
| `MEDIA_SORT_TYPE_COMPREHENSIVE` | 综合排序 |
| `MEDIA_SORT_TYPE_LATEST` | 最新 |
| `MEDIA_SORT_TYPE_HOT` | 最热 |

### Example: 采集小红书数据

```python
import requests

API_KEY = "your-api-key"
BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"

def collect_xiaohongshu_data():
    url = f"{BASE_URL}/api/v1/task"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "name": "小红书美妆数据采集",
        "data_source": {
            "connector_run_config": {
                "media_type": "MEDIA_TYPE_RED_NOTE",
                "data_type": "MEDIA_DATA_TYPE_POST",
                "keywords": "美妆+护肤",
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-01-31 23:59:59",
                "sort_type": "MEDIA_SORT_TYPE_INTERACTION_DESC",
                "limit": 100
            }
        },
        "prompt": {
            "prompt_text": "请分析这篇帖子的主题和情感：{{content}}"
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()
```

---

## 3. DBRunConfig - 数据库

用于从 MySQL 数据库获取数据。

### Configuration

```json
{
  "data_source": {
    "data_source_id": "db-connection-id",
    "db_run_config": {
      "table_name": "users",
      "primary_key": "id",
      "sql": "status = 'active' AND created_at > '2024-01-01'"
    }
  }
}
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sql` | string | Yes | SQL 语句或 WHERE 条件 |
| `table_name` | string | No | 表名 |
| `primary_key` | string | No | 主键字段 |

### SQL Usage

- **table_name 为空时**：`sql` 是完整的 SELECT 语句
- **table_name 不为空时**：`sql` 是 WHERE 条件

### Example: Complete SELECT

```json
{
  "data_source": {
    "data_source_id": "db-xxx",
    "db_run_config": {
      "sql": "SELECT id, name, content FROM articles WHERE status = 'published' LIMIT 100"
    }
  }
}
```

### Example: WHERE Condition

```json
{
  "data_source": {
    "data_source_id": "db-xxx",
    "db_run_config": {
      "table_name": "articles",
      "primary_key": "id",
      "sql": "status = 'published' AND created_at > '2024-01-01'"
    }
  }
}
```

---

## 4. BIRunConfig - 飞书多维表格

用于从飞书多维表格读取数据，并支持结果回写。

### Configuration

```json
{
  "data_source": {
    "data_source_url": "https://bluefocus.feishu.cn/wiki/xxx?table=tblxxx&view=vewxxx",
    "bi_run_config": {
      "app_token": "xxx",
      "table_id": "tblxxx",
      "view_id": "vewxxx",
      "write_back_map": [
        {
          "json_result_key": "sentiment",
          "bi_column_name": "情感分析结果"
        },
        {
          "json_result_key": "summary",
          "bi_column_name": "内容摘要"
        }
      ]
    }
  }
}
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `app_token` | string | Yes | 飞书多维表格 App Token |
| `table_id` | string | Yes | 表格 ID |
| `view_id` | string | No | 视图 ID |
| `write_back_map` | array | No | 回写映射配置 |

### Write Back Map

用于将 LLM 结果回写到飞书多维表格的指定列：

| Field | Description |
|-------|-------------|
| `json_result_key` | LLM 返回 JSON 中的 key |
| `bi_column_name` | 飞书多维表格的列名 |

### Example: 带回写的飞书表格任务

```python
import requests

API_KEY = "your-api-key"
BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"

def create_feishu_task():
    url = f"{BASE_URL}/api/v1/task"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "name": "飞书表格数据分析",
        "data_source": {
            "data_source_url": "https://bluefocus.feishu.cn/wiki/xxx?table=tblxxx&view=vewxxx",
            "bi_run_config": {
                "app_token": "xxx",
                "table_id": "tblxxx",
                "view_id": "vewxxx",
                "write_back_map": [
                    {
                        "json_result_key": "sentiment",
                        "bi_column_name": "情感分析"
                    }
                ]
            }
        },
        "prompt": {
            "prompt_text": "请分析以下内容的情感倾向，返回JSON格式：{\"sentiment\": \"positive/negative/neutral\"}。内容：{{内容}}",
            "prompt_run_config": {
                "require_llm_result_json": true
            }
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()
```

---

## 5. data_source_text - 直接传入数据

用于直接传入 JSON 格式的数据，无需上传文件。

### Configuration

```json
{
  "data_source": {
    "data_source_text": [
      {"id": 1, "content": "第一条数据"},
      {"id": 2, "content": "第二条数据"},
      {"id": 3, "content": "第三条数据"}
    ]
  }
}
```

### Example

```python
import requests

API_KEY = "your-api-key"
BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"

def create_task_with_data():
    url = f"{BASE_URL}/api/v1/task"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "name": "直接数据处理",
        "data_source": {
            "data_source_text": [
                {"id": 1, "text": "这是一条正面评论"},
                {"id": 2, "text": "这是一条负面评论"},
                {"id": 3, "text": "这是一条中性评论"}
            ]
        },
        "prompt": {
            "prompt_text": "请分析以下文本的情感：{{text}}"
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()
```
