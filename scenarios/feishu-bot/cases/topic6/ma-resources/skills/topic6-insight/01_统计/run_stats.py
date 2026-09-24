#!/usr/bin/env python3
"""
01_统计 · 编排入口（MA 版）
读宽表 → 标准化字段 → 过滤营销可用 → 调用4个版块统计函数 → 写出 e1_data.md ~ e4_data.md。

MA 适配：删掉 PROJECT_ROOT=parents[2] 上溯逻辑，--project-dir 支持绝对/相对，
相对路径以 WORKSPACE_ROOT=/workspace 为根。
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = Path("/workspace")

from e1_industry import prep_e1_industry
from e2_nodes import prep_e2_nodes
from e3_platforms import prep_e3_platforms, prep_e3_word_freq_audit
from e4_marketing import prep_e4_marketing, prep_e4_candidates


def _latest_run_id(directory: Path, glob_pattern: str) -> int | None:
    """扫描目录下已有 _r{N} 文件，返回已存在的最大编号（无匹配返回 None）。"""
    nums = []
    for f in directory.glob(glob_pattern):
        m = re.search(r"_r(\d+)\.xlsx$", f.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else None


def normalize_wide_table(df: pd.DataFrame) -> pd.DataFrame:
    if "heat_score" in df.columns and "标准化热度分" not in df.columns:
        df = df.rename(columns={"heat_score": "标准化热度分"})
    rename_map = {
        c: c[3:]
        for c in df.columns
        if c.startswith("c1_") and not c.endswith("_error")
    }
    if rename_map:
        df = df.rename(columns=rename_map)
    if "platform" in df.columns and "平台" not in df.columns:
        df = df.rename(columns={"platform": "平台"})
    if "title" in df.columns and "热点标题" not in df.columns:
        df = df.rename(columns={"title": "热点标题"})
    if "热点驱动词" not in df.columns:
        df["热点驱动词"] = ""
    return df


def filter_usable(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["是否营销可用"] == "是"].copy()


def main():
    parser = argparse.ArgumentParser(description="01_统计：宽表 → 4 版块数据预处理")
    parser.add_argument("--project-dir", required=True,
                        help="项目目录（绝对路径，或相对 /workspace）")
    parser.add_argument("--mode", choices=["test", "full"], default="full")
    parser.add_argument("--publish-date", default=None,
                        help="报告发布日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--run-id", type=int, default=None,
                        help="宽表版本号（不传时自动取已存在的最新一轮）")
    parser.add_argument("--out-dir", default=None,
                        help="输出目录覆盖（默认 {project_dir}/06_洞察/）")
    args = parser.parse_args()

    proj = Path(args.project_dir)
    if not proj.is_absolute():
        proj = WORKSPACE_ROOT / proj
    if not proj.exists():
        print(f"[run_stats] ❌ 项目目录不存在：{proj}", file=sys.stderr)
        sys.exit(1)

    pub_date = (
        date.fromisoformat(args.publish_date) if args.publish_date else date.today()
    )
    print(f"[run_stats] 发布日：{pub_date}")

    merge_dir = proj / "05_合并"
    run_id = args.run_id
    if run_id is None:
        run_id = _latest_run_id(merge_dir, f"wide_table_{args.mode}_r*.xlsx")

    if run_id is not None:
        wide_file = merge_dir / f"wide_table_{args.mode}_r{run_id}.xlsx"
        print(f"[run_stats] run_id = r{run_id}")
    else:
        wide_file = merge_dir / f"wide_table_{args.mode}.xlsx"
        print(f"[run_stats] 未找到版本化宽表，回退旧命名")

    if not wide_file.exists():
        print(f"[run_stats] ❌ 宽表不存在：{wide_file}", file=sys.stderr)
        sys.exit(1)

    print(f"[run_stats] 读取宽表：{wide_file}")
    df = pd.read_excel(wide_file, dtype=str)
    df = normalize_wide_table(df)
    for col in ["标准化热度分", "hotpost_num"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df_y = filter_usable(df)
    print(f"[run_stats] 总条目：{len(df)}，营销可用：{len(df_y)}")

    out_dir = Path(args.out_dir) if args.out_dir else (proj / "06_洞察")
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        ("E1 行业数据",   "e1_data.md",  lambda: prep_e1_industry(df_y, proj)),
        ("E3 平台数据",   "e3_data.md",  lambda: prep_e3_platforms(df_y)),
        ("E4 营销数据",   "e4_data.md",  lambda: prep_e4_marketing(df_y)),
    ]

    for label, fname, fn in tasks:
        print(f"[run_stats] 生成 {label}...")
        content = fn()
        (out_dir / fname).write_text(content, encoding="utf-8")
        print(f"[run_stats]   ✅ {out_dir / fname}")

    print("[run_stats] 生成 E3 平台热点词审计表...")
    audit_content = prep_e3_word_freq_audit(df_y)
    (out_dir / "e3_word_freq_audit.md").write_text(audit_content, encoding="utf-8")
    print(f"[run_stats]   ✅ {out_dir / 'e3_word_freq_audit.md'}")

    print("[run_stats] 生成 E2 节点数据...")
    e2_result = prep_e2_nodes(df_y, pub_date)
    e2_flags_path = out_dir / "e2_flags.json"
    e2_flags_path.write_text(
        json.dumps(e2_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[run_stats]   ✅ {e2_flags_path}")

    print("[run_stats] 生成 E4 候选打标用数据...")
    e4_candidates = prep_e4_candidates(df_y)
    e4_candidates_path = out_dir / "e4_candidates.json"
    e4_candidates_path.write_text(
        json.dumps(e4_candidates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[run_stats]   ✅ {e4_candidates_path}")

    print(f"\n[run_stats] ✅ 全部完成。输出目录：{out_dir}")


if __name__ == "__main__":
    main()
