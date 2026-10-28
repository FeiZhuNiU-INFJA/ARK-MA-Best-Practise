"""DataHub API Python SDK

简洁的 DataHub API 封装，支持文件上传、任务创建和结果查询。

使用示例:
    from sdk import DataHubClient, DataHubError

    client = DataHubClient()
    try:
        result = client.run_task("data.csv", "分析内容：{{content}}")
    except DataHubError as e:
        print(f"错误: {e.message}")
"""

from .client import DataHubClient, DataHubError

__all__ = ["DataHubClient", "DataHubError"]
__version__ = "1.1.0"