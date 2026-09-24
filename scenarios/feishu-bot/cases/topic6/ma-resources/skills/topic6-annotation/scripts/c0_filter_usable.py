#!/usr/bin/env python3
"""
c0_filter_usable.py — 从 phase1_merged 筛选"营销可用"子集供 R1~R5 使用

MA 精简版:
  1. 去掉 --force-accept-unresolved / --reason 越权分支
     unresolved_ratio > 5% 直接 raise, 由 MA 协调器决定是否重新委派子 Agent 补标注
  2. 默认保留策略保持不变: is_usable | c0_parse_error | missing_c0
  3. 输出保留 phase1_merged 全部列 (供 R1~R5 上传时占位符匹配)

CLI:
  python c0_filter_usable.py --project-dir /workspace/Projects/W35 \\
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

UNRESOLVED_THRESHOLD = 0.05


def _resolve_project_dir(project_dir: str) -> Path:
    p = Path(project_dir)
    if not p.is_absolute():
        p = Path("/workspace") / project_dir
    return p


def run(project_dir: str, mode: str, run_id: int) -> dict:
    proj = _resolve_project_dir(project_dir)

    merged_file = proj / "04_合并" / f"phase1_merged_{mode}_r{run_id}.xlsx"
    if not merged_file.exists():
        raise FileNotFoundError(f"phase1_merged 不存在: {merged_file}")
    print(f"[filter] 读取: {merged_file}")
    df = pd.read_excel(merged_file)
    total = len(df)
    print(f"[filter] 合并宽表行数: {total}")

    if "是否营销可用" not in df.columns:
        raise ValueError("合并宽表缺少 '是否营销可用' 列")

    is_usable = df["是否营销可用"] == "是"
    parse_error = df.get("c0_parse_error", pd.Series(0, index=df.index)).fillna(0).astype(int) == 1
    missing_c0 = df["是否营销可用"].isna()
    keep_mask = is_usable | parse_error | missing_c0

    usable_count = int(is_usable.sum())
    parse_error_count = int((parse_error & ~is_usable).sum())
    missing_count = int((missing_c0 & ~parse_error).sum())
    excluded_count = int((~keep_mask).sum())
    kept_count = int(keep_mask.sum())

    print(f"[filter] 明确可用: {usable_count}")
    print(f"[filter] C0 解析失败但保留: {parse_error_count}")
    print(f"[filter] C0 缺失但保留: {missing_count}")
    print(f"[filter] 明确不可用 (排除): {excluded_count}")
    print(f"[filter] 保留合计: {kept_count}/{total} ({kept_count/total:.1%})")

    if kept_count == 0:
        raise RuntimeError("筛选后保留 0 行, 请检查 phase1_merged 结果")

    unresolved_ratio = (parse_error_count + missing_count) / total
    if unresolved_ratio > UNRESOLVED_THRESHOLD:
        raise RuntimeError(
            f"解析失败/缺失占比 {unresolved_ratio:.1%} 超过 {UNRESOLVED_THRESHOLD:.0%} 阈值, "
            "由协调器决定是否补标注后重新触发本步骤"
        )

    df_out = df.loc[keep_mask].copy()

    out_dir = proj / "04_标注" / "_可用子集"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"usable_subset_{mode}_r{run_id}.xlsx"
    tmp = out_file.with_suffix(".tmp.xlsx")
    try:
        df_out.to_excel(tmp, index=False)
        os.replace(tmp, out_file)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    print(f"\n[filter] ✅ 可用子集: {out_file}")
    print(f"[filter] R1~R5 将只处理 {kept_count} 行, 而不是全部 {total} 行")

    return {
        "mode": mode, "run_id": run_id, "total_rows": total,
        "usable_count": usable_count,
        "parse_error_included": parse_error_count,
        "missing_included": missing_count,
        "excluded_count": excluded_count,
        "kept_count": kept_count,
        "kept_ratio": round(kept_count / total, 4),
        "unresolved_ratio": round(unresolved_ratio, 4),
        "merged_source": str(merged_file),
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
        print("\n=== 筛选完成 ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
