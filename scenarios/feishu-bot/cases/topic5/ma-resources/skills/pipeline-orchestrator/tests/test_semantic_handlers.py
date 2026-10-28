"""语义节点严格运行契约与证据护栏测试。"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import tempfile
import unittest
from unittest.mock import patch

import handlers
import semantic
from _util import make_state


class TestSemanticDegradedContract(unittest.TestCase):
    def setUp(self):
        # 确保无凭据：语义 handler 走降级分支（不触网）。
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("ARK_BASE_URL", "ARK_API_KEY",
                                 "OPENAI_BASE_URL", "OPENAI_API_KEY")}
        self.ws = tempfile.mkdtemp()
        self.st = make_state(self.ws)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_llm_unavailable_without_creds(self):
        self.assertFalse(semantic.llm_available())

    def test_ugc_fixed_four_is_deterministic(self):
        self.st.params["ugc_category_mode"] = "fixed_four"
        out = handlers.ugc_discovery({"id": "n08", "name": "ugc"}, self.st, {})
        art = out["artifacts"]["ugc_category_list"]
        self.assertEqual(art["categories"], ["模仿打卡", "创意改编", "测评分享", "自发安利"])
        self.assertEqual(art["source"], "fixed")
        self.assertNotIn("degraded", art)  # 固定四类不是降级，是正常口径路径

    def test_ugc_dynamic_rejects_missing_model(self):
        self.st.params["ugc_category_mode"] = "dynamic"
        self.st.write_artifact("cleaned_data", {"rows": [{"title": "打卡", "content": "试用"}]})
        with self.assertRaisesRegex(RuntimeError, "能力不可用"):
            handlers.ugc_discovery({"id": "n08", "name": "ugc"}, self.st, {})

    def test_opinion_candidates_rejects_missing_model(self):
        self.st.write_artifact("annotated_round1",
                               {"rows": [{"title": "好用", "content": "回购"}]})
        with self.assertRaisesRegex(RuntimeError, "能力不可用"):
            handlers.opinion_candidates({"id": "n09b", "name": "oc"}, self.st, {})

    def test_wordcloud_insight_platforms_not_merged(self):
        # 两平台各自词表和证据 → 各平台独立出议题，不跨平台合并。
        self.st.write_artifact("wordcloud_data", {"campaigns": {"campA": {"overall": {
            "platforms": {
                "xiaohongshu": {"好用": 10, "回购": 8},
                "douyin": {"好看": 5, "有趣": 3},
            },
            "evidence_rows": [
                {"id": "x1", "platform": "xiaohongshu", "content": "好用，准备回购"},
                {"id": "d1", "platform": "douyin", "content": "好看而且有趣"},
            ],
        }}}})

        def fake_chat_json(_system, user, **_kwargs):
            if "平台：xiaohongshu" in user:
                return {"topics": [{"topic": "使用体验", "words": ["好用", "回购"],
                                    "evidence_ids": ["x1"]}]}
            return {"topics": [{"topic": "视觉趣味", "words": ["好看", "有趣"],
                                "evidence_ids": ["d1"]}]}

        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", side_effect=fake_chat_json):
            out = handlers.wordcloud_insight({"id": "n15", "name": "wc"}, self.st, {})
        art = out["artifacts"]["wordcloud_insight"]
        platforms = art["campaigns"]["campA"]["overall"]["platforms"]
        self.assertNotIn("degraded", art)
        self.assertIn("xiaohongshu", platforms)
        self.assertIn("douyin", platforms)
        # 每平台独立 topics，键不混入另一平台的词
        xhs_words = {w for t in platforms["xiaohongshu"]["topics"] for w in t["words"]}
        self.assertIn("好用", xhs_words)
        self.assertNotIn("好看", xhs_words)

    def test_wordcloud_rejects_unbound_evidence_and_words(self):
        self.st.write_artifact("wordcloud_data", {"campaigns": {"campA": {
            "overall": {
                "platforms": {"xiaohongshu": {"好用": 10}},
                "evidence_rows": [{
                    "id": "x1", "platform": "xiaohongshu",
                    "content": "产品好用",
                }],
            },
        }}})
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", return_value={
                    "topics": [{
                        "topic": "伪造结论",
                        "words": ["完全无来源"],
                        "evidence_ids": ["fake-999"],
                    }],
                }):
            with self.assertRaisesRegex(RuntimeError, "证据或语义解读不完整"):
                handlers.wordcloud_insight(
                    {"id": "n15", "name": "wc"}, self.st, {}
                )

    def test_wordcloud_short_refs_map_to_immutable_ids(self):
        self.st.write_artifact("wordcloud_data", {"campaigns": {"campA": {
            "overall": {
                "platforms": {"xiaohongshu": {"好用": 10, "回购": 8}},
                "evidence_rows": [{
                    "id": "long-immutable-post-id",
                    "platform": "xiaohongshu",
                    "content": "产品好用，准备回购",
                }],
            },
        }}})
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", return_value={
                    "topics": [{
                        "topic": "使用体验",
                        "words": ["好用", "回购"],
                        "evidence_refs": ["E01"],
                    }],
                }):
            out = handlers.wordcloud_insight(
                {"id": "n15", "name": "wc"}, self.st, {}
            )
        topic = out["artifacts"]["wordcloud_insight"]["campaigns"][
            "campA"]["overall"]["platforms"]["xiaohongshu"]["topics"][0]
        self.assertEqual(topic["evidence_ids"], ["long-immutable-post-id"])
        self.assertNotIn("evidence_refs", topic)

    def test_wordcloud_filters_model_added_words_but_keeps_grounded_topic(self):
        self.st.write_artifact("wordcloud_data", {"campaigns": {"campA": {
            "overall": {
                "platforms": {"xiaohongshu": {"好用": 10, "回购": 8}},
                "evidence_rows": [{
                    "id": "post-1",
                    "platform": "xiaohongshu",
                    "content": "产品好用，准备回购",
                }],
            },
        }}})
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", return_value={
                    "topics": [{
                        "topic": "使用体验",
                        "words": ["好用", "模型新增同义词"],
                        "evidence_refs": ["E01"],
                    }],
                }):
            out = handlers.wordcloud_insight(
                {"id": "n15", "name": "wc"}, self.st, {}
            )
        topic = out["artifacts"]["wordcloud_insight"]["campaigns"][
            "campA"]["overall"]["platforms"]["xiaohongshu"]["topics"][0]
        self.assertEqual(topic["words"], ["好用"])
        self.assertEqual(
            topic["dropped_unsupported_words"], ["模型新增同义词"]
        )

    def test_wordcloud_prompt_is_bounded(self):
        words = {f"词{i:02d}": 100 - i for i in range(40)}
        rows = [{
            "id": f"post-{i:02d}",
            "platform": "xiaohongshu",
            "title": "题" * 200,
            "content": "正文" * 500,
        } for i in range(20)]
        self.st.write_artifact("wordcloud_data", {"campaigns": {"campA": {
            "overall": {
                "platforms": {"xiaohongshu": words},
                "evidence_rows": rows,
            },
        }}})
        captured = {}

        def fake_chat_json(_system, user, **kwargs):
            captured["user"] = user
            captured["kwargs"] = kwargs
            return {"topics": [{
                "topic": "高频议题",
                "words": ["词00"],
                "evidence_refs": ["E01"],
            }]}

        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", side_effect=fake_chat_json):
            handlers.wordcloud_insight(
                {"id": "n15", "name": "wc"}, self.st, {}
            )

        self.assertIn('"ref": "E06"', captured["user"])
        self.assertNotIn('"ref": "E07"', captured["user"])
        self.assertIn("词15", captured["user"])
        self.assertNotIn("词16", captured["user"])
        self.assertLess(len(captured["user"]), 3500)
        self.assertEqual(captured["kwargs"]["max_tokens"], 768)
        self.assertEqual(captured["kwargs"]["retries"], 1)
        self.assertEqual(captured["kwargs"]["thinking"], "disabled")

    def test_wordcloud_rejects_unknown_short_ref_with_diagnostics(self):
        self.st.write_artifact("wordcloud_data", {"campaigns": {"campA": {
            "overall": {
                "platforms": {"xiaohongshu": {"好用": 10}},
                "evidence_rows": [{
                    "id": "post-1",
                    "platform": "xiaohongshu",
                    "content": "产品好用",
                }],
            },
        }}})
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", return_value={
                    "topics": [{
                        "topic": "使用体验",
                        "words": ["好用"],
                        "evidence_refs": ["E99"],
                    }],
                }):
            with self.assertRaisesRegex(RuntimeError, "E99"):
                handlers.wordcloud_insight(
                    {"id": "n15", "name": "wc"}, self.st, {}
                )

    def test_fallback_report_adapter_uses_real_metrics_and_whitelist(self):
        stats = {"campaigns": {
            "campA": {"metrics": {"活动相关条数": 120, "条均互动量": 33.5,
                                  "品牌提及率": 0.42, "产品提及占比": 0.31, "NSR": 0.18}},
        }}
        md = handlers._fallback_report_md(stats, {}, {}, "five", "0.0")
        # 真数字进正文
        self.assertIn("33.5", md)
        self.assertIn("条均互动量", md)
        # 禁用措辞不得出现
        for bad in ("人均", "转化率", "性价比"):
            self.assertNotIn(bad, md)

    def test_fallback_report_formats_distributions_and_evidence_as_prose(self):
        row = {
            "_annot_id": "internal-id",
            "title": "联名活动热门笔记",
            "author": "测试作者",
            "interaction_cnt": 57945,
            "url": "https://www.xiaohongshu.com/discovery/item/post-1",
            "content": "不应整行泄漏到报告",
        }
        stats = {
            "brand": {
                "sentiment_dist": {
                    "_total": {"positive": 10, "neutral": 3, "negative": 2}
                },
                "platform_dist": {"xiaohongshu": 15},
                "content_type_dist": {"PGC": 2, "UGC": 12, "UKN": 1},
                "hot_content": {"xiaohongshu": [row]},
            },
            "campaigns": {
                "campA": {
                    "metrics": {},
                    "content_type_dist": {"PGC": 2, "UGC": 12, "UKN": 1},
                    "top_content": {"pgc": [row], "ugc": []},
                },
            },
        }
        md = handlers._fallback_report_md(stats, {}, {}, "five", "0.0")
        self.assertIn("正面 10 条；中性 3 条；负面 2 条", md)
        self.assertIn("PGC 2 条；UGC 12 条；未知 1 条", md)
        self.assertIn("[联名活动热门笔记]", md)
        self.assertIn("互动量：57,945", md)
        self.assertNotIn('{"PGC"', md)
        self.assertNotIn("_annot_id", md)
        self.assertNotIn("internal-id", md)

    def test_write_report_forbidden_term_guard(self):
        # 正文护栏：命中禁用措辞直接报错（真路径生成后校验）。
        with self.assertRaises(ValueError):
            handlers._assert_no_forbidden_terms("本活动人均互动量很高")

    def test_report_guardrail_drops_forbidden_sentence_without_rewrite(self):
        md = (
            "# 总结与建议\n"
            "品牌提及率保持稳定。购买转化仍需后续观察。NSR 表现积极。\n"
            "- 性价比是主要优势\n"
        )
        cleaned, removed = handlers._strip_forbidden_sentences(md)
        self.assertIn("品牌提及率保持稳定。", cleaned)
        self.assertIn("NSR 表现积极。", cleaned)
        self.assertNotIn("转化", cleaned)
        self.assertNotIn("性价比", cleaned)
        self.assertEqual(len(removed), 2)
        handlers._assert_no_forbidden_terms(cleaned)

    def test_write_report_uses_deterministic_template_for_invalid_structure(self):
        stats = {"campaigns": {"campA": {"metrics": {}}}, "brand": {}}
        self.st.write_artifact("stats_bundle", stats)
        self.st.write_artifact("chart_urls", {"charts": []})
        self.st.write_artifact("wordcloud_insight", {"campaigns": {}})
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", return_value="# 简短报告\n没有完整章节"):
            out = handlers.write_report(
                {"id": "n16", "name": "report"}, self.st, {}
            )
        report = out["artifacts"]["report_md"]
        self.assertEqual(report["source"], "deterministic_guardrail")
        self.assertIn("缺少必需章节", report["guardrail_fallback_reason"])
        handlers._assert_report_structure(report["markdown"], ["campA"])
        handlers._assert_no_forbidden_terms(report["markdown"])

    def test_fix_report_rejects_missing_model_and_increments_attempt(self):
        self.st.write_artifact("report_md", {"markdown": "# 初稿", "chapters": "five",
                                             "version": "0.0"})
        self.st.write_artifact("qc_report", {"has_p0": True,
                                             "issues": [{"level": "P0", "msg": "数值与图表不一致"}]})
        with self.assertRaisesRegex(RuntimeError, "无法执行报告返修"):
            handlers.fix_report({"id": "n19", "name": "fix"}, self.st, {})
        self.assertEqual(self.st.counters["qc_rounds"], 1)
        self.assertEqual(self.st.counters["report_patch"], 1)

    def test_fix_report_rejects_missing_model_with_nonstring_issues(self):
        self.st.write_artifact("report_md", {"markdown": "# 初稿"})
        self.st.write_artifact("qc_report", {"has_p0": True, "issues": [1, 2]})
        with self.assertRaisesRegex(RuntimeError, "无法执行报告返修"):
            handlers.fix_report({"id": "n19", "name": "fix"}, self.st, {})

    def test_fix_report_sends_full_document_and_rejects_truncated_replacement(self):
        self.st.params["run_mode"] = "full"
        stats = {"campaigns": {"campA": {}}}
        self.st.write_artifact("stats_bundle", stats)
        complete = handlers._fallback_report_md(
            stats, {}, {}, "five", "0.0"
        )
        complete += "\n\n" + ("保留正文。" * 1200) + "\nTAIL_SENTINEL"
        self.st.write_artifact("report_md", {
            "markdown": complete, "chapters": "five", "version": "0.0",
        })
        self.st.write_artifact("qc_report", {
            "has_p0": True,
            "issues": [{"severity": "P0", "msg": "修复一处数字"}],
        })
        prompts = []

        def fake_chat(_system, user, **_kwargs):
            prompts.append(user)
            return "# 两行替换\n内容"

        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", side_effect=fake_chat):
            with self.assertRaises(ValueError):
                handlers.fix_report({"id": "n19", "name": "fix"}, self.st, {})
        self.assertIn("TAIL_SENTINEL", prompts[0])

    def test_fix_report_retries_preflight_inside_same_node(self):
        stats = {"campaigns": {"campA": {}}}
        self.st.write_artifact("stats_bundle", stats)
        self.st.write_artifact("chart_urls", {"charts": []})
        self.st.write_artifact("requirement_confirmed", {})
        complete = handlers._fallback_report_md(
            stats, {}, {}, "five", "0.0"
        )
        self.st.write_artifact("report_md", {
            "markdown": complete, "chapters": "five", "version": "0.0",
        })
        self.st.write_artifact("qc_report", {
            "has_p0": True,
            "issues": [{
                "id": "P0-small-sample-campA-口感",
                "severity": "P0",
                "title": "分维 NSR 小样本未披露",
                "evidence": "campA/口感: sample=19<30",
                "locus": "dimension_nsr",
            }],
        })
        failed = {
            "has_p0": True,
            "p0_count": 1,
            "issues": [{
                "id": "P0-small-sample-campA-口感",
                "severity": "P0",
                "title": "分维 NSR 小样本未披露",
            }],
            "scan_mode": "full_scan",
        }
        passed = {
            "has_p0": False,
            "p0_count": 0,
            "issues": [],
            "scan_mode": "full_scan",
        }
        prompts = []

        def fake_chat(_system, user, **_kwargs):
            prompts.append(user)
            return complete

        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", side_effect=fake_chat), \
                patch(
                    "handlers.validator_mod.validate_report",
                    side_effect=[failed, passed],
                ):
            out = handlers.fix_report(
                {"id": "n19", "name": "fix"}, self.st, {}
            )
        report = out["artifacts"]["report_md"]
        self.assertEqual(len(prompts), 2)
        self.assertIn("P0-small-sample-campA-口感", prompts[1])
        self.assertEqual(
            report["deterministic_preflight"],
            {"attempts": 2, "p0_count": 0, "scan_mode": "full_scan"},
        )

    def test_fix_report_fails_closed_after_three_preflight_attempts(self):
        stats = {"campaigns": {"campA": {}}}
        self.st.write_artifact("stats_bundle", stats)
        self.st.write_artifact("chart_urls", {"charts": []})
        complete = handlers._fallback_report_md(
            stats, {}, {}, "five", "0.0"
        )
        self.st.write_artifact("report_md", {
            "markdown": complete, "chapters": "five", "version": "0.0",
        })
        issue = {
            "id": "P0-still-open",
            "severity": "P0",
            "title": "仍未修复",
        }
        self.st.write_artifact(
            "qc_report", {"has_p0": True, "issues": [issue]}
        )
        failed = {
            "has_p0": True,
            "p0_count": 1,
            "issues": [issue],
            "scan_mode": "full_scan",
        }
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", return_value=complete), \
                patch(
                    "handlers.validator_mod.validate_report",
                    return_value=failed,
                ):
            with self.assertRaisesRegex(
                ValueError, "3 次节点内返修后仍有确定性 P0"
            ):
                handlers.fix_report(
                    {"id": "n19", "name": "fix"}, self.st, {}
                )


if __name__ == "__main__":
    unittest.main()
