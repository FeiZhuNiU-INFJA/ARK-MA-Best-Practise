#!/usr/bin/env python3
"""
01_统计 · E4 营销发现
统计口径唯一权威。02_洞察/E4_营销发现/v1.md 不重复。
"""

from __future__ import annotations

import pandas as pd

from _common import fmt_score, safe_field


def _title_with_link(r) -> str:
    """单行"代表热搜"格式化为"[标题](链接)"，链接缺失时退化为纯标题——不留空的markdown
    链接语法。合作动态/营销观察/消费洞察专用（这三部分数据已按事件簇去重成1行代表，不需要
    像舆情风险那样group-level取TOP N条，直接用这1行自带的热点标题+链接即可，2026-09-18新增，
    修复"代表热搜"此前一直是纯文本、案例卡"链接"字段无数据来源被LLM编造平台首页链接的问题）。"""
    title = safe_field(r, "热点标题", "").replace("|", "\\|")
    link = safe_field(r, "链接", "")
    return f"[{title}]({link})" if (link and link != "—") else title


def _titles_platform_score(grp: pd.DataFrame, n: int = 3) -> str:
    """取 grp 中标准化分最高的 top-n 条，格式化为 "- [标题](链接) · 平台 · 热度值"，
    <br>分隔——舆情风险子版块专用（参考真实生产报告"3.2品牌舆情风险"的
    "具体热搜·平台·热度值"列格式，比 _common.py::top_titles_with_links_str()
    多带一个热度值字段，不复用那个函数，避免改动其签名影响 E2/E3 已有调用）。
    链接缺失时退化为纯标题，不留空的 markdown 链接语法。"""
    top = grp.nlargest(min(n, len(grp)), "标准化热度分")
    lines = []
    for _, r in top.iterrows():
        title = safe_field(r, "热点标题", "").replace("|", "\\|")
        link = safe_field(r, "链接", "")
        platform = safe_field(r, "平台", "")
        score = fmt_score(r["标准化热度分"])
        text = f"[{title}]({link})" if (link and link != "—") else title
        lines.append(f"- {text} · {platform} · {score}")
    return "<br>".join(lines)


def _select_cooperation_events(df_y: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """合作动态候选池：是否商业合作=是，事件簇去重（保留标准化热度分最高的1行代表），
    按分降序取TOP N。抽成独立函数（2026-09-19），供 prep_e4_marketing() 的markdown渲染
    和 prep_e4_candidates() 的JSON导出共用候选口径——避免同一套筛选/排序/截断逻辑
    在两处重复实现，后续改一处漏改一处。"""
    co = df_y[df_y["是否商业合作"] == "是"].copy()
    co = co[co["事件簇名"].notna() & (co["事件簇名"].str.strip() != "")]
    return co.sort_values("标准化热度分", ascending=False).drop_duplicates("事件簇名", keep="first").head(n)


def _select_risk_events(df_y: pd.DataFrame, n: int = 20) -> list[tuple]:
    """舆情风险候选池：是否风险预警=是，按事件簇聚合，取每簇最高分代表 + 该簇完整明细，
    按代表分降序取TOP N。返回 [(event_name, top_row, grp_sorted), ...]，grp_sorted是
    该事件簇全部原始行（按分降序），供需要"簇内多条热搜"的调用方（如
    _titles_platform_score()/prep_e4_candidates()）按需取用，不重复groupby。"""
    risk = df_y[df_y["是否风险预警"] == "是"].copy()
    risk = risk[risk["事件簇名"].notna() & (risk["事件簇名"].str.strip() != "")]
    if risk.empty:
        return []
    reps = []
    for event, grp in risk.groupby("事件簇名"):
        grp_sorted = grp.sort_values("标准化热度分", ascending=False)
        reps.append((event, grp_sorted.iloc[0], grp_sorted))
    reps.sort(key=lambda x: x[1]["标准化热度分"], reverse=True)
    return reps[:n]


def prep_e4_marketing(df_y: pd.DataFrame) -> str:
    """
    生成4个子版块数据（Markdown）：合作动态 / 舆情风险 / 营销观察 / 消费洞察

    四个子版块只做"筛选+聚合+排序+取数"，不做语义分类（案例适配度判断等留给LLM在
    撰写时判断，本函数不实现，Prompt层给判断依据）。

    2026-09-18更新，对照真实生产报告核对后修正案例遴选与格式：
    - 舆情风险：不再按"商业实体/营销维度"出单独列，改成参考报告的精简格式——事件簇按
      标准化热度分排序，每个事件簇附最多3条"具体热搜·平台·热度值"（`_titles_platform_score()`）
    - 营销观察：候选池从TOP5放宽到TOP15，不在数据层做"三选五"的语义判断——把决策权
      交给LLM（Prompt层要求"先遍历全部候选，按适配度优先，热度分仅做打分犹豫时的
      辅助判断"，允许3-5个之外酌情多写，最终数量在合并报告阶段收口）
    - 消费洞察见下方 4. 单独说明（跨热点归纳，改动更大）

    2026-09-18第二轮更新，基于真实v3产出复盘用户反馈修正：
    - 合作动态/舆情风险此前"无上限（宁多勿漏）"——实测合作动态82条、舆情风险108个事件簇
      全部进表格，跟"洞察筛选过的高价值数据"这个定位不符。改法：两者都加`.head(20)`
      （按标准化热度分降序取TOP20），跟消费洞察候选池同量级
    - 合作动态/营销观察"代表热搜"列此前是`r['热点标题']`纯文本，数据层根本没提供链接，
      改法：换成`_title_with_link()`格式化成markdown链接
    - 消费洞察新增"代表热搜"列（同样用`_title_with_link()`）

    2026-09-19第三轮更新，落地"乙方视角先打标筛选再写洞察"方案（详见
    `E4_营销发现/_tagging/v1.md`docstring）：
    - 舆情风险/合作动态的"风险类型该分到哪桶""这个候选乙方能不能接/对营销人有没有
      参考价值"，此前是让撰写阶段的LLM在写长文的同一次调用里顺带判断，语义负荷太重，
      实测出现分类错误（星宇股份劝退应届生被分到"高管言论"、希尔顿贴牌酒店被分到
      "文娱作品口碑"）——改法：这两项判断挪到 pipeline_e.py::build_e4_task() 新增的
      "打标"步骤（单独一次轻量LLM调用，只判断不写长文），过滤"不接"/"无参考价值"的
      候选后，才把数据交给本函数产出的这份markdown对应的撰写Prompt(v5.md起)
    - 舆情风险/合作动态两部分的候选口径（筛选/排序/TOP20）本身不变，只是"选出哪些
      candidate"这段逻辑抽成`_select_cooperation_events()`/`_select_risk_events()`，
      跟`prep_e4_candidates()`（打标步骤用的JSON版本）共用，不重复实现

    1. 合作动态：是否商业合作=是（R2），候选池TOP20（按标准化热度分降序，不再无上限）
       列：事件簇名 | 商业实体 | 行业 | 热度分 | 代表热搜（带链接） | 合作动态说明
    2. 舆情风险：是否风险预警=是（R3），按事件簇聚合后取TOP20（不再无上限）
       列：事件簇名 | 行业 | 热度分 | 具体热搜（最多3条，带平台+热度值+链接） | 风险判断说明
    3. 营销观察：是否营销发现=是（R4），候选池TOP15（不是最终案例数，交给LLM筛选）
       列：事件簇名 | 平台 | 行业 | 热度分 | 代表热搜（带链接） | 营销发现说明
    4. 消费洞察：是否消费者行为=是（R5），见下方口径

    以上标签均为R1~R5路由标注阶段判定，本函数直接引用，不做二次判断。
    """
    parts = []

    # ===== 合作动态（是否商业合作=是）=====
    co = _select_cooperation_events(df_y, n=20)

    if co.empty:
        parts.append("## 合作动态数据\n\n上周无商业合作热点（是否商业合作=是）。")
    else:
        lines = [
            f"## 合作动态数据\n\n共 {len(co)} 条（TOP20，事件簇去重后按标准化热度分降序，是否商业合作=是；"
            "打标步骤会再按对营销人参考价值二次过滤，最终展示条数可能更少）\n",
            "| 事件簇名 | 商业实体 | 行业 | 热度分 | 代表热搜 | 合作动态说明 |",
            "|---|---|---|---|---|---|",
        ]
        for _, r in co.iterrows():
            desc = str(safe_field(r, "合作动态说明", "")).replace("\n", " ")[:60]
            lines.append(
                f"| {r['事件簇名']} | {safe_field(r, '商业实体')} | {safe_field(r, '行业归属')} "
                f"| {fmt_score(r['标准化热度分'])} | {_title_with_link(r)} | {desc} |"
            )
        parts.append("\n".join(lines))

    # ===== 舆情风险（是否风险预警=是）=====
    risk_events = _select_risk_events(df_y, n=20)

    if not risk_events:
        parts.append("## 舆情风险数据\n\n上周无风险预警热点。")
    else:
        lines = [
            f"## 舆情风险数据\n\n共 {len(risk_events)} 个事件簇（TOP20，按标准化热度分降序，是否风险预警=是；"
            "打标步骤会再按乙方公关可接性二次过滤，最终展示条数可能更少）\n",
            "| 事件簇名 | 行业 | 热度分 | 具体热搜 | 风险判断说明 |",
            "|---|---|---|---|---|",
        ]
        for event, top, grp_sorted in risk_events:
            desc = str(safe_field(top, "风险判断说明", "")).replace("\n", " ")[:80]
            lines.append(
                f"| {event} | {safe_field(top, '行业归属')} "
                f"| {fmt_score(top['标准化热度分'])} | {_titles_platform_score(grp_sorted, n=3)} | {desc} |"
            )
        parts.append("\n".join(lines))

    # ===== 营销观察（是否营销发现=是）=====
    # 2026-09-18改法：候选池TOP5放宽到TOP15，不在数据层做"三选五"的语义判断——
    # 案例适配度筛选交给LLM在Prompt指导下判断（"遍历全部候选，适配度优先于热度分"）
    creative = df_y[df_y["是否营销发现"] == "是"].copy()
    creative = creative[creative["事件簇名"].notna() & (creative["事件簇名"].str.strip() != "")]
    creative = creative.sort_values("标准化热度分", ascending=False).drop_duplicates("事件簇名", keep="first").head(15)

    if creative.empty:
        parts.append("## 营销观察数据\n\n上周无营销发现热点（是否营销发现=是）。")
    else:
        lines = [
            f"## 营销观察数据\n\n共 {len(creative)} 条候选（TOP15，是否营销发现=是；最终成文3-5个，量不足时按实际数量，不强凑）\n",
            "| 事件簇名 | 平台 | 行业 | 热度分 | 代表热搜 | 营销发现说明 |",
            "|---|---|---|---|---|---|",
        ]
        for _, r in creative.iterrows():
            desc = str(safe_field(r, "营销发现说明", "")).replace("\n", " ")[:80]
            lines.append(
                f"| {r['事件簇名']} | {safe_field(r, '平台')} | {safe_field(r, '行业归属')} "
                f"| {fmt_score(r['标准化热度分'])} | {_title_with_link(r)} | {desc} |"
            )
        parts.append("\n".join(lines))

    # ===== 消费洞察（是否消费者行为=是）=====
    # 2026-09-18改法：候选池TOP5放宽到TOP20——参考报告的洞察常引用2+个不同事件簇作为
    # 佐证（跨热点归纳，比单一事件驱动的孤立现象更有说服力），数据层不能再把候选去重
    # 挤压到5个代表事件簇，得给LLM更大候选范围自己识别哪些候选属于同一类消费心理/
    # 行为现象。排序仍按热度分降序只是候选呈现顺序，不代表最终成文顺序（Prompt层
    # 要求按"覆盖人群范围"重新排序，那是语义判断，数据层不做）
    cons = df_y[df_y["是否消费者行为"] == "是"].copy()
    cons = cons[cons["事件簇名"].notna() & (cons["事件簇名"].str.strip() != "")]
    cons = cons.sort_values("标准化热度分", ascending=False).drop_duplicates("事件簇名", keep="first").head(20)

    if cons.empty:
        parts.append("## 消费洞察数据\n\n上周无消费者行为热点（是否消费者行为=是）。")
    else:
        lines = [
            f"## 消费洞察数据\n\n共 {len(cons)} 条候选（TOP20，是否消费者行为=是；最终成文3-5条，可合并多个候选为1条跨热点洞察）\n",
            "| 事件簇名 | 平台 | 行业 | 热度分 | 代表热搜 | 商业实体 | 消费者行为说明 |",
            "|---|---|---|---|---|---|---|",
        ]
        for _, r in cons.iterrows():
            desc = str(safe_field(r, "消费者行为说明", "")).replace("\n", " ")[:80]
            lines.append(
                f"| {r['事件簇名']} | {safe_field(r, '平台')} | {safe_field(r, '行业归属')} "
                f"| {fmt_score(r['标准化热度分'])} | {_title_with_link(r)} | {safe_field(r, '商业实体')} | {desc} |"
            )
        parts.append("\n".join(lines))

    return "\n\n---\n\n".join(parts)


def prep_e4_candidates(df_y: pd.DataFrame) -> dict:
    """舆情风险/合作动态候选的结构化（JSON友好）版本，供 pipeline_e.py 的"打标"步骤
    使用（2026-09-19新增）。候选口径跟 prep_e4_marketing() 完全一致（复用同一对
    `_select_*_events()`辅助函数），只是这里返回python原生dict/list，不渲染成
    markdown文本。

    为什么不直接解析 prep_e4_marketing() 产出的markdown文本，而要另写一份结构化版本：
    "行业归属"/"商业实体"字段本身用"|"做多值分隔符（如"科技/AI产业|汽车出行"），
    这个"|"跟markdown表格自身的列分隔符是同一个字符且未转义——在
    e4_marketing.py -> pipeline_e.py 之间用markdown文本做round-trip解析不安全，
    会把多值字段错误拆成多一列，导致后续按列取值全部错位。结构化数据在python
    对象层面直接传递没有这个歧义。

    营销观察/消费洞察两部分不涉及"乙方可接性/参考价值"打标（候选适配度筛选交给
    撰写阶段的LLM自己判断，这次改动范围不含这两部分），因此本函数不导出它们。

    返回：{"舆情风险": [...], "合作动态": [...]}
    - 舆情风险每条：{事件簇名, 行业, 热度分, 具体热搜:[{标题,链接,平台,热度分}...], 风险判断说明}
    - 合作动态每条：{事件簇名, 商业实体, 行业, 热度分, 代表热搜标题, 代表热搜链接, 合作动态说明}
    """
    result: dict = {"舆情风险": [], "合作动态": []}

    for event, top, grp_sorted in _select_risk_events(df_y, n=20):
        titles = []
        for _, tr in grp_sorted.head(3).iterrows():
            titles.append({
                "标题": safe_field(tr, "热点标题", ""),
                "链接": safe_field(tr, "链接", ""),
                "平台": safe_field(tr, "平台", ""),
                "热度分": fmt_score(tr["标准化热度分"]),
            })
        result["舆情风险"].append({
            "事件簇名": event,
            "行业": safe_field(top, "行业归属"),
            "热度分": fmt_score(top["标准化热度分"]),
            "具体热搜": titles,
            "风险判断说明": str(safe_field(top, "风险判断说明", "")).replace("\n", " ")[:80],
        })

    co = _select_cooperation_events(df_y, n=20)
    for _, r in co.iterrows():
        result["合作动态"].append({
            "事件簇名": r["事件簇名"],
            "商业实体": safe_field(r, "商业实体"),
            "行业": safe_field(r, "行业归属"),
            "热度分": fmt_score(r["标准化热度分"]),
            "代表热搜标题": safe_field(r, "热点标题", ""),
            "代表热搜链接": safe_field(r, "链接", ""),
            "合作动态说明": str(safe_field(r, "合作动态说明", "")).replace("\n", " ")[:60],
        })

    return result
