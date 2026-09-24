#!/usr/bin/env python3
"""
01_统计 · 共享工具函数
跨版块复用的基础设施。专属逻辑放对应的 e{N}_xxx.py，这里只放≥2个版块共用的函数。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


def fmt_score(v, suffix: str = " 分") -> str:
    """格式化标准化分：保留 1 位小数 + 可选后缀（默认" 分"）。
    suffix="" 用于不带"分"字的场景（如E3热点事件/平台热点词的热度值列，2026-09-15新增）。"""
    try:
        f = float(v)
        return f"{f:.1f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def top_titles_str(grp: pd.DataFrame, n: int = 5) -> str:
    """取 grp 中标准化分最高的 top-n title，用 <br> 分隔"""
    top = grp.nlargest(min(n, len(grp)), "标准化热度分")["热点标题"].tolist()
    return "<br>".join(top)


def top_titles_with_links_str(grp: pd.DataFrame, n: int = 5, show_platform: bool = True) -> str:
    """取 grp 中标准化分最高的 top-n 条，格式化为 "- [标题](链接) (平台)"，<br>分隔。
    链接/平台取自宽表原始列（不由 LLM 编造/转写时改写——LLM 只负责原样转写这段文本）。
    链接缺失时退化为 "- 标题 (平台)"（不留空的 markdown 链接语法）。
    show_platform=False 用于调用方已按平台分组、平台名冗余的场景（如E3，2026-09-15新增）。
    标题原样保留时会转义"|"为"\\|"（部分B站标题自带竖线，否则会把表格撑破一列）。"""
    top = grp.nlargest(min(n, len(grp)), "标准化热度分")
    lines = []
    for _, r in top.iterrows():
        title = safe_field(r, "热点标题", "").replace("|", "\\|")
        link = safe_field(r, "链接", "")
        platform = safe_field(r, "平台", "")
        text = f"[{title}]({link})" if (link and link != "—") else title
        if show_platform:
            text += f" ({platform})"
        lines.append(f"- {text}")
    return "<br>".join(lines)


def safe_field(r, key: str, default: str = "—") -> str:
    """安全取值：key不存在或值为NaN/空字符串时返回default（`x or default`对NaN不生效）"""
    v = r.get(key)
    if v is None or (isinstance(v, float) and pd.isna(v)) or not str(v).strip():
        return default
    return v


def explode_pipe(series: pd.Series) -> pd.Series:
    """将 | 分隔的多值字段展开为单值 Series"""
    return series.fillna("").str.split("|").explode().str.strip()


def explode_aligned_pipe_cols(df: pd.DataFrame, anchor_col: str, aligned_cols: list) -> pd.DataFrame:
    """按 anchor_col（如"提取节点"）用 | 拆分行数为基准，把 aligned_cols 里的其他字段也按位置对齐拆开。

    对应 c3_v6_flatten.py 的广播存储惯例：某字段若对该行所有节点取值相同（含全空），
    只存一个值不加 |；段数少于 anchor 段数时视为广播值，重复填充到每个展开位置，
    不是"这个字段缺了几段"。段数相等则按位置一一对应拆开。

    anchor_col 为空/NaN 的行直接跳过（无命中，不产出任何展开行）。
    返回值：行数 = 原表按 anchor 段数展开后的总数，其余原始列原样重复填充到每个展开行。
    """
    records = []
    for _, row in df.iterrows():
        anchor_val = row.get(anchor_col)
        if pd.isna(anchor_val) or str(anchor_val).strip() == "":
            continue
        names = [n.strip() for n in str(anchor_val).split("|")]
        n = len(names)
        col_parts = {}
        for col in aligned_cols:
            val = row.get(col)
            parts = [] if pd.isna(val) else [p.strip() for p in str(val).split("|")]
            if len(parts) == n:
                col_parts[col] = parts
            elif len(parts) <= 1:
                col_parts[col] = [(parts[0] if parts else "")] * n
            else:
                # 段数既不等于 n 也不是广播（≤1），异常兜底：重复最后一段填满，不报错中断
                col_parts[col] = (parts + [parts[-1]] * n)[:n]
        for i, name in enumerate(names):
            new_row = row.to_dict()
            new_row[anchor_col] = name
            for col in aligned_cols:
                new_row[col] = col_parts[col][i]
            records.append(new_row)
    return pd.DataFrame(records)


def find_prev_project_dir(proj: Path) -> Path | None:
    """从当前项目目录名解析日期区间，往前推7天，在同级目录glob匹配上一期项目文件夹。
    找不到返回None。"""
    m = re.search(r"(\d{8})-(\d{8})", proj.name)
    if not m:
        return None
    start = date.fromisoformat(m.group(1)[:4] + "-" + m.group(1)[4:6] + "-" + m.group(1)[6:])
    end = date.fromisoformat(m.group(2)[:4] + "-" + m.group(2)[4:6] + "-" + m.group(2)[6:])
    prev_start = start - timedelta(days=7)
    prev_end = end - timedelta(days=7)
    prev_range = f"{prev_start:%Y%m%d}-{prev_end:%Y%m%d}"

    candidates = [d for d in proj.parent.glob(f"*{prev_range}*") if d.is_dir()]
    return candidates[0] if candidates else None
