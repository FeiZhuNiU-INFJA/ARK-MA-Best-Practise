"""DataHub API 客户端

封装 DataHub REST API 调用，提供文件上传、任务创建、状态查询和轮询等待功能。
"""

import os
import time
from pathlib import Path
from typing import Optional

import requests


class DataHubError(Exception):
    """DataHub API 错误"""

    def __init__(self, message: str, code: int = None, response: dict = None):
        self.message = message
        self.code = code
        self.response = response
        super().__init__(message)


class DataHubClient:
    """DataHub API 客户端

    示例:
        client = DataHubClient()

        # 一站式执行
        result = client.run_task("data.csv", "分析：{{content}}")

        # 或分步执行
        ds_id = client.upload_file("data.csv")
        task_id = client.create_task(ds_id, "分析：{{content}}")
        result = client.wait_for_task(task_id)
    """

    DEFAULT_BASE_URL = "https://bmc-data-hub.bluemediagroup.cn"

    # 任务状态常量
    STATUS_PENDING = "TASK_STATUS_PENDING"
    STATUS_RUNNING = "TASK_STATUS_RUNNING"
    STATUS_SUCCESS = "TASK_STATUS_SUCCESS"
    STATUS_GENERATED_RESULT = "TASK_STATUS_GENERATED_RESULT"
    STATUS_FAILED = "TASK_STATUS_FAILED"

    # Gemini 模型前缀列表（需要使用 gcp_url / gs:// 链接）
    GEMINI_MODEL_PREFIXES = ("gemini-", "gemini_")

    def __init__(self, api_key: str = None, base_url: str = None):
        """初始化客户端

        Args:
            api_key: API 密钥，默认从环境变量 DATAHUB_API_KEY 获取
            base_url: API 基础 URL，默认为生产环境
        """
        self.api_key = api_key or os.environ.get("DATAHUB_API_KEY")
        if not self.api_key:
            raise DataHubError("未提供 API Key，请设置 DATAHUB_API_KEY 环境变量或传入 api_key 参数")

        self.base_url = (base_url or os.environ.get("DATAHUB_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    @classmethod
    def is_gemini_model(cls, model_id: str) -> bool:
        """判断是否为 Gemini 模型（需要使用 gcp_url / gs:// 链接）"""
        if not model_id:
            return False
        model_lower = model_id.lower()
        return any(model_lower.startswith(prefix) for prefix in cls.GEMINI_MODEL_PREFIXES)

    @classmethod
    def adapt_vision_config_for_model(cls, prompt_config: dict, model_id: str) -> dict:
        """根据模型类型自动调整视觉配置中的 URL 字段

        Gemini 模型需要使用 gcp_url (gs://) 格式，其他模型使用 url (https://)。
        此方法会自动将 vision_fields_names 中的 'url' 替换为 'gcp_url'（仅对 Gemini 模型）。

        Args:
            prompt_config: prompt 配置字典，包含 prompt_text 和 prompt_run_config
            model_id: 模型 ID

        Returns:
            调整后的 prompt 配置
        """
        if not cls.is_gemini_model(model_id):
            return prompt_config

        # 深拷贝避免修改原始配置
        import copy
        config = copy.deepcopy(prompt_config)

        # 调整 vision_fields_names
        run_config = config.get("prompt_run_config", {})
        vision_fields = run_config.get("vision_fields_names", [])

        if vision_fields:
            # 将 'url' 替换为 'gcp_url'
            new_fields = []
            for field in vision_fields:
                if field == "url":
                    new_fields.append("gcp_url")
                else:
                    new_fields.append(field)
            run_config["vision_fields_names"] = new_fields
            config["prompt_run_config"] = run_config

        # 调整 prompt_text 中的 {{url}} 为 {{gcp_url}}
        prompt_text = config.get("prompt_text", "")
        if "{{url}}" in prompt_text:
            config["prompt_text"] = prompt_text.replace("{{url}}", "{{gcp_url}}")

        return config

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """发送 HTTP 请求"""
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, **kwargs)

        try:
            data = response.json()
        except ValueError:
            raise DataHubError(f"无效的 JSON 响应: {response.text}", response.status_code)

        if response.status_code >= 400 or data.get("code", 0) != 0:
            error_msg = data.get("message") or data.get("msg") or f"HTTP {response.status_code}"
            raise DataHubError(error_msg, data.get("code"), data)

        return data

    def upload_file(self, file_path: str) -> str:
        """上传文件到 DataHub

        Args:
            file_path: 本地文件路径（支持相对路径，会自动转换为绝对路径）

        Returns:
            data_source_id: 数据源 ID

        Raises:
            DataHubError: 文件不存在
        """
        path = Path(file_path)

        # 如果是相对路径，尝试使用原始工作目录解析
        if not path.is_absolute():
            original_cwd = os.environ.get("DATAHUB_ORIGINAL_CWD")
            if original_cwd:
                path = Path(original_cwd) / path
            else:
                path = path.resolve()

        if not path.exists():
            raise DataHubError(f"文件不存在: {path}")

        # 使用 multipart/form-data 格式上传
        with open(path, "rb") as f:
            files = {"file": (path.name, f)}
            data = self._request(
                "POST",
                "/api/v1/file/upload",
                params={"file_name": path.name},
                files=files,
            )

        return data["data"]["data_source_id"]

    def list_models(self, only_vision: bool = False, vision_type: str = None) -> list:
        """获取可用模型列表

        Args:
            only_vision: 是否只返回支持视觉的模型
            vision_type: 按视觉类型过滤，可选值: image, video, audio

        Returns:
            模型列表，每个模型包含 model_id, name, platform, vision_support, vision_types 等字段

        Example:
            # 获取所有模型
            models = client.list_models()

            # 只获取支持图片分析的模型
            vision_models = client.list_models(only_vision=True, vision_type="image")

            # 打印模型能力
            client.print_models(vision_models)
        """
        params = {}
        if only_vision:
            params["only_vision"] = "true"
        if vision_type:
            params["vision_type"] = vision_type

        data = self._request("GET", "/api/v1/model/list", params=params)
        return data.get("data", {}).get("models", [])

    def print_models(self, models: list = None, only_vision: bool = False):
        """打印模型列表及其能力

        Args:
            models: 模型列表，为空时自动获取
            only_vision: 是否只显示视觉模型
        """
        if models is None:
            models = self.list_models(only_vision=only_vision)

        print("\n可用模型列表：")
        print("-" * 80)
        print(f"{'模型 ID':<35} {'名称':<20} {'视觉能力':<15} {'URL 类型'}")
        print("-" * 80)

        for m in models:
            model_id = m.get("model_id", "")[:34]
            name = m.get("name", "")[:19]
            vision_types = m.get("vision_types", [])

            if vision_types:
                # 图片/视频/音频
                vision_str = "+".join(["图片" if v == "image" else "视频" if v == "video" else "音频" for v in vision_types])
                # Gemini 需要 gcp_url
                url_type = "gcp_url (gs://)" if model_id.lower().startswith("gemini") else "url (https://)"
            else:
                vision_str = "-"
                url_type = "-"

            print(f"{model_id:<35} {name:<20} {vision_str:<15} {url_type}")

        print("-" * 80)
        print(f"共 {len(models)} 个模型")
        print("\n提示：Gemini 模型需要使用 gcp_url (gs://) 格式的链接，SDK 会自动转换。\n")

    def create_task(
        self,
        data_source_id: str,
        prompt: str,
        model: str = None,
        output_format: str = None,
        prompt_type: str = None,
        vision_fields: list = None,
        require_json: bool = False,
        **kwargs,
    ) -> int:
        """创建 LLM 数据处理任务

        Args:
            data_source_id: 数据源 ID
            prompt: Prompt 模板。文本任务使用 {{变量名}} 引用数据字段；
                    视觉任务无需引用图片 URL，系统会根据 vision_fields 自动处理
            model: 可选，指定模型 ID（如 gpt-4o、gemini-2.5-flash）
                   提示：调用 client.print_models() 可查看所有可用模型及其能力
            output_format: 可选，输出格式要求
            prompt_type: 可选，Prompt 类型（如 PROMPT_TYPE_VISION 用于视觉任务）
            vision_fields: 可选，包含图片 URL 的字段列表（如 ["url"]）
                          使用 Gemini 模型时会自动将 "url" 转换为 "gcp_url"
            **kwargs: 其他任务参数

        Returns:
            task_id: 任务 ID

        Note:
            - 使用 Gemini 模型时，SDK 会自动将 vision_fields 中的 "url" 替换为 "gcp_url"
            - 调用 client.list_models(only_vision=True) 可获取支持视觉的模型列表
            - 调用 client.print_models() 可打印模型能力详情（图片/视频/音频支持）
        """
        # 构建 prompt 配置
        prompt_config = {"prompt_text": prompt}

        if prompt_type:
            prompt_config["prompt_type"] = prompt_type

        if vision_fields:
            prompt_config["prompt_run_config"] = {"vision_fields_names": vision_fields}

        if require_json:
            prompt_config.setdefault("prompt_run_config", {})["require_llm_result_json"] = True

        payload = {
            "data_source": {"data_source_id": data_source_id},
            "prompt": prompt_config,
        }

        if model:
            payload["model"] = {"model_id": model}
        if output_format:
            payload["output_format"] = output_format

        # 合并额外参数（可能覆盖 prompt 和 model）
        payload.update(kwargs)

        # 视觉任务校验：必须提供 vision_fields_names
        final_prompt = payload.get("prompt", {})
        final_prompt_type = final_prompt.get("prompt_type", "")
        final_vision_fields = final_prompt.get("prompt_run_config", {}).get("vision_fields_names", [])

        if final_prompt_type == "PROMPT_TYPE_VISION" and not final_vision_fields:
            raise DataHubError(
                "检测到您正在创建视觉分析任务 (PROMPT_TYPE_VISION)，但未指定图片 URL 列。\n\n"
                "请添加 vision_fields 参数指定数据中包含图片 URL 的列名：\n"
                "  vision_fields=['url']  # 或 ['gcp_url']、['image_url'] 等\n\n"
                "如果您想进行文本处理而非图片分析，请移除 prompt_type 参数。"
            )

        # 从最终 payload 中获取模型 ID，统一进行 Gemini 适配
        final_model_id = None
        if "model" in payload:
            if isinstance(payload["model"], dict):
                final_model_id = payload["model"].get("model_id") or payload["model"].get("model_name")
            elif isinstance(payload["model"], str):
                final_model_id = payload["model"]
                payload["model"] = {"model_id": final_model_id}

        # Gemini 模型自动适配：将 vision_fields_names 中的 "url" 替换为 "gcp_url"
        if final_model_id and self.is_gemini_model(final_model_id):
            payload["prompt"] = self.adapt_vision_config_for_model(payload["prompt"], final_model_id)

        data = self._request("POST", "/api/v1/task", json=payload)
        return data["data"]["task_id"]

    def get_task(self, task_id: int, page_num: int = 1, page_size: int = 100) -> dict:
        """查询任务状态和结果

        Args:
            task_id: 任务 ID
            page_num: 结果分页页码
            page_size: 每页结果数量

        Returns:
            任务详情，包含 task_status、result_url、result_list 等
        """
        data = self._request(
            "GET",
            f"/api/v1/task/{task_id}",
            params={"page_num": page_num, "page_size": page_size},
        )
        return data["data"]

    def is_task_completed(self, task_status: str) -> bool:
        """判断任务是否已完成"""
        return task_status in (self.STATUS_SUCCESS, self.STATUS_GENERATED_RESULT)

    def is_task_failed(self, task_status: str) -> bool:
        """判断任务是否失败"""
        return task_status == self.STATUS_FAILED

    def wait_for_task(
        self,
        task_id: int,
        timeout: int = 300,
        interval: int = 5,
        callback: callable = None,
    ) -> dict:
        """轮询等待任务完成

        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒），默认 300 秒
            interval: 轮询间隔（秒），默认 5 秒
            callback: 可选回调函数，每次轮询时调用 callback(task_data)

        Returns:
            任务详情

        Raises:
            DataHubError: 任务失败或超时
        """
        start_time = time.time()

        while True:
            task_data = self.get_task(task_id)
            status = task_data.get("task_status")

            if callback:
                callback(task_data)

            if self.is_task_completed(status):
                return task_data

            if self.is_task_failed(status):
                error_msg = task_data.get("error_message") or "任务执行失败"
                raise DataHubError(f"任务失败: {error_msg}", response=task_data)

            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise DataHubError(f"任务超时: 等待 {timeout} 秒后仍未完成，当前状态: {status}")

            time.sleep(interval)

    def run_task(
        self,
        file_path: str,
        prompt: str,
        timeout: int = 300,
        interval: int = 5,
        verbose: bool = False,
        **kwargs,
    ) -> dict:
        """一站式执行任务：上传文件 -> 创建任务 -> 等待完成

        Args:
            file_path: 本地文件路径
            prompt: Prompt 模板
            timeout: 超时时间（秒）
            interval: 轮询间隔（秒）
            verbose: 是否打印进度信息
            **kwargs: 传递给 create_task 的额外参数

        Returns:
            任务详情（包含结果）
        """
        if verbose:
            print(f"[1/3] 上传文件: {file_path}")

        data_source_id = self.upload_file(file_path)

        if verbose:
            print(f"[2/3] 创建任务 (data_source_id: {data_source_id})")

        task_id = self.create_task(data_source_id, prompt, **kwargs)

        if verbose:
            print(f"[3/3] 等待任务完成 (task_id: {task_id})")

        def progress_callback(task_data):
            if verbose:
                status = task_data.get("task_status", "UNKNOWN")
                print(f"      状态: {status}")

        result = self.wait_for_task(task_id, timeout, interval, callback=progress_callback)

        if verbose:
            print(f"任务完成!")

        return result