#!/usr/bin/env python3
"""阶段 8：热度统一与排序。不调模型。

单平台聚合，所以只有一条口径，没有自由参数：

平台内热度百分位 = 该记录在本平台入选记录里的热度排名百分位（0~100）
事件热度         = 该事件全部成员的百分位峰值

**不做跨平台聚合，也不相加原始热度值。** 不同平台的热度是不同量纲的自造指标，
相加没有意义；即便换成百分位，跨平台相加也只是把「上榜平台多」伪装成「更热」。

事件排名按事件热度降序，同分时按成员数、再按 event_id 打破。

热度从 work/frames.jsonl 读（阶段 00 已把它带进流水线），不再回读原始 CSV——
多一个入口就多一种「这一趟用的是哪份热度」查不清的可能。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import log_run, read_jsonl, write_csv, write_jsonl


def percentiles(values: list[float]) -> dict[float, float]:
    """同值同百分位。用「小于该值的数量 / 总数」，最低分得 0，最高分接近 100。"""
    ordered = sorted(values)
    n = len(ordered)
    out = {}
    for value in set(ordered):
        below = sum(1 for v in ordered if v < value)
        out[value] = round(below / n * 100, 2) if n else 0.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    args = ap.parse_args()

    rd = args.run_dir
    src = rd / "work" / "block_events.jsonl"
    if not src.exists():
        raise SystemExit("缺少 work/block_events.jsonl，先跑 07_block_archive.py")
    events = list(read_jsonl(src))

    frames_path = rd / "work" / "frames_resolved.jsonl"
    if not frames_path.exists():
        frames_path = rd / "work" / "frames.jsonl"
    if not frames_path.exists():
        raise SystemExit("缺少 frames.jsonl，热度靠它带过来")
    frames = {f["record_id"]: f for f in read_jsonl(frames_path)}

    assigned = {m for e in events for m in e["member_record_ids"]}
    platforms = {frames[r].get("platform", "") for r in assigned if r in frames}
    if len(platforms) > 1:
        print(f"本运行目录混了 {len(platforms)} 个平台：{sorted(platforms)}。"
              "百分位会在混合池里算，结果不可比——单平台聚合应该一个平台一个运行目录",
              file=sys.stderr)

    values = [float(frames[r].get("heat") or 0.0) for r in assigned
              if r in frames and frames[r].get("heat") is not None]
    pct = percentiles(values)
    if len(set(values)) <= 1:
        print("热度没有区分度（全部相同或全为 0），排名会退化成按成员数排。"
              "多半是阶段 00 没传 --heat-col", file=sys.stderr)

    record_rows = []
    for rid in sorted(assigned):
        frame = frames.get(rid, {})
        heat = frame.get("heat")
        value = None if heat is None else float(heat)
        record_rows.append({
            "record_id": rid,
            "platform": frame.get("platform", ""),
            "heat_value": "" if value is None else value,
            "平台内热度百分位": "" if value is None else pct.get(value, 0.0),
            "平台内入选总数": len(values),
        })
    pct_of = {r["record_id"]: r["平台内热度百分位"] for r in record_rows}

    for event in events:
        member_pct = [float(pct_of[m]) for m in event["member_record_ids"]
                      if pct_of.get(m) not in ("", None)]
        event["事件热度"] = round(max(member_pct), 2) if member_pct else 0.0
        # 只留前 5 个，成员多的事件把整列打满会让 CSV 没法看
        event["成员百分位"] = sorted((round(v, 2) for v in member_pct), reverse=True)[:5]

    events.sort(key=lambda e: (-e["事件热度"], -e["member_count"], e["event_id"]))
    for rank, event in enumerate(events, 1):
        event["事件排名"] = rank

    write_jsonl(rd / "work" / "block_events_ranked.jsonl", events)
    write_csv(rd / "out" / "热度明细.csv", record_rows,
              ["record_id", "platform", "heat_value", "平台内热度百分位",
               "平台内入选总数"])
    write_csv(rd / "out" / "事件排名.csv",
              [{"事件排名": e["事件排名"], "event_id": e["event_id"],
                "一级_事件名": e.get("event_name") or e["anchor_title"],
                "二级_宣传角度": " / ".join(f["angle"] for f in e.get("facets") or []),
                "事件热度": e["事件热度"],
                "member_count": e["member_count"],
                "成员百分位Top5": "; ".join(str(v) for v in e["成员百分位"]),
                "锚点": e.get("anchor") or "",
                "实例": e.get("series_instance") or ""}
               for e in events],
              ["事件排名", "event_id", "一级_事件名", "二级_宣传角度", "事件热度",
               "member_count", "成员百分位Top5", "锚点", "实例"])

    missing = sum(1 for r in record_rows if r["heat_value"] == "")
    print(f"{len(assigned)} 条归档记录，平台 {sorted(platforms)}，热度缺失 {missing} 条")
    print("热度前 10 个事件：")
    for event in events[:10]:
        print(f"  #{event['事件排名']:>3} 热度{event['事件热度']:>6.1f} "
              f"成员{event['member_count']:>2}  "
              f"{(event.get('event_name') or event['anchor_title'])[:46]}")
    log_run(rd / "run_manifest.json",
            {"stage": "08_rank_events", "events": len(events),
             "platforms": sorted(platforms), "heat_missing": missing})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
