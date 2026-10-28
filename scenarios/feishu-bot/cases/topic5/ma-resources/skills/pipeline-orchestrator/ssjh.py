"""SSJH（数说聚合）取数适配层 — 供 ssjh_pull handler 接真使用。

设计（类比 semantic.py）：
  - 真路径：定位仓内 SSJH-Social-Stat skill，调其权威 run() 取数，归一化出口 payload。
  - 降级路径：无凭据 / 无 skill / 调用失败 → 确定性 fallback，出口 schema 与真路径一致，
    带 degraded=True 审计标记，供下游（clean_campaign/aggregate/charts/wordcloud_insight）无差别消费。

出口结构固定为 SSJH 11 维聚合（meta/total_*/sentiment_dist/volume_type/*_trend/cloud_word/
articles/region_dist 等），articles 为 {platform: [row]}，row 字段与真实抓取一致
（content/title/publish_time/interaction_cnt/sentiment/keywords/fingerprint/id/url/
site_name/content_mode 等），使 clean_campaign 的展平/去重/情感口径无需分支。

凭据只从环境变量读（生产由 MA Vault 注入）：
  SSJH_API_TOKEN —— 不存在即判定不可用，走降级。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# MA 挂载名取 SKILL.md 的小写 name；仓内源目录保留客户原始大小写。
_ORCH_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILLS_DIR = os.path.dirname(_ORCH_DIR)
SSJH_DIR = next(
    (
        path
        for path in (
            os.path.join(_SKILLS_DIR, "ssjh-social-stat"),
            os.path.join(_SKILLS_DIR, "SSJH-Social-Stat"),
        )
        if os.path.isdir(path)
    ),
    os.path.join(_SKILLS_DIR, "ssjh-social-stat"),
)

_CST = timezone(timedelta(hours=8))
# 降级样本平台（与 clean_campaign/aggregate 消费的平台 slug 对齐）。
_FALLBACK_PLATFORMS = ("weibo", "xiaohongshu", "douyin", "weixin")


def api_available() -> bool:
    """是否具备真调用条件：有 token 且 skill 入口可导入。"""
    if not os.environ.get("SSJH_API_TOKEN"):
        return False
    return os.path.isfile(os.path.join(SSJH_DIR, "skill.py"))


def _load_skill():
    """惰性导入仓内 SSJH skill 的 DatashuoshuoSocialmedia 类。"""
    if SSJH_DIR not in sys.path:
        sys.path.insert(0, SSJH_DIR)
    from skill import DatashuoshuoSocialmedia  # noqa: E402
    return DatashuoshuoSocialmedia


def _ymd_dash(s: Any) -> Optional[str]:
    """归一化日期到 YYYY-MM-DD（SSJH run() 需要的入参格式）。接受 YYYYMMDD / YYYY-MM-DD。"""
    if not s:
        return None
    t = str(s).replace("-", "").replace("/", "")[:8]
    if len(t) != 8 or not t.isdigit():
        return None
    return f"{t[0:4]}-{t[4:6]}-{t[6:8]}"


def _plan_window(plan: dict, *, baseline: bool) -> tuple[Optional[str], Optional[str]]:
    """从 confirmed_plan 取窗口（YYYY-MM-DD）。

    baseline=True 时取基准期（活动前 30 天且不重叠）：优先 plan 内显式字段，
    否则由活动期起点回推 30 天[start-30, start-1]。缺窗口返回 (None, None)。
    """
    if baseline:
        bs = _ymd_dash(plan.get("baseline_start"))
        be = _ymd_dash(plan.get("baseline_end"))
        if bs and be:
            return bs, be
        start = _ymd_dash(plan.get("period_start"))
        if start:
            d0 = datetime.strptime(start, "%Y-%m-%d")
            end = (d0 - timedelta(days=1)).strftime("%Y-%m-%d")
            begin = (d0 - timedelta(days=30)).strftime("%Y-%m-%d")
            return begin, end
        return None, None
    return _ymd_dash(plan.get("period_start")), _ymd_dash(plan.get("period_end"))


def _keywords(plan: dict) -> list[str]:
    """取品牌/关键词：优先 plan.keywords，回退 brand_words，再回退 brand。"""
    kws = plan.get("keywords") or plan.get("brand_words")
    if isinstance(kws, str):
        kws = [kws]
    if not kws:
        b = plan.get("brand")
        kws = [b] if b else []
    return [str(k) for k in kws if str(k).strip()]


def _platforms(plan: dict) -> list[str]:
    ps = plan.get("platforms")
    if isinstance(ps, str):
        ps = [p.strip() for p in ps.split(",") if p.strip()]
    return list(ps) if ps else list(_FALLBACK_PLATFORMS)


def pull(plan: dict, *, baseline: bool = False, wordcloud: bool = False,
         filterword: str = "", content_mode: str = "ALL", msg_type: str = "1",
         filter_ad: bool = False) -> dict:
    """SSJH 取数（真路径）。多关键词各查一次后按 SSJH 合并语义合并声量维度。

    返回 SSJH 11 维聚合 dict（含 articles/cloud_word/...）。失败抛异常由调用方降级。
    """
    start, end = _plan_window(plan, baseline=baseline)
    if not (start and end):
        raise ValueError("SSJH: confirmed_plan 缺少取数窗口（period_start/period_end）")
    kws = _keywords(plan)
    if not kws:
        raise ValueError("SSJH: confirmed_plan 缺少关键词（keywords/brand）")
    platforms = _platforms(plan)

    Skill = _load_skill()
    client = Skill()  # 凭据从环境变量 SSJH_API_TOKEN 读取
    results = []
    for kw in kws:
        r = client.run(
            keyword=kw, start_date=start, end_date=end, platforms=platforms,
            filterword=filterword, content_mode=content_mode, msg_type=msg_type,
            filter_ad=filter_ad, mode="full", save=False,
        )
        results.append(r)
    merged = results[0] if len(results) == 1 else client._merge_full_results(results)
    merged.setdefault("meta", {})
    merged["meta"].update({"start_date": start, "end_date": end,
                           "keywords": kws, "platforms": platforms,
                           "window": "baseline" if baseline else "campaign"})
    return merged


# ── 确定性降级 ──────────────────────────────────────────────
def _fallback_row(platform: str, idx: int, start_dash: str, sentiment: int) -> dict:
    """构造一条结构真实的降级 article row（字段与真实抓取一致，供 clean/aggregate 消费）。"""
    ts = int(datetime.strptime(start_dash, "%Y-%m-%d")
             .replace(tzinfo=_CST).timestamp() * 1000) + idx * 86_400_000
    return {
        "id": f"fb_{platform}_{idx}",
        "item_id": f"fb_{platform}_{idx}",
        "fingerprint": f"fb_{platform}_{idx}",
        "url": f"https://example.invalid/{platform}/{idx}",
        "site_name": platform,
        "_platform": platform,
        "title": "活动相关内容（降级占位）",
        "content": "品牌活动相关内容样本，用于无凭据时跑通链路（降级占位）。",
        "keywords": [],
        "publish_time": ts,
        "interaction_cnt": 100 + idx * 7,
        "sentiment": sentiment,      # int 1/0/-1，与 SSJH 情感口径一致
        "content_mode": "UGC",
        "is_ad": 0,
    }


def fallback(plan: dict, *, baseline: bool = False) -> dict:
    """无凭据/失败降级：返回 SSJH 同构 11 维聚合（degraded=True），articles 结构真实。"""
    start, end = _plan_window(plan, baseline=baseline)
    start = start or "2024-05-01"
    end = end or "2024-05-31"
    platforms = _platforms(plan)
    kws = _keywords(plan) or ["品牌"]

    articles: dict[str, list[dict]] = {}
    cloud_word: dict[str, dict] = {}
    sentiment_total = {"positive": 0, "neutral": 0, "negative": 0}
    volume_total = 0
    for p in platforms:
        rows = []
        for i in range(3):
            senti = (1, 0, -1)[i % 3]
            rows.append(_fallback_row(p, i, start, senti))
            sk = {1: "positive", 0: "neutral", -1: "negative"}[senti]
            sentiment_total[sk] += 1
        articles[p] = rows
        volume_total += len(rows)
        cloud_word[p] = {kws[0]: 100, "活动": 60, "好评": 30}

    return {
        "degraded": True,
        "meta": {"start_date": start, "end_date": end, "keywords": kws,
                 "platforms": platforms, "window": "baseline" if baseline else "campaign"},
        "total_volume": volume_total,
        "total_interaction": sum(r["interaction_cnt"] for rs in articles.values() for r in rs),
        "total_sentiment": None,
        "sentiment_dist": {"_total": sentiment_total},
        "volume_type": {"_total": {"UGC": volume_total, "PGC": 0, "UKN": 0}},
        "data_type": {p: len(rs) for p, rs in articles.items()},
        "volume_trend": {}, "interaction_trend": {},
        "cloud_word": cloud_word,
        "articles": articles,
        "region_dist": {},
    }
