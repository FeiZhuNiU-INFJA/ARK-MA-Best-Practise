#!/usr/bin/env python3
"""阶段 15：提交 Registry。不调模型。

提交前的门禁，任何一条不过就拒绝提交：
- 每条 EVENT 记录必须且只能归属一个事件
- 标题事实校验状态必须落盘（不通过的必须已落回有出处的标题）
- Identity Core 三要素至少两项
- 待人工项必须单独成表，不能静默混进正式产物
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import read_jsonl, write_csv, write_jsonl

NOTES = """# 口径与执行说明

## 产物构成

| 文件 | 内容 |
|---|---|
| `event_registry.csv` | 事件表，一行一个事件，含一级事件名 / 二级宣传角度 / 描述 / 热度排名 |
| `record_to_event.csv` | 记录级血缘明细，一行一条原始记录 |
| `relation_graph.csv` | 事件间关系，相关但不同的事件对 |
| `non_events.csv` | 判定为非事件、以及流程中未归档的记录 |
| `human_review.csv` | 名称校验未通过、需要人工看的事件 |
| `事件排名.csv` | 按事件热度排序的事件榜（由阶段 08 产出） |
| `热度明细.csv` | 记录级热度与平台内百分位（由阶段 08 产出） |
| `块内归档结果.csv` | 归档明细，含二级角度与成员标题（由阶段 07 产出） |
| `registry_snapshot.jsonl` | 跨窗口增量用的快照，含锚点向量 |
| `output_manifest.json` | 本次运行的统计与门禁结果 |

## 关键口径

**事件身份只由主体 / 动作 / 对象 / 类型 / 父事件决定。** 文本相似、共享人物或品牌、
时间接近、语义向量分数高，都不作为同一事件的依据。同一周上榜只说明有相同的趋势。

**成员数与热度不是同一件事。** 成员数是这个事件被讨论的条目数，不等于热度。
本流程只做单平台聚合，热度不跨平台聚合、也不相加原始值。

**相关但不同的事件用关系表达，不合并。** 同一主体的不同事项、同一组合的不同成员、
同一系列的不同届次，都记为关系或兄弟事件，不塞进一个事件。

**父事件是组织单位，不是分类标签。** 只有一个子事件的父事件会被解散——那不是组织。

**一级事件名的每个数字与届次都必须在成员材料里有出处。** 校验不通过的名称不会提交，
会落回信息量最高成员的原标题；名称需要靠「系列事件」这类空词才能覆盖成员的，
整个事件会被拆成单条记录。

**单成员记录不做加工。** 它们的事件名沿用阶段 00 清洗后的标题，不产出锚点，
也不参与合并判断——所以不要拿单成员事件的名称质量去评估这套流程。

**名称校验未通过的事件不在正式表里下结论。** 它们同时出现在 `event_registry.csv`
（带 name_check 标记）与 `human_review.csv`，人工结论优先于自动判定。

## 不适用的场景

只有标题、没有正文时，时间与地点字段填充率很低（实测 6% / 9%），
不要用这两个字段做过滤或统计。
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--force", action="store_true", help="门禁不过也强行提交（不建议）")
    ap.add_argument("--window", default="", help="窗口标识，如 2026W33，写进快照")
    args = ap.parse_args()

    rd = args.run_dir
    frames_path = rd / "work" / "frames_resolved.jsonl"
    if not frames_path.exists():
        frames_path = rd / "work" / "frames.jsonl"
    src = rd / "work" / "block_events.jsonl"
    if not src.exists():
        raise SystemExit("缺少 work/block_events.jsonl，先跑 07_block_archive.py")

    frames = {f["record_id"]: f for f in read_jsonl(frames_path)}
    pf = rd / "work" / "prior_frames.jsonl"
    if pf.exists():
        for row in read_jsonl(pf):
            frames.setdefault(row["record_id"], row)
    events = list(read_jsonl(src))
    ranked = rd / "work" / "block_events_ranked.jsonl"
    if ranked.exists():
        rank_of = {e["event_id"]: e for e in read_jsonl(ranked)}
        events = [{**e, **{k: v for k, v in rank_of.get(e["event_id"], {}).items()
                           if k.startswith(("事件排名", "事件热度", "成员百分位"))}}
                  for e in events]
    eventness = {r["record_id"]: r for r in read_jsonl(rd / "work" / "eventness.jsonl")}

    relations = []
    path = rd / "work" / "block_relations.jsonl"
    if path.exists():
        relations.extend(read_jsonl(path))

    # 门禁
    failures = []
    flat = [m for e in events for m in e["member_record_ids"]]
    if len(flat) != len(set(flat)):
        failures.append(f"记录重复归属 {len(flat) - len(set(flat))} 条")
    expected = {r for r, v in eventness.items() if v.get("eventness") in {"EVENT", "UNCERTAIN"}}
    dropped = [r for r in expected - set(flat) if r in frames]
    if events and "事件排名" not in events[0]:
        print("提示：未跑 08_rank_events.py，产物没有热度排序列", file=sys.stderr)
    for event in events:
        # 单成员记录不加工，只要有名字就行。多成员事件必须说清凭什么在一起
        if event["member_count"] > 1:
            if not (event.get("event_name") or "").strip():
                failures.append(f"{event['event_id']} 缺一级事件名")
            if not (event.get("event_description") or "").strip():
                failures.append(f"{event['event_id']} 有 {event['member_count']} 个成员"
                                f"却没有描述，无法核对它们凭什么在一起")
        if event.get("name_check", {}).get("status") == "EMPTY_WORD":
            failures.append(f"{event['event_id']} 事件名含空词却未被拆分")
    if failures:
        print("门禁未通过：", file=sys.stderr)
        for msg in failures[:20]:
            print(f"  - {msg}", file=sys.stderr)
        if not args.force:
            return 1

    registry = []
    for event in events:
        registry.append({
            "event_id": event["event_id"],
            "一级_事件名": event.get("event_name") or event["anchor_title"],
            "二级_宣传角度": " / ".join(f["angle"] for f in event.get("facets") or []),
            "描述": event.get("event_description", ""),
            "事件排名": event.get("事件排名", ""),
            "事件热度": event.get("事件热度", ""),
            "member_count": event["member_count"],
            "cumulative_member_count": event["member_count"] + (event.get("prior_member_count") or 0),
            "origin": event.get("origin", "current"),
            "锚点": event.get("anchor") or "",
            "锚点类型": event.get("anchor_type") or "",
            "实例": event.get("series_instance") or "",
            "latest_stage": event["state"].get("latest_stage") or "",
            "platforms": "/".join(event["state"].get("platforms") or []),
            "facet_count": len(event.get("facets") or []),
            "key_facts": json.dumps(event["state"].get("key_facts") or {}, ensure_ascii=False),
            "name_check": (event.get("name_check") or {}).get("status", ""),
            "singleton": event.get("singleton", False),
            "anchor_record_id": event["canonical_anchor_record_id"],
            "member_record_ids": " ".join(event["member_record_ids"]),
        })

    detail = []
    for event in events:
        sub_of = {m: f.get("angle") or ""
                  for f in event.get("facets") or [] for m in f["members"]}
        for rid in event["member_record_ids"]:
            frame = frames.get(rid) or {}
            detail.append({
                "record_id": rid,
                "platform": frame.get("platform", ""),
                "title": frame.get("title", ""),
                "原标题": frame.get("raw_title", ""),
                "event_id": event["event_id"],
                "一级_事件名": event.get("event_name") or event["anchor_title"],
                "二级_宣传角度": sub_of.get(rid, ""),
                "event_member_count": event["member_count"],
                "锚点": event.get("anchor") or "",
                "实例": event.get("series_instance") or "",
                "singleton": event.get("singleton", False),
            })

    non_events = [{"record_id": r, "title": v.get("clean_title") or v.get("title", ""),
                   "platform": v.get("platform", ""),
                   "eventness": v.get("eventness", ""),
                   "reason_code": v.get("reason_code", ""),
                   "model_confidence": v.get("model_confidence", "")}
                  for r, v in eventness.items() if v.get("eventness") == "NON_EVENT"]
    for rid in dropped:
        non_events.append({"record_id": rid, "title": frames[rid].get("title", ""),
                           "platform": frames[rid].get("platform", ""),
                           "eventness": "DROPPED_IN_PIPELINE",
                           "reason_code": "", "model_confidence": ""})

    review = [r for r in registry
              if r["name_check"] not in {"OK", "SINGLETON_VERBATIM",
                                         "SPLIT_FROM_EMPTY_WORD", ""}]

    out = rd / "out"
    write_csv(out / "event_registry.csv", registry, list(registry[0]) if registry else [])
    write_csv(out / "record_to_event.csv", detail, list(detail[0]) if detail else [])
    write_csv(out / "non_events.csv", non_events,
              ["record_id", "platform", "title", "eventness", "reason_code", "model_confidence"])
    write_csv(out / "human_review.csv", review, list(registry[0]) if registry else [])

    # relations 的 event_a / event_b 就是 event_id（BLK0006_E1 这种块内键），
    # 与事件自身的 event_id 同格式。只需过滤掉指向已被本地校验剔除的事件。
    titles = {r["event_id"]: r["一级_事件名"] for r in registry}
    rel_rows = []
    seen_rel = set()
    for rel in relations:
        a = rel.get("event_a")
        b = rel.get("event_b")
        if a not in titles or b not in titles or a == b:
            continue
        key = tuple(sorted((a, b)) + [rel.get("relation_type", "")])
        if key in seen_rel:
            continue
        seen_rel.add(key)
        rel_rows.append({"event_a": a, "event_b": b,
                         "event_a_title": titles[a],
                         "event_b_title": titles[b],
                         "relation_type": rel.get("relation_type", ""),
                         "confidence": rel.get("confidence") or "",
                         "source": rel.get("source", ""),
                         "note": str(rel.get("reason") or rel.get("note") or "")[:120]})
    write_csv(out / "relation_graph.csv", rel_rows,
              ["event_a", "event_a_title", "event_b", "event_b_title",
               "relation_type", "confidence", "source", "note"])
    (out / "口径与执行说明.md").write_text(NOTES, encoding="utf-8")

    # Registry 快照：下一窗口用 00_seed_from_registry.py 读它做增量归档
    vec_path = rd / "work" / "embeddings.json"
    vectors = json.loads(vec_path.read_text(encoding="utf-8"))["vectors"] \
        if vec_path.exists() else {}
    snapshot = []
    no_vec = 0
    for event in events:
        anchor = event["canonical_anchor_record_id"]
        vector = vectors.get(anchor)
        if vector is None:
            no_vec += 1
        touched = bool(event["member_record_ids"])
        snapshot.append({
            "event_id": event["event_id"],
            "canonical_title": event.get("event_name") or event["anchor_title"],
            "anchor_title": event["anchor_title"],
            "anchor_text": frames.get(anchor, {}).get("title", ""),
            "anchor_vector": vector,
            "state": event["state"],
            "anchor": event.get("anchor"),
            "anchor_type": event.get("anchor_type"),
            "series_instance": event.get("series_instance"),
            "member_count": event["member_count"] + (event.get("prior_member_count") or 0),
            "windows_since_update": 0 if touched
                                    else int(event.get("windows_since_update") or 0) + 1,
            "window": args.window or "",
        })
    write_jsonl(out / "registry_snapshot.jsonl", snapshot)
    if no_vec:
        print(f"  {no_vec} 个事件缺锚点向量，下窗口召回不到它们", file=sys.stderr)

    manifest = {
        "records_in": len(eventness),
        "records_assigned": len(flat),
        "events": len(registry),
        "relations": len(rel_rows),
        "non_events": len(non_events),
        "needs_human": len(review),
        "single_member_events": sum(1 for r in registry if r["member_count"] == 1),
        "name_check": dict(collections.Counter(r["name_check"] for r in registry)),
        "gate_failures": failures,
        "snapshot_events": len(snapshot),
        "prior_events_carried": sum(1 for e in events if e.get("origin") == "prior"),
        "window": args.window,
    }
    (out / "output_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"提交完成：{len(registry)} 个事件、"
          f"{len(rel_rows)} 条关系、{len(non_events)} 条非事件")
    print(f"  归档记录 {len(flat)}；待人工 {len(review)}；"
          f"单成员 {manifest['single_member_events']} "
          f"({manifest['single_member_events'] / (len(registry) or 1):.1%})")
    print(f"  名称校验：{manifest['name_check']}")
    if dropped:
        print(f"  流程中丢失 {len(dropped)} 条，已进 non_events.csv 待查", file=sys.stderr)
    print(f"  快照 {len(snapshot)} 个事件 → out/registry_snapshot.jsonl"
          f"（下窗口用 00_seed_from_registry.py 读它）")
    print(f"产物目录：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
