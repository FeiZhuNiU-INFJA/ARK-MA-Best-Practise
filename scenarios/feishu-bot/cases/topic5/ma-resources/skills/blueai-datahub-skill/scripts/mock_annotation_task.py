#!/usr/bin/env python3
"""
完整标注流程：创建100条模拟数据 → 上传 → 建任务 → 轮询完成 → 保存返参
"""
import json
import os
import sys
import time
import csv
from pathlib import Path

# Add sdk to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sdk import DataHubClient, DataHubError


def generate_mock_data(count=100):
    """生成 count 条模拟数据，保存为 CSV"""
    data_file = Path(__file__).parent.parent / "mock_data_100.csv"
    
    mock_items = [
        {"id": i, "content": f"这是第{i}条测试文本，需要进行情感分析。内容质量{'很好' if i % 3 == 0 else '一般' if i % 3 == 1 else '较差'}。"}
        for i in range(1, count + 1)
    ]
    
    with open(data_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['id', 'content'])
        writer.writeheader()
        writer.writerows(mock_items)
    
    print(f"✓ 生成 {count} 条模拟数据 → {data_file}")
    return data_file


def run_annotation_task():
    """执行完整标注流程"""
    client = DataHubClient()
    
    # 1. 生成模拟数据
    data_file = generate_mock_data(100)
    
    # 2. 上传文件
    print("\n[上传文件]")
    try:
        ds_id = client.upload_file(str(data_file))
        print(f"✓ 数据源 ID: {ds_id}")
    except DataHubError as e:
        print(f"✗ 上传失败: {e.message}")
        sys.exit(1)
    
    # 3. 创建标注任务
    print("\n[创建标注任务]")
    prompt = """请分析以下文本的情感倾向（正面/负面/中性），并给出置信度和分析原因。

文本内容：{{content}}

返回 JSON 格式：
{
  "sentiment": "正面/负面/中性",
  "confidence": 0.0-1.0的浮点数,
  "reason": "判断依据"
}"""
    
    try:
        task_id = client.create_task(
            ds_id,
            prompt,
            model="gpt-4o-mini"
        )
        print(f"✓ 任务 ID: {task_id}")
    except DataHubError as e:
        print(f"✗ 创建任务失败: {e.message}")
        sys.exit(1)
    
    # 4. 轮询等待完成
    print("\n[轮询等待完成]")
    max_wait = 600  # 10分钟超时
    poll_interval = 5
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            task_info = client.get_task(task_id)
            status = task_info.get("task_status")
            processed = task_info.get("processed_count", 0)
            total = task_info.get("total_count", 100)
            
            print(f"  状态: {status} | 进度: {processed}/{total}", end="\r")
            
            # 检查是否完成
            if status in ["TASK_STATUS_GENERATED_RESULT", "TASK_STATUS_SUCCESS"]:
                print(f"\n✓ 任务完成，状态: {status}")
                break
            elif status == "TASK_STATUS_FAILED":
                print(f"\n✗ 任务失败: {status}")
                sys.exit(1)
            
            time.sleep(poll_interval)
        except DataHubError as e:
            print(f"\n✗ 查询失败: {e.message}")
            sys.exit(1)
    else:
        print(f"\n✗ 轮询超时 ({max_wait}秒)")
        sys.exit(1)
    
    # 5. 获取完整返参
    print("\n[获取完整返参]")
    try:
        # 获取分页结果（首页）
        result = client.get_task(task_id, page_num=1, page_size=10)
        
        # 保存完整返参
        response_file = Path(__file__).parent.parent / f"annotation_response_{task_id}.json"
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        print(f"✓ 完整返参已保存 → {response_file}")
        
        # 关键指标
        print("\n[关键指标]")
        print(f"  任务 ID: {task_id}")
        print(f"  总条数: {result.get('total_count', 0)}")
        print(f"  已处理: {result.get('processed_count', 0)}")
        print(f"  失败数: {result.get('failed_count', 0)}")
        print(f"  空结果: {result.get('empty_result_count', 0)}")
        print(f"  总消耗 token: {result.get('total_tokens', 0)}")
        print(f"  总费用: {result.get('total_consume', 0)} {result.get('llm_consume_currency', 'USD')}")
        print(f"  结果下载: {result.get('result_url', 'N/A')}")
        
        # 打印首条结果示例
        if result.get('result_list') and len(result['result_list']) > 0:
            print("\n[首条结果示例]")
            first_result = result['result_list'][0]
            print(f"  原始数据: {first_result.get('origin_data', {})}")
            print(f"  LLM 结果: {first_result.get('llm_result', 'N/A')}")
        
        # 返回任务 ID 供后续查询
        return task_id, response_file
        
    except DataHubError as e:
        print(f"✗ 获取返参失败: {e.message}")
        sys.exit(1)


if __name__ == "__main__":
    print("=" * 60)
    print("DataHub 批量标注完整流程演示")
    print("=" * 60)
    task_id, response_file = run_annotation_task()
    print("\n" + "=" * 60)
    print(f"✓ 完整流程执行完毕")
    print(f"  任务 ID: {task_id}")
    print(f"  返参文件: {response_file}")
    print("=" * 60)
