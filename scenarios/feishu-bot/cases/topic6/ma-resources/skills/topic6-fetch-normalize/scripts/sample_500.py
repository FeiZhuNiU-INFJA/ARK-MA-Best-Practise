#!/usr/bin/env python3
"""
sample_500.py — 分层随机抽样 500 条(test 模式用)

MA 口径 v1(2026-09-24):
  - --project-dir 支持绝对路径;相对路径以 /workspace 为根
  - 固定随机种子 42,结果可复现
  - 最大余数法按平台行数占比分配

用法:
    python sample_500.py \\
        --project-dir /workspace/Projects/W35_20260824-20260830

输出:
    {project_dir}/03_抽样/sample_500.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

SAMPLE_SIZE = 500
RANDOM_SEED = 42
PLATFORM_COL = "platform"

WORKSPACE_ROOT = Path(os.environ.get("MA_WORKSPACE", "/workspace"))
DEFAULT_INPUT_REL = "02_标准化/hot_topics_normalized.xlsx"
DEFAULT_OUTPUT_REL = "03_抽样/sample_500.xlsx"


def allocate(counts: dict, total: int) -> dict:
    n_total = sum(counts.values())
    if n_total == 0:
        return {k: 0 for k in counts}
    exact = {k: v / n_total * total for k, v in counts.items()}
    floors = {k: int(v) for k, v in exact.items()}
    remainder = total - sum(floors.values())
    by_frac = sorted(exact.keys(), key=lambda k: exact[k] - floors[k], reverse=True)
    for k in by_frac[:remainder]:
        floors[k] += 1
    return floors


def stratified_sample(df: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    platform_counts = df[PLATFORM_COL].value_counts().to_dict()
    allocation = allocate(platform_counts, n)

    parts = []
    shortfall = 0

    for platform, quota in sorted(allocation.items()):
        sub = df[df[PLATFORM_COL] == platform]
        actual = min(len(sub), quota)
        if actual < quota:
            shortfall += quota - actual
        parts.append(sub.sample(n=actual, random_state=seed))

    result = pd.concat(parts, ignore_index=True)

    if shortfall > 0:
        largest_platform = max(platform_counts, key=platform_counts.get)
        already_in_result = result[result[PLATFORM_COL] == largest_platform]
        available_extra = platform_counts[largest_platform] - len(already_in_result)
        extra = min(shortfall, available_extra)
        if extra > 0:
            taken_row_ids = set(already_in_result["row_id"].tolist()) if "row_id" in result.columns else set()
            if taken_row_ids:
                pool = df[
                    (df[PLATFORM_COL] == largest_platform) &
                    (~df["row_id"].isin(taken_row_ids))
                ]
            else:
                pool = df[df[PLATFORM_COL] == largest_platform].iloc[len(already_in_result):]
            result = pd.concat([result, pool.sample(n=extra, random_state=seed)], ignore_index=True)

    return result.reset_index(drop=True)


def _project_path(project_dir: str) -> Path:
    p = Path(project_dir)
    return p if p.is_absolute() else WORKSPACE_ROOT / project_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="分层随机抽样 500 条(MA 口径)")
    parser.add_argument("--project-dir", required=True,
                        help="项目目录,绝对路径或相对 /workspace")
    parser.add_argument("--size", type=int, default=SAMPLE_SIZE,
                        help=f"抽样数量(默认 {SAMPLE_SIZE})")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED,
                        help=f"随机种子(默认 {RANDOM_SEED})")
    args = parser.parse_args()

    proj = _project_path(args.project_dir)
    input_path = proj / DEFAULT_INPUT_REL
    output_path = proj / DEFAULT_OUTPUT_REL

    if not input_path.exists():
        print(f"[sample_500] 输入文件不存在:{input_path}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_excel(str(input_path))
    print(f"[sample_500] 读入:{input_path}({len(df)} 行)")

    if PLATFORM_COL not in df.columns:
        print(f"[sample_500] 缺少 '{PLATFORM_COL}' 列", file=sys.stderr)
        sys.exit(1)

    actual_size = min(args.size, len(df))
    if actual_size < args.size:
        print(f"[sample_500] 数据总量 {len(df)} 行 ≤ 抽样上限 {args.size},全量输出。")

    platform_counts = df[PLATFORM_COL].value_counts().to_dict()
    allocation = allocate(platform_counts, actual_size)
    print(f"[sample_500] 分层配额(seed={args.seed}):")
    for p in sorted(platform_counts):
        quota = allocation.get(p, 0)
        actual = min(platform_counts[p], quota)
        note = " (不足,已取全量)" if actual < quota else ""
        print(f"  {p}:总 {platform_counts[p]} 行 → 配额 {quota} → 实际抽 {actual}{note}")

    sample = stratified_sample(df, actual_size, args.seed)

    if len(sample) != actual_size:
        print(f"[sample_500] WARNING: 预期 {actual_size} 行,实际 {len(sample)} 行",
              file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample.to_excel(str(output_path), index=False)
    print(f"[sample_500] 已输出:{output_path}({len(sample)} 行)")

    stat = sample[PLATFORM_COL].value_counts().sort_index()
    print("[sample_500] 输出平台分布:")
    for p, n in stat.items():
        print(f"  {p}:{n} 行")


if __name__ == "__main__":
    main()
