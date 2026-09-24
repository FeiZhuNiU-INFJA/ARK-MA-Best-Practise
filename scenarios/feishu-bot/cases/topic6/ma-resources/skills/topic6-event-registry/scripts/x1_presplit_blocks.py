#!/usr/bin/env python3
"""阶段 x1：失败块重拆。不调模型。

**这一步是反应式的，不是预测式的。** 先跑 07，失败了再用它拆，然后重跑 07。

为什么不预测：曾试过用「块内锚点多样性」预估输出量来提前拆，拿 12 个真实失败块
校准后发现完全分不开——

    失败块预估区间 [14680, 23000]
    成功块预估区间 [410, 25240]      ← 完全覆盖失败块区间

成功块里预估最高的两个（25240）比所有失败块都高。再查实际输出：成功的 60 条块
最大只输出 9361 字符，失败块超过 53850 字符。**同样 60 条记录，输出差 5 倍**，
说明失败不是结构决定的，是模型在那些块上写飞了。60 条块里约 29% 会失败，
预测不了是哪 29%。

而失败名单是已知事实，比任何预测都准。所以流程是：

    07（有块失败）→ x1（只拆失败的）→ 07（重跑，成功的走缓存不重算）

**输出爆炸只能靠减小块，不能靠提 max_tokens。** 实测顺序：

    max_tokens 16000  → 截断报错
    max_tokens 32000  → 仍截断（output_tokens 正好 32000，文本 53850 字符）
    max_tokens 64000  → 网关返回空内容，比截断更难查
    拆成 ≤20 条子块   → 全部成功

64000 的探测请求本身正常返回，所以不是参数上限，是长生成在网关侧超时。

**不要改成全局小 cap。** 174 个块里只有 12 个失败，全局降 cap 会把另外 162 个
正常块无谓切碎，而切碎正是这套设计要消除的东西——同一件事的记录被切到不同块，
模型就永远看不到它们的关系。

用法：
    python3 07_block_archive.py --run-dir .          # 有块失败
    python3 x1_presplit_blocks.py --run-dir .        # 只拆失败的
    python3 07_block_archive.py --run-dir .          # 重跑，成功的走缓存
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import log_run, read_jsonl, write_csv, write_jsonl

TOK_PER_RECORD = 90


def resplit(members: list[str], frames: dict, cand: dict, cap: int) -> list[list[str]]:
    """在块内按阶段 06 同一套规则重新分块，只是 cap 更小。不引入新判据。"""
    scope = set(members)
    edges = []
    for rid in members:
        for x in (cand.get(rid) or {}).get("candidates", []):
            if x["target_id"] in scope:
                edges.append((rid, x["target_id"], x["embedding_score"]))

    parent: dict[str, str] = {}
    size: dict[str, int] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for rid in members:
        find(rid)
        size[rid] = 1

    for a, b, _s in sorted(edges, key=lambda e: -e[2]):
        ra, rb = find(a), find(b)
        if ra == rb or size[ra] + size[rb] > cap:
            continue
        parent[ra] = rb
        size[rb] += size[ra]

    groups: dict[str, list[str]] = collections.defaultdict(list)
    for rid in members:
        groups[find(rid)].append(rid)
    return sorted(groups.values(), key=len, reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--sub-cap", type=int, default=20,
                    help="拆出的子块记录数上限。实测 20 条能稳定跑通")
    ap.add_argument("--blocks", default="",
                    help="逗号分隔，手工指定要拆的块。留空则自动识别 07 没产出的块")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不改 blocks.jsonl")
    args = ap.parse_args()

    rd = args.run_dir
    fp = rd / "work" / "frames_resolved.jsonl"
    if not fp.exists():
        fp = rd / "work" / "frames.jsonl"
    for p in (fp, rd / "work" / "blocks.jsonl", rd / "work" / "candidates.jsonl"):
        if not p.exists():
            raise SystemExit(f"缺少 {p}，先跑上游阶段")

    frames = {f["record_id"]: f for f in read_jsonl(fp)}
    cand = {c["record_id"]: c for c in read_jsonl(rd / "work" / "candidates.jsonl")}
    blocks = list(read_jsonl(rd / "work" / "blocks.jsonl"))
    by_id = {b["block_id"]: b for b in blocks}

    if args.blocks:
        want = [b.strip() for b in args.blocks.split(",") if b.strip()]
        missing = [b for b in want if b not in by_id]
        if missing:
            raise SystemExit(f"blocks.jsonl 里没有这些块：{missing}")
    else:
        raw = rd / "raw" / "block_archive"
        done = {p.stem for p in raw.glob("*.json")} if raw.exists() else set()
        want = [b["block_id"] for b in blocks if b["block_id"] not in done]
        if not done:
            raise SystemExit(
                "raw/block_archive 里没有任何产出，说明 07 还没跑过。"
                "这一步是反应式的——先跑 07，有块失败了再用它。")

    if not want:
        print("所有块都有产出，没有需要拆的。")
        return 0

    print(f"{len(blocks)} 个块，其中 {len(want)} 个没有产出（07 失败）：")
    for bid in want:
        b = by_id[bid]
        print(f"  {bid} {b['record_count']:>3}条 | {' / '.join(b['top_actors'][:3])}")

    if args.dry_run:
        print("\n（dry-run，未改动 blocks.jsonl）")
        return 0

    new_blocks = []
    for bid in want:
        b = by_id[bid]
        members = [r for r in b["record_ids"] if r in frames]
        parts = resplit(members, frames, cand, args.sub_cap)
        print(f"\n{bid}: {len(members)} 条 → {len(parts)} 个子块 {[len(p) for p in parts]}")
        for i, part in enumerate(parts):
            actors = collections.Counter()
            for rid in part:
                for a in frames[rid].get("actor") or []:
                    if a.get("name"):
                        actors[a["name"]] += 1
            part.sort(key=lambda r: (-(frames[r].get("information_score") or 0),
                                     -float(frames[r].get("heat") or 0), r))
            new_blocks.append({
                "block_id": f"{bid}S{i:02d}",
                "record_ids": part, "record_count": len(part),
                "est_input_tokens": sum(len(frames[r].get("title", "")) + 45
                                        for r in part),
                "est_output_tokens": len(part) * TOK_PER_RECORD,
                "split_from": bid,
                "top_actors": [a for a, _ in actors.most_common(5)],
            })

    merged = [b for b in blocks if b["block_id"] not in set(want)] + new_blocks
    total = sum(b["record_count"] for b in merged)
    src_total = sum(b["record_count"] for b in blocks)
    if total != src_total:
        raise SystemExit(f"拆分前后记录数不一致：{src_total} → {total}，已中止未写入")

    write_jsonl(rd / "work" / "blocks.jsonl", merged)
    write_csv(rd / "out" / "x1_重拆概览.csv",
              [{"block_id": b["block_id"], "记录数": b["record_count"],
                "拆自": b.get("split_from", ""),
                "主要主体": " / ".join(b["top_actors"])}
               for b in sorted(merged, key=lambda x: -x["record_count"])],
              ["block_id", "记录数", "拆自", "主要主体"])

    print(f"\nblocks.jsonl: {len(blocks)} → {len(merged)} 个块，覆盖 {total} 条（未丢记录）")
    print(f"最大块 {max(b['record_count'] for b in merged)} 条")
    print("\n下一步：重跑 07_block_archive.py。已成功的块走 raw 缓存，不重复计费。")
    log_run(rd / "run_manifest.json",
            {"stage": "x1_presplit_blocks", "blocks_before": len(blocks),
             "blocks_after": len(merged), "resplit": len(want),
             "sub_cap": args.sub_cap})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
