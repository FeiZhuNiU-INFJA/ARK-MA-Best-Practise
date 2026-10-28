"""Node handler registry + stub implementations.

每个 handler 签名：handler(node, state, ctx) -> dict
返回约定：
  code/tool/agent 节点：{"artifacts": {artifact_key: payload, ...}}
  decision 节点：      {"route": "<routes 键>"}
  human 节点：          raise HumanPause（由引擎捕获，置 awaiting_human）

stub 阶段：外部工具(SSJH/DataHub/飞书)和语义节点(发现/洞察/撰写/质检)
只产出占位 payload，证明编排骨架真实可跑。真实实现后续按 handler 名增量替换。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import semantic
import ssjh
import charts
import annotate as annotate_mod
import excel_source
import feishu
import validator as validator_mod

# ── 业务口径常量（源：飞书 wiki 活动复盘口径 + knowledge/_字段速查.md）────────
# 指标命名白名单：活动层只允许这些指标名；禁用“人均/转化率/性价比”等无数据支撑说法。
METRIC_WHITELIST = ("条均互动量", "品牌提及率", "产品提及占比", "NSR")
FORBIDDEN_METRIC_TERMS = ("人均", "转化率", "转化", "性价比")
# 平台中文名 → 规整英文 slug（site_name 回退映射）。
_PLATFORM_CN = {
    "小红书": "xiaohongshu", "抖音": "douyin", "哔哩哔哩": "bilibili",
    "B站": "bilibili", "微博": "weibo", "微信": "weixin", "微信公众号": "weixin",
}
# 内容类型规整：SSJH content_mode 的“未知”归一为 UKN。
_CONTENT_TYPE = {"UGC": "UGC", "PGC": "PGC", "未知": "UKN", "UKN": "UKN"}
# 周期过滤后被剔除比例超过此阈值 → 置 needs_sampling_review，供人工抽查。
_REMOVE_RATIO_REVIEW = 0.10
# demo 只对清洗后的活动明细做稳定抽样；full 始终保留全量。
_DEMO_SAMPLE_LIMIT = 500
# SSJH publish_time 为 epoch 毫秒（CST）；统一按 UTC+8 转 YYYYMMDD（与声量趋势键对齐）。
_CST = timezone(timedelta(hours=8))


class HumanPause(Exception):
    """人工关卡：引擎捕获后暂停 run，等待 resume 注入人工产物。"""

    def __init__(self, prompt: str, expect: list[str]):
        super().__init__(prompt)
        self.prompt = prompt
        self.expect = expect  # 需要人工提供的 artifact 键


_REGISTRY: dict[str, Callable] = {}


def handler(name: str):
    def deco(fn: Callable) -> Callable:
        _REGISTRY[name] = fn
        return fn
    return deco


def get(name: str) -> Callable:
    if name not in _REGISTRY:
        raise KeyError(f"未注册的 handler: {name}")
    return _REGISTRY[name]


def _stub_payload(node: dict, kind: str) -> dict:
    return {"_stub": True, "kind": kind, "node": node["id"], "name": node.get("name")}


# ── 清洗/聚合口径辅助（n07/n12 真实逻辑） ─────────────────────
def _iter_ssjh_rows(brand_stats: dict) -> list[dict]:
    """展平 SSJH articles（{platform: [row, ...]}），平台写入每行 _platform。"""
    arts = (brand_stats or {}).get("articles") or {}
    rows: list[dict] = []
    if isinstance(arts, dict):
        for platform, plist in arts.items():
            for r in (plist or []):
                rr = dict(r)
                rr.setdefault("_platform", platform)
                rows.append(rr)
    elif isinstance(arts, list):
        rows = [dict(r) for r in arts]
    return rows


def _normalize_platform(row: dict, fallback: str | None) -> str:
    """派生规整平台 slug：优先行内 _platform；回退 site_name 中文映射。"""
    p = row.get("_platform") or fallback
    if p:
        return str(p)
    return _PLATFORM_CN.get(str(row.get("site_name") or ""), "unknown")


def _derive_kol_level(row: dict) -> str:
    """派生 KOL 层级：按粉丝数分级；无有效粉丝数 → unknown（下游不生成达人层级图）。"""
    fans = row.get("fans") if row.get("fans") is not None else row.get("fans_count")
    try:
        fans = int(fans)
    except (TypeError, ValueError):
        return "unknown"
    if fans >= 500_000:
        return "head"
    if fans >= 50_000:
        return "waist"
    if fans >= 5_000:
        return "tail"
    return "ordinary"


def _publish_ymd(row: dict) -> str | None:
    """日期口径统一取发表时间 publish_time（epoch ms，CST）→ YYYYMMDD；缺失返回 None。"""
    ts = row.get("publish_time")
    if ts is None:
        return None
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:  # 毫秒
        ts /= 1000.0
    return datetime.fromtimestamp(ts, _CST).strftime("%Y%m%d")


def _dedup_key(row: dict) -> str:
    """去重键：优先 fingerprint，回退 id / item_id / url。"""
    for k in ("fingerprint", "id", "item_id", "url"):
        v = row.get(k)
        if v:
            return str(v)
    return repr(sorted(row.items()))


def _stable_sample(rows: list[dict], limit: int) -> list[dict]:
    """按稳定去重键的 SHA-256 排序抽样，重跑及输入换序时样本不漂移。"""
    return sorted(
        rows,
        key=lambda row: (
            hashlib.sha256(_dedup_key(row).encode("utf-8")).hexdigest(),
            _dedup_key(row),
        ),
    )[:limit]


# ── code 节点 ───────────────────────────────────────────────
@handler("intake_check")
def intake_check(node, state, ctx):
    """生成 G1 待填写的需求模板。

    n00 发生在用户提交 G1 表单之前，因此这里只声明字段，不伪造 demo 值。用户输入由
    hc_bridge 在 resume 时二次校验并写成 requirement_confirmed。
    """
    return {"artifacts": {"requirement_snapshot": {
        "status": "awaiting_user_input",
        "required_fields": [
            "brand", "activity_name", "period_start", "period_end", "platforms",
            "brand_query", "activity_query", "product_query", "content_scope",
            "source_file",
        ],
        "optional_fields": ["brand_filter", "activity_filter"],
        "fields_complete": False,
    }}}


@handler("load_context")
def load_context(node, state, ctx):
    return {"artifacts": {"exec_context": _stub_payload(node, "lm_error_cases")}}


@handler("clean_campaign")
def clean_campaign(node, state, ctx):
    """活动数据清洗（本地确定性，无副作用）。

    口径（源：飞书 wiki + knowledge/01_输入与范围）：
      - 周期过滤：日期取**发表时间**，落在活动窗口 [period_start, period_end] 外剔除。
      - 去重：按 fingerprint（回退 id/item_id/url）。
      - 派生：规整平台 slug、KOL 层级、发表日 YYYYMMDD。
      - demo：清洗后超过 500 条时做稳定哈希抽样；full 保留全量。
      - 剔除比例 > 10% → 置 needs_sampling_review，供人工抽查（不阻断）。
      - 全部缺发表时间 → 直接报错（日期口径无法建立）。
    """
    plan = state.read_artifact("confirmed_plan") or {}
    try:
        excel_data = excel_source.load_activity_rows(plan)
    except FileNotFoundError:
        raise
    if excel_data is None:
        brand_stats = state.read_artifact("brand_stats") or {}
        raw_rows = _iter_ssjh_rows(brand_stats)
        meta = brand_stats.get("meta") or {}
        source = "ssjh_articles"
    else:
        raw_rows = list(excel_data["rows"])
        meta = excel_data.get("meta") or {}
        source = "user_uploaded_excel"
    total_in = len(raw_rows)

    # 活动窗口：优先 confirmed_plan，回退 SSJH meta 的 start/end（YYYY-MM-DD → YYYYMMDD）。
    def _ymd(s):
        return str(s).replace("-", "")[:8] if s else None
    period_start = _ymd(plan.get("period_start") or meta.get("start_date"))
    period_end = _ymd(plan.get("period_end") or meta.get("end_date"))

    # 全缺发表时间 → 报错（不兜底）。
    if total_in and all(_publish_ymd(r) is None for r in raw_rows):
        raise ValueError("clean_campaign: 所有记录缺发表时间（publish_time），日期口径无法建立")

    seen: set[str] = set()
    cleaned: list[dict] = []
    removed = {"out_of_period": 0, "duplicate": 0, "no_date": 0}
    for r in raw_rows:
        ymd = _publish_ymd(r)
        if ymd is None:
            removed["no_date"] += 1
            continue
        if period_start and ymd < period_start:
            removed["out_of_period"] += 1
            continue
        if period_end and ymd > period_end:
            removed["out_of_period"] += 1
            continue
        key = _dedup_key(r)
        if key in seen:
            removed["duplicate"] += 1
            continue
        seen.add(key)
        platform = _normalize_platform(r, meta.get("platforms", [None])[0]
                                       if meta.get("platforms") else None)
        cleaned.append({
            **r,
            "_platform": platform,
            "_kol_level": _derive_kol_level(r),
            "_publish_ymd": ymd,
            "_dedup_key": key,
        })

    eligible_out = len(cleaned)
    run_mode = str(state.params.get("run_mode") or "full").lower()
    sample_applied = run_mode == "demo" and eligible_out > _DEMO_SAMPLE_LIMIT
    if sample_applied:
        cleaned = _stable_sample(cleaned, _DEMO_SAMPLE_LIMIT)
    total_out = len(cleaned)
    removed_total = sum(removed.values())
    remove_ratio = (removed_total / total_in) if total_in else 0.0
    cleaned_payload = {
        "rows": cleaned,
        "clean_record": {
            "source": source,
            "source_file": meta.get("file_name"),
            "total_in": total_in,
            "eligible_out": eligible_out,
            "total_out": total_out,
            "removed": removed,
            "remove_ratio": round(remove_ratio, 4),
            "needs_sampling_review": remove_ratio > _REMOVE_RATIO_REVIEW,
            "period": {"start": period_start, "end": period_end},
            "platforms": sorted({c["_platform"] for c in cleaned}),
            "sampling": {
                "applied": sample_applied,
                "mode": run_mode,
                "limit": _DEMO_SAMPLE_LIMIT if run_mode == "demo" else None,
                "population_size": eligible_out,
                "sample_size": total_out,
                "method": (
                    "stable_sha256_dedup_key" if sample_applied else "none"
                ),
                "disclosure": (
                    f"demo 模式从 {eligible_out} 条清洗后活动明细中稳定抽样 "
                    f"{total_out} 条，结果仅供流程演示与方向参考，不代表全量结论。"
                    if sample_applied else ""
                ),
            },
        },
    }
    return {"artifacts": {"cleaned_data": cleaned_payload}}


def _row_sentiment(row: dict) -> int | None:
    """取 AI 情感（优先标注 ai_sentiment，回退 SSJH sentiment）→ {1,0,-1}；缺失返回 None。

    口径：以 AI 标注为准；缺 AI 情感由调用方直接报错，不在此兜底。
    """
    v = row.get("ai_sentiment") if "ai_sentiment" in row else row.get("sentiment")
    if v is None:
        return None
    if isinstance(v, str):
        m = {"正": 1, "正面": 1, "positive": 1, "中": 0, "中性": 0, "neutral": 0,
             "负": -1, "负面": -1, "negative": -1}
        if v in m:
            return m[v]
        try:
            v = int(v)
        except ValueError:
            return None
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return None
    return iv if iv in (1, 0, -1) else None


def _is_relevant(row: dict) -> bool | None:
    """活动相关性（轮次一标注）。无 relevance 字段 → None（未标注）。"""
    if "relevance" not in row:
        return None
    v = row["relevance"]
    if isinstance(v, bool):
        return v
    return str(v) in ("是", "相关", "true", "True", "relevant", "1")


def _mentions(row: dict, words: list[str]) -> bool:
    """文本提及判定：标题/正文/关键词命中任一词。"""
    if not words:
        return False
    hay = " ".join(str(row.get(k) or "") for k in ("title", "content"))
    kw = row.get("keywords") or []
    if isinstance(kw, list):
        hay += " " + " ".join(str(x) for x in kw)
    return any(w and w in hay for w in words)


def _campaign_stats(rows: list[dict], brand_words: list[str], product_words: list[str],
                    include_neutral: bool) -> dict:
    """单活动指标：只统计传入的活动相关行；指标名走白名单。"""
    n = len(rows)
    interaction = sum(int(r.get("interaction_cnt") or 0) for r in rows)
    pos = sum(1 for r in rows if _row_sentiment(r) == 1)
    neu = sum(1 for r in rows if _row_sentiment(r) == 0)
    neg = sum(1 for r in rows if _row_sentiment(r) == -1)
    nsr_denom = (pos + neu + neg) if include_neutral else (pos + neg)
    # 提及漏斗（嵌套）：产品提及只在品牌提及行中计，保证次层 ≤ 首层。
    brand_rows = [r for r in rows if _mentions(r, brand_words)]
    brand_hits = len(brand_rows)
    product_hits = sum(1 for r in brand_rows if _mentions(r, product_words))

    trend: dict[str, int] = {}
    interaction_trend: dict[str, int] = {}
    platform_dist: dict[str, int] = {}
    kol_dist: dict[str, int] = {}
    kol_interactions: dict[str, int] = {}
    content_dist: dict[str, int] = {}
    ugc_dist: dict[str, int] = {}
    dimension_rows: dict[str, list[dict]] = {}
    for r in rows:
        ymd = r.get("_publish_ymd")
        if ymd:
            trend[ymd] = trend.get(ymd, 0) + 1
            interaction_trend[ymd] = interaction_trend.get(ymd, 0) + int(
                r.get("interaction_cnt") or 0
            )
        platform_dist[r.get("_platform", "unknown")] = platform_dist.get(r.get("_platform", "unknown"), 0) + 1
        kol_dist[r.get("_kol_level", "unknown")] = kol_dist.get(r.get("_kol_level", "unknown"), 0) + 1
        kol_interactions[r.get("_kol_level", "unknown")] = (
            kol_interactions.get(r.get("_kol_level", "unknown"), 0)
            + int(r.get("interaction_cnt") or 0)
        )
        ct = _CONTENT_TYPE.get(str(r.get("content_mode") or ""), "UKN")
        content_dist[ct] = content_dist.get(ct, 0) + 1
        category = str(r.get("ugc_category") or "").strip()
        if ct == "UGC" and category and category != "N/A":
            ugc_dist[category] = ugc_dist.get(category, 0) + 1
        dimension = str(r.get("discussion_dimension") or "").strip()
        if dimension and dimension != "N/A":
            dimension_rows.setdefault(dimension, []).append(r)

    dimension_nsr = {}
    for dimension, group in dimension_rows.items():
        gpos = sum(1 for row in group if _row_sentiment(row) == 1)
        gneu = sum(1 for row in group if _row_sentiment(row) == 0)
        gneg = sum(1 for row in group if _row_sentiment(row) == -1)
        gdenom = gpos + gneu + gneg if include_neutral else gpos + gneg
        enough = len(group) >= 30
        dimension_nsr[dimension] = {
            "status": "available" if enough else "insufficient_sample",
            "sample_size": len(group),
            "value": round((gpos - gneg) / gdenom, 4)
            if enough and gdenom else None,
        }

    metrics = {
        "活动相关条数": n,
        "互动量": interaction,
        # 白名单指标：条均互动量 = 互动量 ÷ 活动相关条数（替代“人均”）
        "条均互动量": round(interaction / n, 2) if n else None,
        # 品牌提及率 = 品牌提及条数 ÷ 活动相关条数
        "品牌提及率": round(brand_hits / n, 4) if n else None,
        # 产品提及占比 = 产品提及条数 ÷ 品牌提及条数（有产品关键词时）
        "产品提及占比": (round(product_hits / brand_hits, 4) if brand_hits and product_words else None),
        # NSR = (正-负) ÷ 分母（分母是否含中性由 params 决定）
        "NSR": (round((pos - neg) / nsr_denom, 4) if nsr_denom else None),
    }
    # 指标命名白名单硬校验：禁止出现“人均/转化/性价比”等措辞键。
    for key in metrics:
        for bad in FORBIDDEN_METRIC_TERMS:
            if bad in key:
                raise ValueError(f"aggregate: 指标名命中禁用措辞 '{bad}'：{key}")
    return {
        "row_count": n,
        "metrics": metrics,
        "sentiment_dist": {"positive": pos, "neutral": neu, "negative": neg},
        "nsr_denominator_includes_neutral": include_neutral,
        "mention_funnel": {"brand_hits": brand_hits, "product_hits": product_hits,
                           "has_product_keywords": bool(product_words)},
        "trend": dict(sorted(trend.items())),
        "interaction_trend": dict(sorted(interaction_trend.items())),
        "platform_dist": platform_dist,
        "kol_level_dist": kol_dist,
        "kol_effectiveness": {
            level: {
                "volume": count,
                "volume_share": round(count / n, 6) if n else None,
                "avg_interaction": round(
                    kol_interactions.get(level, 0) / count, 2
                ) if count else None,
            }
            for level, count in kol_dist.items()
        },
        "content_type_dist": content_dist,
        "ugc_category_dist": ugc_dist,
        "dimension_nsr": dimension_nsr,
        "top_content": {
            "pgc": sorted(
                [
                    row for row in rows
                    if _CONTENT_TYPE.get(str(row.get("content_mode") or ""), "UKN")
                    == "PGC"
                ],
                key=lambda row: int(row.get("interaction_cnt") or 0),
                reverse=True,
            )[:6],
            "ugc": sorted(
                [
                    row for row in rows
                    if _CONTENT_TYPE.get(str(row.get("content_mode") or ""), "UKN")
                    == "UGC"
                ],
                key=lambda row: int(row.get("interaction_cnt") or 0),
                reverse=True,
            )[:10],
        },
    }


@handler("aggregate")
def aggregate(node, state, ctx):
    """分对象统计聚合（本地确定性，无副作用）。

    口径（源：飞书 wiki + knowledge/_字段速查）：
      - 逐活动独立统计，**不相加为合计声量**（多活动 campaigns 各算）。
      - 活动层所有指标**只统计活动相关**内容（relevance=是），分母=活动相关条数。
      - NSR 分母是否含中性由 params.nsr_denominator_includes_neutral 决定（默认 true）。
      - 指标命名白名单（条均互动量/品牌提及率/产品提及占比），禁用人均/转化/性价比。
      - 情感以 AI 标注为准；活动相关行缺 AI 情感 → 直接报错，不兜底。
    """
    annotated = state.read_artifact("annotated_data") or {}
    cleaned = state.read_artifact("cleaned_data") or {}
    plan = state.read_artifact("confirmed_plan") or {}
    brand_period = state.read_artifact("brand_stats") or {}
    baseline = state.read_artifact("brand_stats_baseline") or {}

    # 行源：优先标注产物 rows，回退清洗产物 rows（标注接真前允许本地跑通）。
    rows = annotated.get("rows") if isinstance(annotated, dict) and annotated.get("rows") else None
    if rows is None:
        rows = cleaned.get("rows") if isinstance(cleaned, dict) else None
    rows = rows or []

    include_neutral = bool(state.params.get("nsr_denominator_includes_neutral", True))

    # 活动相关过滤：有 relevance 标注时按标注筛；全未标注时用全部并标记 relevance_missing。
    relevance_vals = [_is_relevant(r) for r in rows]
    relevance_missing = all(v is None for v in relevance_vals)
    if relevance_missing:
        relevant_rows = list(rows)
    else:
        relevant_rows = [r for r, v in zip(rows, relevance_vals) if v]

    # 缺 AI 情感不兜底：任一活动相关行取不到情感 → 报错。
    for r in relevant_rows:
        if _row_sentiment(r) is None:
            raise ValueError("aggregate: 活动相关内容缺 AI 情感标注，按口径直接报错（不兜底平台口径）")

    # 活动切分优先采用 Excel 权威字段 analysis_object，避免多活动混入 default。
    analysis_objects = sorted({
        str(row.get("analysis_object") or "").strip()
        for row in relevant_rows
        if str(row.get("analysis_object") or "").strip()
    })
    campaigns = (
        [{"id": value, "analysis_object": value} for value in analysis_objects]
        or plan.get("campaigns")
        or [{"id": plan.get("activity_name") or "default", "filter_words": []}]
    )
    brand_words = plan.get("brand_words") or ([brand] if (brand := plan.get("brand")) else [])
    product_words = plan.get("product_words") or []

    per_campaign: dict[str, dict] = {}
    for camp in campaigns:
        cid = camp.get("id", "default")
        analysis_object = camp.get("analysis_object")
        fw = camp.get("filter_words") or []
        if analysis_object:
            crows = [
                row for row in relevant_rows
                if str(row.get("analysis_object") or "").strip() == analysis_object
            ]
        else:
            crows = [r for r in relevant_rows if _mentions(r, fw)] if fw else list(relevant_rows)
        per_campaign[cid] = _campaign_stats(crows, brand_words, product_words, include_neutral)

    baseline_meta = baseline.get("meta") if isinstance(baseline, dict) else {}
    baseline_unavailable = bool(
        isinstance(baseline, dict)
        and (baseline.get("unavailable") or (baseline_meta or {}).get("unavailable"))
    )
    data_issues = []
    if baseline_unavailable:
        data_issues.append({
            "id": f"baseline-unavailable-{state.run_id}",
            "source": "controlled-stat-tool",
            "affected_module": "baseline_lift",
            "severity": "module_degraded",
            "reason": (baseline_meta or {}).get("data_issue")
            or "brand baseline unavailable",
            "action": "suppress baseline comparison and disclose limitation",
        })

    stats_bundle = {
        "schema_version": "1.1",
        # Preserve the controlled-stat-tool brand aggregate verbatim. It contains
        # daily volume/interaction, platform/content splits, sentiment, word
        # clouds and hot-content rows required by report sections 1.1-1.4.
        "brand": {
            "source": brand_period,
            "total_volume": brand_period.get("total_volume"),
            "total_interaction": brand_period.get("total_interaction"),
            "sentiment_dist": brand_period.get("sentiment_dist") or {},
            "platform_dist": brand_period.get("data_type") or {},
            "content_type_dist": brand_period.get("volume_type") or {},
            "trend": brand_period.get("volume_trend") or {},
            "interaction_trend": brand_period.get("interaction_trend") or {},
            "wordcloud": brand_period.get("cloud_word") or {},
            "hot_content": brand_period.get("articles") or {},
            "degraded": bool(brand_period.get("degraded")),
        },
        "campaigns": per_campaign,
        "relevance_missing": relevance_missing,
        "metric_whitelist": list(METRIC_WHITELIST),
        "baseline": (
            {"status": "unavailable", "total_volume": None,
             "total_interaction": None, "data_issue_id": data_issues[0]["id"]}
            if baseline_unavailable else
            ({"status": "available", "total_volume": baseline.get("total_volume"),
              "total_interaction": baseline.get("total_interaction")}
             if baseline else None)
        ),
        "baseline_lift": (
            {
                "status": "available",
                "period_volume": brand_period.get("total_volume"),
                "baseline_volume": baseline.get("total_volume"),
                "volume_change": (
                    brand_period.get("total_volume") - baseline.get("total_volume")
                    if isinstance(brand_period.get("total_volume"), (int, float))
                    and isinstance(baseline.get("total_volume"), (int, float))
                    else None
                ),
            }
            if baseline and not baseline_unavailable else
            {"status": "unavailable", "value": None}
        ),
        "data_issues": data_issues,
        "reconciliation": {
            "relevant_rows": len(relevant_rows),
            "campaign_rows": {
                key: value.get("row_count", 0)
                for key, value in per_campaign.items()
            },
            "analysis_object_source": bool(analysis_objects),
        },
        "clean_record": cleaned.get("clean_record") if isinstance(cleaned, dict) else None,
    }
    return {"artifacts": {
        "stats_bundle": stats_bundle,
        "data_issues": {"issues": data_issues},
    }}


@handler("handoff")
def handoff(node, state, ctx):
    """交回 Supervisor 闭环（n24_handoff）：复读 revision_id → 校验版本链一致 → 写 handoff.json。

    消费 delivery_record + task_result（+ feishu_doc / user_confirmation）。
    校验（validate_handoff）：
      - 交付记录已提交、发布后检查通过；
      - 复读飞书文档当前 revision_id：若与交付绑定的 revision 不一致 → 标记 revision_changed（作废告警）；
      - 用户确认版本与报告版本一致（版本链对齐，指向同一 revision 而非 latest）。
    真路径复读失败/降级时以交付记录中的 revision 为准，validated 仍可成立（degraded 透传）。
    side_effect=none：本节点只读校验 + 写闭环产物，不产生对外副作用。
    """
    delivery_record = state.read_artifact("delivery_record") or {}
    task_result = state.read_artifact("task_result") or {}
    feishu_doc = state.read_artifact("feishu_doc") or {}
    receipts = state.read_artifact("external_receipts") or {}
    user_conf = state.read_artifact("user_confirmation") or {}

    doc_token = (feishu_doc.get("doc_token") if isinstance(feishu_doc, dict) else None) \
        or (receipts.get("doc_token") if isinstance(receipts, dict) else None)
    bound_rev = (feishu_doc.get("revision_id") if isinstance(feishu_doc, dict) else None)
    if bound_rev is None and isinstance(receipts, dict):
        bound_rev = receipts.get("revision_id")
    report_version = (receipts.get("report_version") if isinstance(receipts, dict) else None) \
        or (feishu_doc.get("report_version") if isinstance(feishu_doc, dict) else None)
    degraded = bool((feishu_doc or {}).get("degraded") or (receipts or {}).get("degraded"))

    # 真交付必须实时复读；None 表示无法验证，不得等同于“未变化”。
    live_rev = feishu.current_revision(doc_token) if not degraded else None
    revision_changed = bool(live_rev is not None and bound_rev is not None
                            and live_rev != bound_rev)
    live_revision_verified = bool(
        live_rev is not None and bound_rev is not None and live_rev == bound_rev
    )
    current_delivery_ref = state.artifact_ref("delivery_draft")

    checks = {
        "committed": bool(delivery_record.get("committed")),
        "post_publish_checks_passed":
            delivery_record.get("post_publish_checks") in ("passed", True, "pass"),
        "revision_bound": bound_rev is not None,
        "live_revision_verified": live_revision_verified,
        "revision_changed": revision_changed,
        "ownership_transferred": bool(delivery_record.get("ownership_transferred")),
        "link_accessible": bool(delivery_record.get("link_accessible")),
        "version_chain_aligned": bool(report_version) and (
            isinstance(user_conf, dict)
            and user_conf.get("status") == "confirmed"
            and user_conf.get("report_version") == report_version
            and user_conf.get("revision_id") == bound_rev
            and user_conf.get("report_ref") == delivery_record.get("report_ref")
            and user_conf.get("delivery_ref") == current_delivery_ref
            and delivery_record.get("delivery_ref") == current_delivery_ref
        ),
        "validation_aligned": (
            delivery_record.get("validation_ref") == state.artifact_ref("qc_report")
            and delivery_record.get("report_ref") == state.artifact_ref("report_md")
        ),
        "degraded": degraded,
    }
    # 校验门：已提交 + 发布后检查过 + revision 已绑定且未被改动 + 版本链对齐。
    validated = (checks["committed"] and checks["post_publish_checks_passed"]
                 and checks["revision_bound"] and checks["live_revision_verified"]
                 and not checks["revision_changed"]
                 and checks["ownership_transferred"] and checks["link_accessible"]
                 and checks["version_chain_aligned"]
                 and checks["validation_aligned"] and not degraded)
    handoff_payload = {
        "validated": validated,
        "checks": checks,
        "doc_token": doc_token,
        "bound_revision_id": bound_rev,
        "live_revision_id": live_rev,
        "report_version": report_version,
        "task_result": task_result,
        "user_confirmation": user_conf,
        "degraded": degraded,
        "terminal_accepted": validated,
    }
    # 落 handoff.json 到 run_dir/delivery（闭环证据，供 Gateway/Supervisor 复核）。
    try:
        hp = state.run_dir / "delivery" / "handoff.json"
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(json.dumps(handoff_payload, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    except Exception:
        pass
    return {"artifacts": {"handoff": handoff_payload}}


# ── tool 节点（外部副作用，接真：SSJH 取数 + 确定性降级）──────────
@handler("ssjh_pull")
def ssjh_pull(node, state, ctx):
    """SSJH（数说聚合）社媒统计取数。按 node.outputs 区分三种拉取语义：

      - n05_brand_pull     → brand_stats          （品牌活动期聚合，供 n07_clean 消费 articles）
      - n06_brand_baseline → brand_stats_baseline （基准期=活动前30天不重叠，供 n12_aggregate）
      - n13_wordcloud_pull → wordcloud_data        （词云，供 n14_charts / n15_wordcloud_insight）

    真路径调仓内 SSJH skill.run()；无凭据/失败降级到确定性 fallback（出口 schema 真实，
    带 degraded=True）。基准期(n06)与词云(n13)在 DAG 里 on_fail=exception_bypass，
    降级产物保证下游 join 不断链。
    """
    out = node.get("outputs", [])
    plan = state.read_artifact("confirmed_plan") or {}
    baseline = "brand_stats_baseline" in out
    wordcloud = "wordcloud_data" in out

    if wordcloud:
        campaigns = plan.get("campaigns") or [{
            "id": plan.get("activity_name") or "default",
            "filter_words": [],
        }]
        dimensions: dict[str, dict] = {}
        degraded_any = False
        for campaign in campaigns:
            cid = str(campaign.get("id") or "default")
            local_words = [
                str(word).strip()
                for word in (campaign.get("filter_words") or [])
                if str(word).strip()
            ]
            local_query = (
                campaign.get("activity_query")
                or campaign.get("query")
                or ("(" + "|".join(local_words) + ")" if local_words else None)
            )
            if len(campaigns) > 1 and not local_query:
                raise ValueError(
                    f"ssjh_pull: 多活动 {cid} 缺少活动级 query/filter_words"
                )
            activity_query = str(
                local_query
                or plan.get("activity_query")
                or cid
            )
            query_map = {
                "overall": activity_query,
                "brand": f"({activity_query})+({plan.get('brand_query') or plan.get('brand') or ''})",
            }
            if plan.get("product_query"):
                query_map["product"] = (
                    f"({activity_query})+({plan.get('product_query')})"
                )
            campaign_result: dict[str, dict] = {}
            for dimension, query in query_map.items():
                scoped_plan = {
                    **plan,
                    "brand_words": [query],
                    "keywords": [query],
                }
                try:
                    result = (
                        ssjh.pull(
                            scoped_plan,
                            baseline=False,
                            wordcloud=True,
                            filterword=str(plan.get("activity_filter") or ""),
                        )
                        if ssjh.api_available()
                        else ssjh.fallback(scoped_plan, baseline=False)
                    )
                except Exception:
                    result = ssjh.fallback(scoped_plan, baseline=False)
                degraded = bool(result.get("degraded"))
                degraded_any = degraded_any or degraded
                campaign_result[dimension] = {
                    "query": query,
                    "platforms": result.get("cloud_word") or {},
                    "evidence_rows": [
                        {
                            "id": row.get("id") or row.get("item_id"),
                            "platform": row.get("_platform") or row.get("site_name"),
                            "title": row.get("title"),
                            "content": str(row.get("content") or "")[:300],
                            "url": row.get("url"),
                        }
                        for row in _iter_ssjh_rows(result)[:100]
                    ],
                    "degraded": degraded,
                    "not_applicable": False,
                }
            if "product" not in query_map:
                campaign_result["product"] = {
                    "query": None,
                    "platforms": {},
                    "degraded": False,
                    "not_applicable": True,
                }
            dimensions[cid] = campaign_result
        if degraded_any:
            raise RuntimeError(
                "ssjh_pull: 逐活动/整体/品牌/产品词云取数不完整"
            )
        return {"artifacts": {"wordcloud_data": {
            "campaigns": dimensions,
            "degraded": degraded_any,
            "query_contract": "campaign x overall/brand/product",
        }}}

    def _get() -> dict:
        if ssjh.api_available():
            try:
                return ssjh.pull(
                    plan,
                    baseline=baseline,
                    wordcloud=wordcloud,
                    filterword=str(plan.get("brand_filter") or ""),
                )
            except Exception:
                return ssjh.fallback(plan, baseline=baseline)
        return ssjh.fallback(plan, baseline=baseline)

    payload = _get()
    if payload.get("degraded"):
        if not baseline and not wordcloud:
            raise RuntimeError("ssjh_pull: 品牌活动期取数失败，禁止使用模拟数据")
        payload = {
            "articles": {},
            "cloud_word": {},
            "meta": {
                "unavailable": True,
                "data_issue": "controlled-stat-tool unavailable",
                "baseline": baseline,
                "wordcloud": wordcloud,
            },
            "degraded": True,
            "unavailable": True,
        }
    arts: dict[str, Any] = {}
    for k in out:
        if k == "wordcloud_data":
            # 词云出口：逐平台词表（{platform: {word: freq}}），各平台独立不合并。
            arts[k] = {
                "platforms": payload.get("cloud_word") or {},
                "meta": payload.get("meta"),
                **({"degraded": True} if payload.get("degraded") else {}),
            }
        else:
            # brand_stats / brand_stats_baseline：原样透传 SSJH 11 维聚合（含 articles）。
            arts[k] = payload
    return {"artifacts": arts}


@handler("annotate")
def annotate(node, state, ctx):
    """AI 标注（轮次一 n09 / 轮次二 n11），按 node.outputs 分流。

      - outputs 含 annotated_round1 → 轮次一：情感 + 活动相关（消费 cleaned_data.rows）。
      - outputs 含 annotated_data   → 轮次二：观点/UGC方式/讨论维度 三独立任务 + 覆盖检查
        （消费 annotated_round1.rows，注入 confirmed_opinion_labels + ugc_category_list）。

    路由：行数 ≥ params.annotation_routing_threshold 且 DataHub 可用 → 批量引擎；
    否则小引擎（semantic）；无凭据降级（degraded=True，出口 schema 一致）。
    口径：情感以 AI 标注为准；轮次二覆盖检查 ERROR（真路径缺口）→ raise 让引擎 pause 重试。
    side_effect=annotation-batch、idempotency、retry 由引擎处理；本 handler 不因缺凭据抛异常。
    """
    out = node.get("outputs", [])
    threshold = int(state.params.get("annotation_routing_threshold", 1000))

    if "annotated_round1" in out:
        cleaned = state.read_artifact("cleaned_data") or {}
        src_rows = cleaned.get("rows") if isinstance(cleaned, dict) else None
        rows = [dict(r) for r in (src_rows or [])]
        result = annotate_mod.annotate_round1(rows, threshold=threshold)
        if result.get("degraded"):
            raise RuntimeError("annotate r1: 标注能力不可用，禁止确定性伪标注")
        coverage = result.get("coverage") or {}
        if (
            coverage.get("sentiment_missing", 0) > 0
            or coverage.get("relevance_missing", 0) > 0
        ):
            raise ValueError(
                "annotate r1: 覆盖检查存在缺口；"
                f"sentiment_missing={coverage.get('sentiment_missing', 0)}, "
                f"relevance_missing={coverage.get('relevance_missing', 0)}"
            )
        return {"artifacts": {"annotated_round1": result}}

    if "annotated_data" in out:
        r1 = state.read_artifact("annotated_round1") or {}
        src_rows = r1.get("rows") if isinstance(r1, dict) else None
        if not src_rows:
            cleaned = state.read_artifact("cleaned_data") or {}
            src_rows = cleaned.get("rows") if isinstance(cleaned, dict) else None
        rows = [dict(r) for r in (src_rows or [])]

        confirmed = state.read_artifact("confirmed_opinion_labels") or {}
        opinion_labels = (confirmed.get("labels") or confirmed.get("candidates")
                          if isinstance(confirmed, dict) else None) or []
        ugc_list = state.read_artifact("ugc_category_list") or {}
        ugc_not_applicable = bool(
            isinstance(ugc_list, dict) and ugc_list.get("not_applicable")
        )
        ugc_categories = (
            confirmed.get("ugc_categories")
            if isinstance(confirmed, dict) else None
        ) or (
            ugc_list.get("categories") if isinstance(ugc_list, dict) else None
        ) or []

        result = annotate_mod.annotate_round2(
            rows, opinion_labels=list(opinion_labels),
            ugc_categories=list(ugc_categories), threshold=threshold,
            ugc_not_applicable=ugc_not_applicable)
        if result.get("degraded"):
            raise RuntimeError("annotate r2: 标注能力不可用，禁止确定性伪标注")
        # 覆盖检查缺口（真路径 ERROR）→ 直接报错，交引擎按 retry/on_fail=pause 处理。
        cov = result.get("coverage") or {}
        if cov.get("has_error"):
            miss = {t: cov[t]["missing"] for t in ("opinion", "ugc_category",
                    "discussion_dimension") if cov.get(t, {}).get("status") == "ERROR"}
            raise ValueError(f"annotate r2: 覆盖检查存在缺口（missing），按口径报错：{miss}")
        return {"artifacts": {"annotated_data": result}}

    # 兜底：未知 outputs，保持 schema 可跑（不应触达）。
    return {"artifacts": {k: _stub_payload(node, "annotated") for k in out}}


@handler("charts_render")
def charts_render(node, state, ctx):
    """图表 + 封面渲染为本地 PNG（n14_charts）。

    消费 stats_bundle + wordcloud_data，产 chart_urls（含逐张 local_path + 封面）。
    图表选题/数据绑定为确定性（charts.build_chart_specs）；真路径逐张渲染 PNG 到
    run_dir/charts（供飞书交付层上传 media），逐张生成逐张自检；无 node_modules / node /
    渲染失败降级到确定性占位（出口 schema 与真路径一致，带 degraded=True，local_path=None，
    保留 spec 便于后续补渲染）。COS 已从链路移除——图片经飞书 medias/upload_all 原生上传。

    side_effect=chart-render、idempotency=chart-render:{task_id}:{resource_lock}、retry max2 on_fail=pause
    由引擎统一处理：本 handler 只需保证返回 chart_urls 且不因缺渲染器抛异常（避免 pause）。
    """
    stats = state.read_artifact("stats_bundle") or {}
    wc = state.read_artifact("wordcloud_data") or {}
    plan = state.read_artifact("confirmed_plan") or {}

    project = str(plan.get("project") or plan.get("brand") or "topic5")
    report_name = str(plan.get("report_name") or f"{project}营销活动复盘")
    version = f"v0.{state.counters.get('report_patch', 0)}"
    outdir = str(state.run_dir / "charts")

    def _build() -> dict:
        if charts.renderer_available():
            try:
                return charts.render_charts(
                    stats, wc, project=project, version=version,
                    report_name=report_name, outdir=outdir)
            except Exception:
                return charts.fallback(
                    stats, wc, project=project, version=version, report_name=report_name)
        return charts.fallback(
            stats, wc, project=project, version=version, report_name=report_name)

    result = _build()
    if result.get("degraded"):
        raise RuntimeError("charts_render: 图表渲染失败，禁止无图交付")
    return {"artifacts": {"chart_urls": result}}


@handler("feishu_export")
def feishu_export(node, state, ctx):
    return {"artifacts": {"feishu_doc": {"_stub": True,
                                         "url": "https://feishu.example/doc/stub",
                                         "revision": 1}}}


# ── agent 节点（语义，接真：Ark Chat + 确定性降级）─────────────
# 固定四类（params.ugc_category_mode=fixed_four，默认）：活动复盘 UGC 参与方式标准四类。
_UGC_FIXED_FOUR = ["模仿打卡", "创意改编", "测评分享", "自发安利"]


def _sample_texts(rows: list[dict], limit: int = 40, maxlen: int = 120) -> list[str]:
    """从行取标题/正文样本供语义提示；去空、截断、限量，控制 token。"""
    out: list[str] = []
    for r in rows:
        t = str(r.get("title") or "").strip()
        c = str(r.get("content") or "").strip()
        s = (t + " " + c).strip()[:maxlen]
        if s:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _wordcloud_platforms(wc: dict) -> dict[str, list[str]]:
    """从词云产物抽各平台词表：兼容 {platform: {word: freq}} / {platform: [word]}。"""
    plats: dict[str, list[str]] = {}
    src = (wc or {}).get("platforms") or (wc or {}).get("wordclouds") or wc or {}
    if isinstance(src, dict):
        for plat, words in src.items():
            if plat in ("_stub", "kind", "node", "name"):
                continue
            if isinstance(words, dict):
                plats[plat] = list(words.keys())[:50]
            elif isinstance(words, list):
                plats[plat] = [str(w) for w in words][:50]
    return plats


@handler("ugc_discovery")
def ugc_discovery(node, state, ctx):
    """UGC 参与方式发现。

    口径（knowledge/_字段速查 + params）：
      - ugc_category_mode=fixed_four（默认）→ 锁定标准四类，不调模型。
      - dynamic → 从活动相关内容样本语义聚类出参与方式类别；无凭据降级到四类。
    输出 schema 固定：{mode, categories[], degraded?, source}
    """
    plan = state.read_artifact("confirmed_plan") or {}
    selected = plan.get("selected_modules") if isinstance(plan, dict) else {}
    enabled = (
        selected.get("core_plus_conditional")
        if isinstance(selected, dict) else None
    ) or []
    if (
        str(plan.get("content_scope") or "").lower() == "pgc"
        or (enabled and "ugc_participation" not in enabled)
    ):
        return {"artifacts": {"ugc_category_list": {
            "mode": "not_applicable",
            "categories": [],
            "source": "confirmed_plan",
            "not_applicable": True,
            "reason": "content_scope=pgc; UGC participation module disabled",
        }}}

    mode = state.params.get("ugc_category_mode", "fixed_four")
    if mode == "fixed_four":
        return {"artifacts": {"ugc_category_list": {
            "mode": mode, "categories": list(_UGC_FIXED_FOUR), "source": "fixed"}}}

    cleaned = state.read_artifact("cleaned_data") or {}
    rows = cleaned.get("rows") if isinstance(cleaned, dict) else None
    samples = _sample_texts(rows or [])
    if not semantic.llm_available() or not samples:
        raise RuntimeError("ugc_discovery: 动态类别发现能力不可用")

    sys_p = ("你是营销 UGC 分析师。从用户发布的活动相关内容中，归纳 UGC（用户生产内容）的"
             "“参与方式”类别（如模仿打卡、创意改编、测评分享、自发安利等）。只输出 JSON，"
             "格式 {\"categories\": [\"类别1\", ...]}，4-6 个互斥类别，用简体中文短语。")
    user_p = "活动相关内容样本（每行一条）：\n" + "\n".join(samples)
    try:
        data = semantic.chat_json(sys_p, user_p, max_tokens=1024)
        cats = [str(c).strip() for c in (data.get("categories") or []) if str(c).strip()]
        if not cats:
            raise ValueError("模型未返回类别")
        return {"artifacts": {"ugc_category_list": {
            "mode": mode, "categories": cats[:6], "source": "llm"}}}
    except Exception:
        raise


@handler("opinion_candidates")
def opinion_candidates(node, state, ctx):
    """观点候选生成（供 n10_hc_opinion 人工确认后注入轮次二）。

    基于轮次一标注的活动相关内容，语义聚类出观点候选（正/负/属性讨论的主题标签）。
    无凭据降级到基于文本的确定性候选。输出 schema：{candidates[], source, degraded?}
    """
    r1 = state.read_artifact("annotated_round1") or {}
    rows = r1.get("rows") if isinstance(r1, dict) else None
    if not rows:
        cleaned = state.read_artifact("cleaned_data") or {}
        rows = cleaned.get("rows") if isinstance(cleaned, dict) else None
    samples = _sample_texts(rows or [])

    if not semantic.llm_available() or not samples:
        raise RuntimeError("opinion_candidates: 观点发现能力不可用")

    sys_p = ("你是舆情观点分析师。从活动相关内容中归纳“观点候选”——用户讨论聚焦的观点主题"
             "（覆盖正面/负面/属性维度），供人工确认后用于第二轮观点标注。只输出 JSON，"
             "格式 {\"candidates\": [\"观点主题1\", ...]}，5-8 个，用简体中文短语，互斥、可标注。")
    user_p = "活动相关内容样本（每行一条）：\n" + "\n".join(samples)
    try:
        data = semantic.chat_json(sys_p, user_p, max_tokens=1024)
        cands = [str(c).strip() for c in (data.get("candidates") or []) if str(c).strip()]
        if not cands:
            raise ValueError("模型未返回观点候选")
        return {"artifacts": {"opinion_candidates": {
            "candidates": cands[:8],
            "source": "llm",
            "r1_ref": bool(r1),
            "evidence_count": len(samples),
        }}}
    except Exception:
        raise


@handler("opinion_policy_gate")
def opinion_policy_gate(node, state, ctx):
    """Auto-confirm stable label sets; pause only when human judgment is needed."""
    opinion = state.read_artifact("opinion_candidates") or {}
    ugc = state.read_artifact("ugc_category_list") or {}
    labels = [
        str(value).strip()
        for value in (opinion.get("candidates") or [])
        if str(value).strip()
    ]
    ugc_categories = [
        str(value).strip()
        for value in (ugc.get("categories") or [])
        if str(value).strip()
    ]
    vague_labels = {"其他", "其它", "未知", "一般", "相关", "正面", "负面", "中性"}
    reasons = []
    if opinion.get("source") != "llm" or opinion.get("degraded"):
        reasons.append("观点候选不是独立语义模型的完整结果")
    if not 5 <= len(labels) <= 8:
        reasons.append("观点标签数量不在 5-8 个范围")
    if len(set(labels)) != len(labels):
        reasons.append("观点标签存在重复")
    if any(len(label) > 20 or label in vague_labels for label in labels):
        reasons.append("观点标签过长或语义过于宽泛")
    if int(opinion.get("evidence_count") or 0) < len(labels):
        reasons.append("候选标签缺少足够样本支撑")
    if (
        not ugc.get("not_applicable")
        and (
            ugc.get("degraded")
            or not ugc_categories
            or len(set(ugc_categories)) != len(ugc_categories)
            or ugc.get("mode") not in ("fixed_four", "dynamic")
        )
    ):
        reasons.append("UGC 类别不完整或不稳定")
    if ugc.get("mode") == "dynamic":
        reasons.append("动态 UGC 类别需要人工确认业务可用性")

    if reasons:
        raise HumanPause(
            prompt="观点与 UGC 标签存在歧义，需要人工确认：" + "；".join(reasons),
            expect=node.get("outputs", []),
        )

    return {"artifacts": {"confirmed_opinion_labels": {
        "labels": labels,
        "ugc_categories": ugc_categories,
        "ugc_not_applicable": bool(ugc.get("not_applicable")),
        "confirmed": True,
        "confirmed_by": "validator-policy",
        "automatic": True,
        "policy_version": "opinion-label-policy-v1",
        "subject": {
            "opinion_candidates": state.artifact_ref("opinion_candidates"),
            "ugc_category_list": state.artifact_ref("ugc_category_list"),
        },
    }}}


@handler("wordcloud_insight")
def wordcloud_insight(node, state, ctx):
    """Resolve each word against source posts, remove noise, then cluster topics."""
    wc = state.read_artifact("wordcloud_data") or {}
    campaigns = wc.get("campaigns") if isinstance(wc, dict) else None
    if not semantic.llm_available() or not campaigns:
        raise RuntimeError("wordcloud_insight: 缺少可核查词云或语义能力")

    sys_p = ("你是内容议题分析师。对给定平台的词云高频词做三步解读：剔除噪音词，把相关词"
             "聚成恰好 3 个简洁议题，每个议题给名称、关键词、用户行为/市场含义和行动建议。"
             "words 必须逐字选自给定词云高频词；每个结论必须引用给定帖子的短引用 "
             "E01/E02 等，禁止改写或编造引用；不要跨活动、维度或平台合并。只输出 JSON，格式 "
             "{\"noise_words\":[],\"topics\":[{\"topic\":\"\",\"words\":[],"
             "\"evidence_refs\":[],\"insight\":\"\",\"action\":\"\"}]}。")
    out: dict[str, dict] = {}
    degraded = False
    failures: list[dict] = []
    for cid, dimensions in campaigns.items():
        out[cid] = {}
        for dimension, bundle in (dimensions or {}).items():
            if bundle.get("not_applicable"):
                out[cid][dimension] = {"not_applicable": True, "platforms": {}}
                continue
            evidence = bundle.get("evidence_rows") or []
            platform_words = bundle.get("platforms") or {}
            out[cid][dimension] = {"platforms": {}}
            for platform, frequency in platform_words.items():
                words = (
                    list(frequency.keys())
                    if isinstance(frequency, dict) else list(frequency or [])
                )
                posts = [
                    row for row in evidence
                    if str(row.get("platform") or "") in (str(platform), "")
                ]
                if not words or not posts:
                    degraded = True
                    failures.append({
                        "campaign": cid,
                        "dimension": dimension,
                        "platform": platform,
                        "status": "insufficient_evidence",
                        "word_count": len(words),
                        "post_count": len(posts),
                    })
                    out[cid][dimension]["platforms"][platform] = {
                        "topics": [],
                        "status": "insufficient_evidence",
                    }
                    continue
                # Keep this semantic request bounded. Source posts can contain full
                # social articles; sending dozens verbatim caused repeated 180s
                # model timeouts without improving the evidence contract.
                prompt_posts = posts[:6]
                evidence_ref_map = {
                    f"E{index:02d}": str(row.get("id"))
                    for index, row in enumerate(prompt_posts, 1)
                    if row.get("id") is not None
                }
                prompt_evidence = [
                    {
                        "ref": f"E{index:02d}",
                        "title": str(row.get("title") or "")[:80],
                        "content": str(row.get("content") or "")[:180],
                    }
                    for index, row in enumerate(prompt_posts, 1)
                ]
                prompt_words = words[:16]
                user_p = (
                    f"活动：{cid}\n维度：{dimension}\n平台：{platform}\n"
                    f"词云高频词：{'、'.join(prompt_words)}\n"
                    "代表帖子（只能引用 ref）：\n"
                    + json.dumps(prompt_evidence, ensure_ascii=False)
                )
                try:
                    data = semantic.chat_json(
                        sys_p,
                        user_p,
                        max_tokens=768,
                        retries=1,
                        thinking="disabled",
                    )
                    valid_ids = set(evidence_ref_map.values())
                    allowed_words = {
                        str(word).strip().casefold()
                        for word in prompt_words
                        if str(word).strip()
                    }
                    topics = []
                    invalid_reasons = []
                    for topic in (data.get("topics") or [])[:5]:
                        if not isinstance(topic, dict):
                            invalid_reasons.append("topic_not_object")
                            continue
                        evidence_refs = {
                            str(value).strip().upper()
                            for value in (topic.get("evidence_refs") or [])
                            if str(value).strip()
                        }
                        evidence_ids = {
                            evidence_ref_map[value]
                            for value in evidence_refs
                            if value in evidence_ref_map
                        }
                        # Backward compatibility for exact immutable IDs.
                        evidence_ids.update(
                            str(value)
                            for value in (topic.get("evidence_ids") or [])
                            if str(value) in valid_ids
                        )
                        topic_words = [
                            str(value).strip()
                            for value in (topic.get("words") or [])
                            if str(value).strip()
                        ]
                        unknown_refs = sorted(
                            value for value in evidence_refs
                            if value not in evidence_ref_map
                        )
                        unsupported_words = [
                            value for value in topic_words
                            if value.casefold() not in allowed_words
                        ]
                        supported_words = [
                            value for value in topic_words
                            if value.casefold() in allowed_words
                        ]
                        if not evidence_ids or unknown_refs or not supported_words:
                            invalid_reasons.append({
                                "topic": topic.get("topic"),
                                "unknown_evidence_refs": unknown_refs,
                                "unsupported_words": unsupported_words,
                            })
                            continue
                        topic = {
                            **topic,
                            "words": supported_words,
                            "evidence_ids": sorted(evidence_ids),
                        }
                        if unsupported_words:
                            topic["dropped_unsupported_words"] = unsupported_words
                        topic.pop("evidence_refs", None)
                        topics.append(topic)
                    if not topics:
                        degraded = True
                        failures.append({
                            "campaign": cid,
                            "dimension": dimension,
                            "platform": platform,
                            "status": "invalid_evidence",
                            "reasons": invalid_reasons[:3],
                        })
                        out[cid][dimension]["platforms"][platform] = {
                            "topics": topics,
                            "noise_words": data.get("noise_words") or [],
                            "evidence_count": len(posts),
                            "status": "invalid_evidence",
                            "invalid_reasons": invalid_reasons,
                        }
                        continue
                    out[cid][dimension]["platforms"][platform] = {
                        "topics": topics,
                        "noise_words": data.get("noise_words") or [],
                        "evidence_count": len(posts),
                        "status": "ok",
                        **(
                            {"discarded_topics": invalid_reasons}
                            if invalid_reasons else {}
                        ),
                    }
                except Exception as error:
                    degraded = True
                    failures.append({
                        "campaign": cid,
                        "dimension": dimension,
                        "platform": platform,
                        "status": "semantic_failure",
                        "error": f"{type(error).__name__}: {error}"[:300],
                    })
                    out[cid][dimension]["platforms"][platform] = {
                        "topics": [],
                        "status": "semantic_failure",
                    }
    if degraded:
        raise RuntimeError(
            "wordcloud_insight: 逐词查帖证据或语义解读不完整；"
            + json.dumps(failures[:5], ensure_ascii=False)
        )
    return {"artifacts": {"wordcloud_insight": {
        "campaigns": out, "source": "llm" if not degraded else "mixed",
        **({"degraded": True} if degraded else {})}}}


def _report_evidence(stats: dict) -> dict:
    """保留完整聚合证据，移除重复原始抓取数据及个人账号信息。

    不能截断 stats_bundle 的 JSON：brand.source 中的原帖可能挤掉后面的活动统计。
    """
    def compact(value, *, anonymous=False):
        if isinstance(value, list):
            return [compact(item, anonymous=anonymous) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key.startswith("_") and key != "_total":
                continue
            if key in {"source", "articles", "content", "raw", "keywords"}:
                continue
            if key in {"author", "author_id", "user_id", "nickname", "account_name"}:
                continue
            result[key] = compact(item, anonymous=anonymous or key == "ugc")
        return result
    return compact(stats)


@handler("write_report")
def write_report(node, state, ctx):
    """报告内容层撰写（MD，不含飞书布局）。

    口径（knowledge/04 报告结构 v2.6）：
      - 五章结构（report_chapters=five 默认）：篇头/品牌传播/活动传播/综合定位/经验总结(+总结建议)。
      - 指标白名单（条均互动量/品牌提及率/产品提及占比/NSR），禁用“人均/转化/性价比”。
      - 活动层只讲“提及”不讲“转化”；多活动逐个处理不相加；竞品章已下线。
    消费 stats_bundle + chart_urls + wordcloud_insight。无凭据降级到确定性模板 MD。
    输出 schema：{markdown, chapters, version, source, degraded?}
    """
    ver = state.counters.get("report_patch", 0)
    version = f"0.{ver}"
    chapters = state.params.get("report_chapters", "five")
    stats = state.read_artifact("stats_bundle") or {}
    charts = state.read_artifact("chart_urls") or {}
    wc_insight = state.read_artifact("wordcloud_insight") or {}

    if not semantic.llm_available() or not stats:
        raise RuntimeError("write_report: 缺少统计产物或语义能力")

    sys_p = (
        "你是资深营销复盘报告撰写人。依据统计数据与词云议题，写出活动复盘报告的内容层 Markdown"
        "（不含飞书布局）。严格遵守口径：\n"
        "1) 指标只用【条均互动量/品牌提及率/产品提及占比/NSR】，禁用“人均/转化/转化率/性价比”等无数据支撑说法；\n"
        "2) 活动层指标只统计活动相关内容，多活动逐个处理、不相加为合计声量；\n"
        "3) 提及漏斗只讲“提及”不讲“转化”；竞品分析已下线，不要写竞品章；\n"
        f"4) 报告结构={chapters}，必须包含：数据说明、整体洞察、1.1传播概况、1.2口碑分析、"
        "1.3内容分析、1.4热门内容；每个活动独立包含传播概况/活动声量渗透分析/口碑分析/"
        "内容分析/热门内容；以及 3.1 基准期对比、经验总结中的成功经验/待改进问题/行动计划、"
        "总结与建议；不得生成已下线的竞品章节；\n"
        "5) 所有数字必须来自给定数据，不得编造；证据不足就说明，不凑根因；\n"
        "6) 需插图时用 Markdown 图片语法 `![标题](local_path)`，local_path 取 chart_urls 中每张图的"
        "`local_path` 字段（本地 PNG 路径，交付时会上传进飞书文档）；local_path 为空的图跳过不插。\n"
        "7) 五个大章必须原样命名：`## 01 品牌传播表现`、`## 02 活动传播表现`、"
        "`## 03 综合表现定位`、`## 04 经验总结`、`## 05 总结与建议`；"
        "活动标题为 `### 2.N 活动 {活动名}`，五个分节用 `#### 2.N.1` 至 `#### 2.N.5`。"
        "每节顺序为分析文字→对应图→数据表；图必须放在对应章节，不能集中堆在热门内容后。\n"
        "8) 整体洞察必须含具体发现和数字，成功经验、待改进问题、行动计划逐条回指章节和数字；"
        "有负面样本必须分析，不能用泛泛的证据不足代替；小样本<30标明样本不足，不下确定性结论。\n"
        "9) 不输出内部字段名、版本、模板生成说明、布局指令或原始 JSON；UGC 个人账号必须匿名，"
        "公开 PGC 机构或 KOL 作者仅可在热门内容表展示；词云解读必须为连贯叙事。\n"
        "10) 基准期30天和活动期长度不同，必须同时比较日均声量并披露假期影响；"
        "不把总量差异直接解释为活动效果；所有推导数值需写清分子、分母或计算方式。\n"
        "11) 若 stats_bundle.clean_record.sampling.applied=true，必须在“00 篇头 · 数据说明”"
        "明确写出总体量、实际样本量、demo 抽样及“仅供参考、不代表全量结论”，不得把样本统计"
        "外推成全量活动结论。\n"
        "只输出 Markdown 正文。")
    user_p = ("统计数据(stats_bundle)：\n" + json.dumps(_report_evidence(stats), ensure_ascii=False)
              + "\n\n词云议题(wordcloud_insight)：\n"
              + json.dumps(wc_insight, ensure_ascii=False)
              + "\n\n图表(chart_urls，含每张图 local_path)：\n"
              + json.dumps(charts, ensure_ascii=False)
              + "\n\n冻结需求：\n"
              + json.dumps(state.read_artifact("confirmed_plan") or {}, ensure_ascii=False))
    try:
        md = semantic.chat(sys_p, user_p, max_tokens=8192)
        if not md.strip():
            raise ValueError("模型返回空报告")
        md, removed_sentences = _strip_forbidden_sentences(md)
        source = "llm"
        guardrail_reason = ""
        try:
            _assert_no_forbidden_terms(md)
            _assert_report_structure(
                md, list((stats.get("campaigns") or {}).keys())
            )
        except ValueError as error:
            guardrail_reason = str(error)
            md = _fallback_report_md(
                stats, charts, wc_insight, chapters, version
            )
            md, fallback_removed = _strip_forbidden_sentences(md)
            removed_sentences.extend(fallback_removed)
            _assert_no_forbidden_terms(md)
            _assert_report_structure(
                md, list((stats.get("campaigns") or {}).keys())
            )
            source = "deterministic_guardrail"
        return {"artifacts": {"report_md": {
            "markdown": md,
            "chapters": chapters,
            "version": version,
            "source": source,
            "guardrail_removed_sentences": removed_sentences,
            **(
                {"guardrail_fallback_reason": guardrail_reason}
                if guardrail_reason else {}
            ),
        }}}
    except Exception:
        raise


@handler("fix_report")
def fix_report(node, state, ctx):
    """P0 修复（patch 递增后复检）。

    读 qc_report 的 P0 问题与当前 report_md，语义修复后版本递增。
    无凭据降级：在报告追加“修复记录”并递增版本（保持可复检）。
    输出 schema 与 write_report 一致：{markdown, chapters, version, fixed_round, source, degraded?}
    """
    state.counters["qc_rounds"] = state.counters.get("qc_rounds", 0) + 1
    state.counters["report_patch"] = state.counters.get("report_patch", 0) + 1
    version = f"0.{state.counters['report_patch']}"
    rnd = state.counters["qc_rounds"]
    qc = state.read_artifact("qc_report") or {}
    prev = state.read_artifact("report_md") or {}
    chapters = (prev.get("chapters") if isinstance(prev, dict) else None) \
        or state.params.get("report_chapters", "five")
    prev_md = prev.get("markdown", "") if isinstance(prev, dict) else ""
    issues = qc.get("issues") or []

    def _issue_text(items) -> str:
        parts = [str(i.get("msg", i)) if isinstance(i, dict) else str(i) for i in items]
        return "；".join(parts) or "按质检意见修订"

    if not semantic.llm_available() or not prev_md:
        raise RuntimeError("fix_report: 无法执行报告返修")

    stats = state.read_artifact("stats_bundle") or {}
    charts_payload = state.read_artifact("chart_urls") or {}
    requirement = state.read_artifact("requirement_confirmed") or {}
    sys_p = (
        "你是报告质检修复人。逐项关闭给定质检问题后输出完整 Markdown。"
        "每个 P0 都必须根据 id/title/evidence/locus 修复，不能只概括处理；保持未涉及章节和图表不变。"
        "修订结果会立即由与独立 Validator 相同的确定性 scan_report 规则预检，仍有 P0 会在当前"
        "返修节点继续要求修改。口径不变：指标白名单、只讲提及不讲转化、数字不得编造。"
        "小样本披露可使用“样本不足”“样本量不足”“样本数不足”“样本量<30”等明确措辞，"
        "并且必须靠近对应维度名称，同时不得输出确定性结论。只输出修订后的完整 Markdown。"
    )

    def build_user_prompt(current_md: str, current_issues: list) -> str:
        return (
            "必须逐项关闭的质检问题（完整结构）：\n"
            + json.dumps(current_issues, ensure_ascii=False)
            + "\n\n当前报告 Markdown（完整正文，必须保留未涉及章节）：\n"
            + current_md
            + "\n\n完整聚合证据（不含原始抓取数据，UGC账号已去除）：\n"
            + json.dumps(_report_evidence(stats), ensure_ascii=False)
            + "\n\n图表与正确本地路径（按对应章节插入）：\n"
            + json.dumps(charts_payload, ensure_ascii=False)
            + "\n\n词云议题：\n"
            + json.dumps(state.read_artifact("wordcloud_insight") or {}, ensure_ascii=False)
            + "\n\n冻结需求：\n"
            + json.dumps(state.read_artifact("confirmed_plan") or {}, ensure_ascii=False)
        )

    current_md = prev_md
    current_issues = list(issues)
    last_preflight: dict = {}
    try:
        for repair_attempt in range(1, 4):
            md = semantic.chat(
                sys_p,
                build_user_prompt(current_md, current_issues),
                max_tokens=16384,
            )
            if not md.strip():
                raise ValueError("模型返回空修订")
            md, removed_sentences = _strip_forbidden_sentences(md)
            _assert_no_forbidden_terms(md)
            _assert_report_structure(
                md,
                list((stats.get("campaigns") or {}).keys()),
            )
            previous_headings = [
                line.strip()
                for line in prev_md.splitlines()
                if line.lstrip().startswith("##")
            ]
            missing_headings = [heading for heading in previous_headings if heading not in md]
            if missing_headings:
                raise ValueError(
                    "fix_report: 修订丢失原有章节: " + ", ".join(missing_headings)
                )
            last_preflight = validator_mod.validate_report(
                {"markdown": md, "degraded": False},
                requirement=requirement,
                stats=stats,
                charts=charts_payload,
            )
            remaining_p0 = [
                item
                for item in (last_preflight.get("issues") or [])
                if item.get("severity") == "P0"
            ]
            if not remaining_p0:
                return {"artifacts": {"report_md": {
                    "markdown": md, "chapters": chapters, "version": version,
                    "fixed_round": rnd,
                    "source": "llm",
                    "guardrail_removed_sentences": removed_sentences,
                    "deterministic_preflight": {
                        "attempts": repair_attempt,
                        "p0_count": 0,
                        "scan_mode": last_preflight.get("scan_mode"),
                    },
                }}}
            current_md = md
            current_issues = remaining_p0
            sys_p += (
                "\n上一版未通过确定性预检。下一次必须精确关闭以下剩余 P0，"
                "不得用仅语义近似但机器规则不接受的表达。"
            )
        remaining_ids = [
            str(item.get("id") or item.get("title") or "unknown")
            for item in (last_preflight.get("issues") or [])
            if item.get("severity") == "P0"
        ]
        raise ValueError(
            "fix_report: 3 次节点内返修后仍有确定性 P0: "
            + ", ".join(remaining_ids)
        )
    except Exception:
        raise


def _strip_forbidden_sentences(md: str) -> tuple[str, list[str]]:
    """删除含禁用措辞的整句；不做同义改写，避免制造无数据支撑的新表述。"""
    cleaned_lines: list[str] = []
    removed: list[str] = []
    for line in md.splitlines():
        if not any(term in line for term in FORBIDDEN_METRIC_TERMS):
            cleaned_lines.append(line)
            continue
        if line.lstrip().startswith("#"):
            cleaned_lines.append(line)
            continue
        parts = re.split(r"(?<=[。！？；])", line)
        kept = []
        for part in parts:
            if any(term in part for term in FORBIDDEN_METRIC_TERMS):
                if part.strip():
                    removed.append(part.strip())
            else:
                kept.append(part)
        cleaned = "".join(kept).rstrip()
        if cleaned:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines), removed


def _assert_no_forbidden_terms(md: str) -> None:
    """报告正文禁用措辞硬校验：命中“人均/转化率/转化/性价比”直接报错。"""
    for bad in FORBIDDEN_METRIC_TERMS:
        if bad in md:
            raise ValueError(f"report: 正文命中禁用措辞 '{bad}'")


def _assert_report_structure(md: str, campaign_ids: list[str]) -> None:
    required = [
        "数据说明", "整体洞察", "品牌传播表现", "1.1", "1.2", "1.3", "1.4",
        "活动传播表现", "传播概况", "活动声量渗透", "口碑分析", "内容分析",
        "热门内容", "综合表现定位", "经验总结", "总结与建议",
    ]
    missing = [item for item in required if item not in md]
    missing.extend(
        f"活动:{campaign_id}"
        for campaign_id in campaign_ids
        if campaign_id not in md
    )
    if missing:
        raise ValueError("report: 缺少必需章节或活动分节: " + ", ".join(missing))


_REPORT_LABELS = {
    "positive": "正面",
    "neutral": "中性",
    "negative": "负面",
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "weibo": "微博",
    "PGC": "PGC",
    "UGC": "UGC",
    "UKN": "未知",
    "UNK": "未知",
    "pgc": "PGC",
    "ugc": "UGC",
}


def _report_label(value: object) -> str:
    text = str(value or "").strip()
    return _REPORT_LABELS.get(text, text or "未知")


def _format_report_number(value: object) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value or "N/A")


def _format_distribution(value: object) -> str:
    """Render internal distribution objects as compact user-facing prose."""
    data = value if isinstance(value, dict) else {}
    if isinstance(data.get("_total"), dict):
        data = data["_total"]
    parts = [
        f"{_report_label(key)} {_format_report_number(count)} 条"
        for key, count in data.items()
        if isinstance(count, (int, float)) and not isinstance(count, bool)
    ]
    return "；".join(parts) if parts else "暂无可用数据"


def _markdown_text(value: object, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.replace("[", "【").replace("]", "】")
    return text[:limit] + ("…" if len(text) > limit else "")


def _format_hot_content(value: object, *, limit_per_group: int = 3) -> list[str]:
    """Keep only evidence fields suitable for the report; never expose raw rows."""
    if isinstance(value, list):
        groups = [("热门内容", value)]
    elif isinstance(value, dict):
        groups = [
            (_report_label(group), rows)
            for group, rows in value.items()
            if isinstance(rows, list)
        ]
    else:
        groups = []
    lines: list[str] = []
    for group, rows in groups:
        for row in rows[:limit_per_group]:
            if not isinstance(row, dict):
                continue
            title = _markdown_text(row.get("title") or row.get("content"), 70)
            if not title:
                title = "未命名内容"
            url = str(row.get("url") or "").strip()
            title_text = f"[{title}]({url})" if url.startswith("https://") else title
            details = []
            if row.get("author"):
                details.append(f"作者：{_markdown_text(row.get('author'), 30)}")
            if row.get("interaction_cnt") is not None:
                details.append(
                    f"互动量：{_format_report_number(row.get('interaction_cnt'))}"
                )
            suffix = f"（{'；'.join(details)}）" if details else ""
            lines.append(f"- {group}：{title_text}{suffix}")
    return lines or ["- 暂无可展示的热门内容证据。"]


def _fallback_report_md(stats, charts, wc_insight, chapters, version) -> str:
    """确定性降级报告：用真数据渲染五章骨架 MD，供下游质检/交付消费。

    嵌入 n14 产出的图表/封面（chart_urls={charts:[...], cover:{local_path}}）：封面置顶，
    各活动章节下嵌入该活动的情感/平台/趋势/内容图，词云派生图置综合定位章。图片以本地
    PNG 路径嵌入（`![](local_path)`），由 feishu.md_to_blocks 上传 media 进飞书文档；
    local_path 为空（降级无图）则跳过，不塞死链。
    """
    camps = (stats or {}).get("campaigns") or {}
    chart_items = (charts or {}).get("charts") or []
    cover = (charts or {}).get("cover") or {}

    def _img_src(c) -> str:
        """图片引用源：优先本地 PNG 路径（进飞书 media），回退 url（浏览器预览兼容）。"""
        return c.get("local_path") or c.get("url") or ""

    # 按 chart_id 前缀归类：{cid}_xxx 归该活动，wordcloud_xxx 归派生区。
    by_cid: dict[str, list] = {}
    derived: list = []
    for c in chart_items:
        cid_key = str(c.get("chart_id") or "")
        if cid_key.startswith("wordcloud_"):
            derived.append(c)
            continue
        owner = cid_key.rsplit("_", 1)[0] if "_" in cid_key else ""
        by_cid.setdefault(owner, []).append(c)

    lines = [f"# 营销活动复盘报告（v{version}）", ""]
    if _img_src(cover):
        lines += [f"![封面]({_img_src(cover)})", ""]
    lines += [f"> 结构：{chapters} · 指标口径：{'/'.join(METRIC_WHITELIST)}",
              "> 本报告由确定性模板生成，数字均取自统计产物。", ""]
    data_notes = [
        f"- 活动数：{len(camps)}（多活动逐个处理，不相加为合计声量）",
        "- 日期口径：发表时间；情感以 AI 标注为准。",
    ]
    sampling = ((stats or {}).get("clean_record") or {}).get("sampling") or {}
    if sampling.get("applied"):
        data_notes.append(
            "- " + (
                sampling.get("disclosure")
                or (
                    f"demo 模式从 {sampling.get('population_size')} 条清洗后活动明细中"
                    f"抽样 {sampling.get('sample_size')} 条，仅供参考，不代表全量结论。"
                )
            )
        )
    lines += ["## 00 篇头 · 数据说明", "", *data_notes, ""]
    lines += ["## 整体洞察", "",
              "以下结论仅基于已确认统计产物与证据链。", ""]
    brand = (stats or {}).get("brand") or {}
    lines += ["## 01 品牌传播表现", "",
              "### 1.1 传播概况",
              f"- 品牌声量：{brand.get('total_volume')}",
              f"- 品牌互动量：{brand.get('total_interaction')}", "",
              "### 1.2 口碑分析",
              f"- 情感分布：{_format_distribution(brand.get('sentiment_dist'))}", "",
              "### 1.3 内容分析",
              f"- 平台分布：{_format_distribution(brand.get('platform_dist'))}",
              f"- 内容类型：{_format_distribution(brand.get('content_type_dist'))}", "",
              "### 1.4 热门内容"]
    lines.extend(_format_hot_content(brand.get("hot_content")))
    lines.append("")
    lines += ["## 02 活动传播表现", ""]
    for index, (cid, s) in enumerate(camps.items(), 1):
        m = (s or {}).get("metrics", {})
        lines.append(f"### 2.{index} 活动 {cid}")
        lines.append(f"#### 2.{index}.1 传播概况")
        lines.append(f"- 活动相关条数：{m.get('活动相关条数')}")
        lines.append(f"- 条均互动量：{m.get('条均互动量')}")
        lines.append(f"#### 2.{index}.2 活动声量渗透分析")
        lines.append(f"- 品牌提及率：{m.get('品牌提及率')}")
        lines.append(f"- 产品提及占比：{m.get('产品提及占比')}")
        lines.append(f"#### 2.{index}.3 口碑分析")
        lines.append(f"- NSR：{m.get('NSR')}")
        lines.append(f"#### 2.{index}.4 内容分析")
        lines.append(
            f"- 内容类型：{_format_distribution((s or {}).get('content_type_dist'))}"
        )
        lines.append(f"#### 2.{index}.5 热门内容")
        lines.extend(_format_hot_content((s or {}).get("top_content")))
        lines.append("")
        imgs = [c for c in by_cid.get(cid, []) if _img_src(c)]
        for c in imgs:
            lines.append(f"![{c.get('title')}]({_img_src(c)})")
        if imgs:
            lines.append("")
    lines += ["## 03 综合表现定位", "", "### 3.1 活动期与基准期对比", ""]
    baseline = (stats or {}).get("baseline_lift") or {}
    if baseline.get("status") == "available":
        lines.append(
            "- 活动期间品牌声量与前置基准期相比的变化："
            f"{_format_report_number(baseline.get('volume_change'))}。"
        )
    else:
        lines.append("- 基准期数据不可用，本节不输出确定性比较结论。")
    lines.append("")
    derived_imgs = [c for c in derived if _img_src(c)]
    for c in derived_imgs:
        lines.append(f"![{c.get('title')}]({_img_src(c)})")
    if derived_imgs:
        lines.append("")
    lines += [
        "## 04 经验总结", "",
        "### 4.1 成功经验", "",
        "- 证据不足，暂不形成确定性成功经验；后续结论需回指来源章节与数值。", "",
        "### 4.2 待改进问题", "",
        "- 证据不足，暂不形成确定性问题归因。", "",
        "### 4.3 行动计划", "",
        "- 补齐证据后再制定可验证的行动计划。", "",
    ]
    lines += ["## 05 总结与建议", "", "（事实/机制/建议分开，不越过数据边界。）", ""]
    return "\n".join(lines)


# ── decision 节点 ───────────────────────────────────────────
@handler("qc_decision")
def qc_decision(node, state, ctx):
    qc = state.read_artifact("qc_report") or {}
    max_rounds = state.params.get("qc_max_rounds", 3)
    rounds = state.counters.get("qc_rounds", 0)
    if not qc.get("has_p0"):
        return {"route": "no_p0"}
    if rounds >= max_rounds:
        return {"route": "has_p0_maxed"}
    return {"route": "has_p0_retry"}


# ── v0.2 新增：方案 / Run 初始化 / 交付 draft·commit / 终验路由 ──────────
@handler("plan_compile")
def plan_compile(node, state, ctx):
    """Compile the complete G2 review contract from the confirmed requirement."""
    requirement = state.read_artifact("requirement_confirmed") or {}
    if not isinstance(requirement, dict) or not requirement.get("confirmed"):
        raise ValueError("plan_compile: requirement_confirmed 尚未确认")

    def stable_hash(value: Any) -> str:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    activity_name = str(requirement.get("activity_name") or "营销活动")
    campaigns = requirement.get("campaigns") or []
    activity_names = [
        str(item.get("id"))
        for item in campaigns
        if isinstance(item, dict) and item.get("id")
    ] or [activity_name]
    platforms = [str(item) for item in (requirement.get("platforms") or [])]
    content_scope = str(requirement.get("content_scope") or "all")
    source_file = requirement.get("source_file") or {}
    plan_revision = int(
        (state.artifacts.get("analysis_plan") or {}).get("version", 0)
    ) + 1
    previous_confirmation = state.read_artifact("confirmed_plan") or {}
    revision_request = (
        previous_confirmation.get("note")
        if isinstance(previous_confirmation, dict)
        and previous_confirmation.get("confirmed") is False
        else ""
    )

    core_modules = [
        "brand_context",
        "activity_performance",
        "evidence_summary",
    ]
    conditional_modules = [
        "baseline_lift",
        "sentiment_opinion",
        "wordcloud_topics",
        "top_content",
        "kol_effectiveness",
    ]
    reasons = {
        "baseline_lift": "对比活动期与前置基准期，判断相对变化",
        "sentiment_opinion": "输出情感结构、观点标签与讨论维度",
        "wordcloud_topics": "按平台提炼高频议题，不跨平台合并",
        "top_content": "保留代表性内容作为结论证据",
        "kol_effectiveness": "按可用粉丝量派生达人层级，缺失时不强行出图",
    }
    if requirement.get("product_query"):
        conditional_modules.append("product_funnel")
        reasons["product_funnel"] = "需求提供了产品关键词，可计算品牌提及内的产品提及占比"
    if content_scope in ("all", "ugc"):
        conditional_modules.append("ugc_participation")
        reasons["ugc_participation"] = "范围包含 UGC，需要识别参与类型与内容贡献"
    if len(activity_names) > 1:
        conditional_modules.append("multi_activity_compare")
        reasons["multi_activity_compare"] = "包含多个活动，逐活动计算并横向比较，不合并相加"
    enabled_modules = core_modules + conditional_modules

    module_registry = {
        "brand_context": {
            "inputs": ["confirmed_plan.brand_query", "period", "platforms"],
            "data_source": "controlled-stat-tool",
            "route": "SSJH-Social-Stat",
            "outputs": ["brand_stats"],
            "failure_strategy": "fail-closed; degraded artifacts cannot enter the report chain",
        },
        "activity_performance": {
            "inputs": ["user-uploaded activity_detail XLSX"],
            "data_source": "user-uploaded-excel",
            "route": "excel_source + deterministic cleaning/aggregation",
            "outputs": ["cleaned_data", "stats_bundle"],
            "failure_strategy": "schema/date/range errors stop the affected run",
        },
        "evidence_summary": {
            "inputs": ["stats_bundle", "chart_urls", "wordcloud_insight"],
            "data_source": "versioned pipeline artifacts",
            "route": "Narrative handler",
            "outputs": ["report_md"],
            "failure_strategy": "missing evidence blocks report assembly",
        },
        "baseline_lift": {
            "inputs": ["brand_query", "30-day non-overlapping baseline"],
            "data_source": "controlled-stat-tool",
            "route": "SSJH-Social-Stat",
            "outputs": ["brand_stats_baseline"],
            "failure_strategy": "create DataIssue and suppress unsupported comparison",
        },
        "sentiment_opinion": {
            "inputs": ["cleaned_data", "confirmed label set"],
            "data_source": "activity_detail rows",
            "route": "small Ark engine or DataHub by threshold",
            "outputs": ["annotated_round1", "annotated_data"],
            "failure_strategy": "coverage ERROR stops; no silent AI sentiment fallback in full",
        },
        "wordcloud_topics": {
            "inputs": ["queries", "period", "platforms"],
            "data_source": "controlled-stat-tool",
            "route": "SSJH-Social-Stat + Insight handler",
            "outputs": ["wordcloud_data", "wordcloud_insight"],
            "failure_strategy": "missing platform is explicit; never merge other platforms",
        },
        "top_content": {
            "inputs": ["activity rows with URL/content/interaction"],
            "data_source": "activity_detail rows",
            "route": "deterministic ranking",
            "outputs": ["Top PGC/UGC evidence"],
            "failure_strategy": "missing fields are disclosed; no fabricated rows",
        },
        "kol_effectiveness": {
            "inputs": ["platform", "fans", "content type", "interaction"],
            "data_source": "activity_detail rows",
            "route": "deterministic platform-aware classification",
            "outputs": ["KOL tier metrics"],
            "failure_strategy": "omit module with explicit reason when fan data unavailable",
        },
        "product_funnel": {
            "inputs": ["brand_words", "product_words", "activity-related rows"],
            "data_source": "activity_detail rows",
            "route": "deterministic nested-set aggregation",
            "outputs": ["brand mention rate", "product mention share"],
            "failure_strategy": "enforce product subset of brand; zero denominator yields N/A",
        },
        "ugc_participation": {
            "inputs": ["activity-related UGC rows"],
            "data_source": "activity_detail rows",
            "route": "Discovery handler + confirmed categories",
            "outputs": ["ugc_category_list", "UGC distribution"],
            "failure_strategy": "PGC-only is explicit N/A; label changes invalidate downstream",
        },
        "multi_activity_compare": {
            "inputs": ["rows partitioned by confirmed activity"],
            "data_source": "activity_detail rows",
            "route": "deterministic per-activity aggregation",
            "outputs": ["per-activity metrics and comparison"],
            "failure_strategy": "never sum overlapping activities into one total",
        },
    }
    unknown_modules = [name for name in enabled_modules if name not in module_registry]
    questions = requirement.get("business_questions") or []
    if isinstance(questions, str):
        questions = [questions]
    objective_capability_map = []
    unsupported_objectives = []
    capability_keywords = {
        "activity_performance": (
            "活动表现", "表现", "传播", "声量", "互动", "提及", "效果", "口碑", "NSR",
        ),
        "sentiment_opinion": ("情感", "观点", "评价", "讨论"),
        "wordcloud_topics": ("内容", "话题", "议题", "关键词", "热点"),
        "top_content": ("热门", "高互动", "代表内容"),
        "kol_effectiveness": ("达人", "KOL", "博主"),
        "ugc_participation": ("UGC", "用户参与", "参与方式"),
        "evidence_summary": ("经验", "改进", "建议", "总结", "策略", "复盘"),
        "product_funnel": ("产品", "品牌提及"),
        "multi_activity_compare": ("多活动", "对比", "比较"),
    }
    unsupported_terms = (
        "销售转化", "转化率", "营销 ROI", "ROI", "GMV", "营收", "销量",
        "购买率", "成交",
    )
    for question in questions:
        text = str(question).strip()
        matched = [
            module for module, keywords in capability_keywords.items()
            if any(keyword.lower() in text.lower() for keyword in keywords)
            and module in enabled_modules
        ]
        reason = None
        if any(term.lower() in text.lower() for term in unsupported_terms):
            matched = []
            reason = "缺少销售/交易数据源和已登记转化或 ROI 模块"
        elif not matched:
            reason = "无法映射到当前已登记模块"
        objective_capability_map.append({
            "objective": text,
            "modules": matched,
            "supported": bool(matched),
            **({"reason": reason} if reason else {}),
        })
        if not matched:
            unsupported_objectives.append(text)
    delivery_supported = any(
        marker in str(requirement.get("delivery_expectation") or "")
        for marker in ("飞书", "报告", "复盘")
    )
    actions_supported = (
        "已登记" in str(requirement.get("allowed_actions") or "")
        or str(requirement.get("allowed_actions") or "").strip()
        in ("分析与生成报告", "只读分析")
    )

    required_fields = (
        "brand", "activity_name", "period_start", "period_end", "platforms",
        "brand_query", "activity_query", "content_scope", "business_questions",
        "delivery_expectation", "allowed_actions",
    )
    missing_fields = [key for key in required_fields if not requirement.get(key)]
    source_declared = bool(
        isinstance(source_file, dict)
        and source_file.get("name")
        and source_file.get("mount_path")
    )
    preflight = {
        "status": (
            ("READY" if source_declared else "READY_PENDING_INPUT")
            if (
                not missing_fields and not unknown_modules
                and not unsupported_objectives
                and delivery_supported and actions_supported
            ) else (
                "NEEDS_CAPABILITY_EXTENSION"
                if unsupported_objectives or not delivery_supported or not actions_supported
                else "BLOCKED"
            )
        ),
        "checks": [
            {"name": "required_requirement_fields", "ok": not missing_fields,
             "detail": missing_fields},
            {"name": "activity_detail_reference", "ok": source_declared,
             "deferred": not source_declared,
             "detail": source_file.get("name") if isinstance(source_file, dict) else None},
            {"name": "registered_modules_only", "ok": not unknown_modules,
             "detail": unknown_modules},
            {"name": "tool_routes_declared", "ok": all(
                module_registry[name].get("route") for name in enabled_modules
                if name in module_registry
            ), "detail": enabled_modules},
            {"name": "objectives_mapped_to_registered_modules",
             "ok": not unsupported_objectives,
             "detail": objective_capability_map},
            {"name": "delivery_supported", "ok": delivery_supported,
             "detail": requirement.get("delivery_expectation")},
            {"name": "allowed_actions_supported", "ok": actions_supported,
             "detail": requirement.get("allowed_actions")},
        ],
    }

    analysis_plan = {
        "schema_version": "1.0",
        "revision": plan_revision,
        "title": f"{activity_name}营销活动复盘分析方案",
        "scope": {
            "brand": requirement.get("brand"),
            "activities": activity_names,
            "period_start": requirement.get("period_start"),
            "period_end": requirement.get("period_end"),
            "platforms": platforms,
            "content_scope": content_scope,
            "source_file_name": (
                source_file.get("name") if isinstance(source_file, dict) else None
            ),
        },
        "objectives": list(requirement.get("business_questions") or []),
        "objective_capability_map": objective_capability_map,
        "delivery_expectation": requirement.get("delivery_expectation"),
        "allowed_actions": requirement.get("allowed_actions"),
        "metrics": [
            {"name": "条均互动量", "definition": "活动相关内容互动量之和 / 活动相关内容数"},
            {"name": "品牌提及率", "definition": "品牌提及内容数 / 活动相关内容数"},
            {"name": "产品提及占比", "definition": "产品提及内容数 / 品牌提及内容数"},
            {
                "name": "NSR",
                "definition": "(正面内容数 - 负面内容数) / 正中负内容总数",
                "neutral_in_denominator": bool(
                    state.params.get("nsr_denominator_includes_neutral", True)
                ),
            },
        ],
        "dimensions": ["平台", "发表日期", "内容类型", "KOL 层级", "UGC 类别", "观点标签"],
        "comparisons": [
            "活动期与前置 30 天不重叠基准期对比",
            "多活动逐个计算和比较，不汇总相加",
        ],
        "report_structure": [
            "品牌传播表现",
            "活动传播表现",
            "综合表现定位",
            "经验总结",
            "总结与建议",
        ],
        "data_requirements": [
            {
                "name": "activity_detail",
                "authority": "user-uploaded-excel",
                "required_fields": [
                    "平台", "发表时间", "内容类型", "标题/正文", "互动量",
                    "活动分析对象", "URL",
                ],
                "reference": {
                    "file_name": source_file.get("name"),
                    "mount_path": source_file.get("mount_path"),
                } if isinstance(source_file, dict) else {},
                "validation": "XLSX container, schema, period, grouping and row reconciliation",
            },
            {
                "name": "brand_period_and_baseline",
                "authority": "controlled-stat-tool",
                "parameters": ["brand_query", "brand_filter", "period", "platforms"],
            },
            {
                "name": "wordcloud",
                "authority": "controlled-stat-tool",
                "parameters": ["activity_query", "product_query", "period", "platforms"],
            },
        ],
        "chart_plan": [
            {"section": "品牌传播表现", "chart": "声量与互动趋势", "type": "dual-axis line",
             "condition": "daily series available"},
            {"section": "品牌传播表现", "chart": "平台声量分布", "type": "donut",
             "condition": "more than one platform"},
            {"section": "品牌传播表现", "chart": "PGC/UGC 结构", "type": "donut",
             "condition": "content_scope=all"},
            {"section": "口碑分析", "chart": "情感分布", "type": "donut",
             "condition": "AI sentiment coverage complete"},
            {"section": "活动声量渗透", "chart": "品牌/产品提及漏斗", "type": "bar/funnel",
             "condition": "product query selects three-level funnel"},
            {"section": "内容分析", "chart": "逐平台词云/高频议题", "type": "wordcloud/rank",
             "condition": "platform data available"},
        ],
        "evidence_requirements": [
            "每个数字可回指输入 Artifact、口径版本和分母",
            "观点与建议必须关联代表内容或统计证据",
            "报告、图表和表格引用同一 stats_bundle 版本",
        ],
        "guardrails": [
            "活动层指标仅统计活动相关内容",
            "产品提及是品牌提及的子集，产品提及占比不超过 1",
            "指标仅使用条均互动量、品牌提及率、产品提及占比、NSR",
            "禁用人均、转化率、转化、性价比等无数据支撑表述",
            "竞品分析已下线，不生成竞品章节",
        ],
        "revision_request": revision_request or None,
    }
    execution_plan = {
        "schema_version": "1.0",
        "revision": plan_revision,
        "stages": [
            {"name": "数据准备", "work": "校验上传明细，拉取品牌活动期、基准期与词云数据"},
            {"name": "清洗标注", "work": "按发表时间过滤去重，完成相关性、情感、观点和 UGC 标注"},
            {"name": "统计洞察", "work": "按活动和平台计算白名单指标，生成图表与议题洞察"},
            {"name": "撰写质检", "work": "生成五部分报告，经独立 Validator 扫描并处理 P0 问题"},
            {"name": "发布交付", "work": "发布飞书文档，回读校验版本与内容后提交终验"},
        ],
        "human_gates": [
            {"gate": "G2", "purpose": "确认本分析与执行方案"},
            {"gate": "N10", "purpose": "确认观点候选标签"},
            {"gate": "G3", "purpose": "确认报告质检结果"},
            {"gate": "G4", "purpose": "确认最终交付文档"},
        ],
        "modules": [
            {"module": name, **module_registry[name]}
            for name in enabled_modules if name in module_registry
        ],
        "concurrency": [
            ["brand_period_pull", "brand_baseline_pull", "wordcloud_pull"],
            ["ugc_discovery", "round1_annotation"],
            ["chart_render", "wordcloud_insight"],
        ],
        "retry_and_timeout": {
            "external_unknown": "reconcile-before-retry",
            "side_effects": "idempotency key required",
            "g4": "2 business days reminder, then 1 business day escalation",
        },
        "preflight": preflight,
        "delivery": requirement.get("delivery_expectation"),
    }
    selected_modules = {
        "core": core_modules,
        "conditional": conditional_modules,
        "core_plus_conditional": core_modules + conditional_modules,
        "reasons": reasons,
        "excluded": {
            "competitor_sov": "竞品分析已按当前业务口径下线",
        },
        "preflight": preflight,
    }
    plan_bundle = {
        "analysis_plan": analysis_plan,
        "execution_plan": execution_plan,
        "selected_modules": selected_modules,
    }
    bundle_hash = stable_hash(plan_bundle)
    for payload in (analysis_plan, execution_plan, selected_modules):
        payload["plan_bundle_sha256"] = bundle_hash
    return {"artifacts": {
        "analysis_plan": analysis_plan,
        "execution_plan": execution_plan,
        "selected_modules": selected_modules,
    }}


@handler("run_init")
def run_init(node, state, ctx):
    plan = state.read_artifact("confirmed_plan") or {}
    if not isinstance(plan, dict) or not plan.get("confirmed"):
        raise ValueError("run_init: G2 ConfirmedPlan 未通过")
    for key in ("analysis_plan", "execution_plan", "selected_modules"):
        if (plan.get("subject") or {}).get(key) != state.artifact_ref(key):
            raise ValueError(f"run_init: ConfirmedPlan 引用的 {key} 已失效")

    source = plan.get("source_file") or {}
    mount_path = str(source.get("mount_path") or "")
    resolved_upload = excel_source.resolve_activity_xlsx(plan)
    source_path = next(
        (
            candidate for candidate in (
                resolved_upload,
                Path(mount_path),
                Path("/workspace") / mount_path,
                state.run_dir / mount_path,
            )
            if candidate is not None and candidate.is_file()
        ),
        None,
    )
    if source_path:
        activity_checksum = hashlib.sha256(source_path.read_bytes()).hexdigest()
    else:
        raise ValueError("run_init: 无法读取 G1 挂载的活动明细，不能冻结源数据哈希")

    if source_path:
        input_data = excel_source.load_activity_rows(plan)
        rows = (input_data or {}).get("rows") or []
        input_schema_validation = {
            "status": "PASSED",
            "row_count": len(rows),
            "platforms": (input_data or {}).get("meta", {}).get("platforms") or [],
            "campaigns": (input_data or {}).get("meta", {}).get("campaigns") or [],
            "source_row_count": (
                (input_data or {}).get("meta", {}).get("source_row_count")
            ),
            "accepted_row_count": (
                (input_data or {}).get("meta", {}).get("accepted_row_count")
            ),
            "period_start": (input_data or {}).get("meta", {}).get("period_start"),
            "period_end": (input_data or {}).get("meta", {}).get("period_end"),
            "source_file": source.get("name"),
            "sha256": activity_checksum,
        }
    def resource_fingerprint(path: Path) -> str:
        if not path.is_file():
            return "unavailable"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def combined_fingerprint(paths: list[Path]) -> str:
        values = [resource_fingerprint(path) for path in paths]
        if any(value == "unavailable" for value in values):
            return "unavailable"
        return hashlib.sha256(":".join(values).encode("ascii")).hexdigest()

    skills_root = Path(__file__).resolve().parent.parent
    package_root = Path(__file__).resolve().parent
    runtime_resources = package_root / "runtime_resources"
    default_resource_versions = {
        "dag-contract": resource_fingerprint(
            package_root / "contracts" / "pipeline.v0.2.yaml"
        ),
        "rules-implementation": combined_fingerprint([
            Path(__file__),
            Path(__file__).resolve().parent / "acceptance.py",
        ]),
        "coordinator-agent": combined_fingerprint([
            runtime_resources / "agents" / "coordinator.json",
            runtime_resources / "agents" / "coordinator.system.md",
        ]),
        "validator-agent": combined_fingerprint([
            runtime_resources / "agents" / "validator.json",
            runtime_resources / "agents" / "validator.system.md",
        ]),
        "environment": resource_fingerprint(
            runtime_resources / "environment.json"
        ),
        "pipeline-orchestrator": resource_fingerprint(Path(__file__)),
        "topic5-validator": combined_fingerprint([
            skills_root / "topic5-validator" / "SKILL.md",
            skills_root / "topic5-validator" / "scripts" / "scan_report.py",
        ]),
        "SSJH-Social-Stat": resource_fingerprint(
            next((
                candidate for candidate in (
                    skills_root / "ssjh-social-stat" / "skill.py",
                    skills_root / "SSJH-Social-Stat" / "skill.py",
                ) if candidate.is_file()
            ), skills_root / "ssjh-social-stat" / "skill.py")
        ),
        "blueai-datahub-skill": resource_fingerprint(
            skills_root / "blueai-datahub-skill" / "sdk" / "client.py"
        ),
        "blueai-yuqing-echarts-skill-new": resource_fingerprint(
            skills_root / "blueai-yuqing-echarts-skill-new"
            / "scripts" / "render_from_spec.js"
        ),
        "report-cover-skill": resource_fingerprint(
            skills_root / "report-cover-skill" / "scripts" / "generate_cover_gpt.py"
        ),
        "feishu-delivery-tool": resource_fingerprint(
            Path(__file__).resolve().parent / "feishu.py"
        ),
        "semantic-tool": resource_fingerprint(
            Path(__file__).resolve().parent / "semantic.py"
        ),
        "annotation-tool": resource_fingerprint(
            Path(__file__).resolve().parent / "annotate.py"
        ),
        "model": (
            os.environ.get("TOPIC5_MODEL_ID")
            or os.environ.get("ARK_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "configured-at-agent"
        ),
    }
    resource_versions = ctx.get("resource_versions") or {
        **default_resource_versions,
    }
    required_resource_keys = set(default_resource_versions)
    missing_resource_keys = sorted(required_resource_keys - set(resource_versions))
    unavailable_resources = sorted(
        key for key, value in resource_versions.items()
        if value in ("", "unavailable", "configured-at-agent")
    )
    if missing_resource_keys or unavailable_resources:
        raise ValueError("run_init: 无法冻结全部实际资源版本")

    lock_payload = {
        "confirmed_plan": {
            "version": plan.get("version"),
            "sha256": plan.get("plan_bundle_sha256"),
            "subject": plan.get("subject"),
        },
        "activity_detail_checksum": activity_checksum,
        "dag": f"{state.dag_slug}@{state.dag_version}",
        "input_schema_validation": input_schema_validation,
        "resources": resource_versions,
        "run_mode": state.params.get("run_mode", "full"),
    }
    lock = "lock_" + hashlib.sha256(
        json.dumps(
            lock_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:16]
    state.params["resource_lock"] = lock
    state.params["activity_detail_checksum"] = activity_checksum
    return {"artifacts": {
        "run_record": {
            "run_id": state.run_id,
            "task_id": state.task_id,
            "confirmed_plan_ref": state.artifact_ref("confirmed_plan"),
            "resource_lock": lock,
            "status": "initialized",
        },
        "resource_lock": {**lock_payload, "lock": lock, "frozen": True},
        "context_snapshot": {
            "confirmed_plan": plan,
            "confirmed_plan_ref": state.artifact_ref("confirmed_plan"),
            "params": dict(state.params),
        },
        "input_schema_validation": input_schema_validation,
    }}


@handler("delivery_draft")
def delivery_draft(node, state, ctx):
    """交付草稿（n20_delivery_draft，G4）：内容层 MD → 飞书文档 draft + 外部回执。

    真路径（feishu.create_delivery）：建 docx → 写 block → 转移所有权 →
    导出后回读核对（章节齐全/图引用生效/绑定报告版本一致）→ 记录 revision_id。
    无凭据/失败降级到确定性占位（feishu.fallback，degraded=True，schema 与真路径一致）。

    幂等（idempotency=feishu:{task_id}:{resource_lock}:{report_version} 由引擎处理）：
    本层命中已有回执（external_receipts.doc_token）则复用、不新建文档；side_effect=feishu-doc-create、
    retry max2 on_fail=pause 由引擎统一处理，本 handler 不因缺凭据抛异常（避免 pause）。
    """
    out = node.get("outputs", [])
    report = state.read_artifact("report_md") or {}
    report_md = report.get("markdown") if isinstance(report, dict) else ""
    report_version = str(report.get("version") if isinstance(report, dict) else "") or "0.0"
    plan = state.read_artifact("confirmed_plan") or {}
    brand = str(plan.get("brand") or plan.get("project") or "立白")
    title = str(plan.get("report_name") or f"{brand}营销活动复盘报告 v{report_version}")
    task_id = state.task_id

    # 幂等复用：命中已存在回执则复用 doc_token，不新建。
    prev_receipts = state.read_artifact("external_receipts") or {}
    reused = (prev_receipts.get("doc_token")
              if isinstance(prev_receipts, dict) else "") or ""

    if feishu.api_available():
        try:
            feishu_doc = feishu.create_delivery(
                report_md, title=title, task_id=task_id,
                report_version=report_version, reused_doc_token=reused)
        except Exception:
            raise
    else:
        raise RuntimeError("delivery_draft: 飞书交付凭据不可用")

    readback = feishu_doc.get("readback") or {}
    ownership_transferred = bool(feishu_doc.get("ownership_transferred"))
    post_publish_issues = []
    if not readback.get("chapters_complete"):
        post_publish_issues.append({
            "id": "P0-postpublish-chapters",
            "severity": "P0",
            "title": "飞书文档回读缺少章节",
            "evidence": str(readback.get("missing_chapters") or []),
            "locus": "feishu-docx",
        })
    if not readback.get("images_referenced"):
        post_publish_issues.append({
            "id": "P0-postpublish-images",
            "severity": "P0",
            "title": "飞书文档图片块不完整",
            "evidence": (
                f"expected={readback.get('expected_images')}, "
                f"got={readback.get('got_images')}"
            ),
            "locus": "feishu-docx",
        })
    if not ownership_transferred:
        post_publish_issues.append({
            "id": "P0-postpublish-owner",
            "severity": "P0",
            "title": "飞书文档未完成所有权转移",
            "evidence": "ownership_transferred=false",
            "locus": "feishu-permission",
        })
    post_publish_validation = {
        "validator": "topic5-validator",
        "rules_version": "topic5-validator-qc-v1.6",
        "scope": "post_publish",
        "issues": post_publish_issues,
        "p0_count": len(post_publish_issues),
        "has_p0": bool(post_publish_issues),
        "manual_checks": [
            "5.2 传播概况 grid", "5.3 词云 grid", "5.4 口碑观点 grid",
            "5.6 callout 类型与视觉",
        ],
    }
    # 发布后机器检查通过才进入 G4；原生 grid/callout 视觉仍由用户终验。
    draft_state = (
        "ready"
        if readback.get("ok") and ownership_transferred
        else "needs_review"
    )
    delivery_draft_art = {
        "state": draft_state,
        "report_version": report_version,
        "doc_token": feishu_doc.get("doc_token"),
        "url": feishu_doc.get("url"),
        "revision_id": feishu_doc.get("revision_id"),
        "report_ref": state.artifact_ref("report_md"),
        "ownership_transferred": ownership_transferred,
        "readback": readback,
        "post_publish_validation": post_publish_validation,
        "degraded": bool(feishu_doc.get("degraded")),
    }
    external_receipts = {
        "doc_token": feishu_doc.get("doc_token"),
        "url": feishu_doc.get("url"),
        "revision_id": feishu_doc.get("revision_id"),
        "report_version": report_version,
        "ownership_transferred": bool(feishu_doc.get("ownership_transferred")),
        "side_effect": "feishu-doc-create",
        "degraded": bool(feishu_doc.get("degraded")),
    }
    base = {
        "delivery_draft": delivery_draft_art,
        "external_receipts": external_receipts,
        "feishu_doc": feishu_doc,
    }
    return {"artifacts": {k: base.get(k, _stub_payload(node, "delivery")) for k in out}}


@handler("delivery_commit")
def delivery_commit(node, state, ctx):
    """交付提交（n23_delivery_commit，发布后检查）。

    control_plane=blueflare-ada 未上线：本地 stub 执行，但产出结构对齐真实契约——
    绑定交付文档 revision_id 与 report_version（版本链对齐，指向同一 revision 而非 latest），
    发布后检查涵盖图片/布局/所有权/链接/版本一致。上线后只换 handler 不改 schema。
    side_effect=delivery-commit、idempotency=commit:{task_id}:{resource_lock} 由引擎处理。
    """
    feishu_doc = state.read_artifact("feishu_doc") or {}
    receipts = state.read_artifact("external_receipts") or {}
    user_conf = state.read_artifact("user_confirmation") or {}
    readback = (feishu_doc.get("readback") if isinstance(feishu_doc, dict) else {}) or {}
    revision_id = (feishu_doc.get("revision_id") if isinstance(feishu_doc, dict) else None) \
        or (receipts.get("revision_id") if isinstance(receipts, dict) else None)
    report_version = (feishu_doc.get("report_version") if isinstance(feishu_doc, dict) else None) \
        or (receipts.get("report_version") if isinstance(receipts, dict) else None)
    checks_ok = bool(readback.get("ok", True))
    user_confirmed = bool(
        isinstance(user_conf, dict) and user_conf.get("status") == "confirmed"
    )
    current_report_ref = state.artifact_ref("report_md")
    current_delivery_ref = state.artifact_ref("delivery_draft")
    url = str((feishu_doc or {}).get("url") or (receipts or {}).get("url") or "")
    link_accessible = bool(
        url.startswith("https://")
        and "/docx/" in url
        and readback.get("ok")
    )
    ownership_transferred = bool((feishu_doc or {}).get("ownership_transferred"))
    acceptance_aligned = bool(
        user_confirmed
        and user_conf.get("report_version") == report_version
        and user_conf.get("revision_id") == revision_id
        and user_conf.get("report_ref") == current_report_ref
        and user_conf.get("delivery_ref") == current_delivery_ref
    )
    if (
        not checks_ok
        or not acceptance_aligned
        or not revision_id
        or not ownership_transferred
        or not link_accessible
        or bool((feishu_doc or {}).get("degraded"))
    ):
        raise ValueError(
            "delivery_commit: 发布后检查、用户终验绑定、revision、所有权、链接"
            "或真实交付状态不满足"
        )
    delivery_record = {
        "committed": (
            checks_ok and acceptance_aligned and bool(revision_id)
            and ownership_transferred and link_accessible
            and not bool((feishu_doc or {}).get("degraded"))
        ),
        "post_publish_checks": "passed" if checks_ok else "needs_review",
        "doc_token": (feishu_doc.get("doc_token") if isinstance(feishu_doc, dict) else None),
        "url": url,
        "revision_id": revision_id,
        "report_version": report_version,
        "report_ref": current_report_ref,
        "delivery_ref": current_delivery_ref,
        "validation_ref": state.artifact_ref("qc_report"),
        "user_acceptance_ref": state.artifact_ref("user_confirmation"),
        "ownership_transferred": ownership_transferred,
        "link_accessible": link_accessible,
        "acceptance_aligned": acceptance_aligned,
        "user_confirmed": user_confirmed,
        "degraded": bool((feishu_doc or {}).get("degraded")),
        "control_plane": node.get("control_plane"),
    }
    return {"artifacts": {
        "delivery_record": delivery_record,
        "task_result": {"status": "delivered", "report_version": report_version,
                        "revision_id": revision_id,
                        "doc_token": delivery_record["doc_token"]},
    }}


@handler("user_decision")
def user_decision(node, state, ctx):
    uc = state.read_artifact("user_confirmation") or {}
    status = uc.get("status", ctx.get("user_decision"))
    if status not in node["routes"]:
        raise ValueError(f"user_decision: 非法或缺失的用户终验状态 {status!r}")
    return {"route": status}


# ── human 节点 ──────────────────────────────────────────────
@handler("human_gate")
def human_gate(node, state, ctx):
    raise HumanPause(prompt=node.get("name", node["id"]), expect=node.get("outputs", []))


@handler("validator_gate")
def validator_gate(node, state, ctx):
    """Pause for the independent Validator Agent, never for end-user review."""
    raise HumanPause(
        prompt=node.get("name", node["id"]),
        expect=node.get("outputs", []),
    )
