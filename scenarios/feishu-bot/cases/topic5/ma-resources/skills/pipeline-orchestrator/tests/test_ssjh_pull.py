"""波次3：SSJH 数据源接真（ssjh_pull n05/n06/n13）契约测试。

无 SSJH_API_TOKEN 时走确定性降级：锁定三种出口的 schema、平台不合并、
articles 结构真实（可被 clean_campaign 直接消费），以及 brand/baseline 窗口区分。
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import ssjh
import handlers
from _util import make_state


def _node(node_id, outputs):
    return {"id": node_id, "name": node_id, "outputs": outputs}


class TestSsjhPull(unittest.TestCase):
    def setUp(self):
        # 清空凭据，确保走降级路径（测试不打真实外网）。
        os.environ.pop("SSJH_API_TOKEN", None)
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)
        self.plan = {
            "brand": "立白", "keywords": ["立白洗衣粉"],
            "platforms": ["weibo", "xiaohongshu", "douyin"],
            "period_start": "2024-05-01", "period_end": "2024-05-31",
        }
        self.st.write_artifact("confirmed_plan", self.plan)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_api_unavailable_without_token(self):
        self.assertFalse(ssjh.api_available())

    def test_brand_pull_outputs_brand_stats_with_articles(self):
        bs = ssjh.fallback(self.plan)
        self.assertTrue(bs.get("degraded"))
        self.assertIn("articles", bs)
        self.assertEqual(set(bs["articles"].keys()),
                         {"weibo", "xiaohongshu", "douyin"})
        # articles row 结构可被 clean_campaign 直接消费（含 publish_time/fingerprint/sentiment）。
        row = bs["articles"]["weibo"][0]
        for k in ("publish_time", "fingerprint", "sentiment", "interaction_cnt",
                  "content", "content_mode"):
            self.assertIn(k, row)
        self.assertEqual(bs["meta"]["window"], "campaign")

    def test_brand_stats_cannot_replace_required_activity_excel(self):
        self.st.write_artifact("brand_stats", ssjh.fallback(self.plan))
        with patch(
            "handlers.excel_source.load_activity_rows", return_value=None
        ), self.assertRaisesRegex(ValueError, "缺少 G2 冻结"):
            handlers.get("clean_campaign")(
                _node("n07_clean", ["cleaned_data"]), self.st, {}
            )

    def test_baseline_window_is_30d_before_campaign(self):
        bl = ssjh.fallback(self.plan, baseline=True)
        self.assertEqual(bl["meta"]["window"], "baseline")
        # 基准期落在活动期起点之前（不重叠）。
        self.assertEqual(bl["meta"]["end_date"], "2024-04-30")
        self.assertEqual(bl["meta"]["start_date"], "2024-04-01")

    def test_wordcloud_platforms_not_merged(self):
        wc = ssjh.fallback(self.plan)
        self.assertTrue(wc.get("degraded"))
        # 每个活动和维度内各平台独立词表，不合并成单一 _total。
        self.assertEqual(set(wc["cloud_word"].keys()),
                         {"weibo", "xiaohongshu", "douyin"})
        self.assertNotIn("_total", wc["cloud_word"])

    def test_wordcloud_uses_each_campaign_local_query(self):
        plan = {
            **self.plan,
            "activity_query": "GLOBAL_QUERY",
            "brand_query": "立白",
            "campaigns": [
                {"id": "campA", "filter_words": ["QUERY_A"]},
                {"id": "campB", "filter_words": ["QUERY_B"]},
            ],
        }
        self.st.write_artifact("confirmed_plan", plan)
        seen = []

        def fake_pull(scoped_plan, **kwargs):
            seen.append(scoped_plan["keywords"][0])
            return {"cloud_word": {"weibo": {"词": 1}}, "articles": {}}

        with patch("handlers.ssjh.api_available", return_value=True), \
                patch("handlers.ssjh.pull", side_effect=fake_pull):
            result = handlers.get("ssjh_pull")(
                _node("n13_wordcloud_pull", ["wordcloud_data"]), self.st, {}
            )["artifacts"]["wordcloud_data"]
        self.assertIn("(QUERY_A)", seen)
        self.assertIn("(QUERY_B)", seen)
        self.assertNotEqual(
            result["campaigns"]["campA"]["overall"]["query"],
            result["campaigns"]["campB"]["overall"]["query"],
        )

    def test_wordcloud_feeds_insight_per_platform(self):
        """降级词云适配器仍保持逐平台结构，但主链会拒绝该产物。"""
        payload = ssjh.fallback(self.plan)
        self.assertEqual(
            set(payload["cloud_word"]),
            {"weibo", "xiaohongshu", "douyin"},
        )
        with self.assertRaisesRegex(RuntimeError, "取数失败"):
            handlers.get("ssjh_pull")(
                _node("n05_brand_pull", ["brand_stats"]), self.st, {}
            )

    def test_missing_window_falls_back_to_defaults(self):
        bs = ssjh.fallback({"brand": "立白"})
        self.assertIn("articles", bs)
        self.assertTrue(bs.get("degraded"))


if __name__ == "__main__":
    unittest.main()
