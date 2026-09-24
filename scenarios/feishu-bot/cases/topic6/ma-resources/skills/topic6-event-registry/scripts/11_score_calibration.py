#!/usr/bin/env python3
"""校准打分。不调模型。

读人工回填的 gt_verdict，算指标并把置信档实测错误率写进 run_manifest.json。

优先级只有一条：**False Merge Rate 必须最低**。理由不对称——错拆还能在后续
补救，错合会污染整个事件，并让事件名退化成「系列事件」这种没法用的形式。

校准表绑定 prompt_version + model + input_hash。换提示词或换模型，校准表作废
必须重测——置信度分布会随两者变化。
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import log_run, write_csv

MULTI_VERDICTS = {"CORRECT", "WRONG_MERGE", "SHOULD_SPLIT", "WRONG_NAME", "AMBIGUOUS"}
SINGLE_VERDICTS = {"CORRECT", "SHOULD_MERGE"}
MIN_BAND = 20


def load(path: Path, required: bool = True) -> list[dict]:
    if not path.exists():
        if required:
            raise SystemExit(f"缺少 {path}，先跑 10_build_calibration_sample.py")
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def filled(rows: list[dict], allowed: set[str], label: str) -> list[dict]:
    out, bad = [], []
    for r in rows:
        v = (r.get("gt_verdict") or "").strip().upper()
        if not v:
            continue
        if v in allowed:
            out.append({**r, "gt": v})
        else:
            bad.append(r.get("sample_id"))
    if bad:
        print(f"{label}：{len(bad)} 条 gt_verdict 取值非法，已忽略：{bad[:5]}",
              file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    args = ap.parse_args()
    out = args.run_dir / "out"

    multi_all = load(out / "校准抽样_多成员事件.csv")
    single_all = load(out / "校准抽样_单成员.csv", required=False)
    ne_all = load(out / "校准抽样_非事件.csv", required=False)

    multi = filled(multi_all, MULTI_VERDICTS, "多成员事件")
    single = filled(single_all, SINGLE_VERDICTS, "单成员")
    if not multi:
        raise SystemExit("多成员事件表一条都没填。先按 out/校准填写说明.md 人工填写。")

    print(f"多成员事件：{len(multi_all)} 抽样，已填 {len(multi)} "
          f"({len(multi) / len(multi_all):.0%})")
    if len(multi) < len(multi_all) * 0.8:
        print("填写率低于 80%，下面的指标只反映已填部分", file=sys.stderr)

    def rate(pool, verdicts):
        if not pool:
            return (0.0, 0, 0)
        hits = sum(1 for r in pool if r["gt"] in verdicts)
        return (hits / len(pool), hits, len(pool))

    fmr, fm_n, fm_d = rate(multi, {"WRONG_MERGE"})
    split_r, sp_n, sp_d = rate(multi, {"SHOULD_SPLIT"})
    prec, ok_n, ok_d = rate(multi, {"CORRECT"})
    name_r, nm_n, nm_d = rate(multi, {"WRONG_NAME"})
    miss_r, ms_n, ms_d = rate(single, {"SHOULD_MERGE"})

    ne_filled = [r for r in ne_all if (r.get("gt_verdict") or "").strip()]
    ne_wrong = sum(1 for r in ne_filled
                   if r["gt_verdict"].strip().upper() not in {"CORRECT", "OK"})
    ne_rate = ne_wrong / len(ne_filled) if ne_filled else None

    print("\n核心指标")
    print(f"  False Merge Rate      {fmr:>6.1%}  ({fm_n}/{fm_d})   ← 优先压最低")
    print(f"  多成员事件正确率      {prec:>6.1%}  ({ok_n}/{ok_d})")
    print(f"  该拆未拆率            {split_r:>6.1%}  ({sp_n}/{sp_d})")
    print(f"  名称不覆盖成员率      {name_r:>6.1%}  ({nm_n}/{nm_d})")
    if ms_d:
        print(f"  单成员漏并率          {miss_r:>6.1%}  ({ms_n}/{ms_d})")
    if ne_rate is None:
        print(f"  非事件误杀率          未填（{len(ne_all)} 条待核）")
    else:
        print(f"  非事件误杀率          {ne_rate:>6.1%}  ({ne_wrong}/{len(ne_filled)})"
              f"   ← 唯一真正丢数据的判定")

    table = []
    by_band: dict[str, list[dict]] = collections.defaultdict(list)
    for r in multi:
        by_band[r.get("置信区间") or "无置信度"].append(r)
    print("\n置信度校准表（多成员事件）")
    for band in ["≥0.9", "0.8~0.9", "0.7~0.8", "<0.7", "无置信度"]:
        pool = by_band.get(band)
        if not pool:
            continue
        wrong = sum(1 for r in pool if r["gt"] != "CORRECT")
        err = wrong / len(pool)
        usable = len(pool) >= MIN_BAND
        table.append({"置信区间": band, "抽样量": len(pool),
                      "实测错误率": round(err, 4), "样本量达标": usable,
                      "可否自动路由": "可评估" if usable else f"不足{MIN_BAND}条，禁止据此设阈值"})
        note = "" if usable else f"  ← 不足 {MIN_BAND} 条，不能据此设阈值"
        print(f"  {band:<10} 抽样{len(pool):>4}  实测错误率 {err:>6.1%}{note}")

    print("\n判定分布")
    for key, n in sorted(collections.Counter(r["gt"] for r in multi).items()):
        print(f"  多成员/{key:<14} {n}")
    for key, n in sorted(collections.Counter(r["gt"] for r in single).items()):
        print(f"  单成员/{key:<14} {n}")

    write_csv(out / "置信度校准表.csv", table,
              ["置信区间", "抽样量", "实测错误率", "样本量达标", "可否自动路由"])
    write_csv(out / "校准指标.csv",
              [{"指标": "False Merge Rate", "值": round(fmr, 4),
                "分子": fm_n, "分母": fm_d, "优先级": "最高"},
               {"指标": "多成员事件正确率", "值": round(prec, 4),
                "分子": ok_n, "分母": ok_d, "优先级": "高"},
               {"指标": "该拆未拆率", "值": round(split_r, 4),
                "分子": sp_n, "分母": sp_d, "优先级": "中"},
               {"指标": "名称不覆盖成员率", "值": round(name_r, 4),
                "分子": nm_n, "分母": nm_d, "优先级": "中"},
               {"指标": "单成员漏并率", "值": round(miss_r, 4) if ms_d else "",
                "分子": ms_n, "分母": ms_d, "优先级": "中"},
               {"指标": "非事件误杀率",
                "值": "" if ne_rate is None else round(ne_rate, 4),
                "分子": ne_wrong, "分母": len(ne_filled), "优先级": "必须单独测"}],
              ["指标", "值", "分子", "分母", "优先级"])

    log_run(args.run_dir / "run_manifest.json",
            {"stage": "11_score_calibration",
             "filled": len(multi), "total": len(multi_all),
             "false_merge_rate": round(fmr, 4),
             "multi_event_precision": round(prec, 4),
             "should_split_rate": round(split_r, 4),
             "wrong_name_rate": round(name_r, 4),
             "single_miss_merge_rate": round(miss_r, 4) if ms_d else None,
             "non_event_false_negative": None if ne_rate is None else round(ne_rate, 4),
             "calibration_table": table,
             "note": "校准表绑定 prompt_version + model + input_hash，"
                     "换提示词或模型即作废"})

    if fmr > 0:
        print(f"\nFalse Merge Rate {fmr:.1%} > 0，逐个看 WRONG_MERGE 的"
              f"「应移出的记录」列，判断是判据问题还是少样本没覆盖", file=sys.stderr)
    if all(not t["样本量达标"] for t in table):
        print("所有置信档样本量都不足，校准表暂不可用于设阈值", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
