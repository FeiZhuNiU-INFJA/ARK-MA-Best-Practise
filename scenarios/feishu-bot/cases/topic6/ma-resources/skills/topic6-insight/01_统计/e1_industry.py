#!/usr/bin/env python3
"""
01_统计 · E1 行业及热门话题
统计口径唯一权威。02_洞察/E1_行业话题/v1.md 不重复。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from _common import find_prev_project_dir


def find_latest_e1_data(prev_proj: Path) -> Path | None:
    """在上一期项目的 06_洞察/ 下找版本号最大的 e1_data.md（找不到返回 None）。"""
    insight_dir = prev_proj / "06_洞察"
    if not insight_dir.exists():
        return None
    candidates = []
    for d in insight_dir.glob("v*"):
        if d.is_dir():
            m = re.match(r"v(\d+)$", d.name)
            if m and (d / "e1_data.md").exists():
                candidates.append((int(m.group(1)), d / "e1_data.md"))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def parse_top10_from_e1_data(text: str) -> list[str]:
    """从 e1_data.md 的 Markdown 表格里按行顺序解析出 TOP10行业 列（第一列）的行业名。"""
    industries = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0] in ("TOP10行业", "") or set(cells[0]) <= set("-: "):
            continue
        industries.append(cells[0])
    return industries


def build_wow_comparison(proj: Path, current_top10: list[str]) -> str:
    """生成「环比数据」文本块：只比较两期 TOP10 行业的排序变化（新进/跌出/排名升降），
    不比具体数值。找不到上一期数据时，明确告知 LLM 如实说明，不允许编造。"""
    prev_proj = find_prev_project_dir(proj)
    if prev_proj is None:
        return (
            "**环比数据**：Projects/ 下未找到对应上一自然周日期区间的项目目录，"
            "本期是该系列的首次归档（或上一期尚未跑到 E 阶段）。"
            "第三句必须如实写「本期为该系列首次归档，暂无上期数据可比」，不允许编造任何变化。"
        )

    prev_e1_data = find_latest_e1_data(prev_proj)
    if prev_e1_data is None:
        return (
            f"**环比数据**：找到了上一期项目目录（{prev_proj.name}），"
            "但其 06_洞察/ 下没有可用的 e1_data.md 快照（可能上一期还没跑到 E 阶段）。"
            "第三句必须如实写「上一期暂无洞察数据可比」，不允许编造任何变化。"
        )

    prev_top10 = parse_top10_from_e1_data(prev_e1_data.read_text(encoding="utf-8"))
    if not prev_top10:
        return (
            f"**环比数据**：上一期 e1_data.md（{prev_e1_data}）解析不到 TOP10 行业列表。"
            "第三句必须如实写「上一期数据解析失败，暂无法环比」，不允许编造任何变化。"
        )

    cur_rank = {ind: i + 1 for i, ind in enumerate(current_top10)}
    prev_rank = {ind: i + 1 for i, ind in enumerate(prev_top10)}

    new_in = [ind for ind in current_top10 if ind not in prev_rank]
    dropped = [ind for ind in prev_top10 if ind not in cur_rank]
    moved = [
        (ind, prev_rank[ind], cur_rank[ind])
        for ind in current_top10
        if ind in prev_rank and prev_rank[ind] != cur_rank[ind]
    ]
    unchanged_top1 = current_top10 and prev_top10 and current_top10[0] == prev_top10[0]

    lines = [f"**环比数据**（对比上一期 {prev_proj.name} 的 TOP10 行业排序，脚本已算好，直接引用，不需要重新比较）："]
    if current_top10 and prev_top10:
        if unchanged_top1:
            lines.append(f"- TOP1 行业：上期与本期均为「{current_top10[0]}」，未变化")
        else:
            prev_top1 = prev_top10[0] if prev_top10 else "—"
            lines.append(f"- TOP1 行业：上期「{prev_top1}」→ 本期「{current_top10[0]}」，发生变化")
    if new_in:
        lines.append(f"- 新进入 TOP10：{'、'.join(f'「{i}」' for i in new_in)}（上期未进 TOP10）")
    if dropped:
        lines.append(f"- 跌出 TOP10：{'、'.join(f'「{i}」' for i in dropped)}（上期在 TOP10，本期未进）")
    if moved:
        for ind, pr, cr in moved:
            direction = "上升" if cr < pr else "下降"
            lines.append(f"- 「{ind}」排名由第{pr}名→第{cr}名（{direction}{abs(pr - cr)}名）")
    if not new_in and not dropped and not moved:
        lines.append("- 整体排序与上期完全一致，无行业新进/跌出/升降")

    return "\n".join(lines)


def prep_e1_industry(df_y: pd.DataFrame, proj: Path) -> str:
    """
    生成 TOP10 行业统计表（Markdown）。列：TOP10行业 | 热点总计 | 热度值 | 主要驱动话题

    口径：
      - 热点总计：行业归属 explode 后逐条计数，多值行业各记1，不去重
      - 热度值：该行业所有热搜 标准化热度分 的中位数，裸数字不带"分"单位
      - 主要驱动话题：该行业下 事件簇名 计数 TOP3；本函数输出带"(N次)"，
        Prompt 层去掉次数、转成 bullet + <br> 格式
      - 行业总数 + 辅助统计（TOP1/TOP3/TOP10占比、TOP1对TOP10倍数）：预计算供 LLM 直接引用
      - 声量-热度错位候选：热度值中位数最高的行业及其声量排名
      - 环比数据：见 build_wow_comparison()，无上一期数据时输出兜底措辞
    """
    df = df_y.copy()

    # explode 行业归属
    df_exp = df.assign(行业=df["行业归属"].fillna("").str.split("|")).explode("行业")
    df_exp["行业"] = df_exp["行业"].str.strip()
    df_exp = df_exp[df_exp["行业"] != ""]

    rows = []
    for ind, grp in df_exp.groupby("行业"):
        热点总计 = len(grp)
        热度值 = grp["标准化热度分"].median()

        # 主要驱动话题：该行业下 事件簇名 计数 TOP3（附出现次数，供 LLM 判断信号强弱）
        events = grp["事件簇名"].fillna("").str.strip()
        events = events[events != ""]
        top3_vc = events.value_counts().head(3)
        驱动话题 = " | ".join(f"{e}({c}次)" for e, c in top3_vc.items()) if not top3_vc.empty else "—"

        rows.append({
            "TOP10行业": ind,
            "热点总计": 热点总计,
            "热度值": f"{热度值:.1f}",
            "主要驱动话题": 驱动话题,
        })

    result = (
        pd.DataFrame(rows)
        .sort_values("热点总计", ascending=False)
        .head(10)
        .reset_index(drop=True)
    )

    行业总数 = df_exp["行业"].nunique()
    总条次 = len(df_exp)

    lines = [
        f"**数据基础**：营销可用热搜 {len(df_y)} 条，行业归属 explode 后共 {总条次} 条次，"
        f"覆盖 {行业总数} 个行业（下表仅展示热点总计 TOP10，不代表上周热点只涉及 10 个行业）",
        "",
    ]

    if not result.empty:
        top1 = result.iloc[0]
        top3_sum = int(result.head(min(3, len(result)))["热点总计"].sum())
        top10_sum = int(result["热点总计"].sum())
        last = result.iloc[-1]
        lines.append("**辅助统计**（已算好，正文直接引用，不需要重新计算或推断行业总数）：")
        lines.append(
            f"- TOP1 行业「{top1['TOP10行业']}」：{int(top1['热点总计'])} 条次，"
            f"占全部 {总条次} 条次的 {int(top1['热点总计']) / 总条次 * 100:.1f}%"
        )
        if len(result) >= 3:
            top3_names = "+".join(result.head(3)["TOP10行业"].tolist())
            lines.append(
                f"- TOP3 行业（{top3_names}）合计：{top3_sum} 条次，"
                f"占全部 {总条次} 条次的 {top3_sum / 总条次 * 100:.1f}%"
            )
        lines.append(
            f"- TOP10 行业合计：{top10_sum} 条次，占全部 {总条次} 条次的 {top10_sum / 总条次 * 100:.1f}%"
            f"（另有 {总条次 - top10_sum} 条次分布在 TOP10 以外的 {行业总数 - len(result)} 个行业，未在表格展示）"
        )
        if int(last["热点总计"]) > 0:
            lines.append(
                f"- TOP1/TOP10 倍数：「{top1['TOP10行业']}」（{int(top1['热点总计'])}）"
                f"是第十名「{last['TOP10行业']}」（{int(last['热点总计'])}）的 "
                f"{int(top1['热点总计']) / int(last['热点总计']):.1f} 倍"
            )

        # 声量-热度错位候选：热度值（中位数）最高的行业，及其声量排名（脚本挑对象，
        # LLM 只需从「主要驱动话题」归纳主题原因，不用自己判断该点名谁）
        result["_热度值_float"] = result["热度值"].astype(float)
        max_heat_row = result.loc[result["_热度值_float"].idxmax()]
        max_heat_rank = int(result.index[result["TOP10行业"] == max_heat_row["TOP10行业"]][0]) + 1
        lines.append(
            f"- 热度值 TOP1 行业：「{max_heat_row['TOP10行业']}」（{max_heat_row['热度值']} 分），"
            f"声量排名第 {max_heat_rank} 名"
            + ("（同时也是声量头部行业）" if max_heat_rank <= 3 else "（声量排名靠后，说明单条穿透力强，存在声量与热度错位）")
        )
        result = result.drop(columns=["_热度值_float"])
        lines.append("")

    lines.append("| TOP10行业 | 热点总计 | 热度值 | 主要驱动话题 |")
    lines.append("|---|---|---|---|")
    for _, r in result.iterrows():
        lines.append(
            f"| {r['TOP10行业']} | {r['热点总计']} | {r['热度值']} | {r['主要驱动话题']} |"
        )

    lines.append("")
    lines.append(build_wow_comparison(proj, result["TOP10行业"].tolist()))

    return "\n".join(lines)
