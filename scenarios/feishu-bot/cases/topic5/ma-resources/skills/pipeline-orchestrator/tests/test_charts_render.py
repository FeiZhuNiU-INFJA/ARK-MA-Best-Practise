"""波次4：图表接真（charts_render n14）契约测试（只渲染本地 PNG）。

无 node_modules / node 时走确定性降级：
  - build_chart_specs 把 stats_bundle + wordcloud_data 翻译为符合 echarts bundle 契约的 spec；
  - 资格规则剔除空图/全零/单类别，多活动逐个出图，词云派生图逐平台独立不合并；
  - fallback 适配器产 chart_urls（degraded=True），local_path=None，供独立契约测试；
    write_report 的 _fallback_report_md 无差别消费。
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import shutil
import unittest

import charts
import handlers
from _util import make_state


def _node(node_id, outputs):
    return {"id": node_id, "name": node_id, "outputs": outputs}


_STATS_BUNDLE = {
    "campaigns": {
        "c1": {
            "metrics": {"活动相关条数": 30, "条均互动量": 12.5,
                        "品牌提及率": 0.8, "产品提及占比": 0.5, "NSR": 0.3},
            "sentiment_dist": {"positive": 18, "neutral": 7, "negative": 5},
            "platform_dist": {"weibo": 12, "xiaohongshu": 10, "douyin": 8},
            "trend": {"20240501": 10, "20240502": 12, "20240503": 8},
            "content_type_dist": {"UGC": 22, "PGC": 6, "UKN": 2},
        },
    },
}
_WORDCLOUD = {"campaigns": {"c1": {"overall": {
    "platforms": {
        "weibo": {"立白": 100, "活动": 60, "好评": 30, "促销": 20},
        "xiaohongshu": {"立白": 80, "测评": 55, "种草": 25},
    },
    "evidence_rows": [],
}}}}


class TestChartSpecs(unittest.TestCase):
    def test_renderer_registers_packaged_chinese_font_files(self):
        render_script = charts._RENDER_SCRIPT.replace(
            "render_from_spec.js", "render_chart.js"
        )
        with open(render_script, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("fontFiles", source)
        self.assertNotIn("fontBuffers", source)
        self.assertIn("Source Han Sans SC", source)

    def test_build_specs_per_campaign_and_wordcloud(self):
        specs = charts.build_chart_specs(_STATS_BUNDLE, _WORDCLOUD)
        ids = {s["chart_id"] for s in specs}
        # 活动 c1 四类图齐全。
        self.assertIn("c1_sentiment", ids)
        self.assertIn("c1_platform", ids)
        self.assertIn("c1_trend", ids)
        self.assertIn("c1_content", ids)
        # 词云逐平台独立派生图，不合并。
        self.assertIn("wordcloud_c1_overall_weibo", ids)
        self.assertIn("wordcloud_c1_overall_xiaohongshu", ids)

    def test_specs_match_echarts_bundle_contract(self):
        """每个 spec 必含 render_from_spec.js 要求的 chart_type/categories/series。"""
        for s in charts.build_chart_specs(_STATS_BUNDLE, _WORDCLOUD):
            self.assertIn(s["chart_type"],
                          {"donut", "pie", "bar", "line", "rank_bar", "wordcloud"})
            self.assertIsInstance(s["categories"], list)
            self.assertIsInstance(s["series"], list)
            self.assertTrue(s["series"] and "values" in s["series"][0])
            self.assertEqual(len(s["categories"]), len(s["series"][0]["values"]))

    def test_all_zero_sentiment_dropped(self):
        stats = {"campaigns": {"c1": {"metrics": {},
                 "sentiment_dist": {"positive": 0, "neutral": 0, "negative": 0}}}}
        ids = {s["chart_id"] for s in charts.build_chart_specs(stats, {})}
        self.assertNotIn("c1_sentiment", ids)

    def test_single_platform_bar_dropped(self):
        stats = {"campaigns": {"c1": {"metrics": {}, "platform_dist": {"weibo": 10}}}}
        ids = {s["chart_id"] for s in charts.build_chart_specs(stats, {})}
        self.assertNotIn("c1_platform", ids)

    def test_three_level_mention_funnel_uses_supported_bar_type(self):
        stats = {"campaigns": {"c1": {
            "metrics": {"活动相关条数": 30},
            "mention_funnel": {
                "brand_hits": 20,
                "product_hits": 10,
                "has_product_keywords": True,
            },
        }}}
        specs = {
            s["chart_id"]: s for s in charts.build_chart_specs(stats, {})
        }
        spec = specs["c1_mention_funnel"]
        self.assertEqual(spec["chart_type"], "bar")
        self.assertEqual(
            spec["categories"], ["活动相关", "品牌提及", "产品提及"]
        )

    def test_wordcloud_is_native_and_split_by_platform(self):
        """真正词云按活动、维度、平台独立生成。"""
        specs = {s["chart_id"]: s for s in charts.build_chart_specs({}, _WORDCLOUD)}
        spec = specs["wordcloud_c1_overall_weibo"]
        self.assertEqual(spec["chart_type"], "wordcloud")
        self.assertEqual(spec["source_mode"], "native")


class TestChartsRenderHandler(unittest.TestCase):
    def setUp(self):
        for k in ("REPORT_COVER_API_KEY",):
            os.environ.pop(k, None)
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)
        self.st.write_artifact("stats_bundle", _STATS_BUNDLE)
        self.st.write_artifact("wordcloud_data", _WORDCLOUD)
        self.st.write_artifact("confirmed_plan", {"brand": "立白"})

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_fallback_has_local_path_none(self):
        """降级 fallback 每张图 local_path=None（无渲染器即无图），url 也为 None。"""
        cu = charts.fallback(_STATS_BUNDLE, _WORDCLOUD, project="立白",
                             version="v0.0", report_name="立白营销活动复盘")
        self.assertTrue(cu["degraded"])
        c0 = cu["charts"][0]
        self.assertIsNone(c0["local_path"])
        self.assertIsNone(c0["url"])
        self.assertIn("spec", c0)
        self.assertIsNone(cu["cover"]["local_path"])

    def test_handler_rejects_degraded_render(self):
        for mode in ("demo", "full"):
            with self.subTest(mode=mode):
                self.st.params["run_mode"] = mode
                with self.assertRaisesRegex(RuntimeError, "禁止无图交付"):
                    handlers.get("charts_render")(
                        _node("n14_charts", ["chart_urls"]), self.st, {}
                    )

    def test_chart_urls_consumable_by_fallback_report(self):
        """降级 chart_urls 喂给 write_report 应产出报告 MD（无图时不塞死链）。"""
        chart_urls = charts.fallback(
            _STATS_BUNDLE, _WORDCLOUD, project="立白",
            version="v0.0", report_name="立白营销活动复盘",
        )
        md = handlers._fallback_report_md(
            _STATS_BUNDLE, chart_urls, {}, "five", "0.0"
        )
        self.assertIn("营销活动复盘报告", md)
        # 降级无图：report 不应嵌入 None 链接。
        self.assertNotIn("](None)", md)


if __name__ == "__main__":
    unittest.main()
