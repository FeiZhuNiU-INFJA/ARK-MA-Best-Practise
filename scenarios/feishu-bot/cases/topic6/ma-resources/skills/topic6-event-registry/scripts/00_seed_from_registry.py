#!/usr/bin/env python3
"""阶段 0：从上一窗口的 Registry 快照预载事件池。跨窗口增量归档的入口。

闭窗批处理的问题是每周从零开始，第二周的「票房破六亿」会新建一个事件，
而不是接到第一周的「票房进展」事件上。这一步让归档能看到历史事件。

做三件事：
1. 读上一窗口的 registry_snapshot.jsonl，转成本窗口的候选事件卡
2. 把快照里的锚点向量注入本窗口的 embeddings，让召回能命中历史事件
3. 造合成 Frame，让事件卡渲染与本窗口新事件走同一套代码

历史事件带 stable_event_id，成员为空。本窗口若有记录归入，它就是「更新」；
一条都没有，它保持原样不动，不会因为本周没热度就消失。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import log_run, read_jsonl, write_jsonl

PRIOR_PREFIX = "PRIOR:"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--prev-registry", required=True, type=Path,
                    help="上一窗口的 out/registry_snapshot.jsonl")
    ap.add_argument("--max-age-windows", type=int, default=4,
                    help="超过这个窗口数没更新的历史事件不再参与召回")
    args = ap.parse_args()

    rd = args.run_dir
    emb_path = rd / "work" / "embeddings.json"
    if not emb_path.exists():
        raise SystemExit(f"缺少 {emb_path}，先跑 04_build_embeddings.py")
    if not args.prev_registry.exists():
        raise SystemExit(f"缺少 {args.prev_registry}")

    snapshot = list(read_jsonl(args.prev_registry))
    blob = json.loads(emb_path.read_text(encoding="utf-8"))
    dim = blob.get("dim")

    prior_events = []
    prior_frames = []
    skipped_stale = 0
    skipped_novec = 0
    for row in snapshot:
        age = int(row.get("windows_since_update") or 0) + 1
        if age > args.max_age_windows:
            skipped_stale += 1
            continue
        vector = row.get("anchor_vector")
        if not vector or (dim and len(vector) != dim):
            skipped_novec += 1
            continue
        stable = row["event_id"]
        synth_rid = f"{PRIOR_PREFIX}{stable}"
        core = row.get("identity_core") or {}
        blob["vectors"][synth_rid] = vector
        blob.setdefault("texts", {})[synth_rid] = row.get("anchor_text", "")

        prior_frames.append({
            "record_id": synth_rid,
            "title": row.get("canonical_title") or row.get("anchor_title", ""),
            "actor": [{"name": n, "type": "OTHER", "actor_id": None,
                       "parent_entity_id": None}
                      for n in str(core.get("actor") or "").split("、") if n],
            "actor_ids": core.get("actor_ids") or [],
            "action": core.get("action", ""),
            "object": core.get("object", ""),
            "event_type": core.get("event_type", ""),
            "parent_hint": row.get("parent_event_name"),
            "stage": (row.get("state") or {}).get("latest_stage"),
            "time": None, "location": None,
            "key_facts": (row.get("state") or {}).get("key_facts") or {},
            # 历史锚点信息量给到上限，避免它被本窗口的新成员抢走锚点位置
            "information_score": 999,
            "heat": 0.0, "platform": "", "is_prior": True,
        })
        prior_events.append({
            "provisional_event_id": f"PRIOR_{stable}",
            "stable_event_id": stable,
            "origin": "prior",
            "windows_since_update": age,
            "members": [],
            "prior_member_count": row.get("member_count", 0),
            "canonical_anchor_record_id": synth_rid,
            "anchor_title": row.get("canonical_title") or row.get("anchor_title", ""),
            "identity_core": core,
            "state": row.get("state") or {},
            "sub_events": [],
            "parent_event_id": row.get("parent_event_id"),
            "parent_event_name": row.get("parent_event_name"),
            "support": row.get("member_count", 0),
            "needs_arbitration": False,
        })

    blob["count"] = len(blob["vectors"])
    emb_path.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    write_jsonl(rd / "work" / "prior_events.jsonl", prior_events)
    write_jsonl(rd / "work" / "prior_frames.jsonl", prior_frames)

    print(f"快照 {len(snapshot)} 个事件 → 预载 {len(prior_events)} 个")
    print(f"  超过 {args.max_age_windows} 个窗口未更新，跳过 {skipped_stale} 个")
    if skipped_novec:
        print(f"  缺锚点向量或维度不符，跳过 {skipped_novec} 个", file=sys.stderr)
    print(f"  embeddings 现有 {blob['count']} 个向量")
    print("\n下一步：跑 07 时加 --prior-events work/prior_events.jsonl "
          "--prior-frames work/prior_frames.jsonl")
    log_run(rd / "run_manifest.json",
            {"stage": "00_seed_from_registry", "snapshot": len(snapshot),
             "loaded": len(prior_events), "skipped_stale": skipped_stale,
             "skipped_novec": skipped_novec})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
