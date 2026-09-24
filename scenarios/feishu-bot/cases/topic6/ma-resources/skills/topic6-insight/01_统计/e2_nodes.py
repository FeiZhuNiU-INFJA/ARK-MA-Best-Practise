#!/usr/bin/env python3
"""
01_统计 · E2 营销节点
统计口径唯一权威。02_洞察/E2_营销节点/v1.md 不重复。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from _common import explode_pipe, fmt_score, top_titles_with_links_str

# marketing-node-tagging 日历路径（与宽表无关，E2 专属输入）。
# MA 部署默认复用 topic6-annotation skill 的日历文件（annotation/insight 共用同一份
# 全年节点日历，避免同一份 marketing_calendar.md 在两个 skill 里各存一份）；
# 需要换其它日历文件时通过环境变量 MARKETING_CALENDAR_PATH 覆盖。
_DEFAULT_CALENDAR = "/mnt/skills/topic6-annotation/references/marketing_calendar/marketing_calendar.md"
_calendar_env = os.environ.get("MARKETING_CALENDAR_PATH", _DEFAULT_CALENDAR)
CALENDAR_PATH = Path(_calendar_env) if _calendar_env else None

# 节点类型 → 是否需要长周期筹备（对应 marketing-node-tagging/SKILL.md 候选节点窗口规则：
# 电商大促14天/大众节日7天 vs 小众节点2天/二十四节气1天，7天为分界）。
# "本周节点预告"密度判断不能只看节点数量——节点数量多但全是短筹备类型时，
# 实际筹备工作量是可控的；只有出现长筹备类型才真正意味着需要提前启动准备。
LONG_PREP_TYPES = {"电商大促", "大众节日"}


def _load_node_calendar_full() -> dict[str, date]:
    """解析节点日历全年数据，返回{节点名称: 节点锚点日期}（不限时间窗口）。

    跟"本周节点预告"部分复用同一份日历文件，但那边只挑发布日~+6天窗口内的行；
    这里要拿到全年所有节点的锚点日期，供"提取节点"命中按锚点日期二次校验用
    （见 prep_e2_nodes() docstring）。文件不存在时返回空字典，调用方按
    "查不到锚点"分支统一兜底，不额外报错。"""
    if not CALENDAR_PATH or not CALENDAR_PATH.exists():
        return {}
    calendar: dict[str, date] = {}
    with open(CALENDAR_PATH, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
            if m:
                try:
                    d = date.fromisoformat(m.group(1))
                except ValueError:
                    continue
                calendar[m.group(2).strip()] = d
    return calendar


def _build_last_week_table(bucket_df: pd.DataFrame) -> tuple[bool, str | None]:
    """按"提取节点"分组产出"上周节点回顾"汇总表markdown。
    返回 (has_data, data_md)；bucket_df 为空时 has_data=False。"""
    if bucket_df.empty:
        return False, None

    rows = []
    for node, grp in bucket_df.groupby("提取节点"):
        热搜条数 = len(grp)
        最高分 = grp["标准化热度分"].max()

        # 涉及品牌IP：C0"商业实体"字段（V2里C3"涉及品牌"+C1"关键实体"两个旧字段
        # 已收敛成这1个实体字段，不再分品牌/人物两列展示）
        entities = explode_pipe(grp["商业实体"])
        entities = sorted(set(e for e in entities if e))
        涉及品牌IP = " / ".join(entities) if entities else "—"

        # 涉及品类（行业归属 explode distinct）
        inds = explode_pipe(grp["行业归属"])
        inds = sorted(set(i for i in inds if i))
        涉及品类 = " / ".join(inds) if inds else "—"

        # 具体热搜（标准化分最高 top-5，带链接+平台）
        n_rep = 5 if 热搜条数 > 5 else 热搜条数
        具体热搜 = top_titles_with_links_str(grp, n=n_rep)

        rows.append({
            "节点": node,
            "热点总计": 热搜条数,
            "热度值": fmt_score(最高分),
            "涉及品牌IP": 涉及品牌IP,
            "涉及品类": 涉及品类,
            "具体热搜": 具体热搜,
        })

    node_df = pd.DataFrame(rows).sort_values("热点总计", ascending=False).reset_index(drop=True)
    col_order = ["节点", "热点总计", "热度值", "涉及品牌IP", "涉及品类", "具体热搜"]

    lines = [
        f"共命中 {len(node_df)} 个节点，{len(bucket_df)} 条节点-热搜对应关系",
        "",
        "| " + " | ".join(col_order) + " |",
        "|" + "---|" * len(col_order),
    ]
    for _, r in node_df.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in col_order) + " |")
    return True, "\n".join(lines)


def prep_e2_nodes(df_y: pd.DataFrame, pub_date: date) -> dict:
    """
    返回结构化结果：

    {
      "last_week": {"has_data": bool, "data_md": str|None, "empty_text": str},
      "next_week": {"has_data": bool, "data_md": str|None, "empty_text": str},
    }

    has_data=False 时 data_md=None，empty_text 是无数据情况下的最终文案——
    这种情况 pipeline_e.py 会直接采用 empty_text 作为最终输出，不再调用 LLM
    （避免"无节点"这种确定性状态被交给 LLM 判断/转写导致输出不稳定）。
    has_data=True 时 data_md 是喂给 LLM 的表格片段（不含各子部分标题，
    标题由 pipeline_e.py 组装 Prompt 输入时统一加）。
    """
    result: dict = {}

    # ===== 提取节点展开 + 按锚点日期二次校验 =====
    exploded = df_y.assign(提取节点=df_y["提取节点"].fillna("").str.split("|")).explode("提取节点")
    exploded["提取节点"] = exploded["提取节点"].str.strip()
    exploded = exploded[exploded["提取节点"] != ""]

    signal_lines: list[str] = []  # dist>=0的节点，折叠进"本周节点预告"的补充信号文字

    if exploded.empty:
        result["last_week"] = {"has_data": False, "data_md": None, "empty_text": "上周无节点。"}
    else:
        calendar = _load_node_calendar_full()
        node_dist: dict[str, int | None] = {}
        for node in exploded["提取节点"].unique():
            node_date = calendar.get(node)
            node_dist[node] = (node_date - pub_date).days if node_date else None

        unknown_nodes = sorted(n for n, d in node_dist.items() if d is None)
        if unknown_nodes:
            print(
                f"[e2_nodes] ⚠️ 日历里查不到以下节点的锚点日期，已归入'上周节点回顾'（可能是"
                f"命名对不上日历或自定义节点，建议核对）：{unknown_nodes}",
                file=sys.stderr,
            )

        def _classify(node: str) -> str | None:
            dist = node_dist[node]
            if dist is None or -7 <= dist <= -1:
                return "last_week"
            if dist >= 0:
                return "upcoming"
            return None

        exploded["_bucket"] = exploded["提取节点"].map(_classify)

        has_lw, md_lw = _build_last_week_table(exploded[exploded["_bucket"] == "last_week"])
        result["last_week"] = {"has_data": has_lw, "data_md": md_lw, "empty_text": "上周无节点。"}

        upcoming_df = exploded[exploded["_bucket"] == "upcoming"]
        if not upcoming_df.empty:
            for node, grp in upcoming_df.groupby("提取节点"):
                dist = node_dist[node]
                热搜条数 = len(grp)
                最高分 = grp["标准化热度分"].max()
                signal_lines.append(
                    f"{node}(还有{dist}天)已有{热搜条数}条提前讨论热搜,热度{fmt_score(最高分)}"
                )

    # ===== 本周节点预告 =====
    window_end = pub_date + timedelta(days=6)

    if not CALENDAR_PATH or not CALENDAR_PATH.exists():
        result["next_week"] = {
            "has_data": False,
            "data_md": None,
            "empty_text": (
                f"节点日历文件未找到（MARKETING_CALENDAR_PATH="
                f"{CALENDAR_PATH or '未设置'}），发布窗口：{pub_date} ~ {window_end}"
            ),
        }
    else:
        with open(CALENDAR_PATH, encoding="utf-8") as f:
            raw = f.readlines()

        next_week = []
        for line in raw:
            m = re.match(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
            if m:
                try:
                    d = date.fromisoformat(m.group(1))
                except ValueError:
                    continue
                if pub_date <= d <= window_end:
                    next_week.append({
                        "节点日期": m.group(1),
                        "节点名称": m.group(2).strip(),
                        "节点类型": m.group(3).strip(),
                    })

        if not next_week:
            result["next_week"] = {
                "has_data": False,
                "data_md": None,
                "empty_text": "本周暂无节点。",
            }
        else:
            覆盖天数 = len({n["节点日期"] for n in next_week})
            窗口天数 = (window_end - pub_date).days + 1
            长筹备 = [n for n in next_week if n["节点类型"] in LONG_PREP_TYPES]
            短筹备 = [n for n in next_week if n["节点类型"] not in LONG_PREP_TYPES]
            lines = [
                f"发布窗口：{pub_date} ~ {window_end}（共{窗口天数}天），"
                f"共 {len(next_week)} 个节点，覆盖 {覆盖天数}/{窗口天数} 天；"
                f"长筹备节点（电商大促/大众节日，通常需提前7-14天启动）{len(长筹备)} 个，"
                f"短筹备节点（小众节点/二十四节气，1-2天即可）{len(短筹备)} 个",
                "",
                "| 节点日期 | 节点名称 | 节点类型 |",
                "|---|---|---|",
            ]
            for n in next_week:
                lines.append(f"| {n['节点日期']} | {n['节点名称']} | {n['节点类型']} |")

            if signal_lines:
                lines.append("")
                lines.append("补充信号（已有真实讨论数据支撑，供预告段落酌情提及，不需要单独出表格）：")
                for s in signal_lines:
                    lines.append(f"- {s}")

            result["next_week"] = {
                "has_data": True,
                "data_md": "\n".join(lines),
                "empty_text": "本周暂无节点。",
            }

    return result
