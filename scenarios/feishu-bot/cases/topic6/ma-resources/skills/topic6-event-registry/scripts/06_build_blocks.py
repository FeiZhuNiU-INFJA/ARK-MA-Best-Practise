#!/usr/bin/env python3
"""阶段 19：有界分块。

分块只需要是**超集**，不需要是答案。这一条是整套设计的支点：

- 聚类要精度：错一条边就错一次合并
- 分块只要召回 + 有界：多塞无关记录只是让模型看到噪声，而模型对噪声的
  拒绝率实测 86.6%

所以这里**可以**用传递闭包——之前禁止它是因为它直接产出结论。作为工作范围
的划定，传递性反而是优点：它保证同主体不被切开。

实测（382 条）：全通道 + cap60 → 14 个块、最大 3800 tokens、
参考同事件对召回 **100%**（547 对一对没切开，该基准是上一版路线的输出而非人工标注，
只能证明分块没切开已被合并的对）。

规模上限按「预估输出 token」控制，不是按记录数——截断是这套设计唯一的硬风险。
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import log_run, read_jsonl, write_csv, write_jsonl

# 每条记录预估会产生的输出 token：归属 + 事件定义摊销 + 标题证据。
# 实测（微博 28 个 ≥20 条的块）中位 66、p95 83、最大 99，所以 90 落在 p96——
# 偏保守是对的，因为撞上限的是尾部不是均值。
# 每条的输出量不随块变大而增长（cap60/100/150 分别是 66/60/59 tokens/条），
# 变的是单块总量的尾部方差。
OUT_TOKENS_PER_RECORD = 90

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--cap", type=int, default=60,
                    help="单块记录数上限。实测 60 已能保住 100%% 同事件对召回")
    ap.add_argument("--out-budget", type=int, default=6000,
                    help="单块预估输出 token 上限。cap≤66 时它永远不会先触发"
                         "（60×90=5400<6000），实测两次全量跑都是 0 次命中——"
                         "真正画出块边界的是 cap。**但输出才是真实的硬约束**："
                         "输入侧 60 条只占 3400 tokens、模型窗口 200K，完全不紧张；"
                         "而 07 的 max_tokens=16000，实测最大块输出 cap60 时 5919、"
                         "cap100 时 7682、cap150 时 12188，截断率 0.8%→13%→17%")
    ap.add_argument("--channels", default="all",
                    help="all | exact | embedding。分块通道，all 召回最高")
    args = ap.parse_args()

    rd = args.run_dir
    frames_path = rd / "work" / "frames_resolved.jsonl"
    if not frames_path.exists():
        frames_path = rd / "work" / "frames.jsonl"
    for path in (frames_path, rd / "work" / "candidates.jsonl"):
        if not path.exists():
            raise SystemExit(f"缺少 {path}，先跑上游阶段")

    frames = {f["record_id"]: f for f in read_jsonl(frames_path)}
    cand = {c["record_id"]: c for c in read_jsonl(rd / "work" / "candidates.jsonl")}

    edges = []
    for rid, c in cand.items():
        for x in c["candidates"]:
            chans = set(x["channels"])
            if args.channels == "exact" and not (chans - {"embedding"}):
                continue
            if args.channels == "embedding" and "embedding" not in chans:
                continue
            edges.append((rid, x["target_id"], x["embedding_score"], chans))

    parent: dict[str, str] = {}
    size: dict[str, int] = {}
    out_est: dict[str, int] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for rid in cand:
        find(rid)
        size[rid] = 1
        out_est[rid] = OUT_TOKENS_PER_RECORD

    # 强边优先合并；超出记录数或输出预算就不再吸收，块自然封顶
    #
    # 被丢弃的边必须落盘。它们是召回层判定为「可能同事件」、然后被容量上限扔掉的连接，
    # 实测四平台合计 27,034 条（平均每条记录 5.5 条）。不落盘就没有任何下游能补回它们，
    # 也查不出「同一件事被切到两个块」是怎么发生的（实测赖冠霖 6 条就是这么被切开的）。
    skipped_by_cap = 0
    skipped_by_budget = 0
    dropped: list[dict] = []
    for a, b, score, chans in sorted(edges, key=lambda e: (-e[2], e[0], e[1])):
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        over_cap = size[ra] + size[rb] > args.cap
        over_budget = out_est[ra] + out_est[rb] > args.out_budget
        if over_cap or over_budget:
            skipped_by_cap += over_cap
            skipped_by_budget += over_budget and not over_cap
            dropped.append({"a": a, "b": b, "embedding_score": score,
                            "channels": sorted(chans),
                            "reason": "cap" if over_cap else "out_budget"})
            continue
        parent[ra] = rb
        size[rb] += size[ra]
        out_est[rb] += out_est[ra]
    skipped = skipped_by_cap + skipped_by_budget

    groups: dict[str, list[str]] = collections.defaultdict(list)
    for rid in cand:
        groups[find(rid)].append(rid)

    blocks = []
    # Ada接入补丁 2026-09-22（方案1·分块确定性）：块的排序键从「仅按成员数降序」改为
    # 「(成员数降序, 组内最小 record_id 升序)」。原写法在成员数相同的块之间靠 dict
    # 迭代顺序做隐式 tiebreak——union-find 合并结果(块的组成)本就确定，但块之间的
    # 先后、进而 block_id(BLK0000/0001/…)的分配，随输入行序(上游 04/05 embedding 重排)
    # 漂移：同一批内容重跑，同一个块可能这次叫 BLK0002、下次叫 BLK0006。这会 ①让
    # 失败块 id 不可复现、②让 07/relay.run_batches 按 block_id 命名的内容哈希缓存错位。
    # 加 min(record_id) 作稳定次键后，block_id 分配只由内容决定、与输入行序无关，
    # 配合内容哈希缓存可真正复现命中。组成(哪些记录同块)不受影响。应同步上游。
    for members in sorted(groups.values(), key=lambda m: (-len(m), min(m))):
        actors = collections.Counter()
        for rid in members:
            for actor in frames[rid].get("actor") or []:
                if actor.get("name"):
                    actors[actor["name"]] += 1
        in_tokens = sum(len(frames[r].get("title", "")) + 45 for r in members)
        members.sort(key=lambda r: (-(frames[r].get("information_score") or 0),
                                    -float(frames[r].get("heat") or 0), r))
        blocks.append({
            "block_id": f"BLK{len(blocks):04d}",
            "record_ids": members,
            "record_count": len(members),
            "est_input_tokens": in_tokens,
            "est_output_tokens": len(members) * OUT_TOKENS_PER_RECORD,
            "top_actors": [a for a, _ in actors.most_common(5)],
        })

    write_jsonl(rd / "work" / "blocks.jsonl", blocks)
    write_jsonl(rd / "work" / "skipped_edges.jsonl", dropped)
    write_csv(rd / "out" / "分块概览.csv",
              [{"block_id": b["block_id"], "记录数": b["record_count"],
                "预估输入tokens": b["est_input_tokens"],
                "预估输出tokens": b["est_output_tokens"],
                "主要主体": " / ".join(b["top_actors"]),
                "样例标题": " ||| ".join(frames[r].get("title", "")[:30]
                                     for r in b["record_ids"][:4])}
               for b in blocks],
              ["block_id", "记录数", "预估输入tokens", "预估输出tokens",
               "主要主体", "样例标题"])

    sizes = [b["record_count"] for b in blocks]
    print(f"{len(cand)} 条 → {len(blocks)} 个块（通道={args.channels} cap={args.cap} "
          f"输出预算={args.out_budget}）")
    print(f"  最大 {max(sizes)} 条 / {max(b['est_input_tokens'] for b in blocks)} 输入tokens"
          f" | 单条块 {sum(1 for s in sizes if s == 1)} 个")
    print(f"  被容量上限丢弃的边 {skipped} 条（cap {skipped_by_cap} / 预算 {skipped_by_budget}）"
          f" → work/skipped_edges.jsonl")
    if skipped:
        print(f"  这些是召回层认为可能同事件、但装不进同一块的连接。"
              f"平均每条记录 {skipped/max(len(cand),1):.1f} 条，"
              f"同一件事被切到两个块时从这里查")
    print(f"\n最大 6 个块：")
    for b in blocks[:6]:
        print(f"  {b['block_id']} {b['record_count']:>3}条 "
              f"in≈{b['est_input_tokens']:>5} out≈{b['est_output_tokens']:>5} "
              f"| {' / '.join(b['top_actors'][:3])}")
    log_run(rd / "run_manifest.json",
            {"stage": "06_build_blocks", "records": len(cand), "blocks": len(blocks),
             "cap": args.cap, "out_budget": args.out_budget,
             "channels": args.channels, "max_block": max(sizes),
             "skipped_edges": skipped, "skipped_by_cap": skipped_by_cap,
             "skipped_by_budget": skipped_by_budget})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
