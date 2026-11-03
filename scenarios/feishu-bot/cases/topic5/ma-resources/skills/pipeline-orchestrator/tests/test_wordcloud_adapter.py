"""Full customer Skill integration, numeric integrity and source evidence checks."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import charts
import wordcloud_adapter as adapter
from _util import make_state


def topic(term="好用", ids=None):
    return {"title": "使用体验", "terms": [term], "interpretation": "讨论围绕使用体验。",
            "rationale": "依据原词和已读原帖，不推算总体成效。", "post_ids": ids or []}


class TestWordcloudAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script_dir = charts.WORDCLOUD_DIR
        cls.font = Path(charts.ECHARTS_DIR) / "assets/fonts/SourceHanSansSC-Bold.otf"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = make_state(str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def chart(self, words=None, rows=None):
        words = words or {"好用": 10.5, "不用": 5}
        wc = {"campaigns": {"campA": {"overall": {
            "platforms": {"xiaohongshu": words}, "raw_source": {"articles": {"xiaohongshu": rows or []}},
        }}}}
        self.state.write_artifact("wordcloud_data", wc)
        self.state.write_artifact("stats_bundle", {})
        spec = charts.build_chart_specs({}, wc, {"brand": "测试品牌"})[0]
        rendered = adapter.render(spec, self.root, self.script_dir, self.font)
        return {**spec, "wordcloud_artifacts": rendered}

    def test_full_table_floats_case_and_stable_cutoff(self):
        words = {f"词{i:03}": 1.5 for i in range(101)}
        chart = self.chart(words=words)
        meta = json.loads(Path(chart["wordcloud_artifacts"]["render_path"]).read_text())
        self.assertEqual(len(chart["categories"]), 101)
        self.assertEqual(meta["selection"]["input_count"], 101)
        self.assertEqual(meta["selection"]["omitted_words"], [{"term": "词100", "value": 1.5}])
        self.assertEqual(meta["layout"][0]["value"], 1.5)
        self.assertEqual(meta["glyph_overlap_pixels"], 0)
        self.assertTrue(meta["all_display_words_preserved"])

    def test_source_tie_order_survives_artifact_key_sorting(self):
        import ssjh
        from unittest.mock import Mock
        client = Mock()
        client.run.return_value = {"cloud_word": {"xiaohongshu": {"Z": 1.5, "A": 1.5}}}
        with patch("ssjh._load_skill", return_value=Mock(return_value=client)):
            source = ssjh.pull({"period_start": "2026-10-01", "period_end": "2026-10-25",
                                "brand": "品牌", "platforms": ["xiaohongshu"]}, wordcloud=True)
        self.state.write_artifact("brand_stats", source)
        source = self.state.read_artifact("brand_stats")
        self.assertEqual(list(source["cloud_word"]["xiaohongshu"]), ["A", "Z"])
        stats = {"brand": {"wordcloud": source["cloud_word"],
                           "wordcloud_rows": source["_wordcloud_tables"]}}
        specs = charts.build_chart_specs(stats, {})
        spec = next(s for s in specs if s["chart_type"] == "wordcloud")
        self.assertEqual(spec["categories"], ["Z", "A"])
        self.assertEqual(spec["series"][0]["values"], [1.5, 1.5])

    def test_optional_posts_full_analysis_and_dual_images(self):
        chart = self.chart()
        prompts = []
        def model(_sys, user, **_kwargs):
            prompts.append(user)
            return {"topics": [topic("不用")]}
        with patch("semantic.chat_json", side_effect=model):
            result = adapter.analyze(self.state, chart, self.script_dir, ("转化",))
        self.assertIn('"value": 10.5', prompts[0])
        self.assertEqual(result["topics"][0]["word_frequencies"], {"不用": 5})
        self.assertEqual(result["evidence_count"], 0)
        for path in result["artifacts"].values():
            self.assertTrue(Path(path).is_file())
        from PIL import Image, ImageChops
        with Image.open(result["artifacts"]["wordcloud.png"]) as rgba, \
                Image.open(result["artifacts"]["wordcloud-white.png"]) as white:
            self.assertEqual((rgba.mode, white.mode, rgba.size), ("RGBA", "RGB", (1400, 880)))
            composite = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
            self.assertIsNone(ImageChops.difference(white, composite).getbbox())

    def test_provided_posts_read_full_body_supplement_and_source_locations(self):
        body = "原始正文" * 200 + "希望品牌赞助，并非已经合作。"
        chart = self.chart(rows=[
            {"id": "p1", "content": body, "title": "体验"},
            {"id": "p2", "content": "另一条不赞同此观点。", "title": "反例"},
        ])
        prompts = []
        responses = [
            {"candidates": [{"title": "体验", "terms": ["好用"]}], "initial_post_ids": ["p1"],
             "selection_note": "按议题检索，保留另一观点作为补读候选"},
            {"read_posts": [{"post_id": "p1", "note": "阅读全文，愿望并非合作事实",
                             "evidence": [{"field": "text", "excerpt": "希望品牌赞助，并非已经合作。",
                                           "claim": "存在赞助愿望", "relation": "qualifies"}]}]},
            {"supplemental_post_ids": ["p2"], "supplemental_reason": "主动查看相反观点"},
            {"read_posts": [{"post_id": "p2", "note": "已读反例全文",
                             "evidence": [{"field": "text", "excerpt": "另一条不赞同此观点。",
                                           "claim": "样本出现分歧", "relation": "contradicts"}]}]},
            {"topics": [topic(ids=["p1", "p2"])], "supplemental_post_ids": [],
             "stopping_reason": "全部两条已读，关键愿望与事实歧义已消除"},
            {"topics": [topic(ids=["p1", "p2"])]},
        ]
        def model(_sys, user, **kwargs):
            prompts.append(user)
            return responses.pop(0)
        with patch("semantic.chat_json", side_effect=model):
            result = adapter.analyze(self.state, chart, self.script_dir, ("转化",))
        self.assertIn(body, prompts[1])
        self.assertEqual(result["evidence_count"], 2)
        evidence = json.loads(Path(result["artifacts"]["analysis-evidence.json"]).read_text())
        review = evidence["post_review"]
        self.assertEqual(review["schema_version"], 2)
        self.assertEqual(review["read_posts"][1]["phase"], "supplemental")
        self.assertEqual(review["read_posts"][0]["evidence"][0]["source"]["pointer"],
                         "/campaigns/campA/overall/raw_source/articles/xiaohongshu/0/content")

    def test_unknown_term_is_retried_not_silently_dropped(self):
        chart = self.chart()
        with patch("semantic.chat_json", return_value={"topics": [topic("模型新词")]}) as model:
            with self.assertRaisesRegex(RuntimeError, "代表词"):
                adapter.analyze(self.state, chart, self.script_dir, ("转化",))
        self.assertEqual(model.call_count, 3)

    def test_unknown_post_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "未读"):
            adapter.validate_topics({"topics": [topic(ids=["fake"])]}, {"好用"}, {"real"}, ())

    def test_case_sensitive_original_term_and_forbidden_claim(self):
        with self.assertRaisesRegex(ValueError, "原词"):
            adapter.validate_topics({"topics": [topic("abc")]}, {"ABC"}, set(), ())
        bad = topic()
        bad["interpretation"] = "提高转化率。"
        with self.assertRaisesRegex(ValueError, "禁用"):
            adapter.validate_topics({"topics": [bad]}, {"好用"}, set(), ("转化",))

    def test_command_checks_exit_status_and_json(self):
        import subprocess
        for stdout, code in [('{"status":"PARTIAL"}', 3), ('{"status":"COMPOSED"}', 2), ("broken", 0)]:
            with self.subTest(stdout=stdout):
                with patch("wordcloud_adapter.subprocess.run", return_value=subprocess.CompletedProcess([], code, stdout, "")):
                    with self.assertRaises(RuntimeError):
                        adapter.command(self.script_dir, "compose")

    def test_modified_white_image_prevents_compose(self):
        chart = self.chart()
        path = Path(chart["wordcloud_artifacts"]["white_path"])
        from PIL import Image
        Image.new("RGB", (1400, 880), "black").save(path)
        with patch("semantic.chat_json", return_value={"topics": [topic()]}):
            with self.assertRaisesRegex(RuntimeError, "IMAGE_MISMATCH"):
                adapter.analyze(self.state, chart, self.script_dir, ())

    def test_original_field_tampering_is_not_silently_accepted(self):
        from importlib.util import spec_from_file_location, module_from_spec
        scripts = Path(self.script_dir) / "scripts"
        sys.path.insert(0, str(scripts))
        spec = spec_from_file_location("customer_post_evidence", scripts / "post_evidence.py")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        chart = self.chart(rows=[{"id": "p1", "content": "完整正文"}])
        prepared = adapter.prepare_posts(self.state, chart["wordcloud_artifacts"]["scope"], self.root)
        posts_path, payload = prepared
        payload["posts"][0]["text"] = "完整正文伪造"
        adapter.write(posts_path, payload)
        review = self.root / "post-review.json"
        adapter.write(review, {"schema_version": 2, "input_sha256": "hash",
                              "posts_file": str(posts_path), "posts_sha256": adapter.file_hash(posts_path),
                              "selection_note": "抽样", "stopping_reason": "读完",
                              "read_posts": [{"post_id": "p1", "phase": "initial", "note": "已读",
                                              "evidence": [{"field": "text", "excerpt": "完整正文",
                                                            "claim": "判断", "relation": "supports"}]}]})
        with self.assertRaisesRegex(ValueError, "differs from original"):
            module.load_post_evidence(review, "hash", [topic(ids=["p1"])])


if __name__ == "__main__":
    unittest.main()
