#!/usr/bin/env python3
"""阶段 x2：归属置信度筛查。不调模型（可选调一次强模型裁定边界带）。

阶段 07 给每条成员打了归属置信度，这一步按阈值决定去留：低分的从事件里摘出、
转成单条热点。摘出的记录不丢，只是不再被算作那个事件的成员。

**为什么要这一步**：「同一场活动里的人物话题算该活动的二级角度」这条规则被过度
套用是本流程最主要的误合并来源。实测一个 107 成员的颁奖典礼事件混进 14 条无关
记录，共同特征都是「只共享同一个人、没有事件层面的关系」。这类错误在归档阶段
拦不住——数据里只有标题、没有时间地点，模型无从验证「这条到底发生在哪」。

**为什么是两条线而不是一条**：抽样 88 条人工判定后，单一阈值全都有明显误伤——

    阈值 50 → 踢 15 条，误伤  0 条，漏踢 10 条
    阈值 60 → 踢 28 条，误伤  9 条（32%）
    阈值 70 → 踢 38 条，误伤 15 条（39%）
    阈值 75 → 踢 48 条，误伤 23 条（48%）

原因是分档质量两头准、中间浑：90 以上和 75~89 各抽 20 条全部「属于」；40 以下
抽 8 条全部「不属于」；而 60~74 是 14 属于 / 6 不属于，40~59 是 9 属于 / 11 不属于。

所以：低于 --cut 自动剔除（样本上零误伤），--cut ~ --keep 之间交裁定，
--keep 以上直接保留。

**边界带怎么处理**，两条路都验证过：
  --verdict off （默认）只出待裁定表，人工填「属于/不属于」后用 --apply-verdict 落地
  --verdict opus 交强模型裁定。65 条判出 45 属于 / 20 不属于，成本约 $0.6

关于模型：第一轮用 opus 裁定边界带时改对 7 改错 8，看似无效。但它的错误全部集中
在两类——「后续影响型」（赛事排名变化）和「话题桶型」（同题材成员）——而这两类
当时没写进刻度。补全刻度后重测质量明显提升。**刻度不全时换模型没用。**

用法：
    python3 x2_confidence_filter.py --run-dir .                    # 出待裁定表
    python3 x2_confidence_filter.py --run-dir . --verdict opus     # 让强模型裁定
    python3 x2_confidence_filter.py --run-dir . --apply-verdict    # 落地人工裁定
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import Relay, log_run, make_batches, read_jsonl, run_batches, write_csv, write_jsonl

VERDICT_SYSTEM = """你在裁定一批热点记录到底属不属于它被归入的那个事件。

这些记录是自动流程判不准、落在边界带的部分，需要你逐条给出明确结论。

判据：**这条记录的动作，是发生在这个事件的时空里，还是发生在别的场合？**

# 属于（四类，后两类最容易被误判为不属于）

1. 标题直接写明了这件事：出现活动名、届次、作品名，或明确写了这次动作
2. 是这件事的一个环节：票数、获奖结果、红毯造型、剧情讨论、票房数字
3. **是这件事的后续影响或派生数据**，它是这件事造成的结果，不是另一件事
   例：`国乒男单世界排名前10仅剩2人` 属于本次赛事的国乒失利事件
   例：`票房预测上调至35.4亿` 属于该片上映事件
4. **话题桶型事件的同题材成员**。有些事件本身就是围绕一个题材聚起来的（节日营销
   热潮、某类数据集中发布、某类现象讨论），对这类事件「共享同一题材」正是入选标准
   例：`这届年轻人真的在整顿婚礼` 属于结婚登记数据发布及婚恋现象讨论事件
   判断方法：看事件名和描述是「一次具体动作」还是「一个题材集合」

# 不属于（三类）

1. 只共享同一个人 / 品牌 / 题材，看不出事件层面的关系
   例：`张元英口红像偷吃完辣条` 不属于百花奖（该艺人与百花奖无关联）
2. 动作发生在另一个场合：另一个活动、另一部作品、另一个奖项、另一个时间点
   例：`沈腾路演泪崩` 不属于百花奖（路演是另一个宣传场合）
   例：`辛芷蕾又拿大奖了` 不属于百花奖（「又」暗示是另一个奖项）
3. 借这件事引申出去的泛议题，讨论对象已经不是这件事本身

# 关键对照

同样只写人名加动作，结论可能相反，区别在动作发生在哪：

  属于   `易烊千玺一直在抠脑壳`   → 他是本届影帝，典礼现场的紧张反应
  属于   `竟然不是高叶`           → 指本届某奖项结果出乎意料
  不属于 `易烊千玺还有三部待播作品` → 关于未来作品的话题，不在典礼里
  不属于 `易烊千玺带松果出席活动`   → 未指明是哪个活动

# 输出

对每条给出 verdict（属于 / 不属于）和一句具体理由，说清「动作发生在哪」，
不许写「关联度较低」这类空话。

拿不准时判「不属于」——错拆能补救（它变成一条独立热点，不丢数据），
错合会污染整个事件。

只输出 JSON，不要任何解释。"""

VERDICT_USER = """裁定以下 {n} 条记录。

{items}

输出格式：
{{"results":[{{"record_id":"","verdict":"属于|不属于","reason":""}}]}}

每个 record_id 必须出现且仅出现一次。"""


def load(rd: Path):
    events = list(read_jsonl(rd / "work" / "block_events.jsonl"))
    frames_p = rd / "work" / "frames_resolved.jsonl"
    if not frames_p.exists():
        frames_p = rd / "work" / "frames.jsonl"
    frames = {f["record_id"]: f for f in read_jsonl(frames_p)}
    return events, frames


def scores(events: list[dict]) -> dict[str, int]:
    out = {}
    for e in events:
        if e.get("singleton"):
            continue
        for rid, v in (e.get("member_confidence") or {}).items():
            if v is not None:
                out[rid] = int(v)
    return out


def demote(events: list[dict], frames: dict, kick: set[str],
           origin: str) -> tuple[list[dict], int]:
    """把 kick 里的记录从各事件摘出、各自成为单条事件。成员掉到 1 条的事件整体降级。"""
    for e in events:
        if e.get("singleton"):
            continue
        keep = [r for r in e["member_record_ids"] if r not in kick]
        if len(keep) == len(e["member_record_ids"]):
            continue
        e["member_record_ids"] = keep
        e["member_count"] = len(keep)
        e["facets"] = [{**f, "members": [m for m in f["members"] if m in keep]}
                       for f in e.get("facets") or []]
        e["facets"] = [f for f in e["facets"] if f["members"]]
        e["member_confidence"] = {k: v for k, v in
                                  (e.get("member_confidence") or {}).items() if k in keep}

    demoted = 0
    for e in events:
        if e.get("singleton") or e["member_count"] != 1:
            continue
        rid = e["member_record_ids"][0]
        e.update({"singleton": True, "event_name": frames[rid].get("title", ""),
                  "event_description": "", "facets": [],
                  "anchor": "", "anchor_type": "", "anchor_raw": "",
                  "series_instance": None,
                  "name_check": {"status": "SINGLETON_VERBATIM",
                                 "unsupported": [], "empty_words": []}})
        demoted += 1

    events = [e for e in events if e["member_count"] > 0]
    for i, rid in enumerate(sorted(kick), 1):
        events.append({
            "event_id": f"X2_{i:04d}", "block_id": "", "origin": origin,
            "event_name": frames[rid].get("title", ""), "event_description": "",
            "facets": [], "anchor": "", "anchor_type": "", "anchor_raw": "",
            "series_instance": None, "empty_words": [], "singleton": True,
            "member_record_ids": [rid], "member_count": 1,
            "member_confidence": {rid: None}, "member_notes": {},
            "canonical_anchor_record_id": rid,
            "anchor_title": frames[rid].get("title", ""),
            "identity_core": {"actor": "", "action": frames[rid].get("action", ""),
                              "object": frames[rid].get("object", ""),
                              "event_type": "OTHER"},
            "name_evidence": {}, "confidence": None,
            "name_check": {"status": "SINGLETON_VERBATIM",
                           "unsupported": [], "empty_words": []},
            "state": {"latest_stage": frames[rid].get("stage"), "member_count": 1,
                      "key_facts": frames[rid].get("key_facts") or {},
                      "platforms": [frames[rid].get("platform", "")]},
        })
    return events, demoted


def ask_verdict(rows: list[dict], events: list[dict], model: str,
                rd: Path, concurrency: int) -> dict[str, dict]:
    """让强模型裁定边界带。刻度已在 SYSTEM 里写全，不要精简。"""
    desc = {e["event_name"]: e.get("event_description", "") for e in events}
    relay = Relay(model, 16000, VERDICT_SYSTEM)
    batches = make_batches(rows, 10, id_field="record_id", tag="VD")

    def worker(b: dict) -> dict:
        lines = []
        for r in b["items"]:
            lines.append(
                f"[{r['record_id']}] {r['热点标题']}\n"
                f"    被归到的事件：{r['被归到的事件']}（{r['事件总条数']} 条成员）\n"
                f"    该事件的描述：{(desc.get(r['被归到的事件']) or '（无）')[:150]}\n"
                f"    自动流程给的分数与理由：{r['分数']} 分，{r['模型给的理由'][:110]}")
        p = relay.call_json(VERDICT_USER.format(n=len(b["items"]),
                                               items="\n\n".join(lines)))
        return {"batch_id": b["batch_id"], "results": p.get("results", [])}

    res, fail = run_batches(batches, worker, rd / "raw" / "x2_verdict",
                            concurrency, "x2_verdict")
    if fail:
        print(f"有 {len(fail)} 批失败，重跑即可续跑：{fail}", file=sys.stderr)
    out = {x["record_id"]: x for p in res for x in p.get("results", [])}
    print(f"裁定 {len(out)} 条，成本 ${relay.cost():.4f}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--cut", type=int, default=50,
                    help="低于此分自动剔除。实测 50 在样本上零误伤")
    ap.add_argument("--keep", type=int, default=75,
                    help="不低于此分直接保留。实测 75 以上样本 40 条全对")
    ap.add_argument("--verdict", choices=["off", "opus", "sonnet"], default="off",
                    help="边界带交谁裁定。off = 只出待裁定表给人工")
    ap.add_argument("--apply-verdict", action="store_true",
                    help="读回已填好人工裁定的表并落地")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    if args.cut >= args.keep:
        raise SystemExit(f"--cut({args.cut}) 必须小于 --keep({args.keep})")

    rd = args.run_dir
    src = rd / "work" / "block_events.jsonl"
    if not src.exists():
        raise SystemExit(f"缺少 {src}，先跑 07_block_archive.py")
    events, frames = load(rd)
    sc = scores(events)
    if not sc:
        raise SystemExit(
            "事件里没有 member_confidence。这个字段由 07 产出，"
            "说明 07 用的是旧提示词——请确认 07 的 SCHEMA 已含置信度刻度后重跑 07。")

    owner = {rid: e for e in events if not e.get("singleton")
             for rid in e["member_record_ids"]}
    notes = {rid: n for e in events for rid, n in (e.get("member_notes") or {}).items()}

    kick = {r for r, s in sc.items() if s < args.cut}
    band = sorted((r for r, s in sc.items() if args.cut <= s < args.keep),
                  key=lambda r: sc[r])
    dist = collections.Counter(
        "90+" if s >= 90 else "75-89" if s >= 75 else "60-74" if s >= 60
        else "40-59" if s >= 40 else "<40" for s in sc.values())
    print(f"打分 {len(sc)} 条：" +
          " ".join(f"{k}={dist[k]}" for k in ("90+", "75-89", "60-74", "40-59", "<40")))
    print(f"自动剔除(<{args.cut}) {len(kick)} 条 | "
          f"待裁定({args.cut}~{args.keep-1}) {len(band)} 条 | "
          f"直接保留(>={args.keep}) {sum(1 for s in sc.values() if s >= args.keep)} 条")

    band_rows = [{"record_id": r, "分数": sc[r],
                  "平台": frames[r].get("platform", ""),
                  "热点标题": frames[r].get("title", ""),
                  "被归到的事件": owner[r]["event_name"],
                  "事件总条数": owner[r]["member_count"],
                  "模型给的理由": notes.get(r, ""),
                  "你的判断（属于/不属于）": ""}
                 for r in band if r in owner]

    # 边界带交强模型裁定
    if args.verdict != "off" and band_rows:
        model = "claude-opus-4-7" if args.verdict == "opus" else "claude-sonnet-4-6"
        v = ask_verdict(band_rows, events, model, rd, args.concurrency)
        for row in band_rows:
            row["你的判断（属于/不属于）"] = v.get(row["record_id"], {}).get("verdict", "")
            row["裁定理由"] = v.get(row["record_id"], {}).get("reason", "")
        kick |= {r["record_id"] for r in band_rows
                 if r["你的判断（属于/不属于）"] == "不属于"}

    # 读回人工填好的表
    if args.apply_verdict:
        p = rd / "out" / "x2_待裁定_边界带.csv"
        if not p.exists():
            raise SystemExit(f"缺少 {p}")
        filled = list(csv.DictReader(p.open(encoding="utf-8-sig")))
        col = "你的判断（属于/不属于）"
        got = [r for r in filled if (r.get(col) or "").strip()]
        print(f"读回 {len(got)}/{len(filled)} 条已填裁定")
        kick |= {r["record_id"] for r in got if r[col].strip() == "不属于"}
        band_rows = filled

    cols = ["record_id", "分数", "平台", "热点标题", "被归到的事件", "事件总条数",
            "模型给的理由", "你的判断（属于/不属于）"]
    if band_rows and "裁定理由" in band_rows[0]:
        cols.append("裁定理由")
    if band_rows:
        write_csv(rd / "out" / "x2_待裁定_边界带.csv", band_rows, cols)

    kick_rows = [{"record_id": r, "分数": sc.get(r, ""),
                  "平台": frames[r].get("platform", ""),
                  "热点标题": frames[r].get("title", ""),
                  "原先被归到的事件": owner[r]["event_name"] if r in owner else "",
                  "剔除依据": ("低于 %d 分自动剔除" % args.cut
                           if sc.get(r, 100) < args.cut else "裁定为不属于"),
                  "理由": notes.get(r, ""), "人工复核": ""}
                 for r in sorted(kick, key=lambda x: sc.get(x, 0))]
    if kick_rows:
        write_csv(rd / "out" / "x2_已剔除.csv", kick_rows,
                  ["record_id", "分数", "平台", "热点标题", "原先被归到的事件",
                   "剔除依据", "理由", "人工复核"])

    origin = f"x2_kicked_lt{args.cut}" if args.verdict == "off" else "x2_kicked"
    events, demoted = demote(events, frames, kick, origin)
    write_jsonl(rd / "work" / "block_events.jsonl", events)

    multi = [e for e in events if e["member_count"] > 1]
    print(f"\n剔除 {len(kick)} 条 | 降级为单条的事件 {demoted} 个")
    print(f"事件 {len(events)} 个（多成员 {len(multi)} / 单条 {len(events)-len(multi)}）")
    total = sum(e["member_count"] for e in events)
    print(f"记录总数 {total}（应与 07 的归档条数一致，剔除的转成单条不丢）")
    if band_rows and args.verdict == "off" and not args.apply_verdict:
        print(f"\n→ out/x2_待裁定_边界带.csv（{len(band_rows)} 条，填完用 --apply-verdict 落地）")
    print("→ out/x2_已剔除.csv")
    log_run(rd / "run_manifest.json",
            {"stage": "x2_confidence_filter", "cut": args.cut, "keep": args.keep,
             "verdict": args.verdict, "kicked": len(kick), "band": len(band),
             "demoted": demoted, "events": len(events)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
