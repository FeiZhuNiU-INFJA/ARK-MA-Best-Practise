#!/usr/bin/env python3
"""查询已完成任务的成本，并按预设汇率换算为人民币。

用法:
  scripts/run.sh scripts/query_cost.py 4368
  scripts/run.sh scripts/query_cost.py 4368 4369 4370 --rate 6.75
  scripts/run.sh scripts/query_cost.py 4368 --json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sdk import DataHubClient, DataHubError

DEFAULT_RATE = 7.0
TERMINAL = ("TASK_STATUS_SUCCESS", "TASK_STATUS_GENERATED_RESULT")


def to_cny(consume, currency, rate):
    """折算人民币。CNY 原样返回；USD 按汇率换算；其他币种返回 None，避免按错误汇率乱算。"""
    if consume is None:
        return None
    cur = (currency or "USD").upper()
    if cur == "CNY":
        return float(consume)
    if cur == "USD":
        return float(consume) * rate
    return None


def fetch(client, task_id):
    task = client.get_task(task_id, page_num=1, page_size=1)
    consume = task.get("total_consume")
    currency = task.get("llm_consume_currency") or "USD"
    count = task.get("total_count")
    return {
        "task_id": task_id,
        "task_name": task.get("task_name"),
        "task_status": task.get("task_status"),
        "model_id": (task.get("model") or {}).get("model_id"),
        "total_count": count,
        "total_tokens": task.get("total_tokens"),
        "total_consume": consume,
        "currency": currency,
        "failed_count": task.get("failed_count"),
        "empty_result_count": task.get("empty_result_count"),
        "unprocessed_count": task.get("unprocessed_count"),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("task_ids", nargs="+", type=int, help="任务 ID，可传多个")
    p.add_argument("--rate", type=float, default=DEFAULT_RATE,
                   help=f"USD→CNY 汇率，默认预设 {DEFAULT_RATE}（非实时）")
    p.add_argument("--json", action="store_true", dest="as_json", help="输出 JSON")
    args = p.parse_args()

    client = DataHubClient()
    rate_note = "预设值，非实时" if args.rate == DEFAULT_RATE else "手动指定"
    rows, errors = [], []

    for tid in args.task_ids:
        try:
            row = fetch(client, tid)
        except DataHubError as e:
            errors.append((tid, getattr(e, "message", str(e))))
            continue
        row["cny"] = to_cny(row["total_consume"], row["currency"], args.rate)
        rows.append(row)

    if args.as_json:
        print(json.dumps({"rate": args.rate, "rate_source": "preset" if args.rate == DEFAULT_RATE else "manual",
                          "tasks": rows,
                          "errors": [{"task_id": t, "message": m} for t, m in errors]},
                         ensure_ascii=False, indent=2))
        return

    for r in rows:
        print("=" * 58)
        print(f"task_id       : {r['task_id']}  {r['task_name'] or ''}")
        print(f"状态          : {r['task_status']}")
        print(f"模型          : {r['model_id']}")
        print(f"处理条数      : {r['total_count']}")
        print(f"消耗 tokens   : {r['total_tokens']}")
        print(f"成本消耗      : {r['total_consume']} {r['currency']}")
        if r["cny"] is None:
            print(f"人民币        : 无法换算（币种 {r['currency']}，非 USD/CNY，或成本字段为空）")
        else:
            note = "接口原生 CNY，未换算" if r["currency"].upper() == "CNY" \
                else f"汇率 {args.rate} {rate_note}"
            print(f"人民币        : ¥{r['cny']:.4f}   ({note})")
            if r["total_count"]:
                print(f"单条均价      : ¥{r['cny'] / int(r['total_count']):.6f}")
        print(f"异常统计      : 失败 {r['failed_count']} / 空结果 "
              f"{r['empty_result_count']} / 未处理 {r['unprocessed_count']}")
        if r["task_status"] not in TERMINAL:
            print("提醒          : 任务尚未到终态，以上成本可能不完整")

    if len(rows) > 1:
        cny = sum(r["cny"] for r in rows if r["cny"] is not None)
        skipped = [r["task_id"] for r in rows if r["cny"] is None]
        cnt = sum(int(r["total_count"] or 0) for r in rows)
        tok = sum(int(r["total_tokens"] or 0) for r in rows)
        print("=" * 58)
        print(f"合计 {len(rows)} 个任务 : {cnt} 条 / {tok} tokens / ¥{cny:.4f}")
        if skipped:
            print(f"（未计入合计：task {skipped}，币种无法折算）")

    print("=" * 58)
    for tid, msg in errors:
        print(f"task {tid} 查询失败: {msg}", file=sys.stderr)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
