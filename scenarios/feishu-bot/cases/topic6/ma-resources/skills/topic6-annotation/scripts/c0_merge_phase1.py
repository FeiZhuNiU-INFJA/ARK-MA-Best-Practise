#!/usr/bin/env python3
"""
c0_merge_phase1.py — 第一阶段合并 (C0 + C3 → 04_合并/phase1_merged)

MA 精简版:
  1. 只做 LEFT JOIN, 不再支持 --patch-run-id 越权分支
     (补标注由 MA 协调器重新委派 datahub_annotate 完成后, 用新 run_id 重跑本脚本)
  2. 基础字段保持英文原名 (row_id/platform/title/hottopic_desc/...) 供 R1~R5 上传时占位符匹配
  3. C0 判断字段去 c0_ 前缀转中文, c0_parse_error 保留原名
  4. C3 字段沿用中文原名

CLI:
  python c0_merge_phase1.py --project-dir /workspace/Projects/W35 \\
    --mode test --run-id 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

C0_RENAME = {
    "c0_商业实体": "商业实体", "c0_热点驱动词": "热点驱动词", "c0_行业归属": "行业归属",
    "c0_营销触发方式": "营销触发方式", "c0_营销维度": "营销维度",
    "c0_平台原生形式": "平台原生形式", "c0_不可用原因": "不可用原因",
    "c0_是否营销可用": "是否营销可用", "c0_判断说明": "判断说明",
}
C3_KEEP = [
    "row_id", "提取节点", "是否节日营销", "涉及品牌", "是否节点定制营销",
    "相关依据", "距节点天数", "是否窗口期内", "c3_parse_error",
]


def _resolve_project_dir(project_dir: str) -> Path:
    p = Path(project_dir)
    if not p.is_absolute():
        p = Path("/workspace") / project_dir
    return p


def run(project_dir: str, mode: str, run_id: int) -> dict:
    proj = _resolve_project_dir(project_dir)

    base_file = (
        proj / "03_抽样" / "sample_500.xlsx" if mode == "test"
        else proj / "02_标准化" / "hot_topics_normalized.xlsx"
    )
    if not base_file.exists():
        raise FileNotFoundError(f"基础表不存在: {base_file}")
    print(f"[merge_p1] 基础表: {base_file}")
    df = pd.read_excel(base_file)
    total = len(df)
    print(f"[merge_p1] 基础表行数: {total}")

    c0_file = proj / "04_标注" / "C0_基础事实" / f"c0_postprocess_r{run_id}.xlsx"
    if not c0_file.exists():
        raise FileNotFoundError(f"C0 结果不存在: {c0_file}")
    print(f"[merge_p1] C0 结果: {c0_file}")
    c0_cols = ["row_id"] + list(C0_RENAME.keys()) + ["c0_parse_error"]
    df_c0 = pd.read_excel(c0_file, usecols=lambda c: c in c0_cols)
    missing = [c for c in C0_RENAME if c not in df_c0.columns]
    if missing:
        raise ValueError(f"C0 缺少字段: {missing}")

    df = df.merge(df_c0, on="row_id", how="left")
    missing_c0_rows = int(df["c0_是否营销可用"].isna().sum())
    if missing_c0_rows:
        print(f"[merge_p1] ⚠️ {missing_c0_rows} 行 C0 未匹配", file=sys.stderr)

    c3_file = proj / "04_标注" / "C3_节点标注" / f"c3_postprocess_r{run_id}.xlsx"
    missing_c3 = False
    if c3_file.exists():
        print(f"[merge_p1] C3 结果: {c3_file}")
        df_c3 = pd.read_excel(c3_file, usecols=lambda c: c in C3_KEEP)
        keep = [c for c in C3_KEEP if c in df_c3.columns]
        df = df.merge(df_c3[keep], on="row_id", how="left")
    else:
        print(f"[merge_p1] ⚠️ C3 结果不存在, 跳过: {c3_file}", file=sys.stderr)
        missing_c3 = True

    df = df.rename(columns=C0_RENAME)

    merge_dir = proj / "04_合并"
    merge_dir.mkdir(parents=True, exist_ok=True)
    out_file = merge_dir / f"phase1_merged_{mode}_r{run_id}.xlsx"
    tmp = out_file.with_suffix(".tmp.xlsx")
    try:
        df.to_excel(tmp, index=False)
        os.replace(tmp, out_file)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    print(f"\n[merge_p1] ✅ 输出: {out_file}")
    print(f"[merge_p1] 规格: {len(df)} 行 × {len(df.columns)} 列")

    print("\n[merge_p1] === 是否营销可用 分布 ===")
    print(df["是否营销可用"].value_counts(dropna=False).to_string())

    return {
        "mode": mode, "run_id": run_id,
        "total_rows": total, "output_rows": len(df),
        "row_match": len(df) == total,
        "missing_c0_rows": missing_c0_rows,
        "missing_c3": missing_c3,
        "usable_yes": int((df["是否营销可用"] == "是").sum()),
        "usable_no": int((df["是否营销可用"] == "否").sum()),
        "out_file": str(out_file),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project-dir", required=True)
    p.add_argument("--mode", default="test", choices=["test", "full"])
    p.add_argument("--run-id", type=int, required=True)
    args = p.parse_args()
    try:
        result = run(args.project_dir, args.mode, args.run_id)
        print("\n=== 合并完成 ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
