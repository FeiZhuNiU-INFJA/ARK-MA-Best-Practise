#!/usr/bin/env python3
"""阶段 x4：出明细表。不调模型。

**输出形态是「原表原样 + 后面接几列」**，不是重排过的新表。分析师要能拿它跟自己
手里的原始导出对照，所以原有列名、列序、行序、行数一个不动，事件归属追加在最后。

不出热度排名。排名要另一套口径（跨平台热度不可加），这一步只负责把「这条属于哪个
事件」标清楚。

追加的列：

    事件ID / 一级事件名 / 二级角度 / 事件成员数 / 归属置信度 / 状态 / 说明

状态只有四种，业务语言，不带内审代号：

    多条记录的事件   这条和别的记录一起构成一个事件
    独立热点         只有这一条，自成一件事
    剔除后独立       原本被归进某事件，置信度不够被摘出来，现在自成一条
    未进入聚类       判定为不成事件，或上游没产出

用法：
    python3 x4_detail_table.py --run-dir . --source 原始数据.csv
"""

from __future__ import annotations

import argparse
import csv
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import log_run, read_jsonl, write_csv

# 内审代号 → 业务语言。表是给分析师看的，不是给流程看的
WHY_NOT = {
    "GENERIC_KNOWLEDGE": "泛化知识或科普，没有具体发生的事",
    "OPINION_SOLICIT": "纯观点征求，没有事实内核",
    "EMOTION_OR_DISPLAY": "情绪表达或才艺展示，不构成事件",
    "ENTITY_ONLY": "只有主体名，没写发生了什么",
    "TRUNCATED": "标题被截断，看不出完整事件",
    "INSUFFICIENT_INFO": "信息量不足，无法判断",
}
ADD_COLS = ["事件ID", "一级事件名", "二级角度", "事件成员数", "归属置信度",
            "状态", "说明"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--source", required=True, type=Path,
                    help="进入阶段 00 的那份 CSV（必须含 record_id 列）")
    ap.add_argument("--id-col", default="record_id")
    ap.add_argument("--out", default="热点明细_含事件归属.csv")
    args = ap.parse_args()

    rd = args.run_dir
    ev_p = rd / "work" / "block_events.jsonl"
    if not ev_p.exists():
        raise SystemExit(f"缺少 {ev_p}，先跑 07_block_archive.py")
    with args.source.open(encoding="utf-8-sig", newline="") as h:
        src = list(csv.DictReader(h))
    if not src:
        raise SystemExit(f"{args.source} 没有数据行")
    if args.id_col not in src[0]:
        raise SystemExit(f"{args.source} 缺列 {args.id_col}，实际列名：{list(src[0])}")
    base_cols = [c for c in src[0] if c not in ADD_COLS]

    events = list(read_jsonl(ev_p))
    en = {}
    p = rd / "work" / "eventness.jsonl"
    if p.exists():
        en = {r["record_id"]: r for r in read_jsonl(p)}

    owner, angle = {}, {}
    for e in events:
        for rid in e["member_record_ids"]:
            owner[rid] = e
        for f in e.get("facets") or []:
            for rid in f.get("members") or []:
                angle[rid] = f.get("angle") or ""

    rows, stat = [], collections.Counter()
    for r in src:
        rid = str(r[args.id_col]).strip()
        e = owner.get(rid)
        if e is None:
            code = (en.get(rid) or {}).get("reason_code", "")
            note = WHY_NOT.get(code) or ("判定为不成事件"
                                        if rid in en else "上游未产出该条")
            add = {"事件ID": "", "一级事件名": "", "二级角度": "", "事件成员数": "",
                   "归属置信度": "", "状态": "未进入聚类", "说明": note}
        else:
            multi = e["member_count"] > 1
            kicked = str(e.get("origin") or "").startswith("x2_kicked")
            conf = (e.get("member_confidence") or {}).get(rid)
            add = {"事件ID": e["event_id"], "一级事件名": e["event_name"],
                   "二级角度": angle.get(rid, ""),
                   "事件成员数": e["member_count"],
                   "归属置信度": "" if conf is None else conf,
                   "状态": ("多条记录的事件" if multi else
                          "剔除后独立" if kicked else "独立热点"),
                   "说明": (e.get("member_notes") or {}).get(rid, "")}
        stat[add["状态"]] += 1
        rows.append({**{c: r.get(c, "") for c in base_cols}, **add})

    out_p = rd / "out" / args.out
    write_csv(out_p, rows, base_cols + ADD_COLS)

    if len(rows) != len(src):
        raise SystemExit(f"行数不一致：原表 {len(src)} → 输出 {len(rows)}")
    dup = [k for k, v in collections.Counter(
        r["事件ID"] + "|" + str(r[args.id_col]) for r in rows).items() if v > 1]
    print(f"原表 {len(src)} 行 → 输出 {len(rows)} 行，列 {len(base_cols)} + {len(ADD_COLS)}")
    print("状态分布：" + " ".join(f"{k}={v}" for k, v in stat.most_common()))
    if dup:
        print(f"警告：有 {len(dup)} 个重复的记录归属，需排查", file=sys.stderr)

    multi = [e for e in events if e["member_count"] > 1]
    reg = [{"事件ID": e["event_id"], "一级事件名": e["event_name"],
            "成员数": e["member_count"], "锚点": e.get("anchor", ""),
            "届次": e.get("series_instance") or "",
            "二级角度": " / ".join((f.get("angle") or "")
                                for f in e.get("facets") or []),
            "描述": e.get("event_description", ""),
            "平台": " / ".join(sorted(set(
                (e.get("state") or {}).get("platforms") or []))),
            "来源": e.get("origin", "")}
           for e in sorted(multi, key=lambda x: -x["member_count"])]
    write_csv(rd / "out" / "事件清单.csv", reg,
              ["事件ID", "一级事件名", "成员数", "锚点", "届次", "二级角度",
               "描述", "平台", "来源"])
    print(f"事件 {len(events)} 个（多成员 {len(multi)} / 单条 {len(events)-len(multi)}）")
    print(f"→ out/{args.out}\n→ out/事件清单.csv")
    log_run(rd / "run_manifest.json",
            {"stage": "x4_detail_table", "rows": len(rows),
             "cols": len(base_cols) + len(ADD_COLS),
             "events": len(events), "multi_events": len(multi),
             "status": dict(stat)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
