#!/usr/bin/env python3
"""阶段 4：Embedding。

默认 embed 的是 Frame 拼装文本。`--text-source clean_title` 切成只 embed 清洗后的标题，
这是「清洗能否替代 Frame」的对照开关——两种模式读同一份 frames.jsonl，
其余阶段完全不动，所以召回率差异只归因于文本来源这一处。

Embedding 只产出候选，分数只进入特征，不决定关系。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import Embedder, log_run, read_jsonl


def frame_text(frame: dict) -> str:
    actors = "、".join(a.get("name", "") for a in frame.get("actor") or []) or "-"
    parts = [
        f"主体：{actors}",
        f"动作：{frame.get('action') or '-'}",
        f"对象：{frame.get('object') or '-'}",
        f"类型：{frame.get('event_type') or '-'}",
        f"父事件线索：{frame.get('parent_hint') or '-'}",
        f"标题：{frame.get('title') or ''}",
    ]
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument(
        "--model",
        default=os.environ.get(
            "EMBEDDING_MODEL_ID", "doubao-embedding-vision-251215"
        ),
        help="默认使用 doubao-embedding-vision-251215；可通过 "
             "EMBEDDING_MODEL_ID 或 --model 覆盖。vision 模型走方舟 "
             "/embeddings/multimodal，文本逐条请求并由 --concurrency 并发。"
             "模型变化会使旧向量缓存自动失效。",
    )
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--text-source", choices=["frame", "clean_title"], default="frame",
                    help="frame=六字段拼装文本；clean_title=只用清洗后标题")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="并发请求数。vision 接口每条文本一次请求，此参数控制请求并发；"
                         "显式指定旧批量 embedding 模型时仍按 --batch-size 分批")
    args = ap.parse_args()

    src = args.run_dir / "work" / "frames_resolved.jsonl"
    if not src.exists():
        src = args.run_dir / "work" / "frames.jsonl"
    if not src.exists():
        raise SystemExit("缺少 frames_resolved.jsonl / frames.jsonl，先跑 02（或 03）")
    frames = list(read_jsonl(src))

    cache = args.run_dir / "work" / "embeddings_cache.json"
    embedder = Embedder(args.model, cache)
    if args.text_source == "frame":
        texts = [frame_text(f) for f in frames]
    else:
        texts = [f.get("title") or "" for f in frames]
    vectors = embedder.embed(texts, args.batch_size, args.concurrency)
    embedder.save()

    if len(vectors) != len(frames):
        raise SystemExit(f"向量数 {len(vectors)} 与 Frame 数 {len(frames)} 不一致")

    out = args.run_dir / "work" / "embeddings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": args.model,
        "text_source": args.text_source,
        "dim": len(vectors[0]) if vectors else 0,
        "count": len(vectors),
        "vectors": {f["record_id"]: v for f, v in zip(frames, vectors)},
        "texts": {f["record_id"]: t for f, t in zip(frames, texts)},
    }, ensure_ascii=False), encoding="utf-8")

    total = embedder.hits + embedder.misses or 1
    print(f"文本来源 {args.text_source}，向量 {len(vectors)} 条，"
          f"维度 {len(vectors[0]) if vectors else 0}，"
          f"缓存命中 {embedder.hits}/{total} = {embedder.hits / total:.1%}，"
          f"估算 ${embedder.cost():.5f}")
    log_run(args.run_dir / "run_manifest.json",
            {"stage": "04_build_embeddings", "model": args.model,
             "text_source": args.text_source,
             "count": len(vectors), "cache_hits": embedder.hits,
             "input_tokens": embedder.usage["input_tokens"],
             "cost_usd": round(embedder.cost(), 6)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
