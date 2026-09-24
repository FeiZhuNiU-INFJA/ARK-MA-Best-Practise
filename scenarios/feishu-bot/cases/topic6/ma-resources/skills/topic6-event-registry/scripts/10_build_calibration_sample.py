#!/usr/bin/env python3
"""校准抽样。不调模型。

抽样单位是**事件**，不是记录。要测的是「合并对不对」，而合并只发生在多成员
事件里——按记录随机抽会全落在单成员上，那一类根本没有合并判断。

三张表：
  多成员事件   默认全查，判成员是否真属于同一次发生 → False Merge Rate
  单成员记录   抽查并附上同块的多成员事件，判是否漏并
  非事件记录   按 reason_code 分层抽查 → 误杀率（唯一真正丢数据的判定）
"""

from __future__ import annotations

import argparse
import collections
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import log_run, read_jsonl, write_csv

BANDS = [(0.9, 1.01, "≥0.9"), (0.8, 0.9, "0.8~0.9"),
         (0.7, 0.8, "0.7~0.8"), (-0.01, 0.7, "<0.7")]


def band_of(conf) -> str:
    try:
        value = float(conf)
    except (TypeError, ValueError):
        return "无置信度"
    for lo, hi, name in BANDS:
        if lo <= value < hi:
            return name
    return "无置信度"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=20260820)
    ap.add_argument("--multi-sample", type=int, default=0,
                    help="多成员事件抽样量，0 表示全查（推荐）")
    ap.add_argument("--single-sample", type=int, default=80)
    ap.add_argument("--non-event-sample", type=int, default=100)
    args = ap.parse_args()

    rd = args.run_dir
    rng = random.Random(args.seed)
    frames_path = rd / "work" / "frames_resolved.jsonl"
    if not frames_path.exists():
        frames_path = rd / "work" / "frames.jsonl"
    src = next((p for p in (rd / "work" / "block_events_ranked.jsonl",
                            rd / "work" / "block_events.jsonl") if p.exists()), None)
    if src is None:
        raise SystemExit("缺少 work/block_events.jsonl，先跑 07_block_archive.py")

    frames = {f["record_id"]: f for f in read_jsonl(frames_path)}
    events = list(read_jsonl(src))
    multi = [e for e in events if e["member_count"] > 1]
    single = [e for e in events if e["member_count"] == 1]

    pool = multi if not args.multi_sample else rng.sample(
        multi, min(args.multi_sample, len(multi)))
    multi_rows = []
    for event in sorted(pool, key=lambda e: -e["member_count"]):
        multi_rows.append({
            "sample_id": f"M{len(multi_rows):04d}",
            "event_id": event["event_id"],
            "一级_事件名": event.get("event_name", ""),
            "描述": event.get("event_description", ""),
            "二级_宣传角度": " / ".join(f["angle"] for f in event.get("facets") or []),
            "成员数": event["member_count"],
            "锚点": event.get("anchor", ""),
            "实例": event.get("series_instance") or "",
            "事件热度": event.get("事件热度", ""),
            "confidence": event.get("confidence", ""),
            "置信区间": band_of(event.get("confidence")),
            "全部成员标题": " ||| ".join(frames[m].get("title", "")
                                   for m in event["member_record_ids"]),
            "gt_verdict": "", "应移出的记录": "", "gt_note": "",
        })

    by_block: dict[str, list[dict]] = collections.defaultdict(list)
    for event in events:
        by_block[event.get("block_id", "")].append(event)

    picked = rng.sample(single, min(args.single_sample, len(single)))
    single_rows = []
    for event in picked:
        rid = event["member_record_ids"][0]
        siblings = [e for e in by_block.get(event.get("block_id", ""), [])
                    if e["member_count"] > 1]
        single_rows.append({
            "sample_id": f"S{len(single_rows):04d}",
            "event_id": event["event_id"], "record_id": rid,
            "platform": frames[rid].get("platform", ""),
            "title": frames[rid].get("title", ""),
            "同块的多成员事件": " ||| ".join(
                f"[{e['event_id']}] {e.get('event_name', '')}" for e in siblings[:6]),
            "gt_verdict": "", "应并入": "", "gt_note": "",
        })

    eventness = list(read_jsonl(rd / "work" / "eventness.jsonl"))
    non_events = [r for r in eventness if r.get("eventness") == "NON_EVENT"]
    by_reason: dict[str, list[dict]] = collections.defaultdict(list)
    for r in non_events:
        by_reason[r.get("reason_code") or "无"].append(r)
    ne_rows = []
    per_reason = max(1, args.non_event_sample // max(len(by_reason), 1))
    for reason, group in by_reason.items():
        rng.shuffle(group)
        for r in group[:per_reason]:
            ne_rows.append({"sample_id": f"N{len(ne_rows):04d}",
                            "record_id": r["record_id"],
                            "platform": r.get("platform", ""),
                            "title": r.get("clean_title") or r.get("title", ""),
                            "reason_code": reason,
                            "confidence": r.get("model_confidence", ""),
                            "gt_verdict": "", "gt_note": ""})

    out = rd / "out"
    write_csv(out / "校准抽样_多成员事件.csv", multi_rows,
              list(multi_rows[0]) if multi_rows else [])
    write_csv(out / "校准抽样_单成员.csv", single_rows,
              list(single_rows[0]) if single_rows else [])
    write_csv(out / "校准抽样_非事件.csv", ne_rows,
              ["sample_id", "record_id", "platform", "title", "reason_code",
               "confidence", "gt_verdict", "gt_note"])
    (out / "校准填写说明.md").write_text(f"""# 校准表怎么填

三张表，只填 `gt_verdict` 和它后面几列，其他列不要改。

## 校准抽样_多成员事件.csv（{len(multi_rows)} 个）—— 最重要

看 `一级_事件名`、`描述`、`全部成员标题`，判断这些成员是否真属于**同一个创作灵感方向**。

| gt_verdict | 含义 |
|---|---|
| `CORRECT` | 成员都该在一起 |
| `WRONG_MERGE` | 混进了不该在的成员 —— **这一类最重要** |
| `SHOULD_SPLIT` | 整个事件该拆成两个以上 |
| `WRONG_NAME` | 成员对但名字不覆盖成员 |
| `AMBIGUOUS` | 材料不足以判断 |

判 `WRONG_MERGE` 时在 `应移出的记录` 写下该移出的标题片段。

判据：问「**这些热点背后能提供的创作灵感参考，是一个还是多个？**」
一个就该合、不同切入点进二级角度；多个就该拆。

票房破 2 亿到 8 亿只提供一个方向「影片大卖」（该合）；
台风致灾与官方辟谣是两个方向「灾情应对」和「信息治理」（该拆）。

## 校准抽样_单成员.csv（{len(single_rows)} 条）

看 `title` 和 `同块的多成员事件`，判断这条是否本该并进其中某个。
填 `CORRECT`（确实独立）或 `SHOULD_MERGE`（漏并），后者在 `应并入` 写 event_id。

## 校准抽样_非事件.csv（{len(ne_rows)} 条）

判断这条是否真的不构成事件。填 `CORRECT` 或 `WRONG`（误删）。
特别留意容易被误删的四类：具名营销活动、具名作品上线、对具体事件的解读讨论，
以及**正在上映/上线/更新的当代作品的剧情人物讨论**（这一类实测被误判过 12 条）。

## 填完之后

跑 `11_score_calibration.py --run-dir <本目录>`，算出 False Merge Rate 等指标
并把各置信档实测错误率写进 run_manifest.json。

**在这三张表填完之前，不要给 confidence 设任何自动路由阈值。**
未校准的置信度是没有刻度的仪表——实测廉价模型给出的高置信判定有 49% 是错的。
""", encoding="utf-8")

    print(f"多成员事件 {len(multi_rows)}/{len(multi)}（合并正确性，核心）")
    print(f"单成员抽样 {len(single_rows)}/{len(single)}（漏并）")
    print(f"非事件抽样 {len(ne_rows)}/{len(non_events)}（误杀）")
    bands = collections.Counter(r["置信区间"] for r in multi_rows)
    print("多成员事件各置信档：" + " ".join(f"{k}={v}" for k, v in bands.most_common()))
    thin = [k for k, v in bands.items() if v < 20]
    if thin:
        print(f"  这些档不足 20 条，校准表在这些档上不可用：{thin}", file=sys.stderr)
    print("\n下一步：人工填 gt_verdict，再跑 11_score_calibration.py")
    log_run(rd / "run_manifest.json",
            {"stage": "10_build_calibration_sample",
             "multi_events": len(multi_rows), "single_samples": len(single_rows),
             "non_event_samples": len(ne_rows)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
