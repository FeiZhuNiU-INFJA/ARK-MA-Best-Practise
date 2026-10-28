"""
SSJH-Social-Stat skill
数说聚合平台社媒统计全量取数

version: 1.4.0
"""

import json
import os
import requests
import time
from datetime import datetime, timedelta
from typing import Optional

# ─────────────────────────────────────────────
# 平台映射表
# ─────────────────────────────────────────────
PLATFORM_MAP = {
    "weibo":       {"source": "weibo",       "siteIds": None},
    "weixin":      {"source": "weixin",      "siteIds": None},
    "xiaohongshu": {"source": "xiaohongshu", "siteIds": None},
    "douyin":      {"source": "shortVideo",  "siteIds": "1003583"},
    "kuaishou":    {"source": "shortVideo",  "siteIds": "1334510"},
    "shipinhao":   {"source": "shortVideo",  "siteIds": "25047925"},
    "bilibili":    {"source": "video",       "siteIds": "44"},
    "zhihu":       {"source": "qa",          "siteIds": "101944"},
    "news":        {"source": "news",        "siteIds": None},
    "forum":       {"source": "forum",       "siteIds": None},
    "ecom":        {"source": "ecom",        "siteIds": None},
}

# 各平台不支持的指标（调用会报错，直接跳过返回 None）
PLATFORM_UNSUPPORTED = {
    "news":      {"total_interaction", "interaction_trend", "volume_type", "sentiment_dist"},
    "forum":     {"total_interaction", "interaction_trend", "volume_type", "sentiment_dist"},
    "shipinhao": {"region_dist"},
    "ecom":      {"total_interaction", "interaction_trend", "region_dist", "volume_type", "sentiment_dist"},
}

# 各平台原文排序支持情况（不支持 interactionCntDesc 的降级为 publishTimestampDesc）
PLATFORM_NO_INTERACTION_SORT = {"news", "forum", "ecom"}

# matchFields 策略（各平台固定配置）
MATCH_FIELDS = {
    "weibo":      "content,title,cover_ocr_content",          # 正文+标题+封面OCR
    "weixin":     "content,title",
    "xiaohongshu":"content,title,cover_ocr_content",
    "shortVideo": "content,title,cover_ocr_content",
    "video":      "content,title,cover_ocr_content,video_labels",  # B站加视频标签
    "qa":         "content,title",
    "news":       "content,title",
    "forum":      "content,title",
    "ecom":       "content,title",
}

# postTypes 策略（保证全量）
POST_TYPES = {
    "video":                "video,dynamic,article",   # B站全量
    "qa":                   "questions,articles,pins", # 知乎全量
    "shortVideo_douyin":    "video#0,image#0",         # 抖音仅常规（过滤广告类型）
}

DEFAULT_PLATFORMS = [
    "weibo", "weixin", "xiaohongshu", "douyin", "kuaishou",
    "shipinhao", "bilibili", "zhihu", "news", "forum"
]  # 默认10平台，不含ecom

BASE_URL = "http://api.dc.datastory.com.cn"


class DatashuoshuoSocialmedia:

    def __init__(self, token: str = None):
        if token is None:
            # 凭据来源优先级：显式入参 > 环境变量 SSJH_API_TOKEN > 本地 config.json。
            # 仓内不入库 config.json（含真实 api_token），生产由 MA Vault 注入环境变量。
            token = os.environ.get("SSJH_API_TOKEN")
        if token is None:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                token = cfg.get("api_token")
        if not token:
            raise RuntimeError(
                "SSJH 缺少 api_token：请设置环境变量 SSJH_API_TOKEN 或提供 config.json")
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ─────────────────────────────────────────────
    # 内部工具
    # ─────────────────────────────────────────────

    def _ts(self, date_str: str, end: bool = False) -> str:
        """'2024-05-01' → 13位毫秒时间戳字符串"""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if end:
            dt = dt.replace(hour=23, minute=59, second=59)
        return str(int(dt.timestamp() * 1000))

    def _build_extra(self, plat_key: str, msg_type: str) -> dict:
        """构建单平台的 extra_condition value"""
        info = PLATFORM_MAP[plat_key]
        source = info["source"]
        site_id = info["siteIds"]
        ec = {}
        if site_id:
            ec["siteIds"] = site_id
        ec["matchFields"] = (getattr(self, '_kf', None) or MATCH_FIELDS.get(source, "content,title"))
        ec["msgType"] = msg_type
        # postTypes
        if source == "video":
            ec["postTypes"] = POST_TYPES["video"]
        elif source == "qa":
            ec["postTypes"] = POST_TYPES["qa"]
        elif source == "shortVideo" and site_id == "1003583":  # 抖音
            ec["postTypes"] = POST_TYPES["shortVideo_douyin"]
        return ec

    def _build_custom_conditions(
        self,
        filter_ad: bool,
        content_mode: str,
        min_interaction: Optional[int],
    ) -> list:
        """构建 customConditions"""
        conds = {}
        if filter_ad:
            conds["is_ad"] = ["0"]
        if content_mode and content_mode.upper() != "ALL":
            conds["content_mode"] = [content_mode.upper()]
        if min_interaction is not None:
            conds["interaction_cnt"] = [min_interaction, None]
        if not conds:
            return []
        return [{"sources": "", "tConditions": [conds]}]

    def _post(self, path: str, body: dict, retries: int = 2) -> dict:
        """POST 请求，带重试"""
        url = BASE_URL + path
        for attempt in range(retries + 1):
            try:
                r = self.session.post(url, json=body, timeout=20)
                data = r.json()
                if data.get("success") and data.get("code") == 200:
                    return data.get("data", {})
                else:
                    raise ValueError(f"API error: code={data.get('code')}, msg={data.get('message')}")
            except Exception as e:
                if attempt < retries:
                    time.sleep(1)
                else:
                    raise
        return {}

    @staticmethod
    def _net_sentiment(total: dict) -> Optional[str]:
        """
        由汇总后的 positive/negative 计数派生净情感度百分比字符串（如 "58.68%"），
        与数说聚合 totalSentiments 接口自身的计算口径一致：(正面-负面)/(正面+负面)。
        不含中性；分母为 0（无正负面数据）时返回 None。
        """
        pos, neg = total.get("positive", 0), total.get("negative", 0)
        denom = pos + neg
        if not denom:
            return None
        return f"{round((pos - neg) / denom * 100, 2)}%"

    def _aggregate_metric(
        self, endpoint: str, platforms: list, keyword: str, filterword: str,
        sentiments: list, start_ts: str, end_ts: str, get_msg_type, custom: list,
        extra_body: dict = None, metric_name: str = None, catch_errors: bool = False,
    ) -> dict:
        """
        逐平台调用聚合类接口（Fix #3：修复原按 source 分组只取 pks[0] 导致同 source
        下其余平台被静默丢弃的问题）。每次调用只包含单个平台的 siteIds，
        因此同 source 的多个平台（如 douyin/kuaishou/shipinhao 均为 shortVideo）
        不会互相覆盖。返回 {platform_key: raw_response_data}，自动跳过该指标
        在 PLATFORM_UNSUPPORTED 中登记为不支持的平台。
        """
        per_platform = {}
        for pk in platforms:
            if metric_name and metric_name in PLATFORM_UNSUPPORTED.get(pk, set()):
                continue
            sv = PLATFORM_MAP[pk]["source"]
            ec = {sv: self._build_extra(pk, get_msg_type(pk))}
            b = {
                "token": self.token, "keyword": keyword, "filterword": filterword,
                "sources": [sv], "sentiments": sentiments,
                "startTime": start_ts, "endTime": end_ts,
                "extra_condition": ec,
            }
            if custom:
                b["customConditions"] = custom
            if extra_body:
                b.update(extra_body)
            try:
                per_platform[pk] = self._post(endpoint, b)
            except ValueError:
                if not catch_errors:
                    raise
                per_platform[pk] = {}
            time.sleep(0.3)
        return per_platform

    def _split_date_range(self, start_date: str, end_date: str, max_days: int = 366) -> list:
        """按 max_days 切分日期区间（Fix #6：数说聚合 API 对单次查询有 366 天上限）"""
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(end_date, "%Y-%m-%d")
        total_days = (d2 - d1).days + 1
        if total_days <= max_days:
            return [(start_date, end_date)]

        segments = []
        seg_start = d1
        while seg_start <= d2:
            seg_end = min(seg_start + timedelta(days=max_days - 1), d2)
            segments.append((seg_start.strftime("%Y-%m-%d"), seg_end.strftime("%Y-%m-%d")))
            seg_start = seg_end + timedelta(days=1)
        return segments

    def _merge_full_results(self, results: list) -> dict:
        """合并多个日期分段的 full 模式结果（Fix #6）"""
        if len(results) == 1:
            return results[0]

        merged = {"meta": dict(results[0]["meta"])}
        merged["meta"]["start_date"] = results[0]["meta"]["start_date"]
        merged["meta"]["end_date"] = results[-1]["meta"]["end_date"]

        def sum_or_none(key):
            vals = [r.get(key) for r in results if r.get(key) is not None]
            return sum(vals) if vals else None

        merged["total_volume"] = sum_or_none("total_volume")
        merged["total_interaction"] = sum_or_none("total_interaction")

        def merge_flat_numeric_dict(key):
            out = {}
            for r in results:
                for k, v in (r.get(key) or {}).items():
                    if isinstance(v, (int, float)):
                        out[k] = out.get(k, 0) + v
                    elif k not in out:
                        out[k] = v
            return out

        merged["data_type"] = merge_flat_numeric_dict("data_type")

        def merge_nested_numeric_dict(key):
            out = {}
            for r in results:
                for plat, vals in (r.get(key) or {}).items():
                    if not isinstance(vals, dict):
                        continue
                    acc = out.setdefault(plat, {})
                    for k, v in vals.items():
                        if isinstance(v, (int, float)):
                            acc[k] = acc.get(k, 0) + v
            return out

        merged["sentiment_dist"] = merge_nested_numeric_dict("sentiment_dist")
        merged["total_sentiment"] = self._net_sentiment(merged["sentiment_dist"].get("_total", {}))
        merged["volume_type"] = merge_nested_numeric_dict("volume_type")

        def merge_trend(key):
            out = {}
            for r in results:
                for plat, days in (r.get(key) or {}).items():
                    if not isinstance(days, dict):
                        continue
                    acc = out.setdefault(plat, {})
                    for day, cnt in days.items():
                        acc[day] = acc.get(day, 0) + cnt
            return out

        merged["volume_trend"] = merge_trend("volume_trend")
        merged["interaction_trend"] = merge_trend("interaction_trend")

        def merge_cloud_word():
            out = {}
            for r in results:
                for plat, words in (r.get("cloud_word") or {}).items():
                    if not isinstance(words, dict):
                        continue
                    acc = out.setdefault(plat, {})
                    for w, cnt in words.items():
                        acc[w] = acc.get(w, 0) + cnt
            return out

        merged["cloud_word"] = merge_cloud_word()

        def merge_articles():
            out = {}
            for r in results:
                for plat, arts in (r.get("articles") or {}).items():
                    out.setdefault(plat, []).extend(arts or [])
            for plat in out:
                out[plat].sort(key=lambda a: a.get("interaction_cnt", 0) or 0, reverse=True)
            return out

        merged["articles"] = merge_articles()

        def merge_region_dist():
            if all(r.get("region_dist") is None for r in results):
                return None
            out = {}
            for r in results:
                for region, cnt in (r.get("region_dist") or {}).items():
                    if isinstance(cnt, (int, float)):
                        out[region] = out.get(region, 0) + cnt
            return out

        merged["region_dist"] = merge_region_dist()

        return merged

    # ─────────────────────────────────────────────
    # 主入口
    # ─────────────────────────────────────────────

    def save_json(self, result: dict, output_dir: str = "/tmp/datashuoshuo") -> str:
        """
        将 run() 结果保存为本地 JSON 文件。
        文件名格式：{keyword}_{start}_{end}_{timestamp}.json
        返回保存路径。
        """
        os.makedirs(output_dir, exist_ok=True)
        meta = result.get("meta", {})
        keyword = meta.get("keyword", "unknown").replace("+", "_").replace(" ", "_")
        start  = meta.get("start_date", "").replace("-", "")
        end    = meta.get("end_date",   "").replace("-", "")
        ts     = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{keyword}_{start}_{end}_{ts}.json"
        path = os.path.join(output_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        return path

    def build_summary(self, result: dict) -> dict:
        """
        从 full 结果中提取摘要层，适合 agent 消费（控制在 2K token 内）。
        包含预计算百分比，不含原文全文和完整趋势序列。
        """
        meta = result.get("meta", {})

        def pct(val, total):
            if not total:
                return 0.0
            return round(val / total * 100, 1)

        # 情感分布
        sd = result.get("sentiment_dist", {})
        sentiment_summary = {}
        for plat, vals in sd.items():
            total = sum(vals.values())
            sentiment_summary[plat] = {
                "positive":     vals.get("positive", 0),
                "positive_pct": pct(vals.get("positive", 0), total),
                "neutral":      vals.get("neutral", 0),
                "neutral_pct":  pct(vals.get("neutral", 0), total),
                "negative":     vals.get("negative", 0),
                "negative_pct": pct(vals.get("negative", 0), total),
            }

        # 声量类型
        vt = result.get("volume_type", {})
        volume_type_summary = {}
        for plat, vals in vt.items():
            total = sum(vals.values())
            volume_type_summary[plat] = {
                "UGC": vals.get("UGC", 0), "UGC_pct": pct(vals.get("UGC", 0), total),
                "PGC": vals.get("PGC", 0), "PGC_pct": pct(vals.get("PGC", 0), total),
                "UKN": vals.get("UKN", 0), "UKN_pct": pct(vals.get("UKN", 0), total),
            }

        # 数据类型（各平台声量 + 占比）
        dt = result.get("data_type", {})
        dt_total = sum(v for v in dt.values() if isinstance(v, (int, float)))
        data_type_summary = {
            k: {"count": v, "pct": pct(v, dt_total)}
            for k, v in dt.items() if isinstance(v, (int, float))
        }

        # TOP10 地区
        rd = result.get("region_dist") or {}
        top_regions = sorted(rd.items(), key=lambda x: x[1], reverse=True)[:10]

        # 各平台 TOP20 关键词
        cloud = result.get("cloud_word", {})
        top_keywords = {
            plat: sorted(words.items(), key=lambda x: x[1], reverse=True)[:20]
            for plat, words in cloud.items()
        }

        # 原文摘要（每平台 TOP10，只留 title/url/interaction_cnt/sentiment）
        articles = result.get("articles", {})
        article_digest = {}
        for plat, arts in articles.items():
            article_digest[plat] = [
                {
                    "title":           a.get("title", "")[:100],
                    "url":             a.get("url", ""),
                    "interaction_cnt": a.get("interaction_cnt", 0),
                    "sentiment":       a.get("sentiment", ""),
                    "site_name":       a.get("site_name", ""),
                    "publish_time":    a.get("publish_time", ""),
                }
                for a in (arts or [])[:10]
            ]

        return {
            "meta":              meta,
            "total_volume":      result.get("total_volume"),
            "total_interaction": result.get("total_interaction"),
            "total_sentiment":   result.get("total_sentiment"),
            "data_type":         data_type_summary,
            "sentiment_dist":    sentiment_summary,
            "volume_type":       volume_type_summary,
            "top_regions":       top_regions,
            "top_keywords":      top_keywords,
            "article_digest":    article_digest,
        }

    def _run_single_segment(
        self,
        keyword: str,
        start_date: str,
        end_date: str,
        platforms: list,
        filterword: str,
        content_mode: str,
        msg_type,
        filter_ad: bool,
        keyword_fields: Optional[str],
        min_interaction: Optional[int],
        sort: str,
        sentiments: list,
        custom: list,
    ) -> dict:
        """
        单个日期分段（≤366天）的核心取数逻辑，始终返回 full 形态结果。
        由 run() 负责分段调度、mode/save 转换；分段合并见 _merge_full_results()。
        """
        start_ts = self._ts(start_date, end=False)
        end_ts   = self._ts(end_date,   end=True)

        def get_msg_type(pk):
            if isinstance(msg_type, dict):
                return msg_type.get(pk, "0")
            return msg_type

        result = {
            "meta": {
                "keyword": keyword,
                "filterword": filterword,
                "platforms": platforms,
                "start_date": start_date,
                "end_date": end_date,
                "content_mode": content_mode,
                "msg_type": msg_type,
                "filter_ad": filter_ad,
                "min_interaction": min_interaction,
                "sort": sort,
            }
        }

        # ── 1. 总声量（逐平台求和）──
        per = self._aggregate_metric(
            "/api/socialmedia/totalVolume", platforms, keyword, filterword,
            sentiments, start_ts, end_ts, get_msg_type, custom,
        )
        vals = [d.get("total") for d in per.values() if d.get("total") is not None]
        result["total_volume"] = sum(vals) if vals else None

        # ── 2. 总互动量（逐平台求和，跳过 news/forum/ecom）──
        per = self._aggregate_metric(
            "/api/socialmedia/totalInteraction", platforms, keyword, filterword,
            sentiments, start_ts, end_ts, get_msg_type, custom,
            metric_name="total_interaction",
        )
        vals = [d.get("total") for d in per.values() if d.get("total") is not None]
        result["total_interaction"] = sum(vals) if vals else None

        # ── 3&5. 情感分布（逐平台调用，_total 由分平台结果求和得出，跳过不支持的）──
        # 总情感度（total_sentiment）不是可逐平台/逐时间段求和的原始计数，而是 API 按
        # (正面-负面)/(正面+负面) 计算的净情感度百分比（已用真实 API 响应验证：
        # douyin 单平台 positive=121134 negative=31540 → (121134-31540)/(121134+31540)
        # = 58.69%，与 totalSentiments 接口直接返回的 "58.68%" 一致，误差在 API 自身
        # 舍入范围内）。因此改为从下方已正确聚合的 positive/negative 计数派生，
        # 不再单独调用 totalSentiments 接口逐平台求和（那样会把百分比字符串错误相加）。
        per = self._aggregate_metric(
            "/api/socialmedia/sentimentDistribute", platforms, keyword, filterword,
            sentiments, start_ts, end_ts, get_msg_type, custom,
            extra_body={"countType": True}, metric_name="sentiment_dist",
        )
        sentiment_dist = {}
        total = {"positive": 0, "neutral": 0, "negative": 0}
        for pk, d in per.items():
            raw = d.get("total") or {}
            vals = {
                "positive": raw.get("1", 0),
                "neutral":  raw.get("0", 0),
                "negative": raw.get("-1", 0),
            }
            sentiment_dist[pk] = vals
            for k in total:
                total[k] += vals[k]
        if per:
            sentiment_dist["_total"] = total
        result["sentiment_dist"] = sentiment_dist
        result["total_sentiment"] = self._net_sentiment(total)

        # ── 4. 数据类型（逐平台调用后按 key 求和）──
        per = self._aggregate_metric(
            "/api/socialmedia/dataType", platforms, keyword, filterword,
            sentiments, start_ts, end_ts, get_msg_type, custom,
            extra_body={"countType": True},
        )
        data_type = {}
        for d in per.values():
            raw = d.get("total") or {}
            for k, v in raw.items():
                if isinstance(v, (int, float)):
                    data_type[k] = data_type.get(k, 0) + v
                elif k not in data_type:
                    data_type[k] = v
        result["data_type"] = data_type

        # ── 6. 声量类型（逐平台调用，_total 由分平台结果求和得出，跳过不支持的）──
        per = self._aggregate_metric(
            "/api/socialmedia/volumeType", platforms, keyword, filterword,
            sentiments, start_ts, end_ts, get_msg_type, custom,
            extra_body={"countType": True}, metric_name="volume_type",
        )
        volume_type = {}
        total = {"UGC": 0, "PGC": 0, "UKN": 0}
        for pk, d in per.items():
            raw = d.get("total") or {}
            vals = {"UGC": raw.get("UGC", 0), "PGC": raw.get("PGC", 0), "UKN": raw.get("UKN", 0)}
            volume_type[pk] = vals
            for k in total:
                total[k] += vals[k]
        if per:
            volume_type["_total"] = total
        result["volume_type"] = volume_type

        # ── 7. 声量趋势（逐平台）──
        volume_trend = {}
        for pk in platforms:
            info = PLATFORM_MAP[pk]
            sv = info["source"]
            ec = {sv: self._build_extra(pk, get_msg_type(pk))}
            b = {
                "token": self.token, "keyword": keyword, "filterword": filterword,
                "source": sv, "sentiments": sentiments,
                "startTime": start_ts, "endTime": end_ts,
                "extra_condition": ec,
            }
            if custom: b["customConditions"] = custom
            d = self._post("/api/socialmedia/volumeTread", b)
            for k, v in d.get("total", {}).items():
                volume_trend[pk] = v
            time.sleep(0.3)
        result["volume_trend"] = volume_trend

        # ── 8. 互动量趋势（逐平台，跳过不支持的）──
        interaction_trend = {}
        for pk in platforms:
            if "interaction_trend" in PLATFORM_UNSUPPORTED.get(pk, set()):
                interaction_trend[pk] = None
                continue
            info = PLATFORM_MAP[pk]
            sv = info["source"]
            ec = {sv: self._build_extra(pk, get_msg_type(pk))}
            b = {
                "token": self.token, "keyword": keyword, "filterword": filterword,
                "source": sv, "sentiments": sentiments,
                "startTime": start_ts, "endTime": end_ts,
                "extra_condition": ec,
            }
            if custom: b["customConditions"] = custom
            d = self._post("/api/socialmedia/interactionTread", b)
            for k, v in d.get("total", {}).items():
                interaction_trend[pk] = v
            time.sleep(0.3)
        result["interaction_trend"] = interaction_trend

        # ── 9. 关键词云（逐平台）──
        cloud_word = {}
        for pk in platforms:
            info = PLATFORM_MAP[pk]
            sv = info["source"]
            ec = {sv: self._build_extra(pk, get_msg_type(pk))}
            b = {
                "token": self.token, "keyword": keyword, "filterword": filterword,
                "source": sv, "sentiments": sentiments,
                "startTime": start_ts, "endTime": end_ts,
                "extra_condition": ec,
            }
            if custom: b["customConditions"] = custom
            d = self._post("/api/socialmedia/cloudWord", b)
            for k, v in d.get("total", {}).items():
                cloud_word[pk] = v
            time.sleep(0.3)
        result["cloud_word"] = cloud_word

        # ── 10. 原文内容（逐平台，按互动量排序）──
        articles = {}
        for pk in platforms:
            info = PLATFORM_MAP[pk]
            sv = info["source"]
            # news/forum/ecom 不支持互动量排序，降级为发布时间排序
            plat_sort = "publishTimestampDesc" if pk in PLATFORM_NO_INTERACTION_SORT else sort
            ec = {sv: {**self._build_extra(pk, get_msg_type(pk)), "sort": plat_sort}}
            b = {
                "token": self.token, "keyword": keyword, "filterword": filterword,
                "source": sv, "sentiments": sentiments,
                "startTime": start_ts, "endTime": end_ts,
                "extra_condition": ec,
            }
            if custom: b["customConditions"] = custom
            d = self._post("/api/socialmedia/text", b)
            items = d.get("total", [])
            articles[pk] = items if isinstance(items, list) else []
            time.sleep(0.3)
        result["articles"] = articles

        # ── 11. 区域分布（逐平台调用后按地域求和，跳过不支持的）──
        per = self._aggregate_metric(
            "/api/socialmedia/regionDistribution", platforms, keyword, filterword,
            sentiments, start_ts, end_ts, get_msg_type, custom,
            metric_name="region_dist", catch_errors=True,
        )
        if per:
            region_dist = {}
            for d in per.values():
                raw = d.get("total") or {}
                for region, cnt in raw.items():
                    if isinstance(cnt, (int, float)):
                        region_dist[region] = region_dist.get(region, 0) + cnt
            result["region_dist"] = region_dist
        else:
            result["region_dist"] = None

        return result

    def run(
        self,
        keyword: str,
        start_date: str,
        end_date: str,
        platforms: list = None,
        filterword: str = "",
        content_mode: str = "ALL",      # UGC / PGC / UKN / ALL
        msg_type="1",                   # str 或 dict{plat: type}；1=仅主贴 0=主贴+评论
        filter_ad: bool = False,
        keyword_fields: str = None,    # 覆盖 matchFields，如 "content,title" 关闭OCR
        min_interaction: Optional[int] = None,
        sort: str = "interactionCntDesc",
        sentiments: list = None,
        mode: str = "full",             # full / summary
        save: bool = False,             # 是否自动保存 JSON
        output_dir: str = "/tmp/datashuoshuo",
    ) -> dict:
        """
        标准入口。内部按 366 天上限自动切分日期区间（Fix #6），
        逐段调用 _run_single_segment() 后用 _merge_full_results() 合并，
        对调用方完全透明——无论跨度多长，返回值始终是单个完整结果。
        """
        if sentiments is None:
            sentiments = ["0", "1", "-1"]
        if platforms is None:
            platforms = DEFAULT_PLATFORMS

        self._kf = keyword_fields  # 传递给 _build_extra()

        custom = self._build_custom_conditions(filter_ad, content_mode, min_interaction)
        segments = self._split_date_range(start_date, end_date)

        seg_results = [
            self._run_single_segment(
                keyword, seg_start, seg_end, platforms, filterword,
                content_mode, msg_type, filter_ad, keyword_fields,
                min_interaction, sort, sentiments, custom,
            )
            for seg_start, seg_end in segments
        ]
        result = self._merge_full_results(seg_results)
        result["meta"]["start_date"] = start_date
        result["meta"]["end_date"] = end_date

        # 自动保存
        if save:
            result["_json_path"] = self.save_json(result, output_dir)

        if mode == "summary":
            return self.build_summary(result)
        return result
