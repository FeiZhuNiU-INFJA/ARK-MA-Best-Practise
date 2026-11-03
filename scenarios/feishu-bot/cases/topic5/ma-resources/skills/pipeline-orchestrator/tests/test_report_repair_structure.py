"""返修必须保留真实表格，并容忍等价标题及有限重试。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _util import make_state
import handlers
import report_contract as contract


class TestReportRepairStructure(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.state = make_state(self.work.name)
        row = {"title": "实测", "content": "原始正文|保留",
               "site_name": "小红书", "author": "公开机构",
               "url": "https://example.com/post", "interaction_cnt": 123}
        self.stats = {"brand": {"top_content": {"pgc": [row], "ugc": [row]}},
                      "campaigns": {"campA": {}}}
        self.base = handlers._fallback_report_md(self.stats, {}, {}, "five", "0.0")
        self.state.write_artifact("stats_bundle", self.stats)
        self.state.write_artifact("chart_urls", {"charts": []})
        self.state.write_artifact("qc_report", {
            "issues": [{"severity": "P0", "id": "P0-test", "title": "修订正文"}]})
        self.state.write_artifact("report_md", {"markdown": self.base})
        self.passed = {"p0_count": 0, "issues": [], "scan_mode": "full_scan"}

    def test_assemble_replaces_nested_hot_sections_once(self):
        old = self.base
        bounds = contract.section(old, "1.4")
        self.assertIsNotNone(bounds)
        at = bounds[1]
        old = old[:at] + "\n#### 品牌 PGC 热门内容 Top10\n旧表内容\n#### 品牌 UGC 热门内容 Top10\n旧表内容\n" + old[at:]
        assembled = contract.assemble(old, plan={}, stats=self.stats, charts={}, insight={})
        again = contract.assemble(assembled, plan={}, stats=self.stats, charts={}, insight={})
        self.assertNotIn("旧表内容", assembled)
        self.assertEqual(assembled, again)
        self.assertEqual(assembled.count("品牌 PGC 热门内容 Top10"), 1)
        self.assertIn(r"原始正文\|保留", assembled)

    def test_assemble_preserves_overall_insight_heading_variants(self):
        brand = self.base.index("## 01")
        rest = self.base[brand:]
        for heading in ("**整体洞察**", "### 整体洞察", "##### 整体洞察",
                        "## 数据说明与整体洞察"):
            with self.subTest(heading=heading):
                intro = "# 报告\n\n"
                if "数据说明" not in heading:
                    intro += "## 00 篇头 · 数据说明\n\n旧数据说明。\n\n"
                insight = "活动样本123条，品牌提及45条，发现应保留。"
                original = intro + heading + "\n\n" + insight + "\n\n" + rest
                md = contract.assemble(original, plan={}, stats=self.stats, charts={}, insight={})
                self.assertIn(insight, md)
                self.assertIn("整体洞察", md)
                self.assertEqual(md, contract.assemble(md, plan={}, stats=self.stats, charts={}, insight={}))

    def test_write_report_rejects_missing_insight_after_assembly(self):
        assembled_without_insight = self.base.replace("整体洞察", "观察")
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", return_value=self.base), \
                patch("report_contract.assemble", return_value=assembled_without_insight):
            with self.assertRaisesRegex(ValueError, "整体洞察"):
                handlers.write_report({"id": "n17"}, self.state, {})

    def test_wordcloud_retries_forbidden_action_using_same_evidence(self):
        import wordcloud_adapter as adapter
        bad = {"topics": [{"title": "渠道参与", "terms": ["山姆"], "post_ids": ["post-1"],
                            "rationale": "源词及原帖依据", "interpretation": "提升用户参与和转化。"}]}
        good = {"topics": [{**bad["topics"][0], "interpretation": "样本中出现试饮参与。"}]}
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", side_effect=[bad, good]) as model:
            out = adapter.ask(adapter.SYSTEM, "已读原帖post-1：山姆渠道试饮",
                              lambda data: adapter.validate_topics(data, {"山姆"}, {"post-1"},
                                                                   handlers.FORBIDDEN_METRIC_TERMS))
        self.assertEqual(model.call_count, 2)
        self.assertIn("post-1", model.call_args_list[1].args[1])
        self.assertIn("禁用", model.call_args_list[1].args[1])
        self.assertEqual(out["topics"][0]["post_ids"], ["post-1"])

    def test_wordcloud_stops_after_three_forbidden_actions(self):
        import wordcloud_adapter as adapter
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat_json", return_value={"topics": [{
                    "title": "渠道参与", "terms": ["山姆"], "rationale": "依据",
                    "post_ids": ["post-1"], "interpretation": "提升转化。"}]}) as model:
            with self.assertRaisesRegex(RuntimeError, "禁用措辞"):
                adapter.ask(adapter.SYSTEM, "原帖post-1",
                            lambda data: adapter.validate_topics(data, {"山姆"}, {"post-1"},
                                                                 handlers.FORBIDDEN_METRIC_TERMS))
        self.assertEqual(model.call_count, 3)

    def test_fix_report_accepts_equivalent_generated_top_headings(self):
        bounds = contract.section(self.base, "1.4")
        at = bounds[1]
        previous = self.base[:at] + "\n#### 品牌 PGC 热门内容 Top10\n旧版表\n#### 品牌 UGC 热门内容 Top10\n旧版表\n" + self.base[at:]
        self.state.write_artifact("report_md", {"markdown": previous})
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", return_value=self.base) as model, \
                patch("handlers.validator_mod.validate_report", return_value=self.passed):
            out = handlers.fix_report({"id": "n19"}, self.state, {})
        self.assertEqual(model.call_count, 1)
        self.assertIn("https://example.com/post", out["artifacts"]["report_md"]["markdown"])

    def test_fix_report_retries_structure_error_with_original_document(self):
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", side_effect=["# 截断报告", self.base]) as model, \
                patch("handlers.validator_mod.validate_report", return_value=self.passed):
            out = handlers.fix_report({"id": "n19"}, self.state, {})
        self.assertEqual(model.call_count, 2)
        self.assertIn("1.4", model.call_args_list[1].args[1])
        self.assertIn("P0-repair-structure", model.call_args_list[1].args[1])
        self.assertEqual(out["artifacts"]["report_md"]["deterministic_preflight"]["attempts"], 2)

    def test_fix_report_stops_after_three_structure_errors(self):
        with patch("semantic.llm_available", return_value=True), \
                patch("semantic.chat", return_value="# 截断报告") as model:
            with self.assertRaisesRegex(ValueError, "3 次节点内返修"):
                handlers.fix_report({"id": "n19"}, self.state, {})
        self.assertEqual(model.call_count, 3)

    def test_actual_hot_tables_reject_missing_rows_columns_and_changed_values(self):
        assembled = contract.assemble(self.base, plan={}, stats=self.stats, charts={}, insight={})
        row = next(line for line in assembled.splitlines() if line.startswith("| 1 |"))
        mutations = {
            "缺行": assembled.replace(row + "\n", "", 1),
            "缺列": assembled.replace("| 作者 |", "|", 1),
            "改数": assembled.replace("| 123 |", "| 999 |", 1),
        }
        for name, revised in mutations.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "字段、行数或源数据不一致"):
                contract.assert_hot_tables(revised, self.stats)

    def test_actual_hot_tables_accept_heading_format_and_require_empty_disclosure(self):
        assembled = contract.assemble(self.base, plan={}, stats=self.stats, charts={}, insight={})
        changed = assembled.replace("**品牌 PGC 热门内容 Top10**", "#### PGC榜单")
        contract.assert_hot_tables(changed, self.stats)
        changed = assembled.replace("本次可用数据中无 UGC 内容。", "", 1)
        with self.assertRaisesRegex(ValueError, "无数据披露"):
            contract.assert_hot_tables(changed, self.stats)

    def test_preserved_structure_accepts_numbered_bold_and_rejects_missing_section(self):
        assembled = contract.assemble(self.base, plan={}, stats=self.stats, charts={}, insight={})
        changed = assembled.replace("### 1.4 热门内容", "**1.4 热门榜单**")
        contract.assert_preserved_structure(assembled, changed)
        self.assertIsNotNone(contract.section(changed, "1.4"))
        canonical = contract.assemble(changed, plan={}, stats=self.stats, charts={}, insight={})
        self.assertIn("### 1.4 热门榜单", canonical)
        self.assertNotIn("**1.4", canonical)
        changed = assembled.replace("### 4.2 待改进问题", "待改进问题")
        with self.assertRaisesRegex(ValueError, "section:4.2"):
            contract.assert_preserved_structure(assembled, changed)


if __name__ == "__main__":
    unittest.main()
