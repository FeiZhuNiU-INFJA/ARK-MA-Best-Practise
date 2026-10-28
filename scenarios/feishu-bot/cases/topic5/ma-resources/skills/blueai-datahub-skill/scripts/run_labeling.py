#!/usr/bin/env python3
"""上传 -> 建任务 -> 轮询到完成 -> 输出结果链接与成本消耗。

用法:
  scripts/run.sh scripts/run_labeling.py --file data.csv --prompt "分析情感：{{content}}"
  scripts/run.sh scripts/run_labeling.py --data-source-id 1322 --prompt-file p.md --model gpt-4o-mini
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sdk import DataHubClient, DataHubError

COST_FIELDS = ("total_count", "total_tokens", "total_consume")


def cost_summary(task):
    """提取成本字段；字段缺失时列出 task 对象里所有疑似成本字段，便于核实真实字段名。"""
    found = {k: task.get(k) for k in COST_FIELDS}
    if all(v is None for v in found.values()):
        suspect = {
            k: v
            for k, v in task.items()
            if any(w in k.lower() for w in ("token", "consum", "cost", "price", "count", "usage"))
        }
        return found, suspect
    return found, None


def main():
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group()
    src.add_argument("--file", help="本地数据文件 (.csv/.xlsx/.xls/.json)")
    src.add_argument("--data-source-id", help="已有数据源 ID，跳过上传")
    p.add_argument("--task-id", type=int, help="只轮询已有任务并输出成本，跳过上传和建任务")
    pr = p.add_mutually_exclusive_group()
    pr.add_argument("--prompt", help="Prompt 文本，占位符 {{列名}}")
    pr.add_argument("--prompt-file", help="从文件读取 Prompt")
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--name", default="数据标注任务")
    p.add_argument("--sheet", help="Excel sheet 名")
    p.add_argument("--vision-fields", nargs="+", help="图片 URL 列名，指定即走视觉任务")
    p.add_argument("--json-result", action="store_true", help="要求 LLM 返回 JSON")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--interval", type=int, default=10)
    args = p.parse_args()

    if not args.task_id and not (args.file or args.data_source_id):
        p.error("需要 --file 或 --data-source-id（或用 --task-id 只轮询已有任务）")
    if not args.task_id and not (args.prompt or args.prompt_file):
        p.error("需要 --prompt 或 --prompt-file")

    prompt = args.prompt
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as f:
            prompt = f.read().rstrip()

    client = DataHubClient()

    if args.task_id:
        task_id = args.task_id
        print(f"[1/1] 轮询已有任务 task_id={task_id}", flush=True)
    else:
        ds_id = args.data_source_id
        if args.file:
            print(f"[1/3] 上传 {args.file}", flush=True)
            ds_id = client.upload_file(args.file)
        print(f"      data_source_id: {ds_id}", flush=True)

        kwargs = {}
        if args.sheet:
            kwargs["data_source"] = {
                "data_source_id": ds_id,
                "local_file_run_config": {"sheet_name": args.sheet},
            }
        if args.vision_fields:
            kwargs["prompt_type"] = "PROMPT_TYPE_VISION"
            kwargs["vision_fields"] = args.vision_fields

        print(f"[2/3] 创建任务 (model={args.model})", flush=True)
        task_id = client.create_task(
            ds_id, prompt, model=args.model, name=args.name,
            require_json=args.json_result, **kwargs,
        )
        print(f"      task_id: {task_id}", flush=True)
        print(f"[3/3] 轮询 (间隔 {args.interval}s, 超时 {args.timeout}s)", flush=True)

    started = time.time()
    last = None

    def on_poll(task):
        nonlocal last
        status = task.get("task_status")
        if status != last:
            print(f"      {int(time.time() - started):>5}s  {status}", flush=True)
            last = status

    try:
        task = client.wait_for_task(task_id, args.timeout, args.interval, callback=on_poll)
    except DataHubError as e:
        print(f"\n任务未完成: {e.message if hasattr(e, 'message') else e}", file=sys.stderr)
        print(f"可稍后用 task_id {task_id} 重新查询。", file=sys.stderr)
        sys.exit(1)

    elapsed = time.time() - started
    found, suspect = cost_summary(task)
    cur = task.get("llm_consume_currency") or ""
    model = (task.get("model") or {}).get("model_id") if isinstance(task.get("model"), dict) else None

    print("\n" + "=" * 56)
    print(f"task_id       : {task_id}")
    print(f"任务名        : {task.get('task_name')}")
    print(f"状态          : {task.get('task_status')}")
    print(f"耗时          : {elapsed:.1f}s")
    print(f"模型          : {model or args.model}")
    print(f"处理条数      : {found['total_count']}")
    print(f"消耗 tokens   : {found['total_tokens']}")
    print(f"成本消耗      : {found['total_consume']} {cur}")
    if found["total_count"] and found["total_consume"]:
        print(f"单条均价      : {float(found['total_consume']) / int(found['total_count']):.6f} {cur}")
    print(
        f"异常统计      : 失败 {task.get('failed_count')} / "
        f"空结果 {task.get('empty_result_count')} / 未处理 {task.get('unprocessed_count')}"
    )
    if task.get("error_message"):
        print(f"错误信息      : {task.get('error_message')}")
    if suspect is not None:
        print(f"\n注意：标准成本字段全为空。task 对象里疑似成本字段：{json.dumps(suspect, ensure_ascii=False)}")
    print("=" * 56)

    url = task.get("result_url")
    if url:
        print(f"\n结果下载：{url}")
    else:
        print("\n结果下载：无（该任务未生成导出文件，状态 TASK_STATUS_SUCCESS）")
        print("           需要文件时创建任务加 export_all_result=true，或分页读 result_list")


if __name__ == "__main__":
    main()
