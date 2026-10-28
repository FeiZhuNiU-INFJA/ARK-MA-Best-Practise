"""波次1 真逻辑单测：clean_campaign(n07) + aggregate(n12)。

口径锚点（源：飞书 wiki 活动复盘口径 + knowledge/）：
  - 清洗：日期取发表时间、周期过滤、fingerprint 去重、派生平台/KOL 层级、剔除比例>10% 抽查。
  - 聚合：逐活动统计、只统计活动相关、NSR 分母由 params 决定、指标白名单、缺 AI 情感报错。
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import handlers
from _util import make_state

_CST = timezone(timedelta(hours=8))


def _ms(ymd: str) -> int:
    """YYYYMMDD(CST 正午) → epoch 毫秒（贴合 SSJH publish_time 口径）。"""
    dt = datetime.strptime(ymd, "%Y%m%d").replace(hour=12, tzinfo=_CST)
    return int(dt.timestamp() * 1000)


def _row(fp, ymd, sentiment, interaction, title="", content="", keywords=None,
         platform="xiaohongshu", content_mode="UGC", **extra):
    r = {"fingerprint": fp, "publish_time": _ms(ymd), "sentiment": sentiment,
         "interaction_cnt": interaction, "title": title, "content": content,
         "keywords": keywords or [], "content_mode": content_mode}
    r.update(extra)
    return {platform: r}  # marker; flattened below


def _brand_stats(rows_by_platform):
    arts = {}
    for d in rows_by_platform:
        for platform, row in d.items():
            arts.setdefault(platform, []).append(row)
    return {"meta": {"start_date": "2026-07-01", "end_date": "2026-07-31",
                     "platforms": list(arts.keys())}, "articles": arts}


class TestCleanCampaign(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _run(self, brand_stats):
        self.st.write_artifact("brand_stats", brand_stats)
        return handlers.clean_campaign({"id": "n07"}, self.st, {})["artifacts"]["cleaned_data"]

    def test_period_filter_and_dedup(self):
        bs = _brand_stats([
            _row("a", "20260710", 1, 10),             # 在窗口内
            _row("a", "20260712", 1, 20),             # fingerprint 重复 → 去重
            _row("b", "20260705", 0, 5),              # 在窗口内
            _row("c", "20260615", -1, 3),             # 早于窗口 → 剔除
            _row("d", "20260820", 1, 7),              # 晚于窗口 → 剔除
        ])
        out = self._run(bs)
        rec = out["clean_record"]
        self.assertEqual(rec["total_in"], 5)
        self.assertEqual(rec["total_out"], 2)        # a(一条) + b
        self.assertEqual(rec["removed"]["duplicate"], 1)
        self.assertEqual(rec["removed"]["out_of_period"], 2)
        # 剔除 3/5 = 60% > 10% → 需抽查
        self.assertTrue(rec["needs_sampling_review"])

    def test_derives_platform_kol_ymd(self):
        bs = _brand_stats([_row("x", "20260715", 1, 9, fans=600000)])
        out = self._run(bs)
        row = out["rows"][0]
        self.assertEqual(row["_platform"], "xiaohongshu")
        self.assertEqual(row["_kol_level"], "head")   # 粉丝 60w → head
        self.assertEqual(row["_publish_ymd"], "20260715")

    def test_no_fans_is_unknown_level(self):
        bs = _brand_stats([_row("x", "20260715", 1, 9)])
        out = self._run(bs)
        self.assertEqual(out["rows"][0]["_kol_level"], "unknown")

    def test_all_missing_publish_time_raises(self):
        bad = {"articles": {"xiaohongshu": [
            {"fingerprint": "a", "sentiment": 1, "interaction_cnt": 1},
        ]}}
        self.st.write_artifact("brand_stats", bad)
        with self.assertRaises(ValueError):
            handlers.clean_campaign({"id": "n07"}, self.st, {})

    def test_uploaded_excel_rows_take_precedence(self):
        # 即使 SSJH 有 articles，只要 confirmed_plan 冻结了上传源，n07 应消费 Excel 行。
        self.st.write_artifact(
            "brand_stats",
            _brand_stats([_row("ssjh", "20260710", 1, 999)]),
        )
        self.st.write_artifact(
            "confirmed_plan",
            {
                "period_start": "2026-07-01",
                "period_end": "2026-07-31",
                "source_file": {"mount_path": "activity.xlsx"},
            },
        )
        excel_payload = {
            "rows": [
                {
                    "id": "excel",
                    "publish_time": _ms("20260715"),
                    "interaction_cnt": 12,
                    "_platform": "xiaohongshu",
                }
            ],
            "meta": {
                "source": "user_uploaded_excel",
                "file_name": "activity.xlsx",
                "platforms": ["xiaohongshu"],
            },
        }
        with patch(
            "handlers.excel_source.load_activity_rows",
            return_value=excel_payload,
        ):
            out = handlers.clean_campaign(
                {"id": "n07"}, self.st, {}
            )["artifacts"]["cleaned_data"]
        self.assertEqual([row["id"] for row in out["rows"]], ["excel"])
        self.assertEqual(
            out["clean_record"]["source"], "user_uploaded_excel"
        )
        self.assertEqual(out["clean_record"]["source_file"], "activity.xlsx")

    def test_demo_stably_samples_at_most_500_rows(self):
        rows = [
            _row(f"row-{index}", "20260715", 0, index)
            for index in range(600)
        ]
        self.st.params["run_mode"] = "demo"
        first = self._run(_brand_stats(rows))
        second = self._run(_brand_stats(list(reversed(rows))))

        self.assertEqual(len(first["rows"]), 500)
        self.assertEqual(
            {row["fingerprint"] for row in first["rows"]},
            {row["fingerprint"] for row in second["rows"]},
        )
        sampling = first["clean_record"]["sampling"]
        self.assertTrue(sampling["applied"])
        self.assertEqual(sampling["population_size"], 600)
        self.assertEqual(sampling["sample_size"], 500)
        self.assertIn("仅供", sampling["disclosure"])

    def test_full_mode_keeps_more_than_500_rows(self):
        rows = [
            _row(f"row-{index}", "20260715", 0, index)
            for index in range(501)
        ]
        out = self._run(_brand_stats(rows))
        self.assertEqual(len(out["rows"]), 501)
        self.assertFalse(out["clean_record"]["sampling"]["applied"])
        self.assertIsNone(out["clean_record"]["sampling"]["limit"])

    def test_demo_under_limit_does_not_claim_sampling(self):
        self.st.params["run_mode"] = "demo"
        out = self._run(_brand_stats([
            _row("a", "20260715", 0, 1),
            _row("b", "20260715", 0, 2),
        ]))
        self.assertEqual(len(out["rows"]), 2)
        self.assertFalse(out["clean_record"]["sampling"]["applied"])
        self.assertEqual(out["clean_record"]["sampling"]["limit"], 500)


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _seed_annotated(self, rows):
        self.st.write_artifact("annotated_data", {"rows": rows})

    def _agg(self):
        return handlers.aggregate({"id": "n12"}, self.st, {})["artifacts"]["stats_bundle"]

    def test_metrics_and_whitelist(self):
        rows = [
            {"relevance": "是", "sentiment": 1, "interaction_cnt": 100,
             "title": "立白活动", "content": "", "keywords": ["立白"], "_platform": "xiaohongshu"},
            {"relevance": "是", "sentiment": 0, "interaction_cnt": 50,
             "title": "普通", "content": "立白洗衣粉好用", "keywords": [], "_platform": "xiaohongshu"},
            {"relevance": "否", "sentiment": -1, "interaction_cnt": 999,
             "title": "无关", "content": "", "keywords": [], "_platform": "xiaohongshu"},
        ]
        self._seed_annotated(rows)
        self.st.write_artifact("confirmed_plan", {
            "brand_words": ["立白"], "product_words": ["洗衣粉"],
        })
        bundle = self._agg()
        camp = bundle["campaigns"]["default"]
        m = camp["metrics"]
        # 只统计活动相关 2 条，分母=2（不含 relevance=否 的 999 互动）
        self.assertEqual(m["活动相关条数"], 2)
        self.assertEqual(m["互动量"], 150)
        self.assertEqual(m["条均互动量"], 75.0)
        # 品牌提及 2/2
        self.assertEqual(m["品牌提及率"], 1.0)
        # 白名单落在产物里；禁用措辞不出现在 metrics 键
        self.assertEqual(bundle["metric_whitelist"], list(handlers.METRIC_WHITELIST))
        for key in m:
            for bad in handlers.FORBIDDEN_METRIC_TERMS:
                self.assertNotIn(bad, key)

    def test_nsr_denominator_param(self):
        rows = [
            {"relevance": "是", "sentiment": 1, "interaction_cnt": 1},
            {"relevance": "是", "sentiment": 0, "interaction_cnt": 1},
            {"relevance": "是", "sentiment": -1, "interaction_cnt": 1},
        ]
        self._seed_annotated(rows)
        # 默认含中性：分母=3 → (1-1)/3 = 0.0
        self.st.params["nsr_denominator_includes_neutral"] = True
        self.assertEqual(self._agg()["campaigns"]["default"]["metrics"]["NSR"], 0.0)
        # 不含中性：分母=2 → (1-1)/2 = 0.0（符号测试用非对称数据）
        rows2 = rows + [{"relevance": "是", "sentiment": 1, "interaction_cnt": 1}]
        self._seed_annotated(rows2)
        self.st.params["nsr_denominator_includes_neutral"] = False
        # pos=2 neg=1 denom(不含中)=3 → (2-1)/3 ≈ 0.3333
        self.assertEqual(self._agg()["campaigns"]["default"]["metrics"]["NSR"], 0.3333)
        self.st.params["nsr_denominator_includes_neutral"] = True
        # pos=2 neg=1 neu=1 denom(含中)=4 → 1/4 = 0.25
        self.assertEqual(self._agg()["campaigns"]["default"]["metrics"]["NSR"], 0.25)

    def test_missing_ai_sentiment_raises(self):
        rows = [{"relevance": "是", "interaction_cnt": 1}]  # 无 sentiment/ai_sentiment
        self._seed_annotated(rows)
        with self.assertRaises(ValueError):
            self._agg()

    def test_multi_campaign_not_summed(self):
        rows = [
            {"relevance": "是", "sentiment": 1, "interaction_cnt": 10,
             "title": "A活动打卡", "content": "", "keywords": []},
            {"relevance": "是", "sentiment": 1, "interaction_cnt": 20,
             "title": "B活动测评", "content": "", "keywords": []},
        ]
        self._seed_annotated(rows)
        self.st.write_artifact("confirmed_plan", {
            "campaigns": [
                {"id": "campA", "filter_words": ["A活动"]},
                {"id": "campB", "filter_words": ["B活动"]},
            ],
        })
        bundle = self._agg()
        self.assertEqual(set(bundle["campaigns"]), {"campA", "campB"})
        self.assertEqual(bundle["campaigns"]["campA"]["metrics"]["活动相关条数"], 1)
        self.assertEqual(bundle["campaigns"]["campB"]["metrics"]["活动相关条数"], 1)
        # 逐活动各算，互动不相加为合计
        self.assertEqual(bundle["campaigns"]["campA"]["metrics"]["互动量"], 10)
        self.assertEqual(bundle["campaigns"]["campB"]["metrics"]["互动量"], 20)

    def test_ai_sentiment_overrides_platform(self):
        # ai_sentiment 优先于 SSJH sentiment
        rows = [{"relevance": "是", "sentiment": -1, "ai_sentiment": 1, "interaction_cnt": 5}]
        self._seed_annotated(rows)
        camp = self._agg()["campaigns"]["default"]
        self.assertEqual(camp["sentiment_dist"], {"positive": 1, "neutral": 0, "negative": 0})

    def test_mention_funnel_nested(self):
        # 提及漏斗嵌套：产品提及是品牌提及的子集 → 产品提及占比 ≤ 1
        rows = [
            {"relevance": "是", "sentiment": 1, "interaction_cnt": 1,
             "title": "立白洗衣粉很好", "content": "", "keywords": []},   # 品牌+产品
            {"relevance": "是", "sentiment": 1, "interaction_cnt": 1,
             "title": "立白不错", "content": "", "keywords": []},         # 仅品牌
            {"relevance": "是", "sentiment": 1, "interaction_cnt": 1,
             "title": "洗衣粉通用", "content": "", "keywords": []},        # 仅产品，不计入漏斗
        ]
        self._seed_annotated(rows)
        self.st.write_artifact("confirmed_plan",
                               {"brand_words": ["立白"], "product_words": ["洗衣粉"]})
        camp = self._agg()["campaigns"]["default"]
        self.assertEqual(camp["mention_funnel"]["brand_hits"], 2)
        self.assertEqual(camp["mention_funnel"]["product_hits"], 1)   # 仅品牌行内的产品命中
        self.assertLessEqual(camp["metrics"]["产品提及占比"], 1.0)

    def test_preserves_complete_brand_statistics_block(self):
        brand_stats = {
            "total_volume": 88,
            "total_interaction": 999,
            "sentiment_dist": {"_total": {
                "positive": 50, "neutral": 20, "negative": 18,
            }},
            "data_type": {"xiaohongshu": 60, "douyin": 28},
            "volume_type": {"_total": {"UGC": 70, "PGC": 18}},
            "volume_trend": {"20260101": 40, "20260102": 48},
            "interaction_trend": {"20260101": 400, "20260102": 599},
            "cloud_word": {"xiaohongshu": {"好用": 10}},
            "articles": {"xiaohongshu": [{"id": "brand-top"}]},
        }
        self.st.write_artifact("brand_stats", brand_stats)
        self._seed_annotated([
            {"relevance": "是", "sentiment": 1, "interaction_cnt": 1},
        ])
        brand = self._agg()["brand"]
        self.assertEqual(brand["source"], brand_stats)
        self.assertEqual(
            brand["hot_content"]["xiaohongshu"][0]["id"], "brand-top"
        )
        self.assertEqual(brand["trend"]["20260102"], 48)


class TestCleanToAggregateFlow(unittest.TestCase):
    """清洗→聚合串联：cleaned_data.rows 直接喂聚合（标注接真前的回退路径）。"""

    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_flow_without_annotation(self):
        bs = _brand_stats([
            _row("a", "20260710", 1, 10, title="立白", keywords=["立白"]),
            _row("b", "20260712", -1, 20, title="立白", keywords=["立白"]),
        ])
        self.st.write_artifact("brand_stats", bs)
        out = handlers.clean_campaign({"id": "n07"}, self.st, {})
        # 直调 handler 不经引擎持久化，手动写回 Artifact（模拟引擎 commit）
        self.st.write_artifact("cleaned_data", out["artifacts"]["cleaned_data"])
        # 无 annotated_data → 回退清洗 rows；无 relevance → relevance_missing
        bundle = handlers.aggregate({"id": "n12"}, self.st, {})["artifacts"]["stats_bundle"]
        self.assertTrue(bundle["relevance_missing"])
        self.assertEqual(bundle["campaigns"]["default"]["metrics"]["活动相关条数"], 2)


if __name__ == "__main__":
    unittest.main()
