"""Selected sentence IDs must resolve to exact, field-bound original evidence."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wordcloud_adapter as adapter
from _util import make_state


class TestQuoteSelection(unittest.TestCase):
    def test_unknown_terms_feedback_names_topic_and_allowed_words(self):
        with self.assertRaises(ValueError) as error:
            adapter.validate_terms({"title": "周边", "terms": ["酸奶", "行李牌", "联名"]},
                                   {"酸奶", "周边"})
        self.assertIn('"unknown_terms": ["联名", "行李牌"]', str(error.exception))
        self.assertIn('"allowed_terms": ["周边", "酸奶"]', str(error.exception))
        self.assertIn("周边", str(error.exception))

    def test_duplicate_and_unread_post_ids_are_distinguished(self):
        topic = {"title": "产品", "terms": ["酸奶"], "interpretation": "讨论产品。",
                 "rationale": "已读原帖。", "post_ids": ["read", "read", "unread"]}
        with self.assertRaises(ValueError) as error:
            adapter.validate_topics({"topics": [topic]}, {"酸奶"}, {"read"}, ())
        self.assertIn('"duplicate_ids": ["read"]', str(error.exception))
        self.assertIn('"unread_ids": ["unread"]', str(error.exception))
        self.assertIn('"allowed_read_ids": ["read"]', str(error.exception))

    def test_original_punctuation_spacing_and_negation_are_preserved(self):
        post = {"id": "p", "text": "开头。\r\n回看孙颖莎的来时路…并非购买效果！\n",
                "ocr_text": "WTT中国大满贯2026\n官方合作伙伴  冠益乳"}
        choices = adapter.quote_choices(post)
        self.assertTrue(all(c["excerpt"] in post[c["field"]] for c in choices))
        chosen = next(c for c in choices if "并非" in c["excerpt"])
        evidence = {"field": chosen["field"], "quote_id": chosen["quote_id"]}
        adapter.resolve_quote(evidence, post)
        self.assertEqual(evidence["excerpt"], "回看孙颖莎的来时路…并非购买效果！")
        ocr = next(c for c in choices if "官方" in c["excerpt"])
        adapter.resolve_quote(ocr, post)
        self.assertEqual(ocr["excerpt"], "官方合作伙伴  冠益乳")

    def test_unknown_and_cross_field_ids_are_rejected(self):
        post = {"id": "p", "text": "原始正文。", "title": "标题"}
        for fragment in ({"field": "text", "quote_id": "text:999"},
                         {"field": "title", "quote_id": "text:0"}):
            with self.subTest(fragment=fragment), self.assertRaisesRegex(ValueError, "编号"):
                adapter.resolve_quote(fragment, post)

    def test_fabricated_excerpt_is_rejected_even_with_valid_id(self):
        with self.assertRaisesRegex(ValueError, "不一致"):
            adapter.resolve_quote({"field": "text", "quote_id": "text:0", "excerpt": "原文..."},
                                  {"id": "p", "text": "原文并非购买效果。"})

    def test_legacy_exact_quotes_still_require_original_field(self):
        post = {"id": "p", "text": "原文并非购买效果。"}
        adapter.resolve_quote({"field": "text", "excerpt": "并非购买效果。"}, post)
        with self.assertRaisesRegex(ValueError, "连续原文"):
            adapter.resolve_quote({"field": "text", "excerpt": "原文..."}, post)

    def test_analysis_reads_full_post_and_persists_canonical_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = make_state(tmp)
            body = "完整原帖开头。\n好用，但并非购买效果。"
            state.write_artifact("wordcloud_data", {"campaigns": {"c": {"overall": {
                "raw_source": {"articles": {"xiaohongshu": [{"id": "p", "content": body}]}}
            }}}})
            state.write_artifact("stats_bundle", {})
            payload = {"words": [{"term": "好用", "value": 1}], "context": {}, "metric": "unknown"}
            input_file = root / "input.json"
            adapter.write(input_file, payload)
            render = root / "render"
            render.mkdir()
            chart = {"wordcloud_input": payload, "wordcloud_artifacts": {
                "input_path": str(input_file), "output_dir": str(render),
                "scope": {"campaign": "c", "dimension": "overall", "platform": "xiaohongshu"}}}
            selected = next(c for c in adapter.quote_choices({"text": body}) if "并非" in c["excerpt"])
            topic = {"title": "使用讨论", "terms": ["好用"], "interpretation": "样本讨论使用感受。",
                     "rationale": "原文保留条件，不推断购买效果。", "post_ids": ["p"]}
            responses = [
                {"candidates": [{"title": "使用讨论", "terms": ["好用"]}],
                 "initial_post_ids": ["p"], "selection_note": "按议题选择唯一原帖"},
                {"read_posts": [{"post_id": "p", "note": "全文保留条件",
                  "evidence": [{"field": "text", "quote_id": selected["quote_id"],
                                "claim": "样本保留限定", "relation": "qualifies"}]}]},
                {"topics": [topic], "supplemental_post_ids": [], "stopping_reason": "原帖已读"},
                {"topics": [topic]},
            ]
            prompts = []
            def model(_system, user, **kwargs):
                prompts.append(user)
                return responses.pop(0)
            with patch("semantic.chat_json", side_effect=model), \
                    patch.object(adapter, "command", return_value={"input_sha256": "fingerprint"}):
                result = adapter.analyze(state, chart, root, ())
            review = json.loads(Path(result["post_review_path"]).read_text())
            evidence = review["read_posts"][0]["evidence"][0]
            self.assertEqual(evidence["excerpt"], "好用，但并非购买效果。")
            self.assertEqual(result["evidence_count"], 1)
            self.assertIn(json.dumps(body, ensure_ascii=False), prompts[1])
            self.assertIn("quote_choices", prompts[1])
            self.assertIn(json.dumps(evidence["excerpt"], ensure_ascii=False), prompts[2])


if __name__ == "__main__":
    unittest.main()
