#!/usr/bin/env python3
"""阶段 x0：跨平台合流。不调模型。

阶段 00 按平台走四套清洗规则，所以四个平台各跑一次、各产出一份
`work/clean_titles.jsonl`。这一步把它们拼成一份，供阶段 01 往后共用一套判定。

为什么合流放在 00 之后、01 之前：清洗规则必须按平台分（知乎 100% 带提问外壳、
微博大量裸话题词），判定规则必须共用一套（否则同一件事在不同平台被判成不同档
就没法合并）。这个位置是唯一同时满足两者的接缝。

**跨平台合并是可选路线。** skill 主线是单平台（一个平台一个运行目录），跨平台
只在需要「同一件事在几个平台同时热」这种口径时用。实测跨平台事件占多成员事件
的 56%，所以这条路线有价值，但它引入了跨体裁误合并风险——必须配合 x2 置信度
筛查兜住，不要单独用。

用法：
    python3 x0_merge_platforms.py --out-dir <合流后的运行目录> \\
        --from <知乎目录> <微博目录> <抖音目录> <B站目录>
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import ids_hash, read_jsonl, write_csv, write_jsonl


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", required=True, type=Path,
                    help="合流后的运行目录，阶段 01 往后都在这里跑")
    ap.add_argument("--from", dest="sources", required=True, nargs="+", type=Path,
                    help="各平台的运行目录，每个都要已经跑完阶段 00")
    args = ap.parse_args()

    merged: list[dict] = []
    seen: dict[str, str] = {}
    dup = 0
    for src in args.sources:
        path = src / "work" / "clean_titles.jsonl"
        if not path.exists():
            raise SystemExit(f"缺少 {path}，该平台还没跑阶段 00")
        rows = list(read_jsonl(path))
        plats = collections.Counter(r.get("platform", "?") for r in rows)
        # 同一份原始 CSV 拆平台跑时 record_id 是全局唯一的；如果有人分别编号
        # 就会撞号，撞号会让下游把两条不同记录当成一条，必须拦下来
        for r in rows:
            rid = r["record_id"]
            if rid in seen:
                dup += 1
                if dup <= 5:
                    print(f"  撞号 {rid}：已存在于 {seen[rid]}，又出现在 {src.name}",
                          file=sys.stderr)
                continue
            seen[rid] = src.name
            merged.append(r)
        print(f"  {src.name}: {len(rows)} 条 | " +
              " ".join(f"{k}={v}" for k, v in plats.most_common()))

    if dup:
        raise SystemExit(
            f"有 {dup} 个 record_id 在多个平台目录里重复。合流前请确认各平台是从"
            f"同一份原始 CSV 按 platform 过滤出来的（record_id 全局编号），"
            f"而不是各自从 1 开始编号")

    reverted = sum(1 for r in merged if r.get("reverted"))
    plats = collections.Counter(r.get("platform", "?") for r in merged)

    write_jsonl(args.out_dir / "work" / "clean_titles.jsonl", merged)
    rows_csv = []
    for r in merged:
        rows_csv.append({**r,
                         "ops": ",".join(r["ops"]) if isinstance(r.get("ops"), list)
                         else r.get("ops", ""),
                         "flags": ",".join(r["flags"]) if isinstance(r.get("flags"), list)
                         else r.get("flags", "")})
    write_csv(args.out_dir / "out" / "x0_合流后清洗结果.csv", rows_csv,
              ["record_id", "platform", "title", "clean_title", "ops", "flags",
               "reverted", "heat"])

    print(f"\n合流 {len(merged)} 条 → {args.out_dir}/work/clean_titles.jsonl")
    print("平台分布：" + " ".join(f"{k}={v}" for k, v in plats.most_common()))
    print(f"input_hash={ids_hash([r['record_id'] for r in merged])}")
    print(f"清洗被回退的 {reverted} 条（清洗新增了内容，本地校验拦下并回退原标题）")
    print("\n下一步：在合流目录里跑 01_eventness.py，往后全流程共用一套判定口径。")
    print("提醒：跨平台路线必须配合 x2_confidence_filter.py，否则跨体裁误合并没人兜。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
