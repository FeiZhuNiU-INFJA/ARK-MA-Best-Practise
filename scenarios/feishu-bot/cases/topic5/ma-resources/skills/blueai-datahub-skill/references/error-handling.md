# DataHub API Error Handling

## Error Response Format

所有错误响应都遵循统一格式：

```json
{
   "code": <error_code>,
   "message": "<error_message>",
   "data": null
}
```

---

## Error Codes

| Code | Name | Description | Action |
|------|------|-------------|--------|
| 0 | SUCCESS | 请求成功 | - |
| 9001 | BadRequest | 请求参数错误 | 检查请求体格式和必填字段 |
| 1003 | Unauthorized | 无效的 API Key | 检查 Authorization 头 |
| 2000 | NotFound | 资源不存在或无权限查看 | 确认 ID 正确且有访问权限 |
| 9099 | InternalError | 内部服务错误 | 重试或联系支持 |

---

## Common Errors and Solutions

### 9001 - 请求参数错误

**常见原因：**

1. **缺少必填字段**
   ```json
   // 错误：缺少 data_source
   {
     "prompt": {"prompt_text": "..."}
   }

   // 正确
   {
     "data_source": {"data_source_id": "ds-xxx"},
     "prompt": {"prompt_text": "..."}
   }
   ```

2. **Prompt 缺少占位符**
   ```json
   // 错误：没有 {{}} 占位符
   {
     "prompt": {"prompt_text": "请分析内容"}
   }

   // 正确
   {
     "prompt": {"prompt_text": "请分析：{{content}}"}
   }
   ```

3. **占位符格式错误**
   ```json
   // 错误：使用了空格
   {
     "prompt": {"prompt_text": "请分析：{{ content }}"}
   }

   // 正确
   {
     "prompt": {"prompt_text": "请分析：{{content}}"}
   }
   ```

4. **数据源配置冲突**
   ```json
   // 错误：同时提供多个数据源
   {
     "data_source": {
       "data_source_id": "ds-xxx",
       "data_source_url": "https://..."
     }
   }

   // 正确：只提供一个
   {
     "data_source": {
       "data_source_id": "ds-xxx"
     }
   }
   ```

### 1003 - 无效的 API Key

**常见原因：**

1. **缺少 Authorization 头**
   ```bash
   # 错误
   curl -X POST 'https://bmc-data-hub.bluemediagroup.cn/api/v1/task' \
     -H 'Content-Type: application/json' \
     -d '...'

   # 正确
   curl -X POST 'https://bmc-data-hub.bluemediagroup.cn/api/v1/task' \
     -H 'Authorization: Bearer <your-api-key>' \
     -H 'Content-Type: application/json' \
     -d '...'
   ```

2. **Bearer 格式错误**
   ```python
   # 错误
   headers = {"Authorization": "your-api-key"}
   headers = {"Authorization": "bearer your-api-key"}

   # 正确
   headers = {"Authorization": "Bearer your-api-key"}
   ```

3. **API Key 已过期或被禁用**
   - 联系管理员重新生成 API Key

### 2000 - 资源不存在

**常见原因：**

1. **Task ID 错误**
   ```python
   # 确认 task_id 是创建任务时返回的
   response = create_task(...)
   task_id = response["data"]["task_id"]  # 使用这个 ID
   ```

2. **无权限查看**
   - 确认 API Key 有访问该任务的权限
   - 检查任务的 user_emails 是否包含当前用户

3. **Data Source ID 不存在**
   - 文件上传后立即使用返回的 data_source_id
   - 检查 ID 是否拼写正确

### 9099 - 内部服务错误

**处理方式：**

1. **重试策略**
   ```python
   import time

   def request_with_retry(func, max_retries=3, delay=5):
       for attempt in range(max_retries):
           try:
               result = func()
               if result.get("code") != 9099:
                   return result
           except Exception as e:
               print(f"请求失败: {e}")

           if attempt < max_retries - 1:
               print(f"等待 {delay} 秒后重试...")
               time.sleep(delay)
               delay *= 2  # 指数退避

       return {"code": 9099, "message": "多次重试后仍然失败"}
   ```

2. **联系支持**
   - 记录请求时间和参数
   - 联系技术支持提供错误详情

3. **`POLICY_IP_DENIED` 是服务端问题，不要排查本地**（2026-08-11 实测）

   报错形如：
   ```
   code: 9099
   get model deployment list: rpc error: code = Unauthenticated
   flowctrl auth failed: stage=validate reason=POLICY_IP_DENIED
   cred_type=central ip=10.80.80.9
   ```

   报错里的 `10.80.x.x` 是 **DataHub 自己的 Pod 内网地址**，不是调用方出口 IP ——
   是 DataHub 后端的 central 凭据在向模型网关取"模型部署列表"时被网关的 IP 白名单拦下。
   已验证与模型无关（换 3 个模型报错完全一致）、与本地代理无关，且 `GET /api/v1/model/list` 正常，
   说明只有建任务这条网关链路被封。

   所以遇到这个报错**不要**再查本地代理、API Key 或 payload —— 都已排除，直接找平台侧放行 Pod 网段。
   期间已上传的 `data_source_id` 仍然有效，恢复后可直接复用，不用重新上传。

---

## 实测陷阱（不报错，但结果是错的）

这些不会返回错误码，所以最容易被当成"跑成功了"。

### 任务会永久挂在 `RUNNING`，既不推进也不失败（2026-08-12 实测）

task 4369 处理到第 12/100 行后停住，之后 15 分钟内 `unprocessed_count` 恒为 88、零增长，
但 `task_status` 始终是 `TASK_STATUS_RUNNING`，`failed_count=0`、`error_message` 为空。
平台既不继续处理，也不把状态翻成 `TASK_STATUS_FAILED`。

后果是**轮询逻辑会一直干等到超时**（本例设了 1800s），而这段等待毫无意义。
同批次、同模型、同样 100 条的 task 4368 是完整跑完的（13331 tokens / $0.006822），
说明不是数据量或模型的问题，是服务端挂死——与 `POLICY_IP_DENIED` 同类，只是这次没有报错。

**判断方法**：连续两次以上查询 `unprocessed_count`，如果数字完全不变（而不是缓慢下降），
就是挂死而非慢。别只看 `task_status`，它在这种情况下没有信息量。
确认挂死后停掉轮询、找平台侧处理，已完成的行可以从 `result_list` 里捞出来用。

### `RUNNING` 时聚合成本字段不回填，要用行级字段自己加

同一次实测中，任务已完成 12 行、行级 `llm_total_tokens` 累加为 1669，
但顶层 `total_tokens` 是 `"0"`、`total_consume` 是 `0`。

所以任务未到终态时，顶层成本字段**不能**当"当前已消耗"来读——它不是"部分成本"，而是压根没算。
需要中途成本就遍历 `result_list` 累加 `llm_total_tokens`，别指望聚合字段。

### 平台没有任务列表接口

只有 `GET /api/v1/task/{task_id}`（单个任务），**没有** `GET /api/v1/task`（列表）—— 后者返回 404。

这意味着"查一下我最近那个任务"在 API 层做不到，必须由用户从控制台提供 task_id。
不要试图遍历或猜测 task_id 去找任务，那既拿不到自己的任务，也会撞上别人的权限。

### CSV 必须用 `utf-8`，不能用 `utf-8-sig`

带 BOM 写出的 CSV，首列列名会变成 `﻿id` 而不是 `id`。
后果是 Prompt 里的 `{{id}}` 静默匹配不上——任务照常跑完、照常计费，但那一列的值是空的。

生成数据文件时固定用 `encoding="utf-8"`。如果怀疑已经中招，检查返回结果里首列占位符是否被原样输出。

### 任务"成功"不代表每一行都成功

`task_status` 为 `TASK_STATUS_SUCCESS` 只说明任务跑完了。必须同时看
`failed_count` / `empty_result_count` / `unprocessed_count`，否则会把不完整的结果当成完整结果，
成本也会对不上（费用只覆盖实际处理的行）。

### `result_url` 只在 `TASK_STATUS_GENERATED_RESULT` 时有值

终态是 `TASK_STATUS_SUCCESS` 时没有导出文件，`result_url` 为空——这是正常的，不是失败。
需要文件就在创建任务时加 `export_all_result=true`，或者分页读 `result_list` 自己落盘。

---

## Error Handling Best Practices

### Python Error Handler

```python
import requests

class DataHubError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"DataHub Error [{code}]: {message}")


class DataHubClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://bmc-data-hub.bluemediagroup.cn"

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        url = f"{self.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"

        response = requests.request(method, url, headers=headers, **kwargs)

        try:
            result = response.json()
        except ValueError:
            raise DataHubError(9099, "Invalid JSON response")

        if result.get("code") != 0:
            raise DataHubError(result.get("code"), result.get("message"))

        return result

    def create_task(self, data_source: dict, prompt: str) -> int:
        result = self._request(
            "POST",
            "/api/v1/task",
            json={
                "data_source": data_source,
                "prompt": {"prompt_text": prompt}
            }
        )
        return result["data"]["task_id"]

    def get_task(self, task_id: int) -> dict:
        return self._request("GET", f"/api/v1/task/{task_id}")


# Usage
client = DataHubClient("your-api-key")

try:
    task_id = client.create_task(
        {"data_source_id": "ds-xxx"},
        "请分析：{{content}}"
    )
    print(f"任务创建成功: {task_id}")
except DataHubError as e:
    if e.code == 9001:
        print(f"请求参数错误: {e.message}")
    elif e.code == 1003:
        print("API Key 无效，请检查配置")
    elif e.code == 2000:
        print(f"资源不存在: {e.message}")
    else:
        print(f"服务错误: {e.message}")
```

### Validation Before Request

```python
def validate_prompt(prompt_text: str) -> bool:
    """验证 Prompt 是否包含有效占位符"""
    import re
    pattern = r'\{\{[a-zA-Z_\u4e00-\u9fa5][a-zA-Z0-9_\u4e00-\u9fa5]*\}\}'
    return bool(re.search(pattern, prompt_text))


def validate_data_source(data_source: dict) -> bool:
    """验证数据源配置是否有效"""
    valid_keys = ["data_source_id", "data_source_url", "data_source_text"]
    provided = [k for k in valid_keys if data_source.get(k)]
    return len(provided) == 1  # 必须且只能提供一个


# Usage
prompt = "请分析：{{content}}"
if not validate_prompt(prompt):
    print("错误：Prompt 必须包含 {{变量名}} 格式的占位符")

data_source = {"data_source_id": "ds-xxx"}
if not validate_data_source(data_source):
    print("错误：必须提供且仅提供一个数据源")
```

---

## Task Status Error Handling

### Failed Task

```python
def handle_task_status(task_data: dict):
    status = task_data.get("task_status")

    if status == "TASK_STATUS_SUCCESS":
        print("任务成功完成")
        print(f"结果数量: {task_data.get('total_count')}")
        return True

    elif status == "TASK_STATUS_FAILED":
        print("任务执行失败")
        # 检查是否有错误信息
        return False

    elif status == "TASK_STATUS_RUNNING":
        print("任务正在执行中...")
        return None

    elif status == "TASK_STATUS_PENDING":
        print("任务等待执行...")
        return None

    elif status in ["TASK_STATUS_STOP", "TASK_STATUS_CANCEL"]:
        print("任务已停止或取消")
        return False

    else:
        print(f"未知状态: {status}")
        return None
```

---

## File Upload Error Handling

```python
def upload_file_safe(file_path: str, api_key: str) -> dict:
    """安全的文件上传函数（使用 multipart/form-data）"""
    import os

    # 1. 检查文件存在
    if not os.path.exists(file_path):
        return {"success": False, "error": f"文件不存在: {file_path}"}

    # 2. 检查文件大小（假设限制 100MB）
    file_size = os.path.getsize(file_path)
    if file_size > 100 * 1024 * 1024:
        return {"success": False, "error": "文件大小超过限制 (100MB)"}

    # 3. 检查文件类型
    valid_extensions = [".xlsx", ".xls", ".csv", ".json"]
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in valid_extensions:
        return {"success": False, "error": f"不支持的文件类型: {ext}"}

    # 4. 上传文件
    file_name = os.path.basename(file_path)
    url = f"https://bmc-data-hub.bluemediagroup.cn/api/v1/file/upload?file_name={file_name}"

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        with open(file_path, "rb") as f:
            files = {"file": (file_name, f)}
            response = requests.post(url, headers=headers, files=files, timeout=300)

        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}"}

        result = response.json()
        if result.get("code") == 0:
            return {
                "success": True,
                "data_source_id": result["data"]["data_source_id"],
                "file_url": result["data"]["file_url"]
            }
        else:
            return {"success": False, "error": result.get("message")}

    except requests.Timeout:
        return {"success": False, "error": "上传超时"}
    except requests.ConnectionError:
        return {"success": False, "error": "网络连接失败"}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

---

## Debugging Checklist

遇到问题时，按以下步骤排查：

1. **检查 API Key**
   - [ ] Authorization 头格式正确 (`Bearer xxx`)
   - [ ] API Key 未过期

2. **检查请求格式**
   - [ ] Content-Type 正确 (`application/json` 或 `application/octet-stream`)
   - [ ] JSON 格式有效

3. **检查必填字段**
   - [ ] data_source 存在且只有一种类型
   - [ ] prompt.prompt_text 包含 `{{变量名}}` 占位符

4. **检查资源 ID**
   - [ ] task_id 是有效的数字
   - [ ] data_source_id 是上传返回的值

5. **检查网络**
   - [ ] 可以访问 `https://bmc-data-hub.bluemediagroup.cn`
   - [ ] 没有防火墙阻止
