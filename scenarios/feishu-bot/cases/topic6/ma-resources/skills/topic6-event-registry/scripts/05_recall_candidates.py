#!/usr/bin/env python3
"""阶段 5：多路召回。

候选 = Embedding Top-K ∪ Actor 精确 ∪ Object 精确 ∪ 强锚点匹配 ∪ Parent Hint 匹配。

用 Top-K 而不是相似度阈值：阈值拒绝候选，Top-K 保证候选。
实测 `老王探班龙餐馆vlog` 与主事件相似度只有 0.513，在 0.55 阈值下被拒，
但在 Top-10 下必然出现在模型面前。

本脚本只产出「谁和谁值得放在一起让模型看」的邻接表，不做任何合并判定。
不构建连通分量，不做 Union-Find。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    import numpy as np
except ImportError:
    raise SystemExit("需要 numpy：pip install numpy")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import log_run, read_jsonl, write_jsonl


def normalize_key(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--top-k", type=int, default=10,
                    help="Embedding 召回条数。这个值只影响候选数量，不影响结论")
    ap.add_argument("--min-score", type=float, default=0.30,
                    help="仅用于剔除明显无关的噪声候选，不是同事件判据")
    args = ap.parse_args()

    frames_path = args.run_dir / "work" / "frames_resolved.jsonl"
    if not frames_path.exists():
        frames_path = args.run_dir / "work" / "frames.jsonl"
    emb_path = args.run_dir / "work" / "embeddings.json"
    for path in (frames_path, emb_path):
        if not path.exists():
            raise SystemExit(f"缺少 {path}，先跑上游阶段")

    frames = list(read_jsonl(frames_path))
    vectors = json.loads(emb_path.read_text(encoding="utf-8"))["vectors"]
    frames = [f for f in frames if f["record_id"] in vectors]
    frames.sort(key=lambda f: -float(f.get("heat") or 0))
    ids = [f["record_id"] for f in frames]
    by_id = {f["record_id"]: f for f in frames}
    print(f"参与召回 {len(frames)} 条")

    # 精确通道索引
    actor_idx: dict[str, list[str]] = defaultdict(list)
    object_idx: dict[str, list[str]] = defaultdict(list)
    hint_idx: dict[str, list[str]] = defaultdict(list)
    for frame in frames:
        rid = frame["record_id"]
        for actor in frame.get("actor") or []:
            key = actor.get("actor_id") or normalize_key(actor.get("name"))
            if key:
                actor_idx[key].append(rid)
        okey = normalize_key(frame.get("object"))
        if okey:
            object_idx[okey].append(rid)
        hkey = normalize_key(frame.get("parent_hint"))
        if hkey:
            hint_idx[hkey].append(rid)

    matrix = np.asarray([vectors[rid] for rid in ids], dtype=np.float32)
    matrix /= (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)
    pos = {rid: n for n, rid in enumerate(ids)}

    rows = []
    channel_stats: dict[str, int] = defaultdict(int)
    for i, rid in enumerate(ids):
        frame = by_id[rid]
        sims = matrix @ matrix[i]
        sims[i] = -1.0
        # argpartition 只做部分排序，n 大时比全排序快得多
        k = min(args.top_k, len(ids) - 1)
        top = np.argpartition(-sims, k)[:k] if k > 0 else np.array([], dtype=int)

        channels: dict[str, set[str]] = defaultdict(set)
        for j in top:
            if sims[j] >= args.min_score:
                channels[ids[j]].add("embedding")
        for actor in frame.get("actor") or []:
            key = actor.get("actor_id") or normalize_key(actor.get("name"))
            for other in actor_idx.get(key, []):
                if other != rid:
                    channels[other].add("actor_exact")
        okey = normalize_key(frame.get("object"))
        for other in object_idx.get(okey, []):
            if other != rid:
                channels[other].add("object_exact")
        hkey = normalize_key(frame.get("parent_hint"))
        for other in hint_idx.get(hkey, []):
            if other != rid:
                channels[other].add("parent_hint_match")

        candidates = sorted(
            ({"target_id": other, "target_type": "record",
              "channels": sorted(chs),
              "embedding_score": round(float(sims[pos[other]]), 4)}
             for other, chs in channels.items()),
            key=lambda c: -c["embedding_score"])
        for cand in candidates:
            for ch in cand["channels"]:
                channel_stats[ch] += 1
        for rank, cand in enumerate(candidates, 1):
            cand["rank"] = rank

        # 静默否决是漏并唯一查不到痕迹的地方，必须落 rejected
        order = np.argsort(-sims)[:20 + len(channels)]
        rejected = [{"target_id": ids[j], "embedding_score": round(float(sims[j]), 4),
                     "reason": "无召回信号命中"}
                    for j in order if ids[j] not in channels and j != i][:20]

        rows.append({"record_id": rid, "candidates": candidates, "rejected": rejected})

    write_jsonl(args.run_dir / "work" / "candidates.jsonl", rows)

    sizes = [len(r["candidates"]) for r in rows]
    empty = sum(1 for s in sizes if s == 0)
    print(f"候选数：均值 {sum(sizes) / (len(sizes) or 1):.1f}，最大 {max(sizes, default=0)}，"
          f"无候选 {empty} 条")
    print("通道命中：" + " ".join(f"{k}={v}" for k, v in sorted(channel_stats.items())))
    if empty:
        print(f"无候选的 {empty} 条会各自独立成块，归档时只能是单条记录")
    log_run(args.run_dir / "run_manifest.json",
            {"stage": "05_recall_candidates", "records": len(rows),
             "top_k": args.top_k, "min_score": args.min_score,
             "avg_candidates": round(sum(sizes) / (len(sizes) or 1), 2),
             "empty_candidates": empty, "channels": dict(channel_stats)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
