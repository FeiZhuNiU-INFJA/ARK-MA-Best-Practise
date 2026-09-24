#!/usr/bin/env python3
"""
01_统计 · E3 平台新鲜事
统计口径唯一权威。02_洞察/E3_平台新鲜事/v1.md 不重复。
综合热度指数公式来源：references/综合热度指数算法说明.md（用户提供的原始算法说明副本）。
"""

from __future__ import annotations

import math
import sys

import pandas as pd

from _common import fmt_score, top_titles_with_links_str

EXPECTED_PLATFORMS = ["微博", "抖音", "B站", "知乎"]


def _check_platform_coverage(df_y: pd.DataFrame) -> None:
    """防御性检查（2026-09-15新增）：宽表"平台"字段的实际取值跟EXPECTED_PLATFORMS
    不完全对应时打印warning。风险场景：宽表平台字段拼写漂移（如"哔哩哔哩"vs"B站"），
    会导致该平台被 df_y[df_y["平台"]==pname] 过滤成空，静默走"上周XX无营销可用热点"
    分支，看起来像"这平台真的没热点"而不是"平台名没匹配上"——只打印，不中断执行。"""
    actual = set(df_y["平台"].dropna().unique())
    expected = set(EXPECTED_PLATFORMS)
    unexpected = actual - expected
    missing = expected - actual
    if unexpected:
        print(f"[e3_platforms] ⚠️ 宽表出现预期外的平台名：{unexpected}"
              f"（这些数据不会被任何版块统计，检查是否是平台字段拼写漂移）", file=sys.stderr)
    if missing:
        print(f"[e3_platforms] ⚠️ 预期平台缺失：{missing}"
              f"（本轮宽表里完全没有这个平台的数据，若非真实情况请检查平台字段拼写）", file=sys.stderr)


def _check_driving_words_coverage(p: pd.DataFrame, pname: str) -> None:
    """防御性检查（2026-09-15新增）："热点驱动词"字段大面积为空时打印warning。
    风险场景：这种情况下"平台热点词TOP3"会静默判定成"候选<2个不输出"，但真实原因
    可能是C1标注管道异常（那一轮忘了产出驱动词字段），不是真的没有驱动词候选——
    两种情况从最终报告上看不出区别，需要靠这条warning在执行日志里区分。"""
    if "热点驱动词" not in p.columns or p.empty:
        return
    non_empty_ratio = p["热点驱动词"].fillna("").str.strip().ne("").mean()
    if non_empty_ratio < 0.05:
        print(f"[e3_platforms] ⚠️ {pname}的热点驱动词字段非空率仅{non_empty_ratio:.1%}，"
              f"「平台热点词TOP3」可能因此空缺——请确认C1标注是否正确产出该字段"
              f"（而不是真的没有驱动词候选）", file=sys.stderr)


def _score_driving_words(p: pd.DataFrame) -> pd.DataFrame:
    """对单个平台的数据做"热点驱动词"拆分+综合热度指数打分，按综合热度指数降序返回。
    列：热点驱动词 / 词频 / 均分 / 综合热度指数 / 具体热搜。
    prep_e3_platforms()的"平台热点词TOP3"和prep_e3_word_freq_audit()的审计表共用本函数，
    避免两处各写一份打分逻辑、后续改动漏改一处（2026-09-15抽取，原逻辑内嵌在prep_e3_platforms里）。

    口径（对应 references/综合热度指数算法说明.md Step1-5）：
      - 统计单位：热点驱动词（"热点驱动词"字段 | 分隔多值，explode拆分）
      - 词频=该驱动词在本平台的出现条目数，词频=1直接剔除（样本太少无统计意义）
      - 均分=该驱动词下所有条目标准化热度分的算术平均值
      - freq_norm = log2(词频) / log2(平台内最高词频+1)
      - raw = 均分 × (0.7 + 0.3 × freq_norm)（均分占70%基础权重，词频最多贡献30%加成）
      - 综合热度指数 = 40 + 60 × (raw-raw_min)/(raw_max-raw_min)，min/max只在平台内部算
        （raw_max==raw_min退化情况：全部记为100.0，避免除零）
      - 具体热搜：该驱动词下标准化分最高的title，固定最多3条

    无词频>1候选时返回空DataFrame（同样带这几列，调用方直接判断.empty/len）。
    """
    cols = ["热点驱动词", "词频", "均分", "综合热度指数", "具体热搜"]

    words = p.copy()
    words["_驱动词"] = words["热点驱动词"].fillna("").str.split("|")
    words = words.explode("_驱动词")
    words["_驱动词"] = words["_驱动词"].str.strip()
    words = words[words["_驱动词"] != ""]

    word_rows = []
    for word, wgrp in words.groupby("_驱动词"):
        词频 = len(wgrp)
        if 词频 <= 1:
            continue  # 单条样本噪音风险高，剔除（综合热度指数算法说明.md Step1）
        word_rows.append({
            "热点驱动词": word,
            "词频": 词频,
            "均分": wgrp["标准化热度分"].mean(),
            "具体热搜": top_titles_with_links_str(wgrp, n=3, show_platform=False),
        })

    if not word_rows:
        return pd.DataFrame(columns=cols)

    wdf = pd.DataFrame(word_rows)
    max_freq = wdf["词频"].max()
    wdf["freq_norm"] = wdf["词频"].apply(lambda f: math.log2(f) / math.log2(max_freq + 1))
    wdf["raw"] = wdf["均分"] * (0.7 + 0.3 * wdf["freq_norm"])
    raw_min, raw_max = wdf["raw"].min(), wdf["raw"].max()
    if raw_max > raw_min:
        wdf["综合热度指数"] = 40 + 60 * (wdf["raw"] - raw_min) / (raw_max - raw_min)
    else:
        wdf["综合热度指数"] = 100.0
    return wdf.sort_values("综合热度指数", ascending=False).reset_index(drop=True)[cols]


def prep_e3_platforms(df_y: pd.DataFrame) -> str:
    """
    生成4个平台（微博/抖音/B站/知乎）各自的热门事件TOPN + 平台热点词TOP3（Markdown）
    df_y 入参已是全局"是否营销可用=是"过滤后的数据（见 run_stats.py::filter_usable()），
    本函数不重复做这层过滤，只按 平台 二次筛选。E3的"热度值"列不带"分"字后缀
    （fmt_score(v, suffix="")），跟E2/E4带"分"字的格式不同，对齐参考示例。

    1. 热门事件TOPN（按事件簇名分组，2026-09-15更新）
       列：排名 | 事件名称 | 行业 | 热点总计 | 热度值 | 具体热搜
       口径：
         - 先过滤：热点总计（事件簇内热搜条数）≥2 的事件簇才纳入候选（单条独立话题不展示）
         - 排序：热点总计降序，相同再按最高标准化分降序
         - 取TOP5，候选不足5个则标题按实际数量写"TOP N"
         - 具体热搜：事件簇内标准化分最高的title，固定最多3条，
           格式"- [标题](链接)"（不带平台标签，本表已按平台分组），<br>换行
         - 候选全部被过滤（无热点总计≥2的事件簇）：不出表格，给出说明文案

    2. 平台热点词TOP3（2026-09-15重构，原名"借势玩法TOP3"）
       列：平台热点词 | 热度值 | 具体热搜（"适配领域""借势方向"两列由LLM在Prompt层判断产出，
       本函数不生成）
       打分口径见 _score_driving_words()；不再限定"营销关注点=平台借势"——过滤范围跟
       热门事件TOPN一致，只按平台筛（2026-09-15起变更）
       排序：综合热度指数降序，取TOP3；候选（词频>1的驱动词）<2个则不输出此子版块

    完整候选明细（含TOP10全量打分，供人工/自动化流程核对）见 prep_e3_word_freq_audit()，
    该函数产出不进LLM Prompt，是独立的审计向导出。
    """
    platforms = EXPECTED_PLATFORMS
    sections = []

    _check_platform_coverage(df_y)

    for pname in platforms:
        p = df_y[df_y["平台"] == pname].copy()

        if p.empty:
            sections.append(f"## {pname}\n\n上周 {pname} 无营销可用热点。")
            continue

        _check_driving_words_coverage(p, pname)

        plines = [f"## {pname}", ""]

        # ---- 热门事件 TOPN ----
        rows_agg = []
        for event_name, grp in p.groupby("事件簇名"):
            热点总计 = len(grp)
            最高分 = grp["标准化热度分"].max()
            具体热搜 = top_titles_with_links_str(grp, n=3, show_platform=False)
            行业 = grp["行业归属"].dropna().iloc[0] if grp["行业归属"].notna().any() else "—"
            rows_agg.append({
                "事件簇名": event_name,
                "热点总计": 热点总计,
                "最高标准化分": 最高分,
                "具体热搜": 具体热搜,
                "行业归属": 行业,
            })
        agg = pd.DataFrame(rows_agg) if rows_agg else pd.DataFrame(
            columns=["事件簇名", "热点总计", "最高标准化分", "具体热搜", "行业归属"]
        )

        # 过滤单条独立话题（热点总计<2），再按"热点总计降序，同数按最高标准化分降序"排序取TOP5
        agg = agg[agg["热点总计"] >= 2]
        agg = agg.sort_values(
            ["热点总计", "最高标准化分"], ascending=[False, False]
        ).head(5).reset_index(drop=True)

        plines.append(f"本平台营销可用热搜：{len(p)} 条 / 事件簇：{p['事件簇名'].nunique()} 个\n")
        if agg.empty:
            plines.append(f"{pname} 上周热门事件均为单条独立话题（热点总计<2），不展示热门事件榜单。")
        else:
            plines.append(f"### {pname} · 热门事件 TOP {len(agg)}\n")
            plines.append("| 排名 | 事件名称 | 行业 | 热点总计 | 热度值 | 具体热搜 |")
            plines.append("|---|---|---|---|---|---|")
            for i, r in agg.iterrows():
                plines.append(
                    f"| {i+1} | {r['事件簇名']} | {r['行业归属']} | {int(r['热点总计'])} "
                    f"| {fmt_score(r['最高标准化分'], suffix='')} | {r['具体热搜']} |"
                )

        # ---- 平台热点词 TOP3（打分逻辑见 _score_driving_words()）----
        wdf = _score_driving_words(p)
        plines.append("")
        if len(wdf) < 2:
            plines.append(f"> {pname} 上周平台热点词候选（词频>1）<2 个，不输出此子版块。")
        else:
            top3 = wdf.head(3)
            plines.append(f"### {pname} · 平台热点词 TOP {len(top3)}\n")
            plines.append("| 平台热点词 | 热度值 | 具体热搜 |")
            plines.append("|---|---|---|")
            for _, r in top3.iterrows():
                plines.append(
                    f"| {r['热点驱动词']} | {fmt_score(r['综合热度指数'], suffix='')} | {r['具体热搜']} |"
                )

        sections.append("\n".join(plines))

    return "\n\n---\n\n".join(sections)


def prep_e3_word_freq_audit(df_y: pd.DataFrame) -> str:
    """
    审计向导出，不进LLM Prompt——纯供人工/自动化流程核对"平台热点词TOP3"的打分过程。
    每个平台输出TOP10候选驱动词的完整打分明细：排名/热点驱动词/词频/均分/综合热度指数/具体热搜，
    统计口径与 prep_e3_platforms() 的"平台热点词TOP3"完全一致（共用 _score_driving_words()），
    这里只是不截断到TOP3、多展示到TOP10，方便回答"为什么是这三个词入选/为什么某词没入选"。
    """
    platforms = EXPECTED_PLATFORMS
    sections = []
    for pname in platforms:
        p = df_y[df_y["平台"] == pname].copy()
        if p.empty:
            sections.append(f"## {pname}\n\n上周 {pname} 无营销可用热点。")
            continue

        wdf = _score_driving_words(p)
        if wdf.empty:
            sections.append(f"## {pname}\n\n无词频>1的驱动词候选。")
            continue

        top10 = wdf.head(10)
        lines = [
            f"## {pname}",
            "",
            "| 排名 | 热点驱动词 | 词频 | 均分 | 综合热度指数 | 具体热搜 |",
            "|---|---|---|---|---|---|",
        ]
        for i, r in top10.iterrows():
            lines.append(
                f"| {i+1} | {r['热点驱动词']} | {int(r['词频'])} "
                f"| {fmt_score(r['均分'], suffix='')} | {fmt_score(r['综合热度指数'], suffix='')} "
                f"| {r['具体热搜']} |"
            )
        sections.append("\n".join(lines))

    return "\n\n---\n\n".join(sections)
